"""HTTP-only metrics adapter."""

import time


class MetricsMiddleware:
    def __init__(self, app, monitoring):
        self.app, self.monitoring = app, monitoring

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in (
            "/metrics",
            "/api/v1/monitoring",
        ):
            return await self.app(scope, receive, send)
        started, status = time.perf_counter(), 500
        self.monitoring.inflight.inc()

        async def observed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            # The React catch-all also serves unknown URLs; use its template.
            method = scope.get("method", "OTHER")
            if method not in (
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "HEAD",
                "OPTIONS",
            ):
                method = "OTHER"
            self.monitoring.http.labels(method, route, str(status)).inc()
            self.monitoring.http_seconds.labels(method, route).observe(
                time.perf_counter() - started
            )
            self.monitoring.inflight.dec()
