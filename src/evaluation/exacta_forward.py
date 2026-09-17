"""二連単の前向き記録 (forward_exacta_picks)。

2026-09-17〜18 の検証で、学習外 12,000 レースの中で 100% に最も近かったのは
「モデルの二連単本命 1 点 × 選別条件 (進入変更リスク 10% 以下・女性選手なし・
回収率の悪い 8 場を除く)」の 96.4% だった。過去の数字だけでは決められないので、
毎晩 翌日の候補を作って残し、あとから結果と締切5分前オッズを埋めて確かめる。

流れ:
  build_picks()  PC 01:00  翌日の全レースについて本命 1 点を決め、選別条件の
                          当否も一緒に残す (条件外も残すのは比較のため)
  settle()       翌晩以降  結果 (race_payouts) と T-5min の odds_exacta を埋める
  summarize()    いつでも  選別あり/なし別の回収率を出す

確率の作り方は scratchpad の検証 (mc_top4_ev_v2.py) と同じ:
1着確率で 1 着を、2着以内率・3着以内率に合うよう調整した強さで 2・3 着を
順に決める Plackett-Luce。二連単は 1-2 着が同じ 120 通りを足し合わせる。
"""
from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable, Optional

import numpy as np

JST = timezone(timedelta(hours=9))
STRATEGY = "exacta_top1_v08"
MODEL_VERSION = "v0.8"
# scripts/odds_scheduler.EXCLUDE_B_VENUES と同じ 8 場 (L4 で回収率が確実に負だった場)
BAD_VENUES = frozenset({2, 4, 7, 8, 10, 19, 21, 24})
ENTRY_CHANGE_MAX = 10.0  # % 表記 (asof_race_features.bN_entry_change_rate)

_PERMS = list(itertools.permutations(range(6), 3))
_A = np.array([p[0] for p in _PERMS])
_B = np.array([p[1] for p in _PERMS])
_C = np.array([p[2] for p in _PERMS])
_OB = np.eye(6)[_B]
_OC = np.eye(6)[_C]
_EXACTA = [(a, b) for a in range(6) for b in range(6) if a != b]
_AGG = np.zeros((120, 30))
for _t, (_a, _b, _d) in enumerate(_PERMS):
    _AGG[_t, _EXACTA.index((_a, _b))] = 1.0
EXACTA_LABELS = [f"{a + 1}-{b + 1}" for a, b in _EXACTA]


def _project(t: np.ndarray, cap: np.ndarray) -> np.ndarray:
    """各艇の上限 cap を守りながら合計 1 に配り直す。

    v0.8 の 2着以内率・3着以内率は別々に較正されているため
    「ちょうど2着になる確率 > 1 − 1着確率」というありえない値が混じる。
    そのまま反復調整すると発散するので、あふれた分を余裕のある艇へ回す。
    """
    t = t / t.sum(1, keepdims=True)
    for _ in range(30):
        t = np.minimum(t, cap)
        deficit = 1.0 - t.sum(1, keepdims=True)
        room = np.where(t < cap - 1e-9, t, 0.0)
        t = t + deficit * room / np.clip(room.sum(1, keepdims=True), 1e-12, None)
    return t


def _joint(s1: np.ndarray, s2: np.ndarray, s3: np.ndarray) -> np.ndarray:
    first = s1[:, _A]
    second = s2[:, _B] / np.clip(1.0 - s2[:, _A], 1e-9, None)
    third = s3[:, _C] / np.clip(1.0 - s3[:, _A] - s3[:, _B], 1e-9, None)
    return first * second * third


def _update(s: np.ndarray, target: np.ndarray, marginal: np.ndarray) -> np.ndarray:
    s = s * np.sqrt(target / np.clip(marginal, 1e-12, None))
    s = np.clip(s, 1e-7, None)
    s = s / s.sum(1, keepdims=True)
    s = np.minimum(s, 0.995)
    return s / s.sum(1, keepdims=True)


def trifecta_probabilities(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray, iters: int = 40) -> np.ndarray:
    """(races, 6) の 1着 / 2着以内 / 3着以内 確率 → (races, 120) の三連単確率。"""
    p1 = np.clip(np.asarray(p1, float), 1e-6, None)
    p1 = p1 / p1.sum(1, keepdims=True)
    p2 = np.clip(np.asarray(p2, float), 1e-6, None)
    p2 = p2 / p2.sum(1, keepdims=True) * 2.0
    p3 = np.clip(np.asarray(p3, float), 1e-6, None)
    p3 = p3 / p3.sum(1, keepdims=True) * 3.0
    p2 = np.clip(np.maximum(p2, p1), None, 1.0)
    p3 = np.clip(np.maximum(p3, p2), None, 1.0)
    t2 = _project(np.clip(p2 - p1, 1e-4, None), np.clip(1.0 - p1, 1e-4, None) * 0.98)
    t3 = _project(np.clip(p3 - p2, 1e-4, None), np.clip(1.0 - p1 - t2, 1e-4, None) * 0.98)
    s1, s2, s3 = p1.copy(), t2.copy(), t3.copy()
    for _ in range(iters):
        s2 = _update(s2, t2, _joint(s1, s2, s3) @ _OB)
        s3 = _update(s3, t3, _joint(s1, s2, s3) @ _OC)
    return _joint(s1, s2, s3)


def exacta_probabilities(p1, p2, p3) -> np.ndarray:
    """(races, 30) の二連単確率。列の並びは EXACTA_LABELS。"""
    return trifecta_probabilities(p1, p2, p3) @ _AGG


def passes_selection(jcd: Optional[int], female_present: Optional[int],
                     entry_change_rates: Iterable[Optional[float]]) -> tuple[bool, dict[str, Any]]:
    """選別条件の当否と、判定に使った値を返す。判定できない項目があれば不合格。"""
    rates = list(entry_change_rates)
    ecr_max = None if (not rates or any(r is None for r in rates)) else max(float(r) for r in rates)
    detail = {
        "jcd": jcd,
        "female_present": female_present,
        "entry_change_max": ecr_max,
    }
    ok = (
        jcd is not None and int(jcd) not in BAD_VENUES
        and female_present == 0
        and ecr_max is not None and ecr_max <= ENTRY_CHANGE_MAX
    )
    return bool(ok), detail


def build_picks(races: list[dict[str, Any]], now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """レースごとの入力から記録行を作る。

    races の各要素:
      race_id, race_date, jcd, female_present,
      prob_first / prob_top_2 / prob_top_3: 艇番 1〜6 順の list (長さ 6),
      entry_change_rates: 2〜6 号艇の進入変更率 list (欠けは None)
    """
    if not races:
        return []
    p1 = np.array([r["prob_first"] for r in races], float)
    p2 = np.array([r["prob_top_2"] for r in races], float)
    p3 = np.array([r["prob_top_3"] for r in races], float)
    pe = exacta_probabilities(p1, p2, p3)
    created = (now or datetime.now(JST)).isoformat(timespec="seconds")
    out = []
    for i, r in enumerate(races):
        order = np.argsort(-pe[i])
        top = int(order[0])
        selected, detail = passes_selection(r.get("jcd"), r.get("female_present"), r.get("entry_change_rates", []))
        out.append({
            "race_id": r["race_id"],
            "race_date": r["race_date"],
            "strategy": STRATEGY,
            "combination": EXACTA_LABELS[top],
            "prob": float(pe[i, top]),
            "selected": 1 if selected else 0,
            "detail": json.dumps({
                **detail,
                "top3": [(EXACTA_LABELS[int(k)], round(float(pe[i, k]), 4)) for k in order[:3]],
                "prob_first_boat1": round(float(p1[i, 0]), 4),
            }, ensure_ascii=False),
            "created_at": created,
        })
    return out


# ---------------------------------------------------------------- 保存

def ensure_table(conn) -> None:
    is_pg = getattr(conn, "_kind", "") == "postgres"
    ddl = """
        CREATE TABLE IF NOT EXISTS forward_exacta_picks (
            race_id     TEXT NOT NULL,
            race_date   TEXT NOT NULL,
            strategy    TEXT NOT NULL,
            combination TEXT NOT NULL,
            prob        REAL,
            selected    INTEGER NOT NULL DEFAULT 0,
            detail      TEXT,
            created_at  TEXT NOT NULL,
            t5_odds     REAL,
            payout      INTEGER,
            hit         INTEGER,
            settled     INTEGER NOT NULL DEFAULT 0,
            settled_at  TEXT,
            PRIMARY KEY (race_id, strategy)
        );
        CREATE INDEX IF NOT EXISTS idx_forward_exacta_picks_date
            ON forward_exacta_picks(race_date, settled);
    """
    if is_pg:
        ddl += "\n        ALTER TABLE forward_exacta_picks ENABLE ROW LEVEL SECURITY;"
    conn.executescript(ddl)


def save_picks(conn, picks: list[dict[str, Any]]) -> int:
    """同じ (race_id, strategy) は残す (前夜の判断を後から上書きしない)。"""
    if not picks:
        return 0
    ensure_table(conn)
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO forward_exacta_picks
            (race_id, race_date, strategy, combination, prob, selected, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(p["race_id"], p["race_date"], p["strategy"], p["combination"], p["prob"],
          p["selected"], p["detail"], p["created_at"]) for p in picks],
    )
    conn.commit()
    return len(picks)


def settle(conn, payouts: dict[tuple[str, str], int], t5_odds: dict[tuple[str, str], float],
           until_date: str, now: Optional[datetime] = None) -> int:
    """結果が分かった行に払戻と T-5min オッズを入れる。

    payouts: {(race_id, combination): 払戻(100円あたり)} (二連単、当たり目だけ)
    t5_odds: {(race_id, combination): オッズ}
    結果が payouts に無いレース (未確定・中止) は触らない。
    """
    ensure_table(conn)
    rows = conn.execute(
        "SELECT race_id, strategy, combination FROM forward_exacta_picks "
        "WHERE settled = 0 AND race_date <= ?",
        (until_date,),
    ).fetchall()
    settled_at = (now or datetime.now(JST)).isoformat(timespec="seconds")
    done_races = {rid for rid, _ in payouts}
    updates = []
    for race_id, strategy, comb in rows:
        if race_id not in done_races:
            continue
        pay = int(payouts.get((race_id, comb), 0))
        updates.append((pay, 1 if pay > 0 else 0, t5_odds.get((race_id, comb)), settled_at, race_id, strategy))
    if updates:
        conn.executemany(
            "UPDATE forward_exacta_picks SET payout = ?, hit = ?, t5_odds = ?, settled = 1, settled_at = ? "
            "WHERE race_id = ? AND strategy = ?",
            updates,
        )
        conn.commit()
    return len(updates)


def summarize(conn, strategy: str = STRATEGY) -> list[dict[str, Any]]:
    """選別あり/なし別に、確定分の件数・的中・回収率を返す。"""
    rows = conn.execute(
        "SELECT selected, COUNT(*), SUM(hit), SUM(payout), MIN(race_date), MAX(race_date) "
        "FROM forward_exacta_picks WHERE strategy = ? AND settled = 1 GROUP BY selected ORDER BY selected DESC",
        (strategy,),
    ).fetchall()
    out = []
    for selected, n, hits, pay, d0, d1 in rows:
        n = int(n or 0)
        out.append({
            "selected": int(selected),
            "races": n,
            "hits": int(hits or 0),
            "hit_rate": (float(hits or 0) / n * 100.0) if n else 0.0,
            "roi": (float(pay or 0) / (n * 100.0) * 100.0) if n else 0.0,
            "from": d0,
            "to": d1,
        })
    return out
