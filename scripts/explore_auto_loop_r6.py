"""ラウンド6: 桐生 5-1-2 深掘り + 他外艇パターン"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from explore_auto_loop import bt_range, verdict, split_date  # noqa
from datetime import timedelta


def batch():
    out = []
    # 桐生 5-1-2 robust の refinement
    kiryu = {"stadium": [1], "racer_class": [1], "boat1_motor_top2_min": 35.0,
             "weather_exclude": [3],
             "finish_pattern": "5-1-2", "bet_type": "trifecta"}
    out.append((kiryu, "桐生 base: A1 motor≥35 雨除外 5-1-2"))
    for mo in [40.0, 45.0]:
        out.append(({**kiryu, "boat1_motor_top2_min": mo},
                    f"桐生 motor≥{int(mo)} 5-1-2"))
    for n1 in [6.0, 7.0]:
        out.append(({**kiryu, "boat1_natl_1_min": n1},
                    f"桐生 国1≥{n1} 5-1-2"))
    # 桐生 後半R
    out.append(({**kiryu, "race_number": [7,8,9,10,11,12]},
                "桐生 7-12R 5-1-2"))
    out.append(({**kiryu, "race_number": [1,2,3,4,5,6]},
                "桐生 1-6R 5-1-2"))
    # 桐生 別 finish pattern (5頭 別 2-3着)
    for combo in ["5-1-3", "5-2-1", "5-2-3", "5-3-1", "5-3-2",
                   "5-1-4", "5-4-1", "4-1-2", "4-5-1"]:
        out.append(({**kiryu, "finish_pattern": combo},
                    f"桐生 base {combo}"))
    # 桐生 風強い場 → 風が向きで違うはずだが、ここではざっくり 雨除外なし
    out.append(({"stadium": [1], "racer_class": [1],
                  "boat1_motor_top2_min": 35.0,
                  "finish_pattern": "5-1-2", "bet_type": "trifecta"},
                "桐生 base 雨除外なし 5-1-2"))

    # 他 outer venue 5-1-2
    for sta in [7, 6, 21, 4, 17, 11, 12, 18, 24]:
        out.append(({"stadium": [sta], "racer_class": [1],
                     "boat1_motor_top2_min": 35.0, "weather_exclude": [3],
                     "finish_pattern": "5-1-2", "bet_type": "trifecta"},
                    f"transfer 5-1-2: stadium {sta}"))

    # 6号艇 head も試す
    for sta in [1, 7, 21]:
        for combo in ["6-1-2", "6-1-3", "6-2-1"]:
            out.append(({"stadium": [sta], "racer_class": [1],
                         "boat1_motor_top2_min": 35.0,
                         "weather_exclude": [3],
                         "finish_pattern": combo, "bet_type": "trifecta"},
                        f"stadium {sta} {combo}"))

    return out


def main():
    sd = split_date()
    prev = (sd - timedelta(days=1)).isoformat()
    sd_iso = sd.isoformat()
    print(f"=== ラウンド6 split={sd_iso} ===\n")
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
    print(f"\n=== ラウンド6 robust: {len(robust)} ===")
    for c, l, tr, te in robust:
        print(f"  tr={tr['roi']:.1f}% (n={tr['n']}) / te={te['roi']:.1f}% (n={te['n']})  {l}")


if __name__ == "__main__":
    main()
