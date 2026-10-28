"""Produce an installable plugin zip from a single canonical copy of the core."""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "astrbot_plugin_memory_slice"


def build(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = {}
    for path in (ROOT / "plugins" / NAME).iterdir():
        if path.is_file() and path.suffix in (".py", ".json", ".yaml", ".txt", ".md"):
            files[path.name] = path.read_bytes()
    for path in (ROOT / "backend" / "memory_slice").glob("*.py"):
        files["memory_slice/" + path.name] = path.read_bytes()
    files.setdefault("__init__.py", b"")
    files["core-manifest.json"] = json.dumps(
        {
            p: hashlib.sha256(v).hexdigest()
            for p, v in files.items()
            if p.startswith("memory_slice/")
        },
        indent=2,
    ).encode()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(NAME + "/" + name, data)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "dist" / (NAME + "-v0.1.0.zip")))
    print(build(parser.parse_args().output))
