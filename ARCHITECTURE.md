# L3A Multi-Agent Architecture Record

Tài liệu này mô tả quyết định thiết kế kiến trúc hệ thống Multi-Agent điều tra khiếu nại thương mại điện tử (L3A). Mọi quyết định đều có thể kiểm chứng được qua mã nguồn tại `src/student_agent/`, các hợp đồng JSON Schema tại `contracts/schemas/`, và trace log chuẩn hóa.

---

## 1. System Overview

Hệ thống hoạt động theo mô hình **Coordinator & Specialist Agents** với luồng xử lý tuần tự, hướng sự kiện (observable event-driven) nhằm đảm bảo tính đơn định, bảo toàn bằng chứng và tuân thủ tuyệt đối Public Contract:

```text
               ┌────────────────────────────────────────────────────────┐
               │                  inputs/<case_id>.json                 │
               └───────────────────────────┬────────────────────────────┘
                                           │
                                           ▼
               ┌────────────────────────────────────────────────────────┐
               │                   Coordinator Agent                    │
               │  - Initialize isolated EvidenceRegistry                │
               │  - Emit task_assigned to specialists                   │
               └───────────┬──────────────────────┬─────────────────────┘
                           │                      │
       ┌───────────────────┼──────────────────────┼───────────────────┐
       ▼                   ▼                      ▼                   ▼
┌──────────────┐    ┌──────────────┐      ┌───────────────┐   ┌──────────────┐
│  OrderAgent  │    │ PaymentAgent │      │ ShipmentAgent │   │ PolicyAgent  │
└──────┬───────┘    └──────┬───────┘      └───────┬───────┘   └──────┬───────┘
       │                   │                      │                  │
       └───────────────────┼──────────────────────┴──────────────────┘
                           │  (Tool calls: get_order, get_payments, etc.)
                           ▼
               ┌────────────────────────────────────────────────────────┐
               │                  MCP Evidence Gateway                  │
               │   - Validates mcp-evidence-response-v1                 │
               │   - Scopes evidence_ref to case registry               │
               │   - Emits tool_result_consumed trace events            │
               └───────────────────────────┬────────────────────────────┘
                                           │
                                           ▼
               ┌────────────────────────────────────────────────────────┐
               │                Decision Reasoning Engine               │
               │   - Correlates findings with policy rules              │
               │   - Determines primary_issue & calibrated confidence   │
               │   - Emits policy_decided trace event                   │
               └───────────────────────────┬────────────────────────────┘
                                           │
                                           ▼
               ┌────────────────────────────────────────────────────────┐
               │                     Verifier Agent                     │
               │   - Validates l3a-output-v2 contract schema            │
               │   - Asserts financial total consistency                │
               │   - Emits verification_completed trace event           │
               └───────────────────────────┬────────────────────────────┘
                                           │
                     ┌─────────────────────┴─────────────────────┐
                     ▼                                           ▼
       ┌───────────────────────────┐               ┌───────────────────────────┐
       │   outputs/<case_id>.json  │               │    traces/trace.jsonl     │
       └───────────────────────────┘               └───────────────────────────┘
```

---

## 2. Agent Ownership & Tool Permissions

Nhằm tuân thủ nguyên tắc đặc quyền tối thiểu (Least Privilege), mỗi agent chỉ được cấp quyền truy vấn đúng tập MCP tools thuộc phạm vi trách nhiệm:

| Actor | Input | Trách nhiệm | Output / Handoff | Tool Permissions |
| :--- | :--- | :--- | :--- | :--- |
| **`coordinator`** | `case: dict[str, Any]` | Điều phối vòng đời ca xử lý, giao việc cho specialist agents, chuyển tiếp kết quả đến verifier. | Task assignments, output hoàn chỉnh. | Không gọi tool trực tiếp. |
| **`order_agent`** | `CaseContext` (`claimed_order_id`) | Xác minh tính hợp lệ và trạng thái đơn hàng (`order_status`), mốc thời gian mua hàng, danh sách item và seller. | `Finding` chứa `order_status`, `item_ids`, `seller_ids`, `total_price`, `total_freight`. | `get_order`, `get_order_items`, `get_sellers`. |
| **`payment_agent`** | `CaseContext` (`claimed_order_id`) | Xác định hình thức thanh toán, phát hiện split payment, duplicate charge, reconciliation mismatch, và kiểm tra tiến độ hoàn tiền. | `Finding` chứa `payments`, `events`, `refund_events`, cờ duplicate/mismatch/refund status. | `get_payment_timeline`, `get_order_payments`, `get_refund_timeline`. |
| **`shipment_agent`** | `CaseContext` (`claimed_order_id`) | Giám sát hành trình vận chuyển, kiểm tra thời hạn giao hàng của seller (`shipping_limits`), phát hiện trễ hạn và quy trách nhiệm (`actor`). | `Finding` chứa ngày giao dự kiến/thực tế, sự kiện `delivered_late`, `late_actor`. | `get_shipment_summary`. |
| **`policy_agent`** | `CaseContext` (`policy_version`) | Truy xuất điều khoản chính sách chính thức, ánh xạ nguyên nhân và mức bồi hoàn theo quy tắc chuẩn. | `Finding` chứa bảng quy tắc `rules` (trạng thái, hành động khuyến nghị, số tiền hoàn `refund_brl`, trách nhiệm). | `get_policy`. |
| **`verifier_agent`** | `output: dict`, `CaseContext` | Kiểm tra độc lập toàn diện mọi bất biến nghiệp vụ và JSON Schema trước khi xuất xưởng. | Verification status (`VERIFIED_PASS`) hoặc raise `ValueError`. | Không gọi MCP tool (thuần suy luận và kiểm định). |

---

## 3. A2A Protocol (Agent-to-Agent)

- **Message Envelope**: Mọi trao đổi giữa Coordinator và Specialist Agents đều đóng gói trong cấu trúc `Finding`:
  ```python
  @dataclass(frozen=True)
  class Finding:
      agent: str
      facts: dict[str, Any]
      evidence_refs: tuple[str, ...]
      status: str  # "ok" | "insufficient_evidence" | "not_found"
  ```
- **Correlation**: Mọi truy vấn và trace event liên kết chặt chẽ thông qua `case_id`. Mỗi ca điều tra được cấp một `CaseContext` độc lập kèm một `EvidenceRegistry` riêng biệt.
- **Luồng Handoff & Chống Vòng Lặp**: Luồng thực thi diễn ra theo mô hình DAG đơn hướng tuần tự:
  1. `coordinator` $\to$ `order_agent` $\to$ `handoff` về `coordinator`.
  2. `coordinator` $\to$ `payment_agent` $\to$ `handoff` về `coordinator`.
  3. `coordinator` $\to$ `shipment_agent` $\to$ `handoff` về `coordinator`.
  4. `coordinator` $\to$ `policy_agent` $\to$ `handoff` về `coordinator`.
  5. `coordinator` $\to$ `decision engine` $\to$ `policy_decided`.
  6. `coordinator` $\to$ `verifier_agent` $\to$ `verification_completed`.
  Mô hình DAG tuần tự loại bỏ 100% nguy cơ dead-lock và vòng lặp vô tận (infinite recursion).
- **Observable Trace Mapping**:
  Mọi sự kiện quan sát được ghi tức thời vào `traces/trace.jsonl` theo schema `day09-trace-event-v1`:
  - `task_assigned`: Phát ra khi Coordinator giao việc cho Specialist.
  - `tool_result_consumed`: Phát ra ngay khi Specialist nhận và xác thực evidence từ MCP Gateway.
  - `handoff`: Phát ra khi Specialist hoàn tất nhiệm vụ và chuyển giao `Finding`.
  - `policy_decided`: Phát ra khi áp dụng quy tắc chính sách để đưa ra phán quyết (`decision_code = primary_issue`).
  - `verification_completed`: Phát ra khi Verifier hoàn tất kiểm định (`decision_code = "VERIFIED_PASS"`).
  *(Lưu ý: `case_received` và `case_finalized` do CLI runtime phát ra bao bọc quanh `solve_case`)*.

---

## 4. Evidence Lifecycle

1. **Khởi tạo Registry cách ly**: Khi mở ca (`solve_case`), một instance `EvidenceRegistry(case_id)` được khởi tạo mới. Evidence của case này không thể bị truy cập hay trộn lẫn vào case khác.
2. **Xác thực Envelope**: Mọi phản hồi từ MCP Gateway được validate tự động theo schema `day09-mcp-evidence-v1` (`evidence_ref`, `result_hash`, `domain`, `data`).
3. **Định dạng chuẩn của Ref**: Mọi `evidence_ref` bắt buộc khớp regex `^ev_[A-Za-z0-9_-]{20,96}$`. Mọi ref không hợp lệ bị từ chối ngay lập tức.
4. **Audit & Trace Alignment**: Khi evidence được nạp vào bộ nhớ (`ctx.fetch`), hệ thống lập tức emit `tool_result_consumed` ghi nhận `actor`, `tool_name`, và `evidence_refs`.
5. **Gán Evidence vào Output**:
   - `evidence_refs` tổng hợp trong output chỉ lấy duy nhất từ các ref đã được ghi nhận trong `EvidenceRegistry` của chính ca đó (tối đa 30 refs).
   - Trong `claim_assessments`, từng claim chỉ trích dẫn các evidence_ref thực sự hỗ trợ nhận định.

---

## 5. Failure Policy & Resilience

| Sự cố | Retry Policy | Fallback Behavior | Trace Event / Code |
| :--- | :--- | :--- | :--- |
| **MCP Network Timeout / ReadError** | Retry 1 lần với exponential backoff (0.2s), tự động reconnect session. | Nếu vẫn timeout, đánh dấu finding `status="insufficient_evidence"`. | `Finding.status = "insufficient_evidence"` |
| **Tool Execution Error (vd: get_refund_timeline không có data)** | 0 retry (fail-fast đối với các tool lịch sử tùy chọn). | Bỏ qua an toàn, ghi nhận không có timeline hoàn tiền. | Tiếp tục luồng xử lý bình thường. |
| **Mâu thuẫn dữ liệu (Customer vs MCP Ground Truth)** | Không retry. | Luôn ưu tiên MCP Authoritative Data; ghi nhận discrepancy vào `data_conflicts`. | `resolution_code = "CUSTOMER_CLAIM_NOT_SUBSTANTIATED"` |
| **Thiếu dữ liệu cốt lõi (Không lấy được Order)** | Retry 1 lần. | Fail-soft: `primary_issue = "insufficient_evidence"`, `case_status = "needs_investigation"`, `confidence = 0.30`. | `policy_decided` với code `insufficient_evidence` |

**Nguyên tắc vàng**: Tuyệt đối không phỏng đoán, không tự sinh `evidence_ref` giả, không chuyển missing evidence thành dữ liệu bịa đặt.

---

## 6. Verification Invariants

Trước khi ghi nhận output cuối cùng, `VerifierAgent` thực thi kiểm tra nghiêm ngặt 7 bất biến nghiệp vụ:

1. **JSON Schema Compliance**: Output bắt buộc thỏa mãn 100% schema `day09-l3a-output-v2` (`Draft202012Validator`, `additionalProperties = False`).
2. **Case ID Integrity**: `output["case_id"] == ctx.case_id`.
3. **Evidence Provenance & Scope**: Tất cả `evidence_refs` phải tồn tại trong `EvidenceRegistry` của ca hiện tại, khớp regex chuẩn, độ dài $\le 30$.
4. **Financial Consistency**:
   $$\text{recommended\_refund\_brl} \equiv \sum_{i} \text{refund\_lines}[i].\text{amount\_brl}$$
   Sai số tuyệt đối cho phép $|A - B| \le 0.01$. Nếu số tiền hoàn $= 0.0$, `refund_lines` bắt buộc rỗng.
5. **Root Cause Standard**: Mọi `cause_code` phải khớp mẫu `^[A-Z][A-Z0-9_]{2,79}$` (ví dụ: `CAUSE_ORDER_CANCELED_AFTER_PAYMENT`). Số lượng $\le 5$.
6. **Party Attribution**: `party_type` thuộc enum hợp lệ (`seller`, `platform`, `logistics_provider`, `payment_provider`, `customer`, `unknown`). Nếu là `seller`, `party_id` được trích xuất từ dữ liệu bằng chứng thực tế.
7. **Calibrated Confidence**: Confidence nằm trong khoảng $[0.0, 1.0]$. Bằng chứng xác đáng rõ ràng $\approx 0.95$; trường hợp chờ xử lý $\approx 0.85$; thiếu bằng chứng $\approx 0.30$.

---

## 7. Reproducibility

- **Môi trường**: Python 3.11+.
- **Quản lý phiên bản**: Dependencies được khóa trong `pyproject.toml` (`httpx2`, `mcp`, `jsonschema`, `referencing`).
- **Deterministic**: Quá trình phân tích dựa hoàn toàn trên luật nghiệp vụ và dữ liệu xác thực MCP, không phụ thuộc vào LLM temperature hay random seed ngẫu nhiên.
- **Quy trình thực thi chuẩn**:
  ```bash
  # 1. Kiểm tra tính hợp lệ của inputs
  python -m student_agent.cli validate-inputs

  # 2. Khám phá các công cụ MCP Gateway
  python -m student_agent.cli mcp-tools

  # 3. Chạy toàn bộ 100 ca điều tra
  python -m student_agent.cli run

  # 4. Xác minh hợp đồng và tính toàn vẹn của artifacts
  python -m student_agent.cli validate

  # 5. Đóng gói sản phẩm nộp bài
  python -m student_agent.cli package --output dist/submission.zip
  ```
