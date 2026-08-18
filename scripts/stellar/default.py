# -*- coding: utf-8 -*-

import os
import shutil
import sys
import urllib2

import xbmc
import xbmcgui

try:
    import json
except ImportError:
    import simplejson as json

try:
    import xml.etree.ElementTree as ElementTree
except ImportError:
    try:
        import elementtree.ElementTree as ElementTree
    except ImportError:
        ElementTree = None

try:
    unicode
except NameError:
    unicode = str

PY3 = sys.version_info[0] >= 3


WINDOW_ID = 1114
PROPERTY_WINDOW_ID = 10000
MAX_ROWS = 8
CLEAR_ROW_COUNT = 8
PAGE_ITEM_COUNT = 6
SCAN_MAX_DEPTH = 6
SCAN_MAX_ITEMS = 600

PROFILE_DIR = xbmc.translatePath("special://profile")
SETTINGS_FILE = os.path.join(PROFILE_DIR, "tater_stellar_settings.json")
STATE_FILE = os.path.join(PROFILE_DIR, "tater_stellar_state.json")
SOURCES_FILE = os.path.join(PROFILE_DIR, "sources.xml")
SOURCES_BACKUP_FILE = os.path.join(PROFILE_DIR, "sources.xml.tater_stellar.bak")
APP_TITLE = "Stellar Net ISO"
STELLAR_SOURCE_NAME = APP_TITLE + " SMB"
LEGACY_STELLAR_SOURCE_NAMES = ("Tater Stellar SMB",)
STELLAR_SOURCE_SECTIONS = ("programs", "files")
SMB_BROWSE_SHARES = "programs"

HELPER_NAME = "attach.xbe"
FALLBACK_STELLAR_DIR = "E:\\Tater\\Stellar"
FALLBACK_HELPER_PATH = FALLBACK_STELLAR_DIR + "\\" + HELPER_NAME
ATTACH_DOWNLOAD_URL = "https://github.com/MakeMHz/stellar-attach/releases/latest/download/attach.xbe"
DOWNLOAD_TIMEOUT_SECONDS = 90
DOWNLOAD_CHUNK_BYTES = 64 * 1024
MIN_ATTACH_XBE_BYTES = 64 * 1024

STELLAR_NET_DEVICE_ROOT = "\\Device\\Network\\net0"
DEFAULT_STELLAR_ROOT = STELLAR_NET_DEVICE_ROOT
SUPPORTED_EXTENSIONS = (".iso", ".cso")
SKIP_SCAN_EXTENSIONS = (
    ".xbe", ".nfo", ".txt", ".jpg", ".jpeg", ".png", ".gif", ".tbn",
    ".xml", ".db", ".ini", ".sfv", ".md5", ".zip", ".rar", ".7z",
)

SKIN_HELPER_PATHS = (
    "Q:\\skin\\skin.cortana.ai\\scripts\\stellar\\attach.xbe",
)


def _log(message):
    try:
        xbmc.log("TaterStellar: %s" % message, xbmc.LOGNOTICE)
    except Exception:
        try:
            print("TaterStellar: %s" % message)
        except Exception:
            pass


def _text(value):
    if value is None:
        return ""
    try:
        if PY3 and isinstance(value, bytes):
            return value.decode("utf-8", "ignore")
        if not PY3 and isinstance(value, unicode):
            return value.encode("utf-8")
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return ""


def _clean(value, limit=160):
    text = _text(value).replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].strip()
    return text


def _ensure_dir(path):
    if not path or os.path.isdir(path):
        return
    cleaned = path.rstrip("\\/")
    slash = max(cleaned.rfind("\\"), cleaned.rfind("/"))
    parent = cleaned[:slash] if slash > 0 else ""
    if parent and not os.path.isdir(parent):
        _ensure_dir(parent)
    try:
        os.mkdir(cleaned)
    except OSError:
        if not os.path.isdir(cleaned):
            raise


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
        if directory and not os.path.isdir(directory):
            _ensure_dir(directory)
        handle = open(path, "wb")
        try:
            handle.write(json.dumps(value, separators=(",", ":")))
        finally:
            handle.close()
    except Exception as exc:
        _log("Failed to write %s: %s" % (path, exc))


def _source_path():
    candidates = [
        SOURCES_FILE,
        xbmc.translatePath("special://home/UserData/sources.xml"),
        "Q:\\UserData\\sources.xml",
        "E:\\Dashboard\\UserData\\sources.xml",
    ]
    seen = {}
    for path in candidates:
        path = _text(path)
        if not path or seen.get(path):
            continue
        seen[path] = True
        if os.path.exists(path):
            return path
    return SOURCES_FILE


def _xml_child(parent, tag):
    for child in list(parent):
        if child.tag == tag:
            return child
    return None


def _xml_text(node):
    if node is None or node.text is None:
        return ""
    return _text(node.text).strip()


def _indent_xml(node, level=0):
    indent = "\n" + level * "    "
    child_indent = "\n" + (level + 1) * "    "
    children = list(node)
    if children:
        if not node.text or not node.text.strip():
            node.text = child_indent
        for child in children:
            _indent_xml(child, level + 1)
        if not children[-1].tail or not children[-1].tail.strip():
            children[-1].tail = indent
    if level and (not node.tail or not node.tail.strip()):
        node.tail = indent


def _escape_xml(value):
    text = _text(value)
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _ensure_stellar_source_raw(path, smb_url):
    raw = ""
    try:
        if os.path.exists(path):
            handle = open(path, "rb")
            try:
                raw = handle.read()
            finally:
                handle.close()
    except Exception:
        raw = ""
    raw = _text(raw)
    if not raw:
        raw = "<sources>\n</sources>\n"

    escaped_url = _escape_xml(smb_url)
    source_xml = "        <source>\n            <name>%s</name>\n            <path pathversion=\"1\">%s</path>\n        </source>\n" % (STELLAR_SOURCE_NAME, escaped_url)

    for section in STELLAR_SOURCE_SECTIONS:
        section_open = "<%s>" % section
        section_close = "</%s>" % section
        section_start = raw.find(section_open)
        if section_start < 0:
            if "</sources>" not in raw:
                return False
            section_xml = "    <%s>\n        <default pathversion=\"1\"></default>\n%s    </%s>\n" % (section, source_xml, section)
            raw = raw.replace("</sources>", section_xml + "</sources>", 1)
            continue

        section_end = raw.find(section_close, section_start)
        if section_end < 0:
            return False

        body_start = section_start + len(section_open)
        section_body = raw[body_start:section_end]
        marker_pos = -1
        for source_name in (STELLAR_SOURCE_NAME,) + LEGACY_STELLAR_SOURCE_NAMES:
            marker = "<name>%s</name>" % source_name
            marker_pos = section_body.find(marker)
            if marker_pos >= 0:
                break
        if marker_pos >= 0:
            source_start = section_body.rfind("<source>", 0, marker_pos)
            source_end = section_body.find("</source>", marker_pos)
            if source_start < 0 or source_end < 0:
                return False
            section_body = section_body[:source_start] + "\n" + source_xml.rstrip("\n") + section_body[source_end + len("</source>"):]
        else:
            section_body = section_body + source_xml

        raw = raw[:body_start] + section_body + raw[section_end:]

    data = raw
    try:
        if PY3 and isinstance(data, str):
            data = data.encode("utf-8")
    except Exception:
        pass

    handle = open(path, "wb")
    try:
        handle.write(data)
    finally:
        handle.close()
    return True


def _ensure_stellar_source_xml(path, smb_url):
    if ElementTree is None:
        return _ensure_stellar_source_raw(path, smb_url)

    try:
        if os.path.exists(path):
            tree = ElementTree.parse(path)
            root = tree.getroot()
        else:
            root = ElementTree.Element("sources")
            tree = ElementTree.ElementTree(root)

        for section in STELLAR_SOURCE_SECTIONS:
            section_node = _xml_child(root, section)
            if section_node is None:
                section_node = ElementTree.SubElement(root, section)
                default = ElementTree.SubElement(section_node, "default")
                default.set("pathversion", "1")
                default.text = ""

            target_source = None
            source_names = (STELLAR_SOURCE_NAME,) + LEGACY_STELLAR_SOURCE_NAMES
            for source in list(section_node):
                if source.tag == "source" and _xml_text(_xml_child(source, "name")) in source_names:
                    target_source = source
                    break

            if target_source is None:
                target_source = ElementTree.SubElement(section_node, "source")
                name = ElementTree.SubElement(target_source, "name")
            else:
                name = _xml_child(target_source, "name")
                if name is None:
                    name = ElementTree.SubElement(target_source, "name")
            name.text = STELLAR_SOURCE_NAME

            path_node = _xml_child(target_source, "path")
            if path_node is None:
                path_node = ElementTree.SubElement(target_source, "path")
            path_node.set("pathversion", "1")
            path_node.text = _text(smb_url)

        _indent_xml(root)
        tree.write(path, encoding="utf-8")
        return True
    except Exception as exc:
        _log("Failed to update sources.xml with SMB source: %s" % exc)
        try:
            return _ensure_stellar_source_raw(path, smb_url)
        except Exception as raw_exc:
            _log("Raw sources.xml update failed: %s" % raw_exc)
    return False


def _ensure_stellar_source(smb_url):
    path = _source_path()
    try:
        if os.path.exists(path) and not os.path.exists(SOURCES_BACKUP_FILE):
            shutil.copyfile(path, SOURCES_BACKUP_FILE)
    except Exception as exc:
        _log("Failed to back up sources.xml: %s" % exc)

    try:
        return _ensure_stellar_source_raw(path, smb_url)
    except Exception as exc:
        _log("Raw sources.xml update failed: %s" % exc)
        return _ensure_stellar_source_xml(path, smb_url)


def _load_settings():
    settings = _read_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        settings = {}
    if "smb_root" not in settings:
        settings["smb_root"] = ""
    if "smb_url" not in settings:
        settings["smb_url"] = settings.get("smb_root") or ""
    if "smb_user" not in settings:
        settings["smb_user"] = ""
    if not settings.get("smb_share_path"):
        settings["smb_share_path"] = _smb_path_part(settings.get("smb_url") or settings.get("smb_root") or "")
    if not settings.get("browse_start"):
        settings["browse_start"] = settings.get("smb_root") or ""
    return settings


def _save_settings(settings):
    _write_json(SETTINGS_FILE, settings)


def _load_state():
    state = _read_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    if not isinstance(state.get("rows"), list):
        state["rows"] = []
    if not isinstance(state.get("visible_rows"), list):
        state["visible_rows"] = []
    return state


def _save_state(state):
    _write_json(STATE_FILE, state)


def _clear_cache():
    return


def _keyboard(default_value, heading, hidden=False):
    keyboard = xbmc.Keyboard(_text(default_value), heading, hidden)
    keyboard.doModal()
    if not keyboard.isConfirmed():
        return None
    return _text(keyboard.getText()).strip()


def _window():
    return xbmcgui.Window(PROPERTY_WINDOW_ID)


def _set_property(name, value):
    try:
        _window().setProperty("TaterStellar." + name, _text(value))
    except Exception as exc:
        _log("Failed to set property %s: %s" % (name, exc))


def _clear_property(name):
    try:
        _window().clearProperty("TaterStellar." + name)
    except Exception:
        try:
            _window().setProperty("TaterStellar." + name, "")
        except Exception as exc:
            _log("Failed to clear property %s: %s" % (name, exc))


def _set_file_browser_mode(enabled):
    if enabled:
        _set_property("FileBrowserMode", "1")
    else:
        _clear_property("FileBrowserMode")


def _clear_row_properties():
    for index in range(1, CLEAR_ROW_COUNT + 1):
        _set_property("Row%d.Title" % index, "")
        _set_property("Row%d.Detail" % index, "")


def _row(title, detail, action, data=None):
    if data is None:
        data = {}
    return {
        "title": _text(title),
        "detail": _text(detail),
        "action": _text(action),
        "data": data,
    }


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


def _populate_row_properties(rows):
    _clear_row_properties()
    for index, row in enumerate(rows[:MAX_ROWS]):
        number = index + 1
        _set_property("Row%d.Title" % number, _clean(row.get("title"), 56))
        _set_property("Row%d.Detail" % number, _clean(row.get("detail"), 72))


def _render(title, subtitle, rows, status="", page=0):
    if not rows:
        rows = [_row("Settings", "Configure Stellar NetISO", "settings", {})]
    page = _normalize_page(rows, page)
    visible = _page_rows(rows, page)
    display_status = status
    max_page = _max_page(rows)
    if max_page > 0:
        page_text = "Page %d/%d" % (page + 1, max_page + 1)
        display_status = _clean("%s  %s" % (status, page_text), 115) if status else page_text

    _set_property("Title", _clean(title, 64))
    _set_property("Subtitle", _clean(subtitle, 115))
    _set_property("Status", _clean(display_status, 115))
    _populate_row_properties(visible)
    _save_state({
        "title": title,
        "subtitle": subtitle,
        "rows": rows,
        "visible_rows": visible,
        "page": page,
        "status": status,
    })


def _show_window():
    xbmc.executebuiltin("ActivateWindow(%d)" % WINDOW_ID)
    xbmc.sleep(200)


def _basename(path):
    normalized = _text(path).replace("\\", "/").rstrip("/")
    if "/" not in normalized:
        return normalized
    return normalized.rsplit("/", 1)[-1]


def _dirname(path):
    normalized = _text(path).replace("\\", "/").rstrip("/")
    if "/" not in normalized:
        return ""
    return normalized.rsplit("/", 1)[0] + "/"


def _path_dirname(path):
    cleaned = _text(path).rstrip("\\/")
    slash = max(cleaned.rfind("\\"), cleaned.rfind("/"))
    if slash <= 0:
        return ""
    return cleaned[:slash]


def _path_join(folder, filename):
    cleaned = _text(folder).rstrip("\\/")
    if not cleaned:
        return _text(filename)
    return cleaned + "\\" + _text(filename).lstrip("\\/")


def _browse_join(folder, filename):
    cleaned = _text(folder).rstrip("\\/")
    if not cleaned:
        return _text(filename)
    separator = "/" if cleaned.find("/") >= 0 or cleaned.find("://") >= 0 else "\\"
    return cleaned + separator + _text(filename).lstrip("\\/")


def _normalize_folder(path):
    normalized = _text(path).replace("\\", "/").strip()
    if normalized and not normalized.endswith("/"):
        normalized += "/"
    return normalized


def _normalize_smb_url(path):
    value = _text(path).replace("\\", "/").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = "smb:" + value
    elif value.find("://") < 0:
        value = "smb://" + value.lstrip("/")
    if value.startswith("smb://") and not value.endswith("/"):
        value += "/"
    return value


def _smb_authority(url):
    value = _text(url)
    if not value.lower().startswith("smb://"):
        return ""
    rest = value[6:]
    slash = rest.find("/")
    if slash >= 0:
        return rest[:slash]
    return rest


def _smb_path_part(url):
    value = _normalize_smb_url(url)
    authority = _smb_authority(value)
    if not authority:
        return ""
    return value[6 + len(authority):].replace("\\", "/").strip("/")


def _smb_url_with_path(url, share_path):
    value = _normalize_smb_url(url)
    authority = _smb_authority(value)
    clean_path = _text(share_path).replace("\\", "/").strip("/")
    if not authority or not clean_path:
        return value
    return "smb://" + authority + "/" + clean_path + "/"


def _add_smb_credentials(url, username, password):
    value = _normalize_smb_url(url)
    if not value.lower().startswith("smb://") or not username:
        return value
    authority = _smb_authority(value)
    if not authority or "@" in authority:
        return value
    credentials = _text(username)
    if password:
        credentials += ":" + _text(password)
    return "smb://" + credentials + "@" + value[6:]


def _with_smb_credentials(selected, source_url):
    value = _text(selected)
    source = _text(source_url)
    source_authority = _smb_authority(source)
    selected_authority = _smb_authority(value)
    if not source_authority or not selected_authority:
        return value
    if "@" not in source_authority or "@" in selected_authority:
        return value
    credentials = source_authority.split("@", 1)[0]
    return "smb://" + credentials + "@" + value[6:]


def _mask_smb_password(url):
    value = _text(url)
    authority = _smb_authority(value)
    if not authority or "@" not in authority:
        return value
    credentials, target = authority.split("@", 1)
    if ":" in credentials:
        username = credentials.split(":", 1)[0]
        credentials = username + ":***"
    masked_authority = credentials + "@" + target
    return "smb://" + masked_authority + value[6 + len(authority):]


def _strip_smb_credentials(path):
    value = _text(path)
    authority = _smb_authority(value)
    if not authority or "@" not in authority:
        return value
    return "smb://" + authority.split("@", 1)[1] + value[6 + len(authority):]


def _relative_from_root(path, root):
    candidates = (
        (_text(path), _text(root)),
        (_strip_smb_credentials(path), _strip_smb_credentials(root)),
    )
    for candidate_path, candidate_root in candidates:
        normalized_path = _text(candidate_path).replace("\\", "/")
        normalized_root = _normalize_folder(candidate_root)
        if not normalized_root:
            continue
        if normalized_path.lower().startswith(normalized_root.lower()):
            return normalized_path[len(normalized_root):].lstrip("/")
    return ""


def _is_supported_image(path):
    lower = _text(path).lower()
    for extension in SUPPORTED_EXTENSIONS:
        if lower.endswith(extension):
            return True
    return False


def _has_known_file_extension(path):
    lower = _text(path).lower()
    for extension in SUPPORTED_EXTENSIONS + SKIP_SCAN_EXTENSIONS:
        if lower.endswith(extension):
            return True
    return False


def _join_stellar_path(root, filename):
    clean_root = _text(root).replace("/", "\\").rstrip("\\")
    clean_file = _text(filename).replace("/", "\\").lstrip("\\")
    if not clean_file:
        return clean_root
    return clean_root + "\\" + clean_file


def _stellar_device_root(settings):
    return DEFAULT_STELLAR_ROOT


def _stellar_relative_path(settings, selected_path):
    relative_path = _relative_from_root(selected_path, settings.get("smb_url"))
    if relative_path:
        return relative_path
    relative_path = _relative_from_root(selected_path, settings.get("smb_root"))
    if relative_path:
        return relative_path
    return _basename(selected_path)


def _stellar_path_for_selected(settings, selected_path):
    return _join_stellar_path(_stellar_device_root(settings), _stellar_relative_path(settings, selected_path))


def _display_title(path):
    name = _basename(path)
    lower = name.lower()
    for extension in SUPPORTED_EXTENSIONS:
        if lower.endswith(extension):
            name = name[:-len(extension)]
            break
    name = name.replace("_", " ").replace("-", " ").strip()
    while "  " in name:
        name = name.replace("  ", " ")
    return name or _basename(path)


def _game_row(settings, selected):
    relative_path = _stellar_relative_path(settings, selected)
    stellar_path = _join_stellar_path(_stellar_device_root(settings), relative_path)
    folder = _dirname(relative_path).rstrip("/")
    extension = _basename(selected).rsplit(".", 1)[-1].upper()
    detail = extension if not folder else "%s  %s" % (extension, folder)
    return _row(_display_title(selected), detail, "game", {
        "selected_path": selected,
        "relative_path": relative_path,
        "stellar_path": stellar_path,
    })


def _notify(message, duration=2500):
    try:
        xbmc.executebuiltin("Notification(%s, %s, %d)" % (APP_TITLE, _clean(message, 42), duration))
    except Exception:
        pass


def _find_helper():
    for source in SKIN_HELPER_PATHS:
        if os.path.exists(source):
            return source

    if os.path.exists(FALLBACK_HELPER_PATH):
        return FALLBACK_HELPER_PATH

    return ""


def _browse_for_helper():
    dialog = xbmcgui.Dialog()
    selected = dialog.browse(1, "Select official attach.xbe", "files", ".xbe", False, False, "")
    if not selected:
        return False
    try:
        _ensure_dir(FALLBACK_STELLAR_DIR)
        shutil.copyfile(selected, FALLBACK_HELPER_PATH)
        return True
    except Exception as exc:
        _log("Failed to install helper from %s: %s" % (selected, exc))
        dialog.ok(APP_TITLE, "Could not copy helper XBE.", _clean(exc, 80))
    return False


def _download_helper():
    dialog = xbmcgui.Dialog()
    progress = xbmcgui.DialogProgress()
    progress.create(APP_TITLE, "Downloading official stellar-attach", "Please wait.")

    temp_path = FALLBACK_HELPER_PATH + ".download"
    downloaded = 0

    try:
        _ensure_dir(FALLBACK_STELLAR_DIR)
        response = urllib2.urlopen(ATTACH_DOWNLOAD_URL, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        handle = open(temp_path, "wb")
        try:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress.iscanceled():
                    raise Exception("Download canceled")
                progress.update(0, "Downloading official stellar-attach", "%d KB" % (downloaded / 1024))
        finally:
            handle.close()

        if downloaded < MIN_ATTACH_XBE_BYTES:
            raise Exception("Downloaded file is too small")

        if os.path.exists(FALLBACK_HELPER_PATH):
            try:
                os.remove(FALLBACK_HELPER_PATH)
            except Exception:
                pass
        shutil.move(temp_path, FALLBACK_HELPER_PATH)
        progress.close()
        _notify("attach.xbe installed", 2000)
        return True
    except Exception as exc:
        try:
            progress.close()
        except Exception:
            pass
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        _log("Failed to download attach.xbe: %s" % exc)
        dialog.ok(APP_TITLE, "Could not download official attach.xbe.", _clean(exc, 80))
    return False


def _install_helper_interactive():
    dialog = xbmcgui.Dialog()
    choice = dialog.select("Install attach.xbe", [
        "Download official stellar-attach",
        "Browse for official attach.xbe",
        "Cancel",
    ])
    if choice == 0:
        return _download_helper()
    if choice == 1:
        return _browse_for_helper()
    return False


def _ensure_helper():
    helper_path = _find_helper()
    if helper_path:
        return helper_path

    if _install_helper_interactive():
        return _find_helper()

    return ""


def _set_browse_start(settings):
    dialog = xbmcgui.Dialog()
    selected = dialog.browse(0, "Select SMB game root", SMB_BROWSE_SHARES, "", False, False, settings.get("smb_root") or settings.get("browse_start", ""))
    if selected:
        settings["smb_root"] = _normalize_folder(selected)
        settings["browse_start"] = settings["smb_root"]
        _save_settings(settings)
        _clear_cache()
        return True
    return False


def _set_smb_connection(settings):
    dialog = xbmcgui.Dialog()
    default_url = settings.get("smb_url") or settings.get("smb_root") or "smb://"
    smb_url = _keyboard(default_url, "SMB share URL")
    if smb_url is None:
        return False

    smb_url = _normalize_smb_url(smb_url)
    if not smb_url.lower().startswith("smb://"):
        dialog.ok(APP_TITLE, "SMB URL must start with:", "smb://")
        return False

    if "@" not in _smb_authority(smb_url):
        username = _keyboard(settings.get("smb_user") or "", "SMB username (blank guest)")
        if username is None:
            return False
        username = _text(username).strip()
        if username:
            password = _keyboard("", "SMB password", True)
            if password is None:
                return False
            smb_url = _add_smb_credentials(smb_url, username, password)
            settings["smb_user"] = username
        else:
            settings["smb_user"] = ""

    share_path = _smb_path_part(smb_url)
    if not share_path:
        default_share_path = settings.get("smb_share_path") or _smb_path_part(settings.get("smb_root") or "")
        share_path = _keyboard(default_share_path, "Stellar net0 SMB share/path")
        if share_path is None:
            return False
        share_path = _text(share_path).replace("\\", "/").strip("/")
        if not share_path:
            dialog.ok(APP_TITLE, "Enter the SMB share used by Stellar net0.", "Example: retronas")
            return False
        smb_url = _smb_url_with_path(smb_url, share_path)

    settings["smb_url"] = smb_url
    settings["smb_share_path"] = _smb_path_part(smb_url)
    if not _ensure_stellar_source(smb_url):
        dialog.ok(APP_TITLE, "Could not add XBMC SMB source.", "The URL was saved, but browsing may need a reboot.")

    selected = dialog.browse(0, "Select Xbox ISO folder", SMB_BROWSE_SHARES, "", False, False, smb_url)
    selected = _text(selected).strip()
    if selected:
        selected = _with_smb_credentials(selected, smb_url)
        settings["smb_root"] = _normalize_folder(selected)
    else:
        settings["smb_root"] = _normalize_folder(smb_url)

    settings["browse_start"] = settings["smb_root"]
    _save_settings(settings)
    _clear_cache()
    return True


def _write_attach_ini(helper_path, stellar_path):
    helper_dir = _path_dirname(helper_path)
    attach_ini_path = _path_join(helper_dir, "attach.ini")
    if helper_dir:
        _ensure_dir(helper_dir)
    handle = open(attach_ini_path, "wb")
    try:
        handle.write("VIRTUAL_IMAGE_FILE_PATH=%s\r\n" % stellar_path)
    finally:
        handle.close()


def _launch_stellar_path(settings, stellar_path, selected_path, title):
    dialog = xbmcgui.Dialog()

    stellar_path = _text(stellar_path)
    if not stellar_path:
        stellar_path = _stellar_path_for_selected(settings, selected_path)

    helper_path = _ensure_helper()
    if not helper_path:
        return

    try:
        _write_attach_ini(helper_path, stellar_path)
    except Exception as exc:
        _log("Failed to write attach.ini: %s" % exc)
        dialog.ok(APP_TITLE, "Could not write attach.ini.", _clean(exc, 80))
        return

    if selected_path:
        settings["last_iso"] = selected_path
        settings["browse_start"] = _dirname(selected_path)
        _save_settings(settings)

    _notify("Launching %s" % (title or _basename(selected_path) or "NetISO"), 2000)
    xbmc.executebuiltin("XBMC.RunXBE(%s)" % helper_path)


def _launch_iso(settings):
    dialog = xbmcgui.Dialog()

    _set_file_browser_mode(True)
    try:
        selected = dialog.browse(1, "Select Stellar ISO or CSO", SMB_BROWSE_SHARES, "", False, False, settings.get("browse_start", ""))
    finally:
        _set_file_browser_mode(False)
    if not selected:
        return

    if not _is_supported_image(selected):
        dialog.ok(APP_TITLE, "Select an ISO or CSO file.")
        return

    stellar_path = _stellar_path_for_selected(settings, selected)
    _launch_stellar_path(settings, stellar_path, selected, _display_title(selected))


def _show_settings(settings):
    helper = _find_helper() or "Not installed"
    xbmcgui.Dialog().ok(APP_TITLE, "Stellar net0 SMB share:", _mask_smb_password(settings.get("smb_url") or ""), "")
    xbmcgui.Dialog().ok(APP_TITLE, "Xbox ISO folder:", _mask_smb_password(settings.get("smb_root") or ""), "")
    xbmcgui.Dialog().ok(APP_TITLE, "Helper:", helper, "")


def _settings_rows(settings):
    helper_detail = "Bundled helper found" if _find_helper() else "Install or browse for attach.xbe"
    rows = []
    if not settings.get("smb_root"):
        rows.append(_row("Connect SMB Share", "Set the same SMB share Stellar BIOS maps as net0", "connect_smb", {}))
    if settings.get("smb_root"):
        rows.append(_row("Change SMB Share", _mask_smb_password(settings.get("smb_url") or settings.get("smb_root")), "connect_smb", {}))
    rows.append(_row("Install attach.xbe", helper_detail, "install_helper", {}))
    rows.append(_row("Show Settings", "View saved paths and helper status", "show_settings", {}))
    return rows


def _list_folder(path):
    try:
        names = os.listdir(path)
    except Exception as exc:
        return None, exc

    cleaned = []
    for name in names:
        name = _text(name)
        if not name or name in (".", ".."):
            continue
        cleaned.append(name)
    try:
        cleaned.sort(key=lambda value: value.lower())
    except Exception:
        cleaned.sort()
    return cleaned, None


def _looks_like_folder(path, name):
    try:
        if os.path.isdir(path):
            return True
    except Exception:
        pass
    if _has_known_file_extension(name):
        return False
    return True


def _scan_folder(settings, folder, depth, rows, errors):
    if len(rows) >= SCAN_MAX_ITEMS or depth > SCAN_MAX_DEPTH:
        return

    names, error = _list_folder(folder)
    if error is not None:
        if len(errors) < 4:
            errors.append("%s: %s" % (_clean(folder, 44), _clean(error, 60)))
        return

    folders = []
    for name in names:
        child = name if name.find("://") >= 0 else _browse_join(folder, name)
        if _is_supported_image(child):
            rows.append(_game_row(settings, child))
            if len(rows) >= SCAN_MAX_ITEMS:
                return
        elif depth < SCAN_MAX_DEPTH and _looks_like_folder(child, name):
            folders.append(child)

    for child_folder in folders:
        if len(rows) >= SCAN_MAX_ITEMS:
            return
        _scan_folder(settings, child_folder, depth + 1, rows, errors)


def _scan_games(settings):
    root = settings.get("smb_root") or settings.get("browse_start") or ""
    if not root:
        return [], ["SMB root is not set"]

    rows = []
    errors = []
    _scan_folder(settings, root, 0, rows, errors)
    try:
        rows.sort(key=lambda row: row.get("title", "").lower())
    except Exception:
        pass
    return rows, errors


def _open_settings():
    settings = _load_settings()
    _show_window()
    _render("Stellar Net ISO Settings", "Set the SMB share Stellar maps as net0, then choose the Xbox ISO folder.", _settings_rows(settings), "Settings are saved to the XBMC profile.")


def _browse_iso_entry():
    settings = _load_settings()
    if not settings.get("smb_root"):
        _open_settings()
        return
    _launch_iso(settings)


def _open_root(page=0):
    settings = _load_settings()
    _show_window()

    if not settings.get("smb_root"):
        _render(APP_TITLE, "One-time setup is needed before games can launch directly.", _settings_rows(settings), "Connect the SMB share used by Stellar net0.")
        return

    _render(APP_TITLE, "Scanning SMB game root.", [_row("Scanning...", _clean(settings.get("smb_root"), 72), "noop", {})], "Please wait.")
    rows, errors = _scan_games(settings)
    if rows:
        _render(APP_TITLE, "Choose a network ISO to launch with Stellar.", rows, "%d games found." % len(rows), page)
        return

    status = "No ISO or CSO files found."
    if errors:
        status = "Could not scan SMB root. Use Stellar Net ISO or Settings."
        _log("Scan errors: %s" % "; ".join(errors))
    _render(APP_TITLE, "The saved share did not return a game list.", _settings_rows(settings), status)


def _handle_select(index):
    state = _load_state()
    visible_rows = state.get("visible_rows") or []
    try:
        selected_index = int(_text(index).replace("Select", "")) - 1
    except Exception:
        selected_index = 0
    if selected_index < 0 or selected_index >= len(visible_rows):
        return

    row = visible_rows[selected_index]
    action = row.get("action")
    data = row.get("data") or {}
    settings = _load_settings()

    if action == "game":
        _launch_stellar_path(settings, data.get("stellar_path"), data.get("selected_path"), row.get("title"))
    elif action == "browse_iso":
        _launch_iso(settings)
    elif action == "page":
        _render(state.get("title") or APP_TITLE, state.get("subtitle") or "", state.get("rows") or [], state.get("status") or "", data.get("page", 0))
    elif action == "refresh":
        _open_root()
    elif action == "settings":
        _open_settings()
    elif action == "connect_smb":
        if _set_smb_connection(settings):
            _open_root()
        else:
            _open_settings()
    elif action == "set_smb_root":
        if _set_browse_start(settings):
            _open_root()
        else:
            _open_settings()
    elif action == "install_helper":
        if _install_helper_interactive():
            _notify("Helper installed", 2000)
        _open_settings()
    elif action == "show_settings":
        _show_settings(settings)
        _open_settings()


def main():
    action = ""
    if len(sys.argv) > 1:
        action = _text(sys.argv[1])

    if action.startswith("Select"):
        _handle_select(action)
    elif action == "BrowseISO":
        _browse_iso_entry()
    elif action == "Refresh":
        _open_root()
    elif action == "Settings":
        _open_settings()
    elif action == "Back":
        _open_root()
    else:
        _browse_iso_entry()


if __name__ == "__main__":
    main()
