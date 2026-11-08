import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
FIX = Path(__file__).parent / "fixtures"

from news_watch import config as C  # noqa: E402


def src(name: str) -> tuple:
    return next(s for s in C.SOURCES + [C.BINANCE_CMS] if s[0] == name)
