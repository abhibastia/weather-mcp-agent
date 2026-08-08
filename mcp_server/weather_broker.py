"""
Weather data adapter backing the weather MCP server.

Every HTTP call and every bit of response parsing lives in this module. The
MCP tools in weather_mcp_server.py stay thin: they validate arguments, call one
function here, and return the dict they get back. Nothing in this file knows
that MCP exists, which is what makes it testable without an agent, a server, or
a Databricks App.

DATA SOURCES
------------
Open-Meteo (https://open-meteo.com) is the primary source: no signup, no API
key, ~10k calls/day. Because it needs no credential, the whole pipeline can be
built and demonstrated with no secret management at all - there is no key to
leak, rotate, or grant a service principal access to.

The National Weather Service API (https://api.weather.gov) is layered in as a
second source for severe-weather alerts, which Open-Meteo does not provide. NWS
is US-only and requires a descriptive User-Agent; it is used *only* for alerts,
so a non-US location degrades to "no alerts available" rather than failing.

ERROR HANDLING
--------------
Callers get exceptions with plain-language messages (UnknownLocationError,
WeatherAPIError). The MCP layer turns those into a clean error dict so the agent
can say "I couldn't resolve that location, can you be more specific?" instead of
surfacing a stack trace or, worse, inventing the weather.
"""

import datetime
import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger("weather-broker")

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
# Separate host: the archive is a different dataset (ERA5 reanalysis), not the
# forecast model, and the forecast endpoint will not serve past dates.
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
NWS_BASE_URL = os.environ.get("NWS_API_BASE_URL", "https://api.weather.gov").rstrip("/")

# api.weather.gov asks callers to identify themselves and may reject anonymous
# traffic. Deliberately not defaulted to a real address - set NWS_USER_AGENT in
# app.yaml rather than committing a personal email to the repo.
NWS_USER_AGENT = os.environ.get(
    "NWS_USER_AGENT", "weather-mcp-agent (contact-not-configured)"
)

DEFAULT_TIMEOUT = 20
MAX_FORECAST_DAYS = 16   # Open-Meteo's own ceiling
ARCHIVE_LAG_DAYS = 5     # ERA5 reanalysis trails real time by ~5 days
MAX_COMPARE_LOCATIONS = 8


class UnknownLocationError(ValueError):
    """The location string could not be resolved to coordinates."""


class WeatherAPIError(RuntimeError):
    """An upstream weather API failed or returned something unusable."""


# "41.88,-87.63" - lets a caller bypass geocoding entirely.
_LATLON_RE = re.compile(
    r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$"
)

# WMO weather interpretation codes. Open-Meteo returns an integer; an agent
# answering "what's it like outside" needs words, and mapping here keeps the
# tool layer free of lookup tables.
WMO_CODES: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snowfall",
    73: "moderate snowfall",
    75: "heavy snowfall",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def describe_weather_code(code: Any) -> str:
    """Translate a WMO code into a human phrase, without inventing one."""
    try:
        return WMO_CODES.get(int(code), f"unknown conditions (WMO code {code})")
    except (TypeError, ValueError):
        return "unknown conditions"


def _get(url: str, params: dict, headers: dict | None = None) -> dict:
    """Single choke point for outbound HTTP, so timeouts and error shape are uniform."""
    try:
        response = requests.get(
            url, params=params, headers=headers or {}, timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except requests.Timeout as exc:
        raise WeatherAPIError(f"Weather API timed out after {DEFAULT_TIMEOUT}s") from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise WeatherAPIError(f"Weather API returned HTTP {status}") from exc
    except requests.RequestException as exc:
        raise WeatherAPIError(f"Weather API request failed: {exc}") from exc
    except ValueError as exc:
        raise WeatherAPIError("Weather API returned a non-JSON response") from exc


def resolve_location(location: str) -> dict:
    """Resolve a place name to coordinates via Open-Meteo's geocoding API.

    Accepts a raw "lat,lon" string as an escape hatch so the agent can still be
    useful for places the geocoder does not know.

    Args:
        location: City name ("Chicago"), city and region ("Chicago, Illinois"),
            or a "lat,lon" pair ("41.88,-87.63").

    Returns:
        dict with name, country, latitude, longitude, timezone.

    Raises:
        UnknownLocationError: if the string resolves to nothing.
    """
    if not isinstance(location, str) or not location.strip():
        raise UnknownLocationError("Location must be a non-empty string")
    location = location.strip()

    match = _LATLON_RE.match(location)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise UnknownLocationError(
                f"Coordinates out of range: {lat}, {lon}"
            )
        return {
            "name": f"{lat},{lon}",
            "country": None,
            "latitude": lat,
            "longitude": lon,
            "timezone": "auto",
        }

    # Open-Meteo's geocoder matches on the city name; a trailing region hint
    # ("Chicago, IL") is not understood, so search on the first component and
    # let the caller see which match came back in the `resolved_location` field.
    query = location.split(",")[0].strip()
    payload = _get(
        OPEN_METEO_GEOCODING_URL,
        {"name": query, "count": 1, "language": "en", "format": "json"},
    )
    results = payload.get("results") or []
    if not results:
        raise UnknownLocationError(
            f"Could not find a place called {location!r}. "
            "Try a larger nearby city, or pass coordinates as 'lat,lon'."
        )

    hit = results[0]
    return {
        "name": hit.get("name"),
        "country": hit.get("country"),
        "admin1": hit.get("admin1"),
        "latitude": hit["latitude"],
        "longitude": hit["longitude"],
        "timezone": hit.get("timezone", "auto"),
    }


def get_current_conditions(location: str) -> dict:
    """Fetch current conditions for a location from Open-Meteo."""
    place = resolve_location(location)
    payload = _get(
        OPEN_METEO_FORECAST_URL,
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": ",".join(
                [
                    "temperature_2m",
                    "apparent_temperature",
                    "relative_humidity_2m",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                    "wind_gusts_10m",
                ]
            ),
            "timezone": "auto",
        },
    )

    current = payload.get("current") or {}
    units = payload.get("current_units") or {}
    if not current:
        raise WeatherAPIError("Open-Meteo returned no current conditions")

    return {
        "requested_location": location,
        "resolved_location": _label(place),
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "observed_at": current.get("time"),
        "timezone": payload.get("timezone"),
        "temperature": current.get("temperature_2m"),
        "feels_like": current.get("apparent_temperature"),
        "humidity_pct": current.get("relative_humidity_2m"),
        "precipitation": current.get("precipitation"),
        "wind_speed": current.get("wind_speed_10m"),
        "wind_gusts": current.get("wind_gusts_10m"),
        "conditions": describe_weather_code(current.get("weather_code")),
        "units": {
            "temperature": units.get("temperature_2m", "°C"),
            "wind_speed": units.get("wind_speed_10m", "km/h"),
            "precipitation": units.get("precipitation", "mm"),
        },
        "source": "open-meteo",
    }


def get_daily_forecast(location: str, days: int = 3) -> dict:
    """Fetch a multi-day daily forecast for a location from Open-Meteo."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise ValueError(f"days must be an integer, got {days!r}")
    days = max(1, min(days, MAX_FORECAST_DAYS))

    place = resolve_location(location)
    payload = _get(
        OPEN_METEO_FORECAST_URL,
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "daily": ",".join(
                [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_sum",
                    "precipitation_probability_max",
                    "wind_speed_10m_max",
                ]
            ),
            "forecast_days": days,
            "timezone": "auto",
        },
    )

    daily = payload.get("daily") or {}
    units = payload.get("daily_units") or {}
    dates = daily.get("time") or []
    if not dates:
        raise WeatherAPIError("Open-Meteo returned no forecast data")

    def col(name: str) -> list:
        # Open-Meteo returns parallel arrays; a short column would silently
        # misalign days, so pad rather than zip-truncate.
        values = daily.get(name) or []
        return list(values) + [None] * (len(dates) - len(values))

    periods = [
        {
            "date": date,
            "conditions": describe_weather_code(col("weather_code")[i]),
            "temp_max": col("temperature_2m_max")[i],
            "temp_min": col("temperature_2m_min")[i],
            "precipitation_sum": col("precipitation_sum")[i],
            "precipitation_probability_max": col("precipitation_probability_max")[i],
            "wind_speed_max": col("wind_speed_10m_max")[i],
        }
        for i, date in enumerate(dates)
    ]

    return {
        "requested_location": location,
        "resolved_location": _label(place),
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "timezone": payload.get("timezone"),
        "days": len(periods),
        "units": {
            "temperature": units.get("temperature_2m_max", "°C"),
            "precipitation": units.get("precipitation_sum", "mm"),
            "wind_speed": units.get("wind_speed_10m_max", "km/h"),
        },
        "periods": periods,
        "source": "open-meteo",
    }


def get_historical_weather(location: str, date: str) -> dict:
    """Fetch observed weather for a past date from Open-Meteo's archive.

    Args:
        location: City name, city with region, or "lat,lon".
        date: Calendar date as YYYY-MM-DD.

    Raises:
        ValueError: if the date is malformed or not in the past.
        WeatherAPIError: if the archive has no data for that date.
    """
    try:
        requested = datetime.date.fromisoformat(str(date).strip())
    except (TypeError, ValueError):
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")

    # The archive is a reanalysis product and lags real time by about five
    # days. Asking for yesterday returns an empty series rather than an error,
    # which would surface to the agent as "no data" with no explanation - so
    # reject it here with a message that says what to do instead.
    today = datetime.date.today()
    if requested >= today:
        raise ValueError(
            f"{date} is not in the past. Use get_forecast for today or future dates."
        )
    if (today - requested).days < ARCHIVE_LAG_DAYS:
        raise ValueError(
            f"{date} is too recent for the archive, which lags about "
            f"{ARCHIVE_LAG_DAYS} days. Try a date before "
            f"{today - datetime.timedelta(days=ARCHIVE_LAG_DAYS)}."
        )

    place = resolve_location(location)
    payload = _get(
        OPEN_METEO_ARCHIVE_URL,
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "start_date": date,
            "end_date": date,
            "daily": ",".join(
                [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "temperature_2m_mean",
                    "precipitation_sum",
                    "wind_speed_10m_max",
                ]
            ),
            "timezone": "auto",
        },
    )

    daily = payload.get("daily") or {}
    units = payload.get("daily_units") or {}
    if not (daily.get("time") or []):
        raise WeatherAPIError(f"Archive returned no observations for {date}")

    def first(name: str):
        values = daily.get(name) or []
        return values[0] if values else None

    return {
        "requested_location": location,
        "resolved_location": _label(place),
        "date": date,
        "conditions": describe_weather_code(first("weather_code")),
        "temp_max": first("temperature_2m_max"),
        "temp_min": first("temperature_2m_min"),
        "temp_mean": first("temperature_2m_mean"),
        "precipitation_sum": first("precipitation_sum"),
        "wind_speed_max": first("wind_speed_10m_max"),
        "units": {
            "temperature": units.get("temperature_2m_max", "°C"),
            "precipitation": units.get("precipitation_sum", "mm"),
            "wind_speed": units.get("wind_speed_10m_max", "km/h"),
        },
        "source": "open-meteo-archive",
    }


def get_active_alerts(location: str) -> dict:
    """Fetch active NWS severe-weather alerts for a location (US only).

    Open-Meteo has no alerts product, so this is the second data source. A
    non-US location is not an error: NWS simply has nothing for it, and the
    agent should say so rather than implying the area is safe.
    """
    place = resolve_location(location)
    try:
        payload = _get(
            f"{NWS_BASE_URL}/alerts/active",
            {"point": f"{place['latitude']},{place['longitude']}"},
            headers={"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"},
        )
    except WeatherAPIError as exc:
        # NWS 404s outside its coverage area. Report coverage honestly instead
        # of letting the agent read an error as "no alerts".
        return {
            "requested_location": location,
            "resolved_location": _label(place),
            # alert_count is present on this path too, so callers can read it
            # unconditionally. Omitting it here made the response shape depend
            # on coverage, and a consumer doing response["alert_count"] would
            # crash on every non-US location.
            "alert_count": 0,
            "alerts": [],
            "coverage": "unavailable",
            "note": (
                "No alert data available for this location. The National "
                f"Weather Service covers the United States only. ({exc})"
            ),
            "source": "nws",
        }

    alerts = []
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        alerts.append(
            {
                "event": props.get("event"),
                "severity": props.get("severity"),
                "urgency": props.get("urgency"),
                "certainty": props.get("certainty"),
                "headline": props.get("headline"),
                "description": props.get("description"),
                "instruction": props.get("instruction"),
                "effective": props.get("effective"),
                "expires": props.get("expires"),
                "area": props.get("areaDesc"),
            }
        )

    return {
        "requested_location": location,
        "resolved_location": _label(place),
        "alert_count": len(alerts),
        "alerts": alerts,
        "coverage": "us",
        "source": "nws",
    }


def get_current_for_many(locations: list[str]) -> tuple[list[dict], list[dict]]:
    """Fetch current conditions for several locations at once.

    Returns (successes, failures). One bad location must not sink the whole
    comparison - the agent should still be able to compare the cities that did
    resolve, and say which one it could not.

    Fetches are sequential: Open-Meteo is rate-limited per IP, and at the
    handful of cities a comparison involves the added latency is smaller than
    the risk of tripping a 429.
    """
    successes, failures = [], []
    for location in locations[:MAX_COMPARE_LOCATIONS]:
        try:
            successes.append(get_current_conditions(location))
        except UnknownLocationError as exc:
            failures.append({"location": location, "error": "unknown_location",
                             "message": str(exc)})
        except WeatherAPIError as exc:
            failures.append({"location": location, "error": "weather_api_unavailable",
                             "message": str(exc)})
    return successes, failures


def _label(place: dict) -> str:
    """Human-readable name for whatever the geocoder actually matched.

    Surfaced back to the agent so a wrong match ("Paris, Texas" when the user
    meant France) is visible in the answer rather than silently wrong.
    """
    parts = [place.get("name"), place.get("admin1"), place.get("country")]
    return ", ".join(p for p in parts if p)
