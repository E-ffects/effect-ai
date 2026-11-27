"""Domain profile loader for Plug & Play manufacturing domain separation.

Defaults to the bundled PCB profile (rma_agent/profiles/pcb.json).
Set RMA_DOMAIN_PROFILE to either:
  - a profile name inside rma_agent/profiles/ (e.g. "battery" -> profiles/battery.json), or
  - an explicit path to a JSON file.

Any omitted keys fall back to generic manufacturing defaults so partial profiles still work safely.
"""
import json
import os
import threading
from pathlib import Path

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
DEFAULT_PROFILE_NAME = "pcb"

_GENERIC_DEFAULTS = {
    "domain_name": "제조",
    "rules_version": "generic-v1",
    "llm_system_prompt": (
        "너는 제조사 품질(QA) 조직의 RMA(반품) 분석 보조다.\n"
        "규칙:\n"
        "- 주어진 사실(JSON)만 쓴다. 사실에 없는 원인, 수치, 날짜, 장비, 조치를 만들지 않는다.\n"
        "- 수치는 사실에 적힌 그대로 옮긴다. 계산하거나 반올림하지 않는다.\n"
        "- 확정되지 않은 원인은 '유력', '의심', '후보'로 쓰고 단정하지 않는다.\n"
        "- 한국어 존댓말, 짧고 구체적인 문장으로 쓴다. 코드값은 쓰지 말고 이름으로 쓴다.\n"
        "- '사실', 'JSON', '입력' 같은 말을 쓰지 않는다. 근거가 없으면 '확인된 기록이 없습니다'처럼 쓴다."
    ),
    "classifier_default_question": (
        "증상 확인을 위해 문의드립니다. 불량 현상, 발생 위치, 재현 조건을 알려 주십시오."
    ),
    "chat_domain_words": [
        "rma", "반품", "불량", "접수", "고객", "품질", "qa", "cs",
        "분석", "증상", "패턴", "검사", "lot", "로트", "원인",
        "회신", "처리", "조치", "재발", "설비", "공정",
        "이 건", "이건", "이거", "여기", "지금",
    ],
    "item_names": {},
    "certainty_names": {"CONFIRMED": "확정", "PROBABLE": "추정"},
    "event_names": {},
    "test_item_names": {},
}

_lock = threading.Lock()
_cached_key = None
_cached_profile = None


def _resolve_path(spec=None):
    raw = (spec if spec is not None else os.environ.get("RMA_DOMAIN_PROFILE", "")).strip()
    if not raw:
        return PROFILES_DIR / f"{DEFAULT_PROFILE_NAME}.json"
    p = Path(raw)
    if p.exists():
        return p
    candidate = PROFILES_DIR / (raw if raw.endswith(".json") else f"{raw}.json")
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"Domain profile not found: {raw!r} (checked {p} and {candidate})")


def load_profile(spec=None):
    path = _resolve_path(spec)
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    merged = dict(_GENERIC_DEFAULTS)
    for k, v in data.items():
        if v is not None:
            merged[k] = v
    return merged


def get_profile():
    global _cached_key, _cached_profile
    key = os.environ.get("RMA_DOMAIN_PROFILE", "").strip()
    with _lock:
        if _cached_profile is None or _cached_key != key:
            _cached_profile = load_profile(key)
            _cached_key = key
        return _cached_profile


def reload_profile(spec=None):
    global _cached_key, _cached_profile
    with _lock:
        _cached_profile = load_profile(spec)
        _cached_key = (spec if spec is not None else os.environ.get("RMA_DOMAIN_PROFILE", "")).strip()
        return _cached_profile
