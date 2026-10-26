"""WorkBuddy credential storage for daily check-in only."""

import logging
import os
import time
from typing import Any, Dict, List, Optional

from .credential_utils import build_credential_filename
from .credential_store import CredentialNotFound, CredentialStore, JsonCredentialStore

logger = logging.getLogger(__name__)


class WorkBuddyTokenManager:
    """Manage WorkBuddy login tokens used exclusively for check-in."""

    def __init__(self, creds_dir: Optional[str] = None, store: CredentialStore = None):
        if store is None and creds_dir is None:
            from config import get_workbuddy_creds_dir
            creds_dir = get_workbuddy_creds_dir()
        self.store = store if store is not None else JsonCredentialStore(creds_dir)
        self.creds_dir = self.store.directory
        self.credentials: List[Dict[str, Any]] = []
        self.load_all_tokens()

    def load_all_tokens(self) -> None:
        self.credentials = self.store.records()
        for credential in self.credentials:
            credential["data"].setdefault("product", "WorkBuddy")

    @staticmethod
    def is_token_expired(credential_data: Dict[str, Any]) -> bool:
        try:
            created_at = credential_data.get("created_at")
            expires_in = credential_data.get("expires_in")
            if not created_at or not expires_in:
                return False
            return int(time.time()) >= (int(created_at) + int(expires_in) - 300)
        except Exception as exc:
            logger.error("Error checking WorkBuddy token expiry: %s", exc)
            return False

    @staticmethod
    def _get_phone(data: Dict[str, Any], user_info: Dict[str, Any]) -> Optional[str]:
        """Return the phone-style account identifier saved by WorkBuddy."""
        for source in (data, user_info):
            for key in ("phone", "mobile", "mobile_phone", "phone_number", "telephone"):
                value = source.get(key)
                if value:
                    return str(value)

        # WorkBuddy JWTs commonly use preferred_username for the login phone.
        preferred_username = user_info.get("preferred_username")
        if preferred_username:
            return str(preferred_username)
        return None

    def get_credential(self, filename: str) -> Dict[str, Any]:
        for credential in self.credentials:
            if os.path.basename(credential["file_path"]) == filename:
                return dict(credential["data"])
        raise CredentialNotFound(filename)

    def get_credential_records(self) -> List[Dict[str, Any]]:
        return [
            {
                "filename": os.path.basename(cred["file_path"]),
                "data": dict(cred["data"]),
            }
            for cred in self.credentials
        ]

    def get_credentials_info(self) -> List[Dict[str, Any]]:
        info = []
        for index, cred in enumerate(self.credentials):
            data = cred["data"]
            user_info = data.get("user_info") if isinstance(data.get("user_info"), dict) else {}
            phone = self._get_phone(data, user_info)
            expires_at = None
            time_remaining = None
            if data.get("created_at") and data.get("expires_in"):
                expires_at = int(data["created_at"]) + int(data["expires_in"])
                time_remaining = expires_at - int(time.time())
            info.append({
                "index": index,
                "filename": os.path.basename(cred["file_path"]),
                "user_id": data.get("user_id") or user_info.get("uid") or "unknown",
                "phone": phone,
                "email": user_info.get("email") or phone or data.get("user_id"),
                "name": user_info.get("name") or user_info.get("nickname"),
                "domain": data.get("domain"),
                "product": data.get("product", "WorkBuddy"),
                "created_at": data.get("created_at"),
                "expires_in": data.get("expires_in"),
                "expires_at": expires_at,
                "time_remaining": time_remaining,
                "is_expired": self.is_token_expired(data),
                "has_refresh_token": bool(data.get("refresh_token")),
            })
        return info

    def add_credential_with_data(self, credential_data: Dict[str, Any], filename: Optional[str] = None) -> bool:
        credential_data = dict(credential_data)
        credential_data.setdefault("product", "WorkBuddy")
        credential_data.setdefault("created_at", int(time.time()))

        new_user_info = credential_data.get("user_info")
        new_user_info = new_user_info if isinstance(new_user_info, dict) else {}
        new_phone = self._get_phone(credential_data, new_user_info)
        for existing in self.credentials:
            stored = existing["data"]
            if stored.get("domain") != credential_data.get("domain"):
                continue
            stored_user_info = stored.get("user_info")
            stored_user_info = stored_user_info if isinstance(stored_user_info, dict) else {}
            stored_phone = self._get_phone(stored, stored_user_info)
            same_user = (
                credential_data.get("user_id") not in (None, "", "unknown")
                and stored.get("user_id") == credential_data.get("user_id")
            )
            if same_user or (new_phone and stored_phone == new_phone):
                try:
                    self.replace_credential(os.path.basename(existing["file_path"]), credential_data)
                    return True
                except Exception as exc:
                    logger.error("Failed to replace WorkBuddy credential: %s", exc)
                    return False

        if not filename:
            user_id = credential_data.get("user_id", "unknown")
            timestamp = credential_data.get("created_at", int(time.time()))
            filename = build_credential_filename("workbuddy", user_id, timestamp)
        if not filename.endswith(".json"):
            filename += ".json"

        try:
            self.store.write(filename, credential_data)
            self.load_all_tokens()
            return True
        except Exception as exc:
            logger.error("Failed to save WorkBuddy credential: %s", exc)
            return False

    def replace_credential(self, filename: str, credential_data: Dict[str, Any]) -> None:
        from .credential_utils import obsolete_authorization_fields

        self.update_credential(
            filename, credential_data,
            remove_keys=obsolete_authorization_fields(credential_data),
        )

    def update_credential(self, filename: str, updates: Dict[str, Any], remove_keys=()) -> Dict[str, Any]:
        """Atomically persist refreshed tokens without changing the account file."""
        data = self.store.update(filename, updates, remove_keys)
        self.load_all_tokens()
        return data

    def delete_credential(self, filename: str) -> None:
        """Delete exactly the selected file, independent of list ordering."""
        if not filename or os.path.basename(filename) != filename:
            raise FileNotFoundError("WorkBuddy credential not found")
        self.load_all_tokens()
        selected = next(
            (item for item in self.credentials
             if os.path.basename(item["file_path"]) == filename),
            None,
        )
        if selected is None:
            raise FileNotFoundError("WorkBuddy credential not found")
        self.store.delete(filename)
        self.load_all_tokens()

    def delete_credential_by_index(self, index: int) -> bool:
        """Compatibility for in-process callers; HTTP commands require filenames."""
        try:
            if not (0 <= index < len(self.credentials)):
                logger.error("Invalid WorkBuddy credential index for deletion: %s", index)
                return False
            file_path = self.credentials[index]["file_path"]
            filename = os.path.basename(file_path)
            self.delete_credential(filename)
            return True
        except Exception as exc:
            logger.error("Failed to delete WorkBuddy credential at index %s: %s", index, exc)
            return False


workbuddy_token_manager = WorkBuddyTokenManager()
