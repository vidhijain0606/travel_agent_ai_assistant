# One-Page Note

## Design decisions & the rejected alternative

**Chosen:** single-prompt extraction + deterministic rate-sheet engine, extended with
multi-leg trip support, structured (not free-text) accessibility fields on the rate
sheet, a frozen-object narration step, and an explicit `budget_policy` setting. The LLM
is called 4 times per inquiry (extract → questions → per-leg hotel selection → narrate)
but **never once outputs a price** — every number in the output is a lookup against
`data/rate_sheet.csv` multiplied by nights, computed in plain Python. If no rate-sheet
row satisfies a leg's capacity or hard constraints, that leg is marked `"to be quoted"`
before the LLM is even asked to pick anything — no forced matches are possible because
there is nothing to select from.

**Rejected alternative:** a single mega-prompt that pastes the entire rate sheet into
the prompt and asks the LLM to extract, match, price, and write the proposal in one
shot, with "don't invent prices" as a prose instruction. Rejected because there is no
code-level checkpoint between the LLM's claimed price and the output — compliance with
the no-invented-price rule is a matter of instruction-following, not a structural
guarantee, which is exactly the failure mode this task forbids. It also scales worse
(prompt size grows with the rate sheet) and fails silently: a hallucinated price has the
same shape as a correct one. Full comparison against a third (multi-agent chain)
approach is in `SOLUTION.md`.

## What one proposal costs in API fees (working shown)

Assuming an Azure OpenAI deployment priced like GPT-4o-mini (~$0.15 / 1M input tokens,
~$0.60 / 1M output tokens — confirm your actual deployment's rate card, as Azure pricing
varies by region/model):

| Call | Approx. input tokens | Approx. output tokens |
|---|---|---|
| `extract_trip_brief` | 150 | 150 |
| `generate_client_questions` | 250 | 100 |
| `select_hotel` (skipped if shortlist has ≤1 option) | 200 | 50 |
| `narrate_proposal` | 300 | 200 |
| **Total** | **~900** | **~500** |

Cost ≈ (900 / 1,000,000 × $0.15) + (500 / 1,000,000 × $0.60) ≈ $0.000135 + $0.0003 ≈
**$0.0004–0.0006 per proposal**, i.e. well under a tenth of a cent. At 1,000
proposals/month this is roughly **$0.40–0.60/month** in model fees — negligible next to
an advisor's 1–2 hours saved per inquiry. (Multi-leg inquiries like the Japan family run
`select_hotel`/pricing twice, roughly doubling the per-leg cost lines only.)

## Where this tool fails or could give a wrong answer

- **Prompt injection via the raw inquiry.** Untrusted client text is interpreted by two
  LLM calls (extraction, then hotel selection via extracted preferences). Delimiting the
  text as data reduces — but cannot eliminate — the risk that embedded instructions
  steer *which already-valid hotel* gets picked. It structurally cannot steer a price or
  an ineligible hotel, and every selection is logged for after-the-fact audit, but this
  is a bounded risk, not a solved one.
- **Ambiguous constraint phrasing.** Hard constraints are matched against a small fixed
  map of keywords → structured rate-sheet fields (`wheelchair`, `quiet`, `adults only`).
  A constraint phrased in a way the map doesn't recognize is treated as **unmet** (safe
  failure, routes to "to be quoted") rather than guessed — correct by design, but it
  means a legitimate request can get an unnecessary "to be quoted" if phrasing varies
  from the map's keywords. The map would need broadening against real usage data.
- **Multi-city budget allocation.** `budget_usd` is a single number checked against a
  running total across legs in extraction order; it has no concept of per-leg budget
  splitting, so a client who meant "$6k total, roughly even per city" may see one leg
  flagged/blocked and the other not, depending purely on extraction order.
- **LLM extraction errors are not independently re-verified against the raw text** before
  they feed the deterministic filter — e.g. a mis-extracted traveller count of 5 instead
  of 6 would silently produce a different (wrong) shortlist, and nothing downstream would
  catch that it drifted from the client's actual words.

## UK client data (Inquiry 1) — personal data touched, and what I'd change before real use

**What it touches today:** the UK couple's raw inquiry text (marital/relationship
context, travel dates, an implicit budget) is sent to Azure OpenAI twice (extraction,
narration) and logged locally (`select_hotel` logs the raw inquiry alongside the
shortlist and pick, per `SOLUTION.md`'s audit-trail design). Inquiry 3's wheelchair use
is **special category data under UK GDPR** (health-related) once a real client's name is
attached to it — none of the three fictional inquiries carry a name/email here, but a
real pipeline would.

**What I would change before using this on real UK client data:**
1. **Data residency** — pin the Azure OpenAI resource to a UK/EU region and confirm in
   the Azure DPA that prompts aren't used for model training and aren't retained beyond
   the abuse-monitoring window.
2. **PII minimization before the API call** — strip or pseudonymize names, emails, and
   phone numbers from the raw inquiry before it reaches `extract_trip_brief`, resolving
   back to the real client only on the advisor's side, outside the LLM call path.
3. **Special-category data handling** — the wheelchair-access constraint is health data;
   treat `hard_constraints` entries sourced from such mentions with explicit
   lawful-basis review (likely "explicit consent" or "necessary for the contract") before
   this ever touches a production client record, and avoid logging the raw health-related
   phrase verbatim longer than needed for the audit window in point 5.
4. **Retention policy on logs** — the `select_hotel` audit log (raw inquiry + shortlist +
   pick) currently has no expiry; it would need a defined retention window and deletion
   job, not indefinite storage, to meet UK GDPR storage-limitation requirements.
5. **Right to erasure path** — no mechanism currently exists to find and delete a specific
   client's inquiry/brief/log entries on request; this would need a lookup key (e.g. a
   client/booking ID) threaded through `brief_id` before going live.
