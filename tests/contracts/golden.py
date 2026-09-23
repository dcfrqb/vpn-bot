"""Golden-file helper: compare JSON with tests/contracts/golden/<name>.json.

UPDATE_GOLDEN=1 pytest ... rewrites the files (only for an intended contract
change, reviewed together with the site team)."""
import json
import os
import pathlib

GOLDEN_DIR = pathlib.Path(__file__).resolve().parent / "golden"


def assert_golden(name: str, data) -> None:
    path = GOLDEN_DIR / f"{name}.json"
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if os.getenv("UPDATE_GOLDEN") == "1" or not path.exists():
        path.write_text(text)
        if os.getenv("UPDATE_GOLDEN") != "1":
            raise AssertionError(f"golden {path.name} was missing and has been written; re-run")
        return
    assert json.loads(text) == json.loads(path.read_text()), f"contract changed: {path.name}"
