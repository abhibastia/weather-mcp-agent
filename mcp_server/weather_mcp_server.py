"""
Weather-prediction MCP server.

Exposes weather tools over MCP (Model Context Protocol) so a Databricks Agent
Bricks agent - or any MCP client - can call them like any other tool:

    - get_current_weather(location)
    - get_forecast(location, days)
    - should_i_bring_an_umbrella(location)   <- applies judgment, not a passthrough
    - get_severe_weather_alerts(location)    <- second data source (NWS)

Every tool here is deliberately thin. All HTTP calls and response parsing live
in weather_broker.py; these functions validate input, call one broker function,
and shape the result. That split means the weather logic can be tested with a
plain `python -c` call, with no MCP client, no agent, and no deployed app.

Data source: Open-Meteo (no API key, no signup) for conditions and forecasts,
with the National Weather Service API layered in for severe-weather alerts.
Because Open-Meteo needs no credential, this server has no secrets to manage -
see README.md for the secret pattern to follow if you swap in a keyed API.

Deploy this as its own Databricks App (same app.yaml + FastMCP entrypoint
pattern as the reference mcp_server/, documented at
https://docs.databricks.com/aws/en/agents/mcp-tools/custom-mcp) so an Agent
Bricks agent can register its URL as an external MCP server.

Run locally:
    python weather_mcp_server.py
"""

import html
import logging
import os

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

import weather_broker
from weather_broker import UnknownLocationError, WeatherAPIError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-mcp-server")

mcp = FastMCP("weather-prediction")

# Thresholds for should_i_bring_an_umbrella. Named constants rather than magic
# numbers inline, because the tool's docstring quotes them to the agent and the
# two must not drift apart.
UMBRELLA_PROBABILITY_PCT = 40      # chance of precipitation worth acting on
UMBRELLA_ACCUMULATION_MM = 1.0     # enough rain to actually get you wet
SOAKING_ACCUMULATION_MM = 10.0     # "an umbrella won't be enough" territory
WINDY_KMH = 35.0                   # above this an umbrella is a liability


def _error(exc: Exception, location: str) -> dict:
    """Turn an exception into a dict the agent can reason about.

    Returning a structured error rather than raising keeps a bad location from
    surfacing as a stack trace, and gives the model something it can act on -
    it can ask the user to clarify instead of guessing the weather.
    """
    if isinstance(exc, UnknownLocationError):
        return {
            "error": "unknown_location",
            "message": str(exc),
            "requested_location": location,
            "suggestion": "Ask the user to confirm the city, or provide 'lat,lon'.",
        }
    if isinstance(exc, WeatherAPIError):
        return {
            "error": "weather_api_unavailable",
            "message": str(exc),
            "requested_location": location,
            "suggestion": "Tell the user the weather service is unavailable. Do not guess.",
        }
    logger.exception("Unexpected failure for location=%r", location)
    return {
        "error": "unexpected_error",
        "message": str(exc),
        "requested_location": location,
        "suggestion": "Tell the user the request failed. Do not guess the weather.",
    }


@mcp.tool
def get_current_weather(location: str) -> dict:
    """
    Get current weather conditions for a location.

    Args:
        location: City name ("Chicago"), city with region ("Austin, Texas"),
            or coordinates as "lat,lon" ("41.88,-87.63").

    Returns:
        A dict with resolved_location, observed_at, temperature, feels_like,
        humidity_pct, precipitation, wind_speed, wind_gusts, conditions
        (plain-language description) and units. On failure, a dict with an
        "error" key and a "suggestion" describing how to respond.
    """
    try:
        return weather_broker.get_current_conditions(location)
    except Exception as exc:
        return _error(exc, location)


@mcp.tool
def get_forecast(location: str, days: int = 3) -> dict:
    """
    Get a multi-day daily forecast for a location.

    Args:
        location: City name, city with region, or "lat,lon" coordinates.
        days: Number of days to forecast, 1-16. Defaults to 3. Values outside
            that range are clamped rather than rejected.

    Returns:
        A dict with resolved_location, units, and a "periods" list. Each period
        has date, conditions, temp_max, temp_min, precipitation_sum,
        precipitation_probability_max and wind_speed_max. On failure, a dict
        with an "error" key.
    """
    try:
        return weather_broker.get_daily_forecast(location, days)
    except Exception as exc:
        return _error(exc, location)


@mcp.tool
def should_i_bring_an_umbrella(location: str) -> dict:
    """
    Decide whether someone should take an umbrella today, with reasoning.

    This is a judgment tool, not a passthrough: it reads today's forecast and
    applies explicit thresholds rather than handing raw numbers to the model.

    Decision rules, in order:
      - Wind gusts above 35 km/h  -> "no", an umbrella will invert; suggest a
        hooded waterproof instead. Wind overrides rain, because a broken
        umbrella is worse than no umbrella.
      - Precipitation above 10 mm -> "yes, and expect to get wet anyway";
        an umbrella alone is not enough at that volume.
      - Chance of precipitation >= 40% OR expected accumulation >= 1.0 mm
        -> "yes".
      - Otherwise -> "no".

    The 40% threshold is the usual forecasting convention for "likely enough to
    plan around"; 1.0 mm is roughly the point where rain stops being a light
    mist and starts being noticeable. Both are stated in the response so the
    agent can explain the recommendation rather than asserting it.

    Args:
        location: City name, city with region, or "lat,lon" coordinates.

    Returns:
        A dict with recommendation ("yes"/"no"), confidence, reason (a sentence
        the agent can quote), the thresholds applied, and the underlying
        forecast figures it judged. On failure, a dict with an "error" key.
    """
    try:
        forecast = weather_broker.get_daily_forecast(location, days=1)
        today = forecast["periods"][0]

        probability = today.get("precipitation_probability_max")
        accumulation = today.get("precipitation_sum")
        gusts = today.get("wind_speed_max")

        prob = float(probability) if probability is not None else 0.0
        accum = float(accumulation) if accumulation is not None else 0.0
        wind = float(gusts) if gusts is not None else 0.0

        if wind >= WINDY_KMH and (prob >= UMBRELLA_PROBABILITY_PCT or accum >= UMBRELLA_ACCUMULATION_MM):
            recommendation, confidence = "no", "high"
            reason = (
                f"Rain is likely ({prob:.0f}% chance, {accum:.1f} mm expected), but winds "
                f"reach {wind:.0f} km/h - above the {WINDY_KMH:.0f} km/h point where an "
                "umbrella becomes a liability. A hooded waterproof is the better call."
            )
        elif accum >= SOAKING_ACCUMULATION_MM:
            recommendation, confidence = "yes", "high"
            reason = (
                f"Heavy rain expected ({accum:.1f} mm, above the "
                f"{SOAKING_ACCUMULATION_MM:.0f} mm soaking threshold). Take an umbrella, "
                "but expect to get wet regardless - waterproof shoes would help."
            )
        elif prob >= UMBRELLA_PROBABILITY_PCT or accum >= UMBRELLA_ACCUMULATION_MM:
            recommendation, confidence = "yes", "medium" if prob < 70 else "high"
            reason = (
                f"{prob:.0f}% chance of precipitation with {accum:.1f} mm expected, at or "
                f"above the {UMBRELLA_PROBABILITY_PCT}% / "
                f"{UMBRELLA_ACCUMULATION_MM:.1f} mm thresholds for taking an umbrella."
            )
        else:
            recommendation, confidence = "no", "high" if prob < 20 else "medium"
            reason = (
                f"Only a {prob:.0f}% chance of precipitation with {accum:.1f} mm expected, "
                f"below the {UMBRELLA_PROBABILITY_PCT}% / "
                f"{UMBRELLA_ACCUMULATION_MM:.1f} mm thresholds. An umbrella is unnecessary."
            )

        return {
            "requested_location": location,
            "resolved_location": forecast["resolved_location"],
            "date": today["date"],
            "recommendation": recommendation,
            "confidence": confidence,
            "reason": reason,
            "thresholds_applied": {
                "precipitation_probability_pct": UMBRELLA_PROBABILITY_PCT,
                "precipitation_accumulation_mm": UMBRELLA_ACCUMULATION_MM,
                "soaking_accumulation_mm": SOAKING_ACCUMULATION_MM,
                "high_wind_kmh": WINDY_KMH,
            },
            "forecast_used": {
                "conditions": today.get("conditions"),
                "precipitation_probability_max": probability,
                "precipitation_sum": accumulation,
                "wind_speed_max": gusts,
                "temp_max": today.get("temp_max"),
                "temp_min": today.get("temp_min"),
            },
            "units": forecast["units"],
            "source": "open-meteo",
        }
    except Exception as exc:
        return _error(exc, location)


@mcp.tool
def get_severe_weather_alerts(location: str) -> dict:
    """
    Get active severe-weather alerts for a location (United States only).

    Uses the National Weather Service, a second data source, because Open-Meteo
    has no alerts product. Outside the US this returns an empty alert list with
    coverage "unavailable" - which means "no data", NOT "no danger". Say so
    rather than implying the area is clear.

    Args:
        location: City name, city with region, or "lat,lon" coordinates.

    Returns:
        A dict with resolved_location, alert_count, coverage ("us" or
        "unavailable") and an "alerts" list. Each alert has event, severity,
        urgency, headline, description, instruction, effective, expires and
        area. On failure, a dict with an "error" key.
    """
    try:
        return weather_broker.get_active_alerts(location)
    except Exception as exc:
        return _error(exc, location)


# --------------------------------------------------------------------------
# Human-facing routes
#
# FastMCP serves the protocol at /mcp and defines nothing at /, so opening the
# app URL in a browser returns a bare "Not Found". That is correct behaviour for
# an MCP server, but it looks broken to anyone checking whether the deployment
# worked. These two routes exist purely so a human (or a screenshot) can confirm
# the server is alive and see which tools it advertises. Neither is part of the
# MCP surface, and the agent never calls them.
# --------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Liveness probe. Does not call any weather API."""
    return JSONResponse({"status": "ok", "server": "weather-prediction"})


@mcp.custom_route("/", methods=["GET"])
async def landing(request: Request) -> HTMLResponse:
    """Landing page listing the tools this server exposes."""
    # Read the tool list off the registry rather than hard-coding it, so the
    # page cannot drift out of sync with what is actually registered.
    tools = await mcp.list_tools()
    rows = "".join(
        f"<tr><td><code>{html.escape(tool.name)}</code></td>"
        f"<td>{html.escape(((tool.description or '').strip().splitlines() or [''])[0])}</td></tr>"
        for tool in sorted(tools, key=lambda t: t.name)
    )
    # Behind the Databricks Apps proxy, request.url is the *internal* address
    # (localhost:8000), which is useless to anyone copying it into the external
    # MCP registration form. Prefer the forwarded headers the proxy sets.
    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_proto = request.headers.get("x-forwarded-proto", "https")
    if forwarded_host:
        endpoint = html.escape(f"{forwarded_proto}://{forwarded_host}/mcp")
    else:
        endpoint = html.escape(str(request.url.replace(path="/mcp", query="")))
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weather Prediction MCP Server</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.6 system-ui, sans-serif; max-width: 46rem;
         margin: 3rem auto; padding: 0 1.25rem; }}
  h1 {{ font-size: 1.5rem; margin-bottom: .25rem; }}
  .sub {{ opacity: .7; margin-top: 0; }}
  .ok {{ display:inline-block; padding:.15rem .6rem; border-radius:1rem;
         background:#137333; color:#fff; font-size:.8rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  td, th {{ text-align: left; padding: .5rem .4rem;
            border-bottom: 1px solid rgba(128,128,128,.35); vertical-align: top; }}
  code {{ font-size: .9em; }}
  .box {{ padding: .75rem 1rem; border: 1px solid rgba(128,128,128,.35);
          border-radius: .5rem; overflow-x: auto; }}
</style></head><body>
<h1>Weather Prediction MCP Server <span class="ok">running</span></h1>
<p class="sub">Model Context Protocol server exposing weather tools to a Databricks agent.</p>

<h2>MCP endpoint</h2>
<div class="box"><code>{endpoint}</code></div>
<p>Register that URL as an external MCP server (streamable HTTP). This page is
for humans; agents talk to <code>/mcp</code>.</p>

<h2>Tools ({len(tools)})</h2>
<table><tr><th>Tool</th><th>What it does</th></tr>{rows}</table>

<h2>Data sources</h2>
<p>Open-Meteo for conditions and forecasts (no API key required).
National Weather Service for severe-weather alerts (United States only).</p>
</body></html>"""
    )


def build_app():
    """Build the ASGI app, serving MCP on every path the gateway might call.

    WHY MORE THAN ONE MOUNT
    -----------------------
    Databricks registers a Databricks-App-hosted MCP server by storing the
    app URL with "/mcp" already appended, then appends "/mcp" again when it
    calls the server. The result is a request to "/mcp/mcp", which a
    single-mount server answers with a 404 whose body is the bare string
    "Not Found". The gateway tries to JSON-parse that and reports:

        Failed to parse MCP initialize response ...
        Unrecognized token 'Not' ... Response: Not Found

    which looks like a broken server but is really a path mismatch. Mounting
    the same MCP app at "/mcp/mcp" and "/mcp" makes the server correct under
    either convention, and costs nothing: it is one ASGI app behind two routes.

    "/mcp/mcp" is registered first because Starlette matches in order and
    "/mcp" would otherwise swallow it.
    """
    # Starlette Mounts were tried first and rejected: mounting the MCP app at
    # two prefixes makes "/mcp" answer 307 (redirect to "/mcp/") and
    # "/mcp/mcp" answer 404, because a Mount matches its own prefix and then
    # fails to match the inner route. A gateway that does not follow redirects
    # sees a failure either way. Rewriting the path before routing is exact:
    # one app, one route, no redirects.
    mcp_asgi = mcp.http_app(path="/mcp")

    async def rewrite(scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").rstrip("/") == "/mcp/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp"
            scope["raw_path"] = b"/mcp"
        await mcp_asgi(scope, receive, send)

    # Carry the lifespan through: the MCP app owns the streamable-HTTP session
    # manager, which its lifespan starts. Losing it yields a server that 500s
    # on the first request rather than failing loudly at startup.
    rewrite.lifespan = mcp_asgi.lifespan
    return rewrite


if __name__ == "__main__":
    import uvicorn

    # Databricks Apps route external HTTP traffic to this port via app.yaml;
    # streamable-http is the transport Databricks' MCP client/gateway expects
    # (see the "Host your own MCP" doc linked in the module docstring above).
    port = int(os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", 8000)))
    logger.info("Starting weather MCP server on port %d", port)
    uvicorn.run(build_app(), host="0.0.0.0", port=port)
