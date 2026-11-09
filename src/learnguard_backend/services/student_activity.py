"""Raw OULAD activity statistics for one student (Student Profile cards).

Reads the raw OULAD CSV dataset (studentInfo / studentVle / assessments /
studentAssessment / vle) directly with the csv stdlib — the backend
deliberately has no pandas dependency.

``studentVle.csv`` is ~450MB / 10.6M rows (one row per student–site–day
use), so a full ``csv.DictReader`` pass per request takes ~35s. Instead the
file is memory-mapped, the (code_module, code_presentation) byte block is
located by binary search (the archive ships rows grouped by module +
presentation), and the student's line prefix
``"module","presentation","id_student",`` is matched with ``bytes.find``
(memchr speed) — only the few matching rows are CSV-parsed: well under a
second per request. A full-file fallback scan keeps the result correct if
the block layout ever changes.

The day window matches the ml-data pipeline feature window: the cutoff is
day 27 (end of week 4, inclusive — ``ml-data/src/learnguard/settings.py``
``CUTOFF_DAY``). Weeks buckets:

    - pre-start:   day -14 .. -1   (label "Trước khóa")
    - week 1..4:   day 0-6 / 7-13 / 14-20 / 21-27
"""

import csv
import mmap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from learnguard_backend.services.student_keys import pseudonymous_key

# End of week 4, inclusive — MUST match ml-data settings.CUTOFF_DAY
# (ml-data/src/learnguard/settings.py) so this view shows exactly the
# window the model features were built on.
STUDENT_ACTIVITY_CUTOFF_DAY = 27

# (label, from_day, to_day) — pre-start only counts day >= -14.
_ACTIVITY_WEEK_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("Trước khóa", -14, -1),
    ("Tuần 1", 0, 6),
    ("Tuần 2", 7, 13),
    ("Tuần 3", 14, 20),
    ("Tuần 4", 21, STUDENT_ACTIVITY_CUTOFF_DAY),
)

_ACTIVITY_BREAKDOWN_TOP = 6
_PRE_START_MIN_DAY = -14


def _parse_int(raw: str | None) -> int | None:
    """Parse an int CSV cell; blank/garbage -> ``None`` (never raises)."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


@contextmanager
def _iter_csv(path: Path):
    """Yield a ``csv.DictReader`` for ``path`` (file closed on exit)."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        yield csv.DictReader(handle)


def _line_at(view: "mmap.mmap", offset: int) -> tuple[int, int]:
    """Byte range [start, end) of the CSV line containing ``offset``."""
    if offset <= 0:
        line_start = 0
    else:
        newline = view.rfind(b"\n", 0, offset)
        line_start = 0 if newline == -1 else newline + 1
    line_end = view.find(b"\n", line_start)
    return line_start, len(view) if line_end == -1 else line_end


def _first_line_offset(
    view: "mmap.mmap", predicate, lo: int = 0
) -> int:
    """Smallest line-start offset >= lo whose line satisfies ``predicate``."""
    hi = len(view)
    while lo < hi:
        mid = (lo + hi) // 2
        line_start, line_end = _line_at(view, mid)
        if predicate(view[line_start:line_end]):
            hi = line_start
        else:
            lo = line_end + 1
    return lo


def _presentation_block(
    view: "mmap.mmap", code_module: str, code_presentation: str
) -> tuple[int, int] | None:
    """Byte range [start, end) of one presentation's rows in studentVle.csv.

    The OULAD archive ships studentVle.csv grouped (contiguous) by
    code_module, code_presentation — verified over the full dataset — so a
    binary search finds the block and only ~20MB instead of the whole ~450MB
    needs scanning. Returns ``None`` (-> full-file fallback scan) when the
    layout does not hold for this file.
    """
    prefix = f'"{code_module}","{code_presentation}",'.encode()
    # Skip the header line: it starts with lowercase "code_module" and thus
    # sorts AFTER the uppercase data lines, breaking the binary search's
    # monotonicity if included.
    header_newline = view.find(b"\n")
    lo = 0 if header_newline == -1 else header_newline + 1
    start = _first_line_offset(view, lambda line: line >= prefix, lo=lo)
    if start >= len(view) or view[start : start + len(prefix)] != prefix:
        return None
    end = _first_line_offset(
        view, lambda line: not line.startswith(prefix), lo=start
    )
    return start, end


def _scan_prefix_rows(
    view: "mmap.mmap", prefix: bytes, lo: int, hi: int
) -> Iterator[list[str]]:
    """Yield CSV rows of lines starting with ``prefix`` within [lo, hi)."""
    start = lo
    while start < hi:
        pos = view.find(prefix, start, hi)
        if pos == -1:
            return
        if pos == 0 or view[pos - 1] == 10:  # 10 == b"\n" (line-start guard)
            end = view.find(b"\n", pos, hi)
            stop = end if end != -1 else hi
            line = view[pos:stop].rstrip(b"\r")  # CRLF archives
            yield next(csv.reader([line.decode("utf-8")]))
            start = stop + 1 if end != -1 else hi
        else:
            start = pos + 1


def _iter_vle_rows_for_student(
    path: Path, code_module: str, code_presentation: str, id_student: int
) -> Iterator[list[str]]:
    """Yield the CSV rows of one student from the huge ``studentVle.csv``.

    Memory-maps the file, binary-searches the (code_module,
    code_presentation) byte block, and matches the line prefix
    ``'"module","presentation","id_student",'`` with ``bytes.find`` (the
    line prefix uniquely identifies the student's rows; a line-start guard
    rejects any mid-line coincidence). Only matching lines are copied and
    CSV-parsed. Falls back to a full-file scan when the presentation block
    cannot be located.
    """
    prefix = f'"{code_module}","{code_presentation}","{id_student}",'.encode()
    with path.open("rb") as handle:
        if path.stat().st_size == 0:
            return
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view:
            block = _presentation_block(view, code_module, code_presentation)
            ranges = [block] if block is not None else [(0, len(view))]
            for lo, hi in ranges:
                yield from _scan_prefix_rows(view, prefix, lo, hi)


def resolve_student_id(
    data_dir: Path,
    code_module: str,
    code_presentation: str,
    student_key: str,
) -> int | None:
    """Reverse-map a pseudonymous student_key to the raw ``id_student``.

    student_key is one-way sha256 (see ``student_keys.py``), so the only
    mapping is brute-force: hash every id_student enrolled in this
    (code_module, code_presentation) and compare. Returns ``None`` when no
    student in the presentation matches (-> 404 student_not_found).
    """
    path = data_dir / "studentInfo.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None) or []
        try:
            i_module = header.index("code_module")
            i_presentation = header.index("code_presentation")
            i_student = header.index("id_student")
        except ValueError:
            return None
        widest = max(i_module, i_presentation, i_student)
        for row in reader:
            if len(row) <= widest:
                continue
            if (
                row[i_module] != code_module
                or row[i_presentation] != code_presentation
            ):
                continue
            id_student = _parse_int(row[i_student])
            if id_student is None:
                continue
            if (
                pseudonymous_key(code_module, code_presentation, id_student)
                == student_key
            ):
                return id_student
    return None


def build_student_activity(
    data_dir: Path,
    code_module: str,
    code_presentation: str,
    id_student: int,
) -> dict[str, object]:
    """Build the /activity payload for one student from the raw CSVs."""
    cutoff = STUDENT_ACTIVITY_CUTOFF_DAY

    # --- vle.csv: id_site -> activity_type (forum/resource/quiz/...) for
    # this presentation, used to categorise clicks.
    site_types: dict[str, str] = {}
    with _iter_csv(data_dir / "vle.csv") as reader:
        for row in reader:
            if (
                row.get("code_module") != code_module
                or row.get("code_presentation") != code_presentation
            ):
                continue
            id_site = (row.get("id_site") or "").strip()
            activity_type = (row.get("activity_type") or "").strip()
            if id_site and activity_type:
                site_types[id_site] = activity_type

    # --- studentVle.csv: one row PER USE of a site (sum_click accumulates).
    # Fast byte-prefix scan — the file is ~450MB / 10.6M rows.
    clicks_by_day: dict[int, int] = {}
    clicks_by_type: dict[str, int] = {}
    vle_path = data_dir / "studentVle.csv"
    for row in _iter_vle_rows_for_student(
        vle_path, code_module, code_presentation, id_student
    ):
        # Row: [code_module, code_presentation, id_student, id_site, date,
        # sum_click] — module/presentation/id_student already matched by the
        # line prefix.
        if len(row) < 6:
            continue
        day = _parse_int(row[4])
        clicks = _parse_int(row[5])
        if day is None or clicks is None or clicks <= 0:
            continue
        if day < _PRE_START_MIN_DAY or day > cutoff:
            continue
        clicks_by_day[day] = clicks_by_day.get(day, 0) + clicks
        category = site_types.get(row[3].strip())
        if category:
            clicks_by_type[category] = clicks_by_type.get(category, 0) + clicks

    weeks: list[dict[str, object]] = []
    for label, from_day, to_day in _ACTIVITY_WEEK_BUCKETS:
        bucket = {
            day: clicks
            for day, clicks in clicks_by_day.items()
            if from_day <= day <= to_day
        }
        weeks.append(
            {
                "label": label,
                "from_day": from_day,
                "to_day": to_day,
                "clicks": sum(bucket.values()),
                # days_active = DISTINCT days with sum_click > 0 in bucket.
                "days_active": sum(1 for clicks in bucket.values() if clicks > 0),
            }
        )

    # --- studentAssessment.csv: this student's submissions (score may be
    # blank -> None). Keyed by id_assessment; assessments are per-presentation
    # so cross-module rows in this dict are never looked up. csv.reader with
    # index access — DictReader costs ~0.8s over the 174k-row file.
    submissions_by_assessment: dict[str, tuple[int | None, int | None]] = {}
    with (data_dir / "studentAssessment.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        reader = csv.reader(handle)
        header = next(reader, None) or []
        try:
            i_assessment = header.index("id_assessment")
            i_student = header.index("id_student")
            i_submitted = header.index("date_submitted")
            i_score = header.index("score")
        except ValueError:
            i_assessment = i_student = i_submitted = i_score = -1
        if i_assessment >= 0 and i_student >= 0:
            widest = max(i_assessment, i_student, i_submitted, i_score)
            for row in reader:
                if len(row) <= widest or _parse_int(row[i_student]) != id_student:
                    continue
                submissions_by_assessment[row[i_assessment].strip()] = (
                    _parse_int(row[i_submitted]) if i_submitted >= 0 else None,
                    _parse_int(row[i_score]) if i_score >= 0 else None,
                )

    # --- assessments.csv: only assessments due on/before the cutoff;
    # a missing/blank date (end-of-term exam) is skipped entirely.
    submission_rows: list[dict[str, object]] = []
    with _iter_csv(data_dir / "assessments.csv") as reader:
        for row in reader:
            if (
                row.get("code_module") != code_module
                or row.get("code_presentation") != code_presentation
            ):
                continue
            assessment_day = _parse_int(row.get("date"))
            if assessment_day is None or assessment_day > cutoff:
                continue
            submitted = submissions_by_assessment.get(
                (row.get("id_assessment") or "").strip()
            )
            submitted_day, score = submitted if submitted is not None else (None, None)
            submission_rows.append(
                {
                    "assessment_type": (row.get("assessment_type") or "").strip(),
                    "assessment_day": assessment_day,
                    "submitted_day": submitted_day,
                    "score": score,
                    "weight": _parse_int(row.get("weight")),
                    "status": "submitted" if submitted is not None else "not_submitted",
                }
            )
    submission_rows.sort(key=lambda item: item["assessment_day"])

    breakdown = [
        {"category": category, "clicks": clicks}
        for category, clicks in sorted(
            (item for item in clicks_by_type.items() if item[1] > 0),
            key=lambda item: item[1],
            reverse=True,
        )[:_ACTIVITY_BREAKDOWN_TOP]
    ]

    return {
        "cutoff_day": cutoff,
        "weeks": weeks,
        "submissions": submission_rows,
        "activity_breakdown": breakdown,
    }
