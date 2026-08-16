# -*- coding: utf-8 -*-

import os
import sys
import time
import urllib2

import xbmc
import xbmcgui

try:
    import json
except ImportError:
    import simplejson as json

try:
    unicode
except NameError:
    unicode = str


PROPERTY_WINDOW_ID = 10000
WEATHER_API_URL = "http://10.4.20.210:8501/api/portals/xbmc_portal/api/tater-xbmc/v1/weather"
SETTINGS_FILE = os.path.join(xbmc.translatePath("special://profile"), "cortana_chat_settings.json")
CACHE_FILE = os.path.join(xbmc.translatePath("special://profile"), "tater_weather_cache.json")
HTTP_TIMEOUT_SECONDS = 4
CACHE_SECONDS = 600
DEFAULT_ICON = "button_icons/icon-weather.png"
PY2 = sys.version_info[0] == 2


def _log(msg):
    try:
        xbmc.log("TaterWeather: %s" % msg, xbmc.LOGNOTICE)
    except Exception:
        try:
            print("TaterWeather: %s" % msg)
        except Exception:
            pass


def _text(value):
    if value is None:
        return ""
    if not PY2 and isinstance(value, bytes):
        try:
            return value.decode("utf-8", "ignore")
        except Exception:
            return ""
    try:
        if isinstance(value, unicode):
            return value.encode("utf-8") if PY2 else value
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return ""


def _clean(value, limit=80):
    text = _text(value).replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].strip()
    return text


def _read_json(path, default_value):
    try:
        if os.path.exists(path):
            handle = open(path, "rb")
            try:
                return json.loads(handle.read())
            finally:
                handle.close()
    except Exception as exc:
        _log("Failed to read %s: %s" % (path, exc))
    return default_value


def _write_json(path, value):
    try:
        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        handle = open(path, "wb")
        try:
            blob = json.dumps(value, separators=(",", ":"))
            if not PY2 and isinstance(blob, str):
                blob = blob.encode("utf-8")
            handle.write(blob)
        finally:
            handle.close()
    except Exception as exc:
        _log("Failed to write %s: %s" % (path, exc))


def _window():
    return xbmcgui.Window(PROPERTY_WINDOW_ID)


def _set_property(name, value):
    try:
        _window().setProperty("TaterWeather.%s" % name, _text(value))
    except Exception as exc:
        _log("Failed to set property %s: %s" % (name, exc))
    try:
        text = _text(value).replace("\r", " ").replace("\n", " ").replace(",", " ").replace("(", " ").replace(")", " ").strip()
        xbmc.executebuiltin("Skin.SetString(TaterWeather.%s,%s)" % (name, text))
    except Exception as exc:
        _log("Failed to set skin string %s: %s" % (name, exc))


def _api_key():
    settings = _read_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        return ""
    return _clean(settings.get("api_key"), 160)


def _request_headers():
    headers = {
        "Accept": "application/json",
        "User-Agent": "XBMC4Xbox Tater Weather",
    }
    api_key = _api_key()
    if api_key:
        headers["X-Tater-Token"] = api_key
    return headers


def _temperature_display(weather):
    display = _clean(weather.get("temperature_display"), 32)
    if display and display != "--":
        return display
    value = _clean(weather.get("temperature"), 16)
    units = _clean(weather.get("temperature_units"), 4)
    if value and units:
        return "%s %s" % (value, units)
    if value:
        return value
    return "--"


def _weather_from_payload(payload):
    if not isinstance(payload, dict):
        return {}
    weather = payload.get("weather")
    if isinstance(weather, dict):
        return weather
    return payload


def _apply_weather(weather):
    if not isinstance(weather, dict):
        weather = {}
    condition = _clean(weather.get("condition"), 42) or "Environment Core waiting"
    if bool(weather.get("stale")) and condition and "stale" not in condition.lower():
        condition = _clean("%s (stale)" % condition, 42)

    _set_property("ConditionIcon", _clean(weather.get("icon"), 120) or DEFAULT_ICON)
    _set_property("Temperature", _clean(weather.get("temperature"), 16))
    _set_property("TemperatureUnits", _clean(weather.get("temperature_units"), 4))
    _set_property("TemperatureDisplay", _temperature_display(weather))
    _set_property("Condition", condition)
    _set_property("Location", _clean(weather.get("location"), 42))
    _set_property("ConditionKind", _clean(weather.get("condition_kind"), 24))


def _apply_unavailable():
    _apply_weather({
        "temperature_display": "--",
        "condition": "Environment Core unavailable",
        "icon": DEFAULT_ICON,
        "stale": True,
    })


def _fetch_weather():
    request = urllib2.Request(WEATHER_API_URL, headers=_request_headers())
    response = urllib2.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS)
    try:
        raw = response.read()
    finally:
        try:
            response.close()
        except Exception:
            pass
    payload = json.loads(raw)
    return _weather_from_payload(payload)


def _main():
    cache = _read_json(CACHE_FILE, {})
    cached_weather = _weather_from_payload(cache)
    if cached_weather:
        _apply_weather(cached_weather)
    else:
        _apply_weather({"temperature_display": "--", "condition": "Loading Tater weather", "icon": DEFAULT_ICON})

    fetched_at = 0
    try:
        fetched_at = float(cache.get("fetched_at") or 0)
    except Exception:
        fetched_at = 0
    if cached_weather and time.time() - fetched_at < CACHE_SECONDS:
        return

    try:
        weather = _fetch_weather()
    except Exception as exc:
        _log("Fetch failed: %s" % exc)
        if not cached_weather:
            _apply_unavailable()
        return

    if weather:
        _apply_weather(weather)
        _write_json(CACHE_FILE, {"fetched_at": time.time(), "weather": weather})
    elif not cached_weather:
        _apply_unavailable()


if __name__ == "__main__":
    _main()
