"""Lockski prediction ledger: append-only pregame forecasts, one official forecast per game.

Storage: one document per game (collection "ledger" in the app database), keyed by game id
"YYYY-MM-DD-AWY-HOM" (Eastern date). Each document:
  {id, date, away, home, startUTC,
   snapshots: [ {at, version, config, pHome, pAway, xgHome, xgAway, goalies, goalieConfirmed,
                 inputsAsOf, factors} ... ]      # append-only, only ever added BEFORE startUTC
   official: <copy of the last pregame snapshot>  # what public performance is graded on
   experiments: [ ... ]                          # non-production models, never in public record
   result: {winner, ag, hg, ot, settledAt} | absent}

Rules (enforced here, tested in tests/test_ledger.py):
  1. A snapshot is only recorded if now < startUTC. Nothing about a game changes after puck drop
     except adding its result.
  2. Re-running with an identical forecast adds nothing (idempotent).
  3. Exactly one official forecast per game, so a model version change can't double-count a game.
  4. A result is written once; later runs never overwrite it.
  5. A game that vanishes from the schedule 2-8 days after its start with no result is voided (result.void = true),
     never guessed. Voided games are left out of the record; the app returns bets on them.
Each snapshot also records the sportsbook moneylines at lock time (marketML = [awayML, homeML]) so the forward
record can be priced later without touching the past.
"""
import hashlib, json
from datetime import datetime, timezone, timedelta

WINDOW_HOURS = 36


def utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def config_hash(model):
    keep = {k: model.get(k) for k in ("version", "assumptions")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:12]


def record_forecasts(ledger, weeks, model, now=None, channel="production", inputs_as_of=None):
    """Add pregame snapshots for games starting within WINDOW_HOURS. Returns ids of changed docs."""
    now = now or datetime.now(timezone.utc)
    cfg = config_hash(model)
    changed = []
    for wk in weeks.values():
        for g in wk.get("games", []):
            m = model.get("games", {}).get(g["id"])
            if not m:
                continue
            try:
                start = utc(g["startUTC"]); ph = float(m["pHome"]); pa = float(m["pAway"])
            except (KeyError, ValueError, TypeError):
                continue
            if not (0 < ph < 1 and abs(ph + pa - 1) < 0.002):
                continue
            if not (now < start <= now + timedelta(hours=WINDOW_HOURS)):
                continue
            doc = ledger.get(g["id"]) or {"id": g["id"], "date": g["date"], "away": g["away"], "home": g["home"],
                                          "startUTC": g["startUTC"], "snapshots": [], "experiments": []}
            if doc.get("result"):
                continue
            snap = {"at": now.isoformat(), "version": model.get("version"), "config": cfg,
                    "pHome": round(ph, 4), "pAway": round(pa, 4),
                    "xgHome": m.get("xgHome"), "xgAway": m.get("xgAway"),
                    "goalies": [m.get("goalieAway"), m.get("goalieHome")],
                    "goalieConfirmed": bool(m.get("goalieConfirmed")),
                    "inputsAsOf": inputs_as_of, "factors": m.get("factors", []),
                    "inputsMissing": m.get("inputsMissing", []),
                    "market": (m.get("market") or {}).get("pHome"),    # sportsbook no-vig home price at lock time
                    "marketML": [(m.get("market") or {}).get("awayML"), (m.get("market") or {}).get("homeML")] if m.get("market") else None}
            key = "snapshots" if channel == "production" else "experiments"
            last = doc[key][-1] if doc[key] else None
            if (last and last["pHome"] == snap["pHome"] and last["config"] == cfg and last["goalies"] == snap["goalies"]
                    and (last.get("market") is not None or snap["market"] is None)):
                continue                                   # identical forecast: nothing new
            doc[key].append(snap)
            if channel == "production":
                doc["official"] = snap
            doc["startUTC"] = g["startUTC"]                # schedule time can move before puck drop
            ledger[g["id"]] = doc
            changed.append(g["id"])
    return changed


def settle(ledger, results, now=None):
    """results: [{date, away, home, ag, hg, winner:'home'|'away', ot}] from box scores. Returns ids settled."""
    now = (now or datetime.now(timezone.utc)).isoformat()
    changed = []
    for r in results:
        gid = f'{r["date"]}-{r["away"]}-{r["home"]}'
        doc = ledger.get(gid)
        if not doc or doc.get("result") or not doc.get("official"):
            continue
        if r.get("winner") in ("home", "away"):
            winner = r[r["winner"]]
        elif r.get("ag") is not None and r.get("hg") is not None and r["ag"] != r["hg"]:
            winner = r["away"] if r["ag"] > r["hg"] else r["home"]
        else:
            continue
        doc["result"] = {"winner": winner, "ag": r.get("ag"), "hg": r.get("hg"), "ot": bool(r.get("ot")), "settledAt": now}
        changed.append(gid)
    return changed


def void(ledger, gid, reason, now=None):
    """Mark a game as called off (postponed/cancelled). Written once; a settled game is never voided."""
    doc = ledger.get(gid)
    if not doc or doc.get("result"):
        return False
    now = (now or datetime.now(timezone.utc)).isoformat()
    doc["result"] = {"void": True, "reason": reason, "settledAt": now}
    return True


def find_void(ledger, weeks, now=None, min_days=2, max_days=8):
    """Games with an official forecast, no result, that started 2-8 days ago and are no longer on the schedule
    (a rescheduled game gets a new id). Returns ids. Nothing is voided automatically for a merely missing box score."""
    now = now or datetime.now(timezone.utc)
    on_sched = {g["id"] for wk in weeks.values() for g in wk.get("games", [])}
    out = []
    for gid, d in ledger.items():
        if d.get("result") or not d.get("official") or gid in on_sched:
            continue
        try:
            age = (now - utc(d["startUTC"])).total_seconds() / 86400
        except (KeyError, ValueError):
            continue
        if min_days <= age <= max_days:
            out.append(gid)
    return out


def summary(ledger):
    """Headline numbers from official forecasts only (one per game)."""
    import math
    rows = []
    for d in ledger.values():
        o, r = d.get("official"), d.get("result")
        if not o or not r or r.get("void"):
            continue
        ph = o["pHome"]; pick = d["home"] if ph >= 0.5 else d["away"]; p = max(ph, 1 - ph)
        rows.append((p, pick == r["winner"]))
    n = len(rows)
    voided = sum(1 for d in ledger.values() if (d.get("result") or {}).get("void"))
    if not n:
        return {"graded": 0, "voided": voided}
    out = {"graded": n, "accuracy": round(sum(c for _, c in rows) / n, 4),
           "brier": round(sum((p - c) ** 2 for p, c in rows) / n, 4),
           "logLoss": round(-sum(math.log(p if c else 1 - p) for p, c in rows) / n, 4),
           "voided": voided}
    # calibration by confidence band (forward test; sample sizes shown so small counts aren't over-read)
    out["bands"] = []
    for lo, hi in [(.5, .55), (.55, .6), (.6, .65), (.65, .7), (.7, 1.01)]:
        b = [(p, c) for p, c in rows if lo <= p < hi]
        out["bands"].append({"label": f"{int(lo*100)}–{int(hi*100)}%" if hi < 1 else "70%+", "n": len(b),
                             "predicted": round(sum(p for p, _ in b) / len(b), 4) if b else None,
                             "actual": round(sum(c for _, c in b) / len(b), 4) if b else None})
    # head-to-head with the market on games where a market price was saved at lock time
    mk = []
    for d in ledger.values():
        o, r = d.get("official"), d.get("result")
        if not o or not r or r.get("void") or not o.get("market"):
            continue
        hw = r["winner"] == d["home"]; pl, pm = o["pHome"], o["market"]
        mk.append((math.log(pl if hw else 1 - pl), math.log(pm if hw else 1 - pm), (pl >= .5) == hw, (pm >= .5) == hw,
                   (pl - hw) ** 2, (pm - hw) ** 2))
    if mk:
        k = len(mk)
        out["vsMarket"] = {"games": k, "lockskiLogLoss": round(-sum(x[0] for x in mk) / k, 4), "marketLogLoss": round(-sum(x[1] for x in mk) / k, 4),
                           "lockskiAcc": round(sum(x[2] for x in mk) / k, 4), "marketAcc": round(sum(x[3] for x in mk) / k, 4),
                           "lockskiBrier": round(sum(x[4] for x in mk) / k, 4), "marketBrier": round(sum(x[5] for x in mk) / k, 4)}
    return out
