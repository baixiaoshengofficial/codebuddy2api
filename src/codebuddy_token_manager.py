"""
CodeBuddy Token Manager - 管理CodeBuddy认证token
"""
import os
import glob
import json
import time
import logging
import tempfile
from typing import Dict, Optional, List, Any
from .usage_stats_manager import usage_stats_manager
from .credential_utils import build_credential_filename, resolve_project_path

logger = logging.getLogger(__name__)


class CodeBuddyTokenManager:
    """CodeBuddy Token管理器"""
    
    def __init__(self, creds_dir=None):
        if creds_dir is None:
            from config import get_codebuddy_creds_dir
            creds_dir = get_codebuddy_creds_dir()
        
        self.creds_dir = resolve_project_path(creds_dir)
        self.state_file = os.path.join(self.creds_dir, 'manager_state.json')
        self.credentials = []
        self.current_index = 0  # Start from the first credential
        self.usage_count = 0    # Counter for the current credential usage
        self.mode = 'auto'  # 'auto' 自动轮换 | 'manual' 锁定指定凭证
        self.manual_selected_index = None  # manual 模式下锁定的凭证索引
        # 凭证 cooldown: {index: cooldown_until_timestamp}，额度耗尽后临时跳过
        self.cooldowns: Dict[int, float] = {}
        self.cooldown_seconds = 300  # 凭证耗尽后 5 分钟内不选用
        self.load_all_tokens()
        self.load_state()  # 加载保存的状态
    
    def load_all_tokens(self):
        """加载所有token文件"""
        self.credentials = []
        self.current_index = -1
        
        logger.info(f"Loading CodeBuddy credentials from: {self.creds_dir}")
        
        if not os.path.exists(self.creds_dir):
            os.makedirs(self.creds_dir)
            logger.warning(f"Credentials directory created at {self.creds_dir}. No credentials found.")
            return
        
        token_files = glob.glob(os.path.join(self.creds_dir, '*.json'))
        for file_path in token_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if 'bearer_token' in data:
                        self.credentials.append({
                            'file_path': file_path,
                            'data': data
                        })
                        logger.info(f"Successfully loaded credential: {os.path.basename(file_path)}")
                    else:
                        logger.warning(f"Skipping invalid credential file (missing bearer_token): {os.path.basename(file_path)}")
            except Exception as e:
                logger.error(f"Failed to load credential file {os.path.basename(file_path)}: {e}")
        
        logger.info(f"Loaded a total of {len(self.credentials)} CodeBuddy credentials.")
    
    def load_state(self):
        """加载管理器状态，兼容旧版字段"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)

                # 新版: 显式 mode 字段
                saved_mode = state.get('mode')
                if saved_mode in ('auto', 'manual'):
                    self.mode = saved_mode
                else:
                    # 兼容旧版: 无 mode 字段时根据 manual_selected_index / auto_rotation_enabled 推导
                    if state.get('manual_selected_index') is not None:
                        self.mode = 'manual'
                    else:
                        self.mode = 'auto' if state.get('auto_rotation_enabled', True) else 'manual'

                # 恢复手动选择索引
                saved_manual_index = state.get('manual_selected_index')
                if saved_manual_index is not None and 0 <= saved_manual_index < len(self.credentials):
                    saved_filename = state.get('manual_selected_filename')
                    current_filename = os.path.basename(self.credentials[saved_manual_index]['file_path'])
                    if saved_filename == current_filename:
                        self.manual_selected_index = saved_manual_index
                        self.current_index = saved_manual_index
                        logger.info(f"Restored manual selection: {current_filename} (index: {saved_manual_index})")
                    else:
                        logger.warning("Saved credential filename mismatch, ignoring saved selection")
                        self.mode = 'auto'

                # 恢复当前索引
                if self.manual_selected_index is None:
                    saved_current_index = state.get('current_index', 0)
                    if 0 <= saved_current_index < len(self.credentials):
                        self.current_index = saved_current_index

                logger.info(f"State loaded: mode={self.mode}, current_index={self.current_index}")
        except Exception as e:
            logger.warning(f"Failed to load manager state: {e}")
    
    def save_state(self):
        """保存管理器状态"""
        try:
            if not os.path.exists(self.creds_dir):
                os.makedirs(self.creds_dir)

            state = {
                'mode': self.mode,
                'current_index': self.current_index,
                'manual_selected_index': self.manual_selected_index,
                'manual_selected_filename': None,
                'saved_at': int(time.time())
            }

            if self.manual_selected_index is not None and 0 <= self.manual_selected_index < len(self.credentials):
                state['manual_selected_filename'] = os.path.basename(
                    self.credentials[self.manual_selected_index]['file_path']
                )

            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

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

    def mark_credential_exhausted(self, index: int) -> None:
        """标记某凭证额度耗尽，cooldown 一段时间内不再选用"""
        self.cooldowns[index] = time.time() + self.cooldown_seconds
        filename = "unknown"
        if 0 <= index < len(self.credentials):
            filename = os.path.basename(self.credentials[index]['file_path'])
        logger.warning(f"Credential {filename} (index {index}) marked exhausted, cooldown {self.cooldown_seconds}s")

    def get_next_credential(self) -> Optional[Dict]:
        """获取下一个可用的凭证，根据模式与轮换策略，并检查过期/耗尽状态"""
        from config import get_rotation_count

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

        # auto 模式：在未过期且未 cooldown 的凭证间轮换
        # 过滤掉处于 cooldown 的凭证
        available = [(i, c) for i, c in valid_credentials if not self._is_on_cooldown(i)]
        if not available:
            # 所有凭证都在 cooldown，清除 cooldown 重试一轮
            logger.warning("All valid credentials on cooldown, clearing cooldowns and retrying")
            self.cooldowns.clear()
            available = valid_credentials

        available_indices = [i for i, _ in available]
        # 当前索引若不在可用列表，重置到第一个
        if self.current_index not in available_indices:
            self.current_index = available_indices[0]
            self.usage_count = 0
            self.save_state()
            logger.info(f"Reset to first available credential index: {self.current_index}")

        rotation_count = get_rotation_count()
        try:
            current_pos = available_indices.index(self.current_index)
        except ValueError:
            current_pos = 0
            self.current_index = available_indices[0]
            self.usage_count = 0

        # 轮换：rotation_count > 0 时每 N 次请求切换
        if rotation_count > 0 and self.usage_count >= rotation_count:
            next_pos = (current_pos + 1) % len(available)
            self.current_index = available_indices[next_pos]
            self.usage_count = 0
            self.save_state()
            logger.info("Credential rotation triggered.")

        credential = self.credentials[self.current_index]
        self.usage_count += 1

        credential_filename = os.path.basename(credential['file_path'])
        usage_stats_manager.record_credential_usage(credential_filename)

        logger.info(
            f"Using credential: {credential_filename} "
            f"(Usage: {self.usage_count}/{rotation_count})"
        )
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
    
    def add_credential(self, bearer_token: str, user_id: str = None, filename: str = None) -> bool:
        """添加新的凭证（简化版本，向后兼容）"""
        if not filename:
            filename = f"codebuddy_token_{len(self.credentials) + 1}.json"
        
        if not filename.endswith('.json'):
            filename += '.json'
        
        credential_data = {
            "bearer_token": bearer_token,
            "user_id": user_id,
            "created_at": int(time.time())
        }
        
        return self.add_credential_with_data(credential_data, filename)
    
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
                        remove_keys = ['session_dead_at', 'session_dead_reason']
                        if not credential_data.get('expires_in'):
                            remove_keys.append('expires_in')
                        if not credential_data.get('refresh_token'):
                            remove_keys.append('refresh_token')
                        self.update_credential(
                            os.path.basename(existing['file_path']),
                            credential_data,
                            remove_keys=remove_keys,
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
        
        file_path = os.path.join(self.creds_dir, filename)
        
        # 确保必要字段存在
        if 'created_at' not in credential_data:
            credential_data['created_at'] = int(time.time())
        
        try:
            # 确保目录存在
            if not os.path.exists(self.creds_dir):
                os.makedirs(self.creds_dir)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(credential_data, f, indent=4, ensure_ascii=False)
            
            logger.info(f"Added new credential: {filename}")
            self.load_all_tokens()  # 重新加载
            return True
        except Exception as e:
            logger.error(f"Failed to save credential: {e}")
            return False

    def update_credential(
        self,
        filename: str,
        updates: Dict[str, Any],
        remove_keys=(),
    ) -> Dict[str, Any]:
        """Persist refreshed credentials and update the in-memory copy."""
        if os.path.basename(filename) != filename:
            raise ValueError('Invalid CodeBuddy credential filename')
        path = os.path.join(self.creds_dir, filename)
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        data.update(updates)
        for key in remove_keys:
            data.pop(key, None)
        descriptor, temporary = tempfile.mkstemp(dir=self.creds_dir, prefix='.credential-')
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, indent=4, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
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

            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted credential file: {filename}")
            else:
                logger.warning(f"Credential file already missing: {filename}")

            # 重新加载凭证列表，重置索引等状态
            self.load_all_tokens()
            # 清理手动选择（若已删除的索引影响手动选择状态）
            if self.manual_selected_index is not None and self.manual_selected_index == index:
                self.manual_selected_index = None
                logger.info("Cleared manual selection because deleted credential was selected")
            self.save_state()
            return True
        except Exception as e:
            logger.error(f"Failed to delete credential at index {index}: {e}")
            return False

    def set_mode(self, mode: str, index: Optional[int] = None) -> bool:
        """设置凭证模式: 'auto' 自动轮换 | 'manual' 锁定指定凭证"""
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
            logger.info("Switched to auto rotation mode")
        else:
            logger.error(f"Invalid mode: {mode}")
            return False
        self.save_state()
        return True

    def get_current_credential_info(self) -> Dict:
        """获取当前使用的凭证信息"""
        from config import get_rotation_count

        if not self.credentials:
            return {"status": "no_credentials"}

        rotation_count = get_rotation_count()

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
            "rotation_count": rotation_count
        }


# 全局token管理器实例
codebuddy_token_manager = CodeBuddyTokenManager()
