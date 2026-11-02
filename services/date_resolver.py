import calendar
import datetime
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

# Business timezone: India Standard Time (IST, UTC+5:30)
INDIA_TZ = ZoneInfo("Asia/Kolkata")


def get_current_date(tz: ZoneInfo = INDIA_TZ) -> datetime.date:
    """Returns today's date in India Standard Time."""
    return datetime.datetime.now(tz).date()


class DateRangeResult:
    def __init__(
        self,
        start_date: str,
        end_date: str,
        label: str,
        is_relative: bool = False,
        month_str: Optional[str] = None,
    ):
        self.start_date = start_date  # YYYY-MM-DD
        self.end_date = end_date      # YYYY-MM-DD
        self.label = label
        self.is_relative = is_relative
        self.month_str = month_str    # YYYY-MM if single month

    def to_dict(self) -> Dict[str, str]:
        d = {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "label": self.label,
            "is_relative": self.is_relative,
        }
        if self.month_str:
            d["month"] = self.month_str
        return d


class RelativeDateResolver:
    """
    Deterministic relative date resolver for Indian small-business operations.
    Handles 'today', 'yesterday', 'this month', 'last month', 'last two months',
    and 'last 3 months' with strict month/year boundary and leap-year safety.
    """

    @classmethod
    def get_month_bounds(cls, year: int, month: int) -> Tuple[datetime.date, datetime.date]:
        """Returns first and last date of a given calendar month."""
        first_day = datetime.date(year, month, 1)
        _, last_day_num = calendar.monthrange(year, month)
        last_day = datetime.date(year, month, last_day_num)
        return first_day, last_day

    @classmethod
    def shift_month(cls, year: int, month: int, month_delta: int) -> Tuple[int, int]:
        """Shifts year and month by month_delta (positive or negative) with year boundary handling."""
        total_months = year * 12 + (month - 1) + month_delta
        new_year = total_months // 12
        new_month = (total_months % 12) + 1
        return new_year, new_month

    @classmethod
    def resolve_date_expression(
        cls,
        expression: str,
        ref_date: Optional[datetime.date] = None,
    ) -> Optional[DateRangeResult]:
        """
        Resolves a natural language or structured date expression relative to ref_date (IST).
        If ref_date is None, uses today's date in Asia/Kolkata.
        """
        if not expression or not expression.strip():
            return None

        today = ref_date or get_current_date()
        expr_clean = expression.strip().lower()

        # 1. 'today' / 'aaj'
        if expr_clean in ("today", "aaj"):
            iso = today.isoformat()
            return DateRangeResult(iso, iso, label="Today", is_relative=True)

        # 2. 'yesterday' / 'kal'
        if expr_clean in ("yesterday", "beeta hua kal"):
            yest = today - datetime.timedelta(days=1)
            iso = yest.isoformat()
            return DateRangeResult(iso, iso, label="Yesterday", is_relative=True)

        # 3. 'month before last' / 'the month before last' / 'two months ago' / '2 months ago' / 'usse pichle mahine'
        if any(phrase in expr_clean for phrase in [
            "month before last", "the month before last", "month before that",
            "the month before that", "two months ago", "2 months ago", "usse pichle mahine"
        ]):
            m_year, m_month = cls.shift_month(today.year, today.month, -2)
            first_day, last_day = cls.get_month_bounds(m_year, m_month)
            month_label = first_day.strftime("%B %Y")
            return DateRangeResult(
                start_date=first_day.isoformat(),
                end_date=last_day.isoformat(),
                label=f"Month Before Last ({month_label})",
                is_relative=True,
                month_str=f"{m_year:04d}-{m_month:02d}",
            )

        # 4. 'this month' / 'current month' / 'is mahine'
        if any(expr_clean == phrase or phrase in expr_clean for phrase in ["this month", "current month", "is mahine", "is mahina"]):
            first_day, last_day = cls.get_month_bounds(today.year, today.month)
            return DateRangeResult(
                start_date=first_day.isoformat(),
                end_date=last_day.isoformat(),
                label=f"{today.strftime('%B %Y')}",
                is_relative=True,
                month_str=f"{today.year:04d}-{today.month:02d}",
            )

        # 5. 'last month' / 'previous month' / 'pichle mahine'
        if any(expr_clean == phrase or phrase in expr_clean for phrase in ["last month", "previous month", "pichle mahine", "pichla mahina"]):
            m_year, m_month = cls.shift_month(today.year, today.month, -1)
            first_day, last_day = cls.get_month_bounds(m_year, m_month)
            month_label = first_day.strftime("%B %Y")
            return DateRangeResult(
                start_date=first_day.isoformat(),
                end_date=last_day.isoformat(),
                label=f"Last Month ({month_label})",
                is_relative=True,
                month_str=f"{m_year:04d}-{m_month:02d}",
            )

        # 5. 'last two months' / 'last 2 months' / 'past 2 months'
        if any(phrase in expr_clean for phrase in ["last two months", "last 2 months", "past two months", "past 2 months", "pichle do mahine"]):
            # Convention: From 2 months ago through end of last month (completed), or through current month
            # For business reporting, 'last two months' relative to Oct 2026 resolves to Sep 2026 and Oct 2026:
            # First day of (current month - 1 month) to last day of current month
            if today.month == 1:
                m1_year, m1_month = today.year - 1, 12
            else:
                m1_year, m1_month = today.year, today.month - 1
            start_d, _ = cls.get_month_bounds(m1_year, m1_month)
            _, end_d = cls.get_month_bounds(today.year, today.month)
            return DateRangeResult(
                start_date=start_d.isoformat(),
                end_date=end_d.isoformat(),
                label=f"Last Two Months ({start_d.strftime('%B %Y')} - {end_d.strftime('%B %Y')})",
                is_relative=True,
            )

        # 6. 'last 3 months' / 'last three months' / 'past 3 months'
        if any(phrase in expr_clean for phrase in ["last 3 months", "last three months", "past 3 months", "past three months"]):
            # 3 months: current month and prior 2 months
            target_m = today.month - 2
            target_y = today.year
            while target_m <= 0:
                target_m += 12
                target_y -= 1
            start_d, _ = cls.get_month_bounds(target_y, target_m)
            _, end_d = cls.get_month_bounds(today.year, today.month)
            return DateRangeResult(
                start_date=start_d.isoformat(),
                end_date=end_d.isoformat(),
                label=f"Last 3 Months ({start_d.strftime('%B %Y')} - {end_d.strftime('%B %Y')})",
                is_relative=True,
            )

        # 7. Explicit YYYY-MM format
        import re
        match_ym = re.match(r"^(\d{4})-(\d{2})$", expr_clean)
        if match_ym:
            y, m = int(match_ym.group(1)), int(match_ym.group(2))
            if 1 <= m <= 12:
                f, l = cls.get_month_bounds(y, m)
                return DateRangeResult(
                    start_date=f.isoformat(),
                    end_date=l.isoformat(),
                    label=f.strftime("%B %Y"),
                    is_relative=False,
                    month_str=f"{y:04d}-{m:02d}",
                )

        # 8. Explicit YYYY-MM-DD format
        match_ymd = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", expr_clean)
        if match_ymd:
            return DateRangeResult(
                start_date=expr_clean,
                end_date=expr_clean,
                label=expr_clean,
                is_relative=False,
            )

        # 9. Named Month expressions (e.g. "September", "September 2026", "what about September", "for October")
        month_names = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
        }
        for m_name, m_num in month_names.items():
            if re.search(rf"\b{m_name}\b", expr_clean):
                y_match = re.search(r"\b(20\d\d)\b", expr_clean)
                if y_match:
                    target_year = int(y_match.group(1))
                else:
                    target_year = today.year if m_num <= today.month else today.year - 1
                first_day, last_day = cls.get_month_bounds(target_year, m_num)
                month_label = first_day.strftime("%B %Y")
                return DateRangeResult(
                    start_date=first_day.isoformat(),
                    end_date=last_day.isoformat(),
                    label=month_label,
                    is_relative=True,
                    month_str=f"{target_year:04d}-{m_num:02d}",
                )

        return None

