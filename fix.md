# Fix Log

## Issue: raw backend exception leaked to the client

**Reported as:** the `/api/run-inquiry` error response showed
`Could not generate a proposal: ModuleNotFoundError: No module named 'openai'`
directly in the UI — an internal Python exception type and module name exposed to the
end user.

**Verdict on the report:** mostly correct, with one claim in it that's wrong and worth
correcting rather than accepting silently (see below).

### What was actually wrong (confirmed, fixed)

- `webapp/main.py`'s `post_run_inquiry` caught the pipeline's exception and put
  `f"{type(exc).__name__}: {exc}"` straight into the HTTP response `detail` field, which
  the front end displays verbatim. That's a real bug: an internal implementation detail
  (which package is missing, what the exception class is named) has no business reaching
  a client, and in a real deployment this same pattern could leak more sensitive
  internals (file paths, stack frames, connection strings in an error message) depending
  on what failed.

**Fix applied** (`webapp/main.py`):
- The exception is now logged **in full, server-side only** via `logger.exception(...)`,
  tagged with a short random `error_id` for cross-referencing.
- The client receives a generic, safe message plus that `error_id` — e.g. *"The AI
  service isn't configured on this server yet (reference a8d1a5b4). Contact the
  administrator."* — with the exception type, module name, and traceback never sent over
  the wire.
- `ModuleNotFoundError`/`ImportError` (a deployment/config problem — the `openai`
  package genuinely isn't installed in this sandbox, per the DNS-blocked `pip install`
  attempts earlier in this build) gets a distinct, more specific-but-still-safe message
  ("not configured yet") than a generic runtime failure ("couldn't generate a proposal,
  try again"), so an operator reading logs can tell the two failure classes apart by
  `error_id` without the client ever seeing raw internals either way.

**Verified:** restarted the server, re-sent the same request that previously leaked the
exception — response is now the generic message + reference id; `tail`ing the server log
confirms the full traceback (including the `ModuleNotFoundError` line) is still captured
there for debugging. Confirmed by actually running it, not just reading the diff.

### One claim in the report that is factually wrong

> "The core feature of the UI is entirely built around taking a prompt... and relying on
> an LLM to generate a response... rather than using actual programmatic travel data or
> real agents."

This is **not how the system works**, and the error message that prompted the report
(a missing `openai` package) is unrelated to this claim — it's about an LLM call failing
to *extract intent*, not about pricing being LLM-generated. The actual architecture
(`SOLUTION.md`, `WORKING.md`):
- Every price comes from `data/rate_sheet.csv` via `oltaflock.engine.PriceMatchingEngine`
  — a deterministic lookup and multiplication, with no LLM involved in that step at all.
- This is enforced structurally (the hotel-selection LLM call is never shown a price;
  pricing is computed afterward in plain Python) and verified by a mutation-tested unit
  test suite (`tests/test_engine.py`) that confirms the pricing math can't silently drift
  from the rate sheet.
- The LLM is used for 4 narrow things per inquiry: extracting intent, phrasing
  clarifying questions, picking a hotel from an already-price-filtered shortlist, and
  narrating an already-computed result using placeholder substitution (see
  `src/oltaflock/narration.py`) so it can't even mistype a number in prose.

So: the error-leakage finding was correct and is now fixed. The "it's just a thin LLM
wrapper with no real data" characterization is incorrect for this codebase specifically
— the whole point of the architecture (documented across three prior review rounds in
`SOLUTION.md` and `PLAN.md`) is the opposite: isolating the LLM from every numeric
decision.

---

## Round 3 — comparison against a reference "tripdraft-ai" architecture

A reference implementation (module layout + pipeline diagram + rate-sheet shape +
9 numbered design points) was compared point-by-point against our actual code. Findings
below; **nothing in this section has been implemented yet** — each item is pending
explicit approval before any change is made.

### 1. Module layout: do we have every module `tripdraft-ai/` has?

| Their module | Ours | Verdict |
|---|---|---|
| `app.py` | `run.py` (CLI) + `webapp/main.py` + `streamlit_app.py` | Covered, 3 entry points instead of 1 |
| `data/hotels.csv` | `data/rate_sheet.csv` | Covered, different name |
| `models/schemas.py` | `src/oltaflock/schemas.py` | Covered |
| `services/extraction.py` | folded into `llm_azure.py` | **Not separated** — one file holds all 4 LLM calls |
| `services/clarification.py` | folded into `llm_azure.py` | **Not separated** |
| `services/hotel_matcher.py` | folded into `engine.py` | **Not separated** from pricing |
| `services/pricing.py` | folded into `engine.py` | **Not separated** from matching |
| `services/proposal_generator.py` | split across `pipeline.py` + `narration.py` | Covered, different split |
| `utils/rate_loader.py` | `engine.load_rate_sheet` | Covered, different location |
| `utils/validators.py` | **does not exist** | **Real gap** — no dedicated validation layer (see #7) |
| `tests/test_pricing.py` / `test_hotel_matching.py` | both inside `tests/test_engine.py` | Covered, combined not split |
| `tests/test_validation.py` | **does not exist** | **Real gap** — follows from the missing validators module |
| `outputs/inquiry_N.json` (3 separate files) | `output/proposals.json` (one combined file) | **Different** — not per-inquiry files |

**Net: the logic mostly exists; the module *separation* and per-inquiry output files do
not match.** The two real functional gaps are the missing `utils/validators.py`
equivalent and per-inquiry output files.

### 2. How does our architecture differ from their pipeline diagram?

**What we have that their diagram doesn't show:** multi-leg trip support, a multi-room
combinatorial fallback (so a party too big for one room doesn't dead-end), a price-tier
signal into hotel selection (prevents a pick-then-budget-fail loop), placeholder-
substitution narration (closes a specific numeric-hallucination vector), prompt-injection
delimiting + an audit log of every hotel pick, and an explicit `budget_policy`
(flag/block) setting.

**What their diagram has that we don't:**
- An explicit **"Missing Information Detection"** box as its own pipeline stage/module.
  Ours is implicit: a null-check in `pipeline.build_proposal` plus one LLM call that
  free-text-phrases questions — it does not classify every field into
  explicit/inferred/missing/contradictory the way their diagram's box implies. **Real
  gap** (ties directly into #5 below).
- An explicit **"Pricing Validation"** box, separate from the Pricing Engine itself. We
  compute `budget_exceeded` inline inside `price_leg` rather than as its own validation
  pass. **Real gap** (ties into #7).
- An explicit **"Advisor Review"** terminal stage. Our webapp's stage tracker
  (Parsed/Clarify/Priced) is the rough equivalent but isn't named or modeled the same
  way.

### 3. Do we have something like their `data/hotels.csv` for the data module?

| Their column | Ours | Verdict |
|---|---|---|
| `hotel_id` | `hotel_id` | Same |
| `hotel_name` | `name` | Same data, different field name |
| `city` | `city` | Same |
| `room_type` | `room_type` | Same |
| `max_occupancy` | `sleeps` | Same data, different field name |
| `price_per_night` | `price_per_night` | Same |
| `currency` | **missing** | **Real gap** — we hardcode an implicit USD assumption everywhere instead of an explicit column |
| `notes` (free text) | **deliberately replaced** with `wheelchair_accessible` / `quiet_zone` / `adults_only` booleans | **Intentional divergence**, not a gap — a prior review round found free-text constraint matching had a negation/false-positive bug (e.g. "no step-free access" could substring-match as accessible); structured booleans close that. Documented in `SOLUTION.md`. |

Their note — *"the sheet provides nightly rates but doesn't explicitly provide
availability, taxes, meals, cancellation policies, or seasonal pricing — these must not
be assumed"* — exposes a **real, currently-open gap on our side**: our narration prompt
(`llm_azure.narrate_proposal`) only forbids the model from inventing *numbers* (closed by
the placeholder-substitution fix in round 2). It does **not** forbid the model from
inventing *non-numeric* claims — "breakfast included," "free cancellation," "ocean view"
— that aren't in the rate sheet at all. This is a real, open hallucination vector,
distinct from the numeric one already fixed. Ties into #8 below.

### 4. Structured extraction with Pydantic — null handling

Schema-before-prompt: yes, `schemas.py` is written and imported before any prompt in
`llm_azure.py` references it. Null handling for truly unstated fields: yes — confirmed
in the round-2 dry run, Tokyo/Kyoto legs with unstated nights return `null`, not a guess.

Checked against their three specific test cases:
- **Inquiry 2 (no nights stated) → null:** ✅ already correct.
- **Inquiry 1 ("7 or 8 nights") → do we avoid arbitrarily picking 7?** ❌ **Real gap.**
  `TripLeg.nights` is a single `Optional[int]` — there is no way to represent "7 or 8" as
  a range; the extraction prompt would force the model to silently collapse it to one
  value. This is the exact arbitrary-choice problem the critique names.
- **Inquiry 3 (six people, room count not stated) → is the computed room count clearly
  flagged as an assumption, not a stated fact?** ❌ **Real gap.** We do compute
  `room_count = ceil(travellers_count / sleeps)` (the round-2 multi-room fix), but
  nothing in the schema distinguishes a client-stated fact from a system-derived
  assumption — there's no field-level provenance at all.

### 5. Missing Information Detection as its own module (explicit / inferred / missing / contradictory)

❌ **Real gap, matches #2 and #4.** We have no dedicated classification pass. One thing
we do already get right by omission: `dates_or_window` is stored as free text ("March",
"late March") and we never ask the model to produce a fixed check-in date — there's no
such field in the schema — so "do not generate a fixed check-in date for 'sometime in
March'" is satisfied, just not by design intent, by the schema simply never having that
field.

### 6. Deterministic pricing engine, independent of the LLM

✅ **Already fully satisfied — this has been the centerpiece since round 1.**
`PriceMatchingEngine.price_leg` is pure Python; the LLM is never shown a price at any
step. Verified by mutation testing (flipped the pricing formula, confirmed the test
suite went RED, reverted, confirmed GREEN) in round 2. No change needed.

### 7. Additional protections

| Protection requested | Status |
|---|---|
| Never let an LLM-generated rate enter pricing | ✅ Already true — `select_hotel` is never shown a price |
| Use hotel IDs to retrieve rates, not names | ✅ Already true throughout |
| Reject unknown hotel IDs | ✅ Structurally guaranteed (the LLM's enum is built from real shortlisted rows) but **no explicit defensive re-check exists** — worth adding as defense-in-depth, not because it's currently exploitable |
| Reject negative nights / occupancy | ✅ Partially — `TripLeg.nights`/`travellers_count` already have `ge=1` in `schemas.py` | 
| Reject negative/invalid rates from the CSV itself | ❌ **Real gap** — nothing validates `rate_sheet.csv` rows (e.g. a negative `price_per_night` typo would flow straight into pricing math unchecked) |
| Never convert an unavailable rate into zero | ✅ Already true — absence always routes to `to_be_quoted`, never `0` |
| Clear distinction between a partial subtotal and a complete trip total | ❌ **Real gap** — a `"partial"` status proposal (some legs priced, some `to_be_quoted`) still populates `total_price_usd` with the sum of only the priced legs, under the same "Total" label a fully-priced trip gets. Misleading if not visually/semantically distinguished. |
| `to_be_quoted` for anything absent from the sheet | ✅ Already true |
| Use `Decimal`, not float, for money | ❌ **Real gap** — `engine.py` and `schemas.py` use plain `float` for `price_per_night`/`subtotal`/`total_price_usd` throughout. Float arithmetic can introduce real rounding error in financial sums; `Decimal` is the correct practice here. |
| Terms and conditions | **Proposed as explicitly out of scope** — this is a legal/business-content concern, not an engineering one, and none of our inputs (3 inquiries, 5-row rate sheet) contain any T&C data to draw from; generating T&C text would itself be exactly the kind of unverified-content-fabrication risk the rest of this system is built to avoid. |
| Security/compliance rules for the LLM | Partially covered already — prompt-injection delimiting + audit logging (round 1/2) and UK GDPR/PII handling (`NOTES.md` section D) — not consolidated into one named "security policy," but substantively present |

### 8. Does our anti-hallucination approach hold up against a two-stage LLM workflow proposal?

We already exceed their proposed 2-stage split — ours is 4 narrowly-scoped calls
(extract → questions → select → narrate), each with a smaller failure surface than a
single "extraction" stage would have. For **numeric** hallucination specifically, yes —
closed in round 2 via placeholder substitution (the LLM writes `{{leg_0_subtotal}}`
tokens, never a number, so it structurally cannot mistype one). But per #3 above, this
does **not** cover **non-numeric fabrication** (amenities, policies, inclusions not on
the rate sheet) — that vector is still open.

### 9. Is the web dashboard advisor-friendly, with free interaction?

Partially. "The Desk" already has a clear separation between extracted info (Brief/
Questions blocks) and the final proposal (Itinerary block) — that part matches. It does
**not** have the suggested "three inquiry tabs for simultaneous review" — it's a
single-inquiry workspace (type or pick one sample at a time) rather than a fixed 3-tab
view of all reference inquiries side by side. Whether that's a gap depends on how the
advisor actually wants to work the walkthrough call.

---

## Round 3 — implementation status (all items approved individually, then built)

Per-item approval was obtained before any code was written (see conversation). Status:

| # | Change | Status |
|---|---|---|
| 1 | `nights_options` + field-level provenance in schema | ✅ Implemented (`schemas.py` `TripLeg`) |
| 2 | Dedicated missing-information classification module | ✅ Implemented (`info_audit.py`, `classify_brief`) |
| 3 | Validators layer (malformed rate-sheet rows, unknown hotel_id) | ✅ Implemented (`validators.py`, wired into `engine.py`) |
| 4 | Decimal instead of float for all money fields/arithmetic | ✅ Implemented (`schemas.py`, `engine.py`) |
| 5 | Distinguish partial subtotal from complete total | ✅ Implemented (`priced_subtotal_usd` vs `total_price_usd` in `schemas.py`/`pipeline.py`) |
| 6 | Harden narration against non-numeric fabrication | ✅ Implemented (`llm_azure.narrate_proposal` prompt) |
| 7 | Explicit `currency` column in rate sheet | ✅ Implemented (`data/rate_sheet.csv`, `engine.py`) |
| 8 | Reorganize into `services/`/`utils/` module layout | ❌ Declined — cosmetic only, real risk of breaking imports/tests for no functional gain this late |
| 9 | Per-inquiry output files | ✅ Implemented (`run.py` now writes `output/<inquiry_id>.json` + the combined file) |
| 10 | 3-tab web UI for side-by-side review | ❌ Declined — single-inquiry workspace already covers all 3 cases |
| 11 | Terms & conditions | ❌ Confirmed out of scope — legal/business content, no input data to draw from |

**A bug found and fixed while implementing item 2:** the first version of the
`generate_client_questions` gap-filter only triggered on `missing`/`contradictory`
field states, so a client-stated *range* ("7 or 8 nights," classified `explicit` with
an explanatory note, not `missing`) would never generate a clarifying question asking
which value to confirm -- even though pricing cannot proceed without a single value.
Fixed by also triggering on any leg carrying an audit note (`llm_azure.py`).

**Verification performed:**
- Full test suite: 28/28 passing, including 13 new tests across `test_validation.py`
  and `test_info_audit.py` for the two new modules.
- Full dry-run (mocked LLM calls, real `pipeline.py`/`engine.py`/`info_audit.py` code)
  against all 3 reference inquiries: confirmed the night-range case (Inquiry 1) is
  preserved as `nights_options: [7, 8]` with a classification note, never collapsed to
  a guess; confirmed Inquiry 3's multi-room fix still produces `assumed_fields:
  ["room_count"]` on the priced leg.
- Live server smoke test: restarted `webapp/main.py`, confirmed `/api/rate-sheet`
  returns the new `currency` field with `price_per_night` as a plain JSON number
  (Decimal serializes correctly through FastAPI's encoder), confirmed `/api/run-inquiry`
  still fails cleanly (the round-2 error-leak fix still holds after all these changes).
- Also applied the identical error-leak fix (from the "Issue: raw backend exception
  leaked to the client" section above) to `streamlit_app.py`, which had the same defect
  — found while touching that file for the other round-3 UI updates, not a new report.


1. **Schema: represent night-ranges + field-level provenance.** Add `nights_options:
   list[int] | None` alongside `nights` so "7 or 8" is preserved, not collapsed; add a
   `provenance` map (`stated` / `inferred` / `assumed`) per field so a computed value
   (like a multi-room count) is distinguishable from a client-stated one.
2. **A dedicated missing-information classification step**, producing explicit /
   inferred / missing / contradictory buckets per field, instead of the current implicit
   null-check + free-text-question call.
3. **A validators module** rejecting malformed rate-sheet rows (negative/zero price or
   capacity) at load time, plus a defense-in-depth re-check that a selected `hotel_id`
   really is in the shortlist it was offered.
4. **Switch monetary fields/arithmetic from `float` to `Decimal`** across `schemas.py`
   and `engine.py`.
5. **Distinguish a partial subtotal from a complete trip total** in `PricedProposal` and
   in both UIs, instead of one `total_price_usd` field used for both cases.
6. **Harden the narration prompt** to forbid inventing non-numeric claims (amenities,
   meals, cancellation policy, views) not present in the rate sheet — closes the open
   gap from #3/#8.
7. **Add an explicit `currency` column** to `rate_sheet.csv` (all `USD` today) instead of
   an implicit assumption.
8. **Reorganize `src/oltaflock/` into `services/`/`utils/`-style module separation**
   matching the reference layout (cosmetic/structural, no behavior change).
9. **Per-inquiry output files** (`output/inquiry_1.json`, etc.) in addition to or instead
   of the combined `output/proposals.json`.
10. **Add a 3-tab view to the web UI** for simultaneous side-by-side review of all 3
    reference inquiries.
11. **Terms & conditions — explicitly NOT building this**, for the reason stated above.
    Flagging for confirmation that this is the right call, not proposing to skip it
    silently.

