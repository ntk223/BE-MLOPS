# Backend TODO

- [x] Chốt API contract version đầu tiên với frontend và ML/data batch (DB_CONTRACT.md).
- [x] Implement FastAPI app factory, health route và request validation.
- [ ] Implement login, JWT expiry, RBAC advisor/admin và bootstrap user policy.
- [x] Implement student list/detail và filter/sort/pagination (/predictions, /stats, /activity).
- [x] Implement brief generate/read/edit-version/decision workflow (/briefs/...).
- [x] Implement SQLAlchemy entities, migration, repository và transaction boundaries.
- [x] Ghi audit cho auth, prediction access, brief, chat và quyết định; không log PII/secrets.
- [x] Thêm shared LiteLLM client với timeout/retry giới hạn và chuẩn hóa kết quả.
- [x] Triển khai Ollama tự host làm LLM fallback có thể bật qua cấu hình.
- [x] Cung cấp LLM service dùng chung cho các bước điều phối gọi sync/async.
- [x] Tích hợp LLM vào brief generation, citation validator và explicit fallback.
- [x] Thiết kế & triển khai Advisor Copilot Chatbot (hỏi đáp tương tác, grounding, quick prompts, finalize brief).
- [x] Thêm database migration cho `chat_sessions` và `chat_messages`.
- [x] Thêm unit test cho AdvisorChatService và SQLChatRepository.
- [ ] Thêm container build/run config (Dockerfile + docker-compose) cho backend và DB.
