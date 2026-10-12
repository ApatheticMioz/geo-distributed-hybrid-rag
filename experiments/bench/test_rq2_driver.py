"""Tests for the RQ2 node-dropout driver (experiments/bench/rq2_driver.py).

Covers the pure functions and the full run loop against a LOCAL MOCK
GATEWAY (a threaded ``http.server`` on 127.0.0.1) and a MOCK fault
executor (an injected command runner — no real ``bash -c``, no SSH, no
netem, no live node HTTP):

  * parse_fault_at          (index vs time vs suffix vs invalid values)
  * segment_plan            (pre/during planning; time-mode)
  * build_manifest          (schema, fault/recover/probe recording)
  * format_plan             (--dry-run plan output)
  * recovery_streak         (K-streak detection, reset on failure)
  * exec_shell_command      (ok / non-zero exit / exception — the
                              "truncated-command" error handling)
  * summarize_run           (segments, tt-first-success, tt-recovery,
                              p50/p95)
  * run_rq2 end-to-end      (index fault: firing timing, exactly-once
                              fault/recover commands, JSONL + manifest
                              conventions, dry fault, never-recovered
                              run, time-based fault, --dry-run purity,
                              no-overwrite guard)

Fences honored: NO live HTTP to real nodes, NO netem, NO SSH — every
gateway hit is to the local mock and every command goes through the
injected fake runner.
"""

import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rq2_driver as rq2  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
def _write_queries(tmp_path: Path, n: int) -> Path:
    p = tmp_path / "queries.txt"
    p.write_text("\n".join(f"query {i}" for i in range(n)) + "\n",
                 encoding="utf-8")
    return p


def _args(tmp_path: Path, **over) -> "SimpleNamespace":
    """Build an argparse.Namespace matching build_parser's surface."""
    a = SimpleNamespace(
        gateway_url="http://127.0.0.1:1",  # never used in pure-function tests
        queries=str(tmp_path / "queries.txt"),
        rate=None,
        steady=True,
        fault_at="4",
        fault_command="MOCK-FAULT",
        recover_command="MOCK-RECOVER",
        probe_health=None,
        probe_interval=1.0,
        timeout=2.0,
        recover_streak=3,
        out_dir=str(tmp_path / "out"),
        dry_run=False,
    )
    for k, v in over.items():
        setattr(a, k, v)
    return a


class _FakeProc:
    def __init__(self, returncode: int, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr


def _fake_run(calls: list, rc: int = 0, raise_exc: bool = False,
              stderr: str = ""):
    """An injected command runner: records every argv, returns a fake
    CompletedProcess (or raises)."""
    def run(argv):
        calls.append(argv)
        if raise_exc:
            raise OSError("mock bash: command not found")
        return _FakeProc(rc, stderr=stderr)
    return run


# ---------------------------------------------------------------------------
# parse_fault_at
# ---------------------------------------------------------------------------
class TestParseFaultAt:
    def test_index(self):
        assert rq2.parse_fault_at("4", 10) == (4, None)

    def test_time_float(self):
        assert rq2.parse_fault_at("10.5", 10) == (None, 10.5)

    def test_time_suffix_forces_time_mode(self):
        # "0" bare is rejected as an index; "0s" is a valid time-based
        # fault (fire at run start, before the first request).
        assert rq2.parse_fault_at("0s", 10) == (None, 0.0)
        assert rq2.parse_fault_at("10sec", 10) == (None, 10.0)

    def test_index_zero_rejected(self):
        with pytest.raises(ValueError, match="no pre-fault baseline"):
            rq2.parse_fault_at("0", 10)

    def test_index_out_of_range(self):
        with pytest.raises(ValueError, match="exceeds the query"):
            rq2.parse_fault_at("11", 10)

    def test_negative_rejected(self):
        with pytest.raises(ValueError, match=">= 0"):
            rq2.parse_fault_at("-3", 10)

    def test_non_numeric_rejected(self):
        with pytest.raises(ValueError, match="not a number"):
            rq2.parse_fault_at("soon", 10)

    def test_boundary_index_equals_n_ok(self):
        # Firing before the LAST request leaves a one-request fault window.
        assert rq2.parse_fault_at("10", 10) == (10, None)


# ---------------------------------------------------------------------------
# segment_plan
# ---------------------------------------------------------------------------
def test_segment_plan_index():
    plan = rq2.segment_plan(8, 3)
    assert plan == ["pre_fault"] * 3 + ["during_fault"] * 5


def test_segment_plan_time_mode_all_eligible():
    # Time-based faults: every request starts as during_fault-eligible;
    # the actual pre/during boundary is runtime-determined.
    assert rq2.segment_plan(5, None) == ["during_fault"] * 5


# ---------------------------------------------------------------------------
# build_manifest
# ---------------------------------------------------------------------------
def test_build_manifest_index(tmp_path):
    args = _args(tmp_path, fault_at="4", fault_command="A && B",
                 recover_command="C", probe_health="http://x/health")
    man = rq2.build_manifest("rid1", args, ["q0", "q1", "q2", "q3", "q4"],
                             fault_index=4, fault_seconds=None,
                             endpoint="http://x/query/benchmark")
    assert man["schema"] == rq2.SCHEMA_MANIFEST
    assert man["run_id"] == "rid1"
    assert man["n_queries"] == 5
    assert man["fault"] == {
        "at": "4", "mode": "index", "index": 4, "seconds": None,
        "command": "A && B", "dry": False,
    }
    assert man["recovery"] == {"command": "C", "recover_streak": 3}
    assert man["probe"] == {"health_url": "http://x/health",
                            "interval_s": 1.0}
    assert man["pacing"] == "steady"
    assert man["dry_run"] is False


def test_build_manifest_dry_and_time(tmp_path):
    args = _args(tmp_path, fault_at="0.5s", fault_command="",
                 recover_command="")
    man = rq2.build_manifest("rid2", args, ["q"], None, 0.5,
                             "http://x/query/benchmark")
    assert man["fault"]["mode"] == "time"
    assert man["fault"]["seconds"] == 0.5
    assert man["fault"]["dry"] is True
    assert man["recovery"]["command"] == ""


# ---------------------------------------------------------------------------
# format_plan (--dry-run output)
# ---------------------------------------------------------------------------
def test_format_plan_index_shows_timeline_and_commands(tmp_path):
    args = _args(tmp_path, fault_at="4", fault_command="ssh laptop '...'",
                 recover_command="ssh laptop '...2'")
    man = rq2.build_manifest("rid", args, ["q"] * 8, 4, None,
                             "http://x/query/benchmark")
    out = rq2.format_plan(args, man, rq2.segment_plan(8, 4), 8)
    assert "DRY RUN" in out
    # the commands appear exactly as recorded in the manifest
    assert "fault cmd  : " in out and "ssh laptop '...'" in out
    assert "recover cmd: " in out and "ssh laptop '...2'" in out
    assert "DRY — no command" not in out
    assert "K=3" in out
    # timeline rows: seqs 0..3 pre, seqs 4..7 during, marker at index 4
    assert f"{0:>6}  pre_fault" in out
    assert f"{3:>6}  pre_fault" in out
    assert f"{4:>6}  during_fault" in out
    assert "<== fault fires before this request" in out
    # commands exactly as recorded in the manifest
    assert man["fault"]["command"] in out
    assert man["recovery"]["command"] in out


def test_format_plan_time_mode(tmp_path):
    args = _args(tmp_path, fault_at="0.5s")
    man = rq2.build_manifest("rid", args, ["q"] * 5, None, 0.5,
                             "http://x/query/benchmark")
    out = rq2.format_plan(args, man, rq2.segment_plan(5, None), 5)
    assert "0.5s into the run" in out
    assert "pre/during boundary is decided at runtime" in out


# ---------------------------------------------------------------------------
# recovery_streak
# ---------------------------------------------------------------------------
def _recs(seq_oks):
    return [{"seq": i, "ok": ok} for i, ok in enumerate(seq_oks, start=2)]


def test_recovery_streak_basic():
    # fails at 2,3; successes at 4,5,6 -> streak of 3 completes at 6
    assert rq2.recovery_streak(_recs([0, 0, 1, 1, 1]), 3) == 6


def test_recovery_streak_reset_on_failure():
    # ok, ok, fail, ok, ok, ok -> completes at 7
    assert rq2.recovery_streak(_recs([1, 1, 0, 1, 1, 1]), 3) == 7


def test_recovery_streak_never():
    assert rq2.recovery_streak(_recs([0, 1, 0, 1, 0]), 3) is None
    # endless isolated singles never form a streak
    assert rq2.recovery_streak(_recs([1, 0, 1, 0]), 2) is None


def test_recovery_streak_one():
    assert rq2.recovery_streak(_recs([0, 1, 0]), 1) == 3


def test_recovery_streak_invalid():
    with pytest.raises(ValueError):
        rq2.recovery_streak([], 0)


# ---------------------------------------------------------------------------
# exec_shell_command (mocked runner — the "truncated command" handling)
# ---------------------------------------------------------------------------
def test_exec_command_ok(tmp_path):
    calls = []
    out = rq2.exec_shell_command("true", run=_fake_run(calls))
    assert out["ok"] is True and out["returncode"] == 0
    assert out["exception"] is None
    assert calls == [["bash", "-c", "true"]]


def test_exec_command_nonzero_exit_is_logged_not_raised(tmp_path):
    # A fault/recover command that fails (e.g. a truncated/typo'd
    # ssh command) must NOT abort the run: it is logged with ok=False
    # and a stderr tail.
    calls = []
    out = rq2.exec_shell_command("bad", run=_fake_run(
        calls, rc=127, stderr="ssh: command not found\n"))
    assert out["ok"] is False
    assert out["returncode"] == 127
    assert "command not found" in out["stderr_tail"]
    assert out["exception"] is None


def test_exec_command_exception_captured(tmp_path):
    # Runner itself raising (e.g. no bash): captured, not propagated.
    calls = []
    out = rq2.exec_shell_command("x", run=_fake_run(calls, raise_exc=True))
    assert out["ok"] is False
    assert out["returncode"] is None
    assert out["exception"]
    assert "command not found" in out["exception"]


def test_exec_command_stderr_tail_truncated(tmp_path):
    calls = []
    long_err = "E" * 1000
    out = rq2.exec_shell_command("x", run=_fake_run(calls, rc=1,
                                                    stderr=long_err))
    assert len(out["stderr_tail"]) == 400


# ---------------------------------------------------------------------------
# summarize_run
# ---------------------------------------------------------------------------
def _mk_man(tmp_path):
    args = _args(tmp_path)
    return rq2.build_manifest("rid", args, ["q"] * 8, 3, None,
                              "http://x/query/benchmark")


def _summary_recs():
    # 8 records; fault at index 3; failures at 3,4; successes 5,6 (streak
    # completes at 7 with K=3 -> seqs 5,6,7).
    seq_ok = {3: 0, 4: 0, 5: 1, 6: 1, 7: 1}
    recs = []
    for i in range(8):
        ok = bool(seq_ok.get(i, 1))
        seg = ("pre_fault" if i < 3
               else "recovered" if i >= 7
               else "during_fault")
        r = {"seq": i, "ok": ok, "segment": seg,
             "t_sent": 1000.0 + i, "t_resp": 1000.0 + i + 0.1,
             "latency_ms": 100.0}
        if not ok:
            r["error"] = "boom"
        recs.append(r)
    return recs


def test_summarize_segments_and_recovery(tmp_path):
    recs = _summary_recs()
    man = _mk_man(tmp_path)
    fault_event = {"t": 1000.0 + 3, "index": 3}
    s = rq2.summarize_run(recs, fault_event, 7, 3, probes=[],
                          fault_exec={"ok": True},
                          recover_exec={"ok": True}, manifest=man,
                          exit_status="completed")
    segs = s["segments"]
    assert segs["pre_fault"] == {"n": 3, "errors": 0,
                                 "p50_ms": 100.0, "p95_ms": 100.0}
    # during = seqs 3..6 (4 records, 2 of them the failed ones)
    assert segs["during_fault"] == {"n": 4, "errors": 2,
                                    "p50_ms": 100.0, "p95_ms": 100.0}
    # recovered = seq >= 7 (only seq 7 in this 8-record sample)
    assert segs["recovered"]["n"] == 1
    assert segs["recovered"]["errors"] == 0
    # tt-first-success: first post-fault success at seq 5, t_sent - t0
    assert s["time_to_first_success_at_seq"] == 5
    assert s["time_to_first_success_s"] == pytest.approx(2.0)
    # tt-recovery: streak completes at seq 7 -> t_resp(7) - t0
    assert s["time_to_recovery_s"] == pytest.approx(4.1)
    assert s["fault"]["index"] == 3
    assert s["recovery"]["recovered_at_seq"] == 7


def test_summarize_no_recovery(tmp_path):
    seq_ok = {3: 0, 4: 0, 5: 1, 6: 0, 7: 1}
    recs = []
    for i in range(8):
        ok = bool(seq_ok.get(i, 1))
        seg = "pre_fault" if i < 3 else "during_fault"
        r = {"seq": i, "ok": ok, "segment": seg,
             "t_sent": 1000.0 + i, "t_resp": 1000.0 + i + 0.1,
             "latency_ms": 100.0}
        if not ok:
            r["error"] = "boom"
        recs.append(r)
    man = _mk_man(tmp_path)
    s = rq2.summarize_run(recs, {"t": 1003.0, "index": 3}, None, 3, [],
                          None, None, man, "completed")
    assert "recovered" not in s["segments"]
    assert s["segments"]["during_fault"]["n"] == 5
    assert s["time_to_recovery_s"] is None
    assert s["recovery"]["recovered_at_seq"] is None
    # first post-fault success still measured (at seq 5)
    assert s["time_to_first_success_at_seq"] == 5


# ---------------------------------------------------------------------------
# mock gateway (local 127.0.0.1 only)
# ---------------------------------------------------------------------------
class _MockGateway(http.server.BaseHTTPRequestHandler):
    """Serves /query/benchmark and /health on 127.0.0.1.

    ``state["fail_seqs"]``: set of seq numbers that get a 503;
    ``state["health_fail"]``: bool; ``state["n_requests"]``: counter.
    """

    def log_message(self, *a):  # silence
        pass

    def _body(self):
        return json.dumps({"ok": True}).encode()

    def do_POST(self):
        if self.path != "/query/benchmark":
            self.send_response(404)
            self.end_headers()
            return
        n = type(self).state["n_requests"]
        type(self).state["n_requests"] = n + 1
        after = type(self).state.get("health_fail_after_n")
        if after is not None and n >= after:
            type(self).state["health_fail"] = True
        if n in type(self).state["fail_seqs"]:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"gateway down")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(self._body())

    def do_GET(self):
        if self.path == "/health":
            code = 503 if type(self).state["health_fail"] else 200
            self.send_response(code)
            self.end_headers()
            self.wfile.write(self._body())
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def mock_gateway():
    """Local threaded mock gateway on 127.0.0.1 (the ONLY HTTP target in
    these tests).  Yields a dict with its base URL and a mutable state:
      state["fail_seqs"]    set of request counters answered with 503
      state["health_fail"]  bool: /health returns 503 when True
      state["n_requests"]   number of /query/benchmark POSTs seen
    """
    class _Handler(_MockGateway):
        state = {"fail_seqs": set(), "health_fail": False,
                 "health_fail_after_n": None, "n_requests": 0}

    th = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    th.daemon_threads = True
    t = threading.Thread(target=th.serve_forever, daemon=True)
    t.start()
    yield {"url": f"http://127.0.0.1:{th.server_address[1]}",
           "state": _Handler.state}
    th.shutdown()
    th.server_close()
    t.join(timeout=5)


# ---------------------------------------------------------------------------
# run_rq2 end-to-end (mock gateway + injected command runner)
# ---------------------------------------------------------------------------
def test_run_index_fault_full(tmp_path, mock_gateway):
    """Index fault at 3: gateway fails requests 3,4; the K=3 streak
    completes at seq 7.  Checks manifest-first ordering, exactly-once
    fault/recover command execution, record schema, and segment labels."""
    q = _write_queries(tmp_path, 8)
    gw = mock_gateway
    gw["state"]["fail_seqs"] = {3, 4}
    calls = []
    args = _args(tmp_path, gateway_url=gw["url"], queries=str(q),
                 fault_at="3", fault_command="FAKE-FAULT",
                 recover_command="FAKE-RECOVER",
                 out_dir=str(tmp_path / "out"))
    rc = rq2.run_rq2(args, exec_run=_fake_run(calls))
    assert rc == 0

    # the two pluggable commands were each executed EXACTLY once, via
    # bash -c, in fault-then-recover order.
    assert calls == [["bash", "-c", "FAKE-FAULT"],
                     ["bash", "-c", "FAKE-RECOVER"]]

    out = Path(args.out_dir)
    jsonls = sorted(out.glob("rq2_*.jsonl"))
    manifests = sorted(out.glob("rq2_*_manifest.json"))
    summaries = sorted(out.glob("summary_*.json"))
    assert len(jsonls) == 1 and len(manifests) == 1 and len(summaries) == 1

    # manifest FIRST: written before the JSONL ever opened.
    assert manifests[0].stat().st_mtime <= jsonls[0].stat().st_mtime
    man = json.loads(manifests[0].read_text())
    assert man["schema"] == rq2.SCHEMA_MANIFEST
    assert man["fault"] == {"at": "3", "mode": "index", "index": 3,
                            "seconds": None, "command": "FAKE-FAULT",
                            "dry": False}
    assert man["recovery"]["command"] == "FAKE-RECOVER"

    recs = [json.loads(l) for l in jsonls[0].read_text().splitlines()]
    assert len(recs) == 8
    # segment labels: pre 0..2; during 3..6; recovered 7 (streak end)
    assert [r["segment"] for r in recs] == (
        ["pre_fault"] * 3 + ["during_fault"] * 4 + ["recovered"])
    for r in recs:
        # full record schema (the {seq, t_sent, t_resp, ok, latency_ms,
        # error} fields of the task spec, plus segment/query/fault_at)
        assert set(r) >= {"run_id", "seq", "t_sent", "t_resp", "ok",
                          "latency_ms", "segment", "query", "fault_at"}
        assert r["ok"] == (r["seq"] not in (3, 4))
        assert r["latency_ms"] >= 0
        if r["seq"] < 3:
            assert r["fault_at"] is None
        else:
            assert r["fault_at"]["index"] == 3
            assert r["fault_at"]["t"] is not None
        assert ("error" in r) == (not r["ok"])
    # every request was actually sent to the mock
    assert gw["state"]["n_requests"] == 8

    s = json.loads(summaries[0].read_text())
    assert s["schema"] == rq2.SCHEMA_SUMMARY
    assert s["status"] == "completed"
    assert s["segments"]["pre_fault"]["n"] == 3
    assert s["segments"]["pre_fault"]["errors"] == 0
    assert s["segments"]["during_fault"] == {"n": 4, "errors": 2,
                                             "p50_ms": s["segments"]
                                             ["during_fault"]["p50_ms"],
                                             "p95_ms": s["segments"]
                                             ["during_fault"]["p95_ms"]}
    assert s["segments"]["during_fault"]["errors"] == 2
    assert s["segments"]["recovered"]["n"] == 1
    assert s["segments"]["recovered"]["errors"] == 0
    # time-to-first-success: seq 5 (first post-fault ok)
    assert s["time_to_first_success_at_seq"] == 5
    assert s["time_to_first_success_s"] > 0
    # time-to-recovery: the K=3 streak completes at seq 7
    assert s["recovery"]["recovered_at_seq"] == 7
    assert s["time_to_recovery_s"] is not None
    assert s["time_to_recovery_s"] > s["time_to_first_success_s"]
    assert s["fault"]["exec"]["ok"] is True
    assert s["recovery"]["exec"]["ok"] is True


def test_run_dry_fault_command(tmp_path, mock_gateway):
    """--fault-command '': the window still opens at the fault index and
    recovery is still detected; NO command is executed (the recorded
    fault exec is the dry stub, recover is absent)."""
    q = _write_queries(tmp_path, 6)
    gw = mock_gateway
    gw["state"]["fail_seqs"] = {2}
    calls = []
    args = _args(tmp_path, gateway_url=gw["url"], queries=str(q),
                 fault_at="2", fault_command="", recover_command="",
                 out_dir=str(tmp_path / "out"))
    rc = rq2.run_rq2(args, exec_run=_fake_run(calls))
    assert rc == 0
    assert calls == []  # nothing executed: dry fault, no recover command
    jsonls = sorted(Path(args.out_dir).glob("rq2_*.jsonl"))
    recs = [json.loads(l) for l in jsonls[0].read_text().splitlines()]
    # seq2 fails; streak of 3 ok completes at seq5
    assert [r["segment"] for r in recs] == (
        ["pre_fault"] * 2 + ["during_fault"] * 3 + ["recovered"])
    s = json.loads(sorted(Path(args.out_dir).glob("summary_*.json"))[0]
                  .read_text())
    assert s["fault"]["dry"] is True
    assert s["fault"]["exec"] == {"command": "", "ok": True, "returncode": 0,
                                  "stderr_tail": None, "exception": None}
    assert s["recovery"]["exec"] is None
    assert s["recovery"]["recovered_at_seq"] == 5


def test_run_never_recovers(tmp_path, mock_gateway):
    """All post-fault requests fail: no recovered segment, no recovery
    command failure aborts the run; tt-recovery is None."""
    q = _write_queries(tmp_path, 6)
    gw = mock_gateway
    gw["state"]["fail_seqs"] = {2, 3, 4, 5}
    # recover command fails (rc!=0) — must be logged, not fatal
    calls = []
    def dispatch(argv):
        calls.append(argv)
        cmd = argv[-1]
        if "RECOVER" in cmd:
            return _FakeProc(1, stderr="recover failed")
        return _FakeProc(0)
    args = _args(tmp_path, gateway_url=gw["url"], queries=str(q),
                 fault_at="2", fault_command="FAKE-FAULT",
                 recover_command="FAKE-RECOVER", out_dir=str(tmp_path / "out"))
    rc = rq2.run_rq2(args, exec_run=dispatch)
    assert rc == 0  # a failed recovery command is logged, not fatal
    out = Path(args.out_dir)
    jsonls = sorted(out.glob("rq2_*.jsonl"))
    recs = [json.loads(l) for l in jsonls[0].read_text().splitlines()]
    assert recs[0]["segment"] == "pre_fault"
    assert all(r["segment"] == "during_fault" for r in recs[2:])
    summaries = sorted(out.glob("summary_*.json"))
    s = json.loads(summaries[0].read_text())
    assert "recovered" not in s["segments"]
    assert s["recovery"]["recovered_at_seq"] is None
    assert s["time_to_recovery_s"] is None
    assert s["recovery"]["exec"]["ok"] is False
    assert "recover failed" in s["recovery"]["exec"]["stderr_tail"]


def test_run_time_based_fault(tmp_path, mock_gateway):
    """Time-based fault (0.3s) with paced requests: the fault fires
    mid-run (not at a fixed index); the window opens and recovery is
    detected. The manifest records mode='time'."""
    q = _write_queries(tmp_path, 12)
    gw = mock_gateway
    # Fail everything from seq 3 onward for the first 0.6s, then healthy.
    # Simpler deterministic approach: fail a fixed set of seqs that we
    # know fall after the 0.3s mark. With rate=0.05s and ~ms latency,
    # seq 3 lands at ~0.15s, seq 4 at ~0.2s, seq 5 at ~0.25s, seq 6 at
    # ~0.3s -> the fault fires around seq 5-6.  To stay deterministic
    # regardless of exact timing, we fail ALL requests from seq 4 onward
    # except a trailing healthy block: the streak is K=3, so as long as
    # the last 3 requests succeed, recovery is detected no matter where
    # the window opened.
    gw["state"]["fail_seqs"] = set(range(4, 9))  # 4..8 fail; 0-3, 9-11 ok
    args = _args(tmp_path, gateway_url=gw["url"], queries=str(q),
                 fault_at="0.3s", rate=0.05, steady=False,
                 fault_command="FAKE-FAULT", recover_command="",
                 out_dir=str(tmp_path / "out"))
    rc = rq2.run_rq2(args, exec_run=_fake_run([], rc=0))
    assert rc == 0
    out = Path(args.out_dir)
    manifests = sorted(out.glob("rq2_*_manifest.json"))
    man = json.loads(manifests[0].read_text())
    assert man["fault"]["mode"] == "time"
    assert man["fault"]["seconds"] == 0.3
    jsonls = sorted(out.glob("rq2_*.jsonl"))
    recs = [json.loads(l) for l in jsonls[0].read_text().splitlines()]
    # recovery streak completed (last 3 all ok)
    assert recs[-1]["segment"] == "recovered"
    # the fault fired at SOME point: some records have fault_at set,
    # earlier ones don't
    fired = [r for r in recs if r["fault_at"] is not None]
    not_fired = [r for r in recs if r["fault_at"] is None]
    assert fired and not_fired
    # fault_at index is None for time-based faults
    assert all(f["fault_at"]["index"] is None for f in fired)
    summaries = sorted(out.glob("summary_*.json"))
    s = json.loads(summaries[0].read_text())
    assert s["fault"]["mode"] == "time"
    assert s["recovery"]["recovered_at_seq"] is not None


def test_run_dry_run_executes_nothing(tmp_path, monkeypatch):
    """--dry-run: prints the plan, sends NO HTTP request, executes NO
    command, and writes NO files.  (No gateway fixture needed: a dry run
    must never touch the network, so a bogus URL is fine.)"""
    q = _write_queries(tmp_path, 6)
    # guard: monkeypatch the driver's HTTP + exec so any call fails the test
    def boom(*a, **k):
        raise AssertionError("dry-run must not execute this")
    monkeypatch.setattr(rq2, "http_post_json", boom)
    monkeypatch.setattr(rq2, "exec_shell_command", boom)
    args = _args(tmp_path, gateway_url="http://127.0.0.1:1",
                 queries=str(q), fault_at="3",
                 fault_command="SHOULD-NOT-RUN",
                 recover_command="SHOULD-NOT-RUN-EITHER",
                 out_dir=str(tmp_path / "out"), dry_run=True)
    rc = rq2.run_rq2(args)
    assert rc == 0
    # out dir should not even exist (dry-run writes nothing)
    assert not Path(args.out_dir).exists()


def test_run_refuses_to_overwrite(tmp_path, mock_gateway, capsys):
    q = _write_queries(tmp_path, 3)
    out = tmp_path / "out"
    out.mkdir()
    # pre-create a JSONL with the same (timestamped) run id the driver
    # would generate right now
    import datetime as _dt
    rid = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"rq2_{rid}.jsonl").write_text('{"seq": 0}\n')
    args = _args(tmp_path, gateway_url=mock_gateway["url"], queries=str(q),
                 fault_at="1", out_dir=str(out))
    rc = rq2.run_rq2(args, exec_run=_fake_run([]))
    assert rc == 1
    err = capsys.readouterr().err
    assert "already exists" in err


def test_run_health_probe_during_fault(tmp_path, mock_gateway):
    """--probe-health polls during the fault window; the summary records
    probe counts and first-fail/first-ok offsets relative to the fault.
    The gateway's /health flips to 503 on the 3rd request (a
    request-counter trigger, not wall-clock timing) and stays down for
    the rest of the run."""
    q = _write_queries(tmp_path, 12)
    gw = mock_gateway
    gw["state"]["fail_seqs"] = {3, 4}        # query failures in the window
    gw["state"]["health_fail_after_n"] = 3   # /health down from req #3 on
    args = _args(tmp_path, gateway_url=gw["url"], queries=str(q),
                 fault_at="3", rate=0.05, steady=False,
                 fault_command="FAKE-FAULT", recover_command="",
                 probe_health=gw["url"] + "/health", probe_interval=0.02,
                 out_dir=str(tmp_path / "out"))
    rc = rq2.run_rq2(args, exec_run=_fake_run([]))
    assert rc == 0
    s = json.loads(sorted(Path(args.out_dir).glob("summary_*.json"))[0]
                  .read_text())
    assert s["probe"] is not None
    assert s["probe"]["n"] >= 1
    # the gateway stayed down for the rest of the run: every probe after
    # the flip failed, and the first probe after the flip is the first
    # recorded failure.
    assert s["probe"]["n_fail"] >= 1
    assert s["probe"]["first_fail_t"] is not None
    # first_ok_t: no probe ever saw a healthy response once down (the
    # very first poll at the fault moment may have raced ahead of the
    # flip, so only assert the fail side hard).
    if s["probe"]["first_ok_t"] is not None:
        assert s["probe"]["first_ok_t"] < s["probe"]["first_fail_t"]
    # offsets relative to the fault are present and sane
    fa = s["probe"]["firsts_after_t0"]
    assert fa is not None and fa["first_fail_s"] >= 0


def test_parse_fault_at_via_cli_rejects_bad(tmp_path, monkeypatch, capsys):
    """run_rq2 returns 1 (with a clear stderr) when --fault-at is invalid."""
    q = _write_queries(tmp_path, 5)
    args = _args(tmp_path, queries=str(q), fault_at="0")
    rc = rq2.run_rq2(args)
    assert rc == 1
    assert "no pre-fault baseline" in capsys.readouterr().err
    args = _args(tmp_path, queries=str(q), fault_at="99")
    rc = rq2.run_rq2(args)
    assert rc == 1
    assert "exceeds the query" in capsys.readouterr().err
    args = _args(tmp_path, queries=str(q), fault_at="garbage")
    rc = rq2.run_rq2(args)
    assert rc == 1
    assert "not a number" in capsys.readouterr().err
