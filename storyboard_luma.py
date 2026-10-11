# -*- coding: utf-8 -*-
"""
storyboard_luma.py — EL STORYBOARD: la base de todos los videos hiperrealistas
================================================================================

El orden es el de los reels con IA que parecen filmados de verdad:

1. LOS ACTIVOS (ya hechos en la app): su cara en 4K de cerca (Cara HD), su cuerpo SIN
   cabeza con cada prenda puesta sobre gris (el probador de Mis prendas) y el lugar vacío
   (Mis lugares).
2. EL GUION SEGUNDO A SEGUNDO: Claude escribe los cuadros (1 a 3 s cada uno), cada uno con
   su plano, su ángulo, su movimiento, qué se ve y qué se mueve, y la voz corrida.
3. EL STORYBOARD: por cada cuadro, GEMINI (Nano Banana) dibuja la imagen combinando los
   activos: el lugar + su cuerpo con la prenda + su cara en 4K, en ese ángulo exacto. Vos ves
   la grilla y aprobás o rehacés cada cuadro con un comentario. Nada se filma sin aprobar.
4. EL VIDEO: cada cuadro aprobado es el PRIMER CUADRO exacto de su tramo (Kling sigue desde
   esa imagen 3 s), se corta a sus segundos, se une con cortes secos y lleva su voz encima.

Si Gemini rechaza un cuadro puntual (filtro de lencería), se intenta con Seedream y el cuadro
lo dice ("dibujado con Seedream: Gemini lo rechazó").
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import os
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

import claude_director as _claude
import filmado as _film
import referencias_luma as refs_luma
from imagenes_ia import (
    CURRENT_SUB,
    _al_ingles,
    _compress_ref,
    _img_part,
    _pfx,
    _pricing,
    _sanear_prompt_fal,
    _strip_data_url,
    budget_record,
    fal_generate,
    gemini_generate,
    get_settings,
    kv,
    set_current_sub,
)
from personajes import (
    PJ_DIR,
    _bind,
    _cobrar,
    _doc,
    _fal_key,
    _fal_subir,
    _guardar_en_drive,
    _job_nuevo,
    _job_set,
    _k_job,
    _latir,
    _refs_identidad,
    _revisar_job,
    _slug,
    _texto,
    caras_hd,
    cara_identidad,
)
from videos_luma import _duracion_video, _spawn

VERSION = "1.0.0"
ROUTE_PREFIX = os.environ.get("STORYBOARD_PREFIX", "/storyboard").rstrip("/")
API = ROUTE_PREFIX + "/api"
DIR = PJ_DIR / "storyboard"
DIR.mkdir(parents=True, exist_ok=True)

DURACIONES = (10, 15, 20, 30)
MAX_CUADROS = 18
SEG_CUADRO_MIN, SEG_CUADRO_MAX = 1, 3
SEG_FILMA = 3                      # Kling filma 3 s como mínimo: se corta a los segundos del cuadro
PARALELO = 3
MOTOR_VIDEO = os.getenv("STORYBOARD_MOTOR", "kling_pro")
CALIDAD_CUADRO = os.getenv("STORYBOARD_CALIDAD", "2K")
TIPOS = {"ella": "Ella", "producto": "Sólo la prenda"}


# ─────────────────────────────────────────────────────────────────────────────
# GUARDADO
# ─────────────────────────────────────────────────────────────────────────────

def _k_idx() -> str:
    return _pfx() + "sb:idx"


def _k_sb(sid: str) -> str:
    return _pfx() + f"sb:{sid}"


def _k_img(sid: str, cid: str) -> str:
    return _pfx() + f"sb:{sid}:img:{cid}"


def _dir(sid: str) -> Path:
    d = DIR / _slug(_pfx() or "x") / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clip(sid: str, cid: str) -> Path:
    return _dir(sid) / f"{cid}.mp4"


def _final(sid: str) -> Path:
    return _dir(sid) / "storyboard.mp4"


_LOCKS: Dict[str, asyncio.Lock] = {}


def _lock(sid: str) -> asyncio.Lock:
    return _LOCKS.setdefault(sid, asyncio.Lock())


async def _sb(sid: str) -> Dict[str, Any]:
    d = await kv.get(_k_sb(sid))
    if not isinstance(d, dict):
        raise HTTPException(404, "Ese storyboard no existe.")
    return d


async def _guardar(sb: Dict[str, Any]) -> None:
    sb["actualizado"] = time.strftime("%Y-%m-%d %H:%M")
    await kv.set(_k_sb(sb["id"]), sb)


# ─────────────────────────────────────────────────────────────────────────────
# LOS CUADROS
# ─────────────────────────────────────────────────────────────────────────────

def _seg(v: Any) -> int:
    try:
        return max(SEG_CUADRO_MIN, min(SEG_CUADRO_MAX, int(round(float(v)))))
    except (TypeError, ValueError):
        return 2


def _limpiar_cuadro(sb: Dict[str, Any], c: Dict[str, Any]) -> Dict[str, Any]:
    n_col = max(1, len(sb.get("colores") or []))
    try:
        var = max(0, min(n_col - 1, int(c.get("variante") or 0)))
    except (TypeError, ValueError):
        var = 0
    return {"id": "c" + _uuid.uuid4().hex[:7],
            "tipo": c.get("tipo") if c.get("tipo") in TIPOS else "ella",
            "plano": c.get("plano") if c.get("plano") in _film.PLANOS else "medio",
            "angulo": c.get("angulo") if c.get("angulo") in _film.ANGULOS else "ojos",
            "movimiento": c.get("movimiento") if c.get("movimiento") in _film.MOVIMIENTOS else "mano",
            "seg": _seg(c.get("seg")), "variante": var,
            "que_se_ve": _texto(c.get("que_se_ve"), 400), "imagen": _texto(c.get("imagen"), 700),
            "accion": _texto(c.get("accion"), 300), "movimiento_video": _texto(c.get("movimiento_video"), 400),
            "img": False, "aprobado": False, "motor_img": "", "nota_img": "", "error": "", "clip": False}


_PLANTILLA = [  # (plano, ángulo, movimiento, tipo, seg, qué se ve, imagen, acción, movimiento del video)
    ("primer", "ojos", "acerca", "ella", 2, "Ella mira a cámara y sonríe, de cerca.",
     "close-up of her face and shoulders, she looks into the lens with a soft smile, the set's straps visible",
     "Mira a cámara y sonríe.", "she smiles softly at the lens and tilts her head slightly, hair moving"),
    ("detalle", "ojos", "acerca", "ella", 2, "Sus dedos recorren el encaje.",
     "extreme close-up of her fingertips tracing the lace of the set, the fabric texture razor sharp",
     "Los dedos recorren el encaje.", "her fingertips slowly trace the lace"),
    ("americano", "bajo", "mano", "ella", 2, "Desde abajo: se acomoda el pelo.",
     "knees-up shot from a low angle, she runs a hand through her hair, the whole set visible",
     "Se acomoda el pelo.", "she runs her hand through her hair and shifts her weight"),
    ("detalle", "perfil", "paneo", "ella", 2, "De perfil, se acomoda un bretel.",
     "extreme close-up in side profile of her shoulder, two fingers adjusting a strap",
     "Se acomoda un bretel.", "she adjusts the strap with two fingers, it snaps lightly back"),
    ("espejo", "ojos", "mano", "ella", 2, "Selfie en el espejo, cuerpo entero.",
     "mirror selfie: only her reflection in the full-length mirror, phone at chest height, the whole set visible",
     "Se saca una selfie en el espejo.", "she shifts her pose slightly in the mirror selfie"),
    ("entero", "tres_cuartos", "orbita", "ella", 3, "Cuerpo entero de 3/4, gira la cadera.",
     "full-body shot at a three-quarter angle, she turns her hips a quarter showing the side of the set",
     "Gira la cadera un cuarto.", "she slowly turns her hips a quarter, then back"),
    ("americano", "espalda", "mano", "ella", 2, "Desde atrás, mira por sobre el hombro.",
     "knees-up shot from behind, she looks back over her shoulder, the back of the set clearly visible",
     "Mira por sobre el hombro.", "she looks back over her shoulder and smiles"),
    ("medio", "alto", "acerca", "ella", 2, "Cierre: mira a cámara.",
     "medium shot from slightly above, she looks into the lens with a confident smile",
     "Mira a cámara (cierre).", "she smiles and gives a small nod to the lens"),
]


# ─────────────────────────────────────────────────────────────────────────────
# PASO 2 · EL GUION SEGUNDO A SEGUNDO (Claude)
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_GUION = (
    "You are the creative director of Instagram reels for an Argentine lingerie brand. Write the "
    "STORYBOARD of a {dur}-second reel, SECOND BY SECOND, like the hyper-realistic AI reels that look "
    "filmed on a phone. Every frame of the storyboard will be drawn first as an exact photo (from "
    "reference photos of the brand's model, of the set worn on her body and of the real place) and "
    "then animated for its seconds; the cuts between frames are hard cuts.\n"
    "RULES:\n"
    "- {n} frames of 1, 2 or 3 WHOLE seconds (mostly 2; the hook 1-2), {dur} s in total.\n"
    "- Hook in the first 2 seconds. Then the reveal, details, fit and back, and the close.\n"
    "- EVERY frame changes the \"plano\" AND the \"angulo\" versus the previous one; at least 6 different "
    "angulos; at least 3 detail frames (lace, fabric, straps, closure, waistband, fingers on the fabric).\n"
    "- A frame where her FACE is clearly visible (close-up, medium, mirror, facing the camera) at least "
    "every 5 seconds, including the first or second frame.\n"
    "- The garment always has VOLUME: worn by her, held in her hand or on a hanger — never flat or "
    "folded in a pile. The SAME woman in every frame, never other people.\n"
    "- ONE micro-action per frame, readable in 2 seconds (fingers tracing the lace, adjusting a strap, the "
    "elastic snapping back, a quarter turn of the hips, a glance at the mirror, hair falling).\n"
    "- {colores}\n"
    "- Sensual, confident and natural, like the best lingerie try-on reels, never explicit.\n"
    "FIELDS of each frame: \"seg\" (1-3), \"tipo\" (\"ella\" or \"producto\" = the set alone with volume), "
    "\"variante\" (colour index, 0 to {max_var}), \"plano\" (one of {planos}), \"angulo\" (one of "
    "{angulos}), \"movimiento\" (camera move, one of {movimientos}), \"que_se_ve\" (Spanish from "
    "Argentina, one short sentence for the owner), \"imagen\" (English, max 50 words: the exact STILL "
    "frame — her pose caught mid-movement, where she is in the place, what of the set is clearly "
    "visible; do not describe her face or body, they come from the references), \"accion\" (Spanish, "
    "what moves), \"movimiento_video\" (English, max 25 words: what moves in those seconds, subtle, "
    "real-time).\n"
    "Also \"guion\": her VOICE over the whole reel, ONE continuous narration in Spanish from Argentina "
    "(Rioplatense, voseo, natural like a real influencer: short phrases, natural pauses with \"...\", a hook "
    "first and the call to action at the end; no hashtags, no emojis), about {palabras} words. And "
    "\"titulo\" (Spanish, short).\n"
    "Do not invent prices, sizes or promos nobody told you.\n"
    'Answer in JSON: {{"titulo": "...", "guion": "...", "cuadros": [{{"seg": 2, "tipo": "ella", '
    '"variante": 0, "plano": "...", "angulo": "...", "movimiento": "...", "que_se_ve": "...", '
    '"imagen": "...", "accion": "...", "movimiento_video": "..."}}]}}'
)


async def _contexto(sb: Dict[str, Any], doc: Dict[str, Any]) -> str:
    prenda = await refs_luma.prenda(sb["prenda"]) or {}
    lugar = await refs_luma.lugar(sb.get("lugar_ref") or "") if sb.get("lugar_ref") else None
    cuerpo = await _film._cuerpo_en(doc)
    return (f"THE SET: {prenda.get('nombre', '')}. {prenda.get('desc', '')}\n"
            f"COLOURS IN THIS REEL (index: name): "
            + ", ".join(f"{i}: {c}" for i, c in enumerate(sb.get("colores_nombres") or [])) + "\n"
            f"THE PLACE: {(lugar or {}).get('nombre', '')} — {(lugar or {}).get('desc', '') or 'see the photo'}\n"
            f"HER BODY: {cuerpo}\n"
            f"WHAT THE OWNER WANTS: {sb.get('idea') or '(decide yourself: a try-on reel that sells the set)'}")


async def _guion_claude(sb: Dict[str, Any], doc: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
    dur = int(sb.get("duracion") or 15)
    n_col = len(sb.get("colores") or [])
    system = _SYSTEM_GUION.format(
        dur=dur, n=f"{max(5, round(dur / 2.3))} to {min(MAX_CUADROS, round(dur / 1.7))}",
        colores=("Several colours: show them in order, the change of colour on a frame where she covers the "
                 "lens with her hand or turns." if n_col > 1 else "One colour (variante 0)."),
        max_var=max(0, n_col - 1), planos=", ".join(_film.PLANOS),
        angulos=", ".join(f"{k} ({v[1]})" for k, v in _film.ANGULOS.items()),
        movimientos=", ".join(_film.MOVIMIENTOS), palabras=int(dur * 2.3))
    partes: List[Dict[str, Any]] = [{"text": await _contexto(sb, doc)}]
    for i, ci in enumerate(sb.get("colores") or []):
        fotos = await refs_luma.fotos_color(sb["prenda"], ci)
        if fotos:
            partes += [{"text": f"Product photo, colour {i}:"}, _img_part(_compress_ref(base64.b64decode(fotos[0]),
                                                                                       max_dim=900, q=85))]
    lug = await refs_luma.vista_para(sb["lugar_ref"], "entero") if sb.get("lugar_ref") else None
    if lug:
        partes += [{"text": "The place (empty):"}, _img_part(_compress_ref(base64.b64decode(lug), max_dim=900, q=85))]
    data, costo = await _claude.pedir_json(system, partes, max_tokens=12000,
                                           esfuerzo=os.getenv("STORYBOARD_ESFUERZO", "medium"))
    cuadros = [_limpiar_cuadro(sb, c) for c in (data.get("cuadros") or [])[:MAX_CUADROS] if isinstance(c, dict)]
    cuadros = [c for c in cuadros if c["imagen"] or c["que_se_ve"]]
    if len(cuadros) < 3:
        raise _claude.ClaudeNoDisponible("Claude no devolvió un storyboard usable.")
    return {"titulo": _texto(data.get("titulo"), 80), "guion": _texto(data.get("guion"), 1500),
            "cuadros": cuadros}, costo


# ─────────────────────────────────────────────────────────────────────────────
# PASO 3 · LOS CUADROS DEL STORYBOARD (Gemini; Seedream si Gemini rechaza)
# ─────────────────────────────────────────────────────────────────────────────

class _Activos:
    """Lo que mira cada cuadro: el lugar, su cara en 4K, su cuerpo y el probador de cada color."""
    def __init__(self) -> None:
        self.lugar: Optional[str] = None
        self.caras: List[Tuple[str, str]] = []          # (qué es, b64)
        self.retrato: Optional[str] = None
        self.probador: Dict[int, List[str]] = {}        # color → [frente, espalda, (3/4)]
        self.reales: Dict[int, List[str]] = {}          # color → fotos reales del producto
        self.cuerpo_en: str = ""


async def _activos(sb: Dict[str, Any], doc: Dict[str, Any]) -> _Activos:
    a = _Activos()
    if sb.get("lugar_ref"):
        a.lugar = await refs_luma.vista_para(sb["lugar_ref"], "medio")
    refs = await _refs_identidad(doc)
    if not refs:
        raise HTTPException(400, "Este personaje todavía no tiene retrato aprobado.")
    a.retrato = refs[0][1]
    hd = await caras_hd(doc)
    for k, et in (("cara_hd", "her face, frontal close-up in 4K"), ("ojos_hd", "macro of her eyes and skin"),
                  ("perfil_hd", "her face in three-quarter profile")):
        if hd.get(k):
            a.caras.append((et, hd[k]))
    if not a.caras:
        cara = await cara_identidad(doc, a.retrato)
        a.caras.append(("her face, close-up", cara or a.retrato))
    for v, ci in enumerate(sb.get("colores") or []):
        fotos = await refs_luma.fotos_color(sb["prenda"], ci)
        a.reales[v] = fotos[:2]
        pb = await refs_luma.probador_de(sb["prenda"], ci, doc["id"])
        if not pb and fotos:
            # Si todavía no tiene probador para esta modelo, se hace ahora (queda en Mis prendas).
            pb = await _film.probador_para(doc, fotos, refs, ficha=[sb["prenda"], ci])
        a.probador[v] = pb or []
    a.cuerpo_en = await _film._cuerpo_en(doc)
    return a


def _partes_cuadro(sb: Dict[str, Any], c: Dict[str, Any], a: _Activos, imagen_en: str,
                   ancla: Optional[str], correccion: str) -> Tuple[List[Dict[str, Any]], str]:
    """El pedido de un cuadro, con cada referencia rotulada. Devuelve (partes para Gemini,
    prompt plano para Seedream)."""
    producto = c["tipo"] == "producto"
    v = c["variante"]
    plano = _film._PLANO_CORTO.get(c["plano"], "medium shot")
    ang = _film._ANGULO_CORTO.get(c["angulo"], "eye level")
    imgs: List[Tuple[str, str]] = []
    if a.lugar:
        imgs.append(("the REAL place, empty: the photo is taken INSIDE this exact place — same walls, floor, "
                     "furniture, colours and light; only the camera position changes", a.lugar))
    if ancla and not producto:
        imgs.append(("the first frame of this same reel: same session — same hair, makeup, jewellery and "
                     "light (ignore its pose and framing)", ancla))
    if producto:
        for f in a.reales.get(v, [])[:2]:
            imgs.append(("the REAL product photo: copy the set EXACTLY (design, colour, lace, straps)", f))
    else:
        for k, f in enumerate(a.probador.get(v, [])[:3]):
            vista = ("front", "back", "side")[k] if k < 3 else "view"
            imgs.append((f"HER BODY wearing the set ({vista} view, headless studio reference on grey): copy her "
                         "body, proportions and EXACTLY how the set fits her — ignore the grey background and the "
                         "missing head", f))
        for f in a.reales.get(v, [])[:1]:
            imgs.append(("the REAL product photo: the exact colour, fabric and lace of the set", f))
        for et, f in a.caras:
            imgs.append((f"{et}: THIS is her face — the same exact woman, with this real skin texture (pores, "
                         "moles, fine hair)", f))
    quien = ("Only the lingerie set, no person, with volume and shape (on a hanger, a bust form or held by a hand)."
             if producto else
             "One woman: the face of the face references and the body of the body references, wearing EXACTLY "
             "the set of the references." + (f" Her body: {a.cuerpo_en}." if a.cuerpo_en else ""))
    txt = (f"Vertical 9:16 photo: ONE frame of a real Instagram reel filmed on a phone (a storyboard frame "
           f"that will be animated). FRAMING: {plano}, {ang}. MOMENT: {imagen_en} {quien} "
           + _film._PIEL_REAL + " Real phone photo: natural light with real shadows, slight grain, true colours, "
           "the set sharp and fully visible. Not a studio, not a catalogue pose, no text, no logos, no watermark, "
           "no other people."
           + (f" CORRECTIONS (fix these, change nothing else): {correccion}" if correccion else ""))
    txt = _sanear_prompt_fal(txt)
    partes: List[Dict[str, Any]] = [{"text": txt}]
    for k, (et, f) in enumerate(imgs, 1):
        partes += [{"text": f"IMAGE {k}: {et}."}, _img_part(f)]
    roles = " ".join(f"Image {k} is {et}." for k, (et, _) in enumerate(imgs, 1))
    return partes, _sanear_prompt_fal(txt + " " + roles)


async def _dibujar(sb: Dict[str, Any], c: Dict[str, Any], a: _Activos, ancla: Optional[str],
                   correccion: str = "") -> Tuple[str, str, str, float]:
    """Dibuja un cuadro. Devuelve (b64, motor, nota, costo)."""
    imagen_en = c.get("imagen") or (await _al_ingles({"a": c.get("que_se_ve") or ""})).get("a") or c.get("que_se_ve", "")
    partes, plano_txt = _partes_cuadro(sb, c, a, imagen_en, ancla, correccion)
    settings = dict(await get_settings())
    est = _pricing(settings).get(CALIDAD_CUADRO, 0.10)
    await _cobrar(est)
    try:
        img = await gemini_generate(partes, settings, aspect="9:16", image_size=CALIDAD_CUADRO, save_prompt=False)
        await budget_record("storyboard_cuadro", CALIDAD_CUADRO, est, 1, note="storyboard: cuadro (Gemini)")
        return _compress_ref(img, max_dim=1920, q=92), "gemini", "", est
    except HTTPException as e:
        if e.status_code != 422:
            raise
        motivo = str(e.detail)[:160]
    # Gemini lo rechazó (filtro): Seedream, sin respaldos que cambian la modelo.
    s1 = dict(settings, flux_fallback_model="", _fal_sin_adivinar=True)
    slug = str(settings.get("flux_tryon_model") or "bytedance/seedream/v5/pro/edit")
    precio = float(settings.get("precio_flux", 0.07) or 0.07)
    await _cobrar(precio)
    seedream = [{"text": plano_txt}] + [p for p in partes if "inlineData" in p or "inline_data" in p]
    img = await fal_generate(seedream, s1, "9:16", "2K", slug)
    await budget_record("storyboard_cuadro", slug, precio, 1, note="storyboard: cuadro (Seedream)")
    return (_compress_ref(img, max_dim=1920, q=92), "seedream",
            f"Gemini lo rechazó ({motivo}); lo dibujó Seedream.", est + precio)


# ─────────────────────────────────────────────────────────────────────────────
# PASO 4 · EL VIDEO (cada cuadro aprobado es el primer cuadro de su tramo)
# ─────────────────────────────────────────────────────────────────────────────

def _prompt_video(c: Dict[str, Any], mov_en: str, producto: bool) -> str:
    camara = _film._MOV_CORTO.get(c["movimiento"], "handheld")
    quien = ("" if producto else " @Element1 is her: keep her face, hair and body IDENTICAL to the first frame.")
    prenda = "@Element1" if producto else "@Element2"
    return _sanear_prompt_fal(
        f"Vertical 9:16 phone footage. The video STARTS EXACTLY on the given first frame and continues it "
        f"naturally: {mov_en}. Camera: {camara}, small handheld micro-shakes.{quien} The set is {prenda}: keep "
        "it EXACTLY as in the first frame (design, colour, lace, straps). Calm real-time movement, never slow "
        "motion, real skin and fabric texture, real light. No text, no other people, no cuts.")


async def _filmar_cuadro(cli: httpx.AsyncClient, key: str, headers: Dict[str, str], jid: str,
                         sb: Dict[str, Any], c: Dict[str, Any], u_ella: List[str],
                         u_prenda: Dict[int, List[str]]) -> float:
    sid, cid = sb["id"], c["id"]
    img = await kv.get(_k_img(sid, cid))
    if not img:
        raise RuntimeError("falta la imagen del cuadro")
    producto = c["tipo"] == "producto"
    u_inicio = await _fal_subir(cli, key, await asyncio.to_thread(_film._vertical, img), "image/jpeg",
                                f"sb-{sid}-{cid}.jpg")
    mov_en = c.get("movimiento_video") or (await _al_ingles({"a": c.get("accion") or ""})).get("a") \
        or "she moves naturally, breathing, a small shift of weight"
    prendas = u_prenda.get(c["variante"]) or next((x for x in u_prenda.values() if x), [])
    m = _film.MOTORES[MOTOR_VIDEO]
    payload: Dict[str, Any] = {"prompt": _prompt_video(c, mov_en, producto), "start_image_url": u_inicio,
                               "duration": str(SEG_FILMA), "aspect_ratio": "9:16", "generate_audio": False,
                               "negative_prompt": _film._NEGATIVO, "cfg_scale": 0.5}
    if m["tipo"] == "kling":
        els = ([] if producto else [_film._elemento(u_ella)]) + ([_film._elemento(prendas)] if prendas else [])
        if els:
            payload["elements"] = els
    crudo = _dir(sid) / f"{cid}_crudo.mp4"
    try:
        await _film._fal_video(cli, headers, m["modelo"], payload, f"{jid}-{cid}", ("negative_prompt", "cfg_scale"), crudo)
        costo = round(SEG_FILMA * m["precio_seg"], 3)
        await budget_record("storyboard_video", MOTOR_VIDEO, costo, 1, note="storyboard: tramo")
        await asyncio.to_thread(_film._normalizar, crudo, _clip(sid, cid), None, float(c["seg"]), "centro")
    finally:
        crudo.unlink(missing_ok=True)
    return costo


async def _montar(sb: Dict[str, Any], doc: Dict[str, Any]) -> None:
    """Une los tramos con cortes secos, subtítulos y su voz corrida encima."""
    sid = sb["id"]
    clips = [_clip(sid, c["id"]) for c in sb["cuadros"]]
    falso: Dict[str, Any] = {"id": sid, "modo": "fondo", "ritmo": "segundo", "guion": sb.get("guion") or "",
                             "voz": "", "tono": "cercana", "energia": _film.ENERGIA_FILMADO, "mic": True,
                             "tomas": [], "edicion": {"carteles": False, "zoom": False, "foto_producto": False,
                                                      "cierre": False, "subtitulos": bool(sb.get("subtitulos", True))},
                             "motion": {"subs": sb.get("subs") or "palabras", "fuente": "moderna"}}
    voz: Optional[Tuple[Path, float]] = None
    if falso["guion"].strip():
        clave = hashlib.sha1((falso["guion"] + str(doc.get("voz_motor")) + str(doc.get("voz_clon"))).encode()).hexdigest()[:12]
        p = _dir(sid) / f"voz_{clave}.mp3"
        if not p.exists():
            for viejo in _dir(sid).glob("voz_*.mp3"):
                viejo.unlink(missing_ok=True)
            await _film._voz(doc, falso, falso["guion"], p)
        voz = (p, _duracion_video(p))
    tempo = 1.0
    if voz:
        dv = sum(_duracion_video(c) for c in clips)
        tempo = max(1.0, min(_film.GUION_TEMPO_MAX, (voz[1] + _film.GUION_INICIO + 0.3) / max(1.0, dv)))
        falso["_guion_seg"] = round(voz[1] / tempo, 2)
    durs = [_duracion_video(c) for c in clips]
    texto = _film._armar_ass_filmado(falso, durs)
    ass = ""
    if "Dialogue:" in texto:
        (_dir(sid) / "sb.ass").write_text(texto, encoding="utf-8")
        ass = "sb.ass"
    await asyncio.to_thread(_film._unir, clips, _final(sid), _film.LOOK_DEFAULT, "motor", ["corte"] * len(clips),
                            ["no"] * len(clips), ass, [], _dir(sid))
    if voz:
        await asyncio.to_thread(_film._poner_voz, _final(sid), voz[0], tempo)


# ─────────────────────────────────────────────────────────────────────────────
# TRABAJOS EN SEGUNDO PLANO
# ─────────────────────────────────────────────────────────────────────────────

async def _correr(jid: str, sub: Optional[str], fn, *args) -> None:
    set_current_sub(sub)
    parar = asyncio.Event()
    _spawn(_latir(jid, parar))
    try:
        await _job_set(jid, {"estado": "generando"})
        aviso = await fn(jid, *args)
        await _job_set(jid, {"estado": "listo", "paso": "", "aviso": aviso or ""})
    except Exception as e:
        await _job_set(jid, {"estado": "error", "error": str(getattr(e, "detail", "") or e)[:700]})
    finally:
        parar.set()


async def _job_guion(jid: str, sid: str) -> str:
    await _job_set(jid, {"paso": "Claude está escribiendo el storyboard segundo a segundo…"})
    sb = await _sb(sid)
    doc = await _doc(sb["pid"])
    aviso = ""
    plan: Optional[Dict[str, Any]] = None
    if _claude.disponible():
        try:
            plan, c = await _guion_claude(sb, doc)
            await budget_record("storyboard_claude", _claude.MODELO, c, 1, note="storyboard: guion")
        except _claude.ClaudeNoDisponible as e:
            aviso = f"Claude no pudo ({e}): va una plantilla que podés editar."
    else:
        aviso = "Claude no está disponible: va una plantilla que podés editar."
    async with _lock(sid):
        sb = await _sb(sid)
        for c in sb.get("cuadros") or []:
            await kv.delete(_k_img(sid, c["id"]))
            _clip(sid, c["id"]).unlink(missing_ok=True)
        _final(sid).unlink(missing_ok=True)
        if plan:
            sb.update(plan)
        else:
            sb["cuadros"] = [_limpiar_cuadro(sb, {"plano": p_, "angulo": an, "movimiento": mv, "tipo": tp, "seg": sg,
                                                  "que_se_ve": qv, "imagen": im, "accion": ac, "movimiento_video": mvv})
                             for p_, an, mv, tp, sg, qv, im, ac, mvv in _PLANTILLA]
            sb["guion"] = sb.get("guion") or _film.DICE_DEFAULT
        await _guardar(sb)
    return aviso


async def _job_dibujar(jid: str, sid: str, cids: List[str], correccion: str) -> str:
    sb = await _sb(sid)
    doc = await _doc(sb["pid"])
    await _job_set(jid, {"paso": "Preparando su cara en 4K, el probador de la prenda y el lugar…"})
    a = await _activos(sb, doc)
    cuadros = [c for c in sb["cuadros"] if c["id"] in cids]
    primero = sb["cuadros"][0] if sb.get("cuadros") else None
    ancla: Optional[str] = None
    # El primer cuadro con ella va primero: de ahí salen el pelo, el maquillaje y la luz del resto.
    if primero and primero["tipo"] != "producto":
        if primero["id"] in cids:
            cuadros = [primero] + [c for c in cuadros if c["id"] != primero["id"]]
        else:
            ancla = await kv.get(_k_img(sid, primero["id"]))
    hechos, fallas = [0], []
    sem = asyncio.Semaphore(PARALELO)

    async def uno(c: Dict[str, Any]) -> None:
        nonlocal ancla
        async with sem:
            try:
                b64, motor, nota, costo = await _dibujar(sb, c, a, None if c is primero else ancla,
                                                         correccion if len(cids) == 1 else "")
                await kv.set(_k_img(sid, c["id"]), b64)
                if c is primero:
                    ancla = b64
                cambios = {"img": True, "aprobado": False, "motor_img": motor, "nota_img": nota, "error": "",
                           "clip": False, "ts": int(time.time())}
            except Exception as e:
                fallas.append(f"cuadro {sb['cuadros'].index(c) + 1}")
                cambios = {"error": str(getattr(e, "detail", "") or e)[:300]}
        async with _lock(sid):
            fresco = await _sb(sid)
            for x in fresco["cuadros"]:
                if x["id"] == c["id"]:
                    x.update(cambios)
                    _clip(sid, x["id"]).unlink(missing_ok=True)
            _final(sid).unlink(missing_ok=True)
            await _guardar(fresco)
        hechos[0] += 1
        await _job_set(jid, {"paso": f"Dibujando el storyboard: {hechos[0]} de {len(cuadros)} cuadros…"})

    if cuadros and cuadros[0] is primero:
        await uno(primero)
        cuadros = cuadros[1:]
    await asyncio.gather(*(uno(c) for c in cuadros))
    return ("No salieron: " + ", ".join(fallas) + ". Tocá ↻ en esos cuadros.") if fallas else ""


async def _job_filmar(jid: str, sid: str) -> str:
    sb = await _sb(sid)
    doc = await _doc(sb["pid"])
    key = await _fal_key()
    if not key:
        raise RuntimeError("Falta la API key de fal.")
    a = await _activos(sb, doc)
    faltan = [c for c in sb["cuadros"] if not _clip(sid, c["id"]).exists()]
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    fallas: List[str] = []
    async with httpx.AsyncClient(timeout=300) as cli:
        if faltan:
            await _job_set(jid, {"paso": "Subiendo su cara y la prenda a fal…"})
            ella = [a.caras[0][1]] + ([a.retrato] if a.retrato else [])
            u_ella = [await _fal_subir(cli, key, base64.b64decode(b), "image/jpeg", f"sb-{sid}-ella{i}.jpg")
                      for i, b in enumerate(ella)]
            u_prenda: Dict[int, List[str]] = {}
            for v in {c["variante"] for c in faltan}:
                fotos = (a.probador.get(v) or [])[:2] + (a.reales.get(v) or [])[:1]
                u_prenda[v] = [await _fal_subir(cli, key, base64.b64decode(b), "image/jpeg", f"sb-{sid}-p{v}-{i}.jpg")
                               for i, b in enumerate(fotos)]
            hechos = [0]
            sem = asyncio.Semaphore(PARALELO)

            async def uno(c: Dict[str, Any]) -> None:
                async with sem:
                    try:
                        await _filmar_cuadro(cli, key, headers, jid, sb, c, u_ella, u_prenda)
                        cambios = {"clip": True, "error": ""}
                    except Exception as e:
                        fallas.append(f"cuadro {sb['cuadros'].index(c) + 1}")
                        cambios = {"clip": False, "error": str(getattr(e, "detail", "") or e)[:300]}
                async with _lock(sid):
                    fresco = await _sb(sid)
                    for x in fresco["cuadros"]:
                        if x["id"] == c["id"]:
                            x.update(cambios)
                    await _guardar(fresco)
                hechos[0] += 1
                await _job_set(jid, {"paso": f"Filmando: {hechos[0]} de {len(faltan)} tramos (cada uno desde su cuadro)…"})

            await asyncio.gather(*(uno(c) for c in faltan))
    if fallas:
        raise RuntimeError("No salieron: " + ", ".join(fallas) + ". Los que salieron quedaron: tocá Filmar de nuevo.")
    await _job_set(jid, {"paso": "Uniendo los tramos y poniendo su voz encima…"})
    await _montar(await _sb(sid), doc)
    link = await _guardar_en_drive(f"{_slug(doc.get('nombre', ''))}-storyboard-{sid}.mp4", _final(sid).read_bytes(),
                                   "video/mp4")
    async with _lock(sid):
        fresco = await _sb(sid)
        fresco["video"], fresco["drive"] = True, link
        fresco["seg_final"] = round(_duracion_video(_final(sid)), 1)
        await _guardar(fresco)
    return ""


async def _job_montar(jid: str, sid: str) -> str:
    sb = await _sb(sid)
    if not all(_clip(sid, c["id"]).exists() for c in sb["cuadros"]):
        raise RuntimeError("Faltan tramos filmados.")
    await _job_set(jid, {"paso": "Uniendo los tramos y poniendo su voz encima…"})
    await _montar(sb, await _doc(sb["pid"]))
    async with _lock(sid):
        fresco = await _sb(sid)
        fresco["video"] = True
        fresco["seg_final"] = round(_duracion_video(_final(sid)), 1)
        await _guardar(fresco)
    return ""


async def _lanzar(sb: Dict[str, Any], tipo: str, estimado: int, fn, *args) -> str:
    jid = _uuid.uuid4().hex[:10]
    await _job_nuevo(jid, sb["pid"], "storyboard_" + tipo, estimado, {"titulo": "Storyboard", "sid": sb["id"]})
    async with _lock(sb["id"]):
        fresco = await _sb(sb["id"])
        fresco["job"] = jid
        await _guardar(fresco)
    _spawn(_correr(jid, CURRENT_SUB.get(), fn, sb["id"], *args))
    return jid


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(dependencies=[Depends(_bind)])


def _costo_cuadro() -> float:
    return 0.10


def _vista(sb: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(sb)
    precio_seg = _film.MOTORES[MOTOR_VIDEO]["precio_seg"]
    cuadros = []
    for c in sb.get("cuadros") or []:
        x = dict(c)
        x["url"] = f"{API}/{sb['id']}/cuadro/{c['id']}.jpg?t={c.get('ts', 0)}" if c.get("img") else ""
        x["clip_url"] = f"{API}/{sb['id']}/cuadro/{c['id']}/clip.mp4?t={c.get('ts', 0)}" if _clip(sb["id"], c["id"]).exists() else ""
        cuadros.append(x)
    out["cuadros"] = cuadros
    out["seg_total"] = sum(c["seg"] for c in cuadros)
    out["faltan_img"] = sum(1 for c in cuadros if not c.get("img"))
    out["sin_aprobar"] = sum(1 for c in cuadros if c.get("img") and not c.get("aprobado"))
    out["costo_cuadros"] = round(out["faltan_img"] * _costo_cuadro(), 2)
    out["costo_video"] = round(sum(SEG_FILMA * precio_seg for c in cuadros if not c.get("clip_url")), 2)
    out["video_url"] = f"{API}/{sb['id']}/video.mp4?t={sb.get('actualizado', '')}" if _final(sb["id"]).exists() else ""
    return out


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(PAGINA.replace("%%API%%", API).replace("%%VERSION%%", VERSION))


@router.get(API + "/config")
async def api_config() -> Dict[str, Any]:
    return {"planos": {k: v[0] for k, v in _film.PLANOS.items()}, "angulos": {k: v[0] for k, v in _film.ANGULOS.items()},
            "movimientos": {k: v[0] for k, v in _film.MOVIMIENTOS.items()}, "tipos": TIPOS, "duraciones": DURACIONES,
            "claude": _claude.disponible(), "costo_cuadro": _costo_cuadro(),
            "costo_seg_video": _film.MOTORES[MOTOR_VIDEO]["precio_seg"] * SEG_FILMA}


@router.get(API + "/lista")
async def api_lista() -> Dict[str, Any]:
    out = []
    for sid in (await kv.get(_k_idx())) or []:
        d = await kv.get(_k_sb(sid))
        if isinstance(d, dict):
            out.append({"id": sid, "titulo": d.get("titulo") or "Storyboard", "creado": d.get("creado"),
                        "cuadros": len(d.get("cuadros") or []), "video": bool(d.get("video"))})
    return {"storyboards": out}


@router.post(API + "/nuevo")
async def api_nuevo(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    doc = await _doc(str(payload.get("pid") or ""))
    if not (doc.get("hoja") or {}).get("retrato"):
        raise HTTPException(400, "Esa modelo todavía no tiene retrato aprobado (Personajes → ficha).")
    prenda = await refs_luma.prenda(str(payload.get("prenda") or ""))
    if not prenda:
        raise HTTPException(400, "Elegí una prenda de Mis prendas (Referencias → 👗 Mis prendas).")
    colores = []
    for ci in payload.get("colores") or [0]:
        try:
            ci = int(ci)
        except (TypeError, ValueError):
            continue
        if 0 <= ci < len(prenda.get("colores") or []) and ci not in colores:
            colores.append(ci)
    if not colores:
        colores = [0]
    lugar = str(payload.get("lugar_ref") or "")
    sid = "s" + _uuid.uuid4().hex[:9]
    sb = {"id": sid, "pid": doc["id"], "prenda": prenda["id"], "colores": colores,
          "colores_nombres": [prenda["colores"][ci]["nombre"] for ci in colores],
          "lugar_ref": lugar if (lugar.isalnum() and len(lugar) <= 16) else "",
          "duracion": int(payload.get("duracion")) if str(payload.get("duracion") or "").isdigit()
          and int(payload.get("duracion")) in DURACIONES else 15,
          "idea": _texto(payload.get("idea"), 600), "titulo": prenda.get("nombre") or "Storyboard",
          "guion": "", "cuadros": [], "subtitulos": True, "creado": time.strftime("%Y-%m-%d %H:%M")}
    await _guardar(sb)
    await kv.set(_k_idx(), [sid] + [x for x in ((await kv.get(_k_idx())) or []) if x != sid][:40])
    jid = await _lanzar(sb, "guion", 120, _job_guion)
    return {"storyboard": _vista(await _sb(sid)), "job": jid}


@router.get(API + "/job/{jid}")
async def api_job(jid: str) -> Dict[str, Any]:
    j = await kv.get(_k_job(jid))
    if not isinstance(j, dict):
        raise HTTPException(404, "Ese trabajo no existe.")
    j = await _revisar_job(j)
    return {k: j.get(k) for k in ("estado", "paso", "error", "aviso", "inicio")}


@router.get(API + "/{sid}")
async def api_get(sid: str) -> Dict[str, Any]:
    return {"storyboard": _vista(await _sb(sid))}


@router.put(API + "/{sid}")
async def api_editar(sid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    async with _lock(sid):
        sb = await _sb(sid)
        for k, tope in (("guion", 1500), ("idea", 600), ("titulo", 80)):
            if k in payload:
                sb[k] = _texto(payload[k], tope)
        if "subtitulos" in payload:
            sb["subtitulos"] = payload["subtitulos"] is not False
        if str(payload.get("duracion") or "").isdigit() and int(payload["duracion"]) in DURACIONES:
            sb["duracion"] = int(payload["duracion"])
        if any(k in payload for k in ("guion", "subtitulos")):
            _final(sid).unlink(missing_ok=True)
        await _guardar(sb)
    return {"storyboard": _vista(sb)}


@router.post(API + "/{sid}/guion")
async def api_guion(sid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    sb = await _sb(sid)
    if "idea" in payload or "duracion" in payload:
        await api_editar(sid, payload)
        sb = await _sb(sid)
    return {"job": await _lanzar(sb, "guion", 120, _job_guion)}


@router.put(API + "/{sid}/cuadro/{cid}")
async def api_cuadro(sid: str, cid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    async with _lock(sid):
        sb = await _sb(sid)
        c = next((x for x in sb["cuadros"] if x["id"] == cid), None)
        if not c:
            raise HTTPException(404, "Ese cuadro no existe.")
        imagen_cambia = False
        for k, validos in (("plano", _film.PLANOS), ("angulo", _film.ANGULOS), ("tipo", TIPOS)):
            if payload.get(k) in validos and payload[k] != c[k]:
                c[k], imagen_cambia = payload[k], True
        if payload.get("movimiento") in _film.MOVIMIENTOS:
            c["movimiento"] = payload["movimiento"]
            c["clip"] = False
        if "que_se_ve" in payload and _texto(payload["que_se_ve"], 400) != c["que_se_ve"]:
            c["que_se_ve"], c["imagen"], imagen_cambia = _texto(payload["que_se_ve"], 400), "", True
        if "accion" in payload and _texto(payload["accion"], 300) != c["accion"]:
            c["accion"], c["movimiento_video"], c["clip"] = _texto(payload["accion"], 300), "", False
        if "seg" in payload and _seg(payload["seg"]) != c["seg"]:
            c["seg"], c["clip"] = _seg(payload["seg"]), False
        if "variante" in payload:
            try:
                v = max(0, min(len(sb["colores"]) - 1, int(payload["variante"])))
            except (TypeError, ValueError):
                v = c["variante"]
            if v != c["variante"]:
                c["variante"], imagen_cambia = v, True
        if imagen_cambia:
            c["aprobado"] = False
            c["desactualizada"] = bool(c.get("img"))
        if not c.get("clip"):
            _clip(sid, cid).unlink(missing_ok=True)
        _final(sid).unlink(missing_ok=True)
        await _guardar(sb)
    return {"storyboard": _vista(sb)}


@router.post(API + "/{sid}/cuadro")
async def api_cuadro_nuevo(sid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    async with _lock(sid):
        sb = await _sb(sid)
        if len(sb["cuadros"]) >= MAX_CUADROS:
            raise HTTPException(400, f"Hasta {MAX_CUADROS} cuadros.")
        c = _limpiar_cuadro(sb, {"que_se_ve": "Escribí qué se ve en este cuadro.", "seg": 2})
        idx = next((i + 1 for i, x in enumerate(sb["cuadros"]) if x["id"] == payload.get("despues")), len(sb["cuadros"]))
        sb["cuadros"].insert(idx, c)
        _final(sid).unlink(missing_ok=True)
        await _guardar(sb)
    return {"storyboard": _vista(sb)}


@router.delete(API + "/{sid}/cuadro/{cid}")
async def api_cuadro_borrar(sid: str, cid: str) -> Dict[str, Any]:
    async with _lock(sid):
        sb = await _sb(sid)
        sb["cuadros"] = [x for x in sb["cuadros"] if x["id"] != cid]
        await kv.delete(_k_img(sid, cid))
        _clip(sid, cid).unlink(missing_ok=True)
        _final(sid).unlink(missing_ok=True)
        await _guardar(sb)
    return {"storyboard": _vista(sb)}


@router.post(API + "/{sid}/dibujar")
async def api_dibujar(sid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Dibuja cuadros: los pedidos (`cids`), los que faltan, o los primeros N (`prueba`)."""
    sb = await _sb(sid)
    if not sb.get("cuadros"):
        raise HTTPException(400, "Primero el guion segundo a segundo.")
    cids = [x for x in (payload.get("cids") or []) if any(c["id"] == x for c in sb["cuadros"])]
    if not cids:
        pend = [c["id"] for c in sb["cuadros"] if not c.get("img") or c.get("desactualizada")]
        n = int(payload.get("prueba") or 0)
        cids = pend[:n] if n else pend
    if not cids:
        raise HTTPException(400, "No falta dibujar ningún cuadro (para rehacer uno, tocá ↻ en ese cuadro).")
    for c in sb["cuadros"]:
        if c["id"] in cids:
            c.pop("desactualizada", None)
    await _guardar(sb)
    return {"job": await _lanzar(sb, "dibujar", 60 + 40 * len(cids), _job_dibujar, cids,
                                 _texto(payload.get("correccion"), 400))}


@router.post(API + "/{sid}/cuadro/{cid}/aprobar")
async def api_aprobar(sid: str, cid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    async with _lock(sid):
        sb = await _sb(sid)
        for c in sb["cuadros"]:
            if c["id"] == cid or (cid == "todos" and c.get("img")):
                if not c.get("img"):
                    raise HTTPException(400, "Ese cuadro todavía no tiene imagen.")
                c["aprobado"] = payload.get("ok") is not False
        await _guardar(sb)
    return {"storyboard": _vista(sb)}


@router.post(API + "/{sid}/cuadro/{cid}/subir")
async def api_subir(sid: str, cid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Una imagen tuya para ese cuadro (por ejemplo una foto de Fotos que salió perfecta)."""
    try:
        b64 = _compress_ref(base64.b64decode(_strip_data_url(str(payload.get("imagen") or ""))), max_dim=1920, q=92)
    except Exception:
        raise HTTPException(400, "No pude leer la imagen.")
    async with _lock(sid):
        sb = await _sb(sid)
        c = next((x for x in sb["cuadros"] if x["id"] == cid), None)
        if not c:
            raise HTTPException(404, "Ese cuadro no existe.")
        await kv.set(_k_img(sid, cid), b64)
        c.update({"img": True, "aprobado": True, "motor_img": "subida", "nota_img": "", "error": "",
                  "clip": False, "ts": int(time.time())})
        c.pop("desactualizada", None)
        _clip(sid, cid).unlink(missing_ok=True)
        _final(sid).unlink(missing_ok=True)
        await _guardar(sb)
    return {"storyboard": _vista(sb)}


@router.get(API + "/{sid}/cuadro/{cid}.jpg")
async def api_img(sid: str, cid: str):
    b = await kv.get(_k_img(sid, cid))
    if not b:
        raise HTTPException(404, "Ese cuadro no tiene imagen.")
    return Response(base64.b64decode(b), media_type="image/jpeg")


@router.get(API + "/{sid}/cuadro/{cid}/clip.mp4")
async def api_clip(sid: str, cid: str):
    p = _clip(sid, cid)
    if not p.exists():
        raise HTTPException(404, "Ese tramo no está filmado.")
    return FileResponse(p, media_type="video/mp4")


@router.post(API + "/{sid}/filmar")
async def api_filmar(sid: str) -> Dict[str, Any]:
    sb = await _sb(sid)
    v = _vista(sb)
    if not sb.get("cuadros"):
        raise HTTPException(400, "Primero el storyboard.")
    sin = [str(i + 1) for i, c in enumerate(sb["cuadros"]) if not (c.get("img") and c.get("aprobado"))]
    if sin:
        raise HTTPException(400, f"Primero aprobá todos los cuadros del storyboard (falta{'n' if len(sin) > 1 else ''}: "
                                 f"{', '.join(sin)}). Nada se filma sin aprobar.")
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal.")
    await _cobrar(v["costo_video"])
    return {"job": await _lanzar(sb, "filmar", 90 + 60 * math.ceil(len(sb["cuadros"]) / PARALELO), _job_filmar)}


@router.post(API + "/{sid}/montar")
async def api_montar(sid: str) -> Dict[str, Any]:
    sb = await _sb(sid)
    return {"job": await _lanzar(sb, "montar", 60, _job_montar)}


@router.get(API + "/{sid}/video.mp4")
async def api_video(sid: str):
    await _sb(sid)
    p = _final(sid)
    if not p.exists():
        raise HTTPException(404, "Todavía no está el video.")
    return FileResponse(p, media_type="video/mp4", filename=f"storyboard-{sid}.mp4")


@router.delete(API + "/{sid}")
async def api_borrar(sid: str) -> Dict[str, Any]:
    sb = await _sb(sid)
    for c in sb.get("cuadros") or []:
        await kv.delete(_k_img(sid, c["id"]))
    await kv.delete(_k_sb(sid))
    await kv.set(_k_idx(), [x for x in ((await kv.get(_k_idx())) or []) if x != sid])
    for p in _dir(sid).glob("*"):
        p.unlink(missing_ok=True)
    return {"ok": True}


PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Storyboard · Studio Luma</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;--rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f}
  *{box-sizing:border-box} body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:16px;line-height:1.5}
  main{max-width:1180px;margin:0 auto;padding:16px} .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:16px}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:22px;margin:0 0 6px} h3{font-size:17px;margin:0 0 6px;font-weight:500}
  .hint{color:var(--ink-soft);font-size:13px;margin:4px 0 8px} label{display:block;font-size:13px;color:var(--ink-soft);margin:10px 0 4px}
  input,textarea,select{width:100%;background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:9px 11px;font:inherit;font-size:14px}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:9px 16px;font:inherit;font-size:14px;cursor:pointer}
  button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500} button:disabled{opacity:.45;cursor:default}
  button.sm{padding:5px 11px;font-size:12.5px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px} .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
  @media(max-width:700px){.row,.row3{grid-template-columns:1fr}}
  .fila{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px} a{color:var(--rose-deep)}
  .mal{color:var(--bad)} .bien{color:var(--ok)}
  .paso{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--rose-deep);margin-bottom:4px}
  .grilla{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-top:10px}
  .cu{background:var(--card-2);border:1px solid var(--line);border-radius:14px;padding:8px;font-size:13px}
  .cu.ok{border-color:var(--ok)} .cu .img{width:100%;aspect-ratio:9/16;border-radius:10px;background:#0e0d12 center/cover no-repeat;display:flex;align-items:center;justify-content:center;color:var(--ink-soft);font-size:12px;text-align:center;padding:10px;position:relative}
  .cu .num{position:absolute;top:6px;left:6px;background:rgba(0,0,0,.6);border-radius:99px;padding:1px 8px;font-size:12px}
  .cu .seg{position:absolute;top:6px;right:6px;background:rgba(0,0,0,.6);border-radius:99px;padding:1px 8px;font-size:12px}
  .cu textarea{font-size:12.5px;padding:6px 8px} .cu select,.cu input{font-size:12px;padding:5px 6px}
  .cu label{margin:6px 0 2px;font-size:11.5px} .mini{display:grid;grid-template-columns:1fr 1fr;gap:6px}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid var(--ink-soft);border-top-color:transparent;border-radius:50%;animation:g 1s linear infinite;vertical-align:middle;margin-right:6px} @keyframes g{to{transform:rotate(360deg)}}
  video{width:100%;max-width:360px;border-radius:12px;display:block;margin-top:10px}
  .estado{margin-top:8px;font-size:14px}
</style></head><body><main>
<div class="card">
  <h2>🎬 Storyboard <small style="font-family:Jost;font-size:12px;color:var(--ink-soft)">v%%VERSION%%</small></h2>
  <p class="hint">La base de los videos hiperrealistas: <b>1)</b> Claude escribe el guion segundo a segundo · <b>2)</b> Gemini dibuja cada cuadro con
  <b>tu lugar</b>, <b>su cuerpo con la prenda</b> (el probador sin cabeza) y <b>su cara en 4K</b> · <b>3)</b> vos aprobás el storyboard ·
  <b>4)</b> cada cuadro aprobado arranca su tramo de video y se une con su voz. Nada se filma sin aprobar.</p>
  <p class="hint">Antes: la modelo con su <b>Cara HD</b> (Personajes → ficha), la prenda con su <b>probador</b> y tu lugar (<a href="/referencias" target="_blank">Referencias</a>).</p>
  <div id="lista" class="hint"></div>
</div>

<div class="card" id="c1">
  <div class="paso">Paso 1</div><h3>Qué reel querés</h3>
  <div class="row"><div><label>Modelo</label><select id="pid"></select></div><div><label>Prenda (Mis prendas)</label><select id="prenda"></select></div></div>
  <div id="colores" class="fila"></div>
  <div class="row"><div><label>Lugar (Mis lugares)</label><select id="lugar"></select></div><div><label>Duración</label><select id="duracion"></select></div></div>
  <label>Idea (opcional): qué querés contar o mostrar</label>
  <textarea id="idea" rows="2" placeholder="ej: probador del local, se lo prueba y muestra lo cómodo que es; cerrar con 'escribime por DM'"></textarea>
  <div class="fila"><button class="go" id="crear">✍️ Escribir el storyboard</button><span class="estado" id="e1"></span></div>
</div>

<div class="card" id="c2" style="display:none">
  <div class="paso">Paso 2 y 3</div><h3 id="titulo">El storyboard</h3>
  <p class="hint">Cada cuadro: qué se ve, plano, ángulo y segundos. Si cambiás algo, ese cuadro se vuelve a dibujar. Revisá que sea <b>ella</b>, la <b>prenda exacta</b> y <b>tu lugar</b>;
  si no, escribí qué corregir y tocá ↻. Aprobá con ✓.</p>
  <div class="fila">
    <button class="go" id="prueba">🧪 Dibujar los 3 primeros (prueba)</button>
    <button id="todos">🎨 Dibujar los que faltan</button>
    <button id="aprobarTodos">✓ Aprobar todos los dibujados</button>
    <button id="rehacerGuion">✍️ Reescribir el guion</button>
    <span class="hint" id="costos"></span>
  </div>
  <div class="estado" id="e2"></div>
  <div class="grilla" id="grilla"></div>
  <label>🎙️ Su voz (una sola narración encima de todo; se graba con la voz de su ficha)</label>
  <textarea id="guion" rows="3"></textarea><div class="hint" id="guionInfo"></div>
</div>

<div class="card" id="c3" style="display:none">
  <div class="paso">Paso 4</div><h3>El video</h3>
  <p class="hint">Cada cuadro aprobado es el primer cuadro exacto de su tramo: el video sigue desde esa imagen, se corta a sus segundos y se une con su voz.</p>
  <div class="fila"><button class="go" id="filmar">🎥 Filmar el storyboard</button><button id="montar">✂️ Volver a unir (sin filmar)</button>
    <label style="margin:0;display:flex;gap:6px;align-items:center"><input type="checkbox" id="subs" style="width:auto"> Subtítulos</label></div>
  <div class="estado" id="e3"></div>
  <div id="salida"></div>
</div>
<script>
const API = "%%API%%"; let CFG = {}, SB = null, PRENDAS = [], SIGUIENDO = null;
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(p, o = {}) { const r = await fetch(API + p, Object.assign({headers: {"Content-Type": "application/json"}}, o)); let d = {}; try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status)); return d; }
const post = (p, b) => api(p, {method: "POST", body: JSON.stringify(b || {})});
const put = (p, b) => api(p, {method: "PUT", body: JSON.stringify(b || {})});
const opts = (obj, sel) => Object.entries(obj).map(([k, v]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(v)}</option>`).join("");
const EMBED = new URLSearchParams(location.search).get("embed");
if(EMBED) new ResizeObserver(() => parent.postMessage({cambiosAlto: document.documentElement.scrollHeight, de: "storyboard"}, "*")).observe(document.body);

function pintarColores(){ const p = PRENDAS.find(x => x.id === $("#prenda").value);
  $("#colores").innerHTML = p ? '<span class="hint" style="margin:0">Colores:</span>' + p.colores.map((c, i) => `<label style="margin:0;display:flex;gap:4px;align-items:center"><input type="checkbox" value="${i}" ${i === 0 ? "checked" : ""} style="width:auto">${esc(c.nombre)}</label>`).join("") : '<span class="hint">Cargá la prenda en Referencias → Mis prendas.</span>'; }

async function seguir(jid, est){ if(!jid) return; SIGUIENDO = jid; const t0 = Date.now(); bloquear(true);
  try{ for(;;){ const j = await api("/job/" + jid);
      if(j.estado === "listo"){ est.innerHTML = j.aviso ? `<span class="mal">${esc(j.aviso)}</span>` : '<span class="bien">Listo.</span>'; break; }
      if(j.estado === "error"){ est.innerHTML = `<span class="mal">${esc(j.error || "Falló")}</span>`; break; }
      const s = Math.round((Date.now() - (j.inicio ? j.inicio * 1000 : t0)) / 1000);
      est.innerHTML = `<span class="spin"></span>${esc(j.paso || "En cola…")} <span class="hint">${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}</span>`;
      if(SB){ try{ SB = (await api("/" + SB.id)).storyboard; pintar(); }catch(e){} }
      await new Promise(r => setTimeout(r, 4000)); } }
  catch(e){ est.textContent = "Falló: " + e.message; }
  SIGUIENDO = null; bloquear(false); if(SB){ SB = (await api("/" + SB.id)).storyboard; pintar(); } lista(); }
function bloquear(on){ ["#crear", "#prueba", "#todos", "#filmar", "#montar", "#rehacerGuion"].forEach(k => $(k).disabled = on); }

function pintar(){
  if(!SB) return; $("#c2").style.display = ""; $("#c3").style.display = (SB.cuadros || []).length ? "" : "none";
  $("#titulo").textContent = "El storyboard · " + (SB.titulo || "") + ` · ${SB.cuadros.length} cuadros · ${SB.seg_total} s`;
  if(document.activeElement !== $("#guion")) $("#guion").value = SB.guion || "";
  const pal = (SB.guion || "").trim().split(/\s+/).filter(Boolean).length;
  $("#guionInfo").textContent = `${pal} palabras ≈ ${Math.round(pal / 2.6)} s de voz · el storyboard dura ${SB.seg_total} s` + (pal / 2.6 > SB.seg_total + 1 ? " · ⚠ la voz es más larga: acortala o sumá cuadros" : "");
  $("#costos").textContent = (SB.faltan_img ? `faltan ${SB.faltan_img} cuadros (~US$${(SB.faltan_img * CFG.costo_cuadro).toFixed(2)})` : "storyboard dibujado")
    + (SB.sin_aprobar ? ` · ${SB.sin_aprobar} sin aprobar` : "");
  $("#subs").checked = SB.subtitulos !== false;
  const listos = SB.cuadros.length && SB.cuadros.every(c => c.img && c.aprobado);
  $("#filmar").textContent = listos ? `🎥 Filmar el storyboard (~US$${SB.costo_video})` : "🎥 Filmar (primero aprobá todos los cuadros)";
  $("#filmar").disabled = !listos || !!SIGUIENDO; $("#montar").style.display = SB.cuadros.every(c => c.clip_url) ? "" : "none";
  $("#salida").innerHTML = SB.video_url ? `<video src="${SB.video_url}" controls playsinline></video><div class="fila"><a href="${SB.video_url}" download>⬇️ Bajar</a>${SB.drive ? ` · <a href="${SB.drive}" target="_blank">En tu Drive</a>` : ""}</div>` : "";
  const colores = Object.fromEntries((SB.colores_nombres || []).map((n, i) => [String(i), n]));
  $("#grilla").innerHTML = SB.cuadros.map((c, i) => `<div class="cu ${c.aprobado ? "ok" : ""}" data-c="${c.id}">
    <div class="img" style="${c.url ? `background-image:url('${c.url}')` : ""}"><span class="num">${i + 1}</span><span class="seg">${c.seg} s</span>${c.url ? "" : (c.error ? `<span class="mal">${esc(c.error)}</span>` : "Sin dibujar")}</div>
    ${c.desactualizada ? '<div class="mal hint">Cambiaste el cuadro: volvé a dibujarlo (↻).</div>' : ""}
    ${c.nota_img ? `<div class="hint">${esc(c.nota_img)}</div>` : ""}
    ${c.clip_url ? `<video src="${c.clip_url}" controls playsinline preload="none" style="margin-top:6px"></video>` : ""}
    <label>Qué se ve</label><textarea data-k="que_se_ve" rows="2">${esc(c.que_se_ve)}</textarea>
    <div class="mini"><div><label>Plano</label><select data-k="plano">${opts(CFG.planos, c.plano)}</select></div><div><label>Ángulo</label><select data-k="angulo">${opts(CFG.angulos, c.angulo)}</select></div></div>
    <div class="mini"><div><label>Cámara</label><select data-k="movimiento">${opts(CFG.movimientos, c.movimiento)}</select></div><div><label>Segundos</label><select data-k="seg">${opts({"1": "1 s", "2": "2 s", "3": "3 s"}, String(c.seg))}</select></div></div>
    <div class="mini"><div><label>Quién</label><select data-k="tipo">${opts(CFG.tipos, c.tipo)}</select></div>${Object.keys(colores).length > 1 ? `<div><label>Color</label><select data-k="variante">${opts(colores, String(c.variante))}</select></div>` : "<div></div>"}</div>
    <label>Qué se mueve</label><input data-k="accion" value="${esc(c.accion)}">
    <label>Qué corregir al rehacer</label><input data-cor placeholder="ej: la cara es más fina, el corpiño es de encaje">
    <div class="fila" style="margin-top:6px">${c.img ? (c.aprobado ? '<span class="bien">✓ Aprobado</span> <button class="sm" data-a="noap">Quitar</button>' : '<button class="sm go" data-a="ap">✓ Aprobar</button>') : ""}
      <button class="sm" data-a="re">${c.img ? "↻ Rehacer" : "🎨 Dibujar"}</button>
      <label class="sm" style="margin:0;cursor:pointer;color:var(--rose-deep)"><input type="file" accept="image/*" data-sub style="display:none">⬆ Mía</label>
      <button class="sm" data-a="mas" title="Agregar un cuadro después">＋</button><button class="sm" data-a="del" title="Borrar">🗑</button></div></div>`).join("");
  document.querySelectorAll(".cu").forEach(el => { const cid = el.dataset.c;
    el.querySelectorAll("[data-k]").forEach(x => x.onchange = async () => { const b = {}; b[x.dataset.k] = x.value;
      try{ SB = (await put(`/${SB.id}/cuadro/${cid}`, b)).storyboard; pintar(); }catch(e){ $("#e2").textContent = e.message; } });
    el.querySelectorAll("[data-a]").forEach(b => b.onclick = async () => { try{ const a = b.dataset.a;
      if(a === "ap" || a === "noap"){ SB = (await post(`/${SB.id}/cuadro/${cid}/aprobar`, {ok: a === "ap"})).storyboard; pintar(); }
      if(a === "re"){ const d = await post(`/${SB.id}/dibujar`, {cids: [cid], correccion: el.querySelector("[data-cor]").value}); seguir(d.job, $("#e2")); }
      if(a === "mas"){ SB = (await post(`/${SB.id}/cuadro`, {despues: cid})).storyboard; pintar(); }
      if(a === "del"){ if(!confirm("¿Borrar este cuadro?")) return; SB = (await api(`/${SB.id}/cuadro/${cid}`, {method: "DELETE"})).storyboard; pintar(); }
    }catch(e){ $("#e2").textContent = e.message; } });
    el.querySelector("[data-sub]").onchange = async e => { const f = e.target.files[0]; if(!f) return;
      const imagen = await new Promise((ok, mal) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = mal; r.readAsDataURL(f); });
      try{ SB = (await post(`/${SB.id}/cuadro/${cid}/subir`, {imagen})).storyboard; pintar(); }catch(er){ $("#e2").textContent = er.message; } }; });
}
async function abrir(sid){ SB = (await api("/" + sid)).storyboard; try{ localStorage.setItem("sb_id", sid); }catch(e){} pintar();
  if(SB.job){ try{ const j = await api("/job/" + SB.job); if(j.estado === "generando" || j.estado === "en_cola") seguir(SB.job, $("#e2")); }catch(e){} } }
async function lista(){ try{ const l = (await api("/lista")).storyboards;
  $("#lista").innerHTML = l.length ? "Tus storyboards: " + l.map(x => `<a href="#" data-s="${x.id}">${esc(x.titulo)}</a> (${x.cuadros} cuadros${x.video ? ", 🎬" : ""})`).join(" · ") : "";
  document.querySelectorAll("[data-s]").forEach(a => a.onclick = e => { e.preventDefault(); abrir(a.dataset.s); }); }catch(e){} }

$("#crear").onclick = async () => { try{
    const colores = Array.from(document.querySelectorAll("#colores input:checked")).map(x => +x.value);
    const d = await post("/nuevo", {pid: $("#pid").value, prenda: $("#prenda").value, colores, lugar_ref: $("#lugar").value, duracion: +$("#duracion").value, idea: $("#idea").value});
    SB = d.storyboard; try{ localStorage.setItem("sb_id", SB.id); }catch(e){} pintar(); seguir(d.job, $("#e2")); $("#c2").scrollIntoView({behavior: "smooth"}); }
  catch(e){ $("#e1").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#prueba").onclick = async () => { try{ const d = await post(`/${SB.id}/dibujar`, {prueba: 3}); seguir(d.job, $("#e2")); }catch(e){ $("#e2").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#todos").onclick = async () => { try{ const d = await post(`/${SB.id}/dibujar`, {}); seguir(d.job, $("#e2")); }catch(e){ $("#e2").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#aprobarTodos").onclick = async () => { try{ SB = (await post(`/${SB.id}/cuadro/todos/aprobar`, {ok: true})).storyboard; pintar(); }catch(e){ $("#e2").textContent = e.message; } };
$("#rehacerGuion").onclick = async () => { if(!confirm("¿Reescribir el guion? Se reemplazan los cuadros (y sus dibujos).")) return;
  try{ const d = await post(`/${SB.id}/guion`, {}); seguir(d.job, $("#e2")); }catch(e){ $("#e2").textContent = e.message; } };
$("#guion").onchange = async () => { try{ SB = (await put("/" + SB.id, {guion: $("#guion").value})).storyboard; pintar(); }catch(e){ $("#e2").textContent = e.message; } };
$("#subs").onchange = async () => { try{ SB = (await put("/" + SB.id, {subtitulos: $("#subs").checked})).storyboard; pintar(); }catch(e){} };
$("#filmar").onclick = async () => { try{ const d = await post(`/${SB.id}/filmar`); seguir(d.job, $("#e3")); }catch(e){ $("#e3").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#montar").onclick = async () => { try{ const d = await post(`/${SB.id}/montar`); seguir(d.job, $("#e3")); }catch(e){ $("#e3").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#prenda").onchange = pintarColores;
(async () => {
  CFG = await api("/config");
  $("#duracion").innerHTML = CFG.duraciones.map(d => `<option value="${d}" ${d === 15 ? "selected" : ""}>${d} s</option>`).join("");
  try{ const pj = await (await fetch("/personajes/api/lista")).json(); $("#pid").innerHTML = (pj.personajes || []).map(p => `<option value="${esc(p.id)}">${esc(p.nombre)}</option>`).join(""); }catch(e){}
  try{ PRENDAS = (await (await fetch("/referencias/api/prendas")).json()).prendas || [];
    $("#prenda").innerHTML = PRENDAS.map(p => `<option value="${esc(p.id)}">${esc(p.nombre)}</option>`).join("") || '<option value="">Primero cargá una prenda en Referencias</option>'; }catch(e){}
  pintarColores();
  try{ const ml = (await (await fetch("/referencias/api/lugares")).json()).lugares || [];
    $("#lugar").innerHTML = '<option value="">— Sin lugar (no recomendado) —</option>' + ml.filter(l => (l.vistas || []).length).map(l => `<option value="${esc(l.id)}">${esc(l.nombre)}</option>`).join("");
    if(ml.some(l => (l.vistas || []).length)) $("#lugar").selectedIndex = 1; }catch(e){}
  lista();
  let sid = null; try{ sid = localStorage.getItem("sb_id"); }catch(e){}
  if(sid){ try{ await abrir(sid); }catch(e){} }
})();
</script></main></body></html>"""
