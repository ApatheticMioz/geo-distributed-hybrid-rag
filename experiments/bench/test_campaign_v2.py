"""Tests for the Campaign v2 confirmatory + 3-node driver
(experiments/bench/campaign.py).

Covers the PURE functions added for the v2 confirmatory path:
  * verify_pool_manifest        (pool file <-> manifest sha256/n/line_count)
  * v2_line_range / select_pool_slices / slices_disjoint
  * parse_fidelity_gate_output / stratum_for_repeat
  * v2_record_extra / build_record (v2 record-field additions, v1 superset)
  * arm_label / is_v2 / resolve_mode_defaults / build_campaign_manifest
plus a byte-determinism check that the v1 code path is unchanged (the v1
dry-run plan is identical to the pre-v2 implementation's output).

And the WS-CM 3-node run (design §2.2/§3.2/§4.3/§5.1):
  * v2_line_range("three-node") -> lines 1371..1910
  * build_3node_arm_list / three_node_arm_label / select_pool_slices_3node
  * v3node_record_extra / build_3node_manifest / run_fidelity_gate_3node
  * resolve_mode_defaults for the three-node run (Node C gateway, N=80)
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign  # noqa: E402

QUERIES_DIR = Path(__file__).resolve().parent / "queries"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _write_pool(tmp_path: Path, n: int, tag: str = "q") -> Path:
    p = tmp_path / f"pool_{tag}.txt"
    p.write_text("\n".join(f"{tag}-{i}" for i in range(n)) + "\n",
                 encoding="utf-8")
    return p


def _write_manifest(pool_path: Path, n: int, sha: str) -> Path:
    man = {
        "schema": "pdc.query_pool_manifest/v1",
        "seed": 42,
        "n": n,
        "output": {
            "path": str(pool_path),
            "sha256": sha,
            "line_count": n,
        },
    }
    mp = pool_path.with_name(pool_path.name + ".manifest.json")
    mp.write_text(json.dumps(man, indent=2), encoding="utf-8")
    return mp


# ---------------------------------------------------------------------------
# verify_pool_manifest
# ---------------------------------------------------------------------------
def test_verify_pool_manifest_ok(tmp_path):
    pool = _write_pool(tmp_path, 7)
    _write_manifest(pool, 7, campaign.sha256_file(str(pool)))
    man = campaign.verify_pool_manifest(str(pool))
    assert man["n"] == 7
    assert man["_verified_sha256"] == campaign.sha256_file(str(pool))


def test_verify_pool_manifest_sha_mismatch(tmp_path):
    pool = _write_pool(tmp_path, 5)
    _write_manifest(pool, 5, "0" * 64)
    with pytest.raises(campaign.ManifestError, match="sha256 mismatch"):
        campaign.verify_pool_manifest(str(pool))


def test_verify_pool_manifest_n_mismatch(tmp_path):
    pool = _write_pool(tmp_path, 5)
    # n (6) disagrees with line_count (5): internal inconsistency. The sha
    # is correct so the sha256 check passes and the n/line_count check fires.
    man = {
        "schema": "pdc.query_pool_manifest/v1",
        "seed": 42,
        "n": 6,
        "output": {
            "path": str(pool),
            "sha256": campaign.sha256_file(str(pool)),
            "line_count": 5,
        },
    }
    pool.with_name(pool.name + ".manifest.json").write_text(
        json.dumps(man), encoding="utf-8")
    with pytest.raises(campaign.ManifestError, match="internally inconsistent"):
        campaign.verify_pool_manifest(str(pool))


def test_verify_pool_manifest_actual_linecount_mismatch(tmp_path):
    pool = _write_pool(tmp_path, 5)
    man = _write_manifest(pool, 5, campaign.sha256_file(str(pool)))
    # file is later truncated to 4 lines; the manifest (n=5, sha of the
    # 5-line file) no longer matches the file's real content.
    pool.write_text("\n".join(f"q-{i}" for i in range(4)) + "\n",
                    encoding="utf-8")
    with pytest.raises(campaign.ManifestError, match="sha256 mismatch"):
        campaign.verify_pool_manifest(str(pool))


def test_verify_pool_manifest_linecount_mismatch(tmp_path):
    pool = _write_pool(tmp_path, 5)
    man = _write_manifest(pool, 5, campaign.sha256_file(str(pool)))
    # manifest says 5, file actually has 4 (sha still matches the 5-line file?
    # no — rewrite the file to 4 lines, sha stays from the 5-line file)
    pool.write_text("\n".join(f"q-{i}" for i in range(4)) + "\n",
                    encoding="utf-8")
    man.write_text(json.dumps({
        "schema": "pdc.query_pool_manifest/v1", "n": 5,
        "output": {"path": str(pool), "sha256": "0" * 64, "line_count": 5},
    }), encoding="utf-8")
    with pytest.raises(campaign.ManifestError):
        campaign.verify_pool_manifest(str(pool))


def test_verify_pool_manifest_missing_sidecar(tmp_path):
    pool = _write_pool(tmp_path, 3)
    with pytest.raises(campaign.ManifestError, match="sidecar not found"):
        campaign.verify_pool_manifest(str(pool))


def test_verify_pool_manifest_bad_schema(tmp_path):
    pool = _write_pool(tmp_path, 3)
    _write_manifest(pool, 3, campaign.sha256_file(str(pool)))
    mp = pool.with_name(pool.name + ".manifest.json")
    man = json.loads(mp.read_text())
    man["schema"] = "something.else/v0"
    mp.write_text(json.dumps(man))
    with pytest.raises(campaign.ManifestError, match="schema mismatch"):
        campaign.verify_pool_manifest(str(pool))


# ---------------------------------------------------------------------------
# v2 line-range slice selection
# ---------------------------------------------------------------------------
def _arms2():
    return [(0, 0, "hybrid", "baseline"), (0, 0, "hybrid", "sphp"),
            (15, 0, "hybrid", "baseline"), (15, 0, "hybrid", "sphp")]


def test_v2_line_range_confirmatory():
    # design §5.1: confirmatory = first 1,040 lines (8 arms x 130)
    s, e = campaign.v2_line_range("confirmatory", 8, 120, 10)
    assert (s, e) == (1, 1040)


def test_v2_line_range_unknown_run_type():
    with pytest.raises(ValueError, match="WS-CM"):
        campaign.v2_line_range("three_node", 6, 80, 10)


def test_select_pool_slices_disjoint_and_ordered():
    pool = [f"q{i}" for i in range(40)]
    plan = campaign.select_pool_slices(pool, _arms2(), n=2, warmups=1,
                                       line_start=1, line_end=12)
    assert len(plan) == 4
    for i, a in enumerate(plan):
        assert a["pool_start"] == i * 3
        assert a["pool_end"] == i * 3 + 3
        assert len(a["warmups"]) == 1
        assert len(a["measured"]) == 2
        # slice contents are the fixed pool lines (no re-shuffle)
        assert a["warmups"] == [f"q{3 * i}"]
        assert a["measured"] == [f"q{3 * i + 1}", f"q{3 * i + 2}"]
    assert campaign.slices_disjoint(plan)


def test_select_pool_slices_out_of_bounds():
    pool = [f"q{i}" for i in range(10)]
    with pytest.raises(ValueError, match="out of bounds"):
        campaign.select_pool_slices(pool, _arms2(), 2, 1, 1, 12)


def test_select_pool_slices_range_arm_mismatch():
    pool = [f"q{i}" for i in range(20)]
    with pytest.raises(ValueError, match="recompute the range"):
        campaign.select_pool_slices(pool, _arms2(), 2, 1, 1, 11)


def test_select_pool_slices_duplicate_lines_rejected():
    # 4 arms x (N=1 + W=1) = 8 lines, with a duplicate inside the range
    pool = ["q0", "q1", "q1", "q3", "q4", "q5", "q6", "q7"]
    with pytest.raises(ValueError, match="distinct"):
        campaign.select_pool_slices(pool, _arms2(), 1, 1, 1, 8)


def test_slices_disjoint_negative():
    a = {"warmups": ["x"], "measured": ["y"]}
    b = {"warmups": ["z"], "measured": ["y"]}
    assert not campaign.slices_disjoint([a, b])
    assert campaign.slices_disjoint([a, {"warmups": ["z"], "measured": ["w"]}])


# ---------------------------------------------------------------------------
# v1 build_arm_query_plan unchanged (seeded shuffle, disjoint)
# ---------------------------------------------------------------------------
def test_v1_plan_reproducible_and_disjoint():
    pool = [f"q{i}" for i in range(20)]
    plan1 = campaign.build_arm_query_plan(pool, _arms2(), 3, 2, 42)
    plan2 = campaign.build_arm_query_plan(pool, _arms2(), 3, 2, 42)
    assert plan1 == plan2
    assert campaign.slices_disjoint(plan1)
    # a different seed gives a different order (sanity)
    plan3 = campaign.build_arm_query_plan(pool, _arms2(), 3, 2, 7)
    assert plan3 != plan1


# ---------------------------------------------------------------------------
# fidelity-gate output parsing
# ---------------------------------------------------------------------------
def test_parse_fidelity_gate_output():
    out = (
        "Baseline median RTT : 1.450 ms\n"
        "Shaped median RTT   : 16.700 ms\n"
        "Added RTT (Observed): 15.250 ms (Acceptable Gate [10.500, 19.500] ms)\n"
        "Status: PASS\n"
    )
    p = campaign.parse_fidelity_gate_output(out)
    assert p["baseline_median_ms"] == 1.45
    assert p["shaped_median_ms"] == 16.7
    assert p["added_rtt_ms"] == 15.25


def test_parse_fidelity_gate_output_empty():
    p = campaign.parse_fidelity_gate_output("")
    assert p == {"baseline_median_ms": None, "shaped_median_ms": None,
                 "added_rtt_ms": None}


def test_stratum_for_repeat():
    assert campaign.stratum_for_repeat(0) == "cold"
    assert campaign.stratum_for_repeat(1) == "warm"
    assert campaign.stratum_for_repeat(2) == "warm"


def test_run_fidelity_gate_dry_run():
    g = campaign.run_fidelity_gate(15, "pre", dry_run=True)
    assert g == {"rtt_ms": 15, "when": "pre", "status": "planned"}
    g0 = campaign.run_fidelity_gate(0, "pre", dry_run=True)
    assert g0["status"] == "skipped-baseline"
    g0r = campaign.run_fidelity_gate(0, "post", dry_run=False)
    assert g0r["status"] == "skipped-baseline"
    assert g0r["shaped_median_ms"] is None


# ---------------------------------------------------------------------------
# record building: v1 superset + v2 additions
# ---------------------------------------------------------------------------
def test_build_record_v1_fields_unchanged():
    res = {
        "query_id": "100",
        "top_k": 10,
        "timings": {f: 1.0 for f in campaign.TIMING_FIELDS},
        "token_count": 12,
        "decode_tps": 30.0,
    }
    rec = campaign.build_record("rid", 15, 0, "hybrid", "sphp", 0, "what?",
                                res, netem_applied=True)
    for f in ("run_id", "timestamp", "query_id", "query", "rtt_ms", "loss_pct",
              "mode", "variant", "repeat_index", "top_k", "netem_applied") \
            + campaign.TIMING_FIELDS + campaign.RESPONSE_FIELDS:
        assert f in rec, f"missing v1 field {f}"
    assert rec["rtt_ms"] == 15
    assert rec["netem_applied"] is True


def test_build_record_v2_extra():
    man = {"_verified_sha256": "ab" * 32}
    gate = {"shaped_median_ms": 15.2}
    rec = campaign.build_record("rid", 15, 0, "hybrid", "sphp", 1, "q",
                                {"query_id": "1", "top_k": 10, "timings": {}},
                                netem_applied=True,
                                extra=campaign.v2_record_extra(
                                    "hybrid", 1, 15, 0, gate, "pool.txt", man))
    assert rec["stratum"] == "warm"
    assert rec["rtt_ping_median_ms"] == 15.2
    assert rec["netem_link_b_applied"] == {"rtt_ms": 15, "loss_pct": 0}
    assert rec["netem_link_c_applied"] is None  # WS-CM extension point
    assert rec["pool_file"] == "pool.txt"
    assert rec["pool_manifest_sha256"] == "ab" * 32


def test_v2_record_extra_baseline_tier():
    extra = campaign.v2_record_extra("hybrid", 0, 0, 0,
                                     {"shaped_median_ms": None}, "p.txt",
                                     {"_verified_sha256": None})
    assert extra["stratum"] == "cold"
    assert extra["netem_link_b_applied"] is None
    assert extra["rtt_ping_median_ms"] is None


# ---------------------------------------------------------------------------
# arm labels & run spec
# ---------------------------------------------------------------------------
def test_arm_label():
    assert campaign.arm_label("hybrid", "baseline") == "P2"
    assert campaign.arm_label("hybrid", "sphp") == "P2-SPHP"
    assert campaign.arm_label("dense", "baseline") == "dense"


# ---------------------------------------------------------------------------
# CLI: v2 mode activation & defaults
# ---------------------------------------------------------------------------
def test_is_v2_flag():
    ap = campaign.build_parser()
    args = ap.parse_args(["--dry-run", "--pool", "somefile.txt"])
    assert campaign.is_v2(args)
    args = ap.parse_args(["--dry-run"])
    assert not campaign.is_v2(args)


def test_resolve_mode_defaults_v2():
    ap = campaign.build_parser()
    args = ap.parse_args(["--pool", "x.txt", "--dry-run"])
    campaign.resolve_mode_defaults(args)
    assert args.modes == ["hybrid"]
    assert args.sphp_axis is True
    assert args.loss_tiers == [0]
    assert args.query_sample == 120
    assert args.warmups == 10


def test_resolve_mode_defaults_v2_explicit_wins():
    ap = campaign.build_parser()
    args = ap.parse_args(["--pool", "x.txt", "--query-sample", "50",
                          "--warmups", "5", "--dry-run"])
    campaign.resolve_mode_defaults(args)
    assert args.query_sample == 50
    assert args.warmups == 5
    assert args.modes == ["hybrid"]


def test_resolve_mode_defaults_v1():
    ap = campaign.build_parser()
    args = ap.parse_args(["--dry-run"])
    campaign.resolve_mode_defaults(args)
    assert args.modes == ["hybrid", "dense", "sparse"]
    assert args.sphp_axis is False
    assert args.loss_tiers == [0, 1]
    assert args.query_sample == 50
    assert args.warmups == 5


# ---------------------------------------------------------------------------
# campaign manifest builder
# ---------------------------------------------------------------------------
def test_build_campaign_manifest(tmp_path):
    pool = _write_pool(tmp_path, 40)
    man = {"_verified_sha256": "aa" * 32, "n": 40}
    plan = campaign.select_pool_slices(
        [f"q{i}" for i in range(40)], _arms2(), 2, 1, 1, 12)
    doc = campaign.build_campaign_manifest(
        "rid", "confirmatory", str(pool), man, 1, 12, plan, 2, 1, 3, 42)
    assert doc["schema"] == "pdc.campaign_manifest/v2"
    assert doc["design_commit"] == "1f911ef"
    assert doc["disjointness"]["asserted"] is True
    assert doc["pool"]["line_start"] == 1
    assert doc["pool"]["line_end"] == 12
    # 1-based line ranges in the arms
    assert doc["arms"][0]["pool_start"] == 1
    assert doc["arms"][3]["pool_end"] == 12


def _v2_args():
    ap = campaign.build_parser()
    args = ap.parse_args([
        "--pool", str(QUERIES_DIR / "campaign_v2_master5000_seed42.txt"),
        "--dry-run",
    ])
    campaign.resolve_mode_defaults(args)
    return args

# ---------------------------------------------------------------------------
# end-to-end with the REAL repo pools (manifest verification against
# experiments/bench/queries/*.manifest.json, as the startup gate does)
# ---------------------------------------------------------------------------
def test_real_pool_manifests_verify():
    for name in ("campaign_v2_core400_seed42.txt",
                 "campaign_v2_master5000_seed42.txt"):
        p = QUERIES_DIR / name
        man = campaign.verify_pool_manifest(str(p))
        assert man["n"] == len(campaign.load_queries(str(p)))


def test_real_v2_slice_selection_master_pool():
    # The confirmatory core consumes lines 1..1040 of the master pool
    # (8 arms x (120 + 10)); the file has 5,000 lines, so it fits.
    pool = campaign.load_queries(str(QUERIES_DIR / "campaign_v2_master5000_seed42.txt"))
    arms = campaign.build_arm_list(_v2_args())
    assert len(arms) == 8
    s, e = campaign.v2_line_range("confirmatory", len(arms), 120, 10)
    assert (s, e) == (1, 1040)
    plan = campaign.select_pool_slices(pool, arms, 120, 10, s, e)
    assert campaign.slices_disjoint(plan)
    assert len({q for a in plan for q in a["measured"]}) == 8 * 120


# ---------------------------------------------------------------------------
# v2: resumable runs (--resume)
# ---------------------------------------------------------------------------
def _rec(run_id, rtt, loss, mode, variant, query, rep, **kw):
    """Build a minimal v2-style JSONL record for the resume tests."""
    r = {
        "run_id": run_id,
        "timestamp": "2026-01-01T00:00:00Z",
        "query_id": kw.get("query_id", None),
        "query": query,
        "rtt_ms": rtt,
        "loss_pct": loss,
        "mode": mode,
        "variant": variant,
        "repeat_index": rep,
        "top_k": 10,
        "netem_applied": (rtt != 0 or loss != 0),
    }
    if "error" in kw:
        r["error"] = kw["error"]
    return r


def test_find_resumable_jsonl_explicit_run_id(tmp_path):
    path, ambig = campaign.find_resumable_jsonl(tmp_path, "20260101T000000Z")
    assert ambig is None
    assert path == tmp_path / "campaign_20260101T000000Z.jsonl"


def test_find_resumable_jsonl_none(tmp_path):
    path, ambig = campaign.find_resumable_jsonl(tmp_path, None)
    assert path is None and ambig is None


def test_find_resumable_jsonl_single(tmp_path):
    (tmp_path / "campaign_A.jsonl").write_text("{}\n")
    (tmp_path / "summary_A.csv").write_text("x\n")  # ignored
    path, ambig = campaign.find_resumable_jsonl(tmp_path, None)
    assert ambig is None
    assert path == tmp_path / "campaign_A.jsonl"


def test_find_resumable_jsonl_ambiguous(tmp_path):
    (tmp_path / "campaign_A.jsonl").write_text("{}\n")
    (tmp_path / "campaign_B.jsonl").write_text("{}\n")
    path, ambig = campaign.find_resumable_jsonl(tmp_path, None)
    assert path is None and ambig == "ambiguous"


def test_load_jsonl_records_clean(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text("{\"a\":1}\n{\"a\":2}\n", encoding="utf-8")
    recs, trunc = campaign.load_jsonl_records(p)
    assert recs == [{"a": 1}, {"a": 2}]
    assert trunc == 0


def test_load_jsonl_records_missing(tmp_path):
    recs, trunc = campaign.load_jsonl_records(tmp_path / "nope.jsonl")
    assert recs == [] and trunc == 0


def test_load_jsonl_records_truncated_last_line(tmp_path):
    # Three complete records then a truncated/corrupt final line (crash
    # mid-write) with no trailing newline.
    p = tmp_path / "c.jsonl"
    body = '{\"q\":\"a\"}\n{\"q\":\"b\"}\n{\"q\":\"c\"}\n'
    p.write_text(body + '{\"q\":\"d", \'trunc\'=', encoding="utf-8")
    recs, trunc = campaign.load_jsonl_records(p)
    assert [r["q"] for r in recs] == ["a", "b", "c"]
    assert trunc == 1  # the broken final line is tolerated + warned


def test_load_jsonl_records_blank_lines_ignored(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text("\n{\"q\":\"a\"}\n\n\n", encoding="utf-8")
    recs, trunc = campaign.load_jsonl_records(p)
    assert [r["q"] for r in recs] == ["a"] and trunc == 0


def test_extract_completed_triples():
    recs = [
        _rec("rid", 0, 0, "hybrid", "baseline", "q1", 0),
        _rec("rid", 0, 0, "hybrid", "baseline", "q1", 1),
        # an error record: query_id is None but the query TEXT is present,
        # so it still forms a distinct completed triple (would not if keyed
        # on query_id).
        _rec("rid", 15, 0, "hybrid", "sphp", "q2", 0, error="boom"),
    ]
    trip = campaign.extract_completed_triples(recs)
    assert ("0ms-0%-hybrid-baseline", "q1", 0) in trip
    assert ("0ms-0%-hybrid-baseline", "q1", 1) in trip
    assert ("15ms-0%-hybrid-sphp", "q2", 0) in trip
    assert len(trip) == 3
    # identity matches what the runner builds for a (rtt, loss, mode, variant,
    # query, repeat) — cross-check the arm-key rendering.
    ident = campaign.record_identity(
        _rec("rid", 0, 0, "hybrid", "baseline", "q1", 0))
    assert ident == ("0ms-0%-hybrid-baseline", "q1", 0)


def test_resume_flag_in_parser():
    ap = campaign.build_parser()
    args = ap.parse_args(["--pool", "x.txt", "--resume", "--dry-run"])
    assert args.resume is True
    args2 = ap.parse_args(["--pool", "x.txt", "--dry-run"])
    assert args2.resume is False
    args3 = ap.parse_args(["--pool", "x.txt", "--run-id", "20260101T000000Z",
                           "--dry-run"])
    assert args3.run_id == "20260101T000000Z"


# ---------------------------------------------------------------------------
# v2: --resume driver-level integration (network/netem/health mocked)
# ---------------------------------------------------------------------------
def _v2_env(tmp_path):
    """Build a tiny v2 (8 arms) plan + pool and return (args, all_queries,
    manifest, arm_plan, line_start, line_end, pool_path)."""
    pool = tmp_path / "pool.txt"
    n = 24  # 8 arms x (N=2 + W=1)
    pool.write_text("\n".join(f"q{i}" for i in range(n)) + "\n",
                    encoding="utf-8")
    man = campaign.verify_pool_manifest.__wrapped__ if False else None
    manifest = {"_verified_sha256": campaign.sha256_file(str(pool)), "n": n}

    ap = campaign.build_parser()
    args = ap.parse_args([
        "--pool", str(pool),
        "--query-sample", "2",
        "--warmups", "1",
        "--repeats", "2",
        "--gateway-url", "http://gw.test",
        "--out-dir", str(tmp_path / "out"),
    ])
    campaign.resolve_mode_defaults(args)

    arms = campaign.build_arm_list(args)
    assert len(arms) == 8
    all_queries = campaign.load_queries(str(pool))
    line_start, line_end = campaign.v2_line_range(
        "confirmatory", len(arms), args.query_sample, args.warmups)
    arm_plan = campaign.select_pool_slices(
        all_queries, arms, args.query_sample, args.warmups,
        line_start, line_end)
    return args, all_queries, manifest, arm_plan, line_start, line_end


def _patch_net(monkeypatch, posted):
    """Patch all external side effects; record measured/warmup POSTs."""
    def fake_health(url, timeout=15):
        return True
    def fake_gate(rtt, when, dry_run):
        return {"rtt_ms": rtt, "when": when,
                "status": "skipped-baseline" if rtt == 0 else "PASS",
                "baseline_median_ms": None, "shaped_median_ms": 5.0,
                "added_rtt_ms": 5.0, "output_tail": ""}
    def fake_apply(rtt, loss, dry_run):
        return False
    def fake_clear(dry_run):
        return None
    def fake_http(url, payload, timeout):
        posted.append(dict(payload))
        return {"query_id": "1", "top_k": payload.get("top_k"),
                "timings": {"ttft_ms": 1.0, "total_ms": 2.0,
                             "sparse_ms": 0, "dense_ms": 0, "fusion_ms": 0,
                             "decode_ms": 0, "simulated_wan_ms": 0},
                "token_count": 3, "decode_tps": 4.0,
                "sphp_hit": True, "sphp_overlap": 0.5}
    monkeypatch.setattr(campaign, "check_health", fake_health)
    monkeypatch.setattr(campaign, "run_fidelity_gate", fake_gate)
    monkeypatch.setattr(campaign, "apply_netem_link_b", fake_apply)
    monkeypatch.setattr(campaign, "clear_netem", fake_clear)
    monkeypatch.setattr(campaign, "http_post_json", fake_http)


def test_v2_fresh_run_writes_all_and_summary(tmp_path, monkeypatch):
    args, all_q, man, plan, ls, le = _v2_env(tmp_path)
    args.resume = False
    args.run_id = "FRESH"
    posted = []
    _patch_net(monkeypatch, posted)
    with pytest.raises(SystemExit) as ei:
        campaign.run_campaign_v2(args, all_q, man, plan, ls, le)
    assert ei.value.code == 0
    # 8 arms x (W=1 + N=2 x R=2) = 8 warmups + 32 measured = 40 POSTs
    assert len(posted) == 40
    jsonl = tmp_path / "out" / "campaign_FRESH.jsonl"
    recs, trunc = campaign.load_jsonl_records(jsonl)
    assert trunc == 0
    assert len(recs) == 32  # measured only (warmups never recorded)
    summary = json.loads((tmp_path / "out" / "summary_FRESH.json").read_text())
    assert summary["status"] == "completed"
    assert summary["n_ok"] == 32 and summary["n_errors"] == 0
    assert summary["n_warmup_ok"] == 8
    assert len(summary["summary"]) == 8  # one row per arm


def test_v2_resume_skips_exactly_completed(tmp_path, monkeypatch):
    args, all_q, man, plan, ls, le = _v2_env(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "campaign_RES.jsonl"

    # Pre-populate the FIRST arm's (rtt=0,loss=0,hybrid,baseline) measured
    # records: it consumes pool lines 1..3 -> warmup q0, measured q1,q2.
    # With R=2 that's (q1,0),(q1,1),(q2,0),(q2,1) -> all 4 completed.
    pre = []
    for q in ("q1", "q2"):
        for rep in range(2):
            pre.append(_rec("RES", 0, 0, "hybrid", "baseline", q, rep))
    jsonl.write_text("".join(json.dumps(r) + "\n" for r in pre),
                     encoding="utf-8")

    args.resume = True
    args.run_id = "RES"
    posted = []
    _patch_net(monkeypatch, posted)
    with pytest.raises(SystemExit) as ei:
        campaign.run_campaign_v2(args, all_q, man, plan, ls, le)
    assert ei.value.code == 0

    posted_queries = [p["query"] for p in posted]
    # The two pre-measured queries of the first arm were skipped: they must
    # NOT have been re-POSTed (they only ever appear as that arm's measured
    # queries, never as warmups of another arm).
    assert "q1" not in posted_queries
    assert "q2" not in posted_queries
    # Warmups are always re-run: the first arm's warmup q0 IS posted again.
    assert "q0" in posted_queries
    # 4 measured skipped of the 32 total measured; all 8 warmups re-run:
    # 8 warmups + (32 - 4) measured = 36 POSTs.
    assert len(posted) == 36
    # Final JSONL = 4 pre-existing + 28 new measured = 32 measured records.
    recs, trunc = campaign.load_jsonl_records(jsonl)
    assert trunc == 0
    assert len(recs) == 32
    # Summary reflects the FULL data (seeded + new), identical to a one-shot
    # run: n_ok = 32 measured, 8 warmups.
    summary = json.loads((out_dir / "summary_RES.json").read_text())
    assert summary["status"] == "completed"
    assert summary["n_ok"] == 32 and summary["n_warmup_ok"] == 8
    assert len(summary["summary"]) == 8


def test_v2_resume_with_truncated_last_line(tmp_path, monkeypatch):
    args, all_q, man, plan, ls, le = _v2_env(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "campaign_TR.jsonl"
    # One good completed record + a truncated final line (crash mid-write).
    good = _rec("TR", 0, 0, "hybrid", "baseline", "q1", 0)
    # A real q2 rep-0 record whose final bytes are cut off mid-write (no
    # closing brace) -> the loader skips the line but its "query" field
    # (present before the cut) still marks the (arm, q2, 0) triple complete.
    q2_rec = json.dumps(_rec("TR", 0, 0, "hybrid", "baseline", "q2", 0))
    truncated = q2_rec[:len(q2_rec) - 5]
    jsonl.write_text(json.dumps(good) + "\n" + truncated, encoding="utf-8")

    args.resume = True
    args.run_id = "TR"
    posted = []
    _patch_net(monkeypatch, posted)
    with pytest.raises(SystemExit) as ei:
        campaign.run_campaign_v2(args, all_q, man, plan, ls, le)
    assert ei.value.code == 0  # ran to completion despite the corrupt line
    # First arm: warmup q0, measured q1,q2 (R=2).  The good record made
    # (q1, rep0) completed -> skipped; q1 rep1 was never completed ->
    # re-measured, so q1 is POSTed EXACTLY ONCE.  The TRUNCATED q2 line was
    # dropped, so neither (q2, rep0) nor (q2, rep1) is in the completed set
    # -> both re-measured, so q2 is POSTed TWICE.  Warmup q0 is always re-run.
    posted_counts = {q: sum(1 for p in posted if p.get("query") == q)
                     for q in ("q0", "q1", "q2")}
    assert posted_counts["q0"] == 1  # warmup re-run
    assert posted_counts["q1"] == 1  # rep0 skipped, rep1 re-measured
    assert posted_counts["q2"] == 2  # both reps re-measured (line was corrupt)
    recs, trunc = campaign.load_jsonl_records(jsonl)
    assert trunc == 1  # the broken line is still flagged as truncated
    # final file holds the good q1 rep0 (from before) plus all re-measured.
    assert any(r["query"] == "q1" and r["repeat_index"] == 0 for r in recs)
    assert any(r["query"] == "q2" and r["repeat_index"] == 0 for r in recs)
    assert any(r["query"] == "q2" and r["repeat_index"] == 1 for r in recs)


def test_v2_no_resume_existing_file_aborts(tmp_path, monkeypatch):
    args, all_q, man, plan, ls, le = _v2_env(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "campaign_DONE.jsonl").write_text(
        json.dumps(_rec("DONE", 0, 0, "hybrid", "baseline", "q1", 0)) + "\n",
        encoding="utf-8")
    posted = []
    _patch_net(monkeypatch, posted)
    # NO --resume, but pinned to the existing run id -> must abort.
    args.resume = False
    args.run_id = "DONE"
    with pytest.raises(SystemExit) as ei:
        campaign.run_campaign_v2(args, all_q, man, plan, ls, le)
    assert ei.value.code == 1
    # No HTTP was performed (aborted before any request).
    assert posted == []



# ---------------------------------------------------------------------------
# v2: three-node run (WS-CM matrix, design §2.2/§3.2/§4.3/§5.1)
# ---------------------------------------------------------------------------
def test_3node_line_range():
    # design §5.1: WS-CM 3-node = 6 arms x (N=80 + W=10) = 540 lines,
    # starting at line 1371 -> 1371..1910.
    s, e = campaign.v2_line_range("three-node", 6, 80, 10)
    assert (s, e) == (1371, 1910)


def test_3node_arm_expansion():
    spec = campaign.V2_RUN_SPECS["three-node"]
    arms = campaign.build_3node_arm_list(spec)
    # 3 (B,C) cells x 2 placement arms = 6 arms, cell-major then variant.
    assert len(arms) == 6
    assert arms == [
        (15, 15, "baseline"), (15, 15, "sphp"),
        (40, 15, "baseline"), (40, 15, "sphp"),
        (80, 15, "baseline"), (80, 15, "sphp"),
    ]
    # Both legs shaped in every cell (all cells non-baseline); C is the
    # user-facing sparse leg held at 15 ms (design §3.2).
    assert all(rtt_b > 0 and rtt_c > 0 for rtt_b, rtt_c, _ in arms)
    assert all(rtt_c == 15 for _, rtt_c, _ in arms)


def test_3node_arm_label():
    assert campaign.three_node_arm_label("baseline") == "3node-P2"
    assert campaign.three_node_arm_label("sphp") == "3node-SPHP-from-C"


def test_3node_select_pool_slices_disjoint_and_ordered():
    spec = campaign.V2_RUN_SPECS["three-node"]
    # 6 arms x (80 + 10) = 540 lines -> lines 1371..1910 of a big pool.
    pool = [f"q{i}" for i in range(2000)]
    plan = campaign.select_pool_slices_3node(
        pool, spec["cells"], spec["variants"], 80, 10, 1371, 1910)
    assert len(plan) == 6
    # arm i consumes pool lines [1371 + i*90 .. 1371 + (i+1)*90 - 1] (1-based).
    for i, a in enumerate(plan):
        assert a["pool_start"] == (1371 - 1) + i * 90
        assert a["pool_end"] == a["pool_start"] + 90
        assert len(a["warmups"]) == 10
        assert len(a["measured"]) == 80
        # slice contents are the fixed pool lines (no re-shuffle)
        assert a["warmups"] == [f"q{1370 + i * 90 + k}" for k in range(10)]
    # disjointness (warmups + measured)
    sets = [set(a["warmups"]) | set(a["measured"]) for a in plan]
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            assert not (sets[i] & sets[j])
    # measured queries total 6 x 80 distinct
    assert len({q for a in plan for q in a["measured"]}) == 480


def test_3node_select_pool_slices_out_of_bounds():
    spec = campaign.V2_RUN_SPECS["three-node"]
    # pool too short to hold line range 1371..1910
    pool = [f"q{i}" for i in range(1400)]
    with pytest.raises(ValueError, match="out of bounds"):
        campaign.select_pool_slices_3node(
            pool, spec["cells"], spec["variants"], 80, 10, 1371, 1910)


def test_3node_select_pool_slices_range_arm_mismatch():
    spec = campaign.V2_RUN_SPECS["three-node"]
    pool = [f"q{i}" for i in range(2000)]
    # wrong end: 1371..1900 holds 530 lines, not 6 x 90 = 540
    with pytest.raises(ValueError, match="recompute the range"):
        campaign.select_pool_slices_3node(
            pool, spec["cells"], spec["variants"], 80, 10, 1371, 1900)


def test_3node_record_extra():
    man = {"_verified_sha256": "ab" * 32}
    pre_gates = [
        {"link": "b", "rtt_ms": 15, "shaped_median_ms": 16.2},
        {"link": "c", "rtt_ms": 15, "shaped_median_ms": 15.9},
    ]
    extra = campaign.v3node_record_extra(
        0, 15, 0, 15, 0, pre_gates, "pool.txt", man)
    assert extra["stratum"] == "cold"
    # rtt_ping_median_ms = the LINK-B (dense/generation leg) shaped median
    assert extra["rtt_ping_median_ms"] == 16.2
    # per-leg targets are both populated for the 3-node run
    assert extra["netem_link_b_applied"] == {"rtt_ms": 15, "loss_pct": 0}
    assert extra["netem_link_c_applied"] == {"rtt_ms": 15, "loss_pct": 0}
    assert extra["pool_file"] == "pool.txt"
    assert extra["pool_manifest_sha256"] == "ab" * 32


def test_3node_record_extra_warm_stratum():
    extra = campaign.v3node_record_extra(
        1, 40, 0, 15, 0,
        [{"link": "b", "rtt_ms": 40, "shaped_median_ms": 41.0},
         {"link": "c", "rtt_ms": 15, "shaped_median_ms": 15.0}],
        "p.txt", {"_verified_sha256": "cc" * 32})
    assert extra["stratum"] == "warm"
    assert extra["rtt_ping_median_ms"] == 41.0


def test_3node_run_fidelity_gate_dry_run():
    gates = campaign.run_fidelity_gate_3node(
        15, 0, 15, 0, "pre", dry_run=True)
    assert len(gates) == 2
    assert [g["link"] for g in gates] == ["b", "c"]
    assert all(g["status"] == "planned" for g in gates)
    # an unshaped (0) leg is recorded skipped-baseline
    gates2 = campaign.run_fidelity_gate_3node(
        15, 0, 0, 0, "pre", dry_run=True)
    assert gates2[0]["status"] == "planned"
    assert gates2[1]["status"] == "skipped-baseline"


def test_3node_build_manifest(tmp_path):
    # 1,910-line pool: the 3-node range 1371..1910 fits.
    pool = _write_pool(tmp_path, 1910)
    man = {"_verified_sha256": "aa" * 32, "n": 1910}
    spec = campaign.V2_RUN_SPECS["three-node"]
    plan = campaign.select_pool_slices_3node(
        campaign.load_queries(str(pool)), spec["cells"], spec["variants"],
        80, 10, 1371, 1910)
    doc = campaign.build_3node_manifest(
        "rid", str(pool), man, 1371, 1910, plan, 80, 10, 3, 42)
    assert doc["schema"] == "pdc.campaign_manifest/v2"
    assert doc["run"] == "three-node"
    assert doc["design_commit"] == "1f911ef"
    assert doc["disjointness"]["asserted"] is True
    assert doc["pool"]["line_start"] == 1371
    assert doc["pool"]["line_end"] == 1910
    # 6 arms, first is 3node-P2 on the (15,15) cell
    assert len(doc["arms"]) == 6
    assert doc["arms"][0]["arm"] == "3node-P2"
    assert doc["arms"][0]["rtt_b"] == 15 and doc["arms"][0]["rtt_c"] == 15
    assert doc["arms"][1]["arm"] == "3node-SPHP-from-C"
    assert doc["arms"][0]["pool_start"] == 1371  # 1-based line numbers
    assert doc["arms"][5]["pool_end"] == 1910
    # per-leg targets recorded on every arm
    assert doc["arms"][0]["netem_link_b_applied"] == {"rtt_ms": 15, "loss_pct": 0}
    assert doc["arms"][0]["netem_link_c_applied"] == {"rtt_ms": 15, "loss_pct": 0}


def test_3node_resolve_mode_defaults():
    ap = campaign.build_parser()
    args = ap.parse_args([
        "--pool", str(QUERIES_DIR / "campaign_v2_master5000_seed42.txt"),
        "--run", "three-node", "--dry-run",
    ])
    campaign.resolve_mode_defaults(args)
    # Node C gateway (user-facing tier), N=80, W=10, R=3 per design §4.3
    assert args.gateway_url == "http://10.8.0.3:8000"
    assert args.query_sample == 80
    assert args.warmups == 10
    assert args.repeats == 3
    assert args.modes == ["hybrid"]
    assert args.sphp_axis is True
    assert args.loss_tiers == [0]


def test_2node_confirmatory_keeps_node_b_gateway():
    ap = campaign.build_parser()
    args = ap.parse_args([
        "--pool", str(QUERIES_DIR / "campaign_v2_master5000_seed42.txt"),
        "--run", "confirmatory", "--dry-run",
    ])
    campaign.resolve_mode_defaults(args)
    # The confirmatory 2-node core keeps the existing Node B gateway default.
    assert args.gateway_url == "http://10.8.0.2:8000"
    assert args.query_sample == 120
    assert args.warmups == 10
    assert args.repeats == 3


def test_v2_explicit_repeats_wins():
    ap = campaign.build_parser()
    args = ap.parse_args([
        "--pool", str(QUERIES_DIR / "campaign_v2_master5000_seed42.txt"),
        "--run", "three-node", "--repeats", "5", "--dry-run",
    ])
    campaign.resolve_mode_defaults(args)
    assert args.repeats == 5
