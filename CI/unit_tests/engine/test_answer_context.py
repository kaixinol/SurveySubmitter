from __future__ import annotations

from survey_submitter.core.engine.answer_context import (
    AnsweredQuestion,
    build_ai_context_prompt,
    get_answered,
    record_answer,
    reset_answer_context,
)


class AnswerContextTests:
    def test_record_answer_tracks_selected_indices(self) -> None:
        reset_answer_context()
        record_answer(1, "single", selected_indices=[0, 2], selected_texts=["A", "C"])
        answered = get_answered()
        assert len(answered) == 1
        assert answered[1] == AnsweredQuestion(
            question_num=1,
            question_type="single",
            selected_indices=[0, 2],
            selected_texts=["A", "C"],
        )

    def test_record_answer_tracks_text_answer(self) -> None:
        reset_answer_context()
        record_answer(5, "text", text_answer="test answer")
        assert get_answered()[5].text_answer == "test answer"

    def test_record_answer_tracks_matrix_row_answers(self) -> None:
        reset_answer_context()
        record_answer(3, "matrix", selected_indices=[1], row_index=0)
        record_answer(3, "matrix", selected_indices=[2], row_index=1)
        answered = get_answered()[3]
        assert answered.row_answers == {0: [1], 1: [2]}

    def test_reset_answer_context_clears_state(self) -> None:
        record_answer(1, "single", selected_indices=[0])
        reset_answer_context()
        assert get_answered() == {}

    def test_build_ai_context_prompt_includes_recent_answers(self) -> None:
        reset_answer_context()
        record_answer(1, "text", text_answer="some text answer")
        prompt = build_ai_context_prompt()
        assert "前面的作答记录" in prompt
        assert "some text answer" in prompt

    def test_build_ai_context_prompt_returns_empty_when_no_context(self) -> None:
        reset_answer_context()
        assert build_ai_context_prompt() == ""
