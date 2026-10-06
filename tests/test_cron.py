"""Cron parsing and next-run times (including daylight saving), and the scheduler queuing
each slot exactly once however many servers run it."""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pyweb import jobs
from pyweb.jobs import core
from pyweb.jobs.schedule import Cron, CronError, Every
from pyweb.jobs.memory import MemoryBackend
from pyweb.jobs.worker import run_schedules

UTC = dt.timezone.utc


def at(*parts, tz=UTC):
    return dt.datetime(*parts, tzinfo=tz)


@pytest.mark.parametrize("expr,after,expected", [
    ("*/15 * * * *", at(2026, 1, 1, 10, 7), at(2026, 1, 1, 10, 15)),
    ("0 3 * * *", at(2026, 1, 1, 3, 0), at(2026, 1, 2, 3, 0)),            # strictly after
    ("0 9 * * mon-fri", at(2026, 1, 2, 12, 0), at(2026, 1, 5, 9, 0)),     # Friday noon -> Monday
    ("30 8 1 * *", at(2026, 1, 15), at(2026, 2, 1, 8, 30)),
    ("0 0 29 2 *", at(2026, 3, 1), at(2028, 2, 29)),                      # leap day
    ("0 0 13 * fri", at(2026, 1, 1), at(2026, 1, 2)),                     # 13th OR Friday
    ("@hourly", at(2026, 1, 1, 10, 0, 30), at(2026, 1, 1, 11, 0)),
    ("@weekly", at(2026, 1, 1), at(2026, 1, 4)),                          # Sunday
    ("0 12 * jan,jul 0", at(2026, 2, 1), at(2026, 7, 5, 12)),
    ("5-10/5 1 * * 7", at(2026, 1, 1), at(2026, 1, 4, 1, 5)),             # 7 is Sunday too
])
def test_next_run(expr, after, expected):
    assert Cron(expr).next_after(after) == expected


@pytest.mark.parametrize("expr", ["* * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *",
                                  "* * * * 8", "*/0 * * * *", "5-1 * * * *", "a * * * *", "1,,2 * * * *",
                                  "0 0 30 2 *", "0 0 31 4,6,9,11 *"])
def test_bad_expressions(expr):
    with pytest.raises(CronError):
        Cron(expr)


def brute(cron, after):
    """The next matching minute by checking every minute (slow and obviously right)."""
    tz = cron.tz
    t = after.astimezone(UTC).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    for _ in range(60 * 24 * 400):
        local = t.astimezone(tz)
        dom, dow = local.day in cron.days, (local.isoweekday() % 7) in cron.dows
        day_ok = (dom and dow) if (cron.any_day or cron.any_dow) else (dom or dow)
        if cron.any_day and cron.any_dow:
            day_ok = True
        elif cron.any_day:
            day_ok = dow
        elif cron.any_dow:
            day_ok = dom
        if (local.minute in cron.minutes and local.hour in cron.hours and local.month in cron.months and day_ok
                and local.fold == 0):
            return t.astimezone(tz)
        t += dt.timedelta(minutes=1)
    return None


field = st.one_of(st.just("*"), st.integers(0, 59).map(str))
expr_strategy = st.tuples(
    st.sampled_from(["*", "0", "*/7", "15,45", "5-20/5", "59"]),
    st.sampled_from(["*", "0", "2", "1-3", "*/6", "23"]),
    st.sampled_from(["*", "1", "15", "28", "1,31"]),
    st.sampled_from(["*", "3", "10-12", "*/4"]),
    st.sampled_from(["*", "0", "1-5", "6", "sun,wed"]),
).map(" ".join)


@settings(max_examples=150, deadline=None)
@given(expr_strategy, st.sampled_from(["UTC", "Europe/London", "America/New_York", "Australia/Lord_Howe"]),
       st.datetimes(min_value=dt.datetime(2025, 1, 1), max_value=dt.datetime(2027, 12, 31)))
def test_next_run_matches_a_minute_by_minute_scan(expr, tz, naive):
    try:
        cron = Cron(expr, tz)
    except CronError:
        return
    after = naive.replace(tzinfo=UTC)
    expected = brute(cron, after)
    if expected is None:
        return
    assert cron.next_after(after) == expected


def test_spring_forward_skips_the_missing_time():
    london = ZoneInfo("Europe/London")
    c = Cron("30 1 * * *", "Europe/London")                    # 01:30 doesn't exist on 2026-03-29
    first = c.next_after(at(2026, 3, 28, 12, tz=london))
    assert first == at(2026, 3, 30, 1, 30, tz=london)


def test_fall_back_fires_once():
    c = Cron("30 1 * * *", "Europe/London")                    # 01:30 happens twice on 2026-10-25
    first = c.next_after(at(2026, 10, 24, 12))
    assert first.astimezone(UTC) == at(2026, 10, 25, 0, 30)    # the first 01:30 (BST)
    second = c.next_after(first)
    assert second.astimezone(UTC) == at(2026, 10, 26, 1, 30)   # not again an hour later


def test_every_is_aligned_so_servers_agree():
    e = Every(300)
    assert e.next_after(at(2026, 1, 1, 10, 3)) == at(2026, 1, 1, 10, 5)
    assert e.slots(at(2026, 1, 1, 10, 0), at(2026, 1, 1, 10, 16)) == [at(2026, 1, 1, 10, m) for m in (5, 10, 15)]
    with pytest.raises(CronError):
        Every(0)


# ---------------------------------------------------------------- scheduler

@pytest.fixture
def schedules():
    jobs.stop_all(0)
    before_r, before_s = dict(core.REGISTRY), dict(core.SCHEDULES)
    core.SCHEDULES.clear()
    yield
    core.REGISTRY.clear()
    core.REGISTRY.update(before_r)
    core.SCHEDULES.clear()
    core.SCHEDULES.update(before_s)


def ts(*parts):
    return at(*parts).timestamp()


def test_each_slot_is_queued_once_by_many_schedulers(schedules):
    store = MemoryBackend()
    name = f"tick_{uuid.uuid4().hex[:6]}"

    @jobs.every(minutes=5, name=name)
    def tick():
        return 1

    started = ts(2026, 1, 1, 9, 59)
    for now in (ts(2026, 1, 1, 10, 0, 1), ts(2026, 1, 1, 10, 0, 2), ts(2026, 1, 1, 10, 6)):
        for _server in range(3):                                # three servers tick at the same moment
            run_schedules(store, now=now, started_at=started)
    rows = store.list(name=name)
    assert sorted(r["run_at"] for r in rows) == [ts(2026, 1, 1, 10, 0), ts(2026, 1, 1, 10, 5)]


@pytest.mark.parametrize("catchup,expected", [
    ("latest", [(2026, 1, 1, 9)]),
    ("all", [(2026, 1, 1, 7), (2026, 1, 1, 8), (2026, 1, 1, 9)]),
    ("none", []),
])
def test_catching_up_after_downtime(schedules, catchup, expected):
    store = MemoryBackend()
    name = f"hourly_{catchup}_{uuid.uuid4().hex[:6]}"

    @jobs.cron("0 * * * *", catchup=catchup, name=name)
    def hourly():
        return 1

    store.set_mark(name, ts(2026, 1, 1, 6))                     # last ran at 06:00, then the site was down
    run_schedules(store, now=ts(2026, 1, 1, 9, 30), started_at=ts(2026, 1, 1, 9, 29))
    assert sorted(r["run_at"] for r in store.list(name=name)) == [ts(*e) for e in expected]


def test_a_new_schedule_doesnt_backfill(schedules):
    store = MemoryBackend()
    name = f"daily_{uuid.uuid4().hex[:6]}"

    @jobs.cron("0 3 * * *", name=name)
    def daily():
        return 1

    run_schedules(store, now=ts(2026, 1, 1, 12), started_at=ts(2026, 1, 1, 11))
    assert store.list(name=name) == []
    run_schedules(store, now=ts(2026, 1, 2, 3, 0, 5), started_at=ts(2026, 1, 1, 11))
    assert [r["run_at"] for r in store.list(name=name)] == [ts(2026, 1, 2, 3)]


def test_bad_catchup():
    with pytest.raises(ValueError):
        jobs.every(minutes=1, catchup="sometimes")


def test_utc_needs_no_time_zone_database(monkeypatch):
    """Windows and Pyodide have no tz database: importing pyweb (which schedules a UTC job) must work."""
    import pyweb.jobs.schedule as S

    def no_database(name):
        raise Exception(f"No time zone found with key {name}")

    monkeypatch.setattr(S, "ZoneInfo", no_database)
    assert Cron("0 3 * * *").tz is UTC and Cron("0 3 * * *", "utc").tz is UTC
    with pytest.raises(CronError, match="tzdata"):
        Cron("0 3 * * *", "Europe/London")
