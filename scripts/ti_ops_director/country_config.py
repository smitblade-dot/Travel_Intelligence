"""Configuration for 03AA-04_Country_Intelligence_Builder_TI.

Committed values live in config/country_builder.json; TI_CB_* variables override them. Spending limits are the
builder's own: the Ops Director's TI_OPS_* limits and the evaluation allowance are deliberately not read.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT, Config, ConfigError, _num, parse_dry_run

CONFIG_PATH = ROOT / "config" / "country_builder.json"
AGENT_NAME = "03AA-04_Country_Intelligence_Builder_TI"
MODES = ("off", "observe", "draft", "publish")
CATEGORIES = ("SECURITY", "ENTRY", "HEALTH", "EMERGENCY", "TRANSPORT", "ENVIRONMENT", "INSURANCE", "LAWS_CULTURE",
              "COMMUNICATIONS", "FINANCE", "LANGUAGE", "ACCOMMODATION", "EQUIPMENT", "TRAINING")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
TOPIC_KINDS = ("model", "deterministic", "reference", "outlook")


@dataclass
class BuilderConfig:
    raw: dict
    mode: str = "off"
    mode_source: str = "unset"
    state_branch: str = ""
    countries: list = field(default_factory=list)
    categories: list = field(default_factory=list)
    tasks_per_run: int = 1
    dry_run: bool = True
    dry_run_source: str = "committed"
    limits: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    publication_setting: str = "unset"

    @property
    def publication_cfg(self):
        return self.raw.get("publication", {})

    @property
    def publication_enabled(self):
        """Fails closed: mode publish, publication.enabled true, and TI_CB_PUBLISH not 'false'."""
        return self.mode == "publish" and bool(self.publication_cfg.get("enabled")) and self.publication_setting != "false"

    @property
    def sources(self):
        return self.raw["sources"]

    @property
    def category_defs(self):
        return self.raw["categories"]

    @property
    def limits_cfg(self):
        return self.raw["evidence_limits"]

    @property
    def paid_allowance_configured(self):
        return all(self.limits.get(k) is not None and self.limits[k] > 0
                   for k in ("per_task_limit_usd", "daily_limit_usd", "monthly_limit_usd"))

    def gateway_config(self):
        """A Config for the existing ModelGateway. Built from this agent's settings only."""
        spend = self.raw["spending"]
        cfg = Config()
        cfg.dry_run = self.dry_run
        cfg.per_task_limit_usd = self.limits.get("per_task_limit_usd")
        cfg.daily_limit_usd = self.limits.get("daily_limit_usd")
        cfg.monthly_limit_usd = self.limits.get("monthly_limit_usd")
        cfg.total_limit_usd = self.limits.get("total_limit_usd")
        cfg.max_output_tokens = int(spend["max_output_tokens"])
        cfg.model_timeout_seconds = int(spend["model_timeout_seconds"])
        # Calls per task: first answer, one corrective retry, one independent verification, and one Sonnet call only if
        # escalation is enabled.
        cfg.max_model_calls_per_task = 3 if spend.get("sonnet_escalation_enabled") else 2
        cfg.opus_approval = None            # Opus stays disabled
        return cfg


def load_builder_config(env=None, root=ROOT, path=None) -> BuilderConfig:
    import os
    env = os.environ if env is None else env
    p = Path(path) if path else Path(root) / "config" / "country_builder.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    cfg = BuilderConfig(raw=raw)
    err = cfg.errors
    op = raw.get("operation", {})
    mode, src = (op.get("mode") or "").strip().lower(), "config/country_builder.json"
    var = (env.get("TI_CB_MODE") or "").strip().lower()
    if var:
        mode, src = var, "TI_CB_MODE"
    if not mode:
        mode, src = "off", "unset"
    if mode not in MODES:
        err.append(f"mode must be one of {', '.join(MODES)} (got {mode!r} from {src})")
        mode = "off"
    cfg.mode, cfg.mode_source = mode, src
    cfg.state_branch = (env.get("TI_CB_STATE_BRANCH") or op.get("state_branch") or "").strip()
    if cfg.state_branch and (not BRANCH.match(cfg.state_branch) or cfg.state_branch in ("main", "master")):
        err.append("state branch is invalid or is the production branch (main is refused)")
    scope = raw.get("scope", {})
    cfg.countries = [c.strip().lower() for c in (env.get("TI_CB_COUNTRIES") or ",".join(scope.get("countries", []))).split(",") if c.strip()]
    cfg.categories = [c.strip().upper() for c in (env.get("TI_CB_CATEGORIES") or ",".join(scope.get("categories", []))).split(",") if c.strip()]
    for c in cfg.categories:
        if c not in CATEGORIES:
            err.append(f"unknown category {c}")
        elif c not in raw["categories"]:
            err.append(f"category {c} has no source/topic definition yet")
    for cc in cfg.countries:
        if cc not in raw.get("country_params", {}):
            err.append(f"country {cc} has no country_params (source identifiers, location)")
    try:
        cfg.tasks_per_run = max(1, min(int((env.get("TI_CB_BATCH_SIZE") or "").strip() or scope.get("batch_size_tasks_per_run", 1)), 5))
    except (TypeError, ValueError):
        err.append("batch size (batch_size_tasks_per_run / TI_CB_BATCH_SIZE) must be a whole number")
    spend = raw.get("spending", {})
    dry, dstatus = parse_dry_run(env.get("TI_CB_DRY_RUN")) if (env.get("TI_CB_DRY_RUN") or "").strip() else (bool(spend.get("dry_run", True)), "committed")
    cfg.dry_run, cfg.dry_run_source = dry, ("TI_CB_DRY_RUN" if dstatus != "committed" else "config/country_builder.json")
    for key, var_name in (("per_task_limit_usd", "TI_CB_PER_TASK_LIMIT_USD"), ("daily_limit_usd", "TI_CB_DAILY_LIMIT_USD"),
                          ("monthly_limit_usd", "TI_CB_MONTHLY_LIMIT_USD"), ("total_limit_usd", "TI_CB_TOTAL_LIMIT_USD")):
        try:
            cfg.limits[key] = _num(env.get(var_name), spend.get(key), var_name)
        except ConfigError as exc:
            err.append(str(exc))
            cfg.limits[key] = None
    pub = raw.get("publication", {})
    cfg.publication_setting = (env.get("TI_CB_PUBLISH") or "").strip().lower() or "unset"
    if cfg.mode == "publish" and not pub.get("enabled"):
        err.append("mode publish needs publication.enabled true")
    if pub.get("enabled"):
        for k in ("registry", "quality", "verification", "update_policy", "max_records_per_run", "duplicate_similarity"):
            if k not in pub:
                err.append(f"publication.{k} is required when publication is enabled")
        if pub.get("target_branch") != "main" or pub.get("required_ref") != "refs/heads/main":
            err.append("publication must target main from refs/heads/main only")
    for cat, cdef in raw["categories"].items():
        for tname, t in cdef["topics"].items():
            if t["kind"] not in TOPIC_KINDS:
                err.append(f"{cat}.{tname}: unknown topic kind {t['kind']}")
            for sid in t.get("sources", []):
                if sid not in raw["sources"]:
                    err.append(f"{cat}.{tname}: unknown source {sid}")
    return cfg
