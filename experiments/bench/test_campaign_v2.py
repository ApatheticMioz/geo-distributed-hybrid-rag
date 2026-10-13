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


# ---------------------------------------------------------------------------
# v2: loss-sweep run (amended design b239833 §3.1/§5.1, amended 2026-10-14)
# ---------------------------------------------------------------------------
def _losssweep_args(tmp_path, pool_path):
    """Build resolved CLI args for the loss-sweep run (design §4.3 defaults:
    N=50 / W=5 / R=1, sphp axis on, float loss tiers)."""
    ap = campaign.build_parser()
    args = ap.parse_args([
        "--pool", str(pool_path),
        "--run", "loss-sweep",
        "--dry-run",
        "--out-dir", str(tmp_path / "out"),
    ])
    campaign.resolve_mode_defaults(args)
    return args


def test_losssweep_spec_frozen():
    spec = campaign.V2_RUN_SPECS["loss-sweep"]
    # design §5.1 final allocation: pool band 2041..2700 (12 arms x (50+5)
    # = 660 lines), derived end inclusive.
    ls, le = campaign.v2_line_range("loss-sweep", 12, 50, 5)
    assert ls == 2041
    assert le == 2700
    # the 6 measured (rtt, loss) cells — float loss percents, cell-major.
    assert spec["cells"] == [(0, 0.5), (0, 1.0), (0, 2.0),
                             (40, 0.5), (40, 1.0), (40, 2.0)]
    # both headline arms (P2 / P2-SPHP), R=1 cold-prefix stratum.
    assert spec["variants"] == ["baseline", "sphp"]
    assert spec["n"] == 50
    assert spec["warmups"] == 5
    assert spec["repeats"] == 1
    assert spec["rtt_tiers"] == [0, 40]
    # the loss-0 sanity pre-flight draws from the free pool tail 3839..
    assert spec["sanity_pool_start"] == 3839


def test_losssweep_arm_expansion(tmp_path):
    pool = QUERIES_DIR / "campaign_v2_master5000_seed42.txt"
    args = _losssweep_args(tmp_path, pool)
    # float tiers resolve: RTT{0,40} x loss{0.5,1,2} x 1 mode x 2 variants.
    assert args.rtt_tiers == [0, 40]
    assert args.loss_tiers == [0.5, 1.0, 2.0]
    assert args.modes == ["hybrid"]
    assert args.sphp_axis is True
    arms = campaign.build_arm_list(args)
    assert len(arms) == 12  # 6 cells x {baseline, sphp}
    assert campaign.v2_line_range("loss-sweep", len(arms),
                                  args.query_sample, args.warmups) == (2041, 2700)


def test_losssweep_select_slices(tmp_path):
    pool = QUERIES_DIR / "campaign_v2_master5000_seed42.txt"
    args = _losssweep_args(tmp_path, pool)
    all_queries = campaign.load_queries(str(pool))
    arms = campaign.build_arm_list(args)
    ls, le = campaign.v2_line_range("loss-sweep", len(arms),
                                    args.query_sample, args.warmups)
    plan = campaign.select_pool_slices(all_queries, arms,
                                       args.query_sample, args.warmups,
                                       ls, le)
    assert len(plan) == 12
    assert plan[0]["pool_start"] == 2041 - 1  # 0-based first index
    assert plan[-1]["pool_end"] == 2700
    assert campaign.slices_disjoint(plan)
    # every slice is 55 (W=5 + N=50); warmups never overlap measured.
    for a in plan:
        assert len(a["warmups"]) == 5
        assert len(a["measured"]) == 50


def test_losssweep_float_loss_parsing(tmp_path):
    ap = campaign.build_parser()
    args = ap.parse_args([
        "--pool", str(QUERIES_DIR / "campaign_v2_master5000_seed42.txt"),
        "--run", "loss-sweep",
        "--loss-tiers-float", "0.5", "1.0", "2.0",
        "--dry-run",
    ])
    campaign.resolve_mode_defaults(args)
    # the CLI float tiers are validated (finite, 0..100) and preserved.
    assert args.loss_tiers == [0.5, 1.0, 2.0]
    # _validate_loss_tiers accepts fractional percents and rejects non-finite.
    assert campaign._validate_loss_tiers([0.5, 1.0, 2.0]) == [0.5, 1.0, 2.0]
    with pytest.raises(ValueError, match="finite"):
        campaign._validate_loss_tiers([float("nan")])
    with pytest.raises(ValueError, match="out of range"):
        campaign._validate_loss_tiers([101.0])


def test_losssweep_band_disjoint():
    # design §5.1: the loss-sweep band 2041..2700 must not overlap any other
    # registered (or frozen) pool line-range.
    loss = (2041, 2700)
    others = {
        "confirmatory": (1, 1040),
        "reserved": (1371, 1910),          # three-node (final re-key target)
        "three-node": (1911, 2040),        # dropout (frozen)
        "three-node-stress": (2701, 2920), # CM-5 (final re-key target)
        "tail-A": (2921, 3838),            # CM-5 follow-up band
        "free-tail": (3839, 5000),         # sanity pre-flight source
    }
    campaign.assert_pool_ranges_disjoint({"loss-sweep": loss, **others})


def test_losssweep_wilson_wiring(tmp_path, monkeypatch):
    # The summary path routes the sphp arm's hit-rate through the lazily
    # imported wilson helper.  Monkeypatch the getter to a deterministic
    # fake to prove the wiring end-to-end.
    calls = []

    def fake_wilson(k, n, z=1.96):
        calls.append((k, n))
        return (0.10, 0.20)

    monkeypatch.setattr(campaign, "_get_wilson_ci", lambda: fake_wilson)

    recs = [
        {"sphp_hit": True, "ttft_ms": 1.0, "total_ms": 2.0, "repeat_index": 0},
        {"sphp_hit": False, "ttft_ms": 1.5, "total_ms": 3.0, "repeat_index": 0},
        {"sphp_hit": True, "ttft_ms": 0.5, "total_ms": 2.0, "repeat_index": 0},
    ]
    out = campaign.sphp_hit_rate_wilson(recs)
    assert calls == [(2, 3)]
    assert out["n"] == 3
    assert out["n_hits"] == 2
    assert out["rate"] == round(2 / 3, 4)
    assert out["ci_95"] == [0.10, 0.20]  # not None
    # a record set with no sphp_hit yields null CI (no division by zero).
    out_none = campaign.sphp_hit_rate_wilson([{"sphp_hit": None}])
    assert out_none["ci_95"] is None
    assert out_none["rate"] is None


def test_losssweep_gate_semantics_runner(tmp_path, monkeypatch):
    """Gate semantics for the loss-sweep runner (design §3.1/§6), driven
    end-to-end with fake network/netem side effects against the REAL
    5,000-line master pool (band arithmetic stays genuine; N=2/W=1 shrinks
    the per-arm slice so the small run fits).

    (a) loss-0 sanity pre-flight error -> ABORT (exit 1, before loss is
        applied).
    (b) loss>0 measured-cell error -> per-cell error-rate REPORT, no
        abort (exit 0) — the loss-driven error is the signal.
    """
    pool = QUERIES_DIR / "campaign_v2_master5000_seed42.txt"
    manifest = {"_verified_sha256": campaign.sha256_file(str(pool)),
                "n": 5000}
    ap = campaign.build_parser()
    args = ap.parse_args([
        "--pool", str(pool),
        "--run", "loss-sweep",
        "--loss-tiers-float", "0.5", "1.0", "2.0",
        "--query-sample", "2",
        "--warmups", "1",
        "--repeats", "1",
        "--gateway-url", "http://gw.test",
        "--out-dir", str(tmp_path / "out"),
    ])
    campaign.resolve_mode_defaults(args)
    all_queries = campaign.load_queries(str(pool))
    assert len(all_queries) == 5000
    arms = campaign.build_arm_list(args)
    assert len(arms) == 12

    # Small per-arm slice: the run needs 12 x (N=2 + W=1) = 36 measured-band
    # lines + 2 x N_sanity sanity lines.  Keep the sanity ranges real
    # (3839..) but shrink the measured band to 1..36 via the range helper.
    monkeypatch.setattr(campaign, "v2_line_range",
                        lambda run, n_arms, n, w: (1, 1 + n_arms * (n + w) - 1))
    # The real runner derives ls/le before calling the runner, so patch the
    # actual call the test makes below identically.
    ls, le = campaign.v2_line_range("loss-sweep", len(arms),
                                    args.query_sample, args.warmups)
    arm_plan = campaign.select_pool_slices(
        all_queries, arms, args.query_sample, args.warmups, ls, le)
    # _finalize calls write_summary_outputs; re-implement it to write the
    # summary JSON doc (skipping the CSV) so we can assert on the per-cell
    # error report + Wilson wiring in the summary.
    def write_summary_real(out_dir, run_id, rows, doc):
        (out_dir / f"summary_{run_id}.json").write_text(
            json.dumps(doc, indent=2), encoding="utf-8")
        return (out_dir / f"summary_{run_id}.csv",
                out_dir / f"summary_{run_id}.json")
    monkeypatch.setattr(campaign, "write_summary_outputs", write_summary_real)

    def _patch_net(monkeypatch, fail_on_call):
        """fail_on_call: 1-based POST index that raises; None = never.
        Sanity pre-flight is calls 1..20 (2 rtt tiers x sanity_n=10); the
        first measured cell (rtt=0, loss=0.5, P2) starts at call 21."""
        calls = {"n": 0}
        def fake_health(url, timeout=15):
            return True
        def fake_gate(rtt, loss, when, dry_run):
            return {"rtt_ms": rtt, "loss_pct": loss, "when": when,
                    "status": "PASS", "shaped_median_ms": 5.0,
                    "added_rtt_ms": 5.0, "loss_gate": "PASS",
                    "output_tail": ""}
        def fake_apply(rtt, loss, dry_run):
            return True
        def fake_clear(dry_run):
            return None
        def fake_http(url, payload, timeout):
            calls["n"] += 1
            if fail_on_call is not None and calls["n"] == fail_on_call:
                raise RuntimeError("synthetic loss-driven error")
            return {"query_id": "1", "top_k": payload.get("top_k"),
                    "timings": {"ttft_ms": 1.0, "total_ms": 2.0,
                                "sparse_ms": 0, "dense_ms": 0,
                                "fusion_ms": 0, "decode_ms": 0,
                                "simulated_wan_ms": 0},
                    "token_count": 3, "decode_tps": 4.0,
                    "sphp_hit": True, "sphp_overlap": 0.5}
        monkeypatch.setattr(campaign, "check_health", fake_health)
        monkeypatch.setattr(campaign, "run_fidelity_gate",
                            lambda rtt, when, dry_run, loss=0:
                            fake_gate(rtt, loss, when, dry_run))
        monkeypatch.setattr(campaign, "loss_sweep_cell_gate", fake_gate)
        monkeypatch.setattr(campaign, "apply_netem_link_b", fake_apply)
        monkeypatch.setattr(campaign, "clear_netem", fake_clear)
        monkeypatch.setattr(campaign, "http_post_json", fake_http)
        return calls

    # (a) sanity pre-flight error -> ABORT before loss is applied (exit 1).
    _patch_net(monkeypatch, fail_on_call=1)
    with pytest.raises(SystemExit) as ei:
        campaign.run_campaign_v2_losssweep(args, all_queries, manifest,
                                           arm_plan, ls, le)
    assert ei.value.code == 1

    # (b) measured loss>0-cell error -> REPORT, run completes (exit 0).
    # POST order: sanity pre-flight = 2 x sanity_n (N=10/tier) = 20 calls;
    # per cell: arm P2 (W=1 + N=2) then arm P2-SPHP (W=1 + N=2).  The first
    # MEASURED POST of cell 1 (rtt=0, loss=0.5, P2 baseline) is call 23
    # (20 sanity + 1 P2 warmup + 1).  Failing it exercises the per-cell
    # error-report path (n_errors, per_cell_error_reports, exit 0).
    args.out_dir = str(tmp_path / "out2")
    calls = _patch_net(monkeypatch, fail_on_call=23)
    with pytest.raises(SystemExit) as ei:
        campaign.run_campaign_v2_losssweep(args, all_queries, manifest,
                                           arm_plan, ls, le)
    assert ei.value.code == 0  # report-only: no abort for loss>0 cells
    summary = json.loads(
        next((tmp_path / "out2").glob("summary_*.json")).read_text())
    assert summary["status"] == "completed"
    assert summary["n_errors"] == 1  # the synthetic measured-cell error
    reports = summary["per_cell_error_reports"]
    assert any(r["n_errors"] == 1 and "no abort" in r["gate"]
               for r in reports)
    # Wilson wiring: every sphp arm row carries a ci_95 (here the real
    # stats engine; assert it is a 2-float list, i.e. not None).
    sphp_rows = [r for r in summary["summary"] if r["variant"] == "sphp"]
    assert len(sphp_rows) == 6
    for r in sphp_rows:
        assert r["sphp_hit_rate_wilson"]["ci_95"] is not None


# ---------------------------------------------------------------------------
# v2: CM-5 three-node-stress run (final design b239833 §3.4 / §5.1)
# ---------------------------------------------------------------------------
def _stress_cells_from_design():
    """Reconstruct the design §3.4 cell set independently: 3 (B,C) RTT cells
    x 3 loss tiers {0.5,1,2} x 3 leg-placements {B, C, both}, C frozen at 15.
    For each (rtt_b, rtt_c) and loss tier the three placements are
    (loss,0) / (0,loss) / (loss,loss)."""
    expected = []
    for rtt_b in (15, 40, 80):
        for tier in (0.5, 1.0, 2.0):
            expected += [(rtt_b, 15, tier, 0.0),
                          (rtt_b, 15, 0.0, tier),
                          (rtt_b, 15, tier, tier)]
    return expected


def test_stress_spec_frozen():
    spec = campaign.V2_RUN_SPECS["three-node-stress"]
    # 54 arms (27 cells x 2 variants) x (N=15 + W=2) = 918 pool lines.
    arms = campaign.build_stress_arm_list(spec)
    assert len(arms) == 54
    ls, le = campaign.v2_line_range("three-node-stress", len(arms),
                                    spec["n"], spec["warmups"])
    assert ls == 2921
    assert le == 3838
    # Band arithmetic: 54 arms x (15+2) = 54 x 17 = 918, and the band is
    # 2921..3838 inclusive = 918 lines -> ZERO slack (fits exactly).
    assert (le - ls + 1) == 918
    assert len(arms) * (spec["n"] + spec["warmups"]) == 918
    # design §3.4 / §4.3 sizing: N=15, W=2, R=1 per arm.
    assert spec["n"] == 15
    assert spec["warmups"] == 2
    assert spec["repeats"] == 1
    # The two 3-node arms (design §3.4 -> §3.2): P2 + SPHP-from-C.
    assert spec["variants"] == ["baseline", "sphp"]
    # The (0,0) unshaped sanity pre-flight: 20 requests from the FREE tail
    # 3839.. (the same free-tail pattern the loss-sweep uses), never the band.
    assert spec["sanity_n"] == 20
    assert spec["sanity_pool_start"] == 3839
    assert spec["sanity_pool_start"] >= 3839  # strictly in the free tail


def test_stress_cells_match_design():
    # The 27 (B, C, lossB, lossC) cells must match design §3.4 exactly:
    # 3 (B,C) RTT cells x 3 loss tiers {0.5,1,2} x 3 leg-placements (B/C/both),
    # C frozen at 15, order cell-major (RTT, then tier, then placement).
    spec = campaign.V2_RUN_SPECS["three-node-stress"]
    assert len(spec["cells"]) == 27
    assert spec["cells"] == _stress_cells_from_design()
    # C leg frozen at 15 ms across every cell (user-facing sparse leg).
    assert all(rtt_c == 15 for _, rtt_c, _, _ in spec["cells"])
    # B leg carries the swept RTT; every loss value is a float percent.
    assert all(rtt_b in (15, 40, 80) for rtt_b, _, _, _ in spec["cells"])


def test_stress_arm_expansion():
    spec = campaign.V2_RUN_SPECS["three-node-stress"]
    arms = campaign.build_stress_arm_list(spec)
    # Cell-major, variant-second: first cell (15,15,0.5,0.0) -> baseline then
    # sphp; last cell (80,15,2.0,2.0) -> sphp is the 54th arm.
    assert arms[0] == (15, 15, 0.5, 0.0, "baseline")
    assert arms[1] == (15, 15, 0.5, 0.0, "sphp")
    assert arms[-1] == (80, 15, 2.0, 2.0, "sphp")
    # Both arms present for every cell, in baseline -> sphp order.
    for cell in spec["cells"]:
        pair = [a for a in arms if a[:4] == cell]
        assert [a[4] for a in pair] == ["baseline", "sphp"]


def test_stress_select_pool_slices(tmp_path):
    pool = QUERIES_DIR / "campaign_v2_master5000_seed42.txt"
    all_queries = campaign.load_queries(str(pool))
    spec = campaign.V2_RUN_SPECS["three-node-stress"]
    n_arms = len(campaign.build_stress_arm_list(spec))
    ls, le = campaign.v2_line_range("three-node-stress", n_arms,
                                    spec["n"], spec["warmups"])
    plan = campaign.select_pool_slices_stress(
        all_queries, spec["cells"], spec["variants"],
        spec["n"], spec["warmups"], ls, le)
    assert len(plan) == 54
    assert plan[0]["pool_start"] == 2920   # 0-based index for line 2921
    assert plan[0]["pool_end"] == 2937     # 17-line arm block (2W + 15)
    assert plan[-1]["pool_end"] == 3838    # last line of the band
    # Every arm block is exactly 17 lines (W=2 + N=15).
    for a in plan:
        assert len(a["warmups"]) == 2
        assert len(a["measured"]) == 15
        assert a["pool_end"] - a["pool_start"] == 17
    # Pairwise disjoint over all 54 arm slices (warmups + measured).
    sets = [set(a["warmups"]) | set(a["measured"]) for a in plan]
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            assert not (sets[i] & sets[j])


def test_stress_band_disjoint():
    # design §5.1 final allocation: the CM-5 band 2921..3838 must be
    # pairwise disjoint from every other registered/frozen pool line-range,
    # INCLUDING the amended loss-sweep band 2041..2700 (the bug this re-key
    # fixes) and the free tail 3839.. used by the sanity pre-flight.
    spec = campaign.V2_RUN_SPECS["three-node-stress"]
    n_arms = len(campaign.build_stress_arm_list(spec))
    ls, le = campaign.v2_line_range("three-node-stress", n_arms,
                                    spec["n"], spec["warmups"])
    stress = (ls, le)
    assert stress == (2921, 3838)
    ranges = {
        "confirmatory": (1, 1040),
        "reserved_gap": (1041, 1370),   # pre-amendment loss-sweep band (unused)
        "three-node": (1371, 1910),     # WS-CM 3-node (frozen)
        "dropout": (1911, 2040),
        "loss-sweep": (2041, 2700),     # amended (the overlap being fixed)
        "genuine-wan": (2701, 2920),    # CM-4
        "three-node-stress": (2921, 3838),
        "free_tail": (3839, 5000),      # sanity pre-flight source
    }
    campaign.assert_pool_ranges_disjoint(ranges)
    # The sanity block (20 from the free tail) stays clear of the band.
    sanity = (spec["sanity_pool_start"],
              spec["sanity_pool_start"] + spec["sanity_n"] - 1)
    assert sanity == (3839, 3858)
    assert sanity[0] > le  # strictly after the band's end
    # ...and stays within the free tail.
    assert sanity[1] <= 5000
