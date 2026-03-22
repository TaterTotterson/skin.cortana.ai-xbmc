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

# Now talks to the dedicated XBMC platform on port 8790.
CORTANA_API_URL = "http://10.4.20.173:8790/tater-xbmc/v1/message"
HTTP_TIMEOUT_SECONDS = 15
DEFAULT_API_KEY = ""
SETTINGS_FILE = os.path.join(xbmc.translatePath('special://profile'), 'cortana_chat_settings.json')

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


def call_cortana(message):
    """
    Send a message to the XBMC bridge endpoint and return the reply text.
    """

    profile_name = xbmc.getInfoLabel("System.ProfileName") or "XBMC4Xbox"

    payload = {
        "text": message,
        "user_id": profile_name,
        "session_id": "xbmc_%s" % profile_name,
        "device_id": "xbmc4xbox",
        "area_id": "xbmc",
    }

    try:
        data = json.dumps(payload)
    except Exception as e:
        return "JSON error: %s" % e

    _log("Sending to Cortana URL: %s" % CORTANA_API_URL)
    _log("Payload: %s" % data)

    headers = {"Content-Type": "application/json"}
    api_key = _get_api_key()
    if api_key:
        headers["X-Tater-Token"] = api_key

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
            return raw.strip()

        if "response" in obj and isinstance(obj["response"], basestring):
            return obj["response"].strip()

        for key in ("reply", "assistant", "text", "message"):
            if key in obj and isinstance(obj[key], basestring):
                return obj[key].strip()

        return json.dumps(obj)

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
                return "HTTP %s\n%s\n%s" % (e.code, body, hint)
            return "HTTP %s\n%s" % (e.code, hint)

        return "HTTP %s\n%s" % (e.code, body)

    except urllib2.URLError as e:
        return "URL error: %s" % getattr(e, "reason", e)

    except Exception as e:
        return "Error talking to Cortana: %s" % e


def display_cortana_chat():
    """
    Full Cortana chat experience:
    - Greeting
    - Ask Cortana
    - Inline Quick Ask menu
    - History view
    """
    dialog = xbmcgui.Dialog()
    history = []

    # --------------------------
    # Xbox OG Cortana-style greeting on launch
    # --------------------------
    try:
        greeting_prompt = (
            "Greet the user as Cortana from the original Xbox. "
            "Be warm, confident, and helpful. Keep it under 2 sentences."
        )
        greeting_reply = call_cortana(greeting_prompt)
        if greeting_reply:
            # Show greeting popup (wrapped, with up to 3 lines)
            _show_popup(dialog, "Cortana Chat", greeting_reply)
            # Add to history (Cortana only, no faux user line; single-line)
            history.insert(0, "Cortana: %s" % greeting_reply)
    except Exception as e:
        _log("Startup greeting failed: %s" % e)

    xbmc.executebuiltin("Notification(Cortana Chat, Press A to ask Cortana, 2500)")

    while True:
        items = [
            "Ask Cortana…",
            "Quick Ask →",
            "Set API Key…"
        ]

        if history:
            items.append("────────────")
            items.extend(history)

        choice = dialog.select("Cortana Chat", items)

        if choice == -1:
            break

        # ------------------------------
        # Ask Cortana (keyboard)
        # ------------------------------
        if choice == 0:
            kb = xbmc.Keyboard("", "Talk to Cortana", False)
            kb.doModal()

            if not kb.isConfirmed():
                continue

            text = kb.getText()
            if not text:
                continue

            reply = call_cortana(text)

            # Wrapped popup for long replies (up to 3 lines)
            _show_popup(dialog, "Cortana Chat", reply)

            # NEWEST AT TOP: keep entries single-line for the menu
            history.insert(0, "Cortana: %s" % reply)
            history.insert(0, "You:     %s" % text)

            if len(history) > 60:
                history = history[:60]

            continue

        # ------------------------------
        # Quick Ask (inline mode)
        # ------------------------------
        if choice == 1:
            q_choice = dialog.select("Quick Ask", QUICK_ASK_ITEMS)
            if q_choice == -1:
                continue

            text = QUICK_ASK_ITEMS[q_choice]
            reply = call_cortana(text)

            # Wrapped popup
            _show_popup(dialog, "Cortana Chat", reply)

            # NEWEST AT TOP (single-line)
            history.insert(0, "Cortana: %s" % reply)
            history.insert(0, "You:     %s" % text)

            if len(history) > 60:
                history = history[:60]

            continue

        # ------------------------------
        # API key setup
        # ------------------------------
        if choice == 2:
            _set_api_key(dialog)
            continue


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
        #   <onclick>RunScript(Q:\skin\Cortana\scripts\cortana\default.py,QuickAsks)</onclick>
        #   <onclick>RunScript(Q:\skin\Cortana\scripts\cortana\default.py,News)</onclick>
        if len(sys.argv) > 1:
            arg = str(sys.argv[1]).lower()
            if arg == "quickasks":
                display_cortana_quick_asks()
            elif arg == "news":
                display_cortana_news()
            else:
                display_cortana_chat()
        else:
            display_cortana_chat()
    except Exception as e:
        try:
            xbmcgui.Dialog().ok("Cortana Chat", "Fatal error", str(e))
        except Exception:
            pass