"""Evaluation harness tests for the country builder. Scripted replies only: they check the scorer, the
refusal rules and the cap. They cannot measure Haiku's quality, and the harness says so in its output."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import country_support as cs                                             # noqa: E402
from country_support import ROOT, Replies, claim                         # noqa: E402

from ti_ops_director import country_eval as ce                           # noqa: E402
from ti_ops_director.country_config import load_builder_config           # noqa: E402
from ti_ops_director.country_evidence import collect_category, select_passages  # noqa: E402
from ti_ops_director.models import AnthropicProvider                     # noqa: E402
from ti_ops_director.monitor_eval import EvalRefused                     # noqa: E402

RATE = {"source": "ECB", "url": "x", "reference_date": "2026-10-21", "usd_per_eur": 1.16}
APPROVAL = {"approvalId": "t-1", "approvedBy": "Marc", "purpose": ce.PURPOSE, "capEur": 0.50, "model": "claude-haiku-5-5",
            "conversion": {"margin": 0.10, "absoluteUsdCeiling": 1.0}}


def passages_of(case):
    cfg = load_builder_config({}, ROOT)
    ev = collect_category(cfg, "gh", case["category"], ce.FixtureNet(case.get("pages")), ce.EVAL_NOW, cs.base_data()["sources"])
    return select_passages(ev, cfg.category_defs[case["category"]]["topics"], cfg.limits_cfg)[0]


def good_reply(case):
    if case["id"] == "gh-transport-no-airport-evidence":
        d = json.loads(cs.reply_for("TRANSPORT"))
        d["items"] = [i for i in d["items"] if i["topic"] != "AIR_TRAVEL_AND_GATEWAYS"]
        d["missing"] = [{"topic": "AIR_TRAVEL_AND_GATEWAYS", "reason": "no passage about airports or gateways was supplied"}]
        for i in d["items"]:           # the CDC claim about motorbikes still has its passage; keep ids valid for this variant
            for c in i["claims"]:
                c["passageIds"] = [p["id"] for p in passages_of(case) if c["quotes"][0].lower() in p["text"].lower()][:1]
        d["items"] = [dict(i, claims=[c for c in i["claims"] if c["passageIds"]]) for i in d["items"]]
        return json.dumps(d)
    if case["id"] == "gh-transport-conflicting-sources":
        ps = passages_of(case)
        fs = next(p for p in ps if "safer to use pre-booked" in p["text"])
        cd = next(p for p in ps if "Do not use pre-booked taxis" in p["text"])
        return json.dumps({"items": [{"topic": "TAXIS_AND_PUBLIC_TRANSPORT", "title": "Taxis", "uncertainty": "Sources disagree on pre-booked taxis.",
                                      "claims": [{"text": "Sources disagree on whether pre-booked taxis are advisable.", "status": "CONFLICT", "temporal": "STABLE",
                                                  "passageIds": [fs["id"], cd["id"]],
                                                  "quotes": ["it’s safer to use pre-booked vehicles from reputable taxi companies",
                                                             "Do not use pre-booked taxis from reputable companies"]}]}], "missing": []})
    return cs.reply_for(case["category"])


def mock_factory(cfg, replies, cap=1.0):
    ce.prepare_for_eval(cfg, cap)
    return lambda: ce.make_spend(cfg, {}, None, replies, cap)


class Cases(unittest.TestCase):
    def test_case_file_is_internally_consistent(self):
        doc = ce.load_cases()
        cfg = load_builder_config({}, ROOT)
        self.assertGreaterEqual(len(doc["cases"]), 5)
        kinds = set()
        for c in doc["cases"]:
            topics = cfg.category_defs[c["category"]]["topics"]
            self.assertTrue(c["manual_assessment"])
            for m in c["must_cover"]:
                self.assertIn(m["topic"], topics)
                re.compile("|".join(m["any"]))
            for p in c["must_not"]:
                re.compile(p)
            for t in c["must_report_missing"]:
                self.assertIn(t, topics)
            for frag, name in (c.get("pages") or {}).items():
                self.assertTrue((ce.FIXTURES / name).exists(), name)
            kinds.add(c["id"])
        self.assertTrue({"gh-transport-no-airport-evidence", "gh-transport-conflicting-sources"} <= kinds)   # trap and conflict cases

    def test_trap_and_conflict_fixtures_are_what_they_claim(self):
        self.assertNotIn("Kotoka", (ce.FIXTURES / "gh/trap_fcdo_safety_no_airport.html").read_text())
        self.assertIn("Do not use pre-booked taxis", (ce.FIXTURES / "gh/trap_cdc_conflict.html").read_text())
        self.assertIn("SYNTHETIC", (ce.FIXTURES / "gh/trap_cdc_conflict.html").read_text())


class MockedHarness(unittest.TestCase):
    def run_eval(self, replies_for):
        cfg = load_builder_config({}, ROOT)
        doc = ce.load_cases()
        replies = Replies(*[replies_for(c) for c in doc["cases"]])
        return ce.evaluate(cfg, ROOT, doc, mock_factory(cfg, replies)), replies

    def test_good_scripted_answers_score_fully_but_are_not_called_quality(self):
        res, replies = self.run_eval(good_reply)
        self.assertEqual(res["measurement"], ce.MOCKED)
        self.assertFalse(res["model_quality_measured"])
        self.assertEqual(res["verdict"], "NOT MEASURED (mock)")
        self.assertIsNone(res["meets_thresholds"])
        t = res["totals"]
        self.assertEqual((t["forbidden_hits"], t["coverage"], t["missing_reported"], t["conflict_cases_handled"]),
                         (0, "11/11", "2/2", "1/1"))
        self.assertEqual(replies.calls, 5)

    def test_hallucinating_answers_are_caught_by_the_checks(self):
        def bad(case):
            d = json.loads(good_reply(case))
            for i in d["items"]:
                i["claims"].append({"text": "The airport is operating normally and Kotoka has no restrictions.", "status": "VERIFIED",
                                    "temporal": "STABLE", "passageIds": [i["claims"][0]["passageIds"][0]],
                                    "quotes": ["Kotoka International Airport is perfectly fine"]})
            return json.dumps(d)
        res, _ = self.run_eval(bad)
        self.assertGreater(res["totals"]["claims_rejected"], 0)
        self.assertEqual(res["totals"]["forbidden_hits"], 0)               # nothing unsupported survived into a kept claim
        self.assertGreater(res["totals"]["rejected_ratio"], 0.25)

    def test_scorer_flags_a_draft_that_misses_required_facts(self):
        def thin(case):
            d = json.loads(good_reply(case))
            for i in d["items"]:
                i["claims"] = i["claims"][-1:]
            return json.dumps(d)
        res, _ = self.run_eval(thin)
        cov = res["totals"]["coverage"].split("/")
        self.assertLess(int(cov[0]), int(cov[1]))


class FakeAnthropic(AnthropicProvider):
    def __init__(self, replies):
        super().__init__("test-key")
        self.replies, self.calls, self.models = list(replies), 0, []

    def complete(self, *, model, system, user, max_tokens, timeout=30, options=None):
        self.calls += 1
        self.models.append(model)
        return {"text": self.replies.pop(0), "input_tokens": 2200, "output_tokens": 600, "stop_reason": "end_turn"}


class Approved(unittest.TestCase):
    def env(self, tmp):
        return {"ANTHROPIC_API_KEY": "k", "TI_CB_STATE_BACKEND": "file", "TI_CB_STATE_DIR": str(Path(tmp) / "st")}

    def run_it(self, tmp, approval=None, rate=None, env=None, replies=None, today="2026-10-22"):
        cfg = load_builder_config({}, ROOT)
        env = env if env is not None else self.env(tmp)
        backend = ce.make_eval_backend(env, ROOT, cfg)
        doc = ce.load_cases()
        prov = FakeAnthropic(replies if replies is not None else [good_reply(c) for c in doc["cases"]])
        out = ce.run_approved(approval or dict(APPROVAL), cfg, ROOT, backend, env, rate or RATE, today, provider=prov, spend_workdir=Path(tmp) / "w")
        return out, prov, backend

    def test_refusals_happen_before_any_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kw, why in (({"approval": dict(APPROVAL, approvedBy="Someone")}, "does not authorise"),
                            ({"approval": dict(APPROVAL, purpose="monitoring")}, "does not authorise"),
                            ({"rate": dict(RATE, reference_date="2026-10-01")}, "stale"),
                            ({"approval": dict(APPROVAL, capEur=0)}, "not positive"),
                            ({"env": {"TI_CB_STATE_BACKEND": "file", "TI_CB_STATE_DIR": str(Path(tmp) / "st2")}}, "ANTHROPIC_API_KEY")):
                cfg = load_builder_config({}, ROOT)
                env = kw.get("env", self.env(tmp))
                backend = ce.make_eval_backend(env, ROOT, cfg)
                prov = FakeAnthropic([])
                with self.assertRaises(EvalRefused, msg=why) as cm:
                    ce.run_approved(kw.get("approval", dict(APPROVAL)), cfg, ROOT, backend, env, kw.get("rate", RATE), "2026-10-22",
                                    provider=None if "env" in kw else prov)
                self.assertIn(why, str(cm.exception))
                self.assertEqual(prov.calls, 0)

    def test_live_labelled_run_measures_quality_under_the_cap_and_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, prov, backend = self.run_it(tmp)
            e = out["evaluation"]
            self.assertTrue(e["measurement"].startswith("LIVE_MODEL:claude-haiku-5-5"))
            self.assertTrue(e["model_quality_measured"])
            self.assertEqual(e["verdict"], "SUITABLE on these cases")
            self.assertEqual((prov.calls, set(prov.models)), (5, {"claude-haiku-5-5"}))
            self.assertLessEqual(out["ledger"]["total_committed_usd"], out["cap"]["usd"])
            self.assertEqual(out["cap"]["usd"], 0.52)                       # floor(0.50 x 1.16 x 0.90) to the cent
            self.assertGreater(e["totals"]["cost_usd"], 0)
            res_text = backend.snapshot().text(ce.RESULTS_KEY)
            self.assertIn('"model_quality_measured": true', res_text)
            # same prompt, model and cases: no new call
            again, prov2, _ = self.run_it(tmp, replies=[])
            self.assertIn("already evaluated", again["skipped"])
            self.assertEqual(prov2.calls, 0)
            # a second approval id for the same completed evaluation is also refused
            other, prov3, _ = self.run_it(tmp, approval=dict(APPROVAL, approvalId="t-2"), replies=[])
            self.assertEqual(prov3.calls, 0)

    def test_cap_stops_the_run_and_the_verdict_says_incomplete(self):
        from datetime import datetime, timezone
        from ti_ops_director.monitor_state import dump_state
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_builder_config({}, ROOT)
            env = self.env(tmp)
            backend = ce.make_eval_backend(env, ROOT, cfg)
            now = datetime.now(timezone.utc)
            seeded = {"spend": {"daily": {now.strftime("%Y-%m-%d"): 0.0099}, "monthly": {now.strftime("%Y-%m"): 0.0099},
                                "total": 0.0099, "open": [], "currency": "USD"}}
            backend.transact(lambda snap: ({"state": dump_state(seeded)}, None))
            approval = dict(APPROVAL, capEur=0.0087, conversion={"margin": 0.0, "absoluteUsdCeiling": 1.0})   # $0.01 cap
            prov = FakeAnthropic([good_reply(c) for c in ce.load_cases()["cases"]])
            out = ce.run_approved(approval, cfg, ROOT, backend, env, RATE, "2026-10-22", provider=prov, spend_workdir=Path(tmp) / "w")
            self.assertEqual(out["cap"]["usd"], 0.01)
            self.assertEqual(prov.calls, 0)                                  # the reservation is refused before the call
            self.assertFalse(out["evaluation"]["model_quality_measured"])
            self.assertTrue(out["evaluation"]["verdict"].startswith("INCOMPLETE"))
            self.assertLessEqual(out["ledger"]["total_committed_usd"], 0.01)
            self.assertNotIn("completed", backend.snapshot().state())        # an incomplete run is not recorded as done

    def test_bad_live_answers_give_a_not_suitable_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = ce.load_cases()
            thin = []
            for c in doc["cases"]:
                d = json.loads(good_reply(c))
                for i in d["items"]:
                    i["claims"] = i["claims"][-1:]
                thin.append(json.dumps(d))
            out, prov, _ = self.run_it(tmp, replies=thin)
            self.assertEqual(out["evaluation"]["verdict"], "NOT SUITABLE on these cases")

    def test_cli_refuses_without_an_approval_file(self):
        import io, contextlib
        sys.path.insert(0, str(ROOT / "scripts"))
        import country_builder as cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["eval"], {})
        self.assertEqual(rc, 2)
        self.assertIn("no approval file", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
