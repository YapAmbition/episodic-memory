"""Produce an installable plugin zip from a single canonical copy of the core."""

import argparse
import hashlib
import json
import subprocess
import shutil
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "astrbot_plugin_memory_slice"


def frontend_sources():
    paths = list((ROOT / "frontend" / "src").rglob("*"))
    paths += [
        ROOT / "frontend" / name
        for name in (
            "index.html",
            "package.json",
            "package-lock.json",
            "tsconfig.json",
            "vite.config.ts",
        )
    ]
    return {
        str(p.relative_to(ROOT / "frontend")): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in paths
        if p.is_file()
    }


def build(output, build_frontend=True):
    frontend = ROOT / "frontend"
    stamp = frontend / "dist" / "source-manifest.json"
    if build_frontend:
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError(
                "打包前端需要 Node.js 与 npm；安装插件的用户无需安装它们"
            )
        if not (frontend / "node_modules").is_dir():
            subprocess.run(
                [npm, "ci", "--no-audit", "--no-fund"], cwd=frontend, check=True
            )
        subprocess.run([npm, "run", "build"], cwd=frontend, check=True)
        stamp.write_text(json.dumps(frontend_sources(), sort_keys=True))
    if not stamp.is_file() or json.loads(stamp.read_text()) != frontend_sources():
        raise RuntimeError("前端未构建或已过期，请重新打包，不要跳过前端构建")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = {}
    for path in (ROOT / "plugins" / NAME).iterdir():
        if path.is_file() and path.suffix in (".py", ".json", ".yaml", ".txt", ".md"):
            files[path.name] = path.read_bytes()
    for path in (ROOT / "backend" / "memory_slice").glob("*.py"):
        files["memory_slice/" + path.name] = path.read_bytes()
    for path in (ROOT / "evals" / "recall").rglob("*.json"):
        files["memory_slice/benchmark_data/" + path.relative_to(ROOT / "evals" / "recall").as_posix()] = path.read_bytes()
    for path in (frontend / "dist").rglob("*"):
        if path.is_file() and path.name != "source-manifest.json":
            files["frontend/" + path.relative_to(frontend / "dist").as_posix()] = (
                path.read_bytes()
            )
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
    parser.add_argument("--output", default=str(ROOT / "dist" / (NAME + "-v0.3.1.zip")))
    parser.add_argument(
        "--skip-frontend-build",
        action="store_true",
        help="复用已验证与源码一致的前端构建",
    )
    args = parser.parse_args()
    print(build(args.output, build_frontend=not args.skip_frontend_build))
