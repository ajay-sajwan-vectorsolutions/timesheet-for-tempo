"""
Unit tests for ScheduleManager (tempo_automation.py lines 481-1122).

Tests cover:
- is_working_day() priority chain (7 levels)
- get_holiday_name()
- count_working_days() / get_expected_hours()
- check_year_end_warning()
- get_month_calendar()
- add_pto() / remove_pto()
- add_extra_holidays() / remove_extra_holidays()
- add_working_days() / remove_working_days()
- _validate_date()
- _save_schedule_to_config() (patched CONFIG_FILE)
- Org holidays parsing (common + state-specific)
- Remote fetch (skipped on network error, updates when version differs)
"""

import json
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Ensure project root is on sys.path (conftest.py already does this,
# but make the module self-contained for direct invocation too).
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tempo_automation import ScheduleManager  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: build a ScheduleManager with all file I/O patched out
# ---------------------------------------------------------------------------

def _make_schedule_manager(
    config: dict,
    org_holidays_data: dict | None = None,
    *,
    tmp_path: Path | None = None,
) -> ScheduleManager:
    """
    Construct a ScheduleManager with:
    - ORG_HOLIDAYS_FILE pointing to a temp file that contains
      ``org_holidays_data`` (or an empty dict if None).
    - _fetch_remote_org_holidays patched out so no real HTTP call is made.
    - CONFIG_FILE pointing to a temp path so _save_schedule_to_config()
      writes to a throwaway file.

    If ``tmp_path`` is None a temporary directory is created automatically
    using ``pytest``'s ``tmp_path`` fixture approach (i.e. a simple
    ``Path`` object backed by ``tempfile.mkdtemp``).
    """
    import tempfile

    if tmp_path is None:
        tmp_dir = Path(tempfile.mkdtemp())
    else:
        tmp_dir = tmp_path

    # Write org holidays fixture to a temp file
    hol_path = tmp_dir / "org_holidays.json"
    hol_path.write_text(
        json.dumps(org_holidays_data or {}, indent=2), encoding="utf-8"
    )
    cfg_path = tmp_dir / "config.json"

    with (
        patch("tempo_automation.ORG_HOLIDAYS_FILE", hol_path),
        patch("tempo_automation.CONFIG_FILE", cfg_path),
        patch.object(
            ScheduleManager,
            "_fetch_remote_org_holidays",
            return_value=None,
        ),
    ):
        sm = ScheduleManager(config)

    # Stash paths on the instance so individual tests can inspect them
    sm._test_hol_path = hol_path
    sm._test_cfg_path = cfg_path
    return sm


# ---------------------------------------------------------------------------
# Shared org holidays data (matching tests/fixtures/org_holidays.json)
# ---------------------------------------------------------------------------

ORG_HOLIDAYS_DATA = {
    "version": "2026.1",
    "holidays": {
        "US": {
            "2026": {
                "common": [
                    {"date": "2026-01-01", "name": "New Year's Day"},
                    {"date": "2026-01-19", "name": "Martin Luther King Jr. Day"},
                    {"date": "2026-05-25", "name": "Memorial Day"},
                    {"date": "2026-07-04", "name": "Independence Day (Observed)"},
                    {"date": "2026-09-07", "name": "Labor Day"},
                    {"date": "2026-11-26", "name": "Thanksgiving Day"},
                    {"date": "2026-12-25", "name": "Christmas Day"},
                ]
            },
            "2027": {
                "common": [
                    {"date": "2027-01-01", "name": "New Year's Day"},
                ]
            },
        },
        "IN": {
            "2026": {
                "common": [
                    {"date": "2026-01-26", "name": "Republic Day"},
                    {"date": "2026-08-15", "name": "Independence Day"},
                    {"date": "2026-10-02", "name": "Gandhi Jayanti"},
                ],
                "MH": [
                    {"date": "2026-05-01", "name": "Maharashtra Day"},
                ],
            }
        },
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_config():
    """Minimal schedule config for a US developer with no overrides."""
    return {
        "schedule": {
            "daily_hours": 8.0,
            "country_code": "US",
            "state": "",
            "pto_days": [],
            "extra_holidays": [],
            "working_days": [],
        },
        "organization": {"holidays_url": ""},
    }


@pytest.fixture
def sm(base_config, tmp_path):
    """ScheduleManager with US org holidays, no overrides."""
    return _make_schedule_manager(base_config, ORG_HOLIDAYS_DATA, tmp_path=tmp_path)


# ===========================================================================
# 1. Construction & org holidays parsing
# ===========================================================================

class TestConstruction:
    def test_daily_hours_defaults_to_8(self, tmp_path):
        config = {"schedule": {}, "organization": {"holidays_url": ""}}
        sm = _make_schedule_manager(config, {}, tmp_path=tmp_path)
        assert sm.daily_hours == 8

    def test_custom_daily_hours(self, base_config, tmp_path):
        base_config["schedule"]["daily_hours"] = 7.5
        sm = _make_schedule_manager(base_config, ORG_HOLIDAYS_DATA, tmp_path=tmp_path)
        assert sm.daily_hours == 7.5

    def test_country_code_defaults_to_us(self, tmp_path):
        config = {"schedule": {}, "organization": {"holidays_url": ""}}
        sm = _make_schedule_manager(config, {}, tmp_path=tmp_path)
        assert sm.country_code == "US"

    def test_org_holidays_parsed_common(self, sm):
        """Common US org holidays are loaded into _org_holidays."""
        assert "2026-12-25" in sm._org_holidays
        assert sm._org_holidays["2026-12-25"] == "Christmas Day"

    def test_org_holidays_excludes_other_country(self, sm):
        """IN holidays must NOT appear in a US-configured manager."""
        assert "2026-01-26" not in sm._org_holidays  # Republic Day (IN)

    def test_org_holidays_state_specific(self, base_config, tmp_path):
        """State-specific holidays are loaded when state is set."""
        base_config["schedule"]["country_code"] = "IN"
        base_config["schedule"]["state"] = "MH"
        sm = _make_schedule_manager(base_config, ORG_HOLIDAYS_DATA, tmp_path=tmp_path)
        assert "2026-05-01" in sm._org_holidays
        assert sm._org_holidays["2026-05-01"] == "Maharashtra Day"

    def test_org_holidays_no_state_excludes_state_entries(self, base_config, tmp_path):
        """Without a state, state-specific entries are NOT included."""
        base_config["schedule"]["country_code"] = "IN"
        base_config["schedule"]["state"] = ""
        sm = _make_schedule_manager(base_config, ORG_HOLIDAYS_DATA, tmp_path=tmp_path)
        # Common IN holiday should be present, state holiday should not
        assert "2026-01-26" in sm._org_holidays
        assert "2026-05-01" not in sm._org_holidays

    def test_missing_org_holidays_file(self, base_config, tmp_path):
        """Missing org_holidays.json does not crash -- empty dict used."""
        absent_path = tmp_path / "missing_org_holidays.json"
        cfg_path = tmp_path / "config.json"
        with (
            patch("tempo_automation.ORG_HOLIDAYS_FILE", absent_path),
            patch("tempo_automation.CONFIG_FILE", cfg_path),
            patch.object(
                ScheduleManager, "_fetch_remote_org_holidays", return_value=None
            ),
        ):
            sm = ScheduleManager(base_config)
        assert sm._org_holidays == {}


# ===========================================================================
# 2. is_working_day() -- priority chain
# ===========================================================================

class TestIsWorkingDay:
    """Priority: working_days > pto > weekend > org_holidays >
    country_holidays > extra_holidays > default working."""

    # Priority 1: compensatory working day overrides everything
    def test_working_day_override_on_weekend(self, sm):
        """A Saturday in working_days returns True."""
        sm.working_days.add("2026-02-28")  # Saturday
        is_work, reason = sm.is_working_day("2026-02-28")
        assert is_work is True
        assert "Compensatory" in reason

    def test_working_day_override_on_org_holiday(self, sm):
        """Christmas in working_days still returns True."""
        sm.working_days.add("2026-12-25")
        is_work, reason = sm.is_working_day("2026-12-25")
        assert is_work is True

    def test_working_day_override_on_pto(self, sm):
        """A PTO date in working_days returns True (override wins)."""
        sm.pto_days.add("2026-03-02")      # Monday
        sm.working_days.add("2026-03-02")  # override
        is_work, reason = sm.is_working_day("2026-03-02")
        assert is_work is True

    # Priority 2: PTO (weekday, not in working_days)
    def test_pto_weekday_returns_false(self, sm):
        sm.pto_days.add("2026-02-23")  # Monday
        is_work, reason = sm.is_working_day("2026-02-23")
        assert is_work is False
        assert reason == "PTO"

    # Priority 3: Weekend
    @pytest.mark.parametrize("date_str,expected_day", [
        ("2026-02-28", "Saturday"),
        ("2026-03-01", "Sunday"),
    ])
    def test_weekend_returns_false(self, sm, date_str, expected_day):
        is_work, reason = sm.is_working_day(date_str)
        assert is_work is False
        assert expected_day in reason

    # Priority 4: Org holiday
    def test_org_holiday_returns_false(self, sm):
        is_work, reason = sm.is_working_day("2026-12-25")
        assert is_work is False
        assert "Christmas Day" in reason

    def test_org_holiday_reason_includes_holiday_prefix(self, sm):
        is_work, reason = sm.is_working_day("2026-05-25")  # Memorial Day
        assert reason.startswith("Holiday:")

    # Priority 5: Country holidays (holidays library)
    def test_country_holiday_new_years_day(self, sm):
        """Jan 1 is both org holiday and country holiday; org takes priority."""
        is_work, reason = sm.is_working_day("2026-01-01")
        assert is_work is False

    def test_country_holiday_without_org_override(self, base_config, tmp_path):
        """A country holiday that is NOT in org_holidays is still blocked."""
        # Use empty org holidays so only the library can fire
        sm = _make_schedule_manager(base_config, {}, tmp_path=tmp_path)
        # New Year's Day -- should be in the US holidays library
        is_work, reason = sm.is_working_day("2026-01-01")
        assert is_work is False
        assert "Holiday" in reason

    # Priority 6: Extra holidays
    def test_extra_holiday_weekday_returns_false(self, sm):
        sm.extra_holidays.add("2026-03-03")  # Tuesday
        is_work, reason = sm.is_working_day("2026-03-03")
        assert is_work is False
        assert "Extra holiday" in reason

    def test_extra_holiday_does_not_block_when_working_days_override(self, sm):
        sm.extra_holidays.add("2026-03-03")
        sm.working_days.add("2026-03-03")
        is_work, _ = sm.is_working_day("2026-03-03")
        assert is_work is True

    # Priority 7: Default working day
    def test_regular_weekday_returns_true(self, sm):
        is_work, reason = sm.is_working_day("2026-02-23")  # Monday
        assert is_work is True
        assert reason == "Working day"

    def test_friday_returns_true(self, sm):
        is_work, reason = sm.is_working_day("2026-02-20")  # Friday
        assert is_work is True


# ===========================================================================
# 3. get_holiday_name()
# ===========================================================================

class TestGetHolidayName:
    def test_org_holiday_returns_name(self, sm):
        name = sm.get_holiday_name("2026-12-25")
        assert name == "Christmas Day"

    def test_non_holiday_weekday_returns_none(self, sm):
        name = sm.get_holiday_name("2026-02-23")  # Regular Monday
        assert name is None

    def test_weekend_not_a_named_holiday(self, sm):
        name = sm.get_holiday_name("2026-02-28")  # Saturday, no special name
        assert name is None

    def test_country_holiday_returns_name_when_not_in_org(self, base_config, tmp_path):
        """When org_holidays is empty, country holidays library is queried."""
        sm = _make_schedule_manager(base_config, {}, tmp_path=tmp_path)
        name = sm.get_holiday_name("2026-01-01")
        assert name is not None
        assert "New Year" in name


# ===========================================================================
# 4. count_working_days() and get_expected_hours()
# ===========================================================================

class TestCountWorkingDays:
    def test_single_working_day(self, sm):
        # 2026-02-23 is a Monday (no holidays)
        assert sm.count_working_days("2026-02-23", "2026-02-23") == 1

    def test_full_work_week(self, sm):
        # Mon-Fri, no holidays in that week
        assert sm.count_working_days("2026-02-23", "2026-02-27") == 5

    def test_week_including_weekend(self, sm):
        # Mon-Sun -- 5 working days (Sat+Sun skipped)
        assert sm.count_working_days("2026-02-23", "2026-03-01") == 5

    def test_range_with_org_holiday(self, sm):
        # Week of Memorial Day (Mon May 25 = holiday, Tue-Fri = 4 days)
        count = sm.count_working_days("2026-05-25", "2026-05-29")
        assert count == 4

    def test_range_with_pto(self, sm):
        sm.pto_days.add("2026-02-23")  # Monday
        count = sm.count_working_days("2026-02-23", "2026-02-27")
        assert count == 4

    def test_same_day_weekend_returns_zero(self, sm):
        assert sm.count_working_days("2026-02-28", "2026-02-28") == 0

    def test_get_expected_hours_two_weeks(self, sm):
        # 2 working weeks = 10 days * 8h = 80h
        hours = sm.get_expected_hours("2026-02-23", "2026-03-06")
        assert hours == 80.0

    def test_get_expected_hours_respects_daily_hours(self, base_config, tmp_path):
        base_config["schedule"]["daily_hours"] = 7.5
        sm = _make_schedule_manager(base_config, ORG_HOLIDAYS_DATA, tmp_path=tmp_path)
        hours = sm.get_expected_hours("2026-02-23", "2026-02-27")
        assert hours == 37.5  # 5 days * 7.5h


# ===========================================================================
# 5. check_year_end_warning()
# ===========================================================================

class TestCheckYearEndWarning:
    def test_non_december_returns_none(self, sm):
        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 6, 15)
            result = sm.check_year_end_warning()
        assert result is None

    def test_december_with_next_year_data_returns_none(self, sm):
        """2027 data exists in fixture -- no warning in December 2026."""
        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 12, 1)
            result = sm.check_year_end_warning()
        assert result is None

    def test_december_missing_next_year_returns_warning(self, base_config, tmp_path):
        """2028 data is missing -- warning should fire in December 2027."""
        sm = _make_schedule_manager(base_config, ORG_HOLIDAYS_DATA, tmp_path=tmp_path)
        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2027, 12, 1)
            result = sm.check_year_end_warning()
        assert result is not None
        assert "2028" in result
        assert "WARNING" in result

    def test_december_missing_state_returns_state_warning(self, base_config, tmp_path):
        """2027 common US data exists but no state-level entry -- state warning fires."""
        base_config["schedule"]["state"] = "CA"
        # ORG_HOLIDAYS_DATA has 2027.US.common but no 2027.US.CA
        sm = _make_schedule_manager(base_config, ORG_HOLIDAYS_DATA, tmp_path=tmp_path)
        with patch("tempo_automation.date") as mock_date:
            mock_date.today.return_value = date(2026, 12, 1)
            result = sm.check_year_end_warning()
        assert result is not None
        assert "CA" in result


# ===========================================================================
# 6. get_month_calendar()
# ===========================================================================

class TestGetMonthCalendar:
    def test_returns_correct_number_of_days(self, sm):
        days = sm.get_month_calendar(2026, 2)  # Feb 2026 has 28 days
        assert len(days) == 28

    def test_day_dict_has_required_keys(self, sm):
        days = sm.get_month_calendar(2026, 2)
        required = {"date", "day", "weekday", "day_name", "status", "label", "reason"}
        for d in days:
            assert required.issubset(d.keys())

    def test_weekend_status(self, sm):
        days = sm.get_month_calendar(2026, 2)
        # Feb 28 2026 = Saturday (weekday=5)
        sat = next(d for d in days if d["day"] == 28)
        assert sat["status"] == "weekend"
        assert sat["label"] == "."

    def test_working_day_status(self, sm):
        days = sm.get_month_calendar(2026, 2)
        mon = next(d for d in days if d["day"] == 23)  # Monday
        assert mon["status"] == "working"
        assert mon["label"] == "W"

    def test_org_holiday_status(self, sm):
        days = sm.get_month_calendar(2026, 12)
        christmas = next(d for d in days if d["day"] == 25)
        assert christmas["status"] == "holiday"
        assert christmas["label"] == "H"

    def test_pto_status(self, sm):
        sm.pto_days.add("2026-02-23")
        days = sm.get_month_calendar(2026, 2)
        pto_day = next(d for d in days if d["day"] == 23)
        assert pto_day["status"] == "pto"
        assert pto_day["label"] == "PTO"

    def test_comp_working_status(self, sm):
        sm.working_days.add("2026-02-28")  # Saturday
        days = sm.get_month_calendar(2026, 2)
        sat = next(d for d in days if d["day"] == 28)
        assert sat["status"] == "comp_working"
        assert sat["label"] == "CW"

    def test_31_day_month(self, sm):
        days = sm.get_month_calendar(2026, 1)  # January
        assert len(days) == 31

    def test_day_numbers_are_sequential(self, sm):
        days = sm.get_month_calendar(2026, 3)
        assert [d["day"] for d in days] == list(range(1, 32))


# ===========================================================================
# 7. add_pto() / remove_pto()
# ===========================================================================

class TestAddRemovePto:
    def test_add_valid_weekday(self, sm, tmp_path):
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            added, skipped = sm.add_pto(["2026-03-02"])  # Monday
        assert "2026-03-02" in added
        assert "2026-03-02" in sm.pto_days

    def test_add_weekend_is_skipped(self, sm, tmp_path):
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            added, skipped = sm.add_pto(["2026-02-28"])  # Saturday
        assert "2026-02-28" not in added
        assert len(skipped) == 1
        assert "weekend" in skipped[0].lower()

    def test_add_duplicate_is_skipped(self, sm, tmp_path):
        sm.pto_days.add("2026-03-02")
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            added, skipped = sm.add_pto(["2026-03-02"])
        assert "2026-03-02" not in added
        assert "already" in skipped[0]

    def test_add_invalid_format_is_skipped(self, sm, tmp_path):
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            added, skipped = sm.add_pto(["not-a-date"])
        assert len(added) == 0
        assert len(skipped) == 1

    def test_add_multiple_dates(self, sm, tmp_path):
        # 2026-03-02 (Mon), 2026-03-03 (Tue), 2026-03-04 (Wed) -- all weekdays
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            added, skipped = sm.add_pto(["2026-03-02", "2026-03-03", "2026-03-04"])
        assert len(added) == 3

    def test_add_pto_saves_config(self, sm, tmp_path):
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            sm.add_pto(["2026-03-02"])
        saved = json.loads(sm._test_cfg_path.read_text(encoding="utf-8"))
        assert "2026-03-02" in saved["schedule"]["pto_days"]

    def test_remove_existing_pto(self, sm, tmp_path):
        sm.pto_days.add("2026-03-02")
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            removed = sm.remove_pto(["2026-03-02"])
        assert "2026-03-02" in removed
        assert "2026-03-02" not in sm.pto_days

    def test_remove_nonexistent_pto_returns_empty(self, sm, tmp_path):
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            removed = sm.remove_pto(["2026-09-09"])
        assert removed == []

    def test_remove_pto_saves_config(self, sm, tmp_path):
        sm.pto_days.add("2026-03-02")
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            sm.remove_pto(["2026-03-02"])
        saved = json.loads(sm._test_cfg_path.read_text(encoding="utf-8"))
        assert "2026-03-02" not in saved["schedule"].get("pto_days", [])


# ===========================================================================
# 8. add_extra_holidays() / remove_extra_holidays()
# ===========================================================================

class TestExtraHolidays:
    def test_add_extra_holiday(self, sm, tmp_path):
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            added = sm.add_extra_holidays(["2026-04-01"])
        assert "2026-04-01" in added
        assert "2026-04-01" in sm.extra_holidays

    def test_add_duplicate_extra_holiday_skipped(self, sm, tmp_path):
        sm.extra_holidays.add("2026-04-01")
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            added = sm.add_extra_holidays(["2026-04-01"])
        assert added == []

    def test_add_invalid_date_skipped(self, sm, tmp_path):
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            added = sm.add_extra_holidays(["2026-13-01"])
        assert added == []

    def test_extra_holiday_blocks_day(self, sm, tmp_path):
        sm.extra_holidays.add("2026-03-03")
        is_work, reason = sm.is_working_day("2026-03-03")
        assert is_work is False
        assert "Extra holiday" in reason

    def test_remove_extra_holiday(self, sm, tmp_path):
        sm.extra_holidays.add("2026-04-01")
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            removed = sm.remove_extra_holidays(["2026-04-01"])
        assert "2026-04-01" in removed
        assert "2026-04-01" not in sm.extra_holidays

    def test_remove_nonexistent_extra_holiday(self, sm, tmp_path):
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            removed = sm.remove_extra_holidays(["2026-04-01"])
        assert removed == []


# ===========================================================================
# 9. add_working_days() / remove_working_days()
# ===========================================================================

class TestWorkingDays:
    def test_add_compensatory_working_day(self, sm, tmp_path):
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            added = sm.add_working_days(["2026-02-28"])  # Saturday
        assert "2026-02-28" in added
        assert "2026-02-28" in sm.working_days

    def test_add_duplicate_working_day_skipped(self, sm, tmp_path):
        sm.working_days.add("2026-02-28")
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            added = sm.add_working_days(["2026-02-28"])
        assert added == []

    def test_add_working_day_saves_config(self, sm, tmp_path):
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            sm.add_working_days(["2026-02-28"])
        saved = json.loads(sm._test_cfg_path.read_text(encoding="utf-8"))
        assert "2026-02-28" in saved["schedule"]["working_days"]

    def test_remove_working_day(self, sm, tmp_path):
        sm.working_days.add("2026-02-28")
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            removed = sm.remove_working_days(["2026-02-28"])
        assert "2026-02-28" in removed
        assert "2026-02-28" not in sm.working_days

    def test_remove_nonexistent_working_day(self, sm, tmp_path):
        with patch("tempo_automation.CONFIG_FILE", sm._test_cfg_path):
            removed = sm.remove_working_days(["2026-02-28"])
        assert removed == []


# ===========================================================================
# 10. _validate_date()
# ===========================================================================

class TestValidateDate:
    @pytest.mark.parametrize("valid_date", [
        "2026-01-01",
        "2026-12-31",
        "2027-06-15",
        "2000-02-29",  # leap year
    ])
    def test_valid_dates_return_true(self, sm, valid_date):
        assert sm._validate_date(valid_date) is True

    @pytest.mark.parametrize("invalid_date", [
        "26-01-01",          # 2-digit year
        "2026/01/01",        # slashes instead of hyphens (non-digit chars)
        "2026-13-01",        # month 13
        "2026-00-01",        # month 0
        "2026-01-32",        # day 32
        "not-a-date",        # non-digit chars
        "",                  # empty string
        "2026 01 01",        # spaces (non-digit chars)
    ])
    def test_invalid_dates_return_false(self, sm, invalid_date):
        assert sm._validate_date(invalid_date) is False

    def test_single_digit_month_day_is_valid(self, sm):
        """strptime('%Y-%m-%d') accepts single-digit month/day -- this is valid."""
        # The implementation uses strptime which accepts "2026-1-1" as a valid date.
        # This documents that _validate_date does NOT enforce zero-padding.
        assert sm._validate_date("2026-1-1") is True

    def test_date_with_non_digit_chars_fails_character_check(self, sm):
        """Dates containing symbols other than digits and hyphens must fail."""
        assert sm._validate_date("2026-0$-01") is False


# ===========================================================================
# 11. Remote fetch behaviour
# ===========================================================================

class TestRemoteFetch:
    def test_fetch_updates_file_when_version_differs(self, base_config, tmp_path):
        """When remote version differs from local, the file is written."""
        local_data = {"version": "old", "holidays": {}}
        hol_path = tmp_path / "org_holidays.json"
        hol_path.write_text(json.dumps(local_data), encoding="utf-8")
        cache_path = tmp_path / "org_holidays_cache.json"
        cfg_path = tmp_path / "config.json"

        remote_data = {"version": "new", "holidays": {"US": {}}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = remote_data
        mock_resp.raise_for_status.return_value = None
        mock_resp.headers = {"ETag": "", "Last-Modified": ""}

        base_config["organization"]["holidays_url"] = "https://example.com/holidays.json"

        with (
            patch("tempo_automation.ORG_HOLIDAYS_FILE", hol_path),
            patch("tempo_automation.ORG_HOLIDAYS_CACHE_FILE", cache_path),
            patch("tempo_automation.CONFIG_FILE", cfg_path),
            patch("tempo_automation.requests.get", return_value=mock_resp) as mock_get,
        ):
            sm = ScheduleManager(base_config)

        mock_get.assert_called_once_with(
            "https://example.com/holidays.json",
            headers={}, timeout=10
        )
        written = json.loads(hol_path.read_text(encoding="utf-8"))
        assert written["version"] == "new"

    def test_fetch_skipped_when_no_url(self, base_config, tmp_path):
        """No HTTP call is made if holidays_url is empty."""
        base_config["organization"]["holidays_url"] = ""
        hol_path = tmp_path / "org_holidays.json"
        hol_path.write_text(json.dumps(ORG_HOLIDAYS_DATA), encoding="utf-8")
        cache_path = tmp_path / "org_holidays_cache.json"
        cfg_path = tmp_path / "config.json"

        with (
            patch("tempo_automation.ORG_HOLIDAYS_FILE", hol_path),
            patch("tempo_automation.ORG_HOLIDAYS_CACHE_FILE", cache_path),
            patch("tempo_automation.CONFIG_FILE", cfg_path),
            patch("tempo_automation.requests.get") as mock_get,
        ):
            sm = ScheduleManager(base_config)

        mock_get.assert_not_called()

    def test_fetch_network_error_is_silently_handled(self, base_config, tmp_path):
        """A requests exception does not crash construction."""
        import requests as req_lib

        base_config["organization"]["holidays_url"] = "https://example.com/h.json"
        hol_path = tmp_path / "org_holidays.json"
        hol_path.write_text(json.dumps(ORG_HOLIDAYS_DATA), encoding="utf-8")
        cache_path = tmp_path / "org_holidays_cache.json"
        cfg_path = tmp_path / "config.json"

        with (
            patch("tempo_automation.ORG_HOLIDAYS_FILE", hol_path),
            patch("tempo_automation.ORG_HOLIDAYS_CACHE_FILE", cache_path),
            patch("tempo_automation.CONFIG_FILE", cfg_path),
            patch(
                "tempo_automation.requests.get",
                side_effect=req_lib.ConnectionError("unreachable"),
            ),
        ):
            sm = ScheduleManager(base_config)  # must not raise

        # Org holidays should still be available from the local file
        assert "2026-12-25" in sm._org_holidays

    def test_fetch_always_overwrites_local_file(self, base_config, tmp_path):
        """Remote data always overwrites local file (URL is source of truth)."""
        same_data = dict(ORG_HOLIDAYS_DATA)
        hol_path = tmp_path / "org_holidays.json"
        hol_path.write_text(json.dumps({"version": "old"}), encoding="utf-8")
        cache_path = tmp_path / "org_holidays_cache.json"
        cfg_path = tmp_path / "config.json"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = same_data
        mock_resp.raise_for_status.return_value = None
        mock_resp.headers = {"ETag": "", "Last-Modified": ""}

        base_config["organization"]["holidays_url"] = "https://example.com/h.json"

        with (
            patch("tempo_automation.ORG_HOLIDAYS_FILE", hol_path),
            patch("tempo_automation.ORG_HOLIDAYS_CACHE_FILE", cache_path),
            patch("tempo_automation.CONFIG_FILE", cfg_path),
            patch("tempo_automation.requests.get", return_value=mock_resp),
        ):
            sm = ScheduleManager(base_config)

        # Local file should contain remote data
        with open(hol_path, 'r') as f:
            saved = json.load(f)
        assert saved.get('version') == same_data.get('version')
        assert "2026-12-25" in sm._org_holidays


# ===========================================================================
# 12. Integration: is_working_day full priority chain in one scenario
# ===========================================================================

class TestPriorityChainIntegration:
    """Verify each priority level in a single ScheduleManager instance."""

    def test_full_priority_chain(self, base_config, tmp_path):
        config = dict(base_config)
        # Set up every level explicitly
        config["schedule"]["working_days"] = ["2026-02-28"]    # P1: Saturday override
        config["schedule"]["pto_days"] = ["2026-02-23"]         # P2: Monday PTO
        config["schedule"]["extra_holidays"] = ["2026-03-03"]   # P6: Tuesday
        sm = _make_schedule_manager(config, ORG_HOLIDAYS_DATA, tmp_path=tmp_path)

        # P1: Saturday in working_days -> True
        is_work, reason = sm.is_working_day("2026-02-28")
        assert is_work is True and "Compensatory" in reason

        # P2: PTO -> False
        is_work, reason = sm.is_working_day("2026-02-23")
        assert is_work is False and reason == "PTO"

        # P3: Normal Sunday -> False
        is_work, reason = sm.is_working_day("2026-03-01")
        assert is_work is False and "Sunday" in reason

        # P4: Org holiday (Christmas) -> False
        is_work, reason = sm.is_working_day("2026-12-25")
        assert is_work is False and "Christmas" in reason

        # P6: Extra holiday -> False
        is_work, reason = sm.is_working_day("2026-03-03")
        assert is_work is False and "Extra holiday" in reason

        # P7: Default working day -> True
        is_work, reason = sm.is_working_day("2026-02-24")
        assert is_work is True and reason == "Working day"
