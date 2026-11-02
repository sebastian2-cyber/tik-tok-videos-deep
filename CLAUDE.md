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
   pip install -q -r engine/requirements.txt   # falls nötig
   python3 engine/make_video.py scripts/NNN_kurzname.json --preview   # erst prüfen
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

- ElevenLabs-Key: Umgebungsvariable `ELEVENLABS_API_KEY` oder Datei `.elevenlabs_key` (nie committen!).
- Reihenfolge (automatisch): `ELEVENLABS_VOICE_ID` → „Simon – Deep and Strong“ (deutscher Muttersprachler,
  braucht Bezahl-Plan) → „Brian“ (Free-Plan). Modell `eleven_multilingual_v2`.
- Ohne Key: Fallback Microsoft `de-DE-FlorianMultilingualNeural` (klingt deutlich schlechter).
- Free-Plan = 10.000 Zeichen/Monat ≈ 9 Videos. Sprachausgaben werden in `.cache/tts` gecacht,
  Neu-Rendern kostet also nichts. Erst mit `--preview` (halbe Auflösung, schnell) prüfen, dann final.
- Zahlen, die anders gesprochen werden als geschrieben → Feld `"say"` mit ausgeschriebenem Text.
- Optional `PEXELS_API_KEY` (kostenlos auf pexels.com/api) → viel bessere kinoartige Clips.

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
  "caption": "TikTok-Beschreibung #wissen #universum #fyp",
  "style": "type",
  "beats": [
    {"text": "Was wäre, wenn ich dir sage, dass du *älter* bist als die Erde?", "clip": ["earth from space", "earth"]},
    {"text": "Nicht du selbst. / Aber fast alles, woraus du *bestehst.*", "clip": ["galaxy", "nebula"]},
    {"text": "Bei über *100 Millionen Grad.*", "say": "Bei über hundert Millionen Grad.", "clip": ["fire"], "bg": "100.000.000°"},
    {"text": "Und als diese Sterne starben, / explodierten sie als [SUPERNOVA]", "clip": ["explosion space"]},
    {"text": "Wir leben im Jahr {1729}", "clip": ["earth"]}
  ]
}
```

- `style`: `"type"` (Weltall-/Gefühls-Videos: Schreibmaschinen-Text, Blöcke über mehrere Zeilen,
  Bass-Hit + Schwarz-Schnitt nach großem Wort) oder `"glitch"` (Geschichte/Mystery/Verschwörung:
  Film-Look, Korn, Flackern, RGB-Glitches, kleine gelbe Untertitel unten, riesige Wörter in der Mitte).
- `*wort*` gelb · `[WORT]` riesig gelb am Satzende → Bass-Hit, danach Schwarz, nur das Wort bleibt
  (2–4 Mal pro Video, an den stärksten Stellen) · `{WORT}` riesiges Hintergrundwort, erscheint wenn
  es gesprochen wird (im Glitch-Stil oft nutzen: Jahreszahlen, „KRIEG“, „KAISER“) · `bg` = Hintergrundwort
  ab Beat-Start · ` / ` = neuer Textblock.
- Ein Beat = ein Clip. Beats ohne Satzzeichen am Ende werden mit dem nächsten zusammen gesprochen.
- `clip`: englische Suchbegriffe, bester zuerst, einfache Wörter.
- `same_clip: true` = Clip läuft weiter · `pause` = Pause danach (Sek.) · `black: false` = kein Schwarz-Schnitt
- Optional: `"music_volume": 0.24`, `"boom_volume": 0.5`.

## Technik

- `engine/make_video.py` – komplette Pipeline. Clips & Suchergebnisse werden in `.cache/` gespeichert.
- Clips: Mixkit (kostenlos, ohne Key, Lizenz erlaubt Nutzung in Social-Media-Videos), max. 720p.
- Netzwerk läuft über einen Proxy; CA-Bundle `/root/.ccr/ca-bundle.crt` wird automatisch genutzt.
