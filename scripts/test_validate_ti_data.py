#!/usr/bin/env python3
"""Regression check that unsupported record dataType values fail validation."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    with tempfile.TemporaryDirectory(prefix="ti-data-type-regression-") as temp:
        root = Path(temp)
        (root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "validate_ti_data.py", root / "scripts" / "validate_ti_data.py")
        data = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
        if not data.get("records"):
            raise SystemExit("Regression fixture requires at least one record")
        data["records"][0]["dataType"] = "GUIDANCE"
        (root / "data.json").write_text(json.dumps(data), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "validate_ti_data.py")],
            cwd=root,
            text=True,
            capture_output=True,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0 or "unsupported dataType 'GUIDANCE'" not in output:
            print(output, file=sys.stderr)
            raise SystemExit("FAIL: unsupported dataType GUIDANCE was not rejected")
    print("PASS: unsupported dataType GUIDANCE is rejected without changing production data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
