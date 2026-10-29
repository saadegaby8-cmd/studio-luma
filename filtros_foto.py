"""Filtros de fotografía que se aplican DESPUÉS de generar (sin IA, sin costo y sin filtros de contenido).

La foto generada sale demasiado limpia. Lo que le falta son cosas DISTINTAS, y por eso van por separado
y se combinan (no es lo mismo una cosa que la otra):

- ATMÓSFERA, lo que hay en el aire entre la cámara y el fondo:
    · neblina de mañana: pareja, fría, más densa hacia el horizonte; lo lejano se lava y pierde contraste.
    · bruma de ola (rocío de mar): la nube de gotitas que levanta una ola grande, de abajo y de los costados,
      despareja, a contraluz se enciende con el sol y tiene gotitas que brillan.
    · humo: volutas grises que se estiran y se cortan; no es pareja como la neblina.
- GRANO: el de la película o el sensor, en todo el cuadro (fino, medio, grueso). No es suciedad.
- SUCIEDAD: lo que está SOBRE la película o el lente: polvo, pelitos, rayitas, manchas de dedo.
- REVELADO DE PELÍCULA: halo rojizo alrededor de las luces y negros lavados, cálidos.

Pedírselo al generador con palabras no alcanza (lo toma como sugerencia y a veces ensucia la prenda); acá
se hace sobre la imagen ya hecha, igual que en el revelado. Sólo PIL (sin numpy: en Railway no está).
"""
import math
import random
from typing import Optional

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

ATMOSFERAS = {
    "": "Sin atmósfera",
    "neblina": "🌫 Neblina de mañana",
    "ola": "🌊 Bruma de ola (rocío de mar)",
    "humo": "💨 Humo",
}
GRANOS = {"": "Sin grano", "fino": "Grano fino", "medio": "Grano medio", "grueso": "Grano grueso"}
SUCIEDADES = {
    "": "Sin suciedad",
    "polvo": "Polvo y pelitos",
    "rayas": "Polvo y rayitas (película gastada)",
    "lente": "Lente sucio (manchas de dedo)",
}
PELICULAS = {"": "Sin revelado", "pelicula": "Revelado de película (halo y negros lavados)"}
FUERZAS = {"suave": 0.6, "media": 1.0, "fuerte": 1.5}

# Lo de la primera versión (un solo selector) se traduce a las capas nuevas.
_VIEJOS = {"bruma": {"atm": "neblina"}, "humo": {"atm": "humo"},
           "pelicula": {"grano": "medio", "sucio": "polvo", "pelicula": "pelicula"},
           "lente": {"sucio": "lente"}, "todo": {"atm": "humo", "grano": "medio", "sucio": "polvo", "pelicula": "pelicula"}}


def _chico(img: Image.Image, lado: int = 512) -> Image.Image:
    W, H = img.size
    k = lado / max(W, H)
    return img.resize((max(8, int(W * k)), max(8, int(H * k))), Image.BILINEAR)


def _nube(W: int, H: int, escala: int, rnd: random.Random, contraste: float = 1.0,
          estirar: float = 1.0) -> Image.Image:
    """Ruido suave de baja frecuencia (0–255) del tamaño W×H. `estirar` > 1 lo alarga en horizontal."""
    w, h = max(4, int(W / escala / estirar)), max(4, H // escala)
    n = Image.effect_noise((w, h), 70).filter(ImageFilter.GaussianBlur(1.2))
    n = ImageOps.autocontrast(n)
    if contraste != 1.0:
        n = ImageEnhance.Contrast(n).enhance(contraste)
    n = n.transform(n.size, Image.AFFINE, (1, 0, rnd.uniform(-w / 4, w / 4), 0, 1, rnd.uniform(-h / 4, h / 4)),
                    resample=Image.BILINEAR)
    return n.resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(max(W, H) / 300))


def _degrade(W: int, H: int, centro: float, ancho: float) -> Image.Image:
    """Máscara vertical: 255 en la altura `centro` (0 arriba, 1 abajo) y bajando a los costados."""
    col = Image.new("L", (1, 256))
    for y in range(256):
        d = abs(y / 255 - centro) / max(0.05, ancho)
        col.putpixel((0, y), int(255 * max(0.0, 1 - d * d)))
    return col.resize((W, H), Image.BICUBIC)


def _resplandor(img: Image.Image, umbral: int, radio_rel: float) -> Image.Image:
    """Las luces altas desparramadas (bloom), calculado chico y agrandado."""
    c = _chico(img)
    luz = c.convert("L").point(lambda v: max(0, v - umbral) * (255 // max(1, 255 - umbral)))
    glow = Image.composite(c, Image.new("RGB", c.size), luz).filter(
        ImageFilter.GaussianBlur(max(c.size) * radio_rel))
    return glow.resize(img.size, Image.BICUBIC)


def _luz_ambiente(img: Image.Image) -> tuple:
    """El color de las luces altas de la foto: la bruma toma ese color (dorada al atardecer, gris de día)."""
    c = _chico(img, 96)
    L = c.convert("L")
    m = L.point(lambda v: 255 if v > 170 else 0)
    if not m.getbbox():
        m = L.point(lambda v: 255 if v > 120 else 0)
    px = [p for p, k in zip(c.getdata(), m.getdata()) if k]
    if not px:
        return (225, 222, 218)
    r, g, b = (sum(p[i] for p in px) / len(px) for i in range(3))
    return (int(min(255, r * 0.6 + 100)), int(min(255, g * 0.6 + 98)), int(min(255, b * 0.6 + 96)))


# ── ATMÓSFERA ──
def _neblina(img: Image.Image, k: float, rnd: random.Random) -> Image.Image:
    """Neblina de mañana: pareja, más densa hacia el horizonte (lo lejano), lava contraste y color."""
    W, H = img.size
    color = (208, 212, 216)
    franja = _degrade(W, H, 0.38, 0.55)                       # el horizonte suele estar en el tercio de arriba
    pareja = Image.new("L", (W, H), 150)
    mascara = ImageChops.lighter(franja.point(lambda v: v * 0.85), pareja)
    mascara = ImageChops.multiply(mascara, _nube(W, H, 6, rnd, 0.6).point(lambda v: 170 + v // 3))
    mascara = mascara.point(lambda v: int(v * min(0.85, 0.42 * k)))
    out = Image.composite(Image.new("RGB", (W, H), color), img, mascara)
    out = ImageEnhance.Color(out).enhance(1 - 0.18 * k)
    return ImageEnhance.Contrast(out).enhance(1 - 0.10 * k)


def _ola(img: Image.Image, k: float, rnd: random.Random) -> Image.Image:
    """Bruma de ola: rocío desparejo que sube de abajo y de los costados, encendido por la luz, con gotitas."""
    W, H = img.size
    color = _luz_ambiente(img)
    abajo = _degrade(W, H, 0.85, 0.75)
    nubes = _nube(W, H, 5, rnd, 1.5)                           # manchones grandes y desparejos
    mascara = ImageChops.multiply(abajo, nubes)
    lados = Image.linear_gradient("L").rotate(90, expand=True).resize((W, H))
    lados = ImageChops.lighter(lados, ImageOps.mirror(lados)).point(lambda v: max(0, v - 120) * 2)
    mascara = ImageChops.lighter(mascara, ImageChops.multiply(lados, nubes).point(lambda v: v * 0.6))
    mascara = mascara.filter(ImageFilter.GaussianBlur(max(W, H) / 150)).point(lambda v: int(v * min(0.95, 0.6 * k)))
    capa = Image.composite(Image.new("RGB", (W, H), color), Image.new("RGB", (W, H)), mascara)
    out = ImageChops.screen(img, capa)                         # el rocío SUMA luz (a contraluz se enciende)
    # gotitas que brillan, algunas fuera de foco
    gotas = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(gotas)
    lado = max(W, H)
    for _ in range(int(260 * k)):
        x, y = rnd.uniform(0, W), H * (1 - rnd.random() ** 0.6)    # más abajo que arriba
        r = rnd.uniform(0.4, 1.6) * lado / 1600
        d.ellipse([x - r, y - r, x + r, y + r], fill=int(rnd.uniform(120, 255)))
    for _ in range(int(25 * k)):
        x, y = rnd.uniform(0, W), H * (1 - rnd.random() ** 0.6)
        r = rnd.uniform(4, 10) * lado / 1600
        d.ellipse([x - r, y - r, x + r, y + r], fill=int(rnd.uniform(25, 70)))
    gotas = gotas.filter(ImageFilter.GaussianBlur(max(1.0, lado / 2400)))
    out = ImageChops.screen(out, Image.composite(Image.new("RGB", (W, H), color), Image.new("RGB", (W, H)), gotas))
    out = ImageChops.screen(out, _resplandor(out, 175, 0.035).point(lambda v: int(v * min(1, 0.4 * k))))
    return ImageEnhance.Contrast(out).enhance(1 - 0.06 * k)


def _humo(img: Image.Image, k: float, rnd: random.Random) -> Image.Image:
    """Humo: volutas grises, estiradas y cortadas, que van y vienen (no una capa pareja)."""
    W, H = img.size
    volutas = _nube(W, H, 10, rnd, 2.2, estirar=2.2)
    cortes = _nube(W, H, 4, rnd, 1.6)
    mascara = ImageChops.multiply(volutas, cortes)
    mascara = ImageOps.autocontrast(mascara).point(lambda v: max(0, v - 45) * 255 // 210)
    mascara = mascara.filter(ImageFilter.GaussianBlur(max(W, H) / 220)).point(lambda v: int(v * min(0.85, 0.6 * k)))
    out = Image.composite(Image.new("RGB", (W, H), (186, 184, 182)), img, mascara)
    return ImageEnhance.Contrast(out).enhance(1 - 0.04 * k)


# ── GRANO ──
def _grano(img: Image.Image, nivel: str) -> Image.Image:
    W, H = img.size
    sigma, suave = {"fino": (7, 0.5), "medio": (12, 0.7), "grueso": (20, 1.1)}.get(nivel, (12, 0.7))
    g = Image.effect_noise((W, H), sigma).filter(ImageFilter.GaussianBlur(suave))
    # el grano se ve más en los medios tonos que en las luces quemadas
    peso = img.convert("L").point(lambda v: int(255 * (1 - abs(v - 128) / 160)))
    g = Image.composite(g, Image.new("L", (W, H), 128), peso)
    return ImageChops.add(img, Image.merge("RGB", (g, g, g)), 1.0, -128)


# ── SUCIEDAD ──
def _mancha(d: ImageDraw.ImageDraw, x: float, y: float, r: float, rnd: random.Random, a: int) -> None:
    """Una mota de polvo: polígono irregular (nunca un círculo perfecto)."""
    n = rnd.randint(5, 9)
    alarga = rnd.uniform(1.0, 2.6)
    giro = rnd.uniform(0, math.pi)
    pts = []
    for t in range(n):
        ang = 2 * math.pi * t / n + rnd.uniform(-0.3, 0.3)
        rr = r * rnd.uniform(0.45, 1.0)
        px, py = math.cos(ang) * rr * alarga, math.sin(ang) * rr
        pts.append((x + px * math.cos(giro) - py * math.sin(giro), y + px * math.sin(giro) + py * math.cos(giro)))
    d.polygon(pts, fill=a)


def _polvo(img: Image.Image, k: float, rnd: random.Random, rayas: bool) -> Image.Image:
    """Polvo SOBRE el negativo escaneado: pocas motas irregulares de distinto tamaño y opacidad (algunas fuera
    de foco), un par de pelitos curvos y, si se pide, alguna raya fina. Nada de puntos redondos parejos
    sembrados por toda la foto, que se ven falsos (parecen nieve)."""
    W, H = img.size
    lado = max(W, H)
    u = lado / 1500
    nitido = Image.new("L", (W, H), 0)
    difuso = Image.new("L", (W, H), 0)
    dn, dd = ImageDraw.Draw(nitido), ImageDraw.Draw(difuso)
    def lugar():
        # como en un retoque real, las motas casi nunca quedan en el centro (donde suele estar ella y su cara)
        for _ in range(8):
            x, y = rnd.uniform(0, W), rnd.uniform(0, H)
            if ((x / W - 0.5) / 0.32) ** 2 + ((y / H - 0.45) / 0.42) ** 2 > 1 or rnd.random() < 0.15:
                return x, y
        return x, y
    for _ in range(int(14 * k) + 4):
        x, y = lugar()
        if rnd.random() < 0.8:
            _mancha(dn, x, y, rnd.uniform(0.6, 2.4) * u, rnd, int(rnd.uniform(110, 230)))
        else:
            _mancha(dd, x, y, rnd.uniform(4, 9) * u, rnd, int(rnd.uniform(40, 90)))
    for _ in range(max(1, int(2 * k))):                         # pelitos: curvos, finos, semitransparentes
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        ang = rnd.uniform(0, 2 * math.pi)
        pts = [(x, y)]
        for _ in range(int(rnd.uniform(8, 16))):
            ang += rnd.uniform(-0.6, 0.6)
            x, y = x + math.cos(ang) * 6 * u, y + math.sin(ang) * 6 * u
            pts.append((x, y))
        dn.line(pts, fill=int(rnd.uniform(110, 190)), width=max(1, int(1.1 * u)))
    if rayas:                                                  # rayas finas y cortadas, poco marcadas
        for _ in range(int(1 + 2 * k)):
            x = rnd.uniform(0.05, 0.95) * W
            y0 = rnd.uniform(0, H * 0.6)
            y1 = y0 + rnd.uniform(0.15, 0.5) * H
            dn.line([(x, y0), (x + rnd.uniform(-2, 2) * u, y1)], fill=int(rnd.uniform(50, 110)), width=max(1, int(0.8 * u)))
    nitido = nitido.filter(ImageFilter.GaussianBlur(max(0.4, 0.5 * u)))
    difuso = difuso.filter(ImageFilter.GaussianBlur(3 * u))
    mascara = ImageChops.lighter(nitido, difuso)
    return Image.composite(Image.new("RGB", (W, H), (244, 242, 236)), img, mascara)


def _lente(img: Image.Image, k: float, rnd: random.Random) -> Image.Image:
    """Lente con huellas: la luz se desparrama en algunas zonas, sobre todo donde hay luces altas (cielo,
    reflejos). Sobre la piel en sombra casi no se nota: nada de manchas de barro."""
    W, H = img.size
    c = _chico(img, 640)
    difuso = ImageEnhance.Brightness(c.filter(ImageFilter.GaussianBlur(max(c.size) / 45))).enhance(1.08)
    m = Image.new("L", c.size, 0)
    d = ImageDraw.Draw(m)
    for _ in range(int(4 + 3 * k)):
        x, y = rnd.uniform(0, c.size[0]), rnd.uniform(0, c.size[1])
        rr = rnd.uniform(0.08, 0.22) * max(c.size)
        d.ellipse([x - rr, y - rr * rnd.uniform(0.5, 1), x + rr, y + rr], fill=int(rnd.uniform(80, 170)))
    m = m.filter(ImageFilter.GaussianBlur(max(c.size) / 18))
    luces = c.convert("L").point(lambda v: max(0, v - 110) * 255 // 145)
    m = ImageChops.multiply(m, luces).point(lambda v: int(v * min(1, 0.55 * k)))
    out = Image.composite(difuso.resize((W, H), Image.BICUBIC), img, m.resize((W, H), Image.BICUBIC))
    return ImageChops.screen(out, _resplandor(out, 170, 0.05).point(lambda v: int(v * min(1, 0.4 * k))))


# ── REVELADO DE PELÍCULA ──
def _revelado(img: Image.Image, k: float) -> Image.Image:
    W, H = img.size
    c = _chico(img)
    luces = c.convert("L").point(lambda v: 255 if v > 215 else 0).filter(ImageFilter.GaussianBlur(max(c.size) / 70))
    halo = Image.composite(Image.new("RGB", c.size, (255, 80, 30)), Image.new("RGB", c.size), luces)
    out = ImageChops.screen(img, halo.resize((W, H), Image.BICUBIC).point(lambda v: int(v * min(1, 0.45 * k))))
    lev = int(16 * k)
    r, g, b = out.split()
    out = Image.merge("RGB", (r.point(lambda v: lev + v * (255 - lev) // 255),
                              g.point(lambda v: int(lev * 0.9) + v * (255 - int(lev * 0.9)) // 255),
                              b.point(lambda v: int(lev * 0.7) + v * (255 - int(lev * 0.7)) // 255)))
    v = Image.radial_gradient("L").resize((W, H)).point(lambda p: int((max(0, p - 110) / 145) ** 1.6 * 255 * min(0.4, 0.16 * k)))
    return Image.composite(Image.new("RGB", (W, H)), out, v)


def capas_de(params: dict) -> dict:
    """Las capas pedidas, leídas de los params de la toma (o del pedido de filtro). Acepta el selector viejo."""
    p = params or {}
    c = dict(_VIEJOS.get(str(p.get("filtro_foto") or ""), {}))
    for clave, dic in (("atm", ATMOSFERAS), ("grano", GRANOS), ("sucio", SUCIEDADES), ("pelicula", PELICULAS)):
        v = str(p.get("filtro_" + clave) or "").strip()
        if v in dic and v:
            c[clave] = v
    f = str(p.get("filtro_fuerza") or "media")
    c["fuerza"] = f if f in FUERZAS else "media"
    return c


def hay_capas(c: dict) -> bool:
    return any(c.get(k) for k in ("atm", "grano", "sucio", "pelicula"))


def aplicar(img: Image.Image, atm: str = "", grano: str = "", sucio: str = "", pelicula: str = "",
            fuerza: str = "media", semilla: Optional[int] = None) -> Image.Image:
    """La imagen con las capas pedidas, en el orden en que pasa en la realidad: el aire (atmósfera), la
    película (revelado y grano) y lo que está encima (suciedad)."""
    k = FUERZAS.get(str(fuerza or "media"), 1.0)
    rnd = random.Random(semilla)
    modo = img.mode
    out = img.convert("RGB")
    if atm == "neblina":
        out = _neblina(out, k, rnd)
    elif atm == "ola":
        out = _ola(out, k, rnd)
    elif atm == "humo":
        out = _humo(out, k, rnd)
    if pelicula == "pelicula":
        out = _revelado(out, k)
    if grano in GRANOS and grano:
        out = _grano(out, grano)
    if sucio == "polvo":
        out = _polvo(out, k, rnd, False)
    elif sucio == "rayas":
        out = _polvo(out, k, rnd, True)
    elif sucio == "lente":
        out = _lente(out, k, rnd)
    return out.convert(modo) if modo in ("RGBA", "L") else out
