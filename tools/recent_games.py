"""Every finished regular-season game of the last 35 days from the NHL score feed -> data/inputs/recent.json.
Used only for the model's recent form (each team's last 10 games: shots on goal and goals), so it covers games
from before the season snapshot too. Past days are cached; only today and the previous 2 days are re-read."""
import json, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

P = Path(__file__).resolve().parents[1] / "data" / "inputs" / "recent.json"
today = datetime.now(ZoneInfo("America/New_York")).date()
cur = json.loads(P.read_text()) if P.exists() else {"days": {}}
days = cur.get("days", {})
fetched = 0
for i in range(35, -1, -1):
    d = (today - timedelta(days=i)).isoformat()
    if d in days and i > 2:
        continue
    try:
        req = urllib.request.Request(f"https://api-web.nhle.com/v1/score/{d}", headers={"user-agent": "lockski.ai"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.load(r)
        out = []
        for g in j.get("games", []):
            a, h = g.get("awayTeam", {}), g.get("homeTeam", {})
            if g.get("gameType") != 2 or g.get("gameState") not in ("OFF", "FINAL"): continue
            if None in (a.get("score"), h.get("score"), a.get("sog"), h.get("sog")) or a.get("score") == h.get("score"): continue
            out.append({"date": d, "away": a["abbrev"], "home": h["abbrev"], "ag": a["score"], "hg": h["score"], "asog": a["sog"], "hsog": h["sog"]})
        days[d] = out; fetched += 1
    except Exception as e:
        print("recent", d, e)
keep = {(today - timedelta(days=i)).isoformat() for i in range(36)}
days = {d: v for d, v in days.items() if d in keep}
games = sorted((g for v in days.values() for g in v), key=lambda g: (g["date"], g["away"]))
new = {"days": days, "games": games}
if new != {k: cur.get(k) for k in ("days", "games")}:
    P.write_text(json.dumps(new, separators=(",", ":")))
print(f"recent games: {len(games)} finished games over {len(days)} days ({fetched} days re-read)")
