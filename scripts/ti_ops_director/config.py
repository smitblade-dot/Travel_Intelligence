"""Configuration: defaults are safe (dry-run, no paid allowance, Opus disabled)."""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
PRICING_FILE = ROOT / "config" / "ops_director_pricing.json"


def default_pricing():
    """Model-keyed list prices shipped with the repository (verified_at/source_url on every entry)."""
    try:
        return json.loads(PRICING_FILE.read_text())["models"]
    except (OSError, ValueError, KeyError):
        return {}


def default_model_options():
    # Haiku 5.5 rejects temperature 0 (HTTP 400), thinks adaptively by default and bills it inside
    # max_tokens, so classification disables thinking at low effort. Haiku 4.5 keeps its old setting.
    return {"claude-haiku-5-5": {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}},
            "claude-haiku-4-5-20251001": {"temperature": 0}}


class ConfigError(ValueError):
    """A configuration value that cannot be interpreted. Callers fail closed: no paid call is made."""


def _num(value, default=None, name="value"):
    if value in (None, ""):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a decimal USD amount, got an invalid value") from None
    if not math.isfinite(number):
        raise ConfigError(f"{name} must be a finite decimal USD amount")
    return number


def parse_dry_run(raw):
    """(dry_run, status). Only an explicit false/0/no turns dry-run off; anything unrecognised stays dry-run."""
    value = (raw or "").strip().lower()
    if not value:
        return True, "unset"
    if value in ("0", "false", "no"):
        return False, "false"
    if value in ("1", "true", "yes"):
        return True, "true"
    return True, "invalid"


@dataclass
class Config:
    db_path: str = str(ROOT / "var" / "ops_director.sqlite3")
    dry_run: bool = True
    max_concurrency: int = 2
    lease_seconds: int = 300
    max_attempts: int = 3
    recheck_after_hours: float = 6.0
    monitor_interval_minutes: int = 15
    # Production controller as a queue responsibility (local or NUC daemon). None = not scheduled by the queue;
    # on GitHub Actions the controller has its own workflow (docs/TI_PRODUCTION_CONTROLLER.md).
    production_check_interval_minutes: Optional[int] = None
    # Country builder (03AA-04_Country_Intelligence_Builder_TI) gap enqueueing. None = not scheduled by the queue.
    country_builder_interval_minutes: Optional[int] = None
    # Investigation and verification (03AA-04_Investigation_Verification_TI) pass as a queue responsibility. None = not scheduled by the queue;
    # on GitHub Actions it has its own workflow (docs/TI_INVESTIGATION_VERIFICATION.md).
    investigation_interval_minutes: Optional[int] = None
    daily_consolidation_local: str = "21:00"
    timezone: str = "Europe/Nicosia"
    # Spending limits in provider currency (USD). None means NOT configured: paid work blocked.
    per_task_limit_usd: Optional[float] = None
    daily_limit_usd: Optional[float] = None
    monthly_limit_usd: Optional[float] = None
    # Optional all-time cap for a bounded, separately approved activity (the quality evaluation). None = unused.
    total_limit_usd: Optional[float] = None
    max_model_calls_per_task: int = 2
    max_output_tokens: int = 800
    model_timeout_seconds: int = 30
    models: dict = field(default_factory=lambda: {
        "haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"})
    # Pricing per million tokens, USD, keyed by MODEL ID. Entries need verified_at and source_url or paid work is blocked.
    pricing: dict = field(default_factory=default_pricing)
    model_options: dict = field(default_factory=default_model_options)
    max_input_tokens_per_call: int = 60000   # keeps every call under Haiku 5.5's 100k-token pricing tier
    # Opus stays off unless Marc approves a defined task type and allowance.
    opus_approval: Optional[dict] = None
    publication_enabled: bool = False
    feed_http_timeout_seconds: int = 15
    feed_http_attempts: int = 2
    feed_max_bytes: int = 2_000_000
    freshness_max_hours: float = 36.0
    repo_root: str = str(ROOT)
    config_warnings: list = field(default_factory=list)

    @property
    def paid_allowance_configured(self) -> bool:
        return all(v is not None and v > 0 for v in (
            self.per_task_limit_usd, self.daily_limit_usd, self.monthly_limit_usd))


def load_config(path: Optional[str] = None, env=None) -> Config:
    env = os.environ if env is None else env
    cfg = Config()
    path = path or env.get("TI_OPS_CONFIG")
    if path and Path(path).exists():
        raw = json.loads(Path(path).read_text())
        for key, value in raw.items():
            if key.startswith("_"):
                continue
            if not hasattr(cfg, key):
                raise ValueError(f"Unknown configuration key: {key}")
            setattr(cfg, key, value)
    if env.get("TI_OPS_DB_PATH"):
        cfg.db_path = env["TI_OPS_DB_PATH"]
    if env.get("TI_OPS_REPO_ROOT"):
        cfg.repo_root = env["TI_OPS_REPO_ROOT"]
    if env.get("TI_OPS_MAX_MODEL_CALLS_PER_TASK"):
        try:
            cfg.max_model_calls_per_task = int(env["TI_OPS_MAX_MODEL_CALLS_PER_TASK"])
        except ValueError:
            raise ConfigError("TI_OPS_MAX_MODEL_CALLS_PER_TASK must be a whole number") from None
    if (env.get("TI_OPS_DRY_RUN") or "").strip():
        cfg.dry_run, status = parse_dry_run(env["TI_OPS_DRY_RUN"])
        if status == "invalid":
            cfg.config_warnings.append("TI_OPS_DRY_RUN has an unrecognised value; dry-run kept on")
    for attr, name in (("per_task_limit_usd", "TI_OPS_PER_TASK_LIMIT_USD"),
                       ("daily_limit_usd", "TI_OPS_DAILY_LIMIT_USD"),
                       ("monthly_limit_usd", "TI_OPS_MONTHLY_LIMIT_USD"),
                       ("total_limit_usd", "TI_OPS_TOTAL_LIMIT_USD")):
        if env.get(name):
            setattr(cfg, attr, _num(env[name], name=name))
    if env.get("TI_HAIKU_MODEL"):             # the existing monitor's variable keeps working
        cfg.models["haiku"] = env["TI_HAIKU_MODEL"]
    for tier, name in (("haiku", "TI_OPS_MODEL_HAIKU"), ("sonnet", "TI_OPS_MODEL_SONNET"),
                       ("opus", "TI_OPS_MODEL_OPUS")):
        if env.get(name):
            cfg.models[tier] = env[name]
    cfg.db_path = str(cfg.db_path)
    return cfg
