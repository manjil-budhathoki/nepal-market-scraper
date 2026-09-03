# Nepal IPO Scraper

FastAPI service that scrapes IPO data from [nepalipaisa.com](https://nepalipaisa.com), normalizes it, dedupes by symbol, persists to `data/ipo.json`, and auto-commits to git every 2 hours.

## Run

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000/docs** for the interactive API.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | `/` | Health + record count |
| GET  | `/ipos` | All stored IPO records (JSON) |
| POST | `/scrape` | Trigger a scrape now |
| GET  | `/scrape/last` | Last run summary |
| GET  | `/docs` | Swagger UI |
| GET  | `/redoc` | ReDoc |

## Two ways to run the 2-hour cron

### Option A — Server (long-running, also serves the API)

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

The lifespan hook in `app.py` starts an `AsyncIOScheduler` that scrapes every 2 hours and auto-commits.

**Auth for `git push`** (one of):

1. **SSH key on the server** — easiest. Add the server's public key to your GitHub repo as a *Deploy Key* with write access.
   ```bash
   git remote set-url origin git@github.com:<you>/nepal-market-scraper.git
   ```
2. **Personal Access Token (PAT)** in the remote URL:
   ```bash
   git remote set-url origin https://<TOKEN>@github.com/<you>/nepal-market-scraper.git
   ```
3. Already-authenticated `gh` CLI on the host.

The app pulls before pushing (`git pull --rebase --autostash`) so concurrent pushes don't break.

Disable scheduler: `ENABLE_CRON=0 uvicorn app:app ...`
Disable commits:  `AUTO_COMMIT=0 uvicorn app:app ...`

### Option B — GitHub Actions (zero server, free for public repos)

The workflow at `.github/workflows/ipo-cron.yml` runs on cron `0 */2 * * *` (every 2 hours on the hour) and pushes the result with the built-in `GITHUB_TOKEN` — **no auth setup needed**, it just works on any repo where Actions are enabled.

Trigger manually from the Actions tab → "Update IPO Data" → "Run workflow".

Disable in repo: **Settings → Actions → disable the workflow.**

## Cookies

Auto-managed. The first request hits the warm-up page (`/ipo`) to receive `_ga` / `fpestid`, caches them to `data/.cookies.json` for 6 hours, and reuses them. **No manual cookie editing required.** `data/.cookies.json` is gitignored.
