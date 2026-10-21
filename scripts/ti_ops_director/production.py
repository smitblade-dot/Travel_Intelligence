"""03AA-04_Production_Controller_TI: production verification for Travel Intelligence.

Detects delayed, missed and failed production work, checks that the published data is valid, fresh and
complete, verifies that the public site serves the release on ``main``, and keeps one deduplicated
diagnostic or recovery task per open exception. Ordinary code: no model call and no spend.

It is the ``production_verification`` worker of the Master Agent (Ops Director), not a second controller.
It never writes ``data.json``, never publishes, and never edits another workflow. The only action it can
take is a bounded ``workflow_dispatch`` of an allow-listed workflow, which is off unless Marc enables it.

A check that cannot run (API or site unreachable) is reported UNVERIFIED and never read as healthy; the
open tasks it covers are kept, not resolved.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from . import BUSINESS_APPLICATION, SUBSTREAM
from .monitor_state import FileStateBackend, GitStateBackend, Snapshot, StateSyncError, dump_state
from .sanitise import clean_text, redact_text
from .scheduling import display, last_daily_slot

AGENT_NAME = "03AA-04_Production_Controller_TI"
CONFIG_FILE = Path("config") / "production_controller.json"
STATE_PATHS = {"state": "production_controller_state.json", "report": "production_controller_report.md"}
MODES = ("off", "observe", "active")
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
CATEGORIES = ("SECURITY", "ENTRY", "HEALTH", "EMERGENCY", "TRANSPORT", "ENVIRONMENT", "INSURANCE", "LAWS_CULTURE",
              "COMMUNICATIONS", "FINANCE", "LANGUAGE", "ACCOMMODATION", "EQUIPMENT", "TRAINING")
COUNTED = ("countries", "records", "sources", "locations", "events", "sourceObservations")
FAILED_CONCLUSIONS = ("failure", "timed_out", "cancelled", "startup_failure", "action_required", "stale")
LEGIT_EVENTS = ("schedule", "workflow_dispatch")
EXIT_OK, EXIT_NEW_CRITICAL, EXIT_CONFIG, EXIT_STATE = 0, 1, 3, 4


class Unavailable(RuntimeError):
    """A source of evidence could not be read. The affected check is UNVERIFIED, never healthy."""


def parse_ts(value):
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def minutes(delta):
    return round(delta.total_seconds() / 60, 1)


# ---- configuration -----------------------------------------------------------------------------------------
@dataclass
class PCConfig:
    mode: str = "off"
    mode_source: str = "unset"
    repository: str = ""
    branch: str = "main"
    timezone: str = "Europe/Nicosia"
    workflows: list = field(default_factory=list)
    output: dict = field(default_factory=dict)
    release: dict = field(default_factory=dict)
    controller: dict = field(default_factory=dict)
    state_branch: Optional[str] = None
    recovery_workflows: tuple = ()
    max_dispatches_per_day: int = 1
    token_present: bool = False
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def site_url(self):
        return self.release.get("site_url", "")


def load_pc_config(env=None, root=None, path=None):
    """Thresholds come from the committed config file; switches from TI_PC_* variables. Fails closed."""
    env = os.environ if env is None else env
    root = Path(root or Path(__file__).resolve().parents[2])
    raw = json.loads(Path(path or root / CONFIG_FILE).read_text(encoding="utf-8"))
    cfg = PCConfig(repository=env.get("GITHUB_REPOSITORY") or raw["repository"], branch=raw.get("branch", "main"),
                   timezone=raw.get("timezone", "Europe/Nicosia"), workflows=raw["workflows"],
                   output=raw["output"], release=raw["release"], controller=raw["controller"])
    cfg.max_dispatches_per_day = int(raw["controller"].get("max_dispatches_per_workflow_per_day", 1))

    mode = (env.get("TI_PC_MODE") or "").strip().lower()
    if not mode:
        # A manual run with no mode set is one read-only observation; schedules stay inert.
        cfg.mode, cfg.mode_source = ("observe", "manual_default") if env.get("TI_PC_EVENT") == "workflow_dispatch" \
            else ("off", "unset")
    elif mode in MODES:
        cfg.mode, cfg.mode_source = mode, "TI_PC_MODE"
    else:
        cfg.mode, cfg.mode_source = "off", "invalid"
        cfg.errors.append("TI_PC_MODE must be off, observe or active; the controller stays off")

    if (env.get("TI_PC_SITE_URL") or "").strip():
        cfg.release = dict(cfg.release, site_url=env["TI_PC_SITE_URL"].strip())
    if urlsplit(cfg.site_url).scheme != "https":
        cfg.errors.append("release.site_url must be an https URL")
    cfg.state_branch = (env.get("TI_PC_STATE_BRANCH") or "").strip() or None
    if cfg.state_branch in ("main", cfg.branch):
        cfg.errors.append("TI_PC_STATE_BRANCH must not be the production branch (every push would redeploy Pages)")
        cfg.state_branch = None
    if cfg.mode == "active" and not cfg.state_branch and (env.get("TI_PC_STATE_BACKEND") or "git") == "git":
        cfg.errors.append("TI_PC_MODE=active needs TI_PC_STATE_BRANCH; without persistent state tasks cannot be deduplicated")

    allowed = {w["file"]: w for w in cfg.workflows}
    names = [n.strip() for n in (env.get("TI_PC_RECOVERY_WORKFLOWS") or "").split(",") if n.strip()]
    for n in names:
        if n not in allowed or not allowed[n].get("dispatchable"):
            cfg.warnings.append(f"TI_PC_RECOVERY_WORKFLOWS: {n} is not a dispatchable monitored workflow; ignored")
    cfg.recovery_workflows = tuple(n for n in names if n in allowed and allowed[n].get("dispatchable"))
    if (env.get("TI_PC_MAX_DISPATCHES_PER_DAY") or "").strip():
        try:
            cfg.max_dispatches_per_day = int(env["TI_PC_MAX_DISPATCHES_PER_DAY"])
        except ValueError:
            cfg.errors.append("TI_PC_MAX_DISPATCHES_PER_DAY must be a whole number")
            cfg.max_dispatches_per_day = 0
    cfg.max_dispatches_per_day = max(0, min(cfg.max_dispatches_per_day, 3))   # hard ceiling in code
    cfg.token_present = bool(env.get("GITHUB_TOKEN"))
    return cfg


# ---- evidence sources --------------------------------------------------------------------------------------
@dataclass
class HttpReply:
    status: Optional[int]
    body: bytes
    error: Optional[str] = None
    headers: dict = field(default_factory=dict)


def http_request(url, *, headers=None, data=None, method="GET", timeout=20, max_bytes=20_000_000):
    """Bounded request. Network errors are returned, not raised; the caller decides what UNVERIFIED means."""
    req = Request(url, data=data, method=method, headers={"User-Agent": "TI-Production-Controller/1.0",
                                                         **(headers or {})})
    try:
        with urlopen(req, timeout=timeout) as r:
            return HttpReply(r.status, r.read(max_bytes), None, dict(r.headers))
    except HTTPError as exc:
        return HttpReply(exc.code, exc.read(4000) if hasattr(exc, "read") else b"", "http_error")
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return HttpReply(None, b"", "network_error: " + clean_text(reason, 120))


class GitHubAPI:
    """Read-only REST calls (plus workflow dispatch). The token is never logged or stored."""
    BASE = "https://api.github.com"

    def __init__(self, repository, token, http=http_request):
        self.repo, self._token, self._http = repository, token, http

    def _headers(self, accept="application/vnd.github+json"):
        h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            h["Authorization"] = "Bearer " + self._token
        return h

    def get(self, path, accept="application/vnd.github+json"):
        r = self._http(self.BASE + path, headers=self._headers(accept))
        if r.status != 200:
            raise Unavailable(f"GitHub API {path.split('?')[0]}: {r.status or r.error}")
        return r.body if accept.endswith("raw") else json.loads(r.body)

    def workflow_runs(self, workflow, branch, per_page=40):
        return self.get(f"/repos/{self.repo}/actions/workflows/{quote(workflow)}/runs?branch={quote(branch)}"
                        f"&per_page={per_page}")["workflow_runs"]

    def run_jobs(self, run_id):
        return self.get(f"/repos/{self.repo}/actions/runs/{run_id}/jobs?per_page=50")["jobs"]

    def branch_head(self, branch):
        c = self.get(f"/repos/{self.repo}/commits/{quote(branch)}")
        return c["sha"], parse_ts(c["commit"]["committer"]["date"])

    def pages_latest(self):
        return self.get(f"/repos/{self.repo}/pages/builds/latest")

    def file_at(self, path, ref):
        return self.get(f"/repos/{self.repo}/contents/{quote(path)}?ref={quote(ref)}",
                        accept="application/vnd.github.raw")

    def dispatch(self, workflow, ref):
        r = self._http(self.BASE + f"/repos/{self.repo}/actions/workflows/{quote(workflow)}/dispatches",
                       headers=self._headers(), data=json.dumps({"ref": ref}).encode(), method="POST")
        return r.status == 204, r.status or r.error


# ---- findings ----------------------------------------------------------------------------------------------
def finding(key, severity, check, summary, action, evidence=None, dispatch=None, requires_human=True):
    """One exception. ``key`` is the deduplication subject; ``dispatch`` names a workflow a re-run may fix."""
    return {"key": key, "severity": severity, "check": check, "summary": clean_text(summary, 400),
            "action": clean_text(action, 400), "evidence": evidence or {}, "dispatch": dispatch,
            "requires_human": requires_human}


@dataclass
class CheckResult:
    name: str
    covers: tuple                       # key prefixes this check evaluated (open tasks under them may resolve)
    findings: list = field(default_factory=list)
    status: str = "OK"                  # OK | EXCEPTION | UNVERIFIED
    detail: str = ""
    facts: dict = field(default_factory=dict)

    def done(self):
        if self.status != "UNVERIFIED":
            self.status = "EXCEPTION" if any(f["severity"] != "info" for f in self.findings) else "OK"
        return self


def _run_summary(run):
    return {"run_id": run.get("id"), "event": run.get("event"), "status": run.get("status"),
            "conclusion": run.get("conclusion"), "created_utc": run.get("created_at"),
            "url": run.get("html_url"), "head_sha": (run.get("head_sha") or "")[:12]}


def _classify_completed(api, spec, run):
    """(kind, severity, detail) for a completed run, from its jobs and steps where that matters."""
    conclusion = run.get("conclusion")
    if conclusion == "success" and not spec.get("required_job"):
        return None
    jobs = api.run_jobs(run["id"])
    if conclusion == "success":
        job = next((j for j in jobs if j.get("name") == spec["required_job"]), None)
        if job is None or job.get("conclusion") == "skipped":
            return ("gate_skipped", "critical",
                    f"run concluded success but job '{spec['required_job']}' did not run (gate skipped the work)")
        return None
    by_step = {}
    for j in jobs:
        for s in j.get("steps") or []:
            if s.get("conclusion") in FAILED_CONCLUSIONS:
                by_step.setdefault(s.get("name"), []).append(j.get("name"))
    # Matrix jobs fail the same step many times: name it once with the number of jobs.
    failed_steps = [f"'{step}' in {len(js)} jobs" if len(js) > 1 else f"{js[0]}/{step}"
                    for step, js in by_step.items()][:4]
    out = spec.get("output_step")
    if out:
        out_ok = any(s.get("name") == out and s.get("conclusion") == "success"
                     for j in jobs for s in (j.get("steps") or []))
        if out_ok:
            return ("post_output_failure", "warning",
                    f"output was committed ('{out}' succeeded) but a later step failed: {', '.join(failed_steps) or conclusion}")
    return ("failed", "critical", f"run {conclusion}; failed steps: {', '.join(failed_steps) or 'none reported'}")


def _workflow_actions(kind, spec):
    label = spec["label"]
    return {
        "missed": f"No {label} run for the expected slot. Check that the workflow is enabled and its schedule "
                  f"is still registered (Actions tab), then dispatch it manually if the output is needed.",
        "delayed": f"{label} started late or has not started yet. GitHub schedules are best effort; no action "
                   f"unless this repeats or becomes 'missed'.",
        "stalled": f"{label} has run longer than its limit. Inspect the run; cancel it if it is hung.",
        "failed": f"{label} failed before producing output. Read the failed step's log, fix the cause, then re-run.",
        "post_output_failure": f"{label} committed its output but a later step failed. The data is published; "
                               f"read the failed step (often the discovery feed audit) and fix the cause.",
        "gate_skipped": f"{label} ran but skipped its work. Check the schedule gate against the cron entry.",
        "under_delivery": f"{label} is running far less often than scheduled. GitHub is dropping or delaying "
                          f"scheduled runs; consider a shorter-interval trigger elsewhere or accept the gap.",
    }[kind]


def check_workflow(api, spec, now):
    """Delayed, missed, stalled and failed runs for one monitored workflow on the production branch."""
    res = CheckResult("workflow:" + spec["id"], covers=(f"workflow:{spec['id']}:",))
    try:
        runs = [r for r in api.workflow_runs(spec["file"], "main") if r.get("event") in LEGIT_EVENTS]
    except Unavailable as exc:
        res.status, res.detail = "UNVERIFIED", str(exc)
        return res
    runs.sort(key=lambda r: r["created_at"], reverse=True)
    sched = spec["schedule"]
    key = lambda kind: f"workflow:{spec['id']}:{kind}"   # noqa: E731
    dispatch = spec["file"] if spec.get("dispatchable") else None

    def add(kind, severity, summary, evidence, can_dispatch=False):
        res.findings.append(finding(key(kind), severity, res.name, summary, _workflow_actions(kind, spec),
                                    evidence, dispatch if can_dispatch else None))

    try:
        if sched["kind"] == "daily":
            _check_daily(api, spec, runs, now, add, res)
        else:
            _check_interval(api, spec, runs, now, add, res)
    except Unavailable as exc:
        res.status, res.detail = "UNVERIFIED", str(exc)
    return res.done()


def _check_daily(api, spec, runs, now, add, res):
    sched = spec["schedule"]
    slot = last_daily_slot(now, sched["local_time"], sched["timezone"])
    window = lambda s: [r for r in runs if s - timedelta(minutes=10) <= parse_ts(r["created_at"])  # noqa: E731
                        < s + timedelta(days=1) - timedelta(minutes=10)]
    in_slot = window(slot)
    if not in_slot and now - slot < timedelta(minutes=spec["start_late_minutes"]):
        slot = slot - timedelta(days=1)    # today's slot is still within its grace: judge the previous one
        in_slot = window(slot)
    res.facts = {"slot_utc": iso(slot), "slot_local": display(slot, sched["timezone"]), "runs_in_slot": len(in_slot)}
    age = now - slot
    if not in_slot:
        ev = {"slot_utc": iso(slot), "minutes_since_slot": minutes(age),
              "last_run": _run_summary(runs[0]) if runs else None}
        if age >= timedelta(minutes=spec["missed_after_minutes"]):
            add("missed", "critical", f"{spec['label']}: no run for the {display(slot, sched['timezone'])} slot "
                                      f"after {minutes(age)} minutes", ev, can_dispatch=True)
        elif age >= timedelta(minutes=spec["start_late_minutes"]):
            add("delayed", "warning", f"{spec['label']}: not started {minutes(age)} minutes after its slot", ev)
        return
    first, latest = in_slot[-1], in_slot[0]
    start_delay = parse_ts(first["created_at"]) - slot
    res.facts.update(start_delay_minutes=minutes(start_delay), latest=_run_summary(latest))
    if start_delay > timedelta(minutes=spec["start_late_minutes"]):
        add("delayed", "warning", f"{spec['label']}: started {minutes(start_delay)} minutes after the "
                                  f"{display(slot, sched['timezone'])} slot", {"first_run": _run_summary(first)})
    _judge_latest(api, spec, latest, now, add)


def _check_interval(api, spec, runs, now, add, res):
    every = spec["schedule"]["minutes"]
    done = [r for r in runs if r.get("status") == "completed"]
    ok = next((r for r in done if r.get("conclusion") == "success"), None)
    since_ok = now - parse_ts(ok["created_at"]) if ok else None
    win_h = spec.get("coverage_window_hours", 24)
    start = now - timedelta(hours=win_h)
    recent = [r for r in runs if parse_ts(r["created_at"]) >= start]
    observed_from = max(start, parse_ts(runs[-1]["created_at"])) if runs else start
    expected = max(1, int((now - observed_from).total_seconds() // (every * 60)))
    res.facts = {"last_success_utc": ok and ok["created_at"],
                 "minutes_since_success": since_ok and minutes(since_ok),
                 "runs_in_window": len(recent), "expected_in_window": expected, "window_hours": win_h,
                 "latest": _run_summary(runs[0]) if runs else None}
    ev = {"last_success": ok and _run_summary(ok), "minutes_since_success": since_ok and minutes(since_ok)}
    if since_ok is None or since_ok >= timedelta(minutes=spec["missed_after_minutes"]):
        add("missed", "critical", f"{spec['label']}: no successful run for "
                                  f"{'ever' if since_ok is None else str(minutes(since_ok)) + ' minutes'} "
                                  f"(scheduled every {every} minutes)", ev, can_dispatch=True)
    elif since_ok >= timedelta(minutes=spec["late_after_minutes"]):
        add("delayed", "warning", f"{spec['label']}: last successful run {minutes(since_ok)} minutes ago "
                                  f"(scheduled every {every} minutes)", ev)
    ratio = len(recent) / expected
    if expected >= 4 and ratio < spec.get("coverage_warning_ratio", 0.5):
        add("under_delivery", "warning", f"{spec['label']}: {len(recent)} runs in the last {win_h} h against "
                                         f"{expected} scheduled ({round(100 * ratio)} percent)",
            {"runs": len(recent), "expected": expected, "observed_from_utc": iso(observed_from)})
    if runs:
        _judge_latest(api, spec, runs[0], now, add)


def _judge_latest(api, spec, run, now, add):
    if run.get("status") != "completed":
        started = parse_ts(run.get("run_started_at") or run["created_at"])
        if now - started > timedelta(minutes=spec["max_runtime_minutes"]):
            add("stalled", "critical", f"{spec['label']}: run {run.get('id')} still {run.get('status')} after "
                                       f"{minutes(now - started)} minutes", {"run": _run_summary(run)})
        return
    verdict = _classify_completed(api, spec, run)
    if verdict:
        kind, severity, detail = verdict
        add(kind, severity, f"{spec['label']}: {detail}", {"run": _run_summary(run)},
            can_dispatch=kind in ("failed", "gate_skipped"))


# ---- outputs -----------------------------------------------------------------------------------------------
def check_outputs(root, cfg, now, baseline=None, validator=None):
    """Validity, freshness and completeness of the published data in the checkout of the production branch."""
    root, o = Path(root), cfg.output
    res = CheckResult("outputs", covers=("output:",))
    add = lambda key, sev, summary, action, ev=None: res.findings.append(  # noqa: E731
        finding("output:" + key, sev, "outputs", summary, action, ev))
    try:
        data = json.loads((root / "data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        add("unreadable", "critical", f"data.json cannot be read: {clean_text(exc, 160)}",
            "Restore the last valid data.json from Git history and re-run the refresh.")
        return res.done()
    ok, tail = (validator or run_validator)(root)
    if not ok:
        add("invalid", "critical", "data.json fails scripts/validate_ti_data.py", "Fix the data-model error, "
            "validate locally, and commit; the public site is serving an invalid dataset.", {"validator": tail})

    meta = data.get("meta") or {}
    gen = None
    try:
        gen = parse_ts(meta.get("generated"))
    except ValueError:
        pass
    if gen is None:
        add("generated_missing", "critical", "meta.generated is missing or not a timestamp",
            "The refresh must set meta.generated; check the 'Synchronise public intelligence metadata' step.")
    else:
        age_h = round((now - gen).total_seconds() / 3600, 1)
        res.facts["generated_utc"], res.facts["age_hours"] = iso(gen), age_h
        if age_h >= o["stale_critical_hours"]:
            add("stale", "critical", f"Published data is {age_h} hours old (meta.generated {iso(gen)}); at least "
                "two daily refreshes have not produced output", "Check the daily refresh exceptions and re-run it.",
                {"generated_utc": iso(gen), "age_hours": age_h})
        elif age_h >= o["stale_warning_hours"]:
            add("stale", "warning", f"Published data is {age_h} hours old (meta.generated {iso(gen)}); the last "
                "daily refresh has not produced output", "Check the daily refresh exceptions.",
                {"generated_utc": iso(gen), "age_hours": age_h})

    counts = {k: len(data.get(k) or []) for k in COUNTED}
    res.facts["counts"] = counts
    declared = meta.get("counts") or {}
    wrong = {k: {"declared": declared.get(k), "actual": v} for k, v in counts.items() if declared.get(k) != v}
    published = [e for e in data.get("events") or [] if e.get("publicationStatus") == "PUBLISHED"]
    ids = {e.get("id") for e in published}
    ci_actual = {"events": len(published),
                 "sourceObservations": sum(1 for s in data.get("sourceObservations") or [] if s.get("eventId") in ids)}
    ci_declared = meta.get("currentIntelligence") or {}
    wrong.update({f"currentIntelligence.{k}": {"declared": ci_declared.get(k), "actual": v}
                  for k, v in ci_actual.items() if ci_declared.get(k) != v})
    if wrong:
        add("counts", "critical", f"meta counts disagree with the data: {', '.join(sorted(wrong))}",
            "Re-run the metadata synchronisation; the public counters are wrong.", {"mismatch": wrong})

    low = {k: {"minimum": m, "actual": counts.get(k)} for k, m in (o.get("minimum_counts") or {}).items()
           if counts.get(k, 0) < m}
    if baseline:
        for k, before in baseline.items():
            if before and counts.get(k, 0) < before * (1 - o["shrink_critical_ratio"]):
                low[k] = {"previous": before, "actual": counts.get(k)}
    if low:
        add("shrink", "critical", f"Implausibly small dataset: {', '.join(sorted(low))}", "Compare data.json "
            "with the previous commit; restore lost collections before the next refresh builds on them.", low)
    if not data.get("oilGasSummary"):
        add("oilgas_missing", "critical", "oilGasSummary is empty or missing", "Re-run scripts/refresh_oilgas.py; "
            "it must preserve the previous summary on failure, so an empty one is a defect.")

    active = {(str(r.get("countryId")).lower(), r.get("category")) for r in data.get("records") or []
              if r.get("status", "ACTIVE") == "ACTIVE"}
    total = len(data.get("countries") or []) * len(CATEGORIES)
    gaps = sum(1 for c in data.get("countries") or [] for k in CATEGORIES if (str(c.get("id")).lower(), k) not in active)
    res.facts["baseline_coverage"] = {"covered": total - gaps, "total": total}
    if gaps:
        add("baseline_gaps", "warning", f"Baseline coverage {total - gaps}/{total}: {gaps} country/category "
            "combinations have no sourced baseline record (CLAUDE.md: a build defect)",
            "Repair the baseline build (see its workflow exceptions) so it fills the gaps with sourced records.",
            {"covered": total - gaps, "total": total})

    sig = root / "data" / "phase1_signals.json"
    try:
        sig_gen = parse_ts(json.loads(sig.read_text(encoding="utf-8")).get("generated"))
        age_h = round((now - sig_gen).total_seconds() / 3600, 1)
        if age_h >= o["discovery_stale_hours"]:
            add("discovery_stale", "warning", f"Discovery evidence (data/phase1_signals.json) is {age_h} hours old",
                "The daily refresh's collection step has not run; see the daily refresh exceptions.",
                {"generated_utc": iso(sig_gen)})
    except (OSError, ValueError, TypeError, AttributeError):
        add("discovery_missing", "warning", "data/phase1_signals.json is missing or unreadable",
            "Re-run the daily refresh collection step.")
    res.findings += check_monitor_state(root, o, now)
    return res.done()


def check_monitor_state(root, o, now):
    """Spending-gateway outcomes of the Haiku monitor, read from its committed state (no paid call)."""
    path = Path(root) / "data" / "continuous_monitor_state.json"
    if not path.exists():
        return []
    st = json.loads(path.read_text(encoding="utf-8"))
    last = st.get("lastOutcome") or {}
    triage, out = last.get("triage"), []
    ev = {"triage": triage, "reason": last.get("reason"), "at": last.get("at"),
          "consecutive_untriaged": st.get("consecutiveUntriaged", 0), "consecutive_failed": st.get("consecutiveFailed", 0),
          "limits": (last.get("spend") or {}).get("limits")}
    mk = lambda key, sev, summary, action: out.append(  # noqa: E731
        finding("output:monitor_" + key, sev, "outputs", summary, action, ev))
    if triage == "NOT_RUN_NOT_CONFIGURED":
        mk("not_configured", "critical", f"Haiku triage cannot run: configuration defect ({last.get('reason')})",
           "Fix the named TI_OPS_* variable or key (docs/TI_OPS_DIRECTOR.md, Production transition).")
    elif triage == "FAILED" and st.get("consecutiveFailed", 0) >= o["monitor_failed_runs_critical"]:
        mk("failing", "critical", f"Haiku triage failed {st['consecutiveFailed']} runs in a row ({last.get('reason')})",
           "Read the monitor run summary; provider error, invalid output or state sync failure.")
    elif triage == "NOT_RUN_BUDGET_EXHAUSTED":
        mk("budget", "warning", f"Haiku triage stopped by a spending control ({last.get('reason')}); headlines are kept",
           "Expected when a limit is reached. Raise TI_OPS_* limits only as a deliberate decision.")
    elif triage == "NOT_RUN_DRY_RUN" and st.get("consecutiveUntriaged", 0) >= o["monitor_untriaged_runs_info"]:
        mk("dry_run", "info", f"Haiku triage is not running: gateway in dry-run, {st.get('consecutiveUntriaged')} "
           "runs with new headlines untriaged", "Configuration choice, not a fault: paid triage starts only when "
           "Marc sets TI_OPS_DRY_RUN=false and the three TI_OPS_* USD limits.")
    stale = [r for r in (st.get("spend") or {}).get("open") or []
             if r.get("at") and now - parse_ts(r["at"]) > timedelta(minutes=o["abandoned_reservation_minutes"])]
    if stale:
        mk("abandoned_reservations", "warning", f"{len(stale)} spend reservation(s) unsettled for more than "
           f"{o['abandoned_reservation_minutes']} minutes (charged in full; a run was killed)",
           "No action needed for the limits; check why the monitor run was killed.")
    return out


def run_validator(root):
    p = subprocess.run([sys.executable, str(Path(root) / "scripts" / "validate_ti_data.py")], cwd=str(root),
                       capture_output=True, text=True, timeout=300)
    return p.returncode == 0, clean_text("\n".join((p.stdout + p.stderr).strip().splitlines()[-5:]), 800)


# ---- release -----------------------------------------------------------------------------------------------
def check_release(api, cfg, now, http=http_request):
    """Pages built the head of main, and the public site serves exactly those files."""
    rel = cfg.release
    res = CheckResult("release", covers=("release:",))
    add = lambda key, sev, summary, action, ev=None: res.findings.append(  # noqa: E731
        finding("release:" + key, sev, "release", summary, action, ev))
    try:
        head, head_at = api.branch_head(cfg.branch)
        pages = api.pages_latest()
    except Unavailable as exc:
        res.status, res.detail = "UNVERIFIED", str(exc)
        return res
    built, status = pages.get("commit"), pages.get("status")
    built_at = parse_ts(pages.get("updated_at") or pages.get("created_at"))
    res.facts = {"main_head": head[:12], "main_head_utc": iso(head_at), "pages_commit": (built or "")[:12],
                 "pages_status": status, "pages_updated_utc": iso(built_at)}
    if status == "errored":
        add("build_failed", "critical", f"GitHub Pages build errored for {(built or '')[:12]}: "
            f"{clean_text((pages.get('error') or {}).get('message'), 200)}",
            "Open the 'pages build and deployment' run; the public site keeps serving the previous release.",
            {"pages": res.facts})
    if built != head and now - head_at > timedelta(minutes=rel["deploy_grace_minutes"]):
        add("not_deployed", "critical", f"Main is at {head[:12]} but Pages last built {(built or 'nothing')[:12]}, "
            f"{minutes(now - head_at)} minutes after the commit", "Check Settings > Pages and the 'pages build and "
            "deployment' runs; re-run the latest build if it was dropped.", {"pages": res.facts})

    base = rel["site_url"].rstrip("/") + "/"
    stamp = now.strftime("%Y%m%d%H%M%S")
    served = {}
    for name in rel["files"]:
        r = http(f"{base}{name}?pc={stamp}", headers={"Cache-Control": "no-cache"})
        if r.status != 200:
            add("unreachable", "critical", f"Public site did not serve {name}: {r.status or r.error}",
                "Check the domain, DNS and Pages status; the public cannot see the release.",
                {"url": base + name, "status": r.status, "error": r.error})
            continue
        served[name] = r.body
    if "data.json" in served:
        try:
            pub_meta = json.loads(served["data.json"]).get("meta") or {}
            res.facts["public_generated"] = pub_meta.get("generated")
        except ValueError:
            add("public_invalid", "critical", "Public data.json is not valid JSON",
                "The site is serving a corrupt dataset; check the Pages build of the current commit.")
    for name, needles in (rel.get("required_text") or {}).items():
        if name in served:
            text = served[name].decode("utf-8", "replace")
            missing = [n for n in needles if n not in text]
            if missing:
                add("legal_text", "critical", f"Public {name} lacks required text: {'; '.join(missing)}",
                    "Restore the exact legal footer (CLAUDE.md) and redeploy.")
    if built and served:
        mismatched = {}
        for name, body in served.items():
            try:
                expected = api.file_at(name, built)
            except Unavailable as exc:
                res.detail = f"could not read {name} at {built[:12]}: {exc}"
                continue
            got, want = hashlib.sha256(body).hexdigest(), hashlib.sha256(expected).hexdigest()
            if got != want:
                mismatched[name] = {"served_sha256": got[:16], "release_sha256": want[:16]}
        res.facts["files_match_release"] = not mismatched
        if mismatched:
            settled = built_at and now - built_at > timedelta(minutes=rel["propagation_grace_minutes"])
            add("stale_content", "critical" if settled else "info",
                f"Public site serves different {', '.join(sorted(mismatched))} from the Pages release "
                f"{built[:12]}" + ("" if settled else " (still inside the CDN propagation window)"),
                "Wait for the CDN, then check whether Pages is serving an older deployment or another branch.",
                {"files": mismatched, "pages_commit": built[:12]})
    return res.done()


# ---- controller self-check ---------------------------------------------------------------------------------
def check_self(state, cfg, now):
    res = CheckResult("controller", covers=("controller:",))
    last = parse_ts(state.get("lastRun")) if state.get("lastRun") else None
    gap = cfg.controller["self_gap_warning_minutes"]
    if last and now - last > timedelta(minutes=gap):
        res.findings.append(finding("controller:gap", "warning", "controller",
                                    f"The production controller itself did not run for {minutes(now - last)} minutes "
                                    f"(expected every {cfg.controller['expected_interval_minutes']})",
                                    "GitHub delayed or dropped the controller's schedule; exceptions in that gap "
                                    "were detected late.", {"previous_run_utc": iso(last)}))
    return res.done()


# ---- task ledger (deduplication) ---------------------------------------------------------------------------
def _task_id(key, opened):
    return "pc-" + hashlib.sha256(f"{key}|{opened}".encode()).hexdigest()[:12]


def reconcile(state, results, now, cfg):
    """Open, update, escalate and resolve tasks. One open task per dedupe key; history is kept, not overwritten."""
    tasks = [dict(t) for t in state.get("tasks") or []]
    open_by_key = {t["dedupe_key"]: t for t in tasks if t["state"] == "blocked"}
    seen, delta = set(), {"opened": [], "escalated": [], "resolved": [], "ongoing": []}
    for res in results:
        if res.status == "UNVERIFIED":
            continue
        for f in res.findings:
            seen.add(f["key"])
            t = open_by_key.get(f["key"])
            if t is None:
                t = {"id": _task_id(f["key"], iso(now)), "dedupe_key": f["key"],
                     "task_type": "production_recovery" if f["dispatch"] else "production_diagnostic",
                     "worker": "production_recovery", "business_application": BUSINESS_APPLICATION,
                     "substream": SUBSTREAM, "check": f["check"], "state": "blocked",
                     "first_seen_utc": iso(now), "occurrences": 0, "dispatches": 0}
                tasks.append(t)
                open_by_key[f["key"]] = t
                delta["opened"].append(t["id"])
            elif SEVERITY_RANK[f["severity"]] > SEVERITY_RANK[t["severity"]]:
                delta["escalated"].append(t["id"])
            else:
                delta["ongoing"].append(t["id"])
            t.update(severity=f["severity"], summary=f["summary"], blocking_reason=f["action"],
                     requires_human=f["requires_human"], evidence=json.loads(redact_text(json.dumps(f["evidence"]))),
                     dispatch_workflow=f["dispatch"], last_seen_utc=iso(now), occurrences=t["occurrences"] + 1)
    covered = tuple(p for r in results if r.status != "UNVERIFIED" for p in r.covers)
    for t in tasks:
        if t["state"] == "blocked" and t["dedupe_key"] not in seen and t["dedupe_key"].startswith(covered):
            t.update(state="completed", resolved_utc=iso(now), resolution="condition no longer observed")
            delta["resolved"].append(t["id"])
    keep_after = now - timedelta(days=cfg.controller["retention_days"])
    tasks = [t for t in tasks if t["state"] == "blocked" or parse_ts(t.get("resolved_utc")) > keep_after]
    return tasks, delta


def plan_dispatches(state, tasks, cfg, now, active_workflows=()):
    """Bounded recovery: allow-listed workflows only, capped per workflow per UTC day, never while one runs."""
    if cfg.mode != "active" or not cfg.recovery_workflows or cfg.max_dispatches_per_day < 1:
        return [], list(state.get("dispatches") or [])
    day = now.strftime("%Y-%m-%d")
    history = [d for d in state.get("dispatches") or [] if parse_ts(d["at"]) > now - timedelta(days=7)]
    planned = []
    for t in tasks:
        wf = t.get("dispatch_workflow")
        if t["state"] != "blocked" or t["severity"] != "critical" or wf not in cfg.recovery_workflows:
            continue
        if wf in active_workflows or any(p["workflow"] == wf for p in planned):
            continue
        if sum(1 for d in history if d["workflow"] == wf and d["day"] == day) >= cfg.max_dispatches_per_day:
            continue
        entry = {"workflow": wf, "ref": cfg.branch, "task": t["id"], "reason": t["dedupe_key"], "at": iso(now), "day": day}
        planned.append(entry)
        history.append(entry)
        t["dispatches"] = t.get("dispatches", 0) + 1
    return planned, history


# ---- orchestration -----------------------------------------------------------------------------------------
def run_checks(cfg, api, root, now, state=None, http=http_request, validator=None):
    """Every read-only check against current production. No state is written here."""
    state = state or {}
    results = [check_workflow(api, spec, now) for spec in cfg.workflows]
    results.append(check_outputs(root, cfg, now, baseline=state.get("outputBaseline"), validator=validator))
    results += [check_release(api, cfg, now, http=http), check_self(state, cfg, now)]
    return results


def evaluate(cfg, api, root, now, state=None, http=http_request, validator=None):
    results = run_checks(cfg, api, root, now, state, http, validator)
    return (results,) + apply(state, results, cfg, now)


def apply(state, results, cfg, now):
    """Reconcile check results into the task ledger. Re-applied to the latest state on a compare-and-swap retry."""
    state = dict(state or {})
    out = next(r for r in results if r.name == "outputs")
    tasks, delta = reconcile(state, results, now, cfg)
    running = tuple(spec["file"] for spec, r in zip(cfg.workflows, results)
                    if any(f["key"].endswith(":stalled") for f in r.findings)
                    or (r.facts.get("latest") or {}).get("status") in ("queued", "in_progress", "waiting"))
    planned, dispatches = plan_dispatches(state, tasks, cfg, now, running)
    counts = out.facts.get("counts")
    healthy_output = out.status == "OK" or not any(f["severity"] == "critical" for f in out.findings)
    new_state = dict(state, schemaVersion=1, agent=AGENT_NAME, tasks=tasks, dispatches=dispatches,
                     lastRun=iso(now), lastMode=cfg.mode,
                     outputBaseline=counts if counts and healthy_output else state.get("outputBaseline"))
    history = list(state.get("runs") or [])
    history.append({"at": iso(now), "mode": cfg.mode, "verdict": verdict(results, tasks),
                    "opened": len(delta["opened"]), "escalated": len(delta["escalated"]),
                    "resolved": len(delta["resolved"]), "dispatches": len(planned),
                    "unverified": [r.name for r in results if r.status == "UNVERIFIED"]})
    new_state["runs"] = history[-cfg.controller["run_history"]:]
    return new_state, delta, planned


# ---- persistence and entry point ---------------------------------------------------------------------------
class BranchStateBackend(GitStateBackend):
    """Compare-and-swap commits to the controller's own state branch. It holds only controller files and is
    created as an orphan branch on first use, so state commits never touch main or trigger a Pages build.
    With ``create=False`` (observe mode) it is strictly read-only: a missing branch reads as no state."""

    def __init__(self, *a, create=True, **kw):
        super().__init__(*a, **kw)
        self.create = create

    def _exists(self):
        return bool(self.git("ls-remote", "--heads", self.remote, self.branch).stdout.strip())

    def snapshot(self):
        if not self.create and not self._exists():
            return Snapshot(lambda key: None)
        return super().snapshot()

    def transact(self, fn, message=""):
        if not self.create:
            raise StateSyncError("read-only state backend (observe mode) refuses to write")
        return super().transact(fn, message)

    def _fetch(self):
        if self.create and not self._exists():
            ident = {"GIT_AUTHOR_NAME": "Travel Intelligence Bot", "GIT_COMMITTER_NAME": "Travel Intelligence Bot",
                     "GIT_AUTHOR_EMAIL": "actions@users.noreply.github.com",
                     "GIT_COMMITTER_EMAIL": "actions@users.noreply.github.com"}
            empty = self.git("mktree", input_text="").stdout.strip()
            first = self.git("commit-tree", empty, "-m", f"{AGENT_NAME} state branch", env=ident).stdout.strip()
            # Rejected harmlessly if a concurrent run created the branch first; the fetch below then reads it.
            self.git("push", "--quiet", self.remote, f"{first}:refs/heads/{self.branch}", check=False)
        return super()._fetch()


def make_pc_backend(env, root, cfg):
    """git (Actions): TI_PC_STATE_BRANCH, never main. file: local, correct on one host only. None: no state."""
    choice = (env.get("TI_PC_STATE_BACKEND") or "git").strip().lower()
    if choice == "file":
        base = Path(env.get("TI_PC_STATE_DIR") or Path(root) / "var" / "production_controller")
        return FileStateBackend({k: base / v for k, v in STATE_PATHS.items()})
    if choice != "git":
        cfg.errors.append("TI_PC_STATE_BACKEND must be 'git' or 'file'")
        return None
    if not cfg.state_branch:
        return None
    return BranchStateBackend(root, STATE_PATHS, branch=cfg.state_branch, mirror_worktree=False,
                              create=cfg.mode == "active")


def _summary(env, text):
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text)


def run(env=None, root=None, data_root=None, now=None, api=None, http=http_request, backend=None,
        validator=None, out=print):
    """One controller pass. Returns the process exit code (see EXIT_*)."""
    env = os.environ if env is None else env
    root = Path(root or Path(__file__).resolve().parents[2])
    cfg = load_pc_config(env, root)
    if cfg.mode == "off":
        msg = (f"{AGENT_NAME}: OFF ({cfg.mode_source}). No checks run. Set TI_PC_MODE to observe or active "
               f"to enable.\n" + "".join(f"\nERROR: {e}" for e in cfg.errors))
        out(msg)
        _summary(env, "## Production controller\n\n" + msg + "\n")
        return EXIT_CONFIG if cfg.errors else EXIT_OK
    now = now or datetime.now(timezone.utc)
    if not cfg.token_present and api is None:
        cfg.warnings.append("GITHUB_TOKEN is not set: GitHub API checks run unauthenticated and may be rate limited")
    api = api or GitHubAPI(cfg.repository, env.get("GITHUB_TOKEN"), http)
    if backend is None:
        backend = make_pc_backend(env, root, cfg)
    try:
        state = backend.snapshot().state() if backend else {}
    except StateSyncError as exc:
        out(f"::error title=Production controller state::{exc}")
        return EXIT_STATE
    results = run_checks(cfg, api, data_root or root, now, state, http, validator)
    if cfg.mode == "active" and not cfg.errors:
        def fn(snap):
            new_state, delta, planned = apply(snap.state(), results, cfg, now)
            report = render_report(cfg, results, new_state, delta, planned, now)
            return {"state": dump_state(new_state), "report": report}, (new_state, delta, planned, report)
        try:
            _, (new_state, delta, planned, report) = backend.transact(fn, message=f"{AGENT_NAME}: {iso(now)}")
        except StateSyncError as exc:
            out(f"::error title=Production controller state::{exc}")
            return EXIT_STATE
    else:
        new_state, delta, planned = apply(state, results, cfg, now)
        report = render_report(cfg, results, new_state, delta, planned, now, persisted=False, prior=state)
    out(report)
    for line in annotations(results, new_state, delta, cfg):
        out(line)
    _summary(env, report)
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"dispatches={json.dumps([{'workflow': p['workflow'], 'ref': p['ref']} for p in planned])}\n")
            fh.write(f"verdict={verdict(results, new_state['tasks'])}\n")
    return exit_code(cfg, new_state, delta)


def dispatch_commands(dispatches_json, cfg):
    """Turn the check job's planned dispatches into ``gh`` commands. Re-checks the allow-list and the branch,
    so a malformed or tampered output can only ever dispatch an allow-listed workflow on the production branch."""
    try:
        planned = json.loads(dispatches_json or "[]")
    except ValueError:
        raise ValueError("dispatches output is not JSON") from None
    if not isinstance(planned, list):
        raise ValueError("dispatches output must be a list")
    dispatchable = {w["file"] for w in cfg.workflows if w.get("dispatchable")}
    cmds = []
    for d in planned:
        wf, ref = (d or {}).get("workflow"), (d or {}).get("ref")
        if wf not in dispatchable or wf not in cfg.recovery_workflows or ref != cfg.branch:
            raise ValueError(f"refused dispatch of {wf!r} on {ref!r}: not allow-listed for {cfg.branch}")
        cmds.append(["gh", "workflow", "run", wf, "--repo", cfg.repository, "--ref", ref])
    return cmds


def run_dispatch(env=None, root=None, runner=subprocess.run, out=print):
    """Recovery job entry point. Exit 0 when every planned dispatch was accepted; 3 when one was refused."""
    env = os.environ if env is None else env
    cfg = load_pc_config(dict(env, TI_PC_MODE="active", TI_PC_STATE_BACKEND="file"),
                         root or Path(__file__).resolve().parents[2])
    try:
        cmds = dispatch_commands(env.get("TI_PC_DISPATCHES"), cfg)
    except ValueError as exc:
        out(f"::error title=Production recovery refused::{exc}")
        return EXIT_CONFIG
    failed = 0
    for cmd in cmds:
        out(f"Dispatching {cmd[3]} on {cmd[-1]}")
        if runner(cmd).returncode != 0:
            failed += 1
            out(f"::error title=Production recovery dispatch failed::{cmd[3]}")
    return EXIT_STATE if failed else EXIT_OK


# ---- Master Agent queue integration ------------------------------------------------------------------------
def sync_queue(store, state, delta):
    """Mirror the ledger into the Ops Director queue: one blocked task per open exception, cancelled on resolution."""
    from .store import fingerprint, task_dedupe_key
    by_pc = {}
    for q in store.list_tasks(("pending", "blocked")):
        pc = json.loads(q["inputs"]).get("pc_task")
        if pc:
            by_pc[pc] = q["id"]
    created = []
    for t in state["tasks"]:
        if t["state"] == "blocked":
            inputs = {"pc_task": t["id"], "dedupe_key": t["dedupe_key"]}
            fp = fingerprint(inputs)
            tid, new = store.create_task(
                worker="production_recovery", task_type=t["task_type"], inputs=inputs,
                dedupe_key=task_dedupe_key(t["task_type"], t["dedupe_key"], fp), business_application=BUSINESS_APPLICATION,
                substream=SUBSTREAM, priority=90 if t["severity"] == "critical" else 60, input_fingerprint=fp)
            store.set_blocked(tid, f"{t['severity']}: {t['summary']} | action: {t['blocking_reason']}",
                              requires_human=True)
            if new:
                created.append(tid)
        elif t["id"] in by_pc:
            store.cancel(by_pc[t["id"]], "condition no longer observed by the production controller")
    return created


def verdict(results, tasks):
    open_ = [t for t in tasks if t["state"] == "blocked"]
    if any(t["severity"] == "critical" for t in open_):
        return "FAILING"
    if any(r.status == "UNVERIFIED" for r in results):
        return "UNVERIFIED"
    if any(t["severity"] == "warning" for t in open_):
        return "DEGRADED"
    return "OK"


def render_report(cfg, results, state, delta, planned, now, persisted=True, prior=None):
    """``persisted`` False (observe): the run recorded nothing, so the report says what was detected and how it
    relates to the ledger it read (``prior``), and never claims a task, incident or dispatch was created."""
    tasks = state["tasks"]
    open_ = sorted((t for t in tasks if t["state"] == "blocked"),
                   key=lambda t: (-SEVERITY_RANK[t["severity"]], t["first_seen_utc"]))
    v = verdict(results, tasks)
    L = [f"# Production report: {AGENT_NAME}", "",
         f"FORGEDTITANIUM LTD | 03 Agents & Automation | {SUBSTREAM} | Business application: {BUSINESS_APPLICATION}",
         "", f"Generated {iso(now)} UTC ({display(now, cfg.timezone)}). Mode: **{cfg.mode.upper()}** "
         f"({cfg.mode_source}). Recovery dispatch: "
         f"{'allowed for ' + ', '.join(cfg.recovery_workflows) if cfg.recovery_workflows and cfg.mode == 'active' else 'off'}.",
         "", f"## Verdict: {v}", "",
         "OK = every check ran and found no exception. UNVERIFIED = a check could not run; that is not health.", "",
         "| Check | Status | Facts |", "|---|---|---|"]
    for r in results:
        facts = r.detail if r.status == "UNVERIFIED" else ", ".join(
            f"{k}={json.dumps(val, default=str)}" for k, val in r.facts.items()
            if not isinstance(val, dict) or k in ("counts", "baseline_coverage"))
        L.append(f"| {r.name} | {r.status} | {clean_text(facts, 300).replace('|', '/')} |")
    if not persisted:
        return "\n".join(L + _observed(open_, prior, delta, cfg)) + "\n"
    L += ["", f"## Open exceptions ({len(open_)})", ""]
    if open_:
        L += ["| Severity | Since (UTC) | Seen | Exception | Action | Task |", "|---|---|---|---|---|---|"]
        for t in open_:
            L.append(f"| {t['severity'].upper()} | {t['first_seen_utc']} | {t['occurrences']} | "
                     f"{t['summary'].replace('|', '/')} | {t['blocking_reason'].replace('|', '/')} | {t['id']} |")
    else:
        L.append("None.")
    by_id = {t["id"]: t for t in tasks}
    L += ["", "## Changes this run", ""]
    for kind in ("opened", "escalated", "resolved"):
        items = [f"{by_id[i]['dedupe_key']} ({i})" for i in delta[kind] if i in by_id]
        L.append(f"- {kind.capitalize()}: {', '.join(items) if items else 'none'}")
    L += ["", "## Recovery", ""]
    L += ([f"- Dispatch {p['workflow']} on {p['ref']} for {p['reason']} (task {p['task']})" for p in planned]
          or ["- No dispatch this run."])
    if cfg.warnings or cfg.errors:
        L += ["", "## Configuration", ""] + [f"- ERROR: {e}" for e in cfg.errors] + [f"- {w}" for w in cfg.warnings]
    return "\n".join(L) + "\n"


def _observed(found, prior, delta, cfg):
    ledger = {t["dedupe_key"]: t for t in (prior or {}).get("tasks") or [] if t["state"] == "blocked"}
    have_ledger = bool((prior or {}).get("lastRun"))
    L = ["", f"## Exceptions detected by this run ({len(found)})", "",
         "**Observe mode: nothing was written.** These exceptions were detected by this run only. No task, "
         "incident, state change or dispatch was recorded, so no task identifiers exist for new detections.", ""]
    if found:
        L += ["| Severity | Exception | Action | Persisted ledger |", "|---|---|---|---|"]
        for t in found:
            known = ledger.get(t["dedupe_key"])
            status = (f"open since {known['first_seen_utc']} ({known['id']})" if known
                      else "not recorded (observe mode)")
            L.append(f"| {t['severity'].upper()} | {t['summary'].replace('|', '/')} | "
                     f"{t['blocking_reason'].replace('|', '/')} | {status} |")
    else:
        L.append("None.")
    L += ["", "## Ledger", ""]
    if have_ledger:
        L.append(f"- Read the persisted ledger (last written {prior['lastRun']}). Not changed by this run: it would "
                 f"have opened {len(delta['opened'])}, escalated {len(delta['escalated'])} and resolved "
                 f"{len(delta['resolved'])}.")
    else:
        L.append("- No persisted ledger was read (TI_PC_STATE_BRANCH unset, or the branch does not exist until "
                 "the first active run). Nothing was created.")
    L += ["", "## Recovery", "", "- Off in observe mode: nothing is dispatched."]
    if cfg.warnings or cfg.errors:
        L += ["", "## Configuration", ""] + [f"- ERROR: {e}" for e in cfg.errors] + [f"- {w}" for w in cfg.warnings]
    return L


def annotations(results, state, delta, cfg):
    """GitHub annotations: new and escalated exceptions only, so a standing fault does not repeat every hour."""
    by_id = {t["id"]: t for t in state["tasks"]}
    lines = [f"::error title=Production controller configuration::{e}" for e in cfg.errors]
    lines += [f"::warning title=Production controller configuration::{w}" for w in cfg.warnings]
    fresh = delta["opened"] + delta["escalated"]
    for i in fresh:
        t = by_id.get(i)
        if t and t["severity"] != "info":
            level = "error" if t["severity"] == "critical" else "warning"
            tag = "" if cfg.mode == "active" else " (observe: detected, not recorded)"
            lines.append(f"::{level} title=Production {t['severity']}: {t['check']}{tag}::{t['summary']}")
    for r in results:
        if r.status == "UNVERIFIED":
            lines.append(f"::warning title=Production check UNVERIFIED: {r.name}::{clean_text(r.detail, 200)}")
    return lines


def exit_code(cfg, state, delta):
    if cfg.errors:
        return EXIT_CONFIG
    if cfg.mode != "active":
        return EXIT_OK
    by_id = {t["id"]: t for t in state["tasks"]}
    fresh = [by_id[i] for i in delta["opened"] + delta["escalated"] if i in by_id]
    return EXIT_NEW_CRITICAL if any(t["severity"] == "critical" for t in fresh) else EXIT_OK
