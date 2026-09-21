"""Jarvis agent core."""
import os
import sys

if os.name == "nt":
    try:
        import pyautogui
        pyautogui.PAUSE = 0
        pyautogui.FAILSAFE = False
        pyautogui.MINIMUM_DURATION = 0
        pyautogui.MINIMUM_SLEEP = 0
    except ImportError:
        pass
