# Backend TODO

- [ ] Chốt API contract version đầu tiên với frontend và ML/data batch.
- [ ] Implement FastAPI app factory, health route và request validation.
- [ ] Implement login, JWT expiry, RBAC advisor/admin và bootstrap user policy.
- [ ] Implement student list/detail và filter/sort/pagination.
- [ ] Implement brief generate/read/edit-version/decision workflow.
- [ ] Implement SQLAlchemy entities, migration, repository và transaction boundaries.
- [ ] Ghi audit cho auth, prediction access, brief và quyết định; không log PII/secrets.
- [x] Thêm shared LiteLLM client với timeout/retry giới hạn và chuẩn hóa kết quả.
- [x] Triển khai Ollama tự host làm LLM fallback có thể bật qua cấu hình.
- [x] Cung cấp LLM service dùng chung cho các bước điều phối gọi sync/async.
- [ ] Tích hợp LLM vào brief generation, citation validator và explicit fallback.
- [ ] Thêm API smoke tests, error contracts và OpenAPI review.
- [ ] Thêm container build/run config sau khi health route và settings hoàn thành.
