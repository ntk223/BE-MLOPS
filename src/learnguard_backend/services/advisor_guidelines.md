# Sổ Hướng Dẫn Cố Vấn Học Tập — LearnGuard

> Tài liệu nội bộ dành cho cố vấn học tập sử dụng hệ thống LearnGuard.
> Phiên bản: 1.0 | Cập nhật: 2026-11

---

## 1. Phân Loại Mức Độ Rủi Ro

### 1.1 Rủi ro CAO (risk_score ≥ 0.70)

**Đặc điểm thường gặp:**
- Lượt tương tác VLE rất thấp hoặc giảm mạnh từ tuần 2 trở đi
- Có bài kiểm tra bị bỏ lỡ (not_submitted) hoặc nộp trễ đáng kể
- Điểm trung bình bài nộp dưới 40/100
- Xu hướng click tuần 3-4 giảm hơn 50% so với tuần 1-2
- Không tham gia diễn đàn (forumng clicks = 0)

**Mức ưu tiên can thiệp:** KHẨN CẤP — liên hệ trong vòng 48 giờ

**Chiến lược can thiệp:**
1. Liên hệ trực tiếp qua email/tin nhắn cá nhân (không nhắc thuật toán AI)
2. Hẹn gặp 1-1 để tìm hiểu khó khăn cá nhân (tài chính, sức khỏe, động lực)
3. Giới thiệu dịch vụ hỗ trợ học tập: mentor, gia sư, nhóm học
4. Lập kế hoạch học tập tuần cụ thể với mốc thời gian ngắn hạn
5. Theo dõi lại sau 1 tuần để đánh giá tiến triển

### 1.2 Rủi ro TRUNG BÌNH (0.40 ≤ risk_score < 0.70)

**Đặc điểm thường gặp:**
- Tương tác VLE có nhưng không đều đặn
- Nộp bài đúng hạn nhưng điểm trung bình ~ 50-65/100
- Xu hướng click có dao động nhưng chưa giảm mạnh
- Có tham gia một số hoạt động nhưng thiếu chiều sâu

**Mức ưu tiên can thiệp:** QUAN SÁT TÍCH CỰC — liên hệ trong vòng 1 tuần

**Chiến lược can thiệp:**
1. Gửi email hỏi thăm chung về tiến độ học tập
2. Gợi ý tài nguyên bổ sung (video, bài đọc thêm, bài tập mẫu)
3. Khuyến khích tăng tần suất truy cập VLE (ít nhất 3 ngày/tuần)
4. Nhắc nhở lịch nộp bài sắp tới
5. Theo dõi lại sau 2 tuần

### 1.3 Rủi ro THẤP (risk_score < 0.40)

**Đặc điểm thường gặp:**
- Tương tác VLE đều đặn, xu hướng ổn định hoặc tăng
- Nộp bài đúng hạn với điểm trung bình ≥ 70/100
- Tham gia đa dạng hoạt động (quiz, forum, content)

**Mức ưu tiên can thiệp:** GIÁM SÁT — không cần can thiệp trực tiếp

**Chiến lược:**
1. Duy trì giám sát định kỳ qua hệ thống
2. Gửi lời động viên nếu có cơ hội
3. Tập trung nguồn lực cho nhóm rủi ro cao và trung bình

---

## 2. Giải Thích Các Chỉ Số Chính

### 2.1 Risk Score (Điểm rủi ro)
- Xác suất từ 0.0 đến 1.0, dự đoán khả năng sinh viên KHÔNG hoàn thành môn học
- Dựa trên dữ liệu hành vi 4 tuần đầu (day 0–27)
- **Lưu ý:** Đây là xác suất thống kê, không phải kết luận chắc chắn

### 2.2 Top Signals (SHAP)
- Các đặc trưng có ảnh hưởng lớn nhất đến điểm rủi ro
- `direction: increases_risk` → đặc trưng này đẩy điểm rủi ro LÊN
- `direction: decreases_risk` → đặc trưng này kéo điểm rủi ro XUỐNG (điểm sáng)
- `contribution` → mức đóng góp (giá trị tuyệt đối càng lớn → ảnh hưởng càng mạnh)

### 2.3 Weekly VLE Clicks (Lượt tương tác tuần)
- Tổng số lần sinh viên truy cập tài nguyên học tập trực tuyến (VLE)
- Phân theo tuần: Trước khóa (day -14..-1), Tuần 1-4 (day 0-27)
- `days_active` = số ngày RIÊNG BIỆT có hoạt động trong tuần đó
- **Mẫu tích cực:** Click tăng dần hoặc ổn định, days_active ≥ 3/tuần
- **Mẫu tiêu cực:** Click giảm mạnh hoặc days_active ≤ 1/tuần

### 2.4 Submissions (Lịch sử nộp bài)
- `assessment_type`: TMA (bài tập), CMA (kiểm tra máy), Exam (thi cuối kỳ)
- `submitted_day` vs `assessment_day`: Chênh lệch dương = nộp trễ
- `score`: Điểm 0-100; `weight`: Trọng số đánh giá (%)
- `status: not_submitted`: Bài chưa nộp — tín hiệu rủi ro rất quan trọng

### 2.5 Activity Breakdown (Phân loại hoạt động)
- Phân loại click theo loại tài nguyên VLE: `forumng`, `quiz`, `oucontent`, `resource`, `subpage`, v.v.
- **Sinh viên khỏe mạnh** thường có phân bố đa dạng (forum + content + quiz)
- **Sinh viên rủi ro** thường chỉ truy cập 1-2 loại hoặc không có hoạt động nào

---

## 3. Hướng Dẫn Giao Tiếp

### 3.1 Nguyên tắc chung
- **Đồng cảm trước, giải pháp sau:** Luôn hỏi thăm khó khăn trước khi đưa ra lời khuyên
- **Không phán xét:** Tránh sử dụng ngôn ngữ buộc tội ("bạn không học bài", "bạn lười")
- **Cụ thể và khả thi:** Mỗi gợi ý phải có hành động rõ ràng sinh viên có thể thực hiện ngay
- **Bảo mật thuật toán:** KHÔNG BAO GIỜ tiết lộ cho sinh viên rằng có hệ thống AI đang theo dõi

### 3.2 Mẫu email hỏi thăm (rủi ro cao)
```
Chào em [Tên],

Thầy/Cô muốn hỏi thăm tình hình học tập môn [Tên môn] của em. 
Thầy/Cô nhận thấy gần đây em ít truy cập hệ thống học trực tuyến 
hơn so với tuần đầu, và có một số bài tập chưa được nộp.

Thầy/Cô hiểu rằng có thể em đang gặp khó khăn — nếu có bất kỳ vấn đề 
gì (về nội dung môn học, thời gian, hoặc cá nhân), em đừng ngại chia 
sẻ để Thầy/Cô có thể hỗ trợ em tốt hơn.

Em có thể hẹn gặp Thầy/Cô qua [link lịch hẹn] để trao đổi thêm nhé.

Thân,
[Tên cố vấn]
```

### 3.3 Mẫu email nhắc nhở (rủi ro trung bình)
```
Chào em [Tên],

Thầy/Cô muốn nhắc em rằng bài [loại bài] sắp đến hạn nộp vào ngày 
[ngày]. Nếu em cần hỗ trợ về nội dung bài, có thể tham khảo thêm 
tài liệu trên VLE hoặc tham gia diễn đàn môn học.

Chúc em học tốt!

Thân,
[Tên cố vấn]
```

### 3.4 Hướng Dẫn Kịch Bản Gặp 1-1 (One-on-One Session)
Khi sinh viên đồng ý gặp mặt sau lời mời, cố vấn nên tuân thủ tiến trình 4 bước sau để tạo dựng lòng tin và giúp sinh viên cởi mở:

1. **Tạo không gian an toàn (0–5 phút)**
   - **Mục tiêu:** Giúp sinh viên giảm bớt áp lực tâm lý, không cảm thấy đây là một buổi "kỷ luật" hay "quở trách".
   - **Cách thực hiện:** Bắt đầu bằng thái độ thân thiện, hỏi thăm nhịp sống hoặc công việc làm thêm (nếu có). Khẳng định rõ: "Thầy/Cô gặp em để lắng nghe xem em đang gặp vướng mắc gì và cùng em tìm cách tháo gỡ."

2. **Lắng nghe chủ động & Tìm nguyên nhân gốc rễ (5–15 phút)**
   - **Mục tiêu:** Xác định chính xác lý do đằng sau mức rủi ro (học thuật, tài chính, sức khỏe, hay phương pháp học).
   - **Câu hỏi gợi ý:**
     - "Em cảm thấy phần nào của môn học này đang là thử thách lớn nhất đối với em?"
     - "Thời gian gần đây lịch trình học tập hoặc sinh hoạt của em có bị xáo trộn không?"
     - "Em có đang gặp khó khăn gì với việc tiếp thu tài liệu hoặc quản lý thời gian nộp bài không?"

3. **Đồng sáng tạo giải pháp ngắn hạn (15–25 phút)**
   - **Mục tiêu:** Cùng sinh viên chọn ra tối đa 1–2 hành động nhỏ nhất có thể thực hiện ngay trong tuần này để tạo đà tâm lý.
   - **Cách thực hiện:** Không áp đặt giải pháp. Ví dụ, nếu sinh viên bị ngợp kiến thức, hãy gợi ý: "Thay vì cố gắng đọc hết tài liệu tuần này, tuần này em chỉ cần tập trung xem lại video bài giảng của tuần 2 và làm 1 bài quiz thử, được không?" Giới thiệu các nguồn hỗ trợ ngang hàng (mentor, nhóm học tập) nếu cần.

4. **Chốt cam kết và lên lịch hẹn theo dõi (5 phút)**
   - **Mục tiêu:** Định hình rõ bước tiếp theo và tạo mốc thời gian để tái đánh giá.
   - **Cách thực hiện:** Thống nhất rõ ràng: "Vậy tụi mình chốt là em sẽ nộp bài tập bù trước thứ Sáu nhé. Thứ Hai tuần sau, Thầy/Cô trò chuyện lại 5 phút qua tin nhắn để xem tình hình thế nào nha."

---

## 4. Quy Trình Escalation

### Khi nào cần escalation lên cấp quản lý:
1. Sinh viên có risk_score ≥ 0.90 VÀ không có hoạt động trong 2 tuần liên tiếp
2. Sinh viên không phản hồi sau 2 lần liên hệ (email + tin nhắn)
3. Sinh viên tiết lộ vấn đề nghiêm trọng (sức khỏe tâm thần, tài chính khẩn cấp)
4. Nhiều sinh viên trong cùng một module có risk_score cao bất thường (vấn đề hệ thống)

### Quy trình:
1. Ghi nhận toàn bộ nỗ lực can thiệp đã thực hiện vào Brief
2. Tạo báo cáo tóm tắt gửi trưởng bộ môn
3. Đề xuất biện pháp hỗ trợ cấp khoa (hỗ trợ tài chính, giảm tải, nghỉ phép)

---

## 5. Lưu Ý Quan Trọng

### 5.1 Giới hạn của mô hình
- Mô hình dựa trên dữ liệu OULAD (Open University Learning Analytics Dataset)
- Dự đoán dựa trên 4 tuần đầu — chưa phản ánh đầy đủ toàn bộ quá trình học
- **Không phải kết luận:** Risk score là công cụ hỗ trợ, quyết định cuối cùng thuộc về cố vấn
- Uncertainty score cao → mô hình không tự tin → cần xem xét thêm bối cảnh

### 5.2 Bảo mật dữ liệu
- `student_key` là mã băm SHA-256, KHÔNG phải mã số sinh viên thật
- KHÔNG BAO GIỜ chia sẻ risk score hoặc dữ liệu dự đoán trực tiếp cho sinh viên
- Mọi thao tác trên hệ thống đều được ghi audit log

### 5.3 Đánh giá hiệu quả can thiệp
- So sánh risk_score trước và sau can thiệp (nếu có batch mới)
- Theo dõi thay đổi hành vi VLE sau khi liên hệ
- Ghi nhận kết quả cuối kỳ để cải thiện quy trình

---

## 6. Xử Lý Tình Huống Khẩn Cấp & Khủng Hoảng Tâm Lý

Trong quá trình liên hệ hoặc gặp gỡ sinh viên (nhất là nhóm rủi ro cao), cố vấn có thể tiếp nhận những tín hiệu hoặc thông tin vượt ra ngoài phạm vi học tập thông thường.

### 6.1 Nhận diện "Cờ Đỏ" (Red Flags) khẩn cấp
Cố vấn cần đặc biệt lưu ý và chuyển đổi ngay sang quy trình khẩn cấp khi sinh viên:
- Thể hiện sự tuyệt vọng cùng cực, buông xuôi hoàn toàn hoặc có phát ngôn ám chỉ hành vi tự làm hại bản thân / tự tử (qua email, tin nhắn hoặc trực tiếp).
- Gặp biến cố gia đình/tài chính đột ngột dẫn đến nguy cơ buộc phải thôi học ngay lập tức.
- Tiết lộ các vấn đề sức khỏe tâm thần nghiêm trọng (trầm cảm nặng, rối loạn hoảng sợ) đang cản trở hoàn toàn khả năng sinh hoạt.

### 6.2 Quy trình xử lý khẩn cấp (Crisis Protocol)
1. **Giữ bình tĩnh và thể hiện sự đồng cảm tối đa:** Không phán xét hay xem nhẹ cảm xúc của sinh viên. Nếu sinh viên có dấu hiệu tự hại, tuyệt đối không để sinh viên một mình (nếu đang gặp trực tiếp) và giữ thái độ kiên nhẫn, vững vàng.
2. **Kích hoạt kênh hỗ trợ chuyên môn:**
   - Bỏ qua các bước theo dõi thông thường của LearnGuard.
   - Chuyển tiếp ngay thông tin vụ việc tới Phòng Công tác sinh viên / Bộ phận Tư vấn tâm lý học đường trong vòng tối đa 24 giờ.
3. **Báo cáo cấp quản lý (Escalation):** Ghi nhận ngắn gọn sự việc vào hệ thống brief và thông báo trực tiếp cho Trưởng bộ môn hoặc Quản lý chương trình cố vấn.
4. **Giữ ranh giới vai trò:** Cố vấn học tập đóng vai trò kết nối và đồng hành ban đầu, không thay thế chuyên gia tâm lý hay bác sĩ tâm thần trong việc điều trị các bệnh lý lâm sàng.
