"""Evidence retrieval for 03AA-04_Investigation_Verification_TI: permitted sources only, bounded, read-only.

* Only hosts named in config/investigation_verification.json are ever contacted. URLs are built from configured templates
  and validated identifiers; a URL supplied by a discovery candidate is never fetched.
* Redirects are followed only to another allow-listed https host (at most ``max_redirects``); no credentials are sent.
* robots.txt is honoured through the Country Builder's reader (unreadable = not permitted, conservative).
* The source must be in config/source_registry.json with an allowed tier, status and automation permission.
* Every provider answer is untrusted data. It is parsed against an expected shape, its text fields pass an allowlist, and an
  answer that does not match what was asked for (wrong event id, wrong type) is rejected as malformed.
Every failure is returned as a status; nothing here raises on a provider problem.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .country_evidence import Robots
from .feed_health import HttpResult
from .sanitise import clean_text

USER_AGENT = "TravelIntelligenceInvestigationVerification/1.0 (+https://travelintelligence22.com; read-only; honours robots.txt)"
ID_RE = re.compile(r"^[0-9]{1,12}$")
SEV_TEXT_OK = re.compile(r"^[A-Za-z0-9 .,'()/&:>%_=+-]{0,160}$")      # '>' appears in GDACS' own class text ("Hurricane/Typhoon > 74 mph")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'()/&_-]{0,59}$")
INJECTION = re.compile(
    r"(ignore\s+(all|any|the|previous|prior|above)|disregard\s+(the|all|previous)|system\s+prompt|developer\s+message|"
    r"you\s+are\s+(now\s+)?(an?|the)\s|as\s+an\s+ai\b|new\s+instructions?|override|<\s*/?script|javascript:|```|"
    r"\bpublish\s+this\b|\bmark\s+(this\s+)?(as\s+)?confirmed\b|https?://)", re.I)


def screen_text(value, limit=200):
    """(clean text, instruction_like). Control characters are removed; instruction-like text is flagged, never obeyed."""
    text = clean_text(value, limit)
    return text, bool(INJECTION.search(text))


def safe_name(value):
    """A provider-supplied name that may appear in public text: allowlisted characters only, no instruction-like content."""
    text, flagged = screen_text(value, 80)
    text = " ".join(text.split())
    if flagged or not NAME_RE.match(text):
        return None
    return text


def parse_dt(value):
    """ISO-8601 (with or without zone; naive values are UTC, as GDACS states them) -> aware UTC datetime or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def iso_utc(d):
    return d.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if d else None


# ---- transport -------------------------------------------------------------------------------------------------
class _Restricted(HTTPRedirectHandler):
    def __init__(self, hosts, limit):
        self.hosts, self.limit, self.n = frozenset(hosts), limit, 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        self.n += 1
        if self.n > self.limit or parts.scheme != "https" or parts.hostname not in self.hosts:
            raise HTTPError(newurl, 403, "redirect outside the allow-list refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def make_transport(hosts, max_redirects=2):
    """A bounded GET that refuses non-https, non-allow-listed hosts and redirects out of the allow-list."""
    allowed = frozenset(hosts)

    def http_get(url, *, timeout=20, attempts=2, max_bytes=2_000_000):
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in allowed or parts.username or parts.password:
            return HttpResult(None, b"", "host_not_allowed", 0.0, 0)
        last = None
        for i in range(max(1, attempts)):
            t0 = time.monotonic()
            try:
                opener = build_opener(_Restricted(allowed, max_redirects))
                req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,*/*;q=0.5", "Accept-Language": "en"})
                with opener.open(req, timeout=timeout) as r:
                    return HttpResult(r.status, r.read(max_bytes + 1)[:max_bytes + 1], None, time.monotonic() - t0, i + 1)
            except HTTPError as exc:
                last = HttpResult(exc.code, b"", "http_error", time.monotonic() - t0, i + 1)
                if exc.code not in (429, 500, 502, 503, 504):
                    return last
            except (URLError, TimeoutError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                last = HttpResult(None, b"", "network_error:" + type(reason).__name__, time.monotonic() - t0, i + 1)
            if i < attempts - 1:
                time.sleep(2)
        return last
    return http_get


class RequestBudget:
    """Bounds retrieval per run and per task. A request that would exceed either bound is not made."""
    def __init__(self, per_run, per_task):
        self.per_run, self.per_task, self.run_used, self.task_used = per_run, per_task, 0, {}

    def take(self, task_id):
        if self.run_used >= self.per_run or self.task_used.get(task_id, 0) >= self.per_task:
            return False
        self.run_used += 1
        self.task_used[task_id] = self.task_used.get(task_id, 0) + 1
        return True


# ---- register gate ---------------------------------------------------------------------------------------------
def registry_gate(cfg, root, source_key):
    """(ok, reason): the source must be registered with an allowed tier, status and automation permission."""
    from pathlib import Path
    reg = json.loads((Path(root) / cfg.raw["registry"]["file"]).read_text(encoding="utf-8"))
    rid = cfg.sources[source_key]["registry_id"]
    entry = next((s for s in reg["sources"] if s["id"] == rid), None)
    rules = cfg.raw["registry"]
    if entry is None:
        return False, "source_not_in_registry"
    if entry["tier"] not in rules["allowed_tiers"]:
        return False, f"source_tier_not_allowed:{entry['tier']}"
    if entry["status"] not in rules["allowed_status"]:
        return False, f"source_status_not_allowed:{entry['status']}"
    if entry["automation"] not in rules["allowed_automation"]:
        return False, f"source_automation_not_permitted:{entry['automation']}"
    return True, "registered"


# ---- GDACS ------------------------------------------------------------------------------------------------------
def parse_gdacs(body, expect_type, expect_id):
    """The GDACS feature for exactly the requested event, as plain validated fields. ValueError(code) on any mismatch.

    The register notes that GDACS' single-event endpoint could not always be pinned to the requested id, so the id and type
    that come back must equal what was asked for; anything else is rejected rather than trusted."""
    try:
        doc = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("not_json") from None
    if not isinstance(doc, dict):
        raise ValueError("unexpected_shape")
    feats = doc["features"] if isinstance(doc.get("features"), list) else ([doc] if doc.get("type") == "Feature" else None)
    if feats is None:
        raise ValueError("unexpected_shape")
    best = None
    for f in feats:
        p = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(p, dict):
            continue
        try:
            same = str(p.get("eventtype")) == expect_type and int(p.get("eventid")) == int(expect_id)
        except (TypeError, ValueError):
            continue
        if same and (best is None or str(p.get("datemodified") or "") >= str(best.get("datemodified") or "")):
            best = p
    if best is None:
        raise ValueError("requested_event_not_in_response")
    sev = best.get("severitydata") if isinstance(best.get("severitydata"), dict) else {}
    affected = []
    for a in best.get("affectedcountries") or []:
        if isinstance(a, dict) and isinstance(a.get("iso2"), str) and len(a["iso2"]) == 2:
            affected.append({"iso2": a["iso2"].lower(), "iso3": str(a.get("iso3") or "")[:3], "name": safe_name(a.get("countryname"))})
    raw_name = clean_text(best.get("eventname") or "", 80)
    report = best.get("url", {}).get("report") if isinstance(best.get("url"), dict) else None
    sev_text = clean_text(sev.get("severitytext"), 160)
    sev_untrusted = bool(sev_text) and (bool(INJECTION.search(sev_text)) or not SEV_TEXT_OK.match(sev_text))
    try:
        episode = int(best["episodeid"]) if best.get("episodeid") not in (None, "") else None
    except (TypeError, ValueError):
        episode = None
    try:
        severity = float(sev.get("severity")) if sev.get("severity") is not None else None
    except (TypeError, ValueError):
        severity = None
    return {
        "eventType": expect_type, "eventId": int(expect_id), "episodeId": episode,
        "name": safe_name(raw_name), "nameInstructionLike": bool(INJECTION.search(raw_name)),
        "alertLevel": best.get("alertlevel") if best.get("alertlevel") in ("Green", "Orange", "Red") else None,
        "isCurrent": {"true": True, "false": False}.get(str(best.get("iscurrent")).lower()),
        "from": best.get("fromdate"), "to": best.get("todate"), "modified": best.get("datemodified"),
        "origin": clean_text(best.get("source"), 20) or None, "affected": affected,
        "severity": severity, "severityText": "" if sev_untrusted else sev_text, "severityTextUntrusted": sev_untrusted, "severityUnit": clean_text(sev.get("severityunit"), 12),
        "reportUrl": report if isinstance(report, str) else None,
    }


def canonical_hash(structured):
    """Hash of the facts the assessment depends on. Retrieval time and unstable chrome are excluded, so unchanged evidence
    has an unchanged hash and costs no further work."""
    keys = ("eventType", "eventId", "name", "alertLevel", "isCurrent", "from", "to", "modified", "affected", "severity", "severityText", "severityUnit")
    return hashlib.sha256(json.dumps({k: structured.get(k) for k in keys}, sort_keys=True, default=str).encode()).hexdigest()[:20]


def new_evidence(cfg, source_key, now, **extra):
    s = cfg.sources[source_key]
    doc = {"sourceKey": source_key, "sourceId": s["sourceId"], "publisher": s["publisher"], "tier": s.get("quality"),
           "independenceGroupId": s["independence_group"], "originalLanguage": s["original_language"],
           "contentRights": s["content_rights"], "accessMethod": s["access_method"],
           "automationPermission": s["automation_permission"], "retrievedAt": iso_utc(now), "status": "UNAVAILABLE",
           "error": None, "httpStatus": None, "structured": None, "payloadSha256": None, "evidenceHash": None, "retrieval": "live"}
    doc.update(extra)
    return doc


def fetch_gdacs(cfg, root, http, robots, now, etype_key, event_id, task_id, budget, retrieval="live"):
    """One evidence document for a GDACS event. Never raises; every failure is a status."""
    src = cfg.sources["gdacs"]
    gd_type = cfg.raw["event_types"][etype_key]["gdacs"]
    ev = new_evidence(cfg, "gdacs", now, retrieval=retrieval)
    if not ID_RE.match(str(event_id)):
        ev.update(status="PARSE_FAILED", error="invalid_event_id")
        return ev
    ok, why = registry_gate(cfg, root, "gdacs")
    if not ok:
        ev.update(status="NOT_PERMITTED", error=why)
        return ev
    url = src["detail_url"].format(type=gd_type, id=int(event_id))
    ev["apiUrl"] = url
    allowed, why = robots.allowed(url)
    if not allowed:
        ev.update(status="BLOCKED_BY_ROBOTS", error=why)
        return ev
    if not budget.take(task_id):
        ev.update(status="BUDGET_EXHAUSTED", error="request_budget_exhausted")
        return ev
    lim = cfg.evidence
    res = http(url, timeout=lim["http_timeout_seconds"], attempts=lim["http_attempts"], max_bytes=lim["max_bytes_per_response"])
    ev["httpStatus"] = getattr(res, "status", None)
    if res is None or res.status != 200:
        ev["error"] = (getattr(res, "error_kind", None) or "no_response") + (f":{res.status}" if res and res.status else "")
        return ev
    if len(res.body) > lim["max_bytes_per_response"] or not res.body:
        ev.update(status="PARSE_FAILED", error="response_empty_or_too_large")
        return ev
    try:
        st = parse_gdacs(res.body, gd_type, int(event_id))
    except ValueError as exc:
        ev.update(status="PARSE_FAILED", error=str(exc))
        return ev
    st["eventTypeKey"] = etype_key
    ev.update(status="RETRIEVED", structured=st, payloadSha256=hashlib.sha256(res.body).hexdigest(),
              evidenceHash=canonical_hash(st))
    ev["url"] = src["report_url"].format(type=gd_type, id=int(event_id), ep=st["episodeId"] or "")
    ev["evidenceId"] = "ev-" + hashlib.sha256(f"gdacs|{gd_type}|{event_id}|{ev['evidenceHash']}".encode()).hexdigest()[:14]
    return ev


def make_robots(http, timeout=15):
    return Robots(http, timeout)
