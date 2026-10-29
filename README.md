# LearnGuard Backend

Git repository riêng cho M6 API, M7 persistence, M8 advisor brief và phần audit/observability được gọi từ API. Các module hiện là contract/TODO, chưa chạy server.

## Boundaries

- Backend phục vụ advisor dashboard; không có endpoint dành cho sinh viên hay hành động tự động tới sinh viên.
- M4/M5 batch ML và M1–M3 data engineering thuộc repository `project` hiện tại.
- Backend đọc prediction đã được batch ghi vào persistence. Khi tách deployment, trao đổi qua API hoặc schema/event contract đã version hóa; không import mã Python nội bộ từ repo ML/data.
- M8 chạy trong tiến trình backend theo system design; LLM chỉ nhận context tối thiểu và brief luôn qua advisor review.

## Module map

- `src/learnguard_backend/api/`: app factory, controllers, auth và request/response DTO.
- `src/learnguard_backend/db/`: records, repository interfaces và session contract.
- `src/learnguard_backend/services/briefs.py`: brief generation/validation/fallback contract.
- `src/learnguard_backend/services/llm.py`: client LLM dùng LiteLLM, hỗ trợ chat completion đồng bộ/bất đồng bộ và chuẩn hóa usage/cost/latency.
- `src/learnguard_backend/observability.py`: audit, request metrics và run summary contracts.
- `src/learnguard_backend/settings.py`: backend-only runtime configuration.

Core methods đang để TODO/`NotImplementedError`. Xem `TODO.md` cho thứ tự triển khai. Chưa có Dockerfile hoặc Compose chạy được.

## LLM configuration

Tạo file cấu hình một lần bằng PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
```

Trong `.env`, thay `LLM_API_KEY` bằng OpenRouter API key của bạn rồi lưu file. Settings tự đọc `.env` ở thư mục gốc backend; biến môi trường đã được đặt trong process sẽ được ưu tiên hơn giá trị trong file. `.env` được Git bỏ qua; không commit hoặc chia sẻ file này.

Mẫu `.env.example` dùng `openrouter/google/gemini-2.5-flash:free` và endpoint OpenRouter. Nếu model free này không còn khả dụng trên tài khoản, thay `LLM_MODEL` bằng slug `:free` đang được OpenRouter cung cấp. Các thiết lập khác gồm `LLM_TIMEOUT_SECONDS` mặc định `30`, `LLM_MAX_RETRIES` mặc định `2`, và có thể cấu hình qua `.env`.

### Ollama fallback tự host

Fallback Ollama được bật khi `LLM_FALLBACK_MODEL` được cấu hình. Khởi chạy service bằng Docker Compose và tải model một lần:

```powershell
docker compose -f compose.ollama.yml up -d
docker compose -f compose.ollama.yml exec ollama ollama pull qwen2.5:3b
```

`.env.example` cấu hình model fallback là `ollama/qwen2.5:3b` tại `http://localhost:11434`. Backend thử lại bằng Ollama khi provider chính gặp lỗi; fallback có timeout riêng `LLM_FALLBACK_TIMEOUT_SECONDS` (mặc định `120` giây) để chờ model local nạp/chạy inference. Nếu cả hai lỗi, `LLMFailoverError` được ném ra thay vì trả kết quả giả. Đặt `LLM_FALLBACK_MODEL` rỗng hoặc bỏ biến này để tắt fallback. Model được giữ trong volume `ollama_models`; dịch vụ chỉ bind vào loopback máy chủ. Tùy chỉnh `OLLAMA_PORT` nếu cổng mặc định `11434` đã được sử dụng và cập nhật `LLM_FALLBACK_API_BASE` tương ứng.

Mỗi request giới hạn mặc định tối đa `8.000` token đầu vào (`LLM_MAX_INPUT_TOKENS`) và `1.000` token đầu ra (`LLM_MAX_OUTPUT_TOKENS`). Đầu vào được đếm bằng LiteLLM theo model trước khi gửi; request vượt giới hạn bị từ chối. Giới hạn đầu ra được truyền tới provider và không thể bị ghi đè từ tùy chọn từng request.

Các bước điều phối trong backend dùng service dùng chung bằng cách import `get_llm_service()` từ `learnguard_backend.services.llm`. Factory khởi tạo cấu hình từ environment ở lần gọi đầu tiên và tái sử dụng một instance trong suốt vòng đời process:

```python
from learnguard_backend.services.llm import get_llm_service

llm = get_llm_service()
result = llm.complete(
    [{"role": "user", "content": "Summarize this orchestration step."}],
    temperature=0.2,
)
# Trong luồng async: result = await llm.acomplete(messages, temperature=0.2)
```

Gọi `complete(messages, ...)` hoặc `await acomplete(messages, ...)`; các tùy chọn riêng của model/provider có thể truyền tiếp dưới dạng keyword arguments. Kết quả bao gồm nội dung, model thực tế, usage, latency và cost nếu provider cung cấp. Khi fallback được cấu hình, lỗi provider chính sẽ kích hoạt thử model fallback; nếu cả hai thất bại, client ném `LLMFailoverError` và giữ nguyên lỗi của từng provider để chẩn đoán. Nếu không cấu hình fallback, lỗi provider chính được truyền lại như trước. Các thay đổi environment được áp dụng sau khi khởi động lại backend. `tenacity` được khai báo trực tiếp vì LiteLLM cần dependency này để thực hiện retry trong một số luồng lỗi.
