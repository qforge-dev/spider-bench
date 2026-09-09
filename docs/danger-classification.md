# Danger classification (plan §2.2)

Danger belongs to a **taxon in a geographic context** (Poland), not a photo, and
needs cited evidence + review. Never infer from venom, size/appearance, common
names, anecdotes, unverified records, or model output.

`medical_significance` values:

- `none_known` — no evidence of medically significant effects in scope (bite still possible).
- `minor_local_effects` — credible evidence of transient local effects.
- `medically_significant` — credible evidence of effects needing medical assessment.
- `uncertain` — insufficient/conflicting/off-scope evidence.

Each assessment records: geographic scope, rationale, evidence ids, reviewer,
review date, status. Missing data stays `uncertain`/draft — **never default to
`none_known`** (validated in code; gate §11 fails the release otherwise).

No numeric danger scores. `display_ordinal()` (none_known=0, minor=1,
significant=2, uncertain=3) is a documented display-sort helper only.

Exports include a safety notice: not medical advice; seek medical/emergency help
for severe or uncertain symptoms.

CLI: `danger evidence import`, `danger assessments import`, `danger audit`.
