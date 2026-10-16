# Day27 — Đối chiếu yêu cầu trước khi nộp

Ngày kiểm tra: **16/10/2026**. Kết luận: hoàn thành **5 bài bắt buộc** theo README,
bao gồm phương án SQLite được đề cho phép. Bộ kiểm tra cuối: **26/26 đạt**.

| Yêu cầu | Triển khai | Bằng chứng |
|---|---|---|
| Đọc metadata, diff và danh sách file của PR | `common/github.py`, node `fetch_pr` | Kiểm tra HTTP giả lập bao gồm phân trang; đã đọc thật PR #1 và #2 |
| LLM trả về review có cấu trúc | `exercises/review_helpers.py`: `with_structured_output(PRAnalysis)` | Kiểm tra prompt có diff/Q&A; đã gọi thật OpenRouter |
| Confidence routing | Exercise 1, `common/schemas.py` | Kiểm tra cả ba nhánh và các biên 0.58 / 0.73 |
| Dừng và tiếp tục với người duyệt | Exercise 2: `interrupt`, `Command(resume=...)`, `MemorySaver` | Kiểm tra pause, Edit, Approve và Reject |
| Hiện diff, reasoning và các nhận xét | Payload chung, CLI và `app.py` | Streamlit AppTest chạy luồng duyệt |
| Escalation hỏi thêm ngữ cảnh | Exercise 3: `escalate`, `synthesize` | Kiểm tra câu trả lời bắt buộc, tổng hợp lại và xác nhận cuối |
| Edit nội dung review | Node `edit_review` trong exercise 2–4 | Review sửa xong phải được duyệt lại trước khi đăng |
| Đăng comment sau khi duyệt / tự động | Các node `commit`, `auto_approve`; GitHub helper | Kiểm tra thành công, URL comment, lỗi API, retry và Reject không đăng |
| Audit có các cột theo `AuditEntry` | Exercise 4, `common/db.py`, `audit/schema.sql` | Kiểm tra request/response, reviewer, decision, confidence mới sau synthesis |
| Resume phiên bền vững | `AsyncSqliteSaver`, `drive_graph` | Kiểm tra tiến trình Python mới và tiếp tục sau lỗi analyze mà không fetch lại |
| Replay và liệt kê phiên | `audit/replay.py` | CLI đã chạy; UI sử dụng cùng truy vấn recent sessions |
| Streamlit UI | `app.py` | **7 bài kiểm tra UI**: approve, edit/reject, escalation, auto, refresh phiên hoàn tất, refresh phiên chờ, sidebar, lỗi đăng (*) |
| Giữ `thread_id` khi refresh | `st.session_state` + query parameter `thread` + SQLite | Khôi phục cả phiên chờ duyệt và phiên đã hoàn tất |
| Cài đặt tái lập | `pyproject.toml`, `uv.lock` | `uv sync --locked`, build wheel và xác nhận có `audit/schema.sql` |

(*) Ca approve đồng thời kiểm tra refresh phiên hoàn tất; tổng cộng **7 test method UI**.

## Những điểm được đối chiếu rõ với đề

- Dòng deliverable gốc ghi PostgreSQL, nhưng phần "What this scaffolding already
  covers" và exercise 4 cho phép SQLite để chạy không cần cài server. Bản nộp dùng
  SQLite, chưa triển khai PostgreSQL.
- Ngưỡng chính xác theo code: `c >= 0.73` tự động; `0.58 <= c < 0.73` chờ duyệt;
  `c < 0.58` escalation. Đây là cách xử lý số thực của nhãn 58–72% trong đề.
- Nhánh escalation và Edit đều có bước xác nhận cuối. Không đăng ngay khi người dùng
  vừa gửi câu trả lời hay yêu cầu sửa.
- Các placeholder có chủ đích ở exercise 1 và nhánh escalation exercise 2 là yêu cầu
  của từng bài trung gian. Graph hoàn chỉnh ở exercise 4 và UI không còn TODO.
- Bonus auto-edit đã làm. Time-travel, confidence calibration theo nhiều phiên và
  multi-reviewer fan-out là phần tùy chọn, chưa triển khai.

## Giới hạn kiểm chứng

Lần kiểm tra API thật gần nhất trả về PR #1 confidence **0.65** (`human_approval`),
PR #2 confidence **0.45** (`escalate`, 4 câu hỏi). Điểm do model trả về có thể thay đổi.
Thao tác đăng comment được kiểm tra bằng HTTP giả lập, không đăng comment lên PR thật
trong quá trình kiểm tra này. Key, database và log cục bộ không nằm trong bản nộp.

Chi tiết lệnh chạy: [HUONG_DAN.md](HUONG_DAN.md).
Kết quả kiểm tra: [VALIDATION.md](VALIDATION.md).
