"""Tests for the frozen-manifest placeholder substitution (narration.py).
Confirms the LLM's output is only ever re-combined with precomputed numbers, never
asked to reproduce them -- this is the fix for the 'prose over-write vector'."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from oltaflock.narration import build_placeholder_map, substitute_placeholders  # noqa: E402
from oltaflock.schemas import LineItem, PricedLeg, PricedProposal  # noqa: E402


def _proposal():
    return PricedProposal(
        brief_id="b1",
        inquiry_id="b1",
        status="priced",
        legs=[
            PricedLeg(
                destination="Bali",
                status="priced",
                hotel_name="Ubud Hideaway",
                line_item=LineItem(
                    description="Ubud Hideaway — Villa",
                    nights=7,
                    rate_per_night=320.0,
                    room_count=1,
                    subtotal=2240.0,
                ),
            )
        ],
        total_price_usd=2240.0,
    )


def test_placeholder_substitution_uses_the_frozen_value_exactly():
    tokens = build_placeholder_map(_proposal())
    text = "Stay {{leg_0_nights}} nights at {{leg_0_hotel_name}} for {{leg_0_subtotal}}."
    rendered = substitute_placeholders(text, tokens)
    assert rendered == "Stay 7 nights at Ubud Hideaway for $2,240.00."


def test_unrecognized_placeholder_is_left_visible_not_silently_dropped():
    tokens = build_placeholder_map(_proposal())
    text = "Total: {{leg_0_subtotal}}, bonus: {{not_a_real_token}}"
    rendered = substitute_placeholders(text, tokens)
    assert "{{not_a_real_token}}" in rendered, "a missing token must stay visibly wrong"
    assert "$2,240.00" in rendered


def test_model_cannot_drift_the_number_even_if_it_tries():
    """Even if the model's prose string contains a DIFFERENT literal number next to
    the placeholder, the placeholder substitution still inserts the correct frozen
    value -- the LLM's own text has no power to override it."""
    tokens = build_placeholder_map(_proposal())
    text = "A three-night stay (actually {{leg_0_nights}} nights) costs {{leg_0_subtotal}}."
    rendered = substitute_placeholders(text, tokens)
    assert "actually 7 nights" in rendered
    assert "$2,240.00" in rendered
