from __future__ import annotations
import pytest
from unittest.mock import patch
from survey_submitter.core.questions.config import make_question_entry
from survey_submitter.core.config.schema import (
    RuntimeConfig,
    SurveySection,
    ExecutionSection,
    AnswerConfigSection,
)
from survey_submitter.core.reverse_fill.schema import ReverseFillSpec
from survey_submitter.core.engine.execution_builder import (
    PreparedExecutionArtifacts,
    RuntimePreparationError,
    prepare_execution_artifacts,
)
from survey_submitter.providers.contracts import ensure_survey_question_meta


class _FakeHttpResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class RuntimePreparationTests:
    _SAMPLE_QUESTIONS_INFO = [
        ensure_survey_question_meta(
            {"num": 1, "title": "Q1", "provider_question_id": "q1", "provider_page_id": "p1"}
        )
    ]

    def _build_config(self) -> RuntimeConfig:
        config = RuntimeConfig(
            survey=SurveySection(
                url="https://wj.qq.com/s2/demo",
                title="测试问卷",
                provider="qq",
            ),
            execution=ExecutionSection(
                target_num=5,
                num_threads=3,
                answer_duration_range_seconds=(12, 20),
                answer_datetime_window=("", ""),
                submit_interval_range_seconds=(1, 2),
                random_proxy_ip=True,
                random_user_agent=True,
                user_agent_ratios={"wechat": 20, "mobile": 30, "pc": 50},
            ),
            answer_config=AnswerConfigSection(
                answer_rules=[{"num": 1, "equals": [1]}],
                question_entries=[
                    make_question_entry(
                        question_type="single",
                        probabilities=[100.0, 0.0],
                        option_count=2,
                        question_num=1,
                        provider="wjx",
                        provider_question_id="q1",
                        provider_page_id="p1",
                    )
                ],
            ),
        )
        return config

    def test_prepare_execution_artifacts_rejects_empty_question_entries(self) -> None:
        config = RuntimeConfig()
        with pytest.raises(RuntimePreparationError) as cm:
            prepare_execution_artifacts(config)
        assert "未配置任何题目" in cm.value.user_message

    def test_prepare_execution_artifacts_rejects_validation_error(self) -> None:
        config = self._build_config()
        with patch(
            "survey_submitter.core.engine.execution_builder.validate_question_config",
            return_value="第1题配置冲突",
        ):
            with pytest.raises(RuntimePreparationError) as cm:
                prepare_execution_artifacts(
                    config, questions_info=self._SAMPLE_QUESTIONS_INFO
                )
        assert "题目配置存在冲突" in cm.value.user_message
        assert "第1题配置冲突" in cm.value.log_message

    def test_prepare_execution_artifacts_blocks_stopped_wjx_before_runtime(self) -> None:
        config = self._build_config()
        config.survey.url = "https://v.wjx.cn/vm/demo.aspx"
        config.survey.provider = "wjx"
        config.answer_config.question_entries[0].survey_provider = "wjx"
        html = (
            "<html><body><div id='divWorkError'>此问卷处于停止状态，无法作答！</div></body></html>"
        )
        with (
            patch(
                "survey_submitter.network.http.get", return_value=_FakeHttpResponse(html)
            ) as http_get,
            patch(
                "survey_submitter.core.engine.execution_builder.validate_question_config",
                return_value="",
            ),
        ):
            with pytest.raises(RuntimePreparationError) as cm:
                prepare_execution_artifacts(
                    config, questions_info=self._SAMPLE_QUESTIONS_INFO
                )
        assert cm.value.user_message == "问卷已停止，无法作答"
        assert http_get.call_args.kwargs.get("proxies") == {}

    def test_prepare_execution_artifacts_blocks_enterprise_unavailable_wjx_before_runtime(
        self,
    ) -> None:
        config = self._build_config()
        config.survey.url = "https://v.wjx.cn/vm/demo.aspx"
        config.survey.provider = "wjx"
        config.answer_config.question_entries[0].survey_provider = "wjx"
        html = """
        <html><body>
          <div>问卷发布者还未购买企业标准版或企业标准版已到期，此问卷暂时不能被填写！</div>
          <div id="divQuestion"><fieldset><div topic="1" type="3">Q1</div></fieldset></div>
        </body></html>
        """
        with (
            patch("survey_submitter.network.http.get", return_value=_FakeHttpResponse(html)),
            patch(
                "survey_submitter.core.engine.execution_builder.validate_question_config",
                return_value="",
            ),
        ):
            with pytest.raises(RuntimePreparationError) as cm:
                prepare_execution_artifacts(
                    config, questions_info=self._SAMPLE_QUESTIONS_INFO
                )
        assert cm.value.user_message == "问卷发布者企业标准版未购买或已到期，暂时不能填写"

    def test_prepare_execution_artifacts_marks_reverse_fill_error_as_detailed(self) -> None:
        config = self._build_config()
        with patch(
            "survey_submitter.core.engine.execution_builder.build_enabled_reverse_fill_spec",
            side_effect=RuntimeError("反填源文件损坏"),
        ):
            with pytest.raises(RuntimePreparationError) as cm:
                prepare_execution_artifacts(
                    config, questions_info=self._SAMPLE_QUESTIONS_INFO
                )
        assert cm.value.detailed
        assert cm.value.user_message == "反填源文件损坏"

    def test_prepare_execution_artifacts_builds_template_and_questions_metadata(self) -> None:
        config = self._build_config()

        def fake_configure_probabilities(entries, *, ctx, reliability_mode_enabled: bool) -> None:
            assert len(entries) == 1
            assert reliability_mode_enabled
            ctx.single_prob = [[100.0, 0.0]]
            ctx.question_config_index_map = {1: ("single", 0)}

        with (
            patch(
                "survey_submitter.core.engine.execution_builder.build_enabled_reverse_fill_spec",
                return_value=None,
            ),
            patch(
                "survey_submitter.core.engine.execution_builder.configure_probabilities",
                side_effect=fake_configure_probabilities,
            ),
            patch(
                "survey_submitter.core.engine.execution_builder.set_proxy_occupy_minute_by_answer_duration"
            ) as sync_proxy_duration,
        ):
            artifacts = prepare_execution_artifacts(
                config,
                fallback_survey_title="后备标题",
                questions_info=self._SAMPLE_QUESTIONS_INFO,
            )
        assert isinstance(artifacts, PreparedExecutionArtifacts)
        assert artifacts.provider == "wjx"
        assert artifacts.execution_config_template.title == "测试问卷"
        assert artifacts.execution_config_template.target_num == 5
        assert artifacts.execution_config_template.num_threads == 3
        assert artifacts.execution_config_template.question_config_index_map == {1: ("single", 0)}
        assert artifacts.execution_config_template.questions_metadata[1].title == "Q1"
        assert artifacts.execution_config_template.provider_question_metadata_map == {
            "wjx:p1:q1": artifacts.execution_config_template.questions_metadata[1]
        }
        assert artifacts.execution_config_template.answer_rules == [{"num": 1, "equals": [1]}]
        assert artifacts.execution_config_template.answer_datetime_window_ms == (0, 0)
        assert artifacts.execution_config_template.proxy_ip_pool == []
        sync_proxy_duration.assert_called_once_with((12, 20), provider="wjx")

    def test_prepare_execution_artifacts_uses_fallback_title_when_config_title_blank(self) -> None:
        config = self._build_config()
        config.survey.title = ""
        with (
            patch(
                "survey_submitter.core.engine.execution_builder._verify_wjx_survey_is_answerable",
                return_value=None,
            ),
            patch(
                "survey_submitter.core.engine.execution_builder.build_enabled_reverse_fill_spec",
                return_value=None,
            ),
            patch(
                "survey_submitter.core.engine.execution_builder.configure_probabilities",
                return_value=None,
            ),
        ):
            artifacts = prepare_execution_artifacts(
                config,
                fallback_survey_title="解析得到的标题",
                questions_info=self._SAMPLE_QUESTIONS_INFO,
            )
        assert artifacts.execution_config_template.title == "解析得到的标题"
        assert artifacts.provider == "wjx"
        assert len(artifacts.questions_info) == 1
        assert artifacts.questions_info[0].title == "Q1"

    def test_prepare_execution_artifacts_clamps_threads_by_http_limit(self) -> None:
        config = self._build_config()
        config.execution.num_threads = 99
        with (
            patch(
                "survey_submitter.core.engine.execution_builder.build_enabled_reverse_fill_spec",
                return_value=None,
            ),
            patch(
                "survey_submitter.core.engine.execution_builder.configure_probabilities",
                return_value=None,
            ),
        ):
            artifacts = prepare_execution_artifacts(
                config, questions_info=self._SAMPLE_QUESTIONS_INFO
            )
        assert artifacts.execution_config_template.num_threads == 64

    def test_prepare_execution_artifacts_uses_reverse_fill_sample_count_and_threads(self) -> None:
        config = self._build_config()
        config.execution.target_num = 2
        config.execution.num_threads = 8
        config.execution.reverse_fill.threads = 3
        reverse_fill_spec = ReverseFillSpec(
            source_path="D:/demo.xlsx",
            selected_format="wjx_sequence",
            detected_format="wjx_sequence",
            start_row=1,
            total_samples=9,
            available_samples=9,
            target_num=9,
        )
        with (
            patch(
                "survey_submitter.core.engine.execution_builder._verify_wjx_survey_is_answerable",
                return_value=None,
            ),
            patch(
                "survey_submitter.core.engine.execution_builder.build_enabled_reverse_fill_spec",
                return_value=reverse_fill_spec,
            ),
            patch(
                "survey_submitter.core.engine.execution_builder.configure_probabilities",
                return_value=None,
            ),
        ):
            artifacts = prepare_execution_artifacts(
                config, questions_info=self._SAMPLE_QUESTIONS_INFO
            )
        assert artifacts.execution_config_template.target_num == 9
        assert artifacts.execution_config_template.num_threads == 3
