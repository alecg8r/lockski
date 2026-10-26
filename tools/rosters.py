"""NHL rosters + official headshots for all 32 teams -> data/rosters.json (refreshed at most every 12 hours).
Source: api-web.nhle.com/v1/roster/<TEAM>/current (same public NHL feed as scores). Only names, ids, positions and the
headshot address are kept; the photos themselves stay on the NHL's servers (assets.nhle.com).
Also attaches headshot addresses to data/model.json players, goalies and each game's goalies (field "hs")."""
import json, re, sys, unicodedata, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "data"
TEAMS = ["ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI",
         "NYR", "OTT", "PHI", "PIT", "SJS", "SEA", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG"]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s.replace("-", " ")).strip()


def fetch_rosters():
    out = {}
    for t in TEAMS:
        try:
            req = urllib.request.Request(f"https://api-web.nhle.com/v1/roster/{t}/current", headers={"user-agent": "lockski.ai"})
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.load(r)
            rows = []
            for grp in ("forwards", "defensemen", "goalies"):
                for p in j.get(grp, []):
                    name = f'{(p.get("firstName") or {}).get("default", "")} {(p.get("lastName") or {}).get("default", "")}'.strip()
                    if p.get("id") and name and str(p.get("headshot", "")).startswith("https://"):
                        rows.append([p["id"], name, p.get("positionCode", ""), p["headshot"]])
            if rows: out[t] = rows
        except Exception as e:
            print("roster", t, e)
    return out


class Faces:
    def __init__(self, rosters):
        self.by = {}; self.last = {}
        for t, rows in rosters.items():
            for pid, name, pos, hs in rows:
                self.by[(t, norm(name))] = hs
                k = (t, norm(name).split(" ")[-1]); self.last.setdefault(k, []).append(hs)

    def get(self, name, team):
        n = norm(str(name).replace("✓", ""))
        if (team, n) in self.by: return self.by[(team, n)]
        c = self.last.get((team, n.split(" ")[-1] if n else ""), [])
        return c[0] if len(c) == 1 else None   # last name only when it's unique on that team


def main(force=False):
    rp = D / "rosters.json"
    cur = json.loads(rp.read_text()) if rp.exists() else {}
    age = None
    try: age = datetime.now(timezone.utc) - datetime.fromisoformat(cur["at"])
    except Exception: pass
    if force or age is None or age > timedelta(hours=12) or len(cur.get("teams", {})) < 32:
        r = fetch_rosters()
        if len(r) >= 30:   # never replace a good file with a partial one
            cur = {"at": datetime.now(timezone.utc).isoformat(), "source": "api-web.nhle.com/v1/roster/<team>/current", "teams": r}
            rp.write_text(json.dumps(cur, separators=(",", ":")))
            print("rosters:", sum(map(len, r.values())), "players on", len(r), "teams")
        else:
            print("rosters: only", len(r), "teams answered; keeping the previous file")
    mp = D / "model.json"
    if not mp.exists() or not cur.get("teams"): return
    m = json.loads(mp.read_text()); before = json.dumps(m, sort_keys=True)
    n, miss = attach(m, cur)
    if json.dumps(m, sort_keys=True) != before: mp.write_text(json.dumps(m, separators=(",", ":")))
    print(f"headshots: {n} hot-list players matched, {miss} without a photo")


def attach(m, cur=None):
    """Adds headshot addresses ("hs") to a model dict in place. Used by the live engine before it publishes."""
    if cur is None:
        try: cur = json.loads((D / "rosters.json").read_text())
        except Exception: return 0, 0
    f = Faces(cur.get("teams", {})); n = miss = 0
    for x in (m.get("players") or []) + (m.get("goalies") or []):
        hs = f.get(x.get("name"), x.get("team"))
        if hs: x["hs"] = hs; n += 1
        else: x.pop("hs", None); miss += 1
    for g in (m.get("games") or {}).values():
        for side, team in (("Home", g.get("home")), ("Away", g.get("away"))):
            hs = f.get(g.get("goalie" + side, ""), team)
            if hs: g["goalie" + side + "Hs"] = hs
    return n, miss

if __name__ == "__main__":
    main(force="--force" in sys.argv)
