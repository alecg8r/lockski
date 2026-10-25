"""lockski.ai pregame inputs, deterministic (no AI): starting goalies, injuries and sportsbook moneylines.

Runs on GitHub Actions about every 10 minutes, around the clock (see .github/workflows/inputs.yml) and writes
data/inputs/gameday.json in the shape engine/model.py and engine/market.py already read:
  {"kind", "updatedAt", "source_goalies", "source_injuries", "source_odds",
   "starters": {"YYYY-MM-DD": [[AWY, goalie, "Confirmed"|"Unconfirmed", HOM, goalie, status], ...]},
   "injuries": [[City, player, pos, status], ...],
   "odds": {"date": today, "source": "...", "lines": [[AWY, awayML, HOM, homeML, date], ...]},
   "sources": {name: {url, fetchedAt, ok, items, error, cadence}}}

Sources (public pages, read as a browser would, a few times an hour, only the facts kept — never the pages):
  goalies   Daily Faceoff starting-goalies page (server-rendered JSON in the page): names, Confirmed/other, source + time
  injuries  CBS Sports NHL injuries page: team, player, position, status text
  odds      CBS Sports NHL odds page: moneylines (stand-ins for PrizePicks Team Pick prices; the app says "estimated")
Rules: a section that cannot be fetched or parsed keeps the previous file's section if it is still for today
(or tomorrow), otherwise it is emptied. Nothing is ever invented. Every section carries its own timestamp.
Exit code is 0 unless the result would be invalid JSON; the live engine and the app treat missing sections honestly.
"""
import html as html_mod, json, re, sys, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
INP = ROOT / "data" / "inputs"
ET = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 lockski.ai/1.0 (+https://lockski.netlify.app)"
OURS = {"ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI",
        "NYR", "OTT", "PHI", "PIT", "SJS", "SEA", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG"}
ALIAS = {"LA": "LAK", "NJ": "NJD", "WAS": "WSH", "SJ": "SJS", "LV": "VGK", "VEG": "VGK", "CLB": "CBJ", "TB": "TBL", "MON": "MTL",
         "NAS": "NSH", "CAL": "CGY", "WIN": "WPG", "UTAH": "UTA", "ARI": "UTA", "NYI": "NYI", "NYR": "NYR"}
CITY = {"ANA": "Anaheim", "BOS": "Boston", "BUF": "Buffalo", "CGY": "Calgary", "CAR": "Carolina", "CHI": "Chicago", "COL": "Colorado",
        "CBJ": "Columbus", "DAL": "Dallas", "DET": "Detroit", "EDM": "Edmonton", "FLA": "Florida", "LAK": "Los Angeles", "MIN": "Minnesota",
        "MTL": "Montreal", "NSH": "Nashville", "NJD": "New Jersey", "NYI": "N.Y. Islanders", "NYR": "N.Y. Rangers", "OTT": "Ottawa",
        "PHI": "Philadelphia", "PIT": "Pittsburgh", "SJS": "San Jose", "SEA": "Seattle", "STL": "St. Louis", "TBL": "Tampa Bay",
        "TOR": "Toronto", "UTA": "Utah", "VAN": "Vancouver", "VGK": "Vegas", "WSH": "Washington", "WPG": "Winnipeg"}
MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def abbr(x):
    x = (x or "").strip().upper()
    return x if x in OURS else ALIAS.get(x)


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"user-agent": UA, "accept": "text/html,application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def strip(s):
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


# ---------------- schedule (what counts as a game) ----------------
def schedule_games():
    w = json.loads((ROOT / "data" / "weeks.json").read_text())
    out = {}
    for wk in w.values():
        for g in wk.get("games", []):
            out[(g["date"], g["away"], g["home"])] = g
    return out


# ---------------- goalies: Daily Faceoff ----------------
def goalies_for(day):
    url = f"https://www.dailyfaceoff.com/starting-goalies/{day}"
    page = get(url)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise ValueError("no __NEXT_DATA__ on page")
    data = json.loads(m.group(1))["props"]["pageProps"]["data"]
    rows = []
    for g in data:
        if g.get("date") != day:
            continue
        a, h = abbr(g.get("awayTeamAbbreviation")), abbr(g.get("homeTeamAbbreviation"))
        ag, hg = (g.get("awayGoalieName") or "").strip(), (g.get("homeGoalieName") or "").strip()
        if not (a and h and ag and hg):
            continue
        st = lambda s: "Confirmed" if (s or "").strip().lower() == "confirmed" else "Unconfirmed"
        rows.append([a, ag, st(g.get("awayNewsStrengthName")), h, hg, st(g.get("homeNewsStrengthName"))])
    return url, rows


# ---------------- injuries: CBS ----------------
def injuries():
    url = "https://www.cbssports.com/nhl/injuries/"
    page = get(url)
    out = []
    for sec in re.split(r'class="TableBase-title', page)[1:]:
        tm = re.search(r"/nhl/teams/([A-Z]+)/", sec[:800])
        team = abbr(tm.group(1)) if tm else None
        if not team:
            continue
        for tr in re.findall(r"<tr.*?</tr>", sec, re.S):
            cells = [strip(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
            if len(cells) < 5 or cells[0] == "Player":
                continue
            name = cells[0]
            # "P. Mintyukov Pavel Mintyukov" -> full name (the long form after the abbreviated one)
            mm = re.match(r"^[A-Z]\. ?\S+ (.+)$", name)
            if mm:
                name = mm.group(1).strip()
            status = cells[4].replace("IR. ", "IR, ").replace("Expected to be out", "Expected out")
            out.append([CITY[team], name, cells[1], status])
    if not out:
        raise ValueError("no injury rows parsed")
    return url, out


# ---------------- odds: CBS ----------------
def odds(today):
    url = "https://www.cbssports.com/nhl/odds/"
    page = get(url)
    lines = []
    for tbl in re.findall(r"<table.*?</table>", page, re.S):
        trs = re.findall(r"<tr.*?</tr>", tbl, re.S)
        if len(trs) < 3:
            continue
        head = [strip(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", trs[0], re.S)]
        if not head or "Moneyline" not in " ".join(head):
            continue
        dm = re.search(r"([A-Z][a-z]{2}) (\d{1,2}),", head[0])
        if not dm:
            continue
        mon, day = MON.get(dm.group(1)), int(dm.group(2))
        yr = today.year + (1 if mon == 1 and today.month == 12 else 0) - (1 if mon == 12 and today.month == 1 else 0)
        gdate = f"{yr:04d}-{mon:02d}-{day:02d}"
        col = next((i for i, c in enumerate(head) if "Moneyline" in c), None)
        teams = []
        for tr in trs[1:3]:
            cells = [strip(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
            if len(cells) <= col:
                break
            ab = abbr(cells[0].split(" ")[0])
            mlm = re.search(r"([+-]\d{3,4})", cells[col])
            if not (ab and mlm):
                break
            teams.append((ab, int(mlm.group(1))))
        if len(teams) == 2:
            (a, aml), (h, hml) = teams
            lines.append([a, aml, h, hml, gdate])
    if not lines:
        raise ValueError("no moneyline rows parsed")
    return url, lines


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "scheduled"
    import os
    try:   # rate limit: one fetch per ~9 minutes unless forced
        prev = json.loads((INP / "gameday.json").read_text()).get("updatedAt")
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(prev.replace("Z", "+00:00"))).total_seconds() / 60 if prev else 1e9
        if age < 9 and os.environ.get("FORCE_INPUTS") != "1":
            print(f"inputs: fetched {age:.0f} min ago, next fetch in {9 - age:.0f} min"); return
    except Exception:
        pass
    today = datetime.now(ET).date(); tomorrow = today + timedelta(days=1)
    days = {today.isoformat(), tomorrow.isoformat()}
    sched = schedule_games()
    old = {}
    try:
        old = json.loads((INP / "gameday.json").read_text())
    except Exception:
        pass
    out = {"kind": kind, "updatedAt": now_iso(), "starters": {}, "injuries": [], "odds": {}, "sources": {}}
    src = out["sources"]

    # goalies: today and tomorrow (tomorrow's projections let tomorrow's picks use the likely starter)
    starters, errs, n = {}, [], 0
    for d in sorted(days):
        try:
            url, rows = goalies_for(d)
            rows = [r for r in rows if (d, r[0], r[3]) in sched]
            if rows:
                starters[d] = rows; n += len(rows)
        except Exception as e:
            errs.append(f"{d}: {e}")
    if starters:
        out["starters"] = starters
        src["goalies"] = {"url": "https://www.dailyfaceoff.com/starting-goalies/", "fetchedAt": now_iso(), "ok": True, "items": n,
                          "confirmed": sum(1 for rs in starters.values() for r in rs for s in (r[2], r[5]) if s == "Confirmed"),
                          "error": "; ".join(errs) or None, "cadence": "about every 10 min, around the clock"}
        out["source_goalies"] = f"dailyfaceoff.com/starting-goalies (pulled {now_iso()})"
    else:   # keep the old section only while it is still for today/tomorrow
        keep = {d: r for d, r in (old.get("starters") or {}).items() if d in days}
        out["starters"] = keep
        src["goalies"] = {"url": "https://www.dailyfaceoff.com/starting-goalies/", "fetchedAt": now_iso(), "ok": False, "items": sum(map(len, keep.values())),
                          "error": "; ".join(errs) or "nothing for these dates", "keptPrevious": bool(keep), "cadence": "about every 10 min, around the clock"}
        out["source_goalies"] = old.get("source_goalies") if keep else None

    try:
        url, rows = injuries()
        out["injuries"] = rows
        src["injuries"] = {"url": url, "fetchedAt": now_iso(), "ok": True, "items": len(rows), "error": None, "cadence": "about every 10 min, around the clock"}
        out["source_injuries"] = f"cbssports.com/nhl/injuries (pulled {now_iso()})"
    except Exception as e:
        out["injuries"] = old.get("injuries") or []
        src["injuries"] = {"url": "https://www.cbssports.com/nhl/injuries/", "fetchedAt": now_iso(), "ok": False, "items": len(out["injuries"]),
                           "error": str(e), "keptPrevious": bool(out["injuries"]), "cadence": "about every 10 min, around the clock"}
        out["source_injuries"] = old.get("source_injuries")

    try:
        url, lines = odds(today)
        lines = [l for l in lines if (l[4], l[0], l[2]) in sched]
        if not lines:
            raise ValueError("no lines matched the schedule")
        out["odds"] = {"date": today.isoformat(), "source": f"{url} {now_iso()}", "lines": lines}
        src["odds"] = {"url": url, "fetchedAt": now_iso(), "ok": True, "items": len(lines), "error": None, "cadence": "about every 10 min, around the clock",
                       "note": "sportsbook moneylines, used as an estimate of PrizePicks prices"}
        out["source_odds"] = f"cbssports.com/nhl/odds (pulled {now_iso()})"
    except Exception as e:
        oo = old.get("odds") or {}
        keep = [l for l in oo.get("lines", []) if len(l) >= 5 and l[4] in days] if oo else []
        out["odds"] = {"date": today.isoformat(), "source": oo.get("source"), "lines": keep} if keep else {}
        src["odds"] = {"url": "https://www.cbssports.com/nhl/odds/", "fetchedAt": now_iso(), "ok": False, "items": len(keep), "error": str(e),
                       "keptPrevious": bool(keep), "cadence": "about every 10 min, around the clock"}
        out["source_odds"] = oo.get("source") if keep else None

    # validation: every starter / odds row is a scheduled game; no duplicates
    for d, rows in out["starters"].items():
        seen = set()
        for r in rows:
            assert (d, r[0], r[3]) in sched and (r[0], r[3]) not in seen, ("bad starter row", d, r)
            seen.add((r[0], r[3]))
    seen = set()
    for l in out["odds"].get("lines", []):
        assert (l[4], l[0], l[2]) in sched and (l[4], l[0], l[2]) not in seen, ("bad odds row", l)
        seen.add((l[4], l[0], l[2]))
    text = json.dumps(out, separators=(",", ":"))
    json.loads(text)
    (INP / "gameday.json").write_text(text)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "url"} for k, v in src.items()}, indent=1))


if __name__ == "__main__":
    main()
