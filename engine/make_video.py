#!/usr/bin/env python3
"""Baut ein fertiges TikTok-Video aus einem Skript (JSON) im Stil der Referenzvideos.

Ablauf: Stimme (ElevenLabs oder kostenloser Fallback) -> Stock-Clips (Mixkit)
-> abgedunkelter Hintergrund mit langsamem Zoom -> Wort-für-Wort-Untertitel
-> Hintergrundmusik -> MP4.

Aufruf:
    python3 engine/make_video.py scripts/001_beruehrung.json
    python3 engine/make_video.py scripts/001_beruehrung.json --vertical
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
FONTS = ENGINE / "fonts"
CACHE = ROOT / ".cache"
MUSIC = ROOT / "Ohsxnta - changes (intro only) slowed.mp3"
CA = "/root/.ccr/ca-bundle.crt"

# --- Stil (aus den Referenzvideos abgeleitet) ---------------------------------
WHITE = (240, 240, 236)
YELLOW = (236, 196, 28)          # Gelb der Highlight-Wörter
SR = 44100
FPS = 30
PAUSE = 0.38                     # Pause zwischen zwei Sätzen
DARKEN = 0.50                    # wie stark der Clip abgedunkelt wird

# Stimme: immer dieselbe.
ELEVEN_VOICE = "nPczCjzI2devNBz1zQrb"   # "Brian" – tief, ruhig, Erzähler (Standardstimme, im Free-Plan nutzbar)
ELEVEN_MODEL = "eleven_multilingual_v2"
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


# --- Skript-Markup ------------------------------------------------------------
# *wort*   -> gelb hervorgehoben
# [WORT]   -> riesig, eigene Einblendung (wie "ALIVE", "YOU")
# "say"    -> optional abweichender Sprechtext (z. B. Zahlen ausgeschrieben)

def parse_tokens(text):
    """Zerlegt den Untertiteltext in Wörter mit Stil-Info (*...* = gelb, [...] = riesig)."""
    tokens, mode = [], "normal"
    for raw in text.split():
        style = mode
        if raw.startswith("*"):
            style = "hl"
            mode = "hl"
        if raw.startswith("["):
            style = "big"
            mode = "big"
        core = raw.rstrip(".,!?:;…\"'")
        if (mode == "hl" and core.endswith("*")) or (mode == "big" and "]" in raw):
            mode = "normal"
        tokens.append({"word": raw.replace("*", "").replace("[", "").replace("]", ""), "style": style})
    return tokens


def plain(text):
    return text.replace("*", "").replace("[", "").replace("]", "")


def norm(s):
    return re.sub(r"[^0-9a-zäöüß]", "", s.lower())


# --- Stimme -------------------------------------------------------------------

def tts_eleven(text, prev_text, next_text, key, out_mp3):
    body = json.dumps({
        "text": text,
        "model_id": ELEVEN_MODEL,
        "language_code": "de",
        "previous_text": prev_text or None,
        "next_text": next_text or None,
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.15,
                           "use_speaker_boost": True, "speed": 0.95},
    }).encode()
    res = json.loads(http(f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE}/with-timestamps",
                          data=body, headers={"xi-api-key": key, "Content-Type": "application/json"}, timeout=120))
    Path(out_mp3).write_bytes(base64.b64decode(res["audio_base64"]))
    al = res.get("normalized_alignment") or res["alignment"]
    words, cur, start, end = [], "", None, 0
    for ch, s, e in zip(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]):
        if ch.isspace():
            if cur:
                words.append((cur, start, end))
            cur, start = "", None
        else:
            if start is None:
                start = s
            cur += ch
            end = e
    if cur:
        words.append((cur, start, end))
    return words


def tts_edge(text, out_mp3, rate="-6%", pitch="-2Hz"):
    import edge_tts
    import edge_tts.communicate as c
    c._SSL_CTX = ssl_ctx()

    async def go():
        words, audio = [], bytearray()
        com = edge_tts.Communicate(text, EDGE_VOICE, rate=rate, pitch=pitch, boundary="WordBoundary")
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


# --- Untertitel-Grafiken -------------------------------------------------------

def font(name, size):
    return ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)


def chunk_tokens(tokens, max_words=3, max_chars=20):
    """Gruppiert Wörter in kurze Einblendungen (2–4 Wörter wie im Original)."""
    chunks, cur = [], []
    for i, t in enumerate(tokens):
        if t["style"] == "big":
            if cur:
                chunks.append(cur)
                cur = []
            # zusammenhängende große Wörter gemeinsam
            if chunks and chunks[-1] and chunks[-1][-1]["style"] == "big" and chunks[-1][-1].get("_idx") == i - 1:
                chunks[-1].append({**t, "_idx": i})
            else:
                chunks.append([{**t, "_idx": i}])
            continue
        cur.append({**t, "_idx": i})
        text_len = sum(len(x["word"]) + 1 for x in cur)
        ends = t["word"][-1] in ".,!?:;…"
        if ends or len(cur) >= max_words or text_len >= max_chars:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    # einzelne kurze Wörter am Ende an den vorigen Chunk hängen
    merged = []
    for c in chunks:
        if (merged and len(c) == 1 and c[0]["style"] == "normal" and len(c[0]["word"]) <= 3
                and merged[-1][-1]["style"] != "big" and merged[-1][-1]["word"][-1] not in ".!?"):
            merged[-1].extend(c)
        else:
            merged.append(c)
    return merged


def draw_line(draw, img, x_center, y, words, fnt, colors, shadow=True):
    space = fnt.getlength(" ")
    widths = [fnt.getlength(w) for w in words]
    total = sum(widths) + space * (len(words) - 1)
    x = x_center - total / 2
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        xx = x
        for w, wd in zip(words, widths):
            sd.text((xx + 3, y + 5), w, font=fnt, fill=(0, 0, 0, 170), anchor="ls")
            xx += wd + space
        sh = sh.filter(ImageFilter.GaussianBlur(9))
        img.alpha_composite(sh)
    for w, wd, col in zip(words, widths, colors):
        draw.text((x, y), w, font=fnt, fill=col + (255,), anchor="ls")
        x += wd + space


def render_chunk(chunk, prev_chunk, W, H, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = W / 1920 if W > H else W / 1080
    cy = H * 0.5
    is_big = chunk[0]["style"] == "big"
    is_num = all(re.fullmatch(r"[\d.,^%:/+\-x×]+|zu|in|von|1", t["word"]) for t in chunk) and any(
        re.search(r"\d", t["word"]) for t in chunk)
    if is_big:
        words = [t["word"].upper().rstrip(".,!?:;") for t in chunk]
        f = font("jakarta-extrabold", int(230 * s))
        while sum(f.getlength(w) for w in words) > W * 0.88 and f.size > 60:
            f = font("jakarta-extrabold", f.size - 8)
        if prev_chunk and prev_chunk[0]["style"] != "big":
            small = font("jakarta-extrabold", int(84 * s))
            draw_line(d, img, W / 2, cy - f.size * 0.62, [t["word"] for t in prev_chunk], small,
                      [YELLOW if t["style"] == "hl" else WHITE for t in prev_chunk])
        draw_line(d, img, W / 2, cy + f.size * 0.42, words, f, [YELLOW] * len(words))
    elif is_num:
        f = font("oswald-medium", int(150 * s))
        draw_line(d, img, W / 2, cy + f.size * 0.35, [t["word"] for t in chunk], f, [YELLOW] * len(chunk))
    else:
        f = font("jakarta-extrabold", int(90 * s))
        words = [t["word"] for t in chunk]
        cols = [YELLOW if t["style"] == "hl" else WHITE for t in chunk]
        if sum(f.getlength(w) for w in words) > W * 0.85:   # Hochformat: in zwei Zeilen
            half = (len(words) + 1) // 2
            draw_line(d, img, W / 2, cy - f.size * 0.25, words[:half], f, cols[:half])
            draw_line(d, img, W / 2, cy + f.size * 0.95, words[half:], f, cols[half:])
        else:
            draw_line(d, img, W / 2, cy + f.size * 0.35, words, f, cols)
    img.save(path)


def render_bigword(word, W, H, path):
    """Riesiges, halbtransparentes Wort hinter dem Untertitel (wie "BIG BANG", "WAR")."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font("oswald-medium", int(min(W, H * 1.6) * 0.30))
    while f.getlength(word) > W * 0.92:
        f = font("oswald-medium", f.size - 10)
    d.text((W / 2, H * 0.5), word, font=f, fill=(225, 225, 230, 70), anchor="mm")
    img.save(path)


def render_handle(handle, W, H, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font("jakarta-bold", int(28 * (W / 1920 if W > H else W / 1080)))
    d.text((W * 0.5 + W * 0.17, H * 0.5 + H * 0.06), handle, font=f, fill=(255, 255, 255, 45), anchor="mm")
    img.save(path)


# --- Stock-Clips (Mixkit, kostenlos & ohne Key) ---------------------------------

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
        if re.search(r"green screen|chroma|brainstorm|logo|mockup|template|text space", it["text"]):
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


def pick_clip(queries, used, rng, vertical=False):
    for q in queries:
        ids = [v for v in mixkit_search(q, vertical) if v not in used][:6]
        rng.shuffle(ids)
        for vid in ids[:4]:
            p = mixkit_download(vid)
            if p:
                used.add(vid)
                return p
    return None


def render_bg(clip, dur, W, H, path, rng, fade_in=False):
    """Clip zuschneiden, abdunkeln, langsamer Zoom – wie im Original."""
    if clip is None:
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={dur:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(path)])
        return
    cd = probe_duration(clip)
    start = rng.uniform(0, max(0, cd - dur - 0.2)) if cd > dur + 0.5 else 0
    zoom_in = rng.random() < 0.6
    z0, z1 = (1.0, 1.08) if zoom_in else (1.08, 1.0)
    z = f"({z0}+({z1 - z0})*t/{dur:.3f})"
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame,crop={W}:{H},"
          f"setsar=1,fps={FPS},eq=saturation=0.85:contrast=1.05,"
          f"colorchannelmixer=rr={1 - DARKEN}:gg={1 - DARKEN}:bb={1 - DARKEN},vignette=PI/4.5")
    if fade_in:
        vf += ",fade=t=in:st=0:d=0.35"
    loop = ["-stream_loop", "-1"] if cd < dur + start else []
    run(["ffmpeg", "-y", "-v", "error", *loop, "-ss", f"{start:.2f}", "-i", str(clip), "-t", f"{dur:.3f}",
         "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(path)])


# --- Hauptprogramm --------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--vertical", action="store_true", help="9:16 statt 16:9")
    ap.add_argument("--out", help="Ausgabedatei")
    ap.add_argument("--voice", choices=["auto", "eleven", "edge"], default="auto")
    args = ap.parse_args()

    spec = json.loads(Path(args.script).read_text())
    name = Path(args.script).stem
    W, H = (1080, 1920) if args.vertical or spec.get("vertical") else (1920, 1080)
    out = Path(args.out or ROOT / "videos" / f"{name}{'_9x16' if H > W else ''}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = CACHE / "work" / name
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    rng = random.Random(spec.get("seed", name))

    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key and (ROOT / ".elevenlabs_key").exists():
        key = (ROOT / ".elevenlabs_key").read_text().strip()
    use_eleven = args.voice == "eleven" or (args.voice == "auto" and key)
    print(f"Stimme: {'ElevenLabs Brian' if use_eleven else 'Fallback ' + EDGE_VOICE}")

    beats = spec["beats"]
    voice = []
    captions = []      # (start, end, chunk, prev_chunk)
    # Beats zu ganzen Sätzen zusammenfassen: endet ein Beat nicht mit . ? ! …,
    # wird er zusammen mit dem nächsten gesprochen (natürliche Betonung).
    groups_u, cur = [], []
    for i, b in enumerate(beats):
        cur.append(i)
        txt = b["text"].rstrip()
        if (plain(txt).endswith((".", "?", "!", "…")) or txt.endswith("]") or "pause" in b
                or i == len(beats) - 1):
            groups_u.append(cur)
            cur = []
    timeline = [None] * len(beats)
    t = 0.0
    for gi, idxs in enumerate(groups_u):
        say = " ".join(beats[i].get("say") or plain(beats[i]["text"]) for i in idxs)
        mp3 = work / f"v{gi:02d}.mp3"
        if use_eleven:
            prev_t = " ".join(beats[i].get("say") or plain(beats[i]["text"]) for i in range(max(0, idxs[0] - 2), idxs[0]))
            next_t = " ".join(beats[i].get("say") or plain(beats[i]["text"]) for i in range(idxs[-1] + 1, min(len(beats), idxs[-1] + 3)))
            words = tts_eleven(say, prev_t, next_t, key, mp3)
        else:
            words = tts_edge(say, mp3, rate=spec.get("rate", "-6%"), pitch=spec.get("pitch", "-2Hz"))
        a, words = trim_silence(decode(mp3), words)
        if any(beats[i].get("say") for i in idxs):
            words = []     # abweichender Sprechtext: Zeiten nach Länge verteilen
        dur_voice = len(a) / SR
        pause = beats[idxs[-1]].get("pause", PAUSE)
        seg = dur_voice + pause
        tokens, owner = [], []
        for i in idxs:
            tk = parse_tokens(beats[i]["text"])
            tokens += tk
            owner += [i] * len(tk)
        wt = map_word_times(tokens, words, dur_voice)
        # Schnittpunkte zwischen den Beats dieses Satzes: kurz vor dem ersten Wort des nächsten Beats
        bstart = {}
        for k, i in enumerate(owner):
            bstart.setdefault(i, wt[k])
        for n, i in enumerate(idxs):
            s0 = 0.0 if n == 0 else max(0.0, bstart[i] - 0.08)
            s1 = seg if n == len(idxs) - 1 else max(0.0, bstart[idxs[n + 1]] - 0.08)
            timeline[i] = (t + s0, s1 - s0)
        for i in idxs:
            sub = [tokens[k] for k in range(len(tokens)) if owner[k] == i]
            subt = [wt[k] for k in range(len(tokens)) if owner[k] == i]
            for k, tok in enumerate(sub):
                tok["_t"] = subt[k]
            chunks = chunk_tokens(sub)
            for j, c in enumerate(chunks):
                cs = t + sub[c[0]["_idx"]]["_t"]
                if j + 1 < len(chunks):
                    ce = t + sub[chunks[j + 1][0]["_idx"]]["_t"]
                else:
                    ce = timeline[i][0] + timeline[i][1]
                captions.append([cs, ce, c, chunks[j - 1] if j > 0 else None])
        voice.append(a)
        voice.append(np.zeros(int(pause * SR), np.float32))
        t += seg
        print(f"  Satz {gi + 1}/{len(groups_u)}: {dur_voice:.1f}s  {say[:60]}")
    total = t + 0.6
    voice.append(np.zeros(int(0.6 * SR), np.float32))
    vwav = work / "voice.f32"
    np.concatenate(voice).astype(np.float32).tofile(vwav)

    # Hintergrund pro Satz (aufeinanderfolgende Sätze mit gleichem Clip-Begriff teilen sich den Clip)
    used = set()
    groups = []
    for i, b in enumerate(beats):
        q = b.get("clip", "dark sky")
        st, du = timeline[i]
        if groups and b.get("same_clip"):
            groups[-1]["end"] = st + du
        else:
            groups.append({"q": q, "start": st, "end": st + du, "fade": b.get("fade", False)})
    groups[-1]["end"] = total
    for g in groups:   # auf ganze Frames runden, damit Bild und Ton synchron bleiben
        g["dur"] = (round(g["end"] * FPS) - round(g["start"] * FPS)) / FPS
    seg_files = []
    for gi, g in enumerate(groups):
        queries = [g["q"]] if isinstance(g["q"], str) else g["q"]
        queries = queries + ["night sky stars", "dark clouds"]
        clip = pick_clip(queries, used, rng, vertical=H > W)
        f = work / f"bg{gi:02d}.mp4"
        render_bg(clip, g["dur"], W, H, f, rng, fade_in=g["fade"] or gi == 0)
        seg_files.append(f)
        print(f"  Clip {gi + 1}/{len(groups)}: {g['q']} -> {clip.name if clip else 'schwarz'}")
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{f}'\n" for f in seg_files))
    bg = work / "bg.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(bg)])

    # Overlays: Riesenwörter (pro Satz) + Untertitel (pro Chunk)
    overlays = []
    for i, b in enumerate(beats):
        if b.get("bg"):
            p = work / f"big{i:02d}.png"
            render_bigword(b["bg"], W, H, p)
            overlays.append((timeline[i][0], timeline[i][0] + timeline[i][1], p, 0.25))
    for k, (s, e, c, prev) in enumerate(captions):
        p = work / f"cap{k:03d}.png"
        render_chunk(c, prev, W, H, p)
        overlays.append((s, e, p, 0.12))
    if spec.get("handle"):
        p = work / "handle.png"
        render_handle(spec["handle"], W, H, p)
        overlays.append((0, total, p, 0))

    inputs = ["-i", str(bg), "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", str(vwav),
              "-stream_loop", "-1", "-i", str(spec.get("music", MUSIC))]
    fc, last = [], "[0:v]"
    for n, (s, e, p, fd) in enumerate(overlays):
        inputs += ["-loop", "1", "-t", f"{e - s:.3f}", "-i", str(p)]
        idx = n + 3
        fade = f",fade=t=in:st=0:d={fd}:alpha=1" if fd else ""
        fc.append(f"[{idx}:v]format=rgba{fade},setpts=PTS+{s:.3f}/TB[o{n}]")
        fc.append(f"{last}[o{n}]overlay=0:0:eof_action=pass[v{n}]")
        last = f"[v{n}]"
    fc.append(f"{last}format=yuv420p[vout]")
    mvol = spec.get("music_volume", 0.22)
    fc.append(f"[1:a]loudnorm=I=-15:TP=-1.5:LRA=11,aresample={SR}[vo]")
    fc.append(f"[2:a]atrim=0:{total:.3f},volume={mvol},afade=t=out:st={total - 1.8:.3f}:d=1.8[mu]")
    fc.append("[vo][mu]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]")
    script = work / "filter.txt"
    script.write_text(";\n".join(fc))
    print("Rendere Endvideo ...")
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex_script", str(script),
         "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    print(f"Fertig: {out}  ({total:.1f}s)")


if __name__ == "__main__":
    main()
