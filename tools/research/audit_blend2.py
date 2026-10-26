import os, json, io, contextlib
os.environ["FITFILE"] = "/home/claude/lockski/fitted_clean.json"
import numpy as np, experiment as X, backtest2 as bt
with contextlib.redirect_stdout(io.StringIO()):
    from audit_v06 import match, dec
lg = lambda p: np.log(p / (1 - p)); sg = lambda z: 1 / (1 + np.exp(-z))
S25, G25 = bt.S[bt.S.season == 2025].reset_index(drop=True), X.G[X.G.season == 2025].reset_index(drop=True)
for label, kw in [("actual starters", X.PROD), ("no goalie info", dict(X.PROD, goalie_cap=0.0))]:
    M = match(X.calib(bt.predict(S25, **kw)), G25); y = M.homeWon.values; pk = M.p_mkt.values; dh, da = dec(M.home_money_line.values), dec(M.away_money_line.values); p = M.p.values
    for w in (0.5, 0.35):
        pb = sg(w * lg(p) + (1 - w) * lg(pk))
        for name, bh, ba in [("lockski +4 only", p - 1/dh >= .04, (1-p) - 1/da >= .04), (f"lockski +4 AND blend({w}) beats payout", (p - 1/dh >= .04) & (pb > 1/dh), ((1-p) - 1/da >= .04) & ((1-pb) > 1/da)), (f"lockski +4 AND gap < 6 pts", (p - 1/dh >= .04) & (p - pk < .06), ((1-p) - 1/da >= .04) & (pk - p < .06))]:
            pnl = np.concatenate([np.where(y[bh] == 1, dh[bh] - 1, -1), np.where(y[ba] == 0, da[ba] - 1, -1)])
            rng = np.random.default_rng(7); bs = [rng.choice(pnl, len(pnl)).mean() for _ in range(2000)]
            print(f"{label:16} | {name:38} | bets {len(pnl):4} | roi {pnl.mean():+.3f} | 95% range {np.percentile(bs,2.5):+.3f} to {np.percentile(bs,97.5):+.3f}")
