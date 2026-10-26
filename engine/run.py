"""Lockski daily engine run.

Usage: python3 run.py WORKDIR
WORKDIR must contain: weeks.json, gameday.json, increments.json
Optional: ledger.json ({gameId: ledgerDoc} as last stored in the app database)
Writes: WORKDIR/model.json, WORKDIR/ledger.json (full) and WORKDIR/ledger_changes.json (only docs to write).
"""
import json, subprocess, sys, os
from datetime import datetime, timezone
from pathlib import Path

W = Path(sys.argv[1]); E = Path(__file__).parent
sys.path.insert(0, str(E))
import ledger as L
import market as MK

env = dict(os.environ, ENGINE_DATA=str(E / "data"), INCREMENTS_FILE=str(W / "increments.json"))
if (W / "xg_games.json").exists():
    env["XG_FILE"] = str(W / "xg_games.json")    # exact 5-on-5 shot quality from the NHL play-by-play (web app repo)
for args, label in [([sys.executable, E / "model.py", W / "model.json", W / "weeks.json", W / "gameday.json", W / "increments.json"], "model"),
                    ([sys.executable, E / "hot.py", W / "model.json"], "heat")]:
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, env=env)
    print(r.stdout[-1200:])
    if r.returncode != 0:
        print(r.stderr[-3000:]); sys.exit(f"{label} failed")

m = json.loads((W / "model.json").read_text())
replay = E / "data" / "replay.json"
m["replay"] = json.loads(replay.read_text()) if replay.exists() else None   # reproducible historical replay, clearly labeled
m.pop("backtest", None)                                                      # old unverified headline numbers removed
m.pop("record", None)

weeks = json.loads((W / "weeks.json").read_text())
gameday = json.loads((W / "gameday.json").read_text())
inc = json.loads((W / "increments.json").read_text())
led = json.loads((W / "ledger.json").read_text()) if (W / "ledger.json").exists() else {}
# Puck line (±1.5): P(team wins by 2+) from its win chance, calibrated on 2022-24 finals and checked on 2024-26
# (engine/data/puckline.json). Empty-net goals and OT/shootout (always 1-goal games) are in the data, not assumed.
PL = json.loads((E / "data" / "puckline.json").read_text())
import math
for g in m.get("games", {}).values():
    lg = lambda p: math.log(p / (1 - p))
    by2 = lambda p: round(1 / (1 + math.exp(-(PL["a"] + PL["b"] * lg(min(max(p, 1e-4), 1 - 1e-4))))), 4)
    g["pl"] = {"home": by2(g["pHome"]), "away": by2(g["pAway"])}   # chance each team wins by 2+ (covers -1.5)
MK.attach(m, weeks, gameday)                                                 # sportsbook prices ~ PrizePicks Team Pick prices
now = datetime.now(timezone.utc)
settled = L.settle(led, inc.get("games", []), now)
voided = [gid for gid in L.find_void(led, weeks, now) if L.void(led, gid, "no longer on the schedule (postponed or cancelled)", now)]
added = L.record_forecasts(led, weeks, m, now, inputs_as_of={"goalies": gameday.get("source_goalies"),
                                                             "injuries": gameday.get("source_injuries"),
                                                             "results": inc.get("lastResultsDate")})
changed = sorted(set(settled) | set(added) | set(voided))
(W / "ledger.json").write_text(json.dumps(led))
(W / "ledger_changes.json").write_text(json.dumps([led[i] for i in changed]))
m["ledgerSummary"] = L.summary(led)
m["generatedAt"] = now.isoformat()
# one-line record of this run for meta/status.runLog (the refresh prepends it; the app shows the latest)
m["run"] = {"at": now.isoformat(), "ok": True, "settled": len(settled), "locked": len(added), "voided": len(voided),
            "marketGames": m.get("marketGames", 0), "newGames": len(inc.get("games", [])), "warnings": (m.get("warnings") or [])[:5]}
(W / "model.json").write_text(json.dumps(m))
print(f"ledger: {len(added)} forecasts recorded, {len(settled)} games settled, {len(voided)} voided; official record {m['ledgerSummary']}")
