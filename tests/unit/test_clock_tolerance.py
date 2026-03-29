"""Tests for SimulatedClock strict/lenient modes."""
import pytest
from datetime import datetime
from btframework.core.clock import SimulatedClock


class TestClockStrict:
    def test_strict_raises_on_out_of_order(self):
        """Strict mode raises on out-of-order timestamps."""
        clock = SimulatedClock(strict=True)
        clock.advance(datetime(2024, 1, 2))
        with pytest.raises(ValueError, match="Cannot go back in time"):
            clock.advance(datetime(2024, 1, 1))

    def test_strict_duplicate_skips(self):
        """Even in strict mode, duplicate timestamps are silently skipped."""
        clock = SimulatedClock(strict=True)
        assert clock.advance(datetime(2024, 1, 1)) is True
        assert clock.bar_index == 1
        assert clock.advance(datetime(2024, 1, 1)) is False
        assert clock.bar_index == 1  # Not incremented


class TestClockLenient:
    def test_lenient_skips_out_of_order(self):
        """Lenient mode skips out-of-order timestamps without raising."""
        clock = SimulatedClock(strict=False)
        clock.advance(datetime(2024, 1, 2))
        result = clock.advance(datetime(2024, 1, 1))
        assert result is False
        assert clock.now == datetime(2024, 1, 2)
        assert clock.bar_index == 1

    def test_lenient_skips_duplicates(self):
        """Lenient mode skips duplicate timestamps."""
        clock = SimulatedClock(strict=False)
        clock.advance(datetime(2024, 1, 1))
        result = clock.advance(datetime(2024, 1, 1))
        assert result is False
        assert clock.bar_index == 1

    def test_lenient_advances_normally(self):
        """Lenient mode advances normally for valid timestamps."""
        clock = SimulatedClock(strict=False)
        assert clock.advance(datetime(2024, 1, 1)) is True
        assert clock.advance(datetime(2024, 1, 2)) is True
        assert clock.bar_index == 2
        assert clock.now == datetime(2024, 1, 2)


class TestClockReset:
    def test_reset_clears_state(self):
        clock = SimulatedClock(strict=True)
        clock.advance(datetime(2024, 1, 1))
        clock.advance(datetime(2024, 1, 2))
        clock.reset()
        assert clock.bar_index == 0
        assert clock.started is False
