"""问卷星 HTML 解析器回归对账工具。

对 ``CI/fixtures/wjx`` 下的真实页面 DOM 跑解析，逐字段与基线对比。

用法::

    # 生成 / 刷新基线（默认用当前实现）
    python CI/scripts/wjx_parser_regression.py --emit

    # 用指定的解析器模块生成基线（例如从 git 历史恢复的旧实现，
    # 放到某个临时包路径下再用 --impl 指向它）
    python CI/scripts/wjx_parser_regression.py --emit --impl <模块路径>

    # 与基线对账（改造解析器后跑）
    python CI/scripts/wjx_parser_regression.py --check

基线只保存解析结果，因此旧实现可以整体替换后再对账，
只要解析出的字段集合与取值保持一致即视为通过。
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_IMPL = "survey_submitter.providers.wjx.html_parser"

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

FIXTURE_DIR = REPO_ROOT / "CI" / "fixtures" / "wjx"
BASELINE_FILE = FIXTURE_DIR / "baseline.json"


def _load_parser(impl: str = DEFAULT_IMPL) -> Any:
    module = importlib.import_module(impl)
    return module.parse_survey_questions_from_html, module.extract_survey_title_from_html


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def collect(impl: str = DEFAULT_IMPL) -> dict[str, Any]:
    parse_questions, extract_title = _load_parser(impl)
    result: dict[str, Any] = {}
    for path in sorted(FIXTURE_DIR.glob("*.html")):
        html = path.read_text(encoding="utf-8")
        result[path.name] = {
            "title": extract_title(html),
            "questions": parse_questions(html),
        }
    return _jsonable(result)


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            out.update(_flatten(item, f"{prefix}[{index}]"))
        return out
    return {prefix: value}


def diff(expected: Any, actual: Any) -> list[str]:
    expected_flat = _flatten(expected)
    actual_flat = _flatten(actual)
    problems: list[str] = []
    for key in sorted(set(expected_flat) | set(actual_flat)):
        want = expected_flat.get(key, "<缺失>")
        got = actual_flat.get(key, "<缺失>")
        if want != got:
            problems.append(f"    {key}: 基线={want!r} 实际={got!r}")
    return problems


def emit(impl: str = DEFAULT_IMPL) -> int:
    data = collect(impl)
    BASELINE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    total = sum(len(v["questions"]) for v in data.values())
    print(f"已写入基线：{BASELINE_FILE.relative_to(REPO_ROOT)}（实现 {impl}）")
    for name, payload in data.items():
        types = sorted({str(q.get("type_code")) for q in payload["questions"]})
        print(f"  {name:<20} 题目 {len(payload['questions']):>3}  题型 {types}")
    print(f"合计 {total} 道题")
    return 0


def check() -> int:
    if not BASELINE_FILE.exists():
        print("基线不存在，请先执行 --emit")
        return 2
    expected = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    actual = collect()
    failed = False
    for name in sorted(set(expected) | set(actual)):
        want = expected.get(name)
        got = actual.get(name)
        if want is None:
            print(f"[新增] {name}：基线中没有该用例")
            failed = True
            continue
        if got is None:
            print(f"[缺失] {name}：解析结果中找不到该用例")
            failed = True
            continue
        problems = diff(want, got)
        if problems:
            failed = True
            print(f"[失败] {name}：{len(problems)} 处字段不一致")
            for line in problems[:40]:
                print(line)
            if len(problems) > 40:
                print(f"    ... 另有 {len(problems) - 40} 处")
        else:
            print(f"[通过] {name}：{len(got['questions'])} 道题逐字段一致")
    if failed:
        print("\n回归失败：新实现与基线存在字段差异")
        return 1
    print("\n回归通过：所有题型解析结果与基线逐字段一致")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="问卷星 HTML 解析器回归对账")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--emit", action="store_true", help="生成基线")
    group.add_argument("--check", action="store_true", help="与基线对账")
    parser.add_argument("--impl", default=DEFAULT_IMPL, help="解析器模块路径（--emit 时生效）")
    args = parser.parse_args()
    return emit(args.impl) if args.emit else check()


if __name__ == "__main__":
    raise SystemExit(main())
