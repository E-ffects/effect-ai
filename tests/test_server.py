"""Reference AI server (server/app.py): Spring's call flow (Spring_API_v4 5), service token, error format."""
import json
import threading
import time
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from server import app as server

SECRET = "test-only-secret-0123456789-abcdefghijklmnop"  # same shape as effect-backend application-test.yml
B = server.BASE
SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "samples" / "RMA-08927.json").read_text(encoding="utf-8"))


def token(secret=SECRET, role="SYSTEM", ttl=600, alg="HS256", **extra):
    now = int(time.time())
    return jwt.encode({"sub": "spring", "role": role, "type": "ACCESS", "iat": now, "exp": now + ttl, **extra},
                      secret, algorithm=alg)


def bearer(**kw):
    return {"Authorization": f"Bearer {token(**kw)}"}


@pytest.fixture(scope="module")
def client():
    return TestClient(server.create_app(secret=SECRET))


def wait(client, analysis_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        body = client.get(f"{B}/analyses/{analysis_id}", headers=bearer()).json()
        if body["status"] not in ("QUEUED", "RUNNING"):
            return body
        time.sleep(0.2)
    raise AssertionError("analysis did not finish")


def test_service_token_is_required(client):
    body = {"rma_id": "RMA-08927"}
    assert client.post(f"{B}/triage", json=body).status_code == 401
    assert client.post(f"{B}/triage", json=body, headers=bearer(secret="other-secret-0123456789-abcdefghijk")).status_code == 401
    assert client.post(f"{B}/triage", json=body, headers=bearer(ttl=-5)).json()["code"] == "UNAUTHORIZED"
    assert client.post(f"{B}/triage", json=body, headers=bearer(role="CS")).status_code == 403  # user tokens are refused
    assert client.post(f"{B}/triage", json=body, headers=bearer()).status_code == 200
    assert client.get(f"{B}/health").status_code == 200  # no token for health


def test_longer_spring_secrets_use_hs512():
    """jjwt signWith(key) picks HS256/384/512 by key length; a 64-byte secret gives HS512."""
    long_secret = "x" * 64
    client = TestClient(server.create_app(secret=long_secret))
    res = client.post(f"{B}/triage", json={"rma_id": "RMA-08927"}, headers=bearer(secret=long_secret, alg="HS512"))
    assert res.status_code == 200


def test_health(client):
    body = client.get(f"{B}/health").json()
    assert body["status"] == "ok" and body["db"] == "ok" and body["rules"] == "pcb-v5"


def test_spring_flow_classify_triage_analysis_refresh(client):
    claim = SAMPLE["claim"]
    res = client.post(f"{B}/classify", headers={**bearer(), "X-Request-Id": "req-test"}, json={
        "rma_id": claim["rma_id"], "raw_text": claim["raw_text"], "detection_stage": claim["detection_stage"],
        "reported_part_no": claim["reported_part_no"], "customer_id": claim["customer_id"]})
    assert res.status_code == 200
    body = res.json()
    assert body["request_id"] == "req-test" and body["status"] == "CLASSIFIED" and body["minor_code"] == "ELEC02"

    triage = client.post(f"{B}/triage", headers=bearer(), json={"rma_id": "RMA-08927"}).json()
    assert triage == SAMPLE["triage"]  # Spring sends no as_of

    res = client.post(f"{B}/analyses", headers=bearer(), json={"rma_id": "RMA-08927", "options": {"mode": "B", "top_k": 3}})
    assert res.status_code == 202
    started = res.json()
    assert started["status"] in ("QUEUED", "RUNNING") and started["poll_after_ms"] == 1000
    assert started["as_of"] == SAMPLE["analysis"]["as_of"]
    done = wait(client, started["analysis_id"])
    assert done["status"] == "SUCCEEDED" and done["error"] is None and done["progress"] is None
    assert done["as_of"] == SAMPLE["analysis"]["as_of"]
    assert set(done["result"]) == set(SAMPLE["analysis"]["result"])
    assert done["result"]["drafts"]["service_action"]["action_type"] == \
        SAMPLE["analysis"]["result"]["drafts"]["service_action"]["action_type"]
    assert [t["agent"] for t in done["trace"]] == [t["agent"] for t in SAMPLE["analysis"]["trace"]]

    similar = client.get(f"{B}/claims/RMA-08927/similar?limit=3", headers=bearer()).json()
    assert len(similar["items"]) == 3
    refreshed = client.post(f"{B}/cases/refresh", headers=bearer(), json={
        "rma_id": "RMA-08927", "analysis_id": started["analysis_id"], "decision": "APPROVED",
        "decided_at": "2025-11-21T10:12:00+09:00"}).json()
    assert refreshed["indexed"] is True and refreshed["case_count"] > 0


def test_errors_use_the_spec_format(client):
    h = bearer()
    res = client.post(f"{B}/triage", headers=h, json={"rma_id": "RMA-99999"})
    assert res.status_code == 404 and res.json()["code"] == "CLAIM_NOT_FOUND" and res.json()["detail"]
    res = client.post(f"{B}/analyses", headers=h, json={"rma_id": "RMA-99999"})
    assert res.status_code == 404 and res.json()["code"] == "CLAIM_NOT_FOUND"  # right away, not a FAILED job
    res = client.post(f"{B}/analyses", headers=h, json={"rma_id": "RMA-08927", "as_of": "2025-11-20"})
    assert res.status_code == 422 and res.json()["code"] == "AS_OF_INVALID"
    res = client.post(f"{B}/analyses", headers=h, json={"rma_id": "RMA-08927", "options": {"mode": "C"}})
    assert res.status_code == 400 and res.json()["code"] == "INVALID_INPUT"
    res = client.post(f"{B}/classify", headers=h, json={"raw_text": "  "})
    assert res.status_code == 400 and res.json()["code"] == "INVALID_INPUT"
    res = client.post(f"{B}/classify", headers=h, json={})
    assert res.status_code == 400 and res.json()["code"] == "INVALID_INPUT"
    res = client.get(f"{B}/analyses/ana-none", headers=h)
    assert res.status_code == 404 and res.json()["code"] == "ANALYSIS_NOT_FOUND"
    res = client.get(f"{B}/priority/patterns?weights=notjson", headers=h)
    assert res.status_code == 400 and res.json()["code"] == "INVALID_INPUT"


def test_priority_routes(client):
    h = bearer()
    res = client.get(f"{B}/priority/patterns", headers=h,
                     params={"as_of": "2025-12-31T18:56:13+09:00", "limit": 3, "weights": '{"loss": 1}'})
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 3 and items[0]["rank"] == 1
    assert len(client.get(f"{B}/priority/responses?limit=5", headers=h).json()["items"]) == 5


def test_running_job_is_reused_and_progress_reported():
    gate = threading.Event()

    def runner(rma_id, as_of=None, mode="B", top_k=3, on_progress=None):
        on_progress("QA", 2, 7)
        gate.wait(5)
        return {"rma_id": rma_id, "as_of": as_of, "mode": mode, "status": "PARTIAL", "result": {"drafts": {}},
                "trace": [], "warnings": ["LLM_UNAVAILABLE"], "langsmith_run_id": None}

    client = TestClient(server.create_app(secret=None, runner=runner))
    first = client.post(f"{B}/analyses", json={"rma_id": "RMA-08927"}).json()
    second = client.post(f"{B}/analyses", json={"rma_id": "RMA-08927"}).json()
    assert first["analysis_id"] == second["analysis_id"]  # same rma_id and as_of: no duplicate run
    time.sleep(0.2)
    running = client.get(f"{B}/analyses/{first['analysis_id']}").json()
    assert running["status"] == "RUNNING" and running["progress"] == {"current_agent": "QA", "steps_done": 2, "steps_total": 7}
    gate.set()
    done = wait(client, first["analysis_id"], timeout=5)
    assert done["status"] == "PARTIAL" and done["warnings"] == ["LLM_UNAVAILABLE"]
    third = client.post(f"{B}/analyses", json={"rma_id": "RMA-08927", "options": {"include_drafts": False}}).json()
    assert third["analysis_id"] != first["analysis_id"]  # finished jobs are not reused
    assert wait(client, third["analysis_id"], timeout=5)["result"]["drafts"] is None


def test_slow_analysis_times_out(monkeypatch):
    monkeypatch.setitem(server.TIMEOUT_S, "B", 0.2)
    gate = threading.Event()

    def runner(rma_id, as_of=None, mode="B", top_k=3, on_progress=None):
        gate.wait(5)
        return {"status": "SUCCEEDED", "result": {"drafts": {}}}

    client = TestClient(server.create_app(secret=None, runner=runner))
    job = client.post(f"{B}/analyses", json={"rma_id": "RMA-08927"}).json()
    time.sleep(0.4)
    body = client.get(f"{B}/analyses/{job['analysis_id']}").json()
    assert body["status"] == "FAILED" and body["error"]["code"] == "ANALYSIS_TIMEOUT"
    gate.set()
    time.sleep(0.2)
    assert client.get(f"{B}/analyses/{job['analysis_id']}").json()["status"] == "FAILED"  # late result dropped


def test_server_refuses_to_start_without_a_secret(monkeypatch):
    monkeypatch.delenv("RMA_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("RMA_AUTH", raising=False)
    with pytest.raises(RuntimeError):
        server.create_app()
    monkeypatch.setenv("RMA_AUTH", "off")
    assert server.create_app() is not None


def test_chat_route_follows_the_spring_contract(client):
    """Spring HttpAiClient.chat: snake_case body, answer + status in CHAT_STATUSES, else Spring treats it as an outage."""
    from test_chat import rma_context
    body = {"thread_id": "6f1c", "audience": "QUALITY", "scope": {"type": "RMA", "id": "RMA-08927"},
            "message": "1순위 원인 근거가 뭐야?", "history": [{"role": "user", "text": "안녕"}], "context": rma_context()}
    res = client.post(f"{B}/chat", headers=bearer(), json=body)
    assert res.status_code == 200
    out = res.json()
    assert out["status"] in {"ANSWERED", "NEED_ANALYSIS", "OUT_OF_SCOPE", "FALLBACK"} and out["answer"]
    assert {"intent", "evidence", "suggestions", "warnings", "model_version"} <= set(out)
    assert client.post(f"{B}/chat", json=body).status_code == 401
    bad = client.post(f"{B}/chat", headers=bearer(), json={**body, "audience": "ADMIN"})
    assert bad.status_code == 400 and bad.json()["code"] == "INVALID_INPUT"
    no_ctx = client.post(f"{B}/chat", headers=bearer(), json={**body, "context": {"rma": {}, "analysis": None}})
    assert no_ctx.json()["status"] == "NEED_ANALYSIS"
