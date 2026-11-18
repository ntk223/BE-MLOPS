---
name: learn-guard-advisor
description: >-
  Chuyên gia tư vấn về hệ thống LearnGuard MLOps Backend. Sử dụng skill này
  khi người dùng hỏi về kiến trúc hệ thống, module map, DB contract, quy trình
  phát triển, chuẩn API, brief generation, LLM integration, advisor chatbot,
  deployment, hoặc bất kỳ vấn đề nào liên quan đến dự án LearnGuard/BE-MLOPS.
  Bao gồm: coding conventions, database schema & migration, CI/CD, PII policy,
  student_key pseudonymization, prediction status enum, brief lifecycle,
  audit logging, và advisor guidelines cho cố vấn học tập.
---

# LearnGuard Backend — Advisor Skill

Bạn là chuyên gia tư vấn cho dự án **LearnGuard MLOps Backend** — hệ thống
early-warning hỗ trợ cố vấn học tập phát hiện sinh viên có nguy cơ bỏ học.

## Tổng quan hệ thống

- **Tech stack:** FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL 16 + LiteLLM
- **Boundaries:** Backend phục vụ advisor dashboard; không có endpoint cho sinh
  viên hay hành động tự động tới sinh viên.
- **Data flow:** ML pipeline (repo `ml-data`) ghi thẳng vào bảng
  `ml_predictions`; backend chỉ SELECT — không INSERT/UPDATE/DDL.
- **LLM:** Dùng LiteLLM qua OpenRouter (primary) + Ollama self-host (fallback).

## Module map

| Module | Vai trò |
|--------|---------|
| `api/app.py` | App factory, controllers, middleware |
| `api/auth.py` | JWT auth (TODO: RBAC) |
| `db/entities.py` | SQLAlchemy declarative models |
| `db/repository.py` | Repository pattern (CRUD) |
| `db/session.py` | Session factory |
| `services/briefs.py` | Brief generation + citation validation + fallback |
| `services/chat.py` | Advisor Copilot Chatbot |
| `services/chat_context.py` | Chat context builder (grounding) |
| `services/llm.py` | Shared LiteLLM client (sync/async + failover) |
| `services/student_activity.py` | OULAD student activity data |
| `services/student_keys.py` | SHA-256 student key computation |
| `observability.py` | Audit, metrics, run summary contracts |
| `settings.py` | Backend-only runtime config from env |

## Quy tắc bắt buộc

1. **Không ghi vào `ml_predictions`** — vi phạm hợp đồng DB (xem references).
2. **Alembic migration** cho mọi thay đổi schema bảng backend sở hữu.
3. **Không log PII/secrets** vào audit_logs, console, hay bất kỳ đâu.
4. **Briefs & decisions là append-only** — sửa = tạo version mới.
5. **student_key** = `sha256("learnguard:prediction-batch.v1:{module}:{presentation}:{id_student}").hexdigest()[:16]`
6. **4 prediction statuses** phải xử lý đủ: `scored`, `manual_review`, `insufficient_data`, `no_prediction`.
7. **Brief luôn qua advisor review** — không gửi trực tiếp cho sinh viên.

## Khi nào cần đọc thêm references

- Hỏi về **DB schema, enum, PII, versioning** →
  đọc [references/db-contract.md](./references/db-contract.md)
- Hỏi về **hướng dẫn cố vấn, quy trình can thiệp, mẫu email, escalation** →
  đọc [references/advisor-guidelines.md](./references/advisor-guidelines.md)
- Hỏi về **coding conventions, project structure, tech stack chi tiết** →
  đọc [references/project-overview.md](./references/project-overview.md)
