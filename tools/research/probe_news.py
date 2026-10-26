"""Probe free news/injury sources for NHL (read-only)."""
import json, urllib.request
UA = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 lockski.ai/1.0", "accept": "application/json,text/html,application/xml"}
probes = {
  "espn_injuries": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries",
  "espn_news": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/news?limit=50",
  "dfo_news": "https://www.dailyfaceoff.com/hockey-player-news",
  "dfo_lines_tor": "https://www.dailyfaceoff.com/teams/toronto-maple-leafs/line-combinations",
  "rotowire_rss": "https://www.rotowire.com/rss/news.php?sport=NHL",
  "bsky_search": "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=%22starting%20goalie%22&limit=25&sort=latest",
}
log = {}
for k, u in probes.items():
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
            body = r.read().decode("utf-8", "replace"); log[k] = {"status": r.status, "bytes": len(body), "type": r.headers.get("content-type")}
    except Exception as e:
        body = str(e); log[k] = {"error": body[:200]}
    open(f"probe_{k}.txt", "w").write(body)
open("probe_news_log.json", "w").write(json.dumps(log, indent=1)); print(json.dumps(log, indent=1))
