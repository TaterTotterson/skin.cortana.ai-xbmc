# -*- coding: utf-8 -*-
#
# Cortana Chat for XBMC4Xbox
# Cortana-style Dialog.select chat:
#   - Ask Cortana (keyboard)
#   - Quick Ask preset questions
#   - Chat history (new messages at TOP)
#   - Popup window with reply
#

import os
import socket
import sys
import threading
import time
import wave
import xbmc
import xbmcgui
import urllib2

# JSON compatibility
try:
    import json
except ImportError:
    import simplejson as json

try:
    basestring
except NameError:
    basestring = str


# --------------------------
# Config
# --------------------------

# Now talks to the XBMC portal on the standard Tater port.
CORTANA_API_URL = "http://10.4.20.210:8501/api/portals/xbmc_portal/api/tater-xbmc/v1/message"
HTTP_TIMEOUT_SECONDS = 15
DEFAULT_API_KEY = ""
SETTINGS_FILE = os.path.join(xbmc.translatePath('special://profile'), 'cortana_chat_settings.json')
CORTANA_STATE_FILE = os.path.join(xbmc.translatePath('special://profile'), 'cortana_overlay_state.json')
CORTANA_OVERLAY_WINDOW_ID = 9016
CORTANA_PROPERTY_WINDOW_ID = 10000
TTS_ENABLED = True
TTS_MAX_CHARS = 900
TTS_CACHE_DIR = os.path.join(xbmc.translatePath('special://profile'), 'cortana_tts')
TTS_PAUSE_BGVIDEO_ENABLED = True
BG_VIDEO_PATH = "E:\\BGVideo\\BGVideo.avi"
TTS_PLAY_EXTRA_SECONDS = 0.35
TTS_LOCK = threading.Lock()

# Shared quick-ask prompts (used by both full chat and QuickAsks-only mode)
QUICK_ASK_ITEMS = [
    "Recommend an original Xbox game to play",
    "What's a hidden gem on the original Xbox?",
    "Give me a fun fact about the original Xbox",
    "Tell me about yourself Cortana?",
    "Recommend a multiplayer original Xbox game for tonight",
    "Turn the lights in the game room to blue",
    "What tools do you have available?",
    "What is your real name?",
]


def _log(msg):
    try:
        xbmc.log("CortanaChat: %s" % msg, xbmc.LOGNOTICE)
    except Exception:
        try:
            print("CortanaChat: %s" % msg)
        except Exception:
            pass


def _tater_base_url():
    marker = "/api/portals/"
    idx = CORTANA_API_URL.find(marker)
    if idx >= 0:
        return CORTANA_API_URL[:idx].rstrip("/")
    parts = CORTANA_API_URL.split("/")
    if len(parts) >= 3:
        return "/".join(parts[:3]).rstrip("/")
    return CORTANA_API_URL.rstrip("/")


def _cortana_api_base_url():
    marker = "/message"
    idx = CORTANA_API_URL.rfind(marker)
    if idx >= 0:
        return CORTANA_API_URL[:idx].rstrip("/")
    return CORTANA_API_URL.rstrip("/")


def _tts_preview_url():
    return _cortana_api_base_url() + "/tts.wav"


def _absolute_tater_url(url):
    value = str(url or "").strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/"):
        return _tater_base_url() + value
    if value:
        return _tater_base_url() + "/" + value
    return ""


def _request_headers(content_type=None):
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type

    api_key = _get_api_key()
    if api_key:
        headers["X-Tater-Token"] = api_key

    return headers


def _extract_reply(obj):
    if not isinstance(obj, dict):
        return ""

    if "response" in obj and isinstance(obj["response"], basestring):
        return obj["response"].strip()

    for key in ("reply", "assistant", "text", "message"):
        if key in obj and isinstance(obj[key], basestring):
            return obj[key].strip()

    return ""


def _clean_quick_ask(value):
    text = str(value or "").strip().replace("\r", " ").replace("\n", " ")
    while "  " in text:
        text = text.replace("  ", " ")
    text = text.strip(" -\t")
    if len(text) > 64:
        text = text[:64].rsplit(" ", 1)[0].strip()
    return text


def _button_label(text, limit=40):
    label = _clean_quick_ask(text)
    if len(label) > limit:
        label = label[:limit].rsplit(" ", 1)[0].strip()
    return label


def _fallback_quick_asks(user_text="", reply_text=""):
    combined = ("%s %s" % (user_text, reply_text)).lower()
    if "light" in combined:
        return ["Set the lights to blue", "Turn the lights off", "What else can you control?"]
    if "game" in combined or "xbox" in combined:
        return ["Tell me more about that game", "Recommend another game", "Find a multiplayer game"]
    if "news" in combined:
        return ["Tell me the top story", "Any Insignia updates?", "Find more Xbox news"]
    return ["Tell me more", "What can you do next?", "Give me a quick suggestion"]


def _extract_quick_asks(obj, user_text="", reply_text=""):
    asks = []
    if isinstance(obj, dict):
        values = obj.get("quick_asks") or obj.get("suggestions") or obj.get("replies") or []
        if isinstance(values, list):
            for item in values:
                ask = _clean_quick_ask(item)
                if ask and ask not in asks:
                    asks.append(ask)
                if len(asks) >= 3:
                    break

    if len(asks) < 3:
        for fallback in _fallback_quick_asks(user_text, reply_text):
            ask = _clean_quick_ask(fallback)
            if ask and ask not in asks:
                asks.append(ask)
            if len(asks) >= 3:
                break

    return asks[:3]


def _cortana_result(reply="", quick_asks=None, response_obj=None):
    return {
        "reply": str(reply or "").strip(),
        "quick_asks": quick_asks or [],
        "response_obj": response_obj,
    }


def _extract_tts_url(obj):
    if not isinstance(obj, dict):
        return ""

    for key in ("tts_url", "audio_url", "speech_url", "voice_url", "wav_url"):
        value = obj.get(key)
        if isinstance(value, basestring) and value.strip():
            return _absolute_tater_url(value)

    audio = obj.get("audio")
    if isinstance(audio, dict):
        for key in ("url", "source_url", "media_url", "tts_url", "wav_url"):
            value = audio.get(key)
            if isinstance(value, basestring) and value.strip():
                return _absolute_tater_url(value)

    tts = obj.get("tts")
    if isinstance(tts, dict):
        for key in ("url", "source_url", "media_url", "audio_url", "wav_url"):
            value = tts.get(key)
            if isinstance(value, basestring) and value.strip():
                return _absolute_tater_url(value)

    return ""


def _ensure_tts_cache_dir():
    try:
        if not os.path.isdir(TTS_CACHE_DIR):
            os.makedirs(TTS_CACHE_DIR)
        return True
    except Exception as e:
        _log("TTS cache setup failed: %s" % e)
        return False


def _cleanup_old_tts_files():
    try:
        if not os.path.isdir(TTS_CACHE_DIR):
            return

        now = time.time()
        for name in os.listdir(TTS_CACHE_DIR):
            if not name.startswith("cortana_reply_") or not name.endswith(".wav"):
                continue

            path = os.path.join(TTS_CACHE_DIR, name)
            try:
                if now - os.path.getmtime(path) > 600:
                    os.remove(path)
            except Exception:
                pass
    except Exception as e:
        _log("TTS cleanup failed: %s" % e)


def _tts_file_path():
    if not _ensure_tts_cache_dir():
        return ""

    _cleanup_old_tts_files()
    stamp = int(time.time() * 1000)
    return os.path.join(TTS_CACHE_DIR, "cortana_reply_%s.wav" % stamp)


def _wav_duration_seconds(path):
    source = None

    try:
        source = wave.open(path, "rb")
        rate = float(source.getframerate() or 0)
        if rate <= 0:
            return 0.0
        return float(source.getnframes()) / rate
    except Exception as e:
        _log("Unable to read TTS WAV duration: %s" % e)
        return 0.0
    finally:
        try:
            if source:
                source.close()
        except Exception:
            pass


def _normalize_path(path):
    try:
        return str(path or "").replace("/", "\\").lower()
    except Exception:
        return ""


def _get_playing_file(player):
    try:
        return player.getPlayingFile()
    except Exception:
        return ""


def _is_background_video_file(path):
    return _normalize_path(path) == _normalize_path(BG_VIDEO_PATH)


def _toggle_player_pause():
    try:
        player = xbmc.Player()
        if hasattr(player, "pause"):
            player.pause()
        else:
            xbmc.executebuiltin("PlayerControl(Pause)")
        return True
    except Exception as e:
        _log("Player pause toggle failed: %s" % e)
        return False


def _pause_background_video_for_tts():
    if not TTS_PAUSE_BGVIDEO_ENABLED:
        return False

    try:
        player = xbmc.Player()
        if not player.isPlaying():
            return False

        current_file = _get_playing_file(player)
        if not _is_background_video_file(current_file):
            if current_file:
                _log("TTS leaving current media playing: %s" % current_file)
            return False

        if _toggle_player_pause():
            _log("Paused background video for TTS")
            time.sleep(0.15)
            return True

    except Exception as e:
        _log("Unable to pause background video for TTS: %s" % e)

    return False


def _resume_background_video_after_tts(paused):
    if not paused:
        return

    try:
        player = xbmc.Player()
        current_file = _get_playing_file(player)
        if current_file and not _is_background_video_file(current_file):
            _log("Not resuming background video; another file is active: %s" % current_file)
            return

        if _toggle_player_pause():
            _log("Resumed background video after TTS")

    except Exception as e:
        _log("Unable to resume background video after TTS: %s" % e)


def _save_tts_wav(audio):
    if not audio:
        return ""

    path = _tts_file_path()
    if not path:
        return ""

    try:
        f = open(path, "wb")
        try:
            f.write(audio)
        finally:
            f.close()
        _log("Saved TTS WAV: %s (%s bytes)" % (path, len(audio)))
        return path
    except Exception as e:
        _log("Unable to save TTS WAV: %s" % e)
        return ""


def _fetch_tts_from_url(url):
    audio_url = _absolute_tater_url(url)
    if not audio_url:
        return ""

    try:
        req = urllib2.Request(audio_url, None, _request_headers())
        socket.setdefaulttimeout(HTTP_TIMEOUT_SECONDS)
        resp = urllib2.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS)
        try:
            return _save_tts_wav(resp.read())
        finally:
            try:
                resp.close()
            except Exception:
                pass
    except Exception as e:
        _log("TTS URL fetch failed: %s" % e)
        return ""


def _synthesize_tts(reply):
    text = str(reply or "").strip()
    if not text:
        return ""

    if len(text) > TTS_MAX_CHARS:
        text = text[:TTS_MAX_CHARS].rsplit(" ", 1)[0].strip()

    payload = {"text": text}

    try:
        data = json.dumps(payload)
    except Exception as e:
        _log("TTS JSON error: %s" % e)
        return ""

    try:
        req = urllib2.Request(_tts_preview_url(), data, _request_headers("application/json"))
        socket.setdefaulttimeout(HTTP_TIMEOUT_SECONDS)
        resp = urllib2.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS)
        try:
            return _save_tts_wav(resp.read())
        finally:
            try:
                resp.close()
            except Exception:
                pass
    except Exception as e:
        _log("TTS synthesis failed: %s" % e)
        return ""


def _play_tts_file(path):
    if not path or not os.path.exists(path):
        return

    TTS_LOCK.acquire()
    paused_bgvideo = False
    try:
        duration = _wav_duration_seconds(path)
        paused_bgvideo = _pause_background_video_for_tts()

        _log("Playing TTS WAV: %s" % path)
        if hasattr(xbmc, "playSFX"):
            xbmc.playSFX(path)
        else:
            xbmc.executebuiltin('PlaySFX("%s")' % path)

        if duration > 0:
            time.sleep(duration + TTS_PLAY_EXTRA_SECONDS)

        _resume_background_video_after_tts(paused_bgvideo)
        paused_bgvideo = False

    except Exception as e:
        _log("TTS playback failed: %s" % e)
    finally:
        _resume_background_video_after_tts(paused_bgvideo)
        try:
            TTS_LOCK.release()
        except Exception:
            pass


def _play_reply_tts(reply, response_obj=None):
    if not TTS_ENABLED:
        return

    text = str(reply or "").strip()
    if not text:
        return

    if text.startswith("HTTP ") or text.startswith("URL error:") or text.startswith("Error talking to Cortana:"):
        return

    path = ""
    tts_url = _extract_tts_url(response_obj)
    if tts_url:
        path = _fetch_tts_from_url(tts_url)

    if not path:
        path = _synthesize_tts(text)

    _play_tts_file(path)


def _play_reply_tts_async(reply, response_obj=None):
    if not TTS_ENABLED:
        return

    try:
        worker = threading.Thread(target=_play_reply_tts, args=(reply, response_obj))
        worker.setDaemon(True)
        worker.start()
    except Exception as e:
        _log("Unable to start TTS worker: %s" % e)
        _play_reply_tts(reply, response_obj)


def _format_popup(text, width=60):
    """
    Make Cortana replies look good in Dialog.ok():
    - Convert escaped newlines to real ones
    - If still one long line, insert line breaks every ~width chars
    """
    if not text:
        return ""

    # Normalize newlines + unescape \n
    clean = text.replace("\r\n", "\n").replace("\\n", "\n")

    # If Cortana already sent real newlines, honor them
    if "\n" in clean:
        return clean

    # Otherwise, hard-wrap long text into multiple lines
    if len(clean) <= width:
        return clean

    words = clean.split(" ")
    lines = []
    current = []
    count = 0

    for w in words:
        wlen = len(w)
        # +1 for the space we add
        if count + wlen + (1 if current else 0) > width:
            if current:
                lines.append(" ".join(current))
            current = [w]
            count = wlen
        else:
            current.append(w)
            count += wlen + (1 if current else 0)

    if current:
        lines.append(" ".join(current))

    return "\n".join(lines)


def _show_popup(dialog, title, text):
    """
    XBMC4Xbox Dialog.ok supports:
        ok(heading, line1, line2='', line3='')
    Newlines inside a single string don't always render correctly,
    so we split into up to 3 lines and pass them separately.
    """
    formatted = _format_popup(text, width=60)
    parts = formatted.split("\n")

    line1 = parts[0] if len(parts) > 0 else ""
    line2 = parts[1] if len(parts) > 1 else ""
    line3 = parts[2] if len(parts) > 2 else ""

    dialog.ok(title, line1, line2, line3)


def _load_overlay_state():
    try:
        if not os.path.exists(CORTANA_STATE_FILE):
            return {"reply": "", "quick_asks": _fallback_quick_asks(), "history": []}
        f = open(CORTANA_STATE_FILE, "r")
        try:
            raw = f.read()
        finally:
            f.close()
        parsed = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            parsed = {}
        return {
            "reply": str(parsed.get("reply") or ""),
            "quick_asks": _extract_quick_asks(parsed, "", str(parsed.get("reply") or "")),
            "history": parsed.get("history") if isinstance(parsed.get("history"), list) else [],
        }
    except Exception as e:
        _log("Overlay state load failed: %s" % e)
        return {"reply": "", "quick_asks": _fallback_quick_asks(), "history": []}


def _save_overlay_state(state):
    try:
        folder = os.path.dirname(CORTANA_STATE_FILE)
        if folder and not os.path.exists(folder):
            os.makedirs(folder)
        f = open(CORTANA_STATE_FILE, "w")
        try:
            f.write(json.dumps(state))
        finally:
            f.close()
        return True
    except Exception as e:
        _log("Overlay state save failed: %s" % e)
        return False


def _overlay_lines(text, width=40, max_lines=5):
    clean = _format_popup(text, width=width)
    lines = []
    for line in clean.split("\n"):
        line = str(line or "").strip()
        if line:
            lines.append(line)
        if len(lines) >= max_lines:
            break
    while len(lines) < max_lines:
        lines.append("")
    return lines[:max_lines]


def _set_overlay_property(name, value):
    text = str(value or "")
    try:
        xbmcgui.Window(CORTANA_PROPERTY_WINDOW_ID).setProperty(name, text)
    except Exception as e:
        _log("Overlay property failed for %s: %s" % (name, e))


def _refresh_overlay_properties(state):
    reply = str(state.get("reply") or "")
    quick_asks = list(state.get("quick_asks") or [])[:3]
    history = list(state.get("history") or [])[:4]

    for idx, line in enumerate(_overlay_lines(reply), 1):
        _set_overlay_property("Cortana.Reply%s" % idx, line)

    for idx in range(3):
        text = quick_asks[idx] if idx < len(quick_asks) else _fallback_quick_asks()[idx]
        _set_overlay_property("Cortana.Quick%s" % (idx + 1), _button_label(text, limit=38))

    for idx in range(4):
        line = history[idx] if idx < len(history) else ""
        _set_overlay_property("Cortana.History%s" % (idx + 1), _button_label(line, limit=55))


def _open_cortana_skin_overlay(state):
    _save_overlay_state(state)
    _refresh_overlay_properties(state)
    _log("Opening Cortana skin overlay window %s" % CORTANA_OVERLAY_WINDOW_ID)
    xbmc.executebuiltin("ActivateWindow(%s)" % CORTANA_OVERLAY_WINDOW_ID)
    time.sleep(0.15)
    _refresh_overlay_properties(state)


def _show_result_on_skin_overlay(result, history, user_text=""):
    reply = str(result.get("reply") or "")
    quick_asks = list(result.get("quick_asks") or _fallback_quick_asks(user_text, reply))[:3]
    state = {"reply": reply, "quick_asks": quick_asks, "history": list(history or [])[:60]}
    _open_cortana_skin_overlay(state)
    _play_reply_tts(reply, result.get("response_obj"))
    return state


def _load_chat_settings():
    settings = {"api_key": DEFAULT_API_KEY}
    try:
        if not os.path.exists(SETTINGS_FILE):
            return settings
        f = open(SETTINGS_FILE, "r")
        try:
            raw = f.read()
        finally:
            f.close()
        if not raw:
            return settings
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            settings["api_key"] = str(parsed.get("api_key") or "").strip()
    except Exception as e:
        _log("Settings load failed: %s" % e)
    return settings


def _save_chat_settings(settings):
    try:
        folder = os.path.dirname(SETTINGS_FILE)
        if folder and not os.path.exists(folder):
            os.makedirs(folder)
        f = open(SETTINGS_FILE, "w")
        try:
            f.write(json.dumps(settings))
        finally:
            f.close()
        return True
    except Exception as e:
        _log("Settings save failed: %s" % e)
        return False


def _get_api_key():
    settings = _load_chat_settings()
    return str(settings.get("api_key") or "").strip()


def _set_api_key(dialog):
    settings = _load_chat_settings()
    current = str(settings.get("api_key") or "").strip()

    kb = xbmc.Keyboard(current, "Set Tater API Key (blank clears)", True)
    kb.doModal()
    if not kb.isConfirmed():
        return

    new_key = kb.getText().strip()
    settings["api_key"] = new_key

    if _save_chat_settings(settings):
        if new_key:
            xbmc.executebuiltin("Notification(Cortana Chat, API key saved, 2200)")
        else:
            xbmc.executebuiltin("Notification(Cortana Chat, API key cleared, 2200)")
    else:
        dialog.ok("Cortana Chat", "Failed to save API key.")


def call_cortana_result(message, auto_tts=True):
    """
    Send a message to the XBMC bridge endpoint and return reply text plus quick asks.
    """

    profile_name = xbmc.getInfoLabel("System.ProfileName") or "XBMC4Xbox"

    payload = {
        "text": message,
        "user_id": profile_name,
        "session_id": "xbmc_%s" % profile_name,
        "device_id": "xbmc4xbox",
        "area_id": "xbmc",
        "include_tts": True,
        "tts_format": "wav",
        "include_quick_asks": True,
    }

    try:
        data = json.dumps(payload)
    except Exception as e:
        return _cortana_result("JSON error: %s" % e)

    _log("Sending to Cortana URL: %s" % CORTANA_API_URL)
    _log("Payload: %s" % data)

    headers = _request_headers("application/json")

    req = urllib2.Request(
        CORTANA_API_URL,
        data,
        headers
    )

    try:
        socket.setdefaulttimeout(HTTP_TIMEOUT_SECONDS)
        resp = urllib2.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS)
        raw = resp.read()
        _log("Raw response: %s" % raw)

        try:
            obj = json.loads(raw)
        except Exception:
            return _cortana_result(raw.strip(), _fallback_quick_asks(message, raw))

        reply = _extract_reply(obj)
        if reply:
            if auto_tts:
                _play_reply_tts_async(reply, obj)
            return _cortana_result(reply, _extract_quick_asks(obj, message, reply), obj)

        fallback_reply = json.dumps(obj)
        return _cortana_result(fallback_reply, _fallback_quick_asks(message, fallback_reply))

    except urllib2.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = ""

        if e.code in (401, 403):
            if _get_api_key():
                hint = "Invalid API key. Open Cortana Chat and choose Set API Key."
            else:
                hint = "API key required. Open Cortana Chat and choose Set API Key."
            if body:
                return _cortana_result("HTTP %s\n%s\n%s" % (e.code, body, hint))
            return _cortana_result("HTTP %s\n%s" % (e.code, hint))

        return _cortana_result("HTTP %s\n%s" % (e.code, body))

    except urllib2.URLError as e:
        return _cortana_result("URL error: %s" % getattr(e, "reason", e))

    except Exception as e:
        return _cortana_result("Error talking to Cortana: %s" % e)


def call_cortana(message):
    """
    Compatibility wrapper for the older popup flows.
    """
    return call_cortana_result(message, auto_tts=True).get("reply", "")


def display_cortana_chat():
    """
    Full Cortana chat experience:
    - Skin XML Cortana overlay
    - Dynamic quick replies from Tater
    - Ask Cortana keyboard action
    - Preset quick asks, news, and settings actions
    """
    history = []

    try:
        greeting_prompt = (
            "Greet the user as Cortana from the original Xbox. "
            "Ask if they want help controlling lights, finding a game, or using tools. "
            "Use one warm, confident sentence under 22 words with no markdown."
        )
        current = call_cortana_result(greeting_prompt, auto_tts=False)
    except Exception as e:
        _log("Startup greeting failed: %s" % e)
        current = _cortana_result("Cortana is online. Ask me about lights, games, or tools.")

    if current.get("reply"):
        history.insert(0, "Cortana: %s" % current.get("reply"))

    _show_result_on_skin_overlay(current, history)


def _send_overlay_message(text):
    text = str(text or "").strip()
    if not text:
        return

    state = _load_overlay_state()
    history = list(state.get("history") or [])
    xbmc.executebuiltin("Notification(Cortana Chat, Working..., 1500)")
    result = call_cortana_result(text, auto_tts=False)

    history.insert(0, "Cortana: %s" % result.get("reply", ""))
    history.insert(0, "You:     %s" % text)
    if len(history) > 60:
        history = history[:60]

    _show_result_on_skin_overlay(result, history, text)


def handle_overlay_action(action):
    dialog = xbmcgui.Dialog()
    token = str(action or "").lower()
    state = _load_overlay_state()

    if token in ("quick1", "quick2", "quick3"):
        idx = int(token[-1]) - 1
        quick_asks = list(state.get("quick_asks") or [])
        if 0 <= idx < len(quick_asks):
            _send_overlay_message(quick_asks[idx])
        return

    if token == "ask":
        kb = xbmc.Keyboard("", "Talk to Cortana", False)
        kb.doModal()
        if kb.isConfirmed():
            _send_overlay_message(kb.getText())
        else:
            _open_cortana_skin_overlay(state)
        return

    if token == "presets":
        q_choice = dialog.select("Preset Quick Asks", QUICK_ASK_ITEMS)
        if q_choice != -1:
            _send_overlay_message(QUICK_ASK_ITEMS[q_choice])
        else:
            _open_cortana_skin_overlay(state)
        return

    if token in ("news", "overlaynews"):
        _send_overlay_message(
            "What's the latest OG Xbox news? "
            "Use the web_search tool to look it up first, then summarize the most important updates."
        )
        return

    if token == "settings":
        _set_api_key(dialog)
        _open_cortana_skin_overlay(state)
        return


def display_cortana_quick_asks():
    """
    Lightweight mode for the 'Cortana Quick Asks' menu entry:
    - No greeting
    - Just a list of QUICK_ASK_ITEMS
    - Sends, shows popup, and lets the user pick again or Back
    """
    dialog = xbmcgui.Dialog()

    while True:
        q_choice = dialog.select("Cortana Quick Asks", QUICK_ASK_ITEMS)
        if q_choice == -1:
            break

        text = QUICK_ASK_ITEMS[q_choice]
        reply = call_cortana(text)

        # Reuse the same wrapped popup logic
        _show_popup(dialog, "Cortana Chat", reply)


def display_cortana_news():
    """
    One-shot OG Xbox news:
    - No greeting
    - Sends a fixed prompt that tells Tater to use the web_search tool
    - Shows a single popup with the reply, then exits
    """
    dialog = xbmcgui.Dialog()
    news_prompt = (
        "What's the latest OG Xbox news? "
        "Use the web_search tool to look it up first, then summarize the most important updates."
    )

    reply = call_cortana(news_prompt)

    # Reuse the same wrapped popup logic
    _show_popup(dialog, "OG Xbox News", reply)


if __name__ == "__main__":
    try:
        # Called from the skin like:
        #   <onclick>RunScript(Q:\skin\skin.cortana.ai\scripts\cortana\default.py,QuickAsks)</onclick>
        #   <onclick>RunScript(Q:\skin\skin.cortana.ai\scripts\cortana\default.py,News)</onclick>
        if len(sys.argv) > 1:
            arg = str(sys.argv[1]).lower()
            if arg == "quickasks":
                display_cortana_quick_asks()
            elif arg == "news":
                display_cortana_news()
            elif arg in ("quick1", "quick2", "quick3", "ask", "presets", "settings", "overlaynews"):
                handle_overlay_action(arg)
            else:
                display_cortana_chat()
        else:
            display_cortana_chat()
    except Exception as e:
        try:
            xbmcgui.Dialog().ok("Cortana Chat", "Fatal error", str(e))
        except Exception:
            pass
