"""Discovery candidates for 03AA-04_Investigation_Verification_TI.

A candidate is a *lead*, never a fact. Two producers feed the same intake:

* ``discovery_signal_gdacs``: the GDACS alert items already collected by the existing discovery layer
  (``data/phase1_signals.json``). Ordinary code, no model, no cost. The worker does not trust these values: it retrieves the
  event itself from the provider before anything is assessed.
* ``triage_leads``: headline leads that the Haiku triage selected (``candidates`` in ``data/continuous_monitor_state.json``).
  They exist only while paid triage runs. While triage is disabled the producer finds nothing; that is reported, not hidden.

Cheap scope checks (type, alert level, tracked country, stated dates) run here so that an out-of-scope or stale lead costs no
retrieval. Everything else is judged on retrieved evidence in ``investigation_assess``.
"""
from __future__ import annotations

import hashlib
import re
from datetime import timedelta

from .investigation_config import ALERT_LEVELS
from .investigation_sources import iso_utc, parse_dt, safe_name

GDACS_TO_KEY = {"TC": "TROPICAL_CYCLONE", "EQ": "EARTHQUAKE"}
LEAD_WORDS = {"TROPICAL_CYCLONE": re.compile(r"\b(cyclone|typhoon|hurricane|tropical (storm|depression))\b", re.I),
              "EARTHQUAKE": re.compile(r"\b(earthquake|quake|tremor|magnitude)\b", re.I)}


def _h(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:14]


def signal_fingerprint(sig):
    keys = ("alertLevel", "severity", "from", "to", "modified", "isCurrent", "affected")
    return _h(repr([sig.get(k) for k in keys]))


def gdacs_group_key(gd_type, event_id):
    return f"gdacs:{gd_type}:{int(event_id)}"


# ---- producer 1: discovery signals -----------------------------------------------------------------------------
def gdacs_signal_candidates(signals, data, cfg, now):
    """-> (candidates, stats). ``signals`` is data/phase1_signals.json. Never raises on a malformed snapshot."""
    stats = {"producer": "discovery_signal_gdacs", "items": 0, "out_of_scope_type": 0, "below_alert": 0, "no_tracked_country": 0,
             "malformed": 0, "snapshot": "ok", "signalGenerated": None}
    src = ((signals or {}).get("sources") or {}).get("gdacs-alerts") or {}
    stats["signalGenerated"] = (signals or {}).get("generated")
    gen = parse_dt(stats["signalGenerated"])
    if not src.get("ok") or not isinstance((src.get("data") or {}).get("features"), list):
        stats["snapshot"] = "gdacs_signal_unavailable"
        return [], stats
    if gen is None or now - gen > timedelta(days=cfg.scope["max_signal_age_days"]):
        stats["snapshot"] = "snapshot_too_old_or_undated"
        return [], stats
    tracked = {c["id"] for c in data["countries"]}
    out = []
    for f in src["data"]["features"]:
        stats["items"] += 1
        p = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(p, dict):
            stats["malformed"] += 1
            continue
        key = GDACS_TO_KEY.get(str(p.get("eventtype")))
        if key not in cfg.event_types:
            stats["out_of_scope_type"] += 1
            continue
        try:
            eid = int(p["eventid"])
        except (KeyError, TypeError, ValueError):
            stats["malformed"] += 1
            continue
        if ALERT_LEVELS.get(p.get("alertlevel"), 0) < cfg.min_alert:
            stats["below_alert"] += 1
            continue
        affected = [{"iso2": a["iso2"].lower(), "name": safe_name(a.get("countryname"))}
                    for a in (p.get("affectedcountries") or []) if isinstance(a, dict) and isinstance(a.get("iso2"), str)]
        in_scope = [a["iso2"] for a in affected if a["iso2"] in cfg.countries and a["iso2"] in tracked]
        if not in_scope:
            stats["no_tracked_country"] += 1
            continue
        sev = p.get("severitydata") if isinstance(p.get("severitydata"), dict) else {}
        sig = {"alertLevel": p.get("alertlevel"), "severity": sev.get("severity"), "from": p.get("fromdate"), "to": p.get("todate"),
               "modified": p.get("datemodified"), "isCurrent": {"true": True, "false": False}.get(str(p.get("iscurrent")).lower()),
               "name": safe_name(p.get("eventname")), "affected": [a["iso2"] for a in affected]}
        gd = str(p.get("eventtype"))
        out.append({"id": f"cand-gdacs-{gd}-{eid}", "producer": "discovery_signal_gdacs", "kind": "hazard_signal",
                    "groupKey": gdacs_group_key(gd, eid), "eventTypeKey": key, "countryIds": in_scope,
                    "gdacs": {"type": gd, "eventId": eid, "episodeId": p.get("episodeid")}, "signal": sig,
                    "signalFingerprint": signal_fingerprint(sig), "signalGenerated": stats["signalGenerated"],
                    "lineage": {"file": "data/phase1_signals.json", "source": "gdacs-alerts"}})
    return out, stats


# ---- producer 2: triage leads ----------------------------------------------------------------------------------
def triage_lead_candidates(monitor_state, data, cfg, now):
    """-> (candidates, stats). Headline leads from completed Haiku triage. Empty while triage is disabled."""
    leads = (monitor_state or {}).get("candidates")
    stats = {"producer": "triage_leads", "items": 0, "out_of_scope": 0, "malformed": 0, "triageStatus": None}
    last = (monitor_state or {}).get("lastOutcome") or {}
    stats["triageStatus"] = last.get("triage") or "unknown"
    if not isinstance(leads, list) or not leads:
        stats["note"] = ("no triaged candidates are stored" + ("; triage has not completed (" + stats["triageStatus"] + ")"
                         if stats["triageStatus"] not in ("COMPLETED", "NO_NEW_HEADLINES") else ""))
        return [], stats
    tracked = {c["id"] for c in data["countries"]}
    out = []
    for c in leads:
        stats["items"] += 1
        if not isinstance(c, dict) or not c.get("id") or not isinstance(c.get("title"), str) or not str(c.get("url", "")).startswith("https://"):
            stats["malformed"] += 1
            continue
        title = c["title"]
        key = next((k for k, rx in LEAD_WORDS.items() if rx.search(title)), None)
        cc = c.get("countryId")
        if (c.get("category") != "NATURAL_HAZARD" or key not in cfg.event_types or cc not in cfg.countries or cc not in tracked):
            stats["out_of_scope"] += 1
            continue
        out.append({"id": "cand-lead-" + _h(str(c["id"])), "producer": "triage_leads", "kind": "report_lead",
                    "groupKey": "lead:" + _h(str(c["id"])), "eventTypeKey": key, "countryIds": [cc],
                    "lead": {"headline": title[:250], "url": c["url"][:600], "publisher": (c.get("publisher") or None),
                             "publisherUrl": c.get("publisherUrl"), "reportPublishedAt": c.get("reportPublishedAt"),
                             "incidentDate": c.get("incidentDate"), "incidentDateBasis": c.get("incidentDateBasis"),
                             "incidentKey": c.get("incidentKey"), "existingId": c.get("existingId"),
                             "triagedAt": c.get("triagedAt"), "model": c.get("model")},
                    "signalFingerprint": _h(repr([title, c.get("reportPublishedAt")])),
                    "lineage": {"file": "data/continuous_monitor_state.json", "source": "haiku_triage"}})
    return out, stats


def match_lead(lead_cand, hazard_cands):
    """The hazard candidate a headline lead is about, or None. Same type, same tracked country, and the report is published
    inside the event's activity window (one day before it begins to two days after it ends)."""
    lead = lead_cand["lead"]
    rp = parse_dt(lead.get("reportPublishedAt"))
    if rp is None:
        return None
    for h in hazard_cands:
        if h["eventTypeKey"] != lead_cand["eventTypeKey"] or not set(h["countryIds"]) & set(lead_cand["countryIds"]):
            continue
        a, b = parse_dt(h["signal"].get("from")), parse_dt(h["signal"].get("to")) or parse_dt(h["signal"].get("from"))
        if a and b and a - timedelta(days=1) <= rp <= b + timedelta(days=2):
            return h
    return None


# ---- cheap eligibility -----------------------------------------------------------------------------------------
def pre_eligibility(cand, cfg, now):
    """(eligible, reason). Uses only what the lead itself states; retrieval and full assessment come later."""
    if cand["kind"] == "report_lead":
        return True, "lead_awaiting_primary_event"
    sig = cand["signal"]
    frm, to = parse_dt(sig.get("from")), parse_dt(sig.get("to"))
    last_activity = max([d for d in (frm, to) if d], default=None)
    if last_activity is None:
        return False, "no_activity_date_in_signal"
    if now - last_activity > timedelta(hours=cfg.fresh["max_event_age_hours"]):
        return False, "stale_event_activity_date"
    return True, "in_scope"


def represented_event_id(data, cand):
    """The id of an existing event that already represents this provider event (any publication status), else None."""
    gd = cand.get("gdacs") or {}
    for e in data["events"]:
        if str(e.get("gdacsEventType")) == gd.get("type") and str(e.get("gdacsEventId")) == str(gd.get("eventId")):
            return e["id"]
    return None


def event_id_for(cfg, cand):
    gd = cand["gdacs"]
    return f"iv-{cfg.raw['event_types'][cand['eventTypeKey']]['id_prefix']}-gdacs-{int(gd['eventId'])}"
