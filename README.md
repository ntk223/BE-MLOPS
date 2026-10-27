# LearnGuard Backend

Git repository riêng cho M6 API, M7 persistence, M8 advisor brief và phần audit/observability được gọi từ API. Các module hiện là contract/TODO, chưa chạy server.

## Boundaries

- Backend phục vụ advisor dashboard; không có endpoint dành cho sinh viên hay hành động tự động tới sinh viên.
- M4/M5 batch ML và M1–M3 data engineering thuộc repository `project` hiện tại.
- Backend đọc prediction đã được batch ghi vào persistence. Khi tách deployment, trao đổi qua API hoặc schema/event contract đã version hóa; không import mã Python nội bộ từ repo ML/data.
- M8 chạy trong tiến trình backend theo system design; LLM chỉ nhận context tối thiểu và brief luôn qua advisor review.

## Module map

- `src/learnguard/api/`: app factory, controllers, auth và request/response DTO.
- `src/learnguard/db/`: records, repository interfaces và session contract.
- `src/learnguard/services/briefs.py`: brief generation/validation/fallback contract.
- `src/learnguard/observability.py`: audit, request metrics và run summary contracts.
- `src/learnguard/settings.py`: backend-only runtime configuration.

Core methods đang để TODO/`NotImplementedError`. Xem `TODO.md` cho thứ tự triển khai. Chưa có Dockerfile hoặc Compose chạy được.
