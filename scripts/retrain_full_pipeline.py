"""
全データで再学習パイプライン

順:
  1. 期間内データで build_training_frame
  2. ranker_<version> 学習 + 較正
  3. cascade-<version> 学習 (stage 2/3)
  4. pw-<version> 学習 (per-winner 6モデル)
  5. joint calibration fit (cascade と pw それぞれ)

usage:
    python scripts/retrain_full_pipeline.py --date-from 2022-05-08 --date-to 2026-05-08 \\
        --split-ratio 0.85 --version v0.4
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.features.builder import build_training_frame
from src.models.train import train_ranker, predict_probs, save_artifact, split_time_ratio
from src.models.calibration import fit_calibrators, save_calibrators
from src.models.cascade import (
    prepare_stage2_data, prepare_stage3_data,
    train_classifier, save_cascade, predict_trifecta_joint,
)
from src.models.cascade_per_winner import (
    prepare_stage2_per_winner, prepare_stage3_per_winner,
    train_per_winner, save_per_winner_cascade, predict_trifecta_per_winner,
)
from src.models.joint_calibration import (
    build_calibration_data, fit_joint_calibrator, save_joint_calibrator,
)
from src.evaluation.evaluate_with_payouts import load_payouts as load_payouts_pf


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.85)
    p.add_argument("--version", default="v0.4")
    p.add_argument("--skip-ranker", action="store_true")
    p.add_argument("--skip-cascade", action="store_true")
    p.add_argument("--skip-pw", action="store_true")
    p.add_argument("--skip-calibration", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print(f"[1/6] データ読み込み {args.date_from} .. {args.date_to}")
    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    df_train, df_val = split_time_ratio(df, args.split_ratio)
    print(f"      train: {df_train['race_id'].nunique():,} races / val: {df_val['race_id'].nunique():,} races")

    # ---- Stage 1: Ranker
    if not args.skip_ranker:
        print(f"\n[2/6] Stage 1 Ranker (v{args.version})")
        model, feature_cols = train_ranker(df_train, df_val)
        # 較正
        df_calib = df_train[df_train["race_date"] >= df_train["race_date"].quantile(0.8)]
        df_calib_pred = predict_probs(model, df_calib, feature_cols)
        try:
            calibrators = fit_calibrators(df_calib_pred)
            save_calibrators(calibrators, args.version)
        except Exception as e:
            print(f"      [WARN] 較正失敗: {e}")
            calibrators = None
        out = save_artifact(model, feature_cols, args.version, calibrators=calibrators)
        print(f"      saved: {out}")

    # ---- Stage 2/3 unified Cascade
    if not args.skip_cascade:
        print(f"\n[3/6] Unified Cascade (cascade-{args.version})")
        s2_train = prepare_stage2_data(df_train)
        s2_val = prepare_stage2_data(df_val)
        s2_model, s2_features = train_classifier(s2_train, s2_val, name="stage2")
        s3_train = prepare_stage3_data(df_train)
        s3_val = prepare_stage3_data(df_val)
        s3_model, s3_features = train_classifier(s3_train, s3_val, name="stage3")
        out = save_cascade(s2_model, s2_features, s3_model, s3_features, f"cascade-{args.version}")
        print(f"      saved: {out}")

    # ---- Per-Winner cascade
    if not args.skip_pw:
        print(f"\n[4/6] PerWinner Cascade (pw-{args.version})")
        s2_train_d = prepare_stage2_per_winner(df_train)
        s2_val_d = prepare_stage2_per_winner(df_val)
        for w in range(1, 7):
            print(f"      stage2 winner={w}: train={len(s2_train_d[w]):,} val={len(s2_val_d[w]):,}")
        s2_dict = train_per_winner(s2_train_d, s2_val_d, name_prefix="stage2")
        s3_train_d = prepare_stage3_per_winner(df_train)
        s3_val_d = prepare_stage3_per_winner(df_val)
        for w in range(1, 7):
            print(f"      stage3 winner={w}: train={len(s3_train_d[w]):,} val={len(s3_val_d[w]):,}")
        s3_dict = train_per_winner(s3_train_d, s3_val_d, name_prefix="stage3")
        out = save_per_winner_cascade(s2_dict, s3_dict, f"pw-{args.version}")
        print(f"      saved: {out}")

    # ---- Joint Calibration (use train data)
    if not args.skip_calibration:
        print(f"\n[5/6] Joint Calibration")
        from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs
        artifact = load_artifact(args.version)
        # cascade load
        from src.models.cascade import load_cascade
        from src.models.cascade_per_winner import load_per_winner_cascade
        cas = load_cascade(f"cascade-{args.version}")
        pw = load_per_winner_cascade(f"pw-{args.version}")

        # 較正用に train 末尾20%
        df_calib_full = df_train[df_train["race_date"] >= df_train["race_date"].quantile(0.8)].copy()
        print(f"      calibration source: {df_calib_full['race_id'].nunique():,} races")
        df_calib_pred = predict_with_probs(artifact, df_calib_full)

        # トリフェクタの実績
        d_min = df_calib_full["race_date"].min().date()
        d_max = df_calib_full["race_date"].max().date()
        tri_p_df = load_payouts_pf(config.DB_PATH, d_min, d_max, "trifecta")
        payouts = {r["race_id"]: (r["combination"], float(r["payout"]))
                   for _, r in tri_p_df.iterrows()}

        # cascade calibration
        cas_pred = predict_trifecta_joint(
            df_calib_pred,
            cas["stage2_model"], cas["stage2_features"],
            cas["stage3_model"], cas["stage3_features"],
        )
        cas_calib_data = build_calibration_data(cas_pred, payouts)
        if len(cas_calib_data) > 100:
            iso, _ = fit_joint_calibrator(cas_calib_data)
            save_joint_calibrator(iso, f"cascade-{args.version}")
            print(f"      cascade joint calibrator saved")
        else:
            print(f"      cascade calibration skipped (insufficient data: {len(cas_calib_data)})")

        # per-winner calibration
        pw_pred = predict_trifecta_per_winner(
            df_calib_pred, pw["s2"], pw["s3"],
            fallback_s2_model=cas["stage2_model"], fallback_s2_features=cas["stage2_features"],
            fallback_s3_model=cas["stage3_model"], fallback_s3_features=cas["stage3_features"],
        )
        pw_calib_data = build_calibration_data(pw_pred, payouts)
        if len(pw_calib_data) > 100:
            iso, _ = fit_joint_calibrator(pw_calib_data)
            save_joint_calibrator(iso, f"pw-{args.version}")
            print(f"      pw joint calibrator saved")

    print(f"\n[6/6] 完了 (version={args.version})")


if __name__ == "__main__":
    main()
