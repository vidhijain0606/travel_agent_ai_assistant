# Review of the QA/Architect Critique — Analysis, Decisions, Plan

This documents the review of the external critique (6 "faults" + 4 proposed fixes + the
"Master Orchestration Engine" state-machine spec), what's actually implementable given
**our real inputs** (3 fixed inquiries, a 5-row static rate sheet, a 6–8 hour take-home
scope — see `Oltaflock_Take_Home_Vidhi.pdf`), and what I'm building vs. deferring.

A note on citations: several of the critique's claims are general industry patterns
(OTA price-tiering, hybrid retrieval, PMS multi-room booking) rather than facts I can
cite to a specific paper without browsing live sources. Where I can't verify a specific
claim, I say so rather than inventing a reference.

## Fault-by-fault verdict

| # | Fault | Valid in general? | Applicable to OUR data? | Verdict |
|---|---|---|---|---|
| 1 | Preference/constraint "chicken-and-egg" | **Yes** | Yes — extraction could mislabel a soft preference as a hard constraint, causing an avoidable `to_be_quoted` | **Fix now** (tighten extraction prompt) |
| 2 | Date-dependent multi-leg domino effect | Yes, for live-inventory systems | **No** — `rate_sheet.csv` has no date/availability column at all; there is no per-date inventory to domino across | **Document as N/A** — would require a schema change upstream (date-indexed availability) that's out of this task's input shape |
| 3 | Static rate sheet / yield management | Yes, for a real OTA | **No** — the take-home brief explicitly mandates "the rate sheet is the only source of prices" as a static file read from disk, not a live feed | **Not a bug — it's the assignment's deliberate constraint.** Document in NOTES.md as a named production gap, not fixed here |
| 4 | Multi-room capacity math | **Yes** | **Yes** — Inquiry 3 (group of 6, wheelchair) hits exactly this: no single row sleeps 6 | **Fix now** (basic combinatorial fallback: try N identical rooms before declaring `to_be_quoted`) |
| 5 | Shortlist blindness → budget-fail-after-select loop | **Yes** | Yes — our `select_hotel` already can pick something that then fails `budget_policy="block"` | **Fix now** (price-tier signal, not exact price, into the selector prompt) |
| 6 | "To Be Quoted" ash-heap | Partially — real risk, but it's also the assignment's explicit "no forced matches" rule in tension with usability | Yes | **Mitigated by #1 and #4**, not separately engineered — over-building semantic relaxation (their fix #1, vector search) is unjustified at n=5 rows |
| 7 | Prose over-write vector (LLM mangling numbers in narration) | **Yes — this is the sharpest, most concrete fault** | Yes, directly | **Fix now** — highest-value fix in the whole critique |

## Proposed-fixes verdict

| Their fix | Verdict | Why |
|---|---|---|
| 1. Pre-scoring + embeddings/vector search over preferences | **Not implementable usefully here** | At 5 rate-sheet rows, a vector search is solving a problem that doesn't exist yet — there's nothing to retrieve from at that scale. Legitimate at hundreds/thousands of properties; documented as a scale-triggered future enhancement, not built now. |
| 2. Price-Tier token to the selector (no exact $) | **Implement now** | Cheap, preserves the "LLM never sees a price" guarantee (a tier label isn't a price), and directly closes fault #5. |
| 3. Matrix date-shifting fallback (±1-2 days) | **Not implementable** | There is no date-indexed availability in `rate_sheet.csv` to shift across — rows have no dates at all. Would need a different data model upstream; out of scope. |
| 4. UI Data-Isolation — template placeholders instead of free-form prose numbers | **Implement now** | This is the direct fix for fault #7 and is cheap: narration output uses placeholder tokens, a deterministic Python render step substitutes the real numbers afterward. The LLM physically cannot mistype a number it never writes. |

## Answers to the critique's questions

- **"Are hard constraints binary checkboxes or complex logic rules?"** Binary, by design.
  `engine.py`'s `HARD_CONSTRAINT_FIELD_MAP` maps constraint phrases to structured boolean
  columns (`wheelchair_accessible`, `quiet_zone`, `adults_only`) — chosen specifically to
  avoid the free-text/negation parsing bug found in an earlier review round (see
  `SOLUTION.md`). An unrecognized constraint is treated as **unmet**, never silently
  passed.
- **"How critical is multi-room booking?"** Directly critical — Inquiry 3 (group of 6)
  is in the fixed input set and currently dead-ends to `to_be_quoted` for exactly this
  reason. Fixing it (fault #4) changes real output for one of the 3 required inquiries.

## What I'm building, in order

1. **Frozen-manifest narration with placeholder substitution** (fault #7 fix). Change
   `narrate_proposal`'s contract: the LLM returns prose with `{{leg_N_subtotal}}`-style
   placeholders, never raw numbers; a deterministic Python step substitutes them from the
   frozen `PricedProposal` after the LLM call returns. A number the LLM never writes is a
   number it cannot mistype.
2. **Price-tier signal into `select_hotel`** (fault #5 fix). Compute a tier
   (`budget`/`standard`/`premium`) per shortlisted row relative to
   `budget_usd / total_nights`, pass the tier label (not the dollar figure) into the
   selection prompt.
3. **Multi-room combinatorial fallback** (fault #4 fix). Before declaring a leg
   `to_be_quoted` on capacity alone, try combinations of 2+ identical-room-type rows at
   the same hotel whose combined `sleeps` covers the traveller count, and price it as the
   sum of those rooms.
4. **Tighten extraction prompt wording** (fault #1 mitigation). Explicit instruction:
   only phrase-matches that are genuine deal-breakers go in `hard_constraints`; anything
   stylistic/soft goes in `preferences`, even if phrased with words like "need" or "must"
   loosely.
5. **Document, not build:** date-dependent multi-leg inventory matrix, live/dynamic
   pricing, and vector-based preference retrieval — added to `NOTES.md`'s failure-modes
   section as named production gaps with the reason each is out of scope for this input
   shape, not silently dropped.

## Review checkpoint

Re-read this plan against the fixed 3 inquiries before implementing: does fix #3
(multi-room) change Inquiry 3's outcome? Yes — 6 travellers, Sanur Garden Inn sleeps 4
and is wheelchair accessible; 2 rooms = 8 capacity, satisfies 6, and is the only
wheelchair-accessible room on the sheet, so it was already the sole legitimate option —
the fix should turn this from `to_be_quoted` into a real priced, 2-room proposal. That's
a meaningful, verifiable change to go implement.
