"""Defensive validation for data entering the pricing-critical path.

Two distinct concerns:
1. A malformed rate_sheet.csv row (negative/zero price or capacity, unparseable
   number) must be rejected at load time, loudly, rather than flowing silently into
   arithmetic.
2. A hotel_id reaching the pricing engine must be re-confirmed to actually exist on
   the loaded rate sheet -- defense-in-depth on top of the LLM's enum constraint,
   which should already make this unreachable, but "should already be unreachable"
   is not the same guarantee as "is checked."
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


class RateSheetValidationError(ValueError):
    pass


def validate_rate_sheet_row(raw: dict[str, str]) -> None:
    hotel_id = raw.get("hotel_id", "<unknown>")

    try:
        price = Decimal(raw["price_per_night"])
    except (InvalidOperation, KeyError) as exc:
        raise RateSheetValidationError(
            f"hotel_id={hotel_id}: price_per_night is not a valid number: "
            f"{raw.get('price_per_night')!r}"
        ) from exc
    if price <= 0:
        raise RateSheetValidationError(
            f"hotel_id={hotel_id}: price_per_night must be positive, got {price}"
        )

    try:
        sleeps = int(raw["sleeps"])
    except (ValueError, KeyError) as exc:
        raise RateSheetValidationError(
            f"hotel_id={hotel_id}: sleeps is not a valid integer: {raw.get('sleeps')!r}"
        ) from exc
    if sleeps <= 0:
        raise RateSheetValidationError(f"hotel_id={hotel_id}: sleeps must be positive, got {sleeps}")


def assert_known_hotel_id(hotel_id: str, valid_ids: set[str]) -> None:
    if hotel_id not in valid_ids:
        raise RateSheetValidationError(
            f"hotel_id {hotel_id!r} is not present in the loaded rate sheet -- refusing to price it"
        )
