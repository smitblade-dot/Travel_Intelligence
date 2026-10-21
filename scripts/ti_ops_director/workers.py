"""Worker interface, capability restriction and registry (including explicit unimplemented workers)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlsplit


class CapabilityError(Exception):
    pass


@dataclass
class WorkResult:
    state: str                       # completed | blocked | failed
    outputs: Optional[dict] = None
    evidence: list = field(default_factory=list)
    blocking_reason: Optional[str] = None
    requires_human: bool = False
    error: Optional[str] = None


class Worker:
    name = "base"
    task_types: tuple = ()
    capabilities: frozenset = frozenset()
    implemented = True
    description = ""

    def run(self, task: dict, ctx: "WorkContext") -> WorkResult:  # pragma: no cover
        raise NotImplementedError


class PlannedWorker(Worker):
    """A functional role that is not built yet. Tasks for it are blocked explicitly, never faked."""
    implemented = False

    def __init__(self, name, task_types, description):
        self.name, self.task_types, self.description = name, tuple(task_types), description


@dataclass
class WorkContext:
    cfg: object
    store: object
    gateway: object
    worker: Worker
    task_id: str
    owner: str
    http: Optional[Callable] = None
    allowed_hosts: frozenset = frozenset()

    def should_stop(self) -> bool:
        return self.store.get_control("stop", "0") == "1"

    def checkpoint(self, data):
        self.store.checkpoint(self.task_id, self.owner, data, self.cfg.lease_seconds)

    def http_get(self, url, **kw):
        if "network_read" not in self.worker.capabilities:
            raise CapabilityError(f"{self.worker.name} lacks network_read")
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in self.allowed_hosts:
            raise CapabilityError("host not in this task's allowed hosts")
        if self.http is None:
            raise CapabilityError("no http transport configured")
        return self.http(url, **kw)


PLANNED = [
    ("discovery", ("discovery",), "Discovery. Python collectors exist in this repository but are not wired to the queue."),
    ("triage", ("triage",), "Triage (Haiku). Existing monitor script is not yet a queue worker."),
    ("investigation_content", ("investigation", "content_building"), "Investigation and content building"),
    ("verification", ("verification",), "Verification"),
    ("publication", ("publication",), "Publication (automatic publication disabled)"),
    ("production_recovery", ("production_diagnostic", "production_recovery"),
     "Production exceptions found by the production controller. Operator action; the only automatic recovery is "
     "a bounded workflow dispatch on GitHub Actions, off unless Marc enables it."),
    ("daily_consolidation", ("daily_consolidation",),
     "Daily consolidation. The existing GitHub Actions 21:00 Europe/Nicosia refresh remains the consolidator."),
]


class Registry:
    def __init__(self):
        self._workers = {}
        self._by_type = {}

    def register(self, worker: Worker):
        if worker.name in self._workers:
            raise ValueError("duplicate worker " + worker.name)
        self._workers[worker.name] = worker
        for t in worker.task_types:
            self._by_type[t] = worker.name

    def get(self, name):
        return self._workers.get(name)

    def for_type(self, task_type):
        n = self._by_type.get(task_type)
        return self._workers.get(n) if n else None

    def all(self):
        return list(self._workers.values())

    def unimplemented(self):
        return [w for w in self._workers.values() if not w.implemented]


def default_registry():
    from .feed_health import FeedHealthWorker
    from .production_worker import ProductionVerificationWorker
    reg = Registry()
    reg.register(FeedHealthWorker())
    reg.register(ProductionVerificationWorker())
    for name, types, desc in PLANNED:
        reg.register(PlannedWorker(name, types, desc))
    return reg
