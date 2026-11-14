"""Package-local job record publication with the existing .tmp/replace policy."""
import json
from pathlib import Path


def save_job_record(path: Path, record: object) -> None:
    """Write default JSON and replace; preserve the saved record on failure."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record))
    temporary.replace(path)
