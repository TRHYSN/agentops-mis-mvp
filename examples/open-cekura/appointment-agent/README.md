# AI Appointment Agent Reliability Test

This public demo uses the deterministic `MockAgentAdapter`; it is an offline
reliability simulation, not evidence of production traffic reliability.

The mock appointment backend exposes four observed tools:

- `lookup_booking`
- `list_available_slots`
- `update_booking`
- `cancel_booking`

`baseline.json` enables three behavior defects: duplicate mutation, mutation
before confirmation, and a success claim without state mutation. The
`candidate.json` profile removes those defects. Campaign and release-gate
outcomes are computed from observed behavior and evaluators; neither profile
contains a campaign ID or a hardcoded outcome.
