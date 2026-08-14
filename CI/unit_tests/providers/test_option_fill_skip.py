from __future__ import annotations

import random

import pytest

from survey_submitter.providers.answering.option_fill import (
    option_fill_is_required,
    should_skip_optional_option_fill,
)
from survey_submitter.providers.contracts import ensure_survey_question_meta


def _choice_meta() -> object:
    return ensure_survey_question_meta(
        {
            "num": 1,
            "title": "多选",
            "type_code": "4",
            "option_texts": ["A", "B", "C"],
            "fillable_options": [0, 1, 2],
            "required_fillable_options": [2],
        }
    )


class OptionFillRequiredTests:
    def test_required_index_is_detected(self) -> None:
        assert option_fill_is_required(_choice_meta(), 2) is True

    def test_fillable_but_optional_index_is_not_required(self) -> None:
        assert option_fill_is_required(_choice_meta(), 0) is False
        assert option_fill_is_required(_choice_meta(), 1) is False

    def test_missing_required_field_treats_everything_as_optional(self) -> None:
        meta = ensure_survey_question_meta(
            {
                "num": 2,
                "title": "旧配置",
                "type_code": "4",
                "option_texts": ["A", "B"],
                "fillable_options": [0, 1],
            }
        )
        assert option_fill_is_required(meta, 0) is False
        assert option_fill_is_required(meta, 1) is False


class OptionalFillSkipTests:
    def test_zero_ratio_never_skips(self) -> None:
        assert should_skip_optional_option_fill(_choice_meta(), 0, 0.0) is False
        assert should_skip_optional_option_fill(_choice_meta(), 1, 0.0) is False

    def test_required_option_is_never_skipped(self) -> None:
        assert should_skip_optional_option_fill(_choice_meta(), 2, 1.0) is False

    def test_non_fillable_option_is_never_skipped(self) -> None:
        meta = ensure_survey_question_meta(
            {
                "num": 3,
                "title": "单选",
                "type_code": "3",
                "option_texts": ["A", "B"],
                "fillable_options": [1],
                "required_fillable_options": [1],
            }
        )
        assert should_skip_optional_option_fill(meta, 0, 1.0) is False

    def test_full_ratio_skips_optional_fill(self) -> None:
        assert should_skip_optional_option_fill(_choice_meta(), 0, 1.0) is True
        assert should_skip_optional_option_fill(_choice_meta(), 1, 1.0) is True

    def test_ratio_above_one_is_clamped_to_one(self) -> None:
        assert should_skip_optional_option_fill(_choice_meta(), 0, 1.7) is True

    def test_negative_ratio_is_clamped_to_zero(self) -> None:
        assert should_skip_optional_option_fill(_choice_meta(), 0, -0.5) is False

    def test_mid_ratio_obeys_random(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(random, "random", lambda: 0.2)
        assert should_skip_optional_option_fill(_choice_meta(), 0, 0.5) is True
        monkeypatch.setattr(random, "random", lambda: 0.8)
        assert should_skip_optional_option_fill(_choice_meta(), 0, 0.5) is False
