"""JSON persistence shared by credential managers, independent from routing."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Protocol

from .credential_utils import resolve_project_path

logger = logging.getLogger(__name__)


class CredentialNotFound(LookupError):
    """The requested account no longer exists in the credential collection."""


class CredentialStore(Protocol):
    directory: str

    def records(self) -> List[Dict[str, Any]]: ...
    def read(self, filename: str) -> Dict[str, Any]: ...
    def write(self, filename: str, data: Dict[str, Any]) -> None: ...
    def update(
        self, filename: str, updates: Dict[str, Any], remove_keys=()
    ) -> Dict[str, Any]: ...
    def delete(self, filename: str) -> None: ...


class CredentialReader(Protocol):
    def get_credential(self, filename: str) -> Dict[str, Any]: ...


class CredentialReplacer(CredentialReader, Protocol):
    def replace_credential(self, filename: str, credential: Dict[str, Any]) -> None: ...


class CredentialWriter(Protocol):
    def add_credential_with_data(
        self, credential_data: Dict[str, Any], filename: str = None
    ) -> bool: ...


class JsonCredentialStore:
    def __init__(self, directory: str):
        self.directory = resolve_project_path(directory)

    def _path(self, filename):
        if (
            not filename
            or os.path.basename(filename) != filename
            or not filename.endswith(".json")
        ):
            raise ValueError("Invalid credential filename")
        return Path(self.directory) / filename

    def records(self):
        records = []
        for path in sorted(Path(self.directory).glob("*.json")):
            if path.name == "manager_state.json":
                continue
            try:
                data = self.read(path.name)
                if "bearer_token" in data:
                    records.append({"file_path": str(path), "data": data})
            except (OSError, ValueError):
                logger.warning("Skipping unreadable credential: %s", path.name)
        return records

    def read(self, filename):
        with self._path(filename).open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("Credential JSON must be an object")
        return data

    def write(self, filename, data):
        destination = self._path(filename)
        payload = json.dumps(data, ensure_ascii=False, indent=4)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=self.directory, prefix=".credential-"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def update(self, filename, updates, remove_keys=()):
        data = self.read(filename)
        data.update(updates)
        for key in remove_keys:
            data.pop(key, None)
        self.write(filename, data)
        return data

    def delete(self, filename):
        self._path(filename).unlink()
