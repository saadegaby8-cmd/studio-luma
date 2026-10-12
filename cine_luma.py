# -*- coding: utf-8 -*-
"""
cine_luma.py — MODO CINE: de una idea a un corto o un comercial
================================================================

Escribís la idea ("comercial de verano: el dron baja desde el cielo al atardecer, recorre la isla
y se posa sobre mis personajes con las mallas de la temporada"), elegís el ELENCO (cada
personaje con su prenda y su color, de Mis prendas) y:

1. EL DIRECTOR (Claude) escribe la película: el mundo (la isla, el mar, la luz), los OBJETOS 3D
   del set (mar, isla, palmeras, rocas, sombrillas, sol…), dónde está cada personaje y con qué
   pose, y el RECORRIDO DE CÁMARA: puntos clave en el tiempo (un dron que arranca muy alto,
   baja a toda velocidad, recorre la isla y se posa sobre cada personaje). Entre puntos, la cámara
   vuela de corrido; un punto puede ser un corte.
2. EL SET 3D (en el navegador): el mundo con sus objetos, los maniquíes y la línea del dron. El
   animatic vuela el recorrido entero; se pueden mover objetos, personajes y puntos.
3. LOS PUNTOS CLAVE se dibujan con Gemini, en estilo cine (con la guía del 3D, su Cara HD y el
   probador con su prenda), y se aprueban.
4. SE FILMA DE PUNTO A PUNTO: cada tramo arranca en un punto y termina EXACTO en el siguiente
   (Kling O3 con foto de inicio y de final), así que unidos son UN plano continuo.
5. EL MONTAJE: los tramos unidos, color y grano de cine, la voz en off y el cartel final.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

import claude_director as _claude
import filmado as _film
import higgsfield_luma as hf
import motion_luma as motion
import referencias_luma as refs_luma
import storyboard_luma as _sb
from estudio3d_luma import MANIQUI_JS, POSES_3D
from imagenes_ia import (
    CURRENT_SUB,
    _compress_ref,
    _img_part,
    _pfx,
    _pricing,
    _sanear_prompt_fal,
    budget_record,
    fal_generate,
    gemini_generate,
    get_settings,
    kv,
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
    _indice,
    _job_nuevo,
    _job_set,
    _k_job,
    _refs_identidad,
    _revisar_job,
    _texto,
    cara_identidad,
    caras_hd,
)
from videos_luma import _duracion_video, _spawn

VERSION = "1.3.0"
ROUTE_PREFIX = os.environ.get("CINE_PREFIX", "/cine").rstrip("/")
API = ROUTE_PREFIX + "/api"
DIR = PJ_DIR / "cine"
DURACIONES = (15, 20, 30, 45, 60)
MAX_PUNTOS = 14
MAX_ELENCO = 6
SEG_TRAMO_MIN, SEG_TRAMO_MAX = 3, 10
TIPOS = {"comercial": "Comercial", "corto": "Corto (historia)"}
CALIDAD = os.getenv("CINE_CALIDAD", "2K")

# Los objetos del set 3D (las mismas claves que la página) y su tamaño "normal" en metros.
OBJETOS = {
    "mar": "the sea (a large water surface)", "isla": "an island of sand with some vegetation (about 60 m across at escala 1)",
    "arena": "a stretch of beach sand", "palmera": "a palm tree", "roca": "a rock", "sombrilla": "a beach umbrella",
    "reposera": "a sun lounger", "cabana": "a small wooden cabana", "barco": "a sailing boat", "muelle": "a wooden pier",
    "yate": "a luxury motor yacht (about 25 m long, deck at y = 2.5)", "tabla_surf": "a surfboard on a wave",
    "ola": "a breaking wave",
    "sol": "the sun (place it far away, low for sunset)", "arbol": "a tree", "edificio": "a building",
    "mesa": "a table", "silla": "a chair", "cama": "a bed", "pared": "a wall", "auto": "a car",
}
# La luz del mundo (la página tiene las mismas claves).
LUCES = {
    "atardecer": "golden hour sunset, warm low sun, long soft shadows, glowing rim light",
    "amanecer": "soft pastel sunrise light, gentle and fresh",
    "mediodia": "bright midday tropical sun, crisp shadows, vivid turquoise water",
    "nublado": "soft overcast daylight, even and moody",
    "noche": "night, moonlight and warm practical lights",
    "estudio": "controlled studio lighting, a large soft key light",
}
CABEZA_CINE = ("cinematic film still from a high-end commercial, shot on an ARRI Alexa 35 with anamorphic lenses, "
               "motivated light, rich natural colour grade, subtle halation, fine 35mm film grain")

router = APIRouter(dependencies=[Depends(_bind), Depends(_sb._hf_clave)])


# ─────────────────────────────────────────────────────────────────────────────
# GUARDADO
# ─────────────────────────────────────────────────────────────────────────────

def _k_idx() -> str:
    return _pfx() + "cine:idx"


def _k(cid: str) -> str:
    return _pfx() + f"cine:{cid}"


def _k_img(cid: str, pid: str) -> str:
    return _pfx() + f"cine:{cid}:img:{pid}"


def _k_guia(cid: str, pid: str) -> str:
    return _pfx() + f"cine:{cid}:guia:{pid}"


def _dir(cid: str) -> Path:
    d = DIR / _pfx().replace(":", "-").strip("-") / cid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tramo(cid: str, pid: str) -> Path:
    return _dir(cid) / f"{pid}.mp4"


def _final(cid: str) -> Path:
    return _dir(cid) / "pelicula.mp4"


_LOCKS: Dict[str, asyncio.Lock] = {}


def _lock(cid: str) -> asyncio.Lock:
    return _LOCKS.setdefault(cid, asyncio.Lock())


async def _pr(cid: str) -> Dict[str, Any]:
    p = await kv.get(_k(cid))
    if not isinstance(p, dict):
        raise HTTPException(404, "No encuentro esa película.")
    return p


async def _guardar(p: Dict[str, Any]) -> None:
    p["actualizado"] = time.strftime("%Y-%m-%d %H:%M:%S")
    await kv.set(_k(p["id"]), p)


def _num(v: Any, lo: float, hi: float, defecto: float) -> float:
    try:
        return round(max(lo, min(hi, float(v))), 2)
    except (TypeError, ValueError):
        return defecto


# ─────────────────────────────────────────────────────────────────────────────
# EL ELENCO: cada personaje con su prenda y su color
# ─────────────────────────────────────────────────────────────────────────────

class _Elenco:
    """Todo lo de un personaje del elenco: sus caras (HD, perfil, retrato), su cuerpo con la
    prenda (probador de frente y de espalda), la foto real de la prenda y cómo es (en palabras)."""
    def __init__(self) -> None:
        self.pid, self.nombre, self.genero, self.desc, self.prenda_en = "", "", "mujer", "", ""
        self.caras: List[Tuple[str, str]] = []
        self.probador: List[str] = []
        self.reales: List[str] = []

    @property
    def cara(self) -> str:
        return self.caras[0][1] if self.caras else ""

    @property
    def quien(self) -> str:
        return "man" if self.genero == "hombre" else "woman"


async def _elenco(p: Dict[str, Any]) -> List[_Elenco]:
    out: List[_Elenco] = []
    for e in p.get("elenco") or []:
        x = _Elenco()
        try:
            doc = await _doc(e["pid"])
        except Exception:
            continue
        refs = await _refs_identidad(doc)
        if not refs:
            continue
        x.pid, x.nombre = doc["id"], doc.get("nombre") or "Personaje"
        x.genero = "hombre" if doc.get("genero") == "hombre" else "mujer"
        hd = await caras_hd(doc)
        for k, et in (("cara_hd", "face, frontal close-up"), ("perfil_hd", "face in three-quarter profile")):
            if hd.get(k):
                x.caras.append((et, hd[k]))
        if not x.caras:
            x.caras.append(("face, close-up", (await cara_identidad(doc, refs[0][1])) or refs[0][1]))
        x.caras.append(("portrait (face and hair)", refs[0][1]))
        try:
            cuerpo = await _film._cuerpo_en(doc)
        except Exception:
            cuerpo = ""
        x.desc = " ".join(t for t in (str(doc.get("desc_cara") or "")[:300], cuerpo) if t)
        if e.get("prenda"):
            ci = int(e.get("color") or 0)
            fotos = await refs_luma.fotos_color(e["prenda"], ci)
            x.reales = fotos[:2]
            pb = await refs_luma.probador_de(e["prenda"], ci, doc["id"])
            if not pb and fotos:
                pb = await _film.probador_para(doc, fotos, refs, ficha=[e["prenda"], ci])
            x.probador = pb or []
            f = await _sb._ficha(e["prenda"], ci, fotos)
            x.prenda_en = f.get("render_en") or ""
        out.append(x)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# EL DIRECTOR: la película entera (mundo, objetos, personajes, recorrido de cámara)
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_DIRECTOR = (
    "You are a film director and director of photography. Turn the owner's idea into a {tipo} of {dur} "
    "seconds for Instagram (vertical 9:16), shot like a high-end cinema commercial, starring the cast below "
    "(each one wears the garment from the brand that is listed). Design it as a 3D PREVIS:\n"
    "WORLD: \"mundo_en\" (English, max 60 words: the place, the season, the time of day, the atmosphere) and "
    "\"mundo_es\" (Spanish, one sentence). \"luz\": one of {luces}.\n"
    "SET (metres; the ground / sea level is y = 0; +z is the front of the set): \"objetos\": the 3D objects "
    "of the set, each {{\"tipo\", \"x\", \"z\", \"escala\" (1 = normal size), \"giro\" (degrees)}}, from this list "
    "(tipo: what it is): {objetos}. Build a believable place (for a beach: the sea, an island or sand, palm "
    "trees, rocks, umbrellas, the sun low on the horizon for a sunset…), max 30 objects.\n"
    "CAST: \"personajes\": where each cast member is, [{{\"i\": cast index, \"x\", \"y\" (0 on the ground; the "
    "deck height if on a boat), \"z\", \"giro\" (0 = facing +z), \"pose\" (one of {poses})}}]. Put them where the "
    "camera will meet them.\n"
    "CAMERA: \"puntos\": the KEYFRAMES of the camera in time, {n} of them, the first at t = 0 and the last at "
    "t = {dur}, with 3 to 10 seconds between consecutive points. Each point: \"t\", \"cam\" {{\"x\",\"y\",\"z\"}} "
    "(drone very high = y 150-300; fast low pass = y 3-15; close on a person = y 1.4-2 at 2-4 m from her), "
    "\"mira\" {{\"x\",\"y\",\"z\"}} (where the camera looks), \"lente\" (mm: 18-24 aerial, 35-50 people, 85 close "
    "details), \"corte\" (true ONLY if this point is a hard cut instead of flying continuously from the "
    "previous one), \"personajes\" (cast indices clearly visible at this point), \"en\" (English, max 45 "
    "words: the exact still frame at this point, what is seen and how they look; who wears what), \"es\" "
    "(Spanish, one short sentence for the owner), \"mov_en\" (English, max 30 words: how the camera moves FROM "
    "the previous point TO this one, e.g. 'the drone dives fast from the clouds toward the island, then "
    "levels out low over the turquoise water'; empty for the first point and for cuts).\n"
    "FOLLOW THE IDEA LITERALLY, scene by scene and in its order: every place, object, person, garment and "
    "ACTION the owner asked for must be there (a couple ON the deck of a luxury yacht anchored in the bay → a "
    "yate object in the water and the couple at its position with y = its deck height; a surfer → she is "
    "SURFING a wave on a surfboard, not standing). Put the action in \"en\" explicitly. Each cast member wears "
    "ONLY their listed garment and appears where the idea puts them; never invent people who are not in the "
    "cast. If a scene asks for a person the cast does not have, use the closest cast member. Every point's "
    "\"personajes\" lists EVERY cast member visible in it.\n"
    "If the idea asks for one continuous drone shot, NO cuts at all. Show every cast member up close at least "
    "once, with the garment clearly visible. Sensual and elegant, never explicit.\n"
    "Also: \"titulo\" (Spanish, short), \"logline\" (Spanish, one sentence), \"guion\" (the voice-over in "
    "Spanish from Argentina, Rioplatense, about {palabras} words, or empty if the idea has no voice), "
    "\"cierre\" {{\"titulo\": \"max 30 characters\", \"linea\": \"max 40 characters\"}} (the final card: it is added in "
    "editing with the logo, so NO point may ask for text, titles or logos inside the image).\n"
    "Answer ONLY JSON with those keys."
)


async def _director(p: Dict[str, Any], elenco: List[_Elenco]) -> Tuple[Dict[str, Any], float]:
    dur = int(p.get("duracion") or 20)
    system = _SYSTEM_DIRECTOR.format(
        tipo="commercial" if p.get("tipo") == "comercial" else "short film", dur=dur,
        luces="; ".join(f"{k}: {v}" for k, v in LUCES.items()),
        objetos="; ".join(f"{k}: {v}" for k, v in OBJETOS.items()),
        poses="; ".join(f"{k}: {v}" for k, v in POSES_3D.items()),
        n=f"{max(3, dur // 6)} to {min(MAX_PUNTOS, dur // 3 + 1)}", palabras=int(dur * 2.2))
    reparto = "\n".join(f"{i}. {x.nombre} ({x.quien}) — wears: {x.prenda_en or 'their own look'}" for i, x in enumerate(elenco))
    partes: List[Dict[str, Any]] = [{"text": f"THE IDEA (from the owner): {p.get('idea')}\n\nTHE CAST:\n{reparto}"}]
    for i, x in enumerate(elenco[:MAX_ELENCO]):
        if x.probador:
            partes += [{"text": f"Cast {i} ({x.nombre}) wearing her garment:"},
                       _img_part(_compress_ref(base64.b64decode(x.probador[0]), max_dim=700, q=82))]
    data, costo = await _claude.pedir_json(system, partes, max_tokens=12000,
                                           esfuerzo=os.getenv("CINE_ESFUERZO", "medium"))
    return _limpiar_plan(data, dur, len(elenco)), costo


def _limpiar_plan(d: Dict[str, Any], dur: int, n_elenco: int) -> Dict[str, Any]:
    objs = []
    for o in (d.get("objetos") or [])[:40]:
        if isinstance(o, dict) and o.get("tipo") in OBJETOS:
            objs.append({"tipo": o["tipo"], "x": _num(o.get("x"), -2000, 2000, 0), "z": _num(o.get("z"), -2000, 2000, 0),
                         "escala": _num(o.get("escala"), 0.05, 50, 1), "giro": _num(o.get("giro"), -360, 360, 0)})
    pjs = []
    for x in (d.get("personajes") or [])[:MAX_ELENCO]:
        try:
            i = int(x.get("i"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < n_elenco:
            pjs.append({"i": i, "x": _num(x.get("x"), -500, 500, 0), "y": _num(x.get("y"), 0, 50, 0), "z": _num(x.get("z"), -500, 500, 0),
                        "giro": _num(x.get("giro"), -360, 360, 0), "pose": x.get("pose") if x.get("pose") in POSES_3D else "de_pie"})
    pts = []
    for k, x in enumerate((d.get("puntos") or [])[:MAX_PUNTOS]):
        if not isinstance(x, dict):
            continue
        cam, mira = x.get("cam") or {}, x.get("mira") or {}
        pts.append({"id": "p" + _uuid.uuid4().hex[:7], "t": _num(x.get("t"), 0, 600, 0),
                    "cam": [_num(cam.get("x"), -3000, 3000, 0), _num(cam.get("y"), 0.2, 3000, 2), _num(cam.get("z"), -3000, 3000, 5)],
                    "mira": [_num(mira.get("x"), -3000, 3000, 0), _num(mira.get("y"), 0, 3000, 1), _num(mira.get("z"), -3000, 3000, 0)],
                    "lente": int(_num(x.get("lente"), 12, 135, 35)), "corte": bool(x.get("corte")) and k > 0,
                    "personajes": [int(i) for i in (x.get("personajes") or []) if str(i).isdigit() and int(i) < n_elenco][:4],
                    "en": _texto(x.get("en"), 600), "es": _texto(x.get("es"), 300), "mov_en": _texto(x.get("mov_en"), 400),
                    "img": False, "aprobado": False})
    _ajustar_tiempos(pts, dur)
    cierre = d.get("cierre") if isinstance(d.get("cierre"), dict) else {}
    return {"titulo": _texto(d.get("titulo"), 80), "logline": _texto(d.get("logline"), 300),
            "mundo_en": _texto(d.get("mundo_en"), 600), "mundo_es": _texto(d.get("mundo_es"), 300),
            "luz": d.get("luz") if d.get("luz") in LUCES else "atardecer", "objetos": objs, "personajes": pjs,
            "puntos": pts, "guion": _texto(d.get("guion"), 2000),
            "cierre": {"titulo": _texto(cierre.get("titulo"), 40), "linea": _texto(cierre.get("linea"), 60)}}


def _ajustar_tiempos(pts: List[Dict[str, Any]], dur: int) -> None:
    """Cada tramo entre 3 y 10 s (lo que filma bien Kling); el primero en 0."""
    if not pts:
        return
    pts.sort(key=lambda x: x["t"])
    pts[0]["t"] = 0
    for a, b in zip(pts, pts[1:]):
        b["t"] = round(a["t"] + max(SEG_TRAMO_MIN, min(SEG_TRAMO_MAX, round(b["t"] - a["t"]))))


def _tramos(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Lo que se filma: de cada punto al siguiente, de corrido (salvo que el siguiente sea un corte).
    Un punto que arranca un grupo y no tiene siguiente continuo se filma solo (3 s)."""
    pts = p.get("puntos") or []
    out = []
    if _una_pelicula(p) and pts:
        # Tomas de hasta 15 s (Kling) o 30 s (Seedance) de corrido: cada una cubre los puntos que
        # entran; la siguiente arranca en el ÚLTIMO CUADRO filmado de la anterior (sin corte).
        tope = SEEDANCE_MAX if p.get("motor_video") == "seedance25" else 15
        k = 0
        while k < len(pts) - 1 or (k == 0 and len(pts) == 1):
            j = k + 1
            while j + 1 < len(pts) and pts[j + 1]["t"] - pts[k]["t"] <= tope:
                j += 1
            j = min(j, len(pts) - 1)
            seg = int(max(4, min(tope, round((pts[j]["t"] - pts[k]["t"]) or 4))))
            out.append({"id": "toma_" + pts[k]["id"], "de": pts[k], "a": pts[j] if j > k else None, "seg": seg,
                        "puntos": pts[k:j + 1], "seedance": True})
            if j <= k:
                break
            k = j
        return out
    for k, a in enumerate(pts):
        b = pts[k + 1] if k + 1 < len(pts) else None
        if b and not b.get("corte"):
            out.append({"id": a["id"], "de": a, "a": b, "seg": int(max(SEG_TRAMO_MIN, min(15, round(b["t"] - a["t"]))))})
        elif k == 0 or a.get("corte"):
            out.append({"id": a["id"], "de": a, "a": None, "seg": SEG_TRAMO_MIN})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# LOS PUNTOS CLAVE (Gemini, en estilo cine)
# ─────────────────────────────────────────────────────────────────────────────

MOTORES_CUADRO = {"gemini": "Gemini (Nano Banana)", "hf_marketing": "Marketing Studio · Higgsfield (más barato)"}
MOTORES_VIDEO = {
    "pelicula_kling": "🎬 UNA SOLA PELÍCULA · Kling O3 (fal) — un prompt con la línea de tiempo, sus caras y prendas; "
                      "sólo se dibuja el primer cuadro; tomas de 15 s encadenadas sin cortes",
    "pelicula_hf_o3": "🎬 UNA SOLA PELÍCULA · Kling O3 (Higgsfield) — con tus caras y prendas como referencia, más "
                      "barato que por fal (~US$0,07/s); tomas de 15 s encadenadas",
    "pelicula_hf_kling3": "🎬 UNA SOLA PELÍCULA · Kling 3.0 (Higgsfield) — lo más barato (~US$0,05/s), tomas de 15 s "
                         "encadenadas; sólo recibe el primer cuadro y el texto (los personajes que aparecen después no "
                         "salen con sus caras ni sus prendas exactas): ideal para borradores",
    "seedance25": "🎬 UNA SOLA PELÍCULA · Seedance 2.5 (Higgsfield) — hasta 30 s de corrido con la línea de tiempo; "
                  "sólo el primer cuadro (si rechaza las caras, va con sus cuerpos y prendas)",
    "fal_kling_o3": "Punto a punto · Kling O3 (fal) — cada punto dibujado y aprobado, tramo por tramo",
    "hf_kling3_pro": "Kling 3.0 Pro · Higgsfield — más barato; arranca en el punto pero no llega exacto al siguiente",
    "hf_kling3_std": "Kling 3.0 Standard · Higgsfield — el más barato; igual que el Pro, menos detalle",
}
UNA_PELICULA = ("pelicula_kling", "seedance25", "pelicula_hf_kling3", "pelicula_hf_o3")
# Kling O3 con referencias en Higgsfield: sus rutas copian las de fal (Seedance 2.5 anduvo igual).
KLING_O3_HF = [x for x in [os.getenv("HIGGSFIELD_KLING_O3", ""), "kling-video/o3/pro/reference-to-video",
                           "kling-video/o3/reference-to-video", "kling-video/o3/standard/reference-to-video"] if x]
PRECIO_KLING_O3_HF = float(os.getenv("HIGGSFIELD_PRECIO_KLING_O3", "0.071"))


def _una_pelicula(p: Dict[str, Any]) -> bool:
    """El modo principal: la película sale de UN prompt (la escena y la línea de tiempo) con las
    referencias de su elenco; sólo el primer cuadro se dibuja y se aprueba."""
    return (p.get("motor_video") or "pelicula_kling") in UNA_PELICULA
# SEEDANCE 2.5 EN UNA TOMA: todos los puntos aprobados como referencia y la línea de tiempo segundo a
# segundo en el pedido. Por Higgsfield (por fal es ~3 veces más caro y su filtro ya rechazó caras
# realistas). La ruta del modelo en Higgsfield se prueba en este orden (o HIGGSFIELD_SEEDANCE25).
SEEDANCE_HF = [x for x in [os.getenv("HIGGSFIELD_SEEDANCE25", ""), "bytedance/seedance-2.5/reference-to-video",
                           "seedance-2.5/reference-to-video", "bytedance/seedance/v2.5/reference-to-video"] if x]
# OJO: US$0,175/s es el precio "desde" de Higgsfield (la resolución más baja). En 1080p un pedido de 20 s
# pidió más saldo del que había. Va en 720p; el precio exacto por resolución se carga cuando se sepa.
PRECIO_SEEDANCE_HF = float(os.getenv("HIGGSFIELD_PRECIO_SEEDANCE25", "0.173"))
# Lo que cobró Higgsfield de verdad (con el 30 % de descuento de la cuenta): 720p, 20 s = US$6,46.
PRECIOS_SEEDANCE = {"480p": float(os.getenv("HIGGSFIELD_SEEDANCE_480", "0.173")),
                    "720p": float(os.getenv("HIGGSFIELD_SEEDANCE_720", "0.323")),
                    "1080p": float(os.getenv("HIGGSFIELD_SEEDANCE_1080", "0.65"))}
RESOLUCIONES = {"480p": "480p · ~US$0,17/s (borrador barato)", "720p": "720p · ~US$0,32/s (lo que cobró)",
                "1080p": "1080p · ~US$0,65/s (estimado: todavía no lo vimos cobrar)"}
SEEDANCE_MAX = 30


def _camara_parecida(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """El dibujo anterior sólo sirve de referencia si la cámara está más o menos igual (misma
    altura y cerca) y no hay gente: si no, Gemini lo pegaba de fondo (un collage: la playa vista
    desde arriba detrás de ella parada en la arena)."""
    if b.get("personajes") or b.get("corte"):
        return False
    ya, yb = max(0.5, a["cam"][1]), max(0.5, b["cam"][1])
    dist = sum((u - v) ** 2 for u, v in zip(a["cam"], b["cam"])) ** 0.5
    return max(ya, yb) / min(ya, yb) < 2.5 and dist < max(ya, yb) * 1.5


def _camara_txt(pt: Dict[str, Any]) -> str:
    """Dónde está la cámara, en palabras: para que la imagen sea UNA foto coherente desde ahí."""
    cx, cy, cz = pt["cam"]
    mx, my, mz = pt["mira"]
    d = ((cx - mx) ** 2 + (cz - mz) ** 2) ** 0.5
    altura = (f"a drone {int(cy)} m high in the sky" if cy > 40 else f"a drone flying low, {int(cy)} m above the ground/sea"
              if cy > 6 else f"at {cy:.1f} m from the ground (human height)" if cy > 1 else "very low, near the ground")
    mira = "looking down steeply" if cy - my > d * 0.8 else "looking slightly down" if cy - my > d * 0.2 else "looking straight ahead"
    return (f"THE CAMERA is {altura}, {d:.0f} m away from what it frames, {mira}, {pt.get('lente') or 35}mm lens. It is ONE "
            "single real photograph taken from there: the horizon, the perspective and the scale of everything match that "
            "camera position. NEVER a collage, never a pasted or layered background, never two viewpoints in one image.")


def _quienes(pt: Dict[str, Any], elenco: List[_Elenco]) -> List[_Elenco]:
    return [elenco[i] for i in pt.get("personajes") or [] if i < len(elenco)]


def _partes_punto(p: Dict[str, Any], pt: Dict[str, Any], elenco: List[_Elenco], guia: Optional[str],
                  previo: Optional[str], correccion: str) -> Tuple[List[Dict[str, Any]], str]:
    """El pedido de un punto. Cada personaje que se ve con TODAS sus referencias (sus caras, su
    cuerpo con la prenda de frente y de espalda, la foto real de la prenda). La guía del 3D sólo
    manda la cámara; la acción la manda el texto. Devuelve (partes, prompt plano)."""
    quienes = _quienes(pt, elenco)[:3]
    n_caras = 3 if len(quienes) <= 1 else 2 if len(quienes) == 2 else 1
    imgs: List[Tuple[str, str]] = []
    if guia:
        imgs.append(("the CAMERA GUIDE (a rough 3D previs with grey shapes): use it ONLY for the camera position, "
                     "height, angle, lens and where things sit in the frame. It does NOT decide the poses, the action "
                     "or what people stand on — the text does (if the text says she surfs a wave, she surfs). Ignore "
                     "its grey shapes and flat colours", guia))
    if previo:
        imgs.append(("the previous keyframe of this same film, from a similar camera: ONLY to keep the same world "
                     "(colours of the sea, the land, the sky, the light). Do NOT copy or paste it as a background", previo))
    for x in quienes:
        for et, f in x.caras[:n_caras]:
            imgs.append((f"{x.nombre}'s {et}: THIS is {x.nombre}, the exact same {x.quien}", f))
        for k, f in enumerate(x.probador[:2 if len(quienes) <= 2 else 1]):
            imgs.append((f"{x.nombre}'s BODY wearing their garment ({'front' if k == 0 else 'back'}, headless "
                         "reference on grey): copy the body and EXACTLY the garment and how it fits", f))
        if x.reales:
            imgs.append((f"the REAL product photo of {x.nombre}'s garment: exact colour, fabric, cut and details", x.reales[0]))
    gente = " ".join(f"{x.nombre} ({x.quien}{'; ' + x.desc if x.desc else ''}) wears ONLY {x.prenda_en or 'their garment'}."
                     for x in quienes)
    cuantos = (f"EXACTLY {len(quienes)} person{'s' if len(quienes) > 1 else ''} in the frame: "
               + ", ".join(x.nombre for x in quienes) + ". No other people anywhere."
               if quienes else "No people in this frame (or only far tiny silhouettes).")
    txt = (f"Vertical 9:16 {CABEZA_CINE}. A keyframe of the film. THE WORLD: {p.get('mundo_en', '')} LIGHT: "
           f"{LUCES.get(p.get('luz') or 'atardecer', '')}. {_camara_txt(pt)} THIS FRAME (do exactly this action): {pt.get('en', '')} "
           f"{cuantos} " + (f"PEOPLE: {gente} Each person is EXACTLY the person of their face references (same "
                            "features, skin, hair), with real skin texture, and the garment is EXACTLY their product. "
                            if quienes else "") +
           "Photorealistic, no text, no logos, no watermark."
           + (f" CORRECTIONS (fix these, change nothing else): {correccion}" if correccion else ""))
    txt = _sanear_prompt_fal(txt)
    partes: List[Dict[str, Any]] = [{"text": txt}]
    for k, (et, f) in enumerate(imgs, 1):
        partes += [{"text": f"IMAGE {k}: {et}."}, _img_part(f)]
    roles = " ".join(f"Image {k} is {et}." for k, (et, _) in enumerate(imgs, 1))
    return partes, _sanear_prompt_fal(roles + " " + txt)


QC_ELENCO = (
    "Sos supervisora de casting y vestuario de un comercial. La PRIMERA imagen es un cuadro generado con IA. "
    "Después vienen, por cada personaje, sus REFERENCIAS rotuladas con su nombre (su cara, su cuerpo con la "
    "prenda y la foto real de la prenda). Revisá, personaje por personaje: 1) que esté y que sea ÉL o ELLA "
    "(rasgos de la cara, piel, color y peinado del pelo); 2) que tenga puesta EXACTAMENTE su prenda (diseño, "
    "colores, cortes, breteles, cierres, mangas) y no otra; 3) que no haya gente de más; 4) que haga la acción "
    "pedida: {accion}; 5) que sea UNA sola foto coherente: si parece un COLLAGE (un fondo pegado detrás de la "
    "persona, el paisaje visto desde otra altura que la persona, un horizonte que no coincide, una playa encima de "
    "una montaña, escalas imposibles) es un error grave. Ignorá la luz y el encuadre.\n"
    "Devolvé SOLO un JSON: {{\"puntaje\": <1-10>, \"diferencias\": [\"concreta y accionable, con el nombre\"]}}. "
    "Una cara que no es la persona, una prenda distinta, una persona de más o un collage nunca pueden tener más de 4."
)


async def _inspector_elenco(b64: str, pt: Dict[str, Any], quienes: List[_Elenco]) -> Optional[Dict[str, Any]]:
    try:
        key = await _current_api_key()
        if not key or not quienes:
            return None
        st = await get_settings()
        modelo = str(st.get("qc_model") or "").strip() or ANALYZE_MODEL
        partes: List[Dict[str, Any]] = [{"text": QC_ELENCO.format(accion=pt.get("en") or pt.get("es") or "")},
                                        {"text": "CUADRO GENERADO:"},
                                        _img_part(_compress_ref(base64.b64decode(b64), max_dim=2048, q=92))]
        for x in quienes:
            for et, f in x.caras[:2]:
                partes += [{"text": f"{x.nombre} — su cara ({et}):"}, _img_part(f)]
            if x.probador:
                partes += [{"text": f"{x.nombre} — su cuerpo con SU prenda:"}, _img_part(x.probador[0])]
            if x.reales:
                partes += [{"text": f"{x.nombre} — foto REAL de su prenda:"}, _img_part(x.reales[0])]
        return await _qc_call(partes, key, modelo)
    except Exception:
        return None


async def _dibujar_punto(p: Dict[str, Any], pt: Dict[str, Any], elenco: List[_Elenco], previo: Optional[str],
                         correccion: str) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    guia = await kv.get(_k_guia(p["id"], pt["id"])) if pt.get("guia") else None
    partes, plano = _partes_punto(p, pt, elenco, guia, previo, correccion)
    settings = dict(await get_settings())
    nota = ""
    img: Optional[bytes] = None
    if p.get("motor_cuadros") == "hf_marketing" and hf.disponible():
        precio = _sb.MOTORES_CUADRO["hf_marketing"]["precio"]
        await _cobrar(precio)
        try:
            img = await _sb._dibujar_hf(partes, plano)
            await budget_record("cine_punto", "hf_marketing", precio, 1, note="cine: punto clave (Higgsfield)")
        except hf.HiggsfieldAceptado as e:
            raise RuntimeError(str(e)[:300])
        except Exception as e:
            nota = f"Marketing Studio no pudo ({str(e)[:120]}); "
    if img is None:
        est = _pricing(settings).get(CALIDAD, 0.10)
        await _cobrar(est)
        try:
            img = await gemini_generate(partes, settings, aspect="9:16", image_size=CALIDAD, save_prompt=False)
            await budget_record("cine_punto", CALIDAD, est, 1, note="cine: punto clave (Gemini)")
            nota = (nota + "lo dibujó Gemini.") if nota else ""
        except HTTPException as e:
            if e.status_code != 422:
                raise
            s1 = dict(settings, flux_fallback_model="", _fal_sin_adivinar=True)
            slug = str(settings.get("flux_tryon_model") or "bytedance/seedream/v5/pro/edit")
            precio = float(settings.get("precio_flux", 0.07) or 0.07)
            await _cobrar(precio)
            img = await fal_generate([{"text": plano}] + [x for x in partes if "inlineData" in x or "inline_data" in x],
                                     s1, "9:16", "2K", slug)
            await budget_record("cine_punto", slug, precio, 1, note="cine: punto clave (Seedream)")
            nota += f"Gemini lo rechazó ({str(e.detail)[:120]}); lo dibujó Seedream."
    b64 = _compress_ref(img, max_dim=1920, q=92)
    # LOS INSPECTORES: el de elenco (cada cara, cada prenda, nadie de más, la acción) en TODO punto
    # con gente, y el de Fotos (la prenda con lupa y la anatomía) cuando se ve una sola.
    quienes = _quienes(pt, elenco)
    tareas = [_inspector_elenco(b64, pt, quienes)] if quienes else []
    if len(quienes) == 1 and quienes[0].reales:
        tareas.append(verificar_prenda(base64.b64decode(b64), quienes[0].reales))
    res = [r for r in await asyncio.gather(*tareas) if isinstance(r, dict)] if tareas else []
    qc = None
    if res:
        difs: List[str] = []
        for r in res:
            difs += [d for d in (r.get("diferencias") or []) if d not in difs]
        qc = {"puntaje": min(int(r.get("puntaje", 10)) for r in res), "diferencias": difs[:8]}
    return b64, nota, qc


# ─────────────────────────────────────────────────────────────────────────────
# FILMAR: de punto a punto (Kling O3 con inicio y final) → un plano continuo
# ─────────────────────────────────────────────────────────────────────────────

async def _filmar_tramo(cli: httpx.AsyncClient, key: str, headers: Dict[str, str], jid: str, p: Dict[str, Any],
                        tr: Dict[str, Any], elenco: List[_Elenco], subidas: Dict[str, str]) -> float:
    cid, a, b = p["id"], tr["de"], tr["a"]
    ini = await kv.get(_k_img(cid, a["id"]))
    if not ini:
        raise RuntimeError("falta el dibujo del punto")
    u_ini = await _fal_subir(cli, key, await asyncio.to_thread(_film._vertical, ini), "image/jpeg", f"cine-{cid}-{a['id']}.jpg")
    u_fin = ""
    if b:
        fin = await kv.get(_k_img(cid, b["id"]))
        if not fin:
            raise RuntimeError("falta el dibujo del punto siguiente")
        u_fin = await _fal_subir(cli, key, await asyncio.to_thread(_film._vertical, fin), "image/jpeg", f"cine-{cid}-{b['id']}.jpg")
    # Sus caras como elementos (las de los que se ven al llegar), así no se las inventa en el vuelo.
    quienes = sorted(set((b or a).get("personajes") or []) | set(a.get("personajes") or []))[:2]
    els, nombres = [], []
    for i in quienes:
        if i >= len(elenco):
            continue
        x = elenco[i]
        if x.pid not in subidas:
            subidas[x.pid] = await _fal_subir(cli, key, base64.b64decode(x.cara), "image/jpeg", f"cine-{cid}-{x.pid}.jpg")
            if x.probador:
                subidas[x.pid + "_pb"] = await _fal_subir(cli, key, base64.b64decode(x.probador[0]), "image/jpeg",
                                                          f"cine-{cid}-{x.pid}-pb.jpg")
        els.append(_film._elemento([subidas[x.pid]] + ([subidas[x.pid + "_pb"]] if x.pid + "_pb" in subidas else [])))
        nombres.append(f"@Element{len(els)} is {x.nombre}" + (f", wearing {x.prenda_en[:160]}" if x.prenda_en else ""))
    mov = (b or {}).get("mov_en") or a.get("mov_en") or "a smooth cinematic camera move"
    prompt = _sanear_prompt_fal(
        f"Vertical 9:16 cinematic footage from a high-end commercial, shot on a cinema camera with anamorphic lenses "
        f"at 24fps with natural motion blur. ONE continuous shot, no cuts: {mov}. The video starts EXACTLY on the "
        f"first frame" + (" and ends EXACTLY on the last frame" if b else "") + f". The world: {p.get('mundo_en', '')} "
        f"Light: {LUCES.get(p.get('luz') or 'atardecer', '')}. " + (" ".join(nombres) + ": keep their faces and "
        "their garments EXACTLY. " if nombres else "") + "Smooth stabilised drone or crane movement, real-time motion "
        "of the people and the water, photorealistic. No text, no extra people.")
    motor = p.get("motor_video") if p.get("motor_video") in MOTORES_VIDEO else "fal_kling_o3"
    if motor.startswith("hf_") and hf.disponible():
        mv = _sb.MOTORES_VIDEO[motor]
        crudo = _dir(cid) / f"{tr['id']}_crudo.mp4"
        try:
            res = await hf.video(cli, mv["hf"], {"prompt": prompt, "image_url": u_ini, "duration": tr["seg"],
                                                 "generate_audio": False, "sound": "off",
                                                 "negative_prompt": _film._NEGATIVO}, crudo)
            costo = round(_sb._precio_tramo(mv, tr["seg"]), 3)
            await budget_record("cine_tramo", motor, costo, 1, note=f"cine: tramo de {tr['seg']} s (Higgsfield {res.get('_request_id', '')})")
            await asyncio.to_thread(_film._normalizar, crudo, _tramo(cid, tr["id"]), None, 0.0, "centro")
            return costo
        except hf.HiggsfieldAceptado as e:
            raise RuntimeError(str(e)[:300])      # ya aceptado: no se paga dos veces
        except Exception as e:
            print(f"[cine] Higgsfield no aceptó el tramo ({e}); va por fal")
        finally:
            crudo.unlink(missing_ok=True)
    m = _film.MOTORES[_sb.MOTOR_VIDEO]
    payload: Dict[str, Any] = {"prompt": prompt, "start_image_url": u_ini, "duration": str(tr["seg"]),
                               "aspect_ratio": "9:16", "generate_audio": False, "negative_prompt": _film._NEGATIVO,
                               "cfg_scale": 0.5}
    if u_fin:
        payload["end_image_url"] = u_fin
    if els:
        payload["elements"] = els
    crudo = _dir(cid) / f"{tr['id']}_crudo.mp4"
    try:
        await _film._fal_video(cli, headers, m["modelo"], payload, f"{jid}-{tr['id']}", ("negative_prompt", "cfg_scale"), crudo)
        costo = round(tr["seg"] * m["precio_seg"], 3)
        await budget_record("cine_tramo", _sb.MOTOR_VIDEO, costo, 1, note=f"cine: tramo de {tr['seg']} s")
        await asyncio.to_thread(_film._normalizar, crudo, _tramo(cid, tr["id"]), None, 0.0, "centro")
    finally:
        crudo.unlink(missing_ok=True)
    return costo


def _ultimo_cuadro(video: Path) -> Optional[str]:
    """El último cuadro de una toma (de ahí arranca la siguiente: el dron no corta)."""
    out = video.with_name(video.stem + "_ultimo.jpg")
    r = subprocess.run([_film._ffmpeg_bin(), "-y", "-sseof", "-0.12", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)],
                       capture_output=True, timeout=60)
    if r.returncode != 0 or not out.exists():
        return None
    b = base64.b64encode(out.read_bytes()).decode()
    out.unlink(missing_ok=True)
    return b


def _linea_pelicula(p: Dict[str, Any], tr: Dict[str, Any]) -> str:
    """La línea de tiempo de ESTA toma, en sus segundos, desde los puntos del director."""
    pts, t0 = tr["puntos"], tr["puntos"][0]["t"]
    lineas = [f"0s: {pts[0].get('en', '')}"]
    for a, b in zip(pts, pts[1:]):
        lineas.append(f"{int(round(a['t'] - t0))}s-{int(round(b['t'] - t0))}s: {b.get('mov_en') or 'the camera keeps moving smoothly'}"
                      f" → at {int(round(b['t'] - t0))}s: {b.get('en', '')}")
    return "; ".join(lineas)


def _prompt_pelicula(p: Dict[str, Any], tr: Dict[str, Any], leyenda: str, primera: bool) -> str:
    return _sanear_prompt_fal(
        f"Vertical 9:16 cinematic footage from a high-end commercial, shot on a cinema camera with anamorphic lenses at "
        f"24fps with natural motion blur. ONE single continuous {tr['seg']}-second shot, NO cuts, NO scene changes: the "
        f"camera flies through ONE coherent world, like a real drone or crane take. "
        + ("It starts EXACTLY on the given first frame. " if primera else
           "It CONTINUES EXACTLY from the given first frame (the last frame of the previous part of the same take). ")
        + f"THE WORLD: {p.get('mundo_en', '')} LIGHT: {LUCES.get(p.get('luz') or 'atardecer', '')}. TIMELINE (follow it "
        f"in order, second by second): {_linea_pelicula(p, tr)}. {leyenda} Every person looks EXACTLY like their "
        "reference and wears EXACTLY their garment, with real skin texture and real fabric. Photorealistic. NO text, NO "
        "titles, NO logos, NO letters anywhere in the video (the title card is added later), no extra people.")


async def _filmar_pelicula(cli: httpx.AsyncClient, key: str, headers: Dict[str, str], jid: str, p: Dict[str, Any],
                           tr: Dict[str, Any], elenco: List[_Elenco], inicio_b64: str, primera: bool) -> float:
    """UNA toma de la película: un prompt con la escena y la línea de tiempo, el primer cuadro
    (o el último de la toma anterior) y las referencias del elenco que aparece."""
    cid = p["id"]
    u_ini = await _fal_subir(cli, key, await asyncio.to_thread(_film._vertical, inicio_b64), "image/jpeg", f"cine-{cid}-{tr['id']}-ini.jpg")
    idx = sorted({i for pt in tr["puntos"] for i in (pt.get("personajes") or []) if i < len(elenco)})[:4]
    caras, cuerpos = {}, {}
    for i in idx:
        x = elenco[i]
        caras[i] = await _fal_subir(cli, key, base64.b64decode(x.cara), "image/jpeg", f"cine-{cid}-{x.pid}-cara.jpg")
        cuerpos[i] = [await _fal_subir(cli, key, base64.b64decode(b), "image/jpeg", f"cine-{cid}-{x.pid}-pb{k}.jpg")
                      for k, b in enumerate(x.probador[:2])]
    crudo = _dir(cid) / f"{tr['id']}_crudo.mp4"
    try:
        if p.get("motor_video") == "pelicula_hf_kling3":
            if not hf.disponible():
                raise RuntimeError("Kling 3.0 va por Higgsfield: cargá tu clave de Higgsfield (Storyboard → paso 4).")
            gente = " ".join(f"{elenco[i].nombre} is a {elenco[i].quien} ({elenco[i].desc[:200]}) wearing "
                             f"{elenco[i].prenda_en[:200] or 'their garment'}." for i in idx)
            mv = _sb.MOTORES_VIDEO["hf_kling3_pro"]
            res = await hf.video(cli, mv["hf"], {"prompt": _prompt_pelicula(p, tr, gente, primera)[:2400], "image_url": u_ini,
                                                 "duration": min(15, tr["seg"]), "generate_audio": False, "sound": "off",
                                                 "negative_prompt": _film._NEGATIVO}, crudo)
            costo = round(min(15, tr["seg"]) * mv["precio_seg"], 3)
            await budget_record("cine_tramo", "pelicula_hf_kling3", costo, 1,
                                note=f"cine: película Kling 3.0 de {tr['seg']} s (Higgsfield {res.get('_request_id', '')})")
        elif p.get("motor_video") == "seedance25":
            costo = await _seedance(cli, p, tr, elenco, idx, u_ini, caras, cuerpos, primera, crudo)
        else:
            els, leyenda = [], []
            for i in idx:
                x = elenco[i]
                els.append(_film._elemento([caras[i]] + cuerpos[i]))
                leyenda.append(f"@Element{len(els)} is {x.nombre} ({x.quien}), wearing {x.prenda_en[:180] or 'their garment'}")
            payload: Dict[str, Any] = {"prompt": _prompt_pelicula(p, tr, ". ".join(leyenda) + "." if leyenda else "", primera),
                                       "start_image_url": u_ini, "duration": str(min(15, tr["seg"])), "aspect_ratio": "9:16",
                                       "generate_audio": False, "negative_prompt": _film._NEGATIVO, "cfg_scale": 0.5}
            if els:
                payload["elements"] = els
            if p.get("motor_video") == "pelicula_hf_o3":
                costo = await _kling_o3_hf(cli, payload, tr, crudo)
            else:
                m = _film.MOTORES[_sb.MOTOR_VIDEO]
                await _film._fal_video(cli, headers, m["modelo"], payload, f"{jid}-{tr['id']}", ("negative_prompt", "cfg_scale"), crudo)
                costo = round(min(15, tr["seg"]) * m["precio_seg"], 3)
                await budget_record("cine_tramo", _sb.MOTOR_VIDEO, costo, 1, note=f"cine: película, toma de {tr['seg']} s")
        await asyncio.to_thread(_film._normalizar, crudo, _tramo(cid, tr["id"]), None, 0.0, "centro")
        return costo
    finally:
        crudo.unlink(missing_ok=True)


async def _kling_o3_hf(cli: httpx.AsyncClient, payload: Dict[str, Any], tr: Dict[str, Any], crudo: Path) -> float:
    """Kling O3 con referencias (sus caras y prendas como elementos) por Higgsfield: el mismo pedido
    que en fal, más barato. Prueba las rutas probables; una ruta que no existe no cobra."""
    if not hf.disponible():
        raise RuntimeError("Kling O3 por Higgsfield: cargá tu clave de Higgsfield (Storyboard → paso 4).")
    ultimo = ""
    for ruta in KLING_O3_HF:
        try:
            res = await hf.video(cli, ruta, dict(payload, sound="off", duration=int(payload["duration"])), crudo)
        except hf.HiggsfieldAceptado as e:
            raise RuntimeError(str(e)[:300])
        except hf.HiggsfieldError as e:
            ultimo = str(e)
            if "404" in ultimo or "not found" in ultimo.lower():
                continue
            raise RuntimeError(f"Higgsfield no aceptó Kling O3: {ultimo[:240]}")
        costo = round(min(15, tr["seg"]) * PRECIO_KLING_O3_HF, 3)
        await budget_record("cine_tramo", "kling_o3_hf", costo, 1,
                            note=f"cine: película Kling O3 de {tr['seg']} s (Higgsfield {ruta} {res.get('_request_id', '')})")
        return costo
    raise RuntimeError(f"No encontré Kling O3 con referencias en Higgsfield ({ultimo[:160]}). Si sabés la ruta, cargala "
                       "en la variable HIGGSFIELD_KLING_O3 (o usá Kling O3 por fal).")


async def _seedance(cli: httpx.AsyncClient, p: Dict[str, Any], tr: Dict[str, Any], elenco: List[_Elenco], idx: List[int],
                    u_ini: str, caras: Dict[int, str], cuerpos: Dict[int, List[str]], primera: bool, crudo: Path) -> float:
    """Seedance 2.5 por Higgsfield: primero con sus caras; si el filtro de ByteDance las rechaza
    (no cobra), otra vez sólo con sus cuerpos con la prenda (sin cara)."""
    if not hf.disponible():
        raise RuntimeError("Seedance 2.5 va por Higgsfield: cargá tu clave de Higgsfield (Storyboard → paso 4).")

    def armar(con_caras: bool) -> Dict[str, Any]:
        urls, leyenda = [u_ini], []
        for i in idx:
            x = elenco[i]
            refs = ([caras[i]] if con_caras else []) + cuerpos[i]
            nums = []
            for u in refs:
                urls.append(u)
                nums.append(f"@Image{len(urls)}")
            if nums:
                leyenda.append(f"{x.nombre} ({x.quien}) is {' and '.join(nums)}, wearing {x.prenda_en[:160] or 'their garment'}")
        txt = "@Image1 is the first frame. " + (". ".join(leyenda) + "." if leyenda else "")
        return {"prompt": _prompt_pelicula(p, tr, txt, primera), "image_urls": urls[:30], "duration": tr["seg"],
                "aspect_ratio": "9:16", "resolution": p.get("resolucion") if p.get("resolucion") in RESOLUCIONES else os.getenv("CINE_SEEDANCE_RESOLUCION", "720p"),
                "generate_audio": False}

    ultimo = ""
    for con_caras in (True, False):
        payload = armar(con_caras)
        for ruta in SEEDANCE_HF:
            try:
                res = await hf.video(cli, ruta, payload, crudo)
            except hf.HiggsfieldAceptado as e:
                raise RuntimeError(str(e)[:300])
            except hf.HiggsfieldError as e:
                ultimo = str(e)
                if "404" in ultimo or "not found" in ultimo.lower():
                    continue
                if con_caras and any(w in ultimo.lower() for w in ("likeness", "real people", "real person", "face", "sensitive")):
                    break          # el filtro rechazó las caras: probamos sin ellas
                raise RuntimeError(f"Higgsfield no aceptó Seedance 2.5: {ultimo[:240]}")
            costo = round(tr["seg"] * PRECIOS_SEEDANCE.get(payload["resolution"], PRECIO_SEEDANCE_HF), 3)
            await budget_record("cine_tramo", "seedance25_hf", costo, 1,
                                note=f"cine: película Seedance 2.5 de {tr['seg']} s ({'con' if con_caras else 'sin'} caras, "
                                     f"Higgsfield {res.get('_request_id', '')})")
            return costo
        else:
            raise RuntimeError(f"No encontré Seedance 2.5 en Higgsfield ({ultimo[:160]}). Si sabés la ruta del modelo, "
                               "cargala en la variable HIGGSFIELD_SEEDANCE25.")
    raise RuntimeError("Seedance rechazó las referencias (filtro de ByteDance; un pedido rechazado normalmente no se "
                       f"cobra): {ultimo[:200]}. Usá la película con Kling O3.")


async def _montar(p: Dict[str, Any]) -> None:
    cid = p["id"]
    trs = _tramos(p)
    clips = [_tramo(cid, t["id"]) for t in trs]
    # LA PLACA FINAL: tu logo grande que entra creciendo y el título (ej. SUMMER SEASON 2027).
    placa = None
    if p.get("placa", True) is not False:
        ci = p.get("cierre") or {}
        lp = motion.logo_path(_pfx())
        cruda = _dir(cid) / "placa_cruda.mp4"
        ok = await asyncio.to_thread(motion.placa_animada, cruda, "9:16", (ci.get("titulo") or p.get("titulo") or "").upper(),
                                     ci.get("linea") or "", lp if lp.exists() else None, "elegante", "#000000", "#FFFFFF", 3.2)
        if ok and cruda.exists():
            placa = _dir(cid) / "placa.mp4"
            await asyncio.to_thread(_film._normalizar, cruda, placa, None, 0.0, "centro")
            clips = clips + [placa]
    falso: Dict[str, Any] = {"id": cid, "modo": "fondo", "ritmo": "segundo", "guion": p.get("guion") or "",
                             "voz": "", "tono": "cercana", "energia": _film.ENERGIA_FILMADO, "mic": False,
                             "tomas": [{"cartel": "", "dice": ""} for _ in clips],
                             "edicion": {"carteles": False, "zoom": False, "foto_producto": False,
                                         "cierre": p.get("cierre_on", True) is not False and placa is None,
                                         "subtitulos": bool(p.get("subtitulos", False))},
                             "motion": {"subs": "palabras", "fuente": "moderna"}}
    falso["cierre"] = _film._limpiar_cierre(p.get("cierre"), {"info": {"precio": "", "producto": p.get("titulo") or ""}})
    voz: Optional[Tuple[Path, float]] = None
    if falso["guion"].strip() and p.get("voz_pid"):
        try:
            doc = await _doc(p["voz_pid"])
            v = _dir(cid) / "voz.mp3"
            await _film._voz(doc, falso, falso["guion"], v)
            voz = (v, _duracion_video(v))
        except Exception:
            voz = None
    tempo = 1.0
    if voz:
        dv = sum(_duracion_video(c) for c in clips)
        tempo = max(1.0, min(_film.GUION_TEMPO_MAX, (voz[1] + _film.GUION_INICIO + 0.3) / max(1.0, dv)))
        falso["_guion_seg"] = round(voz[1] / tempo, 2)
    durs = [_duracion_video(c) for c in clips]
    texto = _film._armar_ass_filmado(falso, durs)
    ass = ""
    if "Dialogue:" in texto:
        (_dir(cid) / "cine.ass").write_text(texto, encoding="utf-8")
        ass = "cine.ass"
    await asyncio.to_thread(_film._unir, clips, _final(cid), _film.LOOK_DEFAULT, "motor", ["corte"] * len(clips),
                            ["no"] * len(clips), ass, [], _dir(cid))
    if voz:
        await asyncio.to_thread(_film._poner_voz, _final(cid), voz[0], tempo)
    await asyncio.to_thread(_sb._look_cine, _final(cid))
    lp = motion.logo_path(_pfx())
    if p.get("logo") and lp.exists() and placa is None:
        out = _final(cid).with_name("pelicula_logo.mp4")
        if motion.con_logo(_final(cid), out, lp, "9:16", "abajo_der"):
            out.replace(_final(cid))


# ─────────────────────────────────────────────────────────────────────────────
# TRABAJOS
# ─────────────────────────────────────────────────────────────────────────────

async def _job_director(jid: str, cid: str) -> str:
    await _job_set(jid, {"paso": "El director lee la idea y prepara a tu elenco…"})
    p = await _pr(cid)
    elenco = await _elenco(p)
    if not elenco:
        raise RuntimeError("Elegí al menos un personaje con retrato aprobado.")
    await _job_set(jid, {"paso": "El director escribe la película: el mundo, el set, el elenco y el recorrido de la cámara…"})
    plan, costo = await _director(p, elenco)
    await budget_record("cine_claude", _claude.MODELO, costo, 1, note="cine: el director")
    if len(plan["puntos"]) < 2:
        raise RuntimeError("El director no devolvió un recorrido usable: probá de nuevo.")
    # Si el cuadro nombra a alguien del elenco que no figura como visible, se suma (así lleva sus referencias).
    for pt in plan["puntos"]:
        texto = (pt.get("en", "") + " " + pt.get("es", "")).lower()
        for i, x in enumerate(elenco):
            if x.nombre.lower() in texto and i not in pt["personajes"]:
                pt["personajes"].append(i)
    async with _lock(cid):
        p = await _pr(cid)
        for viejo in p.get("puntos") or []:
            await kv.delete(_k_img(cid, viejo["id"]))
            await kv.delete(_k_guia(cid, viejo["id"]))
            _tramo(cid, viejo["id"]).unlink(missing_ok=True)
        _final(cid).unlink(missing_ok=True)
        p.update(plan)
        await _guardar(p)
    return ""


async def _job_dibujar(jid: str, cid: str, pids: List[str], correccion: str) -> str:
    p = await _pr(cid)
    await _job_set(jid, {"paso": "Preparando las caras HD y las prendas del elenco…"})
    elenco = await _elenco(p)
    pts = [x for x in p["puntos"] if x["id"] in pids]
    fallas: List[str] = []
    st = await get_settings()
    umbral = int(st.get("qc_umbral", 7) or 7)
    # En orden: cada punto mira el anterior ya dibujado (el mismo mundo, la misma luz).
    for n, pt in enumerate(pts, 1):
        await _job_set(jid, {"paso": f"Dibujando el punto {p['puntos'].index(pt) + 1} ({n} de {len(pts)})…"})
        k = p["puntos"].index(pt)
        previo = await kv.get(_k_img(cid, p["puntos"][k - 1]["id"])) if k > 0 and _camara_parecida(p["puntos"][k - 1], pt) else None
        try:
            b64, nota, qc = await _dibujar_punto(p, pt, elenco, previo, correccion if len(pts) == 1 else "")
            if qc and int(qc.get("puntaje", 10)) < umbral and qc.get("diferencias"):
                b2, nota2, qc2 = await _dibujar_punto(p, pt, elenco, previo, "; ".join(qc["diferencias"]))
                if qc2 is None or int(qc2.get("puntaje", 0)) >= int(qc.get("puntaje", 0)):
                    b64, nota, qc = b2, nota2, dict(qc2 or qc, corregido=True)
            await kv.set(_k_img(cid, pt["id"]), b64)
            cambios = {"img": True, "aprobado": False, "nota": nota, "error": "", "ts": int(time.time()),
                       "qc": ({"puntaje": int(qc.get("puntaje", 0)), "diferencias": (qc.get("diferencias") or [])[:6],
                               "ok": int(qc.get("puntaje", 0)) >= umbral} if qc else None)}
        except Exception as e:
            fallas.append(str(k + 1))
            cambios = {"error": str(getattr(e, "detail", "") or e)[:300]}
        async with _lock(cid):
            fresco = await _pr(cid)
            for x in fresco["puntos"]:
                if x["id"] == pt["id"]:
                    x.update(cambios)
            await _guardar(fresco)
        p = await _pr(cid)
    return ("No salieron los puntos " + ", ".join(fallas) + ": tocá ↻ en esos.") if fallas else ""


async def _job_filmar(jid: str, cid: str) -> str:
    p = await _pr(cid)
    key = await _fal_key()
    if not key:
        raise RuntimeError("Falta la API key de fal.")
    elenco = await _elenco(p)
    trs = [t for t in _tramos(p) if not _tramo(cid, t["id"]).exists()]
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    subidas: Dict[str, str] = {}
    fallas: List[str] = []
    hechos = [0]
    sem = asyncio.Semaphore(3)
    if _una_pelicula(p):
        todas = _tramos(p)
        async with httpx.AsyncClient(timeout=300) as cli:
            for n, tr in enumerate(todas):
                if _tramo(cid, tr["id"]).exists():
                    continue
                await _job_set(jid, {"paso": f"Filmando la película: toma {n + 1} de {len(todas)} ({tr['seg']} s, de corrido)…"})
                if n == 0:
                    inicio = await kv.get(_k_img(cid, tr["de"]["id"]))
                else:
                    inicio = await asyncio.to_thread(_ultimo_cuadro, _tramo(cid, todas[n - 1]["id"]))
                if not inicio:
                    raise RuntimeError("Falta el primer cuadro (dibujalo y aprobalo).")
                await _filmar_pelicula(cli, key, headers, jid, p, tr, elenco, inicio, n == 0)
        await _job_set(jid, {"paso": "Color de cine, la voz y la placa final…"})
        await _montar(p)
        link = await _guardar_en_drive(f"cine-{cid}.mp4", _final(cid).read_bytes(), "video/mp4")
        async with _lock(cid):
            fresco = await _pr(cid)
            fresco["video"], fresco["drive"] = True, link
            await _guardar(fresco)
        return ""
    async with httpx.AsyncClient(timeout=300) as cli:
        async def uno(tr: Dict[str, Any]) -> None:
            async with sem:
                try:
                    await _filmar_tramo(cli, key, headers, jid, p, tr, elenco, subidas)
                except Exception as e:
                    fallas.append(f"{p['puntos'].index(tr['de']) + 1}: {str(getattr(e, 'detail', '') or e)[:160]}")
                hechos[0] += 1
                await _job_set(jid, {"paso": f"Filmando el vuelo: {hechos[0]} de {len(trs)} tramos…"})
        await asyncio.gather(*(uno(t) for t in trs))
    if fallas:
        raise RuntimeError("No salieron estos tramos: " + " · ".join(fallas) + ". Los que salieron quedaron: tocá Filmar de nuevo.")
    await _job_set(jid, {"paso": "Uniendo el vuelo, color de cine, la voz y el cartel final…"})
    await _montar(p)
    link = await _guardar_en_drive(f"cine-{cid}.mp4", _final(cid).read_bytes(), "video/mp4")
    async with _lock(cid):
        fresco = await _pr(cid)
        fresco["video"], fresco["drive"] = True, link
        await _guardar(fresco)
    return ""


async def _job_montar(jid: str, cid: str) -> str:
    p = await _pr(cid)
    if not all(_tramo(cid, t["id"]).exists() for t in _tramos(p)):
        raise RuntimeError("Faltan tramos filmados.")
    await _job_set(jid, {"paso": "Uniendo el vuelo, color de cine, la voz y el cartel final…"})
    await _montar(p)
    async with _lock(cid):
        fresco = await _pr(cid)
        fresco["video"] = True
        await _guardar(fresco)
    return ""


async def _lanzar(p: Dict[str, Any], tipo: str, estimado: int, fn, *args) -> str:
    jid = _uuid.uuid4().hex[:10]
    pid = ((p.get("elenco") or [{}])[0] or {}).get("pid") or "cine"
    await _job_nuevo(jid, pid, "cine_" + tipo, estimado, {"titulo": "Cine", "cid": p["id"]})
    async with _lock(p["id"]):
        fresco = await _pr(p["id"])
        fresco["job"] = jid
        await _guardar(fresco)
    _spawn(_sb._correr(jid, CURRENT_SUB.get(), fn, p["id"], *args))
    return jid


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

def _vista(p: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(p)
    pts = []
    for x in p.get("puntos") or []:
        y = dict(x)
        y["url"] = f"{API}/{p['id']}/punto/{x['id']}.jpg?t={x.get('ts', 0)}" if x.get("img") else ""
        y["guia_url"] = f"{API}/{p['id']}/punto/{x['id']}/guia.jpg?t={x.get('guia_ts', 0)}" if x.get("guia") else ""
        pts.append(y)
    out["puntos"] = pts
    trs = _tramos(p)
    mvid = p.get("motor_video") if p.get("motor_video") in MOTORES_VIDEO else "pelicula_kling"
    precio = (PRECIOS_SEEDANCE.get(p.get("resolucion") or "720p", PRECIO_SEEDANCE_HF) if mvid == "seedance25" else
              _sb.MOTORES_VIDEO["hf_kling3_pro"]["precio_seg"] if mvid == "pelicula_hf_kling3" else
              PRECIO_KLING_O3_HF if mvid == "pelicula_hf_o3" else _film.MOTORES[_sb.MOTOR_VIDEO]["precio_seg"] if mvid == "pelicula_kling" else _sb.MOTORES_VIDEO[mvid]["precio_seg"] if mvid.startswith("hf_")
              else _film.MOTORES[_sb.MOTOR_VIDEO]["precio_seg"])
    out["tramos"] = [{"id": t["id"], "seg": t["seg"], "continuo": bool(t["a"]), "hecho": _tramo(p["id"], t["id"]).exists()} for t in trs]
    out["una_pelicula"] = _una_pelicula(p)
    out["precio_desde"] = mvid == "seedance25" and (p.get("resolucion") or "720p") == "1080p"
    out["resolucion"] = p.get("resolucion") or "720p"
    out["costo_puntos"] = round(sum(1 for x in (pts[:1] if out["una_pelicula"] else pts) if not x.get("img")) *
                                (0.011 if p.get("motor_cuadros") == "hf_marketing" else 0.10), 2)
    out["motor_cuadros"] = p.get("motor_cuadros") or "gemini"
    out["motor_video"] = mvid
    out["costo_video"] = round(sum(t["seg"] * precio for t in trs if not _tramo(p["id"], t["id"]).exists()), 2)
    out["seg_total"] = sum(t["seg"] for t in trs)
    out["video_url"] = f"{API}/{p['id']}/pelicula.mp4?t={p.get('actualizado', '')}" if _final(p["id"]).exists() else ""
    return out


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(PAGINA.replace("%%API%%", API).replace("%%VERSION%%", VERSION).replace("%%PREFIJO%%", ROUTE_PREFIX))


@router.get(ROUTE_PREFIX + "/set", response_class=HTMLResponse)
async def ui_set() -> HTMLResponse:
    return HTMLResponse(PAGINA_SET.replace("%%API%%", API))


@router.get(API + "/config")
async def api_config() -> Dict[str, Any]:
    pjs = []
    for pid in await _indice():
        try:
            d = await _doc(pid)
        except Exception:
            continue
        if (d.get("hoja") or {}).get("retrato"):
            pjs.append({"id": pid, "nombre": d.get("nombre") or "Personaje"})
    prendas = [{"id": x["id"], "nombre": x.get("nombre", ""), "colores": [c.get("nombre", "") for c in x.get("colores") or []]}
               for x in await refs_luma.prendas()]
    return {"personajes": pjs, "prendas": prendas, "duraciones": DURACIONES, "tipos": TIPOS,
            "motores_cuadro": {k: v for k, v in MOTORES_CUADRO.items() if not k.startswith("hf_") or hf.disponible()},
            "motores_video": {k: v for k, v in MOTORES_VIDEO.items()
                              if not (k.startswith("hf_") or k in ("seedance25", "pelicula_hf_kling3", "pelicula_hf_o3"))
                              or hf.disponible()},
            "higgsfield": hf.disponible(), "logo": motion.logo_path(_pfx()).exists(), "resoluciones": RESOLUCIONES,
            "precio_seg": _film.MOTORES[_sb.MOTOR_VIDEO]["precio_seg"], "claude": _claude.disponible()}


@router.get(API + "/lista")
async def api_lista() -> Dict[str, Any]:
    out = []
    for cid in (await kv.get(_k_idx())) or []:
        p = await kv.get(_k(cid))
        if isinstance(p, dict):
            out.append({"id": cid, "titulo": p.get("titulo") or "Película", "creado": p.get("creado", ""),
                        "video": _final(cid).exists()})
    return {"peliculas": out}


@router.post(API + "/nueva")
async def api_nueva(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    idea = _texto(payload.get("idea"), 2000)
    if len(idea) < 10:
        raise HTTPException(400, "Contá la idea (aunque sea en dos líneas).")
    elenco = []
    for e in (payload.get("elenco") or [])[:MAX_ELENCO]:
        if not isinstance(e, dict) or not e.get("pid"):
            continue
        try:
            await _doc(str(e["pid"]))
        except Exception:
            continue
        pr = await refs_luma.prenda(str(e.get("prenda") or "")) if e.get("prenda") else None
        try:
            color = max(0, min(len((pr or {}).get("colores") or [1]) - 1, int(e.get("color") or 0)))
        except (TypeError, ValueError):
            color = 0
        elenco.append({"pid": str(e["pid"]), "prenda": pr["id"] if pr else "", "color": color})
    if not elenco:
        raise HTTPException(400, "Elegí al menos un personaje para el elenco.")
    if not _claude.disponible():
        raise HTTPException(400, "El director (Claude) no está disponible ahora.")
    dur = int(payload.get("duracion")) if str(payload.get("duracion") or "").isdigit() and int(payload["duracion"]) in DURACIONES else 20
    cid = "c" + _uuid.uuid4().hex[:9]
    p = {"id": cid, "idea": idea, "tipo": payload.get("tipo") if payload.get("tipo") in TIPOS else "comercial",
         "duracion": dur, "elenco": elenco, "voz_pid": elenco[0]["pid"] if payload.get("voz", True) is not False else "",
         "titulo": "Película", "puntos": [], "creado": time.strftime("%Y-%m-%d %H:%M"), "cierre_on": True}
    await _guardar(p)
    await kv.set(_k_idx(), [cid] + [x for x in ((await kv.get(_k_idx())) or []) if x != cid][:40])
    jid = await _lanzar(p, "director", 150, _job_director)
    return {"pelicula": _vista(await _pr(cid)), "job": jid}


@router.get(API + "/job/{jid}")
async def api_job(jid: str) -> Dict[str, Any]:
    j = await kv.get(_k_job(jid))
    if not isinstance(j, dict):
        raise HTTPException(404, "Ese trabajo no existe.")
    j = await _revisar_job(j)
    return {k: j.get(k) for k in ("estado", "paso", "error", "aviso", "inicio")}


@router.get(API + "/{cid}")
async def api_get(cid: str) -> Dict[str, Any]:
    return {"pelicula": _vista(await _pr(cid))}


@router.put(API + "/{cid}")
async def api_editar(cid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    async with _lock(cid):
        p = await _pr(cid)
        for k, tope in (("guion", 2000), ("titulo", 80), ("mundo_en", 600), ("idea", 2000)):
            if k in payload:
                p[k] = _texto(payload[k], tope)
        if payload.get("luz") in LUCES:
            p["luz"] = payload["luz"]
        if payload.get("motor_cuadros") in MOTORES_CUADRO:
            p["motor_cuadros"] = payload["motor_cuadros"]
        if payload.get("motor_video") in MOTORES_VIDEO:
            p["motor_video"] = payload["motor_video"]
        if payload.get("resolucion") in RESOLUCIONES:
            p["resolucion"] = payload["resolucion"]
        for k in ("cierre_on", "logo", "subtitulos", "placa"):
            if k in payload:
                p[k] = payload[k] is not False
        if isinstance(payload.get("cierre"), dict):
            p["cierre"] = {"titulo": _texto(payload["cierre"].get("titulo"), 40), "linea": _texto(payload["cierre"].get("linea"), 60)}
        _final(cid).unlink(missing_ok=True)
        await _guardar(p)
    return {"pelicula": _vista(p)}


@router.post(API + "/logo")
async def api_logo(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Tu logo (el mismo de Comerciales, Reels y Videos): para la placa final."""
    try:
        data = base64.b64decode(str(payload.get("imagen") or "").split(",")[-1])
    except Exception:
        raise HTTPException(400, "No pude leer esa imagen.")
    if not data or len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "Subí una imagen de hasta 10 MB.")
    try:
        await asyncio.to_thread(motion.guardar_logo, _pfx(), data)
    except Exception as e:
        raise HTTPException(400, f"No pude leer esa imagen como logo: {e}")
    return {"ok": True}


@router.get(API + "/logo.png")
async def api_logo_ver():
    lp = motion.logo_path(_pfx())
    if not lp.exists():
        raise HTTPException(404, "Todavía no subiste tu logo.")
    return FileResponse(str(lp), media_type="image/png")


@router.post(API + "/{cid}/director")
async def api_director(cid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    p = await _pr(cid)
    if payload.get("idea"):
        await api_editar(cid, {"idea": payload["idea"]})
        p = await _pr(cid)
    return {"job": await _lanzar(p, "director", 150, _job_director)}


@router.put(API + "/{cid}/punto/{pid}")
async def api_punto(cid: str, pid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    async with _lock(cid):
        p = await _pr(cid)
        x = next((y for y in p["puntos"] if y["id"] == pid), None)
        if not x:
            raise HTTPException(404, "No encuentro ese punto.")
        if "es" in payload and _texto(payload["es"], 300) != x.get("es"):
            x["es"] = _texto(payload["es"], 300)
            x["en"] = _texto(payload.get("en"), 600) or x["es"]
            x["aprobado"] = False
            x["desactualizado"] = bool(x.get("img"))
        if "mov_en" in payload:
            x["mov_en"] = _texto(payload["mov_en"], 400)
        if "corte" in payload and p["puntos"].index(x) > 0:
            x["corte"] = bool(payload["corte"])
        for t in _tramos(p):
            _tramo(cid, t["id"]).unlink(missing_ok=True) if pid in (t["id"], (t["a"] or {}).get("id")) else None
        _final(cid).unlink(missing_ok=True)
        await _guardar(p)
    return {"pelicula": _vista(p)}


@router.post(API + "/{cid}/dibujar")
async def api_dibujar(cid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    p = await _pr(cid)
    pids = [x for x in (payload.get("pids") or []) if any(y["id"] == x for y in p["puntos"])]
    if not pids:
        base = p["puntos"][:1] if _una_pelicula(p) else p["puntos"]
        pids = [y["id"] for y in base if not y.get("img") or y.get("desactualizado")]
    if not pids:
        raise HTTPException(400, "Ya están todos dibujados (para rehacer uno, ↻ en ese punto).")
    async with _lock(cid):
        p = await _pr(cid)
        for y in p["puntos"]:
            if y["id"] in pids:
                y.pop("desactualizado", None)
        await _guardar(p)
    return {"job": await _lanzar(p, "dibujar", 40 + 45 * len(pids), _job_dibujar, pids, _texto(payload.get("correccion"), 400))}


@router.post(API + "/{cid}/punto/{pid}/aprobar")
async def api_aprobar(cid: str, pid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    async with _lock(cid):
        p = await _pr(cid)
        for y in p["puntos"]:
            if (y["id"] == pid or pid == "todos") and y.get("img"):
                y["aprobado"] = payload.get("ok") is not False
        await _guardar(p)
    return {"pelicula": _vista(p)}


@router.get(API + "/{cid}/punto/{pid}.jpg")
async def api_img(cid: str, pid: str):
    b = await kv.get(_k_img(cid, pid))
    if not b:
        raise HTTPException(404, "Ese punto no tiene dibujo.")
    return Response(base64.b64decode(b), media_type="image/jpeg")


@router.get(API + "/{cid}/punto/{pid}/guia.jpg")
async def api_guia(cid: str, pid: str):
    b = await kv.get(_k_guia(cid, pid))
    if not b:
        raise HTTPException(404, "Ese punto no tiene guía 3D.")
    return Response(base64.b64decode(b), media_type="image/jpeg")


@router.post(API + "/{cid}/punto/{pid}/guia")
async def api_guia_guardar(cid: str, pid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    img = str(payload.get("imagen") or "").split(",")[-1]
    try:
        b64 = _compress_ref(base64.b64decode(img), max_dim=1280, q=90)
    except Exception:
        raise HTTPException(400, "La foto de la escena no se pudo leer.")
    async with _lock(cid):
        p = await _pr(cid)
        x = next((y for y in p["puntos"] if y["id"] == pid), None)
        if not x:
            raise HTTPException(404, "No encuentro ese punto.")
        await kv.set(_k_guia(cid, pid), b64)
        x.update({"guia": True, "guia_ts": int(time.time()), "aprobado": False})
        if x.get("img"):
            x["desactualizado"] = True
        await _guardar(p)
    return {"ok": True}


@router.get(API + "/{cid}/set")
async def api_set(cid: str) -> Dict[str, Any]:
    p = await _pr(cid)
    nombres = []
    for e in p.get("elenco") or []:
        try:
            nombres.append((await _doc(e["pid"])).get("nombre") or "Personaje")
        except Exception:
            nombres.append("Personaje")
    return {"pelicula": _vista(p), "nombres": nombres, "objetos": list(OBJETOS), "luces": list(LUCES)}


@router.post(API + "/{cid}/set")
async def api_set_guardar(cid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Lo que se movió en el set 3D: objetos, personajes y la cámara de cada punto."""
    async with _lock(cid):
        p = await _pr(cid)
        if isinstance(payload.get("objetos"), list):
            p["objetos"] = _limpiar_plan({"objetos": payload["objetos"]}, int(p.get("duracion") or 20), 0)["objetos"]
        if isinstance(payload.get("personajes"), list):
            p["personajes"] = [x for x in payload["personajes"][:MAX_ELENCO] if isinstance(x, dict)]
        if isinstance(payload.get("puntos"), dict):
            for y in p["puntos"]:
                q = payload["puntos"].get(y["id"])
                if isinstance(q, dict) and isinstance(q.get("cam"), list) and isinstance(q.get("mira"), list):
                    y["cam"] = [_num(v, -3000, 3000, 0) for v in q["cam"][:3]]
                    y["mira"] = [_num(v, -3000, 3000, 0) for v in q["mira"][:3]]
                    y["lente"] = int(_num(q.get("lente"), 12, 135, y.get("lente") or 35))
                    if isinstance(q.get("poses"), dict):
                        y["poses"] = q["poses"]
        if payload.get("luz") in LUCES:
            p["luz"] = payload["luz"]
        await _guardar(p)
    return {"ok": True}


@router.post(API + "/{cid}/filmar")
async def api_filmar(cid: str) -> Dict[str, Any]:
    p = await _pr(cid)
    if not p.get("puntos"):
        raise HTTPException(400, "Primero el director.")
    pts = p["puntos"][:1] if _una_pelicula(p) else p["puntos"]
    sin = [str(i + 1) for i, y in enumerate(pts) if not (y.get("img") and y.get("aprobado"))]
    if sin:
        raise HTTPException(400, ("Primero aprobá el primer cuadro." if _una_pelicula(p) else
                                  f"Primero aprobá todos los puntos (faltan: {', '.join(sin)}).") + " Nada se filma sin aprobar.")
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal.")
    await _cobrar(_vista(p)["costo_video"])
    return {"job": await _lanzar(p, "filmar", 120 + 50 * len(_tramos(p)), _job_filmar)}


@router.post(API + "/{cid}/montar")
async def api_montar(cid: str) -> Dict[str, Any]:
    return {"job": await _lanzar(await _pr(cid), "montar", 90, _job_montar)}


@router.get(API + "/{cid}/pelicula.mp4")
async def api_video(cid: str):
    await _pr(cid)
    if not _final(cid).exists():
        raise HTTPException(404, "Todavía no está filmada.")
    return FileResponse(str(_final(cid)), media_type="video/mp4", filename=f"cine-{cid}.mp4")


@router.delete(API + "/{cid}")
async def api_borrar(cid: str) -> Dict[str, Any]:
    p = await _pr(cid)
    for y in p.get("puntos") or []:
        await kv.delete(_k_img(cid, y["id"]))
        await kv.delete(_k_guia(cid, y["id"]))
    await kv.delete(_k(cid))
    await kv.set(_k_idx(), [x for x in ((await kv.get(_k_idx())) or []) if x != cid])
    return {"ok": True}


from cine_paginas import PAGINA, PAGINA_SET  # noqa: E402  (las páginas, aparte: son largas)
PAGINA_SET = PAGINA_SET.replace("%%COMUN%%", MANIQUI_JS)
