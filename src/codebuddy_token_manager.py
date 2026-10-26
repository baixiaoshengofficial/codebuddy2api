"""
CodeBuddy Token Manager - 管理CodeBuddy认证token
"""
import os
import time
import logging
from typing import Dict, Optional, List, Any
from .usage_stats_manager import usage_stats_manager
from .credential_utils import build_credential_filename
from .credential_store import CredentialNotFound, CredentialStore, JsonCredentialStore

logger = logging.getLogger(__name__)


class CodeBuddyTokenManager:
    """CodeBuddy Token管理器"""
    
    def __init__(self, creds_dir=None, store: CredentialStore = None):
        if store is None and creds_dir is None:
            from config import get_codebuddy_creds_dir
            creds_dir = get_codebuddy_creds_dir()
        
        self.store = store if store is not None else JsonCredentialStore(creds_dir)
        self.creds_dir = self.store.directory
        self.state_file = os.path.join(self.creds_dir, 'manager_state.json')
        self.credentials = []
        self.current_index = 0  # Start from the first credential
        self.usage_count = 0
        self.mode = 'auto'  # 'auto' 积分不足时切换 | 'manual' 锁定指定凭证
        self.manual_selected_index = None  # manual 模式下锁定的凭证索引
        # 凭证 cooldown: {index: cooldown_until_timestamp}，额度耗尽后临时跳过
        self.cooldowns: Dict[int, float] = {}
        self.cooldown_seconds = 300  # 凭证耗尽后 5 分钟内不选用
        self.load_all_tokens()
        self.load_state()  # 加载保存的状态
    
    def load_all_tokens(self):
        """加载所有token文件"""
        current_filename = None
        if 0 <= self.current_index < len(self.credentials):
            current_filename = os.path.basename(
                self.credentials[self.current_index]["file_path"]
            )
        manual_filename = None
        if (
            self.manual_selected_index is not None
            and 0 <= self.manual_selected_index < len(self.credentials)
        ):
            manual_filename = os.path.basename(
                self.credentials[self.manual_selected_index]["file_path"]
            )
        previous_cooldowns = {
            os.path.basename(self.credentials[index]["file_path"]): until
            for index, until in self.cooldowns.items()
            if 0 <= index < len(self.credentials)
        }
        self.current_index = -1
        self.credentials = self.store.records()
        self.cooldowns = {}
        self.manual_selected_index = None
        for index, credential in enumerate(self.credentials):
            filename = os.path.basename(credential["file_path"])
            if filename == manual_filename:
                self.manual_selected_index = index
            if filename == current_filename:
                self.current_index = index
            if filename in previous_cooldowns:
                self.cooldowns[index] = previous_cooldowns[filename]
        if self.mode == "manual" and self.manual_selected_index is None:
            self.mode = "auto"

    def load_state(self):
        """加载管理器状态，兼容旧版字段"""
        try:
            state = self.store.read("manager_state.json")

            # 新版: 显式 mode 字段
            saved_mode = state.get("mode")
            if saved_mode in ("auto", "manual"):
                self.mode = saved_mode
            else:
                # 兼容旧版: 无 mode 字段时根据 manual_selected_index / auto_rotation_enabled 推导
                if state.get("manual_selected_index") is not None:
                    self.mode = "manual"
                else:
                    self.mode = (
                        "auto" if state.get("auto_rotation_enabled", True) else "manual"
                    )

            # 恢复手动选择索引
            saved_manual_index = state.get("manual_selected_index")
            if saved_manual_index is not None and 0 <= saved_manual_index < len(
                self.credentials
            ):
                saved_filename = state.get("manual_selected_filename")
                current_filename = os.path.basename(
                    self.credentials[saved_manual_index]["file_path"]
                )
                if saved_filename == current_filename:
                    self.manual_selected_index = saved_manual_index
                    self.current_index = saved_manual_index
                    logger.info(
                        f"Restored manual selection: {current_filename} (index: {saved_manual_index})"
                    )
                else:
                    logger.warning(
                        "Saved credential filename mismatch, ignoring saved selection"
                    )
                    self.mode = "auto"

            # 恢复当前索引
            if self.manual_selected_index is None:
                saved_current_index = next(
                    (
                        i
                        for i, item in enumerate(self.credentials)
                        if os.path.basename(item["file_path"])
                        == state.get("current_filename")
                    ),
                    state.get("current_index", 0),
                )
                if 0 <= saved_current_index < len(self.credentials):
                    self.current_index = saved_current_index

            saved_cooldowns = state.get("cooldowns", {})
            if isinstance(saved_cooldowns, dict):
                for index, credential in enumerate(self.credentials):
                    filename = os.path.basename(credential["file_path"])
                    try:
                        until = float(saved_cooldowns.get(filename, 0))
                    except (TypeError, ValueError):
                        continue
                    if until > time.time():
                        self.cooldowns[index] = until

            logger.info(
                f"State loaded: mode={self.mode}, current_index={self.current_index}"
            )
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Failed to load manager state: {e}")

    def save_state(self):
        """保存管理器状态"""
        try:
            state = {
                "mode": self.mode,
                "current_index": self.current_index,
                "current_filename": (
                    os.path.basename(self.credentials[self.current_index]["file_path"])
                    if 0 <= self.current_index < len(self.credentials)
                    else None
                ),
                "manual_selected_index": self.manual_selected_index,
                "manual_selected_filename": None,
                "cooldowns": {
                    os.path.basename(self.credentials[index]["file_path"]): until
                    for index, until in self.cooldowns.items()
                    if 0 <= index < len(self.credentials) and until > time.time()
                },
                "saved_at": int(time.time()),
            }

            if (
                self.manual_selected_index is not None
                and 0 <= self.manual_selected_index < len(self.credentials)
            ):
                state["manual_selected_filename"] = os.path.basename(
                    self.credentials[self.manual_selected_index]["file_path"]
                )

            self.store.write("manager_state.json", state)

            logger.debug(f"Manager state saved to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save manager state: {e}")

    def is_token_expired(self, credential_data: Dict) -> bool:
        """检查token是否过期"""
        try:
            if credential_data.get('session_dead_at'):
                return True
            created_at = credential_data.get('created_at')
            expires_in = credential_data.get('expires_in')
            
            if not created_at or not expires_in:
                # 如果没有过期信息，假设未过期（向后兼容）
                return False
            
            current_time = int(time.time())
            expiry_time = created_at + expires_in
            
            # 提前5分钟认为过期，留出刷新时间
            buffer_time = 300  # 5分钟
            is_expired = current_time >= (expiry_time - buffer_time)
            
            if is_expired:
                user_id = credential_data.get('user_id', 'unknown')
                logger.warning(f"Token for user {user_id} is expired or will expire soon")
            
            return is_expired
        except Exception as e:
            logger.error(f"Error checking token expiry: {e}")
            return False

    def _can_use_credential(self, credential_data: Dict) -> bool:
        if credential_data.get('session_dead_at'):
            return False
        return bool(credential_data.get('refresh_token')) or not self.is_token_expired(credential_data)
    
    def _is_on_cooldown(self, index: int) -> bool:
        """检查凭证是否处于 cooldown（额度耗尽临时跳过）"""
        cooldown_until = self.cooldowns.get(index)
        if cooldown_until and time.time() < cooldown_until:
            return True
        if cooldown_until:
            # cooldown 已过期，清理记录
            self.cooldowns.pop(index, None)
        return False

    def all_available_credentials_exhausted(self) -> bool:
        usable_indices = [
            index for index, credential in enumerate(self.credentials)
            if self._can_use_credential(credential['data'])
        ]
        return bool(usable_indices) and all(
            self._is_on_cooldown(index) for index in usable_indices
        )

    def mark_credential_exhausted(self, index: int) -> None:
        """标记某凭证额度耗尽，cooldown 一段时间内不再选用"""
        if not 0 <= index < len(self.credentials):
            return
        self.cooldowns[index] = time.time() + self.cooldown_seconds
        filename = os.path.basename(self.credentials[index]['file_path'])
        logger.warning(f"Credential {filename} (index {index}) marked exhausted, cooldown {self.cooldown_seconds}s")
        if self.mode == 'auto' and self.current_index == index:
            for offset in range(1, len(self.credentials)):
                next_index = (index + offset) % len(self.credentials)
                if (self._can_use_credential(self.credentials[next_index]['data'])
                        and not self._is_on_cooldown(next_index)):
                    self.current_index = next_index
                    self.usage_count = 0
                    break
        self.save_state()

    def get_next_credential(self) -> Optional[Dict]:
        """保持当前凭证，只有失效或积分不足时才选择下一个。"""

        if not self.credentials:
            return None

        # 过滤掉过期的凭证
        valid_credentials = []
        for i, cred in enumerate(self.credentials):
            if self._can_use_credential(cred['data']):
                valid_credentials.append((i, cred))
            else:
                filename = os.path.basename(cred['file_path'])
                logger.warning(f"Skipping expired credential: {filename}")

        if not valid_credentials:
            logger.error("No valid (non-expired) credentials available")
            return None

        # manual 模式：锁定使用指定凭证
        if self.mode == 'manual' and self.manual_selected_index is not None:
            mi = self.manual_selected_index
            if 0 <= mi < len(self.credentials):
                manual_cred = self.credentials[mi]
                if self._can_use_credential(manual_cred['data']):
                    credential_filename = os.path.basename(manual_cred['file_path'])
                    usage_stats_manager.record_credential_usage(credential_filename)
                    logger.info(f"Using manually selected credential: {credential_filename}")
                    selected = dict(manual_cred['data'])
                    selected['_credential_filename'] = credential_filename
                    selected['_credential_index'] = mi
                    return selected
                else:
                    logger.warning("Manually selected credential is expired, falling back to auto mode")
                    self.mode = 'auto'
                    self.manual_selected_index = None
                    self.save_state()

        # auto 模式：跳过已过期或积分不足的凭证
        # 过滤掉处于 cooldown 的凭证
        available = [(i, c) for i, c in valid_credentials if not self._is_on_cooldown(i)]
        if not available:
            logger.warning("All usable credentials are on cooldown")
            return None

        available_indices = [i for i, _ in available]
        # 当前索引不可用时，从当前位置向后寻找下一个
        if self.current_index not in available_indices:
            self.current_index = next(
                (i for i in available_indices if i > self.current_index),
                available_indices[0],
            )
            self.usage_count = 0
            self.save_state()
            logger.info(f"Selected next available credential index: {self.current_index}")

        credential = self.credentials[self.current_index]
        self.usage_count += 1

        credential_filename = os.path.basename(credential['file_path'])
        usage_stats_manager.record_credential_usage(credential_filename)

        logger.info(f"Using credential: {credential_filename} (requests: {self.usage_count})")
        selected = dict(credential['data'])
        selected['_credential_filename'] = credential_filename
        selected['_credential_index'] = self.current_index
        return selected

    def get_credential_by_index(self, index: int) -> Optional[Dict[str, Any]]:
        """Return one usable credential without changing the active mode or index."""
        if isinstance(index, bool) or not isinstance(index, int):
            return None
        if not 0 <= index < len(self.credentials):
            return None

        credential = self.credentials[index]
        if not self._can_use_credential(credential['data']):
            logger.warning(
                "Requested credential is expired: %s",
                os.path.basename(credential['file_path']),
            )
            return None

        credential_filename = os.path.basename(credential['file_path'])
        usage_stats_manager.record_credential_usage(credential_filename)
        selected = dict(credential['data'])
        selected['_credential_filename'] = credential_filename
        selected['_credential_index'] = index
        return selected
    
    def get_all_credentials(self) -> List[Dict]:
        """获取所有凭证"""
        return [cred['data'] for cred in self.credentials]

    def get_credential(self, filename: str) -> Dict[str, Any]:
        for credential in self.credentials:
            if os.path.basename(credential["file_path"]) == filename:
                return dict(credential["data"])
        raise CredentialNotFound(filename)

    def get_credential_records(self) -> List[Dict[str, Any]]:
        """Return credential snapshots with stable filenames for background jobs."""
        return [
            {
                "filename": os.path.basename(cred["file_path"]),
                "data": dict(cred["data"]),
            }
            for cred in self.credentials
        ]
    
    def get_credentials_info(self) -> List[Dict]:
        """获取所有凭证的详细信息，包括过期状态"""
        credentials_info = []
        for i, cred in enumerate(self.credentials):
            data = cred['data']
            filename = os.path.basename(cred['file_path'])
            
            # 计算过期信息
            is_expired = self.is_token_expired(data)
            expires_at = None
            time_remaining = None
            
            if data.get('created_at') and data.get('expires_in'):
                expires_at = data['created_at'] + data['expires_in']
                time_remaining = expires_at - int(time.time())
            
            # 提取用户信息
            user_info = data.get('user_info', {})
            
            info = {
                'index': i,
                'filename': filename,
                'user_id': data.get('user_id', 'unknown'),
                'email': user_info.get('email') or data.get('user_id'),
                'name': user_info.get('name'),
                'created_at': data.get('created_at'),
                'expires_in': data.get('expires_in'),
                'expires_at': expires_at,
                'time_remaining': time_remaining,
                'is_expired': is_expired,
                'token_type': data.get('token_type', 'Bearer'),
                'scope': data.get('scope'),
                'domain': data.get('domain'),
                'has_refresh_token': bool(data.get('refresh_token')),
                'session_state': data.get('session_state'),
                'file_path': cred['file_path']
            }
            
            credentials_info.append(info)
        
        return credentials_info
    
    def add_credential_with_data(self, credential_data: Dict[str, Any], filename: str = None) -> bool:
        """添加新的凭证（完整数据版本）"""
        credential_data = dict(credential_data)
        user_id = credential_data.get('user_id')
        domain = credential_data.get('domain')
        if user_id and user_id != 'unknown':
            for existing in self.credentials:
                stored = existing['data']
                if stored.get('user_id') == user_id and stored.get('domain') == domain:
                    try:
                        self.replace_credential(
                            os.path.basename(existing['file_path']),
                            credential_data,
                        )
                        return True
                    except Exception as exc:
                        logger.error('Failed to replace CodeBuddy credential: %s', exc)
                        return False
        if not filename:
            user_id = credential_data.get('user_id', 'unknown')
            timestamp = credential_data.get('created_at', int(time.time()))
            filename = build_credential_filename("codebuddy", user_id, timestamp)
        
        if not filename.endswith('.json'):
            filename += '.json'
        
        # 确保必要字段存在
        if 'created_at' not in credential_data:
            credential_data['created_at'] = int(time.time())
        
        try:
            self.store.write(filename, credential_data)
            self.load_all_tokens()  # 重新加载
            return True
        except Exception as e:
            logger.error(f"Failed to save credential: {e}")
            return False

    def replace_credential(self, filename: str, credential_data: Dict[str, Any]) -> None:
        """Replace a login in place, preserving selection and removing stale state."""
        from .credential_utils import obsolete_authorization_fields

        self.update_credential(
            filename, credential_data,
            remove_keys=obsolete_authorization_fields(credential_data),
        )
        for index, credential in enumerate(self.credentials):
            if os.path.basename(credential['file_path']) == filename:
                self.cooldowns.pop(index, None)
                break
        self.save_state()

    def update_credential(
        self,
        filename: str,
        updates: Dict[str, Any],
        remove_keys=(),
    ) -> Dict[str, Any]:
        """Persist refreshed credentials and update the in-memory copy."""
        data = self.store.update(filename, updates, remove_keys)
        for credential in self.credentials:
            if os.path.basename(credential['file_path']) == filename:
                credential['data'] = data
                break
        return dict(data)

    def mark_session_dead(self, filename: str) -> None:
        self.update_credential(filename, {
            'session_dead_at': int(time.time()),
            'session_dead_reason': '12153',
        })

    def delete_credential_by_index(self, index: int) -> bool:
        """删除指定索引的凭证文件，并重新加载列表"""
        try:
            if not (0 <= index < len(self.credentials)):
                logger.error(f"Invalid credential index for deletion: {index}")
                return False

            file_path = self.credentials[index]['file_path']
            filename = os.path.basename(file_path)

            try:
                self.store.delete(filename)
            except FileNotFoundError:
                pass

            # 重新加载凭证列表，重置索引等状态
            self.load_all_tokens()
            self.save_state()
            return True
        except Exception as e:
            logger.error(f"Failed to delete credential at index {index}: {e}")
            return False

    def set_mode(self, mode: str, index: Optional[int] = None) -> bool:
        """设置凭证模式: 'auto' 积分不足时切换 | 'manual' 锁定指定凭证"""
        if mode == 'manual':
            if index is None or not (0 <= index < len(self.credentials)):
                logger.error(f"Invalid credential index for manual mode: {index}")
                return False
            self.mode = 'manual'
            self.manual_selected_index = index
            self.current_index = index
            # 清除该凭证的 cooldown，用户手动选了就用
            self.cooldowns.pop(index, None)
            credential_filename = os.path.basename(self.credentials[index]['file_path'])
            logger.info(f"Switched to manual mode, locked credential: {credential_filename} (index: {index})")
        elif mode == 'auto':
            self.mode = 'auto'
            self.manual_selected_index = None
            logger.info("Switched to automatic credit failover mode")
        else:
            logger.error(f"Invalid mode: {mode}")
            return False
        self.save_state()
        return True

    def get_routing_snapshot(self) -> Dict:
        """Project routing state without selecting or consuming a credential."""
        now = time.time()
        rows = []
        eligible = []
        for info, credential in zip(self.get_credentials_info(), self.credentials):
            index = info["index"]
            data = credential["data"]
            until = self.cooldowns.get(index, 0)
            if data.get("session_dead_at"):
                state = "reauthorize"
            elif not self._can_use_credential(data):
                state = "expired"
            elif until > now and not (
                self.mode == "manual" and index == self.manual_selected_index
            ):
                state = "cooldown"
            else:
                state = "refreshable" if info["is_expired"] else "available"
                eligible.append(index)
            rows.append(
                {
                    key: value
                    for key, value in info.items()
                    if key not in ("file_path", "scope", "token_type")
                }
            )
            rows[-1].update(
                state=state, cooldown_until=int(until * 1000) if until > now else None
            )
        selected = (
            self.manual_selected_index if self.mode == "manual" else self.current_index
        )
        active = selected if selected in eligible else None
        # Reflect the exact next selection rule without incrementing usage counters.
        if self.mode == "auto" and active is None and eligible:
            active = next((i for i in eligible if i > self.current_index), eligible[0])
        for row in rows:
            row["is_current"] = row["index"] == active
        next_index = None
        if self.mode == "auto" and active is not None:
            ordered = [i for i in eligible if i > active] + [
                i for i in eligible if i < active
            ]
            next_index = ordered[0] if ordered else None
        return {
            "mode": self.mode,
            "credentials": rows,
            "current_index": active,
            "next_index": next_index,
            "available": len(eligible),
            "cooldown_seconds": self.cooldown_seconds,
            "server_time": int(now * 1000),
        }

    def get_current_credential_info(self) -> Dict:
        """获取当前使用的凭证信息"""
        if not self.credentials:
            return {"status": "no_credentials"}

        # manual 模式
        if self.mode == 'manual' and self.manual_selected_index is not None and 0 <= self.manual_selected_index < len(self.credentials):
            credential = self.credentials[self.manual_selected_index]
            return {
                "status": "manual",
                "mode": "manual",
                "index": self.manual_selected_index,
                "filename": os.path.basename(credential['file_path']),
                "user_id": credential['data'].get('user_id', 'unknown')
            }

        # auto 模式
        if not (0 <= self.current_index < len(self.credentials)):
            self.current_index = 0
        credential = self.credentials[self.current_index]
        return {
            "status": "auto",
            "mode": "auto",
            "index": self.current_index,
            "filename": os.path.basename(credential['file_path']),
            "user_id": credential['data'].get('user_id', 'unknown'),
            "usage_count": self.usage_count,
        }


# 全局token管理器实例
codebuddy_token_manager = CodeBuddyTokenManager()
