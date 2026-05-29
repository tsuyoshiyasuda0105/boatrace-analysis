"""多条件組合せ探索エージェント。

verification_agent.py が "自然言語 → 既知手法の検証" を行うのに対し、
このスクリプトは "組合せの総当たり" で未知の優位パターンを掘る。

走査する次元 (デフォルト):
  - stadium      : 24 会場
  - course (1号艇からみた進入)  : 1-6 (注: 単純に boat_number=1 のクラスで絞る)
  - racer_class  : A1 / A2 / B1 / B2
  - race_number  : 1-12
  - kimarite     : (なし) / 逃げ / まくり / 差し / まくり差し
  - weather_exc  : (なし) / 雨除外

各セル (組合せ) について backtest.backtest_method を呼び、
ROI と n を計算。Tier 1/2/3 候補を markdown でレポート出力。

組合せ爆発を防ぐため、各次元の選択肢数を小さく保つ + 最小サンプル
(default n_min=30) でフィルタ。

ポリシー:
  - **発見と評価のみ**。本番への自動組込は一切しない。
  - 多重比較 (data-snooping) リスクを警告するため、上位 N 件のみ
    レポート + 「時期分割検証必須」の注釈を入れる。

使い方:
    python scripts/search_strategies.py
    python scripts/search_strategies.py --n-min 50 --top 30
    python scripts/search_strategies.py --bet trifecta --combo 1-2-3
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from src.verification.backtest import backtest_method, _tier  # noqa: E402

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

STADIUMS = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}
CLASS_LABEL = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}

# 探索次元 (None = その軸で絞り込みなし)
STADIUMS_AXIS = [None] + list(range(1, 25))
CLASS_AXIS = [None, 1, 2]  # A1 / A2 のみ (B 級は ROI 低いので除外)
RACE_NO_AXIS = [None] + list(range(1, 13))
KIMARITE_AXIS = [None, "逃げ", "まくり", "差し", "まくり差し"]
WEATHER_EXC_AXIS = [None, [3]]  # 雨除外有無
BET_COMBOS = ["1-2-3", "1-3-2", "1-2-4", "2-1-3", "1-4-2"]


def build_method(stadium, cls, race_no, kimarite, weather_exc, bet_combo, bet_type):
    cond: dict = {"bet_type": bet_type, "finish_pattern": bet_combo}
    if stadium is not None:
        cond["stadium"] = [stadium]
    if cls is not None:
        cond["racer_class"] = [cls]
    if race_no is not None:
        cond["race_number"] = [race_no]
    if kimarite is not None:
        cond["kimarite"] = kimarite
    if weather_exc is not None:
        cond["weather_exclude"] = weather_exc
    return {"conditions": cond, "source_url": "(combinatorial search)",
            "source_quote": "", "confidence": 1.0}


def search(n_min: int, top: int, bet_type: str, combos: list[str],
           include_kimarite: bool, include_race_no: bool):
    """組合せ走査。Tier 1/2/3 候補を返す。"""
    results = []
    kim_axis = KIMARITE_AXIS if include_kimarite else [None]
    rn_axis = RACE_NO_AXIS if include_race_no else [None]
    n_total = (len(STADIUMS_AXIS) * len(CLASS_AXIS) * len(rn_axis)
               * len(kim_axis) * len(WEATHER_EXC_AXIS) * len(combos))
    print(f"=== combinatorial search: {n_total:,} cells ===")
    i = 0
    last_log = datetime.now()
    for stadium, cls, rn, kim, wexc, combo in product(
            STADIUMS_AXIS, CLASS_AXIS, rn_axis, kim_axis,
            WEATHER_EXC_AXIS, combos):
        i += 1
        # 全 None なら大雑把すぎるのでスキップ
        if all(x is None for x in (stadium, cls, rn, kim, wexc)):
            continue
        method = build_method(stadium, cls, rn, kim, wexc, combo, bet_type)
        try:
            bt = backtest_method(method)
        except Exception as e:  # noqa: BLE001
            continue
        if bt.get("n_races", 0) < n_min:
            continue
        if bt.get("roi", 0) < 100:
            continue
        method["backtest"] = bt
        results.append(method)
        if (datetime.now() - last_log).total_seconds() > 5:
            print(f"  {i}/{n_total}  hits={len(results)}")
            last_log = datetime.now()

    # ROI 降順
    results.sort(key=lambda m: (-m["backtest"]["roi"],
                                -m["backtest"]["n_races"]))
    return results[:top]


def render_report(results: list[dict], output: Path, n_min: int):
    output.mkdir(parents=True, exist_ok=True)
    lines = [f"# 多条件総当たり探索レポート  {datetime.now():%Y-%m-%d %H:%M}",
             "",
             "> ⚠ **多重比較の罠**: 数千セルから ROI 上位を抽出するため、",
             "> data-snooping により偽優位が混入する可能性が高い。",
             "> **時期分割検証 (train/test split) を必ず別途行うこと**。",
             "",
             f"## サマリ",
             f"- 最小標本: n ≥ {n_min}",
             f"- ROI 100% 超: {len(results)} セル (上位のみ表示)",
             ""]
    by_tier = {"tier_1": [], "tier_2": [], "tier_3": [], "discard": []}
    for m in results:
        t = m["backtest"]["tier"]
        by_tier.setdefault(t, []).append(m)

    for tier_label, key in [("🏆 Tier 1 (ROI ≥ 150% かつ n ≥ 100)", "tier_1"),
                             ("🥈 Tier 2 (ROI 120-150%)",        "tier_2"),
                             ("🥉 Tier 3 (ROI 100-120%)",         "tier_3")]:
        items = by_tier.get(key, [])
        if not items:
            continue
        lines.append(f"## {tier_label}")
        lines.append("")
        lines.append("| # | 条件 | n | hit | hit率 | ROI | 平均配当 | 損益 |")
        lines.append("|---|------|---|-----|-------|-----|-----------|------|")
        for i, m in enumerate(items, 1):
            cond = m["conditions"]
            bt = m["backtest"]
            parts = []
            if cond.get("stadium"):
                parts.append(STADIUMS[cond["stadium"][0]])
            if cond.get("racer_class"):
                parts.append(CLASS_LABEL[cond["racer_class"][0]])
            if cond.get("race_number"):
                parts.append(f"{cond['race_number'][0]}R")
            if cond.get("kimarite"):
                parts.append(cond["kimarite"])
            if cond.get("weather_exclude"):
                parts.append("雨除外")
            parts.append(cond["finish_pattern"])
            title = " × ".join(parts)
            lines.append(f"| {i} | {title} | {bt['n_races']:,} | "
                         f"{bt['n_hits']:,} | {bt['hit_rate']:.1f}% | "
                         f"**{bt['roi']:.1f}%** | "
                         f"{bt['avg_payout_on_hit']:,.0f}円 | "
                         f"{bt['profit']:+,d}円 |")
        lines.append("")
    out = output / f"search_{datetime.now():%Y%m%d_%H%M}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-min", type=int, default=50,
                        help="最小標本数 (default 50)")
    parser.add_argument("--top", type=int, default=50,
                        help="レポートに出す上位件数 (default 50)")
    parser.add_argument("--bet", default="trifecta", help="bet_type")
    parser.add_argument("--combos", default="1-2-3,1-3-2",
                        help="検証する着順パターン (カンマ区切り)")
    parser.add_argument("--no-kimarite", action="store_true",
                        help="決まり手次元を省略 (高速化)")
    parser.add_argument("--no-race-no", action="store_true",
                        help="レース番号次元を省略 (高速化)")
    parser.add_argument("--output", default="reports")
    args = parser.parse_args()

    combos = [c.strip() for c in args.combos.split(",") if c.strip()]
    results = search(args.n_min, args.top, args.bet, combos,
                     not args.no_kimarite, not args.no_race_no)
    out = render_report(results, Path(args.output), args.n_min)
    print(f"\nレポート出力: {out}")
    print(f"Tier 1: {sum(1 for m in results if m['backtest']['tier']=='tier_1')}")
    print(f"Tier 2: {sum(1 for m in results if m['backtest']['tier']=='tier_2')}")
    print(f"Tier 3: {sum(1 for m in results if m['backtest']['tier']=='tier_3')}")


if __name__ == "__main__":
    main()
