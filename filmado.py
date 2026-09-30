"""
FILMADO DE CERO — un REEL COMPLETO de ella filmado por el motor desde cero (no una foto
que cobra vida), con SU voz, armado toma por toma.

Cómo se arma:
  1. Contás qué querés (la prenda, qué mostrar, cuánto dura, datos del producto) y
     CLAUDE TE PREGUNTA lo que le falta para dirigir bien (el gancho, qué destacar, hasta
     dónde mostrar, el cierre…).
  2. Con tus respuestas Claude arma EL PLAN: varias tomas (que hable a cámara, que gire y
     se vea la espalda, la bombacha, el encaje de cerca, el espejo…), cada una con lo que
     dice y lo que hace. Todo se puede editar: lo que escribís vos va tal cual.
  3. Se filma cada toma: primero la VOZ de Reels (y la toma dura lo que dura lo que dice),
     Kling filma sin voz con ella como @Element1 y la prenda como @Element2, y en las
     tomas donde habla a cámara el LIP-SYNC le pone la boca en sincro con su voz. En las
     tomas que muestran (gira, espalda, detalle) su voz va encima, como en los reels.
  4. Se unen las tomas, se pasa el filtro de Reels y Claude revisa cada toma contra la
     prenda (sólo informa). Una toma que no gustó se rehace sola, sin volver a pagar las
     demás.

v2.1 (después de la prueba del reel):
  - Modo "voz de fondo" (por defecto): ella no habla a cámara, su voz va encima. Sin lip-sync:
    más barato y lo que mejor salió. El modo "habla" sigue para quien lo quiera.
  - Con lip-sync, Kling la filma con la BOCA QUIETA: si el motor también la movía, las dos
    bocas se mezclaban. Kling LipSync afuera (anduvo mal); queda Sync.
  - CONTINUIDAD: la primera toma se filma sola y un cuadro suyo va de referencia (@Image1) a
    todas las demás, más una descripción fija del cuarto, la luz y el peinado: antes cada toma
    inventaba otro fondo y otro pelo.
  - El DIRECTOR entiende qué vendemos (qué es, puntos fuertes, para quién), arma un concepto y
    cada toma lleva plano (detalle, primer plano, medio, entero, espejo…), movimiento de cámara
    y ritmo de reel (2–4 s); puede haber tomas de la prenda sola.

v2.2 (mirando otra vez el video de referencia):
  - TOMA LARGA SIN CORTES: una toma puede "seguir sin cortar" a la anterior: Kling arranca en su
    último cuadro (start_image_url) y se encadenan 20–30 s aunque filme 15 s como mucho.
  - JUEGO CON LA CÁMARA Y TRANSICIONES: cada toma dice cómo entra (corte, tapa la cámara con la
    mano, pasa la prenda por la lente, barrido, sigue sin cortar). El final de una y el
    principio de la otra se piden juntos, y en el montaje van con un fundido cortito a negro.
  - VARIOS COLORES de la prenda (hasta 5): cada toma dice cuál tiene puesto; los cambios de color
    van en las transiciones (la tendencia de tapar la cámara y aparecer con otro color).
  - El director conoce todo eso y puede proponer el movimiento de espaldas (se desprende el
    corpiño, lo deja caer, se da vuelta tapándose con el brazo y tapa la cámara): siempre
    sugerido, nunca explícito.

La voz ya no se corta: la voz se completa con silencio hasta el largo del video, y si el
motor devolviera un video más corto que la voz, el lip-sync lo alarga ("bounce") en vez de
cortarle la voz (la v1.2 usaba "cut_off", que corta lo que sobra).
"""

from __future__ import annotations

import asyncio
import base64
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
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

import claude_director as _claude
from imagenes_ia import (
    CURRENT_SUB,
    _al_ingles,
    _compress_ref,
    _pfx,
    _strip_data_url,
    budget_record,
    kv,
    recorte_cara_avatar,
    set_current_sub,
)
from personajes import (
    COSTO_TTS,
    PJ_DIR,
    VOCES,
    _bind,
    _cobrar,
    _doc,
    _fal_enviar,
    _fal_esperar_y_bajar,
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
    _tts_mp3,
)
from reels import (
    ALTO,
    ANCHO,
    LOOKS,
    _AF_VOZ,
    _FILTRO_LOOK,
    _MANO_CROP,
    _MANO_ESCALA,
    ENERGIAS_VOZ,
    TONOS,
    _cuerpo_en,
    _instruccion_voz,
    _ref_cuerpo,
    _tratar_voz,
    _voz_reel,
)
from videos_luma import _duracion_video, _ffmpeg_bin, _spawn

ROUTE_PREFIX = os.environ.get("FILMADO_PREFIX", "/filmado").rstrip("/")
API = ROUTE_PREFIX + "/api"
VERSION = "2.2.1"   # subí este número cada vez que cambiamos el archivo

SEG_MIN, SEG_MAX = 3, 15            # lo que acepta Kling por clip
SEG_MUESTRA = 4                     # una toma que sólo muestra, sin voz
SEG_CORTE_MIN = 2                   # una toma sin voz se puede cortar a 2 s (Kling filma 3 como mínimo)
DURACIONES = (15, 20, 30)           # el reel entero
MAX_TOMAS = 10
MAX_VARIANTES = 5                   # colores de la prenda en un mismo reel
FUNDIDO = 0.12                      # el negro entre "tapa la cámara" y la toma que sigue
MAX_PROMPT = int(os.getenv("FILMADO_MAX_PROMPT", "2450"))   # Kling acepta 2500 caracteres
PARALELO = 3                        # tomas filmándose a la vez en fal
LUGARES = {
    "dormitorio": ("Su dormitorio",
                   "her own bedroom at home: an unmade bed with rumpled sheets, a bedside lamp switched "
                   "on with warm light, a window with soft daylight and curtains, a dresser with makeup, "
                   "perfume and a full-length standing mirror; an ordinary lived-in room, not a set"),
    "probador": ("El probador de un local",
                 "the fitting room of a small lingerie shop: a curtain, a full-length mirror, a hook with "
                 "hangers, warm shop light"),
}
MOTORES = {
    "kling_pro": {"label": "Kling 3.0 Omni · Pro", "tipo": "kling",
                  "modelo": os.getenv("FAL_KLING_PRO_MODEL", "fal-ai/kling-video/o3/pro/reference-to-video"),
                  "precio_seg": float(os.getenv("COMERCIALES_PRECIO_PRO", "0.112"))},
    "seedance2": {"label": "Seedance 2.0", "tipo": "seedance",
                  "modelo": os.getenv("FAL_SEEDANCE_REF_MODEL", "bytedance/seedance-2.0/reference-to-video"),
                  "precio_seg": float(os.getenv("PERSONAJES_PRECIO_SEEDANCE_REF", "0.30"))},
}
MOTOR_DEFAULT = "kling_pro"
LIPSYNCS = {
    "sync": {"label": "Sync 2 Pro", "modelo": os.getenv("FILMADO_LIPSYNC_MODEL", "fal-ai/sync-lipsync/v2/pro"),
             "precio_seg": float(os.getenv("FILMADO_PRECIO_LIPSYNC", "0.083"))},
}
LIPSYNC_DEFAULT = "sync"
COSTO_REVISION = 0.05               # Claude mirando una toma
COSTO_PLAN = 0.10                   # Claude preguntando y armando el plan
LOOK_DEFAULT = "nitido"             # grano y color de celular sin ablandar: el encaje se sigue viendo
CAMARAS = {"motor": "La que filma el motor", "mano": "Más en mano (se mueve sola, como en Reels)"}
ENERGIA_FILMADO = "natural"         # con sus pausas: la primera prueba "iba tan rápido que parecía IA"
# Cómo cuenta el reel. "Voz de fondo": ella nunca habla a cámara, su voz va encima (sin lip-sync,
# más barato y lo que mejor salió). "Habla": tomas a cámara con lip-sync.
MODOS = {"fondo": "Sin que hable: su voz de fondo (más barato, recomendado)",
         "habla": "Que hable a cámara en algunas tomas (con lip-sync)"}
MODO_DEFAULT = "fondo"
TIPOS = {"habla": "Habla a cámara (lip-sync)", "muestra": "Ella muestra (voz de fondo)",
         "producto": "Sólo la prenda (sin ella)"}
PLANOS = {
    "detalle": ("Detalle (bien de cerca)", "extreme close-up detail shot"),
    "primer": ("Primer plano", "close-up shot"),
    "medio": ("Plano medio", "medium shot, from the waist up"),
    "americano": ("Plano americano", "medium-long shot, from the knees up"),
    "entero": ("Plano entero", "full-body shot, head to feet in frame"),
    "espejo": ("En el espejo", "shot of her reflection in the full-length mirror, as a mirror selfie"),
}
MOVIMIENTOS = {
    "mano": ("En mano", "handheld phone camera with small natural movements"),
    "acerca": ("Se acerca despacio", "slow push-in towards the subject"),
    "aleja": ("Se aleja despacio", "slow pull-back revealing more"),
    "sigue": ("La acompaña", "the camera follows her movement smoothly"),
    "orbita": ("Gira alrededor", "the camera slowly arcs around her"),
    "paneo": ("Paneo lento", "slow pan across the subject"),
    "fija": ("Fija", "locked-off camera on a small tripod"),
    "cenital": ("Desde arriba", "top-down shot"),
}
MOSTRAR = {
    "cama": ("La prenda sola sobre la cama", "the set alone laid out on the bed, product shot"),
    "gira": ("Que gire despacio y se vea la espalda", "she turns slowly so the back of the set is seen"),
    "abajo": ("Que se vea bien la bombacha (la parte de abajo)", "the bottom piece (the panty) is clearly seen, front and back"),
    "detalle": ("El encaje / la tela de cerca", "a close-up of the lace and the fabric"),
    "espejo": ("Cuerpo entero en el espejo", "a full-body shot in the mirror"),
    "mano": ("Que primero la muestre en la mano", "first she shows the set in her hand before wearing it"),
    "cambio": ("Cambio de color tapando la cámara (si subís más de un color)",
               "colour changes as transitions: she covers the lens with her hand (or passes the garment over the "
               "lens) and the next shot she wears the next colour"),
    "espalda_top": ("De espaldas se desprende el corpiño, lo deja caer y se da vuelta tapándose con el brazo; "
                    "con la otra mano tapa la cámara y aparece con otro color",
                    "the trend move: seen from the back she unclasps the top and lets it fall, turns around covering "
                    "her bust with one arm and covers the lens with the other hand; the next shot she wears another "
                    "colour. Implied only, never explicit"),
    "secuencia": ("Una toma larga sin cortes (como el video de referencia)",
                  "one long continuous take without visible cuts, chaining the shots with enlace \"sigue\""),
}
# Cómo ENTRA cada toma desde la anterior. "sigue" arranca en el último cuadro de la anterior
# (start_image_url): se encadenan 20–30 s sin corte aunque Kling filme 15 s como mucho.
ENLACES = {
    "corte": ("Corte directo", "", ""),
    "mano": ("Tapa la cámara con la mano",
             " At the very end of the shot she reaches her open hand towards the lens and covers it completely, "
             "the frame goes dark.",
             " The shot starts with her hand covering the lens (dark frame) and pulling away to reveal the scene."),
    "prenda": ("Pasa la prenda por delante de la cámara",
               " At the very end of the shot she brings the garment or her hand close to the lens, covering it.",
               " The shot starts with fabric covering the lens and pulling away to reveal her."),
    "giro": ("Barrido rápido de cámara",
             " At the very end the camera whips quickly to the side (fast whip pan with motion blur).",
             " The shot starts in the middle of a fast whip pan with motion blur that lands on the subject."),
    "sigue": ("Sigue sin cortar (misma toma larga)", "",
              " The shot continues seamlessly from its first frame, the same continuous take without any cut: "
              "same position, same light, same movement flow."),
}
ENLACES_TAPAN = ("mano", "prenda")      # en el montaje van con un fundido corto a negro
MOSTRAR_DEFAULT = ("gira", "abajo", "detalle", "espejo")
DICE_DEFAULT = ("Chicas, me llegó el conjunto que les dije. Miren este encaje, es divino y re "
                "cómodo. Escríbanme por DM que les paso los talles.")
DIR = PJ_DIR / "filmado"
DIR.mkdir(parents=True, exist_ok=True)

# Lo que hace que se vea FILMADO y no una foto animada.
_FILMADO = (
    "This is REAL phone footage, not an animated photo: the video starts in the MIDDLE of her "
    "movement (never from a still, posed frame), the phone camera is handheld with small natural "
    "shakes and tiny reframing, focus and exposure adjust slightly as she moves, real indoor "
    "light with soft shadows, subtle grain. She moves like a real person: weight shifts, "
    "breathing, blinking, hair moving, small hand gestures, CALM and unhurried, at real-time "
    "speed (never slow motion, never sped up). Real skin with pores and natural shine, no "
    "smoothing, no plastic or waxy look, no doll face. Her body keeps its real proportions and "
    "weight in every frame (do not enlarge or reshape her). No text, no logos, no watermark, no "
    "other people."
)
_NEGATIVO = ("animated photo, static camera, frozen pose, mannequin, plastic skin, waxy, doll, "
             "CGI, 3D render, slow motion, fast motion, walking into frame, morphing face, "
             "different face, exaggerated body, extra fingers, deformed hands, explicit nudity, visible nipples, "
             "text, watermark")
# Con lip-sync la boca la anima DESPUÉS el lip-sync: si el motor también la mueve, las dos se
# mezclan (se vio en la prueba). Que la filme con la boca quieta y la cara bien a cámara.
_HABLA_MUDA = (" She looks straight into the lens with a warm, friendly expression, her face well lit "
               "and frontal the whole time. Her lips stay softly CLOSED and still — she does NOT speak "
               "and does not move her mouth (the mouth is animated afterwards) — with small natural "
               "head movements, blinking and a slight smile in the eyes.")
_MUESTRA = (" She does not talk in this shot (a voice-over goes on top): relaxed natural "
            "expression, lips closed. When her face is seen it is exactly her face.")
_PRODUCTO = (" No person in this shot: only the lingerie set, real fabric that moves slightly with the "
             "air and the light, as filmed with a phone.")
_REF_ESCENA = ("@Image1 is a frame from an earlier shot of this same video: keep EXACTLY the same room, "
               "furniture, wall colour, bedding, light and time of day{ella}. Only the camera angle, the "
               "framing and the action change.")


def _k_reel(rid: str) -> str:
    return _pfx() + f"filmado:reel:{rid}"


def _k_reels() -> str:
    return _pfx() + "filmado:reels"


def _k_prenda(rid: str, i: int, v: int = 0) -> str:
    """Las fotos de la prenda: el color 0 con la clave de siempre (los reels viejos siguen andando)."""
    return _pfx() + (f"filmado:reel:{rid}:prenda:{i}" if not v else f"filmado:reel:{rid}:v{v}:prenda:{i}")


def _k_ref(rid: str) -> str:
    """El cuadro de la primera toma: el lugar, la luz y el peinado que siguen en las demás."""
    return _pfx() + f"filmado:reel:{rid}:ref"


def _dir(rid: str) -> Path:
    d = DIR / re.sub(r"[^a-z0-9]", "", rid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clip(rid: str, tid: str) -> Path:
    return _dir(rid) / f"{re.sub(r'[^a-z0-9]', '', tid)}.mp4"


def _final(rid: str) -> Path:
    return _dir(rid) / "reel.mp4"


_LOCKS: Dict[str, asyncio.Lock] = {}


def _lock(rid: str) -> asyncio.Lock:
    return _LOCKS.setdefault(rid, asyncio.Lock())


async def _reel(rid: str) -> Dict[str, Any]:
    r = await kv.get(_k_reel(rid))
    if not isinstance(r, dict):
        raise HTTPException(404, "Ese reel no existe.")
    return r


async def _guardar(reel: Dict[str, Any]) -> None:
    await kv.set(_k_reel(reel["id"]), reel)


def _variantes(reel: Dict[str, Any]) -> List[Dict[str, Any]]:
    return reel.get("variantes") or [{"nombre": "", "n": int(reel.get("n_prendas") or 0)}]


async def _prendas(rid: str, reel: Dict[str, Any]) -> List[List[str]]:
    """Las fotos de cada color de la prenda."""
    out = []
    for v, var in enumerate(_variantes(reel)):
        out.append([b for b in [await kv.get(_k_prenda(rid, i, v)) for i in range(int(var.get("n") or 0))] if b])
    return out


def _ps(reel: Dict[str, Any]) -> float:
    return ENERGIAS_VOZ.get(reel.get("energia"), ENERGIAS_VOZ[ENERGIA_FILMADO])["palabras_seg"]


def _dur_voz(reel: Dict[str, Any], t: Dict[str, Any]) -> float:
    return len((t.get("dice") or "").split()) / _ps(reel)


def _seg_kling(reel: Dict[str, Any], t: Dict[str, Any]) -> int:
    """Los segundos que filma (y cobra) el motor para esa toma."""
    if t.get("dice"):
        return max(SEG_MIN, min(SEG_MAX, int(math.ceil(_dur_voz(reel, t) + 0.8))))
    return max(SEG_MIN, int(math.ceil(float(t.get("seg") or SEG_MUESTRA))))


def _seg_toma(reel: Dict[str, Any], t: Dict[str, Any]) -> float:
    """Lo que dura la toma en el reel: la que habla, lo que filmó; las demás se cortan a lo que
    dura su voz (así la voz de fondo corre sin baches) o a los segundos elegidos."""
    if t.get("dice"):
        if t.get("tipo") == "habla":
            return float(_seg_kling(reel, t))
        return round(min(_seg_kling(reel, t), max(SEG_CORTE_MIN, _dur_voz(reel, t) + 0.4)), 1)
    return float(max(SEG_CORTE_MIN, min(10, float(t.get("seg") or SEG_MUESTRA))))


def _costo_toma(reel: Dict[str, Any], t: Dict[str, Any]) -> float:
    s = _seg_kling(reel, t)
    c = s * MOTORES[reel.get("motor", MOTOR_DEFAULT)]["precio_seg"] + COSTO_REVISION
    if t.get("dice"):
        c += COSTO_TTS
        if t.get("tipo") == "habla":
            c += LIPSYNCS["sync"]["precio_seg"] * s
    return round(c, 2)


def _aviso_toma(reel: Dict[str, Any], t: Dict[str, Any]) -> str:
    n = len((t.get("dice") or "").split())
    if n and n / _ps(reel) > SEG_MAX - 1:
        return (f"Es largo para una toma: {n} palabras ≈ {n / _ps(reel):.0f} s. Con esta energía entran "
                f"unas {int((SEG_MAX - 1) * _ps(reel))}: partila en dos tomas.")
    return ""


def _vista(reel: Dict[str, Any]) -> Dict[str, Any]:
    """El reel como lo ve la pantalla: con segundos, costos y avisos calculados."""
    out = {k: v for k, v in reel.items()}
    tomas = []
    for t in reel.get("tomas") or []:
        x = dict(t)
        x["seg_est"] = _seg_toma(reel, t)
        x["costo_est"] = _costo_toma(reel, t)
        x["aviso"] = _aviso_toma(reel, t)
        x["filmada"] = _clip(reel["id"], t["id"]).exists()
        tomas.append(x)
    out["tomas"] = tomas
    out["seg_total"] = round(sum(t["seg_est"] for t in tomas))
    out["costo_total"] = round(sum(t["costo_est"] for t in tomas), 2)
    out["costo_falta"] = round(sum(t["costo_est"] for t in tomas if not t["filmada"]), 2)
    out["listo"] = _final(reel["id"]).exists()
    return out


# ── Claude pregunta y arma el plan ───────────────────────────────────────────

def _contexto(reel: Dict[str, Any]) -> str:
    info = reel.get("info") or {}
    datos = "; ".join(f"{k}: {info[k]}" for k in ("producto", "precio", "talles", "colores", "promo") if info.get(k))
    mostrar = ", ".join(MOSTRAR[k][1] for k in reel.get("mostrar") or [] if k in MOSTRAR)
    modo = reel.get("modo", MODO_DEFAULT)
    return (
        f"Brand: LUMA Íntima (Argentine lingerie). Target length of the whole reel: about {reel.get('duracion', 20)} s. "
        f"Place: {LUGARES.get(reel.get('lugar'), LUGARES['dormitorio'])[1]}. "
        + ("She WEARS the set. " if reel.get("puesta") else "She holds the set in her hands (she wears a casual t-shirt). ")
        + ("She NEVER talks to the camera: her voice goes on top as a voice-over the whole reel. " if modo == "fondo"
           else "She may talk to the camera in 1 or 2 shots (lip-synced); the rest is voice-over. ")
        + (f"What the owner wants to show: {mostrar}. " if mostrar else "")
        + (f"Product data: {datos}. " if datos else "")
        + (f"Owner's notes: {info['notas']}. " if info.get("notas") else "")
        + (f"What you understood of the product: {json.dumps(reel['analisis'], ensure_ascii=False)}. " if reel.get("analisis") else "")
        + (f"The set comes in {len(_variantes(reel))} colours: "
           + ", ".join(f"variante {i} = {v.get('nombre') or 'color ' + str(i + 1)}" for i, v in enumerate(_variantes(reel))) + ". "
           if len(_variantes(reel)) > 1 else "")
        + f"Her voice speaks about {_ps(reel)} words per second."
    )


def _partes_prenda(prendas: List[List[str]], texto: str, reel: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """El texto y las fotos reales de la prenda, color por color (numerados desde 0)."""
    vars_ = _variantes(reel or {}) if reel else [{"nombre": ""}] * len(prendas)
    varios = len([p for p in prendas if p]) > 1
    parts: List[Dict[str, Any]] = [{"type": "text", "text": texto + (
        "\nThe set comes in several colours (variante 0, 1, …). These are the real product photos of each:"
        if varios else "\nThese are the real product photos of the set:")}]
    for v, fotos in enumerate(prendas):
        if not fotos:
            continue
        if varios:
            nombre = (vars_[v].get("nombre") if v < len(vars_) else "") or f"color {v + 1}"
            parts.append({"type": "text", "text": f"variante {v}: {nombre}"})
        for b in fotos[:3 if not varios else 2]:
            parts.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}})
    return parts


_SYSTEM_PREGUNTAS = (
    "You are the creative director of Instagram reels for LUMA Íntima, an Argentine lingerie brand "
    "that sells to real Argentine women (20 to 45). The reel will be filmed by an AI video model, "
    "shot by shot, with the brand's AI model (a woman) and her own voice.\n"
    "FIRST understand what we sell. Study the product photos like a buyer and a stylist: what "
    "exactly the garment is (bra, bralette, body, set with panty — which cut: brazilian, culotte, "
    "high-waist, thong…), the fabric and the lace, the colour, the details (straps, closures, "
    "trims, transparencies, underwire or not), what makes it sell (comfort, support, sexy but "
    "elegant, everyday, special occasion, gift, flattering for curves…) and who buys it.\n"
    "THEN ask the owner what you need to direct a reel that SELLS it. Do NOT ask what you already "
    "know from the photos or from what the owner told you. Ask 3 to 6 short, concrete questions "
    "in Spanish from Argentina (voseo, casual), each with 2 to 4 suggested answers the owner can "
    "tap: the angle of the reel (launch, restock, best seller, gift…), the main benefit to push, "
    "the price/sizes/colours if missing, a promo, how it should end (the call to action), the "
    "vibe (fresh, elegant, playful…), how much to show of the back and the bottom piece.\n"
    'Answer in JSON: {"analisis": {"que_es": "...", "puntos_fuertes": ["...", "..."], '
    '"para_quien": "..."}, "resumen": "one sentence", "preguntas": [{"pregunta": "...", '
    '"opciones": ["...", "..."]}]} — analisis and resumen in Spanish from Argentina.'
)

_SYSTEM_PLAN = (
    "You are the creative director and director of photography of Instagram reels for LUMA Íntima, "
    "an Argentine lingerie brand. Plan a COMPLETE reel that SELLS this set, as a shot list. Each "
    "shot is filmed separately by an AI video model (Kling) from reference images of the brand's "
    "AI model and of the set, and her voice is recorded separately.\n"
    "CRAFT:\n"
    "- Structure like the best lingerie reels: HOOK (0-2 s, the most striking image: a detail in "
    "motion, the reveal, a turn) → REVEAL of the whole set on her → DETAILS (lace, fabric, straps, "
    "closures) → FIT and BACK (a slow turn, the back, the bottom piece, how it fits her body) → "
    "the BENEFIT → the CALL TO ACTION.\n"
    "- Vary the framing: never two shots in a row with the same \"plano\". Include at least one "
    "extreme detail, one full body and one mirror shot. Mix camera moves; the detail shots move "
    "slowly (push-in or pan).\n"
    "- Rhythm: most shots 2 to 4 seconds; the hook is short. {n_tomas} shots, about {duracion} s in total.\n"
    "- PLAY WITH THE CAMERA like a real creator filming herself — this is what makes it feel real: "
    "she covers the lens with her hand to change shot or colour, she passes the garment over the "
    "lens as a wipe, a quick whip pan, she walks up to the phone and picks it up, she props the "
    "phone against the mirror, a mirror selfie with the phone visible, she steps back to show the "
    "full body, she adjusts the phone and the frame shakes a little.\n"
    "- TRANSITIONS: \"enlace\" says how each shot ENTERS from the previous one: \"corte\" (straight cut), "
    "\"mano\" (the previous shot ends with her hand covering the lens, this one starts with the hand "
    "pulling away — the classic outfit/colour change), \"prenda\" (the garment passes over the lens), "
    "\"giro\" (whip pan), \"sigue\" (NO cut: this shot starts exactly on the last frame of the previous "
    "one, to build a long continuous take of 20-30 s from shots of up to 15 s). Colour changes go on "
    "\"mano\" or \"prenda\". A shot entering with \"sigue\" keeps the same colour, place and framing "
    "flow as the previous one. The first shot is always \"corte\".\n"
    "- AI video limits: ONE simple action per shot (a transition gesture at the end is fine). Turns "
    "are slow (about 180 degrees). No lying down, no hands on the face, no fast moves. Detail shots "
    "do not need her face.\n"
    "- Continuity: write \"continuidad\" once, in English: the exact room, furniture, bedding, light "
    "and time of day, her hairstyle, makeup and jewellery. It is the same in every shot.\n"
    "- Sensual, confident and natural like the best lingerie try-on reels, but never explicit: no "
    "visible nipples or genitals. If the owner asked for the top-removal move, it is IMPLIED: she is "
    "seen from the back when she unclasps and drops the top, then turns covering her bust fully "
    "with her forearm, and covers the lens with the other hand (the next shot in another colour).\n"
    "SHOT FIELDS:\n"
    "- \"tipo\": {tipos}.\n"
    "- \"plano\": one of {planos}. \"movimiento\": one of {movimientos}.\n"
    "- \"enlace\": one of {enlaces}. \"variante\": the colour she wears (or that is shown) in the shot, "
    "0 to {max_var}.\n"
    "- \"dice\": the voice in that shot, in Spanish from Argentina (Rioplatense, voseo, casual, like a "
    "real influencer talking to her followers; no hashtags, no emojis), tied to what is seen. At "
    "most {max_palabras} words per shot; about {palabras_total} words in the whole reel. It can be "
    "empty (a silent shot).\n"
    "- \"accion\": what is seen, in Spanish, one short sentence for the owner.\n"
    "- \"toma\": the direction for the video model, in English, max 70 words: the action, her "
    "expression and what must be clearly seen of the set. Do NOT describe the framing or the "
    "camera (they go in plano and movimiento). Refer to her as \"she\" and to the set as \"the set\".\n"
    "- \"seg\": only for a shot with empty \"dice\": 2 to 6 seconds (up to 10 inside a long take).\n"
    "Also write \"concepto\": the idea of the reel in one sentence, in Spanish.\n"
    "Use what the owner answered. Do not invent a price, sizes or a promo that nobody told you.\n"
    'Answer in JSON: {{"titulo": "...", "concepto": "...", "continuidad": "...", "tomas": [{{"tipo": "...", '
    '"plano": "...", "movimiento": "...", "enlace": "corte", "variante": 0, "dice": "...", "accion": "...", '
    '"toma": "...", "seg": 0}}]}}'
)


async def claude_preguntas(reel: Dict[str, Any], prendas: List[List[str]]) -> Tuple[Dict[str, Any], float]:
    data, costo = await _claude.pedir_json(_SYSTEM_PREGUNTAS, _partes_prenda(prendas, _contexto(reel), reel),
                                           max_tokens=6000, esfuerzo="high")
    preguntas = []
    for q in (data.get("preguntas") or [])[:6]:
        if isinstance(q, dict) and str(q.get("pregunta") or "").strip():
            preguntas.append({"pregunta": _texto(q["pregunta"], 300),
                              "opciones": [_texto(o, 200) for o in (q.get("opciones") or [])[:4] if str(o).strip()],
                              "respuesta": ""})
    if not preguntas:
        raise _claude.ClaudeNoDisponible("Claude no devolvió preguntas.")
    a = data.get("analisis") if isinstance(data.get("analisis"), dict) else {}
    analisis = {"que_es": _texto(a.get("que_es"), 300), "para_quien": _texto(a.get("para_quien"), 300),
                "puntos_fuertes": [_texto(x, 200) for x in (a.get("puntos_fuertes") or [])[:5] if str(x).strip()]}
    return {"resumen": _texto(data.get("resumen"), 400), "preguntas": preguntas,
            "analisis": analisis if analisis["que_es"] else None}, costo


def _limpiar_toma(reel: Dict[str, Any], t: Dict[str, Any]) -> Dict[str, Any]:
    tipo = t.get("tipo") if t.get("tipo") in TIPOS else "muestra"
    if tipo == "habla" and reel.get("modo", MODO_DEFAULT) == "fondo":
        tipo = "muestra"
    dice = " ".join(_texto(t.get("dice"), 600).split()[:int((SEG_MAX - 1) * _ps(reel))])
    try:
        seg = float(t.get("seg") or 0)
    except (TypeError, ValueError):
        seg = 0
    plano = t.get("plano") if t.get("plano") in PLANOS else ("medio" if tipo == "habla" else "americano")
    mov = t.get("movimiento") if t.get("movimiento") in MOVIMIENTOS else "mano"
    try:
        var = max(0, min(len(_variantes(reel)) - 1, int(t.get("variante") or 0)))
    except (TypeError, ValueError):
        var = 0
    return {"id": "t" + _uuid.uuid4().hex[:7], "tipo": tipo, "plano": plano, "movimiento": mov, "dice": dice,
            "enlace": t.get("enlace") if t.get("enlace") in ENLACES else "corte", "variante": var,
            "accion": _texto(t.get("accion"), 400), "toma": _texto(t.get("toma"), 900),
            "seg": max(SEG_CORTE_MIN, min(10, seg or SEG_MUESTRA)) if not dice else 0}


async def claude_plan(reel: Dict[str, Any], prendas: List[List[str]]) -> Tuple[Dict[str, Any], float]:
    qa = "\n".join(f"- {q['pregunta']} → {q.get('respuesta') or '(sin respuesta: decidí vos)'}"
                   for q in reel.get("preguntas") or [])
    dur = int(reel.get("duracion", 20))
    fondo = reel.get("modo", MODO_DEFAULT) == "fondo"
    tipos = ('"muestra" = she shows the set (her voice goes on top), "producto" = only the set, no '
             'person (on the bed, on a hanger…)') if fondo else (
            '"habla" = she talks TO THE CAMERA, medium shot or close-up, face frontal (lip-synced; use '
            'it for 1 or 2 shots at most, e.g. the hook or the call to action), "muestra" = she shows '
            'the set (her voice goes on top), "producto" = only the set, no person')
    system = _SYSTEM_PLAN.format(n_tomas=f"{max(4, dur // 4)} to {min(MAX_TOMAS, max(5, dur // 3))}", duracion=dur,
                                 tipos=tipos, planos=", ".join(PLANOS), movimientos=", ".join(MOVIMIENTOS),
                                 enlaces=", ".join(ENLACES), max_var=len(_variantes(reel)) - 1,
                                 max_palabras=int(6 * _ps(reel)), palabras_total=int(dur * _ps(reel) * 0.75))
    texto = _contexto(reel) + (f"\nThe owner's answers to your questions:\n{qa}" if qa else "")
    data, costo = await _claude.pedir_json(system, _partes_prenda(prendas, texto, reel), max_tokens=16000,
                                           esfuerzo="high")
    tomas = [_limpiar_toma(reel, t) for t in (data.get("tomas") or [])[:MAX_TOMAS] if isinstance(t, dict)]
    tomas = [t for t in tomas if t["toma"] or t["accion"]]
    if len(tomas) < 2:
        raise _claude.ClaudeNoDisponible("Claude no devolvió un plan usable.")
    tomas[0]["enlace"] = "corte"
    return {"titulo": _texto(data.get("titulo"), 120), "concepto": _texto(data.get("concepto"), 400),
            "continuidad": _texto(data.get("continuidad"), 900), "tomas": tomas}, costo


def _plan_sin_claude(reel: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Si Claude no está: un plan fijo con lo que se pidió mostrar."""
    mostrar = reel.get("mostrar") or []
    base = [{"tipo": "muestra", "plano": "detalle", "movimiento": "acerca", "dice": "Chicas, miren lo que me llegó.",
             "accion": "El encaje bien de cerca.", "toma": "The lace of the set, the fabric moves slightly as she breathes."},
            {"tipo": "muestra", "plano": "entero", "movimiento": "mano", "dice": "",  "seg": 3,
             "accion": "Se ve el conjunto entero puesto.", "toma": "She stands relaxed, weight on one leg, and smiles."}]
    if "gira" in mostrar or "abajo" in mostrar:
        base.append({"tipo": "muestra", "plano": "americano", "movimiento": "fija", "dice": "", "seg": 4,
                     "accion": "Gira despacio y se ve la espalda y la bombacha.",
                     "toma": "She turns slowly about 180 degrees so the back of the set and the bottom piece are seen, then faces the camera again."})
    if "espejo" in mostrar:
        base.append({"tipo": "muestra", "plano": "espejo", "movimiento": "mano", "dice": "Es re cómodo, lo uso todo el día.",
                     "accion": "Selfie en el espejo.", "toma": "She takes a mirror selfie with her phone, relaxed pose."})
    base.append({"tipo": "habla" if reel.get("modo") == "habla" else "muestra", "plano": "medio", "movimiento": "acerca",
                 "dice": "Escríbanme por DM que les paso los talles.",
                 "accion": "Mira a cámara y sonríe.", "toma": "She looks at the camera and smiles."})
    return [_limpiar_toma(reel, t) for t in base]


# ── Filmar una toma ──────────────────────────────────────────────────────────

def _refs_texto(motor: str, n_ref_ella: int, n_prendas: int, con_ref: bool,
                producto: bool) -> Tuple[str, str, str]:
    """Cómo se nombra a ella, a la prenda y al cuadro de referencia del lugar, según el motor
    (y en el mismo orden en que van las imágenes en el pedido)."""
    if MOTORES[motor]["tipo"] == "kling":
        return ("" if producto else "@Element1"), ("@Element1" if producto else "@Element2"), \
            ("@Image1" if con_ref else "")
    k = 1
    escena = ""
    if con_ref:
        escena, k = "@Image1", 2
    ella = ""
    if not producto:
        ella = "the woman of " + " and ".join(f"@Image{k + i}" for i in range(n_ref_ella))
        k += n_ref_ella
    return ella, " and ".join(f"@Image{k + i}" for i in range(n_prendas)), escena


_FILMADO_PRODUCTO = ("This is REAL phone footage, not a render: real indoor light with soft shadows, "
                     "subtle grain, real fabric texture and lace detail in focus. No text, no logos, "
                     "no watermark, no people.")


async def prompt_toma(doc: Dict[str, Any], reel: Dict[str, Any], t: Dict[str, Any], n_ref_ella: int,
                      n_prendas: int, con_ref: bool = False, siguiente: str = "corte") -> str:
    """El pedido al motor para una toma. La voz NO va: el motor filma mudo. Cómo arranca sale de
    su "enlace" (cómo entra desde la anterior) y cómo termina, del enlace de la que sigue: la mano
    que tapa la cámara al final de una es la que se aparta al principio de la otra."""
    motor = reel.get("motor", MOTOR_DEFAULT)
    producto = t.get("tipo") == "producto"
    ella, prenda, escena = _refs_texto(motor, n_ref_ella, n_prendas, con_ref, producto)
    exacta = ("EXACTLY that design, cut, colour, lace pattern, straps and trims, front and back — "
              "not a generic or plain version")
    toma = t.get("toma") or ""
    if not toma:           # la acción la escribió la persona: se traduce tal cual
        toma = (await _al_ingles({"a": t.get("accion") or ""})).get("a") or t.get("accion") or ""
    plano = PLANOS.get(t.get("plano"), PLANOS["medio"])[1]
    mov = MOVIMIENTOS.get(t.get("movimiento"), MOVIMIENTOS["mano"])[1]
    lugar = LUGARES.get(reel.get("lugar"), LUGARES["dormitorio"])[1]
    cont = f" CONTINUITY (identical in every shot of this reel): {reel['continuidad']}." if reel.get("continuidad") else ""
    ref = (" " + _REF_ESCENA.format(ella="" if producto else ", and her same hairstyle, makeup and jewellery")
           if escena else "")
    entra = ENLACES.get(t.get("enlace") or "corte", ENLACES["corte"])[2]
    sale = ENLACES.get(siguiente or "corte", ENLACES["corte"])[1]
    cabeza = f"Vertical 9:16 Instagram reel shot on a phone. SHOT: {plano}, {mov}."
    if producto:
        sujeto = f" SUBJECT: the lingerie set {prenda} alone ({exacta})."
        boca, realismo, realismo_corto, cuerpo = _PRODUCTO, " " + _FILMADO_PRODUCTO, " Real phone footage, real fabric texture, no text, no people.", ""
    else:
        if reel.get("puesta"):
            ropa = f"wearing the lingerie set {prenda} ({exacta})"
        else:
            ropa = (f"wearing a casual fitted black t-shirt and jeans, and holding the lingerie set {prenda} "
                    f"in her hands to show it ({exacta})")
        sujeto = f" {ella}, the same exact woman (same face, hair and body), {ropa}."
        habla = t.get("tipo") == "habla" and t.get("dice")
        boca = _HABLA_MUDA if habla else _MUESTRA
        realismo, realismo_corto = " " + _FILMADO, (" Real phone footage, not an animated photo: handheld, natural "
                                                   "light, real skin texture, calm real-time movement, no plastic "
                                                   "look, no text, no other people.")
        cuerpo = await _cuerpo_en(doc)
    # Por prioridad: si el pedido se pasa de lo que acepta Kling, se acorta primero lo de abajo
    # de la lista (nunca la toma, la prenda ni la transición).
    partes = [  # (texto, versión corta o "", cuánto se puede sacrificar: más alto = primero)
        (cabeza, cabeza, 0), (sujeto, sujeto, 0), (entra, entra, 1), (" " + toma, " " + toma, 0), (sale, sale, 1),
        (f" Her body: {cuerpo}." if cuerpo else "", "", 7),
        (f" PLACE: {lugar}.", "" if cont else f" PLACE: {lugar[:140]}.", 6),
        (cont, cont[:260] + ("…" if len(cont) > 260 else ""), 4),
        (ref, " Keep the same room, light" + ("" if producto else " and hairstyle") + " as @Image1." if ref else "", 3),
        (boca, (" Her lips stay softly closed and still (the mouth is animated later)." if boca is _HABLA_MUDA else ""), 2),
        (realismo, realismo_corto, 5),
    ]
    return _componer(partes)


def _componer(partes: List[Tuple[str, str, int]], limite: int = 0) -> str:
    """Arma el pedido sin pasarse de `limite` caracteres: acorta primero lo más sacrificable."""
    limite = limite or MAX_PROMPT
    textos = [p[0] for p in partes]
    for k in sorted(range(len(partes)), key=lambda k: -partes[k][2]):
        if len("".join(textos)) <= limite:
            break
        if partes[k][2] > 0:
            textos[k] = partes[k][1]
    out = "".join(textos).strip()
    if len(out) > limite:           # último recurso: cortar en una frase
        out = out[:limite]
        out = out[:out.rfind(". ") + 1] if ". " in out[limite // 2:] else out
    return out


async def _voz(doc: Dict[str, Any], reel: Dict[str, Any], texto: str, destino: Path) -> float:
    """La voz de Reels (Gemini, rioplatense) con su energía y el aire de micrófono, en
    `destino`; devuelve los segundos. Va ANTES del lip-sync: la boca sincroniza con esta voz."""
    falso = {"voz": reel.get("voz") or "", "tono": reel.get("tono") or "cercana",
             "voz_energia": reel.get("energia") or ENERGIA_FILMADO}
    await _cobrar(COSTO_TTS)
    mp3 = await _tts_mp3(texto, _voz_reel(doc, falso), doc, instruccion=_instruccion_voz(doc, falso))
    await budget_record("filmado_voz", "mp3", COSTO_TTS, 1, note="filmado: voz")
    af = ",".join(x for x in (ENERGIAS_VOZ.get(falso["voz_energia"], ENERGIAS_VOZ[ENERGIA_FILMADO])["af"],
                              _AF_VOZ if reel.get("mic", True) else "") if x)
    if af:
        try:
            mp3 = await asyncio.to_thread(_tratar_voz, mp3, af, destino.parent, destino.stem + "_t")
        except Exception as e:
            print(f"[filmado] no pude tratar la voz: {e}")
    destino.write_bytes(mp3)
    return round(_duracion_video(destino) or len(texto.split()) / _ps(reel), 2)


def _ff(cmd: List[str], timeout: int = 600) -> None:
    res = subprocess.run([_ffmpeg_bin()] + cmd, capture_output=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg: " + res.stderr.decode(errors="ignore")[-300:])


def _tiene_audio(p: Path) -> bool:
    res = subprocess.run([_ffmpeg_bin(), "-i", str(p)], capture_output=True, timeout=60)
    return "Audio:" in res.stderr.decode(errors="ignore")


def _completar_voz(voz: Path, seg: float, salida: Path) -> None:
    """La voz con silencio al final hasta el largo del video: así nada la corta."""
    _ff(["-y", "-i", str(voz), "-af", "apad", "-t", f"{seg:.3f}", "-ar", "48000", "-ac", "2",
         "-b:a", "192k", str(salida)], timeout=120)


def _normalizar(video: Path, salida: Path, voz: Optional[Path] = None, largo: float = 0.0,
                recorte: str = "centro") -> float:
    """La toma en 1080x1920, 30 fps, con audio estéreo (su voz, la del lip-sync o silencio).
    Con `largo` se corta a eso: "centro" salta el arranque (lo más quieto), "inicio" guarda el
    principio (la toma entra con un gesto o sigue a la anterior sin corte), "final" guarda el
    final (termina tapando la cámara). Si la voz dura más que el video, el último cuadro se
    sostiene: la voz nunca se corta."""
    dv = _duracion_video(video)
    salto = 0.0
    if largo and dv > largo + 0.05:
        salto = {"inicio": 0.0, "final": dv - largo}.get(recorte, min(0.4, dv - largo))
        dv = largo
    da = _duracion_video(voz) if voz else 0.0
    extra = max(0.0, da - dv + 0.15) if voz else 0.0
    vf = (f"scale={ANCHO}:{ALTO}:force_original_aspect_ratio=increase,crop={ANCHO}:{ALTO},fps=30"
          + (f",tpad=stop_mode=clone:stop_duration={extra:.2f}" if extra > 0.05 else "") + ",format=yuv420p")
    total = dv + (extra if extra > 0.05 else 0.0)
    ss = ["-ss", f"{salto:.2f}"] if salto else []
    if voz:
        entradas = ss + ["-i", str(video), "-i", str(voz)]
        audio = "[1:a]aresample=48000,aformat=channel_layouts=stereo,apad[au]"
    elif _tiene_audio(video):
        entradas = ss + ["-i", str(video)]
        audio = "[0:a]aresample=48000,aformat=channel_layouts=stereo,apad[au]"
    else:
        entradas = ss + ["-i", str(video), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        audio = "[1:a]anull[au]"
    _ff(["-y"] + entradas + ["-filter_complex", f"[0:v]{vf}[vo];{audio}", "-map", "[vo]", "-map", "[au]",
                             "-t", f"{total:.3f}", "-c:v", "libx264", "-crf", "17", "-preset", "medium",
                             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(salida)])
    return round(total, 2)


def _recorte(tomas: List[Dict[str, Any]], i: int) -> str:
    """Qué parte de la toma i se guarda si hay que cortarla (ver _normalizar)."""
    entra = (tomas[i].get("enlace") or "corte") if i > 0 else "corte"
    sale = (tomas[i + 1].get("enlace") or "corte") if i + 1 < len(tomas) else "corte"
    guarda_final = sale in ("mano", "prenda", "giro")
    guarda_inicio = entra != "corte"
    if guarda_final and guarda_inicio:
        return "nada"
    return "final" if guarda_final else "inicio" if guarda_inicio else "centro"


def _error_corto(e: Exception) -> str:
    """El error de fal en castellano y sin el pedido entero pegado."""
    txt = str(getattr(e, "detail", "") or e)
    if "string_too_long" in txt:
        return "el pedido a Kling quedó más largo de lo que acepta (2.500 letras)"
    if "content" in txt.lower() and ("policy" in txt.lower() or "moderation" in txt.lower() or "safety" in txt.lower()):
        return "el filtro de Kling rechazó esta toma: suavizá lo que hace y rehacela"
    m = re.search(r'"msg"\s*:\s*"([^"]{1,160})', txt)
    return (m.group(1) if m else txt)[:220]


def _ultimo_cuadro(video: Path) -> Optional[str]:
    """El último cuadro de la toma: de ahí arranca la que sigue sin cortar."""
    out = video.with_name(video.stem + "_ultimo.jpg")
    res = subprocess.run([_ffmpeg_bin(), "-y", "-sseof", "-0.4", "-i", str(video), "-update", "1", "-q:v", "2",
                          str(out)], capture_output=True, timeout=60)
    if res.returncode != 0 or not out.exists():
        return None
    try:
        return base64.b64encode(out.read_bytes()).decode()
    finally:
        out.unlink()


def _unir(clips: List[Path], salida: Path, look: str, camara: str, enlaces: Optional[List[str]] = None) -> None:
    """Une las tomas y le pasa el filtro de Reels. Donde ella tapa la cámara, un fundido cortito
    a negro entre las dos (la mano ya oscurece: el negro une las dos tomas como en los reels)."""
    f = _FILTRO_LOOK.get(look)
    if camara == "mano":
        base = (f"scale={int(ANCHO * _MANO_ESCALA)}:{int(ALTO * _MANO_ESCALA)}:force_original_aspect_ratio=increase,"
                f"{_MANO_CROP}")
    else:
        base = f"scale={ANCHO}:{ALTO}"
    entradas: List[str] = []
    for c in clips:
        entradas += ["-i", str(c)]
    enlaces = enlaces or ["corte"] * len(clips)
    cadenas, pares = [], ""
    for k, c in enumerate(clips):
        fx = []
        if k > 0 and enlaces[k] in ENLACES_TAPAN:
            fx.append(f"fade=t=in:st=0:d={FUNDIDO}")
        if k + 1 < len(clips) and enlaces[k + 1] in ENLACES_TAPAN:
            fx.append(f"fade=t=out:st={max(0.0, _duracion_video(c) - FUNDIDO):.3f}:d={FUNDIDO}")
        if fx:
            cadenas.append(f"[{k}:v]{','.join(fx)}[f{k}]")
            pares += f"[f{k}][{k}:a]"
        else:
            pares += f"[{k}:v][{k}:a]"
    grafo = ("".join(x + ";" for x in cadenas) + f"{pares}concat=n={len(clips)}:v=1:a=1[cv][ca];[cv]{base}"
             + (f",{f}" if f else "") + ",format=yuv420p[vo]")
    _ff(["-y"] + entradas + ["-filter_complex", grafo, "-map", "[vo]", "-map", "[ca]", "-c:v", "libx264",
                             "-crf", "17", "-preset", "medium", "-c:a", "aac", "-b:a", "160k",
                             "-movflags", "+faststart", str(salida)], timeout=1200)


def _cuadro(video: Path, t: float) -> Optional[str]:
    out = video.with_name(video.stem + f"_c{int(t * 10)}.jpg")
    res = subprocess.run([_ffmpeg_bin(), "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                          "-vf", "scale=720:-2", str(out)], capture_output=True, timeout=60)
    if res.returncode != 0 or not out.exists():
        return None
    try:
        return base64.b64encode(out.read_bytes()).decode()
    finally:
        out.unlink()


async def _fal_video(cli: httpx.AsyncClient, headers: Dict[str, str], modelo: str, payload: Dict[str, Any],
                     sub_jid: str, opc: Tuple[str, ...], destino: Path) -> None:
    """Manda a fal y baja el video. Cada toma usa su propio sub-trabajo: así varias tomas
    pueden estar en fal a la vez sin pisarse las URLs."""
    await _fal_enviar(cli, headers, modelo, payload, sub_jid, opc)
    job = await kv.get(_k_job(sub_jid)) or {}
    await _fal_esperar_y_bajar(cli, headers, job["fal_status_url"], job["fal_result_url"], destino, sub_jid,
                               inicio=job.get("fal_inicio"))


async def _filmar_toma(jid: str, doc: Dict[str, Any], reel: Dict[str, Any], t: Dict[str, Any],
                       u_ella: List[str], u_prendas: List[str], cara: str, prendas: List[str],
                       cli: httpx.AsyncClient, key: str, headers: Dict[str, str],
                       u_ref: str = "", u_inicio: str = "", siguiente: str = "corte",
                       recorte: str = "centro") -> Dict[str, Any]:
    """Voz → Kling mudo → lip-sync (habla) o voz encima (muestra) → toma normalizada → revisión."""
    rid, tid = reel["id"], t["id"]
    d = _dir(rid)
    motor = reel.get("motor", MOTOR_DEFAULT)
    m = MOTORES[motor]
    res: Dict[str, Any] = {"costo": 0.0, "lipsync": "", "revision": None, "error": ""}
    voz, voz_seg = None, 0.0
    if t.get("dice"):
        voz = d / f"{tid}_voz.mp3"
        voz_seg = await _voz(doc, reel, t["dice"], voz)
        res["costo"] += COSTO_TTS
        seg = max(SEG_MIN, min(SEG_MAX, int(math.ceil(voz_seg + 0.8))))
    else:
        seg = _seg_kling(reel, t)
    producto = t.get("tipo") == "producto"
    prompt = await prompt_toma(doc, reel, t, len(u_ella), len(u_prendas), bool(u_ref), siguiente)
    el_prenda = {"frontal_image_url": u_prendas[0], "reference_image_urls": u_prendas[1:]}
    if m["tipo"] == "kling":
        # La PRENDA como elemento propio (@Element2): como imagen suelta, Kling la tomaba de
        # inspiración e inventaba otra. El cuadro de la primera toma va como @Image1: el mismo
        # cuarto, la misma luz y el mismo peinado en todas.
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "elements": ([el_prenda] if producto else
                         [{"frontal_image_url": u_ella[0], "reference_image_urls": u_ella[1:]}, el_prenda]),
            "duration": str(seg), "aspect_ratio": "9:16", "generate_audio": False,
            "negative_prompt": _NEGATIVO, "cfg_scale": 0.5}
        if u_ref:
            payload["image_urls"] = [u_ref]
        if u_inicio:
            # "Sigue sin cortar": arranca en el último cuadro de la toma anterior.
            payload["start_image_url"] = u_inicio
        opc: Tuple[str, ...] = ("negative_prompt", "cfg_scale")
    else:
        payload = {"prompt": prompt, "image_urls": ([u_ref] if u_ref else []) + ([] if producto else u_ella) + u_prendas,
                   "resolution": "720p",
                   "duration": str(seg), "aspect_ratio": "9:16", "generate_audio": False,
                   "enable_safety_checker": False}
        opc = ("enable_safety_checker", "resolution")
    crudo = d / f"{tid}_crudo.mp4"
    await _fal_video(cli, headers, m["modelo"], payload, f"{jid}-{tid}", opc, crudo)
    c = round(seg * m["precio_seg"], 3)
    res["costo"] += c
    await budget_record("filmado_toma", motor, c, 1, note=f"{doc.get('nombre', '')}: filmado, toma")
    clip_seg = _duracion_video(crudo)
    fuente, voz_final = crudo, voz
    if voz and t.get("tipo") == "habla":
        # La voz completa con silencio hasta el largo del video (nada se corta); si el video
        # quedó más corto que la voz, el lip-sync lo alarga ("bounce") en vez de cortar la voz.
        pad = d / f"{tid}_voz_pad.mp3"
        if clip_seg >= voz_seg:
            await asyncio.to_thread(_completar_voz, voz, clip_seg, pad)
        else:
            pad = voz
        ls = LIPSYNCS["sync"]
        try:
            u_vid = await _fal_subir(cli, key, crudo.read_bytes(), "video/mp4", f"{tid}.mp4")
            u_voz = await _fal_subir(cli, key, pad.read_bytes(), "audio/mpeg", f"{tid}.mp3")
            sal = d / f"{tid}_lipsync.mp4"
            await _fal_video(cli, headers, ls["modelo"],
                             {"video_url": u_vid, "audio_url": u_voz,
                              "sync_mode": "cut_off" if clip_seg >= voz_seg else "bounce"},
                             f"{jid}-{tid}-ls", ("sync_mode",), sal)
            c = round(max(clip_seg, voz_seg) * ls["precio_seg"], 3)
            res["costo"] += c
            await budget_record("filmado_lipsync", ls["modelo"], c, 1, note="filmado: lip-sync")
            # Si el lip-sync devolvió menos de lo que dura la voz, su voz entera va igual.
            fuente, voz_final = sal, (voz if _duracion_video(sal) + 0.2 < voz_seg else None)
            res["lipsync"] = "ok"
        except Exception as e:
            print(f"[filmado] lip-sync falló: {e}")
            res["lipsync"] = f"falló ({str(getattr(e, 'detail', '') or e)[:120]}): va su voz encima, sin mover la boca"
    # Las que no hablan se cortan a lo que dura su voz (la voz de fondo corre sin baches) o a
    # los segundos elegidos; la que habla queda entera.
    largo = 0.0 if t.get("tipo") == "habla" else (
        max(SEG_CORTE_MIN, voz_seg + 0.4) if voz else _seg_toma(reel, t))
    res["seg"] = await asyncio.to_thread(_normalizar, fuente, _clip(rid, tid), voz_final, largo, recorte)
    res["voz_seg"], res["clip_seg"] = voz_seg, round(clip_seg, 2)
    for x in d.glob(f"{tid}_*"):
        try:
            x.unlink()
        except OSError:
            pass
    # Claude mira un cuadro de la toma contra la cara y la prenda (sólo informa).
    if _claude.disponible():
        cuadro = await asyncio.to_thread(_cuadro, _clip(rid, tid), res["seg"] * 0.55)
        if cuadro:
            try:
                pedido = ("La prenda de las fotos del producto tiene que verse EXACTA (diseño, color, encaje, "
                          "breteles; de espalda también) "
                          + ("sola, sin nadie. " if producto else "puesta. " if reel.get("puesta") else "en sus manos. ")
                          + f"En esta toma: {t.get('accion') or ''}. "
                          + ("" if producto else "Es la misma modelo de la cara de referencia. ")
                          + "Es un cuadro de un video de celular: juzgá la prenda"
                          + ("" if producto else ", la cara") + " y que parezca real.")
                rev, c = await _claude.revisar_foto(cuadro, pedido, "" if producto else cara, prendas[:2])
                res["costo"] += c
                res["revision"] = {"puntaje": rev.get("puntaje"), "fallas": rev.get("fallas")}
                await budget_record("filmado_claude", _claude.MODELO, c, 1, note="filmado: revisión")
            except _claude.ClaudeNoDisponible as e:
                print(f"[filmado] Claude no pudo revisar: {e}")
    return res


async def _procesar(jid: str, rid: str, sub: Optional[str]) -> None:
    set_current_sub(sub)
    parar = asyncio.Event()
    _spawn(_latir(jid, parar))
    try:
        reel = await _reel(rid)
        doc = await _doc(reel["pid"])
        refs = await _refs_identidad(doc)
        if not refs:
            raise RuntimeError("Este personaje todavía no tiene retrato aprobado.")
        retrato = refs[0][1]
        cara = await recorte_cara_avatar({"id": "pj:" + str(doc.get("id", "")), "ref_b64": retrato})
        cuerpo = _ref_cuerpo(refs)
        ella = ([cara or retrato] + ([retrato] if cara else []) + ([cuerpo] if cuerpo else []))[:3]
        prendas = await _prendas(rid, reel)
        if not any(prendas):
            raise RuntimeError("No encuentro las fotos de la prenda de este reel.")
        tomas = reel["tomas"]
        faltan = [t for t in tomas if not _clip(rid, t["id"]).exists()]
        key = await _fal_key()
        headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
        total = len(faltan)
        hechas: List[str] = []
        fallas: List[str] = []
        kling = MOTORES[reel.get("motor", MOTOR_DEFAULT)]["tipo"] == "kling"
        async with httpx.AsyncClient(timeout=300) as cli:
            u_ella: List[str] = []
            if faltan:
                await _job_set(jid, {"estado": "generando", "paso": "Subiendo sus fotos y la prenda a fal…"})
                u_ella = [await _fal_subir(cli, key, base64.b64decode(b), "image/jpeg", f"{rid}-ella{i}.jpg")
                          for i, b in enumerate(ella)]
            u_vars: Dict[int, List[str]] = {}
            subiendo = asyncio.Lock()

            def color(t: Dict[str, Any]) -> int:
                v = int(t.get("variante") or 0)
                return v if 0 <= v < len(prendas) and prendas[v] else next(k for k, p in enumerate(prendas) if p)

            async def urls_color(v: int) -> List[str]:
                async with subiendo:        # cada color se sube una sola vez, y sólo si se usa
                    if v not in u_vars:
                        u_vars[v] = [await _fal_subir(cli, key, base64.b64decode(b), "image/jpeg",
                                                      f"{rid}-v{v}-prenda{i}.jpg") for i, b in enumerate(prendas[v])]
                    return u_vars[v]

            sem = asyncio.Semaphore(PARALELO)
            intentadas: set = set()      # cada toma se intenta UNA vez por corrida (no se paga dos veces)

            async def una(t: Dict[str, Any], u_ref: str, u_inicio: str = "") -> None:
                intentadas.add(t["id"])
                i = tomas.index(t)
                v = color(t)
                siguiente = (tomas[i + 1].get("enlace") or "corte") if i + 1 < len(tomas) else "corte"
                async with sem:
                    try:
                        r = await _filmar_toma(jid, doc, reel, t, u_ella, await urls_color(v), cara or retrato,
                                               prendas[v], cli, key, headers, u_ref, u_inicio, siguiente,
                                               _recorte(tomas, i))
                    except Exception as e:
                        fallas.append(f"Toma {i + 1}: {_error_corto(e)}")
                        r = {"error": _error_corto(e)}
                async with _lock(rid):
                    fresco = await _reel(rid)
                    for x in fresco["tomas"]:
                        if x["id"] == t["id"]:
                            x.update({k: r.get(k) for k in ("lipsync", "revision", "error", "seg", "voz_seg",
                                                            "clip_seg") if k in r})
                            x["costo"] = round(float(x.get("costo") or 0) + float(r.get("costo") or 0), 2)
                    fresco["costo"] = round(float(fresco.get("costo") or 0) + float(r.get("costo") or 0), 2)
                    await _guardar(fresco)
                if not r.get("error"):
                    hechas.append(t["id"])
                await _job_set(jid, {"paso": f"Filmando las tomas: {len(hechas)} de {total} listas"
                                             + (f" ({len(fallas)} fallaron)" if fallas else "") + "…"})

            async def cadena(ts: List[Dict[str, Any]], u_ref: str) -> None:
                """Una toma larga: las que "siguen sin cortar" arrancan en el último cuadro de la
                anterior, así que van en orden. Las cadenas distintas se filman a la vez."""
                for t in ts:
                    if _clip(rid, t["id"]).exists() or t["id"] in intentadas:
                        continue
                    i = tomas.index(t)
                    u_inicio = ""
                    if i > 0 and t.get("enlace") == "sigue" and kling:
                        previa = _clip(rid, tomas[i - 1]["id"])
                        cuadro = await asyncio.to_thread(_ultimo_cuadro, previa) if previa.exists() else None
                        if not cuadro:
                            fallas.append(f"Toma {i + 1}: sigue a la toma {i}, que no salió")
                            continue
                        u_inicio = await _fal_subir(cli, key, base64.b64decode(cuadro), "image/jpeg",
                                                    f"{rid}-{t['id']}-inicio.jpg")
                    await una(t, u_ref, u_inicio)

            cadenas: List[List[Dict[str, Any]]] = []
            for i, t in enumerate(tomas):
                if i > 0 and t.get("enlace") == "sigue" and kling:
                    cadenas[-1].append(t)
                else:
                    cadenas.append([t])
            u_ref = ""
            ref = await kv.get(_k_ref(rid)) if faltan else None
            if faltan and not ref:
                # El cuadro con el cuarto, la luz y el peinado que siguen en todas las demás (sin
                # eso cada toma inventaba otro): de una toma con ella ya filmada, o se filma primero una.
                hecha = next((t for t in tomas if t.get("tipo") != "producto" and _clip(rid, t["id"]).exists()), None)
                if not hecha:
                    primera = next((t for t in faltan if t.get("tipo") != "producto" and
                                    (tomas.index(t) == 0 or t.get("enlace") != "sigue")), faltan[0])
                    await _job_set(jid, {"estado": "generando",
                                         "paso": "Filmando la primera toma (de ahí salen el cuarto, la luz y el "
                                                 "peinado para las demás)…"})
                    await una(primera, "")
                    hecha = primera if _clip(rid, primera["id"]).exists() and primera.get("tipo") != "producto" else None
                if hecha:
                    ref = await asyncio.to_thread(_cuadro, _clip(rid, hecha["id"]),
                                                  _duracion_video(_clip(rid, hecha["id"])) * 0.5)
                    if ref:
                        await kv.set(_k_ref(rid), ref)
            pendientes = [c for c in cadenas if any(not _clip(rid, t["id"]).exists() for t in c)]
            if pendientes and ref:
                u_ref = await _fal_subir(cli, key, base64.b64decode(ref), "image/jpeg", f"{rid}-lugar.jpg")
            if pendientes:
                n = sum(1 for c in pendientes for t in c if not _clip(rid, t["id"]).exists())
                await _job_set(jid, {"estado": "generando",
                                     "paso": f"Filmando {n} toma{'s' if n > 1 else ''} "
                                             f"(de a {PARALELO}; las que siguen sin cortar, una detrás de otra)…"})
                await asyncio.gather(*(cadena(c, u_ref) for c in pendientes))
        reel = await _reel(rid)
        clips = [_clip(rid, t["id"]) for t in reel["tomas"]]
        if not all(c.exists() for c in clips):
            _final(rid).unlink(missing_ok=True)
            raise RuntimeError("Algunas tomas no salieron: " + " · ".join(fallas)
                               + ". Las que sí salieron quedaron guardadas: tocá 'Filmar lo que falta'.")
        await _job_set(jid, {"paso": "Uniendo las tomas y pasándole el filtro…"})
        await asyncio.to_thread(_unir, clips, _final(rid), reel.get("look", LOOK_DEFAULT), reel.get("camara", "motor"),
                                [(t.get("enlace") or "corte") if k else "corte" for k, t in enumerate(reel["tomas"])])
        link = await _guardar_en_drive(f"{_slug(doc.get('nombre', ''))}-reel-{rid}.mp4",
                                       _final(rid).read_bytes(), "video/mp4")
        async with _lock(rid):
            reel = await _reel(rid)
            reel["drive"] = link
            reel["seg_final"] = round(_duracion_video(_final(rid)), 1)
            reel["ts"] = time.strftime("%Y-%m-%d %H:%M")
            await _guardar(reel)
        await _job_set(jid, {"estado": "listo", "paso": "", "drive": link})
    except Exception as e:
        await _job_set(jid, {"estado": "error", "error": str(getattr(e, "detail", "") or e)[:900]})
    finally:
        parar.set()


# ── API ──────────────────────────────────────────────────────────────────────

router = APIRouter(dependencies=[Depends(_bind)])


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(PAGINA.replace("%%API%%", API).replace("%%VERSION%%", VERSION))


@router.get(API + "/config")
async def api_config() -> Dict[str, Any]:
    return {"motores": {k: {"label": v["label"], "precio_seg": v["precio_seg"]} for k, v in MOTORES.items()},
            "motor_default": MOTOR_DEFAULT, "duraciones": list(DURACIONES),
            "modos": MODOS, "modo_default": MODO_DEFAULT,
            "planos": {k: v[0] for k, v in PLANOS.items()}, "movimientos": {k: v[0] for k, v in MOVIMIENTOS.items()},
            "enlaces": {k: v[0] for k, v in ENLACES.items()}, "max_variantes": MAX_VARIANTES,
            "lugares": {k: v[0] for k, v in LUGARES.items()}, "voces": VOCES, "tonos": list(TONOS),
            "energias": {k: {"nombre": v["nombre"], "palabras_seg": v["palabras_seg"]} for k, v in ENERGIAS_VOZ.items()},
            "energia_default": ENERGIA_FILMADO, "claude": _claude.disponible(),
            "looks": {k: v for k, v in LOOKS.items() if k in _FILTRO_LOOK or k == "limpio"},
            "look_default": LOOK_DEFAULT, "camaras": CAMARAS, "tipos": TIPOS,
            "mostrar": {k: v[0] for k, v in MOSTRAR.items()}, "mostrar_default": list(MOSTRAR_DEFAULT),
            "costo_plan": COSTO_PLAN, "fal_key": bool(await _fal_key())}


def _ajustes(payload: Dict[str, Any], reel: Dict[str, Any]) -> None:
    """Los ajustes del reel que vienen de la pantalla (sólo lo que llegó)."""
    voces_ok = {v for lst in VOCES.values() for v, _ in lst}
    elegir = {"motor": MOTORES, "modo": MODOS, "lugar": LUGARES, "tono": TONOS, "energia": ENERGIAS_VOZ,
              "look": LOOKS, "camara": CAMARAS}
    for k, validos in elegir.items():
        if payload.get(k) in validos:
            reel[k] = payload[k]
    if "voz" in payload:
        reel["voz"] = payload["voz"] if payload["voz"] in voces_ok else ""
    for k in ("puesta", "mic"):
        if k in payload:
            reel[k] = bool(payload[k])
    if "duracion" in payload:
        try:
            reel["duracion"] = int(payload["duracion"]) if int(payload["duracion"]) in DURACIONES else 20
        except (TypeError, ValueError):
            pass
    if "mostrar" in payload:
        reel["mostrar"] = [k for k in (payload.get("mostrar") or []) if k in MOSTRAR]
    if isinstance(payload.get("info"), dict):
        reel["info"] = {k: _texto(payload["info"].get(k), 300 if k != "notas" else 800)
                        for k in ("producto", "precio", "talles", "colores", "promo", "notas")}


@router.post(API + "/reel")
async def api_nuevo(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Arranca un reel: guarda la prenda y lo pedido, y Claude pregunta."""
    doc = await _doc(str(payload.get("pid") or ""))
    if not (doc.get("hoja") or {}).get("retrato"):
        raise HTTPException(400, "Ese personaje todavía no tiene retrato aprobado.")
    # Uno o varios colores de la prenda, cada uno con hasta 3 fotos (frente, espalda, detalle).
    crudas = payload.get("variantes")
    if not isinstance(crudas, list) or not crudas:
        crudas = [{"nombre": "", "fotos": payload.get("prendas") or []}]
    prendas: List[List[str]] = []
    variantes: List[Dict[str, Any]] = []
    for var in crudas[:MAX_VARIANTES]:
        if not isinstance(var, dict):
            continue
        fotos = []
        for a in (var.get("fotos") or [])[:3]:
            try:
                fotos.append(_compress_ref(base64.b64decode(_strip_data_url(str(a))), max_dim=1536, q=92))
            except Exception:
                raise HTTPException(400, "No pude leer una foto de la prenda.")
        if fotos:
            prendas.append(fotos)
            variantes.append({"nombre": _texto(var.get("nombre"), 40), "n": len(fotos)})
    if not prendas:
        raise HTTPException(400, "Subí al menos una foto de la prenda (mejor frente y espalda).")
    rid = "r" + _uuid.uuid4().hex[:9]
    reel: Dict[str, Any] = {"id": rid, "pid": doc["id"], "variantes": variantes, "motor": MOTOR_DEFAULT,
                            "modo": MODO_DEFAULT, "lugar": "dormitorio", "puesta": True, "voz": "",
                            "tono": "cercana", "energia": ENERGIA_FILMADO, "mic": True, "look": LOOK_DEFAULT,
                            "camara": "motor", "duracion": 20, "mostrar": list(MOSTRAR_DEFAULT), "info": {},
                            "preguntas": [], "resumen": "", "analisis": None, "concepto": "", "continuidad": "", "tomas": [], "titulo": "", "costo": 0.0,
                            "creado": time.strftime("%Y-%m-%d %H:%M")}
    _ajustes(payload, reel)
    for v, fotos in enumerate(prendas):
        for i, b in enumerate(fotos):
            await kv.set(_k_prenda(rid, i, v), b)
    aviso = ""
    if _claude.disponible():
        try:
            q, c = await claude_preguntas(reel, prendas)
            reel.update(q)
            reel["costo"] = round(c, 2)
            await budget_record("filmado_claude", _claude.MODELO, c, 1, note="filmado: preguntas")
        except _claude.ClaudeNoDisponible as e:
            aviso = f"Claude no pudo preguntar ({e}): armá el plan directo."
    else:
        aviso = "Claude no está disponible (falta ANTHROPIC_API_KEY): el plan sale de una plantilla."
    await _guardar(reel)
    lista = (await kv.get(_k_reels())) or []
    await kv.set(_k_reels(), ([rid] + [x for x in lista if x != rid])[:30])
    return {"reel": _vista(reel), "aviso": aviso}


@router.get(API + "/reel/{rid}")
async def api_reel(rid: str) -> Dict[str, Any]:
    return {"reel": _vista(await _reel(rid))}


@router.post(API + "/reel/{rid}/plan")
async def api_plan(rid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Con las respuestas, Claude arma las tomas (reemplaza el plan anterior: sólo si lo pedís)."""
    async with _lock(rid):
        reel = await _reel(rid)
        _ajustes(payload, reel)
        for i, r in enumerate(payload.get("respuestas") or []):
            if i < len(reel.get("preguntas") or []):
                reel["preguntas"][i]["respuesta"] = _texto(r, 400)
        aviso = ""
        viejas = reel.get("tomas") or []
        if _claude.disponible():
            try:
                plan, c = await claude_plan(reel, await _prendas(rid, reel))
                reel.update(plan)
                reel["costo"] = round(float(reel.get("costo") or 0) + c, 2)
                await budget_record("filmado_claude", _claude.MODELO, c, 1, note="filmado: plan")
            except _claude.ClaudeNoDisponible as e:
                aviso = f"Claude no pudo armar el plan ({e}): va una plantilla que podés editar."
                reel.update({"tomas": _plan_sin_claude(reel), "concepto": "", "continuidad": ""})
        else:
            reel.update({"tomas": _plan_sin_claude(reel), "concepto": "", "continuidad": ""})
        for t in viejas:
            _clip(rid, t["id"]).unlink(missing_ok=True)
        await kv.delete(_k_ref(rid))
        _final(rid).unlink(missing_ok=True)
        await _guardar(reel)
    return {"reel": _vista(reel), "aviso": aviso}


@router.put(API + "/reel/{rid}")
async def api_ajustes(rid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Cambia ajustes del reel. Voz, lip-sync o puesta cambian lo filmado: esas tomas se rehacen."""
    async with _lock(rid):
        reel = await _reel(rid)
        antes = {k: reel.get(k) for k in ("voz", "tono", "energia", "mic", "motor", "lugar", "puesta", "modo")}
        _ajustes(payload, reel)
        if reel.get("modo") == "fondo":
            for t in reel.get("tomas") or []:
                if t.get("tipo") == "habla":
                    t["tipo"] = "muestra"
        if any(reel.get(k) != v for k, v in antes.items()):
            if any(reel.get(k) != antes[k] for k in ("lugar", "puesta", "motor")):
                await kv.delete(_k_ref(rid))
            for t in reel.get("tomas") or []:
                _clip(rid, t["id"]).unlink(missing_ok=True)
        _final(rid).unlink(missing_ok=True)
        await _guardar(reel)
    return {"reel": _vista(reel)}


def _invalidar(reel: Dict[str, Any], i: int) -> None:
    """Borra lo filmado de la toma i y de las que la siguen SIN CORTAR (arrancan en su último
    cuadro: si ella cambia, ellas también)."""
    tomas = reel.get("tomas") or []
    rid = reel["id"]
    while 0 <= i < len(tomas):
        _clip(rid, tomas[i]["id"]).unlink(missing_ok=True)
        tomas[i]["revision"], tomas[i]["lipsync"], tomas[i]["error"] = None, "", ""
        i += 1
        if i >= len(tomas) or tomas[i].get("enlace") != "sigue":
            break
    _final(rid).unlink(missing_ok=True)


def _fin(tomas: List[Dict[str, Any]], i: int) -> str:
    """Cómo termina la toma i: lo dice el enlace de la que viene después."""
    return (tomas[i + 1].get("enlace") or "corte") if i + 1 < len(tomas) else "corte"


@router.put(API + "/reel/{rid}/toma/{tid}")
async def api_toma(rid: str, tid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Lo que escribís en una toma va TAL CUAL. Si cambia, esa toma se vuelve a filmar."""
    async with _lock(rid):
        reel = await _reel(rid)
        t = next((x for x in reel.get("tomas") or [] if x["id"] == tid), None)
        if not t:
            raise HTTPException(404, "Esa toma no existe.")
        cambio = False
        if payload.get("tipo") in TIPOS and payload["tipo"] != t["tipo"]:
            if payload["tipo"] == "habla" and reel.get("modo", MODO_DEFAULT) == "fondo":
                raise HTTPException(400, "En modo 'voz de fondo' ella no habla a cámara: cambiá el modo arriba.")
            t["tipo"], cambio = payload["tipo"], True
        for k, validos in (("plano", PLANOS), ("movimiento", MOVIMIENTOS)):
            if payload.get(k) in validos and payload[k] != t.get(k):
                t[k], cambio = payload[k], True
        if "dice" in payload and _texto(payload["dice"], 600) != t.get("dice"):
            t["dice"], cambio = _texto(payload["dice"], 600), True
        if "accion" in payload and _texto(payload["accion"], 400) != t.get("accion"):
            # La escribiste vos: la toma se arma con TU acción (traducida tal cual), no con la de Claude.
            t["accion"], t["toma"], cambio = _texto(payload["accion"], 400), "", True
        if "seg" in payload:
            try:
                s = max(SEG_CORTE_MIN, min(10, float(payload["seg"])))
            except (TypeError, ValueError):
                s = SEG_MUESTRA
            if s != t.get("seg"):
                t["seg"], cambio = s, True
        if "variante" in payload:
            try:
                v = max(0, min(len(_variantes(reel)) - 1, int(payload["variante"])))
            except (TypeError, ValueError):
                v = 0
            if v != t.get("variante", 0):
                t["variante"], cambio = v, True
        i = reel["tomas"].index(t)
        if payload.get("enlace") in ENLACES and payload["enlace"] != (t.get("enlace") or "corte") and i > 0:
            # Cambia cómo entra ésta Y cómo termina la anterior (la mano que tapa la cámara).
            t["enlace"], cambio = payload["enlace"], True
            _invalidar(reel, i - 1)
        if cambio:
            _invalidar(reel, i)
        await _guardar(reel)
    return {"reel": _vista(reel)}


@router.post(API + "/reel/{rid}/toma")
async def api_toma_nueva(rid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    async with _lock(rid):
        reel = await _reel(rid)
        if len(reel.get("tomas") or []) >= MAX_TOMAS:
            raise HTTPException(400, f"Hasta {MAX_TOMAS} tomas por reel.")
        t = _limpiar_toma(reel, {"tipo": payload.get("tipo") or "muestra", "dice": payload.get("dice"),
                                 "plano": payload.get("plano") or "medio", "movimiento": payload.get("movimiento") or "mano",
                                 "accion": payload.get("accion") or "Escribí acá qué hace.", "seg": SEG_MUESTRA})
        pos = payload.get("despues")
        ids = [x["id"] for x in reel["tomas"]]
        k = ids.index(pos) + 1 if pos in ids else len(ids)
        if k > 0 and _fin(reel["tomas"], k - 1) != "corte":
            _invalidar(reel, k - 1)          # la anterior ya no termina igual
        reel["tomas"].insert(k, t)
        if k + 1 < len(reel["tomas"]) and reel["tomas"][k + 1].get("enlace") == "sigue":
            _invalidar(reel, k + 1)          # ya no sigue a la misma toma
        _final(rid).unlink(missing_ok=True)
        await _guardar(reel)
    return {"reel": _vista(reel)}


@router.delete(API + "/reel/{rid}/toma/{tid}")
async def api_toma_borrar(rid: str, tid: str) -> Dict[str, Any]:
    async with _lock(rid):
        reel = await _reel(rid)
        tomas = reel.get("tomas") or []
        k = next((i for i, x in enumerate(tomas) if x["id"] == tid), -1)
        if k >= 0:
            fin_antes = _fin(tomas, k - 1) if k > 0 else "corte"
            tomas.pop(k)
            if k > 0 and _fin(tomas, k - 1) != fin_antes:
                _invalidar(reel, k - 1)
            if k < len(tomas) and tomas[k].get("enlace") == "sigue":
                _invalidar(reel, k)
        _clip(rid, tid).unlink(missing_ok=True)
        _final(rid).unlink(missing_ok=True)
        await _guardar(reel)
    return {"reel": _vista(reel)}


@router.post(API + "/reel/{rid}/toma/{tid}/rehacer")
async def api_rehacer(rid: str, tid: str) -> Dict[str, Any]:
    """Borra lo filmado de ESA toma (las demás quedan) y la vuelve a filmar."""
    async with _lock(rid):
        reel = await _reel(rid)
        t = next((x for x in reel.get("tomas") or [] if x["id"] == tid), None)
        if not t:
            raise HTTPException(404, "Esa toma no existe.")
        _invalidar(reel, reel["tomas"].index(t))
        await _guardar(reel)
    return await api_filmar(rid)


@router.post(API + "/reel/{rid}/filmar")
async def api_filmar(rid: str) -> Dict[str, Any]:
    """Filma las tomas que faltan (las ya filmadas no se vuelven a pagar) y une el reel."""
    reel = await _reel(rid)
    if not reel.get("tomas"):
        raise HTTPException(400, "Primero armá el plan.")
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal (FAL_KEY en Railway).")
    avisos = [f"Toma {i + 1}: {_aviso_toma(reel, t)}" for i, t in enumerate(reel["tomas"]) if _aviso_toma(reel, t)]
    if avisos:
        raise HTTPException(400, " · ".join(avisos))
    vacias = [str(i + 1) for i, t in enumerate(reel["tomas"]) if not (t.get("toma") or t.get("accion"))]
    if vacias:
        raise HTTPException(400, f"Falta qué hace en la toma {', '.join(vacias)}.")
    v = _vista(reel)
    await _cobrar(v["costo_falta"])
    jid = _uuid.uuid4().hex[:10]
    faltan = sum(1 for t in v["tomas"] if not t["filmada"])
    await _job_nuevo(jid, reel["pid"], "filmado", 240 * max(1, math.ceil(faltan / PARALELO)) + 60,
                     {"costo": v["costo_falta"], "titulo": "Reel filmado de cero", "rid": rid})
    async with _lock(rid):
        reel = await _reel(rid)
        reel["job"] = jid
        await _guardar(reel)
    _spawn(_procesar(jid, rid, CURRENT_SUB.get()))
    return {"job": jid, "costo": v["costo_falta"], "tomas": faltan}


@router.get(API + "/job/{jid}")
async def api_job(jid: str) -> Dict[str, Any]:
    j = await kv.get(_k_job(jid))
    if not isinstance(j, dict):
        raise HTTPException(404, "Ese trabajo no existe.")
    j = await _revisar_job(j)
    return {k: j.get(k) for k in ("id", "estado", "paso", "error", "costo", "drive", "rid")}


@router.get(API + "/reels")
async def api_reels() -> Dict[str, Any]:
    out = []
    for rid in (await kv.get(_k_reels())) or []:
        r = await kv.get(_k_reel(rid))
        if isinstance(r, dict):
            out.append({"id": rid, "titulo": r.get("titulo") or (r.get("info") or {}).get("producto") or "Reel",
                        "tomas": len(r.get("tomas") or []), "listo": _final(rid).exists(),
                        "costo": r.get("costo"), "creado": r.get("creado"), "ts": r.get("ts")})
    return {"reels": out}


@router.get(API + "/reel/{rid}/mp4")
async def api_mp4(rid: str):
    await _reel(rid)
    if not _final(rid).exists():
        raise HTTPException(404, "Ese reel todavía no está filmado.")
    return FileResponse(str(_final(rid)), media_type="video/mp4", filename=f"reel-{rid}.mp4")


@router.get(API + "/reel/{rid}/toma/{tid}/mp4")
async def api_toma_mp4(rid: str, tid: str):
    reel = await _reel(rid)
    if not any(t["id"] == tid for t in reel.get("tomas") or []) or not _clip(rid, tid).exists():
        raise HTTPException(404, "Esa toma todavía no está filmada.")
    return FileResponse(str(_clip(rid, tid)), media_type="video/mp4", filename=f"toma-{tid}.mp4")


PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Filmado de cero · Studio Luma</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;--rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f}
  *{box-sizing:border-box} body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:16px;line-height:1.55}
  main{max-width:1080px;margin:0 auto;padding:16px} .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:16px}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:22px;margin:0 0 6px} h3{font-size:15px;margin:0;font-weight:500} .hint{color:var(--ink-soft);font-size:13px;margin:4px 0 8px}
  label{display:block;font-size:13px;color:var(--ink-soft);margin:10px 0 4px}
  input,select,textarea{width:100%;background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit;font-size:15px}
  input[type=checkbox]{width:auto;margin-right:8px} .chk{display:flex;align-items:center;font-size:14px;color:var(--ink);margin:6px 0}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:9px 16px;font:inherit;font-size:14px;cursor:pointer}
  button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500} button:disabled{opacity:.5} button.chip{padding:5px 12px;font-size:13px;margin:4px 6px 0 0}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px} .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px} @media(max-width:640px){.row,.row3{grid-template-columns:1fr}}
  video{width:100%;max-width:360px;border-radius:14px;background:#000} .thumbs img{width:64px;height:64px;object-fit:cover;border-radius:8px;margin:6px 6px 0 0}
  .toma{border:1px solid var(--line);border-radius:14px;padding:12px;margin:10px 0;background:var(--card-2)} .toma textarea,.toma select,.toma input{background:var(--card)}
  .toma .cab{display:flex;gap:10px;align-items:center;flex-wrap:wrap} .toma .cab select{width:auto} .toma video{max-width:220px;margin-top:8px}
  .mal{color:var(--bad)} .bien{color:var(--ok)} .preg{margin:12px 0} details summary{cursor:pointer;color:var(--ink-soft);font-size:14px;margin-top:10px}
  .vids{display:flex;gap:10px;flex-wrap:wrap}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid var(--ink-soft);border-top-color:var(--rose);border-radius:50%;animation:g 1s linear infinite;vertical-align:-1px;margin-right:6px}@keyframes g{to{transform:rotate(360deg)}}
</style></head><body><main>
<div class="card">
  <h2>Reel filmado de cero</h2>
  <p class="hint">Un reel completo de ella <b>filmado desde cero</b>, toma por toma: contás qué querés, <b>Claude entiende qué vendemos, te pregunta</b> lo que le falta y arma el plan como un director (planos, cámara, ritmo: que gire, la bombacha, el encaje de cerca, el espejo…), vos lo editás, y se filma cada toma con <b>su voz de Reels</b>. La primera toma marca el cuarto, la luz y el peinado para todas las demás. Las tomas que no gusten se rehacen solas, sin pagar las demás.</p>
  <p class="hint mal" id="aviso_claude" style="display:none">Claude no está disponible (falta ANTHROPIC_API_KEY): el plan sale de una plantilla que podés editar.</p>
  <h3>1 · Tu reel</h3>
  <label>Cómo cuenta el reel</label><select id="modo"></select>
  <div class="row"><div><label>Modelo (personaje)</label><select id="pid"></select></div>
  <div><label>Duración del reel</label><select id="duracion"></select></div></div>
  <label>La prenda: hasta 3 fotos por color (frente, espalda y detalle; con la espalda, cuando gira la copia bien). Con más de un color, Claude arma cambios de color tapando la cámara.</label>
  <div id="variantes"></div><p><button id="otro_color">＋ Otro color</button></p>
  <div class="row"><div><label>La prenda</label><select id="puesta"><option value="si">La tiene puesta</option><option value="no">La muestra en la mano (vestida de entrecasa)</option></select></div>
  <div><label>Dónde</label><select id="lugar"></select></div></div>
  <label>Qué querés mostrar</label><div id="mostrar"></div>
  <div class="row3"><div><label>Producto</label><input id="i_producto" placeholder="Conjunto Encaje Rojo"></div><div><label>Precio</label><input id="i_precio" placeholder="$ 25.000"></div><div><label>Talles</label><input id="i_talles" placeholder="S a XL"></div></div>
  <div class="row"><div><label>Colores</label><input id="i_colores" placeholder="rojo, negro, nude"></div><div><label>Promo</label><input id="i_promo" placeholder="envío gratis, 3 cuotas…"></div></div>
  <label>Algo más que quieras que sepa Claude</label><textarea id="i_notas" rows="2"></textarea>
  <details><summary>Voz, filtro y motor</summary>
  <div class="row3"><div><label>Voz</label><select id="voz"></select></div><div><label>Tono</label><select id="tono"></select></div><div><label>Energía</label><select id="energia"></select></div></div>
  <div class="row3"><div><label>Aire de micrófono</label><select id="mic"><option value="si">Sí (suena a celular)</option><option value="no">No, voz limpia</option></select></div>
  <div><label>Motor</label><select id="motor"></select></div><div></div></div>
  <div class="row"><div><label>Filtro (los de Reels)</label><select id="look"></select></div><div><label>Cámara</label><select id="camara"></select></div></div>
  </details>
  <p style="margin-top:14px"><button class="go" id="empezar">💬 Que Claude me pregunte</button></p>
  <p class="hint" id="estado1"></p>
</div>
<div class="card" id="c_preg" style="display:none"><h3>2 · Claude te pregunta</h3><div id="analisis" class="hint"></div><p class="hint" id="resumen"></p><div id="preguntas"></div>
  <p><button class="go" id="plan">🎬 Armar el plan</button></p><p class="hint" id="estado2"></p></div>
<div class="card" id="c_plan" style="display:none"><h3>3 · El plan <span id="titulo" class="hint"></span></h3>
  <p id="concepto"></p><p class="hint" id="continuidad"></p>
  <p class="hint">Todo se puede editar: lo que escribís va tal cual. Si cambiás una toma ya filmada, esa se vuelve a filmar (las demás no).</p>
  <div id="tomas"></div><p><button id="agregar">＋ Agregar una toma al final</button></p>
  <p class="hint" id="totales"></p><button class="go" id="filmar">🎥 Filmar el reel</button><p class="hint" id="estado3"></p></div>
<div class="card" id="c_final" style="display:none"><h3>El reel</h3><div id="final"></div></div>
<div class="card"><h2>Reels anteriores</h2><div id="lista"></div></div>
</main>
<script>
const API = "%%API%%"; let CFG = {}, VARS = [{nombre: "", fotos: []}], REEL = null, SIGUIENDO = null;
const $ = s => document.querySelector(s);
const esc = t => String(t == null ? "" : t).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
async function api(p, o){ const r = await fetch(API + p, Object.assign({headers: {"Content-Type": "application/json"}}, o || {})); const d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.detail || ("HTTP " + r.status)); return d; }
const leer = f => new Promise((ok, mal) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = mal; r.readAsDataURL(f); });
const guardarRid = rid => { try{ localStorage.setItem("filmado_rid", rid); }catch(e){} };
function ajustes(){ return {pid: $("#pid").value, duracion: +$("#duracion").value, puesta: $("#puesta").value === "si", lugar: $("#lugar").value,
  mostrar: Array.from(document.querySelectorAll("#mostrar input:checked")).map(x => x.value),
  info: {producto: $("#i_producto").value, precio: $("#i_precio").value, talles: $("#i_talles").value, colores: $("#i_colores").value, promo: $("#i_promo").value, notas: $("#i_notas").value},
  voz: $("#voz").value, tono: $("#tono").value, energia: $("#energia").value, mic: $("#mic").value === "si", modo: $("#modo").value, motor: $("#motor").value, look: $("#look").value, camara: $("#camara").value}; }
function cargarAjustes(r){ const set = (k, v) => { if(v != null && $(k)) $(k).value = v; };
  set("#duracion", r.duracion); set("#puesta", r.puesta ? "si" : "no"); set("#lugar", r.lugar); set("#voz", r.voz); set("#tono", r.tono); set("#energia", r.energia);
  set("#mic", r.mic === false ? "no" : "si"); set("#modo", r.modo); set("#motor", r.motor); set("#look", r.look); set("#camara", r.camara); set("#pid", r.pid);
  const inf = r.info || {}; ["producto", "precio", "talles", "colores", "promo", "notas"].forEach(k => set("#i_" + k, inf[k] || ""));
  document.querySelectorAll("#mostrar input").forEach(x => x.checked = (r.mostrar || []).includes(x.value)); }
function revision(t){ let h = "";
  if(t.revision){ const f = (t.revision.fallas || []).map(x => `<li>${esc(x)}</li>`).join("");
    h += `<div class="${t.revision.puntaje >= 8 ? "bien" : ""}"><b>Claude: ${esc(t.revision.puntaje)}/10</b>${f ? `<ul style="margin:4px 0 0 18px;padding:0">${f}</ul>` : " · no vio fallas"}</div>`; }
  if(t.lipsync && t.lipsync !== "ok") h += `<div class="mal">Lip-sync ${esc(t.lipsync)}</div>`;
  if(t.error && !t.filmada) h += `<div class="mal">Falló: ${esc(t.error)}</div>`;
  return h; }
function pintar(){ const r = REEL; if(!r) return; $("#empezar").textContent = "💬 Empezar un reel nuevo (Claude pregunta de nuevo)";
  $("#c_preg").style.display = (r.preguntas || []).length ? "" : "none";
  $("#resumen").textContent = r.resumen ? "Lo que ve Claude: " + r.resumen : "";
  const a = r.analisis; $("#analisis").innerHTML = a ? `<b>Qué vendemos:</b> ${esc(a.que_es)}${(a.puntos_fuertes || []).length ? `<br><b>Lo que la vende:</b> ${a.puntos_fuertes.map(esc).join(" · ")}` : ""}${a.para_quien ? `<br><b>Para quién:</b> ${esc(a.para_quien)}` : ""}` : "";
  $("#concepto").innerHTML = r.concepto ? `<b>La idea:</b> ${esc(r.concepto)}` : ""; $("#continuidad").textContent = r.continuidad ? "Igual en todas las tomas: " + r.continuidad : "";
  $("#preguntas").innerHTML = (r.preguntas || []).map((q, i) => `<div class="preg"><b>${esc(q.pregunta)}</b><div>${(q.opciones || []).map(o => `<button class="chip" data-q="${i}" data-o="${esc(o)}">${esc(o)}</button>`).join("")}</div><input class="resp" data-q="${i}" value="${esc(q.respuesta || "")}" placeholder="Tu respuesta (o tocá una opción)"></div>`).join("");
  document.querySelectorAll(".chip").forEach(b => b.onclick = () => { document.querySelector(`.resp[data-q="${b.dataset.q}"]`).value = b.dataset.o; });
  $("#plan").textContent = (r.tomas || []).length ? "🎬 Rearmar el plan (reemplaza las tomas)" : "🎬 Armar el plan";
  $("#c_plan").style.display = (r.tomas || []).length ? "" : "none"; $("#titulo").textContent = r.titulo ? "· " + r.titulo : "";
  const tipos = Object.fromEntries(Object.entries(CFG.tipos).filter(([k]) => k !== "habla" || r.modo === "habla"));
  const colores = (r.variantes || [{nombre: ""}]).map((v, k) => v.nombre || `Color ${k + 1}`);
  const sel = (k, obj, v) => `<select data-k="${k}">${Object.entries(obj).map(([kk, vv]) => `<option value="${kk}" ${kk === v ? "selected" : ""}>${esc(vv)}</option>`).join("")}</select>`;
  $("#tomas").innerHTML = (r.tomas || []).map((t, i) => `<div class="toma" data-t="${t.id}">
    <div class="cab"><h3>Toma ${i + 1}</h3>${sel("tipo", tipos, t.tipo)}
    <span class="hint">~${t.seg_est} s · US$${t.costo_est}${t.filmada ? ' · <span class="bien">filmada</span>' : ""}</span></div>
    <div class="row"><div><label>Plano</label>${sel("plano", CFG.planos, t.plano)}</div><div><label>Cámara</label>${sel("movimiento", CFG.movimientos, t.movimiento)}</div></div>
    <div class="row">${i ? `<div><label>Cómo entra desde la anterior</label>${sel("enlace", CFG.enlaces, t.enlace || "corte")}</div>` : ""}${colores.length > 1 ? `<div><label>Color</label>${sel("variante", Object.fromEntries(colores.map((c, k) => [String(k), c])), String(t.variante || 0))}</div>` : ""}</div>
    <label>${t.tipo === "habla" ? "Qué dice a cámara" : "Su voz de fondo (vacío = sin voz)"}</label><textarea data-k="dice" rows="2">${esc(t.dice)}</textarea>${t.aviso ? `<div class="mal hint">${esc(t.aviso)}</div>` : ""}
    <label>Qué hace</label><textarea data-k="accion" rows="2">${esc(t.accion)}</textarea>
    ${!t.dice ? `<label>Segundos</label><input data-k="seg" type="number" min="2" max="10" step="0.5" value="${t.seg || 4}" style="width:90px">` : ""}
    ${t.filmada ? `<div><video src="${API}/reel/${r.id}/toma/${t.id}/mp4?v=${encodeURIComponent(t.clip_seg || "")}${Date.now()}" controls playsinline preload="metadata"></video></div>` : ""}
    ${revision(t)}
    <p style="margin:8px 0 0">${t.filmada ? `<button data-a="rehacer">↻ Rehacer esta toma (US$${t.costo_est})</button> ` : ""}<button data-a="despues">＋ Toma después</button> <button data-a="borrar">🗑</button></p></div>`).join("");
  document.querySelectorAll(".toma").forEach(el => { const tid = el.dataset.t;
    el.querySelectorAll("[data-k]").forEach(c => c.onchange = async () => { try{ const b = {}; b[c.dataset.k] = (c.dataset.k === "seg" || c.dataset.k === "variante") ? +c.value : c.value; REEL = (await api(`/reel/${REEL.id}/toma/${tid}`, {method: "PUT", body: JSON.stringify(b)})).reel; pintar(); }catch(e){ $("#estado3").textContent = "Falló: " + e.message; } });
    el.querySelectorAll("[data-a]").forEach(b => b.onclick = async () => { try{
      if(b.dataset.a === "borrar"){ if(!confirm("¿Borrar esta toma?")) return; REEL = (await api(`/reel/${REEL.id}/toma/${tid}`, {method: "DELETE"})).reel; pintar(); }
      if(b.dataset.a === "despues"){ REEL = (await api(`/reel/${REEL.id}/toma`, {method: "POST", body: JSON.stringify({despues: tid})})).reel; pintar(); }
      if(b.dataset.a === "rehacer"){ const d = await api(`/reel/${REEL.id}/toma/${tid}/rehacer`, {method: "POST"}); seguir(d.job); }
    }catch(e){ $("#estado3").textContent = "Falló: " + e.message; } }); });
  const falta = (r.tomas || []).filter(t => !t.filmada).length;
  $("#totales").textContent = `${(r.tomas || []).length} tomas · ~${r.seg_total} s en total · ` + (falta ? `filmar ${falta === r.tomas.length ? "todo" : "lo que falta"} cuesta ~US$${r.costo_falta}` : "todas filmadas") + (r.costo ? ` · gastado hasta ahora: US$${r.costo}` : "");
  $("#filmar").textContent = falta === (r.tomas || []).length ? `🎥 Filmar el reel (~US$${r.costo_falta})` : falta ? `🎥 Filmar lo que falta (~US$${r.costo_falta})` : "🎞 Volver a unir el reel";
  $("#c_final").style.display = r.listo ? "" : "none";
  if(r.listo) $("#final").innerHTML = `<video src="${API}/reel/${r.id}/mp4?v=${Date.now()}" controls playsinline></video><p><a href="${API}/reel/${r.id}/mp4" download style="color:var(--rose)">⬇ Bajar el reel</a>${r.drive ? ` · <a href="${esc(r.drive)}" target="_blank" style="color:var(--rose)">Drive</a>` : ""}${r.seg_final ? ` · ${r.seg_final} s` : ""}</p>`;
}
async function seguir(jid){ if(SIGUIENDO === jid) return; SIGUIENDO = jid; $("#filmar").disabled = true;
  try{ for(;;){ const j = await api("/job/" + jid);
      if(j.estado === "listo"){ $("#estado3").textContent = "Listo."; break; }
      if(j.estado === "error"){ $("#estado3").innerHTML = `<span class="mal">${esc(j.error || "Falló")}</span>`; break; }
      $("#estado3").innerHTML = `<span class="spin"></span>${esc(j.paso || "En cola…")}`;
      if(REEL){ try{ REEL = (await api("/reel/" + REEL.id)).reel; pintar(); }catch(e){} }
      await new Promise(res => setTimeout(res, 6000)); } }
  catch(e){ $("#estado3").textContent = "Falló: " + e.message; }
  SIGUIENDO = null; $("#filmar").disabled = false; if(REEL){ REEL = (await api("/reel/" + REEL.id)).reel; pintar(); } lista(); }
async function abrir(rid){ REEL = (await api("/reel/" + rid)).reel; guardarRid(rid); cargarAjustes(REEL); pintar();
  if(REEL.job){ try{ const j = await api("/job/" + REEL.job); if(j.estado === "generando" || j.estado === "en_cola") seguir(REEL.job); }catch(e){} } }
async function lista(){ const l = (await api("/reels")).reels;
  $("#lista").innerHTML = l.length ? l.map(v => `<p><button data-r="${v.id}">Abrir</button> ${esc(v.titulo)} · ${v.tomas} tomas${v.listo ? ' · <span class="bien">filmado</span>' : ""}${v.costo ? " · US$" + esc(v.costo) : ""} · <span class="hint">${esc(v.ts || v.creado)}</span></p>`).join("") : '<span class="hint">Todavía no hiciste ninguno.</span>';
  document.querySelectorAll("#lista [data-r]").forEach(b => b.onclick = () => abrir(b.dataset.r).then(() => window.scrollTo(0, 0))); }
function pintarVars(){ $("#variantes").innerHTML = VARS.map((v, i) => `<div class="toma"><div class="row"><div><label>Color ${i + 1}</label><input data-vn="${i}" value="${esc(v.nombre)}" placeholder="rojo, negro, nude…"></div>
    <div><label>Fotos</label><input type="file" data-vf="${i}" accept="image/*" multiple></div></div><div class="thumbs">${v.fotos.map(s => `<img src="${s}">`).join("")}</div>
    ${i ? `<button data-vx="${i}">Quitar este color</button>` : ""}</div>`).join("");
  document.querySelectorAll("[data-vn]").forEach(x => x.oninput = () => { VARS[+x.dataset.vn].nombre = x.value; });
  document.querySelectorAll("[data-vf]").forEach(x => x.onchange = async e => { VARS[+x.dataset.vf].fotos = await Promise.all(Array.from(e.target.files).slice(0, 3).map(leer)); pintarVars(); });
  document.querySelectorAll("[data-vx]").forEach(x => x.onclick = () => { VARS.splice(+x.dataset.vx, 1); pintarVars(); });
  $("#otro_color").style.display = VARS.length < (CFG.max_variantes || 5) ? "" : "none"; }
$("#otro_color").onclick = () => { VARS.push({nombre: "", fotos: []}); pintarVars(); };
$("#empezar").onclick = async () => { const b = $("#empezar"); b.disabled = true; $("#estado1").innerHTML = '<span class="spin"></span>Claude está mirando la prenda…';
  try{ const d = await api("/reel", {method: "POST", body: JSON.stringify(Object.assign(ajustes(), {variantes: VARS.filter(v => v.fotos.length)}))}); REEL = d.reel; guardarRid(REEL.id);
    $("#estado1").textContent = d.aviso || ""; pintar(); if(!(REEL.preguntas || []).length) $("#plan").click(); else $("#c_preg").scrollIntoView({behavior: "smooth"}); }
  catch(e){ $("#estado1").textContent = "Falló: " + e.message; } b.disabled = false; };
$("#plan").onclick = async () => { if(!REEL) return; if((REEL.tomas || []).length && !confirm("¿Rearmar el plan? Se reemplazan las tomas (y lo filmado).")) return;
  const b = $("#plan"); b.disabled = true; $("#estado2").innerHTML = '<span class="spin"></span>Claude está armando el plan…';
  try{ const d = await api(`/reel/${REEL.id}/plan`, {method: "POST", body: JSON.stringify(Object.assign(ajustes(), {respuestas: Array.from(document.querySelectorAll(".resp")).map(x => x.value)}))});
    REEL = d.reel; $("#estado2").textContent = d.aviso || ""; pintar(); $("#c_plan").scrollIntoView({behavior: "smooth"}); }
  catch(e){ $("#estado2").textContent = "Falló: " + e.message; } b.disabled = false; };
$("#agregar").onclick = async () => { try{ REEL = (await api(`/reel/${REEL.id}/toma`, {method: "POST", body: "{}"})).reel; pintar(); }catch(e){ $("#estado3").textContent = "Falló: " + e.message; } };
$("#filmar").onclick = async () => { try{ const d = await api(`/reel/${REEL.id}/filmar`, {method: "POST"}); seguir(d.job); }catch(e){ $("#estado3").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
(async () => {
  CFG = await api("/config");
  const opts = (sel, obj, def) => { $(sel).innerHTML = Object.entries(obj).map(([k, v]) => `<option value="${k}" ${k === def ? "selected" : ""}>${esc(typeof v === "object" ? (v.label || v.nombre) : v)}</option>`).join(""); };
  opts("#motor", CFG.motores, CFG.motor_default); opts("#modo", CFG.modos, CFG.modo_default); opts("#lugar", CFG.lugares, "dormitorio");
  opts("#look", CFG.looks, CFG.look_default); opts("#camara", CFG.camaras, "motor"); opts("#energia", CFG.energias, CFG.energia_default);
  $("#duracion").innerHTML = CFG.duraciones.map(s => `<option value="${s}" ${s === 20 ? "selected" : ""}>${s} s</option>`).join("");
  $("#voz").innerHTML = '<option value="">La del personaje</option>' + CFG.voces.mujer.map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#tono").innerHTML = CFG.tonos.map(t => `<option value="${t}" ${t === "cercana" ? "selected" : ""}>${esc(t)}</option>`).join("");
  $("#mostrar").innerHTML = Object.entries(CFG.mostrar).map(([k, v]) => `<label class="chk"><input type="checkbox" value="${k}" ${CFG.mostrar_default.includes(k) ? "checked" : ""}>${esc(v)}</label>`).join("");
  $("#aviso_claude").style.display = CFG.claude ? "none" : ""; pintarVars();
  ["#voz", "#tono", "#energia", "#mic", "#modo", "#motor", "#look", "#camara", "#puesta", "#lugar"].forEach(k => $(k).addEventListener("change", async () => {
    if(!REEL || !(REEL.tomas || []).length) return;
    try{ REEL = (await api("/reel/" + REEL.id, {method: "PUT", body: JSON.stringify(ajustes())})).reel; pintar(); }catch(e){ $("#estado3").textContent = "Falló: " + e.message; } }));
  try{ const pj = await (await fetch("/personajes/api/lista")).json(); $("#pid").innerHTML = (pj.personajes || []).map(p => `<option value="${esc(p.id)}">${esc(p.nombre)}</option>`).join(""); }catch(e){}
  if(new URLSearchParams(location.search).get("embed")){ const avisar = () => parent.postMessage({cambiosAlto: document.documentElement.scrollHeight, de: "filmado"}, "*"); new ResizeObserver(avisar).observe(document.body); }
  lista();
  let rid = null; try{ rid = localStorage.getItem("filmado_rid"); }catch(e){}
  if(rid){ try{ await abrir(rid); }catch(e){} }
})();
</script></body></html>
"""
