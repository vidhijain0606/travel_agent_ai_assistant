# Oltaflock Travel Proposal Engine — Solution

## Problem

Travel advisors spend 1–2 hours turning messy client inquiries into priced proposals. This
tool generates the first draft automatically from (1) an unstructured client inquiry and
(2) a fixed rate sheet, under one hard constraint: **prices must come entirely from the rate
sheet — never invented, inferred, or extrapolated.** Anything the rate sheet can't satisfy
must come back as `"to be quoted"`, never a guessed number.

## Chosen Architecture

**Single-prompt extraction + deterministic shortlist/pricing (Approach A), extended with:**

1. **Multi-leg trip support** — `StructuredTripBrief` holds a list of `TripLeg` objects
   (destination, nights, pax per leg) instead of one flat destination/nights pair, so
   multi-city itineraries (e.g. Tokyo + Kyoto) price each leg against the rate sheet
   correctly instead of collapsing to a single, wrong number.
2. **Structured rate-sheet accessibility fields** — booleans/enums (`wheelchair_accessible`,
   `quiet_zone`, etc.) replace free-text `notes` parsing, closing the negation/false-positive
   bug where a note like "no step-free access" could substring-match as accessible.
3. **A frozen-object narration step** — borrowed from the multi-agent (Approach B) design:
   after pricing is computed deterministically, a narrow LLM call turns the already-priced,
   already-validated object into advisor-readable prose. It is handed numbers verbatim and
   instructed never to restate or recompute them — it narrates, it does not generate.
4. **An explicit `budget_policy` setting** (`"flag"` default, `"block"` optional) — makes
   over-budget handling a named, advisor-controlled decision instead of an implicit,
   silent default. In `"flag"` mode, a priced leg exceeding `budget_usd` is still returned
   priced but carries a `budget_exceeded: true` marker the narration step must surface to
   the advisor. In `"block"` mode, that leg is routed to `to_be_quoted` instead of priced,
   on the same deterministic Python path used for unmet hard constraints — no new mechanism,
   just a second condition on the existing to-be-quoted gate.
5. **Resumable `MissingClientQuestions`** — a clarifying question is no longer a dead end.
   Each brief carries a `brief_id`; when the advisor supplies the missing field(s) (nights,
   pax, etc.), the pipeline resumes from the same `brief_id` with the gap patched in, rather
   than restarting the whole extraction from scratch. Closes the UX dead-end where the
   advisor's answer had nowhere to go back into.

### Why this, and not the alternatives

- **Approach C (mega-prompt, LLM does everything) is rejected outright.** Price there is an
  LLM-generated field validated only for *shape*, never for *value correctness* against the
  rate sheet — the exact failure mode the hard constraint forbids. It will eventually
  hallucinate a price that looks perfectly plausible and is silently wrong.
- **Approach B (full 4-node agent chain) is not needed at this scale.** Its node-isolation
  benefits (Extractor / Matcher / Pricer / Writer as separate calls) matter more at much
  larger rate sheets and itinerary complexity. At this scope, borrowing only its "frozen
  object → narration" idea gets the same advisor-facing benefit without the added
  orchestration and failure surface of a 4-node graph.
- **Approach A unmodified has two real correctness gaps** found in review: it can't
  represent multi-leg trips, and its original constraint-matching concept (parsing `notes`
  as free text) is a negation/false-positive risk. Both are fixed additively — neither
  requires changing the core safety mechanism.

## Core Safety Guarantee

The LLM **never outputs a price.** It only:
1. Extracts structured intent from free text (destinations, pax, dates, preferences, hard
   constraints).
2. Selects a hotel id from a pre-filtered, price-free shortlist (or `NONE_AVAILABLE`).
3. Narrates an already-computed, already-validated priced object.

All pricing math (`rate_per_night × nights`, summed across legs) happens in deterministic
Python against the rate sheet. If no rate-sheet row satisfies a leg's capacity + hard
constraints, that leg is marked `to_be_quoted` with the specific unmet constraint named —
before the LLM is ever asked to pick anything.

## Residual Risks — Bounded, Not Eliminated

**Prompt injection (bounded and logged, not eliminable).** The raw client inquiry is
untrusted text fed to two LLM calls (extraction, then hotel selection via the extracted
preferences). Delimiting it as data, not instructions, reduces how readily an embedded
command gets obeyed — but any system whose job is "interpret free text as intent" cannot
fully firewall itself from that text. What IS structurally guaranteed regardless of
injection success:
- Selection can only return a `hotel_id` already present in the deterministic,
  constraint-valid shortlist (or `NONE_AVAILABLE`) — injection can swap *which legal hotel*
  gets picked, never produce an ineligible hotel or a price.
- Every selection is logged with the raw inquiry text, the shortlist offered, and the
  chosen `hotel_id` + model rationale, so a successful steering attempt is detectable
  after the fact even though it isn't preventable before the call.

This is the honest ceiling: **bounded blast radius + audit trail**, not "solved."

**Closed — no longer open risks:**
- Budget handling is now an explicit `budget_policy` setting (`"flag"`/`"block"`), not a
  silent default — see Chosen Architecture, point 4.
- Partial-leg ambiguity now resumes via `brief_id` rather than dead-ending at
  `MissingClientQuestions` — see Chosen Architecture, point 5.

## Round 2 — QA/architect critique review

A follow-up review raised 7 structural/operational faults. Each was triaged against our
actual input shape (3 fixed inquiries, 5-row static rate sheet) rather than against a
generic production system — full analysis, citations, and verdicts in `PLAN.md`. Four
fixes shipped:
1. **Multi-room combinatorial fallback** — a party too large for any single room now
   gets a same-hotel, multi-room candidate (e.g. 2× Sanur Garden Inn = 8 capacity for 6
   travellers) instead of an automatic `to_be_quoted`. This changes Inquiry 3's real
   output from a dead-end to a priced $1,400 proposal.
2. **Price-tier signal to the selector** — `select_hotel` now sees a relative
   `budget`/`standard`/`premium` label per candidate (derived from the client's stated
   budget, never a dollar figure) to avoid picking something wildly mismatched to budget
   before the deterministic budget check runs.
3. **Placeholder-substitution narration** — the narration LLM call now writes
   `{{leg_0_subtotal}}`-style tokens instead of numbers; a deterministic pass
   (`narration.py`) substitutes the real values afterward. Closes the "prose over-write"
   vector (an LLM mistyping "three nights" for a frozen 4-night value) structurally,
   not by instruction alone.
4. **Tightened hard_constraints vs. preferences extraction wording** — explicit guidance
   to default ambiguous phrasing to `preferences`, since an over-strict hard constraint
   can silently empty the shortlist before anyone sees why.

Three of the critique's other proposed fixes (vector/embedding preference search,
date-shifting multi-leg inventory matrix, live/dynamic pricing) were assessed and
**not implemented** — `PLAN.md` explains why each is either unnecessary at this scale
(5 rows) or structurally impossible given the input data (no date-indexed availability
exists in `rate_sheet.csv`).

## Status

This is the agreed design. Implementation has **not** started — see `WORKING.md` for how
the pieces fit together mechanically. Implementation begins only after explicit go-ahead.
