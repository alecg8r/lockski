import sys, unittest, copy
from pathlib import Path
from datetime import datetime, timezone, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
import ledger as L

NOW = datetime(2026, 10, 24, 12, 36, tzinfo=timezone.utc)
WEEKS = {"wk": {"games": [
    {"id": "2026-10-24-ANA-PHI", "date": "2026-10-24", "startUTC": "2026-10-24T17:00:00Z", "away": "ANA", "home": "PHI"},
    {"id": "2026-10-24-SJS-BOS", "date": "2026-10-24", "startUTC": "2026-10-24T23:00:00Z", "away": "SJS", "home": "BOS"},
    {"id": "2026-10-27-VAN-ANA", "date": "2026-10-27", "startUTC": "2026-10-28T02:00:00Z", "away": "VAN", "home": "ANA"},
    {"id": "2026-10-24-TOR-EDM", "date": "2026-10-24", "startUTC": "2026-10-24T10:00:00Z", "away": "TOR", "home": "EDM"}]}}


def model(ph_phi=0.55, version="v0.5"):
    return {"version": version, "assumptions": {"x": 1}, "games": {
        "2026-10-24-ANA-PHI": {"pHome": ph_phi, "pAway": round(1 - ph_phi, 4), "goalieAway": "A", "goalieHome": "B"},
        "2026-10-24-SJS-BOS": {"pHome": 0.65, "pAway": 0.35},
        "2026-10-27-VAN-ANA": {"pHome": 0.6, "pAway": 0.4},
        "2026-10-24-TOR-EDM": {"pHome": 0.6, "pAway": 0.4}}}


class TestLedger(unittest.TestCase):
    def test_window_and_pregame_only(self):
        led = {}
        ids = L.record_forecasts(led, WEEKS, model(), NOW)
        self.assertEqual(sorted(ids), ["2026-10-24-ANA-PHI", "2026-10-24-SJS-BOS"])  # too far ahead / already started excluded

    def test_idempotent_rerun(self):
        led = {}
        L.record_forecasts(led, WEEKS, model(), NOW)
        self.assertEqual(L.record_forecasts(led, WEEKS, model(), NOW + timedelta(minutes=5)), [])

    def test_new_info_appends_and_official_updates_pregame(self):
        led = {}
        L.record_forecasts(led, WEEKS, model(0.55), NOW)
        L.record_forecasts(led, WEEKS, model(0.60), NOW + timedelta(hours=2))
        d = led["2026-10-24-ANA-PHI"]
        self.assertEqual(len(d["snapshots"]), 2)
        self.assertEqual(d["official"]["pHome"], 0.60)
        self.assertEqual(d["snapshots"][0]["pHome"], 0.55)       # history preserved

    def test_no_changes_after_puck_drop(self):
        led = {}
        L.record_forecasts(led, WEEKS, model(0.55), NOW)
        before = copy.deepcopy(led["2026-10-24-ANA-PHI"])
        L.record_forecasts(led, WEEKS, model(0.90), datetime(2026, 10, 24, 17, 30, tzinfo=timezone.utc))
        self.assertEqual(led["2026-10-24-ANA-PHI"], before)

    def test_version_change_does_not_double_count(self):
        led = {}
        L.record_forecasts(led, WEEKS, model(0.55, "v0.5"), NOW)
        L.record_forecasts(led, WEEKS, model(0.58, "v0.6"), NOW + timedelta(hours=1))
        L.settle(led, [{"date": "2026-10-24", "away": "ANA", "home": "PHI", "ag": 1, "hg": 3, "winner": "home"}], NOW + timedelta(hours=10))
        self.assertEqual(L.summary(led)["graded"], 1)

    def test_settle_once_and_ot_shootout(self):
        led = {}
        L.record_forecasts(led, WEEKS, model(), NOW)
        r = [{"date": "2026-10-24", "away": "ANA", "home": "PHI", "ag": 3, "hg": 3, "winner": "away", "ot": True}]
        self.assertEqual(L.settle(led, r, NOW), ["2026-10-24-ANA-PHI"])     # shootout: tied score, explicit winner
        self.assertEqual(led["2026-10-24-ANA-PHI"]["result"]["winner"], "ANA")
        r2 = [{"date": "2026-10-24", "away": "ANA", "home": "PHI", "ag": 1, "hg": 5, "winner": "home"}]
        self.assertEqual(L.settle(led, r2, NOW), [])                         # never overwritten
        self.assertEqual(led["2026-10-24-ANA-PHI"]["result"]["winner"], "ANA")

    def test_unknown_or_unpredicted_games_ignored(self):
        led = {}
        self.assertEqual(L.settle(led, [{"date": "2026-10-24", "away": "TOR", "home": "EDM", "ag": 2, "hg": 3}], NOW), [])

    def test_experiments_never_official(self):
        led = {}
        L.record_forecasts(led, WEEKS, model(0.55), NOW)
        L.record_forecasts(led, WEEKS, model(0.70, "exp-elo"), NOW + timedelta(hours=1), channel="experiment")
        d = led["2026-10-24-ANA-PHI"]
        self.assertEqual(d["official"]["pHome"], 0.55)
        self.assertEqual(len(d["experiments"]), 1)

    def test_bad_probabilities_rejected(self):
        led = {}
        bad = model(); bad["games"]["2026-10-24-ANA-PHI"] = {"pHome": 0.9, "pAway": 0.4}
        self.assertNotIn("2026-10-24-ANA-PHI", L.record_forecasts(led, WEEKS, bad, NOW))



class TestMarket(unittest.TestCase):
    def test_attach_and_validation(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
        import market as MK
        m = model()
        gd = {"odds": {"date": "2026-10-24", "source": "test", "lines": [["ANA", 105, "PHI", -122], ["SJS", 160, "BOS", 160], ["XXX", 100, "YYY", -110]]}}
        self.assertEqual(MK.attach(m, WEEKS, gd), 1)                       # SJS/BOS both +160 rejected, unknown teams skipped
        mk = m["games"]["2026-10-24-ANA-PHI"]["market"]
        self.assertAlmostEqual(mk["pHome"] + mk["pAway"], 1, places=3)
        self.assertTrue(0.52 < mk["pHome"] < 0.54)
        self.assertNotIn("market", m["games"]["2026-10-24-SJS-BOS"])

    def test_market_arrival_adds_pregame_snapshot_and_vs_market(self):
        import market as MK
        led = {}
        L.record_forecasts(led, WEEKS, model(), NOW)                     # morning: no prices yet
        m = model(); MK.attach(m, WEEKS, {"odds": {"date": "2026-10-24", "lines": [["ANA", 105, "PHI", -122]]}})
        ids = L.record_forecasts(led, WEEKS, m, NOW + timedelta(hours=1))
        self.assertEqual(ids, ["2026-10-24-ANA-PHI"])
        self.assertIsNotNone(led["2026-10-24-ANA-PHI"]["official"]["market"])
        self.assertEqual(L.record_forecasts(led, WEEKS, m, NOW + timedelta(hours=2)), [])   # idempotent again
        L.settle(led, [{"date": "2026-10-24", "away": "ANA", "home": "PHI", "ag": 1, "hg": 3, "winner": "home"}], NOW + timedelta(hours=8))
        vm = L.summary(led)["vsMarket"]
        self.assertEqual(vm["games"], 1)
        self.assertLess(vm["lockskiLogLoss"], 1)

if __name__ == "__main__":
    unittest.main()


class TestVoidAndMarketML(unittest.TestCase):
    def test_market_ml_saved_at_lock(self):
        led = {}; mdl = model()
        mdl["games"]["2026-10-24-ANA-PHI"]["market"] = {"pHome": 0.53, "pAway": 0.47, "awayML": 105, "homeML": -122}
        L.record_forecasts(led, WEEKS, mdl, NOW)
        self.assertEqual(led["2026-10-24-ANA-PHI"]["official"]["marketML"], [105, -122])
        self.assertIsNone(led["2026-10-24-SJS-BOS"]["official"]["marketML"])

    def test_void_only_when_gone_from_schedule_and_old(self):
        led = {}
        L.record_forecasts(led, WEEKS, model(), NOW)
        later = NOW + timedelta(days=3)
        self.assertEqual(L.find_void(led, WEEKS, later), [])                       # still on the schedule: never voided
        gone = {"wk": {"games": [g for g in WEEKS["wk"]["games"] if g["id"] != "2026-10-24-ANA-PHI"]}}
        self.assertEqual(L.find_void(led, gone, later), ["2026-10-24-ANA-PHI"])
        self.assertEqual(L.find_void(led, gone, NOW + timedelta(hours=20)), [])    # too soon: could be a late box score
        self.assertTrue(L.void(led, "2026-10-24-ANA-PHI", "test", later))
        self.assertFalse(L.void(led, "2026-10-24-ANA-PHI", "again", later))        # written once
        self.assertEqual(L.settle(led, [{"date": "2026-10-24", "away": "ANA", "home": "PHI", "ag": 1, "hg": 2}], later), [])
        self.assertEqual(L.summary(led).get("graded", 0), 0)                        # voided games are not graded
        self.assertEqual(L.summary(led).get("voided"), 1)

    def test_summary_bands_and_brier_vs_market(self):
        led = {}; mdl = model()
        mdl["games"]["2026-10-24-ANA-PHI"]["market"] = {"pHome": 0.53, "pAway": 0.47, "awayML": 105, "homeML": -122}
        L.record_forecasts(led, WEEKS, mdl, NOW)
        L.settle(led, [{"date": "2026-10-24", "away": "ANA", "home": "PHI", "ag": 1, "hg": 2},
                       {"date": "2026-10-24", "away": "SJS", "home": "BOS", "ag": 3, "hg": 2}], NOW + timedelta(hours=12))
        s = L.summary(led)
        self.assertEqual(s["graded"], 2); self.assertEqual(sum(b["n"] for b in s["bands"]), 2)
        self.assertEqual(s["vsMarket"]["games"], 1); self.assertIn("lockskiBrier", s["vsMarket"])
