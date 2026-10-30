"""student_key computation for joining ``ml_predictions`` to real students.

Formula (source of truth: ml-data/docs/M5_PREDICTION_BATCH.md §2 —
mirrored in DB_CONTRACT.md v1.0 rev2 §9.1):

    student_key = sha256(
        "learnguard:prediction-batch.v1:{code_module}:{code_presentation}:{id_student}"
    ).hexdigest()[:16]

The backend keeps its own student list (id_student + module/presentation)
and computes keys on the fly to join — NO mapping is ever stored in the DB.

Test vectors (must always pass):
    ("AAA", "2013B", 1)      -> "4c337e13fc7b6871"
    ("AAA", "2013J", 11391)  -> "7bd60aced1ccab8c"

Changing the prefix or the key length is a BREAKING change: the pipeline
must bump ``schema_version`` and notify the backend.
"""

import hashlib

KEY_LENGTH = 16
_KEY_PREFIX = "learnguard:prediction-batch.v1"

TEST_VECTORS: tuple[tuple[str, str, int, str], ...] = (
    ("AAA", "2013B", 1, "4c337e13fc7b6871"),
    ("AAA", "2013J", 11391, "7bd60aced1ccab8c"),
)


def pseudonymous_key(
    code_module: str, code_presentation: str, id_student: int
) -> str:
    """Compute the pseudonymous student key for one student–module pair."""
    material = (
        f"{_KEY_PREFIX}:{code_module}:{code_presentation}:{id_student}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:KEY_LENGTH]


def keys_for_students(
    students: list[tuple[str, str, int]],
) -> dict[tuple[str, str, int], str]:
    """Batch-compute keys for (code_module, code_presentation, id_student) tuples."""
    return {
        (module, presentation, student_id): pseudonymous_key(
            module, presentation, student_id
        )
        for module, presentation, student_id in students
    }
