"""
CAMBIO DE CONJUNTO — el video de Instagram en el que la modelo tiene puesto un conjunto,
agarra otro, se lo trae al pecho y aparece con ese puesto; y así con varios colores.
Todo con la modelo de IA (un personaje), sin filmar nada.

Cómo se hace el "truco": un CORTE ESCONDIDO. El clip de un color termina con ella
sosteniendo el conjunto siguiente contra el pecho; el clip siguiente arranca en esa
MISMA pose, pero ya con el conjunto nuevo puesto. Las dos fotos del corte salen una de
la otra (Seedream edita la anterior: cambia sólo la prenda), así calzan casi exacto, y
un destello blanco cortito tapa lo que falte.

Fotos (Seedream, con la cara, el retrato, su cuerpo entero y su cuerpo escrito):
  base 0      ella con el color 0 puesto, parada, en el lugar
  agarra k    (desde la foto anterior) sigue con el color k y sostiene el k+1 contra el pecho
  puesto k+1  (desde "agarra k") la misma pose, pero con el color k+1 puesto
Clips (Seedance con foto de inicio y de final):
  clip 0      base 0      → agarra 0
  clip k      puesto k    → agarra k
  último      puesto N-1  → (sin final: baja la mano y muestra el conjunto)
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from arreglar_cara import arreglar_cara
from imagenes_ia import (
    CURRENT_SUB,
    _al_ingles,
    _compress_ref,
    _img_part,
    _pfx,
    _sanear_prompt_fal,
    _strip_data_url,
    budget_record,
    fal_generate,
    get_settings,
    kv,
    recorte_cara_avatar,
    set_current_sub,
)
from personajes import (
    PJ_DIR,
    _bind,
    _cobrar,
    _doc,
    _fal_enviar,
    _fal_esperar_y_bajar,
    _fal_key,
    _fal_subir,
    _g,
    _guardar_en_drive,
    _job_nuevo,
    _job_set,
    _k_job,
    _latir,
    _refs_identidad,
    _revisar_job,
    _slug,
    _texto,
)
from reels import AMBIENTES, AMBIENTES_EN, _REALISMO_EN, _cuerpo_en, _cuerpo_es, _ref_cuerpo
from videos_luma import _duracion_video, _ffmpeg_bin, _spawn

ROUTE_PREFIX = os.environ.get("CAMBIOS_PREFIX", "/cambios").rstrip("/")
API = ROUTE_PREFIX + "/api"
VERSION = "1.3.2"   # subí este número cada vez que cambiamos el archivo

MAX_COLORES = 5
MAX_FOTOS_COLOR = 3
CLIP_SEG = 5
ANCHO, ALTO = 1080, 1920
# Motores de video que aceptan foto de INICIO y de FINAL (end_image_url en fal).
MOTORES = {
    "seedance_pro": {"label": "Seedance Pro · 1080p (recomendado)",
                     "modelo": os.getenv("FAL_SEEDANCE_PRO_MODEL",
                                         "fal-ai/bytedance/seedance/v1/pro/image-to-video"),
                     "resolucion": "1080p",
                     "precio_seg": float(os.getenv("CAMBIOS_PRECIO_SEEDANCE_PRO", "0.148"))},
    "seedance2": {"label": "Seedance 2.0 · más fino, más caro",
                  "modelo": os.getenv("FAL_SEEDANCE2_I2V_MODEL", "bytedance/seedance-2.0/image-to-video"),
                  "resolucion": "720p",
                  "precio_seg": float(os.getenv("CAMBIOS_PRECIO_SEEDANCE2", "0.30"))},
}
MOTOR_DEFAULT = "seedance_pro"
FAL_QUEUE = "https://queue.fal.run"
CC_DIR = PJ_DIR / "cambios"
CC_DIR.mkdir(parents=True, exist_ok=True)

_IDENTIDAD = (
    "KEEP HER EXACT FACE: recognisably this specific person — same face shape, eyes, eyebrows, "
    "nose, lips, skin tone, freckles or moles, same hair colour and texture. Not a lookalike, "
    "not beautified, not rejuvenated."
)
_LOOK = (
    "A frame of a real vertical phone video for Instagram: sharp, well exposed, true-to-life "
    "colours, natural light, subtle grain. No text, no logos, no watermark, no other people. "
    "Exactly one person. Content for the online store of a lingerie brand: an elegant, modest "
    "e-commerce try-on video."
)
_SUFIJO_CLIP = (
    " Real phone video at 24 fps in REAL TIME: normal human pace, never slow motion. Small, "
    "natural, human movements. The camera does not move. The place, the light and the framing "
    "stay exactly as in the first frame. IDENTITY LOCK: her face, hair and body stay EXACTLY "
    "the same in every frame (same proportions, not slimmed). The garment she wears stays "
    "identical: same design, colour, straps and details. No morphing, no warping, no extra "
    "limbs or fingers, no text."
)


# ─────────────────────────────────────────────────────────────────────────────
# DATOS
# ─────────────────────────────────────────────────────────────────────────────

def _k_idx() -> str:
    return _pfx() + "cc:idx"


def _k_cc(cid: str) -> str:
    return _pfx() + f"cc:{cid}"


def _k_foto(cid: str, k: int, n: int) -> str:
    """Foto del producto n del color k."""
    return _pfx() + f"cc:{cid}:prod:{k}:{n}"


def _k_frame(cid: str, clave: str) -> str:
    return _pfx() + f"cc:{cid}:frame:{clave}"


def _clip_path(cid: str, k: int) -> Path:
    return CC_DIR / f"{cid}_clip{k}.mp4"


def _video_path(cid: str) -> Path:
    return CC_DIR / f"{cid}.mp4"


async def _cc(cid: str) -> Dict[str, Any]:
    d = await kv.get(_k_cc(cid))
    if not isinstance(d, dict):
        raise HTTPException(404, "Ese video de cambio de conjunto no existe.")
    return d


async def _guardar(d: Dict[str, Any]) -> None:
    await kv.set(_k_cc(d["id"]), d)


def plan_frames(n: int) -> List[Dict[str, Any]]:
    """Las fotos que hacen falta, en orden, cada una con la que usa de base."""
    out: List[Dict[str, Any]] = [{"clave": "base0", "tipo": "base", "color": 0, "desde": None,
                                  "titulo": "Color 1 puesto"}]
    ultimo = "base0"
    for k in range(n - 1):
        out.append({"clave": f"agarra{k}", "tipo": "agarra", "color": k, "siguiente": k + 1,
                    "desde": ultimo, "titulo": f"Con el color {k + 1}, agarra el {k + 2}"})
        out.append({"clave": f"puesto{k + 1}", "tipo": "puesto", "color": k + 1,
                    "desde": f"agarra{k}", "titulo": f"Mismo gesto, ya con el color {k + 2}"})
        ultimo = f"puesto{k + 1}"
    return out


def plan_clips(n: int) -> List[Dict[str, Any]]:
    out = []
    for k in range(n):
        inicio = "base0" if k == 0 else f"puesto{k}"
        fin = f"agarra{k}" if k < n - 1 else None
        out.append({"k": k, "inicio": inicio, "fin": fin})
    return out


def costo_estimado(n: int, motor: str, settings: Dict[str, Any], cara: bool) -> Dict[str, float]:
    n_fotos = len(plan_frames(n))
    foto = float(settings.get("precio_flux", 0.07) or 0.07)
    cara_c = float(settings.get("precio_1k", 0) or 0) or 0.07
    fotos = round(n_fotos * (foto + (cara_c if cara else 0)), 2)
    video = round(n * CLIP_SEG * MOTORES[motor]["precio_seg"], 2)
    return {"fotos": fotos, "video": video, "total": round(fotos + video, 2), "n_fotos": n_fotos}


# ─────────────────────────────────────────────────────────────────────────────
# LAS FOTOS (Seedream)
# ─────────────────────────────────────────────────────────────────────────────

async def _nombres_en(d: Dict[str, Any]) -> List[str]:
    nombres = [(_texto(c.get("nombre"), 80) or f"color {k + 1}") for k, c in enumerate(d["colores"])]
    tr = await _al_ingles({str(k): v for k, v in enumerate(nombres)})
    return [_color_seguro(tr.get(str(k)) or v) for k, v in enumerate(nombres)]


def _color_seguro(nombre: str) -> str:
    """"Nude" (el color piel de la lencería) en inglés es "desnuda": el filtro de Seedream lo
    lee así y rechaza la foto. Le llega como beige color piel. Lo mismo "skin"/"piel" solos."""
    n = re.sub(r"\bnudes?\b", "skin-tone beige", nombre, flags=re.IGNORECASE)
    n = re.sub(r"\b(color\s+)?piel\b", "skin-tone beige", n, flags=re.IGNORECASE)
    return re.sub(r"\bskin\b(?!-tone)", "skin-tone beige", n, flags=re.IGNORECASE)


async def prompt_frame(d: Dict[str, Any], doc: Dict[str, Any], f: Dict[str, Any],
                       n_prod: int, con_cuerpo: bool, nombres: List[str],
                       sin_gesto: bool = False) -> str:
    """El pedido para una foto. Orden de imágenes: [la foto anterior], la cara, [su cuerpo
    entero], las fotos del producto."""
    g = _g(doc)
    who = "woman" if g["she"] == "she" else "man"
    ancla = 1 if f.get("desde") else 0
    i_cara = 1 + ancla
    i_cuerpo = i_cara + 1
    p0 = i_cara + 1 + (1 if con_cuerpo else 0)
    prods = f"image {p0}" if n_prod == 1 else f"images {p0} to {p0 + n_prod - 1}"
    lugar = AMBIENTES_EN.get(d.get("ambiente") or "casa", AMBIENTES_EN["casa"])
    lugar_txt = _texto(d.get("lugar"), 500)
    if lugar_txt:
        lugar_txt = (await _al_ingles({"l": lugar_txt})).get("l") or lugar_txt
    cuerpo = await _cuerpo_en(doc)
    identidad = (f"Image {i_cara} is a tight FACE CROP of her. {_IDENTIDAD}"
                 + (f" Image {i_cuerpo} is her FULL-BODY reference: copy her BODY from it (height, "
                    "build, bust, waist, hips, glutes and legs), NOT its pose, clothes or background."
                    if con_cuerpo else ""))
    L: List[str] = []
    if f["tipo"] == "base":
        color = nombres[f["color"]]
        L.append(f"A realistic vertical 9:16 frame of an Instagram lingerie TRY-ON video: the {who} "
                 "from the reference images, standing facing the camera, framed from the knees up, "
                 "relaxed and natural, both arms relaxed at her sides, looking at the lens with a soft "
                 "smile.")
        L.append(identidad)
        if cuerpo:
            L.append(f"HER BODY (define it FIRST — do NOT start from a standard slim model): {cuerpo}. "
                     "Keep exactly these proportions: not slimmed, not idealised.")
        L.append(f"SETTING: {lugar}." + (f" HOW THE PLACE LOOKS: {lugar_txt}" if lugar_txt else ""))
        L.append(f"SHE IS WEARING the {color} set from the product photo(s) ({prods}), EXACTLY: same "
                 "design and cut, same colour, same fabric texture, same straps, underwire, bands and "
                 "trims. If a product photo shows several colours, she wears ONLY the " + color + " one.")
    elif f["tipo"] == "agarra":
        actual, sig = nombres[f["color"]], nombres[f["siguiente"]]
        L.append("Image 1 is the current frame of an Instagram try-on video. Produce the NEXT MOMENT "
                 "of the SAME shot: same place, same light, same camera and framing, same woman, same "
                 f"hair, and she still WEARS the same {actual} set as in image 1, unchanged.")
        L.append(identidad)
        if cuerpo:
            L.append(f"HER BODY: {cuerpo}. Same proportions as in image 1.")
        # Sin "tapando el corpiño" ni "contra el pecho": el filtro de salida de ByteDance
        # (y el de Qwen Image 3) rebotaba esas fotos. Es el gesto de probador de mostrar una
        # prenda por delante, como en un local.
        L.append(f"THE ONLY CHANGE: with her right hand she now holds up the {sig} set from the "
                 f"product photo(s) ({prods}) in front of her upper body, the way a shop assistant "
                 "shows a garment against herself in a fitting-room mirror: the top held at the "
                 "height of her collarbone and the matching bottom hanging below it; her left arm "
                 f"relaxed. The garment she holds is EXACTLY the {sig} one of the product photos: "
                 "same design, colour and details.")
    elif sin_gesto:   # puesto, pero la foto del gesto la hace el video: sale de la anterior
        nuevo = nombres[f["color"]]
        L.append("Image 1 is a frame of an Instagram try-on video. Produce the SAME frame with ONE "
                 f"change: she is now WEARING the {nuevo} set from the product photo(s) ({prods}) "
                 "instead of the garment she had on. Same pose, arms relaxed, same framing, same "
                 "place, same light, same hair, same makeup.")
        L.append(identidad)
        if cuerpo:
            L.append(f"HER BODY: {cuerpo}. Same proportions as in image 1.")
        L.append(f"The {nuevo} set she wears is EXACTLY the one of the product photos: same design and "
                 "cut, same colour, same fabric texture, same straps, underwire, bands and trims.")
    else:   # puesto
        nuevo = nombres[f["color"]]
        L.append("Image 1 is a frame of an Instagram try-on video. Produce the SAME frame with ONE "
                 f"change: she is now WEARING the {nuevo} set from the product photo(s) ({prods}) "
                 "instead of the garment she had on. Her right hand stays up at the height of her "
                 "collarbone, exactly where it is in image 1, now EMPTY and resting lightly just below "
                 "the collarbone (she holds nothing). Same pose, same framing, same place, same light, "
                 "same hair, same makeup.")
        L.append(identidad)
        if cuerpo:
            L.append(f"HER BODY: {cuerpo}. Same proportions as in image 1.")
        L.append(f"The {nuevo} set she wears is EXACTLY the one of the product photos: same design and "
                 "cut, same colour, same fabric texture, same straps, underwire, bands and trims.")
    L.append(_REALISMO_EN)
    L.append(_LOOK)
    return _sanear_prompt_fal("\n\n".join(L))


async def _generar_frame(d: Dict[str, Any], doc: Dict[str, Any], f: Dict[str, Any],
                         settings: Dict[str, Any], nombres: List[str],
                         forzar_sin_gesto: bool = False) -> Tuple[str, str]:
    """Genera una foto con Seedream y la guarda. Devuelve (b64, motor con el que se arregló la
    cara o '')."""
    refs = await _refs_identidad(doc)
    if not refs:
        raise HTTPException(400, "Este personaje todavía no tiene retrato aprobado.")
    retrato = refs[0][1]
    cara = await recorte_cara_avatar({"id": "pj:" + str(doc.get("id", "")), "ref_b64": retrato})
    cuerpo_ref = _ref_cuerpo(refs)
    k_prod = f["siguiente"] if f["tipo"] == "agarra" else f["color"]
    prods = []
    for n in range(int(d["colores"][k_prod].get("n_fotos") or 0)):
        b = await kv.get(_k_foto(d["id"], k_prod, n))
        if b:
            prods.append(b)
    if not prods:
        raise HTTPException(400, f"El color {k_prod + 1} no tiene fotos del producto.")
    desde = f.get("desde")
    sin_gesto = False
    if desde and f["tipo"] == "puesto" and (
            forzar_sin_gesto or ((d.get("frames") or {}).get(desde) or {}).get("omitida")):
        # La foto del gesto la hace el video (Seedream la rechazó): ésta sale de la foto de
        # antes del gesto, con los brazos como estaban.
        desde = next(x["desde"] for x in plan_frames(len(d["colores"])) if x["clave"] == desde)
        sin_gesto = True
    ancla = await kv.get(_k_frame(d["id"], desde)) if desde else None
    if desde and not ancla:
        raise HTTPException(400, f"Falta la foto anterior ({desde}): generá las fotos en orden.")
    # Seedream acepta hasta 8 referencias: foto anterior + cara + cuerpo + hasta 3 de la prenda.
    prods = prods[:MAX_FOTOS_COLOR]
    prompt = await prompt_frame(d, doc, f, len(prods), bool(cuerpo_ref), nombres, sin_gesto)
    parts: List[Dict[str, Any]] = [{"text": prompt}]
    if ancla:
        parts.append(_img_part(ancla))
    parts.append(_img_part(cara or retrato))
    if cuerpo_ref:
        parts.append(_img_part(cuerpo_ref))
    for b in prods:
        parts.append(_img_part(b))
    slug = str(settings.get("flux_tryon_model") or "bytedance/seedream/v5/pro/edit")
    # SÓLO Seedream: nada de respaldo con Qwen (cambia la modelo, la luz y el lugar y el corte
    # deja de calzar). Si el filtro la rechaza, quien llama la reintenta con Seedream.
    s1 = dict(settings, flux_fallback_model="", _fal_sin_adivinar=True)
    img = await fal_generate(parts, s1, "9:16", "1K", slug)
    await budget_record("cambio_foto", slug, float(settings.get("precio_flux", 0.07) or 0.07), 1,
                        note=f"cambio de conjunto: {f['titulo']}")
    # Se GUARDA apenas llega (ya está paga): si después algo falla al arreglar la cara, o el
    # servidor se reinicia con un deploy, la foto de Seedream queda. Antes se guardaba recién
    # al final y una foto que salió en fal no aparecía en la app.
    b64 = _compress_ref(img, max_dim=1920, q=93)
    await kv.set(_k_frame(d["id"], f["clave"]), b64)
    await _marcar(d["id"], f["clave"], {"cara": ""})
    motor_cara = ""
    if d.get("arreglar_cara", True):
        try:
            img2, motor_cara = await arreglar_cara(img, cara, retrato, settings)
            await budget_record("cambio_cara", motor_cara, 0.07, 1, note=f"cambio: cara de {f['titulo']}")
            b64 = _compress_ref(img2, max_dim=1920, q=93)
            await kv.set(_k_frame(d["id"], f["clave"]), b64)
        except Exception as e:
            motor_cara = ""
            print(f"[cambios][cara] {getattr(e, 'detail', e)}")      # queda la de Seedream
    return b64, motor_cara


async def _marcar(cid: str, clave: str, info: Dict[str, Any]) -> None:
    """Anota en la ficha que esa foto está (y con qué), y que el video hay que rearmarlo."""
    d = await _cc(cid)
    d.setdefault("frames", {})[clave] = {"ok": True, "ts": int(time.time()), **info}
    d["video"] = False
    await _guardar(d)


def _siguientes(clave: str, plan: List[Dict[str, Any]]) -> List[str]:
    """Las fotos que salen (directa o indirectamente) de esta: al rehacerla hay que rehacerlas."""
    out, cola = [], [clave]
    while cola:
        c = cola.pop(0)
        for f in plan:
            if f.get("desde") == c:
                out.append(f["clave"])
                cola.append(f["clave"])
    return out


def _faltantes(d: Dict[str, Any]) -> List[str]:
    return [f["clave"] for f in plan_frames(len(d.get("colores") or []))
            if not (d.get("frames") or {}).get(f["clave"])]


async def _procesar_fotos(jid: str, cid: str, desde: Optional[str], sub: Optional[str],
                          faltantes: bool = False) -> None:
    """Genera todas las fotos (o, con `desde`, esa y las que salen de ella; o, con
    `faltantes`, sólo las que no están), en orden. Cada foto que falla se reintenta una vez
    sola; si vuelve a fallar se frena ahí, con el nombre de la foto y el motivo, y las que
    ya salieron quedan (se sigue con "▶ Seguir con las que faltan", sin volver a pagarlas)."""
    set_current_sub(sub)
    parar = asyncio.Event()
    _spawn(_latir(jid, parar))
    try:
        d = await _cc(cid)
        doc = await _doc(d["pid"])
        plan = plan_frames(len(d["colores"]))
        claves = [f["clave"] for f in plan]
        if desde:
            claves = [desde] + _siguientes(desde, plan)
        elif faltantes:
            claves = _faltantes(d)
        settings = await get_settings()
        nombres = await _nombres_en(d)
        hechas = 0
        for f in plan:
            if f["clave"] not in claves:
                continue
            n_foto = plan.index(f) + 1
            await _job_set(jid, {"estado": "generando",
                                 "paso": f"Foto {n_foto} ({hechas + 1} de {len(claves)}): {f['titulo']}…"})
            d = await _cc(cid)          # fresca: una foto anterior pudo quedar "la hace el video"
            motor_cara, omitida, sin_gesto = "", False, False
            for intento in (1, 2):
                try:
                    _, motor_cara = await _generar_frame(d, doc, f, settings, nombres, sin_gesto)
                    break
                except Exception as e:
                    detalle = str(getattr(e, "detail", "") or e)[:300]
                    # "Error validating the input" a los ~50 s es el filtro de SALIDA de Seedream.
                    filtro = any(t in detalle.lower() for t in (
                        "filtro", "bloque", "flagged", "checker", "validating", "valor inválido"))
                    # Otra vuelta con Seedream (el filtro no siempre rechaza lo mismo); sin
                    # presupuesto o con datos que faltan no tiene sentido repetir.
                    if intento == 2 or getattr(e, "status_code", 0) in (400, 402):
                        if filtro and f["tipo"] == "agarra":
                            # El gesto de sostener el siguiente conjunto no pasa: lo hace el video
                            # (sin foto de final) y la foto siguiente sale de la de antes.
                            omitida = True
                            break
                        raise RuntimeError(
                            f"La foto {n_foto} ({f['titulo']}) no salió"
                            + (": el filtro de Seedream la rechazó dos veces. Probá ▶ Seguir de nuevo, "
                               "o subí la tuya con ⬆ en esa foto." if filtro else f": {detalle}"))
                    # "Ya con el siguiente" con la mano arriba es la que más rebota: la segunda
                    # vuelta la pide con los brazos relajados, desde la foto de antes del gesto
                    # (la pose de la foto base, que Seedream acepta). El destello tapa el corte.
                    if filtro and f["tipo"] == "puesto":
                        sin_gesto = True
                    print(f"[cambios] foto {n_foto} falló ({detalle}); reintento con Seedream"
                          + (" y los brazos relajados" if sin_gesto else ""))
                    await _job_set(jid, {"paso": f"Foto {n_foto}: " + (
                        "Seedream la rechazó, pruebo con los brazos relajados…" if sin_gesto else
                        "Seedream la rechazó, pruebo otra vez…" if filtro else "falló una vez, reintentando…")})
            if omitida:
                await kv.delete(_k_frame(cid, f["clave"]))
                await _marcar(cid, f["clave"], {"omitida": True, "cara": ""})
            else:
                await _marcar(cid, f["clave"], {"cara": motor_cara, "sin_gesto": sin_gesto})
            hechas += 1
        await _job_set(jid, {"estado": "listo", "paso": ""})
    except Exception as e:
        await _job_set(jid, {"estado": "error", "error": str(getattr(e, "detail", "") or e)[:600]})
    finally:
        parar.set()


# ─────────────────────────────────────────────────────────────────────────────
# LOS CLIPS (Seedance: foto de inicio → foto de final) Y EL ARMADO
# ─────────────────────────────────────────────────────────────────────────────

def _omitida(d: Dict[str, Any], clave: Optional[str]) -> bool:
    return bool(clave and ((d.get("frames") or {}).get(clave) or {}).get("omitida"))


def prompt_clip(c: Dict[str, Any], nombres: List[str], n: int, d: Optional[Dict[str, Any]] = None) -> str:
    """Qué pasa en el clip. Si la foto del gesto quedó "la hace el video", el clip no tiene
    foto de final (termina con ella sosteniendo el conjunto siguiente) y el clip siguiente
    arranca con los brazos relajados."""
    d = d or {}
    k = c["k"]
    actual = nombres[k]
    # Arranca con la mano arriba sólo si esa foto salió con el gesto.
    baja = (k > 0 and not _omitida(d, f"agarra{k - 1}")
            and not ((d.get("frames") or {}).get(c["inicio"]) or {}).get("sin_gesto"))
    if c["fin"]:
        sig = nombres[k + 1]
        mov = ("She lowers her hand from her chest. " if baja else "") + (
            f"She shows the {actual} set she is wearing with a small, natural turn of her hips to "
            f"one side and back, then reaches down to the side with her right hand, picks up the "
            f"{sig} set and holds it up in front of her upper body, the way you show a garment "
            "against yourself in a fitting-room mirror"
            + (", and keeps it there until the end." if _omitida(d, c["fin"])
               else ", ending exactly like the last frame."))
    else:
        mov = (("She lowers her hand from her chest and shows" if baja else "She shows")
               + f" the {actual} set she is wearing: a slow, natural turn to one side and back, a "
               "hand on her hip, and a soft smile at the lens.")
    return "Instagram lingerie try-on video, the same woman in the same place. " + mov + _SUFIJO_CLIP


async def _generar_clip(cli: httpx.AsyncClient, key: str, d: Dict[str, Any], c: Dict[str, Any],
                        prompt: str, jid: str) -> None:
    m = MOTORES.get(d.get("motor") or MOTOR_DEFAULT, MOTORES[MOTOR_DEFAULT])
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    ini = await kv.get(_k_frame(d["id"], c["inicio"]))
    if not ini:
        raise RuntimeError(f"Falta la foto {c['inicio']}: generá las fotos primero.")
    payload: Dict[str, Any] = {
        "prompt": prompt,
        "image_url": await _fal_subir(cli, key, base64.b64decode(ini), "image/jpeg", f"{jid}-ini.jpg"),
        "resolution": m["resolucion"], "duration": str(CLIP_SEG), "aspect_ratio": "9:16",
    }
    if c["fin"] and not _omitida(d, c["fin"]):
        fin = await kv.get(_k_frame(d["id"], c["fin"]))
        if not fin:
            raise RuntimeError(f"Falta la foto {c['fin']}: generá las fotos primero.")
        payload["end_image_url"] = await _fal_subir(cli, key, base64.b64decode(fin), "image/jpeg",
                                                    f"{jid}-fin.jpg")
    # end_image_url NUNCA se saca en los reintentos: sin la foto de final el corte no calza.
    await _fal_enviar(cli, headers, m["modelo"], payload, jid, ("aspect_ratio", "resolution", "duration"))
    job = await kv.get(_k_job(jid)) or {}
    await _fal_esperar_y_bajar(cli, headers, job["fal_status_url"], job["fal_result_url"],
                               _clip_path(d["id"], c["k"]), jid, inicio=job.get("fal_inicio"))


def armar(clips: List[Path], salida: Path, destello: float = 0.12) -> float:
    """Pega los clips a 1080x1920 con un destello blanco cortito en cada corte (tapa lo que
    no calce exacto entre "agarra" y "puesto"). Devuelve la duración final."""
    ff = _ffmpeg_bin()
    if not ff:
        raise RuntimeError("No hay ffmpeg en el servidor.")
    durs = [_duracion_video(p) for p in clips]
    cmd = [ff, "-y"]
    for p in clips:
        cmd += ["-i", str(p)]
    base = (f"scale={ANCHO}:{ALTO}:force_original_aspect_ratio=increase,crop={ANCHO}:{ALTO},"
            "fps=30,format=yuv420p,setsar=1")
    filtros = [f"[{k}:v]{base}[v{k}]" for k in range(len(clips))]
    ult, t = "v0", durs[0]
    for k in range(1, len(clips)):
        off = max(0.0, t - destello)
        filtros.append(f"[{ult}][v{k}]xfade=transition=fadewhite:duration={destello}:offset={off:.3f}[x{k}]")
        ult = f"x{k}"
        t = off + durs[k]
    cmd += ["-filter_complex", ";".join(filtros), "-map", f"[{ult}]", "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(salida)]
    res = subprocess.run(cmd, capture_output=True, timeout=600)
    if res.returncode != 0 or not salida.exists():
        raise RuntimeError("ffmpeg: " + res.stderr.decode(errors="ignore")[-400:])
    return round(t, 2)


async def _procesar_video(jid: str, cid: str, sub: Optional[str]) -> None:
    set_current_sub(sub)
    parar = asyncio.Event()
    _spawn(_latir(jid, parar))
    try:
        d = await _cc(cid)
        n = len(d["colores"])
        key = await _fal_key()
        nombres = await _nombres_en(d)
        clips = plan_clips(n)
        async with httpx.AsyncClient(timeout=600) as cli:
            for c in clips:
                if _clip_path(cid, c["k"]).exists() and (d.get("clips_ok") or {}).get(str(c["k"])):
                    continue          # ya estaba hecho con estas mismas fotos
                await _job_set(jid, {"estado": "generando",
                                     "paso": f"Clip {c['k'] + 1} de {n}: {nombres[c['k']]}…"})
                await _generar_clip(cli, key, d, c, prompt_clip(c, nombres, n, d), jid)
                m = MOTORES.get(d.get("motor") or MOTOR_DEFAULT, MOTORES[MOTOR_DEFAULT])
                await budget_record("cambio_clip", d.get("motor") or MOTOR_DEFAULT,
                                    round(CLIP_SEG * m["precio_seg"], 3), 1,
                                    note=f"cambio de conjunto: clip {c['k'] + 1}")
                d = await _cc(cid)
                d.setdefault("clips_ok", {})[str(c["k"])] = True
                await _guardar(d)
        await _job_set(jid, {"paso": "Armando el video con los cortes…"})
        dur = await asyncio.to_thread(armar, [_clip_path(cid, c["k"]) for c in clips], _video_path(cid))
        d = await _cc(cid)
        d["video"] = True
        d["duracion"] = dur
        await _guardar(d)
        link = await _guardar_en_drive(f"cambio-{_slug(d.get('titulo') or cid)}-{cid}.mp4",
                                       _video_path(cid).read_bytes(), "video/mp4")
        await _job_set(jid, {"estado": "listo", "paso": "", "drive": link})
    except Exception as e:
        await _job_set(jid, {"estado": "error", "error": str(getattr(e, "detail", "") or e)[:600]})
    finally:
        parar.set()


# ─────────────────────────────────────────────────────────────────────────────
# ROUTER
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(dependencies=[Depends(_bind)])


def _publico(d: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: d.get(k) for k in ("id", "pid", "titulo", "ambiente", "lugar", "colores", "motor",
                                 "arreglar_cara", "frames", "video", "duracion", "job", "creado")}
    out["plan"] = plan_frames(len(d.get("colores") or []))
    return out


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(PAGINA.replace("%%API%%", API).replace("%%VERSION%%", VERSION))


@router.get(API + "/config")
async def api_config() -> Dict[str, Any]:
    settings = await get_settings()
    return {"motores": {k: {"label": v["label"], "precio_seg": v["precio_seg"]} for k, v in MOTORES.items()},
            "motor_default": MOTOR_DEFAULT, "max_colores": MAX_COLORES, "max_fotos": MAX_FOTOS_COLOR,
            "clip_seg": CLIP_SEG, "ambientes": AMBIENTES, "fal_key": bool(await _fal_key()),
            "costos": {str(n): {m: costo_estimado(n, m, settings, True) for m in MOTORES}
                       for n in range(2, MAX_COLORES + 1)}}


@router.get(API + "/lista")
async def api_lista() -> Dict[str, Any]:
    out = []
    for cid in (await kv.get(_k_idx())) or []:
        d = await kv.get(_k_cc(cid))
        if isinstance(d, dict):
            out.append({"id": cid, "titulo": d.get("titulo"), "video": d.get("video"),
                        "colores": len(d.get("colores") or []), "creado": d.get("creado")})
    return {"cambios": out}


@router.post(API + "/crear")
async def api_crear(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Crea el video con la modelo, el lugar y los colores (nombre + fotos del producto)."""
    pid = str(payload.get("pid") or "")
    doc = await _doc(pid)
    if not (doc.get("hoja") or {}).get("retrato"):
        raise HTTPException(400, "Ese personaje todavía no tiene retrato aprobado.")
    colores_in = payload.get("colores") or []
    if not (2 <= len(colores_in) <= MAX_COLORES):
        raise HTTPException(400, f"Hacen falta de 2 a {MAX_COLORES} colores.")
    cid = _uuid.uuid4().hex[:10]
    colores = []
    for k, c in enumerate(colores_in):
        fotos = [x for x in (c.get("fotos") or []) if x][:MAX_FOTOS_COLOR]
        if not fotos:
            raise HTTPException(400, f"El color {k + 1} no tiene fotos del producto.")
        for n, f in enumerate(fotos):
            try:
                b = _compress_ref(base64.b64decode(_strip_data_url(str(f))), max_dim=1536, q=92)
            except Exception:
                raise HTTPException(400, f"No pude leer una foto del color {k + 1}.")
            await kv.set(_k_foto(cid, k, n), b)
        colores.append({"nombre": _texto(c.get("nombre"), 80) or f"Color {k + 1}", "n_fotos": len(fotos)})
    motor = payload.get("motor") if payload.get("motor") in MOTORES else MOTOR_DEFAULT
    d = {"id": cid, "pid": pid, "titulo": _texto(payload.get("titulo"), 80) or colores[0]["nombre"],
         "ambiente": payload.get("ambiente") if payload.get("ambiente") in AMBIENTES else "casa",
         "lugar": _texto(payload.get("lugar"), 500), "colores": colores, "motor": motor,
         "arreglar_cara": payload.get("arreglar_cara") is not False,
         "frames": {}, "video": False, "creado": time.strftime("%Y-%m-%d %H:%M")}
    await _guardar(d)
    await kv.set(_k_idx(), [cid] + [x for x in ((await kv.get(_k_idx())) or []) if x != cid][:30])
    return {"cambio": _publico(d)}


@router.get(API + "/{cid}")
async def api_get(cid: str) -> Dict[str, Any]:
    d = await _cc(cid)
    doc = await _doc(d["pid"])      # de esta cuenta
    # El último trabajo (si se cortó, la pantalla muestra por qué y ofrece seguir).
    job = None
    if d.get("job"):
        j = await _revisar_job(await kv.get(_k_job(str(d["job"]))) or {})
        if j:
            job = {k: j.get(k) for k in ("id", "estado", "paso", "error")}
    settings = await get_settings()
    unit = float(settings.get("precio_flux", 0.07) or 0.07) + (0.07 if d.get("arreglar_cara", True) else 0)
    falt = _faltantes(d)
    return {"cambio": _publico(d), "cuerpo": await _cuerpo_es(doc), "job": job,
            "faltan": len(falt), "costo_faltan": round(len(falt) * unit, 2)}


@router.post(API + "/{cid}/opciones")
async def api_opciones(cid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    d = await _cc(cid)
    if payload.get("motor") in MOTORES and payload["motor"] != d.get("motor"):
        d["motor"] = payload["motor"]
        d["clips_ok"] = {}
        d["video"] = False
    if "arreglar_cara" in payload:
        d["arreglar_cara"] = bool(payload["arreglar_cara"])
    await _guardar(d)
    return {"cambio": _publico(d)}


async def _job_en_curso(d: Dict[str, Any]) -> Optional[str]:
    jid = str(d.get("job") or "")
    if jid:
        j = await _revisar_job(await kv.get(_k_job(jid)) or {})
        if j.get("estado") in ("en_cola", "generando"):
            return jid
    return None


@router.post(API + "/{cid}/fotos")
async def api_fotos(cid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Genera las fotos (todas, o `desde` una foto: esa y las que salen de ella)."""
    d = await _cc(cid)
    en_curso = await _job_en_curso(d)
    if en_curso:
        return {"job": en_curso, "en_curso": True}
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal (FAL_KEY en Railway).")
    plan = plan_frames(len(d["colores"]))
    desde = payload.get("desde")
    faltantes = bool(payload.get("faltantes")) and not desde
    if desde and desde not in {f["clave"] for f in plan}:
        raise HTTPException(404, "Esa foto no existe.")
    if faltantes:
        n_fotos = len(_faltantes(d))
        if not n_fotos:
            raise HTTPException(400, "No falta ninguna foto.")
    else:
        n_fotos = 1 + len(_siguientes(desde, plan)) if desde else len(plan)
    settings = await get_settings()
    unit = float(settings.get("precio_flux", 0.07) or 0.07) + (0.07 if d.get("arreglar_cara", True) else 0)
    costo = round(n_fotos * unit, 2)
    await _cobrar(costo)
    if not faltantes:           # lo que se va a rehacer deja de valer (las que salieron bien, no)
        for f in plan:
            if not desde or f["clave"] == desde or f["clave"] in _siguientes(desde, plan):
                (d.get("frames") or {}).pop(f["clave"], None)
    d["clips_ok"] = {}
    d["video"] = False
    jid = _uuid.uuid4().hex[:10]
    d["job"] = jid
    await _guardar(d)
    await _job_nuevo(jid, d["pid"], "cambio_fotos", 90 * n_fotos,
                     {"costo": costo, "titulo": f"Cambio de conjunto: {n_fotos} fotos"})
    _spawn(_procesar_fotos(jid, cid, desde, CURRENT_SUB.get(), faltantes))
    return {"job": jid, "costo": costo, "n_fotos": n_fotos}


@router.post(API + "/{cid}/frame/{clave}")
async def api_frame_subir(cid: str, clave: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Pone en ese lugar una foto que ya tenés (por ejemplo, la que salió en fal y no quedó
    en la app). No cobra nada. Las fotos que salen de esta quedan como estaban."""
    d = await _cc(cid)
    if clave not in {f["clave"] for f in plan_frames(len(d["colores"]))}:
        raise HTTPException(404, "Esa foto no existe.")
    if await _job_en_curso(d):
        raise HTTPException(409, "Hay fotos generándose: esperá a que termine.")
    try:
        b64 = _compress_ref(base64.b64decode(_strip_data_url(str(payload.get("imagen") or ""))),
                            max_dim=1920, q=95)
    except Exception:
        raise HTTPException(400, "No pude leer la imagen.")
    await kv.set(_k_frame(cid, clave), b64)
    await _marcar(cid, clave, {"cara": "", "subida": True})
    d = await _cc(cid)
    d["clips_ok"] = {}
    await _guardar(d)
    return {"cambio": _publico(d)}


@router.get(API + "/{cid}/frame/{clave}")
async def api_frame(cid: str, clave: str):
    await _cc(cid)
    b = await kv.get(_k_frame(cid, clave))
    if not b:
        raise HTTPException(404, "Esa foto todavía no existe.")
    return Response(content=base64.b64decode(b), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post(API + "/{cid}/video")
async def api_video(cid: str) -> Dict[str, Any]:
    d = await _cc(cid)
    en_curso = await _job_en_curso(d)
    if en_curso:
        return {"job": en_curso, "en_curso": True}
    plan = plan_frames(len(d["colores"]))
    faltan = [f["titulo"] for f in plan if not (d.get("frames") or {}).get(f["clave"])]
    if faltan:
        raise HTTPException(400, "Faltan fotos: " + " · ".join(faltan))
    m = MOTORES.get(d.get("motor") or MOTOR_DEFAULT, MOTORES[MOTOR_DEFAULT])
    pend = [c for c in plan_clips(len(d["colores"])) if not (d.get("clips_ok") or {}).get(str(c["k"]))
            or not _clip_path(cid, c["k"]).exists()]
    costo = round(len(pend) * CLIP_SEG * m["precio_seg"], 2)
    await _cobrar(costo)
    jid = _uuid.uuid4().hex[:10]
    d["job"] = jid
    await _guardar(d)
    await _job_nuevo(jid, d["pid"], "cambio_video", 150 * max(1, len(pend)) + 30,
                     {"costo": costo, "titulo": f"Cambio de conjunto: video ({len(pend)} clips)"})
    _spawn(_procesar_video(jid, cid, CURRENT_SUB.get()))
    return {"job": jid, "costo": costo}


@router.get(API + "/{cid}/mp4")
async def api_mp4(cid: str):
    d = await _cc(cid)
    if not d.get("video") or not _video_path(cid).exists():
        raise HTTPException(404, "Todavía no hay video.")
    return FileResponse(str(_video_path(cid)), media_type="video/mp4",
                        filename=f"cambio-{_slug(d.get('titulo') or cid)}.mp4")


@router.get(API + "/job/{jid}")
async def api_job(jid: str) -> Dict[str, Any]:
    j = await kv.get(_k_job(jid))
    if not isinstance(j, dict):
        raise HTTPException(404, "Ese trabajo no existe.")
    j = await _revisar_job(j)
    return {k: j.get(k) for k in ("id", "estado", "paso", "error", "costo", "inicio", "estimado_seg", "drive")}


@router.delete(API + "/{cid}")
async def api_borrar(cid: str) -> Dict[str, Any]:
    d = await _cc(cid)
    for f in plan_frames(len(d["colores"])):
        await kv.delete(_k_frame(cid, f["clave"]))
    for k, c in enumerate(d["colores"]):
        for n in range(int(c.get("n_fotos") or 0)):
            await kv.delete(_k_foto(cid, k, n))
    for p in [_video_path(cid)] + [_clip_path(cid, k) for k in range(len(d["colores"]))]:
        try:
            p.unlink()
        except OSError:
            pass
    await kv.delete(_k_cc(cid))
    await kv.set(_k_idx(), [x for x in ((await kv.get(_k_idx())) or []) if x != cid])
    return {"ok": True}


PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cambio de conjunto · Studio Luma</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;--rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f}
  *{box-sizing:border-box} body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:16px;line-height:1.55}
  a{color:var(--rose-deep);text-decoration:none}
  header{padding:16px 18px 12px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  .brand{font-family:'Bodoni Moda',serif;font-size:24px;font-weight:600} .brand small{display:block;font-family:Jost;font-size:12px;color:var(--ink-soft)}
  .links{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap} .links a{font-size:13px;padding:7px 13px;border:1px solid var(--line);border-radius:999px;background:var(--card)}
  main{max-width:1080px;margin:0 auto;padding:16px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:16px}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:22px;margin:0 0 6px} h3{font-size:15px;margin:14px 0 6px;color:var(--rose-deep);font-weight:500}
  .hint{color:var(--ink-soft);font-size:13px;margin:4px 0 8px}
  label{display:block;font-size:13px;color:var(--ink-soft);margin:10px 0 4px}
  input,select,textarea{width:100%;background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit;font-size:15px}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:9px 16px;font:inherit;font-size:14px;cursor:pointer}
  button:disabled{opacity:.5} button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500}
  button.sm{padding:6px 12px;font-size:13px} button.bad{color:var(--bad)}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px} @media(max-width:640px){.row{grid-template-columns:1fr}}
  .color{border:1px solid var(--line);border-radius:14px;padding:12px;margin:10px 0;background:var(--card-2)}
  .thumbs{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px} .thumbs img{width:64px;height:64px;object-fit:cover;border-radius:8px;border:1px solid var(--line)}
  .frames{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
  .frame{border:1px solid var(--line);border-radius:12px;padding:8px;background:var(--card-2)}
  .frame img{width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:8px;background:#000}
  .frame .t{font-size:12.5px;margin:6px 0} .flecha{font-size:12px;color:var(--rose-deep)}
  video{width:100%;max-width:380px;border-radius:14px;background:#000}
  .toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);background:#2a2830;border:1px solid var(--rose);padding:10px 16px;border-radius:12px;font-size:14px;display:none;max-width:90vw;z-index:50}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid var(--ink-soft);border-top-color:var(--rose);border-radius:50%;animation:g 1s linear infinite;vertical-align:-1px;margin-right:6px}@keyframes g{to{transform:rotate(360deg)}}
</style></head><body>
<header><div class="brand">Cambio de conjunto<small>La modelo se trae el conjunto siguiente al pecho y aparece con él puesto · v%%VERSION%%</small></div>
<nav class="links"><a href="/personajes">👤 Personajes</a><a href="/reels">🎞️ Reels</a><a href="/">📸 Fotos</a></nav></header>
<script>
// Dentro de Reels (embed=1): sin encabezado, y le avisa a Reels el alto para no tener doble scroll.
if(new URLSearchParams(location.search).get("embed")){
  document.querySelector("header").style.display = "none";
  const avisar = () => parent.postMessage({cambiosAlto: document.documentElement.scrollHeight}, "*");
  new ResizeObserver(avisar).observe(document.body); window.addEventListener("load", avisar);
}
</script>
<main>
<div class="card" id="cNuevo">
  <h2>Nuevo video</h2>
  <p class="hint">Todo con la modelo de IA: tiene puesto un conjunto, agarra el siguiente, se lo trae al pecho y aparece con ese puesto. De 2 a 5 colores; para cada uno subí las fotos del producto (de 1 a 3). El orden de los colores es el orden del video.</p>
  <div class="row"><div><label>Modelo (personaje)</label><select id="pid"></select></div>
  <div><label>Lugar</label><select id="amb"></select></div></div>
  <label>Cómo es el lugar (opcional)</label><textarea id="lugar" rows="2" placeholder="ej: un vestidor con espejo grande y luz cálida"></textarea>
  <h3>Colores</h3><div id="colores"></div>
  <button class="sm" id="masColor">＋ Otro color</button>
  <div class="row"><div><label>Motor del video</label><select id="motor"></select></div>
  <div><label>Arreglar la cara en cada foto</label><select id="cara"><option value="si">Sí (Nano Banana: más ella)</option><option value="no">No</option></select></div></div>
  <p class="hint" id="costo"></p>
  <button class="go" id="crear">📸 Crear y sacar las fotos</button>
</div>
<div class="card" id="cActual" style="display:none">
  <h2 id="tit"></h2><p class="hint" id="cuerpo"></p>
  <p class="hint">Primero salen las fotos: revisalas, sobre todo los pares de cada corte (“agarra” → “ya con el siguiente”): tienen que coincidir la pose y el lugar. Si una no te gusta, <b>↻ Rehacer</b> rehace esa y las que salen de ella. Recién ahí armá el video.</p>
  <p class="hint" id="estado"></p>
  <div id="aviso"></div>
  <div class="frames" id="frames"></div>
  <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="go" id="video">🎬 Armar el video</button><button class="sm" id="todas">↻ Rehacer todas las fotos</button><button class="sm bad" id="borrar">🗑 Borrar</button></div>
  <div id="salida" style="margin-top:12px"></div>
</div>
<div class="card"><h2>Anteriores</h2><div id="lista" class="hint">…</div></div>
</main><div class="toast" id="toast"></div>
<script>
const API = "%%API%%"; let CFG = {}, CC = null;
const $ = s => document.querySelector(s);
const esc = t => String(t == null ? "" : t).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
function toast(m, ms){ const t = $("#toast"); t.textContent = m; t.style.display = "block"; clearTimeout(t._t); t._t = setTimeout(() => t.style.display = "none", ms || 4000); }
async function api(p, o){ const r = await fetch(API + p, Object.assign({headers: {"Content-Type": "application/json"}}, o || {})); const d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.detail || ("HTTP " + r.status)); return d; }
const post = (p, b) => api(p, {method: "POST", body: JSON.stringify(b || {})});
const leer = f => new Promise((ok, mal) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = mal; r.readAsDataURL(f); });
function filaColor(nombre){
  const d = document.createElement("div"); d.className = "color"; d._fotos = [];
  d.innerHTML = `<div class="row"><div><label style="margin-top:0">Color / nombre</label><input class="nom" value="${esc(nombre || "")}" placeholder="ej: blanco"></div>
    <div><label style="margin-top:0">Fotos del producto (1 a 3)</label><input type="file" accept="image/*" multiple class="fot"></div></div><div class="thumbs"></div>
    <button class="sm bad" style="margin-top:6px">Quitar</button>`;
  d.querySelector(".fot").onchange = async e => { d._fotos = await Promise.all(Array.from(e.target.files).slice(0, CFG.max_fotos || 3).map(leer));
    d.querySelector(".thumbs").innerHTML = d._fotos.map(s => `<img src="${s}">`).join(""); };
  d.querySelector("button").onclick = () => { d.remove(); costo(); };
  $("#colores").appendChild(d); costo();
}
function costo(){ const n = document.querySelectorAll(".color").length; const c = ((CFG.costos || {})[String(n)] || {})[$("#motor").value];
  $("#costo").textContent = c ? `${n} colores: ${c.n_fotos} fotos (~US$${c.fotos}) + ${n} clips de ${CFG.clip_seg} s (~US$${c.video}) ≈ US$${c.total} en total.` : "Poné de 2 a " + (CFG.max_colores || 5) + " colores."; }
async function seguir(jid, cuando){
  for(;;){ const j = await api("/job/" + jid);
    if(j.estado === "listo"){ $("#estado").textContent = ""; return j; }
    if(j.estado === "error") throw new Error(j.error || "Falló");
    $("#estado").innerHTML = `<span class="spin"></span>${esc(j.paso || "En cola…")}`; if(cuando) await cuando();
    await new Promise(r => setTimeout(r, 5000)); } }
function pintar(){
  $("#cActual").style.display = ""; $("#tit").textContent = CC.titulo + " · " + CC.colores.map(c => c.nombre).join(" → ");
  const F = $("#frames"); F.innerHTML = "";
  CC.plan.forEach((f, k) => { const ok = (CC.frames || {})[f.clave]; const d = document.createElement("div"); d.className = "frame";
    d.innerHTML = `${f.tipo === "puesto" ? '<div class="flecha">✂ corte</div>' : ""}<img src="${ok && !ok.omitida ? API + "/" + CC.id + "/frame/" + f.clave + "?t=" + (ok.ts || 0) : ""}" style="${ok && !ok.omitida ? "" : "opacity:.15"}"><div class="t">${k + 1}. ${esc(f.titulo)}${ok && ok.cara ? " · cara arreglada" : ""}${ok && ok.subida ? " · subida por vos" : ""}${ok && ok.omitida ? " · la hace el video (Seedream no aceptó esta foto)" : ""}${ok && ok.sin_gesto ? " · con los brazos relajados (Seedream no aceptó la mano arriba)" : ""}${ok ? "" : " · pendiente"}</div><div style="display:flex;gap:4px;flex-wrap:wrap">${ok ? `<button class="sm reh">↻ Rehacer</button>` : ""}<label style="margin:0"><input type="file" accept="image/*" class="sub" style="display:none"><span class="sm" style="display:inline-block;border:1px solid var(--line);border-radius:999px;padding:6px 12px;font-size:13px;cursor:pointer" title="Poné en este lugar una foto que ya tenés (por ejemplo la que salió en fal y no se pegó)">⬆ Subir</span></label></div>`;
    const b = d.querySelector(".reh"); if(b) b.onclick = () => fotos(f.clave);
    d.querySelector(".sub").onchange = async e => { const file = e.target.files[0]; if(!file) return;
      try{ await post("/" + CC.id + "/frame/" + f.clave, {imagen: await leer(file)}); toast("Foto " + (k + 1) + " cargada."); await refrescar(); }catch(err){ toast(err.message, 8000); } };
    F.appendChild(d); });
  $("#salida").innerHTML = CC.video ? `<video src="${API}/${CC.id}/mp4?t=${Date.now()}" controls playsinline></video><div><a href="${API}/${CC.id}/mp4">⬇️ Bajar el video</a> · ${CC.duracion || ""} s</div>` : "";
}
// Si el último trabajo se cortó (fal rechazó, tardó de más, o el servidor se reinició con un
// deploy), se dice qué foto y por qué, arriba de todo, con un botón para seguir SÓLO con las
// que faltan: las que ya salieron bien no se vuelven a pagar.
async function refrescar(){ const d = await api("/" + CC.id); CC = d.cambio; pintar(); pintarAviso(d); return d; }
function pintarAviso(d){
  const A = $("#aviso"); const corriendo = d.job && (d.job.estado === "generando" || d.job.estado === "en_cola");
  const hay = Object.keys(CC.frames || {}).length;
  let h = "";
  if(!corriendo && d.job && d.job.estado === "error")
    h += `<div style="border:1px solid var(--bad);border-radius:12px;padding:10px 12px;margin:8px 0;color:var(--bad);font-size:14px">⚠ Se cortó: ${esc(d.job.error || "el trabajo no terminó")}</div>`;
  if(!corriendo && d.faltan && hay)
    h += `<button class="go" id="seguirFaltan">▶ Seguir con las que faltan (${d.faltan} foto${d.faltan > 1 ? "s" : ""}, US$${d.costo_faltan})</button> <span class="hint">Las que ya salieron quedan como están.</span>`;
  A.innerHTML = h;
  const b = $("#seguirFaltan"); if(b) b.onclick = () => fotos(null, true);
}
async function abrir(id){ const d = await api("/" + id); CC = d.cambio; $("#cuerpo").textContent = d.cuerpo ? "Su cuerpo: " + d.cuerpo : "⚠ Este personaje no tiene cuerpo cargado: completalo en su ficha o la dibuja flaca estándar."; pintar(); pintarAviso(d); history.replaceState(null, "", "?id=" + id + (new URLSearchParams(location.search).get("embed") ? "&embed=1" : ""));
  if(d.job && (d.job.estado === "generando" || d.job.estado === "en_cola")){ try{ await seguir(CC.job, async () => { CC = (await api("/" + id)).cambio; pintar(); }); }catch(e){ $("#estado").textContent = ""; } await refrescar(); } }
async function fotos(desde, faltantes){ try{ $("#aviso").innerHTML = ""; const r = await post("/" + CC.id + "/fotos", desde ? {desde} : (faltantes ? {faltantes: true} : {})); if(r.costo) toast("Sacando " + r.n_fotos + " foto(s) (US$" + r.costo + ")…");
    await seguir(r.job, async () => { CC = (await api("/" + CC.id)).cambio; pintar(); }); }
  catch(e){ toast(e.message, 8000); $("#estado").textContent = ""; }
  await refrescar(); }
$("#crear").onclick = async () => { const b = $("#crear"); b.disabled = true;
  try{ const colores = Array.from(document.querySelectorAll(".color")).map(d => ({nombre: d.querySelector(".nom").value, fotos: d._fotos}));
    const r = await post("/crear", {pid: $("#pid").value, ambiente: $("#amb").value, lugar: $("#lugar").value, colores, motor: $("#motor").value, arreglar_cara: $("#cara").value === "si"});
    await abrir(r.cambio.id); cargarLista(); await fotos(); }
  catch(e){ toast(e.message, 8000); } b.disabled = false; };
$("#todas").onclick = () => { if(confirm("¿Rehacer todas las fotos?")) fotos(); };
$("#video").onclick = async () => { const b = $("#video"); b.disabled = true;
  try{ const r = await post("/" + CC.id + "/video"); if(r.costo != null) toast("Armando el video (US$" + r.costo + ")…");
    const j = await seguir(r.job); CC = (await api("/" + CC.id)).cambio; pintar(); if(j.drive) toast("Listo y guardado en tu Drive."); }
  catch(e){ toast(e.message, 8000); $("#estado").textContent = "Falló: " + e.message; } b.disabled = false; };
$("#borrar").onclick = async () => { if(!confirm("¿Borrar este video y sus fotos?")) return; await api("/" + CC.id, {method: "DELETE"}); CC = null; $("#cActual").style.display = "none"; history.replaceState(null, "", new URLSearchParams(location.search).get("embed") ? "?embed=1" : "?"); cargarLista(); };
$("#masColor").onclick = () => { if(document.querySelectorAll(".color").length >= (CFG.max_colores || 5)) return toast("Hasta " + CFG.max_colores + " colores."); filaColor(""); };
$("#motor").onchange = costo; $("#cara").onchange = costo;
async function cargarLista(){ const l = (await api("/lista")).cambios; $("#lista").innerHTML = l.length ? l.map(x => `<div><a href="?id=${x.id}" onclick="abrir('${x.id}');return false">${esc(x.titulo)}</a> · ${x.colores} colores · ${esc(x.creado || "")}${x.video ? " · 🎬" : ""}</div>`).join("") : "Todavía no hiciste ninguno."; }
(async () => {
  CFG = await api("/config");
  $("#motor").innerHTML = Object.entries(CFG.motores).map(([k, v]) => `<option value="${k}" ${k === CFG.motor_default ? "selected" : ""}>${esc(v.label)} · US$${v.precio_seg}/s</option>`).join("");
  $("#amb").innerHTML = Object.entries(CFG.ambientes).map(([k, v]) => `<option value="${k}" ${k === "casa" ? "selected" : ""}>${esc(k)} — ${esc(v.slice(0, 50))}…</option>`).join("");
  try{ const pj = await (await fetch("/personajes/api/lista")).json(); $("#pid").innerHTML = (pj.personajes || []).map(p => `<option value="${esc(p.id)}">${esc(p.nombre)}</option>`).join(""); }catch(e){}
  filaColor("blanco"); filaColor("negro");
  if(!CFG.fal_key) toast("Falta la API key de fal (FAL_KEY en Railway).", 8000);
  cargarLista(); const id = new URLSearchParams(location.search).get("id"); if(id) abrir(id);
})();
</script></body></html>
"""
