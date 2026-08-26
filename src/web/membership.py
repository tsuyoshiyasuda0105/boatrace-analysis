"""Membership and billing persistence helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.db.connection import connect as db_connect

ROLE_RANK = {
    "guest": 0,
    "free_member": 10,
    "beta_member": 15,
    "paid_member": 20,
    "admin": 100,
}
ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}
_SCHEMA_CHECKED = False


# 直近の認証用接続にかかった時間 (秒)。会員だけが払うコストなので、
# 「会員トップが遅い」の原因切り分けに要る (2026-08-26)。
LAST_AUTH_CONNECT_SEC = [0.0]
AUTH_CONNECT_MAX_SEC = [0.0]


def _auth_db_connect():
    """認証クリティカル経路 (ログイン・会員確認) 用のDB接続。

    共有プールが重いページ処理で枯渇 (PoolTimeout) していても、ログインと
    ロール確認だけは通るように、Postgres では短命の直結接続を使う。
    クエリはすべて主キー参照の軽量なものなので接続コストは許容できる。
    SQLite では通常接続と同一。

    ただし「短命」の代償は接続の張り直しで、Render(シンガポール) から
    Supabase(東京) への新規接続は往復 + TLS で実測 2.5 秒。会員は 60 秒ごとに
    これを踏むため、体感が悪い。所要時間を残して切り分けられるようにする。
    """
    import time as _time

    started = _time.perf_counter()
    conn = db_connect(direct=True)
    elapsed = _time.perf_counter() - started
    LAST_AUTH_CONNECT_SEC[0] = round(elapsed, 3)
    if elapsed > AUTH_CONNECT_MAX_SEC[0]:
        AUTH_CONNECT_MAX_SEC[0] = round(elapsed, 3)
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def role_allows(role: str | None, required: str) -> bool:
    return ROLE_RANK.get(role or "guest", 0) >= ROLE_RANK[required]


def normalize_role(role: str | None) -> str:
    role = (role or "").strip()
    return role if role in ROLE_RANK and role != "guest" else "free_member"


def ensure_membership_schema() -> None:
    """Create the staged Supabase Auth membership tables when missing.

    Production already has these tables, but local/test databases may not.
    Keeping this idempotent prevents a missing auth table from turning every
    logged-in request into a 500.
    """
    global _SCHEMA_CHECKED
    if _SCHEMA_CHECKED:
        return
    ts = now_iso()
    with _auth_db_connect() as conn:
        if getattr(conn, "_kind", "") == "postgres":
            conn.execute("SELECT 1 FROM profiles LIMIT 0")
            conn.execute("SELECT 1 FROM user_roles LIMIT 0")
            conn.execute("SELECT 1 FROM subscriptions LIMIT 0")
            _SCHEMA_CHECKED = True
            return
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                email TEXT,
                stripe_customer_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                granted_at TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT '',
                UNIQUE(user_id, role)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id TEXT NOT NULL,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT PRIMARY KEY,
                stripe_price_id TEXT,
                status TEXT,
                current_period_end TEXT,
                cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        try:
            conn.execute("UPDATE user_roles SET created_at = ? WHERE created_at = ''", (ts,))
        except Exception:
            pass
    _SCHEMA_CHECKED = True


def ensure_profile(user_id: str, email: str | None = None) -> None:
    ensure_membership_schema()
    ts = now_iso()
    with _auth_db_connect() as conn:
        conn.execute(
            """
            INSERT INTO profiles (id, email, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                email = COALESCE(EXCLUDED.email, profiles.email),
                updated_at = EXCLUDED.updated_at
            """,
            (user_id, email, ts, ts),
        )
        conn.execute(
            """
            INSERT INTO user_roles (user_id, role, granted_at)
            VALUES (?, 'free_member', ?)
            ON CONFLICT (user_id, role) DO NOTHING
            """,
            (user_id, ts),
        )


def get_effective_role(user_id: str | None) -> str:
    if not user_id:
        return "guest"
    with _auth_db_connect() as conn:
        rows = conn.execute(
            """
            SELECT role
              FROM user_roles
             WHERE user_id = ?
               AND (expires_at IS NULL OR expires_at > ?)
            """,
            (user_id, now_iso()),
        ).fetchall()
    best = "free_member"
    for (role,) in rows:
        if ROLE_RANK.get(role or "", 0) > ROLE_RANK.get(best, 0):
            best = role
    return best


def replace_paid_role_from_subscription(user_id: str, status: str,
                                        current_period_end: str | None = None) -> str:
    ts = now_iso()
    role = "paid_member" if status in ACTIVE_SUBSCRIPTION_STATUSES else "free_member"
    with db_connect() as conn:
        if role == "paid_member":
            conn.execute(
                """
                INSERT INTO user_roles (user_id, role, granted_at, expires_at)
                VALUES (?, 'paid_member', ?, NULL)
                ON CONFLICT (user_id, role) DO UPDATE SET
                    granted_at = EXCLUDED.granted_at,
                    expires_at = NULL
                """,
                (user_id, ts),
            )
        else:
            conn.execute(
                """
                UPDATE user_roles
                   SET expires_at = COALESCE(?, ?)
                 WHERE user_id = ? AND role = 'paid_member'
                """,
                (current_period_end, ts, user_id),
            )
    return role


def get_billing_profile(user_id: str) -> dict[str, Any]:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT id, email, stripe_customer_id FROM profiles WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return {"id": user_id, "email": None, "stripe_customer_id": None}
    return {"id": row[0], "email": row[1], "stripe_customer_id": row[2]}


def list_membership_overview(limit: int = 200) -> list[dict[str, Any]]:
    with db_connect() as conn:
        profile_rows = conn.execute(
            """
            SELECT id, email, stripe_customer_id, created_at, updated_at
              FROM profiles
             ORDER BY updated_at DESC, created_at DESC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    if not profile_rows:
        return []

    user_ids = [str(row[0]) for row in profile_rows if row and row[0]]
    if not user_ids:
        return []

    placeholders = ",".join("?" for _ in user_ids)
    with db_connect() as conn:
        role_rows = conn.execute(
            f"""
            SELECT user_id, role, expires_at, granted_at
              FROM user_roles
             WHERE user_id IN ({placeholders})
             ORDER BY granted_at DESC
            """,
            tuple(user_ids),
        ).fetchall()
        subscription_rows = conn.execute(
            f"""
            SELECT user_id, stripe_subscription_id, stripe_price_id, status,
                   current_period_end, cancel_at_period_end, updated_at
              FROM subscriptions
             WHERE user_id IN ({placeholders})
             ORDER BY updated_at DESC
            """,
            tuple(user_ids),
        ).fetchall()

    role_map: dict[str, list[dict[str, Any]]] = {}
    for user_id, role, expires_at, granted_at in role_rows:
        role_map.setdefault(str(user_id), []).append(
            {
                "role": str(role),
                "expires_at": expires_at,
                "granted_at": granted_at,
            }
        )

    subscription_map: dict[str, dict[str, Any]] = {}
    for user_id, sub_id, price_id, status, period_end, cancel_at_period_end, updated_at in subscription_rows:
        key = str(user_id)
        if key in subscription_map:
            continue
        subscription_map[key] = {
            "stripe_subscription_id": sub_id,
            "stripe_price_id": price_id,
            "status": status,
            "current_period_end": period_end,
            "cancel_at_period_end": bool(cancel_at_period_end),
            "updated_at": updated_at,
        }

    overview: list[dict[str, Any]] = []
    now = now_iso()
    for user_id, email, stripe_customer_id, created_at, updated_at in profile_rows:
        key = str(user_id)
        raw_roles = role_map.get(key, [])
        active_roles = [
            item["role"]
            for item in raw_roles
            if not item.get("expires_at") or str(item["expires_at"]) > now
        ]
        effective_role = "free_member"
        for role in active_roles:
            if ROLE_RANK.get(role, 0) > ROLE_RANK.get(effective_role, 0):
                effective_role = role
        overview.append(
            {
                "user_id": key,
                "email": email,
                "stripe_customer_id": stripe_customer_id,
                "created_at": created_at,
                "updated_at": updated_at,
                "effective_role": effective_role,
                "roles": raw_roles,
                "subscription": subscription_map.get(key),
            }
        )
    return overview


def set_stripe_customer(user_id: str, stripe_customer_id: str) -> None:
    with db_connect() as conn:
        conn.execute(
            "UPDATE profiles SET stripe_customer_id = ?, updated_at = ? WHERE id = ?",
            (stripe_customer_id, now_iso(), user_id),
        )


def get_user_id_by_stripe_customer(stripe_customer_id: str) -> str | None:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT id FROM profiles WHERE stripe_customer_id = ?",
            (stripe_customer_id,),
        ).fetchone()
    return row[0] if row else None


def upsert_subscription(user_id: str, stripe_customer_id: str | None,
                        subscription: dict[str, Any]) -> None:
    sub_id = str(subscription.get("id") or "")
    if not sub_id:
        return
    status = str(subscription.get("status") or "")
    price_id = None
    try:
        items = subscription.get("items", {}).get("data", [])
        if items:
            price_id = items[0].get("price", {}).get("id")
    except AttributeError:
        price_id = None
    current_period_end = _stripe_ts_to_iso(subscription.get("current_period_end"))
    cancel_at_period_end = bool(subscription.get("cancel_at_period_end") or False)
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (
                user_id, stripe_customer_id, stripe_subscription_id, stripe_price_id,
                status, current_period_end, cancel_at_period_end, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                stripe_customer_id = EXCLUDED.stripe_customer_id,
                stripe_price_id = EXCLUDED.stripe_price_id,
                status = EXCLUDED.status,
                current_period_end = EXCLUDED.current_period_end,
                cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                updated_at = EXCLUDED.updated_at
            """,
            (
                user_id,
                stripe_customer_id,
                sub_id,
                price_id,
                status,
                current_period_end,
                int(cancel_at_period_end),
                now_iso(),
            ),
        )
    replace_paid_role_from_subscription(user_id, status, current_period_end)


def _stripe_ts_to_iso(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OSError):
        return None
