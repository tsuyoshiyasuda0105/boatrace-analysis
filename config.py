"""
BOATRACE データ分析プロジェクト - 設定

環境変数で上書き可能にしておくと、本番/開発の切り替えが楽。
"""
from pathlib import Path
import os

# ============================================================
# ディレクトリ
# ============================================================

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MASTER_DIR = ROOT_DIR / "master"

# Layer 1: 公式DLファイル保存先
OFFICIAL_PROGRAMS_DIR = RAW_DIR / "programs"   # 番組表 (LZH)
OFFICIAL_RESULTS_DIR = RAW_DIR / "results"     # 競走成績 (LZH)
OFFICIAL_FAN_DIR = RAW_DIR / "fan"             # ファン手帳 (LZH)

# Layer 2: Open API レスポンス保存先 (任意。生JSONを残しておくとデバッグが楽)
OPENAPI_RAW_DIR = RAW_DIR / "openapi"

# Layer 3: 直前情報スクレイピング保存先
BEFOREINFO_DIR = RAW_DIR / "beforeinfo"

# DB
DB_PATH = os.getenv("BOATRACE_DB_PATH", str(DATA_DIR / "boatrace.db"))

# ============================================================
# データソース URL
# ============================================================

# Open API (有志運用・MIT License)
OPENAPI_PROGRAMS_URL = "https://boatraceopenapi.github.io/programs/v2/{year}/{date}.json"
OPENAPI_PREVIEWS_URL = "https://boatraceopenapi.github.io/previews/v2/{year}/{date}.json"
OPENAPI_RESULTS_URL = "https://boatraceopenapi.github.io/results/v2/{year}/{date}.json"

# 公式DLファイル (LZH圧縮・Shift_JIS)
# 番組表:  https://www1.mbrace.or.jp/od2/B/{YYYYMM}/b{YYMMDD}.lzh
# 競走成績: https://www1.mbrace.or.jp/od2/K/{YYYYMM}/k{YYMMDD}.lzh
OFFICIAL_PROGRAMS_URL = "https://www1.mbrace.or.jp/od2/B/{yyyymm}/b{yymmdd}.lzh"
OFFICIAL_RESULTS_URL = "https://www1.mbrace.or.jp/od2/K/{yyyymm}/k{yymmdd}.lzh"

# 公式サイト 直前情報 (Layer 3)
BEFOREINFO_URL = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?jcd={jcd:02d}&hd={date}&rno={rno}"

# 公式サイト 三連単オッズ (Layer 3)
ODDS_TRIFECTA_URL = "https://www.boatrace.jp/owpc/pc/race/odds3t?jcd={jcd:02d}&hd={date}&rno={rno}"

# ============================================================
# スクレイピング設定
# ============================================================

REQUEST_INTERVAL_SECONDS = 2.0       # スクレイピング時の最低間隔
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = os.getenv(
    "BOATRACE_USER_AGENT",
    "boatrace-analysis/0.8 (+https://github.com/tsuyoshiyasuda0105/boatrace-analysis)",
)

LAYER3_MAX_RETRIES = 3
LAYER3_RETRY_BACKOFF_SECONDS = 10    # 5xx 等の一時障害時の再試行待機

# ============================================================
# SQLite
# ============================================================

SQLITE_BUSY_TIMEOUT_MS = 30000       # ロック待機 (busy_timeout PRAGMA)
SQLITE_CONNECT_TIMEOUT_SECONDS = 30  # connect() 自身のタイムアウト

# ============================================================
# Web 認証 (会員制機能)
# ============================================================
# WEB_SESSION_SECRET: Flask セッション暗号化キー (本番では環境変数で上書き必須)
# ローカル開発用のデフォルトは弱いランダム値で、本番デプロイ時は必ず .env で上書きすること。
WEB_SESSION_SECRET = os.getenv("BOATRACE_WEB_SECRET", "dev-only-do-not-use-in-prod")
# 会員パスワード (環境変数 BOATRACE_MEMBER_PASSWORD で設定。本番では .env で必ず変更)
WEB_MEMBER_PASSWORD = os.getenv("BOATRACE_MEMBER_PASSWORD", "dev-member")
# Pro プランパスワード (T-15min 期待値表示)。本番では .env で必ず変更
WEB_PRO_PASSWORD = os.getenv("BOATRACE_PRO_PASSWORD", "dev-pro")

# ============================================================
# モデル設定
# ============================================================

MODEL_DIR = ROOT_DIR / "models_artifacts"
DEFAULT_MODEL_VERSION = "v0.8"

# 期待値判定の閾値
EV_THRESHOLD = 0.15           # EV (期待値) 15%以上を Value Bet とする
KELLY_FRACTION = 0.25         # 1/4 Kelly

# ============================================================
# 補助関数
# ============================================================

def ensure_dirs():
    """必要なディレクトリを作成"""
    for d in [
        DATA_DIR, RAW_DIR, PROCESSED_DIR,
        OFFICIAL_PROGRAMS_DIR, OFFICIAL_RESULTS_DIR, OFFICIAL_FAN_DIR,
        OPENAPI_RAW_DIR, BEFOREINFO_DIR, MODEL_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)
