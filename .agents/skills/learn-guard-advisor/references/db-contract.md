# LearnGuard Backend — DB Contract (Reference)

> **Phiên bản:** 1.0 — approved (2026-10-30, bản 2: backend là consumer chỉ đọc)
> **Nguồn gốc:** [`docs/DB_CONTRACT.md`](file:///c:/Users/Asus/Desktop/MLOps/BE-MLOPS/docs/DB_CONTRACT.md)

---

## 1. Luồng dữ liệu

```
OULAD CSV → M1 ingestion → M2 feature snapshot → M3-M4 train → M5 scoring
  → ml-data GHI THẲNG vào PostgreSQL (upsert ml_predictions)
  → Backend chỉ SELECT → REST API → advisor dashboard
```

- Backend **KHÔNG INSERT/UPDATE/DDL** trên `ml_predictions`.
- Bảng `users`, `briefs`, `decisions`, `llm_calls`, `audit_logs`,
  `chat_sessions`, `chat_messages` do backend sở hữu.

## 2. Quy ước chung

| Quy ước | Giá trị |
|---------|---------|
| RDBMS | PostgreSQL >= 15 |
| ORM / migration | SQLAlchemy 2 + Alembic (chỉ bảng backend sở hữu) |
| Naming | `snake_case` |
| PK | `BIGSERIAL` (`*_id`); `ml_predictions` dùng PK composite |
| Timestamp | `TIMESTAMPTZ`, luôn UTC |
| JSON | `JSONB` cho bảng backend; `top_signals_json` là TEXT |
| Bất biến | `briefs` và `decisions` append-only |
| Kết nối | DSN qua env `DATABASE_URL` hoặc `LEARNGUARD_PG_DSN` |

## 3. Schema bảng

### 3.1 `ml_predictions` (ml-data sở hữu — READ ONLY)

PK: `(code_module, code_presentation, student_key)` — luôn là dự đoán mới nhất.

| Cột | Kiểu | Ghi chú |
|-----|------|---------|
| `student_key` | CHAR(16) | Pseudonymous key |
| `code_module` | VARCHAR | PK phần 1 |
| `code_presentation` | VARCHAR | PK phần 2 |
| `risk_score` | DOUBLE PRECISION | NULL khi không score được |
| `predicted_label` | SMALLINT NULL | NULL khi không gán nhãn |
| `uncertainty` | DOUBLE PRECISION | >= 0.8 → `manual_review` |
| `status` | VARCHAR | 4 giá trị: scored/manual_review/insufficient_data/no_prediction |
| `top_signals_json` | TEXT | JSON array max 5 signal |
| `features_json` | TEXT | Full feature vector |
| `scored_at` | TIMESTAMPTZ | Thời điểm chấm |

### 3.2 `users` (backend sở hữu)

| Cột | Kiểu | Ràng buộc |
|-----|------|-----------|
| `user_id` | BIGSERIAL | PK |
| `email` | VARCHAR(255) | UNIQUE |
| `role` | VARCHAR(16) | CHECK ∈ `advisor`, `admin` |
| `active` | BOOLEAN | DEFAULT true |

### 3.3 `briefs` (backend sở hữu — append-only)

| Cột | Kiểu | Ràng buộc |
|-----|------|-----------|
| `brief_id` | BIGSERIAL | PK |
| `student_key` | CHAR(16) | join ml_predictions |
| `version` | INTEGER | bắt đầu 1, tăng dần |
| `content` | JSONB | NOT NULL |
| `citations` | JSONB | DEFAULT `[]` |
| `status` | VARCHAR(24) | awaiting_review / accepted / rejected |

### 3.4 `decisions` (backend sở hữu — append-only)

| Cột | Kiểu | Ràng buộc |
|-----|------|-----------|
| `decision_id` | BIGSERIAL | PK |
| `brief_id` | BIGINT | FK → briefs |
| `action` | VARCHAR(8) | accept / reject / edit |

### 3.5 `llm_calls` (observability — không log prompt/response)

| Cột | Kiểu | Ràng buộc |
|-----|------|-----------|
| `call_id` | BIGSERIAL | PK |
| `model` | VARCHAR(128) | NOT NULL |
| `prompt_tokens` | INTEGER | DEFAULT 0 |
| `completion_tokens` | INTEGER | DEFAULT 0 |
| `cost_usd` | NUMERIC(12,6) | NULL |
| `status` | VARCHAR(24) | ok / validation_failed / api_error |

### 3.6 `audit_logs` (append-only, không PII)

| Cột | Kiểu | Ràng buộc |
|-----|------|-----------|
| `log_id` | BIGSERIAL | PK |
| `action` | VARCHAR(64) | NOT NULL |
| `entity` | VARCHAR(64) | NOT NULL |
| `payload` | JSONB | **không chứa PII/secrets** |

### 3.7 `chat_sessions` & `chat_messages`

- Session scoped to `(code_module, code_presentation, student_key, user_id)`
- Status: `active` → `finalized` / `archived`
- Messages: role ∈ `advisor` / `assistant`, append-only

## 4. Enums & state machine

### PREDICTION_STATUS (4 giá trị bắt buộc xử lý)

| Giá trị | Ý nghĩa |
|---------|---------|
| `scored` | Score bình thường |
| `insufficient_data` | Không đủ dữ liệu (predicted_label NULL) |
| `manual_review` | uncertainty >= 0.8 |
| `no_prediction` | Không sinh được prediction |

### BRIEF_STATUS

`awaiting_review` → (`accepted` | `rejected`); chỉnh sửa → version mới → `awaiting_review`.

## 5. Công thức student_key & PII policy

```
student_key = sha256("learnguard:prediction-batch.v1:{code_module}:{code_presentation}:{id_student}").hexdigest()[:16]
```

**Test vector:**

| code_module | code_presentation | id_student | student_key |
|-------------|-------------------|------------|-------------|
| AAA | 2013B | 1 | `4c337e13fc7b6871` |
| AAA | 2013J | 11391 | `7bd60aced1ccab8c` |

**PII policy:**
1. DB backend KHÔNG chứa `id_student`
2. Không lưu prompt/response LLM
3. Không log PII/secrets vào audit_logs hay console

## 6. Versioning & tiến hóa schema

1. Bảng backend → Alembic migration, không sửa migration đã merge.
2. Đổi schema `ml_predictions` = breaking change do ml-data quản lý.
3. Semver: thêm cột nullable = minor; đổi kiểu/xóa cột = major.
4. Code path ghi vào `ml_predictions` = **vi phạm hợp đồng**.
