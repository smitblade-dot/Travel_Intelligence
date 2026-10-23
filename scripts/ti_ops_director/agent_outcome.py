"""Optional run-outcome record for the FORGEDTITANIUM Agent Command Centre (ACC).

Inert by default: when TI_AGENT_OUTCOME_FILE is unset or empty, ``write`` does nothing. When set, the agent
writes one small JSON record describing its own run (agent code, exit code, mode and the facts it already
computed) so a separate, optional workflow step can report it to the ACC. Writing never raises and never
changes an agent's output, state or exit code. No network access here.
"""
import json
import os
from datetime import datetime, timezone

SCHEMA = "ti-agent-outcome/1"


def write(env, agent_code, record):
    """Write the outcome record if TI_AGENT_OUTCOME_FILE is set. Returns True when written, else False."""
    try:
        path = (env or {}).get("TI_AGENT_OUTCOME_FILE") or ""
        if not path:
            return False
        doc = {"schema": SCHEMA, "agent": agent_code,
               "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
        doc.update(record or {})
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, default=str, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:   # reporting is optional and must never affect the agent
        return False
