"""ラウンド3: 戸田 A2 deep dive + 蒲郡 別パターン探索"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from explore_auto_loop import bt_range, verdict, split_date  # noqa
from datetime import timedelta


def batch():
    out = []
    # 戸田 A2 1-2-3 ファミリー
    toda_base = {"stadium": [2], "racer_class": [2],
                 "finish_pattern": "1-2-3", "bet_type": "trifecta"}
    out.append((toda_base, "戸田 A2 1-2-3 (broad)"))
    for n1 in [6.0, 7.0]:
        out.append(({**toda_base, "boat1_natl_1_min": n1},
                    f"戸田 A2 国1≥{n1} 1-2-3"))
    for mo in [35.0, 40.0]:
        out.append(({**toda_base, "boat1_motor_top2_min": mo},
                    f"戸田 A2 motor≥{int(mo)} 1-2-3"))
    out.append(({**toda_base, "weather_exclude": [3]},
                "戸田 A2 雨除外 1-2-3"))
    out.append(({**toda_base, "boat1_natl_1_min": 6.0, "weather_exclude": [3],
                  "boat1_motor_top2_min": 35.0},
                "戸田 A2 国1≥6 雨除外 motor≥35 1-2-3 (triple)"))

    # 戸田 A2 別 finish pattern
    for combo in ["1-3-2", "1-2-4", "2-1-3"]:
        out.append(({**toda_base, "finish_pattern": combo,
                     "boat1_natl_1_min": 6.0},
                    f"戸田 A2 国1≥6 {combo}"))

    # 蒲郡 別 finish pattern (1-2-3 が強かったので他も試す)
    kama_base = {"stadium": [7], "racer_class": [1], "boat1_motor_top2_min": 35.0,
                 "weather_exclude": [3], "boat1_natl_1_min": 6.0,
                 "bet_type": "trifecta"}
    for combo in ["1-2-4", "1-3-4", "1-4-2", "1-4-3", "1-2-5", "1-3-5"]:
        out.append(({**kama_base, "finish_pattern": combo},
                    f"蒲郡 A1 motor≥35 雨除外 国1≥6 {combo}"))

    # 蒲郡 base + 当地 2連率
    for lo2 in [50.0, 60.0]:
        out.append(({**kama_base, "boat1_local_2_min": lo2,
                     "finish_pattern": "1-2-3"},
                    f"蒲郡 A1 base + 当地2連率≥{int(lo2)} 1-2-3"))

    # 蒲郡 base × 2号艇 / 3号艇 国1着率
    for b2 in [5.0, 6.0]:
        out.append(({**kama_base, "boat3_natl_1_min": b2,
                     "finish_pattern": "1-2-3"},
                    f"蒲郡 base + 3号艇 国1≥{b2} 1-2-3"))

    # 蒲郡 base + レース番号集約 (上位R, 後半R)
    out.append(({**kama_base, "race_number": [1,2,3,4,5,6],
                 "finish_pattern": "1-2-3"},
                "蒲郡 base 1-6R (前半) 1-2-3"))
    out.append(({**kama_base, "race_number": [7,8,9,10,11,12],
                 "finish_pattern": "1-2-3"},
                "蒲郡 base 7-12R (後半) 1-2-3"))

    # 他会場 + 蒲郡同条件 (再 transfer)
    for sta in [4, 17, 21, 18, 24]:
        out.append(({"stadium": [sta], "racer_class": [1],
                     "boat1_motor_top2_min": 35.0, "weather_exclude": [3],
                     "boat1_natl_1_min": 6.0,
                     "finish_pattern": "1-2-3", "bet_type": "trifecta"},
                    f"transfer: stadium {sta} 同条件 1-2-3"))

    return out


def main():
    sd = split_date()
    prev = (sd - timedelta(days=1)).isoformat()
    sd_iso = sd.isoformat()
    print(f"=== ラウンド3 split={sd_iso} ===\n")
    robust = []
    for cond, lbl in batch():
        tr = bt_range(cond, "0000-01-01", prev)
        te = bt_range(cond, sd_iso, "9999-12-31")
        v = verdict(tr["roi"], te["roi"], tr["n"], te["n"])
        icon = "🏆" if v.startswith("🏆") else ("⚠" if v.startswith("⚠") else "❌")
        print(f"  [{icon}] tr n={tr['n']:>4} ROI={tr['roi']:>6.1f}% "
              f"| te n={te['n']:>4} ROI={te['roi']:>6.1f}% | {lbl}")
        if v.startswith("🏆"):
            robust.append(lbl)
    print(f"\n=== ラウンド3 robust: {len(robust)} ===")
    for l in robust:
        print(f"  {l}")


if __name__ == "__main__":
    main()
