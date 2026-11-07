"""Tests for the missing-information classification module (explicit / inferred /
missing / contradictory), independent of any LLM call."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from oltaflock.info_audit import classify_brief  # noqa: E402
from oltaflock.schemas import StructuredTripBrief, TripLeg  # noqa: E402


def _brief(**leg_overrides) -> StructuredTripBrief:
    leg = TripLeg(destination="Bali", **leg_overrides)
    return StructuredTripBrief(brief_id="b1", legs=[leg])


def test_fully_stated_leg_is_explicit():
    audit = classify_brief(_brief(nights=7, travellers_count=2))
    leg = audit.legs[0]
    assert leg.nights == "explicit"
    assert leg.travellers_count == "explicit"


def test_unstated_fields_are_missing():
    audit = classify_brief(_brief())
    leg = audit.legs[0]
    assert leg.nights == "missing"
    assert leg.travellers_count == "missing"


def test_a_stated_range_is_explicit_not_missing_and_not_collapsed():
    """Inquiry 1: '7 or 8 nights' must not be silently turned into a single guess."""
    audit = classify_brief(_brief(nights_options=[7, 8]))
    leg = audit.legs[0]
    assert leg.nights == "explicit"
    assert any("7" in n and "8" in n for n in leg.notes)


def test_confirmed_value_outside_its_own_stated_range_is_contradictory():
    audit = classify_brief(_brief(nights=10, nights_options=[7, 8]))
    leg = audit.legs[0]
    assert leg.nights == "contradictory"


def test_inferred_provenance_is_labeled_inferred_not_explicit():
    audit = classify_brief(_brief(travellers_count=4, provenance={"travellers_count": "inferred"}))
    leg = audit.legs[0]
    assert leg.travellers_count == "inferred"


def test_missing_budget_is_classified():
    audit = classify_brief(_brief())
    assert audit.budget == "missing"
