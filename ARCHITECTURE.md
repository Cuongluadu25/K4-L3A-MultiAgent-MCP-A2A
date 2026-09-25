# L3A Architecture Record

Team phải cập nhật tài liệu này cùng source. Mục tiêu là mô tả quyết định có thể kiểm chứng, không ghi prompt bí mật hoặc chain-of-thought.

## 1. System overview

Vẽ hoặc mô tả luồng từ `inputs/<case_id>.json` đến MCP calls, specialist agents, verifier, output và trace.

```text
Input → Coordinator → Specialists → Verifier → Output
                         │              │
                         └── MCP ───────┴── Trace
```

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Output/handoff |
| --- | --- | --- | --- |
| Coordinator | customer request + case context | Orchestrates specialist calls, coordinates evidence collection, manages turn order, and finalizes the case output | Emits case lifecycle events and hands off to specialists |
| Order/item | order and item evidence | Verifies order status, item identity, and product/seller context | Returns normalized order-summary facts and evidence refs |
| Payment | payment timeline and payment records | Reconciles the amount paid with the claimed refund and identifies mismatch or cancellation scenarios | Produces payment facts and the refund recommendation |
| Shipment | shipment and carrier timeline | Confirms delivery dates, exceptions, and late-delivery signals | Returns delivery status and responsibility signals |
| Policy | active policy version and claim topic | Checks policy rules for refund eligibility and obligation | Returns the entitlement verdict and policy reasons |
| Verifier | full evidence set + draft output | Validates schema, cross-field consistency, claim linkage, and confidence bounds | Emits verification events and approves final output |

Mỗi agent chỉ được phép gọi tool theo specialization của mình: order/item agent gọi order/item/seller/product tools; payment agent chỉ gọi payment tools; shipment agent chỉ gọi shipment tools; policy agent chỉ gọi policy tools; coordinator tổng hợp. Điều này giúp tránh dùng dữ liệu chéo case và giữ trace rõ ràng.

## 3. A2A protocol

Mô tả message envelope, correlation theo `case_id`, điều kiện handoff, timeout và cách tránh vòng lặp. Chỉ trace sự kiện/decision code quan sát được; không trace nội dung suy luận riêng.

## 4. Evidence lifecycle

Mô tả cách validate MCP response, lưu `evidence_ref`, map evidence vào claim/output và emit `tool_result_consumed`. Evidence không được tái sử dụng giữa các case.

## 5. Failure policy

| Failure | Retry? | Fallback | Trace event/code |
| --- | --- | --- | --- |
| MCP timeout | TODO | TODO | TODO |
| Not found | TODO | TODO | TODO |
| Source conflict | TODO | TODO | TODO |
| Invalid specialist result | TODO | TODO | TODO |

Retry phải có giới hạn và idempotent. Không chuyển missing evidence thành dữ liệu phỏng đoán.

## 6. Verification invariants

Liệt kê kiểm tra trước finalize: schema, entity scope, evidence ownership, claim linkage, money totals, responsibility/action consistency và confidence bounds.

## 7. Reproducibility

Ghi model/config, dependency pinning, concurrency limit, random seed (nếu có), lệnh chạy và các giới hạn tài nguyên. Không ghi API key.
