"""Feed-health diagnostic worker. Python only: no model call is needed or made.

HTTP 200 alone never means healthy: content shape, freshness and the stored
validation result must all agree. Fixture runs are labelled FIXTURE and are never
live evidence.
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .sanitise import clean_text, redact_url
from .workers import WorkResult, Worker

REFERENCE_MAX_AGE_HOURS = 24 * 7
# Diagnostic sample point for the ADSB.lol point endpoint (Larnaca area). It is a probe, not coverage.
ADSB_PROBE = (34.88, 33.62, 25)


class HttpResult:
    def __init__(self, status=None, body=b"", error_kind=None, elapsed=0.0, attempts=1):
        self.status, self.body, self.error_kind, self.elapsed, self.attempts = status, body, error_kind, elapsed, attempts


def http_get(url, *, timeout=15, attempts=2, max_bytes=200_000):
    """Bounded read-only GET. Bounded retries on transient failures only. Never logs the URL query."""
    last = None
    for i in range(attempts):
        t0 = time.monotonic()
        try:
            req = Request(url, headers={"User-Agent": "TravelIntelligenceOpsDirector/1.0 (diagnostic, read-only)"})
            with urlopen(req, timeout=timeout) as r:
                return HttpResult(r.status, r.read(max_bytes), None, time.monotonic() - t0, i + 1)
        except HTTPError as exc:
            last = HttpResult(exc.code, exc.read(2000) if hasattr(exc, "read") else b"", "http_error",
                              time.monotonic() - t0, i + 1)
            if exc.code not in (429, 500, 502, 503, 504):
                return last
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            last = HttpResult(None, b"", "network_error:" + type(reason).__name__ + ":" + clean_text(reason, 80),
                              time.monotonic() - t0, i + 1)
        if i < attempts - 1:
            time.sleep(2)
    return last


def _parse_ts(value):
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len("2026-01-01T00:00:00")] if "T" in s and "-" in s else s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def validate_content(source_id, body: bytes, now):
    """Return (content_ok, detail, newest_content_time or None). Checks shape, not just status."""
    try:
        if source_id in ("gdelt-events", "gdacs-alerts", "adsblol-aircraft", "reliefweb-disasters"):
            doc = json.loads(body.decode("utf-8", "replace"))
            key = {"gdelt-events": "articles", "gdacs-alerts": "features", "adsblol-aircraft": "ac",
                   "reliefweb-disasters": "data"}[source_id]
            items = doc.get(key) if isinstance(doc, dict) else None
            if not isinstance(items, list):
                return False, f"JSON lacks expected '{key}' list", None
            if not items:
                return False, f"'{key}' list is empty; absence of data proves nothing", None
            times = []
            if source_id == "gdelt-events":
                times = [_parse_ts(i.get("seendate")) for i in items if isinstance(i, dict)]
            elif source_id == "gdacs-alerts":
                times = [_parse_ts((i.get("properties") or {}).get("todate") or (i.get("properties") or {}).get("fromdate"))
                         for i in items if isinstance(i, dict)]
            if source_id == "adsblol-aircraft" and isinstance(doc.get("now"), (int, float)):
                times = [datetime.fromtimestamp(doc["now"] / 1000, tz=timezone.utc)]
            times = [t for t in times if t]
            return True, f"{len(items)} items parsed", (max(times) if times else None)
        if source_id in ("nasa-firms", "ourairports-airports"):
            text = body.decode("utf-8-sig", "replace")
            # The probe read is truncated, so drop a possibly cut final line.
            lines = text.splitlines()[:-1] if len(body) >= 199_000 else text.splitlines()
            rows = list(csv.DictReader(io.StringIO("\n".join(lines))))
            need = (["latitude", "longitude", "acq_date"] if source_id == "nasa-firms"
                    else ["ident", "iso_country", "latitude_deg", "longitude_deg"])
            if not rows or not set(need).issubset(rows[0].keys()):
                return False, "CSV header or rows do not match the expected schema", None
            if source_id == "nasa-firms":
                times = [_parse_ts(r.get("acq_date")) for r in rows]
                times = [t for t in times if t]
                return True, f"{len(rows)} sampled rows", (max(times) if times else None)
            return True, f"{len(rows)} sampled rows", None
    except (ValueError, UnicodeDecodeError, AttributeError):
        return False, "Response could not be parsed as the expected format", None
    return False, "No content validator for this source", None


def probe_url_for(source):
    sid, ep = source["id"], source["endpoint"]
    if sid == "gdelt-events":
        return ep.replace("maxrecords=20", "maxrecords=5")
    if sid == "nasa-firms":
        return source.get("publicEndpoint", ep)
    if sid == "adsblol-aircraft":
        la, lo, r = ADSB_PROBE
        return ep.format(lat=la, lon=lo, radius=r)
    return ep


def allowed_hosts(source):
    urls = [source["endpoint"], source.get("publicEndpoint", ""), *source.get("fallbackEndpoints", [])]
    return frozenset(urlsplit(u).hostname for u in urls if u and urlsplit(u).hostname)


class FeedHealthWorker(Worker):
    name = "feed_health_diagnostic"
    task_types = ("feed_health_diagnostic",)
    capabilities = frozenset({"network_read", "read_repo_evidence"})
    description = "Bounded read-only feed diagnostics: status, content, freshness, validation, configuration."

    def run(self, task, ctx):
        inp = task["inputs"] if isinstance(task["inputs"], dict) else __import__("json").loads(task["inputs"])
        source, evidence, mode = inp["source"], inp.get("evidence"), inp.get("mode", "live")
        now = ctx.store.clock()
        checks, ev = [], []

        # 1. configuration. Missing approved configuration means NO request is made to the provider.
        env_name = source.get("appnameEnvironment")
        config_ok, approval_needed = True, False
        if env_name:
            present = bool((ctx_env(inp).get(env_name) or "").strip())
            checks.append({"check": "configuration", "name": env_name, "present": present})
            if not present:
                config_ok, approval_needed = False, True
        opt_key = "FIRMS_MAP_KEY" if source["id"] == "nasa-firms" else None
        if opt_key:
            checks.append({"check": "configuration", "name": opt_key, "present": bool((ctx_env(inp).get(opt_key) or "").strip()),
                           "note": "optional; public CSV works without it"})

        # 2. stored validation result
        stored = {"status": (evidence or {}).get("status", "unknown"), "ok": (evidence or {}).get("ok"),
                  "error": clean_text((evidence or {}).get("error", ""), 200)}
        checks.append({"check": "stored_validation", **stored})

        # 3. response (live or fixture)
        resp = None
        if not config_ok:
            checks.append({"check": "response", "skipped": "required approved configuration is absent; no request made"})
        elif mode == "fixture":
            fx = inp["fixture_response"]
            if fx.get("as_of"):                      # deterministic clock for fixtures only
                now = _parse_ts(fx["as_of"][:19]) or now
            resp = HttpResult(fx.get("status"), fx.get("body", "").encode() if isinstance(fx.get("body"), str)
                              else fx.get("body", b""), fx.get("error_kind"))
        else:
            url = probe_url_for(source)
            ctx.checkpoint({"phase": "http", "url": redact_url(url)})
            resp = ctx.http_get(url, timeout=ctx.cfg.feed_http_timeout_seconds,
                                attempts=ctx.cfg.feed_http_attempts, max_bytes=min(ctx.cfg.feed_max_bytes, 200_000))
        content_ok = fresh = None
        newest = None
        if resp is not None:
            checks.append({"check": "response", "status_code": resp.status, "error_kind": resp.error_kind,
                           "bytes": len(resp.body), "attempts": getattr(resp, "attempts", 1)})
            if resp.status == 200:
                content_ok, detail, newest = validate_content(source["id"], resp.body, now)
                checks.append({"check": "expected_content", "ok": content_ok, "detail": detail})
                if content_ok:
                    max_h = (REFERENCE_MAX_AGE_HOURS if source["id"] == "ourairports-airports"
                             else ctx.cfg.freshness_max_hours)
                    if newest is not None:
                        age_h = (now - newest).total_seconds() / 3600
                        fresh = -1 <= age_h <= max_h
                        checks.append({"check": "freshness", "basis": "content_timestamp", "age_hours": round(age_h, 1),
                                       "limit_hours": max_h, "ok": fresh})
                    else:
                        gen = _parse_ts((evidence or {}).get("fetchedAt") or inp.get("snapshot_generated"))
                        if gen:
                            age_h = (now - gen).total_seconds() / 3600
                            fresh = 0 <= age_h <= max_h if source["id"] == "ourairports-airports" else None
                            checks.append({"check": "freshness", "basis": "snapshot_timestamp_only",
                                           "age_hours": round(age_h, 1), "limit_hours": max_h,
                                           "ok": fresh, "note": "no content timestamp available"})
                        else:
                            checks.append({"check": "freshness", "basis": "none", "ok": None})

        classification, health, detail = self._classify(source, approval_needed, resp, content_ok, fresh, stored, mode)
        label = "FIXTURE" if mode == "fixture" else "LIVE"
        ev.append({"kind": "feed_diagnostic", "evidence_mode": label,
                   "live_evidence": mode == "live" and resp is not None,
                   "source_id": source["id"], "publisher": source.get("name"),
                   "source_url": redact_url(source.get("url") or source["endpoint"]),
                   "retrieved_at_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
                   "checks": checks, "verification_state": "collection_failed" if health != "healthy" else "verified"})
        out = {"source_id": source["id"], "classification": classification, "health": health, "detail": detail,
               "evidence_mode": label, "checks": checks}
        if classification in ("approval_required", "configuration_issue"):
            return WorkResult("blocked", out, ev, f"{classification}: {detail}", True)
        if classification == "unknown_cause":
            return WorkResult("blocked", out, ev, "unknown_cause: needs investigation worker (not implemented)", False)
        return WorkResult("completed", out, ev)

    @staticmethod
    def _classify(source, approval_needed, resp, content_ok, fresh, stored, mode):
        if approval_needed:
            return ("approval_required", "blocked",
                    f"{source['name']} needs a provider-approved application name in "
                    f"{source.get('appnameEnvironment')}; none configured. No application will be submitted by this agent. "
                    "Before anyone applies, check whether an application is already pending or approved "
                    "(provider account and e-mail); do not submit a duplicate.")
        if resp is None:
            return "unknown_cause", "unknown", "No response evidence was collected"
        s = resp.status
        if s is None:
            return ("unknown_cause", "unavailable",
                    "No HTTP response (" + clean_text(resp.error_kind, 100) + "). A provider outage cannot be told apart "
                    "from a local network or egress restriction on the checking host")
        if s in (429, 500, 502, 503, 504):
            what = "rate limiting" if s == 429 else f"HTTP {s}"
            return "provider_outage", "unavailable", f"Provider unavailable after bounded retries: {what}"
        if s in (401, 403):
            body = resp.body[:500].decode("utf-8", "replace").lower() if resp.body else ""
            if "appname" in body or "approv" in body:
                return "approval_required", "blocked", f"HTTP {s}: provider requires an approved application name"
            return "configuration_issue", "blocked", f"HTTP {s}: credentials or access configuration rejected"
        if s == 400 or s == 404:
            return "configuration_issue", "blocked", f"HTTP {s}: request or endpoint configuration appears wrong"
        if s != 200:
            return "unknown_cause", "unknown", f"Unexpected HTTP {s}"
        if not content_ok:
            return "unknown_cause", "degraded", "HTTP 200 but expected content not present; status alone is not health"
        if fresh is False:
            return "stale_evidence", "stale", "Responding with valid content, but content is outside the freshness limit"
        if fresh is None and source["id"] != "ourairports-airports" and source["id"] != "adsblol-aircraft":
            return "unknown_cause", "unknown", "Valid content but freshness could not be established"
        if stored["status"] != "healthy":
            return ("recovered_unconfirmed", "degraded",
                    f"Probe passed but the stored snapshot reports '{stored['status']}'; "
                    "re-run the collector before treating as healthy")
        return "healthy", "healthy", "Status, content, freshness and stored validation agree"


def ctx_env(inp):
    return inp.get("env_override") if inp.get("env_override") is not None else os.environ
