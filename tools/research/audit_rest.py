"""Test rest/travel on past games. Replay chances (no rest/travel in them) + schedule from the NHL feed.
Fit adjustments on 2022-24, test on 2024-26. Compare: none, the live settings (b2b 3.5%, travel 1.5% of goals), refit."""
import os, json, io, contextlib, math
os.environ["FITFILE"] = "/home/claude/lockski/fitted_clean.json"
import numpy as np, pandas as pd, experiment as X, backtest2 as bt
from scipy.optimize import minimize
from datetime import date
TZ = {"ANA": -3, "BOS": 0, "BUF": 0, "CGY": -2, "CAR": 0, "CHI": -1, "COL": -2, "CBJ": 0, "DAL": -1, "DET": 0, "EDM": -2, "FLA": 0, "LAK": -3, "MIN": -1, "MTL": 0, "NSH": -1, "NJD": 0, "NYI": 0, "NYR": 0, "OTT": 0, "PHI": 0, "PIT": 0, "SJS": -3, "SEA": -3, "STL": -1, "TBL": 0, "TOR": 0, "UTA": -2, "ARI": -2, "VAN": -3, "VGK": -3, "WSH": 0, "WPG": -1}
D = json.load(open("research_dates.json"))
lg = lambda p: np.log(p / (1 - p)); sg = lambda z: 1 / (1 + np.exp(-z))
rows = []
for s in (2022, 2023, 2024, 2025):
    Ss, Gs = bt.S[bt.S.season == s].reset_index(drop=True), X.G[X.G.season == s].reset_index(drop=True)
    p = X.calib(bt.predict(Ss, **X.PROD)); dd = D[str(s)]
    df = pd.DataFrame({"season": s, "gid": Gs.game_id.astype(int).astype(str), "home": Gs.home, "away": Gs.away, "y": Gs.homeWon.values, "p": p})
    df["date"] = df.gid.map(lambda k: dd.get(k, [None])[0])
    df = df.dropna(subset=["date"]).sort_values(["date", "gid"])
    last = {}; f = []
    for r in df.itertuples():
        def ctx(t):
            pr = last.get(t)
            if not pr: return 0, 0, 3
            gap = (date.fromisoformat(r.date) - date.fromisoformat(pr[0])).days
            return int(gap == 1), int(abs(TZ.get(pr[1], 0) - TZ.get(r.home, 0)) >= 2), min(gap - 1, 3)
        hb, ht, hr = ctx(r.home); ab, at, ar = ctx(r.away)
        f.append((hb, ab, at, hr - ar)); last[r.home] = (r.date, r.home); last[r.away] = (r.date, r.home)
    df[["hb2b", "ab2b", "atrav", "restdiff"]] = f
    rows.append(df)
A = pd.concat(rows, ignore_index=True)
print("games matched to dates:", len(A), "| home b2b share", round(A.hb2b.mean(), 3), "| away b2b", round(A.ab2b.mean(), 3), "| away travel", round(A.atrav.mean(), 3))
def winp(lh, la):   # regulation Poisson + OT split, like the live model
    ph = [math.exp(-lh) * lh**k / math.factorial(k) for k in range(15)]; pa = [math.exp(-la) * la**k / math.factorial(k) for k in range(15)]
    w = sum(ph[i] * pa[j] for i in range(15) for j in range(15) if i > j); t = sum(ph[i] * pa[i] for i in range(15)); return w + t * 0.51
base = winp(3.1, 2.95)
live_b2b = lg(np.array(winp(3.1 * 1.035, 2.95 * .965))) - lg(np.array(base))   # home edge when the AWAY team is on a b2b
live_trav = lg(np.array(winp(3.1, 2.95 * .985))) - lg(np.array(base))
print(f"live settings in win-chance terms: b2b swing {live_b2b:.3f} logit (~{(sg(lg(.5)+live_b2b)-.5)*100:.1f} pts), away travel {live_trav:.3f} (~{(sg(lg(.5)+live_trav)-.5)*100:.1f} pts)")
tr, te = A[A.season.isin([2022, 2023])], A[A.season.isin([2024, 2025])]
X_ = lambda d: np.c_[d.ab2b - d.hb2b, d.atrav, d.restdiff]
def nll(b, d): q = np.clip(sg(lg(d.p.values) + X_(d) @ b), 1e-6, 1 - 1e-6); y = d.y.values; return -np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))
fit = minimize(nll, [0, 0, 0], args=(tr,)).x
live = np.array([live_b2b, live_trav, 0.0])
rng = np.random.default_rng(3); n = len(te); idx = rng.integers(0, n, (2000, n))
def ll_vec(b): q = np.clip(sg(lg(te.p.values) + X_(te) @ b), 1e-6, 1 - 1e-6); y = te.y.values; return -(y * np.log(q) + (1 - y) * np.log(1 - q))
L0, L1, L2 = ll_vec(np.zeros(3)), ll_vec(live), ll_vec(fit)
for name, L in [("no rest/travel", L0), ("live settings", L1), ("refit on 2022-24", L2)]:
    print(f"{name:18} test log loss {L.mean():.5f} | better than none in {(L0[idx].mean(1) - L[idx].mean(1) > 0).mean()*100:.1f}% of resamples")
print("refit coefficients (logit): b2b swing %.3f, away travel %.3f, rest-day diff %.3f" % tuple(fit))
for nm, k in [("away on b2b", (te.ab2b == 1) & (te.hb2b == 0)), ("home on b2b", (te.hb2b == 1) & (te.ab2b == 0))]:
    d = te[k]; print(f"{nm}: {len(d)} games, home won {d.y.mean()*100:.1f}% vs replay predicted {d.p.mean()*100:.1f}%")
print("--- head to head vs the live settings (2024-26 test) ---")
for name, b in [("b2b refit only (travel 0, no rest term)", np.array([fit[0], 0.0, 0.0])), ("full refit", fit)]:
    L = ll_vec(b); print(f"{name:40} log loss {L.mean():.5f} | better than live settings in {(L1[idx].mean(1) - L[idx].mean(1) > 0).mean()*100:.1f}% of resamples")
fit2 = minimize(lambda b: nll(np.array([b[0], 0, 0]), tr), [0]).x[0]; print("b2b-only fit:", round(fit2, 3))
L = ll_vec(np.array([fit2, 0, 0])); print(f"b2b-only fit {fit2:.3f}: log loss {L.mean():.5f} | better than live in {(L1[idx].mean(1) - L[idx].mean(1) > 0).mean()*100:.1f}% | per-season:", [round(float(ll_vec(np.array([fit2,0,0]))[(te.season==s).values].mean() - L1[(te.season==s).values].mean()),5) for s in (2024, 2025)])
