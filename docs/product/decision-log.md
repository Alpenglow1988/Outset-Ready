# Outset Ready decision log

**Established:** 3 September 2026  
**Status:** Accepted unless revisited

This log records product decisions rather than the question-by-question conversation. Later entries should state the date, decision, rationale and any decision they replace.

## Identity and boundary

### DR-001: Product name

Use **Outset Ready**.

**Rationale:** The name connects the product to the Outset family and describes preparation without framing the product as a training-plan generator.

### DR-002: Product proposition

Use the core question: **Does what I’m doing still fit where I want to go?**

Ready assesses whether current actions and existing plans support the user’s chosen goals.

### DR-003: Initial audience

Build V1 for personal use. Preserve a route into the wider Outset ecosystem.

**Rationale:** Personal use gives us a real evidence set and removes premature account, billing and generic-template work.

### DR-004: Supported domains

Limit the product to health, fitness and adventure goals for V1.

Do not expand into finance, career, language or generic habit tracking.

### DR-005: Plan boundary

Assess existing plans. Do not generate training plans or detailed workout prescriptions.

**Rationale:** Ready should connect goals, evidence and plans. The training-plan market already offers mature products.

### DR-006: Platform

Build a desktop-first responsive web application. Treat a future phone product as a companion focused on the current week and quick actions.

## V1 proof

### DR-007: Acceptance journey

Prove V1 against this goal stack:

1. Reach 85 kg.
2. Maintain strength and training consistency.
3. Prepare for Ultra Mirage El Djerid 50 km.

**Rationale:** This case proves priority, supporting goals, future handover, Garmin evidence and adventure readiness without claiming generic coverage.

### DR-008: New repository

Build Ready in `Alpenglow1988/Outset-Ready` and bring proven parts of the WL engine across.

Keep the WL dashboard operational until Ready reaches parity. Do not create a runtime dependency on the WL repository.

## Goals

### DR-009: Multiple goals

Support a goal stack rather than one isolated goal.

### DR-010: User-controlled priority

Let the user choose the current priority. Ready may explain support, constraints and conflicts, but it cannot replace the user’s choice.

### DR-011: Phased priorities

Support one current priority, supporting active goals and scheduled future priorities.

Ask the user to confirm a planned handover rather than changing priority silently.

### DR-012: Goal-first onboarding

Start onboarding with: **What do you want to be ready for?**

Connect Garmin or collect manual evidence after the user establishes the first goal.

### DR-013: Two-stage goal creation

Stage one asks for the goal name, category and optional date. Stage two collects the plan, current capability and goal-specific evidence as needed.

### DR-014: Evidence templates

Give each goal a default evidence template. Let the user mark a signal relevant, irrelevant or unavailable and add another signal.

**Rationale:** Outset can supply expertise while the user corrects assumptions that do not fit.

### DR-015: Goal relationship advice

Explain relationships with conditional language. Do not assume that reaching a weight target will improve an adventure outcome without considering strength, fuelling and recovery.

## Evidence and connectors

### DR-016: Garmin preferred, manual fallback

Use Garmin as the preferred V1 connector. Keep manual entry available when Garmin lacks a signal or fails.

Normalise both sources into a Ready evidence model and preserve source and confidence context.

### DR-017: Additional connectors

Add Strava, Apple Health, COROS or other connectors only when user demand justifies them.

### DR-018: Optional calories and alcohol

Treat calories, protein and alcohol as optional context.

Do not require them, build a calorie tracker or treat missing entries as zero.

### DR-019: Structured manual additions

Provide quick additions for activities, waist, calories or protein, alcohol and other relevant evidence.

Prefer structured choices over AI analysis of free text.

## Plan and calendar

### DR-020: Garmin Calendar first

Use Garmin Calendar as the first plan source because it can aggregate Garmin and Runna-scheduled workouts.

Snapshot imported plan items locally so later calendar changes do not erase the original plan.

### DR-021: Manual plan support

Allow manual plan additions and edits. Use a saved weekly template when no imported calendar plan exists.

### DR-022: Plan revision history

Preserve moved, changed, skipped and removed plan items.

Assess the week against the latest intentional plan while noticing repeated reductions or skips across several weeks.

Do not judge one moved or skipped workout.

### DR-023: Optional change reason

Offer these optional structured reasons:

- schedule;
- recovery;
- soreness or illness;
- travel;
- weather;
- motivation;
- plan changed.

Do not spend AI tokens checking free-text reasons.

## Cadence and review

### DR-024: Weekly plus in-progress experience

Use a weekly assessment cadence and a lightweight Week in Progress screen.

Avoid daily goal verdicts.

### DR-025: Monday draft and confirmation

Prepare the previous week’s draft on Monday. Let the user check optional inputs and plan changes before confirming it.

Run the weekly AI interpretation after confirmation.

### DR-026: Missed finalisation

Let a new week begin when the previous review remains unconfirmed.

Move the earlier draft into history as **Review available**. Keep it available without expiry and allow later finalisation.

Continue using imported measurements and activities in longer trends. Preserve missing optional values as unknown. Do not call AI until the user confirms.

### DR-027: Reminder channel

Use email for the weekly review, one optional follow-up and user-selected goal or event milestones.

Allow connector-problem notices where expected evidence cannot arrive.

Do not add streak messages, missed-workout nagging or adaptive nudges in V1.

## Assessment and language

### DR-028: Per-goal assessment

Give each goal its own state. Add a concise overall read and one recommended change.

### DR-029: State language

Use:

- **Progressing**;
- **Mixed signals**;
- **Review the plan**;
- **Building a picture**.

Do not use **ON_TRACK**, **WATCH**, **OFF_TRACK** or **INSUFFICIENT** in user-facing text.

### DR-030: Missing evidence

Use **Building a picture** when core evidence cannot support a responsible read.

Do not use it because the user omitted optional calories or alcohol.

### DR-031: Layered reasoning

Show one summary state for each goal. Put recent direction, plan follow-through, remaining gap and evidence details behind an accordion or click-through.

### DR-032: Repeated-skip language

Call attention to repeated skipped or reduced work without judgement. Ask the user to check whether the plan still fits.

## Interface

### DR-033: Home-screen hierarchy

Lead with the current priority goal, its state and one action.

Show other goals, Week in Progress and the coach observation as compact cards or accordions. Include quick add for optional context.

### DR-034: Mobile behaviour

Keep the first web application usable at phone widths. Optimise a later companion for today, this week, quick add, move or skip and reminders.

## AI and commercial scope

### DR-035: Hybrid assessment

Use normal code for trends, adherence, flags, missing evidence and status candidates.

Use one compact AI call to interpret the confirmed weekly evidence. Cache the output against the input hash and retain a useful rules-only result when AI fails.

### DR-036: Paid tiers

Do not design paid tiers for V1. Revisit pricing and AI allowances after validation.

## Events and Outset integration

### DR-037: Event records

Distinguish known events, known activities and personal events in the data model.

Allow goals to link to these records even before Outset has a searchable catalogue.

### DR-038: Future catalogue

Let a later Outset catalogue supply dates, requirements, environmental factors, preparation milestones and readiness templates.

### DR-039: Data-driven possibilities

Provide a user-opened Explore catalogue later. Show at most one restrained home-screen possibility when the evidence creates a strong match.

Explain why it appeared, show important unknowns and provide **Dismiss** and **Not for me**. Do not repeat rejected possibilities.

Present possibilities as invitations to explore. Reserve readiness claims for a proper assessment.

### DR-040: Outset hand-offs

Use future event and activity records to connect Ready with Outset Explore, Prepare and Kit.

## Spreadsheet-derived event-readiness expansion

### DR-041: Treat the Tracking and Training workbook as product-discovery evidence

**Date:** 4 September 2026

Use the [Tracking and Training workbook](https://docs.google.com/spreadsheets/d/1uc93_Hncp_B9HTggw6BqNkCKbCbmMDCFiDyGuNElRDQ/edit) to identify useful workflows and reference examples.

Do not make Ready depend on the spreadsheet at runtime or reproduce its formulas, stale facts, unit inconsistencies or two-person assumptions as application rules.

### DR-042: Keep goal-centred event preparation in Ready

**Date:** 4 September 2026

Ready owns the personal, longitudinal parts of event preparation:

- goals and priority handovers;
- plans and completed evidence;
- progress against goal-linked metrics;
- personal pace and fuelling preparation;
- preparation milestones, routines and readiness state.

**Rationale:** These capabilities extend Ready's existing question, **Does what I’m doing still fit where I want to go?** They do not justify another application.

The work remains post-parity and is coordinated through #22.

### DR-043: Divide event and kit ownership across the Outset portfolio

**Date:** 4 September 2026

Outset Core owns discovery, sourced event facts and reusable event demands. Outset Ready owns the user's goals, evidence, progress and preparation state. Outset Kit owns durable equipment inventory, gaps and provision decisions.

Ready may retain snapshots and personal assumptions so it remains usable when another Outset product is unavailable. It must not maintain a second kit ledger or treat the Core catalogue as an always-available runtime dependency.

The canonical cross-product boundary lives in the Outset-App product architecture.

### DR-044: Keep achievement ladders optional and defer social competition

**Date:** 4 September 2026

Treat landmark achievement ladders as an optional later motivation layer built from attributable Ready evidence. Keep them separate from goal assessment and avoid streak pressure.

Defer friend challenges, medals, public leaderboards and opaque scoring. Do not create a separate Outset application for them until real use shows an independent product loop.
