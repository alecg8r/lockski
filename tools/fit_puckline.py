"""Puck line (±1.5) calibration: P(team wins by 2+) from lockski's own pregame win chance.
Empirical, so empty-net goals and OT/shootout (always 1-goal wins) are built in.
Fit 2022-23 + 2023-24, test 2024-25 + 2025-26 (same walk-forward split as the main model)."""
import json, os
os.environ["FITFILE"] = "/home/claude/lockski/fitted_clean.json"
import numpy as np, pandas as pd
import experiment as X, backtest2 as bt
from scipy.optimize import minimize
G = X.G
rows = []
for s in sorted(set(bt.S.season)):
    Ss, Gs = bt.S[bt.S.season == s].reset_index(drop=True), G[G.season == s].reset_index(drop=True)
    p = X.calib(bt.predict(Ss, **X.PROD))
    hw = Gs.homeWon.values == 1
    d = (Gs.h_goals - Gs.a_goals).values.astype(float)
    ok = np.sign(d) == np.where(hw, 1, -1)
    m = np.where(ok, np.abs(d), 1.0)                       # shootout / mismatch -> 1-goal win
    for side_p, won in ((p, hw), (1 - p, ~hw)):
        rows.append(pd.DataFrame({"season": s, "p": side_p, "cover": (won & (m >= 2)).astype(int)}))
D = pd.concat(rows, ignore_index=True)
print("seasons", sorted(D.season.unique()))
lg = lambda p: np.log(p / (1 - p)); sg = lambda z: 1 / (1 + np.exp(-z))
tr, te = D[D.season.isin([2022, 2023])], D[D.season.isin([2024, 2025])]
def nll(w, df): q = np.clip(sg(w[0] + w[1] * lg(df.p.values)), 1e-6, 1 - 1e-6); y = df.cover.values; return -np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))
w = minimize(nll, [0, 1], args=(tr,)).x
share = tr.cover.sum() / (tr.p.values).sum()                  # naive: fixed share of wins are by 2+
q = sg(w[0] + w[1] * lg(te.p.values)); y = te.cover.values
ll = lambda q: float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))
out = {"a": float(w[0]), "b": float(w[1]), "fit": "2022-24", "test": "2024-26", "nTest": int(len(te)),
       "testLogLoss": round(ll(q), 4), "naiveLogLoss": round(ll(np.clip(te.p.values * share, 1e-6, 1 - 1e-6)), 4),
       "constLogLoss": round(ll(np.full_like(q, y.mean())), 4), "coverRateTest": round(float(y.mean()), 4)}
bands = []
for lo, hi in [(.3, .45), (.45, .55), (.55, .6), (.6, .65), (.65, .7), (.7, .9)]:
    k = (te.p.values >= lo) & (te.p.values < hi)
    if k.sum(): bands.append({"winChance": f"{int(lo*100)}-{int(hi*100)}%", "n": int(k.sum()), "predictedCover": round(float(q[k].mean()), 3), "actualCover": round(float(y[k].mean()), 3)})
out["bands"] = bands
json.dump(out, open("/home/claude/lockski-full/engine/data/puckline.json", "w"), indent=1)
print(json.dumps(out, indent=1))
