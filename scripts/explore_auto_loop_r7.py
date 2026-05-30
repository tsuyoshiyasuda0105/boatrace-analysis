"""ラウンド7: 桐生 5-1-2 と 蒲郡 1-2-3 を最強組合せ + 全候補のサマリ"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from explore_auto_loop import bt_range, verdict, split_date  # noqa
from datetime import timedelta


def batch():
    out = []
    # 桐生 5-1-2 強化全部入り
    kiryu = {"stadium": [1], "racer_class": [1], "boat1_motor_top2_min": 35.0,
             "weather_exclude": [3], "boat1_natl_1_min": 6.0,
             "finish_pattern": "5-1-2", "bet_type": "trifecta"}
    out.append((kiryu, "桐生 全強化 (motor≥35 + 雨除外 + 国1≥6) 5-1-2"))
    out.append(({**kiryu, "race_number": [7,8,9,10,11,12]},
                "桐生 全強化 後半R 5-1-2"))
    out.append(({**kiryu, "race_number": [10,11,12]},
                "桐生 全強化 終盤R(10-12) 5-1-2"))
    # 桐生 motor を下げて n 拡張
    for mo in [25.0, 30.0, 32.0]:
        out.append(({**kiryu, "boat1_motor_top2_min": mo},
                    f"桐生 motor≥{int(mo)} 国1≥6 雨除外 5-1-2"))

    # 蒲郡 robust の最終 absolute combo
    kama = {"stadium": [7], "racer_class": [1], "boat1_motor_top2_min": 35.0,
            "weather_exclude": [3], "boat1_natl_1_min": 6.0,
            "race_number": [7,8,9,10,11,12],
            "finish_pattern": "1-2-3", "bet_type": "trifecta"}
    out.append((kama, "蒲郡 後半R 全強化 1-2-3 (best)"))
    # 1ランク絞り (蒲郡 国1≥7)
    out.append(({**kama, "boat1_natl_1_min": 7.0},
                "蒲郡 後半R + 国1≥7 1-2-3"))
    # motor 強化
    for mo in [40.0, 45.0]:
        out.append(({**kama, "boat1_motor_top2_min": mo},
                    f"蒲郡 後半R + motor≥{int(mo)} 国1≥6 1-2-3"))

    # 桐生 別の外艇 head (4-5-x, 4-x-5)
    for combo in ["4-5-1", "4-5-2", "5-4-1", "5-4-2", "4-1-5", "4-2-5"]:
        out.append(({"stadium": [1], "racer_class": [1],
                     "boat1_motor_top2_min": 35.0, "weather_exclude": [3],
                     "boat1_natl_1_min": 6.0,
                     "finish_pattern": combo, "bet_type": "trifecta"},
                    f"桐生 全強化 {combo}"))

    # 戸田 A2 motor≥30 → n 拡張のため 国1 を下げる
    for n1 in [5.0, 5.5, 6.0]:
        out.append(({"stadium": [2], "racer_class": [2],
                     "boat1_motor_top2_min": 30.0,
                     "boat1_natl_1_min": n1,
                     "finish_pattern": "1-2-3", "bet_type": "trifecta"},
                    f"戸田 A2 motor≥30 国1≥{n1} 1-2-3"))

    # 桐生 5-1-2 robust + 蒲郡 1-2-3 robust を portfolio として
    # (1つの WHERE には入らないので、 union 賭式の集計は別計算が必要)
    # ここでは "(桐生 OR 蒲郡) A1 motor≥35 雨除外" を試す → AND/OR混在難しい
    # 替わりに 桐生+蒲郡 stadium IN (1,7) を試す
    out.append(({"stadium": [1, 7], "racer_class": [1],
                  "boat1_motor_top2_min": 35.0, "weather_exclude": [3],
                  "boat1_natl_1_min": 6.0,
                  "finish_pattern": "1-2-3", "bet_type": "trifecta"},
                "桐生+蒲郡 A1 motor≥35 雨除外 国1≥6 1-2-3"))

    return out


def main():
    sd = split_date()
    prev = (sd - timedelta(days=1)).isoformat()
    sd_iso = sd.isoformat()
    print(f"=== ラウンド7 split={sd_iso} ===\n")
    robust = []
    for cond, lbl in batch():
        try:
            tr = bt_range(cond, "0000-01-01", prev)
            te = bt_range(cond, sd_iso, "9999-12-31")
        except Exception as e:
            print(f"  [ERR] {lbl}: {e}")
            continue
        v = verdict(tr["roi"], te["roi"], tr["n"], te["n"])
        icon = "🏆" if v.startswith("🏆") else ("⚠" if v.startswith("⚠") else "❌")
        print(f"  [{icon}] tr n={tr['n']:>4} ROI={tr['roi']:>6.1f}% "
              f"| te n={te['n']:>4} ROI={te['roi']:>6.1f}% | {lbl}")
        if v.startswith("🏆"):
            robust.append((cond, lbl, tr, te))
    print(f"\n=== ラウンド7 robust: {len(robust)} ===")
    for c, l, tr, te in sorted(robust, key=lambda x: -x[3]["roi"]):
        print(f"  tr={tr['roi']:.1f}% (n={tr['n']}) / te={te['roi']:.1f}% (n={te['n']})  {l}")


if __name__ == "__main__":
    main()
