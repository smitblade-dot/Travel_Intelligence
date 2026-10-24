"""Builds the schema-valid event, source and observations for 03AA-04_Investigation_Verification_TI.

Public text is composed from a fixed template and validated fields only. Provider free text never becomes TI prose, so a
hostile or malformed provider string cannot place its own words (or instructions) on the public site. Every published claim
is mapped to the observation that supports it (``claimIds`` on the observation, ``claimAssessments`` on the event); claims
that were not supported are listed with their status and are never presented as fact.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

from .investigation_config import AGENT_NAME
from .investigation_sources import SEV_TEXT_OK, iso_utc, parse_dt
from .investigation_candidates import event_id_for

CAVEATS = {"TROPICAL_CYCLONE": "GDACS tropical-cyclone notifications are automatic and have not been reviewed by human experts before issue.",
           "EARTHQUAKE": "GDACS earthquake notifications are automatic and have not been reviewed by human experts before issue."}
NO_IMPACT = ("TI holds no verified information on this event's effect on travel, transport or safety; the absence of that "
             "information does not mean travel is unaffected.")


def _fmt(d):
    return d.strftime("%-d %b %Y") if d else None


def _claim(assessment, ctype):
    return next((c for c in assessment["claims"] if c["type"] == ctype), None)


def compose(cfg, cand, assessment, st, data):
    """(title, summary, uncertainty) from validated fields only."""
    et = cfg.raw["event_types"][cand["eventTypeKey"]]
    label = et["label"]
    names = {c["id"]: c.get("name") for c in data["countries"]}
    primary = assessment["flags"]["primaryCountry"]
    name_claim = _claim(assessment, "name")
    ident = name_claim["value"] if name_claim and name_claim["status"] == "SUPPORTED" else f"GDACS event {st['eventId']}"
    alert = _claim(assessment, "alert_level")
    alert_txt = f" ({alert['value']} alert)" if alert and alert["status"] == "SUPPORTED" else ""
    title = f"{label.capitalize()} {ident}{alert_txt}, {names.get(primary) or primary.upper()}"[:180]
    listed = [a["name"] for a in st["affected"] if a.get("name")][:8]
    frm, to = parse_dt(st["from"]), parse_dt(st["to"])
    status_claim = _claim(assessment, "status")
    parts = [f"GDACS reports {label} {ident}"]
    if listed:
        parts[0] += f", with {', '.join(listed)} listed by GDACS as affected"
    span = f"active from {_fmt(frm)}" + (f" to {_fmt(to)}" if to and to != frm else "") + " (UTC)"
    parts[0] += f", {span}."
    if status_claim and status_claim["status"] == "SUPPORTED":
        parts.append("GDACS marks the event as ongoing at its last update." if status_claim["value"] == "current"
                     else "GDACS marks the event as ended.")
    inten = _claim(assessment, "intensity")
    if inten and inten["status"] == "SUPPORTED":
        v = inten["value"]
        parts.append(f"GDACS gives a maximum wind speed of {v:.0f} km/h." if et["intensity"] == "wind_kmh" else f"GDACS gives a magnitude of {v:.1f}.")
    if alert and alert["status"] == "SUPPORTED":
        parts.append(f"The alert level shown ({alert['value']}) is GDACS's own classification, not a TI assessment.")
    parts.append(CAVEATS[cand["eventTypeKey"]])
    parts.append("TI has not independently confirmed this event." if assessment["independentChains"] < 2 else
                 "Two independent sources report this event.")
    parts.append(NO_IMPACT)
    notes = [CAVEATS[cand["eventTypeKey"]]]
    excluded = [c for c in assessment["claims"] if c["status"] in ("CONFLICTING", "STALE", "EXCLUDED_UNTRUSTED") and not c["core"]]
    for c in excluded:
        notes.append({"intensity": ("GDACS's intensity text did not pass TI's text checks and is not published." if c["status"] == "EXCLUDED_UNTRUSTED"
                                    else "GDACS's reported intensity values were inconsistent with each other and are not published."),
                      "name": "The provider's event name did not pass TI's text checks and is not shown.",
                      "status": "GDACS's current/ended flag did not match its own dates and is not relied on."}.get(c["type"], f"The {c['type']} claim was excluded."))
    notes.append(NO_IMPACT)
    return title, " ".join(parts), " ".join(dict.fromkeys(notes))


def content_hash(title, summary, status, lifecycle, evidence_hashes):
    raw = json.dumps([title, summary, status, lifecycle, sorted(evidence_hashes.items())], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def public_claims(assessment, obs_ids):
    out = []
    for c in assessment["claims"]:
        item = {"id": c["id"], "type": c["type"], "status": c["status"], "publishedAsFact": bool(c["publish"] and c["status"] == "SUPPORTED"
                and c["type"] != "alert_level"), "attributedTo": c.get("attribution")}
        if c["status"] == "SUPPORTED" and c["publish"]:
            item["observationIds"] = obs_ids
        out.append(item)
    return out


def build_item(cfg, cand, assessment, docs, data, now, pub_id, previous=None):
    """The publication item for one assessed candidate (decision PUBLISH). Pure."""
    got = [d for d in docs if d["status"] == "RETRIEVED"]
    st = got[0]["structured"]
    et = cfg.raw["event_types"][cand["eventTypeKey"]]
    eid = event_id_for(cfg, cand)
    title, summary, unc = compose(cfg, cand, assessment, st, data)
    chash = content_hash(title, summary, assessment["eventStatus"], assessment["lifecycle"], assessment["evidenceHashes"])
    ts, day = iso_utc(now), now.date().isoformat()
    frm = parse_dt(st["from"])
    src_cfg = cfg.sources["gdacs"]
    observations, sources = [], []
    sup = [c["id"] for c in assessment["claims"] if c["status"] == "SUPPORTED" and c["publish"]]
    for d in got:
        s = cfg.sources[d["sourceKey"]]
        oid = f"obs-{eid}-{d['sourceKey']}-{d['evidenceHash'][:6]}"
        sev = st["severityText"] if SEV_TEXT_OK.match(st.get("severityText") or "") else ""
        claim_text = (f"GDACS event {st['eventType']} {st['eventId']}" + (f" '{st['name']}'" if st.get("name") else "") +
                      f": alert level {st['alertLevel'] or 'not stated'}; from {st['from']} to {st['to'] or 'not stated'} (UTC)"
                      + (f"; severity '{sev}'" if sev else "") + f"; last modified {st['modified'] or 'not stated'}; "
                      f"affected countries per provider: {', '.join(a['iso2'].upper() for a in st['affected']) or 'none stated'}.")
        observations.append({
            "id": oid, "recordId": None, "eventId": eid, "sourceId": d["sourceId"], "sourceUrl": d["url"], "extractedClaim": claim_text,
            "publisherName": s["publisher"], "publisherUrl": "https://www.gdacs.org", "independenceGroupId": d["independenceGroupId"],
            "observationStatus": "AUTOMATED_AGGREGATION", "dateChecked": day, "retrievedAt": d["retrievedAt"],
            "publicationDate": st["modified"], "publicationDateStatus": "STATED" if st.get("modified") else "NOT_STATED",
            "originalLanguage": d["originalLanguage"], "translated": False, "translationMethod": "NONE",
            "contentRights": d["contentRights"], "rightsStatus": d["contentRights"], "accessMethod": d["accessMethod"],
            "automationPermission": d["automationPermission"], "legalReviewStatus": "NOT_REQUIRED",
            "uncertainty": s["terms_note"], "claimIds": sup, "evidenceHash": d["evidenceHash"], "publicationId": pub_id,
            "retrievalMode": d.get("retrieval", "live")})
        if not any(x["id"] == d["sourceId"] for x in data["sources"]):
            sources.append({"id": d["sourceId"], "sourceName": s["publisher"], "organisation": s["organisation"],
                            "sourceType": "INTERNATIONAL_ORGANISATION", "url": "https://www.gdacs.org", "active": True,
                            "accessMethod": s["access_method"], "contentRights": s["content_rights"],
                            "automationPermission": s["automation_permission"], "originalLanguage": s["original_language"],
                            "geographicScope": "GLOBAL", "dateChecked": day, "confidence": "MEDIUM",
                            "notes": s["terms_note"]})
    oids = [o["id"] for o in observations]
    history = list((previous or {}).get("statusHistory") or [])
    if previous is None:
        history.append({"date": day, "status": assessment["eventStatus"],
                        "note": f"Published automatically by {AGENT_NAME} after the TI evidence rules passed; "
                                + ("single reporting chain." if assessment["independentChains"] < 2 else "two or more independent chains.")})
    elif not history or history[-1].get("status") != assessment["eventStatus"]:
        history.append({"date": day, "status": assessment["eventStatus"], "note": "Status updated on reassessment of changed evidence."})
    event = {
        "id": eid, "countryId": assessment["flags"]["primaryCountry"], "title": title, "summary": summary,
        "eventType": "NATURAL_HAZARD", "eventSubtype": cand["eventTypeKey"],
        "eventDate": frm.date().isoformat(), "eventTime": frm.strftime("%H:%M:%S"), "eventStatus": assessment["eventStatus"],
        "publicationStatus": "PUBLISHED", "lifecycleStage": assessment["lifecycle"], "confidence": assessment["confidence"],
        "geographicScope": "MULTI_COUNTRY" if len(st["affected"]) > 1 else "NATIONAL", "locationId": None,
        "impactType": [], "affectedInfrastructure": [],
        "conflictingInformation": any(c["status"] == "CONFLICTING" for c in assessment["claims"]),
        "countrySpecificLegalFlag": False, "sensitiveAllegation": False, "personalDataPresent": False,
        "specialCategoryDataPresent": False, "legalReviewStatus": "NOT_REQUIRED", "relatedEventIds": [],
        "supersededByEventId": None, "lastChecked": day, "lastUpdated": ts,
        "reviewDate": (now + timedelta(hours=cfg.publication_cfg["review_hours"])).date().isoformat(),
        "sourceObservationIds": oids, "uncertaintyNotes": unc, "statusHistory": history,
        "gdacsEventType": st["eventType"], "gdacsEventId": st["eventId"], "gdacsEpisodeId": st["episodeId"],
        "claimAssessments": public_claims(assessment, oids),
        "provenance": {"builder": AGENT_NAME, "publicationId": pub_id, "contentHash": chash, "method": "CODE_RULES",
                       "evidenceHashes": assessment["evidenceHashes"], "candidateId": cand["id"]},
    }
    return {"eventId": eid, "event": event, "sources": sources, "observations": observations, "contentHash": chash,
            "needsSources": sorted({o["sourceId"] for o in observations}), "candidateId": cand["id"],
            "groupKey": cand["groupKey"], "lifecycle": assessment["lifecycle"]}
