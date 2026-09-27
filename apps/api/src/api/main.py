import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from agent.tracing import setup_tracing
from api.routes.chat import router as chat_router
from api.routes.datasets import router as datasets_router
from api.routes.transforms import router as transforms_router
from api.routes.users import router as users_router
from core.models import Base
from core.storage import get_engine, get_redis_client, get_s3_client

# No handler was ever configured for this app's own loggers - only uvicorn's
# own request-access line ("POST /chat/ask HTTP/1.1 200 OK") ever reached the
# terminal, which says nothing about what the agent actually did. Without
# this, `logging.getLogger(...).info(...)` calls anywhere in the app (agent
# routing/chart-spec decisions, per-attempt retries) are silently dropped -
# below WARNING, Python's logging module only has a bare last-resort
# handler - and even the unhandled-exception handler's own logger.error
# below would print unformatted, with no timestamp/logger name. This is
# root-level config, so it also covers every other module's logger
# (agent.qa, etc.) without each needing its own setup.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(get_engine())
    setup_tracing()
    yield


app = FastAPI(title="Data Agent API", lifespan=lifespan)
app.include_router(datasets_router)
app.include_router(chat_router)
app.include_router(transforms_router)
app.include_router(users_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Every route already catches the specific, expected exceptions it can
    raise (DatasetNotFound, PendingTransformNotFound, etc.) and turns them
    into a proper 4xx. This is the backstop for everything else - a genuinely
    unexpected bug anywhere in a request (a LangGraph node, a deterministic
    tool like chart_spec/chart_render, a DB/S3 call) would otherwise propagate
    all the way up as a raw 500 with no JSON body, which is exactly what the
    frontend can't render as a normal chat message. The full traceback still
    goes to the server log for debugging - only the response to the caller is
    softened, and only for this one unhandled-exception path; every route's
    existing specific except clauses are untouched and still run first.
    """
    logger.error("Unhandled exception on %s %s:\n%s", request.method, request.url.path, traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong processing that request. Please try again or rephrase it."},
    )


@app.get("/health")
def health() -> dict:
    status = {}

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        status["postgres"] = "up"
    except Exception as exc:
        status["postgres"] = f"error: {exc}"

    try:
        get_s3_client().list_buckets()
        status["minio"] = "up"
    except Exception as exc:
        status["minio"] = f"error: {exc}"

    try:
        get_redis_client().ping()
        status["redis"] = "up"
    except Exception as exc:
        status["redis"] = f"error: {exc}"

    return status
