"""Automatic WorkBuddy / CodeBuddy daily check-in management."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

import httpx

from config import (
    get_auto_checkin_enabled,
    get_bark_url,
    get_checkin_hour_minute,
    get_checkin_time,
    get_codebuddy_api_endpoint,
    get_codebuddy_ssl_verify,
)
from .workbuddy_token_manager import workbuddy_token_manager

logger = logging.getLogger(__name__)
CHINA_TIMEZONE = timezone(timedelta(hours=8))
# Desktop check-in uses User-Agent WorkBuddy/* but X-Product = deploymentType (SaaS).
# X-Product=WorkBuddy is only for /v2/activity/workbuddy/banner, not billing/meter.
WORKBUDDY_CLIENT_VERSION = "5.2.5"
CHECKIN_PRODUCT = "SaaS"


class CheckinError(RuntimeError):
    """Raised when the upstream check-in API returns an error."""


class CodeBuddyCheckinManager:
    STATUS_PATH = "/billing/meter/checkin-status"
    CLAIM_PATH = "/billing/meter/daily-checkin"
    # Upstream daily-checkin business code when the account already claimed today.
    ALREADY_CHECKED_IN_CODES = {10001}

    def __init__(
        self,
        token_manager=None,
        state_file: str = "config/checkin_state.json",
        client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
    ):
        self.token_manager = token_manager or workbuddy_token_manager
        self.state_file = state_file
        self.client_factory = client_factory
        self.state: Dict[str, Any] = {
            "last_run_at": None,
            "last_scheduled_date": None,
            "accounts": {},
        }
        self._run_lock = asyncio.Lock()
        self._stop_event: Optional[asyncio.Event] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._load_state()

    def _load_state(self) -> None:
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r", encoding="utf-8") as handle:
                    stored = json.load(handle)
                if isinstance(stored, dict):
                    self.state.update(stored)
                    self.state.setdefault("accounts", {})
        except Exception as exc:
            logger.warning("Failed to load check-in state: %s", exc)

    def _save_state(self) -> None:
        directory = os.path.dirname(self.state_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary = f"{self.state_file}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.state_file)

    @staticmethod
    def _today() -> str:
        return datetime.now(CHINA_TIMEZONE).date().isoformat()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(CHINA_TIMEZONE)

    def _new_client(self) -> httpx.AsyncClient:
        if self.client_factory:
            return self.client_factory()
        return httpx.AsyncClient(
            base_url=get_codebuddy_api_endpoint().rstrip("/"),
            timeout=httpx.Timeout(15.0),
            verify=get_codebuddy_ssl_verify(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    @staticmethod
    def _credential_user_id(credential: Dict[str, Any]) -> str:
        user_info = credential.get("user_info") if isinstance(credential.get("user_info"), dict) else {}
        for key in ("user_id", "uid", "id"):
            value = credential.get(key) or user_info.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _credential_domain(credential: Dict[str, Any]) -> str:
        for key in ("domain", "auth_domain"):
            value = credential.get(key)
            if value:
                return str(value)
        from config import get_codebuddy_api_host
        return get_codebuddy_api_host()

    @classmethod
    def _workbuddy_headers(cls, credential: Dict[str, Any], token: str) -> Dict[str, str]:
        """Build headers aligned with WorkBuddy desktop /billing/meter check-in calls."""
        user_id = cls._credential_user_id(credential)
        domain = cls._credential_domain(credential)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": f"WorkBuddy/{WORKBUDDY_CLIENT_VERSION}",
            "X-Product": CHECKIN_PRODUCT,
            "X-Domain": domain,
            "X-Requested-With": "XMLHttpRequest",
        }
        if user_id:
            headers["X-User-Id"] = user_id
        enterprise_id = credential.get("enterprise_id") or credential.get("enterpriseId")
        if enterprise_id:
            headers["X-Enterprise-Id"] = str(enterprise_id)
            headers["X-Tenant-Id"] = str(enterprise_id)
        return headers

    @staticmethod
    async def _post_payload(
        client: httpx.AsyncClient,
        path: str,
        credential: Dict[str, Any],
        token: str,
    ) -> Dict[str, Any]:
        response = await client.post(
            path,
            headers=CodeBuddyCheckinManager._workbuddy_headers(credential, token),
            json={},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            response.raise_for_status()
            raise CheckinError(f"签到接口返回了非 JSON 响应（HTTP {response.status_code}）") from exc
        if not isinstance(payload, dict):
            raise CheckinError("签到接口返回了无效数据")
        # Upstream often returns business failures as HTTP 400 with {code,msg}.
        if response.status_code >= 500:
            raise CheckinError(f"签到接口 HTTP {response.status_code}")
        return payload

    @classmethod
    async def _post(
        cls,
        client: httpx.AsyncClient,
        path: str,
        credential: Dict[str, Any],
        token: str,
    ) -> Dict[str, Any]:
        payload = await cls._post_payload(client, path, credential, token)
        if payload.get("code") != 0:
            message = payload.get("msg") or f"业务错误 {payload.get('code')}"
            raise CheckinError(str(message))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise CheckinError("签到接口未返回状态数据")
        return data

    @classmethod
    def _is_already_checked_in(cls, payload: Dict[str, Any]) -> bool:
        if payload.get("code") in cls.ALREADY_CHECKED_IN_CODES:
            return True
        return "已签到" in str(payload.get("msg") or "")

    def _base_result(self, record: Dict[str, Any]) -> Dict[str, Any]:
        data = record["data"]
        return {
            "filename": record["filename"],
            "user_id": self._credential_user_id(data) or data.get("domain") or "unknown",
            "status": "pending",
            "message": "等待检查",
            "checked_at": None,
            "checkin_date": self._today(),
            "active": None,
            "today_checked_in": False,
            "streak_days": 0,
            "today_credit": 0,
            "total_credits": 0,
            "week_progress": [],
        }

    @staticmethod
    def _merge_upstream(result: Dict[str, Any], data: Dict[str, Any]) -> None:
        for key in (
            "active",
            "today_checked_in",
            "streak_days",
            "today_credit",
            "daily_credit",
            "total_credits",
            "week_progress",
            "is_streak_day",
            "next_streak_day",
            "streak_bonus_credit",
            "activity_name",
            "end_time",
        ):
            if key in data:
                result[key] = data[key]

    async def _check_account(
        self,
        client: httpx.AsyncClient,
        record: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = self._base_result(record)
        result["checked_at"] = int(time.time())
        credential = record["data"]
        token = credential.get("bearer_token")
        if not token:
            result.update(status="error", message="凭证缺少 Bearer Token")
            return result
        if self.token_manager.is_token_expired(credential):
            result.update(status="expired", message="凭证已过期")
            return result

        try:
            status_data = await self._post(client, self.STATUS_PATH, credential, token)
            self._merge_upstream(result, status_data)
            logger.info(
                "Check-in status for %s: active=%s today_checked_in=%s activity=%s",
                record["filename"],
                status_data.get("active"),
                status_data.get("today_checked_in"),
                status_data.get("activity_name"),
            )
            if status_data.get("today_checked_in"):
                result.update(status="already_checked_in", message="今日奖励已领取")
                return result

            # checkin-status may report active=false even after a successful same-day claim.
            # Resolve the real state by attempting daily-checkin.
            claim_payload = await self._post_payload(client, self.CLAIM_PATH, credential, token)
            claim_code = claim_payload.get("code")
            claim_msg = str(claim_payload.get("msg") or "")
            logger.info(
                "Check-in claim for %s: code=%s msg=%s",
                record["filename"],
                claim_code,
                claim_msg,
            )

            if claim_code == 0:
                claim_data = claim_payload.get("data")
                if isinstance(claim_data, dict):
                    self._merge_upstream(result, claim_data)
                result.update(
                    status="claimed",
                    message="签到成功，奖励已领取",
                    today_checked_in=True,
                    active=True,
                    claimed_at=int(time.time()),
                )
                try:
                    refreshed = await self._post(client, self.STATUS_PATH, credential, token)
                    self._merge_upstream(result, refreshed)
                except Exception as exc:
                    logger.debug(
                        "Post-claim status refresh failed for %s: %s",
                        record["filename"],
                        exc,
                    )
                return result

            if self._is_already_checked_in(claim_payload):
                result.update(
                    status="already_checked_in",
                    message=claim_msg or "今日奖励已领取",
                    today_checked_in=True,
                )
                return result

            if not status_data.get("active"):
                activity = status_data.get("activity_name")
                message = claim_msg or "当前账号未开放签到活动"
                if activity and "活动" not in message:
                    message = f"{message}（{activity}）"
                result.update(status="inactive", message=message)
                return result

            result.update(
                status="error",
                message=f"签到检查失败：{claim_msg or f'业务错误 {claim_code}'}",
            )
            return result
        except httpx.HTTPStatusError as exc:
            detail = f"HTTP {exc.response.status_code}"
        except (httpx.HTTPError, CheckinError, ValueError) as exc:
            detail = str(exc)
        except Exception as exc:
            logger.exception("Unexpected check-in failure for %s", record["filename"])
            detail = str(exc)

        result.update(status="error", message=f"签到检查失败：{detail}")
        return result

    async def run_all(self, force: bool = False, scheduled: bool = False) -> Dict[str, Any]:
        async with self._run_lock:
            self._running = True
            results: List[Dict[str, Any]] = []
            try:
                if hasattr(self.token_manager, "load_all_tokens"):
                    self.token_manager.load_all_tokens()
                records = self.token_manager.get_credential_records()
                today = self._today()
                existing = self.state.setdefault("accounts", {})
                if records:
                    async with self._new_client() as client:
                        for record in records:
                            previous = existing.get(record["filename"], {})
                            if (
                                not force
                                and previous.get("checkin_date") == today
                                and previous.get("status") in ("claimed", "already_checked_in")
                            ):
                                result = dict(previous)
                            else:
                                result = await self._check_account(client, record)
                            existing[record["filename"]] = result
                            results.append(result)

                current_filenames = {record["filename"] for record in records}
                self.state["accounts"] = {
                    filename: value
                    for filename, value in existing.items()
                    if filename in current_filenames
                }
                self.state["last_run_at"] = int(time.time())
                if scheduled:
                    self.state["last_scheduled_date"] = today
                self._save_state()
                logger.info(
                    "Automatic check-in completed: %s",
                    {item["filename"]: item["status"] for item in results},
                )
            finally:
                self._running = False
        status = self.get_status()
        if scheduled and results:
            await self._push_bark(status)
        return status

    @staticmethod
    def _format_bark_body(status: Dict[str, Any]) -> str:
        accounts = status.get("accounts") or []
        if not accounts:
            return "没有可签到账号"
        lines = []
        claimed = 0
        done = 0
        errors = 0
        for account in accounts:
            name = account.get("user_id") or account.get("filename") or "账号"
            message = account.get("message") or account.get("status") or ""
            credit = account.get("today_credit") or account.get("daily_credit") or 0
            state = account.get("status")
            if state in ("claimed", "already_checked_in"):
                done += 1
            if state == "claimed":
                claimed += 1
            if state in ("error", "expired"):
                errors += 1
            lines.append(f"{name}: {message} (+{credit})")
        summary = f"完成 {done}/{len(accounts)}，新领取 {claimed}"
        if errors:
            summary += f"，失败 {errors}"
        return summary + "\n" + "\n".join(lines)

    async def _push_bark(self, status: Dict[str, Any]) -> None:
        bark_url = get_bark_url()
        if not bark_url:
            return
        title = "CodeBuddy 每日签到"
        body = self._format_bark_body(status)
        base = bark_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.post(
                    base,
                    json={
                        "title": title,
                        "body": body,
                        "group": "CodeBuddy签到",
                    },
                )
                if response.status_code >= 400:
                    response = await client.get(f"{base}/{quote(title)}/{quote(body)}")
                response.raise_for_status()
            logger.info("Check-in result pushed to Bark")
        except Exception as exc:
            logger.warning("Failed to push check-in result to Bark: %s", exc)

    def _next_run_datetime(self, now: Optional[datetime] = None) -> datetime:
        current = now or self._now()
        hour, minute = get_checkin_hour_minute()
        today_target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        today = current.date().isoformat()
        if self.state.get("last_scheduled_date") == today:
            return today_target + timedelta(days=1)
        if current < today_target:
            return today_target
        # Past today's scheduled time and not yet marked as scheduled → run ASAP.
        return current

    def _seconds_until_next_run(self) -> float:
        target = self._next_run_datetime()
        return max(0.0, (target - self._now()).total_seconds())

    def get_status(self) -> Dict[str, Any]:
        if hasattr(self.token_manager, "load_all_tokens"):
            self.token_manager.load_all_tokens()
        records = self.token_manager.get_credential_records()
        stored = self.state.get("accounts", {})
        accounts = []
        for record in records:
            result = stored.get(record["filename"])
            if result:
                accounts.append(dict(result))
            else:
                accounts.append(self._base_result(record))

        last_run_at = self.state.get("last_run_at")
        schedule_time = get_checkin_time()
        next_run = self._next_run_datetime() if get_auto_checkin_enabled() else None
        authorized = bool(records)
        return {
            "enabled": get_auto_checkin_enabled(),
            "authorized": authorized,
            "message": None if authorized else "请先授权 WorkBuddy",
            "schedule_time": schedule_time,
            "running": self._running,
            "last_run_at": last_run_at,
            "last_scheduled_date": self.state.get("last_scheduled_date"),
            "next_run_at": int(next_run.timestamp()) if next_run else None,
            "bark_url": get_bark_url(),
            "accounts": accounts,
        }

    async def _loop(self) -> None:
        try:
            while self._stop_event and not self._stop_event.is_set():
                if not get_auto_checkin_enabled():
                    delay = 60.0
                else:
                    delay = self._seconds_until_next_run()
                    if delay <= 0:
                        try:
                            await self.run_all(scheduled=True)
                        except Exception:
                            logger.exception("Automatic check-in cycle failed")
                        # Avoid a tight loop if something goes wrong with state.
                        delay = max(1.0, self._seconds_until_next_run())
                    # Wake periodically so schedule/settings changes apply without restart.
                    delay = min(delay, 60.0)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def startup(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="codebuddy-auto-checkin")
        logger.info(
            "Automatic check-in scheduler started (enabled=%s, time=%s CST)",
            get_auto_checkin_enabled(),
            get_checkin_time(),
        )

    async def shutdown(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None


checkin_manager = CodeBuddyCheckinManager()
