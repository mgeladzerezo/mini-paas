import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests by route and status.", ["method", "route", "status"]
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request latency by route.", ["method", "route"]
)
RELEASES_FINISHED = Counter(
    "paas_releases_finished_total", "Releases that reached a final state.", ["kind", "outcome"]
)
RELEASE_DURATION = Histogram(
    "paas_release_duration_seconds",
    "Time from release creation to healthy.",
    buckets=(10, 30, 60, 120, 300, 600, 1200),
)
WEBHOOK_DELIVERIES = Counter(
    "paas_webhook_deliveries_total", "GitHub webhook deliveries by result.", ["event", "result"]
)
WORKER_TICK_ERRORS = Counter(
    "paas_worker_errors_total", "Unexpected errors while advancing a release."
)


def install_metrics(app: FastAPI) -> None:
    @app.middleware("http")
    async def record_request(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            # The route template, not the raw path, keeps label cardinality bounded.
            route = request.scope.get("route")
            label = getattr(route, "path", "unmatched")
            HTTP_REQUESTS.labels(request.method, label, str(status)).inc()
            HTTP_DURATION.labels(request.method, label).observe(time.perf_counter() - started)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
