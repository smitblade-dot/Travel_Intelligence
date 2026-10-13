import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("resolver", ROOT / "scripts/resolve_phase1_signals.py")
resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolver)

class DiscoveryTests(unittest.TestCase):
    def test_fairford_without_country_name(self):
        text = "B-1 bombers redeployed from RAF Fairford"
        self.assertEqual(resolver.country_ids(text, {"gb", "ru"}), ["gb"])
        self.assertIn("SECURITY", resolver.categories(text))

    def test_sochi_without_country_name(self):
        text = "Aframax Rio tanker fire off Sochi"
        self.assertEqual(resolver.country_ids(text, {"gb", "ru"}), ["ru"])
        self.assertIn("PORT", resolver.categories(text))
        self.assertIn("FIRE", resolver.categories(text))

    def test_short_alias_does_not_match_inside_word(self):
        self.assertEqual(resolver.country_ids("Ukraine tanker", {"gb"}), [])

if __name__ == "__main__":
    unittest.main()
