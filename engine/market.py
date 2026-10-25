"""Market prices for PrizePicks Team Picks.

PrizePicks Team Picks are event contracts priced 1-99 cents that pay $1 if right, so the price is the
market's win probability. PrizePicks has no public price feed reachable from the refresh, so the refresh
reads sportsbook moneylines (CBS Sports odds page) and converts them to a no-vig probability: the
estimated fair market price. Typed PrizePicks prices in the app always override the estimate.

gameday.json may carry: "odds": {"date": "YYYY-MM-DD", "source": "...", "lines": [[AWY, awayML, HOM, homeML(, date)], ...]}
A line's own 5th value (game date) wins over odds.date, so one file can price today's and tomorrow's games.
"""


def implied(ml):
    ml = float(ml)
    return -ml / (-ml + 100) if ml < 0 else 100 / (ml + 100)


def valid_pair(a, h):
    """Moneylines must look like a real two-way market: |ML| >= 100 and a 0-12% bookmaker margin."""
    try:
        a, h = float(a), float(h)
    except (TypeError, ValueError):
        return False
    if abs(a) < 100 or abs(h) < 100 or abs(a) > 1500 or abs(h) > 1500:
        return False
    s = implied(a) + implied(h)
    return 1.0 <= s <= 1.12


def attach(model, weeks, gameday):
    """Adds game['market'] = {pHome, pAway, awayML, homeML, source} to matching games. Returns count."""
    od = (gameday or {}).get("odds") or {}
    date, lines = od.get("date"), od.get("lines") or []
    ids = {}
    for wk in weeks.values():
        for g in wk.get("games", []):
            ids[(g["date"], g["away"], g["home"])] = g["id"]
    n = 0
    for row in lines:
        try:
            away, aml, home, hml = row[:4]
            rdate = row[4] if len(row) > 4 and row[4] else date
        except (TypeError, ValueError, IndexError):
            continue
        gid = ids.get((rdate, away, home))
        g = model.get("games", {}).get(gid)
        if not g or not valid_pair(aml, hml):
            continue
        ia, ih = implied(aml), implied(hml)
        g["market"] = {"pHome": round(ih / (ia + ih), 4), "pAway": round(ia / (ia + ih), 4),
                       "awayML": int(float(aml)), "homeML": int(float(hml)), "source": od.get("source")}
        n += 1
    model["marketGames"] = n
    return n
