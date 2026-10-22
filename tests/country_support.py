"""Shared offline helpers for the country builder tests: recorded Ghana evidence, a fake network, and scripted
model responses. Everything here is a MOCK or a recorded fixture; none of it measures model quality."""
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ti_ops_director.country_config import load_builder_config          # noqa: E402
from ti_ops_director.country_engine import Builder, LocalBackend        # noqa: E402
from ti_ops_director.country_evidence import collect_category, select_passages  # noqa: E402
from ti_ops_director.feed_health import HttpResult                       # noqa: E402

FX = ROOT / "tests" / "fixtures" / "country_builder" / "gh"
NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)

PAGES = [("safety-and-security", "fcdo_safety.html"), ("/health", "fcdo_health.html"), ("travelhealthpro", "travelhealthpro.html"),
         ("cdc.gov", "cdc_travel.html"), ("nasa.gov", "nasa_power.json"), ("cckpapi", "worldbank_cckp.json")]


class FakeNet:
    """URL substring -> fixture. ``fail`` forces a status for matching URLs; ``robots`` per host."""
    def __init__(self, fail=None, robots=None, overrides=None):
        self.calls, self.fail, self.robots, self.overrides = [], fail or {}, robots or {}, overrides or {}

    def __call__(self, url, **kw):
        self.calls.append(url)
        for key, status in self.fail.items():
            if key in url:
                return HttpResult(status, b"", "http_error" if status else "network_error:Timeout")
        if url.endswith("/robots.txt"):
            host = url.split("/")[2]
            body = self.robots.get(host)
            return HttpResult(200, body.encode(), None) if body is not None else HttpResult(404, b"", "http_error")
        for key, body in self.overrides.items():
            if key in url:
                return HttpResult(200, body if isinstance(body, bytes) else body.encode(), None)
        for key, name in PAGES:
            if key in url:
                return HttpResult(200, (FX / name).read_bytes(), None)
        return HttpResult(404, b"", "http_error")


def base_data():
    """A small data.json-shaped dataset: Ghana with no ENVIRONMENT/TRANSPORT/EQUIPMENT record, plus Egypt with a stale one."""
    real = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    gh = next(c for c in real["countries"] if c["id"] == "gh")
    srcs = [s for s in real["sources"] if s["id"] in ("gh-fcdo-security", "gh-fcdo-health", "gh-fcdo-entry")]
    eg = dict(gh, id="eg", name="Egypt", officialName="Arab Republic of Egypt", iso3="EGY")
    recs = [r for r in real["records"] if r["countryId"] == "gh" and r["category"] not in ("ENVIRONMENT", "TRANSPORT", "EQUIPMENT")]
    stale = {"id": "eg-environment-old", "countryId": "eg", "category": "ENVIRONMENT", "title": "Old", "content": "Old content.",
             "sourceId": "gh-fcdo-entry", "status": "ACTIVE", "dateChecked": "2024-01-10", "reviewDate": "2025-01-10", "dataType": "FACT"}
    return {"meta": {"schemaVersion": "2.0", "counts": {}}, "countries": [gh, eg], "records": recs + [stale], "sources": srcs,
            "locations": [], "events": [], "sourceObservations": [], "change_log": [], "oilGasSummary": {}}


def setup(tmp, mode="draft", dry_run=True, limits=None, env=None, data=None, sonnet=False):
    tmp = Path(tmp)
    (tmp / "data.json").write_text(json.dumps(data or base_data()), encoding="utf-8")
    e = {"TI_CB_MODE": mode, "TI_CB_STATE_BACKEND": "file", "TI_CB_STATE_DIR": str(tmp / "state"),
         "TI_CB_DRY_RUN": "true" if dry_run else "false"}
    if limits:
        e.update({"TI_CB_PER_TASK_LIMIT_USD": str(limits[0]), "TI_CB_DAILY_LIMIT_USD": str(limits[1]),
                  "TI_CB_MONTHLY_LIMIT_USD": str(limits[2])})
    e.update(env or {})
    cfg = load_builder_config(e, ROOT)
    if sonnet:
        cfg.raw["spending"]["sonnet_escalation_enabled"] = True
    return cfg, e


def make_builder(tmp, provider=None, net=None, clock=None, **kw):
    cfg, env = setup(tmp, **kw)
    backend = LocalBackend(env["TI_CB_STATE_DIR"])
    net = net or FakeNet()
    out = []
    b = Builder(cfg, ROOT, backend, http=net, provider=provider, clock=clock or (lambda: NOW), env=env, out=out.append,
                workdir=Path(tmp) / "work", data_path=Path(tmp) / "data.json")
    b.captured = out
    b.net = net
    return b


def passages_for(category, net=None, cfg=None):
    cfg = cfg or load_builder_config({"TI_CB_MODE": "draft"}, ROOT)
    ev = collect_category(cfg, "gh", category, net or FakeNet(), NOW, base_data()["sources"])
    ps, _ = select_passages(ev, cfg.category_defs[category]["topics"], cfg.limits_cfg)
    return ev, ps


class Replies:
    """Scripted provider. Each reply is (text, stop_reason) or a callable(model) -> text. Records models used."""
    def __init__(self, *replies):
        self.replies, self.calls, self.models, self.users = list(replies), 0, [], []

    def complete(self, *, model, system, user, max_tokens, timeout=30, options=None):
        self.calls += 1
        self.models.append(model)
        self.users.append(user)
        r = self.replies.pop(0) if self.replies else self.replies_last
        self.replies_last = r
        text, stop = r if isinstance(r, tuple) else (r, "end_turn")
        return {"text": text() if callable(text) else text, "input_tokens": 1800, "output_tokens": 500, "stop_reason": stop}


def claim(passages, quote, text, status="VERIFIED", temporal="STABLE", ids=None):
    ids = ids or [p["id"] for p in passages if quote.lower() in p["text"].lower()][:1]
    return {"text": text, "status": status, "temporal": temporal, "passageIds": ids, "quotes": [quote]}


def reply_for(category, net=None, **tweak):
    """MOCK of a good Haiku answer for Ghana, built from the recorded passages so the quotes are verbatim."""
    _, ps = passages_for(category, net)
    c = lambda q, t, **k: claim(ps, q, t, **k)
    if category == "ENVIRONMENT":
        out = {"items": [{"topic": "FLOODING_NATURAL_HAZARDS", "title": "Flooding and earthquakes", "claims": [
            c("Flooding is common in the Upper West, North East, Northern and Savannah regions during the rainy season, from March to November.",
              "FCDO says flooding is common in the Upper West, North East, Northern and Savannah regions in the rainy season, March to November."),
            c("Heavy rainfall can also cause flooding in other parts of Ghana, including urban areas such as Accra and its surroundings.",
              "FCDO says heavy rain can also flood other areas, including Accra and its surroundings."),
            c("Earthquakes and tremors are a risk in Ghana.", "Earthquakes and tremors are a risk in Ghana.")],
            "uncertainty": "FCDO gives no dates for urban flooding outside the stated rainy-season months."}], "missing": []}
    elif category == "TRANSPORT":
        out = {"items": [
            {"topic": "ROAD_TRAVEL", "title": "Road travel", "claims": [
                c("Roads can be in a poor condition, particularly in rural areas.", "Roads can be in poor condition, particularly in rural areas."),
                c("Avoid travelling by road outside the main towns after dark",
                  "FCDO advises avoiding road travel outside the main towns after dark."),
                c("You can drive in Ghana using the 1949 version of the international driving permit (IDP) or a Ghanaian driving licence.",
                  "Driving is allowed on a 1949-version international driving permit or a Ghanaian licence.")],
             "uncertainty": "Road conditions on specific routes are not described by the sources."},
            {"topic": "TAXIS_AND_PUBLIC_TRANSPORT", "title": "Taxis and public transport", "claims": [
                c("Safety standards of taxi services in Ghana are low.", "FCDO says safety standards of taxi services in Ghana are low."),
                c("Avoid riding on motorcycles or motorbikes, especially motorbike taxis.",
                  "CDC advises avoiding motorcycles and motorbike taxis.")],
             "uncertainty": "No fare or licensing data was retrieved."},
            {"topic": "AIR_TRAVEL_AND_GATEWAYS", "title": "Air travel and Kotoka airport", "claims": [
                c("There are reports of a small number of thefts of luggage and travel documents at Kotoka International Airport.",
                  "FCDO reports a small number of luggage and document thefts at Kotoka International Airport.")],
             "uncertainty": "Airline, schedule and capacity information was not retrieved."}],
            "missing": []}
    else:
        out = {"items": [
            {"topic": "CLOTHING", "title": "Clothing", "claims": [
                c("wear loose and lightweight clothing", "Wear loose and lightweight clothing in the heat."),
                c("Dress modestly in public.", "Dress modestly in public."),
                c("Wearing military clothing including camouflage is illegal.", "Military clothing, including camouflage, is illegal to wear.",)],
             "uncertainty": "No month-by-month packing guidance was retrieved."},
            {"topic": "MOSQUITO", "title": "Mosquito protection", "claims": [
                c("There is a high risk of malaria in Ghana", "TravelHealthPro rates malaria risk in Ghana as high."),
                c("Use a bed net if the area where you are sleeping is exposed to the outdoors.",
                  "CDC advises a bed net where sleeping areas are exposed to the outdoors."),
                c("Use a repellent that contains 20% or more DEET", "CDC advises a repellent with 20% or more DEET.")],
             "uncertainty": "Which antimalarial suits an individual needs a clinician; this draft does not advise on medicines."}],
            "missing": []}
    for fn in tweak.values():
        fn(out)
    return json.dumps(out)
