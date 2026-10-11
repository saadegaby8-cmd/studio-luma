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
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

BASE = os.getenv("HIGGSFIELD_BASE", "https://api.higgsfield.ai").rstrip("/")
ESPERA_MAX = int(os.getenv("HIGGSFIELD_ESPERA_MAX", "900"))      # segundos
FINALES_OK = {"completed", "succeeded", "success", "done", "finished"}
FINALES_MAL = {"failed", "error", "nsfw", "canceled", "cancelled", "rejected", "timeout"}


class HiggsfieldError(Exception):
    pass


def clave() -> str:
    """El valor del header Authorization (vacío si no hay clave cargada)."""
    junta = (os.getenv("HIGGSFIELD_API_KEY") or "").strip()
    kid = (os.getenv("HIGGSFIELD_KEY_ID") or "").strip()
    sec = (os.getenv("HIGGSFIELD_KEY_SECRET") or os.getenv("HIGGSFIELD_SECRET") or "").strip()
    if kid and sec:
        return f"Key {kid}:{sec}"
    if junta:
        return junta if junta.lower().startswith("key ") else f"Key {junta}"
    return ""


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


async def esperar(cli: httpx.AsyncClient, status_url: str, maximo: int = ESPERA_MAX) -> Dict[str, Any]:
    """Consulta el estado hasta que termina. Devuelve el JSON final (con el resultado)."""
    t0, pausa = time.time(), 4.0
    while time.time() - t0 < maximo:
        r = await cli.get(status_url, headers=_headers())
        if r.status_code >= 400 and r.status_code != 404:
            raise HiggsfieldError(f"Higgsfield ({r.status_code}): {_error(r)}")
        d = r.json() if r.status_code < 400 else {}
        estado = (_buscar(d, "status", "state") or "").lower()
        if estado in FINALES_OK:
            return d
        if estado in FINALES_MAL:
            motivo = _buscar(d, "error", "detail", "message", "reason") or estado
            raise HiggsfieldError(f"Higgsfield no pudo ({estado}): {motivo[:300]}")
        await asyncio.sleep(pausa)
        pausa = min(10.0, pausa * 1.3)
    raise HiggsfieldError(f"Higgsfield tardó más de {maximo // 60} minutos.")


async def video(cli: httpx.AsyncClient, modelo: str, payload: Dict[str, Any], destino: Path) -> Dict[str, Any]:
    """Pide un video, espera y lo baja a `destino`."""
    _, url = await enviar(cli, modelo, payload)
    d = await esperar(cli, url)
    link = _buscar_link(d, (".mp4", ".mov", ".webm"))
    if not link:
        raise HiggsfieldError(f"Higgsfield terminó pero no encontré el video en la respuesta: {str(d)[:300]}")
    r = await cli.get(link, timeout=300)
    if r.status_code >= 400 or not r.content:
        raise HiggsfieldError("No pude bajar el video de Higgsfield.")
    destino.write_bytes(r.content)
    return d


async def probar() -> Tuple[bool, str]:
    """¿La clave anda? Consulta un pedido que no existe: 404 = clave buena, 401/403 = mala."""
    if not disponible():
        return False, "No hay clave cargada (HIGGSFIELD_KEY_ID y HIGGSFIELD_KEY_SECRET en Railway)."
    try:
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.get(f"{BASE}/requests/00000000-0000-0000-0000-000000000000/status", headers=_headers())
    except Exception as e:
        return False, f"No pude conectar con Higgsfield: {e}"
    if r.status_code in (401, 403):
        return False, f"Higgsfield no acepta la clave ({r.status_code}): {_error(r)}"
    return True, "La clave de Higgsfield anda."
