"""Use the host's providers; never open a separate model connection."""

import asyncio
import time
from .memory_slice.errors import MemoryError
from .memory_slice.model import (
    model_slot,
    ContextLimitError,
    OutputLimitError,
    is_context_limit,
)


def field(obj, name, default=None):
    return (
        obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
    )


class AstrBotModelAdapter:
    def __init__(self, context, provider_id=""):
        self.context, self.provider_id = context, provider_id

    async def complete(
        self,
        settings,
        api_key,
        messages,
        json_mode=False,
        purpose="other",
        metrics=None,
        max_tokens=None,
    ):
        started, outcome = time.perf_counter(), "error"
        try:
            async with model_slot(metrics, purpose):

                async def invoke():
                    provider = (
                        self.provider_id
                        or await self.context.get_current_chat_provider_id(
                            settings["astrbot_umo"]
                        )
                    )
                    if not provider:
                        raise MemoryError(422, "当前 AstrBot 会话没有聊天模型")
                    # Core calls contain a system prompt followed by one user payload.
                    if any(m["role"] not in ("system", "user") for m in messages):
                        raise MemoryError(422, "记忆模型请求包含不支持的角色")
                    system = "\n\n".join(
                        m["content"] for m in messages if m["role"] == "system"
                    )
                    if json_mode:
                        system += "\n只返回完整的 JSON 对象，不要 Markdown 围栏。"
                    kwargs = (
                        {"max_tokens": max_tokens} if max_tokens is not None else {}
                    )
                    return await self.context.llm_generate(
                        chat_provider_id=provider,
                        system_prompt=system,
                        prompt="\n\n".join(
                            m["content"] for m in messages if m["role"] == "user"
                        ),
                        **kwargs,
                    )

                response = await asyncio.wait_for(
                    invoke(), settings.get("timeout_seconds", 60)
                )
                raw = field(response, "raw_completion")
                choices = field(raw, "choices", []) or []
                reason = (
                    field(choices[0], "finish_reason")
                    if choices
                    else field(raw, "stop_reason")
                )
                if (
                    reason in ("length", "max_tokens")
                    or field(raw, "status") == "incomplete"
                ):
                    outcome = "output_limit"
                    raise OutputLimitError()
                text = field(response, "completion_text", "")
                if (
                    field(response, "role") == "err"
                    or not isinstance(text, str)
                    or not text.strip()
                ):
                    raise MemoryError(502, "AstrBot 模型未返回有效文本")
                usage = field(response, "usage")
                reported = False
                if metrics and usage is not None:
                    for name, direction in (("input", "input"), ("output", "output")):
                        value = field(usage, name)
                        if (
                            isinstance(value, int)
                            and not isinstance(value, bool)
                            and value >= 0
                        ):
                            metrics.tokens.labels(purpose, direction).inc(value)
                            reported = True
                if metrics and not reported:
                    metrics.usage_missing.labels(purpose).inc()
                outcome = "ok"
                return text
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except asyncio.TimeoutError:
            outcome = "timeout"
            raise MemoryError(504, "AstrBot 记忆模型请求超时") from None
        except MemoryError:
            raise
        except Exception as exc:
            response = getattr(exc, "response", None)
            if response is not None and is_context_limit(response):
                outcome = "context_limit"
                raise ContextLimitError() from None
            # Provider exceptions can contain prompts and API keys. Do not persist them.
            raise MemoryError(
                502, "AstrBot 模型调用失败（%s）" % type(exc).__name__
            ) from None
        finally:
            if metrics:
                metrics.model_calls.labels(purpose, outcome).inc()
                metrics.model_attempts.labels(purpose, outcome).inc()
                metrics.model_seconds.labels(purpose).observe(
                    time.perf_counter() - started
                )
