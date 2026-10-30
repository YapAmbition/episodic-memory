"""Managed local host. Kept out of the framework-independent memory core."""
import argparse
from contextlib import asynccontextmanager
import asyncio
from collections.abc import MutableMapping
import json
from pathlib import Path
import secrets


class CredentialMap(MutableMapping):
    """Keychain-backed mapping; service shutdown clears RAM, never saved keys."""
    def __init__(self, backend, namespace, ids):
        self.backend, self.namespace, self.ids = backend, namespace, set(ids)

    def __getitem__(self, key):
        value = self.backend.get_password(self.namespace, key)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        self.backend.set_password(self.namespace, key, value)
        self.ids.add(key)

    def __delitem__(self, key):
        self[key]
        self.backend.delete_password(self.namespace, key)
        self.ids.discard(key)

    def __iter__(self):
        return (key for key in self.ids if self.get(key) is not None)

    def __len__(self):
        return sum(1 for _ in self)

    def clear(self):
        # Core calls clear() on shutdown. Explicit removal uses pop().
        pass


def make_app(home):
    from fastapi import FastAPI, Request
    from memory_slice.errors import MemoryError as DomainError
    from starlette.responses import JSONResponse
    from memory_slice.web import create_app
    config = json.loads((home/'config.json').read_text(encoding="utf-8"))
    metadata = json.loads((Path(__file__).parent/'service-package.json').read_text(encoding="utf-8"))
    inner = create_app(data_dir=home/'data', seed=False, access_token=config['token'], host_mode='agent')
    service = inner.state.memory
    if config['credentials'] == 'keyring':
        import keyring
        backend = keyring.get_keyring()
        if backend.priority <= 0:
            raise RuntimeError('系统密钥库不可用；配置可用的 keyring 后重试，或显式选择 --credentials session')
        ids = [s['id'] for s in service.store.list('space')]
        for kind, owner, attribute in [('model', service, 'api_keys'), ('embedding', service.embeddings, 'keys')]:
            setattr(owner, attribute, CredentialMap(backend, 'memory-slice:'+config['instance_id']+':'+kind, ids))

    # Outer app reserves management URLs before the inner SPA catch-all.
    from codex_sync import HistorySync
    sync = HistorySync(service)

    @asynccontextmanager
    async def lifespan(app):
        async with inner.router.lifespan_context(inner):
            sync.start()
            try:
                yield
            finally:
                await sync.close()

    app = FastAPI(lifespan=lifespan)
    app.state.history_sync = sync
    app.state.server = None
    app.state.active = 0
    app.state.draining = False
    app.state.memory = service

    @app.middleware('http')
    async def requests(request, call_next):
        management = request.url.path.startswith('/_memory_slice/')
        integration = request.url.path.startswith('/api/v1/integrations/codex')
        if management or integration:
            token = request.headers.get('authorization', '')
            if not secrets.compare_digest(token.encode(), ('Bearer '+config['token']).encode()):
                return JSONResponse({'detail':'Unauthorized'}, status_code=401)
        if app.state.draining and not management:
            return JSONResponse({'detail':'服务正在重启，请稍后重试'}, status_code=503)
        if not management:
            app.state.active += 1
        try:
            return await call_next(request)
        finally:
            if not management:
                app.state.active -= 1

    @app.get('/_memory_slice/health')
    async def health():
        return {'status':'ok', 'instance_id':config['instance_id'], 'version':metadata['version'], 'api_version':metadata['api_version'], 'credentials':config['credentials']}

    @app.post('/_memory_slice/stop')
    async def stop():
        if app.state.active or any(j.get('status') in ('queued','running') for j in service.store.list('job')):
            return JSONResponse({'detail':'当前存在请求或整理任务，稍后重试'}, status_code=409)
        app.state.draining = True
        # Stop schedulers before returning so they cannot start a new maintenance job.
        await sync.close()
        await service.close()
        asyncio.get_running_loop().call_later(.2, setattr, app.state.server, 'should_exit', True)
        return {'stopping':True}

    @app.get('/api/v1/integrations/codex')
    async def sync_status():
        return sync.status()

    @app.put('/api/v1/integrations/codex')
    async def sync_config(request: Request):
        try:
            return await sync.configure(await request.json())
        except (ValueError, TypeError, DomainError) as exc:
            return JSONResponse({'detail':str(exc)}, status_code=422)

    @app.post('/api/v1/integrations/codex/poll')
    async def sync_now():
        sync.wakeup.set()
        return {'queued':True}

    @app.post('/api/v1/integrations/codex/backfill')
    async def sync_backfill(request: Request):
        from codex_sync import RpcError
        try:
            body=await request.json()
            return await sync.backfill(body['thread_id'],body['since'],body['until'])
        except (ValueError, TypeError, KeyError, RpcError, DomainError) as exc:
            return JSONResponse({'detail':str(exc)}, status_code=422)

    app.mount('/', inner)
    return app


if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    home = parser.parse_args().home
    cfg = json.loads((home/'config.json').read_text(encoding="utf-8"))
    app = make_app(home)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=cfg['port'], access_log=False))
    app.state.server = server
    server.run()
