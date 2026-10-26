"""Team news for every game, all the time (free, public pages and feeds, read a few times an hour):
  - Daily Faceoff player news: beat-writer reports (mostly from X), tagged goalie start / injury / lineup, with source
  - ESPN NHL injuries: a second injury list (status, return date, note) used to cross-check CBS
  - ESPN NHL news: headlines tagged by team
-> data/inputs/news.json (at most every 4 minutes). attach(model, gameday) puts each team's latest items in
model["news"] and adds ESPN injuries that CBS doesn't list to the model's injury input. Facts only; nothing invented."""
import json, os, re, unicodedata, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "data"
P = D / "inputs" / "news.json"
UA = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 lockski.ai/1.0",
      "accept": "application/json,text/html"}
NAME = {"Anaheim Ducks": "ANA", "Boston Bruins": "BOS", "Buffalo Sabres": "BUF", "Calgary Flames": "CGY", "Carolina Hurricanes": "CAR",
        "Chicago Blackhawks": "CHI", "Colorado Avalanche": "COL", "Columbus Blue Jackets": "CBJ", "Dallas Stars": "DAL", "Detroit Red Wings": "DET",
        "Edmonton Oilers": "EDM", "Florida Panthers": "FLA", "Los Angeles Kings": "LAK", "Minnesota Wild": "MIN", "Montreal Canadiens": "MTL",
        "Montréal Canadiens": "MTL", "Nashville Predators": "NSH", "New Jersey Devils": "NJD", "New York Islanders": "NYI", "New York Rangers": "NYR",
        "Ottawa Senators": "OTT", "Philadelphia Flyers": "PHI", "Pittsburgh Penguins": "PIT", "San Jose Sharks": "SJS", "Seattle Kraken": "SEA",
        "St. Louis Blues": "STL", "Tampa Bay Lightning": "TBL", "Toronto Maple Leafs": "TOR", "Utah Mammoth": "UTA", "Utah Hockey Club": "UTA",
        "Vancouver Canucks": "VAN", "Vegas Golden Knights": "VGK", "Washington Capitals": "WSH", "Winnipeg Jets": "WPG"}
CITY = {"ANA": "Anaheim", "BOS": "Boston", "BUF": "Buffalo", "CGY": "Calgary", "CAR": "Carolina", "CHI": "Chicago", "COL": "Colorado",
        "CBJ": "Columbus", "DAL": "Dallas", "DET": "Detroit", "EDM": "Edmonton", "FLA": "Florida", "LAK": "Los Angeles", "MIN": "Minnesota",
        "MTL": "Montreal", "NSH": "Nashville", "NJD": "New Jersey", "NYI": "N.Y. Islanders", "NYR": "N.Y. Rangers", "OTT": "Ottawa",
        "PHI": "Philadelphia", "PIT": "Pittsburgh", "SJS": "San Jose", "SEA": "Seattle", "STL": "St. Louis", "TBL": "Tampa Bay",
        "TOR": "Toronto", "UTA": "Utah", "VAN": "Vancouver", "VGK": "Vegas", "WSH": "Washington", "WPG": "Winnipeg"}
ALIAS = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "LV": "VGK", "WAS": "WSH", "UTAH": "UTA"}
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def get(u, js=True):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25) as r:
        b = r.read().decode("utf-8", "replace")
    return json.loads(b) if js else b


def nm(x):
    x = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z ]", " ", x).split())


def dfo():
    h = get("https://www.dailyfaceoff.com/hockey-player-news", js=False)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
    rows = json.loads(m.group(1))["props"]["pageProps"]["data"]["data"]
    out = []
    for x in rows:
        t = (x.get("teamAbbreviation") or "").upper(); t = ALIAS.get(t, t)
        if t not in CITY or not x.get("details"): continue
        out.append({"id": f"dfo{x['id']}", "src": "Daily Faceoff", "by": x.get("sourceName"), "team": t, "player": x.get("playerName"),
                    "pos": x.get("playerPosition"), "cat": x.get("newsCategoryName"), "strength": x.get("newsStrengthName"),
                    "text": x["details"].strip(), "at": x.get("createdAt"), "breaking": bool(x.get("breakingNews"))})
    return out


def espn_news():
    out = []
    for a in get("https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/news?limit=50").get("articles", []):
        teams = sorted({NAME[c["description"]] for c in a.get("categories", []) if c.get("description") in NAME})
        if not teams or not a.get("headline"): continue
        out.append({"id": f"espn{a.get('id')}", "src": "ESPN", "teams": teams, "text": a["headline"].strip(), "at": a.get("published"),
                    "url": ((a.get("links") or {}).get("web") or {}).get("href")})
    return out


def espn_injuries():
    out = []
    for t in get("https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries").get("injuries", []):
        team = NAME.get(t.get("displayName"))
        if not team: continue
        for i in t.get("injuries", []):
            ath = i.get("athlete") or {}; st = (i.get("status") or "").strip(); ret = ((i.get("details") or {}).get("returnDate") or "")[:10]
            if not ath.get("displayName") or not st: continue
            out.append({"team": team, "player": ath["displayName"], "pos": ((ath.get("position") or {}).get("abbreviation") or ""),
                        "status": st, "returnDate": ret or None, "note": (i.get("shortComment") or "").strip(), "at": i.get("date")})
    return out


def main():
    cur = json.loads(P.read_text()) if P.exists() else {}
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(cur["at"])).total_seconds() / 60
        if age < 4 and os.environ.get("FORCE_INPUTS") != "1":
            print(f"news: fetched {age:.0f} min ago"); return
    except Exception:
        pass
    new = {"at": datetime.now(timezone.utc).isoformat(), "items": [], "espnInjuries": cur.get("espnInjuries", []), "errors": {}}
    keep = {x["id"]: x for x in cur.get("items", [])}
    for name, fn in (("dailyfaceoff", dfo), ("espn_news", espn_news)):
        try:
            for x in fn(): keep[x["id"]] = x
        except Exception as e:
            new["errors"][name] = str(e)
    try:
        new["espnInjuries"] = espn_injuries()
    except Exception as e:
        new["errors"]["espn_injuries"] = str(e)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    new["items"] = sorted((x for x in keep.values() if (x.get("at") or "") >= cutoff[:19]), key=lambda x: x.get("at") or "", reverse=True)[:400]
    P.write_text(json.dumps(new, separators=(",", ":")))
    print("news:", len(new["items"]), "items,", len(new["espnInjuries"]), "ESPN injuries, errors:", new["errors"] or "none")


def espn_status(x):
    """ESPN injury -> the status wording the model reads (same as CBS: 'IR, Expected out until at least Oct 29')."""
    s = x["status"]; low = s.lower()
    if "day-to-day" in low: return "Day-to-Day"
    if x.get("returnDate"):
        y, mth, d = x["returnDate"].split("-"); when = f"{MON[int(mth) - 1]} {int(d)}"
        return f"{'IR, ' if 'reserve' in low else ''}Expected out until at least {when}"
    if "season" in low: return "Out for the season"
    return s


def merge_injuries(gd):
    """Adds ESPN injuries that CBS doesn't list (same team, same player by name) to gameday injuries. Returns count added."""
    try: news = json.loads(P.read_text())
    except Exception: return 0
    have = {(i[0], nm(i[1])) for i in gd.get("injuries", [])}; n = 0
    for x in news.get("espnInjuries", []):
        city = CITY[x["team"]]
        if (city, nm(x["player"])) in have: continue
        gd.setdefault("injuries", []).append([city, x["player"], x.get("pos") or "", espn_status(x)]); have.add((city, nm(x["player"]))); n += 1
    return n


def attach(m):
    """model["news"] = {TEAM: [latest items, newest first, max 6]} from the last 48 hours."""
    try: news = json.loads(P.read_text())
    except Exception: return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()[:19]
    by = {}
    for x in news.get("items", []):
        if (x.get("at") or "") < cutoff: continue
        for t in ([x["team"]] if x.get("team") else x.get("teams", [])):
            if len(by.setdefault(t, [])) < 6:
                by[t].append({k: x.get(k) for k in ("src", "by", "player", "cat", "strength", "text", "at", "url", "breaking") if x.get(k) not in (None, "", False)})
    m["news"] = by; m["newsAt"] = news.get("at")
    return sum(map(len, by.values()))


if __name__ == "__main__":
    main()
