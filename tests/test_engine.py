"""Tests for the deterministic pricing engine -- the correctness-critical seam.
No LLM calls happen in this file; these assert pure rate-sheet math/filtering.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decimal import Decimal

from oltaflock.engine import PriceMatchingEngine, RoomCandidate, load_rate_sheet  # noqa: E402
from oltaflock.schemas import TripLeg  # noqa: E402

RATE_SHEET_PATH = Path(__file__).parent.parent / "data" / "rate_sheet.csv"


def _engine() -> PriceMatchingEngine:
    return PriceMatchingEngine(load_rate_sheet(RATE_SHEET_PATH))


def _row(engine: PriceMatchingEngine, hotel_id: str):
    return next(r for r in engine._rate_sheet if r.hotel_id == hotel_id)


def test_capacity_filter_excludes_hotels_that_cant_even_combine_to_fit():
    engine = _engine()
    # Largest Bali room sleeps 4; MAX_ROOMS_PER_HOTEL=4 caps combined capacity at 16.
    # 17 travellers exceeds that for every Bali row, even combined.
    leg = TripLeg(destination="Bali", nights=5, travellers_count=17)
    shortlist = engine.shortlist(leg, hard_constraints=[])
    assert shortlist == [], "no combination of Bali rooms covers 17 travellers"


def test_group_of_six_gets_a_multi_room_combination_not_a_dead_end():
    """Regression for the 'multi-room capacity math failure' critique: a party of 6
    with no single room big enough must still get a combined-room candidate, not an
    automatic to_be_quoted, when a same-hotel multi-room combo covers the party."""
    engine = _engine()
    leg = TripLeg(destination="Bali", nights=5, travellers_count=6)
    shortlist = engine.shortlist(leg, hard_constraints=["wheelchair accessible"])
    assert len(shortlist) == 1
    candidate = shortlist[0]
    assert candidate.hotel_id == "sanur_garden"
    assert candidate.room_count == 2  # sleeps 4 per room; 2 rooms = 8 >= 6
    assert candidate.combined_capacity >= 6


def test_wheelchair_constraint_only_matches_structured_field():
    engine = _engine()
    leg = TripLeg(destination="Bali", nights=5, travellers_count=4)
    shortlist = engine.shortlist(leg, hard_constraints=["wheelchair accessible"])
    assert [c.hotel_id for c in shortlist] == ["sanur_garden"]
    assert shortlist[0].room_count == 1


def test_quiet_preference_as_hard_constraint_excludes_nightlife_hotel():
    engine = _engine()
    leg = TripLeg(destination="Bali", nights=7, travellers_count=2)
    shortlist = engine.shortlist(leg, hard_constraints=["quiet"])
    ids = [c.hotel_id for c in shortlist]
    assert "seminyak_beach" not in ids
    assert "ubud_hideaway" in ids


def test_unmet_reasons_names_the_specific_gap():
    engine = _engine()
    leg = TripLeg(destination="Bali", nights=5, travellers_count=17)
    reasons = engine.unmet_reasons(leg, hard_constraints=[])
    assert any("17" in r for r in reasons)


def test_price_leg_computes_exact_subtotal_from_rate_sheet():
    engine = _engine()
    leg = TripLeg(destination="Bali", nights=7, travellers_count=2)
    candidate = RoomCandidate(row=_row(engine, "ubud_hideaway"), room_count=1)
    priced = engine.price_leg(leg, candidate, budget_usd=None, budget_policy="flag", running_total=Decimal("0"))
    assert priced.status == "priced"
    assert priced.line_item.subtotal == 320 * 7
    # Mutation check: if price_per_night were read from anywhere other than the
    # rate sheet, this would not equal the CSV's 320 * nights.


def test_price_leg_multiplies_by_room_count_for_multi_room_candidates():
    engine = _engine()
    leg = TripLeg(destination="Bali", nights=5, travellers_count=6)
    candidate = RoomCandidate(row=_row(engine, "sanur_garden"), room_count=2)
    priced = engine.price_leg(leg, candidate, budget_usd=None, budget_policy="flag", running_total=Decimal("0"))
    assert priced.line_item.subtotal == 140 * 2 * 5
    assert priced.line_item.room_count == 2


def test_budget_policy_block_routes_over_budget_leg_to_to_be_quoted():
    engine = _engine()
    leg = TripLeg(destination="Bali", nights=8, travellers_count=2)
    candidate = RoomCandidate(row=_row(engine, "ubud_hideaway"), room_count=1)  # $320/night
    priced = engine.price_leg(
        leg, candidate, budget_usd=Decimal("1000.0"), budget_policy="block", running_total=Decimal("0")
    )
    assert priced.status == "to_be_quoted"
    assert priced.line_item is None


def test_budget_policy_flag_keeps_leg_priced_but_marks_it():
    engine = _engine()
    leg = TripLeg(destination="Bali", nights=8, travellers_count=2)
    candidate = RoomCandidate(row=_row(engine, "ubud_hideaway"), room_count=1)
    priced = engine.price_leg(
        leg, candidate, budget_usd=Decimal("1000.0"), budget_policy="flag", running_total=Decimal("0")
    )
    assert priced.status == "priced"
    assert priced.budget_exceeded is True


def test_unrecognized_constraint_text_is_never_treated_as_satisfied():
    """A hard constraint with no matching structured field must exclude every row,
    not silently pass -- an unverifiable claim is never treated as true."""
    engine = _engine()
    leg = TripLeg(destination="Bali", nights=5, travellers_count=2)
    shortlist = engine.shortlist(leg, hard_constraints=["has a rooftop pool"])
    assert shortlist == []


def test_price_tier_never_reveals_an_exact_price():
    engine = _engine()
    candidate = RoomCandidate(row=_row(engine, "ubud_hideaway"), room_count=1)  # $320/night
    tier = engine.price_tier(candidate, budget_usd=Decimal("300.0"), nights=1)  # implied budget $300/night
    assert tier in {"budget", "standard", "premium", "unknown"}
    assert "320" not in tier and "300" not in tier


def test_price_tier_is_unknown_without_a_budget():
    engine = _engine()
    candidate = RoomCandidate(row=_row(engine, "ubud_hideaway"), room_count=1)
    assert engine.price_tier(candidate, budget_usd=None, nights=5) == "unknown"
