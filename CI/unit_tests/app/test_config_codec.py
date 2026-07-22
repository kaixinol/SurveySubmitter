from __future__ import annotations
from collections import Counter
import pytest

from survey_submitter.constants import DEFAULT_USER_AGENT, USER_AGENT_PRESETS
from survey_submitter.core.config.codec import (
    UserAgentProfile,
    _ensure_supported_config_payload,
    _select_user_agent_from_ratios,
    build_runtime_config_snapshot,
    deserialize_question_detail,
    deserialize_runtime_config,
    normalize_runtime_config_payload,
    serialize_question_detail,
    serialize_runtime_config,
    survey_questions_from_definition,
)
from survey_submitter.core.config.schema import (
    RuntimeConfig,
    SurveySection,
    ExecutionSection,
    AnswerConfigSection,
    AnswerRulesConfig,
    ReverseFillSection,
    QuestionInfo,
)
from survey_submitter.providers.contracts import ensure_survey_question_meta
from survey_submitter.core.questions.schema import (
    TextQuestionAnswerConfig,
    MultiTextQuestionAnswerConfig,
    LocationQuestionAnswerConfig,
    QuestionDetail,
)
from survey_submitter.core.reverse_fill.schema import REVERSE_FILL_FORMAT_WJX_SEQUENCE


def _make_question_info(
    *,
    question_type: str = "single",
    num: int = 1,
    probabilities=None,
    options: list[str] | None = None,
    **detail_kwargs,
) -> QuestionInfo:
    detail = QuestionDetail(probabilities=probabilities, **detail_kwargs)
    return QuestionInfo(
        num=num,
        title=f"Q{num}",
        question_type=question_type,
        options=options or [],
        details=detail,
    )


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
        assert payload["execution"]["answer_datetime_window"] == ("2026-02-10 09:00:00", "2026-02-10 10:00:00")
        assert restored.execution.answer_datetime_window == ("2026-02-10 09:00:00", "2026-02-10 10:00:00")

    def test_build_runtime_config_snapshot_returns_detached_copies(self) -> None:
        qi = _make_question_info(
            question_type="single",
            num=1,
            probabilities=[60.0, 40.0],
            options=["A", "B"],
        )
        config = RuntimeConfig(
            survey=SurveySection(provider="wjx"),
            answer_config=AnswerConfigSection(
                answer_rules=AnswerRulesConfig(
                    constraints=[{"question_num": 1, "equals": [0]}],
                ),
                survey_questions=[qi],
            ),
        )
        snapshot = build_runtime_config_snapshot(config)
        assert snapshot is not config
        assert snapshot.answer_config.survey_questions is not config.answer_config.survey_questions
        assert snapshot.answer_config.survey_questions[0] is not config.answer_config.survey_questions[0]
        snapshot.answer_config.answer_rules.constraints[0]["equals"][0] = 9
        assert config.answer_config.answer_rules.constraints[0]["equals"][0] == 0

    def test_unknown_fields_raise_corruption_error(self) -> None:
        with pytest.raises(ValueError, match="该配置文件损坏"):
            normalize_runtime_config_payload({"survey": {"url": "https://example.test"}, "unknown_field": 1})
        with pytest.raises(ValueError, match="该配置文件损坏"):
            normalize_runtime_config_payload(
                {
                    "survey": {"url": "https://example.test"},
                    "answer_config": {"survey_questions": [{"question_type": "single", "unexpected": 1}]},
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

    def test_survey_questions_roundtrip_preserves_minimal_fields(self) -> None:
        config = RuntimeConfig(
            survey=SurveySection(provider="wjx"),
            answer_config=AnswerConfigSection(
                survey_questions=[
                    QuestionInfo(
                        num=1,
                        title="性别",
                        question_type="single",
                        options=["男", "女"],
                        required=True,
                    ),
                    QuestionInfo(num=2, title="建议", question_type="text", options=[]),
                ],
            ),
        )
        payload = serialize_runtime_config(config)
        assert "survey_questions" in payload["answer_config"]
        assert payload["answer_config"]["survey_questions"][0]["question_type"] == "single"
        assert payload["answer_config"]["survey_questions"][0]["options"] == ["男", "女"]
        assert payload["answer_config"]["survey_questions"][1]["options"] == []
        restored = deserialize_runtime_config(payload)
        assert len(restored.answer_config.survey_questions) == 2
        assert restored.answer_config.survey_questions[0].title == "性别"
        assert restored.answer_config.survey_questions[0].question_type == "single"

    def test_config_without_survey_questions_still_loads(self) -> None:
        config = normalize_runtime_config_payload(
            {
                "survey": {"url": "https://example.test"},
                "answer_config": {"answer_rules": []},
            }
        )
        assert config.answer_config.survey_questions == []

    def test_survey_questions_from_definition_extracts_minimal_fields(self) -> None:
        definition = [
            ensure_survey_question_meta(
                {
                    "num": 1,
                    "title": "性别",
                    "type_code": "single",
                    "option_texts": ["男", "女"],
                    "required": True,
                    "provider_question_id": "q1",
                    "provider_page_id": "p1",
                }
            ),
            ensure_survey_question_meta(
                {"num": 2, "title": "建议", "type_code": "text"}
            ),
        ]
        infos = survey_questions_from_definition(definition)
        assert len(infos) == 2
        assert infos[0].num == 1
        assert infos[0].title == "性别"
        assert infos[0].question_type == "single"
        assert infos[0].options == ["男", "女"]
        assert infos[0].required is True
        assert infos[1].question_type == "text"
        assert infos[1].options == []

    def test_question_detail_normalizes_text_modes_ranges_and_dimensions(self) -> None:
        qi = deserialize_question_detail(
            {
                "num": 1,
                "title": "Q1",
                "question_type": "text",
                "options": [],
                "details": {
                    "provider_question_id": " q1 ",
                    "provider_page_id": " p1 ",
                    "probabilities": [],
                    "distribution_mode": "custom",
                    "custom_weights": [0, "3"],
                    "dimension": " 未分组 ",
                    "answer_config": {
                        "ai_enabled": True,
                        "text_random_mode": "integer",
                        "text_random_int_range": ["5", "9"],
                    },
                },
            }
        )

        assert qi.details.probabilities == [0.0, 3.0]
        assert qi.details.custom_weights == [0.0, 3.0]
        assert qi.details.provider_question_id == "q1"
        assert qi.details.provider_page_id == "p1"
        assert isinstance(qi.details.answer_config, TextQuestionAnswerConfig)
        assert qi.details.answer_config.text_random_int_range == [5, 9]
        assert qi.details.dimension is None

        payload = serialize_question_detail(qi)
        assert payload["details"]["dimension"] is None
        assert payload["details"]["answer_config"]["text_random_int_range"] == [5, 9]

    def test_question_detail_location_parts_produces_location_config(self) -> None:
        qi = deserialize_question_detail(
            {
                "num": 1,
                "title": "Q1",
                "question_type": "text",
                "options": [],
                "details": {
                    "provider_question_id": " q1 ",
                    "provider_page_id": " p1 ",
                    "probabilities": [],
                    "distribution_mode": "custom",
                    "custom_weights": [0, "3"],
                    "dimension": " 未分组 ",
                    "answer_config": {
                        "ai_enabled": True,
                        "location_parts": ["北京", "北京", "东城区"],
                    },
                },
            }
        )

        assert isinstance(qi.details.answer_config, LocationQuestionAnswerConfig)
        assert qi.details.answer_config.location_parts == ["北京", "北京", "东城区"]

        payload = serialize_question_detail(qi)
        assert payload["details"]["answer_config"]["location_parts"] == ["北京", "北京", "东城区"]

    def test_question_detail_normalizes_multi_text_blank_fields(self) -> None:
        qi = deserialize_question_detail(
            {
                "num": 1,
                "title": "Q1",
                "question_type": "multi_text",
                "options": [],
                "details": {
                    "probabilities": [],
                    "answer_config": {
                        "multi_text_blank_modes": ["name", "bad", "integer"],
                        "multi_text_blank_ai_flags": [1, 0],
                        "multi_text_blank_int_ranges": [[1, 3], "", ["bad"]],
                    },
                },
            }
        )

        assert isinstance(qi.details.answer_config, MultiTextQuestionAnswerConfig)
        assert qi.details.answer_config.multi_text_blank_modes == ["name", "none", "integer"]
        assert qi.details.answer_config.multi_text_blank_ai_flags == [True, False]

        payload = serialize_question_detail(qi)
        assert payload["details"]["answer_config"]["multi_text_blank_modes"] == ["name", "none", "integer"]
        assert payload["details"]["answer_config"]["multi_text_blank_ai_flags"] == [True, False]

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
                    "survey_questions": [{"num": 1, "title": "Q1", "question_type": "single", "options": []}],
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
        assert len(cfg.answer_config.survey_questions) == 1

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
