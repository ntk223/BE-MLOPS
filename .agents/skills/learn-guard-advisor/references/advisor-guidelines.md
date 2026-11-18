# Sổ Hướng Dẫn Cố Vấn Học Tập — LearnGuard (Reference)

> **Nguồn gốc:** [`src/learnguard_backend/services/advisor_guidelines.md`](file:///c:/Users/Asus/Desktop/MLOps/BE-MLOPS/src/learnguard_backend/services/advisor_guidelines.md)
> **Phiên bản:** 1.0 | 2026-11

---

## 1. Phân Loại Mức Độ Rủi Ro

### 1.1 Rủi ro CAO (risk_score ≥ 0.70)

**Đặc điểm:**
- VLE clicks rất thấp hoặc giảm mạnh từ tuần 2
- Bài kiểm tra bị bỏ lỡ hoặc nộp trễ đáng kể
- Điểm trung bình < 40/100
- Click tuần 3-4 giảm > 50% so với tuần 1-2
- Forum clicks = 0

**Ưu tiên:** KHẨN CẤP — liên hệ trong 48 giờ

**Chiến lược:**
1. Liên hệ trực tiếp qua email/tin nhắn (không nhắc AI)
2. Hẹn gặp 1-1 để tìm hiểu khó khăn
3. Giới thiệu dịch vụ hỗ trợ: mentor, gia sư, nhóm học
4. Lập kế hoạch học tập tuần cụ thể
5. Theo dõi lại sau 1 tuần

### 1.2 Rủi ro TRUNG BÌNH (0.40 ≤ risk_score < 0.70)

**Đặc điểm:**
- VLE có nhưng không đều đặn
- Nộp bài đúng hạn, điểm 50-65/100
- Click dao động nhưng chưa giảm mạnh

**Ưu tiên:** QUAN SÁT TÍCH CỰC — liên hệ trong 1 tuần

**Chiến lược:**
1. Email hỏi thăm chung
2. Gợi ý tài nguyên bổ sung
3. Khuyến khích VLE ≥ 3 ngày/tuần
4. Nhắc nhở lịch nộp bài
5. Theo dõi lại sau 2 tuần

### 1.3 Rủi ro THẤP (risk_score < 0.40)

**Ưu tiên:** GIÁM SÁT — không can thiệp trực tiếp

**Chiến lược:**
1. Giám sát định kỳ
2. Động viên nếu có cơ hội
3. Tập trung nguồn lực cho nhóm rủi ro cao/trung bình

---

## 2. Giải Thích Các Chỉ Số

### Risk Score
- Xác suất 0.0–1.0, dự đoán khả năng KHÔNG hoàn thành
- Dựa trên 4 tuần đầu (day 0–27)
- Là công cụ hỗ trợ, KHÔNG phải kết luận

### Top Signals (SHAP)
- `increases_risk` → đẩy điểm rủi ro LÊN
- `decreases_risk` → kéo điểm rủi ro XUỐNG
- `contribution` → giá trị tuyệt đối càng lớn → ảnh hưởng càng mạnh

### Weekly VLE Clicks
- Phân theo tuần: Trước khóa (day -14..-1), Tuần 1-4 (day 0-27)
- `days_active` = số ngày RIÊNG BIỆT có hoạt động
- Mẫu tích cực: Click tăng/ổn, days_active ≥ 3/tuần
- Mẫu tiêu cực: Click giảm mạnh hoặc days_active ≤ 1/tuần

### Submissions
- `assessment_type`: TMA (bài tập), CMA (kiểm tra máy), Exam
- Chênh lệch dương submitted_day vs assessment_day = nộp trễ
- `not_submitted` = tín hiệu rủi ro rất quan trọng

### Activity Breakdown
- Sinh viên khỏe mạnh: phân bố đa dạng (forum + content + quiz)
- Sinh viên rủi ro: chỉ 1-2 loại hoặc không có hoạt động

---

## 3. Hướng Dẫn Giao Tiếp

### Nguyên tắc:
- **Đồng cảm trước, giải pháp sau**
- **Không phán xét** — tránh ngôn ngữ buộc tội
- **Cụ thể và khả thi** — hành động rõ ràng
- **Bảo mật thuật toán** — KHÔNG BAO GIỜ tiết lộ có AI

### Mẫu email rủi ro cao:
```
Chào em [Tên],

Thầy/Cô muốn hỏi thăm tình hình học tập môn [Tên môn] của em.
Thầy/Cô nhận thấy gần đây em ít truy cập hệ thống học trực tuyến
hơn so với tuần đầu, và có một số bài tập chưa được nộp.

Thầy/Cô hiểu rằng có thể em đang gặp khó khăn — nếu có bất kỳ
vấn đề gì, em đừng ngại chia sẻ.

Em có thể hẹn gặp qua [link lịch hẹn] nhé.

Thân,
[Tên cố vấn]
```

### Kịch bản gặp 1-1 (4 bước):
1. **Tạo không gian an toàn (0–5 phút)** — Thân thiện, khẳng định mục đích lắng nghe
2. **Lắng nghe & tìm nguyên nhân (5–15 phút)** — Câu hỏi mở, xác định gốc rễ
3. **Đồng sáng tạo giải pháp (15–25 phút)** — Max 1-2 hành động nhỏ nhất
4. **Chốt cam kết & hẹn theo dõi (5 phút)** — Mốc thời gian rõ ràng

---

## 4. Quy Trình Escalation

### Khi nào escalation:
1. risk_score ≥ 0.90 VÀ không hoạt động 2 tuần
2. Không phản hồi sau 2 lần liên hệ
3. Vấn đề nghiêm trọng (sức khỏe tâm thần, tài chính khẩn cấp)
4. Nhiều SV cùng module có risk cao bất thường (vấn đề hệ thống)

### Quy trình:
1. Ghi nhận nỗ lực can thiệp vào Brief
2. Báo cáo trưởng bộ môn
3. Đề xuất biện pháp cấp khoa

---

## 5. Xử Lý Khẩn Cấp (Crisis Protocol)

### Red Flags:
- Phát ngôn tuyệt vọng / tự hại / tự tử
- Biến cố gia đình/tài chính → nguy cơ thôi học ngay
- Sức khỏe tâm thần nghiêm trọng

### Quy trình:
1. Giữ bình tĩnh, đồng cảm tối đa, không để SV một mình
2. Chuyển tiếp Phòng Công tác SV / Tư vấn tâm lý ≤ 24 giờ
3. Báo cáo Trưởng bộ môn
4. Giữ ranh giới vai trò — không thay thế chuyên gia tâm lý

---

## 6. Lưu Ý Quan Trọng

- Mô hình dựa trên OULAD, dự đoán từ 4 tuần đầu
- Risk score là công cụ hỗ trợ, quyết định thuộc cố vấn
- Uncertainty cao → mô hình không tự tin
- `student_key` là mã băm, KHÔNG phải MSSV
- Mọi thao tác đều ghi audit log
