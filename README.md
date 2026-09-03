# Outset Ready

**Does what I’m doing still fit where I want to go?**

Outset Ready helps you understand whether your current actions and existing plans support the health, fitness and adventure goals you care about.

Ready starts with the user’s goal, gathers evidence from Garmin or manual entries, compares planned and completed activity, and produces a calm weekly read with one useful adjustment. It complements existing training plans. It does not generate them.

## Current status

The first application slice is now in place:

- A desktop-first, responsive dashboard.
- The reference goal stack persisted in SQLite.
- Manual evidence entry with calories, protein and alcohol kept optional.
- The agreed neutral readiness vocabulary implemented as deterministic rules.
- A small goals API and health endpoint for future integrations.

Garmin connection and weekly plan comparison are the next build slices.

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

The attached WL implementation passes all 136 tests under Python 3.12. Ready will move its proven Garmin acquisition, defensive normalisation and metric behaviour behind Ready-owned interfaces. It will not depend on the WL repository at runtime, and no WL secrets, raw payloads, local database or generated reports will be copied.
