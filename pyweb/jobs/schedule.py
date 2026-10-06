"""Cron expressions: ``minute hour day-of-month month day-of-week``.

Each field takes ``*``, numbers, ranges ``1-5``, steps ``*/15`` or ``10-50/20``,
lists ``1,15,30`` and names (``jan``..``dec``, ``sun``..``sat``; 0 and 7 are
Sunday). ``@hourly``, ``@daily``/``@midnight``, ``@weekly``, ``@monthly`` and
``@yearly``/``@annually`` are shorthands. As in standard cron, when both
day-of-month and day-of-week are restricted, a day matching either fires.

Times are wall-clock times in ``tz`` (any IANA name; UTC by default). Around
daylight-saving changes, a time that doesn't exist that day is skipped and a
time that happens twice fires once (the first time).
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
DAYS = {d: i for i, d in enumerate("sun mon tue wed thu fri sat".split())}
SHORTHANDS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
              "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}
FIELDS = (("minute", 0, 59, None), ("hour", 0, 23, None), ("day of month", 1, 31, None),
          ("month", 1, 12, MONTHS), ("day of week", 0, 7, DAYS))


class CronError(ValueError):
    pass


def _number(text, names, what):
    text = text.lower()
    if names and text in names:
        return names[text]
    if not text.isdigit():
        raise CronError(f"{what}: {text!r} isn't a number" + (" or a name" if names else ""))
    return int(text)


def _field(text, lo, hi, names, what):
    values = set()
    for part in text.split(","):
        if not part:
            raise CronError(f"{what}: empty list item")
        base, _, step = part.partition("/")
        if step:
            if not step.isdigit() or int(step) == 0:
                raise CronError(f"{what}: bad step {step!r}")
            step = int(step)
        else:
            step = 1
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            start, end = _number(a, names, what), _number(b, names, what)
        else:
            start = _number(base, names, what)
            end = hi if part.count("/") else start
        if not (lo <= start <= hi and lo <= end <= hi) or start > end:
            raise CronError(f"{what}: {part!r} is outside {lo}-{hi}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


class Cron:
    """A parsed expression. ``next_after(moment)`` is the next firing time (aware, in ``tz``)."""

    def __init__(self, expr, tz="UTC"):
        self.expr = expr
        text = SHORTHANDS.get(expr.strip().lower(), expr)
        parts = text.split()
        if len(parts) != 5:
            raise CronError(f"{expr!r}: a cron expression has 5 fields (minute hour day month weekday)")
        sets = [_field(p, lo, hi, names, what) for p, (what, lo, hi, names) in zip(parts, FIELDS)]
        self.minutes, self.hours, self.days, self.months, dows = sets
        self.dows = frozenset(d % 7 for d in dows)
        self.any_day = parts[2] == "*"
        self.any_dow = parts[4] == "*"
        self.tz = ZoneInfo(tz) if isinstance(tz, str) else tz
        if not any(self._day_ok(dt.date(2000 + y, m, d)) for y in range(28) for m in self.months
                   for d in self.days if d <= 31 and _valid(2000 + y, m, d)):
            raise CronError(f"{expr!r} never fires")

    def _day_ok(self, day):
        dom = day.day in self.days
        dow = (day.isoweekday() % 7) in self.dows
        if self.any_day and self.any_dow:
            return True
        if self.any_day:
            return dow
        if self.any_dow:
            return dom
        return dom or dow                                   # both restricted: either matches

    def next_after(self, moment):
        """The first firing time strictly after ``moment`` (an aware datetime)."""
        if moment.tzinfo is None:
            raise ValueError("next_after needs an aware datetime")
        local = moment.astimezone(self.tz)
        day = local.date()
        start_minute = local.hour * 60 + local.minute + 1      # strictly after (seconds dropped)
        for _ in range(366 * 8):
            if day.month in self.months and self._day_ok(day):
                for hour in sorted(self.hours):
                    for minute in sorted(self.minutes):
                        if hour * 60 + minute < start_minute:
                            continue
                        wall = dt.datetime(day.year, day.month, day.day, hour, minute)
                        when = _resolve(wall, self.tz)
                        if when is not None and when > moment:
                            return when
            day += dt.timedelta(days=1)
            start_minute = 0
        raise CronError(f"{self.expr!r} doesn't fire in the next 8 years")

    def slots(self, after, until, limit=100):
        """Firing times in ``(after, until]``, oldest first, at most ``limit``."""
        out = []
        moment = after
        while len(out) < limit:
            moment = self.next_after(moment)
            if moment > until:
                break
            out.append(moment)
        return out


def _valid(y, m, d):
    try:
        dt.date(y, m, d)
    except ValueError:
        return False
    return True


def _resolve(wall, tz):
    """The aware time for a wall-clock time: None if it doesn't exist (spring forward); the earlier
    one when it happens twice (fall back)."""
    first = wall.replace(tzinfo=tz, fold=0)
    if first.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != wall:
        return None                                          # skipped by a DST jump
    return first


class Every:
    """Fires every ``seconds``, aligned to the Unix epoch (so every server agrees on the slots)."""

    def __init__(self, seconds):
        if seconds < 1:
            raise CronError("an interval must be at least one second")
        self.seconds = int(seconds)
        self.expr = f"every {self.seconds}s"

    def next_after(self, moment):
        ts = moment.timestamp()
        return dt.datetime.fromtimestamp((int(ts // self.seconds) + 1) * self.seconds, UTC)

    def slots(self, after, until, limit=100):
        out = []
        moment = after
        while len(out) < limit:
            moment = self.next_after(moment)
            if moment > until:
                break
            out.append(moment)
        return out
