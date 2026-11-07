# Walkthrough — Oltaflock Travel Proposal Engine

This file is written to be read from, start to finish, during the 20-minute walkthrough
call. Everything you need to explain the solution — the problem, the architecture, the
data flow, the safety guarantees, and the one thing it gets wrong — is here. No other
file needs to be open.

---

## 1. The problem, in one line

Travel advisors spend 1–2 hours turning a messy client message into a priced proposal.
This tool writes the first draft — but it is only ever allowed to use **one** source of
truth for prices: `data/rate_sheet.csv`. The AI must never invent, adjust, or guess a
price; anything the sheet can't satisfy comes back labeled `"to be quoted"`.

Three example inquiries drive the whole build:
1. **UK couple** — Bali, 10th anniversary, "7 or 8 nights," quiet, budget ~$6k.
2. **US family of 4** — Tokyo + Kyoto, spring break, kids are picky eaters.
3. **Group of 6** — Bali, 5 nights, one wheelchair user, price matters most.

---

## 2. Architecture diagram

```
                      ┌─────────────────────────────┐
                      │   data/rate_sheet.csv         │   <- the ONLY source of prices
                      │   (hotel_id, name, city,       │      read from disk, never
                      │   room_type, sleeps, price,     │      pasted into a prompt
                      │   currency, accessibility flags)│
                      └───────────────┬─────────────┘
                                      │ loaded once per run
                                      ▼
   client inquiry (raw text)   ┌─────────────────────┐
          │                    │ PriceMatchingEngine   │
          ▼                    │ (engine.py)            │
 ┌─────────────────┐          │  - shortlist()         │
 │ [LLM #1] Extract  │         │  - price_tier()        │
 │ extract_trip_brief│         │  - price_leg()         │
 │  -> StructuredTrip│         └──────────┬─────────────┘
 │     Brief (JSON)  │                    │
 └────────┬──────────┘                    │
          │                               │
          ▼                               │
 ┌─────────────────────┐                  │
 │ classify_brief()      │  (Python, no   │
 │ info_audit.py          │   LLM call)    │
 │  -> explicit/inferred/ │                │
 │     missing/           │                │
 │     contradictory      │                │
 └────────┬──────────────┘                │
          │                               │
          ▼                               │
 ┌─────────────────────┐                  │
 │ [LLM #2] Questions    │                 │
 │ generate_client_       │                │
 │ questions              │                │
 │  -> ClientQuestions    │                │
 └────────┬──────────────┘                │
          │                               │
          └──────────────┬────────────────┘
                          ▼
              per leg (one per destination):
       ┌──────────────────────────────────────┐
       │  engine.shortlist(leg, constraints)    │  <- Python, filters the rate sheet
       │  empty? ──yes──► to_be_quoted,          │     by destination + capacity +
       │    with the exact reason named          │     structured accessibility flags
       │  no                                     │
       │   ▼                                     │
       │  [LLM #3] select_hotel()                 │  <- sees NAMES/amenities/price_tier
       │   (price-free, enum-constrained)          │     label only -- never a price
       │   ▼                                        │
       │  engine.price_leg()                        │  <- Python: rate x nights x rooms,
       │   (Decimal arithmetic, budget check)        │     Decimal, never float
       └──────────────────┬──────────────────────┘
                          ▼
              ┌─────────────────────────┐
              │ PricedProposal (frozen)   │
              │  - legs[], totals,        │
              │    assumed_fields         │
              └────────────┬─────────────┘
                           ▼
              ┌─────────────────────────┐
              │ [LLM #4] Narrate          │   <- writes {{placeholder}} tokens,
              │ narrate_proposal()        │      NEVER a number itself
              └────────────┬─────────────┘
                           ▼
              ┌─────────────────────────┐
              │ narration.py substitutes  │   <- deterministic: swaps every
              │ real values for tokens    │      {{token}} for the frozen value
              └────────────┬─────────────┘
                           ▼
              InquiryResult: brief + info_audit + client_questions + proposal
                           │
          ┌────────────────┼────────────────────┐
          ▼                ▼                     ▼
     run.py (CLI)     webapp/ ("The Desk")   streamlit_app.py
     saves JSON        dossier-style UI       simpler alt. UI
```

**The one sentence that matters most:** the LLM is called 4 times, and **none of those
4 calls ever produces a number that ends up in a price.** Every dollar figure in the
output is `rate_sheet.csv`'s `price_per_night` multiplied by nights and room count, in
plain Python, using `Decimal` arithmetic.

---

## 3. Data flow, step by step (what actually happens to one inquiry)

Walk through Inquiry 3 (the group of 6) live on the call — it's the most interesting
because it exercises almost every safety mechanism at once.

**Input:** *"Need something for a group, 6 of us, Bali, 5 nights, late March. One of us
uses a wheelchair. Price matters most."*

1. **Extract** (`llm_azure.extract_trip_brief`) — reads the raw text (delimited as
   untrusted data, not instructions), produces:
   `legs: [{destination: "Bali", nights: 5, travellers_count: 6}]`,
   `hard_constraints: ["wheelchair accessible"]`. No price anywhere in this step.
2. **Classify** (`info_audit.classify_brief`) — pure Python, no LLM. Marks `nights` and
   `travellers_count` as `"explicit"` (both stated), `budget` as `"missing"` (none
   given).
3. **Questions** (`llm_azure.generate_client_questions`) — since nothing is missing or
   contradictory here, returns an empty list.
4. **Shortlist** (`engine.shortlist`) — filters `rate_sheet.csv` to Bali rows that are
   `wheelchair_accessible=true`. Only **Sanur Garden Inn** qualifies (sleeps 4, $140/
   night). But the party is 6, and no single room sleeps 6.
5. **Multi-room fallback** — instead of dead-ending, the engine computes
   `room_count = ceil(6 / 4) = 2` and offers "Sanur Garden Inn × 2 rooms" (combined
   capacity 8) as a valid candidate.
6. **Select** (`llm_azure.select_hotel`) — shortlist has exactly one candidate, so no
   LLM call is even made (logged, not spent).
7. **Price** (`engine.price_leg`) — `$140 × 2 rooms × 5 nights = $1,400.00`, computed
   with `Decimal`, never float. `assumed_fields: ["room_count"]` is set, because the
   2-room split was a system assumption, not something the client confirmed.
8. **Narrate** (`llm_azure.narrate_proposal` + `narration.py`) — the LLM writes
   `"Stay {{leg_0_nights}} nights at {{leg_0_hotel_name}} for {{leg_0_subtotal}}"`; Python
   then substitutes the real frozen values. The LLM never had the chance to write
   "$1,400" itself — so it can't mistype it.

**Result:** a fully priced $1,400 proposal, with a flag that the 2-room split was
assumed, not client-confirmed. *(Before a later fix, this inquiry dead-ended into
`"to be quoted"` — walk through this as a concrete before/after if there's time; see
§6 below for the one-thing-it-got-wrong discussion.)*

---

## 4. The complete solution — file by file

| File | What it does | Why it exists |
|---|---|---|
| `data/rate_sheet.csv` | The only source of prices, with structured accessibility columns (not free-text notes) | Read from disk per the task's hard rule; structured booleans avoid a negation-matching bug found in review |
| `data/inquiries.json` | The 3 reference inquiries | Input fixture |
| `src/oltaflock/schemas.py` | Every Pydantic model: `StructuredTripBrief`, `BriefAudit`, `PricedProposal`, etc. | Schema-before-prompt discipline; money fields use `Decimal` |
| `src/oltaflock/engine.py` | `PriceMatchingEngine` — shortlist, price-tier, pricing, multi-room fallback | **The single authoritative pricing seam** — no LLM call anywhere in this file |
| `src/oltaflock/validators.py` | Rejects malformed rate-sheet rows and unknown hotel_ids | Defense-in-depth before arithmetic ever runs |
| `src/oltaflock/info_audit.py` | Classifies every field as explicit/inferred/missing/contradictory | A dedicated module, not folded into a prompt |
| `src/oltaflock/llm_azure.py` | The 4 Azure OpenAI calls (extract, questions, select, narrate) | Every prompt that touches the LLM lives here, auditable in one place |
| `src/oltaflock/narration.py` | Placeholder-token substitution for the narration step | Closes the "LLM mistypes a number in prose" risk structurally |
| `src/oltaflock/pipeline.py` | Orchestrates all of the above per inquiry | The one place that calls everything in order |
| `run.py` | CLI entry point — runs all 3 inquiries, saves `output/*.json` | The 5-minute "just run it" path |
| `webapp/` | "The Desk" — a custom dossier-styled web UI (FastAPI + HTML/CSS/JS) | Demo-friendly, shows the stage tracker + rate sheet live |
| `streamlit_app.py` | A simpler alternate UI | Lighter-weight fallback demo |
| `tests/` | 28 tests across the engine, validators, and info-audit modules | Mutation-tested (changed a line, confirmed RED, reverted, confirmed GREEN) |

---

## 5. The safety guarantees, in the order to explain them

1. **Price-blindness** — `select_hotel` is shown hotel names, room types, accessibility
   flags, and a relative `price_tier` label (budget/standard/premium) — never a dollar
   figure. It is structurally impossible for this call to reason about or invent a
   price.
2. **No forced matches** — an empty shortlist routes to `"to_be_quoted"` with the exact
   reason named, *before* any LLM selection call happens. There's nothing to force a
   match against.
3. **Multi-room, not a dead end** — a party too big for one room gets a same-hotel,
   multi-room combination tried first, flagged as `assumed_fields`, rather than an
   automatic refusal.
4. **Decimal arithmetic** — every money field and calculation uses `Decimal`, never
   `float`, to avoid financial rounding error.
5. **Placeholder narration** — the writer LLM call physically cannot write a number; it
   writes a token, and Python substitutes the real value afterward.
6. **Validators** — a malformed rate-sheet row (negative price, zero capacity) is
   rejected at load time, loudly, before it can reach pricing math.
7. **Prompt-injection delimiting + audit log** — untrusted client text is wrapped in
   `<inquiry>`/`<notes>` tags with explicit "treat as data, not instructions"
   framing, and every hotel selection is logged with its full context for after-the-
   fact audit.

---

## 6. "One thing it got wrong" — the required talking point

The PDF submission brief explicitly asks you to talk through one thing the tool got
wrong. Use this one — it's real, it was found during review, and it's instructive:

> **The multi-room gap.** In an earlier version, Inquiry 3 (group of 6, wheelchair)
> dead-ended straight into `"to_be_quoted"` — because no *single* room on the rate sheet
> sleeps 6, and the engine only ever considered one room at a time. That's a defensible
> failure (it never forced a wrong match), but it was also needlessly conservative: the
> correct answer — two Sanur Garden Inn rooms, the only wheelchair-accessible option on
> the sheet — was sitting right there and the system couldn't see it. The fix was a
> same-hotel, same-room-type combinatorial fallback (`engine.shortlist`, capped at 4
> rooms), which turned this into a real $1,400 priced proposal instead of punting
> everything to a human. The lesson: "never force a match" and "don't dead-end on an
> answer that's actually derivable" are different bars, and the first version only
> cleared the first one.

A second, smaller thing worth mentioning if asked for more: the system still can't
split a budget across multiple legs/cities (it checks the running total against one
flat `budget_usd`), so a multi-city trip with an even per-city budget intent can get an
uneven `budget_exceeded` flag depending on extraction order. Documented, not yet fixed.

---

## 7. If asked "why not just one big prompt?"

The rejected alternative: paste the whole rate sheet into one mega-prompt and ask the
LLM to extract, match, price, and write the proposal in one shot. Rejected because
there is no code-level checkpoint between the LLM's claimed price and the output — it's
instruction-following, not a structural guarantee, which is exactly the failure mode
the task forbids. It also fails silently: a hallucinated price looks identical in shape
to a correct one. Full comparison against a 3-approach + hybrid analysis is in
`SOLUTION.md` and `PLAN.md`, if a deeper follow-up question comes up.

---

## 8. API cost (if asked)

Roughly **$0.0004–0.0006 per proposal** (4 narrow LLM calls, ~900 input / ~500 output
tokens total, at GPT-4o-mini-class pricing) — well under a tenth of a cent. At 1,000
proposals a month that's under $1 in model fees. Full working shown in `NOTES.md`.
