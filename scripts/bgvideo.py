# -*- coding: utf-8 -*-

import os
import socket
import time
import urllib2

import xbmc
import xbmcgui


VIDEO_DIR = "E:\\BGVideo"
VIDEO_PATH = "E:\\BGVideo\\BGVideo.avi"
TEMP_VIDEO_PATH = VIDEO_PATH + ".part"
VIDEO_URLS = [
    "http://10.4.20.210:8501/api/portals/xbmc_portal/api/tater-xbmc/v1/bgvideo.avi",
    "https://github.com/TaterTotterson/skin.cortana.ai-xbmc/releases/latest/download/BGVideo.avi",
]

DOWNLOAD_TIMEOUT_SECONDS = 60
DOWNLOAD_CHUNK_BYTES = 64 * 1024
MIN_VIDEO_BYTES = 10 * 1024 * 1024
HOME_REFOCUS_ATTEMPTS = 4
HOME_REFOCUS_DELAY_SECONDS = 0.5


def log(message):
    try:
        xbmc.log("CortanaBGVideo: %s" % message, xbmc.LOGNOTICE)
    except Exception:
        try:
            print("CortanaBGVideo: %s" % message)
        except Exception:
            pass


def ensure_video_dir():
    if os.path.isdir(VIDEO_DIR):
        return True

    try:
        os.makedirs(VIDEO_DIR)
        return True
    except Exception as error:
        log("Unable to create %s: %s" % (VIDEO_DIR, error))
        return False


def has_video():
    try:
        return os.path.exists(VIDEO_PATH) and os.path.getsize(VIDEO_PATH) >= MIN_VIDEO_BYTES
    except Exception:
        return False


def normalize_path(path):
    try:
        return path.replace("/", "\\").lower()
    except Exception:
        return ""


def get_playing_file(player):
    try:
        return player.getPlayingFile()
    except Exception:
        return ""


def should_start_background_video():
    player = xbmc.Player()

    try:
        if not player.isPlaying():
            return True
    except Exception:
        return True

    current_file = normalize_path(get_playing_file(player))
    if current_file == normalize_path(VIDEO_PATH):
        log("Background video is already playing")
    elif current_file:
        log("Media is already playing; leaving it alone: %s" % current_file)
    else:
        log("Media is already playing; leaving it alone")

    return False


def remove_temp_video():
    try:
        if os.path.exists(TEMP_VIDEO_PATH):
            os.remove(TEMP_VIDEO_PATH)
    except Exception as error:
        log("Unable to remove temp download: %s" % error)


def download_video_from_url(video_url):
    if not ensure_video_dir():
        return False

    remove_temp_video()

    progress = xbmcgui.DialogProgress()
    progress.create("Cortana Background", "Downloading BGVideo.avi", "This only happens once.")

    response = None
    output = None

    try:
        socket.setdefaulttimeout(DOWNLOAD_TIMEOUT_SECONDS)
        request = urllib2.Request(video_url)
        request.add_header("User-Agent", "XBMC4Xbox Cortana Skin")
        response = urllib2.urlopen(request)

        total = 0
        try:
            total = int(response.info().getheader("Content-Length"))
        except Exception:
            total = 0

        output = open(TEMP_VIDEO_PATH, "wb")
        downloaded = 0

        while True:
            if progress.iscanceled():
                log("Download canceled by user")
                return False

            chunk = response.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break

            output.write(chunk)
            downloaded += len(chunk)

            if total > 0:
                percent = int((downloaded * 100) / total)
                progress.update(percent, "Downloading BGVideo.avi", "%d%% complete" % percent)
            else:
                progress.update(0, "Downloading BGVideo.avi", "%d MB downloaded" % (downloaded / 1048576))

        output.close()
        output = None

        if total > 0 and downloaded != total:
            log("Download incomplete: got %d of %d bytes" % (downloaded, total))
            return False

        if downloaded < MIN_VIDEO_BYTES:
            log("Downloaded file is too small: %d bytes" % downloaded)
            return False

        try:
            if os.path.exists(VIDEO_PATH):
                os.remove(VIDEO_PATH)
        except Exception as error:
            log("Unable to replace existing video: %s" % error)
            return False

        os.rename(TEMP_VIDEO_PATH, VIDEO_PATH)
        log("Downloaded %s" % VIDEO_PATH)
        return True

    except Exception as error:
        log("Download failed: %s" % error)
        return False

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

        if not has_video():
            remove_temp_video()


def download_video():
    for video_url in VIDEO_URLS:
        log("Trying download URL: %s" % video_url)
        if download_video_from_url(video_url):
            return True

    log("All BGVideo download URLs failed")
    return False


def play_video():
    # The ",1" argument starts video playback without switching to fullscreen.
    log("Starting background video in windowed mode: %s" % VIDEO_PATH)
    xbmc.executebuiltin("PlayMedia(%s,1,noresume)" % VIDEO_PATH)

    player = xbmc.Player()
    wait_time = 0.0
    max_wait = 10.0

    while not player.isPlaying() and wait_time < max_wait:
        time.sleep(0.1)
        wait_time += 0.1

    for _ in range(HOME_REFOCUS_ATTEMPTS):
        time.sleep(HOME_REFOCUS_DELAY_SECONDS)
        xbmc.executebuiltin("ActivateWindow(Home)")


def main():
    if not has_video():
        log("%s not found; downloading from configured source" % VIDEO_PATH)
        if not download_video():
            return

    if not should_start_background_video():
        return

    play_video()


if __name__ == "__main__":
    main()
