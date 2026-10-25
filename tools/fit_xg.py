"""Fit lockski's expected-goals model on MoneyPuck shot data (2021-22..2023-24), test on 2024-25 and 2025-26.
Only features we can rebuild from the NHL's public play-by-play: distance, angle, shot type, rebound, rush.
5-on-5, unblocked shots (shots on goal + misses), no empty nets. Output: xg/xg_model.json (coefficients)."""
import json, numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
cols = ["season", "game_id", "isPlayoffGame", "team", "event", "goal", "xCordAdjusted", "yCordAdjusted", "shotType", "shotOnEmptyNet",
        "homeSkatersOnIce", "awaySkatersOnIce", "timeSinceLastEvent", "lastEventCategory", "lastEventTeam", "lastEventxCord_adjusted", "xGoal", "isHomeTeam", "homeEmptyNet", "awayEmptyNet"]
S = pd.read_csv("/home/claude/shots/shots_2021-2025.csv", usecols=cols)
S = S[(S.isPlayoffGame == 0) & S.event.isin(["SHOT", "MISS", "GOAL"]) & (S.homeSkatersOnIce == 5) & (S.awaySkatersOnIce == 5)
      & (S.shotOnEmptyNet == 0) & (S.homeEmptyNet == 0) & (S.awayEmptyNet == 0)].copy()
S["x"] = S.xCordAdjusted.clip(-99, 99); S["y"] = S.yCordAdjusted
S["dist"] = np.sqrt((89 - S.x) ** 2 + S.y ** 2).clip(1, 200)
S["ang"] = np.degrees(np.arctan2(S.y.abs(), (89 - S.x).clip(lower=0.5)))
TYPES = ["WRIST", "SNAP", "SLAP", "BACK", "TIP", "DEFL", "WRAP"]
S["shotType"] = S.shotType.fillna("WRIST").where(S.shotType.isin(TYPES), "WRIST")
same = S.lastEventTeam == S.team
S["reb"] = (S.lastEventCategory.isin(["SHOT", "MISS", "GOAL", "BLOCK"]) & same & (S.timeSinceLastEvent <= 3)).astype(int)
S["rush"] = ((S.timeSinceLastEvent <= 4) & (S.lastEventxCord_adjusted < 25) & ~S.lastEventCategory.isin(["SHOT", "MISS", "GOAL", "BLOCK"])).astype(int)

def feats(d):
    X = pd.DataFrame({"ldist": np.log(d.dist), "dist": d.dist / 10, "ang": d.ang / 10, "ang2": (d.ang / 10) ** 2, "da": d.dist / 10 * d.ang / 10,
                      "reb": d.reb, "rebang": d.reb * d.ang / 10, "rush": d.rush})
    for t in TYPES[1:]:
        X["t_" + t] = (d.shotType == t).astype(int)
    return X
tr, te = S[S.season <= 2023], S[S.season >= 2024]
Xtr, Xte = feats(tr), feats(te)
m = LogisticRegression(C=10, max_iter=2000).fit(Xtr, tr.goal)
p = m.predict_proba(Xte)[:, 1]
print(f"shots train {len(tr):,} test {len(te):,}")
print(f"test log loss ours {log_loss(te.goal, p):.4f} | moneypuck {log_loss(te.goal, te.xGoal.clip(1e-4, 1-1e-4)):.4f} | constant {log_loss(te.goal, np.full(len(te), tr.goal.mean())):.4f}")
print(f"test AUC ours {roc_auc_score(te.goal, p):.4f} | moneypuck {roc_auc_score(te.goal, te.xGoal):.4f}")
te = te.assign(p=p)
tg = te.groupby(["season", "game_id", "team"]).agg(ours=("p", "sum"), mp=("xGoal", "sum"), goals=("goal", "sum"), n=("goal", "size"))
print(f"team-game 5v5 xG: corr with moneypuck {np.corrcoef(tg.ours, tg.mp)[0,1]:.3f} | mean ours {tg.ours.mean():.3f} mp {tg.mp.mean():.3f} goals {tg.goals.mean():.3f}")
ts = te.groupby(["season", "team"]).agg(ours=("p", "sum"), mp=("xGoal", "sum"))
print(f"team-season 5v5 xG corr with moneypuck {np.corrcoef(ts.ours, ts.mp)[0,1]:.3f}")
scale = float(tg.mp.mean() / tg.ours.mean())
json.dump({"features": list(Xtr.columns), "coef": m.coef_[0].tolist(), "intercept": float(m.intercept_[0]), "types": TYPES, "mpScale": round(scale, 4),
           "trainedOn": "MoneyPuck 5v5 unblocked shots 2021-22..2023-24", "test": "2024-25 and 2025-26"}, open("xg/xg_model.json", "w"), indent=1)
print("scale to moneypuck level:", round(scale, 4))
