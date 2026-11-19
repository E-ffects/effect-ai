# PCB RMA 데이터 PostgreSQL 적재 (WBS 4.11.1)

SQLite로 배포한 PCB RMA 데이터(v4, v5)를 PostgreSQL에 그대로 옮기는 스크립트다. 데이터 이름(테이블·컬럼)과 값은 SQLite와 같다.

## 파일
| 파일 | 내용 |
|---|---|
| `build.py` | SQLite → 적재 패키지(스키마, 데이터 파일, 적재 스크립트) 생성 |
| `views.sql` | 뷰 10개를 PostgreSQL 문법으로 옮긴 것 |
| `verify.py` | 적재한 DB를 SQLite와 대조. 모든 테이블을 한 행씩, 모든 뷰의 결과를 비교 |

## 사용법
```bash
# 1. 적재 패키지 만들기 (data/ 아래에 생기며 git에는 올리지 않는다)
python db/postgres/build.py data/pcb_rma_v5.sqlite data/postgres_v5 --schema pcb   # 팀 DB는 pcb 스키마

# 2. 빈 데이터베이스에 적재 (psql 필요, 약 10초)
cd data/postgres_v5 && psql "$RMA_DATABASE_URL" -v ON_ERROR_STOP=1 -f load.sql && cd -

# 3. 검증 (약 20초)
python db/postgres/verify.py data/pcb_rma_v5.sqlite "$RMA_DATABASE_URL" --schema pcb
```
- `RMA_DATABASE_URL` 예: `postgresql://rma@localhost:5432/rma`. 비밀번호는 `PGPASSWORD`나 `~/.pgpass`로 넘긴다. 명령줄이나 git에 남기지 않는다.
- Docker로 PostgreSQL을 띄웠다면 패키지 폴더를 컨테이너에 마운트하거나 `docker cp`로 넣고 컨테이너 안의 `psql`로 실행한다.

## 적재 결과 (2026-11-19, 로컬 PostgreSQL 17)
| | v4 | v5 |
|---|---:|---:|
| 테이블 | 45 | 46 |
| 행 | 1,480,745 | 1,496,641 |
| 외래 키 | 74 | 75 |
| 인덱스 (기본 키 제외) | 15 | 15 |
| 뷰 | 10 | 10 |
| SQLite와 대조 | 모두 같음 | 모두 같음 |

- 외래 키는 `DEFERRABLE INITIALLY DEFERRED`로 만든다. 데이터는 한 트랜잭션으로 넣으므로 커밋이 성공했다면 외래 키 위반이 0건이다.
- 값을 일부러 하나 바꾸면 `verify.py`가 그 테이블을 `DIFFERENT`로 잡는 것도 확인했다.

## 팀 DB 구조 (`pcb` / `app`)
- `--schema pcb`로 만들면 `schema.sql`·`views.sql` 맨 앞에 `CREATE SCHEMA IF NOT EXISTS pcb; SET search_path TO pcb;`가 붙고, 데이터도 `pcb`에 들어간다. Spring 전용 테이블(`app` 또는 `public`)은 건드리지 않는다.
- AI 서버용 읽기 전용 계정 예:
  ```sql
  CREATE ROLE rma_reader LOGIN PASSWORD '...';
  GRANT CONNECT ON DATABASE effect TO rma_reader;
  GRANT USAGE ON SCHEMA pcb TO rma_reader;
  GRANT SELECT ON ALL TABLES IN SCHEMA pcb TO rma_reader;
  ALTER DEFAULT PRIVILEGES IN SCHEMA pcb GRANT SELECT ON TABLES TO rma_reader;   -- 새 접수 테이블 행도 보이게
  ```
- AI 서버 환경 변수: `RMA_DATABASE_URL=postgresql://rma_reader@<host>:5432/effect`, `RMA_DB_SCHEMA=pcb`
- 확인 (2026-11-19, 로컬 PostgreSQL 17): `pcb`에 적재·대조 모두 같음, 위 계정은 `pcb` 읽기만 되고 다른 스키마 읽기·쓰기는 거부, 이 계정으로 테스트 78개 통과.

## AI 에이전트가 이 DB를 읽기
`.env`(또는 컨테이너 환경변수)에 `RMA_DATABASE_URL=postgresql://...`를 넣으면 `rma_agent`가 SQLite 대신 이 DB를 읽는다(읽기 전용).
- 확인 (2026-11-19, 로컬 PostgreSQL 17)
  - v5: 테스트 63개 통과, 원인분석 평가 2,537건의 지표 6개가 SQLite와 같다.
  - v4: 테스트 61개 통과, 원인분석 평가 1,907건이 같고, 분석 응답·유사 사례·분류 20건도 글자까지 같다.
- 필요 패키지: `psycopg[binary]` (requirements.txt)
- **타입을 바꿔도 된다.** 시각을 `timestamptz`, `*_json`을 `jsonb`, 0/1 플래그를 `boolean`으로 바꾼 DB(시각 43개, JSON 12개, 플래그 11개)에서도 테스트 71개가 통과하고 원인분석 평가와 응답 20건이 SQLite와 같다(2026-11-19). 에이전트가 읽을 때 원래 형식(`+09:00` 문자열, JSON 문자열, 0/1)으로 되돌린다.

## 정해야 할 것 (백엔드, 윤서정 님)
| 항목 | 지금 | 선택지 |
|---|---|---|
| 테이블 생성 방식 | `schema.sql`이 직접 만든다 | Flyway를 쓰면 `schema.sql` + `views.sql`을 `V1__rma_schema.sql`로 넣고, 적재는 `\copy` 부분만 쓴다. JPA `ddl-auto`라면 엔티티를 이 이름에 맞춘다 |
| 시각 타입 | `text` (ISO-8601, 시간대 포함. 예: `2025-11-20T14:40:46+09:00`) | `timestamptz`로 바꿔도 된다. 에이전트는 그대로 동작한다(뷰의 `substr` 월 계산은 `to_char`로 고친다) |
| JSON 컬럼 (`*_json` 11개) | `text` | `jsonb`로 바꿔도 된다. 에이전트와 뷰 모두 그대로 동작한다 |
| 0/1 플래그 (`ppap_required`, `origin_verified` 등) | `bigint` | `boolean`으로 바꿔도 에이전트는 그대로다. 뷰의 `= 1` 비교만 고친다 |
| 데이터 버전 | **v5 (2026-11-19 팀 공식)** | v4도 같은 스크립트로 적재된다. 행 내용이 v5와 다르다 |

타입을 바꾸더라도 처음에는 이대로 적재해 `verify.py`로 확인한 뒤, 별도 마이그레이션(`V2__...`)으로 바꾸는 쪽이 안전하다.

## 데이터 주의점
- `symptom_assessment.evidence_spans_json`: 정보 부족(`INSUFFICIENT_INFO`) 300건은 QA가 나중에 확인한 증상의 구절이 들어 있다. 접수 문장에는 없는 정보이므로 **접수 화면에 "원문 근거"로 보여 주지 않는다**(원문에 있는 구절만 표시).
- 시각은 모두 `+09:00`이 붙은 문자열이라 문자열 정렬과 시간 정렬이 같다.
