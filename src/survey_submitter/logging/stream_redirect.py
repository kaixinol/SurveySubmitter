import logging

from survey_submitter.logging.log_utils import _safe_internal_log, _should_filter_noise


class StreamToLogger:
    def __init__(self, logger: logging.Logger, level: int, stream=None):
        self.logger = logger
        self.level = level
        self.stream = stream
        self._buffer = ""

    def write(self, message: str):
        if message is None:
            return
        text = str(message)
        if _should_filter_noise(text):
            if self.stream:
                try:
                    self.stream.write(message)
                except Exception as exc:
                    _safe_internal_log("StreamToLogger.write failed", exc)
            return
        self._buffer += text.replace("\r", "")
        if "\n" in self._buffer:
            parts = self._buffer.split("\n")
            self._buffer = parts.pop()
            for line in parts:
                if _should_filter_noise(line):
                    continue
                self.logger.log(self.level, line)
        if self.stream:
            try:
                self.stream.write(message)
            except Exception as exc:
                _safe_internal_log("StreamToLogger.write failed", exc)

    def flush(self):
        if self._buffer and not _should_filter_noise(self._buffer):
            self.logger.log(self.level, self._buffer)
        self._buffer = ""
        if self.stream:
            try:
                self.stream.flush()
            except Exception as exc:
                _safe_internal_log("StreamToLogger.flush failed", exc)
