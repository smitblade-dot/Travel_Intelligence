"""Report a 03AA-04 agent run to the FORGEDTITANIUM Agent Command Centre (ACC).

Optional and fail-safe. It runs as its own workflow step, only when the repository variable
ACC_REPORTING_ENABLED is "true", and always exits 0 so it can never change a run's result. It reads the
agent's own outcome record (scripts/ti_ops_director/agent_outcome.py), maps it to ACC event schema 1.1, signs
it with the agent's ACC key (HMAC-SHA256) and posts it. It reports agent activity only: no workflow, job or
repository monitoring.

  python3 scripts/acc_report.py start --agent <code>
  python3 scripts/acc_report.py end --agent <code> --outcome-file <path> --step-outcome <success|failure|cancelled>

Environment: ACC_REPORT_URL, ACC_KEY_ID, ACC_AGENT_SECRET (Actions secret), optional ACC_EUR_PER_USD and
ACC_EUR_RATE_DATE for converting reported USD model spend. GITHUB_RUN_ID / GITHUB_RUN_ATTEMPT give each event a
stable id so retries are not double counted. Standard library only.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

MASTER = "03AA-04_Master_Agent_TI_Ops_Director"
BUILDER = "03AA-04_Country_Intelligence_Builder_TI"
CONTROLLER = "03AA-04_Production_Controller_TI"
AGENTS = {MASTER: "Master Agent (gated Haiku monitor)", BUILDER: "Country Intelligence Builder", CONTROLLER: "Production Controller"}
PATH = "/v1/events"
TIMEOUT_S = 8


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now_ms():
    """Event time with milliseconds, so a fast run's start and end events never share a timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def run_ref(env):
    rid = env.get("GITHUB_RUN_ID")
    return f"gh-run-{rid}-{env.get('GITHUB_RUN_ATTEMPT', '1')}" if rid else None


def tier_from_model(model):
    m = (model or "").lower()
    for t in ("opus", "sonnet", "haiku"):
        if t in m:
            return t
    return None


def cost_fields(usd, env):
    """Real model spend only. Zero calls = EUR 0. USD is converted only with a configured rate; otherwise the
    USD estimate is passed through and the ACC sees no EUR figure rather than an invented one."""
    if not usd:
        return {"cost_eur": 0.0, "cost_basis": "api"}
    out = {"cost_usd_estimated": round(float(usd), 6), "cost_basis": "api"}
    rate = env.get("ACC_EUR_PER_USD")
    try:
        r = float(rate) if rate else None
    except ValueError:
        r = None
    if r and r > 0:
        out["cost_eur"] = round(float(usd) * r, 6)
        out["cost_note"] = f"estimated from USD at {r} EUR/USD ({env.get('ACC_EUR_RATE_DATE') or 'rate date not stated'})"
    return out


def start_event(agent, env):
    trig = env.get("GITHUB_EVENT_NAME") or "manual"
    return {"status": "working", "task": f"{AGENTS[agent]} run started ({trig})"}


def _no_outcome(step_outcome):
    if step_outcome in ("failure", "cancelled"):
        return {"status": "failed", "task": f"Run ended ({step_outcome}) without an outcome record from the agent"}
    return {"status": "unknown", "task": "Run ended without an outcome record from the agent"}


def map_master(rec, step_outcome, env):
    o = (rec or {}).get("outcome")
    if not o:
        e = _no_outcome(step_outcome)
        if rec and rec.get("error"):
            e["task"] = f"Monitor failed ({rec['error']}) before recording an outcome"
            e["status"] = "failed"
        return e
    code = rec.get("exit_code")
    triage, reason = o.get("triage"), o.get("reason")
    h = o.get("headlines") or {}
    spend = o.get("spend") or {}
    calls = o.get("calls") or 0
    ev = {"dry_run": spend.get("mode") == "dry_run" if spend.get("mode") in ("dry_run", "live") else None,
          "model_tier": tier_from_model(o.get("model")) if calls else "code"}
    if ev["dry_run"] is None:
        del ev["dry_run"]
    if ev["model_tier"] is None:
        del ev["model_tier"]
    ev.update(cost_fields(o.get("estimatedUsd") if calls else 0, env))
    base = f"Gated Haiku monitor: {h.get('collected', 0)} collected, {h.get('new', 0)} new"
    if triage == "NOT_RUN_NOT_CONFIGURED" or code == 3:
        ev.update(status="failed", task=f"{base}; triage not run: spending gateway not configured")
    elif code == 4 or reason == "state_sync_failed" or o.get("persisted") is False:
        ev.update(status="failed", task=f"{base}; state not persisted or triage stalled ({reason or 'exit 4'})")
    elif triage == "FAILED":
        ev.update(status="failed", task=f"{base}; triage failed ({reason or 'no reason given'})")
    elif triage == "NOT_RUN_DRY_RUN":
        ev.update(status="idle", task=f"{base}; dry run, {h.get('pending', 0)} pending triage")
    elif triage in ("COMPLETED", "NO_NEW_HEADLINES"):
        ev.update(status="idle", task=f"{base}; triage {triage.lower().replace('_', ' ')}, published {(o.get('publication') or {}).get('published', 0)}")
    elif triage == "blocked" or (triage or "").startswith("BLOCKED"):
        ev.update(status="idle", task=f"{base}; triage blocked ({reason})")
    else:
        ev.update(status="unknown", task=f"{base}; triage outcome {triage!r} not recognised by the reporter")
    return ev


def map_builder(rec, step_outcome, env):
    if not rec:
        return _no_outcome(step_outcome)
    code, mode, f = rec.get("exit_code"), rec.get("mode"), rec.get("facts") or {}
    ev = {"dry_run": mode != "publish"}
    calls = f.get("model_calls") or 0
    if calls:
        tiers = {tier_from_model(m) for m in f.get("models") or []} - {None}
        if len(tiers) == 1:
            ev["model_tier"] = tiers.pop()
    else:
        ev["model_tier"] = "code"
    ev.update(cost_fields(f.get("estimated_cost_usd") if calls else 0, env))
    if code == 3 or rec.get("config_errors"):
        ev.update(status="failed", task="Configuration error: nothing ran")
        return ev
    if code == 4:
        ev.update(status="failed", task="Ledger state could not be read or persisted")
        return ev
    if mode == "off":
        ev.update(status="offline", task="Mode off: nothing runs")
        return ev
    if code == 5:
        ev.update(status="failed", task="Publication needs attention: " + (", ".join(f.get("publication_failures") or []) or "see run report"))
        return ev
    if not f:
        ev.update(status="unknown" if code == 0 else "failed", task=f"Run ended (exit {code}) without run facts ({mode} mode)")
        return ev
    if f.get("paused_or_stopped"):
        ev.update(status="idle", task="Paused or stopped by operator: nothing started")
        return ev
    summary = (f"{mode} mode: {f.get('tasks', 0)} task(s), {f.get('published_records', 0)} record(s) published, "
               f"{f.get('exceptions_held', 0)} exception(s) held")
    if f.get("exceptions_need_decision"):
        n = f["exceptions_need_decision"]
        ev.update(status="awaiting_approval", task=summary,
                  approval_request={"summary": f"{n} held exception(s) need Marc's decision (source registration or robots.txt exception). See the run report."})
    else:
        ev.update(status="idle", task=summary + ("; live verification pending" if f.get("verification_pending") else ""))
    return ev


def map_controller(rec, step_outcome, env):
    if not rec:
        return _no_outcome(step_outcome)
    code, mode, v = rec.get("exit_code"), rec.get("mode"), rec.get("verdict")
    ev = {"dry_run": mode != "active", "model_tier": "code", "cost_eur": 0.0, "cost_basis": "api"}
    if code == 3 or rec.get("config_errors"):
        ev.update(status="failed", task="Configuration error: no checks run")
    elif code == 4:
        ev.update(status="failed", task="State could not be persisted")
    elif not v:
        ev.update(status="unknown", task=f"Run ended (exit {code}) without a verdict")
    else:
        summary = f"Production check {v} ({mode}): {rec.get('checks', 0)} checks, {rec.get('detections_open', 0)} open exception(s)"
        if mode == "active" and rec.get("requires_human"):
            ev.update(status="awaiting_approval", task=summary,
                      approval_request={"summary": f"{rec['requires_human']} production exception(s) need a decision ({rec.get('critical', 0)} critical). See the run report."})
        else:
            ev.update(status="idle", task=summary)
    return ev


MAPPERS = {MASTER: map_master, BUILDER: map_builder, CONTROLLER: map_controller}


def build_event(phase, agent, env, rec=None, step_outcome=None):
    if agent not in AGENTS:
        raise ValueError("not a verified 03AA-04 agent code: " + str(agent))
    ev = start_event(agent, env) if phase == "start" else MAPPERS[agent](rec, step_outcome, env)
    ref = run_ref(env)
    full = {"agent_id": agent, "timestamp": utc_now_ms()}
    if ref:
        full["event_id"] = f"{ref}-{phase}"
        full["run_ref"] = ref
    full.update(ev)
    full["task"] = str(full.get("task", ""))[:160]
    return full


def read_outcome(path, agent):
    try:
        if not path or not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec if rec.get("agent") == agent else None
    except Exception:
        return None


def sign(secret_b64url, method, path, timestamp, nonce, body):
    key = base64.urlsafe_b64decode(secret_b64url + "=" * (-len(secret_b64url) % 4))
    canonical = "\n".join([method.upper(), path, timestamp, nonce, hashlib.sha256(body).hexdigest()])
    return hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()


def url_allowed(url):
    u = urllib.parse.urlparse(url or "")
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1"))


def post(env, event):
    url, key_id, secret = env.get("ACC_REPORT_URL"), env.get("ACC_KEY_ID"), env.get("ACC_AGENT_SECRET")
    if not (url and key_id and secret):
        return "SKIPPED_NOT_CONFIGURED", None
    if not url_allowed(url):
        return "SKIPPED_INSECURE_URL", None
    body = json.dumps({"version": "1.1", "events": [event]}, separators=(",", ":")).encode()
    ts, nonce = utc_now(), secrets.token_urlsafe(24)
    try:
        signature = sign(secret, "POST", PATH, ts, nonce, body)
    except Exception:
        return "SKIPPED_BAD_SECRET", None
    req = urllib.request.Request(url.rstrip("/") + PATH, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-ACC-Key-Id": key_id, "X-ACC-Timestamp": ts, "X-ACC-Nonce": nonce,
        "X-ACC-Signature": signature})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            res = json.loads(r.read() or b"{}")
            return ("ACCEPTED" if res.get("accepted") else "DUPLICATE" if res.get("duplicates") else "REJECTED"), res
    except urllib.error.HTTPError as e:
        return f"HTTP_{e.code}", None
    except Exception as e:
        return "UNREACHABLE_" + type(e).__name__, None


def main(argv=None, env=None):
    env = os.environ if env is None else env
    try:
        ap = argparse.ArgumentParser()
        ap.add_argument("phase", choices=["start", "end"])
        ap.add_argument("--agent", required=True)
        ap.add_argument("--outcome-file")
        ap.add_argument("--step-outcome", default="")
        a = ap.parse_args(argv)
        rec = read_outcome(a.outcome_file, a.agent) if a.phase == "end" else None
        event = build_event(a.phase, a.agent, env, rec, a.step_outcome)
        status, res = post(env, event)
        flags = sorted({f for r in (res or {}).get("results", []) for f in r.get("flags", []) or []})
        print(f"ACC_REPORT {status} {a.agent} {event['status']}" + (f" flags={','.join(flags)}" if flags else ""))
    except SystemExit:
        print("ACC_REPORT SKIPPED_BAD_ARGUMENTS")
    except Exception as e:   # never affects the agent's run
        print("ACC_REPORT ERROR " + type(e).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
