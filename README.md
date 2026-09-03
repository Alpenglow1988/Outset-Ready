# Outset Ready

**Does what I’m doing still fit where I want to go?**

Outset Ready helps you understand whether your current actions and existing plans support the health, fitness and adventure goals you care about.

Ready starts with the user’s goal, gathers evidence from Garmin or manual entries, compares planned and completed activity, and produces a calm weekly read with one useful adjustment. It complements existing training plans. It does not generate them.

## Current status

This repository contains the product definition for a personal V1. Implementation has not started here.

The V1 reference journey uses a real goal stack:

- Current priority: reach 85 kg.
- Supporting goal: maintain strength and training consistency.
- Future priority: prepare for Ultra Mirage El Djerid 50 km.

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

## Implementation boundary

We will inspect the existing WL dashboard before selecting the application stack or writing architecture documents. The new application should reuse proven Garmin importing, SQLite modelling, calculations and tests without creating a runtime dependency on the old repository.