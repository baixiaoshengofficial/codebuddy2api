"""One filter definition shared by the request list, export and summary."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
import time


@dataclass(frozen=True)
class RequestFilter:
    start: Optional[int] = None
    end: Optional[int] = None
    search: str = ""
    model: str = ""
    client: str = ""
    credential: str = ""
    status: str = ""

    def sql(self):
        clauses, values = [], []
        for column, operator, value in (
            ("created_at", ">=", self.start),
            ("created_at", "<", self.end),
            ("model", "=", self.model),
            ("client", "=", self.client),
            ("credential", "=", self.credential),
            ("status", "=", self.status),
        ):
            if value is not None and value != "":
                clauses.append(column + " " + operator + " ?")
                values.append(value)
        if self.search:
            clauses.append(
                "(instr(request_id, ?) > 0 OR instr(request_preview, ?) > 0)"
            )
            values.extend([self.search, self.search])
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), values


def time_range(period="today", start=None, end=None, offset=480, now=None):
    now = time.time() if now is None else now
    if period == "custom":
        if start is None or end is None or start >= end:
            raise ValueError("请选择有效的开始和结束时间")
        if end - start > 366 * 86400000:
            raise ValueError("自定义范围不能超过 366 天")
        return start, end
    if period == "today":
        local = datetime.fromtimestamp(now, timezone(timedelta(minutes=offset)))
        start = int(
            local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        )
    elif period in ("24h", "7d", "30d"):
        seconds = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}[period]
        start = int((now - seconds) * 1000)
    else:
        raise ValueError("不支持的时间范围")
    return start, int(now * 1000) + 1
