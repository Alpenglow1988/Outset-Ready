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

## Runtime boundaries

| Boundary | V1 responsibility | Later extension |
| --- | --- | --- |
| Web | Dashboard, manual evidence, weekly review | Mobile companion and Outset account shell |
| Domain | Goals, evidence, readiness rules | Event templates and cross-goal advice |
| Storage | Local SQLite | Managed database behind the same repository interface |
| Connectors | Garmin adapter, then manual fallback | Calendar, COROS and other evidence sources |
| Interpretation | Rules first | One cached AI interpretation after weekly confirmation |

## WL reuse decision

The attached WL source passes 136 tests under Python 3.12. Reuse should happen by moving tested behaviour behind Ready-owned interfaces, not by importing the old repository at runtime.

| WL area | Decision | Reason |
| --- | --- | --- |
| Garmin client and pagination | Adapt next | Already isolates Garmin failures and optional endpoints |
| Activity and daily payload normalisation | Adapt next | Defensive parsing has strong test coverage |
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

The next slice should port the tested Garmin acquisition and normalisation code into a `connectors/garmin` package, map WL metrics into neutral evidence records, and add fixture-based contract tests before any real account is connected.

