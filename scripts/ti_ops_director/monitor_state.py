"""Persistent state for the Actions monitor: spend reservations, cumulative budgets, the discovery cursor
and the unchanged-input cache.

GitHub Actions runners have no persistent disk, so the state lives in the repository itself
(``data/continuous_monitor_state.json`` on ``main``). Every change is a compare-and-swap: read the file at
the current tip of ``main``, apply the change, and push a commit whose parent is that tip. A push from a
run that read an older tip is rejected by GitHub as non-fast-forward, so that run re-reads and re-applies.
This is what keeps daily and monthly limits effective across separate runs, re-runs of a failed job, and
two runs executing at the same time: a reservation is only valid once it is on ``main``.

``FileStateBackend`` is the same contract on one host (a lock file), for local runs and tests.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
import tempfile
from pathlib import Path


class StateSyncError(RuntimeError):
    """The shared state could not be read or written. Paid work must not proceed."""


def dump_state(state):
    return json.dumps(state, indent=2) + "\n"


def dump_data(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"


class Snapshot:
    """Read-only view of the files at one revision. ``text(key)`` returns None when the file does not exist."""
    def __init__(self, reader):
        self._reader, self._cache = reader, {}

    def text(self, key):
        if key not in self._cache:
            self._cache[key] = self._reader(key)
        return self._cache[key]

    def state(self):
        raw = self.text("state")
        return json.loads(raw) if raw else {}

    def data(self):
        raw = self.text("data")
        return json.loads(raw) if raw else None


class FileStateBackend:
    """Local files guarded by an exclusive lock. Correct for several processes on ONE host only."""
    name = "file"

    def __init__(self, paths):
        self.paths = {k: Path(v) for k, v in paths.items()}

    def _read(self, key):
        p = self.paths[key]
        return p.read_text(encoding="utf-8") if p.exists() else None

    @contextlib.contextmanager
    def _lock(self):
        lock = self.paths["state"].with_name(self.paths["state"].name + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def snapshot(self):
        return Snapshot(self._read)

    def transact(self, fn, message=""):
        """fn(snapshot) -> {key: new_text} (or None for no change). Returns (writes, result)."""
        with self._lock():
            writes, result = fn(Snapshot(self._read))
            for key, text in (writes or {}).items():
                p = self.paths[key]
                p.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                os.replace(tmp, p)
            return writes, result


class GitStateBackend:
    """Compare-and-swap commits to one branch. Never touches the index or working tree of the checkout,
    except to mirror what was pushed (so later workflow steps see the same files)."""
    name = "git"

    def __init__(self, repo, paths, branch="main", remote="origin", attempts=5, run=subprocess.run,
                 mirror_worktree=True):
        self.repo, self.paths, self.branch, self.remote = Path(repo), dict(paths), branch, remote
        self.attempts, self._run, self.mirror = attempts, run, mirror_worktree
        self.pushes = 0

    def git(self, *args, input_text=None, env=None, check=True):
        full_env = dict(os.environ, **(env or {}))
        p = self._run(["git", "-C", str(self.repo), *args], input=input_text, capture_output=True,
                      text=True, env=full_env)
        if check and p.returncode != 0:
            raise StateSyncError(f"git {args[0]} failed: {(p.stderr or p.stdout).strip()[:300]}")
        return p

    def _fetch(self):
        self.git("fetch", "--quiet", "--depth=1", self.remote, self.branch)
        return self.git("rev-parse", "FETCH_HEAD").stdout.strip()

    def _reader(self, tip):
        def read(key):
            p = self.git("show", f"{tip}:{self.paths[key]}", check=False)
            return p.stdout if p.returncode == 0 else None
        return read

    def snapshot(self):
        return Snapshot(self._reader(self._fetch()))

    def _commit(self, tip, writes, message):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"),
                   "GIT_AUTHOR_NAME": "Travel Intelligence Bot", "GIT_COMMITTER_NAME": "Travel Intelligence Bot",
                   "GIT_AUTHOR_EMAIL": "actions@users.noreply.github.com",
                   "GIT_COMMITTER_EMAIL": "actions@users.noreply.github.com"}
            self.git("read-tree", tip, env=env)
            for key, text in writes.items():
                blob = self.git("hash-object", "-w", "--stdin", input_text=text).stdout.strip()
                self.git("update-index", "--add", "--cacheinfo", f"100644,{blob},{self.paths[key]}", env=env)
            tree = self.git("write-tree", env=env).stdout.strip()
            return self.git("commit-tree", tree, "-p", tip, "-m", message, env=env).stdout.strip()

    def transact(self, fn, message="Continuous intelligence monitor state"):
        last = None
        for _ in range(self.attempts):
            tip = self._fetch()
            writes, result = fn(Snapshot(self._reader(tip)))
            if not writes:
                return writes, result
            commit = self._commit(tip, writes, message)
            push = self.git("push", "--quiet", self.remote, f"{commit}:refs/heads/{self.branch}", check=False)
            if push.returncode == 0:
                self.pushes += 1
                if self.mirror:
                    for key, text in writes.items():
                        target = self.repo / self.paths[key]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(text, encoding="utf-8")
                return writes, result
            last = (push.stderr or push.stdout).strip()[:300]   # usually non-fast-forward: re-read and retry
        raise StateSyncError(f"state push rejected {self.attempts} times: {last}")


def make_backend(env, repo, paths):
    """TI_MONITOR_STATE_BACKEND: unset or 'file' (local default), 'git' (GitHub Actions). Anything else fails."""
    choice = (env.get("TI_MONITOR_STATE_BACKEND") or "file").strip().lower()
    if choice == "file":
        return FileStateBackend({k: Path(repo) / v if not Path(v).is_absolute() else Path(v) for k, v in paths.items()})
    if choice == "git":
        return GitStateBackend(repo, paths, branch=(env.get("TI_MONITOR_STATE_BRANCH") or "main").strip())
    raise StateSyncError("TI_MONITOR_STATE_BACKEND must be 'file' or 'git'")
