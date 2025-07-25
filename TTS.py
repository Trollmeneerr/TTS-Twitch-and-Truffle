import os
import time
import json
import re
import subprocess
import numpy as np
import sounddevice as sd
import keyboard
import hashlib
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    WebDriverException,
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException
)

# ─── CONFIG ────────────────────────────────────────────────────────────────────
PIPER_EXE     = "./piper/piper.exe"
PIPER_MODEL   = "./models/en_US-amy-medium.onnx"    # Change this to your own model path if needed
SITE         = "https://chat.truffle.vip/browser-source/your-org/scene-x" # Change this to your own Truffle chat URL
SPOKEN_FILE   = "spoken_messages.json"
LINK_PATTERN  = re.compile(r"https?://\S+|www\.\S+")

# ─── HOTKEYS ─────────────────────────────────────────────────────────────────
PREFIX        = "!tts" 
HOTKEY_SKIP   = ";"
HOTKEY_TOGGLE_PREFIX = "ctrl+alt+p"
HOTKEY_TOGGLE_TTS = "ctrl+alt+t"

# ─── GLOBALS ───────────────────────────────────────────────────────────────────
tts_enabled    = True
stop_playback  = False
prefix_enabled = True
index = 0

# ─── BANNED WORDS ──────────────────────────────────────────────────────────────
if not os.path.exists('filter.json'):
    print("filter.json not found, creating default with no banned words.")
    with open('filter.json', 'w', encoding='utf-8') as f:
        json.dump({"banned_words": []}, f, indent=2)
        
with open('filter.json', 'r', encoding='utf-8') as f:
    BANNED_WORDS = json.load(f).get('banned_words', [])

# ─── HOTKEYS ───────────────────────────────────────────────────────────────────
def toggle_tts():
    global tts_enabled
    tts_enabled = not tts_enabled
    print("TTS Enabled:", tts_enabled)

def skip_playback():
    global stop_playback
    stop_playback = True
    sd.stop()
    
def toggle_prefix():
    global prefix_enabled, PREFIX
    prefix_enabled = not prefix_enabled
    PREFIX = "!tts" if prefix_enabled else ""   
    print("Prefix:", prefix_enabled, "\nCurrent Prefix:", PREFIX)

keyboard.add_hotkey(HOTKEY_TOGGLE_TTS, toggle_tts)
keyboard.add_hotkey(HOTKEY_SKIP, skip_playback)
keyboard.add_hotkey(HOTKEY_TOGGLE_PREFIX, toggle_prefix)

# ─── UTILS ─────────────────────────────────────────────────────────────────────
def get_platform_and_channel(url):
    p = urlparse(url)
    host = p.netloc.lower()
    path = p.path.strip("/").split("/")
    if "truffle" in host or "localhost" in host or "127.0.0.1" in host:
        return "truffle"
    return "unknown"

def load_spoken():
    if os.path.exists(SPOKEN_FILE):
        try:
            return set(json.load(open(SPOKEN_FILE, 'r', encoding='utf-8')))
        except json.JSONDecodeError:
            print("Warning: corrupt spoken file, starting fresh.")
    return set()

def save_spoken(spoken):
    json.dump(list(spoken), open(SPOKEN_FILE, 'w', encoding='utf-8'), indent=2)

def gen_id(user, msg):
    return hashlib.sha1(f"{user}:{msg}".encode()).hexdigest()

def contains_banned(text):
    tl = text.lower()
    norm = re.sub(r'[\s\.\-_]', '', tl)
    for bw in BANNED_WORDS:
        if re.search(rf"\b{re.escape(bw)}\b", tl):
            return True
        pattern = r"(?:\s*[\.\-_]?\s*)".join(re.escape(c) for c in bw)
        if re.search(pattern, tl) or norm == bw:
            return True
    return False

def contains_links(text):
    return bool(LINK_PATTERN.search(text))

def speak(text):
    global stop_playback
    print("🔊", text)
    cmd = [PIPER_EXE, "-m", PIPER_MODEL, "--output_raw"]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    raw, _ = p.communicate(text.encode())
    audio = np.frombuffer(raw, np.int16)

    stop_playback = False
    sd.play(audio, samplerate=22050, blocking=False)
    while sd.get_stream().active:
        if stop_playback:
            print("⏭ skipped")
            break
        time.sleep(0.1)
    sd.stop()

# ─── SCRAPERS ─────────────────────────────────────────────────────────────────
def scrape_truffle(driver, spoken):
    wait = WebDriverWait(driver, 10)
    try:
        wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(@class, 'chat-message')]")))
    except TimeoutException:
        print("No chat message containers found within the timeout.")
        return

    entries = driver.find_elements(By.XPATH, "//*[contains(@class, 'chat-message')]")
    queue = []
    for e in entries:
        try:
            msg_id = e.get_attribute('id')
            name = e.find_element(By.CLASS_NAME, "name")
            body = e.find_element(By.CLASS_NAME, "c-chat-message-body")

            name_txt = name.get_attribute('innerText').strip()
            body_txt = body.get_attribute('innerText').strip()

            if name_txt and body_txt and msg_id:
                    if msg_id not in spoken:
                        spoken.add(msg_id)
                        if tts_enabled:
                            if body_txt.lower().startswith(PREFIX):
                                tts_txt = body_txt[len(PREFIX):].strip()
                                if contains_banned(tts_txt) or contains_links(tts_txt):
                                    print(f"Filtered message: {tts_txt}")
                                    continue
                                full_txt = f"{name_txt} said {tts_txt}"
                                queue.append(full_txt)
                                print(f"Queued TTS: {full_txt}")
                            
        except (NoSuchElementException, StaleElementReferenceException):
                continue

    for i, msg in enumerate(queue):
        speak(msg)
        if i < len(queue) - 1:
            time.sleep(3)
        
# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    spoken   = load_spoken()
    chrome_opts = Options()
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--headless")
    chrome_opts.add_argument("--disable-dev-shm-usage")
    chrome_opts.add_argument("--silent")
    driver = webdriver.Chrome(options=chrome_opts)
    driver.get(SITE)
    
    if get_platform_and_channel(SITE) != "truffle":
        print("Unsupported platform.")
        driver.quit()
        exit(1)
    
    # Running the program
    print("Starting TTS scraper… Press Ctrl+C to stop.")
    
    try:
        while True:
            if index >= 30:
                index += 1
                print("Scanning for new messages…")
                if index >= 30:
                    index = 0
                                   
            scrape_truffle(driver, spoken)
            save_spoken(spoken)
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("Shutting down…")
        if os.path.exists(SPOKEN_FILE):
                os.remove(SPOKEN_FILE)
        driver.quit()
        
    finally:
        if os.path.exists(SPOKEN_FILE):
            os.remove(SPOKEN_FILE)
        driver.quit()
        