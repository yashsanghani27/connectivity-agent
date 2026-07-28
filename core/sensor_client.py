"""
Sensor client — plain HTTP, no browser, no Selenium.

The Loctite Pulse / Electric Imp agent page (agent.electricimp.com/xxx)
serves its live values by having the page's own JavaScript call
<agenturl>/current, which returns a JSON object. We call that same
endpoint directly with Python's built-in HTTP client. This replaces
the entire Selenium/WebDriver/headless-browser approach.

The URL the user types is the main page URL. We append '/current' to
get the JSON endpoint, unless the user already added it.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

_HTTP_TIMEOUT = 4.0   # seconds per request — if the sensor hasn't responded
                      # in 4 s it's unreachable; no point waiting 8 s and
                      # slowing down the poll cycle unnecessarily
_UA = "ConnectivityAgent/2.0 (Loctite Pulse Field Tool)"
_JSON_PATH = "/current"


@dataclass
class SensorReading:
    connected: bool = False
    raw_state: str  = ""
    rssi:  Optional[int]   = None
    rsrp:  Optional[int]   = None
    rsrq:  Optional[int]   = None
    sinr:  Optional[int]   = None
    speed: Optional[float] = None
    lac:              Optional[str] = None
    cellid:           Optional[str] = None
    access_technology:Optional[str] = None
    sm_2g:            Optional[str] = None
    mccmnc:           Optional[str] = None
    band:             Optional[str] = None
    channel_type:     Optional[str] = None
    error: Optional[str] = None
    raw_json: dict = field(default_factory=dict)


def _json_url(user_url: str) -> str:
    """Convert the user-pasted URL to the /current JSON endpoint."""
    url = user_url.strip()
    if not url:
        raise ValueError("Empty URL")
    parsed = urllib.parse.urlsplit(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urllib.parse.urlsplit(url)
    path = parsed.path.rstrip("/")
    if not path.endswith(_JSON_PATH):
        path += _JSON_PATH
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, "", ""))


def _num(v, is_float: bool = False):
    if v is None:
        return None
    try:
        s = str(v).strip()
        if not s or s.lower() in ("unknown", "n/a", "na", "--", "null", "none"):
            return None
        return float(s) if is_float else int(float(s))
    except (TypeError, ValueError):
        return None


def _text(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("unknown", "n/a", "na", "--", "null", "none") else None


def parse_json(payload: dict) -> SensorReading:
    """Parse the /current JSON payload into a SensorReading.

    Connection rule:
      - Page must say state='connected' (case-insensitive).
      - AND at least RSRP or RSRQ must be a real negative number
        (not 0 — the page returns 0 as a placeholder when the radio
        hasn't registered yet, even if it echoes 'connected').
    """
    raw_state = str(payload.get("state", "")).strip()
    r = SensorReading(raw_state=raw_state, raw_json=payload)

    rsrp = _num(payload.get("RSRP"))
    rsrq = _num(payload.get("RSRQ"))
    sinr = _num(payload.get("SINR"))
    rssi = _num(payload.get("rssi"))
    speed = _num(payload.get("speed1"), is_float=True)

    # Reject placeholders: real RSRP is always negative and in [-140, -44]
    if rsrp is not None and (rsrp >= 0 or rsrp < -140):
        rsrp = None
    if rsrq is not None and (rsrq >= 0 or rsrq < -30):
        rsrq = None
    if rssi is not None and rssi == 0:
        rssi = None

    page_says_connected = raw_state.lower() == "connected"
    have_real_signal = (rsrp is not None) or (rsrq is not None)

    r.connected = page_says_connected and have_real_signal

    if r.connected:
        r.rssi  = rssi
        r.rsrp  = rsrp
        r.rsrq  = rsrq
        r.sinr  = sinr
        r.speed = speed
        r.lac               = _text(payload.get("lac"))
        r.cellid            = _text(payload.get("cellid"))
        r.access_technology = _text(payload.get("at"))
        r.sm_2g             = _text(payload.get("SM"))
        r.mccmnc            = _text(payload.get("MCCMNC"))
        r.band              = _text(payload.get("BAND"))
        r.channel_type      = _text(payload.get("Channel"))

    return r


def poll_sensor(url: str) -> SensorReading:
    """Fetch the sensor JSON and return a SensorReading. Never raises."""
    try:
        endpoint = _json_url(url)
    except ValueError as e:
        return SensorReading(connected=False, raw_state="bad_url",
                             error=str(e))

    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        endpoint,
        headers={"User-Agent": _UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return SensorReading(connected=False, raw_state="unreachable",
                             error=str(e))
    except Exception as e:
        return SensorReading(connected=False, raw_state="error", error=str(e))

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return SensorReading(connected=False, raw_state="non_json",
                             error=f"Non-JSON response: {body[:200]}")

    return parse_json(payload)