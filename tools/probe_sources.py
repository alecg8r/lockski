"""One-off probe: fetch the pregame input pages from GitHub Actions and save the raw responses as artifacts,
so the parser can be written against what the pages really return (never guessed)."""
import urllib.request, sys, json, time
from datetime import datetime
from zoneinfo import ZoneInfo
today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 lockski.ai/1.0 (+https://lockski.netlify.app)"
urls = {"dfo": f"https://www.dailyfaceoff.com/starting-goalies/{today}", "cbs_inj": "https://www.cbssports.com/nhl/injuries/", "cbs_odds": "https://www.cbssports.com/nhl/odds/",
        "nhl_sched": f"https://api-web.nhle.com/v1/schedule/{today}"}
log = {}
for k, u in urls.items():
    t = time.time()
    try:
        req = urllib.request.Request(u, headers={"user-agent": UA, "accept": "text/html,application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read(); log[k] = {"status": r.status, "bytes": len(body), "ms": int((time.time() - t) * 1000), "ctype": r.headers.get("content-type")}
            open(f"probe_{k}.txt", "wb").write(body)
    except Exception as e:
        log[k] = {"error": str(e)}
print(json.dumps(log, indent=1)); open("probe_log.json", "w").write(json.dumps(log))
