from __future__ import annotations

from unittest.mock import patch

import pytest

from survey_submitter.core.config.schema import (
    AnswerConfigSection,
    AnswerRulesConfig,
    ExecutionSection,
    ProxySection,
    QuestionInfo,
    RuntimeConfig,
    SurveySection,
)
from survey_submitter.core.engine.execution_builder import (
    PreparedExecutionArtifacts,
    RuntimePreparationError,
    prepare_execution_artifacts,
)
from survey_submitter.core.questions.schema import ChoiceQuestionAnswerConfig, QuestionDetail
from survey_submitter.core.reverse_fill.schema import ReverseFillSpec
from survey_submitter.providers.contracts import ensure_survey_question_meta


class _FakeHttpResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


@pytest.mark.config
class RuntimePreparationTests:
    _SAMPLE_QUESTIONS_INFO = [
        ensure_survey_question_meta(
            {"num": 1, "title": "Q1", "provider_question_id": "q1", "provider_page_id": "p1"}
        )
    ]

    def setup_method(self, _method) -> None:
        from survey_submitter.network import proxy as proxy_runtime

        self._original_override = proxy_runtime.get_custom_proxy_api_override()

    def teardown_method(self, _method) -> None:
        from survey_submitter.network import proxy as proxy_runtime

        proxy_runtime.set_proxy_api_override(self._original_override)

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
                proxy=ProxySection(
                    enabled=True,
                    custom_api_url="https://proxy.example/api",
                ),
                random_user_agent=True,
                user_agent_ratios={"wechat": 20, "mobile": 30, "pc": 50},
            ),
            answer_config=AnswerConfigSection(
                answer_rules=AnswerRulesConfig(
                    constraints=[{"num": 1, "equals": [1]}],
                ),
                survey_questions=[
                    QuestionInfo(
                        num=1,
                        title="",
                        question_type="single",
                        options=["", ""],
                        details=QuestionDetail(
                            probabilities=[100.0, 0.0],
                            provider_question_id="q1",
                            provider_page_id="p1",
                            answer_config=ChoiceQuestionAnswerConfig(),
                        ),
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
                prepare_execution_artifacts(config, questions_info=self._SAMPLE_QUESTIONS_INFO)
        assert "题目配置存在冲突" in cm.value.user_message
        assert "第1题配置冲突" in cm.value.log_message

    def test_prepare_execution_artifacts_blocks_stopped_wjx_before_runtime(self) -> None:
        config = self._build_config()
        config.survey.url = "https://v.wjx.cn/vm/demo.aspx"
        config.survey.provider = "wjx"
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
                prepare_execution_artifacts(config, questions_info=self._SAMPLE_QUESTIONS_INFO)
        assert cm.value.user_message == "问卷已停止，无法作答"
        assert http_get.call_args.kwargs.get("proxies") == {}

    def test_prepare_execution_artifacts_blocks_enterprise_unavailable_wjx_before_runtime(
        self,
    ) -> None:
        config = self._build_config()
        config.survey.url = "https://v.wjx.cn/vm/demo.aspx"
        config.survey.provider = "wjx"
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
                prepare_execution_artifacts(config, questions_info=self._SAMPLE_QUESTIONS_INFO)
        assert cm.value.user_message == "问卷发布者企业标准版未购买或已到期，暂时不能填写"

    def test_prepare_execution_artifacts_marks_reverse_fill_error_as_detailed(self) -> None:
        config = self._build_config()
        with patch(
            "survey_submitter.core.engine.execution_builder.build_enabled_reverse_fill_spec",
            side_effect=RuntimeError("反填源文件损坏"),
        ):
            with pytest.raises(RuntimePreparationError) as cm:
                prepare_execution_artifacts(config, questions_info=self._SAMPLE_QUESTIONS_INFO)
        assert cm.value.detailed
        assert cm.value.user_message == "反填源文件损坏"

    def test_prepare_execution_artifacts_builds_template_and_questions_metadata(self) -> None:
        config = self._build_config()

        def fake_configure_probabilities(entries, *, ctx, reliability_mode_enabled: bool) -> None:
            assert len(entries) == 1
            assert reliability_mode_enabled
            ctx.answer_probs.single_prob = [[100.0, 0.0]]
            ctx.question_maps.question_config_index_map = {1: ("single", 0)}

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
        assert artifacts.execution_config_template.survey.title == "测试问卷"
        assert artifacts.execution_config_template.control.target_num == 5
        assert artifacts.execution_config_template.control.num_threads == 3
        assert artifacts.execution_config_template.question_maps.question_config_index_map == {1: ("single", 0)}
        assert artifacts.execution_config_template.question_maps.questions_metadata[1].title == "Q1"
        assert artifacts.execution_config_template.question_maps.provider_question_metadata_map == {
            "wjx:p1:q1": artifacts.execution_config_template.question_maps.questions_metadata[1]
        }
        assert artifacts.execution_config_template.answer_policy.answer_rules == [{"num": 1, "equals": [1]}]
        assert artifacts.execution_config_template.control.answer_datetime_window_ms == (0, 0)
        assert artifacts.execution_config_template.proxy_ip_pool == []
        sync_proxy_duration.assert_called_once_with((12, 20), provider="wjx")

    def test_prepare_execution_artifacts_seeds_proxy_ip_list_into_pool(self) -> None:
        config = self._build_config()
        config.execution.proxy.ip_list = ["1.2.3.4:8080", "5.6.7.8:3128"]
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
                config, questions_info=self._SAMPLE_QUESTIONS_INFO
            )
        pool = list(artifacts.execution_config_template.proxy_ip_pool)
        assert [lease.address for lease in pool] == ["http://1.2.3.4:8080", "http://5.6.7.8:3128"]
        assert all(lease.source == "custom" for lease in pool)

    def test_prepare_execution_artifacts_blocks_random_proxy_without_api_or_static_pool(
        self,
    ) -> None:
        config = self._build_config()
        config.execution.proxy.custom_api_url = ""
        config.execution.proxy.ip_list = []
        with pytest.raises(RuntimePreparationError) as cm:
            prepare_execution_artifacts(config, questions_info=self._SAMPLE_QUESTIONS_INFO)
        assert "未配置代理API地址" in cm.value.user_message

    def test_prepare_execution_artifacts_syncs_config_custom_proxy_api_into_runtime(
        self,
    ) -> None:
        from survey_submitter.network import proxy as proxy_runtime

        config = self._build_config()
        config.execution.proxy.custom_api_url = "https://proxy.example/api?num=3"
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
            prepare_execution_artifacts(config, questions_info=self._SAMPLE_QUESTIONS_INFO)
        assert (
            proxy_runtime.get_custom_proxy_api_override()
            == "https://proxy.example/api?num=3"
        )

    def test_prepare_execution_artifacts_rejects_invalid_proxy_api_scheme(self) -> None:
        config = self._build_config()
        config.execution.proxy.custom_api_url = "ftp://proxy.example/api"
        with pytest.raises(RuntimePreparationError) as cm:
            prepare_execution_artifacts(config, questions_info=self._SAMPLE_QUESTIONS_INFO)
        assert "http:// 或 https://" in cm.value.user_message

    def test_prepare_execution_artifacts_blocks_local_proxy_without_ip_list(self) -> None:
        config = self._build_config()
        config.execution.proxy.source = "local"
        config.execution.proxy.custom_api_url = ""
        config.execution.proxy.ip_list = []
        with pytest.raises(RuntimePreparationError) as cm:
            prepare_execution_artifacts(config, questions_info=self._SAMPLE_QUESTIONS_INFO)
        assert "未配置静态代理列表" in cm.value.user_message

    def test_prepare_execution_artifacts_local_proxy_clears_api_override(self) -> None:
        from survey_submitter.network import proxy as proxy_runtime

        config = self._build_config()
        config.execution.proxy.source = "local"
        config.execution.proxy.custom_api_url = ""
        config.execution.proxy.reuse = True
        config.execution.proxy.ip_list = ["1.2.3.4:8080"]
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
            patch(
                "survey_submitter.core.engine.execution_builder.is_proxy_responsive",
                return_value=True,
            ),
        ):
            artifacts = prepare_execution_artifacts(
                config, questions_info=self._SAMPLE_QUESTIONS_INFO
            )
        assert proxy_runtime.get_custom_proxy_api_override() == ""
        assert artifacts.execution_config_template.network.proxy.enabled is True
        assert artifacts.execution_config_template.network.proxy.source == "local"
        assert artifacts.execution_config_template.network.proxy.reuse is True
        pool = list(artifacts.execution_config_template.proxy_ip_pool)
        assert [lease.address for lease in pool] == ["http://1.2.3.4:8080"]

    def test_prepare_execution_artifacts_local_proxy_resolves_file_and_url_sources(
        self,
    ) -> None:
        import tempfile

        from survey_submitter.network.proxy.pool import is_proxy_responsive

        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as handle:
            handle.write("# comment\n1.2.3.4:8080\n\n5.6.7.8:3128\n")
            file_path = handle.name

        config = self._build_config()
        config.execution.proxy.source = "local"
        config.execution.proxy.ip_list = [
            file_path,
            "https://proxy.example/list.txt",
        ]
        url_body = "5.6.7.8:3128, 9.10.11.12:1080\n# dup\n9.10.11.12:1080"

        def fake_get(url, *args, **kwargs):
            class _Resp:
                status_code = 200
                text = url_body

            return _Resp()

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
            patch(
                "survey_submitter.core.engine.execution_builder.is_proxy_responsive",
                return_value=True,
            ),
            patch(
                "survey_submitter.network.proxy.local_source.http_client.get",
                side_effect=fake_get,
            ),
        ):
            artifacts = prepare_execution_artifacts(
                config, questions_info=self._SAMPLE_QUESTIONS_INFO
            )
        pool = [lease.address for lease in artifacts.execution_config_template.proxy_ip_pool]
        assert pool == [
            "http://1.2.3.4:8080",
            "http://5.6.7.8:3128",
            "http://9.10.11.12:1080",
        ]
        assert len(pool) == len(set(pool))

    def test_prepare_execution_artifacts_local_proxy_drops_unresponsive_keeps_responsive(
        self,
    ) -> None:
        config = self._build_config()
        config.execution.proxy.source = "local"
        config.execution.proxy.ip_list = ["1.2.3.4:8080", "5.6.7.8:3128"]

        def fake_responsive(address: str) -> bool:
            return address == "http://5.6.7.8:3128"

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
            patch(
                "survey_submitter.core.engine.execution_builder.is_proxy_responsive",
                side_effect=fake_responsive,
            ),
        ):
            artifacts = prepare_execution_artifacts(
                config, questions_info=self._SAMPLE_QUESTIONS_INFO
            )
        pool = [lease.address for lease in artifacts.execution_config_template.proxy_ip_pool]
        assert pool == ["http://5.6.7.8:3128"]

    def test_prepare_execution_artifacts_local_proxy_all_unresponsive_raises(self) -> None:
        config = self._build_config()
        config.execution.proxy.source = "local"
        config.execution.proxy.ip_list = ["1.2.3.4:8080", "5.6.7.8:3128"]
        with (
            patch(
                "survey_submitter.core.engine.execution_builder.is_proxy_responsive",
                return_value=False,
            ),
        ):
            with pytest.raises(RuntimePreparationError) as cm:
                prepare_execution_artifacts(
                    config, questions_info=self._SAMPLE_QUESTIONS_INFO
                )
        assert "无法连接 wjx.cn" in cm.value.user_message

    def test_prepare_execution_artifacts_local_proxy_respects_target_num_cap(self) -> None:
        config = self._build_config()
        config.execution.target_num = 2
        config.execution.proxy.source = "local"
        config.execution.proxy.ip_list = [f"10.0.0.{i}:8080" for i in range(20)]
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
            patch(
                "survey_submitter.core.engine.execution_builder.is_proxy_responsive",
                return_value=True,
            ),
        ):
            artifacts = prepare_execution_artifacts(
                config, questions_info=self._SAMPLE_QUESTIONS_INFO
            )
        pool = [lease.address for lease in artifacts.execution_config_template.proxy_ip_pool]
        assert len(pool) == 3  # ceil(2 * 1.5)
        assert set(pool).issubset(
            {f"http://10.0.0.{i}:8080" for i in range(20)}
        )

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
        assert artifacts.execution_config_template.survey.title == "解析得到的标题"
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
        assert artifacts.execution_config_template.control.num_threads == 64

    def test_prepare_execution_artifacts_plumbs_optional_fill_skip_ratio(self) -> None:
        config = self._build_config()
        config.answer_config.optional_fill_skip_ratio = 0.4
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
        assert artifacts.execution_config_template.choice_fill.optional_fill_skip_ratio == 0.4

    def test_prepare_execution_artifacts_clamps_optional_fill_skip_ratio(self) -> None:
        config = self._build_config()
        config.answer_config.optional_fill_skip_ratio = 1.7
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
        assert artifacts.execution_config_template.choice_fill.optional_fill_skip_ratio == 1.0

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
        assert artifacts.execution_config_template.control.target_num == 9
        assert artifacts.execution_config_template.control.num_threads == 3
