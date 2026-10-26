"""Live prices from the prediction markets you can use in SC: Kalshi and Polymarket (both free, public, read-only).
-> data/inputs/markets.json, at most every 4 minutes. Attached to each game by the live engine (attach()).

A contract costs P dollars and pays $1 if right, so the multiplier is 1 / cost:
  Kalshi:     cost = ask + taker fee, fee = 0.07 * P * (1 - P) per contract (Kalshi's general fee schedule)
  Polymarket: cost = ask; shown as "before fees" because fees depend on the account (US app vs international)
Only prices someone is actually offering (an ask between 3 and 97 cents) are kept. Nothing is ever estimated."""
import json, os, re, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "data"
P = D / "inputs" / "markets.json"
UA = {"user-agent": "lockski.ai/1.0", "accept": "application/json"}
OURS = {"ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI",
        "NYR", "OTT", "PHI", "PIT", "SJS", "SEA", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG"}
ALIAS = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "LV": "VGK", "VEG": "VGK", "WAS": "WSH", "UTAH": "UTA", "ARI": "UTA", "MON": "MTL", "CLB": "CBJ", "NAS": "NSH", "CAL": "CGY", "WIN": "WPG"}
CODES = sorted(OURS | set(ALIAS), key=len, reverse=True)
MON = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def ab(x):
    x = (x or "").upper(); return x if x in OURS else ALIAS.get(x)


def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25) as r:
        return json.load(r)


def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def ok(p):
    return p is not None and 0.03 <= p <= 0.97


def kalshi_x(ask):   # multiplier after Kalshi's taker fee
    return round(1 / (ask + 0.07 * ask * (1 - ask)), 3)


def split_pair(s):   # "DETEDM" -> ("DET", "EDM") using known codes
    for a in CODES:
        if s.startswith(a) and ab(s[len(a):]) and ab(a):
            return ab(a), ab(s[len(a):])
    return None


def kalshi():
    K = "https://api.elections.kalshi.com/trade-api/v2/markets?status=open&limit=500&series_ticker="
    out = {}
    for m in get(K + "KXNHLGAME").get("markets", []):
        ev = re.match(r"KXNHLGAME-(\d\d)([A-Z]{3})(\d\d)([A-Z]+)$", m.get("event_ticker", ""))
        if not ev: continue
        pair = split_pair(ev.group(4)); team = ab(m["ticker"].rsplit("-", 1)[-1])
        if not pair or team not in pair: continue
        date = f"20{ev.group(1)}-{MON[ev.group(2)]:02d}-{ev.group(3)}"
        ask = num(m.get("yes_ask_dollars"))
        if ok(ask):
            out.setdefault(f"{date}-{pair[0]}-{pair[1]}", {}).setdefault("ml", {})[team] = {"ask": ask, "x": kalshi_x(ask)}
    for m in get(K + "KXNHLSPREAD").get("markets", []):
        if num(m.get("floor_strike")) != 1.5: continue           # "wins by over 1.5 goals" = that team -1.5
        ev = re.match(r"KXNHLSPREAD-(\d\d)([A-Z]{3})(\d\d)([A-Z]+)$", m.get("event_ticker", ""))
        if not ev: continue
        pair = split_pair(ev.group(4)); team = ab(re.sub(r"\d+$", "", m["ticker"].rsplit("-", 1)[-1]))
        if not pair or team not in pair: continue
        gid = f"20{ev.group(1)}-{MON[ev.group(2)]:02d}-{ev.group(3)}-{pair[0]}-{pair[1]}"; other = pair[1] if team == pair[0] else pair[0]
        ya, na = num(m.get("yes_ask_dollars")), num(m.get("no_ask_dollars"))
        pl = out.setdefault(gid, {}).setdefault("pl", {})
        if ok(ya): pl[f"{team}:-1.5"] = {"ask": ya, "x": kalshi_x(ya)}
        if ok(na): pl[f"{other}:+1.5"] = {"ask": na, "x": kalshi_x(na)}   # NO on "team wins by 2+" = other team +1.5
    return out


NICK = {"ducks": "ANA", "bruins": "BOS", "sabres": "BUF", "flames": "CGY", "hurricanes": "CAR", "blackhawks": "CHI", "avalanche": "COL",
        "blue jackets": "CBJ", "stars": "DAL", "red wings": "DET", "oilers": "EDM", "panthers": "FLA", "kings": "LAK", "wild": "MIN",
        "canadiens": "MTL", "predators": "NSH", "devils": "NJD", "islanders": "NYI", "rangers": "NYR", "senators": "OTT", "flyers": "PHI",
        "penguins": "PIT", "sharks": "SJS", "kraken": "SEA", "blues": "STL", "lightning": "TBL", "maple leafs": "TOR", "mammoth": "UTA",
        "utah": "UTA", "canucks": "VAN", "golden knights": "VGK", "capitals": "WSH", "jets": "WPG"}


def polymarket():
    out = {}
    for e in get("https://gamma-api.polymarket.com/events?tag_slug=nhl&closed=false&limit=200"):
        sm = re.match(r"nhl-([a-z]+)-([a-z]+)-(\d{4}-\d{2}-\d{2})$", e.get("slug", ""))
        if not sm: continue
        a, h = ab(sm.group(1)), ab(sm.group(2))
        if not (a and h): continue
        gid = f"{sm.group(3)}-{a}-{h}"
        for mk in e.get("markets", []):
            if mk.get("closed"): continue
            try: names = json.loads(mk.get("outcomes") or "[]")
            except Exception: continue
            bb, ba = num(mk.get("bestBid")), num(mk.get("bestAsk"))
            if len(names) != 2 or ba is None or bb is None: continue
            teams = [NICK.get(n.strip().lower()) for n in names]
            if set(teams) != {a, h}: continue                    # both outcomes must be this game's teams
            asks = [ba, round(1 - bb, 4)]                          # buy outcome 1 at its ask; outcome 2 costs 1 - outcome 1's bid
            t = mk.get("sportsMarketType")
            if t == "moneyline":
                for team, ask in zip(teams, asks):
                    if ok(ask): out.setdefault(gid, {}).setdefault("ml", {})[team] = {"ask": ask, "x": round(1 / ask, 3)}
            elif t == "spreads" and num(mk.get("line")) == -1.5:    # outcome 1 is the team giving 1.5
                for (team, ln), ask in zip(((teams[0], "-1.5"), (teams[1], "+1.5")), asks):
                    if ok(ask): out.setdefault(gid, {}).setdefault("pl", {})[f"{team}:{ln}"] = {"ask": ask, "x": round(1 / ask, 3)}
    return out


def main():
    cur = json.loads(P.read_text()) if P.exists() else {}
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(cur["at"])).total_seconds() / 60
        if age < 4 and os.environ.get("FORCE_INPUTS") != "1":
            print(f"markets: fetched {age:.0f} min ago"); return
    except Exception:
        pass
    new = {"at": datetime.now(timezone.utc).isoformat(), "venues": {}, "errors": {}}
    for name, fn in (("kalshi", kalshi), ("polymarket", polymarket)):
        try:
            new["venues"][name] = fn()
        except Exception as e:
            new["errors"][name] = str(e)
            if (cur.get("venues") or {}).get(name): new["venues"][name] = cur["venues"][name]   # keep the last good prices, marked by "errors"
    P.write_text(json.dumps(new, separators=(",", ":")))
    print("markets:", {k: len(v) for k, v in new["venues"].items()}, "errors:", new["errors"] or "none")


def attach(m):
    """model games get g["venues"] = {"kalshi": {"ml": {TEAM: {ask, x}}, "pl": {"TEAM:-1.5": {...}}}, "polymarket": {...}, "at": ...}"""
    try: mk = json.loads(P.read_text())
    except Exception: return 0
    n = 0
    for gid, g in (m.get("games") or {}).items():
        v = {name: ven[gid] for name, ven in (mk.get("venues") or {}).items() if gid in ven}
        if v: g["venues"] = {**v, "at": mk.get("at")}; n += 1
        else: g.pop("venues", None)
    return n


if __name__ == "__main__":
    main()
