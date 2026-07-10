"""SurveyController — CLI entry point.

Use `python cli.py run config.yaml` to run survey submissions.
"""
from software.app.main import bootstrap

bootstrap()

from cli import main  # noqa: E402

if __name__ == "__main__":
    main()
