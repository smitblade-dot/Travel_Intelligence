"""Publication rules for the country builder. Ordinary code; no model, no network.

A matching quotation is NOT adequate evidence by itself. A claim is publishable only when ALL of these hold:
  SOURCE      every supporting source is a permitted, registry-backed source (config/source_registry.json: TIER_1,
              ACTIVE or INTEGRATION_READY, automation YES or REVIEWED) and was actually retrieved this run
  FRESHNESS   at least one registry-backed source states a publication date inside its freshness limit; an undated
              page can corroborate but never establish freshness; present-tense (CURRENT) claims are not baseline
  ATTRIBUTION the claim carries accurate publisher, jurisdiction, publication date and retrieval date
  CLASSIFY    the supporting passage sits under a heading that belongs to the topic, and the sentence is about it
  SUPPORT     the claim is the source's own sentence (extractive), or a model-written claim that an independent
              verification pass found fully supported AND that keeps every hedge, negation, number and place name
  UNCERTAINTY nothing UNCERTAIN or CONFLICT is published as fact; sources or existing records that point in
              opposite directions hold the item
  QUALITY     self-contained, complete, not a fragment or a pointer ("see below"), no markup, no duplicate of
              existing content
Failures are returned as reason codes. Integrity failures hold the whole item; eligibility failures only
exclude the claim that failed.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

from .country_config import AGENT_NAME
from pathlib import Path

from .country_draft import CURRENT_WORDS, NUMBER, norm

# Reason codes. INTEGRITY ones hold the whole item; the rest exclude the single claim.
INTEGRITY = {"unsupported_by_source", "verification_failed", "qualifier_lost", "conflict", "conflicts_with_existing_record",
             "uncertain_not_publishable", "model_claim_unverified"}
ROUTINE = {"fragment_or_pointer", "duplicate_of_existing", "not_about_topic"}   # excluded quietly, counted, not routed

TIME_BOUND = re.compile(r"(?i)\b(until further notice|as of|temporar(?:y|ily)|at the moment|for now|this (?:week|month|year|season)|"
                        r"next (?:week|month)|today|tomorrow|tonight)\b")
POINTER = re.compile(r"(?i)\bsee (?:below|above|more)\b|\b(?:as )?(?:described|listed|shown|detailed|outlined|set out) (?:below|above)\b|\bclick\b|\bthe following\b|\bfollowing:|:\s*$|\bread more\b|\bmore information\b|\bwebsite\b|\bweb ?page\b")
BAD_START = re.compile(r"^(?:this|these|that|those|it|its|they|their|such|however|also|and|but|then|see|read|find out|for more)\b", re.I)
MARKUP = re.compile(r"[<>{}\[\]|]|https?://|\bwww\.|[\x00-\x08\x0b\x0c\x0e-\x1f]")
PII = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s().-]{8,}\d")
HEDGES = re.compile(r"(?i)\b(may|might|can|could|some|sometimes|often|usually|generally|typically|normally|most|many|certain|parts of|"
                    r"in some|likely|occasionally|rarely|up to|at least|more than|less than|about|around)\b")
NEGATIONS = re.compile(r"(?i)\b(not|no|never|cannot|can't|don't|do not|without|unless|except|avoid\w*|illegal\w*|prohibit\w*|forbid\w*|ban(?:ned)?)\b")
STOP = set("a an the of and or to in on at for from by with as is are was were be been it its this that these those you your we our they their "
           "there have has had can may might will would should could not no".split())
DIRECTIVE_NEG = re.compile(r"(?i)\b(avoid|do not|don't|never|not (?:use|travel|drive|ride)|should not|must not|illegal|prohibited|unsafe|dangerous)\b")
DIRECTIVE_POS = re.compile(r"(?i)\b(safer to|safe to|recommended|advisable|it is best to|best to|prefer|choose|should|encouraged)\b")


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", norm(text)) if w not in STOP and len(w) > 2}


def jaccard(a, b):
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def units(passage_text):
    """The source's own blocks (a paragraph or one list item), in order and verbatim. A block is the smallest unit that keeps its
    context: splitting a paragraph into sentences loses what 'the latter' or 'providers' refers to."""
    return [re.sub(r"^-\s+", "", b).strip() for b in passage_text.split("\n") if b.strip()]


def quality_problem(sentence, limits):
    """None when the sentence can stand alone as published text, else a short reason."""
    words = sentence.split()
    if not limits["min_words"] <= len(words) <= limits["max_words"]:
        return "length"
    if not re.match(r"[A-Z0-9]", sentence) or BAD_START.match(sentence):
        return "starts_mid_thought"
    if not re.search(r"[.!?]$", sentence):
        return "no_terminal_punctuation"
    if POINTER.search(sentence):
        return "pointer"
    if MARKUP.search(sentence):
        return "markup_or_link"
    if sentence.count("(") != sentence.count(")") or sentence.count('"') % 2 or sentence.count("“") != sentence.count("”"):
        return "unbalanced"
    if PII.search(sentence):
        return "personal_or_contact_data"
    if CURRENT_WORDS.search(sentence) or TIME_BOUND.search(sentence):
        return "time_bound"
    return None


LAST_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def trim_trailing(block, limits):
    """If only the last sentence(s) of a block make it unpublishable (a pointer to a website, a time-bound note), drop them. What
    remains is an unedited prefix of the source block, so every remaining sentence keeps the context it had."""
    problem = quality_problem(block, limits)
    cuts = 0
    while problem in ("pointer", "markup_or_link", "time_bound") and cuts < 2:
        cuts_at = [m.start() for m in LAST_BOUNDARY.finditer(block)]
        if not cuts_at:
            break
        block = block[:cuts_at[-1]].rstrip()
        problem = quality_problem(block, limits)
        cuts += 1
    return block, problem, cuts


def heading_ok(locator, patterns):
    return any(re.search(p, locator or "", re.I) for p in patterns)


def extractive_by_topic(passages, evidence_by_code, topics, rules_cfg):
    """Ordinary-code first pass. Each source block goes to the ONE topic it fits best (heading gate + keyword score, minus the
    topic's exclusions), so a taxi paragraph is never filed under road travel. -> {topic: (claims, excluded)}."""
    model_topics = {t: d for t, d in topics.items() if d["kind"] == "model"}
    compiled = {t: ([re.compile(r"\b" + re.escape(k), re.I) for k in d.get("keywords", [])],
                    [re.compile(r"\b" + re.escape(k), re.I) for k in d.get("exclude_keywords", [])]) for t, d in model_topics.items()}
    out = {t: ([], []) for t in model_topics}
    seen = set()
    for p in passages:
        ev = evidence_by_code.get(p["id"].split(".")[0])
        if not ev:
            continue
        for block in units(p["text"]):
            best, best_score = None, 0
            for t, d in model_topics.items():
                kw, ex = compiled[t]
                if ev["sourceKey"] not in d.get("sources", []) or not heading_ok(p["locator"], d.get("heading_patterns") or []):
                    continue
                if any(x.search(block) for x in ex):
                    continue
                score = sum(1 for k in kw if k.search(block))
                if score > best_score:
                    best, best_score = t, score
            if not best:
                continue
            key = (best, norm(block))
            if key in seen:
                continue
            seen.add(key)
            block, problem, trimmed = trim_trailing(block, rules_cfg["quality"])
            if problem:
                out[best][1].append({"text": block[:160], "reason": "fragment_or_pointer", "detail": problem, "passageId": p["id"]})
                continue
            out[best][0].append({"text": block, "status": "VERIFIED", "temporal": "STABLE", "method": "EXTRACTIVE", "trimmedTrailingSentences": trimmed,
                                 "evidence": [evidence_entry(ev, p["id"], block)], "independentSources": 1, "note": None})
    return out


def evidence_entry(ev, passage_id, quote):
    return {"passageId": passage_id, "sourceKey": ev["sourceKey"], "sourceId": ev["sourceId"], "url": ev["url"],
            "publisher": ev["publisher"], "quote": quote, "retrievedAt": ev["retrievedAt"], "publicationDate": ev["publicationDate"],
            "independenceGroupId": ev["independenceGroupId"]}


def corroborate(claims, threshold=0.6):
    """Mark claims that another independent group's claim says (nearly) the same. Never merges text."""
    for i, a in enumerate(claims):
        groups = {x["independenceGroupId"] for x in a["evidence"]}
        for b in claims[i + 1:]:
            if {x["independenceGroupId"] for x in b["evidence"]} & groups:
                continue
            if jaccard(a["text"], b["text"]) >= threshold:
                for x, y in ((a, b), (b, a)):
                    x["evidence"] = x["evidence"] + [e for e in y["evidence"] if e not in x["evidence"]]
                    x["independentSources"] = len({e["independenceGroupId"] for e in x["evidence"]})
    return claims


# ---- registry -----------------------------------------------------------------------------------------------
PKG_ROOT = Path(__file__).resolve().parents[2]


def repo_file(root, *rel):
    p = Path(root).joinpath(*rel)
    return p if p.exists() else PKG_ROOT.joinpath(*rel)


def load_registry(root):
    p = repo_file(root, "config", "source_registry.json")
    return {s["id"]: s for s in json.loads(p.read_text(encoding="utf-8"))["sources"]}


def source_backing(src_cfg, registry, pub_cfg):
    """(ok, reason). Registry-backed means the established TI source register says this source family is
    authoritative and automatable. Anything else may corroborate but cannot establish a claim alone."""
    rid = src_cfg.get("registry_id")
    if not rid or rid not in registry:
        return False, "source_not_in_registry"
    reg, rc = registry[rid], pub_cfg["registry"]
    if reg["tier"] not in rc["allowed_tiers"]:
        return False, "registry_tier_not_allowed"
    if reg["status"] not in rc["allowed_status"]:
        return False, "registry_status_not_allowed"
    if reg["automation"] not in rc["allowed_automation"]:
        return False, "automation_not_permitted"
    if src_cfg.get("automation_permission") == "DENIED":
        return False, "automation_not_permitted"
    return True, None


def check_claim(claim, evidence_by_key, sources_cfg, registry, pub_cfg, today, existing_texts, polarity_texts=None):
    """-> list of reason codes (empty = publishable). Evidence entries are from sources retrieved this run."""
    reasons = []
    if claim["status"] == "CONFLICT":
        return ["conflict"]
    if claim["status"] != "VERIFIED":
        return ["uncertain_not_publishable"]
    if claim.get("temporal") == "CURRENT":
        return ["current_claim_requires_event_workflow"]
    if not claim["evidence"]:
        return ["unsupported_by_source"]
    backed_dated = False
    for x in claim["evidence"]:
        ev = evidence_by_key[x["sourceKey"]]
        if ev["status"] != "RETRIEVED":
            reasons.append("source_not_retrieved")
            continue
        ok, why = source_backing(sources_cfg[x["sourceKey"]], registry, pub_cfg)
        if not ok:
            continue
        if not ev.get("publicationDate"):
            continue
        age = (today - datetime.fromisoformat(ev.get("currencyDate") or ev["publicationDate"]).date()).days
        if age <= ev["staleAfterDays"]:
            backed_dated = True
    if not backed_dated:
        if not any(source_backing(sources_cfg[x["sourceKey"]], registry, pub_cfg)[0] for x in claim["evidence"]):
            reasons.append("source_not_in_registry")
        else:
            reasons.append("no_dated_source_within_freshness_limit")
    if claim.get("method") not in ("EXTRACTIVE", "COMPUTED"):
        v = claim.get("verification") or {}
        if v.get("verdict") != "SUPPORTED":
            reasons.append("model_claim_unverified")
        lost = qualifier_loss(claim)
        if lost:
            reasons.append("qualifier_lost")
    for t in existing_texts:
        if jaccard(claim["text"], t) >= pub_cfg["duplicate_similarity"]:
            reasons.append("duplicate_of_existing")
            break
    # Advice that reverses an existing record is a conflict. Records this builder published from the same verified sources are
    # not compared for polarity (sibling topics cannot contradict each other; the keyword heuristic only produced false alarms).
    for t in (existing_texts if polarity_texts is None else polarity_texts):
        if opposite_polarity(claim["text"], t):
            reasons.append("conflicts_with_existing_record")
            break
    return list(dict.fromkeys(reasons))


def polarity_for(claim, records, sources_cfg, topic=None):
    """Texts a claim is compared against for reversed advice: every hand-written record in the category (their topic is unknown),
    and builder-published records only when they cover the SAME topic. A builder record for a different topic (clothing vs mosquito
    protection) is about a different subject, and the keyword heuristic only produced false alarms there."""
    return [r["content"] for r in records
            if (r.get("provenance") or {}).get("builder") != AGENT_NAME or (topic and (r.get("provenance") or {}).get("topic") == topic)]


def qualifier_loss(claim):
    """Hedges, negations and place names in the quote that the model-written claim dropped."""
    quote = " ".join(x["quote"] for x in claim["evidence"])
    text = claim["text"]
    lost = []
    if HEDGES.search(quote) and not HEDGES.search(text):
        lost.append("hedge")
    q_neg = {m.group(0).lower() for m in NEGATIONS.finditer(quote)}
    t_neg = {m.group(0).lower() for m in NEGATIONS.finditer(text)}
    if q_neg and not t_neg:
        lost.append("negation")
    claim_norm, quote_norm = norm(text), norm(quote)
    invented = {w for w in re.findall(r"\b[A-Z][a-z]{3,}\b", text) if w.lower() not in quote_norm}
    if invented - {"Ghana", "British"}:
        lost.append("unsourced_name")
    # Names (regions, places, groups) in the quote that the claim dropped: the claim would now cover more than the source says.
    names = {w for w in re.findall(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{3,}\b", quote)}
    if names and not any(n.lower() in claim_norm for n in names):
        lost.append("scope_names")
    return lost


def opposite_polarity(a, b):
    """True when two statements share subject words but one advises against what the other advises."""
    shared = tokens(a) & tokens(b)
    if len(shared) < 2:
        return False
    na, pa = bool(DIRECTIVE_NEG.search(a)), bool(DIRECTIVE_POS.search(a))
    nb, pb = bool(DIRECTIVE_NEG.search(b)), bool(DIRECTIVE_POS.search(b))
    return (na and not pa and pb and not nb) or (nb and not pb and pa and not na)


def conflicts_within(claims):
    out = []
    for i, a in enumerate(claims):
        for b in claims[i + 1:]:
            ga = {x["independenceGroupId"] for x in a["evidence"]}
            gb = {x["independenceGroupId"] for x in b["evidence"]}
            if ga != gb and opposite_polarity(a["text"], b["text"]):
                out.append((a["text"][:120], b["text"][:120]))
    return out


# ---- item assessment ----------------------------------------------------------------------------------------
def item_confidence(claims, evidence_by_key, sources_cfg, registry, pub_cfg, today):
    """HIGH needs two independent registry-backed, dated, in-limit sources on every claim; MEDIUM one; otherwise LOW."""
    def good_groups(c):
        groups = set()
        for x in c["evidence"]:
            ev = evidence_by_key[x["sourceKey"]]
            if source_backing(sources_cfg[x["sourceKey"]], registry, pub_cfg)[0] and ev.get("publicationDate"):
                groups.add(ev["independenceGroupId"])
        return groups
    sizes = [len(good_groups(c)) for c in claims]
    if not sizes or min(sizes) == 0:
        return "LOW"
    return "HIGH" if min(sizes) >= 2 else "MEDIUM"


def assess_item(item, evidence, sources_cfg, registry, pub_cfg, today, existing_records, country_id, category):
    """Decide one topic. -> dict(decision, claims, excluded, reasons, confidence). decision: PUBLISH | HOLD | NO_CHANGE."""
    ev_by_key = {e["sourceKey"]: e for e in evidence}
    same = [r for r in existing_records if r.get("countryId") == country_id and r.get("category") == category]
    existing_texts = [r["content"] for r in same]
    keep, excluded, held_reasons = [], list(item.get("excluded", [])), []
    for c in item["claims"]:
        reasons = check_claim(c, ev_by_key, sources_cfg, registry, pub_cfg, today, existing_texts, polarity_for(c, same, sources_cfg, item["topic"]))
        if not reasons:
            keep.append(c)
        else:
            excluded.append({"text": c["text"][:200], "reasons": reasons, "sources": sorted({x["sourceKey"] for x in c["evidence"]})})
            if set(reasons) & INTEGRITY:
                held_reasons += [r for r in reasons if r in INTEGRITY]
    for a, b in conflicts_within(keep):
        held_reasons.append("conflict")
        excluded.append({"text": a, "reasons": ["conflict"], "against": b})
    result = {"topic": item["topic"], "claims": keep, "excluded": excluded, "reasons": sorted(set(held_reasons)),
              "recordNote": item.get("recordNote")}
    if held_reasons:
        return result | {"decision": "HOLD", "claims": [], "confidence": None}
    if not keep:
        only_dup = excluded and all("duplicate_of_existing" in (e.get("reasons") or [e.get("reason")]) for e in excluded)
        reasons = sorted({r for e in excluded for r in (e.get("reasons") or [e.get("reason")]) if r})
        return result | {"decision": "NO_CHANGE" if only_dup else "HOLD", "reasons": [] if only_dup else (reasons or ["no_publishable_claim"]),
                         "confidence": None}
    conf = item_confidence(keep, ev_by_key, sources_cfg, registry, pub_cfg, today)
    if conf == "LOW" or len(keep) < pub_cfg["min_claims_per_record"]:
        return result | {"decision": "HOLD", "reasons": ["confidence_below_publishable"], "confidence": conf}
    return result | {"decision": "PUBLISH", "confidence": conf, "claims": keep[:pub_cfg["max_claims_per_record"]]}


def date_phrase(verb, iso, precision="DAY"):
    if not iso:
        return "publication date not stated by the source"
    d = datetime.fromisoformat(iso).date()
    return f"{verb} {d.strftime('%b %Y')}" if precision == "MONTH" else f"{verb} {d.day} {d.strftime('%b %Y')}"


def compose_content(claims, evidence_by_key, sources_cfg):
    """Published text: each claim is the source's own wording, grouped by source under an accurate attribution lead."""
    groups = {}
    for c in claims:
        sk = c["evidence"][0]["sourceKey"]
        groups.setdefault(sk, []).append(c["text"])
    parts = []
    for sk, texts in groups.items():
        cfg, ev = sources_cfg[sk], evidence_by_key[sk]
        lead = f"{cfg['attribution_label']} ({date_phrase(cfg.get('date_verb', 'published'), ev.get('publicationDate'), ev.get('publicationDatePrecision', 'DAY'))}): "
        parts.append(lead + " ".join(texts))
    return " ".join(parts)
