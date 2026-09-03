"""
IPO scraper for nepalipaisa.com with automatic cookie management.

Public surface:
    get_session()       -> requests.Session (auto-warms cookies, 6h cache)
    fetch_ipos()        -> raw API payload
    parse_ipos()        -> list of records pulled from envelope
    save_ipos()         -> atomic merge + write to data/ipo.json
    run_scrape()        -> all-in-one: fetch -> parse -> merge -> save
"""
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from config import (
    BASE_HEADERS, COOKIE_CACHE, COOKIE_TTL_HOURS, DEFAULT_PARAMS,
    HTTP_TIMEOUT, IPO_API_URL, OUTPUT_PATH, WARMUP_HEADERS, WARMUP_URL,
)

# ---------- date / number helpers ----------

_DATE_FMTS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
              "%Y/%m/%d", "%d-%m-%Y", "%b %d, %Y", "%d %b %Y")
_MS_JSON = re.compile(r"/Date\((-?\d+)")
_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean(v: Any) -> str | None:
    if v is None or v == "":
        return None
    s = _HTML_TAG.sub(" ", str(v))
    s = _WS.sub(" ", s).strip()
    return s or None


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "")))
    except (ValueError, TypeError):
        return None


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _to_date(v: Any) -> str | None:
    if v is None or v == "":
        return None
    s = str(v).strip()
    m = _MS_JSON.search(s)
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return None
    if s.isdigit() and len(s) >= 10:
        try:
            return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            pass
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ---------- normalization ----------

RECORD_FIELDS = ["symbol", "company_name", "ipo_type", "status", "units",
                 "price", "min_units", "max_units", "opening_date",
                 "closing_date", "remarks", "source_url", "fetched_at"]


def normalize_record(raw: dict, source_url: str = "") -> dict:
    rec = {f: None for f in RECORD_FIELDS}
    rec["source_url"] = source_url or None
    rec["fetched_at"] = datetime.now(timezone.utc).isoformat()

    rec["symbol"] = _clean(raw.get("stockSymbol") or raw.get("symbol"))
    rec["company_name"] = _clean(raw.get("companyName") or raw.get("name") or raw.get("company"))
    rec["ipo_type"] = _clean(raw.get("ipoType") or raw.get("type"))
    status = _clean(raw.get("status") or raw.get("ipoStatus"))
    rec["status"] = status.lower() if status else None

    rec["units"] = _to_int(raw.get("units") or raw.get("totalUnits"))
    rec["price"] = _to_float(raw.get("price") or raw.get("unitPrice"))
    rec["min_units"] = _to_int(raw.get("minUnits") or raw.get("min"))
    rec["max_units"] = _to_int(raw.get("maxUnits") or raw.get("max"))

    rec["opening_date"] = _to_date(raw.get("openingDate") or raw.get("openDate") or raw.get("issueOpenDate"))
    rec["closing_date"] = _to_date(raw.get("closingDate") or raw.get("closeDate") or raw.get("issueCloseDate"))
    rec["remarks"] = _clean(raw.get("remarks") or raw.get("description"))
    return rec


# ---------- cookie cache ----------

def _cache_fresh() -> bool:
    if not COOKIE_CACHE.exists():
        return False
    try:
        cache = json.loads(COOKIE_CACHE.read_text())
        if not cache.get("saved_at") or not cache.get("cookies"):
            return False
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(cache["saved_at"])).total_seconds()
        return age < COOKIE_TTL_HOURS * 3600
    except (json.JSONDecodeError, OSError, ValueError):
        return False


def _save_cookies(session: requests.Session) -> None:
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "cookies": [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
            for c in session.cookies
        ],
    }
    COOKIE_CACHE.write_text(json.dumps(payload, indent=2))


def _load_cookies(session: requests.Session) -> None:
    try:
        cache = json.loads(COOKIE_CACHE.read_text())
        for c in cache.get("cookies", []):
            session.cookies.set(c["name"], c["value"])
    except (json.JSONDecodeError, OSError, KeyError):
        pass


def get_session(force_refresh: bool = False) -> requests.Session:
    """Return a session with auto-managed cookies. First call warms them."""
    session = requests.Session()
    if force_refresh or not _cache_fresh():
        try:
            session.get(WARMUP_URL, headers=WARMUP_HEADERS, timeout=HTTP_TIMEOUT, allow_redirects=True)
        except requests.RequestException:
            pass
        if session.cookies:
            _save_cookies(session)
    else:
        _load_cookies(session)
    return session


# ---------- fetch + parse ----------

def fetch_ipos(page: int = 1, per_page: int = 10, stock_symbol: str = "",
               session: requests.Session | None = None) -> dict:
    session = session or get_session()
    params = dict(DEFAULT_PARAMS)
    params.update({"pageNo": str(page), "itemsPerPage": str(per_page), "stockSymbol": stock_symbol})
    params["_"] = str(int(time.time() * 1000))  # cache buster
    r = session.get(IPO_API_URL, params=params, headers=BASE_HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def parse_ipos(payload) -> list:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for k in ("data", "result", "items", "records", "rows"):
        v = payload.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for ik in ("items", "records", "rows", "data"):
                if isinstance(v.get(ik), list):
                    return v[ik]
    return []


# ---------- storage (atomic JSON) ----------

def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_ipos(records: list) -> dict:
    """
    Merge `records` into data/ipo.json keyed by symbol.
    New wins on conflict. Returns stats.
    """
    existing = _load(OUTPUT_PATH, [])
    if not isinstance(existing, list):
        existing = []
    by_key: dict[str, dict] = {}
    passthrough = []
    for rec in existing:
        sym = (rec or {}).get("symbol") if isinstance(rec, dict) else None
        if isinstance(sym, str) and sym.strip():
            by_key[sym.strip()] = rec
        else:
            passthrough.append(rec)

    added = updated = 0
    for rec in records:
        sym = rec.get("symbol")
        if not (isinstance(sym, str) and sym.strip()):
            continue
        sym = sym.strip()
        if sym in by_key:
            if by_key[sym] != rec:
                by_key[sym] = rec
                updated += 1
        else:
            by_key[sym] = rec
            added += 1

    merged = [by_key[k] for k in sorted(by_key)] + passthrough
    _atomic_write(OUTPUT_PATH, merged)
    return {
        "added": added, "updated": updated,
        "kept_unchanged": len(by_key) - added - updated,
        "final_total": len(merged),
    }


# ---------- all-in-one ----------

def run_scrape(page: int = 1, per_page: int = 50, stock_symbol: str = "",
               force_cookie_refresh: bool = False) -> dict:
    """Fetch -> parse -> normalize -> save. Returns a summary dict."""
    summary = {"ok": False, "error": None, "fetched": 0, "valid": 0, "stats": None}
    try:
        session = get_session(force_refresh=force_cookie_refresh)
        payload = fetch_ipos(page=page, per_page=per_page, stock_symbol=stock_symbol, session=session)
    except Exception as e:
        summary["error"] = f"fetch failed: {e}"
        return summary

    raw = parse_ipos(payload)
    summary["fetched"] = len(raw)
    if not raw:
        summary["ok"] = True
        return summary

    normalized = [normalize_record(r, source_url=IPO_API_URL) for r in raw if isinstance(r, dict)]
    valid = [r for r in normalized if r.get("symbol") and r.get("company_name")]
    summary["valid"] = len(valid)

    if not valid:
        summary["ok"] = True
        return summary

    try:
        summary["stats"] = save_ipos(valid)
    except OSError as e:
        summary["error"] = f"save failed: {e}"
        return summary

    summary["ok"] = True
    return summary


def load_ipos() -> list:
    """Return the current dataset from disk."""
    data = _load(OUTPUT_PATH, [])
    return data if isinstance(data, list) else []
