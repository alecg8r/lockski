"""Nightly shot quality: score every 5-on-5 shot of every finished NHL game with lockski's expected-goals model
(tools/xg_model.json, trained on 2021-24 shot data, tested on 2024-26) and write per-team game totals to
data/xg_games.json. The daily engine run replaces its shot-count estimates with these exact numbers.

Per game: {date, away, home, axg5, hxg5 (expected goals), aff5, hff5 (unblocked attempts), acf5p, hcf5p (all attempts),
asog5, hsog5 (shots on goal), ag5, hg5 (goals), n (shots scored), src}. Only 5-on-5 with both goalies in net."""
import json, math, sys, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
M = json.loads((ROOT / "tools" / "xg_model.json").read_text())
OUT = ROOT / "data" / "xg_games.json"
STATUS = ROOT / "data" / "xg_status.json"
TYPES = M["types"]
TMAP = {"wrist": "WRIST", "snap": "SNAP", "slap": "SLAP", "backhand": "BACK", "tip-in": "TIP", "deflected": "DEFL", "wrap-around": "WRAP"}
SHOTS = {"shot-on-goal", "missed-shot", "goal", "blocked-shot"}


def get(path):
    req = urllib.request.Request("https://api-web.nhle.com/v1/" + path, headers={"user-agent": "lockski.ai"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def secs(p):
    mm, ss = (p.get("timeInPeriod") or "0:0").split(":")
    return ((p.get("periodDescriptor") or {}).get("number", 1) - 1) * 1200 + int(mm) * 60 + int(ss)


def xg(dist, ang, typ, reb, rush):
    f = {"ldist": math.log(dist), "dist": dist / 10, "ang": ang / 10, "ang2": (ang / 10) ** 2, "da": dist / 10 * ang / 10,
         "reb": reb, "rebang": reb * ang / 10, "rush": rush}
    for t in TYPES[1:]:
        f["t_" + t] = 1 if typ == t else 0
    z = M["intercept"] + sum(c * f[k] for k, c in zip(M["features"], M["coef"]))
    return 1 / (1 + math.exp(-z))


def score_game(g):
    pbp = get(f"gamecenter/{g['id']}/play-by-play")
    home_id, away_id = pbp["homeTeam"]["id"], pbp["awayTeam"]["id"]
    team_of = {r["playerId"]: r["teamId"] for r in pbp.get("rosterSpots", [])}
    tot = {s: {"xg5": 0.0, "ff5": 0, "cf5p": 0, "sog5": 0, "g5": 0} for s in ("a", "h")}
    prev = None; n = 0
    for p in pbp.get("plays", []):
        typ = p.get("typeDescKey"); d = p.get("details") or {}
        if (p.get("periodDescriptor") or {}).get("periodType") == "SO":
            break
        if typ in SHOTS:
            shooter = d.get("shootingPlayerId") or d.get("scoringPlayerId")
            tid = team_of.get(shooter)
            if tid is None:   # blocked shots are owned by the blocking team in this feed
                own = d.get("eventOwnerTeamId"); tid = (away_id if own == home_id else home_id) if typ == "blocked-shot" else own
            side = "h" if tid == home_id else "a"
            if p.get("situationCode") == "1551":
                tot[side]["cf5p"] += 1
                if typ != "blocked-shot":
                    x, y = d.get("xCoord"), d.get("yCoord")
                    if x is not None and y is not None:
                        defend = p.get("homeTeamDefendingSide")
                        if defend in ("left", "right"):
                            right = (defend == "left") if side == "h" else (defend == "right")
                            xa, ya = (x, y) if right else (-x, -y)
                        else:
                            xa, ya = abs(x), y
                        dist = max(1.0, min(200.0, math.hypot(89 - xa, ya)))
                        ang = math.degrees(math.atan2(abs(ya), max(0.5, 89 - xa)))
                        st = TMAP.get((d.get("shotType") or "").lower(), "WRIST")
                        reb = rush = 0
                        if prev is not None:
                            dt = secs(p) - secs(prev)
                            ps = prev.get("typeDescKey"); pd_ = prev.get("details") or {}
                            pteam = pd_.get("eventOwnerTeamId")
                            same = (pteam == tid) if ps != "blocked-shot" else (pteam != tid)
                            if ps in SHOTS and same and 0 <= dt <= 3:
                                reb = 1
                            elif ps not in SHOTS and 0 <= dt <= 4:
                                z = pd_.get("zoneCode")
                                rush = int((z in ("N", "D")) if pteam == tid else (z in ("N", "O")))
                        tot[side]["xg5"] += xg(dist, ang, st, reb, rush); tot[side]["ff5"] += 1; n += 1
                        if typ in ("shot-on-goal", "goal"):
                            tot[side]["sog5"] += 1
                        if typ == "goal":
                            tot[side]["g5"] += 1
        if typ in SHOTS or typ in ("faceoff", "hit", "giveaway", "takeaway", "stoppage", "penalty"):
            prev = p
    out = {"date": g["gameDate"], "away": g["awayTeam"]["abbrev"], "home": g["homeTeam"]["abbrev"], "n": n, "src": f"nhl play-by-play {g['id']}"}
    for s in ("a", "h"):
        for k, v in tot[s].items():
            out[s + k] = round(v * M["mpScale"], 3) if k == "xg5" else v
    return out


def main():
    data = json.loads(OUT.read_text()) if OUT.exists() else {}
    et = datetime.now(ZoneInfo("America/New_York")).date()
    done, errs = [], []
    for back in range(0, 4):
        day = et - timedelta(days=back)
        try:
            games = get(f"score/{day}").get("games", [])
        except Exception as e:
            errs.append(f"score {day}: {e}"); continue
        for g in games:
            if g.get("gameState") not in ("OFF", "FINAL") or g.get("gameType") not in (2, None):
                continue
            gid = f"{g['gameDate']}-{g['awayTeam']['abbrev']}-{g['homeTeam']['abbrev']}"
            if gid in data:
                continue
            try:
                r = score_game(g)
                # sanity: plausible 5v5 volumes, both sides present
                if not (15 <= r["aff5"] + r["hff5"] <= 160 and 0.5 <= r["axg5"] + r["hxg5"] <= 12):
                    errs.append(f"{gid}: implausible totals {r}"); continue
                data[gid] = r; done.append(gid)
            except Exception as e:
                errs.append(f"{gid}: {e}")
    if done:
        OUT.write_text(json.dumps(dict(sorted(data.items())), separators=(",", ":")))
    STATUS.write_text(json.dumps({"at": datetime.utcnow().isoformat() + "Z", "added": done, "errors": errs[:20], "games": len(data)}, indent=1))
    print("xg added:", done or "none", "| errors:", errs[:5])


if __name__ == "__main__":
    main()
