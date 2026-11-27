"""Retrieval for the agent panel chat (RAG): embedding + character search over past cases and the quality master data.

Documents (built from the read-only PCB DB; each has the time it became known, so nothing after as_of is retrieved)
  CASE           approved past return: intake text, symptom, product, confirmed cause, process, equipment   (known_at)
  CAUSE          cause master: mechanism, required evidence, QA evidence rule, standard action, owner        (always)
  ESCAPE         escape (outflow) master: description, standard action                                     (always)
  SYMPTOM        symptom definition: inclusion / exclusion rules                                           (always)
  ACTION         corrective action of an investigation: description, cause, owner                          (selected_at)
  INVESTIGATION  investigation title and scope                                                            (opened_at)

Search: score = W_EMBED x cosine(query, doc) + (1 - W_EMBED) x character score (query's bigrams found in the doc,
+ exact identifiers), top k after the time and role
filters. Without embeddings (no API key, or the index not built yet) the character score alone is used.
CS: only CASE documents, returned without cause / equipment / process / Lot (the screen hides them from CS).

Embeddings: OpenAI text-embedding-3-small, 512 dimensions, cached in data/rag/<db>.npz by document text hash, so a
refresh embeds only new or changed documents (about 10,000 documents: one-time cost under 0.1 USD).
Settings: RMA_RAG_EMBEDDING=off turns embeddings off (character search only).
"""
import hashlib
import re
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 512
BATCH = 256
IDENT_BONUS = 0.5
W_EMBED = 0.5   # best on both checks (results/20261127_rag_v5.md): intake -> cases 91.0% vs 90.6% Hit@5 (characters
                # only), paraphrased questions -> master documents 68.1% vs 66.4% Hit@1. Embeddings alone were worse.
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "rag"
ALWAYS = ""                          # known_at of master data: before any as_of
CS_TYPES = {"CASE"}


@dataclass
class Doc:
    doc_id: str
    type: str
    known_at: str
    text: str                        # what is embedded and searched
    meta: dict = field(default_factory=dict)
    rma_id: str | None = None
    intake_label: str | None = None


_IDENT = re.compile(r"[A-Za-z]{2,}[A-Za-z0-9_]*-[A-Za-z0-9-]+")   # OLE-02, LT-01603, RMA-08927, INV-25-0020


def _idents(text):
    return {m.upper() for m in _IDENT.findall(text or "")}


def _grams(text):
    cleaned = "".join(ch for ch in (text or "").lower() if ch.isalnum() or ch == " ")
    return frozenset(cleaned[i:i + 2] for i in range(len(cleaned) - 1) if " " not in cleaned[i:i + 2])


def build_docs(conn, masters, library):
    """All retrievable documents from the DB. library: the CaseLibrary (approved cases with their known time)."""
    symptoms, processes, causes = masters["symptoms"], masters["processes"], masters["causes"]
    reqs = masters["requirements"]
    pname = lambda pid: processes.get(pid, {}).get("process_name", pid)
    docs = []
    for c in library.as_of("9999"):
        cause = causes.get(c.cause_code, {})
        minor = symptoms.get(c.intake_label, {}).get("minor_name", c.intake_label)
        # Structured facts first: intake texts are mostly boilerplate (greetings, P/N, requests)
        text = (f"{minor} 반품 사례. 확정 원인 {cause.get('cause_name', c.cause_code)} ({pname(cause.get('process_id'))}, "
                f"설비 {c.equipment_id or '특정 불가'}). 제품 {c.spec_id}, 검출 단계 {c.detection_stage}, Lot {c.lot_id or '미상'}.\n"
                f"고객 원문: {c.raw_text[:300]}")
        docs.append(Doc(f"CASE:{c.rma_id}", "CASE", c.applied_at, text, rma_id=c.rma_id, intake_label=c.intake_label, meta={
            "rma_id": c.rma_id, "접수일": c.opened_at[:10], "증상": minor, "제품": c.spec_id,
            "원문": c.raw_text[:200], "원인": cause.get("cause_name", c.cause_code), "공정": pname(cause.get("process_id")),
            "설비": c.equipment_id, "확정 여부": {"CONFIRMED": "확정", "PROBABLE": "추정"}.get(c.certainty, c.certainty),
            "조사": c.investigation_id}))
    for code, cause in causes.items():
        req = reqs.get(code, {})
        text = (f"원인 {cause['cause_name']} ({cause['cause_category']}, {pname(cause['process_id'])})\n"
                f"메커니즘: {cause.get('mechanism')}\n필요 근거: {cause.get('required_evidence')}\n"
                f"QA 확정 기준: {req.get('requirement_text', '')}\n표준 조치: {cause.get('standard_action')} "
                f"(담당 {cause.get('action_owner_dept')})")
        docs.append(Doc(f"CAUSE:{code}", "CAUSE", ALWAYS, text, meta={
            "원인": cause["cause_name"], "구분": cause["cause_category"], "공정": pname(cause["process_id"]),
            "메커니즘": cause.get("mechanism"), "필요 근거": cause.get("required_evidence"),
            "QA 확정 기준": req.get("requirement_text"), "표준 조치": cause.get("standard_action"),
            "담당": cause.get("action_owner_dept")}))
    for code, esc in masters["escapes"].items():
        text = (f"유출 원인 {esc['escape_name']} (검출 공정 {pname(esc['detection_process_id'])})\n"
                f"설명: {esc.get('description')}\n표준 조치: {esc.get('standard_action')}")
        docs.append(Doc(f"ESCAPE:{code}", "ESCAPE", ALWAYS, text, meta={
            "유출 원인": esc["escape_name"], "검출 공정": pname(esc["detection_process_id"]),
            "설명": esc.get("description"), "표준 조치": esc.get("standard_action")}))
    for code, sym in symptoms.items():
        text = (f"증상 {sym['minor_name']} ({sym['major_name']}), 심각도 {sym.get('severity')}\n"
                f"포함: {sym.get('inclusion_rules')}\n제외: {sym.get('exclusion_rules')}")
        docs.append(Doc(f"SYMPTOM:{code}", "SYMPTOM", ALWAYS, text, meta={
            "증상": sym["minor_name"], "대분류": sym["major_name"], "심각도": sym.get("severity"),
            "포함": sym.get("inclusion_rules"), "제외": sym.get("exclusion_rules")}))
    inv_title = {}
    for r in conn.execute("select investigation_id, title, scope_definition, opened_at from investigation"):
        inv_title[r["investigation_id"]] = r["title"]
        docs.append(Doc(f"INVESTIGATION:{r['investigation_id']}", "INVESTIGATION", r["opened_at"],
                        f"조사 {r['investigation_id']} {r['title']}\n범위: {r['scope_definition']}",
                        meta={"조사": r["investigation_id"], "제목": r["title"], "범위": r["scope_definition"],
                              "시작": (r["opened_at"] or "")[:10]}))
    for r in conn.execute("""select corrective_action_id, investigation_id, action_role, action_category, description,
                                    owner_dept, cause_code, escape_code, selected_at, implemented_at
                             from corrective_action where selected_at is not null"""):
        target = causes.get(r["cause_code"], {}).get("cause_name") or \
            masters["escapes"].get(r["escape_code"], {}).get("escape_name") or ""
        docs.append(Doc(f"ACTION:{r['corrective_action_id']}", "ACTION", r["selected_at"],
                        f"대책 {r['corrective_action_id']} ({r['action_category']}) {target}\n{r['description']}\n"
                        f"조사 {r['investigation_id']} {inv_title.get(r['investigation_id'], '')}, 담당 {r['owner_dept']}",
                        meta={"대책": r["corrective_action_id"], "구분": r["action_category"], "대상": target,
                              "내용": r["description"], "담당": r["owner_dept"], "조사": r["investigation_id"],
                              "결정": (r["selected_at"] or "")[:10], "_implemented_at": r["implemented_at"]}))
    return docs


class Embedder:
    """OpenAI embeddings with a file cache keyed by text hash. Raises if the API is not available."""

    def __init__(self, cache_path):
        self.cache_path = Path(cache_path)
        self.vectors = {}
        if self.cache_path.exists():
            data = np.load(self.cache_path, allow_pickle=False)
            self.vectors = dict(zip(data["keys"].tolist(), data["vectors"]))

    @staticmethod
    def key(text):
        return hashlib.sha1(f"{EMBED_MODEL}:{EMBED_DIM}:{text}".encode()).hexdigest()

    def _client(self):
        from .agents.llm import _load_dotenv
        _load_dotenv()
        if os.environ.get("RMA_RAG_EMBEDDING", "").lower() == "off" or not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("embeddings off (RMA_RAG_EMBEDDING=off or no OPENAI_API_KEY)")
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(model=EMBED_MODEL, dimensions=EMBED_DIM, timeout=20, max_retries=1)

    def embed_docs(self, texts):
        missing = list(dict.fromkeys(t for t in texts if self.key(t) not in self.vectors))
        if missing:
            client = self._client()
            for i in range(0, len(missing), BATCH):
                part = missing[i:i + BATCH]
                for text, vec in zip(part, client.embed_documents(part)):
                    self.vectors[self.key(text)] = np.asarray(vec, dtype=np.float32)
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            keys = list(self.vectors)
            np.savez(self.cache_path, keys=np.array(keys), vectors=np.stack([self.vectors[k] for k in keys]))
            log.info("embedded %d new documents (%d cached)", len(missing), len(keys))
        return np.stack([self.vectors[self.key(t)] for t in texts])

    def embed_query(self, text):
        return np.asarray(self._client().embed_query(text), dtype=np.float32)


def _normalise(m):
    norm = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(norm == 0, 1, norm)


class RagIndex:
    def __init__(self, docs, embedder=None):
        self.docs = docs
        self.grams = [_grams(d.text) for d in docs]
        self.idents = [_idents(d.text) for d in docs]
        self.embedder = embedder
        self.matrix = None
        if embedder is not None:
            try:
                self.matrix = _normalise(embedder.embed_docs([d.text for d in docs]))
            except Exception as exc:  # no key / outage: character search only
                log.warning("RAG embeddings unavailable, character search only: %s", exc)

    @property
    def mode(self):
        return "hybrid" if self.matrix is not None else "lexical"

    def search(self, query, as_of=None, audience="QUALITY", types=None, k=4, exclude_rma=None, intake_label=None,
               query_vector=None, w_embed=None):
        """Top k documents for the query known at as_of. Returns [{type, score, rma_id, meta}] (meta filtered for CS)."""
        allowed = set(types or {d.type for d in self.docs})
        if audience == "CS":
            allowed &= CS_TYPES
        keep = [i for i, d in enumerate(self.docs)
                if d.type in allowed and (as_of is None or d.known_at <= as_of) and (exclude_rma is None or d.rma_id != exclude_rma)
                and (intake_label is None or d.type != "CASE" or d.intake_label == intake_label)]
        if not keep:
            return []
        q, q_ids = _grams(query), _idents(query)
        # share of the query's character pairs found in the document (+ exact identifiers such as OLE-02, LT-01603)
        lexical = np.array([len(q & self.grams[i]) / (len(q) or 1) + (IDENT_BONUS * len(q_ids & self.idents[i]) / len(q_ids)
                                                                    if q_ids else 0) for i in keep], dtype=np.float32)
        score = lexical
        if self.matrix is not None:
            try:
                vec = query_vector if query_vector is not None else self.embedder.embed_query(query)
                vec = vec / (np.linalg.norm(vec) or 1)
                w = W_EMBED if w_embed is None else w_embed
                score = w * (self.matrix[keep] @ vec) + (1 - w) * lexical
            except Exception as exc:
                log.warning("RAG query embedding failed, character search only: %s", exc)
        top = np.argsort(-score, kind="stable")[:k]
        out = []
        for j in top:
            d = self.docs[keep[j]]
            meta = {k2: v for k2, v in d.meta.items() if not k2.startswith("_") and v not in (None, "")}
            if d.type == "ACTION":
                done = d.meta.get("_implemented_at")
                meta["시행"] = done[:10] if done and (as_of is None or done <= as_of) else "시행 전"
            if audience == "CS":
                meta = {k2: meta[k2] for k2 in ("rma_id", "접수일", "증상", "제품", "원문") if k2 in meta}
            out.append({"type": d.type, "doc_id": d.doc_id, "score": round(float(score[j]), 3), "rma_id": d.rma_id,
                        "meta": meta})
        return out


_index, _lock, _building = None, threading.Lock(), False


def get_index(build_if_missing=True):
    """The process-wide index (built once from the runtime's DB and case library)."""
    global _index
    with _lock:
        if _index is None and build_if_missing:
            from .agents.runtime import get_runtime
            from .db import DEFAULT_DB
            rt = get_runtime()
            docs = build_docs(rt.conn, rt.masters, rt.library)
            name = "pg" if os.environ.get("RMA_DATABASE_URL") else Path(os.environ.get("RMA_DB") or DEFAULT_DB).stem
            embedder = None if os.environ.get("RMA_RAG_EMBEDDING", "").lower() == "off" else \
                Embedder(CACHE_DIR / f"{name}_{EMBED_MODEL}_{EMBED_DIM}.npz")
            _index = RagIndex(docs, embedder)
            log.info("RAG index: %d documents, %s", len(docs), _index.mode)
        return _index


def reset():
    """Drop the index; the next search rebuilds it (new approved cases after POST /cases/refresh)."""
    global _index
    with _lock:
        _index = None


def search(query, **kwargs):
    return get_index().search(query, **kwargs)


def ready_index():
    """The index if built, else None (chat answers without retrieval while the server builds it)."""
    return _index


def build_in_background():
    """Build the index in a thread (server start, after /cases/refresh) so requests are never blocked by it."""
    global _building
    with _lock:
        if _building:
            return
        _building = True

    def work():
        global _building
        try:
            get_index()
        except Exception:
            log.exception("RAG index build failed")
        finally:
            _building = False

    threading.Thread(target=work, name="rag-index", daemon=True).start()


def status():
    return _index.mode if _index is not None else ("building" if _building else "not_built")
