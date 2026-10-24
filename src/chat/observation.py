"""Optional request observations, independent of persistence and routing rules."""

import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class AttemptObserver:
    """Best effort observability; a storage failure must never replay a model request."""

    def __init__(self, repository=None, record_id=None):
        self.repository = repository
        self.record_id = record_id

    def _write(self, method, *args):
        if self.repository is None or self.record_id is None:
            return None
        try:
            return getattr(self.repository, method)(*args)
        except Exception:
            logger.exception("Unable to persist upstream diagnostics")

    @contextmanager
    def attempt(self, number, credential):
        if self.record_id and self.record_id % 100 == 0 and number == 1:
            self._write("prune")
        attempt_id = self._write("start", self.record_id, number, credential)
        result = {"status_code": 200, "error": None, "complete": False}
        try:
            yield result
        except BaseException as exc:
            if not result["complete"]:
                result["status_code"] = getattr(
                    exc,
                    "status_code",
                    (
                        499
                        if type(exc).__name__ in ("CancelledError", "GeneratorExit")
                        else 502
                    ),
                )
                result["error"] = getattr(exc, "detail", str(exc)) or type(exc).__name__
            raise
        finally:
            if attempt_id is not None:
                self._write(
                    "finish", attempt_id, result["status_code"], result["error"]
                )

    def switch(self, previous, current):
        self._write("switch", previous, current, "insufficient_credit", self.record_id)
