"""Full-duplex (wavelength-division) FSO link between Terminal A and Terminal B.

Each terminal transmits on its own wavelength (default A->B 1550 nm, B->A 1310 nm)
and filters out the other one.  Both directions run simultaneously in discrete
time slots; in every slot each terminal sends exactly one frame
    type(8) | seq(16) | fb(8) | len(8) | data | CRC-32 (IEEE 802.3)        (K + 9 bytes)
where `fb` PIGGYBACKS the ACK/NACK for the frame the terminal received from its
peer in the previous slot (true duplex: no separate ACK channel).  A terminal
with nothing left to send emits feedback-only frames ("ACK" type in the timeline).

Selective repeat with per-segment CRC: a NACK, or a reverse frame that fails its
CRC (feedback unknown -> time-out), re-queues the segment (retransmissions take
priority over new data) until 1 + max_retries attempts are used.  Because the
feedback rides on the reverse frame, a bad reverse frame loses the data AND the
ACK of that slot; a retransmission of data the peer already holds is counted as
"spurious".

Channel physics per direction (src -> dst), same atmosphere both ways:
  P_rx = P_tx - lens/aperture loss(optics.lens_link_budget at the src wavelength)
         [- receive-lens transmittance] - alpha(weather, lambda) d - fixed - extra
  scintillation index from Rytov(Cn2, d, lambda) -> log-normal fading
  crosstalk (near-end) into dst's receiver, from dst's OWN transmitter:
         P_xt = P_tx(dst) - near_end_coupling_dB - filter_isolation_dB(dst)
         SNR = P_rx^2 / (sigma_noise^2 + P_xt^2)
Fading is independent per slot and per direction with cross-direction correlation
`reciprocity_rho` (the same turbulent medium is traversed both ways).
Frame synchronisation is assumed; only bit errors are modelled.
"""
from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend import bitcheck, optics
from backend.arq import (MAX_RETRIES, ArqChannel, FadingProcess, SegRecord, assemble_result, flip_bits,
                         hamming, split_payload)
from backend.engine import crc32
from backend.propagation3d import scintillation_index_from_rytov, weather_alpha_dB_km

MAX_SLOTS = 10000
TIMELINE_KEEP = 1000      # returned timeline: first N + last N slots (+ timeline_truncated)
HEADER_BYTES = 5            # type + seq(2) + fb + len
FRAME_OVERHEAD_BYTES = 9    # header + crc(4)
T_DATA, T_FB = 0, 1
FB_NONE, FB_ACK, FB_NACK = 0, 1, 2
FB_NAMES = {FB_NONE: "NONE", FB_ACK: "ACK", FB_NACK: "NACK"}
AB, BA = "A→B", "B→A"


@dataclass
class Terminal:
    name: str = "A"
    tx_power_dBm: float = 10.0
    wavelength_nm: float = 1550.0
    tx_lens: str = "none"
    rx_lens: str = "none"
    rx_radius_m: float = 0.025
    filter_isolation_dB: float = 40.0    # rejection of the other terminal's wavelength
    sensitivity_dBm: float = -40.0
    noise_rms_dBm: float = -30.0
    near_end_coupling_dB: float = 30.0   # back-reflection / leakage of own TX into own RX path


def _frame(ftype: int, seq: int, fb: int, data: bytes) -> List[int]:
    body = bytes([ftype]) + seq.to_bytes(2, "big") + bytes([fb, len(data)]) + data
    return bitcheck.bytes_to_bits(body + crc32(body).to_bytes(4, "big"))


def _parse(bits) -> Dict:
    raw = bitcheck.bits_to_bytes(bits)
    if len(raw) < FRAME_OVERHEAD_BYTES:
        return {"ok": False, "type": None, "seq": None, "fb": FB_NONE, "data": b""}
    body, crc = raw[:-4], int.from_bytes(raw[-4:], "big")
    ok = crc32(body) == crc and body[4] == len(body) - HEADER_BYTES
    return {"ok": ok, "type": body[0], "seq": int.from_bytes(body[1:3], "big"),
            "fb": body[3], "data": bytes(body[HEADER_BYTES:])}


def link_budget(src: Terminal, dst: Terminal, distance_m: float, weather: str = "clear",
                cn2: float = 1e-15, fixed_loss_dB: float = 3.0, extra_loss_dB: float = 0.0,
                atmos_loss_dB: Optional[float] = None, sigma_I2: Optional[float] = None) -> Dict:
    """Budget + ArqChannel for src -> dst.  atmos_loss_dB / sigma_I2 override the
    weather/Cn2 models (e.g. with a 3D propagation result)."""
    lam = src.wavelength_nm * 1e-9
    tx = optics.build_preset(src.tx_lens)
    rx = optics.build_preset(dst.rx_lens)
    if tx.role == "receive":
        raise ValueError(f"tx_lens '{src.tx_lens}' is a receive lens")
    if rx.role == "transmit":
        raise ValueError(f"rx_lens '{dst.rx_lens}' is a transmit lens")
    rx_radius = rx.rx_aperture_radius if rx.role == "receive" else dst.rx_radius_m
    lb = optics.lens_link_budget(tx, distance_m, wavelength=lam, rx_radius=rx_radius)
    optics_loss = lb["loss_with_dB"]
    if rx.role == "receive":
        optics_loss += -10.0 * math.log10(max(rx.system.transmittance, 1e-300))
    alpha = weather_alpha_dB_km(weather, lam)
    atmos = alpha * distance_m / 1000.0 if atmos_loss_dB is None else atmos_loss_dB
    p_rx = src.tx_power_dBm - optics_loss - atmos - fixed_loss_dB - extra_loss_dB
    if sigma_I2 is None:
        k = 2.0 * math.pi / lam
        sigma_I2 = scintillation_index_from_rytov(1.23 * cn2 * k ** (7.0 / 6.0) * distance_m ** (11.0 / 6.0))
    xt = dst.tx_power_dBm - dst.near_end_coupling_dB - dst.filter_isolation_dB
    ch = ArqChannel(p_rx_dBm=p_rx, noise_rms_dBm=dst.noise_rms_dBm, sigma_I2=sigma_I2,
                    interference_dBm=xt)
    clean = ArqChannel(p_rx_dBm=p_rx, noise_rms_dBm=dst.noise_rms_dBm)
    budget = {
        "wavelength_nm": src.wavelength_nm, "p_tx_dBm": src.tx_power_dBm, "p_rx_dBm": p_rx,
        "optics_loss_dB": optics_loss, "atmos_loss_dB": atmos, "atmos_alpha_dB_km": alpha,
        "fixed_loss_dB": fixed_loss_dB, "extra_loss_dB": extra_loss_dB,
        "crosstalk_dBm": xt, "sir_dB": p_rx - xt, "sigma_I2": sigma_I2,
        "margin_dB": p_rx - dst.sensitivity_dBm, "viable": p_rx > dst.sensitivity_dBm,
        "ber_no_crosstalk": clean.mean_ber(), "ber_mean_power": ch.mean_ber(),
        "ber_faded_mean": ch.faded_mean_ber(),
        "distance_m": distance_m,
    }
    return {"budget": budget, "channel": ch}


def rescale_propagation(summary: Dict, distance_m: float) -> Dict:
    """Map a 3D-propagation summary (computed for its own link length L) onto the duplex
    distance d.  The duplex distance is authoritative; the propagation contributes the
    *atmospheric state* (weather, Cn2 profile, anomalies) as per-length quantities:
      extinction  atmos_dB(d) = total_atten_dB * d / L            (Beer-Lambert, linear in d)
      turbulence  sigma_R^2(d) = sigma2_R * (d / L)^(11/6)        (Rytov scaling), then
                  sigma_I^2 = scintillation_index_from_rytov(.)
    Lens/aperture loss is always evaluated at d by the optics model, and beam-spread/pointing
    terms of the propagation are not reused (the optics link budget covers them)."""
    L = float(summary["length_m"])
    if L <= 0:
        raise ValueError("propagation length must be > 0")
    k = distance_m / L
    s2R = float(summary.get("sigma2_R", 0.0)) * k ** (11.0 / 6.0)
    return {"atmos_loss_dB": float(summary["total_atten_dB"]) * k,
            "sigma_I2": scintillation_index_from_rytov(s2R), "scale": k, "source_length_m": L}


class _Side:
    """Sender state of one terminal + receiver bookkeeping of what the peer sent."""

    def __init__(self, payload, segment_bytes: int, max_retries: int):
        self.payload, self.recs = split_payload(payload, segment_bytes)
        self.max_retries = max_retries
        self.queue = deque(range(len(self.recs)))
        self.sent_prev: Optional[int] = None     # seq of the DATA frame sent last slot
        self.fb_to_send = FB_NONE                # status for the frame received last slot
        self.data_bits = 0
        self.fb_bits = 0
        self.frames = 0

    @property
    def done(self) -> bool:
        return not self.queue and self.sent_prev is None

    def requeue(self, seq: int) -> None:
        r = self.recs[seq]
        if r.attempts <= self.max_retries:
            self.queue.appendleft(seq)           # retransmissions take priority
        else:
            r.gave_up = True


def run_duplex(term_a: Optional[Terminal] = None, term_b: Optional[Terminal] = None,
               payload_a="Terminal A -> B: merhaba FSO!", payload_b="Terminal B -> A: selam!",
               distance_m: float = 2000.0, weather: str = "clear", cn2: float = 1e-15,
               segment_bytes: int = 8, max_retries: int = 5, reciprocity_rho: float = 0.5,
               fading_rho: float = 0.0, bitrate_bps: float = 1e9, seed: int = 0,
               fixed_loss_dB: float = 3.0, extra_loss_dB: Optional[Dict[str, float]] = None,
               overrides: Optional[Dict[str, Dict]] = None, ber_override: Optional[float] = None) -> Dict:
    """Simulate both directions.  overrides: {"A→B": {"atmos_loss_dB", "sigma_I2"}, ...}.
    ber_override forces a fixed BER on both directions (testing)."""
    a = term_a or Terminal("A", wavelength_nm=1550.0)
    b = term_b or Terminal("B", wavelength_nm=1310.0)
    if not 0.0 <= reciprocity_rho <= 1.0 or not 0.0 <= fading_rho < 1.0:
        raise ValueError("reciprocity_rho in [0,1], fading_rho in [0,1)")
    if bitrate_bps <= 0 or distance_m <= 0 or not 0 <= max_retries <= MAX_RETRIES:
        raise ValueError(f"bitrate_bps and distance_m must be > 0; max_retries within [0, {MAX_RETRIES}]")
    extra_loss_dB = extra_loss_dB or {}
    overrides = overrides or {}
    lk = {AB: link_budget(a, b, distance_m, weather, cn2, fixed_loss_dB, extra_loss_dB.get(AB, 0.0),
                          **overrides.get(AB, {})),
          BA: link_budget(b, a, distance_m, weather, cn2, fixed_loss_dB, extra_loss_dB.get(BA, 0.0),
                          **overrides.get(BA, {}))}
    if ber_override is not None:
        for v in lk.values():
            v["channel"].ber_override = ber_override

    rng = random.Random(seed)
    fade = FadingProcess(rng, fading_rho)
    sides = {"A": _Side(payload_a, segment_bytes, max_retries),
             "B": _Side(payload_b, segment_bytes, max_retries)}
    chan = {"A": lk[AB]["channel"], "B": lk[BA]["channel"]}     # channel used by a sender
    peer = {"A": "B", "B": "A"}
    max_slots = min(MAX_SLOTS, 4 * (len(sides["A"].recs) + len(sides["B"].recs)) * (max_retries + 1) + 20)

    timeline: List[Dict] = []
    slot = 0
    while slot < max_slots and not (sides["A"].done and sides["B"].done):
        z_a = fade.next()
        z_b = reciprocity_rho * z_a + math.sqrt(1.0 - reciprocity_rho ** 2) * rng.gauss(0.0, 1.0)
        zs = {"A": z_a, "B": z_b}
        tx_info, rx_parsed, sent_now = {}, {}, {}
        for who in ("A", "B"):
            sd = sides[who]
            if sd.queue:
                seq = sd.queue.popleft()
                r = sd.recs[seq]
                retx = r.attempts > 0
                if retx and r.rx_ok:
                    r.spurious += 1
                bits = _frame(T_DATA, seq, sd.fb_to_send, r.data)
                ftype, sent_now[who] = ("RETX" if retx else "DATA"), seq
            else:
                bits = _frame(T_FB, 0, sd.fb_to_send, b"")
                ftype, seq, sent_now[who] = "ACK", None, None
            ber_t = chan[who].ber_from_z(zs[who])
            rx_bits = flip_bits(bits, ber_t, rng)
            sd.frames += 1
            sd.data_bits += len(bits)
            sd.fb_bits += 8
            tx_info[who] = {"seq": seq, "type": ftype, "fb": FB_NAMES[sd.fb_to_send]}
            rx_parsed[who] = (_parse(rx_bits), ber_t, hamming(bits, rx_bits))
            if sent_now[who] is not None:
                sd.recs[sent_now[who]].record_attempt(rx_parsed[who][0]["ok"], ber_t,
                                                      rx_parsed[who][2])

        # receivers: store data, decide feedback for the next slot, read piggybacked feedback
        new_fb = {}
        rx_ok = {}
        for who in ("A", "B"):             # `who` = sender, receiver is peer[who]
            p = rx_parsed[who][0]
            rcv = peer[who]
            rx_ok[rcv] = bool(p["ok"])
            if p["ok"]:
                if p["type"] == T_DATA:
                    r = sides[who].recs[p["seq"]] if p["seq"] < len(sides[who].recs) else None
                    if r is not None:
                        if not r.rx_ok:
                            r.rx_ok, r.rx_data = True, p["data"]
                    new_fb[rcv] = FB_ACK
                else:
                    new_fb[rcv] = FB_NONE
            else:
                new_fb[rcv] = FB_NACK
        for who in ("A", "B"):
            sd = sides[who]
            rcvd = rx_parsed[peer[who]][0]      # frame `who` received this slot (carries fb for last slot)
            if sd.sent_prev is not None:
                seq = sd.sent_prev
                r = sd.recs[seq]
                if rcvd["ok"] and rcvd["fb"] == FB_ACK:
                    r.acked = True
                else:                           # NACK, or reverse frame lost -> time-out
                    sd.requeue(seq)
            sd.sent_prev = sent_now[who]
            sd.fb_to_send = new_fb[who]
        timeline.append({"slot": slot, "a_tx": tx_info["A"], "b_tx": tx_info["B"],
                         "a_rx_ok": rx_ok["A"], "b_rx_ok": rx_ok["B"]})
        slot += 1

    total_slots = slot
    k_max = max(segment_bytes, 1)
    slot_s = (k_max + FRAME_OVERHEAD_BYTES) * 8 / bitrate_bps
    total_s = max(total_slots, 1) * slot_s
    directions = {}
    for name, who in ((AB, "A"), (BA, "B")):
        sd = sides[who]
        arq = assemble_result(sd.payload, sd.recs, sd.data_bits, sd.fb_bits,
                              {"piggybacked_feedback": True, "frames_sent": sd.frames,
                               "max_retries": max_retries, "segment_bytes": segment_bytes,
                               "forward_mean_ber": lk[name]["channel"].mean_ber(),
                               "forward_effective_ber": lk[name]["budget"]["ber_faded_mean"]})
        ok_bytes = sum(len(r.data) for r in sd.recs if r.rx_ok)
        directions[name] = {
            "link_budget": lk[name]["budget"],
            # `ber` is the fading-averaged BER the ARQ channel actually applies (per attempt
            # mean of `ber_history` converges to it); ber_mean_power is the BER at mean power.
            "ber": lk[name]["budget"]["ber_faded_mean"],
            "ber_mean_power": lk[name]["budget"]["ber_mean_power"],
            "ber_observed_mean": (sum(b for r in sd.recs for b in r.ber_history)
                                  / max(1, sum(len(r.ber_history) for r in sd.recs))),
            "arq_result": arq,
            "throughput_bps": sd.data_bits / total_s,
            "goodput_bps": ok_bytes * 8 / total_s,
        }
    tl_total = len(timeline)
    tl_trunc = tl_total > 2 * TIMELINE_KEEP
    if tl_trunc:
        timeline = timeline[:TIMELINE_KEEP] + timeline[-TIMELINE_KEEP:]
    both = directions[AB]["arq_result"]["delivered_ok"] and directions[BA]["arq_result"]["delivered_ok"]
    return {
        "directions": directions, "timeline": timeline,
        "timeline_truncated": tl_trunc, "timeline_total_slots": tl_total,
        "timeline_omitted": tl_total - len(timeline),
        "summary": {
            "both_delivered": both, "total_slots": total_slots,
            "slot_duration_s": slot_s, "total_time_s": total_s, "bitrate_bps": bitrate_bps,
            "timed_out": total_slots >= max_slots and not (sides["A"].done and sides["B"].done),
            "asymmetry_dB": lk[AB]["budget"]["p_rx_dBm"] - lk[BA]["budget"]["p_rx_dBm"],
            "asymmetry_note": "P_rx(A→B) - P_rx(B→A) in dB; positive = A→B stronger",
        },
    }
