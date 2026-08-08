"""
Dashboard for the weather MCP server (optional stretch).

Shows what the agent has actually been asking for: recent tool calls, which
tools get used, how often calls fail, and how slow they are. The MCP server
writes one row per tool call to Lakebase (see mcp_server/call_log.py); this app
reads them back.

Deployed as its OWN Databricks App, separate from the MCP server, mirroring the
reference repo's mcp_server/ + dashboard/ split. The two apps share no memory,
which is precisely why the call log lives in Lakebase rather than in a process
variable.

call_log.py is a copy of the MCP server's module rather than an import: a
Databricks App is deployed from a single source path, so a sibling directory is
not importable at runtime. The alternative - a shared package published to the
workspace - is more machinery than a 100-line module justifies.

Run locally:
    python app.py
"""

import logging
import os

from flask import Flask, jsonify, render_template

import call_log

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-dashboard")

app = Flask(__name__)


@app.route("/healthz")
def healthz():
    """Liveness probe. Deliberately does not touch Lakebase, so a database
    problem cannot make the platform think the container is dead."""
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    """Render recent agent activity.

    A Lakebase failure renders an explanatory page rather than a 500: the most
    likely cause is a missing secret ACL on this app's service principal, and a
    stack trace in the browser would not say so.
    """
    try:
        return render_template(
            "index.html",
            calls=call_log.recent(int(os.environ.get("DASHBOARD_LIMIT", "50"))),
            stats=call_log.stats(),
            error=None,
        )
    except Exception as exc:
        logger.exception("Could not read the call log")
        return render_template(
            "index.html", calls=[], stats={}, error=str(exc)
        )


@app.route("/api/calls")
def api_calls():
    """JSON feed of the same data, for anything that would rather not scrape HTML."""
    try:
        return jsonify({"calls": call_log.recent(200), "stats": call_log.stats()})
    except Exception as exc:
        logger.exception("Could not read the call log")
        return jsonify({"error": str(exc)}), 503


if __name__ == "__main__":
    port = int(os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", 8001)))
    # debug defaults off: app.yaml runs this same entrypoint in the deployed
    # app, and Flask's debug mode exposes the Werkzeug console to anyone who can
    # trigger a 500.
    debug = os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
    app.run(debug=debug, host="0.0.0.0", port=port)
