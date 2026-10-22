"""Coverage and staleness detection for country baseline intelligence. Ordinary code: no model, no network."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .country_config import CATEGORIES
from .store import fingerprint, task_dedupe_key

TASK_TYPE = "country_content_draft"


def _day(value):
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def combo_records(data):
    """{(countryId, category): [active records]}. Same definition of 'covered' as scripts/audit_baseline_coverage.py."""
    out = {}
    for r in data.get("records", []):
        if r.get("status", "ACTIVE") == "ACTIVE" and r.get("category") in CATEGORIES:
            out.setdefault((str(r.get("countryId")).lower(), r["category"]), []).append(r)
    return out


def coverage_summary(data):
    countries = [c for c in data.get("countries", []) if c.get("status", "ACTIVE") == "ACTIVE"]
    have = combo_records(data)
    total = len(countries) * len(CATEGORIES)
    covered = sum(1 for c in countries for cat in CATEGORIES if have.get((str(c["id"]).lower(), cat)))
    return {"countries": len(countries), "combinations": total, "covered": covered, "missing": total - covered}


def find_gaps(data, countries, categories, today=None, stale_after_days=365, gap_types=("MISSING", "STALE")):
    """Gaps for the configured scope only, ordered by category order then country. A combination with an active
    record is STALE only when every active record is past its reviewDate or was last checked too long ago."""
    today = today or date.today()
    names = {str(c["id"]).lower(): c for c in data.get("countries", [])}
    have = combo_records(data)
    gaps = []
    for cat in categories:
        for cc in countries:
            country = names.get(cc)
            if not country or country.get("status", "ACTIVE") != "ACTIVE":
                continue
            recs = have.get((cc, cat), [])
            if not recs:
                gap = {"type": "MISSING", "detail": f"No active {cat} record for {country['name']}."}
            else:
                def stale(r):
                    due, checked = _day(r.get("reviewDate")), _day(r.get("dateChecked"))
                    return (due is not None and due < today) or (checked is not None and (today - checked).days > stale_after_days)
                if not all(stale(r) for r in recs):
                    continue
                gap = {"type": "STALE", "detail": f"All {len(recs)} active {cat} record(s) are past review or older than "
                                                  f"{stale_after_days} days."}
            if gap["type"] in gap_types:
                gaps.append({"countryId": cc, "country": country["name"], "category": cat, "gap": gap,
                             "existingRecordIds": sorted(r["id"] for r in recs)})
    return gaps


def recheck_bucket(today, days):
    return "W%d" % ((today - date(2026, 1, 1)).days // max(1, int(days)))


def task_for(gap, today, recheck_days):
    """The queue task for one gap, with the Director's dedupe-key format. Same gap in the same recheck window
    always yields the same key, so a repeat plan creates nothing."""
    inputs = {"countryId": gap["countryId"], "country": gap["country"], "category": gap["category"],
              "gap": gap["gap"], "existingRecordIds": gap["existingRecordIds"]}
    fp = fingerprint({k: inputs[k] for k in ("countryId", "category", "existingRecordIds")} | {"gapType": gap["gap"]["type"]})
    key = task_dedupe_key(TASK_TYPE, f"{gap['countryId']}:{gap['category']}:{gap['gap']['type']}", fp,
                          recheck_bucket(today, recheck_days))
    return {"dedupe_key": key, "task_type": TASK_TYPE, "inputs": inputs, "input_fingerprint": fp}
