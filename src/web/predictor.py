"""
Web UI 用予測ラッパー

- 学習済み artifact (LightGBM Ranker + 較正器) を 1度ロード
- 日付別に予測 DataFrame と三連単 joint 確率をキャッシュ
- スレッドセーフ
"""
from __future__ import annotations

import logging
import pickle
import threading
from typing import Optional

import pandas as pd

import config
from src.features.builder import build_inference_frame
from src.models.train import predict_probs
from src.models.calibration import apply_calibrators, add_top_k_uncalibrated
from src.models.cascade import load_cascade, predict_trifecta_joint
from src.models.cascade_per_winner import (
    load_per_winner_cascade, predict_trifecta_per_winner,
)

logger = logging.getLogger(__name__)


class Predictor:
    # Render Free (512MB) 対策: キャッシュ上限。超えたら古い順に削除。
    _MAX_DATE_CACHE = 2   # predict_date の DataFrame (1 日 = 数万行)
    _MAX_TRI_CACHE = 200  # predict_trifecta の 120 通り x N レース

    def __init__(self, version: str = config.DEFAULT_MODEL_VERSION,
                 cascade_version: str = "cascade-v0.6",
                 per_winner_version: str = "pw-v0.6"):
        self.version = version
        self.cascade_version = cascade_version
        self.per_winner_version = per_winner_version
        self.artifact: Optional[dict] = None
        self.cascade: Optional[dict] = None
        self.per_winner: Optional[dict] = None
        # 挿入順を保つため通常 dict (Py3.7+ で順序保証)
        self._cache: dict[str, pd.DataFrame] = {}
        self._tri_cache: dict[str, list] = {}
        self._lock = threading.Lock()

    def _evict_cache(self, target: dict, max_size: int):
        """挿入順の古い方から削除して max_size 以内に収める。"""
        while len(target) > max_size:
            oldest_key = next(iter(target))
            target.pop(oldest_key, None)

    def load(self) -> None:
        path = config.MODEL_DIR / f"ranker_{self.version}.pkl"
        if not path.exists():
            raise FileNotFoundError(f"artifact not found: {path}")
        with open(path, "rb") as f:
            self.artifact = pickle.load(f)
        logger.info("loaded artifact %s (features=%d, calibrators=%s)",
                    path, len(self.artifact["feature_cols"]),
                    list((self.artifact.get("calibrators") or {}).keys()))

        try:
            self.cascade = load_cascade(self.cascade_version)
            if self.cascade:
                logger.info("loaded cascade %s", self.cascade_version)
        except Exception as e:
            logger.warning("cascade load failed: %s", e)

        try:
            self.per_winner = load_per_winner_cascade(self.per_winner_version)
            if self.per_winner:
                logger.info("loaded per-winner cascade %s", self.per_winner_version)
        except Exception as e:
            logger.warning("per-winner cascade load failed: %s", e)

    def predict_date(self, target_date: str, force: bool = False) -> pd.DataFrame:
        """
        target_date (YYYY-MM-DD) の全レース×6艇の予測を返す。

        Returns:
          DataFrame with columns:
            race_id, race_date, stadium_number, race_number, boat_number,
            racer_number, racer_name, ...,
            prob_first, prob_top_2, prob_top_3, raw_score
        """
        if self.artifact is None:
            self.load()

        with self._lock:
            if not force and target_date in self._cache:
                return self._cache[target_date]

        df = build_inference_frame(target_date)
        if df.empty:
            with self._lock:
                self._cache[target_date] = df
                self._evict_cache(self._cache, self._MAX_DATE_CACHE)
            return df

        # ranker_<v>.pkl 学習時の元 build_training_frame と列名を揃える
        df_pred = predict_probs(self.artifact["model"], df, self.artifact["feature_cols"])
        if self.artifact.get("calibrators"):
            df_pred = apply_calibrators(df_pred, self.artifact["calibrators"])
        else:
            df_pred = add_top_k_uncalibrated(df_pred)
            df_pred["prob_first"] = df_pred["prob_first_uncalibrated"]
            df_pred["prob_top_2"] = df_pred["prob_top_2_uncalibrated"]
            df_pred["prob_top_3"] = df_pred["prob_top_3_uncalibrated"]

        with self._lock:
            self._cache[target_date] = df_pred
            self._evict_cache(self._cache, self._MAX_DATE_CACHE)
        return df_pred

    def predict_trifecta(self, target_date: str, race_id: str,
                          mode: str = "per_winner") -> Optional[list[tuple[str, float]]]:
        """
        指定レースの三連単 120組合せ確率を返す。降順 sort 済。
        mode: "per_winner" (Top-1 推奨) | "unified" (Top-3 分散推奨) | "plackett"
        """
        cache_key = f"{target_date}::{race_id}::{mode}"
        with self._lock:
            if cache_key in self._tri_cache:
                return self._tri_cache[cache_key]

        df = self.predict_date(target_date)
        if df.empty:
            return None
        race_df = df[df["race_id"] == race_id].copy()
        if race_df.empty or len(race_df) < 6:
            return None

        if mode == "plackett":
            from src.evaluation.value_bet import trifecta_combination_prob
            boat_probs = {
                int(r["boat_number"]): {
                    "prob_first": float(r["prob_first"]),
                    "prob_top_2": float(r["prob_top_2"]),
                    "prob_top_3": float(r["prob_top_3"]),
                }
                for _, r in race_df.iterrows()
            }
            combos = trifecta_combination_prob(boat_probs)
        elif mode == "per_winner" and self.per_winner is not None:
            result = predict_trifecta_per_winner(
                race_df, self.per_winner["s2"], self.per_winner["s3"],
                fallback_s2_model=self.cascade["stage2_model"] if self.cascade else None,
                fallback_s2_features=self.cascade["stage2_features"] if self.cascade else None,
                fallback_s3_model=self.cascade["stage3_model"] if self.cascade else None,
                fallback_s3_features=self.cascade["stage3_features"] if self.cascade else None,
            )
            combos = result.get(race_id, {})
        elif mode == "unified" and self.cascade is not None:
            result = predict_trifecta_joint(
                race_df,
                self.cascade["stage2_model"], self.cascade["stage2_features"],
                self.cascade["stage3_model"], self.cascade["stage3_features"],
                pattern_2nd=self.cascade.get("pattern_2nd"),
                pattern_3rd=self.cascade.get("pattern_3rd"),
            )
            combos = result.get(race_id, {})
        else:
            return None

        sorted_combos = sorted(combos.items(), key=lambda x: -x[1])
        with self._lock:
            self._tri_cache[cache_key] = sorted_combos
            self._evict_cache(self._tri_cache, self._MAX_TRI_CACHE)
        return sorted_combos

    def predict_whatif(self, target_date: str, race_id: str,
                       overrides: dict) -> Optional[dict]:
        """
        What-if 予測: 特徴量を上書きして再予測。

        overrides の形式:
          Race-level (全艇に適用): {"wind_speed": 8, "wave_height": 5, "temperature": 18, ...}
          Per-boat (艇番号別):      {"boat_1.exhibition_time": 6.5, "boat_3.start_timing_exhibition": 0.05, ...}

        Returns:
          {
            "boats": [{boat_number, prob_first, prob_top_2, prob_top_3, ...}, ...],
            "top1_combo": str,
            "top1_prob": float,
            "top_combos": [(combo, prob), ...],
          }
        """
        if self.artifact is None:
            self.load()

        # ベース DataFrame を再構築 (キャッシュは使わない)
        df = build_inference_frame(target_date)
        race_df = df[df["race_id"] == race_id].copy()
        if race_df.empty or len(race_df) < 6:
            return None

        # オーバーライド適用
        for key, value in (overrides or {}).items():
            if value is None or value == "":
                continue
            try:
                v = float(value)
            except (ValueError, TypeError):
                continue
            if "." in key:
                # boat_N.column 形式
                left, col = key.split(".", 1)
                if not left.startswith("boat_"):
                    continue
                try:
                    bn = int(left.split("_")[1])
                except (ValueError, IndexError):
                    continue
                race_df.loc[race_df["boat_number"] == bn, col] = v
            else:
                # レース全体に適用
                race_df[key] = v

        # 予測実行
        df_pred = predict_probs(self.artifact["model"], race_df, self.artifact["feature_cols"])
        if self.artifact.get("calibrators"):
            df_pred = apply_calibrators(df_pred, self.artifact["calibrators"])
        else:
            df_pred = add_top_k_uncalibrated(df_pred)
            df_pred["prob_first"] = df_pred["prob_first_uncalibrated"]
            df_pred["prob_top_2"] = df_pred["prob_top_2_uncalibrated"]
            df_pred["prob_top_3"] = df_pred["prob_top_3_uncalibrated"]

        # 6艇の Stage 1 予測
        boats_out = []
        for _, r in df_pred.sort_values("prob_first", ascending=False).iterrows():
            boats_out.append({
                "boat_number": int(r["boat_number"]),
                "prob_first": float(r["prob_first"]),
                "prob_top_2": float(r["prob_top_2"]),
                "prob_top_3": float(r["prob_top_3"]),
            })

        # 三連単 (PerWinner 優先, fallback unified)
        top_combos: list = []
        try:
            if self.per_winner is not None:
                result = predict_trifecta_per_winner(
                    df_pred, self.per_winner["s2"], self.per_winner["s3"],
                    fallback_s2_model=self.cascade["stage2_model"] if self.cascade else None,
                    fallback_s2_features=self.cascade["stage2_features"] if self.cascade else None,
                    fallback_s3_model=self.cascade["stage3_model"] if self.cascade else None,
                    fallback_s3_features=self.cascade["stage3_features"] if self.cascade else None,
                )
                combos = result.get(race_id, {})
                top_combos = sorted(combos.items(), key=lambda x: -x[1])[:10]
        except Exception as e:
            logger.warning("whatif trifecta failed: %s", e)

        top1_combo = top_combos[0][0] if top_combos else None
        top1_prob = top_combos[0][1] if top_combos else None

        return {
            "boats": boats_out,
            "top1_combo": top1_combo,
            "top1_prob": top1_prob,
            "top_combos": top_combos,
        }

    def warm_trifecta_cache(self, target_date: str, mode: str = "per_winner") -> int:
        """指定日全レースの三連単 joint 確率を **1回の per_winner 呼び出しで** 計算し、
        個別 race_id 用キャッシュにバラして格納する。

        旧実装は ``find_value_bets_for_race`` を 156 回ループする際、
        毎回 ``predict_trifecta_per_winner(df[df.race_id==rid])`` を呼んでいて
        DataFrame 生成 / groupby / model.predict_proba の Python オーバヘッドが重複していた。
        ここで全レースを 1 度の groupby で処理すれば numpy/pandas のベクトル化が効きやすい。

        Returns: ウォームしたレース数。
        """
        df = self.predict_date(target_date)
        if df.empty:
            return 0
        race_ids = df["race_id"].drop_duplicates().tolist()
        # 既に全レースキャッシュ済みならスキップ
        missing = [rid for rid in race_ids
                   if f"{target_date}::{rid}::{mode}" not in self._tri_cache]
        if not missing:
            return 0
        df_missing = df[df["race_id"].isin(missing)].copy()
        if mode == "per_winner" and self.per_winner is not None:
            result = predict_trifecta_per_winner(
                df_missing, self.per_winner["s2"], self.per_winner["s3"],
                fallback_s2_model=self.cascade["stage2_model"] if self.cascade else None,
                fallback_s2_features=self.cascade["stage2_features"] if self.cascade else None,
                fallback_s3_model=self.cascade["stage3_model"] if self.cascade else None,
                fallback_s3_features=self.cascade["stage3_features"] if self.cascade else None,
            )
        elif mode == "unified" and self.cascade is not None:
            result = predict_trifecta_joint(
                df_missing,
                self.cascade["stage2_model"], self.cascade["stage2_features"],
                self.cascade["stage3_model"], self.cascade["stage3_features"],
                pattern_2nd=self.cascade.get("pattern_2nd"),
                pattern_3rd=self.cascade.get("pattern_3rd"),
            )
        else:
            return 0
        n = 0
        with self._lock:
            for rid, combos in result.items():
                sorted_combos = sorted(combos.items(), key=lambda x: -x[1])
                self._tri_cache[f"{target_date}::{rid}::{mode}"] = sorted_combos
                n += 1
            self._evict_cache(self._tri_cache, self._MAX_TRI_CACHE)
        return n

    def find_value_bets_for_race(
        self, target_date: str, race_id: str,
        snapshot_label: str = "T-5min",
        ev_threshold: float = 0.0,
        min_prob: float = 0.005,
        max_odds: float = 500.0,
        odds_lookup: Optional[dict] = None,
        decay_table=None,
    ) -> Optional[dict]:
        """
        指定レース・スナップショットラベルのオッズを使って EV+ 組合せを検出。
        decay_factor (DB) を適用して adjusted_odds で EV を計算。

        Args:
          odds_lookup: ``{combination: odds}`` の事前取得 dict (バッチ呼出側で再利用)。
                       None なら本関数内で DB から取得する。
          decay_table: 事前ロード済の decay_table。None なら load_decay_table() を呼ぶ
                       (内部で 30分キャッシュあり、ホットパスで実害なし)。

        Returns:
          {
            "race_id": ..., "snapshot_label": ...,
            "n_value_bets": int,
            "value_bets": [{combination, prob, odds, adjusted_odds, ev, adj_ev}, ...] (top 10),
            "best_ev": float, "best_combo": str,
          } or None
        """
        from src.analysis.decay_factor import (
            load_decay_table, adjust_odds_with_decay,
        )
        from src.db.connection import connect as db_connect

        # 三連単 joint 確率を取得
        combos = self.predict_trifecta(target_date, race_id, mode="per_winner")
        if not combos:
            return None

        # スナップショット時点のオッズを取得 (事前取得済なら再利用)
        if odds_lookup is None:
            with db_connect() as conn:
                cur = conn.execute(
                    "SELECT combination, odds FROM odds_trifecta "
                    "WHERE race_id = ? AND snapshot_label = ?",
                    (race_id, snapshot_label),
                )
                odds_lookup = {r[0]: float(r[1]) for r in cur.fetchall()}
        if not odds_lookup:
            return None

        if decay_table is None:
            decay_table = load_decay_table()

        # combos は list[(combination, prob)] 形式
        combo_dict = dict(combos)
        rows = []
        for comb, prob in combo_dict.items():
            o = odds_lookup.get(comb)
            if o is None or o < 1.0 or o > max_odds:
                continue
            if prob < min_prob:
                continue
            adj = adjust_odds_with_decay(o, decay_table) if not decay_table.empty else o
            ev = prob * o - 1.0
            adj_ev = prob * adj - 1.0
            if adj_ev >= ev_threshold:
                rows.append({
                    "combination": comb,
                    "prob": float(prob),
                    "odds": float(o),
                    "adjusted_odds": float(adj),
                    "ev": float(ev),
                    "adj_ev": float(adj_ev),
                })

        rows.sort(key=lambda x: -x["adj_ev"])
        best = rows[0] if rows else None
        return {
            "race_id": race_id,
            "snapshot_label": snapshot_label,
            "n_value_bets": len(rows),
            "value_bets": rows[:10],
            "best_ev": best["adj_ev"] if best else None,
            "best_combo": best["combination"] if best else None,
        }

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._tri_cache.clear()
