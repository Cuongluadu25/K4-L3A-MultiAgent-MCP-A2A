# TASKBOARD — L3A Multi-Agent MCP + A2A (nhóm 3 người)

> Tài liệu điều phối nội bộ. Không nằm trong ZIP nộp bài.
> Nguồn chuẩn: `README.md`, `contracts/`, `ARCHITECTURE.md`.

---

## 0. Trạng thái repo hiện tại

| Hạng mục | Trạng thái |
| --- | --- |
| Starter kit + contracts + tests | ✅ có sẵn |
| `inputs/L3A_CASE_*.json` | ❌ chưa có (cần tải ZIP) |
| `case-set.json` | ❌ chưa có |
| `.env` (Team API Key thật) | ❌ chưa có |
| `src/student_agent/workflow.py` | ❌ `raise NotImplementedError` |
| `ARCHITECTURE.md` | ❌ toàn TODO |
| Danh sách MCP tool | ❌ chưa khám phá |

**Việc chặn tất cả (làm ngay, cả nhóm):**

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv/Scripts/activate
python -m pip install -e ".[dev]"
cp .env.example .env                # điền COMPETITION_TEAM_API_KEY thật
```

1. Đăng ký team tại `/register` trên Competition Workspace → lấy `sk-team-...`
2. Điền vào `.env` (URL đã có sẵn trong `.env.example`, chỉ cần thay key)
3. Tải ZIP input **L3A** từ GitHub Release, giải nén vào root repo
4. `day09 validate-inputs` → phải in `OK: l3a / ... / 100 cases`

---

## 1. Bản đồ điểm — quyết định thứ tự ưu tiên

| Thành phần | Trọng số | Ai phụ trách chính |
| --- | ---: | --- |
| semantic (đúng nghiệp vụ) | **45%** | C (quyết định) + B (dữ liệu đầu vào) |
| evidence (chất lượng bằng chứng) | 15% | B + A |
| provenance (khớp MCP audit) | 15% | **A** |
| consistency (nhất quán cross-field) | 10% | C |
| schema | 5% | A |
| calibration (confidence hợp lý) | 5% | C |
| workflow (trace) | 5% | A |

**Hard gate → case bị 0 điểm ngay:** `case_id_mismatch`, `unscorable_schema`,
`missing_required_evidence`, `invalid_evidence_refs`, `unknown_evidence_ref`,
`cross_scope_evidence_ref`.

→ 30% điểm (evidence + provenance) và toàn bộ hard gate nằm ở **tầng evidence của A**.
Đây là lý do A phải làm **trước** và đóng băng interface sớm.

---

## 2. Chia file để 3 người không giẫm chân nhau

`workflow.py` là file duy nhất bắt buộc, nhưng **không để 3 người cùng sửa**. Tách module:

```text
src/student_agent/
├── workflow.py           ← A (chỉ A sửa: coordinator + ráp output)
├── models.py             ← A đóng băng đầu tiên, cả nhóm đọc
├── evidence.py           ← A (registry, scope, validate)
├── extract.py            ← B (chuẩn hoá số/ngày/tiền từ MCP data)
├── agents/
│   ├── __init__.py
│   ├── order_item.py     ← B
│   ├── payment.py        ← B
│   ├── shipment.py       ← B
│   ├── policy.py         ← C
│   └── verifier.py       ← C
├── decision.py           ← C (phân loại issue + tính refund)
└── ARCHITECTURE.md       ← C
```

**Quy tắc git:** mỗi người chỉ commit file mình sở hữu. `models.py` chỉ A sửa sau khi đóng băng.

---

## 3. Interface đóng băng (A làm đầu tiên — M0)

Cả B và C code song song dựa trên hợp đồng này, không cần chờ A xong.

```python
# models.py
@dataclass(frozen=True)
class Evidence:
    evidence_ref: str
    domain: str            # order|item|payment|shipment|seller|customer|product|refund|policy
    data: Any
    result_hash: str

@dataclass(frozen=True)
class Fact:
    key: str               # vd "order.status", "payment.total_brl"
    value: Any             # scalar JSON
    evidence_refs: tuple[str, ...]

@dataclass(frozen=True)
class Finding:
    agent: str
    facts: tuple[Fact, ...]
    status: str            # "ok" | "insufficient_evidence"
```

```python
# mọi specialist agent
async def run(ctx: CaseContext) -> Finding: ...

# decision engine (C)
def decide(ctx: CaseContext, findings: list[Finding]) -> Decision: ...

# verifier (C)
def verify(output: dict, ctx: CaseContext) -> list[str]: ...   # rỗng = pass
```

`CaseContext` do A cung cấp, chứa: `case_id`, `case`, `gateway`, `trace`, `registry`,
và helper `await ctx.fetch(tool_name, **args) -> Evidence` (tự validate + tự emit
`tool_result_consumed` + tự ghi vào registry).

---

## 4. Checklist theo người

### 🅰️ A — Coordinator & Evidence Integrity (đường găng)

Sở hữu: `workflow.py`, `models.py`, `evidence.py`

- [ ] **A1.** Setup môi trường + đăng ký team + tải input + `day09 validate-inputs` pass
- [ ] **A2.** `day09 mcp-tools` → ghi **tên tool thật + tham số** vào `MCP-NOTES.md`. **Không đoán tên tool.**
- [ ] **A3.** Viết `models.py` (Evidence/Fact/Finding/CaseContext) → **thông báo đóng băng** cho B, C
- [ ] **A4.** `evidence.py` — `EvidenceRegistry` **mới cho mỗi case**:
      - [ ] validate envelope trước khi nhận (schema `day09-mcp-evidence-v1`)
      - [ ] chặn trùng `evidence_ref`, chặn ref ngoài case hiện tại
      - [ ] `refs_for(*domains)` để B/C trích dẫn đúng phạm vi
- [ ] **A5.** `ctx.fetch()`: gọi `gateway.call(tool, case_id=..., **args)` → validate → ghi registry → emit `tool_result_consumed` kèm `tool_name` + `evidence_refs`  ⚠️ **MCP audit chấm điểm ở đây**
- [ ] **A6.** Coordinator trong `solve_case()`: emit `task_assigned` cho từng specialist → chạy specialists → emit `handoff` → gọi `decide()` → `verify()` → ráp output → emit `verification_completed`
      - ⚠️ **KHÔNG** emit lại `case_received` / `case_finalized` — [cli.py:49](src/student_agent/cli.py#L49) và [cli.py:60](src/student_agent/cli.py#L60) đã làm
- [ ] **A7.** Ráp output: `affected_entities` dedupe + ≤20/field; `evidence_refs` **chỉ lấy từ registry**, unique, ≤30, khớp regex `^ev_[A-Za-z0-9_-]{20,96}$`
- [ ] **A8.** Failure policy có giới hạn: timeout MCP → retry tối đa 1–2 lần (idempotent) → nếu vẫn fail thì `Finding(status="insufficient_evidence")`, **tuyệt đối không bịa dữ liệu**
- [ ] **A9.** ⚠️ **Kiểm tra concurrency**: `EvidenceGateway` dùng **một `ClientSession` duy nhất**. Gọi `call_tool` song song trên cùng session có thể lỗi. Test trước khi dùng `asyncio.gather`; nếu nghi ngờ → gọi tuần tự trong specialist
- [ ] **A10.** Chạy `ruff check .` sạch

### 🅱️ B — Domain Evidence Agents (order/item, payment, shipment)

Sở hữu: `agents/order_item.py`, `agents/payment.py`, `agents/shipment.py`, `extract.py`

- [ ] **B1.** Mở `inputs/L3A_CASE_001.json` → **catalog toàn bộ field** của case (customer message, claim, order refs, claim_id) → ghi `CASE-SHAPE.md`
- [ ] **B2.** `order_item.py`: gọi tool order + item → trích `order_status`, các mốc thời gian (purchase / approved / delivered / estimated), `item_ids`, `seller_ids`, `price`, `freight_value`
- [ ] **B3.** `payment.py`: các dòng payment (sequential, type, value, installments) → tổng payment vs tổng order → phát hiện `valid_split_payment`, `payment_mismatch`, `duplicate_charge`
- [ ] **B4.** `shipment.py`: ngày giao thực tế, ngày carrier nhận hàng, `shipping_limit_date` → kết luận `late_delivery_seller` hay `late_delivery_logistics`
- [ ] **B5.** `extract.py`: chuẩn hoá tiền về 2 chữ số thập phân + BRL, parse timestamp về UTC. **Mọi fact phải mang theo `evidence_ref` nguồn**
- [ ] **B6.** Chỉ trích dẫn evidence **thật sự hỗ trợ kết luận**. Điểm evidence là **F1** — trích thừa làm giảm precision, trích thiếu làm giảm recall
- [ ] **B7.** Fail-soft: thiếu dữ liệu → `status="insufficient_evidence"`, không suy đoán
- [ ] **B8.** Test từng agent độc lập trên 3–5 case trước khi ráp vào coordinator

### 🅲 C — Decision Engine, Verifier & Submission

Sở hữu: `agents/policy.py`, `agents/verifier.py`, `decision.py`, `ARCHITECTURE.md`

- [ ] **C1.** Bảng quyết định `findings → primary_issue` cho **đủ 11 giá trị enum**: `canceled_order_paid`, `unavailable_order_paid`, `late_delivery_seller`, `late_delivery_logistics`, `valid_split_payment`, `payment_mismatch`, `duplicate_charge`, `refund_pending`, `refund_failed`, `unsupported_claim`, `insufficient_evidence`
      - [ ] mỗi rule ghi rõ: điều kiện + evidence bắt buộc. **Đây là 45% điểm**
- [ ] **C2.** Suy ra `case_status`: `action_required` / `no_action` / `needs_investigation`
- [ ] **C3.** `ranked_causes` — từ vựng `cause_code` dạng `^[A-Z][A-Z0-9_]{2,79}$` + `rank` 1–5; `responsible_parties` dùng đúng enum `party_type`, `party_id` **lấy từ evidence** (không bịa), null nếu không xác định
- [ ] **C4.** `financial_resolution`: ⚠️ `recommended_refund_brl` **phải bằng tổng** `refund_lines[].amount_brl` (consistency check). `refund_lines` chỉ dựng từ payment evidence
- [ ] **C5.** `data_conflicts`: phát hiện mâu thuẫn nguồn (vd order_status vs payment status) → `sources` ≥2, chọn `selected_source`, ghi `resolution_code`
- [ ] **C6.** `claim_assessments`: một entry cho mỗi claim trong input (≤5), verdict đúng enum + confidence + evidence_refs
- [ ] **C7.** `resolution_actions`: ≤8, unique, ≤80 ký tự, **nhất quán với `case_status`** (`no_action` → rỗng hoặc tối thiểu)
- [ ] **C8.** `verifier.py` — invariant trước khi finalize: schema, entity scope, evidence ownership, claim linkage, tổng tiền, consistency status/refund/action, confidence ∈ [0,1], không trùng action
- [ ] **C9.** Calibration: điểm = `1 - (confidence - đúng/sai)²`. Evidence mạnh → ~0.9; trung bình → 0.5–0.6; `insufficient_evidence` → ~0.3. **Đừng để mọi case đều 1.0**
- [ ] **C10.** Điền đủ 7 mục `ARCHITECTURE.md` (system overview, agent ownership, A2A protocol, evidence lifecycle, failure policy, verification invariants, reproducibility) — **ghi quyết định kiểm chứng được, không ghi prompt/chain-of-thought**

---

## 5. Mốc tiến độ

| Mốc | Điều kiện hoàn thành | Ai chốt |
| --- | --- | --- |
| **M0** | `day09 validate-inputs` OK + có `MCP-NOTES.md` + `models.py` đóng băng | A |
| **M1** | 1 case chạy hết `solve_case()` ra output **pass schema** | A |
| **M2** | `day09 run` xong 100 case không crash, `day09 validate` pass 100/100 | A |
| **M3** | `pytest -q` + `ruff check .` sạch; trace đủ 5 lifecycle event/case | A |
| **M4** | Soát semantic theo từng `primary_issue`, siết evidence precision | B + C |
| **M5** | Verifier không còn violation; calibration hợp lý | C |
| **M6** | `ARCHITECTURE.md` xong; `day09 package` → soi ZIP → upload `/l3a` | C |

---

## 6. Lỗi cần tránh (mỗi lỗi = 0 điểm case đó)

1. Bịa `evidence_ref` hoặc sửa ref → `invalid_evidence_refs` / `unknown_evidence_ref`
2. Dùng evidence của case khác → `cross_scope_evidence_ref` (→ **tạo registry mới mỗi case**)
3. Truyền sai `case_id` vào `gateway.call()` → `case_id_mismatch`
4. Output sai schema → `unscorable_schema`
5. Thiếu evidence bắt buộc → `missing_required_evidence`
6. Tin lời khách hàng làm ground truth — **customer message không phải ground truth**, phải đối chiếu MCP

## 7. Cảnh báo vận hành

- **`pytest -q` sẽ FAIL sau khi giải nén input.** [test_release_safety.py:4-9](tests/test_release_safety.py#L4-L9) assert repo sạch (`case-set.json` không tồn tại, `inputs/*.json` rỗng). Đây là test cho repo gốc, **không phải bug của nhóm**. Đừng sửa test, đừng commit input.
- **`.gitignore` đã chặn** `case-set.json`, `inputs/*`, `outputs/*`, `traces/*`, `dist/*`, `.env` → commit sẽ không lộ key/input. Nhưng vẫn **tự kiểm tra** `git status` trước khi push.
- **`day09 run` xoá sạch `outputs/` và `traces/` mỗi lần chạy** ([cli.py:38-40](src/student_agent/cli.py#L38-L40)) → chạy lại toàn bộ 100 case, không chạy lẻ.
- **ZIP nộp bài chỉ chứa** `manifest.json`, `trace.jsonl`, `outputs/<case_id>.json`. Không source, không `.env`, không log. `day09 package` đã tự validate — nhưng vẫn mở ZIP soi lại.
- **Mọi call MCP đều bị audit** → config đúng ngay từ đầu để không mất quyền lợi.
- **Điểm cuối = 20% public + 80% private**, chọn điểm cao nhất trong các submission của team.

---

## 8. Lệnh thường dùng

```bash
day09 validate-inputs    # kiểm tra case-set + 100 input
day09 mcp-tools          # liệt kê tool thật (chạy sau khi có .env)
day09 run                # chạy workflow toàn bộ 100 case
day09 validate           # validate outputs + trace
day09 package --output dist/submission.zip
ruff check . && pytest -q
```
