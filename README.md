# Oltaflock Travel Proposal Engine

Turns a messy client inquiry into a structured brief, advisor-ready clarifying
questions, and a priced proposal — using a fixed rate sheet as the **only** source of
prices. The AI never invents, adjusts, or extrapolates a price; anything the rate sheet
can't satisfy is marked `"to be quoted"`.

## Run it (under 5 minutes)

```bash
pip install -r requirements.txt
cp .env.example .env    # fill in your Azure OpenAI credentials
python run.py
```

Output for all three reference inquiries is printed to the console and saved to
`output/proposals.json`.

To run the test suite (no API key needed — these test the deterministic pricing engine
only, no LLM calls):

```bash
pytest tests/ -v
```

### Web UI — "The Desk" (for the walkthrough demo)

A custom-designed dossier-style web UI, served by a thin FastAPI wrapper over the same
`oltaflock.pipeline` — no new logic, just a visual front end:

```bash
uvicorn webapp.main:app --reload
```

Then open `http://127.0.0.1:8000`. Pick one of the 3 sample inquiries or write your own,
click **Generate proposal**, and watch the stage tracker move through Parsed → Clarify →
Priced as the structured brief, client questions, and priced itinerary render live. The
rate sheet is always visible (collapsed) in the left rail — the UI shows, not just
claims, that it's the only source of prices.

### Streamlit UI (alternate, simpler)

A plainer Streamlit wrapper, kept as a lighter-weight alternative:

```bash
streamlit run streamlit_app.py
```

## Architecture

```
inquiry text
    │
    ▼
[LLM] extract_trip_brief        → StructuredTripBrief   (intent only, no prices)
    │
    ▼
[LLM] generate_client_questions → ClientQuestions        (gaps, phrased for the client)
    │
    ▼
per leg:
  [Python] shortlist rate_sheet.csv by destination + capacity + hard constraints
      │
      ├─ empty shortlist → to_be_quoted, reason named (no LLM call)
      │
      ▼
  [LLM] select_hotel             → hotel_id from the shortlist ONLY (no prices shown)
      │
      ▼
  [Python] price_leg             → subtotal = rate_sheet price × nights (pure math)
    │
    ▼
[LLM] narrate_proposal          → day-by-day outline + prose, from the FROZEN priced object
```

**The price-safety guarantee:** the LLM is called 4 times per inquiry, and none of those
calls ever produces a number that becomes `total_price_usd`. Pricing is a lookup and a
multiplication against `data/rate_sheet.csv`, nothing else. Full design rationale and the
two rejected alternative architectures are in `SOLUTION.md`; step-by-step mechanics are in
`WORKING.md`.

## Files

| Path | What it is |
|---|---|
| `data/rate_sheet.csv` | The only source of prices. Read from disk, never pasted into a prompt. |
| `data/inquiries.json` | The 3 reference client inquiries. |
| `src/oltaflock/schemas.py` | Pydantic models: `StructuredTripBrief`, `ClientQuestions`, `PricedProposal`. |
| `src/oltaflock/engine.py` | Deterministic shortlist + pricing — the only place a price is computed. |
| `src/oltaflock/llm_azure.py` | The 4 Azure OpenAI calls (extraction, questions, selection, narration). |
| `src/oltaflock/pipeline.py` | Orchestrates the above per inquiry. |
| `run.py` | CLI entry point; runs all 3 inquiries, saves `output/proposals.json`. |
| `webapp/main.py` | FastAPI backend for "The Desk" web UI — thin HTTP wrapper over the pipeline. |
| `webapp/static/` | The dossier-style front end (HTML/CSS/JS) — see `PLAN.md` for the design brief. |
| `streamlit_app.py` | Alternate, simpler Streamlit UI wrapper over the same pipeline. |
| `tests/test_engine.py` | Tests for the pricing/matching engine (no LLM calls). |
| `SOLUTION.md` | Design decisions, rejected alternatives, residual risks. |
| `WORKING.md` | Step-by-step data flow through the pipeline. |
| `NOTES.md` | The required one-page submission note. |

## LLM API used

**Microsoft Azure OpenAI** (via the `openai` Python SDK's `AzureOpenAI` client), using
tool-calling (function calling) to enforce structured JSON output at each of the 4 call
sites. Configure via `.env` (see `.env.example`): `AZURE_OPENAI_ENDPOINT`,
`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`.

## AI coding assistance disclosure

Built with Claude Code (Anthropic) as a pair-programming assistant for design discussion,
code generation, and test authoring. All architectural decisions, tradeoff analysis, and
the choice of the final design were reviewed and directed interactively; the deterministic
pricing engine's test suite was run and its mutation-teeth verified (not just written) as
part of this process.
