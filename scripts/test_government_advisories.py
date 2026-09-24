#!/usr/bin/env python3
"""Offline regression tests for the government advisory change collector."""
from __future__ import annotations
import importlib.util
import json
import tempfile
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("collect_government_advisories.py")
spec = importlib.util.spec_from_file_location("collector", MODULE_PATH)
collector = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(collector)

def run():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        collector.SNAPSHOT = root / "source_snapshots.json"
        collector.QUEUE = root / "government_change_queue.json"
        collector.FEEDS = {"test-source": ["https://example.test/advisories"]}

        raw = b"""<?xml version="1.0"?><rss><channel>
          <item><title>Testland</title><link>https://example.test/testland</link>
          <pubDate>2026-09-23</pubDate><description>Exercise increased caution</description></item>
        </channel></rss>"""

        def ok_fetch(url):
            return raw, "application/rss+xml"

        collector.fetch = ok_fetch
        assert collector.main() == 0
        first = json.loads(collector.QUEUE.read_text(encoding="utf-8"))
        assert len(first["items"]) == 1
        assert first["items"][0]["changeType"] == "NEW"

        # A changed advisory must become UPDATED, not another NEW item.
        changed = raw.replace(b"Exercise increased caution", b"Reconsider travel")
        collector.fetch = lambda url: (changed, "application/rss+xml")
        assert collector.main() == 0
        second = json.loads(collector.QUEUE.read_text(encoding="utf-8"))
        assert len(second["items"]) == 1
        assert second["items"][0]["changeType"] == "UPDATED"

        # A total feed failure must preserve the last known snapshot and emit
        # no false changes.
        collector.fetch = lambda url: (_ for _ in ()).throw(RuntimeError("simulated outage"))
        assert collector.main() == 0
        snapshot = json.loads(collector.SNAPSHOT.read_text(encoding="utf-8"))
        queue = json.loads(collector.QUEUE.read_text(encoding="utf-8"))
        assert snapshot["sources"]["test-source"]["itemCount"] == 1
        assert queue["items"] == []

    print("PASS: government advisory collector regression tests")
    return 0

if __name__ == "__main__":
    raise SystemExit(run())
