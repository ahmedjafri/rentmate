"""Tests for db/utils.py phone normalization."""

from db.utils import normalize_phone


class TestNormalizePhone:
    def test_none_for_empty_string(self):
        assert normalize_phone("") is None

    def test_none_for_whitespace_only(self):
        assert normalize_phone("   ") is None

    def test_strips_formatting_chars(self):
        assert normalize_phone("(555) 123-4567") == "+15551234567"

    def test_strips_dots(self):
        assert normalize_phone("555.123.4567") == "+15551234567"

    def test_ten_digit_prepends_plus_one(self):
        assert normalize_phone("5551234567") == "+15551234567"

    def test_preserves_plus_country_code(self):
        assert normalize_phone("+447911123456") == "+447911123456"

    def test_uk_number_with_formatting(self):
        assert normalize_phone("+44 79 1112 3456") == "+447911123456"

    def test_eleven_digit_starting_with_one(self):
        assert normalize_phone("15551234567") == "+15551234567"

    def test_non_us_eleven_digit_without_plus(self):
        result = normalize_phone("92301234567")
        assert result == "+92301234567"

    def test_too_short_returns_none(self):
        assert normalize_phone("12345") is None

    def test_custom_country_code(self):
        assert normalize_phone("7911123456", default_country_code="+44") == "+447911123456"

    def test_strips_whitespace_before_processing(self):
        assert normalize_phone("  5551234567  ") == "+15551234567"

    def test_already_normalized(self):
        assert normalize_phone("+15551234567") == "+15551234567"
