# Outset Ready

**Does what I’m doing still fit where I want to go?**

Outset Ready helps you understand whether your current actions and existing plans support the health, fitness and adventure goals you care about.

Ready starts with the user’s goal, gathers evidence from Garmin or manual entries, compares planned and completed activity, and produces a calm weekly read with one useful adjustment. It complements existing training plans. It does not generate them.

## Current status

The first three application slices are now in place, with the private owner foundation under review:

- A desktop-first, responsive dashboard.
- The reference goal stack persisted in SQLite.
- Manual evidence entry with calories, protein and alcohol kept optional.
- The agreed neutral readiness vocabulary implemented as deterministic rules.
- A small goals API and health endpoint for future integrations.
- Garmin login with reusable local tokens and MFA support.
- Paginated daily health and activity syncing into connector-neutral tables.
- Local raw payload storage for debugging and safe reprocessing.
- Sync status and recent Garmin activity visibility in the dashboard.
- One-owner sign-in with signed, HTTP-only sessions and CSRF-protected forms.
- User-scoped goals, evidence, activities and connector history.
- SQLite for local development and managed Postgres for durable production data.
- A private Connections screen and separate application/database health checks.

Hosted Garmin connection is the next parity slice. Weekly insight parity with WL follows it.

## Run locally

Requires Python 3.12 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
outset-ready hash-password
```

Store the printed hash and two other private settings in your shell or local secret manager:

```bash
export OUTSET_READY_OWNER_EMAIL='your-email@example.com'
export OUTSET_READY_OWNER_PASSWORD_HASH='scrypt$...'
export OUTSET_READY_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
python -m uvicorn outset_ready.web:create_app --factory --reload
```

Open <http://127.0.0.1:8000> and sign in. Do not commit any of these values.

The local database defaults to `data/outset_ready.sqlite`. Override it when needed:

```bash
OUTSET_READY_DB_PATH=/path/to/ready.sqlite uvicorn outset_ready.web:create_app --factory
```

Run tests:

```bash
python -m pytest -q
```

## Vercel production

Vercel uses the exported FastAPI application in `app.py`. Production deliberately
refuses to start with temporary SQLite storage. Before merging this build:

1. Add a managed Postgres integration to the Vercel project.
2. Set `OUTSET_READY_DATABASE_URL` to the provider's Postgres connection value.
3. Add `OUTSET_READY_OWNER_EMAIL`, `OUTSET_READY_OWNER_PASSWORD_HASH` and
   `OUTSET_READY_SESSION_SECRET` to the Production environment only.
4. Confirm the values are not exposed to Preview or Development unless intended.

`vercel.json` tells Vercel to ignore every branch except `main`. Build and test
review branches locally and in GitHub Actions, then allow one production deploy
when the reviewed PR is merged.

## Connect Garmin locally

The current local connector can still read Garmin credentials from a private
local environment file:

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

Build #5 will replace this local credential step with an authenticated browser
connection. It will discard the Garmin password after login and store encrypted
reusable token material in the durable database.

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
