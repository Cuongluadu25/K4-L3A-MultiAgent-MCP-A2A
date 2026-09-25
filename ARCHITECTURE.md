# L3A Architecture Record

Team phải cập nhật tài liệu này cùng source. Mục tiêu là mô tả quyết định có thể kiểm chứng, không ghi prompt bí mật hoặc chain-of-thought.

## 1. System overview

Luồng đầy đủ từ input tới submission:

```text
inputs/L3A_CASE_XXX.json
        │
        ▼
   CLI (_run)  ──emit──▶ case_received
        │
        ▼
  ┌─────────────────────────────────────────────────────────┐
  │ coordinator  (src/student_agent/workflow.py)            │
  │                                                         │
  │  emit task_assigned ×3 ─────────────┐                   │
  │                                     ▼                   │
  │   order-item-agent ──▶ payment-agent ──▶ shipment-agent │
  │        │  emit handoff (mỗi agent) │                    │
  │        └────────────┬───────────────┘                   │
  │                     ▼                                   │
  │              policy-agent  ──▶ emit policy_decided      │
  │                     │                                   │
  │                     ▼                                   │
  │            decision engine (src/student_agent/decision.py)
  │                     │                                   │
  │                     ▼                                   │
  │              verifier agent ──▶ emit verification_completed
  └─────────────────────┬───────────────────────────────────┘
                        ▼
              outputs/L3A_CASE_XXX.json
                        │
        CLI ──emit──▶ case_finalized
```

Mọi lần đọc dữ liệu đều đi qua **một** cửa duy nhất: `CaseContext.fetch()`
(`src/student_agent/evidence.py`). Không module nào khác được phép gọi
`gateway.call()` trực tiếp. Đây là điểm kiểm soát cho cả `case_id` scope lẫn
`tool_result_consumed` trace.

`case_received` và `case_finalized` do CLI phát, workflow **không** phát lại —
tránh trùng event khiến trace bị coi là không hợp lệ.

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Output/handoff |
| --- | --- | --- | --- |
| Coordinator (`workflow.py`) | `case` dict + gateway + trace | Tạo `CaseContext`, phân việc, chạy specialist tuần tự, gọi decision engine, gọi verifier, ráp output theo schema | `task_assigned` → `handoff` → `policy_decided` → `verification_completed` → output dict |
| Order/item (`agents/order_item.py`) | `case_id`, `order_id` | Dòng order, dòng item, seller; tính tổng price/freight, gom `item_ids`/`seller_ids`, shipping limit | `Finding(agent="order-item-agent")`, facts `order.*`, `items.*` |
| Payment (`agents/payment.py`) | `case_id`, `order_id` | Payment rows + lifecycle events; phát hiện duplicate signature, reconciliation mismatch đang mở, trạng thái refund | facts `payments.*`, `refund.*` |
| Shipment (`agents/shipment.py`) | `case_id`, `order_id` | Timestamp giao hàng, shipping limit, shipment events; quyết định trễ **theo timestamp** | facts `shipment.*` |
| Policy (`agents/policy.py`) | `case_id`, `policy_version` | Lấy policy template, expose rule theo từng issue code | `policy.version`, `policy.currency`, `policy.rule_codes`, `policy.rules` |
| Verifier (`agents/verifier.py`) | output đã ráp + findings + registry | Kiểm tra bất biến trước khi finalize; trả danh sách vi phạm | `list[str]` mã vi phạm → `verification_completed` |
| Decision engine (`decision.py`) | findings + claims + registry | Biến finding thành một phán quyết duy nhất | `Decision` |

### Quyền gọi tool theo agent

Mỗi specialist chỉ được gọi đúng nhóm tool thuộc domain của mình. Không agent
nào có quyền truy vấn toàn bộ 10 tool.

| Agent | Tool được phép | Tool KHÔNG dùng |
| --- | --- | --- |
| Order/item | `get_order`, `get_order_items`, `get_sellers` | toàn bộ nhóm payment/shipment/policy |
| Payment | `get_order_payments`, `get_payment_timeline`, `get_refund_timeline` | nhóm order/shipment/policy |
| Shipment | `get_shipment_summary` | nhóm order/payment/policy |
| Policy | `get_policy` | tất cả nhóm còn lại |
| Coordinator / Verifier | không gọi tool trực tiếp | — |

Hai tool `get_customer_history` và `get_product_context` **không được dùng**:
không có issue code nào trong `l3a-output-v2` cần tới chúng, nên gọi thêm chỉ
tạo evidence thừa và làm loãng tỉ lệ evidence relevance.

## 3. A2A protocol

**Message envelope.** Trao đổi giữa các agent không dùng envelope JSON tự chế mà
dùng thẳng `Finding` (`src/student_agent/models.py`) — dataclass nội bộ chứa
`agent`, danh sách `Fact(key, value, evidence_refs)`, `status`, `warnings`.
Chọn dataclass thay vì dict để mọi fact bắt buộc mang theo `evidence_refs`; không
thể tạo fact "trần" không truy vết được.

**Correlation.** Mọi bước trong một case chia sẻ đúng một `CaseContext`, giữ
`case_id`, `order_id`, `policy_version` và registry. Không có state toàn cục
giữa các case — registry được tạo mới mỗi case và không bao giờ tái sử dụng, đây
là cơ chế chống `cross_scope_evidence_ref`.

**Điều kiện handoff.**

- Coordinator → specialist: phát `task_assigned` trước khi agent chạy, kèm
  `decision_code="COLLECT_EVIDENCE"` và `order_id`.
- Specialist → policy: phát `handoff` **sau khi** agent trả `Finding`, với
  `decision_code` = `finding.status.upper()` (`OK` hoặc `INSUFFICIENT_EVIDENCE`)
  và `evidence_refs` là refs agent đó thực sự dùng (tối đa 20).
- Policy → verifier: `policy_decided` mang `case_status`, `refund_brl`,
  `confidence`; nếu policy không lấy được thì phát thêm `handoff` với
  `decision_code="POLICY_UNAVAILABLE"`.

**Timeout.** Không có timeout ở tầng A2A: mọi specialist chạy trong cùng một
event loop và bị chặn bởi retry của `fetch()` (mục 5). Giới hạn thời gian thực
tế nằm ở tầng MCP call.

**Chống vòng lặp.** Đồ thị agent là DAG cố định, không có agent nào gọi ngược
lên coordinator hay gọi lại agent khác. Số bước là hằng số theo mỗi case
(1 policy call + 1 verify), nên không thể phát sinh vòng lặp.

**Chỉ trace sự kiện quan sát được.** `attributes` của trace chỉ chứa số đếm,
mã trạng thái và độ tin cậy. Không có chain-of-thought, không có nội dung suy
luận, không có nội dung message của khách hàng trong trace.

## 4. Evidence lifecycle

1. **Call.** `CaseContext.fetch(tool_name, actor=..., **args)` gọi
   `gateway.call(tool_name, case_id=self.case_id, **args)`. `case_id` được chèn
   tự động — không call site nào truyền tay, nên không thể quên (nguyên tắc 1
   của MCP Gateway).
2. **Validate.** Envelope phải có `evidence_ref` kiểu `str` **và** `domain` kiểu
   `str`; thiếu một trong hai thì ném `ValueError` chứ không đoán giá trị mặc
   định.
3. **Register.** `EvidenceRegistry.register()` lưu theo `evidence_ref`. Nếu một
   ref đã tồn tại nhưng `domain` hoặc `data` khác nội dung cũ, registry ném
   `ValueError` — chặn việc tái sử dụng/sửa đổi ref (nguyên tắc 2).
4. **Trace.** Ngay sau khi register, phát `tool_result_consumed` với
   `actor` = specialist yêu cầu, `tool_name`, `target` = domain,
   `evidence_refs=[evidence_ref]`, `attributes={"domain":..., "attempts":...}`
   (nguyên tắc 4). `attempts` ghi lại số lần thử, để retry không bị che giấu.
5. **Cite.** `decision._cite(ctx, *domains)` chỉ trả về ref **có trong registry
   của chính case đó**, đã lọc theo domain liên quan và cắt còn 30 (nguyên tắc
   3). Không có đường nào để một `evidence_ref` không tồn tại lọt vào output.
6. **Repair.** `workflow._repair()` là chốt chặn cuối: quét lại `evidence_refs`
   và `claim_assessments[].evidence_refs`, loại bỏ mọi ref không có trong
   registry. Nếu verifier báo lỗi, output được sửa rồi verify lại.
7. **Cách ly giữa các case.** Registry thuộc `CaseContext`, mà `CaseContext`
   được dựng mới trong `solve_case()` cho từng case. Không có cache dùng chung,
   nên ref của case A không thể xuất hiện trong output của case B.

## 5. Failure policy

| Failure | Retry? | Fallback | Trace event/code |
| --- | --- | --- | --- |
| MCP timeout / lỗi mạng tạm thời | Có — tối đa 6 lần, backoff `0.8s × (lần thử)`, chặn trần 4s (≈12s kiên nhẫn/call) | Nếu hết lượt và tool nằm trong `TOLERATE_TOOL_ERROR` → coi như "không có dữ liệu", trả `None`; ngược lại ném lỗi | `tool_result_consumed` với `attributes.attempts` > 1 |
| `get_refund_timeline` báo lỗi vì đơn không có refund | Không (đã thử hết lượt) | Trả `None` + ghi warning `get_refund_timeline:no_data` vào `ctx.warnings`; payment agent bỏ qua nhánh refund | `handoff` với `decision_code="OK"`, warning nằm trong finding |
| Order/item không lấy được | Có (theo luật trên) | `Finding.status = "insufficient_evidence"`, không tạo fact nào | `handoff` với `decision_code="INSUFFICIENT_EVIDENCE"` |
| Policy không lấy được | Có | `decision.decide()` dùng rule rỗng → `case_status="needs_investigation"`, refund 0 | `handoff` `POLICY_UNAVAILABLE` + `policy_decided` |
| Xung đột nguồn (claim vs evidence, mismatch, event vs timestamp) | Không | Ưu tiên evidence; ghi lại xung đột vào `data_conflicts` kèm `resolution_code` | `policy_decided`; chi tiết nằm trong output |
| Specialist trả kết quả không hợp lệ | Không | Verifier trả mã vi phạm → `_repair()` → verify lại | `verification_completed` với `decision_code="REPAIRED"` hoặc `"FAIL"` |
| Output vẫn sai sau khi sửa | Không | Không chặn ghi file; lỗi schema sẽ do CLI phát hiện và dừng cả lượt chạy | `verification_completed` với `decision_code="FAIL"` |

Retry chỉ áp dụng cho lỗi mạng (idempotent: cùng `case_id` + cùng tham số, MCP
read-only nên thử lại không gây tác dụng phụ). **Không** có nhánh nào biến
evidence thiếu thành dữ liệu phỏng đoán: thiếu bằng chứng thì kết luận là
`insufficient_evidence`, không bịa ra giá trị.

## 6. Verification invariants

`agents/verifier.py` chạy trước khi output được ghi. Danh sách kiểm tra:

| Nhóm | Bất biến kiểm tra |
| --- | --- |
| Schema | Đủ 9 field bắt buộc ở top level |
| Case identity | `case_id` của output khớp `ctx.case_id` (round-trip) |
| Evidence ownership | Mọi ref trong `evidence_refs` và trong `claim_assessments[].evidence_refs` đều tồn tại trong registry của case |
| Evidence tối thiểu | Output phải trích ít nhất một ref; không có ref trùng lặp |
| Entity scope | `item_ids`, `seller_ids`, `order_ids` đều phải là entity có thật của case; `party_id` của seller phải nằm trong `items.seller_ids` |
| Claim linkage | Mỗi claim trong input có đúng một entry trong `claim_assessments` (`claim_id` one-for-one) |
| Money totals | `recommended_refund_brl` khớp tổng `refund_lines[].amount_brl`; mọi dòng refund không âm |
| Responsibility / action | `case_status` nhất quán với refund và action (`no_action` ⇒ refund 0); `resolution_actions` không trùng và không rỗng |
| Confidence bounds | `confidence` nằm trong [0,1]; case `needs_investigation` không được vượt 0.85 |

Vi phạm được trả về dưới dạng mã (ví dụ `missing_field`, `unknown_evidence_ref`,
`entity_out_of_scope`), và được ghi vào `verification_completed.attributes`.
`attributes` chỉ chứa giá trị vô hướng nên danh sách vi phạm được nối thành một
chuỗi — schema trace không cho phép mảng trong `attributes`.

## 7. Reproducibility

**Model.** Không dùng LLM ở bất kỳ bước nào. Toàn bộ pipeline là luật tất định
trên dữ liệu MCP: chọn issue theo claim-topic + kiểm chứng bằng evidence. Cùng
một `case_id` luôn cho cùng một output. Vì vậy không có `temperature`, không có
sampling và không cần random seed.

**Dependency pinning.** `pyproject.toml` khoá theo khoảng phiên bản:
`httpx2>=2,<3`, `jsonschema[format]>=4.25,<5`, `mcp>=2,<3`,
`python-dotenv>=1.1,<2`; dev: `pytest>=8.4,<9`, `ruff>=0.12,<1`. Python >= 3.11
(môi trường chạy thực tế: CPython 3.11.9, venv tại `.venv/`).

**Concurrency limit.** Một MCP session duy nhất cho cả lượt chạy
(`connect_gateway` mở đúng một `ClientSession`). `EvidenceGateway` bọc session
đó và không được thiết kế cho truy cập song song, nên ba specialist chạy **tuần
tự** chứ không đồng thời. Đổi sang chạy song song sẽ hỏng vì cùng ghi lên một
connection.

**Chi phí mạng.** Mỗi case thực hiện 8 MCP call (3 order/item + 3 payment + 1
shipment + 1 policy). Retry làm tăng con số này khi mạng chập chờn; `attempts`
trong trace là nơi kiểm chứng.

**Lệnh chạy.**

```bash
day09 validate-inputs          # kiểm tra case-set.json + 100 input
day09 mcp-tools                # xác thực API key, liệt kê tool
day09 run                      # chạy toàn bộ 100 case → outputs/ + traces/
day09 validate                 # đối chiếu output với schema + trace
day09 package --output dist/submission.zip
```

**Giới hạn tài nguyên.** Không có biến môi trường nào điều chỉnh luồng. API key
nằm trong `.env` (đã bị `.gitignore` chặn) và không xuất hiện trong trace,
output hay tài liệu này.

## 8. Design decisions

Các quyết định dưới đây là chỗ dễ gây tranh cãi nhất, ghi lại kèm lý do.

**1. Claim topic là giả thuyết, evidence là trọng tài.** Mỗi case có đúng hai
claim: claim-a là một trong 10 issue code, claim-b luôn là
`requested_full_refund`. `decision._choose_issue()` thử claim-a trước; nếu
evidence thực sự hỗ trợ thì giữ nguyên và đặt confidence 0.95. Nếu evidence phủ
nhận, issue bị thay bằng issue mà evidence hỗ trợ, và xung đột được ghi vào
`data_conflicts` với `resolution_code="CLAIM_CONTRADICTED_BY_EVIDENCE"`.

Lý do không suy thẳng từ evidence: có những cặp case gần như trùng tín hiệu.
Ví dụ L3A_CASE_006 (`payment_mismatch`) và L3A_CASE_008 (`refund_pending`) có
cùng bộ payment/refund row; phân biệt được chúng chỉ nhờ claim topic. Ngược
lại, không thể tin claim tuyệt đối — `unsupported_claim` tồn tại chính là để
bắt trường hợp claim không có gì chống lưng.

**2. `refund_brl` lấy từ policy, `party_id` thì không.** `get_policy` trả về
cùng một template cho mọi case (đã đối chiếu 5 case khác nhau). Quyết định: ba
field `case_status`, `recommended_action`, `party_type` dùng nguyên văn từ
policy; `refund_brl` cũng lấy từ policy (pool giá trị 79/89/64/52/35/18/16/0
khớp với dải giá trị của generator ở 7/10 issue). Riêng `party_id` là
placeholder nên bị thay bằng seller thật của case (`items.seller_ids[0]`) —
`_responsible_parties()` làm việc này và verifier kiểm tra lại.

**3. Trễ giao hàng xét theo timestamp, không theo nhãn event.** Gateway có thể
trả event `delivered_late` cho đơn vẫn giao trong hạn. `late_by_timestamps` so
`delivered_customer_at` với `estimated_delivery_at`; nếu nhãn event nói trễ mà
timestamp nói không, đó là xung đột thật và được ghi vào `data_conflicts`
(`EVENT_LABEL_DISAGREES_WITH_TIMESTAMPS`). Đây cũng là cách `unsupported_claim`
lộ diện.

**4. `valid_split_payment` không bị phủ nhận bởi refund row.** Phiên bản đầu
yêu cầu case không có refund `failed`/`pending` mới được coi là split payment
hợp lệ; điều này khiến L3A_CASE_005 (claim `valid_split_payment`, evidence có 2
sequential + 0 mismatch + 0 duplicate, kèm một refund row `failed` lạc) bị đẩy
sang `refund_failed` — sai. Refund row đi kèm phần lớn case như nhiễu của
generator, nên tiêu chí split payment giờ chỉ còn: ≥2 sequential, không
mismatch, không duplicate.

**5. Specialist chạy tuần tự, không song song.** Xem mục 7 (một MCP session).
Đánh đổi: chậm hơn nhưng đúng; efficiency chiếm 0% trọng số trong L3A.

**6. Không dùng `get_customer_history` và `get_product_context`.** Hai tool này
không phục vụ issue code nào. Gọi chúng chỉ thêm ref mà không thêm căn cứ, làm
giảm chất lượng trích dẫn.
