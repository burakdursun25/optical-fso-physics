"""Retry limit 200 + end-to-end check/retransmission of /api/send-data."""
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def _send(grid_x=60, cell=100.0, **kw):
    body = {"simParams": {"gridX": grid_x, "cellSize": cell}, "pattern": "prbs7", "numBits": 128}
    body.update(kw)
    r = client.post("/api/send-data", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- retry limit
def test_max_retries_200_accepted_201_rejected():
    ok = client.post("/api/arq", json={"ber": 0.0, "maxRetries": 200})
    assert ok.status_code == 200
    assert client.post("/api/arq", json={"ber": 0.0, "maxRetries": 201}).status_code == 422
    assert client.post("/api/duplex", json={"berOverride": 0.0, "maxRetries": 200}).status_code == 200
    assert client.post("/api/duplex", json={"maxRetries": 201}).status_code == 422
    sd = {"simParams": {}, "maxRetries": 201}
    assert client.post("/api/send-data", json=sd).status_code == 422


def test_arq_worst_case_bounded_size_and_time():
    t0 = time.time()
    r = client.post("/api/arq", json={"payload": "x" * 2048, "segmentBytes": 8, "maxRetries": 200,
                                      "ber": 0.05, "seed": 1})
    dt = time.time() - t0
    assert r.status_code == 200
    assert len(r.content) < 2_000_000 and dt < 3.0
    b = r.json()
    assert b["histories_truncated"] is True
    assert b["budget_exhausted"] in (True, False)
    for sg in b["segments"]:
        assert len(sg["crc_history"]) <= 2 * b["history_keep_each_side"]
        if sg["history_truncated"]:
            assert sg["history_omitted"] == sg["attempts"] - len(sg["crc_history"]) > 0
        assert sg["failed_attempts"] >= sum(1 for c in sg["crc_history"] if not c)
    assert b["rounds_truncated"] is True
    assert any(rd.get("detail_omitted") for rd in b["rounds"])


def test_duplex_worst_case_bounded_and_timeline_truncated():
    t0 = time.time()
    r = client.post("/api/duplex", json={"berOverride": 0.05, "maxRetries": 200, "segmentBytes": 8,
                                         "payloadA": "a" * 512, "payloadB": "b" * 512, "seed": 3})
    dt = time.time() - t0
    assert r.status_code == 200
    assert len(r.content) < 2_000_000 and dt < 3.0
    b = r.json()
    assert b["timeline_truncated"] is True
    assert len(b["timeline"]) <= 2000
    assert b["timeline_total_slots"] > len(b["timeline"])
    assert b["timeline_omitted"] == b["timeline_total_slots"] - len(b["timeline"])


def test_small_runs_not_truncated():
    b = client.post("/api/arq", json={"ber": 0.003, "maxRetries": 10, "seed": 2}).json()
    assert b["histories_truncated"] is False and b["rounds_truncated"] is False
    d = client.post("/api/duplex", json={"berOverride": 0.0}).json()
    assert d["timeline_truncated"] is False


# ---------------------------------------------------------------- send-data e2e
def test_good_link_delivers_without_retransmissions():
    b = _send()
    assert b["final_check"]["ok"] is True
    assert b["totalRetransmissions"] == 0 and b["n_rounds"] == 1
    assert b["reassembled_bits"] == b["sent_bits"] == b["data"]
    assert b["final_check"]["message_crc_sent"] == b["final_check"]["message_crc_received"]
    assert b["bitErrorRate"] == 0.0 and b["efficiency"] == 1.0


def test_distance_used_equals_grid_distance():
    b = _send(grid_x=40, cell=150.0)
    assert b["distance_used_m"] == pytest.approx(40 * 150.0)
    assert b["grid_distance_m"] == 6000.0
    assert b["link"]["one_way_delay_s"] == pytest.approx(6000.0 / 299792458.0)


def test_marginal_link_retransmits_and_recovers():
    b = _send(grid_x=60, cell=250.0, maxRetries=200, seed=4)
    assert 0.0 < b["link"]["mean_ber"] < 0.3
    assert b["totalRetransmissions"] > 0 and b["n_rounds"] > 1
    assert b["first_pass_bit_errors"] > 0 and b["bitErrorRate"] > 0      # first pass shows the damage
    assert b["final_check"]["ok"] is True
    assert b["final_check"]["residual_errors"]["bit_errors"] == 0
    assert b["reassembled_bits"] == b["sent_bits"]
    assert b["first_pass_bits"] != b["sent_bits"]
    # rounds: round 0 is the full first pass, later rounds only resend failed segments
    r0 = b["rounds"][0]
    assert r0["kind"] == "first_pass" and r0["n_resent"] == b["n_segments"]
    assert b["rounds"][-1]["all_ok_after"] is True
    reinserted = {x["segment"] for rd in b["rounds"] for x in rd["became_ok"]}
    assert reinserted == set(range(b["n_segments"]))
    # re-inserted bits sit at the right position of the sent message
    for rd in b["rounds"]:
        for x in rd["became_ok"]:
            assert b["sent_bits"][x["bit_start"]:x["bit_end"]] == x["bits"]
    assert set(b["arq"]) >= {"segments", "retries_per_region", "total_transmissions", "efficiency"}
    assert b["arq"]["efficiency"] < 1.0
    assert b["totalRetransmissions"] == b["arq"]["total_transmissions"] - b["n_segments"]


def test_zero_retries_on_bad_link_reports_residual_errors():
    b = _send(grid_x=60, cell=300.0, maxRetries=0, seed=4)
    fc = b["final_check"]
    assert fc["ok"] is False and fc["retries_exhausted"] is True
    assert fc["residual_errors"]["failed_segments"] > 0 and fc["residual_errors"]["bit_errors"] > 0
    assert fc["message_crc_sent"] != fc["message_crc_received"]
    assert b["totalRetransmissions"] == 0 and b["n_rounds"] == 1
    assert b["reassembled_bits"] != b["sent_bits"]


def test_non_byte_aligned_bits_and_segment_autobump():
    b = _send(numBits=10000, segmentBytes=1)
    assert b["n_segments"] <= 256 and b["segment_bytes_used"] >= 5
    assert b["reassembled_bits"] == b["sent_bits"] and len(b["reassembled_bits"]) == 10000
    c = _send(numBits=13, pattern="alternating")
    assert c["pad_bits"] == 3 and len(c["reassembled_bits"]) == 13 and c["final_check"]["ok"]


def test_use_arq_false_keeps_legacy_behaviour():
    b = _send(useArq=False)
    assert b["arq"] is None and "final_check" not in b
    assert {"data", "results", "totalBits", "correctBits", "bitErrorRate", "successRate"} <= set(b)


def test_deterministic_for_seed():
    a = _send(cell=300.0, seed=9, pattern="alternating")
    b = _send(cell=300.0, seed=9, pattern="alternating")
    assert a["first_pass_bits"] == b["first_pass_bits"] and a["n_rounds"] == b["n_rounds"]


def test_send_data_final_check_uses_32bit_crc():
    import re
    b = _send()
    fc = b["final_check"]
    assert fc["crc_algorithm"].startswith("CRC-32")
    assert re.fullmatch(r"[0-9A-F]{8}", fc["message_crc_sent"])
    assert fc["message_crc_sent"] == fc["message_crc_received"] and fc["ok"]
