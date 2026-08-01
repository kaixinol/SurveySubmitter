from __future__ import annotations

import threading
from dataclasses import dataclass, field

from survey_submitter.core.questions.types import QuestionType


@dataclass
class AnsweredQuestion:
    question_num: int
    question_type: str
    selected_indices: list[int] = field(default_factory=list)
    selected_texts: list[str] = field(default_factory=list)
    text_answer: str = ""
    row_answers: dict[int, list[int]] = field(default_factory=dict)


_thread_local = threading.local()


def reset_answer_context() -> None:
    _thread_local.answered = {}


def record_answer(
    question_num: int,
    question_type: str,
    selected_indices: list[int] | None = None,
    selected_texts: list[str] | None = None,
    text_answer: str = "",
    row_index: int | None = None,
) -> None:

    ctx = getattr(_thread_local, "answered", None)
    if ctx is None:
        _thread_local.answered = {}
        ctx = _thread_local.answered
    if row_index is not None:
        if question_num not in ctx:
            ctx[question_num] = AnsweredQuestion(
                question_num=question_num,
                question_type=question_type,
            )
        ctx[question_num].row_answers[row_index] = selected_indices or []
    else:
        ctx[question_num] = AnsweredQuestion(
            question_num=question_num,
            question_type=question_type,
            selected_indices=selected_indices or [],
            selected_texts=selected_texts or [],
            text_answer=text_answer,
        )


def get_answered() -> dict[int, AnsweredQuestion]:

    return getattr(_thread_local, "answered", {})


def build_ai_context_prompt() -> str:

    parts: list[str] = []

    answered = get_answered()
    if answered:
        sorted_questions = sorted(answered.items(), key=lambda x: x[0])

        recent = sorted_questions[-10:]
        if recent:
            summary_lines = []
            for q_num, record in recent:
                if record.question_type == QuestionType.TEXT and record.text_answer:
                    summary_lines.append(f"  第{q_num}题(填空): {record.text_answer[:50]}")
                elif record.selected_texts:
                    texts = "、".join(record.selected_texts[:3])
                    summary_lines.append(f"  第{q_num}题: 选了「{texts}」")
            if summary_lines:
                parts.append("你在这份问卷中前面的作答记录：")
                parts.extend(summary_lines)
                parts.append("请保持与前面回答的一致性。")

    return "\n".join(parts)
