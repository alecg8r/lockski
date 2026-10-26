"""Research helper: date of every regular-season game in 2022-23 .. 2025-26 from the NHL schedule feed
(weekly pages), keyed by MoneyPuck-style game_id (e.g. 20001) per season. Output: research_dates.json"""
import json, urllib.request
from datetime import date, timedelta
out = {}
for season, (a, b) in {2022: ("2022-10-07", "2023-04-14"), 2023: ("2023-10-10", "2024-04-18"), 2024: ("2024-10-04", "2025-04-17"), 2025: ("2025-10-07", "2026-04-16")}.items():
    d = date.fromisoformat(a); end = date.fromisoformat(b); got = {}
    while d <= end:
        try:
            with urllib.request.urlopen(urllib.request.Request(f"https://api-web.nhle.com/v1/schedule/{d}", headers={"user-agent": "lockski.ai"}), timeout=30) as r:
                j = json.load(r)
            for day in j.get("gameWeek", []):
                for g in day.get("games", []):
                    if g.get("gameType") == 2 and str(g["id"])[:4] == str(season):
                        got[str(int(str(g["id"])[4:]))] = [day["date"], g["awayTeam"]["abbrev"], g["homeTeam"]["abbrev"], g.get("venueUTCOffset")]
        except Exception as e:
            print("week", d, e)
        d += timedelta(days=7)
    out[str(season)] = got; print(season, len(got))
open("research_dates.json", "w").write(json.dumps(out))
