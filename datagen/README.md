# datagen: PCB RMA 합성 데이터 생성기

PCB RMA 데이터(v5)를 만드는 스크립트와 문서다. **데이터 파일은 git에 없다.** 데이터는 Google Drive의 zip으로 배포하고, 이 폴더는 "어떻게 만들었고 무엇이 바뀌었는지"를 기록한다.

## 데이터 받기 (Drive)
| 파일 | 내용 | 쓰는 사람 |
|---|---|---|
| `PCB_RMA_dataset_v5.zip` | 전체 테이블 CSV + 데이터 사전·README | 팀 전체 (엑셀, 화면 개발) |
| `PCB_RMA_dataset_v5_full.zip` | SQLite DB + 평가 파일 + 생성 스크립트 | DB 적재(`db/postgres`), AI 에이전트(`data/pcb_rma_v5.sqlite`) |
| `PCB_RMA_dataset_v4*.zip` | 이전 버전. 2026-11-19부터 팀 공식은 v5 | 기록용 |

받은 파일이 맞는 버전인지 SHA-256으로 확인한다 (`shasum -a 256 <파일>`).

| 파일 (2026-11-19, 요청 문구 정리 반영) | SHA-256 |
|---|---|
| `PCB_RMA_dataset_v5_full.zip` | `be815cf482839860c2f51fdab05e40be6c3d11a2d4d8d90d235cfe0828debd8b` |
| `PCB_RMA_dataset_v5.zip` | `e2956284fb0ee9e3b202c801b5dc765967de423fdd9df090327cdf0b3403b3e0` |
| v5 `pcb_rma.sqlite` (zip 안) | `5a7fb8760001e6b0e7c67634c04a5f80fecb4858c16f6d982f44a5abe400b083` |
| `PCB_RMA_dataset_v4_full.zip` | `59165e84e9710425e91f0c341f58a206e24618440d867f22fc0bd1f915120087` |
| `PCB_RMA_dataset_v4.zip` | `3beeb7f2f3d3a21a711a83e65288c626dc8b8994658fb222848ca67804819f03` |
| v4 `pcb_rma.sqlite` (zip 안) | `99c78843c0a394b1571ef44ecb9d9b463723d705ffe6ae7467f1db0650c6bb4b` |

## 다시 만들기
```bash
cd datagen
PYTHONHASHSEED=0 ./run_all.sh /tmp/pcb_v5      # 몇 분. /tmp/pcb_v5/pcb_rma.sqlite 와 평가 파일이 생긴다
```
- `PYTHONHASHSEED=0`이 없으면 일부 난수가 실행마다 달라진다(생성기가 파이썬 `hash()`를 씀).
- 2026-11-19에 이 스크립트로 다시 만든 v5가 배포본과 테이블·뷰 56개, 평가 파일, 미리보기 CSV까지 같음을 확인했다.
- 순서: `generate_v30` → `finalize_v30` → `patch_v31` → `reconcile_v31` → `patch_v3` → `patch_v3_text` → `patch_v3_severity` → `patch_v5_noise` → `patch_v5_text` → `finalize_v3` → `patch_v4_loss` → `patch_v4_terms` → `slim_v3` (`run_all.sh`).
- `patch_request_text.py`: 이미 만들어진 데이터 폴더의 고객 요청 문구 3종을 고치는 패치다(v4·v5 배포본에 적용함). 생성기에는 같은 문구가 이미 들어 있어 다시 만들 때는 필요 없다.
- `manifest.json`: v5 배포본의 행 수, 분포, 자체 검사 51개 결과, 패치 기록.

## 문서 (`docs/`)
| 파일 | 내용 |
|---|---|
| `PCB_RMA_v5_설계.md`, `PCB_RMA_v5_수정내역.md` | v5: 재발 사건 4개, 헛신호, 지저분한 고객 문장. 에이전트 평가 결과 |
| `PCB_RMA_v4_수정내역.md` | v4: 품질 손실 금액 |
| `PCB_RMA_v3_수정내역.md`, `PCB_RMA_v31_감사및수정내역.md`, `PCB_RMA_v30_메일반영_변경내역.md` | 이전 버전 이력 |
| `데이터사전_v4.md` | 테이블·컬럼 설명 (v5도 같고 `claim_text_variant`만 추가) |
| `dataset_README_v5.md` | 배포 zip의 README |

## 고칠 때
1. 이 폴더의 스크립트를 고친다.
2. `PYTHONHASHSEED=0 ./run_all.sh <새 폴더>`로 다시 만들고 자체 검사(`manifest.json`의 `checks`)가 모두 통과하는지 본다.
3. effect-ai 테스트와 평가를 새 데이터로 돌린다(`RMA_DB=<새 폴더>/pcb_rma.sqlite python3 -m pytest -q`).
4. zip을 다시 만들어 Drive에 올리고, 위 SHA-256 표와 `docs/`의 수정 내역을 고친다.
