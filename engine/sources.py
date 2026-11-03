"""Stimme (ElevenLabs / Fallback) und Stock-Clips (Pexels / Mixkit)."""
import asyncio
import base64
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
CA = "/root/.ccr/ca-bundle.crt"
SR = 44100

# Stimme: immer dieselbe.
# Bevorzugt: "Simon – Deep and Strong" (deutscher Muttersprachler, ruhiger Erzähler; braucht Bezahl-Plan).
# Free-Plan: automatisch "Brian" (tief, ruhig).
ELEVEN_VOICES = [v for v in [os.environ.get("ELEVENLABS_VOICE_ID"),
                             "FTNCalFNG5bRnkkaP5Ug",   # "Otto" – Wunschstimme, deutsch, ruhig (Bezahl-Plan)
                             "tZPwJezAhAZUGGBZDmR0",   # "Simon" – deutsch (Bezahl-Plan)
                             "nPczCjzI2devNBz1zQrb"] if v]   # "Brian" – Free-Plan
ELEVEN_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")
EDGE_VOICE = "de-DE-FlorianMultilingualNeural"


def ssl_ctx():
    return ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()


def http(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
        return r.read()


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(p.stderr[-3000:])
        raise SystemExit(f"Befehl fehlgeschlagen: {' '.join(map(str, cmd[:6]))} ...")
    return p.stdout


def probe_duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).strip())


def norm(s):
    return re.sub(r"[^0-9a-zäöüß]", "", s.lower())


# --- Stimme -------------------------------------------------------------------

def tts_eleven(text, prev_text, next_text, key, out_mp3, model=None):
    """ElevenLabs mit Wort-Zeitstempeln. Ergebnisse werden gecacht (spart Credits).
    model="eleven_v3": menschlicher/emotionaler, versteht Anweisungen wie [whispers] im Text."""
    import shutil as _sh
    model = model or ELEVEN_MODEL
    if model == "eleven_v3":
        settings = {"stability": 0.5, "similarity_boost": 0.8, "use_speaker_boost": True}
        prev_text = next_text = None      # v3 unterstützt kein Request-Stitching
    else:
        settings = {"stability": 0.4, "similarity_boost": 0.8, "style": 0.2, "use_speaker_boost": True, "speed": 0.94}
    bad = CACHE / "tts" / "unavailable_voices.txt"
    skip = set(bad.read_text().split()) if bad.exists() else set()
    last_err = None
    for voice in [v for v in ELEVEN_VOICES if v not in skip]:
        h = hashlib.md5(json.dumps([voice, model, text, prev_text, next_text, settings]).encode()).hexdigest()
        cm, cj = CACHE / "tts" / f"{h}.mp3", CACHE / "tts" / f"{h}.json"
        if cm.exists() and cj.exists():
            _sh.copy(cm, out_mp3)
            return [tuple(w) for w in json.loads(cj.read_text())]
        body = json.dumps({"text": text, "model_id": model, "language_code": "de",
                           "previous_text": prev_text or None, "next_text": next_text or None,
                           "voice_settings": settings}).encode()
        try:
            raw = http(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps", data=body,
                       headers={"xi-api-key": key, "Content-Type": "application/json"}, timeout=120)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")
            last_err = f"{e.code} {msg[:200]}"
            if e.code in (402, 403, 404) and "quota" not in msg:
                print(f"    Stimme {voice} nicht verfügbar ({e.code}) – nehme nächste")
                bad.parent.mkdir(parents=True, exist_ok=True)
                with open(bad, "a") as f:
                    f.write(voice + "\n")
                continue
            raise SystemExit(f"ElevenLabs-Fehler: {last_err}")
        res = json.loads(raw)
        Path(out_mp3).write_bytes(base64.b64decode(res["audio_base64"]))
        al = res.get("normalized_alignment") or res["alignment"]
        words, cur, start, end = [], "", None, 0
        for ch, s0, e0 in zip(al["characters"], al["character_start_times_seconds"],
                              al["character_end_times_seconds"]):
            if ch.isspace():
                if cur:
                    words.append((cur, start, end))
                cur, start = "", None
            else:
                if start is None:
                    start = s0
                cur += ch
                end = e0
        if cur:
            words.append((cur, start, end))
        words = [w for w in words if not re.fullmatch(r"\[[^\]]*\]", w[0])]   # [whispers] usw. entfernen
        cm.parent.mkdir(parents=True, exist_ok=True)
        _sh.copy(out_mp3, cm)
        cj.write_text(json.dumps(words))
        return words
    raise SystemExit(f"Keine ElevenLabs-Stimme nutzbar: {last_err}")


def sts_eleven(src_mp3, key, out_mp3):
    """Voice Changer: deutsche Quell-Aufnahme -> ElevenLabs-Stimme. Aussprache/Timing bleiben vom Original
    (also akzentfrei), nur die Klangfarbe wird ersetzt. Gecacht."""
    import shutil as _sh
    settings = {"stability": 0.55, "similarity_boost": 0.85, "style": 0, "use_speaker_boost": True}
    src = Path(src_mp3).read_bytes()
    bad = CACHE / "tts" / "unavailable_voices.txt"
    skip = set(bad.read_text().split()) if bad.exists() else set()
    last_err = None
    for voice in [v for v in ELEVEN_VOICES if v not in skip]:
        h = hashlib.md5(src + json.dumps([voice, "sts", settings]).encode()).hexdigest()
        cm = CACHE / "tts" / f"sts_{h}.mp3"
        if cm.exists():
            _sh.copy(cm, out_mp3)
            return voice
        bnd = "----b" + h[:16]
        body = b""
        for name, val in (("model_id", "eleven_multilingual_sts_v2"), ("voice_settings", json.dumps(settings))):
            body += f"--{bnd}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode()
        body += (f"--{bnd}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"a.mp3\"\r\n"
                 f"Content-Type: audio/mpeg\r\n\r\n").encode() + src + f"\r\n--{bnd}--\r\n".encode()
        try:
            raw = http(f"https://api.elevenlabs.io/v1/speech-to-speech/{voice}?output_format=mp3_44100_128",
                       data=body, headers={"xi-api-key": key, "Content-Type": f"multipart/form-data; boundary={bnd}"},
                       timeout=180)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")
            last_err = f"{e.code} {msg[:200]}"
            if e.code in (402, 403, 404) and "quota" not in msg:
                print(f"    Stimme {voice} nicht verfügbar ({e.code}) – nehme nächste")
                bad.parent.mkdir(parents=True, exist_ok=True)
                with open(bad, "a") as f:
                    f.write(voice + "\n")
                continue
            raise SystemExit(f"ElevenLabs-Fehler: {last_err}")
        Path(out_mp3).write_bytes(raw)
        cm.parent.mkdir(parents=True, exist_ok=True)
        _sh.copy(out_mp3, cm)
        return voice
    raise SystemExit(f"Keine ElevenLabs-Stimme nutzbar: {last_err}")


def tts_edge(text, out_mp3, rate="-6%", pitch="-2Hz", voice=None):
    import edge_tts
    import edge_tts.communicate as c
    c._SSL_CTX = ssl_ctx()

    async def go():
        words, audio = [], bytearray()
        com = edge_tts.Communicate(text, voice or EDGE_VOICE, rate=rate, pitch=pitch, boundary="WordBoundary")
        async for ch in com.stream():
            if ch["type"] == "audio":
                audio.extend(ch["data"])
            elif ch["type"] == "WordBoundary":
                s = ch["offset"] / 1e7
                words.append((ch["text"], s, s + ch["duration"] / 1e7))
        Path(out_mp3).write_bytes(bytes(audio))
        return words
    for attempt in range(4):
        try:
            return asyncio.run(go())
        except Exception as e:
            if attempt == 3:
                raise
            print(f"    Stimme: Netzwerkfehler ({type(e).__name__}), neuer Versuch ...")
            import time
            time.sleep(2 ** attempt)


def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def trim_silence(a, words, thresh=0.01):
    """Schneidet Stille am Anfang/Ende weg und verschiebt die Wortzeiten."""
    idx = np.where(np.abs(a) > thresh)[0]
    if len(idx) == 0:
        return a, words
    s = max(0, idx[0] - int(0.03 * SR))
    e = min(len(a), idx[-1] + int(0.08 * SR))
    off = s / SR
    return a[s:e], [(w, max(0, ws - off), max(0, we - off)) for w, ws, we in words]


def map_word_times(tokens, tts_words, dur):
    """Ordnet jedem Untertitel-Wort eine Startzeit zu (über Zeichenpositionen)."""
    tts_chars, tts_pos = [], 0
    for w, s, e in tts_words:
        n = len(norm(w))
        if n:
            tts_chars.append((tts_pos, tts_pos + n, s, e))
            tts_pos += n
    tok_lens = [max(1, len(norm(t["word"]))) for t in tokens]
    total_tok = sum(tok_lens)
    if not tts_chars:
        # gleichmäßig nach Zeichenlänge verteilen
        acc, times = 0, []
        for n in tok_lens:
            times.append(dur * acc / total_tok)
            acc += n
        return times
    scale = tts_pos / total_tok
    times, acc = [], 0
    for n in tok_lens:
        p = acc * scale
        t = tts_chars[-1][2]
        for a, b, s, e in tts_chars:
            if a <= p < b:
                t = s + (e - s) * (p - a) / max(1, b - a)
                break
        times.append(t)
        acc += n
    # monoton halten
    for i in range(1, len(times)):
        times[i] = max(times[i], times[i - 1] + 0.05)
    return times


def mixkit_items(url):
    """Liest alle Videos (ID, Titel, Beschreibung, Lizenz) aus einer Mixkit-Seite."""
    key = hashlib.md5(url.encode()).hexdigest()[:12]
    cf = CACHE / "search" / f"{key}.json"
    if cf.exists():
        return json.loads(cf.read_text())
    try:
        html = http(url).decode("utf-8", "ignore")
    except Exception:
        return []
    free = {}
    for block in html.split('{"@type":"VideoObject"')[1:]:
        vid = re.search(r"videos/(\d+)/\1-\d+\.mp4", block)
        lic = re.search(r'"copyrightNotice":"([^"]*)"', block)
        if vid and lic:
            free[vid.group(1)] = lic.group(1) == "Free"
    items, seen = [], set()
    for card in re.split(r'<div class="item-grid-video-player(?=[ "])', html)[1:]:
        vid = re.search(r'item-id-value="(\d+)"', card)
        if not vid or vid.group(1) in seen:
            continue
        seen.add(vid.group(1))
        head = card[:200]
        title = re.search(r'overlay-video-title">\s*(.*?)\s*</span>', card, re.S)
        desc = re.search(r'overlay-video-description">\s*(.*?)\s*</span>', card, re.S)
        items.append({"id": vid.group(1),
                      "text": ((title.group(1) if title else "") + " " + (desc.group(1) if desc else "")).lower(),
                      "vertical": "--vertical" in head,
                      "free": free.get(vid.group(1), True)})
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps(items))
    return items


BAD_CLIPS = {"32645", "47325"}   # unpassend/albern (z. B. Hypnose-Gesicht bei "brain")


def mixkit_search(query, vertical=False):
    """Sucht passende Clips. Liefert IDs, die besten zuerst (Titel muss zum Suchbegriff passen)."""
    q = query.strip().lower()
    words = q.split()
    items = []
    for w in words[:2]:
        items += mixkit_items(f"https://mixkit.co/free-stock-video/{w}/")
        items += mixkit_items(f"https://mixkit.co/free-stock-video/{w}/?page=2")
    items += mixkit_items(f"https://mixkit.co/free-stock-video/discover/?q={urllib.parse.quote_plus(q)}")
    best = {}
    for it in items:
        if re.search(r"green screen|chroma|brainstorm|logo|mockup|template|text space|hypno|buddha|cartoon|funny|comic|crazy|kitten|cat |puppy|dog|pasta|food|dough|knead|cooking", it["text"]) or it["id"] in BAD_CLIPS:
            continue
        hits = sum(1 for w in words if re.search(r"\b" + re.escape(w), it["text"]))
        if hits == 0:
            continue
        score = hits / len(words) + (0.2 if it["free"] else 0) + (0.3 if it.get("vertical", False) == vertical else 0)
        best[it["id"]] = max(best.get(it["id"], 0), score)
    ranked = sorted(best, key=lambda k: -best[k])
    top = best[ranked[0]] if ranked else 0
    return [k for k in ranked if best[k] >= top - 0.25]


def mixkit_download(vid):
    path = CACHE / "clips" / f"{vid}.mp4"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        for res in ("720", "360"):
            try:
                path.write_bytes(http(f"https://assets.mixkit.co/videos/{vid}/{vid}-{res}.mp4", timeout=180))
                break
            except Exception:
                continue
    return path if path.exists() and path.stat().st_size > 10000 else None


def pexels_search(query, vertical, key):
    """Pexels-API (kostenloser Key): deutlich bessere, kinoartige Clips."""
    q = query.strip().lower()
    cf = CACHE / "search" / ("px_" + hashlib.md5(f"{q}|{vertical}".encode()).hexdigest()[:12] + ".json")
    if cf.exists():
        return json.loads(cf.read_text())
    url = ("https://api.pexels.com/videos/search?" + urllib.parse.urlencode(
        {"query": q, "per_page": 15, "orientation": "portrait" if vertical else "landscape", "size": "medium"}))
    try:
        data = json.loads(http(url, headers={"Authorization": key}))
    except Exception as e:
        print(f"    Pexels-Fehler: {e}")
        return []
    out = []
    for v in data.get("videos", []):
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
        if not files or v.get("duration", 0) < 4:
            continue
        target = 1080 if vertical else 1920
        f = min(files, key=lambda f: abs((f["width"] if not vertical else f["height"] or 0) - target) + (5000 if (f["width"] or 0) < 1200 and not vertical else 0))
        out.append({"id": f"px{v['id']}", "url": f["link"]})
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps(out))
    return out


def pexels_download(item):
    path = CACHE / "clips" / f"{item['id']}.mp4"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_bytes(http(item["url"], timeout=300))
        except Exception:
            return None
    return path if path.stat().st_size > 10000 else None


def pick_clip(queries, used, rng, vertical=False):
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if key:
        for q in queries:
            items = [it for it in pexels_search(q, vertical, key) if it["id"] not in used][:5]
            rng.shuffle(items)
            for it in items:
                p = pexels_download(it)
                if p:
                    used.add(it["id"])
                    return p
    return pick_clip_mixkit(queries, used, rng, vertical)


def pick_clip_mixkit(queries, used, rng, vertical=False):
    for q in queries:
        ids = [v for v in mixkit_search(q, vertical) if v not in used][:6]
        rng.shuffle(ids)
        for vid in ids[:4]:
            p = mixkit_download(vid)
            if p:
                used.add(vid)
                return p
    return None


