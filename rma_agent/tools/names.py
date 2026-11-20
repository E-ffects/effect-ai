"""Korean display names for codes that appear in evidence sentences (the data has codes only).

Evidence summaries use these names; ref_table / ref_id keep the codes for linking to source records.
"""

ITEM_NAMES = {
    "rinse_conductivity_us_cm": "린스 전도도", "desmear_rate_mg_cm2": "디스미어 제거량", "eless_cu_um": "무전해 동 두께",
    "hit_count_per_bit": "비트당 타공 수", "spindle_load_pct": "스핀들 부하", "hole_position_um": "홀 위치 편차",
    "aoi_defects_per_panel": "AOI 패널당 불량 수", "line_width_dev_um": "선폭 편차", "conveyor_m_min": "컨베이어 속도",
    "exposure_mj_cm2": "노광량", "press_peak_temp_c": "프레스 최고 온도", "press_pressure_kgf_cm2": "프레스 압력",
    "registration_offset_um": "층간 정렬 편차", "cooling_rate_c_min": "냉각 속도", "aoi_sensitivity_level": "AOI 감도",
    "spray_pressure_bar": "스프레이 압력", "oxide_weight_g_m2": "산화 피막 중량", "bath_temp_c": "약품조 온도",
    "seal_bar_temp_c": "실링 바 온도", "bake_hours": "베이킹 시간", "current_density_asd": "전류 밀도",
    "bath_cu_g_l": "도금액 동 농도", "outline_dev_um": "외형 치수 편차", "ni_thickness_um": "Ni 두께",
    "au_thickness_um": "Au 두께", "ni_bath_mto": "Ni 도금액 사용 횟수(MTO)", "osp_thickness_um": "OSP 두께",
    "sn_thickness_um": "주석 두께", "sm_thickness_um": "PSR 두께", "cure_temp_c": "경화 온도",
}

CERTAINTY_NAMES = {"CONFIRMED": "확정", "PROBABLE": "추정"}

EVENT_NAMES = {
    "PREVENTIVE_MAINTENANCE": "예방 보전", "CALIBRATION": "교정", "BIT_COUNTER_RESET_ANOMALY": "비트 카운터 리셋 이상",
    "BATH_ANALYSIS": "약품조 분석", "BATH_CHANGE": "약품조 교체", "EQUIPMENT_FAULT": "설비 고장", "REPAIR": "수리",
    "NOZZLE_CHECK": "노즐 점검", "FIXTURE_CHECK": "픽스처 점검", "RECIPE_CHANGE": "레시피 변경",
    "CONSUMABLE_CHANGE": "소모품 교체",
}

TEST_ITEM_NAMES = {"ET_SHORT": "E-Test 쇼트", "ET_OPEN": "E-Test 오픈"}


def item_name(code):
    return ITEM_NAMES.get(code, code)


def certainty_name(code):
    return CERTAINTY_NAMES.get(code, code)


def event_name(code):
    return EVENT_NAMES.get(code, code)


def test_item_name(code):
    return TEST_ITEM_NAMES.get(code, code)


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
