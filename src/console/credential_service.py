"""Credential management commands coordinated independently from HTTP routing."""

import logging

logger = logging.getLogger(__name__)


class CredentialConflict(ValueError):
    pass


class CredentialConsoleService:
    def __init__(self, tokens, diagnostics):
        self.tokens = tokens
        self.diagnostics = diagnostics

    @staticmethod
    def _current_name(snapshot):
        return next(
            (row["filename"] for row in snapshot["credentials"] if row["is_current"]),
            None,
        )

    def set_mode(self, mode, filename=None):
        before = self.tokens.get_routing_snapshot()
        index = next(
            (
                row["index"]
                for row in before["credentials"]
                if row["filename"] == filename
            ),
            None,
        )
        if mode == "manual" and index is None:
            raise CredentialConflict("凭证列表已变更，请刷新后重试")
        if not self.tokens.set_mode(mode, index):
            raise ValueError("请选择有效的模式或凭证")
        after = self.tokens.get_routing_snapshot()
        try:
            self.diagnostics.switch(
                self._current_name(before),
                self._current_name(after),
                "manual_selection" if mode == "manual" else "automatic_mode",
            )
        except Exception:
            logger.exception(
                "Credential mode changed but routing journal could not be saved"
            )
        return after

    def delete(self, filename):
        index = next(
            (
                row["index"]
                for row in self.tokens.get_credentials_info()
                if row["filename"] == filename
            ),
            None,
        )
        if index is None:
            raise CredentialConflict("凭证不存在，请刷新列表")
        if not self.tokens.delete_credential_by_index(index):
            raise OSError("删除失败")
