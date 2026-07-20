from __future__ import annotations
import tempfile
from pathlib import Path
from unittest.mock import patch
import survey_submitter.logging.log_utils as log_utils
import survey_submitter.logging.session_log as session_log
from survey_submitter.logging.log_utils import (
    log_deduped_message,
    reset_deduped_log_message,
)
from survey_submitter.logging.session_log import (
    export_full_log_to_file,
    finalize_session_log_persistence,
    get_auto_save_log_settings,
    prune_session_log_files,
)


class LogUtilsTests:
    def teardown_method(self, _method) -> None:
        reset_deduped_log_message("test_random_ip_sync_failure")
        session_log._remove_session_log_sink()
        session_log._SESSION_LOG_PATH = ""
        session_log._DELETE_SESSION_LOG_ON_SHUTDOWN = False

    def test_log_deduped_message_only_logs_same_message_once(self) -> None:
        with patch("survey_submitter.logging.log_utils.logger") as mock_logger:
            first = log_deduped_message(
                "test_random_ip_sync_failure", "同步随机IP额度失败：网络超时", level="INFO"
            )
            second = log_deduped_message(
                "test_random_ip_sync_failure", "同步随机IP额度失败：网络超时", level="INFO"
            )
        assert first
        assert not second
        mock_logger.log.assert_called_once_with("INFO", "同步随机IP额度失败：网络超时")

    def test_reset_deduped_log_message_allows_same_message_to_log_again(self) -> None:
        with patch("survey_submitter.logging.log_utils.logger") as mock_logger:
            first = log_deduped_message(
                "test_random_ip_sync_failure", "同步随机IP额度失败：网络超时", level="INFO"
            )
            reset_deduped_log_message("test_random_ip_sync_failure")
            second = log_deduped_message(
                "test_random_ip_sync_failure", "同步随机IP额度失败：网络超时", level="INFO"
            )
        assert first
        assert second
        assert mock_logger.log.call_count == 2

    def test_should_filter_runtime_probe_noise_messages(self) -> None:
        assert log_utils._should_filter_noise(
            "2026-05-07 00:07:55 [INFO] WJX 页面题目快照刷新：reason=question_2_expected_visible_miss count=8 elapsed=0.004s"
        )
        assert log_utils._should_filter_noise(
            "2026-05-07 00:07:57 [INFO] 随机代理首载：探测页面可用性 timeout=2500ms interval=0.25s"
        )
        assert log_utils._should_filter_noise(
            "2026-05-07 00:07:58 [INFO] WJX 题目处理耗时：question=5 type=4 elapsed=2.259s"
        )
        assert not log_utils._should_filter_noise("2026-05-07 00:07:59 [INFO] 提交成功")

    def test_export_full_log_to_file_prefers_session_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = str(Path(temp_dir) / "session.log")
            target_path = str(Path(temp_dir) / "exported.log")
            Path(source_path).write_text("第一行\n第二行\n", encoding="utf-8")
            session_log._SESSION_LOG_PATH = source_path
            try:
                exported_path = export_full_log_to_file(temp_dir, target_path)
                assert exported_path == target_path
                assert Path(target_path).read_text(encoding="utf-8") == "第一行\n第二行\n"
            finally:
                session_log._SESSION_LOG_PATH = ""

    def test_get_auto_save_log_settings_returns_defaults_when_values_missing(self) -> None:

        class _StubSettings:
            @staticmethod
            def value(_key):
                return None

        with patch(
            "survey_submitter.logging.session_log.app_settings", return_value=_StubSettings()
        ):
            enabled, keep_count = get_auto_save_log_settings()
        assert enabled
        assert keep_count == 10

    def test_prune_session_log_files_keeps_recent_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_dir = Path(temp_dir) / "logs"
            logs_dir.mkdir()
            retained_names = []
            for index in range(3):
                name = f"session_20250101_00000{index}.log"
                path = logs_dir / name
                path.write_text(name, encoding="utf-8")
                import os
                os.utime(str(path), (100 + index, 100 + index))
                retained_names.append(name)
            removed_count = prune_session_log_files(str(temp_dir), 2)
            assert removed_count == 1
            assert not (logs_dir / retained_names[0]).exists()
            assert (logs_dir / retained_names[1]).exists()
            assert (logs_dir / retained_names[2]).exists()

    def test_finalize_session_log_persistence_exports_last_session_and_prunes_history_when_enabled(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_dir = Path(temp_dir) / "logs"
            logs_dir.mkdir()
            source_path = str(logs_dir / "session_20250101_000003.log")
            Path(source_path).write_text("本次日志\n", encoding="utf-8")
            session_log._SESSION_LOG_PATH = source_path
            import os
            stale_paths = []
            for index in range(2):
                stale_path = str(logs_dir / f"session_20250101_00000{index}.log")
                Path(stale_path).write_text("旧日志\n", encoding="utf-8")
                os.utime(stale_path, (100 + index, 100 + index))
                stale_paths.append(stale_path)
            os.utime(source_path, (200, 200))
            try:
                with patch(
                    "survey_submitter.logging.session_log.get_auto_save_log_settings",
                    return_value=(True, 2),
                ):
                    finalize_session_log_persistence(str(temp_dir))
                last_session_path = str(logs_dir / "last_session.log")
                assert Path(last_session_path).exists()
                assert Path(last_session_path).read_text(encoding="utf-8") == "本次日志\n"
                assert Path(source_path).exists()
                assert Path(stale_paths[1]).exists()
                assert not Path(stale_paths[0]).exists()
                assert not session_log._DELETE_SESSION_LOG_ON_SHUTDOWN
            finally:
                session_log._SESSION_LOG_PATH = ""

    def test_finalize_session_log_persistence_marks_session_for_deletion_when_disabled(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_dir = Path(temp_dir) / "logs"
            logs_dir.mkdir()
            last_session_path = logs_dir / "last_session.log"
            last_session_path.write_text("旧的上次日志\n", encoding="utf-8")
            with patch(
                "survey_submitter.logging.session_log.get_auto_save_log_settings",
                return_value=(False, 10),
            ):
                finalize_session_log_persistence(str(temp_dir))
            assert not last_session_path.exists()
            assert session_log._DELETE_SESSION_LOG_ON_SHUTDOWN
