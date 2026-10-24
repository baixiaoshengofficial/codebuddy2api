"""Execute each upstream attempt once; dependencies are supplied by composition."""

import json
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .observation import AttemptObserver
from .protocol import (
    SSE_HEADERS,
    InsufficientCreditError,
    OpenAICompatibilityConverter,
    StreamResponseAggregator,
    format_sse_error,
    has_stream_output,
    headers_for_credential,
    is_insufficient_credit_error,
    parse_sse_line,
)

logger = logging.getLogger(__name__)


class CodeBuddyStreamService:
    """CodeBuddy 流式服务类 - 职责分离，使用连接池优化"""

    def __init__(
        self,
        client_factory,
        api_url_factory,
        credentials,
        select_after_exhaustion,
        credential=None,
        observer=None,
    ):
        self.client_factory = client_factory
        self.api_url_factory = api_url_factory
        self.credentials = credentials
        self.select_after_exhaustion = select_after_exhaustion
        self.observer = observer or AttemptObserver()
        self.attempt_count = 0
        self._record_credential(credential or {})

    def _mark_exhausted(self, index):
        self.credentials.mark_credential_exhausted(index)
        snapshot = self.credentials.get_routing_snapshot()
        current = next(
            (row["filename"] for row in snapshot["credentials"] if row["is_current"]),
            None,
        )
        previous = self.credential_for_audit.get("_credential_filename")
        if previous != current:
            self.observer.switch(previous, current)

    def _record_credential(self, credential: Dict[str, Any]) -> None:
        previous = getattr(self, "credential_for_audit", {}).get("_credential_filename")
        if previous and previous != credential.get("_credential_filename"):
            self.observer.switch(previous, credential.get("_credential_filename"))
        self.credential_for_audit = {
            key: credential.get(key) for key in ("_credential_filename", "user_id")
        }

    def _handle_api_error(self, status_code: int, error_msg: str) -> None:
        """统一的API错误处理 - 直接抛出异常"""
        logger.error(f"CodeBuddy API错误: {status_code} - {error_msg}")

        if status_code == 401:
            raise HTTPException(
                status_code=401, detail="CodeBuddy API authentication failed"
            )
        elif status_code == 429:
            raise HTTPException(
                status_code=429, detail="CodeBuddy API rate limit exceeded"
            )
        elif status_code >= 500:
            raise HTTPException(status_code=502, detail="CodeBuddy API server error")
        else:
            raise HTTPException(
                status_code=status_code, detail=f"CodeBuddy API error: {error_msg}"
            )

    async def handle_stream_response(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        credential_index: Optional[int] = None,
        retry_on_exhaustion: bool = False,
    ) -> StreamingResponse:
        """处理流式响应 - 使用OpenAI兼容性转换器修复格式问题"""

        async def stream_attempt(attempt_headers: Dict[str, str], observation):
            client = await self.client_factory()
            self.attempt_count += 1
            async with client.stream(
                "POST", self.api_url_factory(), json=payload, headers=attempt_headers
            ) as response:
                observation["status_code"] = response.status_code
                if response.status_code != 200:
                    error_text = await response.aread()
                    error_msg = error_text.decode("utf-8", errors="ignore")
                    if is_insufficient_credit_error(error_msg):
                        raise InsufficientCreditError(error_msg)
                    yield format_sse_error(
                        f"CodeBuddy API error: {response.status_code} - {error_msg}",
                        "api_error",
                    )
                    return

                buffer = ""
                tool_call_index_map = {}  # 用于跟踪工具调用ID到index的映射

                async for chunk in response.aiter_text():
                    if not chunk:
                        continue

                    buffer += chunk

                    # 处理完整的SSE行
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)

                        # 跳过空行和注释行
                        if not line.strip() or line.startswith(":"):
                            continue

                        # 检查是否结束
                        if "[DONE]" in line:
                            yield "data: [DONE]\n\n"
                            return

                        # 解析SSE数据
                        chunk_data = parse_sse_line(line)
                        if chunk_data:
                            if is_insufficient_credit_error(chunk_data):
                                raise InsufficientCreditError(
                                    str(chunk_data.get("error", chunk_data))
                                )
                            # 🔑 关键修改：使用OpenAI兼容性转换器
                            converted_chunk = OpenAICompatibilityConverter.convert_sse_chunk_to_openai_format(
                                chunk_data, tool_call_index_map
                            )

                            # 重新格式化为SSE格式并发送
                            converted_line = f"data: {json.dumps(converted_chunk, ensure_ascii=False)}"
                            yield converted_line + "\n\n"
                        else:
                            # 非数据行直接转发
                            yield line + "\n\n"

                # 处理缓冲区中剩余的数据
                if buffer.strip():
                    chunk_data = parse_sse_line(buffer.strip())
                    if chunk_data:
                        if is_insufficient_credit_error(chunk_data):
                            raise InsufficientCreditError(
                                str(chunk_data.get("error", chunk_data))
                            )
                        converted_chunk = OpenAICompatibilityConverter.convert_sse_chunk_to_openai_format(
                            chunk_data, tool_call_index_map
                        )
                        converted_line = (
                            f"data: {json.dumps(converted_chunk, ensure_ascii=False)}"
                        )
                        yield converted_line + "\n\n"
                    else:
                        yield buffer + "\n\n"

        async def observed_attempt(attempt_headers):
            with self.observer.attempt(
                self.attempt_count + 1,
                self.credential_for_audit.get("_credential_filename"),
            ) as result:
                async for chunk in stream_attempt(attempt_headers, result):
                    for line in str(chunk).splitlines():
                        if line.strip() == "data: [DONE]":
                            result["complete"] = True
                        obj = parse_sse_line(line)
                        if obj and obj.get("error"):
                            result.update(
                                status_code=(
                                    result["status_code"]
                                    if result["status_code"] >= 400
                                    else 502
                                ),
                                error=obj["error"],
                            )
                    yield chunk

        async def stream_core():
            active_headers = headers
            active_index = credential_index
            attempted = {active_index}
            while True:
                sent_output = False
                pending = []
                try:
                    async for chunk in observed_attempt(active_headers):
                        if not sent_output and not has_stream_output(chunk):
                            pending.append(chunk)
                            continue
                        for prior in pending:
                            yield prior
                        pending.clear()
                        sent_output = True
                        yield chunk
                    for prior in pending:
                        yield prior
                    return
                except InsufficientCreditError as exc:
                    if not retry_on_exhaustion or sent_output:
                        if (
                            retry_on_exhaustion
                            and sent_output
                            and active_index is not None
                        ):
                            self._mark_exhausted(active_index)
                        yield format_sse_error(str(exc), "insufficient_credit")
                        return
                    credential = await self.select_after_exhaustion(active_index)
                    if not credential or credential["_credential_index"] in attempted:
                        yield format_sse_error(
                            "所有可用凭证积分不足", "insufficient_credit"
                        )
                        return
                    active_index = credential["_credential_index"]
                    attempted.add(active_index)
                    active_headers = headers_for_credential(headers, credential)
                    self._record_credential(credential)

        async def stream_response():
            try:
                async for chunk in stream_core():
                    yield chunk
            except httpx.TransportError as exc:
                # A dropped connection does not prove the upstream rejected the request.
                # Replaying this POST can charge credits for the same conversation twice.
                logger.error(
                    "CodeBuddy stream connection failed: %s", type(exc).__name__
                )
                yield format_sse_error(
                    "CodeBuddy upstream connection failed", "connection_failed"
                )
            except Exception as exc:
                logger.error("CodeBuddy stream failed: %s", exc)
                yield format_sse_error("CodeBuddy stream failed", "stream_error")
                raise

        return StreamingResponse(
            stream_response(), media_type="text/event-stream", headers=SSE_HEADERS
        )

    async def handle_non_stream_response(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        credential_index: Optional[int] = None,
        retry_on_exhaustion: bool = False,
    ) -> Dict[str, Any]:
        """处理非流式响应 - 使用修复后的聚合器，支持多工具调用"""
        active_headers = headers
        active_index = credential_index
        attempted = {active_index}
        while True:
            try:
                with self.observer.attempt(
                    self.attempt_count + 1,
                    self.credential_for_audit.get("_credential_filename"),
                ):
                    return await self._handle_non_stream_once(payload, active_headers)
            except InsufficientCreditError as exc:
                if not retry_on_exhaustion:
                    raise HTTPException(
                        status_code=402, detail="CodeBuddy 凭证积分不足"
                    )
                if not exc.can_retry:
                    if active_index is not None:
                        self._mark_exhausted(active_index)
                    raise HTTPException(
                        status_code=402, detail="CodeBuddy 凭证积分不足，回复已开始生成"
                    )
                credential = await self.select_after_exhaustion(active_index)
                if not credential or credential["_credential_index"] in attempted:
                    raise HTTPException(status_code=402, detail="所有可用凭证积分不足")
                active_index = credential["_credential_index"]
                attempted.add(active_index)
                active_headers = headers_for_credential(headers, credential)
                self._record_credential(credential)

    async def _handle_non_stream_once(
        self, payload: Dict[str, Any], headers: Dict[str, str]
    ) -> Dict[str, Any]:
        try:
            client = await self.client_factory()
            # Never replay a chat POST after a transport failure: it may have been billed.
            self.attempt_count += 1
            response = await client.post(
                self.api_url_factory(), json=payload, headers=headers
            )

            if response.status_code != 200:
                error_msg = response.text
                if is_insufficient_credit_error(error_msg):
                    raise InsufficientCreditError(error_msg)
                self._handle_api_error(response.status_code, error_msg)

            aggregator = StreamResponseAggregator()
            buffer = ""
            output_started = False

            async for chunk in response.aiter_text():
                if not chunk:
                    continue
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    obj = parse_sse_line(line)
                    if obj:
                        if is_insufficient_credit_error(obj):
                            raise InsufficientCreditError(
                                str(obj.get("error", obj)),
                                can_retry=not output_started,
                            )
                        if obj.get("error"):
                            raise HTTPException(
                                status_code=502, detail=str(obj["error"])
                            )
                        output_started = output_started or has_stream_output(line)
                        aggregator.process_chunk(obj)

            if buffer.strip():
                obj = parse_sse_line(buffer.strip())
                if obj:
                    if is_insufficient_credit_error(obj):
                        raise InsufficientCreditError(
                            str(obj.get("error", obj)),
                            can_retry=not output_started,
                        )
                    if obj.get("error"):
                        raise HTTPException(status_code=502, detail=str(obj["error"]))
                    aggregator.process_chunk(obj)

            return aggregator.finalize()

        except httpx.TimeoutException as exc:
            logger.error("CodeBuddy API 超时: %s", type(exc).__name__)
            raise HTTPException(status_code=504, detail="CodeBuddy API timeout")
        except httpx.TransportError as e:
            logger.error(f"网络错误: {e}")
            raise HTTPException(status_code=502, detail=f"Network error: {str(e)}")
        except HTTPException:
            raise
        except InsufficientCreditError:
            raise
        except Exception as e:
            logger.error(f"请求异常: {e}")
            raise HTTPException(status_code=500, detail=f"Request error: {str(e)}")
