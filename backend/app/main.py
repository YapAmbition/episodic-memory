import asyncio
import io
import json
import os
import secrets
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, APIRouter, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles

from memory_slice.monitoring import CONTENT_TYPE_LATEST
from .middleware import MetricsMiddleware
from memory_slice.schemas import ChatInput, ContextInput, ConversationInput, EventInput, MaintenanceInput, MemoryConfig, MessageInput, ModelInput, PersonInput, ReinforceInput, SpaceInput, MergeInput, SplitInput, RecallFeedback, EvaluationInput, SearchInput


from memory_slice.errors import MemoryError as HTTPException
from memory_slice.service import MemoryService

async def cancellable_recall(request, operation):
    """Stop a superseded read-only model request when its HTTP client disconnects."""
    task = asyncio.create_task(operation)
    try:
        while not task.done():
            completed, _ = await asyncio.wait((task,), timeout=.1)
            if completed:
                break
            if await request.is_disconnected():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise HTTPException(499, "客户端已取消召回")
        return await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def create_app(data_dir=None, seed=True):
    service = MemoryService(data_dir or os.environ.get("MEMORY_DATA_DIR", "data"), seed=seed)
    store, metrics = service.store, service.metrics
    config_for, recall = service.config_for, service.recall

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(title="记忆切片 · Memory Slice API", version="0.6.0", description="以事件为核心的记忆服务。默认程序多路召回，可选显式大模型联想。分数为工程模型，不代表人类记忆概率。", lifespan=lifespan)
    @app.exception_handler(HTTPException)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)

    app.state.store = store
    app.state.monitoring = metrics
    app.state.memory = service
    app.add_middleware(MetricsMiddleware, monitoring=metrics)
    app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","), allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])
    bearer = HTTPBearer(auto_error=False)

    async def authenticate(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
        token = os.environ.get("MEMORY_API_TOKEN")
        if token and (credentials is None or not secrets.compare_digest(credentials.credentials, token)):
            raise HTTPException(401, "需要有效的服务访问令牌", headers={"WWW-Authenticate": "Bearer"})

    api = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])

    async def metrics_auth(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
        token = os.environ.get("MEMORY_METRICS_TOKEN") or os.environ.get("MEMORY_API_TOKEN")
        if token and (credentials is None or not secrets.compare_digest(credentials.credentials, token)):
            raise HTTPException(401, "需要有效的监控访问令牌", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/metrics", tags=["Monitoring"], dependencies=[Depends(metrics_auth)])
    def prometheus_metrics():
        return Response(metrics.prometheus(), headers={"Content-Type": CONTENT_TYPE_LATEST})

    @api.get("/monitoring", tags=["Monitoring"])
    def monitoring_json():
        return metrics.snapshot()

    @app.get("/api/health", tags=["Health"])
    def health():
        return {"status": "ok", "version": "0.6.0"}

    @api.get("/spaces", tags=["Spaces"])
    def spaces():
        return service.spaces()

    @api.post("/spaces", status_code=201, tags=["Spaces"])
    def add_space(body: SpaceInput):
        return service.add_space(body)

    @api.get("/spaces/{sid}/persons", tags=["People"])
    def persons(sid: str):
        return service.persons(sid)

    @api.post("/spaces/{sid}/persons", status_code=201, tags=["People"])
    def add_person(sid: str, body: PersonInput):
        return service.add_person(sid, body)

    def save_person(sid, pid, body):
        return service.save_person(sid, pid, body)

    @api.put("/spaces/{sid}/persons/{pid}", tags=["People"])
    def update_person(sid: str, pid: str, body: PersonInput):
        return service.update_person(sid, pid, body)

    @api.get("/spaces/{sid}/config", tags=["Configuration"])
    def get_config(sid: str):
        return service.get_config(sid)

    @api.put("/spaces/{sid}/config", tags=["Configuration"])
    def put_config(sid: str, body: MemoryConfig):
        return service.put_config(sid, body)

    @api.get("/spaces/{sid}/model", tags=["Configuration"])
    def get_model(sid: str):
        return service.get_model(sid)

    @api.put("/spaces/{sid}/model", tags=["Configuration"])
    def put_model(sid: str, body: ModelInput):
        return service.put_model(sid, body)

    @api.post("/spaces/{sid}/model/test", tags=["Configuration"])
    async def test_model(sid: str):
        return await service.test_model(sid)

    @api.get("/spaces/{sid}/capabilities", tags=["Configuration"])
    def capabilities(sid: str):
        return service.capabilities(sid)

    @api.get("/spaces/{sid}/conversations", tags=["Conversations"])
    def conversations(sid: str):
        return service.conversations(sid)

    @api.post("/spaces/{sid}/conversations", status_code=201, tags=["Conversations"])
    def add_conversation(sid: str, body: ConversationInput):
        return service.add_conversation(sid, body)

    @api.get("/spaces/{sid}/conversations/{cid}/messages", tags=["Conversations"])
    def messages(sid: str, cid: str):
        return service.messages(sid, cid)

    @api.post("/spaces/{sid}/conversations/{cid}/messages", status_code=201, tags=["Conversations"])
    def post_message(sid: str, cid: str, body: MessageInput):
        return service.post_message(sid, cid, body)

    @api.post("/spaces/{sid}/conversations/{cid}/chat", tags=["Simulator"])
    async def chat(sid: str, cid: str, body: ChatInput):
        return await service.chat(sid, cid, body)

    @api.get("/spaces/{sid}/events", tags=["Events"])
    def events(sid: str, at: Optional[datetime] = None, q: str = ""):
        return service.events(sid, at, q)

    @api.post("/spaces/{sid}/events", status_code=201, tags=["Events"])
    def create_event(sid: str, body: EventInput):
        return service.create_event(sid, body)

    @api.post("/spaces/{sid}/events/merge", tags=["Events"])
    def merge(sid: str, body: MergeInput):
        return service.merge(sid, body)

    @api.post("/spaces/{sid}/events/{eid}/split", tags=["Events"])
    def split(sid: str, eid: str, body: SplitInput):
        return service.split(sid, eid, body)

    @api.get("/spaces/{sid}/events/{eid}/fragments", tags=["Events"])
    def fragments(sid: str, eid: str):
        return service.fragments(sid, eid)

    @api.get("/spaces/{sid}/operations", tags=["Maintenance"])
    def operations(sid: str):
        return service.operations(sid)

    @api.get("/spaces/{sid}/events/{eid}", tags=["Events"])
    def get_event(sid: str, eid: str, at: Optional[datetime] = None):
        return service.get_event(sid, eid, at)

    @api.put("/spaces/{sid}/events/{eid}", tags=["Events"])
    def update_event(sid: str, eid: str, body: EventInput):
        return service.update_event(sid, eid, body)

    @api.delete("/spaces/{sid}/events/{eid}", tags=["Events"])
    def delete_event(sid: str, eid: str):
        return service.delete_event(sid, eid)

    @api.get("/spaces/{sid}/events/{eid}/versions", tags=["Events"])
    def versions(sid: str, eid: str):
        return service.versions(sid, eid)

    @api.post("/spaces/{sid}/events/{eid}/reinforce", tags=["Events"])
    def reinforce(sid: str, eid: str, body: ReinforceInput):
        return service.reinforce(sid, eid, body)

    @api.post("/spaces/{sid}/context", tags=["Memory"])
    async def context(sid: str, body: ContextInput, request: Request):
        return await cancellable_recall(request, recall(sid, body))

    @api.post("/spaces/{sid}/search", tags=["Memory"])
    async def search(sid: str, body: SearchInput, request: Request):
        return await cancellable_recall(request, recall(sid, body, search_only=True))

    @api.get("/spaces/{sid}/recalls", tags=["Memory"])
    def recalls(sid: str):
        return service.recalls(sid)

    @api.get("/spaces/{sid}/recalls/{rid}", tags=["Memory"])
    def recall_detail(sid: str, rid: str):
        return service.recall_detail(sid, rid)

    @api.put("/spaces/{sid}/recalls/{rid}/feedback", tags=["Memory"])
    def recall_feedback(sid: str, rid: str, body: RecallFeedback):
        return service.recall_feedback(sid, rid, body)

    @api.post("/spaces/{sid}/evaluations", tags=["Memory"])
    async def evaluate(sid: str, body: EvaluationInput):
        return await service.evaluate(sid, body)

    @api.post("/spaces/{sid}/maintenance", status_code=202, tags=["Maintenance"])
    async def maintenance_async(sid: str, body: MaintenanceInput):
        return await service.maintenance_async(sid, body)

    @api.get("/spaces/{sid}/jobs", tags=["Maintenance"])
    def jobs(sid: str, type: Optional[str] = None):
        return service.jobs(sid, type)

    @api.get("/spaces/{sid}/jobs/{job_id}", tags=["Maintenance"])
    def get_job(sid: str, job_id: str):
        return service.get_job(sid, job_id)

    @api.post("/spaces/{sid}/jobs/{job_id}/retry", status_code=202, tags=["Maintenance"])
    async def retry_job(sid: str, job_id: str):
        return await service.retry_job(sid, job_id)

    @api.get("/spaces/{sid}/stats", tags=["Memory"])
    def stats(sid: str, at: Optional[datetime] = None):
        return service.stats(sid, at)

    @api.get("/spaces/{sid}/export", tags=["Events"])
    def export(sid: str):
        cfg = config_for(sid)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("config.json", json.dumps(cfg, ensure_ascii=False, indent=2))
            for event in store.list("event", sid):
                metadata = {k: v for k, v in event.items() if k != "body"}
                archive.writestr("events/" + event["id"] + ".md", "---\n" + json.dumps(metadata, ensure_ascii=False, indent=2) + "\n---\n\n# " + event["title"] + "\n\n" + event["body"])
        return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="memory-slice.zip"'})

    app.include_router(api)
    frontend_dir = Path(os.environ.get("FRONTEND_DIST", str(Path(__file__).resolve().parents[2] / "frontend" / "dist")))
    if (frontend_dir / "assets").is_dir() and (frontend_dir / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "接口不存在")
            return FileResponse(frontend_dir / "index.html")
    return app


app = create_app(seed=os.environ.get("SEED_DEMO", "true").lower() == "true")
