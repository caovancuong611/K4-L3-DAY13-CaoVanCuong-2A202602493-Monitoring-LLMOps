# Báo cáo cá nhân — K4-L3A Day 13 Monitoring & LLMOps

> Mỗi học viên hoàn thiện một file duy nhất này. Khi dẫn evidence, dùng đường dẫn tương đối, ví dụ `evidence/07-trace-waterfall.png`.

## 1. Thông tin học viên

- **Họ và tên:** Cao Văn Cường
- **MSSV:** 2A202602493
- **Lớp:** K4-L3A
- **Repository URL:**https://github.com/caovancuong611/K4-L3-DAY13-CaoVanCuong-2A202602493-Monitoring-LLMOps.git
- **Commit SHA cuối:**
- **Challenge ID:** `day13-k4-l3a-monitoring-llmops-v1`
- **Tên project Langfuse cá nhân:** `day13-k4-l3a-2A202602493`

## 2. Evidence index

Điền đúng đường dẫn tới evidence thực tế. Có thể đổi tên hoặc dùng nhiều ảnh nếu cần.

| Evidence            | Đường dẫn                             |
| ------------------- | ------------------------------------- |
| Pytest cuối         | `evidence/01-pytest.png`              |
| Log validator       | `evidence/02-log-validator.png`       |
| Dashboard validator | `evidence/03-dashboard-validator.png` |
| Structured log      | `evidence/04-structured-log.png`      |
| PII redaction       | `evidence/05-pii-redaction.png`       |
| Trace list          | `evidence/06-trace-list.png`          |
| Trace waterfall     | `evidence/07-trace-waterfall.png`     |
| Trace metadata      | `evidence/08-trace-metadata.png`      |
| Prompt versions v1  | `evidence/09-prompt-versions.png`     |
| Prompt versions v2  | `evidence/09b-prompt-v2.png`          |
| Prompt promote v2   | `evidence/10-prompt-promote-v2.png`   |
| Prompt rollback     | `evidence/10-prompt-rollback.png`     |
| Dashboard runtime   | `evidence/11-dashboard-overview.png`  |
| Incident metric     | `evidence/12-incident-metric.png`     |
| Incident log        | `evidence/13-incident-log.png`        |
| Incident trace      | `evidence/14-incident-trace.png`      |

## 3. Kết quả kỹ thuật

| Nội dung                | Baseline                                                                                 | Kết quả cuối                                       | Nhận xét                                                                              |
| ----------------------- | ---------------------------------------------------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `validate_logs.py`      | 30/100 (22 record, 20 thiếu trường bắt buộc, 20 thiếu metadata, 0 correlation ID hợp lệ) | 100/100 (10 correlation ID, 0 thiếu trường, 0 PII) | Baseline: correlation_id đang là `MISSING`. Xem `evidence/baseline-log-validator.txt` |
| `validate_dashboard.py` | 6/6 panel hợp lệ                                                                         | 6/6 panel hợp lệ                                   | Contract hợp lệ ngay từ starter. Xem `evidence/baseline-dashboard-validator.txt`      |
| `pytest`                | 22 passed                                                                                | 42 passed                                          | Xem `evidence/baseline-pytest.txt`                                                    |
| Số traces hợp lệ        | 0 (export bị 401, chưa có key)                                                           | 14                                                 | 14 trace, mỗi trace có `retrieval` + `llm-generate`; xem `evidence/06-trace-list.png` |
| Số PII leak             | 0 (theo validator)                                                                       | 0                                                  | Baseline chỉ có `message_preview` đã được `summarize_text` scrub sẵn                  |
| Latency P95 / TTFT P95  | 151 ms / 50 ms                                                                           | 169 ms / 65 ms | Tính từ 10 dòng `response_sent` trong log baseline (10 request `load_test.py`) Kết quả cuối: 10 request `response_sent` sau khi tắt `rag_slow` (P50 152 ms, P95 = P99 = 169 ms); không gộp log sự cố challenge. |
| Retrieval success rate  | 100% (10/10)                                                                             | 100% (10/10) | `tool_success=true` ở cả 10 dòng `response_sent` Kết quả cuối: `tool_success=true` ở cả 10 request sau khi tắt sự cố. |

## 4. Logging và PII

- **Cách tạo/nhận và truyền correlation ID:** `CorrelationIdMiddleware` (`app/middleware.py`) gọi `clear_contextvars()` đầu mỗi request để không rò context, đọc header `x-request-id`; nếu hợp lệ (`[A-Za-z0-9_-]{1,64}`, tránh log injection) thì dùng lại, nếu không thì sinh `req-<8 hex>` bằng `uuid4`. ID được `bind_contextvars` nên mọi dòng log trong request tự có `correlation_id`, lưu vào `request.state`, và trả lại qua header `x-request-id` cùng `x-response-time-ms`. `LabAgent.run` cũng đưa ID vào metadata của trace Langfuse.
- **Các metadata được ghi vào structured log:** `ts`, `level`, `service`, `event`, `correlation_id`, và (bind trong `chat()` trước log `request_received`) `user_id_hash` (SHA-256 cắt 12 ký tự, không lưu user_id thật), `session_id`, `feature`, `model`, `env`. Log `response_sent` còn có `latency_ms`, `ttft_ms`, `tokens_in`, `tokens_out`, `cost_usd`, `quality_score`, `tool_name`, `tool_success`.
- **Cách bảo đảm PII được scrub trước khi ghi:** processor `scrub_event` được đăng ký trong `configure_logging()` **trước** `JsonlFileProcessor` và `JSONRenderer`, nên dữ liệu đã sạch khi được serialize và ghi ra file/stdout. Hàm này đệ quy qua mọi chuỗi (kể cả `payload` lồng nhau, danh sách và `exception`), chỉ bỏ qua các trường do hệ thống sinh (`ts`, `level`, `correlation_id`, `user_id_hash`, `session_id`) để hash 12 chữ số không bị nhầm là CCCD. `app/pii.py` có pattern cho thẻ thanh toán, CCCD, điện thoại VN (`0`, `84`, `+84`, cho phép dấu cách/chấm/gạch), email, passport và từ khóa địa chỉ; thẻ và CCCD được xử lý trước điện thoại để chuỗi thẻ không bị nhận nhầm.
- **Cách kiểm chứng kết quả:** `python scripts/validate_logs.py` từ 30/100 lên 100/100 sau khi đổi tên log baseline; `pytest` 42 passed, trong đó có `tests/test_pii.py` (email, điện thoại, CCCD, thẻ, passport, địa chỉ) và `tests/test_correlation_and_scrub.py` (ID được sinh/nhận, ID lạ bị thay, log đủ metadata và không rò giữa hai request, file log không chứa email/số điện thoại/số thẻ gốc). Đã mở `data/logs.jsonl` và thấy `[REDACTED_EMAIL]`, `[REDACTED_PHONE_VN]`, `[REDACTED_CREDIT_CARD]` thay cho dữ liệu gốc.

## 5. Tracing và prompt versioning

- **Cách xác nhận traces do chính tôi tạo trong project cá nhân:** Trace nằm trong project Langfuse cá nhân `day13-k4-l3a-2A202602493` (organization `caovancuong611's Organization`, tài khoản `caovancuong611`), dùng cặp API key của chính project này trong `.env` (không commit). Mỗi trace tạo bởi request tôi gọi vào `/chat` trên máy mình và mang `correlation_id` trùng với dòng log local (ví dụ `req-66e16df4`, `req-07ce8d3f`); trang Tracing hiện 14 trace (42 observation: 14 `lab-agent-run`, 14 `retrieval`, 14 `llm-generate`), environment `dev`.
- **Cấu trúc root/retrieval/generation observations:** root `lab-agent-run` (agent) có hai con: `retrieval` (loại `retriever`, ghi số tài liệu và input đã scrub) và `llm-generate` (loại `generation`, ghi `model`, `usage_details` input/output/total, `cost_details` USD, input/output đã scrub PII, metadata `ttft_ms`). Khi `retrieve()` ném lỗi thì span `retrieval` tự được đánh dấu ERROR. Đã kiểm tra quan hệ cha–con bằng OpenTelemetry in-memory exporter trước khi đẩy lên Langfuse.
- **Cách nối trace với log:** cùng một `correlation_id`: log ghi ID này ở mọi dòng, còn trace có nó trong metadata (`propagate_attributes(metadata={"correlation_id": ...})`). Từ một log bất thường, tìm trace Langfuse có metadata `correlation_id` tương ứng.
- **Prompt name:** `day13-chat`
- **Version/label baseline:** `day13-chat` **v1**, label `baseline` (ban đầu cũng là `production`). Nội dung: `Feature={{feature}} / Docs={{docs}} / Question={{message}}`, không giới hạn độ dài câu trả lời.
- **Version/label candidate:** `day13-chat` **v2**, label `candidate` (và `latest`). Thêm dòng `Answer in at most 3 sentences.` vào cuối prompt.
- **Trace ID của mỗi version:** xem bảng bên dưới (Trace ID là chuỗi hex của Langfuse, `correlation_id` là ID của app trong log).

  | Lần chạy     | Label chạy          | Prompt version | correlation_id | Trace ID                           |
  | ------------ | ------------------- | -------------- | -------------- | ---------------------------------- |
  | Baseline     | `baseline`          | v1             | `req-07ce8d3f` | `aaa3f55029bedb387dc7d2ef4dd80dcb` |
  | Candidate    | `candidate`         | v2             | `req-2bdc8418` | `f7b6fcaf1d594e0c2c4079a0ac44b18b` |
  | Sau promote  | `production` (= v2) | v2             | `req-0aefadcc` | `6793c1da6f770a4b537382ba1cd5529c` |
  | Sau rollback | `production` (= v1) | v1             | `req-66e16df4` | `b419ecdb49ff2526038ed4a974b33a37` |

- **Cách promote và rollback `production`:** Trên Langfuse vào Prompts → `day13-chat` → chọn version → popover **Prompt labels** → tick `production` → xác nhận **Promote to production?**. Promote: gắn `production` cho v2 (v1 chỉ còn `baseline`), ảnh `evidence/10-prompt-promote-v2.png`. Rollback: gắn lại `production` cho v1 (v2 còn `latest` + `candidate`), ảnh `evidence/10-prompt-rollback.png`. App đọc prompt theo `LANGFUSE_PROMPT_LABEL` trong `.env` (đặt `production`) và cache prompt 60 giây, nên sau khi đổi label tôi restart uvicorn để có hiệu lực ngay. Kiểm chứng bằng cùng một câu hỏi `What is your refund policy?`: trace sau promote hiện `day13-chat (v2)`, trace sau rollback hiện `day13-chat (v1)`; metadata trace có `prompt_label`, `prompt_version`, `prompt_source=langfuse`. Không cần sửa code hay deploy lại để rollback.

## 6. Dashboard, SLO và alerts

- **Dashboard và sáu panel:** `python scripts/build_dashboard.py` đọc `data/logs.jsonl` và `config/dashboard.yaml` rồi sinh `submission/evidence/dashboard.html`: Latency (P50/P95/P99 và TTFT P95, threshold P95 ≤ 3000 ms), Traffic (request/phút), Errors (error rate, phân rã `error_type`, Retrieval success), Cost (tổng và theo phút), Tokens (input/output) và Quality (trung bình). Mỗi panel có đơn vị, cửa sổ 60 phút, đường threshold, trạng thái đạt/vượt ngưỡng (có chữ, không chỉ màu) và bảng số liệu.
- **SLO và lý do chọn:** 99,5% request trong 28 ngày phải thành công và có `latency_ms` ≤ 2000 ms. Baseline của tôi: P95 ≈ 151 ms, TTFT P95 = 50 ms, lỗi 0%. Tôi siết ngưỡng từ 3000 xuống 2000 ms vì sự cố `rag_slow` chỉ cộng thêm 2,5 s (đo được P95 ≈ 2657 ms), nên vẫn lọt qua ngưỡng 3000 ms dù chậm gấp ~17 lần baseline. Panel latency trong dashboard vẫn giữ 3000 ms theo contract của đề.
- **Cách tính error budget:** 100% − 99,5% = 0,5% số request. Ví dụ 10.000 request/28 ngày cho phép tối đa 50 request lỗi hoặc chậm hơn 2000 ms; tính theo thời gian, 0,5% của 40.320 phút ≈ 201,6 phút (≈ 3,4 giờ). Burn rate = tỷ lệ request xấu quan sát được / 0,5%.
- **Ba alert và runbook tương ứng:** (1) `slow_responses_p95_breach`, P2, P95 > 2000 ms trong 5 phút; (2) `request_errors_or_retrieval_down`, P1, error rate > 2% hoặc retrieval success < 90% trong 3 phút; (3) `cost_per_request_spike`, P2, chi phí trung bình > 0,005 USD/request trong 10 phút (baseline ≈ 0,0022). Cả ba đều symptom-based, gửi Slack `#day13-k4l3a-alerts`, có owner và runbook ba bước Metrics → Logs → Traces trong `docs/alerts.md`. Đã kiểm tra bằng practice scenario: `rag_slow` cho P95 ≈ 2657 ms với TTFT không đổi; `tool_fail` cho 100% request lỗi; `cost_spike` cho chi phí/request ≈ 0,0066 USD.

## 7. Điều tra challenge

- **Challenge ID:** `day13-k4-l3a-monitoring-llmops-v1` (cohort K4, incident `rag_slow`, seed 1311, feature `monitoring`, ngưỡng 2000 ms). Chạy bằng `python scripts/inject_incident.py` và `python scripts/load_test.py --challenge --concurrency 5` với `config/challenge.json` nguyên bản do Lab Coach gửi (file không được commit).
- **Khoảng thời gian điều tra:** 2026-09-29 từ 17:22:04 (log `incident_enabled`) đến 17:22:26 (giờ Việt Nam, UTC+7), 5 request thuộc session `k4-l3a-challenge-s01` … `s05`; phân tích metric, log, trace trong khoảng 17:22–17:30.
- **Triệu chứng từ metrics:** Latency tăng vọt: cả 5/5 request vượt ngưỡng 2000 ms; P50 = 2656 ms, P95 = P99 = 5969 ms (baseline của tôi P95 ≈ 151 ms). TTFT P95 vẫn 50 ms, error rate 0%, retrieval success 100%, chi phí trung bình ≈ 0,0020 USD/request và token bình thường, nên chỉ có **độ trễ** bất thường, không có lỗi hay tăng chi phí. Bốn request có latency gần như bằng nhau (2652–2656 ms); request đầu tiên (`req-bfcfab33`) 5969 ms, dài hơn khoảng 3,3 s so với bốn request còn lại, phần chênh này tôi chưa điều tra riêng. Evidence: `evidence/12-incident-metric.png`.
- **Log line và correlation ID liên quan:** `req-793c21be` (session `k4-l3a-challenge-s01`, feature `monitoring`), log `response_sent` lúc 2026-09-29T10:22:26Z (= 17:22:26 giờ VN): `latency_ms=2656`, `ttft_ms=50`, `tool_success=true`, `tokens_in=38`, `tokens_out=87`, `cost_usd=0.001419`. Bốn request còn lại có `latency_ms` 2652–2656 (và 5969 cho request đầu tiên), đều `tool_success=true`, không có log mức error. Evidence: `evidence/13-incident-log.png`.
- **Trace ID và span gây ảnh hưởng:** Trace ID `4fe6d08f9b0651d40acbb805ff4780cc` (2,66 s tổng, 125 token, 0,001419 USD; khớp `req-793c21be` ở token `38 + 87` và chi phí). Trong Timeline: `lab-agent-run` 2,66 s, span **`retrieval` 2,50 s (≈ 94% tổng thời gian)**, span `llm-generate` chỉ 155 ms. Span gây ảnh hưởng là `retrieval`. Evidence: `evidence/14-incident-trace.png`.
- **Root cause:** Bước truy xuất tài liệu (RAG `retrieval`) bị làm chậm thêm khoảng 2,5 s khi sự cố `rag_slow` được bật (`STATE["rag_slow"]` trong `app/mock_rag.py`). Ba tín hiệu cùng chỉ về nguyên nhân này: (1) metric cho thấy latency vượt ngưỡng trong khi TTFT không đổi và không có lỗi; (2) log của `req-793c21be` có `latency_ms=2656` so với baseline ≈ 150 ms; (3) trace cùng `correlation_id` cho thấy `retrieval` chiếm 2,50 s trong 2,66 s còn `llm-generate` chỉ 155 ms. Vì vậy nguyên nhân không nằm ở LLM hay ở lỗi ứng dụng.
- **Fix action:** Tắt sự cố bằng `python scripts/inject_incident.py --disable` (đọc incident từ `config/challenge.json`), sau đó gửi lại một request và kiểm tra `latency_ms` trở về mức baseline (vài trăm ms) và span `retrieval` ngắn lại. Nếu là sự cố thật ở dịch vụ RAG: chuyển tạm sang câu trả lời với tài liệu dự phòng (fallback) hoặc bỏ qua retrieval cho tới khi dịch vụ phục hồi.
- **Preventive measure:** (1) Đặt timeout cho `retrieval` (ví dụ 800 ms) kèm fallback để một dependency chậm không kéo cả request; (2) ghi thêm thời gian retrieval (`retrieval_ms`) vào log và thêm vào dashboard để chỉ ra ngay nguồn chậm mà không cần mở trace; (3) giữ alert `slow_responses_p95_breach` (P95 > 2000 ms trong 5 phút) và runbook Metrics → Logs → Traces trong `docs/alerts.md`; (4) chạy `load_test.py` với các scenario practice định kỳ để phát hiện hồi quy độ trễ; (5) cân nhắc cache kết quả retrieval cho các câu hỏi lặp lại.

## 8. Giải thích và tự đánh giá

- **Một quyết định kỹ thuật quan trọng và lý do:** đặt scrub_event trước bước ghi file/render để PII không bao giờ vào log; hoặc siết SLO từ 3000 xuống 2000 ms vì rag_slow chỉ cộng ~2,5 s nên vẫn lọt ngưỡng 3000.
- **Một lỗi/blocker đã gặp:**Python 3.14 không cài được pydantic-core (thiếu Rust) nên phải dùng Python 3.12; hoặc lỗi 401 Langfuse do key/region; hoặc tìm trace bằng req-... không ra vì đó là correlation_id chứ không phải Trace ID.
- **Cách tìm nguyên nhân và xử lý:**"No results", nhận ra hai loại ID khác nhau, chuyển sang xem Metadata của trace để đối chiếu correlation_id.
- **Cách hiểu luồng Metrics → Logs → Traces:**metric cho biết có chuyện (P95 vượt 2000 ms, TTFT không đổi), log cho biết request nào (req-793c21be, latency_ms=2656), trace cho biết bước nào (retrieval 2,50 s trên tổng 2,66 s). Giải thích vì sao thiếu một tầng thì khó kết luận (ví dụ chỉ có metric thì không biết span nào chậm).
- **Vai trò của prompt version, token/cost, SLO hoặc rollback trong vận hành LLM:**promote v2 rồi rollback v1 chỉ bằng đổi label production mà không deploy lại; token/cost trong llm-generate giúp phát hiện cost_spike; SLO và error budget cho biết khi nào cần dừng thay đổi.
- **Điều quan trọng nhất đã học:**phải nối ba tín hiệu bằng cùng một correlation_id, hoặc "không được kết luận khi chưa có metric, log và trace cùng chỉ một hướng".
- **Hạn chế hoặc phần chưa hoàn thành, nếu có:**ví dụ request đầu tiên req-bfcfab33 chậm 5969 ms mà chưa điều tra, alert chưa gửi Slack thật mà chỉ cấu hình, server xử lý tuần tự nên latency đo qua HTTP cao hơn latency trong agent.

## 9. Checklist trước khi nộp

- [ ] Kết quả và evidence thuộc commit SHA cuối.
- [ ] Tất cả ảnh/output mở được bằng đường dẫn tương đối.
- [ ] Incident evidence nối đúng metric → log → trace.
- [ ] Trace/prompt evidence thuộc project Langfuse cá nhân và ảnh không lộ key/secret.
- [ ] Repository chạy lại được theo README.
- [ ] Không có secret, API key, PII thô hoặc evidence của người khác/lớp khác.
- [ ] URL repo và commit SHA cuối đã được nộp trên LMS/Codelabs.
