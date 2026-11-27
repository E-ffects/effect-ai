"""Korean display names for codes that appear in evidence sentences (the data has codes only).

Evidence summaries use these names; ref_table / ref_id keep the codes for linking to source records.
"""

from ..domain_profile import get_profile

_DEFAULT = get_profile()
ITEM_NAMES = _DEFAULT["item_names"]
CERTAINTY_NAMES = _DEFAULT["certainty_names"]
EVENT_NAMES = _DEFAULT["event_names"]
TEST_ITEM_NAMES = _DEFAULT["test_item_names"]


def item_name(code):
    return get_profile()["item_names"].get(code, code)


def certainty_name(code):
    return get_profile()["certainty_names"].get(code, code)


def event_name(code):
    return get_profile()["event_names"].get(code, code)


def test_item_name(code):
    return get_profile()["test_item_names"].get(code, code)


# ---------- Korean particles after a name (은/는, 이/가, 을/를, 으로/로) for template sentences
_DIGIT_FINAL = {"0": "ㅇ", "1": "ㄹ", "2": "", "3": "ㅁ", "4": "", "5": "", "6": "ㄱ", "7": "ㄹ", "8": "ㄹ", "9": ""}
_LETTER_FINAL = {"L": "ㄹ", "M": "ㅁ", "N": "ㄴ", "R": "ㄹ"}  # el, em, en, ar; other letters end in a vowel
_PAIRS = {"은": ("은", "는"), "이": ("이", "가"), "을": ("을", "를"), "과": ("과", "와"), "으로": ("으로", "로")}


def _final(word):
    """Final consonant sound of the word's last readable character: '' (vowel), 'ㄹ', or another consonant."""
    for ch in reversed(str(word).strip()):
        if "가" <= ch <= "힣":
            code = (ord(ch) - 0xAC00) % 28
            return "" if code == 0 else "ㄹ" if code == 8 else "ㅇ"
        if ch.isdigit():
            return _DIGIT_FINAL[ch]
        if ch.isalpha():
            return _LETTER_FINAL.get(ch.upper(), "")
    return ""


def josa(word, particle):
    """word + the right form of particle ('은', '이', '을', '과', '으로'). josa('외층 에칭 잔동', '은') -> '... 잔동은'."""
    with_final, without = _PAIRS[particle]
    final = _final(word)
    if particle == "으로":
        return f"{word}{without if final in ('', 'ㄹ') else with_final}"
    return f"{word}{with_final if final else without}"
