"""Model-compatibility evaluation harness. Every model output here is MOCKED or RECORDED; none of these tests
measures Haiku 5.5 quality. Offline, no API key, no charge."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops_support import ROOT   # noqa: E402
from test_ti_ops_director_monitor_integration import LIMITS, FakeProvider, monitor  # noqa: E402

from ti_ops_director.config import load_config                  # noqa: E402
from ti_ops_director.models import AnthropicProvider             # noqa: E402
from ti_ops_director.monitor_bridge import MonitorSpend          # noqa: E402
from ti_ops_director import monitor_eval                         # noqa: E402

DOC = json.loads((ROOT / "tests/fixtures/ops_director/monitor_labelled_cases.json").read_text())


def spend_with(provider, env=LIMITS):
    cfg = load_config(env=dict(env))
    cfg.max_output_tokens = 2200
    return MonitorSpend(cfg, {}, workdir=tempfile.mkdtemp(), provider=provider)


def reference_reply(case, **override):
    """A mocked answer built FROM THE LABEL (so it scores as correct by construction)."""
    lab = case["labels"]
    if not lab["relevant"]:
        return {"index": 0, "countryId": lab.get("countryId"), "publish": False, "existingId": None, "category": "OTHER",
                "incidentDate": None, "continuingImpact": False, "incidentKey": None}
    d = {"index": 0, "countryId": lab["countryId"], "publish": True, "existingId": None, "category": lab["category"],
         "incidentDate": lab["incidentDate"], "continuingImpact": lab["continuingImpact"],
         "incidentKey": case["id"].replace("_", "-")}
    d.update(override)
    return d


def conflict_reply(case, same_key=True):
    return [{"index": i, "countryId": "cy" if "larnaca" in case["id"] else "ng", "publish": True, "existingId": None,
             "category": "TRANSPORT_DISRUPTION", "incidentDate": None, "continuingImpact": True,
             "incidentKey": case["labels"]["incident"] + ("" if same_key else f"-{i}")} for i in range(len(case["titles"]))]


def replies(wrong_country_for=None, same_key=True):
    out = []
    for case in DOC["relevance_extraction"]:
        override = {"countryId": "gb"} if case["id"] == wrong_country_for else {}
        out.append(json.dumps({"decisions": [reference_reply(case, **override)]}))
    out += [json.dumps({"decisions": conflict_reply(c, same_key)}) for c in DOC["conflicting_evidence"]]
    return out


class LabelledCases(unittest.TestCase):
    def test_fixture_is_labelled_and_balanced(self):
        rel = DOC["relevance_extraction"]
        self.assertGreaterEqual(len(rel), 12)
        self.assertGreaterEqual(sum(c["labels"]["relevant"] for c in rel), 5)
        self.assertGreaterEqual(sum(not c["labels"]["relevant"] for c in rel), 5)
        self.assertTrue(all("countryId" in c["labels"] and "category" in c["labels"] for c in rel if c["labels"]["relevant"]))
        self.assertIn("not copied from any feed", DOC["_provenance"]["labelled_cases"])

    def test_reference_mock_scores_fully_and_is_labelled_as_mocked(self):
        tmp = tempfile.mkdtemp()
        result = monitor_eval.evaluate_labelled(DOC, spend_with(FakeProvider(replies(), tmp)), monitor)
        self.assertEqual(result["measurement"], monitor_eval.MOCKED)
        self.assertFalse(result["model_quality_measured"])
        self.assertIn("Model quality NOT measured", result["note"])
        r = result["relevance"]
        self.assertEqual((r["scored"], r["correct_firm"], r["correct_borderline"]), (r["cases"], r["firm"], r["borderline"]))
        self.assertEqual(result["extraction"]["fields_correct"], result["extraction"]["fields_checked"])
        self.assertEqual(result["conflicting_evidence"]["within_limit"], 2)    # same key -> duplicate guard holds
        self.assertTrue(all(s == ["EARLY_REPORT/LOW"] for s in (c["statuses"] for c in result["conflicts"])))

    def test_scorer_detects_a_wrong_extraction(self):
        tmp = tempfile.mkdtemp()
        result = monitor_eval.evaluate_labelled(DOC, spend_with(FakeProvider(replies("cy_wildfire_roads"), tmp)), monitor)
        row = next(c for c in result["cases"] if c["id"] == "cy_wildfire_roads")
        self.assertTrue(row["relevance_correct"])
        self.assertFalse(row["extraction"]["countryId"])
        self.assertEqual(result["extraction"]["fields_correct"], result["extraction"]["fields_checked"] - 1)

    def test_known_limit_conflicting_reports_with_different_keys_create_several_events(self):
        """Recorded as a limitation, not hidden: the code guard only deduplicates identical incident keys."""
        tmp = tempfile.mkdtemp()
        result = monitor_eval.evaluate_labelled(DOC, spend_with(FakeProvider(replies(same_key=False), tmp)), monitor)
        self.assertEqual(result["conflicting_evidence"]["within_limit"], 0)
        self.assertEqual([c["events_created"] for c in result["conflicts"]], [3, 2])

    def test_without_allowance_nothing_runs_and_nothing_is_claimed(self):
        result = monitor_eval.evaluate_labelled(DOC, spend_with(FakeProvider([], tempfile.mkdtemp()),
                                                                env={"TI_OPS_DRY_RUN": "false"}), monitor)
        self.assertEqual(result["relevance"]["scored"], 0)
        self.assertEqual(result["measurement"], "NOT_RUN")
        self.assertFalse(result["model_quality_measured"])

    def test_live_label_requires_the_real_provider_outside_dry_run(self):
        live = spend_with(AnthropicProvider("not-used"))
        self.assertEqual(monitor_eval.measurement_of(live), "LIVE_MODEL:claude-haiku-5-5")
        dry = spend_with(AnthropicProvider("not-used"), env={})
        self.assertEqual(monitor_eval.measurement_of(dry), monitor_eval.MOCKED)


class MalformedResponses(unittest.TestCase):
    def test_each_malformed_response_is_handled_as_labelled(self):
        result = monitor_eval.replay_malformed(DOC, monitor, spend_with)
        failures = [r for r in result["cases"] if not r["as_expected"]]
        self.assertEqual(failures, [])
        by = {r["id"]: r for r in result["cases"]}
        self.assertEqual((by["extra_object"]["accepted"], by["extra_object"]["calls"]), (False, 2))  # one corrective retry
        self.assertTrue(by["fenced_json"]["accepted"])
        self.assertEqual(by["out_of_range_and_duplicate_index"]["published"], 1)


class RecordedOutput(unittest.TestCase):
    def test_recorded_haiku_4_5_output_against_current_guards(self):
        r = monitor_eval.check_recorded(DOC, monitor)
        self.assertEqual(r["measurement"], "RECORDED_OUTPUT:claude-haiku-4-5-20251001")
        self.assertTrue(r["validator_accepts"])
        self.assertEqual(r["model_publish_true"], 2)
        # Recorded before PR #25's fields existed: today's guards publish neither of the two "publish" decisions.
        self.assertEqual(r["published_after_current_guards"], 0)
        self.assertEqual(r["missing_current_schema_fields"], ["continuingImpact", "incidentDate", "incidentKey"])


class EvalCommand(unittest.TestCase):
    def test_cli_without_allowance_reports_not_measured(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import ops_director
        out = io.StringIO()
        env = {"TI_OPS_DB_PATH": str(Path(tempfile.mkdtemp()) / "ops.sqlite3"), "RUNNER_TEMP": tempfile.mkdtemp()}
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            ops_director.main(["eval-monitor"])
        report = json.loads(out.getvalue())
        self.assertFalse(report["labelled"]["model_quality_measured"])
        self.assertEqual(report["labelled"]["relevance"]["scored"], 0)
        self.assertEqual(report["recorded"]["published_after_current_guards"], 0)


if __name__ == "__main__":
    unittest.main()


class RecordedHaiku55(unittest.TestCase):
    """RECORDED live Haiku 5.5 decisions (approved evaluation, 21 Oct 2026) replayed through today's guards."""
    REC = json.loads((ROOT / "tests/fixtures/ops_director/monitor_recorded_haiku55.json").read_text())

    def published(self, case_id, titles):
        items = [{"title": t, "url": f"https://example.org/{case_id}/{i}", "date": ""} for i, t in enumerate(titles)]
        data = monitor_eval._empty_data(DOC["countries"])
        return monitor.publish(data, items, self.REC["decisions"][case_id]), data

    def test_relevant_reports_survive_key_normalisation(self):
        got = {c["id"]: self.published(c["id"], [c["title"]])[0] for c in DOC["relevance_extraction"]}
        # Before the fix the space-separated keys of these two were dropped by the guard.
        self.assertEqual((got["ng_expressway_explosion"], got["ng_advisory_change"]), (1, 1))
        relevant = [c["id"] for c in DOC["relevance_extraction"] if c["labels"]["relevant"]]
        self.assertEqual(sum(got[i] for i in relevant), 5)            # sa_sandstorm_delays: model said not ongoing
        firm_irrelevant = [c["id"] for c in DOC["relevance_extraction"]
                           if not c["labels"]["relevant"] and not c.get("borderline")]
        self.assertEqual(sum(got[i] for i in firm_irrelevant), 0)
        self.assertEqual(got["us_local_hazmat"], 1)                   # borderline: model chose to publish

    def test_conflicting_reports_never_create_two_events(self):
        for case in DOC["conflicting_evidence"]:
            n, data = self.published(case["id"], case["titles"])
            self.assertLessEqual(n, case["labels"]["max_events"], case["id"])
            self.assertTrue(all(e["eventStatus"] == "EARLY_REPORT" and e["confidence"] == "LOW" for e in data["events"]))

    def test_key_normalisation(self):
        self.assertEqual(monitor.normalise_incident_key("Lagos-Ibadan expressway tanker  explosion"),
                         "lagos-ibadan-expressway-tanker-explosion")
        self.assertIsNone(monitor.normalise_incident_key("fire"))     # too short to be a stable key
        self.assertIsNone(monitor.normalise_incident_key(None))
