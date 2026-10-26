"""Parsers for the pregame sources, against trimmed copies of the real pages (captured Oct 24, 2026)."""
import sys, unittest
from datetime import date
from pathlib import Path
T = Path(__file__).resolve().parent
sys.path.insert(0, str(T.parent))
import fetch_inputs as F

PAGES = {"dailyfaceoff": "dfo.html", "injuries": "cbs_injuries.html", "odds": "cbs_odds.html"}
F.get = lambda url, timeout=30: (T / "fixtures" / next(v for k, v in PAGES.items() if k in url)).read_text()


class TestInputs(unittest.TestCase):
    def test_goalies(self):
        _, rows = F.goalies_for("2026-10-24")
        self.assertEqual(rows[0], ["ANA", "Lukas Dostal", "Confirmed", "PHI", "Joseph Woll", "Confirmed"])
        self.assertTrue(all(len(r) == 6 and r[0] in F.OURS and r[3] in F.OURS for r in rows))

    def test_goalies_other_date_ignored(self):
        _, rows = F.goalies_for("2026-10-25")
        self.assertEqual(rows, [])

    def test_injuries(self):
        _, rows = F.injuries()
        self.assertIn(["Anaheim", "Pavel Mintyukov", "D", "IR, Expected out until at least Oct 29"], rows)
        self.assertTrue(all(r[0] in F.CITY.values() for r in rows))
        self.assertFalse(any(r[1].startswith(("P. ", "I. ")) for r in rows))   # full names, not "P. Mintyukov"

    def test_odds(self):
        _, lines = F.odds(date(2026, 10, 24))
        self.assertEqual(lines[0][:5], ["FLA", -180, "CHI", 150, "2026-10-25"])
        self.assertEqual(lines[0][5:], [-1.5, 136, 1.5, -161])   # puck line: FLA -1.5 +136, CHI +1.5 -161
        for a, aml, h, hml, d, *pl in lines:
            self.assertNotEqual(a, h); self.assertGreaterEqual(abs(aml), 100); self.assertGreaterEqual(abs(hml), 100)

    def test_abbr(self):
        self.assertEqual(F.abbr("LA"), "LAK"); self.assertEqual(F.abbr("utah"), "UTA"); self.assertIsNone(F.abbr("XYZ"))


if __name__ == "__main__":
    unittest.main()
