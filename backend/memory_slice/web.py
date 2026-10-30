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

from .monitoring import CONTENT_TYPE_LATEST
from .web_middleware import MetricsMiddleware
from .schemas import (
    EmbeddingInput,
    ChatInput,
    ContextInput,
    ConversationInput,
    EventInput,
    MaintenanceInput,
    MemoryConfig,
    MessageInput,
    ModelInput,
    PersonInput,
    ReinforceInput,
    SpaceInput,
    MergeInput,
    SplitInput,
    RecallFeedback,
    EvaluationInput,
    SearchInput,
)


from .errors import MemoryError as HTTPException
from .service import MemoryService


async def cancellable_recall(request, operation):
    """Stop a superseded read-only model request when its HTTP client disconnects."""
    task = asyncio.create_task(operation)
    try:
        while not task.done():
            completed, _ = await asyncio.wait((task,), timeout=0.1)
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


def create_app(
    data_dir=None,
    seed=True,
    *,
    service=None,
    frontend_dir=None,
    host_mode="standalone",
    access_token=None,
    scope_info=None,
):
    benchmark_tasks = set()
    benchmark_jobs = {}
    owns_service = service is None
    if service is None:
        service = MemoryService(
            data_dir or os.environ.get("MEMORY_DATA_DIR", "data"), seed=seed
        )
    managed = host_mode == "astrbot"
    if managed and (not access_token or len(access_token) < 16):
        raise ValueError("插件管理接口必须配置至少 16 位访问令牌")
    store, metrics = service.store, service.metrics
    config_for, recall = service.config_for, service.recall

    @asynccontextmanager
    async def lifespan(app):
        if owns_service:
            await service.start()
        try:
            yield
        finally:
            for task in benchmark_tasks:
                task.cancel()
            await asyncio.gather(*benchmark_tasks, return_exceptions=True)
            if owns_service:
                await service.close()

    app = FastAPI(
        title="记忆切片 · Memory Slice API",
        version="0.7.0",
        description="以事件为核心的记忆服务。默认程序多路召回，可选显式大模型联想。分数为工程模型，不代表人类记忆概率。",
        lifespan=lifespan,
    )

    # Configuration validation must not reflect submitted keys into UI errors.
    from fastapi.exceptions import RequestValidationError
    from fastapi.exception_handlers import request_validation_exception_handler

    @app.exception_handler(RequestValidationError)
    async def safe_configuration_errors(request: Request, exc: RequestValidationError):
        if request.url.path.endswith(("/embedding", "/model")):
            return JSONResponse(status_code=422, content={"detail": [
                {key: error[key] for key in ("type", "loc", "msg") if key in error}
                for error in exc.errors()
            ]})
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(HTTPException)
    async def domain_error(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )

    app.state.store = store
    app.state.monitoring = metrics
    app.state.memory = service
    app.add_middleware(MetricsMiddleware, monitoring=metrics)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[]
        if managed
        else os.environ.get(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(","),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    bearer = HTTPBearer(auto_error=False)

    async def authenticate(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ):
        token = (
            access_token
            if access_token is not None
            else os.environ.get("MEMORY_API_TOKEN")
        )
        if token and (
            credentials is None
            or not secrets.compare_digest(
                credentials.credentials.encode(), token.encode()
            )
        ):
            raise HTTPException(
                401, "需要有效的服务访问令牌", headers={"WWW-Authenticate": "Bearer"}
            )

        if managed and request.method not in ("GET", "HEAD", "OPTIONS"):
            import re

            path = request.url.path.removeprefix("/api/v1")
            allowed = bool(
                re.fullmatch(
                    r"/spaces/[^/]+/(?:events(?:/[^/]+(?:/(?:split|reinforce))?)?|context|search|benchmarks|maintenance|jobs/[^/]+/retry|recalls/[^/]+/feedback)",
                    path,
                )
            )
            if not allowed:
                raise HTTPException(
                    403, "此操作由 AstrBot 管理；网页不创建模拟对话或修改插件配置"
                )
            if (path.endswith("/maintenance") or "/jobs/" in path) and scope_info:
                sid = path.split("/")[2]
                if not scope_info(sid).get("active", False):
                    raise HTTPException(
                        409, "此空间的关注配置或会话已停用，不能提交整理"
                    )

    api = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])

    async def metrics_auth(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ):
        token = (
            access_token
            if access_token is not None
            else (
                os.environ.get("MEMORY_METRICS_TOKEN")
                or os.environ.get("MEMORY_API_TOKEN")
            )
        )
        if token and (
            credentials is None
            or not secrets.compare_digest(
                credentials.credentials.encode(), token.encode()
            )
        ):
            raise HTTPException(
                401, "需要有效的监控访问令牌", headers={"WWW-Authenticate": "Bearer"}
            )

    @app.get("/metrics", tags=["Monitoring"], dependencies=[Depends(metrics_auth)])
    def prometheus_metrics():
        return Response(
            metrics.prometheus(), headers={"Content-Type": CONTENT_TYPE_LATEST}
        )

    @api.get("/benchmarks", tags=["Evaluation"])
    def benchmark_list():
        return list(benchmark_jobs.values())

    @api.post("/spaces/{sid}/benchmarks", tags=["Evaluation"])
    async def start_benchmark(sid: str, mode: str = "local"):
        import tempfile
        from types import SimpleNamespace
        from .benchmark import run
        from .store import new_id, iso
        service.config_for(sid)
        if mode not in ("local", "both"):
            raise HTTPException(422, "请选择 local 或 both")
        if benchmark_tasks:
            raise HTTPException(409, "已有测试正在运行，请等待完成")
        embedding_config = None
        if mode == "both":
            embedding_config = service.embeddings.settings(sid)
            if not embedding_config["enabled"] or not embedding_config["base_url"] or not embedding_config["model"]:
                raise HTTPException(422, "请先在当前空间配置并启用 Embedding")
            embedding_config["api_key"] = service.embeddings.keys.get(sid) or os.environ.get("EMBEDDING_API_KEY")
        jid = new_id("benchmark")
        job = dict(id=jid, space_id=sid, mode=mode, status="running", phase="preparing", completed=0,
                   total=28*(2 if mode == "both" else 1), created_at=iso(), report=None, error=None)
        benchmark_jobs[jid] = job
        while len(benchmark_jobs) > 20:
            del benchmark_jobs[next(iter(benchmark_jobs))]

        async def execute():
            try:
                with tempfile.TemporaryDirectory(prefix="memory-benchmark-report-") as directory:
                    output = Path(directory) / "report.json"
                    args = SimpleNamespace(mode=mode, k=5, timeout=1.5, compare=None, output=str(output))
                    await run(args, embedding_config=embedding_config, progress=job.update, quiet=True)
                    report = json.loads(output.read_text())
                    # Only compare same dataset/config to a known baseline, never silently bless a new result.
                    from .benchmark import dataset_directory
                    baseline_file = dataset_directory() / "baselines/local-v1.json"
                    baseline = json.loads(baseline_file.read_text()) if baseline_file.exists() else None
                    if baseline:
                        baseline.get("config", {}).setdefault("maintenance_pending_token_limit", 4000)
                    compatible = baseline and all(baseline.get(k) == report.get(k) for k in ("dataset_sha256", "config", "k"))
                    report["baseline"] = baseline["summaries"]["local"] if compatible else None
                    previous = {r["id"]: r for r in baseline["results"]} if compatible else {}
                    for row in report["results"]:
                        old = previous.get(row["id"]) if row["mode"] == "local" else None
                        row["regressed"] = bool(old and (set(old["hits"]) - set(row["hits"]) or row["reciprocal_rank"] < old["reciprocal_rank"] or (not old["selected"] and not row["expected"] and row["selected"]) or set(row["forbidden_hits"]) - set(old["forbidden_hits"])))
                    job.update(status="completed", phase="completed", report=report)
            except asyncio.CancelledError:
                job.update(status="cancelled", error="服务关闭，测试已取消")
                raise
            except Exception as exc:
                job.update(status="failed", error=f"测试未完成（{type(exc).__name__}）。请检查 Embedding 连接、密钥或稍后重试；日常记忆未改变。")
        task = asyncio.create_task(execute())
        benchmark_tasks.add(task)
        task.add_done_callback(benchmark_tasks.discard)
        return job

    @api.get("/monitoring", tags=["Monitoring"])
    def monitoring_json():
        return metrics.snapshot()

    @app.get("/api/health", tags=["Health"])
    def health():
        return {"status": "ok", "version": "0.7.0"}

    @api.get("/runtime", tags=["Configuration"])
    def runtime_info():
        return {
            "mode": host_mode,
            "managed_configuration": managed,
            "simulator_enabled": not managed,
        }

    @api.get("/spaces", tags=["Spaces"])
    def spaces():
        return [
            dict(space, host=scope_info(space["id"])) if scope_info else space
            for space in service.spaces()
        ]

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
        result = service.get_model(sid)
        if managed:
            result.pop("astrbot_umo", None)
            result.update(base_url="", has_key=False, key_source="astrbot")
        return result

    @api.put("/spaces/{sid}/model", tags=["Configuration"])
    def put_model(sid: str, body: ModelInput):
        return service.put_model(sid, body)

    @api.post("/spaces/{sid}/model/test", tags=["Configuration"])
    async def test_model(sid: str):
        return await service.test_model(sid)

    @api.get("/spaces/{sid}/embedding", tags=["Configuration"])
    async def embedding_status(sid: str):
        service.config_for(sid)
        return service.embeddings.status(sid)

    @api.put("/spaces/{sid}/embedding", tags=["Configuration"])
    async def embedding_config(sid: str, body: EmbeddingInput):
        service.config_for(sid)
        return service.embeddings.configure(sid, body)

    @api.post("/spaces/{sid}/embedding/test", tags=["Configuration"])
    async def embedding_test(sid: str):
        service.config_for(sid)
        from .embedding import Unavailable
        try:
            await service.embeddings.request(sid, ["connection test"], "test")
            return {"ok": True}
        except Unavailable as exc:
            return {"ok": False, "reason": str(exc)}

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

    @api.post(
        "/spaces/{sid}/conversations/{cid}/messages",
        status_code=201,
        tags=["Conversations"],
    )
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

    @api.post(
        "/spaces/{sid}/jobs/{job_id}/retry", status_code=202, tags=["Maintenance"]
    )
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
            archive.writestr(
                "config.json", json.dumps(cfg, ensure_ascii=False, indent=2)
            )
            for event in store.list("event", sid):
                metadata = {k: v for k, v in event.items() if k != "body"}
                archive.writestr(
                    "events/" + event["id"] + ".md",
                    "---\n"
                    + json.dumps(metadata, ensure_ascii=False, indent=2)
                    + "\n---\n\n# "
                    + event["title"]
                    + "\n\n"
                    + event["body"],
                )
        return Response(
            output.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="memory-slice.zip"'},
        )

    app.include_router(api)
    frontend_dir = Path(
        frontend_dir
        or os.environ.get(
            "FRONTEND_DIST",
            str(Path(__file__).resolve().parents[2] / "frontend" / "dist"),
        )
    )
    if (frontend_dir / "assets").is_dir() and (frontend_dir / "index.html").is_file():
        app.mount(
            "/assets",
            StaticFiles(directory=str(frontend_dir / "assets")),
            name="assets",
        )

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "接口不存在")
            return FileResponse(frontend_dir / "index.html")

    return app
