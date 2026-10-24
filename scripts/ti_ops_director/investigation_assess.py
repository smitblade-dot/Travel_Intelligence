"""Claim-level assessment for 03AA-04_Investigation_Verification_TI. Ordinary code; no model.

Each retrieved fact is assessed on its own: a supported claim never validates an unsupported one. Evidence is judged on its
stated dates and its meaning, not on the fact that it was fetched today. Nothing is inferred from silence: missing evidence
is recorded as MISSING, never as "normal" or "no impact".

Claim statuses
  SUPPORTED                the retrieved evidence supports it
  CONFLICTING              the evidence contradicts itself or another source
  STALE                    the evidence is too old or the provider's flag disagrees with its own dates
  MISSING                  no evidence was retrieved for it
  COLLECTION_FAILURE       the source could not be read (outage, malformed answer, blocked, over budget)
  ATTRIBUTION_UNVERIFIED   a headline or secondhand statement whose original was not retrieved
  EXCLUDED_UNTRUSTED       provider text that looked like an instruction or failed the allowlist
Decision: PUBLISH, HOLD (an exception, with a class: retry or decision) or INELIGIBLE (out of scope or too old; not an exception).
"""
from __future__ import annotations

import re
from datetime import timedelta

from .investigation_sources import iso_utc, parse_dt

NUM = re.compile(r"\d+(?:\.\d+)?")
TC_BANDS = (("tropical depression", 0, 63), ("tropical storm", 63, 119), ("hurricane/typhoon", 119, 10_000))
FAILURE_CLASS = {"UNAVAILABLE": "retry", "BUDGET_EXHAUSTED": "retry", "PARSE_FAILED": "retry",
                 "BLOCKED_BY_ROBOTS": "decision", "NOT_PERMITTED": "decision"}


def claim(cid, ctype, status, *, text=None, value=None, reasons=(), evidence=(), core=False, attribution=None, publish=None):
    return {"id": cid, "type": ctype, "status": status, "text": text, "value": value, "reasons": list(reasons),
            "evidenceIds": list(evidence), "core": core, "attribution": attribution,
            "publish": (status == "SUPPORTED") if publish is None else publish}


def _last_activity(frm, to):
    return max([d for d in (frm, to) if d], default=None)


def tc_band(label_text):
    t = (label_text or "").lower()
    if "tropical depression" in t:
        return "tropical depression"
    if "tropical storm" in t:
        return "tropical storm"
    if "hurricane" in t or "typhoon" in t:
        return "hurricane/typhoon"
    return None


def assess_intensity(st, kind, rng, ev_ids):
    """Numeric value, in range, present in the provider's own text, and (for cyclones) not contradicting its class label."""
    if st.get("severityTextUntrusted"):
        return claim("intensity", "intensity", "EXCLUDED_UNTRUSTED", reasons=["provider_text_failed_allowlist_or_instruction_like"], evidence=ev_ids)
    v, text = st.get("severity"), st.get("severityText") or ""
    if v is None or not isinstance(v, (int, float)) or v != v:
        return claim("intensity", "intensity", "MISSING", reasons=["no_numeric_value"], evidence=ev_ids)
    if not (rng[0] <= v <= rng[1]):
        return claim("intensity", "intensity", "CONFLICTING", reasons=["value_outside_plausible_range"], value=v, evidence=ev_ids)
    nums = [float(n) for n in NUM.findall(text)]
    if not any(abs(n - v) <= max(0.51, abs(v) * 0.005) for n in nums):
        return claim("intensity", "intensity", "CONFLICTING", reasons=["value_not_stated_in_provider_text"], value=v, evidence=ev_ids)
    if kind == "wind_kmh":
        band = tc_band(text)
        if band:
            lo, hi = next((a, b) for n, a, b in TC_BANDS if n == band)
            if not (lo <= v < hi):
                return claim("intensity", "intensity", "CONFLICTING", value=v, evidence=ev_ids,
                             reasons=["class_label_contradicts_wind_value"])
    return claim("intensity", "intensity", "SUPPORTED", value=v, evidence=ev_ids, attribution="GDACS")


def compare_evidence(docs):
    """Contradictions between independent retrieved documents about the same event: start date more than a day apart, or
    intensity values that differ beyond tolerance. -> list of reason codes."""
    got = [d for d in docs if d.get("status") == "RETRIEVED"]
    reasons = []
    for i, a in enumerate(got):
        for b in got[i + 1:]:
            fa, fb = parse_dt(a["structured"].get("from")), parse_dt(b["structured"].get("from"))
            if fa and fb and abs(fa - fb) > timedelta(days=1):
                reasons.append("sources_disagree_on_start_date")
            va, vb = a["structured"].get("severity"), b["structured"].get("severity")
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and abs(va - vb) > max(0.5, 0.05 * abs(va)):
                reasons.append("sources_disagree_on_intensity")
    return sorted(set(reasons))


def assess_hazard(cand, docs, cfg, data, now, reassess=False):
    """-> assessment dict. ``docs`` are evidence documents (investigation_sources) for this candidate's event.

    ``reassess`` is True for an event TI already published: the freshness and alert-threshold gates that decide whether to publish
    a NEW event do not apply (an event gets older; that moves its lifecycle, it does not unpublish it)."""
    et = cfg.raw["event_types"][cand["eventTypeKey"]]
    claims, reasons = [], []
    out = {"candidateId": cand["id"], "decision": "HOLD", "exceptionClass": None, "reasons": reasons, "claims": claims,
           "independentChains": 0, "chains": [], "eventStatus": None, "confidence": None, "lifecycle": None,
           "changedSinceSignal": [], "flags": {}, "assessedAt": iso_utc(now), "evidenceHashes": {}}
    got = [d for d in docs if d.get("status") == "RETRIEVED"]
    if not got:
        statuses = sorted({d.get("status") for d in docs} or {"UNAVAILABLE"})
        cls = "decision" if any(FAILURE_CLASS.get(s) == "decision" for s in statuses) else "retry"
        claims.append(claim("occurrence", "occurrence", "COLLECTION_FAILURE", core=True,
                            reasons=[f"{d.get('status')}:{d.get('error')}" for d in docs] or ["no_evidence_attempted"]))
        reasons.append("collection_failure:" + ",".join(statuses))
        out.update(exceptionClass=cls)
        return out
    out["evidenceHashes"] = {d["sourceKey"]: d["evidenceHash"] for d in got}
    ev_ids = [d["evidenceId"] for d in got]
    st = got[0]["structured"]
    # --- relevance: provider's own list of affected countries against the scope --------------------------------
    tracked = {c["id"] for c in data["countries"]}
    in_scope = [a for a in st["affected"] if a["iso2"] in cfg.countries and a["iso2"] in tracked]
    sig = cand.get("signal") or {}
    for key, sk in (("alertLevel", "alertLevel"), ("severity", "severity"), ("from", "from"), ("to", "to"), ("isCurrent", "isCurrent")):
        if cand["kind"] == "hazard_signal" and sig.get(key) != st.get(sk) and not (sig.get(key) is None and st.get(sk) is None):
            try:
                if key == "severity" and abs(float(sig.get(key)) - float(st.get(sk))) < 1e-6:
                    continue
            except (TypeError, ValueError):
                pass
            out["changedSinceSignal"].append(key)
    if not in_scope:
        claims.append(claim("location", "location", "CONFLICTING", core=True, evidence=ev_ids, reasons=["no_tracked_country_in_evidence"]))
        out.update(decision="INELIGIBLE")
        reasons.append("no_tracked_country_in_evidence")
        return out
    # --- dates ------------------------------------------------------------------------------------------------------
    frm, to, mod = parse_dt(st.get("from")), parse_dt(st.get("to")), parse_dt(st.get("modified"))
    tol = timedelta(minutes=cfg.fresh["future_tolerance_minutes"])
    period_reasons = []
    if frm is None:
        period_reasons.append("start_date_missing_or_unreadable")
    elif frm > now + tol:
        period_reasons.append("start_date_in_the_future")
    if to is not None and frm is not None and to < frm:
        period_reasons.append("end_date_before_start_date")
    if to is not None and to > now + timedelta(days=cfg.fresh["max_forecast_end_days"]):
        period_reasons.append("end_date_implausibly_far_ahead")
    if period_reasons:
        claims.append(claim("period", "period", "CONFLICTING" if frm is not None else "MISSING", core=True, evidence=ev_ids, reasons=period_reasons))
        reasons += period_reasons
        out.update(exceptionClass="retry")
        return out
    last = _last_activity(frm, to)
    stale_limit = timedelta(hours=cfg.fresh["max_event_age_hours"])
    if not reassess and now - last > stale_limit:
        why = ("old_event_republished_as_new" if mod and mod - last > timedelta(days=cfg.fresh["republish_gap_days"])
               else "stale_event_activity_date")
        claims.append(claim("occurrence", "occurrence", "STALE", core=True, evidence=ev_ids, reasons=[why]))
        out.update(decision="INELIGIBLE")
        reasons.append(why)
        return out
    if not reassess and cfg.min_alert and st.get("alertLevel") is not None and {"Green": 1, "Orange": 2, "Red": 3}[st["alertLevel"]] < cfg.min_alert:
        claims.append(claim("alert_level", "alert_level", "SUPPORTED", value=st["alertLevel"], evidence=ev_ids, publish=False,
                            reasons=["below_scope_threshold"]))
        out.update(decision="INELIGIBLE")
        reasons.append("alert_below_scope_threshold")
        return out
    conflicts = compare_evidence(docs)
    if conflicts:
        claims.append(claim("occurrence", "occurrence", "CONFLICTING", core=True, evidence=ev_ids, reasons=conflicts))
        reasons += conflicts
        out.update(exceptionClass="retry")
        return out
    # --- supported core claims ----------------------------------------------------------------------------------
    claims.append(claim("occurrence", "occurrence", "SUPPORTED", core=True, evidence=ev_ids, attribution="GDACS",
                        value={"eventId": st["eventId"], "type": et["label"]}))
    claims.append(claim("period", "period", "SUPPORTED", core=True, evidence=ev_ids, attribution="GDACS",
                        value={"from": iso_utc(frm), "to": iso_utc(to)}))
    named = [a for a in in_scope if a["name"]]
    claims.append(claim("location", "location", "SUPPORTED", core=True, evidence=ev_ids, attribution="GDACS",
                        value=[a["iso2"] for a in in_scope]) if named else
                  claim("location", "location", "EXCLUDED_UNTRUSTED", core=True, evidence=ev_ids, reasons=["country_name_failed_allowlist"]))
    if not named:
        out.update(exceptionClass="retry")
        reasons.append("country_name_failed_allowlist")
        return out
    # --- secondary claims, each on its own ------------------------------------------------------------------------
    if st.get("nameInstructionLike") or st.get("name") is None:
        claims.append(claim("name", "name", "EXCLUDED_UNTRUSTED", evidence=ev_ids, reasons=["provider_name_failed_allowlist_or_instruction_like"]))
        out["flags"]["untrustedText"] = True
    else:
        claims.append(claim("name", "name", "SUPPORTED", value=st["name"], evidence=ev_ids, attribution="GDACS"))
    claims.append(assess_intensity(st, et["intensity"], et["intensity_range"], ev_ids))
    if st.get("alertLevel"):
        claims.append(claim("alert_level", "alert_level", "SUPPORTED", value=st["alertLevel"], evidence=ev_ids, attribution="GDACS",
                            reasons=["provider_own_assessment_not_a_ti_finding"]))
    else:
        claims.append(claim("alert_level", "alert_level", "MISSING", reasons=["no_valid_alert_level"], evidence=ev_ids))
    idle = now - last
    if st.get("isCurrent") is True and idle > timedelta(hours=cfg.fresh["current_flag_max_idle_hours"]):
        claims.append(claim("status", "status", "STALE", evidence=ev_ids, publish=False,
                            reasons=["provider_marks_current_but_dates_show_no_recent_activity"]))
        active = False
    else:
        active = st.get("isCurrent") is True
        claims.append(claim("status", "status", "SUPPORTED" if st.get("isCurrent") is not None else "MISSING", value="current" if active else "ended",
                            evidence=ev_ids, attribution="GDACS", publish=st.get("isCurrent") is not None))
    claims.append(claim("impact", "impact", "MISSING", reasons=["no_in_remit_statement_retrieved"], publish=False))
    # --- independence and status -----------------------------------------------------------------------------------
    chains = sorted({d["independenceGroupId"] for d in got})
    out.update(chains=chains, independentChains=len(chains))
    rules = cfg.raw["claim_rules"]["status_rules"]
    single_only = all(d["sourceId"] in rules["never_confirmed_from"] for d in got)
    status = rules["two_or_more_independent_chains"] if len(chains) >= 2 else rules["single_independent_chain"]
    if st.get("isCurrent") is False:
        status = "RESOLVED"
    out.update(eventStatus=status, confidence="MEDIUM" if (len(chains) < 2 or single_only) else "HIGH")
    age_h = idle.total_seconds() / 3600
    out["lifecycle"] = ("ALERT" if active and age_h <= cfg.fresh["alert_until_hours"] else
                        "ARCHIVED" if age_h > cfg.fresh["archive_after_hours"] else "CURRENT")
    out["flags"].update(active=active, lastActivity=iso_utc(last), primaryCountry=in_scope[0]["iso2"], countries=[a["iso2"] for a in in_scope])
    out.update(decision="PUBLISH")
    return out


def attach_leads(assessment, leads):
    """Headline leads about the same event are recorded as attributed, unverified statements. They never count as an
    independent chain and never reach public text: only retrieved evidence can."""
    notes = []
    for ld in leads:
        notes.append(claim(f"lead-{ld['id'][-8:]}", "attributed_report", "ATTRIBUTION_UNVERIFIED", publish=False,
                           text=ld["lead"]["headline"], attribution=ld["lead"].get("publisher") or "original publisher not identified",
                           reasons=["headline_only_original_not_retrieved", "google_news_redirect_not_a_publisher_url"]))
    assessment["claims"].extend(notes)
    assessment["leadsAttached"] = [ld["id"] for ld in leads]
    return assessment
