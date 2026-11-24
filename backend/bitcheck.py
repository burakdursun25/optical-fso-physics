"""Bit-level input/output verification for the FSO link.

Contents
--------
* bytes/str <-> bit-list helpers (MSB first)
* frame builder/parser: preamble | sync | 16-bit length | payload | CRC-32
* compare_bits: sent vs. received statistics (BER, flips, bursts, per-byte map)
* a deterministic (seeded) binary-symmetric channel driven by a link budget
* run_link_check: frame -> channel -> compare + CRC, returned as a JSON dict

Bits are lists of ints 0/1.  A received bit may be ``None`` = erasure (the
channel was down / the bit never arrived); erasures always count as errors.
(The legacy DataTransmissionSimulator reports 0 for a dropped channel, which
would make zeros look correct - this module deliberately does not do that.)
"""
import math
import random
from typing import Dict, List, Optional, Sequence, Union

from backend.engine import crc32

Bit = Optional[int]

PREAMBLE = bytes([0xAA, 0xAA])
SYNC_WORD = bytes([0x2D, 0xD4])
CRC_BITS = 32                                   # CRC-32 field
HEADER_BYTES = len(PREAMBLE) + len(SYNC_WORD) + 2   # + 16-bit length
MAX_PAYLOAD = 0xFFFF


# --------------------------------------------------------------------------
# bits <-> bytes
# --------------------------------------------------------------------------
_BYTE_TO_BITS = tuple(tuple((byte >> (7 - i)) & 1 for i in range(8)) for byte in range(256))


def bytes_to_bits(data: Union[bytes, bytearray, str]) -> List[int]:
    """MSB-first bit list. str is encoded as UTF-8."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    out: List[int] = []
    extend = out.extend
    table = _BYTE_TO_BITS
    for byte in data:
        extend(table[byte])
    return out


def bits_to_bytes(bits: Sequence[Bit]) -> bytes:
    """Pack MSB-first bits into bytes; length must be a multiple of 8 and
    erasures (None) are not allowed here."""
    n = len(bits)
    if n % 8:
        raise ValueError("bit count must be a multiple of 8")
    out = bytearray(n >> 3)
    for i in range(0, n, 8):
        b0, b1, b2, b3, b4, b5, b6, b7 = (
            bits[i], bits[i + 1], bits[i + 2], bits[i + 3],
            bits[i + 4], bits[i + 5], bits[i + 6], bits[i + 7],
        )
        if (b0 not in (0, 1) or b1 not in (0, 1) or b2 not in (0, 1) or b3 not in (0, 1)
                or b4 not in (0, 1) or b5 not in (0, 1) or b6 not in (0, 1) or b7 not in (0, 1)):
            raise ValueError("bits must be 0 or 1")
        out[i >> 3] = (b0 << 7) | (b1 << 6) | (b2 << 5) | (b3 << 4) | (b4 << 3) | (b5 << 2) | (b6 << 1) | b7
    return bytes(out)


def bits_to_str(bits: Sequence[Bit]) -> str:
    return "".join("?" if b is None else str(b) for b in bits)


# --------------------------------------------------------------------------
# framing
# --------------------------------------------------------------------------
def build_frame(payload: Union[bytes, bytearray, str]) -> List[int]:
    """preamble(AA AA) | sync(2D D4) | length(16b) | payload | CRC-32 (IEEE 802.3).

    The CRC covers length field + payload.
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload too long (max {MAX_PAYLOAD} bytes)")
    length = len(payload).to_bytes(2, "big")
    crc = crc32(length + payload).to_bytes(4, "big")
    return bytes_to_bits(PREAMBLE + SYNC_WORD + length + payload + crc)


def _hamming(a: Sequence[int], b: Sequence[int]) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


def parse_frame(bits: Sequence[Bit]) -> Dict[str, object]:
    """Parse a received frame (fixed-offset framing).

    Returns {payload (bytes|None), crc_ok, sync_ok, preamble_bit_errors,
    sync_bit_errors, length_field, crc_received, crc_computed, errors:[str]}.
    crc_ok is True only if the frame is structurally complete, has no erasures
    and the CRC matches.
    """
    errors: List[str] = []
    res: Dict[str, object] = {
        "payload": None, "crc_ok": False, "sync_ok": False,
        "preamble_bit_errors": None, "sync_bit_errors": None,
        "length_field": None, "crc_received": None, "crc_computed": None,
        "errors": errors,
    }
    n_erased = sum(1 for b in bits if b is None)
    if n_erased:
        errors.append(f"{n_erased} erased bit(s)")
    if len(bits) < HEADER_BYTES * 8 + CRC_BITS:
        errors.append("frame truncated: shorter than header + CRC")
        return res
    clean = [0 if b is None else int(b) for b in bits]

    pre_bits = bytes_to_bits(PREAMBLE)
    sync_bits = bytes_to_bits(SYNC_WORD)
    p0 = len(pre_bits)
    s0 = p0 + len(sync_bits)
    res["preamble_bit_errors"] = _hamming(clean[:p0], pre_bits)
    res["sync_bit_errors"] = _hamming(clean[p0:s0], sync_bits)
    res["sync_ok"] = res["sync_bit_errors"] == 0
    if not res["sync_ok"]:
        errors.append(f"sync word mismatch ({res['sync_bit_errors']} bit errors)")

    length = int.from_bytes(bits_to_bytes(clean[s0:s0 + 16]), "big")
    res["length_field"] = length
    end = s0 + 16 + length * 8 + CRC_BITS
    if len(clean) < end:
        errors.append(f"frame truncated: length field {length} needs {end} bits, got {len(clean)}")
        return res
    body = bits_to_bytes(clean[s0 + 16:end - CRC_BITS])
    crc_rx = int.from_bytes(bits_to_bytes(clean[end - CRC_BITS:end]), "big")
    crc_calc = crc32(length.to_bytes(2, "big") + body)
    res["payload"] = body
    res["crc_received"] = crc_rx
    res["crc_computed"] = crc_calc
    crc_match = crc_rx == crc_calc
    if not crc_match:
        errors.append("CRC mismatch")
    res["crc_ok"] = bool(crc_match and res["sync_ok"] and not n_erased)
    return res


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------
def compare_bits(sent: Sequence[int], received: Sequence[Bit]) -> Dict[str, object]:
    """Compare sent vs received bits (JSON-serializable result).

    Received bits missing at the tail (shorter list) and None entries are
    erasures and count as errors.  Keys: total, errors, ber, error_positions,
    erasures, flips_0_to_1, flips_1_to_0, longest_burst, burst_start,
    byte_errors (bit errors per byte), bytes_with_errors.
    """
    total = len(sent)
    rx = list(received[:total]) + [None] * max(0, total - len(received))
    positions: List[int] = []
    f01 = f10 = erasures = 0
    for i, (s, r) in enumerate(zip(sent, rx)):
        if r is None:
            erasures += 1
            positions.append(i)
        elif r != s:
            positions.append(i)
            if s == 0:
                f01 += 1
            else:
                f10 += 1
    longest = start = 0
    run = run_start = 0
    prev = None
    for p in positions:
        if prev is not None and p == prev + 1:
            run += 1
        else:
            run, run_start = 1, p
        if run > longest:
            longest, start = run, run_start
        prev = p
    byte_errors = [0] * ((total + 7) // 8)
    for p in positions:
        byte_errors[p // 8] += 1
    return {
        "total": total,
        "errors": len(positions),
        "ber": (len(positions) / total) if total else 0.0,
        "error_positions": positions,
        "erasures": erasures,
        "flips_0_to_1": f01,
        "flips_1_to_0": f10,
        "longest_burst": longest,
        "burst_start": start if longest else None,
        "byte_errors": byte_errors,
        "bytes_with_errors": sum(1 for e in byte_errors if e),
    }


# --------------------------------------------------------------------------
# channel model
# --------------------------------------------------------------------------
def ook_ber(snr_linear: float) -> float:
    """OOK / IM-DD bit error rate  BER = 0.5*erfc(sqrt(SNR)/(2*sqrt(2))) = Q(sqrt(SNR)/2).

    SNR is the electrical signal-to-noise power ratio (linear, I_signal^2/sigma^2
    with I_signal the on-off current swing).  SNR=0 gives 0.5.
    """
    if snr_linear < 0 or math.isnan(snr_linear):
        raise ValueError("snr_linear must be >= 0")
    return 0.5 * math.erfc(math.sqrt(snr_linear) / (2.0 * math.sqrt(2.0)))


def snr_from_received_power(p_rx_dBm: float, noise_rms_dBm: float = -30.0) -> float:
    """Electrical SNR from optical power: (P_rx / P_noise)^2 where P_noise is the
    rms noise-equivalent optical power of the receiver (thermal+shot, in dBm)."""
    return (10.0 ** ((p_rx_dBm - noise_rms_dBm) / 10.0)) ** 2


def ber_from_received_power(p_rx_dBm: float, noise_rms_dBm: float = -30.0) -> float:
    """OOK BER for a received optical power [dBm] and receiver noise floor [dBm]."""
    return ook_ber(snr_from_received_power(p_rx_dBm, noise_rms_dBm))


def apply_channel(bits: Sequence[int], ber: float, seed: int = 0) -> List[int]:
    """Binary symmetric channel: flip each bit with probability `ber`
    using random.Random(seed) (deterministic)."""
    if not 0.0 <= ber <= 1.0:
        raise ValueError("ber must be within [0, 1]")
    rng = random.Random(seed)
    return [b ^ 1 if rng.random() < ber else b for b in bits]


def link_power_dBm(preset: str, distance_m: float, tx_power_dBm: float = 10.0,
                   wavelength: float = 1550e-9, rx_radius: float = 0.025,
                   atmos_dB_per_km: float = 0.2, fixed_loss_dB: float = 3.0) -> Dict[str, float]:
    """Received power [dBm] = Tx power - geometric/optics loss (optics.lens_link_budget)
    - atmospheric loss (dB/km * km) - fixed implementation loss."""
    from backend.optics import lens_link_budget
    if distance_m < 0:
        raise ValueError("distance_m must be >= 0")
    lb = lens_link_budget(preset, distance_m, wavelength=wavelength, rx_radius=rx_radius)
    atmos = atmos_dB_per_km * distance_m / 1000.0
    p_rx = tx_power_dBm - lb["loss_with_dB"] - atmos - fixed_loss_dB
    return {"p_rx_dBm": p_rx, "optics_loss_dB": lb["loss_with_dB"],
            "atmospheric_loss_dB": atmos, "fixed_loss_dB": fixed_loss_dB,
            "lens_gain_dB": lb["gain_dB"]}


def link_ber(preset: str, distance_m: float, noise_rms_dBm: float = -30.0, **kw) -> float:
    """BER of the link at distance_m (see link_power_dBm for kwargs)."""
    return ber_from_received_power(link_power_dBm(preset, distance_m, **kw)["p_rx_dBm"],
                                   noise_rms_dBm)


# --------------------------------------------------------------------------
# end-to-end
# --------------------------------------------------------------------------
def run_link_check(payload: Union[bytes, str], distance_m: float, preset: str = "none",
                   seed: int = 0, tx_power_dBm: float = 10.0, noise_rms_dBm: float = -30.0,
                   wavelength: float = 1550e-9, rx_radius: float = 0.025,
                   atmos_dB_per_km: float = 0.2, fixed_loss_dB: float = 3.0,
                   sensitivity_dBm: Optional[float] = None) -> Dict[str, object]:
    """Frame `payload`, send it through the modelled channel, verify.

    If sensitivity_dBm is given and P_rx falls below it the channel is dropped:
    every bit is an erasure (counted as an error, never as a silent zero).
    """
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    link = link_power_dBm(preset, distance_m, tx_power_dBm, wavelength, rx_radius,
                          atmos_dB_per_km, fixed_loss_dB)
    ber = ber_from_received_power(link["p_rx_dBm"], noise_rms_dBm)
    sent = build_frame(raw)
    dropped = sensitivity_dBm is not None and link["p_rx_dBm"] < sensitivity_dBm
    received: List[Bit] = [None] * len(sent) if dropped else apply_channel(sent, ber, seed)
    cmp_ = compare_bits(sent, received)
    parsed = parse_frame(received)
    body = parsed["payload"]
    return {
        "payload_text": raw.decode("utf-8", errors="replace"),
        "payload_bytes": len(raw),
        "frame_bits": len(sent),
        "distance_m": distance_m, "preset": preset, "seed": seed,
        "link": {**link, "noise_rms_dBm": noise_rms_dBm,
                 "snr_linear": snr_from_received_power(link["p_rx_dBm"], noise_rms_dBm),
                 "ber_theory": ber, "channel_dropped": bool(dropped)},
        "comparison": cmp_,
        "frame": {
            "crc_ok": parsed["crc_ok"], "sync_ok": parsed["sync_ok"],
            "length_field": parsed["length_field"],
            "crc_received": parsed["crc_received"], "crc_computed": parsed["crc_computed"],
            "preamble_bit_errors": parsed["preamble_bit_errors"],
            "sync_bit_errors": parsed["sync_bit_errors"],
            "errors": parsed["errors"],
        },
        "payload_intact": body == raw,
        "received_payload_hex": body.hex() if body is not None else None,
        "sent_bits": "".join(map(str, sent)),
        "received_bits": bits_to_str(received),
    }


# --------------------------------------------------------------------------
# canned scenarios for reports
# --------------------------------------------------------------------------
SUMMARY_SCENARIOS = [
    ("Merhaba FSO", "none", 100.0), ("Merhaba FSO", "none", 3000.0),
    ("Merhaba FSO", "none", 4500.0), ("Merhaba FSO", "none", 6000.0),
    ("Merhaba FSO", "keplerian_telescope", 6000.0), ("Merhaba FSO", "keplerian_telescope", 15000.0),
    ("Merhaba FSO", "diverging", 1000.0),
]


def summary_rows(seed: int = 1) -> List[Dict[str, object]]:
    """Deterministic bit-check results for the canned scenarios (report table)."""
    rows = []
    for payload, preset, dist in SUMMARY_SCENARIOS:
        r = run_link_check(payload, dist, preset, seed=seed)
        rows.append({"payload": payload, "preset": preset, "distance_m": dist,
                     "p_rx_dBm": r["link"]["p_rx_dBm"], "ber_theory": r["link"]["ber_theory"],
                     "errors": r["comparison"]["errors"], "total": r["comparison"]["total"],
                     "ber": r["comparison"]["ber"], "crc_ok": r["frame"]["crc_ok"]})
    return rows
