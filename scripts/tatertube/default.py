# -*- coding: utf-8 -*-

import os
import re
import sys
import threading
import time
import xbmc
import xbmcgui
import urllib
import urllib2
import urlparse

try:
    import json
except ImportError:
    import simplejson as json

try:
    unicode
except NameError:
    unicode = str


WINDOW_ID = 1112
PROPERTY_WINDOW_ID = 10000
FULLSCREEN_VIDEO_WINDOW_ID = 2005
LIST_CONTROL_ID = 2400
MAX_ROWS = 9
CLEAR_ROW_COUNT = 10
PAGE_ITEM_COUNT = 7
HTTP_TIMEOUT_SECONDS = 20
STREAM_TIMEOUT_SECONDS = 330
DEFAULT_SERVER_URL = "http://10.4.20.210:8080"
DEFAULT_PLAYER_NAME = "Original Xbox"
XBOX_PROFILE = "xbox_480p"
TV_LINEUP_PARSE_LIMIT = 1024 * 1024
TV_LINEUP_BUFFER_LIMIT = 64 * 1024
TV_PLAYLIST_ITEM_COUNT = 24
TV_PLAYLIST_ROW_BUFFER_LIMIT = 2 * 1024 * 1024
SETTINGS_FILE = os.path.join(xbmc.translatePath("special://profile"), "tater_tube_settings.json")
STATE_FILE = os.path.join(xbmc.translatePath("special://profile"), "tater_tube_state.json")
TV_CHANNEL_HEADER_RE = re.compile(r'\{"number":"((?:\\.|[^"\\])*)","title":"((?:\\.|[^"\\])*)","streamUrl":"((?:\\.|[^"\\])*)".{0,4096}?"schedule":', re.S)
TV_STARTED_RE = re.compile(r'"startedAt":"([^"]+)"')
TV_SERVER_NOW_RE = re.compile(r'"serverNow":"([^"]+)"')
TV_TOTAL_DURATION_RE = re.compile(r'"totalDuration":([0-9.]+)')


def _log(msg):
    try:
        xbmc.log("TaterTube: %s" % msg, xbmc.LOGNOTICE)
    except Exception:
        try:
            print("TaterTube: %s" % msg)
        except Exception:
            pass


def _text(value):
    if value is None:
        return ""
    try:
        if isinstance(value, unicode):
            return value.encode("utf-8")
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


def _float(value, default_value=0.0):
    try:
        return float(value)
    except Exception:
        return default_value


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
            handle.write(json.dumps(value, separators=(",", ":")))
        finally:
            handle.close()
    except Exception as exc:
        _log("Failed to write %s: %s" % (path, exc))


def _load_settings():
    settings = _read_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        settings = {}
    if not settings.get("server_url"):
        settings["server_url"] = DEFAULT_SERVER_URL
    if not settings.get("player_name"):
        settings["player_name"] = DEFAULT_PLAYER_NAME
    return settings


def _save_settings(settings):
    _write_json(SETTINGS_FILE, settings)


def _load_state():
    state = _read_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    if not isinstance(state.get("rows"), list):
        state["rows"] = []
    if not isinstance(state.get("stack"), list):
        state["stack"] = []
    if not isinstance(state.get("visible_rows"), list):
        state["visible_rows"] = []
    return state


def _save_state(state):
    _write_json(STATE_FILE, state)


def _window():
    return xbmcgui.Window(PROPERTY_WINDOW_ID)


def _set_property(name, value):
    try:
        _window().setProperty("TaterTube." + name, _text(value))
    except Exception as exc:
        _log("Failed to set property %s: %s" % (name, exc))


def _clear_row_properties():
    for index in range(1, CLEAR_ROW_COUNT + 1):
        _set_property("Row%d.Title" % index, "")
        _set_property("Row%d.Detail" % index, "")


def _page_rows(rows, page):
    if not rows:
        return []
    page = _normalize_page(rows, page)
    start = page * PAGE_ITEM_COUNT
    visible = rows[start:start + PAGE_ITEM_COUNT]
    max_page = _max_page(rows)
    if max_page > 0:
        if page > 0:
            visible.append(_row("Previous Page", "Page %d of %d" % (page, max_page + 1), "page", {"page": page - 1}))
        if page < max_page:
            visible.append(_row("Next Page", "Page %d of %d" % (page + 2, max_page + 1), "page", {"page": page + 1}))
    return visible[:MAX_ROWS]


def _max_page(rows):
    if not rows:
        return 0
    return max(0, (len(rows) - 1) // PAGE_ITEM_COUNT)


def _normalize_page(rows, page):
    try:
        page = int(page)
    except Exception:
        page = 0
    if page < 0:
        return 0
    max_page = _max_page(rows)
    if page > max_page:
        return max_page
    return page


def _list_item(title, detail):
    label = _clean(title, 56)
    label2 = _clean(detail, 72)
    try:
        item = xbmcgui.ListItem(label)
    except Exception:
        item = xbmcgui.ListItem()
    try:
        item.setLabel2(label2)
    except Exception:
        pass
    try:
        item.setProperty("Detail", label2)
    except Exception:
        pass
    return item


def _populate_list(rows):
    try:
        window = xbmcgui.Window(WINDOW_ID)
        control = window.getControl(LIST_CONTROL_ID)
        try:
            control.reset()
        except Exception:
            control.clear()
        for row in rows:
            control.addItem(_list_item(row.get("title"), row.get("detail")))
        try:
            window.setFocus(control)
        except Exception:
            pass
    except Exception as exc:
        _log("Failed to populate list control: %s" % exc)


def _populate_row_properties(rows):
    _clear_row_properties()
    for index, row in enumerate(rows[:MAX_ROWS]):
        number = index + 1
        _set_property("Row%d.Title" % number, _clean(row.get("title"), 56))
        _set_property("Row%d.Detail" % number, _clean(row.get("detail"), 72))


def _selected_list_index():
    try:
        control = xbmcgui.Window(WINDOW_ID).getControl(LIST_CONTROL_ID)
        return int(control.getSelectedPosition()) + 1
    except Exception as exc:
        _log("Failed to read selected list position: %s" % exc)
    return 1


def _render(title, subtitle, rows, stack=None, status="", page=0):
    if stack is None:
        stack = []
    if not rows:
        rows = [_row("Pair/Settings", "Configure Tater Tube", "settings", {})]
    page = _normalize_page(rows, page)
    visible = _page_rows(rows, page)
    _set_property("Title", _clean(title, 64))
    _set_property("Subtitle", _clean(subtitle, 115))
    display_status = status
    max_page = _max_page(rows)
    if max_page > 0:
        page_text = "Page %d/%d" % (page + 1, max_page + 1)
        display_status = _clean("%s  %s" % (status, page_text), 115) if status else page_text
    _set_property("Status", _clean(display_status, 115))
    _populate_row_properties(visible)

    _save_state({
        "title": title,
        "subtitle": subtitle,
        "rows": rows,
        "visible_rows": visible,
        "stack": stack,
        "page": page,
        "status": status,
    })


def _show_window():
    xbmc.executebuiltin("ActivateWindow(%d)" % WINDOW_ID)
    xbmc.sleep(200)


def _notify(message, duration=2200):
    try:
        xbmc.executebuiltin("Notification(Tater Tube, %s, %d)" % (_clean(message, 42), duration))
    except Exception:
        pass


def _dialog_error(message):
    try:
        xbmcgui.Dialog().ok("Tater Tube", _clean(message, 90))
    except Exception:
        _log(message)


def _keyboard(default_value, heading, hidden=False):
    keyboard = xbmc.Keyboard(_text(default_value), heading, hidden)
    keyboard.doModal()
    if keyboard.isConfirmed():
        return keyboard.getText().strip()
    return None


def _build_url(settings, path, params=None):
    base = _text(settings.get("server_url") or DEFAULT_SERVER_URL).rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    url = base + path
    if params:
        pairs = []
        for key in params:
            value = params[key]
            if value is None:
                continue
            pairs.append((key, _text(value)))
        if pairs:
            url += "?" + urllib.urlencode(pairs)
    return url


def _absolute_url(settings, value):
    url = _text(value).strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return _text(settings.get("server_url") or DEFAULT_SERVER_URL).rstrip("/") + url
    return url


def _append_query(url, params):
    parts = urlparse.urlparse(url)
    query = {}
    try:
        for key, value in urlparse.parse_qsl(parts.query, keep_blank_values=True):
            query[key] = value
    except Exception:
        query = {}
    for key in params:
        query[key] = params[key]
    new_query = urllib.urlencode(query)
    return urlparse.urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment))


def _with_xbox_transcode(url):
    if not url:
        return ""
    return _append_query(url, {
        "transcode": "1",
        "profile": XBOX_PROFILE,
        "hwaccel": "auto",
    })


def _request_json(settings, path, params=None, method="GET", payload=None, auth=True, timeout=HTTP_TIMEOUT_SECONDS):
    url = _build_url(settings, path, params)
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "XBMC4Xbox Tater Tube",
    }
    if payload is not None:
        data = json.dumps(payload)
        headers["Content-Type"] = "application/json"
    if auth and settings.get("token"):
        headers["Authorization"] = "Bearer " + _text(settings.get("token"))

    request = urllib2.Request(url, data, headers)
    if method and method.upper() != "GET":
        request.get_method = lambda: method.upper()

    try:
        response = urllib2.urlopen(request, timeout=timeout)
        raw = response.read()
    except urllib2.HTTPError as exc:
        detail = exc.read()
        message = ""
        try:
            obj = json.loads(detail)
            error = obj.get("error") or {}
            if isinstance(error, dict):
                message = error.get("message") or error.get("details")
            else:
                message = error
        except Exception:
            message = ""
        raise Exception(_text(message or ("HTTP %s" % exc.code)))
    except Exception as exc:
        raise Exception(_text(exc))

    if not raw:
        return {}
    obj = json.loads(raw)
    if isinstance(obj, dict) and obj.get("success") is True and "data" in obj:
        return obj.get("data")
    return obj


def _open_json(settings, path, params=None, auth=True, timeout=HTTP_TIMEOUT_SECONDS):
    headers = {
        "Accept": "application/json",
        "User-Agent": "XBMC4Xbox Tater Tube",
    }
    if auth and settings.get("token"):
        headers["Authorization"] = "Bearer " + _text(settings.get("token"))
    return urllib2.urlopen(urllib2.Request(_build_url(settings, path, params), None, headers), timeout=timeout)


def _json_string(value):
    try:
        return json.loads('"' + value + '"')
    except Exception:
        return value.replace("\\/", "/")


def _tv_channels_from_response(obj):
    if isinstance(obj, dict) and obj.get("success") is True and "data" in obj:
        obj = obj.get("data")
    channels = obj.get("channels") if isinstance(obj, dict) else []
    if isinstance(channels, list):
        return [channel for channel in channels if isinstance(channel, dict)]
    return []


def _extract_tv_channel_headers(buffer, channels):
    last_end = 0
    for match in TV_CHANNEL_HEADER_RE.finditer(buffer):
        channels.append({
            "number": _json_string(match.group(1)),
            "title": _json_string(match.group(2)),
            "streamUrl": _json_string(match.group(3)),
        })
        last_end = match.end()
    if last_end > 0:
        buffer = buffer[last_end:]
    if len(buffer) > TV_LINEUP_BUFFER_LIMIT:
        buffer = buffer[-TV_LINEUP_BUFFER_LIMIT:]
    return buffer


def _request_tv_channels(settings):
    response = _open_json(settings, "/api/tater/tv/lineup", {"summary": "1"})
    chunks = []
    channels = []
    buffer = ""
    total = 0
    while True:
        chunk = response.read(32768)
        if not chunk:
            break
        total += len(chunk)
        if total <= TV_LINEUP_PARSE_LIMIT:
            chunks.append(chunk)
        elif chunks:
            chunks = []
        buffer = _extract_tv_channel_headers(buffer + chunk, channels)

    if chunks:
        raw = "".join(chunks)
        try:
            parsed_channels = _tv_channels_from_response(json.loads(raw))
            if parsed_channels:
                return parsed_channels
        except Exception as exc:
            _log("Summary lineup parse skipped: %s" % exc)
    if channels:
        return channels
    raise Exception("Tube TV lineup was too large to read.")


def _parse_iso_seconds(value):
    text = _text(value).strip()
    if len(text) < 19:
        return None
    try:
        return time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def _seconds_query(value):
    text = "%.3f" % _float(value)
    while "." in text and text.endswith("0"):
        text = text[:-1]
    if text.endswith("."):
        text = text[:-1]
    return text


def _compact_tv_row(row, index):
    url = row.get("streamUrl") or row.get("url")
    return {
        "index": index,
        "start": _float(row.get("start")),
        "end": _float(row.get("end")),
        "mediaOffset": _float(row.get("mediaOffset")),
        "duration": _float(row.get("duration")),
        "url": url,
    }


def _extract_tv_time(buffer, meta):
    if not meta.get("startedAt"):
        match = TV_STARTED_RE.search(buffer)
        if match:
            meta["startedAt"] = match.group(1)
    if not meta.get("serverNow"):
        match = TV_SERVER_NOW_RE.search(buffer)
        if match:
            meta["serverNow"] = match.group(1)
    if not meta.get("totalDuration"):
        match = TV_TOTAL_DURATION_RE.search(buffer)
        if match:
            meta["totalDuration"] = _float(match.group(1))


def _request_tv_schedule(settings, number):
    target = '"number":"' + _text(number).replace("\\", "\\\\").replace('"', '\\"') + '"'
    response = _open_json(settings, "/api/tater/tv/lineup", {"summary": "0"})
    decoder = json.JSONDecoder()
    buffer = ""
    rows = []
    meta = {"startedAt": "", "serverNow": "", "totalDuration": 0.0}
    phase = "find_channel"
    row_index = 0

    while True:
        chunk = response.read(32768)
        if not chunk:
            break
        buffer += chunk

        while True:
            if phase == "find_channel":
                pos = buffer.find(target)
                if pos < 0:
                    if len(buffer) > TV_LINEUP_BUFFER_LIMIT:
                        buffer = buffer[-TV_LINEUP_BUFFER_LIMIT:]
                    break
                buffer = buffer[pos:]
                phase = "find_schedule"

            if phase == "find_schedule":
                pos = buffer.find('"schedule":[')
                if pos < 0:
                    if len(buffer) > TV_LINEUP_BUFFER_LIMIT:
                        buffer = buffer[-TV_LINEUP_BUFFER_LIMIT:]
                    break
                buffer = buffer[pos + len('"schedule":['):]
                phase = "rows"

            if phase == "rows":
                stripped = buffer.lstrip()
                buffer = stripped
                if not buffer:
                    break
                first = buffer[0]
                if first == ",":
                    buffer = buffer[1:]
                    continue
                if first == "]":
                    buffer = buffer[1:]
                    phase = "after_channel"
                    continue
                if first != "{":
                    if len(buffer) > TV_PLAYLIST_ROW_BUFFER_LIMIT:
                        raise Exception("Tube TV schedule parser lost its place.")
                    break
                try:
                    row, end = decoder.raw_decode(buffer)
                except ValueError:
                    if len(buffer) > TV_PLAYLIST_ROW_BUFFER_LIMIT:
                        raise Exception("Tube TV schedule row was too large.")
                    break
                if isinstance(row, dict):
                    rows.append(_compact_tv_row(row, row_index))
                row_index += 1
                buffer = buffer[end:]
                continue

            if phase == "after_channel":
                _extract_tv_time(buffer, meta)
                if meta.get("startedAt") and meta.get("serverNow") and meta.get("totalDuration"):
                    return rows, meta
                if len(buffer) > TV_LINEUP_BUFFER_LIMIT:
                    buffer = buffer[-TV_LINEUP_BUFFER_LIMIT:]
                break

    _extract_tv_time(buffer, meta)
    if rows:
        return rows, meta
    raise Exception("Tube TV channel schedule was not found.")


def _tv_schedule_position(rows, meta):
    total = _float(meta.get("totalDuration"))
    started = _parse_iso_seconds(meta.get("startedAt"))
    now = _parse_iso_seconds(meta.get("serverNow"))
    if total <= 0 or started is None or now is None:
        return 0, 0.0
    position = (now - started) % total
    for index, row in enumerate(rows):
        start = _float(row.get("start"))
        end = _float(row.get("end"))
        if position >= start and position < end:
            return index, _float(row.get("mediaOffset")) + max(0.0, position - start)
    return 0, _float(rows[0].get("mediaOffset")) if rows else 0.0


def _tv_item_url(settings, channel, row, start_seconds):
    url = row.get("url")
    if not url:
        number = _text(channel.get("number")).strip()
        if number:
            path = "/api/tater/tv/channel/%s/item/%d" % (urllib.quote(number, safe=""), int(row.get("index") or 0))
            url = _build_url(settings, path, {"player_token": settings.get("token")})
    url = _absolute_url(settings, url)
    if start_seconds > 0:
        url = _append_query(url, {"start": _seconds_query(start_seconds)})
    return _with_xbox_transcode(url)


def _tv_playlist_urls(settings, channel):
    number = _text(channel.get("number")).strip()
    if not number:
        return []
    rows, meta = _request_tv_schedule(settings, number)
    if not rows:
        return []
    start_index, start_seconds = _tv_schedule_position(rows, meta)
    urls = []
    for index, row in enumerate(rows[start_index:]):
        start = start_seconds if index == 0 else _float(row.get("mediaOffset"))
        url = _tv_item_url(settings, channel, row, start)
        if url:
            urls.append(url)
        if len(urls) >= TV_PLAYLIST_ITEM_COUNT:
            break
    return urls


def _stop_server_streams(settings):
    if not settings.get("token"):
        return
    try:
        _request_json(settings, "/api/tater/streams/active/stop", method="POST", payload={}, timeout=3)
    except Exception as exc:
        _log("Stream cleanup skipped: %s" % exc)


def _current_window_id():
    try:
        return xbmcgui.getCurrentWindowId()
    except Exception:
        return -1


def _is_video_fullscreen():
    try:
        if xbmc.getCondVisibility("Window.IsActive(VideoFullScreen)"):
            return True
        if xbmc.getCondVisibility("Window.IsActive(%d)" % FULLSCREEN_VIDEO_WINDOW_ID):
            return True
    except Exception:
        pass
    return _current_window_id() == FULLSCREEN_VIDEO_WINDOW_ID


def _start_video_cleanup_watcher(settings):
    snapshot = dict(settings)

    def worker():
        saw_fullscreen = False
        for _ in range(80):
            if _is_video_fullscreen():
                saw_fullscreen = True
                break
            xbmc.sleep(250)

        if not saw_fullscreen:
            _log("Video fullscreen was not detected; cleanup watcher idle")
            return

        player = xbmc.Player()
        idle_ticks = 0
        while True:
            try:
                playing = player.isPlayingVideo()
            except Exception:
                playing = False
            if not playing:
                idle_ticks += 1
                if idle_ticks >= 8:
                    break
                xbmc.sleep(500)
                continue
            idle_ticks = 0
            if not _is_video_fullscreen():
                _log("Video left fullscreen; stopping active Tater Tube streams")
                _stop_server_streams(snapshot)
                try:
                    xbmc.executebuiltin("PlayerControl(Stop)")
                except Exception:
                    pass
                return
            xbmc.sleep(1000)

        _stop_server_streams(snapshot)

    try:
        thread = threading.Thread(target=worker)
        thread.setDaemon(True)
        thread.start()
    except Exception as exc:
        _log("Stream cleanup watcher failed: %s" % exc)


def _row(title, detail, action, data=None):
    if data is None:
        data = {}
    return {
        "title": _text(title),
        "detail": _text(detail),
        "action": action,
        "data": data,
    }


def _row_detail(item):
    parts = []
    for key in ("sizeText", "category", "date", "artist", "album", "mediaType"):
        value = item.get(key)
        if value:
            parts.append(_clean(value, 32))
    return " / ".join(parts[:3])


def _category_row(category):
    kind = _text(category.get("type")).strip()
    title = category.get("title") or category.get("name") or category.get("id")
    detail = category.get("detail") or category.get("group") or ""
    children = category.get("children")
    if isinstance(children, list) and children:
        return _row(title, detail, "children", {"children": children, "title": title})
    if kind == "tubeTv":
        return _row(title or "Tube TV", detail or "SERVER", "tv_root", {})
    if kind == "search":
        return _row(title or "Search", detail or "STREAM", "search_stream", {})
    if kind == "discover":
        return _row(title, detail, "load_discover", category)
    if kind == "trending":
        return _row(title, detail, "load_trending", category)
    if kind == "continue":
        return _row(title or "Continue Watching", detail or "LOCAL", "load_continue", category)
    return _row(title, detail, "load_items", category)


def _item_row(item):
    title = item.get("title") or item.get("name") or item.get("key") or "Untitled"
    detail = _row_detail(item)
    kind = _text(item.get("type")).strip()
    media_type = _text(item.get("mediaType")).strip()

    if kind == "localFolder":
        return _row(title, detail or "FOLDER", "load_items", item)
    if kind == "musicLibrary":
        return _row(title, detail or "MUSIC", "music_albums", item)
    if kind == "album":
        return _row(title, detail or "ALBUM", "music_tracks", item)
    if kind == "track" or media_type == "audio":
        return _row(title, detail or "TRACK", "play_audio", item)
    if kind == "discovery" or item.get("searchQuery"):
        return _row(title, detail or "SEARCH", "search_query", item)
    if item.get("streamUrl"):
        return _row(title, detail or "PLAY", "play_local", item)
    if item.get("nzbUrl"):
        return _row(title, detail or "STREAM", "play_nzb", item)
    return _row(title, detail, "noop", item)


def _push_state(current_state):
    return {
        "title": current_state.get("title", "Tater Tube"),
        "subtitle": current_state.get("subtitle", ""),
        "rows": current_state.get("rows", []),
        "page": current_state.get("page", 0),
        "status": current_state.get("status", ""),
    }


def _render_child(title, subtitle, rows, status=""):
    state = _load_state()
    stack = state.get("stack", [])
    stack.append(_push_state(state))
    _render(title, subtitle, rows, stack, status, 0)


def _ensure_paired(settings):
    if settings.get("token"):
        return True
    return _pair_flow(settings, True)


def _pair_flow(settings, ask_server):
    if ask_server or not settings.get("server_url"):
        server_url = _keyboard(settings.get("server_url") or DEFAULT_SERVER_URL, "Tater Tube Server URL")
        if server_url is None:
            return False
        settings["server_url"] = server_url.rstrip("/")

    pin = _keyboard("", "Tater Tube Pairing PIN")
    if pin is None or not pin.strip():
        return False

    try:
        data = _request_json(settings, "/api/tater/players/pair", method="POST", payload={
            "pin": pin.strip(),
            "name": settings.get("player_name") or DEFAULT_PLAYER_NAME,
        }, auth=False)
    except Exception as exc:
        _dialog_error("Pairing failed: %s" % exc)
        return False

    token = data.get("token") or data.get("player_token")
    if not token:
        _dialog_error("Pairing did not return a player token.")
        return False
    settings["token"] = token
    settings["player_id"] = data.get("player_id") or data.get("id") or ""
    settings["player_name"] = data.get("player_name") or settings.get("player_name") or DEFAULT_PLAYER_NAME
    _save_settings(settings)
    _notify("Paired")
    return True


def _open_root():
    settings = _load_settings()
    _show_window()
    _render("Tater Tube", settings.get("server_url"), [_row("Loading", "Connecting", "noop", {})], [], "Connecting to server...", 0)
    if not _ensure_paired(settings):
        _render("Tater Tube", settings.get("server_url"), [
            _row("Pair/Settings", "Enter server URL and PIN", "settings", {}),
        ], [], "Pair this Xbox to Tater Tube Server.", 0)
        return

    try:
        catalog = _request_json(settings, "/api/tater/usenet/catalog")
    except Exception as exc:
        _render("Tater Tube", settings.get("server_url"), [
            _row("Pair/Settings", "Check URL or pairing", "settings", {}),
        ], [], "Catalog failed: %s" % exc, 0)
        return

    rows = []
    categories = catalog.get("categories") if isinstance(catalog, dict) else []
    if isinstance(categories, list):
        for category in categories:
            if isinstance(category, dict):
                rows.append(_category_row(category))

    try:
        music = _request_json(settings, "/api/tater/music/libraries")
        libraries = music.get("libraries") if isinstance(music, dict) else []
        if isinstance(libraries, list) and libraries:
            rows.append(_row("Music", "LOCAL", "music_root", {}))
    except Exception:
        pass

    if not rows:
        rows = [_row("Pair/Settings", "No catalog sections found", "settings", {})]
    _render("Tater Tube", settings.get("server_url"), rows, [], "Local / Stream / Music / Tube TV", 0)


def _load_items(settings, data, title=None):
    category_id = data.get("categoryId") or data.get("id")
    if not category_id:
        raise Exception("Missing category")
    params = {
        "category_id": category_id,
        "title": title or data.get("title") or "Tater Tube",
    }
    if data.get("sourceIndex") is not None:
        params["source"] = data.get("sourceIndex")
    elif data.get("source") is not None:
        params["source"] = data.get("source")
    if data.get("path"):
        params["path"] = data.get("path")

    result = _request_json(settings, "/api/tater/usenet/items", params)
    items = result.get("items") if isinstance(result, dict) else []
    rows = []
    if isinstance(items, list):
        rows = [_item_row(item) for item in items if isinstance(item, dict)]
    return result.get("title") or params["title"], rows


def _load_continue(settings):
    result = _request_json(settings, "/api/tater/playstate/continue")
    if isinstance(result, dict):
        items = result.get("items") or result.get("rows") or []
    else:
        items = []
    rows = []
    if isinstance(items, list):
        rows = [_item_row(item) for item in items if isinstance(item, dict)]
    return rows


def _load_discover(settings, data):
    result = _request_json(settings, "/api/tater/usenet/discover", {"catalog": data.get("id")})
    items = result.get("items") if isinstance(result, dict) else []
    rows = []
    if isinstance(items, list):
        rows = [_item_row(item) for item in items if isinstance(item, dict)]
    return result.get("title") or data.get("title") or "Discover", rows


def _load_trending(settings, data):
    result = _request_json(settings, "/api/tater/usenet/trending", {
        "category": data.get("category"),
        "period": data.get("time"),
    })
    items = result.get("items") if isinstance(result, dict) else []
    rows = []
    if isinstance(items, list):
        rows = [_item_row(item) for item in items if isinstance(item, dict)]
    return result.get("title") or data.get("title") or "Trending", rows


def _search_stream(settings, query):
    result = _request_json(settings, "/api/tater/usenet/search", {"q": query})
    items = result.get("items") if isinstance(result, dict) else []
    rows = []
    if isinstance(items, list):
        rows = [_item_row(item) for item in items if isinstance(item, dict)]
    return result.get("title") or ("Search: " + query), rows


def _music_root(settings):
    result = _request_json(settings, "/api/tater/music/libraries")
    libraries = result.get("libraries") if isinstance(result, dict) else []
    rows = []
    if isinstance(libraries, list):
        rows = [_item_row(item) for item in libraries if isinstance(item, dict)]
    return rows


def _music_albums(settings, item):
    category_id = _text(item.get("categoryId")).replace("local:", "", 1)
    result = _request_json(settings, "/api/tater/music/albums", {"category_id": category_id})
    albums = result.get("albums") if isinstance(result, dict) else []
    rows = []
    if isinstance(albums, list):
        rows = [_item_row(album) for album in albums if isinstance(album, dict)]
    return rows


def _music_tracks(settings, item):
    album_id = item.get("key") or item.get("ratingKey")
    result = _request_json(settings, "/api/tater/music/tracks", {"album_id": album_id})
    tracks = result.get("tracks") if isinstance(result, dict) else []
    rows = []
    if isinstance(tracks, list):
        rows = [_item_row(track) for track in tracks if isinstance(track, dict)]
    return rows


def _tv_root(settings):
    channels = _request_tv_channels(settings)
    rows = []
    if isinstance(channels, list):
        for channel in channels:
            if not isinstance(channel, dict):
                continue
            number = channel.get("number") or ""
            title = channel.get("title") or ("Channel " + _text(number))
            detail = "CH " + _text(number) if number else "LIVE"
            rows.append(_row(title, detail, "play_tv", {
                "number": number,
                "title": title,
                "streamUrl": channel.get("streamUrl"),
            }))
    return rows


def _tv_channel_url(settings, channel):
    url = channel.get("streamUrl")
    if url:
        parts = urlparse.urlparse(url)
        if parts.path.endswith("/playlist.m3u8"):
            path = parts.path[:-len("/playlist.m3u8")] + "/stream"
            return urlparse.urlunparse((parts.scheme, parts.netloc, path, parts.params, parts.query, parts.fragment))
        return url
    number = _text(channel.get("number")).strip()
    if number:
        path = "/api/tater/tv/channel/%s/stream" % urllib.quote(number, safe="")
        return _build_url(settings, path, {"player_token": settings.get("token")})
    return url


def _next_bumper(settings, source):
    if not source:
        return None
    try:
        data = _request_json(settings, "/api/tater/bumpers/next", {"source": source})
        if isinstance(data, dict) and data.get("enabled"):
            item = data.get("item")
            if isinstance(item, dict) and item.get("streamUrl"):
                return item
    except Exception as exc:
        _log("Bumper skipped: %s" % exc)
    return None


def _bumper_source_for_local(item):
    media_type = _text(item.get("mediaType")).lower()
    if media_type in ("show", "series", "episode", "tv"):
        return "local_series"
    if media_type in ("movie", "video", ""):
        return "local_movies"
    return ""


def _bumper_source_for_nzb(item):
    media_type = _text(item.get("mediaType") or item.get("category")).lower()
    if media_type in ("audio", "music"):
        return ""
    return "nzb_movies"


def _play_video_entries(settings, media_urls, title):
    urls = []
    for url in media_urls:
        url = _text(url).strip()
        if url:
            urls.append(url)
    if not urls:
        _dialog_error("This item does not have a playable stream URL.")
        return

    _stop_server_streams(settings)
    xbmc.executebuiltin("SetVolume(100,false)")
    player = xbmc.Player()
    if len(urls) > 1:
        playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        playlist.clear()
        for url in urls:
            playlist.add(url)
        player.play(playlist)
    else:
        player.play(urls[0])
    _start_video_cleanup_watcher(settings)
    _notify("Playing %s" % title)


def _play_video_url(settings, url, title, bumper_source=""):
    media_url = _with_xbox_transcode(_absolute_url(settings, url))
    if not media_url:
        _dialog_error("This item does not have a playable stream URL.")
        return

    urls = []
    bumper = _next_bumper(settings, bumper_source)
    if bumper and bumper.get("streamUrl"):
        bumper_url = _with_xbox_transcode(_absolute_url(settings, bumper.get("streamUrl")))
        urls.append(bumper_url)
    urls.append(media_url)
    _play_video_entries(settings, urls, title)


def _play_audio_url(settings, url, title):
    media_url = _absolute_url(settings, url)
    if not media_url:
        _dialog_error("This track does not have a playable stream URL.")
        return
    xbmc.executebuiltin("SetVolume(100,false)")
    try:
        playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
        playlist.clear()
        playlist.add(media_url)
        xbmc.Player().play(playlist)
    except Exception:
        xbmc.Player().play(media_url)
    _notify("Playing %s" % title)


def _play_nzb(settings, item):
    progress = xbmcgui.DialogProgress()
    try:
        progress.create("Tater Tube", "Preparing stream...", _clean(item.get("title"), 60))
    except Exception:
        progress = None

    try:
        result = _request_json(settings, "/api/tater/usenet/play", method="POST", payload={
            "nzb_url": item.get("nzbUrl"),
            "title": item.get("title"),
            "category": item.get("category") or item.get("mediaType") or "",
            "timeout": 300,
        }, timeout=STREAM_TIMEOUT_SECONDS)
    finally:
        try:
            if progress:
                progress.close()
        except Exception:
            pass

    streams = result.get("streams") if isinstance(result, dict) else []
    if not isinstance(streams, list) or not streams:
        _dialog_error("No playable files were returned.")
        return
    if len(streams) == 1:
        stream = streams[0]
        _play_video_url(settings, stream.get("url"), stream.get("title") or item.get("title"), _bumper_source_for_nzb(item))
        return

    rows = []
    for stream in streams:
        if isinstance(stream, dict):
            rows.append(_row(stream.get("title") or stream.get("name"), "STREAM", "play_stream", {
                "url": stream.get("url"),
                "title": stream.get("title") or stream.get("name"),
                "bumperSource": _bumper_source_for_nzb(item),
            }))
    _render_child(item.get("title") or "Choose file", "Select a playable file", rows, "Stream is ready.")


def _play_tv(settings, channel):
    try:
        urls = _tv_playlist_urls(settings, channel)
        if urls:
            _play_video_entries(settings, urls, channel.get("title") or "Tube TV")
            return
    except Exception as exc:
        _log("Tube TV playlist build failed: %s" % exc)
    url = _tv_channel_url(settings, channel)
    _play_video_url(settings, url, channel.get("title") or "Tube TV", "")


def _same_episode_group(first, second):
    if _text(first.get("mediaType")).lower() != "episode":
        return False
    if _text(second.get("mediaType")).lower() != "episode":
        return False
    if _text(first.get("categoryId")) != _text(second.get("categoryId")):
        return False
    if _text(first.get("sourceIndex")) != _text(second.get("sourceIndex")):
        return False
    return os.path.dirname(_text(first.get("path"))) == os.path.dirname(_text(second.get("path")))


def _episode_playlist_urls(settings, rows, selected_row):
    selected = selected_row.get("data") or {}
    if _text(selected.get("mediaType")).lower() != "episode":
        return []

    start_index = -1
    selected_path = _text(selected.get("path"))
    for index, row in enumerate(rows):
        data = row.get("data") or {}
        if row.get("action") == "play_local" and _text(data.get("path")) == selected_path:
            start_index = index
            break
    if start_index < 0:
        return []

    urls = []
    for row in rows[start_index:]:
        if row.get("action") != "play_local":
            break
        data = row.get("data") or {}
        if not _same_episode_group(selected, data):
            break
        url = data.get("streamUrl")
        if url:
            urls.append(_with_xbox_transcode(_absolute_url(settings, url)))
    return urls


def _play_local(settings, item, rows, selected_row):
    urls = _episode_playlist_urls(settings, rows, selected_row)
    if len(urls) > 1:
        bumper = _next_bumper(settings, _bumper_source_for_local(item))
        if bumper and bumper.get("streamUrl"):
            urls.insert(0, _with_xbox_transcode(_absolute_url(settings, bumper.get("streamUrl"))))
        _play_video_entries(settings, urls, item.get("title"))
        return
    _play_video_url(settings, item.get("streamUrl"), item.get("title"), _bumper_source_for_local(item))


def _handle_select(index):
    settings = _load_settings()
    state = _load_state()
    visible = state.get("visible_rows", [])
    if index < 1 or index > len(visible):
        return
    row = visible[index - 1]
    action = row.get("action")
    data = row.get("data") or {}

    try:
        if action == "noop":
            return
        if action == "settings":
            _handle_settings()
            return
        if action == "page":
            _render(state.get("title"), state.get("subtitle"), state.get("rows", []), state.get("stack", []), state.get("status", ""), data.get("page", 0))
            return
        if action == "children":
            rows = [_category_row(child) for child in data.get("children", []) if isinstance(child, dict)]
            _render_child(data.get("title") or row.get("title"), "Browse Tater Tube", rows, "")
            return
        if action == "load_items":
            title, rows = _load_items(settings, data, row.get("title"))
            _render_child(title, "Select an item to play", rows, "%d items" % len(rows))
            return
        if action == "load_continue":
            rows = _load_continue(settings)
            _render_child("Continue Watching", "Resume local playback", rows, "%d items" % len(rows))
            return
        if action == "load_discover":
            title, rows = _load_discover(settings, data)
            _render_child(title, "Choose a title to search", rows, "%d items" % len(rows))
            return
        if action == "load_trending":
            title, rows = _load_trending(settings, data)
            _render_child(title, "Select an item to stream", rows, "%d items" % len(rows))
            return
        if action == "search_stream":
            query = _keyboard("", "Search Tater Tube Stream")
            if query:
                title, rows = _search_stream(settings, query)
                _render_child(title, "Select an item to stream", rows, "%d items" % len(rows))
            return
        if action == "search_query":
            query = data.get("searchQuery") or data.get("title")
            title, rows = _search_stream(settings, query)
            _render_child(title, "Select an item to stream", rows, "%d items" % len(rows))
            return
        if action == "music_root":
            rows = _music_root(settings)
            _render_child("Music", "Choose a library", rows, "%d libraries" % len(rows))
            return
        if action == "music_albums":
            rows = _music_albums(settings, data)
            _render_child(row.get("title"), "Choose an album", rows, "%d albums" % len(rows))
            return
        if action == "music_tracks":
            rows = _music_tracks(settings, data)
            _render_child(row.get("title"), "Choose a track", rows, "%d tracks" % len(rows))
            return
        if action == "tv_root":
            rows = _tv_root(settings)
            _render_child("Tube TV", "Choose a channel", rows, "%d channels" % len(rows))
            return
        if action == "play_tv":
            _play_tv(settings, data)
            return
        if action == "play_local":
            _play_local(settings, data, state.get("rows", []), row)
            return
        if action == "play_audio":
            _play_audio_url(settings, data.get("streamUrl"), data.get("title"))
            return
        if action == "play_nzb":
            _play_nzb(settings, data)
            return
        if action == "play_stream":
            _play_video_url(settings, data.get("url"), data.get("title"), data.get("bumperSource"))
            return
    except Exception as exc:
        _dialog_error(_text(exc))


def _handle_back():
    state = _load_state()
    stack = state.get("stack", [])
    if stack:
        previous = stack.pop()
        _render(previous.get("title"), previous.get("subtitle"), previous.get("rows", []), stack, previous.get("status", ""), previous.get("page", 0))
        return
    _open_root()


def _handle_settings():
    settings = _load_settings()
    dialog = xbmcgui.Dialog()
    choice = dialog.select("Tater Tube", [
        "Pair with PIN",
        "Change server URL",
        "Clear pairing",
        "Refresh catalog",
    ])
    if choice == 0:
        if _pair_flow(settings, True):
            _open_root()
    elif choice == 1:
        server_url = _keyboard(settings.get("server_url") or DEFAULT_SERVER_URL, "Tater Tube Server URL")
        if server_url is not None:
            settings["server_url"] = server_url.rstrip("/")
            settings["token"] = ""
            _save_settings(settings)
            _notify("Server saved. Pair again.")
            _open_root()
    elif choice == 2:
        settings["token"] = ""
        settings["player_id"] = ""
        _save_settings(settings)
        _notify("Pairing cleared")
        _open_root()
    elif choice == 3:
        _open_root()


def main():
    action = "Open"
    if len(sys.argv) > 1:
        action = sys.argv[1]
    if action.startswith("Select"):
        try:
            if action == "SelectFocused":
                _handle_select(_selected_list_index())
            else:
                _handle_select(int(action.replace("Select", "", 1)))
        except Exception as exc:
            _dialog_error(_text(exc))
    elif action == "Back":
        _handle_back()
    elif action == "Settings":
        _handle_settings()
    else:
        _open_root()


if __name__ == "__main__":
    main()
