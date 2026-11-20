"""Reference AI server (FastAPI) for docs/api_spec.md 3. Spring is the only caller (Spring_API_v4 5).

This is a thin, working wrapper around the rma_agent functions so Spring can switch from MockAiClient to real
calls (ai.mode=http). The FastAPI owner can keep it as is or move the routes into their own app: every route only
calls a rma_agent function, so the AI side can change without touching this file.

Run (port 8080, base path /ai/v1):
    RMA_SERVICE_JWT_SECRET=<same value as Spring jwt.secret> uvicorn server.app:create_app --factory --port 8080
    RMA_AUTH=off uvicorn server.app:create_app --factory --port 8080     # local only, no token check

Auth: Spring sends `Authorization: Bearer <service token>` signed with its jwt.secret (HMAC, the secret's UTF-8
bytes, as jjwt Keys.hmacShaKeyFor). Required claims: role = "SYSTEM", exp. `type`, when present, must be ACCESS.
GET /health needs no token (status checks and probes).

Analysis jobs live in memory (spec 3-0): a restart loses them and Spring maps the 404 to ANALYSIS_LOST.
"""
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import jwt
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import rma_agent
from rma_agent.db import KST
from rma_agent.errors import RmaAgentError

log = logging.getLogger("rma_server")
VERSION = "0.1.0"
BASE = "/ai/v1"
TIMEOUT_S = {"B": 30, "A": 90}      # spec 4: ANALYSIS_TIMEOUT
POLL_AFTER_MS = 1000
MAX_JOBS = 2000                     # finished jobs kept in memory; Spring stores results after polling
ALGORITHMS = ["HS256", "HS384", "HS512"]


class ApiError(Exception):
    def __init__(self, status, code, detail, **extra):
        super().__init__(detail)
        self.status, self.body = status, {"detail": detail, "code": code, **extra}


def _now():
    return datetime.now(KST).isoformat(timespec="seconds")


# ---------- requests (spec 3-1, 3-2, 3-5, 3-7)
class ClassifyRequest(BaseModel):
    raw_text: str
    rma_id: str | None = None
    detection_stage: str | None = None
    reported_part_no: str | None = None
    part_no: str | None = None          # same as reported_part_no
    customer_id: str | None = None
    as_of: str | None = None


class AnalysisOptions(BaseModel):
    top_k: int = 3
    include_drafts: bool = True
    mode: str = "B"


class AnalysisRequest(BaseModel):
    rma_id: str
    as_of: str | None = None
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)


class TriageRequest(BaseModel):
    rma_id: str
    as_of: str | None = None


class RefreshRequest(BaseModel):
    rma_id: str | None = None
    analysis_id: str | None = None
    decision: str | None = None
    decided_at: str | None = None


# ---------- analysis jobs
class Jobs:
    """In-memory analysis jobs. One job per (rma_id, as_of, mode) runs at a time."""

    def __init__(self, runner, workers=4):
        self.runner = runner
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analysis")
        self.lock = threading.Lock()
        self.jobs = {}

    def submit(self, rma_id, as_of, mode, top_k, include_drafts):
        with self.lock:
            for job in self.jobs.values():
                if (job["rma_id"], job["as_of"], job["mode"]) == (rma_id, as_of, mode) and \
                        self._state(job) in ("QUEUED", "RUNNING"):
                    return dict(job), False
            stamp = datetime.now(KST)
            job = {"analysis_id": f"ana-{stamp:%Y%m%d}-{rma_id.split('-')[-1]}-{uuid.uuid4().hex[:6]}",
                   "rma_id": rma_id, "as_of": as_of, "mode": mode, "status": "QUEUED", "created_at": _now(),
                   "started": None, "completed_at": None, "progress": None, "output": None, "error": None}
            self.jobs[job["analysis_id"]] = job
            self._trim()
            snapshot = dict(job)  # the worker may start before the response is written
        self.pool.submit(self._run, job, top_k, include_drafts)
        return snapshot, True

    def _trim(self):
        done = [k for k, j in self.jobs.items() if j["status"] not in ("QUEUED", "RUNNING")]
        for key in done[:max(0, len(self.jobs) - MAX_JOBS)]:
            del self.jobs[key]

    def _run(self, job, top_k, include_drafts):
        job["status"], job["started"] = "RUNNING", time.monotonic()

        def progress(agent, done, total):
            job["progress"] = {"current_agent": agent, "steps_done": done, "steps_total": total}

        try:
            out = self.runner(job["rma_id"], as_of=job["as_of"], mode=job["mode"], top_k=top_k,
                              on_progress=progress)
        except RmaAgentError as exc:
            self._finish(job, "FAILED", error=exc.body())
            return
        except Exception as exc:  # keep the server alive; Spring sees FAILED
            log.exception("analysis %s failed", job["analysis_id"])
            self._finish(job, "FAILED", error={"detail": f"분석 중 오류: {exc}", "code": "INTERNAL_ERROR"})
            return
        if not include_drafts:
            out["result"]["drafts"] = None
        self._finish(job, out["status"], output=out)

    def _finish(self, job, status, output=None, error=None):
        with self.lock:
            if job["status"] == "FAILED" and job["error"] and job["error"]["code"] == "ANALYSIS_TIMEOUT":
                return  # already reported as timed out; a late result is dropped
            job.update(status=status, output=output, error=error, completed_at=_now())

    def _state(self, job):
        """Status, turning a job that ran past its time limit into FAILED/ANALYSIS_TIMEOUT."""
        if job["status"] == "RUNNING" and job["started"] and \
                time.monotonic() - job["started"] > TIMEOUT_S.get(job["mode"], 30):
            job.update(status="FAILED", completed_at=_now(), progress=None,
                       error={"detail": f"분석 시간 초과 ({TIMEOUT_S.get(job['mode'], 30)}초)", "code": "ANALYSIS_TIMEOUT"})
        return job["status"]

    def view(self, analysis_id):
        with self.lock:
            job = self.jobs.get(analysis_id)
            if job is None:
                return None
            status = self._state(job)
            out = job["output"] or {}
            return {"analysis_id": job["analysis_id"], "rma_id": job["rma_id"], "as_of": out.get("as_of", job["as_of"]),
                    "mode": job["mode"], "status": status, "created_at": job["created_at"],
                    "completed_at": job["completed_at"],
                    "progress": job["progress"] if status in ("QUEUED", "RUNNING") else None,
                    "result": out.get("result"), "trace": out.get("trace"), "warnings": out.get("warnings", []),
                    "langsmith_run_id": out.get("langsmith_run_id"), "model_versions": out.get("model_versions"),
                    "elapsed_ms": out.get("elapsed_ms"), "error": job["error"]}


# ---------- auth
def _auth_dependency(secret):
    def check(authorization: str | None = Header(default=None)):
        if secret is None:
            return
        if not authorization or not authorization.lower().startswith("bearer "):
            raise ApiError(401, "UNAUTHORIZED", "서비스 토큰이 없습니다")
        try:
            claims = jwt.decode(authorization[7:].strip(), secret, algorithms=ALGORITHMS,
                                options={"require": ["exp"]})
        except jwt.ExpiredSignatureError:
            raise ApiError(401, "UNAUTHORIZED", "서비스 토큰이 만료되었습니다")
        except jwt.InvalidTokenError:
            raise ApiError(401, "UNAUTHORIZED", "서비스 토큰을 확인할 수 없습니다")
        if claims.get("role") != "SYSTEM" or claims.get("type", "ACCESS") != "ACCESS":
            raise ApiError(403, "FORBIDDEN", "SYSTEM 역할의 서비스 토큰만 호출할 수 있습니다")
    return check


def _secret_from_env():
    if os.environ.get("RMA_AUTH", "").lower() == "off":
        log.warning("RMA_AUTH=off: service token is not checked (local use only)")
        return None
    secret = os.environ.get("RMA_SERVICE_JWT_SECRET")
    if not secret:
        raise RuntimeError("RMA_SERVICE_JWT_SECRET (Spring jwt.secret) is not set. Set it, or RMA_AUTH=off for local runs.")
    return secret


# ---------- app
def create_app(secret=..., runner=None):
    """secret: Spring's jwt.secret, None to skip the token check, default from the environment.
    runner: analysis function (tests pass a fake); default rma_agent.run_analysis."""
    secret = _secret_from_env() if secret is ... else secret
    jobs = Jobs(runner or rma_agent.run_analysis)
    app = FastAPI(title="E!ffect RMA AI", version=VERSION)
    app.state.jobs = jobs
    auth = [Depends(_auth_dependency(secret))]

    @app.exception_handler(RmaAgentError)
    async def agent_error(request, exc):
        return JSONResponse(exc.body(), status_code=exc.http_status)

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return JSONResponse(exc.body, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request, exc):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return JSONResponse({"detail": f"요청 형식 오류: {where} {first.get('msg', '')}".strip(), "code": "INVALID_INPUT"},
                            status_code=400)

    @app.exception_handler(Exception)
    async def internal(request, exc):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "AI 서버 내부 오류", "code": "INTERNAL_ERROR"}, status_code=500)

    @app.post(f"{BASE}/classify", dependencies=auth)
    def classify(req: ClassifyRequest, x_request_id: str | None = Header(default=None)):
        out = rma_agent.classify(req.raw_text, detection_stage=req.detection_stage,
                                 part_no=req.reported_part_no or req.part_no, customer_id=req.customer_id,
                                 as_of=req.as_of)
        return {"request_id": x_request_id or f"req-{uuid.uuid4().hex[:4]}", **({"rma_id": req.rma_id} if req.rma_id else {}),
                **out}

    @app.post(f"{BASE}/triage", dependencies=auth)
    def triage(req: TriageRequest):
        return rma_agent.triage(req.rma_id, as_of=req.as_of)

    @app.post(f"{BASE}/analyses", dependencies=auth, status_code=202)
    def start_analysis(req: AnalysisRequest):
        opts = req.options
        if opts.mode not in ("A", "B"):
            raise ApiError(400, "INVALID_INPUT", f"mode는 A 또는 B입니다: {opts.mode!r}")
        if not 1 <= opts.top_k <= 10:
            raise ApiError(400, "INVALID_INPUT", f"top_k는 1~10입니다: {opts.top_k!r}")
        as_of = rma_agent.analysis_as_of(req.rma_id, req.as_of)  # 404/422 now, not as a FAILED job
        job, _ = jobs.submit(req.rma_id, as_of, opts.mode, opts.top_k, opts.include_drafts)
        return {"analysis_id": job["analysis_id"], "rma_id": job["rma_id"], "as_of": as_of, "status": job["status"],
                "created_at": job["created_at"], "poll_after_ms": POLL_AFTER_MS}

    @app.get(f"{BASE}/analyses/{{analysis_id}}", dependencies=auth)
    def get_analysis(analysis_id: str):
        view = jobs.view(analysis_id)
        if view is None:
            raise ApiError(404, "ANALYSIS_NOT_FOUND", f"분석 {analysis_id}을(를) 찾을 수 없습니다")
        return view

    @app.get(f"{BASE}/claims/{{rma_id}}/similar", dependencies=auth)
    def similar(rma_id: str, as_of: str | None = None, limit: int = 5):
        return rma_agent.similar_cases(rma_id, as_of=as_of, limit=limit)

    @app.post(f"{BASE}/cases/refresh", dependencies=auth)
    def refresh(req: RefreshRequest | None = None):
        count = rma_agent.refresh_cases()
        return {"rma_id": req.rma_id if req else None, "indexed": True, "case_count": count}

    def _weights(raw):
        if raw is None:
            return None
        try:
            weights = json.loads(raw)
        except ValueError:
            raise ApiError(400, "INVALID_INPUT", f"weights는 JSON 객체입니다: {raw!r}")
        if not isinstance(weights, dict):
            raise ApiError(400, "INVALID_INPUT", f"weights는 JSON 객체입니다: {raw!r}")
        return weights

    @app.get(f"{BASE}/priority/patterns", dependencies=auth)
    def patterns(as_of: str | None = None, limit: int | None = Query(default=None, ge=1),
                 weights: str | None = None):
        return rma_agent.pattern_priority(as_of=as_of, weights=_weights(weights), limit=limit)

    @app.get(f"{BASE}/priority/responses", dependencies=auth)
    def responses(as_of: str | None = None, limit: int | None = Query(default=None, ge=1),
                  weights: str | None = None):
        return rma_agent.response_priority(as_of=as_of, weights=_weights(weights), limit=limit)

    @app.get(f"{BASE}/health")
    def health():
        from rma_agent.agents.graph import RULES_VERSION
        from rma_agent.agents.llm import model_name
        from rma_agent.agents.runtime import get_runtime
        try:
            get_runtime().conn.execute("select 1").fetchone()
            db = "ok"
        except Exception:
            db = "down"
        llm = "ok" if model_name() else "off"   # off: no model or key configured, sentences use templates
        return {"status": "ok" if db == "ok" else "degraded", "db": db, "llm": llm, "version": VERSION,
                "rules": RULES_VERSION}

    return app
