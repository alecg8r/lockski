"""lockski.ai live engine: keeps everything current around the clock, no Claude run needed.

Each run (GitHub Actions, every ~20 minutes, and right after new inputs land):
  1. schedule      NHL schedule for 7 days back .. 14 days ahead            -> data/weeks.json
  2. results       every finished game since the season snapshot, from the NHL box score
                   (score, shots, power plays, goalies, every skater line)  -> data/inputs/increments.json
  3. inputs        goalies / injuries / odds from data/inputs/gameday.json (refreshed by the Claude lineup runs)
  4. model         engine/run.py with all of the above + play-by-play shot quality (data/xg_games.json)
  5. checks        32 teams, sane probabilities and scoring; nothing is published if a check fails
  6. publish       data/model.json, data/ledger.json, data/meta.json (Netlify redeploys on commit)
Forecasts are only ever added before puck drop (engine/ledger.py rules), results are written once.
"""
import json, os, shutil, subprocess, sys, tempfile, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA, INP = ROOT / "data", ROOT / "data" / "inputs"
ET = ZoneInfo("America/New_York")
SEASON_SNAPSHOT_THROUGH = "2026-10-22"   # engine/data/*_cur.csv cover games through this date
notes, errors = [], []


def get(path, retries=2):
    for i in range(retries + 1):
        try:
            req = urllib.request.Request("https://api-web.nhle.com/v1/" + path, headers={"user-agent": "lockski.ai"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception as e:
            if i == retries:
                raise
    return None


def load(p, default):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


# ---------------- 1. schedule ----------------
def schedule(today):
    games = {}
    for start in (today - timedelta(days=7), today, today + timedelta(days=7)):
        try:
            for d in get(f"schedule/{start}").get("gameWeek", []):
                for g in d.get("games", []):
                    if g.get("gameType") != 2 or g.get("gameScheduleState") in ("PPD", "CNCL"):
                        continue
                    a, h = g["awayTeam"]["abbrev"], g["homeTeam"]["abbrev"]
                    gid = f'{d["date"]}-{a}-{h}'
                    games[gid] = {"id": gid, "date": d["date"], "startUTC": g["startTimeUTC"].replace(".000", ""), "away": a, "home": h, "nhlId": g["id"]}
        except Exception as e:
            errors.append(f"schedule {start}: {e}")
    if not games:
        return None
    weeks = {}
    for g in sorted(games.values(), key=lambda x: x["startUTC"]):
        d = date.fromisoformat(g["date"]); mon = d - timedelta(days=d.weekday())
        wk = weeks.setdefault(f"wk-{mon}", {"weekId": f"wk-{mon}", "start": str(mon), "end": str(mon + timedelta(days=6)), "games": [], "partialDays": []})
        wk["games"].append({k: g[k] for k in ("id", "date", "startUTC", "away", "home")})
    return weeks, games


# ---------------- 2. results from the NHL box score ----------------
def mmss(t):
    try:
        m, s = str(t).split(":"); return round(int(m) + int(s) / 60, 2)
    except Exception:
        return 0.0


def box_increment(g):
    nid = g["nhlId"]
    box = get(f"gamecenter/{nid}/boxscore"); rail = get(f"gamecenter/{nid}/right-rail")
    if box.get("gameState") not in ("OFF", "FINAL"):
        return None
    pbp = get(f"gamecenter/{nid}/play-by-play")
    full = {r["playerId"]: f'{r["firstName"]["default"]} {r["lastName"]["default"]}' for r in pbp.get("rosterSpots", [])}
    A, H = box["awayTeam"], box["homeTeam"]
    pp = {"a": (0, 0), "h": (0, 0)}
    for st in rail.get("teamGameStats", []):
        if st.get("category") == "powerPlay":
            for side, key in (("a", "awayValue"), ("h", "homeValue")):
                try:
                    x, y = str(st[key]).split("/"); pp[side] = (int(x), int(y))
                except Exception:
                    pass
    inc = {"date": g["date"], "away": g["away"], "home": g["home"], "ag": A["score"], "hg": H["score"], "asog": A.get("sog"), "hsog": H.get("sog"),
           "appg": pp["a"][0], "appo": pp["a"][1], "hppg": pp["h"][0], "hppo": pp["h"][1],
           "winner": "away" if A["score"] > H["score"] else "home",
           "ot": (box.get("periodDescriptor") or {}).get("periodType", "REG") != "REG", "players": [], "src": f"nhl box score {nid}"}
    pg = box.get("playerByGameStats", {})
    for side, key, team in (("a", "awayTeam", g["away"]), ("h", "homeTeam", g["home"])):
        t = pg.get(key, {})
        gl = sorted(t.get("goalies", []), key=lambda x: mmss(x.get("toi")), reverse=True)
        if gl:
            x = gl[0]
            inc[side + "goalie"] = {"name": full.get(x["playerId"], x["name"]["default"]), "sa": int(x.get("shotsAgainst") or 0), "ga": int(x.get("goalsAgainst") or 0)}
        for x in t.get("forwards", []) + t.get("defense", []):
            inc["players"].append({"team": team, "name": full.get(x["playerId"], x["name"]["default"]), "g": int(x.get("goals") or 0),
                                   "a": int(x.get("assists") or 0), "sog": int(x.get("sog") or 0), "toi": mmss(x.get("toi"))})
    if inc["asog"] is None or inc["hsog"] is None or inc["ag"] == inc["hg"]:
        return None
    return inc


def results(games, today):
    incf = INP / "increments.json"
    inc = load(incf, {"games": [], "snapshot": f"MoneyPuck season files through {SEASON_SNAPSHOT_THROUGH}"})
    have = {(x["date"], x["away"], x["home"]) for x in inc["games"]}
    added = []
    for g in sorted(games.values(), key=lambda x: x["startUTC"]):
        if g["date"] <= SEASON_SNAPSHOT_THROUGH or (g["date"], g["away"], g["home"]) in have:
            continue
        if datetime.fromisoformat(g["startUTC"].replace("Z", "+00:00")) > datetime.now(timezone.utc) - timedelta(hours=2):
            continue
        try:
            r = box_increment(g)
            if r:
                inc["games"].append(r); added.append(g["id"])
        except Exception as e:
            errors.append(f"box {g['id']}: {e}")
    if added:
        inc["games"].sort(key=lambda x: (x["date"], x["away"]))
        done = {x["date"] for x in inc["games"]}
        inc["lastResultsDate"] = max(done)
        incf.write_text(json.dumps(inc, separators=(",", ":")))
    return inc, added


# ---------------- 3-6. run the engine, check, publish ----------------
def main():
    today = datetime.now(ET).date()
    sch = schedule(today)
    if not sch:
        errors.append("no schedule; keeping last published data")
        return finish(False, "schedule unavailable")
    weeks, games = sch
    inc, added = results(games, today)
    gd = load(INP / "gameday.json", {"starters": {}, "injuries": [], "odds": {}, "source_goalies": None, "source_injuries": None})
    gd["recent_games"] = [f'{g["date"]}|{g["away"]}|{g["home"]}' for g in games.values() if today - timedelta(days=7) <= date.fromisoformat(g["date"]) < today]
    gd.setdefault("source_goalies", None); gd.setdefault("source_injuries", None); gd.setdefault("odds", {})
    W = Path(tempfile.mkdtemp())
    (W / "weeks.json").write_text(json.dumps(weeks)); (W / "gameday.json").write_text(json.dumps(gd))
    (W / "increments.json").write_text(json.dumps(inc)); (W / "ledger.json").write_text((DATA / "ledger.json").read_text() if (DATA / "ledger.json").exists() else "{}")
    if (DATA / "xg_games.json").exists():
        shutil.copy(DATA / "xg_games.json", W / "xg_games.json")
    r = subprocess.run([sys.executable, str(ROOT / "engine" / "run.py"), str(W)], capture_output=True, text=True)
    if r.returncode != 0:
        errors.append("engine: " + (r.stderr or r.stdout)[-600:])
        return finish(False, "engine failed")
    m = json.loads((W / "model.json").read_text())
    led = json.loads((W / "ledger.json").read_text())
    # checks (same as the daily run's sanity step)
    ok, why = True, ""
    gs = list(m.get("games", {}).values())
    if len(m.get("teams", {})) != 32: ok, why = False, "teams != 32"
    elif any(abs(x["pHome"] + x["pAway"] - 1) > 0.001 or not (0.15 <= x["pHome"] <= 0.88) for x in gs): ok, why = False, "probability out of range"
    elif gs and not (5.4 <= sum(x["xgHome"] + x["xgAway"] for x in gs) / len(gs) <= 6.9): ok, why = False, "scoring level out of range"
    if not ok:
        errors.append("check failed: " + why)
        return finish(False, why)
    # keep finals the live job already wrote
    old = load(DATA / "ledger.json", {})
    for gid, doc in old.items():
        if doc.get("result") and gid in led and not led[gid].get("result"):
            led[gid]["result"] = doc["result"]
    # only write (and so only commit) when something real changed, so the repo history stays small
    vol = lambda d: {k: v for k, v in d.items() if k not in ("generatedAt", "run")}
    old_m = load(DATA / "model.json", {})
    changed = vol(old_m) != vol(m) or old != led or load(DATA / "weeks.json", {}) != weeks or bool(added)
    meta = load(DATA / "meta.json", {})
    stale = (datetime.now(timezone.utc) - datetime.fromisoformat(meta.get("lastSync", "2000-01-01T00:00:00+00:00").replace("Z", "+00:00"))) > timedelta(hours=6)
    if not changed and not stale:
        print("no change"); return
    (DATA / "model.json").write_text(json.dumps(m, separators=(",", ":")))
    (DATA / "ledger.json").write_text(json.dumps(led, separators=(",", ":")))
    (DATA / "weeks.json").write_text(json.dumps(weeks, separators=(",", ":")))
    notes.append(f"{len(added)} new results, {m.get('marketGames', 0)} games priced, {m.get('pbpShotQualityTeamGames', 0)} team-games with play-by-play shot quality")
    return finish(True, "", m.get("run"))


def finish(ok, why, run=None):
    meta = load(DATA / "meta.json", {})
    now = datetime.now(timezone.utc).isoformat()
    entry = {"at": now, "kind": "live", "ok": ok}
    if run: entry.update({k: run.get(k) for k in ("settled", "locked", "voided", "marketGames", "warnings")})
    if why: entry["reason"] = why
    if errors: entry["errors"] = errors[:5]
    meta["runLog"] = [entry] + [x for x in meta.get("runLog", []) if x.get("kind") == "live"][:29]
    if ok:
        meta["lastSync"] = now
    meta["nextRefresh"] = "live: every 10 minutes, around the clock"
    meta["source"] = "NHL schedule, box scores and play-by-play (live); goalies, injuries and odds from Daily Faceoff and CBS (lineup runs)"
    (DATA / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    print("ok" if ok else "FAILED", why, "|", "; ".join(notes), "|", errors[:5])


if __name__ == "__main__":
    main()
