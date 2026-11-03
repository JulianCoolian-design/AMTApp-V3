import math
from itertools import combinations_with_replacement

import plotly.graph_objects as go
import streamlit as st

# ==========================================
# 1. DATENBANK
# ==========================================
FLUGZEUGE = [
    {"name": "Il-96-300", "preis": 110000000, "reichweite": 11500, "sitze": 300, "verbrauch": 4.42, "abnutzung": 2.4,
     "kategorie": 6},
    {"name": "A321XLR", "preis": 159895000, "reichweite": 8500, "sitze": 244, "verbrauch": 1.75, "abnutzung": 1.2,
     "kategorie": 5},
    {"name": "A321neo", "preis": 127000000, "reichweite": 6644, "sitze": 244, "verbrauch": 1.89, "abnutzung": 1.4,
     "kategorie": 5},
    {"name": "A350-1000", "preis": 335000000, "reichweite": 14750, "sitze": 522, "verbrauch": 2.06, "abnutzung": 1.4,
     "kategorie": 8},
    {"name": "A380-800", "preis": 403000000, "reichweite": 15556, "sitze": 853, "verbrauch": 2.44, "abnutzung": 1.7,
     "kategorie": 8},
    {"name": "737-900ER", "preis": 94600000, "reichweite": 6050, "sitze": 220, "verbrauch": 2.23, "abnutzung": 1.8,
     "kategorie": 6},
    {"name": "787-10", "preis": 257000000, "reichweite": 10067, "sitze": 440, "verbrauch": 2.07, "abnutzung": 1.4,
     "kategorie": 7},
    {"name": "787-8", "preis": 206800000, "reichweite": 15196, "sitze": 381, "verbrauch": 2.23, "abnutzung": 1.6,
     "kategorie": 8},
    {"name": "747-300", "preis": 272500000, "reichweite": 12400, "sitze": 608, "verbrauch": 2.43, "abnutzung": 2.6,
     "kategorie": 4},
    {"name": "747-8I", "preis": 352800000, "reichweite": 14825, "sitze": 730, "verbrauch": 2.24, "abnutzung": 1.6,
     "kategorie": 9},
]
ALLE_NAMEN = [f["name"] for f in FLUGZEUGE]

OPTION_COLORS = ["#4fc3f7", "#ba68c8", "#ffb74d"]
RADAR_AXES = ["Deckung", "Gesamtkosten", "Kosten/Sitz", "Verbrauch", "Abnutzung", "Flottengröße"]


# ==========================================
# 2. HILFSFUNKTIONEN
# ==========================================
def abrunden_gerade(x):
    """Rundet auf die nächste gerade ganze Zahl ab (501 -> 500, 99.9 -> 98, -3 -> 0)."""
    return max(0, int(x // 2) * 2)


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


@st.cache_data(show_spinner=False)
def finde_optionen(namen, gesamt_nachfrage):
    """Testet alle Kombinationen (1-7 Flugzeuge) und liefert die 3 besten, eindeutigen Optionen."""
    gueltige = [f for f in FLUGZEUGE if f["name"] in namen]
    optionen = []
    for r in range(1, 8):
        for kombi in combinations_with_replacement(gueltige, r):
            sitze = sum(f["sitze"] * 2 for f in kombi)
            if sitze > gesamt_nachfrage + 10:
                continue
            preis = sum(f["preis"] for f in kombi)
            zaehlung = {}
            for f in kombi:
                zaehlung[f["name"]] = zaehlung.get(f["name"], 0) + 1
            teile = [f"{n}x {name}" for name, n in zaehlung.items()]
            optionen.append({
                "teile": teile,
                "raw_text": " + ".join(teile),
                "sitze": sitze,
                "preis": preis,
                "verbrauch": sum(f["verbrauch"] for f in kombi) / len(kombi),
                "abnutzung": sum(f["abnutzung"] for f in kombi) / len(kombi),
                "abweichung": abs(sitze - gesamt_nachfrage),
                "anzahl": len(kombi),
                "kosten_pro_sitz": preis / sitze if sitze else 0,
                "auslastung": (sitze / gesamt_nachfrage * 100) if gesamt_nachfrage else 0,
            })
    optionen.sort(key=lambda o: (o["abweichung"], o["preis"]))

    top, gesehen = [], set()
    for o in optionen:
        if o["raw_text"] not in gesehen:
            gesehen.add(o["raw_text"])
            top.append(o)
            if len(top) == 3:
                break
    return top


def berechne_scores(top):
    """Normiert jede Kennzahl auf 0-100 (100 = bester Wert) und bildet den Gesamtscore."""
    min_p = min(o["preis"] for o in top)
    min_c = min(o["kosten_pro_sitz"] for o in top)
    min_n = min(o["anzahl"] for o in top)
    min_v = min(f["verbrauch"] for f in FLUGZEUGE)
    min_a = min(f["abnutzung"] for f in FLUGZEUGE)
    for o in top:
        o["achsen"] = [
            min(o["auslastung"], 100.0),
            100.0 * min_p / o["preis"],
            100.0 * min_c / o["kosten_pro_sitz"],
            100.0 * min_v / o["verbrauch"],
            100.0 * min_a / o["abnutzung"],
            100.0 * min_n / o["anzahl"],
        ]
        o["score"] = sum(o["achsen"]) / len(o["achsen"])


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
    ]
    parts = [
        f'<div style="border-top:4px solid {accent};padding-top:8px">',
        f'<div style="color:{accent};font-weight:700;font-size:1.1rem">Option {i + 1}'
        f'{"  ★ beste Deckung" if i == 0 else ""}</div>',
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

    route_rw = st.number_input("Reichweite (km)", min_value=0, value=5000, step=100)
    airport_kat = st.number_input("Max. Flughafen-Kat (1–10)", min_value=1, max_value=10, value=10, step=1)

    st.markdown("**Nachfrage**")
    raw_eco = st.number_input("Economy", min_value=0, value=500, step=1)
    raw_biz = st.number_input("Business", min_value=0, value=100, step=1)
    raw_first = st.number_input("First Class", min_value=0, value=20, step=1)

    eco, biz, first = abrunden_gerade(raw_eco), abrunden_gerade(raw_biz), abrunden_gerade(raw_first)
    st.caption(f"Verwendet (abgerundet auf gerade Zahl): Eco **{eco}** · Biz **{biz}** · First **{first}**")

    ausgewaehlt = st.multiselect("Flugzeug-Verfügbarkeit", ALLE_NAMEN, default=ALLE_NAMEN)

# >>> Eingaben wurden oben auf gerade Zahl abgerundet, erst danach wird gerechnet <<<
gesamt_nachfrage = eco * 1 + biz * 2 + first * 4

st.title("Vergleich & AM4-Auswertung")

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

top_3 = finde_optionen(gueltige_namen, gesamt_nachfrage)
if not top_3:
    st.error("⚠️ Keine passenden Kombinationen im Rahmen der Toleranz gefunden.")
    st.stop()

berechne_scores(top_3)

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
