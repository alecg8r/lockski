"""Probe Kalshi and Polymarket public market data for NHL games (no login). Saves raw responses."""
import json, urllib.request, urllib.parse
UA = {"user-agent": "lockski.ai/1.0", "accept": "application/json"}
def get(u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)
K = "https://api.elections.kalshi.com/trade-api/v2"
P = "https://gamma-api.polymarket.com"
probes = {
  "kalshi_series_sports": K + "/series?category=Sports",
  "kalshi_events_nhlgame": K + "/events?series_ticker=KXNHLGAME&status=open&with_nested_markets=true&limit=50",
  "kalshi_markets_nhlgame": K + "/markets?series_ticker=KXNHLGAME&status=open&limit=200",
  "kalshi_markets_nhlspread": K + "/markets?series_ticker=KXNHLSPREAD&status=open&limit=200",
  "poly_sports": P + "/sports",
  "poly_events_nhl": P + "/events?tag_slug=nhl&closed=false&limit=100",
}
log = {}
for k, u in probes.items():
    st, body = get(u); log[k] = {"url": u, "status": st, "bytes": len(body)}
    open(f"probe_{k}.json", "w").write(body)
open("probe_markets_log.json", "w").write(json.dumps(log, indent=1)); print(json.dumps(log, indent=1))
