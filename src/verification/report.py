"""検証結果を markdown レポートとして出力する。

実装ポリシー:
  - 出力はマークダウンファイルのみ。**本番戦略コードへの自動反映はしない**。
  - 各手法に「出典 URL」「引用」「条件 dict」「検証結果」を併記し、
    人間レビュアが採否を判断できるようにする。
  - Tier 1 でも `unsupported_conditions` が残っていれば「不完全検証」と
    マークし、過信を防ぐ。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.verification.extract import title_of

TIER_ORDER = ["tier_1", "tier_2", "tier_3", "discard", "insufficient_sample"]
TIER_LABEL = {
    "tier_1": "🏆 Tier 1 (採用候補 / ROI ≥ 150% かつ n ≥ 100)",
    "tier_2": "🥈 Tier 2 (観察 / ROI 120-150%)",
    "tier_3": "🥉 Tier 3 (参考 / ROI 100-120%)",
    "discard": "❌ 棄却 (ROI < 100%)",
    "insufficient_sample": "⚠ サンプル不足 (n<30)",
}


def write_report(methods: list[dict], output_dir: Path) -> Path:
    """検証済 method リストを markdown ファイルに保存し、パスを返す。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now()
    fpath = output_dir / f"verification_{ts:%Y%m%d_%H%M}.md"

    lines: list[str] = []
    lines.append(f"# 検証エージェント レポート  {ts:%Y-%m-%d %H:%M}")
    lines.append("")
    lines.append("> このレポートは候補手法の **発見と DB 検証のみ** を行ったものです。")
    lines.append("> 本番戦略への組込みは人間レビューを経て個別判断してください。")
    lines.append("")

    # サマリ
    counts: dict[str, int] = {}
    for m in methods:
        tier = (m.get("backtest") or {}).get("tier", "n/a")
        counts[tier] = counts.get(tier, 0) + 1
    n_sources = len({m.get("source_url", "") for m in methods})
    lines.append("## サマリ")
    lines.append(f"- 検査ソース URL: {n_sources}")
    lines.append(f"- 抽出された候補手法: {len(methods)}")
    for tier in TIER_ORDER:
        lines.append(f"- {TIER_LABEL[tier]}: **{counts.get(tier, 0)}** 件")
    lines.append("")

    # Tier 別出力
    for tier in TIER_ORDER:
        ms = [m for m in methods if (m.get("backtest") or {}).get("tier") == tier]
        if not ms:
            continue
        # 良い順 (ROI 降順 / 同 ROI なら n 大きい順)
        ms.sort(key=lambda x: (-(x.get("backtest") or {}).get("roi", 0),
                                -(x.get("backtest") or {}).get("n_races", 0)))
        lines.append(f"## {TIER_LABEL[tier]}")
        lines.append("")
        for i, m in enumerate(ms, 1):
            bt = m.get("backtest") or {}
            cond = m.get("conditions", {})
            lines.append(f"### {i}. {title_of(m)}")
            url = m.get("source_url") or "(unknown)"
            lines.append(f"- **出典**: {url}")
            quote = (m.get("source_quote") or "").replace("\n", " ")[:200]
            lines.append(f"- **引用**: > {quote}…")
            lines.append(f"- **条件**: `{cond}`")
            lines.append(f"- **賭け式**: {bt.get('bet_type', '?')}  /  combination = `{bt.get('bet_combo', '?')}`")
            if "error" in bt:
                lines.append(f"- **検証**: {bt['error']}")
            else:
                lines.append(
                    f"- **検証**: n={bt.get('n_races', 0):,} / hit={bt.get('n_hits', 0):,} "
                    f"({bt.get('hit_rate', 0):.1f}%, CI 95% "
                    f"[{bt.get('hit_rate_ci_low', 0):.1f}%, "
                    f"{bt.get('hit_rate_ci_high', 0):.1f}%]) / "
                    f"ROI **{bt.get('roi', 0):.1f}%** / "
                    f"平均配当 {bt.get('avg_payout_on_hit', 0):,.0f}円 / "
                    f"損益 {bt.get('profit', 0):+,d}円"
                )
            unsupp = bt.get("unsupported") or []
            if unsupp:
                lines.append("- **⚠ 不完全検証**: 以下の条件は SQL に落とせていません:")
                for u in unsupp:
                    lines.append(f"  - {u}")
            lines.append(f"- 抽出信頼度: {m.get('confidence', 0):.2f}")
            lines.append("")

    text = "\n".join(lines) + "\n"
    fpath.write_text(text, encoding="utf-8")
    return fpath
