"""Backend ingestion boundary for completed M5 prediction batch artifacts."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class BatchImportReport:
    run_id: str
    model_version: str
    snapshot_version: str
    inserted_rows: int
    skipped_rows: int
    status: str


class PredictionBatchImportService:
    def import_manifest(self, manifest_path: Path) -> BatchImportReport:
        """TODO: validate v1 manifest/checksum and persist prediction records."""
        raise NotImplementedError

    def validate_schema_version(self, schema_version: str) -> None:
        """TODO: reject unsupported batch contract versions."""
        raise NotImplementedError
