"""
FastAPI app for the Nepal IPO scraper.

Auto-docs:
    GET  /docs        Swagger UI
    GET  /redoc       ReDoc
    GET  /openapi.json

Endpoints:
    GET  /                       health + dataset stats
    GET  /ipos                   all stored IPO records
    POST /scrape                 run a scrape now (manual trigger)
    GET  /scrape/last            last run summary
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse

from config import CRON_INTERVAL_MINUTES
from scraper import load_ipos, run_scrape

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ipo")

LAST_RUN: dict = {"ok": None, "at": None, "result": None}


async def _cron_job() -> None:
    """Background task: scrape every CRON_INTERVAL_MINUTES and commit to repo."""
    log.info("Cron tick: starting scrape...")
    result = await asyncio.to_thread(run_scrape)
    LAST_RUN["ok"] = result.get("ok")
    LAST_RUN["at"] = datetime.now(timezone.utc).isoformat()
    LAST_RUN["result"] = result
    log.info("Cron scrape result: %s", result)
    if os.environ.get("AUTO_COMMIT", "1") == "1" and result.get("ok"):
        await asyncio.to_thread(_commit_to_repo)


def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a git command and return the result. Logs stderr on failure."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0 and result.stderr:
        log.warning("git %s failed: %s", " ".join(cmd[:3]), result.stderr.strip()[:200])
    return result


def _commit_to_repo() -> None:
    """
    Stage data/ipo.json, commit, and push. Safe no-op if:
      - not in a git repo
      - no remote configured
      - no actual changes
    Pulls first to avoid non-fast-forward rejections.
    """
    import subprocess
    try:
        repo_root = os.path.dirname(os.path.abspath(__file__))

        # Bail early if this isn't a git repo (e.g. local dev without init).
        if not os.path.isdir(os.path.join(repo_root, ".git")):
            log.info("Not a git repo; skipping commit.")
            return

        _run(["git", "config", "user.email", "scraper@bot.local"], cwd=repo_root)
        _run(["git", "config", "user.name", "IPO Scraper Bot"], cwd=repo_root)

        # Best-effort pull so we don't diverge if someone else pushed.
        # Uses --rebase --autostash so local uncommitted changes don't block.
        _run(["git", "pull", "--rebase", "--autostash"], cwd=repo_root)

        # Stage only the data files. Cookies are gitignored — see .gitignore.
        _run(["git", "add", "data/ipo.json"], cwd=repo_root)

        # Skip commit if nothing changed.
        diff = _run(["git", "diff", "--cached", "--quiet"], cwd=repo_root)
        if diff.returncode == 0:
            log.info("No changes in data/ipo.json; skipping commit.")
            return

        ts = datetime.now(timezone.utc).isoformat()
        commit = _run(
            ["git", "commit", "-m", f"chore(data): update ipo.json at {ts}"],
            cwd=repo_root,
        )
        if commit.returncode != 0:
            log.error("git commit failed; not pushing.")
            return

        # Push. If push fails (no auth, no remote, etc.) the commit stays local
        # so you can recover or push manually.
        push = _run(["git", "push"], cwd=repo_root)
        if push.returncode == 0:
            log.info("Pushed data/ipo.json to remote.")
        else:
            log.error("git push failed. Commit is local; push manually when ready.")
    except FileNotFoundError:
        log.warning("git not installed; skipping commit.")
    except Exception as e:
        log.exception("Unexpected error in _commit_to_repo: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    if os.environ.get("ENABLE_CRON", "1") == "1":
        scheduler = AsyncIOScheduler()
        scheduler.add_job(_cron_job, "interval", minutes=CRON_INTERVAL_MINUTES,
                          next_run_time=datetime.now(timezone.utc))  # first run on startup
        scheduler.start()
        log.info("Cron started: every %d minutes.", CRON_INTERVAL_MINUTES)
    yield
    # shutdown handled by asyncio garbage collection


app = FastAPI(
    title="Nepal IPO Scraper",
    description="Scrapes IPO data from nepalipaisa.com every 2 hours and exposes it as JSON.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/", tags=["meta"])
def root():
    data = load_ipos()
    return {
        "service": "nepal-ipo-scraper",
        "status": "ok",
        "records_on_disk": len(data),
        "cron_interval_minutes": CRON_INTERVAL_MINUTES,
        "docs": "/docs",
    }


@app.get("/ipos", tags=["data"])
def get_ipos():
    """Return all stored IPO records."""
    return JSONResponse(content=load_ipos())


@app.post("/scrape", tags=["actions"])
def trigger_scrape(background: BackgroundTasks, page: int = 1, per_page: int = 50,
                   force_cookie_refresh: bool = False):
    """Run a scrape now. By default returns the result; pass ?background=true to fire-and-forget."""
    if os.environ.get("SCRAPE_BLOCKING", "1") == "1":
        result = run_scrape(page=page, per_page=per_page, force_cookie_refresh=force_cookie_refresh)
        LAST_RUN["ok"] = result.get("ok")
        LAST_RUN["at"] = datetime.now(timezone.utc).isoformat()
        LAST_RUN["result"] = result
        if result.get("ok") and os.environ.get("AUTO_COMMIT", "1") == "1":
            _commit_to_repo()
        return result
    background.add_task(run_scrape, page=page, per_page=per_page, force_cookie_refresh=force_cookie_refresh)
    return {"queued": True}


@app.get("/scrape/last", tags=["meta"])
def last_scrape():
    return LAST_RUN
