#!/usr/bin/env python3
"""
RQ2 node-dropout driver (plan item AB-6; feeds the CM-3 dropout campaign).

Streams a steady request stream at a target gateway, fires a PLUGGABLE fault
command at a scheduled point (a request index, or a number of seconds into
the run), keeps streaming, records every request as a per-record JSONL
timing record (the same conventions as ``campaign.py``: manifest written
BEFORE any request, one JSON object per line, per-request
t_sent/t_resp/latency_ms/error), and finally fires an optional recovery
command.  "Recovery" is defined as the first ``--recover-streak`` (default
K=3) consecutive successful requests after the fault.

The driver is deliberately topology-agnostic: the fault and recovery are
shell command strings executed on the node the driver runs from (Node A in
the current testbed), and the target is any HTTP gateway.  The node
specifics are therefore NOT embedded here — they are supplied at launch
time.  Intended launch recipe (comments only, for the record):

  * D-B (Node B down; pipeline consolidates to A — design §2.2):
      - target the Node B hybrid gateway, e.g.
            --gateway-url http://10.8.0.2:8000
      - fault: kill the Node B gateway from Node A over SSH (PowerShell 7
        on B):
            --fault-command "ssh laptop 'schtasks /End /TN pdc_gateway'"
      - recover: bring it back up:
            --recover-command "ssh laptop 'schtasks /Run /TN pdc_gateway'"
        (gateway lifecycle per docs/NODE_ACCESS.md §2: schtasks
        End/Run of the ``pdc_gateway`` scheduled task; gateway returns in
        ~10 s, verify with ``http://10.8.0.2:8000/health``.)
  * D-C (Node C down; sparse leg fails over to B's local index):
      - target the user-facing Node C gateway:
            --gateway-url http://10.8.0.3:8000
      - fault: stop the Node C gateway from Node A:
            --fault-command "ssh friend-laptop 'schtasks /End /TN pdc_gateway_c'"
      - recover:
            --recover-command \
              "ssh friend-laptop 'schtasks /Change /TN pdc_gateway_c /ENABLE; schtasks /Run /TN pdc_gateway_c'"
        (the ``pdc_gateway_c`` scheduled task must be ENABLED first or
        ``/Run`` fails silently — docs/NODE_ACCESS.md §3; always use
        single-line commands when issuing commands into Node C.)
  * A dry (no-op) fault (``--fault-command ""``) is supported: the fault
    window still opens at ``--fault-at`` and the driver still measures
    recovery — useful for validating segment labeling / the K-streak
    logic against an always-healthy gateway.

Records: each JSONL line is
    {"run_id", "seq", "t_sent", "t_resp", "ok", "latency_ms",
     "segment": "pre_fault" | "during_fault" | "recovered",
     "query",
     "fault_at": null | {"t", "index", "elapsed_s"} (present on every
                    record from the fault point onward),
     "error": <string, only when !ok>}
with the fault window closing (segment flips to ``recovered``) at the
completion of the first K consecutive post-fault successes.

Outputs in ``--out-dir``:
  * ``rq2_<run-id>_manifest.json`` — written BEFORE any request (same
    manifest-first discipline as campaign.py).
  * ``rq2_<run-id>.jsonl``         — one line per request, flushed per line.
  * ``summary_<run-id>.json``      — per-segment counts/errors,
    time-to-first-success, time-to-recovery (K-streak), p50/p95 latency per
    segment (bare percentiles; the stats engine adds CIs later).

Verification fences: the unit tests (``test_rq2_driver.py``) use a local
threaded mock gateway and a monkeypatched fault executor — no live HTTP to
real nodes, no netem, no SSH.  ``--dry-run`` prints the plan (timeline,
fault point, commands exactly as recorded in the manifest) without
executing anything.
"""

import argparse
import json
import math
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCHEMA_MANIFEST = "pdc.rq2_driver_manifest/v1"
SCHEMA_SUMMARY = "pdc.rq2_summary/v1"
DESIGN_DOC = "docs/campaign_v2_design.md"
DEFAULT_OUT_DIR = "experiments/bench/rq2_runs"

# Segment labels (frozen; downstream analysis keys on these).
SEG_PRE = "pre_fault"
SEG_DURING = "during_fault"
SEG_RECOVERED = "recovered"

# Payload template POSTed to <gateway>/query/benchmark (the gateway's
# documented /query/benchmark contract per campaign.py; the driver only
# needs the gateway to respond — the payload is not node-specific).
DEFAULT_PAYLOAD = {"top_k": 10, "mode": "hybrid", "sphp": True}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def percentile(values: List[float], p: float) -> float:
    """Linear-interpolation percentile (p in 0..100) — same definition as
    campaign.percentile so the two drivers are comparable downstream."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n == 1:
        return float(s[0])
    idx = (p / 100.0) * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(s[lo])
    frac = idx - lo
    return float(s[lo] * (1.0 - frac) + s[hi] * frac)


def load_queries(path: str) -> List[str]:
    """Load a one-query-per-line text file, skipping blank lines
    (same convention as campaign.load_queries)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"queries file not found: {path}")
    queries: List[str] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.strip():
                queries.append(line)
    return queries


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="RQ2 node-dropout driver (AB-6 / CM-3): steady request "
                    "stream + pluggable fault/recover shell commands + "
                    "per-request JSONL with pre_fault/during_fault/"
                    "recovered segment labels.")
    ap.add_argument("--gateway-url", default="http://127.0.0.1:8000",
                    help="target gateway base URL (under test; e.g. "
                         "http://10.8.0.2:8000 for D-B or "
                         "http://10.8.0.3:8000 for D-C)")
    ap.add_argument("--queries", required=True, metavar="FILE",
                    help="one query per line (blank lines skipped)")
    ap.add_argument("--rate", type=float, default=None, metavar="SEC",
                    help="seconds to sleep between requests "
                    "(mutually exclusive with --steady)")
    ap.add_argument("--steady", action="store_true",
                    help="send requests back-to-back with no pacing delay "
                         "(mutually exclusive with --rate)")
    ap.add_argument("--fault-at", required=True, metavar="N|T",
                    help="when to fire the fault: an integer request index "
                         "(0-based) or a float seconds-into-the-run value "
                         "(e.g. '10' or '10.5').  Must not exceed the last "
                         "request index; index 0 is rejected (no pre-fault "
                         "baseline).")
    ap.add_argument("--fault-command", default="",
                    help="shell command executed exactly once at the fault "
                         "point (the actual node fault, e.g. an SSH "
                         "schtasks End).  Empty string = dry fault "
                         "(no command executed; the window still opens).")
    ap.add_argument("--recover-command", default="",
                    help="shell command executed exactly once after the "
                         "stream ends (the recovery, e.g. an SSH "
                         "schtasks Run).  Empty string = no recovery "
                         "command.")
    ap.add_argument("--probe-health", default=None, metavar="URL",
                    help="optional URL to poll (GET) at --probe-interval "
                         "during the fault window (e.g. the gateway's "
                         "/health); results are logged in the summary only")
    ap.add_argument("--probe-interval", type=float, default=1.0,
                    help="seconds between --probe-health polls (default: "
                         "1.0)")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="per-request HTTP timeout in seconds (default: "
                         "30.0)")
    ap.add_argument("--recover-streak", type=int, default=3,
                    help="K: number of consecutive successful post-fault "
                         "requests that defines recovery (default: 3)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, metavar="DIR",
                    help=f"output directory (default: {DEFAULT_OUT_DIR})")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan (timeline, fault point, commands "
                         "exactly as recorded in the manifest) without "
                         "executing any request or command")
    return ap


# ---------------------------------------------------------------------------
# Planning (pure — shared by dry-run and real execution)
# ---------------------------------------------------------------------------
def parse_fault_at(value: str, n_queries: int) -> Tuple[Optional[int],
                                                       Optional[float]]:
    """Parse ``--fault-at`` into ``(fault_index, fault_seconds)``.

    Integer text (e.g. "4") -> an index-based fault (fires before request
    index 4; 1-based request 5).  Float text (e.g. "10.5") or a non-integer
    float -> a time-based fault (seconds into the run, measured from before
    the first request is sent).

    Validation (raises ValueError): the value must be >= 0; an index-based
    fault at index 0 is rejected (no pre-fault baseline); an index-based
    fault must fall within the query range [1, n_queries] (firing at or
    after the last request would leave an empty fault window).
    """
    raw = value.strip()
    # An explicit time suffix forces time mode even for integer values
    # (bare "10" is an index, by convention; "10s" is seconds).
    suffix = None
    for sfx in ("secs", "sec", "s"):
        if raw.endswith(sfx):
            raw = raw[: -len(sfx)].strip()
            suffix = sfx
            break
    try:
        num = float(raw)
    except ValueError:
        raise ValueError(f"--fault-at {value!r} is not a number"
                         + (f" (after stripping suffix {suffix!r})"
                            if suffix else ""))
    if num < 0:
        raise ValueError(f"--fault-at must be >= 0 (got {value!r})")
    if suffix is not None:
        return None, num
    if raw.lstrip("-+").isdigit():
        idx = int(raw)
        if idx == 0:
            raise ValueError("--fault-at index 0 leaves no pre-fault "
                             "baseline; use a time-based value (e.g. '0') "
                             "or index >= 1")
        if idx > n_queries:
            raise ValueError(f"--fault-at index {idx} exceeds the query "
                             f"count ({n_queries})")
        return idx, None
    return None, num


def segment_plan(n_queries: int,
                 fault_index: Optional[int]) -> List[str]:
    """Per-request segment labels for an INDEX-based fault.

    Requests with seq < fault_index are ``pre_fault``; from the fault
    request onward they start as ``during_fault`` (the actual flip to
    ``recovered`` happens at runtime on the K-streak, so this plan only
    distinguishes pre from the fault window).  Time-based faults have
    ``fault_index=None``: the plan is all ``during_fault``-eligible
    (every request starts as ``during_fault``) because the fault point in
    request space is only known at runtime.
    """
    if fault_index is None:
        return [SEG_DURING] * n_queries
    return ([SEG_PRE] * fault_index +
            [SEG_DURING] * (n_queries - fault_index))


def build_manifest(run_id: str, args: argparse.Namespace,
                   queries: List[str],
                   fault_index: Optional[int],
                   fault_seconds: Optional[float],
                   endpoint: str) -> Dict[str, Any]:
    """Build the manifest document written BEFORE any request (the
    manifest-first discipline of campaign.py)."""
    return {
        "schema": SCHEMA_MANIFEST,
        "design_doc": DESIGN_DOC,
        "run_id": run_id,
        "generated_at_utc": _now_iso(),
        "gateway_url": args.gateway_url,
        "endpoint": endpoint,
        "queries_file": str(args.queries),
        "n_queries": len(queries),
        "pacing": ("steady" if args.steady else f"{args.rate}s"),
        "payload": dict(DEFAULT_PAYLOAD),
        "timeout_s": args.timeout,
        "fault": {
            "at": args.fault_at,
            "mode": ("index" if fault_index is not None else "time"),
            "index": fault_index,
            "seconds": fault_seconds,
            "command": args.fault_command,
            "dry": args.fault_command == "",
        },
        "recovery": {
            "command": args.recover_command,
            "recover_streak": args.recover_streak,
        },
        "probe": {
            "health_url": args.probe_health,
            "interval_s": args.probe_interval,
        },
        "dry_run": bool(args.dry_run),
    }


def format_plan(args: argparse.Namespace, manifest: Dict[str, Any],
                segments: List[str], n_queries: int) -> str:
    """Render the --dry-run plan: the timeline (per-request segment
    labels) plus the fault point and the commands exactly as recorded in
    the manifest."""
    f = manifest["fault"]
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("RQ2 DRIVER — DRY RUN (nothing will be executed)")
    lines.append("=" * 72)
    lines.append(f"gateway    : {manifest['gateway_url']}")
    lines.append(f"endpoint   : {manifest['endpoint']}")
    lines.append(f"queries    : {manifest['queries_file']} "
                 f"({n_queries} queries)")
    lines.append(f"pacing     : {manifest['pacing']}")
    lines.append(f"timeout    : {manifest['timeout_s']}s per request")
    at = f"request index {f['index']}" if f["mode"] == "index" else \
        f"{f['seconds']}s into the run"
    lines.append(f"fault at   : {at}")
    lines.append(f"fault cmd  : {f['command']!r} "
                 f"({'DRY — no command' if f['dry'] else 'executed once '
                   'at the fault point'})")
    lines.append(f"recover cmd: {manifest['recovery']['command']!r} "
                 f"({'none' if manifest['recovery']['command'] == ''
                   else 'executed once at end of run'})")
    lines.append(f"recover    : first K={manifest['recovery']['recover_streak']}"
                 " consecutive successful post-fault requests")
    lines.append(f"probe      : {manifest['probe']['health_url'] or '-'} "
                 f"every {manifest['probe']['interval_s']}s during the "
                 "fault window")
    lines.append("-" * 72)
    if f["mode"] == "index" and f["index"] is not None:
        lines.append("timeline   : (seq, segment)")
        for seq, seg in enumerate(segments):
            lines.append(f"  {seq:>6}  {seg}")
        lines.append(f"  {f['index']:>6}  <== fault fires before this request"
                     f" (recovery flips the segment to 'recovered' at the"
                     f" first K={manifest['recovery']['recover_streak']}"
                     " consecutive successes)")
    else:
        lines.append(f"timeline   : {n_queries} requests; the fault fires"
                     f" {f['seconds']}s into the run (wall clock), so the"
                     " pre/during boundary is decided at runtime; segment"
                     f" labels in the JSONL reflect the actual flip at the"
                     f" first K={manifest['recovery']['recover_streak']}"
                     " consecutive successes")
    lines.append("-" * 72)
    lines.append("manifest   : will be written before any request; "
                 "per-request records to rq2_<run-id>.jsonl; "
                 "summary to summary_<run-id>.json")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fault / recovery execution (pluggable)
# ---------------------------------------------------------------------------
def exec_shell_command(cmd: str,
                       run: Optional[Callable[[List[str]],
                                              "subprocess.CompletedProcess"]]
                       = None) -> Dict[str, Any]:
    """Execute a shell command exactly once via ``bash -c``.

    ``run`` is an injectable runner (for tests); default is
    ``subprocess.run``.  Returns ``{"command", "ok", "returncode",
    "stderr_tail", "exception"}``.  A non-zero exit code is a *logged*
    failure, not a raised one — the fault window still opens (the
    external fault may have partially taken effect), and a recovery
    failure is likewise just logged (the run's data is still valid).
    """
    if run is None:
        run = subprocess.run
    out: Dict[str, Any] = {
        "command": cmd, "ok": False, "returncode": None,
        "stderr_tail": None, "exception": None,
    }
    try:
        proc = run(["bash", "-c", cmd])
        out["returncode"] = proc.returncode
        out["ok"] = proc.returncode == 0
        if not out["ok"]:
            err = (getattr(proc, "stderr", "") or "")
            out["stderr_tail"] = err.strip()[-400:] or None
    except Exception as e:  # bash missing, timeout, ...
        out["exception"] = str(e)
    return out


# ---------------------------------------------------------------------------
# HTTP (same conventions as campaign.py: urllib, per-call timeout)
# ---------------------------------------------------------------------------
def http_post_json(url: str, payload: Dict[str, Any],
                   timeout: float) -> Dict[str, Any]:
    """POST JSON to ``url``; return the parsed response (raises on any
    HTTP/transport failure)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_health(url: str, timeout: float = 5.0) -> bool:
    """GET ``url``; True iff HTTP 200 (any other outcome: False)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Recovery / summary (pure)
# ---------------------------------------------------------------------------
def recovery_streak(reponses: List[Dict[str, Any]],
                    streak: int) -> Optional[int]:
    """Return the seq of the request that COMPLETES the first ``streak``
    consecutive successful post-fault responses, or None.

    ``reponses`` is the list of post-fault records (in order) with keys
    ``seq`` and ``ok``.  A failure resets the running count.  ``streak``
    must be >= 1.
    """
    if streak < 1:
        raise ValueError(f"streak must be >= 1 (got {streak})")
    count = 0
    for rec in reponses:
        if rec.get("ok"):
            count += 1
            if count == streak:
                return rec["seq"]
        else:
            count = 0
    return None


def summarize_run(records: List[Dict[str, Any]],
                  fault_event: Optional[Dict[str, Any]],
                  recovered_at_seq: Optional[int],
                  streak: int,
                  probes: List[Dict[str, Any]],
                  fault_exec: Optional[Dict[str, Any]],
                  recover_exec: Optional[Dict[str, Any]],
                  manifest: Dict[str, Any],
                  exit_status: str,
                  abort_reason: Optional[str] = None,
                  ) -> Dict[str, Any]:
    """Compute the end-of-run summary document from the in-memory records.

    Segments: ``pre_fault`` = seq < fault seq; ``recovered`` = seq >=
    completed-streak seq (when recovery happened); everything in between
    (and everything post-fault when no recovery happened) =
    ``during_fault``.
    """
    per_segment: Dict[str, Dict[str, Any]] = {}

    def _seg(name: str) -> Dict[str, Any]:
        if name not in per_segment:
            per_segment[name] = {"n": 0, "errors": 0, "latencies": []}
        return per_segment[name]

    for r in records:
        if r["segment"] == SEG_PRE:
            name = SEG_PRE
        elif recovered_at_seq is not None and r["seq"] >= recovered_at_seq:
            name = SEG_RECOVERED
        else:
            name = SEG_DURING
        s = _seg(name)
        s["n"] += 1
        if r.get("error"):
            s["errors"] += 1
        s["latencies"].append(r.get("latency_ms") or 0.0)

    segments: Dict[str, Any] = {}
    for name in (SEG_PRE, SEG_DURING, SEG_RECOVERED):
        s = per_segment.get(name)
        if s is None:
            continue
        lats = s["latencies"]
        segments[name] = {
            "n": s["n"],
            "errors": s["errors"],
            "p50_ms": round(percentile(lats, 50), 3),
            "p95_ms": round(percentile(lats, 95), 3),
        }

    # Time-to-first-success: first post-fault success, relative to the
    # fault event (index-based) or to run start (time-based).
    t0 = (fault_event or {}).get("t")
    if t0 is None and records:
        # Time-based fault events carry no wall-clock t in the event dict
        # here only when constructed externally; fall back to the run
        # start = the first record's send time.
        t0 = records[0].get("t_sent")
    first_success_seq = None
    time_to_first_success_s = None
    for r in records:
        if r["segment"] == SEG_PRE or not r.get("ok"):
            continue
        first_success_seq = r["seq"]
        if t0 is not None and r.get("t_sent") is not None:
            time_to_first_success_s = round(
                r["t_sent"] - t0, 6)
        break

    # Time-to-recovery: end of the K-streak, relative to the fault event.
    time_to_recovery_s = None
    if recovered_at_seq is not None:
        rec = next(r for r in records if r["seq"] == recovered_at_seq)
        if t0 is not None and rec.get("t_resp") is not None:
            time_to_recovery_s = round(rec["t_resp"] - t0, 6)

    probes_out = None
    if probes:
        n_fail = sum(1 for p in probes if not p["ok"])
        first_fail = next((p["t"] for p in probes if not p["ok"]), None)
        first_ok = next((p["t"] for p in probes if p["ok"]), None)
        probes_out = {
            "n": len(probes),
            "n_fail": n_fail,
            "first_fail_t": first_fail,
            "first_ok_t": first_ok,
            "firsts_after_t0": None,
        }
        if t0 is not None:
            if first_fail is not None:
                probes_out["firsts_after_t0"] = {
                    "first_fail_s": round(first_fail - t0, 6),
                    "first_ok_s": (round(first_ok - t0, 6)
                                   if first_ok is not None else None),
                }

    return {
        "schema": SCHEMA_SUMMARY,
        "design_doc": DESIGN_DOC,
        "run_id": manifest["run_id"],
        "generated_at_utc": _now_iso(),
        "status": exit_status,
        "abort_reason": abort_reason,
        "gateway_url": manifest["gateway_url"],
        "n_queries": manifest["n_queries"],
        "pacing": manifest["pacing"],
        "fault": {
            "at": manifest["fault"]["at"],
            "mode": manifest["fault"]["mode"],
            "t": (fault_event or {}).get("t"),
            "index": (fault_event or {}).get("index"),
            "exec": fault_exec,
            "dry": manifest["fault"]["dry"],
        },
        "recovery": {
            "recover_streak": streak,
            "recovered_at_seq": recovered_at_seq,
            "exec": recover_exec,
        },
        "time_to_first_success_s": time_to_first_success_s,
        "time_to_first_success_at_seq": first_success_seq,
        "time_to_recovery_s": time_to_recovery_s,
        "segments": segments,
        "probe": probes_out,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class _Abort(Exception):
    """Internal: stop the stream (data up to here stays valid)."""


def run_rq2(args: argparse.Namespace,
            exec_run: Optional[Callable[[List[str]],
                                        "subprocess.CompletedProcess"]]
            = None) -> int:
    """Execute the RQ2 run.  Returns the process exit code.

    ``exec_run`` is an injectable command runner for tests (default:
    ``subprocess.run``); it is handed to :func:`exec_shell_command`.
    """
    queries = load_queries(args.queries)
    if not queries:
        print(f"ERROR: queries file is empty: {args.queries}", file=sys.stderr)
        return 1
    try:
        fault_index, fault_seconds = parse_fault_at(args.fault_at, len(queries))
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.recover_streak < 1:
        print(f"ERROR: --recover-streak must be >= 1 (got "
              f"{args.recover_streak})", file=sys.stderr)
        return 1

    endpoint = args.gateway_url.rstrip("/") + "/query/benchmark"
    run_id = _run_id()
    manifest = build_manifest(run_id, args, queries, fault_index,
                              fault_seconds, endpoint)
    plan_segments = segment_plan(len(queries), fault_index)

    if args.dry_run:
        print(format_plan(args, manifest, plan_segments, len(queries)))
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"rq2_{run_id}.jsonl"
    if jsonl_path.exists():
        print(f"ERROR: {jsonl_path} already exists (refusing to overwrite "
              f"a previous run).", file=sys.stderr)
        return 1
    # Manifest FIRST (campaign.py discipline): the plan is on disk before
    # any request is sent.
    manifest_path = out_dir / f"rq2_{run_id}_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest written: {manifest_path}")
    print(f"Gateway target  : {args.gateway_url} "
          f"({len(queries)} queries, pacing={manifest['pacing']}, "
          f"fault at {args.fault_at!r}, K={args.recover_streak})")

    jsonl_f = open(jsonl_path, "w", encoding="utf-8")
    records: List[Dict[str, Any]] = []
    probes: List[Dict[str, Any]] = []
    fault_event: Optional[Dict[str, Any]] = None
    fault_exec: Optional[Dict[str, Any]] = None
    recover_exec: Optional[Dict[str, Any]] = None
    recovered_at_seq: Optional[int] = None
    streak_count = 0
    exit_status = "completed"
    abort_reason: Optional[str] = None
    t_start = time.time()
    try:
        last_probe_t = 0.0
        for seq, query in enumerate(queries):
            now = time.time()
            # --- fire the fault at the scheduled point (once) ---
            fire = False
            if fault_index is not None:
                fire = seq == fault_index
            elif fault_seconds is not None:
                fire = (now - t_start) >= fault_seconds
            if fault_event is None and fire:
                # ``t`` is always a wall-clock timestamp: the fire time
                # for index faults, the t_start anchor for time faults
                # (it is what the summary diffs against).
                event_t = now if fault_index is not None else t_start
                fault_event = {
                    "t": event_t,
                    "index": (seq if fault_index is not None else None),
                    "elapsed_s": round(now - t_start, 6),
                    "at": args.fault_at,
                    "mode": "index" if fault_index is not None else "time",
                }
                if args.fault_command:
                    fault_exec = exec_shell_command(args.fault_command,
                                                    run=exec_run)
                    print(f"[fault] fired at seq={seq} "
                          f"({'index' if fault_index is not None else 'time'}"
                          f"={args.fault_at}): ok={fault_exec['ok']} "
                          f"rc={fault_exec['returncode']} "
                          f"cmd={args.fault_command!r}")
                else:
                    fault_exec = {"command": "", "ok": True,
                                  "returncode": 0, "stderr_tail": None,
                                  "exception": None}
                    print(f"[fault] dry fault at seq={seq} "
                          f"(no command; window opens)")
            # --- health probe during the fault window ---
            if (fault_event is not None and recovered_at_seq is None
                    and args.probe_health
                    and (now - last_probe_t) >= args.probe_interval):
                last_probe_t = now
                probe = {"t": now, "ok": False, "detail": None}
                try:
                    probe["ok"] = check_health(args.probe_health,
                                               timeout=args.timeout)
                except Exception as e:
                    probe["detail"] = str(e)
                probes.append(probe)
            # --- the request itself (always recorded) ---
            t_sent = time.time()
            ok = True
            error: Optional[str] = None
            try:
                http_post_json(endpoint, dict(DEFAULT_PAYLOAD),
                               timeout=args.timeout)
            except Exception as e:
                ok = False
                error = str(e)
            t_resp = time.time()
            # --- segment labeling ---
            if fault_event is None:
                seg = SEG_PRE
            elif recovered_at_seq is not None:
                seg = SEG_RECOVERED
            else:
                seg = SEG_DURING
                if ok:
                    streak_count += 1
                    if streak_count >= args.recover_streak:
                        recovered_at_seq = seq
                        seg = SEG_RECOVERED
                        print(f"[recover] K={args.recover_streak}-streak "
                              f"completed at seq={seq} "
                              f"(t={t_resp - t_start:.3f}s)")
                else:
                    streak_count = 0
            rec = {
                "run_id": run_id,
                "seq": seq,
                "t_sent": t_sent,
                "t_resp": t_resp,
                "ok": ok,
                "latency_ms": round((t_resp - t_sent) * 1000.0, 3),
                "segment": seg,
                "query": query,
                "fault_at": ({"t": fault_event["t"],
                              "index": fault_event.get("index"),
                              "elapsed_s": fault_event["elapsed_s"]}
                             if fault_event else None),
            }
            if error is not None:
                rec["error"] = error
            records.append(rec)
            jsonl_f.write(json.dumps(rec) + "\n")
            jsonl_f.flush()
            # --- pacing ---
            if not args.steady and args.rate is not None:
                time.sleep(args.rate)
    except _Abort as e:
        abort_reason = str(e)
        exit_status = "aborted"
    finally:
        jsonl_f.close()

    # --- recovery command: exactly once, after the stream ends ---
    if args.recover_command:
        recover_exec = exec_shell_command(args.recover_command,
                                          run=exec_run)
        print(f"[recover] end-of-run command ok={recover_exec['ok']} "
              f"rc={recover_exec['returncode']} "
              f"cmd={args.recover_command!r}")

    summary = summarize_run(records, fault_event, recovered_at_seq,
                            args.recover_streak, probes, fault_exec,
                            recover_exec, manifest, exit_status,
                            abort_reason)
    summary_path = out_dir / f"summary_{run_id}.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\nJSONL   : {jsonl_path}")
    print(f"Summary : {summary_path}")
    segs = summary["segments"]
    print(f"segments: "
          + "  ".join(f"{k}: n={v['n']} err={v['errors']}"
                      for k, v in segs.items()))
    print(f"tt-first-success: "
          f"{summary['time_to_first_success_s']}s (seq="
          f"{summary['time_to_first_success_at_seq']})")
    print(f"tt-recovery(K={args.recover_streak}): "
          f"{summary['time_to_recovery_s']}s "
          f"(seq={summary['recovery']['recovered_at_seq']})")
    if abort_reason:
        print(f"ABORTED: {abort_reason}", file=sys.stderr)
    print("Done.")
    return 0 if exit_status == "completed" else 1


def main() -> None:
    args = build_parser().parse_args()
    if args.rate is not None and args.steady:
        print("ERROR: --rate and --steady are mutually exclusive",
              file=sys.stderr)
        sys.exit(2)
    if args.rate is None and not args.steady:
        print("ERROR: pass exactly one of --rate SEC or --steady",
              file=sys.stderr)
        sys.exit(2)
    if args.rate is not None and args.rate < 0:
        print("ERROR: --rate must be >= 0", file=sys.stderr)
        sys.exit(2)
    sys.exit(run_rq2(args))


if __name__ == "__main__":
    main()
