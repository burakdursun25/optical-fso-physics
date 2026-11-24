# API contract v2: 3D propagation, selective-repeat ARQ, full duplex

Base URL: same FastAPI app (`backend/app.py`). All endpoints are `POST`, JSON in / JSON out.
Validation errors (out-of-range numbers, unknown enum values, oversized grids/payloads, source/target
outside the grid, ...) return **HTTP 422** (FastAPI format `{"detail": ...}`); unexpected failures return 500.
All responses are deterministic for a given request (every random draw is seeded by `seed`).
Every response stays below ~2 MB at maximum parameters (tested). Floats are rounded to 6 significant digits
(coordinates of centerline/rings to 10) to keep payloads small.

World coordinates: metres. `x` runs along the grid X axis (link direction), `y` horizontal, `z` = altitude.
Grid extent = `(gridX, gridY, gridZ) * cellSize`. Default link: `(0, Y/2, Z/2)` to `(X, Y/2, Z/2)`.

Shared enum `LensPresetName`: `none | collimating | focusing | keplerian_telescope | galilean_telescope | cassegrain | diverging`
(`focusing` is a *receive* lens: allowed only as `rxLens`; transmit presets only as `lensPreset` / `txLens`).

---------------------------------------------------------------------------------------------------

## 1. `POST /api/propagation3d`

3D Gaussian-beam propagation through the atmosphere grid (built exactly like `/api/simulate`, via
`build_atmosphere(SimParams)`), with turbulence broadening, refraction bending, 1/e^2 rings for a 3D beam tube
and a "degradation cloud" showing where the light is most degraded.

### Request (`Propagation3DParams`); every field optional

| field | type / bounds | default | meaning |
|---|---|---|---|
| `atmosphere` | `SimParams` object (same fields as `/api/simulate`: `gridX 1..600, gridY 1..100, gridZ 1..100, cellSize, thermalProfile, baseTemp, deltaT, humidity, weather, Cn2Level, windSpeed, wavelengthNm, environmentPreset`, ...) | defaults of `SimParams` | **cells `gridX*gridY*gridZ` must be <= 40 000** (422 otherwise; building the grid is pure Python) |
| `source`, `target` | `[x,y,z]` metres, inside grid, must differ | grid mid-height, x=0 to x=X | beam start / aim point |
| `w0` | float, 0 < w0 <= 1 | preset default (2 mm without lens) | source waist radius [m] |
| `lensPreset` | `LensPresetName` (not `focusing`) | `none` | transmit optics (ABCD, clipping + surface losses) |
| `nSamples` | int 20..400 | 200 | samples along the link |
| `nRays` | int 3..24 | 16 | points per ring (marginal rays) |
| `seed` | int >= 0 | 0 | only rotates the ring phase (the model itself is deterministic) |
| `rxRadiusM` | float > 0, <= 10 | 0.05 | receiver aperture radius for `power_frac` |
| `m2` | 1..10 | 1 | beam quality factor |
| `maxVoxels` | int 1..4000 | 2000 | cap of returned voxels (top-K by score) |
| `anomalies` | list (max 8) of `{center:[x,y,z], size:[dx,dy,dz], cn2_factor>=0 (default 1), extra_alpha_dB_km 0..1000 (default 0), delta_T -100..100 (default 0)}` | `[]` | box-shaped disturbances: fog patch, turbulent pocket, warm/cold bubble (changes n). Applied to a private copy; the grid is untouched. Use these to demo the hotspot detector |
| `distanceM` | float 100..20000 (m), optional | null | link length. When set, the backend rebuilds the grid itself: `gridY/gridZ` are kept (reduced only if 20 x-cells would not fit in 40 000 cells), `gridX = clamp(round(distanceM / atmosphere.cellSize), 20, min(600, 40000/(gridY*gridZ)))`, `cellSize = distanceM / gridX` (so bounds.x == distanceM exactly). All other `atmosphere` fields are used unchanged. Default endpoints then span 0..distanceM; explicit `source`/`target` are checked against the derived bounds. Out of range -> 422. Without `distanceM` the request behaves exactly as before. `meta.grid` echoes the grid actually used |

### Response

```jsonc
{
  "centerline": [            // nSamples+1 entries, s = 0 .. L
    { "s": 75.0,             // arclength from source [m]
      "x": 75.0000063, "y": 300.000067, "z": 299.9999263,   // beam centroid incl. refraction bending [m]
      "w": 0.0186095,        // vacuum Gaussian 1/e^2 radius (optics.beam_radius / lens system) [m]
      "w_lt": 0.0186096,     // long-term radius incl. turbulence spread, w_lt >= w [m]
      "wander": 4.7e-05,     // rms beam wander at this distance [m]
      "power_frac": 0.99655, // cumulative power fraction reaching a rxRadiusM aperture placed here (0..1):
                             //   10^(-atten/10) * lens transmission * aperture/pointing coupling
      "atten_dB": 0.015,     // cumulative extinction [dB]
      "scint_index": 4.4e-05,// scintillation index sigma_I^2 (cumulative)
      "sigma2_R": 4.4e-05,   // Rytov variance (cumulative)
      "deflection": 9.97e-05,// lateral centroid deflection from the straight line [m]
      "n": 1.000198528, "cn2": 2.77e-16, "temp_C": 22.98, "humidity": 49.86,   // local cell values (interpolated)
      "alpha_dB_km": 0.2, "grad_n": 3.1e-08, // local extinction and |grad n| [1/m]
      "score": 0.268 }       // local degradation score 0..1 on the beam path
  ],
  "rings": [                 // <= 64 rings (decimated); each ring = nRays points [x,y,z] on the w_lt envelope
    [[0.0, 299.99888, 300.00166], [0.0, 299.998, 299.99986], "..."]
  ],
  "ring_sample_indices": [0, 1, 2],   // rings[i] belongs to centerline[ring_sample_indices[i]]
  "degradation_cloud": {
    "voxels": [              // <= maxVoxels (<= 4000), sorted by score desc; one voxel = one grid cell
      { "x": 1850.0, "y": 350.0, "z": 250.0,      // cell centre [m]
        "score": 0.664,                           // 0..1
        "atten_dB_km": 60.2, "cn2": 3.1e-16, "grad_n": 5.4e-08,
        "scint": 9.1e-05 }                        // local Rytov variance contribution of the cell
    ],
    "max_score": 0.664126, "n_voxels": 3, "cell_size": 100.0,   // cell_size = edge length to draw voxels with
    "hotspot": { "x": 2025.0, "y": 300.0012, "z": 299.9729, "s": 2025.0, "score": 0.631,
                 "dominant": "attenuation",                    // attenuation | turbulence | gradient
                 "reason_tr": "Yoğun zayıflama (sis/yağmur/kar) — ışık burada en çok sönümleniyor",
                 "reason_en": "Strong extinction (fog/rain/snow): light is attenuated most here" }
  },
  "summary": {
    "length_m": 3000.0, "total_atten_dB": 24.225,
    "final_w": 0.740073, "final_w_lt": 0.74022,
    "wander_m": 0.0119928, "deflection_m": 0.0381311,
    "power_at_rx_frac": 3.41e-05, "power_at_rx_dB": 44.67,     // loss, positive dB
    "sigma2_R": 0.0412805, "scint_index": 0.0412927,
    "lens_transmission": 1.0, "weather_alpha_dB_km": 0.2,
    "worst_segment": { "s_start": 1875.0, "s_end": 2175.0, "score": 0.6245 }   // worst 10 % window of the link
  },
  "bounds": { "x": 3000.0, "y": 600.0, "z": 600.0 },           // grid extent [m]: use for the scene box
  "meta": { "wavelengthNm": 1550, "weather": "clear", "cells": 1080,
            "source": [0.0, 300.0, 300.0], "target": [3000.0, 300.0, 300.0] }
}
```

**How to draw it.** Tube: connect `rings[i]` to `rings[i+1]` (same index = same angle). Centreline: polyline of
`centerline[*].{x,y,z}`; note the centroid can miss the target by `summary.deflection_m`. Colour the tube by
`centerline[i].score` or `power_frac`. Voxels: cube of edge `cell_size` at `(x,y,z)`, colour/alpha by `score`
(0..1, absolute, not renormalised: compare runs). Mark `hotspot` with a marker + `reason_tr`.

**Models** (documented in `backend/propagation3d.py`): Rytov variance with profile
`sigma_R^2 = 2.25 k^(7/6) Int Cn2(s')(s-s')^(5/6) ds'`; long-term radius
`w_LT = w sqrt(1 + 1.33 sigma_R^2 Lambda^(5/6))`, `Lambda = 2s/(k w^2)`; wander
`<r_c^2> = 2.42 Cn2_avg s^3 w(0)^(-1/3)`; refraction by integrating `d/ds(n dr/ds) = grad n` with RK4 (bends toward
higher n); pointing/aperture coupling after Farid & Hranilovic. Voxel score =
`beam_share * (0.35 f_att + 0.40 f_cn2 + 0.25 f_grad)` with saturating references (10 dB/km, log10 Cn2 in [-17,-13],
|grad n| 5e-8 /m); `beam_share` = 1 within 3 w_LT of the beam (box distance) and decays with half a cell length outside.

---------------------------------------------------------------------------------------------------

## 2. `POST /api/arq`

CRC-based selective-repeat ARQ. Payload is cut into `segmentBytes` segments, each framed
`seq(16b) | len(8b) | data | CRC-32` (IEEE 802.3; was CRC-16/CCITT before the CRC-32 switch); the receiver ACKs/NACKs per segment over a reverse channel with BER
`ackBer`; only bad (NACKed) or unacknowledged segments are resent, at most `1 + maxRetries` attempts each. A lost ACK
causes a **spurious retransmission** (counted).

Forward channel, one of (priority order): fixed `ber`; else `propagation` (channel derived from the 3D result:
`P_rx = txPowerDbm - power_at_rx_dB - fixedLossDb`, scintillation from the result); else the analytic link model
(`lensPreset, distanceM, wavelengthNm, rxRadiusM, atmosDbPerKm, cn2, ...`). Unless `ber` is fixed, every transmission
sees log-normal fading (`sigma_I^2`) from a seeded AR(1) sequence (`fadingRho`), so retries can succeed.

### Request (`ArqParams`)

| field | bounds | default |
|---|---|---|
| `payload` | UTF-8 text, 1..2048 bytes; <= 256 segments | demo text |
| `segmentBytes` | 1..255 | 8 |
| `maxRetries` | 0..200 | 5 |
| `seed` | int >= 0 | 0 |
| `ackBer` | 0..0.5 | 0 |
| `ber` | 0..1 or null (fixed forward BER, disables link model + fading) | null |
| `distanceM, lensPreset, txPowerDbm, wavelengthNm, rxRadiusM, atmosDbPerKm, fixedLossDb, extraLossDb, noiseRmsDbm, cn2, fadingRho` | bounded; link model | 1000 m, `none`, 10 dBm, 1550, 0.025, 0.2, 3, 0, -30, 1e-15, 0 |
| `spatialBins` | 2..20 | 10 |
| `propagation` | `Propagation3DParams` or null | null (also enables `retries_by_spatial_region`, even with fixed `ber`) |

### Response

```jsonc
{
  "segments": [
    { "index": 1, "bytes_range": [8, 15], "region_tr": "Segment 1 (bayt 8–15)",
      "attempts": 11, "retries": 10, "final_ok": true,       // final_ok: receiver holds a CRC-valid copy
      "acked": true,                                          // sender saw the ACK
      "spurious_retransmissions": 4,
      "crc_history": [false, false, true],                    // one entry per attempt (see truncation below)
      "ber_history": [0.01, 0.01, 0.01],                      // channel BER of each attempt
      "error_bits_history": [1, 2, 0],                        // bit flips actually injected
      "failed_attempts": 2,                                   // exact count of CRC failures, even when truncated
      "history_truncated": false, "history_omitted": 0 }
  ],
  "retries_per_region": { "0": 0, "1": 10, "2": 3 },          // segment index -> retries
  "retries_histogram":  { "0": 1, "3": 1, "10": 1 },          // retries -> number of segments
  "total_transmissions": 34, "retransmissions": 29, "spurious_retransmissions": 21,
  "lost_acks": 10, "lost_feedback_frames": 18,
  "goodput_ratio": 0.0904,     // payload bits / all data bits on the wire (incl. headers + retransmissions)
  "efficiency": 0.147,         // n_segments / total_transmissions
  "data_wire_bits": 2920, "feedback_wire_bits": 1360,
  "delivered_payload": "…", "delivered_encoding": "utf-8",    // "hex" if the bytes are not valid UTF-8
  "delivered_ok": true,
  "residual_errors": { "failed_segments": 0, "lost_bytes": 0, "undetected_segments": 0, "bit_errors": 0 },
                       // failed segments are delivered as zero bytes; undetected = CRC passed, data wrong
  "n_segments": 5, "payload_bytes": 33, "n_rounds": 11,
  "rounds": [ { "round": 0, "sent": [0,1,2,3,4], "crc_ok": [true,false,false,false,true],
                "n_sent": 5, "n_crc_ok": 2, "feedback_lost": 1, "acks_lost": 1 } ],
  "rounds_truncated": false, "budget_exhausted": false, "histories_truncated": false,
  "forward_mean_ber": 0.01, "ack_ber": 0.02, "max_retries": 10, "segment_bytes": 8,
  "channel": { "p_rx_dBm": -23.3, "sigma_I2": 0.1056, "mean_ber": 0.0101 },   // nulls when `ber` fixed
  // only when `propagation` was given:
  "retries_by_spatial_region": [
    { "bin": 2, "s_start": 1500.0, "s_end": 2250.0, "score_mean": 0.4456, "share": 0.351,
      "expected_retries": 7.02, "sampled_retries": 8, "label_tr": "Bölge 2 (1500–2250 m)" } ],
  "propagation_summary": { "summary": { "…": "as in /api/propagation3d" }, "hotspot": { "…": "…" } }
}
```

`retries_by_spatial_region` is a *heuristic attribution*: a CRC failure cannot tell where the bits were damaged,
so each failed attempt is apportioned to the `spatialBins` equal-distance bins in proportion to the mean local
degradation score (floor 1e-3) of each bin. `expected_retries = share * failed_attempts`; `sampled_retries` is the seeded
random draw (their sums equal the number of failed attempts). Use it to colour the link segments.

---------------------------------------------------------------------------------------------------

## 3. `POST /api/duplex`

Full-duplex wavelength-division link A <-> B in discrete time slots. Both terminals transmit **in every slot**
(a terminal with nothing left to send emits feedback-only frames). Frame = `type | seq | fb | len | data | CRC-32`;
`fb` piggybacks the ACK/NACK for the frame received from the peer in the previous slot. A bad reverse frame loses its
data *and* its feedback (time-out -> possible spurious retransmission). Same atmosphere both ways but wavelength-
dependent attenuation/diffraction/scintillation, independent fading draws per direction (correlation `reciprocityRho`),
and near-end crosstalk from the receiver's own transmitter: `P_xt = P_tx(dst) - nearEndCouplingDb - filterIsolationDb(dst)`,
`SNR = P_rx^2 / (sigma_noise^2 + P_xt^2)`.

### Request (`DuplexParams`)

| field | bounds | default |
|---|---|---|
| `terminalA`, `terminalB` | `TerminalParams` | A: 1550 nm, B: 1310 nm |
| `payloadA`, `payloadB` | text, 1..512 bytes, <= 64 segments each | demo texts |
| `distanceM` | 0 < d <= 1e6 | 2000 |
| `weather` | `clear \| haze \| fog \| rain \| snow \| storm` | clear |
| `cn2` | 0..1e-10 | 1e-15 |
| `segmentBytes` / `maxRetries` | 1..255 / 0..200 | 8 / 5 |
| `reciprocityRho` / `fadingRho` | 0..1 / 0..<1 | 0.5 / 0 |
| `bitrateBps` | 1e3..1e12 | 1e9 |
| `seed`, `fixedLossDb` | int >= 0; 0..100 | 0; 3 |
| `berOverride` | 0..1 or null (forces fixed BER on both directions) | null |
| `propagation` | `Propagation3DParams` or null: runs the 3D model for A->B (at A's wavelength) and B->A (reversed path, B's wavelength); their `total_atten_dB` / `scint_index` replace the weather/Cn2 model and `distanceM` is taken from the path | null |

`TerminalParams`: `txPowerDbm -60..60 (10)`, `wavelengthNm 200..20000`, `txLens` (not `focusing`), `rxLens` (`none | focusing`),
`rxRadiusM (0.025)`, `filterIsolationDb 0..120 (40)`, `sensitivityDbm (-40)`, `noiseRmsDbm (-30)`, `nearEndCouplingDb 0..120 (30)`.

### Response

```jsonc
{
  "directions": {
    "A→B": {                                   // keys are literally "A→B" and "B→A"
      "link_budget": { "wavelength_nm": 1550.0, "p_tx_dBm": 10.0, "p_rx_dBm": -16.31,
                       "optics_loss_dB": 22.9, "atmos_loss_dB": 0.4, "atmos_alpha_dB_km": 0.2, "fixed_loss_dB": 3.0,
                       "extra_loss_dB": 0.0, "crosstalk_dBm": -60.0, "sir_dB": 43.7, "sigma_I2": 0.0707,
                       "margin_dB": 23.7, "viable": true,
                       "ber_no_crosstalk": 5.96e-32, "ber_mean_power": 5.96e-32 },
      "ber": 5.96e-32,                          // mean-power BER including crosstalk
      "arq_result": { "…": "same shape as /api/arq (no `rounds`), plus piggybacked_feedback:true, frames_sent" },
      "throughput_bps": 853333333.3,            // all frame bits sent / elapsed time
      "goodput_bps": 386666666.7                // CRC-valid payload bits / elapsed time
    },
    "B→A": { "…": "…" }
  },
  "timeline": [                                 // one entry per slot, both terminals always transmit
    { "slot": 0,
      "a_tx": { "seq": 0, "type": "DATA", "fb": "NONE" },   // type: DATA | RETX | ACK (feedback-only, seq null)
      "b_tx": { "seq": 0, "type": "DATA", "fb": "NONE" },   // fb: piggybacked NONE | ACK | NACK
      "a_rx_ok": true,                                      // A received B's frame with a valid CRC
      "b_rx_ok": true } ],
  "summary": { "both_delivered": true, "total_slots": 5, "slot_duration_s": 1.2e-07, "total_time_s": 6e-07,
               "bitrate_bps": 1e9, "timed_out": false,
               "asymmetry_dB": -1.457,          // P_rx(A→B) - P_rx(B→A); negative = B→A (1310 nm) stronger
               "asymmetry_note": "…" },
  "propagation_summary": { "A→B": { "summary": {}, "hotspot": {} }, "B→A": {} }   // only with `propagation`
}
```

`slot_duration_s = (segmentBytes + 7) * 8 / bitrateBps`. Frame synchronisation is assumed; only bit errors are modelled.
The default clear-air 1310 vs 1550 nm asymmetry is geometric (shorter wavelength = narrower diffraction beam, captured
by the same aperture); 1310 nm scintillates more (`sigma_R^2 ~ lambda^(-7/6)`).


## Retry limit 200 and response bounds

`maxRetries` is accepted up to **200** (`/api/arq`, `/api/duplex`, `/api/send-data`); 201 -> HTTP 422. To keep response
size (< 2 MB) and runtime (~ 1 s typical, < 3 s worst case) bounded:

* **Per-segment histories** (`crc_history`, `ber_history`, `error_bits_history`) keep the first 8 + last 8 attempts when a
  segment needed more than 16; `history_truncated: true` and `history_omitted: N` say so. `attempts`, `retries` and
  `failed_attempts` always stay exact; the top level has `histories_truncated`.
* **Rounds** (`/api/arq`): beyond 40 rounds only the first 20 and last 20 keep their `sent`/`crc_ok` lists; middle rounds have
  `detail_omitted: true` and only the counts `n_sent`, `n_crc_ok`, `feedback_lost`, `acks_lost`. `rounds_truncated` flags it.
* **Wire-bit budget**: one ARQ run may put at most 6 000 000 forward bits on the wire (`max_wire_bits`). When exhausted the
  remaining segments give up (`delivered_ok: false`) and `budget_exhausted: true` is reported.
* **Duplex timeline**: at most 2000 slots are returned (first 1000 + last 1000): `timeline_truncated`,
  `timeline_total_slots`, `timeline_omitted`. The slot simulation itself is capped at 10 000 slots (`summary.timed_out`).

## POST /api/send-data (end-to-end CRC check + retransmission)

Request additions to `SendDataParams` (old fields `simParams`, `pattern`, `numBits` unchanged):

| field | range | default |
|---|---|---|
| `useArq` | bool; `false` = legacy deterministic per-bit transmission only (response has `arq: null`) | true |
| `segmentBytes` | 1..255 (raised automatically so that there are <= 256 segments; see `segment_bytes_used`) | 4 |
| `maxRetries` | 0..200 | 20 |
| `seed` | int >= 0 (channel fading + bit flips are deterministic per seed) | 0 |

Flow: the whole message (round 0 = every segment) is sent over the **actual link distance** `gridX * cellSize`
(`distance_used_m`, not a fixed 6 km). Only after it has arrived does the receiver check each segment's CRC-32 and the
whole-message CRC-32, request the failed segments again, and re-insert the corrected ones at their position in the
receive buffer; this repeats until everything verifies or `maxRetries` is spent. Channel: received power and Rytov
scintillation from the engine link budget at that distance, noise floor chosen so BER = 1e-9 at `receiverSensitivity_dBm`;
a ray that misses the receiver (`link.beam_blocked`) gives BER 0.5. Message bits are padded with zero bits to a byte
boundary (`pad_bits`); all bit arrays are cropped back to `numBits`.

Legacy fields (`data`, `results`, `totalBits`, `correctBits`, `bitErrorRate`, `totalOnes`, `totalZeros`, `missedOnes`,
`missedZeros`, `successRate`) now describe the **first pass** (before any retransmission). For messages the per-bit `trace`
is only present on the last `results` entry (and `linkBudget` only on the last entry when `numBits > 512`).

New fields:

```jsonc
{
  "distance_used_m": 6000.0, "grid_distance_m": 6000.0,
  "link": { "p_rx_dBm": -45.4, "noise_rms_dBm": -50.8, "sigma_I2": 0.1, "mean_ber": 0.04, "effective_ber": 0.09,
            "beam_blocked": false, "lateral_deviation_m": 0.01, "one_way_delay_s": 2e-05 },
  "sent_bits": [...], "first_pass_bits": [...],      // as received before any retransmission (errors included)
  "reassembled_bits": [...],                         // receiver buffer after all rounds; == sent_bits when final_check.ok
  "first_pass_bit_errors": 12, "segment_bytes_used": 4, "pad_bits": 0, "n_segments": 16,
  "n_rounds": 7, "retransmissionRounds": 6, "totalRetransmissions": 12, "efficiency": 0.57,
  "rounds": [
    { "round": 0, "kind": "first_pass", "n_resent": 16, "n_became_ok": 9, "became_ok": [ { "segment": 0, "bit_start": 0, "bit_end": 32, "bits": [...] } ],
      "still_failed": 7, "segments_ok_after": 9, "bit_errors_after": 18, "all_ok_after": false, "resent": [0,1,...] },
    { "round": 1, "kind": "retransmission", "n_resent": 7, "n_became_ok": 4, "became_ok": [...], "still_failed": 3, "...": "..." } ],
                                                     // middle rounds beyond 40 drop `resent` (detail_omitted); became_ok is always complete
  "final_check": { "crc_algorithm": "CRC-32 (IEEE 802.3)", "message_crc_sent": "CBF43926", "message_crc_received": "CBF43926", "ok": true,
                   "residual_errors": { "failed_segments": 0, "bit_errors": 0, "undetected_segments": 0 },
                   "retries_exhausted": false },
  "arq": { "segments": [...], "retries_per_region": {}, "retries_histogram": {}, "total_transmissions": 28, "retransmissions": 12,
           "efficiency": 0.57, "goodput_ratio": 0.3, "spurious_retransmissions": 0, "histories_truncated": false,
           "budget_exhausted": false, "rounds_truncated": false, "data_wire_bits": 0, "feedback_wire_bits": 0,
           "max_retries": 20, "segment_bytes": 4, "residual_errors": {}, "n_segments": 16 }
}
```

With too few retries (e.g. `maxRetries: 0` on a marginal link) `final_check.ok` is `false`, `residual_errors` lists the
failed segments / wrong bits and `message_crc_received != message_crc_sent`: the failure is reported, not hidden.

---------------------------------------------------------------------------------------------------

## CRC-32 change (integrity check)

The whole project now uses **CRC-32 (IEEE 802.3 / ISO-HDLC**: poly 0x04C11DB7, reflected 0xEDB88320, init and xorout
0xFFFFFFFF, refin/refout true; identical to `zlib.crc32`, check("123456789") = 0xCBF43926) instead of CRC-16/CCITT-FALSE.
Consequences:
- Every frame CRC field is 32 bits: `/api/send-data` frame = preamble | sync | length(16) | payload | CRC(32);
  ARQ segment = `seq(2) | len(1) | data | CRC(4)` (7 bytes overhead), ACK frame = 56 bits; duplex frame overhead = 9 bytes.
- `final_check.message_crc_sent` / `message_crc_received` are 8 upper-case hex digits; `crc_algorithm` is
  `"CRC-32 (IEEE 802.3)"`. `frame.crc_received` / `frame.crc_computed` (send-data) are integers up to 2^32-1.
- Wire sizes (bit_start/bit_end, slot lengths, throughput/efficiency) grow accordingly for the same payload.
- `engine.crc16_ccitt` / `validate_crc` remain as legacy helpers only; new API: `engine.crc32`, `engine.validate_crc32`.
