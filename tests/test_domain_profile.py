import json

import pytest

from rma_agent import domain_profile
from rma_agent.agents import chat, classifier, graph, llm
from rma_agent.tools import names


def test_default_pcb_profile_loads():
    p = domain_profile.get_profile()
    assert p["domain_name"] == "PCB"
    assert p["rules_version"] == "pcb-v6"
    assert names.item_name("sm_thickness_um") == "PSR 두께"
    assert names.test_item_name("ET_SHORT") == "E-Test 쇼트"
    assert "PCB" in llm.system_prompt()
    assert "pcb" in chat.domain_words()


def test_custom_non_pcb_profile_plug_and_play(tmp_path, monkeypatch):
    battery_json = tmp_path / "battery.json"
    battery_json.write_text(
        json.dumps(
            {
                "domain_name": "배터리 셀",
                "rules_version": "battery-v1",
                "llm_system_prompt": "너는 배터리 셀 제조사 품질(QA) 조직의 RMA 분석 보조다.",
                "classifier_default_question": "배터리 불량 현상(스웰링·전압 강하·발열 등)을 알려 주십시오.",
                "chat_domain_words": ["rma", "배터리", "셀", "스웰링", "전압"],
                "item_names": {"electrolyte_wt_g": "전해액 주액량"},
                "test_item_names": {"OCV_DROP": "OCV 전압 강하"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RMA_DOMAIN_PROFILE", str(battery_json))
    try:
        assert domain_profile.get_profile()["domain_name"] == "배터리 셀"
        assert graph.rules_version() == "battery-v1"
        assert names.item_name("electrolyte_wt_g") == "전해액 주액량"
        assert names.item_name("unknown_metric") == "unknown_metric"
        assert names.test_item_name("OCV_DROP") == "OCV 전압 강하"
        assert "배터리 셀" in llm.system_prompt()
        assert "스웰링" in classifier.default_question()
        assert "배터리" in chat.domain_words()
        assert "pcb" not in chat.domain_words()
    finally:
        monkeypatch.delenv("RMA_DOMAIN_PROFILE", raising=False)
        domain_profile.reload_profile()
