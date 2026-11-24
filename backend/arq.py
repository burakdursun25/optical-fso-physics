"""CRC-based selective-repeat ARQ over a fading FSO link.

Protocol (round based, "stop-and-collect" selective repeat):

* The payload is split into K-byte segments.  Each segment is framed as
      seq(16 b) | len(8 b) | data | CRC-32 (IEEE 802.3) over seq+len+data
  (same CRC as bitcheck/engine.crc32; preamble/sync handling is bitcheck's
  job - here frame synchronisation is assumed and only bit flips are modelled).
* Every round the sender transmits all still-pending segments; the receiver
  checks each CRC.  Good frame -> ACK, bad frame -> NACK, both sent back as a
  small CRC-protected feedback frame  seq(16) | status(8) | CRC-32  over the
  reverse channel with its own BER `ack_ber`.
* A segment is retransmitted when it was NACKed OR when its feedback frame was
  corrupted/lost (sender time-out).  A lost ACK for a segment the receiver
  already holds therefore causes a *spurious retransmission*, which is counted.
* A segment is attempted at most 1 + max_retries times (max_retries <= 200).
* Runtime guard: the total forward wire bits are capped (MAX_WIRE_BITS); on exhaustion the
  remaining segments give up and `budget_exhausted` is reported.

Forward channel: per transmission the received power is
    P_rx = P_mean + 10 log10(I),  I ~ log-normal with E[I]=1,
    sigma_lnI^2 = ln(1 + sigma_I^2)   (scintillation index sigma_I^2),
drawn from a seeded AR(1) Gaussian sequence with correlation `fading_rho` between
consecutive transmissions, then BER = bitcheck.ook_ber(SNR) (optionally with an
interference term for the duplex crosstalk).  Retries therefore see a fresh,
possibly better, channel state.

Spatial view (optional `propagation` result from propagate_beam_3d): the link is
cut into `n_spatial_bins` equal distance bins; every failed attempt (= a retry
that had to be made) is apportioned to the bins in proportion to the mean local
degradation score of each bin (floor 1e-3), both as an expected fractional count
and as a seeded sampled count.  This is a heuristic attribution (a CRC failure
cannot tell where the bits were damaged), documented as such.
"""
from __future__ import annotations

from functools import lru_cache
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Union

import numpy as np

from backend import bitcheck
from backend.engine import crc32
from backend.propagation3d import scintillation_index_from_rytov

MAX_PAYLOAD_BYTES = 4096
MAX_SEGMENTS = 256
MAX_RETRIES = 200
HISTORY_KEEP = 8             # per-segment history: first N + last N attempts (+ counts)
ROUND_DETAIL_KEEP = 20       # rounds keeping per-segment lists: first N + last N
MAX_WIRE_BITS = 6_000_000    # total forward-bit budget of one run (runtime / memory guard)
SEG_OVERHEAD_BYTES = 7       # seq(2) + len(1) + crc(4)
ACK_FRAME_BITS = 56          # seq(2) + status(1) + crc(4)
ACK_FRAME_BYTES = 7
ACK, NACK = 1, 2


@lru_cache(maxsize=16)
def _hermegauss_weights(n: int):
    x, w = np.polynomial.hermite_e.hermegauss(n)
    w = w / w.sum()
    return tuple(float(xi) for xi in x), tuple(float(wi) for wi in w)


# --------------------------------------------------------------------------
# framing
# --------------------------------------------------------------------------
def frame_segment(seq: int, data: bytes) -> List[int]:
    if not 0 <= seq <= 0xFFFF:
        raise ValueError("seq out of range")
    if len(data) > 255:
        raise ValueError("segment too long (max 255 bytes)")
    body = seq.to_bytes(2, "big") + bytes([len(data)]) + data
    return bitcheck.bytes_to_bits(body + crc32(body).to_bytes(4, "big"))


def parse_segment(bits: Sequence[int]) -> Dict[str, object]:
    """-> {ok, seq, data}.  ok = CRC valid and length field consistent."""
    raw = bitcheck.bits_to_bytes(bits)
    if len(raw) < SEG_OVERHEAD_BYTES:
        return {"ok": False, "seq": None, "data": b""}
    body, crc = raw[:-4], int.from_bytes(raw[-4:], "big")
    ok = crc32(body) == crc and body[2] == len(body) - 3
    return {"ok": ok, "seq": int.from_bytes(body[:2], "big"), "data": bytes(body[3:])}


def frame_ack(seq: int, status: int) -> List[int]:
    body = seq.to_bytes(2, "big") + bytes([status])
    return bitcheck.bytes_to_bits(body + crc32(body).to_bytes(4, "big"))


def parse_ack(bits: Sequence[int]) -> Dict[str, object]:
    raw = bitcheck.bits_to_bytes(bits)
    if len(raw) < ACK_FRAME_BYTES:
        return {"ok": False, "seq": None, "status": None}
    body, crc = raw[:-4], int.from_bytes(raw[-4:], "big")
    return {"ok": crc32(body) == crc, "seq": int.from_bytes(body[:2], "big"), "status": body[2]}


def flip_bits(bits: Sequence[int], ber: float, rng: random.Random) -> List[int]:
    """Binary symmetric channel using the caller's rng (stateful, deterministic)."""
    if ber <= 0.0:
        return list(bits)
    rnd = rng.random
    return [b ^ 1 if rnd() < ber else b for b in bits]


def hamming(a: Sequence[int], b: Sequence[int]) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


# --------------------------------------------------------------------------
# channel
# --------------------------------------------------------------------------
@dataclass
class ArqChannel:
    """Forward-channel description.  Set ber_override for a fixed-BER channel
    (no fading); otherwise BER follows the received power with log-normal fading."""
    p_rx_dBm: float = -20.0
    noise_rms_dBm: float = -30.0
    sigma_I2: float = 0.0               # scintillation index of the intensity
    fading_rho: float = 0.0             # AR(1) correlation between consecutive transmissions
    interference_dBm: Optional[float] = None   # e.g. crosstalk (duplex)
    ber_override: Optional[float] = None

    def faded_power_dBm(self, z: float) -> float:
        if self.sigma_I2 <= 0.0:
            return self.p_rx_dBm
        var = math.log1p(self.sigma_I2)
        chi = -0.5 * var + math.sqrt(var) * z
        return self.p_rx_dBm + 10.0 * chi / math.log(10.0)

    def mean_ber(self) -> float:
        if self.ber_override is not None:
            return self.ber_override
        return self.ber_at_power(self.p_rx_dBm)

    def faded_mean_ber(self, n: int = 40) -> float:
        """Fading-averaged BER E_z[BER(P_rx e^chi)] by Gauss-Hermite quadrature: the BER the
        ARQ channel really applies on average (mean_ber() is the BER at the *mean* power)."""
        if self.ber_override is not None:
            return self.ber_override
        if self.sigma_I2 <= 0.0:
            return self.mean_ber()
        xs, ws = _hermegauss_weights(n)
        return float(sum(wi * self.ber_from_z(xi) for xi, wi in zip(xs, ws)))

    def ber_at_power(self, p_dBm: float) -> float:
        p = 10.0 ** (p_dBm / 10.0)
        noise2 = (10.0 ** (self.noise_rms_dBm / 10.0)) ** 2
        if self.interference_dBm is not None:
            noise2 += (10.0 ** (self.interference_dBm / 10.0)) ** 2
        return bitcheck.ook_ber(p * p / noise2)

    def ber_from_z(self, z: float) -> float:
        if self.ber_override is not None:
            return self.ber_override
        return self.ber_at_power(self.faded_power_dBm(z))


class FadingProcess:
    """Seeded standard-normal AR(1) sequence z_t = rho z_{t-1} + sqrt(1-rho^2) xi."""

    def __init__(self, rng: random.Random, rho: float = 0.0):
        if not 0.0 <= rho < 1.0:
            raise ValueError("fading_rho must be in [0, 1)")
        self.rng, self.rho = rng, rho
        self.z = rng.gauss(0.0, 1.0)

    def next(self) -> float:
        self.z = self.rho * self.z + math.sqrt(1.0 - self.rho ** 2) * self.rng.gauss(0.0, 1.0)
        return self.z


def channel_from_link(preset: str = "none", distance_m: float = 1000.0, tx_power_dBm: float = 10.0,
                      wavelength: float = 1550e-9, rx_radius: float = 0.025,
                      atmos_dB_per_km: float = 0.2, fixed_loss_dB: float = 3.0,
                      noise_rms_dBm: float = -30.0, cn2: float = 1e-15,
                      fading_rho: float = 0.0, extra_loss_dB: float = 0.0) -> ArqChannel:
    """Channel from the bitcheck link model + Rytov scintillation for `cn2`."""
    lp = bitcheck.link_power_dBm(preset, distance_m, tx_power_dBm, wavelength, rx_radius,
                                 atmos_dB_per_km, fixed_loss_dB)
    k = 2.0 * math.pi / wavelength
    s2 = 1.23 * cn2 * k ** (7.0 / 6.0) * distance_m ** (11.0 / 6.0)
    return ArqChannel(p_rx_dBm=lp["p_rx_dBm"] - extra_loss_dB, noise_rms_dBm=noise_rms_dBm,
                      sigma_I2=scintillation_index_from_rytov(s2), fading_rho=fading_rho)


def channel_from_propagation(prop: Dict, tx_power_dBm: float = 10.0, fixed_loss_dB: float = 3.0,
                             noise_rms_dBm: float = -30.0, fading_rho: float = 0.0) -> ArqChannel:
    """Channel from a propagate_beam_3d result: power_at_rx_frac already contains
    extinction, lens clipping, long-term beam spread and pointing (deflection+wander)."""
    s = prop["summary"]
    return ArqChannel(p_rx_dBm=tx_power_dBm - s["power_at_rx_dB"] - fixed_loss_dB,
                      noise_rms_dBm=noise_rms_dBm, sigma_I2=s["scint_index"], fading_rho=fading_rho)


# --------------------------------------------------------------------------
# bookkeeping shared with duplex.py
# --------------------------------------------------------------------------
@dataclass
class SegRecord:
    index: int
    start: int
    end: int                         # inclusive last byte
    data: bytes
    attempts: int = 0
    crc_history: List[bool] = field(default_factory=list)
    ber_history: List[float] = field(default_factory=list)
    error_bits_history: List[int] = field(default_factory=list)
    rx_ok: bool = False
    rx_data: Optional[bytes] = None
    acked: bool = False              # sender saw an ACK
    spurious: int = 0                # retransmissions of data the receiver already held
    gave_up: bool = False

    def record_attempt(self, crc_ok: bool, ber: float, err_bits: int) -> None:
        self.attempts += 1
        self.crc_history.append(bool(crc_ok))
        self.ber_history.append(float(ber))
        self.error_bits_history.append(int(err_bits))


def split_payload(payload: Union[bytes, bytearray, str], segment_bytes: int) -> (bytes, List[SegRecord]):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    payload = bytes(payload)
    if not 1 <= segment_bytes <= 255:
        raise ValueError("segment_bytes must be within [1, 255]")
    if not 1 <= len(payload) <= MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload must be 1..{MAX_PAYLOAD_BYTES} bytes")
    n = math.ceil(len(payload) / segment_bytes)
    if n > MAX_SEGMENTS:
        raise ValueError(f"too many segments ({n} > {MAX_SEGMENTS}); increase segment_bytes")
    recs = []
    for i in range(n):
        a, b = i * segment_bytes, min(len(payload), (i + 1) * segment_bytes)
        recs.append(SegRecord(i, a, b - 1, payload[a:b]))
    return payload, recs


def region_label(rec: SegRecord) -> str:
    return f"Segment {rec.index} (bayt {rec.start}–{rec.end})"


def spatial_weights(propagation: Dict, n_bins: int = 10) -> List[Dict]:
    """Bin the centerline degradation score into equal-distance bins; returns
    [{bin, s_start, s_end, score_mean, share}] with shares summing to 1."""
    cl = propagation["centerline"]
    L = cl[-1]["s"]
    bins = []
    for b in range(n_bins):
        lo, hi = L * b / n_bins, L * (b + 1) / n_bins
        vals = [c["score"] for c in cl if lo <= c["s"] <= hi or (b == n_bins - 1 and c["s"] >= lo)]
        m = sum(vals) / len(vals) if vals else 0.0
        bins.append({"bin": b, "s_start": lo, "s_end": hi, "score_mean": m})
    tot = sum(max(x["score_mean"], 1e-3) for x in bins)
    for x in bins:
        x["share"] = max(x["score_mean"], 1e-3) / tot
    return bins


def spatial_retries(propagation: Dict, failed_attempts: int, rng: random.Random,
                    n_bins: int = 10, length_m: Optional[float] = None) -> List[Dict]:
    """Distribute failed attempts over the link.  If `length_m` is given the propagation's
    relative (normalised) distance profile is mapped onto that link length."""
    bins = spatial_weights(propagation, n_bins)
    if length_m is not None and bins and bins[-1]["s_end"] > 0:
        k = length_m / bins[-1]["s_end"]
        for x in bins:
            x["s_start"] *= k
            x["s_end"] *= k
    sampled = [0] * n_bins
    for _ in range(failed_attempts):
        sampled[rng.choices(range(n_bins), weights=[x["share"] for x in bins])[0]] += 1
    out = []
    for x, s in zip(bins, sampled):
        out.append({"bin": x["bin"], "s_start": round(x["s_start"], 3), "s_end": round(x["s_end"], 3),
                    "score_mean": round(x["score_mean"], 6), "share": round(x["share"], 6),
                    "expected_retries": round(x["share"] * failed_attempts, 4), "sampled_retries": s,
                    "label_tr": f"Bölge {x['bin']} ({x['s_start']:.0f}–{x['s_end']:.0f} m)"})
    return out


def _trim(seq: Sequence, keep: int = HISTORY_KEEP) -> Dict:
    """first `keep` + last `keep` items; returns {values, omitted} (values in order)."""
    seq = list(seq)
    if len(seq) <= 2 * keep:
        return {"values": seq, "omitted": 0}
    return {"values": seq[:keep] + seq[-keep:], "omitted": len(seq) - 2 * keep}


def assemble_result(payload: bytes, recs: List[SegRecord], data_wire_bits: int, feedback_wire_bits: int,
                    extra: Optional[Dict] = None, propagation: Optional[Dict] = None,
                    rng: Optional[random.Random] = None, n_spatial_bins: int = 10) -> Dict:
    delivered = bytearray()
    for r in recs:
        delivered += r.rx_data if r.rx_ok else b"\x00" * len(r.data)
    delivered = bytes(delivered)
    bit_errors = hamming(bitcheck.bytes_to_bits(payload), bitcheck.bytes_to_bits(delivered))
    failed = [r for r in recs if not r.rx_ok]
    undetected = [r for r in recs if r.rx_ok and r.rx_data != r.data]
    total_tx = sum(r.attempts for r in recs)
    try:
        text, enc = delivered.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, enc = delivered.hex(), "hex"
    retries = {str(r.index): max(r.attempts - 1, 0) for r in recs}
    hist: Dict[str, int] = {}
    for v in retries.values():
        hist[str(v)] = hist.get(str(v), 0) + 1
    segments = []
    for r in recs:
        crc_t, ber_t, err_t = _trim(r.crc_history), _trim([float(f"{b:.4g}") for b in r.ber_history]), \
            _trim(r.error_bits_history)
        segments.append({
            "index": r.index, "bytes_range": [r.start, r.end], "region_tr": region_label(r),
            "attempts": r.attempts, "retries": max(r.attempts - 1, 0), "final_ok": r.rx_ok,
            "acked": r.acked, "spurious_retransmissions": r.spurious,
            "failed_attempts": sum(1 for c in r.crc_history if not c),
            "crc_history": crc_t["values"], "ber_history": ber_t["values"],
            "error_bits_history": err_t["values"],
            "history_truncated": crc_t["omitted"] > 0, "history_omitted": crc_t["omitted"],
        })
    res = {
        "segments": segments,
        "retries_per_region": retries,
        "retries_histogram": hist,
        "total_transmissions": total_tx,
        "retransmissions": total_tx - len(recs),
        "spurious_retransmissions": sum(r.spurious for r in recs),
        "goodput_ratio": len(payload) * 8 / data_wire_bits if data_wire_bits else 0.0,
        "efficiency": len(recs) / total_tx if total_tx else 0.0,
        "data_wire_bits": data_wire_bits, "feedback_wire_bits": feedback_wire_bits,
        "delivered_payload": text, "delivered_encoding": enc,
        "delivered_ok": not failed and delivered == payload,
        "residual_errors": {
            "failed_segments": len(failed), "lost_bytes": sum(len(r.data) for r in failed),
            "undetected_segments": len(undetected), "bit_errors": bit_errors,
        },
        "n_segments": len(recs), "payload_bytes": len(payload),
        "histories_truncated": any(sg["history_truncated"] for sg in segments),
        "history_keep_each_side": HISTORY_KEEP,
    }
    if extra:
        res.update(extra)
    if propagation is not None:
        failed_attempts = sum(1 for r in recs for c in r.crc_history if not c)
        res["retries_by_spatial_region"] = spatial_retries(
            propagation, failed_attempts, rng or random.Random(0), n_spatial_bins)
    return res


# --------------------------------------------------------------------------
# the protocol
# --------------------------------------------------------------------------
def run_arq(payload: Union[bytes, str], segment_bytes: int = 8, max_retries: int = 5,
            channel: Optional[ArqChannel] = None, ber: Optional[float] = None,
            ack_ber: float = 0.0, seed: int = 0, propagation: Optional[Dict] = None,
            n_spatial_bins: int = 10, max_wire_bits: int = MAX_WIRE_BITS,
            trace: Optional[Dict] = None) -> Dict:
    """Run selective-repeat ARQ; see module docstring.  Pass either `channel` or a
    fixed forward `ber`.  Deterministic for a given seed."""
    if not 0 <= max_retries <= MAX_RETRIES:
        raise ValueError(f"max_retries must be within [0, {MAX_RETRIES}]")
    if not 0.0 <= ack_ber <= 0.5:
        raise ValueError("ack_ber must be within [0, 0.5]")
    if channel is None:
        if ber is None:
            raise ValueError("give a channel or a ber")
        if not 0.0 <= ber <= 1.0:
            raise ValueError("ber must be within [0, 1]")
        channel = ArqChannel(ber_override=ber)
    rng = random.Random(seed)
    fading = FadingProcess(rng, channel.fading_rho)
    payload, recs = split_payload(payload, segment_bytes)
    framed_segs = [frame_segment(i, r.data) for i, r in enumerate(recs)]

    pending = list(range(len(recs)))
    seg_err = [0] * len(recs)        # trace mode: data bit errors of the receiver's current copy
    if trace is not None:
        trace.update({"rounds": [], "first_pass": {}, "last_rx": {}})
    rounds = []
    data_bits = fb_bits = 0
    budget_exhausted = False
    while pending:
        nxt, entries = [], []
        if trace is not None:
            tr_round = {"resent": [], "became_ok": [], "round": len(rounds)}
            trace["rounds"].append(tr_round)
        for i in pending:
            r = recs[i]
            if data_bits >= max_wire_bits:           # runtime guard: give up the rest
                budget_exhausted = True
                r.gave_up = True
                continue
            if r.attempts > 0 and r.rx_ok:
                r.spurious += 1
            tx = framed_segs[i]
            ber_t = channel.ber_from_z(fading.next())
            rx = flip_bits(tx, ber_t, rng)
            parsed = parse_segment(rx)
            data_bits += len(tx)
            r.record_attempt(parsed["ok"], ber_t, hamming(tx, rx))
            if trace is not None:
                tr_round["resent"].append(i)
                if not r.rx_ok:
                    trace["last_rx"][i] = parsed["data"]
                    seg_err[i] = hamming(bitcheck.bytes_to_bits(r.data),
                                         bitcheck.bytes_to_bits(parsed["data"]))
                    if len(rounds) == 0:
                        trace["first_pass"][i] = parsed["data"]
            if parsed["ok"]:
                if parsed["seq"] == i and not r.rx_ok:
                    r.rx_ok, r.rx_data = True, parsed["data"]
                    if trace is not None:
                        tr_round["became_ok"].append(i)
                        seg_err[i] = hamming(bitcheck.bytes_to_bits(r.data),
                                             bitcheck.bytes_to_bits(parsed["data"]))
                status = ACK
            else:
                status = NACK
            fb = flip_bits(frame_ack(i, status), ack_ber, rng)
            fb_bits += ACK_FRAME_BITS
            pa = parse_ack(fb)
            fb_lost = not pa["ok"]
            if not fb_lost and pa["status"] == ACK and pa["seq"] == i:
                r.acked = True
            else:
                if r.attempts <= max_retries:
                    nxt.append(i)
                else:
                    r.gave_up = True
            entries.append({"segment": i, "crc_ok": bool(parsed["ok"]), "feedback_lost": fb_lost,
                            "ack_lost": fb_lost and status == ACK})
        rounds.append({"round": len(rounds), "sent": [e["segment"] for e in entries],
                       "crc_ok": [e["crc_ok"] for e in entries],
                       "n_sent": len(entries), "n_crc_ok": sum(e["crc_ok"] for e in entries),
                       "feedback_lost": sum(e["feedback_lost"] for e in entries),
                       "acks_lost": sum(e["ack_lost"] for e in entries)})
        if trace is not None:
            tr_round["bit_errors_after"] = sum(seg_err)
            tr_round["segments_ok_after"] = sum(1 for x in recs if x.rx_ok)
        pending = nxt
    # compress the middle rounds (counts kept, per-segment lists dropped)
    n_rounds = len(rounds)
    rounds_truncated = n_rounds > 2 * ROUND_DETAIL_KEEP
    out_rounds = []
    for rd in rounds:
        if rounds_truncated and ROUND_DETAIL_KEEP <= rd["round"] < n_rounds - ROUND_DETAIL_KEEP:
            rd = {k: v for k, v in rd.items() if k not in ("sent", "crc_ok")}
            rd["detail_omitted"] = True
        out_rounds.append(rd)
    rounds = out_rounds
    extra = {"rounds": rounds, "n_rounds": n_rounds, "rounds_truncated": rounds_truncated,
             "budget_exhausted": budget_exhausted, "max_wire_bits": max_wire_bits,
             "forward_mean_ber": channel.mean_ber(), "forward_effective_ber": channel.faded_mean_ber(),
             "ack_ber": ack_ber, "max_retries": max_retries,
             "segment_bytes": segment_bytes,
             "lost_acks": sum(rd["acks_lost"] for rd in rounds),
             "lost_feedback_frames": sum(rd["feedback_lost"] for rd in rounds)}
    return assemble_result(payload, recs, data_bits, fb_bits, extra, propagation, rng, n_spatial_bins)


# --------------------------------------------------------------------------
# end-to-end send: whole message first, then check + selective re-insertion
# --------------------------------------------------------------------------
def run_end_to_end(bits: Sequence[int], segment_bytes: int, max_retries: int, channel: ArqChannel,
                   ack_ber: float = 0.0, seed: int = 0) -> Dict:
    """Send `bits` over the full link first (round 0 = every segment), then the receiver checks the
    per-segment CRC-32 and the whole-message CRC-32, requests the failed segments again and re-inserts
    the corrected ones at their position, until everything verifies or max_retries is spent."""
    bits = [int(b) & 1 for b in bits]
    if not bits:
        raise ValueError("bits must not be empty")
    pad = (-len(bits)) % 8
    payload = bitcheck.bits_to_bytes(bits + [0] * pad)
    seg_used = max(segment_bytes, math.ceil(len(payload) / MAX_SEGMENTS))
    tr: Dict = {}
    res = run_arq(payload, seg_used, max_retries, channel=channel, ack_ber=ack_ber, seed=seed, trace=tr)
    _, recs = split_payload(payload, seg_used)

    def _copy(src: Dict, i: int) -> bytes:       # receiver copy; zeros if never received (budget guard)
        return src.get(i, b"\x00" * len(recs[i].data))

    first = b"".join(_copy(tr["first_pass"], i) for i in range(len(recs)))
    final = b"".join(_copy(tr["last_rx"], i) for i in range(len(recs)))
    first_bits = bitcheck.bytes_to_bits(first)[:len(bits)]
    final_bits = bitcheck.bytes_to_bits(final)[:len(bits)]
    crc_sent, crc_recv = crc32(payload), crc32(final)
    n_rounds = len(tr["rounds"])
    out_rounds = []
    for tr_rd in tr["rounds"]:
        k = tr_rd["round"]
        detail = not (n_rounds > 2 * ROUND_DETAIL_KEEP and ROUND_DETAIL_KEEP <= k < n_rounds - ROUND_DETAIL_KEEP)
        rd = {"round": k, "kind": "first_pass" if k == 0 else "retransmission",
              "n_resent": len(tr_rd["resent"]), "n_became_ok": len(tr_rd["became_ok"]),
              "became_ok": [{"segment": i, "bit_start": recs[i].start * 8,
                             "bit_end": min(len(bits), (recs[i].end + 1) * 8),
                             "bits": bitcheck.bytes_to_bits(_copy(tr['last_rx'], i))[
                                 :max(0, min(len(bits), (recs[i].end + 1) * 8) - recs[i].start * 8)]}
                            for i in tr_rd["became_ok"]],
              "still_failed": res["n_segments"] - tr_rd["segments_ok_after"],
              "segments_ok_after": tr_rd["segments_ok_after"],
              "bit_errors_after": tr_rd["bit_errors_after"],
              "all_ok_after": tr_rd["segments_ok_after"] == res["n_segments"]}
        if detail:
            rd["resent"] = tr_rd["resent"]
        else:
            rd["detail_omitted"] = True
        out_rounds.append(rd)
    n_failed = res["residual_errors"]["failed_segments"]
    bit_errors = sum(1 for a, b in zip(bits, final_bits) if a != b)
    ok = n_failed == 0 and crc_sent == crc_recv and bit_errors == 0
    return {
        "arq": res, "rounds": out_rounds, "n_rounds": n_rounds,
        "segment_bytes_used": seg_used, "pad_bits": pad, "n_segments": len(recs),
        "first_pass_bits": first_bits, "reassembled_bits": final_bits,
        "first_pass_bit_errors": sum(1 for a, b in zip(bits, first_bits) if a != b),
        "final_check": {
            "crc_algorithm": "CRC-32 (IEEE 802.3)", "message_crc_sent": f"{crc_sent:08X}",
            "message_crc_received": f"{crc_recv:08X}", "ok": bool(ok),
            "residual_errors": {"failed_segments": n_failed, "bit_errors": bit_errors,
                                "undetected_segments": res["residual_errors"]["undetected_segments"]},
            "retries_exhausted": n_failed > 0,
        },
    }

