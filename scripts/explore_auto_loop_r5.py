"""ラウンド5: 別賭式 / 外艇1着 / 戸田A2拡張"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from explore_auto_loop import bt_range, verdict, split_date  # noqa
from datetime import timedelta


def batch():
    out = []
    # 蒲郡 robust → 別賭式
    kama = {"stadium": [7], "racer_class": [1], "boat1_motor_top2_min": 35.0,
            "weather_exclude": [3], "boat1_natl_1_min": 6.0}
    # 単勝 (1号艇)
    out.append(({**kama, "finish_pattern": "1", "bet_type": "win"},
                "蒲郡 base 単勝 1"))
    # 2連単 1-2
    out.append(({**kama, "finish_pattern": "1-2", "bet_type": "exacta"},
                "蒲郡 base 2連単 1-2"))
    # 2連単 1-3
    out.append(({**kama, "finish_pattern": "1-3", "bet_type": "exacta"},
                "蒲郡 base 2連単 1-3"))
    # 2連複 1=2
    out.append(({**kama, "finish_pattern": "1=2", "bet_type": "quinella"},
                "蒲郡 base 2連複 1=2"))
    # 3連単 別パターン
    for combo in ["1-2-3", "1-3-2"]:
        out.append(({**kama, "finish_pattern": combo, "bet_type": "trifecta",
                     "race_number": [7,8,9,10,11,12]},
                    f"蒲郡 base 7-12R {combo}"))

    # 戸田 A2 国1≥6 拡張
    toda = {"stadium": [2], "racer_class": [2], "boat1_natl_1_min": 6.0,
            "finish_pattern": "1-2-3", "bet_type": "trifecta"}
    # motor 帯網羅 (10刻み)
    for mo in [25.0, 28.0, 30.0, 32.0]:
        out.append(({**toda, "boat1_motor_top2_min": mo},
                    f"戸田 A2 国1≥6 motor≥{mo}"))
    # 2号艇 / 3号艇 重ねがけ
    for b2 in [35.0, 40.0]:
        out.append(({**toda, "boat2_top2_min": b2},
                    f"戸田 A2 国1≥6 2号艇top2≥{int(b2)}"))
    # 別 finish pattern
    for combo in ["1-3-2", "1-2-4", "1-4-2"]:
        out.append(({**toda, "finish_pattern": combo},
                    f"戸田 A2 国1≥6 {combo}"))
    # 別賭式
    out.append(({**toda, "finish_pattern": "1-2", "bet_type": "exacta"},
                "戸田 A2 国1≥6 2連単 1-2"))
    out.append(({**toda, "finish_pattern": "1=2", "bet_type": "quinella"},
                "戸田 A2 国1≥6 2連複 1=2"))

    # 外艇 head 全会場 1-2-3着 (発見されてない可能性)
    # 4頭 / 5頭 を当地→さらに class / motor で絞る (n が出る venue)
    for sta in [7, 6, 21, 4, 17, 1]:
        for combo in ["4-1-2", "4-1-3", "5-1-2", "5-1-3"]:
            out.append(({"stadium": [sta], "racer_class": [1],
                         "boat1_motor_top2_min": 35.0,
                         "weather_exclude": [3],
                         "finish_pattern": combo, "bet_type": "trifecta"},
                        f"{['','桐生','戸田','江戸川','平和島','多摩川','浜名湖','蒲郡'][sta] if sta<=7 else ['','','','','','','','蒲郡','常滑','津','三国','びわこ','住之江','尼崎','鳴門','丸亀','児島','宮島','徳山','下関','若松','芦屋'][sta] if sta<22 else ['','','','','','','','','','','','','','','','','','','','','','','福岡','唐津','大村'][sta]} A1 motor≥35 雨除外 {combo}"))

    # 男性のみ × 蒲郡 (Venus 反対)
    out.append(({"stadium": [7], "racer_class": [1],
                  "boat1_motor_top2_min": 35.0,
                  "weather_exclude": [3], "boat1_natl_1_min": 6.0,
                  "no_female": True,
                  "finish_pattern": "1-2-3", "bet_type": "trifecta"},
                "蒲郡 base + 男性のみ 1-2-3"))

    return out


def main():
    sd = split_date()
    prev = (sd - timedelta(days=1)).isoformat()
    sd_iso = sd.isoformat()
    print(f"=== ラウンド5 split={sd_iso} ===\n")
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
    print(f"\n=== ラウンド5 robust: {len(robust)} ===")
    for c, l, tr, te in robust:
        print(f"  tr={tr['roi']:.1f}% (n={tr['n']}) / te={te['roi']:.1f}% (n={te['n']})  {l}")


if __name__ == "__main__":
    main()
