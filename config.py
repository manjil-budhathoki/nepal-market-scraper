"""Single source of truth — URLs, headers, paths, tunables."""
from pathlib import Path

BASE_URL = "https://nepalipaisa.com"
WARMUP_URL = f"{BASE_URL}/ipo"
IPO_API_URL = f"{BASE_URL}/api/GetIpos"

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_PATH = DATA_DIR / "ipo.json"
COOKIE_CACHE = DATA_DIR / ".cookies.json"

BASE_HEADERS = {
    "accept": "application/json, text/javascript, */*; q=0.01",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json; charset=utf-8",
    "referer": WARMUP_URL,
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/147.0.0.0 Safari/537.36",
    "x-requested-with": "XMLHttpRequest",
}

WARMUP_HEADERS = {
    "accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "user-agent": BASE_HEADERS["user-agent"],
    "accept-language": "en-US,en;q=0.9",
}

DEFAULT_PARAMS = {"stockSymbol": "", "pageNo": "1", "itemsPerPage": "10", "pagePerDisplay": "5"}

COOKIE_TTL_HOURS = 6
HTTP_TIMEOUT = 15
CRON_INTERVAL_MINUTES = 120  # every 2 hours
