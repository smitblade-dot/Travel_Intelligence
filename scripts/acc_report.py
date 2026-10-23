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

Outage buffer (--outbox-file, --pending-file). If the ACC cannot be reached, events are kept and offered again
on the agent's next run, never in a loop:
  - the start event waits in a per-run pending file; the end step adds it, the end event and any events kept
    from earlier runs (restored by the workflow from the Actions cache) and makes ONE delivery attempt;
  - bounded: at most 200 events, 72 hours and 24 attempts per event; start events are dropped first; drops are
    counted and reported with the next delivered event;
  - no duplicates: every event keeps its run-based event_id and the ACC ignores repeats; events are removed
    when the ACC accepts, already holds, or permanently rejects them;
  - event time is never changed; each buffered event carries delivery metadata (queued_at_utc, attempts,
    dropped_before) so the ACC can show original time and delivery time separately;
  - the buffer holds only the mapped ACC events (status, counts and enumerated reasons, no agent output text)
    and an HMAC tag made with the agent's ACC secret; a buffer that fails the check is discarded, not sent.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
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
START_TIMEOUT_S = 3          # the start step runs before the agent: keep it short
OUTBOX_FORMAT = "acc-outbox/1"
OUTBOX_MAX_EVENTS = 200
OUTBOX_MAX_AGE_H = 72
OUTBOX_MAX_ATTEMPTS = 24
MAX_RESPONSE_BYTES = 256 * 1024   # the ACC answers with a few hundred bytes; anything bigger is refused


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


def _config(env):
    url, key_id, secret = env.get("ACC_REPORT_URL"), env.get("ACC_KEY_ID"), env.get("ACC_AGENT_SECRET")
    if not (url and key_id and secret):
        return None, "SKIPPED_NOT_CONFIGURED"
    if not url_allowed(url):
        return None, "SKIPPED_INSECURE_URL"
    try:
        _key(secret)
    except Exception:
        return None, "SKIPPED_BAD_SECRET"
    return (url, key_id, secret), None


def _key(secret_b64url):
    return base64.urlsafe_b64decode(secret_b64url + "=" * (-len(secret_b64url) % 4))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def send_batch(env, events, timeout=TIMEOUT_S):
    """One signed POST of up to OUTBOX_MAX_EVENTS events. Returns (status, response or None). Never raises."""
    cfg, why = _config(env)
    if not cfg:
        return why, None
    url, key_id, secret = cfg
    body = json.dumps({"version": "1.1", "events": events}, separators=(",", ":")).encode()
    ts, nonce = utc_now(), secrets.token_urlsafe(24)
    req = urllib.request.Request(url.rstrip("/") + PATH, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-ACC-Key-Id": key_id, "X-ACC-Timestamp": ts, "X-ACC-Nonce": nonce,
        "X-ACC-Signature": sign(secret, "POST", PATH, ts, nonce, body)})
    # One hard deadline covers DNS, connect, headers and body, so a service that hangs or drips bytes cannot hold the step open.
    # Redirects are refused (the signed request goes only to the configured address) and the reply is size-capped.
    def attempt():
        with _OPENER.open(req, timeout=timeout) as r:
            chunks, size = [], 0
            while True:
                chunk = r.read1(4096)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ValueError("response too large")
                chunks.append(chunk)
            return "DELIVERED", json.loads(b"".join(chunks) or b"{}")
    box = {}

    def runner():
        try:
            box["ok"] = attempt()
        except urllib.error.HTTPError as e:
            box["ok"] = (f"HTTP_{e.code}", None)
        except BaseException as e:   # never let the reporter raise
            box["ok"] = ("UNREACHABLE_" + type(e).__name__, None)
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "UNREACHABLE_Deadline", None
    return box.get("ok", ("UNREACHABLE_Unknown", None))


def post(env, event, timeout=TIMEOUT_S):
    status, res = send_batch(env, [event], timeout)
    if status != "DELIVERED":
        return status, res
    return ("ACCEPTED" if res.get("accepted") else "DUPLICATE" if res.get("duplicates") else "REJECTED"), res


# ---------- outage buffer ----------

def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _tag(secret, agent, entries, dropped):
    canon = json.dumps({"agent": agent, "entries": entries, "dropped": dropped}, sort_keys=True, separators=(",", ":"))
    return hmac.new(_key(secret), (OUTBOX_FORMAT + "\n" + canon).encode(), hashlib.sha256).hexdigest()


def load_outbox(path, agent, secret):
    """(entries, dropped, note). A missing file is empty; a file that fails the integrity check is discarded."""
    if not path or not os.path.exists(path):
        return [], 0, None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        entries, dropped = doc["entries"], int(doc.get("dropped", 0))
        ok = (doc.get("format") == OUTBOX_FORMAT and doc.get("agent") == agent and isinstance(entries, list)
              and hmac.compare_digest(_tag(secret, agent, entries, dropped), str(doc.get("tag", ""))))
        if not ok:
            return [], 0, "outbox_discarded_integrity"
        return entries, dropped, None
    except Exception:
        return [], 0, "outbox_discarded_unreadable"


def save_outbox(path, agent, secret, entries, dropped):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    doc = {"format": OUTBOX_FORMAT, "agent": agent, "entries": entries, "dropped": dropped,
           "tag": _tag(secret, agent, entries, dropped)}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def prune(entries, now):
    """Apply retention. Returns (kept, dropped_count). Start events go first when over the size limit."""
    limit = now.timestamp() - OUTBOX_MAX_AGE_H * 3600
    kept = [e for e in entries if _parse(e["queued_at_utc"]).timestamp() >= limit and e.get("attempts", 0) < OUTBOX_MAX_ATTEMPTS]
    dropped = len(entries) - len(kept)
    if len(kept) > OUTBOX_MAX_EVENTS:
        excess = len(kept) - OUTBOX_MAX_EVENTS
        starts = [i for i, e in enumerate(kept) if e["event"].get("status") == "working"][:excess]
        kept = [e for i, e in enumerate(kept) if i not in set(starts)]
        excess = len(kept) - OUTBOX_MAX_EVENTS
        if excess > 0:
            kept = kept[excess:]
        dropped += len(entries) - dropped - len(kept)
    return kept, dropped


def with_delivery(entry, dropped_before):
    ev = dict(entry["event"])
    if entry.get("attempts", 0) > 0 or dropped_before:
        ev["delivery"] = {"queued_at_utc": entry["queued_at_utc"], "attempts": entry.get("attempts", 0) + 1}
        if dropped_before:
            ev["delivery"]["dropped_before"] = dropped_before
    return ev


def deliver(env, entries, dropped, timeout=TIMEOUT_S):
    """ONE attempt for the whole buffer. Returns (status, remaining_entries, dropped, results_by_event_id)."""
    if not entries:
        return "NOTHING_TO_SEND", [], dropped, {}
    events = [with_delivery(e, dropped if i == 0 else 0) for i, e in enumerate(entries)]
    status, res = send_batch(env, events, timeout)
    if status != "DELIVERED" or not isinstance((res or {}).get("results"), list) or len(res["results"]) != len(entries):
        for e in entries:
            e["attempts"] = e.get("attempts", 0) + 1
        return status if status != "DELIVERED" else "BAD_RESPONSE", entries, dropped, {}
    outcomes = {}
    for e, r in zip(entries, res["results"]):
        outcomes[e["event"].get("event_id") or id(e)] = r.get("outcome")
    # accepted, duplicate and rejected are final: never resend. The dropped count travelled with event 0.
    return "DELIVERED", [], 0, outcomes


def write_output(env, name, value):
    path = env.get("GITHUB_OUTPUT")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"{name}={value}\n")
        except Exception:
            pass


def run_end_buffered(agent, event, env, outbox_path, pending_path):
    cfg, why = _config(env)
    if not cfg:
        return why, None
    secret = cfg[2]
    now = _now()
    entries, dropped, note = load_outbox(outbox_path, agent, secret)
    restored = len(entries)
    pending = read_pending(pending_path, agent)
    if pending:
        entries.append(pending)
    entries.append({"event": event, "queued_at_utc": _iso(now), "attempts": 0})
    entries, pruned = prune(entries, now)
    dropped += pruned
    status, remaining, dropped, outcomes = deliver(env, entries, dropped)
    if remaining or restored or note:
        save_outbox(outbox_path, agent, secret, remaining, dropped)
        write_output(env, "outbox_save", "true")
    end_outcome = outcomes.get(event.get("event_id") or "", None) if outcomes else None
    summary = {"status": status, "end": end_outcome, "delivered": len(outcomes), "queued": len(remaining),
               "dropped": dropped, "restored": restored, "note": note}
    return status, summary


def write_pending(path, event):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"event": event, "queued_at_utc": _iso(_now()), "attempts": 1}, fh)
    except Exception:
        pass


def read_pending(path, agent):
    try:
        if not path or not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            p = json.load(fh)
        return p if p.get("event", {}).get("agent_id") == agent else None
    except Exception:
        return None


def main(argv=None, env=None):
    env = os.environ if env is None else env
    try:
        ap = argparse.ArgumentParser()
        ap.add_argument("phase", choices=["start", "end"])
        ap.add_argument("--agent", required=True)
        ap.add_argument("--outcome-file")
        ap.add_argument("--step-outcome", default="")
        ap.add_argument("--outbox-file", help="end: outage buffer restored from and saved to the Actions cache")
        ap.add_argument("--pending-file", help="start writes, end reads: start event not yet delivered")
        a = ap.parse_args(argv)
        rec = read_outcome(a.outcome_file, a.agent) if a.phase == "end" else None
        event = build_event(a.phase, a.agent, env, rec, a.step_outcome)
        if a.phase == "end" and a.outbox_file:
            status, summary = run_end_buffered(a.agent, event, env, a.outbox_file, a.pending_file)
            if summary is None:
                print(f"ACC_REPORT {status} {a.agent} {event['status']}")
            else:
                shown = {"accepted": "ACCEPTED", "duplicate": "DUPLICATE", "rejected": "REJECTED"}.get(summary["end"], "QUEUED" if summary["queued"] else status)
                print(f"ACC_REPORT {shown} {a.agent} {event['status']} delivery={summary['status']} delivered={summary['delivered']} "
                      f"queued={summary['queued']} dropped={summary['dropped']}" + (f" note={summary['note']}" if summary["note"] else ""))
            return 0
        status, res = post(env, event, START_TIMEOUT_S if a.phase == "start" else TIMEOUT_S)
        if a.phase == "start" and a.pending_file and status not in ("ACCEPTED", "DUPLICATE", "REJECTED") and not status.startswith("SKIPPED"):
            write_pending(a.pending_file, event)
            status = "QUEUED_" + status
        flags = sorted({f for r in (res or {}).get("results", []) for f in r.get("flags", []) or []})
        print(f"ACC_REPORT {status} {a.agent} {event['status']}" + (f" flags={','.join(flags)}" if flags else ""))
    except SystemExit:
        print("ACC_REPORT SKIPPED_BAD_ARGUMENTS")
    except Exception as e:   # never affects the agent's run
        print("ACC_REPORT ERROR " + type(e).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
