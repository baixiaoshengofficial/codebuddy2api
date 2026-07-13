"""WorkBuddy credential storage for daily check-in only."""

import glob
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class WorkBuddyTokenManager:
    """Manage WorkBuddy login tokens used exclusively for check-in."""

    def __init__(self, creds_dir: Optional[str] = None):
        if creds_dir is None:
            from config import get_workbuddy_creds_dir
            creds_dir = get_workbuddy_creds_dir()
        # Prefer project-root relative path so Docker mount /app/.workbuddy_creds works.
        if os.path.isabs(creds_dir):
            self.creds_dir = creds_dir
        else:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            self.creds_dir = os.path.join(project_root, creds_dir)
        self.credentials: List[Dict[str, Any]] = []
        self.load_all_tokens()

    def load_all_tokens(self) -> None:
        self.credentials = []
        logger.info("Loading WorkBuddy credentials from: %s", self.creds_dir)
        if not os.path.exists(self.creds_dir):
            os.makedirs(self.creds_dir)
            logger.warning("WorkBuddy credentials directory created at %s", self.creds_dir)
            return

        for file_path in sorted(glob.glob(os.path.join(self.creds_dir, "*.json"))):
            basename = os.path.basename(file_path)
            if basename == "manager_state.json":
                continue
            try:
                with open(file_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if "bearer_token" not in data:
                    logger.warning("Skipping invalid WorkBuddy credential (missing bearer_token): %s", basename)
                    continue
                data.setdefault("product", "WorkBuddy")
                self.credentials.append({"file_path": file_path, "data": data})
                logger.info("Loaded WorkBuddy credential: %s", basename)
            except Exception as exc:
                logger.error("Failed to load WorkBuddy credential %s: %s", basename, exc)
        logger.info("Loaded %s WorkBuddy credentials", len(self.credentials))

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
            expires_at = None
            time_remaining = None
            if data.get("created_at") and data.get("expires_in"):
                expires_at = int(data["created_at"]) + int(data["expires_in"])
                time_remaining = expires_at - int(time.time())
            info.append({
                "index": index,
                "filename": os.path.basename(cred["file_path"]),
                "user_id": data.get("user_id") or user_info.get("uid") or "unknown",
                "email": user_info.get("email") or data.get("user_id"),
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

        if not filename:
            user_id = credential_data.get("user_id", "unknown")
            timestamp = credential_data.get("created_at", int(time.time()))
            safe_user_id = "".join(c for c in str(user_id) if c.isalnum() or c in "._-")[:20]
            filename = f"workbuddy_{safe_user_id}_{timestamp}.json"
        if not filename.endswith(".json"):
            filename += ".json"

        file_path = os.path.join(self.creds_dir, filename)
        try:
            os.makedirs(self.creds_dir, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as handle:
                json.dump(credential_data, handle, indent=4, ensure_ascii=False)
            logger.info("Added WorkBuddy credential: %s", filename)
            self.load_all_tokens()
            return True
        except Exception as exc:
            logger.error("Failed to save WorkBuddy credential: %s", exc)
            return False

    def delete_credential_by_index(self, index: int) -> bool:
        try:
            if not (0 <= index < len(self.credentials)):
                logger.error("Invalid WorkBuddy credential index for deletion: %s", index)
                return False
            file_path = self.credentials[index]["file_path"]
            filename = os.path.basename(file_path)
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info("Deleted WorkBuddy credential: %s", filename)
            self.load_all_tokens()
            return True
        except Exception as exc:
            logger.error("Failed to delete WorkBuddy credential at index %s: %s", index, exc)
            return False


workbuddy_token_manager = WorkBuddyTokenManager()
