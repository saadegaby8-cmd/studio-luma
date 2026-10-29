"""Filtros de fotografía que se aplican DESPUÉS de generar (sin IA, sin costo y sin filtros de contenido).

La foto generada sale demasiado limpia: aire transparente, negros profundos, piel y tela sin grano.
Una foto real de playa al atardecer tiene bruma en el aire, la luz se abre en las altas, la película
tiene grano, polvo y el halo rojizo alrededor de las luces. Pedírselo al generador con palabras no
alcanza (lo toma como sugerencia y a veces ensucia la prenda); acá se hace sobre la imagen ya hecha,
igual que en el revelado.

Sólo PIL (sin numpy): en Railway no está instalado.
"""
import random
from typing import Optional

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

FILTROS = {
    "": "Sin filtro",
    "bruma": "🌫 Bruma (aire con luz abierta)",
    "humo": "💨 Humo",
    "pelicula": "🎞 Película sucia (grano, polvo, halo)",
    "lente": "🔍 Lente sucio (manchas de luz)",
    "todo": "✨ Todo junto (humo + película sucia)",
}
FUERZAS = {"suave": 0.6, "media": 1.0, "fuerte": 1.5}


def _chico(img: Image.Image, lado: int = 512) -> Image.Image:
    W, H = img.size
    k = lado / max(W, H)
    return img.resize((max(8, int(W * k)), max(8, int(H * k))), Image.BILINEAR)


def _nube(W: int, H: int, escala: int, rnd: random.Random, contraste: float = 1.0) -> Image.Image:
    """Ruido suave de baja frecuencia (nubes/volutas), 0–255, del tamaño W×H."""
    w, h = max(4, W // escala), max(4, H // escala)
    n = Image.effect_noise((w, h), 70).filter(ImageFilter.GaussianBlur(1.2))
    n = ImageOps.autocontrast(n)
    if contraste != 1.0:
        n = ImageEnhance.Contrast(n).enhance(contraste)
    # desplazamiento al azar para que dos fotos no tengan el mismo humo
    n = n.transform(n.size, Image.AFFINE, (1, 0, rnd.uniform(-w / 4, w / 4), 0, 1, rnd.uniform(-h / 4, h / 4)),
                    resample=Image.BILINEAR)
    return n.resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(max(W, H) / 300))


def _resplandor(img: Image.Image, umbral: int, radio_rel: float) -> Image.Image:
    """Las luces altas desparramadas (bloom), calculado chico y agrandado."""
    c = _chico(img)
    luz = c.convert("L").point(lambda v: max(0, v - umbral) * (255 // max(1, 255 - umbral)))
    glow = Image.composite(c, Image.new("RGB", c.size), luz).filter(
        ImageFilter.GaussianBlur(max(c.size) * radio_rel))
    return glow.resize(img.size, Image.BICUBIC)


def _bruma(img: Image.Image, k: float, rnd: random.Random) -> Image.Image:
    W, H = img.size
    color = Image.new("RGB", (W, H), (236, 226, 214))
    grad = Image.linear_gradient("L").resize((W, H)).point(lambda v: 255 - v)     # más arriba que abajo
    mascara = ImageChops.add(grad.point(lambda v: v * 0.55), _nube(W, H, 40, rnd).point(lambda v: v * 0.45))
    mascara = mascara.point(lambda v: int(v * min(0.9, 0.24 * k)))
    out = Image.composite(color, img, mascara)
    out = ImageChops.screen(out, _resplandor(out, 170, 0.035).point(lambda v: int(v * min(1, 0.55 * k))))
    return ImageEnhance.Contrast(out).enhance(1 - 0.08 * k)


def _humo(img: Image.Image, k: float, rnd: random.Random) -> Image.Image:
    """Humo en el aire: volutas grandes y muy suaves, sumadas como luz (nunca manchas sobre la piel)."""
    W, H = img.size
    volutas = _nube(W, H, 9, rnd, contraste=1.15).filter(ImageFilter.GaussianBlur(max(W, H) / 120))
    grad = Image.linear_gradient("L").resize((W, H)).point(lambda v: 255 - v // 2)      # más arriba
    mascara = ImageChops.multiply(volutas, grad).point(lambda v: int(v * min(0.42, 0.26 * k)))
    capa = Image.composite(Image.new("RGB", (W, H), (205, 198, 190)), Image.new("RGB", (W, H)), mascara)
    out = ImageChops.screen(img, capa)
    out = ImageChops.screen(out, _resplandor(out, 185, 0.03).point(lambda v: int(v * min(1, 0.35 * k))))
    return ImageEnhance.Contrast(out).enhance(1 - 0.06 * k)


def _pelicula(img: Image.Image, k: float, rnd: random.Random) -> Image.Image:
    W, H = img.size
    out = img
    # halo rojizo alrededor de las luces (halation de la película)
    c = _chico(out)
    luces = c.convert("L").point(lambda v: 255 if v > 215 else 0).filter(ImageFilter.GaussianBlur(max(c.size) / 70))
    halo = Image.composite(Image.new("RGB", c.size, (255, 80, 30)), Image.new("RGB", c.size), luces)
    out = ImageChops.screen(out, halo.resize((W, H), Image.BICUBIC).point(lambda v: int(v * min(1, 0.45 * k))))
    # negros levantados y apenas cálidos (película expuesta, no digital)
    lev = int(16 * k)
    r, g, b = out.split()
    out = Image.merge("RGB", (r.point(lambda v: lev + v * (255 - lev) // 255),
                              g.point(lambda v: int(lev * 0.9) + v * (255 - int(lev * 0.9)) // 255),
                              b.point(lambda v: int(lev * 0.7) + v * (255 - int(lev * 0.7)) // 255)))
    # grano (de luminancia, apenas suavizado: el de película no es píxel a píxel)
    grano = Image.effect_noise((W, H), 11 * k).filter(ImageFilter.GaussianBlur(0.6))
    out = ImageChops.add(out, Image.merge("RGB", (grano, grano, grano)), 1.0, -128)
    # polvo y pelitos
    d = ImageDraw.Draw(out)
    lado = max(W, H)
    for _ in range(int(70 * k)):
        x, y, rr = rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(0.6, 2.2) * lado / 1500
        col = (250, 248, 240) if rnd.random() < 0.7 else (25, 22, 20)
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col)
    for _ in range(int(4 * k)):
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        pts = [(x, y)]
        for _ in range(6):
            x, y = x + rnd.uniform(-1, 1) * lado / 90, y + rnd.uniform(-1, 1) * lado / 90
            pts.append((x, y))
        d.line(pts, fill=(240, 238, 230), width=max(1, lado // 1600))
    # viñeta suave
    v = Image.radial_gradient("L").resize((W, H)).point(lambda p: int(min(255, p * 1.15) * min(0.6, 0.28 * k)))
    out = Image.composite(Image.new("RGB", (W, H)), out, v)
    return out


def _lente(img: Image.Image, k: float, rnd: random.Random) -> Image.Image:
    W, H = img.size
    c = _chico(img, 640)
    difuso = ImageEnhance.Brightness(c.filter(ImageFilter.GaussianBlur(max(c.size) / 60))).enhance(1.12)
    m = Image.new("L", c.size, 0)
    d = ImageDraw.Draw(m)
    for _ in range(int(5 + 4 * k)):
        x, y = rnd.uniform(0, c.size[0]), rnd.uniform(0, c.size[1])
        rr = rnd.uniform(0.06, 0.18) * max(c.size)
        d.ellipse([x - rr, y - rr * rnd.uniform(0.5, 1), x + rr, y + rr], fill=int(rnd.uniform(90, 200)))
    m = m.filter(ImageFilter.GaussianBlur(max(c.size) / 25)).point(lambda v: int(v * min(1, 0.6 * k)))
    out = Image.composite(difuso.resize((W, H), Image.BICUBIC), img, m.resize((W, H), Image.BICUBIC))
    return ImageChops.screen(out, _resplandor(out, 160, 0.05).point(lambda v: int(v * min(1, 0.5 * k))))


def aplicar(img: Image.Image, tipo: str, fuerza: str = "media", semilla: Optional[int] = None) -> Image.Image:
    """La imagen con el filtro. Tipo desconocido o vacío → la misma imagen."""
    tipo = str(tipo or "").strip()
    if tipo not in FILTROS or not tipo:
        return img
    k = FUERZAS.get(str(fuerza or "media"), 1.0)
    rnd = random.Random(semilla)
    modo = img.mode
    out = img.convert("RGB")
    if tipo == "bruma":
        out = _bruma(out, k, rnd)
    elif tipo == "humo":
        out = _humo(out, k, rnd)
    elif tipo == "pelicula":
        out = _pelicula(out, k, rnd)
    elif tipo == "lente":
        out = _lente(out, k, rnd)
    elif tipo == "todo":
        out = _pelicula(_humo(out, 0.8 * k, rnd), k, rnd)
    return out.convert(modo) if modo in ("RGB", "RGBA", "L") and modo != "RGB" else out
