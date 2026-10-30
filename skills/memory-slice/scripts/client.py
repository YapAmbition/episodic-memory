#!/usr/bin/env python3
"""Memory Slice local bootstrap and shared HTTP client (Python 3.11+, stdlib only)."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

SKILL = Path(__file__).resolve().parents[1]
AGENTS = {'codex': 'Codex 记忆', 'qoder': 'Qoder 记忆', 'deepseek-harness': 'DeepSeek Harness 记忆', 'unknown': '通用记忆'}


def agent_name(value):
    return value if value in AGENTS else 'unknown'


def default_home():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/MemorySlice'
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'MemorySlice'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share'))) / 'memory-slice'


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


@contextmanager
def lock(path, timeout=20):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0); f.write(b'0'); f.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() > deadline:
                    raise RuntimeError('另一个安装/调用正在进行，请稍后重试')
                time.sleep(.1)
        try:
            yield
        finally:
            if os.name == 'nt':
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def version(value):
    import re
    if not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('首版仅支持 X.Y.Z 正式版本号')
    return tuple(map(int, value.split('.')))


def request(config, path, body=None, timeout=10):
    req = Request(f"http://127.0.0.1:{config['port']}" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json', 'Authorization':'Bearer '+config['token']})
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        # Never print submitted keys or provider errors.
        raise RuntimeError(f'接口返回 HTTP {exc.code}：{path}') from None


def health(config):
    try:
        row = request(config, '/_memory_slice/health', timeout=.5)
        if row.get('instance_id') != config['instance_id']:
            raise RuntimeError('端口由其他服务或另一份记忆切片实例占用；请指定其他 --port')
        return row
    except (URLError, TimeoutError, ConnectionError, OSError):
        return None


def free_port(port):
    if not 1024 <= port <= 65535:
        raise ValueError('端口范围为 1024–65535')
    try:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('127.0.0.1', port))
    except OSError:
        raise RuntimeError(f'端口 {port} 已占用；不会关闭占用进程，请使用 --port 指定其他端口') from None


def stop(config):
    if not health(config):
        return
    request(config, '/_memory_slice/stop', {})
    for _ in range(150):
        if not health(config):
            return
        time.sleep(.1)
    raise RuntimeError('服务尚未退出；未强制停止，请检查日志')


def python_at(release):
    return release / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def start(home, config):
    current = health(config)
    if current:
        if current.get('api_version') != 1:
            raise RuntimeError('服务接口版本不兼容')
        if current.get('version') != config['version']:
            raise RuntimeError('运行版本与安装记录不一致，请先停止服务再重试')
        return current
    failed = read(home/'start-failure.json', {})
    if time.time() - failed.get('at', 0) < 15:
        raise RuntimeError('启动失败后冷却 15 秒，请查看 logs/service.log')
    free_port(config['port'])
    release = home/'releases'/config['version']
    env = dict(os.environ, PYTHONPATH=str(release/'backend'), MEMORY_DATA_DIR=str(home/'data'),
        FRONTEND_DIST=str(release/'frontend'), SEED_DEMO='false', MEMORY_API_TOKEN=config['token'])
    # Explicit installation configuration, not inherited model credentials.
    for key in ('MODEL_API_KEY','EMBEDDING_API_KEY'):
        env.pop(key, None)
    command = [str(python_at(release)), str(release/'service_host.py'), '--home', str(home)]
    (home/'logs').mkdir(exist_ok=True)
    kwargs = {'creationflags':subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
    with (home/'logs/service.log').open('ab') as log:
        proc = subprocess.Popen(command, cwd=release, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kwargs)
    ready = False
    try:
        for _ in range(100):
            if proc.poll() is not None:
                break
            row = health(config)
            if row and row.get('version') == config['version'] and row.get('api_version') == 1:
                ready = True
                return row
            time.sleep(.1)
    finally:
        if not ready:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            write(home/'start-failure.json', {'at':time.time()})
    raise RuntimeError('服务启动未通过健康检查；查看 logs/service.log 后重试')


def stage(home, manifest, package=None):
    version(manifest['service_version'])
    target = home/'releases'/manifest['service_version']
    if target.exists() and not (target/'release.json').exists():
        active = read(home/'config.json', {})
        if active.get('version') != manifest['service_version']:
            # A killed installer may leave a partial, never-activated release.
            shutil.rmtree(target)
    if target.exists():
        meta = read(target/'release.json', {})
        if meta.get('sha256') != manifest['sha256'] or not python_at(target).exists():
            raise RuntimeError('同版本程序包内容变化或安装不完整；请发布新版本，勿覆盖正在使用的版本')
        return target
    source = Path(package or manifest['source']['path']).expanduser()
    if not source.is_absolute():
        source = SKILL / source
    source = source.resolve()
    # First release intentionally uses cp, not network download. Windows has no POSIX cp.
    with tempfile.TemporaryDirectory(prefix='memory-slice-package-') as td:
        archive = Path(td)/'service.zip'
        if os.name == 'nt':
            shutil.copyfile(source, archive)
        else:
            subprocess.run(['cp', str(source), str(archive)], check=True, timeout=30)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['sha256']:
            raise RuntimeError('程序包 SHA256 不匹配，未安装')
        target.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive) as z:
                for member in z.infolist():
                    path = Path(member.filename)
                    if path.is_absolute() or PureWindowsPath(member.filename).drive or '..' in path.parts or '\\' in member.filename or (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('不安全的程序包路径')
                if sum(x.file_size for x in z.infolist()) > 200_000_000:
                    raise ValueError('程序包解压体积超限')
                z.extractall(target)
            info = read(target/'service-package.json')
            if info['version'] != manifest['service_version'] or info['api_version'] != 1:
                raise RuntimeError('程序包版本与清单不一致')
            subprocess.run([sys.executable,'-m','venv',str(target/'venv')], check=True, stdout=subprocess.DEVNULL)
            (home/'logs').mkdir(exist_ok=True)
            with (home/'logs/install.log').open('ab') as log:
                subprocess.run([str(python_at(target)),'-m','pip','install','--index-url','https://pypi.org/simple','-r',str(target/'requirements.txt')], check=True, stdout=log, stderr=log, timeout=300)
            write(target/'release.json', {'version':manifest['service_version'],'sha256':manifest['sha256']})
        except BaseException:
            shutil.rmtree(target)
            raise
    return target


def ensure(home, *, port=None, credentials=None, package=None):
    manifest = read(SKILL/'release.json')
    if not manifest:
        raise RuntimeError('缺少 release.json；请使用 build_skill_bundle.py 生成的 Skill 分发目录')
    config = read(home/'config.json')
    if not config:
        free_port(port or 8095)
        config = {'version':manifest['service_version'],'port':port or 8095,'token':secrets.token_urlsafe(32),
                  'instance_id':str(uuid.uuid4()),'credentials':credentials or 'keyring','spaces':{},'sessions':{}}
        stage(home, manifest, package)
        write(home/'config.json', config)
    elif version(config['version']) < version(manifest['service_version']):
        stage(home, manifest, package)  # prepare while the old version remains available
        old = dict(config)
        was_running = bool(health(old))
        stop(old)  # refuses to stop while calls or maintenance are in flight
        backup = home/'backups'/f"{old['version']}-{time.time_ns()}"
        backup.mkdir(parents=True)
        db = home/'data/memory.sqlite3'
        if db.exists():
            with sqlite3.connect(db) as source, sqlite3.connect(backup/'memory.sqlite3') as dest:
                source.backup(dest)
        config = dict(config, version=manifest['service_version'])
        write(home/'config.json', config)
        try:
            start(home, config)
        except BaseException:
            # Never overwrite a database while an uncertain new process may still be running.
            stop(config)
            if (backup/'memory.sqlite3').exists():
                for suffix in ('-wal','-shm'):
                    Path(str(db)+suffix).unlink(missing_ok=True)
                shutil.copy2(backup/'memory.sqlite3', db)
            write(home/'config.json', old)
            (home/'start-failure.json').unlink(missing_ok=True)
            if was_running:
                start(home, old)
            raise
    if port is not None and port != config['port']:
        free_port(port)
        stop(config)
        config['port'] = port
        write(home/'config.json', config)
    if credentials and credentials != config['credentials']:
        stop(config)
        config['credentials'] = credentials
        write(home/'config.json', config)
    start(home, config)
    return config


def binding(home, config, agent, session=None):
    name = agent_name(agent)
    sid = config['spaces'].get(name)
    if not sid:
        # Recover mapping after a crash between HTTP creation and saving configuration.
        marker = 'memory-slice-integration:' + config['instance_id'] + ':' + name
        spaces = request(config, '/api/v1/spaces')
        found = next((s for s in spaces if s.get('description') == marker), None)
        sid = (found or request(config,'/api/v1/spaces',{'name':AGENTS[name], 'description':marker}))['id']
        config['spaces'][name] = sid
        write(home/'config.json', config)
    cid = None
    if session is not None:
        if not session:
            raise ValueError('记录消息必须提供稳定的 --session，不默认复用最近会话')
        key = name + ':' + hashlib.sha256(session.encode()).hexdigest()
        cid = config['sessions'].get(key)
        if not cid:
            title = 'agent-session:' + key
            found = next((c for c in request(config,f'/api/v1/spaces/{sid}/conversations') if c.get('title') == title), None)
            person = request(config, f'/api/v1/spaces/{sid}/persons')[0]['id']
            cid = (found or request(config,f'/api/v1/spaces/{sid}/conversations',{'person_id':person,'title':title}))['id']
            config['sessions'][key] = cid
            write(home/'config.json',config)
    return sid, cid


def install_hooks(home, codex_home):
    import re
    import shlex
    exe = shutil.which('codex')
    if not exe:
        return {'status':'unsupported_environment','reason':'未找到 codex CLI；可按需调用，未安装 Hook'}
    result = subprocess.run([exe,'--version'], capture_output=True,text=True,timeout=10)
    match = re.search(r'(\d+\.\d+\.\d+)',result.stdout)
    if not match or version(match[1]) < (0,144,6):
        return {'status':'unsupported_environment','reason':'首版适配验证基于 Codex CLI 0.144.6+；当前版本未通过检查'}
    codex_home.mkdir(parents=True,exist_ok=True)
    path = codex_home/'hooks.json'
    with lock(codex_home/'memory-slice-install.lock'):
        doc = read(path, {'hooks':{}})
        if not isinstance(doc.get('hooks'),dict):
            raise ValueError('已有 hooks.json 格式不兼容，未覆盖')
        before = json.dumps(doc,sort_keys=True)
        marker = 'Memory Slice — '
        for event in ('SessionStart','UserPromptSubmit','Stop'):
            groups = doc['hooks'].get(event,[])
            kept=[]
            for group in groups:
                handlers = [h for h in group.get('hooks',[]) if not h.get('statusMessage','').startswith(marker)]
                if handlers:
                    kept.append(dict(group,hooks=handlers))
            command = [sys.executable,str(SKILL/'scripts/hook.py'),'--home',str(home)]
            handler={'type':'command','command':shlex.join(command),'commandWindows':subprocess.list2cmdline(command),
                     'timeout':30,'statusMessage':marker+event}
            if event=='UserPromptSubmit':
                handler['additionalContextLimit']=6000
            kept.append({'hooks':[handler]})
            doc['hooks'][event]=kept
        if json.dumps(doc,sort_keys=True)!=before:
            if path.exists():
                shutil.copy2(path,path.with_name('hooks.json.backup-'+str(time.time_ns())))
            write(path,doc)
    return {'status':'installed_needs_review','path':str(path),'reason':'在 Codex /hooks 中审阅并信任；安装文件不代表已启用'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['setup','status','stop','recall','record','maintain','new-session','open'])
    p.add_argument('--agent',default='unknown')
    p.add_argument('--home',type=Path,default=default_home())
    p.add_argument('--port',type=int)
    p.add_argument('--package')
    p.add_argument('--credentials',choices=['keyring','session'])
    p.add_argument('--codex-home',type=Path,default=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))))
    p.add_argument('--no-hooks',action='store_true')
    p.add_argument('--no-sync',action='store_true',help='Isolated testing only: do not enable history sync')
    p.add_argument('--query',default='')
    p.add_argument('--session')
    p.add_argument('--message-id')
    p.add_argument('--role',choices=['user','assistant'],default='user')
    p.add_argument('--text-file',type=Path,help='UTF-8 text file; do not place message text or keys in command arguments')
    args=p.parse_args()
    if sys.version_info < (3,11):
        p.error('需要 Python 3.11 或更新版本')
    home=args.home.expanduser().resolve();home.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        with lock(home/'service.lock',timeout=300 if args.action=='setup' else 20):
            if args.action=='status':
                cfg=read(home/'config.json')
                row={'installed':bool(cfg),'running':bool(health(cfg)) if cfg else False,'version':cfg.get('version') if cfg else None,'spaces':cfg.get('spaces',{}) if cfg else {},'credentials':cfg.get('credentials') if cfg else None,'url':f"http://127.0.0.1:{cfg['port']}" if cfg else None}
            elif args.action=='stop':
                cfg=read(home/'config.json')
                if cfg: stop(cfg)
                row={'stopped':True,'data_preserved':True}
            elif args.action=='new-session':
                row={'session':str(uuid.uuid4())}
            else:
                cfg=ensure(home,port=args.port,credentials=args.credentials,package=args.package)
                agent=agent_name(args.agent)
                sid,cid=binding(home,cfg,agent,args.session if args.action=='record' else None)
                prefix=f'/api/v1/spaces/{sid}'
                if args.action=='setup':
                    if agent=='codex' and not args.no_sync:
                        req=Request(f"http://127.0.0.1:{cfg['port']}/api/v1/integrations/codex",method='PUT',data=json.dumps({'space_id':sid}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+cfg['token']})
                        with urlopen(req,timeout=30) as response:
                            json.load(response)
                    hooks=install_hooks(home,args.codex_home) if agent=='codex' and not args.no_hooks else {'status':'on_demand'}
                    row={'url':f"http://127.0.0.1:{cfg['port']}",'space_id':sid,'agent':agent,'version':cfg['version'],'hooks':hooks,'credentials':cfg['credentials'],'note':'页面首次访问需要服务令牌；运行 open 自动在本机浏览器配置。请在页面填写模型 Key。'}
                elif args.action=='recall':
                    row=request(cfg,prefix+'/context',{'query':args.query})
                elif args.action=='record':
                    if not args.session or not args.message_id or not args.text_file:
                        raise ValueError('record 需要 --session、--message-id 和 --text-file')
                    from hook import record
                    record(cfg,sid,cid,args.message_id,args.role,args.text_file.read_text(encoding='utf-8'))
                    row={'recorded':True}
                elif args.action=='maintain':
                    row=request(cfg,prefix+'/maintenance',{})
                else:
                    import webbrowser
                    webbrowser.open(f"http://127.0.0.1:{cfg['port']}/#memory-slice-token="+cfg['token'])
                    row={'opened':True}
        print(json.dumps(row,ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print(str(exc) if isinstance(exc,(RuntimeError,ValueError,FileNotFoundError)) else f'操作失败（{type(exc).__name__}）；检查日志后重试',file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
