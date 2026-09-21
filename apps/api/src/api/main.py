from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from api.routes.chat import router as chat_router
from api.routes.datasets import router as datasets_router
from api.routes.transforms import router as transforms_router
from core.models import Base
from core.storage import get_engine, get_redis_client, get_s3_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(get_engine())
    yield


app = FastAPI(title="Data Agent API", lifespan=lifespan)
app.include_router(datasets_router)
app.include_router(chat_router)
app.include_router(transforms_router)


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
