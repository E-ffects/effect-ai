# 재발 판정 평가: pcb_rma_v5.sqlite

- 대상: `recurrence_review`에 있는 반품 2,606건, 반품별 분석 시작 시점
- 일치: **91.4%** (2,383건)

| 정답 \ 에이전트 | RECURRENCE | PRE_ACTION_PRODUCT | PENDING_CAUSE_REVIEW | NOT_APPLICABLE | 합계 |
|---|---:|---:|---:|---:|---:|
| RECURRENCE | 653 | 0 | 15 | 87 | 755 |
| PRE_ACTION_PRODUCT | 20 | 1,730 | 44 | 42 | 1,836 |
| PENDING_CAUSE_REVIEW | 14 | 1 | 0 | 0 | 15 |

- RECURRENCE 재현율 86.5%

- PRE_ACTION_PRODUCT 재현율 94.2%

- PENDING_CAUSE_REVIEW 재현율 0.0%
