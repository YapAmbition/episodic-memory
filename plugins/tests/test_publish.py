"""Release tests use fixture ZIPs and a local bare repository; never contact GitHub."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from publish_astrbot_plugin import PREFIX, REPO, git, publish, read_package, validate_tag


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(self.remote)],
                       check=True, capture_output=True)

    def package(self, version='1.0.0', extra=None):
        files = {'main.py': b'# plugin', 'metadata.yaml': f'version: v{version}\nrepo: {REPO}\n'.encode(),
                 'requirements.txt': b'', '_conf_schema.json': b'{}', 'README.md': b'plugin',
                 'memory_slice/service.py': b'# core', 'frontend/index.html': b'<html></html>',
                 'frontend/assets/chunk.js': b'// lazy chunk'}
        files['core-manifest.json'] = json.dumps({'memory_slice/service.py': hashlib.sha256(files['memory_slice/service.py']).hexdigest()}).encode()
        files.update(extra or {})
        path = self.root / (version + '.zip')
        with zipfile.ZipFile(path, 'w') as z:
            for name, data in files.items():
                z.writestr(PREFIX + name, data)
        return path

    def test_tag_must_match_metadata(self):
        with self.assertRaises(ValueError):
            validate_tag('astrbot-v1.0.1', 'version: v1.0.0')
        with self.assertRaises(ValueError):
            validate_tag('astrbot-v1.0.0;echo hi', 'version: v1.0.0')

    def test_empty_remote_publish_and_idempotent_retry(self):
        package = self.package()
        publish(package, 'astrbot-v1.0.0', 'a' * 40, remote=str(self.remote))
        self.assertEqual(git(self.remote, 'show', 'main:frontend/assets/chunk.js'), '// lazy chunk')
        self.assertEqual(git(self.remote, 'rev-parse', 'main'), git(self.remote, 'rev-parse', 'astrbot-v1.0.0'))
        head = git(self.remote, 'rev-parse', 'main')
        publish(package, 'astrbot-v1.0.0', 'a' * 40, remote=str(self.remote))
        self.assertEqual(head, git(self.remote, 'rev-parse', 'main'))
        with self.assertRaises(ValueError):
            publish(package, 'astrbot-v1.0.0', 'b' * 40, remote=str(self.remote))

    def test_update_preserves_history_removes_stale_assets_and_refuses_downgrade(self):
        publish(self.package(extra={'frontend/assets/old.js': b'old'}), 'astrbot-v1.0.0', 'a' * 40, remote=str(self.remote))
        first = git(self.remote, 'rev-parse', 'main')
        publish(self.package('1.1.0'), 'astrbot-v1.1.0', 'b' * 40, remote=str(self.remote))
        self.assertEqual(first, git(self.remote, 'rev-parse', 'main^'))
        self.assertNotIn('old.js', git(self.remote, 'ls-tree', '-r', '--name-only', 'main'))
        with self.assertRaises(ValueError):
            publish(self.package('1.0.1'), 'astrbot-v1.0.1', 'c' * 40, remote=str(self.remote))
        self.assertEqual(first, git(self.remote, 'rev-parse', 'astrbot-v1.0.0'))

    def test_rejects_unsafe_paths_before_touching_remote(self):
        for name in ['../oops', '.git/config', '.github/workflows/evil.yml', 'data/secret.json', 'memory.sqlite3']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                read_package(self.package(extra={name: b'x'}), 'astrbot-v1.0.0')
        self.assertEqual(git(self.remote, 'for-each-ref'), '')

    def test_rejects_corrupt_core_and_missing_resources(self):
        with self.assertRaises(ValueError):
            read_package(self.package(extra={'memory_slice/service.py': b'tampered'}), 'astrbot-v1.0.0')
        path = self.root / 'empty.zip'
        with zipfile.ZipFile(path, 'w'):
            pass
        with self.assertRaises(ValueError):
            read_package(path, 'astrbot-v1.0.0')

    def test_rejects_unknown_target_content_without_deleting_it(self):
        local = self.root / 'seed'
        git(self.root, 'clone', str(self.remote), str(local))
        (local / 'precious.txt').write_text('keep me')
        git(local, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'add', '.')
        git(local, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'seed')
        git(local, 'push', 'origin', 'main')
        with self.assertRaises(ValueError):
            publish(self.package(), 'astrbot-v1.0.0', 'a' * 40, remote=str(self.remote))
        self.assertEqual(git(self.remote, 'show', 'main:precious.txt'), 'keep me')
