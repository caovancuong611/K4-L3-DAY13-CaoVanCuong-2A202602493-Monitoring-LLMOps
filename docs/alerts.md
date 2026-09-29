# Alert và Runbook

Mỗi alert dựa trên **triệu chứng người dùng/SLO** (chậm, lỗi, tốn kém), không dựa trên tên hàm nội bộ. Cấu hình máy đọc được nằm ở [`config/alert_rules.yaml`](../config/alert_rules.yaml); SLO ở [`config/slo.yaml`](../config/slo.yaml).

Luồng điều tra chung: **Metrics** (dashboard) → **Logs** (`data/logs.jsonl`, lọc theo `correlation_id`) → **Traces** (Langfuse, tìm trace có metadata `correlation_id` đó, so sánh span `retrieval` và `llm-generate`).

## Alert 1

- Tên: `slow_responses_p95_breach`
- Severity: P2
- Duration: 5 phút
- Kênh thông báo: Slack `#day13-k4l3a-alerts`
- SLI/SLO liên quan: `primary_slo.fast_successful_requests` (request thành công và ≤ 2000 ms, mục tiêu 99,5%/28 ngày)
- Điều kiện và thời gian duy trì: P95 của `latency_ms` trên các dòng `response_sent` > 2000 ms liên tục 5 phút. Baseline P95 ≈ 151 ms.
- Ảnh hưởng tới người dùng: câu trả lời chậm; error budget bị đốt nhanh dù HTTP vẫn trả 200.
- Ba bước kiểm tra đầu tiên:
  1. Metrics: mở panel Latency. Nếu P95 tăng nhưng **TTFT P95 vẫn ≈ 50 ms** thì phần chậm không nằm ở LLM.
  2. Logs: lọc `event == "response_sent"` và `latency_ms > 2000`, lấy một `correlation_id`.
  3. Traces: mở trace có `correlation_id` đó, so sánh thời gian span `retrieval` với `llm-generate`; span chiếm gần hết thời gian là bottleneck (kịch bản `rag_slow`: retrieval thêm 2,5 s).
- Mitigation tạm thời: đặt timeout ngắn cho retrieval và trả lời bằng tài liệu fallback/cache; tăng tài nguyên hoặc khởi động lại vector store; khi luyện tập tắt sự cố bằng `python scripts/inject_incident.py --scenario rag_slow --disable`.
- Owner: Cao Van Cuong (2A202602493)

## Alert 2

- Tên: `request_errors_or_retrieval_down`
- Severity: P1
- Duration: 3 phút
- Kênh thông báo: Slack `#day13-k4l3a-alerts`
- SLI/SLO liên quan: guardrail `error_rate_pct_max = 2` và `retrieval_success_rate_pct_min = 90`; request lỗi cũng là request "xấu" của SLO chính.
- Điều kiện và thời gian duy trì: `request_failed / request_received > 2%` **hoặc** tỷ lệ `tool_success == true` < 90%, liên tục 3 phút.
- Ảnh hưởng tới người dùng: nhận lỗi HTTP 500, không có câu trả lời.
- Ba bước kiểm tra đầu tiên:
  1. Metrics: panel Errors cho biết error rate, phân rã theo `error_type` và Retrieval success.
  2. Logs: lọc `event == "request_failed"`, xem `error_type`, `tool_name == "retrieval"`, `tool_success == false` và `payload.detail` (ví dụ `Vector store timeout`); lấy `correlation_id`.
  3. Traces: mở trace cùng `correlation_id`; span `retrieval` phải ở trạng thái ERROR và không có span `llm-generate` phía sau.
- Mitigation tạm thời: degrade có kiểm soát (trả lời fallback không dùng retrieval thay vì lỗi 500); khởi động lại/failover vector store; thêm retry có giới hạn hoặc circuit breaker; luyện tập tắt sự cố bằng `--scenario tool_fail --disable`.
- Owner: Cao Van Cuong (2A202602493)

## Alert 3

- Tên: `cost_per_request_spike`
- Severity: P2
- Duration: 10 phút
- Kênh thông báo: Slack `#day13-k4l3a-alerts`
- SLI/SLO liên quan: guardrail `daily_cost_usd_max = 2.5` USD/ngày.
- Điều kiện và thời gian duy trì: trung bình `cost_usd` trên `response_sent` > 0,005 USD/request liên tục 10 phút. Baseline đo được ≈ 0.0022 USD/request; kịch bản `cost_spike` nhân `tokens_out` lên 4 lần.
- Ảnh hưởng tới người dùng: người dùng không thấy lỗi ngay nhưng chi phí vượt kế hoạch, có thể dẫn tới giới hạn tốc độ hoặc phải tắt tính năng.
- Ba bước kiểm tra đầu tiên:
  1. Metrics: panel Cost và Tokens; nếu `tokens_out` tăng mạnh còn latency/TTFT gần như không đổi thì nghi ngờ đầu ra dài bất thường.
  2. Logs: lọc `response_sent` có `tokens_out` cao và `cost_usd` lớn, lấy `correlation_id`.
  3. Traces: mở trace, xem observation `llm-generate` (usage, cost) và metadata `prompt_version`/`prompt_label` để biết prompt nào đang chạy.
- Mitigation tạm thời: đặt giới hạn `max_tokens` và độ dài câu trả lời; rollback label `production` của prompt về version trước (xem [PROMPT_VERSIONING.md](PROMPT_VERSIONING.md)); giới hạn tốc độ; luyện tập tắt sự cố bằng `--scenario cost_spike --disable`.
- Owner: Cao Van Cuong (2A202602493)

## Cách kiểm tra alert bằng practice scenario

1. Chạy `python scripts/load_test.py --concurrency 5` để có baseline, rồi `python scripts/build_dashboard.py` và mở `submission/evidence/dashboard.html`.
2. Bật một sự cố: `python scripts/inject_incident.py --scenario rag_slow` (hoặc `tool_fail`, `cost_spike`).
3. Chạy lại load test cùng input, dựng lại dashboard.
4. Xác nhận panel liên quan vượt ngưỡng đúng hướng: `rag_slow` làm P95 vượt 2000 ms; `tool_fail` làm error rate và Retrieval success xấu đi; `cost_spike` làm chi phí/request và output tokens tăng.
5. Tắt sự cố bằng `--disable`.

Repo này chưa có hệ thống Alertmanager thật; `config/alert_rules.yaml` là định nghĩa alert (điều kiện, duration, kênh, runbook), và dashboard hiển thị trạng thái vượt ngưỡng tương ứng.
