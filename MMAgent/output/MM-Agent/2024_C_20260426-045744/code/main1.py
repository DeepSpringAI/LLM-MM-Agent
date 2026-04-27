# Here is the Python code.
import os
import json
import math
import warnings
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# Optional imports with safe fallback
try:
    import statsmodels.api as sm
    import statsmodels.formula.api as smf
    HAS_STATSMODELS = True
except Exception:
    HAS_STATSMODELS = False

try:
    from sklearn.metrics import brier_score_loss, log_loss
    HAS_SKLEARN = True
except Exception:
    HAS_SKLEARN = False


# ----------------------------
# Helper utilities
# ----------------------------

STD_SCORE_ORDER = ["0", "15", "30", "40", "AD"]
STD_SCORE_TO_INT = {"0": 0, "15": 1, "30": 2, "40": 3, "AD": 4}
INT_TO_STD_SCORE = {0: "0", 1: "15", 2: "30", 3: "40", 4: "AD"}


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def safe_numeric(series):
    return pd.to_numeric(series, errors="coerce")


def parse_elapsed_seconds(x):
    if pd.isna(x):
        return np.nan
    try:
        parts = str(x).split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + int(s)
        return np.nan
    except Exception:
        return np.nan


def tennis_score_state(p1_score, p2_score):
    p1 = str(p1_score)
    p2 = str(p2_score)

    # standard deuce / ad mapping
    if p1 == "40" and p2 == "40":
        return "deuce"
    if p1 == "AD" and p2 == "40":
        return "ad-in"
    if p1 == "40" and p2 == "AD":
        return "ad-out"

    return f"{p1}-{p2}"


def is_tiebreak_from_scores(p1_score, p2_score):
    # In this dataset, tiebreak point scores are typically numeric and not standard tennis labels.
    std = {"0", "15", "30", "40", "AD"}
    s1 = str(p1_score)
    s2 = str(p2_score)
    return (s1 not in std) or (s2 not in std)


def parse_tiebreak_score(p1_score, p2_score):
    try:
        return int(float(p1_score)), int(float(p2_score))
    except Exception:
        return np.nan, np.nan


def tiebreak_state_group(p1_score, p2_score):
    a, b = parse_tiebreak_score(p1_score, p2_score)
    if pd.isna(a) or pd.isna(b):
        return "tb_unknown"
    diff = int(a - b)
    if diff <= -3:
        diff_g = "tb_diff_le_-3"
    elif diff >= 3:
        diff_g = "tb_diff_ge_3"
    else:
        diff_g = f"tb_diff_{diff}"
    late = "late" if (a + b) >= 10 else "early"
    imminent = "imminent" if max(a, b) >= 6 else "not_imminent"
    return f"{diff_g}_{late}_{imminent}"


def current_game_point_counts(p1_score, p2_score):
    # Convert standard game score to underlying point counts for game-point logic.
    # Only for non-tiebreak states.
    s1 = str(p1_score)
    s2 = str(p2_score)

    if s1 == "AD" and s2 == "40":
        return 4, 3
    if s1 == "40" and s2 == "AD":
        return 3, 4
    if s1 in STD_SCORE_TO_INT and s2 in STD_SCORE_TO_INT:
        return STD_SCORE_TO_INT[s1], STD_SCORE_TO_INT[s2]
    return np.nan, np.nan


def classify_pressure(row):
    """
    Returns:
      break_point_p1, break_point_p2, game_point_p1, game_point_p2,
      multiple_break_point_for_p1, multiple_break_point_for_p2,
      set_point_p1, set_point_p2
    """
    p1_bp = int(row.get("p1_break_pt", 0) == 1)
    p2_bp = int(row.get("p2_break_pt", 0) == 1)
    server = int(row["server"]) if not pd.isna(row["server"]) else np.nan
    tiebreak = int(row["tiebreak"])

    gp1 = gp2 = 0
    mbp1 = mbp2 = 0
    sp1 = sp2 = 0

    if tiebreak == 0:
        a, b = current_game_point_counts(row["p1_score"], row["p2_score"])
        if not (pd.isna(a) or pd.isna(b)):
            # game point:
            # player has game point if winning this point wins the game
            # Standard logic.
            if a >= 3 and (a - b) >= 1:
                gp1 = 1
            if b >= 3 and (b - a) >= 1:
                gp2 = 1

            # multiple break point:
            # receiver has 2+ game-winning chances if score is 0-40 or 15-40 etc.
            # Approximate with number of game points available to receiver > 1.
            # Server identity matters.
            if server == 2 and p1_bp == 1:
                # p1 receiving
                # approximate number of chances:
                if str(row["p1_score"]) == "40" and str(row["p2_score"]) in {"0", "15"}:
                    mbp1 = 1
                elif str(row["p1_score"]) == "30" and str(row["p2_score"]) == "40":
                    mbp1 = 0
                elif str(row["p1_score"]) == "40" and str(row["p2_score"]) == "30":
                    mbp1 = 0
                elif str(row["p1_score"]) == "15" and str(row["p2_score"]) == "40":
                    mbp1 = 1
                elif str(row["p1_score"]) == "0" and str(row["p2_score"]) == "40":
                    mbp1 = 1
            if server == 1 and p2_bp == 1:
                if str(row["p2_score"]) == "40" and str(row["p1_score"]) in {"0", "15"}:
                    mbp2 = 1
                elif str(row["p2_score"]) == "15" and str(row["p1_score"]) == "40":
                    mbp2 = 1
                elif str(row["p2_score"]) == "0" and str(row["p1_score"]) == "40":
                    mbp2 = 1

        # Set point approximation for standard games:
        # If winning this game would win the set and player has game point.
        p1_games = int(row["p1_games"]) if not pd.isna(row["p1_games"]) else np.nan
        p2_games = int(row["p2_games"]) if not pd.isna(row["p2_games"]) else np.nan

        if not (pd.isna(p1_games) or pd.isna(p2_games)):
            # if player currently leads enough that winning game ends set
            # Wimbledon final-set tiebreak at 6-6 in 2023; non-tiebreak set win when game becomes 6 with margin >=2 or 7-5.
            # Approximate based on current game score:
            # player at 5+ and lead >=1, or at 6 with lead 0 in non-tiebreak impossible unless malformed.
            p1_wins_set_if_game = ((p1_games == 5 and p2_games <= 4) or
                                   (p1_games == 6 and p2_games == 5))
            p2_wins_set_if_game = ((p2_games == 5 and p1_games <= 4) or
                                   (p2_games == 6 and p1_games == 5))

            if p1_wins_set_if_game and gp1 == 1:
                sp1 = 1
            if p2_wins_set_if_game and gp2 == 1:
                sp2 = 1
    else:
        # Tiebreak set point
        a, b = parse_tiebreak_score(row["p1_score"], row["p2_score"])
        if not (pd.isna(a) or pd.isna(b)):
            if a >= 6 and (a - b) >= 0:
                sp1 = 1
            if b >= 6 and (b - a) >= 0:
                sp2 = 1

    return pd.Series({
        "p1_game_point": gp1,
        "p2_game_point": gp2,
        "p1_multiple_break_point": mbp1,
        "p2_multiple_break_point": mbp2,
        "p1_set_point": sp1,
        "p2_set_point": sp2
    })


def score_progression_check(group: pd.DataFrame) -> Dict[str, int]:
    """
    Light consistency checks; does not enforce full deterministic repair.
    """
    dup_count = int(group.duplicated(subset=["set_no", "game_no", "point_no"]).sum())
    missing_server = int(group["server"].isna().sum())
    bad_point_victor = int((~group["point_victor"].isin([1, 2])).sum())

    impossible_score = 0
    for _, row in group.iterrows():
        if row["tiebreak"] == 0:
            s1 = str(row["p1_score"])
            s2 = str(row["p2_score"])
            legal = {"0", "15", "30", "40", "AD"}
            if (s1 not in legal) or (s2 not in legal):
                impossible_score += 1
    return {
        "duplicate_point_keys": dup_count,
        "missing_server": missing_server,
        "bad_point_victor": bad_point_victor,
        "impossible_standard_scores": impossible_score
    }


def build_strength_proxy(df: pd.DataFrame) -> pd.DataFrame:
    """
    Leave-match-out restrained in-sample proxy:
    player historical point-win rate on serve and return across OTHER matches in dataset.
    """
    base = df.copy()
    base["p1_won"] = (base["point_victor"] == 1).astype(int)
    base["p2_won"] = (base["point_victor"] == 2).astype(int)

    # player-match rows
    p1_rows = pd.DataFrame({
        "match_id": base["match_id"],
        "player": base["player1"],
        "serve_points_won": np.where(base["server"] == 1, base["p1_won"], np.nan),
        "serve_points_total": np.where(base["server"] == 1, 1, np.nan),
        "return_points_won": np.where(base["server"] == 2, base["p1_won"], np.nan),
        "return_points_total": np.where(base["server"] == 2, 1, np.nan),
    })
    p2_rows = pd.DataFrame({
        "match_id": base["match_id"],
        "player": base["player2"],
        "serve_points_won": np.where(base["server"] == 2, base["p2_won"], np.nan),
        "serve_points_total": np.where(base["server"] == 2, 1, np.nan),
        "return_points_won": np.where(base["server"] == 1, base["p2_won"], np.nan),
        "return_points_total": np.where(base["server"] == 1, 1, np.nan),
    })
    longp = pd.concat([p1_rows, p2_rows], axis=0, ignore_index=True)

    agg = longp.groupby(["player", "match_id"], as_index=False).agg({
        "serve_points_won": "sum",
        "serve_points_total": "sum",
        "return_points_won": "sum",
        "return_points_total": "sum"
    }).fillna(0)

    player_totals = agg.groupby("player", as_index=False).agg({
        "serve_points_won": "sum",
        "serve_points_total": "sum",
        "return_points_won": "sum",
        "return_points_total": "sum"
    })

    player_totals = player_totals.rename(columns={
        "serve_points_won": "all_serve_won",
        "serve_points_total": "all_serve_total",
        "return_points_won": "all_return_won",
        "return_points_total": "all_return_total",
    })

    agg = agg.merge(player_totals, on="player", how="left")

    # leave-match-out rates with slight smoothing
    def loo_rate(w_all, t_all, w_match, t_match, prior=0.5, k=20.0):
        num = (w_all - w_match) + prior * k
        den = (t_all - t_match) + k
        return np.where(den > 0, num / den, prior)

    agg["serve_strength_loo"] = loo_rate(
        agg["all_serve_won"], agg["all_serve_total"],
        agg["serve_points_won"], agg["serve_points_total"],
        prior=0.65, k=20.0
    )
    agg["return_strength_loo"] = loo_rate(
        agg["all_return_won"], agg["all_return_total"],
        agg["return_points_won"], agg["return_points_total"],
        prior=0.35, k=20.0
    )

    # map back to match players
    p1_strength = agg[["player", "match_id", "serve_strength_loo", "return_strength_loo"]].rename(columns={
        "player": "player1",
        "serve_strength_loo": "p1_serve_strength_loo",
        "return_strength_loo": "p1_return_strength_loo"
    })
    p2_strength = agg[["player", "match_id", "serve_strength_loo", "return_strength_loo"]].rename(columns={
        "player": "player2",
        "serve_strength_loo": "p2_serve_strength_loo",
        "return_strength_loo": "p2_return_strength_loo"
    })

    match_strength = df[["match_id", "player1", "player2"]].drop_duplicates()
    match_strength = match_strength.merge(p1_strength, on=["match_id", "player1"], how="left")
    match_strength = match_strength.merge(p2_strength, on=["match_id", "player2"], how="left")

    match_strength["strength_diff_general"] = (
        (match_strength["p1_serve_strength_loo"] + match_strength["p1_return_strength_loo"]) -
        (match_strength["p2_serve_strength_loo"] + match_strength["p2_return_strength_loo"])
    )
    match_strength["strength_diff_serve"] = match_strength["p1_serve_strength_loo"] - match_strength["p2_serve_strength_loo"]
    match_strength["strength_diff_return"] = match_strength["p1_return_strength_loo"] - match_strength["p2_return_strength_loo"]

    return match_strength


# ----------------------------
# Model wrapper
# ----------------------------

class Model1:
    def __init__(self, output_dir="./task1_outputs"):
        self.output_dir = output_dir
        ensure_dir(self.output_dir)
        self.model = None
        self.formula = None
        self.fit_summary_text = None
        self.metrics = {}

    def load_data(self, path="Wimbledon_featured_matches.csv") -> pd.DataFrame:
        print(f"[INFO] Loading dataset from: {path}")
        df = pd.read_csv(path)
        print(f"[INFO] Raw shape: {df.shape}")
        return df

    def clean_and_engineer(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict]:
        print("[INFO] Starting cleaning and feature engineering...")

        # Standard type coercion
        numeric_cols = [
            "set_no", "game_no", "point_no", "p1_sets", "p2_sets", "p1_games", "p2_games",
            "server", "serve_no", "point_victor", "p1_points_won", "p2_points_won",
            "game_victor", "set_victor", "p1_break_pt", "p2_break_pt"
        ]
        for c in numeric_cols:
            if c in df.columns:
                df[c] = safe_numeric(df[c])

        # order data
        df = df.sort_values(["match_id", "set_no", "game_no", "point_no"]).reset_index(drop=True)

        # elapsed time
        if "elapsed_time" in df.columns:
            df["elapsed_seconds"] = df["elapsed_time"].apply(parse_elapsed_seconds)
        else:
            df["elapsed_seconds"] = np.nan

        # core target
        df["p1_win_point"] = (df["point_victor"] == 1).astype(int)

        # tiebreak status
        df["tiebreak"] = df.apply(lambda r: int(is_tiebreak_from_scores(r["p1_score"], r["p2_score"])), axis=1)

        # score state
        df["score_state"] = df.apply(
            lambda r: tiebreak_state_group(r["p1_score"], r["p2_score"]) if r["tiebreak"] == 1
            else tennis_score_state(r["p1_score"], r["p2_score"]),
            axis=1
        )

        # set / game context
        df["set_diff"] = df["p1_sets"] - df["p2_sets"]
        df["games_diff"] = df["p1_games"] - df["p2_games"]
        df["late_set"] = ((df[["p1_games", "p2_games"]].max(axis=1)) >= 5).astype(int)
        df["level_games"] = (df["p1_games"] == df["p2_games"]).astype(int)

        # server indicator from player 1 perspective
        df["p1_serving"] = (df["server"] == 1).astype(int)

        # game number / set number
        df["set_no_cat"] = "set_" + df["set_no"].fillna(-1).astype(int).astype(str)
        df["game_no_cat"] = "game_" + df["game_no"].fillna(-1).astype(int).astype(str)

        # pressure indicators
        pressure = df.apply(classify_pressure, axis=1)
        df = pd.concat([df, pressure], axis=1)

        # signed pressure from player 1 perspective
        df["signed_break_point"] = np.where(df["p1_break_pt"] == 1, 1, np.where(df["p2_break_pt"] == 1, -1, 0))
        df["signed_set_point"] = np.where(df["p1_set_point"] == 1, 1, np.where(df["p2_set_point"] == 1, -1, 0))
        df["signed_game_point"] = np.where(df["p1_game_point"] == 1, 1, np.where(df["p2_game_point"] == 1, -1, 0))
        df["multi_break_point"] = np.where(df["p1_multiple_break_point"] == 1, 1,
                                           np.where(df["p2_multiple_break_point"] == 1, -1, 0))

        # approximate match point from set state
        # In Gentlemen's singles best-of-5, match point if player has 2 sets and set point.
        df["p1_match_point"] = ((df["p1_sets"] == 2) & (df["p1_set_point"] == 1)).astype(int)
        df["p2_match_point"] = ((df["p2_sets"] == 2) & (df["p2_set_point"] == 1)).astype(int)
        df["signed_match_point"] = np.where(df["p1_match_point"] == 1, 1, np.where(df["p2_match_point"] == 1, -1, 0))

        # check duplicates and drop exact point-key duplicates keeping first
        before = len(df)
        df = df.drop_duplicates(subset=["match_id", "set_no", "game_no", "point_no"], keep="first").reset_index(drop=True)
        dropped_dups = before - len(df)

        # drop rows with missing essential fields
        essential = ["match_id", "player1", "player2", "server", "point_victor", "set_no", "game_no", "point_no"]
        missing_essential = df[essential].isna().any(axis=1)
        n_missing_essential = int(missing_essential.sum())
        df = df.loc[~missing_essential].copy()

        # drop illegal point victor
        bad_victor = ~df["point_victor"].isin([1, 2])
        n_bad_victor = int(bad_victor.sum())
        df = df.loc[~bad_victor].copy()

        # missing scores: assign category unknown rather than drop if minimal
        df["score_state"] = df["score_state"].fillna("unknown_score")

        # strength proxy
        print("[INFO] Building restrained leave-match-out in-sample strength proxy...")
        strength_df = build_strength_proxy(df)
        df = df.merge(strength_df, on=["match_id", "player1", "player2"], how="left")

        # fill strength missing conservatively
        for c in ["p1_serve_strength_loo", "p1_return_strength_loo", "p2_serve_strength_loo",
                  "p2_return_strength_loo", "strength_diff_general", "strength_diff_serve", "strength_diff_return"]:
            if c in df.columns:
                df[c] = df[c].fillna(df[c].median() if df[c].notna().sum() else 0.0)

        # light sequential audit per match
        audits = []
        for mid, g in df.groupby("match_id", sort=False):
            out = score_progression_check(g)
            out["match_id"] = mid
            audits.append(out)
        audit_df = pd.DataFrame(audits)

        cleaning_report = {
            "raw_rows_after_load": int(before),
            "dropped_duplicate_point_keys": int(dropped_dups),
            "dropped_missing_essential_rows": int(n_missing_essential),
            "dropped_bad_point_victor_rows": int(n_bad_victor),
            "final_rows_after_cleaning": int(len(df)),
            "n_matches": int(df["match_id"].nunique()),
            "audit_totals": audit_df.drop(columns=["match_id"]).sum().to_dict()
        }

        # save audit files
        audit_path = os.path.join(self.output_dir, "task1_match_audit.csv")
        audit_df.to_csv(audit_path, index=False)
        print(f"[INFO] Saved match audit: {audit_path}")

        return df.reset_index(drop=True), cleaning_report

    def fit_baseline_model(self, df: pd.DataFrame) -> pd.DataFrame:
        print("[INFO] Fitting baseline contextual point-win model...")

        # conservative sample restriction to rows with usable features
        model_df = df.copy()

        # reduce high-cardinality score categories if very sparse
        score_counts = model_df["score_state"].value_counts()
        rare_scores = set(score_counts[score_counts < 10].index.tolist())
        model_df["score_state_model"] = model_df["score_state"].apply(lambda x: "rare_score_state" if x in rare_scores else x)

        # coarse time variable
        if model_df["elapsed_seconds"].notna().sum() > 0:
            model_df["elapsed_minutes"] = model_df["elapsed_seconds"] / 60.0
            model_df["elapsed_minutes_centered"] = model_df["elapsed_minutes"] - model_df["elapsed_minutes"].median()
        else:
            model_df["elapsed_minutes_centered"] = 0.0

        # formula
        # Parsimonious model with limited interactions
        self.formula = (
            "p1_win_point ~ p1_serving + C(set_no_cat) + set_diff + games_diff + late_set + "
            "C(score_state_model) + tiebreak + signed_break_point + signed_set_point + "
            "signed_match_point + multi_break_point + strength_diff_general + "
            "p1_serving:strength_diff_general + p1_serving:tiebreak + elapsed_minutes_centered"
        )

        if HAS_STATSMODELS:
            try:
                self.model = smf.glm(
                    formula=self.formula,
                    data=model_df,
                    family=sm.families.Binomial()
                ).fit()
                model_df["baseline_p1_point_win_prob"] = self.model.predict(model_df)
                self.fit_summary_text = self.model.summary().as_text()
                print("[INFO] Model fit completed with statsmodels GLM.")
            except Exception as e:
                print(f"[WARN] Statsmodels fit failed: {e}")
                self.model = None

        if self.model is None:
            # fallback manual baseline: grouped empirical / smoothed probability
            print("[WARN] Falling back to heuristic baseline due to unavailable/failing statsmodels.")
            grp = model_df.groupby(["p1_serving", "score_state_model"])["p1_win_point"].agg(["sum", "count"]).reset_index()
            grp["prob"] = (grp["sum"] + 5) / (grp["count"] + 10)
            model_df = model_df.merge(grp[["p1_serving", "score_state_model", "prob"]],
                                      on=["p1_serving", "score_state_model"], how="left")
            overall = model_df["p1_win_point"].mean()
            model_df["baseline_p1_point_win_prob"] = model_df["prob"].fillna(overall)
            self.fit_summary_text = "Fallback heuristic model used: smoothed empirical probability by serving x score_state."
            self.metrics["fallback_model"] = True

        model_df["baseline_residual"] = model_df["p1_win_point"] - model_df["baseline_p1_point_win_prob"]
        return model_df

    def validate(self, df: pd.DataFrame) -> Dict:
        print("[INFO] Computing validation diagnostics...")
        y = df["p1_win_point"].astype(int).values
        p = np.clip(df["baseline_p1_point_win_prob"].astype(float).values, 1e-6, 1 - 1e-6)

        if HAS_SKLEARN:
            brier = float(brier_score_loss(y, p))
            ll = float(log_loss(y, p))
        else:
            brier = float(np.mean((y - p) ** 2))
            ll = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

        # calibration table by deciles
        tmp = pd.DataFrame({"y": y, "p": p})
        tmp["bin"] = pd.qcut(tmp["p"], q=min(10, tmp["p"].nunique()), duplicates="drop")
        calib = tmp.groupby("bin", observed=False).agg(
            mean_pred=("p", "mean"),
            obs_rate=("y", "mean"),
            n=("y", "size")
        ).reset_index()

        calib_path = os.path.join(self.output_dir, "task1_calibration_table.csv")
        calib.to_csv(calib_path, index=False)

        # coefficient plausibility snapshot
        coeffs = {}
        if self.model is not None and hasattr(self.model, "params"):
            params = self.model.params
            for name in ["p1_serving", "strength_diff_general", "tiebreak",
                         "signed_break_point", "signed_set_point", "signed_match_point",
                         "p1_serving:strength_diff_general", "p1_serving:tiebreak"]:
                coeffs[name] = float(params[name]) if name in params.index else None

        diagnostics = {
            "brier_score": brier,
            "log_loss": ll,
            "mean_predicted_probability": float(np.mean(p)),
            "observed_player1_point_win_rate": float(np.mean(y)),
            "coefficient_snapshot": coeffs,
            "calibration_table_path": calib_path
        }
        self.metrics.update(diagnostics)
        return diagnostics

    def save_outputs(self, df: pd.DataFrame, cleaning_report: Dict, diagnostics: Dict):
        print("[INFO] Saving outputs...")

        prepared_cols = [
            "match_id", "player1", "player2", "set_no", "game_no", "point_no",
            "elapsed_time", "elapsed_seconds", "p1_sets", "p2_sets", "p1_games", "p2_games",
            "p1_score", "p2_score", "server", "point_victor", "p1_win_point",
            "tiebreak", "score_state", "set_diff", "games_diff", "late_set", "level_games",
            "p1_serving", "p1_break_pt", "p2_break_pt",
            "p1_game_point", "p2_game_point", "p1_set_point", "p2_set_point",
            "p1_match_point", "p2_match_point",
            "signed_break_point", "signed_game_point", "signed_set_point", "signed_match_point",
            "multi_break_point",
            "p1_serve_strength_loo", "p1_return_strength_loo", "p2_serve_strength_loo", "p2_return_strength_loo",
            "strength_diff_general", "strength_diff_serve", "strength_diff_return",
            "baseline_p1_point_win_prob", "baseline_residual"
        ]
        prepared_cols = [c for c in prepared_cols if c in df.columns]
        prepared_path = os.path.join(self.output_dir, "task1_prepared_point_level_dataset.csv")
        df[prepared_cols].to_csv(prepared_path, index=False)

        model_info = {
            "model_formula": self.formula,
            "cleaning_report": cleaning_report,
            "diagnostics": diagnostics,
            "notes": [
                "Player labels are already match-consistent in source data and retained as player1/player2 orientation.",
                "Tiebreak status is inferred from non-standard point scores.",
                "Pre-match strength uses restrained leave-match-out in-sample proxy from other matches only.",
                "Baseline is intentionally parsimonious and excludes momentum-like lagged features."
            ]
        }

        report_path = os.path.join(self.output_dir, "task1_model_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(model_info, f, indent=2)

        summary_path = os.path.join(self.output_dir, "task1_model_summary.txt")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(self.fit_summary_text if self.fit_summary_text is not None else "No model summary available.")

        print(f"[INFO] Saved prepared dataset: {prepared_path}")
        print(f"[INFO] Saved model report: {report_path}")
        print(f"[INFO] Saved model summary: {summary_path}")

    def run(self, path="Wimbledon_featured_matches.csv"):
        df = self.load_data(path)
        clean_df, cleaning_report = self.clean_and_engineer(df)
        fitted_df = self.fit_baseline_model(clean_df)
        diagnostics = self.validate(fitted_df)
        self.save_outputs(fitted_df, cleaning_report, diagnostics)

        # Detailed console output
        print("\n" + "=" * 90)
        print("TASK 1 COMPLETE: CLEAN POINT-LEVEL BASELINE MODEL")
        print("=" * 90)
        print("[CLEANING REPORT]")
        print(json.dumps(cleaning_report, indent=2))

        print("\n[MODEL FORMULA]")
        print(self.formula)

        print("\n[VALIDATION DIAGNOSTICS]")
        print(json.dumps(diagnostics, indent=2))

        print("\n[SAMPLE OF FINAL PREPARED DATA]")
        preview_cols = [c for c in [
            "match_id", "player1", "player2", "set_no", "game_no", "point_no",
            "score_state", "p1_serving", "signed_break_point", "signed_set_point",
            "strength_diff_general", "baseline_p1_point_win_prob", "baseline_residual"
        ] if c in fitted_df.columns]
        print(fitted_df[preview_cols].head(15).to_string(index=False))

        if self.model is not None and hasattr(self.model, "params"):
            print("\n[TOP COEFFICIENTS BY ABSOLUTE VALUE]")
            coef_df = pd.DataFrame({
                "term": self.model.params.index,
                "coef": self.model.params.values
            })
            coef_df["abs_coef"] = coef_df["coef"].abs()
            print(coef_df.sort_values("abs_coef", ascending=False).head(20).to_string(index=False))

        return fitted_df, cleaning_report, diagnostics


# ----------------------------
# Task entry point
# ----------------------------

def task1():
    model = Model1(output_dir="./task1_outputs")
    fitted_df, cleaning_report, diagnostics = model.run("Wimbledon_featured_matches.csv")
    return fitted_df, cleaning_report, diagnostics


if __name__ == "__main__":
    task1()