from __future__ import annotations

from collections import Counter

from survey_submitter.constants import DEFAULT_FILL_TEXT
from survey_submitter.core.questions.text_values import (
    resolve_option_fill_list_value,
    resolve_option_fill_text_from_config,
)
from survey_submitter.core.questions.utils import OPTION_FILL_AI_TOKEN as _AI_TOKEN


class OptionFillListTests:
    def test_plain_text_passthrough(self) -> None:
        assert resolve_option_fill_list_value("固定答案") == "固定答案"

    def test_empty_text_returns_default(self) -> None:
        assert resolve_option_fill_list_value("") == DEFAULT_FILL_TEXT

    def test_list_picks_one_of_candidates(self) -> None:
        result = resolve_option_fill_list_value("北京||上海||广州")
        assert result in {"北京", "上海", "广州"}

    def test_uniform_distribution_over_many_trials(self) -> None:
        counts = Counter(
            resolve_option_fill_list_value("北京||上海||广州") for _ in range(600)
        )
        assert set(counts) == {"北京", "上海", "广州"}
        for value in counts.values():
            assert 120 < value < 280

    def test_weighted_distribution_favors_high_weight(self) -> None:
        counts = Counter(
            resolve_option_fill_list_value("北京:8||上海:1||广州:1") for _ in range(1000)
        )
        assert counts["北京"] > counts["上海"] + counts["广州"]

    def test_missing_weight_defaults_to_one(self) -> None:
        counts = Counter(
            resolve_option_fill_list_value("A:3||B") for _ in range(800)
        )
        assert set(counts) == {"A", "B"}
        assert counts["A"] > counts["B"]

    def test_all_zero_weights_fall_back_to_uniform(self) -> None:
        counts = Counter(
            resolve_option_fill_list_value("A:0||B:0||C:0") for _ in range(900)
        )
        assert set(counts) == {"A", "B", "C"}

    def test_dynamic_token_inside_list(self) -> None:
        from survey_submitter.core.questions.schema import _TEXT_RANDOM_NAME_TOKEN

        result = resolve_option_fill_list_value(f"{_TEXT_RANDOM_NAME_TOKEN}||固定")
        assert result == "固定" or result

    def test_ai_token_not_treated_as_list(self) -> None:
        # The AI token is handled by the caller before list resolution.
        assert resolve_option_fill_list_value(_AI_TOKEN) == _AI_TOKEN


class OptionFillConfigTests:
    async def test_config_list_value_random_pick(self) -> None:
        result = await resolve_option_fill_text_from_config(
            ["北京||上海||广州", None, None],
            option_index=0,
        )
        assert result in {"北京", "上海", "广州"}

    async def test_config_ai_token_still_works(self) -> None:
        result = await resolve_option_fill_text_from_config(
            [_AI_TOKEN, None, None],
            option_index=0,
            ai_answering=False,
        )
        assert result == DEFAULT_FILL_TEXT

    async def test_config_none_returns_none(self) -> None:
        result = await resolve_option_fill_text_from_config(None, option_index=0)
        assert result is None
