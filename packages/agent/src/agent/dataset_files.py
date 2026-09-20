import tempfile
from pathlib import Path

from core.settings import get_settings
from core.storage import get_s3_client

from agent.schema_context import get_active_dataset


def download_active_version(dataset_id: str) -> tuple[str, Path]:
    """Pulls the dataset's active-version parquet fresh from object storage.

    Returns (dataset_name, local_path). The local filename is arbitrary - the
    sandbox executor renames by dict key, not by the source file's name.
    """
    dataset, version = get_active_dataset(dataset_id)
    settings = get_settings()
    s3 = get_s3_client()

    tmp_dir = Path(tempfile.mkdtemp(prefix="qa-dataset-"))
    local_path = tmp_dir / "data.parquet"
    s3.download_file(settings.s3_bucket, version.parquet_key, str(local_path))
    return dataset.name, local_path
