"""
Flask Web UI: 予測表示

ルート:
  GET /                    → 今日の日付にリダイレクト
  GET /races?date=YYYY-MM-DD → 指定日のレース一覧
  GET /race/<race_id>      → 1レースの予測詳細
  GET /api/race/<race_id>  → JSON
  GET /healthz             → ヘルスチェック
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from datetime import timedelta

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

import config
from src.db.connection import connect as db_connect
from src.web.auth import (
    is_member, is_pro, member_only_api, pro_only_api, pro_required,
    register_auth_routes,
)
from src.web.predictor import Predictor

logger = logging.getLogger(__name__)


def _format_race_id(race_id: str) -> tuple[str, int, int]:
    """'YYYYMMDD-SS-RR' → (date_str, stadium, race_no)"""
    parts = race_id.split("-")
    return parts[0], int(parts[1]), int(parts[2])


def _stadium_name_map() -> dict[int, str]:
    with db_connect() as conn:
        rows = conn.execute("SELECT stadium_number, name FROM stadiums").fetchall()
    return {n: name for n, name in rows}


def _race_basic_info(race_id: str) -> Optional[dict]:
    with db_connect() as conn:
        row = conn.execute("""
            SELECT r.race_id, r.race_date, r.stadium_number, r.race_number,
                   r.race_grade_number, r.race_title, r.race_subtitle,
                   r.race_closed_at, s.name AS stadium_name
              FROM races r
              JOIN stadiums s ON r.stadium_number = s.stadium_number
             WHERE r.race_id = ?
        """, (race_id,)).fetchone()
    if not row:
        return None
    keys = ["race_id", "race_date", "stadium_number", "race_number",
            "race_grade_number", "race_title", "race_subtitle",
            "race_closed_at", "stadium_name"]
    return dict(zip(keys, row))


def _races_for_date(target_date: str) -> list[dict]:
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at,
                   s.name AS stadium_name,
                   (SELECT COUNT(*) FROM race_results WHERE race_id = r.race_id) AS results_count
              FROM races r
              JOIN stadiums s ON r.stadium_number = s.stadium_number
             WHERE r.race_date = ?
             ORDER BY r.stadium_number, r.race_number
        """, (target_date,)).fetchall()
    keys = ["race_id", "stadium_number", "race_number", "race_closed_at",
            "stadium_name", "results_count"]
    return [dict(zip(keys, r)) for r in rows]


def _race_predictions(predictor: Predictor, race_id: str) -> list[dict]:
    target_date = race_id[:8]
    target_date_iso = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"
    df = predictor.predict_date(target_date_iso)
    if df.empty:
        return []
    sub = df[df["race_id"] == race_id].copy()
    if sub.empty:
        return []
    sub = sub.sort_values("prob_first", ascending=False)
    sub["pred_rank"] = range(1, len(sub) + 1)
    cols = [
        "boat_number", "racer_number",
        "class_number", "national_top_2_percent", "local_top_2_percent",
        "assigned_motor_top_2_percent", "exhibition_time", "start_timing_exhibition",
        "prob_first", "prob_top_2", "prob_top_3", "raw_score", "pred_rank",
        "finishing_position",
    ]
    available = [c for c in cols if c in sub.columns]
    return sub[available].to_dict(orient="records")


def _racer_names(race_id: str) -> dict[int, str]:
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT boat_number, racer_name FROM race_entries WHERE race_id = ?
        """, (race_id,)).fetchall()
    return {bn: name for bn, name in rows}


# Bootstrap CI で確実マイナスと検証された会場 (v0.2 検証)
_LOSING_VENUES = {2: "戸田", 7: "蒲郡", 10: "三国", 21: "芦屋"}
_QUESTIONABLE_VENUES = {4: "平和島", 8: "常滑", 19: "下関", 24: "大村"}


def _race_actual_result(race_id: str) -> Optional[dict]:
    """確定結果と各券種の払戻を取得。結果が無ければ None."""
    with db_connect() as conn:
        # 着順
        rows = conn.execute("""
            SELECT boat_number, finishing_position, course_number, start_timing, kimarite
              FROM race_results
             WHERE race_id = ?
             ORDER BY finishing_position
        """, (race_id,)).fetchall()
        if not rows:
            return None
        finishers = []
        positions: dict[int, int] = {}
        for bn, pos, course, st, kim in rows:
            if pos is None:
                continue
            try:
                pos_int = int(pos)
            except (TypeError, ValueError):
                continue
            positions[int(bn)] = pos_int
            finishers.append({
                "boat_number": int(bn),
                "position": pos_int,
                "course_number": int(course) if course is not None else None,
                "start_timing": float(st) if st is not None else None,
                "kimarite": kim,
            })
        # 払戻
        payout_rows = conn.execute("""
            SELECT bet_type, combination, payout
              FROM race_payouts WHERE race_id = ?
        """, (race_id,)).fetchall()
    if not finishers:
        return None
    finishers.sort(key=lambda f: f["position"])
    payouts: dict[str, list[dict]] = {}
    for bt, comb, p in payout_rows:
        payouts.setdefault(bt, []).append({"combination": comb, "payout": int(p)})

    # 1着-2着-3着 の組合せ
    by_pos = {f["position"]: f["boat_number"] for f in finishers}
    trifecta_combo = None
    if 1 in by_pos and 2 in by_pos and 3 in by_pos:
        trifecta_combo = f"{by_pos[1]}-{by_pos[2]}-{by_pos[3]}"
    return {
        "finishers": finishers,
        "by_position": by_pos,
        "trifecta_combo": trifecta_combo,
        "payouts": payouts,
    }


def _race_current_conditions(race_id: str) -> dict:
    """
    race_previews から現状のレース条件と艇別の展示値を取得。
    What-if シミュレーターの初期値プリフィル用。
    """
    out = {
        "wind_speed": None, "wave_height": None, "temperature": None, "water_temperature": None,
        "wind_direction_number": None, "weather_number": None,
        "boats": {},   # {boat_number: {exhibition_time, start_timing_exhibition, course_number, tilt_adjustment, weight_adjustment}}
    }
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT boat_number, weather_number, wind_speed, wind_direction_number,
                   wave_height, temperature, water_temperature,
                   course_number, exhibition_time, start_timing_exhibition,
                   weight_adjustment, tilt_adjustment
              FROM race_previews
             WHERE race_id = ?
             ORDER BY boat_number
        """, (race_id,)).fetchall()
    if not rows:
        return out
    keys = ["boat_number", "weather_number", "wind_speed", "wind_direction_number",
            "wave_height", "temperature", "water_temperature",
            "course_number", "exhibition_time", "start_timing_exhibition",
            "weight_adjustment", "tilt_adjustment"]
    for row in rows:
        d = dict(zip(keys, row))
        # レース全体の値 (どの行でも同じはずなので最初の値で上書き)
        for k in ("weather_number", "wind_speed", "wind_direction_number",
                  "wave_height", "temperature", "water_temperature"):
            if out[k] is None and d.get(k) is not None:
                out[k] = d[k]
        # 艇別
        out["boats"][int(d["boat_number"])] = {
            "course_number": d.get("course_number"),
            "exhibition_time": d.get("exhibition_time"),
            "start_timing_exhibition": d.get("start_timing_exhibition"),
            "weight_adjustment": d.get("weight_adjustment"),
            "tilt_adjustment": d.get("tilt_adjustment"),
        }
    return out


# ============================================================
# Flask アプリ
# ============================================================

def create_app(version: str = config.DEFAULT_MODEL_VERSION) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SECRET_KEY"] = config.WEB_SESSION_SECRET
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
    app.jinja_env.auto_reload = True
    register_auth_routes(app)
    # テンプレートから is_member() / is_pro() を呼べるように
    app.jinja_env.globals["is_member"] = is_member
    app.jinja_env.globals["is_pro"] = is_pro
    predictor = Predictor(version=version)

    # 起動時にモデルを先読み (失敗してもアプリは動かす)
    try:
        predictor.load()
    except FileNotFoundError as e:
        logger.warning("model not loaded: %s. UI will show error until model is trained.", e)

    @app.route("/healthz")
    def healthz():
        return {"status": "ok", "model_loaded": predictor.artifact is not None}

    @app.route("/")
    def index():
        target = request.args.get("date") or date.today().isoformat()
        return redirect(url_for("races", date=target))

    @app.route("/races")
    def races():
        target_date = request.args.get("date") or date.today().isoformat()
        races_list = _races_for_date(target_date)
        if not races_list:
            return render_template(
                "index.html",
                target_date=target_date,
                stadium_groups=[],
                empty=True,
            )

        # 会場別にグループ化
        stadium_groups: dict[int, dict] = {}
        for r in races_list:
            sn = r["stadium_number"]
            if sn not in stadium_groups:
                stadium_groups[sn] = {
                    "stadium_number": sn,
                    "stadium_name": r["stadium_name"],
                    "races": [],
                }
            stadium_groups[sn]["races"].append(r)

        return render_template(
            "index.html",
            target_date=target_date,
            stadium_groups=sorted(stadium_groups.values(),
                                  key=lambda g: g["stadium_number"]),
            empty=False,
        )

    @app.route("/race/<race_id>")
    def race_detail(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            abort(404)

        try:
            preds = _race_predictions(predictor, race_id)
        except Exception as e:
            logger.exception("prediction failed: %s", race_id)
            return render_template(
                "race.html",
                info=info,
                preds=[],
                error=str(e),
                racer_names={},
                trifecta_pw=[],
                trifecta_unified=[],
                conditions={},
            )

        names = _racer_names(race_id)
        target_date = info["race_date"]
        conditions = _race_current_conditions(race_id)
        actual_result = _race_actual_result(race_id)

        # 戦略タグ判定
        sn = info["stadium_number"]
        venue_warning = None
        if sn in _LOSING_VENUES:
            venue_warning = {
                "level": "danger",
                "venue": _LOSING_VENUES[sn],
                "msg": "Bootstrap CI で確実マイナスと検証済の会場。単勝固定買い非推奨",
            }
        elif sn in _QUESTIONABLE_VENUES:
            venue_warning = {
                "level": "caution",
                "venue": _QUESTIONABLE_VENUES[sn],
                "msg": "ROI 弱マイナス会場 (CI 一部正側、慎重)",
            }

        # 鉄板狙い判定 (1号艇 prob 70%+ かつ非マイナス会場)
        sweet_spot = False
        if preds and preds[0].get("boat_number") == 1 and preds[0].get("prob_first", 0) >= 0.70:
            if sn not in _LOSING_VENUES:
                sweet_spot = True

        # 三連単予測
        tri_pw = []
        tri_uni = []
        try:
            pw = predictor.predict_trifecta(target_date, race_id, mode="per_winner")
            if pw:
                tri_pw = pw[:10]  # top 10
        except Exception as e:
            logger.warning("per-winner trifecta failed for %s: %s", race_id, e)
        try:
            uni = predictor.predict_trifecta(target_date, race_id, mode="unified")
            if uni:
                tri_uni = uni[:10]
        except Exception as e:
            logger.warning("unified trifecta failed for %s: %s", race_id, e)

        return render_template(
            "race.html",
            info=info,
            preds=preds,
            racer_names=names,
            trifecta_pw=tri_pw,
            trifecta_unified=tri_uni,
            conditions=conditions,
            venue_warning=venue_warning,
            sweet_spot=sweet_spot,
            actual_result=actual_result,
            error=None,
        )

    @app.route("/api/race/<race_id>/value-bets")
    @member_only_api
    def race_value_bets(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            return jsonify({"error": "not found"}), 404
        snapshot = request.args.get("snapshot", "T-5min")
        thr = float(request.args.get("ev", "0.0"))
        try:
            r = predictor.find_value_bets_for_race(
                info["race_date"], race_id, snapshot_label=snapshot, ev_threshold=thr
            )
        except Exception as e:
            logger.exception("value bet failed: %s", race_id)
            return jsonify({"error": str(e)}), 500
        if r is None:
            return jsonify({"race_id": race_id, "snapshot_label": snapshot,
                            "n_value_bets": 0, "value_bets": [],
                            "best_ev": None, "best_combo": None,
                            "warning": "no model prediction or no odds snapshot"})
        return jsonify(r)

    @app.route("/api/ev-races")
    def ev_races_for_date():
        """指定日の EV+ レース一覧 (UI のマーク表示用)
        snapshot: T-15min / T-5min / T-1min / final / auto (利用可能な最良に自動選択)
        """
        target_date = request.args.get("date") or date.today().isoformat()
        snapshot = request.args.get("snapshot", "auto")
        thr = float(request.args.get("ev", "0.0"))

        # auto モード: T-15min → T-5min → T-1min → final の順で利用可能なものを選択
        if snapshot == "auto":
            with db_connect() as conn:
                avail = {row[0] for row in conn.execute("""
                    SELECT DISTINCT o.snapshot_label FROM odds_trifecta o
                      JOIN races r ON o.race_id=r.race_id
                     WHERE r.race_date=? AND o.snapshot_label IS NOT NULL
                """, (target_date,)).fetchall()}
            for fb in ["T-15min", "T-5min", "T-1min", "final"]:
                if fb in avail:
                    snapshot = fb
                    break
            if snapshot == "auto":
                snapshot = "final"

        with db_connect() as conn:
            rows = conn.execute("""
                SELECT DISTINCT o.race_id
                  FROM odds_trifecta o
                  JOIN races r ON o.race_id = r.race_id
                 WHERE r.race_date = ? AND o.snapshot_label = ?
            """, (target_date, snapshot)).fetchall()
        race_ids = [r[0] for r in rows]
        ev_marks: dict[str, dict] = {}
        n_positive = 0
        for rid in race_ids:
            try:
                # 全レースの best EV を取得 (ev_threshold=-1.0 で全候補)
                # min_prob=0.05 で長尾過大評価を除外
                r = predictor.find_value_bets_for_race(
                    target_date, rid,
                    snapshot_label=snapshot,
                    ev_threshold=-1.0,
                    min_prob=0.05,
                    max_odds=100.0,
                )
                if r and r.get("value_bets"):
                    best = r["value_bets"][0]
                    ev_marks[rid] = {
                        "best_ev": best["adj_ev"],
                        "best_combo": best["combination"],
                        "best_prob": best["prob"],
                        "best_odds": best["odds"],
                        "n": sum(1 for v in r["value_bets"] if v["adj_ev"] >= thr),
                    }
                    if best["adj_ev"] >= thr:
                        n_positive += 1
            except Exception as e:
                logger.warning("ev-races failed for %s: %s", rid, e)
        return jsonify({"date": target_date, "snapshot": snapshot,
                        "n_marked": n_positive,
                        "n_total": len(ev_marks),
                        "ev_threshold": thr, "marks": ev_marks})

    @app.route("/api/race/<race_id>/whatif", methods=["POST"])
    def race_whatif(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            return jsonify({"error": "race not found"}), 404
        target_date = info["race_date"]
        overrides = (request.get_json(silent=True) or {}).get("overrides", {})
        try:
            result = predictor.predict_whatif(target_date, race_id, overrides)
        except Exception as e:
            logger.exception("whatif failed: %s", race_id)
            return jsonify({"error": str(e)}), 500
        if result is None:
            return jsonify({"error": "no data"}), 404
        names = _racer_names(race_id)
        for b in result["boats"]:
            b["racer_name"] = names.get(b["boat_number"])
        return jsonify(result)

    @app.route("/api/race/<race_id>")
    def race_api(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            return jsonify({"error": "not found"}), 404
        try:
            preds = _race_predictions(predictor, race_id)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"info": info, "predictions": preds})

    # =====================================================
    # Pro プラン: T-15min 期待値モニター
    # =====================================================

    @app.route("/pro/ev")
    @pro_required
    def pro_ev():
        target_date = request.args.get("date") or date.today().isoformat()
        snapshot = request.args.get("snapshot") or "T-15min"

        # 指定 snapshot に該当データが無ければ final にフォールバック
        with db_connect() as conn:
            cnt = conn.execute("""
                SELECT COUNT(DISTINCT o.race_id) FROM odds_trifecta o
                  JOIN races r ON o.race_id=r.race_id
                 WHERE r.race_date=? AND o.snapshot_label=?
            """, (target_date, snapshot)).fetchone()[0]
        if cnt == 0:
            snapshot = "final"  # 暫定: T-15min 未蓄積期は final で代替

        # 該当日 + snapshot のあるレースを取得
        with db_connect() as conn:
            rows = conn.execute("""
                SELECT DISTINCT r.race_id, r.stadium_number, r.race_number,
                       r.race_closed_at, s.name AS stadium_name,
                       (SELECT COUNT(*) FROM race_results WHERE race_id = r.race_id) AS results_count
                  FROM races r
                  JOIN stadiums s ON r.stadium_number = s.stadium_number
                  JOIN odds_trifecta o ON o.race_id = r.race_id
                 WHERE r.race_date = ?
                   AND o.snapshot_label = ?
                 ORDER BY r.race_closed_at, r.stadium_number
            """, (target_date, snapshot)).fetchall()
        keys = ["race_id", "stadium_number", "race_number", "race_closed_at",
                "stadium_name", "results_count"]
        races = [dict(zip(keys, r)) for r in rows]

        # 各レースの best EV を計算
        # min_prob=0.05 でモデル長尾過大評価を除外 (True Value Bet 検証で判明)
        ev_min = float(request.args.get("ev_min", "-1.0"))
        for r in races:
            try:
                vb = predictor.find_value_bets_for_race(
                    target_date, r["race_id"], snapshot_label=snapshot,
                    ev_threshold=-1.0, max_odds=100.0, min_prob=0.05,
                )
                if vb and vb.get("value_bets"):
                    best = vb["value_bets"][0]
                    r["best_combo"] = best["combination"]
                    r["best_ev"] = best["adj_ev"]
                    r["best_prob"] = best["prob"]
                    r["best_odds"] = best["odds"]
                    r["n_positive_ev"] = sum(1 for v in vb["value_bets"]
                                              if v["adj_ev"] >= 0)
                else:
                    r["best_combo"] = None
                    r["best_ev"] = None
                    r["n_positive_ev"] = 0
            except Exception as e:
                logger.warning("pro_ev calc failed for %s: %s", r["race_id"], e)
                r["best_ev"] = None
                r["n_positive_ev"] = 0

            # 会場警告
            sn = r["stadium_number"]
            if sn in _LOSING_VENUES:
                r["venue_warning"] = "danger"
            elif sn in _QUESTIONABLE_VENUES:
                r["venue_warning"] = "caution"
            else:
                r["venue_warning"] = None

        # フィルター適用
        if ev_min > -1.0:
            races = [r for r in races if (r["best_ev"] or -99) >= ev_min]

        return render_template(
            "pro_ev.html",
            target_date=target_date,
            races=races,
            snapshot=snapshot,
            ev_min=ev_min,
            n_total=len(rows),
            n_filtered=len(races),
        )

    @app.route("/pro/ev/race/<race_id>")
    @pro_required
    def pro_ev_race(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            abort(404)
        snapshot = request.args.get("snapshot", "T-15min")
        target_date = info["race_date"]

        # T-15min が無ければ自動 fallback (T-5min → final)
        with db_connect() as conn:
            avail = {
                row[0] for row in conn.execute(
                    "SELECT DISTINCT snapshot_label FROM odds_trifecta WHERE race_id=?",
                    (race_id,)
                ).fetchall()
            }
        if snapshot not in avail:
            for fb in ["T-15min", "T-5min", "T-1min", "final"]:
                if fb in avail:
                    snapshot = fb
                    break

        try:
            vb = predictor.find_value_bets_for_race(
                target_date, race_id, snapshot_label=snapshot,
                ev_threshold=-1.0, max_odds=200.0, min_prob=0.03,
            )
        except Exception as e:
            logger.exception("pro_ev_race failed: %s", race_id)
            vb = None

        names = _racer_names(race_id)
        try:
            preds = _race_predictions(predictor, race_id)
        except Exception:
            preds = []

        # 戦略タグ
        sn = info["stadium_number"]
        venue_warning = None
        if sn in _LOSING_VENUES:
            venue_warning = {
                "level": "danger", "venue": _LOSING_VENUES[sn],
                "msg": "Bootstrap CI で確実マイナスと検証済の会場",
            }
        elif sn in _QUESTIONABLE_VENUES:
            venue_warning = {
                "level": "caution", "venue": _QUESTIONABLE_VENUES[sn],
                "msg": "ROI 弱マイナス会場 (慎重)",
            }

        # Kelly 比率の参考計算 (kelly = (b*p - (1-p)) / b, b=odds-1)
        if vb and vb.get("value_bets"):
            for bet in vb["value_bets"]:
                p, o = bet["prob"], bet["odds"]
                if o > 1.0 and p > 0:
                    b = o - 1.0
                    kelly = (b * p - (1 - p)) / b
                    bet["kelly"] = max(0.0, kelly) * config.KELLY_FRACTION
                else:
                    bet["kelly"] = 0.0
                # フェアオッズ (controlled for takeout=25%)
                bet["fair_odds"] = (1.0 / p) if p > 0 else None

        return render_template(
            "pro_ev_race.html",
            info=info,
            preds=preds,
            racer_names=names,
            value_bet=vb,
            snapshot=snapshot,
            venue_warning=venue_warning,
        )

    @app.route("/api/pro/ev/<race_id>")
    @pro_only_api
    def pro_ev_api(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            return jsonify({"error": "not found"}), 404
        snapshot = request.args.get("snapshot", "T-15min")
        try:
            vb = predictor.find_value_bets_for_race(
                info["race_date"], race_id, snapshot_label=snapshot,
                ev_threshold=-1.0, max_odds=500.0,
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        if not vb:
            return jsonify({"race_id": race_id, "snapshot": snapshot,
                            "available": False})
        return jsonify({"race_id": race_id, "snapshot": snapshot,
                        "available": True, **vb})

    return app
