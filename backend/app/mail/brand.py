"""Website brand and operator facts, not deployment configuration.

Mail uses the product's role addresses; the website's general company address
is maintained separately in website/src/content/operator.ts.
"""

OPERATOR = "fogu UG (haftungsbeschränkt)"
ADDRESS = "Im Großenbruch 34, 66583 Spiesen-Elversberg"
CONTACT = "hallo@verwalino.de"
PRIVACY_CONTACT = "datenschutz@verwalino.de"
IMPRINT_URL = "https://verwalino.de/impressum/"
PRIVACY_URL = "https://verwalino.de/datenschutz/"

LIGHT = {
    "outer": "#F3F4F2", "card": "#FFFFFF", "border": "#D2D6D0",
    "text": "#232724", "secondary": "#555F57", "band": "#17211B",
    "band_text": "#EFF1E9", "accent": "#B62930", "button_text": "#FFFFFF",
    "note": "#E7EBE4", "note_border": "#7E8980",
}
DARK = {
    "outer": "#1C2420", "card": "#26312A", "border": "#435047",
    "text": "#EFF1E9", "secondary": "#B4BFB4", "band": "#111A15",
    "band_text": "#EFF1E9", "accent": "#F18280", "button_text": "#1C2420",
    "note": "#2C382F", "note_border": "#7E8980",
}
BODY_FONT = "'IBM Plex Sans', Arial, Helvetica, sans-serif"
MONO_FONT = "'IBM Plex Mono', Consolas, 'Courier New', monospace"
