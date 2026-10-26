"""Checks every published data file before it is committed. Exit 1 on any failure (the workflow then restores the
last good copy with `git checkout -- data/`), so a bad update can never replace good data with blank or partial files.

Usage: python3 tools/validate_data.py [data_dir]   (default: repo data/)
"""
import json, math, sys
from datetime import datetime, timezone
from pathlib import Path

D = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data"
TEAMS = {"ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI",
         "NYR", "OTT", "PHI", "PIT", "SJS", "SEA", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG"}
errors, warns = [], []


def load(name):
    p = D / name
    if not p.exists():
        errors.append(f"{name}: missing"); return None
    try:
        return json.loads(p.read_text())
    except Exception as e:
        errors.append(f"{name}: not valid JSON ({e})"); return None


def fin(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


weeks, model, ledger, meta = load("weeks.json"), load("model.json"), load("ledger.json"), load("meta.json")
inc = load("inputs/increments.json"); gd = load("inputs/gameday.json")

# ---- schedule ----
ids = set()
if isinstance(weeks, dict):
    for wid, wk in weeks.items():
        for g in wk.get("games", []):
            for k in ("id", "date", "startUTC", "away", "home"):
                if k not in g: errors.append(f"weeks: game missing {k}: {g}")
            if g.get("away") not in TEAMS or g.get("home") not in TEAMS: errors.append(f"weeks: unknown team in {g.get('id')}")
            if g.get("away") == g.get("home"): errors.append(f"weeks: same team both sides {g.get('id')}")
            try: datetime.fromisoformat(g["startUTC"].replace("Z", "+00:00"))
            except Exception: errors.append(f"weeks: bad startUTC {g.get('id')}")
            if g.get("id") in ids: errors.append(f"weeks: duplicate game id {g.get('id')}")
            ids.add(g.get("id"))
    if not ids: errors.append("weeks: no games at all")
else:
    errors.append("weeks: not an object")

# ---- model ----
if isinstance(model, dict):
    t = model.get("teams") or {}
    if set(t) != TEAMS: errors.append(f"model: teams != 32 ({len(t)})")
    gs = model.get("games") or {}
    if not gs: errors.append("model: no games")
    tot = 0
    for gid, g in gs.items():
        ph, pa = g.get("pHome"), g.get("pAway")
        if not (fin(ph) and fin(pa) and 0 < ph < 1 and 0 < pa < 1 and abs(ph + pa - 1) < 1e-3): errors.append(f"model: bad probabilities {gid}")
        elif not (0.12 <= ph <= 0.90): errors.append(f"model: implausible pHome {ph} {gid}")
        xh, xa = g.get("xgHome"), g.get("xgAway")
        if not (fin(xh) and fin(xa) and xh >= 0 and xa >= 0): errors.append(f"model: bad xG {gid}")
        else: tot += xh + xa
        if g.get("home") == g.get("away"): errors.append(f"model: same team both sides {gid}")
        pl = g.get("pl")
        if pl is not None and not all(fin(pl.get(k)) and 0 < pl[k] < 1 for k in ("home", "away")): errors.append(f"model: bad puck line {gid}")
        if pl is not None and fin(pl.get("home")) and fin(pl.get("away")) and pl["home"] + pl["away"] >= 1: errors.append(f"model: puck lines add past 1 {gid}")
        mk = g.get("market")
        if mk and not (fin(mk.get("pHome")) and 0 < mk["pHome"] < 1): errors.append(f"model: bad market price {gid}")
    if gs and not (5.2 <= tot / len(gs) <= 7.0): errors.append(f"model: scoring level {tot / len(gs):.2f} out of range")
    if not model.get("generatedAt"): errors.append("model: no generatedAt")
else:
    errors.append("model: not an object")

# ---- ledger ----
if isinstance(ledger, dict):
    for gid, d in ledger.items():
        if d.get("id") != gid: errors.append(f"ledger: id mismatch {gid}")
        o = d.get("official")
        if o and not (fin(o.get("pHome")) and 0 < o["pHome"] < 1): errors.append(f"ledger: bad official prob {gid}")
        snaps = d.get("snapshots") or []
        try:
            start = datetime.fromisoformat(d["startUTC"].replace("Z", "+00:00"))
            for s in snaps:
                if datetime.fromisoformat(s["at"].replace("Z", "+00:00")) >= start: errors.append(f"ledger: snapshot after puck drop {gid}")
        except Exception: errors.append(f"ledger: bad timestamps {gid}")
        r = d.get("result")
        if r and not r.get("void"):
            if r.get("winner") not in (d.get("home"), d.get("away")): errors.append(f"ledger: winner not in game {gid}")
            if r.get("ag") == r.get("hg"): errors.append(f"ledger: tied final {gid}")
else:
    errors.append("ledger: not an object")

# ---- results / inputs ----
if isinstance(inc, dict):
    seen = set()
    for g in inc.get("games", []):
        k = (g.get("date"), g.get("away"), g.get("home"))
        if k in seen: errors.append(f"increments: duplicate {k}")
        seen.add(k)
        if g.get("ag") == g.get("hg"): errors.append(f"increments: tie {k}")
        if g.get("away") not in TEAMS or g.get("home") not in TEAMS: errors.append(f"increments: unknown team {k}")
if isinstance(gd, dict):
    for d, rows in (gd.get("starters") or {}).items():
        for r in rows:
            if len(r) != 6 or r[0] not in TEAMS or r[3] not in TEAMS or r[2] not in ("Confirmed", "Unconfirmed") or r[5] not in ("Confirmed", "Unconfirmed"):
                errors.append(f"gameday: bad starter row {r}")
    for l in (gd.get("odds") or {}).get("lines", []):
        if len(l) < 4 or l[0] not in TEAMS or l[2] not in TEAMS or not (fin(l[1]) and fin(l[3])): errors.append(f"gameday: bad odds row {l}")
    for i in gd.get("injuries") or []:
        if len(i) != 4: errors.append(f"gameday: bad injury row {i}")

# ---- meta ----
if isinstance(meta, dict):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(meta["lastSync"].replace("Z", "+00:00"))).total_seconds() / 3600
        if age > 12: warns.append(f"meta: lastSync is {age:.1f}h old")
    except Exception: errors.append("meta: bad lastSync")

for w in warns: print("warn:", w)
for e in errors: print("ERROR:", e)
print("validate:", "OK" if not errors else f"{len(errors)} error(s)")
sys.exit(1 if errors else 0)
