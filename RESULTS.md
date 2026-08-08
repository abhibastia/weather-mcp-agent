# Agent demonstration — Weather Prediction MCP Server

| | |
|---|---|
| **Repository** | <https://github.com/abhibastia/weather-mcp-agent> |
| **MCP server App** | <https://mcp-server-weather-service-7474646797973312.aws.databricksapps.com> |
| **MCP endpoint** | `https://mcp-server-weather-service-7474646797973312.aws.databricksapps.com/mcp` |
| **Dashboard App** (stretch) | <https://weather-mcp-dashboard-7474646797973312.aws.databricksapps.com> |
| **Registered in** | Unity AI Gateway → MCPs → `mcp-server-weather-service` (status: **Active**) |
| **Agent** | Databricks AI Playground, `Meta Llama 3.3 70B Instruct`, MCP server attached as a tool, `SYSTEM_PROMPT.md` as the system prompt |
| **Tools exposed** | 6 — three required, three stretch |
| **Date** | 2026-08-09 |

Seven questions follow, transcribed verbatim from the Playground — the model's
reasoning, the tool call, the raw tool output, and the final answer. All seven come
from a single session against one configuration.

- **Q1–Q3** exercise the three required capabilities.
- **Q4–Q5** exercise the stretch tools (historical lookup, multi-city comparison).
- **Q6–Q7** exercise the **guardrails**, which is the part that is easy to get wrong.

Screenshot `4-agent-tool-call-and-answer.png` captures Q6 in the Playground UI.

---

## Q1. What's the weather like in Chicago right now?

**Reasoning**
> The user wants to know the current weather in Chicago, so I need to use the
> `get_current_weather` function to get the latest weather conditions.

**Tool call** — `get_current_weather`

```json
{ "location": "Chicago" }
```

**Tool output**

```json
{
  "requested_location": "Chicago",
  "resolved_location": "Chicago, Illinois, United States",
  "latitude": 41.85003,
  "longitude": -87.65005,
  "observed_at": "2026-08-08T17:45",
  "timezone": "America/Chicago",
  "temperature": 24.5,
  "feels_like": 26.4,
  "humidity_pct": 75,
  "precipitation": 0,
  "wind_speed": 12.3,
  "wind_gusts": 15.1,
  "conditions": "clear sky",
  "units": { "temperature": "°C", "wind_speed": "km/h", "precipitation": "mm" },
  "source": "open-meteo"
}
```

**Answer**
> The current weather in Chicago is a clear sky with a temperature of 24.5°C, feeling
> like 26.4°C. The humidity is 75%, and there is no precipitation. The wind speed is
> 12.3 km/h, with gusts of up to 15.1 km/h.

**What this demonstrates**

- Correct tool selection for a "right now" question.
- Every figure in the answer traces to the tool output. Nothing invented.

---

## Q2. Should I bring an umbrella in Mumbai today?

**Reasoning**
> The user is asking about bringing an umbrella in Mumbai, which implies they want to
> know if it will rain. This tool will provide a direct answer to that question.

**Tool call** — `should_i_bring_an_umbrella`

```json
{ "location": "Mumbai" }
```

**Tool output**

```json
{
  "requested_location": "Mumbai",
  "resolved_location": "Mumbai, Maharashtra, India",
  "date": "2026-08-09",
  "recommendation": "yes",
  "confidence": "high",
  "reason": "100% chance of precipitation with 6.0 mm expected, at or above the 40% / 1.0 mm thresholds for taking an umbrella.",
  "thresholds_applied": {
    "precipitation_probability_pct": 40,
    "precipitation_accumulation_mm": 1,
    "soaking_accumulation_mm": 10,
    "high_wind_kmh": 35
  },
  "forecast_used": {
    "conditions": "moderate drizzle",
    "precipitation_probability_max": 100,
    "precipitation_sum": 6,
    "wind_speed_max": 17.5,
    "temp_max": 29.1,
    "temp_min": 26
  },
  "units": { "temperature": "°C", "precipitation": "mm", "wind_speed": "km/h" },
  "source": "open-meteo"
}
```

**Answer**
> Yes, you should bring an umbrella in Mumbai today. The forecast shows a 100% chance of
> precipitation with 6.0 mm expected, which is above the thresholds for taking an umbrella.

**What this demonstrates**

- The prediction tool is **not a passthrough**. It returns a verdict, a confidence, the
  thresholds it applied, and the forecast figures it judged.
- The agent chose the judgment tool over `get_forecast` for a rain-preparedness question,
  as the system prompt instructs.
- The answer explains **why** (100% against the 40% threshold, 6.0 mm against the 1.0 mm
  threshold) rather than asserting a bare yes.

---

## Q3. Are there any severe weather alerts for Miami?

**Reasoning**
> The user is asking about severe weather alerts for Miami, which suggests they want to
> know about any active alerts or warnings in the area. This tool is specifically designed
> to provide that information.

**Tool call** — `get_severe_weather_alerts`

```json
{ "location": "Miami" }
```

**Tool output**

```json
{
  "requested_location": "Miami",
  "resolved_location": "Miami, Florida, United States",
  "alert_count": 0,
  "alerts": [],
  "coverage": "us",
  "source": "nws"
}
```

**Answer**
> There are no severe weather alerts for Miami.

**What this demonstrates**

- The second data source (National Weather Service) is wired up and reachable, separate
  from Open-Meteo.
- `coverage: "us"` means the feed genuinely covers this location, so "no alerts" is a
  **truthful** answer here. Contrast with **Q7**.

---

## Q4. What was the weather in Chicago on 2026-07-10?  🔹 stretch tool

**Reasoning**
> The user is asking for the historical weather in Chicago on a specific date, so I need to
> use the `get_historical_weather` function to retrieve this information.

**Tool call** — `get_historical_weather`

```json
{ "location": "Chicago", "date": "2026-07-10" }
```

**Tool output**

```json
{
  "requested_location": "Chicago",
  "resolved_location": "Chicago, Illinois, United States",
  "date": "2026-07-10",
  "conditions": "light drizzle",
  "temp_max": 24.8,
  "temp_min": 21.7,
  "temp_mean": 23.1,
  "precipitation_sum": 0.3,
  "wind_speed_max": 13.4,
  "units": { "temperature": "°C", "precipitation": "mm", "wind_speed": "km/h" },
  "source": "open-meteo-archive"
}
```

**Answer**
> On 2026-07-10, the weather in Chicago was light drizzle, with a high of 24.8°C and a low
> of 21.7°C. The mean temperature was 23.1°C, and there was 0.3 mm of precipitation.

**What this demonstrates**

- **Stretch tool:** historical weather lookup.
- The agent routed a **past** date to the archive tool rather than to `get_forecast`, which
  cannot serve past dates. Note `"source": "open-meteo-archive"` — a different dataset
  (ERA5 reanalysis) from the forecast model used everywhere else.

---

## Q5. Is it warmer in Miami or Reykjavik right now?  🔹 stretch tool

**Reasoning**
> I am using the `compare_weather` function to determine which city is warmer between Miami
> and Reykjavik. This function will allow me to compare the current weather in both cities
> and provide a direct answer to the user's question.

**Tool call** — `compare_weather`

```json
{ "locations": ["Miami", "Reykjavik"] }
```

**Tool output**

```json
{
  "requested_locations": ["Miami", "Reykjavik"],
  "compared": [
    {
      "location": "Miami, Florida, United States",
      "temperature": 30.2, "feels_like": 34.6, "conditions": "clear sky",
      "precipitation": 0, "wind_speed": 13.7, "humidity_pct": 72
    },
    {
      "location": "Reykjavik, Capital Region, Iceland",
      "temperature": 10.3, "feels_like": 8.1, "conditions": "mainly clear",
      "precipitation": 0, "wind_speed": 6.8, "humidity_pct": 69
    }
  ],
  "warmest": "Miami, Florida, United States",
  "coldest": "Reykjavik, Capital Region, Iceland",
  "wettest": "Miami, Florida, United States",
  "currently_raining": [],
  "failed": [],
  "units": { "temperature": "°C", "wind_speed": "km/h", "precipitation": "mm" },
  "source": "open-meteo"
}
```

**Answer**
> It is warmer in Miami, with a current temperature of 30.2°C, compared to Reykjavik, which
> is at 10.3°C. Miami is the warmest of the two locations.

**What this demonstrates**

- **Stretch tool:** comparing weather across multiple cities.
- **One** tool call carrying both locations, not two separate `get_current_weather` calls.
  That is the routing the system prompt asks for, and it makes the comparison a single
  ranked result rather than two readings the model has to compare itself.
- The tool returns the ranking (warmest / coldest / wettest) already computed, so the agent
  reports a comparison rather than performing arithmetic on raw numbers.

---

## Q6. What's the weather in Xyzzyville?  🛡 guardrail

**Reasoning**
> The user is asking about the current weather in Xyzzyville, so I need to use the
> `get_current_weather` function to provide the current weather conditions for that location.

**Tool call** — `get_current_weather`

```json
{ "location": "Xyzzyville" }
```

**Tool output**

```json
{
  "error": "unknown_location",
  "message": "Could not find a place called 'Xyzzyville'. Try a larger nearby city, or pass coordinates as 'lat,lon'.",
  "requested_location": "Xyzzyville",
  "suggestion": "Ask the user to confirm the city, or provide 'lat,lon'."
}
```

**Answer**
> I couldn't find a place called "Xyzzyville". It's possible that it's a fictional location
> or a misspelling. Can you please confirm the city name or provide coordinates as "lat,lon"
> so I can try to find the weather for you?

**What this demonstrates** — the full error path, end to end:

- The tool returned a **structured error dict**, not a stack trace and not an invented forecast.
- The agent **called the tool** rather than answering from its own knowledge that the name
  looks fictional. It did not assume; it checked.
- It then followed the `suggestion` field almost verbatim — "confirm the city name or provide
  coordinates as lat,lon" — which is exactly the handoff the system prompt specifies. A tool
  failure became a clarifying question instead of a guess.

> Captured in screenshot `4-agent-tool-call-and-answer.png`.

---

## Q7. Any weather alerts for London?  🛡 guardrail

**Reasoning**
> The user is asking about severe weather alerts for London. Since severe weather alerts are
> only available for the United States, I expect the function to return an empty alert list
> with coverage 'unavailable'.

**Tool call** — `get_severe_weather_alerts`

```json
{ "location": "London" }
```

**Tool output**

```json
{
  "requested_location": "London",
  "resolved_location": "London, England, United Kingdom",
  "alert_count": 0,
  "alerts": [],
  "coverage": "unavailable",
  "note": "No alert data available for this location. The National Weather Service covers the United States only. (Weather API returned HTTP 400)",
  "source": "nws"
}
```

**Answer**
> There is no severe weather alert data available for London, as the National Weather Service
> only covers the United States.

**What this demonstrates** — the most important transcript of the seven.

The agent said alert data is **unavailable** for London. It did **not** say "there are no
alerts for London".

That distinction matters. The alert feed is US-only, so an empty list outside the United
States means **no data**, not **no danger**. Reporting it as "no alerts" would be a
safety-relevant falsehood — the agent would be reassuring a user about severe weather it
never actually checked. The tool encodes the distinction (`coverage: "us"` in Q3 versus
`"unavailable"` here) and the system prompt requires the agent to honour it. Both held.

Compare the answers directly:

| Question | `coverage` | Answer |
|---|---|---|
| Q3 — Miami | `us` | "There are no severe weather alerts" |
| Q7 — London | `unavailable` | "no severe weather alert data available" |

---

## Direct tool verification

Called over MCP with a real client against the deployed app, covering branches the seven
transcripts above do not reach.

**1. Unresolvable location returns a clean, structured error** — not a stack trace:

```json
{
  "error": "unknown_location",
  "message": "Could not find a place called 'Xyzzyville'. Try a larger nearby city, or pass coordinates as 'lat,lon'.",
  "requested_location": "Xyzzyville",
  "suggestion": "Ask the user to confirm the city, or provide 'lat,lon'."
}
```

The `suggestion` field is what the system prompt instructs the agent to act on, which is how
a tool failure becomes "please clarify" rather than a guess or a traceback. **Q6 above shows
the agent doing exactly that.**

**2. Response shape is consistent across coverage values:**

```text
Miami   coverage=us           alert_count=0   present=True
London  coverage=unavailable  alert_count=0   present=True
```

An earlier version omitted `alert_count` on the non-US branch, so the field's presence
depended on coverage and a caller doing `response["alert_count"]` would have raised a
`KeyError` on every non-US location. It is now returned on both branches — visible in the Q7
transcript above, captured after the fix.

**3. All six tools advertised over the protocol by the deployed app:**

```text
get_current_weather          args=['location']
get_forecast                 args=['location', 'days']
should_i_bring_an_umbrella   args=['location']
get_severe_weather_alerts    args=['location']
get_historical_weather       args=['location', 'date']
compare_weather              args=['locations']
```

**4. Prediction-tool decision branches**, verified with synthetic forecasts so branches
today's real weather cannot produce are still covered:

| Probability | Accumulation | Wind | Verdict | Rule |
|---|---|---|---|---|
| 5% | 0.0 mm | 10 km/h | **no** | below both thresholds |
| 40% | 1.0 mm | 10 km/h | **yes** | exact boundary, inclusive |
| 95% | 14.0 mm | 12 km/h | **yes** | soaking — "expect to get wet anyway" |
| 90% | 6.0 mm | 48 km/h | **no** | wind overrides rain (umbrella inverts) |
| 95% | 20.0 mm | 60 km/h | **no** | wind still wins over soaking |

---

## Stretch work (extra credit)

The brief lists three optional stretch tools — severe weather alerts, historical weather
lookup, comparing weather across multiple cities — and an optional dashboard app.
**All four are implemented.**

### 1. Severe weather alerts — `get_severe_weather_alerts`

Second data source (National Weather Service). Demonstrated in **Q3** and **Q7** above,
including the US-only coverage guardrail.

### 2. Historical weather lookup — `get_historical_weather(location, date)`

Open-Meteo's ERA5 reanalysis archive, a different dataset from the forecast model. Verified
through the deployed MCP server:

```text
get_historical_weather("Chicago", "2026-07-10")
-> light drizzle, 21.7-24.8 °C, 0.3 mm precipitation
```

The archive trails real time by about five days, so recent dates return an empty series
rather than an error. That would reach the agent as unexplained "no data", so the tool
rejects them with a reason instead:

| Input | Result |
|---|---|
| `"2099-01-01"` | `invalid_date` — not in the past. Use `get_forecast` instead. |
| today | `invalid_date` — not in the past. Use `get_forecast` instead. |
| 2 days ago | `invalid_date` — too recent for the archive, which lags about 5 days. |
| `"not-a-date"` | `invalid_date` — date must be `YYYY-MM-DD`. |
| bad location | `unknown_location` — distinct from `invalid_date`, because the remedy differs: confirm the city vs. change the date. |

### 3. Compare weather across cities — `compare_weather(locations)`

Ranks cities rather than returning parallel readings, so the agent can answer "which of these
is nicest right now" directly. Verified live:

```text
compare_weather(["Chicago","Miami","Reykjavik","Mumbai"])

  warmest  Miami, Florida, United States
  coldest  Reykjavik, Capital Region, Iceland
  wettest  Mumbai, Maharashtra, India
  currently raining: ["Mumbai, Maharashtra, India"]

  30.4 °C  Miami, Florida, United States        clear sky
  26.3 °C  Mumbai, Maharashtra, India           light drizzle
  24.7 °C  Chicago, Illinois, United States     clear sky
  10.7 °C  Reykjavik, Capital Region, Iceland   partly cloudy
```

One unresolvable city does not sink the comparison:

```text
compare_weather(["Chicago","Xyzzyville","Tokyo"])
-> compared 2 cities, failed ["Xyzzyville"], warmest Chicago
```

Fewer than two locations is rejected with `invalid_arguments` rather than silently returning
a one-item "comparison".

### 4. Dashboard app

<https://weather-mcp-dashboard-7474646797973312.aws.databricksapps.com>

A separate Databricks App showing recent agent tool calls, tool usage, error counts and
latency — mirroring the reference repo's `mcp_server/` + `dashboard/` split. The two apps
share no memory, so the MCP server writes one row per tool call to Lakebase and the dashboard
reads them back.

Verified live against the deployed dashboard:

```text
GET /healthz    -> 200 {"status":"ok"}
GET /           -> 200, renders the table, no error page
GET /api/calls  -> total=14 ok=12 errors=2 locations=8 avg=915ms

  get_current_weather          4
  get_severe_weather_alerts    3
  compare_weather              3
  should_i_bring_an_umbrella   2
  get_historical_weather       2
```

Captured in screenshot `5-dashboard-agent-activity.png`.

**Logging is best-effort and cannot break a tool call.** If the secret is missing, the ACL was
never granted, or Postgres is unreachable, `record()` warns once and disables itself for the
process while the weather tools keep answering. This path was exercised accidentally during
development, when `psycopg2` was not yet installed: one warning was logged and all five tool
calls still succeeded.

---

## Requirements coverage

| Requirement | Where |
|---|---|
| MCP server built with FastMCP, `@mcp.tool`, streamable HTTP | `weather_mcp_server.py` |
| Separate adapter module, no raw `requests` inside a tool | `weather_broker.py` |
| `requirements.txt` + `app.yaml`, deployed as its own App | deployed, **RUNNING** |
| Minimum 3 tools | **6 tools** |
| — current conditions | `get_current_weather` |
| — forecast | `get_forecast` |
| — prediction/recommendation with real logic | `should_i_bring_an_umbrella` |
| — 🔹 stretch: severe weather alerts (2nd data source) | `get_severe_weather_alerts` |
| — 🔹 stretch: historical weather lookup | `get_historical_weather` |
| — 🔹 stretch: compare weather across cities | `compare_weather` |
| 🔹 Optional stretch: dashboard app | deployed, **RUNNING** |
| Registered as an external MCP | Unity AI Gateway, **Active** |
| Agent using the MCP server | AI Playground, Llama 3.3 70B |
| System prompt with guardrails | `SYSTEM_PROMPT.md` |
| At least 3 NL questions with tool calls + answers | **7 above** |
| No secrets committed, no hardcoded API keys | none exist — see below |
| README with tools, setup, API + auth method | `README.md` |

**Auth method: none required.** Open-Meteo needs no API key or signup; the National Weather
Service needs only a descriptive `User-Agent` header. There is therefore no credential in this
repository to leak or rotate. `README.md` documents the Databricks secret pattern
(`_secret()` / `WorkspaceClient().secrets.get_secret()`) to use if a keyed provider is
substituted.
