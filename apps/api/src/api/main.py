from fastapi import FastAPI
from sqlalchemy import text

from core.storage import get_engine, get_redis_client, get_s3_client

app = FastAPI(title="Data Agent API")


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
