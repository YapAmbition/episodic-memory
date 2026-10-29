"""Local development process lifecycle; no containers or production frontend build."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / '.run'
RUN.mkdir(exist_ok=True)
BACKEND_PORT = int(os.environ.get('MEMORY_BACKEND_PORT', '8000'))
FRONTEND_PORT = int(os.environ.get('MEMORY_FRONTEND_PORT', '5173'))


def owned(name):
    file = RUN / f'{name}.json'
    if not file.exists():
        return None
    row = json.loads(file.read_text())
    result = subprocess.run(['ps', '-p', str(row['pid']), '-o', 'command='], capture_output=True, text=True)
    marker = str(ROOT / ('backend' if name == 'backend' else 'frontend/node_modules/vite/bin/vite.js'))
    if result.returncode == 0 and marker in result.stdout:
        return row
    file.unlink(missing_ok=True)
    return None


def stop(name):
    row = owned(name)
    if not row:
        print(f'{name}: 未运行')
        return
    os.killpg(row['pid'], signal.SIGTERM)
    for _ in range(150):
        if not owned(name):
            print(f'{name}: 已停止')
            return
        time.sleep(.1)
    raise RuntimeError(f'{name} 尚未退出，请查看 .run/{name}.log；未强制终止以保护正在写入的数据')


def start(name):
    row = owned(name)
    if row:
        print(f'{name}: 已运行，http://127.0.0.1:{row["port"]}')
        return False
    port = BACKEND_PORT if name == 'backend' else FRONTEND_PORT
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', port))
    env = dict(os.environ)
    env['PYTHONPATH'] = str(ROOT / 'backend')
    env['MEMORY_DATA_DIR'] = str(Path(env.get('MEMORY_DATA_DIR', str(ROOT / 'data'))).resolve())
    env['MEMORY_BACKEND_PORT'] = str(BACKEND_PORT)
    env.setdefault('SEED_DEMO', 'false')
    if name == 'backend':
        cmd = [str(ROOT / '.venv/bin/python'), '-m', 'uvicorn', 'app.main:app', '--app-dir', str(ROOT / 'backend'), '--host', '127.0.0.1', '--port', str(port)]
        cwd = ROOT
    else:
        node = shutil.which('node')
        if not node:
            raise RuntimeError('请安装 Node.js')
        cmd = [node, str(ROOT / 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', str(port), '--strictPort']
        cwd = ROOT / 'frontend'
    with (RUN / f'{name}.log').open('ab') as log:
        process = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (RUN / f'{name}.json').write_text(json.dumps({'pid': process.pid, 'port': port}))
    url = f'http://127.0.0.1:{port}' + ('/api/health' if name == 'backend' else '/')
    for _ in range(100):
        if process.poll() is not None:
            break
        try:
            with urlopen(url, timeout=.5) as response:
                if response.status == 200:
                    print(f'{name}: http://127.0.0.1:{port}（日志 .run/{name}.log）')
                    return True
        except (OSError, TimeoutError):
            pass
        time.sleep(.1)
    stop(name)
    raise RuntimeError(f'{name} 启动失败，请查看 .run/{name}.log')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['start', 'stop', 'restart-backend', 'status'])
    args = parser.parse_args()
    if args.command == 'start':
        created = start('backend')
        try:
            start('frontend')
        except Exception:
            if created:
                stop('backend')
            raise
    elif args.command == 'stop':
        stop('frontend')
        stop('backend')
    elif args.command == 'restart-backend':
        stop('backend')
        start('backend')
    else:
        for name in ('backend', 'frontend'):
            row = owned(name)
            print(f'{name}: http://127.0.0.1:{row["port"]} PID={row["pid"]}' if row else f'{name}: 未运行')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
