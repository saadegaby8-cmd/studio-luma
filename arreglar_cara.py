"""
ARREGLAR LA CARA: la foto la hace el motor que mejor sale (Seedream: calidad, prenda,
lencería) y la cara la pone el que mejor copia la identidad (Nano Banana).

Seedream no se puede entrenar y copia la cara "más o menos". Nano Banana copia la cara
muy bien pero bloquea la lencería. La salida: se recorta SOLO la cabeza de la foto (un
recorte de cara no tiene lencería), Nano Banana la rehace con la cara de ella, y se pega
de vuelta con bordes suaves y el mismo tono. Nada más de la foto cambia.

Si Nano Banana igual bloquea, va el face swap de fal (más blando, pero sin filtro).
"""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFilter, ImageStat

from imagenes_ia import (
    ANALYZE_ENDPOINT,
    FAL_API_KEY,
    _compress_ref,
    _current_api_key,
    _img_part,
    gemini_generate,
)

FACE_SWAP_MODEL = "fal-ai/face-swap"

_CAJA_PROMPT = (
    "Devolvé SOLO un JSON con el recuadro de la CARA de la persona principal (de la frente "
    "al mentón, de oreja a oreja, SIN el pelo), en coordenadas normalizadas de 0 a 1000 "
    'sobre la imagen: {"box_2d": [ymin, xmin, ymax, xmax]}. Si no hay ninguna cara '
    'visible, {"box_2d": null}. Sin texto extra.'
)

_PROMPT_CARA = (
    "Image 1 is a crop of a finished photo. Edit ONLY the FACE in image 1 so that it is "
    "unmistakably the same person as in images 2 and 3: same face shape, jaw, cheekbones, "
    "eyes and eye colour, eyebrows, nose, lips, skin tone, freckles and moles. It must be "
    "recognisable at first glance as her, not a lookalike.\n"
    "Keep EVERYTHING ELSE of image 1 exactly as it is: the head angle and tilt, where she "
    "looks, the expression (mouth open or closed, smile), the hair (colour, style and "
    "position), the light direction, colour and intensity on the face, the skin tone "
    "matching her neck and shoulders, the sharpness, the grain, the crop and the framing, "
    "the background and the clothes. Same size, same composition: output image 1 with only "
    "the face identity changed. Photorealistic skin with real texture, no retouching, no "
    "beautifying, no makeup change."
)


async def caja_cara(img_b64: str) -> Optional[Tuple[float, float, float, float]]:
    """(ymin, xmin, ymax, xmax) de la cara, de 0 a 1. None si no hay cara."""
    api_key = await _current_api_key()
    if not api_key:
        raise HTTPException(500, "Falta la API key de Google para ubicar la cara.")
    body = {"contents": [{"role": "user", "parts": [{"text": _CAJA_PROMPT}, _img_part(img_b64)]}],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}}
    async with httpx.AsyncClient(timeout=60) as cli:
        r = await cli.post(ANALYZE_ENDPOINT, json=body,
                           headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})
    if r.status_code != 200:
        raise HTTPException(502, f"No pude ubicar la cara (Gemini HTTP {r.status_code}).")
    try:
        txt = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
        txt = re.sub(r"^```(json)?|```$", "", txt.strip(), flags=re.MULTILINE).strip()
        bb = json.loads(txt).get("box_2d")
    except Exception:
        return None
    if not (isinstance(bb, list) and len(bb) == 4):
        return None
    ymin, xmin, ymax, xmax = [max(0.0, min(1000.0, float(v))) / 1000.0 for v in bb]
    if xmax - xmin < 0.02 or ymax - ymin < 0.02:
        return None
    return ymin, xmin, ymax, xmax


def recorte_cabeza(w: int, h: int, caja: Tuple[float, float, float, float]) -> Tuple[int, int, int]:
    """Un cuadrado con la cabeza entera (pelo, orejas, cuello) alrededor de la cara:
    (x0, y0, lado), siempre dentro de la foto."""
    ymin, xmin, ymax, xmax = caja
    fw, fh = (xmax - xmin) * w, (ymax - ymin) * h
    lado = int(max(fw, fh) * 2.2)
    lado = max(64, min(lado, w, h))
    cx = (xmin + xmax) / 2 * w
    cy = (ymin + ymax) / 2 * h - 0.05 * lado       # un poco hacia arriba: entra el pelo
    x0 = int(min(max(0, cx - lado / 2), w - lado))
    y0 = int(min(max(0, cy - lado / 2), h - lado))
    return x0, y0, lado


def mascara_cara(lado: int, caja_rel: Tuple[float, float, float, float]) -> Image.Image:
    """Óvalo de la cara (un poco más grande que la caja) con el borde difuminado: el pelo
    y el fondo del recorte quedan los de la foto original."""
    ymin, xmin, ymax, xmax = caja_rel
    cx, cy = (xmin + xmax) / 2 * lado, (ymin + ymax) / 2 * lado
    rx, ry = (xmax - xmin) / 2 * lado * 1.12, (ymax - ymin) / 2 * lado * 1.12
    m = Image.new("L", (lado, lado), 0)
    ImageDraw.Draw(m).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    return m.filter(ImageFilter.GaussianBlur(max(2, lado * 0.035)))


def igualar_tono(nueva: Image.Image, original: Image.Image, mascara: Image.Image) -> Image.Image:
    """Lleva el color medio de la cara nueva al de la original (sólo adentro del óvalo),
    para que no quede una cara más clara o más cálida que el cuello."""
    so = ImageStat.Stat(original, mask=mascara).mean
    sn = ImageStat.Stat(nueva, mask=mascara).mean
    bandas = []
    for k, banda in enumerate(nueva.split()):
        d = (so[k] - sn[k]) * 0.8
        bandas.append(banda.point(lambda v, d=d: max(0, min(255, int(v + d)))))
    return Image.merge("RGB", bandas)


def pegar(foto: Image.Image, cabeza_nueva: Image.Image, x0: int, y0: int, lado: int,
          caja_rel: Tuple[float, float, float, float]) -> Image.Image:
    original = foto.crop((x0, y0, x0 + lado, y0 + lado))
    nueva = cabeza_nueva.convert("RGB").resize((lado, lado), Image.LANCZOS)
    m = mascara_cara(lado, caja_rel)
    nueva = igualar_tono(nueva, original, m)
    out = foto.copy()
    out.paste(Image.composite(nueva, original, m), (x0, y0))
    return out


async def _face_swap_fal(cabeza_jpg: bytes, cara_b64: str, settings: Dict[str, Any]) -> bytes:
    key = str(settings.get("fal_api_key") or FAL_API_KEY).strip()
    if not key:
        raise HTTPException(400, "Nano Banana bloqueó la cara y no hay API key de fal para el respaldo.")
    body = {"base_image_url": "data:image/jpeg;base64," + base64.b64encode(cabeza_jpg).decode(),
            "swap_image_url": "data:image/jpeg;base64," + cara_b64}
    async with httpx.AsyncClient(timeout=180) as cli:
        r = await cli.post(f"https://fal.run/{FACE_SWAP_MODEL}", json=body,
                           headers={"Authorization": f"Key {key}", "Content-Type": "application/json"})
        if r.status_code != 200:
            raise HTTPException(502, f"El face swap de fal falló (HTTP {r.status_code}): {r.text[:200]}")
        d = r.json()
        img = d.get("image") or ((d.get("images") or [None])[0])
        url = img.get("url") if isinstance(img, dict) else None
        if not url:
            raise HTTPException(502, f"El face swap de fal no devolvió imagen: {str(d)[:200]}")
        if url.startswith("data:"):
            return base64.b64decode(url.split(",", 1)[1])
        rr = await cli.get(url)
        if rr.status_code != 200:
            raise HTTPException(502, "No pude bajar la cara del face swap.")
        return rr.content


async def arreglar_cara(foto: bytes, cara_b64: str, retrato_b64: str,
                        settings: Dict[str, Any]) -> Tuple[bytes, str]:
    """Devuelve (foto con la cara de ella, motor usado: "nano_banana" o "face_swap")."""
    img = Image.open(io.BytesIO(foto)).convert("RGB")
    w, h = img.size
    caja = await caja_cara(_compress_ref(foto, max_dim=1024, q=88))
    if not caja:
        raise HTTPException(422, "No encontré una cara en la escena para arreglar.")
    x0, y0, lado = recorte_cabeza(w, h, caja)
    ymin, xmin, ymax, xmax = caja
    caja_rel = ((ymin * h - y0) / lado, (xmin * w - x0) / lado,
                (ymax * h - y0) / lado, (xmax * w - x0) / lado)
    buf = io.BytesIO()
    img.crop((x0, y0, x0 + lado, y0 + lado)).save(buf, format="JPEG", quality=95)
    cabeza = buf.getvalue()
    parts = [{"text": _PROMPT_CARA},
             {"text": "IMAGE 1 (the crop to edit):"}, _img_part(base64.b64encode(cabeza).decode()),
             {"text": "IMAGE 2 (her face):"}, _img_part(cara_b64 or retrato_b64)]
    if retrato_b64 and cara_b64:
        parts += [{"text": "IMAGE 3 (her portrait):"}, _img_part(retrato_b64)]
    motor = "nano_banana"
    try:
        nueva = await gemini_generate(parts, settings, "1:1", "2K" if lado > 1100 else "1K",
                                      save_prompt=False)
    except HTTPException as e:
        if e.status_code != 422:
            raise
        nueva = await _face_swap_fal(cabeza, cara_b64 or retrato_b64, settings)
        motor = "face_swap"
    out = pegar(img, Image.open(io.BytesIO(nueva)), x0, y0, lado, caja_rel)
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=95)
    return buf.getvalue(), motor
