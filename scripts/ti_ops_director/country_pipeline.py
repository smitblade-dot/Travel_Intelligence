"""Item building and assessment for one country/category task: extractive claims first, model only for what code
cannot cover, independent verification of model-written claims, then the publication rules.

Kept apart from the engine so the whole path (collect -> claims -> rules -> publication items) can be tested on
representative cases without a network, a model or git.
"""
from __future__ import annotations

from .country_draft import (VERIFIER_PROMPT, build_verify_user, climate_item, reference_item, verify_valid)
from .country_publish import build_publication_item
from .country_rules import assess_item, corroborate, extractive_by_topic, load_registry

ACTIONS = {
    "source_not_in_registry": ("Add this source family to config/source_registry.json after due diligence, or leave these claims held. "
                               "It may corroborate but cannot establish a claim alone.", True),
    "registry_tier_not_allowed": ("The source register ranks this source below the permitted tier.", True),
    "registry_status_not_allowed": ("The source register does not yet mark this source ACTIVE or INTEGRATION_READY.", True),
    "automation_not_permitted": ("The source register or source record does not permit automation.", True),
    "no_dated_source_within_freshness_limit": ("Held until a registry-backed source states a publication date inside its freshness limit. No action unless it persists.", False),
    "source_not_retrieved": ("A supporting source could not be retrieved this run; retried automatically.", False),
    "conflict": ("Sources disagree on this point. Nothing was published or changed; review which is correct if it persists.", True),
    "conflicts_with_existing_record": ("The new text points the opposite way from an existing record. The existing record was kept untouched.", True),
    "uncertain_not_publishable": ("Only VERIFIED claims are published; this one is UNCERTAIN.", False),
    "model_claim_unverified": ("The model-written claim did not pass independent verification.", False),
    "qualifier_lost": ("The model-written claim dropped a hedge, negation or name from the source.", False),
    "current_claim_requires_event_workflow": ("A present-tense condition belongs in Current Intelligence (events), not the baseline.", False),
    "existing_record_not_owned": ("A hand-written or earlier record already uses this id; it was not touched.", True),
    "confidence_below_publishable": ("Evidence was not strong enough for MEDIUM confidence.", False),
    "no_publishable_claim": ("No sentence passed all rules.", False),
    "unsupported_by_source": ("The claim is not supported by a retrieved source.", False),
}


def source_exceptions(inputs, evidence):
    out = []
    for e in evidence:
        if e["status"] in ("RETRIEVED",):
            continue
        human = e["status"] in ("BLOCKED_BY_ROBOTS", "PARSE_FAILED")
        out.append({"key": f"{inputs['countryId']}:{inputs['category']}:source:{e['sourceKey']}:{e['status']}", "scope": "source",
                    "countryId": inputs["countryId"], "category": inputs["category"], "topic": None, "reasons": ["source_" + e["status"].lower()],
                    "detail": f"{e['sourceKey']}: {e.get('error') or e['status']}",
                    "action": ("Source cannot be used as configured: " + (e.get("error") or e["status"]) +
                               (". Needs Marc's decision (robots exception or another source)." if human else ". Retried automatically each run.")),
                    "requiresHuman": human})
    return out


def topic_exceptions(inputs, topic, assessed):
    """Exceptions for one topic. 'Source not in the register' is one exception per source (not per topic): the decision is the same."""
    out = []
    if assessed.get("decision") == "REFERENCE_ONLY":
        return [{"key": f"{inputs['countryId']}:{inputs['category']}:{topic}:no_external_source", "scope": "topic", "countryId": inputs["countryId"],
                 "category": inputs["category"], "topic": topic, "reasons": ["no_external_source_for_reference_value"], "decision": "REFERENCE_ONLY",
                 "detail": "TI's own reference fields hold a value, but no register-backed external source was retrieved for it",
                 "action": "Add an authoritative source for this value to the source register (for example the IEC world plugs list); until then it is not republished as a sourced record.",
                 "requiresHuman": True}]
    excluded = assessed.get("excluded", [])
    reasons = set(assessed.get("reasons", [])) | {r for e in excluded for r in (e.get("reasons") or [])}
    reasons -= {"duplicate_of_existing"}
    if "source_not_in_registry" in reasons:
        reasons.discard("source_not_in_registry")
        srcs = sorted({s for e in excluded if "source_not_in_registry" in (e.get("reasons") or []) for s in e.get("sources", [])})
        for sk in srcs:
            out.append({"key": f"{inputs['countryId']}:{inputs['category']}:registry:{sk}", "scope": "source_register",
                        "countryId": inputs["countryId"], "category": inputs["category"], "topic": None, "reasons": ["source_not_in_registry"],
                        "detail": f"{sk} would support {topic} (and possibly other topics)", "action": ACTIONS["source_not_in_registry"][0],
                        "requiresHuman": True})
    if reasons:
        acts = [ACTIONS.get(r, (r, False)) for r in sorted(reasons)]
        out.append({"key": f"{inputs['countryId']}:{inputs['category']}:{topic}:" + "+".join(sorted(reasons)), "scope": "topic",
                    "countryId": inputs["countryId"], "category": inputs["category"], "topic": topic, "reasons": sorted(reasons),
                    "decision": assessed["decision"],
                    "detail": "; ".join((e.get("text", "")[:100] + " -> " + ",".join(e.get("reasons") or [e.get("reason", "")])) for e in excluded[:3]),
                    "action": " ".join(a for a, _ in acts), "requiresHuman": any(h for _, h in acts)})
    return out


def unique_exceptions(items):
    seen = {}
    for e in items:
        seen.setdefault(e["key"], e)
    return list(seen.values())


def build_items(builder, inputs, country, evidence, passages, per_topic, topics, today, data):
    """-> (items, missing, notes). Items carry claims; nothing here decides publication."""
    cfg = builder.cfg
    ev_code = {e["code"]: e for e in evidence}
    items, missing = [], []
    all_passages = [p for e in evidence if e["status"] == "RETRIEVED" for p in e["passages"]]
    by_topic = extractive_by_topic(all_passages, ev_code, topics, cfg.publication_cfg or {"quality": {"min_words": 4, "max_words": 90}})
    for tname, tdef in topics.items():
        kind = tdef["kind"]
        if kind == "deterministic":
            it = climate_item(evidence, tdef, today)
            if it:
                items.append(it | {"method": "COMPUTED", "excluded": []})
            else:
                missing.append({"topic": tname, "reason": "the climatology source was not retrieved", "origin": "collector"})
        elif kind == "reference":
            it = reference_item(country, tdef)
            if it:
                items.append(it | {"method": "REFERENCE", "excluded": []})
        elif kind == "outlook":
            missing.append({"topic": tname, "origin": "collector",
                            "reason": "No approved current seasonal-outlook source is configured for the next 4-6 weeks. "
                                      "Model knowledge is not used as evidence of current conditions."})
        elif kind == "model":
            claims, excluded = by_topic[tname]
            if claims:
                items.append({"topic": tname, "title": tdef["subcategory"], "claims": claims, "excluded": excluded, "method": "EXTRACTIVE",
                              "uncertainty": "Wording is the source's own. Other nationalities' governments may advise differently.",
                              "candidateRecord": True})
    corroborate([c for it in items if it["method"] == "EXTRACTIVE" for c in it["claims"]])
    return items, missing


def assess_all(builder, items, evidence, data, inputs, today, pub_id):
    """Run the publication rules over every item. -> (assessments, publication_items)."""
    cfg = builder.cfg
    pub_cfg = cfg.publication_cfg
    if not pub_cfg:
        return [], []
    registry = load_registry(builder.root)
    topics = cfg.category_defs[inputs["category"]]["topics"]
    existing = [r for r in data["records"] if r.get("status", "ACTIVE") == "ACTIVE"]
    assessments, pub_items = [], []
    for it in items:
        if it.get("candidateRecord") is False:
            assessments.append({"topic": it["topic"], "decision": "REFERENCE_ONLY", "claims": [], "excluded": [], "reasons": [], "confidence": None})
            continue
        own_id = f"{inputs['countryId']}-{inputs['category'].lower()}-" + it["topic"].lower().replace("_", "-")
        others = [r for r in existing if r["id"] != own_id]
        a = assess_item(it, evidence, cfg.sources, registry, pub_cfg, today, others, inputs["countryId"], inputs["category"])
        assessments.append(a)
        if a["decision"] == "PUBLISH":
            pub_items.append(build_publication_item(inputs["countryId"], inputs["category"], it["topic"], topics[it["topic"]], a,
                                                    evidence, cfg.sources, today, pub_cfg, pub_id))
    return assessments, pub_items


def verify_model_claims(builder, spend, items, passages, batch_key):
    """Independent second pass over model-written claims. Cost goes through the same gateway. Sets claim['verification']."""
    claims = [c for it in items if it.get("method") == "MODEL" for c in it["claims"]]
    if not claims or spend is None:
        return {"calls": 0, "claims": 0}
    by_id = {p["id"]: p for p in passages}
    user = build_verify_user(claims, by_id)
    res = spend.draft(VERIFIER_PROMPT, user, batch_key + ":verify", validator=lambda o: verify_valid(o, len(claims)),
                      max_output_tokens=int(builder.cfg.raw["spending"]["max_output_tokens"]))
    spend.checkpoint()
    info = {"calls": res.calls, "claims": len(claims), "status": res.status, "reason": res.reason, "model": res.model,
            "estimatedUsd": res.estimated_usd}
    if res.status not in ("ok", "cached"):
        return info
    verdicts = {v["index"]: v for v in res.output["verdicts"]}
    partial = [i for i, v in verdicts.items() if v["verdict"] == "PARTIAL"]
    if partial and builder.cfg.raw["spending"].get("sonnet_escalation_enabled"):
        sub = [claims[i] for i in partial]
        r2 = spend.draft(VERIFIER_PROMPT, build_verify_user(sub, by_id), batch_key + ":verify2",
                         validator=lambda o: verify_valid(o, len(sub)), want_tier="sonnet", escalation_reason="conflicting_evidence",
                         max_output_tokens=int(builder.cfg.raw["spending"]["max_output_tokens"]))
        spend.checkpoint()
        info["escalated"] = {"model": r2.model, "status": r2.status, "claims": len(sub)}
        if r2.status in ("ok", "cached"):
            for j, v in enumerate(r2.output["verdicts"]):
                verdicts[partial[v["index"]]] = v
    for i, c in enumerate(claims):
        v = verdicts[i]
        c["verification"] = {"verdict": v["verdict"], "problem": v.get("problem", "")}
    return info
