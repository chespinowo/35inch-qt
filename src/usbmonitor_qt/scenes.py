"""Cenas: o que é desenhado no display. Cada cena renderiza um QImage e traz seu próprio painel de ajustes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from .protocol import Orientation
from .sysinfo import SystemStats, fmt_gb, fmt_rate, fmt_uptime
from .widgets import ColorButton

# ---------------------------------------------------------------------------
# paleta e helpers de desenho
# ---------------------------------------------------------------------------
BG = QColor("#0b0e13")
CARD = QColor("#151a22")
CARD_BORDER = QColor("#232b37")
TRACK = QColor("#252d3a")
TEXT = QColor("#e8eef5")
MUTED = QColor("#8593a5")
WARN = QColor("#fbbf24")
CRIT = QColor("#f87171")
GOOD = QColor("#34d399")

_A = Qt.AlignmentFlag
LEFT = (_A.AlignLeft | _A.AlignVCenter).value
RIGHT = (_A.AlignRight | _A.AlignVCenter).value
CENTER = _A.AlignCenter.value
WRAP = Qt.TextFlag.TextWordWrap.value

WEEKDAYS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
WEEKDAYS_SHORT = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


@dataclass
class RenderContext:
    stats: SystemStats
    now: datetime
    orientation: Orientation


def new_canvas(size: QSize, bg: QColor = BG) -> tuple[QImage, QPainter]:
    img = QImage(size, QImage.Format.Format_RGB32)
    img.fill(bg)
    p = QPainter(img)
    p.setRenderHints(
        QPainter.RenderHint.Antialiasing
        | QPainter.RenderHint.TextAntialiasing
        | QPainter.RenderHint.SmoothPixmapTransform
    )
    return img, p


def draw_text(p: QPainter, rect: QRectF, s: str, px: float, color: QColor, bold: bool = False, align: int = CENTER) -> None:
    font = QFont(p.font())
    font.setPixelSize(max(6, int(round(px))))
    font.setBold(bold)
    p.setFont(font)
    p.setPen(color)
    p.drawText(rect, align, s)


def fit_px(s: str, max_w: float, max_h: float, bold: bool = True, lo: int = 8, hi: int = 400) -> int:
    """Maior tamanho de fonte (px) em que `s` cabe em max_w × max_h."""
    font = QFont()
    font.setBold(bold)
    best = lo
    while lo <= hi:
        mid = (lo + hi) // 2
        font.setPixelSize(mid)
        fm = QFontMetricsF(font)
        if fm.horizontalAdvance(s) <= max_w and fm.height() <= max_h:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def elide(p: QPainter, s: str, px: float, bold: bool, width: float) -> str:
    font = QFont(p.font())
    font.setPixelSize(int(px))
    font.setBold(bold)
    return QFontMetricsF(font).elidedText(s, Qt.TextElideMode.ElideRight, width)


def level_color(pct: float, accent: QColor, warn: float = 70, crit: float = 90) -> QColor:
    if pct >= crit:
        return CRIT
    if pct >= warn:
        return WARN
    return accent


def draw_card(p: QPainter, r: QRectF) -> None:
    p.setPen(QPen(CARD_BORDER, 1))
    p.setBrush(CARD)
    p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


# ---------------------------------------------------------------------------
# base
# ---------------------------------------------------------------------------
class Scene(QObject):
    changed = Signal()
    key = ""
    title = ""
    DEFAULTS: dict = {}

    def __init__(self) -> None:
        super().__init__()
        self.cfg: dict = dict(self.DEFAULTS)

    # persistência
    def load(self, data: dict) -> None:
        for k, default in self.DEFAULTS.items():
            if k in data and isinstance(data[k], type(default)):
                self.cfg[k] = data[k]

    def dump(self) -> dict:
        return dict(self.cfg)

    def set(self, key: str, value) -> None:
        self.cfg[key] = value
        self.changed.emit()

    # a implementar
    def render(self, size: QSize, ctx: RenderContext) -> QImage:
        raise NotImplementedError

    def build_panel(self) -> QWidget:
        raise NotImplementedError

    # construtores de controles ligados a self.cfg
    def _form(self) -> tuple[QWidget, QFormLayout]:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        return w, form

    def _check(self, form: QFormLayout, label: str, key: str) -> QCheckBox:
        box = QCheckBox(label)
        box.setChecked(self.cfg[key])
        box.toggled.connect(lambda v, k=key: self.set(k, bool(v)))
        form.addRow(box)
        return box

    def _color(self, form: QFormLayout, label: str, key: str) -> ColorButton:
        btn = ColorButton(self.cfg[key])
        btn.colorChanged.connect(lambda c, k=key: self.set(k, c.name()))
        form.addRow(label, btn)
        return btn

    def _combo(self, form: QFormLayout, label: str, key: str, options: list[tuple[str, str]]) -> QComboBox:
        combo = QComboBox()
        for value, text in options:
            combo.addItem(text, value)
        combo.setCurrentIndex(max(0, combo.findData(self.cfg[key])))
        combo.currentIndexChanged.connect(lambda _i, c=combo, k=key: self.set(k, c.currentData()))
        form.addRow(label, combo)
        return combo

    def _spin(self, form: QFormLayout, label: str, key: str, lo: int, hi: int, suffix: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setSuffix(suffix)
        spin.setValue(self.cfg[key])
        spin.valueChanged.connect(lambda v, k=key: self.set(k, int(v)))
        form.addRow(label, spin)
        return spin


# ---------------------------------------------------------------------------
# painel de sistema
# ---------------------------------------------------------------------------
def dashboard_layout(w: float, h: float, names: list[str]) -> dict[str, QRectF]:
    margin, gap, header = 10.0, 8.0, 56.0
    area = QRectF(margin, header, w - 2 * margin, h - header - margin)
    rings = [n for n in ("cpu", "ram") if n in names]
    wides = [n for n in ("temp", "disk", "net") if n in names]
    out: dict[str, QRectF] = {}

    def stack(names_: list[str], rect: QRectF) -> None:
        each = (rect.height() - gap * (len(names_) - 1)) / len(names_)
        for i, n in enumerate(names_):
            out[n] = QRectF(rect.left(), rect.top() + i * (each + gap), rect.width(), each)

    def row(names_: list[str], rect: QRectF) -> None:
        each = (rect.width() - gap * (len(names_) - 1)) / len(names_)
        for i, n in enumerate(names_):
            out[n] = QRectF(rect.left() + i * (each + gap), rect.top(), each, rect.height())

    if h >= w:  # retrato: anéis em cima, cartões largos empilhados embaixo
        if rings and wides:
            ring_h = area.height() * 0.40
            row(rings, QRectF(area.left(), area.top(), area.width(), ring_h))
            stack(wides, QRectF(area.left(), area.top() + ring_h + gap, area.width(), area.height() - ring_h - gap))
        elif rings:
            row(rings, area)
        elif wides:
            stack(wides, area)
    else:  # paisagem: anéis à esquerda, cartões largos à direita
        if rings and wides:
            left_w = area.width() * (0.5 if len(rings) == 2 else 0.34)
            row(rings, QRectF(area.left(), area.top(), left_w, area.height()))
            stack(wides, QRectF(area.left() + left_w + gap, area.top(), area.width() - left_w - gap, area.height()))
        elif rings:
            row(rings, area)
        elif wides:
            stack(wides, area)
    return out


class DashboardScene(Scene):
    key = "dashboard"
    title = "Painel"
    DEFAULTS = dict(accent="#38bdf8", cpu=True, ram=True, temp=True, disk=True, net=True, uptime=True, h24=True)

    def render(self, size: QSize, ctx: RenderContext) -> QImage:
        img, p = new_canvas(size)
        w, h = size.width(), size.height()
        accent = QColor(self.cfg["accent"])
        s = ctx.stats
        self._header(p, w, accent, ctx)
        names = [n for n in ("cpu", "ram", "temp", "disk", "net") if self.cfg[n]]
        for name, rect in dashboard_layout(w, h, names).items():
            getattr(self, f"_draw_{name}")(p, rect, accent, s)
        p.end()
        return img

    # -- cabeçalho --
    def _header(self, p: QPainter, w: int, accent: QColor, ctx: RenderContext) -> None:
        s, now = ctx.stats, ctx.now
        left_w = w * 0.52
        name = elide(p, s.hostname, 17, True, left_w)
        draw_text(p, QRectF(14, 8, left_w, 24), name, 17, TEXT, True, LEFT)
        if self.cfg["uptime"]:
            draw_text(p, QRectF(14, 31, left_w, 16), f"ligado há {fmt_uptime(s.uptime)}", 11, MUTED, False, LEFT)
        fmt = "%H:%M" if self.cfg["h24"] else "%I:%M"
        clock = now.strftime(fmt).lstrip("0") if not self.cfg["h24"] else now.strftime(fmt)
        draw_text(p, QRectF(w * 0.45, 4, w * 0.55 - 14, 34), clock, 28, TEXT, True, RIGHT)
        date = f"{WEEKDAYS_SHORT[now.weekday()]}, {now.day} {MONTHS[now.month - 1]}"
        draw_text(p, QRectF(w * 0.45, 33, w * 0.55 - 14, 16), date, 11, MUTED, False, RIGHT)

    # -- anéis --
    def _ring(self, p: QPainter, r: QRectF, title: str, pct: float, color: QColor, big: str, small: str) -> None:
        draw_card(p, r)
        draw_text(p, QRectF(r.left() + 12, r.top() + 8, r.width() - 24, 16), title, 11, MUTED, True, LEFT)
        area = QRectF(r.left() + 8, r.top() + 26, r.width() - 16, r.height() - 34)
        side = max(20.0, min(area.width(), area.height()))
        ring = QRectF(area.center().x() - side / 2, area.center().y() - side / 2, side, side)
        thick = max(7.0, side * 0.11)
        arc = ring.adjusted(thick / 2, thick / 2, -thick / 2, -thick / 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(TRACK, thick)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(arc, 225 * 16, -270 * 16)
        if pct > 0.5:
            pen.setColor(color)
            p.setPen(pen)
            p.drawArc(arc, 225 * 16, int(-270 * 16 * min(pct, 100) / 100))
        draw_text(p, QRectF(ring.left(), ring.center().y() - side * 0.22, side, side * 0.3), big, side * 0.25, TEXT, True)
        if small:
            draw_text(p, QRectF(ring.left(), ring.center().y() + side * 0.08, side, side * 0.16), small, max(9, side * 0.1), MUTED)

    def _draw_cpu(self, p: QPainter, r: QRectF, accent: QColor, s: SystemStats) -> None:
        sub = f"{s.cpu_freq_ghz:.1f} GHz" if s.cpu_freq_ghz else f"{s.cpu_cores} núcleos"
        self._ring(p, r, "CPU", s.cpu, level_color(s.cpu, accent), f"{s.cpu:.0f}%", sub)

    def _draw_ram(self, p: QPainter, r: QRectF, accent: QColor, s: SystemStats) -> None:
        sub = f"{fmt_gb(s.ram_used)}/{fmt_gb(s.ram_total)} GB"
        self._ring(p, r, "MEMÓRIA", s.ram_pct, level_color(s.ram_pct, accent), f"{s.ram_pct:.0f}%", sub)

    # -- cartões largos --
    def _bar_card(self, p: QPainter, r: QRectF, title: str, value: str, pct: float, color: QColor, sub: str = "") -> None:
        draw_card(p, r)
        pad = 12
        draw_text(p, QRectF(r.left() + pad, r.top() + 8, r.width() / 2, 16), title, 11, MUTED, True, LEFT)
        draw_text(p, QRectF(r.center().x() - 20, r.top() + 5, r.width() / 2 + 20 - pad, 22), value, 16, TEXT, True, RIGHT)
        bar_h = 8.0
        track = QRectF(r.left() + pad, r.bottom() - pad - bar_h, r.width() - 2 * pad, bar_h)
        if sub and r.height() >= 62:
            draw_text(p, QRectF(r.left() + pad, track.top() - 20, r.width() - 2 * pad, 16), sub, 11, MUTED, False, LEFT)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(TRACK)
        p.drawRoundedRect(track, 4, 4)
        if pct > 0:
            fill = QRectF(track.left(), track.top(), max(bar_h, track.width() * min(pct, 100) / 100), bar_h)
            grad = QLinearGradient(fill.topLeft(), fill.topRight())
            grad.setColorAt(0, color.darker(150))
            grad.setColorAt(1, color)
            p.setBrush(grad)
            p.drawRoundedRect(fill, 4, 4)

    def _draw_temp(self, p: QPainter, r: QRectF, accent: QColor, s: SystemStats) -> None:
        if s.temp is None:
            self._bar_card(p, r, "TEMPERATURA", "—", 0, accent, "sem sensor disponível")
            return
        self._bar_card(p, r, "TEMPERATURA", f"{s.temp:.0f} °C", s.temp, level_color(s.temp, accent, 65, 82), "CPU")

    def _draw_disk(self, p: QPainter, r: QRectF, accent: QColor, s: SystemStats) -> None:
        sub = f"{fmt_gb(s.disk_used)} / {fmt_gb(s.disk_total)} GB"
        self._bar_card(p, r, "DISCO", f"{s.disk_pct:.0f}%", s.disk_pct, level_color(s.disk_pct, accent, 80, 92), sub)

    def _draw_net(self, p: QPainter, r: QRectF, accent: QColor, s: SystemStats) -> None:
        draw_card(p, r)
        pad = 12
        draw_text(p, QRectF(r.left() + pad, r.top() + 8, r.width() / 2, 16), "REDE", 11, MUTED, True, LEFT)
        row_h = min(22.0, (r.height() - 32) / 2)
        for i, (rate, color, down) in enumerate(((s.net_down, accent, True), (s.net_up, GOOD, False))):
            cy = r.top() + 32 + row_h * (i + 0.5)
            tri = QPolygonF(
                [QPointF(r.left() + pad, cy - 4), QPointF(r.left() + pad + 9, cy - 4), QPointF(r.left() + pad + 4.5, cy + 4)]
                if down
                else [QPointF(r.left() + pad, cy + 4), QPointF(r.left() + pad + 9, cy + 4), QPointF(r.left() + pad + 4.5, cy - 4)]
            )
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawPolygon(tri)
            draw_text(p, QRectF(r.left() + pad + 16, cy - row_h / 2, r.width() * 0.46, row_h), fmt_rate(rate), 14, TEXT, True, LEFT)
        spark = QRectF(r.left() + r.width() * 0.52, r.top() + 12, r.width() * 0.48 - pad, r.height() - 24)
        self._sparkline(p, spark, list(s.down_hist), accent)

    @staticmethod
    def _sparkline(p: QPainter, r: QRectF, values: list[float], color: QColor) -> None:
        peak = max(max(values), 256 * 1024)
        step = r.width() / max(1, len(values) - 1)
        pts = [QPointF(r.left() + i * step, r.bottom() - r.height() * (v / peak)) for i, v in enumerate(values)]
        area = QPainterPath(QPointF(r.left(), r.bottom()))
        for pt in pts:
            area.lineTo(pt)
        area.lineTo(QPointF(r.right(), r.bottom()))
        area.closeSubpath()
        grad = QLinearGradient(r.topLeft(), r.bottomLeft())
        top = QColor(color)
        top.setAlpha(110)
        bottom = QColor(color)
        bottom.setAlpha(0)
        grad.setColorAt(0, top)
        grad.setColorAt(1, bottom)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawPath(area)
        line = QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(color, 1.6))
        p.drawPath(line)

    def build_panel(self) -> QWidget:
        w, form = self._form()
        self._color(form, "Cor de destaque", "accent")
        for label, key in (
            ("Processador (CPU)", "cpu"),
            ("Memória (RAM)", "ram"),
            ("Temperatura", "temp"),
            ("Disco", "disk"),
            ("Rede", "net"),
            ("Tempo ligado", "uptime"),
            ("Relógio de 24 horas", "h24"),
        ):
            self._check(form, label, key)
        return w


# ---------------------------------------------------------------------------
# relógio
# ---------------------------------------------------------------------------
class ClockScene(Scene):
    key = "clock"
    title = "Relógio"
    DEFAULTS = dict(accent="#38bdf8", h24=True, seconds=True, date=True)

    def render(self, size: QSize, ctx: RenderContext) -> QImage:
        img, p = new_canvas(size)
        w, h = size.width(), size.height()
        now, accent = ctx.now, QColor(self.cfg["accent"])
        text = now.strftime("%H:%M" if self.cfg["h24"] else "%I:%M")
        if not self.cfg["h24"]:
            text = text.lstrip("0")
        px = fit_px(text, w * 0.9, h * 0.36)
        cy = h * (0.44 if self.cfg["date"] else 0.5)
        draw_text(p, QRectF(0, cy - px * 0.62, w, px * 1.24), text, px, TEXT, True)
        y = cy + px * 0.72
        if not self.cfg["h24"]:
            draw_text(p, QRectF(0, y - px * 0.1, w, px * 0.3), "PM" if now.hour >= 12 else "AM", px * 0.22, MUTED, True)
            y += px * 0.28
        if self.cfg["seconds"]:
            bar = QRectF(w * 0.15, y, w * 0.7, 6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(TRACK)
            p.drawRoundedRect(bar, 3, 3)
            frac = (now.second + now.microsecond / 1e6) / 60
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(6.0, bar.width() * frac), 6), 3, 3)
            draw_text(p, QRectF(0, y + 12, w, 24), f"{now.second:02d} s", 15, MUTED)
            y += 40
        if self.cfg["date"]:
            date = f"{WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} {now.year}"
            dpx = fit_px(date, w * 0.9, 30, True, hi=22)
            draw_text(p, QRectF(0, y + 14, w, dpx * 1.5), date, dpx, accent, True)
        p.end()
        return img

    def build_panel(self) -> QWidget:
        w, form = self._form()
        self._color(form, "Cor de destaque", "accent")
        self._check(form, "Relógio de 24 horas", "h24")
        self._check(form, "Mostrar segundos", "seconds")
        self._check(form, "Mostrar data", "date")
        return w


# ---------------------------------------------------------------------------
# imagem
# ---------------------------------------------------------------------------
class ImageScene(Scene):
    key = "image"
    title = "Imagem"
    DEFAULTS = dict(path="", fit="fit", bg="#000000")

    def __init__(self) -> None:
        super().__init__()
        self._cache: tuple[str, float, QImage] | None = None

    def _load(self) -> QImage | None:
        path = self.cfg["path"]
        if not path or not os.path.isfile(path):
            return None
        mtime = os.path.getmtime(path)
        if self._cache and self._cache[0] == path and self._cache[1] == mtime:
            return self._cache[2]
        img = QImage(path)
        if img.isNull():
            return None
        self._cache = (path, mtime, img)
        return img

    def render(self, size: QSize, ctx: RenderContext) -> QImage:
        img, p = new_canvas(size, QColor(self.cfg["bg"]))
        src = self._load()
        w, h = size.width(), size.height()
        if src is None:
            msg = "Escolha uma imagem" if not self.cfg["path"] else "Não foi possível abrir a imagem"
            draw_text(p, QRectF(0, 0, w, h), msg, 16, MUTED)
        else:
            mode = {
                "fit": Qt.AspectRatioMode.KeepAspectRatio,
                "fill": Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                "stretch": Qt.AspectRatioMode.IgnoreAspectRatio,
            }[self.cfg["fit"]]
            scaled = src.scaled(size, mode, Qt.TransformationMode.SmoothTransformation)
            p.drawImage(QPointF((w - scaled.width()) / 2, (h - scaled.height()) / 2), scaled)
        p.end()
        return img

    def build_panel(self) -> QWidget:
        w, form = self._form()
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(self.cfg["path"])
        edit.setPlaceholderText("Nenhuma imagem selecionada")
        edit.setReadOnly(True)
        browse = QPushButton("Escolher…")

        def pick() -> None:
            path, _ = QFileDialog.getOpenFileName(
                w.window(), "Escolher imagem", os.path.dirname(self.cfg["path"]) or os.path.expanduser("~"),
                "Imagens (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;Todos os arquivos (*)",
            )
            if path:
                edit.setText(path)
                self.set("path", path)

        browse.clicked.connect(pick)
        lay.addWidget(edit, 1)
        lay.addWidget(browse)
        form.addRow("Arquivo", row)
        self._combo(form, "Ajuste", "fit", [("fit", "Caber (mantém proporção)"), ("fill", "Preencher (corta as bordas)"), ("stretch", "Esticar")])
        self._color(form, "Cor de fundo", "bg")
        return w


# ---------------------------------------------------------------------------
# texto
# ---------------------------------------------------------------------------
class TextScene(Scene):
    key = "text"
    title = "Texto"
    DEFAULTS = dict(text="Olá, mundo!", color="#e8eef5", bg="#0b0e13", size=36, align="center", bold=True)

    def render(self, size: QSize, ctx: RenderContext) -> QImage:
        img, p = new_canvas(size, QColor(self.cfg["bg"]))
        w, h = size.width(), size.height()
        font = QFont(p.font())
        font.setPixelSize(int(self.cfg["size"]))
        font.setBold(self.cfg["bold"])
        p.setFont(font)
        p.setPen(QColor(self.cfg["color"]))
        align = {"left": _A.AlignLeft, "center": _A.AlignHCenter, "right": _A.AlignRight}[self.cfg["align"]].value
        box = QRectF(16, 16, w - 32, h - 32)
        flags = align | WRAP
        used = p.boundingRect(box, flags, self.cfg["text"])
        box.moveTop(max(16.0, (h - used.height()) / 2))
        p.drawText(box, flags, self.cfg["text"])
        p.end()
        return img

    def build_panel(self) -> QWidget:
        w, form = self._form()
        edit = QPlainTextEdit(self.cfg["text"])
        edit.setFixedHeight(96)
        edit.textChanged.connect(lambda: self.set("text", edit.toPlainText()))
        form.addRow("Texto", edit)
        self._spin(form, "Tamanho", "size", 8, 200, " px")
        self._combo(form, "Alinhamento", "align", [("left", "Esquerda"), ("center", "Centro"), ("right", "Direita")])
        self._check(form, "Negrito", "bold")
        self._color(form, "Cor do texto", "color")
        self._color(form, "Cor de fundo", "bg")
        return w


# ---------------------------------------------------------------------------
# padrões de teste
# ---------------------------------------------------------------------------
class TestPatternScene(Scene):
    __test__ = False  # evita que o pytest tente coletar esta classe
    key = "test"
    title = "Teste"
    DEFAULTS = dict(pattern="orientation")

    def render(self, size: QSize, ctx: RenderContext) -> QImage:
        img, p = new_canvas(size, QColor("#000000"))
        p.setRenderHints(QPainter.RenderHint.Antialiasing, self.cfg["pattern"] == "orientation")
        w, h = size.width(), size.height()
        getattr(self, f"_pat_{self.cfg['pattern']}")(p, w, h)
        p.end()
        return img

    @staticmethod
    def _pat_bars(p: QPainter, w: int, h: int) -> None:
        colors = ["#ffffff", "#ffff00", "#00ffff", "#00ff00", "#ff00ff", "#ff0000", "#0000ff", "#000000"]
        bar_w = w / len(colors)
        for i, c in enumerate(colors):
            p.fillRect(QRectF(i * bar_w, 0, bar_w + 1, h * 0.75), QColor(c))
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0, QColor("#000000"))
        grad.setColorAt(1, QColor("#ffffff"))
        p.fillRect(QRectF(0, h * 0.75, w, h * 0.25), grad)

    @staticmethod
    def _pat_gradient(p: QPainter, w: int, h: int) -> None:
        for i, c in enumerate(("#ff0000", "#00ff00", "#0000ff", "#ffffff")):
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0, QColor("#000000"))
            grad.setColorAt(1, QColor(c))
            p.fillRect(QRectF(0, i * h / 4, w, h / 4), grad)

    @staticmethod
    def _pat_grid(p: QPainter, w: int, h: int) -> None:
        p.setPen(QPen(QColor("#3a475a"), 1))
        for x in range(0, w + 1, 20):
            p.drawLine(x, 0, x, h)
        for y in range(0, h + 1, 20):
            p.drawLine(0, y, w, y)
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawLine(w // 2, 0, w // 2, h)
        p.drawLine(0, h // 2, w, h // 2)
        p.drawRect(0, 0, w - 1, h - 1)

    @staticmethod
    def _pat_orientation(p: QPainter, w: int, h: int) -> None:
        sz = 56
        for (x, y), c, label in (
            ((0, 0), "#ef4444", "A"),
            ((w - sz, 0), "#22c55e", "B"),
            ((0, h - sz), "#3b82f6", "C"),
            ((w - sz, h - sz), "#eab308", "D"),
        ):
            p.fillRect(QRectF(x, y, sz, sz), QColor(c))
            draw_text(p, QRectF(x, y, sz, sz), label, 30, QColor("#000000"), True)
        draw_text(p, QRectF(0, h * 0.30, w, 40), "▲ TOPO", 28, TEXT, True)
        draw_text(p, QRectF(0, h * 0.46, w, 30), f"{w} × {h}", 22, QColor("#38bdf8"), True)
        draw_text(p, QRectF(16, h * 0.56, w - 32, 50), "A vermelho no canto superior esquerdo?", 14, MUTED, False, CENTER | WRAP)

    def build_panel(self) -> QWidget:
        w, form = self._form()
        self._combo(
            form, "Padrão", "pattern",
            [("orientation", "Orientação (cantos A B C D)"), ("bars", "Barras de cor"), ("gradient", "Degradês R G B"), ("grid", "Grade")],
        )
        return w


SCENE_CLASSES = [DashboardScene, ClockScene, ImageScene, TextScene, TestPatternScene]
