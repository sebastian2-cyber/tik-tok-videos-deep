#!/usr/bin/env python3
"""Baut ein fertiges TikTok-Video aus einem Skript (JSON) im Stil der Referenzvideos.

Zwei Stile (Feld "style" im Skript):
  "type"   – wie die Weltall-Videos: Text wird Buchstabe für Buchstabe zur Stimme getippt,
             baut sich über mehrere Zeilen auf, Schlüsselwort riesig in Gelb, danach Schnitt auf
             Schwarz und nur das Schlüsselwort bleibt stehen.
  "glitch" – wie das 1729-Video: Film-Look, Korn, Flackern, RGB-Glitches, kleine gelbe Untertitel
             unten, riesige Wörter/Zahlen in der Mitte, die Zeichen für Zeichen erscheinen.

Aufruf:
    python3 engine/make_video.py scripts/002_xyz.json [--vertical] [--preview]
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources as S  # noqa: E402

ROOT = S.ROOT
FONTS = ROOT / "engine" / "fonts"
CACHE = S.CACHE
MUSIC = ROOT / "Ohsxnta - changes (intro only) slowed.mp3"
SR = S.SR
FPS = 30

WHITE = (238, 238, 234)
YELLOW = (238, 196, 26)
PAUSE = 0.35          # Pause nach einem Satz
BLACK_HOLD = 0.85     # Schwarz-Pause nach einem großen Schlusswort

# Echte Sound-Effekte in assets/sfx: Name -> Sekunde des Einschlags in der Datei
SFX_LIB = {"impact_big": 2.18, "impact_soft": 1.76, "impact_trailer": 1.00, "heartbeat_impact": 0.09,
           "whoosh_fast": 1.08, "whoosh_tunnel": 0.62, "whoosh_impact": 0.56, "riser_trailer": 2.50}

# Voice-Mastering (Preset v2): Rumpeln weg, Zischlaute weicher, Wärme + Präsenz, sanfte Kompression
VOICE_MASTER = ("highpass=f=70,deesser=i=0.35,equalizer=f=160:t=q:w=1:g=2,"
                "equalizer=f=3200:t=q:w=1.2:g=2,equalizer=f=9000:t=q:w=1:g=-1.5,"
                "acompressor=threshold=-20dB:ratio=3:attack=8:release=140:makeup=2,")


# ---------------------------------------------------------------------------
# Skript-Markup
#   *wort*     gelb
#   [WORT]     riesig + gelb, danach Schnitt auf Schwarz (nur das Wort bleibt)
#   {WORT}     riesiges Hintergrundwort, erscheint genau wenn es gesprochen wird
#   " / "      neuer Textblock
# ---------------------------------------------------------------------------

def parse_tokens(text):
    tokens, mode = [], None
    for raw in text.split():
        if raw == "/":
            if tokens:
                tokens[-1]["brk"] = True
            continue
        style = mode or "normal"
        if raw.startswith("*"):
            style = mode = "hl"
        elif raw.startswith("["):
            style = mode = "big"
        elif raw.startswith("{"):
            style = mode = "bgw"
        core = raw.rstrip(".,!?:;…\"'")
        if (mode == "hl" and core.endswith("*")) or (mode == "big" and "]" in raw) or (mode == "bgw" and "}" in raw):
            mode = None
        word = re.sub(r"[*\[\]{}]", "", raw)
        tokens.append({"word": word, "style": style})
    return tokens


def plain(text):
    return re.sub(r"[*\[\]{}]", "", text).replace(" / ", " ")


def font(name, size):
    return ImageFont.truetype(str(FONTS / f"{name}.ttf"), max(8, int(size)))


# ---------------------------------------------------------------------------
# 1) Stimme + Wortzeiten
# ---------------------------------------------------------------------------

def build_voice(spec, beats, work, use_eleven, key):
    groups, cur = [], []
    for i, b in enumerate(beats):
        cur.append(i)
        txt = b["text"].rstrip()
        if (plain(txt).rstrip().endswith((".", "?", "!", "…")) or txt.endswith("]") or "pause" in b
                or i == len(beats) - 1):
            groups.append(cur)
            cur = []
    say_of = lambda i: beats[i].get("say") or plain(beats[i]["text"])  # noqa: E731
    audio, t = [], 0.0
    for gi, idxs in enumerate(groups):
        say = " ".join(say_of(i) for i in idxs)
        mp3 = work / f"v{gi:02d}.mp3"
        if use_eleven:
            prev_t = " ".join(say_of(i) for i in range(max(0, idxs[0] - 2), idxs[0]))
            next_t = " ".join(say_of(i) for i in range(idxs[-1] + 1, min(len(beats), idxs[-1] + 3)))
            model = spec.get("voice_model")
            tone = beats[idxs[0]].get("tone")
            if spec.get("voice_pipeline") == "sts":
                # Preset v3: deutsche Muttersprachler-Stimme spricht -> Voice Changer macht daraus die Zielstimme
                src = work / f"src{gi:02d}.mp3"
                words = S.tts_edge(say, src, rate=spec.get("rate", "-8%"), pitch=spec.get("pitch", "-4Hz"),
                                   voice=spec.get("sts_source", "de-DE-ConradNeural"))
                used = S.sts_eleven(src, key, mp3)
                if gi == 0:
                    print(f"    Stimme: {spec.get('sts_source', 'de-DE-ConradNeural')} -> ElevenLabs {used}")
            else:
                if tone and model == "eleven_v3":
                    say = f"[{tone}] {say}"
                words = S.tts_eleven(say, prev_t, next_t, key, mp3, model=model)
        else:
            words = S.tts_edge(say, mp3, rate=spec.get("rate", "-6%"), pitch=spec.get("pitch", "-2Hz"))
        a, words = S.trim_silence(S.decode(mp3), words)
        if any(beats[i].get("say") for i in idxs):
            words = []
        dv = len(a) / SR
        last = beats[idxs[-1]]
        ends_big = last["text"].rstrip().endswith("]")
        pause = last.get("pause", BLACK_HOLD if ends_big else PAUSE)
        tokens, owner = [], []
        for i in idxs:
            tk = parse_tokens(beats[i]["text"])
            tokens += tk
            owner += [i] * len(tk)
        wt = S.map_word_times(tokens, words, dv)
        for k, tok in enumerate(tokens):
            tok["t"] = t + wt[k]
            nxt = t + (wt[k + 1] if k + 1 < len(tokens) else dv)
            tok["te"] = max(tok["t"] + 0.08, nxt)
        first = {}
        for k, i in enumerate(owner):
            first.setdefault(i, tokens[k]["t"])
        for n, i in enumerate(idxs):
            b = beats[i]
            b["_tokens"] = [tokens[k] for k in range(len(tokens)) if owner[k] == i]
            b["_start"] = t if n == 0 else first[i] - 0.06
            b["_end"] = (t + dv + pause) if n == len(idxs) - 1 else first[idxs[n + 1]] - 0.06
            b["_black"] = (t + dv + 0.05) if (n == len(idxs) - 1 and ends_big and b.get("black", True)) else None
        audio += [a, np.zeros(int(pause * SR), np.float32)]
        t += dv + pause
        print(f"  Satz {gi + 1}/{len(groups)}: {dv:.1f}s  {say[:70]}")
    tail = 0.5
    audio.append(np.zeros(int(tail * SR), np.float32))
    return np.concatenate(audio), t + tail


# ---------------------------------------------------------------------------
# 2) Hintergrund-Clips
# ---------------------------------------------------------------------------

def render_bg(clip, dur, W, H, path, rng, style):
    if clip is None:
        S.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={dur:.3f}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(path)])
        return
    cd = S.probe_duration(clip)
    start = rng.uniform(0, max(0, cd - dur - 0.2)) if cd > dur + 0.5 else 0
    z0, z1 = (1.0, 1.10) if rng.random() < 0.65 else (1.10, 1.0)
    z = f"({z0}+({z1 - z0})*t/{dur:.3f})"
    dark = 0.42 if style == "type" else 0.50
    grade = ("eq=saturation=0.8:contrast=1.08" if style == "type"
             else "eq=saturation=0.55:contrast=1.15:gamma=0.95,colorbalance=rs=0.04:bs=-0.03:rh=0.03")
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame,crop={W}:{H},"
          f"setsar=1,fps={FPS},{grade},"
          f"colorchannelmixer=rr={1 - dark}:gg={1 - dark}:bb={1 - dark},vignette=PI/4")
    loop = ["-stream_loop", "-1"] if cd < dur + start else []
    S.run(["ffmpeg", "-y", "-v", "error", *loop, "-ss", f"{start:.2f}", "-i", str(clip), "-t", f"{dur:.3f}",
           "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(path)])


def music_beats(path, total):
    """Beat-Zeitpunkte der Musik (gecacht)."""
    import hashlib
    cf = CACHE / "beats" / (hashlib.md5(str(path).encode()).hexdigest()[:10] + ".npy")
    if not cf.exists():
        import librosa
        y, sr = librosa.load(str(path), sr=22050, mono=True)
        _, bt = librosa.beat.beat_track(y=y, sr=sr)
        bt = librosa.frames_to_time(bt, sr=sr)
        cf.parent.mkdir(parents=True, exist_ok=True)
        np.save(cf, np.concatenate([bt, [len(y) / sr]]))
    arr = np.load(cf)
    bt, ln = arr[:-1], arr[-1]
    if len(bt) > 1 and np.median(np.diff(bt)) > 0.9:      # langsame Musik: halbe Beats dazunehmen
        bt = np.sort(np.concatenate([bt, (bt[:-1] + bt[1:]) / 2]))
    reps = int(total // ln) + 1                       # Musik wird geloopt
    return np.concatenate([bt + k * ln for k in range(reps)])


def build_background(beats, total, W, H, work, rng, style, max_shot=2.8, beat_times=None):
    groups = []
    for b in beats:
        if groups and b.get("same_clip"):
            groups[-1]["end"] = b["_end"]
        else:
            groups.append({"q": b.get("clip", "dark sky"), "start": b["_start"], "end": b["_end"]})
    groups[0]["start"] = 0.0
    groups[-1]["end"] = total
    # Lange Einstellungen in mehrere Clips teilen (schnellere Schnitte = mehr Spannung)
    split = []
    for g in groups:
        d = g["end"] - g["start"]
        n = max(1, int(d / max_shot + 0.35)) if not g.get("keep") else 1
        qs = [g["q"]] if isinstance(g["q"], str) else list(g["q"])
        for k in range(n):
            q = qs[k % len(qs):] + qs[:k % len(qs)]
            split.append({"q": q, "start": g["start"] + d * k / n, "end": g["start"] + d * (k + 1) / n})
    groups = split
    if beat_times is not None and len(beat_times):
        # Schnittpunkte auf den nächsten Musik-Beat legen (max. 0,25 s verschieben)
        for k in range(1, len(groups)):
            c = groups[k]["start"]
            j = int(np.argmin(np.abs(beat_times - c)))
            nb = float(beat_times[j])
            if abs(nb - c) <= 0.25 and nb - groups[k - 1]["start"] > 0.8 and groups[k]["end"] - nb > 0.8:
                groups[k - 1]["end"] = groups[k]["start"] = nb
    used, files, cuts = set(), [], []
    for gi, g in enumerate(groups):
        f0, f1 = round(g["start"] * FPS), round(g["end"] * FPS)
        dur = (f1 - f0) / FPS
        cuts.append(f0 / FPS)
        queries = ([g["q"]] if isinstance(g["q"], str) else list(g["q"])) + ["night sky stars", "dark clouds"]
        clip = S.pick_clip(queries, used, rng, vertical=H > W)
        f = work / f"bg{gi:02d}.mp4"
        render_bg(clip, dur, W, H, f, rng, style)
        files.append(f)
        print(f"  Clip {gi + 1}/{len(groups)}: {queries[0]} -> {clip.name if clip else 'schwarz'}")
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{f}'\n" for f in files))
    bg = work / "bg.mp4"
    S.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(bg)])
    return bg, cuts


# ---------------------------------------------------------------------------
# 3) Text-Layout
# ---------------------------------------------------------------------------

class Item:
    """Ein Wort an fester Position."""
    __slots__ = ("text", "x", "y", "font", "color", "t", "te", "big")

    def __init__(self, text, x, y, fnt, color, t, te, big=False):
        self.text, self.x, self.y, self.font, self.color = text, x, y, fnt, color
        self.t, self.te, self.big = t, te, big


def is_num(w):
    return bool(re.search(r"\d", w)) and len(re.sub(r"[\d.,%^:/+\-x×]", "", w)) <= 1


def make_blocks_type(beats, W, H, cscale=1.0, vanchor=0.45):
    """Text-Blöcke im Tipp-Stil: bis zu ~3 Zeilen, Buchstabe für Buchstabe aufgebaut."""
    s = W / 1920 if W > H else W / 1080
    f_norm = font("jakarta-extrabold", 82 * s * cscale)
    f_big = font("jakarta-extrabold", 200 * s * cscale)
    f_num = font("oswald-medium", 118 * s * cscale)
    maxw = W * (0.62 if W > H else 0.86)
    anchors = [0.5, 0.42, 0.5, 0.58, 0.36]
    blocks = []
    for bi, b in enumerate(beats):
        toks = b["_tokens"]
        groups, cur = [], []
        for k, tok in enumerate(toks):
            cur.append(tok)
            words = len([x for x in cur if x["style"] != "big"])
            end_sent = tok["word"][-1:] in ".?!…" and words >= 2
            end_comma = tok["word"][-1:] in ",:;" and words >= 5
            if tok.get("brk") or end_sent or end_comma or words >= 8:
                if k + 1 < len(toks) and toks[k + 1]["style"] == "big" and not tok.get("brk"):
                    continue
                groups.append(cur)
                cur = []
        if cur:
            if groups and len(cur) == 1 and cur[0]["style"] != "big" and len(groups[-1]) < 6:
                groups[-1] += cur
            else:
                groups.append(cur)
        ay = anchors[bi % len(anchors)] if W > H else vanchor
        for gi, g in enumerate(groups):
            # ausgewogene Zeilen: Breite so wählen, dass alle Zeilen ähnlich lang sind
            normal = [t for t in g if t["style"] != "big"]
            tot = sum((f_num if is_num(t["word"]) else f_norm).getlength(t["word"] + " ") for t in normal)
            nl = max(1, int(np.ceil(tot / maxw)))
            lim = min(maxw, tot / nl * 1.3 + 30)
            lines, line, lw = [], [], 0
            for tok in g:
                if tok["style"] == "big":
                    if line:
                        lines.append(line)
                    lines.append([tok])
                    line, lw = [], 0
                    continue
                fnt = f_num if is_num(tok["word"]) else f_norm
                w = fnt.getlength(tok["word"])
                sp = fnt.getlength(" ")
                if line and lw + sp + w > lim:
                    lines.append(line)
                    line, lw = [], 0
                line.append(tok)
                lw += (sp if lw else 0) + w
            if line:
                lines.append(line)
            metrics = []
            for ln in lines:
                if ln[0]["style"] == "big":
                    fb = f_big
                    while fb.getlength(ln[0]["word"].upper()) > W * 0.86 and fb.size > 16:
                        fb = font("jakarta-extrabold", fb.size - 10)
                    metrics.append((ln, fb, fb.size * 0.92))
                else:
                    hmax = max((f_num if is_num(t["word"]) else f_norm).size for t in ln)
                    metrics.append((ln, None, hmax * 1.08))
            total_h = sum(m[2] for m in metrics)
            y = H * ay - total_h / 2
            start = g[0]["t"] - 0.02
            end = groups[gi + 1][0]["t"] - 0.04 if gi + 1 < len(groups) else b["_end"]
            items = []
            for ln, fb, lh in metrics:
                y += lh
                if fb is not None:
                    tok = ln[0]
                    txt = tok["word"].upper().rstrip(".,!?:;")
                    items.append(Item(txt, W / 2 - fb.getlength(txt) / 2, y - lh * 0.08, fb, YELLOW,
                                      tok["t"], tok["te"], big=True))
                    continue
                fonts_ = [f_num if is_num(t["word"]) else f_norm for t in ln]
                widths = [f.getlength(t["word"]) for f, t in zip(fonts_, ln)]
                sp = f_norm.getlength(" ")
                x = W / 2 - (sum(widths) + sp * (len(ln) - 1)) / 2
                for f, t, w in zip(fonts_, ln, widths):
                    col = YELLOW if (t["style"] == "hl" or is_num(t["word"])) else WHITE
                    items.append(Item(t["word"], x, y - lh * 0.22, f, col, t["t"], t["te"]))
                    x += w + sp
            blocks.append({"start": start, "end": end, "items": items, "black": b.get("_black")})
    return blocks


def make_blocks_glitch(beats, W, H):
    """Glitch-Stil: kleine gelbe Untertitel unten, ganze Phrase auf einmal."""
    s = W / 1920 if W > H else W / 1080
    f_sub = font("oswald-medium", 50 * s)
    blocks = []
    for b in beats:
        groups, cur = [], []
        for tok in b["_tokens"]:
            cur.append(tok)
            if tok.get("brk") or tok["word"][-1:] in ".?!…,:" or len(cur) >= 5:
                groups.append(cur)
                cur = []
        if cur:
            groups.append(cur)
        for gi, g in enumerate(groups):
            txt = " ".join(t["word"] for t in g).rstrip(".,")
            start = g[0]["t"] - 0.03
            end = groups[gi + 1][0]["t"] - 0.03 if gi + 1 < len(groups) else b["_end"]
            x = W / 2 - f_sub.getlength(txt) / 2
            y = H * (0.925 if W > H else 0.80)
            blocks.append({"start": start, "end": end, "black": b.get("_black"), "instant": True,
                           "items": [Item(txt, x, y, f_sub, YELLOW, start, start + 0.01)]})
    return blocks


def make_bigwords(beats):
    """Riesige Hintergrundwörter ({WORT} im Text oder Feld "bg")."""
    out = []
    for b in beats:
        words = []
        if b.get("bg"):
            words.append((b["bg"], b["_start"] + 0.05))
        run_, t0 = [], None
        for tok in b["_tokens"]:
            if tok["style"] == "bgw":
                run_.append(tok["word"].rstrip(".,!?:;"))
                t0 = t0 if t0 is not None else tok["t"]
            elif run_:
                words.append((" ".join(run_), t0))
                run_, t0 = [], None
        if run_:
            words.append((" ".join(run_), t0))
        for w, t0 in words:
            out.append({"text": w, "start": t0, "end": b["_end"]})
    return out


# ---------------------------------------------------------------------------
# 4) Text-Ebenen zeichnen
# ---------------------------------------------------------------------------

def visible_chars(it, t, instant=False):
    if instant or it.big:
        return len(it.text) if t >= it.t else 0
    if t < it.t:
        return 0
    dur = min(max(0.045 * len(it.text), 0.10), max(0.10, (it.te - it.t) * 0.9), 0.45)
    return min(len(it.text), int(len(it.text) * (t - it.t) / dur) + 1)


def draw_items(items, counts, W, H):
    vis = [(it, n) for it, n in zip(items, counts) if n > 0]
    if not vis:
        return None
    pad = 40
    x0 = int(max(0, min(it.x for it, _ in vis) - pad))
    x1 = int(min(W, max(it.x + it.font.getlength(it.text) for it, _ in vis) + pad))
    y0 = int(max(0, min(it.y - it.font.size for it, _ in vis) - pad))
    y1 = int(min(H, max(it.y + it.font.size * 0.3 for it, _ in vis) + pad))
    img = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    for it, n in vis:
        sd.text((it.x - x0 + 2, it.y - y0 + 4), it.text[:n], font=it.font, fill=(0, 0, 0, 150), anchor="ls")
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)))
    gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(gl)
    glow = False
    for it, n in vis:
        if it.color == YELLOW:
            glow = True
            gd.text((it.x - x0, it.y - y0), it.text[:n], font=it.font, fill=(255, 190, 20, 150), anchor="ls")
    if glow:
        img.alpha_composite(gl.filter(ImageFilter.GaussianBlur(max(6, vis[0][0].font.size // 6))))
    d = ImageDraw.Draw(img)
    for it, n in vis:
        d.text((it.x - x0, it.y - y0), it.text[:n], font=it.font, fill=it.color + (255,), anchor="ls")
    return x0, y0, np.asarray(img)


def composite(frame, layer, scale=1.0):
    if layer is None:
        return
    x0, y0, arr = layer
    if scale != 1.0:
        h, w = arr.shape[:2]
        nw, nh = int(w * scale), int(h * scale)
        arr = cv2.resize(arr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        x0 -= (nw - w) // 2
        y0 -= (nh - h) // 2
    H, W = frame.shape[:2]
    h, w = arr.shape[:2]
    ax0, ay0 = max(0, x0), max(0, y0)
    ax1, ay1 = min(W, x0 + w), min(H, y0 + h)
    if ax1 <= ax0 or ay1 <= ay0:
        return
    src = arr[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0].astype(np.float32)
    a = src[..., 3:4] / 255.0
    dst = frame[ay0:ay1, ax0:ax1].astype(np.float32)
    frame[ay0:ay1, ax0:ax1] = (dst * (1 - a) + src[..., :3] * a).astype(np.uint8)


def render_bigword(text, n, W, H, style):
    txt = text[:n]
    if not txt:
        return None
    s = W / 1920 if W > H else W / 1080 * 0.75
    f = font("oswald-medium", (430 if style == "glitch" else 360) * s)
    while f.getlength(text) > W * 0.92 and f.size > 80:
        f = font("oswald-medium", f.size - 10)
    full_w = f.getlength(text)
    img = Image.new("RGBA", (int(full_w) + 40, int(f.size * 1.3)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = (214, 216, 226, 225) if style == "glitch" else (230, 230, 236, 80)
    d.text((20, f.size * 1.05), txt, font=f, fill=col, anchor="ls")
    return int(W / 2 - full_w / 2 - 20), int(H / 2 - f.size * 0.7), np.asarray(img)


# ---------------------------------------------------------------------------
# 5) Effekte
# ---------------------------------------------------------------------------

def glitch(frame, rng, strength=1.0):
    H, W = frame.shape[:2]
    out = frame.copy()
    for _ in range(rng.randint(2, 6)):                      # verschobene Bildstreifen
        y = rng.randint(0, H - 10)
        h = rng.randint(6, int(70 * strength) + 8)
        out[y:y + h] = np.roll(out[y:y + h], int(rng.uniform(-90, 90) * strength), axis=1)
    k = int(rng.uniform(5, 16) * strength)                  # RGB-Versatz
    out[..., 0] = np.roll(out[..., 0], k, axis=1)
    out[..., 2] = np.roll(out[..., 2], -k, axis=1)
    for _ in range(rng.randint(1, 3)):                      # helle Linien
        y = rng.randint(0, H - 6)
        x0 = rng.randint(0, W // 2)
        x1 = rng.randint(x0 + W // 6, W)
        col = np.array(rng.choice([(255, 230, 255), (230, 240, 255), (255, 255, 255), (200, 120, 255)]), np.float32)
        hh = rng.randint(2, 7)
        band = out[y:y + hh, x0:x1].astype(np.float32)
        out[y:y + hh, x0:x1] = np.clip(band * 0.35 + col * 0.65, 0, 255).astype(np.uint8)
    if rng.random() < 0.35:
        out = np.clip(out.astype(np.int16) + rng.randint(15, 45), 0, 255).astype(np.uint8)
    return out


class Grain:
    def __init__(self, W, H, amount, seed=1):
        r = np.random.default_rng(seed)
        self.frames = []
        for _ in range(6):
            n = r.normal(0, amount, (H // 2, W // 2)).astype(np.float32)
            n = cv2.resize(n, (W, H), interpolation=cv2.INTER_LINEAR)
            self.frames.append(n[..., None].astype(np.int16))
        self.i = 0

    def apply(self, frame):
        self.i = (self.i + 1) % len(self.frames)
        return np.clip(frame.astype(np.int16) + self.frames[self.i], 0, 255).astype(np.uint8)


def punch(frame, scale):
    H, W = frame.shape[:2]
    big = cv2.resize(frame, (int(W * scale), int(H * scale)), interpolation=cv2.INTER_LINEAR)
    y, x = (big.shape[0] - H) // 2, (big.shape[1] - W) // 2
    return big[y:y + H, x:x + W]


# ---------------------------------------------------------------------------
# 6) Sound-Effekte (selbst erzeugt)
# ---------------------------------------------------------------------------

def sfx_boom():
    t = np.arange(int(1.2 * SR)) / SR
    f = 62 * np.exp(-t * 1.5) + 34
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 3.2)
    noise = np.random.default_rng(3).normal(0, 1, len(t)) * np.exp(-t * 28) * 0.25
    noise = np.convolve(noise, np.ones(30) / 30, mode="same")
    return (sub * 0.9 + noise).astype(np.float32)


def sfx_glitch(rng):
    n = int(rng.uniform(0.08, 0.16) * SR)
    x = np.random.default_rng(rng.randint(0, 10 ** 6)).normal(0, 1, n)
    step = rng.randint(30, 120)
    x = np.repeat(x[::step], step)[:n]
    x = x * (np.sin(np.arange(n) / SR * 2 * np.pi * rng.uniform(40, 120)) > 0)
    env = np.minimum(1, np.arange(n) / 200) * np.minimum(1, (n - np.arange(n)) / 400)
    return (x * env * 0.5).astype(np.float32)


def sfx_riser(dur=0.9):
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = np.random.default_rng(5).normal(0, 1, n)
    out = np.zeros(n, np.float32)
    for w, a in ((30, 1.0), (10, 0.6), (3, 0.35)):
        out += np.convolve(x, np.ones(w) / w, mode="same").astype(np.float32) * a
    tone = np.sin(2 * np.pi * np.cumsum(180 + 900 * (t / dur) ** 2) / SR) * 0.15
    env = (t / dur) ** 2.5
    return ((out * np.linspace(0.3, 1, n) + tone) * env * 1.6).astype(np.float32)


def sfx_whoosh():
    n = int(0.55 * SR)
    x = np.random.default_rng(9).normal(0, 1, n)
    out = np.zeros(n, np.float32)
    lo = np.convolve(x, np.ones(40) / 40, mode="same")      # dunkles Rauschen
    hi = np.convolve(x, np.ones(6) / 6, mode="same")         # helles Rauschen
    mixk = np.linspace(0, 1, n)
    out = lo * (1 - mixk) + hi * mixk * 0.5
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    return (out * env * 3.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--vertical", action="store_true", help="9:16 statt 16:9")
    ap.add_argument("--out")
    ap.add_argument("--voice", choices=["auto", "eleven", "edge"], default="auto")
    ap.add_argument("--preview", action="store_true", help="schnell, halbe Auflösung")
    args = ap.parse_args()

    spec = json.loads(Path(args.script).read_text())
    if spec.get("preset"):   # Voreinstellungen aus engine/presets/<name>.json (Skript-Werte haben Vorrang)
        pre = json.loads((ROOT / "engine" / "presets" / f"{spec['preset']}.json").read_text())
        spec = {**{k: v for k, v in pre.items() if not k.startswith("_")}, **spec}
    name = Path(args.script).stem
    style = spec.get("style", "type")
    vertical = args.vertical or spec.get("vertical")
    W, H = (1080, 1920) if vertical else (1920, 1080)
    if args.preview:
        W, H = W // 2, H // 2
    out = Path(args.out or ROOT / "videos" /
               f"{name}{'_9x16' if vertical else ''}{'_preview' if args.preview else ''}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = CACHE / "work" / name
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    rng = random.Random(spec.get("seed", name))

    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key and (ROOT / ".elevenlabs_key").exists():
        key = (ROOT / ".elevenlabs_key").read_text().strip()
    use_eleven = args.voice == "eleven" or (args.voice == "auto" and bool(key))
    print(f"Stimme: {'ElevenLabs' if use_eleven else 'Fallback ' + S.EDGE_VOICE}"
          f" | Clips: {'Pexels + Mixkit' if os.environ.get('PEXELS_API_KEY') else 'Mixkit'} | Stil: {style}")

    beats = spec["beats"]
    voice, total = build_voice(spec, beats, work, use_eleven, key)
    bg, cuts = build_background(beats, total, W, H, work, rng, style, spec.get("max_shot", 2.8),
                                music_beats(spec.get("music", MUSIC), total) if spec.get("beat_snap") else None)

    blocks = make_blocks_glitch(beats, W, H) if style == "glitch" else make_blocks_type(
        beats, W, H, spec.get("caption_scale", 1.0), spec.get("text_anchor_vertical", 0.45))
    bigwords = make_bigwords(beats)
    blacks = [(b["_black"], total if b is beats[-1] else b["_end"]) for b in beats if b.get("_black")]
    for bl in blocks:
        if bl["end"] >= beats[-1]["_end"] - 0.01:
            bl["end"] = total

    gl_events = []
    if style == "glitch":
        for c in cuts[1:]:
            gl_events.append((c - 0.05, c + rng.uniform(0.12, 0.22)))
        for bw in bigwords:
            gl_events.append((bw["start"], bw["start"] + 0.12))
        t = 1.0
        while t < total:
            t += rng.uniform(1.6, 3.8)
            gl_events.append((t, t + rng.uniform(0.06, 0.2)))
    big_hits = sorted({round(it.t, 3) for bl in blocks for it in bl["items"] if it.big})

    # --- Audio: Stimme + Musik + Effekte
    sfx = np.zeros(int((total + 2) * SR), np.float32)

    def add(sig, at, vol):
        i = int(at * SR)
        j = min(len(sfx), i + len(sig))
        if 0 <= i < j:
            sfx[i:j] += sig[:j - i] * vol
    beat_cuts = sorted({round(b["_start"], 3) for b in beats[1:] if not b.get("same_clip")})
    if spec.get("sfx") == "samples":
        # Echte Sound-Effekte (assets/sfx, Mixkit-Lizenz). "hit" = Zeitpunkt des Einschlags in der Datei.
        lib = {n: (S.decode(ROOT / "assets" / "sfx" / f"{n}.mp3"), hit) for n, hit in SFX_LIB.items()}

        def place(name, at, vol):
            sig, hit = lib[name]
            st = at - hit
            if st < 0:
                sig = sig[int(-st * SR):]
                st = 0
            add(sig, st, vol)
        place(spec.get("hook_sfx", "impact_soft"), 0.0, 0.55)
        for t in big_hits:
            place("riser_trailer", t, 0.45)
            place("impact_big", t, 0.55)
        last_wh = -10
        for c in beat_cuts:
            if c - last_wh > 4 and rng.random() < spec.get("whoosh_chance", 0.45) and \
                    not any(abs(c - t) < 1.5 for t in big_hits):
                place("whoosh_fast", c, 0.22)
                last_wh = c
        for b in beats:
            if b.get("sfx"):
                place(b["sfx"], b["_start"] + 0.05, b.get("sfx_volume", 0.5))
    else:
        boom = sfx_boom()
        riser = sfx_riser()
        hook_hits = [0.0] if spec.get("hook_hit", True) else []
        for t in big_hits + hook_hits:
            add(boom, t, spec.get("boom_volume", 0.55))
        for t in big_hits:
            add(riser, t - len(riser) / SR, 0.18)
        if style == "type":
            wh = sfx_whoosh()
            for c in cuts[1:]:
                add(wh, c - 0.22, 0.07)
    if style == "glitch":
        for a, _ in gl_events:
            add(sfx_glitch(rng), a, 0.09)
        wh = sfx_whoosh()
        for bw in bigwords:
            add(wh, bw["start"] - 0.25, 0.22)
    sfx = sfx[:int(total * SR)]
    vpath, spath = work / "voice.f32", work / "sfx.f32"
    voice.astype(np.float32).tofile(vpath)
    sfx.tofile(spath)
    mix = work / "mix.wav"
    mvol = spec.get("music_volume", 0.34)
    S.run(["ffmpeg", "-y", "-v", "error",
           "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", str(vpath),
           "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", str(spath),
           "-stream_loop", "-1", "-i", str(spec.get("music", MUSIC)),
           "-filter_complex",
           f"[0:a]{VOICE_MASTER if spec.get('audio_master') else ''}"
           f"loudnorm=I={-14 if spec.get('audio_master') else -15}:TP=-1.5:LRA={9 if spec.get('audio_master') else 11},"
           f"aresample={SR},aformat=channel_layouts=stereo[vo];"
           f"[1:a]aformat=channel_layouts=stereo[fx];"
           f"[2:a]atrim=0:{total:.3f},volume={mvol},afade=t=out:st={total - 1.8:.3f}:d=1.8,"
           f"aformat=channel_layouts=stereo[mu];"
           f"[vo]asplit=2[vo1][vsc];[mu][vsc]sidechaincompress="
           f"{'threshold=0.08:ratio=2:attack=40:release=700' if spec.get('audio_master') else 'threshold=0.03:ratio=3:attack=20:release=350'}[mud];"
           f"[vo1][fx][mud]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.95[a]",
           "-map", "[a]", "-t", f"{total:.3f}", str(mix)])

    # --- Video Bild für Bild
    print("Rendere Bilder ...")
    nframes = int(round(total * FPS))
    reader = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(bg), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                              stdout=subprocess.PIPE)
    writer = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                               "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(mix),
                               "-c:v", "libx264", "-preset", "veryfast" if args.preview else "medium",
                               "-crf", "21", "-maxrate", "10M", "-bufsize", "20M", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                               "-shortest", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    grain = Grain(W, H, 13 if style == "glitch" else 7)
    fx_cut = spec.get("cut_flash", True)
    cache, bw_cache = {}, {}
    fsize = W * H * 3
    last = np.zeros((H, W, 3), np.uint8)
    for fi in range(nframes):
        t = fi / FPS
        raw = reader.stdout.read(fsize)
        frame = np.frombuffer(raw, np.uint8).reshape(H, W, 3).copy() if len(raw) == fsize else last.copy()
        last = frame
        black = any(a <= t < e for a, e in blacks)
        if black:
            frame = np.zeros_like(frame)
        else:
            sc = 1.0
            for bt in big_hits + [0.0]:                       # Punch-Zoom bei großen Wörtern + Hook
                if 0 <= t - bt < 0.4:
                    sc = max(sc, 1.0 + 0.09 * (1 - (t - bt) / 0.4) ** 2)
            for c in cuts[1:]:                                # Zoom-Übergang bei jedem Schnitt
                if 0 <= t - c < 0.25:
                    sc = max(sc, 1.0 + 0.10 * (1 - (t - c) / 0.25) ** 3)
            if sc > 1.001:
                frame = punch(frame, sc)
            if fx_cut and any(0 <= t - c < 1.0 / FPS for c in cuts[1:]):
                frame = np.clip(frame.astype(np.int16) + 55, 0, 255).astype(np.uint8)
            for bi, bw in enumerate(bigwords):                # riesige Hintergrundwörter
                if bw["start"] <= t < bw["end"]:
                    n = min(len(bw["text"]), int((t - bw["start"]) / 0.09) + 1)
                    k = (bi, n)
                    if k not in bw_cache:
                        bw_cache[k] = render_bigword(bw["text"], n, W, H, style)
                    composite(frame, bw_cache[k])
        for bi, bl in enumerate(blocks):                      # Text
            if not (bl["start"] <= t < bl["end"]):
                continue
            items = bl["items"]
            if black and bl.get("black") and t >= bl["black"]:
                items = [it for it in items if it.big] or items
            counts = tuple(visible_chars(it, t, bl.get("instant")) for it in items)
            k = (bi, len(items), counts)
            if k not in cache:
                if len(cache) > 300:
                    cache.clear()
                cache[k] = draw_items(items, counts, W, H)
            composite(frame, cache[k])
        if style == "glitch":                                 # Film-Look + Glitches
            fl = 1.0 + 0.035 * np.sin(t * 13.0) * np.sin(t * 3.1)
            frame = np.clip(frame.astype(np.float32) * fl, 0, 255).astype(np.uint8)
            for a, e in gl_events:
                if a <= t < e:
                    frame = glitch(frame, rng, 1.0 if W >= 1280 else 0.5)
                    break
        # Einschlag bei großen Wörtern / Hook: Wackeln, Blitz, RGB-Versatz
        hit = min([t - bt for bt in big_hits + [0.0] if 0 <= t - bt < 0.45] or [None]) if True else None
        if hit is not None:
            k = 1 - hit / 0.45
            amp = 22 * (max(W, H) / 1920) * spec.get("shake", 1.0) * k ** 2
            dx, dy = rng.uniform(-amp, amp), rng.uniform(-amp, amp)
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            frame = cv2.warpAffine(frame, M, (W, H), borderMode=cv2.BORDER_REFLECT)
            if hit < 0.12:
                frame = np.clip(frame.astype(np.float32) + 170 * (1 - hit / 0.12), 0, 255).astype(np.uint8)
            if hit < 0.1:
                sh = int(12 * (W / 1920))
                frame[..., 0] = np.roll(frame[..., 0], sh, axis=1)
                frame[..., 2] = np.roll(frame[..., 2], -sh, axis=1)
        frame = grain.apply(frame)
        writer.stdin.write(frame.tobytes())
        if fi % (FPS * 10) == 0:
            print(f"  {t:5.1f}s / {total:.1f}s")
    writer.stdin.close()
    writer.wait()
    reader.kill()
    print(f"Fertig: {out}  ({total:.1f}s)")


if __name__ == "__main__":
    main()
