# -*- coding: utf-8 -*-
"""pytest 共通の初期化 (リポジトリルート conftest)。

.env の DATABASE_URL が本番 Postgres を指しているため、テストで
`from config import ...` や `from src.db.connection import connect` を
呼ぶと、config.py の load_dotenv が値を注入し、`connect()` が本番 DB を
返してしまう。読み取りだけならほぼ実害は無いが、書き込むテストが 1 つでも
入れば本番データを壊す。

pytest はルート conftest.py を**テスト収集より先に**必ず一度だけ import する。
そこで **project モジュールを import する前に** DATABASE_URL に空文字を入れて、
`load_dotenv(override=False)` に「既に値がある」と誤認させ、再注入を防ぐ
(空文字も存在扱いになる)。以降の connect() はローカル SQLite にフォールバックする。

さらに session 開始時に接続種別を実測し、Postgres 相当が返ってきたら
セッションを止める。ルート conftest を経由しない実行方法や、別モジュールが
先に load_dotenv を強制した場合に備えた最終防御。
"""
import os

# ↓ ここより上に、config や src の import を書いてはいけない ↓
os.environ["DATABASE_URL"] = ""


def pytest_configure(config):  # noqa: ARG001 - pytest hook signature
    # 遅延 import: 上の設定が反映されてから初めて DB 層を触る。
    from src.db.connection import connect

    conn = connect()
    kind = type(conn).__name__
    try:
        conn.close()
    except Exception:  # noqa: BLE001 - close 失敗は主目的ではない
        pass
    if "Pg" in kind:
        raise RuntimeError(
            "test session refused: connect() returned a Postgres connection "
            f"({kind}). DATABASE_URL is bleeding into the test process; "
            "the root conftest.py must clear it before any project import."
        )
