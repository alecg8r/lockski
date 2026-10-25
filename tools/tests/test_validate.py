"""The publish gate: good data passes, every kind of bad data is caught."""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
R = Path(__file__).resolve().parents[2]


def run(mutate):
    d = Path(tempfile.mkdtemp()); shutil.copytree(R / "data", d / "data")
    mutate(d / "data")
    return subprocess.run([sys.executable, str(R / "tools" / "validate_data.py"), str(d / "data")], capture_output=True, text=True)


def edit(name, fn):
    def m(d):
        p = d / name; j = json.loads(p.read_text()); fn(j); p.write_text(json.dumps(j))
    return m


class TestValidate(unittest.TestCase):
    def test_current_data_ok(self):
        self.assertEqual(run(lambda d: None).returncode, 0)

    def test_blank_file(self):
        self.assertEqual(run(lambda d: (d / "model.json").write_text("")).returncode, 1)

    def test_missing_file(self):
        self.assertEqual(run(lambda d: (d / "ledger.json").unlink()).returncode, 1)

    def test_bad_probability(self):
        def f(j): next(iter(j["games"].values()))["pHome"] = 1.4
        self.assertEqual(run(edit("model.json", f)).returncode, 1)

    def test_probs_not_summing(self):
        def f(j): g = next(iter(j["games"].values())); g["pHome"] = 0.6; g["pAway"] = 0.6
        self.assertEqual(run(edit("model.json", f)).returncode, 1)

    def test_missing_team(self):
        def f(j): j["teams"].pop("BOS")
        self.assertEqual(run(edit("model.json", f)).returncode, 1)

    def test_duplicate_game(self):
        def f(j): wk = next(iter(j.values())); wk["games"].append(dict(wk["games"][0]))
        self.assertEqual(run(edit("weeks.json", f)).returncode, 1)

    def test_same_team_both_sides(self):
        def f(j): wk = next(iter(j.values())); wk["games"][0]["home"] = wk["games"][0]["away"]
        self.assertEqual(run(edit("weeks.json", f)).returncode, 1)

    def test_snapshot_after_puck_drop(self):
        def f(j):
            d = next(iter(j.values())); s = dict(d["snapshots"][-1]); s["at"] = "2099-01-01T00:00:00+00:00"; d["snapshots"].append(s)
        self.assertEqual(run(edit("ledger.json", f)).returncode, 1)

    def test_bad_starter_row(self):
        def f(j): j["starters"] = {"2026-10-24": [["ANA", "X", "Maybe", "PHI", "Y", "Confirmed"]]}
        self.assertEqual(run(edit("inputs/gameday.json", f)).returncode, 1)


if __name__ == "__main__":
    unittest.main()
