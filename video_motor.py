# -*- coding: utf-8 -*-
"""
video_motor.py — UN solo motor de video para toda la app, con selector de PROVEEDOR y de MODELO
================================================================================================

Proveedores: fal · Higgsfield · Kling directo (la API oficial de Kling). Cada modelo dice por cuáles
se puede pedir, con qué ruta o nombre, y cuánto sale por segundo en cada uno; la pantalla muestra el
costo antes de generar.

FOTOGRAMAS CLAVE: un video largo se arma con imágenes en momentos elegidos (principio, una o varias en el
medio, final) y lo que pasa en cada tramo. Se filma TRAMO POR TRAMO: cada tramo arranca exactamente en la
imagen donde terminó el anterior y termina en la siguiente (cuadro inicial + cuadro final), y después se
unen. Así funciona con cualquier modelo que acepte "cuadro final", y el movimiento queda continuo.

LAS RUTAS Y NOMBRES DE LOS MODELOS NUEVOS (Kling 4.0) se prueban en orden hasta que el proveedor acepta
una, y se pueden fijar con variables de entorno sin tocar el código (VM_<PROVEEDOR>_<MODELO>, p. ej.
VM_HIGGSFIELD_KLING40_PRO). Si el proveedor rechaza el pedido, el error llega tal cual a la pantalla.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from contextvars import ContextVar
from typing import Awaitable, Callable

import higgsfield_luma as hf

# CUANDO EL PROVEEDOR ACEPTA UN PEDIDO (y ya lo cobra), quien llamó puede anotar su id: si el servidor se
# reinicia (un deploy de Railway) mientras el video se hace, se RECUPERA con `recuperar` en vez de perderse un
# video pagado. Se fija por tarea: AL_ACEPTAR.set(funcion) antes de llamar a tramo() o referencias().
AL_ACEPTAR: ContextVar[Optional[Callable[[Dict[str, Any]], Awaitable[None]]]] = ContextVar("vm_al_aceptar", default=None)


async def _aceptado(info: Dict[str, Any]) -> None:
    cb = AL_ACEPTAR.get()
    if cb:
        try:
            await cb(dict(info, ts=time.time()))
        except Exception as e:
            print(f"[video_motor] no pude anotar el pedido aceptado: {e}")


async def _hf_pedir(cli: httpx.AsyncClient, ruta: str, payload: Dict[str, Any], destino: Path) -> Dict[str, Any]:
    """hf.video, pero anotando el pedido apenas Higgsfield lo acepta."""
    rid, url = await hf.enviar(cli, ruta, payload)
    await _aceptado({"prov": "higgsfield", "id": rid, "status_url": url})
    d = await hf.esperar(cli, url, rid=rid)
    await _hf_bajar(cli, d, rid, destino)
    return {"_request_id": rid}


async def _hf_bajar(cli: httpx.AsyncClient, d: Dict[str, Any], rid: str, destino: Path) -> None:
    link = hf._buscar_link(d, (".mp4", ".mov", ".webm"))
    if not link:
        raise hf.HiggsfieldAceptado(f"Higgsfield terminó el pedido {rid} pero no encontré el video: {str(d)[:300]}", rid)
    r = await cli.get(link, timeout=300)
    if r.status_code >= 400 or not r.content:
        raise hf.HiggsfieldAceptado(f"No pude bajar el video del pedido {rid}.", rid)
    destino.write_bytes(r.content)


def clave_pendiente(modulo: str, *partes: Any) -> str:
    """La clave del pedido pendiente: el módulo y lo que define el video (no la hora ni el trabajo), así al volver
    a pedir LA MISMA toma se encuentra lo que ya estaba pagado."""
    from imagenes_ia import _pfx
    h = hashlib.sha1(json.dumps([str(x) for x in partes], ensure_ascii=False).encode()).hexdigest()[:16]
    return _pfx() + f"vmpend:{modulo}:{h}"


async def con_recuperacion(clave: str, destino: Path, costo_est: float, pedir: Callable[[], Awaitable[Dict[str, Any]]],
                           key_fal: str = "") -> Dict[str, Any]:
    """Pide el video anotando el pedido apenas el proveedor lo acepta. Si ya había uno aceptado (y pagado) para
    esta misma toma que un reinicio cortó, lo baja en vez de volver a pedirlo. Devuelve {costo, id, recuperado}."""
    from imagenes_ia import kv
    pend = await kv.get(clave)
    if isinstance(pend, dict) and await recuperar(pend, destino, key_fal=key_fal):
        await kv.delete(clave)
        return {"costo": float(pend.get("costo") or costo_est), "id": pend.get("id", ""), "recuperado": True,
                "seg": pend.get("seg")}

    async def anotar(info: Dict[str, Any]) -> None:
        await kv.set(clave, dict(info, costo=costo_est), ttl=3 * 86400)
    tok = AL_ACEPTAR.set(anotar)
    try:
        r = await pedir()
    finally:
        AL_ACEPTAR.reset(tok)
    try:
        await kv.delete(clave)
    except Exception:
        pass
    return dict(r or {}, recuperado=False)


async def recuperar(info: Dict[str, Any], destino: Path, key_fal: str = "") -> bool:
    """Baja el video de un pedido YA aceptado (y pagado) que quedó sin bajar. True si lo bajó."""
    prov = info.get("prov")
    async with httpx.AsyncClient(timeout=120) as cli:
        try:
            if prov == "higgsfield" and info.get("status_url"):
                d = await hf.esperar(cli, info["status_url"], rid=info.get("id", ""))
                await _hf_bajar(cli, d, info.get("id", ""), destino)
                return True
            if prov == "fal" and info.get("status_url") and key_fal:
                headers = {"Authorization": f"Key {key_fal}"}
                t0 = time.time()
                while time.time() - t0 < ESPERA_MAX:
                    st = ((await cli.get(info["status_url"], headers=headers)).json() or {}).get("status", "")
                    if st in ("COMPLETED", "OK"):
                        rr = (await cli.get(info["response_url"], headers=headers)).json()
                        url = ((rr.get("video") or {}).get("url") if isinstance(rr.get("video"), dict) else None) \
                            or (rr.get("videos") or [{}])[0].get("url")
                        if not url:
                            return False
                        destino.write_bytes((await cli.get(url, timeout=300, follow_redirects=True)).content)
                        return True
                    if st in ("FAILED", "ERROR", "CANCELLED"):
                        return False
                    await asyncio.sleep(6)
                return False
            if prov == "kling" and info.get("id"):
                ruta = "omni-video" if info.get("tipo") == "omni-video" else "image2video"
                t0 = time.time()
                while time.time() - t0 < ESPERA_MAX:
                    q = await cli.get(f"{KLING_BASE}/v1/videos/{ruta}/{info['id']}", headers={"Authorization": kling_auth()})
                    dd = (q.json() if q.status_code < 400 else {}).get("data") or {}
                    st = str(dd.get("task_status") or "").lower()
                    if st in ("succeed", "succeeded", "success", "completed"):
                        url = (((dd.get("task_result") or {}).get("videos") or [{}])[0]).get("url")
                        if not url:
                            return False
                        destino.write_bytes((await cli.get(url, timeout=300, follow_redirects=True)).content)
                        return True
                    if st == "failed":
                        return False
                    await asyncio.sleep(8)
        except Exception as e:
            print(f"[video_motor] no pude recuperar {info}: {e}")
    return False

FAL_BASE = "https://queue.fal.run"
FAL_STORAGE = "https://rest.alpha.fal.ai/storage/upload/initiate"
KLING_BASE = os.getenv("KLING_BASE", "https://api-singapore.klingai.com").rstrip("/")
ESPERA_MAX = int(os.getenv("VM_ESPERA_MAX", "1500"))       # segundos por tramo
KLING_UNIT_USD = float(os.getenv("KLING_UNIT_USD", "0.1413"))

PROVEEDORES = {"fal": "fal", "higgsfield": "Higgsfield", "kling": "Kling directo"}


def _env(prov: str, mid: str, cal: str) -> List[str]:
    v = os.getenv(f"VM_{prov.upper()}_{mid.upper()}_{cal.upper()}", "").strip()
    return [v] if v else []


# ── EL CATÁLOGO ──
# precio: US$ por segundo por proveedor y calidad ("std" = 720p, "pro" = 1080p). Kling directo cobra en
# units (1 unit = KLING_UNIT_USD); con sonido nativo se multiplica por `audio_x`.
MODELOS: Dict[str, Dict[str, Any]] = {
    "kling40": {
        "nombre": "Kling 4.0", "seg": (3, 30), "fin": True, "audio": True, "audio_x": 1.0,
        "rutas": {
            "higgsfield": {"std": ["kling-video/v4.0/standard/image-to-video", "kling-video/v4.0/image-to-video",
                                   "kling-video/v4/image-to-video"],
                           "pro": ["kling-video/v4.0/pro/image-to-video", "kling-video/v4.0/image-to-video",
                                   "kling-video/v4/image-to-video"]},
            "fal": {"std": ["fal-ai/kling-video/v4/standard/image-to-video", "fal-ai/kling-video/v4.0/standard/image-to-video"],
                    "pro": ["fal-ai/kling-video/v4/pro/image-to-video", "fal-ai/kling-video/v4.0/pro/image-to-video"]},
            "kling": {"std": ["kling-v4-0", "kling-v4"], "pro": ["kling-v4-0", "kling-v4"]},
        },
        "precio": {"higgsfield": {"std": 0.110, "pro": 0.165}, "fal": {"std": 0.157, "pro": 0.235},
                   "kling": {"std": 1.0 * KLING_UNIT_USD, "pro": 1.5 * KLING_UNIT_USD}},
    },
    "kling40_flash": {
        "nombre": "Kling 4.0 Flash", "seg": (3, 30), "fin": True, "audio": True, "audio_x": 1.0,
        "rutas": {
            "higgsfield": {"std": ["kling-video/v4.0-flash/image-to-video", "kling-video/v4.0/flash/image-to-video"],
                           "pro": ["kling-video/v4.0-flash/image-to-video", "kling-video/v4.0/flash/image-to-video"]},
            "kling": {"std": ["kling-v4-0-flash", "kling-v4-flash"], "pro": ["kling-v4-0-flash", "kling-v4-flash"]},
        },
        "precio": {"higgsfield": {"std": 0.08, "pro": 0.12}, "kling": {"std": 0.6 * KLING_UNIT_USD, "pro": 0.9 * KLING_UNIT_USD}},
    },
    "kling30": {
        "nombre": "Kling 3.0", "seg": (3, 15), "fin": True, "audio": True, "audio_x": 1.5,
        "rutas": {
            "higgsfield": {"std": ["kling-video/v3.0/standard/image-to-video"],
                           "pro": ["kling-video/v3.0/pro/image-to-video"]},
            "fal": {"std": ["fal-ai/kling-video/o3/standard/image-to-video"],
                    "pro": ["fal-ai/kling-video/o3/pro/image-to-video"]},
            "kling": {"std": ["kling-v3-0", "kling-v3"], "pro": ["kling-v3-0", "kling-v3"]},
        },
        "precio": {"higgsfield": {"std": 0.042, "pro": 0.064}, "fal": {"std": 0.084, "pro": 0.112},
                   "kling": {"std": 0.6 * KLING_UNIT_USD, "pro": 0.8 * KLING_UNIT_USD}},
    },
    "seedance": {
        "nombre": "Seedance (barato)", "seg": (5, 10), "fin": True, "audio": False, "audio_x": 1.0,
        "rutas": {
            "fal": {"std": ["fal-ai/bytedance/seedance/v1/lite/image-to-video"],
                    "pro": ["fal-ai/bytedance/seedance/v1/pro/image-to-video"]},
            "higgsfield": {"std": ["bytedance/seedance-2.5/image-to-video"], "pro": ["bytedance/seedance-2.5/image-to-video"]},
        },
        "precio": {"fal": {"std": 0.036, "pro": 0.148}, "higgsfield": {"std": 0.144, "pro": 0.206}},
    },
    "wan30": {
        "nombre": "Wan 3.0 (el más barato, para probar)", "seg": (2, 30), "fin": False, "audio": False, "audio_x": 1.0,
        "rutas": {"higgsfield": {"std": ["wan/v3.0/image-to-video", "wan-3.0/image-to-video"],
                                 "pro": ["wan/v3.0/image-to-video", "wan-3.0/image-to-video"]}},
        "precio": {"higgsfield": {"std": 0.020, "pro": 0.030}},
    },
}
# CON REFERENCIAS (ella y la prenda como "elementos", sin foto de arranque obligatoria): el formato del pedido
# es el de Kling O3 en fal (elements / image_urls / start_image_url / multi_prompt) y se manda igual a cada
# proveedor. Lo usan Filmado, Comerciales, Reels y Cine.
MODELOS.update({
    "kling40_ref": {
        "nombre": "Kling 4.0 · con referencias (ella y la prenda)", "tipo": "ref", "seg": (3, 30), "fin": True,
        "audio": False, "audio_x": 1.0,
        "rutas": {
            # Higgsfield: UNA ruta; la calidad va en "resolution" (720p / 1080p).
            "higgsfield": {"std": ["kling-video/v4.0/reference-to-video"],
                           "pro": ["kling-video/v4.0/reference-to-video"]},
            "fal": {"std": ["fal-ai/kling-video/v4/standard/reference-to-video"],
                    "pro": ["fal-ai/kling-video/v4/pro/reference-to-video"]},
            "kling": {"std": ["kling-v4-0", "kling-v4"], "pro": ["kling-v4-0", "kling-v4"]},
        },
        "precio": {"higgsfield": {"std": 0.094, "pro": 0.141}, "fal": {"std": 0.157, "pro": 0.235},
                   "kling": {"std": 1.0 * KLING_UNIT_USD, "pro": 1.5 * KLING_UNIT_USD}},
    },
    "kling_o3_ref": {
        "nombre": "Kling 3.0 Omni (O3) · con referencias", "tipo": "ref", "seg": (3, 15), "fin": True,
        "audio": False, "audio_x": 1.0,
        "rutas": {
            "fal": {"std": ["fal-ai/kling-video/o3/standard/reference-to-video"],
                    "pro": ["fal-ai/kling-video/o3/pro/reference-to-video"]},
            "higgsfield": {"std": ["kling-video/o3/standard/reference-to-video", "kling-video/o3/reference-to-video"],
                           "pro": ["kling-video/o3/pro/reference-to-video", "kling-video/o3/reference-to-video"]},
            "kling": {"std": ["kling-video-o1", "kling-v3-omni"], "pro": ["kling-video-o1", "kling-v3-omni"]},
        },
        "precio": {"fal": {"std": 0.084, "pro": 0.112}, "higgsfield": {"std": 0.071, "pro": 0.095},
                   "kling": {"std": 0.6 * KLING_UNIT_USD, "pro": 0.8 * KLING_UNIT_USD}},
    },
})
for _m in MODELOS.values():
    _m.setdefault("tipo", "i2v")
MODELO_DEFAULT = os.getenv("VM_MODELO_DEFAULT", "kling30")
PROVEEDOR_DEFAULT = os.getenv("VM_PROVEEDOR_DEFAULT", "higgsfield")


def rutas(modelo: str, prov: str, cal: str) -> List[str]:
    m = MODELOS.get(modelo) or {}
    return _env(prov, modelo, cal) + list(((m.get("rutas") or {}).get(prov) or {}).get(cal) or [])


def precio_seg(modelo: str, prov: str, cal: str, audio: bool) -> float:
    m = MODELOS.get(modelo) or {}
    p = float(((m.get("precio") or {}).get(prov) or {}).get(cal) or 0.0)
    return p * (float(m.get("audio_x") or 1.0) if audio else 1.0)


def seg_valido(modelo: str, seg: float) -> int:
    lo, hi = (MODELOS.get(modelo) or {}).get("seg") or (5, 10)
    return int(max(lo, min(hi, round(seg))))


def catalogo(disponibles: Dict[str, bool]) -> Dict[str, Any]:
    """Para la pantalla: modelos, por qué proveedores sale cada uno y su precio."""
    out = []
    for mid, m in MODELOS.items():
        provs = [p for p in PROVEEDORES if (m.get("rutas") or {}).get(p) or _env(p, mid, "std")]
        out.append({"id": mid, "nombre": m["nombre"], "tipo": m["tipo"], "seg": list(m["seg"]), "fin": m["fin"], "audio": m["audio"],
                    "proveedores": [{"id": p, "nombre": PROVEEDORES[p], "conectado": disponibles.get(p, False),
                                     "precio": {c: precio_seg(mid, p, c, False) for c in ("std", "pro")},
                                     "precio_audio": {c: precio_seg(mid, p, c, True) for c in ("std", "pro")}}
                                    for p in provs]})
    return {"modelos": out, "defecto": {"modelo": MODELO_DEFAULT, "proveedor": PROVEEDOR_DEFAULT},
            "proveedores": PROVEEDORES}


class MotorError(Exception):
    """El proveedor no aceptó el pedido (no cobró)."""


class MotorAceptado(MotorError):
    """El proveedor ya aceptó el pedido (puede haber cobrado) y después algo falló."""


# ── KLING DIRECTO ──
def _jwt(ak: str, sk: str) -> str:
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=")
    ahora = int(time.time())
    msg = b({"alg": "HS256", "typ": "JWT"}) + b"." + b({"iss": ak, "exp": ahora + 1800, "nbf": ahora - 5})
    sig = base64.urlsafe_b64encode(hmac.new(sk.encode(), msg, hashlib.sha256).digest()).rstrip(b"=")
    return (msg + b"." + sig).decode()


def kling_auth() -> str:
    key = os.getenv("KLING_API_KEY", "").strip()
    if key:
        return "Bearer " + key
    ak, sk = os.getenv("KLING_ACCESS_KEY", "").strip(), os.getenv("KLING_SECRET_KEY", "").strip()
    return ("Bearer " + _jwt(ak, sk)) if ak and sk else ""


def _sin_prefijo(b64: str) -> str:
    return b64.split(",", 1)[1] if b64.startswith("data:") else b64


async def _kling_tramo(cli: httpx.AsyncClient, modelo: str, cal: str, inicio: str, fin: Optional[str],
                       prompt: str, seg: int, audio: bool, aspect: str, destino: Path) -> Dict[str, Any]:
    auth = kling_auth()
    if not auth:
        raise MotorError("Falta la clave de Kling: cargá KLING_API_KEY (o KLING_ACCESS_KEY y KLING_SECRET_KEY) en Railway.")
    ultimo = ""
    for nombre in rutas(modelo, "kling", cal):
        body: Dict[str, Any] = {"model_name": nombre, "image": _sin_prefijo(inicio), "prompt": prompt[:2500],
                                "mode": cal, "duration": str(seg), "aspect_ratio": aspect,
                                "sound": "on" if audio else "off"}
        if fin:
            body["image_tail"] = _sin_prefijo(fin)
        for intento in range(3):
            r = await cli.post(f"{KLING_BASE}/v1/videos/image2video", json=body,
                               headers={"Authorization": auth, "Content-Type": "application/json"})
            d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code < 400 and d.get("code", 0) == 0 and (d.get("data") or {}).get("task_id"):
                tid = d["data"]["task_id"]
                await _aceptado({"prov": "kling", "tipo": "image2video", "id": tid})
                return await _kling_esperar(cli, auth, tid, destino)
            ultimo = f"({r.status_code}) {d.get('message') or r.text[:300]}"
            low = ultimo.lower()
            if "sound" in low and "sound" in body:
                body.pop("sound"); continue
            if "aspect" in low and "aspect_ratio" in body:
                body.pop("aspect_ratio"); continue
            if "duration" in low and body.get("duration") not in ("5", "10"):
                body["duration"] = "10" if seg > 7 else "5"; continue
            break
        if "model" not in ultimo.lower():
            break                                  # el error no es del nombre del modelo: no seguir probando
    raise MotorError(f"Kling rechazó el pedido {ultimo}")


async def _kling_esperar(cli: httpx.AsyncClient, auth: str, tid: str, destino: Path) -> Dict[str, Any]:
    t0, pausa = time.time(), 5.0
    while time.time() - t0 < ESPERA_MAX:
        await asyncio.sleep(pausa)
        pausa = min(15.0, pausa * 1.3)
        r = await cli.get(f"{KLING_BASE}/v1/videos/image2video/{tid}", headers={"Authorization": kling_auth() or auth})
        d = (r.json() if r.status_code < 400 else {}).get("data") or {}
        st = str(d.get("task_status") or "").lower()
        if st in ("succeed", "succeeded", "success", "completed"):
            vids = ((d.get("task_result") or {}).get("videos") or [{}])
            url = vids[0].get("url") if vids else ""
            if not url:
                raise MotorAceptado(f"Kling terminó el pedido {tid} pero no devolvió el video.")
            v = await cli.get(url, timeout=300, follow_redirects=True)
            destino.write_bytes(v.content)
            return {"id": tid}
        if st == "failed":
            raise MotorAceptado(f"Kling no pudo hacer el pedido {tid}: {d.get('task_status_msg') or 'falló'}")
    raise MotorAceptado(f"Kling tardó más de {ESPERA_MAX // 60} minutos con el pedido {tid}.")


# ── FAL ──
async def _fal_url(cli: httpx.AsyncClient, key: str, b64: str, nombre: str) -> str:
    raw = base64.b64decode(_sin_prefijo(b64))
    try:
        r = await cli.post(FAL_STORAGE, headers={"Authorization": f"Key {key}"},
                           json={"content_type": "image/jpeg", "file_name": nombre})
        if r.status_code in (200, 201):
            d = r.json()
            if d.get("upload_url") and d.get("file_url"):
                r2 = await cli.put(d["upload_url"], content=raw, headers={"Content-Type": "image/jpeg"})
                if r2.status_code in (200, 201, 204):
                    return d["file_url"]
    except Exception:
        pass
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


async def _fal_tramo(cli: httpx.AsyncClient, key: str, modelo: str, cal: str, inicio: str, fin: Optional[str],
                     prompt: str, seg: int, audio: bool, aspect: str, destino: Path) -> Dict[str, Any]:
    if not key:
        raise MotorError("Falta la clave de fal.")
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    u_ini = await _fal_url(cli, key, inicio, "inicio.jpg")
    u_fin = await _fal_url(cli, key, fin, "fin.jpg") if fin else ""
    ultimo = ""
    for ruta in rutas(modelo, "fal", cal):
        payload: Dict[str, Any] = {"prompt": prompt[:2500], "image_url": u_ini, "start_image_url": u_ini,
                                   "duration": str(seg), "aspect_ratio": aspect, "generate_audio": bool(audio)}
        if u_fin:
            payload["end_image_url"] = u_fin
        r = None
        for _ in range(5):
            r = await cli.post(f"{FAL_BASE}/{ruta}", headers=headers, json=payload)
            if r.status_code in (400, 422):
                txt = r.text.lower()
                quitado = False
                for k in ("start_image_url", "generate_audio", "aspect_ratio"):
                    if k in payload and k in txt:
                        payload.pop(k); quitado = True
                if quitado:
                    continue
                if "duration" in txt:
                    payload["duration"] = "10" if seg > 7 else "5"; continue
            break
        if r.status_code == 404:
            ultimo = f"la ruta {ruta} no existe"
            continue
        if r.status_code not in (200, 201):
            raise MotorError(f"fal rechazó el pedido ({r.status_code}): {r.text[:300]}")
        d = r.json()
        rid = d.get("request_id")
        st_url = d.get("status_url") or f"{FAL_BASE}/{ruta}/requests/{rid}/status"
        res_url = d.get("response_url") or f"{FAL_BASE}/{ruta}/requests/{rid}"
        await _aceptado({"prov": "fal", "id": rid, "status_url": st_url, "response_url": res_url})
        t0 = time.time()
        while time.time() - t0 < ESPERA_MAX:
            await asyncio.sleep(6)
            s = await cli.get(st_url, headers=headers)
            st = (s.json() if s.status_code == 200 else {}).get("status", "")
            if st in ("COMPLETED", "OK"):
                rr = (await cli.get(res_url, headers=headers)).json()
                url = ((rr.get("video") or {}).get("url") if isinstance(rr.get("video"), dict) else None) \
                    or (rr.get("videos") or [{}])[0].get("url")
                if not url:
                    raise MotorAceptado(f"fal terminó pero no devolvió el video: {str(rr)[:200]}")
                destino.write_bytes((await cli.get(url, timeout=300, follow_redirects=True)).content)
                return {"id": rid}
            if st in ("FAILED", "ERROR", "CANCELLED"):
                raise MotorAceptado(f"fal no pudo hacer el pedido: {s.text[:300]}")
        raise MotorAceptado(f"fal tardó más de {ESPERA_MAX // 60} minutos.")
    raise MotorError(f"fal: {ultimo or 'no hay ruta para este modelo'}")


# ── HIGGSFIELD ──
async def _hf_tramo(cli: httpx.AsyncClient, key_fal: str, modelo: str, cal: str, inicio: str, fin: Optional[str],
                    prompt: str, seg: int, audio: bool, aspect: str, destino: Path) -> Dict[str, Any]:
    if not hf.disponible():
        raise MotorError("Falta la clave de Higgsfield (HIGGSFIELD_KEY_ID y HIGGSFIELD_KEY_SECRET).")
    # Higgsfield recibe las imágenes por link: se suben al almacenamiento de fal (como ya hace Storyboard)
    u_ini = await _fal_url(cli, key_fal, inicio, "inicio.jpg") if key_fal else "data:image/jpeg;base64," + _sin_prefijo(inicio)
    u_fin = (await _fal_url(cli, key_fal, fin, "fin.jpg") if key_fal else "data:image/jpeg;base64," + _sin_prefijo(fin)) if fin else ""
    ultimo = ""
    for ruta in rutas(modelo, "higgsfield", cal):
        payload: Dict[str, Any] = {"prompt": prompt[:2500], "image_url": u_ini, "duration": seg,
                                   "aspect_ratio": aspect, "generate_audio": bool(audio), "sound": "on" if audio else "off"}
        if u_fin:
            payload["end_image_url"] = u_fin
        try:
            res = await _hf_pedir(cli, ruta, payload, destino)
            return {"id": res.get("_request_id", "")}
        except hf.HiggsfieldAceptado as e:
            raise MotorAceptado(str(e))
        except hf.HiggsfieldError as e:
            ultimo = str(e)
            if "404" in ultimo or "not found" in ultimo.lower():
                continue
            raise MotorError(ultimo)
    raise MotorError(ultimo or "Higgsfield: no hay ruta para este modelo")


async def tramo(prov: str, modelo: str, cal: str, inicio_b64: str, fin_b64: Optional[str], prompt: str,
                seg: float, audio: bool, aspect: str, destino: Path, key_fal: str = "") -> Dict[str, Any]:
    """Un tramo: arranca en `inicio`, termina en `fin` (si el modelo lo acepta). Devuelve {id, costo, seg}."""
    m = MODELOS.get(modelo)
    if not m:
        raise MotorError(f"No conozco el modelo {modelo}.")
    s = seg_valido(modelo, seg)
    fin = fin_b64 if m.get("fin") else None
    async with httpx.AsyncClient(timeout=120) as cli:
        if prov == "kling":
            r = await _kling_tramo(cli, modelo, cal, inicio_b64, fin, prompt, s, audio, aspect, destino)
        elif prov == "higgsfield":
            r = await _hf_tramo(cli, key_fal, modelo, cal, inicio_b64, fin, prompt, s, audio, aspect, destino)
        elif prov == "fal":
            r = await _fal_tramo(cli, key_fal, modelo, cal, inicio_b64, fin, prompt, s, audio, aspect, destino)
        else:
            raise MotorError(f"No conozco el proveedor {prov}.")
    r.update({"seg": s, "costo": round(s * precio_seg(modelo, prov, cal, audio), 3)})
    return r


# La frase del video de referencia de movimiento: Higgsfield lo recibe (@video_1); fal y Kling directo no, y ahí
# se saca la frase junto con el video.
FRASE_VIDEO = (" MOTION REFERENCE @Video1: follow its camera movement and the rhythm of the movement only — never "
               "its people, clothes, place or light.")


def _sin_video(p: Dict[str, Any]) -> Dict[str, Any]:
    p = dict(p)
    p.pop("video_urls", None)
    if p.get("prompt"):
        p["prompt"] = p["prompt"].replace(FRASE_VIDEO, "")
    return p


def _payload_hf_ref(p: Dict[str, Any], seg: int, cal: str) -> Dict[str, Any]:
    """Higgsfield (Kling reference-to-video) no usa "elements": quiere una lista `image_urls` (hasta 10) y en el
    texto @image_1, @image_2… en ese orden. Se pasan las fotos de cada elemento en fila y se traduce el texto:
    @Element1 → su foto principal, con una línea que dice qué otras fotos son de lo mismo."""
    urls: List[str] = []
    nombres: Dict[str, str] = {}
    guia: List[str] = []
    for k, el in enumerate(p.get("elements") or [], 1):
        fotos = [u for u in [el.get("frontal_image_url")] + list(el.get("reference_image_urls") or []) if u]
        fotos = list(dict.fromkeys(fotos))[:max(1, 9 - len(urls))]
        if not fotos:
            continue
        ids = [f"@image_{len(urls) + i + 1}" for i in range(len(fotos))]
        urls += fotos
        nombres[f"@Element{k}"] = ids[0]
        if len(ids) > 1:
            guia.append(f"{', '.join(ids[1:])} show the SAME as {ids[0]} from other angles.")
    for k, u in enumerate(p.get("image_urls") or [], 1):
        if len(urls) < 10:
            urls.append(u)
            nombres[f"@Image{k}"] = f"@image_{len(urls)}"
    videos = [u for u in (p.get("video_urls") or []) if u][:5]
    for k in range(1, len(videos) + 1):
        nombres[f"@Video{k}"] = f"@video_{k}"
    texto = p.get("prompt") or " ".join(x.get("prompt", "") for x in p.get("multi_prompt") or [])
    for viejo in sorted(nombres, key=len, reverse=True):
        texto = texto.replace(viejo, nombres[viejo])
    if guia:
        try:
            d = json.loads(texto)          # pedido en JSON: la guía de fotos va adentro, sin romperlo
            d["reference_photos"] = " ".join(guia)
            texto = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            texto = texto.rstrip() + " REFERENCES: " + " ".join(guia)
    # Un JSON cortado por la mitad no se entiende: ése no se recorta (el texto suelto, a 4000).
    out = {"prompt": texto if texto.lstrip().startswith("{") else texto[:4000], "image_urls": urls, "duration": int(seg),
           "resolution": "1080p" if cal == "pro" else "720p", "aspect_ratio": p.get("aspect_ratio") or "9:16"}
    if videos:
        out["video_urls"] = videos
    return out


async def referencias(prov: str, modelo: str, cal: str, payload: Dict[str, Any], destino: Path,
                      key_fal: str = "") -> Dict[str, Any]:
    """Un video CON REFERENCIAS. `payload` viene en el formato de Kling O3 de fal (prompt o multi_prompt,
    elements, image_urls, start_image_url, end_image_url, duration, aspect_ratio)."""
    m = MODELOS.get(modelo)
    if not m or m.get("tipo") != "ref":
        raise MotorError(f"{modelo} no es un modelo con referencias.")
    seg = seg_valido(modelo, float(payload.get("duration") or 5))
    p = dict(payload, duration=str(seg))
    if prov != "higgsfield":
        p = _sin_video(p)
    if prov == "fal" and p.get("elements"):
        # Kling O3 en fal: la foto principal + hasta 3 de referencia por elemento.
        p["elements"] = [dict(e, reference_image_urls=list(e.get("reference_image_urls") or [])[:3])
                         for e in p.get("elements") or []]
    async with httpx.AsyncClient(timeout=120) as cli:
        if prov == "fal":
            if not key_fal:
                raise MotorError("Falta la clave de fal.")
            headers = {"Authorization": f"Key {key_fal}", "Content-Type": "application/json"}
            ultimo = ""
            for ruta in rutas(modelo, "fal", cal):
                r = None
                for _ in range(4):
                    r = await cli.post(f"{FAL_BASE}/{ruta}", headers=headers, json=p)
                    if r.status_code in (400, 422):
                        txt = r.text.lower()
                        quitado = [k for k in ("negative_prompt", "cfg_scale", "generate_audio", "end_image_url") if k in p and k in txt]
                        if quitado:
                            for k in quitado:
                                p.pop(k)
                            continue
                    break
                if r.status_code == 404:
                    ultimo = f"la ruta {ruta} no existe"
                    continue
                if r.status_code not in (200, 201):
                    raise MotorError(f"fal rechazó el pedido ({r.status_code}): {r.text[:300]}")
                d = r.json()
                rid = d.get("request_id")
                st_url = d.get("status_url") or f"{FAL_BASE}/{ruta}/requests/{rid}/status"
                res_url = d.get("response_url") or f"{FAL_BASE}/{ruta}/requests/{rid}"
                await _aceptado({"prov": "fal", "id": rid, "status_url": st_url, "response_url": res_url})
                t0 = time.time()
                while time.time() - t0 < ESPERA_MAX:
                    await asyncio.sleep(6)
                    s_ = await cli.get(st_url, headers=headers)
                    st = (s_.json() if s_.status_code == 200 else {}).get("status", "")
                    if st in ("COMPLETED", "OK"):
                        rr = (await cli.get(res_url, headers=headers)).json()
                        url = ((rr.get("video") or {}).get("url") if isinstance(rr.get("video"), dict) else None) \
                            or (rr.get("videos") or [{}])[0].get("url")
                        if not url:
                            raise MotorAceptado(f"fal terminó pero no devolvió el video: {str(rr)[:200]}")
                        destino.write_bytes((await cli.get(url, timeout=300, follow_redirects=True)).content)
                        return {"id": rid, "seg": seg, "costo": round(seg * precio_seg(modelo, prov, cal, False), 3)}
                    if st in ("FAILED", "ERROR", "CANCELLED"):
                        raise MotorAceptado(f"fal no pudo hacer el pedido: {s_.text[:300]}")
                raise MotorAceptado(f"fal tardó más de {ESPERA_MAX // 60} minutos.")
            raise MotorError(f"fal: {ultimo or 'no hay ruta para este modelo'}")
        if prov == "higgsfield":
            if not hf.disponible():
                raise MotorError("Falta la clave de Higgsfield.")
            ultimo = ""
            for ruta in rutas(modelo, "higgsfield", cal):
                try:
                    res = await _hf_pedir(cli, ruta, _payload_hf_ref(p, seg, cal), destino)
                    return {"id": res.get("_request_id", ""), "seg": seg,
                            "costo": round(seg * precio_seg(modelo, prov, cal, False), 3)}
                except hf.HiggsfieldAceptado as e:
                    raise MotorAceptado(str(e))
                except hf.HiggsfieldError as e:
                    ultimo = str(e)
                    if "404" in ultimo or "not found" in ultimo.lower():
                        continue
                    raise MotorError(ultimo)
            raise MotorError(ultimo or "Higgsfield: no hay ruta para este modelo")
        if prov == "kling":
            auth = kling_auth()
            if not auth:
                raise MotorError("Falta la clave de Kling (KLING_API_KEY en Railway).")
            imgs = []
            # Kling directo nombra las fotos con <<<image_N>>> (fal usa @Element1 / @Image1): se traducen
            # para que sepa cuál es ella, cuál la prenda y cuál el lugar.
            nombres: Dict[str, str] = {}
            for k, el in enumerate(p.get("elements") or [], 1):
                nombres[f"@Element{k}"] = f"<<<image_{len(imgs) + 1}>>>"
                imgs += [el.get("frontal_image_url")] + list(el.get("reference_image_urls") or [])
            for k, u in enumerate(p.get("image_urls") or [], 1):
                nombres[f"@Image{k}"] = f"<<<image_{len(imgs) + 1}>>>"
                imgs.append(u)
            texto = p.get("prompt") or " ".join(x.get("prompt", "") for x in p.get("multi_prompt") or [])
            for viejo in sorted(nombres, key=len, reverse=True):
                texto = texto.replace(viejo, nombres[viejo])
            ultimo = ""
            for nombre in rutas(modelo, "kling", cal):
                body: Dict[str, Any] = {"model_name": nombre, "prompt": texto[:2500], "mode": cal, "duration": str(seg),
                                        "aspect_ratio": p.get("aspect_ratio") or "9:16",
                                        "image_list": [{"image_url": u} for u in imgs if u][:15]}
                if p.get("start_image_url"):
                    body["image_list"].append({"image_url": p["start_image_url"], "type": "first_frame"})  # al final: no corre los <<<image_N>>>
                if p.get("end_image_url"):
                    body["image_list"].append({"image_url": p["end_image_url"], "type": "end_frame"})
                r = await cli.post(f"{KLING_BASE}/v1/videos/omni-video", json=body,
                                   headers={"Authorization": auth, "Content-Type": "application/json"})
                d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                if r.status_code < 400 and d.get("code", 0) == 0 and (d.get("data") or {}).get("task_id"):
                    tid = d["data"]["task_id"]
                    await _aceptado({"prov": "kling", "tipo": "omni-video", "id": tid})
                    t0 = time.time()
                    while time.time() - t0 < ESPERA_MAX:
                        await asyncio.sleep(8)
                        q = await cli.get(f"{KLING_BASE}/v1/videos/omni-video/{tid}", headers={"Authorization": kling_auth()})
                        dd = (q.json() if q.status_code < 400 else {}).get("data") or {}
                        st = str(dd.get("task_status") or "").lower()
                        if st in ("succeed", "succeeded", "success", "completed"):
                            url = (((dd.get("task_result") or {}).get("videos") or [{}])[0]).get("url")
                            if not url:
                                raise MotorAceptado(f"Kling terminó el pedido {tid} sin video.")
                            destino.write_bytes((await cli.get(url, timeout=300, follow_redirects=True)).content)
                            return {"id": tid, "seg": seg, "costo": round(seg * precio_seg(modelo, prov, cal, False), 3)}
                        if st == "failed":
                            raise MotorAceptado(f"Kling no pudo hacer el pedido {tid}: {dd.get('task_status_msg') or 'falló'}")
                    raise MotorAceptado(f"Kling tardó más de {ESPERA_MAX // 60} minutos con el pedido {tid}.")
                ultimo = f"({r.status_code}) {d.get('message') or r.text[:300]}"
                if "model" not in ultimo.lower():
                    break
            raise MotorError(f"Kling rechazó el pedido {ultimo}")
    raise MotorError(f"No conozco el proveedor {prov}.")


def costo_video(modelo: str, prov: str, cal: str, audio: bool, tramos_seg: List[float]) -> float:
    return round(sum(seg_valido(modelo, s) * precio_seg(modelo, prov, cal, audio) for s in tramos_seg), 2)


# ── UNIR ──
def _ffmpeg() -> Optional[str]:
    b = shutil.which("ffmpeg")
    if b:
        return b
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def unir(clips: List[Path], largos: List[float], salida: Path, aspect: str) -> None:
    """Une los tramos (cada uno recortado al largo pedido) en un solo video, con o sin audio."""
    b = _ffmpeg()
    if not b:
        raise RuntimeError("No hay ffmpeg para unir los tramos.")
    W, H = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}.get(aspect, (1080, 1920))
    normal = []
    for k, (c, lg) in enumerate(zip(clips, largos)):
        n = c.with_name(f"n{k}_{c.name}")
        info = subprocess.run([b, "-i", str(c)], capture_output=True, timeout=60).stderr.decode(errors="ignore")
        audio = "1:a" if "Audio:" not in info else "0:a:0"          # siempre UNA pista de audio (o silencio)
        subprocess.run([b, "-y", "-i", str(c), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{lg:.2f}",
                        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,format=yuv420p",
                        "-map", "0:v:0", "-map", audio, "-c:v", "libx264", "-preset", "fast",
                        "-crf", "19", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(n)], capture_output=True, timeout=600)
        if not n.exists():
            raise RuntimeError(f"No pude preparar el tramo {k + 1}.")
        normal.append(n)
    lista = salida.with_suffix(".txt")
    lista.write_text("".join(f"file '{p.as_posix()}'\n" for p in normal))
    r = subprocess.run([b, "-y", "-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy", "-movflags", "+faststart",
                        str(salida)], capture_output=True, timeout=900)
    for p in normal:
        p.unlink(missing_ok=True)
    lista.unlink(missing_ok=True)
    if r.returncode != 0 or not salida.exists():
        raise RuntimeError("No pude unir los tramos: " + r.stderr.decode(errors="ignore")[-300:])
