"""Add 0-100 'heat' scores to the model output.

Team heat  = 65% true strength (model rating, built on shots) + 35% results (goal differential per game).
             Tag: Lucky if 5v5 PDO >= 1.025 (winning on luck that usually fades),
                  Due   if PDO <= 0.975 (better than their results), else Legit.
Player heat = percentile within position group (forwards / defense) of
             45% points per game + 30% individual expected goals per 60 + 25% on-ice 5v5 xG share.
             Tag: Lucky if goals beat expected goals by 2.5+, Due if 2.5+ below, else Legit.
"""
import json, sys, os
from pathlib import Path
D = Path(os.environ.get("ENGINE_DATA", Path(__file__).parent / "data"))
import numpy as np
import pandas as pd


def heat_curve(z):
    """0-100 heat that flattens near the top: avg = 50, 1 SD ~ 64, elite (3 SD) ~ 84, ceiling 95."""
    return np.clip(np.round(50 + 45 * np.tanh(np.asarray(z, dtype=float) / 3)), 3, 95).astype(int)

MODEL = sys.argv[1]; SKATERS = D / "skaters_cur.csv"
m = json.load(open(MODEL))


def clamp(v, lo=3, hi=95):
    return max(lo, min(hi, v))


for t, v in m["teams"].items():
    strength = clamp(50 + v["rating"] * 1.8)
    gd_pg = (v["gf"] - v["ga"]) / max(v["gp"], 1)
    form = clamp(50 + gd_pg * 25)
    v["heat"] = int(round(0.65 * strength + 0.35 * form))
    v["tag"] = "Lucky" if v["pdo"] >= 1.025 else "Due" if v["pdo"] <= 0.975 else "Legit"

GOALIES = os.environ.get("GOALIES_FILE", D / "goalies_cur.csv")
s = pd.read_csv(SKATERS)
al = s[s.situation == "all"].set_index("playerId")
e5 = s[s.situation == "5on5"].set_index("playerId")
df = al[["name", "team", "position", "games_played", "icetime", "I_F_points", "I_F_goals", "I_F_xGoals",
         "I_F_primaryAssists", "I_F_secondaryAssists", "I_F_shotAttempts", "I_F_highDangerShots"]].copy()
# ---- Daily player lines from box scores (v0.6.1) ----
# Each recorded game may carry players: [{team, name, g, a, sog, toi}] (toi in minutes). A team's lines
# are used only if they add up: player goals = team goals (or one less, for a shootout winner's credited
# goal) and player shots = team shots on goal (within 1). Rates that box scores don't carry (expected goals,
# attempts, high-danger shots) scale with the player's shots at his own season-to-date ratio.
INC = Path(os.environ.get("INCREMENTS_FILE", "")) if os.environ.get("INCREMENTS_FILE") else None
lines_used = 0
if INC and INC.exists():
    games_ = json.loads(INC.read_text()).get("games", [])
    key = {(r.team, r["name"]): pid for pid, r in df.iterrows()}
    for g_ in games_:
        pl = g_.get("players") or []
        for side, team in (("a", g_["away"]), ("h", g_["home"])):
            mine = [x for x in pl if x.get("team") == team]
            if not mine: continue
            sg = sum(int(x.get("g", 0)) for x in mine); ss = sum(int(x.get("sog", 0)) for x in mine)
            if sg not in (g_[side + "g"], g_[side + "g"] - 1) or abs(ss - g_[side + "sog"]) > 1:
                continue
            for x in mine:
                pid = key.get((team, x.get("name")))
                if pid is None: continue
                r = df.loc[pid]; sog = float(x.get("sog", 0)); sh = max(float(r.I_F_shotAttempts), 1)
                df.loc[pid, ["games_played", "icetime", "I_F_points", "I_F_goals", "I_F_primaryAssists", "I_F_secondaryAssists"]] += [
                    1, float(x.get("toi", 0)) * 60, x.get("g", 0) + x.get("a", 0), x.get("g", 0), 0.6 * x.get("a", 0), 0.4 * x.get("a", 0)]
                att = sog / 0.55
                df.loc[pid, "I_F_xGoals"] += att * r.I_F_xGoals / sh
                df.loc[pid, "I_F_shotAttempts"] += att
                df.loc[pid, "I_F_highDangerShots"] += att * r.I_F_highDangerShots / sh
                lines_used += 1
m["playerLinesUsed"] = lines_used
df["toi_gp"] = df.icetime / 60 / df.games_played.where(df.games_played > 0, 1)
df = df[(df.games_played >= 3) & (df.toi_gp >= 8)].copy()
hrs = df.icetime / 3600
df["pp60"] = (df.I_F_goals + df.I_F_primaryAssists + 0.5 * df.I_F_secondaryAssists) / hrs   # primary points, 2nd assists half credit
df["ixg60"] = df.I_F_xGoals / hrs
df["icf60"] = df.I_F_shotAttempts / hrs
df["hd60"] = df.I_F_highDangerShots / hrs
e = e5.reindex(df.index)
df["onxg"] = e["onIce_xGoalsPercentage"]
df["onxga60"] = -(e["OnIce_A_xGoals"] / (e["icetime"] / 3600))          # negative so higher = better defense
df["grp"] = df.position.map(lambda p: "D" if p == "D" else "F")
WTS = {"F": {"pp60": .30, "ixg60": .20, "icf60": .10, "hd60": .10, "onxg": .15, "toi_gp": .15},
       "D": {"pp60": .15, "ixg60": .05, "icf60": .10, "onxg": .30, "onxga60": .15, "toi_gp": .25}}
K_PLAYER_GP = 12   # weight on last season's level, in games
FIT = json.load(open(D / "fitted.json"))
CARRY = FIT["player_carry"]      # fitted 2008-25: forwards 0.81, defense 0.71


def comp_z(frame, min_gp):
    d = frame[(frame.games_played >= min_gp)].copy()
    d["z"] = 0.0
    for grp, w in WTS.items():
        sub = d[d.grp == grp]
        comp = sum(wt * (sub[c] - sub[c].mean()) / sub[c].std() for c, wt in w.items())
        d.loc[sub.index, "z"] = (comp - comp.mean()) / comp.std()
    return d.z


def build(src):
    al_ = src[src.situation == "all"].set_index("playerId")
    e5_ = src[src.situation == "5on5"].set_index("playerId")
    d = al_[["name", "team", "position", "games_played", "icetime", "I_F_points", "I_F_goals", "I_F_xGoals",
             "I_F_primaryAssists", "I_F_secondaryAssists", "I_F_shotAttempts", "I_F_highDangerShots"]].copy()
    d["toi_gp"] = d.icetime / 60 / d.games_played.where(d.games_played > 0, 1)
    d = d[d.toi_gp >= 8].copy()
    h = d.icetime / 3600
    d["pp60"] = (d.I_F_goals + d.I_F_primaryAssists + 0.5 * d.I_F_secondaryAssists) / h
    d["ixg60"] = d.I_F_xGoals / h
    d["icf60"] = d.I_F_shotAttempts / h
    d["hd60"] = d.I_F_highDangerShots / h
    ee = e5_.reindex(d.index)
    d["onxg"] = ee["onIce_xGoalsPercentage"]
    d["onxga60"] = -(ee["OnIce_A_xGoals"] / (ee["icetime"] / 3600))
    d["grp"] = d.position.map(lambda p: "D" if p == "D" else "F")
    return d


prev = build(pd.read_csv(D / "skaters_2025.csv"))
z_prev = comp_z(prev, 20)
df["z"] = comp_z(df, 3)
df["zp"] = df.index.map(z_prev)
df["carry"] = df.grp.map(CARRY)
# this season's games + last season's level (carried) worth K games; rookies regress to average
# Age (v0.6): fitted on 2008-2025 player pairs, tested on 2019+ (0.8% lower error). Young players tend to
# improve next season and players past 30 decline, so last season's level is nudged by an age drift.
AGE = json.load(open(D / "age_fit.json"))
_pl = pd.read_csv(D / "players.csv")
_born = dict(zip(_pl.playerId, pd.to_datetime(_pl.birthDate, errors="coerce")))
def age_drift(pid, grp):
    b = _born.get(pid)
    if b is None or pd.isna(b): return 0.0
    age = (pd.Timestamp("2026-10-01") - b).days / 365.25
    bins, drift = AGE[grp]["bins"], AGE[grp]["drift"]
    for i in range(len(drift)):
        if bins[i] < age <= bins[i + 1]: return drift[i]
    return 0.0
df["zr"] = [(r.z * r.games_played + ((r.carry * r.zp + age_drift(pid, r.grp)) if pd.notna(r.zp) else 0.0) * K_PLAYER_GP) / (r.games_played + K_PLAYER_GP)
            for pid, r in df.iterrows()]
df["heat"] = heat_curve(df.zr)
df["diff"] = df.I_F_goals - df.I_F_xGoals
df["tag"] = df["diff"].map(lambda d: "Lucky" if d >= 2.5 else "Due" if d <= -2.5 else "Legit")
# Why a player is hot or cold: percentile (within forwards or defense) of each ingredient, this season vs last.
elig = df[df.games_played >= 5].copy()
for c in ["pp60", "ixg60", "hd60", "onxg"]:
    elig[c + "_pct"] = elig.groupby("grp")[c].rank(pct=True).mul(100).round()
elig["prev_heat"] = [int(heat_curve(z)) if pd.notna(z) else None for z in elig.zp]
top = elig.sort_values(["heat", "pp60"], ascending=False).head(40)
m["players"] = [
    {"name": r["name"], "team": r.team, "pos": r.position, "heat": int(r.heat), "tag": r.tag,
     "gp": int(r.games_played), "pts": int(r.I_F_points), "g": int(r.I_F_goals),
     "why": {"xg": round(float(r.I_F_xGoals), 1), "diff": round(float(r["diff"]), 1), "toi": round(float(r.toi_gp), 1),
             "scoringPct": int(r.pp60_pct), "chancesPct": int(r.ixg60_pct), "dangerPct": int(r.hd60_pct), "drivePct": int(r.onxg_pct) if pd.notna(r.onxg_pct) else None,
             "prevHeat": r.prev_heat, "group": "forwards" if r.grp == "F" else "defensemen"}}
    for _, r in top.iterrows()
]

g = pd.read_csv(GOALIES)
g = g[(g.situation == "all") & (g.games_played >= 3)].copy()
g["gsax"] = g.xGoals - g.goals
gm = FIT["goalie_multi"]
gh = pd.read_csv(D / "goalies_hist.csv")
gh = gh[gh.situation == "all"]
q1 = gh[gh.season == 2025].set_index("playerId"); q2 = gh[gh.season == 2024].set_index("playerId")
def pv(dfq, pid, c): return float(dfq.loc[pid, c]) if pid in dfq.index else 0.0
g["gs_w"] = [r.gsax + gm["w_prev1"] * (pv(q1, r.playerId, "xGoals") - pv(q1, r.playerId, "goals")) + gm["w_prev2"] * (pv(q2, r.playerId, "xGoals") - pv(q2, r.playerId, "goals")) for _, r in g.iterrows()]
g["xg_w"] = [r.xGoals + gm["w_prev1"] * pv(q1, r.playerId, "xGoals") + gm["w_prev2"] * pv(q2, r.playerId, "xGoals") for _, r in g.iterrows()]
g["eff"] = g.gs_w / (g.xg_w + gm["K"])
g["z"] = (g.eff - g.eff.mean()) / g.eff.std()
g["heat"] = heat_curve(g.z)
m["goalies"] = [
    {"name": r["name"], "team": r.team, "pos": "G", "heat": int(r.heat), "tag": "Legit" if r.games_played >= 6 else "Lucky" if r.gsax > 0 else "Due",
     "gp": int(r.games_played), "gsax": round(float(r.gsax), 1)}
    for _, r in g.sort_values("heat", ascending=False).iterrows()
]
json.dump(m, open(MODEL, "w"), indent=1)

ts = sorted(m["teams"].items(), key=lambda kv: -kv[1]["heat"])
print("teams hottest:", [(t, v["heat"], v["tag"]) for t, v in ts[:6]])
print("teams coldest:", [(t, v["heat"], v["tag"]) for t, v in ts[-4:]])
print("heat range:", ts[-1][1]["heat"], "-", ts[0][1]["heat"])
print("players:", [(p["name"], p["team"], p["heat"], p["tag"], p["pts"]) for p in m["players"][:10]])
print("player heat max/median:", df.heat.max(), int(df.heat.median()), "| eligible:", len(df))
print("goalies:", [(x["name"], x["team"], x["heat"], x["gsax"]) for x in m["goalies"][:5]], "max", m["goalies"][0]["heat"])
