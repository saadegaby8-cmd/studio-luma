# -*- coding: utf-8 -*-
"""SET 3D PRO del modo Cine: un editor 3D completo adentro del programa (tipo Blender, pero
guiado y en castellano) y el CELULAR COMO CÁMARA.

- El editor (/cine/set3d?cid=…): formas simples, maniquíes, modelos 3D de la biblioteca gratis
  de Poly Haven (CC0) o subidos (.glb/.gltf/.fbx/.obj), luces reales con sombras (sol, foco,
  lámpara, ventana) y cielos HDRI, cámara con lente en milímetros, una LÍNEA DE TIEMPO con
  claves para la cámara y los objetos, animatic, y las guías de los puntos de Cine.
- El celular (/cine-cel/{código}): se abre escaneando un QR, sin login. Manda su orientación
  (giroscopio) —y en Android con AR, también su posición— al editor por WebSocket: movés el
  celular y la cámara del set se mueve igual. Ve lo que ve la cámara, y graba recorridos que
  quedan como claves en la línea de tiempo.

La escena se guarda en la película de Cine (p["escena3d"]) y la cámara de cada punto se sigue
guardando como antes (cam/mira/lente), así el resto de Cine no cambia.
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
from fastapi.responses import HTMLResponse, Response

from imagenes_ia import _pfx, kv, session_sub_from_request, set_current_sub

VERSION = "1.0.0"
PREF = "/cine"
API = PREF + "/api"
CEL = "/cine-cel"          # abierto (sin login): lo abre el celular con el código del QR

router = APIRouter()

# ── MODELOS SUBIDOS ──────────────────────────────────────────────────────────
_EXT_OK = {"glb": "model/gltf-binary", "gltf": "model/gltf+json", "fbx": "application/octet-stream",
           "obj": "text/plain"}
_MAX_MODELO = 80 * 1024 * 1024            # 80 MB
_MAX_KV = 8 * 1024 * 1024                 # copia de respaldo en Redis hasta 8 MB


def _dir_modelos(cid: str) -> Path:
    from cine_luma import _dir
    d = _dir(cid) / "modelos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _k_modelo(cid: str, mid: str) -> str:
    return _pfx() + f"cine:{cid}:modelo:{mid}"


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


@router.get(PREF + "/set3d", response_class=HTMLResponse)
async def pagina_set3d() -> HTMLResponse:
    from estudio3d_luma import MANIQUI_JS
    return HTMLResponse(PAGINA_SET3D.replace("%%COMUN%%", MANIQUI_JS).replace("%%API%%", API)
                        .replace("%%PREF%%", PREF).replace("%%VERSION%%", VERSION))


from cine_set3d_paginas import PAGINA_SET3D, PAGINA_CEL  # noqa: E402  (las páginas: largas)
