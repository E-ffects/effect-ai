"""LLM for sentences only: explanations, review note, improvement drafts, customer reply.

Ranking, evidence checks and every number come from the tools. The LLM rewrites them into sentences.

Settings (environment or effect-ai/.env, never committed):
  RMA_LLM          model as "provider:model" (default openai:gpt-5.4-mini), "off" to use templates only
                   Models in BLOCKED_MODELS are never used (team rule).
  OPENAI_API_KEY   key for the openai provider (other providers: their own key variable)

write() returns None when the LLM is off, fails, or invents a number that is not in the facts.
The caller then keeps its template sentence, so an LLM outage never changes ranking or evidence.
"""
import json
import logging
import os
import re
import threading
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger(__name__)
DEFAULT_MODEL = "openai:gpt-5.4-mini"
BLOCKED_MODELS = {"gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-5.5-pro"}  # not allowed for this project
KEY_VARIABLE = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "google_genai": "GOOGLE_API_KEY"}
TIMEOUT_S = 30

SYSTEM = """너는 PCB 제조사 품질(QA) 조직의 RMA(반품) 분석 보조다.
규칙:
- 주어진 사실(JSON)만 쓴다. 사실에 없는 원인, 수치, 날짜, 장비, 조치를 만들지 않는다.
- 수치는 사실에 적힌 그대로 옮긴다. 계산하거나 반올림하지 않는다.
- 확정되지 않은 원인은 '유력', '의심', '후보'로 쓰고 단정하지 않는다.
- 한국어 존댓말, 짧고 구체적인 문장으로 쓴다. 코드값(예: OL_ETCH_RESIDUE)은 쓰지 말고 이름으로 쓴다.
- '사실', 'JSON', '입력' 같은 말을 쓰지 않는다. 근거가 없으면 '확인된 기록이 없습니다'처럼 쓴다."""

_dotenv_loaded = False
_lock = threading.Lock()
_models = {}


def _load_dotenv():
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    try:
        from dotenv import dotenv_values
    except ImportError:
        return
    # API keys in the project .env win over old keys in the shell; other settings (RMA_LLM, ...) already set
    # in the environment are kept, so tests and one-off runs can override them.
    for name, value in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
        if value and (name.endswith("_API_KEY") or name not in os.environ):
            os.environ[name] = value


def model_name():
    """Configured model, or None when the LLM is off or its key is missing."""
    _load_dotenv()
    name = os.environ.get("RMA_LLM", DEFAULT_MODEL).strip()
    if not name or name.lower() == "off":
        return None
    provider, _, model = name.partition(":") if ":" in name else ("openai", "", name)
    if (model or name).lower() in BLOCKED_MODELS:
        log.error("RMA_LLM=%s is a blocked model; using templates only", name)
        return None
    key = KEY_VARIABLE.get(provider)
    if key and not os.environ.get(key):
        return None
    return name


def _chat_model(name):
    with _lock:
        if name not in _models:
            from langchain.chat_models import init_chat_model
            _models[name] = init_chat_model(name, temperature=0, timeout=TIMEOUT_S, max_retries=1)
        return _models[name]


def generate(task, instruction, facts, schema):
    """One structured call. Raises on any failure (tests replace this function)."""
    model = _chat_model(model_name()).with_structured_output(schema)
    prompt = f"{instruction}\n\n사실(JSON):\n{json.dumps(facts, ensure_ascii=False, indent=1)}"
    result = model.invoke([("system", SYSTEM), ("user", prompt)], config={"run_name": f"llm_{task}"})
    return result.model_dump() if isinstance(result, BaseModel) else result


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def invented_numbers(text, facts):
    """Numbers in the LLM text that do not appear in the facts (a sign of a made-up value)."""
    source = json.dumps(facts, ensure_ascii=False)
    plain = source.replace(",", "")
    return [n for n in _NUMBER.findall(text or "") if n not in source and n.replace(",", "") not in plain]


class Outcome:
    """What happened to the LLM calls of one analysis: used, failed (outage) or rejected (invented numbers)."""

    def __init__(self):
        self.n_used, self.failed, self.rejected = 0, False, False

    @property
    def used(self):
        return self.n_used > 0


def write(task, instruction, facts, schema, texts_of, outcome):
    """Run one LLM task. Returns the parsed dict, or None (caller keeps its template)."""
    if model_name() is None or outcome.failed:  # after one failure in this analysis, skip the rest
        return None
    try:
        result = generate(task, instruction, facts, schema)
    except Exception as exc:  # outage, timeout, bad output: keep the template
        log.warning("LLM %s failed: %s", task, exc)
        outcome.failed = True
        return None
    bad = [n for text in texts_of(result) for n in invented_numbers(text, facts)]
    if bad:
        log.warning("LLM %s rejected, numbers not in facts: %s", task, bad)
        outcome.rejected = True
        return None
    outcome.n_used += 1
    return result
