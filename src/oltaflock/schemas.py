"""Pydantic schemas for the trip brief / clarifying-questions / priced-proposal pipeline.

The LLM only ever produces StructuredTripBrief and HotelSelection. Every price in
PricedProposal is computed by the deterministic engine (engine.py), never by the LLM.
Money fields use Decimal, never float, to avoid financial rounding error.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

FieldState = Literal["explicit", "inferred", "missing", "contradictory"]


class TripLeg(BaseModel):
    """One destination/city within a trip. Most inquiries have exactly one leg.

    `provenance` records, per field name, whether the LLM extraction call read the
    value directly off the client's words ("stated") or had to make a judgment call
    ("inferred") -- so a downstream reader can tell a confirmed fact from a guess
    without re-reading the raw inquiry.
    """

    destination: str
    nights: Optional[int] = Field(None, ge=1)
    nights_options: Optional[list[int]] = Field(
        None, description="A range the client gave instead of a single number, e.g. [7, 8]."
    )
    travellers_count: Optional[int] = Field(None, ge=1)
    provenance: dict[str, Literal["stated", "inferred"]] = Field(default_factory=dict)

    @field_validator("nights_options")
    @classmethod
    def _options_need_at_least_two(cls, v: Optional[list[int]]) -> Optional[list[int]]:
        if v is not None and len(v) < 2:
            raise ValueError("nights_options must have at least 2 values; use `nights` for a single confirmed value")
        return v


class StructuredTripBrief(BaseModel):
    """What the LLM extracts from the raw client inquiry. Contains no prices."""

    brief_id: str
    legs: list[TripLeg]
    dates_or_window: Optional[str] = Field(None, description="e.g. 'March', 'spring break'")
    budget_usd: Optional[Decimal] = Field(None, ge=0)
    preferences: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    traveller_notes: Optional[str] = None

    @field_validator("legs")
    @classmethod
    def _at_least_one_leg(cls, v: list[TripLeg]) -> list[TripLeg]:
        if not v:
            raise ValueError("at least one trip leg must be extracted")
        return v


class LegAudit(BaseModel):
    """Missing-information classification for one leg -- a dedicated judgment, not an
    afterthought folded into the clarifying-questions prompt."""

    destination: str
    nights: FieldState
    travellers_count: FieldState
    notes: list[str] = Field(default_factory=list)


class BriefAudit(BaseModel):
    """Every field in the brief classified as explicit / inferred / missing /
    contradictory. See oltaflock.info_audit.classify_brief."""

    legs: list[LegAudit]
    budget: FieldState


class ClientQuestions(BaseModel):
    """Advisor-ready follow-up questions for what's missing or unclear in the inquiry."""

    brief_id: str
    questions: list[str] = Field(default_factory=list)


class LineItem(BaseModel):
    description: str
    nights: int
    rate_per_night: Decimal
    room_count: int = 1
    subtotal: Decimal


class PricedLeg(BaseModel):
    destination: str
    status: Literal["priced", "to_be_quoted"]
    hotel_name: Optional[str] = None
    line_item: Optional[LineItem] = None
    budget_exceeded: bool = False
    unmet_reasons: list[str] = Field(default_factory=list)
    assumed_fields: list[str] = Field(
        default_factory=list,
        description="Fields computed by assumption rather than confirmed by the client, "
        "e.g. 'room_count' when a multi-room combination was inferred from a minimum-"
        "occupancy calculation.",
    )


class PricedProposal(BaseModel):
    brief_id: str
    inquiry_id: str
    status: Literal["priced", "partial", "to_be_quoted"]
    legs: list[PricedLeg]
    day_by_day_outline: list[str] = Field(default_factory=list)
    # total_price_usd is populated ONLY when every leg priced (status == "priced") --
    # a complete trip total. priced_subtotal_usd is the sum of just the priced legs and
    # is populated whenever at least one leg priced, including on a "partial" proposal.
    # These are never the same field, so a partial sum can never be mistaken for a
    # complete total.
    total_price_usd: Optional[Decimal] = None
    priced_subtotal_usd: Optional[Decimal] = None
    narration: Optional[str] = None


class InquiryResult(BaseModel):
    """Full output bundle for one client inquiry: brief + questions + proposal."""

    inquiry_id: str
    raw_inquiry: str
    brief: StructuredTripBrief
    info_audit: BriefAudit
    client_questions: ClientQuestions
    proposal: PricedProposal
