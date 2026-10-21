"""Configuration: defaults are safe (dry-run, no paid allowance, Opus disabled)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]


def _num(value, default=None):
    if value in (None, ""):
        return default
    return float(value)


@dataclass
class Config:
    db_path: str = str(ROOT / "var" / "ops_director.sqlite3")
    dry_run: bool = True
    max_concurrency: int = 2
    lease_seconds: int = 300
    max_attempts: int = 3
    recheck_after_hours: float = 6.0
    monitor_interval_minutes: int = 15
    daily_consolidation_local: str = "21:00"
    timezone: str = "Europe/Nicosia"
    # Spending limits in provider currency (USD). None means NOT configured: paid work blocked.
    per_task_limit_usd: Optional[float] = None
    daily_limit_usd: Optional[float] = None
    monthly_limit_usd: Optional[float] = None
    max_model_calls_per_task: int = 2
    max_output_tokens: int = 800
    models: dict = field(default_factory=lambda: {
        "haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"})
    # Pricing per million tokens, USD. Entries need verified_at and source_url or paid work is blocked.
    pricing: dict = field(default_factory=dict)
    # Opus stays off unless Marc approves a defined task type and allowance.
    opus_approval: Optional[dict] = None
    publication_enabled: bool = False
    feed_http_timeout_seconds: int = 15
    feed_http_attempts: int = 2
    feed_max_bytes: int = 2_000_000
    freshness_max_hours: float = 36.0
    repo_root: str = str(ROOT)

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
    if env.get("TI_OPS_DRY_RUN") is not None:
        cfg.dry_run = env["TI_OPS_DRY_RUN"].strip().lower() not in ("0", "false", "no")
    for attr, name in (("per_task_limit_usd", "TI_OPS_PER_TASK_LIMIT_USD"),
                       ("daily_limit_usd", "TI_OPS_DAILY_LIMIT_USD"),
                       ("monthly_limit_usd", "TI_OPS_MONTHLY_LIMIT_USD")):
        if env.get(name):
            setattr(cfg, attr, _num(env[name]))
    for tier, name in (("haiku", "TI_OPS_MODEL_HAIKU"), ("sonnet", "TI_OPS_MODEL_SONNET"),
                       ("opus", "TI_OPS_MODEL_OPUS")):
        if env.get(name):
            cfg.models[tier] = env[name]
    cfg.db_path = str(cfg.db_path)
    return cfg
