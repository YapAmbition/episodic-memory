"""Publish the tested ZIP to the dedicated installable repository (stdlib only)."""
import argparse
import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import zipfile

TARGET = 'https://github.com/YapAmbition/memory-slice-astrbot-plugin.git'
REPO = TARGET.removesuffix('.git')
PREFIX = 'astrbot_plugin_memory_slice/'
VERSION = r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'


def version_of(text):
    match = re.search(r'^version:\s*[\"\']?v?(' + VERSION + r')[\"\']?\s*$', text, re.M)
    if not match:
        raise ValueError('metadata.yaml 缺少有效的三段版本号')
    return match.group(1)


def validate_tag(tag, metadata):
    version = version_of(metadata)
    if tag != 'astrbot-v' + version:
        raise ValueError('发布标签必须与 metadata.yaml 一致：astrbot-v' + version)
    return version


def read_package(archive, tag):
    if Path(archive).stat().st_size > 16 * 1024 * 1024:
        raise ValueError('插件 ZIP 超过 16 MiB')
    files = {}
    with zipfile.ZipFile(archive) as z:
        if sum(i.file_size for i in z.infolist()) > 128 * 1024 * 1024:
            raise ValueError('解压内容过大')
        for item in z.infolist():
            name = item.filename
            if item.is_dir():
                continue
            if not name.startswith(PREFIX):
                raise ValueError('ZIP 必须是构建脚本生成的插件包')
            relative = name[len(PREFIX):]
            parts = PurePosixPath(relative).parts
            if (not parts or relative.startswith('/') or '\\' in relative
                    or any(p in ('.', '..') or p.startswith('.') for p in parts)
                    or any(p in ('node_modules', '__pycache__', 'data', 'backups') for p in parts)
                    or relative.endswith(('.db', '.sqlite', '.sqlite3', '.log', '.pyc'))
                    or (item.external_attr >> 16) & 0o170000 == 0o120000
                    or relative in files):
                raise ValueError('ZIP 包含不允许发布的路径')
            files[relative] = z.read(item)
    required = {'main.py', 'metadata.yaml', 'requirements.txt', '_conf_schema.json',
                'README.md', 'memory_slice/service.py', 'frontend/index.html', 'core-manifest.json'}
    if not required <= files.keys() or not any(n.startswith('frontend/assets/') for n in files):
        raise ValueError('插件包缺少运行必需文件')
    metadata = files['metadata.yaml'].decode()
    version = validate_tag(tag, metadata)
    if not re.search(r'^repo:\s*[\"\']?' + re.escape(REPO) + r'[\"\']?\s*$', metadata, re.M):
        raise ValueError('插件 repo 必须指向发布仓库')
    import hashlib
    manifest = json.loads(files['core-manifest.json'])
    actual = {n: hashlib.sha256(b).hexdigest() for n, b in files.items() if n.startswith('memory_slice/')}
    if manifest != actual:
        raise ValueError('插件核心校验清单不匹配')
    return files, version


def git(folder, *args, env=None):
    result = subprocess.run(['git', *args], cwd=folder, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        # Avoid echoing credentials, remote responses or local paths in CI logs.
        raise RuntimeError('Git 操作失败：' + args[0] + '；请检查凭据、分支保护或并发更新')
    return result.stdout.strip()


def publish(archive, tag, source_sha, remote=TARGET, env=None):
    if not re.fullmatch(r'[0-9a-f]{40}', source_sha):
        raise ValueError('来源提交必须是完整 SHA')
    files, version = read_package(archive, tag)
    release = {'tag': tag, 'source_commit': source_sha,
               'source_repository': 'YapAmbition/episodic-memory'}
    files['release-info.json'] = (json.dumps(release, indent=2) + '\n').encode()
    with tempfile.TemporaryDirectory(prefix='memory-publish-') as temp:
        folder = Path(temp) / 'repo'
        git(temp, 'clone', '--quiet', remote, str(folder), env=env)
        branches = git(folder, 'branch', '-r', env=env).splitlines()
        if any(b.strip() == 'origin/main' for b in branches):
            git(folder, 'checkout', '-B', 'main', 'origin/main', env=env)
        elif branches:
            raise ValueError('目标仓库已有其他分支但没有 main，请先核对')
        else:
            git(folder, 'checkout', '--orphan', 'main', env=env)
        existing = folder / 'metadata.yaml'
        old_version = version_of(existing.read_text(encoding="utf-8")) if existing.exists() else None
        tag_exists = bool(git(folder, 'tag', '--list', tag, env=env))
        if tag_exists:
            old = git(folder, 'show', tag + ':release-info.json', env=env)
            if json.loads(old) == release:
                return '该标签已发布，无需重复推送'
            raise ValueError('目标标签已存在且来源不同，禁止覆盖')
        if old_version and tuple(map(int, version.split('.'))) <= tuple(map(int, old_version.split('.'))):
            raise ValueError('发布版本必须高于目标仓库现有版本')
        # Only generated trees may be replaced; a fresh GitHub README is allowed.
        tracked = set(filter(None, git(folder, 'ls-files', env=env).splitlines()))
        if tracked and not (folder / 'release-info.json').exists() and tracked - {'README.md', '.gitignore', 'LICENSE'}:
            raise ValueError('目标仓库包含非发布内容，拒绝覆盖')
        # Retain a licensing notice supplied by the target repository owner.
        license_path = folder / 'LICENSE'
        if license_path.is_file() and not license_path.is_symlink():
            files.setdefault('LICENSE', license_path.read_bytes())
        for path in folder.iterdir():
            if path.name == '.git':
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        for name, content in files.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        git(folder, 'config', 'user.name', 'github-actions[bot]', env=env)
        git(folder, 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com', env=env)
        git(folder, 'add', '--all', env=env)
        git(folder, 'commit', '-m', 'Publish ' + tag + ' from ' + source_sha[:12], env=env)
        git(folder, 'tag', tag, env=env)
        git(folder, 'push', '--atomic', 'origin', 'HEAD:refs/heads/main', 'refs/tags/' + tag, env=env)
    return '已发布 ' + tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-tag', help='仅校验标签与源码版本，不发布')
    parser.add_argument('--archive')
    parser.add_argument('--tag')
    parser.add_argument('--source-sha')
    args = parser.parse_args()
    if args.check_tag:
        root = Path(__file__).resolve().parents[1]
        metadata = (root / 'plugins/astrbot_plugin_memory_slice/metadata.yaml').read_text(encoding="utf-8")
        print(validate_tag(args.check_tag, metadata))
        return
    if not all((args.archive, args.tag, args.source_sha)):
        parser.error('发布需要 --archive、--tag、--source-sha')
    token = os.environ.get('PLUGIN_PUBLISH_TOKEN', '')
    if not token:
        raise ValueError('开发仓库尚未配置 PLUGIN_PUBLISH_TOKEN Secret')
    env = dict(os.environ)
    env.pop('PLUGIN_PUBLISH_TOKEN', None)
    env.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_COUNT='1',
               GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
               GIT_CONFIG_VALUE_0='AUTHORIZATION: basic ' + base64.b64encode(('x-access-token:' + token).encode()).decode())
    print(publish(args.archive, args.tag, args.source_sha, env=env))


if __name__ == '__main__':
    main()
