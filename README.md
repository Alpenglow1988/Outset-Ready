# Outset Ready

**Does what I’m doing still fit where I want to go?**

Outset Ready helps you understand whether your current actions and existing plans support the health, fitness and adventure goals you care about.

Ready starts with the user’s goal, gathers evidence from Garmin or manual entries, compares planned and completed activity, and produces a calm weekly read with one useful adjustment. It complements existing training plans. It does not generate them.

## Current status

The first two application slices are now in place:

- A desktop-first, responsive dashboard.
- The reference goal stack persisted in SQLite.
- Manual evidence entry with calories, protein and alcohol kept optional.
- The agreed neutral readiness vocabulary implemented as deterministic rules.
- A small goals API and health endpoint for future integrations.
- Garmin login with reusable local tokens and MFA support.
- Paginated daily health and activity syncing into connector-neutral tables.
- Local raw payload storage for debugging and safe reprocessing.
- Sync status and recent Garmin activity visibility in the dashboard.

Garmin calendar plan ingestion and weekly plan comparison are the next build slices.

## Run locally

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
uvicorn outset_ready.web:create_app --factory --reload
```

Open <http://127.0.0.1:8000>.

The local database defaults to `data/outset_ready.sqlite`. Override it when needed:

```bash
OUTSET_READY_DB_PATH=/path/to/ready.sqlite uvicorn outset_ready.web:create_app --factory
```

Run tests:

```bash
python -m pytest -q
```

## Vercel preview

Vercel uses the exported FastAPI application in `app.py`. The hosted build is a
product preview and stores its SQLite file under `/tmp`, so entries can reset
between serverless instances or deployments. Keep personal evidence, Garmin
credentials, raw payloads and reusable tokens in the local runtime.

Durable hosted accounts will require a managed database adapter in a later
Outset ecosystem phase. The preview banner makes the current boundary visible.

## Connect Garmin locally

Copy the local environment template and add your Garmin login:

```bash
cp .env.example .env
```

Set `GARMIN_EMAIL` and `GARMIN_PASSWORD` in `.env`, then start with a short sync:

```bash
python -m outset_ready.cli sync-garmin --days 3
```

Garmin may request an MFA code during the first login. Ready stores reusable Garmin tokens in `~/.garminconnect` by default. A normal weekly refresh uses:

```bash
python -m outset_ready.cli sync-garmin --days 7
```

Ready stores the SQLite database and raw Garmin payloads under `data/`. Git ignores that directory. Do not commit `.env`, the database, raw payloads or Garmin tokens.

## Product principles

- The user chooses which goal matters most.
- Ready explains supporting relationships and conflicts without judging the goal.
- Garmin supplies preferred evidence; manual entry remains a valid fallback.
- Calories, protein and alcohol can enrich a review but never become required inputs.
- Rules calculate trends, adherence and flags; one cached AI call interprets a confirmed weekly review.
- Ready assesses an existing plan and the evidence around it. It does not create a training programme.

## Documents

- [V1 product brief](docs/product/v1-product-brief.md)
- [Decision log](docs/product/decision-log.md)
- [V1 architecture](docs/engineering/architecture.md)

## WL boundary

The attached WL implementation passes all 136 tests under Python 3.12. Ready has moved its proven Garmin acquisition and defensive normalisation behaviour behind Ready-owned interfaces. It does not depend on the WL repository at runtime, and no WL secrets, raw payloads, local database or generated reports were copied.
