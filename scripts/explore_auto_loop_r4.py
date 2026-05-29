"""ラウンド4: 蒲郡 後半R x 強化条件 + 他会場 後半R 検証"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from explore_auto_loop import bt_range, verdict, split_date  # noqa
from datetime import timedelta


def batch():
    out = []
    # 蒲郡 後半R を更に深掘り
    kama_base = {"stadium": [7], "racer_class": [1], "boat1_motor_top2_min": 35.0,
                 "weather_exclude": [3], "boat1_natl_1_min": 6.0,
                 "finish_pattern": "1-2-3", "bet_type": "trifecta"}
    for rn_set, lbl in [
        ([7, 8, 9, 10, 11, 12], "7-12R"),
        ([9, 10, 11, 12], "9-12R"),
        ([10, 11, 12], "10-12R"),
        ([11, 12], "11-12R"),
        ([12], "12R only"),
        ([8, 9, 10, 11], "8-11R"),
    ]:
        out.append(({**kama_base, "race_number": rn_set},
                    f"蒲郡 base {lbl}"))

    # 蒲郡 後半R + motor 上げ
    for mo in [40.0, 45.0]:
        out.append(({**kama_base, "race_number": [7,8,9,10,11,12],
                     "boat1_motor_top2_min": mo},
                    f"蒲郡 後半R + motor≥{int(mo)}"))

    # 蒲郡 後半R + 国1 上げ
    for n1 in [7.0]:
        out.append(({**kama_base, "race_number": [7,8,9,10,11,12],
                     "boat1_natl_1_min": n1},
                    f"蒲郡 後半R + 国1≥{n1}"))

    # 他会場 後半R 1-2-3 (同条件)
    for sta in [4, 17, 21, 18, 24, 19, 1, 11, 12, 13]:
        out.append(({"stadium": [sta], "racer_class": [1],
                     "boat1_motor_top2_min": 35.0, "weather_exclude": [3],
                     "boat1_natl_1_min": 6.0, "race_number": [7,8,9,10,11,12],
                     "finish_pattern": "1-2-3", "bet_type": "trifecta"},
                    f"transfer 後半R: stadium {sta}"))

    # 戸田 A2 motor 段階的
    for mo in [30.0, 35.0, 40.0]:
        out.append(({"stadium": [2], "racer_class": [2],
                     "boat1_motor_top2_min": mo, "boat1_natl_1_min": 6.0,
                     "finish_pattern": "1-2-3", "bet_type": "trifecta"},
                    f"戸田 A2 motor≥{int(mo)} 国1≥6 1-2-3"))

    # 戸田 A2 雨除外 + motor
    for mo in [30.0, 35.0]:
        out.append(({"stadium": [2], "racer_class": [2],
                     "boat1_motor_top2_min": mo, "weather_exclude": [3],
                     "boat1_natl_1_min": 6.0,
                     "finish_pattern": "1-2-3", "bet_type": "trifecta"},
                    f"戸田 A2 motor≥{int(mo)} 雨除外 国1≥6"))

    # 戸田 後半R も
    out.append(({"stadium": [2], "racer_class": [2],
                  "boat1_natl_1_min": 6.0, "race_number": [7,8,9,10,11,12],
                  "finish_pattern": "1-2-3", "bet_type": "trifecta"},
                "戸田 A2 国1≥6 後半R 1-2-3"))

    return out


def main():
    sd = split_date()
    prev = (sd - timedelta(days=1)).isoformat()
    sd_iso = sd.isoformat()
    print(f"=== ラウンド4 split={sd_iso} ===\n")
    robust = []
    for cond, lbl in batch():
        tr = bt_range(cond, "0000-01-01", prev)
        te = bt_range(cond, sd_iso, "9999-12-31")
        v = verdict(tr["roi"], te["roi"], tr["n"], te["n"])
        icon = "🏆" if v.startswith("🏆") else ("⚠" if v.startswith("⚠") else "❌")
        print(f"  [{icon}] tr n={tr['n']:>4} ROI={tr['roi']:>6.1f}% "
              f"| te n={te['n']:>4} ROI={te['roi']:>6.1f}% | {lbl}")
        if v.startswith("🏆"):
            robust.append((cond, lbl, tr, te))
    print(f"\n=== ラウンド4 robust: {len(robust)} ===")
    for c, l, tr, te in robust:
        print(f"  tr={tr['roi']:.1f}% (n={tr['n']}) / te={te['roi']:.1f}% (n={te['n']})  {l}")


if __name__ == "__main__":
    main()
