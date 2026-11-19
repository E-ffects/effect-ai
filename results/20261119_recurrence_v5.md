# 재발 판정 평가: pcb_rma_v5.sqlite

- 대상: `recurrence_review`에 있는 반품 2,606건, 반품별 분석 시작 시점
- 일치: **90.1%** (2,348건)

| 정답 \ 에이전트 | RECURRENCE | PRE_ACTION_PRODUCT | PENDING_CAUSE_REVIEW | NOT_APPLICABLE | 합계 |
|---|---:|---:|---:|---:|---:|
| RECURRENCE | 619 | 0 | 49 | 87 | 755 |
| PRE_ACTION_PRODUCT | 10 | 1,729 | 54 | 43 | 1,836 |
| PENDING_CAUSE_REVIEW | 14 | 1 | 0 | 0 | 15 |

- RECURRENCE 재현율 82.0%

- PRE_ACTION_PRODUCT 재현율 94.2%

- PENDING_CAUSE_REVIEW 재현율 0.0%

- **RECURRENCE 정밀도** (검토 대상이 아닌 반품까지 포함): 73.2% (619 / 846)

검토 대상이 아닌 반품(4,701건, 2025-07 이후 접수): NOT_APPLICABLE 3,687, PENDING_CAUSE_REVIEW 659, RECURRENCE 203, PRE_ACTION_PRODUCT 152. NOT_APPLICABLE이 아닌 판정(잘못 붙인 판정) 21.6%
