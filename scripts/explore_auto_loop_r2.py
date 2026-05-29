"""ラウンド2: round 1 で発見した 蒲郡 robust の更なる絞り込み +
惜しかった候補の n 拡張検証。

検証する派生 (全て蒲郡 A1 motor≥35 雨除外 国1≥6 1-2-3 をベースに):
  - motor 閾値変更: ≥40, ≥45
  - 国1着率 閾値変更: ≥7, ≥7.5
  - 2号艇 top_2 重ね合わせ (≥35, ≥40)
  - 3号艇 国1着率 重ね合わせ (≥6, ≥7)
  - 当地1着率 重ね合わせ (≥40, ≥50)
  - レース番号 specific (1-12R)
  - 別 finish_pattern: 1-3-2 / 2-1-3
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 同じヘルパーを使う
from explore_auto_loop import (  # noqa: E402
    bt_range, verdict, split_date, label_of, THRESHOLD
)
from datetime import timedelta


def batch():
    """蒲郡 A1 motor≥35 雨除外 国1≥6 1-2-3 (#1 robust) からの派生"""
    base = {"stadium": [7], "racer_class": [1], "boat1_motor_top2_min": 35.0,
            "weather_exclude": [3], "boat1_natl_1_min": 6.0,
            "finish_pattern": "1-2-3", "bet_type": "trifecta"}
    out = [(base, "base: 蒲郡 A1 motor≥35 雨除外 国1≥6 1-2-3")]
    # motor 強化
    for mo in [40.0, 45.0]:
        out.append(({**base, "boat1_motor_top2_min": mo},
                    f"motor≥{int(mo)}"))
    # 国1着率 強化
    for n1 in [7.0, 7.5]:
        out.append(({**base, "boat1_natl_1_min": n1},
                    f"国1≥{n1}"))
    # 2号艇 / 3号艇
    for b2 in [35.0, 40.0]:
        out.append(({**base, "boat2_top2_min": b2},
                    f"2号艇 top2≥{int(b2)}"))
    for b3 in [6.0, 7.0]:
        out.append(({**base, "boat3_natl_1_min": b3},
                    f"3号艇 国1≥{b3}"))
    # 当地
    for lo in [40.0, 50.0]:
        out.append(({**base, "boat1_local_1_min": lo},
                    f"当地≥{int(lo)}"))
    # レース番号
    for rn in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]:
        out.append(({**base, "race_number": [rn]},
                    f"{rn}R"))
    # 別 finish pattern
    out.append(({**base, "finish_pattern": "1-3-2"}, "1-3-2着"))
    out.append(({**base, "finish_pattern": "2-1-3"}, "2-1-3着"))
    # トリプル combo: 国1≥7 + motor≥40
    out.append(({**base, "boat1_natl_1_min": 7.0,
                 "boat1_motor_top2_min": 40.0},
                "国1≥7 + motor≥40 (triple)"))
    # 戸田 A2 の追加検証 (n 拡張するため雨除外を外す)
    out.append(({"stadium": [2], "racer_class": [2], "boat1_natl_1_min": 6.0,
                 "finish_pattern": "1-2-3", "bet_type": "trifecta"},
                "戸田 A2 国1≥6 1-2-3 (雨除外なし、n拡張)"))
    # 蒲郡 A2 motor で試す
    out.append(({"stadium": [7], "racer_class": [2],
                 "boat1_motor_top2_min": 35.0,
                 "finish_pattern": "1-2-3", "bet_type": "trifecta"},
                "蒲郡 A2 motor≥35 1-2-3"))
    out.append(({"stadium": [7], "racer_class": [2],
                 "boat1_motor_top2_min": 35.0, "weather_exclude": [3],
                 "finish_pattern": "1-2-3", "bet_type": "trifecta"},
                "蒲郡 A2 motor≥35 雨除外 1-2-3"))
    return out


def main():
    sd = split_date()
    prev = (sd - timedelta(days=1)).isoformat()
    sd_iso = sd.isoformat()
    print(f"=== ラウンド2 split={sd_iso} ===\n")
    results = []
    for cond, lbl in batch():
        tr = bt_range(cond, "0000-01-01", prev)
        te = bt_range(cond, sd_iso, "9999-12-31")
        v = verdict(tr["roi"], te["roi"], tr["n"], te["n"])
        icon = "🏆" if v.startswith("🏆") else ("⚠" if v.startswith("⚠") else "❌")
        print(f"  [{icon}] tr n={tr['n']:>4} ROI={tr['roi']:>6.1f}% "
              f"| te n={te['n']:>4} ROI={te['roi']:>6.1f}% | {lbl}")
        results.append((cond, lbl, tr, te, v))

    robust = [(c, l) for c, l, tr, te, v in results if v.startswith("🏆")]
    print(f"\n=== ラウンド2 robust: {len(robust)} 件 ===")
    for c, l in robust:
        print(f"  {l}")


if __name__ == "__main__":
    main()
