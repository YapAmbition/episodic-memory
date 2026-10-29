import json
import re
import asyncio
import time
from contextlib import asynccontextmanager

import httpx
from .errors import MemoryError as HTTPException


class ContextLimitError(HTTPException):
    def __init__(self):
        super().__init__(413, "模型明确报告上下文超限")


class OutputLimitError(HTTPException):
    def __init__(self):
        super().__init__(502, "模型输出达到上限，整理结果未提交")


def is_context_limit(response):
    if response.status_code not in (400, 413, 422):
        return False
    try:
        error = response.json().get("error", {})
        if not isinstance(error, dict):
            return False
        code = str(error.get("code", "")).lower()
        message = str(error.get("message", "")).lower()
        return code in {"context_length_exceeded", "context_window_exceeded", "prompt_too_long", "input_too_long"} or bool(re.search(
            r"maximum context length|context (?:length|window).*(?:exceed|too (?:long|large))|输入.*(?:超过|超出).*(?:上下文|长度)|上下文.*(?:超限|超出)", message))
    except (ValueError, AttributeError):
        return False


@asynccontextmanager
async def model_slot(metrics, purpose="other"):
    if metrics is None:
        yield
    else:
        started = time.perf_counter()
        async with metrics.model_slots:
            metrics.model_queue_seconds.labels(purpose).observe(time.perf_counter() - started)
            yield


async def complete(settings, api_key, messages, json_mode=False, purpose="other", metrics=None, max_tokens=None):
    if not settings.get("model"):
        raise HTTPException(422, "请先在模型连接中填写模型名称")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    payload = {"model": settings["model"], "messages": messages, "stream": False}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    started, outcome = time.perf_counter(), "error"
    try:
        async with model_slot(metrics, purpose):
            async with httpx.AsyncClient(timeout=settings.get("timeout_seconds", 60), follow_redirects=False) as client:
                for attempt in range(settings.get("max_retries", 2) + 1):
                    retryable, error = False, None
                    try:
                        response = await client.post(settings["base_url"].rstrip("/") + "/chat/completions", headers=headers, json=payload)
                        if response.status_code >= 400:
                            if is_context_limit(response):
                                outcome = "context_limit"
                                raise ContextLimitError()
                            retryable = response.status_code in (429, 500, 502, 503, 504)
                            outcome = "http_error"
                            error = HTTPException(502, f"模型服务返回 HTTP {response.status_code}，请检查连接配置")
                        else:
                            data = response.json()
                            reported = False
                            if metrics:
                                usage = data.get("usage") or {}
                                if isinstance(usage, dict):
                                    for field, direction in (("prompt_tokens", "input"), ("completion_tokens", "output")):
                                        value = usage.get(field)
                                        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                                            metrics.tokens.labels(purpose, direction).inc(value)
                                            reported = True
                            if data["choices"][0].get("finish_reason") == "length":
                                outcome = "output_limit"
                                raise OutputLimitError()
                            message = data["choices"][0]["message"]
                            content = message.get("content")
                            if message.get("refusal") or not isinstance(content, str) or not content.strip():
                                raise ValueError("empty or refused")
                            outcome = "success"
                            if metrics and not reported:
                                metrics.usage_missing.labels(purpose).inc()
                            return content
                    except httpx.TimeoutException:
                        outcome, retryable = "timeout", True
                        error = HTTPException(504, "模型服务响应超时，请稍后重试")
                    except httpx.HTTPError:
                        outcome, retryable = "network_error", True
                        error = HTTPException(502, "模型连接失败，请检查模型连接配置")
                    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                        outcome = "invalid_response"
                        error = HTTPException(502, "模型返回空内容、拒绝或不兼容的响应格式")
                    finally:
                        if metrics:
                            metrics.model_attempts.labels(purpose, outcome).inc()
                    if not retryable or attempt >= settings.get("max_retries", 2):
                        raise error
                    await asyncio.sleep(min(2, .25 * 2 ** attempt))
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        if metrics:
            metrics.model_calls.labels(purpose, outcome).inc()
            metrics.model_seconds.labels(purpose).observe(time.perf_counter() - started)


def parse_json(content):
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        return json.loads(content)
    except ValueError:
        raise HTTPException(502, "模型未返回有效 JSON，记忆库未作更改")
