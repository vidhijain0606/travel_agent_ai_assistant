"""Deterministic rate-sheet engine: the ONLY source of prices.

No LLM call happens anywhere in this module. Candidate filtering and price
calculation are both plain Python against rows loaded from data/rate_sheet.csv.
Money uses Decimal throughout -- never float -- to avoid financial rounding error.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from oltaflock.schemas import LineItem, PricedLeg, TripLeg
from oltaflock.validators import assert_known_hotel_id, validate_rate_sheet_row

MAX_ROOMS_PER_HOTEL = 4  # a cap on how many identical rooms we'll combine for one leg
CENTS = Decimal("0.01")

HARD_CONSTRAINT_FIELD_MAP = {
    "wheelchair": "wheelchair_accessible",
    "accessib": "wheelchair_accessible",
    "step-free": "wheelchair_accessible",
    "quiet": "quiet_zone",
    "adults only": "adults_only",
    "adults-only": "adults_only",
}


@dataclass(frozen=True)
class RateSheetRow:
    hotel_id: str
    name: str
    city: str
    room_type: str
    sleeps: int
    price_per_night: Decimal
    currency: str
    wheelchair_accessible: bool
    quiet_zone: bool
    adults_only: bool


def load_rate_sheet(path: str | Path) -> list[RateSheetRow]:
    rows: list[RateSheetRow] = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            validate_rate_sheet_row(r)
            rows.append(
                RateSheetRow(
                    hotel_id=r["hotel_id"],
                    name=r["name"],
                    city=r["city"],
                    room_type=r["room_type"],
                    sleeps=int(r["sleeps"]),
                    price_per_night=Decimal(r["price_per_night"]),
                    currency=r["currency"],
                    wheelchair_accessible=r["wheelchair_accessible"].strip().lower() == "true",
                    quiet_zone=r["quiet_zone"].strip().lower() == "true",
                    adults_only=r["adults_only"].strip().lower() == "true",
                )
            )
    return rows


def _unmet_structured_constraints(row: RateSheetRow, hard_constraints: list[str]) -> list[str]:
    """Return the subset of hard_constraints this row fails, by structured field lookup.

    Unrecognized constraint text (no entry in HARD_CONSTRAINT_FIELD_MAP) is treated
    as unmet rather than silently ignored -- an unverifiable constraint must never be
    treated as satisfied.
    """
    unmet = []
    for constraint in hard_constraints:
        key = constraint.lower()
        field_name = next((f for k, f in HARD_CONSTRAINT_FIELD_MAP.items() if k in key), None)
        if field_name is None or not getattr(row, field_name, False):
            unmet.append(constraint)
    return unmet


@dataclass(frozen=True)
class RoomCandidate:
    """One shortlisted option for a leg: a rate-sheet row, plus how many identical
    rooms of it are needed to cover the party. room_count == 1 for an ordinary
    single-room match; > 1 only when no single room on the sheet sleeps enough
    people and multiple rooms at the SAME hotel/room-type are combined instead."""

    row: RateSheetRow
    room_count: int

    @property
    def hotel_id(self) -> str:
        return self.row.hotel_id

    @property
    def combined_capacity(self) -> int:
        return self.row.sleeps * self.room_count


class PriceMatchingEngine:
    """The single authoritative pricing seam. Every number PricedLeg carries is
    computed here, directly from RateSheetRow.price_per_night."""

    def __init__(self, rate_sheet: list[RateSheetRow]):
        self._rate_sheet = rate_sheet
        self._valid_hotel_ids = {r.hotel_id for r in rate_sheet}

    def shortlist(self, leg: TripLeg, hard_constraints: list[str]) -> list[RoomCandidate]:
        """Single-room matches first; if a row's single room is too small for the
        party, offer it combined with itself (2..MAX_ROOMS_PER_HOTEL identical rooms)
        as a multi-room candidate instead of silently dropping it. This is a same-
        hotel, same-room-type combination only -- never a cross-hotel split, since a
        real party books contiguous rooms at one property."""
        candidates: list[RoomCandidate] = []
        for row in self._rate_sheet:
            if leg.destination.lower() not in row.city.lower():
                continue
            if _unmet_structured_constraints(row, hard_constraints):
                continue

            if leg.travellers_count is None:
                candidates.append(RoomCandidate(row=row, room_count=1))
                continue

            if row.sleeps >= leg.travellers_count:
                candidates.append(RoomCandidate(row=row, room_count=1))
                continue

            room_count = math.ceil(leg.travellers_count / row.sleeps)
            if 1 < room_count <= MAX_ROOMS_PER_HOTEL:
                candidates.append(RoomCandidate(row=row, room_count=room_count))

        return candidates

    def unmet_reasons(self, leg: TripLeg, hard_constraints: list[str]) -> list[str]:
        """Explain why a leg's shortlist is empty, for a to_be_quoted response."""
        same_city = [r for r in self._rate_sheet if leg.destination.lower() in r.city.lower()]
        if not same_city:
            return [f"No rate-sheet entry for destination '{leg.destination}'"]
        reasons = []
        if leg.travellers_count is not None:
            max_combined = max(
                (r.sleeps * MAX_ROOMS_PER_HOTEL for r in same_city), default=0
            )
            if max_combined < leg.travellers_count:
                reasons.append(
                    f"No {leg.destination} room (even combining up to "
                    f"{MAX_ROOMS_PER_HOTEL} identical rooms at one hotel) covers "
                    f"{leg.travellers_count} travellers"
                )
        for row in same_city:
            reasons.extend(
                f"{row.name}: does not satisfy '{c}'"
                for c in _unmet_structured_constraints(row, hard_constraints)
            )
        return reasons or ["No rate-sheet row satisfies the stated constraints"]

    def price_tier(
        self, candidate: RoomCandidate, budget_usd: Decimal | None, nights: int
    ) -> str:
        """Relative price-tier label for this candidate, NEVER an exact dollar figure.
        Used only to give the hotel-selection LLM call a sense of affordability
        without ever showing it a price (see SOLUTION.md's price-blind guarantee)."""
        if budget_usd is None or nights <= 0:
            return "unknown"
        implied_nightly_budget = budget_usd / nights
        candidate_nightly_cost = candidate.row.price_per_night * candidate.room_count
        if implied_nightly_budget <= 0:
            return "unknown"
        ratio = candidate_nightly_cost / implied_nightly_budget
        if ratio <= Decimal("0.6"):
            return "budget"
        if ratio <= Decimal("1.1"):
            return "standard"
        return "premium"

    def price_leg(
        self,
        leg: TripLeg,
        candidate: RoomCandidate,
        budget_usd: Decimal | None,
        budget_policy: str,
        running_total: Decimal,
    ) -> PricedLeg:
        assert_known_hotel_id(candidate.hotel_id, self._valid_hotel_ids)

        row, room_count = candidate.row, candidate.room_count
        nights = leg.nights or 0
        subtotal = (row.price_per_night * room_count * nights).quantize(
            CENTS, rounding=ROUND_HALF_UP
        )
        budget_exceeded = budget_usd is not None and (running_total + subtotal) > budget_usd

        if budget_exceeded and budget_policy == "block":
            return PricedLeg(
                destination=leg.destination,
                status="to_be_quoted",
                unmet_reasons=[
                    f"Projected total ${running_total + subtotal:.2f} exceeds stated "
                    f"budget ${budget_usd:.2f} under budget_policy='block'"
                ],
            )

        room_note = f" x{room_count} rooms" if room_count > 1 else ""
        assumed_fields = ["room_count"] if room_count > 1 else []
        return PricedLeg(
            destination=leg.destination,
            status="priced",
            hotel_name=row.name,
            line_item=LineItem(
                description=f"{row.name} — {row.room_type}{room_note}",
                nights=nights,
                rate_per_night=row.price_per_night,
                room_count=room_count,
                subtotal=subtotal,
            ),
            budget_exceeded=budget_exceeded,
            assumed_fields=assumed_fields,
        )

    def to_be_quoted_leg(self, leg: TripLeg, reasons: list[str]) -> PricedLeg:
        return PricedLeg(destination=leg.destination, status="to_be_quoted", unmet_reasons=reasons)
