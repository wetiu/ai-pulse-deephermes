#!/usr/bin/env python3
"""Compose the AI Pulse QR card: dark cyberpunk frame + white scan panel + caption."""
from PIL import Image, ImageDraw, ImageFont

ROOT = "/home/wetiu/deephermes/projects/ai-pulse"
QR_PATH = f"{ROOT}/assets/qr-ai-pulse-plain.png"
OUT = f"{ROOT}/assets/qr-ai-pulse.png"
URL_DISPLAY = "wetiu.github.io/ai-pulse-deephermes"

F_BOLD = "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"
F_REG = "/usr/share/fonts/TTF/DejaVuSans.ttf"

BG = (13, 13, 22)          # #0d0d16
BORDER = (40, 40, 62)      # #28283e
TEXT_HI = (232, 232, 244)  # #e8e8f4
TEXT_LO = (150, 150, 175)  # #9696af
TEXT_DIM = (104, 104, 128) # #686880
NEON = [(108, 92, 231), (0, 210, 255), (255, 107, 157)]  # purple -> cyan -> pink

qr = Image.open(QR_PATH).convert("RGB")
QR_W, QR_H = qr.size

PAD = 56        # card side padding
PANEL_PAD = 20  # white panel padding around QR
BAR_H = 7
BAR_GAP = 26
GAP1, GAP2, GAP3 = 34, 10, 14
BOTTOM = 44

title_f = ImageFont.truetype(F_BOLD, 46)
url_f = ImageFont.truetype(F_REG, 27)
hint_f = ImageFont.truetype(F_REG, 21)

panel_w, panel_h = QR_W + PANEL_PAD * 2, QR_H + PANEL_PAD * 2
W = panel_w + PAD * 2

tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))

def th(text, font):
    l, t, r, b = font.getbbox(text)
    return b - t

TITLE = "AI Pulse"
HINT = "scan with your phone camera"
h_title, h_url, h_hint = th(TITLE, title_f), th(URL_DISPLAY, url_f), th(HINT, hint_f)

H = PAD + BAR_H + BAR_GAP + panel_h + GAP1 + h_title + GAP2 + h_url + GAP3 + h_hint + BOTTOM

card = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(card)
d.rounded_rectangle([0, 0, W - 1, H - 1], radius=30, outline=BORDER, width=2)

# 3-stop neon gradient bar
bar_w = panel_w
bar = Image.new("RGB", (bar_w, BAR_H))
for x in range(bar_w):
    t = x / (bar_w - 1)
    if t < 0.5:
        a, b, tt = NEON[0], NEON[1], t * 2
    else:
        a, b, tt = NEON[1], NEON[2], (t - 0.5) * 2
    col = tuple(round(a[i] + (b[i] - a[i]) * tt) for i in range(3))
    for y in range(BAR_H):
        bar.putpixel((x, y), col)
card.paste(bar, (PAD, PAD))

# white QR panel
py0 = PAD + BAR_H + BAR_GAP
d.rounded_rectangle([PAD, py0, PAD + panel_w, py0 + panel_h], radius=18, fill=(255, 255, 255))
card.paste(qr, (PAD + PANEL_PAD, py0 + PANEL_PAD))

def center_text(text, font, color, y):
    w = d.textlength(text, font=font)
    d.text(((W - w) / 2, y), text, font=font, fill=color)

cy = py0 + panel_h + GAP1
center_text(TITLE, title_f, TEXT_HI, cy)
cy += h_title + GAP2
center_text(URL_DISPLAY, url_f, TEXT_LO, cy)
cy += h_url + GAP3
center_text(HINT, hint_f, TEXT_DIM, cy)

card.save(OUT)
print("saved", OUT, card.size)
