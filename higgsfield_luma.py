# -*- coding: utf-8 -*-
"""
higgsfield_luma.py — La API de Higgsfield (open.higgsfield.ai)
==============================================================

Mismo esquema que fal: se manda el pedido a https://api.higgsfield.ai/<modelo>, vuelve un
request_id, y se consulta /requests/<id>/status hasta que termina. La clave va como
"Authorization: Key <ID>:<SECRETO>" (variables HIGGSFIELD_KEY_ID y HIGGSFIELD_KEY_SECRET en
Railway, o HIGGSFIELD_API_KEY con "ID:SECRETO").

La respuesta se lee de forma tolerante (el primer link de video o de imagen que aparezca),
porque los campos cambian de modelo a modelo; si Higgsfield devuelve un error, el mensaje
llega tal cual a la pantalla.
"""

from __future__ import annotations

import asyncio
import os
import re
from contextvars import ContextVar
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

BASE = os.getenv("HIGGSFIELD_BASE", "https://api.higgsfield.ai").rstrip("/")
ESPERA_MAX = int(os.getenv("HIGGSFIELD_ESPERA_MAX", "900"))      # segundos
FINALES_OK = {"completed", "succeeded", "success", "done", "finished"}
FINALES_MAL = {"failed", "error", "nsfw", "canceled", "cancelled", "rejected", "timeout"}


class HiggsfieldError(Exception):
    """Higgsfield NO aceptó el pedido (no cobra): se puede probar con otro motor."""


class HiggsfieldAceptado(HiggsfieldError):
    """Higgsfield YA aceptó el pedido (puede haber cobrado) y algo falló después: NUNCA se
    vuelve a pedir a otro motor (se pagaría dos veces). Trae el id para buscarlo en su panel."""
    def __init__(self, msg: str, rid: str = "") -> None:
        super().__init__(msg)
        self.rid = rid


# La clave cargada en la app (Ajustes de la cuenta) para este pedido; si no hay, la de Railway.
_CLAVE_APP: ContextVar[str] = ContextVar("higgsfield_clave_app", default="")


def armar(kid: str = "", sec: str = "", junta: str = "") -> str:
    """El header a partir de lo que haya: ID y secreto por separado, o pegados juntos (con ":",
    un espacio o un salto de línea en el medio), con o sin "Key " adelante."""
    kid, sec, junta = (kid or "").strip(), (sec or "").strip(), (junta or "").strip()
    if kid.lower().startswith("key "):
        kid = kid[4:].strip()
    if kid and not sec and re.search(r"[:\s]", kid):
        junta, kid = kid, ""
    if junta and not (kid and sec):
        j = junta[4:].strip() if junta.lower().startswith("key ") else junta
        partes = [x for x in re.split(r"[:\s]+", j) if x]
        if len(partes) >= 2:
            kid, sec = partes[0], partes[1]
        else:
            return f"Key {j}"
    if kid and sec:
        return f"Key {kid}:{sec}"
    return ""


def _clave_railway() -> str:
    return armar(os.getenv("HIGGSFIELD_KEY_ID") or os.getenv("HF_KEY_ID") or "",
                 os.getenv("HIGGSFIELD_KEY_SECRET") or os.getenv("HIGGSFIELD_SECRET") or os.getenv("HF_KEY_SECRET") or "",
                 os.getenv("HIGGSFIELD_API_KEY") or os.getenv("HIGGSFIELD_KEY") or "")


def clave() -> str:
    """El valor del header Authorization (vacío si no hay clave cargada)."""
    return _CLAVE_APP.get() or _clave_railway()


def origen() -> str:
    return "la app" if _CLAVE_APP.get() else ("Railway" if _clave_railway() else "")


async def cargar_de_ajustes() -> None:
    """Toma la clave guardada en los Ajustes de la cuenta (si la hay) para este pedido."""
    try:
        from imagenes_ia import get_settings
        s = await get_settings()
        _CLAVE_APP.set(armar(str(s.get("higgsfield_key_id") or ""), str(s.get("higgsfield_key_secret") or "")))
    except Exception:
        _CLAVE_APP.set("")


def disponible() -> bool:
    return bool(clave())


def _headers() -> Dict[str, str]:
    return {"Authorization": clave(), "Content-Type": "application/json", "Accept": "application/json"}


def _buscar(d: Any, *claves: str) -> str:
    """El primer valor de texto bajo alguna de esas claves, buscando en todo el JSON."""
    if isinstance(d, dict):
        for k in claves:
            v = d.get(k)
            if isinstance(v, str) and v:
                return v
        for v in d.values():
            r = _buscar(v, *claves)
            if r:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _buscar(v, *claves)
            if r:
                return r
    return ""


def _buscar_link(d: Any, extensiones: Tuple[str, ...]) -> str:
    """El primer link (http) del resultado, preferentemente con esa extensión."""
    links = []

    def recorrer(x: Any) -> None:
        if isinstance(x, dict):
            for v in x.values():
                recorrer(v)
        elif isinstance(x, list):
            for v in x:
                recorrer(v)
        elif isinstance(x, str) and x.startswith("http"):
            links.append(x)
    recorrer(d)
    for u in links:
        if u.split("?")[0].lower().endswith(extensiones):
            return u
    return next((u for u in links if "/requests/" not in u), "")


def _error(r: httpx.Response) -> str:
    try:
        d = r.json()
        return str(d.get("detail") or d.get("error") or d.get("message") or d)[:400]
    except Exception:
        return r.text[:400]


async def enviar(cli: httpx.AsyncClient, modelo: str, payload: Dict[str, Any]) -> Tuple[str, str]:
    """Manda el pedido. Devuelve (request_id, status_url)."""
    if not disponible():
        raise HiggsfieldError("Falta la clave de Higgsfield (HIGGSFIELD_KEY_ID y HIGGSFIELD_KEY_SECRET en Railway).")
    r = await cli.post(f"{BASE}/{modelo.strip('/')}", json=payload, headers=_headers())
    if r.status_code >= 400:
        raise HiggsfieldError(f"Higgsfield rechazó el pedido ({r.status_code}): {_error(r)}")
    d = r.json()
    rid = _buscar(d, "request_id", "id", "requestId")
    url = _buscar(d, "status_url", "statusUrl") or (f"{BASE}/requests/{rid}/status" if rid else "")
    if not url:
        raise HiggsfieldError(f"Higgsfield no devolvió el id del pedido: {str(d)[:300]}")
    return rid, url


async def esperar(cli: httpx.AsyncClient, status_url: str, maximo: int = ESPERA_MAX, rid: str = "") -> Dict[str, Any]:
    """Consulta el estado hasta que termina. Devuelve el JSON final (con el resultado). Todo error
    acá es "ya aceptado" (Higgsfield puede haber cobrado)."""
    t0, pausa, no_esta = time.time(), 4.0, 0
    while time.time() - t0 < maximo:
        r = await cli.get(status_url, headers=_headers())
        if r.status_code == 404:
            no_esta += 1
            if no_esta >= 6:      # la dirección del estado no existe: no seguir 15 minutos a ciegas
                raise HiggsfieldAceptado(f"Higgsfield aceptó el pedido {rid} pero su estado no aparece "
                                         f"({status_url}). Buscalo en tu panel de Higgsfield.", rid)
        elif r.status_code >= 400:
            raise HiggsfieldAceptado(f"Higgsfield aceptó el pedido {rid} y después dio error "
                                     f"({r.status_code}): {_error(r)}", rid)
        d = r.json() if r.status_code < 400 else {}
        estado = (_buscar(d, "status", "state") or "").lower()
        if estado in FINALES_OK:
            return d
        if estado in FINALES_MAL:
            motivo = _buscar(d, "error", "detail", "message", "reason") or estado
            raise HiggsfieldAceptado(f"Higgsfield no pudo terminar el pedido {rid} ({estado}): {motivo[:300]}", rid)
        await asyncio.sleep(pausa)
        pausa = min(10.0, pausa * 1.3)
    raise HiggsfieldAceptado(f"Higgsfield tardó más de {maximo // 60} minutos con el pedido {rid}: buscalo en su panel.", rid)


async def video(cli: httpx.AsyncClient, modelo: str, payload: Dict[str, Any], destino: Path) -> Dict[str, Any]:
    """Pide un video, espera y lo baja a `destino`."""
    rid, url = await enviar(cli, modelo, payload)
    d = await esperar(cli, url, rid=rid)
    link = _buscar_link(d, (".mp4", ".mov", ".webm"))
    if not link:
        raise HiggsfieldAceptado(f"Higgsfield terminó el pedido {rid} pero no encontré el video en la respuesta: "
                                 f"{str(d)[:300]}", rid)
    r = await cli.get(link, timeout=300)
    if r.status_code >= 400 or not r.content:
        raise HiggsfieldAceptado(f"No pude bajar el video del pedido {rid} ({link}).", rid)
    destino.write_bytes(r.content)
    d["_request_id"] = rid
    return d


async def imagen(cli: httpx.AsyncClient, modelo: str, payload: Dict[str, Any]) -> bytes:
    """Pide una imagen, espera y devuelve sus bytes."""
    rid, url = await enviar(cli, modelo, payload)
    d = await esperar(cli, url, rid=rid)
    link = _buscar_link(d, (".png", ".jpg", ".jpeg", ".webp"))
    if not link:
        raise HiggsfieldAceptado(f"Higgsfield terminó el pedido {rid} pero no encontré la imagen: {str(d)[:300]}", rid)
    r = await cli.get(link, timeout=120)
    if r.status_code >= 400 or not r.content:
        raise HiggsfieldAceptado(f"No pude bajar la imagen del pedido {rid}.", rid)
    return r.content


async def probar() -> Tuple[bool, str]:
    """¿La clave anda? Consulta un pedido que no existe: 404 = clave buena, 401/403 = mala."""
    if not disponible():
        return False, ("No encuentro la clave. Pegala acá abajo (Key ID y Key Secret) o cargala en Railway como "
                       "HIGGSFIELD_KEY_ID y HIGGSFIELD_KEY_SECRET (y esperá que se vuelva a publicar).")
    try:
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.get(f"{BASE}/requests/00000000-0000-0000-0000-000000000000/status", headers=_headers())
    except Exception as e:
        return False, f"No pude conectar con Higgsfield: {e}"
    if r.status_code in (401, 403):
        return False, (f"Higgsfield rechaza la clave cargada en {origen()} ({r.status_code}): {_error(r)}. Revisá que "
                       "sean el Key ID y el Key Secret de la misma clave (API keys en open.higgsfield.ai).")
    return True, f"La clave de Higgsfield anda (cargada en {origen()})."
