"""Placeholder-substitution for narration output.

Fixes the "prose over-write vector": an LLM writing free-form prose can mistype or
reword a number mid-sentence even when told not to (e.g. "a three-night stay" for a
frozen 4-night value). The fix is structural, not another instruction: the LLM is
asked to write {{token}} placeholders instead of numbers, and a deterministic Python
pass substitutes the real values afterward. A number the LLM never writes is a number
it cannot mistype.
"""

from __future__ import annotations

import re

from oltaflock.schemas import PricedProposal

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def build_placeholder_map(proposal: PricedProposal) -> dict[str, str]:
    """All tokens the narration prompt is allowed to reference, pre-formatted from
    the frozen, already-computed PricedProposal -- never recomputed here."""
    tokens: dict[str, str] = {
        "total_price_usd": (
            f"${proposal.total_price_usd:,.2f}" if proposal.total_price_usd is not None else "N/A"
        ),
        "status": proposal.status,
    }
    for i, leg in enumerate(proposal.legs):
        prefix = f"leg_{i}"
        tokens[f"{prefix}_destination"] = leg.destination
        tokens[f"{prefix}_status"] = leg.status
        tokens[f"{prefix}_hotel_name"] = leg.hotel_name or "N/A"
        if leg.line_item:
            tokens[f"{prefix}_nights"] = str(leg.line_item.nights)
            tokens[f"{prefix}_room_count"] = str(leg.line_item.room_count)
            tokens[f"{prefix}_rate_per_night"] = f"${leg.line_item.rate_per_night:,.2f}"
            tokens[f"{prefix}_subtotal"] = f"${leg.line_item.subtotal:,.2f}"
        else:
            tokens[f"{prefix}_nights"] = "N/A"
            tokens[f"{prefix}_room_count"] = "N/A"
            tokens[f"{prefix}_rate_per_night"] = "N/A"
            tokens[f"{prefix}_subtotal"] = "N/A"
    return tokens


def substitute_placeholders(text: str, tokens: dict[str, str]) -> str:
    """Replace every {{token}} with its precomputed value. An unrecognized token is
    left as-is rather than silently dropped or guessed -- a missing substitution must
    stay visibly wrong, not quietly become blank."""

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        return tokens.get(key, match.group(0))

    return _PLACEHOLDER_RE.sub(_replace, text)


def render_narration(
    outline: list[str], narration: str, proposal: PricedProposal
) -> tuple[list[str], str]:
    tokens = build_placeholder_map(proposal)
    rendered_outline = [substitute_placeholders(line, tokens) for line in outline]
    rendered_narration = substitute_placeholders(narration, tokens)
    return rendered_outline, rendered_narration
