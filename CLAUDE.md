# TikTok-Videos (deutsch) – Anleitung für Claude

Der Nutzer schreibt z. B. **„Erstell mir ein neues Video“**. Dann machst du ALLES selbst:
Idee → Skript → Video rendern → Video an den Nutzer schicken (SendUserFile) → committen & pushen.
Nicht nachfragen, außer der Nutzer wünscht ein bestimmtes Thema.

## Ablauf

1. Thema wählen: eins aus `IDEEN.md`, das noch nicht in `scripts/` steht (oder das Thema des Nutzers).
   Danach in `IDEEN.md` als erledigt markieren.
2. Skript schreiben: `scripts/NNN_kurzname.json` (nächste freie Nummer). Format siehe unten.
3. Rendern:
   ```
   pip install -q edge-tts numpy pillow   # falls nötig
   python3 engine/make_video.py scripts/NNN_kurzname.json
   ```
   Ergebnis: `videos/NNN_kurzname.mp4` (1920x1080, 16:9 wie die Originale).
   Optional `--vertical` für 9:16 (1080x1920).
4. Kontrolle: Frames ansehen (`ffmpeg -i video -vf "fps=1,scale=400:-1,tile=4x4" g%02d.jpg`),
   prüfen, dass Clips zum Text passen (kein Greenscreen, kein Logo, nichts Albernes).
   Unpassenden Clip → anderen Suchbegriff im Skript, neu rendern.
5. Video mit `SendUserFile` schicken, dazu die TikTok-Beschreibung (`caption` aus dem Skript).
6. Committen & pushen (Skript + Video).

## Stimme

- Immer dieselbe Stimme. Wenn `ELEVENLABS_API_KEY` gesetzt ist (oder Datei `.elevenlabs_key`):
  ElevenLabs „Brian“ (`nPczCjzI2devNBz1zQrb`), Modell `eleven_multilingual_v2` – tief, ruhig, Erzähler.
- Sonst Fallback: Microsoft `de-DE-FlorianMultilingualNeural` (kostenlos, kein Key).
- Zahlen, die anders gesprochen werden als geschrieben → Feld `"say"` mit ausgeschriebenem Text.

## Stil der Referenzvideos (savetik*.mp4 – Kanal @ronneson)

- Länge 60–75 s, 16:9, durchgehend dunkle, kinoartige Stock-Clips (Weltall, Erde, Natur,
  Silhouetten, Menschen), stark abgedunkelt, langsamer Zoom. Schnitt etwa alle 2–5 s, harte Schnitte.
- Untertitel mittig, 2–4 Wörter pro Einblendung, Wort-genau zur Stimme, fette runde Sans-Serif
  (Plus Jakarta Sans ExtraBold), weiß; Schlüsselwörter gelb (#ECC41C).
- Höhepunkte: ein Wort riesig in Gelb, darüber kleiner der vorige Text („And you wouldn't be / YOU“).
- Zahlen in schmaler Schrift (Oswald), gelb, groß.
- Manchmal riesiges halbtransparentes Wort hinter dem Untertitel („BIG BANG“, „WAR“, „Bubble“).
- Musik: `Ohsxnta - changes (intro only) slowed.mp3` leise im Hintergrund, durchgehend.
- Stimme: ruhig, tief, ernst, kleine Pausen zwischen Sätzen.

### Aufbau eines Skripts (wichtig!)

1. **Hook (erster Satz, < 4 s):** provokante Frage oder „Was wäre, wenn ich dir sage …“.
   Muss sofort stutzig machen. Beispiele: „Wie hoch ist die Wahrscheinlichkeit, dass du stirbst? Eins zu eins.“
2. **Kurze Verstärkung:** „Diese Frage zerstört jedes Gehirn, das sie berührt.“ / „Klingt verrückt. Aber …“
3. **Hauptteil:** 6–10 kurze Sätze mit echten, überprüfbaren Fakten (Wissenschaft, Universum, Geschichte,
   Psychologie). Bilder im Kopf erzeugen („Wäre ein Atomkern so groß wie ein Fußball …“). Kurz, einfach.
4. **Wendung / Einordnung:** „Und trotzdem …“ / „Aber hier ist der Punkt …“
5. **Emotionales oder nachdenkliches Ende:** zurück zum Zuschauer persönlich
   („… und trotzdem habe ich dich gefunden. Wie hoch ist die Wahrscheinlichkeit dafür?“).
   Letzter Satz kurz, gern mit einem großen Wort.
- 140–175 Wörter gesamt (≈ 60–70 s). Du-Form. Keine Emojis im gesprochenen Text.
- Fakten müssen stimmen. Spekulative Theorien als Theorie kennzeichnen („Manche Physiker glauben …“).

## Skript-Format

```json
{
  "title": "Kurzer Titel",
  "caption": "TikTok-Beschreibung mit Hashtags #wissen #universum #fyp",
  "beats": [
    {"text": "Was wäre, wenn du noch *nie* etwas berührt hast?", "clip": ["hand touching", "hand"]},
    {"text": "Nicht mal den Menschen, den du am meisten [LIEBST]", "clip": ["couple holding hands", "couple"]},
    {"text": "Und ein Atom ist zu *99,9999 %* leer.", "say": "Und ein Atom ist zu neunundneunzig Komma neun neun neun neun Prozent leer.", "clip": ["nebula", "space"]},
    {"text": "Alles besteht aus *Atomen.*", "clip": ["particles"], "bg": "ATOME"},
    {"text": "hast du es *gespürt.*", "clip": ["hug"], "same_clip": true}
  ]
}
```

- Ein Beat = ein Satz oder Satzteil = ein Clip. Satzteile ohne Satzzeichen am Ende werden
  automatisch mit dem nächsten Beat zusammen gesprochen (natürliche Betonung), aber trotzdem geschnitten.
- `*wort*` gelb · `[WORT]` riesig (am Satzende, danach Pause) · `bg` riesiges Hintergrundwort
- `clip`: englische Suchbegriffe für Mixkit, bester zuerst. Einfache Wörter funktionieren am besten
  (space, galaxy, earth, ocean, sunset, silhouette, hug, brain, lightning, city night, forest, particles …).
- `same_clip: true` = gleicher Clip läuft weiter · `pause` = Pause nach dem Satz in Sekunden (Standard 0.38)
- Optional auf oberster Ebene: `"handle": "@deinname"` (Wasserzeichen), `"music_volume": 0.22`.

## Technik

- `engine/make_video.py` – komplette Pipeline. Clips & Suchergebnisse werden in `.cache/` gespeichert.
- Clips: Mixkit (kostenlos, ohne Key, Lizenz erlaubt Nutzung in Social-Media-Videos), max. 720p.
- Netzwerk läuft über einen Proxy; CA-Bundle `/root/.ccr/ca-bundle.crt` wird automatisch genutzt.
