"""HTTP boundary for console queries and credential commands."""

import os
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from ..auth import authenticate
from ..codebuddy_token_manager import codebuddy_token_manager
from ..request_log_manager import request_log_manager
from .diagnostics import DiagnosticsRepository
from .credential_service import CredentialConsoleService, CredentialConflict
from ..request_query import RequestFilter, time_range
from .read_models import ConsoleReadModel

router = APIRouter(prefix="/api/console", dependencies=[Depends(authenticate)])


async def get_logs():
    return request_log_manager


async def get_tokens():
    return codebuddy_token_manager


async def get_diagnostics(logs=Depends(get_logs)):
    return DiagnosticsRepository(logs.connect)


async def request_filter(
    period: str = "today",
    start: int = None,
    end: int = None,
    offset: int = Query(480, ge=-720, le=840),
    search: str = Query("", max_length=200),
    model: str = "",
    client: str = "",
    credential: str = "",
    status: str = "",
):
    try:
        start, end = time_range(period, start, end, offset)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if status not in ("", "success", "error", "pending"):
        raise HTTPException(422, "无效的请求状态")
    return RequestFilter(start, end, search, model, client, credential, status)


@router.get("/status")
async def service_status():
    return {
        "status": "healthy",
        "version": os.getenv("CODEBUDDY_VERSION", "1.0.0"),
        "revision": os.getenv("CODEBUDDY_REVISION") or None,
    }


@router.get("/credentials")
async def credentials(tokens=Depends(get_tokens), diagnostics=Depends(get_diagnostics)):
    return {**tokens.get_routing_snapshot(), "events": diagnostics.events()}


class CredentialCommand(BaseModel):
    mode: str
    filename: str = None


@router.post("/credentials/mode")
async def set_mode(
    command: CredentialCommand,
    tokens=Depends(get_tokens),
    diagnostics=Depends(get_diagnostics),
):
    try:
        return CredentialConsoleService(tokens, diagnostics).set_mode(
            command.mode, command.filename
        )
    except CredentialConflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.delete("/credentials/{filename}")
async def delete_credential(
    filename: str, tokens=Depends(get_tokens), diagnostics=Depends(get_diagnostics)
):
    try:
        CredentialConsoleService(tokens, diagnostics).delete(filename)
    except CredentialConflict as exc:
        raise HTTPException(404, str(exc))
    except OSError as exc:
        raise HTTPException(500, str(exc))
    return {"message": "凭证已删除"}


@router.get("/requests")
async def requests(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    query=Depends(request_filter),
    logs=Depends(get_logs),
):
    return logs.list_requests(page=page, page_size=page_size, query=query)


@router.get("/requests/export")
async def export(query=Depends(request_filter), logs=Depends(get_logs)):
    filename, content = logs.export_csv(query=query)
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="' + filename + '"'},
    )


@router.get("/requests/{record_id}")
async def detail(
    record_id: int, logs=Depends(get_logs), diagnostics=Depends(get_diagnostics)
):
    result = ConsoleReadModel(logs, diagnostics).detail(record_id)
    if result is None:
        raise HTTPException(404, "请求记录不存在")
    return result


@router.get("/overview")
async def overview(
    query=Depends(request_filter),
    logs=Depends(get_logs),
    diagnostics=Depends(get_diagnostics),
):
    return ConsoleReadModel(logs, diagnostics).summary(query)
