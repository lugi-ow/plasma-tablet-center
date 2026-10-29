#!/usr/bin/env python3
# make-images.py OUT_DIR - drawn illustrations for plasma-tablet-center.
# Writes placement.svg: three panels of the cursor-stationary placement rule
# (TECHNICAL.md). A stylized 1280x720 desktop with the precision overlay
# (precision_image, the same geometry the scripts use) stays available for a
# drawn stand-in of the screenshot. It is not written. Offscreen Qt, no display.
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import (QColor, QFont, QGuiApplication, QImage,  # noqa: E402
                         QLinearGradient, QPainter, QPainterPath, QPen,
                         QRadialGradient)

W, H = 1280, 720
ASPECT = 224 / 148            # Intuos Pro M active area
SCALE = 0.29                  # the example size used everywhere in the docs
AMBER = QColor(0xe6, 0xa8, 0x17)
out_dir = sys.argv[1]
app = QGuiApplication(sys.argv)


def area(scale, norm, sw=W, sh=H):
    """Precision rectangle for a pen at normalized tablet position norm."""
    w = round(sw * scale)
    h = round(w / ASPECT)
    if h > sh:
        h = sh
        w = round(h * ASPECT)
    x = round(norm[0] * (sw - w))
    y = round(norm[1] * (sh - h))
    return x, y, w, h


def stroke(p, pts, color, width):
    path = QPainterPath(QPointF(*pts[0]))
    for i in range(1, len(pts) - 1, 2):
        path.quadTo(QPointF(*pts[i]), QPointF(*pts[i + 1]))
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.strokePath(path, pen)


def desktop(p):
    """Wallpaper, a painting window with a sketch, a bottom panel."""
    g = QLinearGradient(0, 0, W, H)
    g.setColorAt(0, QColor(0x1d, 0x2b, 0x3a))
    g.setColorAt(1, QColor(0x14, 0x3d, 0x46))
    p.fillRect(0, 0, W, H, g)
    r = QRadialGradient(QPointF(900, 120), 700)
    r.setColorAt(0, QColor(255, 255, 255, 40))
    r.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(0, 0, W, H, r)

    # window: title bar, left tools, right dockers, canvas
    wx, wy, ww, wh = 120, 48, 1040, 610
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0x2a, 0x2e, 0x33))
    p.drawRoundedRect(QRectF(wx, wy, ww, wh), 8, 8)
    p.setBrush(QColor(0x3a, 0x3f, 0x45))
    p.drawRoundedRect(QRectF(wx, wy, ww, 30), 8, 8)
    p.drawRect(QRectF(wx, wy + 16, ww, 14))
    for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        p.setBrush(QColor(c))
        p.drawEllipse(QPointF(wx + 18 + i * 20, wy + 15), 6, 6)
    p.setPen(QColor(0xcc, 0xcc, 0xcc))
    p.setFont(QFont("sans-serif", 10))
    p.drawText(QRectF(wx, wy, ww, 30), Qt.AlignmentFlag.AlignCenter, "sketch.kra")
    p.setPen(Qt.PenStyle.NoPen)
    tools_w, dock_w = 44, 190
    p.setBrush(QColor(0x23, 0x26, 0x2b))
    p.drawRect(QRectF(wx, wy + 30, tools_w, wh - 30))
    p.drawRect(QRectF(wx + ww - dock_w, wy + 30, dock_w, wh - 30))
    for i in range(11):
        p.setBrush(QColor(0x4a, 0x50, 0x58) if i != 2 else AMBER)
        p.drawRoundedRect(QRectF(wx + 10, wy + 44 + i * 34, 24, 24), 4, 4)
    for i in range(7):
        p.setBrush(QColor(0x30, 0x34, 0x3a) if i else QColor(0x44, 0x4a, 0x52))
        p.drawRoundedRect(QRectF(wx + ww - dock_w + 12, wy + 46 + i * 40, dock_w - 24, 30), 4, 4)
    cx, cy = wx + tools_w, wy + 30
    cw, ch = ww - tools_w - dock_w, wh - 30
    p.setBrush(QColor(0x4d, 0x52, 0x59))
    p.drawRect(QRectF(cx, cy, cw, ch))
    canvas = QRectF(cx + 60, cy + 40, cw - 120, ch - 80)
    p.setBrush(QColor(0xf4, 0xf0, 0xe8))
    p.drawRect(canvas)
    # the sketch: a leaf and a stem in graphite
    graphite = QColor(0x3b, 0x3f, 0x45)
    ox, oy = canvas.x() + 90, canvas.y() + 70
    stroke(p, [(ox + 20, oy + 420), (ox + 120, oy + 250), (ox + 230, oy + 60)], graphite, 3)
    stroke(p, [(ox + 230, oy + 60), (ox + 420, oy + 40), (ox + 470, oy + 200),
               (ox + 480, oy + 330), (ox + 300, oy + 330), (ox + 240, oy + 200),
               (ox + 230, oy + 60)], graphite, 2.5)
    stroke(p, [(ox + 230, oy + 60), (ox + 300, oy + 180), (ox + 400, oy + 300)], graphite, 1.6)
    for k in range(5):
        stroke(p, [(ox + 250 + k * 30, oy + 90 + k * 45), (ox + 300 + k * 30, oy + 110 + k * 45),
                   (ox + 340 + k * 28, oy + 140 + k * 40)], QColor(0x6b, 0x70, 0x76), 1.2)
    stroke(p, [(ox + 60, oy + 380), (ox + 110, oy + 330), (ox + 160, oy + 350)], QColor(0x9a, 0x9e, 0xa3), 1.2)

    # bottom panel
    p.setBrush(QColor(0x12, 0x14, 0x18, 220))
    p.drawRect(QRectF(0, H - 36, W, 36))
    for i, c in enumerate(("#3daee9", "#e6a817", "#8e44ad", "#27ae60", "#c0392b")):
        p.setBrush(QColor(c))
        p.drawRoundedRect(QRectF(14 + i * 40, H - 30, 24, 24), 5, 5)
    p.setPen(QColor(0xdd, 0xdd, 0xdd))
    p.setFont(QFont("sans-serif", 10))
    p.drawText(QRectF(W - 120, H - 36, 106, 36), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "14:28")
    p.setPen(Qt.PenStyle.NoPen)


def pen_cursor(p, x, y):
    """Brush-outline cursor a painting app shows under the pen."""
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(0, 0, 0, 200), 3))
    p.drawEllipse(QPointF(x, y), 13, 13)
    p.setPen(QPen(QColor(255, 255, 255), 1.5))
    p.drawEllipse(QPointF(x, y), 13, 13)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(QPointF(x, y), 2, 2)
    p.setBrush(QColor(0, 0, 0))
    p.drawEllipse(QPointF(x, y), 1, 1)


def new_image():
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    desktop(p)
    return img, p


def precision_image():
    img, p = new_image()
    norm = (0.66, 0.42)
    px, py = norm[0] * W, norm[1] * H
    x, y, w, h = area(SCALE, norm)
    dim = QColor(0, 0, 0, round(0.35 * 255))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(dim)
    p.drawRect(QRectF(0, 0, W, y))
    p.drawRect(QRectF(0, y + h, W, H - y - h))
    p.drawRect(QRectF(0, y, x, h))
    p.drawRect(QRectF(x + w, y, W - x - w, h))
    p.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(AMBER, 2)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    p.drawRect(QRectF(x + 1, y + 1, w - 2, h - 2))      # inset like the QML border
    pen_cursor(p, px, py)
    p.end()
    return img


def placement_svg():
    """Three panels: the same rule with the pen centred, off-centre, in a corner."""
    cases = [((0.5, 0.5), "pen at the centre (0.5, 0.5)"), ((0.8, 0.3), "pen at (0.8, 0.3)"),
             ((1.0, 1.0), "pen in the corner (1, 1)")]
    pw, total_h, top = 330, 226, 40
    tw, th = 72, round(72 / ASPECT)
    sw, sh = 192, 108
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{pw * 3}" height="{total_h}" '
             f'viewBox="0 0 {pw * 3} {total_h}" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="13">',
             f'<rect width="{pw * 3}" height="{total_h}" rx="10" fill="#ffffff"/>',
             '<defs><marker id="a" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto">'
             '<path d="M0 0 L8 4 L0 8 z" fill="#9ca3af"/></marker></defs>']
    for i, (norm, title) in enumerate(cases):
        ox = i * pw + 20
        parts.append(f'<text x="{ox}" y="24" fill="#111827" font-weight="600">{title}</text>')
        # tablet with the pen's normalized position
        tx, ty = ox, top + (sh - th) / 2
        parts.append(f'<rect x="{tx}" y="{ty:.1f}" width="{tw}" height="{th}" rx="5" fill="#2e3440" stroke="#4c566a"/>')
        parts.append(f'<rect x="{tx + 6}" y="{ty + 6:.1f}" width="{tw - 12}" height="{th - 12}" rx="3" fill="none" stroke="#4c566a" stroke-dasharray="3 3"/>')
        pxt, pyt = tx + 6 + norm[0] * (tw - 12), ty + 6 + norm[1] * (th - 12)
        parts.append(f'<circle cx="{pxt:.1f}" cy="{pyt:.1f}" r="3.5" fill="#e6a817"/>')
        parts.append(f'<text x="{tx}" y="{top + sh + 18}" fill="#4b5563">tablet</text>')
        parts.append(f'<path d="M{tx + tw + 8} {top + sh / 2} h 20" stroke="#9ca3af" stroke-width="2" fill="none" marker-end="url(#a)"/>')
        # screen: dimmed, with the clear precision area and the cursor
        sx, sy = ox + tw + 36, top
        x, y, w, h = area(SCALE, norm, sw, sh)
        parts.append(f'<rect x="{sx}" y="{sy}" width="{sw}" height="{sh}" fill="#b9c1c9" stroke="#7a8794"/>')
        parts.append(f'<rect x="{sx + x}" y="{sy + y}" width="{w}" height="{h}" fill="#eef2f6"/>')
        parts.append(f'<rect x="{sx + x + 1}" y="{sy + y + 1}" width="{w - 2}" height="{h - 2}" fill="none" stroke="#e6a817" stroke-width="2"/>')
        cxp = min(sx + norm[0] * sw, sx + sw - 1)
        cyp = min(sy + norm[1] * sh, sy + sh - 1)
        parts.append(f'<circle cx="{cxp:.1f}" cy="{cyp:.1f}" r="4.5" fill="none" stroke="#111827" stroke-width="2"/>')
        parts.append(f'<circle cx="{cxp:.1f}" cy="{cyp:.1f}" r="1.5" fill="#111827"/>')
        parts.append(f'<text x="{sx}" y="{top + sh + 18}" fill="#4b5563">screen</text>')
    parts.append(f'<text x="{pw * 3 / 2}" y="{total_h - 14}" text-anchor="middle" fill="#374151">'
                 'area origin = norm × (screen − area)  ·  cursor = norm × screen  ·  '
                 'the area always holds the cursor and never leaves the screen</text>')
    parts.append("</svg>")
    return "\n".join(parts)


os.makedirs(out_dir, exist_ok=True)
# precision-mode.png is a real screenshot now (docs/screenshot-precision-mode.png).
# The drawn version stays available: precision_image().save(...)
with open(os.path.join(out_dir, "placement.svg"), "w", encoding="utf-8") as f:
    f.write(placement_svg())
print("wrote", sorted(os.listdir(out_dir)))
