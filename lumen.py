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
import re
import secrets
import urllib.parse
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

import httpx
from fastapi import APIRouter, Body, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from imagenes_ia import _pfx, kv, session_sub_from_request, set_current_sub

VERSION = "1.0.0"
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
                            "url": f"{API}/proyectos/{pid}/toma/{t['id']}.jpg?t={t.get('ts', 0)}"})
    return {"tomas": out}


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


@router.get(PREF, response_class=HTMLResponse)
async def pagina_inicio() -> HTMLResponse:
    return HTMLResponse(PAGINA_INICIO.replace("%%API%%", API).replace("%%PREF%%", PREF)
                        .replace("%%VERSION%%", VERSION))


@router.get(PREF + "/editor", response_class=HTMLResponse)
async def pagina_set3d() -> HTMLResponse:
    from estudio3d_luma import MANIQUI_JS
    return HTMLResponse(PAGINA_SET3D.replace("%%COMUN%%", MANIQUI_JS).replace("%%API%%", API)
                        .replace("%%PREF%%", PREF).replace("%%VERSION%%", VERSION))


from lumen_paginas import PAGINA_SET3D, PAGINA_CEL, PAGINA_INICIO  # noqa: E402  (las páginas: largas)
