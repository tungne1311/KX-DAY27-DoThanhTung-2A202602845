# Day27 — Hướng dẫn chạy và trình bày bài lab

Đã hoàn thành 5 bài: confidence routing, HITL `interrupt()` / `Command(resume=...)`,
escalation hỏi đáp, audit SQLite có replay và giao diện Streamlit.

## 1. Mở giao diện trên Windows

Mở thư mục dự án và chạy `run_lab.cmd`, hoặc dùng PowerShell:

```powershell
cd E:\KX-DAY27-DoThanhTung-2A202602845
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

Truy cập `http://localhost:8501`. Giao diện mặc định bật **Offline demo**.

Nếu cài lại trên máy khác:

```powershell
python -m pip install uv
python -m uv sync --locked
Copy-Item .env.example .env
```

Các ví dụ dùng `python -m uv` để không phụ thuộc PATH của Windows.

## 2. Demo đủ ba nhánh, không cần API key

Giữ chọn **Offline demo**. URL demo chỉ dùng chọn dữ liệu mẫu trên máy;
không đọc PR thật, không gọi LLM và không đăng nhận xét lên GitHub.

| URL nhập vào | Confidence mẫu | Kết quả |
|---|---:|---|
| `https://github.com/VinUni-AI20k/PR-Demo/pull/1` | 65% | Hiện review, diff, reasoning và Approve / Reject / Edit |
| `https://github.com/VinUni-AI20k/PR-Demo/pull/2` | 45% | Hiện rủi ro và câu hỏi; trả lời → review tinh chỉnh → xác nhận cuối |
| `https://github.com/VinUni-AI20k/PR-Demo/pull/3` | 94% | Tự hoàn thành review; thao tác đăng chỉ được mô phỏng |

**Kịch bản trình bày:**

1. Chạy PR #1, bấm **Approve** và mở **Session audit trail**.
2. Chạy lại PR #1, nhập yêu cầu vào **Feedback / edit instructions**, bấm **Edit**.
   Agent sửa review rồi dừng để bạn duyệt phiên bản mới. Có thể tiếp tục sửa hoặc từ chối.
3. Chạy PR #2, nhập đủ câu trả lời và bấm **Submit answers**. Review được tổng hợp lại;
   agent chỉ hoàn tất sau khi bấm **Approve** ở bước xác nhận cuối. **Reject** không đăng.
4. Chạy PR #3 để minh họa nhánh tự động.
5. Mở một phiên ở sidebar, hoặc refresh URL có `?thread=...` để chứng minh khôi phục từ SQLite.

Demo có nhãn riêng trong giao diện, trạng thái cuối và audit; không dùng kết quả demo
để khẳng định đã gọi API hoặc đăng comment thật.

## 3. Chạy với GitHub và LLM thật

Máy hiện tại đã được cấu hình bằng OpenRouter key của Day19 và phiên GitHub CLI
đã đăng nhập. Hai kết nối đã được xác thực thành công. Khi dùng trên máy này,
bạn chỉ cần bỏ chọn **Offline demo**. Phần điền `.env` dưới đây dành cho việc cài
lại hoặc thay key.

Điền vào `.env` (file đã được git-ignore):

```dotenv
OPENROUTER_API_KEY=<OpenRouter key của bạn>
LLM_MODEL=openai/gpt-4o-mini
LLM_BASE_URL=https://openrouter.ai/api/v1
GITHUB_TOKEN=<GitHub PAT của bạn>
GITHUB_USER=<GitHub username của người duyệt>
```

Khởi động lại Streamlit sau khi đổi `.env`. Bỏ chọn **Offline demo**, nhập URL PR thật
và bấm **Run review**. Nhánh tự động hoặc thao tác duyệt sẽ đăng một comment thảo luận
lên PR. Đây không phải merge PR hay một formal GitHub Approve review.

Confidence của LLM thật không cố định theo số PR. Ngưỡng phân nhánh luôn là:
`>= 0.73` tự động, `0.58 <= c < 0.73` duyệt, `< 0.58` escalation.
Reviewer username do người dùng cung cấp để ghi audit, không phải cơ chế đăng nhập.

## 4. Chạy từng exercise

```powershell
python -m uv run python -m exercises.exercise_1_confidence --demo --pr https://github.com/VinUni-AI20k/PR-Demo/pull/1
python -m uv run python -m exercises.exercise_2_hitl --demo --pr https://github.com/VinUni-AI20k/PR-Demo/pull/1
python -m uv run python -m exercises.exercise_3_escalation --demo --pr https://github.com/VinUni-AI20k/PR-Demo/pull/2
python -m uv run python -m exercises.exercise_4_audit --demo --pr https://github.com/VinUni-AI20k/PR-Demo/pull/2
```

Bỏ `--demo` khi muốn dùng API thật. Bài 1 chỉ phân nhánh; bài 2 giữ escalation làm
điểm dừng cho bài 3. Bài 3 dùng MemorySaver; bài 4 và UI dùng AsyncSqliteSaver.

## 5. Resume và replay

Sau khi bắt đầu, CLI in `thread_id`; UI lưu nó trong URL và sidebar.
Database mặc định là `hitl_audit.db`, có thể đổi bằng `HITL_DB_PATH`.

```powershell
# Khôi phục phiên CLI, không fetch và phân tích PR từ đầu
python -m uv run python -m exercises.exercise_4_audit --thread <thread_id>

# Liệt kê phiên / xem lại chuỗi sự kiện audit
python -m uv run python -m audit.replay --list
python -m uv run python -m audit.replay --thread <thread_id>
```

Checkpoint lưu trạng thái chạy của graph; `audit_events` lưu các cột thời gian, agent,
action, confidence, risk, reviewer, decision, reason và execution time để truy vấn.
Trong lab, cả hai nằm trong SQLite theo hướng dẫn README gốc; chưa triển khai PostgreSQL.

Các sự kiện yêu cầu duyệt được đặt ở node riêng trước node `interrupt()`, tránh
ghi lặp khi resume. Nếu tiến trình dừng ở một node chưa xong, chọn **Continue saved session**.
Mỗi thao tác đăng thật gắn marker theo thread và revision để một worker có thể kiểm tra
comment đã đăng khi thử lại. Marker không bảo đảm loại trùng giữa nhiều worker chạy đồng thời.
Audit và checkpoint là hai lần ghi riêng, nên việc kill tiến trình ngay giữa hai lần ghi
vẫn có thể làm lặp sự kiện audit khi chạy lại node đó.

## 6. Kiểm tra

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
python -m uv sync --locked
```

Test dùng LangGraph, checkpoint SQLite thật và Streamlit AppTest. Các ca kiểm tra
bao gồm ba nhánh, biên confidence, approve/reject/edit, Q&A và xác nhận cuối,
khôi phục qua tiến trình Python mới, audit, lỗi đăng GitHub, link comment,
pagination khi tìm comment cũ và thao tác trên giao diện. API được giả lập khi test;
bài chạy live cần API key và token thật.

## 7. Đối chiếu sản phẩm

| Yêu cầu | Nơi thực hiện |
|---|---|
| Phân tích diff, structured output, confidence routing | exercise 1 + `exercises/review_helpers.py` |
| Interrupt và resume, approve/reject/edit | exercise 2 |
| Escalation có câu hỏi cụ thể và tổng hợp lại review | exercise 3 |
| Durable checkpoint, AuditEntry, resume/replay | exercise 4, `common/db.py`, `audit/` |
| Streamlit UI, giữ phiên khi refresh, sidebar | `app.py` |
| Bonus auto-edit và xem lại trước khi đăng | exercise 2–4 và UI |

`common/` giữ các model và ngưỡng gốc. Có các chỉnh sửa hỗ trợ chạy hoàn chỉnh:
UTF-8 trên Windows, đọc schema UTF-8, phân trang danh sách file PR, kiểm tra URL,
trả về URL comment và marker tránh đăng lặp khi retry. `uv.lock` đã được đồng bộ
với dependencies SQLite/Streamlit trong `pyproject.toml`.
