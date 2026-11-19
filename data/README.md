# data

이 폴더의 데이터 파일은 git에 올리지 않는다.

- `pcb_rma_v5.sqlite`: **팀 공식 데이터 v5 (2026-11-19부터, 기본값).** Drive의 `PCB_RMA_dataset_v5_full.zip` 안의 `pcb_rma.sqlite`를 이 이름으로 둔다. v4의 사건 21개에 재발 사건 4개, 헛신호, 실제 같은 고객 문장을 더했다.
- `pcb_rma_v4.sqlite`: 이전 버전. v5 파일이 없을 때만 쓴다. 테스트와 샘플은 v5 기준이라 v4로는 일부 테스트가 실패한다.
- 팀 공유용 CSV(`PCB_RMA_dataset_v5.zip`)는 Drive의 `03. 데이터 › ssd_data`에 있다.
- 다른 위치의 파일을 쓰려면 환경 변수 `RMA_DB`에 경로를 지정한다.
- 받은 zip이 맞는 버전인지는 `datagen/README.md`의 SHA-256 표로 확인한다. 생성 스크립트도 그 폴더에 있다.
