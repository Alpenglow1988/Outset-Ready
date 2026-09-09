# WL weekly parity decisions

## Accepted parity boundary

Outset Ready reproduces WL's underlying completed-week evidence calculations while
using Ready's connector-neutral records, owner scope and neutral language. The
synthetic golden fixture in `tests/fixtures/wl_weekly_parity.json` fixes the shared
calculation contract without copying personal Garmin data.

| Concern | WL behaviour | Ready behaviour | Decision |
| --- | --- | --- | --- |
| Training week | Last completed Monday to Sunday | Same | Exact parity |
| Current weight | Average of available readings in the completed week | Same, with a minimum of two measured days before classification | Intentional guardrail |
| Weekly weight change | Current seven-day average minus the previous seven-day average | Same | Exact parity |
| 30-day weight change | Latest reading minus the latest reading at or before the 30-day point | Same within the 42-day hosted history window | Equivalent for the imported window |
| Body composition | Latest available smart-scale values | Same, labelled directional | Exact parity |
| Waist | Latest manual value minus the previous manual value | Same | Exact parity |
| Activity totals | Counts, duration, run distance and longest session from the completed week | Same | Exact parity |
| Long session | Run at least 75 minutes or 10 km; hike at least 90 minutes | Same | Exact parity |
| Recovery | Weekly sleep and stress averages, latest HRV, resting heart rate against a 30-day baseline | Same, with Body Battery coverage added | Parity plus visible evidence |
| Manual overlap | Separate manual-note model | A same-day manual weight or sleep value overrides the imported value | Intentional source rule |
| Missing optional context | Absent values omitted from calculations | Absent values remain unknown | Exact semantic parity |
| Training verdict | Fixed run, strength and long-session expectations | Metrics only until Ready stores the user's actual plan | Intentional product boundary, tracked by #8 |
| Status language | `ON_TRACK`, `WATCH`, `OFF_TRACK` and related states | Progressing, Mixed signals, Review the plan, Building a picture | Intentional product decision |
| Target projections | Projects dates from one weekly rate | Omitted from the weekly read | Intentional guardrail against false precision |
| Raw hosted payloads | WL retains local JSON | Ready stores normalised records and sync metadata only | Intentional privacy and serverless boundary |

## Classification thresholds retained from WL

- Expected weekly weight movement: -0.75 kg to -0.25 kg.
- Fast-loss review point: below -0.9 kg per week.
- Low weekly average sleep: below 6.5 hours.
- Resting heart-rate review point: more than 5 bpm above the 30-day baseline.
- Negative HRV hints: low, lower than usual, unbalanced, poor or below.

Ready exposes the calculations behind these thresholds. It does not treat Garmin
or smart-scale values as exact truth, and it does not make a medical or event
clearance judgement.
