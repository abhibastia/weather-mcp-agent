"""
Best-effort logging of MCP tool calls to Lakebase, for the dashboard app.

The dashboard is a separate Databricks App, so it cannot read the MCP server's
memory. Lakebase (the workspace's managed Postgres) is the shared surface: this
module writes one row per tool call, and dashboard/app.py reads them back.

EVERY FAILURE HERE IS SWALLOWED, ON PURPOSE
-------------------------------------------
Logging is observability, not function. If the secret is missing, the ACL was
never granted, or Postgres is unreachable, the weather tools must still answer -
an agent losing the ability to report the weather because a telemetry insert
failed would be a strictly worse outcome than losing the telemetry. So the
connection is resolved lazily, failures are logged once and then suppressed, and
`record()` never raises.
"""

import base64
import json
import logging
import os
import threading

logger = logging.getLogger("call-log")

_SCOPE = os.environ.get("LAKEBASE_SECRET_SCOPE", "database")
_KEY = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")
TABLE = os.environ.get("MCP_CALL_LOG_TABLE", "mcp_tool_calls")

# Set MCP_CALL_LOG_ENABLED=false to run the server with no Lakebase dependency
# at all - useful locally, and the documented fallback if the dashboard is not
# deployed.
ENABLED = os.environ.get("MCP_CALL_LOG_ENABLED", "true").lower() not in {"false", "0", "no"}

_url: str | None = None
_lock = threading.Lock()
_broken = False  # set after the first hard failure, so we warn once not per call


def _lakebase_url() -> str | None:
    """Resolve the Postgres URL from the Databricks secret scope, once."""
    global _url, _broken
    if _url is not None:
        return _url
    with _lock:
        if _url is None:
            explicit = os.environ.get("LAKEBASE_URL")
            if explicit:
                _url = explicit
            else:
                from databricks.sdk import WorkspaceClient

                secret = WorkspaceClient().secrets.get_secret(scope=_SCOPE, key=_KEY)
                _url = base64.b64decode(secret.value).decode("utf-8")
    return _url


def record(tool_name: str, arguments: dict, outcome: str,
           summary: str | None = None, duration_ms: int | None = None) -> None:
    """Write one tool-call row. Never raises, never blocks the caller's result."""
    global _broken
    if not ENABLED or _broken:
        return
    try:
        import psycopg2

        location = arguments.get("location")
        if location is None and isinstance(arguments.get("locations"), list):
            location = ", ".join(map(str, arguments["locations"]))

        with psycopg2.connect(_lakebase_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {TABLE}
                        (tool_name, arguments, location, outcome, summary, duration_ms)
                        VALUES (%s, %s, %s, %s, %s, %s)""",
                    (tool_name, json.dumps(arguments, default=str),
                     str(location) if location is not None else None,
                     outcome, summary, duration_ms),
                )
                conn.commit()
    except Exception as exc:
        # Warn once. A per-call warning would flood the app log with the same
        # line and obscure real errors.
        _broken = True
        logger.warning(
            "Tool-call logging disabled for this process: %s. Weather tools are "
            "unaffected; only the dashboard's history is.", exc
        )


def recent(limit: int = 50) -> list[dict]:
    """Read recent tool calls, newest first. Used by the dashboard app."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    limit = max(1, min(int(limit), 500))
    with psycopg2.connect(_lakebase_url(), cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, called_at, tool_name, arguments, location,
                           outcome, summary, duration_ms
                    FROM {TABLE} ORDER BY called_at DESC LIMIT %s""",
                (limit,),
            )
            return cur.fetchall()


def stats() -> dict:
    """Aggregate counts for the dashboard's summary cards."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(_lakebase_url(), cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT count(*)                                        AS total_calls,
                       count(*) FILTER (WHERE outcome = 'ok')          AS ok_calls,
                       count(*) FILTER (WHERE outcome <> 'ok')         AS error_calls,
                       count(DISTINCT location)                        AS locations,
                       round(avg(duration_ms))                         AS avg_ms
                FROM {TABLE}""")
            summary = dict(cur.fetchone() or {})
            cur.execute(f"""
                SELECT tool_name, count(*) AS calls
                FROM {TABLE} GROUP BY tool_name ORDER BY calls DESC""")
            summary["by_tool"] = cur.fetchall()
            return summary
