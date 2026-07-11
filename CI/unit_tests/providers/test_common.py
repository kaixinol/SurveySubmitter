from __future__ import annotations
from survey_submitter.providers.common import (
    SURVEY_PROVIDER_WJX,
    detect_survey_provider,
    ensure_question_provider_fields,
    ensure_questions_provider_fields,
    is_supported_survey_url,
    is_wjx_domain,
    is_wjx_survey_url,
    normalize_survey_provider,
)


class ProviderCommonTests:
    def test_normalize_survey_provider_returns_default_for_unknown_value(self) -> None:
        assert (
            normalize_survey_provider("unknown", default=SURVEY_PROVIDER_WJX) == SURVEY_PROVIDER_WJX
        )

    def test_detect_survey_provider_identifies_wjx(self) -> None:
        assert detect_survey_provider("https://www.wjx.cn/vm/demo.aspx") == SURVEY_PROVIDER_WJX

    def test_wjx_helpers_accept_subdomains(self) -> None:
        assert is_wjx_domain("https://foo.wjx.top/demo")
        assert is_wjx_survey_url("https://sub.v.wjx.cn/m/demo.aspx")
        assert is_wjx_survey_url("https://www.wjx.top/vm/wx9ez4J.aspx")

    def test_is_supported_survey_url_returns_false_for_unknown_domain(self) -> None:
        assert not is_supported_survey_url("https://example.com/form")

    def test_ensure_question_provider_fields_normalizes_provider_metadata(self) -> None:
        result = ensure_question_provider_fields(
            {
                "provider": "  wjx  ",
                "provider_question_id": " question-7 ",
                "provider_page_id": " 1 ",
                "provider_type": " dropdown ",
                "unsupported": 1,
                "unsupported_reason": "  暂不支持  ",
            }
        )
        assert result["provider"] == SURVEY_PROVIDER_WJX
        assert result["provider_question_id"] == "question-7"
        assert result["provider_page_id"] == "1"
        assert result["provider_type"] == "dropdown"
        assert result["unsupported"]
        assert result["unsupported_reason"] == "暂不支持"

    def test_ensure_question_provider_fields_returns_empty_dict_for_non_mapping(self) -> None:
        assert ensure_question_provider_fields("bad") == {}  # ty:ignore[invalid-argument-type]

    def test_ensure_questions_provider_fields_filters_invalid_items(self) -> None:
        result = ensure_questions_provider_fields(
            [{"provider": "wjx", "provider_question_id": " q1 "}, "bad", {"provider": "nope"}],  # ty:ignore[invalid-argument-type]
            default_provider=SURVEY_PROVIDER_WJX,
        )
        assert len(result) == 2
        assert result[0]["provider"] == SURVEY_PROVIDER_WJX
        assert result[0]["provider_question_id"] == "q1"
        assert result[1]["provider"] == SURVEY_PROVIDER_WJX
