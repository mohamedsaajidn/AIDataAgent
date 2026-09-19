import boto3
import redis
from sqlalchemy import Engine, create_engine

from core.settings import get_settings


def get_engine() -> Engine:
    return create_engine(get_settings().postgres_dsn, pool_pre_ping=True)


def get_s3_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def get_redis_client() -> redis.Redis:
    return redis.from_url(get_settings().redis_url)
