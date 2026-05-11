"""
特徴量エンジニアリング

races / race_entries / race_previews / race_results を結合し、
学習・推論に使える DataFrame を生成する。

設計のポイント:
  - 「予測時点で利用可能なデータ」のみを特徴量化する
  - 直前情報を使う場合は predict_phase='before_race' を指定
  - 確定オッズ等を使う場合は predict_phase='after_close' を指定
  - 過去データから時系列特徴量 (直近5走の1着率など) を生成
"""
from __future__ import annotations

import os
import sqlite3
from typing import Literal
import pandas as pd

import config
from src.db.connection import connect as db_connect


PredictPhase = Literal["before_day", "before_race", "after_close"]


# ============================================================
# 基本データ取得
# ============================================================

def load_base_dataframe(
    db_path: str = None,
    date_from: str = None,
    date_to: str = None,
) -> pd.DataFrame:
    """
    races x race_entries x race_previews x race_results を結合した
    1艇=1行の生 DataFrame を返す。
    """
    where = ""
    params: list = []
    # DATABASE_URL があれば Postgres、無ければ SQLite を使う
    use_postgres = bool(os.getenv("DATABASE_URL", "").strip())
    placeholder = "%s" if use_postgres else "?"
    if date_from:
        where += f" AND r.race_date >= {placeholder}"
        params.append(date_from)
    if date_to:
        where += f" AND r.race_date <= {placeholder}"
        params.append(date_to)

    sql = f"""
    SELECT
        r.race_id, r.race_date, r.stadium_number, r.race_number,
        r.race_grade_number, r.race_distance,

        e.boat_number, e.racer_number, e.class_number,
        e.age, e.weight, e.flying_count, e.late_count,
        e.avg_start_timing,
        e.national_top_1_percent, e.national_top_2_percent, e.national_top_3_percent,
        e.local_top_1_percent, e.local_top_2_percent, e.local_top_3_percent,
        e.assigned_motor_number,
        e.assigned_motor_top_2_percent, e.assigned_motor_top_3_percent,
        e.assigned_boat_top_2_percent, e.assigned_boat_top_3_percent,

        p.weather_number, p.wind_speed, p.wind_direction_number,
        p.wave_height, p.temperature, p.water_temperature,
        p.course_number AS exhibition_course,
        p.exhibition_time, p.start_timing_exhibition,
        p.weight_adjustment, p.tilt_adjustment,

        s.water, s.is_night, s.in_strength, s.tide_effect, s.altitude_high,

        res.finishing_position, res.start_timing AS actual_start_timing,
        res.kimarite

    FROM races r
    JOIN race_entries  e ON r.race_id = e.race_id
    LEFT JOIN race_previews p ON r.race_id = p.race_id AND e.boat_number = p.boat_number
    LEFT JOIN race_results  res ON r.race_id = res.race_id AND e.boat_number = res.boat_number
    LEFT JOIN stadiums      s ON r.stadium_number = s.stadium_number
    WHERE 1=1 {where}
    ORDER BY r.race_date, r.stadium_number, r.race_number, e.boat_number
    """

    if use_postgres:
        # psycopg3 経由 (pandas は raw connection を受け付ける)
        import psycopg
        dsn = os.getenv("DATABASE_URL", "").strip()
        if dsn.startswith("postgres://"):
            dsn = "postgresql://" + dsn[len("postgres://"):]
        if "sslmode=" not in dsn:
            dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
        with psycopg.connect(dsn, autocommit=True) as conn:
            # Supabase Free tmp 領域節約
            try:
                cur = conn.cursor()
                cur.execute("SET max_parallel_workers_per_gather = 0")
                cur.execute("SET work_mem = '4MB'")
                cur.close()
            except Exception:
                pass
            df = pd.read_sql_query(sql, conn, params=tuple(params))
    else:
        path = db_path or config.DB_PATH
        with sqlite3.connect(path) as conn:
            df = pd.read_sql_query(sql, conn, params=params)
    df["race_date"] = pd.to_datetime(df["race_date"])
    return df


# ============================================================
# 派生特徴量
# ============================================================

def add_recent_form_features(df: pd.DataFrame, n_recent: int = 10) -> pd.DataFrame:
    """
    各選手の「直近n走」のフォーム特徴量を追加。
    リーク防止のため shift(1) を必ず入れること（自レースを含めない）。

    [v0.7 拡張] n_recent=10 の他に 30/50 走の長期 form も追加。
    "選手 alpha" (持続性 r=0.334) を捕捉するための長窓特徴。
    """
    df = df.sort_values(["racer_number", "race_date", "race_number"]).copy()
    df["is_first"] = (df["finishing_position"] == 1).astype("Int64")
    df["is_top2"] = (df["finishing_position"] <= 2).astype("Int64")
    df["is_top3"] = (df["finishing_position"] <= 3).astype("Int64")

    grp = df.groupby("racer_number", group_keys=False)
    for col, name in [("is_first", "first_rate"), ("is_top2", "top2_rate"), ("is_top3", "top3_rate")]:
        df[f"recent_{n_recent}_{name}"] = (
            grp[col]
            .apply(lambda s: s.shift(1).rolling(n_recent, min_periods=1).mean())
        )

    df[f"recent_{n_recent}_avg_st"] = (
        grp["actual_start_timing"]
        .apply(lambda s: s.shift(1).rolling(n_recent, min_periods=1).mean())
    )

    # [v0.7] 長窓フォーム
    for n in (30, 50):
        df[f"recent_{n}_first_rate"] = (
            grp["is_first"].apply(lambda s: s.shift(1).rolling(n, min_periods=5).mean())
        )
    df["recent_30_top2_rate"] = (
        grp["is_top2"].apply(lambda s: s.shift(1).rolling(30, min_periods=5).mean())
    )

    # [v0.7] 全国通算 1着率 (national_top_1_percent は 0-100 表記) との乖離
    # = 直近の調子 - キャリア平均 → "選手 alpha (real signal)" を直接捕捉
    if "national_top_1_percent" in df.columns:
        baseline = df["national_top_1_percent"] / 100.0
        df["recent_30_first_rate_vs_national"] = df["recent_30_first_rate"] - baseline
        df["recent_50_first_rate_vs_national"] = df["recent_50_first_rate"] - baseline
    return df


def add_relative_features(df: pd.DataFrame) -> pd.DataFrame:
    """同レース内6艇の相対特徴量"""
    grp = df.groupby("race_id")
    for col in ["national_top_2_percent", "assigned_motor_top_2_percent", "exhibition_time"]:
        if col in df.columns:
            df[f"{col}_rank_in_race"] = grp[col].rank(ascending=False, method="min")
            df[f"{col}_diff_from_mean"] = df[col] - grp[col].transform("mean")
    return df


def add_stadium_course_features(df: pd.DataFrame) -> pd.DataFrame:
    """会場×コースの基礎フラグ"""
    df["is_course_changed"] = (df["boat_number"] != df["exhibition_course"]).astype("Int64")
    df["is_inner_course"] = (df["boat_number"] <= 2).astype("Int64")
    return df


def add_stadium_racer_form(df: pd.DataFrame, n_recent: int = 20) -> pd.DataFrame:
    """
    [特徴1a] 各選手 × 各会場 の直近 n_recent 走のフォーム。
    全国通算ではなく会場特化の調子を表現する。

    [v0.8 拡張] 100走の長期版も追加 (= 会場特化スペシャリスト指標)。
    """
    df = df.sort_values(["racer_number", "stadium_number", "race_date", "race_number"]).copy()
    if "is_first" not in df.columns:
        df["is_first"] = (df["finishing_position"] == 1).astype("Int64")
    if "is_top2" not in df.columns:
        df["is_top2"] = (df["finishing_position"] <= 2).astype("Int64")

    grp = df.groupby(["racer_number", "stadium_number"], group_keys=False)
    df[f"stadium_recent_{n_recent}_first_rate"] = (
        grp["is_first"].apply(lambda s: s.shift(1).rolling(n_recent, min_periods=3).mean())
    )
    df[f"stadium_recent_{n_recent}_top2_rate"] = (
        grp["is_top2"].apply(lambda s: s.shift(1).rolling(n_recent, min_periods=3).mean())
    )

    # [v0.8] 100走長期 (= 会場のスペシャリスト指標)
    df["stadium_lt_100_first_rate"] = (
        grp["is_first"].apply(lambda s: s.shift(1).rolling(100, min_periods=10).mean())
    )
    df["stadium_lt_100_top2_rate"] = (
        grp["is_top2"].apply(lambda s: s.shift(1).rolling(100, min_periods=10).mean())
    )
    return df


def add_course_racer_form(df: pd.DataFrame, n_recent: int = 30) -> pd.DataFrame:
    """
    [特徴1b] 各選手 × 進入コース の直近 n_recent 走の勝率/2連率。
    枠なり進入が崩れた時の補正にも効く (進入コース別)。
    course_number は race_previews 由来の進入コース。
    """
    if "course_number" not in df.columns and "exhibition_course" not in df.columns:
        return df
    course_col = "course_number" if "course_number" in df.columns else "exhibition_course"
    has_course = df[course_col].notna()
    if not has_course.any():
        return df

    df = df.sort_values(["racer_number", course_col, "race_date", "race_number"]).copy()
    if "is_first" not in df.columns:
        df["is_first"] = (df["finishing_position"] == 1).astype("Int64")
    if "is_top2" not in df.columns:
        df["is_top2"] = (df["finishing_position"] <= 2).astype("Int64")

    grp = df.groupby(["racer_number", course_col], group_keys=False)
    df[f"course_recent_{n_recent}_first_rate"] = (
        grp["is_first"].apply(lambda s: s.shift(1).rolling(n_recent, min_periods=3).mean())
    )
    df[f"course_recent_{n_recent}_top2_rate"] = (
        grp["is_top2"].apply(lambda s: s.shift(1).rolling(n_recent, min_periods=3).mean())
    )
    # [v0.8] 100走長期 (= コーススペシャリスト指標, persistence r=0.247)
    df["course_lt_100_first_rate"] = (
        grp["is_first"].apply(lambda s: s.shift(1).rolling(100, min_periods=10).mean())
    )
    df["course_lt_100_top2_rate"] = (
        grp["is_top2"].apply(lambda s: s.shift(1).rolling(100, min_periods=10).mean())
    )
    return df


def add_motor_long_term_features(df: pd.DataFrame, n_recent: int = 50) -> pd.DataFrame:
    """
    [特徴6] モーター単位の長期成績 (公式の節時点 2連率より広い窓で集計)。
    モーターは会場×モーター番号で一意とみなす (実運用上ほぼ正しい)。
    """
    if "assigned_motor_number" not in df.columns:
        return df
    df = df.sort_values(["stadium_number", "assigned_motor_number", "race_date", "race_number"]).copy()
    if "is_first" not in df.columns:
        df["is_first"] = (df["finishing_position"] == 1).astype("Int64")
    if "is_top2" not in df.columns:
        df["is_top2"] = (df["finishing_position"] <= 2).astype("Int64")
    if "is_top3" not in df.columns:
        df["is_top3"] = (df["finishing_position"] <= 3).astype("Int64")

    grp = df.groupby(["stadium_number", "assigned_motor_number"], group_keys=False)
    df[f"motor_long_{n_recent}_first_rate"] = (
        grp["is_first"].apply(lambda s: s.shift(1).rolling(n_recent, min_periods=5).mean())
    )
    df[f"motor_long_{n_recent}_top2_rate"] = (
        grp["is_top2"].apply(lambda s: s.shift(1).rolling(n_recent, min_periods=5).mean())
    )
    df[f"motor_long_{n_recent}_top3_rate"] = (
        grp["is_top3"].apply(lambda s: s.shift(1).rolling(n_recent, min_periods=5).mean())
    )
    # 公式値とのdiff (直近上振れ/下振れ)
    if "assigned_motor_top_2_percent" in df.columns:
        df["motor_top2_diff_vs_official"] = (
            df[f"motor_long_{n_recent}_top2_rate"] * 100
            - df["assigned_motor_top_2_percent"]
        )
    return df


def add_weather_racer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    [特徴7] 天候・水面の悪条件下での選手別過去成績。
      - 強風 (wind_speed >= 5 m/s)
      - 高波 (wave_height >= 5 cm)
    各選手の過去 expanding 平均を取り、悪条件下の地力を表現。
    リーク防止のため shift(1)。
    """
    if "wind_speed" not in df.columns:
        return df

    df = df.sort_values(["racer_number", "race_date", "race_number"]).copy()
    if "is_first" not in df.columns:
        df["is_first"] = (df["finishing_position"] == 1).astype("Int64")
    if "is_top2" not in df.columns:
        df["is_top2"] = (df["finishing_position"] <= 2).astype("Int64")

    # 悪条件マスク
    is_strong_wind = df["wind_speed"].fillna(0) >= 5
    is_high_wave = df["wave_height"].fillna(0) >= 5

    # 強風時の hit
    df["_wind_first"] = (df["is_first"].astype(float) * is_strong_wind.astype(float))
    df["_wind_n"] = is_strong_wind.astype(float)
    grp = df.groupby("racer_number", group_keys=False)
    df["wind_strong_first_rate"] = (
        (grp["_wind_first"].apply(lambda s: s.shift(1).expanding().sum())
         / grp["_wind_n"].apply(lambda s: s.shift(1).expanding().sum()))
    )

    # 高波時の hit
    df["_wave_first"] = (df["is_first"].astype(float) * is_high_wave.astype(float))
    df["_wave_n"] = is_high_wave.astype(float)
    df["wave_high_first_rate"] = (
        (grp["_wave_first"].apply(lambda s: s.shift(1).expanding().sum())
         / grp["_wave_n"].apply(lambda s: s.shift(1).expanding().sum()))
    )

    # 全条件平均との差 (悪条件で強い/弱いの偏差)
    df["wind_strong_first_rate_diff"] = (
        df["wind_strong_first_rate"]
        - grp["is_first"].apply(lambda s: s.shift(1).expanding().mean())
    )

    df = df.drop(columns=["_wind_first", "_wind_n", "_wave_first", "_wave_n"], errors="ignore")
    return df


# ============================================================
# 学習用 / 推論用 DataFrame
# ============================================================

def _apply_all_features(df: pd.DataFrame) -> pd.DataFrame:
    """全特徴量を順番に適用 (training/inference 共通)"""
    df = add_recent_form_features(df)
    df = add_stadium_racer_form(df)
    df = add_course_racer_form(df)
    df = add_motor_long_term_features(df)
    df = add_weather_racer_features(df)
    df = add_relative_features(df)
    df = add_stadium_course_features(df)
    return df


def build_training_frame(
    db_path: str = None,
    date_from: str = None,
    date_to: str = None,
    phase: PredictPhase = "before_race",
) -> pd.DataFrame:
    """学習用 DataFrame を構築。"""
    df = load_base_dataframe(db_path, date_from, date_to)
    df = _apply_all_features(df)

    # 結果が無い (中止等) レースは学習対象外
    df = df.dropna(subset=["finishing_position"])

    # 各 phase で使えない列を除外する想定 (TODO: 列名を厳密にメタデータ化)
    if phase == "before_day":
        # 直前情報・結果の列を落とす
        cols_to_drop = [c for c in df.columns if c.startswith(("weather_", "wind_", "wave_", "temperature", "water_temperature", "exhibition_", "tilt_", "weight_adjustment"))]
        df = df.drop(columns=cols_to_drop, errors="ignore")

    return df


def build_inference_frame(
    target_date: str,
    db_path: str = None,
    history_days: int = 90,
) -> pd.DataFrame:
    """
    推論用 DataFrame を構築 (target_date のレースに対して予測)。

    - 直近 history_days 日のデータを履歴として読み込み (recent_form 計算用)
    - その後 target_date の行のみ返す (finishing_position が NaN でも残す)
    """
    from datetime import date as _date, timedelta as _td

    target = _date.fromisoformat(target_date)
    date_from = (target - _td(days=history_days)).isoformat()
    date_to = target.isoformat()

    df = load_base_dataframe(db_path, date_from, date_to)
    df = _apply_all_features(df)

    # 対象日のみ抽出 (finishing_position が NaN/未確定でも残す)
    target_ts = pd.Timestamp(target)
    df = df[df["race_date"] == target_ts].reset_index(drop=True)
    return df
