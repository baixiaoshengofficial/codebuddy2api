"""Persistent request-level audit log for model API calls."""
import csv
import io
import json
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "config", "request_usage.db")
)
MAX_PREVIEW_LENGTH = 500
MAX_RECORDS = 50000


def _compact_text(value: Any) -> str:
    if isinstance(value, str):
        text = value
    elif value is None:
        text = ""
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    return re.sub(r"\s+", " ", text).strip()


def _redact_secrets(text: str) -> str:
    patterns = (
        (r"(?i)(bearer\s+)[a-z0-9._~+/-]+", r"\1[REDACTED]"),
        (r"(?i)((?:api[_-]?key|token|password)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"sk-[a-zA-Z0-9_-]{16,}", "sk-[REDACTED]"),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def build_request_preview(request_body: Dict[str, Any]) -> str:
    """Build a compact preview for only the current request turn."""
    preview = ""
    messages = request_body.get("messages")
    if isinstance(messages, list):
        conversational = [
            item for item in messages
            if isinstance(item, dict) and item.get("role") in ("user", "assistant", "tool")
        ]
        request_messages = [
            item for item in conversational if item.get("role") in ("user", "tool")
        ]
        current = request_messages[-1] if request_messages else (
            conversational[-1] if conversational else None
        )
        if current:
            item = current
            role = str(item.get("role", "message")).capitalize()
            preview = f"{role}: {_compact_text(item.get('content'))}"
    elif "input" in request_body:
        input_value = request_body.get("input")
        if isinstance(input_value, list):
            conversational = [
                item for item in input_value
                if isinstance(item, dict) and item.get("role") in ("user", "assistant", "tool")
            ]
            if conversational:
                request_messages = [
                    item for item in conversational if item.get("role") in ("user", "tool")
                ]
                item = request_messages[-1] if request_messages else conversational[-1]
                role = str(item.get("role", "input")).capitalize()
                preview = f"{role}: {_compact_text(item.get('content'))}"
            else:
                preview = _compact_text(input_value[-1] if input_value else "")
        else:
            preview = _compact_text(input_value)

    preview = _redact_secrets(preview)
    if not preview:
        preview = "(empty request preview)"
    return preview[:MAX_PREVIEW_LENGTH]


def build_response_preview(output_value: Any) -> Optional[str]:
    """Build a compact, redacted response preview when output is available."""
    preview = _redact_secrets(_compact_text(output_value))
    return preview[:MAX_PREVIEW_LENGTH] if preview else None


CLIENT_HINT_HEADERS = (
    "x-client-name",
    "x-agent-name",
    "x-app-name",
    "x-ide-name",
    "x-source",
)

CLIENT_SIGNATURES = (
    (("hermes agent", "hermes-agent", "hermesagent"), "Hermes"),
    (("claude code", "claude-code"), "Claude Code"),
    (("codex",), "Codex"),
    (("codebuddy", "code-buddy"), "CodeBuddy"),
    (("open-webui", "open webui"), "Open WebUI"),
    (("cherry studio", "cherry-studio"), "Cherry Studio"),
    (("roo code", "roo-code"), "Roo Code"),
    (("cline",), "Cline"),
    (("cursor",), "Cursor"),
    (("continue.dev", "continue/"), "Continue"),
    (("aider",), "Aider"),
    (("litellm",), "LiteLLM"),
    (("langchain",), "LangChain"),
    (("llamaindex", "llama-index"), "LlamaIndex"),
    (("chatbox",), "Chatbox"),
    (("curl",), "curl"),
)


def _canonical_client_name(value: Any) -> str:
    source = _compact_text(value)
    source_lower = source.lower()
    for signatures, label in CLIENT_SIGNATURES:
        if any(signature in source_lower for signature in signatures):
            return label
    return source[:80]


def _body_client_hint(request_body: Optional[Dict[str, Any]]) -> str:
    if not isinstance(request_body, dict):
        return ""

    metadata = request_body.get("metadata")
    if isinstance(metadata, dict):
        for key in ("client", "client_name", "source", "app", "agent"):
            if metadata.get(key):
                return _canonical_client_name(metadata[key])

    for key in ("client", "client_name", "source", "app"):
        if request_body.get(key):
            return _canonical_client_name(request_body[key])

    system_parts: List[str] = []
    if request_body.get("instructions"):
        system_parts.append(_compact_text(request_body["instructions"]))
    messages = request_body.get("messages") or request_body.get("input")
    if isinstance(messages, list):
        for item in messages:
            if isinstance(item, dict) and item.get("role") in ("system", "developer"):
                system_parts.append(_compact_text(item.get("content")))

    system_text = " ".join(system_parts).lower()
    for signatures, label in CLIENT_SIGNATURES:
        if any(signature in system_text for signature in signatures):
            return label
    return ""


def detect_client(headers: Any, request_body: Optional[Dict[str, Any]] = None) -> str:
    """Identify the calling app, keeping generic SDKs as a last resort."""
    for header in CLIENT_HINT_HEADERS:
        if headers.get(header):
            return _canonical_client_name(headers.get(header)) or "Unknown"

    body_hint = _body_client_hint(request_body)
    if body_hint:
        return body_hint

    user_agent = _compact_text(headers.get("user-agent", ""))
    canonical_user_agent = _canonical_client_name(user_agent)
    if canonical_user_agent and canonical_user_agent != user_agent[:80]:
        return canonical_user_agent

    user_agent_lower = user_agent.lower()
    stainless_language = _compact_text(headers.get("x-stainless-lang", ""))
    if "openai/" in user_agent_lower or stainless_language:
        language = stainless_language or (
            "Python" if "python" in user_agent_lower else "SDK"
        )
        return f"OpenAI {language.title()} SDK"
    if "python" in user_agent_lower or "httpx" in user_agent_lower or "requests" in user_agent_lower:
        return "Python（未标识）"
    if "node" in user_agent_lower or "javascript" in user_agent_lower:
        return "Node.js（未标识）"
    return user_agent[:80] or "Unknown"


def get_client_detail(headers: Any) -> Optional[str]:
    """Return non-secret transport details useful when auditing detection."""
    details: List[str] = []
    user_agent = _compact_text(headers.get("user-agent", ""))
    if user_agent:
        details.append(user_agent)
    stainless_language = _compact_text(headers.get("x-stainless-lang", ""))
    stainless_version = _compact_text(headers.get("x-stainless-package-version", ""))
    if stainless_language:
        sdk_detail = f"OpenAI {stainless_language} SDK"
        if stainless_version:
            sdk_detail += f" {stainless_version}"
        if sdk_detail.lower() not in user_agent.lower():
            details.append(sdk_detail)
    detail = " · ".join(details)
    return _redact_secrets(detail)[:160] or None


class RequestLogManager:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS request_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    conversation_id TEXT,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    endpoint TEXT NOT NULL,
                    model TEXT NOT NULL,
                    client TEXT NOT NULL,
                    client_detail TEXT,
                    client_host TEXT,
                    credential TEXT,
                    credential_user_id TEXT,
                    request_preview TEXT NOT NULL,
                    response_preview TEXT,
                    request_hash TEXT NOT NULL,
                    is_streaming INTEGER NOT NULL DEFAULT 0,
                    status_code INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    latency_ms INTEGER,
                    upstream_attempts INTEGER NOT NULL DEFAULT 1,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    token_estimated INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_request_logs_created_at ON request_logs(created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_request_logs_model ON request_logs(model)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_request_logs_credential ON request_logs(credential)"
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(request_logs)").fetchall()
            }
            if "token_estimated" not in columns:
                connection.execute(
                    "ALTER TABLE request_logs ADD COLUMN token_estimated INTEGER NOT NULL DEFAULT 0"
                )
            if "upstream_attempts" not in columns:
                connection.execute(
                    "ALTER TABLE request_logs ADD COLUMN upstream_attempts INTEGER NOT NULL DEFAULT 1"
                )
            if "client_detail" not in columns:
                connection.execute(
                    "ALTER TABLE request_logs ADD COLUMN client_detail TEXT"
                )
            if "response_preview" not in columns:
                connection.execute(
                    "ALTER TABLE request_logs ADD COLUMN response_preview TEXT"
                )

    def start_request(
        self,
        request_id: str,
        conversation_id: Optional[str],
        endpoint: str,
        model: str,
        client: str,
        client_detail: Optional[str],
        client_host: Optional[str],
        credential: Optional[str],
        credential_user_id: Optional[str],
        request_preview: str,
        request_hash: str,
        is_streaming: bool,
    ) -> int:
        now = int(time.time() * 1000)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO request_logs (
                    request_id, conversation_id, created_at, endpoint, model,
                    client, client_detail, client_host, credential, credential_user_id,
                    request_preview, request_hash, is_streaming
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    conversation_id,
                    now,
                    endpoint,
                    model or "unknown",
                    client,
                    client_detail,
                    client_host,
                    credential,
                    credential_user_id,
                    request_preview,
                    request_hash,
                    1 if is_streaming else 0,
                ),
            )
            record_id = int(cursor.lastrowid)
            if record_id % 100 == 0:
                connection.execute(
                    "DELETE FROM request_logs WHERE id NOT IN "
                    "(SELECT id FROM request_logs ORDER BY id DESC LIMIT ?)",
                    (MAX_RECORDS,),
                )
            return record_id

    def finish_request(
        self,
        record_id: int,
        status_code: int,
        latency_ms: int,
        usage: Optional[Dict[str, Any]] = None,
        upstream_attempts: int = 1,
        estimated_input_tokens: Optional[int] = None,
        estimated_output_tokens: Optional[int] = None,
        response_preview: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        usage = usage or {}
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        total_tokens = usage.get("total_tokens")
        token_estimated = 0
        if input_tokens is None and estimated_input_tokens is not None:
            input_tokens = estimated_input_tokens
            token_estimated = 1
        if output_tokens is None and estimated_output_tokens is not None:
            output_tokens = estimated_output_tokens
            token_estimated = 1
        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            total_tokens = int(input_tokens or 0) + int(output_tokens or 0)

        completed_at = int(time.time() * 1000)
        status = "success" if 200 <= status_code < 400 and not error else "error"
        safe_error = _redact_secrets(_compact_text(error))[:500] if error else None
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE request_logs
                SET completed_at = ?, status_code = ?, status = ?, latency_ms = ?,
                    upstream_attempts = ?, input_tokens = ?, output_tokens = ?, total_tokens = ?,
                    token_estimated = ?, response_preview = ?, error = ?
                WHERE id = ?
                """,
                (
                    completed_at,
                    status_code,
                    status,
                    latency_ms,
                    max(1, upstream_attempts),
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    token_estimated,
                    response_preview,
                    safe_error,
                    record_id,
                ),
            )

    def list_requests(
        self,
        page: int = 1,
        page_size: int = 20,
        days: Optional[int] = None,
        model: Optional[str] = None,
        client: Optional[str] = None,
        credential: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        page = max(1, page)
        page_size = min(100, max(1, page_size))
        clauses: List[str] = []
        params: List[Any] = []
        if days and days > 0:
            clauses.append("created_at >= ?")
            params.append(int((time.time() - days * 86400) * 1000))
        if model:
            clauses.append("model = ?")
            params.append(model)
        if client:
            clauses.append("client = ?")
            params.append(client)
        if credential:
            clauses.append("credential = ?")
            params.append(credential)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._lock, self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM request_logs{where_sql}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM request_logs{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [page_size, (page - 1) * page_size],
            ).fetchall()
            models = [row[0] for row in connection.execute(
                "SELECT DISTINCT model FROM request_logs ORDER BY model"
            ).fetchall()]
            credentials = [row[0] for row in connection.execute(
                "SELECT DISTINCT credential FROM request_logs "
                "WHERE credential IS NOT NULL ORDER BY credential"
            ).fetchall()]
            clients = [row[0] for row in connection.execute(
                "SELECT DISTINCT client FROM request_logs ORDER BY client"
            ).fetchall()]

        return {
            "items": [dict(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
            "filters": {
                "models": models,
                "clients": clients,
                "credentials": credentials,
            },
        }

    def get_aggregate_stats(self) -> Dict[str, Dict[str, int]]:
        with self._lock, self._connect() as connection:
            model_rows = connection.execute(
                "SELECT model, COUNT(*) FROM request_logs GROUP BY model"
            ).fetchall()
            credential_rows = connection.execute(
                "SELECT credential, COUNT(*) FROM request_logs "
                "WHERE credential IS NOT NULL GROUP BY credential"
            ).fetchall()
            client_rows = connection.execute(
                "SELECT client, COUNT(*) FROM request_logs GROUP BY client"
            ).fetchall()
        return {
            "model_usage": {row[0]: row[1] for row in model_rows},
            "credential_usage": {row[0]: row[1] for row in credential_rows},
            "client_usage": {row[0]: row[1] for row in client_rows},
        }

    def export_csv(
        self,
        days: Optional[int] = None,
        model: Optional[str] = None,
        client: Optional[str] = None,
        credential: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Tuple[str, str]:
        clauses: List[str] = []
        params: List[Any] = []
        if days and days > 0:
            clauses.append("created_at >= ?")
            params.append(int((time.time() - days * 86400) * 1000))
        if model:
            clauses.append("model = ?")
            params.append(model)
        if client:
            clauses.append("client = ?")
            params.append(client)
        if credential:
            clauses.append("credential = ?")
            params.append(credential)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM request_logs{where_sql} ORDER BY created_at DESC LIMIT ?",
                params + [MAX_RECORDS],
            ).fetchall()

        output = io.StringIO()
        output.write("\ufeff")
        fieldnames = [
            "created_at", "completed_at", "status", "status_code", "model",
            "client", "client_detail", "client_host", "credential", "credential_user_id", "endpoint",
            "request_id", "conversation_id", "request_hash", "request_preview", "response_preview",
            "is_streaming", "latency_ms", "upstream_attempts", "input_tokens", "output_tokens", "total_tokens",
            "token_estimated", "error",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
        filename = time.strftime("codebuddy-request-logs-%Y%m%d-%H%M%S.csv")
        return filename, output.getvalue()


request_log_manager = RequestLogManager()
