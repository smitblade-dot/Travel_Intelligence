"""Schedules. Local operational times use Europe/Nicosia; everything stored is UTC."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def local_to_utc(date_local, hhmm, tz_name):
    """Resolve a local wall time to UTC. Nonexistent times move forward; ambiguous times use the first occurrence."""
    tz = ZoneInfo(tz_name)
    h, m = (int(x) for x in hhmm.split(":"))
    naive = datetime(date_local.year, date_local.month, date_local.day, h, m)
    local = naive.replace(tzinfo=tz, fold=0)
    utc = local.astimezone(timezone.utc)
    if utc.astimezone(tz).replace(tzinfo=None) != naive:   # gap: wall time does not exist
        utc = (naive + timedelta(hours=1)).replace(tzinfo=tz, fold=0).astimezone(timezone.utc)
    return utc


def next_daily_run(now_utc, hhmm, tz_name):
    tz = ZoneInfo(tz_name)
    today = now_utc.astimezone(tz).date()
    for offset in (0, 1):
        cand = local_to_utc(today + timedelta(days=offset), hhmm, tz_name)
        if cand > now_utc:
            return cand
    raise AssertionError("unreachable")


def last_daily_slot(now_utc, hhmm, tz_name):
    """Most recent scheduled slot at or before now (UTC)."""
    tz = ZoneInfo(tz_name)
    today = now_utc.astimezone(tz).date()
    for offset in (0, -1):
        cand = local_to_utc(today + timedelta(days=offset), hhmm, tz_name)
        if cand <= now_utc:
            return cand
    raise AssertionError("unreachable")


def interval_slot(now_utc, minutes):
    """Slot start for a fixed interval aligned to the UTC epoch."""
    sec = int(now_utc.timestamp()) // (minutes * 60) * (minutes * 60)
    return datetime.fromtimestamp(sec, tz=timezone.utc)


def due_slots(last_done_slot, now_utc, slot_fn):
    """Missed-run policy: coalesce. At most one run is due, naming how many slots were missed."""
    cur = slot_fn(now_utc)
    if last_done_slot is None:
        return cur, 0
    if cur <= last_done_slot:
        return None, 0
    return cur, None  # missed count is computed by the caller from slot spacing when needed


def display(dt_utc, tz_name):
    return dt_utc.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M %Z") + f" ({tz_name})"
