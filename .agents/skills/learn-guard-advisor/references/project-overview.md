# LearnGuard Backend — Project Overview (Reference)

> Tổng hợp từ README.md, pyproject.toml, settings.py, và codebase

---

## 1. Tech Stack

| Thành phần | Công nghệ | Version |
|-----------|-----------|---------|
| Framework | FastAPI | >= 0.115 |
| ASGI server | Uvicorn | >= 0.30 |
| ORM | SQLAlchemy 2 (declarative) | >= 2 |
| Migration | Alembic | — |
| Database | PostgreSQL | >= 15 (demo: 16.15) |
| DB driver | psycopg (binary) | >= 3.2 |
| Validation | Pydantic 2 | >= 2.9 |
| Auth | python-jose (JWT) + passlib (bcrypt) | — |
| LLM client | LiteLLM | >= 1.63 |
| Retry | tenacity | >= 8 |
| Config | python-dotenv + env vars | — |

## 2. Project Structure

```
BE-MLOPS/
├── src/learnguard_backend/
│   ├── main.py                    # ASGI entrypoint
│   ├── settings.py                # Runtime config from env
│   ├── observability.py           # Audit, metrics, run summary
│   ├── __init__.py
│   ├── api/
│   │   ├── app.py                 # App factory + all controllers (~1800 lines)
│   │   ├── auth.py                # JWT middleware
│   │   ├── eval_runner.py         # Evaluation trigger endpoint
│   │   └── schemas.py             # Request/response DTOs
│   ├── db/
│   │   ├── entities.py            # SQLAlchemy models (all tables)
│   │   ├── models.py              # Dataclass DTOs for services
│   │   ├── repository.py          # Repository pattern (CRUD)
│   │   └── session.py             # Session factory
│   └── services/
│       ├── briefs.py              # M8 brief generation + citation validation
│       ├── chat.py                # Advisor Copilot Chatbot
│       ├── chat_context.py        # Chat context builder
│       ├── llm.py                 # Shared LiteLLM client
│       ├── student_activity.py    # OULAD student activity
│       ├── student_keys.py        # SHA-256 key computation
│       └── advisor_guidelines.md  # Guidelines cho LLM system prompt
├── docs/
│   └── DB_CONTRACT.md             # Database contract (source of truth)
├── tests/
├── migrations/                    # Alembic migrations
├── benchmarks/
├── pyproject.toml
├── alembic.ini
├── compose.ollama.yml
└── .env.example
```

## 3. Boundaries (Ranh giới hệ thống)

- Backend phục vụ **advisor dashboard** — không có endpoint cho sinh viên.
- M4/M5 batch ML và M1-M3 data engineering thuộc repo `project`/`ml-data`.
- Backend đọc prediction từ shared PostgreSQL; không import code Python từ repo ML.
- M8 LLM chạy trong tiến trình backend; LLM chỉ nhận context tối thiểu.
- Brief luôn qua advisor review trước khi hành động.

## 4. LLM Configuration

### Environment variables chính:

| Variable | Default | Mô tả |
|----------|---------|-------|
| `LLM_API_KEY` | — | OpenRouter API key |
| `LLM_MODEL` | `gemini-3.8-flash` | Model chính |
| `LLM_API_BASE` | — | API endpoint |
| `LLM_FALLBACK_MODEL` | — | Model fallback (Ollama) |
| `LLM_TIMEOUT_SECONDS` | 30 | Timeout primary |
| `LLM_FALLBACK_TIMEOUT_SECONDS` | 120 | Timeout fallback |
| `LLM_MAX_INPUT_TOKENS` | 8000 | Giới hạn input |
| `LLM_MAX_OUTPUT_TOKENS` | 1000 | Giới hạn output |
| `LLM_MAX_RETRIES` | 2 | Retry count |
| `CHAT_HISTORY_WINDOW_PAIRS` | 3 | Chat history window |

### Sử dụng LLM service:

```python
from learnguard_backend.services.llm import get_llm_service

llm = get_llm_service()
result = llm.complete(
    [{"role": "user", "content": "..."}],
    temperature=0.2,
)
# Async: result = await llm.acomplete(messages, temperature=0.2)
```

- Factory singleton — khởi tạo 1 lần, tái sử dụng.
- Failover: primary lỗi → fallback (nếu configured) → `LLMFailoverError`.
- Không cấu hình fallback → lỗi primary truyền thẳng.

## 5. Brief Generation Flow

```
ml_predictions row → build_context() → PII-free JSON
  → LLM (system prompt + data)
  → _parse_draft() → validate_citations()
  → Nếu có ungrounded citations → gỡ + ghi vào limitations
  → BriefGenerationResult (generated | fallback_review | unavailable)
```

**Quy tắc:**
- LLM chỉ nhận student_key (hash), không bao giờ nhận id_student.
- Mỗi reason phải cite feature có trong dữ liệu → citation validation.
- Failure → explicit fallback, KHÔNG fabricate content.

## 6. Coding Conventions

- Tên bảng/cột: `snake_case`
- PK: `BIGSERIAL` với suffix `_id`
- Timestamps: `TIMESTAMPTZ`, UTC
- Mọi truy cập prediction → ghi audit log
- Không soft-delete; `active = false` = vô hiệu hóa
- JSON: `JSONB` cho bảng backend, `TEXT` cho ml_predictions signals

## 7. Running the Backend

```bash
# Install
pip install -e .

# Start server
uvicorn learnguard_backend.main:app --reload

# Health check: http://127.0.0.1:8000/health
# OpenAPI docs: http://127.0.0.1:8000/docs

# Tests
python -m unittest discover -s tests -v
```

## 8. TODO (remaining)

- [ ] Login, JWT expiry, RBAC advisor/admin, bootstrap user policy
- [ ] Dockerfile + docker-compose cho backend & DB
