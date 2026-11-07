"""Missing-information detection as its own module, not an afterthought folded into
the clarifying-questions prompt.

For every leg in a StructuredTripBrief, classifies each tracked field as:
  - "explicit"      the client stated it (including a stated range, e.g. "7 or 8 nights")
  - "inferred"      the extraction call made a judgment call, not a direct read
  - "missing"       genuinely absent
  - "contradictory" the brief itself conflicts (e.g. a single nights value outside
                     its own stated nights_options)

This is deterministic Python over an already-extracted StructuredTripBrief -- no LLM
call happens here.
"""

from __future__ import annotations

from oltaflock.schemas import BriefAudit, FieldState, LegAudit, StructuredTripBrief, TripLeg


def _classify_nights(leg: TripLeg) -> tuple[FieldState, list[str]]:
    notes: list[str] = []
    if leg.nights is not None and leg.nights_options:
        if leg.nights not in leg.nights_options:
            notes.append(
                f"Confirmed nights ({leg.nights}) is outside the client's stated "
                f"range {leg.nights_options} -- brief is internally inconsistent."
            )
            return "contradictory", notes
        notes.append(
            f"Client gave a range {leg.nights_options}; {leg.nights} was treated as confirmed."
        )
        return "explicit", notes
    if leg.nights is not None:
        return _provenance_state(leg, "nights"), notes
    if leg.nights_options:
        notes.append(
            f"Client gave a range ({' or '.join(map(str, leg.nights_options))} nights), "
            "not a single confirmed value -- do not price against an arbitrary pick."
        )
        return "explicit", notes
    return "missing", notes


def _provenance_state(leg: TripLeg, field: str) -> FieldState:
    source = leg.provenance.get(field)
    if source == "inferred":
        return "inferred"
    return "explicit"


def _classify_travellers(leg: TripLeg) -> tuple[FieldState, list[str]]:
    if leg.travellers_count is None:
        return "missing", []
    return _provenance_state(leg, "travellers_count"), []


def classify_brief(brief: StructuredTripBrief) -> BriefAudit:
    leg_audits: list[LegAudit] = []
    for leg in brief.legs:
        nights_state, nights_notes = _classify_nights(leg)
        travellers_state, travellers_notes = _classify_travellers(leg)
        leg_audits.append(
            LegAudit(
                destination=leg.destination,
                nights=nights_state,
                travellers_count=travellers_state,
                notes=nights_notes + travellers_notes,
            )
        )

    budget_state: FieldState = "missing" if brief.budget_usd is None else "explicit"
    return BriefAudit(legs=leg_audits, budget=budget_state)
