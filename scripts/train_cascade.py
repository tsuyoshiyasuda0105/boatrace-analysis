"""
カスケードモデル (Stage 2 + Stage 3) の学習

Stage 1 は既存の ranker_<version>.pkl を再利用 (P(1着) 推論)。
ここでは Stage 2 (P(2着 | 1着)) と Stage 3 (P(3着 | 1着, 2着)) のみ学習。

usage:
    python scripts/train_cascade.py --base v0.2 \
        --date-from 2025-05-08 --date-to 2026-05-08 --split-ratio 0.8 \
        --version cascade-v0.1
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import config
from src.features.builder import build_training_frame
from src.models.train import split_time_ratio
from src.models.cascade import (
    prepare_stage2_data, prepare_stage3_data,
    train_classifier, save_cascade,
)
from src.models.pattern_features import build_pattern_2nd, build_pattern_3rd


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="v0.2", help="Stage1 として使う ranker_*.pkl のバージョン")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.8)
    p.add_argument("--version", default="cascade-v0.1")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    print(f"[1/6] 学習データ読み込み {args.date_from} .. {args.date_to}")
    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    df_train, df_val = split_time_ratio(df, args.split_ratio)
    print(f"      train: {len(df_train):,} 行 / {df_train['race_id'].nunique():,} レース")
    print(f"      val  : {len(df_val):,} 行 / {df_val['race_id'].nunique():,} レース")

    print("[2/6] 経験的パターン (会場×1着レーン×候補レーン) 集計 (train データのみ)")
    pattern_2nd = build_pattern_2nd(df_train)
    pattern_3rd = build_pattern_3rd(df_train)
    print(f"      pattern_2nd: {len(pattern_2nd):,} cells (24 × 6 × 5 = 720 max)")
    print(f"      pattern_3rd: {len(pattern_3rd):,} cells (24 × 6 × 5 × 4 = 2880 max)")

    print("[3/6] Stage 2 (2着) 学習データ生成 ...")
    s2_train = prepare_stage2_data(df_train, pattern_2nd=pattern_2nd)
    s2_val = prepare_stage2_data(df_val, pattern_2nd=pattern_2nd)
    print(f"      train: {len(s2_train):,}  pos={s2_train['target'].mean():.4f}")
    print(f"      val  : {len(s2_val):,}  pos={s2_val['target'].mean():.4f}")

    print("[4/6] Stage 2 LightGBM 学習")
    s2_model, s2_features = train_classifier(s2_train, s2_val, name="stage2")

    print("[5/6] Stage 3 (3着) 学習データ生成 + 学習")
    s3_train = prepare_stage3_data(df_train, pattern_2nd=pattern_2nd, pattern_3rd=pattern_3rd)
    s3_val = prepare_stage3_data(df_val, pattern_2nd=pattern_2nd, pattern_3rd=pattern_3rd)
    print(f"      train: {len(s3_train):,}  pos={s3_train['target'].mean():.4f}")
    print(f"      val  : {len(s3_val):,}  pos={s3_val['target'].mean():.4f}")
    s3_model, s3_features = train_classifier(s3_train, s3_val, name="stage3")

    print("[6/6] 保存")
    out = save_cascade(s2_model, s2_features, s3_model, s3_features, args.version,
                       pattern_2nd=pattern_2nd, pattern_3rd=pattern_3rd)
    print(f"      saved: {out}")


if __name__ == "__main__":
    main()
