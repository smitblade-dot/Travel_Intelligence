"""Daily refresh slot: 21:00 Europe/Nicosia, triggered by two UTC crons and one deterministic gate.

GitHub's ``timezone:`` schedule key was not observed to fire at the local time (20 and 21 October 2026 runs
started 21:21-21:22 UTC for an 18:00 UTC slot), so the workflow schedules both candidate UTC hours: 18:00 UTC
(21:00 EEST, summer) and 19:00 UTC (21:00 EET, winter). Exactly one of them is 21:00 Cyprus time on any date.

The gate decides from the cron entry that fired, not from how late the runner started: it resolves the
most recent instance of that cron at or before the gate's clock and asks whether that instance was 21:00 in
Cyprus. Any queue delay shorter than 24 hours therefore resolves to the same instance and the same decision.

Stdlib only. The workflow runs this file directly (``python3 scripts/ti_ops_director/refresh_slot.py``);
the production controller imports it to tell an intentional off-slot skip from a real gate failure.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TIMEZONE = "Europe/Nicosia"
LOCAL_TIME = "21:00"
CRONS = ("0 18 * * *", "0 19 * * *")
_DAILY_CRON = re.compile(r"^(\d{1,2}) (\d{1,2}) \* \* \*$")


def cron_instance(cron, at_utc):
    """Most recent UTC instance of a daily ``M H * * *`` cron at or before ``at_utc``."""
    m = _DAILY_CRON.match(cron or "")
    if not m:
        raise ValueError(f"not a daily cron: {cron!r}")
    minute, hour = int(m.group(1)), int(m.group(2))
    at_utc = at_utc.astimezone(timezone.utc)
    inst = at_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return inst if inst <= at_utc else inst - timedelta(days=1)


def is_refresh_slot(cron, at_utc):
    """True when the instance of ``cron`` that ``at_utc`` belongs to is 21:00 Cyprus time."""
    if cron not in CRONS:
        return False
    local = cron_instance(cron, at_utc).astimezone(ZoneInfo(TIMEZONE))
    return local.strftime("%H:%M") == LOCAL_TIME


def decide(event_name, cron, now_utc):
    """Gate decision for one workflow run: (should_run, explanation)."""
    if event_name == "workflow_dispatch":
        return True, "Refresh allowed: manual dispatch."
    if event_name != "schedule" or cron not in CRONS:
        return False, f"Unexpected trigger: {event_name} / {cron}."
    inst = cron_instance(cron, now_utc)
    local = inst.astimezone(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M %Z")
    if is_refresh_slot(cron, now_utc):
        return True, f"Refresh allowed: '{cron}' slot {inst:%Y-%m-%dT%H:%MZ} is {local} ({TIMEZONE})."
    return False, (f"Off-slot trigger skipped: '{cron}' slot {inst:%Y-%m-%dT%H:%MZ} is {local}; "
                   f"the other UTC cron is today's {LOCAL_TIME} {TIMEZONE} slot.")


def main(env=None):
    env = os.environ if env is None else env
    now = datetime.fromtimestamp(int(env["GATE_NOW_EPOCH"]), tz=timezone.utc)
    ok, why = decide(env.get("EVENT_NAME", ""), env.get("SCHEDULED_CRON", ""), now)
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"should_run={'true' if ok else 'false'}\n")
    print(why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
