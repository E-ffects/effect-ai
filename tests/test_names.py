"""Template sentences: Korean particles follow the last sound of the name (rma_agent.tools.names.josa)."""
import re
from pathlib import Path

from rma_agent.tools.names import josa


def test_particles():
    assert josa("외층 에칭 잔동", "은") == "외층 에칭 잔동은"
    assert josa("취급 스크래치", "은") == "취급 스크래치는"
    assert josa("반송품 시험", "이") == "반송품 시험이"
    assert josa("디스미어 부족", "을") == "디스미어 부족을"
    assert josa("E-Test 재검 판정 오처리", "을") == "E-Test 재검 판정 오처리를"
    assert josa("디스미어 부족", "으로") == "디스미어 부족으로"
    assert josa("취급 스크래치", "으로") == "취급 스크래치로"
    assert josa("공정 이물 혼입", "으로") == "공정 이물 혼입으로"
    assert josa("LT-01603", "은") == "LT-01603은"      # 삼
    assert josa("ML-CU_FOIL-B-00859", "이") == "ML-CU_FOIL-B-00859가"  # 구
    assert josa("OLE-02", "은") == "OLE-02는"          # 이
    assert josa("DSM-01", "으로") == "DSM-01로"        # 일 (ㄹ)


def test_no_placeholder_particles_left_in_templates():
    root = Path(__file__).resolve().parents[1] / "rma_agent"
    pattern = re.compile(r"\((은|는|이|가|을|를|으)\)")
    left = [f"{p.name}:{i}" for p in root.rglob("*.py") for i, line in enumerate(p.read_text().splitlines(), 1)
            if pattern.search(line)]
    assert left == []
