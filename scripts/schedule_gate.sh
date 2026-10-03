#!/usr/bin/env bash
set -euo pipefail

if [[ "${EVENT_NAME:-}" != "schedule" ]]; then
  echo "run_refresh=true" >> "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"
  echo "Manual workflow run; proceeding without the schedule gate."
  exit 0
fi

candidate_utc_hour=$(awk '{print $2}' <<< "${EVENT_SCHEDULE:-}")
if [[ ! "$candidate_utc_hour" =~ ^[0-9]{1,2}$ ]]; then
  echo "Invalid scheduled event cron expression: ${EVENT_SCHEDULE:-<empty>}" >&2
  exit 1
fi
utc_date=${TI_UTC_DATE:-$(date -u +%F)}
expected_utc_hour=$(python3 - "$utc_date" <<'PY'
from datetime import date, datetime, time, timezone
import sys
from zoneinfo import ZoneInfo

day = date.fromisoformat(sys.argv[1])
noon_utc = datetime.combine(day, time(12), tzinfo=timezone.utc)
offset = noon_utc.astimezone(ZoneInfo("Europe/Nicosia")).utcoffset()
print((21 - int(offset.total_seconds() // 3600)) % 24)
PY
)

if ((10#$candidate_utc_hour != expected_utc_hour)); then
  echo "run_refresh=false" >> "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"
  echo "Skipping UTC candidate ${candidate_utc_hour}; Cyprus 21:00 maps to UTC ${expected_utc_hour} today."
  exit 0
fi

echo "run_refresh=true" >> "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"
echo "Running TI daily refresh at 21:00 Europe/Nicosia."
