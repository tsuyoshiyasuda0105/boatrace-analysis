"""購読者 alert_types 関連の回帰テスト。

backlog event (2026-05-17):
  F1 戦略 (L4_general_f1) を採用ベースに昇格した時、以下が漏れて
  メール通知が届かなかった。

  1. DEFAULT_ALERT_TYPES に L4_general_f1 が含まれていなかった
  2. ALL_ALERT_TYPES に L4_general_f1 / L4_morning_general_f1 が含まれていなかった

このテストは、新規 alert_type を増やしたが対応リストへ追加し忘れた場合、
CI で気付けるようにする。
"""

from src.notifications.subscribers import DEFAULT_ALERT_TYPES, ALL_ALERT_TYPES


# 現在「採用ベース」(本日候補リスト + メール通知の対象) の alert_type 一覧。
# 新戦略を追加したら、ここに alert_type 名も追加すること。
ADOPTED_ALERT_TYPES = {
    # 確定オッズベース
    "L4_SG", "L4_G1", "L4_G2", "L4_G3",
    "L4_general_f1",          # 一般戦 F1 採用ベース (OOS Tier 1, ROI 204%)
    # 朝予測ベース
    "L4_morning_SG", "L4_morning_G1", "L4_morning_G2", "L4_morning_G3",
    "L4_morning_general_f1",
}


def test_default_alert_types_includes_all_adopted():
    """DEFAULT_ALERT_TYPES (新規購読者の初期値) に全採用 alert_type が含まれること。

    含まれないと新規購読者が採用戦略のメール通知を受け取れない。
    """
    default_set = set(DEFAULT_ALERT_TYPES)
    missing = ADOPTED_ALERT_TYPES - default_set
    assert not missing, (
        f"DEFAULT_ALERT_TYPES に採用戦略の alert_type が不足: {missing}\n"
        f"現在の DEFAULT_ALERT_TYPES: {sorted(default_set)}\n"
        f"対応: src/notifications/subscribers.py の DEFAULT_ALERT_TYPES に追加してください"
    )


def test_all_alert_types_includes_all_adopted():
    """ALL_ALERT_TYPES (購読画面の選択肢) に全採用 alert_type が含まれること。

    含まれないとユーザーが購読画面で選択できない。
    """
    all_keys = set(ALL_ALERT_TYPES.keys())
    missing = ADOPTED_ALERT_TYPES - all_keys
    assert not missing, (
        f"ALL_ALERT_TYPES に採用戦略の alert_type が不足: {missing}\n"
        f"対応: src/notifications/subscribers.py の ALL_ALERT_TYPES dict に追加してください"
    )


def test_all_alert_types_has_valid_descriptions():
    """ALL_ALERT_TYPES の各値が空でない説明文であること。"""
    for key, desc in ALL_ALERT_TYPES.items():
        assert isinstance(desc, str) and len(desc) > 5, (
            f"alert_type {key} の説明文が短すぎる/空: {desc!r}"
        )


def test_default_alert_types_is_subset_of_all():
    """DEFAULT_ALERT_TYPES の全てが ALL_ALERT_TYPES に存在すること。"""
    default_set = set(DEFAULT_ALERT_TYPES)
    all_keys = set(ALL_ALERT_TYPES.keys())
    invalid = default_set - all_keys
    assert not invalid, (
        f"DEFAULT_ALERT_TYPES に ALL_ALERT_TYPES に無い key がある: {invalid}"
    )
