"""Read-only projections for the management console."""


class ConsoleReadModel:
    def __init__(self, logs, diagnostics):
        self.logs = logs
        self.diagnostics = diagnostics

    def detail(self, record_id):
        with self.logs.connect() as db:
            row = db.execute(
                "SELECT * FROM request_logs WHERE id=?", (record_id,)
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "attempts": self.diagnostics.attempts(record_id)}

    def summary(self, query):
        where, values = query.sql()
        with self.logs.connect() as db:
            totals = dict(
                db.execute(
                    "SELECT COUNT(*) AS requests, "
                    "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending, "
                    "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS successes, "
                    "AVG(CASE WHEN status!='pending' THEN latency_ms END) AS average_latency_ms, "
                    "SUM(upstream_attempts) AS upstream_attempts, SUM(total_tokens) AS tokens "
                    "FROM request_logs" + where,
                    values,
                ).fetchone()
            )
            groups = {}
            for field, label in (("model", "models"), ("client", "clients")):
                groups[label] = dict(
                    db.execute(
                        "SELECT "
                        + field
                        + ", COUNT(*) FROM request_logs"
                        + where
                        + " GROUP BY "
                        + field,
                        values,
                    ).fetchall()
                )
        requests, pending = totals["requests"], totals["pending"] or 0
        completed = requests - pending
        average = totals["average_latency_ms"]
        return {
            "requests": requests,
            "pending": pending,
            "success_rate": (
                round((totals["successes"] or 0) / completed * 100, 1)
                if completed
                else None
            ),
            "average_latency_ms": round(average) if average is not None else None,
            "upstream_attempts": totals["upstream_attempts"] or 0,
            "tokens": totals["tokens"] or 0,
            **groups,
        }
