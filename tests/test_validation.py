"""Tests for the defensive validation layer -- rejecting malformed rate-sheet rows
and unknown hotel_ids before they reach pricing arithmetic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest  # noqa: E402

from oltaflock.validators import (  # noqa: E402
    RateSheetValidationError,
    assert_known_hotel_id,
    validate_rate_sheet_row,
)


def _row(**overrides):
    base = {"hotel_id": "h1", "price_per_night": "100", "sleeps": "2"}
    base.update(overrides)
    return base


def test_valid_row_passes():
    validate_rate_sheet_row(_row())  # must not raise


def test_negative_price_is_rejected():
    with pytest.raises(RateSheetValidationError):
        validate_rate_sheet_row(_row(price_per_night="-50"))


def test_zero_price_is_rejected():
    with pytest.raises(RateSheetValidationError):
        validate_rate_sheet_row(_row(price_per_night="0"))


def test_unparseable_price_is_rejected():
    with pytest.raises(RateSheetValidationError):
        validate_rate_sheet_row(_row(price_per_night="not-a-number"))


def test_zero_or_negative_sleeps_is_rejected():
    with pytest.raises(RateSheetValidationError):
        validate_rate_sheet_row(_row(sleeps="0"))
    with pytest.raises(RateSheetValidationError):
        validate_rate_sheet_row(_row(sleeps="-1"))


def test_known_hotel_id_passes():
    assert_known_hotel_id("h1", {"h1", "h2"})  # must not raise


def test_unknown_hotel_id_is_rejected():
    with pytest.raises(RateSheetValidationError):
        assert_known_hotel_id("not_on_sheet", {"h1", "h2"})
