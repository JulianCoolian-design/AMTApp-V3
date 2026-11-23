import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from io import BytesIO
from itertools import combinations_with_replacement

import airportsdata
import numpy as np
import plotly.graph_objects as go
import pytesseract
import streamlit as st
from PIL import Image, ImageOps

# ==========================================
# 1. DATENBANK
# ==========================================
FLUGZEUGE = [
    {"name": "Il-96-300", "preis": 110000000, "reichweite": 11500, "sitze": 300, "verbrauch": 4.42, "abnutzung": 2.4,
     "kategorie": 6},
    {"name": "A350-900ULR", "preis": 317400000, "reichweite": 18000, "sitze": 440, "verbrauch": 2.13, "abnutzung": 1.3,
     "kategorie": 5},
    {"name": "E195-E2", "preis": 60200000, "reichweite": 4815, "sitze": 146, "verbrauch": 2.46, "abnutzung": 1.4,
     "kategorie": 4},
    {"name": "B777-300ER", "preis": 315000000, "reichweite": 14695, "sitze": 522, "verbrauch": 2.28, "abnutzung": 1.9,
     "kategorie": 8},
    {"name": "A380-800", "preis": 403000000, "reichweite": 15556, "sitze": 853, "verbrauch": 2.44, "abnutzung": 1.7,
     "kategorie": 8},
    {"name": "B737-900ER", "preis": 94600000, "reichweite": 6050, "sitze": 220, "verbrauch": 2.23, "abnutzung": 1.8,
     "kategorie": 6},
    {"name": "B787-10", "preis": 257000000, "reichweite": 10067, "sitze": 440, "verbrauch": 2.07, "abnutzung": 1.4,
     "kategorie": 7},
    {"name": "B787-8", "preis": 206800000, "reichweite": 15196, "sitze": 381, "verbrauch": 2.23, "abnutzung": 1.6,
     "kategorie": 8},
    {"name": "B747-300", "preis": 272500000, "reichweite": 12400, "sitze": 608, "verbrauch": 2.43, "abnutzung": 2.6,
     "kategorie": 4},
    {"name": "B747-8I", "preis": 352800000, "reichweite": 14825, "sitze": 730, "verbrauch": 2.24, "abnutzung": 1.6,
     "kategorie": 9},
]
ALLE_NAMEN = [f["name"] for f in FLUGZEUGE]

OPTION_COLORS = ["#4fc3f7", "#ba68c8", "#ffb74d"]
RADAR_AXES = ["Deckung", "Gesamtkosten", "Kosten/Sitz", "Verbrauch", "Abnutzung", "Flottengröße"]

SCORING_DEFAULTS = {
    "deckung": 20,
    "gesamtkosten": 10,
    "kosten_pro_sitz": 35,
    "verbrauch": 10,
    "abnutzung": 20,
    "flottengroesse": 5,
}

SCORING_LABELS = {
    "deckung": "Nachfrageabdeckung",
    "gesamtkosten": "Gesamtkosten",
    "kosten_pro_sitz": "Kosten pro Sitz",
    "verbrauch": "Verbrauch",
    "abnutzung": "Abnutzung",
    "flottengroesse": "Flottengröße",
}


# ==========================================
# 2. HILFSFUNKTIONEN
# ==========================================
def abrunden_gerade(x):
    """Rundet den Betrag auf eine gerade Zahl ab und erhält das Vorzeichen."""
    wert = int(x)
    vorzeichen = -1 if wert < 0 else 1
    return vorzeichen * ((abs(wert) // 2) * 2)


def gradient_color(val, min_val, max_val):
    """Verlauf Grün -> Gelb -> Rot."""
    if max_val == min_val:
        return "#66bb6a"
    score = max(0.0, min(1.0, (val - min_val) / (max_val - min_val)))
    green, yellow, red = (102, 187, 106), (255, 202, 40), (239, 83, 80)
    if score <= 0.5:
        a, b, t = green, yellow, score * 2.0
    else:
        a, b, t = yellow, red, (score - 0.5) * 2.0
    r, g, bl = (int(a[i] + t * (b[i] - a[i])) for i in range(3))
    return f"#{r:02x}{g:02x}{bl:02x}"


def hex_to_rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def de_num(x, decimals=0):
    return f"{x:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_money(x):
    return de_num(x) + " $"


@st.cache_resource
def lade_flughafen_datenbank():
    """Lädt eine lokale IATA-Datenbank für Flughafennamen und Länder."""
    return airportsdata.load("IATA")


def land_flagge(iso_code):
    """Wandelt einen ISO-3166-Alpha-2-Code in eine Länderflagge um."""
    if not iso_code or len(iso_code) != 2:
        return ""
    return "".join(chr(127397 + ord(char)) for char in iso_code.upper())


# ==========================================
# 2b. BILDERKENNUNG (AM4-Screenshot)
# ==========================================
# Relative Positionen (Anteil an Breite/Höhe) – in allen Test-Screenshots identisch.
CODE_ORIGIN_X = (0.462, 0.499)
CODE_DEST_X = (0.501, 0.537)
CODE_Y = (0.160, 0.240)
NAME_Y = (0.165, 0.235)
CATEGORY_BAND_Y = (0.300, 0.405)
CATEGORY_X = (0.47, 0.585)
DISTANCE_BOX = (0.45, 0.382, 0.565, 0.430)
DEMAND_X = (0.72, 0.875)
DEMAND_ROWS = {"economy": 0.351, "business": 0.405, "first": 0.459, "freight": 0.514}
ROW_HALF_HEIGHT = 0.024
NORMAL_WIDTH = 2400


# ---------------------------------------------------------------- Hilfsfunktionen
def _crop(image, x1, y1, x2, y2):
    w, h = image.size
    return image.crop((int(w * x1), int(h * y1), int(w * x2), int(h * y2)))


def _mask_to_image(mask, scale=6, pad=24):
    """Boolesche Maske (True = Text) -> schwarzer Text auf weißem Grund, mit Rand."""
    arr = np.where(mask, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr, "L")
    img = img.resize((max(1, img.width * scale), max(1, img.height * scale)), Image.LANCZOS)
    return ImageOps.expand(img, border=pad, fill=255)


def _tight_bbox(mask, margin=2):
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    y0, y1 = max(0, ys.min() - margin), min(mask.shape[0], ys.max() + 1 + margin)
    x0, x1 = max(0, xs.min() - margin), min(mask.shape[1], xs.max() + 1 + margin)
    return x0, y0, x1, y1


def _ocr(img, psm, whitelist=None, lang="eng"):
    cfg = f"--oem 3 --psm {psm}"
    if whitelist:
        cfg += f" -c tessedit_char_whitelist={whitelist}"
    return pytesseract.image_to_string(img, lang=lang, config=cfg).strip()


def _runs(flags):
    """Zusammenhängende True-Abschnitte eines 1D-Arrays als (start, ende)."""
    runs, start = [], None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(flags)))
    return runs


def _drop_border_blobs(mask):
    """Entfernt Spalten am Rand, die zu Flaggen/Kanten gehören (Maske berührt den Rand)."""
    cols = mask.any(axis=0)
    runs = _runs(cols)
    out = np.zeros_like(mask)
    for x0, x1 in runs:
        if x0 == 0 or x1 == mask.shape[1]:
            continue
        out[:, x0:x1] = mask[:, x0:x1]
    return out


# ---------------------------------------------------------------- Flughafen-Codes
def _code_candidates(image, x_range):
    """Liest einen 3-Buchstaben-Code mit mehreren Schwellen/Segmentierungen und zählt Stimmen."""
    rgb = np.asarray(_crop(image, x_range[0], CODE_Y[0], x_range[1], CODE_Y[1])).astype(int)
    darkest = rgb.max(axis=2)  # dunkle Schrift -> kleiner Maximalwert
    votes = Counter()
    for threshold in (75, 95, 115):
        mask = _drop_border_blobs(darkest < threshold)
        # Reststaub (Gebäude, Flaggenkanten) entfernen: nur Spalten mit genug Text behalten
        if mask.sum() < 40:
            continue
        box = _tight_bbox(mask)
        if not box:
            continue
        x0, y0, x1, y1 = box
        if (y1 - y0) < 8:
            continue
        img = _mask_to_image(mask[y0:y1, x0:x1])
        for psm in (7, 8):
            text = re.sub(r"[^A-Z]", "", _ocr(img, psm, "ABCDEFGHIJKLMNOPQRSTUVWXYZ").upper())
            if len(text) == 3:
                votes[text] += 1
    return votes


def _hamming1(code, airports):
    """Alle Codes der Datenbank, die sich an genau einer Stelle von 'code' unterscheiden."""
    out = []
    for i in range(3):
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            alt = code[:i] + letter + code[i + 1:]
            if alt != code and alt in airports:
                out.append(alt)
    return out


def _read_airport_name(image, side):
    """Liest 'Flughafen von X' (Textteil) als Gegenprobe für den Code."""
    x_range = (0.14, 0.43) if side == "origin" else (0.57, 0.86)
    rgb = np.asarray(_crop(image, x_range[0], NAME_Y[0], x_range[1], NAME_Y[1])).astype(int)
    mask = rgb.max(axis=2) < 95
    if mask.sum() < 60:
        return ""
    box = _tight_bbox(mask)
    x0, y0, x1, y1 = box
    img = _mask_to_image(mask[y0:y1, x0:x1], scale=4)
    try:
        return _ocr(img, 7, None, lang="eng+deu")
    except Exception:  # deutsches Sprachpaket nicht installiert -> nur Englisch
        return _ocr(img, 7, None, lang="eng")


def _name_score(entry, ocr_name):
    if not ocr_name:
        return 0.0
    text = re.sub(r"^\s*(flughafen\s+(von|vom)?|airport)\s+", "", ocr_name, flags=re.I).lower().strip()
    if not text:
        return 0.0
    best = 0.0
    for field in (entry.get("city", ""), entry.get("name", "")):
        field = field.lower()
        if not field:
            continue
        best = max(best, SequenceMatcher(None, text, field).ratio())
        if text in field or field in text:
            best = max(best, 0.9)
    return best


def _pick_code(votes, airports, ocr_name):
    """Wählt den besten Code: Stimmen + gültig in IATA-DB + passt zum Flughafennamen."""
    scores = Counter()
    for code, count in votes.items():
        if code in airports:
            scores[code] += count * 2 + 1
        else:  # OCR-Verwechslung? Nur Nachbarn nehmen, die auch zum Namen passen.
            for alt in _hamming1(code, airports):
                if _name_score(airports[alt], ocr_name) >= 0.6:
                    scores[alt] += count
    for code in list(scores):
        scores[code] += 6 * _name_score(airports[code], ocr_name)
    if not scores:
        return None
    return scores.most_common(1)[0][0]


def lese_flughafencodes(image, airports):
    origin = _pick_code(_code_candidates(image, CODE_ORIGIN_X), airports, _read_airport_name(image, "origin"))
    dest = _pick_code(_code_candidates(image, CODE_DEST_X), airports, _read_airport_name(image, "dest"))
    if not origin or not dest or origin == dest:
        raise ValueError(
            "Abflug- und Zielflughafen konnten nicht sicher erkannt werden "
            f"(Kandidaten: {origin or '—'}, {dest or '—'}). Bitte Screenshot prüfen."
        )
    return origin, dest


# ---------------------------------------------------------------- Kategorie
def lese_zielkategorie(image):
    """Findet den rechten weißen Kreis (Zielkategorie) und liest die blaue Ziffer darin."""
    rgb = np.asarray(_crop(image, CATEGORY_X[0], CATEGORY_BAND_Y[0], CATEGORY_X[1], CATEGORY_BAND_Y[1])).astype(int)
    white = (rgb.min(axis=2) > 215)
    cols = white.sum(axis=0) >= max(4, int(white.shape[0] * 0.10))
    runs = [r for r in _runs(cols) if r[1] - r[0] >= 6]
    if not runs:
        raise ValueError("Kategorie-Kreise nicht gefunden. Bitte vollständigen Screenshot hochladen.")
    x0, x1 = runs[-1]  # rechtester weißer Block = Zielkategorie
    rows = white[:, x0:x1].sum(axis=1) >= 2
    ry = _runs(rows)
    if not ry:
        raise ValueError("Kategorie-Kreis nicht gefunden.")
    y0, y1 = max(ry, key=lambda r: r[1] - r[0])
    circle = rgb[y0:y1, x0:x1]
    h, w = circle.shape[:2]
    # Nur Pixel innerhalb des Kreises zählen (Ecken des Ausschnitts enthalten Randreste)
    yy, xx = np.mgrid[0:h, 0:w]
    inside = ((xx - (w - 1) / 2) / (w * 0.40)) ** 2 + ((yy - (h - 1) / 2) / (h * 0.40)) ** 2 <= 1.0
    # Ziffer = dunkler/bläulicher Pixel innerhalb des weißen Kreises
    digit_mask = (circle.max(axis=2) < 175) & inside
    box = _tight_bbox(digit_mask, margin=1)
    if not box or digit_mask.sum() < 6:
        raise ValueError("Zielkategorie konnte nicht erkannt werden.")
    bx0, by0, bx1, by1 = box
    img = _mask_to_image(digit_mask[by0:by1, bx0:bx1], scale=10, pad=40)
    votes = Counter()
    for psm in (7, 8, 10, 13):
        text = re.sub(r"\D", "", _ocr(img, psm, "0123456789"))
        if text.isdigit() and 1 <= int(text) <= 10:
            votes[int(text)] += 1
    if not votes:
        raise ValueError("Zielkategorie konnte nicht erkannt werden.")
    return votes.most_common(1)[0][0]


# ---------------------------------------------------------------- Zahlen
def _main_text_rows(mask):
    """Behält nur die Zeilen des Haupttextblocks (entfernt Trennlinien/Randreste darüber/darunter)."""
    runs = _runs(mask.sum(axis=1) >= 2)
    if not runs:
        return mask
    y0, y1 = max(runs, key=lambda r: mask[r[0]:r[1]].sum())
    out = np.zeros_like(mask)
    out[y0:y1] = mask[y0:y1]
    return out


def _glyph_count(mask):
    """Zahl der Ziffern-Blöcke (Punkte/Kommas sind niedrig und zählen nicht)."""
    height = int(mask.any(axis=1).sum())
    count = 0
    for x0, x1 in _runs(mask.any(axis=0)):
        if mask[:, x0:x1].any(axis=1).sum() >= 0.6 * height:
            count += 1
    return count


def _dilate(mask):
    out = mask.copy()
    out[1:] |= mask[:-1]
    out[:-1] |= mask[1:]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


def _read_number(masks):
    """Liest eine Zahl; Tausenderpunkte werden ignoriert.

    `masks` ist eine Liste von Masken derselben Zahl, erzeugt mit verschiedenen
    Farbschwellen. Jede wird in mehreren Varianten gelesen (Zeichenhöhe,
    Segmentierung, leicht verdickt). Gegenprobe: Die Anzahl gelesener Ziffern
    muss zur Anzahl der Ziffern-Blöcke im Bild passen; die häufigste Antwort gewinnt.
    """
    if isinstance(masks, np.ndarray):
        masks = [masks]
    readings, expected = [], Counter()
    for mask in masks:
        mask = _main_text_rows(mask)
        box = _tight_bbox(mask, margin=2)
        if not box:
            continue
        x0, y0, x1, y1 = box
        base = mask[y0:y1, x0:x1]
        expected[_glyph_count(base)] += 1
        for variant in (base, _dilate(base)):
            for target_height in (40, 64):
                scale = max(1, round(target_height / variant.shape[0]))
                img = _mask_to_image(variant, scale=scale, pad=30)
                for psm in (7, 8):
                    digits = re.sub(r"\D", "", _ocr(img, psm, "0123456789."))
                    if digits:
                        readings.append(digits)
    if not readings:
        return None
    expected_len = expected.most_common(1)[0][0]
    matching = [d for d in readings if len(d) == expected_len]
    pool = matching or readings
    return int(Counter(pool).most_common(1)[0][0])


def lese_distanz(image):
    rgb = np.asarray(_crop(image, *DISTANCE_BOX)).astype(int)
    white = _main_text_rows(rgb.min(axis=2) > 200)
    white_loose = rgb.min(axis=2) > 165
    # Zahl ohne das nachgestellte "km": größte Lücke von rechts suchen
    cols = white.any(axis=0)
    runs = _runs(cols)
    if not runs:
        raise ValueError("Distanz nicht erkannt.")
    # Spalten zu Wörtern gruppieren (Lücke > ~0.6 Zeichenhöhe trennt "9706" und "km")
    height = max(1, int(white.any(axis=1).sum()))
    groups, cur = [], [runs[0]]
    for r in runs[1:]:
        if r[0] - cur[-1][1] > height * 0.45:
            groups.append(cur)
            cur = [r]
        else:
            cur.append(r)
    groups.append(cur)
    number_group = groups[0] if len(groups) >= 2 else groups[-1]
    x0, x1 = number_group[0][0], number_group[-1][1]
    x0, x1 = max(0, x0 - 2), min(white.shape[1], x1 + 2)  # Sicherheitsrand
    value = _read_number([white[:, x0:x1], white_loose[:, x0:x1]])
    if value is None:
        raise ValueError("Distanz nicht erkannt.")
    return value


def lese_nachfrage_zeile(image, key):
    """Eine Nachfragezeile. Grün = positiv, Rot = negativ (Vorzeichen aus der Farbe)."""
    cy = DEMAND_ROWS[key]
    if True:
        rgb = np.asarray(_crop(image, DEMAND_X[0], cy - ROW_HALF_HEIGHT, DEMAND_X[1], cy + ROW_HALF_HEIGHT)).astype(int)
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        green = (g > 170) & (r < 150) & (b < 120) & (g > r * 1.3)
        red = (r > 170) & (g < 110) & (b < 110)
        white = rgb.min(axis=2) > 225
        text = _main_text_rows(green | red | white)
        loose_green = (g > 140) & (r < 170) & (b < 150) & (g > r * 1.2)
        loose_red = (r > 150) & (g < 130) & (b < 130) & (r > g * 1.4)
        loose = loose_green | loose_red | (rgb.min(axis=2) > 200)
        if text.sum() < 15:
            raise ValueError(f"{key}-Nachfrage konnte nicht erkannt werden.")
        # "t" bei Fracht abtrennen: nur den ersten (linken) Wortblock = Zahl
        cols = text.any(axis=0)
        runs = _runs(cols)
        height = max(1, int(text.any(axis=1).sum()))
        groups, cur = [], [runs[0]]
        for run in runs[1:]:
            if run[0] - cur[-1][1] > height * 0.35:
                groups.append(cur)
                cur = [run]
            else:
                cur.append(run)
        groups.append(cur)
        if key == "freight" and len(groups) >= 2:
            groups = groups[:-1]  # das "t" entfernen
        x0, x1 = groups[0][0][0], groups[-1][-1][1]
        x0, x1 = max(0, x0 - 2), min(text.shape[1], x1 + 2)  # Sicherheitsrand: nichts abschneiden
        part = text[:, x0:x1]
        part_loose = loose[:, x0:x1]
        # Rotes Minus: im Rot-Anteil ist die Zahl oft von einem kurzen Strich begleitet -> Farbe zählen
        negative = red[:, x0:x1].sum() > green[:, x0:x1].sum() and red[:, x0:x1].sum() > 10
        value = _read_number([part, part_loose])
        if value is None:
            raise ValueError(f"{key}-Nachfrage konnte nicht erkannt werden.")
        return -value if negative and key != "freight" else value




# ---------------------------------------------------------------- Einstieg
def _erkenne_routen_felder(image: Image.Image, airports):
    image = image.convert("RGB")
    w, h = image.size
    if w < 900 or h < 400:
        raise ValueError("Das Bild ist zu klein. Bitte einen vollständigen Routendetails-Screenshot hochladen.")
    # Auf feste Breite normalisieren: Alle Pixel-Schwellen der Pipeline gelten dann
    # unabhängig von der Auflösung des hochgeladenen Screenshots.
    if w != NORMAL_WIDTH:
        image = image.resize((NORMAL_WIDTH, round(h * NORMAL_WIDTH / w)), Image.LANCZOS)

    # Die Felder sind voneinander unabhängig -> parallel lesen (Tesseract läuft als eigener Prozess).
    with ThreadPoolExecutor(max_workers=6) as pool:
        f_codes = pool.submit(lese_flughafencodes, image, airports)
        f_cat = pool.submit(lese_zielkategorie, image)
        f_dist = pool.submit(lese_distanz, image)
        f_rows = {key: pool.submit(lese_nachfrage_zeile, image, key) for key in DEMAND_ROWS}
        origin_code, destination_code = f_codes.result()
        return {
            "origin_code": origin_code,
            "destination_code": destination_code,
            "destination_category": f_cat.result(),
            "distance_km": f_dist.result(),
            "economy": f_rows["economy"].result(),
            "business": f_rows["business"].result(),
            "first": f_rows["first"].result(),
            "freight_tons": f_rows["freight"].result(),
        }

def erkenne_routen_screenshot(image_bytes):
    """Liest einen AM4-Routendetails-Screenshot aus und ergänzt Flughafen-Stammdaten."""
    image = Image.open(BytesIO(image_bytes))
    airports = lade_flughafen_datenbank()
    werte = _erkenne_routen_felder(image, airports)
    origin = airports[werte["origin_code"]]
    destination = airports[werte["destination_code"]]
    werte.update({
        "origin_city": origin.get("city") or origin.get("name") or werte["origin_code"],
        "destination_city": destination.get("city") or destination.get("name") or werte["destination_code"],
        "origin_name": origin.get("name") or werte["origin_code"],
        "destination_name": destination.get("name") or werte["destination_code"],
        "origin_country": origin.get("country", ""),
        "destination_country": destination.get("country", ""),
        "origin_category": None,  # nur die Zielkategorie ist für die Flugzeugauswahl relevant
    })
    return werte


@st.cache_data(show_spinner=False)
def finde_optionen(namen, gesamt_nachfrage, mindestdeckung=95):
    """Ermittelt alle gültigen Kombinationen mit Mindest-Nachfrageabdeckung."""
    gueltige = [f for f in FLUGZEUGE if f["name"] in namen]
    optionen = []

    if gesamt_nachfrage <= 0:
        return []

    mindest_sitze = gesamt_nachfrage * mindestdeckung / 100

    for r in range(1, 8):
        for kombi in combinations_with_replacement(gueltige, r):
            sitze = sum(f["sitze"] * 2 for f in kombi)

            if sitze < mindest_sitze:
                continue
            if sitze > gesamt_nachfrage + 50:
                continue

            preis = sum(f["preis"] for f in kombi)
            zaehlung = {}
            for f in kombi:
                zaehlung[f["name"]] = zaehlung.get(f["name"], 0) + 1

            teile = [f"{anzahl}x {name}" for name, anzahl in zaehlung.items()]

            optionen.append({
                "teile": teile,
                "raw_text": " + ".join(teile),
                "sitze": sitze,
                "preis": preis,
                "verbrauch": sum(f["verbrauch"] for f in kombi) / len(kombi),
                "abnutzung": sum(f["abnutzung"] for f in kombi) / len(kombi),
                "abweichung": abs(sitze - gesamt_nachfrage),
                "anzahl": len(kombi),
                "kosten_pro_sitz": preis / sitze,
                "auslastung": sitze / gesamt_nachfrage * 100,
            })

    return optionen


def berechne_scores(optionen, gewichte):
    """Berechnet sechs Einzel-Scores und daraus einen gewichteten Gesamtscore."""
    if not optionen:
        return

    min_p = min(o["preis"] for o in optionen)
    min_c = min(o["kosten_pro_sitz"] for o in optionen)
    min_n = min(o["anzahl"] for o in optionen)
    min_v = min(o["verbrauch"] for o in optionen)
    min_a = min(o["abnutzung"] for o in optionen)

    gewicht_summe = sum(gewichte.values()) or 1

    for o in optionen:
        o["achsen"] = [
            min(o["auslastung"], 100.0),
            100.0 * min_p / o["preis"],
            100.0 * min_c / o["kosten_pro_sitz"],
            100.0 * min_v / o["verbrauch"],
            100.0 * min_a / o["abnutzung"],
            100.0 * min_n / o["anzahl"],
        ]

        o["score"] = sum(
            achse * gewicht
            for achse, gewicht in zip(o["achsen"], gewichte.values())
        ) / gewicht_summe


AXIS_FORMATTERS = [
    lambda o: f"{o['sitze']} Sitze ({de_num(o['auslastung'], 1)}%)",
    lambda o: f"{de_num(o['preis'] / 1e6, 1)} Mio $",
    lambda o: f"{de_num(o['kosten_pro_sitz'] / 1e3)}k $",
    lambda o: f"{de_num(o['verbrauch'], 2)} L",
    lambda o: f"{de_num(o['abnutzung'], 2)} %",
    lambda o: f"{o['anzahl']} Stk.",
]


# ==========================================
# 3. DARSTELLUNG
# ==========================================
def card_html(o, i, rng, gesamt_nachfrage):
    accent = OPTION_COLORS[i]
    diff = gesamt_nachfrage - o["sitze"]
    if diff < 0:
        status, st_col = f"⚠️ Überschreitet um {abs(diff):.0f} Sitze", "#ef5350"
    else:
        status, st_col = f"Ungenutzt: {diff:.0f} Sitze", "inherit"

    metrics = [
        ("Gesamtkosten", fmt_money(o["preis"]), gradient_color(o["preis"], *rng["preis"])),
        ("Kosten pro Sitz", de_num(o["kosten_pro_sitz"], 2) + " $",
         gradient_color(o["kosten_pro_sitz"], *rng["cps"])),
        ("Abgedeckte Sitze", f"{o['sitze']} ({de_num(o['auslastung'], 1)}%)",
         gradient_color(o["abweichung"], *rng["dev"])),
        ("Ø Verbrauch", f"{de_num(o['verbrauch'], 2)} Liter", gradient_color(o["verbrauch"], *rng["v"])),
        ("Ø Abnutzung", f"{de_num(o['abnutzung'], 2)}% / 100h", gradient_color(o["abnutzung"], *rng["a"])),
        ("Status / Differenz", status, st_col),
        ("Gesamtscore", f"{o['score']:.1f} / 100",
         gradient_color(100 - o["score"], 0, 100)),
    ]
    parts = [
        f'<div style="border-top:4px solid {accent};padding-top:8px">',
        f'<div style="color:{accent};font-weight:700;font-size:1.1rem">Option {i + 1}'
        f'{"  ★ höchster Gesamtscore" if i == 0 else ""}</div>',
        f'<div style="font-weight:600;margin:6px 0 2px">{"<br>+ ".join(o["teile"])}</div>',
        f'<div style="opacity:.6;font-size:.8rem;margin-bottom:6px">{o["anzahl"]} Flugzeug(e)</div>',
    ]
    for label, value, color in metrics:
        parts.append(
            f'<div style="border-top:1px solid #2f3b4d;padding:6px 0">'
            f'<div style="opacity:.6;font-size:.75rem">{label}</div>'
            f'<div style="color:{color};font-weight:600">{value}</div></div>'
        )
    parts.append("</div>")
    return "".join(parts)


def radar_figure(top):
    fig = go.Figure()
    theta = RADAR_AXES + [RADAR_AXES[0]]
    for k, o in enumerate(top):
        col = OPTION_COLORS[k]
        raw = [fn(o) for fn in AXIS_FORMATTERS]
        fig.add_trace(go.Scatterpolar(
            r=o["achsen"] + [o["achsen"][0]],
            theta=theta,
            customdata=raw + [raw[0]],
            fill="toself",
            name=f"Option {k + 1}",
            line=dict(color=col, width=2),
            fillcolor=hex_to_rgba(col, 0.18),
            marker=dict(size=7),
            hovertemplate="<b>%{theta}</b><br>%{customdata}<br>Score: %{r:.0f}/100"
                          f"<extra>Option {k + 1}</extra>",
        ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(range=[0, 100], tickvals=[25, 50, 75, 100], gridcolor="#2a3545",
                            tickfont=dict(size=10, color="#8b98a9")),
            angularaxis=dict(gridcolor="#2a3545", tickfont=dict(size=13)),
        ),
        legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center"),
        margin=dict(l=60, r=60, t=30, b=40),
        height=460,
    )
    return fig


def score_figure(top):
    best = max(o["score"] for o in top)
    labels = [f"Option {k + 1}" for k in range(len(top))]
    fig = go.Figure(go.Bar(
        x=[o["score"] for o in top],
        y=labels,
        orientation="h",
        marker_color=OPTION_COLORS[:len(top)],
        text=[f"{o['score']:.1f}" + (" ★" if o["score"] == best else "") for o in top],
        textposition="outside",
        cliponaxis=False,
    ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(range=[0, 115], showgrid=False, visible=False),
        yaxis=dict(autorange="reversed"),
        margin=dict(l=10, r=30, t=10, b=10),
        height=40 + 55 * len(top),
    )
    return fig


def table_html(top):
    head = "".join(
        f'<th style="color:{OPTION_COLORS[k]};text-align:center;padding:6px">Option {k + 1}</th>'
        for k in range(len(top))
    )
    rows = []
    for i, name in enumerate(RADAR_AXES):
        best = max(o["achsen"][i] for o in top)
        cells = []
        for o in top:
            is_best = len(top) > 1 and o["achsen"][i] >= best - 1e-9
            style = "font-weight:700" if is_best else "opacity:.65"
            cells.append(f'<td style="text-align:center;padding:6px;{style}">'
                         f'{AXIS_FORMATTERS[i](o)}{" ★" if is_best else ""}</td>')
        rows.append(f'<tr style="border-top:1px solid #2a3545"><td style="padding:6px;opacity:.65">{name}</td>'
                    f'{"".join(cells)}</tr>')
    return ('<table style="width:100%;border-collapse:collapse;font-size:.85rem">'
            f'<tr><th></th>{head}</tr>{"".join(rows)}</table>'
            '<div style="text-align:right;opacity:.5;font-size:.7rem;margin-top:4px">'
            '★ = bester Wert in dieser Zeile</div>')


# ==========================================
# 4. APP
# ==========================================
st.set_page_config(page_title="Airline Tycoon – Routen-Planer", page_icon="✈️", layout="wide")

with st.sidebar:
    st.title("✈️ Routen-Planer")
    st.caption("Airline Manager Tycoon · AM4")

    st.subheader("🖼️ Route per Screenshot einlesen")
    route_image = st.file_uploader(
        "AM4-Routendetails hochladen",
        type=["png", "jpg", "jpeg", "webp"],
        key="route_screenshot",
        help="Lade den vollständigen Bildschirm mit Routendetails hoch.",
    )
    if route_image and st.button("Bild auslesen und Werte übernehmen", type="primary", use_container_width=True):
        try:
            erkannte_route = erkenne_routen_screenshot(route_image.getvalue())
            st.session_state["route_rw_input"] = erkannte_route["distance_km"]
            st.session_state["airport_kat_input"] = erkannte_route["destination_category"]
            st.session_state["eco_input"] = erkannte_route["economy"]
            st.session_state["biz_input"] = erkannte_route["business"]
            st.session_state["first_input"] = erkannte_route["first"]
            st.session_state["route_info"] = erkannte_route
            st.success("Screenshot erkannt. Werte wurden übernommen.")
        except Exception as exc:
            st.error(f"Erkennung nicht vollständig: {exc}")

    st.divider()
    st.markdown("**Routen- und Nachfragewerte**")
    st.session_state.setdefault("route_rw_input", 5000)
    st.session_state.setdefault("airport_kat_input", 10)
    st.session_state.setdefault("eco_input", 500)
    st.session_state.setdefault("biz_input", 100)
    st.session_state.setdefault("first_input", 20)

    route_rw = st.number_input("Reichweite (km)", min_value=0, step=100, key="route_rw_input")
    airport_kat = st.number_input("Max. Flughafen-Kat (1–10)", min_value=1, max_value=10, step=1, key="airport_kat_input")

    st.markdown("**Nachfrage**")
    raw_eco = st.number_input("Economy", min_value=-100000, step=1, key="eco_input")
    raw_biz = st.number_input("Business", min_value=-100000, step=1, key="biz_input")
    raw_first = st.number_input("First Class", min_value=-100000, step=1, key="first_input")

    # Positive und negative Nachfrage werden betragsmäßig auf gerade
    # Zahlen abgerundet; das Vorzeichen bleibt erhalten.
    eco, biz, first = (
        abrunden_gerade(raw_eco),
        abrunden_gerade(raw_biz),
        abrunden_gerade(raw_first),
    )

    ausgewaehlt = st.multiselect("Flugzeug-Verfügbarkeit", ALLE_NAMEN, default=ALLE_NAMEN)

    st.divider()
    st.subheader("⚖️ Scoring-Gewichtung")

    gewichte = {}
    for key, label in SCORING_LABELS.items():
        gewichte[key] = st.slider(
            label,
            min_value=0,
            max_value=100,
            value=SCORING_DEFAULTS[key],
            step=5,
            key=f"weight_{key}",
        )

    mindestdeckung = st.slider(
        "Mindest-Nachfrageabdeckung (%)",
        min_value=50,
        max_value=100,
        value=95,
        step=5,
    )

# >>> Nachfragewerte wurden oben auf gerade Zahlen abgerundet <<<
gesamt_nachfrage = eco * 1 + biz * 2 + first * 4

st.title("Vergleich & AM4-Auswertung")

route_info = st.session_state.get("route_info")
if route_info:
    st.markdown(
        f"### {land_flagge(route_info['origin_country'])} "
        f"{route_info['origin_city']} ({route_info['origin_code']})"
        f"  →  {land_flagge(route_info['destination_country'])} "
        f"{route_info['destination_city']} ({route_info['destination_code']})"
    )
    st.caption(
        f"{route_info['origin_name']} · Ziel-Kategorie "
        f"{route_info['destination_category']} · "
        f"{de_num(route_info['distance_km'])} km · Fracht: {route_info['freight_tons']} t"
    )

c1, c2, c3, c4 = st.columns(4)
c1.metric("Gesamtnachfrage", f"{gesamt_nachfrage} Sitze")
c2.metric("Economy (×1)", eco)
c3.metric("Business (×2)", biz)
c4.metric("First (×4)", first)

geaendert = [f"{n}: {r} → {u}" for n, r, u in
             (("Eco", raw_eco, eco), ("Biz", raw_biz, biz), ("First", raw_first, first)) if r != u]
if geaendert:
    st.info("Abgerundet auf gerade Zahl – " + "  |  ".join(geaendert))

gueltige_namen = tuple(
    f["name"] for f in FLUGZEUGE
    if f["reichweite"] >= route_rw and f["kategorie"] <= airport_kat and f["name"] in ausgewaehlt
)

if not gueltige_namen:
    st.error("⚠️ Kein Flugzeug erfüllt die Anforderungen oder alle wurden ausgeschlossen!")
    st.stop()

alle_optionen = finde_optionen(
    gueltige_namen,
    gesamt_nachfrage,
    mindestdeckung,
)

if not alle_optionen:
    st.error(
        "⚠️ Keine passenden Kombinationen gefunden. "
        "Bitte Mindestdeckung oder Flugzeugauswahl prüfen."
    )
    st.stop()

# Scores für alle gültigen Kombinationen berechnen
berechne_scores(alle_optionen, gewichte)

# Primär nach bestem Gesamtscore sortieren.
# Bei Gleichstand entscheiden Kosten pro Sitz, Nachfrageabweichung und Gesamtpreis.
alle_optionen.sort(
    key=lambda o: (
        -o["score"],
        o["kosten_pro_sitz"],
        o["abweichung"],
        o["preis"],
    )
)

top_3 = []
gesehen = set()
for option in alle_optionen:
    if option["raw_text"] not in gesehen:
        gesehen.add(option["raw_text"])
        top_3.append(option)
    if len(top_3) == 3:
        break


rng = {
    "preis": (min(o["preis"] for o in top_3), max(o["preis"] for o in top_3)),
    "cps": (min(o["kosten_pro_sitz"] for o in top_3), max(o["kosten_pro_sitz"] for o in top_3)),
    "dev": (min(o["abweichung"] for o in top_3), max(o["abweichung"] for o in top_3)),
    "v": (min(f["verbrauch"] for f in FLUGZEUGE), max(f["verbrauch"] for f in FLUGZEUGE)),
    "a": (min(f["abnutzung"] for f in FLUGZEUGE), max(f["abnutzung"] for f in FLUGZEUGE)),
}

st.subheader("Die besten Optionen")
cols = st.columns(len(top_3))
for i, (col, o) in enumerate(zip(cols, top_3)):
    with col:
        with st.container(border=True):
            st.markdown(card_html(o, i, rng, gesamt_nachfrage), unsafe_allow_html=True)

st.subheader("📊 Gesamtvergleich")
st.caption("Radar: je weiter außen, desto besser (100 = Bestwert der Auswahl). "
           "Mit der Maus über die Punkte fahren zeigt die echten Werte.")
left, right = st.columns([3, 2])
with left:
    st.plotly_chart(radar_figure(top_3))
with right:
    st.markdown("**Gesamtscore (Ø aller 6 Achsen)**")
    st.plotly_chart(score_figure(top_3))
    st.markdown(table_html(top_3), unsafe_allow_html=True)
