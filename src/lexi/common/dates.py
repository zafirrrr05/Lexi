"""How a spoken day-of-month or "<day> <month>" resolves against today. C3 resolves with these; the Twin picks only spoken forms that resolve back
to the date it means, so gold stays exact."""

import calendar
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def today_ist(ts: float) -> date:
    """The ledger's calendar day (C9 view) and the Twin's: both read it from an injected clock, so they agree."""
    return datetime.fromtimestamp(ts, IST).date()


def day_of_month(d: int, today: date) -> date | None:
    """"<d> tarikh": this month if not yet passed, else next."""
    for k in (0, 1):
        y, m = today.year + (today.month + k - 1) // 12, (today.month + k - 1) % 12 + 1
        if d <= calendar.monthrange(y, m)[1] and date(y, m, d) >= today:
            return date(y, m, d)
    return None


def month_day(d: int, month: int, today: date) -> date | None:
    """"<d> <month>": this year if not yet passed, else next."""
    for y in (today.year, today.year + 1):
        if d <= calendar.monthrange(y, month)[1] and date(y, month, d) >= today:
            return date(y, month, d)
    return None
