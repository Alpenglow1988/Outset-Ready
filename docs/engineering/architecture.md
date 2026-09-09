# Outset Ready V1 architecture

## Decision

Build V1 as a Python modular monolith with FastAPI and server-rendered Jinja templates. Use SQLite locally and managed Postgres for durable production state.

This keeps the proven WL language, data store and test approach. It also gives Ready HTTP boundaries that the wider Outset ecosystem can call later without splitting V1 into separately deployed frontend and backend services.

## First vertical slice

The first slice proves the product shape end to end:

1. Seed the agreed reference goal stack in SQLite.
2. Render the goal stack and current readiness state in a desktop-first dashboard.
3. Accept and persist manual evidence.
4. Treat calories, protein and alcohol as optional evidence types.
5. Apply a deterministic readiness vocabulary with no AI call.
6. Expose a health endpoint and a small goals API for future integrations.

## Garmin connector slice

The second slice moves the proven WL integration behind Ready-owned boundaries:

1. `GarminClient` owns login, MFA callbacks, token reuse and paginated retrieval.
2. Pure normalisers turn synthetic Garmin-shaped fixtures into `DailyObservation` and `ActivityRecord` domain objects.
3. The sync service stores raw local payloads, upserts normalised records and records each sync outcome.
4. SQLite keys daily observations by date and source, and activities by source and external ID, so repeated syncs remain idempotent.
5. The dashboard reports the last Garmin sync and shows recent imported activities.

The web application never receives Garmin passwords. The local CLI reads them from
`.env`, handles MFA in one process and exports only the reusable token bundle.

## Hosted Garmin connection

The private-owner deployment bridges Garmin's stateful MFA flow without placing a
password in Vercel:

1. The local CLI authenticates and writes an owner-readable token JSON file.
2. The owner-only Connections form checks CSRF and validates the upload structure.
3. Ready encrypts the canonical token fields with a dedicated Fernet key and stores
   the ciphertext in Postgres.
4. `Sync now` decrypts the token, fetches seven days of normalised evidence and
   writes refreshed token material back to Postgres.
5. An explicit Garmin authentication rejection changes the connection to
   `reconnect_required`; transient failures keep the saved connection available.

Hosted sync does not use Vercel's temporary filesystem for authoritative data or
raw payload history. Postgres holds normalised evidence, sync history and encrypted
token material. Vercel gives the FastAPI function a 120-second cap to prevent a
stalled Garmin request from running without a bound.

Issue #24 tracks official Garmin approval and the supported multi-user integration.
The private token bridge must not expand beyond the owner account.

## Weekly evidence parity

Ready calculates the last fully completed Monday-to-Sunday period from its
connector-neutral evidence tables. The weekly service owns four concerns:

1. Merge imported and manual evidence without counting a measurement twice.
2. Calculate WL-compatible weight, body-composition, waist, activity and recovery
   measures.
3. Show the evidence coverage behind each value and preserve missing data as
   unknown.
4. Translate the current weight direction and recovery thresholds into Ready's
   neutral four-state vocabulary.

Hosted history import covers 42 days in persisted batches of no more than seven
days. Completed connector-sync intervals define backfill progress. A failed or
interrupted interval cannot claim coverage, and an idempotent retry can safely
write the same observations and activities again.

The weekly read remains deterministic. Issue #8 will add planned-versus-completed
work, and issue #9 will add one user-confirmed, cached AI interpretation.

## Editable goal stack

The owner can create, edit and archive health, fitness or adventure goals. Ready
enforces one active current priority. Selecting another current goal explicitly
moves the previous current goal to supporting; changing or archiving the current
goal directly fails with guidance to choose its replacement first.

Every goal mutation writes an append-only snapshot to `goal_revisions`. Weekly
calculations resolve the revision effective at the end of that period, so a later
target or priority change cannot rewrite an earlier read. Archive remains a soft
operation and preserves the complete goal history.

Scheduled handovers, goal relationships and configurable evidence templates stay
outside this slice. They can build on the same revision history without changing
the current goal-management contract.

## Private owner and deployment boundary

`app.py` exports the FastAPI application for Vercel discovery. Production fails
closed unless it receives a Postgres URL, one configured owner email, a scrypt
password hash and a session-signing secret. Plaintext owner passwords never
enter source control or application storage.

Signed, HTTP-only cookies authenticate the owner. State-changing forms require a
session-bound CSRF token. Queries for goals, evidence, daily observations,
activities and connector history all require the authenticated user ID. V1 has
one owner, but the persistence boundary does not assume globally shared records.

Postgres and SQLite use the same schema and storage functions. Existing local
single-owner SQLite tables are migrated in place by adding the owner scope. The
Vercel filesystem is never treated as authoritative storage.

Vercel ignores non-main Git branches through `vercel.json`. A review branch is
validated locally and in GitHub Actions; merging an accepted PR is the only
action intended to create a production deployment.

## Runtime boundaries

| Boundary | V1 responsibility | Later extension |
| --- | --- | --- |
| Web | Dashboard, manual evidence, weekly review | Mobile companion and Outset account shell |
| Domain | Goals, evidence, readiness rules | Event templates and cross-goal advice |
| Storage | Local SQLite and managed Postgres in production | Managed migrations and retention controls |
| Connectors | Garmin adapter, then manual fallback | Calendar, COROS and other evidence sources |
| Interpretation | Rules first | One cached AI interpretation after weekly confirmation |

## Outset family resemblance

Ready inherits the Outset-App design tokens, Avenir/Iowan font stacks, real brand mark, square editorial surfaces, uppercase utility labels and black primary controls. Ready keeps a left-hand workspace rail because users return to it as an ongoing dashboard rather than moving through Outset’s public planning journey.

## WL reuse decision

The attached WL source passes 136 tests under Python 3.12. Reuse should happen by moving tested behaviour behind Ready-owned interfaces, not by importing the old repository at runtime.

| WL area | Decision | Reason |
| --- | --- | --- |
| Garmin client and pagination | Adapted | Isolates Garmin failures and optional endpoints |
| Activity and daily payload normalisation | Adapted | Defensive parsing now has fixture-based contract coverage |
| Metric calculations | Adapt by goal | Useful calculations, but current names assume weight loss |
| SQLite helpers | Use as reference | Ready needs goal, evidence, plan snapshot and event concepts |
| Status engine | Rebuild | Old `ON_TRACK`, `WATCH` and `OFF_TRACK` language conflicts with the agreed tone |
| Coach prompt | Rebuild | Ready should interpret a confirmed weekly review, not raw free text |
| Static dashboard renderer | Replace | V1 needs in-progress screens and manual interaction |

No `.env`, Garmin token, raw payload, SQLite database, report or archived virtual-environment file belongs in Ready.

## Data direction

The domain owns neutral evidence records. Garmin and manual entry both write through that boundary. Goal-specific rules consume normalised evidence and plan snapshots, then produce one of four user-facing states:

- Progressing
- Mixed signals
- Review the plan
- Building a picture

Missing optional context must never force `Building a picture`. That state should reflect a lack of evidence required by the active goal template only.

## Immediate follow-on

Calendar plan comparison can now consume the hosted weekly evidence and historical
goal context rather than a local report. Scheduled priority handovers and evidence
templates remain the next goal-specific extension after the plan comparison slice.
