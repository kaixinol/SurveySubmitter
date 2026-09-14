from __future__ import annotations

from unittest.mock import patch

import survey_submitter.logging.log_utils as log_utils
from survey_submitter.logging.log_utils import (
    log_deduped_message,
    reset_deduped_log_message,
)


class LogUtilsTests:
    def teardown_method(self, _method) -> None:
        reset_deduped_log_message("test_random_ip_sync_failure")

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
