"""自律検証ループ:
  1. 大量の仮説バッチを生成 (refinement / analog / Venus / combinations)
  2. 各仮説を train/test split で検証
  3. robust survivors を蓄積
  4. survivors 同士の組合せをさらに検証
  5. 結果を markdown レポート出力

ポリシー: 発見と検証のみ、本番実装はしない。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from itertools import product
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config
from src.verification.backtest import _build_where

STAD = {1:"桐生",2:"戸田",3:"江戸川",4:"平和島",5:"多摩川",6:"浜名湖",7:"蒲郡",8:"常滑",
        9:"津",10:"三国",11:"びわこ",12:"住之江",13:"尼崎",14:"鳴門",15:"丸亀",16:"児島",
        17:"宮島",18:"徳山",19:"下関",20:"若松",21:"芦屋",22:"福岡",23:"唐津",24:"大村"}

THRESHOLD = 130.0


def _conn():
    if os.getenv("DATABASE_URL", "").strip():
        from src.db.connection import connect as db_connect
        return db_connect()
    return sqlite3.connect(config.DB_PATH)


def split_date():
    c = _conn()
    row = c.execute("SELECT MIN(race_date), MAX(race_date) FROM races").fetchone()
    c.close()
    s = datetime.strptime(row[0], "%Y-%m-%d").date()
    e = datetime.strptime(row[1], "%Y-%m-%d").date()
    return date.fromordinal(s.toordinal() + (e - s).days // 2)


def bt_range(cond: dict, date_from: str, date_to: str) -> dict:
    where, args, joins = _build_where(cond)
    bt = cond.get("bet_type", "trifecta")
    fp = cond.get("finish_pattern")
    combo = fp if fp and "-" in fp else "1-2-3"
    joins_str = "\n  ".join(joins)
    sql = f"""
        SELECT COUNT(DISTINCT r.race_id), COUNT(pp.payout),
               COALESCE(SUM(pp.payout), 0)
          FROM races r
          LEFT JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
          LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
          {joins_str}
          LEFT JOIN race_payouts pp ON pp.race_id=r.race_id
                                  AND pp.bet_type=? AND pp.combination=?
         WHERE {where} AND r.race_date>=? AND r.race_date<=?
    """
    full_args = list(args) + [bt, combo, date_from, date_to]
    # 順序調整: WHERE 句のplaceholderが先、JOIN条件のplaceholderが後だが、
    # ここではJOINに?を使ってないので問題なし。
    # ただしSQL中の?順は WHERE -> bet_type/combo の順に並んでる。
    # 修正: bet_type/comboは LEFT JOIN内にあるので先、その後WHERE
    sql = f"""
        SELECT COUNT(DISTINCT r.race_id), COUNT(pp.payout),
               COALESCE(SUM(pp.payout), 0)
          FROM races r
          LEFT JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
          LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
          {joins_str}
          LEFT JOIN race_payouts pp ON pp.race_id=r.race_id
                                  AND pp.bet_type=? AND pp.combination=?
         WHERE {where} AND r.race_date>=? AND r.race_date<=?
    """
    full_args = [bt, combo] + list(args) + [date_from, date_to]
    conn = _conn()
    try:
        n, hits, pay = conn.execute(sql, full_args).fetchone()
    finally:
        conn.close()
    n = n or 0; hits = hits or 0; pay = int(pay or 0)
    return {"n": n, "hits": hits, "pay": pay,
            "roi": (pay/(100*n)*100) if n else 0.0}


def label_of(cond: dict, name: str = "") -> str:
    if name:
        return name
    parts = []
    if cond.get("stadium"):
        parts.append("/".join(STAD.get(s, str(s)) for s in cond["stadium"]))
    if cond.get("racer_class"):
        parts.append("/".join({1:"A1",2:"A2",3:"B1",4:"B2"}.get(c, str(c))
                              for c in cond["racer_class"]))
    if cond.get("boat1_motor_top2_min"):
        parts.append(f"モ2≥{int(cond['boat1_motor_top2_min'])}")
    if cond.get("boat1_natl_1_min"):
        parts.append(f"国1≥{int(cond['boat1_natl_1_min'])}")
    if cond.get("boat1_local_1_min"):
        parts.append(f"当地≥{int(cond['boat1_local_1_min'])}")
    if cond.get("weather_exclude"):
        parts.append("雨除外")
    if cond.get("race_number"):
        parts.append("/".join(f"{r}R" for r in cond["race_number"]))
    if cond.get("kimarite"):
        parts.append(cond["kimarite"])
    if cond.get("venus_only"):
        parts.append("Venus")
    if cond.get("no_female"):
        parts.append("男のみ")
    parts.append(cond.get("finish_pattern", "1-2-3"))
    return " × ".join(parts)


def verdict(tr_roi, te_roi, tr_n=0, te_n=0, min_n=30):
    if tr_n < min_n or te_n < min_n:
        return "⚠ small-n"
    if tr_roi >= THRESHOLD and te_roi >= THRESHOLD:
        return "🏆 robust"
    if tr_roi >= THRESHOLD or te_roi >= THRESHOLD:
        return "⚠ one-sided"
    return "❌ dead"


# ======================================================================
# 仮説バッチ
# ======================================================================

def batch_b_kamagori_refinements():
    """蒲郡 A1 motor≥35 1-2-3 (新 robust) の細分化"""
    base = {"stadium": [7], "racer_class": [1], "boat1_motor_top2_min": 35.0,
            "finish_pattern": "1-2-3", "bet_type": "trifecta"}
    return [
        (base, "蒲郡 A1 motor≥35 1-2-3 (base)"),
        ({**base, "weather_exclude": [3]},
         "蒲郡 A1 motor≥35 雨除外 1-2-3"),
        ({**base, "boat1_motor_top2_min": 40.0},
         "蒲郡 A1 motor≥40 1-2-3"),
        ({**base, "boat1_motor_top2_min": 45.0},
         "蒲郡 A1 motor≥45 1-2-3"),
        ({**base, "boat1_natl_1_min": 6.0},
         "蒲郡 A1 国1≥6 motor≥35 1-2-3"),
        ({**base, "boat1_natl_1_min": 7.0},
         "蒲郡 A1 国1≥7 motor≥35 1-2-3"),
        ({**base, "boat1_local_1_min": 40.0},
         "蒲郡 A1 当地≥40 motor≥35 1-2-3"),
        ({**base, "boat1_local_1_min": 50.0},
         "蒲郡 A1 当地≥50 motor≥35 1-2-3"),
        ({**base, "weather_exclude": [3], "boat1_motor_top2_min": 40.0},
         "蒲郡 A1 雨除外 motor≥40 1-2-3 (combo)"),
        ({**base, "weather_exclude": [3], "boat1_natl_1_min": 7.0},
         "蒲郡 A1 雨除外 国1≥7 motor≥35 1-2-3 (combo)"),
    ]


def batch_b_analog_venues():
    """他会場 A1 motor≥35 1-2-3 (transfer learning)"""
    out = []
    for sta in [1, 4, 17, 18, 19, 21, 24, 5, 11, 12, 13, 15]:
        out.append((
            {"stadium": [sta], "racer_class": [1], "boat1_motor_top2_min": 35.0,
             "finish_pattern": "1-2-3", "bet_type": "trifecta"},
            f"{STAD[sta]} A1 motor≥35 1-2-3"))
        out.append((
            {"stadium": [sta], "racer_class": [1], "boat1_motor_top2_min": 35.0,
             "weather_exclude": [3],
             "finish_pattern": "1-2-3", "bet_type": "trifecta"},
            f"{STAD[sta]} A1 motor≥35 雨除外 1-2-3"))
    return out


def batch_c_venus():
    """Venus universe探索"""
    return [
        ({"venus_only": True, "finish_pattern": "1-2-3", "bet_type": "trifecta"},
         "Venus全体 1-2-3"),
        ({"venus_only": True, "racer_class": [1],
          "finish_pattern": "1-2-3", "bet_type": "trifecta"},
         "Venus A1 1-2-3"),
        ({"venus_only": True, "weather_exclude": [3],
          "finish_pattern": "1-2-3", "bet_type": "trifecta"},
         "Venus 雨除外 1-2-3"),
        ({"venus_only": True, "racer_class": [1], "weather_exclude": [3],
          "finish_pattern": "1-2-3", "bet_type": "trifecta"},
         "Venus A1 雨除外 1-2-3"),
        ({"venus_only": True, "stadium": [24],
          "finish_pattern": "1-2-3", "bet_type": "trifecta"},
         "Venus 大村 1-2-3"),
        ({"venus_only": True, "boat1_natl_1_min": 6.0,
          "finish_pattern": "1-2-3", "bet_type": "trifecta"},
         "Venus 国1≥6 1-2-3"),
    ]


def batch_d_domain_dump():
    """ドメイン知識から大量候補"""
    out = []
    # 級別 venue 別 1-2-3 / 1-3-2 / 2-1-3
    for sta in [1, 2, 3, 4, 5, 6, 7, 8, 18, 19, 21, 24]:
        for cls in [1, 2]:
            for combo in ["1-2-3", "1-3-2"]:
                out.append((
                    {"stadium": [sta], "racer_class": [cls],
                     "boat1_natl_1_min": 6.0,
                     "weather_exclude": [3],
                     "finish_pattern": combo, "bet_type": "trifecta"},
                    f"{STAD[sta]} A{cls} 国1≥6 雨除外 {combo}"))
    return out


def batch_combos(robust_picks):
    """robust 候補の組合せ (2 つの条件を AND)"""
    out = []
    for i, (c1, l1) in enumerate(robust_picks):
        for j, (c2, l2) in enumerate(robust_picks):
            if i >= j:
                continue
            # 互換性チェック: stadium が両方指定されていて違うなら skip
            if (c1.get("stadium") and c2.get("stadium")
                    and set(c1["stadium"]) != set(c2["stadium"])):
                continue
            if (c1.get("finish_pattern") and c2.get("finish_pattern")
                    and c1["finish_pattern"] != c2["finish_pattern"]):
                continue
            merged = {**c1}
            for k, v in c2.items():
                if k not in merged or merged[k] is None:
                    merged[k] = v
            out.append((merged, f"({l1}) ∩ ({l2})"))
    return out


def run_batch(name: str, batch: list, sd, prev_iso: str, sd_iso: str):
    print(f"\n=== {name} ({len(batch)} 件) ===")
    results = []
    for cond, lbl in batch:
        try:
            tr = bt_range(cond, "0000-01-01", prev_iso)
            te = bt_range(cond, sd_iso, "9999-12-31")
            v = verdict(tr["roi"], te["roi"], tr["n"], te["n"])
            results.append({"cond": cond, "label": lbl, "tr": tr, "te": te, "v": v})
            icon = "🏆" if v.startswith("🏆") else ("⚠" if v.startswith("⚠") else "❌")
            print(f"  [{icon}] tr n={tr['n']:>4} ROI={tr['roi']:>6.1f}% "
                  f"| te n={te['n']:>4} ROI={te['roi']:>6.1f}% | {lbl}")
        except Exception as e:
            print(f"  [ERR] {lbl}: {e}")
    return results


def main():
    sd = split_date()
    sd_iso = sd.isoformat()
    prev_iso = (sd - timedelta(days=1)).isoformat()
    print(f"=== 自律検証ループ split={sd_iso} threshold={THRESHOLD}% ===")

    all_results = []

    # B: 蒲郡 refinement
    r = run_batch("B-1: 蒲郡 A1 motor≥35 1-2-3 細分化", batch_b_kamagori_refinements(),
                  sd, prev_iso, sd_iso)
    all_results.extend(r)

    # B-analog: 他会場 transfer
    r = run_batch("B-2: 他会場 A1 motor≥35 1-2-3 transfer", batch_b_analog_venues(),
                  sd, prev_iso, sd_iso)
    all_results.extend(r)

    # C: Venus
    r = run_batch("C-1: Venus universe", batch_c_venus(), sd, prev_iso, sd_iso)
    all_results.extend(r)

    # D: ドメイン知識
    r = run_batch("D-1: ドメイン知識 (会場×級×国1×雨除外×combo)",
                  batch_d_domain_dump(), sd, prev_iso, sd_iso)
    all_results.extend(r)

    # robust survivor を抽出
    robust = [(x["cond"], x["label"]) for x in all_results if x["v"].startswith("🏆")]
    print(f"\n=== 第一段階 robust survivors: {len(robust)} 件 ===")
    for c, l in robust:
        print(f"  {l}")

    # combinations
    if len(robust) >= 2:
        combo_batch = batch_combos(robust)
        r = run_batch(f"組合せ検証 ({len(combo_batch)} pairs)", combo_batch,
                      sd, prev_iso, sd_iso)
        all_results.extend(r)

    # 全体 robust
    final_robust = [x for x in all_results if x["v"].startswith("🏆")]
    print(f"\n=== 全 robust: {len(final_robust)} 件 ===")
    for x in sorted(final_robust, key=lambda y: -y["te"]["roi"]):
        tr, te = x["tr"], x["te"]
        print(f"  tr={tr['roi']:.1f}% (n={tr['n']}) / te={te['roi']:.1f}% (n={te['n']})  "
              f"{x['label']}")

    # markdown 出力
    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    fp = out_dir / f"auto_loop_{datetime.now():%Y%m%d_%H%M}.md"
    lines = [f"# 自律検証ループレポート {datetime.now():%Y-%m-%d %H:%M}",
             f"split={sd_iso}, threshold={THRESHOLD}%", "",
             f"## robust survivors ({len(final_robust)} 件)", "",
             "| train n | train ROI | test n | test ROI | 手法 |",
             "|---------|-----------|--------|----------|------|"]
    for x in sorted(final_robust, key=lambda y: -y["te"]["roi"]):
        tr, te = x["tr"], x["te"]
        lines.append(f"| {tr['n']:,} | {tr['roi']:.1f}% | {te['n']:,} | "
                     f"{te['roi']:.1f}% | {x['label']} |")
    lines.append("")
    lines.append(f"## 全検証数: {len(all_results)}")
    fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nレポート: {fp}")


if __name__ == "__main__":
    main()
