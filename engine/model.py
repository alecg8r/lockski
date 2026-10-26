"""lockski.ai NHL team model v0.3 (preliminary, not yet backtested).

Expected goals for each side of a game are built from game STATES, not one blended rate:
  5-on-5   : blend of xG (50%), Corsi (30%), Fenwick (20%), score/venue adjusted
  Power play / penalty kill : xG rates at 5-on-4 and 4-on-5, with each team's
             penalty draw/take rates deciding how much special-teams time it gets
  Other    : 4v4, 3v3 OT, empty net etc. at league-average rates
Every rate is regressed toward last season (2025-26), which is itself pulled
partly to league average.

Game-day adjustments:
  goalies   : projected/confirmed starter from Daily Faceoff; else ice-time blend of
              healthy goalies. Effect = regressed GSAx per expected goal.
  injuries  : each injured skater's value (regressed 5v5 on-ice xG impact relative to
              teammates) x share of team games he has played (what's already baked in)
  rest      : second night of back-to-back penalty
  travel    : road team coming off a game 2+ time zones away
  home ice  : calibrated so evenly matched teams split ~53.5 / 46.5
PDO is reported as a luck flag, never used (the model ignores actual goals).
"""
import json, math, re, sys
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

import os
D = Path(os.environ.get("ENGINE_DATA", Path(__file__).parent / "data"))
OUT, WEEKS, GAMEDAY = (Path(a) for a in sys.argv[1:4])
INCREMENTS = Path(sys.argv[4]) if len(sys.argv) > 4 else None

# ---------------- Assumptions (fitted by backtest later) ----------------
# In-season 5v5 blend. History (2008-25) says Corsi predicts best, Fenwick adds ~nothing beyond Corsi,
# and actual goals add real signal over a full season. Goals are noisier early, so they get less here.
W_XG, W_CORSI, W_FENWICK, W_GOALS = 0.40, 0.30, 0.10, 0.20   # tuned on 2022-24 games, tested on 2024-26
FIT = json.loads((D / "fitted.json").read_text())
PRIOR_COEF = FIT["team_nnls"]   # fitted year-ahead weights on last season (1) and two seasons ago (2)
K_GAMES = 8             # this-season weight = GP/(GP+20)
PRIOR_CARRY = 0.65      # share of last season kept (5v5)
ST_PRIOR_CARRY = 0.70   # special teams carry less year to year
K_ST_MIN = 150          # PP/PK regression in special-teams minutes
K_GOALIE_XGA = FIT["goalie_multi"]["K"]   # fitted: 400 expected goals faced
G_W1, G_W2 = FIT["goalie_multi"]["w_prev1"], FIT["goalie_multi"]["w_prev2"]
GOALIE_CAP = 0.10
TUNED = json.loads((D / "tuned.json").read_text())
STRETCH = TUNED["stretch"]   # calibration from backtest: model was too modest on strong picks
UNCONF_STARTER_W = 0.80 # weight on Daily Faceoff "unconfirmed" starter (rest = healthy blend)
HOME_WIN_TARGET = 0.535
B2B_PENALTY = 0.062   # tested Oct 26 2026 on 5,248 dated games: fit 2022-24, better on 2024-26 in 95% of resamples (was 0.035)
TRAVEL_PENALTY = 0.0   # road team, previous game 2+ time zones away: no measurable effect in the same test (was 0.015)
K_PLAYER_MIN = 300      # player 5v5 minutes for regression of on-ice impact
PLAYER_SHARE = 0.6      # on-ice results are shared with linemates
PLAYER_CAP = 0.15       # max goals/game impact per player
REPLACEMENT = -0.03     # call-up replacing an injured player is below an average teammate
TEAM_INJ_CAP = 0.35     # max goals/game total injury adjustment per team
OT_HOME_WIN = 0.51

TZ = {"ANA": -3, "BOS": 0, "BUF": 0, "CGY": -2, "CAR": 0, "CHI": -1, "COL": -2, "CBJ": 0, "DAL": -1,
      "DET": 0, "EDM": -2, "FLA": 0, "LAK": -3, "MIN": -1, "MTL": 0, "NSH": -1, "NJD": 0, "NYI": 0,
      "NYR": 0, "OTT": 0, "PHI": 0, "PIT": 0, "SJS": -3, "SEA": -3, "STL": -1, "TBL": 0, "TOR": 0,
      "UTA": -2, "VAN": -3, "VGK": -3, "WSH": 0, "WPG": -1}
CITY = {"Anaheim": "ANA", "Boston": "BOS", "Buffalo": "BUF", "Calgary": "CGY", "Carolina": "CAR",
        "Chicago": "CHI", "Colorado": "COL", "Columbus": "CBJ", "Dallas": "DAL", "Detroit": "DET",
        "Edmonton": "EDM", "Florida": "FLA", "Los Angeles": "LAK", "Minnesota": "MIN", "Montreal": "MTL",
        "Nashville": "NSH", "New Jersey": "NJD", "N.Y. Islanders": "NYI", "N.Y. Rangers": "NYR",
        "Ottawa": "OTT", "Philadelphia": "PHI", "Pittsburgh": "PIT", "San Jose": "SJS", "Seattle": "SEA",
        "St. Louis": "STL", "Tampa Bay": "TBL", "Toronto": "TOR", "Utah": "UTA", "Vancouver": "VAN",
        "Vegas": "VGK", "Washington": "WSH", "Winnipeg": "WPG"}
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

warnings = []

import difflib, unicodedata
def _nm(x):
    x = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower().replace("-", " ").replace(".", "")
    return " ".join(x.split())
def match_name(name, candidates):
    """Same person across sources that spell names differently (accents, 'Merilainen' vs 'Merilinen', Mitch/Mitchell).
    Exact first, then accent-free, then a close spelling (ratio >= 0.85) with the same first initial. Only ever
    called with candidates from the same team, so two different players can't be confused."""
    cands = list(candidates)
    if name in cands: return name
    n = _nm(name); by = {_nm(c): c for c in cands}
    if n in by: return by[n]
    best, score = None, 0.0
    for c in cands:
        cn = _nm(c)
        if not cn or not n or cn[0] != n[0]: continue
        r = difflib.SequenceMatcher(None, n, cn).ratio()
        if r > score: best, score = c, r
    return best if score >= 0.85 else None

# ---------------- Load ----------------
teams = pd.read_csv(D / "teams_cur.csv")
goalies = pd.read_csv(D / "goalies_cur.csv")
skaters = pd.read_csv(D / "skaters_cur.csv")
prev = pd.read_csv(D / "teams_2025.csv")
gd = json.loads(GAMEDAY.read_text())

# ---------------- New games since the MoneyPuck snapshot (from box scores) ----------------
# Results, shots on goal, power plays and goalie saves arrive daily. Shot-quality stats (xG, Corsi,
# Fenwick) for these games are estimated from shots on goal at each team's own snapshot rates, and are
# replaced with exact values whenever a fresh MoneyPuck file is dropped in.
inc_games = json.loads(INCREMENTS.read_text()).get("games", []) if INCREMENTS and INCREMENTS.exists() else []
corsi_used = [0]
pbp_used = [0]
# play-by-play shot quality (data/xg_games.json from the web app repo), merged in by game
XGF = Path(os.environ["XG_FILE"]) if os.environ.get("XG_FILE") else None
if XGF and XGF.exists():
    _xg = json.loads(XGF.read_text())
    for gm_ in inc_games:
        r = _xg.get(f'{gm_["date"]}-{gm_["away"]}-{gm_["home"]}')
        if r:
            for k in ("axg5", "hxg5", "aff5", "hff5", "acf5p", "hcf5p", "asog5", "hsog5"):
                gm_[k] = r[k]
def _apply_increments(teams, goalies, games):
    T = teams.set_index(["team", "situation"])
    for gm_ in games:
        for side, opp in [("a", "h"), ("h", "a")]:
            t = gm_["away"] if side == "a" else gm_["home"]
            gf, ga = gm_[side + "g"], gm_[opp + "g"]
            sog, osog = gm_[side + "sog"], gm_[opp + "sog"]
            ppg, oppg = gm_.get(side + "ppg", 0), gm_.get(opp + "ppg", 0)
            ppo, oppo = gm_.get(side + "ppo", 0), gm_.get(opp + "ppo", 0)
            if (t, "all") not in T.index:
                continue
            al_, e5_ = T.loc[(t, "all")].copy(), T.loc[(t, "5on5")].copy()
            share = e5_.shotsOnGoalFor / max(al_.shotsOnGoalFor, 1); oshare = e5_.shotsOnGoalAgainst / max(al_.shotsOnGoalAgainst, 1)
            s5, os5 = sog * share, osog * oshare
            # Exact 5-on-5 shot quality from the NHL play-by-play (tools/xg_nightly.py) when this game has it
            pb = all(gm_.get(k) is not None for k in (side + "xg5", opp + "xg5", side + "ff5", opp + "ff5", side + "cf5p", opp + "cf5p", side + "sog5", opp + "sog5"))
            if pb:
                s5, os5 = float(gm_[side + "sog5"]), float(gm_[opp + "sog5"])
            for sit in ["all", "5on5", "5on4", "4on5", "other"]:
                if (t, sit) in T.index:
                    T.loc[(t, sit), "games_played"] += 1
            T.loc[(t, "all"), ["goalsFor", "goalsAgainst", "shotsOnGoalFor", "shotsOnGoalAgainst", "penaltiesAgainst", "penaltiesFor", "iceTime"]] += [gf, ga, sog, osog, ppo, oppo, 3600]
            for col, base, n in [("scoreVenueAdjustedxGoalsFor", "shotsOnGoalFor", s5), ("scoreAdjustedShotsAttemptsFor", "shotsOnGoalFor", s5),
                                 ("scoreAdjustedUnblockedShotAttemptsFor", "shotsOnGoalFor", s5),
                                 ("scoreVenueAdjustedxGoalsAgainst", "shotsOnGoalAgainst", os5), ("scoreAdjustedShotsAttemptsAgainst", "shotsOnGoalAgainst", os5),
                                 ("scoreAdjustedUnblockedShotAttemptsAgainst", "shotsOnGoalAgainst", os5)]:
                T.loc[(t, "5on5"), col] += n * e5_[col] / max(e5_[base], 1)
            if pb:   # replace the estimates just added with the exact play-by-play totals
                for col, exact, base, n in [("scoreVenueAdjustedxGoalsFor", gm_[side + "xg5"], "shotsOnGoalFor", s5),
                                            ("scoreAdjustedUnblockedShotAttemptsFor", gm_[side + "ff5"], "shotsOnGoalFor", s5),
                                            ("scoreAdjustedShotsAttemptsFor", gm_[side + "cf5p"], "shotsOnGoalFor", s5),
                                            ("scoreVenueAdjustedxGoalsAgainst", gm_[opp + "xg5"], "shotsOnGoalAgainst", os5),
                                            ("scoreAdjustedUnblockedShotAttemptsAgainst", gm_[opp + "ff5"], "shotsOnGoalAgainst", os5),
                                            ("scoreAdjustedShotsAttemptsAgainst", gm_[opp + "cf5p"], "shotsOnGoalAgainst", os5)]:
                    T.loc[(t, "5on5"), col] += float(exact) - n * e5_[col] / max(e5_[base], 1)
                pbp_used[0] += 1
            # Exact 5-on-5 Corsi from the box score when available and self-consistent
            # (one team's attempts for must match the other's attempts against, within 2).
            cf, ca = (None, None) if pb else (gm_.get(side + "cf5"), gm_.get(side + "ca5"))
            ocf, oca = gm_.get(opp + "cf5"), gm_.get(opp + "ca5")
            if None not in (cf, ca, ocf, oca) and abs(cf - oca) <= 2 and abs(ca - ocf) <= 2 and 20 <= cf <= 120 and 20 <= ca <= 120:
                est_cf = s5 * e5_["scoreAdjustedShotsAttemptsFor"] / max(e5_["shotsOnGoalFor"], 1)
                est_ca = os5 * e5_["scoreAdjustedShotsAttemptsAgainst"] / max(e5_["shotsOnGoalAgainst"], 1)
                T.loc[(t, "5on5"), "scoreAdjustedShotsAttemptsFor"] += cf - est_cf
                T.loc[(t, "5on5"), "scoreAdjustedShotsAttemptsAgainst"] += ca - est_ca
                corsi_used[0] += 1
            T.loc[(t, "5on5"), ["shotsOnGoalFor", "shotsOnGoalAgainst", "goalsFor", "goalsAgainst", "iceTime"]] += [s5, os5, max(gf - ppg, 0), max(ga - oppg, 0), e5_.iceTime / max(e5_.games_played, 1)]
            gk = gm_.get(side + "goalie")
            if gk and gk.get("name"):
                m_ = (goalies.team == t) & (goalies.name == gk["name"]) & (goalies.situation == "all")
                if m_.any():
                    i = goalies.index[m_][0]
                    rate = goalies.at[i, "xGoals"] / max(goalies.at[i, "ongoal"], 1)
                    goalies.at[i, "xGoals"] += gk["sa"] * rate
                    goalies.at[i, "goals"] += gk["ga"]; goalies.at[i, "ongoal"] += gk["sa"]
                    goalies.at[i, "games_played"] += 1; goalies.at[i, "icetime"] += 3600
    return T.reset_index(), goalies
if inc_games:
    teams, goalies = _apply_increments(teams, goalies, inc_games)


def sit(df, s):
    return df[df.situation == s].set_index("team")


cur = {s: sit(teams, s) for s in ["all", "5on5", "5on4", "4on5", "other"]}
pri = {s: sit(prev, s) for s in ["all", "5on5", "5on4", "4on5", "other"]}
IDX = cur["all"].index
GP = cur["all"].games_played
W = GP / (GP + K_GAMES)
import os
BLIND = os.environ.get("BLIND") == "1"   # backtest: ignore all 2026-27 data
if BLIND: W = W * 0


def ratios(df):
    """5v5 per-60 rates as ratios to league average, each stat on its own scale."""
    hrs = df.iceTime / 3600
    out = {}
    for k, f, a in [("xg", "scoreVenueAdjustedxGoalsFor", "scoreVenueAdjustedxGoalsAgainst"),
                    ("c", "scoreAdjustedShotsAttemptsFor", "scoreAdjustedShotsAttemptsAgainst"),
                    ("f", "scoreAdjustedUnblockedShotAttemptsFor", "scoreAdjustedUnblockedShotAttemptsAgainst"),
                    ("g", "goalsFor", "goalsAgainst")]:
        F, A = df[f] / hrs, df[a] / hrs
        lg = (F.mean() + A.mean()) / 2
        out[k] = (F / lg, A / lg)
    lgxg = (df.scoreVenueAdjustedxGoalsFor.sum() + df.scoreVenueAdjustedxGoalsAgainst.sum()) / (2 * hrs.sum())
    return out, lgxg


def shrink(obs, prior, w):
    return prior + w * (obs - prior)


# 5-on-5
R, LG5 = ratios(cur["5on5"])
R1, _ = ratios(pri["5on5"])
prev2 = pd.read_csv(D / "teams_2024.csv")
R2, _ = ratios(prev2[(prev2.season == 2024) & (prev2.situation == "5on5")].set_index("team"))
obsF = W_XG * R["xg"][0] + W_CORSI * R["c"][0] + W_FENWICK * R["f"][0] + W_GOALS * R["g"][0]
obsA = W_XG * R["xg"][1] + W_CORSI * R["c"][1] + W_FENWICK * R["f"][1] + W_GOALS * R["g"][1]


def prior_ratio(side):
    tot = pd.Series(1.0, index=IDX)
    for k in ["xg", "c", "f", "g"]:
        tot += PRIOR_COEF[k + "1"] * (R1[k][side].reindex(IDX).fillna(1) - 1)
        tot += PRIOR_COEF[k + "2"] * (R2[k][side].reindex(IDX).fillna(1) - 1)
    return tot


# ---------------- Recent form (v0.6) ----------------
# Backtest on 13 training seasons + untouched 2024-26: blending each team's last-10-game rates into
# this season's numbers at 30% improved log loss (better in 87% of resamples). Live, the last 10 games
# come from daily box scores (shots on goal + goals as the per-game measure), so the effect ramps in as
# results accumulate: weight = 0.30 * min(1, games/10), and nothing until a team has 5 recorded games.
RECENT_W, RECENT_N, RECENT_MIN = 0.30, 10, 5
recent_used = {}
# Live source: every finished game of the last 35 days from the NHL score feed (RECENT_FILE), so recent form works from
# the start of the season instead of waiting for 5 games after the snapshot. Falls back to the post-snapshot games.
_rf = Path(os.environ["RECENT_FILE"]) if os.environ.get("RECENT_FILE") else None
recent_games = json.loads(_rf.read_text()).get("games", []) if _rf and _rf.exists() else inc_games
if recent_games:
    lg_sog = cur["all"].shotsOnGoalFor.sum() / GP.sum()
    lg_gpg = cur["all"].goalsFor.sum() / GP.sum()
    for t in IDX:
        mine = [g_ for g_ in sorted(recent_games, key=lambda x: x["date"]) if t in (g_["away"], g_["home"])][-RECENT_N:]
        if len(mine) < RECENT_MIN:
            continue
        sf = sa = gf = ga = 0.0
        for g_ in mine:
            me, op = ("a", "h") if g_["away"] == t else ("h", "a")
            sf += g_[me + "sog"]; sa += g_[op + "sog"]; gf += g_[me + "g"]; ga += g_[op + "g"]
        n_ = len(mine)
        rF = 0.8 * (sf / n_) / lg_sog + 0.2 * (gf / n_) / lg_gpg
        rA = 0.8 * (sa / n_) / lg_sog + 0.2 * (ga / n_) / lg_gpg
        rw = RECENT_W * min(1.0, n_ / RECENT_N)
        obsF[t] = (1 - rw) * obsF[t] + rw * rF
        obsA[t] = (1 - rw) * obsA[t] + rw * rA
        recent_used[t] = n_

off5 = shrink(obsF, prior_ratio(0), W)
def5 = shrink(obsA, prior_ratio(1), W)


# Special teams (xG only)
def st_rate(df, col):
    hrs = df.iceTime / 3600
    return df[col] / hrs, df[col].sum() / hrs.sum()


ppF, LGPP = st_rate(cur["5on4"], "xGoalsFor")
pkA, LGPK = st_rate(cur["4on5"], "xGoalsAgainst")
shF, LGSH = st_rate(cur["4on5"], "xGoalsFor")          # shorthanded chances
p_ppF, p_LGPP = st_rate(pri["5on4"], "xGoalsFor")
p_pkA, p_LGPK = st_rate(pri["4on5"], "xGoalsAgainst")
w_pp = (cur["5on4"].iceTime / 60) / (cur["5on4"].iceTime / 60 + K_ST_MIN)
w_pk = (cur["4on5"].iceTime / 60) / (cur["4on5"].iceTime / 60 + K_ST_MIN)
if BLIND: w_pp, w_pk = w_pp * 0, w_pk * 0
offPP = shrink(ppF / LGPP, (1 + ST_PRIOR_CARRY * (p_ppF / p_LGPP - 1)).reindex(IDX).fillna(1), w_pp)
defPK = shrink(pkA / LGPK, (1 + ST_PRIOR_CARRY * (p_pkA / p_LGPK - 1)).reindex(IDX).fillna(1), w_pk)

# Penalty draw / take rates per game
drawn = cur["all"].penaltiesAgainst / GP
taken = cur["all"].penaltiesFor / GP
LG_PEN = cur["all"].penaltiesFor.sum() / GP.sum()
p_drawn = (pri["all"].penaltiesAgainst / pri["all"].games_played).reindex(IDX).fillna(LG_PEN)
p_taken = (pri["all"].penaltiesFor / pri["all"].games_played).reindex(IDX).fillna(LG_PEN)
p_lgpen = pri["all"].penaltiesFor.sum() / pri["all"].games_played.sum()
drawnR = shrink(drawn / LG_PEN, 1 + PRIOR_CARRY * (p_drawn / p_lgpen - 1), W)
takenR = shrink(taken / LG_PEN, 1 + PRIOR_CARRY * (p_taken / p_lgpen - 1), W)

# League minutes per game by state (per team)
MIN_PP = (cur["5on4"].iceTime.sum() / 60) / GP.sum()
MIN_OTHER = (cur["other"].iceTime.sum() / 60) / GP.sum()
MIN_5 = (cur["5on5"].iceTime.sum() / 60) / GP.sum()
LGOTHER = (cur["other"].xGoalsFor.sum()) / (cur["other"].iceTime.sum() / 3600)


def lam(t, o, pp_t, pp_o):
    """Expected goals for team t vs o, given PP minutes for t and for o."""
    t5 = MIN_5 + (2 * MIN_PP - pp_t - pp_o)   # 5v5 absorbs special-teams time differences
    return (LG5 * off5[t] * def5[o] * t5 / 60
            + LGPP * offPP[t] * defPK[o] * pp_t / 60
            + LGSH * pp_o / 60
            + LGOTHER * MIN_OTHER / 60)


def pp_minutes(t, o):
    return MIN_PP * (drawnR[t] + takenR[o]) / 2


# ---------------- Goalies ----------------
g = goalies[goalies.situation == "all"].copy()
g["gsax"] = g.xGoals - g.goals
gh = pd.read_csv(D / "goalies_hist.csv")
gh = gh[gh.situation == "all"]
p1 = gh[gh.season == 2025].set_index("playerId"); p2 = gh[gh.season == 2024].set_index("playerId")
def _prev(df_, pid, col):
    return float(df_.loc[pid, col]) if pid in df_.index else 0.0
g["gs_w"] = [(0 if BLIND else r.gsax) + G_W1 * (_prev(p1, r.playerId, "xGoals") - _prev(p1, r.playerId, "goals"))
             + G_W2 * (_prev(p2, r.playerId, "xGoals") - _prev(p2, r.playerId, "goals")) for _, r in g.iterrows()]
g["xg_w"] = [(0 if BLIND else r.xGoals) + G_W1 * _prev(p1, r.playerId, "xGoals") + G_W2 * _prev(p2, r.playerId, "xGoals") for _, r in g.iterrows()]
g["eff"] = g.gs_w / (g.xg_w + K_GOALIE_XGA)   # 3 seasons, fitted weights, fitted regression


def parse_return(text, season_start=date(2026, 7, 1)):
    m = re.search(r"at least ([A-Z][a-z]{2}) (\d{1,2})", text)
    if not m:
        return None
    mon, day = MONTHS[m.group(1)], int(m.group(2))
    yr = 2026 if mon >= 7 else 2027
    return date(yr, mon, day)


def out_prob(status, gdate):
    """Probability a listed player misses a game on gdate."""
    s = status.lower()
    if "day-to-day" in s or "day to day" in s:
        return 0.5 if gdate <= date.today() + timedelta(days=2) else 0.0   # listed day-to-day: coin flip for the next couple of days
    if "out for the season" in s or "out for season" in s or "season-ending" in s:
        return 1.0
    ret = parse_return(status)
    if ret is None:
        return 0.5
    if gdate < ret: return 1.0
    if gdate == ret: return 0.5
    if gdate <= ret + timedelta(days=7): return 0.25
    return 0.0


inj = []
for city, name, pos, status in gd["injuries"]:
    t = CITY.get(city)
    if not t:
        warnings.append(f"Unknown team '{city}' in injury list"); continue
    inj.append({"team": t, "name": name, "pos": pos, "status": status})

goalie_out = {}
for x in inj:
    if x["pos"] == "G":
        goalie_out[(x["team"], x["name"])] = x["status"]


notes = []
_ghn = gh.set_index("name") if "name" in gh.columns else None
def hist_eff(name):
    """Rating for a goalie with no games for his current team yet, from the two previous seasons (any team)."""
    rows = gh[gh.name == name]
    if rows.empty:
        return None
    a = rows[rows.season == 2025]; b = rows[rows.season == 2024]
    xg1, g1 = float(a.xGoals.sum()), float(a.goals.sum()); xg2, g2 = float(b.xGoals.sum()), float(b.goals.sum())
    gs_w = G_W1 * (xg1 - g1) + G_W2 * (xg2 - g2); xg_w = G_W1 * xg1 + G_W2 * xg2
    return gs_w / (xg_w + K_GOALIE_XGA)


def goalie_effect(team, gdate, starter=None, status=None):
    d = g[g.team == team].copy()
    d["avail"] = [1 - out_prob(goalie_out[(team, n)], gdate) if (team, n) in goalie_out else 1.0 for n in d.name]
    d["w"] = d.icetime * d.avail
    if d.w.sum() <= 0:
        d["w"] = d.icetime
    blend = float((d.w * d.eff).sum() / d.w.sum())
    if starter:
        mn = match_name(starter, d.name.tolist())
        row = d[d.name == mn] if mn else d.iloc[0:0]
        if row.empty:
            he = hist_eff(starter)
            if he is None:
                warnings.append(f"Starter {starter} not found on {team}; using blend")
                eff = blend
            else:   # new to this team (trade, signing, call-up): rated on his last two seasons, same formula as everyone
                notes.append(f"Starter {starter} is new to {team}; rated on his last two seasons")
                eff = he if status == "Confirmed" else UNCONF_STARTER_W * he + (1 - UNCONF_STARTER_W) * blend
        else:
            se = float(row.eff.iloc[0])
            eff = se if status == "Confirmed" else UNCONF_STARTER_W * se + (1 - UNCONF_STARTER_W) * blend
    else:
        eff = blend
    return max(-GOALIE_CAP, min(GOALIE_CAP, eff))


# ---------------- Player values (for injuries) ----------------
s5 = skaters[skaters.situation == "5on5"].copy()
s5["mins"] = s5.icetime / 60
s5["on_diff60"] = (s5.OnIce_F_xGoals - s5.OnIce_A_xGoals) / (s5.icetime / 3600)
s5["off_diff60"] = (s5.OffIce_F_xGoals - s5.OffIce_A_xGoals) / (s5.timeOnBench / 3600).where(s5.timeOnBench > 0, 1)
s5["rel_cur"] = s5.on_diff60 - s5.off_diff60
sp = pd.read_csv(D / "skaters_2025.csv")
sp = sp[sp.situation == "5on5"].set_index("playerId")
sp_rel = (sp.OnIce_F_xGoals - sp.OnIce_A_xGoals) / (sp.icetime / 3600) - (sp.OffIce_F_xGoals - sp.OffIce_A_xGoals) / (sp.timeOnBench / 3600).where(sp.timeOnBench > 0, 1)
s5["rel_prev"] = s5.playerId.map(sp_rel).fillna(0.0)
s5["min_prev"] = s5.playerId.map(sp.icetime / 60).fillna(0.0) * 0.75          # last season counts 75% (fitted carry ~0.7-0.8)
s5["rel60"] = (s5.rel_cur * s5.mins + s5.rel_prev * s5.min_prev) / (s5.mins + s5.min_prev + K_PLAYER_MIN)
s5["toi_gp"] = s5.mins / s5.games_played.where(s5.games_played > 0, 1)
s5["value"] = (s5.rel60 * s5.toi_gp / 60 * PLAYER_SHARE).clip(-PLAYER_CAP, PLAYER_CAP)
s5["share_played"] = s5.games_played / s5.team.map(GP)
pv = {(r.team, r["name"]): r for _, r in s5.iterrows()}

matched, unmatched = 0, []
inj_players = {}
for x in inj:
    if x["pos"] == "G":
        continue
    r = pv.get((x["team"], x["name"]))
    if r is None:
        mn = match_name(x["name"], [k[1] for k in pv if k[0] == x["team"]])
        r = pv.get((x["team"], mn)) if mn else None
    if r is None:
        unmatched.append(f'{x["name"]} ({x["team"]})')   # hasn't played this season: already absent from team stats
        continue
    matched += 1
    inj_players.setdefault(x["team"], []).append((x, float(r.value), float(r.share_played)))


def injury_adj(team, gdate):
    """Goals/game the team loses from players out (only the part already baked into its stats)."""
    tot, who = 0.0, []
    for x, val, share in inj_players.get(team, []):
        p = out_prob(x["status"], gdate)
        if p <= 0:
            continue
        a = max(0.0, val - REPLACEMENT) * share * p
        tot += a
        if p >= 0.5 and val - REPLACEMENT >= 0.06:
            who.append(x["name"])
    tot = max(-TEAM_INJ_CAP, min(TEAM_INJ_CAP, tot))
    return tot, who


# ---------------- Schedule context: rest + travel ----------------
weeks = json.loads(WEEKS.read_text())
sched = []
for r in gd["recent_games"]:
    d_, a_, h_ = r.split("|"); sched.append((d_, a_, h_))
for wk in weeks.values():
    for gm in wk["games"]:
        sched.append((gm["date"], gm["away"], gm["home"]))
sched.sort()
last = {}   # team -> (date, venue team) of previous game, filled in date order
context = {}
for d_, a_, h_ in sched:
    for t in (a_, h_):
        context[(t, d_)] = last.get(t)
    last[a_] = (d_, h_); last[h_] = (d_, h_)


def rest_travel(team, gdate_s, venue):
    prev_ = context.get((team, gdate_s))
    b2b = travel = False
    if prev_:
        pd_, pven = prev_
        b2b = (date.fromisoformat(gdate_s) - date.fromisoformat(pd_)).days == 1
        travel = abs(TZ[pven] - TZ[venue]) >= 2
    return b2b, travel


# ---------------- Win probability ----------------
def pois(k, l):
    return math.exp(-l) * l ** k / math.factorial(k)


def win_prob(lh, la):
    ph = pa = pt = 0.0
    for i in range(16):
        pi = pois(i, lh)
        for j in range(16):
            p = pi * pois(j, la)
            if i > j: ph += p
            elif i < j: pa += p
            else: pt += p
    return ph + pt * OT_HOME_WIN, pa + pt * (1 - OT_HOME_WIN), pt


AVG_LAM = LG5 * MIN_5 / 60 + LGPP * MIN_PP / 60 + LGSH * MIN_PP / 60 + LGOTHER * MIN_OTHER / 60
ACTUAL_GPG = float(cur["all"].goalsFor.sum() / GP.sum())
SCALE = ACTUAL_GPG / AVG_LAM   # xG runs a bit hot vs. real scoring; anchor to actual goals per game
LG5 *= SCALE; LGPP *= SCALE; LGSH *= SCALE; LGOTHER *= SCALE; AVG_LAM = ACTUAL_GPG
lo, hi = 0.0, 0.2
for _ in range(40):
    mid = (lo + hi) / 2
    if win_prob(AVG_LAM * (1 + mid), AVG_LAM * (1 - mid))[0] < HOME_WIN_TARGET: lo = mid
    else: hi = mid
HOME_EDGE = (lo + hi) / 2

starters = {}
for d_, rows in gd["starters"].items():
    for a_, ag, ast, h_, hg, hst in rows:
        starters[(d_, a_)] = (ag, ast); starters[(d_, h_)] = (hg, hst)

def calib(ph):
    return 1 / (1 + math.exp(-(STRETCH[0] * math.log(ph / (1 - ph)) + STRETCH[1])))


def lam5_only(t, o):
    """Expected goals with 5-on-5 strength only (special teams at league average)."""
    return LG5 * off5[t] * def5[o] * MIN_5 / 60 + LGPP * MIN_PP / 60 + LGSH * MIN_PP / 60 + LGOTHER * MIN_OTHER / 60


games = {}
for wk in weeks.values():
    for gm in wk["games"]:
        h, a, ds = gm["home"], gm["away"], gm["date"]
        gdate = date.fromisoformat(ds)
        # Factors are applied in a fixed order; each step's change in home win probability is recorded,
        # so the explanation is the model's own arithmetic and sums exactly to the final number.
        steps = []
        def step(name, lh_, la_, prev):
            p_ = win_prob(max(lh_, 0.5), max(la_, 0.5))[0]
            steps.append([name, round(p_ - prev, 4)])
            return p_
        p0 = 0.5
        lh, la = lam5_only(h, a), lam5_only(a, h)
        p1 = step("5-on-5 play", lh, la, p0)
        ppH, ppA = pp_minutes(h, a), pp_minutes(a, h)
        lh, la = lam(h, a, ppH, ppA), lam(a, h, ppA, ppH)
        p2 = step("Special teams", lh, la, p1)
        lh *= 1 + HOME_EDGE; la *= 1 - HOME_EDGE
        p3 = step("Home ice", lh, la, p2)
        sH = starters.get((ds, h)); sA = starters.get((ds, a))
        geH = goalie_effect(h, gdate, *(sH or (None, None)))
        geA = goalie_effect(a, gdate, *(sA or (None, None)))
        lh *= 1 - geA; la *= 1 - geH
        p4 = step("Goaltending", lh, la, p3)
        iH, whoH = injury_adj(h, gdate); iA, whoA = injury_adj(a, gdate)
        lh -= iH / 2; la += iH / 2          # half offense, half defense
        la -= iA / 2; lh += iA / 2
        p5 = step("Injuries", lh, la, p4)
        hb2b, htrav = rest_travel(h, ds, h)
        ab2b, atrav = rest_travel(a, ds, h)
        if hb2b: lh *= 1 - B2B_PENALTY; la *= 1 + B2B_PENALTY
        if ab2b: la *= 1 - B2B_PENALTY; lh *= 1 + B2B_PENALTY
        if atrav: la *= 1 - TRAVEL_PENALTY
        lh, la = max(lh, 0.5), max(la, 0.5)
        p6 = step("Rest and travel", lh, la, p5)
        ph_raw, pa_raw, pt = win_prob(lh, la)
        ph = calib(ph_raw); pa = 1 - ph
        steps.append(["Calibration", round(ph - p6, 4)])
        steps = [s_ for s_ in steps if abs(s_[1]) >= 0.0005]
        drift = round(ph - 0.5 - sum(s_[1] for s_ in steps), 4)
        if steps and drift: steps[-1][1] = round(steps[-1][1] + drift, 4)   # rounding only
        gH = (sH[0] + (" ✓" if sH[1] == "Confirmed" else "")) if sH else g[g.team == h].sort_values("icetime").name.iloc[-1]
        gA = (sA[0] + (" ✓" if sA[1] == "Confirmed" else "")) if sA else g[g.team == a].sort_values("icetime").name.iloc[-1]
        missing = []
        if not sH or not sA: missing.append("Starting goalies not published yet; using each team's usual goalie mix")
        elif sH[1] != "Confirmed" or sA[1] != "Confirmed": missing.append("Starting goalies projected, not confirmed")
        games[gm["id"]] = {
            "id": gm["id"], "home": h, "away": a,
            "pHome": round(ph, 4), "pAway": round(pa, 4), "pOT": round(pt, 4),
            "xgHome": round(lh, 2), "xgAway": round(la, 2),
            "b2bHome": hb2b, "b2bAway": ab2b, "travelAway": atrav,
            "goalieHome": gH, "goalieAway": gA, "goalieSource": "Daily Faceoff" if sH else "projected",
            "goalieConfirmed": bool(sH and sA and sH[1] == "Confirmed" and sA[1] == "Confirmed"),
            "injHome": whoH, "injAway": whoA,
            "injAdjHome": round(iH, 3), "injAdjAway": round(iA, 3),
            "factors": steps, "inputsMissing": missing,
        }

# ---------------- Team table ----------------
ev = cur["5on5"]
out_teams = {}
for t in IDX:
    e = ev.loc[t]
    sh = e.goalsFor / e.shotsOnGoalFor
    sv = 1 - e.goalsAgainst / e.shotsOnGoalAgainst
    ppT = pp_minutes(t, t)  # vs a mirror average-ish opponent isn't right; use league-average opponent below
    # strength vs a league-average opponent, neutral ice, average goalie, healthy
    lf = (LG5 * off5[t] * MIN_5 / 60 + LGPP * offPP[t] * MIN_PP * (drawnR[t] + 1) / 2 / 60
          + LGSH * MIN_PP * (takenR[t] + 1) / 2 / 60 + LGOTHER * MIN_OTHER / 60)
    la_ = (LG5 * def5[t] * MIN_5 / 60 + LGPP * defPK[t] * MIN_PP * (takenR[t] + 1) / 2 / 60
           + LGSH * MIN_PP * (drawnR[t] + 1) / 2 / 60 + LGOTHER * MIN_OTHER / 60)
    gb = goalie_effect(t, date(2026, 10, 25))
    la_ *= 1 - gb
    best = g[g.team == t].sort_values("icetime").iloc[-1]
    out_teams[t] = {
        "gp": int(GP[t]),
        "cf": round(float(e.corsiPercentage), 3), "ff": round(float(e.fenwickPercentage), 3),
        "xgPct": round(float(e.xGoalsPercentage), 3),
        "pdo": round(float(sh + sv), 3), "shPct": round(float(sh), 3), "svPct": round(float(sv), 3),
        "gf": int(cur["all"].loc[t].goalsFor), "ga": int(cur["all"].loc[t].goalsAgainst),
        "off": round(float(off5[t]), 3), "def": round(float(def5[t]), 3),
        "pp": round(float(offPP[t]), 3), "pk": round(float(defPK[t]), 3),
        "drawn": round(float(drawnR[t]), 3), "taken": round(float(takenR[t]), 3),
        "rating": round(float((lf - la_) / AVG_LAM * 100), 1),
        "goalie": {"name": best["name"], "gp": int(best.games_played),
                   "gsax": round(float(best.gsax), 1), "svAdj": round(float(gb), 4)},
    }
for i, t in enumerate(sorted(out_teams, key=lambda t: -out_teams[t]["rating"]), 1):
    out_teams[t]["rank"] = i

model = {
    "version": "v0.6.2", "newGamesApplied": len(inc_games), "recentFormTeams": len(recent_used), "boxScoreCorsiTeamGames": corsi_used[0], "pbpShotQualityTeamGames": pbp_used[0],
    "dataThrough": f"2026-27 to date + 2025-26 prior; goalies: {gd.get('source_goalies') or 'none'}; injuries: {gd.get('source_injuries') or 'none'}",
    "assumptions": {
        "blend5v5": {"xG": W_XG, "corsi": W_CORSI, "fenwick": W_FENWICK, "goals": W_GOALS}, "priorCoef": PRIOR_COEF, "goalieK": K_GOALIE_XGA,
        "stPriorCarry": ST_PRIOR_CARRY, "kGames": K_GAMES,
        "homeWinEvenTeams": HOME_WIN_TARGET, "homeScoringBoost": round(HOME_EDGE, 4),
        "b2b": B2B_PENALTY, "travel": TRAVEL_PENALTY, "recentForm": RECENT_W, "goalieCap": GOALIE_CAP,
    },
    "sources": {"goalies": gd["source_goalies"], "injuries": gd["source_injuries"]},
    "warnings": warnings, "notes": notes,
    "teams": out_teams,
    "games": games,
}
OUT.write_text(json.dumps(model, indent=1))

# ---------------- Validation printout ----------------
G = list(games.values())
print("avg lambda/team/game:", round(AVG_LAM, 3), "scale", round(SCALE, 3), "| home boost:", round(HOME_EDGE, 4))
print("league min/game 5v5, PP, other:", round(MIN_5, 1), round(MIN_PP, 1), round(MIN_OTHER, 1))
print("games:", len(G), "avg home win:", round(sum(x["pHome"] for x in G) / len(G), 3),
      "range:", round(min(x["pHome"] for x in G), 3), round(max(x["pHome"] for x in G), 3))
print("avg total goals/game:", round(sum(x["xgHome"] + x["xgAway"] for x in G) / len(G), 2))
print("max prob sum error:", max(abs(x["pHome"] + x["pAway"] - 1) for x in G))
print("injuries matched to player data:", matched, "| not playing this season (no adj):", len(unmatched))
print("warnings:", warnings)
rk = sorted(out_teams, key=lambda t: -out_teams[t]["rating"])
print("top:", [(t, out_teams[t]["rating"]) for t in rk[:6]])
print("bottom:", [(t, out_teams[t]["rating"]) for t in rk[-5:]])
print("PP best:", sorted(out_teams, key=lambda t: -out_teams[t]["pp"])[:3], "PK best:", sorted(out_teams, key=lambda t: out_teams[t]["pk"])[:3])
big = sorted(((t, x["name"], round(v, 3), round(sh_, 2)) for t, L in inj_players.items() for x, v, sh_ in L), key=lambda z: -abs(z[2] * z[3]))[:8]
print("biggest injury effects (team, player, value g/gm, share played):", big)
