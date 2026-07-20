from __future__ import annotations

from survey_submitter.providers.contracts import TextQuestionMeta
from cli import _question_type_label, _type_label


class CliSurveyPrintTests:
    def test_type_label_maps_location_to_region_by_default(self) -> None:
        assert _type_label("location") == "地区"
        assert _type_label("text") == "填空"
        assert _type_label("unknown_type") == "unknown_type"

    def test_question_type_label_distinguishes_university_from_region(self) -> None:
        region = TextQuestionMeta(
            num=1,
            title="所在地区",
            type_code="location",
            is_location=True,
            location_verify_type="省市区",
        )
        school = TextQuestionMeta(
            num=2,
            title="你的学校全称是？",
            type_code="location",
            is_location=True,
            location_verify_type="高校",
        )

        assert _question_type_label(region) == "地区"
        assert _question_type_label(school) == "高校"
