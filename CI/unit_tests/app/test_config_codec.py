from __future__ import annotations
from collections import Counter
import pytest

from survey_submitter.constants import DEFAULT_USER_AGENT, USER_AGENT_PRESETS
from survey_submitter.core.config.codec import (
    CURRENT_CONFIG_SCHEMA_VERSION,
    UserAgentProfile,
    _ensure_supported_config_payload,
    _select_user_agent_from_ratios,
    build_runtime_config_snapshot,
    deserialize_question_entry,
    deserialize_runtime_config,
    normalize_runtime_config_payload,
    serialize_question_entry,
    serialize_runtime_config,
)
from survey_submitter.core.config.schema import (
    RuntimeConfig,
    SurveySection,
    ExecutionSection,
    AnswerConfigSection,
    AISection,
    ReverseFillSection,
)
from survey_submitter.core.questions.schema import QuestionEntry
from survey_submitter.core.reverse_fill.schema import REVERSE_FILL_FORMAT_WJX_SEQUENCE
from survey_submitter.providers.contracts import ensure_survey_question_meta


class ConfigCodecTests:
    def test_default_user_agent_is_pc_web(self) -> None:
        assert DEFAULT_USER_AGENT == USER_AGENT_PRESETS["pc_web"]["ua"]
        assert "Windows NT" in DEFAULT_USER_AGENT

    def test_runtime_config_roundtrip_keeps_reverse_fill_fields(self) -> None:
        config = RuntimeConfig(
            execution=ExecutionSection(
                reverse_fill=ReverseFillSection(
                    enabled=True,
                    source_path="D:/demo.xlsx",
                    format=REVERSE_FILL_FORMAT_WJX_SEQUENCE,
                    start_row=3,
                    threads=4,
                )
            )
        )
        payload = serialize_runtime_config(config)
        restored = deserialize_runtime_config(payload)
        assert "config_schema_version" not in payload
        assert restored.execution.reverse_fill.enabled
        assert restored.execution.reverse_fill.source_path == "D:/demo.xlsx"
        assert restored.execution.reverse_fill.format == REVERSE_FILL_FORMAT_WJX_SEQUENCE
        assert restored.execution.reverse_fill.start_row == 3
        assert restored.execution.reverse_fill.threads == 4

    def test_runtime_config_roundtrip_keeps_answer_datetime_window(self) -> None:
        config = RuntimeConfig(
            execution=ExecutionSection(
                answer_datetime_window=("2026-02-10 09:00:00", "2026-02-10 10:00:00")
            )
        )
        payload = serialize_runtime_config(config)
        restored = deserialize_runtime_config(payload)
        assert payload["execution"]["answer_datetime_window"] == ("2026-02-10 09:00:00", "2026-02-10 10:00:00")  # ty:ignore[not-subscriptable]
        assert restored.execution.answer_datetime_window == ("2026-02-10 09:00:00", "2026-02-10 10:00:00")

    def test_runtime_config_roundtrip_keeps_questions_info_provider_metadata(self) -> None:
        config = RuntimeConfig(
            survey=SurveySection(survey_provider="wjx"),
            answer_config=AnswerConfigSection(
                questions_info=[
                    ensure_survey_question_meta(
                        {
                            "num": 3,
                            "title": "联系方式",
                            "type_code": "3",
                            "provider_question_id": "question-3",
                            "provider_page_id": "page-2",
                            "option_texts": ["姓名", "电话"],
                            "required": True,
                            "logic_parse_status": "unknown",
                            "question_media": [
                                {
                                    "kind": "image",
                                    "scope": "title",
                                    "index": None,
                                    "source_url": "https://example.com/q3.png",
                                    "label": "题干图",
                                }
                            ],
                        }
                    )
                ]
            ),
        )
        payload = serialize_runtime_config(config)
        restored = deserialize_runtime_config(payload)
        assert payload["answer_config"]["questions_info"][0]["provider_question_id"] == "question-3"  # ty:ignore[not-subscriptable]
        assert payload["answer_config"]["questions_info"][0]["provider_page_id"] == "page-2"  # ty:ignore[not-subscriptable]
        assert payload["answer_config"]["questions_info"][0]["required"]  # ty:ignore[not-subscriptable]
        assert payload["answer_config"]["questions_info"][0]["logic_parse_status"] == "unknown"  # ty:ignore[not-subscriptable]
        assert (
            payload["answer_config"]["questions_info"][0]["question_media"][0]["source_url"]  # ty:ignore[not-subscriptable]
            == "https://example.com/q3.png"
        )
        assert len(restored.answer_config.questions_info or []) == 1
        assert restored.answer_config.questions_info is not None
        restored_info = restored.answer_config.questions_info[0]
        assert restored_info.provider_question_id == "question-3"
        assert restored_info.provider_page_id == "page-2"
        assert restored_info.required
        assert restored_info.logic_parse_status == "unknown"
        assert restored_info.question_media[0]["label"] == "题干图"  # ty:ignore[unresolved-attribute]

    def test_build_runtime_config_snapshot_returns_detached_copies(self) -> None:
        config = RuntimeConfig(
            survey=SurveySection(survey_provider="wjx"),
            answer_config=AnswerConfigSection(
                answer_rules=[{"question_num": 1, "equals": [0]}],
                question_entries=[
                    QuestionEntry(
                        question_type="single",
                        probabilities=[60.0, 40.0],
                        texts=["A", "B"],
                        option_count=2,
                        question_num=1,
                    )
                ],
                questions_info=[
                    ensure_survey_question_meta(
                        {
                            "num": 1,
                            "title": "单选题",
                            "type_code": "3",
                            "option_texts": ["A", "B"],
                            "provider_question_id": "q1",
                        }
                    )
                ],
            ),
        )
        snapshot = build_runtime_config_snapshot(config)
        assert snapshot is not config
        assert snapshot.answer_config.question_entries is not config.answer_config.question_entries
        assert snapshot.answer_config.questions_info is not config.answer_config.questions_info
        assert snapshot.answer_config.question_entries[0] is not config.answer_config.question_entries[0]
        assert snapshot.answer_config.questions_info is not None
        assert config.answer_config.questions_info is not None
        assert snapshot.answer_config.questions_info[0] is not config.answer_config.questions_info[0]
        assert snapshot.answer_config.question_entries[0].texts is not None
        snapshot.answer_config.question_entries[0].texts[0] = "已修改"
        snapshot.answer_config.questions_info[0].option_texts[0] = "已修改"  # ty:ignore[unresolved-attribute]
        snapshot.answer_config.answer_rules[0]["equals"][0] = 9
        assert config.answer_config.question_entries[0].texts is not None
        assert config.answer_config.question_entries[0].texts[0] == "A"
        assert config.answer_config.questions_info[0].option_texts[0] == "A"  # ty:ignore[unresolved-attribute]
        assert config.answer_config.answer_rules[0]["equals"][0] == 0

    def test_unknown_fields_raise_corruption_error(self) -> None:
        with pytest.raises(ValueError, match="该配置文件损坏"):
            normalize_runtime_config_payload({"survey": {"url": "https://example.test"}, "unknown_field": 1})
        with pytest.raises(ValueError, match="该配置文件损坏"):
            normalize_runtime_config_payload(
                {
                    "survey": {"url": "https://example.test"},
                    "answer_config": {"question_entries": [{"question_type": "single", "unexpected": 1}]},
                }
            )
        with pytest.raises(ValueError, match="该配置文件损坏"):
            normalize_runtime_config_payload(
                {
                    "survey": {"url": "https://example.test"},
                    "answer_config": {"questions_info": [{"num": 1, "title": "Q1", "unexpected": 1}]},
                }
            )

    def test_ensure_supported_config_payload_keeps_payload_without_schema_version(self) -> None:
        payload = _ensure_supported_config_payload(
            {"survey": {"url": "https://example.test"}}, config_path="demo.json"
        )
        assert payload == {"survey": {"url": "https://example.test"}}

    def test_question_entry_normalizes_text_modes_ranges_provider_and_dimensions(self) -> None:
        entry = deserialize_question_entry(
            {
                "question_type": "text",
                "probabilities": [],
                "texts": ["答案"],
                "rows": "2",
                "option_count": "0",
                "distribution_mode": "custom",
                "custom_weights": [0, "3"],
                "survey_provider": "unknown",
                "provider_question_id": " q1 ",
                "provider_page_id": " p1 ",
                "ai_enabled": True,
                "multi_text_blank_modes": ["name", "bad", "integer"],
                "multi_text_blank_ai_flags": [1, 0],
                "multi_text_blank_int_ranges": [[1, 3], "", ["bad"]],
                "text_random_mode": "integer",
                "text_random_int_range": ["5", "9"],
                "is_location": True,
                "location_parts": ["北京", "北京", "东城区"],
                "dimension": " 未分组 ",
            }
        )

        assert entry.probabilities == [0.0, 3.0]
        assert entry.custom_weights == [0.0, 3.0]
        assert entry.survey_provider == "wjx"
        assert entry.provider_question_id == "q1"
        assert entry.provider_page_id == "p1"
        assert entry.multi_text_blank_modes == ["name", "none", "integer"]
        assert entry.multi_text_blank_ai_flags == [True, False]
        assert entry.text_random_int_range == [5, 9]
        assert entry.is_location is True
        assert entry.location_parts == ["北京", "北京", "东城区"]
        assert entry.dimension is None

        payload = serialize_question_entry(entry)
        assert payload["dimension"] is None
        assert payload["text_random_int_range"] == [5, 9]
        assert payload["location_parts"] == ["北京", "北京", "东城区"]

    def test_normalize_runtime_config_payload_covers_boundaries_and_invalid_values(self) -> None:
        cfg = normalize_runtime_config_payload(
            {
                "survey": {"url": "https://wjx.cn/vm/demo.aspx"},
                "execution": {
                    "target_num": "bad",
                    "num_threads": "4",
                    "submit_interval_range_seconds": ["1", "3"],
                    "answer_duration_range_seconds": ["bad"],
                    "answer_datetime_window": ["2026-02-10 09:00:00", "bad"],
                    "random_proxy_ip": "yes",
                    "proxy_source": "bad",
                    "custom_proxy_api": "https://proxy.example",
                    "random_user_agent": "false",
                    "user_agent_ratios": {"wechat": 20, "mobile": 20, "pc": 20},
                    "reverse_fill": {
                        "format": "bad",
                        "start_row": "-2",
                        "threads": "0",
                    },
                },
                "answer_config": {
                    "questions_info": "bad",
                    "question_entries": [{"question_type": "single", "rows": "bad"}],
                },
            }
        )

        assert cfg.execution.target_num == 1
        assert cfg.execution.num_threads == 4
        assert cfg.execution.submit_interval_range_seconds == (1, 3)
        assert cfg.execution.answer_duration_range_seconds == (60, 120)
        assert cfg.execution.answer_datetime_window == ("2026-02-10 09:00:00", "")
        assert cfg.execution.random_proxy_ip is True
        assert cfg.execution.proxy_source == "default"
        assert cfg.execution.random_user_agent is False
        assert cfg.execution.user_agent_ratios == {"wechat": 33, "mobile": 33, "pc": 34}
        assert cfg.execution.reverse_fill.format == "auto"
        assert cfg.execution.reverse_fill.start_row == 1
        assert cfg.execution.reverse_fill.threads == 1
        assert cfg.answer_config.questions_info == []
        assert len(cfg.answer_config.question_entries) == 1
        assert cfg.answer_config.question_entries[0].rows == 1

    def test_random_ip_enabled_survives_official_proxy_sources(self) -> None:
        for source in ("default", "benefit", "custom"):
            cfg = normalize_runtime_config_payload(
                {
                    "execution": {
                        "random_proxy_ip": True,
                        "proxy_source": source,
                    }
                }
            )

            assert cfg.execution.random_proxy_ip is True
            assert cfg.execution.proxy_source == source

    def test_runtime_config_payload_defaults_proxy_source_to_default(self) -> None:
        assert normalize_runtime_config_payload({}).execution.proxy_source == "default"
        assert normalize_runtime_config_payload({"execution": {"proxy_source": "bad"}}).execution.proxy_source == "default"

    def test_answer_duration_legacy_single_value_expands_to_10_percent_range(self) -> None:
        assert normalize_runtime_config_payload({"execution": {"answer_duration_range_seconds": 90}}).execution.answer_duration_range_seconds == (81, 99)
        assert normalize_runtime_config_payload({"execution": {"answer_duration_range_seconds": ["90"]}}).execution.answer_duration_range_seconds == (
            81,
            99,
        )
        assert normalize_runtime_config_payload(
            {"execution": {"answer_duration_range_seconds": [180, 180]}}
        ).execution.answer_duration_range_seconds == (
            162,
            198,
        )
        assert normalize_runtime_config_payload({}).execution.answer_duration_range_seconds == (60, 120)
        assert normalize_runtime_config_payload({"execution": {"answer_duration_range_seconds": 9999}}).execution.answer_duration_range_seconds == (
            1620,
            1800,
        )
        assert normalize_runtime_config_payload(
            {"execution": {"answer_duration_range_seconds": [1200, 9999]}}
        ).execution.answer_duration_range_seconds == (
            1200,
            1800,
        )

    def test_random_ua_ratio_normalization_ignores_unknown_keys_and_rejects_invalid_values(
        self,
    ) -> None:
        assert normalize_runtime_config_payload(
            {"execution": {"user_agent_ratios": {"wechat": 100, "unknown": 0}}}
        ).execution.user_agent_ratios == {"wechat": 100, "mobile": 0, "pc": 0}
        assert normalize_runtime_config_payload(
            {"execution": {"user_agent_ratios": {"wechat": 50, "unknown": 50}}}
        ).execution.user_agent_ratios == {"wechat": 33, "mobile": 33, "pc": 34}
        assert normalize_runtime_config_payload(
            {"execution": {"user_agent_ratios": {"wechat": 200, "mobile": -100, "pc": 0}}}
        ).execution.user_agent_ratios == {"wechat": 33, "mobile": 33, "pc": 34}

    def test_random_ua_legacy_keys_raise(self) -> None:
        with pytest.raises(ValueError, match="该配置文件损坏"):
            normalize_runtime_config_payload(
                {
                    "execution": {
                        "random_ua_keys": ["pc_web"],
                        "user_agent_ratios": {"wechat": 0, "mobile": 0, "pc": 100},
                    }
                }
            )

    def test_select_user_agent_from_ratios_handles_empty_unknown_and_valid_devices(self) -> None:
        assert _select_user_agent_from_ratios({"wechat": 0, "mobile": 0}) is None
        profile = _select_user_agent_from_ratios({"pc": 1})
        assert isinstance(profile, UserAgentProfile)
        assert profile.ua
        assert profile.label
        assert profile.category == "pc"
        assert _select_user_agent_from_ratios({"unknown": 1}) is None

    def test_select_user_agent_from_ratios_distribution_tracks_weights(self) -> None:
        rng = __import__("random").Random(20260616)
        counts = Counter()

        for _ in range(10000):
            profile = _select_user_agent_from_ratios(
                {"wechat": 55, "mobile": 34, "pc": 11},
                rng=rng,
            )
            assert profile is not None
            counts[profile.category] += 1

        assert abs(counts["wechat"] / 10000 - 0.55) < 0.02
        assert abs(counts["mobile"] / 10000 - 0.34) < 0.02
        assert abs(counts["pc"] / 10000 - 0.11) < 0.02
