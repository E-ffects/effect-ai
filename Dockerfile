FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY rma_agent rma_agent
COPY server server
COPY models models

RUN useradd --system --uid 10001 --no-create-home app
USER 10001

EXPOSE 8080
# 분석 작업을 프로세스 메모리에 두므로 워커는 1개로 실행한다
CMD ["uvicorn", "server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
