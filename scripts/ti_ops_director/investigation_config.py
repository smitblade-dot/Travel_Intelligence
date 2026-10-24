"""Configuration for 03AA-04_Investigation_Verification_TI.

Committed values live in config/investigation_verification.json; TI_IV_* variables override the ones listed in
``OVERRIDES``. Spending limits are this agent's own: the Ops Director's TI_OPS_* limits, the Country Builder's TI_CB_*
limits and the earlier evaluation allowance are deliberately not read.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT, Config, ConfigError, _num, parse_dry_run

AGENT_NAME = "03AA-04_Investigation_Verification_TI"
WORKER_NAME = "investigation_verification"
MODES = ("off", "observe", "draft", "publish")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
ALERT_LEVELS = {"Green": 1, "Orange": 2, "Red": 3}
OVERRIDES = ("TI_IV_MODE", "TI_IV_PUBLISH", "TI_IV_STATE_BRANCH", "TI_IV_COUNTRIES", "TI_IV_EVENT_TYPES", "TI_IV_BATCH_SIZE",
             "TI_IV_DRY_RUN", "TI_IV_PER_TASK_LIMIT_USD", "TI_IV_DAILY_LIMIT_USD", "TI_IV_MONTHLY_LIMIT_USD", "TI_IV_TOTAL_LIMIT_USD",
             "TI_IV_TRIAL", "TI_IV_TARGET_BRANCH", "TI_IV_SITE_URL", "TI_IV_FIXTURE")


@dataclass
class IVConfig:
    raw: dict
    mode: str = "off"
    mode_source: str = "unset"
    state_branch: str = ""
    countries: list = field(default_factory=list)
    event_types: list = field(default_factory=list)
    tasks_per_run: int = 1
    dry_run: bool = True
    dry_run_source: str = "committed"
    limits: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    publication_setting: str = "unset"
    trial: bool = False
    target_branch: str = "main"
    site_url_override: str = ""
    fixture: str = ""

    @property
    def scope(self):
        return self.raw["scope"]

    @property
    def fresh(self):
        return self.raw["freshness"]

    @property
    def evidence(self):
        return self.raw["evidence_limits"]

    @property
    def retries(self):
        return self.raw["retries"]

    @property
    def sources(self):
        return self.raw["sources"]

    @property
    def publication_cfg(self):
        return self.raw["publication"]

    @property
    def min_alert(self):
        return ALERT_LEVELS[self.scope["min_alert_level"]]

    @property
    def publication_enabled(self):
        """Fails closed: mode publish, publication.enabled true, and TI_IV_PUBLISH not 'false'."""
        return self.mode == "publish" and bool(self.publication_cfg.get("enabled")) and self.publication_setting != "false"

    @property
    def paid_allowance_configured(self):
        return all(self.limits.get(k) is not None and self.limits[k] > 0
                   for k in ("per_task_limit_usd", "daily_limit_usd", "monthly_limit_usd"))

    def gateway_config(self):
        """A Config for the existing ModelGateway built from this agent's settings only."""
        spend = self.raw["spending"]
        cfg = Config()
        cfg.dry_run = self.dry_run
        cfg.per_task_limit_usd = self.limits.get("per_task_limit_usd")
        cfg.daily_limit_usd = self.limits.get("daily_limit_usd")
        cfg.monthly_limit_usd = self.limits.get("monthly_limit_usd")
        cfg.total_limit_usd = self.limits.get("total_limit_usd")
        cfg.max_output_tokens = int(spend["max_output_tokens"])
        cfg.model_timeout_seconds = int(spend["model_timeout_seconds"])
        cfg.max_model_calls_per_task = 3 if spend.get("sonnet_escalation_enabled") else 2
        cfg.opus_approval = None                 # Opus stays disabled for runtime tasks
        return cfg


def load_config(env=None, root=ROOT, path=None) -> IVConfig:
    import os
    env = os.environ if env is None else env
    p = Path(path) if path else Path(root) / "config" / "investigation_verification.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    cfg = IVConfig(raw=raw)
    err = cfg.errors
    op = raw.get("operation", {})
    mode, src = (op.get("mode") or "").strip().lower(), "config/investigation_verification.json"
    var = (env.get("TI_IV_MODE") or "").strip().lower()
    if var:
        mode, src = var, "TI_IV_MODE"
    if not mode:
        mode, src = "off", "unset"
    if mode not in MODES:
        err.append(f"mode must be one of {', '.join(MODES)} (got {mode!r} from {src})")
        mode = "off"
    cfg.mode, cfg.mode_source = mode, src
    cfg.trial = (env.get("TI_IV_TRIAL") or "").strip().lower() == "true"
    cfg.fixture = (env.get("TI_IV_FIXTURE") or "").strip()
    cfg.state_branch = (env.get("TI_IV_STATE_BRANCH") or op.get("state_branch") or "").strip()
    if cfg.state_branch and (not BRANCH.match(cfg.state_branch) or cfg.state_branch in ("main", "master")):
        err.append("state branch is invalid or is the production branch (main is refused)")
    pub = raw["publication"]
    cfg.target_branch = pub["target_branch"]
    cfg.site_url_override = (env.get("TI_IV_SITE_URL") or "").strip()
    if cfg.trial:
        # A pull-request trial must never share production state or write the production branch.
        tb = (env.get("TI_IV_TARGET_BRANCH") or "").strip()
        if not tb or tb in ("main", "master") or not BRANCH.match(tb):
            err.append("a trial needs TI_IV_TARGET_BRANCH set to a non-production branch")
        else:
            cfg.target_branch = tb
        if not cfg.site_url_override.startswith("https://"):
            err.append("a trial needs TI_IV_SITE_URL (https) for the isolated target")
        if cfg.state_branch == op.get("state_branch"):
            err.append("a trial must use its own state branch, not the production ledger")
    elif (env.get("TI_IV_TARGET_BRANCH") or cfg.site_url_override):
        err.append("TI_IV_TARGET_BRANCH / TI_IV_SITE_URL are accepted only for a trial (TI_IV_TRIAL=true)")
    scope = raw["scope"]
    cfg.countries = [c.strip().lower() for c in (env.get("TI_IV_COUNTRIES") or ",".join(scope["countries"])).split(",") if c.strip()]
    cfg.event_types = [c.strip().upper() for c in (env.get("TI_IV_EVENT_TYPES") or ",".join(scope["event_types"])).split(",") if c.strip()]
    for t in cfg.event_types:
        if t not in raw["event_types"]:
            err.append(f"unknown event type {t}")
    if scope.get("min_alert_level") not in ALERT_LEVELS:
        err.append("scope.min_alert_level must be Green, Orange or Red")
    try:
        cfg.tasks_per_run = max(1, min(int((env.get("TI_IV_BATCH_SIZE") or "").strip() or scope["batch_size_tasks_per_run"]), 10))
    except (TypeError, ValueError):
        err.append("batch size must be a whole number")
    spend = raw["spending"]
    if (env.get("TI_IV_DRY_RUN") or "").strip():
        cfg.dry_run, status = parse_dry_run(env["TI_IV_DRY_RUN"])
        cfg.dry_run_source = "TI_IV_DRY_RUN"
    else:
        cfg.dry_run = bool(spend.get("dry_run", True))
    for key, var_name in (("per_task_limit_usd", "TI_IV_PER_TASK_LIMIT_USD"), ("daily_limit_usd", "TI_IV_DAILY_LIMIT_USD"),
                          ("monthly_limit_usd", "TI_IV_MONTHLY_LIMIT_USD"), ("total_limit_usd", "TI_IV_TOTAL_LIMIT_USD")):
        try:
            cfg.limits[key] = _num(env.get(var_name), spend.get(key), var_name)
        except ConfigError as exc:
            err.append(str(exc))
            cfg.limits[key] = None
    cfg.publication_setting = (env.get("TI_IV_PUBLISH") or "").strip().lower() or "unset"
    if cfg.mode == "publish" and not pub.get("enabled"):
        err.append("mode publish needs publication.enabled true")
    if pub.get("enabled") and (pub.get("required_ref") != "refs/heads/main"):
        err.append("publication must be allowed from refs/heads/main only")
    for name, s in raw["sources"].items():
        if not s.get("registry_id") or not s.get("hosts"):
            err.append(f"source {name} needs registry_id and hosts")
    return cfg
