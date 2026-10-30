from PySide6.QtGui import QColor, QImage

from usbmonitor_qt.postfx import apply_effects, warm_tint


def solid(color: str, w: int = 8, h: int = 6) -> QImage:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    return img


def two_tone() -> QImage:
    """Esquerda vermelha, direita azul; em cima verde, embaixo amarelo no canto esquerdo."""
    img = solid("#0000ff", 10, 8)
    for x in range(5):
        for y in range(8):
            img.setPixelColor(x, y, QColor("#ff0000"))
    img.setPixelColor(0, 0, QColor("#00ff00"))
    return img


def test_warm_tint_scale():
    assert warm_tint(0) == QColor("#ffffff")
    mid, full = warm_tint(50), warm_tint(100)
    assert full.blue() < mid.blue() < 255
    assert full.green() < mid.green() < 255
    assert full.red() == 255  # só o verde e o azul diminuem: a imagem fica âmbar
    assert warm_tint(-20) == warm_tint(0) and warm_tint(500) == warm_tint(100)


def test_no_effect_returns_the_same_image_without_copying():
    img = solid("#123456")
    assert apply_effects(img) is img


def test_mirror_horizontal_and_vertical():
    img = two_tone()
    h = apply_effects(img, mirror_h=True)
    assert h.pixelColor(9, 0) == QColor("#00ff00") and h.pixelColor(0, 0) == QColor("#0000ff")
    v = apply_effects(img, mirror_v=True)
    assert v.pixelColor(0, 7) == QColor("#00ff00") and v.pixelColor(0, 0) == QColor("#ff0000")
    both = apply_effects(img, mirror_h=True, mirror_v=True)
    assert both.pixelColor(9, 7) == QColor("#00ff00")


def test_warmth_multiplies_the_image():
    white = apply_effects(solid("#ffffff"), warmth=100).pixelColor(3, 3)
    assert white == warm_tint(100)
    assert apply_effects(solid("#000000"), warmth=100).pixelColor(3, 3) == QColor("#000000")
    half = apply_effects(solid("#808080"), warmth=100).pixelColor(3, 3)
    assert half.red() == 0x80 and half.blue() < half.green() < half.red()


def test_warmth_does_not_modify_the_original():
    img = solid("#ffffff")
    apply_effects(img, warmth=100)
    assert img.pixelColor(0, 0) == QColor("#ffffff")


def test_effects_work_on_other_pixel_formats():
    img = solid("#ffffff").convertToFormat(QImage.Format.Format_RGB16)
    assert apply_effects(img, warmth=100).pixelColor(1, 1).blue() < 255
