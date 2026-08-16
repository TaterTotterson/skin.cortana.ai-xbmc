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
import random
import re
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
    import sqlite3
except Exception:
    sqlite3 = None

try:
    import xml.etree.ElementTree as ElementTree
except Exception:
    ElementTree = None

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
GAMES_FILE = xbmc.translatePath('special://home/games.txt')
PROGRAM_DATABASE_PATHS = (
    xbmc.translatePath('special://profile/Database/MyPrograms6.db'),
    xbmc.translatePath('special://home/UserData/Database/MyPrograms6.db'),
    "Q:\\UserData\\Database\\MyPrograms6.db",
    "E:\\Dashboard\\UserData\\Database\\MyPrograms6.db",
)
PROGRAM_SOURCES_PATHS = (
    xbmc.translatePath('special://profile/sources.xml'),
    xbmc.translatePath('special://home/UserData/sources.xml'),
    "Q:\\UserData\\sources.xml",
    "E:\\Dashboard\\UserData\\sources.xml",
)
GAME_CONTEXT_MAX_ITEMS = 1200
QUICK_ASK_COUNT = 4
LAUNCH_QUICK_ASK_COUNT = 2
WATCH_TATER_TUBE_LABEL = "Watch Tater Tube"
QUICK_ASKS_LABEL = "Quick Asks"
RECENT_GAME_RECOMMENDATION_LIMIT = 12
TATER_TUBE_SCRIPT_PATH = "Q:\\skin\\skin.cortana.ai\\scripts\\tatertube\\default.py"
SAFE_SINGLE_WORD_GAME_ALIASES = {"halo"}
INSTALLED_GAMES_CACHE_SECONDS = 300
INSTALLED_GAMES_CACHE = {"loaded_at": 0, "games": None}
TTS_ENABLED = True
TTS_MAX_CHARS = 900
TTS_HTTP_TIMEOUT_SECONDS = 60
TTS_RETRY_COUNT = 2
TTS_RETRY_DELAY_SECONDS = 1.0
TTS_CACHE_DIR = os.path.join(xbmc.translatePath('special://profile'), 'cortana_tts')
TTS_PAUSE_BGVIDEO_ENABLED = True
BG_VIDEO_PATH = "E:\\BGVideo\\BGVideo.avi"
BG_VIDEO_VOLUME = 60
TTS_VOLUME = 100
TTS_PLAY_EXTRA_SECONDS = 0.35
TTS_LOCK = threading.Lock()

# Shared quick-ask prompts (used by both full chat and QuickAsks-only mode)
QUICK_ASK_ITEMS = [
    "Recommend three installed original Xbox games to play now. Do not ask my genre first.",
    "What's a hidden gem on the original Xbox?",
    "Give me a fun fact about the original Xbox",
    "Tell me about yourself Cortana?",
    "Recommend three installed multiplayer original Xbox games for tonight. Do not ask my genre first.",
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


def _time_of_day(hour):
    try:
        value = int(hour)
    except Exception:
        value = 12

    if 5 <= value < 12:
        return "morning"
    if 12 <= value < 17:
        return "afternoon"
    if 17 <= value < 22:
        return "evening"
    return "late night"


def _local_time_context():
    try:
        hour = int(time.strftime("%H"))
        local_time = time.strftime("%I:%M %p").lstrip("0")
        return {
            "local_time": local_time,
            "weekday": time.strftime("%A"),
            "time_of_day": _time_of_day(hour),
            "hour": hour,
        }
    except Exception:
        return {}


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
        return [
            "Set the lights to blue",
            "Turn the lights off",
            "Dim the lights",
            "Set game room mode",
            "What else can you control?",
        ]
    if "game" in combined or "xbox" in combined:
        return [
            "Recommend three installed games",
            "Find a multiplayer game",
            "Pick a hidden gem",
            "Tell me more about that game",
            "Surprise me",
        ]
    if "news" in combined:
        return [
            "Tell me the top story",
            "Any Insignia updates?",
            "Find more Xbox news",
            "What's new for homebrew?",
            "Summarize it shorter",
        ]
    return [
        "Tell me more",
        "What can you do next?",
        "Give me a quick suggestion",
        "Make it shorter",
        "Surprise me",
    ]


def _extract_quick_asks(obj, user_text="", reply_text=""):
    asks = []
    if isinstance(obj, dict):
        values = obj.get("quick_asks") or obj.get("suggestions") or obj.get("replies") or []
        if isinstance(values, list):
            for item in values:
                ask = _clean_quick_ask(item)
                if ask and ask not in asks:
                    asks.append(ask)
                if len(asks) >= QUICK_ASK_COUNT:
                    break

    if len(asks) < QUICK_ASK_COUNT:
        for fallback in _fallback_quick_asks(user_text, reply_text):
            ask = _clean_quick_ask(fallback)
            if ask and ask not in asks:
                asks.append(ask)
            if len(asks) >= QUICK_ASK_COUNT:
                break

    return asks[:QUICK_ASK_COUNT]


def _parse_game_line(line):
    text = str(line or "").strip()
    if not text or not text.startswith('"') or not text.endswith('"'):
        return None

    try:
        name, path = text[1:-1].split('", "', 1)
    except Exception:
        return None

    name = str(name or "").strip()
    path = str(path or "").strip()
    if not name or not path:
        return None

    return {"name": name, "path": path}


def _path_exists(path):
    try:
        return path and os.path.exists(path)
    except Exception:
        return False


def _unique_existing_paths(paths):
    values = []
    seen = {}
    for path in paths:
        value = str(path or "").strip()
        key = _normalize_path(value)
        if not value or key in seen:
            continue
        seen[key] = True
        if _path_exists(value):
            values.append(value)
    return values


def _normalize_source_prefix(path):
    value = _normalize_path(path).strip()
    if value and not value.endswith("\\"):
        value += "\\"
    return value


def _path_under_source(path, source_prefixes):
    normalized = _normalize_path(path)
    if not normalized:
        return False
    for prefix in source_prefixes:
        if prefix and normalized.startswith(prefix):
            return True
    return False


def _load_program_sources():
    sources = []

    if ElementTree is None:
        return sources

    for sources_path in _unique_existing_paths(PROGRAM_SOURCES_PATHS):
        try:
            tree = ElementTree.parse(sources_path)
            root = tree.getroot()
            programs = root.find("programs")
            if programs is None:
                continue

            for source in programs.findall("source"):
                name_node = source.find("name")
                source_name = name_node.text if name_node is not None else ""
                for path_node in source.findall("path"):
                    source_path = str(path_node.text or "").strip()
                    if source_path:
                        sources.append({"name": str(source_name or "").strip(), "path": source_path})

            if sources:
                return sources
        except Exception as e:
            _log("Program sources load failed from %s: %s" % (sources_path, e))

    return sources


def _game_source_prefixes():
    sources = _load_program_sources()
    selected = []

    for source in sources:
        source_name = str(source.get("name") or "").lower()
        source_path = str(source.get("path") or "")
        normalized_path = _normalize_source_prefix(source_path)
        if not normalized_path:
            continue
        if "game" in source_name or "\\games\\" in normalized_path or normalized_path.endswith("\\games\\"):
            selected.append(normalized_path)

    if not selected:
        for source in sources:
            normalized_path = _normalize_source_prefix(source.get("path"))
            if normalized_path:
                selected.append(normalized_path)

    unique = []
    seen = {}
    for prefix in selected:
        if prefix not in seen:
            seen[prefix] = True
            unique.append(prefix)

    return unique


def _parent_folder_from_xbe_path(path):
    value = str(path or "").replace("/", "\\").rstrip("\\")
    if not value:
        return ""

    parts = value.split("\\")
    if len(parts) >= 2 and parts[-1].lower() == "default.xbe":
        return parts[-2]
    if parts:
        return parts[-1]
    return ""


def _clean_installed_game_name(name):
    original = str(name or "").replace("_", " ").strip()
    text = original
    if not text:
        return ""

    region_suffix = re.compile(
        r"\s*[\(\[]\s*"
        r"(usa[ ._-]*pal|pal[ ._-]*usa|usa|u|europe|eur|e|japan|jpn|j|"
        r"world|glo|global|region free|aus|australia|"
        r"de|ger|germany|fr|fra|french|es|spa|spanish|it|ita|italian|"
        r"kor|korea|asia|cn|china|"
        r"ntsc[^)\]]*|pal[^)\]]*|rev[ ._-]*[a-z0-9]+|"
        r"en[,\- a-z]*|multi[0-9]*|beta|prototype|proto|demo|sample|"
        r"disc[ ._-]*[0-9]+|dvd[ ._-]*[0-9]+)"
        r"\s*[\)\]]\s*$",
        re.I,
    )

    changed = True
    while changed:
        cleaned = region_suffix.sub("", text).strip()
        changed = cleaned != text
        text = cleaned

    text = " ".join(text.split()).strip(" -")
    return text or original


def _db_game_from_row(row):
    filename = str(row[0] or "").strip() if len(row) > 0 else ""
    xbe_description = str(row[1] or "").strip() if len(row) > 1 else ""
    plays = row[2] if len(row) > 2 else 0
    last_accessed = row[3] if len(row) > 3 else 0
    folder_name = _parent_folder_from_xbe_path(filename)
    name = _clean_installed_game_name(folder_name or xbe_description)

    if not name or not filename:
        return None

    game = {"name": name, "path": filename, "source": "xbmc_programs"}
    if xbe_description:
        game["xbe_description"] = xbe_description
    if plays:
        game["plays"] = plays
    if last_accessed:
        game["last_accessed"] = last_accessed
    return game


def _load_xbmc_program_games(limit=0):
    games = []
    seen = {}

    if sqlite3 is None:
        _log("XBMC Programs DB unavailable: sqlite3 is not installed")
        return games

    source_prefixes = _game_source_prefixes()
    query = (
        "select strFilename, xbedescription, iTimesPlayed, lastAccessed "
        "from files "
        "where strFilename is not null and lower(strFilename) like '%default.xbe' "
        "order by iTimesPlayed desc, lastAccessed desc, xbedescription collate nocase, strFilename collate nocase"
    )

    for db_path in _unique_existing_paths(PROGRAM_DATABASE_PATHS):
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(query)

            for row in cursor:
                game = _db_game_from_row(row)
                if not game:
                    continue

                path = game.get("path")
                if source_prefixes and not _path_under_source(path, source_prefixes):
                    continue

                key = _normalize_path(path)
                if key in seen:
                    continue
                seen[key] = True
                games.append(game)

                if limit and len(games) >= limit:
                    break

            try:
                cursor.close()
            except Exception:
                pass

            if games:
                _log("Loaded %d installed games from XBMC Programs DB" % len(games))
                return games
        except Exception as e:
            _log("XBMC Programs DB load failed from %s: %s" % (db_path, e))
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    return games


def _load_games_file(limit=0):
    games = []
    seen = {}

    try:
        if not os.path.exists(GAMES_FILE):
            return games

        f = open(GAMES_FILE, "r")
        try:
            for line in f:
                game = _parse_game_line(line)
                if not game:
                    continue

                key = _normalize_path(game.get("path"))
                if key in seen:
                    continue
                seen[key] = True
                games.append(game)

                if limit and len(games) >= limit:
                    break
        finally:
            f.close()
    except Exception as e:
        _log("Installed games file load failed: %s" % e)

    return games


def _load_installed_games(limit=0):
    now = time.time()
    cached_games = INSTALLED_GAMES_CACHE.get("games")
    cached_at = INSTALLED_GAMES_CACHE.get("loaded_at") or 0

    if cached_games is not None and (now - cached_at) < INSTALLED_GAMES_CACHE_SECONDS:
        games = list(cached_games)
        return games[:limit] if limit else games

    games = _load_xbmc_program_games()
    if not games:
        games = _load_games_file()

    INSTALLED_GAMES_CACHE["loaded_at"] = now
    INSTALLED_GAMES_CACHE["games"] = list(games)

    return games[:limit] if limit else games


def _should_include_game_context(message):
    text = str(message or "").lower()
    terms = (
        "game", "games", "play", "launch", "start", "load", "boot", "recommend",
        "suggest", "find", "pick", "hidden", "gem", "surprise", "library",
        "installed", "racing", "sports", "shooter", "multiplayer", "co-op",
        "coop", "xbox"
    )
    for term in terms:
        if term in text:
            return True
    return False


def _installed_games_payload(message):
    if not _should_include_game_context(message):
        return []

    payload = []
    for game in _load_installed_games(GAME_CONTEXT_MAX_ITEMS):
        name = str(game.get("name") or "").strip()
        if name:
            payload.append({"name": name})
    return payload


def _normalize_game_text(text):
    raw = str(text or "").lower()
    chars = []
    last_space = False
    for ch in raw:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            chars.append(ch)
            last_space = False
        else:
            if not last_space:
                chars.append(" ")
                last_space = True
    return " ".join("".join(chars).split())


def _game_match_aliases(name):
    raw = str(name or "").strip()
    aliases = []
    candidates = [raw]

    for separator in (" - ", ": "):
        if separator in raw:
            candidates.append(raw.split(separator, 1)[0])

    for candidate in candidates:
        normalized = _normalize_game_text(candidate)
        if len(normalized) < 4:
            continue
        if " " not in normalized and normalized not in SAFE_SINGLE_WORD_GAME_ALIASES:
            continue
        if normalized not in aliases:
            aliases.append(normalized)

    return aliases


def _game_state_list(value):
    games = []
    seen = {}

    if isinstance(value, dict):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []

    for game in values:
        if not isinstance(game, dict):
            continue
        path_key = _normalize_path(game.get("path"))
        name_key = _normalize_game_text(game.get("name"))
        key = path_key or name_key
        if not key or key in seen:
            continue
        seen[key] = True
        games.append(game)

    return games


def _find_installed_games_in_text(text, max_items=0):
    normalized_text = " %s " % _normalize_game_text(text)
    games = _load_installed_games()
    games.sort(key=lambda item: len(str(item.get("name") or "")), reverse=True)
    matches = []
    seen = {}
    exact_aliases = {}

    for game in games:
        name = str(game.get("name") or "").strip()
        normalized_name = _normalize_game_text(name)
        if not normalized_name:
            continue

        pos = normalized_text.find(" %s " % normalized_name)
        if pos < 0:
            continue

        key = _normalize_path(game.get("path")) or normalized_name
        if key in seen:
            continue
        seen[key] = True
        exact_aliases[normalized_name] = True
        matches.append((pos, -len(normalized_name), game))

    for game in games:
        name = str(game.get("name") or "").strip()
        key = _normalize_path(game.get("path")) or _normalize_game_text(name)
        if key in seen:
            continue

        aliases = _game_match_aliases(name)
        for alias in aliases[1:]:
            if alias in exact_aliases:
                continue

            pos = normalized_text.find(" %s " % alias)
            if pos < 0:
                continue

            seen[key] = True
            matches.append((pos, -len(alias), game))
            break

    matches.sort(key=lambda item: (item[0], item[1]))
    result = [item[2] for item in matches]
    return result[:max_items] if max_items else result


def _find_installed_game_in_text(text):
    games = _find_installed_games_in_text(text, 1)
    return games[0] if games else None


def _strip_launch_text(text):
    value = _clean_quick_ask(text).lower()
    prefixes = (
        "please launch ", "please play ", "please start ", "please load ",
        "can you launch ", "can you play ", "can you start ", "can you load ",
        "could you launch ", "could you play ", "could you start ", "could you load ",
        "i want to launch ", "i want to play ", "i want to start ", "i want to load ",
        "launch ", "play ", "start ", "load ", "boot ", "run ",
    )
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def _game_launch_aliases(name):
    raw = str(name or "").strip()
    aliases = []
    candidates = [raw]

    for separator in (" - ", ": "):
        if separator in raw:
            parts = raw.split(separator)
            for part in parts:
                part = part.strip()
                if part:
                    candidates.append(part)

    for candidate in candidates:
        normalized = _normalize_game_text(candidate)
        if len(normalized) >= 4 and normalized not in aliases:
            aliases.append(normalized)

    return aliases


def _find_installed_game_for_launch(text):
    requested = _normalize_game_text(_strip_launch_text(text))
    if not requested:
        return None

    games = _load_installed_games()
    games.sort(key=lambda item: len(str(item.get("name") or "")), reverse=True)

    for game in games:
        normalized_name = _normalize_game_text(game.get("name"))
        if normalized_name and normalized_name == requested:
            return game

    for game in games:
        for alias in _game_launch_aliases(game.get("name")):
            if alias == requested:
                return game

    padded_requested = " %s " % requested
    for game in games:
        normalized_name = _normalize_game_text(game.get("name"))
        if len(requested) >= 6 and normalized_name and padded_requested in (" %s " % normalized_name):
            return game

    return None


def _launch_quick_label(game):
    name = str((game or {}).get("name") or "").strip()
    return "Launch %s" % _button_label(name, limit=31)


def _is_launch_intent(text):
    token = str(text or "").lower().strip()
    compact = " ".join(token.split())
    launch_terms = ("launch", "play", "start", "load", "boot", "run")
    for term in launch_terms:
        if (compact == term or compact.startswith(term + " ") or
                compact.startswith("please " + term + " ") or
                compact.startswith("can you " + term + " ") or
                compact.startswith("could you " + term + " ") or
                compact.startswith("i want to " + term + " ")):
            return True

    return compact in ("yes", "yes please", "do it", "go ahead", "sure", "launch it", "play it")


def _launch_game_from_state(state, text=""):
    launch_games = []
    if isinstance(state, dict):
        launch_games = _game_state_list(state.get("launch_games"))
        seen = {}
        for game in launch_games:
            seen[_normalize_path(game.get("path")) or _normalize_game_text(game.get("name"))] = True
        for game in _game_state_list(state.get("launch_game")):
            key = _normalize_path(game.get("path")) or _normalize_game_text(game.get("name"))
            if key and key not in seen:
                seen[key] = True
                launch_games.append(game)

    if launch_games:
        clean_text = _clean_quick_ask(text).lower()
        normalized_text = " %s " % _normalize_game_text(text)

        for game in launch_games:
            if clean_text == _launch_quick_label(game).lower():
                return game

        for game in launch_games:
            normalized_name = _normalize_game_text(game.get("name"))
            if normalized_name and (
                (" %s " % normalized_name) in normalized_text or
                normalized_text.strip() == normalized_name
            ):
                return game

        if len(launch_games) == 1 and _is_launch_intent(text):
            return launch_games[0]

    if _is_launch_intent(text):
        requested = _find_installed_game_for_launch(text) or _find_installed_game_in_text(text)
        if requested:
            return requested

    return None


def _launch_installed_game(game):
    name = str((game or {}).get("name") or "").strip()
    path = str((game or {}).get("path") or "").strip()
    if not name or not path:
        xbmcgui.Dialog().ok("Cortana Chat", "I could not find that game path.")
        return False

    try:
        xbmc.executebuiltin("SetVolume(100,false)")
    except Exception:
        pass

    _log("Launching installed game: %s -> %s" % (name, path))
    xbmc.executebuiltin("Notification(Cortana Chat, Launching %s, 2000)" % _button_label(name, limit=32))
    xbmc.executebuiltin("XBMC.RunXBE(%s)" % path)
    return True


def _game_recommendation_key(game):
    return _normalize_path((game or {}).get("path")) or _normalize_game_text((game or {}).get("name"))


def _dedupe_recommendation_games(games):
    values = []
    seen = {}
    for game in list(games or []):
        if not isinstance(game, dict):
            continue
        key = _game_recommendation_key(game)
        name = str(game.get("name") or "").strip()
        path = str(game.get("path") or "").strip()
        if not key or not name or not path or key in seen:
            continue
        seen[key] = True
        values.append(game)
    return values


def _recent_game_recommendations(state):
    values = []
    raw_values = []
    if isinstance(state, dict) and isinstance(state.get("recent_game_recommendations"), list):
        raw_values = state.get("recent_game_recommendations") or []
    seen = {}
    for value in raw_values:
        key = str(value or "").strip().lower()
        if key and key not in seen:
            seen[key] = True
            values.append(key)
    return values


def _pick_game_recommendations(state, count=2):
    games = _dedupe_recommendation_games(_load_installed_games())
    if not games:
        return [], _recent_game_recommendations(state)

    recent = _recent_game_recommendations(state)
    blocked = {}
    for key in recent:
        blocked[key] = True

    available = []
    for game in games:
        key = _game_recommendation_key(game)
        if key and key not in blocked:
            available.append(game)

    if len(available) < count:
        available = list(games)
        recent = []

    random.shuffle(available)
    selected = available[:count]
    selected_keys = []
    for game in selected:
        key = _game_recommendation_key(game)
        if key and key not in selected_keys:
            selected_keys.append(key)

    recent = selected_keys + [key for key in recent if key not in selected_keys]
    try:
        max_recent = min(max(RECENT_GAME_RECOMMENDATION_LIMIT, count), max(len(games), count))
    except Exception:
        max_recent = RECENT_GAME_RECOMMENDATION_LIMIT

    return selected, recent[:max_recent]


def _is_tater_tube_action(text):
    token = _normalize_game_text(text)
    return token in ("watch tater tube", "open tater tube", "tater tube")


def _is_quick_asks_action(text):
    token = _normalize_game_text(text)
    return token in ("quick asks", "cortana quick asks", "preset quick asks")


def _open_tater_tube_menu():
    _log("Opening Tater Tube menu from Cortana")
    xbmc.executebuiltin("RunScript(%s,Open)" % TATER_TUBE_SCRIPT_PATH)
    return True


def _cortana_home_greeting():
    context = _local_time_context()
    time_of_day = str(context.get("time_of_day") or "").strip()
    if time_of_day in ("morning", "afternoon", "evening"):
        return "Good %s, welcome back" % time_of_day
    return "Welcome back"


def _game_reply_name(game):
    return _button_label(str((game or {}).get("name") or "").strip(), limit=26)


def _build_cortana_home_menu_state():
    previous_state = _load_overlay_state()
    games, recent = _pick_game_recommendations(previous_state, LAUNCH_QUICK_ASK_COUNT)
    game_names = [_game_reply_name(game) for game in games if _game_reply_name(game)]

    if len(game_names) >= 2:
        reply = (
            "%s. You should try %s or %s. "
            "For a show, open Tater Tube."
        ) % (_cortana_home_greeting(), game_names[0], game_names[1])
    elif len(game_names) == 1:
        reply = (
            "%s. You should try %s. "
            "For a show, open Tater Tube."
        ) % (_cortana_home_greeting(), game_names[0])
    else:
        reply = "%s. For a show, open Tater Tube, or choose Quick Asks." % _cortana_home_greeting()

    quick_asks = []
    for game in games[:LAUNCH_QUICK_ASK_COUNT]:
        quick_asks.append(_launch_quick_label(game))
    quick_asks.append(WATCH_TATER_TUBE_LABEL)
    quick_asks.append(QUICK_ASKS_LABEL)

    return {
        "reply": reply,
        "quick_asks": quick_asks[:QUICK_ASK_COUNT],
        "history": [],
        "launch_game": games[0] if games else None,
        "launch_games": games[:LAUNCH_QUICK_ASK_COUNT],
        "recent_game_recommendations": recent,
        "menu_mode": "home",
    }


def _with_launch_quick_ask(quick_asks, games):
    launch_games = _game_state_list(games)
    if not launch_games:
        return list(quick_asks or [])[:QUICK_ASK_COUNT]

    values = []
    for game in launch_games[:LAUNCH_QUICK_ASK_COUNT]:
        launch_label = _launch_quick_label(game)
        if launch_label and launch_label not in values:
            values.append(launch_label)

    for item in list(quick_asks or []):
        label = _clean_quick_ask(item)
        if not label:
            continue
        if label.lower().startswith("launch"):
            continue
        if label not in values:
            values.append(label)
        if len(values) >= QUICK_ASK_COUNT:
            break

    for fallback in _fallback_quick_asks("", ""):
        if len(values) >= QUICK_ASK_COUNT:
            break
        if fallback not in values:
            values.append(fallback)

    return values[:QUICK_ASK_COUNT]


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


def _set_xbmc_volume(percent, reason):
    try:
        value = int(percent)
    except Exception:
        return False

    if value < 0:
        value = 0
    if value > 100:
        value = 100

    try:
        xbmc.executebuiltin("SetVolume(%d,false)" % value)
        _log("Set volume to %d%% for %s" % (value, reason))
        return True
    except Exception as e:
        _log("Unable to set volume for %s: %s" % (reason, e))
        return False


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

        _set_xbmc_volume(BG_VIDEO_VOLUME, "background video")

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

    for attempt in range(1, TTS_RETRY_COUNT + 1):
        resp = None
        try:
            req = urllib2.Request(audio_url, None, _request_headers())
            socket.setdefaulttimeout(TTS_HTTP_TIMEOUT_SECONDS)
            resp = urllib2.urlopen(req, timeout=TTS_HTTP_TIMEOUT_SECONDS)
            return _save_tts_wav(resp.read())
        except Exception as e:
            _log("TTS URL fetch failed (attempt %d/%d): %s" % (attempt, TTS_RETRY_COUNT, e))
            if attempt < TTS_RETRY_COUNT:
                time.sleep(TTS_RETRY_DELAY_SECONDS)
        finally:
            try:
                if resp:
                    resp.close()
            except Exception:
                pass

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

    for attempt in range(1, TTS_RETRY_COUNT + 1):
        resp = None
        try:
            req = urllib2.Request(_tts_preview_url(), data, _request_headers("application/json"))
            socket.setdefaulttimeout(TTS_HTTP_TIMEOUT_SECONDS)
            resp = urllib2.urlopen(req, timeout=TTS_HTTP_TIMEOUT_SECONDS)
            return _save_tts_wav(resp.read())
        except Exception as e:
            _log("TTS synthesis failed (attempt %d/%d): %s" % (attempt, TTS_RETRY_COUNT, e))
            if attempt < TTS_RETRY_COUNT:
                time.sleep(TTS_RETRY_DELAY_SECONDS)
        finally:
            try:
                if resp:
                    resp.close()
            except Exception:
                pass

    return ""


def _play_tts_file(path):
    if not path or not os.path.exists(path):
        return

    TTS_LOCK.acquire()
    paused_bgvideo = False
    try:
        duration = _wav_duration_seconds(path)
        paused_bgvideo = _pause_background_video_for_tts()
        if paused_bgvideo:
            _set_xbmc_volume(TTS_VOLUME, "Cortana TTS")

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
            return {
                "reply": "",
                "quick_asks": _fallback_quick_asks(),
                "history": [],
                "launch_game": None,
                "launch_games": [],
                "recent_game_recommendations": [],
            }
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
            "launch_game": parsed.get("launch_game") if isinstance(parsed.get("launch_game"), dict) else None,
            "launch_games": _game_state_list(parsed.get("launch_games")),
            "recent_game_recommendations": (
                parsed.get("recent_game_recommendations")
                if isinstance(parsed.get("recent_game_recommendations"), list)
                else []
            ),
            "menu_mode": str(parsed.get("menu_mode") or ""),
        }
    except Exception as e:
        _log("Overlay state load failed: %s" % e)
        return {
            "reply": "",
            "quick_asks": _fallback_quick_asks(),
            "history": [],
            "launch_game": None,
            "launch_games": [],
            "recent_game_recommendations": [],
        }


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
    quick_asks = list(state.get("quick_asks") or [])[:QUICK_ASK_COUNT]
    history = list(state.get("history") or [])[:4]
    fallback_asks = _fallback_quick_asks()

    for idx, line in enumerate(_overlay_lines(reply), 1):
        _set_overlay_property("Cortana.Reply%s" % idx, line)

    for idx in range(QUICK_ASK_COUNT):
        text = quick_asks[idx] if idx < len(quick_asks) else fallback_asks[idx]
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
    launch_games = _find_installed_games_in_text(reply, LAUNCH_QUICK_ASK_COUNT)
    seen_launch_games = {}
    for game in launch_games:
        seen_launch_games[_normalize_path(game.get("path")) or _normalize_game_text(game.get("name"))] = True

    quick_asks = list(result.get("quick_asks") or _fallback_quick_asks(user_text, reply))[:QUICK_ASK_COUNT]
    for ask in quick_asks:
        game = _find_installed_game_for_launch(ask)
        if not game:
            continue
        key = _normalize_path(game.get("path")) or _normalize_game_text(game.get("name"))
        if key and key not in seen_launch_games:
            seen_launch_games[key] = True
            launch_games.append(game)
        if len(launch_games) >= LAUNCH_QUICK_ASK_COUNT:
            break

    launch_games = launch_games[:LAUNCH_QUICK_ASK_COUNT]
    launch_game = launch_games[0] if launch_games else None
    quick_asks = _with_launch_quick_ask(quick_asks, launch_games)
    state = {
        "reply": reply,
        "quick_asks": quick_asks,
        "history": list(history or [])[:60],
        "launch_game": launch_game,
        "launch_games": launch_games,
    }
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


def call_cortana_result(message, auto_tts=True, include_game_context=True):
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
        "platform_context": "original Xbox running XBMC4Xbox",
        "include_tts": True,
        "tts_format": "wav",
        "include_quick_asks": True,
    }
    payload.update(_local_time_context())

    installed_games = _installed_games_payload(message) if include_game_context else []
    if installed_games:
        payload["installed_games"] = installed_games

    try:
        data = json.dumps(payload)
    except Exception as e:
        return _cortana_result("JSON error: %s" % e)

    _log("Sending to Cortana URL: %s" % CORTANA_API_URL)
    try:
        log_payload = dict(payload)
        if installed_games:
            log_payload["installed_games"] = "%d installed games" % len(installed_games)
        _log("Payload: %s" % json.dumps(log_payload))
    except Exception:
        _log("Payload prepared")

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
    Lightweight Cortana home menu:
    - Two local installed-game recommendations that can launch immediately
    - One Tater Tube entry for movie and TV recommendations from local Tater
    """
    try:
        state = _build_cortana_home_menu_state()
    except Exception as e:
        _log("Startup menu failed: %s" % e)
        state = {
            "reply": "Cortana is online. Tater Tube has movie and TV picks.",
            "quick_asks": [WATCH_TATER_TUBE_LABEL, QUICK_ASKS_LABEL],
            "history": [],
            "launch_game": None,
            "launch_games": [],
            "recent_game_recommendations": [],
            "menu_mode": "home",
        }

    _open_cortana_skin_overlay(state)
    _play_reply_tts(state.get("reply"))


def _send_overlay_message(text):
    text = str(text or "").strip()
    if not text:
        return

    state = _load_overlay_state()
    launch_game = _launch_game_from_state(state, text)
    if launch_game:
        _launch_installed_game(launch_game)
        return

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

    if token.startswith("quick") and token[5:].isdigit():
        idx = int(token[5:]) - 1
        if 0 <= idx < QUICK_ASK_COUNT:
            quick_asks = list(state.get("quick_asks") or [])
            if idx < len(quick_asks):
                if _is_tater_tube_action(quick_asks[idx]):
                    _open_tater_tube_menu()
                    return
                if _is_quick_asks_action(quick_asks[idx]):
                    display_cortana_quick_asks()
                    return
                launch_game = _launch_game_from_state(state, quick_asks[idx])
                if launch_game:
                    _launch_installed_game(launch_game)
                    return
                _send_overlay_message(quick_asks[idx])
        return

    if token in ("tatertube", "tater_tube", "watchtube"):
        _open_tater_tube_menu()
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
            elif ((arg.startswith("quick") and arg[5:].isdigit()) or
                    arg in ("ask", "presets", "settings", "overlaynews", "tatertube", "tater_tube", "watchtube")):
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
