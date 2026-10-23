# -*- coding: utf-8 -*-
"""LUMEN: el programa 3D de Studio Luma (tipo Blender, pero guiado y en castellano) y el
CELULAR COMO CÁMARA. Sirve para todo: películas de Cine, reels, comerciales, storyboards,
fotos con modelo.

- Proyectos sueltos (/lumen): cada uno con su formato (9:16, 16:9, 1:1, 4:5), su duración,
  su escena y sus TOMAS (momentos marcados en la línea de tiempo que se guardan como imagen
  y se mandan como guía de cámara y pose a cualquier parte del programa).
- El editor (/lumen/editor?id=… o ?cine=<película>): formas, maniquíes, modelos 3D de la
  biblioteca gratis de Poly Haven (CC0) o subidos (.glb/.gltf/.fbx/.obj), luces reales con
  sombras, cielos HDRI, cámara con lente en mm, línea de tiempo con claves, animatic.
- El celular (/lumen-cel/{código}): se abre escaneando un QR, sin login. Manda su orientación
  (giroscopio) —y en Android con AR, también su posición— por WebSocket: movés el celular y la
  cámara de la escena se mueve igual; ve lo que ve la cámara y graba recorridos.

Con una película de Cine, la escena se guarda en la película (p["escena3d"]) y la cámara de
cada punto como siempre (cam/mira/lente), así el resto de Cine no cambia.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import secrets
import urllib.parse
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

import httpx
from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from imagenes_ia import _pfx, kv, session_sub_from_request, set_current_sub

VERSION = "1.4.0"
PREF = "/lumen"
API = PREF + "/api"
CEL = "/lumen-cel"         # abierto (sin login): lo abre el celular con el código del QR

router = APIRouter()

# ── MODELOS SUBIDOS ──────────────────────────────────────────────────────────
_EXT_OK = {"glb": "model/gltf-binary", "gltf": "model/gltf+json", "fbx": "application/octet-stream",
           "obj": "text/plain"}
_MAX_MODELO = 80 * 1024 * 1024            # 80 MB
_MAX_KV = 8 * 1024 * 1024                 # copia de respaldo en Redis hasta 8 MB


def _dueno_ok(d: str) -> str:
    if not re.fullmatch(r"(cine-)?[0-9a-zA-Z]{4,40}", d or ""):
        raise HTTPException(400, "Proyecto inválido.")
    return d


def _dir_modelos(dueno: str) -> Path:
    from personajes import PJ_DIR
    d = PJ_DIR.parent / "lumen" / (_pfx().replace(":", "-").strip("-") or "x") / _dueno_ok(dueno) / "modelos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _k_modelo(dueno: str, mid: str) -> str:
    return _pfx() + f"lumen:{dueno}:modelo:{mid}"


@router.post(API + "/{cid}/modelo")
async def api_subir_modelo(cid: str, request: Request, nombre: str = "") -> Dict[str, Any]:
    """Sube un modelo 3D (el cuerpo del pedido es el archivo tal cual)."""
    set_current_sub(session_sub_from_request(request))
    ext = (nombre.rsplit(".", 1)[-1] if "." in nombre else "").lower()
    if ext not in _EXT_OK:
        raise HTTPException(400, "Formato no soportado: usá .glb, .gltf, .fbx u .obj.")
    datos = await request.body()
    if not datos:
        raise HTTPException(400, "El archivo llegó vacío.")
    if len(datos) > _MAX_MODELO:
        raise HTTPException(413, "El modelo pesa más de 80 MB: bajale la calidad o probá con .glb.")
    mid = secrets.token_hex(6)
    (_dir_modelos(cid) / f"{mid}.{ext}").write_bytes(datos)
    if len(datos) <= _MAX_KV:
        # el disco de Railway se borra al redeployar: una copia en Redis lo recupera
        try:
            await kv.set(_k_modelo(cid, mid), {"ext": ext, "b64": base64.b64encode(datos).decode()})
        except Exception:
            pass
    return {"ok": True, "id": mid, "ext": ext, "url": f"{API}/{cid}/modelo/{mid}.{ext}",
            "nombre": re.sub(r"\.[^.]+$", "", nombre)[:60] or "Modelo"}


@router.get(API + "/{cid}/modelo/{archivo}")
async def api_modelo(cid: str, archivo: str, request: Request) -> Response:
    set_current_sub(session_sub_from_request(request))
    m = re.fullmatch(r"([0-9a-f]{12})\.([a-z]{3,4})", archivo)
    if not m or m.group(2) not in _EXT_OK:
        raise HTTPException(404, "No existe ese modelo.")
    f = _dir_modelos(cid) / archivo
    if not f.exists():
        d = await kv.get(_k_modelo(cid, m.group(1)))
        if not (isinstance(d, dict) and d.get("b64")):
            raise HTTPException(404, "Ese modelo ya no está en el servidor (se borró al actualizar): volvé a subirlo.")
        f.write_bytes(base64.b64decode(d["b64"]))
    return Response(f.read_bytes(), media_type=_EXT_OK[m.group(2)],
                    headers={"Cache-Control": "private, max-age=86400"})


# ── PROYECTOS ────────────────────────────────────────────────────────────────
FORMATOS = {"9:16": "Vertical 9:16 (reels, historias, Cine)", "16:9": "Horizontal 16:9 (cine, YouTube)",
            "1:1": "Cuadrado 1:1", "4:5": "Retrato 4:5 (feed de Instagram, fotos)"}
MAX_PROYECTOS = 200

# ── EL ASISTENTE DEL MUNDO ───────────────────────────────────────────────────
# Antes de abrir el editor, el asistente pregunta qué se va a hacer, dónde, quiénes, con qué
# objetos y con qué cámara; el editor arma la escena con esas respuestas (escenaDeMundo).
# Lo mismo lo leen las dos pantallas. Posiciones en metros: la gente en el centro, la cámara
# del lado +Z y el fondo hacia -Z. "fijos" = [forma, x, y, z, giro°, escala]; "suelo" = altura
# donde se paran; "sugeridos" = los objetos que el asistente ofrece ya tildados.
MUNDO = {
    "tipos": {
        "pelicula": {"n": "🎞 Película / corto", "formato": "16:9", "dur": 20},
        "reel": {"n": "📱 Reel / historia", "formato": "9:16", "dur": 15},
        "comercial": {"n": "📺 Comercial", "formato": "9:16", "dur": 15},
        "fotos": {"n": "📸 Fotos (poses y cámara)", "formato": "4:5", "dur": 6},
        "storyboard": {"n": "🗂 Storyboard", "formato": "9:16", "dur": 15},
    },
    "lugares": {
        "playa": {"n": "🏖 Playa", "cielo": "atardecer", "suelo": 0,
                  "fijos": [["mar", 0, 0, 0, 0, 1], ["arena", 0, -0.24, 0, 0, 1]],
                  "sugeridos": ["sombrilla", "reposera", "palmera", "tabla_surf"]},
        "isla": {"n": "🏝 Isla", "cielo": "atardecer", "suelo": 0,
                 "fijos": [["mar", 0, 0, 0, 0, 1], ["isla", 0, 0, 0, 0, 1]],
                 "sugeridos": ["palmera", "roca", "barco"]},
        "yate": {"n": "🛥 Yate en el mar", "cielo": "mediodia", "suelo": 2.2,
                 "fijos": [["mar", 0, 0, 0, 0, 1], ["yate", 0, 0, -9, 0, 1]],
                 "sugeridos": ["reposera"]},
        "estudio": {"n": "💡 Estudio de fotos", "cielo": "estudio", "suelo": 0,
                    "fijos": [["piso", 0, 0, 0, 0, 1], ["pared", 0, 0, -3.5, 0, 2]],
                    "luces": "estudio", "sugeridos": ["escalon", "caja", "silla"]},
        "dormitorio": {"n": "🛏 Dormitorio", "cielo": "mediodia", "suelo": 0,
                       "fijos": [["piso", 0, 0, 0, 0, 1], ["pared", 0, 0, -3, 0, 1.2], ["pared", -3.6, 0, 0, 90, 1.2],
                                 ["cama", 1.7, 0, -1.8, 0, 1]],
                       "luces": "interior", "sugeridos": ["silla", "mesa"]},
        "living": {"n": "🛋 Living", "cielo": "mediodia", "suelo": 0,
                   "fijos": [["piso", 0, 0, 0, 0, 1], ["pared", 0, 0, -3.2, 0, 1.4], ["pared", 4, 0, -0.2, -90, 1.2]],
                   "luces": "interior", "sugeridos": ["mesa", "silla"]},
        "calle": {"n": "🏙 Calle / ciudad", "cielo": "mediodia", "suelo": 0,
                  "fijos": [["piso", 0, 0, 0, 0, 1], ["edificio", -15, 0, -20, 0, 1], ["edificio", 0, 0, -24, 0, 1],
                            ["edificio", 15, 0, -20, 0, 1]],
                  "sugeridos": ["auto"]},
        "campo": {"n": "🌳 Campo / bosque", "cielo": "amanecer", "suelo": 0,
                  "fijos": [["piso", 0, 0, 0, 0, 1], ["arbol", -6, 0, -8, 0, 1], ["arbol", 5, 0, -10, 0, 1.2],
                            ["arbol", -12, 0, -14, 0, 1], ["arbol", 11, 0, -5, 0, 0.9], ["arbol", 0, 0, -16, 0, 1.3]],
                  "sugeridos": ["roca"]},
        "vacio": {"n": "⬜ Vacío (lo armo yo)", "cielo": "mediodia", "suelo": 0, "fijos": [["piso", 0, 0, 0, 0, 1]],
                  "sugeridos": []},
    },
    "cielos": {"amanecer": "🌅 Amanecer", "mediodia": "☀ Mediodía", "atardecer": "🌇 Atardecer",
               "nublado": "☁ Nublado", "noche": "🌙 Noche", "estudio": "⬛ Estudio (fondo oscuro)"},
    "camaras": {
        "celular": {"n": "📱 Celular en mano", "h": 1.5, "lente": 26, "mov": "quieta"},
        "tripode": {"n": "📷 Trípode", "h": 1.55, "lente": 35, "mov": "quieta"},
        "gimbal": {"n": "🎥 Gimbal / steadicam", "h": 1.45, "lente": 35, "mov": "acercarse"},
        "cine": {"n": "🎬 Cámara de cine", "h": 1.4, "lente": 40, "mov": "lateral"},
        "dron": {"n": "🚁 Dron", "h": 1.8, "lente": 24, "mov": "dron"},
        "grua": {"n": "🏗 Grúa", "h": 1.5, "lente": 28, "mov": "grua"},
        "tele": {"n": "🔭 Teleobjetivo (de lejos)", "h": 1.6, "lente": 85, "mov": "quieta"},
    },
    "planos": {"general": "Plano general (todo el lugar)", "entero": "Cuerpo entero",
               "medio": "Plano medio (de la cintura)", "primer": "Primer plano (la cara)"},
    "movs": {"quieta": "Quieta", "acercarse": "Se acerca", "alejarse": "Se aleja", "alrededor": "Gira alrededor",
             "lateral": "Travelling de costado", "dron": "Baja desde el cielo (dron)", "grua": "Sube (grúa)"},
    "rondas": {"fila": "Uno al lado del otro", "ronda": "En ronda, mirándose", "profundidad": "Uno detrás del otro"},
}


def _k_idx() -> str:
    return _pfx() + "lumen:idx"


def _k_proy(pid: str) -> str:
    return _pfx() + f"lumen:proy:{pid}"


def _k_img(pid: str, tid: str) -> str:
    return _pfx() + f"lumen:img:{pid}:{tid}"


async def _proy(pid: str) -> Dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{10}", pid or ""):
        raise HTTPException(404, "No existe ese proyecto.")
    p = await kv.get(_k_proy(pid))
    if not isinstance(p, dict):
        raise HTTPException(404, "No existe ese proyecto (¿lo borraste?).")
    return p


def _resumen(p: Dict[str, Any]) -> Dict[str, Any]:
    tomas = [t for t in p.get("tomas") or [] if isinstance(t, dict)]
    con = [t for t in tomas if t.get("img")]
    return {"id": p["id"], "nombre": p.get("nombre") or "Proyecto", "formato": p.get("formato") or "9:16",
            "dur": p.get("dur") or 10, "tomas": len(tomas), "actualizado": p.get("actualizado") or "",
            "portada": (f"{API}/proyectos/{p['id']}/toma/{con[0]['id']}.jpg?t={con[0].get('ts', 0)}" if con else "")}


@router.get(API + "/proyectos")
async def api_proyectos(request: Request) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    out = []
    for pid in (await kv.get(_k_idx())) or []:
        p = await kv.get(_k_proy(pid))
        if isinstance(p, dict):
            out.append(_resumen(p))
    out.sort(key=lambda x: x["actualizado"], reverse=True)
    return {"proyectos": out, "formatos": FORMATOS}


@router.post(API + "/proyectos")
async def api_proyecto_nuevo(request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    idx = (await kv.get(_k_idx())) or []
    if len(idx) >= MAX_PROYECTOS:
        raise HTTPException(400, f"Hasta {MAX_PROYECTOS} proyectos: borrá alguno.")
    pid = secrets.token_hex(5)
    fmt = payload.get("formato") if payload.get("formato") in FORMATOS else "9:16"
    try:
        dur = max(3, min(180, int(payload.get("dur") or 10)))
    except (TypeError, ValueError):
        dur = 10
    p = {"id": pid, "nombre": str(payload.get("nombre") or "Proyecto nuevo").strip()[:80], "formato": fmt,
         "dur": dur, "escena3d": None, "tomas": [], "creado": time.strftime("%Y-%m-%d %H:%M"),
         "actualizado": time.strftime("%Y-%m-%d %H:%M:%S")}
    mundo = payload.get("mundo")
    if isinstance(mundo, dict) and mundo.get("lugar") in MUNDO["lugares"] and len(json.dumps(mundo)) < 30000:
        p["mundo"] = mundo        # las respuestas del asistente: el editor arma la escena la primera vez
    await kv.set(_k_proy(pid), p)
    await kv.set(_k_idx(), [pid] + idx)
    return {"ok": True, "proyecto": p}


@router.get(API + "/proyectos/{pid}")
async def api_proyecto(pid: str, request: Request) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    p = await _proy(pid)
    for t in p.get("tomas") or []:
        t["url"] = f"{API}/proyectos/{pid}/toma/{t['id']}.jpg?t={t.get('ts', 0)}" if t.get("img") else ""
    return {"proyecto": p, "formatos": FORMATOS}


_LOCKS: Dict[str, asyncio.Lock] = {}


@router.post(API + "/proyectos/{pid}")
async def api_proyecto_guardar(pid: str, request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    async with _LOCKS.setdefault(pid, asyncio.Lock()):
        p = await _proy(pid)
        if isinstance(payload.get("escena3d"), dict) and isinstance(payload["escena3d"].get("items"), list):
            if len(json.dumps(payload["escena3d"])) > 3_000_000:
                raise HTTPException(413, "La escena es demasiado grande para guardarla.")
            p["escena3d"] = payload["escena3d"]
            try:
                p["dur"] = max(3, min(180, int(payload["escena3d"].get("dur") or p.get("dur") or 10)))
            except (TypeError, ValueError):
                pass
        if isinstance(payload.get("tomas"), list):
            viejas = {t["id"]: t for t in p.get("tomas") or [] if isinstance(t, dict) and t.get("id")}
            nuevas = []
            for t in payload["tomas"][:60]:
                if not (isinstance(t, dict) and re.fullmatch(r"[0-9a-z]{3,12}", str(t.get("id") or ""))):
                    continue
                v = viejas.get(t["id"], {})
                nuevas.append({"id": t["id"], "nombre": str(t.get("nombre") or "Toma").strip()[:60],
                               "t": round(float(t.get("t") or 0), 2), "img": v.get("img", False), "ts": v.get("ts", 0),
                               "cam": t.get("cam") if isinstance(t.get("cam"), dict) else v.get("cam")})
            for tid in set(viejas) - {t["id"] for t in nuevas}:
                await kv.delete(_k_img(pid, tid))
            p["tomas"] = nuevas
        if str(payload.get("nombre") or "").strip():
            p["nombre"] = str(payload["nombre"]).strip()[:80]
        if payload.get("formato") in FORMATOS:
            p["formato"] = payload["formato"]
        p["actualizado"] = time.strftime("%Y-%m-%d %H:%M:%S")
        await kv.set(_k_proy(pid), p)
    return {"ok": True}


@router.delete(API + "/proyectos/{pid}")
async def api_proyecto_borrar(pid: str, request: Request) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    p = await _proy(pid)
    for t in p.get("tomas") or []:
        await kv.delete(_k_img(pid, t.get("id", "")))
    await kv.delete(_k_proy(pid))
    await kv.delete(_k_ref(pid))
    await kv.set(_k_idx(), [x for x in ((await kv.get(_k_idx())) or []) if x != pid])
    return {"ok": True}


@router.post(API + "/proyectos/{pid}/toma/{tid}")
async def api_toma_img(pid: str, tid: str, request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """La imagen de una toma (el render de la cámara en ese segundo) + dónde estaba la cámara."""
    set_current_sub(session_sub_from_request(request))
    img = str(payload.get("imagen") or "")
    if not img.startswith("data:image/"):
        raise HTTPException(400, "Falta la imagen.")
    b64 = img.split(",", 1)[1]
    if len(b64) > 6_000_000:
        raise HTTPException(413, "La imagen es demasiado grande.")
    async with _LOCKS.setdefault(pid, asyncio.Lock()):
        p = await _proy(pid)
        t = next((x for x in p.get("tomas") or [] if x.get("id") == tid), None)
        if not t:
            raise HTTPException(404, "No existe esa toma (guardá el proyecto primero).")
        await kv.set(_k_img(pid, tid), b64)
        t.update({"img": True, "ts": int(time.time())})
        if isinstance(payload.get("cam"), dict):
            t["cam"] = payload["cam"]
        p["actualizado"] = time.strftime("%Y-%m-%d %H:%M:%S")
        await kv.set(_k_proy(pid), p)
    return {"ok": True, "url": f"{API}/proyectos/{pid}/toma/{tid}.jpg?t={t['ts']}"}


@router.get(API + "/proyectos/{pid}/toma/{tid}.jpg")
async def api_toma_jpg(pid: str, tid: str, request: Request) -> Response:
    set_current_sub(session_sub_from_request(request))
    b64 = await kv.get(_k_img(pid, tid))
    if not b64:
        raise HTTPException(404, "Esa toma todavía no tiene imagen.")
    return Response(base64.b64decode(b64), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


async def toma_b64(pid: str, tid: str) -> Optional[str]:
    """Para los otros módulos: la imagen de una toma de Lumen (base64 JPEG) o None."""
    b = await kv.get(_k_img(pid, tid))
    return b if isinstance(b, str) and b else None


def describir_camara(c: Any) -> str:
    """La cámara de una toma en palabras (altura, distancia, inclinación y lente), para el prompt."""
    try:
        cx, cy, cz = (float(v) for v in c["cam"])
        mx, my, mz = (float(v) for v in c["mira"])
        lente = float(c.get("lente") or 35)
    except (KeyError, TypeError, ValueError):
        return ""
    horiz = math.hypot(mx - cx, mz - cz)
    dist = math.hypot(horiz, my - cy)
    incl = math.degrees(math.atan2(my - cy, horiz or 0.001))
    alt = ("casi desde el piso" if cy < 0.6 else "a la altura de la cadera" if cy < 1.2 else
           "a la altura de los ojos" if cy < 1.85 else "desde arriba" if cy < 4 else "desde muy arriba, como un dron")
    lejos = ("muy cerca" if dist < 1.5 else "cerca" if dist < 3 else "a media distancia" if dist < 6 else
             "lejos" if dist < 12 else "bien lejos")
    ang = ("mirando hacia arriba (contrapicado)" if incl > 8 else "mirando hacia abajo (picado)" if incl < -8
           else "derecha, sin inclinar")
    tipo = ("gran angular" if lente < 24 else "normal" if lente < 40 else "de retrato" if lente < 70 else "teleobjetivo")
    return (f"Cámara a {cy:.1f} m de altura ({alt}), a {dist:.1f} m de lo que mira ({lejos}), "
            f"{ang}, lente {lente:.0f} mm ({tipo}).")


async def toma_info(pid: str, tid: str) -> Optional[Dict[str, Any]]:
    """Todo lo que otro módulo necesita de una toma: imagen (base64 JPEG), formato, cámara
    (números y en palabras) y cuántas personas hay en la escena. None si no existe o no tiene imagen."""
    if not (re.fullmatch(r"[0-9a-f]{10}", str(pid or "")) and re.fullmatch(r"[0-9a-z]{3,12}", str(tid or ""))):
        return None
    p = await kv.get(_k_proy(pid))
    if not isinstance(p, dict):
        return None
    t = next((x for x in p.get("tomas") or [] if isinstance(x, dict) and x.get("id") == tid), None)
    img = await toma_b64(pid, tid) if t else None
    if not img:
        return None
    items = ((p.get("escena3d") or {}).get("items") or [])
    personas = sum(1 for x in items if isinstance(x, dict) and x.get("tipo") == "persona")
    return {"img": img, "nombre": t.get("nombre") or "Toma", "proyecto_nombre": p.get("nombre") or "Proyecto",
            "formato": p.get("formato") or "9:16", "cam": t.get("cam"), "camara": describir_camara(t.get("cam")),
            "personas": personas}


def leer_ref(payload: Any) -> Optional[tuple]:
    """El par (proyecto, toma) que mandan las pantallas como {"lumen": {"proyecto": …, "toma": …}}."""
    d = payload.get("lumen") if isinstance(payload, dict) else None
    if isinstance(d, dict) and d.get("proyecto") and d.get("toma"):
        return str(d["proyecto"]), str(d["toma"])
    return None


@router.get(API + "/tomas")
async def api_tomas(request: Request, formato: str = "") -> Dict[str, Any]:
    """MIS TOMAS: todas las tomas con imagen, de todos los proyectos (para elegir desde Fotos,
    Reels, Storyboard…)."""
    set_current_sub(session_sub_from_request(request))
    out = []
    for pid in (await kv.get(_k_idx())) or []:
        p = await kv.get(_k_proy(pid))
        if not isinstance(p, dict):
            continue
        for t in p.get("tomas") or []:
            if t.get("img") and (not formato or p.get("formato") == formato):
                out.append({"proyecto": pid, "proyecto_nombre": p.get("nombre") or "Proyecto", "id": t["id"],
                            "nombre": t.get("nombre") or "Toma", "formato": p.get("formato") or "9:16", "t": t.get("t", 0),
                            "camara": describir_camara(t.get("cam")),
                            "url": f"{API}/proyectos/{pid}/toma/{t['id']}.jpg?t={t.get('ts', 0)}"})
    return {"tomas": out}


# ── DESDE UN VIDEO O UNA FOTO: Lumen arma el mundo copiando tu referencia ─────
# El navegador saca cuadros del video (con su segundo); Claude los mira y devuelve la escena
# con las piezas de Lumen: el lugar, los objetos, cada persona con su recorrido (dónde está,
# para dónde mira y en qué pose, segundo a segundo) y la cámara (posición, a dónde mira y
# lente). Después se edita igual que cualquier proyecto. Los cuadros quedan como referencia.
MAX_CUADROS = 16
_SYS_VIDEO = """You are a film PREVIS artist. You get frames of the owner's own reference video (or one photo),
each labelled with its second. Rebuild it as a simple 3D blocking for a previs editor: the place, the objects, every
person with their path and poses over time, and the camera move. The goal is to REPRODUCE the same shot (framing,
camera move, where people walk and turn, their poses), not to describe the people.

COORDINATES (metres, y up, floor at y=0): at the FIRST frame the camera looks along -Z toward the scene; +X is the
camera's right; the main person starts near the origin. A person's "giro" is the direction they FACE, in degrees:
0 = facing +Z (toward the first camera position), 90 = facing +X (camera's right), 180 = back to the first camera,
-90 = facing -X. Estimate real distances from body size (an adult is ~1.65 m tall) and the lens from perspective.

PLACE (pick the closest): {lugares}. SKY / TIME: {cielos}.
OBJECTS you can place (only these keys): {formas}.
POSES (only these keys): {poses}.
CAMERA TYPES: {camaras}.

Return JSON:
{{"lugar": key, "cielo": key, "tipo_camara": key,
 "descripcion": "Spanish, one or two sentences: the real place, the light and what each person wears",
 "objetos": [{{"forma": key, "x": m, "z": m, "giro": deg, "escala": 1}}],
 "personas": [{{"ref": "Spanish: who this is, e.g. 'la chica de bikini turquesa'",
                "keys": [{{"t": s, "x": m, "z": m, "giro": deg, "pose": key}}]}}],
 "camara": [{{"t": s, "cam": [x, y, z], "mira": [x, y, z], "lente": mm}}]}}
- One key per person at EVERY frame where you can see them (and at t=0); if they are out of frame, interpolate.
- Camera: one key per frame you were given (smooth, consistent with the people's positions on screen). A handheld
  phone filmed at arm's length is ~1.5 m high with a 24-26 mm lens; a mirror selfie: the camera is the phone.
- Only the people who are actually in the shot (0 to 6). Objects: only what matters for blocking (max 20)."""


def _num(v: Any, lo: float, hi: float, d: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return d
    return max(lo, min(hi, x)) if math.isfinite(x) else d


_GIRO_PROPIO = {"espalda_mira": 180, "surf": 90}


def limpiar_plan_video(d: Dict[str, Any], dur: float, formas: Set[str], poses: Set[str]) -> Dict[str, Any]:
    """Lo que devolvió Claude, acotado a lo que el editor sabe armar."""
    L = MUNDO["lugares"]
    out: Dict[str, Any] = {"lugar": d.get("lugar") if d.get("lugar") in L else "vacio",
                           "cielo": d.get("cielo") if d.get("cielo") in MUNDO["cielos"] else "mediodia",
                           "tipo_camara": d.get("tipo_camara") if d.get("tipo_camara") in MUNDO["camaras"] else "celular",
                           "descripcion": str(d.get("descripcion") or "")[:400], "objetos": [], "personas": [], "camara": []}
    for o in (d.get("objetos") or [])[:20]:
        if isinstance(o, dict) and o.get("forma") in formas:
            out["objetos"].append({"forma": o["forma"], "x": round(_num(o.get("x"), -80, 80), 2), "z": round(_num(o.get("z"), -80, 80), 2),
                                   "giro": round(_num(o.get("giro"), -360, 360)), "escala": round(_num(o.get("escala"), 0.2, 5, 1), 2)})
    for p in (d.get("personas") or [])[:6]:
        if not isinstance(p, dict):
            continue
        ks = []
        for k in (p.get("keys") or [])[:40]:
            if isinstance(k, dict):
                ks.append({"t": round(_num(k.get("t"), 0, dur), 2), "x": round(_num(k.get("x"), -60, 60), 2),
                           "z": round(_num(k.get("z"), -60, 60), 2), "giro": round(_num(k.get("giro"), -720, 720)),
                           "pose": k.get("pose") if k.get("pose") in poses else "de_pie"})
        ks.sort(key=lambda k: k["t"])
        for k in ks:   # esas poses ya giran el cuerpo solas: el giro que dio Claude es hacia dónde mira de verdad
            k["giro"] -= _GIRO_PROPIO.get(k["pose"], 0)
        if ks:
            out["personas"].append({"ref": str(p.get("ref") or "")[:120], "keys": ks})
    for c in (d.get("camara") or [])[:40]:
        if isinstance(c, dict) and isinstance(c.get("cam"), list) and isinstance(c.get("mira"), list) \
                and len(c["cam"]) == 3 and len(c["mira"]) == 3:
            cam = [round(_num(c["cam"][0], -80, 80), 2), round(_num(c["cam"][1], 0.1, 80, 1.5), 2), round(_num(c["cam"][2], -80, 80), 2)]
            mira = [round(_num(c["mira"][i], -80, 80), 2) for i in range(3)]
            if math.dist(cam, mira) > 0.2:
                out["camara"].append({"t": round(_num(c.get("t"), 0, dur), 2), "cam": cam, "mira": mira,
                                      "lente": round(_num(c.get("lente"), 12, 200, 26))})
    out["camara"].sort(key=lambda k: k["t"])
    if not out["camara"]:
        out["camara"] = [{"t": 0, "cam": [0, 1.5, 3.5], "mira": [0, 1.1, 0], "lente": 26}]
    return out


def _formato_de(w: float, h: float) -> str:
    r = (w / h) if h else 0.5625
    return min({"9:16": 9 / 16, "4:5": 0.8, "1:1": 1.0, "16:9": 16 / 9}.items(), key=lambda kv: abs(kv[1] - r))[0]


def _k_ref(pid: str) -> str:
    return _pfx() + f"lumen:ref:{pid}"


@router.post(API + "/desde_video")
async def api_desde_video(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """{nombre, ancho, alto, dur, cuadros: [{t, img}], nota} → proyecto con la escena armada."""
    import claude_director as _claude
    from imagenes_ia import _compress_ref, _img_part, budget_check, budget_record
    from estudio3d_luma import POSES_3D
    set_current_sub(session_sub_from_request(request))
    cuadros = [c for c in (payload.get("cuadros") or []) if isinstance(c, dict) and str(c.get("img") or "").startswith("data:image/")][:MAX_CUADROS]
    if not cuadros:
        raise HTTPException(400, "No llegaron cuadros del video o de la foto.")
    if not _claude.disponible():
        raise HTTPException(400, "Para armar el mundo desde un video hace falta la clave de Anthropic (ANTHROPIC_API_KEY en Railway).")
    ok, motivo, _, _ = await budget_check(0.3)
    if not ok:
        raise HTTPException(402, motivo)
    dur = _num(payload.get("dur"), 0, 180, 0)
    foto = dur < 0.5 or len(cuadros) == 1
    dur_proy = 6 if foto else max(3, min(180, int(math.ceil(dur))))
    formas = set(_nombres_formas()) | {"piso", "mar", "arena", "isla"}
    sistema = _SYS_VIDEO.format(
        lugares="; ".join(f"{k} ({v['n']})" for k, v in MUNDO["lugares"].items()),
        cielos=", ".join(MUNDO["cielos"]), formas=", ".join(sorted(formas)),
        poses="; ".join(f"{k}: {v}" for k, v in POSES_3D.items()), camaras=", ".join(MUNDO["camaras"]))
    chicos = [_compress_ref(base64.b64decode(c["img"].split(",", 1)[1]), max_dim=768, q=80) for c in cuadros]
    partes: list = [{"text": ("ONE PHOTO (a single moment: put everything at t=0)." if foto else
                              f"A VIDEO of {dur:.1f} s. Frames in order, each with its second.")
                     + (f"\nThe owner adds: {str(payload.get('nota'))[:400]}" if payload.get("nota") else "")}]
    for c, b in zip(cuadros, chicos):
        partes += [{"text": f"Frame at t={_num(c.get('t'), 0, 180):.1f} s:"}, _img_part(b)]
    try:
        data, costo = await _claude.pedir_json(sistema, partes, max_tokens=12000, esfuerzo=os.getenv("LUMEN_ESFUERZO", "medium"))
    except _claude.ClaudeNoDisponible as e:
        raise HTTPException(502, f"Claude no pudo mirar el video: {e}")
    await budget_record("lumen_desde_video", _claude.MODELO, costo, 1, note=f"Lumen: mundo desde {'foto' if foto else 'video'}")
    plan = limpiar_plan_video(data, dur_proy, formas, set(POSES_3D))
    idx = (await kv.get(_k_idx())) or []
    if len(idx) >= MAX_PROYECTOS:
        raise HTTPException(400, f"Hasta {MAX_PROYECTOS} proyectos: borrá alguno.")
    pid = secrets.token_hex(5)
    fmt = _formato_de(_num(payload.get("ancho"), 1, 10000, 9), _num(payload.get("alto"), 1, 10000, 16))
    p = {"id": pid, "nombre": str(payload.get("nombre") or "Desde mi video").strip()[:80], "formato": fmt, "dur": dur_proy,
         "escena3d": None, "tomas": [], "creado": time.strftime("%Y-%m-%d %H:%M"), "actualizado": time.strftime("%Y-%m-%d %H:%M:%S"),
         "plan_video": plan, "mundo": {"lugar": plan["lugar"], "cielo": plan["cielo"], "idea": plan["descripcion"]},
         "ref": [{"t": round(_num(c.get("t"), 0, 180), 2)} for c in cuadros]}
    await kv.set(_k_ref(pid), [_compress_ref(base64.b64decode(b), max_dim=420, q=72) for b in chicos])
    await kv.set(_k_proy(pid), p)
    await kv.set(_k_idx(), [pid] + idx)
    return {"ok": True, "proyecto": {"id": pid}, "costo": costo,
            "resumen": {"personas": len(plan["personas"]), "objetos": len(plan["objetos"]), "claves_camara": len(plan["camara"])}}


@router.post(API + "/cuadros_video")
async def api_cuadros_video(request: Request, video: UploadFile = File(...)) -> Dict[str, Any]:
    """Respaldo: si el navegador no puede abrir el video (un .mov HEVC del iPhone, por ejemplo),
    los cuadros se sacan acá con ffmpeg. Devuelve lo mismo que arma la página."""
    set_current_sub(session_sub_from_request(request))
    crudo = await video.read()
    if len(crudo) > 400_000_000:
        raise HTTPException(413, "El video pesa demasiado (hasta 400 MB).")
    tmp = _dir_videos() / f"ref_{secrets.token_hex(6)}"
    tmp.write_bytes(crudo)
    try:
        pr = await asyncio.create_subprocess_exec(_ffmpeg(), "-i", str(tmp), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await pr.communicate()
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err.decode(errors="ignore"))
        if not m:
            raise HTTPException(400, "No pude leer ese video.")
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        largo = min(60.0, dur)
        n = max(2, min(MAX_CUADROS, round(largo * 1.2) + 1))
        cuadros, ancho, alto = [], 0, 0
        for i in range(n):
            t = max(0.0, min(largo - 0.05, i * largo / (n - 1)))
            pr = await asyncio.create_subprocess_exec(
                _ffmpeg(), "-ss", f"{t:.2f}", "-i", str(tmp), "-frames:v", "1",
                "-vf", "scale='if(gt(iw,ih),768,-2)':'if(gt(iw,ih),-2,768)'", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            jpg, _ = await pr.communicate()
            if not jpg:
                continue
            if not ancho:
                from PIL import Image
                import io
                ancho, alto = Image.open(io.BytesIO(jpg)).size
            cuadros.append({"t": round(t, 1), "img": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()})
    finally:
        tmp.unlink(missing_ok=True)
    if not cuadros:
        raise HTTPException(400, "No pude sacar cuadros de ese video.")
    return {"ancho": ancho, "alto": alto, "dur": dur, "cuadros": cuadros, "largo": dur > 60}


@router.get(API + "/proyectos/{pid}/ref/{i}.jpg")
async def api_ref(pid: str, i: int, request: Request) -> Response:
    set_current_sub(session_sub_from_request(request))
    L = await kv.get(_k_ref(pid)) if re.fullmatch(r"[0-9a-f]{10}", pid) else None
    if not isinstance(L, list) or not 0 <= i < len(L):
        raise HTTPException(404, "No hay ese cuadro de referencia.")
    return Response(base64.b64decode(L[i]), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# ── FILMAR CON IA: el recorrido (animatic) como guía de movimiento ────────────
# El editor graba el recorrido de la cámara y los maniquíes (al tamaño del formato) y lo manda;
# acá se pasa a mp4 y va al motor de video como @Video1: de ahí salen la cámara, los tiempos y
# por dónde camina y gira cada uno. Las fotos de cada personaje (cara + cuerpo) dan quién es.
# Seedance 2.0 con video de referencia es el mismo pedido que "Movete" de Personajes (probado);
# Kling O3 video a video va como prueba: si fal no acepta algún campo, se saca y se reintenta.
MOTORES_FILMAR = {
    "seedance": {"n": "Seedance 2.0 · 720p", "modelo": os.getenv("FAL_SEEDANCE_REF_MODEL", "bytedance/seedance-2.0/reference-to-video"),
                 "precio": float(os.getenv("PERSONAJES_PRECIO_SEEDANCE_REF", "0.30")), "tipo": "seedance"},
    "seedance_fast": {"n": "Seedance 2.0 rápido", "modelo": os.getenv("FAL_SEEDANCE_REF_FAST_MODEL", "bytedance/seedance-2.0/fast/reference-to-video"),
                      "precio": float(os.getenv("PERSONAJES_PRECIO_SEEDANCE_REF_FAST", "0.24")), "tipo": "seedance"},
    "kling": {"n": "Kling O3 video a video (prueba)", "modelo": os.getenv("FAL_KLING_O3_V2V_MODEL", "fal-ai/kling-video/o3/standard/video-to-video/reference"),
              "precio": float(os.getenv("LUMEN_PRECIO_KLING_V2V", "0.17")), "tipo": "kling"},
}
FILMAR_MIN, FILMAR_MAX = 3, 15
MAX_PERSONAS_FILMAR = 4         # 2 fotos por persona: entran en las 9 de Seedance


def _dir_videos() -> Path:
    from personajes import PJ_DIR
    d = PJ_DIR.parent / "lumen" / (_pfx().replace(":", "-").strip("-") or "x") / "videos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ffmpeg() -> str:
    import shutil
    b = shutil.which("ffmpeg")
    if b:
        return b
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


async def _a_mp4(origen: Path, destino: Path) -> float:
    """El recorrido grabado (webm) a mp4 H.264 a 24 cuadros. Devuelve los segundos."""
    cmd = [_ffmpeg(), "-y", "-i", str(origen), "-r", "24", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-an", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-movflags", "+faststart", str(destino)]
    pr = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await pr.communicate()
    if pr.returncode != 0 or not destino.exists():
        raise HTTPException(400, "No pude convertir el recorrido a video: " + err.decode(errors="ignore")[-200:])
    m = re.findall(r"time=(\d+):(\d+):([\d.]+)", err.decode(errors="ignore"))
    if not m:
        return 0.0
    h, mi, se = m[-1]
    return int(h) * 3600 + int(mi) * 60 + float(se)


def prompt_filmar(personas: list, descripcion: str, tipo: str) -> str:
    """El pedido: el recorrido 3D manda en cámara y movimiento; cada maniquí pasa a ser su
    persona (de izquierda a derecha al arrancar); el piso y las formas son un boceto del lugar."""
    k = "@Element" if tipo == "kling" else "@Image"
    partes = ["@Video1 is a simple 3D previsualization (an animatic with plain mannequins) of this exact shot. Keep "
              "EXACTLY its camera movement, framing, lens and timing, and where each mannequin stands, walks, turns and "
              "changes pose, second by second."]
    n_img, n_el = 1, 1
    for i, p in enumerate(personas):
        quien = p.get("quien_en") or "a real person"
        if p.get("fotos"):
            if tipo == "kling":
                refs, n_el = f"{k}{n_el}", n_el + 1
            else:
                refs = " and ".join(f"{k}{n_img + j}" for j in range(p["fotos"]))
                n_img += p["fotos"]
            quien = f"the {p.get('genero_en') or 'person'} of {refs} (same face, hair, skin tone and body)"
        partes.append(f"Mannequin {i + 1}" + (" (counting from the left at the start)" if i == 0 and len(personas) > 1 else "")
                      + f" becomes {quien}.")
    if personas:
        partes.append("They are real people, never mannequins: real skin, hair and clothes, natural human motion.")
    partes.append("The plain floor and the simple shapes are only a sketch of the place: turn them into a real, "
                  "detailed location" + (f": {descripcion}" if descripcion else "") + ".")
    partes.append("Photorealistic live-action footage, real time at 24 fps, natural light with real shadows. "
                  "Faces stay identical in every frame. No morphing, no warping, no extra people, no text, no logos, "
                  "no watermark. No music.")
    return " ".join(partes)


async def _personas_filmar(lista: list) -> tuple:
    """[(foto b64…)] y la descripción de cada persona, en el orden de la pantalla."""
    import personajes as _pj
    out, fotos = [], []
    for x in (lista or [])[:MAX_PERSONAS_FILMAR]:
        d = {"quien_en": str(x.get("describe") or "")[:200]}
        pid = str(x.get("personaje") or "")
        if pid:
            try:
                doc = await _pj._doc(pid)
            except HTTPException:
                doc = None
            refs = await _pj._refs_identidad(doc) if doc else []
            if refs:
                cara = await _pj.cara_identidad(doc, refs[0][1])
                cuerpo = next((b for et, b in refs if et.startswith("cuerpo entero de frente")), None)
                mias = [b for b in (cara, cuerpo or refs[0][1]) if b]
                fotos.append(mias)
                d.update({"fotos": len(mias), "genero_en": "man" if (doc.get("genero") or "") in ("hombre", "masculino", "m") else "woman"})
                out.append(d)
                continue
        fotos.append([])
        out.append(d)
    return out, fotos


async def _filmar_job(jid: str, pid: str, mp4: Path, seg: float, motor: str, personas: list, fotos: list,
                      descripcion: str, formato: str, sub: Optional[str]) -> None:
    import personajes as _pj
    from imagenes_ia import budget_record
    set_current_sub(sub)
    m = MOTORES_FILMAR[motor]
    try:
        key = await _pj._fal_key()
        if not key:
            raise RuntimeError("Falta la API key de fal (FAL_KEY en Railway o en Fotos → Ajustes).")
        headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
        await _pj._job_set(jid, {"estado": "generando", "paso": "Subiendo el recorrido y las fotos a fal…"})
        from imagenes_ia import _al_ingles
        textos = {"d": descripcion, **{f"p{i}": x["quien_en"] for i, x in enumerate(personas) if x.get("quien_en")}}
        tr = await _al_ingles({k: v for k, v in textos.items() if v}) if any(textos.values()) else {}
        for i, x in enumerate(personas):
            x["quien_en"] = tr.get(f"p{i}") or x.get("quien_en") or ""
        prompt = prompt_filmar(personas, tr.get("d") or descripcion, m["tipo"])
        async with httpx.AsyncClient(timeout=300) as cli:
            vurl = await _pj._fal_subir(cli, key, mp4.read_bytes(), "video/mp4", mp4.name)
            urls = [[await _pj._fal_subir(cli, key, base64.b64decode(b), "image/jpeg", f"p{i}_{j}.jpg")
                     for j, b in enumerate(fs)] for i, fs in enumerate(fotos)]
            if m["tipo"] == "kling":
                payload = {"prompt": prompt, "video_url": vurl, "keep_audio": False, "duration": str(int(round(seg))),
                           "aspect_ratio": formato,
                           "elements": [{"frontal_image_url": u[0], "reference_image_urls": u[1:] or u[:1]} for u in urls if u]}
                opc = ("keep_audio", "duration", "aspect_ratio")
            else:
                payload = {"prompt": prompt, "video_urls": [vurl], "image_urls": [x for u in urls for x in u][:9],
                           "resolution": "720p" if motor == "seedance" else "480p", "duration": "auto",
                           "aspect_ratio": "auto", "generate_audio": False, "enable_safety_checker": False}
                opc = ("enable_safety_checker", "generate_audio", "aspect_ratio", "duration", "resolution")
            await _pj._job_set(jid, {"paso": f"{m['n']} está filmando…", "prompt": prompt})
            await _pj._fal_enviar(cli, headers, m["modelo"], payload, jid, opc)
            job = await kv.get(_pj._k_job(jid)) or {}
            destino = _dir_videos() / f"{pid}_{jid}.mp4"
            await _pj._fal_esperar_y_bajar(cli, headers, job["fal_status_url"], job["fal_result_url"], destino, jid,
                                           inicio=job.get("fal_inicio"))
        costo = round(m["precio"] * seg, 3)
        await budget_record("lumen_filmar", motor, costo, 1, note=f"Lumen: filmar {seg:.0f}s con {m['n']}")
        async with _LOCKS.setdefault(pid, asyncio.Lock()):
            p = await kv.get(_k_proy(pid))
            if isinstance(p, dict):
                p.setdefault("videos", []).insert(0, {"id": jid, "motor": m["n"], "seg": round(seg, 1),
                                                      "ts": int(time.time()), "costo": costo})
                p["videos"] = p["videos"][:30]
                await kv.set(_k_proy(pid), p)
        await _pj._job_set(jid, {"estado": "listo", "paso": "", "url": f"{API}/proyectos/{pid}/video/{jid}.mp4"})
    except Exception as e:
        await _pj._job_set(jid, {"estado": "error", "error": str(getattr(e, "detail", e))[:600]})
    finally:
        mp4.unlink(missing_ok=True)


@router.post(API + "/proyectos/{pid}/filmar")
async def api_filmar(pid: str, request: Request, video: UploadFile = File(...), datos: str = Form(...)) -> Dict[str, Any]:
    """El recorrido grabado (webm) + {motor, personas:[{personaje, describe}], descripcion}."""
    from imagenes_ia import budget_check
    sub = session_sub_from_request(request)
    set_current_sub(sub)
    p = await _proy(pid)
    try:
        d = json.loads(datos)
    except ValueError:
        raise HTTPException(400, "Datos inválidos.")
    motor = d.get("motor") if d.get("motor") in MOTORES_FILMAR else "seedance"
    crudo = await video.read()
    if len(crudo) > 120_000_000:
        raise HTTPException(413, "El recorrido pesa demasiado.")
    jid = secrets.token_hex(6)
    web, mp4 = _dir_videos() / f"tmp_{jid}.webm", _dir_videos() / f"tmp_{jid}.mp4"
    web.write_bytes(crudo)
    try:
        seg = await _a_mp4(web, mp4)
    finally:
        web.unlink(missing_ok=True)
    if not (FILMAR_MIN - 0.3 <= seg <= FILMAR_MAX + 0.6):
        mp4.unlink(missing_ok=True)
        raise HTTPException(400, f"El recorrido dura {seg:.1f} s: tiene que durar entre {FILMAR_MIN} y {FILMAR_MAX} s.")
    est = round(MOTORES_FILMAR[motor]["precio"] * seg, 2)
    ok, motivo, _, _ = await budget_check(est)
    if not ok:
        mp4.unlink(missing_ok=True)
        raise HTTPException(402, motivo)
    personas, fotos = await _personas_filmar(d.get("personas") or [])
    import personajes as _pj
    await _pj._job_set(jid, {"id": jid, "estado": "en_cola", "paso": "En cola…", "lumen": pid, "motor": motor,
                             "seg": round(seg, 1), "estimado": est})
    asyncio.create_task(_filmar_job(jid, pid, mp4, seg, motor, personas, fotos,
                                    str(d.get("descripcion") or "")[:600], p.get("formato") or "9:16", sub))
    return {"job": jid, "seg": round(seg, 1), "estimado": est}


@router.get(API + "/filmar/{jid}")
async def api_filmar_estado(jid: str, request: Request) -> Dict[str, Any]:
    import personajes as _pj
    set_current_sub(session_sub_from_request(request))
    j = await kv.get(_pj._k_job(jid))
    if not isinstance(j, dict) or not j.get("lumen"):
        raise HTTPException(404, "No encuentro ese trabajo.")
    return {k: j.get(k) for k in ("estado", "paso", "error", "url", "seg", "estimado", "motor")}


@router.get(API + "/proyectos/{pid}/video/{jid}.mp4")
async def api_video(pid: str, jid: str, request: Request) -> Response:
    if not (re.fullmatch(r"[0-9a-f]{10}", pid) and re.fullmatch(r"[0-9a-f]{12}", jid)):
        raise HTTPException(404, "No existe.")
    set_current_sub(session_sub_from_request(request))
    f = _dir_videos() / f"{pid}_{jid}.mp4"
    if not f.exists():
        raise HTTPException(404, "Ese video ya no está en el servidor (se borra al actualizar): bajalo cuando sale.")
    return Response(f.read_bytes(), media_type="video/mp4", headers={"Content-Disposition": f'inline; filename="lumen-{jid}.mp4"'})


@router.get(API + "/motores")
async def api_motores() -> Dict[str, Any]:
    return {"motores": {k: {"n": v["n"], "precio": v["precio"]} for k, v in MOTORES_FILMAR.items()},
            "min": FILMAR_MIN, "max": FILMAR_MAX, "max_personas": MAX_PERSONAS_FILMAR}


# ── BIBLIOTECA GRATIS (Poly Haven, CC0) ───────────────────────────────────────
# Pasa por el servidor: así no depende de los permisos del navegador (CORS) y queda en caché.
_PH_API = "https://api.polyhaven.com"
_PH_HOSTS = ("dl.polyhaven.org", "cdn.polyhaven.com")
_PH_CACHE: Dict[str, Any] = {}


async def _ph_json(ruta: str) -> Any:
    if ruta in _PH_CACHE and time.time() - _PH_CACHE[ruta][0] < 6 * 3600:
        return _PH_CACHE[ruta][1]
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "StudioLuma/1.0"}) as cli:
        r = await cli.get(_PH_API + ruta)
    if r.status_code != 200:
        raise HTTPException(502, f"La biblioteca no respondió ({r.status_code}). Probá en un rato.")
    d = r.json()
    _PH_CACHE[ruta] = (time.time(), d)
    return d


@router.get(API + "/ph/lista")
async def api_ph_lista(tipo: str = "models") -> Dict[str, Any]:
    """Los modelos (o los cielos HDRI) de Poly Haven, livianos para la grilla."""
    if tipo not in ("models", "hdris"):
        raise HTTPException(400, "tipo: models o hdris")
    d = await _ph_json(f"/assets?t={tipo}")
    out = []
    for k, v in (d or {}).items():
        out.append({"id": k, "nombre": v.get("name") or k, "cat": v.get("categories") or [],
                    "tags": v.get("tags") or [], "dl": v.get("download_count") or 0})
    out.sort(key=lambda x: -x["dl"])
    return {"items": out}


@router.get(API + "/ph/archivos/{aid}")
async def api_ph_archivos(aid: str, tipo: str = "models") -> Dict[str, Any]:
    """Las URLs del modelo glTF 1K (con sus texturas) o del HDRI 1K, apuntando al proxy."""
    if not re.fullmatch(r"[a-z0-9_\-]{2,80}", aid):
        raise HTTPException(400, "id inválido")
    d = await _ph_json(f"/files/{aid}")
    prox = lambda u: f"{API}/ph/dl?u={urllib.parse.quote(str(u), safe='')}"
    if tipo == "hdris":
        try:
            u = d["hdri"]["1k"]["hdr"]["url"]
        except (KeyError, TypeError):
            raise HTTPException(404, "Ese cielo no tiene versión HDR 1K.")
        return {"hdr": prox(u)}
    try:
        g = d["gltf"]["1k"]["gltf"]
    except (KeyError, TypeError):
        try:
            g = d["gltf"]["2k"]["gltf"]
        except (KeyError, TypeError):
            raise HTTPException(404, "Ese modelo no tiene versión glTF.")
    return {"gltf": prox(g["url"]),
            "include": {k: prox(v["url"]) for k, v in (g.get("include") or {}).items() if v.get("url")}}


async def _bajar(url: httpx.URL) -> tuple:
    async with httpx.AsyncClient(timeout=120, follow_redirects=True,
                                 headers={"User-Agent": "StudioLuma/1.0"}) as cli:
        r = await cli.get(url)
    if r.status_code != 200:
        raise HTTPException(502, f"No pude bajar el archivo ({r.status_code}).")
    return r.content, r.headers.get("content-type", "application/octet-stream")


@router.get(API + "/ph/dl")
async def api_ph_dl(u: str) -> Response:
    url = httpx.URL(u)
    if url.scheme != "https" or url.host not in _PH_HOSTS:
        raise HTTPException(400, "Sólo archivos de la biblioteca.")
    datos, tipo = await _bajar(url)
    return Response(datos, media_type=tipo, headers={"Cache-Control": "public, max-age=604800"})


# ── EL CELULAR COMO CÁMARA ───────────────────────────────────────────────────
# Un código por sesión de editor. El celular y el editor se conectan por WebSocket y el
# servidor sólo pasa los mensajes de uno al otro (en memoria: Railway corre un solo proceso).
_CEL: Dict[str, Dict[str, Any]] = {}
_CEL_TTL = 12 * 3600


def _limpiar_codigos() -> None:
    ya = time.time()
    for c in [c for c, v in _CEL.items() if ya - v["ts"] > _CEL_TTL and not v["ed"] and not v["cel"]]:
        _CEL.pop(c, None)


@router.post(API + "/cel/nuevo")
async def api_cel_nuevo(request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _limpiar_codigos()
    sub = session_sub_from_request(request) or ""
    code = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")[:8]
    _CEL[code] = {"ts": time.time(), "sub": sub, "cid": str(payload.get("cid") or ""),
                  "ed": set(), "cel": set()}
    base = str(request.base_url).rstrip("/")
    # detrás del proxy de Railway llega como http: el celular necesita https para el giroscopio
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[len("http://"):]
    return {"code": code, "url": f"{base}{CEL}/{code}"}


async def _relevo(ws: WebSocket, code: str, rol: str) -> None:
    sala = _CEL.get(code)
    if not sala:
        await ws.close(code=4404)
        return
    await ws.accept()
    sala[rol].add(ws)
    sala["ts"] = time.time()
    otro = "cel" if rol == "ed" else "ed"

    async def a_todos(msg: str, quienes: Set[WebSocket]) -> None:
        for w in list(quienes):
            try:
                await w.send_text(msg)
            except Exception:
                quienes.discard(w)

    await a_todos(json.dumps({"tipo": "conectado", "rol": rol}), sala[otro])
    if sala[otro]:
        await ws.send_text(json.dumps({"tipo": "conectado", "rol": otro}))
    try:
        while True:
            msg = await ws.receive_text()
            if len(msg) > 400_000:
                continue
            sala["ts"] = time.time()
            await a_todos(msg, sala[otro])
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        sala[rol].discard(ws)
        await a_todos(json.dumps({"tipo": "desconectado", "rol": rol}), sala[otro])


@router.websocket(API + "/cel/ws/{code}")
async def ws_editor(ws: WebSocket, code: str) -> None:
    # Los WebSocket no pasan por el control de login de main.py: el editor tiene que ser la
    # misma persona que pidió el código (el celular entra sólo con el código).
    sala = _CEL.get(code)
    if sala and sala.get("sub") and session_sub_from_request(ws) != sala["sub"]:  # type: ignore[arg-type]
        await ws.close(code=4403)
        return
    await _relevo(ws, code, "ed")


@router.websocket(CEL + "/ws/{code}")
async def ws_celular(ws: WebSocket, code: str) -> None:
    await _relevo(ws, code, "cel")


@router.get(CEL + "/{code}", response_class=HTMLResponse)
async def pagina_celular(code: str) -> HTMLResponse:
    if code not in _CEL:
        return HTMLResponse("<meta name=viewport content='width=device-width'><body style='font-family:system-ui;"
                            "background:#131218;color:#eee;padding:24px'><h3>Código vencido</h3><p>Volvé a tocar "
                            "📱 en el set 3D y escaneá el QR nuevo.</p>", status_code=404)
    return HTMLResponse(PAGINA_CEL.replace("%%CODE%%", code).replace("%%WS%%", CEL + "/ws/" + code))


@router.get("/cine/set3d")
async def compat_cine(cid: str = "") -> RedirectResponse:
    """El set 3D de Cine ahora es Lumen."""
    return RedirectResponse(f"{PREF}/editor?cine={urllib.parse.quote(cid)}")


@router.get(PREF + "/elegir.js")
async def elegir_js() -> Response:
    """El selector de tomas que usan las otras pantallas: window.Lumen.elegir()."""
    return Response(ELEGIR_JS, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@router.get(PREF, response_class=HTMLResponse)
async def pagina_inicio() -> HTMLResponse:
    return HTMLResponse(PAGINA_INICIO.replace("%%API%%", API).replace("%%PREF%%", PREF)
                        .replace("%%VERSION%%", VERSION).replace("%%MUNDO%%", json.dumps(MUNDO, ensure_ascii=False))
                        .replace("%%POSES%%", json.dumps(_nombres_poses(), ensure_ascii=False))
                        .replace("%%FORMAS%%", json.dumps(_nombres_formas(), ensure_ascii=False)))


def _nombres_formas() -> Dict[str, str]:
    """Las formas del editor que el asistente ofrece como objetos (el suelo lo pone el lugar)."""
    m = re.search(r"const FORMAS = \{(.*?)\};", PAGINA_SET3D, re.S)
    todas = dict(re.findall(r'(\w+): "([^"]+)"', m.group(1))) if m else {}
    return {k: v for k, v in todas.items() if k not in ("piso", "mar", "arena", "isla")}


def _nombres_poses() -> Dict[str, str]:
    """Las poses del maniquí con su nombre en castellano (salen del mismo código del maniquí)."""
    from estudio3d_luma import MANIQUI_JS
    return dict(re.findall(r'^\s*(\w+): \["([^"]+)"', MANIQUI_JS, re.M))


@router.get(PREF + "/editor", response_class=HTMLResponse)
async def pagina_set3d() -> HTMLResponse:
    from estudio3d_luma import MANIQUI_JS
    return HTMLResponse(PAGINA_SET3D.replace("%%COMUN%%", MANIQUI_JS).replace("%%API%%", API)
                        .replace("%%PREF%%", PREF).replace("%%VERSION%%", VERSION)
                        .replace("%%MUNDO%%", json.dumps(MUNDO, ensure_ascii=False)))


from lumen_paginas import PAGINA_SET3D, PAGINA_CEL, PAGINA_INICIO, ELEGIR_JS  # noqa: E402  (las páginas: largas)
