# Outset Ready

**Does what I’m doing still fit where I want to go?**

Outset Ready helps you understand whether your current actions and existing plans support the health, fitness and adventure goals you care about.

Ready starts with the user’s goal, gathers evidence from Garmin or manual entries, compares planned and completed activity, and produces a calm weekly read with one useful adjustment. It complements existing training plans. It does not generate them.

## Current status

The first nine application slices now provide a private, durable owner workspace:

- A desktop-first, responsive dashboard.
- An editable health, fitness and adventure goal stack in SQLite or Postgres.
- Explicit current-priority handovers and archive-safe goal history.
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
- Local Garmin MFA authentication with a safe token-file export.
- Owner-only token upload, encrypted Neon storage and hosted `Sync now`.
- Refreshed Garmin token persistence without new Vercel variables or deployments.
- A resumable 42-day hosted history import that runs in seven-day batches.
- A completed Monday-to-Sunday weekly evidence read.
- WL-parity weight, waist, activity and recovery calculations.
- Per-metric coverage so missing Garmin values remain visible and unknown.
- Pending states that disable slow forms and protect against duplicate submissions.
- A live Monday-to-Sunday Week in Progress workspace without daily judgement.
- Garmin Calendar planned-workout import behind the existing encrypted connection.
- Manual planned sessions when the plan lives outside Garmin.
- Immutable provider snapshots and append-only plan revision history.
- Owner-recorded moves, replacements, reductions, skips and restores, with optional reasons.
- Conservative automatic activity matching and manual resolution when a match is ambiguous.
- Completed-week plan follow-through, with explicit skips excluded from the completion denominator.
- Durable weekly review drafts with immutable evidence revisions.
- Explicit owner confirmation before one cached AI interpretation.
- Review history that preserves unfinished weeks without blocking the next week.

Ready now covers WL's deterministic weekly evidence and planned-versus-completed
layers, then lets the owner close each week through a confirmed review.

Open `Goals` to create, edit or archive a goal. Ready keeps exactly one active
current priority. To replace it, edit another goal and choose `Current`; Ready
moves the previous priority to `Supporting` and records both revisions. Earlier
weekly reads continue to use the goal target and priority that applied then.

## Run locally

Requires Python 3.12 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
outset-ready hash-password
outset-ready generate-encryption-key
```

Store the printed hash and the two generated secrets in your shell or local secret manager:

```bash
export OUTSET_READY_OWNER_EMAIL='your-email@example.com'
export OUTSET_READY_OWNER_PASSWORD_HASH='scrypt$...'
export OUTSET_READY_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export OUTSET_READY_CREDENTIAL_ENCRYPTION_KEY='the-generated-key'
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
4. Generate `OUTSET_READY_CREDENTIAL_ENCRYPTION_KEY` with
   `outset-ready generate-encryption-key` and add it to Production.
5. Confirm the values are not exposed to Preview or Development unless intended.

AI interpretation is optional. Add `OUTSET_READY_OPENAI_API_KEY` to Production to
enable it. Ready defaults to `gpt-5.6-luna`; set `OUTSET_READY_OPENAI_MODEL` when a
different enabled model fits the account. Without an API key, the owner can still
finalise and retain the rules-based weekly review.

`vercel.json` tells Vercel to ignore every branch except `main`. Build and test
review branches locally and in GitHub Actions, then allow one production deploy
when the reviewed PR is merged.

## Connect Garmin

The current local connector can still read Garmin credentials from a private
local environment file:

```bash
cp .env.example .env
```

Set `GARMIN_EMAIL` and `GARMIN_PASSWORD` in `.env`, then generate the upload file:

```bash
python -m outset_ready.cli export-garmin-token
```

Garmin may request an MFA code. The command writes `data/garmin-token.json` with
owner-only file permissions and never prints its contents. Sign in to Ready, open
Connections, upload the file and select `Sync now`. Remove the exported file after
Ready accepts it.

The first sync imports seven days. Use `Import next history batch` on Connections
until Ready shows 42 of 42 checked days. Ready runs one persisted batch per request,
so an interrupted import resumes without repeating completed intervals. Open
`Weekly read` after the history import to inspect the last completed Monday-to-Sunday
period and the evidence behind each total.

Open `Week in progress`, then select `Refresh Garmin plan` to snapshot the current
Monday-to-Sunday Garmin Calendar plan. Imported workouts and manual sessions can be
moved, replaced, shortened, skipped or restored without erasing what the provider
originally supplied. Ready only auto-matches a completed activity when exactly one
same-day, same-type candidate exists; use the match control when the choice is
ambiguous.

Open `Weekly read` after Sunday. Ready prepares drafts for completed weeks covered
by the recent evidence history. Check the optional context and plan follow-through,
then confirm the selected revision. Confirmation locks that evidence snapshot. If
the evidence changes later, Ready creates a new visible draft revision and asks for
confirmation again.

When the OpenAI key is configured, confirmation sends the structured weekly
snapshot for one interpretation and saves the result. Ready sets `store=false` on
the Responses API request. Reloading or confirming the same completed revision does
not create another call. An API failure leaves the confirmed evidence intact and
offers a retry.

Ready encrypts the reusable token bundle before storing it in Postgres. Each hosted
sync saves any refreshed token material back to Postgres. Garmin passwords remain
local and never enter the website, Postgres or Vercel.

The local-only sync remains available for debugging:

```bash
python -m outset_ready.cli sync-garmin --days 7
```

Ready stores the SQLite database and raw Garmin payloads under `data/`. Git ignores that directory. Do not commit `.env`, the database, raw payloads or Garmin tokens.

The private-owner connector uses Garmin's unofficial account interface. Issue #24
tracks approval and migration to Garmin's supported API before Ready serves other
users.

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
- [WL parity decisions](docs/engineering/wl-parity.md)

## WL boundary

The attached WL implementation passes all 136 tests under Python 3.12. Ready has moved its proven Garmin acquisition, defensive normalisation and weekly metric behaviour behind Ready-owned interfaces. It does not depend on the WL repository at runtime, and no WL secrets, raw payloads, local database or generated reports were copied.
