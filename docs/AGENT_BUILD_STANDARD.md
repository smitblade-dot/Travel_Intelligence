# 03AA-04 agent build standard

FORGEDTITANIUM LTD | 03 Agents & Automation | 03AA-04 Intelligence Automation Agents.
Mandatory acceptance criteria for every agent built in this repository, learned from
03AA-04_Master_Agent_TI_Ops_Director and 03AA-04_Production_Controller_TI. `CLAUDE.md` makes this document
binding; every build brief should reference it (clause at the end).

Marc is not a software specialist. He must never have to reconstruct the result, chase configuration or
ask whether an agent is complete.

## Acceptance criteria

1. **Establish the exact configuration before implementing.** Inspect current `main`, the workflows and the
   live run history (not handovers). Read every accessible setting yourself and label it UNSET, FALSE or
   UNVERIFIED; never infer a live value from an example file. Present one table:
   SETTING | EXACT VARIABLE NAME OR CONFIG PATH | CURRENT VALUE | PROPOSED VALUE | EFFECT | ACTION OWNER.
   Ask Marc for genuinely missing choices once, bundled, with recommended values and their practical effect.
2. **Apply authorised settings wherever access permits.** Once Marc confirms a value, apply it in the same
   session. Repository variables and secrets often cannot be set from a Claude session; non-secret
   operating values can and should then be committed to the agent's configuration file (with the approval
   recorded beside them), with repository variables kept only as overrides. Never put a secret in Git.
3. **Give exact manual steps immediately where access prevents configuration.** One concise list: exact
   names, exact values, and the exact settings location (for example Settings › Secrets and variables ›
   Actions › Variables). Never ask for a secret in chat.
4. **Verify the agent in its intended operating mode.** For a scheduled agent that means at least one
   automatic run (schedule or event trigger, not only a manual run) in the configured mode on `main`,
   checked from its log. A manual run proves the code, not the operation.
5. **Show a real output and a direct link.** Quote the key lines of the latest real run and give the URL of
   that run's report (job summary or committed report file).
6. **Explain plainly** what the agent does, when and where it runs, what it costs and what Marc must do.
7. **State the five stages separately:** built, tested, merged, configured, operational (verified in
   automatic operation). Never let one stand in for another.
8. **Report actual cost:** AI spend at runtime (measured, with source and whether estimated or invoiced),
   build-time AI usage (API versus Claude subscription), and infrastructure (for example GitHub Actions
   minutes). Say "none" explicitly when there is none.
9. **Issue your own concise final completion handover** using the template below, without being asked. Do not
   describe the handover as complete while configuration or verification is outstanding.
10. **Keep scope.** Findings outside the build (other workflows, data defects) are reported and queued as
    separate work, not fixed silently or used to expand the build. Future agents and the NUC stay outside a
    build's assessment unless the brief includes them.

## Completion handover template

```
# Completion handover: <agent name>

What it is: <one plain-English sentence>
What it does: <3-6 plain bullets>
When and where it runs: <platform, trigger, frequency; known scheduling limits>

| Stage | Status |
|---|---|
| Built | |
| Tested | <tests, reviews> |
| Merged | <PR link, commit> |
| Configured | <exact values and where they live; who applied them> |
| Operational | <automatic run verified: run link, time, mode> |

Configuration still required: <none, or exact name = value at exact location>
Real output (latest run, <time>): <key lines>
Report: <direct URL>
Validation: <what was checked, measured>
Cost: runtime AI <...>; build-time AI <...>; infrastructure <...>
Outstanding actions: <action — owner>

COMPLETE - <practical result>
or
NOT COMPLETE - <exact remaining action and owner>
```

## Clause for future build briefs

> Acceptance: this build must meet `docs/AGENT_BUILD_STANDARD.md` in full (configuration established and
> applied, operation verified in its intended mode, real output and report link, plain-English completion
> handover with a COMPLETE / NOT COMPLETE verdict).
