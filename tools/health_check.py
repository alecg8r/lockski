"""Real health checks for lockski.ai -> data/health.json. Exit 1 only if something is OFFLINE.
Subsystems: site, api_bets, api_nhl, nhl_feed, data (model/ledger freshness), inputs (goalies/odds/injuries), shot_quality, engine (last cycle), actions.
Status: healthy | degraded | offline. Each has a plain-English detail and checkedAt."""
import json, os, subprocess, sys, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]; D = ROOT / "data"
SITE = "https://lockski.netlify.app"
NOW = datetime.now(timezone.utc); ET = datetime.now(ZoneInfo("America/New_York"))
out = {}


def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def age_min(s):
    try: return (NOW - ts(s)).total_seconds() / 60
    except Exception: return None


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"user-agent": "lockski-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def put(name, status, detail, **extra):
    out[name] = {"status": status, "detail": detail, "checkedAt": NOW.isoformat(), **extra}


def load(p):
    try: return json.loads((D / p).read_text())
    except Exception: return None


# --- site + functions (from outside, like a phone would) ---
try:
    st, body = fetch(SITE + "/"); b = body.decode("utf-8", "replace")
    ok = st == 200 and "lockski" in b and "renderBets" in b
    put("site", "healthy" if ok else "degraded", f"HTTP {st}, {len(body) // 1024} KB" + ("" if ok else ", page content unexpected"))
except Exception as e:
    put("site", "offline", f"not reachable: {e}")
try:
    st, body = fetch(SITE + "/api/bets"); j = json.loads(body)
    n = len(j.get("bets", {}))
    put("api_bets", "healthy" if st == 200 and isinstance(j.get("bets"), dict) else "degraded", f"HTTP {st}, {n} bets stored", bets=n)
except Exception as e:
    put("api_bets", "offline", f"bet store not answering: {e}")
try:
    st, body = fetch(SITE + "/api/nhl/score/now"); j = json.loads(body)
    live = sum(1 for g in j.get("games", []) if g.get("gameState") in ("LIVE", "CRIT"))
    put("api_nhl", "healthy" if st == 200 and "games" in j else "degraded", f"HTTP {st}, {len(j.get('games', []))} games today, {live} live", live=live)
except Exception as e:
    put("api_nhl", "offline", f"NHL proxy not answering: {e}")
try:
    st, body = fetch("https://api-web.nhle.com/v1/score/now"); j = json.loads(body)
    put("nhl_feed", "healthy" if st == 200 and "games" in j else "degraded", f"HTTP {st} from the NHL feed")
except Exception as e:
    put("nhl_feed", "offline", f"NHL feed down: {e}")

# --- data freshness (what the app reads) ---
meta, model, led, gd, xs = load("meta.json"), load("model.json"), load("ledger.json"), load("inputs/gameday.json"), load("xg_status.json")
a = age_min((meta or {}).get("lastSync"))
if a is None: put("data", "offline", "no lastSync in meta.json")
elif a <= 45: put("data", "healthy", f"picks published {a:.0f} min ago", ageMin=round(a))
elif a <= 180: put("data", "degraded", f"picks are {a / 60:.1f} h old (expected under 45 min)", ageMin=round(a))
else: put("data", "offline", f"picks are {a / 60:.1f} h old", ageMin=round(a))
runs = [r for r in (meta or {}).get("runLog", []) if r.get("kind") == "live"]
last = runs[0] if runs else None
if not last: put("engine", "offline", "no engine cycle recorded")
elif last.get("ok"): put("engine", "healthy", f"last cycle ok at {last['at'][:16]}Z: {last.get('locked', 0)} forecasts locked, {last.get('settled', 0)} graded, {last.get('marketGames', 0)} priced")
else:
    fails = 0
    for r in runs:
        if r.get("ok"): break
        fails += 1
    put("engine", "degraded" if fails < 6 else "offline", f"last {fails} cycle(s) failed: {last.get('reason', '?')}", consecutiveFailures=fails)

# --- pregame inputs (only matter from 9am ET until the last puck drop) ---
src = (gd or {}).get("sources") or {}
today = ET.date().isoformat()
games_today = [g for wk in (load("weeks.json") or {}).values() for g in wk.get("games", []) if g["date"] == today]
pending = [g for g in games_today if ts(g["startUTC"]) > NOW]
for name in ("goalies", "injuries", "odds"):
    s = src.get(name)
    if not s: put("inputs_" + name, "degraded", "never fetched by the deterministic fetcher yet"); continue
    ag = age_min(s.get("fetchedAt")); daytime = 9 <= ET.hour <= 22 and pending
    if s.get("ok") and ag is not None and ag <= 30:
        put("inputs_" + name, "healthy", f"{s.get('items', 0)} items, fetched {ag:.0f} min ago" + (f", {s.get('confirmed')} confirmed" if name == "goalies" else ""), ageMin=round(ag))
    elif s.get("ok") and not daytime:
        put("inputs_" + name, "healthy", f"{s.get('items', 0)} items (last fetched {ag / 60:.1f} h ago)", ageMin=round(ag or 0))
    else:
        put("inputs_" + name, "degraded", f"last fetch failed or stale ({s.get('error') or f'{ag:.0f} min old'})" + (", previous kept" if s.get("keptPrevious") else ""), ageMin=round(ag or 0))

# --- shot quality ---
ax = age_min((xs or {}).get("at"))
if ax is None: put("shot_quality", "degraded", "no xg_status.json")
elif (xs or {}).get("errors"): put("shot_quality", "degraded", f"{len(xs['errors'])} game(s) failed to score: {xs['errors'][:2]}")
else: put("shot_quality", "healthy" if ax <= 60 else "degraded", f"{(xs or {}).get('games', 0)} games scored, checked {ax:.0f} min ago")

# --- GitHub Actions: last completed 'live' run ---
try:
    r = subprocess.run(["gh", "run", "list", "--workflow", "live.yml", "-L", "5", "--json", "conclusion,createdAt,status"], capture_output=True, text=True, timeout=30,
                       env={**os.environ, "GH_REPO": "alecg8r/lockski"})
    rl = json.loads(r.stdout or "[]"); done = [x for x in rl if x["status"] == "completed"]
    if not done: put("actions", "degraded", "no completed live runs found")
    else:
        la = age_min(done[0]["createdAt"]); bad = sum(1 for x in done if x["conclusion"] not in ("success", "cancelled", "skipped"))
        put("actions", "healthy" if la <= 30 and bad == 0 else "degraded" if la <= 120 else "offline", f"last live run {la:.0f} min ago, {bad} of last {len(done)} failed", ageMin=round(la))
except Exception as e:
    put("actions", "degraded", f"could not read workflow runs: {e}")

rank = {"healthy": 0, "degraded": 1, "offline": 2}
overall = max(out.values(), key=lambda x: rank[x["status"]])["status"]
health = {"at": NOW.isoformat(), "status": overall, "subsystems": out,
          "legend": {"healthy": "working as expected", "degraded": "working with stale or partial data", "offline": "not working"}}
(D / "health.json").write_text(json.dumps(health, separators=(",", ":")))
for k, v in out.items(): print(f"{v['status']:9} {k}: {v['detail']}")
print("overall:", overall)
sys.exit(1 if overall == "offline" else 0)
