"""Grade finished games within minutes: reads the NHL's public score feed for today and yesterday (Eastern) and
writes the final into data/ledger.json for any game lockski made a pregame call on. A result is written once and
never changed; void/postponed games are left to the daily run. Exit code 0 always; prints what it changed."""
import json, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

D = Path(__file__).resolve().parents[1] / "data"
led_p = D / "ledger.json"
led = json.loads(led_p.read_text()) if led_p.exists() else {}
et = datetime.now(ZoneInfo("America/New_York"))
changed = []
for day in (et.date(), (et - timedelta(days=1)).date()):
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://api-web.nhle.com/v1/score/{day}", headers={"user-agent": "lockski.ai"}), timeout=20) as r:
            j = json.load(r)
    except Exception as e:
        print("feed error", day, e); continue
    for g in j.get("games", []):
        if g.get("gameState") not in ("OFF", "FINAL"):
            continue
        a, h = g["awayTeam"]["abbrev"], g["homeTeam"]["abbrev"]
        gid = f'{g["gameDate"]}-{a}-{h}'
        doc = led.get(gid)
        if not doc or doc.get("result") or not doc.get("official"):
            continue
        ag, hg = g["awayTeam"].get("score"), g["homeTeam"].get("score")
        if ag is None or hg is None or ag == hg:
            continue
        pt = (g.get("periodDescriptor") or {}).get("periodType", "REG")
        doc["result"] = {"winner": a if ag > hg else h, "ag": ag, "hg": hg, "ot": pt != "REG", "settledAt": datetime.now(timezone.utc).isoformat(), "source": "nhl live feed"}
        changed.append(gid)
if changed:
    led_p.write_text(json.dumps(led, separators=(",", ":")))
print("settled:", changed or "nothing new")
