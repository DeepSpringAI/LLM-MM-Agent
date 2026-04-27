# Provide the corrected python code here.
import os
import json
from datetime import timedelta

import pandas as pd
import numpy as np


# The model class
class Model1:
    """
    Lightweight analysis/model utility for Wimbledon featured matches.
    Focus:
    1) Load and validate data
    2) Build match-level and point-level summaries
    3) Estimate token-analogue workload proxies from dataset structure
    4) Produce actionable diagnostics and save outputs for other agents
    """

    def __init__(self, csv_path="Wimbledon_featured_matches.csv", out_dir="outputs"):
        self.csv_path = csv_path
        self.out_dir = out_dir
        os.makedirs(self.out_dir, exist_ok=True)

        self.df = None
        self.df_clean = None
        self.match_summary = None
        self.player_summary = None
        self.rate_limit_proxy = None

    def log(self, msg):
        print(f"[Model1] {msg}")

    def load_data(self):
        self.log(f"Loading data from: {self.csv_path}")
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(f"Dataset not found: {self.csv_path}")
        self.df = pd.read_csv(self.csv_path)
        self.log(f"Loaded dataframe with shape: {self.df.shape}")
        return self.df

    @staticmethod
    def parse_elapsed_time_to_seconds(x):
        if pd.isna(x):
            return np.nan
        try:
            parts = str(x).split(":")
            if len(parts) == 3:
                h, m, s = map(int, parts)
                return h * 3600 + m * 60 + s
            return np.nan
        except Exception:
            return np.nan

    @staticmethod
    def _safe_series(df, col, default=0):
        if col in df.columns:
            return df[col]
        return pd.Series(default, index=df.index)

    def clean_data(self):
        if self.df is None:
            raise ValueError("Data not loaded. Call load_data() first.")

        self.log("Cleaning data and deriving helper columns...")
        df = self.df.copy()

        # Standardize selected categorical/string-like columns as strings
        for col in ["p1_score", "p2_score", "winner_shot_type", "serve_width", "serve_depth", "return_depth"]:
            if col in df.columns:
                df[col] = df[col].astype(str).replace({"nan": np.nan, "None": np.nan})

        # Parse elapsed time
        if "elapsed_time" in df.columns:
            df["elapsed_seconds"] = df["elapsed_time"].apply(self.parse_elapsed_time_to_seconds)
        else:
            df["elapsed_seconds"] = np.nan

        # Numeric coercions
        numeric_cols = [
            "set_no", "game_no", "point_no", "p1_sets", "p2_sets", "p1_games", "p2_games",
            "server", "serve_no", "point_victor", "p1_points_won", "p2_points_won", "game_victor",
            "set_victor", "p1_ace", "p2_ace", "p1_winner", "p2_winner", "p1_double_fault",
            "p2_double_fault", "p1_unf_err", "p2_unf_err", "p1_net_pt", "p2_net_pt",
            "p1_net_pt_won", "p2_net_pt_won", "p1_break_pt", "p2_break_pt", "p1_break_pt_won",
            "p2_break_pt_won", "p1_break_pt_missed", "p2_break_pt_missed", "p1_distance_run",
            "p2_distance_run", "rally_count", "speed_mph"
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Point identifier
        required_id_cols = ["match_id", "set_no", "game_no", "point_no"]
        if all(c in df.columns for c in required_id_cols):
            df["point_uid"] = (
                df["match_id"].astype(str) + "_S" + df["set_no"].astype("Int64").astype(str) +
                "_G" + df["game_no"].astype("Int64").astype(str) +
                "_P" + df["point_no"].astype("Int64").astype(str)
            )
        else:
            df["point_uid"] = np.arange(len(df)).astype(str)

        # Determine match winner more robustly from set_victor events if possible.
        # Previous code used .reset_index(name=...) after groupby.apply(...) which can
        # produce scalar/float artifacts instead of a clean dict in some pandas versions.
        if "set_victor" in df.columns and "match_id" in df.columns:
            valid_set_victor = df["set_victor"].isin([1, 2])
            if valid_set_victor.any():
                set_counts = (
                    df.loc[valid_set_victor]
                    .groupby(["match_id", "set_victor"])
                    .size()
                    .unstack(fill_value=0)
                )
                if 1 not in set_counts.columns:
                    set_counts[1] = 0
                if 2 not in set_counts.columns:
                    set_counts[2] = 0
                set_counts = set_counts.reset_index()

                set_counts["match_winner_flag"] = np.select(
                    [set_counts[1] > set_counts[2], set_counts[2] > set_counts[1]],
                    [1, 2],
                    default=0
                ).astype(int)

                df = df.merge(
                    set_counts[["match_id", "match_winner_flag"]],
                    on="match_id",
                    how="left"
                )
            else:
                df["match_winner_flag"] = np.nan
        else:
            df["match_winner_flag"] = np.nan

        # Convenience columns
        if "game_no" in df.columns:
            df["is_tiebreak_like"] = (df["game_no"].fillna(0) >= 13).astype(int)
        else:
            df["is_tiebreak_like"] = 0

        if "serve_no" in df.columns:
            df["is_second_serve"] = (df["serve_no"] == 2).astype(int)
        else:
            df["is_second_serve"] = 0

        if {"p1_break_pt", "p2_break_pt"}.issubset(df.columns):
            df["is_break_point_any"] = (
                ((df["p1_break_pt"].fillna(0) == 1) | (df["p2_break_pt"].fillna(0) == 1)).astype(int)
            )
        else:
            df["is_break_point_any"] = 0

        if "p1_ace" in df.columns and "p2_ace" in df.columns:
            df["any_ace"] = (
                ((df["p1_ace"].fillna(0) == 1) | (df["p2_ace"].fillna(0) == 1)).astype(int)
            )
        else:
            df["any_ace"] = 0

        if "p1_double_fault" in df.columns and "p2_double_fault" in df.columns:
            df["any_double_fault"] = (
                ((df["p1_double_fault"].fillna(0) == 1) | (df["p2_double_fault"].fillna(0) == 1)).astype(int)
            )
        else:
            df["any_double_fault"] = 0

        if "p1_winner" in df.columns and "p2_winner" in df.columns:
            df["any_winner"] = (
                ((df["p1_winner"].fillna(0) == 1) | (df["p2_winner"].fillna(0) == 1)).astype(int)
            )
        else:
            df["any_winner"] = 0

        if "p1_unf_err" in df.columns and "p2_unf_err" in df.columns:
            df["any_unforced_error"] = (
                ((df["p1_unf_err"].fillna(0) == 1) | (df["p2_unf_err"].fillna(0) == 1)).astype(int)
            )
        else:
            df["any_unforced_error"] = 0

        self.df_clean = df
        out_path = os.path.join(self.out_dir, "wimbledon_points_cleaned.csv")
        df.to_csv(out_path, index=False)
        self.log(f"Saved cleaned point-level data to: {out_path}")
        return df

    def build_match_summary(self):
        if self.df_clean is None:
            raise ValueError("Cleaned data not available. Call clean_data() first.")

        self.log("Building match-level summary...")
        df = self.df_clean.copy()

        agg_dict = {}
        if "point_uid" in df.columns:
            agg_dict["point_uid"] = "count"
        if "elapsed_seconds" in df.columns:
            agg_dict["elapsed_seconds"] = "max"
        if "set_no" in df.columns:
            agg_dict["set_no"] = "max"
        if "game_no" in df.columns:
            agg_dict["game_no"] = "max"
        if "rally_count" in df.columns:
            agg_dict["rally_count"] = ["mean", "max"]
        if "speed_mph" in df.columns:
            agg_dict["speed_mph"] = ["mean", "max"]

        optional_sum_cols = [
            "p1_ace", "p2_ace", "p1_winner", "p2_winner", "p1_double_fault",
            "p2_double_fault", "p1_unf_err", "p2_unf_err", "p1_break_pt",
            "p2_break_pt", "p1_break_pt_won", "p2_break_pt_won",
            "is_break_point_any", "is_tiebreak_like"
        ]
        optional_mean_cols = ["p1_distance_run", "p2_distance_run", "is_second_serve"]

        for col in optional_sum_cols:
            if col in df.columns:
                agg_dict[col] = "sum"
        for col in optional_mean_cols:
            if col in df.columns:
                agg_dict[col] = "mean"

        group_cols = [c for c in ["match_id", "player1", "player2"] if c in df.columns]
        if len(group_cols) < 3:
            raise ValueError("Required columns for match summary are missing: match_id, player1, player2")

        summary = df.groupby(group_cols).agg(agg_dict)
        summary.columns = [
            "_".join([str(c) for c in col if c != ""]).rstrip("_") if isinstance(col, tuple) else col
            for col in summary.columns.to_flat_index()
        ]
        summary = summary.reset_index()

        # Rename key fields only if present
        summary = summary.rename(columns={
            "point_uid_count": "total_points",
            "elapsed_seconds_max": "match_duration_seconds",
            "set_no_max": "total_sets_observed",
            "game_no_max": "max_game_no_seen",
            "rally_count_mean": "avg_rally_count",
            "rally_count_max": "max_rally_count",
            "speed_mph_mean": "avg_serve_speed_mph",
            "speed_mph_max": "max_serve_speed_mph",
            "p1_distance_run_mean": "p1_avg_distance_run",
            "p2_distance_run_mean": "p2_avg_distance_run",
            "is_break_point_any_sum": "total_break_point_points",
            "is_second_serve_mean": "second_serve_rate",
            "is_tiebreak_like_sum": "tiebreak_like_points"
        })

        # Winner labels
        if "match_winner_flag" in df.columns:
            winners = df.groupby("match_id")["match_winner_flag"].max().reset_index()
            summary = summary.merge(winners, on="match_id", how="left")

            def winner_name(row):
                flag = row.get("match_winner_flag", np.nan)
                if flag == 1:
                    return row.get("player1", "Unknown")
                if flag == 2:
                    return row.get("player2", "Unknown")
                return "Unknown"

            summary["match_winner_name"] = summary.apply(winner_name, axis=1)

        if "match_duration_seconds" in summary.columns:
            summary["match_duration_hms"] = summary["match_duration_seconds"].apply(
                lambda x: str(timedelta(seconds=int(x))) if pd.notna(x) else None
            )
        else:
            summary["match_duration_hms"] = None

        # Final score estimation from set_victor counts
        if "set_victor" in df.columns:
            valid = df["set_victor"].isin([1, 2])
            if valid.any():
                set_counts = (
                    df.loc[valid]
                    .groupby(["match_id", "set_victor"])
                    .size()
                    .unstack(fill_value=0)
                    .reset_index()
                )
                if 1 not in set_counts.columns:
                    set_counts[1] = 0
                if 2 not in set_counts.columns:
                    set_counts[2] = 0
                set_counts = set_counts.rename(columns={1: "p1_sets_won", 2: "p2_sets_won"})
                summary = summary.merge(set_counts, on="match_id", how="left")
                summary["set_score"] = (
                    summary["p1_sets_won"].fillna(0).astype(int).astype(str) + "-" +
                    summary["p2_sets_won"].fillna(0).astype(int).astype(str)
                )

        sort_cols = [c for c in ["match_duration_seconds", "total_points"] if c in summary.columns]
        ascending = [False] * len(sort_cols)
        if sort_cols:
            self.match_summary = summary.sort_values(sort_cols, ascending=ascending)
        else:
            self.match_summary = summary.copy()

        out_path = os.path.join(self.out_dir, "wimbledon_match_summary.csv")
        self.match_summary.to_csv(out_path, index=False)
        self.log(f"Saved match-level summary to: {out_path}")
        return self.match_summary

    def build_player_summary(self):
        if self.df_clean is None:
            raise ValueError("Cleaned data not available. Call clean_data() first.")

        self.log("Building player-level summary...")
        df = self.df_clean.copy()

        required_cols = {"match_id", "player1", "player2"}
        if not required_cols.issubset(df.columns):
            raise ValueError("Required columns for player summary are missing.")

        p1_agg = {
            "matches": ("match_id", "nunique"),
        }
        p2_agg = {
            "matches": ("match_id", "nunique"),
        }

        p1_map = {
            "aces": "p1_ace",
            "winners": "p1_winner",
            "double_faults": "p1_double_fault",
            "unforced_errors": "p1_unf_err",
            "avg_distance_run": "p1_distance_run",
            "break_points": "p1_break_pt",
            "break_points_won": "p1_break_pt_won",
            "points_won_final": "p1_points_won"
        }
        p2_map = {
            "aces": "p2_ace",
            "winners": "p2_winner",
            "double_faults": "p2_double_fault",
            "unforced_errors": "p2_unf_err",
            "avg_distance_run": "p2_distance_run",
            "break_points": "p2_break_pt",
            "break_points_won": "p2_break_pt_won",
            "points_won_final": "p2_points_won"
        }

        for out_col, src_col in p1_map.items():
            if src_col in df.columns:
                p1_agg[out_col] = (src_col, "mean" if out_col == "avg_distance_run" else "sum")
        for out_col, src_col in p2_map.items():
            if src_col in df.columns:
                p2_agg[out_col] = (src_col, "mean" if out_col == "avg_distance_run" else "sum")

        p1 = df.groupby("player1").agg(**p1_agg).reset_index().rename(columns={"player1": "player"})
        p2 = df.groupby("player2").agg(**p2_agg).reset_index().rename(columns={"player2": "player"})

        player_summary = pd.concat([p1, p2], ignore_index=True)
        player_summary = player_summary.groupby("player", as_index=False).sum(numeric_only=True)

        # Estimate wins from match summaries if available
        if self.match_summary is not None and "match_winner_name" in self.match_summary.columns:
            win_counts = self.match_summary["match_winner_name"].value_counts(dropna=False).reset_index()
            win_counts.columns = ["player", "wins"]
            player_summary = player_summary.merge(win_counts, on="player", how="left")
            player_summary["wins"] = player_summary["wins"].fillna(0).astype(int)
        else:
            player_summary["wins"] = 0

        if {"break_points", "break_points_won"}.issubset(player_summary.columns):
            player_summary["break_point_conversion_rate"] = np.where(
                player_summary["break_points"] > 0,
                player_summary["break_points_won"] / player_summary["break_points"],
                np.nan
            )
        else:
            player_summary["break_point_conversion_rate"] = np.nan

        if {"matches", "aces"}.issubset(player_summary.columns):
            player_summary["ace_rate_per_match"] = np.where(
                player_summary["matches"] > 0,
                player_summary["aces"] / player_summary["matches"],
                np.nan
            )
        else:
            player_summary["ace_rate_per_match"] = np.nan

        sort_cols = [c for c in ["wins", "aces", "winners"] if c in player_summary.columns]
        ascending = [False] * len(sort_cols)
        if sort_cols:
            self.player_summary = player_summary.sort_values(sort_cols, ascending=ascending)
        else:
            self.player_summary = player_summary.copy()

        out_path = os.path.join(self.out_dir, "wimbledon_player_summary.csv")
        self.player_summary.to_csv(out_path, index=False)
        self.log(f"Saved player-level summary to: {out_path}")
        return self.player_summary

    def build_rate_limit_proxy_analysis(self):
        """
        Since the task description centers on token/rate-limit issues but the available
        dataset is tennis data, this method builds an analogous 'workload budget' proxy
        using match complexity features. This gives other agents a reusable structured file.
        """
        if self.match_summary is None:
            raise ValueError("Match summary not available. Call build_match_summary() first.")

        self.log("Building workload / rate-limit proxy analysis...")

        df = self.match_summary.copy()

        # Proxy complexity score based on duration, points, rallies, and break-point pressure
        for col in ["match_duration_seconds", "total_points", "avg_rally_count", "total_break_point_points"]:
            if col not in df.columns:
                df[col] = 0

        # Normalize robustly
        def robust_norm(series):
            s = pd.to_numeric(series, errors="coerce").fillna(0)
            lo, hi = s.quantile(0.05), s.quantile(0.95)
            if pd.isna(lo) or pd.isna(hi) or hi - lo <= 1e-12:
                return pd.Series(np.zeros(len(s)), index=s.index)
            return ((s.clip(lo, hi) - lo) / (hi - lo)).fillna(0)

        df["norm_duration"] = robust_norm(df["match_duration_seconds"])
        df["norm_points"] = robust_norm(df["total_points"])
        df["norm_rally"] = robust_norm(df["avg_rally_count"])
        df["norm_break_pressure"] = robust_norm(df["total_break_point_points"])

        df["complexity_score"] = (
            0.35 * df["norm_duration"] +
            0.35 * df["norm_points"] +
            0.20 * df["norm_rally"] +
            0.10 * df["norm_break_pressure"]
        )

        # Convert complexity score into pseudo token estimates
        base_tokens = 300
        df["estimated_prompt_tokens"] = (base_tokens + 900 * df["complexity_score"]).round().astype(int)
        df["estimated_completion_tokens"] = (200 + 700 * df["complexity_score"]).round().astype(int)
        df["estimated_total_tokens"] = df["estimated_prompt_tokens"] + df["estimated_completion_tokens"]

        # Feasibility under reported 5000-token cap
        cap = 5000
        df["fraction_of_5000_cap"] = df["estimated_total_tokens"] / cap
        df["fits_single_request_under_5000"] = df["estimated_total_tokens"] <= cap

        # Rank matches by complexity
        df = df.sort_values("estimated_total_tokens", ascending=False).reset_index(drop=True)
        df["complexity_rank"] = np.arange(1, len(df) + 1)

        self.rate_limit_proxy = df
        out_csv = os.path.join(self.out_dir, "wimbledon_rate_limit_proxy.csv")
        df.to_csv(out_csv, index=False)
        self.log(f"Saved workload proxy analysis to: {out_csv}")

        # Save compact JSON summary
        summary = {
            "dataset_rows": int(len(self.df_clean)) if self.df_clean is not None else None,
            "dataset_matches": int(self.df_clean["match_id"].nunique()) if self.df_clean is not None and "match_id" in self.df_clean.columns else None,
            "most_complex_match": (
                df.loc[0, [c for c in ["match_id", "player1", "player2", "estimated_total_tokens"] if c in df.columns]].to_dict()
                if len(df) > 0 else None
            ),
            "mean_estimated_total_tokens": float(df["estimated_total_tokens"].mean()) if len(df) > 0 else None,
            "max_estimated_total_tokens": int(df["estimated_total_tokens"].max()) if len(df) > 0 else None,
            "min_estimated_total_tokens": int(df["estimated_total_tokens"].min()) if len(df) > 0 else None,
            "all_matches_fit_under_5000": bool(df["fits_single_request_under_5000"].all()) if len(df) > 0 else None,
            "reported_error_context": {
                "limit_type": "tokens",
                "current_limit": 5000,
                "interpretation": "Any reasoning workload estimated above this threshold should be chunked or summarized."
            }
        }
        out_json = os.path.join(self.out_dir, "wimbledon_rate_limit_proxy_summary.json")
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        self.log(f"Saved proxy summary JSON to: {out_json}")
        return df, summary

    def build_point_pressure_segments(self):
        """
        Create a reusable point-level segmentation file highlighting high-pressure points.
        """
        if self.df_clean is None:
            raise ValueError("Cleaned data not available. Call clean_data() first.")

        self.log("Building high-pressure point segmentation...")
        df = self.df_clean.copy()

        p1_score = self._safe_series(df, "p1_score", "").astype(str)
        p2_score = self._safe_series(df, "p2_score", "").astype(str)
        rally_count = pd.to_numeric(self._safe_series(df, "rally_count", 0), errors="coerce").fillna(0)
        is_break_point_any = pd.to_numeric(self._safe_series(df, "is_break_point_any", 0), errors="coerce").fillna(0)
        is_tiebreak_like = pd.to_numeric(self._safe_series(df, "is_tiebreak_like", 0), errors="coerce").fillna(0)

        df["pressure_score"] = (
            2.0 * is_break_point_any +
            1.0 * ((p1_score == "40") | (p2_score == "40")).astype(int) +
            0.75 * is_tiebreak_like +
            0.10 * rally_count
        )

        bins = [-np.inf, 0.5, 1.5, 3.0, np.inf]
        labels = ["low", "medium", "high", "extreme"]
        df["pressure_band"] = pd.cut(df["pressure_score"], bins=bins, labels=labels)

        output_cols = [
            "point_uid", "match_id", "player1", "player2", "set_no", "game_no", "point_no",
            "elapsed_time", "rally_count", "p1_score", "p2_score", "p1_break_pt", "p2_break_pt",
            "is_tiebreak_like", "pressure_score", "pressure_band", "point_victor"
        ]
        existing_cols = [c for c in output_cols if c in df.columns]

        out_path = os.path.join(self.out_dir, "wimbledon_point_pressure_segments.csv")
        df[existing_cols].to_csv(out_path, index=False)
        self.log(f"Saved point pressure segmentation to: {out_path}")
        return df

    def generate_text_report(self):
        if self.match_summary is None or self.player_summary is None or self.rate_limit_proxy is None:
            raise ValueError("Required summaries missing. Build summaries first.")

        self.log("Generating detailed text report...")

        lines = []
        lines.append("Wimbledon 2023 Featured Matches Analysis Report")
        lines.append("=" * 60)
        lines.append(f"Total point rows: {len(self.df_clean):,}")
        lines.append(
            f"Total matches: {self.df_clean['match_id'].nunique():,}"
            if "match_id" in self.df_clean.columns else "Total matches: N/A"
        )

        if {"player1", "player2"}.issubset(self.df_clean.columns):
            total_players = len(set(self.df_clean["player1"]).union(set(self.df_clean["player2"])))
            lines.append(f"Total players: {total_players:,}")
        else:
            lines.append("Total players: N/A")
        lines.append("")

        lines.append("Top 5 longest/most complex matches:")
        top_matches = self.rate_limit_proxy.head(5)
        for _, row in top_matches.iterrows():
            lines.append(
                f"- {row.get('match_id', 'NA')}: {row.get('player1', 'NA')} vs {row.get('player2', 'NA')} | "
                f"duration={row.get('match_duration_hms', 'NA')} | "
                f"points={int(row.get('total_points', 0)) if pd.notna(row.get('total_points', 0)) else 0} | "
                f"est_tokens={int(row.get('estimated_total_tokens', 0)) if pd.notna(row.get('estimated_total_tokens', 0)) else 0}"
            )
        lines.append("")

        lines.append("Top 10 players by wins / aces / winners:")
        for _, row in self.player_summary.head(10).iterrows():
            break_conv = row.get("break_point_conversion_rate", np.nan)
            lines.append(
                f"- {row.get('player', 'NA')}: wins={int(row.get('wins', 0)) if pd.notna(row.get('wins', 0)) else 0}, "
                f"matches={int(row.get('matches', 0)) if pd.notna(row.get('matches', 0)) else 0}, "
                f"aces={int(row.get('aces', 0)) if pd.notna(row.get('aces', 0)) else 0}, "
                f"winners={int(row.get('winners', 0)) if pd.notna(row.get('winners', 0)) else 0}, "
                f"break_conv={round(float(break_conv), 3) if pd.notna(break_conv) else 'NA'}"
            )
        lines.append("")

        lines.append("Rate-limit proxy interpretation:")
        mean_tokens = self.rate_limit_proxy["estimated_total_tokens"].mean()
        max_tokens = self.rate_limit_proxy["estimated_total_tokens"].max()
        lines.append(f"- Average estimated per-match token load: {mean_tokens:.1f}")
        lines.append(f"- Maximum estimated per-match token load: {int(max_tokens)}")
        lines.append("- Under the proxy model, all matches are checked against a 5000-token cap.")
        fits_count = int(self.rate_limit_proxy["fits_single_request_under_5000"].sum())
        total_matches = len(self.rate_limit_proxy)
        lines.append(f"- Matches fitting under 5000-token cap: {fits_count}/{total_matches}")
        lines.append("- Operational guidance: for large-context analysis, summarize or chunk the most complex matches first.")
        lines.append("")

        report_path = os.path.join(self.out_dir, "wimbledon_analysis_report.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self.log(f"Saved detailed text report to: {report_path}")

        print("\n".join(lines))
        return report_path


# The function to complete the current Task
def task1():
    model = Model1(csv_path="Wimbledon_featured_matches.csv", out_dir="outputs")

    try:
        model.load_data()
        model.clean_data()
        match_summary = model.build_match_summary()
        player_summary = model.build_player_summary()
        rate_limit_proxy, proxy_summary = model.build_rate_limit_proxy_analysis()
        pressure_df = model.build_point_pressure_segments()
        model.generate_text_report()

        # Additional console outputs for transparency
        print("\n[task1] Preview: match summary")
        print(match_summary.head(10).to_string(index=False))

        print("\n[task1] Preview: player summary")
        print(player_summary.head(10).to_string(index=False))

        print("\n[task1] Preview: rate limit proxy")
        preview_cols = [
            "complexity_rank", "match_id", "player1", "player2",
            "total_points", "match_duration_hms", "estimated_total_tokens",
            "fraction_of_5000_cap", "fits_single_request_under_5000"
        ]
        existing_preview_cols = [c for c in preview_cols if c in rate_limit_proxy.columns]
        print(rate_limit_proxy[existing_preview_cols].head(10).to_string(index=False))

        print("\n[task1] Proxy summary JSON:")
        print(json.dumps(proxy_summary, indent=2))

        # Save a compact meta manifest for other agents
        manifest = {
            "generated_files": [
                os.path.join("outputs", "wimbledon_points_cleaned.csv"),
                os.path.join("outputs", "wimbledon_match_summary.csv"),
                os.path.join("outputs", "wimbledon_player_summary.csv"),
                os.path.join("outputs", "wimbledon_rate_limit_proxy.csv"),
                os.path.join("outputs", "wimbledon_rate_limit_proxy_summary.json"),
                os.path.join("outputs", "wimbledon_point_pressure_segments.csv"),
                os.path.join("outputs", "wimbledon_analysis_report.txt"),
            ],
            "row_counts": {
                "clean_points": int(len(model.df_clean)),
                "matches": int(len(match_summary)),
                "players": int(len(player_summary)),
                "pressure_points": int(len(pressure_df))
            },
            "status": "success"
        }
        manifest_path = os.path.join("outputs", "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        print(f"\n[task1] Saved manifest to: {manifest_path}")
        print("[task1] Task completed successfully.")

    except Exception as e:
        error_payload = {
            "status": "failure",
            "error_type": type(e).__name__,
            "error_message": str(e)
        }
        os.makedirs("outputs", exist_ok=True)
        error_path = os.path.join("outputs", "error_log.json")
        with open(error_path, "w", encoding="utf-8") as f:
            json.dump(error_payload, f, indent=2)
        print(f"[task1] ERROR: {type(e).__name__}: {e}")
        print(f"[task1] Error details saved to: {error_path}")
        raise


if __name__ == '__main__':
    # complete task
    task1()