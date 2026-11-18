# data

이 폴더의 데이터 파일은 git에 올리지 않는다.

- `pcb_rma_v4.sqlite`: PCB RMA 데이터 v4의 SQLite 원본 (기본값). AI/DATA 담당(유희범)이 보관하는 `PCB_RMA_dataset_v4_full.zip` 안의 `pcb_rma.sqlite`를 이 이름으로 둔다.
- `pcb_rma_v3.sqlite`: v3 원본. v4 파일이 없을 때만 쓴다.
- 팀 공유용 CSV(`PCB_RMA_dataset_v4.zip`)는 Drive의 `03. 데이터 › ssd_data`에 있다.
- 다른 위치의 파일을 쓰려면 환경 변수 `RMA_DB`에 경로를 지정한다.
