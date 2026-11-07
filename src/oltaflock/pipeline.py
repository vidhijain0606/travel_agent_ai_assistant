"""Orchestration: inquiry text -> (brief, info audit, client_questions, priced proposal).

Order per inquiry:
  1. extract_trip_brief       (LLM)             -- intent only, no prices
  2. classify_brief           (Python)          -- explicit/inferred/missing/contradictory
  3. generate_client_questions (LLM)            -- gaps, phrased for the client, off the audit
  4. per leg: shortlist -> select_hotel (LLM) -> price_leg (deterministic)
  5. narrate_proposal (LLM)                      -- reads already-fixed numbers only

A leg with an empty shortlist is marked to_be_quoted before any LLM selection call --
no invented or forced match is possible because there is nothing to select from.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from oltaflock.engine import PriceMatchingEngine, RateSheetRow
from oltaflock.info_audit import classify_brief
from oltaflock.llm_azure import extract_trip_brief, generate_client_questions, narrate_proposal, select_hotel
from oltaflock.narration import render_narration
from oltaflock.schemas import InquiryResult, PricedLeg, PricedProposal, StructuredTripBrief

DEFAULT_BUDGET_POLICY = "flag"  # "flag" | "block"


def _overall_status(legs: list[PricedLeg]) -> str:
    statuses = {leg.status for leg in legs}
    if statuses == {"priced"}:
        return "priced"
    if statuses == {"to_be_quoted"}:
        return "to_be_quoted"
    return "partial"


def build_proposal(
    brief: StructuredTripBrief,
    engine: PriceMatchingEngine,
    budget_policy: str = DEFAULT_BUDGET_POLICY,
) -> PricedProposal:
    priced_legs: list[PricedLeg] = []
    running_total = Decimal("0")

    for leg in brief.legs:
        if leg.nights is None or leg.travellers_count is None:
            priced_legs.append(
                PricedLeg(
                    destination=leg.destination,
                    status="to_be_quoted",
                    unmet_reasons=["Nights and/or traveller count not yet confirmed by client"],
                )
            )
            continue

        shortlist = engine.shortlist(leg, brief.hard_constraints)
        if not shortlist:
            reasons = engine.unmet_reasons(leg, brief.hard_constraints)
            priced_legs.append(engine.to_be_quoted_leg(leg, reasons))
            continue

        tiers = {
            c.hotel_id: engine.price_tier(c, brief.budget_usd, leg.nights) for c in shortlist
        }
        hotel_id = select_hotel(
            leg, shortlist, brief.preferences, brief.traveller_notes, tiers
        )
        if hotel_id is None:
            priced_legs.append(
                engine.to_be_quoted_leg(
                    leg, ["No shortlisted hotel was judged a suitable fit for stated preferences"]
                )
            )
            continue

        candidate = next(c for c in shortlist if c.hotel_id == hotel_id)
        priced_leg = engine.price_leg(leg, candidate, brief.budget_usd, budget_policy, running_total)
        if priced_leg.line_item is not None:
            running_total += priced_leg.line_item.subtotal
        priced_legs.append(priced_leg)

    status = _overall_status(priced_legs)
    any_priced = any(leg.status == "priced" for leg in priced_legs)

    # total_price_usd is a COMPLETE trip total -- only set when every leg priced.
    # priced_subtotal_usd is the sum of whichever legs priced, set whenever at least
    # one did, including on a "partial" proposal. The two fields never overlap, so a
    # partial sum can never be mistaken for a complete total.
    total_price_usd = running_total if status == "priced" else None
    priced_subtotal_usd = running_total if any_priced else None

    return PricedProposal(
        brief_id=brief.brief_id,
        inquiry_id=brief.brief_id,
        status=status,
        legs=priced_legs,
        total_price_usd=total_price_usd,
        priced_subtotal_usd=priced_subtotal_usd,
    )


def run_inquiry(
    inquiry_id: str,
    raw_inquiry: str,
    rate_sheet: list[RateSheetRow],
    budget_policy: str = DEFAULT_BUDGET_POLICY,
) -> InquiryResult:
    brief_id = f"{inquiry_id}-{uuid.uuid4().hex[:8]}"
    engine = PriceMatchingEngine(rate_sheet)

    brief = extract_trip_brief(brief_id, raw_inquiry)
    info_audit = classify_brief(brief)
    client_questions = generate_client_questions(brief, raw_inquiry, info_audit)
    proposal = build_proposal(brief, engine, budget_policy)

    raw_outline, raw_narration = narrate_proposal(proposal.model_dump(mode="json"))
    outline, narration = render_narration(raw_outline, raw_narration, proposal)
    proposal.day_by_day_outline = outline
    proposal.narration = narration

    return InquiryResult(
        inquiry_id=inquiry_id,
        raw_inquiry=raw_inquiry,
        brief=brief,
        info_audit=info_audit,
        client_questions=client_questions,
        proposal=proposal,
    )
