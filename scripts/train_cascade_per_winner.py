"""Per-winner カスケード学習 + 評価"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features.builder import build_training_frame
from src.models.train import split_time_ratio
from src.models.cascade_per_winner import (
    prepare_stage2_per_winner, prepare_stage3_per_winner,
    train_per_winner, save_per_winner_cascade,
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.8)
    p.add_argument("--version", default="pw-v0.1")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print(f"[1/4] データ読み込み {args.date_from} .. {args.date_to}")
    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    df_train, df_val = split_time_ratio(df, args.split_ratio)
    print(f"      train: {df_train['race_id'].nunique():,} / val: {df_val['race_id'].nunique():,}")

    print("\n[2/4] Stage 2: 1着レーンごとに分割学習")
    s2_train_dict = prepare_stage2_per_winner(df_train)
    s2_val_dict = prepare_stage2_per_winner(df_val)
    for w in range(1, 7):
        n_t = len(s2_train_dict[w])
        n_v = len(s2_val_dict[w])
        pos = s2_train_dict[w]["target"].mean() if n_t > 0 else 0
        print(f"      winner={w}: train={n_t:6,} val={n_v:5,} pos_rate={pos:.4f}")
    s2_dict = train_per_winner(s2_train_dict, s2_val_dict, name_prefix="stage2")

    print("\n[3/4] Stage 3: 1着レーンごとに分割学習")
    s3_train_dict = prepare_stage3_per_winner(df_train)
    s3_val_dict = prepare_stage3_per_winner(df_val)
    for w in range(1, 7):
        n_t = len(s3_train_dict[w])
        n_v = len(s3_val_dict[w])
        print(f"      winner={w}: train={n_t:6,} val={n_v:5,}")
    s3_dict = train_per_winner(s3_train_dict, s3_val_dict, name_prefix="stage3")

    print("\n[4/4] 保存")
    out = save_per_winner_cascade(s2_dict, s3_dict, args.version)
    print(f"      saved: {out}")


if __name__ == "__main__":
    main()
