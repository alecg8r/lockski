import json, numpy as np, pandas as pd
cols = ["season", "game_id", "isPlayoffGame", "teamCode", "homeTeamCode", "awayTeamCode", "event", "goal", "xCordAdjusted", "yCordAdjusted", "shotType", "shotOnEmptyNet",
        "homeSkatersOnIce", "awaySkatersOnIce", "timeSinceLastEvent", "lastEventCategory", "lastEventTeam", "team", "lastEventxCord_adjusted", "xGoal", "homeEmptyNet", "awayEmptyNet"]
S = pd.read_csv("/home/claude/shots/shots_2021-2025.csv", usecols=cols)
S = S[(S.isPlayoffGame == 0) & S.event.isin(["SHOT", "MISS", "GOAL"]) & (S.homeSkatersOnIce == 5) & (S.awaySkatersOnIce == 5) & (S.shotOnEmptyNet == 0) & (S.homeEmptyNet == 0) & (S.awayEmptyNet == 0) & (S.season >= 2024)].copy()
M = json.load(open("xg/xg_model.json"))
S["x"] = S.xCordAdjusted.clip(-99, 99); S["y"] = S.yCordAdjusted
S["dist"] = np.sqrt((89 - S.x) ** 2 + S.y ** 2).clip(1, 200); S["ang"] = np.degrees(np.arctan2(S.y.abs(), (89 - S.x).clip(lower=0.5)))
S["shotType"] = S.shotType.fillna("WRIST").where(S.shotType.isin(M["types"]), "WRIST")
same = S.lastEventTeam == S.team
S["reb"] = (S.lastEventCategory.isin(["SHOT", "MISS", "GOAL", "BLOCK"]) & same & (S.timeSinceLastEvent <= 3)).astype(int)
S["rush"] = ((S.timeSinceLastEvent <= 4) & (S.lastEventxCord_adjusted < 25) & ~S.lastEventCategory.isin(["SHOT", "MISS", "GOAL", "BLOCK"])).astype(int)
F = {"ldist": np.log(S.dist), "dist": S.dist / 10, "ang": S.ang / 10, "ang2": (S.ang / 10) ** 2, "da": S.dist / 10 * S.ang / 10, "reb": S.reb, "rebang": S.reb * S.ang / 10, "rush": S.rush}
for t in M["types"][1:]: F["t_" + t] = (S.shotType == t).astype(int)
z = M["intercept"] + sum(c * F[f] for f, c in zip(M["features"], M["coef"]))
S["ours"] = 1 / (1 + np.exp(-z))
S["opp"] = np.where(S.teamCode == S.homeTeamCode, S.awayTeamCode, S.homeTeamCode)
f = S.groupby(["season", "game_id", "teamCode"]).agg(ours=("ours", "sum"), mp=("xGoal", "sum"), g=("goal", "sum")).reset_index()
a = S.groupby(["season", "game_id", "opp"]).agg(oursA=("ours", "sum"), mpA=("xGoal", "sum"), gA=("goal", "sum")).reset_index().rename(columns={"opp": "teamCode"})
G = f.merge(a, on=["season", "game_id", "teamCode"]).sort_values(["season", "game_id"])
ts = G.groupby(["season", "teamCode"]).agg(ours=("ours", "sum"), mp=("mp", "sum")).reset_index()
print(f"team-season 5v5 xG corr with moneypuck {np.corrcoef(ts.ours, ts.mp)[0,1]:.3f}")
# predictive test: first 41 games' xG share -> goal share in the last 41
rows = []
for (s, t), d in G.groupby(["season", "teamCode"]):
    d = d.sort_values("game_id"); h = len(d) // 2; A, B = d.iloc[:h], d.iloc[h:]
    rows.append((A.ours.sum() / (A.ours.sum() + A.oursA.sum()), A.mp.sum() / (A.mp.sum() + A.mpA.sum()), A.g.sum() / (A.g.sum() + A.gA.sum()), B.g.sum() / (B.g.sum() + B.gA.sum())))
R = np.array(rows)
print(f"predicting 2nd-half 5v5 goal share from 1st half (n={len(R)} team-seasons): our xG r={np.corrcoef(R[:,0], R[:,3])[0,1]:.3f} | moneypuck xG r={np.corrcoef(R[:,1], R[:,3])[0,1]:.3f} | goals r={np.corrcoef(R[:,2], R[:,3])[0,1]:.3f}")
