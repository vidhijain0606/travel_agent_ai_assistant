# How The Solution Works — Mechanics

This describes the data flow through the chosen architecture (extended Approach A), end to
end, for one client inquiry.

## 1. Input

- One unstructured client inquiry (free text).
- The rate sheet — now with structured accessibility fields, not free-text notes:

  | field | type | example |
  |---|---|---|
  | `hotel_id` | string | `sanur_garden` |
  | `name` | string | `Sanur Garden Inn` |
  | `destination` | string | `Bali` |
  | `room_type` | string | `Family room, sleeps 4` |
  | `sleeps` | int | `4` |
  | `price_per_night` | float | `140.0` |
  | `wheelchair_accessible` | bool | `true` |
  | `quiet_zone` | bool | `false` |

## 2. Extraction (LLM call #1)

**In:** raw inquiry text.
**Out:** `StructuredTripBrief` — validated by Pydantic / native structured output.

```
StructuredTripBrief
├── legs: list[TripLeg]            # one entry per destination/city in the trip
│     ├── destination: str
│     ├── nights: Optional[int]
│     └── travellers_count: int
├── traveller_notes: Optional[str]
├── month_or_window: Optional[str]
├── budget_usd: Optional[float]
├── preferences: list[str]
└── hard_constraints: list[str]
```

If any leg is missing `nights` or `travellers_count`, the pipeline stops here and returns
`MissingClientQuestions` — it does not guess.

**Resumable, not a dead end.** Every brief gets a `brief_id` the moment extraction runs.
`MissingClientQuestions` carries that `brief_id` back to the advisor. When the advisor
supplies the missing field(s), the pipeline is called again with `brief_id` + the patch
(e.g. `{"legs": [{"destination": "Tokyo", "nights": 4}, ...]}`) and resumes from step 3 on
the patched brief — extraction does not re-run, so a correct earlier extraction can't be
silently re-interpreted differently on the second pass.

## 3. Deterministic Shortlist (pure Python, per leg)

For each `TripLeg`, filter the rate sheet to rows where:
- `destination` matches the leg's destination,
- `sleeps >= leg.travellers_count`,
- every hard constraint maps to a `True` structured boolean field (no string-guessing).

**If a leg's shortlist is empty → that leg is marked `to_be_quoted` immediately, with the
specific unmet constraint recorded** (e.g. "no rate-sheet row in Bali sleeps 6"). The LLM is
never consulted for that leg.

## 4. Hotel Selection (LLM call #2, per leg with a non-empty shortlist)

**In:** the leg's preferences/notes + the shortlist (hotel names, room types, structured
amenity flags — **no prices**).
**Out:** one `hotel_id` from the shortlist, or `NONE_AVAILABLE`.

The model's possible outputs are constrained to an enum built from the shortlist at request
time — it structurally cannot name a hotel absent from that list.

**Logged for injection audit.** Every call here is logged with: the raw inquiry text, the
shortlist offered, the `hotel_id` returned, and the model's stated rationale. Injection in
the raw text can only ever steer the pick *within* this already-valid shortlist — it can't
produce a price or an ineligible hotel — but the log makes a steering attempt detectable
after the fact, which delimiting alone does not guarantee prevention of.

## 5. Pricing (pure Python, per leg)

```
subtotal = chosen_row.price_per_night * leg.nights
```

Summed across all legs → `total_price_usd`. This step contains no LLM call and no
conditional logic that could "round favorably" or substitute a different rate — it is a
straight lookup against the rate sheet followed by multiplication and summation.

**Budget policy gate (same step, before the leg is marked priced).** If `budget_usd` was
extracted and the running total exceeds it:
- `budget_policy == "flag"` (default): leg stays `priced`, but carries
  `budget_exceeded: true` — the narration step (step 6) is required to mention this to the
  advisor.
- `budget_policy == "block"`: leg is routed to `to_be_quoted` instead, using the exact same
  deterministic gate as an unmet hard constraint — no new mechanism, one more condition on
  the existing gate.

## 6. Narration (LLM call #3 — frozen-object writer)

**In:** the fully computed `PricedProposal` object (hotel names, nights, per-leg subtotals,
total — exactly as calculated in step 5), handed to the model as fixed data, not as
something to recompute.
**Out:** advisor-readable prose — a short day-by-day outline and a plain-English summary of
the already-fixed numbers.

The model is explicitly instructed to copy the given numbers verbatim into prose, never to
restate them in rounded or paraphrased form that could drift from the source values.

## 7. Output

```
PricedProposal
├── brief_id: str                                      # for resuming on a follow-up answer
├── status: "priced" | "to_be_quoted" | "partial"   # partial = some legs priced, some not
├── legs: list[PricedLeg]
│     ├── hotel_name, destination, nights, rate_per_night, subtotal, budget_exceeded: bool
│     OR
│     └── unmet_constraints: list[str]                # if this leg is to_be_quoted
├── total_price_usd: Optional[float]                  # only for fully priced legs
└── narration: str                                     # advisor-facing prose, from step 6
```

## End-to-End Example — US Family (Tokyo + Kyoto)

1. Extraction produces two legs: Tokyo (4 travellers, nights unknown), Kyoto (4 travellers,
   nights unknown).
2. Because `nights` is missing on both legs → pipeline halts at step 2, returns
   `MissingClientQuestions: ["How many nights in Tokyo, and how many in Kyoto?"]`.
3. Once the advisor supplies nights (say, 4 + 3), the brief is re-run: shortlist for Tokyo →
   `Shinjuku Central` ($290/night); shortlist for Kyoto → `Gion Ryokan` ($410/night).
4. LLM selection picks between shortlisted options per leg (here, only one option exists
   per leg, so the choice is trivial but still goes through the same enum-constrained path).
5. Pricing: Tokyo subtotal = $1,160 (4 nights), Kyoto subtotal = $1,230 (3 nights), total =
   $2,390.
6. Narration step writes a two-city day-by-day summary using those exact numbers.

## What Never Happens

- The LLM is never shown the rate sheet's prices.
- The LLM never outputs a number that becomes `total_price_usd`.
- A leg with no valid rate-sheet match never gets a guessed or averaged price — it is
  flagged `to_be_quoted` with the reason named.
