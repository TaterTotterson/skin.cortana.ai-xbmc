# -*- coding: utf-8 -*-

import os
import re
import shutil
import socket
import sys
import time
import urllib2
import zipfile

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
API_BASE = "http://10.4.20.210:8501/api/portals/xbmc_portal/api/tater-xbmc/v1"
LATEST_URL = API_BASE + "/skin/latest"
ZIP_URL = API_BASE + "/skin/latest.zip"
HTTP_TIMEOUT_SECONDS = 8
DOWNLOAD_TIMEOUT_SECONDS = 120
DOWNLOAD_CHUNK_BYTES = 64 * 1024
MIN_ZIP_BYTES = 64 * 1024
CACHE_SECONDS = 6 * 60 * 60
BGVIDEO_PATH = "E:\\BGVideo\\BGVideo.avi"

PROFILE_DIR = xbmc.translatePath("special://profile")
SETTINGS_FILE = os.path.join(PROFILE_DIR, "cortana_chat_settings.json")
TATERTUBE_SETTINGS_FILE = os.path.join(PROFILE_DIR, "tater_tube_settings.json")
CACHE_FILE = os.path.join(PROFILE_DIR, "tater_skin_update_cache.json")
UPDATE_DIR = os.path.join(PROFILE_DIR, "tater_skin_update")
ZIP_PATH = os.path.join(UPDATE_DIR, "skin.cortana.ai.latest.zip")
STAGING_DIR = os.path.join(UPDATE_DIR, "staged")

ALLOWED_TOP_LEVEL = set([
    "720p",
    "backgrounds",
    "button_icons",
    "colors",
    "fonts",
    "language",
    "media",
    "scripts",
    "sounds",
])
ALLOWED_ROOT_FILES = set([
    "changelog.txt",
    "skin.xml",
])
SKIP_TOP_LEVEL = set([
    ".git",
    ".github",
    "bg",
    "screenshots",
])


def _log(message):
    try:
        xbmc.log("TaterSkinUpdate: %s" % message, xbmc.LOGNOTICE)
    except Exception:
        try:
            print("TaterSkinUpdate: %s" % message)
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


def _clean(value, limit=120):
    text = _text(value).replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].strip()
    return text


def _window():
    return xbmcgui.Window(PROPERTY_WINDOW_ID)


def _set_property(name, value):
    try:
        _window().setProperty("TaterSkin.%s" % name, _text(value))
    except Exception as exc:
        _log("Failed to set property %s: %s" % (name, exc))
    try:
        text = _text(value).replace("\r", " ").replace("\n", " ").replace(",", " ").replace("(", " ").replace(")", " ").strip()
        xbmc.executebuiltin("Skin.SetString(TaterSkin.%s,%s)" % (name, text))
    except Exception as exc:
        _log("Failed to set skin string %s: %s" % (name, exc))


def _notify(message, duration=2500):
    try:
        xbmc.executebuiltin("Notification(Tater, %s, %d)" % (_clean(message, 42), duration))
    except Exception:
        pass


def _ensure_dir(path):
    if not path or os.path.isdir(path):
        return
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        _ensure_dir(parent)
    try:
        os.mkdir(path)
    except OSError:
        if not os.path.isdir(path):
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
        if directory:
            _ensure_dir(directory)
        handle = open(path, "wb")
        try:
            blob = json.dumps(value, separators=(",", ":"))
            try:
                if isinstance(blob, unicode):
                    blob = blob.encode("utf-8")
            except Exception:
                pass
            handle.write(blob)
        finally:
            handle.close()
    except Exception as exc:
        _log("Failed to write %s: %s" % (path, exc))


def _skin_root():
    candidates = [
        xbmc.translatePath("special://skin"),
        "Q:\\skin\\skin.cortana.ai",
        "E:\\dashboard\\skin\\skin.cortana.ai",
    ]
    for path in candidates:
        if path and os.path.exists(os.path.join(path, "skin.xml")):
            return path
    return candidates[0]


def _installed_version():
    skin_xml = os.path.join(_skin_root(), "skin.xml")
    try:
        handle = open(skin_xml, "rb")
        try:
            data = handle.read()
        finally:
            handle.close()
        match = re.search(r"<version>\s*([^<]+?)\s*</version>", data)
        if match:
            return _clean(match.group(1), 32)
    except Exception as exc:
        _log("Failed to read installed skin version: %s" % exc)
    return "unknown"


def _version_parts(version):
    parts = []
    for token in re.findall(r"\d+", _text(version)):
        try:
            parts.append(int(token))
        except Exception:
            pass
    return parts or [0]


def _compare_versions(left, right):
    a = _version_parts(left)
    b = _version_parts(right)
    count = max(len(a), len(b))
    while len(a) < count:
        a.append(0)
    while len(b) < count:
        b.append(0)
    for index in range(count):
        if a[index] > b[index]:
            return 1
        if a[index] < b[index]:
            return -1
    return 0


def _api_key():
    settings = _read_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        return ""
    return _clean(settings.get("api_key"), 160)


def _request_headers(accept="application/json"):
    headers = {
        "Accept": accept,
        "User-Agent": "XBMC4Xbox Tater Skin Updater",
    }
    api_key = _api_key()
    if api_key:
        headers["X-Tater-Token"] = api_key
    return headers


def _urlopen(url, timeout, accept="application/json"):
    socket.setdefaulttimeout(timeout)
    request = urllib2.Request(url, headers=_request_headers(accept))
    return urllib2.urlopen(request, timeout=timeout)


def _fetch_latest():
    response = _urlopen(LATEST_URL, HTTP_TIMEOUT_SECONDS, "application/json")
    try:
        payload = json.loads(response.read())
    finally:
        try:
            response.close()
        except Exception:
            pass
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(_clean(payload.get("detail") if isinstance(payload, dict) else "bad response", 90))
    return payload


def _cached_latest():
    cache = _read_json(CACHE_FILE, {})
    if not isinstance(cache, dict):
        return {}
    latest = cache.get("latest")
    if isinstance(latest, dict):
        return latest
    return {}


def _cache_age():
    cache = _read_json(CACHE_FILE, {})
    try:
        return time.time() - float(cache.get("checked_at") or 0)
    except Exception:
        return 999999


def _save_latest(payload):
    _write_json(CACHE_FILE, {"checked_at": time.time(), "latest": payload})


def _tube_settings():
    settings = _read_json(TATERTUBE_SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        settings = {}
    server = _clean(settings.get("server_url"), 90) or "Not paired"
    player = _clean(settings.get("player_name"), 60) or "Original Xbox"
    return server, player


def _apply_info(latest=None, status=None):
    installed = _installed_version()
    if latest is None:
        latest = _cached_latest()
    if not isinstance(latest, dict):
        latest = {}

    latest_version = _clean(latest.get("version"), 32) or "--"
    latest_tag = _clean(latest.get("tag"), 32) or "--"

    if status is None:
        if latest_version == "--":
            status = "Press Check Update to ask Tater for the latest GitHub release."
        else:
            compare = _compare_versions(latest_version, installed)
            if compare > 0:
                status = "Update available. Install %s from the latest GitHub release." % latest_version
            elif compare == 0:
                status = "Skin is up to date."
            else:
                status = "Installed build is newer than the latest GitHub release."

    server, player = _tube_settings()
    _set_property("InstalledVersion", installed)
    _set_property("LatestVersion", latest_version)
    _set_property("LatestTag", latest_tag)
    _set_property("Status", status)
    _set_property("TaterTubeServer", server)
    _set_property("TaterTubePlayer", player)


def _check_latest(silent=False):
    _apply_info(status="Checking GitHub release through Tater...")
    try:
        payload = _fetch_latest()
        _save_latest(payload)
        _apply_info(payload)
        if not silent:
            _notify("Update check complete")
        return payload
    except Exception as exc:
        message = "Unable to check update: %s" % _clean(exc, 80)
        _apply_info(status=message)
        if not silent:
            try:
                xbmcgui.Dialog().ok("Tater Skin Update", message)
            except Exception:
                pass
        return {}


def _content_length(info):
    try:
        value = info.getheader("Content-Length")
    except Exception:
        try:
            value = info.get("Content-Length")
        except Exception:
            value = 0
    try:
        return int(value)
    except Exception:
        return 0


def _download_zip():
    _ensure_dir(UPDATE_DIR)
    temp_path = ZIP_PATH + ".part"
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except Exception:
        pass

    progress = xbmcgui.DialogProgress()
    progress.create("Tater Skin Update", "Downloading skin release", "Please wait.")
    response = None
    output = None

    try:
        response = _urlopen(ZIP_URL, DOWNLOAD_TIMEOUT_SECONDS, "application/zip")
        total = _content_length(response.info())
        output = open(temp_path, "wb")
        downloaded = 0

        while True:
            if progress.iscanceled():
                raise RuntimeError("Download canceled.")
            chunk = response.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            output.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                percent = int((downloaded * 100) / total)
                progress.update(percent, "Downloading skin release", "%d%% complete" % percent)
            else:
                progress.update(0, "Downloading skin release", "%d KB downloaded" % (downloaded / 1024))

        output.close()
        output = None

        if downloaded < MIN_ZIP_BYTES:
            raise RuntimeError("Downloaded release ZIP is too small.")
        if total > 0 and downloaded != total:
            raise RuntimeError("Download incomplete.")

        if os.path.exists(ZIP_PATH):
            os.remove(ZIP_PATH)
        os.rename(temp_path, ZIP_PATH)
        return ZIP_PATH

    finally:
        try:
            if output:
                output.close()
        except Exception:
            pass
        try:
            if response:
                response.close()
        except Exception:
            pass
        try:
            progress.close()
        except Exception:
            pass
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


def _member_relative_path(name):
    path = _text(name).replace("\\", "/").strip("/")
    if not path or path.endswith("/"):
        return ""

    parts = path.split("/")
    if parts[0] in ALLOWED_TOP_LEVEL or parts[0] in ALLOWED_ROOT_FILES:
        rel = path
    elif len(parts) > 1:
        rel = "/".join(parts[1:])
    else:
        rel = parts[0]
    rel = rel.strip("/")
    if not rel:
        return ""

    top = rel.split("/", 1)[0]
    if top in SKIP_TOP_LEVEL:
        return ""
    if top.startswith("."):
        return ""
    if rel.endswith(".pyc") or rel.endswith(".pyo") or rel.endswith(".DS_Store"):
        return ""
    if top in ALLOWED_TOP_LEVEL or rel in ALLOWED_ROOT_FILES:
        return rel
    return ""


def _safe_target(root, rel_path):
    target = os.path.abspath(os.path.join(root, rel_path.replace("/", os.sep)))
    base = os.path.abspath(root)
    try:
        if not target.lower().startswith(base.lower()):
            return ""
    except Exception:
        if not target.startswith(base):
            return ""
    return target


def _stage_zip(zip_path):
    if os.path.isdir(STAGING_DIR):
        shutil.rmtree(STAGING_DIR)
    _ensure_dir(STAGING_DIR)

    archive = zipfile.ZipFile(zip_path, "r")
    try:
        names = archive.namelist()
        rel_names = []
        for name in names:
            rel = _member_relative_path(name)
            if rel:
                rel_names.append((name, rel))
        if not rel_names:
            raise RuntimeError("Release ZIP does not contain a skin payload.")

        progress = xbmcgui.DialogProgress()
        progress.create("Tater Skin Update", "Preparing update", "Staging skin files.")
        try:
            total = len(rel_names)
            for index, item in enumerate(rel_names):
                name, rel = item
                if progress.iscanceled():
                    raise RuntimeError("Update canceled.")
                target = _safe_target(STAGING_DIR, rel)
                if not target:
                    continue
                _ensure_dir(os.path.dirname(target))
                data = archive.read(name)
                handle = open(target, "wb")
                try:
                    handle.write(data)
                finally:
                    handle.close()
                percent = int(((index + 1) * 100) / total)
                progress.update(percent, "Preparing update", rel[:70])
        finally:
            try:
                progress.close()
            except Exception:
                pass
    finally:
        archive.close()


def _copy_staged_skin():
    root = _skin_root()
    progress = xbmcgui.DialogProgress()
    progress.create("Tater Skin Update", "Installing update", root)

    files = []
    for base, _dirs, names in os.walk(STAGING_DIR):
        for name in names:
            source = os.path.join(base, name)
            rel = source[len(STAGING_DIR):].lstrip("\\/")
            files.append((source, rel))

    if not files:
        raise RuntimeError("No staged files found.")

    total = len(files)
    try:
        for index, item in enumerate(files):
            if progress.iscanceled():
                raise RuntimeError("Update canceled.")
            source, rel = item
            target = _safe_target(root, rel)
            if not target:
                continue
            _ensure_dir(os.path.dirname(target))
            shutil.copyfile(source, target)
            percent = int(((index + 1) * 100) / total)
            progress.update(percent, "Installing update", rel[:70])
    finally:
        try:
            progress.close()
        except Exception:
            pass


def _playing_bgvideo():
    try:
        player = xbmc.Player()
        if not player.isPlaying():
            return False
        current = player.getPlayingFile().replace("/", "\\").lower()
        return current == BGVIDEO_PATH.lower()
    except Exception:
        return False


def _reload_skin():
    try:
        if _playing_bgvideo():
            xbmc.Player().stop()
            xbmc.sleep(500)
    except Exception:
        pass
    _apply_info(status="Reloading skin...")
    xbmc.executebuiltin("ActivateWindow(Home)")
    xbmc.sleep(250)
    xbmc.executebuiltin("XBMC.ReloadSkin()")


def _update_skin():
    latest = _check_latest(silent=True)
    if not latest:
        return

    installed = _installed_version()
    latest_version = _clean(latest.get("version"), 32)
    if not latest_version:
        _apply_info(latest, "Latest release did not include a version.")
        return

    compare = _compare_versions(latest_version, installed)
    if compare <= 0:
        message = "Latest GitHub release is not newer than this skin. Reinstall it anyway?"
        try:
            if not xbmcgui.Dialog().yesno("Tater Skin Update", message, "Installed: %s  Latest: %s" % (installed, latest_version)):
                _apply_info(latest)
                return
        except Exception:
            _apply_info(latest)
            return
    else:
        try:
            if not xbmcgui.Dialog().yesno("Tater Skin Update", "Install Cortana AI XBMC Skin %s now?" % latest_version, "XBMC will reload the skin when finished."):
                _apply_info(latest)
                return
        except Exception:
            pass

    try:
        _apply_info(latest, "Downloading update...")
        zip_path = _download_zip()
        _apply_info(latest, "Preparing update...")
        _stage_zip(zip_path)
        _apply_info(latest, "Installing update...")
        _copy_staged_skin()
        _apply_info(latest, "Skin updated to %s. Reloading..." % latest_version)
        _notify("Skin updated")
        _reload_skin()
    except Exception as exc:
        message = "Update failed: %s" % _clean(exc, 90)
        _apply_info(latest, message)
        try:
            xbmcgui.Dialog().ok("Tater Skin Update", message)
        except Exception:
            pass


def _open_blade():
    _apply_info()


def _main():
    action = "Open"
    if len(sys.argv) > 1:
        action = _clean(sys.argv[1], 32)

    if action == "Check":
        _check_latest(silent=False)
    elif action == "Update":
        _update_skin()
    elif action == "Reload":
        _reload_skin()
    else:
        _open_blade()


if __name__ == "__main__":
    _main()
