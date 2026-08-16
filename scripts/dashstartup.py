# -*- coding: utf-8 -*-

import os
import time

import xbmc
import xbmcgui


PROPERTY_WINDOW_ID = 10000
START_DELAY_SECONDS = 8
REFRESH_SCRIPTS = [
    ("taterweather.py", ""),
    ("skinupdate.py", ",Open"),
]


def log(message):
    try:
        xbmc.log("TaterDashStartup: %s" % message, xbmc.LOGNOTICE)
    except Exception:
        try:
            print("TaterDashStartup: %s" % message)
        except Exception:
            pass


def mark_queued():
    try:
        xbmcgui.Window(PROPERTY_WINDOW_ID).setProperty("TaterDashStartup.Queued", "1")
    except Exception as error:
        log("Unable to set startup property: %s" % error)


def sleep_seconds(seconds):
    try:
        xbmc.sleep(int(seconds * 1000))
    except Exception:
        time.sleep(seconds)


def run_script(script_name, arguments):
    script_path = os.path.join(os.path.dirname(__file__), script_name)
    try:
        xbmc.executebuiltin("RunScript(%s%s)" % (script_path, arguments))
        log("Queued refresh script: %s" % script_name)
    except Exception as error:
        log("Unable to queue refresh script %s: %s" % (script_name, error))


def main():
    mark_queued()
    sleep_seconds(START_DELAY_SECONDS)
    for script_name, arguments in REFRESH_SCRIPTS:
        run_script(script_name, arguments)


if __name__ == "__main__":
    main()
