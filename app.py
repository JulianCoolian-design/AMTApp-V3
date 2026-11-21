import math
import re
from io import BytesIO
from itertools import combinations_with_replacement

import airportsdata
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


def _ocr_bildausschnitt(image, box, whitelist=None):
    """Mehrstufige OCR für einen proportionalen Ausschnitt des AM4-Layouts."""
    width, height = image.size
    x1, y1, x2, y2 = box
    crop = image.crop((
        int(width * x1), int(height * y1),
        int(width * x2), int(height * y2),
    ))
    gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
    gray = gray.resize((max(1, gray.width * 4), max(1, gray.height * 4)))

    # Mehrere Bildvarianten und Segmentierungen fangen kleine UI-Schrift ab.
    results = []
    for variant in (gray, gray.point(lambda p: 255 if p > 155 else 0)):
        for psm in (6, 11):
            config = f"--oem 3 --psm {psm}"
            if whitelist:
                config += f" -c tessedit_char_whitelist={whitelist}"
            text = pytesseract.image_to_string(
                variant, lang="eng+deu", config=config
            ).strip()
            if text and text not in results:
                results.append(text)
    return "\n".join(results)



def _ocr_nachfrage(image):
    """Liest Nachfragewerte inklusive roter, negativer Zahlen und Minuszeichen."""
    width, height = image.size
    crop = image.crop((
        int(width * 0.58), int(height * 0.32),
        int(width * 0.87), int(height * 0.54),
    )).convert("RGB")

    # Rote AM4-Zahlen werden gezielt aufgehellt, damit sie bei der
    # Graustufenumwandlung nicht mit dem dunklen Hintergrund verschmelzen.
    pixels = []
    for red, green, blue in crop.getdata():
        if red > 70 and red > green * 1.25 and red > blue * 1.15:
            pixels.append((255, 255, 255))
        else:
            gray = int(0.299 * red + 0.587 * green + 0.114 * blue)
            pixels.append((gray, gray, gray))
    enhanced = Image.new("RGB", crop.size)
    enhanced.putdata(pixels)
    enhanced = ImageOps.autocontrast(ImageOps.grayscale(enhanced))
    enhanced = enhanced.resize((enhanced.width * 5, enhanced.height * 5))

    results = []
    for variant in (
        enhanced,
        enhanced.point(lambda p: 255 if p > 110 else 0),
        enhanced.point(lambda p: 255 if p > 145 else 0),
    ):
        for psm in (6, 11, 12):
            text = pytesseract.image_to_string(
                variant, lang="eng",
                config=(
                    f"--oem 3 --psm {psm} "
                    "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-−:., "
                ),
            ).strip()
            if text and text not in results:
                results.append(text)
    return "\\n".join(results)


def _ocr_flughafenkategorien(image, fallback_text=""):
    """Erkennt die rechte Flughafenkategorie; nur sie ist für die Auswahl relevant."""
    width, height = image.size

    def read_category(box):
        x1, y1, x2, y2 = box
        crop = image.crop((
            int(width * x1), int(height * y1),
            int(width * x2), int(height * y2),
        ))
        gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
        gray = gray.resize((gray.width * 8, gray.height * 8))
        variants = [
            gray,
            gray.point(lambda p: 255 if p > 125 else 0),
            gray.point(lambda p: 0 if p > 125 else 255),
        ]
        for variant in variants:
            for psm in (10, 13, 8):
                text = pytesseract.image_to_string(
                    variant, lang="eng",
                    config=(
                        f"--oem 3 --psm {psm} "
                        "-c tessedit_char_whitelist=0123456789"
                    ),
                ).strip()
                match = re.search(r"10|[1-9]", text)
                if match:
                    value = int(match.group(0))
                    if 1 <= value <= 10:
                        return value
        return None

    # Linke Zahl ist nur Zusatzinformation. Ausschlaggebend ist die rechte
    # Zahl hinter dem Pfeil (z.B. 1 -> 9 bedeutet Kategorie 9).
    origin_category = read_category((0.480, 0.290, 0.535, 0.410))
    destination_category = read_category((0.515, 0.290, 0.570, 0.410))

    # Zusätzlicher, unabhängiger Versuch ausschließlich für die zweistellige
    # Kategorie 10. Die bisherige Erkennung oben bleibt unverändert.
    if destination_category is None:
        ten_crop = image.crop((
            int(width * 0.505), int(height * 0.275),
            int(width * 0.590), int(height * 0.425),
        ))
        ten_gray = ImageOps.autocontrast(ImageOps.grayscale(ten_crop))
        ten_gray = ten_gray.resize((ten_gray.width * 10, ten_gray.height * 10))
        ten_variants = (
            ten_gray,
            ten_gray.point(lambda p: 255 if p > 110 else 0),
            ten_gray.point(lambda p: 255 if p > 145 else 0),
            ten_gray.point(lambda p: 0 if p > 135 else 255),
        )
        for variant in ten_variants:
            for psm in (7, 8, 13):
                ten_text = pytesseract.image_to_string(
                    variant,
                    lang="eng",
                    config=(
                        f"--oem 3 --psm {psm} "
                        "-c tessedit_char_whitelist=0123456789"
                    ),
                ).strip()
                if re.search(r"(?<!\\d)10(?!\\d)", ten_text):
                    destination_category = 10
                    break
            if destination_category == 10:
                break

    if destination_category is None:
        raise ValueError(
            "Die relevante Flughafenkategorie (rechte Zahl) konnte nicht erkannt werden. "
            f"(OCR links: {origin_category or '—'}, rechts: —). Bitte Screenshot prüfen."
        )

    return origin_category, destination_category


def _ocr_flughafencodes(image, airports):
    """Liest die beiden Codes aus den festen, schmalen Codefeldern der Kopfzeile."""
    # Im AM4-Routendetails-Screenshot stehen die Codes direkt neben den Länderflaggen.
    # Die Positionen wurden anhand eines echten 2556x1179-Screenshots kalibriert
    # und skalieren über relative Koordinaten mit anderen Auflösungen.
    origin = _ocr_flughafencode(image, (0.455, 0.145, 0.495, 0.225))
    destination = _ocr_flughafencode(image, (0.495, 0.145, 0.535, 0.225))

    if origin in airports and destination in airports and origin != destination:
        return origin, destination

    # Bei leicht abweichender Skalierung/Position zusätzlich die ganze obere
    # Kopfzeile als Rückfallebene prüfen.
    width, height = image.size
    header = image.crop((
        int(width * 0.08), int(height * 0.13),
        int(width * 0.92), int(height * 0.24),
    ))
    gray = ImageOps.autocontrast(ImageOps.grayscale(header))
    gray = gray.resize((gray.width * 3, gray.height * 3))
    candidates = {}
    for psm in (7, 11):
        data = pytesseract.image_to_data(
            gray, lang="eng",
            config=f"--oem 3 --psm {psm} -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            output_type=pytesseract.Output.DICT,
        )
        for i, raw in enumerate(data.get("text", [])):
            token = re.sub(r"[^A-Z]", "", raw.upper())
            if len(token) == 3 and token in airports:
                x_center = int(data["left"][i]) + int(data["width"][i]) / 2
                candidates[token] = x_center

    ordered = sorted(candidates.items(), key=lambda item: item[1])
    if len(ordered) >= 2:
        return ordered[0][0], ordered[-1][0]

    raise ValueError(
        "Abflug- und Zielflughafen konnten nicht sicher erkannt werden "
        f"(OCR-Kandidaten: {origin or '—'}, {destination or '—'}). "
        "Bitte Screenshot prüfen."
    )

def _ocr_flughafencode(image, box):
    """Liest einen einzelnen IATA-Code neben der Länderflagge."""
    width, height = image.size
    x1, y1, x2, y2 = box
    crop = image.crop((
        int(width * x1), int(height * y1),
        int(width * x2), int(height * y2),
    ))
    crop = ImageOps.autocontrast(ImageOps.grayscale(crop))
    crop = crop.resize((crop.width * 8, crop.height * 8))

    # PSM 10 (ein Zeichenwort) erkennt FRA zuverlässig trotz Flagge links;
    # PSM 7/6 helfen bei Codes ohne angrenzende Flagge wie VCP.
    for psm in (10, 7, 6):
        result = pytesseract.image_to_string(
            crop,
            lang="eng",
            config=(
                f"--oem 3 --psm {psm} "
                "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            ),
        ).strip().upper()
        match = re.search(r"[A-Z]{3}", result)
        if match:
            return match.group(0)
    return ""

def erkenne_routen_screenshot(image_bytes):
    """
    Liest einen AM4-Routendetails-Screenshot aus.
    Die Ausschnitte sind auf das feste Layout bezogen und skalieren
    automatisch mit der Auflösung des hochgeladenen Bildes.
    """
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    width, height = image.size

    if width < 900 or height < 400:
        raise ValueError("Das Bild ist zu klein. Bitte einen vollständigen Routendetails-Screenshot hochladen.")

    # Flughafencodes einzeln aus ihren festen Positionen lesen.
    # Das ist zuverlässiger als OCR über die gesamte, grafisch überlagerte Kopfzeile.
    header = _ocr_bildausschnitt(image, (0.13, 0.16, 0.88, 0.255))
    category_text = _ocr_bildausschnitt(
        image, (0.05, 0.27, 0.95, 0.43), "0123456789KategorieCategory "
    )
    distance_text = _ocr_bildausschnitt(image, (0.13, 0.375, 0.57, 0.43), "0123456789.kmKM ")
    demand_text = _ocr_nachfrage(image)

    airports = lade_flughafen_datenbank()

    def finde_code(ocr_text, position):
        """Löst häufige OCR-Verwechslungen auf und prüft den Code gegen die IATA-Datenbank."""
        kandidaten = re.findall(r"[A-Z]{3}", ocr_text.upper())
        if not kandidaten:
            raise ValueError(
                f"Der {position}flughafen-Code konnte nicht gelesen werden. "
                f"OCR-Ausgabe: {ocr_text.strip() or '(leer)'}"
            )

        code = kandidaten[0]
        # Häufige Verwechslungen bei der verwendeten AM4-Schrift.
        korrekturen = {
            "MPN": "MPM",  # M wird im Screenshot gelegentlich als N gelesen
            "TEU": "TFU",  # F wird gelegentlich als E gelesen
        }
        code = korrekturen.get(code, code)

        if code not in airports:
            raise ValueError(
                f"Der erkannte {position}flughafen-Code '{code}' ist nicht in der "
                "IATA-Datenbank vorhanden. Bitte den Screenshot prüfen."
            )
        return code

    # Beide Codes gemeinsam auslesen: getrennte Mittel-Ausschnitte
    # überlappten sich und ließen den Zielflughafen häufig leer.
    origin_code, destination_code = _ocr_flughafencodes(image, airports)
    origin = airports[origin_code]
    destination = airports[destination_code]

    origin_category, destination_category = _ocr_flughafenkategorien(image, category_text)

    distance_match = re.search(r"(\d[\d.,]*)\s*km", distance_text, re.IGNORECASE)
    if not distance_match:
        distance_match = re.search(r"\d[\d.,]{2,}", distance_text)
    if not distance_match:
        raise ValueError(f"Distanz nicht erkannt. Gelesener Text: {distance_text.strip()}")
    distance_km = int(re.sub(r"\D", "", distance_match.group(0)))

    def demand_value(label):
        # Minuszeichen können je nach OCR als Bindestrich oder Unicode-Minus
        # erscheinen; beide Varianten werden als negatives Vorzeichen gewertet.
        match = re.search(
            rf"{label}\s*[:：]?\s*([−-]?\s*[\d][\d.,]*)",
            demand_text,
            re.IGNORECASE,
        )
        if not match:
            raise ValueError(
                f"{label}-Nachfrage konnte nicht erkannt werden. "
                f"Gelesener Nachfragebereich: {demand_text.strip() or '(leer)'}"
            )
        raw = match.group(1).replace("−", "-").replace(" ", "")
        sign = -1 if raw.startswith("-") else 1
        digits = re.sub(r"\D", "", raw)
        return sign * int(digits)

    return {
        "origin_code": origin_code,
        "destination_code": destination_code,
        "origin_city": origin.get("city") or origin.get("name") or origin_code,
        "destination_city": destination.get("city") or destination.get("name") or destination_code,
        "origin_name": origin.get("name") or origin_code,
        "destination_name": destination.get("name") or destination_code,
        "origin_country": origin.get("country", ""),
        "destination_country": destination.get("country", ""),
        "origin_category": origin_category,
        "destination_category": destination_category,
        "distance_km": distance_km,
        "economy": demand_value("Economy"),
        "business": demand_value("Business"),
        "first": demand_value("First"),
        "freight_tons": demand_value("Fracht"),
    }


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
        f"{route_info['origin_name']} · Kategorie "
        f"{route_info['origin_category']} → {route_info['destination_category']} · "
        f"{route_info['distance_km']:,} km · Fracht: {route_info['freight_tons']} t"
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
