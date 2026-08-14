"""Generate a YAML config from a survey URL using the project's own APIs."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from survey_submitter.core.config.codec import survey_questions_from_definition  # noqa: E402
from survey_submitter.core.config.schema import (  # noqa: E402
    AnswerConfigSection,
    AnswerRulesConfig,
    ExecutionSection,
    RuntimeConfig,
    SurveySection,
)
from survey_submitter.core.config.yaml_loader import save_yaml_config  # noqa: E402
from survey_submitter.core.questions.default_builder import build_default_survey_questions  # noqa: E402
from survey_submitter.providers.registry import parse_survey  # noqa: E402


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "https://v.wjx.cn/vm/wFJsDQ4.aspx#"
    output = sys.argv[2] if len(sys.argv) > 2 else "survey_wFJsDQ4.yaml"

    print(f"[generate] 解析问卷: {url}")
    definition = await parse_survey(url)
    print(f"[generate] 标题: {definition.title}, 题数: {len(definition.questions)}")

    survey_questions = survey_questions_from_definition(definition.questions)
    question_entries = build_default_survey_questions(
        definition.questions,
        survey_url=url,
        existing_entries=survey_questions,
    )

    config = RuntimeConfig(
        survey=SurveySection(
            url=url,
            title=definition.title,
            provider=definition.provider,
        ),
        execution=ExecutionSection(
            target_num=100,
            num_threads=4,
            submit_interval_range_seconds=(5, 15),
            answer_duration_range_seconds=(60, 180),
            answer_datetime_window=("", ""),
            random_proxy_ip=False,
            proxy_source="default",
            custom_proxy_api="",
            proxy_area_code=None,
            random_user_agent=False,
            user_agent_ratios={"wechat": 33, "mobile": 33, "pc": 34},
            stop_on_fail=True,
            pause_on_aliyun_captcha=True,
            reliability_mode=True,
        ),
        answer_config=AnswerConfigSection(
            survey_questions=question_entries,
            answer_rules=AnswerRulesConfig(),
            test_profiles=[],
        ),
    )

    save_yaml_config(config, output)
    print(f"[generate] 已保存: {output}")


if __name__ == "__main__":
    asyncio.run(main())
