# Outset Ready V1 architecture

## Decision

Build V1 as a Python modular monolith with FastAPI, server-rendered Jinja templates and SQLite.

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

The web application never receives Garmin credentials. The local CLI reads them from `.env` and passes them to the connector.

## Deployment boundary

`app.py` exports the FastAPI application for Vercel discovery. A Vercel build
uses `/tmp/outset_ready.sqlite` and presents itself as a preview because that
filesystem does not provide the durable application state Ready needs. It must
not receive Garmin credentials, token files or personal raw payloads.

The local runtime remains the V1 source of truth. A future hosted release should
replace the SQLite repository with managed persistence behind the same storage
boundary before authentication or personal sync moves into the Outset ecosystem.

## Runtime boundaries

| Boundary | V1 responsibility | Later extension |
| --- | --- | --- |
| Web | Dashboard, manual evidence, weekly review | Mobile companion and Outset account shell |
| Domain | Goals, evidence, readiness rules | Event templates and cross-goal advice |
| Storage | Local SQLite; temporary SQLite for hosted previews | Managed database behind the same repository interface |
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

The next slice should ingest planned workouts from the Garmin calendar, preserve plan snapshots when Garmin or Runna changes the schedule, and compare planned activity with completed activity in the Week in Progress view.
