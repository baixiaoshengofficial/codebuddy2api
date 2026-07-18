"""
Settings Router - For loading and saving .env configurations
"""
import os
import logging
from fastapi import APIRouter, HTTPException, Depends, Query, Response
from pydantic import BaseModel
from typing import Dict, Any

from .auth import authenticate
from config import get_active_config, update_settings
from .usage_stats_manager import usage_stats_manager
from .request_log_manager import request_log_manager
from .checkin_manager import checkin_manager

logger = logging.getLogger(__name__)
router = APIRouter()

# 中文标签映射
SETTING_LABELS = {
    "CODEBUDDY_HOST": "服务主机地址",
    "CODEBUDDY_PORT": "服务端口",
    "CODEBUDDY_PASSWORD": "API 服务访问密码",
    "CODEBUDDY_SITE": "CodeBuddy 站点 (international/china)",
    "CODEBUDDY_CREDS_DIR": "CodeBuddy 凭证文件目录",
    "WORKBUDDY_CREDS_DIR": "WorkBuddy 签到凭证目录",
    "CODEBUDDY_LOG_LEVEL": "日志级别",
    "CODEBUDDY_ROTATION_COUNT": "凭证轮换频率 (N次请求/凭证，设为0关闭轮换)",
    "CODEBUDDY_AUTO_CHECKIN": "自动领取每日签到奖励",
    "CODEBUDDY_CHECKIN_TIME": "每日自动签到时间（北京时间 HH:MM）",
    "CODEBUDDY_BARK_URL": "签到结果 Bark 推送地址",
}

READONLY_SETTING_KEYS = {
    "CODEBUDDY_HOST",
    "CODEBUDDY_PORT",
    "CODEBUDDY_CREDS_DIR",
    "WORKBUDDY_CREDS_DIR",
    "CODEBUDDY_LOG_LEVEL",
}

class Settings(BaseModel):
    settings: Dict[str, Any]

@router.get("/settings", summary="Get all current active settings and labels")
async def get_settings(_token: str = Depends(authenticate)):
    """Returns the current config and their Chinese labels."""
    try:
        return {
            "settings": get_active_config(),
            "labels": SETTING_LABELS,
            "readonly_keys": sorted(READONLY_SETTING_KEYS),
        }
    except Exception as e:
        logger.error(f"Error retrieving active config: {e}")
        raise HTTPException(status_code=500, detail="Could not retrieve settings.")

@router.post("/settings", summary="Save and hot-reload settings")
async def save_settings(new_settings: Settings, _token: str = Depends(authenticate)):
    """Saves settings to config.json and hot-reloads them into memory."""
    try:
        settings = {
            key: value
            for key, value in new_settings.settings.items()
            if key not in READONLY_SETTING_KEYS
        }
        update_settings(settings)
        return {"message": "设置已保存并成功热加载！"}
    except Exception as e:
        logger.error(f"Error saving settings: {e}")
        raise HTTPException(status_code=500, detail="无法保存设置文件。")

@router.get("/stats", summary="Get usage statistics")
async def get_usage_stats(_token: str = Depends(authenticate)):
    """Returns usage statistics for models and credentials."""
    try:
        persistent_stats = request_log_manager.get_aggregate_stats()
        if persistent_stats["model_usage"] or persistent_stats["credential_usage"]:
            return persistent_stats
        return usage_stats_manager.get_stats()
    except Exception as e:
        logger.error(f"Error retrieving usage stats: {e}")
        raise HTTPException(status_code=500, detail="Could not retrieve usage statistics.")


@router.get("/checkin", summary="Get automatic check-in status")
async def get_checkin_status(_token: str = Depends(authenticate)):
    return checkin_manager.get_status()


@router.post("/checkin/run", summary="Run check-in for every credential now")
async def run_checkin(_token: str = Depends(authenticate)):
    try:
        return await checkin_manager.run_all(force=True)
    except Exception as e:
        logger.error("Manual check-in failed: %s", e)
        raise HTTPException(status_code=500, detail="自动签到执行失败。")


@router.get("/request-logs", summary="Get persistent request-level usage logs")
async def get_request_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    days: int = Query(1, ge=0, le=3650),
    model: str = Query(""),
    client: str = Query(""),
    credential: str = Query(""),
    status: str = Query(""),
    _token: str = Depends(authenticate),
):
    try:
        return request_log_manager.list_requests(
            page=page,
            page_size=page_size,
            days=days or None,
            model=model or None,
            client=client or None,
            credential=credential or None,
            status=status or None,
        )
    except Exception as e:
        logger.error(f"Error retrieving request logs: {e}")
        raise HTTPException(status_code=500, detail="Could not retrieve request logs.")


@router.get("/request-logs/export", summary="Export request-level usage logs as CSV")
async def export_request_logs(
    days: int = Query(30, ge=0, le=3650),
    model: str = Query(""),
    client: str = Query(""),
    credential: str = Query(""),
    status: str = Query(""),
    _token: str = Depends(authenticate),
):
    try:
        filename, content = request_log_manager.export_csv(
            days=days or None,
            model=model or None,
            client=client or None,
            credential=credential or None,
            status=status or None,
        )
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.error(f"Error exporting request logs: {e}")
        raise HTTPException(status_code=500, detail="Could not export request logs.")
