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
import json
import math
import os
import re
import subprocess
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

import claude_director as _claude
import filmado as _film
import higgsfield_luma as hf
import video_motor as VM
import referencias_luma as refs_luma
import motion_luma as motion
from estudio3d_luma import LUCES_3D, PAGINA_3D, POSES_3D
from imagenes_ia import (
    CURRENT_SUB,
    FIDELITY_FABRIC,
    _al_ingles,
    _compress_ref,
    _img_part,
    _pfx,
    _pricing,
    _sanear_prompt_fal,
    _strip_data_url,
    a_json_generico,
    budget_record,
    fal_generate,
    ficha_to_text,
    gemini_analyze,
    gemini_con_json,
    gemini_generate,
    get_settings,
    kv,
    set_current_sub,
    verificar_prenda,
)
from imagenes_ia import ANALYZE_MODEL, _current_api_key, _qc_call
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

VERSION = "2.9.1"
ROUTE_PREFIX = os.environ.get("STORYBOARD_PREFIX", "/storyboard").rstrip("/")
API = ROUTE_PREFIX + "/api"
DIR = PJ_DIR / "storyboard"
DIR.mkdir(parents=True, exist_ok=True)

DURACIONES = (10, 15, 20, 30)
MAX_CUADROS = 8
# Cada cuadro es un MOMENTO de 3 a 5 s filmado de corrido con su minilínea de tiempo: se pide
# y se paga exactamente lo que dura (antes eran cuadros de 2 s y se pagaban 3).
SEG_CUADRO_MIN, SEG_CUADRO_MAX = 3, 5
SEG_FILMA = 3                      # lo mínimo que filma Kling
PARALELO = 3
MOTOR_VIDEO = "kling_pro"            # el de fal (respaldo)
# EL MOTOR DEL VIDEO: Higgsfield (más barato) con Kling 3.0 imagen a video (el cuadro es el
# primer cuadro), o fal con Kling O3 (además recibe a ella y la prenda como referencia).
MOTORES_VIDEO: Dict[str, Dict[str, Any]] = {
    "hf_kling3_pro": {"label": "Kling 3.0 Pro · Higgsfield", "hf": os.getenv("HIGGSFIELD_KLING_PRO",
                      "kling-video/v3.0/pro/image-to-video"), "precio_seg": float(os.getenv("HIGGSFIELD_PRECIO_PRO", "0.095"))},
    "hf_kling3_std": {"label": "Kling 3.0 Standard · Higgsfield", "hf": os.getenv("HIGGSFIELD_KLING_STD",
                      "kling-video/v3.0/standard/image-to-video"), "precio_seg": float(os.getenv("HIGGSFIELD_PRECIO_STD", "0.071"))},
    "fal_kling_o3": {"label": "Kling O3 · fal (con ella y la prenda de referencia)", "fal": "kling_pro",
                     "precio_seg": _film.MOTORES["kling_pro"]["precio_seg"]},
    # EL MOTOR COMÚN (video_motor.py): modelo y proveedor a elección (fal · Higgsfield · Kling directo).
    "vm": {"label": "🎛 Elegir modelo y proveedor (Kling 4.0, 3.0, Seedance… por fal, Higgsfield o Kling)",
           "precio_seg": 0.0},
    # SIN CUADROS: Kling recibe sus referencias (cara, cuerpo con la prenda, el lugar) y el pedido de cada
    # momento, y CREA el video. No se dibuja ni se aprueba nada antes.
    "vm_ref": {"label": "🧩 Kling con referencias, sin cuadros (su cara, la prenda y el lugar + el pedido)",
               "precio_seg": 0.0},
}


def _vm_ok(vm: Any, tipo: str = "") -> bool:
    return (isinstance(vm, dict) and vm.get("modelo") in VM.MODELOS
            and (not tipo or VM.MODELOS[vm["modelo"]].get("tipo", "i2v") == tipo)
            and bool(VM.rutas(str(vm.get("modelo")), str(vm.get("proveedor")), "pro" if vm.get("calidad") == "pro" else "std")))


def _es_ref(sb: Dict[str, Any]) -> bool:
    """Modo SIN CUADROS: Kling crea cada momento con sus referencias."""
    return _motor_video(sb) == "vm_ref"


def _mv_de(sb: Dict[str, Any]) -> Dict[str, Any]:
    """El motor del storyboard con su precio (el común lo calcula con el modelo y el proveedor elegidos)."""
    m = _motor_video(sb)
    if m in ("vm", "vm_ref"):
        vm = sb["motor_vm" if m == "vm" else "motor_vm_ref"]
        cal = "pro" if vm.get("calidad") == "pro" else "std"
        return {"label": f"{VM.MODELOS[vm['modelo']]['nombre']} · {VM.PROVEEDORES.get(vm['proveedor'], '')}",
                "precio_seg": VM.precio_seg(vm["modelo"], vm["proveedor"], cal, False)}
    return MOTORES_VIDEO[m]


def _precio_tramo(mv: Dict[str, Any], seg: Any = SEG_FILMA) -> float:
    """Lo que cuesta filmar un momento de `seg` segundos con ese motor (siempre sin sonido: con
    sonido Kling cobra 1,5x)."""
    return max(SEG_FILMA, int(seg or SEG_FILMA)) * float(mv["precio_seg"])


def _motor_video(sb: Dict[str, Any]) -> str:
    """El motor elegido en ese storyboard; si no eligió, Kling 3.0 por Higgsfield cuando hay clave."""
    m = sb.get("motor_video")
    if m == "vm":
        return "vm" if _vm_ok(sb.get("motor_vm")) else ("hf_kling3_pro" if hf.disponible() else "fal_kling_o3")
    if m == "vm_ref":
        return "vm_ref" if _vm_ok(sb.get("motor_vm_ref"), "ref") else ("hf_kling3_pro" if hf.disponible() else "fal_kling_o3")
    if m in MOTORES_VIDEO and (not m.startswith("hf_") or hf.disponible()):
        return m
    # Sin elegir: Kling 3.0 Pro por Higgsfield (lo más barato que tiene la marca); sin clave, fal.
    return "hf_kling3_pro" if hf.disponible() else "fal_kling_o3"
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


def _k_guia(sid: str, cid: str) -> str:
    """La foto del Estudio 3D (maniquí y cámara): la guía de pose del cuadro."""
    return _pfx() + f"sb:{sid}:guia:{cid}"


def _k_img_fin(sid: str, cid: str) -> str:
    """El CUADRO FINAL del momento (cuando arranca de espalda y se da vuelta: con su cara)."""
    return _pfx() + f"sb:{sid}:imgfin:{cid}"


def _con_final(c: Dict[str, Any]) -> bool:
    return c.get("tipo") != "producto" and bool(c.get("final") or c.get("final_es"))


def _sin_cara(c: Dict[str, Any]) -> bool:
    """El cuadro de arranque no muestra su cara: el video no tiene de dónde sacarla."""
    return c.get("tipo") != "producto" and (c.get("angulo") == "espalda" or c.get("plano") == "detalle")


def _va_por_fal(sb: Dict[str, Any], c: Dict[str, Any]) -> bool:
    """Higgsfield (imagen a video) sólo recibe el cuadro de arranque: si ahí no se le ve la cara, o
    el momento tiene cuadro final, va por fal (Kling O3 con su cara de referencia y el final)."""
    return _con_final(c) or _sin_cara(c)


def _k_img_prev(sid: str, cid: str) -> str:
    """La imagen anterior del cuadro (para comparar motores y volver atrás)."""
    return _pfx() + f"sb:{sid}:imgprev:{cid}"


# EL MOTOR DE LOS CUADROS: Gemini (Nano Banana) o Marketing Studio de Higgsfield (mucho más
# barato; mismo pedido y mismas referencias rotuladas). Se elige por storyboard y se puede
# rehacer un cuadro con el otro para compararlos.
MOTORES_CUADRO: Dict[str, Dict[str, Any]] = {
    "gemini": {"label": "Gemini (Nano Banana)", "precio": 0.10},
    "hf_marketing": {"label": "Marketing Studio · Higgsfield", "precio": float(os.getenv("HIGGSFIELD_PRECIO_MS", "0.011")),
                     "hf": os.getenv("HIGGSFIELD_MARKETING", "marketing-studio/image")},
}


def _motor_cuadro(sb: Dict[str, Any], pedido: str = "") -> str:
    m = pedido or sb.get("motor_cuadros") or "gemini"
    if m not in MOTORES_CUADRO or (m.startswith("hf_") and not hf.disponible()):
        return "gemini"
    return m


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
        return 4


def _num(v: Any, lo: float, hi: float, defecto: float) -> float:
    try:
        return round(max(lo, min(hi, float(v))), 1)
    except (TypeError, ValueError):
        return defecto


def _linea_de(c: Dict[str, Any]) -> Tuple[str, str]:
    """La minilínea de tiempo del momento: (castellano para vos, inglés para el video). Claude
    la manda como [{"t": "0-2", "es": "...", "en": "..."}]; editada a mano llega como texto."""
    lin = c.get("linea")
    if isinstance(lin, list):
        es, en = [], []
        for b in lin[:5]:
            if not isinstance(b, dict):
                continue
            t = _texto(b.get("t"), 12).replace(" ", "").replace("s", "")
            if str(b.get("es") or "").strip():
                es.append(f"{t} s: {_texto(b['es'], 180)}")
            if str(b.get("en") or "").strip():
                en.append(f"{t}s: {_texto(b['en'], 220)}")
        return "\n".join(es), "; ".join(en)
    return _texto(lin, 800), _texto(c.get("linea_en"), 1000)


def _limpiar_cuadro(sb: Dict[str, Any], c: Dict[str, Any]) -> Dict[str, Any]:
    n_col = max(1, len(sb.get("colores") or []))
    try:
        var = max(0, min(n_col - 1, int(c.get("variante") or 0)))
    except (TypeError, ValueError):
        var = 0
    linea, linea_en = _linea_de(c)
    return {"id": "c" + _uuid.uuid4().hex[:7], "linea": linea, "linea_en": linea_en,
            "habla": bool(c.get("habla")) and c.get("tipo") != "producto", "dice": _texto(c.get("dice"), 300),
            "cartel": _texto(c.get("cartel"), 40), "cartel_t": _num(c.get("cartel_t"), 0, 4.5, 1.0),
            "cartel_gesto": bool(c.get("cartel_gesto")) and c.get("tipo") != "producto",
            "final": _texto(c.get("final"), 500), "final_es": _texto(c.get("final_es"), 300), "img_fin": False,
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

_SYSTEM_PREGUNTAS = (
    "You are the creative director of a hyper-realistic Instagram reel for an Argentine lingerie brand, "
    "filmed by AI with the brand's model. BEFORE writing the storyboard you ask the owner what you need. "
    "Study the product photos, the place and the model's portrait.\n"
    "Ask 4 to 6 SHORT, concrete questions in Spanish from Argentina (voseo, casual), each with 2 to 4 "
    "suggested answers she can tap. ALWAYS ask about her LOOK for this reel, because it must be the same "
    "in every second: her HAIR (loose, soft waves, wet look, ponytail, bun… propose what suits her face "
    "and hair in the portrait), her MAKEUP (natural, soft glam, red lips…) and NAILS / JEWELLERY (none, "
    "nude nails, a thin gold chain, small hoops…). Then, ONLY if it is not already in what she told you: "
    "the PRICE and sizes to show as text on screen, a promo, the angle of the reel (launch, restock, gift, "
    "best seller…) and how it ends (the call to action). Never ask what you already know.\n"
    'Answer in JSON: {"resumen": "one sentence in Spanish: what you understood of the set and the reel", '
    '"preguntas": [{"pregunta": "...", "opciones": ["...", "..."]}]}'
)

_HABLA = (
    "- SHE TALKS TO THE CAMERA in 1 or 2 moments (the hook and/or the close), like a real creator: habla = "
    "true, plano primer or medio, angulo ojos, she looks into the lens, 4 or 5 seconds, its linea is about how "
    "she talks (small head movements, a smile, a hand gesture), and its \"dice\" is what she says to the "
    "camera (short and natural: it is lip-synced). Never more than 2.\n"
)

# UN SOLO VIDEO DE CORRIDO: el director sabe que Kling filma todo junto desde las referencias (sin fotos de arranque)
# y escribe el video entero segundo a segundo, para que sea consistente de punta a punta.
_CORRIDO_INTRO = (
    "You are the creative director of Instagram reels for an Argentine lingerie brand. Write the STORYBOARD of a "
    "{dur}-second reel that will be filmed as ONE SINGLE CONTINUOUS VIDEO by a video model (Kling) in one go, "
    "from reference photos of the brand's model, of the set and of the real place. There are NO first frames and "
    "NO separate clips: the whole reel is one take (or a few motivated cuts inside the same video). The reel is "
    "described in MOMENTS only so the owner can read it; they all happen in the SAME place, with the SAME light, "
    "the SAME look and the SAME set, and each one flows into the next.\n")
_CORRIDO_REGLAS = (
    "ONE CONTINUOUS VIDEO — extra rules that override anything above:\n"
    "- Consistency first: one place, one time of day, one light, one hairstyle and makeup, one set from the first "
    "to the last second. Each moment starts exactly where the previous one ended (her position, her pose, the "
    "camera). Changes of framing are done by moving the camera (push-in, pull-back, orbit, tilt) or by a clean "
    "cut on her action — never a jump to another place.\n"
    "- habla is ALWAYS false (if she should talk to the camera, the owner decides at the end).\n"
    "- CAMERA PLAN THAT KEEPS HER IDENTICAL (video models deform people in some shots): camera at her eye or chest "
    "height, NEVER a low angle (it stretches the neck and legs); a natural 50-85 mm look, never wide-angle close to "
    "her; mostly medium, americano and close shots; at most ONE full-body shot and never as the last shot; when "
    "she turns, she turns SLOWLY and the turn ends in a medium shot with her face to the lens; no extreme "
    "close-ups of the neck; the last seconds are a medium close-up (chest up) of her face.\n"
    "- REAL, NOT AI-LOOKING: never a frontal, centred, symmetrical face close-up like an ID photo (it is the shot "
    "that looks most fake); faces are framed slightly off-centre, three-quarter or turning, chest up, never tighter "
    "than the shoulders. Candid instead of posed: she looks away and then back to the lens, a small natural smile or "
    "a soft laugh with the lips barely open (no big toothy smile), a breath, a hand fixing the hair. Light always "
    "has a direction (from a window or a lamp of the place), never flat front light.\n"
    "- \"prompt_en\": the WHOLE video for the video model as a LIST, SECOND BY SECOND covering 0 to {dur} with no "
    "gaps: [{{\"t\": \"0-2s\", \"shot\": \"medium shot, eye level, 50mm\", \"camera\": \"slow push-in\", "
    "\"action\": \"…\"}}, …]. Name her ALWAYS as @Element1, the set as @Element2 and the place as @Image1. "
    "Each span: ONE simple physical action, where she is in the place, the framing and how the camera moves. "
    "Do not describe her face or body (they come from the references).\n"
    "- \"cinematografia\": the DIRECTOR OF PHOTOGRAPHY sheet for THIS reel, in English, chosen for the idea, the "
    "place and the light of the place: {{\"camera\": body and sensor, \"lens\": exact primes (between 40 and 85 mm "
    "on her), \"frame_rate\", \"shutter\", \"aperture\", \"iso\", \"focus\", \"lighting\": {{\"key\", \"fill\", "
    "\"rim\", \"falloff\", \"practicals\", \"color_temperature\", \"never\"}} (motivated by the real light of "
    "the place and the time of day: ONE main source from the side with real shadows on her face, a 3:1 to 4:1 "
    "ratio, light that falls off across the room, mixed colour temperatures; never ring light or flat frontal "
    "light — flat light is what makes video look AI-generated), \"grade\": colour grade and film emulation, \"texture\": grain, halation, skin, \"movement_style\": "
    "rig and feel of the moves}}. One look for the whole video.\n")
_SYSTEM_GUION = (
    "You are the creative director of Instagram reels for an Argentine lingerie brand. Write the "
    "STORYBOARD of a {dur}-second reel {estilo_camara}. The "
    "reel is made of MOMENTS. Every moment is drawn first as ONE exact photo (from reference photos of the "
    "brand's model, of the set worn on her body and of the real place) and then filmed as ONE continuous "
    "clip of 3 to 5 seconds that STARTS on that photo and follows a second-by-second TIMELINE. Between "
    "moments there are hard cuts.\n"
    "RULES:\n"
    "- {n} moments of 3, 4 or 5 WHOLE seconds, {dur} s in total (exactly).\n"
    "- Every moment has a \"linea\": its timeline, 2 or 3 beats that cover all its seconds in order, "
    "e.g. [{{\"t\": \"0-2\", ...}}, {{\"t\": \"2-4\", ...}}]. Each beat is ONE simple, physical, "
    "continuous action a video model can do without cutting (she turns her hips a quarter, her fingers "
    "trace the lace, she lets the strap snap back, she looks back over her shoulder, the camera slowly "
    "pushes in). Never more than one action per beat, never a change of place, angle or outfit inside a "
    "moment.\n"
    "- Hook in the first moment. Then the reveal, details, fit and back, and the close.\n"
    "- Each moment changes the \"plano\" AND the \"angulo\" versus the previous one; include at least one "
    "detail moment (lace, fabric, straps, closure) and one where the back of the set is seen.\n"
    "- Her FACE clearly visible (close-up, medium, mirror, facing the camera) in the first or second "
    "moment and at least every 8 seconds.\n"
    "- The garment always has VOLUME: worn by her, held in her hand or on a hanger — never flat. The SAME "
    "woman in every moment, never other people.\n"
    "- {colores}\n"
    "- Sensual, confident and natural, like the best lingerie try-on reels, never explicit.\n"
    "- Her look (hair, makeup, nails, jewellery) is what the owner answered, IDENTICAL in every moment.\n"
    "{habla}"
    "FIELDS of each moment: \"seg\" (3-5), \"tipo\" (\"ella\" or \"producto\" = the set alone with "
    "volume), \"variante\" (colour index, 0 to {max_var}), \"plano\" (one of {planos}), \"angulo\" (one "
    "of {angulos}), \"movimiento\" (camera move, one of {movimientos}), \"que_se_ve\" (Spanish from "
    "Argentina, one short sentence for the owner), \"imagen\" (English, max 50 words: the exact STILL "
    "first frame of the moment — her pose, where she is in the place, what of the set is clearly visible; "
    "do not describe her face or body, they come from the references), \"linea\" (the timeline: list of "
    "{{\"t\": \"0-2\", \"es\": \"Spanish, short\", \"en\": \"English, max 20 words\"}}), \"accion\" "
    "(Spanish, the whole moment in a few words), \"final\" and \"final_es\" (ONLY when the moment starts "
    "with her back to the camera or her face out of frame and she turns or looks back so her face is seen at "
    "the end: the exact STILL LAST frame, English max 40 words / Spanish one sentence; otherwise both empty. "
    "That last frame is drawn with her real face so the video keeps it), \"habla\" (true when she TALKS to the camera in this moment), \"dice\" "
    "(Spanish from Argentina: the words of her voice during THIS moment, max {pal_seg} words per second of the "
    "moment — said on camera when habla, voice-over otherwise), \"cartel_t\" (the second inside the moment when the cartel appears), "
    "\"cartel_gesto\" (true when SHE presents it with her hand: see PRICE below), \"cartel\" (optional on-screen text, max 30 characters: the "
    "price, the sizes, a benefit or the promo — ONLY with data the owner gave you; empty if none).\n"
    "Also: \"guion\": her VOICE over the whole reel, ONE continuous narration in Spanish from Argentina "
    "(Rioplatense, voseo, natural like a real influencer: short phrases, natural pauses with \"...\", a hook "
    "first and the call to action at the end; no hashtags, no emojis), about {palabras} words. \"estilo_en\": "
    "her look in English, one sentence (hair, makeup, nails, jewellery) as the owner answered. \"precio\": "
    "the price as the owner wrote it, or empty. \"cierre\": the final on-screen card, {{\"titulo\": \"max 30 "
    "characters (the price or the name of the set)\", \"linea\": \"max 40 characters, the call to action\"}}. "
    "And \"titulo\" (Spanish, short).\n"
    "PRICE WITH A GESTURE (this is what makes it feel real): if the owner gave a price, ONE moment facing the "
    "camera (medio or americano) presents it: in its linea, at second N she raises her open hand beside her at "
    "shoulder height, palm up, as if presenting something floating next to her, and holds it about 1.5 seconds "
    "while she smiles at the lens; that moment has cartel = the price, cartel_t = N, cartel_gesto = true (the "
    "price appears floating over her palm). If she was also told sizes, a promo or a benefit, another moment "
    "can show it the same way (pointing with her index finger to the empty side of the frame). Every other "
    "cartel is a plain text card (cartel_gesto false).\n"
    "Do not invent prices, sizes or promos nobody told you.\n"
    'Answer in JSON: {{"titulo": "...", "guion": "...", "estilo_en": "...", "precio": "", "cierre": {{"titulo": '
    '"...", "linea": "..."}}, "cuadros": [{{"seg": 4, "tipo": "ella", "variante": 0, "plano": "...", '
    '"angulo": "...", "movimiento": "...", "que_se_ve": "...", "imagen": "...", "linea": [{{"t": "0-2", '
    '"es": "...", "en": "..."}}, {{"t": "2-4", "es": "...", "en": "..."}}], "accion": "...", "habla": false, "dice": "...", "cartel": "", "cartel_t": 1, "cartel_gesto": false}}]}}'
)


async def _contexto(sb: Dict[str, Any], doc: Dict[str, Any]) -> str:
    prenda = await refs_luma.prenda(sb["prenda"]) or {}
    lugar = await refs_luma.lugar(sb.get("lugar_ref") or "") if sb.get("lugar_ref") else None
    cuerpo = await _film._cuerpo_en(doc)
    qa = "\n".join(f"- {q['pregunta']} → {q.get('respuesta') or '(sin respuesta: decidí vos)'}"
                   for q in sb.get("preguntas") or [])
    return (f"THE SET: {prenda.get('nombre', '')}. {prenda.get('desc', '')}\n"
            f"COLOURS IN THIS REEL (index: name): "
            + ", ".join(f"{i}: {c}" for i, c in enumerate(sb.get("colores_nombres") or [])) + "\n"
            f"THE PLACE: {(lugar or {}).get('nombre', '')} — {(lugar or {}).get('desc', '') or 'see the photo'}\n"
            f"HER BODY: {cuerpo}\n"
            f"WHAT THE OWNER WANTS: {sb.get('idea') or '(decide yourself: a try-on reel that sells the set)'}"
            + ("\nTHE OWNER ANSWERED:\n" + qa if qa else ""))


async def _partes_producto(sb: Dict[str, Any], doc: Dict[str, Any], con_ella: bool = False) -> List[Dict[str, Any]]:
    """Lo que Claude mira: el contexto, la prenda en cada color, el lugar y (para preguntar por
    el pelo y el maquillaje) el retrato de ella."""
    partes: List[Dict[str, Any]] = [{"text": await _contexto(sb, doc)}]
    for i, ci in enumerate(sb.get("colores") or []):
        fotos = await refs_luma.fotos_color(sb["prenda"], ci)
        if fotos:
            partes += [{"text": f"Product photo, colour {i}:"}, _img_part(_compress_ref(base64.b64decode(fotos[0]),
                                                                                       max_dim=900, q=85))]
    lug = await refs_luma.vista_para(sb["lugar_ref"], "entero") if sb.get("lugar_ref") else None
    if lug:
        partes += [{"text": "The place (empty):"}, _img_part(_compress_ref(base64.b64decode(lug), max_dim=900, q=85))]
    if con_ella:
        refs = await _refs_identidad(doc)
        if refs:
            partes += [{"text": "The model (her portrait: her face and her hair today):"},
                       _img_part(_compress_ref(base64.b64decode(refs[0][1]), max_dim=900, q=85))]
    return partes


async def _preguntas_claude(sb: Dict[str, Any], doc: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
    data, costo = await _claude.pedir_json(_SYSTEM_PREGUNTAS, await _partes_producto(sb, doc, True), max_tokens=4000,
                                           esfuerzo=os.getenv("STORYBOARD_ESFUERZO", "medium"))
    preguntas = []
    for q in (data.get("preguntas") or [])[:6]:
        if isinstance(q, dict) and str(q.get("pregunta") or "").strip():
            preguntas.append({"pregunta": _texto(q["pregunta"], 300),
                              "opciones": [_texto(o, 120) for o in (q.get("opciones") or [])[:4] if str(o).strip()],
                              "respuesta": ""})
    if not preguntas:
        raise _claude.ClaudeNoDisponible("Claude no devolvió preguntas.")
    return {"preguntas": preguntas, "resumen": _texto(data.get("resumen"), 400)}, costo


async def _guion_claude(sb: Dict[str, Any], doc: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
    dur = int(sb.get("duracion") or 15)
    n_col = len(sb.get("colores") or [])
    system = _SYSTEM_GUION.format(
        dur=dur, n=f"{max(2, round(dur / 5))} to {min(MAX_CUADROS, dur // 3)}",
        colores=("Several colours: show them in order, the change of colour on a cut between moments (the "
                 "previous moment ends with her hand covering the lens)." if n_col > 1 else "One colour (variante 0)."),
        max_var=max(0, n_col - 1), planos=", ".join(_film.PLANOS),
        angulos=", ".join(f"{k} ({v[1]})" for k, v in _film.ANGULOS.items()),
        movimientos=", ".join(_film.MOVIMIENTOS), palabras=int(dur * 2.3), pal_seg=2.3,
        estilo_camara=("filmed like a HIGH-END LINGERIE COMMERCIAL with a cinema camera: elegant cinematic "
                       "language (slow dolly-ins, steadicam, arcs around her, crane from above, close details "
                       "with shallow depth of field), no phone tricks like covering the lens" if _es_cine(sb)
                       else "like the hyper-realistic AI reels that look filmed on a phone"),
        habla=(_HABLA if sb.get("hablar", True) is not False else
               "- She never talks to the camera: habla is always false (her voice is voice-over).\n"))
    if sb.get("corrido"):
        system = _CORRIDO_INTRO.format(dur=dur) + system[system.index("RULES:"):] + _CORRIDO_REGLAS.format(dur=dur)
    partes = await _partes_producto(sb, doc)
    data, costo = await _claude.pedir_json(system, partes, max_tokens=12000,
                                           esfuerzo=os.getenv("STORYBOARD_ESFUERZO", "medium"))
    cuadros = [_limpiar_cuadro(sb, c) for c in (data.get("cuadros") or [])[:MAX_CUADROS] if isinstance(c, dict)]
    cuadros = [c for c in cuadros if c["imagen"] or c["que_se_ve"]]
    if len(cuadros) < 3:
        raise _claude.ClaudeNoDisponible("Claude no devolvió un storyboard usable.")
    _ajustar_segundos(cuadros, dur)
    if sum(1 for c in cuadros if c["habla"]) > 2:
        for c in [c for c in cuadros if c["habla"]][2:]:
            c["habla"] = False
    cierre = data.get("cierre") if isinstance(data.get("cierre"), dict) else {}
    return {"titulo": _texto(data.get("titulo"), 80), "guion": _texto(data.get("guion"), 1500),
            "estilo_en": _texto(data.get("estilo_en"), 400), "precio": _texto(data.get("precio"), 40),
            "cierre": {"titulo": _texto(cierre.get("titulo"), 40), "linea": _texto(cierre.get("linea"), 60)},
            "cuadros": cuadros,
            **({"cine_en": data["cinematografia"]} if sb.get("corrido") and isinstance(data.get("cinematografia"), dict)
               else {}),
            **({"prompt_corrido_en": (json.dumps(data["prompt_en"], ensure_ascii=False, indent=1)[:4000]
                                      if isinstance(data.get("prompt_en"), list) else _texto(data.get("prompt_en"), 3000))}
               if sb.get("corrido") else {})}, costo


def _ajustar_segundos(cuadros: List[Dict[str, Any]], dur: int) -> None:
    """Que los momentos sumen la duración pedida (cada uno entre 3 y 5 s)."""
    for _ in range(40):
        total = sum(c["seg"] for c in cuadros)
        if total == dur:
            return
        if total < dur:
            c = min((x for x in cuadros if x["seg"] < SEG_CUADRO_MAX), key=lambda x: x["seg"], default=None)
        else:
            c = max((x for x in cuadros if x["seg"] > SEG_CUADRO_MIN), key=lambda x: x["seg"], default=None)
        if not c:
            return
        c["seg"] += 1 if total < dur else -1


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
        self.fichas: Dict[int, Dict[str, Any]] = {}     # color → ficha técnica (la misma de Fotos)
        self.cuerpo_en: str = ""
        self.hoja: List[Tuple[str, str]] = []           # su hoja de identidad (retrato, cuerpo, perfil, espalda)


async def _ficha(fid: str, ci: int, fotos: List[str]) -> Dict[str, Any]:
    """La ficha técnica de la prenda en ese color, como la arma Fotos (tela, color, costuras,
    escote, espalda, errores a evitar), con su versión en inglés para el video. Se hace una vez."""
    k = _pfx() + f"sb:ficha:{fid}:{ci}"
    f = await kv.get(k)
    if isinstance(f, dict) and "render_en" in f:
        return f
    if not fotos:
        return {}
    try:
        f = await gemini_analyze(fotos[:3])
    except Exception:
        return {}
    if not isinstance(f, dict):
        return {}
    negs = "; ".join(str(x) for x in (f.get("negativos_sugeridos") or []) if str(x).strip())
    try:
        en = await _al_ingles({"r": str(f.get("ficha_para_render") or ""), "n": negs})
    except Exception:
        en = {}
    f["render_en"], f["neg_en"] = en.get("r") or "", en.get("n") or ""
    await kv.set(k, f)
    return f


async def _activos(sb: Dict[str, Any], doc: Dict[str, Any]) -> _Activos:
    a = _Activos()
    if sb.get("lugar_ref"):
        a.lugar = await refs_luma.vista_para(sb["lugar_ref"], "medio")
    refs = await _refs_identidad(doc)
    if not refs:
        raise HTTPException(400, "Este personaje todavía no tiene retrato aprobado.")
    a.retrato = refs[0][1]
    a.hoja = refs
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
        a.reales[v] = fotos[:3]
        a.fichas[v] = await _ficha(sb["prenda"], ci, fotos)
        pb = await refs_luma.probador_de(sb["prenda"], ci, doc["id"])
        if not pb and fotos:
            # Si todavía no tiene probador para esta modelo, se hace ahora (queda en Mis prendas).
            pb = await _film.probador_para(doc, fotos, refs, ficha=[sb["prenda"], ci])
        a.probador[v] = pb or []
    a.cuerpo_en = await _film._cuerpo_en(doc)
    return a


def _partes_cuadro(sb: Dict[str, Any], c: Dict[str, Any], a: _Activos, imagen_en: str,
                   ancla: Optional[str], correccion: str, inicio: Optional[str] = None,
                   guia: Optional[str] = None) -> Tuple[List[Dict[str, Any]], str]:
    """El pedido de un cuadro, con cada referencia rotulada. Devuelve (partes para Gemini,
    prompt plano para Seedream)."""
    producto = c["tipo"] == "producto"
    v = c["variante"]
    plano = _film._PLANO_CORTO.get(c["plano"], "medium shot")
    ang = _film._ANGULO_CORTO.get(c["angulo"], "eye level")
    imgs: List[Tuple[str, str]] = []
    if guia:
        # La guía del Estudio 3D va PRIMERA: de ahí sale la composición (pose, lugar en el
        # cuadro, ángulo y distancia de la cámara). NO es su cuerpo ni su ropa ni el lugar.
        imgs.append(("the POSE AND CAMERA GUIDE, a 3D blocking with grey mannequins: copy EXACTLY the pose of "
                     "the mannequin (head, torso, arms, hands, legs), where it stands in the frame, and the camera "
                     "angle, height, distance and lens. It is NOT her body, skin, face, clothes or the place: ignore "
                     "the grey material, the floor and the backdrop", guia))
    if a.lugar:
        imgs.append(("the REAL place, empty: the photo is taken INSIDE this exact place — same walls, floor, "
                     "furniture, colours and light; only the camera position changes", a.lugar))
    if inicio:
        # El cuadro FINAL de un momento: la misma escena segundos después (se dio vuelta).
        imgs.append(("the FIRST frame of this same continuous shot, a few seconds earlier: the SAME place, light, "
                     "camera, set and HAIR (same hairstyle seen from another side); only her pose has changed", inicio))
    if ancla and not producto:
        imgs.append(("the first frame of this same reel where her face and hair are seen from the front: same "
                     "session — the SAME hairstyle, hair colour (roots included), makeup, jewellery and light "
                     "(ignore its pose and framing)", ancla))
    ficha = a.fichas.get(v) or {}
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
             "the set of the references." + (f" Her body: {a.cuerpo_en}." if a.cuerpo_en else "")
             + (f" HER LOOK, identical in every frame of the reel: {sb['estilo_en']}." if sb.get("estilo_en") else ""))
    if not producto and (c.get("angulo") == "espalda" or inicio):
        quien += (" HER HAIR seen from behind or from the side is the SAME hairstyle as in the references, just "
                  "from another angle: same colour all over (do not add darker roots or other tones), same shape, "
                  "height and position of the bun or ponytail, same loose strands.")
    if inicio:
        quien += (" This is the LAST frame of the shot: she has turned and her FACE is clearly visible, and it "
                  "must be EXACTLY the face of the face references (same eyes, nose, lips, jaw, freckles and moles).")
    prenda_en = (f" THE SET (technical sheet): {ficha['render_en']}" if ficha.get("render_en") else "") + \
        (f" AVOID: {ficha['neg_en']}." if ficha.get("neg_en") else "")
    if guia:
        plano, ang = "exactly as the pose and camera guide", (c.get("guia_desc") or "as the guide")
    if _es_cine(sb):
        cabeza = ("Vertical 9:16 FILM STILL from a high-end lingerie commercial shot on an ARRI Alexa 35 with "
                  "anamorphic lenses (a storyboard frame that will be animated)")
        estilo = (" Cinematic: motivated light with real shadows, shallow depth of field with soft creamy bokeh, "
                  "subtle halation on highlights, fine 35mm film grain, rich natural colour grade, the set in "
                  "sharp focus and fully visible. Not a catalogue pose, no text, no logos, no watermark, no other "
                  "people.")
    else:
        cabeza = ("Vertical 9:16 photo: ONE frame of a real Instagram reel filmed on a phone (a storyboard frame "
                  "that will be animated)")
        estilo = (" Real phone photo: natural light with real shadows, slight grain, true colours, the set sharp "
                  "and fully visible. Not a studio, not a catalogue pose, no text, no logos, no watermark, no other "
                  "people.")
    txt = (f"{cabeza}. FRAMING: {plano}, {ang}. MOMENT: {imagen_en} {quien} " + _film._PIEL_REAL + estilo + (f" LIGHT: {LUCES_3D[sb['luz']]}." if sb.get("luz") in LUCES_3D else "") + prenda_en
           + (f" CORRECTIONS (fix these, change nothing else): {correccion}" if correccion else ""))
    txt = _sanear_prompt_fal(txt)
    partes: List[Dict[str, Any]] = [{"text": txt}]
    # Lo mismo que usa Fotos para la prenda: la ficha completa y las reglas de tela y costuras.
    ficha_txt = ficha_to_text(ficha)
    if ficha_txt:
        partes.append({"text": "FICHA TÉCNICA DE LA PRENDA (copiala exacta):\n" + ficha_txt})
    partes.append({"text": FIDELITY_FABRIC})
    for k, (et, f) in enumerate(imgs, 1):
        partes += [{"text": f"IMAGE {k}: {et}."}, _img_part(f)]
    roles = " ".join(f"Image {k} is {et}." for k, (et, _) in enumerate(imgs, 1))
    return partes, _sanear_prompt_fal(roles + " " + txt)


async def _dibujar_hf(partes: List[Dict[str, Any]], plano_txt: str) -> bytes:
    """El cuadro con Marketing Studio de Higgsfield: el mismo pedido (con los roles de cada
    imagen) y las mismas referencias, en el mismo orden."""
    imgs = [p.get("inlineData", p.get("inline_data", {})).get("data") for p in partes
            if "inlineData" in p or "inline_data" in p]
    async with httpx.AsyncClient(timeout=300) as cli:
        key = await _fal_key()
        urls = []
        for i, b in enumerate(imgs[:16]):
            chico = _compress_ref(base64.b64decode(b), max_dim=1600, q=90)
            if key:
                urls.append(await _fal_subir(cli, key, base64.b64decode(chico), "image/jpeg", f"sb-ref-{i}.jpg"))
            else:
                urls.append("data:image/jpeg;base64," + chico)
        payload: Dict[str, Any] = {"prompt": plano_txt[:3800], "image_urls": urls, "aspect_ratio": "9:16",
                                   "resolution": os.getenv("HIGGSFIELD_MS_RESOLUCION", "2k"), "enhance_prompt": False,
                                   "quality": os.getenv("HIGGSFIELD_MS_CALIDAD", "high")}
        return await hf.imagen(cli, MOTORES_CUADRO["hf_marketing"]["hf"], payload)


async def _dibujar(sb: Dict[str, Any], c: Dict[str, Any], a: _Activos, ancla: Optional[str],
                   correccion: str = "", motor: str = "gemini", inicio: Optional[str] = None) -> Tuple[str, str, str, float]:
    """Dibuja un cuadro (o, con `inicio`, el cuadro FINAL de ese momento). Devuelve (b64, motor,
    nota, costo)."""
    if inicio:
        imagen_en = c.get("final") or (await _al_ingles({"a": c.get("final_es") or ""})).get("a") or c.get("final_es", "")
    else:
        imagen_en = c.get("imagen") or (await _al_ingles({"a": c.get("que_se_ve") or ""})).get("a") or c.get("que_se_ve", "")
    guia = None if inicio else (await kv.get(_k_guia(sb["id"], c["id"])) if c.get("guia") else None)
    partes, plano_txt = _partes_cuadro(sb, c, a, imagen_en, ancla, correccion, inicio, guia)
    nota_hf = ""
    if motor == "hf_marketing":
        precio_hf = MOTORES_CUADRO["hf_marketing"]["precio"]
        await _cobrar(precio_hf)
        try:
            img = await _dibujar_hf(partes, plano_txt)
            await budget_record("storyboard_cuadro", "hf_marketing", precio_hf, 1, note="storyboard: cuadro (Higgsfield)")
            return _compress_ref(img, max_dim=1920, q=92), "hf_marketing", "", precio_hf
        except hf.HiggsfieldAceptado as e:
            raise RuntimeError(str(e)[:300])      # ya aceptado: no se paga dos veces con Gemini
        except Exception as e:
            nota_hf = f"Marketing Studio falló ({str(e)[:140]}); "
    settings = dict(await get_settings())
    est = _pricing(settings).get(CALIDAD_CUADRO, 0.10)
    await _cobrar(est)
    try:
        # el pedido en JSON (prendido por defecto) y, si el texto se bloquea por el pedido, otra vez en JSON
        con_json = sb.get("prompt_json", True)
        pedido = {"framing": f"{c.get('plano', '')}, {c.get('angulo', '')}", "what_is_seen": c.get("que_se_ve") or "",
                  "action": c.get("accion") or ""}
        conv = lambda t: a_json_generico(t, pedido)
        enviar = [dict(x) for x in partes]
        if con_json:
            enviar[0] = {"text": conv(enviar[0]["text"])}
        img, nota_json = await gemini_con_json(enviar, settings, "9:16", CALIDAD_CUADRO, formato="json" if con_json else "texto",
                                               a_json=conv, modulo="storyboard", save_prompt=False)
        await budget_record("storyboard_cuadro", CALIDAD_CUADRO, est, 1, note="storyboard: cuadro (Gemini" + (", " + nota_json if nota_json else "") + ")")
        txt_nota = (nota_hf + "lo dibujó Gemini.") if nota_hf else ""
        if nota_json == "bloqueado en texto: salió en JSON":
            txt_nota = (txt_nota + " " if txt_nota else "") + "Gemini lo bloqueó en texto y salió pidiéndolo en JSON."
        return _compress_ref(img, max_dim=1920, q=92), "gemini", txt_nota, est
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
            f"{nota_hf}Gemini lo rechazó ({motivo}); lo dibujó Seedream.", est + precio)


QC_IDENTIDAD = (
    "Sos supervisora de casting y continuidad. La PRIMERA imagen es un cuadro generado con IA para un "
    "reel; las otras son REFERENCIAS de la MISMA mujer real (su cara de cerca y el primer cuadro del reel). "
    "Revisá SOLO que sea ella y que tenga el mismo look: rasgos de la cara (ojos, nariz, labios, mandíbula, "
    "pecas y lunares), tono de piel, COLOR del pelo (también las raíces: si en las referencias es rubia "
    "pareja, raíces oscuras es un error), PEINADO (forma, altura y lugar del rodete o la colita, mechones "
    "sueltos), maquillaje y joyas. Si en el cuadro no se le ve la cara (está de espalda), juzgá solo el "
    "pelo, la piel y las joyas. Ignorá la ropa, la pose, el encuadre y el fondo.\n"
    "Devolvé SOLO un JSON válido: {\"puntaje\": <1-10, 10 = es ella idéntica>, \"diferencias\": "
    "[\"diferencia concreta y accionable, ej: el rodete es prolijo y bajo, en ella es desarmado y alto\"]}. "
    "Si es ella, diferencias = []. Una cara que no es ella nunca puede tener más de 4."
)


async def _inspector_identidad(img_b64: str, a: _Activos, ancla: Optional[str]) -> Optional[Dict[str, Any]]:
    """¿Es ella? Su cara, su pelo (color y peinado) y su look, contra la Cara HD y el primer cuadro."""
    try:
        key = await _current_api_key()
        if not key:
            return None
        st = await get_settings()
        modelo = str(st.get("qc_model") or "").strip() or ANALYZE_MODEL
        partes: List[Dict[str, Any]] = [{"text": QC_IDENTIDAD}, {"text": "CUADRO GENERADO (el que hay que revisar):"},
                                        _img_part(_compress_ref(base64.b64decode(img_b64), max_dim=2048, q=92))]
        for et, f in a.caras[:3]:
            partes += [{"text": f"REFERENCIA — {et}:"}, _img_part(f)]
        if ancla:
            partes += [{"text": "REFERENCIA — el primer cuadro del reel (su pelo y su look de este reel):"}, _img_part(ancla)]
        return await _qc_call(partes, key, modelo)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PASO 4 · EL VIDEO (cada cuadro aprobado es el primer cuadro de su tramo)
# ─────────────────────────────────────────────────────────────────────────────

# MODO CINE: en vez de "filmado con el celular", como un comercial de lencería de alta gama: cámara
# de cine, lentes anamórficos, poca profundidad de campo, 24 cuadros, movimientos de grúa,
# travelling y steadicam; y en el montaje, grano de película y color de cine.
CAMARAS = {"celular": "📱 Celular (real, como una creadora)", "cine": "🎬 Cine (como un comercial de alta gama)"}
# LA FICHA DE DIRECCIÓN DE FOTOGRAFÍA del video (cámara, lente, exposición, luz, color, textura, movimiento). Esta
# es la base según la cámara del reel; el director la ajusta a la idea y al lugar, y la usuaria la puede corregir.
# El lente se queda en 50–85 mm: más angular la deforma (cuello y cara más largos).
CINE_BASE: Dict[str, Dict[str, Any]] = {
    "cine": {
        "camera": "ARRI Alexa 35, Super 35 sensor, ProRes 4444",
        "lens": "Cooke S4/i 50 mm and 75 mm spherical primes (never wider than 40 mm on her)",
        "frame_rate": "24 fps", "shutter": "180 degrees (1/48 s), natural motion blur",
        "aperture": "T2.0 for the face, T2.8 for full body", "iso": "800 base",
        "focus": "follow focus on her eyes, shallow depth of field with soft creamy bokeh",
        "lighting": {"key": "ONE motivated soft source from the side (the window or a lamp of the place), 60-90 "
                            "degrees off her nose and a bit above her eyes, so one side of her face falls into shadow",
                     "fill": "NO frontal fill: negative fill (black flag) on the shadow side, key-to-fill ratio 4:1, "
                             "real shadows under the nose, chin and cheekbones",
                     "rim": "only if the place motivates it (a second window or lamp behind her), subtle",
                     "falloff": "light falls off across the room and across her body: brighter near the source, "
                                "darker away from it, the background a stop under her face",
                     "practicals": "the real lamps of the place on, slightly warmer than the window light",
                     "color_temperature": "mixed and real: cool daylight from the window (5600 K) against warm "
                                          "practicals (3200 K), never one flat neutral white",
                     "never": "ring light, flat frontal softbox, beauty dish straight on, shadowless even light, "
                              "glowing skin, studio-catalogue lighting"},
        "grade": "rich natural colour, true skin tones, soft contrast, gentle highlight roll-off (Kodak 2383 print look)",
        "texture": "fine 35 mm film grain, subtle halation on highlights, real skin pores, no beauty filter",
        "movement_style": "Steadicam and slow dolly moves, perfectly smooth, no shake",
    },
    "celular": {
        "camera": "iPhone 16 Pro, main 24 mm camera cropped to a 48 mm look, Apple Log off, HDR off",
        "lens": "phone main lens used at 2x (48 mm equivalent) near her, never the 0.5x ultra-wide",
        "frame_rate": "30 fps", "shutter": "auto, natural motion blur",
        "aperture": "f/1.8 phone lens, natural phone depth of field", "iso": "auto, low noise daylight",
        "focus": "tap focus on her face, the set sharp",
        "lighting": {"key": "real window daylight from the SIDE, one side of her face brighter than the other",
                     "fill": "only the room's natural bounce, no extra light: real shadows under the nose and chin",
                     "rim": "none or natural from a second window",
                     "falloff": "the room gets darker away from the window",
                     "practicals": "the room's own lamps, warmer than the window",
                     "color_temperature": "natural daylight with warm lamps mixed in, phone auto white balance",
                     "never": "ring light, selfie light, flat frontal light, shadowless even light"},
        "grade": "true phone colours, no filter, natural contrast",
        "texture": "light phone sensor noise, real skin texture, no beauty mode",
        "movement_style": "handheld by a friend, small natural micro-shakes, real Instagram reel",
    },
}


def _cine_de(sb: Dict[str, Any]) -> Dict[str, Any]:
    """La ficha de fotografía de ESTE storyboard: la base de su cámara + lo que ajustó el director o la usuaria."""
    base = json.loads(json.dumps(CINE_BASE["cine" if _es_cine(sb) else "celular"]))
    for k, v in (sb.get("cine_en") or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update({kk: str(vv)[:300] for kk, vv in v.items() if str(vv).strip()})
        elif str(v).strip():
            base[k] = str(v)[:400]
    return base


_MOV_CINE = {"mano": "smooth steadicam float", "acerca": "slow dolly-in", "aleja": "slow dolly-out revealing more",
             "sigue": "steadicam tracking shot following her", "orbita": "slow arc dolly around her",
             "paneo": "slow cinematic pan", "fija": "locked-off on a tripod", "cenital": "top-down crane shot"}


def _es_cine(sb: Dict[str, Any]) -> bool:
    return sb.get("camara") == "cine"


def _que_pasa(c: Dict[str, Any], linea_en: str, mov_en: str) -> str:
    """La minilínea de tiempo del momento (o, si no hay, lo que se mueve)."""
    seg = int(c["seg"])
    if linea_en:
        return (f"TIMELINE of this {seg}-second continuous shot, in this exact order, one action after the "
                f"other with no cuts: {linea_en}.")
    return f"During these {seg} seconds: {mov_en}."


def _extra_video(sb: Dict[str, Any], c: Dict[str, Any], a: "_Activos") -> str:
    f = a.fichas.get(c["variante"]) or {}
    x = f" The set: {f['render_en'][:450]}" if f.get("render_en") else ""
    if c["tipo"] != "producto" and sb.get("estilo_en"):
        x += f" Her look stays the same: {sb['estilo_en']}."
    if sb.get("luz") in LUCES_3D:
        x += f" Light: {LUCES_3D[sb['luz']]}."
    return x


def _camara_video(c: Dict[str, Any], cine: bool) -> Tuple[str, str]:
    """(cómo empieza el pedido, cómo se mueve la cámara) según sea celular o cine."""
    if cine:
        return ("Vertical 9:16 cinematic footage from a high-end lingerie commercial, shot on a cinema camera with "
                "anamorphic lenses at 24fps with natural motion blur, shallow depth of field",
                f"{_MOV_CINE.get(c['movimiento'], 'smooth steadicam float')}, perfectly smooth, no shake")
    return ("Vertical 9:16 phone footage", f"{_film._MOV_CORTO.get(c['movimiento'], 'handheld')}, small handheld micro-shakes")


def _prompt_video(c: Dict[str, Any], linea_en: str, mov_en: str, producto: bool, extra: str, cine: bool = False) -> str:
    cabeza, camara = _camara_video(c, cine)
    quien = ("" if producto else " @Element1 is her: keep her face, hair and body IDENTICAL to the first frame.")
    prenda = "@Element1" if producto else "@Element2"
    return _sanear_prompt_fal(
        f"{cabeza}. The video STARTS EXACTLY on the given first frame and continues it "
        f"naturally. {_que_pasa(c, linea_en, mov_en)} Camera: {camara}.{quien} The "
        f"set is {prenda}: keep it EXACTLY as in the first frame (design, colour, lace, seams, straps).{extra} "
        "Calm real-time movement, never slow motion, real skin and fabric texture, real light. No text, no other "
        "people, no cuts.")


def _prompt_video_hf(c: Dict[str, Any], linea_en: str, mov_en: str, producto: bool, extra: str,
                     cine: bool = False) -> str:
    """Kling 3.0 imagen a video: el cuadro aprobado es el primer cuadro (ahí ya están su cara, la
    prenda y el lugar); el pedido dice la línea de tiempo y que nada cambie."""
    cabeza, camara = _camara_video(c, cine)
    quien = ("The lingerie set" if producto else "Her face, hair and body and the lingerie set")
    return _sanear_prompt_fal(
        f"{cabeza}. The video starts exactly on the given image and continues it naturally. "
        f"{_que_pasa(c, linea_en, mov_en)} Camera: {camara}. {quien} stay EXACTLY as "
        f"in the image (same face, same design, colour, lace, seams and straps).{extra} Calm real-time movement, "
        "never slow motion, real skin and fabric texture, real light. No text, no other people, no cuts.")


def _prompt_ref(sb: Dict[str, Any], c: Dict[str, Any], imagen_en: str, linea_en: str, mov_en: str,
                extra: str, con_lugar: bool) -> str:
    """El pedido SIN CUADRO: quién es cada referencia, dónde pasa, el encuadre y la línea de tiempo."""
    cabeza, camara = _camara_video(c, _es_cine(sb))
    producto = c["tipo"] == "producto"
    plano = _film._PLANO_CORTO.get(c["plano"], "medium shot")
    ang = _film._ANGULO_CORTO.get(c["angulo"], "eye level")
    if producto:
        quien = ("@Element1 is the lingerie set: show ONLY the set, no person, with volume and shape (on a hanger, a "
                 "bust form or held by a hand), copying EXACTLY its design, colour, lace, seams and straps.")
    else:
        quien = ("@Element1 is HER: the same exact woman, keep her face, eyes, nose, lips, freckles, hair and body "
                 "IDENTICAL to her references. She wears @Element2, the lingerie set: copy EXACTLY its design, "
                 "colour, lace, seams and straps, fitting her as in the references.")
    lugar = (" The scene happens INSIDE @Image1, this exact real place: same walls, floor, furniture, colours and "
             "light." if con_lugar else "")
    if not producto and sb.get("_rasgos"):
        quien += (f" HER REAL FEATURES: {sb['_rasgos']} Never idealise her: no longer or thinner neck, no slimmer "
                  "face or body.")
    return _sanear_prompt_fal(
        f"{cabeza}. {quien}{lugar} FRAMING: {plano}, {ang}. WHAT WE SEE: {imagen_en.rstrip('. ')}. "
        f"{_que_pasa(c, linea_en, mov_en)} Camera: {camara}.{extra} Calm real-time movement, never slow motion, "
        "real skin and fabric texture, real light. No text, no logos, no other people, no cuts.")


async def _filmar_ref(cli: httpx.AsyncClient, key: str, sb: Dict[str, Any], c: Dict[str, Any],
                      refs: Dict[str, Any], a: "_Activos") -> float:
    """Un momento SIN CUADRO: Kling crea el video con sus referencias (cara y cuerpo, la prenda, el
    lugar) y el pedido. Con recuperación si un reinicio corta un pedido ya pagado."""
    sid, cid = sb["id"], c["id"]
    seg = max(SEG_FILMA, int(c["seg"]))
    vm = sb["motor_vm_ref"]
    producto = c["tipo"] == "producto"
    imagen_en = c.get("imagen") or (await _al_ingles({"a": c.get("que_se_ve") or ""})).get("a") or c.get("que_se_ve", "")
    linea_en = c.get("linea_en") or ""
    if not linea_en and c.get("linea"):
        linea_en = (await _al_ingles({"a": c["linea"]})).get("a") or ""
    mov_en = c.get("movimiento_video") or (await _al_ingles({"a": c.get("accion") or ""})).get("a") \
        or "she moves naturally, breathing, a small shift of weight"
    prenda = refs["prenda"].get(c["variante"]) or next((x for x in refs["prenda"].values() if x), [])
    els = ([] if producto else [_film._elemento(refs["ella"])]) + ([_film._elemento(prenda)] if prenda else [])
    sb["_rasgos"] = refs.get("rasgos") or ""
    payload: Dict[str, Any] = {"prompt": _prompt_ref(sb, c, imagen_en, linea_en, mov_en, _extra_video(sb, c, a),
                                                     bool(refs.get("lugar"))),
                               "elements": els, "duration": str(seg), "aspect_ratio": "9:16",
                               "generate_audio": False, "negative_prompt": _film._NEGATIVO}
    if refs.get("lugar"):
        payload["image_urls"] = [refs["lugar"]]
    _con_video(payload, refs)
    crudo = _dir(sid) / f"{cid}_crudo.mp4"
    k_pend = _pfx() + f"sb:{sid}:pendiente:{cid}"
    costo_est = round(VM.seg_valido(vm["modelo"], seg) * VM.precio_seg(vm["modelo"], vm["proveedor"], vm["calidad"], False), 3)
    try:
        pend = await kv.get(k_pend)
        if isinstance(pend, dict) and await VM.recuperar(pend, crudo, key_fal=key):
            r = {"costo": float(pend.get("costo") or costo_est), "id": pend.get("id", "")}
            c["nota_video"] = f"Recuperado el video ya pagado (pedido {r['id']}) que un reinicio había cortado."
        else:
            async def anotar(info: Dict[str, Any]) -> None:
                await kv.set(k_pend, dict(info, costo=costo_est), ttl=3 * 86400)
            VM.AL_ACEPTAR.set(anotar)
            r = await VM.referencias(vm["proveedor"], vm["modelo"], vm["calidad"], payload, crudo, key_fal=key)
            c["nota_video"] = (f"{VM.MODELOS[vm['modelo']]['nombre']} por {VM.PROVEEDORES[vm['proveedor']]}, "
                               f"con referencias (pedido {r.get('id', '')}).")
        await kv.delete(k_pend)
        await budget_record("storyboard_video", vm["modelo"], r["costo"], 1,
                            note=f"storyboard sin cuadros: momento de {seg} s ({VM.PROVEEDORES[vm['proveedor']]} {r.get('id', '')})")
        await asyncio.to_thread(_film._normalizar, crudo, _clip(sid, cid), None, float(seg), "centro")
        c["motor_video"], c["cartel_pos"] = "vm_ref", None
        return r["costo"]
    except VM.MotorError as e:
        c["nota_video"] = str(e)[:300]
        raise RuntimeError(str(e)[:300])
    finally:
        crudo.unlink(missing_ok=True)


def _corrido_ok(sb: Dict[str, Any]) -> bool:
    """¿Se filma TODO el storyboard en un solo video? Sólo sin cuadros, si el modelo llega a esa duración y la
    usuaria no lo apagó."""
    if not _es_ref(sb) or (sb.get("de_corrido") is False and not sb.get("corrido")):
        return False
    total = sum(max(SEG_FILMA, int(c["seg"])) for c in sb.get("cuadros") or [])
    lo, hi = VM.MODELOS[sb["motor_vm_ref"]["modelo"]].get("seg") or (3, 10)
    return lo <= total <= hi and len(sb.get("cuadros") or []) > 1


async def _filmar_corrido(cli: httpx.AsyncClient, key: str, jid: str, sb: Dict[str, Any], refs: Dict[str, Any],
                          a: "_Activos") -> float:
    """TODO el storyboard en UN pedido (Kling 4.0 llega a 30 s). Si el director escribió el video entero segundo a
    segundo (`prompt_corrido_en`), va ése; si no, se arma con las tomas. El resultado queda CRUDO en
    `_crudo(sid)`: no se corta ni se toca (motion y lip-sync sólo si después la usuaria los pide)."""
    sid = sb["id"]
    vm = sb["motor_vm_ref"]
    cuadros = sb["cuadros"]
    hay_ella = any(c["tipo"] != "producto" for c in cuadros)
    cabeza, _ = _camara_video(cuadros[0], _es_cine(sb))
    tomas, t = [], 0
    for i, c in enumerate(cuadros, 1):
        seg = max(SEG_FILMA, int(c["seg"]))
        imagen_en = c.get("imagen") or (await _al_ingles({"a": c.get("que_se_ve") or ""})).get("a") or c.get("que_se_ve", "")
        linea_en = c.get("linea_en") or ""
        if not linea_en and c.get("linea"):
            linea_en = (await _al_ingles({"a": c["linea"]})).get("a") or ""
        _, camara = _camara_video(c, _es_cine(sb))
        quien = "ONLY the set, no person" if c["tipo"] == "producto" else "@Element1 wearing @Element2"
        tomas.append(f"SHOT {i} ({t}-{t + seg}s): {_film._PLANO_CORTO.get(c['plano'], 'medium shot')}, "
                     f"{_film._ANGULO_CORTO.get(c['angulo'], 'eye level')}, camera {camara}. {quien}: "
                     f"{imagen_en.rstrip('. ')}." + (f" Inside this shot (seconds counted from its start): {linea_en.rstrip('. ')}." if linea_en else ""))
        t += seg
    if not hay_ella:
        tomas = [x.replace("ONLY the set", "ONLY @Element1") for x in tomas]
    # EL PEDIDO EN JSON: cada cosa en su lugar (quién es quién, sus rasgos reales, la prenda, la línea de tiempo y
    # lo que NO se hace), como en Fotos: así Kling no mezcla instrucciones ni "embellece" a la modelo.
    ficha = a.fichas.get(cuadros[0]["variante"]) or {}
    timeline: Any = None
    try:
        timeline = json.loads(sb.get("prompt_corrido_en") or "")
    except ValueError:
        timeline = None
    if not isinstance(timeline, list) or not timeline:
        timeline = sb.get("prompt_corrido_en") or tomas
    pedido: Dict[str, Any] = {
        "video": f"{cabeza}. ONE continuous {t}-second vertical 9:16 video, in real time, never slow motion.",
        "references": {
            "@Element1": ("HER, the only woman in the video: every photo of @Element1 is the SAME woman (face from the "
                          "front, in three-quarter profile, her portrait, her full body). Ignore the clothes she wears "
                          "in those photos." if hay_ella else "the lingerie set, alone, with volume"),
            **({"@Element2": "the lingerie set she wears: real photos and the set worn on her body"} if hay_ella else {}),
            **({"@Image1": "the real place: everything happens inside it"} if refs.get("lugar") else {}),
        },
        **({"identity_lock": {
            "her_real_features": refs.get("rasgos") or "",
            "her_body": a.cuerpo_en or "",
            "keep": "her exact face shape, cheeks, jaw, neck length, shoulders and body proportions as in @Element1, "
                    "in every second and from every angle, also when she turns",
            "never": "do NOT idealise or stylise her: no longer or thinner neck, no slimmer face, no sharper jaw or "
                     "cheekbones, no slimmer waist, no more defined abs, no longer legs, no model makeover",
        }} if hay_ella else {}),
        "the_set": {"copy_exactly": "design, colour, fabric texture, lace, seams, straps and the cut of the bottom",
                    **({"technical_sheet": ficha["render_en"][:500]} if ficha.get("render_en") else {}),
                    **({"avoid": ficha["neg_en"][:200]} if ficha.get("neg_en") else {})},
        **({"look": sb["estilo_en"]} if sb.get("estilo_en") and hay_ella else {}),
        **({"light": LUCES_3D[sb["luz"]]} if sb.get("luz") in LUCES_3D else {}),
        "camera": "at her eye or chest height, never a low angle; natural 50-85 mm lens look, never a wide-angle "
                  "lens close to her; smooth and motivated moves",
        "cinematography": _cine_de(sb),
        "timeline": timeline,
        "consistency": "the SAME woman, the SAME set, the SAME place, light, hair and makeup from the first to the "
                       "last second; each moment starts where the previous one ended",
        "realism": "it must look filmed, not generated: real skin with pores, fine lines, peach fuzz and uneven tone, "
                   "no smoothing or beauty filter, no waxy or plastic skin, natural asymmetry of the face, real "
                   "flyaway hairs; candid micro-expressions, a natural smile with lips barely open (no big toothy "
                   "grin); faces off-centre, never a frontal centred symmetrical close-up; ONE directional "
                   "light from the side with real shadows on the face (nose, chin, cheekbones) and falloff into "
                   "the room — never flat, shadowless or ring-light lighting, which is what looks generated",
        "forbidden": "text, logos, watermarks, other people, slow motion, ID-photo framing, CGI look",
    }
    if refs.get("video"):
        pedido["motion_reference"] = VM.FRASE_VIDEO
    prompt = _sanear_prompt_fal(json.dumps(pedido, ensure_ascii=False))
    prenda = refs["prenda"].get(cuadros[0]["variante"]) or next((x for x in refs["prenda"].values() if x), [])
    els = ([_film._elemento(refs["ella"])] if hay_ella else []) + ([_film._elemento(prenda)] if prenda else [])
    payload: Dict[str, Any] = {"prompt": prompt, "elements": els, "duration": str(t), "aspect_ratio": "9:16",
                               "generate_audio": False, "negative_prompt": _film._NEGATIVO}
    if refs.get("lugar"):
        payload["image_urls"] = [refs["lugar"]]
    _con_video(payload, refs)
    crudo = _crudo(sid)
    k_pend = _pfx() + f"sb:{sid}:pendiente:corrido"
    costo_est = round(VM.seg_valido(vm["modelo"], t) * VM.precio_seg(vm["modelo"], vm["proveedor"], vm["calidad"], False), 3)
    await _job_set(jid, {"paso": f"Filmando los {t} s de corrido con {VM.MODELOS[vm['modelo']]['nombre']} por "
                                 f"{VM.PROVEEDORES[vm['proveedor']]} (un solo video)…"})
    try:
        pend = await kv.get(k_pend)
        if isinstance(pend, dict) and await VM.recuperar(pend, crudo, key_fal=key):
            r = {"costo": float(pend.get("costo") or costo_est), "id": pend.get("id", "")}
            nota = f"Recuperado el video ya pagado (pedido {r['id']})."
        else:
            async def anotar(info: Dict[str, Any]) -> None:
                await kv.set(k_pend, dict(info, costo=costo_est), ttl=3 * 86400)
            VM.AL_ACEPTAR.set(anotar)
            r = await VM.referencias(vm["proveedor"], vm["modelo"], vm["calidad"], payload, crudo, key_fal=key)
            nota = (f"{VM.MODELOS[vm['modelo']]['nombre']} por {VM.PROVEEDORES[vm['proveedor']]} "
                    f"(pedido {r.get('id', '')}), {t} s de corrido.")
        await kv.delete(k_pend)
        await budget_record("storyboard_video", vm["modelo"], r["costo"], 1,
                            note=f"storyboard de corrido: {t} s ({VM.PROVEEDORES[vm['proveedor']]} {r.get('id', '')})")
        sb["nota_corrido"] = nota
        return r["costo"]
    except VM.MotorError as e:
        crudo.unlink(missing_ok=True)
        raise RuntimeError(str(e)[:300])


def _crudo(sid: str) -> Path:
    """El video de corrido tal cual lo devolvió Kling (se guarda aunque después se le sume motion)."""
    return _dir(sid) / "corrido.mp4"


async def _cortar_corrido(sb: Dict[str, Any]) -> None:
    """Sólo si la usuaria pide motion o que hable: un clip por momento cortado del crudo (si Kling devolvió otra
    duración, se reparte en proporción), para que la voz, el lip-sync y los carteles caigan en su segundo."""
    sid = sb["id"]
    crudo = _crudo(sid)
    t = sum(max(SEG_FILMA, int(c["seg"])) for c in sb["cuadros"])
    escala = (_duracion_video(crudo) or t) / t
    ini = 0.0
    for c in sb["cuadros"]:
        seg = max(SEG_FILMA, int(c["seg"]))
        tramo = _dir(sid) / f"{c['id']}_corrido.mp4"
        try:
            await asyncio.to_thread(_film._ff, ["-y", "-ss", f"{ini * escala:.3f}", "-i", str(crudo),
                                                "-t", f"{seg * escala:.3f}", "-c:v", "libx264", "-crf", "16",
                                                "-an", str(tramo)])
            await asyncio.to_thread(_film._normalizar, tramo, _clip(sid, c["id"]), None, float(seg), "inicio")
        finally:
            tramo.unlink(missing_ok=True)
        c["motor_video"], c["cartel_pos"], c["clip"] = "vm_ref", None, True
        c["nota_video"] = sb.get("nota_corrido", "")
        ini += seg


async def _subir_refs(cli: httpx.AsyncClient, key: str, sb: Dict[str, Any], a: "_Activos",
                      variantes: set) -> Dict[str, Any]:
    """Sube UNA vez las referencias del modo sin cuadros: su cara (de frente primero) y su cuerpo con la
    prenda, la prenda de cada color (probador y foto real) y el lugar."""
    sid = sb["id"]
    # Ella hasta 5 (cara de frente, ojos, perfil 3/4, retrato, cuerpo con la prenda) + la prenda hasta 4 (2 reales
    # y el probador de frente y espalda) + el lugar = 10, el tope de Kling 4.0 por Higgsfield.
    # Sólo fotos donde se la ve a ELLA con su cara y sus proporciones reales: el macro de ojos y el cuerpo sin cabeza
    # del probador no la identifican (y Kling la "estilizaba": cuello largo, cara más fina).
    def cara(et: str) -> Optional[str]:
        return next((f for e, f in a.caras if et in e), None)
    def hoja(et: str) -> Optional[str]:
        return next((f for e, f in a.hoja if e.startswith(et)), None)
    ella_b64 = [x for x in (cara("frontal") or (a.caras[0][1] if a.caras else None), cara("three-quarter"),
                            a.retrato, hoja("perfil"), hoja("cuerpo entero de frente")) if x]
    ella_b64 = list(dict.fromkeys(ella_b64))
    for x in [hoja("cuerpo entero de espalda"), cara("macro")]:
        if x and len(ella_b64) < 5 and x not in ella_b64:
            ella_b64.append(x)
    sube = lambda b, n: _fal_subir(cli, key, base64.b64decode(_compress_ref(base64.b64decode(b), max_dim=1600, q=90)),
                                   "image/jpeg", n)
    out: Dict[str, Any] = {"ella": [await sube(b, f"sb-{sid}-ref-ella{i}.jpg") for i, b in enumerate(ella_b64[:5])],
                           "prenda": {}, "lugar": ""}
    for v in variantes:
        fotos = (a.reales.get(v) or [])[:2] + (a.probador.get(v) or [])[:2]
        out["prenda"][v] = [await sube(b, f"sb-{sid}-ref-p{v}-{i}.jpg") for i, b in enumerate(fotos)]
    if a.lugar:
        out["lugar"] = await sube(a.lugar, f"sb-{sid}-ref-lugar.jpg")
    if _video_ref(sid).exists():
        out["video"] = await _fal_subir(cli, key, _video_ref(sid).read_bytes(), "video/mp4", f"sb-{sid}-ref-mov.mp4")
    return out


_SYSTEM_RASGOS = (
    "You describe a real woman's identity for a video model that tends to idealise people (longer neck, slimmer "
    "face, model body). Look at her photos and write, in plain neutral English, 70-100 words, what makes HER: face "
    "shape (round, oval, square…), cheeks fullness, jaw, chin, nose, lips, eyes colour and shape, brows, freckles "
    "and moles (where), neck length and thickness RELATIVE to her face and shoulders, shoulders, body build and "
    "proportions (be honest: average, soft, curvy, petite, athletic…), hair. No compliments, no judgement. Answer "
    "JSON: {\"rasgos_en\": \"...\"}")


async def _rasgos_en(doc: Dict[str, Any], a: "_Activos") -> str:
    """Sus rasgos reales en palabras (cara, cuello, proporciones), para que Kling no la "estilice". Se hace una vez
    por modelo y queda guardado (se rehace si cambia su cara)."""
    fotos = [f for _, f in a.caras[:1]] + [f for e, f in a.caras if "three-quarter" in e][:1] \
        + [f for e, f in a.hoja if e.startswith("cuerpo entero de frente")][:1]
    if not fotos:
        return str(doc.get("desc_cara") or "")
    k = _pfx() + f"sb:rasgos:{doc['id']}:{hashlib.sha1(''.join(x[:200] for x in fotos).encode()).hexdigest()[:10]}"
    hecho = await kv.get(k)
    if isinstance(hecho, str) and hecho:
        return hecho
    if not _claude.disponible():
        return str(doc.get("desc_cara") or "")
    partes: List[Dict[str, Any]] = [{"text": "Her photos (face, three-quarter profile, full body):"}]
    for f in fotos:
        partes.append(_img_part(_compress_ref(base64.b64decode(f), max_dim=1000, q=88)))
    try:
        data, costo = await _claude.pedir_json(_SYSTEM_RASGOS, partes, max_tokens=1500, esfuerzo="low")
        await budget_record("storyboard_claude", _claude.MODELO, costo, 1, note="storyboard: sus rasgos")
    except _claude.ClaudeNoDisponible:
        return str(doc.get("desc_cara") or "")
    txt = _texto(data.get("rasgos_en"), 900)
    if txt:
        await kv.set(k, txt)
    return txt


def _video_ref(sid: str) -> Path:
    """El video de referencia de MOVIMIENTO que subió la usuaria (un reel que le gusta, un recorrido de Lumen)."""
    return _dir(sid) / "ref_mov.mp4"


def _con_video(payload: Dict[str, Any], refs: Dict[str, Any]) -> None:
    """Suma el video de referencia de movimiento al pedido (Higgsfield lo recibe como @video_1; los demás
    proveedores lo sacan)."""
    if refs.get("video"):
        payload["video_urls"] = [refs["video"]]
        if VM.FRASE_VIDEO not in payload["prompt"]:
            payload["prompt"] = payload["prompt"].rstrip() + VM.FRASE_VIDEO


async def _filmar_cuadro(cli: httpx.AsyncClient, key: str, headers: Dict[str, str], jid: str,
                         sb: Dict[str, Any], c: Dict[str, Any], u_ella: List[str],
                         u_prenda: Dict[int, List[str]], a: "_Activos") -> float:
    """Filma el MOMENTO entero de corrido (3 a 5 s, lo que dura: no se paga de más), desde su
    cuadro aprobado y siguiendo su minilínea de tiempo."""
    sid, cid = sb["id"], c["id"]
    seg = max(SEG_FILMA, int(c["seg"]))
    img = await kv.get(_k_img(sid, cid))
    if not img:
        raise RuntimeError("falta la imagen del cuadro")
    producto = c["tipo"] == "producto"
    u_inicio = await _fal_subir(cli, key, await asyncio.to_thread(_film._vertical, img), "image/jpeg",
                                f"sb-{sid}-{cid}.jpg")
    linea_en = c.get("linea_en") or ""
    if not linea_en and c.get("linea"):
        linea_en = (await _al_ingles({"a": c["linea"]})).get("a") or ""
    mov_en = c.get("movimiento_video") or (await _al_ingles({"a": c.get("accion") or ""})).get("a") \
        or "she moves naturally, breathing, a small shift of weight"
    extra = _extra_video(sb, c, a)
    prendas = u_prenda.get(c["variante"]) or next((x for x in u_prenda.values() if x), [])
    motor = _motor_video(sb)
    fin = await kv.get(_k_img_fin(sid, cid)) if _con_final(c) else None
    if motor == "vm":
        # EL MOTOR COMÚN: el cuadro aprobado es el primer cuadro; si hay cuadro final, el tramo termina ahí.
        vm = sb["motor_vm"]
        crudo = _dir(sid) / f"{cid}_crudo.mp4"
        k_pend = _pfx() + f"sb:{sid}:pendiente:{cid}"
        costo_est = round(VM.seg_valido(vm["modelo"], seg) * VM.precio_seg(vm["modelo"], vm["proveedor"], vm["calidad"], False), 3)
        try:
            # ¿Ya se había pedido y pagado este momento y un reinicio del servidor lo cortó? Se recupera, no se repide.
            pend = await kv.get(k_pend)
            if isinstance(pend, dict) and await VM.recuperar(pend, crudo, key_fal=key):
                r = {"costo": float(pend.get("costo") or costo_est), "id": pend.get("id", "")}
                c["nota_video"] = f"Recuperado el video ya pagado (pedido {r['id']}) que un reinicio había cortado."
            else:
                async def anotar(info: Dict[str, Any]) -> None:
                    await kv.set(k_pend, dict(info, costo=costo_est), ttl=3 * 86400)
                VM.AL_ACEPTAR.set(anotar)
                ini = base64.b64encode(await asyncio.to_thread(_film._vertical, img)).decode()
                fin_b64 = base64.b64encode(await asyncio.to_thread(_film._vertical, fin)).decode() if fin else None
                r = await VM.tramo(vm["proveedor"], vm["modelo"], vm["calidad"], ini, fin_b64,
                                   _prompt_video_hf(c, linea_en, mov_en, producto, extra, _es_cine(sb)), seg, False,
                                   "9:16", crudo, key_fal=key)
            await kv.delete(k_pend)
            await budget_record("storyboard_video", vm["modelo"], r["costo"], 1,
                                note=f"storyboard: momento de {seg} s ({VM.PROVEEDORES[vm['proveedor']]} {r.get('id', '')})")
            await asyncio.to_thread(_film._normalizar, crudo, _clip(sid, cid), None, float(seg), "centro")
            c["motor_video"], c["cartel_pos"] = "vm", None
            if not c.get("nota_video", "").startswith("Recuperado"):
                c["nota_video"] = f"{VM.MODELOS[vm['modelo']]['nombre']} por {VM.PROVEEDORES[vm['proveedor']]}."
            return r["costo"]
        except VM.MotorError as e:
            c["nota_video"] = str(e)[:300]
            raise RuntimeError(str(e)[:300])
        finally:
            crudo.unlink(missing_ok=True)
    nota_fal = ""
    if motor.startswith("hf_") and _va_por_fal(sb, c):
        nota_fal = ("Por fal: arranca sin su cara y Higgsfield no recibe su cara de referencia"
                    + (" ni el cuadro final" if fin else "") + ".")
    if motor.startswith("hf_") and not nota_fal:
        mv = MOTORES_VIDEO[motor]
        crudo = _dir(sid) / f"{cid}_crudo.mp4"
        try:
            res = await hf.video(cli, mv["hf"], {"prompt": _prompt_video_hf(c, linea_en, mov_en, producto, extra, _es_cine(sb)),
                                                 "image_url": u_inicio, "duration": seg, "generate_audio": False,
                                                 "sound": "off", "negative_prompt": _film._NEGATIVO}, crudo)
            dur_hf = _duracion_video(crudo)
            costo = round(_precio_tramo(mv, seg), 3)
            await budget_record("storyboard_video", motor, costo, 1,
                                note=f"storyboard: momento de {seg} s (Higgsfield {res.get('_request_id', '')})")
            await asyncio.to_thread(_film._normalizar, crudo, _clip(sid, cid), None, float(seg), "centro")
            c["motor_video"], c["cartel_pos"] = motor, None
            c["nota_video"] = (f"Higgsfield (pedido {res.get('_request_id', '')}): devolvió {dur_hf:.1f} s"
                               + (" — ¡más largo de lo pedido!" if dur_hf > seg + 0.6 else "") + ".")
            return costo
        except hf.HiggsfieldAceptado as e:
            # Higgsfield YA aceptó (y puede haber cobrado): no se vuelve a pedir a fal.
            c["nota_video"] = str(e)[:300]
            raise RuntimeError(str(e)[:300])
        except Exception as e:
            # Higgsfield NO lo aceptó (no cobró): sale por fal y el cuadro lo dice.
            c["nota_video"] = f"Higgsfield no aceptó el pedido ({str(e)[:160]}); lo filmó fal."
        finally:
            crudo.unlink(missing_ok=True)
    m = _film.MOTORES[MOTOR_VIDEO]
    if fin:
        extra += (" The shot ENDS exactly on the given last frame: she has turned and her face is EXACTLY "
                  "@Element1's face (same features, freckles and hair).")
    payload: Dict[str, Any] = {"prompt": _prompt_video(c, linea_en, mov_en, producto, extra, _es_cine(sb)),
                               "start_image_url": u_inicio, "duration": str(seg), "aspect_ratio": "9:16",
                               "generate_audio": False, "negative_prompt": _film._NEGATIVO, "cfg_scale": 0.5}
    if fin:
        payload["end_image_url"] = await _fal_subir(cli, key, await asyncio.to_thread(_film._vertical, fin),
                                                    "image/jpeg", f"sb-{sid}-{cid}-fin.jpg")
    if m["tipo"] == "kling":
        els = ([] if producto else [_film._elemento(u_ella)]) + ([_film._elemento(prendas)] if prendas else [])
        if els:
            payload["elements"] = els
    crudo = _dir(sid) / f"{cid}_crudo.mp4"
    try:
        await _film._fal_video(cli, headers, m["modelo"], payload, f"{jid}-{cid}", ("negative_prompt", "cfg_scale"), crudo)
        costo = round(seg * m["precio_seg"], 3)
        await budget_record("storyboard_video", MOTOR_VIDEO, costo, 1, note=f"storyboard: momento de {seg} s")
        await asyncio.to_thread(_film._normalizar, crudo, _clip(sid, cid), None, float(seg), "centro")
    finally:
        crudo.unlink(missing_ok=True)
    c["motor_video"], c["cartel_pos"] = "fal_kling_o3", None
    c["nota_video"] = nota_fal or c.get("nota_video", "")
    return costo


def _hay_habla(sb: Dict[str, Any]) -> bool:
    return any(c.get("habla") for c in sb.get("cuadros") or [])


_VOZ_FALSO = {"voz": "", "tono": "cercana", "energia": _film.ENERGIA_FILMADO, "mic": True}


async def _voz_momento(sb: Dict[str, Any], doc: Dict[str, Any], c: Dict[str, Any]) -> Optional[Tuple[Path, float]]:
    """Lo que dice en ESE momento, con su voz (queda guardado hasta que cambie el texto)."""
    texto = (c.get("dice") or "").strip()
    if not texto:
        return None
    clave = hashlib.sha1((texto + str(doc.get("voz_motor")) + str(doc.get("voz_clon"))).encode()).hexdigest()[:10]
    p = _dir(sb["id"]) / f"{c['id']}_voz_{clave}.mp3"
    if not p.exists():
        for viejo in _dir(sb["id"]).glob(f"{c['id']}_voz_*.mp3"):
            viejo.unlink(missing_ok=True)
        await _film._voz(doc, dict(_VOZ_FALSO), texto, p)
    return p, _duracion_video(p)


async def _hablar(cli: httpx.AsyncClient, key: str, headers: Dict[str, str], jid: str, sb: Dict[str, Any],
                  doc: Dict[str, Any], c: Dict[str, Any]) -> Tuple[float, str]:
    """El momento en que HABLA A CÁMARA: su voz (lo que dice) y la boca sincronizada sobre el
    tramo ya filmado (el mismo lip-sync de Filmado). Devuelve (costo, nota)."""
    sid, cid = sb["id"], c["id"]
    v = await _voz_momento(sb, doc, c)
    if not v:
        return 0.0, "Habla a cámara pero no tiene texto: escribí qué dice."
    voz, voz_seg = v
    clip = _clip(sid, cid)
    clip_seg = _duracion_video(clip)
    pad = _dir(sid) / f"{cid}_voz_pad.mp3"
    if clip_seg >= voz_seg:
        await asyncio.to_thread(_film._completar_voz, voz, clip_seg, pad)
    else:
        pad = voz
    ls = _film.LIPSYNCS["sync"]
    sal = _dir(sid) / f"{cid}_lipsync.mp4"
    try:
        u_vid = await _fal_subir(cli, key, clip.read_bytes(), "video/mp4", f"sb-{sid}-{cid}.mp4")
        u_voz = await _fal_subir(cli, key, pad.read_bytes(), "audio/mpeg", f"sb-{sid}-{cid}.mp3")
        await _film._fal_video(cli, headers, ls["modelo"],
                               {"video_url": u_vid, "audio_url": u_voz,
                                "sync_mode": "cut_off" if clip_seg >= voz_seg else "bounce"},
                               f"{jid}-{cid}-ls", ("sync_mode",), sal)
        costo = round(max(clip_seg, voz_seg) * ls["precio_seg"], 3)
        await budget_record("storyboard_lipsync", ls["modelo"], costo, 1, note="storyboard: habla a cámara")
        await asyncio.to_thread(_film._normalizar, sal, clip, None, 0.0, "centro")
        return costo, "🗣 Habla a cámara: boca sincronizada con su voz."
    except Exception as e:
        return 0.0, f"🗣 El lip-sync falló ({str(getattr(e, 'detail', '') or e)[:120]}): va su voz encima, sin mover la boca."
    finally:
        sal.unlink(missing_ok=True)
        if pad != voz:
            pad.unlink(missing_ok=True)


async def _montar(sb: Dict[str, Any], doc: Dict[str, Any]) -> None:
    """Une los tramos con cortes secos, subtítulos y su voz corrida encima."""
    sid = sb["id"]
    clips = [_clip(sid, c["id"]) for c in sb["cuadros"]]
    falso: Dict[str, Any] = {"id": sid, "modo": "fondo", "ritmo": "segundo", "guion": sb.get("guion") or "",
                             "voz": "", "tono": "cercana", "energia": _film.ENERGIA_FILMADO, "mic": True,
                             "tomas": [{"cartel": c.get("cartel") or "", "dice": ""} for c in sb["cuadros"]],
                             "edicion": {"carteles": False, "zoom": False,
                                         "foto_producto": False, "cierre": sb.get("cierre_on", True) is not False,
                                         "subtitulos": bool(sb.get("subtitulos", True))},
                             "motion": {"subs": sb.get("subs") or "palabras", "fuente": sb.get("fuente") or "moderna"}}
    # Los mismos motions de Filmado: carteles que entran deslizándose (precio, talles, promo) y
    # el cartel final con el precio y el llamado a la acción.
    falso["cierre"] = _film._limpiar_cierre(sb.get("cierre"), {"info": {"precio": sb.get("precio") or "",
                                                                        "producto": sb.get("titulo") or ""}})
    voz: Optional[Tuple[Path, float]] = None
    por_momento = _hay_habla(sb)
    if por_momento:
        # Con momentos que hablan, la voz va POR MOMENTO: lo que dice a cámara cae justo en su
        # momento (con la boca sincronizada) y el resto es su voz en off de cada momento.
        durs0 = [_duracion_video(c) for c in clips]
        voces = [await _voz_momento(sb, doc, c) for c in sb["cuadros"]]
        audio = _dir(sid) / "voz_momentos.mp3"
        await asyncio.to_thread(_film._audio_bloque, [(v[0] if v else None, d) for v, d in zip(voces, durs0)], audio)
        voz = (audio, _duracion_video(audio))
        for t, c, v, d in zip(falso["tomas"], sb["cuadros"], voces, durs0):
            t["dice"], t["voz_seg"] = (c.get("dice") or "", min(v[1], d)) if v else ("", 0)
    elif falso["guion"].strip():
        clave = hashlib.sha1((falso["guion"] + str(doc.get("voz_motor")) + str(doc.get("voz_clon"))).encode()).hexdigest()[:12]
        p = _dir(sid) / f"voz_{clave}.mp3"
        if not p.exists():
            for viejo in _dir(sid).glob("voz_*.mp3"):
                viejo.unlink(missing_ok=True)
            await _film._voz(doc, falso, falso["guion"], p)
        voz = (p, _duracion_video(p))
    tempo = 1.0
    if voz and not por_momento:
        dv = sum(_duracion_video(c) for c in clips)
        tempo = max(1.0, min(_film.GUION_TEMPO_MAX, (voz[1] + _film.GUION_INICIO + 0.3) / max(1.0, dv)))
        falso["_guion_seg"] = round(voz[1] / tempo, 2)
    durs = [_duracion_video(c) for c in clips]
    texto = _film._armar_ass_filmado(falso, durs)
    if sb.get("carteles", True) is not False:
        await _ubicar_manos(sb, durs)
        texto = texto.rstrip("\n") + "\n" + "\n".join(_eventos_carteles(sb, durs)) + "\n"
    ass = ""
    if "Dialogue:" in texto:
        (_dir(sid) / "sb.ass").write_text(texto, encoding="utf-8")
        ass = "sb.ass"
    await asyncio.to_thread(_film._unir, clips, _final(sid), _film.LOOK_DEFAULT, "motor", ["corte"] * len(clips),
                            ["no"] * len(clips), ass, [], _dir(sid))
    if voz:
        await asyncio.to_thread(_film._poner_voz, _final(sid), voz[0], tempo)
    if _es_cine(sb):
        await asyncio.to_thread(_look_cine, _final(sid))
    await asyncio.to_thread(_motion_final, sb, clips)


def _look_cine(final: Path) -> None:
    """Color y textura de cine sobre el reel unido: 24 cuadros, un poco más de contraste, colores
    algo más suaves, grano de película y una viñeta apenas marcada. El audio queda igual."""
    if not final.exists():
        return
    out = final.with_name(final.stem + "_cine.mp4")
    vf = ("fps=24,eq=contrast=1.06:saturation=0.9:gamma=0.98,"
          "colorbalance=rs=0.02:bs=-0.02:rh=0.03:bh=-0.03,noise=alls=7:allf=t,vignette=PI/6,format=yuv420p")
    r = subprocess.run([_film._ffmpeg_bin(), "-y", "-i", str(final), "-vf", vf, "-c:v", "libx264", "-crf", "18",
                        "-preset", "medium", "-c:a", "copy", "-movflags", "+faststart", str(out)],
                       capture_output=True, timeout=1200)
    if r.returncode == 0 and out.exists() and out.stat().st_size > 1000:
        out.replace(final)


_SYSTEM_MANO = (
    "You locate things in a video frame. The image is a vertical frame of a reel where a woman raises her "
    "open hand (palm up) or points with her finger to present a price that will be added in editing. Find "
    "the point just ABOVE her open palm (or the tip of her pointing finger). Answer ONLY JSON: {\"mano\": true, "
    "\"x\": 0.0-1.0 (from the left), \"y\": 0.0-1.0 (from the top)}. If no raised hand or pointing finger "
    "is visible, answer {\"mano\": false}."
)


async def _ubicar_manos(sb: Dict[str, Any], durs: List[float], crudo: Optional[Path] = None) -> None:
    """Dónde quedó su mano en el video filmado (para que el precio aparezca justo ahí). Con `crudo`, la busca en el
    video de corrido, en el segundo de ese momento."""
    pend = [c for c in sb["cuadros"] if c.get("cartel") and c.get("cartel_gesto") and not c.get("cartel_pos")
            and (crudo or _clip(sb["id"], c["id"]).exists())]
    if not pend or not _claude.disponible():
        return
    for c in pend:
        dur = durs[sb["cuadros"].index(c)] if sb["cuadros"].index(c) < len(durs) else float(c["seg"])
        t = min(float(c.get("cartel_t") or 1.0) + 0.7, max(0.1, dur - 0.2))
        k = sb["cuadros"].index(c)
        foto = (await asyncio.to_thread(_film._cuadro, crudo, sum(durs[:k]) + t) if crudo
                else await asyncio.to_thread(_film._cuadro, _clip(sb["id"], c["id"]), t))
        if not foto:
            continue
        try:
            d, costo = await _claude.pedir_json(_SYSTEM_MANO, [{"text": "The frame:"}, _img_part(foto)],
                                                max_tokens=300, esfuerzo="low")
            await budget_record("storyboard_claude", _claude.MODELO, costo, 1, note="storyboard: dónde está la mano")
        except Exception:
            continue
        pos = ({"x": _num(d.get("x"), 0.05, 0.95, 0.5), "y": _num(d.get("y"), 0.12, 0.85, 0.45)}
               if d.get("mano") else {"x": -1, "y": -1})
        async with _lock(sb["id"]):
            fresco = await _sb(sb["id"])
            for x in fresco["cuadros"]:
                if x["id"] == c["id"]:
                    x["cartel_pos"] = pos
            await _guardar(fresco)
        c["cartel_pos"] = pos


def _eventos_carteles(sb: Dict[str, Any], durs: List[float]) -> List[str]:
    """Los carteles de cada momento. Si ella lo presenta con la mano, el precio aparece flotando
    justo sobre su palma (con un pop y subiendo apenas); si no, entra deslizándose de costado."""
    ev: List[str] = []
    t0, n = 0.0, 0
    cuadros = sb.get("cuadros") or []
    for k, (c, dur) in enumerate(zip(cuadros, durs)):
        t1 = t0 + dur
        if c.get("cartel"):
            ini = t0 + min(float(c.get("cartel_t") or 0.4), max(0.2, dur - 0.9))
            fin = t1 - 0.05
            if k == len(cuadros) - 1 and sb.get("cierre_on", True) is not False:
                fin = min(fin, t1 - _film.SEG_CIERRE)
            if fin - ini >= 0.6:
                txt = _film._ass_texto(c["cartel"])
                pos = c.get("cartel_pos") or {}
                if c.get("cartel_gesto") and pos.get("x", -1) >= 0:
                    x = int(max(200, min(880, pos["x"] * 1080)))
                    y = int(max(220, min(1700, pos["y"] * 1920 - 30)))
                    ev.append(f"Dialogue: 3,{_film._ass_tiempo(ini)},{_film._ass_tiempo(fin)},Cartel,,0,0,0,,"
                              f"{{\\an2\\move({x},{y},{x},{y - 18},0,{int((fin - ini) * 1000)})\\fs84\\fscx0\\fscy0"
                              f"\\t(0,170,\\fscx118\\fscy118)\\t(170,300,\\fscx100\\fscy100)\\fad(0,160)}}{txt}")
                else:
                    izq = n % 2 == 0
                    x0, x1 = (-700, 70) if izq else (1780, 1010)
                    ev.append(f"Dialogue: 3,{_film._ass_tiempo(ini)},{_film._ass_tiempo(fin)},Cartel,,0,0,0,,"
                              f"{{\\an{7 if izq else 9}\\move({x0},330,{x1},330,0,320)\\fad(0,180)}}{txt}")
                n += 1
        t0 = t1
    return ev


def _motion_final(sb: Dict[str, Any], clips: List[Path]) -> None:
    """El golpe en cada corte (si lo elegiste) y tu logo en una esquina."""
    final = _final(sb["id"])
    if not final.exists():
        return
    corte = sb.get("corte") or "ninguno"
    if corte in motion.EFECTOS_CORTE and corte != "ninguno" and len(clips) > 1:
        cortes, acc = [], 0.0
        for c in clips[:-1]:
            acc += _duracion_video(c)
            cortes.append(acc)
        out = final.with_name(final.stem + "_cortes.mp4")
        if motion.efectos_en_cortes(final, out, cortes, corte, "9:16"):
            out.replace(final)
    lp = motion.logo_path(_pfx())
    if sb.get("logo") and lp.exists():
        out = final.with_name(final.stem + "_logo.mp4")
        if motion.con_logo(final, out, lp, "9:16", "abajo_der"):
            out.replace(final)


# ─────────────────────────────────────────────────────────────────────────────
# TRABAJOS EN SEGUNDO PLANO
# ─────────────────────────────────────────────────────────────────────────────

async def _correr(jid: str, sub: Optional[str], fn, *args) -> None:
    set_current_sub(sub)
    await hf.cargar_de_ajustes()
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


async def _job_preguntas(jid: str, sid: str) -> str:
    """El director primero pregunta (el pelo, el maquillaje, el precio para los carteles…)."""
    await _job_set(jid, {"paso": "Claude mira la prenda, el lugar y a ella, y prepara sus preguntas…"})
    sb = await _sb(sid)
    doc = await _doc(sb["pid"])
    try:
        q, c = await _preguntas_claude(sb, doc)
        await budget_record("storyboard_claude", _claude.MODELO, c, 1, note="storyboard: preguntas")
    except _claude.ClaudeNoDisponible:
        return await _job_guion(jid, sid)          # sin preguntas: escribe directo
    async with _lock(sid):
        sb = await _sb(sid)
        sb.update(q)
        await _guardar(sb)
    return ""


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
        _crudo(sid).unlink(missing_ok=True)       # guion nuevo = video nuevo
        sb["crudo"] = False
        if plan:
            sb.update(plan)
        else:
            cuadros = [_limpiar_cuadro(sb, {"plano": p_, "angulo": an, "movimiento": mv, "tipo": tp, "seg": sg,
                                            "que_se_ve": qv, "imagen": im, "accion": ac, "movimiento_video": mvv,
                                            "linea": ac, "linea_en": mvv})
                       for p_, an, mv, tp, sg, qv, im, ac, mvv in _PLANTILLA]
            dur, n = int(sb.get("duracion") or 15), 0
            while n < len(cuadros) and sum(c["seg"] for c in cuadros[:n]) < dur:
                n += 1
            sb["cuadros"] = cuadros[:max(2, n)]
            _ajustar_segundos(sb["cuadros"], dur)
            sb["guion"] = sb.get("guion") or _film.DICE_DEFAULT
        await _guardar(sb)
    return aviso


async def _job_dibujar(jid: str, sid: str, cids: List[str], correccion: str, motor_pedido: str = "",
                       solo_final: Optional[List[str]] = None) -> str:
    """Dibuja (e inspecciona) los cuadros pedidos; los de `solo_final` sólo su cuadro final."""
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
    st = await get_settings()
    si = ("si", "sí", "1", "on", "true")
    qc_on = str(st.get("qc_prenda", "si")).lower() in si
    umbral = int(st.get("qc_umbral", 7) or 7)
    reintento = str(st.get("qc_reintento", "si")).lower() in si

    def _qc_vista(qc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not qc:
            return None
        return {"puntaje": int(qc.get("puntaje", 0)), "diferencias": (qc.get("diferencias") or [])[:6],
                "corregido": bool(qc.get("corregido")), "ok": int(qc.get("puntaje", 0)) >= umbral}

    async def revisar(img_b64: str, c: Dict[str, Any], anc: Optional[str]) -> Optional[Dict[str, Any]]:
        """El inspector de Fotos (prenda y anatomía) + el de identidad (su cara y su pelo)."""
        reales = a.reales.get(c["variante"]) or []
        tareas = [verificar_prenda(base64.b64decode(img_b64), reales)] if reales else []
        if c["tipo"] != "producto":
            tareas.append(_inspector_identidad(img_b64, a, anc))
        res = [r for r in await asyncio.gather(*tareas) if isinstance(r, dict)] if tareas else []
        if not res:
            return None
        difs: List[str] = []
        for r in res:
            difs += [d for d in (r.get("diferencias") or []) if d not in difs]
        return {"puntaje": min(int(r.get("puntaje", 10)) for r in res), "diferencias": difs[:8]}

    async def dibujar_revisado(c: Dict[str, Any], anc: Optional[str], corr: str, motor_c: str,
                               inicio: Optional[str] = None) -> Tuple[str, str, str, Optional[Dict[str, Any]]]:
        b64, motor, nota, _ = await _dibujar(sb, c, a, anc, corr, motor_c, inicio)
        if not qc_on:
            return b64, motor, nota, None
        n = sb["cuadros"].index(c) + 1
        await _job_set(jid, {"paso": f"El inspector revisa el cuadro {n}{' (final)' if inicio else ''}…"})
        qc = await revisar(b64, c, anc)
        if qc and reintento and qc["puntaje"] < umbral and qc["diferencias"]:
            arreglo = "; ".join(qc["diferencias"])
            b2, m2, n2, _ = await _dibujar(sb, c, a, anc, (corr + "; " if corr else "") + arreglo, motor_c, inicio)
            qc2 = await revisar(b2, c, anc)
            if qc2 is None or qc2["puntaje"] >= qc["puntaje"]:
                b64, motor, nota, qc = b2, m2, n2, (qc2 or qc)
            qc = dict(qc, corregido=True)
        return b64, motor, nota, qc

    async def uno(c: Dict[str, Any]) -> None:
        nonlocal ancla
        async with sem:
            try:
                corr = correccion if len(cids) == 1 else ""
                motor_c = _motor_cuadro(sb, motor_pedido)
                anc = None if c is primero else ancla
                cambios: Dict[str, Any] = {"error": ""}
                parte = "final" if c["id"] in (solo_final or []) else ""
                if parte != "final":
                    b64, motor, nota, qc = await dibujar_revisado(c, anc, corr, motor_c)
                    previa = await kv.get(_k_img(sid, c["id"]))
                    if previa:
                        await kv.set(_k_img_prev(sid, c["id"]), previa)
                    await kv.set(_k_img(sid, c["id"]), b64)
                    if c is primero:
                        ancla = b64
                    cambios.update({"img": True, "motor_img": motor, "nota_img": nota, "qc": _qc_vista(qc),
                                    "hay_previa": bool(previa), "motor_previa": c.get("motor_img", "") if previa else ""})
                else:
                    b64 = await kv.get(_k_img(sid, c["id"]))
                    if not b64:
                        raise RuntimeError("primero dibujá el cuadro de arranque")
                # EL CUADRO FINAL: si arranca de espalda y se da vuelta, el final con su cara real
                # (el video va de un cuadro al otro: no puede inventarle la cara al girar).
                if _con_final(c):
                    fin, _, nota_f, qc_f = await dibujar_revisado(c, anc or (None if c is primero else ancla),
                                                                  corr if parte == "final" else "", motor_c, b64)
                    await kv.set(_k_img_fin(sid, c["id"]), fin)
                    cambios.update({"img_fin": True, "qc_fin": _qc_vista(qc_f), "nota_fin": nota_f})
                cambios.update({"aprobado": False, "clip": False, "ts": int(time.time())})
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


async def _job_filmar(jid: str, sid: str, solo: str = "") -> str:
    """Filma los tramos que faltan y monta. Con `solo`, filma (o refilma) SÓLO ese cuadro, de prueba,
    y no monta."""
    sb = await _sb(sid)
    doc = await _doc(sb["pid"])
    key = await _fal_key()
    if not key:
        raise RuntimeError("Falta la API key de fal.")
    a = await _activos(sb, doc)
    faltan = ([c for c in sb["cuadros"] if c["id"] == solo] if solo
              else [c for c in sb["cuadros"] if not _clip(sid, c["id"]).exists()])
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    fallas: List[str] = []
    motivos: List[str] = []
    async with httpx.AsyncClient(timeout=300) as cli:
        if faltan:
            u_ella: List[str] = []
            u_prenda: Dict[int, List[str]] = {}
            refs: Dict[str, Any] = {}
            if _es_ref(sb):
                await _job_set(jid, {"paso": "Subiendo sus referencias (cara, cuerpo con la prenda, lugar)…"})
                refs = await _subir_refs(cli, key, sb, a, {c["variante"] for c in faltan})
                refs["rasgos"] = await _rasgos_en(doc, a)
            # Con el motor común (foto → video) el cuadro aprobado ya tiene su cara y la prenda: no hace falta
            # subirlas aparte (antes decía "Subiendo a fal…" aunque filmara Higgsfield o Kling).
            if _es_ref(sb):
                vm = sb["motor_vm_ref"]
                await _job_set(jid, {"paso": f"Creando con {VM.MODELOS[vm['modelo']]['nombre']} por "
                                             f"{VM.PROVEEDORES[vm['proveedor']]}…"})
            elif _motor_video(sb) != "vm":
                await _job_set(jid, {"paso": "Subiendo su cara y la prenda (referencias)…"})
                ella = [a.caras[0][1]] + ([a.retrato] if a.retrato else [])
                u_ella = [await _fal_subir(cli, key, base64.b64decode(b), "image/jpeg", f"sb-{sid}-ella{i}.jpg")
                          for i, b in enumerate(ella)]
            else:
                vm = sb["motor_vm"]
                await _job_set(jid, {"paso": f"Filmando con {VM.MODELOS[vm['modelo']]['nombre']} por "
                                             f"{VM.PROVEEDORES[vm['proveedor']]}…"})
            for v in ({c["variante"] for c in faltan} if _motor_video(sb) not in ("vm", "vm_ref") else set()):
                fotos = (a.probador.get(v) or [])[:2] + (a.reales.get(v) or [])[:1]
                u_prenda[v] = [await _fal_subir(cli, key, base64.b64decode(b), "image/jpeg", f"sb-{sid}-p{v}-{i}.jpg")
                               for i, b in enumerate(fotos)]
            hechos = [0]
            sem = asyncio.Semaphore(PARALELO)
            if refs and not solo and _corrido_ok(sb):
                # DE CORRIDO: un solo pedido y el video queda CRUDO, sin cortar ni tocar. Motion, voz y que
                # hable a cámara, sólo si después la usuaria los pide (✨ Terminar).
                await _filmar_corrido(cli, key, jid, sb, refs, a)
                import shutil as _sh
                _sh.copyfile(_crudo(sid), _final(sid))
                link = await _guardar_en_drive(f"{_slug(doc.get('nombre', ''))}-storyboard-{sid}-crudo.mp4",
                                               _crudo(sid).read_bytes(), "video/mp4")
                async with _lock(sid):
                    fresco = await _sb(sid)
                    fresco.update({"video": True, "crudo": True, "drive": link, "nota_corrido": sb.get("nota_corrido", ""),
                                   "seg_final": round(_duracion_video(_final(sid)), 1)})
                    for x in fresco["cuadros"]:
                        _clip(sid, x["id"]).unlink(missing_ok=True)
                        x.update({"clip": False, "error": ""})
                    await _guardar(fresco)
                return ""

            async def uno(c: Dict[str, Any]) -> None:
                async with sem:
                    try:
                        if refs:
                            await _filmar_ref(cli, key, sb, c, refs, a)
                        else:
                            await _filmar_cuadro(cli, key, headers, jid, sb, c, u_ella, u_prenda, a)
                        if c.get("habla"):
                            _, nota_ls = await _hablar(cli, key, headers, jid, sb, doc, c)
                            c["nota_video"] = " ".join(x for x in (c.get("nota_video"), nota_ls) if x)
                        cambios = {"clip": True, "error": "", "motor_video": c.get("motor_video", ""),
                                   "nota_video": c.get("nota_video", ""), "cartel_pos": None}
                    except Exception as e:
                        fallas.append(f"cuadro {sb['cuadros'].index(c) + 1}")
                        cambios = {"clip": False, "error": str(getattr(e, "detail", "") or e)[:300]}
                        motivos.append(cambios["error"])
                        print(f"[storyboard] {sid} cuadro {c['id']} no salió: {cambios['error']}")
                async with _lock(sid):
                    fresco = await _sb(sid)
                    for x in fresco["cuadros"]:
                        if x["id"] == c["id"]:
                            x.update(cambios)
                    await _guardar(fresco)
                hechos[0] += 1
                await _job_set(jid, {"paso": f"Filmando: {hechos[0]} de {len(faltan)} tramos" + (" (con sus referencias)…" if refs else " (cada uno desde su cuadro)…")})

            await asyncio.gather(*(uno(c) for c in faltan))
    if fallas:
        if solo:
            c = (await _sb(sid))["cuadros"]
            raise RuntimeError(next((x.get("error") for x in c if x["id"] == solo), "") or "No salió.")
        # El motivo va a la vista: si fallaron todos por lo mismo (clave, ruta, saldo), se ve sin buscar cuadro por cuadro.
        motivo = max(set(motivos), key=motivos.count) if motivos else ""
        raise RuntimeError("No salieron: " + ", ".join(fallas) + (f". Motivo: {motivo}" if motivo else "")
                           + ". Los que salieron quedaron: tocá Filmar de nuevo.")
    if solo:
        return ""
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


_VOZ_LIMPIA = {"voz": "", "tono": "cercana", "energia": _film.ENERGIA_FILMADO, "mic": False}


def _asegurar_precio(sb: Dict[str, Any]) -> None:
    """Si hay precio y ningún momento lo muestra, va en el momento donde ella presenta con la mano (o en el
    anteúltimo, que no tapa el cartel final)."""
    precio = (sb.get("precio") or "").strip()
    cuadros = sb.get("cuadros") or []
    if not precio or len(cuadros) < 2:
        return
    num = re.sub(r"\D", "", precio)
    if any(num and num in re.sub(r"\D", "", c.get("cartel") or "") for c in cuadros):
        return
    c = next((x for x in cuadros if x.get("cartel_gesto")), None) or cuadros[-2]
    if not c.get("cartel"):
        c["cartel"] = (f"${int(num):,}".replace(",", ".") if precio.replace(".", "").isdigit()
                       else (precio if precio.startswith("$") else f"${precio}"))
        c["cartel_t"] = c.get("cartel_t") or 1.0


def _fps(video: Path) -> str:
    """Los cuadros por segundo del video (para no cambiarlos: pasar de 24 a 30 lo hace saltar)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                            "-of", "csv=p=0", str(video)], capture_output=True, text=True, timeout=30)
        v = r.stdout.strip()
        return v if re.fullmatch(r"\d+(/\d+)?", v) else "24"
    except Exception:
        return "24"


async def _montar_crudo(jid: str, sb: Dict[str, Any], doc: Dict[str, Any], opc: Dict[str, bool]) -> None:
    """El MOTION sobre el video de corrido, SIN cortarlo ni cambiarle la velocidad: carteles y precio (sobre su mano
    si lo presenta), cartel final, su voz en off con subtítulos y, si marcó, la boca sincronizada en los momentos
    que habla. La voz nunca estira el video: si no entra, se apura un poco y al final se apaga."""
    sid = sb["id"]
    crudo = _crudo(sid)
    cuadros = sb["cuadros"]
    total = _duracion_video(crudo)
    segs = [max(SEG_FILMA, int(c["seg"])) for c in cuadros]
    durs = [s * total / sum(segs) for s in segs]
    fps = _fps(crudo)
    base = crudo
    # 1) Que hable a cámara: sólo esos momentos pasan por el lip-sync y vuelven al MISMO ritmo de cuadros.
    hablan = [k for k, c in enumerate(cuadros) if c.get("habla") and (c.get("dice") or "").strip()] if opc["hablar"] else []
    if hablan:
        key = await _fal_key()
        headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
        piezas: List[Path] = []
        async with httpx.AsyncClient(timeout=300) as cli:
            for k, c in enumerate(cuadros):
                pz = _dir(sid) / f"pz{k}.mp4"
                await asyncio.to_thread(_film._ff, ["-y", "-ss", f"{sum(durs[:k]):.3f}", "-i", str(crudo), "-t",
                                                    f"{durs[k]:.3f}", "-an", "-c:v", "libx264", "-crf", "15", str(pz)])
                if k in hablan:
                    await _job_set(jid, {"paso": f"Sincronizando su boca en el momento {k + 1}…"})
                    pz.replace(_clip(sid, c["id"]))
                    _, nota = await _hablar(cli, key, headers, jid, sb, doc, c)
                    c["nota_video"] = nota
                    await asyncio.to_thread(_film._ff, ["-y", "-i", str(_clip(sid, c["id"])), "-t", f"{durs[k]:.3f}",
                                                        "-vf", f"scale=1080:1920:flags=lanczos,fps={fps}", "-an",
                                                        "-c:v", "libx264", "-crf", "15", str(pz)])
                else:
                    await asyncio.to_thread(_film._ff, ["-y", "-i", str(pz), "-vf", "scale=1080:1920:flags=lanczos",
                                                        "-c:v", "libx264", "-crf", "15", str(pz.with_suffix(".b.mp4"))])
                    pz.with_suffix(".b.mp4").replace(pz)
                piezas.append(pz)
        lista = _dir(sid) / "piezas.txt"
        lista.write_text("".join(f"file '{p.name}'\n" for p in piezas))
        base = _dir(sid) / "con_boca.mp4"
        await asyncio.to_thread(_film._ff, ["-y", "-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy", str(base)],
                                600, _dir(sid))
        for p in piezas:
            p.unlink(missing_ok=True)
    # 2) La voz: por momento si alguno habla (lo de cada momento cae en su lugar), si no la voz en off corrida.
    audio: Optional[Path] = None
    falso: Dict[str, Any] = {"id": sid, "modo": "fondo", "ritmo": "segundo", "guion": sb.get("guion") or "",
                             "voz": "", "tono": "cercana", "energia": _film.ENERGIA_FILMADO, "mic": False,
                             "tomas": [{"cartel": "", "dice": ""} for _ in cuadros],
                             "edicion": {"carteles": False, "zoom": False, "foto_producto": False,
                                         "cierre": opc["cierre"], "subtitulos": opc["voz"] and opc["subtitulos"]},
                             "motion": {"subs": sb.get("subs") or "palabras", "fuente": sb.get("fuente") or "moderna"}}
    falso["cierre"] = _film._limpiar_cierre(sb.get("cierre"), {"info": {"precio": sb.get("precio") or "",
                                                                        "producto": sb.get("titulo") or ""}})
    tempo = 1.0
    if hablan or (opc["voz"] and any((c.get("dice") or "").strip() for c in cuadros) and not falso["guion"].strip()):
        await _job_set(jid, {"paso": "Grabando su voz de cada momento…"})
        voces = []
        for c in cuadros:
            v = await _voz_momento(sb, doc, c) if (opc["voz"] or c.get("habla")) else None
            voces.append(v)
        audio = _dir(sid) / "voz_momentos.mp3"
        await asyncio.to_thread(_film._audio_bloque, [(v[0] if v else None, d) for v, d in zip(voces, durs)], audio)
        for t, c, v, d in zip(falso["tomas"], cuadros, voces, durs):
            t["dice"], t["voz_seg"] = (c.get("dice") or "", min(v[1], d)) if v else ("", 0)
    elif opc["voz"] and falso["guion"].strip():
        await _job_set(jid, {"paso": "Grabando su voz en off…"})
        p = _dir(sid) / f"vozoff_{hashlib.sha1(falso['guion'].encode()).hexdigest()[:10]}.mp3"
        if not p.exists():
            await _film._voz(doc, dict(_VOZ_LIMPIA), falso["guion"], p)
        audio = p
        tempo = max(1.0, min(_film.GUION_TEMPO_MAX, (_duracion_video(p) + _film.GUION_INICIO + 0.4) / max(1.0, total)))
        falso["_guion_seg"] = round(_duracion_video(p) / tempo, 2)
    # 3) Los textos: subtítulos, carteles (el precio siempre que haya) y el cartel final.
    texto = _film._armar_ass_filmado(falso, durs)
    if opc["carteles"]:
        _asegurar_precio(sb)
        await _ubicar_manos(sb, durs, crudo)
        texto = texto.rstrip("\n") + "\n" + "\n".join(_eventos_carteles(sb, durs)) + "\n"
    await _job_set(jid, {"paso": "Poniendo los carteles y su voz…"})
    vf = "scale=1080:1920:flags=lanczos"
    if "Dialogue:" in texto:
        (_dir(sid) / "sb.ass").write_text(texto, encoding="utf-8")
        vf += ",subtitles=sb.ass"
    if _es_cine(sb) and sb.get("look_cine_extra"):
        vf += ",eq=contrast=1.04:saturation=0.96"
    cmd = ["-y", "-i", str(base)]
    if audio:
        ms = int(_film.GUION_INICIO * 1000) if tempo > 1.0 or not hablan else 0
        af = (f"[1:a]{f'atempo={tempo:.3f},' if tempo > 1.001 else ''}adelay={ms}|{ms},aresample=48000,"
              f"aformat=channel_layouts=stereo,apad,atrim=0:{total:.3f},afade=t=out:st={max(0.0, total - 0.5):.3f}:d=0.5[a]")
        cmd += ["-i", str(audio), "-filter_complex", f"[0:v]{vf}[v];{af}", "-map", "[v]", "-map", "[a]",
                "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-vf", vf, "-an"]
    # Mismo largo y mismos cuadros por segundo que el crudo: nada se congela ni se traba.
    await asyncio.to_thread(_film._ff, cmd + ["-t", f"{total:.3f}", "-c:v", "libx264", "-crf", "16", "-preset",
                                              "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                                              str(_final(sid))], 900, _dir(sid))
    if base != crudo:
        base.unlink(missing_ok=True)
    lp = motion.logo_path(_pfx())
    if sb.get("logo") and lp.exists():
        out = _final(sid).with_name(_final(sid).stem + "_logo.mp4")
        if motion.con_logo(_final(sid), out, lp, "9:16", "abajo_der"):
            out.replace(_final(sid))


async def _job_terminar(jid: str, sid: str, opc: Optional[Dict[str, bool]] = None) -> str:
    """El crudo + lo que la usuaria pidió al final: que hable a cámara (los momentos marcados) y el montaje con su
    voz, carteles, precio y cartel final."""
    sb = await _sb(sid)
    if not _crudo(sid).exists():
        raise RuntimeError("No encuentro el video crudo: filmalo de nuevo.")
    doc = await _doc(sb["pid"])
    opc = opc or {"carteles": True, "cierre": True, "voz": False, "subtitulos": True, "hablar": True}
    await _montar_crudo(jid, sb, doc, opc)
    link = await _guardar_en_drive(f"{_slug(doc.get('nombre', ''))}-storyboard-{sid}.mp4", _final(sid).read_bytes(),
                                   "video/mp4")
    async with _lock(sid):
        fresco = await _sb(sid)
        por_id = {c["id"]: c for c in sb["cuadros"]}
        for x in fresco["cuadros"]:
            if x["id"] in por_id:
                x.update({k: por_id[x["id"]].get(k) for k in ("nota_video", "cartel_pos", "cartel", "cartel_t")})
        fresco["terminado"] = opc
        fresco.update({"video": True, "crudo": False, "drive": link, "seg_final": round(_duracion_video(_final(sid)), 1)})
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

async def _hf_clave() -> None:
    await hf.cargar_de_ajustes()       # la clave de Higgsfield pegada en la app (si la hay)


router = APIRouter(dependencies=[Depends(_bind), Depends(_hf_clave)])


def _costo_cuadro() -> float:
    return 0.10


def _vista(sb: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(sb)
    mv = _mv_de(sb)
    out["motor_video"] = _motor_video(sb)
    out["motor_cuadros"] = _motor_cuadro(sb)
    cuadros = []
    for c in sb.get("cuadros") or []:
        x = dict(c)
        x["url"] = f"{API}/{sb['id']}/cuadro/{c['id']}.jpg?t={c.get('ts', 0)}" if c.get("img") else ""
        x["clip_url"] = f"{API}/{sb['id']}/cuadro/{c['id']}/clip.mp4?t={c.get('ts', 0)}" if _clip(sb["id"], c["id"]).exists() else ""
        x["precio_filmar"] = round(_precio_tramo(MOTORES_VIDEO["fal_kling_o3"] if _va_por_fal(sb, c) else mv,
                                                 c["seg"])
                                   + (c["seg"] * _film.LIPSYNCS["sync"]["precio_seg"] if c.get("habla") else 0), 2)
        x["url_fin"] = f"{API}/{sb['id']}/cuadro/{c['id']}-final.jpg?t={c.get('ts', 0)}" if c.get("img_fin") else ""
        x["con_final"] = _con_final(c)
        x["guia_url"] = f"{API}/{sb['id']}/cuadro/{c['id']}/guia.jpg?t={c.get('guia_ts', 0)}" if c.get("guia") else ""
        x.pop("escena", None)
        x["por_fal"] = _va_por_fal(sb, c) and _motor_video(sb).startswith("hf_")
        if _es_ref(sb):
            x["precio_filmar"] = round(_precio_tramo(mv, c["seg"])
                                       + (c["seg"] * _film.LIPSYNCS["sync"]["precio_seg"] if c.get("habla") else 0), 2)
            x["por_fal"] = False
        cuadros.append(x)
    out["cuadros"] = cuadros
    out["sin_cuadros"] = _es_ref(sb)
    out["de_corrido"] = _corrido_ok(sb)
    out["de_corrido_off"] = sb.get("de_corrido") is False
    out["cine_ficha"] = json.dumps(_cine_de(sb), ensure_ascii=False, indent=1)
    out["video_ref_url"] = (f"{API}/{sb['id']}/video_ref.mp4?t={sb.get('video_ref')}"
                            if _video_ref(sb["id"]).exists() else "")
    out["crudo_url"] = f"{API}/{sb['id']}/crudo.mp4?t={sb.get('actualizado', '')}" if _crudo(sb["id"]).exists() else ""
    out["seg_total"] = sum(c["seg"] for c in cuadros)
    out["faltan_img"] = sum(1 for c in cuadros if not c.get("img"))
    out["sin_aprobar"] = sum(1 for c in cuadros if c.get("img") and not c.get("aprobado"))
    out["costo_cuadros"] = round(out["faltan_img"] * MOTORES_CUADRO[_motor_cuadro(sb)]["precio"], 2)
    out["costo_video"] = round(sum(c["precio_filmar"] for c in cuadros if not c.get("clip_url")), 2)
    out["cierre_final"] = _film._limpiar_cierre(sb.get("cierre"), {"info": {"precio": sb.get("precio") or "",
                                                                             "producto": sb.get("titulo") or ""}})
    out["logo_cargado"] = motion.logo_path(_pfx()).exists()
    out["video_url"] = f"{API}/{sb['id']}/video.mp4?t={sb.get('actualizado', '')}" if _final(sb["id"]).exists() else ""
    if sb.get("crudo") and out.get("crudo_url"):
        out["video_url"] = out["crudo_url"]      # el crudo ES el video hasta que pida motion
    return out


@router.get(ROUTE_PREFIX + "/3d", response_class=HTMLResponse)
async def ui_3d() -> HTMLResponse:
    movs = json.dumps({k: v[0] for k, v in _film.MOVIMIENTOS.items()}, ensure_ascii=False)
    return HTMLResponse(PAGINA_3D.replace("%%API%%", API).replace("%%MOVS%%", movs))


@router.get(API + "/{sid}/escena3d")
async def api_escena3d(sid: str) -> Dict[str, Any]:
    """La escena 3D del reel (un momento por cuadro), el storyboard y tu lugar de fondo."""
    sb = await _sb(sid)
    lugar = ""
    if sb.get("lugar_ref"):
        b = await refs_luma.vista_para(sb["lugar_ref"], "entero")
        if b:
            lugar = "data:image/jpeg;base64," + _compress_ref(base64.b64decode(b), max_dim=1400, q=85)
    return {"storyboard": _vista(sb), "escena3d": sb.get("escena3d"), "lugar": lugar, "luz": sb.get("luz") or ""}


@router.post(API + "/{sid}/escena3d")
async def api_escena3d_guardar(sid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    e = payload.get("escena3d")
    if not isinstance(e, dict) or len(json.dumps(e)) > 400000:
        raise HTTPException(400, "La escena no se pudo guardar.")
    async with _lock(sid):
        sb = await _sb(sid)
        sb["escena3d"] = e
        if e.get("luz") in LUCES_3D and e["luz"] != sb.get("luz"):
            sb["luz"] = e["luz"]
        await _guardar(sb)
    return {"ok": True}


_SYSTEM_BLOCKING = (
    "You are the director of an Instagram lingerie reel doing the 3D PREVIS (blocking with a grey mannequin) "
    "of a storyboard. The set: the real place is a backdrop behind her (at z = -2.6 m); she stands around the "
    "centre (x and z between -1.2 and 1.2 m). For EACH moment decide: \"ini\" = her pose when the moment "
    "starts and \"fin\" = her pose when it ends (empty if she holds about the same pose), both from this list "
    "(key: meaning): {poses}. Also \"x\" and \"z\" (where she is, metres; keep her in the same spot between "
    "moments unless she walks) and \"giro\" (degrees her body faces: 0 = towards the front of the set; the "
    "camera of each moment is placed from its plano and angulo relative to her body). Rules: a moment whose "
    "angulo is espalda starts with espalda_mira or de_pie seen from behind; if its timeline says she turns to "
    "the camera, fin = de_pie or presenta; when she shows the price, fin or ini = presenta; a mirror selfie = "
    "selfie; sitting or lying only if the place has a bed or a seat. Also choose \"luz\", the light of the whole "
    "reel, one of: {luces}. Answer ONLY JSON: {{\"luz\": \"...\", \"nota\": \"one short sentence in Spanish "
    "about the staging\", \"momentos\": [{{\"n\": 1, \"ini\": \"...\", \"fin\": \"\", \"x\": 0, \"z\": 0, \"giro\": 0}}]}}"
)


@router.post(API + "/{sid}/escena3d/director")
async def api_escena3d_director(sid: str) -> Dict[str, Any]:
    """El director arma la escena 3D del reel a partir del storyboard (una llamada a Claude)."""
    sb = await _sb(sid)
    if not sb.get("cuadros"):
        raise HTTPException(400, "Primero el storyboard.")
    if not _claude.disponible():
        raise HTTPException(400, "Claude no está disponible ahora.")
    lugar = await refs_luma.lugar(sb.get("lugar_ref") or "") if sb.get("lugar_ref") else None
    momentos = "\n".join(
        f"{i + 1}. {c['seg']} s · tipo {c['tipo']} · plano {c['plano']} · angulo {c['angulo']} · camera {c['movimiento']}"
        f" · {c.get('que_se_ve', '')} · timeline: {(c.get('linea') or c.get('accion') or '').replace(chr(10), ' / ')}"
        + (" · she talks to the camera" if c.get("habla") else "") for i, c in enumerate(sb["cuadros"]))
    texto = (f"THE PLACE: {(lugar or {}).get('nombre', '')} — {(lugar or {}).get('desc', '')}\n"
             f"THE MOMENTS:\n{momentos}")
    partes: List[Dict[str, Any]] = [{"text": texto}]
    lug = await refs_luma.vista_para(sb["lugar_ref"], "entero") if sb.get("lugar_ref") else None
    if lug:
        partes += [{"text": "The place:"}, _img_part(_compress_ref(base64.b64decode(lug), max_dim=900, q=85))]
    system = _SYSTEM_BLOCKING.format(poses="; ".join(f"{k}: {v}" for k, v in POSES_3D.items()),
                                     luces="; ".join(f"{k}: {v}" for k, v in LUCES_3D.items()))
    try:
        data, costo = await _claude.pedir_json(system, partes, max_tokens=3000, esfuerzo="low")
    except _claude.ClaudeNoDisponible as e:
        raise HTTPException(502, f"El director no pudo: {e}")
    await budget_record("storyboard_claude", _claude.MODELO, costo, 1, note="storyboard: escena 3D")
    out: Dict[str, Any] = {}
    for m in data.get("momentos") or []:
        try:
            c = sb["cuadros"][int(m.get("n")) - 1]
        except (TypeError, ValueError, IndexError):
            continue
        out[c["id"]] = {"ini": m.get("ini") if m.get("ini") in POSES_3D else "de_pie",
                        "fin": m.get("fin") if m.get("fin") in POSES_3D else "",
                        "x": _num(m.get("x"), -1.5, 1.5, 0), "z": _num(m.get("z"), -1.5, 1.5, 0),
                        "giro": _num(m.get("giro"), -180, 180, 0)}
    return {"momentos": out, "luz": data.get("luz") if data.get("luz") in LUCES_3D else "",
            "nota": _texto(data.get("nota"), 200)}


@router.get(API + "/{sid}/cuadro/{cid}/escena")
async def api_escena(sid: str, cid: str) -> Dict[str, Any]:
    """Lo que el Estudio 3D necesita: la escena guardada, el cuadro y tu lugar de fondo."""
    sb = await _sb(sid)
    c = next((x for x in sb["cuadros"] if x["id"] == cid), None)
    if not c:
        raise HTTPException(404, "No encuentro ese cuadro.")
    lugar = ""
    if sb.get("lugar_ref"):
        b = await refs_luma.vista_para(sb["lugar_ref"], "entero")
        if b:
            lugar = "data:image/jpeg;base64," + _compress_ref(base64.b64decode(b), max_dim=1400, q=85)
    return {"escena": c.get("escena") or None, "lugar": lugar, "plano": c["plano"], "angulo": c["angulo"],
            "que_se_ve": c.get("que_se_ve", ""), "n": sb["cuadros"].index(c) + 1}


@router.post(API + "/{sid}/cuadro/{cid}/escena")
async def api_escena_guardar(sid: str, cid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    img = _strip_data_url(str(payload.get("imagen") or ""))
    if not img:
        raise HTTPException(400, "Falta la foto de la escena.")
    try:
        b64 = _compress_ref(base64.b64decode(img), max_dim=1280, q=90)
    except Exception:
        raise HTTPException(400, "La foto de la escena no se pudo leer.")
    escena = payload.get("escena") if isinstance(payload.get("escena"), dict) else None
    if escena and len(json.dumps(escena)) > 60000:
        escena = None
    async with _lock(sid):
        sb = await _sb(sid)
        c = next((x for x in sb["cuadros"] if x["id"] == cid), None)
        if not c:
            raise HTTPException(404, "No encuentro ese cuadro.")
        await kv.set(_k_guia(sid, cid), b64)
        c.update({"guia": True, "guia_desc": _texto(payload.get("descripcion"), 300), "escena": escena,
                  "guia_ts": int(time.time()), "aprobado": False})
        if c.get("img"):
            c["desactualizada"] = True
        await _guardar(sb)
    return {"ok": True}


@router.delete(API + "/{sid}/cuadro/{cid}/escena")
async def api_escena_borrar(sid: str, cid: str) -> Dict[str, Any]:
    async with _lock(sid):
        sb = await _sb(sid)
        for c in sb["cuadros"]:
            if c["id"] == cid:
                c.update({"guia": False, "guia_desc": "", "escena": None})
                await kv.delete(_k_guia(sid, cid))
        await _guardar(sb)
    return {"storyboard": _vista(sb)}


@router.get(API + "/{sid}/cuadro/{cid}/guia.jpg")
async def api_guia(sid: str, cid: str):
    b = await kv.get(_k_guia(sid, cid))
    if not b:
        raise HTTPException(404, "Ese cuadro no tiene pose 3D.")
    return Response(base64.b64decode(b), media_type="image/jpeg")


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(PAGINA.replace("%%API%%", API).replace("%%VERSION%%", VERSION).replace("%%PREFIJO%%", ROUTE_PREFIX))


@router.get(API + "/config")
async def api_config() -> Dict[str, Any]:
    return {"planos": {k: v[0] for k, v in _film.PLANOS.items()}, "angulos": {k: v[0] for k, v in _film.ANGULOS.items()},
            "movimientos": {k: v[0] for k, v in _film.MOVIMIENTOS.items()}, "tipos": TIPOS, "duraciones": DURACIONES,
            "claude": _claude.disponible(), "costo_cuadro": _costo_cuadro(), "higgsfield": hf.disponible(),
            "cortes": motion.EFECTOS_CORTE, "camaras": CAMARAS,
            "motor_video_default": _motor_video({}),
            "motores_video": {k: (v["label"] if k in ("vm", "vm_ref") else f"{v['label']} · US${v['precio_seg']:.3f}/s sin sonido")
                              for k, v in MOTORES_VIDEO.items() if not k.startswith("hf_") or hf.disponible()},
            "motores_cuadro": {k: f"{v['label']} · ~US${v['precio']:.3f} c/u" for k, v in MOTORES_CUADRO.items()
                               if not k.startswith("hf_") or hf.disponible()},
            "precios_cuadro": {k: v["precio"] for k, v in MOTORES_CUADRO.items()}}


@router.get(API + "/higgsfield")
async def api_higgsfield() -> Dict[str, Any]:
    ok, msg = await hf.probar()
    return {"ok": ok, "mensaje": msg, "origen": hf.origen()}


@router.post(API + "/higgsfield")
async def api_higgsfield_guardar(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Guarda la clave de Higgsfield en la cuenta (Key ID y Key Secret, o pegados juntos) y la prueba.
    El secreto nunca vuelve a la pantalla."""
    from imagenes_ia import save_settings
    kid, sec = str(payload.get("key_id") or "").strip(), str(payload.get("key_secret") or "").strip()
    if payload.get("borrar"):
        kid = sec = ""
    elif kid and not sec:
        h = hf.armar(junta=kid)
        if h.startswith("Key ") and ":" in h:
            kid, sec = h[4:].split(":", 1)
    elif not (kid and sec):
        raise HTTPException(400, "Pegá el Key ID y el Key Secret (o los dos juntos, separados por dos puntos).")
    await save_settings({"higgsfield_key_id": kid, "higgsfield_key_secret": sec})
    await hf.cargar_de_ajustes()
    ok, msg = await hf.probar() if hf.disponible() else (False, "Clave borrada de la app.")
    return {"ok": ok, "mensaje": msg, "origen": hf.origen()}


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
          "hablar": payload.get("hablar", True) is not False and payload.get("modo") != "corrido",
          **({"corrido": True, "motor_video": "vm_ref",
              "motor_vm_ref": {"modelo": "kling40_ref", "proveedor": "higgsfield" if hf.disponible() else "fal",
                               "calidad": "std"}} if payload.get("modo") == "corrido" else {}),
          "camara": payload.get("camara") if payload.get("camara") in CAMARAS else "celular",
          "guion": "", "cuadros": [], "subtitulos": True, "creado": time.strftime("%Y-%m-%d %H:%M")}
    await _guardar(sb)
    await kv.set(_k_idx(), [sid] + [x for x in ((await kv.get(_k_idx())) or []) if x != sid][:40])
    jid = (await _lanzar(sb, "preguntas", 60, _job_preguntas) if _claude.disponible()
           else await _lanzar(sb, "guion", 120, _job_guion))
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
        if "cine_en" in payload:
            v = payload["cine_en"]
            if isinstance(v, str):
                try:
                    v = json.loads(v or "{}")
                except ValueError:
                    raise HTTPException(400, "La ficha de fotografía no es un JSON válido: revisá comas y comillas.")
            if not isinstance(v, dict):
                raise HTTPException(400, "La ficha de fotografía tiene que ser un objeto JSON.")
            sb["cine_en"] = v
        if "prompt_corrido_en" in payload:
            sb["prompt_corrido_en"] = _texto(payload["prompt_corrido_en"], 4000)
        if "de_corrido" in payload:
            sb["de_corrido"] = payload["de_corrido"] is not False
        if isinstance(payload.get("respuestas"), list):
            for i, r in enumerate(payload["respuestas"][:len(sb.get("preguntas") or [])]):
                sb["preguntas"][i]["respuesta"] = _texto(r, 300)
        for k in ("carteles", "cierre_on", "logo"):
            if k in payload:
                sb[k] = payload[k] is not False
        if payload.get("camara") in CAMARAS and payload["camara"] != sb.get("camara"):
            sb["camara"] = payload["camara"]
            _final(sid).unlink(missing_ok=True)
        if payload.get("corte") in motion.EFECTOS_CORTE:
            sb["corte"] = payload["corte"]
        if "precio" in payload:
            sb["precio"] = _texto(payload["precio"], 40)
        if "estilo_en" in payload:
            sb["estilo_en"] = _texto(payload["estilo_en"], 400)
        if isinstance(payload.get("cierre"), dict):
            sb["cierre"] = {"titulo": _texto(payload["cierre"].get("titulo"), 40),
                            "linea": _texto(payload["cierre"].get("linea"), 60)}
        if payload.get("motor_video") in MOTORES_VIDEO:
            sb["motor_video"] = payload["motor_video"]
        if _vm_ok(payload.get("motor_vm")):
            v = payload["motor_vm"]
            sb["motor_vm"] = {"modelo": v["modelo"], "proveedor": v["proveedor"],
                              "calidad": "pro" if v.get("calidad") == "pro" else "std"}
        if _vm_ok(payload.get("motor_vm_ref"), "ref"):
            v = payload["motor_vm_ref"]
            sb["motor_vm_ref"] = {"modelo": v["modelo"], "proveedor": v["proveedor"],
                                  "calidad": "pro" if v.get("calidad") == "pro" else "std"}
        if payload.get("motor_cuadros") in MOTORES_CUADRO:
            sb["motor_cuadros"] = payload["motor_cuadros"]
        if str(payload.get("duracion") or "").isdigit() and int(payload["duracion"]) in DURACIONES:
            sb["duracion"] = int(payload["duracion"])
        if any(k in payload for k in ("guion", "subtitulos", "carteles", "cierre_on", "logo", "corte", "precio",
                                      "cierre")):
            _final(sid).unlink(missing_ok=True)
        await _guardar(sb)
    return {"storyboard": _vista(sb)}


@router.post(API + "/{sid}/guion")
async def api_guion(sid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    sb = await _sb(sid)
    if any(k in payload for k in ("idea", "duracion", "respuestas")):
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
        if "linea" in payload and _texto(payload["linea"], 800) != c.get("linea", ""):
            c["linea"], c["linea_en"], c["clip"] = _texto(payload["linea"], 800), "", False
        if "cartel" in payload:
            c["cartel"] = _texto(payload["cartel"], 40)
        if "cartel_t" in payload:
            c["cartel_t"] = _num(payload["cartel_t"], 0, 4.5, 1.0)
        if "cartel_gesto" in payload:
            c["cartel_gesto"] = bool(payload["cartel_gesto"])
        if "habla" in payload and bool(payload["habla"]) != bool(c.get("habla")) and c["tipo"] != "producto":
            c["habla"], c["clip"] = bool(payload["habla"]), False
        if "dice" in payload and _texto(payload["dice"], 300) != c.get("dice", ""):
            c["dice"] = _texto(payload["dice"], 300)
            if c.get("habla"):
                c["clip"] = False          # la boca tiene que decir lo nuevo: se vuelve a filmar
        if "final_es" in payload and _texto(payload["final_es"], 300) != c.get("final_es", ""):
            c["final_es"], c["final"], c["img_fin"], c["aprobado"], c["clip"] = \
                _texto(payload["final_es"], 300), "", False, False, False
            await kv.delete(_k_img_fin(sid, cid))
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
    solo_final: List[str] = list(cids) if payload.get("parte") == "final" else []
    if not cids:
        pend = [c["id"] for c in sb["cuadros"] if not c.get("img") or c.get("desactualizada")
                or (_con_final(c) and not c.get("img_fin"))]
        n = int(payload.get("prueba") or 0)
        cids = pend[:n] if n else pend
        solo_final = [c["id"] for c in sb["cuadros"] if c["id"] in cids and c.get("img")
                      and not c.get("desactualizada") and _con_final(c) and not c.get("img_fin")]
    if not cids:
        raise HTTPException(400, "No falta dibujar ningún cuadro (para rehacer uno, tocá ↻ en ese cuadro).")
    for c in sb["cuadros"]:
        if c["id"] in cids:
            c.pop("desactualizada", None)
    if "prompt_json" in payload:
        sb["prompt_json"] = payload["prompt_json"] is not False     # la casilla de Fotos (se recuerda en el navegador)
    await _guardar(sb)
    motor = payload.get("motor") if payload.get("motor") in MOTORES_CUADRO else ""
    return {"job": await _lanzar(sb, "dibujar", 60 + 40 * len(cids), _job_dibujar, cids,
                                 _texto(payload.get("correccion"), 400), motor, solo_final)}


@router.post(API + "/{sid}/cuadro/{cid}/volver")
async def api_volver(sid: str, cid: str) -> Dict[str, Any]:
    """Vuelve a la imagen anterior del cuadro (intercambia: la actual queda como anterior)."""
    async with _lock(sid):
        sb = await _sb(sid)
        c = next((x for x in sb["cuadros"] if x["id"] == cid), None)
        previa = await kv.get(_k_img_prev(sid, cid))
        if not c or not previa:
            raise HTTPException(404, "Ese cuadro no tiene imagen anterior.")
        actual = await kv.get(_k_img(sid, cid))
        await kv.set(_k_img(sid, cid), previa)
        if actual:
            await kv.set(_k_img_prev(sid, cid), actual)
        c["motor_img"], c["motor_previa"] = c.get("motor_previa", ""), c.get("motor_img", "")
        c.update({"aprobado": False, "clip": False, "ts": int(time.time()), "nota_img": ""})
        _clip(sid, cid).unlink(missing_ok=True)
        _final(sid).unlink(missing_ok=True)
        await _guardar(sb)
    return {"storyboard": _vista(sb)}


@router.post(API + "/{sid}/cuadro/{cid}/aprobar")
async def api_aprobar(sid: str, cid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    async with _lock(sid):
        sb = await _sb(sid)
        for c in sb["cuadros"]:
            if c["id"] == cid or (cid == "todos" and c.get("img")):
                if not c.get("img"):
                    raise HTTPException(400, "Ese cuadro todavía no tiene imagen.")
                if _con_final(c) and not c.get("img_fin") and payload.get("ok") is not False:
                    if cid == "todos":
                        continue
                    raise HTTPException(400, "A este momento le falta su cuadro final (con su cara): dibujalo antes.")
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
    if cid.endswith("-final"):
        b = await kv.get(_k_img_fin(sid, cid[:-6]))
        if not b:
            raise HTTPException(404, "Ese momento no tiene cuadro final.")
        return Response(base64.b64decode(b), media_type="image/jpeg")
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
    if sin and not _es_ref(sb):
        raise HTTPException(400, f"Primero aprobá todos los cuadros del storyboard (falta{'n' if len(sin) > 1 else ''}: "
                                 f"{', '.join(sin)}). Nada se filma sin aprobar.")
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal.")
    await _cobrar(v["costo_video"])
    return {"job": await _lanzar(sb, "filmar", 90 + 60 * math.ceil(len(sb["cuadros"]) / PARALELO), _job_filmar)}


@router.post(API + "/{sid}/cuadro/{cid}/filmar")
async def api_filmar_uno(sid: str, cid: str) -> Dict[str, Any]:
    """Filma SÓLO este cuadro (de prueba, o para rehacer su tramo). No monta el reel."""
    sb = await _sb(sid)
    c = next((x for x in sb.get("cuadros") or [] if x["id"] == cid), None)
    if not c:
        raise HTTPException(404, "No encuentro ese cuadro.")
    if not (c.get("img") and c.get("aprobado")) and not _es_ref(sb):
        raise HTTPException(400, "Primero aprobá este cuadro. Nada se filma sin aprobar.")
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal.")
    await _cobrar(round(_precio_tramo(_mv_de(sb), c["seg"]), 2))
    return {"job": await _lanzar(sb, "filmar", 150, _job_filmar, cid)}


@router.post(API + "/{sid}/video_ref")
async def api_video_ref(sid: str, video: UploadFile = File(...)) -> Dict[str, Any]:
    """Sube el video de referencia de movimiento: se pasa a mp4 y se recorta a 30 s (el tope de Kling)."""
    sb = await _sb(sid)
    datos = await video.read()
    if len(datos) > 200_000_000:
        raise HTTPException(413, "El video pesa demasiado (máximo 200 MB).")
    tmp = _dir(sid) / "ref_mov_subido"
    tmp.write_bytes(datos)
    try:
        await asyncio.to_thread(_film._ff, ["-y", "-i", str(tmp), "-t", "30", "-r", "24", "-c:v", "libx264", "-preset",
                                            "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", "-vf",
                                            "scale='min(1080,iw)':-2", "-movflags", "+faststart", str(_video_ref(sid))])
    except Exception as e:
        raise HTTPException(400, f"No pude leer ese video: {str(e)[-160:]}")
    finally:
        tmp.unlink(missing_ok=True)
    async with _lock(sid):
        sb = await _sb(sid)
        sb["video_ref"] = round(_duracion_video(_video_ref(sid)), 1)
        await _guardar(sb)
    return {"storyboard": _vista(sb)}


@router.delete(API + "/{sid}/video_ref")
async def api_video_ref_borrar(sid: str) -> Dict[str, Any]:
    _video_ref(sid).unlink(missing_ok=True)
    async with _lock(sid):
        sb = await _sb(sid)
        sb["video_ref"] = 0
        await _guardar(sb)
    return {"storyboard": _vista(sb)}


@router.get(API + "/{sid}/video_ref.mp4")
async def api_video_ref_ver(sid: str):
    if not _video_ref(sid).exists():
        raise HTTPException(404, "No hay video de referencia.")
    return FileResponse(_video_ref(sid), media_type="video/mp4")


@router.post(API + "/{sid}/terminar")
async def api_terminar(sid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Al crudo de corrido le suma lo que la usuaria eligió: carteles y precio, cartel final, voz en off (con
    subtítulos) y que hable a cámara en los momentos marcados."""
    sb = await _sb(sid)
    if not _crudo(sid).exists():
        raise HTTPException(400, "Primero filmá el video de corrido.")
    opc = {k: payload.get(k, d) is not False and bool(payload.get(k, d))
           for k, d in (("carteles", True), ("cierre", True), ("voz", False), ("subtitulos", True), ("hablar", True))}
    if not any(opc[k] for k in ("carteles", "cierre", "voz", "hablar")):
        raise HTTPException(400, "Elegí al menos una cosa para sumarle (o dejalo crudo).")
    hablan = sum(1 for c in sb["cuadros"] if c.get("habla")) if opc["hablar"] else 0
    await _cobrar(round(sum(c["seg"] * _film.LIPSYNCS["sync"]["precio_seg"] for c in sb["cuadros"]
                            if c.get("habla") and opc["hablar"]), 2))
    return {"job": await _lanzar(sb, "terminar", 60 + 90 * hablan, _job_terminar, opc)}


@router.get(API + "/{sid}/crudo.mp4")
async def api_crudo(sid: str):
    if not _crudo(sid).exists():
        raise HTTPException(404, "Todavía no hay video crudo.")
    return FileResponse(_crudo(sid), media_type="video/mp4", filename=f"storyboard-{sid}-crudo.mp4")


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
  .fin{display:flex;gap:8px;align-items:flex-start;margin-top:6px} .fin img{width:64px;border-radius:8px;flex:none}
  .preg{margin:12px 0} .preg b{display:block;margin-bottom:4px;font-weight:500}
  .chip{padding:5px 11px;font-size:12.5px;margin:0 6px 6px 0} .chip.on{background:var(--rose);color:#17140d;border-color:var(--rose)}
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
  <p class="hint">La base de los videos hiperrealistas: <b>1)</b> el director te pregunta (pelo, maquillaje, precio…) y escribe el reel en <b>momentos de 3 a 5 s</b> con su línea de tiempo · <b>2)</b> Gemini dibuja cada cuadro con
  <b>tu lugar</b>, <b>su cuerpo con la prenda</b> (el probador sin cabeza) y <b>su cara en 4K</b> · <b>3)</b> vos aprobás el storyboard ·
  <b>4)</b> cada cuadro aprobado arranca su momento de video (de corrido, siguiendo su línea de tiempo) y se une con su voz, carteles y precio. Nada se filma sin aprobar.</p>
  <p class="hint">Antes: la modelo con su <b>Cara HD</b> (Personajes → ficha), la prenda con su <b>probador</b> y tu lugar (<a href="/referencias" target="_blank">Referencias</a>).</p>
  <div id="lista" class="hint"></div>
</div>

<div class="card" id="c1">
  <div class="paso">Paso 1</div><h3>Qué reel querés</h3>
  <div class="row"><div><label>Modelo</label><select id="pid"></select></div><div><label>Prenda (Mis prendas)</label><select id="prenda"></select></div></div>
  <div id="colores" class="fila"></div>
  <div class="row"><div><label>Lugar (Mis lugares)</label><select id="lugar"></select></div><div><label>Duración</label><select id="duracion"></select></div></div>
  <div class="row"><div><label>Cámara</label><select id="camara"><option value="cine" selected>🎬 Cine (como un comercial de alta gama)</option><option value="celular">📱 Celular (real, como una creadora)</option></select></div><div></div></div>
  <div class="row"><div><label>Cómo se filma</label><select id="modo"><option value="corrido" selected>🎞 Un solo video de corrido (Kling 4.0 con sus referencias, hasta 30 s)</option><option value="cuadros">🖼 Por cuadros (se dibuja cada momento y se anima)</option></select></div><div></div></div>
  <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="hablar" checked style="width:auto"> 🗣 Que hable a cámara en 1 o 2 momentos (el gancho y/o el cierre, con la boca sincronizada)</label>
  <label>Idea (opcional): qué querés contar o mostrar</label>
  <textarea id="idea" rows="2" placeholder="ej: probador del local, se lo prueba y muestra lo cómodo que es; cerrar con 'escribime por DM'"></textarea>
  <div class="fila"><button class="go" id="crear">🎬 Empezar (el director te pregunta)</button><span class="estado" id="e1"></span></div>
</div>

<div class="card" id="cq" style="display:none">
  <div class="paso">Paso 2</div><h3>🎬 El director te pregunta</h3>
  <p class="hint" id="resumen"></p><div id="preguntas"></div>
  <div class="fila"><button class="go" id="escribir">✍️ Escribir el storyboard con esto</button><span class="hint">Lo que no contestes, lo decide el director.</span></div>
</div>

<div class="card" id="c2" style="display:none">
  <div class="paso">Paso 3</div><h3 id="titulo">El storyboard</h3>
  <p class="hint">Cada cuadro es un <b>momento de 3 a 5 segundos</b>: la foto es cómo arranca y la <b>línea de tiempo</b> dice qué pasa en cada segundo (se filma de corrido, sin pagar segundos de más). Cada cuadro pasa por el <b>inspector de Fotos</b> (prenda y anatomía) y usa la <b>ficha de la prenda</b> (tela, costuras, escote, espalda). Qué se ve, plano, ángulo y segundos: Si cambiás algo, ese cuadro se vuelve a dibujar. Revisá que sea <b>ella</b>, la <b>prenda exacta</b> y <b>tu lugar</b>;
  si no, escribí qué corregir y tocá ↻. Aprobá con ✓.</p>
  <div class="row"><div><label>Motor de los cuadros</label><select id="motorCuadros"></select></div><div></div></div>
  <div class="fila">
    <button class="go" id="prueba">🧪 Dibujar los 3 primeros (prueba)</button>
    <button id="todos">🎨 Dibujar los que faltan</button>
    <button id="aprobarTodos">✓ Aprobar todos los dibujados</button>
    <button id="rehacerGuion">✍️ Reescribir el guion</button>
    <button id="escena3d" title="Armá la escena de todo el reel en 3D, con una cámara por momento, y mirala en movimiento">🧍 Escena 3D y animatic</button>
    <span class="hint" id="costos"></span>
  </div>
  <div class="estado" id="e2"></div>
  <div class="grilla" id="grilla"></div>
  <label>💇 Su look en todo el reel (pelo, maquillaje, uñas, joyas — lo que contestaste)</label>
  <input id="estilo" placeholder="ej: loose soft waves, natural makeup, nude nails, thin gold chain">
  <label>🎙️ Su voz (una sola narración encima de todo; se graba con la voz de su ficha)</label>
  <textarea id="guion" rows="3"></textarea><div class="hint" id="guionInfo"></div>
</div>

<div class="card" id="c3" style="display:none">
  <div class="paso">Paso 4</div><h3>El video</h3>
  <p class="hint">Cada cuadro aprobado es el primer cuadro exacto de su momento: el video se filma de corrido desde esa imagen, siguiendo su línea de tiempo, y dura exactamente sus segundos (no se pagan segundos de más). Siempre sin sonido: la voz va aparte.</p>
  <div class="row"><div><label>Motor del video</label><select id="motorVideo"></select><div id="motorVM" style="display:none;margin-top:6px"></div><div id="motorVMref" style="display:none;margin-top:6px"></div>
    <label id="corridoBox" style="display:none;margin:6px 0 0;align-items:center;gap:6px"><input type="checkbox" id="deCorrido" style="width:auto"> 🎞 <b>De corrido</b>: un solo video con todos los momentos (Kling hace los cortes), en vez de un tramo por momento</label>
    <div id="videoRefBox" style="display:none;margin-top:8px"><label>🎥 Video de referencia de movimiento (opcional)</label>
      <div class="hint">Un reel que te guste o un recorrido grabado en Lumen: Kling copia <b>cómo se mueve la cámara y el ritmo</b>, no a la gente, la ropa ni el lugar. Hasta 30 s. Sólo por Higgsfield.</div>
      <div id="videoRefVer"></div>
      <div class="fila"><label class="sm" style="margin:0;cursor:pointer;color:var(--rose-deep)"><input type="file" accept="video/*" id="videoRefFile" style="display:none">⬆ Subir video</label><button class="sm" id="videoRefQuitar" style="display:none">Quitar</button><span class="hint" id="videoRefEstado"></span></div></div>
    <div id="corridoPrompt" style="display:none;margin-top:6px"><label>🎞 El video entero, segundo a segundo (lo escribió el director; lo podés corregir)</label><textarea id="promptCorrido" rows="6"></textarea>
      <label>🎥 Ficha de fotografía: cámara, lente, exposición, luz, color, textura y movimiento (la armó el director; la podés corregir)</label><textarea id="cineFicha" rows="8" style="font-family:monospace;font-size:12px"></textarea><span class="hint" id="cineEstado"></span></div>
    <div class="hint" id="refInfo" style="display:none">🧩 <b>Sin cuadros:</b> no hace falta dibujar ni aprobar nada. Kling recibe su cara, su cuerpo con la prenda y el lugar, más lo que escribiste en cada momento (qué se ve, plano, ángulo, cámara y línea de tiempo), y crea el video.</div></div>
    <div><label>&nbsp;</label><button class="sm" id="probarHf">🔑 Probar la clave de Higgsfield</button> <span class="hint" id="hfEstado"></span></div></div>
  <details id="hfBox" style="margin-top:8px"><summary class="hint" style="cursor:pointer">🔑 Cargar o cambiar la clave de Higgsfield (open.higgsfield.ai → API keys)</summary>
    <div class="row"><div><label>Key ID</label><input id="hfId" autocomplete="off" placeholder="el ID de la clave (o ID:SECRETO juntos)"></div>
    <div><label>Key Secret</label><input id="hfSecret" type="password" autocomplete="off" placeholder="el secreto"></div></div>
    <div class="fila"><button class="sm go" id="hfGuardar">Guardar y probar</button><span class="hint">Queda guardada en tu cuenta; el secreto no se vuelve a mostrar.</span></div></details>
  <div class="fila"><button class="go" id="filmar">🎥 Filmar el storyboard</button><button id="montar">✂️ Volver a unir (sin filmar)</button>
    <label style="margin:0;display:flex;gap:6px;align-items:center"><input type="checkbox" id="subs" style="width:auto"> Subtítulos</label></div>
  <details style="margin-top:10px" open><summary class="hint" style="cursor:pointer">✨ Motion: textos en pantalla, precio y cartel final</summary>
    <div class="fila">
      <label style="margin:0;display:flex;gap:6px;align-items:center"><input type="checkbox" id="carteles" style="width:auto"> Carteles de cada cuadro (precio, talles, promo)</label>
      <label style="margin:0;display:flex;gap:6px;align-items:center"><input type="checkbox" id="cierreOn" style="width:auto"> Cartel final</label>
      <label style="margin:0;display:flex;gap:6px;align-items:center"><input type="checkbox" id="logo" style="width:auto"> Mi logo <span class="hint" id="logoInfo"></span></label></div>
    <div class="row3"><div><label>Precio</label><input id="precio" placeholder="ej: $24.900"></div>
      <div><label>Cartel final: título</label><input id="cierreTitulo" placeholder="el precio o el nombre"></div>
      <div><label>Cartel final: llamado</label><input id="cierreLinea" placeholder="Escribinos por DM"></div></div>
    <div class="row"><div><label>Golpe en los cortes</label><select id="corte"></select></div>
      <div><label>Cámara del reel</label><select id="camara2"><option value="celular">📱 Celular</option><option value="cine">🎬 Cine (color y grano de película)</option></select></div></div>
    <p class="hint">Los carteles se escriben en cada cuadro (arriba). Cambiar esto no vuelve a filmar: tocá ✂️ Volver a unir.</p>
  </details>
  <div class="estado" id="e3"></div>
  <div id="salida"></div>
</div>
<script>
const API = "%%API%%"; let CFG = {}, SB = null, PRENDAS = [], SIGUIENDO = null;
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(p, o = {}) { const r = await fetch(API + p, Object.assign({headers: {"Content-Type": "application/json"}}, o)); let d = {}; try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status)); return d; }
// la casilla "prompt en JSON" de Fotos (se recuerda en el navegador; prendida por defecto)
const promptJson = () => { try{ return localStorage.getItem("luma_prompt_json") !== "0"; }catch(e){ return true; } };
const post = (p, b) => api(p, {method: "POST", body: JSON.stringify(p.endsWith("/dibujar") ? Object.assign({prompt_json: promptJson()}, b || {}) : (b || {}))});
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
function bloquear(on){ ["#crear", "#escribir", "#prueba", "#todos", "#filmar", "#montar", "#rehacerGuion"].forEach(k => $(k).disabled = on); }

function otroId(c){ const ks = Object.keys(CFG.motores_cuadro); return ks.find(k => k !== (c.motor_img === "seedream" ? "gemini" : c.motor_img)) || ks[0]; }
function otroMotor(c){ return CFG.motores_cuadro[otroId(c)] || ""; }
let PREG_SIG = "";
function pintarPreguntas(){
  const qs = SB.preguntas || [];
  $("#cq").style.display = qs.length ? "" : "none";
  const sig = SB.id + JSON.stringify(qs.map(q => q.pregunta));
  if(sig === PREG_SIG) return; PREG_SIG = sig;
  $("#resumen").textContent = SB.resumen || "";
  $("#preguntas").innerHTML = qs.map((q, i) => `<div class="preg"><b>${esc(q.pregunta)}</b><div>${(q.opciones || []).map(o => `<button class="chip ${o === q.respuesta ? "on" : ""}" data-q="${i}" data-o="${esc(o)}">${esc(o)}</button>`).join("")}</div><input class="resp" data-q="${i}" value="${esc(q.respuesta || "")}" placeholder="Tu respuesta (o tocá una opción)"></div>`).join("");
  document.querySelectorAll("#preguntas .chip").forEach(b => b.onclick = () => { const i = b.dataset.q;
    document.querySelector(`#preguntas .resp[data-q="${i}"]`).value = b.dataset.o;
    document.querySelectorAll(`#preguntas .chip[data-q="${i}"]`).forEach(x => x.classList.toggle("on", x === b)); });
}
function pintar(){
  if(!SB) return; pintarPreguntas();
  $("#c2").style.display = (SB.cuadros || []).length || !(SB.preguntas || []).length ? "" : "none"; $("#c3").style.display = (SB.cuadros || []).length ? "" : "none";
  const enfoque = document.activeElement;
  if(enfoque !== $("#estilo")) $("#estilo").value = SB.estilo_en || "";
  $("#carteles").checked = SB.carteles !== false; $("#cierreOn").checked = SB.cierre_on !== false; $("#logo").checked = !!SB.logo;
  $("#logoInfo").textContent = SB.logo_cargado ? "" : "(subilo en Filmado → Motion)";
  if(enfoque !== $("#precio")) $("#precio").value = SB.precio || "";
  if(enfoque !== $("#cierreTitulo")) $("#cierreTitulo").value = (SB.cierre || {}).titulo || "";
  if(enfoque !== $("#cierreLinea")) $("#cierreLinea").value = (SB.cierre || {}).linea || "";
  $("#cierreTitulo").placeholder = (SB.cierre_final || {}).titulo || "el precio o el nombre";
  if(!$("#corte").options.length) $("#corte").innerHTML = opts(CFG.cortes || {ninguno: "Ninguno"}, "ninguno");
  $("#corte").value = SB.corte || "ninguno";
  $("#camara2").value = SB.camara || "celular";
  $("#titulo").textContent = "El storyboard · " + (SB.titulo || "") + ` · ${SB.cuadros.length} cuadros · ${SB.seg_total} s`;
  if(document.activeElement !== $("#guion")) $("#guion").value = SB.guion || "";
  const habla = SB.cuadros.some(c => c.habla);
  $("#guion").disabled = habla;
  if(habla){ $("#guionInfo").textContent = "Hay momentos que hablan a cámara: la voz va por momento (lo que dice cada cuadro, arriba). Este texto es sólo de referencia."; }
  const pal = (SB.guion || "").trim().split(/\s+/).filter(Boolean).length;
  if(!habla) $("#guionInfo").textContent = `${pal} palabras ≈ ${Math.round(pal / 2.6)} s de voz · el storyboard dura ${SB.seg_total} s` + (pal / 2.6 > SB.seg_total + 1 ? " · ⚠ la voz es más larga: acortala o sumá cuadros" : "");
  $("#motorCuadros").value = SB.motor_cuadros || "gemini";
  $("#costos").textContent = (SB.faltan_img ? `faltan ${SB.faltan_img} cuadros (~US$${SB.costo_cuadros.toFixed(2)})` : "storyboard dibujado")
    + (SB.sin_aprobar ? ` · ${SB.sin_aprobar} sin aprobar` : "")
    + (SB.sin_cuadros ? " · 🧩 con «Kling con referencias» (Paso 4) no hace falta dibujar: escribí cada momento y creá el video" : "");
  $("#subs").checked = SB.subtitulos !== false; $("#motorVideo").value = SB.motor_video || CFG.motor_video_default || "";
  const listos = SB.cuadros.length && (SB.sin_cuadros || SB.cuadros.every(c => c.img && c.aprobado));
  $("#filmar").textContent = listos ? `🎥 ${SB.de_corrido ? `Crear los ${SB.seg_total} s de corrido` : SB.sin_cuadros ? "Crear el video con referencias" : "Filmar el storyboard"} (~US$${SB.costo_video})` : "🎥 Filmar (primero aprobá todos los cuadros)";
  $("#deCorrido").checked = !SB.de_corrido_off;
  $("#corridoPrompt").style.display = SB.de_corrido ? "" : "none";
  $("#videoRefBox").style.display = SB.sin_cuadros ? "" : "none";
  $("#videoRefQuitar").style.display = SB.video_ref_url ? "" : "none";
  if(($("#videoRefVer").dataset.u || "") !== (SB.video_ref_url || "")){ $("#videoRefVer").dataset.u = SB.video_ref_url || "";
    $("#videoRefVer").innerHTML = SB.video_ref_url ? `<video src="${SB.video_ref_url}" controls playsinline muted style="max-height:220px;margin-top:6px"></video>` : ""; }
  if(document.activeElement !== $("#promptCorrido")) $("#promptCorrido").value = SB.prompt_corrido_en || "";
  if(document.activeElement !== $("#cineFicha")) $("#cineFicha").value = SB.cine_ficha || "";
  $("#filmar").disabled = !listos || !!SIGUIENDO; $("#montar").style.display = SB.cuadros.every(c => c.clip_url) ? "" : "none";
  $("#salida").innerHTML = (SB.video_url ? `<video src="${SB.video_url}" controls playsinline></video><div class="fila"><a href="${SB.video_url}" download>⬇️ Bajar</a>${SB.drive ? ` · <a href="${SB.drive}" target="_blank">En tu Drive</a>` : ""}${SB.crudo_url && !SB.crudo ? ` · <a href="${SB.crudo_url}" download>⬇️ El crudo</a>` : ""}</div>` : "")
    + (SB.crudo ? `<div class="card" style="margin-top:10px;padding:12px"><b>✅ Este es el video crudo, tal cual salió de Kling.</b>
      <p class="hint">¿Le sumo algo? Si querés que <b>hable a cámara</b>, marcá arriba en qué momento ("🗣 Habla a cámara: Sí") y escribí lo que dice. El motion son los carteles, el precio, el cartel final y su voz en off.</p>
      <div class="fila" style="flex-wrap:wrap;gap:10px">
        <label style="margin:0;display:flex;gap:6px;align-items:center"><input type="checkbox" id="tCarteles" checked style="width:auto"> 🏷 Carteles y precio${SB.precio ? ` (${esc(SB.precio)})` : ""}</label>
        <label style="margin:0;display:flex;gap:6px;align-items:center"><input type="checkbox" id="tCierre" checked style="width:auto"> 🎬 Cartel final</label>
        <label style="margin:0;display:flex;gap:6px;align-items:center"><input type="checkbox" id="tVoz" style="width:auto"> 🎙️ Su voz en off (con subtítulos)</label>
        ${SB.cuadros.some(c => c.habla) ? `<label style="margin:0;display:flex;gap:6px;align-items:center"><input type="checkbox" id="tHablar" checked style="width:auto"> 🗣 Que hable a cámara (${SB.cuadros.filter(c => c.habla).length} momento/s)</label>` : ""}</div>
      <div class="fila"><button class="sm go" id="terminar">✨ Sumarle esto</button><span class="hint">El video no se corta ni cambia de velocidad. O dejalo así: ya está en tu Drive.</span></div></div>` : "")
    + (!SB.crudo && SB.crudo_url ? `<div class="fila"><button class="sm" id="reTerminar">✨ Cambiar lo que le sumé</button></div>` : "");
  const terminar = async () => { try{ const d = await post(`/${SB.id}/terminar`, {carteles: $("#tCarteles") ? $("#tCarteles").checked : true, cierre: $("#tCierre") ? $("#tCierre").checked : true, voz: $("#tVoz") ? $("#tVoz").checked : false, hablar: $("#tHablar") ? $("#tHablar").checked : false, subtitulos: true}); seguir(d.job, $("#e3")); }catch(e){ $("#e3").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
  if($("#terminar")) $("#terminar").onclick = terminar;
  if($("#reTerminar")) $("#reTerminar").onclick = async () => { SB.crudo = true; pintar(); };
  const colores = Object.fromEntries((SB.colores_nombres || []).map((n, i) => [String(i), n]));
  $("#grilla").innerHTML = SB.cuadros.map((c, i) => `<div class="cu ${c.aprobado ? "ok" : ""}" data-c="${c.id}">
    <div class="img" style="${c.url ? `background-image:url('${c.url}')` : ""}"><span class="num">${i + 1}</span><span class="seg">${c.seg} s</span>${c.url ? "" : (c.error ? `<span class="mal">${esc(c.error)}</span>` : "Sin dibujar")}</div>
    ${c.desactualizada ? '<div class="mal hint">Cambiaste el cuadro: volvé a dibujarlo (↻).</div>' : ""}
    ${c.img ? `<div class="hint">${esc((CFG.motores_cuadro[c.motor_img] || c.motor_img || "").split(" · ~")[0] || "")}</div>` : ""}
    ${c.nota_img ? `<div class="hint">${esc(c.nota_img)}</div>` : ""}
    ${c.guia_url ? `<div class="fin"><img src="${c.guia_url}" alt="pose 3D"><div class="hint">🧍 <b>Pose 3D</b>: el cuadro se dibuja con esta pose y esta cámara.${c.desactualizada ? " Tocá ↻ para dibujarlo así." : ""} <button class="sm" data-a="sinpose">Quitar</button></div></div>` : ""}
    ${c.con_final ? `<div class="fin">${c.url_fin ? `<img src="${c.url_fin}" alt="cuadro final">` : `<div class="hint">Falta el cuadro final</div>`}<div class="hint">🎯 <b>Cuadro final</b>: cómo termina (se da vuelta y se le ve la cara). El video va del primero a este.${c.qc_fin ? ` <span class="${c.qc_fin.ok ? "bien" : "mal"}">🔍 ${c.qc_fin.puntaje}/10${!c.qc_fin.ok && (c.qc_fin.diferencias || []).length ? " · " + esc(c.qc_fin.diferencias.join(" · ")) : ""}</span>` : ""}</div></div>` : ""}
    ${c.por_fal ? '<div class="hint">🎥 Este momento se filma por fal: arranca sin su cara y Higgsfield no recibe su cara de referencia.</div>' : ""}
    ${c.qc ? `<div class="hint ${c.qc.ok ? "bien" : "mal"}">🔍 Inspector: ${c.qc.puntaje}/10${c.qc.corregido ? " (lo corrigió una vez)" : ""}${!c.qc.ok && (c.qc.diferencias || []).length ? " · " + esc(c.qc.diferencias.join(" · ")) : ""}</div>` : ""}
    ${c.nota_video ? `<div class="hint">${esc(c.nota_video)}</div>` : ""}
    ${c.clip_url ? `<video src="${c.clip_url}" controls playsinline preload="none" style="margin-top:6px"></video>` : ""}
    <label>Qué se ve</label><textarea data-k="que_se_ve" rows="2">${esc(c.que_se_ve)}</textarea>
    <div class="mini"><div><label>Plano</label><select data-k="plano">${opts(CFG.planos, c.plano)}</select></div><div><label>Ángulo</label><select data-k="angulo">${opts(CFG.angulos, c.angulo)}</select></div></div>
    <div class="mini"><div><label>Cámara</label><select data-k="movimiento">${opts(CFG.movimientos, c.movimiento)}</select></div><div><label>Segundos</label><select data-k="seg">${opts({"3": "3 s", "4": "4 s", "5": "5 s"}, String(c.seg))}</select></div></div>
    <div class="mini"><div><label>Quién</label><select data-k="tipo">${opts(CFG.tipos, c.tipo)}</select></div>${Object.keys(colores).length > 1 ? `<div><label>Color</label><select data-k="variante">${opts(colores, String(c.variante))}</select></div>` : "<div></div>"}</div>
    <label>⏱ Línea de tiempo (qué pasa en cada segundo)</label><textarea data-k="linea" rows="3" placeholder="0-2 s: gira la cadera&#10;2-4 s: mira por sobre el hombro">${esc(c.linea || c.accion || "")}</textarea>
    ${c.tipo !== "producto" ? `<div class="mini"><div><label>🗣 Habla a cámara</label><select data-k="habla">${opts({"": "No (voz en off)", "1": "Sí (boca sincronizada)"}, c.habla ? "1" : "")}</select></div><div></div></div>` : ""}
    <label>${c.habla ? "🗣 Lo que dice a cámara" : "🎙️ Su voz en este momento"}</label><textarea data-k="dice" rows="2" placeholder="${c.habla ? "ej: Chicas, miren lo que llegó…" : "lo que dice su voz en off acá"}">${esc(c.dice || "")}</textarea>
    ${(c.dice || "").trim().split(/\s+/).filter(Boolean).length > c.seg * 2.5 ? `<div class="hint mal">Es mucho texto para ${c.seg} s: acortalo o subí los segundos.</div>` : ""}
    ${c.tipo !== "producto" ? `<label>🎯 Termina con (cuadro final, si se da vuelta)</label><input data-k="final_es" value="${esc(c.final_es || "")}" placeholder="vacío = sin cuadro final · ej: se da vuelta y mira a cámara">` : ""}
    <label>✨ Cartel en pantalla (opcional)</label><input data-k="cartel" maxlength="40" value="${esc(c.cartel || "")}" placeholder="ej: $24.900 · talles S al XL">
    ${c.cartel ? `<div class="mini"><div><label>Aparece en el segundo</label><input data-k="cartel_t" type="number" min="0" max="4.5" step="0.5" value="${c.cartel_t ?? 1}"></div>
      <div><label>🖐 Lo presenta con la mano</label><select data-k="cartel_gesto">${opts({"": "No (entra de costado)", "1": "Sí (sobre su palma)"}, c.cartel_gesto ? "1" : "")}</select></div></div>
      ${c.cartel_gesto ? `<div class="hint">${c.cartel_pos && c.cartel_pos.x >= 0 ? "📍 Ubicado sobre su mano en el video." : (c.cartel_pos ? "No se le ve la mano levantada en el video: va de costado. Sumá a la línea de tiempo que levanta la mano con la palma para arriba." : "Al unir, se busca su mano en el video y el precio aparece ahí. La línea de tiempo tiene que decir que levanta la mano.")}</div>` : ""}` : ""}
    <label>Qué corregir al rehacer</label><input data-cor placeholder="ej: la cara es más fina, el corpiño es de encaje">
    <div class="fila" style="margin-top:6px">${c.img ? (c.aprobado ? '<span class="bien">✓ Aprobado</span> <button class="sm" data-a="noap">Quitar</button>' : '<button class="sm go" data-a="ap">✓ Aprobar</button>') : ""}
      <button class="sm" data-a="pose3d" title="Armar la pose y la cámara con un maniquí en 3D">🧍 Pose 3D</button>
      <button class="sm" data-a="lumen" title="Usar una toma de Lumen como pose y cámara de este cuadro">🧊 Lumen</button>
      <button class="sm" data-a="re">${c.img ? "↻ Rehacer" : "🎨 Dibujar"}</button>
      ${c.con_final && c.img ? `<button class="sm" data-a="refin" title="Rehacer sólo el cuadro final">↻ Final</button>` : ""}
      ${c.img && Object.keys(CFG.motores_cuadro).length > 1 ? `<button class="sm" data-a="otro" title="Rehacer este cuadro con el otro motor, para comparar">⇄ Con ${esc(otroMotor(c).split(" · ")[0].split(" (")[0])}</button>` : ""}
      ${c.aprobado || SB.sin_cuadros ? `<button class="sm" data-a="filmar1" title="Filma sólo este cuadro con el motor de video elegido abajo">🎬 ${c.clip_url ? "Refilmar" : "Filmar"} solo este (~US$${(c.precio_filmar || 0).toFixed(2)})</button>` : ""}
      ${c.hay_previa ? `<button class="sm" data-a="volver" title="Volver a la imagen anterior">↶ Anterior</button>` : ""}
      <label class="sm" style="margin:0;cursor:pointer;color:var(--rose-deep)"><input type="file" accept="image/*" data-sub style="display:none">⬆ Mía</label>
      <button class="sm" data-a="mas" title="Agregar un cuadro después">＋</button><button class="sm" data-a="del" title="Borrar">🗑</button></div></div>`).join("");
  document.querySelectorAll(".cu").forEach(el => { const cid = el.dataset.c;
    el.querySelectorAll("[data-k]").forEach(x => x.onchange = async () => { const b = {}; b[x.dataset.k] = x.value;
      try{ SB = (await put(`/${SB.id}/cuadro/${cid}`, b)).storyboard; pintar(); }catch(e){ $("#e2").textContent = e.message; } });
    el.querySelectorAll("[data-a]").forEach(b => b.onclick = async () => { try{ const a = b.dataset.a;
      if(a === "ap" || a === "noap"){ SB = (await post(`/${SB.id}/cuadro/${cid}/aprobar`, {ok: a === "ap"})).storyboard; pintar(); }
      if(a === "re"){ const d = await post(`/${SB.id}/dibujar`, {cids: [cid], correccion: el.querySelector("[data-cor]").value}); seguir(d.job, $("#e2")); }
      if(a === "otro"){ const cu = SB.cuadros.find(x => x.id === cid); const d = await post(`/${SB.id}/dibujar`, {cids: [cid], motor: otroId(cu), correccion: el.querySelector("[data-cor]").value}); seguir(d.job, $("#e2")); }
      if(a === "filmar1"){ const cu = SB.cuadros.find(x => x.id === cid); if(!confirm(`¿Filmar sólo este momento (${cu.seg} s)? Cuesta ~US$${(cu.precio_filmar || 0).toFixed(2)}.`)) return; const d = await post(`/${SB.id}/cuadro/${cid}/filmar`); seguir(d.job, $("#e2")); }
      if(a === "pose3d"){ window.open(`%%PREFIJO%%/3d?sid=${SB.id}&cid=${cid}`, "_blank"); }
      if(a === "lumen"){ if(!window.Lumen) throw new Error("No cargó Lumen: recargá la página.");
        const t = await Lumen.elegir({titulo: "La toma de Lumen para este cuadro (pose y cámara)"}); if(!t) return;
        await post(`/${SB.id}/cuadro/${cid}/escena`, {imagen: await Lumen.dataURL(t.url), descripcion: t.camara || t.nombre}); await abrir(SB.id); }
      if(a === "sinpose"){ SB = (await api(`/${SB.id}/cuadro/${cid}/escena`, {method: "DELETE"})).storyboard; pintar(); }
      if(a === "refin"){ const d = await post(`/${SB.id}/dibujar`, {cids: [cid], parte: "final", correccion: el.querySelector("[data-cor]").value}); seguir(d.job, $("#e2")); }
      if(a === "volver"){ SB = (await post(`/${SB.id}/cuadro/${cid}/volver`)).storyboard; pintar(); }
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
    const d = await post("/nuevo", {pid: $("#pid").value, prenda: $("#prenda").value, colores, lugar_ref: $("#lugar").value, duracion: +$("#duracion").value, idea: $("#idea").value, hablar: $("#hablar").checked, camara: $("#camara").value, modo: $("#modo").value});
    SB = d.storyboard; try{ localStorage.setItem("sb_id", SB.id); }catch(e){} pintar(); seguir(d.job, $("#e1")); }
  catch(e){ $("#e1").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#prueba").onclick = async () => { try{ const d = await post(`/${SB.id}/dibujar`, {prueba: 3}); seguir(d.job, $("#e2")); }catch(e){ $("#e2").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#todos").onclick = async () => { try{ const d = await post(`/${SB.id}/dibujar`, {}); seguir(d.job, $("#e2")); }catch(e){ $("#e2").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#aprobarTodos").onclick = async () => { try{ SB = (await post(`/${SB.id}/cuadro/todos/aprobar`, {ok: true})).storyboard; pintar(); }catch(e){ $("#e2").textContent = e.message; } };
window.addEventListener("message", async e => { if(e.data && e.data.de === "estudio3d" && SB){ try{ SB = (await api("/" + SB.id)).storyboard; pintar(); }catch(er){} } });
$("#escena3d").onclick = () => window.open(`%%PREFIJO%%/3d?sid=${SB.id}`, "_blank");
$("#escribir").onclick = async () => { try{
    const respuestas = Array.from(document.querySelectorAll("#preguntas .resp")).map(x => x.value);
    const d = await post(`/${SB.id}/guion`, {respuestas}); $("#c2").style.display = ""; seguir(d.job, $("#e2")); $("#c2").scrollIntoView({behavior: "smooth"}); }
  catch(e){ $("#e2").textContent = e.message; } };
const guardar = b => put("/" + SB.id, b).then(d => { SB = d.storyboard; pintar(); }).catch(e => { $("#e3").textContent = e.message; });
$("#estilo").onchange = () => guardar({estilo_en: $("#estilo").value});
$("#carteles").onchange = () => guardar({carteles: $("#carteles").checked});
$("#cierreOn").onchange = () => guardar({cierre_on: $("#cierreOn").checked});
$("#logo").onchange = () => guardar({logo: $("#logo").checked});
$("#precio").onchange = () => guardar({precio: $("#precio").value});
$("#cierreTitulo").onchange = $("#cierreLinea").onchange = () => guardar({cierre: {titulo: $("#cierreTitulo").value, linea: $("#cierreLinea").value}});
$("#corte").onchange = () => guardar({corte: $("#corte").value});
$("#camara2").onchange = () => { guardar({camara: $("#camara2").value}); $("#e3").textContent = "Cambió la cámara: el color de cine se aplica al unir (✂️ Volver a unir). Para que los cuadros y el video también cambien, rehacé los cuadros y volvé a filmar."; };
$("#rehacerGuion").onclick = async () => { if(!confirm("¿Reescribir el guion? Se reemplazan los cuadros (y sus dibujos).")) return;
  try{ const d = await post(`/${SB.id}/guion`, {}); seguir(d.job, $("#e2")); }catch(e){ $("#e2").textContent = e.message; } };
$("#guion").onchange = async () => { try{ SB = (await put("/" + SB.id, {guion: $("#guion").value})).storyboard; pintar(); }catch(e){ $("#e2").textContent = e.message; } };
$("#subs").onchange = async () => { try{ SB = (await put("/" + SB.id, {subtitulos: $("#subs").checked})).storyboard; pintar(); }catch(e){} };
$("#filmar").onclick = async () => { try{ const d = await post(`/${SB.id}/filmar`); seguir(d.job, $("#e3")); }catch(e){ $("#e3").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#montar").onclick = async () => { try{ const d = await post(`/${SB.id}/montar`); seguir(d.job, $("#e3")); }catch(e){ $("#e3").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#prenda").onchange = pintarColores;
$("#motorCuadros").onchange = async () => { try{ SB = (await put("/" + SB.id, {motor_cuadros: $("#motorCuadros").value})).storyboard; pintar(); }catch(e){ $("#e2").textContent = e.message; } };
$("#motorVideo").onchange = async () => { try{
    const body = {motor_video: $("#motorVideo").value};
    if(body.motor_video === "vm" && window.MotorVideo){ await armarVM(); body.motor_vm = MotorVideo.valor($("#motorVM")); }
    if(body.motor_video === "vm_ref" && window.MotorVideo){ await armarVMref(); body.motor_vm_ref = MotorVideo.valor($("#motorVMref")); }
    SB = (await put("/" + SB.id, body)).storyboard; pintar(); }catch(e){ $("#e3").textContent = e.message; } };
// EL SELECTOR DEL MOTOR COMÚN (modelo · proveedor · calidad): se guarda en el storyboard al cambiar
let VM_ARMADO = false;
async function armarVM(){ if(VM_ARMADO || !window.MotorVideo) return; VM_ARMADO = true;
  await MotorVideo.armar($("#motorVM"), async v => { if(!SB || $("#motorVideo").value !== "vm") return;
    try{ SB = (await put("/" + SB.id, {motor_video: "vm", motor_vm: v})).storyboard; pintar(); }catch(e){ $("#e3").textContent = e.message; } }); }
setInterval(() => { const box = $("#motorVM"); if(!box) return; const on = $("#motorVideo").value === "vm";
  box.style.display = on ? "" : "none"; if(on) armarVM();
  const onR = $("#motorVideo").value === "vm_ref"; $("#motorVMref").style.display = onR ? "" : "none";
  $("#refInfo").style.display = onR ? "" : "none"; $("#corridoBox").style.display = onR ? "flex" : "none"; if(onR) armarVMref(); }, 800);
$("#videoRefFile").onchange = async () => { const f = $("#videoRefFile").files[0]; if(!f) return;
  $("#videoRefEstado").textContent = "Subiendo…";
  try{ const fd = new FormData(); fd.append("video", f);
    const r = await fetch(`${API}/${SB.id}/video_ref`, {method: "POST", body: fd}); const j = await r.json();
    if(!r.ok) throw new Error(j.detail || r.status); SB = j.storyboard; $("#videoRefEstado").textContent = "✓ Listo"; pintar(); }
  catch(e){ $("#videoRefEstado").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } $("#videoRefFile").value = ""; };
$("#videoRefQuitar").onclick = async () => { try{ const r = await fetch(`${API}/${SB.id}/video_ref`, {method: "DELETE"}); SB = (await r.json()).storyboard; pintar(); }catch(e){} };
$("#cineFicha").onchange = async () => { try{ SB = (await put("/" + SB.id, {cine_en: $("#cineFicha").value})).storyboard; $("#cineEstado").innerHTML = '<span class="bien">✓ Guardada</span>'; pintar(); }catch(e){ $("#cineEstado").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#promptCorrido").onchange = async () => { try{ SB = (await put("/" + SB.id, {prompt_corrido_en: $("#promptCorrido").value})).storyboard; pintar(); }catch(e){ $("#e3").textContent = e.message; } };
$("#deCorrido").onchange = async () => { try{ SB = (await put("/" + SB.id, {de_corrido: $("#deCorrido").checked})).storyboard; pintar(); }catch(e){ $("#e3").textContent = e.message; } };
// EL MISMO SELECTOR, sólo con los modelos que reciben referencias (modo sin cuadros)
let VMR_ARMADO = false;
async function armarVMref(){ if(VMR_ARMADO || !window.MotorVideo) return; VMR_ARMADO = true;
  await MotorVideo.armar($("#motorVMref"), async v => { if(!SB || $("#motorVideo").value !== "vm_ref") return;
    try{ SB = (await put("/" + SB.id, {motor_video: "vm_ref", motor_vm_ref: v})).storyboard; pintar(); }catch(e){ $("#e3").textContent = e.message; } }, {tipo: "ref"}); }
$("#hfGuardar").onclick = async () => { $("#hfEstado").textContent = "Guardando y probando…";
  try{ const d = await post("/higgsfield", {key_id: $("#hfId").value, key_secret: $("#hfSecret").value}); $("#hfSecret").value = "";
    $("#hfEstado").innerHTML = d.ok ? `<span class="bien">✓ ${esc(d.mensaje)}</span>` : `<span class="mal">${esc(d.mensaje)}</span>`;
    if(d.ok){ CFG = await api("/config"); $("#motorVideo").innerHTML = Object.entries(CFG.motores_video).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
      $("#motorCuadros").innerHTML = Object.entries(CFG.motores_cuadro).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join(""); if(SB) pintar(); } }
  catch(e){ $("#hfEstado").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#probarHf").onclick = async () => { $("#hfEstado").textContent = "Probando…"; try{ const d = await api("/higgsfield"); $("#hfEstado").innerHTML = d.ok ? `<span class="bien">✓ ${esc(d.mensaje)}</span>` : `<span class="mal">${esc(d.mensaje)}</span>`; }catch(e){ $("#hfEstado").textContent = e.message; } };
(async () => {
  CFG = await api("/config");
  $("#motorCuadros").innerHTML = Object.entries(CFG.motores_cuadro).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#motorVideo").innerHTML = Object.entries(CFG.motores_video).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
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
</script><script src="/lumen/elegir.js"></script><script src="/clips/api/motor.js"></script></main></body></html>"""
