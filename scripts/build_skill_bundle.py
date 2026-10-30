"""Build a small Skill and a separate local service archive, without user data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def archive(path, files):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            z.writestr(entry, data)


def build(output, version='0.2.6', build_frontend=True):
    from build_astrbot_plugin import frontend_sources
    import re
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('版本必须为 X.Y.Z')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    frontend = ROOT/'frontend'
    stamp = frontend/'dist/source-manifest.json'
    if build_frontend:
        npm = shutil.which('npm')
        if not npm:
            raise RuntimeError('开发者构建需要 Node.js/npm，安装用户不需要')
        # Refresh from the lockfile even after a source update with existing node_modules.
        subprocess.run([npm, 'ci', '--no-audit', '--no-fund'], cwd=frontend, check=True)
        subprocess.run([npm, 'run', 'build'], cwd=frontend, check=True)
        stamp.write_text(json.dumps(frontend_sources(), sort_keys=True))
    if not stamp.exists() or json.loads(stamp.read_text()) != frontend_sources():
        raise RuntimeError('前端构建不存在或已过期，请重新构建')
    files = {}
    for path in (ROOT/'backend/memory_slice').glob('*.py'):
        files['backend/memory_slice/'+path.name] = path.read_bytes()
    for path in (ROOT/'evals/recall').rglob('*.json'):
        files['backend/memory_slice/benchmark_data/'+path.relative_to(ROOT/'evals/recall').as_posix()] = path.read_bytes()
    for path in (frontend/'dist').rglob('*'):
        if path.is_file() and path.name != 'source-manifest.json' and path.suffix != '.map':
            files['frontend/'+path.relative_to(frontend/'dist').as_posix()] = path.read_bytes()
    for name in ('service_host.py','codex_sync.py'):
        files[name] = (ROOT/'distribution'/name).read_bytes()
    files['requirements.txt'] = (ROOT/'backend/requirements.txt').read_bytes()+b'\nkeyring==25.6.0\n'
    files['service-package.json'] = json.dumps({'version':version,'api_version':1}).encode()
    package = output/f'memory-slice-service-{version}.zip'
    archive(package, files)
    skill = output/'memory-slice'
    # Only copy explicit source types; never ship __pycache__, local data or keys.
    source_files = {}
    for path in (ROOT/'skills/memory-slice').rglob('*'):
        if path.is_file() and path.suffix in ('.py','.md','.yaml'):
            name = path.relative_to(ROOT/'skills/memory-slice').as_posix()
            source_files[name] = path.read_bytes()
    source_files['release.json'] = json.dumps({'service_version':version,'api_version':1,
        'source':{'type':'local-copy','path':'../'+package.name},
        'sha256':hashlib.sha256(package.read_bytes()).hexdigest()}, indent=2).encode()
    for name, data in source_files.items():
        dest = skill/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    archive(output/f'memory-slice-skill-{version}.zip', {'memory-slice/'+k:v for k,v in source_files.items()})
    return package, skill


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/skill-local')
    parser.add_argument('--version', default='0.2.6')
    parser.add_argument('--skip-frontend-build', action='store_true')
    args = parser.parse_args()
    for path in build(args.output, args.version, not args.skip_frontend_build):
        print(path)
