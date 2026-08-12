# -*- coding: utf-8 -*-
"""
imagenes_ia.py — Generador de imágenes IA para LUMA Íntima
============================================================

Módulo STANDALONE y self-contained (mismo molde que videos_ia.py).

Qué hace
--------
- Worker que te pregunta TODO antes de generar: tela, puños, costuras,
  cuello/escote, color, modelo, pose, fondo, luz, formato y cantidad.
- Avatares propios (hasta 24 por género, configurable con AVATAR_SLOTS). Los generás UNA vez,
  los aprobás y se LOCKEAN como referencia. Después en cada generación
  va: avatar + foto real de la prenda -> la modelo con TU producto puesto,
  misma cara siempre (Nano Banana Pro mantiene consistencia).
- Modo PRODUCTO para infantil (niñas/niños) y flat-lay: flat-lay, doblado,
  percha o maniquí fantasma. SIN personas (por diseño y por seguridad).
- Settings de AI Studio ANCLADOS: modelo, aspect ratio, resolución (4K),
  temperature, top-p, system instruction, safety y seed quedan fijos.
- Splitter de paneles (PIL): pedís 21:9 con N paneles separados por línea
  blanca y te corta N imágenes sueltas. El costo por imagen se parte
  (ej: 4K = US$0,151 / 2 paneles = ~US$0,076 c/u).
- Salida configurable: PNG master (lossless), optimizado (JPEG/WebP) o ambos.
- Ledger de presupuesto en Redis con tope mensual: si llegás al límite, frena.

Cómo se engancha
----------------
Es un APIRouter. En tu app principal:

    from imagenes_ia import router as imagenes_router
    app.include_router(imagenes_router)

UI: GET /imagenes

O corre solo:  python imagenes_ia.py   (levanta su propio server en :8090)

Variables de entorno
--------------------
  GEMINI_API_KEY            (obligatoria)  -> tu key de Google AI Studio (AIza...)
  NANO_BANANA_MODEL         (opcional)     -> default: gemini-3.1-flash-image (Nano Banana 2)
  UPSTASH_REDIS_REST_URL    (opcional)     -> persistencia Upstash REST
  UPSTASH_REDIS_REST_TOKEN  (opcional)
  REDIS_URL                 (opcional)     -> fallback redis:// si no usás Upstash
  IMAGENES_PORT             (opcional)     -> default 8090 (solo modo standalone)

Si no hay ningún Redis, usa memoria (no persiste entre deploys; sirve para probar).

Dependencias:  fastapi, httpx, pillow   (uvicorn si corre standalone)
"""

import os
import io
import re
import json
import time
import random
import base64
import hmac
import hashlib
import asyncio
import uuid as _uuid
import datetime as _dt
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PIL import Image, ImageOps
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

ROUTE_PREFIX = os.environ.get("IMAGENES_PREFIX", "/imagenes").rstrip("/")
VERSION = "2.28.3"   # subí este número cada vez que cambiamos el archivo

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FAL_API_KEY = os.getenv("FAL_KEY", "") or os.getenv("FAL_API_KEY", "")
MODEL_ID = os.getenv("NANO_BANANA_MODEL", "gemini-3.1-flash-image")  # Nano Banana 2
GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_ID}:generateContent"
)
# Modelo de VISIÓN+TEXTO para analizar la prenda (ficha técnica). Barato vs generar imagen.
ANALYZE_MODEL = os.getenv("NANO_ANALYZE_MODEL", "gemini-3.1-flash-lite")
ANALYZE_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{ANALYZE_MODEL}:generateContent"
)

# Precio por imagen segun resolucion (Nano Banana 2 / gemini-3.1-flash-image, jul 2026).
# 1K = US$0,067 ; 2K = US$0,101 ; 4K = US$0,151. (el input de referencias suma centavos)
PRICING = {"1K": 0.067, "2K": 0.101, "4K": 0.151}   # Nano Banana 2 (editables en Ajustes)




def _pricing(settings: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """Precio por imagen segun resolucion. Sale de los ajustes (editable por la usuaria),
    porque cambia segun el modelo que use."""
    s = settings or {}
    out = {}
    for k, campo in (("1K", "precio_1k"), ("2K", "precio_2k"), ("4K", "precio_4k")):
        try:
            v = float(s.get(campo) or PRICING[k])
        except (TypeError, ValueError):
            v = PRICING[k]
        out[k] = max(0.0, v)
    return out

# Slots fijos del registro de avatares
SLOTS_POR_GENERO = int(os.getenv("AVATAR_SLOTS", "24"))
GENEROS = ["mujer", "hombre"]

# Aspect ratios soportados por la API
ASPECTOS_VALIDOS = ["1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "16:9", "9:16", "21:9"]

# Mapa aspect-ratio -> ratio numerico (w/h) para reencuadre del splitter
RATIO_NUM = {
    "1:1": 1.0, "3:2": 1.5, "2:3": 2 / 3, "3:4": 0.75, "4:3": 4 / 3,
    "4:5": 0.8, "5:4": 1.25, "16:9": 16 / 9, "9:16": 9 / 16, "21:9": 21 / 9,
}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "model": MODEL_ID,
    "precio_1k": 0.067,             # US$ por imagen, Nano Banana 2 (ajustable)
    "precio_2k": 0.101,
    "precio_4k": 0.151,
    "aspect_ratio": "4:5",          # formato base de generacion
    "image_size": "4K",             # SIEMPRE 4K por pedido (override por generacion)
    "temperature": 0.45,            # más bajo = más fiel a la foto (menos "creatividad")
    "top_p": 0.95,
    "seed": None,                   # poné un entero fijo para máxima repetibilidad
    "safety": "relaxed",            # "default" | "relaxed" (catalogo de ropa intima)
    "plantilla_prompt": "",         # prompt propio de la usuaria (manda tal cual si está)
    "refs_hd": "si",                # "si" -> manda las fotos casi sin comprimir (como el Playground)
    "output_format": "both",        # "png" | "optimized" | "both"
    "optimized_format": "jpeg",     # "jpeg" | "webp"
    "optimized_quality": 90,
    "default_style": "instagram_real",  # estilo por defecto en Generar
    # ── Motor alternativo FLUX (fal.ai) — para lencería y modelo propio (LoRA) ──
    "engine": "gemini",                 # SOLO Nano Banana (fal/FLUX desconectado por pedido)
    "fal_api_key": "",                  # o variable FAL_KEY en Railway
    "flux_tryon_model": "bytedance/seedream/v5/pro/edit",   # avatar + prenda (FLUX.2 dev: calidad + moder. liviana)
    "flux_edit_model": "bytedance/seedream/v5/pro/edit",    # sin avatar / solo producto (dev, multi-referencia)
    "flux_fallback_model": "fal-ai/bytedance/seedream/v5/lite/edit",  # respaldo si el Pro bloquea
    "precio_flux": 0.07,                # US$ por imagen con FLUX (editable)
    "flux_guidance": 5.0,               # + alto = FLUX obedece más el prompt (pose/ambiente)
    "seedream_final_4k": "si",          # tras Seedream: Nano Banana rehace la imagen en 4K (no bloquea retoques)
    # ── Control de fidelidad de prenda (inspector automático post-generación) ──
    "qc_prenda": "si",                  # inspecciona cada imagen vs las fotos reales
    "qc_model": "gemini-3.1-flash",     # modelo del inspector (flash entiende mejor que lite)
    "qc_umbral": 9,                     # nota mínima (1-10); menos que esto = reintenta
    "qc_reintento": "si",               # reintenta con las correcciones detectadas
    "qc_estricto": "si",                # si tras corregir no llega al umbral: se marca DESCARTADA
    # ── Borrador barato antes de la final (ahorra 4K malgastadas) ──
    "borrador": "no",                   # "no" | "auto" (corta si no pasa) | "preguntar"
    "borrador_size": "1K",              # resolución del borrador
    "system_instruction": (
        "Marca: LUMA Íntima (ropa interior y prendas íntimas, Argentina). "
        "Regla principal: las prendas siempre fieles a la foto real del producto — "
        "nunca inventes ni cambies diseño, color, terminaciones ni detalles."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# ESTILOS (bloques de look reusables; se eligen por generación)
# El estilo define la VIBRA/fotografía. La prenda siempre la manda la foto real.
# ─────────────────────────────────────────────────────────────────────────────

STYLE_PRESETS: Dict[str, Dict[str, str]] = {
    "instagram_real": {
        "label": "Instagram casual realista",
        "text": (
            "ESTILO: foto real estilo Instagram, tomada al pasar en interiores con un celular "
            "o cámara común, NO una producción. Que parezca una foto de verdad, no una imagen "
            "generada. Poses naturales y espontáneas, sin posar: postura relajada, sonrisa "
            "auténtica, pelo movido, gestos casuales. Piel con textura real y natural (poros, "
            "brillos suaves), tonos de piel auténticos. Iluminación ambiental de día, colores "
            "equilibrados, look de foto tomada por un amigo. Enfoque natural en la cara y fondo "
            "levemente desenfocado. Evitá el aspecto de render/CGI, la piel plástica, la "
            "sobre-nitidez y la perfección de catálogo: buscá una foto casual creíble."
        ),
    },
    "catalogo": {
        "label": "Catálogo sobrio",
        "text": (
            "ESTILO: foto de catálogo de e-commerce, estudio profesional, sobria y elegante. "
            "Pose limpia de catálogo, fondo neutro, luz de estudio pareja y suave, colores "
            "fieles, texturas nítidas. Apta para tienda online y redes."
        ),
    },
    "editorial": {
        "label": "Editorial / campaña",
        "text": (
            "ESTILO: fotografía editorial de moda, dirección de arte cuidada, iluminación "
            "con intención, composición con carácter pero elegante. Realista, sin exagerar, "
            "texturas y piel naturales. Para campaña de marca."
        ),
    },
}


def _style_text(style: str, settings: Dict[str, Any]) -> str:
    key = style or settings.get("default_style", "instagram_real")
    return STYLE_PRESETS.get(key, STYLE_PRESETS["instagram_real"])["text"]

# ─────────────────────────────────────────────────────────────────────────────
# KV STORE  (REDIS_URL como tu get_redis()  ->  Upstash REST  ->  memoria)
# ─────────────────────────────────────────────────────────────────────────────


class KV:
    """Clave-valor con JSON. Prioriza REDIS_URL (igual que el get_redis() de la app)."""

    def __init__(self) -> None:
        self.redis_url = os.getenv("REDIS_URL", "")
        self.upstash_url = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
        self.upstash_token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
        self._mem: Dict[str, str] = {}
        self._redis = None
        self.backend = "memoria"
        self.last_error = ""

        # 1) REDIS_URL — mismo camino probado que usa toda tu app
        if self.redis_url:
            try:
                import redis  # type: ignore
                if self.redis_url.startswith("rediss://"):
                    self._redis = redis.from_url(
                        self.redis_url, decode_responses=True, socket_timeout=5,
                        ssl_cert_reqs=None)
                else:
                    self._redis = redis.from_url(
                        self.redis_url, decode_responses=True, socket_timeout=5)
                self._redis.ping()
                self.backend = "redis"
            except Exception as e:
                self.last_error = f"redis init: {e}"
                self._redis = None

        # 2) Upstash REST — solo si no hubo REDIS_URL utilizable
        if self.backend == "memoria" and self.upstash_url and self.upstash_token:
            self.backend = "upstash"

        if self.backend == "memoria":
            print(f"[imagenes_ia][KV] ⚠ Sin Redis: usando MEMORIA (no persiste). "
                  f"{self.last_error}")

    async def _upstash(self, command: List[Any]) -> Any:
        headers = {"Authorization": f"Bearer {self.upstash_token}"}
        async with httpx.AsyncClient(timeout=20) as cli:
            r = await cli.post(self.upstash_url, json=command, headers=headers)
            r.raise_for_status()
            return r.json().get("result")

    async def get(self, key: str) -> Optional[Any]:
        try:
            if self.backend == "redis":
                raw = await asyncio.to_thread(self._redis.get, key)
            elif self.backend == "upstash":
                raw = await self._upstash(["GET", key])
            else:
                raw = self._mem.get(key)
            return json.loads(raw) if raw else None
        except Exception as e:
            self.last_error = f"get {key}: {e}"
            print(f"[imagenes_ia][KV] {self.last_error}")
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        raw = json.dumps(value, ensure_ascii=False)
        try:
            if self.backend == "redis":
                if ttl:
                    await asyncio.to_thread(self._redis.set, key, raw, ex=ttl)
                else:
                    await asyncio.to_thread(self._redis.set, key, raw)
            elif self.backend == "upstash":
                cmd = ["SET", key, raw] + (["EX", str(ttl)] if ttl else [])
                await self._upstash(cmd)
            else:
                self._mem[key] = raw
            return True
        except Exception as e:
            self.last_error = f"set {key} ({len(raw)} bytes): {e}"
            print(f"[imagenes_ia][KV] {self.last_error}")
            return False

    async def delete(self, key: str) -> bool:
        try:
            if self.backend == "redis":
                await asyncio.to_thread(self._redis.delete, key)
            elif self.backend == "upstash":
                await self._upstash(["DEL", key])
            else:
                self._mem.pop(key, None)
            return True
        except Exception as e:
            self.last_error = f"del {key}: {e}"
            print(f"[imagenes_ia][KV] {self.last_error}")
            return False


kv = KV()

import contextvars

# Sub del usuario logueado en el request actual (lo setea el gate en main.py).
CURRENT_SUB: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "current_sub", default=None)


def set_current_sub(sub: Optional[str]) -> None:
    try:
        CURRENT_SUB.set(sub or None)
    except Exception:
        pass


def _pfx() -> str:
    """Prefijo de claves. Con usuario logueado, aísla los datos por cuenta.
    Sin usuario (ej. montado en ML×TN sin login), usa el espacio global de siempre."""
    sub = CURRENT_SUB.get()
    return f"imagenes:u:{sub}:" if sub else "imagenes:"


def k_settings() -> str:
    return _pfx() + "settings"


def k_avatars() -> str:
    return _pfx() + "avatars"


def k_avref(avatar_id: str) -> str:
    return _pfx() + "avref:" + avatar_id


def k_cap() -> str:
    return _pfx() + "budget:cap"


def k_templates() -> str:
    return _pfx() + "templates"


K_DRIVE = "imagenes:drive"           # credenciales/estado de Google Drive (legacy global)


def _ledger_key(month: Optional[str] = None) -> str:
    month = month or _dt.date.today().strftime("%Y-%m")
    return _pfx() + f"ledger:{month}"


# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────────────────────────────


_FLUX_SLUGS_VIEJOS = {"fal-ai/flux-2/lora", "fal-ai/flux-2-pro/edit",
                      "fal-ai/flux-2-lora-gallery/virtual-tryon", "fal-ai/flux-2/dev",
                      "fal-ai/flux-2/edit", "fal-ai/bytedance/seedream/v4.5/edit",
                      "fal-ai/bytedance/seedream/v5/pro/edit"}


async def get_settings() -> Dict[str, Any]:
    s = await kv.get(k_settings())
    merged = dict(DEFAULT_SETTINGS)
    if isinstance(s, dict):
        merged.update(s)
    # Migración de modelos FLUX que dieron mala calidad o bloqueos, hacia FLUX.2 dev
    # (calidad alta + moderación liviana). Si elegiste otro a mano distinto, se respeta.
    for k in ("flux_tryon_model", "flux_edit_model"):
        if str(merged.get(k, "")).strip() in _FLUX_SLUGS_VIEJOS:
            merged[k] = DEFAULT_SETTINGS[k]
    if merged.get("qc_umbral") == 7:      # umbral viejo por defecto → ahora exigimos 9
        merged["qc_umbral"] = 9
    merged["borrador"] = "no"             # borradores ELIMINADOS (eran para FLUX): nunca más
    if str(merged.get("flux_fallback_model", "")).strip() in (
            "fal-ai/flux-2/lora", "fal-ai/flux-2-lora-gallery/virtual-tryon"):
        merged["flux_fallback_model"] = DEFAULT_SETTINGS["flux_fallback_model"]
    return merged


async def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    s = await get_settings()
    for k, v in patch.items():
        if k in DEFAULT_SETTINGS:
            s[k] = v
    await kv.set(k_settings(), s)
    return s


# ─────────────────────────────────────────────────────────────────────────────
# AVATARES
# ─────────────────────────────────────────────────────────────────────────────


async def _avatar_store() -> Dict[str, Any]:
    data = await kv.get(k_avatars())
    if not isinstance(data, dict):
        data = {}
    return data


async def list_avatars() -> Dict[str, List[Optional[Dict[str, Any]]]]:
    """Devuelve 6 slots por genero (None = vacio). Lee solo el indice liviano."""
    store = await _avatar_store()
    out: Dict[str, List[Optional[Dict[str, Any]]]] = {}
    for g in GENEROS:
        fila: List[Optional[Dict[str, Any]]] = [None] * SLOTS_POR_GENERO
        for av in store.get(g, []):
            slot = av.get("slot")
            if isinstance(slot, int) and 0 <= slot < SLOTS_POR_GENERO:
                fila[slot] = {
                    "id": av["id"], "name": av.get("name", ""),
                    "gender": g, "slot": slot,
                    "description": av.get("description", ""),
                    "locked": av.get("locked", True),
                    "has_ref": True,
                    "created_at": av.get("created_at"),
                }
        out[g] = fila
    return out


async def get_avatar_ref(avatar_id: str) -> Optional[str]:
    """Devuelve el b64 de la referencia (blob separado).
    Compatibilidad: si no existe el blob, busca la foto embebida en el índice
    (formato viejo) y la migra al blob separado para futuras lecturas."""
    ref = await kv.get(k_avref(avatar_id))
    if ref:
        return ref
    store = await _avatar_store()
    for g in GENEROS:
        for av in store.get(g, []):
            if av.get("id") == avatar_id and av.get("ref_b64"):
                await kv.set(k_avref(avatar_id), av["ref_b64"])  # auto-migración
                return av["ref_b64"]
    return None


async def get_avatar(avatar_id: str) -> Optional[Dict[str, Any]]:
    """Devuelve metadata + ref_b64 (busca el blob aparte)."""
    store = await _avatar_store()
    for g in GENEROS:
        for av in store.get(g, []):
            if av.get("id") == avatar_id:
                meta = dict(av)
                meta["ref_b64"] = await get_avatar_ref(avatar_id)
                return meta
    return None


async def save_avatar(av: Dict[str, Any]) -> bool:
    """Guarda la referencia (blob) y el indice. Verifica que ambos persistan."""
    ref_b64 = av.pop("ref_b64", "")
    if not ref_b64:
        return False

    # 1) blob de la referencia
    ok_ref = await kv.set(k_avref(av["id"]), ref_b64)
    if not ok_ref:
        return False

    # 2) indice (solo metadata; un avatar por slot)
    store = await _avatar_store()
    g = av["gender"]
    fila = [a for a in store.get(g, []) if a.get("slot") != av["slot"]]
    fila.append(av)
    store[g] = fila
    ok_idx = await kv.set(k_avatars(), store)
    if not ok_idx:
        return False

    # 3) verificacion real de lectura
    check = await kv.get(k_avref(av["id"]))
    return bool(check)


async def delete_avatar(avatar_id: str) -> bool:
    store = await _avatar_store()
    changed = False
    for g in GENEROS:
        nueva = [a for a in store.get(g, []) if a.get("id") != avatar_id]
        if len(nueva) != len(store.get(g, [])):
            store[g] = nueva
            changed = True
    if changed:
        await kv.set(k_avatars(), store)
        await kv.delete(k_avref(avatar_id))
    return changed


# ─────────────────────────────────────────────────────────────────────────────
# PRESUPUESTO / LEDGER
# ─────────────────────────────────────────────────────────────────────────────


async def get_cap() -> Optional[float]:
    c = await kv.get(k_cap())
    if isinstance(c, dict):
        return c.get("monthly_usd")
    return None


async def set_cap(monthly_usd: Optional[float]) -> None:
    await kv.set(k_cap(), {"monthly_usd": monthly_usd})


async def get_templates() -> Dict[str, Any]:
    t = await kv.get(k_templates())
    return t if isinstance(t, dict) else {}


async def save_template(name: str, data: Dict[str, Any]) -> bool:
    t = await get_templates()
    t[name] = data
    return await kv.set(k_templates(), t)


async def delete_template(name: str) -> bool:
    t = await get_templates()
    if name in t:
        del t[name]
        return await kv.set(k_templates(), t)
    return False


async def get_ledger(month: Optional[str] = None) -> Dict[str, Any]:
    led = await kv.get(_ledger_key(month))
    if not isinstance(led, dict):
        led = {"month": month or _dt.date.today().strftime("%Y-%m"),
               "total": 0.0, "assets": 0, "records": []}
    return led


async def budget_check(est_cost: float) -> Tuple[bool, str, float, Optional[float]]:
    """Devuelve (permitido, motivo, total_mes_actual, cap)."""
    cap = await get_cap()
    led = await get_ledger()
    total = float(led.get("total", 0.0))
    if cap is not None and (total + est_cost) > cap + 1e-9:
        return (False,
                f"Tope mensual alcanzado: ya gastaste US${total:.2f} de US${cap:.2f}. "
                f"Esta generación sumaría US${est_cost:.2f}.",
                total, cap)
    return (True, "", total, cap)


async def budget_record(mode: str, image_size: str, cost: float,
                        assets: int, note: str = "") -> Dict[str, Any]:
    led = await get_ledger()
    rec = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "mode": mode, "size": image_size,
        "cost": round(cost, 4), "assets": assets,
        "cost_per_asset": round(cost / max(assets, 1), 4),
        "note": note,
    }
    led["records"].insert(0, rec)
    led["records"] = led["records"][:300]
    led["total"] = round(float(led.get("total", 0.0)) + cost, 4)
    led["assets"] = int(led.get("assets", 0)) + assets
    await kv.set(_ledger_key(), led)
    return rec


# ─────────────────────────────────────────────────────────────────────────────
# PROMPTS (bien robustos, anclados a la foto real del producto)
# ─────────────────────────────────────────────────────────────────────────────


def _bloque_consistencia(n: int) -> str:
    if n <= 0:
        return ""
    return (
        "\n\nMISMA PERSONA Y MISMA PRENDA (CRÍTICO — cada imagen llega con un rótulo de texto "
        "justo antes que dice qué es; guiate por los rótulos, no por el orden):\n"
        "• Las imágenes rotuladas «TOMA PREVIA DEL SET» son tomas anteriores de ESTA misma "
        "sesión y sirven ÚNICAMENTE como referencia de la PERSONA: su cara y rasgos, su color y "
        "corte de pelo, su tono de piel y su cuerpo/proporciones. Es exactamente la misma "
        "persona: no la reinventes ni le cambies la cara ni el cuerpo.\n"
        "• Las tomas previas NO son la referencia de la prenda. La prenda se copia SIEMPRE de "
        "las imágenes rotuladas «FOTO REAL DEL PRODUCTO» (única verdad para diseño, estampa, "
        "textura, color y terminaciones). La estampa tiene que ser LA MISMA tela exacta en "
        "todas las tomas: mismos dibujos, mismas palabras, misma escala, mismo color de fondo. "
        "Si una toma previa muestra la prenda distinta de la foto real, la foto real manda. "
        "PROHIBIDO inventar otra estampa parecida.\n"
        "• Esto vale también si esta toma es de ESPALDA o de PERFIL: aunque la cara se vea "
        "poco o nada, el peinado, el corte y color de pelo, el tono de piel, la edad y el "
        "físico son los de ESA misma persona — no pongas a otra persona.\n"
        "• Tampoco copies de las tomas previas la pose ni el encuadre: esta toma usa la pose "
        "indicada más abajo."
    )


def _bloque_detalles(p: Dict[str, Any]) -> str:
    campos = [
        ("Tela", p.get("tela")),
        ("Puños", p.get("punos")),
        ("Costuras", p.get("costuras")),
        ("Cuello/escote", p.get("cuello")),
        ("Color real", p.get("color")),
        ("Talle/calce", p.get("calce")),
    ]
    base = "\n".join(f"- {k}: {v}" for k, v in campos if v)
    color_set = (p.get("color_set") or "").strip()
    if color_set:
        base += (f"\n\n⚠ COLOR DE LA PRENDA EN ESTA TOMA (obligatorio): la prenda va en color "
                 f"{color_set}. Es el MISMO modelo y diseño de la foto real, cambiando ÚNICAMENTE "
                 f"el color de la tela a {color_set}. Respetá estampa/textura/calce; solo cambia "
                 f"el color.")
    acl = (p.get("aclaraciones") or "").strip()
    if acl:
        base += (f"\n\n⚠ ACLARACIONES OBLIGATORIAS DE LA USUARIA (máxima prioridad, por encima "
                 f"de cualquier interpretación tuya; respetar al pie de la letra y NO hacer lo "
                 f"contrario): {acl}")
    ficha = (p.get("ficha") or "").strip()
    if ficha:
        base += (f"\n\nFICHA (solo ayuda SECUNDARIA, NO es la verdad): es una descripción "
                 f"auxiliar de apoyo. La VERDAD es la FOTO REAL del producto. Usá la ficha solo "
                 f"para entender detalles que no se ven claros en la foto; si la ficha dice algo "
                 f"distinto de lo que muestra la foto, IGNORÁ la ficha y copiá la foto.\n{ficha}")
    return base


POSE_POOL = [
    "CUERPO ENTERO, de pie con el peso en una pierna y la cadera quebrada, una mano jugando "
    "con el pelo, cuerpo en leve torsión, actitud relajada y desprevenida",
    "PLANO MEDIO, girada en 3/4 mirando por encima del hombro hacia la cámara, pelo en "
    "movimiento como si recién se hubiera dado vuelta",
    "PLANO MEDIO/AMERICANO, sentada de forma relajada (en un sillón, cama o banco), torso erguido "
    "y en leve giro, manos apoyadas con naturalidad, piernas fuera de cuadro o apenas insinuadas",
    "DE ESPALDA mostrando la parte de atrás de la prenda, cabeza girada hacia la cámara, "
    "una mano en la nuca, cuerpo en contrapposto",
    "CUERPO ENTERO, caminando hacia la cámara en pleno paso, una pierna adelante, brazos "
    "sueltos en movimiento, dinámica y natural",
    "PLANO MEDIO, en plena risa genuina con la cabeza apenas hacia atrás, mirada fuera de "
    "cuadro, gesto totalmente desprevenido",
    "CUERPO ENTERO, apoyada de costado contra una pared, de perfil, una pierna cruzada y un "
    "pie en punta, mirada relajada a lo lejos",
    "PLANO MEDIO, estirándose con naturalidad o acomodándose un bretel, hombros sueltos, "
    "expresión fresca de momento real",
    "PRIMER PLANO / PLANO MEDIO CORTO de cara y hombros, la cara ocupa gran parte del cuadro, "
    "leve giro de cabeza, mirada a cámara, foco total en el detalle de la piel y la expresión",
    # 9-13: poses de CATÁLOGO EDITORIAL real (detalle de prenda, fusión con el ambiente)
    "PLANO DETALLE DE PRODUCTO (zoom): encuadre CERRADO del torso a los muslos, SIN cara (la "
    "cabeza queda fuera de cuadro o cortada a la altura del mentón, como en un catálogo real). "
    "La prenda OCUPA el cuadro y es la única protagonista: tela, costuras y terminaciones "
    "nítidas en primer plano, piel real alrededor, luz que marca la textura",
    "PLANO DETALLE DE ESPALDA (zoom): encuadre cerrado de la espalda, de la nuca a la cadera, "
    "las manos acomodándose o atándose la prenda por detrás en pleno gesto, cara fuera de "
    "cuadro o apenas de perfil, foco en el cierre/terminación trasera de la prenda",
    "CUERPO ENTERO O AMERICANO, FUNDIDA con el ambiente: apoyada, recostada o trepada sobre un "
    "elemento REAL de la escena (una palmera, una roca, una pared, el marco de una puerta, el "
    "respaldo de un sillón — lo que la escena tenga), el cuerpo en contacto natural con ese "
    "elemento, mirada lejos de la cámara, como un descuido editorial",
    "TIRADA O SENTADA EN EL PISO de la escena (la arena, el pasto, una alfombra, la cama — lo "
    "que corresponda al ambiente), cuerpo relajado en el suelo, apoyada en los antebrazos o de "
    "costado, el entorno tocando la piel, mirada perdida o a cámara desde abajo",
    "PLANO MEDIO con UTILERÍA casual coherente con la escena en la mano (una fruta, una flor, "
    "un vaso o botella, un sombrero, una taza — UN solo objeto simple), interactuando con el "
    "objeto de forma desprevenida, mirada al objeto o fuera de cuadro, momento robado real",
]

# Variaciones de expresión/mirada que se suman AL AZAR para que ninguna toma se repita
EXPRESION_VARIANTS = [
    "Expresión: risa real y espontánea, ojos vivos.",
    "Expresión: media sonrisa cómplice, mirada a un costado.",
    "Expresión: serena y natural, mirada suave y cálida.",
    "Expresión: fresca y desprevenida, como en un momento real no posado.",
    "Expresión: sonrisa amplia y luminosa, energía positiva.",
    "Expresión: pensativa y relajada, mirada perdida fuera de cuadro.",
    "Expresión: sutil y elegante, mentón apenas bajo, mirada intensa.",
]


def _expr() -> str:
    return random.choice(EXPRESION_VARIANTS)


# ── Poses masculinas (mismo índice/tamaño de plano que POSE_POOL, en gramática varón) ──
POSE_POOL_H = [
    "CUERPO ENTERO, de pie con el peso en una pierna, una mano relajada al costado y la otra "
    "suelta, hombros relajados, cuerpo en leve torsión, actitud segura y desprevenida",
    "PLANO MEDIO, girado en 3/4 mirando por encima del hombro hacia la cámara, expresión "
    "tranquila y natural",
    "PLANO MEDIO/AMERICANO, sentado de forma relajada (en un sillón, cama o banco), torso "
    "erguido y en leve giro, antebrazos apoyados con naturalidad, piernas fuera de cuadro o "
    "apenas insinuadas",
    "DE ESPALDA mostrando la parte de atrás de la prenda, cabeza girada hacia la cámara, "
    "una mano al costado, cuerpo en contrapposto",
    "CUERPO ENTERO, caminando hacia la cámara en pleno paso, una pierna adelante, brazos "
    "sueltos en movimiento, dinámico y natural",
    "PLANO MEDIO, riéndose de forma genuina con la cabeza apenas hacia atrás, mirada fuera de "
    "cuadro, gesto totalmente desprevenido",
    "CUERPO ENTERO, apoyado de costado contra una pared, de perfil, un pie cruzado sobre el "
    "otro, mirada relajada a lo lejos",
    "PLANO MEDIO, pasándose una mano por el pelo, hombros sueltos, "
    "expresión fresca de momento real",
    "PRIMER PLANO / PLANO MEDIO CORTO de cara y hombros, la cara ocupa gran parte del cuadro, "
    "leve giro de cabeza, mirada a cámara, foco total en el detalle de la piel y la expresión",
    # 9-13: poses de CATÁLOGO EDITORIAL real (detalle de prenda, fusión con el ambiente)
    "PLANO DETALLE DE PRODUCTO (zoom): encuadre CERRADO del torso a los muslos, SIN cara (la "
    "cabeza queda fuera de cuadro o cortada a la altura del mentón, como en un catálogo real). "
    "La prenda OCUPA el cuadro y es la única protagonista: tela, costuras y terminaciones "
    "nítidas en primer plano, piel real alrededor, luz que marca la textura",
    "PLANO DETALLE DE ESPALDA (zoom): encuadre cerrado de la espalda, de la nuca a la cadera, "
    "las manos acomodándose la prenda por detrás en pleno gesto, cara fuera de cuadro o "
    "apenas de perfil, foco en la terminación trasera de la prenda",
    "CUERPO ENTERO O AMERICANO, FUNDIDO con el ambiente: apoyado o recostado sobre un "
    "elemento REAL de la escena (una palmera, una roca, una pared, el marco de una puerta, el "
    "respaldo de un sillón — lo que la escena tenga), el cuerpo en contacto natural con ese "
    "elemento, mirada lejos de la cámara, como un descuido editorial",
    "TIRADO O SENTADO EN EL PISO de la escena (la arena, el pasto, una alfombra, la cama — lo "
    "que corresponda al ambiente), cuerpo relajado en el suelo, apoyado en los antebrazos o de "
    "costado, el entorno tocando la piel, mirada perdida o a cámara desde abajo",
    "PLANO MEDIO con UTILERÍA casual coherente con la escena en la mano (una fruta, un vaso o "
    "botella, un sombrero, una taza de café — UN solo objeto simple), interactuando con el "
    "objeto de forma desprevenida, mirada al objeto o fuera de cuadro, momento robado real",
]

# Poses en INGLÉS para Seedream (mismo índice que POSE_POOL): el prompt de Seedream va
# en inglés y las poses en castellano las IGNORABA — hacía siempre la pose default parada.
POSE_POOL_FLUX = [
    "FULL BODY, standing with the weight on one leg, hip popped, one hand playing with "
    "the hair, slight torso twist, relaxed candid attitude",
    "MEDIUM SHOT, body turned 3/4 away, looking back over the shoulder at the camera, "
    "hair in motion as if they just turned around",
    "MEDIUM SHOT, SITTING relaxed (on a couch, bed or bench), upright torso with a "
    "slight turn, hands resting naturally",
    "SEEN FROM BEHIND, showing the BACK of the garment, head turned toward the camera, "
    "one hand on the nape, contrapposto",
    "FULL BODY, WALKING toward the camera mid-step, one leg forward, arms loose and in "
    "motion, dynamic and natural",
    "MEDIUM SHOT, genuine laugh with the head slightly back, looking off-camera, candid "
    "caught moment",
    "FULL BODY, leaning sideways against a wall, in SIDE PROFILE, one leg crossed, "
    "relaxed gaze into the distance",
    "MEDIUM SHOT, stretching naturally or adjusting a strap, loose shoulders, fresh "
    "real-moment expression",
    "EXTREME CLOSE-UP of face and shoulders, the face fills the frame, slight head "
    "turn, eyes to camera, focus on skin detail and expression",
    "PRODUCT DETAIL SHOT (zoom): tight crop from torso to thighs, NO face (head out of "
    "frame or cropped at the chin, like a real catalog detail shot — this is "
    "intentional), the garment fills the frame, fabric and seams tack-sharp",
    "BACK DETAIL SHOT (zoom): tight crop of the back from nape to hips, hands adjusting "
    "or tying the garment behind the back, face out of frame or barely in profile, "
    "focus on the back closure and finish of the garment",
    "FULL BODY OR 3/4, physically INTERACTING with the scene: leaning or reclining on a "
    "real element of the set (a palm tree, a rock, a wall, a door frame, a couch), body "
    "in natural contact with it, gaze away from the camera, editorial candid",
    "LYING OR SITTING ON THE GROUND of the scene (the sand, the grass, a rug, the bed), "
    "body relaxed on the floor, propped on the forearms or on one side, the environment "
    "touching the skin, gaze lost or up toward the camera",
    "MEDIUM SHOT holding ONE simple casual prop coherent with the scene (a fruit, a "
    "glass or bottle, a hat, a mug), interacting with it candidly, gaze at the prop or "
    "off-camera, stolen-moment feel",
]

# Traducción por palabras clave para poses ESCRITAS en castellano → refuerzo en inglés
_POSE_EN_KW = [
    (("espalda", "de atras", "de atrás"), "seen from BEHIND, the back of the garment to the camera"),
    (("sentada", "sentado"), "SITTING"),
    (("caminando", "camina"), "WALKING toward the camera, mid-step"),
    (("perfil",), "in SIDE PROFILE"),
    (("tirada", "tirado", "acostada", "acostado", "recostada", "recostado"), "LYING DOWN"),
    (("arrodillada", "arrodillado", "de rodillas"), "KNEELING"),
    (("agachada", "agachado", "en cuclillas"), "CROUCHING"),
    (("apoyada", "apoyado"), "LEANING on it"),
    (("de pie", "parada", "parado"), "STANDING"),
    (("mano en el pelo", "tocando el pelo", "tocandose el pelo", "tocándose el pelo"),
     "one hand in the hair"),
    (("mano en la cintura", "manos en la cintura"), "hand on hip"),
    (("por encima del hombro", "sobre el hombro"), "looking back over the shoulder"),
    (("detalle", "zoom", "primer plano de la prenda"),
     "tight close-up DETAIL crop of the garment, face out of frame"),
    (("saltando", "salta"), "JUMPING mid-air"),
    (("bailando", "baila"), "DANCING, body in motion"),
]


def _pose_en_hints(txt: str) -> str:
    """Suma equivalentes en inglés a una pose escrita en castellano (Seedream lee inglés)."""
    low = txt.lower()
    hits = [en for kws, en in _POSE_EN_KW if any(k in low for k in kws)]
    return (" (in English: " + "; ".join(hits) + ")") if hits else ""


_MASC = ("hombre", "varon", "varón", "masculino", "hombres", "h", "m", "male")


def _es_hombre(genero: Optional[str]) -> bool:
    return str(genero or "").strip().lower() in _MASC


def _pose_pool(genero: Optional[str]) -> List[str]:
    return POSE_POOL_H if _es_hombre(genero) else POSE_POOL


def _gwords(genero: Optional[str]) -> Dict[str, str]:
    """Palabras que cambian según el género de la modelo, para no pedir 'mujer' a un hombre."""
    if _es_hombre(genero):
        return {"modelo": "el modelo", "modelo_cap": "El modelo",
                "persona": "un varón adulto", "una_sola": "un solo modelo adulto (varón)",
                "genero_n": "varón", "gen_adj": "masculino"}
    return {"modelo": "la modelo", "modelo_cap": "La modelo",
            "persona": "una mujer adulta", "una_sola": "una sola modelo adulta (mujer)",
            "genero_n": "mujer", "gen_adj": "femenina"}


VIDA_BLOCK = (
    "ENERGÍA Y NATURALIDAD (muy importante): es FOTOGRAFÍA EDITORIAL DE CAMPAÑA real y "
    "espontánea (como un catálogo impreso de una marca top), NO catálogo rígido ni pose de "
    "maniquí. La persona está captada en pleno gesto o movimiento. "
    "Postura asimétrica: peso descargado en una pierna, cadera y hombros relajados, "
    "leve torsión del torso; nunca de frente perfecta y simétrica. Manos con intención (en el "
    "pelo, en la cintura, acomodándose la prenda), nunca pegadas y rígidas al cuerpo. Variá la "
    "MIRADA: no siempre a cámara — perfil, mirada perdida a lo lejos, ojos entrecerrados por el "
    "sol, incluso ojos cerrados valen; que parezca un momento robado, no una pose pedida. "
    "La persona INTERACTÚA con el ambiente real: se apoya, se sienta o se recuesta sobre "
    "elementos concretos de la escena (no flota delante de un fondo). La luz de la escena le "
    "pega de verdad al cuerpo: sombras reales, brillos donde da la luz. Que se sienta un "
    "instante real, con vida y movimiento (pelo, tela). EVITÁ: simetría, sonrisa fija de "
    "catálogo, expresión congelada, brazos tiesos, pose acartonada, persona 'pegada' sobre el "
    "fondo sin tocar nada."
)

CALIDAD_BLOCK = (
    "NATURALIDAD (que NO parezca imagen generada): tiene que verse como una foto real, no como "
    "un render. Piel con textura natural, poros y algún matiz o brillo real donde pega la luz; "
    "ni plástica/cerosa ni con imperfecciones exageradas — piel sana y creíble. Ojos con detalle "
    "y brillo natural. Pelo con algunos pelitos sueltos, no un bloque sólido. Manos y pies "
    "correctos: cinco dedos por mano y por pie, sin dedos de más o de menos ni manos deformadas. "
    "Proporciones humanas naturales y postura coherente. Evitá el aspecto 3D/CGI, el "
    "sobre-nitidez artificial y la simetría perfecta."
)

CLOSEUP_BLOCK = (
    "\nPRIMERÍSIMO PLANO PROFESIONAL: cámara medium format, lente 85mm, apertura f/2.2, enfoque "
    "tack-sharp en los ojos; detalle MACRO del iris y sus fibras, catchlight natural en las "
    "pupilas, pestañas nítidas una por una; máximo detalle de poros y textura de piel; pelo con "
    "hebras sueltas; fondo con bokeh suave."
)

DETALLE_BLOCK = (
    "\nTOMA DE DETALLE PROFESIONAL (de catálogo impreso): lente 85-105mm, apertura f/4, foco "
    "clavado en la PRENDA; se distingue el tejido hilo por hilo, las costuras, los bordes y "
    "cualquier herraje o lazo con nitidez macro. La piel alrededor es piel REAL en primer "
    "plano: poros, vello finito, algún pliegue natural — nada de piel plástica lisa. Fondo con "
    "desenfoque suave que sitúa la escena. La cara NO es protagonista en esta toma: queda "
    "fuera de cuadro o cortada por el encuadre, como en las tomas de detalle de un catálogo "
    "real — eso NO es un error, es el encuadre pedido."
)


def _bloque_paneles(n: int, aspect: str, pose_offset: int = 0,
                    genero: Optional[str] = None) -> str:
    if n <= 1:
        return ""
    pool = _pose_pool(genero)
    poses = [pool[(pose_offset + i) % len(pool)] for i in range(n)]
    detalle = "\n".join(f"  · Panel {i + 1}: {p}. {_expr()}" for i, p in enumerate(poses))
    return (
        f"\nIMPORTANTE — {n} TOMAS DISTINTAS EN UNA SOLA IMAGEN ({aspect}):\n"
        f"Generá {n} fotos de la MISMA modelo con la MISMA prenda, lado a lado, separadas por "
        f"una LÍNEA BLANCA VERTICAL limpia, recta y pareja (blanco puro, ~1.5% del ancho).\n"
        f"Cada panel DEBE tener una pose CLARAMENTE distinta — distinta orientación del cuerpo, "
        f"distinto gesto y, MUY IMPORTANTE, distinto ENCUADRE (combiná cuerpo entero con plano "
        f"medio, primer plano o de espalda; NO todos del mismo tamaño de plano). NO repitas la "
        f"misma pose con cambios mínimos. Asigná exactamente estas poses:\n{detalle}\n"
        f"Poses espontáneas y desprevenidas (estilo Instagram), no acartonadas. Sin texto entre paneles."
    )


_LENC_KW = ("lenceria", "lencería", "corpiño", "corpino", "bralette", "bombacha", "tanga",
            "colaless", "encaje", "brasier", "brasiere", "sostén", "soften", "underwear",
            "lingerie", "bikini", "malla", "traje de baño", "conjunto de encaje",
            "boxer", "bóxer", "calzoncillo", "slip", "bombachón", "culotte")


def _es_ropa_interior(p: Dict[str, Any]) -> bool:
    """True si la toma es de ropa interior/lencería (corpiño/top solo, set de lencería, o
    cuando el texto del producto/tela lo indica)."""
    if str(p.get("complemento", "")).lower() in ("si", "sí", "true", "1", "on", "auto"):
        return True
    if str(p.get("color_set", "")).strip():
        return True
    if str(p.get("lenceria", "")).lower() in ("si", "sí", "true", "1", "on"):
        return True
    txt = " ".join(str(p.get(k, "")) for k in
                   ("producto_manual", "prenda_desc", "tela", "aclaraciones", "piezas")).lower()
    return any(kw in txt for kw in _LENC_KW)


def _bloque_pose_unica(idx: int, genero: Optional[str] = None) -> str:
    pool = _pose_pool(genero)
    pose = pool[idx % len(pool)]
    return (
        f"\nPOSE Y ENCUADRE DE ESTA TOMA (obligatorio, máxima prioridad): {pose}. {_expr()} "
        "Respetá exactamente esa orientación del cuerpo y ese tamaño de plano. "
        "Pose espontánea y desprevenida estilo Instagram, con vida, no acartonada."
    )


# Va pegado a la descripción del producto: ahí el modelo ya sabe QUÉ foto mirar.
_SOLO_LO_QUE_SE_VE = (
    "La prenda lleva SOLO los detalles que se ven en esa(s) foto(s): si un detalle no está en "
    "la foto, no existe en la prenda. Si el pantalón se ve liso y sin bolsillos, va liso y sin "
    "bolsillos, y las manos de la modelo quedan fuera de la prenda (no metidas en ningún lado)."
)


def _bloque_producto_ref(n_prod: int, primera_idx: int) -> str:
    """Describe las imágenes de referencia del producto (pueden ser varias vistas)."""
    if n_prod <= 1:
        return (
            f"IMAGEN {primera_idx}: es EL PRODUCTO REAL de LUMA Íntima y es la FUENTE PRINCIPAL "
            "y la VERDAD ABSOLUTA de la prenda. Copiá la prenda EXACTAMENTE como se ve en esa "
            "foto: mismo diseño, mismo escote y forma, color real, largo, estampa, detalles y "
            "terminaciones. Mirá bien la foto y reproducí ESA prenda puntual, no una parecida ni "
            "una versión genérica. Cualquier texto o ficha es SECUNDARIO y NO puede contradecir "
            "lo que muestra esta foto. NO inventes ni modifiques nada.\n"
            + _SOLO_LO_QUE_SE_VE
        )
    ult = primera_idx + n_prod - 1
    return (
        f"IMÁGENES {primera_idx} a {ult}: son VARIAS VISTAS del MISMO producto real de "
        "LUMA Íntima (por ejemplo: la parte de arriba, el pantalón, y/o un detalle de la "
        "tela). Juntas son la VERDAD ABSOLUTA de la prenda COMPLETA. Combiná todas las "
        "vistas para reproducir el conjunto entero EXACTAMENTE: mismo diseño, color real, "
        "largo, estampa, detalles y terminaciones en cada parte. NO inventes ninguna parte "
        "que no esté en las fotos ni modifiques nada.\n"
        + _SOLO_LO_QUE_SE_VE
    )


FIDELITY_FABRIC = (
    "FIDELIDAD DE TELA Y ESTAMPA (crítico): copiá la TEXTURA de la tela y la ESTAMPA "
    "EXACTAMENTE como se ven en las fotos del producto. Si la tela es lisa (coral/polar liso) "
    "con un dibujo IMPRESO, reproducila lisa con el mismo dibujo impreso — NO la conviertas en "
    "tejido cable, trenzado, matelasseado ni le agregues relieve/textura que la foto no tiene. "
    "Mantené los motivos de la estampa (personajes, caritas, textos) NÍTIDOS, reconocibles y con "
    "la misma forma, el mismo tamaño/escala y la misma distribución que en la foto; NO los "
    "reordenes, NO los simplifiques, NO los desparrames ni cambies su densidad. La estampa debe "
    "verse IDÉNTICA en todas las tomas (mismo dibujo, misma escala, mismos colores) — no varíes "
    "el patrón de una foto a otra. "
    "NO agregues puños, ribb, elásticos ni terminaciones en muñecas, tobillos o cintura que no "
    "se vean claramente en las fotos: si la manga o el pantalón son del mismo género estampado "
    "hasta el borde, dejalos así.\n"
    "Reproducí ÚNICAMENTE los detalles y terminaciones que se ven en las fotos del producto: "
    "no sumes ninguno que la prenda real no tenga.\n"
    "RELIEVE Y TEXTURA 3D (importante): si en la foto la tela es plush / coral fleece / polar "
    "afelpado / sherpa, reproducí el PELO que sobresale, mullido y con volumen real, con "
    "profundidad y sombras suaves entre las hebras — que se vea ESPONJOSO y abrigado, NO una tela "
    "lisa ni una superficie plana. Si los motivos (corazones, ositos, etc.) están EN RELIEVE "
    "(grabados/repujados en la tela), reproducilos en relieve 3D real con sus sombras, NO como un "
    "dibujo estampado plano encima. En resumen: respetá el volumen y el relieve tal cual la foto; "
    "PROHIBIDO aplanar la textura, dejarla 'pintada' o lisa cuando la prenda real es peluda o en "
    "relieve."
)


TIPO_BUSTO = {
    "chico": "busto pequeño", "mediano": "busto mediano", "grande": "busto grande",
    "extra_grande": ("busto extra grande y voluminoso (talle grande, tipo copa DD/E o mayor), "
                     "proporcionado y natural"),
}
TIPO_COLA = {
    "chica": "glúteos pequeños", "mediana": "glúteos medianos", "grande": "glúteos grandes",
    "extra_grande": ("glúteos y caderas extra grandes y anchas (talle grande), volumen marcado "
                     "y natural"),
}
TIPO_ABDOMEN = {
    "fit": "abdomen fit y tonificado", "plano": "abdomen plano natural",
    "natural": "abdomen natural con curvas suaves",
    "con_pancita": "abdomen con pancita suave y natural (real, sin disimular)",
}
TIPO_CONTEXTURA = {
    "delgada": "contextura delgada", "atletica": "contextura atlética y tonificada",
    "curvy": "contextura curvy con curvas marcadas",
    "talle_grande": ("cuerpo de talle grande / plus size REAL (talle 100-110): gordita de "
                     "verdad — pancita con volumen, brazos y muslos llenos, caderas anchas, "
                     "cintura ancha, rollitos suaves naturales. NO es una delgada 'con "
                     "curvas': es una mujer gordita real, mostrada linda y con naturalidad, "
                     "sin adelgazarla ni estilizarla"),
    "talle_extra_grande": ("cuerpo GORDO real de talle XXL (talle 120-130+): mujer gorda de "
                           "verdad — vientre amplio con volumen y caída natural, brazos "
                           "gruesos, muslos anchos que se tocan, caderas y espalda anchas "
                           "con rollitos visibles, mentón con papada suave. PROHIBIDO "
                           "adelgazarla, estilizarla o convertirla en 'curvy': el cuerpo es "
                           "GORDO con volumen real en todo el cuerpo, retratado con "
                           "dignidad, naturalidad y buena luz, como una campaña plus size "
                           "real"),
}
TIPO_EDAD_CORP = {
    "20": "cuerpo de mujer joven (alrededor de 20-25 años), piel firme y tersa",
    "30": "cuerpo de mujer de unos 30 años, natural",
    "40": "cuerpo de mujer de unos 40 años, natural y real",
    "50": "cuerpo de mujer de unos 50 años, natural y real",
}
TIPO_PEINADO = {
    "largo_suelto": "pelo largo y suelto",
    "largo_ondulado": "pelo largo y ondulado, suelto",
    "corto": "pelo corto",
    "media_melena": "pelo media melena (por los hombros)",
    "atado": "pelo atado en una cola",
    "rodete": "pelo recogido en un rodete/moño",
    "trenza": "pelo en una trenza",
    "rapado": "pelo rapado",
}

# Variantes MASCULINAS (acepta también las claves clásicas por compatibilidad)
TIPO_CONTEXTURA_H = {
    "delgado": "contextura delgada", "delgada": "contextura delgada",
    "atletico": "contextura atlética y tonificada", "atletica": "contextura atlética",
    "musculoso": "contextura musculosa y trabajada",
    "robusto": "contextura robusta", "curvy": "contextura robusta",
    "talle_grande": ("cuerpo de talle grande REAL: gordito de verdad — panza con volumen, "
                     "brazos y muslos llenos, torso ancho; NO es un flaco robusto, es un "
                     "hombre gordito real, sin adelgazarlo"),
    "talle_extra_grande": ("cuerpo GORDO real talle XXL: hombre gordo de verdad — panza "
                           "prominente con caída natural, brazos gruesos, muslos anchos, "
                           "pecho y espalda anchos, papada suave. PROHIBIDO adelgazarlo o "
                           "estilizarlo: volumen real en todo el cuerpo, retratado con "
                           "dignidad y naturalidad"),
}
TIPO_ABDOMEN_H = {
    "fit": "abdomen marcado y tonificado", "plano": "abdomen plano",
    "natural": "abdomen natural",
    "con_pancita": "con panza natural (real, sin disimular)",
    "con_panza": "con panza natural (real, sin disimular)",
}
BARBA_H = {
    "sin_barba": "bien afeitado, sin barba",
    "tres_dias": "barba de tres días",
    "corta": "barba corta y prolija",
    "completa": "barba completa",
    "bigote": "bigote",
}

TIPO_ALTURA = {
    "baja": "estatura baja (aprox. 1,55 m)",
    "media": "estatura media (aprox. 1,65 m)",
    "alta": "alta (aprox. 1,75 m)",
    "muy_alta": "muy alta (más de 1,80 m)",
}


APAR_ETNIA = {
    "latina": "latina", "morocha_tez_oscura": "latina morena de tez trigueña/oscura",
    "caucasica": "caucásica de piel clara", "afro": "afrodescendiente de piel oscura",
    "asiatica": "asiática", "mediterranea": "mediterránea de piel trigueña",
    "arabe": "de rasgos árabes / medio-oriente", "mestiza": "mestiza",
}
APAR_PELO = {
    "morocha_largo_ondulado": "pelo largo, ondulado y oscuro (morocha)",
    "negro_lacio": "pelo negro lacio", "castaño_largo": "pelo castaño largo",
    "rubia": "pelo rubio", "pelirroja": "pelo pelirrojo (colorada)",
    "castaño_ondulado": "pelo castaño ondulado", "corto": "pelo corto",
}
APAR_OJOS = {
    "marrones": "ojos marrones", "claros": "ojos claros",
    "verdes": "ojos verdes", "celestes": "ojos celestes", "negros": "ojos negros oscuros",
}
APAR_EDAD = {
    "joven": "joven (20-28 años)", "adulta": "adulta (28-38 años)",
    "madura": "madura (38-50 años)",
}


def _bloque_apariencia(p: Dict[str, Any], genero: Optional[str] = None) -> str:
    """Solo se usa cuando la IA inventa la modelo (sin avatar)."""
    partes = []
    for key, mapa in (("ap_edad", APAR_EDAD), ("ap_etnia", APAR_ETNIA),
                      ("ap_pelo", APAR_PELO), ("ap_ojos", APAR_OJOS)):
        v = str(p.get(key, "")).strip().lower()
        if v and v in mapa:
            partes.append(mapa[v])
    if _es_hombre(genero):
        barba = BARBA_H.get(str(p.get("ap_barba", "")).strip().lower())
        if barba:
            partes.append(barba)
    libre = str(p.get("ap_extra", "")).strip()
    if libre:
        partes.append(libre)
    if not partes:
        return ""
    base = "varón " if _es_hombre(genero) else "mujer "
    return base + ", ".join(partes) + ". "


def _bloque_cuerpo(p: Dict[str, Any], genero: Optional[str] = None) -> str:
    partes = []
    if _es_hombre(genero):
        # Hombre: sin busto/cola; contextura y abdomen en clave masculina + altura y edad.
        cont = TIPO_CONTEXTURA_H.get(str(p.get("cuerpo_contextura", "")).strip().lower())
        if cont:
            partes.append(cont)
        abd = TIPO_ABDOMEN_H.get(str(p.get("cuerpo_abdomen", "")).strip().lower())
        if abd:
            partes.append(abd)
        alt = TIPO_ALTURA.get(str(p.get("cuerpo_altura", "")).strip().lower())
        if alt:
            partes.append(alt)
        ed = str(p.get("cuerpo_edad", "")).strip()
        if ed:
            partes.append(f"varón de unos {ed} años" if ed.isdigit()
                          else f"edad: {ed}")
        encabezado = "TIPO DE CUERPO DEL MODELO (varón)"
    else:
        for key, mapa in (("cuerpo_busto", TIPO_BUSTO), ("cuerpo_cola", TIPO_COLA),
                          ("cuerpo_abdomen", TIPO_ABDOMEN),
                          ("cuerpo_contextura", TIPO_CONTEXTURA),
                          ("cuerpo_edad", TIPO_EDAD_CORP), ("cuerpo_altura", TIPO_ALTURA)):
            v = str(p.get(key, "")).strip().lower()
            if v and v in mapa:
                partes.append(mapa[v])
        encabezado = "TIPO DE CUERPO DE LA MODELO"
    pein = str(p.get("cuerpo_peinado", "")).strip().lower()
    if pein and pein in TIPO_PEINADO:
        partes.append(TIPO_PEINADO[pein] + " (manteniendo el MISMO color de pelo)")
    accs = str(p.get("cuerpo_accesorios", "")).strip()
    if accs:
        partes.append("accesorios: " + accs)
    if not partes:
        return ""
    if p.get("_cuerpo_prioritario"):
        encabezado += (" (así ES el cuerpo de la modelo en esta sesión — usá este bloque "
                       "como única definición del cuerpo)")
    return (
        "\n\n" + encabezado + " (respetá estas proporciones reales y naturales, sin "
        "exagerar ni deformar): " + ", ".join(partes) + ". Proporciones humanas creíbles y "
        "anatómicamente correctas; la prenda calza bien sobre ESE tipo de cuerpo."
    )


def _bloque_producto_manual(p: Dict[str, Any]) -> str:
    """Descripción del producto escrita por la usuaria (independiente del análisis de la IA)."""
    txt = str(p.get("producto_manual", "")).strip()
    if not txt:
        return ""
    return ("DESCRIPCIÓN DEL PRODUCTO POR LA USUARIA (VERDAD, junto con la foto real; respetala "
            "al pie de la letra y por encima de cualquier interpretación tuya): " + txt)


def _bloque_piezas(p: Dict[str, Any]) -> str:
    """Qué piezas del producto lleva puesta la modelo (ej: pijama de 3 piezas, usa solo 2)."""
    txt = str(p.get("piezas", "")).strip()
    if not txt:
        return ""
    return ("QUÉ PIEZAS USA LA MODELO EN ESTA TOMA (obligatorio, máxima prioridad): " + txt +
            ". Mostrá puestas EXACTAMENTE esas piezas y NINGUNA otra. Si el producto tiene otras "
            "piezas que no están en esa lista, esas piezas NO aparecen en la foto (ni puestas ni "
            "sostenidas ni al costado). Cada pieza es fiel a la foto real del producto.")


BEACHWEAR_BLOCK = (
    "MODO VERANO / BEACHWEAR — FOTOGRAFÍA EDITORIAL DE MODA (catálogo profesional): foto "
    "hiperrealista en EXTERIOR con luz natural. Cámara réflex full-frame (DSLR), ISO 100, f/8, "
    "1/1000 s, lente 85 mm; profundidad de campo compartida con el fondo levemente desenfocado. "
    "Día soleado con reflejos cálidos del sol que unifican a la modelo con el paisaje y "
    "destellos de luz en el agua. Tonos de piel reales y balanceados, color equilibrado, textura "
    "nítida de la tela, calidad editorial de moda. Es una producción de catálogo PROFESIONAL, "
    "elegante y de buen gusto: la prenda se ve con su calce real, favorecedor y prolijo, "
    "resaltando las curvas de manera natural; pose con actitud segura y relajada, estilo "
    "campaña de marca para una tienda online."
)


FONDO_NITIDO = (
    "ENFOQUE DEL FONDO: el fondo va NÍTIDO y detallado (gran profundidad de campo, tipo f/11–"
    "f/16, todo en foco). Se ve con claridad la TEXTURA de la arena, las olas rompiendo y la "
    "espuma, los árboles/vegetación y el paisaje. NO desenfoques ni difumines el fondo: ambiente "
    "y modelo, todo nítido."
)

VIENTO_BLOCK = (
    "SENSACIÓN VENTOSA: es un día con viento. El pelo y la tela se mueven con la brisa, mechones "
    "al viento, leve movimiento en la ropa; el mar con olas y espuma, ambiente fresco, dinámico "
    "y natural."
)


ESPALDA_GUARD = (
    "\nORIENTACIÓN DE ESTA TOMA: la modelo está DE ESPALDAS a la cámara y se ve la PARTE DE ATRÁS "
    "de la prenda. Las tomas previas de referencia son de FRENTE: seguí copiando de ellas la "
    "prenda EXACTA (misma estampa, mismos colores, misma tela) y la misma modelo, pero girá el "
    "cuerpo: en ESTA imagen se ve la espalda, no el frente. La parte de atrás debe ser la de la "
    "prenda real de las fotos."
)

ESPALDA_VERANO_SOFT = (
    "\nTOMA DE ESPALDA (estilo catálogo de moda): cuerpo entero, encuadre AMPLIO de pies a "
    "cabeza (NO primer plano de la cola), pose elegante girada 3/4 de espalda mirando por encima "
    "del hombro, actitud natural y de buen gusto, campaña de marca de trajes de baño."
)


def _modelo_spec(item: Dict[str, str], letra: str, img_idx: Optional[int]) -> str:
    """Describe una modelo del set: cara (avatar o etnia IA) + todas sus características."""
    partes = []
    if img_idx is not None:
        partes.append(f"cara/identidad = la de la IMAGEN {img_idx} (respetá sus rasgos exactos)")
    else:
        et = APAR_ETNIA.get(str(item.get('etnia', '')).lower())
        pe = APAR_PELO.get(str(item.get('pelo', '')).lower())
        if et:
            partes.append(f"mujer {et}")
        if pe:
            partes.append(pe)
    for key, mapa in (("contextura", TIPO_CONTEXTURA), ("edad", TIPO_EDAD_CORP),
                      ("busto", TIPO_BUSTO), ("cola", TIPO_COLA),
                      ("abdomen", TIPO_ABDOMEN), ("peinado", TIPO_PEINADO),
                      ("altura", TIPO_ALTURA)):
        v = mapa.get(str(item.get(key, '')).lower())
        if v:
            partes.append(v)
    desc = "; ".join(partes) if partes else "mujer adulta"
    return f"MODELO {letra} ({desc}) lleva la prenda en color {item.get('color', '')}"


_COMPOSICIONES_GRUPAL = [
    ("Composición: paradas una al lado de la otra pero a distintas profundidades (una apenas "
     "adelante), abrazadas por la cintura, riéndose entre ellas; solo la del medio mira a "
     "cámara."),
    ("Composición: caminando juntas hacia la cámara como saliendo de una sesión, en movimiento "
     "natural, una acomodándose el pelo, conversando entre risas."),
    ("Composición: la del medio de frente mirando a cámara; las de los costados giradas en "
     "3/4 hacia ella, apoyándole una mano en el hombro, sonrientes."),
    ("Composición: en ronda abierta como charlando, cuerpos levemente girados entre sí, una "
     "gesticulando con las manos, risas genuinas; ninguna posa para la cámara."),
    ("Composición: apoyadas contra la pared en distintas posturas relajadas (una de frente, "
     "una de 3/4, una casi de perfil mirando a las otras), estilo backstage de campaña."),
    ("Composición: una sentada en un banco alto y las otras dos paradas a sus costados "
     "inclinadas hacia ella, las tres a distinta altura, charla distendida."),
]


def build_prompt_trio(p: Dict[str, Any], settings: Dict[str, Any], asign: List[Dict[str, str]],
                      aspect: str, style: str = "", n_prod: int = 1,
                      img_map: Optional[List[Optional[int]]] = None, prod_primera: int = 1,
                      seguro: bool = False, full_refs: bool = False) -> str:
    """Foto grupal: 3 modelos. img_map[k] = nro de IMAGEN de referencia del modelo k (o None).
    full_refs=True → las referencias son TOMAS INDIVIDUALES completas ya aprobadas (cara +
    cuerpo + prenda + color), no solo caras. prod_primera = dónde empiezan las fotos del
    producto."""
    sysi = settings.get("system_instruction", "").strip()
    estilo = _style_text(style, settings)
    a = (asign or [])[:3]
    while len(a) < 3:
        a.append({"nombre": "", "color": (a[-1]["color"] if a else "blanco")})
    if str(p.get("modo_minimo", "")).lower() in ("si", "sí", "true", "1", "on"):
        # MODO SIMPLE (grupal) — misma lógica que la individual: quiénes son, cuerpo entero,
        # escenario y prenda. Sin medidas de cuerpo ni encuadres cerrados.
        im = img_map or [None, None, None]
        quienes = []
        for k in range(3):
            col_k = str(a[k].get("color", "")).strip()
            if im[k] is not None:
                quienes.append(f"la modelo de la IMAGEN {im[k]} con la prenda en color {col_k}")
            else:
                et = APAR_ETNIA.get(str(a[k].get("etnia", "")).lower(), "")
                quienes.append(("una mujer adulta" + (f" {et}" if et else "")
                                + f" con la prenda en color {col_k}"))
        rango_s = (str(prod_primera) if n_prod <= 1
                   else f"{prod_primera} a {prod_primera + n_prod - 1}")
        fondo_s = str(p.get("fondo", "")).strip() or "un estudio claro con luz natural"
        det = str(p.get("tela", "")).strip()
        compl = ""
        if str(p.get("complemento", "")).lower() in ("si", "sí", "true", "1", "on", "auto"):
            compl = "Las tres llevan también una bombacha haciendo juego. "
        ind = str(p.get("aclaraciones", "")).strip()
        return (
            estilo + "\n\n"
            + "1) Foto de campaña con TRES modelos juntas: " + "; ".join(quienes) + ". "
            + "Son tres mujeres distintas, no gemelas. "
            + "2) Foto de CUERPO ENTERO de las tres, de pies a cabeza, juntas y relajadas, "
              "poses naturales y espontáneas estilo Instagram, no rígidas. "
            + f"3) Están en {fondo_s}. "
            + f"4) Las tres llevan la MISMA prenda de las IMÁGENES {rango_s} "
              "(mismo diseño y calce), cada una en su color. "
            + (f"Detalle de la tela: {det}. " if det else "")
            + compl
            + "5) Recordá: cuerpo entero, mucha luz, la prenda nítida y fiel a la foto real."
            + (f"\n\n{ind}" if ind else "")
        )
    verano = str(p.get("temporada", "")).strip().lower() == "verano"
    fondo_def = ("playa al aire libre, día soleado" if verano else "pared clara y luminosa")
    cuerpo = _bloque_cuerpo(p)
    if img_map is None:
        img_map = [None, None, None]
    tiene_caras = any(x is not None for x in img_map)
    prod_ref = _bloque_producto_ref(n_prod, primera_idx=prod_primera)
    rango = (str(prod_primera) if n_prod <= 1
             else f"{prod_primera} a {prod_primera + n_prod - 1}")
    specs = "\n".join(_modelo_spec(a[k], "ABC"[k], img_map[k]) for k in range(3))
    if tiene_caras and full_refs:
        caras = ", ".join(f"IMAGEN {img_map[k]} = modelo {'ABC'[k]}"
                          for k in range(3) if img_map[k] is not None)
        ident = (
            f"Las siguientes imágenes son TOMAS INDIVIDUALES YA APROBADAS de cada modelo "
            f"({caras}). Cada una muestra EXACTAMENTE cómo es esa modelo: su cara, su cuerpo, "
            "su peinado, su prenda y el color de su prenda. Tu trabajo es REUNIR a ESAS TRES "
            "personas, tal cual se ven en sus tomas (misma cara, mismo cuerpo, misma prenda, "
            "mismo color, mismo peinado), en UNA foto grupal nueva. NO las cambies en nada; "
            "solo cambian la pose y la interacción entre ellas.\n"
            "DEFINICIÓN DE CADA MODELO (coincide con su toma):\n" + specs + "\n\n"
        )
    elif tiene_caras:
        caras = ", ".join(f"IMAGEN {img_map[k]} = modelo {'ABC'[k]}"
                          for k in range(3) if img_map[k] is not None)
        ident = (
            f"Las siguientes imágenes son CARAS/identidad de modelos ({caras}). Respetá sus "
            "rasgos faciales y étnicos; ignorá su ropa, pose y fondo (son solo retratos).\n"
            "DEFINICIÓN EXACTA DE CADA MODELO (respetala tal cual, es clave):\n" + specs + "\n\n"
        )
    else:
        ident = ("DEFINICIÓN EXACTA DE CADA MODELO (respetala tal cual):\n" + specs + "\n\n")
    tiene_dir = "DIRECCIÓN DE LA FOTO GRUPAL" in str(p.get("aclaraciones", ""))
    if tiene_dir:
        comp = ("La COMPOSICIÓN, las poses y las miradas siguen la DIRECCIÓN DE LA FOTO "
                "GRUPAL indicada más abajo (esa manda).")
    else:
        comp = random.choice(_COMPOSICIONES_GRUPAL)
    tarea = (
        "TAREA: generá UNA foto de campaña de catálogo REAL con las TRES modelos (A, B, C) "
        "juntas, en actitud espontánea, relajada y cálida, con gestos naturales. "
        + comp + " Nada rígido ni artificial; que no parezca posado de estudio. "
        f"Cada una con la MISMA prenda de la(s) IMAGEN(es) {rango} (mismo diseño y calce) pero en "
        "SU color. Encuadre aproximado de la cadera para arriba, las tres bien visibles.\n\n"
    )
    return (
        (sysi + "\n\n" if sysi else "")
        + estilo + "\n\n"
        + ident
        + prod_ref + "\n\n"
        + tarea
        + "Lo ÚNICO que cambia entre las tres es el color de la prenda; el modelo de prenda es "
        "idéntico en las tres.\n\n"
        + FIDELITY_FABRIC + "\n\n"
        + (_bloque_complemento(p) + "\n\n"
           if str(p.get("complemento", "")).lower() in ("si", "sí", "true", "1", "on", "auto")
           else "")
        + f"Fondo/escenario: {p.get('fondo') or fondo_def}. Iluminación natural y pareja.\n"
        + cuerpo
        + (("\n\nACLARACIONES DE LA USUARIA (respetalas): "
            + str(p.get("aclaraciones", "")).strip())
           if str(p.get("aclaraciones", "")).strip() else "")
        + "\n\n" + VIDA_BLOCK
        + "\n\n" + CALIDAD_BLOCK
        + "\n\nFOTOGRAFÍA REAL (MÁXIMA PRIORIDAD, vale más que todo lo demás): esta imagen tiene "
        "que ser indistinguible de una foto REAL tomada con cámara profesional (85mm, f/4, luz "
        "natural de ventana lateral suave). NO una imagen generada. Exigencias:\n"
        "• PIEL REAL EN LAS TRES: cada una con textura de piel visible y distinta (poros, pecas, "
        "vello suave, pliegues naturales al moverse, brillo natural en zonas de luz). Nada de "
        "piel lisa/plástica/porcelana en NINGUNA de las tres.\n"
        "• MOMENTO ESPONTÁNEO: capturadas en un instante real entre risas, no posadas en fila. "
        "Asimetría natural: alturas distintas, hombros relajados, alguna en medio gesto.\n"
        "• Imperfecciones de foto real: profundidad de campo (fondo levemente desenfocado), "
        "leve grano fotográfico, luz no perfectamente pareja.\n"
        "Evitá el aspecto de render/IA a toda costa."
        + ("\n\nENCUADRE EDITORIAL SEGURO: foto de catálogo de moda profesional y respetuosa. "
           "Plano de la cadera para arriba (no se ve de la cintura para abajo). Las TRES modelos "
           "llevan una BOMBACHA lisa de talle clásico que combina (nunca sin la parte de abajo). "
           "Poses relajadas y elegantes, actitud natural, estética limpia tipo campaña de ropa "
           "interior de tienda. Estética de catálogo comercial, limpia y prolija."
           if seguro else "")
        + "\n"
        "PROHIBIDO: cambiar el diseño de la prenda; que las tres sean gemelas; logos, marcas de "
        "agua o texto. Exactamente TRES mujeres."
    )


def _bloque_complemento(p: Dict[str, Any], genero: Optional[str] = None) -> str:
    """Cuando la prenda es solo la parte de arriba (corpiño/top), agrega una bombacha lisa
    haciendo juego. Evita que la modelo quede sin la parte de abajo (lo que dispara el filtro)."""
    extra = str(p.get("complemento_desc", "")).strip()
    if _es_hombre(genero):
        base = (
            "COMPLETAR EL LOOK (importante): la(s) foto(s) del producto muestran SOLO la parte "
            "de ARRIBA. Para completar el look de forma prolija, agregá un BÓXER liso y sencillo "
            "en un color que haga juego. El bóxer es un complemento discreto; el modelo NUNCA "
            "queda sin la parte de abajo."
        )
        if extra:
            base += " Preferencia para el bóxer: " + extra + "."
        return base
    base = (
        "COMPLETAR EL LOOK (importante): la(s) foto(s) del producto muestran SOLO la parte de "
        "ARRIBA (corpiño/top). El foco y el protagonismo son de esa prenda de arriba, que se "
        "copia EXACTA de la foto. Para completar el look de forma prolija y presentable, agregá "
        "una BOMBACHA lisa y sencilla, de talle clásico, en un color que HAGA JUEGO con la prenda "
        "de arriba (mismo color o un neutro que combine). La bombacha es un complemento discreto: "
        "no debe competir con la prenda principal ni cambiarle el protagonismo. La modelo NUNCA "
        "queda sin la parte de abajo."
    )
    if extra:
        base += " Preferencia para la bombacha: " + extra + "."
    return base


_POSE_TXT = {0: "de frente", 6: "de perfil, girada en 3/4", 3: "de espalda, mostrando la parte "
            "de atrás de la prenda, con la cabeza girada hacia la cámara",
            2: "sentada de forma relajada", 4: "caminando hacia la cámara"}


def _aplicar_plantilla(txt: str, p: Dict[str, Any], force_pose: Optional[int] = None,
                       n_prod: int = 1, con_avatar: bool = True) -> str:
    """Usa la plantilla de prompt escrita por la usuaria, reemplazando los campos que
    cambian en cada toma. Lo demás se envía EXACTAMENTE como ella lo escribió."""
    pose_txt = _POSE_TXT.get(int(force_pose) if force_pose is not None else 0, "de frente")
    if str(p.get("pose", "")).strip():
        pose_txt = str(p["pose"]).strip()
    reemplazos = {
        "{color}": str(p.get("color_set", "") or p.get("color", "")).strip(),
        "{pose}": pose_txt,
        "{fondo}": str(p.get("fondo", "")).strip(),
        "{tela}": str(p.get("tela", "")).strip(),
        "{aclaraciones}": str(p.get("aclaraciones", "")).strip(),
        "{escote}": str(p.get("escote", "")).strip(),
        "{costuras}": str(p.get("costuras", "")).strip(),
        "{producto}": ("la primera imagen" if n_prod <= 1 else
                       f"las {n_prod} imágenes del producto"),
    }
    out = txt
    for k, v in reemplazos.items():
        out = out.replace(k, v)
    return out.strip()


def build_prompt_on_model(p: Dict[str, Any], settings: Dict[str, Any],
                          paneles: int, aspect: str, style: str = "",
                          n_prod: int = 1, pose_offset: int = 0,
                          force_pose: Optional[int] = None,
                          con_avatar: bool = True,
                          genero: Optional[str] = None) -> str:
    gw = _gwords(genero)
    plantilla = str(p.get("plantilla", "") or "").strip()
    if plantilla:
        # PLANTILLA PROPIA: manda el texto de la usuaria, solo con el color y la pose
        # de cada toma reemplazados. La app no agrega ni saca nada más.
        return _aplicar_plantilla(plantilla, p, force_pose, n_prod, con_avatar)
    if str(p.get("modo_minimo", "")).lower() in ("si", "sí", "true", "1", "on"):
        # MODO SIMPLE — copia la estructura del prompt que a la usuaria le funciona a mano
        # en el Playground: estilo + QUIÉN es la modelo (edad/etnia/pelo, SIN medidas de
        # cuerpo) + cuerpo entero + escenario + descripción corta de la prenda.
        # Deliberadamente NO lleva: medidas de busto/cola, encuadres cerrados de torso,
        # bloques de piel/relieve/fidelidad. Eso es lo que hacía que el pedido dejara de
        # leerse como foto de moda.
        col = str(p.get("color_set", "")).strip()
        estilo_s = _style_text(style, settings)
        es_h = _es_hombre(genero)
        if con_avatar:
            quien = (f"1) {gw['modelo_cap']} es {'el' if es_h else 'la'} de la IMAGEN 1: respetá "
                     "su cara, sus rasgos y su color de pelo. ")
            prod_ref = (f"las IMÁGENES 2{'' if n_prod <= 1 else f' a {1 + n_prod}'}")
        else:
            et = APAR_ETNIA.get(str(p.get("ap_etnia", "")).lower(), "")
            pe = APAR_PELO.get(str(p.get("ap_pelo", "")).lower(), "")
            ed = str(p.get("cuerpo_edad", "")).strip()
            quien = (f"1) {gw['persona'].capitalize()}"
                     + (f" {et}" if et else "")
                     + (f" de unos {ed} años" if ed else "")
                     + (f", {pe}" if pe else "")
                     + ", modelo de pasarela, con expresiones reales, no rígidas. ")
            prod_ref = (f"las IMÁGENES 1{'' if n_prod <= 1 else f' a {n_prod}'}")
        cont = TIPO_CONTEXTURA.get(str(p.get("cuerpo_contextura", "")).lower(), "")
        cuerpo_s = (f"Es de {cont}. " if cont else "")
        fondo_s = str(p.get("fondo", "")).strip() or "un estudio claro con luz natural"
        # La descripción manual del producto pisa la auto-descripción de la ficha.
        prenda_s = (str(p.get("producto_manual", "")).strip()
                    or str(p.get("prenda_desc", "")).strip())
        det = str(p.get("tela", "")).strip()
        if not prenda_s:
            prenda_s = ("la prenda de " + prod_ref + " (copiala exacta: mismo diseño, "
                        "calce y terminaciones)")
        piezas_s = _bloque_piezas(p)
        compl = ""
        if str(p.get("complemento", "")).lower() in ("si", "sí", "true", "1", "on", "auto"):
            compl = ("Lleva también un bóxer haciendo juego. " if es_h
                     else "Lleva también una bombacha haciendo juego. ")
        ind = str(p.get("aclaraciones", "")).strip()
        return (
            estilo_s + "\n\n"
            + quien
            + cuerpo_s
            + "2) Foto de CUERPO ENTERO, de pies a cabeza, pose natural y espontánea estilo "
              "Instagram, relajada, no rígida. "
            + f"3) Está en {fondo_s}. "
            + f"4) Lleva puesta {prenda_s}. "
            + (f"La prenda va en color {col}. " if col else "")
            + (f"Detalle de la tela: {det}. " if det else "")
            + (piezas_s + " " if piezas_s else "")
            + compl
            + "5) Recordá: imagen de cuerpo entero, mucha luz, la prenda nítida y fiel a la "
              "foto real."
            + (f"\n\n{ind}" if ind else "")
        )
    sysi = settings.get("system_instruction", "").strip()
    estilo = _style_text(style, settings)
    verano = str(p.get("temporada", "")).strip().lower() == "verano"
    fondo_def = ("playa al aire libre, médanos de arena de un lado y el mar del otro, día soleado"
                 if verano else "interior simple y claro")
    luz_def = ("luz solar natural de exterior, cálida, con destellos en el agua"
               if verano else "luz natural ambiental")
    # Toma de espalda: por pose fija (3 = espalda, 10 = detalle espalda) o porque la
    # pose ESCRITA por la usuaria menciona la espalda — el guard aplica igual.
    _pose_low = str(p.get("pose", "")).strip().lower()
    _toma_espalda = (paneles <= 1 and (force_pose in (3, 10)
                     or "espalda" in _pose_low or "de atras" in _pose_low
                     or "de atrás" in _pose_low))
    # Si la usuaria definió el CUERPO (contextura/busto/cola/abdomen), ese cuerpo MANDA:
    # por sobre el físico que muestre la foto del avatar y el de las tomas previas.
    _hay_cuerpo = any(str(p.get(k, "")).strip() for k in
                      ("cuerpo_contextura", "cuerpo_busto", "cuerpo_cola", "cuerpo_abdomen"))
    if _hay_cuerpo:
        p = {**p, "_cuerpo_prioritario": True}
    cuerpo = _bloque_cuerpo(p, genero)
    if con_avatar:
        prod_ref = _bloque_producto_ref(n_prod, primera_idx=2)
        rango = "2" if n_prod <= 1 else f"2 a {1 + n_prod}"
        _id_alcance = (
            "SOLO para su IDENTIDAD FACIAL (cara, pelo y tono de piel). La contextura "
            "física de la modelo está definida en el bloque 'TIPO DE CUERPO' de más abajo: "
            "ese bloque describe cómo ES su cuerpo en esta sesión, usalo como única "
            "referencia del cuerpo"
            if _hay_cuerpo else
            "SOLO para su IDENTIDAD (cara y físico)")
        identidad = (
            f"IMAGEN 1 (primera referencia): es {gw['modelo_cap']} (un {gw['genero_n']}) y sirve "
            f"{_id_alcance}. Mantené EXACTOS sus rasgos faciales y "
            "ÉTNICOS: la forma y el corte de los ojos, la estructura de la cara, los pómulos, la "
            "nariz, los labios, el tono de piel real y el tipo y color de pelo. Tiene que ser "
            "RECONOCIBLEMENTE la misma persona de la foto, de la misma etnia. PROHIBIDO "
            "'embellecer', idealizar, europeizar ni promediar la cara hacia una belleza genérica: "
            "respetá la cara real tal cual, con su carácter.\n"
            "MUY IMPORTANTE — LA IMAGEN 1 NO ES UNA FOTO DE LA ESCENA NI DE LA ROPA: es solo un "
            "retrato de referencia de la persona. La ropa que aparece en la IMAGEN 1 NO EXISTE en "
            "esta toma: IGNORALA POR COMPLETO y NO la copies, ni siquiera parcialmente (nada de "
            f"mezclar su buzo/remera/prenda con el producto). {gw['modelo_cap']} lleva ÚNICAMENTE "
            "la prenda de las fotos del producto. Tampoco copies de la IMAGEN 1 la POSE, la "
            "posición de las manos, la inclinación de la cabeza, la expresión, el encuadre, el "
            "fondo ni la iluminación: todo eso lo define la POSE indicada para esta toma. NO "
            "fusiones la IMAGEN 1 con las fotos del producto: son cosas distintas.\n\n"
        )
        tarea = (
            f"TAREA: vestí a {gw['modelo']} de la IMAGEN 1 con la prenda COMPLETA de la(s) "
            f"IMAGEN(es) {rango}, puesta de forma natural, prolija y favorecedora, con el calce "
            f"correcto. Si hay varias vistas (arriba y pantalón), {gw['modelo']} lleva el conjunto "
            f"entero. La ropa sale ÚNICAMENTE de la(s) IMAGEN(es) {rango}: {gw['modelo']} NO "
            "conserva nada de la ropa que tenía puesta en la IMAGEN 1.\n\n"
        )
    else:
        prod_ref = _bloque_producto_ref(n_prod, primera_idx=1)
        rango = "1" if n_prod <= 1 else f"1 a {n_prod}"
        apar = _bloque_apariencia(p, genero)
        identidad = (
            f"MODELO: NO hay foto de modelo de referencia. Creá vos {gw['persona']}, real y "
            "natural, con identidad propia y rasgos creíbles (la generás de cero). "
            + (f"Características OBLIGATORIAS de {gw['modelo']} (respetalas con exactitud): " + apar
               if apar else "")
            + "Pelo, piel y cara realistas y con carácter, con buen detalle facial: textura de "
            "piel real, algún matiz o marca natural, ojos nítidos con detalle de iris; nada de "
            f"cara idealizada de revista ni piel plástica. Mantené {gw['modelo']} "
            "(misma cara, mismo pelo, mismo cuerpo) consistente en todas las tomas.\n\n"
        )
        tarea = (
            f"TAREA: vestí a {gw['modelo']} con la prenda COMPLETA de la(s) IMAGEN(es) {rango}, "
            f"puesta de forma natural, prolija y favorecedora, con el calce correcto. Si hay "
            f"varias vistas (arriba y pantalón), {gw['modelo']} lleva el conjunto entero.\n\n"
        )
    # PEDIDO DE LA USUARIA AL TOPE: lo que ella realmente quiere manda por sobre las reglas fijas.
    _top = []
    pm = _bloque_producto_manual(p)
    pz = _bloque_piezas(p)
    acl = str(p.get("aclaraciones", "")).strip()
    if pm:
        _top.append(pm)
    if pz:
        _top.append(pz)
    if acl:
        _top.append("PEDIDO DE LA USUARIA (máxima prioridad, respetalo al pie de la letra y NO "
                    "hagas lo contrario): " + acl)
    pedido_top = ("\n".join(_top) + "\n\n") if _top else ""
    # POSE: en paneles múltiples, un pool por panel. En 1 panel: si hay pose forzada la usa;
    # si NO y la usuaria no escribió una pose, forzamos una pose variada para no repetir.
    user_pose = str(p.get("pose", "")).strip()
    if paneles > 1:
        pose_block = _bloque_paneles(paneles, aspect, pose_offset, genero)
    elif force_pose is not None:
        pose_block = _bloque_pose_unica(force_pose, genero)
    elif not user_pose:
        pose_block = _bloque_pose_unica(pose_offset, genero)
    else:
        pose_block = ""
    return (
        (sysi + "\n\n" if sysi else "")
        + estilo + "\n\n"
        + pedido_top
        + (BEACHWEAR_BLOCK + "\n\n" if verano else "")
        + identidad
        + prod_ref + "\n\n"
        + tarea
        + f"Detalles de la prenda a respetar:\n{_bloque_detalles(p)}\n\n"
        + (_bloque_complemento(p, genero) + "\n\n"
           if str(p.get("complemento", "")).lower() in ("si", "sí", "true", "1", "on", "auto")
           else "")
        + FIDELITY_FABRIC + "\n\n"
        "Puesta en escena:\n"
        f"- Pose: {user_pose or 'natural, espontánea y relajada'}\n"
        f"- Fondo/escenario: {p.get('fondo') or fondo_def}\n"
        f"- Iluminación: {p.get('luz') or luz_def}\n"
        f"- Encuadre: {p.get('encuadre') or 'cuerpo entero de pies a cabeza'}\n"
        + cuerpo
        + ("\n\n" + FONDO_NITIDO if str(p.get("fondo_foco", "")).lower() == "nitido" else "")
        + ("\n\n" + VIENTO_BLOCK
           if str(p.get("viento", "")).lower() in ("si", "sí", "true", "1", "on") else "")
        + pose_block
        + (ESPALDA_GUARD if _toma_espalda else "")
        + ("\nPROHIBIDO EN LA ESPALDA: copiar el moño, el encaje, el estampado, bolsillo, botones o escote del "
           "FRENTE en la parte de atrás. Si no hay foto de la espalda del producto, la "
           "espalda va simple y coherente con la prenda (misma tela y color), SIN inventar "
           "el diseño del frente atrás." if _toma_espalda else "")
        + (ESPALDA_VERANO_SOFT if (verano and _toma_espalda) else "")
        + "\n\n" + VIDA_BLOCK
        + "\n\n" + CALIDAD_BLOCK
        + (CLOSEUP_BLOCK if (paneles <= 1 and force_pose == 8) else "")
        + (DETALLE_BLOCK if (paneles <= 1 and force_pose in (9, 10)) else "")
        + "\n"
        "PROHIBIDO: cambiar el diseño o el color de la prenda; agregar logos, marcas de agua "
        "o texto; agregar otra persona; poses o encuadres sugerentes; inventar accesorios "
        "(reloj, pulseras, aros, piercings, tatuajes, gorras) o calzado que no se haya "
        "pedido — con pijama/ropa de descanso va descalzo o con medias. "
        f"{gw['una_sola'].capitalize()}."
    )


PRESENTACION_MODOS = {
    "flat_lay": "FLAT-LAY: prenda acostada, prolija, vista cenital sobre superficie limpia.",
    "tirada_piso": ("TIRADA EN EL PISO (vista cenital / desde arriba): la prenda apoyada de forma "
                    "natural y relajada sobre la superficie, fotografiada desde arriba, con caída "
                    "y arrugas reales (NO perfectamente doblada), sombras suaves naturales, look "
                    "espontáneo de estilo de vida."),
    "doblada": "DOBLADA: prenda doblada prolija estilo vitrina de tienda.",
    "percha": "EN PERCHA: prenda colgada de una percha simple sobre fondo limpio.",
    "suspendida": ("COLGADA DE TANZA INVISIBLE: la prenda cuelga en el aire como si "
                   "estuviera sostenida por un hilo de nylon transparente que NO se ve. "
                   "Foto real de producto (NO render 3D), con caída natural de la tela, "
                   "leve sombra abajo. No se ve percha, ni hilo, ni soporte."),
    "maniqui_fantasma": ("MANIQUÍ FANTASMA (ghost mannequin): la prenda toma la forma de "
                          "un cuerpo pero el maniquí es INVISIBLE — no se ve persona ni maniquí."),
}


def build_prompt_product_only(p: Dict[str, Any], settings: Dict[str, Any],
                              modo: str, paneles: int, aspect: str,
                              n_prod: int = 1) -> str:
    sysi = settings.get("system_instruction", "").strip()
    modo_txt = PRESENTACION_MODOS.get(modo, PRESENTACION_MODOS["flat_lay"])
    prod_ref = _bloque_producto_ref(n_prod, primera_idx=1)
    extra_panel = ""
    if paneles > 1:
        extra_panel = (
            f"\nGenerá {paneles} tomas de la prenda lado a lado en una sola imagen {aspect}, "
            f"separadas por una línea blanca vertical limpia y recta (~1.5% del ancho).")
    # ¿La toma es CENITAL (desde arriba / dron)? Por el modo o porque el pedido lo dice.
    _txt_low = " ".join(str(p.get(k, "")) for k in ("aclaraciones", "fondo", "luz")).lower()
    _cenital = (modo in ("flat_lay", "tirada_piso")
                or any(w in _txt_low for w in ("cenital", "dron", "drone", "desde arriba",
                                               "desde el cielo", "vista aérea",
                                               "vista aerea")))
    _cenital_txt = (
        "\n\nTOMA CENITAL REAL (cámara perpendicular al suelo): el encuadre COMPLETO es la "
        "SUPERFICIE vista desde arriba — NO se ve el horizonte, NI el cielo, NI el paisaje "
        "en perspectiva, NI el mar 'de costado'. El escenario pedido se representa por la "
        "superficie misma (la arena, la madera, la sábana) llenando todo el cuadro, y como "
        "mucho elementos apoyados sobre ella (caracolas, hojas, una toalla). Mezclar una "
        "prenda vista desde arriba con un paisaje en perspectiva queda como un RECORTE "
        "PEGADO: prohibido." if _cenital else "")
    return (
        (sysi + "\n\n" if sysi else "")
        + "Fotografía de producto de e-commerce, calidad de estudio, fotorrealista. SIN PERSONAS.\n\n"
        + prod_ref + "\n\n"
        f"Presentación: {modo_txt}\n\n"
        f"Detalles a respetar:\n{_bloque_detalles(p)}\n\n"
        + FIDELITY_FABRIC + "\n\n"
        f"Fondo: {p.get('fondo', 'fondo liso y claro de estudio')}\n"
        f"Iluminación: {p.get('luz', 'luz de estudio pareja y suave')}\n"
        + extra_panel
        + _cenital_txt
        + "\n\nINTEGRACIÓN REAL CON LA ESCENA (nada de 'figurita recortada'): la prenda está "
        "FÍSICAMENTE en la escena — apoya con contacto real sobre la superficie, con "
        "micro-sombras de contacto en los bordes, la superficie se hunde o deforma "
        "levemente bajo la tela, y la prenda recibe la MISMA luz, dirección de sombras, "
        "perspectiva y balance de color que todo el entorno. Si la escena lo amerita, "
        "partículas del entorno sobre la tela (granitos de arena, pelusa) suman realismo. "
        "PROHIBIDO que la prenda parezca recortada y pegada sobre un fondo."
        + "\n\nPROHIBIDO ABSOLUTO: mostrar cualquier persona, niño, niña, bebé, modelo humano, "
        "maniquí visible o parte del cuerpo. SOLO la prenda. Sin texto, logos ni marca de agua."
    )


def build_prompt_recolor(p: Dict[str, Any], settings: Dict[str, Any], modo: str,
                         target_color: str, aspect: str, n_prod: int = 1) -> str:
    """Reproduce la MISMA prenda cambiando ÚNICAMENTE el color."""
    sysi = settings.get("system_instruction", "").strip()
    modo_txt = PRESENTACION_MODOS.get(modo, PRESENTACION_MODOS["suspendida"])
    prod_ref = _bloque_producto_ref(n_prod, primera_idx=1)
    return (
        (sysi + "\n\n" if sysi else "")
        + "Fotografía de producto de e-commerce, calidad de estudio, fotorrealista. SIN PERSONAS.\n\n"
        + prod_ref + "\n\n"
        "TAREA — CAMBIO DE COLOR: reproducí EXACTAMENTE la misma prenda de la referencia: "
        "mismo molde, mismo corte, misma trama y relieve del tejido, misma estampa/diseño, "
        "mismas costuras, mismos puños y cuello, mismos detalles. Lo ÚNICO que cambia es el "
        f"COLOR, que pasa a ser: {target_color}. La textura, el relieve y el dibujo del tejido "
        "se mantienen idénticos, solo teñidos en el color nuevo de forma realista (respetando "
        "luces y sombras de la tela).\n\n"
        f"Presentación: {modo_txt}\n"
        f"Fondo: {p.get('fondo', 'fondo liso y claro de estudio')}\n"
        f"Iluminación: {p.get('luz', 'luz de estudio pareja y suave')}\n\n"
        "PROHIBIDO: cambiar el molde, el corte, la trama, la estampa, los puños, el cuello o "
        "cualquier detalle que no sea el color; mostrar personas o maniquí visible; agregar "
        "texto, logos o marca de agua. SOLO la prenda, solo cambia el color."
    )


def build_prompt_avatar(gender: str, p: Dict[str, Any]) -> str:
    attrs = []
    for k, label in [("edad", "Rango etario (adulto)"), ("piel", "Tono de piel"),
                     ("pelo", "Pelo"), ("contextura", "Contextura"),
                     ("altura", "Altura aprox"), ("rasgos", "Rasgos/vibe")]:
        if p.get(k):
            attrs.append(f"- {label}: {p[k]}")
    attrs_txt = "\n".join(attrs) if attrs else "- Estilo argentino, catálogo, natural."
    genero_txt = "mujer adulta" if gender == "mujer" else "varón adulto"
    return (
        f"Retrato de catálogo de una {genero_txt}, para usar como REFERENCIA de identidad "
        "reutilizable. Foto fotorrealista de estudio.\n\n"
        f"Características:\n{attrs_txt}\n\n"
        "Encuadre: medio cuerpo / 3-4, mirada a cámara, expresión neutra y profesional.\n"
        "Vestuario en esta toma: ropa básica neutra (remera/top liso simple) — NO ropa íntima "
        "en la foto de referencia.\n"
        "Fondo: neutro claro liso. Luz: pareja de catálogo. Una sola persona adulta.\n"
        "Sin texto, sin logos, sin marca de agua. Composición limpia para usar como referencia "
        "de cara y cuerpo en futuras generaciones."
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLAMADA A NANO BANANA PRO
# ─────────────────────────────────────────────────────────────────────────────

# Según la doc de Google, si NO se fija umbral el bloqueo viene apagado por defecto en
# los modelos 2.5 y 3. Mandar BLOCK_ONLY_HIGH era MÁS estricto que no mandar nada, así
# que "relaxed" ahora usa el umbral más permisivo documentado (OFF/BLOCK_NONE).
# Los daños centrales (seguridad infantil, etc.) los bloquea el modelo siempre, sin
# importar este ajuste.
_SAFETY_RELAXED = [
    {"category": c, "threshold": "OFF"} for c in [
        "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT",
    ]
]
_SAFETY_RELAXED_ALT = [
    {"category": c["category"], "threshold": "BLOCK_NONE"} for c in _SAFETY_RELAXED
]


def _img_part(b64: str, mime: str = "image/jpeg") -> Dict[str, Any]:
    return {"inlineData": {"mimeType": mime, "data": b64}}


async def _current_api_key() -> str:
    """Devuelve la API key del usuario logueado si cargó una propia; si no, la global."""
    sub = CURRENT_SUB.get()
    if sub:
        rec = await get_user(sub)
        k = (rec.get("gemini_key") or "").strip()
        if k:
            return k
    return GEMINI_API_KEY


async def gemini_generate(parts: List[Dict[str, Any]], settings: Dict[str, Any],
                          aspect: str, image_size: str,
                          save_prompt: bool = True) -> bytes:
    modelo_sel = str(settings.get("model") or MODEL_ID).strip() or MODEL_ID
    api_key = await _current_api_key()
    if not api_key:
        raise HTTPException(500, "Falta la API key de Google (ni propia ni global).")

    gen_cfg: Dict[str, Any] = {
        "responseModalities": ["TEXT", "IMAGE"],
        "imageConfig": {"aspectRatio": aspect, "imageSize": image_size},
        "temperature": settings.get("temperature", 0.7),
        "topP": settings.get("top_p", 0.95),
    }
    if settings.get("seed") not in (None, "", 0):
        try:
            gen_cfg["seed"] = int(settings["seed"])
        except (TypeError, ValueError):
            pass

    body: Dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen_cfg,
    }
    if settings.get("safety") == "relaxed":
        body["safetySettings"] = _SAFETY_RELAXED

    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    # El MODELO sale de los ajustes: antes el endpoint estaba fijo e ignoraba la elección,
    # así que siempre se llamaba al Pro (filtro mucho más estricto).
    modelo = modelo_sel
    endpoint = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{modelo}:generateContent")
    # Guardamos SIEMPRE el prompt enviado (salga o no), para poder inspeccionarlo
    # desde el diagnóstico. (El acabado 4K pasa save_prompt=False para no pisar el
    # prompt real de la toma con el textito de "recreá en 4K".)
    try:
      if save_prompt:
        _ptxt = next((pt.get("text", "") for pt in parts if pt.get("text")), "")
        _guardado = (_ptxt if len(_ptxt) <= 7000
                     else _ptxt[:3500] + "\n\n[…RECORTE DEL MEDIO…]\n\n" + _ptxt[-3500:])
        await kv.set(_pfx() + "lastprompt", {"prompt": _guardado,
                                             "largo": len(_ptxt),
                                             "modelo": modelo,
                                             "filtro": settings.get("safety", ""),
                                             "ts": int(time.time())}, ttl=7200)
    except Exception:
        pass
    async with httpx.AsyncClient(timeout=240) as cli:
        r = await cli.post(endpoint, json=body, headers=headers)
        # Algunos modelos aceptan BLOCK_NONE pero no OFF (o al revés): si el umbral no
        # le gusta, se reintenta con el otro nombre en vez de fallar.
        if r.status_code == 400 and "safetySettings" in body:
            low = r.text.lower()
            if "threshold" in low or "safetysettings" in low:
                body_alt = dict(body)
                body_alt["safetySettings"] = _SAFETY_RELAXED_ALT
                r = await cli.post(endpoint, json=body_alt, headers=headers)
                if r.status_code == 400:
                    body_sin = {k: v for k, v in body.items() if k != "safetySettings"}
                    r = await cli.post(endpoint, json=body_sin, headers=headers)

    if r.status_code != 200:
        detail = r.text[:500]
        raise HTTPException(r.status_code, f"Nano Banana Pro devolvió error: {detail}")

    data = r.json()
    candidates = data.get("candidates") or []
    pf = data.get("promptFeedback", {}) or {}
    if not candidates:
        br = pf.get("blockReason", "")
        raise HTTPException(422, f"La API no devolvió imagen. Motivo del prompt: {br or pf}")

    cand = candidates[0]
    content = cand.get("content", {}) or {}
    for part in content.get("parts", []):
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            return base64.b64decode(inline["data"])

    # No vino imagen: juntamos el motivo real para no quedar a ciegas
    fr = cand.get("finishReason") or ""
    txt = "".join(p.get("text", "") for p in content.get("parts", []) if p.get("text"))
    blocked = [
        (sr.get("category", "") or "").replace("HARM_CATEGORY_", "")
        for sr in (cand.get("safetyRatings") or []) if sr.get("blocked")
    ]
    br = pf.get("blockReason", "")
    motivo = fr or br or "desconocido"
    safety_like = {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "IMAGE_PROHIBITED_CONTENT",
                   "IMAGE_RECITATION", "RECITATION", "BLOCKLIST", "IMAGE_OTHER"}
    if motivo in safety_like or blocked or br:
        extra = (" Categorías: " + ", ".join(blocked)) if blocked else ""
        # Guardamos el PROMPT exacto que fue bloqueado, para poder verlo en el
        # diagnóstico y cazar la palabra/frase que dispara el filtro.
        try:
            ptxt = ""
            for prt in parts:
                if prt.get("text"):
                    ptxt = prt["text"]
                    break
            guardado = (ptxt if len(ptxt) <= 7000
                        else ptxt[:3500] + "\n\n[…RECORTE DEL MEDIO…]\n\n" + ptxt[-3500:])
            await kv.set(_pfx() + "lastblock",
                         {"motivo": motivo, "prompt": guardado, "largo": len(ptxt),
                          "ts": int(time.time())}, ttl=7200)
        except Exception:
            pass
        raise HTTPException(
            422, f"El modelo bloqueó la imagen por su filtro (motivo: {motivo}).{extra} "
                 f"Probá modo Producto, o reformulá. {txt[:160]}")
    raise HTTPException(422, f"No vino imagen (motivo: {motivo}). {txt[:200]}")


# ─────────────────────────────────────────────────────────────────────────────
# MOTOR FLUX (fal.ai): try-on real (persona + prenda) sin los filtros de Google.
# Las imágenes van como data-URIs en `image_urls` (la persona SIEMPRE primera).
# ─────────────────────────────────────────────────────────────────────────────

def _flux_dims(aspect: str, target_px: int = 3_400_000) -> Tuple[int, int]:
    """Ancho/alto para FLUX (~2MP, múltiplos de 16) según el aspect pedido."""
    ratio = RATIO_NUM.get(aspect, 0.8)
    h = int((target_px / ratio) ** 0.5)
    h = min(h, 2048)                      # tope de Seedream: 2048px
    w = min(int(h * ratio), 2048)
    return max(512, (w // 16) * 16), max(512, (h // 16) * 16)


async def fal_generate(parts: List[Dict[str, Any]], settings: Dict[str, Any],
                       aspect: str, image_size: str, model_slug: str) -> bytes:
    api_key = str(settings.get("fal_api_key") or FAL_API_KEY).strip()
    if not api_key:
        raise HTTPException(500, "Falta la API key de fal.ai: cargá FAL_KEY en Railway "
                                 "(Settings → Variables) o pegala en Ajustes → Motor FLUX.")
    prompt = next((pt.get("text", "") for pt in parts if pt.get("text")), "")
    raws = []
    for pt in parts:
        inline = pt.get("inlineData") or pt.get("inline_data")
        if inline and inline.get("data"):
            raws.append(inline["data"])
    # Seedream acepta hasta 10 referencias (sin el límite de 9MP de FLUX): viajan la
    # persona en alta + TODAS las vistas del producto (frente, espalda, detalle) + ancla.
    raws = raws[:8]
    image_urls = []
    for i, b in enumerate(raws):
        md = 1600 if i == 0 else 1280
        try:
            chico = _compress_ref(base64.b64decode(b), max_dim=md, q=90)
        except Exception:
            chico = b
        image_urls.append(f"data:image/jpeg;base64,{chico}")
    w, h = _flux_dims(aspect)
    body: Dict[str, Any] = {
        "prompt": prompt,
        "image_urls": image_urls,
        "num_images": 1,
        "output_format": "jpeg",
        "sync_mode": True,
        "image_size": {"width": w, "height": h},
        "enable_safety_checker": False,
        "safety_tolerance": "6",
        # guidance más alto = FLUX obedece MÁS al prompt (pose, ambiente, pedidos).
        "guidance_scale": float(settings.get("flux_guidance", 5.0) or 5.0),
    }
    _preset = {"4:5": "portrait_4_3", "3:4": "portrait_4_3", "2:3": "portrait_4_3",
               "9:16": "portrait_16_9", "1:1": "square_hd", "4:3": "landscape_4_3",
               "5:4": "landscape_4_3", "3:2": "landscape_4_3", "16:9": "landscape_16_9",
               "21:9": "landscape_16_9"}.get(aspect, "portrait_4_3")
    _size_fallbacks = [{"image_size": _preset}, {"aspect_ratio": aspect}]
    headers = {"Authorization": f"Key {api_key}", "Content-Type": "application/json"}
    endpoint = f"https://fal.run/{model_slug.strip().strip('/')}"
    try:
        await kv.set(_pfx() + "lastprompt", {"prompt": prompt[:7000], "largo": len(prompt),
                                             "modelo": f"FLUX ({model_slug})",
                                             "filtro": "fal", "ts": int(time.time())}, ttl=7200)
    except Exception:
        pass
    # Si el modelo elegido no acepta algún campo opcional, se reintenta sin él en vez
    # de fallar (cada modelo de fal tiene un esquema levemente distinto).
    opcionales = ["aspect_ratio", "sync_mode", "num_images", "output_format",
                  "enable_safety_checker", "safety_tolerance", "guidance_scale"]
    async with httpx.AsyncClient(timeout=300) as cli:
        r = None
        for intento in range(len(opcionales) + 1):
            r = await cli.post(endpoint, json=body, headers=headers)
            if r.status_code != 422 or not opcionales:
                break
            low = r.text.lower()
            quitado = False
            if "too large" in low or "megapixel" in low:
                if body.get("image_size") != {"width": 1024, "height": 1280}:
                    body["image_size"] = {"width": 1024, "height": 1280}
                    quitado = True
                elif len(body.get("image_urls") or []) > 2:
                    body["image_urls"] = body["image_urls"][:-1]
                    quitado = True
                if quitado:
                    continue
            if "image_size" in low and _size_fallbacks:
                body.pop("image_size", None)
                body.pop("aspect_ratio", None)
                body.update(_size_fallbacks.pop(0))
                quitado = True
            else:
                for k in list(opcionales):
                    if k in low and k in body:
                        body.pop(k)
                        opcionales.remove(k)
                        quitado = True
                        break
            if not quitado:
                break
        if r is None:
            raise HTTPException(502, "FLUX: no se pudo llamar a fal.ai.")
        if r.status_code in (401, 403):
            raise HTTPException(r.status_code, "FLUX: la API key de fal.ai no es válida o no "
                                               "tiene crédito. Revisá FAL_KEY.")
        if r.status_code == 404:
            raise HTTPException(404, f"FLUX: el modelo '{model_slug}' no existe en fal.ai. "
                                     "Revisá el nombre en Ajustes → Motor FLUX.")
        if r.status_code != 200:
            low = r.text.lower()
            if "too large" in low or "megapixel" in low:
                raise HTTPException(422, "FLUX: las imágenes superaron el límite de tamaño "
                                         f"de fal (9MP entrada+salida). Detalle: {r.text[:200]}")
            if any(t in low for t in ("nsfw", "safety", "content policy", "flagged",
                                      "content_policy")):
                fb = str(settings.get("flux_fallback_model") or "").strip()
                if fb and fb != model_slug:
                    # El PRO (hosteado por BFL) modera el prompt y no se puede apagar:
                    # reintentamos solos en el modelo de respaldo permisivo (dev).
                    s2 = dict(settings)
                    s2["flux_fallback_model"] = ""
                    return await fal_generate(parts, s2, aspect, image_size, fb)
                raise HTTPException(422, "FLUX bloqueó la imagen por su filtro de contenido "
                                         "(incluso el modelo de respaldo). Probá otro modelo "
                                         f"en Ajustes → Motor FLUX. Detalle: {r.text[:200]}")
            raise HTTPException(r.status_code, f"FLUX devolvió error: {r.text[:400]}")
        data = r.json()
        imgs = data.get("images") or ([data["image"]] if data.get("image") else [])
        if not imgs or not imgs[0].get("url"):
            raise HTTPException(422, f"FLUX no devolvió imagen: {str(data)[:300]}")
        url = imgs[0]["url"]
        if url.startswith("data:"):
            return base64.b64decode(url.split(",", 1)[1])
        rr = await cli.get(url)
        if rr.status_code != 200:
            raise HTTPException(502, f"FLUX: no pude descargar la imagen ({rr.status_code}).")
        return rr.content


# Encuadre de lencería para FLUX: la enmarca como catálogo comercial de una marca de
# ropa interior. Sube la calidad Y evita bloqueos falsos (queda claro que es una foto de
# producto de tienda, de buen gusto, no algo explícito).
_LENCERIA_FLUX = (
    "Tasteful commercial lingerie e-commerce catalog photo (like a womens underwear online "
    "shop): elegant and classy, NOT explicit, NOT sexual, NOT nude; the adult model wears the "
    "full matching set (top and bottom), well fitted and modestly covered."
)


# ── SKILLS por categoría: dirección de arte basada en cómo shootean las marcas reales ──
# (lencería tipo Intimissimi/Aerie · baño tipo campaña resort · pijama tipo loungewear cozy)
def _categoria(p: Dict[str, Any]) -> str:
    verano = str(p.get("temporada", "")).strip().lower() == "verano"
    txt = " ".join(str(p.get(k, "")) for k in
                   ("producto_manual", "prenda_desc", "tela", "aclaraciones", "piezas")).lower()
    if verano or any(k in txt for k in ("bikini", "malla", "traje de baño", "swimwear",
                                        "beachwear", "enteriza", "trikini")):
        return "bano"
    if _es_ropa_interior(p):
        return "lenceria"
    if any(k in txt for k in ("pijama", "piyama", "camisón", "camison", "loungewear",
                              "descanso", "nightwear", "bata", "robe", "homewear")):
        return "pijama"
    return ""


def _bloque_categoria(cat: str, genero: Optional[str] = None,
                      sin_pose: bool = False) -> str:
    """sin_pose=True: hay una POSE OBLIGATORIA pedida → el bloque de estilo NO sugiere
    ninguna pose (antes su 'three-quarter turn showing front' peleaba contra la pose
    de espalda pedida y Seedream terminaba haciendo el frente)."""
    h = _es_hombre(genero)
    modelo = "the male model" if h else "the model"
    if cat == "lenceria":
        pose = ("confident relaxed stance, weight on one leg, gentle three-quarter turn showing "
                "front and side of the set, one hand at the hip or in the hair" if not h else
                "confident relaxed stance, weight on one leg, subtle three-quarter turn, calm "
                "assured expression")
        _p = "" if sin_pose else f" {modelo} in a {pose},"
        return (f"Style: modern lingerie catalog (Intimissimi / Aerie): soft even daylight that "
                f"reveals the lace texture, clean neutral or soft bedroom backdrop,{_p} "
                f"body-positive elegant mood.")
    if cat == "bano":
        _p = "" if sin_pose else (f" {modelo} in a relaxed confident pose with natural "
                                  f"movement,")
        return (f"Style: swimwear resort campaign, outdoors on a sunny beach with sea softly "
                f"blurred behind (or clean poolside), warm golden sunlight, gentle breeze;"
                f"{_p} aspirational vacation mood, sun-kissed skin.")
    if cat == "pijama":
        return (f"Style: cozy loungewear/sleepwear lifestyle, warm bright bedroom or living room "
                f"with soft window light, a bed or sofa as gentle context; {modelo} in relaxed "
                f"candid at-home poses, warm inviting mood.")
    return ""


def build_prompt_flux(p: Dict[str, Any], pose_txt: str, con_persona: bool,
                      n_prod: int, genero: Optional[str] = None,
                      estilo: str = "") -> str:
    """Prompt LEAN para FLUX.2/edit: corto, en inglés y sin contradicciones. Los prompts
    largos y apilados (estilo Gemini) confunden a FLUX y bajan la fidelidad."""
    h = _es_hombre(genero)
    subj = "the man" if h else "the woman"
    col = str(p.get("color_set", "")).strip()
    fondo_user = str(p.get("fondo", "")).strip()
    cat = _categoria(p)
    L = []
    # 1) Identidad + prenda (lo esencial de un editor multi-referencia)
    if con_persona:
        L.append(f"A realistic fashion catalog photo of {subj} from the FIRST reference image. "
                 "KEEP HER EXACT FACE — it must be recognizably the SAME specific person: same "
                 "facial features, same face shape, same eyes, nose and mouth, same hair color "
                 "and skin tone. Do NOT make a different lookalike, do NOT beautify or average "
                 "the face.")
    else:
        ap = _bloque_apariencia(p, genero)
        L.append(f"A realistic fashion catalog photo of {ap or (subj + '.')}")
    L.append("She wears EXACTLY the garment from the product reference photo(s): same design, "
             "cut, color, lace/fabric texture, straps and trims. Copy that exact garment; do NOT "
             "invent details it does not have (no extra seams, no logos, no lace or trims that "
             "are not in the photo). If the product reference is a CATALOG sheet showing several "
             "garments, copy ONLY the single one that matches the color/description and IGNORE "
             "the other garments, the text, prices, magazine and props around it.")
    if col:
        L.append(f"Garment color: {col}.")
    pm = str(p.get("producto_manual", "")).strip()
    if pm:
        L.append(f"Product description: {pm}.")
    pz = str(p.get("piezas", "")).strip()
    if pz:
        L.append(f"Pieces worn: {pz}.")
    # 2) POSE y ESCENARIO de la usuaria PRIMERO y con prioridad sobre el estilo
    if pose_txt:
        L.append(f"MANDATORY POSE (top priority, overrides any pose mentioned later AND any "
                 f"pose shown in the reference images): {pose_txt}.")
        low_p = pose_txt.lower()
        if "espalda" in low_p or "behind" in low_p or "back detail" in low_p:
            L.append("She is seen from BEHIND: render the BACK of the garment exactly as shown "
                     "in the back-view product photo (straps, elastics, thong back). NEVER copy "
                     "the FRONT design (bow, lace panel, chest print, pocket, buttons, neckline) onto "
                     "the back — "
                     "if no back-view photo is provided, render a simple coherent back in the "
                     "same fabric and color.")
        elif "perfil" in low_p or "profile" in low_p:
            L.append("She is seen in PROFILE (side view), body turned sideways to the camera.")
    if fondo_user:
        L.append(f"MANDATORY SETTING (top priority, overrides any backdrop mentioned later): "
                 f"{fondo_user}. Put her in that exact environment.")
    enc = str(p.get("encuadre", "")).strip()
    if enc:
        L.append(f"Framing: {enc}.")
    luz = str(p.get("luz", "")).strip()
    if luz:
        L.append(f"Lighting: {luz}.")
    _hay_cuerpo_fx = any(str(p.get(k, "")).strip() for k in
                         ("cuerpo_contextura", "cuerpo_busto", "cuerpo_cola",
                          "cuerpo_abdomen"))
    if _hay_cuerpo_fx:
        p = {**p, "_cuerpo_prioritario": True}
    cuerpo = _bloque_cuerpo(p, genero).strip()
    if cuerpo:
        L.append(cuerpo)
        if _hay_cuerpo_fx:
            L.append("BODY OVERRIDE (top priority): the model's body in THIS photo follows "
                     "the body description above, NOT the body shown in the reference "
                     "photos — same face as the reference, THAT body (e.g. if it says "
                     "plus-size XXL, the body must be genuinely fat/plus-size, never "
                     "slimmed down).")
    if cat == "lenceria":
        L.append(_LENCERIA_FLUX)
    cb = _bloque_categoria(cat, genero, sin_pose=bool(pose_txt.strip()))
    if cb:
        L.append(cb)
    # 4) Realismo + tono de piel parejo (mata el tinte naranja) — CORTO
    L.append("Photorealistic like a real camera photo, natural soft daylight, natural relaxed "
             "pose, real natural skin with subtle texture and an EVEN CONSISTENT skin tone over "
             "the whole body (no orange, red or sunburnt color cast on the legs, arms or torso), "
             "correct human proportions and FLAWLESS ANATOMY: hands with exactly five natural fingers, "
             "correct wrists, elbows, knees and thighs, no twisted or merged limbs. Avoid: 3D "
             "render, plastic skin, stiff mannequin pose, extra invented accessories.")
    # 5) Aclaraciones de la usuaria (si las hay) — al final, cortas
    acl = str(p.get("aclaraciones", "")).strip()
    if acl:
        L.append(f"Also respect: {acl}")
    return " ".join(L)


# ─────────────────────────────────────────────────────────────────────────────
# ANÁLISIS DE PRENDA: ficha técnica automática (visión → JSON)
# ─────────────────────────────────────────────────────────────────────────────

FICHA_PROMPT = (
    "Sos un especialista en control de calidad de indumentaria. Te paso una o varias fotos "
    "REALES de una misma prenda de LUMA Íntima (lencería, pijama o ropa térmica). Analizala "
    "con MÁXIMO detalle, como para que otra persona pueda reproducirla sin verla.\n\n"
    "Devolvé EXCLUSIVAMENTE un objeto JSON válido (sin texto antes ni después, sin ```), "
    "en español rioplatense, con EXACTAMENTE estas claves:\n"
    "{\n"
    '  "tipo_prenda": "qué es (ej: conjunto de pijama polar manga larga + pantalón)",\n'
    '  "tela": "tipo y textura real de la tela (ej: polar coral/flannel afelpado, jersey, etc.)",\n'
    '  "color_base": "color de fondo dominante, lo más preciso posible",\n'
    '  "colores_secundarios": ["lista de los otros colores presentes"],\n'
    '  "estampa": {\n'
    '    "descripcion": "qué muestra la estampa (motivos concretos)",\n'
    '    "escala": "tamaño aprox de los motivos respecto a la prenda (chico/mediano/grande)",\n'
    '    "densidad": "qué tan junta está (densa/media/espaciada)",\n'
    '    "distribucion": "cómo se reparte (all-over al azar, en filas, etc.)",\n'
    '    "texto_presente": "si hay palabras en la estampa, transcribilas; si son ilegibles, decí \'texto pequeño decorativo, poco legible\'"\n'
    "  },\n"
    '  "cuello": "forma y terminación del cuello/escote",\n'
    '  "punos": "cómo son los puños y botamangas (elástico, recto, etc.)",\n'
    '  "costuras_detalles": "costuras, vivos, cintura, bolsillos u otros detalles visibles",\n'
    '  "calce": "tipo de calce (holgado, oversize, ajustado)",\n'
    '  "espalda": "cómo es la PARTE DE ATRÁS de la prenda. Si hay fotos rotuladas como ESPALDA, describila EXACTA de esas fotos (estampa o lisa, cierres, tiras, elásticos, terminaciones). Si NO hay foto de la espalda, deducí la espalda MÁS PROBABLE y CONSERVADORA para este tipo de prenda (normalmente: misma tela y color base, LISA y SIN el estampado/estampado principal del frente, sin bolsillos ni botones) y aclarán al inicio: \'(deducida, sin foto)\'. Al final agregá SIEMPRE una traducción corta al inglés entre paréntesis, ej: (back: plain sheer tulle, thin straps, no lace, no bow)",\n'
    '  "ficha_para_render": "UN párrafo denso y preciso que describa la prenda entera para guiar una generación de imagen fiel, mencionando tela, color base, estampa con su escala/densidad, terminaciones Y cómo es la espalda",\n'
    '  "negativos_sugeridos": ["errores típicos a evitar dado lo que ves, ej: no inventar tejido cable, no agregar puños de otro color, mantener la estampa a la misma escala"]\n'
    "}\n"
    "Sé fiel a lo que VES en las fotos. No inventes detalles que no aparezcan."
)


async def gemini_analyze(prod_b64s: List[str],
                         back_b64s: Optional[List[str]] = None) -> Dict[str, Any]:
    api_key = await _current_api_key()
    if not api_key:
        raise HTTPException(500, "Falta la API key de Google (ni propia ni global).")
    parts: List[Dict[str, Any]] = [{"text": FICHA_PROMPT}]
    for _j, _b in enumerate(prod_b64s):
        parts.append({"text": f"FOTO {_j + 1} — FRENTE/VISTA del producto:"})
        parts.append(_img_part(_b))
    for _j, _b in enumerate(back_b64s or []):
        parts.append({"text": f"FOTO — ESPALDA del producto (vista trasera "
                              f"{_j + 1}): usala para el campo 'espalda'."})
        parts.append(_img_part(_b))
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    # "pensamiento" en bajo = mucho más rápido para esta tarea simple.
    cfg_fast = {"temperature": 0.2, "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingLevel": "low"}}
    cfg_plain = {"temperature": 0.2, "responseMimeType": "application/json"}

    async def _call(cfg):
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": cfg}
        async with httpx.AsyncClient(timeout=120) as cli:
            return await cli.post(ANALYZE_ENDPOINT, json=body, headers=headers)

    r = await _call(cfg_fast)
    if r.status_code == 400:          # por si la versión no acepta thinkingConfig
        r = await _call(cfg_plain)
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"El analizador devolvió error: {r.text[:400]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise HTTPException(422, f"El analizador no devolvió nada. {data.get('promptFeedback', {})}")
    txt = ""
    for part in cands[0].get("content", {}).get("parts", []):
        if part.get("text"):
            txt += part["text"]
    txt = txt.strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.lower().startswith("json"):
            txt = txt[4:]
    try:
        return json.loads(txt)
    except Exception:
        # último intento: recortar al primer { ... último }
        a, b = txt.find("{"), txt.rfind("}")
        if a >= 0 and b > a:
            return json.loads(txt[a:b + 1])
        raise HTTPException(422, f"No pude leer la ficha como JSON. {txt[:200]}")


# ─────────────────────────────────────────────────────────────────────────────
# CONTROL DE FIDELIDAD: inspector que compara la imagen generada vs la prenda real
# ─────────────────────────────────────────────────────────────────────────────

QC_PROMPT = (
    "Sos inspector de control de calidad de una marca de indumentaria. Las imágenes llegan "
    "ROTULADAS con un texto antes de cada una: una FOTO GENERADA con IA para el catálogo, "
    "FOTOS REALES del producto (vistas del frente) y, si hay, FOTOS REALES de la ESPALDA "
    "del producto. Tu trabajo: verificar si la prenda de la foto generada es FIEL al "
    "producto real.\n"
    "PRIMERO identificá QUÉ TIPO DE TOMA es la foto generada, porque el catálogo usa "
    "muchas: cuerpo entero de frente, DE PERFIL, DE ESPALDA, PRIMER PLANO de cara, PLANO "
    "DETALLE (un recorte cerrado de la prenda, muchas veces SIN cara — eso es a propósito, "
    "no es un error), o producto solo colgado. Evaluá ÚNICAMENTE lo que ESE encuadre "
    "muestra: en un plano detalle solo se juzga la zona visible de la prenda; en un perfil "
    "solo el costado; NUNCA cuentes como diferencia un elemento que queda fuera de cuadro, "
    "tapado por la pose o del otro lado del cuerpo (ej: 'no se ve el moño' en una toma de "
    "espalda NO es una diferencia).\n"
    "Si la toma es de ESPALDA (o un detalle de la espalda), compará contra las fotos "
    "rotuladas ESPALDA — esa es la única verdad de la parte trasera — y NO la castigues "
    "por no coincidir con el diseño del frente (la espalda de una prenda normalmente ES "
    "distinta del frente). Si muestra la espalda y NO hay foto de espalda, evaluá solo que "
    "la tela, el color y las terminaciones sean coherentes con el producto, sin inventar "
    "diferencias del diseño trasero.\n"
    "OJO CON LAS FOTOS REALES: son fotos de depósito sacadas con celular — pueden venir "
    "ROTADAS o de costado, con la prenda arrugada, puesta en un maniquí o mostrando solo "
    "una parte. Interpretalas con criterio (rotalas mentalmente si hace falta) y usá TODAS "
    "las vistas JUNTAS: si un elemento (un moño, un encaje, una tira, una tanga) aparece "
    "en CUALQUIERA de las fotos reales, ese elemento EXISTE en el producto y NO es un "
    "invento de la foto generada. Reportá SOLO diferencias de las que estés SEGURO después "
    "de mirar todas las vistas; si una diferencia puede deberse al ángulo, a una arruga o "
    "a que la foto real es parcial o está rotada, NO la cuentes como diferencia ni bajes "
    "el puntaje por eso.\n"
    "Compará SOLO la prenda (ignorá cara, pose, fondo e iluminación):\n"
    "1) diseño/molde y largo; 2) color; 3) estampa: motivos, escala y distribución; "
    "4) terminaciones: puños, cuello/escote, cierres, breteles; 5) detalles INVENTADOS que la "
    "prenda real no tiene (bolsillos, botones, capucha, etc.); 6) piezas de más o de menos; "
    "7) si la estampa tiene LETRAS o monogramas: que sean las mismas letras, legibles, con la "
    "misma tipografía (no garabatos genéricos); 8) accesorios o calzado que nadie pidió "
    "(reloj, aros, tatuajes, zapatillas con pijama) — EXCEPCIÓN: un objeto simple de escena "
    "sostenido EN LA MANO (una fruta, un vaso, una flor, una taza) es utilería editorial "
    "válida y NO baja puntaje; 9) anatomía y naturalidad: brazos/manos/"
    "proporciones raras, venas o músculos exagerados, piel con hiperdetalle artificial o "
    "sombras duras irreales (estos defectos también bajan puntaje y van en diferencias).\n"
    "Devolvé SOLO un JSON válido, sin texto extra:\n"
    '{"puntaje": <1-10, 10 = prenda idéntica>, '
    '"diferencias": ["diferencia concreta y accionable", ...]}\n'
    "Si la prenda es fiel, diferencias = []. Sé estricto pero justo: diferencias de pose, "
    "iluminación o arrugas naturales NO bajan puntaje."
)


async def verificar_prenda(gen_bytes: bytes, prod_b64s: List[str],
                           back_b64s: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Compara la imagen generada contra las fotos reales (frente Y espalda, rotuladas).
    Devuelve {puntaje, diferencias} o None si no se pudo verificar (nunca rompe)."""
    try:
        api_key = await _current_api_key()
        if not api_key or not prod_b64s:
            return None
        # El inspector usa su propio modelo (flash completo): el lite se confundía con
        # fotos reales rotadas/arrugadas y con los encuadres de detalle/espalda.
        _qm = str((await get_settings()).get("qc_model") or "").strip() or ANALYZE_MODEL
        _qc_endpoint = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                        f"{_qm}:generateContent")
        gen_b64 = _compress_ref(gen_bytes, max_dim=1024, q=85)
        parts = [{"text": QC_PROMPT},
                 {"text": "IMAGEN GENERADA (la que hay que inspeccionar):"},
                 _img_part(gen_b64)]
        for _j, _b in enumerate(prod_b64s[:3]):
            parts.append({"text": f"FOTO REAL DEL PRODUCTO (vista {_j + 1}):"})
            parts.append(_img_part(_b))
        for _j, _b in enumerate((back_b64s or [])[:2]):
            parts.append({"text": f"FOTO REAL — ESPALDA del producto (vista trasera "
                                  f"{_j + 1}; única verdad de la parte de atrás):"})
            parts.append(_img_part(_b))
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        body = {"contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": 0.1,
                                     "responseMimeType": "application/json"}}
        async with httpx.AsyncClient(timeout=90) as cli:
            r = await cli.post(_qc_endpoint, json=body, headers=headers)
            if r.status_code == 404 and _qm != ANALYZE_MODEL:
                # el modelo configurado no existe → cae al analizador de siempre
                r = await cli.post(ANALYZE_ENDPOINT, json=body, headers=headers)
        if r.status_code != 200:
            return None
        cands = r.json().get("candidates") or []
        txt = "".join(pt.get("text", "")
                      for pt in (cands[0].get("content", {}).get("parts", []) if cands else []))
        txt = txt.strip().strip("`")
        if txt.lower().startswith("json"):
            txt = txt[4:]
        a, b = txt.find("{"), txt.rfind("}")
        if a < 0 or b <= a:
            return None
        data = json.loads(txt[a:b + 1])
        pj = int(data.get("puntaje", 0) or 0)
        difs = [str(d) for d in (data.get("diferencias") or []) if str(d).strip()][:6]
        if pj <= 0:
            return None
        return {"puntaje": max(1, min(10, pj)), "diferencias": difs}
    except Exception:
        return None


AVATAR_DESC_PROMPT = (
    "Sos un asistente que describe el ROSTRO de una persona para RE-CREARLO lo más parecido "
    "posible SIN usar la foto original. Describí SOLO la CARA, la PIEL y el PELO de la mujer, en "
    "español, en un párrafo denso, SIN nombres ni identidad, SIN juicios. Incluí: etnia/tez y "
    "tono de piel exacto, forma de la cara, estructura ósea (pómulos, mandíbula, mentón), forma "
    "y color de ojos, cejas, nariz, labios, pecas/lunares/marcas y textura de piel, y "
    "color/largo/textura/peinado del pelo. "
    "NO describas el cuerpo (busto, cintura, cadera, glúteos, contextura) NI la edad NI la ropa: "
    "eso se define aparte. Devolvé SOLO la descripción del rostro, sin encabezados."
)


async def describe_avatar(ref_b64: str) -> str:
    """Image-to-text: describe el avatar con máximo detalle para recrearlo sin la foto."""
    api_key = await _current_api_key()
    if not api_key:
        return ""
    parts = [{"text": AVATAR_DESC_PROMPT}, _img_part(ref_b64)]
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2}}
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{ANALYZE_MODEL}:generateContent")
    try:
        async with httpx.AsyncClient(timeout=90) as cli:
            r = await cli.post(url, json=body, headers=headers)
        if r.status_code != 200:
            return ""
        cands = r.json().get("candidates") or []
        txt = ""
        for part in (cands[0].get("content", {}).get("parts", []) if cands else []):
            if part.get("text"):
                txt += part["text"]
        return txt.strip()
    except Exception:
        return ""


async def avatar_description_cached(av: Dict[str, Any]) -> str:
    """Devuelve la descripción del avatar (la calcula y cachea la 1ª vez)."""
    if not av:
        return ""
    if av.get("desc"):
        return av["desc"]
    ref = av.get("ref_b64") or await get_avatar_ref(av.get("id", ""))
    if not ref:
        return ""
    desc = await describe_avatar(ref)
    if desc:
        try:
            av["desc"] = desc
            await save_avatar({**av, "ref_b64": ref})
        except Exception:
            pass
    return desc


def ficha_to_text(f: Dict[str, Any]) -> str:
    """Convierte la ficha JSON en un bloque de texto para inyectar en el prompt."""
    if not f:
        return ""
    est = f.get("estampa") or {}
    lineas = []
    if f.get("ficha_para_render"):
        lineas.append(str(f["ficha_para_render"]))
    extra = []
    if f.get("tela"):
        extra.append(f"Tela: {f['tela']}.")
    if f.get("color_base"):
        extra.append(f"Color base: {f['color_base']}.")
    if est.get("descripcion"):
        det = est.get("descripcion")
        sc = est.get("escala"); de = est.get("densidad"); di = est.get("distribucion")
        extra.append(f"Estampa: {det} (escala {sc}, densidad {de}, distribución {di}).")
    if est.get("texto_presente"):
        extra.append(f"Texto en la estampa: {est['texto_presente']}.")
    if f.get("cuello"):
        extra.append(f"Cuello: {f['cuello']}.")
    if f.get("punos"):
        extra.append(f"Puños: {f['punos']}.")
    if f.get("costuras_detalles"):
        extra.append(f"Detalles: {f['costuras_detalles']}.")
    if f.get("espalda"):
        extra.append(f"ESPALDA de la prenda: {f['espalda']}")
    negs = [str(n) for n in (f.get("negativos_sugeridos") or []) if str(n).strip()]
    if negs:
        # Van en la FICHA (se renuevan con cada análisis) y NO en las aclaraciones de la
        # usuaria: antes se acumulaban ahí y mezclaban reglas de productos anteriores.
        extra.append("EVITAR (según el análisis de esta prenda): " + "; ".join(negs[:8]) + ".")
    if extra:
        lineas.append(" ".join(extra))
    return "\n".join(lineas).strip()





def _detect_separators(img: Image.Image, white_thresh: int = 238,
                       min_run_frac: float = 0.004) -> List[Tuple[int, int]]:
    """Detecta bandas de columnas blancas (separadores verticales). Rápido via resize."""
    w, h = img.size
    sample_h = 16
    g = img.convert("L").resize((w, sample_h))
    px = g.load()
    is_sep = [True] * w
    for x in range(w):
        for y in range(sample_h):
            if px[x, y] < white_thresh:
                is_sep[x] = False
                break
    bands: List[Tuple[int, int]] = []
    x = 0
    min_run = max(2, int(w * min_run_frac))
    while x < w:
        if is_sep[x]:
            start = x
            while x < w and is_sep[x]:
                x += 1
            if (x - start) >= min_run:
                bands.append((start, x))
        else:
            x += 1
    # Ignorar bandas pegadas a los bordes (márgenes blancos, no separadores internos)
    return [b for b in bands if b[0] > w * 0.02 and b[1] < w * 0.98]


def _reframe(panel: Image.Image, target_ratio: Optional[float]) -> Image.Image:
    if not target_ratio:
        return panel
    w, h = panel.size
    cur = w / h
    if abs(cur - target_ratio) < 0.01:
        return panel
    if cur > target_ratio:           # muy ancho -> recorto a los lados
        new_w = int(h * target_ratio)
        x0 = (w - new_w) // 2
        return panel.crop((x0, 0, x0 + new_w, h))
    new_h = int(w / target_ratio)    # muy alto -> recorto arriba/abajo
    y0 = (h - new_h) // 2
    return panel.crop((0, y0, w, y0 + new_h))


def split_panels(img_bytes: bytes, esperados: int,
                 reframe_to: Optional[str] = None) -> List[Image.Image]:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    target_ratio = RATIO_NUM.get(reframe_to) if reframe_to else None

    if esperados <= 1:
        return [_reframe(img, target_ratio)]

    bands = _detect_separators(img)
    cuts = [0]
    if len(bands) == esperados - 1:
        for (s, e) in bands:
            cuts.append((s + e) // 2)
    else:
        # Fallback: corte parejo
        for i in range(1, esperados):
            cuts.append(int(w * i / esperados))
    cuts.append(w)

    panels = []
    for i in range(len(cuts) - 1):
        x0, x1 = cuts[i], cuts[i + 1]
        panel = img.crop((x0, 0, x1, h))
        panel = _trim_white_edges(panel)
        panels.append(_reframe(panel, target_ratio))
    return panels


def _trim_white_edges(panel: Image.Image, thresh: int = 240) -> Image.Image:
    """Saca finos márgenes blancos laterales que dejó el separador."""
    w, h = panel.size
    g = panel.convert("L").resize((w, 8))
    px = g.load()

    def col_white(x: int) -> bool:
        return all(px[x, y] >= thresh for y in range(8))

    left = 0
    while left < w - 1 and col_white(left):
        left += 1
    right = w - 1
    while right > left and col_white(right):
        right -= 1
    if right - left < w * 0.5:        # algo raro, no recorto
        return panel
    return panel.crop((left, 0, right + 1, h))


def to_outputs(panel: Image.Image, settings: Dict[str, Any]) -> Dict[str, str]:
    """Devuelve data URLs segun output_format: png / optimized / both."""
    fmt = settings.get("output_format", "both")
    out: Dict[str, str] = {}

    if fmt in ("png", "both"):
        buf = io.BytesIO()
        panel.save(buf, format="PNG", optimize=True)
        out["png"] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    if fmt in ("optimized", "both"):
        ofmt = settings.get("optimized_format", "jpeg")
        q = int(settings.get("optimized_quality", 90))
        buf = io.BytesIO()
        if ofmt == "webp":
            panel.save(buf, format="WEBP", quality=q, method=6)
            mime = "image/webp"
        else:
            panel.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
            mime = "image/jpeg"
        out["optimized"] = f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode()

    return out


def _compress_ref(img_bytes: bytes, max_dim: int = 1024, q: int = 88) -> str:
    """Comprime una imagen de referencia (avatar/producto) a JPEG b64 livianito.
    Respeta la orientación EXIF del celular: sin esto, las fotos sacadas en vertical
    viajaban ACOSTADAS a los motores y al inspector (que no entendía la prenda)."""
    img = Image.open(io.BytesIO(img_bytes))
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = img.convert("RGB")
    img.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=q, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def _strip_data_url(s: str) -> str:
    return s.split(",", 1)[1] if "," in s and s.strip().startswith("data:") else s


# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE DRIVE (OAuth de usuario + subida). Guarda contra el 1TB de la cuenta.
# ─────────────────────────────────────────────────────────────────────────────

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
DRIVE_FOLDER_NAME = "Studio Luma"

# ─────────────────────────────────────────────────────────────────────────────
# LOGIN CON GOOGLE (identidad + Drive en un mismo permiso) + sesión por cookie
# ─────────────────────────────────────────────────────────────────────────────

SESSION_SECRET = os.getenv("SESSION_SECRET", "") or (GOOGLE_CLIENT_SECRET + "::studioluma")
SESSION_COOKIE = "sl_sess"
AUTH_SCOPES = (
    "openid email profile "
    "https://www.googleapis.com/auth/drive.file"
)
# Lista opcional de mails permitidos (coma-separada). Vacío = cualquiera con Google.
ALLOWED_EMAILS = [e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()]


def K_USER(sub: str) -> str:
    return f"studioluma:user:{sub}"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign_session(sub: str) -> str:
    mac = hmac.new(SESSION_SECRET.encode(), sub.encode(), hashlib.sha256).digest()
    return f"{sub}.{_b64u(mac)}"


def _verify_session(cookie: str) -> Optional[str]:
    if not cookie or "." not in cookie:
        return None
    sub, sig = cookie.rsplit(".", 1)
    expected = hmac.new(SESSION_SECRET.encode(), sub.encode(), hashlib.sha256).digest()
    try:
        if hmac.compare_digest(_b64u_dec(sig), expected):
            return sub
    except Exception:
        return None
    return None


def session_sub_from_request(request: Request) -> Optional[str]:
    """Lee la cookie de sesión y devuelve el sub del usuario, o None. Lo usa el gate."""
    return _verify_session(request.cookies.get(SESSION_COOKIE, ""))


def _decode_id_token(id_token: str) -> Dict[str, Any]:
    """Extrae el payload del id_token de Google (viene directo de Google por TLS)."""
    try:
        payload = id_token.split(".")[1]
        return json.loads(_b64u_dec(payload).decode())
    except Exception:
        return {}


async def get_user(sub: str) -> Dict[str, Any]:
    u = await kv.get(K_USER(sub))
    return u if isinstance(u, dict) else {}


async def save_user(sub: str, data: Dict[str, Any]) -> None:
    await kv.set(K_USER(sub), data)


async def current_user(request: Request) -> Dict[str, Any]:
    sub = session_sub_from_request(request)
    if not sub:
        return {}
    u = await get_user(sub)
    if u:
        u["sub"] = sub
    return u




def _drive_redirect_uri(request: Request) -> str:
    override = os.getenv("DRIVE_REDIRECT_URI", "").strip()
    if override:
        return override
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"https://{host}{ROUTE_PREFIX}/api/drive/callback"


async def _drive_state() -> Dict[str, Any]:
    s = await kv.get(K_DRIVE)
    return s if isinstance(s, dict) else {}


async def drive_connected() -> bool:
    s = await _drive_state()
    return bool(s.get("refresh_token"))


async def _drive_access_token(user_sub: Optional[str] = None) -> Optional[str]:
    if user_sub:
        rec = await get_user(user_sub)
        rt = rec.get("refresh_token")
    else:
        s = await _drive_state()
        rt = s.get("refresh_token")
    if not rt:
        return None
    data = {
        "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": rt, "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post("https://oauth2.googleapis.com/token", data=data)
    if r.status_code != 200:
        return None
    return r.json().get("access_token")


async def _drive_connected_for(user_sub: Optional[str]) -> bool:
    if user_sub:
        rec = await get_user(user_sub)
        return bool(rec.get("refresh_token"))
    # Sin usuario identificado NO se usa ningún Drive personal (evita guardar en
    # el Drive de otra cuenta). El Drive global sólo aplica al modo sin login.
    if CURRENT_SUB.get():
        return False
    return await drive_connected()


async def _drive_ensure_folder(token: str, user_sub: Optional[str] = None) -> Optional[str]:
    """Devuelve el folder_id guardado, o crea la carpeta y lo guarda (por usuario si aplica)."""
    if user_sub:
        rec = await get_user(user_sub)
        if rec.get("drive_folder_id"):
            return rec["drive_folder_id"]
    else:
        s = await _drive_state()
        if s.get("folder_id"):
            return s["folder_id"]
    headers = {"Authorization": f"Bearer {token}"}
    meta = {"name": DRIVE_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post("https://www.googleapis.com/drive/v3/files",
                           headers=headers, json=meta)
    if r.status_code not in (200, 201):
        return None
    fid = r.json().get("id")
    if user_sub:
        rec = await get_user(user_sub)
        rec["drive_folder_id"] = fid
        await save_user(user_sub, rec)
    else:
        s = await _drive_state()
        s["folder_id"] = fid
        s["folder_name"] = DRIVE_FOLDER_NAME
        await kv.set(K_DRIVE, s)
    return fid


async def drive_upload(filename: str, content: bytes, mime: str,
                       user_sub: Optional[str] = None) -> Optional[str]:
    """Sube un archivo a la carpeta de Drive del usuario. Devuelve el link, o None si falla."""
    token = await _drive_access_token(user_sub)
    if not token:
        return None
    folder_id = await _drive_ensure_folder(token, user_sub)
    meta: Dict[str, Any] = {"name": filename}
    if folder_id:
        meta["parents"] = [folder_id]
    # subida multipart (metadata + media en una sola llamada)
    boundary = "lumaboundary7c3f"
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        + json.dumps(meta) + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--".encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/related; boundary={boundary}",
    }
    async with httpx.AsyncClient(timeout=120) as cli:
        r = await cli.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webViewLink",
            headers=headers, content=body)
    if r.status_code not in (200, 201):
        print(f"[imagenes_ia][drive] upload fallo {r.status_code}: {r.text[:200]}")
        return None
    return r.json().get("webViewLink")


_BG_TASKS: set = set()


async def _save_panels_to_drive(panels: List[Any], mode: str,
                                user_sub: Optional[str] = None) -> None:
    """Sube los paneles a Drive en PNG, en segundo plano (no bloquea la generación)."""
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    for idx, panel in enumerate(panels):
        try:
            buf = io.BytesIO()
            await asyncio.to_thread(panel.save, buf, "PNG")
            await drive_upload(f"studioluma_{mode}_{ts}_{idx + 1}.png", buf.getvalue(),
                               "image/png", user_sub=user_sub)
        except Exception as e:
            print(f"[imagenes_ia][drive bg] {e}")





# ─────────────────────────────────────────────────────────────────────────────
# ROUTER + ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

async def _bind_user(request: Request) -> None:
    """Se ejecuta en CADA request (en el contexto del endpoint): fija el usuario
    actual para que todas las lecturas/escrituras queden aisladas por cuenta."""
    set_current_sub(session_sub_from_request(request))


router = APIRouter(dependencies=[Depends(_bind_user)])

LOGIN_PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Studio Luma · Entrar</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%23161419'/%3E%3Crect x='2.5' y='2.5' width='59' height='59' rx='12' fill='none' stroke='%23c9a86b' stroke-width='2'/%3E%3Ctext x='32' y='44' font-family='Georgia,serif' font-size='34' font-weight='600' fill='%23d8b878' text-anchor='middle'%3ESL%3C/text%3E%3C/svg%3E">
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:opsz,wght@6..96,500;6..96,600&family=Jost:wght@300;400;500&display=swap" rel="stylesheet">
<style>
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;background:#131218;color:#ecebf1;font-family:Jost,sans-serif;
    display:flex;align-items:center;justify-content:center;padding:24px}
  .box{max-width:380px;width:100%;text-align:center}
  .mono{width:72px;height:72px;border-radius:18px;border:1px solid #c9a86b;margin:0 auto 22px;
    display:flex;align-items:center;justify-content:center;background:linear-gradient(150deg,#221f27,#161419);
    font-family:'Bodoni Moda',serif;font-weight:600;font-size:34px;color:#d8b878;
    box-shadow:inset 0 0 18px rgba(201,168,107,.14)}
  h1{font-family:'Bodoni Moda',serif;font-weight:600;font-size:30px;margin:0 0 6px;letter-spacing:.3px}
  p{color:#96919f;font-size:14px;margin:0 0 28px;line-height:1.55}
  a.btn{display:flex;align-items:center;justify-content:center;gap:10px;text-decoration:none;
    background:#fff;color:#1a1a1a;border-radius:11px;padding:13px 18px;font-weight:500;font-size:15px}
  a.btn:hover{opacity:.92}
  .g{width:20px;height:20px}
  small{display:block;margin-top:22px;color:#6b6675;font-size:12px;line-height:1.6}
</style></head><body>
<div class="box">
  <div class="mono">SL</div>
  <h1>Studio Luma</h1>
  <p>Fotos de producto con IA para tu tienda.<br>Entrá con tu cuenta de Google para empezar.</p>
  <a class="btn" href="%%PREFIX%%/auth/google">
    <svg class="g" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.6l6.7-6.7C35.6 2.6 30.2 0 24 0 14.6 0 6.4 5.4 2.5 13.3l7.8 6.1C12.2 13.7 17.6 9.5 24 9.5z"/><path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.5 3-2.2 5.5-4.7 7.2l7.3 5.7c4.3-4 6.9-9.9 6.9-17.4z"/><path fill="#FBBC05" d="M10.3 28.4c-.5-1.5-.8-3.1-.8-4.9s.3-3.4.8-4.9l-7.8-6.1C.9 15.6 0 19.6 0 23.5s.9 7.9 2.5 11l7.8-6.1z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.3-5.7c-2 1.4-4.7 2.3-8.6 2.3-6.4 0-11.8-4.2-13.7-9.9l-7.8 6.1C6.4 42.6 14.6 48 24 48z"/></svg>
    Entrar con Google
  </a>
  <small>Al entrar, se crea una carpeta "Studio Luma" en tu Google Drive donde se guardan tus imágenes. Podés revocar el acceso cuando quieras desde tu cuenta de Google.</small>
</div></body></html>""".replace("%%PREFIX%%", ROUTE_PREFIX)


def _auth_redirect_uri(request: Request) -> str:
    override = os.getenv("AUTH_REDIRECT_URI", "").strip()
    if override:
        return override
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"https://{host}{ROUTE_PREFIX}/auth/callback"


@router.get(ROUTE_PREFIX + "/auth/login", response_class=HTMLResponse)
async def auth_login_page():
    return HTMLResponse(LOGIN_PAGE)


@router.get(ROUTE_PREFIX + "/auth/google")
async def auth_google(request: Request):
    if not GOOGLE_CLIENT_ID:
        return HTMLResponse("<h3>Falta configurar GOOGLE_CLIENT_ID en el servidor.</h3>", 500)
    from urllib.parse import urlencode
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _auth_redirect_uri(request),
        "response_type": "code",
        "scope": AUTH_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@router.get(ROUTE_PREFIX + "/auth/callback")
async def auth_callback(request: Request, code: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse(ROUTE_PREFIX + "/auth/login")
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": _auth_redirect_uri(request),
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post("https://oauth2.googleapis.com/token", data=data)
    if r.status_code != 200:
        return HTMLResponse(f"<h3>No pude iniciar sesión. {r.text[:200]}</h3>", 400)
    tok = r.json()
    info = _decode_id_token(tok.get("id_token", ""))
    sub = info.get("sub")
    email = (info.get("email") or "").lower()
    if not sub:
        return HTMLResponse("<h3>Google no devolvió tu identidad. Probá de nuevo.</h3>", 400)
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        return HTMLResponse("<h3>Tu cuenta no está habilitada todavía para Studio Luma.</h3>", 403)
    prev = await get_user(sub)
    rec = {
        "sub": sub, "email": email, "name": info.get("name", ""),
        "picture": info.get("picture", ""),
        "refresh_token": tok.get("refresh_token") or prev.get("refresh_token", ""),
        "drive_folder_id": prev.get("drive_folder_id", ""),
    }
    await save_user(sub, rec)
    resp = RedirectResponse(ROUTE_PREFIX or "/")
    resp.set_cookie(SESSION_COOKIE, _sign_session(sub), max_age=60 * 60 * 24 * 30,
                    httponly=True, secure=True, samesite="lax", path="/")
    return resp


@router.get(ROUTE_PREFIX + "/auth/logout")
async def auth_logout():
    resp = RedirectResponse(ROUTE_PREFIX + "/auth/login")
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get(ROUTE_PREFIX + "/auth/me")
async def auth_me(request: Request) -> Dict[str, Any]:
    u = await current_user(request)
    if not u:
        return {"logged_in": False}
    return {"logged_in": True, "email": u.get("email"), "name": u.get("name"),
            "picture": u.get("picture"), "drive": bool(u.get("refresh_token"))}


@router.get(ROUTE_PREFIX + "/api/prefs")
async def api_get_prefs(request: Request) -> Dict[str, Any]:
    u = await current_user(request)
    if not u:
        raise HTTPException(401, "login requerido")
    return {"onboarded": bool(u.get("onboarded")), "prefs": u.get("prefs") or {}}


@router.post(ROUTE_PREFIX + "/api/prefs")
async def api_save_prefs(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    sub = session_sub_from_request(request)
    if not sub:
        raise HTTPException(401, "login requerido")
    rec = await get_user(sub)
    rec["prefs"] = payload.get("prefs") or {}
    rec["onboarded"] = True
    await save_user(sub, rec)
    return {"ok": True}


@router.get(ROUTE_PREFIX + "/api/mykey")
async def api_get_mykey(request: Request) -> Dict[str, Any]:
    u = await current_user(request)
    if not u:
        raise HTTPException(401, "login requerido")
    return {"has_key": bool((u.get("gemini_key") or "").strip())}


@router.post(ROUTE_PREFIX + "/api/mykey")
async def api_set_mykey(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    sub = session_sub_from_request(request)
    if not sub:
        raise HTTPException(401, "login requerido")
    rec = await get_user(sub)
    rec["gemini_key"] = (payload.get("key") or "").strip()
    await save_user(sub, rec)
    return {"ok": True, "has_key": bool(rec["gemini_key"])}


@router.get(ROUTE_PREFIX + "/api/mydrive")
async def api_mydrive(request: Request) -> Dict[str, Any]:
    """Diagnóstico real del Drive del usuario: ¿hay token? ¿funciona?"""
    u = await current_user(request)
    if not u:
        raise HTTPException(401, "login requerido")
    sub = u.get("sub")
    has_rt = bool((u.get("refresh_token") or "").strip())
    if not has_rt:
        return {"connected": False, "reason": "sin_permiso",
                "msg": "Tu cuenta no tiene el permiso de Drive guardado. Reconectá Drive."}
    token = await _drive_access_token(sub)
    if not token:
        return {"connected": False, "reason": "token_invalido",
                "msg": "El permiso de Drive venció o fue revocado. Reconectá Drive."}
    folder = await _drive_ensure_folder(token, sub)
    if not folder:
        return {"connected": False, "reason": "sin_carpeta",
                "msg": "No pude crear la carpeta en tu Drive. Reconectá Drive."}
    return {"connected": True, "folder_id": folder, "folder": DRIVE_FOLDER_NAME,
            "msg": "Drive conectado. Tus imágenes se guardan en la carpeta " + DRIVE_FOLDER_NAME + "."}


@router.get(ROUTE_PREFIX + "/auth/reconnect")
async def auth_reconnect(request: Request):
    """Vuelve a pedir permiso a Google forzando que devuelva refresh_token."""
    from urllib.parse import urlencode
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _auth_redirect_uri(request),
        "response_type": "code",
        "scope": AUTH_SCOPES,
        "access_type": "offline",
        "prompt": "consent",              # fuerza consentimiento => manda refresh_token
        "include_granted_scopes": "true",
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


_PALABRAS_RIESGO = (
    "sensual", "seductor", "provocativ", "sexy", "erótic", "erotic", "atrevid",
    "insinuant", "sugerente", "desnud", "lasciv", "voluptuos", "tentador",
)


_ENCUADRES_RIESGO = [
    # Primer plano de una zona del cuerpo SIN cara, en ropa interior: es de las señales
    # más fuertes del filtro. Se traduce a un plano equivalente pero con rostro visible.
    (r"de cerca[^.,;]*\btorso\b[^.,;]*", "plano medio de la cadera para arriba, con el rostro visible"),
    (r"\bsolo\s+(el\s+)?torso\b[^.,;]*", "plano medio con el rostro visible"),
    (r"primer(?:ísimo)?\s+plano\s+(?:del?\s+)?(torso|busto|escote|cuerpo|abdomen|cola|gl[uú]teos)[^.,;]*",
     "plano medio con el rostro visible"),
    (r"\bdetalle\s+(?:del?\s+)?(busto|escote|cola|gl[uú]teos)\b[^.,;]*",
     "plano medio con el rostro visible"),
]


def _encuadre_seguro(enc: Optional[str]) -> str:
    """Reescribe encuadres que gatillan el filtro (primeros planos de zonas del cuerpo sin
    cara) por un plano equivalente y seguro, conservando la intención."""
    t = str(enc or "").strip()
    if not t:
        return "cuerpo entero de pies a cabeza"
    for pat, rep in _ENCUADRES_RIESGO:
        t = re.sub(pat, rep, t, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", t).strip(" ,;") or "cuerpo entero de pies a cabeza"


_REEMPLAZOS_SEGUROS = [
    # "foto candid / sin que lo sepa" → el filtro lo lee como foto NO consentida en ropa
    # interior y bloquea SIEMPRE. Se traduce a lo que la usuaria quiere decir de verdad.
    (r"desprevenid[ao]s?", "espontánea"),
    (r"sin que (se dé|se de|lo note|lo sepa|se entere)[a-z ]*", "con naturalidad "),
    (r"tomada al azar", "espontánea"),
    (r"foto robada", "foto espontánea"),
    (r"robad[ao]s?", "espontánea"),
    (r"c[aá]mara oculta", "cámara"),
    (r"a escondidas", "con naturalidad"),
    (r"sin posar", "con pose natural"),
]


def _sanear_indicacion(t: str) -> str:
    """Reescribe términos que gatillan el filtro (candid/no-consentido y connotaciones)
    manteniendo la intención. Devuelve texto seguro para el motor de imágenes."""
    out = t or ""
    for pat, rep in _REEMPLAZOS_SEGUROS:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    # palabras con connotación: se eliminan directamente
    for w in _PALABRAS_RIESGO:
        out = re.sub(r"\w*" + re.escape(w) + r"\w*", "", out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip()


def _texto_seguro(t: str) -> bool:
    """True si el texto NO contiene palabras que gatillan el filtro de imágenes.
    (Lección aprendida: UNA sola palabra con connotación bloquea la toma, aunque sea
    para negarla.)"""
    low = (t or "").lower()
    return not any(w in low for w in _PALABRAS_RIESGO)


def _step_desc(sdef: Dict[str, Any]) -> str:
    m = sdef.get("mode")
    if m == "trio":
        return "grupal de las 3 modelos"
    if m == "product_only":
        return "producto solo"
    poses = {0: "de frente", 6: "de perfil", 3: "de espalda", 2: "sentada", 4: "caminando",
             8: "primer plano", 9: "detalle de prenda", 10: "detalle de espalda",
             11: "fundida con el ambiente", 12: "en el piso", 13: "con utilería"}
    d = poses.get(int(sdef.get("force_pose", 0) or 0), "individual")
    col = (sdef.get("color_set") or "").strip()
    return f"individual {d}" + (f" · color {col}" if col else "")


@router.post(ROUTE_PREFIX + "/api/jobs/{jid}/retry/{idx}")
async def api_job_retry(jid: str, idx: int, request: Request,
                        payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Reintenta UNA toma salteada del set (opcionalmente con encuadre seguro)."""
    set_current_sub(session_sub_from_request(request))
    state = await _job_owned(jid)
    base = await _job_in_get(jid)
    ctx = await _job_ctx_get(jid) or {}
    if not (state and base):
        raise HTTPException(404, "No encontré ese trabajo (quizás venció).")
    steps = base.get("plan") or _set_plan(bool(base.get("hq")))
    if not (0 <= idx < len(steps)):
        raise HTTPException(400, "Toma inválida.")
    sdef = steps[idx]
    seguro = str(payload.get("modo", "")) == "seguro"
    use_anchors = None
    crops = ctx.get("group_crops") or []
    if (sdef["mode"] == "on_model" and sdef.get("modelo_idx") is not None
            and not sdef.get("use_back") and not seguro):
        k = int(sdef["modelo_idx"])
        if crops and k < len(crops) and crops[k]:
            use_anchors = [_dataurl_to_anchor(crops[k])]
    elif sdef["mode"] == "on_model" and not seguro:
        # Toma normal reintentada: usa las anclas acumuladas del set (caras primero)
        use_anchors = _anchors_para_envio(ctx.get("anchors") or [])
    p = _build_step_payload(base, sdef, use_anchors)
    if seguro:
        prm = dict(p.get("params") or {})
        prm["complemento"] = "si"
        nota = ("ENCUADRE EDITORIAL SEGURO: plano de la cadera para arriba, bombacha lisa de "
                "talle clásico que combina, estética de catálogo comercial limpia y respetuosa.")
        prev = str(prm.get("aclaraciones", "")).strip()
        prm["aclaraciones"] = (prev + " " if prev else "") + nota
        p["params"] = prm
    res = await _do_generate(p)          # si vuelve a bloquear, devuelve 422 y el front avisa
    await _job_store_result(jid, idx, res)
    state["skipped"] = [s for s in (state.get("skipped") or []) if s != idx]
    await _job_state_save(state)
    return {"ok": True, "idx": idx}


@router.get(ROUTE_PREFIX or "/", response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    # Sin caché: el celular guardaba la página vieja después de cada deploy y las
    # opciones nuevas "no aparecían" hasta un hard refresh (imposible en el celu).
    return HTMLResponse(HTML_PAGE, headers={
        "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
        "Pragma": "no-cache",
    })


@router.get(ROUTE_PREFIX + "/api/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "version": VERSION, "model": MODEL_ID, "kv": kv.backend,
            "redis_url_set": bool(os.getenv("REDIS_URL")),
            "kv_error": kv.last_error,
            "gemini_key": bool(GEMINI_API_KEY),
            "fal_key": bool(FAL_API_KEY)}


@router.get(ROUTE_PREFIX + "/api/settings")
async def api_get_settings() -> Dict[str, Any]:
    return await get_settings()


@router.post(ROUTE_PREFIX + "/api/settings")
async def api_save_settings(patch: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return await save_settings(patch)


@router.post(ROUTE_PREFIX + "/api/analyze")
async def api_analyze(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Analiza las fotos del producto y devuelve una ficha técnica + texto para el prompt."""
    prods = payload.get("product_images") or []
    if not prods:
        raise HTTPException(400, "Subí al menos una foto del producto para analizar.")
    prod_b64s = [
        _compress_ref(base64.b64decode(_strip_data_url(p)), max_dim=1024, q=85)
        for p in prods[:6] if p
    ]
    # Fotos de la ESPALDA (opcionales): van rotuladas aparte para que la ficha
    # describa la espalda REAL; sin ellas la ficha deduce una espalda conservadora.
    backs = payload.get("product_images_back") or []
    back_b64s = [
        _compress_ref(base64.b64decode(_strip_data_url(p)), max_dim=1024, q=85)
        for p in backs[:3] if p
    ]
    ficha = await gemini_analyze(prod_b64s, back_b64s)
    return {"ok": True, "ficha": ficha, "ficha_text": ficha_to_text(ficha)}


_SEGMENT_PROMPT = (
    "Detect the garment(s) / clothing item(s) in this product photo (the clothes only — "
    "NOT the person's skin, hair or face, NOT the background, NOT props). "
    "Give the segmentation masks for the garment(s). Output a JSON list of segmentation "
    "masks where each entry contains the 2D bounding box in the key \"box_2d\", the "
    "segmentation mask in key \"mask\", and the text label in the key \"label\"."
)


@router.post(ROUTE_PREFIX + "/api/segment_prenda")
async def api_segment_prenda(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Devuelve la silueta (máscara) de la prenda para el Editor de color.
    Usa el modelo de análisis (flash-lite): centavos por llamada."""
    img = (payload.get("image") or "").strip()
    if not img:
        raise HTTPException(400, "Falta la imagen.")
    api_key = await _current_api_key()
    if not api_key:
        raise HTTPException(500, "Falta la API key de Google (ni propia ni global).")
    b64 = _compress_ref(base64.b64decode(_strip_data_url(img)), max_dim=1024, q=88)
    parts = [{"text": _SEGMENT_PROMPT}, _img_part(b64)]
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    cfg_fast = {"temperature": 0.1, "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingLevel": "low"}}
    cfg_plain = {"temperature": 0.1, "responseMimeType": "application/json"}

    async def _call(cfg):
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": cfg}
        async with httpx.AsyncClient(timeout=120) as cli:
            return await cli.post(ANALYZE_ENDPOINT, json=body, headers=headers)

    r = await _call(cfg_fast)
    if r.status_code == 400:
        r = await _call(cfg_plain)
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"El detector devolvió error: {r.text[:300]}")
    cands = r.json().get("candidates") or []
    txt = "".join(pt.get("text", "")
                  for pt in (cands[0].get("content", {}).get("parts", []) if cands else [])
                  if pt.get("text"))
    txt = txt.strip().strip("`")
    if txt.lower().startswith("json"):
        txt = txt[4:]
    a, b = txt.find("["), txt.rfind("]")
    if a < 0 or b <= a:
        raise HTTPException(422, "El detector no devolvió máscaras (probá con otra foto).")
    try:
        items = json.loads(txt[a:b + 1])
    except Exception:
        raise HTTPException(422, "No pude leer la respuesta del detector.")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        box = it.get("box_2d")
        mask = it.get("mask") or ""
        if not (isinstance(box, list) and len(box) == 4 and mask):
            continue
        if not str(mask).startswith("data:image"):
            mask = "data:image/png;base64," + str(mask)
        out.append({"box_2d": box, "mask": mask, "label": str(it.get("label", ""))[:60]})
    if not out:
        raise HTTPException(422, "El detector no encontró la prenda (probá con otra foto).")
    return {"ok": True, "items": out}


@router.get(ROUTE_PREFIX + "/api/avatars")
async def api_list_avatars() -> Dict[str, Any]:
    return await list_avatars()


def k_avficha(avatar_id: str) -> str:
    return _pfx() + f"avficha:{avatar_id}"


@router.get(ROUTE_PREFIX + "/api/avatars/{avatar_id}/ficha")
async def api_avatar_ficha_get(avatar_id: str) -> Dict[str, Any]:
    """Ficha guardada del avatar (cuerpo/edad/altura) para autocompletar el set."""
    return {"ficha": (await kv.get(k_avficha(avatar_id))) or {}}


@router.post(ROUTE_PREFIX + "/api/avatars/{avatar_id}/ficha")
async def api_avatar_ficha_set(avatar_id: str,
                               payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    campos = ("contextura", "busto", "cola", "abdomen", "edad", "altura")
    f = {k: str(payload.get(k, ""))[:40] for k in campos}
    await kv.set(k_avficha(avatar_id), f)
    return {"ok": True, "ficha": f}


@router.get(ROUTE_PREFIX + "/api/avatars/{avatar_id}/ref")
async def api_avatar_ref(avatar_id: str):
    ref = await get_avatar_ref(avatar_id)
    if not ref:
        raise HTTPException(404, "Avatar sin referencia.")
    return Response(content=base64.b64decode(ref), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.post(ROUTE_PREFIX + "/api/avatars/generate")
async def api_avatar_generate(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Genera un candidato de avatar (todavía no se lockea)."""
    gender = payload.get("gender")
    if gender not in GENEROS:
        raise HTTPException(400, "gender debe ser 'mujer' u 'hombre'.")
    settings = await get_settings()

    est = _pricing(settings).get("2K", PRICING["2K"])
    ok, motivo, _, _ = await budget_check(est)
    if not ok:
        raise HTTPException(402, motivo)

    prompt = build_prompt_avatar(gender, payload.get("attrs", {}))
    img_bytes = await gemini_generate([{"text": prompt}], settings,
                                      aspect="3:4", image_size="2K")
    await budget_record("avatar", "2K", est, 1, note=f"candidato {gender}")
    ref_b64 = _compress_ref(img_bytes, max_dim=1536, q=92)  # referencia en alta (no cuesta + tokens)
    return {"preview": "data:image/jpeg;base64," + ref_b64, "ref_b64": ref_b64}


@router.post(ROUTE_PREFIX + "/api/avatars/from_upload")
async def api_avatar_from_upload(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Usa una imagen SUBIDA por la usuaria como referencia de avatar (sin IA, sin costo)."""
    img = payload.get("image")
    if not img:
        raise HTTPException(400, "Falta la imagen.")
    raw = base64.b64decode(_strip_data_url(img))
    ref_b64 = _compress_ref(raw, max_dim=1536, q=92)
    return {"preview": "data:image/jpeg;base64," + ref_b64, "ref_b64": ref_b64}


@router.post(ROUTE_PREFIX + "/api/avatars/lock")
async def api_avatar_lock(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Guarda y lockea un avatar en su slot."""
    gender = payload.get("gender")
    slot = payload.get("slot")
    ref_b64 = payload.get("ref_b64")
    if gender not in GENEROS or not isinstance(slot, int) or not ref_b64:
        raise HTTPException(400, "Faltan gender, slot o ref_b64.")
    av = {
        "id": f"{gender[:1]}{slot}_{int(time.time())}",
        "gender": gender, "slot": slot,
        "name": payload.get("name", f"{gender} {slot + 1}"),
        "description": payload.get("description", ""),
        "ref_b64": _strip_data_url(ref_b64),
        "locked": True,
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    ok = await save_avatar(av)
    if not ok:
        raise HTTPException(
            500,
            f"No se pudo guardar el avatar (almacenamiento: {kv.backend}). "
            f"{kv.last_error or 'Revisá que REDIS_URL esté configurada.'}")
    return {"ok": True, "id": av["id"], "backend": kv.backend}


@router.delete(ROUTE_PREFIX + "/api/avatars/{avatar_id}")
async def api_avatar_delete(avatar_id: str) -> Dict[str, Any]:
    ok = await delete_avatar(avatar_id)
    return {"ok": ok}


@router.get(ROUTE_PREFIX + "/api/avatars/debug")
async def api_avatars_debug() -> Dict[str, Any]:
    """Diagnóstico: prueba escritura/lectura (chica y grande) + estado de avatares."""
    # test chico
    tk = "imagenes:__selftest__"
    stamp = int(time.time())
    small_w = await kv.set(tk, {"t": stamp})
    small_r = await kv.get(tk)
    await kv.delete(tk)

    # test grande (~400 KB, tamaño tipico de una foto comprimida)
    bk = "imagenes:__bigtest__"
    big = "x" * 400000
    big_w = await kv.set(bk, big)
    big_r = await kv.get(bk)
    big_ok = (big_r == big)
    await kv.delete(bk)

    store = await _avatar_store()
    detail: Dict[str, Any] = {}
    for g in GENEROS:
        rows = []
        for av in store.get(g, []):
            aid = av.get("id")
            blob = await kv.get(k_avref(aid)) if aid else None
            rows.append({
                "id": aid, "slot": av.get("slot"), "name": av.get("name"),
                "ref_embebida": bool(av.get("ref_b64")),
                "ref_blob_bytes": len(blob) if blob else 0,
                "recuperable": bool(av.get("ref_b64")) or bool(blob),
            })
        detail[g] = rows

    return {
        "backend": kv.backend,
        "last_error": kv.last_error,
        "write_test_ok": small_w,
        "read_back_ok": bool(small_r) and small_r.get("t") == stamp,
        "big_write_ok": big_w,
        "big_read_ok": big_ok,
        "indice_existe": store != {},
        "avatares": detail,
    }


@router.get(ROUTE_PREFIX + "/api/budget")
async def api_budget() -> Dict[str, Any]:
    led = await get_ledger()
    cap = await get_cap()
    return {"ledger": led, "cap": cap, "pricing": _pricing(await get_settings())}


@router.post(ROUTE_PREFIX + "/api/budget/cap")
async def api_set_cap(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    cap = payload.get("monthly_usd")
    if cap in ("", None):
        await set_cap(None)
        return {"cap": None}
    await set_cap(float(cap))
    return {"cap": float(cap)}


@router.get(ROUTE_PREFIX + "/api/templates")
async def api_get_templates() -> Dict[str, Any]:
    return await get_templates()


@router.post(ROUTE_PREFIX + "/api/templates")
async def api_save_template(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    name = (payload.get("name") or "").strip()
    data = payload.get("data") or {}
    if not name:
        raise HTTPException(400, "Falta el nombre de la plantilla.")
    ok = await save_template(name, data)
    if not ok:
        raise HTTPException(500, f"No se pudo guardar (almacenamiento: {kv.backend}).")
    return {"ok": True, "name": name}


@router.delete(ROUTE_PREFIX + "/api/templates/{name}")
async def api_delete_template(name: str) -> Dict[str, Any]:
    ok = await delete_template(name)
    return {"ok": ok}


@router.get(ROUTE_PREFIX + "/api/drive/status")
async def api_drive_status(request: Request) -> Dict[str, Any]:
    s = await _drive_state()
    return {
        "connected": bool(s.get("refresh_token")),
        "email": s.get("email", ""),
        "folder_name": s.get("folder_name", DRIVE_FOLDER_NAME),
        "redirect_uri": _drive_redirect_uri(request),
        "client_id_set": bool(GOOGLE_CLIENT_ID),
    }


@router.get(ROUTE_PREFIX + "/api/drive/auth")
async def api_drive_auth(request: Request):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(400, "Faltan GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET en Railway.")
    from urllib.parse import urlencode
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _drive_redirect_uri(request),
        "response_type": "code",
        "scope": DRIVE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return RedirectResponse(url)


@router.get(ROUTE_PREFIX + "/api/drive/callback")
async def api_drive_callback(request: Request, code: str = "", error: str = ""):
    if error or not code:
        return HTMLResponse(f"<h3>Error conectando Drive: {error or 'sin código'}</h3>")
    data = {
        "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "code": code, "grant_type": "authorization_code",
        "redirect_uri": _drive_redirect_uri(request),
    }
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post("https://oauth2.googleapis.com/token", data=data)
    if r.status_code != 200:
        return HTMLResponse(f"<h3>No se pudo conectar: {r.text[:300]}</h3>")
    tok = r.json()
    rt = tok.get("refresh_token")
    if not rt:
        return HTMLResponse("<h3>Google no devolvió refresh_token. Revocá el acceso de la "
                            "app en tu cuenta Google y volvé a conectar.</h3>")
    # email (opcional, lindo para mostrar)
    email = ""
    try:
        at = tok.get("access_token")
        async with httpx.AsyncClient(timeout=15) as cli:
            ui = await cli.get("https://www.googleapis.com/oauth2/v2/userinfo",
                               headers={"Authorization": f"Bearer {at}"})
        if ui.status_code == 200:
            email = ui.json().get("email", "")
    except Exception:
        pass
    s = await _drive_state()
    s["refresh_token"] = rt
    s["email"] = email
    await kv.set(K_DRIVE, s)
    return HTMLResponse(
        "<h2>✅ Google Drive conectado</h2>"
        "<p>Ya podés cerrar esta pestaña y volver a la app. Las imágenes se van a guardar "
        f"en la carpeta <b>{DRIVE_FOLDER_NAME}</b> de tu Drive.</p>"
        f"<p><a href='{ROUTE_PREFIX or '/'}'>← Volver</a></p>")


@router.post(ROUTE_PREFIX + "/api/drive/disconnect")
async def api_drive_disconnect() -> Dict[str, Any]:
    await kv.delete(K_DRIVE)
    return {"ok": True}


async def _do_generate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generación principal.
      mode: "on_model" | "product_only"
      product_image: data URL de la foto real del producto (obligatorio)
      avatar_id: (on_model) cuál avatar usar
      modo_producto: (product_only) flat_lay|doblada|percha|maniqui_fantasma
      params: {tela, punos, costuras, cuello, color, calce, pose, fondo, luz, encuadre}
      paneles: int (cuántas tomas en una imagen, separadas por línea blanca)
      aspect: aspect ratio de generación (default settings; 21:9 ideal para paneles)
      image_size: "1K"|"2K"|"4K" (default settings)
      reframe: aspect ratio final de cada panel (1:1, 4:5, 9:16...) o null
    """
    if payload.get("user_sub"):
        set_current_sub(payload.get("user_sub"))
    settings = await get_settings()
    mode = payload.get("mode", "on_model")
    params = payload.get("params", {}) or {}
    paneles = max(1, int(payload.get("paneles", 1)))
    aspect = payload.get("aspect") or settings.get("aspect_ratio", "4:5")
    image_size = payload.get("image_size") or settings.get("image_size", "4K")
    reframe = payload.get("reframe")
    style = payload.get("style") or settings.get("default_style", "instagram_real")

    if aspect not in ASPECTOS_VALIDOS:
        raise HTTPException(400, f"aspect inválido. Usá uno de: {ASPECTOS_VALIDOS}")
    precios = _pricing(settings)
    if image_size not in precios:
        raise HTTPException(400, "image_size debe ser 1K, 2K o 4K.")

    # Una o varias fotos del producto (arriba, pantalón, detalle...). Hasta 6.
    prods = payload.get("product_images")
    if not prods:
        single = payload.get("product_image")
        prods = [single] if single else []
    if not prods:
        raise HTTPException(400, "Falta al menos una foto real del producto.")
    prods = prods[:6]
    # Las fotos del producto van al motor lo más fieles posible al original: el filtro
    # analiza los bytes reales, así que degradarlas cambia lo que ve.
    # La doc de OpenAI permite hasta 512 MB por pedido y 1500 imágenes, así que no hace
    # falta degradar las fotos: van casi como el original.
    _hd = str(settings.get("refs_hd", "si")).lower() in ("si", "sí", "1", "on", "true")
    _md, _q = (3200, 98) if _hd else (1536, 90)
    prod_b64s = [
        _compress_ref(base64.b64decode(_strip_data_url(p)), max_dim=_md, q=_q)
        for p in prods
    ]
    n_prod = len(prod_b64s)

    # Fotos de la ESPALDA (si las hay): van al INSPECTOR rotuladas aparte, para que las
    # tomas de espalda se comparen contra la espalda real y no contra el frente.
    _backs_qc = payload.get("product_images_back") or []
    back_qc_b64s = [
        _compress_ref(base64.b64decode(_strip_data_url(b)), max_dim=1024, q=85)
        for b in _backs_qc[:2] if b
    ]

    # Imágenes de "ancla" (tomas previas buenas) para consistencia entre generaciones del
    # set. ACUMULATIVAS: la toma 2 ve la 1; la 3 ve la 1 y la 2; etc. (hasta 8, con las
    # tomas CON CARA primero). Buena resolución: son la referencia de la cara.
    cons = payload.get("consistency_refs") or []
    cons_b64s = [
        _compress_ref(base64.b64decode(_strip_data_url(c)), max_dim=1344, q=90)
        for c in cons[:8] if c
    ]
    n_cons = len(cons_b64s)

    # Motor: "gemini" (Nano Banana) o "flux" (fal.ai — sin filtros de Google, ideal lencería).
    # El set de 3 modelos (trio) por ahora sigue siempre en Gemini.
    _engine_req = str(payload.get("engine") or settings.get("engine") or "gemini").strip().lower()
    _auto = _engine_req == "auto"
    _fal_key = bool(str(settings.get("fal_api_key") or FAL_API_KEY).strip())
    # MODO AUTOMÁTICO: arranca SIEMPRE en Gemini (Nano Banana = máxima calidad). Solo si Gemini
    # BLOQUEA una toma de lencería/trío, se rescata esa misma toma en FLUX (permisivo). Así se
    # tiene la calidad de Gemini casi siempre y nunca se queda trabado por un bloqueo.
    engine = "gemini" if _auto else _engine_req
    use_flux = engine in ("flux", "fal") and mode in ("on_model", "product_only", "recolor", "trio")
    # Rescate a FLUX ante bloqueo de Gemini (solo en auto, con key fal, y en lencería/trío):
    _flux_on_block = (_auto and _fal_key and mode in ("on_model", "trio")
                      and ((_categoria(params) == "lenceria") or mode == "trio"))
    flux_slug: Optional[str] = None

    # Presupuesto
    est = float(settings.get("precio_flux", 0.05)) if use_flux else precios[image_size]
    ok, motivo, total, cap = await budget_check(est)
    if not ok:
        raise HTTPException(402, motivo)

    # Armado de parts segun modo
    con_avatar = False
    av = None
    fp = None
    flux_parts = None      # parts para FLUX (se arma si use_flux o si hay rescate por bloqueo)
    flux_slug = None
    if mode == "on_model":
        avatar_id = payload.get("avatar_id")
        con_avatar = bool(avatar_id) and str(avatar_id).lower() not in ("none", "null", "")
        av = await get_avatar(avatar_id) if con_avatar else None
        if con_avatar and (not av or not av.get("ref_b64")):
            raise HTTPException(400, "Elegí un avatar válido y lockeado.")
        fp = payload.get("force_pose")
        fp = int(fp) if fp is not None else None
        # Género: con avatar sale del avatar; sin avatar, del pedido (default mujer).
        genero = (av.get("gender") if con_avatar and av
                  else str(params.get("genero", "") or payload.get("genero", "")).strip())
        prompt = build_prompt_on_model(params, settings, paneles, aspect, style, n_prod,
                                       int(payload.get("pose_offset", 0)), force_pose=fp,
                                       con_avatar=con_avatar, genero=genero)
        _cons_txt = _bloque_consistencia(n_cons).strip()
        if _cons_txt and any(str(params.get(k, "")).strip() for k in
                             ("cuerpo_contextura", "cuerpo_busto", "cuerpo_cola",
                              "cuerpo_abdomen")):
            _cons_txt += ("\n• CUERPO: la modelo de esta sesión ES del tipo de cuerpo "
                          "descripto en el bloque TIPO DE CUERPO de más abajo; mantené esa "
                          "misma contextura en todas las tomas del set.")
        if _cons_txt:
            prompt = _cons_txt + "\n\n" + prompt   # al TOPE: enterrada al final se ignoraba
        # Cada imagen viaja con un RÓTULO de texto justo antes: sin rótulos Gemini tenía
        # que contar posiciones ("las últimas N") y desde la toma 2 confundía qué imagen
        # era la prenda real y cuál el ancla → inventaba otra estampa o cambiaba la cara.
        parts = [{"text": prompt}]
        _idx = 1
        if con_avatar:
            parts.append({"text": f"IMAGEN {_idx} — AVATAR (solo identidad: cara, pelo y "
                                  "físico; su ropa, pose y fondo NO existen en esta toma):"})
            parts.append(_img_part(av["ref_b64"]))
            _idx += 1
        for _j, _b in enumerate(prod_b64s):
            parts.append({"text": f"IMAGEN {_idx} — FOTO REAL DEL PRODUCTO (vista {_j + 1} "
                                  f"de {n_prod}; ÚNICA verdad de la prenda: copiá EXACTOS "
                                  "diseño, estampa, color y terminaciones):"})
            parts.append(_img_part(_b))
            _idx += 1
        for _b in cons_b64s:
            parts.append({"text": f"IMAGEN {_idx} — TOMA PREVIA DEL SET (solo para mantener "
                                  "a la MISMA persona; NO es referencia de prenda, pose ni "
                                  "encuadre — la prenda sale de las FOTOS REALES DEL "
                                  "PRODUCTO):"})
            parts.append(_img_part(_b))
            _idx += 1
        quien = av.get("name") if con_avatar else "modelo IA (sin avatar)"
        note = f"on_model · {quien} · {n_prod} fotos prod"
        if use_flux or _flux_on_block:
            # FLUX: prompt corto + persona primera (avatar o ancla) y después la(s) prenda(s).
            _pose_txt = str(params.get("pose", "")).strip()
            if _pose_txt:
                # pose escrita en castellano → se le suma la traducción por palabras clave
                _pose_txt += _pose_en_hints(_pose_txt)
            else:
                # pose del pool → versión en INGLÉS (Seedream ignoraba el castellano)
                idx = fp if fp is not None else int(payload.get("pose_offset", 0))
                _pose_txt = POSE_POOL_FLUX[idx % len(POSE_POOL_FLUX)]
            persona_b64 = (av["ref_b64"] if con_avatar
                           else (cons_b64s[0] if cons_b64s else None))
            _fprompt = build_prompt_flux(params, _pose_txt, con_persona=bool(persona_b64),
                                         n_prod=n_prod, genero=genero,
                                         estilo=_style_text(style, settings))
            # OJO: a Seedream NO se le manda el ancla de tomas previas. Es un modelo de
            # EDICIÓN: cuando ve una foto ya lista, copia su composición ENTERA (pose,
            # encuadre, escena) aunque el texto pida otra pose — así salían todas las
            # tomas del set idénticas a la primera. La identidad viaja en la foto del
            # avatar; la pose la define solo el prompt.
            _fprompt += ("\nPOSE RULE: the MANDATORY POSE above is the ONLY pose. Do NOT "
                         "copy the pose, framing or composition from any reference image.")
            flux_parts = [{"text": _fprompt}]
            if persona_b64:
                flux_parts.append(_img_part(persona_b64))
            _nprods_flux = 5
            flux_parts += [_img_part(b) for b in prod_b64s[:_nprods_flux]]
            flux_slug = str((settings.get("flux_tryon_model") if persona_b64
                             else settings.get("flux_edit_model")) or "bytedance/seedream/v5/pro/edit")
            if use_flux:
                parts = flux_parts
                prompt = _fprompt
                note = "FLUX · " + note
    elif mode == "product_only":
        modo_p = payload.get("modo_producto", "flat_lay")
        prompt = build_prompt_product_only(params, settings, modo_p, paneles, aspect, n_prod)
        prompt += _bloque_consistencia(n_cons)
        parts = [{"text": prompt}]
        for _j, _b in enumerate(prod_b64s):
            parts.append({"text": f"IMAGEN {_j + 1} — FOTO REAL DEL PRODUCTO (vista "
                                  f"{_j + 1} de {n_prod}; ÚNICA verdad de la prenda: copiá "
                                  "EXACTOS diseño, estampa, color y terminaciones):"})
            parts.append(_img_part(_b))
        for _j, _b in enumerate(cons_b64s):
            parts.append({"text": f"IMAGEN {n_prod + _j + 1} — TOMA PREVIA DEL SET (solo "
                                  "referencia de continuidad; la prenda sale de las FOTOS "
                                  "REALES DEL PRODUCTO):"})
            parts.append(_img_part(_b))
        note = f"product_only · {modo_p} · {n_prod} fotos prod"
        if use_flux:
            if n_prod > 4:
                prod_b64s = prod_b64s[:4]
                n_prod = 4
            prompt = build_prompt_product_only(params, settings, modo_p, paneles, aspect,
                                               n_prod)
            parts = [{"text": prompt}] + [_img_part(b) for b in prod_b64s]
            flux_slug = str(settings.get("flux_edit_model") or "bytedance/seedream/v5/pro/edit")
            note = "FLUX · " + note
    elif mode == "trio":
        asign = payload.get("asign") or []
        colores = payload.get("colores") or []
        if isinstance(colores, str):
            colores = [c for c in colores.replace(";", ",").split(",") if c.strip()]
        if not asign and colores:
            asign = [{"nombre": "", "color": c} for c in colores[:3]]
        asign = (asign or [])[:3]
        while len(asign) < 3:
            asign.append({"nombre": "", "color": (asign[-1]["color"] if asign else "blanco")})
        # La grupal va PRIMERA: usa las caras de los avatares como identidad.
        av_parts, img_map, nxt = [], [None, None, None], 1
        full_refs = False
        for k in range(3):
            aid = asign[k].get("avatar_id")
            aref = await get_avatar_ref(aid) if aid else None
            if aref:
                av_parts.append({"text": f"IMAGEN {nxt} — CARA DE LA MODELO {'ABC'[k]} "
                                         "(solo identidad: cara y rasgos; su ropa NO "
                                         "existe en esta toma):"})
                av_parts.append(_img_part(aref))
                img_map[k] = nxt
                nxt += 1
        prod_primera = nxt
        prompt = build_prompt_trio(params, settings, asign, aspect, style, n_prod,
                                   img_map=img_map, prod_primera=prod_primera,
                                   full_refs=full_refs)
        parts = [{"text": prompt}] + av_parts
        for _j, _b in enumerate(prod_b64s):
            parts.append({"text": f"IMAGEN {prod_primera + _j} — FOTO REAL DEL PRODUCTO "
                                  f"(vista {_j + 1} de {n_prod}; ÚNICA verdad de la "
                                  "prenda: copiá EXACTOS diseño, estampa, color y "
                                  "terminaciones):"})
            parts.append(_img_part(_b))
        note = "trio · " + ", ".join(f"{i.get('color', '')}" for i in asign)
        if use_flux or _flux_on_block:
            estilo_s = _style_text(style, settings)
            quien = (f"las TRES mujeres de las primeras {len(av_parts)} imágenes (respetá "
                     "sus caras y rasgos exactos)" if av_parts
                     else "tres mujeres adultas distintas (no gemelas)")
            cols = ", ".join(str(i.get("color", "")) for i in asign)
            _cat3 = _categoria(params)
            lenc = (_LENCERIA_FLUX + "\n\n") if _cat3 == "lenceria" else ""
            dir3 = _bloque_categoria(_cat3, None)
            _fprompt3 = (estilo_s + "\n\n" + lenc + "Foto de campaña con TRES modelos juntas: " + quien
                         + ". Las tres llevan la MISMA prenda de la ÚLTIMA imagen — copiala "
                         "EXACTA: mismo diseño, calce y terminaciones — cada una en su color: "
                         + cols + ". Poses naturales, espontáneas y distintas entre sí, "
                         "encuadre de la cadera para arriba, las tres bien visibles. "
                         "Proporciones humanas correctas, piel natural con detalle sutil, luz "
                         "suave y pareja. PROHIBIDO: que sean gemelas; logos, marcas de agua o "
                         "texto; accesorios no pedidos. Exactamente TRES mujeres."
                         + (("\n\n" + dir3) if dir3 else "")
                         + ((f"\nMANDATORY SETTING (top priority): {str(params.get('fondo','')).strip()}. "
                             "Put them in that exact environment.")
                            if str(params.get('fondo','')).strip() else "")
                         + ((f"\nLighting: {str(params.get('luz','')).strip()}.")
                            if str(params.get('luz','')).strip() else "")
                         + ((f"\nMANDATORY DIRECTION from the user (follow it exactly): "
                             f"{str(params.get('aclaraciones','')).strip()}")
                            if str(params.get('aclaraciones','')).strip() else ""))
            _specs3 = "\n".join(_modelo_spec(asign[k], "ABC"[k], img_map[k]) for k in range(3))
            _fprompt3 += ("\nDEFINICIÓN DE CADA MODELO (cuerpo y cara, respetala tal cual):\n"
                          + _specs3)
            flux_parts = [{"text": _fprompt3}] + av_parts + [_img_part(prod_b64s[0])]
            flux_slug = str(settings.get("flux_edit_model") or "bytedance/seedream/v5/pro/edit")
            if use_flux:
                parts = flux_parts
                prompt = _fprompt3
                note = "FLUX · " + note
    elif mode == "recolor":
        modo_p = payload.get("modo_producto", "suspendida")
        target_color = (payload.get("target_color") or "").strip()
        if not target_color:
            raise HTTPException(400, "Falta el color destino (target_color).")
        prompt = build_prompt_recolor(params, settings, modo_p, target_color, aspect, n_prod)
        parts = [{"text": prompt}] + [_img_part(b) for b in prod_b64s]
        note = f"recolor · {target_color} · {modo_p}"
        if use_flux:
            flux_slug = str(settings.get("flux_edit_model") or "bytedance/seedream/v5/pro/edit")
            note = "FLUX · " + note
    else:
        raise HTTPException(400, "mode debe ser on_model, product_only, trio o recolor.")

    # BOCETO APROBADO: si viene un borrador ya aprobado, la final lo reproduce en alta.
    dref = str(payload.get("draft_ref") or "").strip()
    if dref:
        try:
            dref_b64 = _compress_ref(base64.b64decode(_strip_data_url(dref)),
                                     max_dim=1536, q=92)
            for _i, _pt in enumerate(parts):
                if _pt.get("text"):
                    parts[_i] = {"text": _pt["text"] +
                                 "\n\nBOCETO APROBADO: la ÚLTIMA imagen es el boceto YA "
                                 "APROBADO de esta toma. Reproducilo EXACTO en alta "
                                 "resolución: misma composición, misma pose, misma modelo y "
                                 "misma prenda, con la prenda fiel a las fotos reales del "
                                 "producto. No cambies nada del boceto, solo mejorá el "
                                 "detalle y la nitidez."}
                    break
            parts.append(_img_part(dref_b64))
        except Exception:
            pass

    # TU PROMPT MANDA: si escribiste/editaste el texto, se envía EXACTAMENTE ese.
    override = str(payload.get("prompt_override", "") or "").strip()
    if override:
        prompt = override
        for _i, _pt in enumerate(parts):
            if _pt.get("text"):
                parts[_i] = {"text": override}
                break
        else:
            parts = [{"text": override}] + parts
        note += " · prompt editado"

    # Vista previa: devuelve el prompt SIN generar (no gasta nada).
    if payload.get("solo_prompt"):
        return {"solo_prompt": True, "prompt": prompt,
                "n_imagenes": sum(1 for _pt in parts
                                  if _pt.get("inlineData") or _pt.get("inline_data")),
                "modelo": (f"FLUX ({flux_slug})" if use_flux
                           else str(settings.get("model") or MODEL_ID))}

    # Generación (1 sola llamada = 1 cobro), aunque salgan N paneles
    _vino_de_fal = False
    if use_flux and flux_slug:
        img_bytes = await fal_generate(parts, settings, aspect, image_size, flux_slug)
        paneles = 1     # FLUX genera una imagen por llamada (sin trucos de paneles)
        _vino_de_fal = True
    else:
      try:
        img_bytes = await gemini_generate(parts, settings, aspect, image_size)
      except HTTPException as ge:
        blocked = getattr(ge, "status_code", 0) == 422
        # RESCATE A FLUX: si Gemini bloqueó una toma de lencería/trío (modo auto), se
        # reintenta esa MISMA toma en FLUX (permisivo) en vez de darla por perdida.
        if blocked and _flux_on_block and flux_parts and flux_slug:
            img_bytes = await fal_generate(flux_parts, settings, aspect, image_size, flux_slug)
            paneles = 1
            _vino_de_fal = True
            note += " · rescate-FLUX (Gemini bloqueó)"
        # BLOQUEO CON ANCLAS: Google a veces bloquea (motivo OTHER) las tomas que llevan
        # tomas previas + texto de consistencia (lo lee como "modificar a una persona").
        # Se reintenta la MISMA toma SIN anclas ni bloque de consistencia: sale con el
        # avatar y el producto igual que la toma 1 (que pasó), perdiendo solo el
        # encadenamiento de ESA toma. Mejor una toma buena sin ancla que una bloqueada.
        elif blocked and mode == "on_model" and con_avatar and n_cons > 0:
            prompt_na = build_prompt_on_model(params, settings, paneles, aspect, style,
                                              n_prod, int(payload.get("pose_offset", 0)),
                                              force_pose=fp, con_avatar=con_avatar,
                                              genero=genero)
            parts_na = [{"text": prompt_na}]
            _idx2 = 1
            if con_avatar:
                parts_na.append({"text": f"IMAGEN {_idx2} — AVATAR (solo identidad: cara, "
                                         "pelo y físico; su ropa, pose y fondo NO existen "
                                         "en esta toma):"})
                parts_na.append(_img_part(av["ref_b64"]))
                _idx2 += 1
            for _j2, _b2 in enumerate(prod_b64s):
                parts_na.append({"text": f"IMAGEN {_idx2} — FOTO REAL DEL PRODUCTO (vista "
                                         f"{_j2 + 1} de {n_prod}; ÚNICA verdad de la "
                                         "prenda: copiá EXACTOS diseño, estampa, color y "
                                         "terminaciones):"})
                parts_na.append(_img_part(_b2))
                _idx2 += 1
            img_bytes = await gemini_generate(parts_na, settings, aspect, image_size)
            parts = parts_na    # el inspector corrige sobre este prompt si hace falta
            note += " · reintento-sin-ancla (bloqueo)"
        # AVATAR SAGRADO: si una toma CON avatar se bloquea, queda bloqueada. NUNCA se
        # recrea la cara ni el cuerpo de la modelo (el avatar es la cara de la marca).
        elif blocked and mode == "on_model" and not con_avatar:
            # Individual SIN avatar (modelo IA) que bloqueó: reintento con bombacha +
            # encuadre editorial seguro (menos piel), antes de darla por bloqueada.
            params3 = dict(params)
            params3["complemento"] = "si"
            prev_acl = str(params3.get("aclaraciones", "")).strip()
            safe_note = ("ENCUADRE EDITORIAL SEGURO: catálogo de moda profesional; "
                         "plano de la cadera para arriba; bombacha lisa de talle clásico que "
                         "combina (siempre con la parte de abajo puesta); estética comercial "
                         "limpia y prolija.")
            params3["aclaraciones"] = (prev_acl + " " if prev_acl else "") + safe_note
            prompt3 = build_prompt_on_model(params3, settings, paneles, aspect, style, n_prod,
                                            int(payload.get("pose_offset", 0)), force_pose=fp,
                                            con_avatar=False,
                                            genero=str(params.get("genero", "")
                                                       or payload.get("genero", "")).strip())
            prompt3 += _bloque_consistencia(n_cons)
            parts3 = [{"text": prompt3}]
            parts3 += [_img_part(b) for b in prod_b64s]
            parts3 += [_img_part(b) for b in cons_b64s]
            img_bytes = await gemini_generate(parts3, settings, aspect, image_size)
            note += " · reintento-seguro"
        elif blocked and mode == "trio":
            # La grupal se bloqueó: reintentamos UNA vez con encuadre editorial seguro.
            params_s = {**params, "complemento": "si"}
            prompt_s = build_prompt_trio(params_s, settings, asign, aspect, style, n_prod,
                                         img_map=img_map, prod_primera=prod_primera,
                                         seguro=True, full_refs=full_refs)
            parts_s = [{"text": prompt_s}] + av_parts + [_img_part(b) for b in prod_b64s]
            img_bytes = await gemini_generate(parts_s, settings, aspect, image_size)
            note += " · reintento-seguro"
        else:
            raise

    # ACABADO 4K: la imagen de Seedream se rehace en 4K con Nano Banana (retocar una
    # imagen existente NO dispara su filtro). Si igual bloqueara, queda la de Seedream.
    if (_vino_de_fal and str(settings.get("seedream_final_4k", "si")).lower()
            in ("si", "sí", "1", "on", "true")):
        try:
            up = ("Recreá esta MISMA imagen en 4K con máxima calidad y nitidez: idéntica "
                  "composición, mismas personas con las mismas caras y poses, misma prenda "
                  "con su mismo diseño y color, mismo fondo y luz. Mejorá SOLO el detalle "
                  "fino y la definición. No cambies absolutamente nada más.")
            up_parts = [{"text": up},
                        _img_part(_compress_ref(img_bytes, max_dim=2048, q=92))]
            img_bytes = await gemini_generate(up_parts, settings, aspect, "4K",
                                              save_prompt=False)
            est += _pricing(settings)["4K"]
            note += " · final-4K-nano"
        except Exception:
            pass    # queda la imagen de Seedream tal cual

    # CONTROL DE FIDELIDAD: inspector automático + auto-corrección (1 reintento).
    # Compara la prenda generada contra las fotos reales; si detecta errores (bolsillos
    # inventados, estampa cambiada, color distinto), regenera UNA vez con las correcciones.
    qc: Optional[Dict[str, Any]] = None
    gen_cost = est
    _qc_on = (str(settings.get("qc_prenda", "si")).lower() in ("si", "sí", "1", "on", "true")
              and not payload.get("qc_off"))
    if _qc_on and mode in ("on_model", "trio", "product_only", "recolor"):
        qc = await verificar_prenda(img_bytes, prod_b64s, back_qc_b64s)
        umbral = int(settings.get("qc_umbral", 7) or 7)
        _retry_on = str(settings.get("qc_reintento", "si")).lower() in ("si", "sí", "1",
                                                                        "on", "true")
        _rondas = 0
        while (qc and _retry_on and int(qc.get("puntaje", 10)) < umbral
               and qc.get("diferencias") and _rondas < 2):
            _rondas += 1
            corr = ("\n\nCORRECCIONES OBLIGATORIAS: una inspección comparó tu imagen anterior "
                    "con la foto real del producto y encontró estos errores en la prenda. "
                    "Corregilos TODOS en esta nueva imagen:\n• " + "\n• ".join(qc["diferencias"]))
            for _i, _pt in enumerate(parts):
                if _pt.get("text"):
                    parts[_i] = {"text": _pt["text"] + corr}
                    break
            try:
                if use_flux and flux_slug:
                    img2 = await fal_generate(parts, settings, aspect, image_size, flux_slug)
                else:
                    img2 = await gemini_generate(parts, settings, aspect, image_size)
                gen_cost += est
                qc2 = await verificar_prenda(img2, prod_b64s, back_qc_b64s)
                if qc2 is None or int(qc2.get("puntaje", 0)) >= int(qc.get("puntaje", 0)):
                    img_bytes = img2
                    qc = qc2 or qc
            except Exception:
                break   # el reintento nunca rompe: queda la mejor imagen lograda
        if _rondas:
            qc = dict(qc or {})
            qc["reintentada"] = True
            note += f" · QC-corregida x{_rondas}"
        if (qc and int(qc.get("puntaje", 10)) < umbral
                and str(settings.get("qc_estricto", "si")).lower()
                in ("si", "sí", "1", "on", "true")):
            qc = dict(qc)
            qc["rechazada"] = True    # la imagen viaja marcada DESCARTADA, no como buena

    rec = await budget_record(mode, image_size, gen_cost, paneles, note=note)

    # Split + salida
    panels = split_panels(img_bytes, paneles, reframe_to=reframe)
    assets = [to_outputs(p, settings) for p in panels]

    # Guardado en Google Drive EN SEGUNDO PLANO (PNG máxima calidad), sin bloquear la respuesta
    drive_pending = False
    _usub = payload.get("user_sub") or CURRENT_SUB.get()
    if payload.get("save_to_drive", True) and await _drive_connected_for(_usub):
        task = asyncio.create_task(_save_panels_to_drive(panels, mode, user_sub=_usub))
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)
        drive_pending = True

    return {
        "ok": True,
        "assets": assets,
        "panels_detected": len(panels),
        "panels_requested": paneles,
        "cost": rec["cost"],
        "cost_per_asset": rec["cost_per_asset"],
        "month_total": round(total + gen_cost, 4),
        "cap": cap,
        "drive_pending": drive_pending,
        "drive_saved": False,
        "qc": qc,
        "motor": ("fal" if (use_flux and flux_slug) or " FLUX" in (" " + note) else "gemini"),
        "descartada": bool(qc and qc.get("rechazada")),
    }


# ── Jobs persistidos en Redis: sobreviven refresco/cierre, set en server, resumible ──
JOB_TTL = 7200       # estado/contexto del job: 2 h
RES_TTL = 3600       # resultados optimizados visibles al reenganchar: 1 h
PNG_TTL = 1800       # PNG master (pesado) en Redis: 30 min, y se borra al leerse
_LIVE: set = set()   # job_ids corriendo en ESTE proceso (para detectar huérfanos)


def _split_group_to_anchors(res: Dict[str, Any], n: int = 3) -> List[str]:
    """Parte la foto grupal en N recortes verticales (uno por modelo). Cada individual usa
    su recorte como referencia: así la cara y el cuerpo calzan con la campaña."""
    try:
        from PIL import Image as _PILImage
    except Exception:
        return []
    dataurl = ""
    for a in (res.get("assets") or []):
        if a.get("optimized"):
            dataurl = a["optimized"]
            break
    if not dataurl:
        return []
    try:
        raw = base64.b64decode(_strip_data_url(dataurl))
        im = _PILImage.open(io.BytesIO(raw)).convert("RGB")
        w, h = im.size
        paso = w / float(n)
        out = []
        for k in range(n):
            x0 = max(0, int(paso * k - paso * 0.10))
            x1 = min(w, int(paso * (k + 1) + paso * 0.10))
            buf = io.BytesIO()
            im.crop((x0, 0, x1, h)).save(buf, "JPEG", quality=88)
            out.append("data:image/jpeg;base64," +
                       base64.b64encode(buf.getvalue()).decode("ascii"))
        return out
    except Exception:
        return []


def _dataurl_to_anchor(dataurl: str, max_dim: int = 1536, q: int = 92) -> str:
    raw = base64.b64decode(_strip_data_url(dataurl))
    return "data:image/jpeg;base64," + _compress_ref(raw, max_dim=max_dim, q=q)


_REFS_HD = {"max_dim": 3200, "q": 98}     # casi sin degradar (el límite real es 512 MB)
_REFS_NORMAL = {"max_dim": 1536, "q": 88}


def _shrink_products(prods: Optional[List[str]], max_dim: int = 3200, q: int = 98) -> List[str]:
    """Prepara las fotos del producto para guardarlas. Por defecto casi no las degrada
    (2560px, calidad 96) para que lleguen al motor lo más parecidas posible al original,
    igual que cuando se suben a mano en el Playground."""
    out: List[str] = []
    for p in (prods or []):
        if not p:
            continue
        try:
            out.append("data:image/jpeg;base64," +
                       _compress_ref(base64.b64decode(_strip_data_url(p)), max_dim=max_dim, q=q))
        except Exception:
            out.append(p)
    return out


async def _job_in_save(jid: str, base: Dict[str, Any]) -> bool:
    """Guarda los inputs pesados (fotos) UNA sola vez. Devuelve si pudo guardar."""
    return await kv.set(f"imagenes:jobin:{jid}", base, ttl=JOB_TTL)


async def _job_in_get(jid: str) -> Optional[Dict[str, Any]]:
    return await kv.get(f"imagenes:jobin:{jid}")


async def _job_state_save(state: Dict[str, Any]) -> None:
    state["ts"] = time.time()
    if "owner" not in state:
        state["owner"] = CURRENT_SUB.get()
    await kv.set(f"imagenes:job:{state['id']}", state, ttl=JOB_TTL)
    await kv.set(_pfx() + "job:current", {"id": state["id"]}, ttl=JOB_TTL)


async def _job_state_get(jid: str) -> Optional[Dict[str, Any]]:
    return await kv.get(f"imagenes:job:{jid}")


async def _job_owned(jid: str) -> Optional[Dict[str, Any]]:
    """Devuelve el job SOLO si pertenece al usuario actual. Si no, corta."""
    st = await _job_state_get(jid)
    if not st:
        return None
    me = CURRENT_SUB.get()
    owner = st.get("owner")
    if owner != me:
        raise HTTPException(403, "Ese trabajo no es tuyo.")
    return st


async def _job_ctx_save(jid: str, ctx: Dict[str, Any]) -> None:
    await kv.set(f"imagenes:jobctx:{jid}", ctx, ttl=JOB_TTL)


async def _job_ctx_get(jid: str) -> Optional[Dict[str, Any]]:
    return await kv.get(f"imagenes:jobctx:{jid}")


async def _job_store_result(jid: str, idx: int, res: Dict[str, Any]) -> None:
    assets = res.get("assets") or []
    opt = {k: res[k] for k in ("cost", "cost_per_asset", "month_total", "cap",
                               "drive_pending", "drive_saved", "panels_detected",
                               "panels_requested", "qc", "descartada") if k in res}
    opt["assets"] = [{"optimized": a.get("optimized")} for a in assets]
    await kv.set(f"imagenes:jobopt:{jid}:{idx}", opt, ttl=RES_TTL)
    await kv.set(f"imagenes:jobpng:{jid}:{idx}", {"png": [a.get("png") for a in assets]},
                 ttl=PNG_TTL)


async def _gen_con_borrador(jid: str, idx: int, payload: Dict[str, Any],
                            settings: Dict[str, Any]) -> Dict[str, Any]:
    """Flujo BORRADOR: genera barato (1K), lo inspecciona y recién ahí decide si gastar
    en la final. modo 'auto' = corta solo si no pasa la nota; 'preguntar' = pausa el
    trabajo y espera que la usuaria apruebe/rehaga/saltee desde la pantalla."""
    modo = str(settings.get("borrador", "no")).strip().lower()
    if modo not in ("auto", "preguntar") or payload.get("mode") not in ("on_model", "trio"):
        return await _do_generate(payload)
    tam_final = payload.get("image_size") or settings.get("image_size", "4K")
    tam_draft = str(settings.get("borrador_size", "1K"))
    if tam_draft == tam_final:
        return await _do_generate(payload)   # sin ahorro posible: directo
    for _intento in range(3):                # hasta 3 "rehacer" pedidos por la usuaria
        pd = dict(payload)
        pd["image_size"] = tam_draft
        pd["save_to_drive"] = False
        draft = await _do_generate(pd)
        qc = draft.get("qc")
        assets = draft.get("assets") or []
        draft_url = (assets[0].get("optimized") or assets[0].get("png") or "") if assets else ""
        umbral = int(settings.get("qc_umbral", 7) or 7)
        pasa = (qc is None) or (int(qc.get("puntaje", 10)) >= umbral)
        if modo == "auto":
            if not pasa:
                draft["descartada"] = True   # no se gasta la final; queda el borrador y su nota
                return draft
            decision = "ok"
        else:
            # PREGUNTAR: publico el borrador y espero la decisión de la usuaria
            await kv.set(f"imagenes:jobpreview:{jid}:{idx}",
                         {"preview": draft_url, "qc": qc}, ttl=1800)
            st = await _job_state_get(jid) or {"id": jid}
            st["status"] = "waiting_ok"
            st["wait_idx"] = idx
            await _job_state_save(st)
            decision = None
            for _ in range(600):             # espera hasta 20 min (2 s por vuelta)
                await asyncio.sleep(2)
                ctx = await _job_ctx_get(jid) or {}
                d = (ctx.get("decisiones") or {}).get(str(idx))
                if d:
                    decision = str(d)
                    ctx["decisiones"].pop(str(idx), None)
                    await _job_ctx_save(jid, ctx)
                    break
                if ctx.get("stop"):
                    decision = "saltear"
                    break
            st = await _job_state_get(jid) or st
            st["status"] = "running"
            st.pop("wait_idx", None)
            await _job_state_save(st)
        if decision in (None, "saltear", "no"):
            draft["descartada"] = True
            return draft
        if decision == "rehacer":
            # El próximo borrador arranca CON las correcciones que el inspector ya encontró
            if qc and qc.get("diferencias"):
                pp = dict(payload.get("params") or {})
                prev = str(pp.get("aclaraciones", "")).strip()
                corr = ("CORRECCIONES DE LA INSPECCIÓN DEL BORRADOR ANTERIOR (obligatorias, "
                        "corregilas TODAS): • " + " • ".join(qc["diferencias"]))
                pp["aclaraciones"] = ((prev + " ") if prev else "") + corr
                payload = dict(payload)
                payload["params"] = pp
            continue
        # aprobada → FINAL en alta, anclada al boceto (misma toma, más detalle)
        pf = dict(payload)
        pf["draft_ref"] = draft_url
        pf["qc_off"] = True                  # ya está aprobada: no re-inspeccionar
        final = await _do_generate(pf)
        if qc:
            final["qc"] = qc                 # la nota del borrador acompaña a la final
        return final
    draft["descartada"] = True               # 3 rehacer sin aprobar: queda descartada
    return draft


@router.get(ROUTE_PREFIX + "/api/jobs/{jid}/preview/{idx}")
async def api_job_preview(jid: str, idx: int) -> Dict[str, Any]:
    if not await _job_owned(jid):
        return {"status": "unknown"}
    return (await kv.get(f"imagenes:jobpreview:{jid}:{idx}")) or {"status": "pending"}


@router.post(ROUTE_PREFIX + "/api/jobs/{jid}/decide")
async def api_job_decide(jid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """La usuaria decide sobre el borrador: ok | rehacer | saltear."""
    if not await _job_owned(jid):
        raise HTTPException(404, "Ese trabajo no existe o no es tuyo.")
    idx = int(payload.get("idx", 0))
    decision = str(payload.get("decision", "ok")).strip().lower()
    if decision not in ("ok", "rehacer", "saltear"):
        raise HTTPException(400, "decision debe ser ok, rehacer o saltear.")
    ctx = await _job_ctx_get(jid) or {}
    dec = ctx.get("decisiones") or {}
    dec[str(idx)] = decision
    ctx["decisiones"] = dec
    await _job_ctx_save(jid, ctx)
    return {"ok": True, "decision": decision}


# Plan del set: pasos (modo) según calidad. El paso 0 fija la consistencia.
def _set_plan(hq: bool) -> List[Dict[str, Any]]:
    if hq:
        steps = [{"mode": "on_model", "aspect": "4:5", "paneles": 1, "force_pose": k}
                 for k in range(4)]
        for s in steps:
            if s.get("force_pose") == 3:      # POSE_POOL[3] = DE ESPALDA
                s["use_back"] = True
        steps.append({"mode": "product_only", "aspect": "4:5", "paneles": 1,
                      "modo_producto": "suspendida"})
    else:
        # OJO: la toma de ESPALDA usa la foto de espalda del producto, así que NO puede
        # ir agrupada en el mismo panel con una pose de frente (le arruinaba la toma 3).
        steps = [
            {"mode": "on_model", "aspect": "21:9", "paneles": 2, "pose_offset": 0},
            {"mode": "on_model", "aspect": "4:5", "paneles": 1, "force_pose": 2},
            {"mode": "on_model", "aspect": "4:5", "paneles": 1, "force_pose": 3,
             "use_back": True},
            {"mode": "product_only", "aspect": "4:5", "paneles": 1,
             "modo_producto": "suspendida"},
        ]
    return steps


_POSE_EXTRA = {"frente": 0, "perfil": 6, "espalda": 3, "sentada": 2, "caminando": 4}
# Poses variadas y DISTINTAS para cada modelo del set (evita que las 3 salgan iguales).
_IND_ROT = [0, 6, 2, 1, 4, 8]


def _anchors_para_envio(lst: Optional[List[Any]], cap: int = 8) -> Optional[List[str]]:
    """Ordena las anclas acumuladas para el envío: primero las tomas CON CARA (protegen
    la identidad), después las demás (espalda/detalle). Acepta el formato viejo (str)."""
    if not lst:
        return None
    caras = [a for a in lst if not isinstance(a, dict) or a.get("cara")]
    otras = [a for a in lst if isinstance(a, dict) and not a.get("cara")]
    out = [(a["u"] if isinstance(a, dict) else a) for a in caras + otras]
    return out[:cap] or None


def _mk_ind_step(it: Dict[str, Any], k: int) -> Dict[str, Any]:
    """Arma la toma individual de la modelo k con TODAS sus características y su pose.
    Si la usuaria no eligió una pose puntual, cada modelo recibe una pose DISTINTA
    (rotación) para que el set no repita la misma toma tres veces."""
    pose_raw = str(it.get("pose", "")).strip().lower()
    default_pose = _IND_ROT[k % len(_IND_ROT)]
    pose = _POSE_EXTRA.get(pose_raw, default_pose) if pose_raw else default_pose
    st = {"mode": "on_model", "aspect": "4:5", "paneles": 1, "force_pose": pose,
          "color_set": it.get("color", ""), "avatar_id": it.get("avatar_id"),
          "modelo_idx": k, "no_face_recreate": bool(it.get("avatar_id")),
          "indicacion": str(it.get("indicacion", "")).strip(),
          "modelo_spec": {
              "cuerpo_contextura": it.get("contextura", ""),
              "cuerpo_edad": it.get("edad", ""),
              "cuerpo_busto": it.get("busto", ""),
              "cuerpo_cola": it.get("cola", ""),
              "cuerpo_abdomen": it.get("abdomen", ""),
              "cuerpo_peinado": it.get("peinado", ""),
              "cuerpo_altura": it.get("altura", ""),
              "ap_etnia": it.get("etnia", ""),
              "ap_pelo": it.get("pelo", ""),
          }}
    if pose == 3:
        st["use_back"] = True
    return st


def _set_plan_trio(asign: List[Dict[str, str]], colores: List[str],
                   modo_producto: str = "suspendida",
                   extras: Optional[List[Dict[str, Any]]] = None,
                   inc_grupal: bool = True, inc_ind: bool = True,
                   inc_prod: bool = True,
                   grupal_indicacion: str = "") -> List[Dict[str, Any]]:
    """Set de lencería COMPONIBLE. Orden: PRIMERO la GRUPAL (define cómo son las 3 modelos),
    DESPUÉS las individuales, cada una encadenada a su recorte de la grupal. El avatar
    nunca se recrea."""
    if not asign and colores:
        asign = [{"color": c.strip()} for c in colores if c.strip()][:3]
    a = (asign or [])[:3]
    steps: List[Dict[str, Any]] = []
    # La GRUPAL solo tiene sentido con las 3 modelos: con 1 o 2 se salta (antes el modo
    # trío rellenaba hasta 3 e inventaba modelos — por eso el set 'exigía' pack x3).
    if inc_grupal and len(a) >= 3:
        steps.append({"mode": "trio", "aspect": "4:5", "paneles": 1, "asign": a,
                      "indicacion": grupal_indicacion, "critical": True})
    if inc_ind:
        for k, it in enumerate(a):
            steps.append(_mk_ind_step(it, k))
    for ex in (extras or []):
        quien = str(ex.get("quien", "")).lower()
        if quien in ("grupal", "3", "todas"):
            if len(a) >= 3:
                steps.append({"mode": "trio", "aspect": "4:5", "paneles": 1, "asign": a,
                              "indicacion": str(ex.get("indicacion", "")).strip()})
            continue
        try:
            k = int(quien.replace("modelo", "").strip()) - 1
        except Exception:
            continue
        if 0 <= k < len(a):
            it2 = dict(a[k])
            it2["pose"] = ex.get("pose", "frente")
            it2["indicacion"] = ex.get("indicacion", "")
            steps.append(_mk_ind_step(it2, k))
    if inc_prod:
        steps.append({"mode": "product_only", "aspect": "4:5", "paneles": 1,
                      "modo_producto": modo_producto})
    return steps


def _set_plan_custom(poses: List[int], include_product: bool,
                     modo_producto: str = "suspendida") -> List[Dict[str, Any]]:
    """Set a medida: una imagen 4K por pose elegida (+ producto opcional)."""
    steps: List[Dict[str, Any]] = []
    for k in poses:
        s: Dict[str, Any] = {"mode": "on_model", "aspect": "4:5", "paneles": 1,
                             "force_pose": int(k)}
        # 3 = DE ESPALDA, 10 = DETALLE DE ESPALDA → usan la foto de espalda si hay
        if int(k) in (3, 10):
            s["use_back"] = True
        steps.append(s)
    if include_product:
        steps.append({"mode": "product_only", "aspect": "4:5", "paneles": 1,
                      "modo_producto": modo_producto})
    return steps


def _set_plan_poses_txt(poses_txt: List[str], include_product: bool,
                        modo_producto: str = "suspendida") -> List[Dict[str, Any]]:
    """Set a medida con poses ESCRITAS por la usuaria: una imagen 4K por texto de pose."""
    steps: List[Dict[str, Any]] = []
    _SOLO_KW = ("producto solo", "solo producto", "producto suelto", "sin modelo",
                "sin avatar", "sin persona", "prenda sola", "prendas solas",
                "piezas solas", "colgada sola", "colgadas solas", "colgado solo",
                "colgados solos")
    for t in poses_txt:
        t = str(t).strip()
        if not t:
            continue
        low = t.lower()
        # Línea que pide el PRODUCTO SOLO (sin modelo) → toma product_only, no on_model
        # (antes toda línea salía con avatar aunque pidiera "las piezas colgadas solas").
        if any(w in low for w in _SOLO_KW):
            modo = ("percha" if "percha" in low else
                    "flat_lay" if "flat" in low else
                    "tirada_piso" if "piso" in low else
                    "doblada" if "doblad" in low else
                    "maniqui_fantasma" if "fantasma" in low else "suspendida")
            steps.append({"mode": "product_only", "aspect": "4:5", "paneles": 1,
                          "modo_producto": modo, "indicacion": t})
            continue
        st: Dict[str, Any] = {"mode": "on_model", "aspect": "4:5", "paneles": 1,
                              "pose_txt": t}
        # Pose escrita que menciona la espalda → usa la foto de espalda como verdad
        if any(w in low for w in ("espalda", "de atras", "de atrás")):
            st["use_back"] = True
        steps.append(st)
    if include_product:
        steps.append({"mode": "product_only", "aspect": "4:5", "paneles": 1,
                      "modo_producto": modo_producto})
    return steps


def _build_step_payload(base: Dict[str, Any], sdef: Dict[str, Any],
                        anchors: Optional[List[str]]) -> Dict[str, Any]:
    p: Dict[str, Any] = {
        "mode": sdef["mode"],
        "user_sub": base.get("user_sub"),
        "image_size": base.get("image_size", "4K"),
        "aspect": sdef["aspect"],
        "paneles": sdef["paneles"],
        "product_images": base.get("product_images") or [],
        "params": base.get("params") or {},
        "save_to_drive": base.get("save_to_drive", True),
    }
    if sdef["mode"] == "on_model":
        p["avatar_id"] = sdef["avatar_id"] if ("avatar_id" in sdef) else base.get("avatar_id")
        p["no_face_recreate"] = sdef.get("no_face_recreate", False)
        p["style"] = base.get("style", "")
        p["reframe"] = base.get("reframe") if sdef["aspect"] == "21:9" else None
        if "force_pose" in sdef:
            p["force_pose"] = sdef["force_pose"]
        if "pose_offset" in sdef:
            p["pose_offset"] = sdef["pose_offset"]
        extra = {}
        if sdef.get("pose_txt"):
            extra["pose"] = sdef["pose_txt"]   # pose escrita a mano por la usuaria
        if sdef.get("color_set"):
            extra["color_set"] = sdef["color_set"]
        spec = sdef.get("modelo_spec") or {}
        for k, v in spec.items():
            if v:
                extra[k] = v
        # si NO hay avatar, la etnia/pelo definen la cara IA; si hay avatar, la cara
        # viene del avatar y se ignoran esos campos faciales.
        if sdef.get("avatar_id"):
            extra.pop("ap_etnia", None)
            extra.pop("ap_pelo", None)
        ind = str(sdef.get("indicacion", "")).strip()
        if ind:
            prev = str((base.get("params") or {}).get("aclaraciones", "")).strip()
            extra["aclaraciones"] = ((prev + " ") if prev else "") + \
                "DIRECCIÓN DE ESTA TOMA (seguila): " + ind
        if extra:
            p["params"] = {**(base.get("params") or {}), **extra}
    elif sdef["mode"] == "trio":
        p["style"] = base.get("style", "")
        p["asign"] = sdef.get("asign") or []
        p["colores"] = sdef.get("colores") or []
        p["reframe"] = None
        ind = str(sdef.get("indicacion", "")).strip()
        if ind:
            prev = str((base.get("params") or {}).get("aclaraciones", "")).strip()
            p["params"] = {**(base.get("params") or {}),
                           "aclaraciones": ((prev + " ") if prev else "") +
                           "DIRECCIÓN DE LA FOTO GRUPAL (seguila): " + ind}
    else:
        p["modo_producto"] = sdef.get("modo_producto", "suspendida")
        p["reframe"] = None
        ind = str(sdef.get("indicacion", "")).strip()
        if ind:
            prev = str((base.get("params") or {}).get("aclaraciones", "")).strip()
            p["params"] = {**(base.get("params") or {}),
                           "aclaraciones": ((prev + " ") if prev else "") +
                           "PRESENTACIÓN PEDIDA POR LA USUARIA (seguila tal cual): " + ind}
    # Si es la toma de espalda y hay foto de espalda, esa pasa a ser la verdad
    back = base.get("product_images_back") or []
    # La foto de espalda SOLO se usa en tomas de una sola pose (nunca en paneles
    # múltiples, donde arruinaría las poses de frente que comparten la imagen).
    if sdef.get("use_back") and back and int(sdef.get("paneles", 1)) <= 1:
        if sdef["mode"] == "on_model" and int(sdef.get("paneles", 1)) > 1:
            p["product_images"] = back + (base.get("product_images") or [])
        else:
            p["product_images"] = back
    elif back:
        # En las demás tomas, la espalda viaja aparte: el INSPECTOR la usa rotulada
        # para no castigar una toma de espalda por "no coincidir con el frente".
        p["product_images_back"] = back
    if anchors:
        p["consistency_refs"] = anchors
    return p


async def _run_set_job(jid: str) -> None:
    _LIVE.add(jid)
    try:
        state = await _job_state_get(jid)
        ctx = await _job_ctx_get(jid)
        base = await _job_in_get(jid)
        set_current_sub((base or {}).get("user_sub"))
        if not state:
            return
        if not base or not ctx:
            state["status"] = "error"
            state["error"] = "No se pudieron leer los datos del trabajo (Redis)."
            await _job_state_save(state)
            return
        steps = base.get("plan") or _set_plan(bool(base.get("hq")))
        settings_job = await get_settings()
        anchors = ctx.get("anchors") or []
        group_crops = ctx.get("group_crops") or []   # 3 recortes de la grupal, uno por modelo
        done = set(ctx.get("done_indices") or [])
        for i, sdef in enumerate(steps):
            fresh = await _job_ctx_get(jid) or ctx
            if fresh.get("stop"):
                state["status"] = "stopped"
                await _job_state_save(state)
                return
            if i in done:
                continue
            # Encadenamiento del set de lencería (orden nuevo):
            # 1) Cada individual EXTRA de una modelo ancla a la individual base de ESA modelo.
            # 2) La GRUPAL recibe las 3 individuales ya aprobadas como identidad.
            # La toma de ESPALDA no recibe anclas de frente (confunden la orientación).
            if sdef["mode"] == "on_model" and sdef.get("modelo_idx") is not None:
                k = int(sdef["modelo_idx"])
                use_anchors = ([_dataurl_to_anchor(group_crops[k])]
                               if (group_crops and k < len(group_crops)
                                   and group_crops[k]) else None)
            elif base.get("no_anchors"):
                use_anchors = None
            else:
                use_anchors = (_anchors_para_envio(anchors)
                               if (i > 0 and sdef["mode"] == "on_model") else None)
            payload = _build_step_payload(base, sdef, use_anchors)
            try:
                try:
                    res = await _gen_con_borrador(jid, i, payload, settings_job)
                except HTTPException as ge_inner:
                    # Si una toma se bloqueó Y estaba usando una referencia (la individual
                    # de esa modelo), reintentamos SIN la referencia antes de darla por
                    # bloqueada (la referencia en ropa interior a veces dispara el filtro).
                    if (getattr(ge_inner, "status_code", 0) == 422 and use_anchors
                            and sdef.get("modelo_idx") is not None):
                        res = await _do_generate(_build_step_payload(base, sdef, None))
                    else:
                        raise
            except HTTPException as ge:
                code = getattr(ge, "status_code", 0)
                msg = str(getattr(ge, "detail", ge))
                if code == 402:
                    raise  # sin presupuesto: cortamos el set
                if sdef.get("critical"):
                    # La toma principal (grupal de las 3) no salió: frenamos TODO el set.
                    state["status"] = "error"
                    state["error"] = ("No se pudo generar la foto principal (las 3 modelos "
                                      "juntas), así que frené el set para no gastar en el resto. "
                                      "Probá de nuevo o cambiá algún avatar/color. Detalle: " + msg)
                    await _job_state_save(state)
                    return
                # cualquier otra (filtro 422, datos 400): marcamos y SEGUIMOS
                await kv.set(f"imagenes:jobopt:{jid}:{i}",
                             {"assets": [], "blocked": True, "error": msg, "status": "blocked"},
                             ttl=RES_TTL)
                done.add(i)
                sk = state.get("skipped") or []
                if i not in sk:
                    sk.append(i)
                state["skipped"] = sk
                light = await _job_ctx_get(jid) or {}
                light["anchors"] = anchors
                light["done_indices"] = sorted(done)
                await _job_ctx_save(jid, light)
                state["done"] = len(done)
                state["step"] = i + 1
                await _job_state_save(state)
                continue
            except Exception as ge:  # noqa: BLE001 — cualquier otro error NO debe hacer desaparecer la toma
                msg = f"error inesperado: {ge}"
                if sdef.get("critical"):
                    state["status"] = "error"
                    state["error"] = ("No se pudo generar la foto principal (las 3 modelos "
                                      "juntas), así que frené el set. Probá de nuevo. Detalle: "
                                      + msg)
                    await _job_state_save(state)
                    return
                await kv.set(f"imagenes:jobopt:{jid}:{i}",
                             {"assets": [], "blocked": True, "error": msg, "status": "blocked"},
                             ttl=RES_TTL)
                done.add(i)
                sk = state.get("skipped") or []
                if i not in sk:
                    sk.append(i)
                state["skipped"] = sk
                light = await _job_ctx_get(jid) or {}
                light["anchors"] = anchors
                light["done_indices"] = sorted(done)
                await _job_ctx_save(jid, light)
                state["done"] = len(done)
                state["step"] = i + 1
                await _job_state_save(state)
                continue
            if sdef.get("critical") and not (res.get("assets")):
                state["status"] = "error"
                state["error"] = ("La foto principal (las 3 modelos juntas) salió vacía, "
                                  "así que frené el set. Probá de nuevo.")
                await _job_state_save(state)
                return
            await _job_store_result(jid, i, res)
            # Tras la GRUPAL: la partimos en 3 recortes (uno por modelo) para que cada
            # individual calce con cómo salió realmente en la campaña.
            if sdef["mode"] == "trio":
                crops = _split_group_to_anchors(res, n=3)
                if crops:
                    group_crops = crops
            # SIN grupal (set de 1 o 2 modelos): la individual BASE de cada modelo pasa a
            # ser su referencia — así las tomas extra de esa modelo mantienen su cara.
            elif (sdef["mode"] == "on_model" and sdef.get("modelo_idx") is not None):
                _k2 = int(sdef["modelo_idx"])
                while len(group_crops) <= _k2:
                    group_crops.append(None)
                if not group_crops[_k2]:
                    for _a2 in (res.get("assets") or []):
                        if _a2.get("optimized"):
                            group_crops[_k2] = _a2["optimized"]
                            break
            # Anclas del set normal: ACUMULATIVAS — cada toma terminada se suma como
            # referencia para las siguientes (la 2 ve la 1; la 3 ve la 1 y la 2; etc.).
            # Se marca si la toma muestra la CARA: al enviar van primero las tomas con
            # cara (anclar la identidad SOLO a fotos sin cara sacaba otra persona).
            # (En el set de lencería no: cada modelo usa SU individual como referencia.)
            _fp_a = sdef.get("force_pose")
            _pt_a = str(sdef.get("pose_txt", "")).lower()
            _sin_cara = (_fp_a in (3, 9, 10)
                         or any(w in _pt_a for w in ("espalda", "de atras", "de atrás",
                                                     "detalle", "sin cara")))
            if (not base.get("no_anchors") and not base.get("group_anchor_mode")
                    and sdef.get("modelo_idx") is None
                    and sdef["mode"] == "on_model"):
                for a in (res.get("assets") or []):
                    if a.get("optimized"):
                        anchors.append({"u": _dataurl_to_anchor(a["optimized"]),
                                        "cara": not _sin_cara})
                        break
            done.add(i)
            light = await _job_ctx_get(jid) or {}
            light["anchors"] = anchors
            light["group_crops"] = group_crops
            light["done_indices"] = sorted(done)
            await _job_ctx_save(jid, light)
            state["done"] = len(done)
            state["step"] = i + 1
            await _job_state_save(state)
        state["status"] = "done"
        await _job_state_save(state)
    except HTTPException as e:
        st = await _job_state_get(jid) or {"id": jid, "kind": "set"}
        st["status"] = "error"
        st["error"] = str(e.detail)
        await _job_state_save(st)
    except Exception as e:
        st = await _job_state_get(jid) or {"id": jid, "kind": "set"}
        st["status"] = "error"
        st["error"] = str(e)
        await _job_state_save(st)
    finally:
        _LIVE.discard(jid)


async def _run_single_job(jid: str) -> None:
    _LIVE.add(jid)
    try:
        state = await _job_state_get(jid)
        ctx = await _job_ctx_get(jid)
        base = await _job_in_get(jid)
        set_current_sub((base or {}).get("user_sub"))
        if not state:
            return
        if not base or ctx is None:
            state["status"] = "error"
            state["error"] = "No se pudieron leer los datos del trabajo (Redis)."
            await _job_state_save(state)
            return
        if 0 not in set(ctx.get("done_indices") or []):
            res = await _gen_con_borrador(jid, 0, base, await get_settings())
            await _job_store_result(jid, 0, res)
            light = await _job_ctx_get(jid) or {}
            light["done_indices"] = [0]
            await _job_ctx_save(jid, light)
            state["done"] = 1
        state["status"] = "done"
        state["step"] = 1
        await _job_state_save(state)
    except HTTPException as e:
        st = await _job_state_get(jid) or {"id": jid, "kind": "single"}
        st["status"] = "error"
        st["error"] = str(e.detail)
        await _job_state_save(st)
    except Exception as e:
        st = await _job_state_get(jid) or {"id": jid, "kind": "single"}
        st["status"] = "error"
        st["error"] = str(e)
        await _job_state_save(st)
    finally:
        _LIVE.discard(jid)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


@router.post(ROUTE_PREFIX + "/api/preview_prompt")
async def api_preview_prompt(request: Request,
                             payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Arma el prompt y lo devuelve SIN generar la imagen. No gasta nada."""
    set_current_sub(session_sub_from_request(request))
    base = dict(payload)
    base["user_sub"] = session_sub_from_request(request)
    base["product_images"] = _shrink_products(payload.get("product_images"))
    base["solo_prompt"] = True
    res = await _do_generate(base)
    return {"prompt": res.get("prompt", ""), "n_imagenes": res.get("n_imagenes", 0),
            "modelo": res.get("modelo", "")}


@router.post(ROUTE_PREFIX + "/api/generate")
async def api_generate(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Genera UNA imagen en segundo plano (job persistido)."""
    jid = _uuid.uuid4().hex
    base = dict(payload)
    base["user_sub"] = session_sub_from_request(request)
    base["product_images"] = _shrink_products(payload.get("product_images"))
    if not await _job_in_save(jid, base):
        raise HTTPException(507, "No pude guardar el trabajo en Redis (¿fotos muy grandes?). "
                                 "Probá con menos fotos o más chicas.")
    await _job_ctx_save(jid, {"done_indices": []})
    await _job_state_save({"id": jid, "kind": "single", "status": "running",
                           "step": 0, "total": 1, "done": 0, "error": ""})
    _spawn(_run_single_job(jid))
    return {"job_id": jid, "status": "running"}


@router.post(ROUTE_PREFIX + "/api/set")
async def api_set(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Genera el SET completo en segundo plano (encadenado en el server, resumible)."""
    hq = bool(payload.get("hq"))
    base = {
        "hq": hq,
        "user_sub": session_sub_from_request(request),
        "avatar_id": payload.get("avatar_id"),
        "product_images": _shrink_products(payload.get("product_images")),
        "product_images_back": _shrink_products(payload.get("product_images_back")),
        "image_size": payload.get("image_size", "4K"),
        "style": payload.get("style", ""),
        "reframe": payload.get("reframe"),
        "params": payload.get("params") or {},
        "save_to_drive": payload.get("save_to_drive", True),
    }
    total = len(_set_plan(hq))
    colores = payload.get("colores")
    asign = payload.get("asign")
    poses = payload.get("poses")
    poses_txt = payload.get("poses_texto")
    if isinstance(poses_txt, str):
        poses_txt = [ln for ln in poses_txt.replace(";", "\n").split("\n") if ln.strip()]
    if isinstance(colores, str):
        colores = [c for c in colores.replace(";", ",").split(",") if c.strip()]
    if isinstance(asign, list) and len(asign) > 0:
        plan = _set_plan_trio(asign, [], payload.get("modo_producto", "suspendida"),
                              extras=payload.get("extras"),
                              inc_grupal=payload.get("inc_grupal", True),
                              inc_ind=payload.get("inc_ind", True),
                              inc_prod=payload.get("inc_prod", True),
                              grupal_indicacion=str(payload.get("grupal_indicacion", "")))
        base["plan"] = plan
        base["group_anchor_mode"] = True    # cada modelo usa SU individual como referencia
        total = len(plan)
    elif isinstance(colores, list) and len(colores) > 0:
        plan = _set_plan_trio([], colores, payload.get("modo_producto", "suspendida"),
                              extras=payload.get("extras"),
                              inc_grupal=payload.get("inc_grupal", True),
                              inc_ind=payload.get("inc_ind", True),
                              inc_prod=payload.get("inc_prod", True),
                              grupal_indicacion=str(payload.get("grupal_indicacion", "")))
        base["plan"] = plan
        base["group_anchor_mode"] = True
        total = len(plan)
    elif isinstance(poses_txt, list) and len(poses_txt) > 0:
        incp = bool(payload.get("include_product", True))
        modo_p = payload.get("modo_producto", "suspendida")
        plan = _set_plan_poses_txt([str(x) for x in poses_txt][:9], incp, modo_p)
        base["plan"] = plan
        # Encadenamos identidad entre tomas (que sea la MISMA persona); la pose la fija el
        # texto de cada línea y el bloque de consistencia obliga a que cambie igual.
        total = len(plan)
    elif isinstance(poses, list) and len(poses) > 0:
        incp = bool(payload.get("include_product", True))
        modo_p = payload.get("modo_producto", "suspendida")
        plan = _set_plan_custom([int(x) for x in poses][:14], incp, modo_p)
        base["plan"] = plan
        total = len(plan)
    jid = _uuid.uuid4().hex
    if not await _job_in_save(jid, base):
        raise HTTPException(507, "No pude guardar el trabajo en Redis (¿fotos muy grandes?). "
                                 "Probá con menos fotos o más chicas.")
    await _job_ctx_save(jid, {"anchors": [], "done_indices": [], "stop": False})
    await _job_state_save({"id": jid, "kind": "set", "status": "running",
                           "step": 0, "total": total, "done": 0, "error": ""})
    _spawn(_run_set_job(jid))
    return {"job_id": jid, "status": "running", "total": total}


@router.get(ROUTE_PREFIX + "/api/jobs/last_debug")
async def api_jobs_last_debug(request: Request) -> Dict[str, Any]:
    """Diagnóstico legible del ÚLTIMO trabajo: estado y error de cada toma (sin imágenes)."""
    set_current_sub(session_sub_from_request(request))
    ptr = await kv.get(_pfx() + "job:current")
    if not ptr or not ptr.get("id"):
        return {"info": "No hay trabajos recientes para esta cuenta."}
    jid = ptr["id"]
    st = await _job_state_get(jid) or {}
    if st.get("owner") != CURRENT_SUB.get():
        return {"info": "No hay trabajos recientes para esta cuenta."}
    base = await _job_in_get(jid) or {}
    steps = base.get("plan") or []
    total = int(st.get("total") or len(steps) or 0)
    tomas = []
    for i in range(total):
        opt = await kv.get(f"imagenes:jobopt:{jid}:{i}") or {}
        estado = opt.get("status") or ("done" if opt.get("assets") else "sin resultado")
        tomas.append({
            "toma": i + 1,
            "que_es": (_step_desc(steps[i]) if i < len(steps) else ""),
            "estado": estado,
            "tiene_imagen": bool(opt.get("assets")),
            "error": str(opt.get("error", ""))[:400],
        })
    lastblock = await kv.get(_pfx() + "lastblock") or {}
    lastprompt = await kv.get(_pfx() + "lastprompt") or {}
    return {"trabajo": jid[:8], "tipo": st.get("kind", ""), "estado_general": st.get("status", ""),
            "hechas": st.get("done", 0), "total": total,
            "error_general": str(st.get("error", ""))[:400],
            "salteadas": st.get("skipped") or [], "tomas": tomas,
            "prompt_ultima_bloqueada": str(lastblock.get("prompt", ""))[:7000],
            "prompt_ultima_toma": str(lastprompt.get("prompt", ""))[:7000],
            "modelo_usado": str(lastprompt.get("modelo", "")),
            "filtro_usado": str(lastprompt.get("filtro", "")),
            "pedido": (await kv.get(_pfx() + "lastreq")) or {}}


@router.get(ROUTE_PREFIX + "/api/jobs/active")
async def api_jobs_active() -> Dict[str, Any]:
    """Devuelve el job en curso (si hay) y revive huérfanos tras un reinicio."""
    ptr = await kv.get(_pfx() + "job:current")
    if not ptr or not ptr.get("id"):
        return {"status": "none"}
    state = await _job_state_get(ptr["id"])
    if not state:
        return {"status": "none"}
    if state.get("owner") != CURRENT_SUB.get():
        return {"status": "none"}   # nunca mostrar trabajos de otra cuenta
    # Si quedó "running" pero no hay tarea viva en este proceso → reinicio: reanudar.
    if state.get("status") == "running" and state["id"] not in _LIVE:
        if state.get("kind") == "set":
            _spawn(_run_set_job(state["id"]))
        else:
            _spawn(_run_single_job(state["id"]))
        state["resumed"] = True
    return state


@router.get(ROUTE_PREFIX + "/api/jobs/{jid}")
async def api_job_state(jid: str) -> Dict[str, Any]:
    state = await _job_owned(jid)
    return state or {"status": "unknown"}


@router.get(ROUTE_PREFIX + "/api/jobs/{jid}/result/{idx}")
async def api_job_result(jid: str, idx: int) -> Dict[str, Any]:
    if not await _job_owned(jid):
        return {"status": "unknown"}
    opt = await kv.get(f"imagenes:jobopt:{jid}:{idx}")
    if not opt:
        return {"status": "pending"}
    # El PNG master se entrega una vez y se borra de Redis (para no acumular peso).
    png_blob = await kv.get(f"imagenes:jobpng:{jid}:{idx}")
    if png_blob and png_blob.get("png"):
        pngs = png_blob["png"]
        for n, a in enumerate(opt.get("assets", [])):
            if n < len(pngs) and pngs[n]:
                a["png"] = pngs[n]
        await kv.delete(f"imagenes:jobpng:{jid}:{idx}")
    opt["status"] = "done"
    return opt


@router.post(ROUTE_PREFIX + "/api/jobs/{jid}/stop")
async def api_job_stop(jid: str) -> Dict[str, Any]:
    await _job_owned(jid)
    ctx = await _job_ctx_get(jid)
    if ctx is not None:
        ctx["stop"] = True
        await _job_ctx_save(jid, ctx)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# FRONTEND (vanilla JS, una sola página)
# ─────────────────────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="es-AR">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Studio Luma · Fotos de producto con IA</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%23161419'/%3E%3Crect x='2.5' y='2.5' width='59' height='59' rx='12' fill='none' stroke='%23c9a86b' stroke-width='2'/%3E%3Ctext x='32' y='44' font-family='Georgia,serif' font-size='34' font-weight='600' fill='%23d8b878' text-anchor='middle'%3ESL%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:opsz,wght@6..96,400;6..96,500;6..96,600;6..96,700&family=Jost:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --ink:#ecebf1; --ink-soft:#96919f; --line:#2c2a34;
    --ivory:#131218; --card:#1b1a21; --card-2:#232128;
    --rose:#c9a86b; --rose-deep:#d8b878;
    --plum:#c9a86b; --ok:#5fae86; --bad:#e0736f;
    --shadow:0 1px 2px rgba(0,0,0,.5),0 12px 34px rgba(0,0,0,.4);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--ivory);color:var(--ink);
    font-family:Jost,system-ui,sans-serif;font-size:17px;line-height:1.6;
    -webkit-font-smoothing:antialiased}
  a{color:var(--rose-deep);text-decoration:none}
  header{padding:20px 18px 14px;border-bottom:1px solid var(--line);background:rgba(19,18,24,.86);
    backdrop-filter:blur(8px);position:sticky;top:0;z-index:20}
  .brandrow{display:flex;align-items:center;gap:12px}
  .uchip{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--ink-soft)}
  .uchip img{width:26px;height:26px;border-radius:50%;border:1px solid var(--line)}
  .uchip a{color:var(--rose-deep);font-size:12px}
  .ovl{position:fixed;inset:0;background:rgba(8,7,11,.72);backdrop-filter:blur(4px);z-index:100;
    display:none;align-items:flex-start;justify-content:center;overflow-y:auto;padding:24px 14px}
  .ovl.on{display:flex}
  .sheet{background:var(--card);border:1px solid var(--line);border-radius:18px;max-width:460px;
    width:100%;padding:22px;box-shadow:var(--shadow);margin:auto}
  .sheet h2{margin-top:0}
  .sheet .step{display:flex;gap:10px;margin:8px 0;font-size:13px;color:var(--ink-soft)}
  .sheet .step b{color:var(--rose-deep);font-family:'Bodoni Moda',serif;font-size:16px}
  .mkchip{margin-left:8px;font-size:12px;color:var(--rose-deep);cursor:pointer;border:1px solid var(--line);
    border-radius:99px;padding:3px 10px;background:var(--card-2)}
  .q{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;border-radius:50%;
    border:1px solid var(--rose-deep);color:var(--rose-deep);font-size:10px;cursor:help;margin-left:4px;font-weight:600}
  .mono{width:42px;height:42px;border-radius:11px;border:1px solid var(--rose);
    display:flex;align-items:center;justify-content:center;flex:none;
    background:linear-gradient(150deg,#221f27,#161419);
    font-family:'Bodoni Moda',serif;font-weight:600;font-size:20px;color:var(--rose-deep);
    letter-spacing:.02em;box-shadow:inset 0 0 12px rgba(201,168,107,.12)}
  .brand{font-family:'Bodoni Moda',serif;font-size:25px;font-weight:600;letter-spacing:.3px;line-height:1}
  .brand small{font-family:Jost;font-weight:400;font-size:11px;color:var(--ink-soft);
    letter-spacing:.22em;text-transform:uppercase;display:block;margin-top:5px}
  .tabs{display:flex;gap:5px;margin-top:16px;flex-wrap:wrap}
  .tab{padding:10px 17px;border-radius:999px;border:1px solid var(--line);background:var(--card);
    cursor:pointer;font-weight:400;font-size:15px;color:var(--ink-soft);transition:.15s}
  .tab:hover{border-color:var(--rose)}
  .tab.on{background:var(--rose);color:#17140d;border-color:var(--rose);font-weight:500}
  main{max-width:1100px;margin:0 auto;padding:20px}
  .panel{display:none}.panel.on{display:block}
  #p-generar .card{max-width:100%}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;
    padding:22px;margin-bottom:18px;box-shadow:var(--shadow)}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:24px;margin:0 0 6px;letter-spacing:.2px}
  .hint{color:var(--ink-soft);font-size:14.5px;margin:0 0 15px}
  label{display:block;font-size:14px;font-weight:500;color:var(--ink-soft);
    margin:14px 0 6px;letter-spacing:.03em}
  input,select,textarea{width:100%;padding:13px 14px;border:1px solid var(--line);
    border-radius:11px;font:inherit;font-size:16px;background:#17161d;color:var(--ink)}
  input::placeholder,textarea::placeholder{color:#5f5a68}
  input:focus,select:focus,textarea:focus{outline:none;border-color:var(--rose)}
  textarea{resize:vertical;min-height:60px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
  button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;
    border-radius:12px;padding:15px 22px;font-weight:600;cursor:pointer;font-size:17px;margin-top:18px;
    font-family:Jost,sans-serif;letter-spacing:.02em}
  button.go:disabled{opacity:.45;cursor:wait}
  button.ghost{background:var(--card-2);border:1px solid var(--line);border-radius:11px;
    padding:11px 15px;cursor:pointer;font-weight:400;font-size:15px;color:var(--ink)}
  button.ghost:hover{border-color:var(--rose)}
  .grid-av{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
  .slot{border:1px dashed var(--line);border-radius:12px;aspect-ratio:3/4;display:flex;
    align-items:center;justify-content:center;flex-direction:column;gap:6px;cursor:pointer;
    background:#17161d;overflow:hidden;position:relative;text-align:center;padding:6px}
  .slot.filled{border-style:solid}
  .slot.noref{border-style:dashed;border-color:var(--bad)}
  .slot.noref img{display:none}
  .slot.noref::after{content:'⚠ regenerar';color:var(--bad);font-size:12px;font-weight:600}
  .slot img{width:100%;height:100%;object-fit:cover}
  .slot .meta{position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,.68);
    color:#fff;font-size:11px;padding:4px 6px;display:flex;justify-content:space-between;align-items:center}
  .slot .plus{font-size:26px;color:var(--rose);font-weight:300}
  .slot .lbl{font-size:11px;color:var(--ink-soft)}
  .dz{border:1px dashed var(--line);border-radius:12px;padding:26px;text-align:center;
    color:var(--ink-soft);cursor:pointer;background:#17161d;transition:.15s;font-size:16px}
  .dz:hover{border-color:var(--rose)}
  .dz img{max-height:160px;border-radius:8px;margin-top:8px}
  .results{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;margin-top:16px}
  .res{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--card-2)}
  .res img{width:100%;display:block;background:#0f0e13}
  .res .dl{display:flex;gap:6px;padding:8px;flex-wrap:wrap}
  .res a{font-size:14px;border:1px solid var(--line);border-radius:8px;
    padding:8px 11px;color:var(--ink)}
  .pill{display:inline-block;background:rgba(201,168,107,.14);color:var(--rose-deep);border-radius:999px;
    padding:4px 12px;font-size:13.5px;font-weight:500;margin:2px 4px 2px 0}
  .ledrow{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);
    padding:8px 0;font-size:13px}
  .ledrow span:last-child{color:var(--ink-soft)}
  .toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:#2a2833;
    color:#fff;padding:10px 16px;border-radius:10px;font-size:13.5px;z-index:50;display:none;max-width:90%;
    border:1px solid var(--line)}
  .toast.bad{background:var(--bad);border-color:var(--bad)}
  .seg{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
  .seg .opt{padding:9px 15px;border:1px solid var(--line);border-radius:9px;cursor:pointer;
    font-size:15px;background:var(--card-2);color:var(--ink)}
  .seg .opt.on{background:var(--rose);color:#17140d;border-color:var(--rose);font-weight:500}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px}
  .chip{font-size:12px;padding:6px 11px;border-radius:99px;border:1px dashed var(--rose-deep);
    background:transparent;color:var(--rose-deep);cursor:pointer;font-weight:500}
  .chip:active{background:var(--rose);color:#17140d}
  .kv{display:flex;justify-content:space-between;font-size:13px;padding:5px 0;color:var(--ink-soft)}
  .kv b{color:var(--ink)}
  summary::-webkit-details-marker{color:var(--rose)}
  .pk{display:flex;align-items:center;gap:7px;font-size:13px;color:var(--ink);font-weight:400;margin:0;cursor:pointer}
  .pk input{width:auto;margin:0}
  .tcard{border-top:1px solid var(--line);padding-top:4px;margin-top:8px}
  .tcard:first-child{border-top:none;margin-top:0}
  details.adv{background:var(--card-2)!important}
  .note{background:rgba(201,168,107,.08);border:1px solid var(--line);border-radius:10px;padding:10px 12px;
    font-size:12.5px;color:var(--ink-soft);margin-top:10px}
  @media(max-width:560px){.row,.row3{grid-template-columns:1fr}.grid-av{grid-template-columns:repeat(2,1fr)}
    main{padding:12px}.card{padding:16px}}
</style>
</head>
<body>
<header>
  <div class="brandrow">
    <div class="mono">SL</div>
    <div class="brand">Studio Luma<small>Fotos de producto con IA · v%%VERSION%%</small></div>
    <div id="userchip" class="uchip"></div>
  </div>
  <div class="tabs" id="tabs">
    <div class="tab on" data-p="generar">Generar</div>
    <div class="tab" data-p="producto">Producto</div>
    <div class="tab" data-p="colores">Variar color</div>
    <div class="tab" data-p="editor">🎨 Editor</div>
    <div class="tab" data-p="avatares">Avatares</div>
    <div class="tab" data-p="ajustes">Ajustes</div>
    <div class="tab" data-p="presupuesto">Presupuesto</div>
  </div>
</header>

<div class="ovl" id="onboard">
  <div class="sheet">
    <h2 id="ob-title">Bienvenida a Studio Luma</h2>
    <p class="hint" id="ob-sub">Configurá tu marca en 30 segundos. Estos preajustes se aplican solos cada vez que generás. Podés cambiarlos cuando quieras desde “Mi marca”.</p>

    <label>Nombre de tu marca</label>
    <input id="ob-brand" placeholder="Ej: LUMA Íntima">

    <label>Rubro principal</label>
    <select id="ob-rubro">
      <option value="lenceria">Lencería</option>
      <option value="beachwear">Beachwear / bikinis</option>
      <option value="pijamas">Pijamas / ropa de dormir</option>
      <option value="otro">Otro</option>
    </select>

    <div class="row">
      <div><label>Estilo de foto</label>
        <select id="ob-estilo">
          <option value="instagram_real">Instagram real</option>
          <option value="catalogo">Catálogo</option>
          <option value="editorial">Editorial</option>
        </select>
      </div>
      <div><label>Temporada habitual</label>
        <select id="ob-temporada">
          <option value="invierno">Ropa / pijamas</option>
          <option value="verano">Bikini / beachwear</option>
        </select>
      </div>
    </div>

    <div class="row">
      <div><label>Modelo por defecto</label>
        <select id="ob-modelo">
          <option value="ia">IA (sin avatar)</option>
          <option value="avatar">Con mi avatar</option>
        </select>
      </div>
      <div><label>Calidad por defecto</label>
        <select id="ob-size">
          <option value="2K">2K (redes)</option>
          <option value="4K">4K (impresión)</option>
        </select>
      </div>
    </div>

    <label>Fondo / escenario preferido</label>
    <input id="ob-fondo" placeholder="Ej: pared clara / playa / pileta">

    <details style="margin:14px 0;border:1px solid var(--line);border-radius:10px;padding:8px 12px;background:var(--card-2)">
      <summary style="cursor:pointer;font-weight:500">📖 Guía rápida (cómo usarla)</summary>
      <div class="step"><b>1</b><span>Elegí qué vas a fotografiar (ropa o bikini) y subí la foto de tu prenda.</span></div>
      <div class="step"><b>2</b><span>Si querés, tocá “Opciones avanzadas” para ajustar poses, cuerpo, fondo o luz. Si no, se usan tus preajustes de marca.</span></div>
      <div class="step"><b>3</b><span>Tocá “Generar” (una foto) o “Set completo” (varias poses). Tus imágenes se guardan solas en tu Google Drive.</span></div>
      <div class="step"><b>★</b><span>Para bikini/lencería conviene “Sin avatar”: la IA crea la modelo y evita bloqueos.</span></div>
    </details>

    <button class="go" id="ob-save" style="width:100%">Guardar y empezar</button>
    <div style="text-align:center;margin-top:8px"><a id="ob-skip" style="cursor:pointer;font-size:13px">Saltar por ahora</a></div>
  </div>
</div>

<main>

<!-- GENERAR (on_model) -->
<section class="panel on" id="p-generar">
  <div class="card">
    <h2>Generar fotos</h2>
    <p class="hint">3 pasos: elegí qué vas a fotografiar, subí la foto de tu prenda y tocá <b>Generar</b>. Lo demás ya viene listo.</p>
    <label>1) ¿Qué vas a fotografiar?</label>
    <select id="g-temporada">
      <option value="invierno" selected>Ropa / pijamas (fondo interior)</option>
      <option value="verano">Bikini / beachwear (fondo playa)</option>
      <option value="interior_set">Ropa interior — set de colores (3 modelos)</option>
    </select>
    <label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin:6px 0">
      <input type="checkbox" id="g-no-avatar" style="width:auto;margin:0">
      <span>Sin avatar — que la IA invente la modelo (recomendado para bikini/lencería; usa el tipo de cuerpo elegido)</span>
    </label>
    <div id="apar-box" style="display:none;border:1px dashed var(--line);border-radius:10px;padding:10px;margin:6px 0">
      <p class="hint" style="margin-top:0" id="apar-title">Apariencia de la modelo IA (no queda al azar):</p>
      <div class="row">
        <div><label>Género</label>
          <select id="g-genero"><option value="mujer">Mujer</option><option value="hombre">Hombre</option></select>
        </div>
        <div></div>
      </div>
      <div class="row">
        <div><label>Etnia / tez</label>
          <select id="ap-etnia"><option value="">(libre)</option><option value="latina">Latina</option><option value="morocha_tez_oscura">Morocha tez oscura</option><option value="caucasica">Caucásica clara</option><option value="afro">Afro piel oscura</option><option value="asiatica">Asiática</option><option value="mediterranea">Mediterránea</option><option value="arabe">Árabe / medio-oriente</option><option value="mestiza">Mestiza</option></select>
        </div>
        <div><label>Edad aprox.</label>
          <select id="ap-edad"><option value="">(libre)</option><option value="joven">Joven (20-28)</option><option value="adulta">Adulta (28-38)</option><option value="madura">Madura (38-50)</option></select>
        </div>
      </div>
      <div class="row">
        <div><label>Pelo</label>
          <select id="ap-pelo"><option value="">(libre)</option><option value="morocha_largo_ondulado">Largo ondulado oscuro</option><option value="negro_lacio">Negro lacio</option><option value="castaño_largo">Castaño largo</option><option value="castaño_ondulado">Castaño ondulado</option><option value="rubia">Rubia</option><option value="pelirroja">Pelirroja</option><option value="corto">Corto</option></select>
        </div>
        <div><label>Ojos</label>
          <select id="ap-ojos"><option value="">(libre)</option><option value="marrones">Marrones</option><option value="negros">Negros</option><option value="claros">Claros</option><option value="verdes">Verdes</option><option value="celestes">Celestes</option></select>
        </div>
      </div>
      <div class="row" id="wrap-barba" style="display:none">
        <div><label>Barba</label>
          <select id="ap-barba"><option value="">(libre)</option><option value="sin_barba">Sin barba (afeitado)</option><option value="tres_dias">Barba de 3 días</option><option value="corta">Barba corta prolija</option><option value="completa">Barba completa</option><option value="bigote">Bigote</option></select>
        </div>
        <div></div>
      </div>
      <label id="lbl-ap-extra">Detalle extra de la modelo (texto libre)</label>
      <input id="ap-extra" placeholder="ej: bronceada, pecas, fit, cara redonda, sonrisa amplia">
    </div>

    <label>Plantilla de artículo (llená la ficha una vez y reusala para cada color)</label>
    <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      <select id="tpl-list" style="flex:1;min-width:140px"><option value="">— elegir —</option></select>
      <button class="ghost" id="tpl-load">Cargar</button>
      <button class="ghost" id="tpl-save">Guardar</button>
      <button class="ghost" id="tpl-del">🗑</button>
    </div>

    <label>Avatar (modelo)</label>
    <div class="grid-av" id="gen-avatars" style="grid-template-columns:repeat(4,1fr)"></div>

    <label>2) Subí la foto de tu prenda (podés subir varias: arriba, pantalón, detalle)</label>
    <div class="dz" id="dz-gen" onclick="document.getElementById('file-gen').click()">
      <div>Tocá para subir una o varias fotos de la prenda</div>
      <input type="file" id="file-gen" accept="image/*" multiple hidden>
    </div>

    <details class="adv" style="margin-top:12px;border:1px solid var(--line);border-radius:10px;padding:10px 12px">
    <summary style="cursor:pointer;font-weight:600;font-family:'Bodoni Moda',serif">⚙️ Opciones avanzadas <span class="hint" style="font-weight:400">(podés dejarlas como están)</span></summary>
    <div style="height:8px"></div>
    <div style="display:flex;gap:8px;align-items:center;margin-top:6px;flex-wrap:wrap">
      <button class="ghost" id="btn-analyze" style="font-size:13px">🔍 Analizar prenda (autocompletar ficha)</button>
      <span id="ficha-status" class="hint" style="margin:0"></span>
    </div>
    <label style="display:flex;align-items:center;gap:8px;margin-top:6px;font-size:13px;cursor:pointer">
      <input type="checkbox" id="g-use-ficha" checked style="width:auto;margin:0">
      <span>Usar la ficha como ayuda al generar (destildá para ir <b>solo con las fotos</b>)</span>
    </label>
    <div id="ficha-box" class="hint" style="display:none;margin-top:6px;padding:8px;border:1px solid var(--line);border-radius:8px;white-space:pre-wrap"></div>

    <label style="margin-top:10px">Describir vos el producto (opcional — si lo llenás, MANDA por sobre el análisis de la IA)</label>
    <textarea id="g-producto-manual" rows="3" placeholder="Ej: pijama de 3 piezas: remera manga larga + pantalón largo + bata. Polar coral estampa ositos. Escote redondo, puños elastizados." style="width:100%"></textarea>
    <p class="hint" style="margin-top:4px">Es tu descripción del producto tal cual vos lo conocés. La IA la respeta al pie de la letra junto con la foto real.</p>

    <label style="margin-top:10px">Qué piezas usa la modelo en la toma (opcional — ej. si el pijama es de 3 piezas y querés que use solo 2)</label>
    <textarea id="g-piezas" rows="2" placeholder="Ej: usa SOLO la remera y el pantalón, sin la bata." style="width:100%"></textarea>
    <p class="hint" style="margin-top:4px">Sirve para conjuntos de varias piezas: decís qué piezas van puestas y las demás no aparecen en la foto.</p>

    <label style="margin-top:10px">Foto de la ESPALDA de la prenda (opcional — mejora la toma de espalda del set)</label>
    <div class="dz" id="dz-gen-back" onclick="document.getElementById('file-gen-back').click()">
      <div>Tocá para subir la espalda de la prenda</div>
      <input type="file" id="file-gen-back" accept="image/*" multiple hidden>
    </div>

    <div class="row">
      <div><label>Tela</label><input id="g-tela" placeholder="algodón / modal / microfibra / seamless"></div>
      <div><label>Color real</label><input id="g-color" placeholder="negro, nude, blanco..."></div>
    </div>
    <div class="row3">
      <div><label>Puños</label><input id="g-punos" placeholder="elastizado / sin puño"></div>
      <div><label>Costuras</label><input id="g-costuras" placeholder="planas / overlock / sin costura"></div>
      <div><label>Cuello/escote</label><input id="g-cuello" placeholder="redondo / v / corazón"></div>
    </div>
    <div class="row3">
      <div><label>Pose</label><input id="g-pose" placeholder="natural, espontánea"></div>
      <div><label>Fondo / escenario</label><input id="g-fondo" placeholder="pared mármol gris, alfombra, cálido"></div>
      <div><label>Luz</label><input id="g-luz" placeholder="sol natural, mucha luz"></div>
    </div>
    <div class="row3">
      <div id="wrap-busto">
        <label>Busto</label>
        <select id="g-busto"><option value="">(según avatar)</option><option value="chico">Chico</option><option value="mediano">Mediano</option><option value="grande">Grande</option><option value="extra_grande">Extra grande (XXL)</option></select>
      </div>
      <div id="wrap-cola">
        <label>Cola</label>
        <select id="g-cola"><option value="">(según avatar)</option><option value="chica">Chica</option><option value="mediana">Mediana</option><option value="grande">Grande</option><option value="extra_grande">Extra grande (XXL)</option></select>
      </div>
      <div>
        <label>Abdomen</label>
        <select id="g-abdomen"><option value="">(según avatar)</option><option value="fit">Fit / tonificado</option><option value="plano">Plano</option><option value="natural">Natural</option><option value="con_pancita">Con pancita natural</option></select>
      </div>
    </div>
    <div class="row3">
      <div>
        <label>Contextura</label>
        <select id="g-contextura"><option value="">(según avatar)</option><option value="delgada">Delgada</option><option value="atletica">Atlética</option><option value="curvy">Curvy</option><option value="talle_grande">Talle grande</option><option value="talle_extra_grande">Talle XXL (hasta 130+)</option></select>
      </div>
      <div>
        <label>Edad corporal / física</label>
        <select id="g-edadcorp"><option value="">(según avatar)</option><option value="20">~20 (joven)</option><option value="30">~30</option><option value="40">~40</option><option value="50">~50</option></select>
      </div>
      <div>
        <label>Altura</label>
        <select id="g-altura"><option value="">(según avatar)</option><option value="baja">Baja ~1,55</option><option value="media">Media ~1,65</option><option value="alta">Alta ~1,75</option><option value="muy_alta">Muy alta 1,80+</option></select>
      </div>
      <div>
        <label>Peinado <span class="q" title="El color de pelo se mantiene del avatar; acá elegís el estilo.">?</span></label>
        <select id="g-peinado"><option value="">(según avatar)</option><option value="largo_suelto">Largo suelto</option><option value="largo_ondulado">Largo ondulado</option><option value="media_melena">Media melena</option><option value="corto">Corto</option><option value="atado">Atado (cola)</option><option value="rodete">Rodete/moño</option><option value="trenza">Trenza</option></select>
      </div>
    </div>
    <div>
      <label>Accesorios (texto libre) <span class="q" title="Lo que quieras sumarle a la modelo: sombrero, aritos, pulseras, collar, tatuajes, anteojos de sol, etc.">?</span></label>
      <input id="g-accesorios" placeholder="ej: sombrero de playa, aritos dorados, tatuaje en el brazo">
    </div>
    <div style="margin-top:12px">
      <label class="pk" style="font-size:15px"><input type="checkbox" id="g-complemento" checked> <span id="lbl-complemento">Si mando solo el corpiño, agregar una bombacha haciendo juego</span> <span class="q" title="Para ropa interior: si subís solo la parte de arriba, la IA agrega una bombacha lisa que combine, para que la modelo no quede sin la parte de abajo (eso a veces dispara el filtro).">?</span></label>
      <input id="g-complemento-desc" placeholder="opcional: cómo querés la bombacha (ej: colaless negra, clásica nude)" style="margin-top:6px">
    </div>
    <div class="row3">
      <div>
        <label>Enfoque del fondo</label>
        <select id="g-foco"><option value="desenfocado" selected>Desenfocado (resalta modelo)</option><option value="nitido">Nítido (arena, olas, árboles)</option></select>
      </div>
      <div>
        <label>Viento</label>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;height:38px"><input type="checkbox" id="g-viento" style="width:auto;margin:0"> Sensación ventosa</label>
      </div>
      <div></div>
    </div>
    <label>Aclaraciones / negativos de la prenda</label>
    <input id="g-aclaraciones" placeholder="ej: sin bolsillos, polar en puños y cuello, oversize">
    <div class="chips" id="neg-g"></div>

    <div class="row">
      <div>
        <label>Estilo</label>
        <select id="g-style">
          <option value="instagram_real" selected>Instagram casual realista</option>
          <option value="catalogo">Catálogo sobrio</option>
          <option value="editorial">Editorial / campaña</option>
        </select>
      </div>
      <div>
        <label>Encuadre</label>
        <input id="g-encuadre" placeholder="cuerpo entero de pies a cabeza">
      </div>
    </div>

    <label>Formato y cantidad</label>
    <div class="row3">
      <div>
        <label style="margin-top:0">Aspect de generación</label>
        <select id="g-aspect">
          <option value="4:5">4:5 (1 toma)</option>
          <option value="1:1">1:1 (1 toma)</option>
          <option value="9:16">9:16 (1 toma)</option>
          <option value="21:9" selected>21:9 (2 paneles)</option>
        </select>
      </div>
      <div>
        <label style="margin-top:0">Paneles</label>
        <select id="g-paneles"><option>1</option><option selected>2</option><option>3</option><option>4</option></select>
      </div>
      <div>
        <label style="margin-top:0">Recortar c/panel a</label>
        <select id="g-reframe">
          <option value="">No recortar</option>
          <option value="4:5" selected>4:5 feed</option>
          <option value="1:1">1:1</option>
          <option value="9:16">9:16 story</option>
        </select>
      </div>
    </div>
    <div class="seg" style="margin-top:10px">
      <div class="opt" data-size="4K">4K (US$0,151)</div>
      <div class="opt on" data-size="2K">2K (US$0,101)</div>
      <div class="opt" data-size="1K">1K (US$0,067)</div>
    </div>
    </details>

    <label style="margin-top:12px">3) Generá tus fotos</label>
    <details id="wrap-poses" style="margin:6px 0 10px;border:1px solid var(--line);border-radius:10px;padding:8px 12px;background:var(--card-2)">
      <summary style="cursor:pointer;font-weight:500">🎬 Elegir poses del set (opcional)</summary>
      <p class="hint" style="margin:8px 0">Tildá las tomas que querés en tu set. Cada una sale en 4K. Si no tocás nada, el set usa las 4 poses de siempre + producto.</p>
      <div id="pose-pick" style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
        <label class="pk"><input type="checkbox" value="0" checked> De pie (mano en el pelo)</label>
        <label class="pk"><input type="checkbox" value="1" checked> 3/4 sobre el hombro</label>
        <label class="pk"><input type="checkbox" value="2" checked> Sentada</label>
        <label class="pk"><input type="checkbox" value="3" checked> De espalda</label>
        <label class="pk"><input type="checkbox" value="4"> Caminando</label>
        <label class="pk"><input type="checkbox" value="5"> Riéndose</label>
        <label class="pk"><input type="checkbox" value="6"> De perfil apoyada</label>
        <label class="pk"><input type="checkbox" value="7"> Estirándose / bretel</label>
        <label class="pk"><input type="checkbox" value="8"> Primer plano de cara</label>
        <label class="pk"><input type="checkbox" value="9"> 🔍 Detalle prenda (zoom, sin cara)</label>
        <label class="pk"><input type="checkbox" value="10"> 🔍 Detalle espalda (atándose)</label>
        <label class="pk"><input type="checkbox" value="11"> Fundida con el ambiente</label>
        <label class="pk"><input type="checkbox" value="12"> Tirada en el piso / arena</label>
        <label class="pk"><input type="checkbox" value="13"> Con utilería (fruta, vaso...)</label>
        <label class="pk"><input type="checkbox" id="pk-prod" checked> Producto (colgado)</label>
      </div>
      <label style="margin-top:12px">✍️ O escribí vos las poses del set (una por línea — si ponés algo acá, MANDA sobre los tildes de arriba)</label>
      <textarea id="g-poses-texto" rows="4" placeholder="Ej:&#10;de pie de frente, mano en la cintura, sonriendo&#10;sentada en un sillón, de 3/4&#10;caminando hacia la cámara&#10;primer plano de cara" style="width:100%"></textarea>
      <p class="hint" style="margin-top:4px">Cada línea es una toma distinta del set (una imagen 4K por línea). Se genera con tu avatar o modelo IA y respeta tal cual la pose que escribís.</p>
    </details>
    <details id="wrap-colores" style="display:none;margin:6px 0 10px;border:1px solid var(--rose-deep);border-radius:10px;padding:8px 12px;background:var(--card-2)" open>
      <summary style="cursor:pointer;font-weight:500">🎨 Set de colores (seamless / ropa interior)</summary>
      <p class="hint" style="margin:8px 0">Definí 1, 2 o 3 modelos (una por color). Si elegís un avatar, la cara sale del avatar (etnia y pelo se toman de él). Si dejás "modelo IA", completá etnia y pelo. Cuerpo y edad valen siempre. La foto GRUPAL sale solo si definís las 3; con 1 o 2 va directo a las individuales + tomas extra.</p>
      <div id="trio-cards">
        <div class="tcard" data-i="0"></div>
        <div class="tcard" data-i="1"></div>
        <div class="tcard" data-i="2"></div>
      </div>
      <div id="trio-extras-wrap" style="margin-top:10px">
        <label>Tomas extra (opcional)</label>
        <div id="trio-extras"></div>
        <button class="ghost" id="btn-add-extra" style="margin-top:6px">➕ Sumar toma al set</button>
      </div>
      <div style="margin-top:12px;border:1px solid var(--rose-deep);border-radius:10px;padding:10px;background:var(--card-2)">
        <label style="margin:0">📝 Mi plantilla de prompt <span class="q" title="Pegá acá TU prompt (el que te funciona). La app lo manda tal cual y solo reemplaza los campos que cambian en cada toma. Si lo dejás vacío, usa el prompt automático de siempre.">?</span></label>
        <p class="hint" style="margin:4px 0 6px">Pegá tu prompt. Se manda <b>exactamente así</b>. Podés usar estos comodines y la app los completa en cada toma:<br>
        <code>{color}</code> color de esta toma · <code>{pose}</code> pose de esta toma · <code>{fondo}</code> · <code>{tela}</code> · <code>{escote}</code> · <code>{costuras}</code> · <code>{aclaraciones}</code></p>
        <textarea id="g-plantilla" rows="6" placeholder="Ej:&#10;MARCA: LUMA Íntima (Argentina)&#10;OBJETIVO: fotografía hiperrealista de moda para e-commerce. El protagonista de la imagen es la prenda...&#10;PRODUCTO: corpiño de tejido morley. Color para esta toma: {color}&#10;POSE: {pose}&#10;ESCENA: {fondo}" style="width:100%;font-family:ui-monospace,monospace;font-size:12px"></textarea>
        <div style="display:flex;gap:8px;margin-top:6px">
          <button class="ghost" id="btn-plantilla-save" style="flex:1">💾 Guardar plantilla</button>
          <button class="ghost" id="btn-plantilla-clear">Vaciar</button>
        </div>
      </div>
      <label style="margin-top:12px">Qué incluir en el set</label>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
        <label class="pk"><input type="checkbox" id="inc-grupal" checked> Foto grupal (las 3)</label>
        <label class="pk"><input type="checkbox" id="inc-ind" checked> Fotos individuales</label>
        <label class="pk"><input type="checkbox" id="inc-prod"> Producto solo</label>
        <label class="pk"><input type="checkbox" id="set-simple"> ✅ Prompt simple (estilo Playground) <span class="q" title="Manda un pedido corto en TODAS las tomas del set (grupal incluida): quiénes son las modelos, cuerpo entero, escenario y prenda. Sin medidas de cuerpo ni encuadres cerrados. Es la estructura que te funciona a mano.">?</span></label>
                      </div>
      <div style="margin-top:6px">
        <label>Indicaciones para la foto grupal (opcional)</label>
        <input id="g-tgrupal-ind" placeholder="ej: abrazadas riéndose, una despeinando a la otra">
      </div>
      <div id="prod-modo-wrap" style="display:none;margin-top:6px">
        <label>Cómo mostrar el producto solo</label>
        <select id="inc-prod-modo">
          <option value="flat_lay">Flat-lay (acostado prolijo)</option>
          <option value="tirada_piso">Tirada en el piso (cenital)</option>
          <option value="doblada">Doblada</option>
          <option value="percha">En percha</option>
          <option value="maniqui_fantasma">Maniquí fantasma</option>
          <option value="suspendida">Colgada (tanza invisible)</option>
        </select>
      </div>
      <button class="go" id="btn-set-colores" style="margin-top:10px">🎨 Generar set de colores</button>
    </details>
    <div id="wrap-gobtns" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <button class="go" id="btn-gen">Generar imágenes</button>
      <button class="go" id="btn-set" style="background:var(--rose-deep)">🎬 Set completo</button>
    </div>
    <button class="ghost" id="btn-stop" style="display:none;border-color:var(--bad);color:var(--bad);margin-top:8px">⏹ Frenar set</button>
    <label style="display:flex;align-items:center;gap:8px;margin-top:8px;font-size:13px;cursor:pointer">
      <input type="checkbox" id="set-hq" style="width:auto;margin:0">
      <span>Alta calidad: 1 imagen por pose (4K real, sin recorte) — <b>más caro</b></span>
    </label>
    <p class="hint" style="margin-top:6px">Set normal = 3 generaciones (poses de a 2 en un cuadro). Alta calidad = 5 generaciones sueltas, cada pose en 4K completo. Cada toma nueva ve TODAS las anteriores como guía (misma modelo y prenda en todo el set). Si salen mal, tocá <b>Frenar</b> y no gasta el resto.</p>

    <div style="margin-top:12px;padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--ivory)">
      <label style="margin:0">Regenerar una toma puntual</label>
      <p class="hint" style="margin:4px 0 8px">Si una imagen del set salió mal, rehacé <b>solo esa</b>. Respeta tus fotos, la ficha, tus aclaraciones y usa las primeras 2 imágenes del último set como guía (misma modelo y prenda).</p>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <select id="one-pose" style="flex:1;min-width:150px"><!-- se llena por JS con TODAS las poses --></select>
        <button class="go" id="btn-one">Generar esta toma</button>
      </div>
    </div>

    <div style="margin-top:12px;padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--ivory)">
      <label style="margin:0">🧾 Diagnóstico del último set</label>
      <p class="hint" style="margin:4px 0 8px">Muestra qué pasó con cada toma del último trabajo (estado y error exacto). Si algo no genera, copiá esto y pegámelo.</p>
      <button class="ghost" id="btn-debug">Ver diagnóstico</button>
      <pre id="debug-out" style="display:none;white-space:pre-wrap;font-size:13px;background:var(--card-2);border:1px solid var(--line);border-radius:8px;padding:10px;margin-top:8px;user-select:all"></pre>
    </div>
    <div id="gen-out"></div>
  </div>
</section>

<!-- PRODUCTO (product_only) -->
<section class="panel" id="p-producto">
  <div class="card">
    <h2>Solo producto</h2>
    <p class="hint">La prenda sola, sin personas: colgada, tirada en el piso vista desde arriba, flat-lay, doblada, percha o maniquí fantasma. También es el modo para catálogo infantil (niñas/niños).</p>
    <div class="note">Las categorías de niñas/niños van en este modo (solo producto). Es lo que usa el e-commerce infantil y evita generar menores.</div>

    <label>Fotos reales del producto (podés subir varias)</label>
    <div class="dz" id="dz-prod" onclick="document.getElementById('file-prod').click()">
      <div>Tocá para subir una o varias fotos de la prenda</div>
      <input type="file" id="file-prod" accept="image/*" multiple hidden>
    </div>

    <label>Presentación</label>
    <div class="seg" id="modo-prod">
      <div class="opt on" data-modo="flat_lay">Flat-lay</div>
      <div class="opt" data-modo="tirada_piso">Tirada en el piso (cenital)</div>
      <div class="opt" data-modo="doblada">Doblada</div>
      <div class="opt" data-modo="suspendida">Colgada (tanza invisible)</div>
      <div class="opt" data-modo="percha">En percha</div>
      <div class="opt" data-modo="maniqui_fantasma">Maniquí fantasma</div>
    </div>

    <div class="row">
      <div><label>Tela</label><input id="pr-tela" placeholder="algodón / modal..."></div>
      <div><label>Color real</label><input id="pr-color" placeholder="negro, nude..."></div>
    </div>
    <div class="row3">
      <div><label>Puños</label><input id="pr-punos"></div>
      <div><label>Costuras</label><input id="pr-costuras"></div>
      <div><label>Cuello/escote</label><input id="pr-cuello"></div>
    </div>
    <div class="row3">
      <div><label>Fondo / superficie</label><input id="pr-fondo" placeholder="madera, arena, mármol, sábana, liso claro"></div>
      <div><label>Luz</label><input id="pr-luz" placeholder="pareja"></div>
      <div><label>Paneles</label><select id="pr-paneles"><option selected>1</option><option>2</option><option>3</option></select></div>
    </div>
    <label>Aclaraciones / negativos de la prenda</label>
    <input id="pr-aclaraciones" placeholder="ej: sin bolsillos, polar en puños y cuello">
    <div class="chips" id="neg-pr"></div>
    <div class="row">
      <div><label>Aspect</label><select id="pr-aspect">
        <option value="1:1">1:1</option><option value="4:5" selected>4:5</option>
        <option value="3:4">3:4</option><option value="21:9">21:9 (paneles)</option></select></div>
      <div><label>Recortar a</label><select id="pr-reframe">
        <option value="">No</option><option value="1:1">1:1</option>
        <option value="4:5">4:5</option></select></div>
    </div>
    <div class="seg" style="margin-top:10px" id="pr-size">
      <div class="opt on" data-size="4K">4K</div>
      <div class="opt" data-size="2K">2K</div>
      <div class="opt" data-size="1K">1K</div>
    </div>

    <button class="go" id="btn-prod">Generar producto</button>
    <div id="prod-out"></div>
  </div>
</section>

<!-- VARIAR COLOR -->
<section class="panel" id="p-colores">
  <div class="card">
    <h2>Variar color</h2>
    <p class="hint">Si la trama es la misma, no le saques foto a cada color. Subí una foto y pedí el mismo modelo en otros colores. Mantiene molde, trama y detalles — solo cambia el color.</p>

    <label>Foto real del producto (un color)</label>
    <div class="dz" id="dz-col" onclick="document.getElementById('file-col').click()">
      <div>Tocá para subir la foto de la prenda</div>
      <input type="file" id="file-col" accept="image/*" multiple hidden>
    </div>

    <label>Colores que querés (separados por coma)</label>
    <input id="col-colors" placeholder="nude, celeste, gris, negro, verde agua">

    <label>Presentación</label>
    <div class="seg" id="modo-col">
      <div class="opt on" data-modo="suspendida">Colgada (tanza)</div>
      <div class="opt" data-modo="flat_lay">Flat-lay</div>
      <div class="opt" data-modo="percha">En percha</div>
      <div class="opt" data-modo="doblada">Doblada</div>
    </div>

    <div class="row">
      <div><label>Aspect</label><select id="col-aspect">
        <option value="4:5" selected>4:5</option><option value="1:1">1:1</option>
        <option value="3:4">3:4</option></select></div>
      <div><label>Fondo</label><input id="col-fondo" placeholder="liso claro"></div>
    </div>
    <div class="seg" style="margin-top:10px" id="col-size">
      <div class="opt on" data-size="4K">4K</div>
      <div class="opt" data-size="2K">2K</div>
      <div class="opt" data-size="1K">1K</div>
    </div>

    <button class="go" id="btn-col">Generar colores</button>
    <p class="hint" style="margin-top:6px">Cada color es una generación aparte. Ej: 4 colores en 4K = ~US$0,96.</p>
    <div id="col-out"></div>
  </div>
</section>

<!-- EDITOR DE COLOR (100% local, sin IA) -->
<section class="panel" id="p-editor">
  <div class="card">
    <h2>🎨 Editor de color</h2>
    <p class="hint">Cambiá el color de tu prenda al instante, acá mismo en tu dispositivo — gratis, sin IA y sin esperas. Mantiene la TEXTURA REAL: sombras, arrugas, brillos y trama quedan intactos (no pinta un color sólido encima). Ideal para armar la carta de colores con una sola foto.</p>

    <label>Foto de la prenda</label>
    <div class="dz" id="dz-ed" onclick="document.getElementById('file-ed').click()">
      <div>Tocá para subir la foto</div>
      <input type="file" id="file-ed" accept="image/*" hidden>
    </div>
    <div id="ed-from-gen" style="display:none;margin:6px 0"></div>

    <div id="ed-work" style="display:none">
      <label style="margin-top:10px">1) Color que quiero cambiar <span class="q" title="Tocá directamente la prenda EN LA FOTO para tomar su color exacto (cuentagotas). El botón Auto detecta solo el color dominante de la prenda.">?</span></label>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <div id="ed-base-sw" style="width:34px;height:34px;border-radius:8px;border:1px solid var(--line);background:#888"></div>
        <span class="hint" id="ed-base-txt" style="margin:0">tocá la prenda en la foto de abajo</span>
        <button class="ghost" id="ed-auto" style="font-size:13px">✨ Detectar solo</button>
      </div>
      <div class="row" style="margin-top:8px">
        <div><label>Alcance (tolerancia)</label><input type="range" id="ed-tol" min="5" max="60" value="28" style="width:100%"></div>
        <div><label>Suavidad de borde</label><input type="range" id="ed-soft" min="2" max="40" value="14" style="width:100%"></div>
      </div>

      <label style="margin-top:10px">Límite de la prenda <span class="q" title="Si el fondo es del mismo color que la prenda, activá esto: el cambio de color queda encerrado ADENTRO de la silueta de la prenda. Detectar usa IA (centavos). Los pinceles corrigen a mano pintando sobre la foto.">?</span></label>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <button class="ghost" id="ed-detect" style="font-size:13px">🎯 Detectar prenda (IA)</button>
        <button class="ghost" id="ed-brush-add" style="font-size:13px">🖌 Sumar zona</button>
        <button class="ghost" id="ed-brush-del" style="font-size:13px">🧽 Sacar zona</button>
        <input type="range" id="ed-brush-size" min="8" max="90" value="34" style="width:100px" title="Tamaño del pincel">
        <span id="ed-mask-state" class="hint" style="display:none;margin:0">✓ límite activo · <a href="#" id="ed-mask-clear">quitar</a></span>
      </div>

      <label style="margin-top:10px">2) Color nuevo</label>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <input type="color" id="ed-target" value="#c2185b" style="width:48px;height:36px;border:1px solid var(--line);border-radius:8px;background:none;padding:2px">
        <div id="ed-presets" style="display:flex;gap:6px;flex-wrap:wrap"></div>
      </div>
      <div class="row" style="margin-top:8px">
        <div><label>Intensidad del color</label><input type="range" id="ed-sat" min="20" max="140" value="100" style="width:100%"></div>
        <div><label>Más claro / más oscuro</label><input type="range" id="ed-luz" min="-30" max="30" value="0" style="width:100%"></div>
      </div>

      <label class="pk" style="margin-top:8px;display:block"><input type="checkbox" id="ed-mask"> Ver qué zona está agarrando (máscara en rojo)</label>

      <canvas id="ed-canvas" style="width:100%;margin-top:10px;border-radius:12px;border:1px solid var(--line);cursor:crosshair"></canvas>
      <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
        <button class="ghost" id="ed-orig">👁 Mantener apretado: ver original</button>
        <button class="ghost" id="ed-reset">↩ Empezar de nuevo</button>
      </div>
      <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
        <button class="go" id="ed-down" style="flex:1;min-width:150px">⬇ Descargar foto</button>
        <button class="go" id="ed-use" style="flex:1;min-width:150px">➕ Usar en “Generar”</button>
      </div>
      <p class="hint" style="margin-top:6px">La descarga sale en la resolución de tu foto original. “Usar en Generar” la agrega como foto del producto (si ya analizaste la ficha, volvé a tocar “Analizar prenda” para que tome el color nuevo).</p>
    </div>
  </div>
</section>

<!-- AVATARES -->
<section class="panel" id="p-avatares">
  <div class="card">
    <h2>Tus avatares</h2>
    <p class="hint">6 mujeres + 6 hombres. Generás una vez, aprobás y se lockean para reusar siempre la misma modelo.</p>
    <button class="ghost" id="btn-diag" style="margin-bottom:8px">🔧 Diagnóstico de guardado</button>
    <pre id="diag-out" style="display:none;background:#2b1f29;color:#f3e9eb;padding:10px;border-radius:10px;font-size:11px;overflow:auto;white-space:pre-wrap;word-break:break-word"></pre>
    <div style="font-weight:600;font-family:'Bodoni Moda',serif;margin:6px 0">Mujeres</div>
    <div class="grid-av" id="av-mujer"></div>
    <div style="font-weight:600;font-family:'Bodoni Moda',serif;margin:16px 0 6px">Hombres</div>
    <div class="grid-av" id="av-hombre"></div>
  </div>

  <div class="card" id="av-editor" style="display:none">
    <h2 id="av-editor-title">Nuevo avatar</h2>
    <div class="row3">
      <div><label>Nombre</label><input id="av-name" placeholder="ej: Sofi"></div>
      <div><label>Edad (adulta)</label><input id="av-edad" placeholder="25-30"></div>
      <div><label>Tono de piel</label><input id="av-piel" placeholder="trigueña / clara"></div>
    </div>
    <div class="row3">
      <div><label>Pelo</label><input id="av-pelo" placeholder="castaño largo"></div>
      <div><label>Contextura</label><input id="av-contextura" placeholder="delgada / curvy"></div>
      <div><label>Altura</label><input id="av-altura" placeholder="media"></div>
    </div>
    <label>Rasgos / vibe</label>
    <input id="av-rasgos" placeholder="natural, simpática, estilo argentino">
    <p class="hint" style="margin-top:10px">Generá un avatar nuevo desde la descripción, o subí una imagen tuya que ya tengas y te guste (sin costo).</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="go" id="btn-av-gen">Generar candidato</button>
      <button class="ghost" id="btn-av-upload-btn" style="margin-top:16px" onclick="document.getElementById('av-upload').click()">Subir imagen propia</button>
      <button class="ghost" id="btn-av-cancel" style="margin-top:16px">Cancelar</button>
      <input type="file" id="av-upload" accept="image/*" hidden>
    </div>
    <div id="av-preview"></div>
  </div>
</section>

<!-- AJUSTES -->


<section class="panel" id="p-ajustes">
  <div class="card">
    <h2>Google Drive (galería)</h2>
    <p class="hint">Conectá tu Drive y cada imagen generada se guarda sola en tu cuenta (no perdés nada).</p>
    <div id="drive-status">Cargando estado...</div>
  </div>
  <div class="card">
    <h2>Preferencias de generación</h2>
    <p class="hint">En palabras simples. Esto se aplica a todas tus fotos. Los valores por defecto ya andan bien.</p>

    <label>Creatividad <span class="q" title="Qué tanta libertad tiene la IA. Fiel = respeta más la foto real de tu prenda. Variado = más suelta pero puede alejarse.">?</span></label>
    <select id="s-creatividad">
      <option value="0.3">Fiel a la foto (más exacto)</option>
      <option value="0.45" selected>Equilibrado (recomendado)</option>
      <option value="0.7">Más variado (más suelto)</option>
    </select>

    <div class="row">
      <div>
        <label>Formato de la foto <span class="q" title="La forma de la imagen. Vertical 4:5 es la que mejor rinde en Instagram y MercadoLibre.">?</span></label>
        <select id="s-formato">
          <option value="1:1">Cuadrada (feed)</option>
          <option value="4:5" selected>Vertical (recomendado)</option>
          <option value="9:16">Historia / Reel</option>
          <option value="16:9">Horizontal</option>
        </select>
      </div>
      <div>
        <label>Calidad <span class="q" title="2K alcanza de sobra para redes y web, y es más barato. 4K sólo para impresión grande.">?</span></label>
        <select id="s-calidad">
          <option value="2K" selected>2K (redes / web)</option>
          <option value="4K">4K (impresión)</option>
        </select>
      </div>
    </div>

    <label>Estilo de tu marca <span class="q" title="Un texto que le dice a la IA cómo querés que se vean SIEMPRE tus fotos (ej: luz cálida, fondo minimalista, estética natural).">?</span></label>
    <textarea id="s-sys" placeholder="Ej: fotos con luz natural cálida, estética limpia y minimalista, colores fieles"></textarea>

    <div style="border:1px solid var(--rose-deep);border-radius:10px;padding:10px;margin:10px 0;background:var(--card-2)">
      <label style="margin:0">🚀 Motor de imágenes <span class="q" title="Automático (recomendado): la app elige el mejor motor según lo que fotografíes — lencería/ropa interior va a FLUX (permisivo), y pijamas, bikinis, producto e infantil van a Nano Banana (máxima calidad, sin bloqueos para esas categorías). También podés forzar uno solo.">?</span></label>
      <select id="s-engine">
        <option value="auto">Automático — el mejor por categoría (recomendado)</option>
        <option value="gemini">Solo Nano Banana (Google)</option>
        <option value="flux">Solo Seedream (fal.ai) — para probarlo directo</option>
      </select>
      <p class="hint" style="margin:6px 0 0">Automático: lencería/ropa interior → FLUX (sin bloqueos) · pijamas, bikinis, producto, infantil → Nano Banana (máxima calidad). Necesitás la key de fal cargada para la parte de lencería.</p>
      <label style="margin-top:8px">API key de fal.ai <span class="q" title="Creá cuenta gratis en fal.ai → Dashboard → API Keys → creá una y pegala acá. También podés cargarla como variable FAL_KEY en Railway (más seguro).">?</span></label>
      <input id="s-falkey" placeholder="key de fal.ai (o dejá vacío si usás FAL_KEY en Railway)">
      <div class="row">
        <div><label>Modelo try-on (con avatar)</label><input id="s-fluxtryon" placeholder="fal-ai/flux-2-pro/edit"></div>
        <div><label>Modelo sin avatar / producto</label><input id="s-fluxedit" placeholder="fal-ai/flux-2-pro/edit"></div>
      </div>
      <div><label>Precio por imagen FLUX (US$)</label><input id="s-precioflux" type="number" step="0.005" min="0"></div>
      <p class="hint" style="margin:6px 0 0">Con FLUX: el avatar va como "persona" y tus fotos del producto como "prenda" (try-on de verdad). El set de 3 modelos (trío) por ahora sigue en Nano Banana. Si un modelo de fal no existe o cambia de nombre, pegá acá el nuevo (fal.ai → Models).</p>
    </div>

    <div style="border:1px solid var(--rose-deep);border-radius:10px;padding:10px;margin:10px 0;background:var(--card-2)">
      <label style="margin:0">🔍 Inspector de fidelidad de prenda <span class="q" title="Después de cada generación, una IA de visión compara la prenda generada contra tus fotos reales: diseño, color, estampa, terminaciones, detalles inventados. Le pone nota 1-10 y si no llega a la mínima, regenera UNA vez pasándole las correcciones.">?</span></label>
      <div class="row3">
        <div><label>Inspector</label><select id="s-qc"><option value="si">Activado</option><option value="no">Apagado</option></select></div>
        <div><label>Nota mínima (1-10)</label><input id="s-qcumbral" type="number" min="1" max="10"></div>
        <div><label>Auto-corrección</label><select id="s-qcretry"><option value="si">Sí (1 reintento)</option><option value="no">No</option></select></div>
      </div>
      <p class="hint" style="margin:6px 0 0">La inspección cuesta ~US$0,001 por imagen (usa el modelo barato de análisis). Si dispara la auto-corrección, se cobra una imagen más. La nota aparece con cada resultado: "✅ Prenda fiel — 9/10" o "⚠ con diferencias" y el detalle.</p>
      <div style="border-top:1px dashed var(--line);margin:10px 0 8px"></div>
      <label style="margin:0">📝 Borrador barato antes de la final <span class="q" title="Genera primero un borrador en baja resolución (mucho más barato). Si el borrador no pasa la inspección, NO se gasta la imagen final. En modo 'Preguntarme' vos ves el borrador y decidís con un botón si hacer la final, rehacer o saltear.">?</span></label>
      <div class="row">
        <div><label>Modo</label>
          <select id="s-borrador">
            <option value="no">No — directo a la final (como siempre)</option>
            <option value="auto">Auto — corta solo si el borrador no pasa la nota</option>
            <option value="preguntar">Preguntarme — veo el borrador y decido yo</option>
          </select>
        </div>
        <div><label>Resolución del borrador</label>
          <select id="s-borrador-size"><option value="1K">1K (US$0,067)</option><option value="2K">2K (US$0,101)</option></select>
        </div>
      </div>
      <p class="hint" style="margin:6px 0 0">Cuando el borrador se aprueba, la final se genera EN ALTA usando el borrador como guía (misma toma, más detalle). Ejemplo con 4K: borrador 1K US$0,067 + final US$0,151. Si el borrador no va, te ahorrás la final. En "Preguntarme" el set espera tu decisión hasta 20 minutos y sigue con la siguiente toma si no contestás.</p>
    </div>

    <details style="margin:14px 0 4px;border:1px solid var(--line);border-radius:10px;padding:8px 12px;background:var(--card-2)">
      <summary style="cursor:pointer;font-weight:500">⚙️ Ajustes técnicos (avanzado)</summary>
      <p class="hint" style="margin:8px 0">Sólo si sabés lo que hacés. Si no, dejalos como están.</p>
      <div id="flux-notice" class="hint" style="display:none;border:1px solid var(--rose-deep);border-radius:10px;padding:8px 10px;margin-bottom:10px">⚡ Motor FLUX activo: el modelo de imagen, los precios 1K/2K/4K, temperature/top-p/seed y el safety son de Nano Banana y NO aplican. Se ocultaron para no confundir.</div>
      <div id="box-gemini" style="border:1px solid var(--rose-deep);border-radius:10px;padding:10px;margin-bottom:10px;background:var(--card-2)">
        <label>Modelo de imagen <span class="q" title="Cada modelo tiene su propio filtro de seguridad. Si te bloquea las fotos de ropa interior, probá otro modelo: el mismo pedido puede pasar en uno y no en otro.">?</span></label>
        <select id="s-model">
          <option value="gemini-3.1-flash-image">Nano Banana 2 (gemini-3.1-flash-image) — el que usás</option>
          <option value="gemini-3-pro-image">Nano Banana Pro (gemini-3-pro-image) — filtro más estricto</option>
          <option value="gemini-2.5-flash-image">Nano Banana (gemini-2.5-flash-image)</option>
          <option value="gemini-2.0-flash-preview-image-generation">Gemini 2.0 Flash (imagen)</option>
        </select>
        <p class="hint" style="margin:6px 0 0">Si las fotos de ropa interior te dan IMAGE_SAFETY, cambiá acá el modelo y volvé a probar. Es lo mismo que elegir el modelo en AI Studio.</p>
        <label style="margin-top:10px">Precio por imagen (US$) <span class="q" title="Cada modelo cuesta distinto. Poné acá lo que te cobra Google segun tu facturación, para que el presupuesto y los avisos de costo sean exactos.">?</span></label>
        <div class="row3">
          <div><label class="hint" style="margin:0">1K</label><input id="s-p1k" type="number" step="0.001" min="0"></div>
          <div><label class="hint" style="margin:0">2K</label><input id="s-p2k" type="number" step="0.001" min="0"></div>
          <div><label class="hint" style="margin:0">4K</label><input id="s-p4k" type="number" step="0.001" min="0"></div>
        </div>
        <p class="hint" style="margin:6px 0 0">Precios oficiales de Nano Banana 2 (jul 2026): 1K US$0,067 · 2K US$0,101 · 4K US$0,151. Editalos solo si tu facturación difiere.</p>
      </div>
      <div class="row">
        <div><label>Resolución base</label><select id="s-size"><option>1K</option><option selected>2K</option><option>4K</option></select></div>
        <div><label>Aspect base</label><select id="s-aspect"></select></div>
      </div>
      <div class="row3" id="row-gemini-params">
        <div><label>Temperature</label><input id="s-temp" type="number" step="0.05" min="0" max="2"></div>
        <div><label>Top-P</label><input id="s-topp" type="number" step="0.05" min="0" max="1"></div>
        <div><label>Seed (fijo = + repetible)</label><input id="s-seed" placeholder="vacío = aleatorio"></div>
      </div>
      <div class="row3">
        <div><label>Salida</label><select id="s-out"><option value="both">PNG + optimizado</option><option value="png">Solo PNG</option><option value="optimized">Solo optimizado</option></select></div>
        <div><label>Formato optimizado</label><select id="s-ofmt"><option value="jpeg">JPEG</option><option value="webp">WebP</option></select></div>
        <div><label>Calidad opt. (%)</label><input id="s-oq" type="number" min="50" max="100"></div>
      </div>
      <div id="row-gemini-safety"><label>Safety</label><select id="s-safety"><option value="relaxed">Relajado (catálogo íntima)</option><option value="default">Default</option></select></div>
    </details>
    <button class="go" id="btn-save-settings">Guardar ajustes</button>
  </div>

  <div class="card">
    <h2>Tu Google Drive</h2>
    <p class="hint">Tus imágenes se guardan solas en una carpeta “Studio Luma” de <b>tu</b> Drive.</p>
    <div id="drive-status" class="note">Verificando…</div>
    <button class="ghost" id="btn-drive-check" style="margin-top:10px">Verificar de nuevo</button>
    <a class="go" id="btn-drive-reconnect" style="display:inline-block;text-decoration:none;margin-left:8px" href="#">Reconectar Drive</a>
  </div>

  <div class="card">
    <h2>Tu propia API key (facturación)</h2>
    <p class="hint">Por defecto, las imágenes se generan con la cuenta de Studio Luma. Si cargás <b>tu propia API key de Google</b>, generás con ella y <b>Google te factura a vos directamente</b> tu consumo. Si la dejás vacía, se usa la cuenta general.</p>
    <div id="key-status" class="note" style="display:none"></div>
    <label>API key de Google (empieza con AIza…) <span class="q" title="La sacás en Google AI Studio → Get API key. Se guarda de forma privada en tu cuenta y no se comparte.">?</span></label>
    <input id="my-key" type="password" placeholder="AIza… (dejala vacía para usar la cuenta general)">
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="go" id="btn-save-key">Guardar mi key</button>
      <button class="ghost" id="btn-clear-key">Quitar mi key</button>
    </div>
  </div>
</section>

<!-- PRESUPUESTO -->
<section class="panel" id="p-presupuesto">
  <div class="card">
    <h2>Presupuesto del mes</h2>
    <div class="kv"><span>Gastado este mes</span><b id="b-total">—</b></div>
    <div class="kv"><span>Imágenes generadas</span><b id="b-assets">—</b></div>
    <div class="kv"><span>Tope mensual</span><b id="b-cap">—</b></div>
    <label>Tope mensual (US$, vacío = sin tope)</label>
    <div style="display:flex;gap:8px">
      <input id="cap-input" type="number" step="1" placeholder="ej: 50">
      <button class="ghost" id="btn-cap" style="white-space:nowrap">Guardar tope</button>
    </div>
  </div>
  <div class="card">
    <h2>Movimientos</h2>
    <div id="led-rows"></div>
  </div>
</section>

</main>
<div class="toast" id="toast"></div>
<script>
const BASE = "%%PREFIX%%";
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
let SETTINGS = {};
let GEN_PRODUCTS = [], PROD_PRODUCTS = [], COL_PRODUCTS = [], GEN_AVATAR_ID = null;
let GEN_PRODUCTS_BACK = [];
let GEN_FICHA = "";
let GEN_SIZE = "2K", PROD_SIZE = "4K", PROD_MODO = "flat_lay";
let AV_CTX = null, AV_CANDIDATE = null;

function errMsg(e){const m=(e&&e.message)||String(e);return /failed to fetch|network|load failed/i.test(m)?"Se cortó la conexión (la imagen puede haberse generado igual, revisá Drive). Probá de nuevo.":m;}
function toast(msg, bad){const t=$("#toast");t.textContent=msg;t.className="toast"+(bad?" bad":"");t.style.display="block";setTimeout(()=>t.style.display="none",bad?5200:2600);}
function genStatus(sel,msg,bad){let c=$(sel);let s=c.querySelector(".genstatus");if(!s){s=document.createElement("div");s.className="genstatus";c.insertAdjacentElement("afterbegin",s);}s.style.cssText="margin:8px 0;padding:9px 12px;border-radius:9px;font-size:13px;font-weight:600;"+(bad?"background:#fbecec;color:var(--bad)":"background:#eef6f0;color:var(--ok)");s.textContent=msg;}
function clearStatus(sel){const s=$(sel).querySelector(".genstatus");if(s)s.remove();const p=$(sel).querySelector(".progwrap");if(p)p.remove();}

// Barra de progreso por etapas (la IA es caja negra, así que avanzamos por pasos + creep)
function makeProgress(sel){
  clearStatus(sel);
  const c=$(sel);
  const w=document.createElement("div");w.className="progwrap";
  w.style.cssText="margin:8px 0;background:var(--card-2);border:1px solid var(--line);border-radius:10px;padding:10px 12px";
  w.innerHTML='<div class="plabel" style="font-size:13px;font-weight:600;color:var(--ink);margin-bottom:6px">Iniciando...</div>'+
    '<div style="height:10px;background:#e7dcdf;border-radius:99px;overflow:hidden"><div class="pbar" style="height:100%;width:0%;background:linear-gradient(90deg,var(--rose),var(--rose-deep));border-radius:99px"></div></div>'+
    '<div class="ppct" style="font-size:11px;color:var(--ink-soft);text-align:right;margin-top:4px">0%</div>';
  c.insertAdjacentElement("afterbegin",w);
  const bar=w.querySelector(".pbar"),pct=w.querySelector(".ppct"),lab=w.querySelector(".plabel");
  let cur=0,target=0;
  function paint(){bar.style.width=cur.toFixed(1)+"%";pct.textContent=Math.round(cur)+"%";}
  const timer=setInterval(()=>{const ceil=Math.min(target,99);if(cur<ceil){cur+=Math.max(0.15,(ceil-cur)*0.03);if(cur>ceil)cur=ceil;paint();}},150);
  return {
    set(t,label){target=t;if(label)lab.textContent=label;paint();},
    bump(t){if(cur<t){cur=t;if(target<t)target=t;paint();}},
    done(label){clearInterval(timer);cur=100;target=100;paint();if(label)lab.textContent=label;setTimeout(()=>{if(w.parentNode)w.remove();},1000);},
    fail(msg){clearInterval(timer);bar.style.background="var(--bad)";lab.style.color="var(--bad)";lab.textContent="❌ "+msg;}
  };
}
async function runStep(prog,from,to,label,doFetch){prog.bump(from);prog.set(to-3,label);const r=await doFetch();prog.bump(to);return r;}
async function jget(u){const r=await fetch(BASE+u);if(!r.ok)throw new Error((await r.json()).detail||r.status);return r.json();}
// Arranca la generación (job) y consulta hasta que termina. Así no se corta por timeout.
let CURRENT_JOB=null;
let SET_RESULTS=[];  // optimized data-URLs de las imágenes del último set/lote (para regenerar tomas)
async function startJob(endpoint,payload){const s=await jpost(endpoint,payload);if(!s||!s.job_id)throw new Error("No se pudo iniciar la generación");return s.job_id;}
// Compat: arranca un job de 1 imagen y devuelve el resultado final (lo usan Producto y Variar color).
async function runGenerate(payload){
  const jid=await startJob("/api/generate",payload);
  let unknownSince=0;const t0=Date.now();
  while(true){
    await new Promise(s=>setTimeout(s,2500));
    let st;try{st=await jget("/api/jobs/"+jid+"?t="+Date.now());}catch(e){continue;}
    if(st.status==="unknown"){if(!unknownSince)unknownSince=Date.now();if(Date.now()-unknownSince>30000)throw new Error("Se perdió el trabajo. Reintentá.");continue;}
    unknownSince=0;
    if(st.status==="error")throw new Error(st.error||"Error en la generación");
    if(st.status==="done"){const r=await jget("/api/jobs/"+jid+"/result/0?t="+Date.now());if(r&&r.assets)return r;throw new Error("Sin resultado");}
    if(Date.now()-t0>360000)throw new Error("Tardó demasiado (6 min). Probá en 2K.");
  }
}
// Sigue un job (single o set) por su estado en el server. Renderiza cada imagen apenas está lista.
// Sobrevive refrescos: se puede llamar de nuevo con el mismo jid y retoma desde donde iba.
async function pollJob(jid,prog,rendered){
  CURRENT_JOB=jid; rendered=rendered||0;
  const t0=Date.now();let unknownSince=0;
  async function drain(upto){
    while(rendered<upto){
      try{const r=await jget("/api/jobs/"+jid+"/result/"+rendered+"?t="+Date.now());
        if(r&&r.status==="done"&&r.assets){renderResults("#gen-out",r);
          for(const a of r.assets){if(a.optimized)SET_RESULTS.push(a.optimized);}}
        else if(r&&(r.status==="blocked"||r.blocked)){blockedNote(rendered,r.error);}}catch(e){}
      rendered++;
    }
  }
  function blockedNote(idx,err){
    const c=document.querySelector("#gen-out");if(!c)return;
    const d=document.createElement("div");
    d.style.cssText="border:1px solid var(--bad);border-radius:10px;padding:10px 12px;margin:8px 0;color:var(--bad)";
    d.textContent="⚠️ La toma "+(idx+1)+" no salió: "+(err||"la bloqueó el filtro de imágenes")+" — al final el experto te va a ofrecer reintentarla.";
    c.appendChild(d);
  }
  let waitShown=-1;
  while(true){
    await new Promise(s=>setTimeout(s,2500));
    if(ABORT_POLL){ABORT_POLL=false;if(prog)prog.fail("Saliste — sigue en el server, lo ves al recargar");CURRENT_JOB=null;return null;}
    let st;try{st=await jget("/api/jobs/"+jid+"?t="+Date.now());}catch(e){continue;}
    if(st.status==="unknown"){if(!unknownSince)unknownSince=Date.now();
      if(Date.now()-unknownSince>30000){if(prog)prog.fail("Se perdió el trabajo");CURRENT_JOB=null;throw new Error("Se perdió el trabajo");}continue;}
    unknownSince=0;
    if(st.status==="waiting_ok"&&st.wait_idx!==undefined){
      if(waitShown!==st.wait_idx){waitShown=st.wait_idx;await showPreviewDecision(jid,st.wait_idx);}
      if(prog)prog.set(50,"Esperando tu decisión sobre el borrador de la toma "+(st.wait_idx+1)+"...");
      continue;
    }
    if(waitShown>=0){const pv=document.getElementById("preview-box-"+waitShown);if(pv)pv.remove();waitShown=-1;}
    const total=st.total||1,done=st.done||0;
    await drain(done);
    if(prog){const pct=Math.max(2,Math.round((done/total)*100));prog.set(Math.min(98,pct),"Generando "+done+"/"+total+"...");}
    if(st.status==="done"){await drain(total);if(prog){const sk=(st.skipped&&st.skipped.length)?(" — "+st.skipped.length+" toma(s) no salieron; el experto las está revisando…"):"";prog.done("Listo ✓"+sk);}CURRENT_JOB=null;
      return st;}
    if(st.status==="stopped"){if(prog)prog.fail("Frenado — no se generó el resto");CURRENT_JOB=null;return st;}
    if(st.status==="error"){if(prog)prog.fail(st.error||"Error");CURRENT_JOB=null;throw new Error(st.error||"Error");}
    if(Date.now()-t0>900000){if(prog)prog.fail("Tardó demasiado");CURRENT_JOB=null;throw new Error("timeout");}
  }
}
async function showPreviewDecision(jid,idx){
  let pv;try{pv=await jget("/api/jobs/"+jid+"/preview/"+idx+"?t="+Date.now());}catch(e){return;}
  if(!pv||!pv.preview)return;
  const c=document.querySelector("#gen-out");if(!c)return;
  const old=document.getElementById("preview-box-"+idx);if(old)old.remove();
  const d=document.createElement("div");
  d.id="preview-box-"+idx;
  d.style.cssText="border:2px solid var(--rose-deep);border-radius:12px;padding:12px;margin:10px 0;background:var(--card-2)";
  let qtxt="";
  if(pv.qc&&pv.qc.puntaje){
    const ok=pv.qc.puntaje>=7;
    qtxt='<div style="font-size:12px;font-weight:600;color:'+(ok?"var(--ok)":"var(--bad)")+';margin:6px 0">'
      +(ok?"✅":"⚠")+" Fidelidad del borrador: "+pv.qc.puntaje+"/10</div>";
    if((pv.qc.diferencias||[]).length)qtxt+='<div class="hint" style="margin:2px 0 6px">'+pv.qc.diferencias.map(x=>"• "+x).join("<br>")+'</div>';
  }
  d.innerHTML='<div style="font-weight:600;margin-bottom:6px">👀 Borrador de la toma '+(idx+1)+' — ¿la hago en calidad final?</div>'
    +'<img src="'+pv.preview+'" style="max-width:100%;border-radius:8px">'+qtxt
    +'<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">'
    +'<button class="go" style="flex:1" onclick="decideDraft(\''+jid+'\','+idx+',\'ok\')">✅ Sí, hacela en final</button>'
    +'<button class="ghost" onclick="decideDraft(\''+jid+'\','+idx+',\'rehacer\')">🔧 Corregir errores y rehacer</button>'
    +'<button class="ghost" onclick="decideDraft(\''+jid+'\','+idx+',\'saltear\')">⏭ Saltear</button></div>';
  c.appendChild(d);
  d.scrollIntoView({behavior:"smooth",block:"center"});
}
async function decideDraft(jid,idx,decision){
  const box=document.getElementById("preview-box-"+idx);
  if(box)box.querySelectorAll("button").forEach(b=>b.disabled=true);
  try{await jpost("/api/jobs/"+jid+"/decide",{idx:idx,decision:decision});
    toast(decision==="ok"?"Generando la final en alta...":(decision==="rehacer"?"Rehaciendo el borrador...":"Toma salteada"));
  }catch(e){toast(errMsg(e),true);if(box)box.querySelectorAll("button").forEach(b=>b.disabled=false);}
}
async function jpost(u,b,retries){retries=(retries===undefined)?1:retries;
  try{
    const r=await fetch(BASE+u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});
    if(!r.ok){let d;try{d=(await r.json()).detail;}catch(e){d=r.status;}throw new Error(d||r.status);}
    return r.json();
  }catch(e){
    // Solo reintentamos cortes de red (Failed to fetch), no errores del servidor
    if(retries>0 && /failed to fetch|network|load failed/i.test(e.message||"")){
      await new Promise(s=>setTimeout(s,1500));
      return jpost(u,b,retries-1);
    }
    throw e;
  }
}
async function jdel(u){const r=await fetch(BASE+u,{method:"DELETE"});return r.json();}
// ---- Chips de negativos (tocás y se suman al campo) ----
const NEGS=[
  ["Tela lisa","tela lisa con estampa impresa, sin relieve"],
  ["Sin tejido cable","sin tejido cable ni trenzado ni matelasseado"],
  ["No inventar textura","no inventar textura que la foto no tiene"],
  ["Estampa nítida","estampa nítida y reconocible, sin borronear el dibujo"],
  ["Mangas rectas","mangas rectas sin puño ni elástico en la muñeca"],
  ["Pantalón recto","pantalón recto sin puño ni elástico en el tobillo"],
  ["Hasta el borde","manga y pantalón del mismo género estampado hasta el borde"],
  ["Sin bolsillos","sin bolsillos"],
  ["Sin capucha","sin capucha"],
  ["Sin cierre/botones","sin cierre ni botones"],
  ["Sin cuello alto","sin cuello alto, escote redondo simple"],
  ["Calce holgado","calce holgado/oversize como la prenda real"],
];
function addNeg(inputId,text){const el=$("#"+inputId);const cur=(el.value||"").trim();
  const parts=cur?cur.split(",").map(s=>s.trim()).filter(Boolean):[];
  if(parts.some(p=>p.toLowerCase()===text.toLowerCase()))return; // no duplicar
  parts.push(text);el.value=parts.join(", ");}
function renderChips(sel,inputId){const c=$(sel);if(!c)return;c.innerHTML="";
  NEGS.forEach(n=>{const b=document.createElement("button");b.type="button";b.className="chip";
    b.textContent="+ "+n[0];b.onclick=()=>addNeg(inputId,n[1]);c.appendChild(b);});}
renderChips("#neg-g","g-aclaraciones");renderChips("#neg-pr","pr-aclaraciones");

function fileToDataURL(f){return new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(r.result);r.onerror=rej;r.readAsDataURL(f);});}
function downscaleImage(file,maxDim){return new Promise((res,rej)=>{const img=new Image();img.onload=()=>{try{URL.revokeObjectURL(img.src);}catch(e){}let w=img.naturalWidth,h=img.naturalHeight;if(Math.max(w,h)>maxDim){const s=maxDim/Math.max(w,h);w=Math.round(w*s);h=Math.round(h*s);}const c=document.createElement("canvas");c.width=w;c.height=h;c.getContext("2d").drawImage(img,0,0,w,h);res(c.toDataURL("image/jpeg",0.9));};img.onerror=()=>rej(new Error("No se pudo leer la imagen (formato no soportado)"));img.src=URL.createObjectURL(file);});}
function downscaleDataURL(dataurl,maxDim){return new Promise((res,rej)=>{const img=new Image();img.onload=()=>{let w=img.naturalWidth,h=img.naturalHeight;if(Math.max(w,h)>maxDim){const s=maxDim/Math.max(w,h);w=Math.round(w*s);h=Math.round(h*s);}const c=document.createElement("canvas");c.width=w;c.height=h;c.getContext("2d").drawImage(img,0,0,w,h);res(c.toDataURL("image/jpeg",0.85));};img.onerror=()=>rej(new Error("anchor"));img.src=dataurl;});}
// Toma los paneles de un resultado y los deja como anclas livianas (1024px)
async function buildAnchors(r){const out=[];for(const a of (r.assets||[])){const src=a.optimized||a.png;if(!src)continue;try{out.push(await downscaleDataURL(src,1024));}catch(e){}}return out;}

// Tabs
$$("#tabs .tab").forEach(t=>t.onclick=()=>{
  $$("#tabs .tab").forEach(x=>x.classList.remove("on"));t.classList.add("on");
  $$(".panel").forEach(p=>p.classList.remove("on"));$("#p-"+t.dataset.p).classList.add("on");
  if(t.dataset.p==="presupuesto")loadBudget();
  if(t.dataset.p==="avatares")loadAvatars();
  if(t.dataset.p==="generar")loadGenAvatars();
  if(t.dataset.p==="ajustes")loadDrive();
  if(t.dataset.p==="editor")edSyncFromGen();
});

// Segmented helpers
function segWire(sel,attr,cb){$$(sel+" .opt").forEach(o=>o.onclick=()=>{$$(sel+" .opt").forEach(x=>x.classList.remove("on"));o.classList.add("on");cb(o.dataset[attr]);});}
segWire("#p-generar .seg","size",v=>GEN_SIZE=v);
segWire("#pr-size","size",v=>PROD_SIZE=v);
segWire("#modo-prod","modo",v=>PROD_MODO=v);
let COL_SIZE="4K", COL_MODO="suspendida";
segWire("#modo-col","modo",v=>COL_MODO=v);
segWire("#col-size","size",v=>COL_SIZE=v);

// File pickers
async function readFiles(files){const out=[];for(const f of files){out.push(await fileToDataURL(f));}return out;}
function thumbs(arr){return '<div>Listo ✓ ('+arr.length+')</div>'+arr.map(u=>'<img src="'+u+'" style="max-height:90px;margin:4px;border-radius:6px">').join('');}
$("#file-gen").onchange=async e=>{if(!e.target.files.length)return;GEN_PRODUCTS=await readFiles(e.target.files);$("#dz-gen").innerHTML=thumbs(GEN_PRODUCTS);GEN_FICHA="";$("#ficha-box").style.display="none";$("#ficha-status").textContent="";};
$("#file-gen-back").onchange=async e=>{if(!e.target.files.length)return;GEN_PRODUCTS_BACK=await readFiles(e.target.files);$("#dz-gen-back").innerHTML=thumbs(GEN_PRODUCTS_BACK);};
$("#btn-analyze").onclick=async()=>{
  if(!GEN_PRODUCTS.length)return toast("Subí primero las fotos de la prenda.",true);
  const b=$("#btn-analyze");b.disabled=true;const old=b.textContent;b.textContent="Analizando...";
  $("#ficha-status").textContent="leyendo la prenda...";
  try{
    // Achico las fotos antes de subir (más rápido en celu)
    let imgs=[];
    for(const p of GEN_PRODUCTS){try{imgs.push(await downscaleDataURL(p,1280));}catch(e){imgs.push(p);}}
    // La foto de espalda también viaja al análisis (rotulada aparte): así la ficha
    // describe la espalda real y las tomas de espalda no inventan nada.
    let backs=[];
    for(const p of (GEN_PRODUCTS_BACK||[])){try{backs.push(await downscaleDataURL(p,1280));}catch(e){backs.push(p);}}
    const r=await jpost("/api/analyze",{product_images:imgs,product_images_back:backs});
    GEN_FICHA=r.ficha_text||"";
    const f=r.ficha||{};
    // autocompletar campos visibles si están vacíos
    const setIf=(id,v)=>{const el=$(id);if(el&&v&&!el.value)el.value=v;};
    setIf("#g-tela",f.tela);
    setIf("#g-color",f.color_base);
    setIf("#g-cuello",f.cuello);
    setIf("#g-punos",f.punos);
    setIf("#g-costuras",f.costuras_detalles);
    // Los "errores a evitar" ya NO se suman a tus aclaraciones (se acumulaban entre
    // productos distintos y contaminaban el prompt): ahora viajan dentro de la ficha.
    $("#ficha-box").style.display="";
    $("#ficha-box").textContent=GEN_FICHA||"(sin texto)";
    $("#ficha-status").textContent="✓ ficha cargada — se usa en cada generación";
    toast("Ficha técnica lista ✓");
  }catch(e){$("#ficha-status").textContent="";toast(errMsg(e),true);}
  b.disabled=false;b.textContent=old;
};
$("#file-prod").onchange=async e=>{if(!e.target.files.length)return;PROD_PRODUCTS=await readFiles(e.target.files);$("#dz-prod").innerHTML=thumbs(PROD_PRODUCTS);};
$("#file-col").onchange=async e=>{if(!e.target.files.length)return;COL_PRODUCTS=await readFiles(e.target.files);$("#dz-col").innerHTML=thumbs(COL_PRODUCTS);};

// ---- Avatares ----
async function loadAvatars(){
  const data=await jget("/api/avatars?t="+Date.now());
  ["mujer","hombre"].forEach(g=>{
    const cont=$("#av-"+g);cont.innerHTML="";
    data[g].forEach((av,slot)=>{
      const d=document.createElement("div");d.className="slot"+(av?" filled":"");
      if(av){
        d.innerHTML='<img src="'+BASE+"/api/avatars/"+av.id+'/ref?t='+Date.now()+'" loading="lazy" onerror="this.closest(\'.slot\').classList.add(\'noref\')"><div class="meta"><span>'+av.name+'</span><span data-del="'+av.id+'">✕</span></div>';
        d.querySelector("[data-del]").onclick=async ev=>{ev.stopPropagation();if(confirm("¿Borrar este avatar?")){await jdel("/api/avatars/"+av.id);loadAvatars();}};
        d.onclick=()=>openAvatarEditor(g,slot,av);
      }else{
        d.innerHTML='<div class="plus">＋</div><div class="lbl">'+g+" "+(slot+1)+"</div>";
        d.onclick=()=>openAvatarEditor(g,slot,null);
      }
      cont.appendChild(d);
    });
  });
}
function openAvatarEditor(gender,slot,av){
  AV_CTX={gender,slot};AV_CANDIDATE=null;
  $("#av-editor").style.display="block";
  $("#av-editor-title").textContent=(av?"Editar":"Nuevo")+" · "+gender+" "+(slot+1);
  $("#av-name").value=av?av.name:"";
  $("#av-preview").innerHTML="";
  $("#av-editor").scrollIntoView({behavior:"smooth"});
}
$("#btn-av-cancel").onclick=()=>{$("#av-editor").style.display="none";};

function showAvatarCandidate(preview, ref_b64){
  AV_CANDIDATE=ref_b64;
  $("#av-preview").innerHTML='<label>Candidato — ¿lo aprobás?</label><img src="'+preview+'" style="max-width:220px;border-radius:12px;border:1px solid var(--line)"><div style="margin-top:8px"><button class="go" id="btn-av-lock" style="margin:0">Aprobar y lockear</button></div>';
  $("#btn-av-lock").onclick=async()=>{
    const lb=$("#btn-av-lock");lb.disabled=true;lb.textContent="Guardando...";
    try{
      const res=await jpost("/api/avatars/lock",{gender:AV_CTX.gender,slot:AV_CTX.slot,ref_b64:AV_CANDIDATE,name:$("#av-name").value||AV_CTX.gender+" "+(AV_CTX.slot+1)});
      toast("Avatar guardado ✓ ("+res.backend+")");$("#av-editor").style.display="none";loadAvatars();
    }catch(e){toast("No se guardó: "+e.message,true);lb.disabled=false;lb.textContent="Aprobar y lockear";}
  };
}

$("#btn-av-gen").onclick=async()=>{
  const b=$("#btn-av-gen");b.disabled=true;b.textContent="Generando...";
  try{
    const attrs={edad:$("#av-edad").value,piel:$("#av-piel").value,pelo:$("#av-pelo").value,
      contextura:$("#av-contextura").value,altura:$("#av-altura").value,rasgos:$("#av-rasgos").value};
    const r=await jpost("/api/avatars/generate",{gender:AV_CTX.gender,attrs});
    showAvatarCandidate(r.preview, r.ref_b64);
  }catch(e){toast(e.message,true);}
  b.disabled=false;b.textContent="Generar candidato";
};

$("#av-upload").onchange=async e=>{
  const f=e.target.files[0];if(!f)return;
  toast("Procesando imagen...");
  try{
    const img=await downscaleImage(f,1600);
    const r=await jpost("/api/avatars/from_upload",{image:img});
    showAvatarCandidate(r.preview, r.ref_b64);
    toast("Imagen lista — aprobala para guardarla");
  }catch(err){toast("No se pudo cargar: "+err.message,true);}
  e.target.value="";
};

async function loadGenAvatars(){
  const data=await jget("/api/avatars?t="+Date.now());const cont=$("#gen-avatars");cont.innerHTML="";
  let any=false;
  ["mujer","hombre"].forEach(g=>data[g].forEach(av=>{
    if(!av)return;any=true;
    const d=document.createElement("div");d.className="slot filled";
    d.innerHTML='<img src="'+BASE+"/api/avatars/"+av.id+'/ref?t='+Date.now()+'" loading="lazy"><div class="meta"><span>'+av.name+'</span></div>';
    d.onclick=()=>{GEN_AVATAR_ID=av.id;GEN_AVATAR_GENDER=g;$$("#gen-avatars .slot").forEach(x=>x.style.outline="");d.style.outline="3px solid var(--rose)";applyGenderUI();};
    cont.appendChild(d);
  }));
  if(!any)cont.innerHTML='<p class="hint">Todavía no tenés avatares. Creálos en la pestaña Avatares.</p>';
  renderTrioCards(data.mujer||[]);
}
const COLOR_PH=["blanco","negro","nude"];
function renderTrioCards(mujeres){
  const cards=document.getElementById("trio-cards");if(!cards)return;
  const avOpts='<option value="">(modelo IA)</option>'+
    mujeres.filter(Boolean).map(av=>'<option value="'+av.id+'">'+av.name+'</option>').join("");
  const cont='<option value="">(cuerpo)</option><option value="delgada">Delgada</option><option value="atletica">Atlética</option><option value="curvy">Curvy</option><option value="talle_grande">Talle grande</option><option value="talle_extra_grande">Talle XXL</option>';
  const busto='<option value="">(busto)</option><option value="chico">Chico</option><option value="mediano">Mediano</option><option value="grande">Grande</option><option value="extra_grande">XXL</option>';
  const cola='<option value="">(cola)</option><option value="chica">Chica</option><option value="mediana">Mediana</option><option value="grande">Grande</option><option value="extra_grande">XXL</option>';
  const abd='<option value="">(abdomen)</option><option value="fit">Fit</option><option value="plano">Plano</option><option value="natural">Natural</option><option value="con_pancita">Con pancita</option>';
  const edad='<option value="">(edad)</option><option value="20">~20</option><option value="30">~30</option><option value="40">~40</option><option value="50">~50</option>';
  const altura='<option value="">(altura)</option><option value="baja">Baja ~1,55</option><option value="media">Media ~1,65</option><option value="alta">Alta ~1,75</option><option value="muy_alta">Muy alta 1,80+</option>';
  const posesel='<option value="frente">De frente</option><option value="perfil">De perfil</option><option value="espalda">De espalda</option><option value="sentada">Sentada</option><option value="caminando">Caminando</option>';
  const etnia='<option value="">(etnia IA)</option><option value="latina">Latina</option><option value="caucasica">Caucásica</option><option value="morocha_tez_oscura">Trigueña</option><option value="afro">Afro</option><option value="asiatica">Asiática</option><option value="mediterranea">Mediterránea</option><option value="mestiza">Mestiza</option>';
  const pelo='<option value="">(pelo IA)</option><option value="rubia">Rubia</option><option value="castaño_largo">Castaño largo</option><option value="morocha_largo_ondulado">Morocha ondulado</option><option value="negro_lacio">Negro lacio</option><option value="pelirroja">Pelirroja</option><option value="corto">Corto</option>';
  const POSE_PH=["frente","perfil","espalda"];
  [0,1,2].forEach(i=>{
    const c=cards.querySelector('.tcard[data-i="'+i+'"]');if(!c)return;
    const keep=id=>{const e=document.getElementById(id);return e?e.value:"";};
    const v={av:keep("g-tav"+i),col:keep("g-tcol"+i)||COLOR_PH[i],cue:keep("g-tcue"+i),
      ed:keep("g-ted"+i),et:keep("g-tet"+i),pe:keep("g-tpe"+i),
      bu:keep("g-tbu"+i),co:keep("g-tco"+i),ab:keep("g-tab"+i),al:keep("g-tal"+i),
      po:keep("g-tpo"+i)||POSE_PH[i],ind:keep("g-tind"+i)};
    c.innerHTML=
      '<div style="font-weight:600;margin:10px 0 4px;color:var(--rose-deep)">Modelo '+(i+1)+'</div>'+
      '<div class="row"><div><label>Avatar</label><select id="g-tav'+i+'">'+avOpts+'</select></div>'+
      '<div><label>Color</label><input id="g-tcol'+i+'" placeholder="'+COLOR_PH[i]+'"></div></div>'+
      '<div class="row"><div><label>Pose de su foto</label><select id="g-tpo'+i+'">'+posesel+'</select></div>'+
      '<div><label>Cuerpo</label><select id="g-tcue'+i+'">'+cont+'</select></div></div>'+
      '<div class="row3"><div><label>Busto</label><select id="g-tbu'+i+'">'+busto+'</select></div>'+
      '<div><label>Cola</label><select id="g-tco'+i+'">'+cola+'</select></div>'+
      '<div><label>Abdomen</label><select id="g-tab'+i+'">'+abd+'</select></div></div>'+
      '<div class="row3"><div><label>Edad</label><select id="g-ted'+i+'">'+edad+'</select></div>'+
      '<div><label>Altura</label><select id="g-tal'+i+'">'+altura+'</select></div>'+
      '<div><label>Etnia (IA)</label><select id="g-tet'+i+'">'+etnia+'</select></div></div>'+
      '<div class="row"><div><label>Pelo (IA)</label><select id="g-tpe'+i+'">'+pelo+'</select></div>'+
      '<div style="display:flex;align-items:flex-end"><button class="ghost tsave" data-i="'+i+'" style="width:100%">💾 Guardar ficha del avatar</button></div></div>'+
      '<div><label>Indicaciones para su foto (opcional, texto libre)</label>'+
      '<input id="g-tind'+i+'" placeholder="ej: brazos cruzados, media risa, mirando por la ventana"></div>'+
      '<button class="go tgen" data-i="'+i+'" style="margin-top:8px;width:100%">▶ Generar SOLO esta toma</button>'+
      '<label class="pk" style="margin-top:6px"><input type="checkbox" class="tmin" data-i="'+i+'"> ✅ Prompt simple (el que te funciona en el Playground)</label>'+
      '<label class="pk" style="margin-top:4px"><input type="checkbox" class="tedit" data-i="'+i+'"> ✏️ Ver y editar el prompt antes de generar</label>'+
      ''+
      '<p class="hint" style="margin:4px 0 0">Manda un pedido corto tipo Playground: quién es la modelo, cuerpo entero, escenario y prenda. Sin medidas de cuerpo ni encuadres cerrados.</p>';
    ["g-tav","g-tcol","g-tcue","g-ted","g-tet","g-tpe","g-tbu","g-tco","g-tab","g-tal","g-tpo","g-tind"].forEach((p,j)=>{
      const vals=[v.av,v.col,v.cue,v.ed,v.et,v.pe,v.bu,v.co,v.ab,v.al,v.po,v.ind];
      const el=document.getElementById(p+i);if(el)el.value=vals[j];
    });
    const avSel=document.getElementById("g-tav"+i);
    if(avSel)avSel.addEventListener("change",()=>fichaLoad(i));
    const sv=c.querySelector(".tsave");
    if(sv)sv.onclick=()=>fichaSave(i);
    const gn=c.querySelector(".tgen");
    if(gn)gn.onclick=()=>genUnaToma(i);
  });
}
const POSE_MAP={frente:0,perfil:6,espalda:3,sentada:2,caminando:4};
function editarPrompt(txt, nimg, modelo){
  return new Promise(resolve=>{
    $("#pm-text").value=txt;
    $("#pm-info").textContent=txt.length+" caracteres · "+nimg+" imagen(es) de referencia · motor: "+(modelo||"?")+
      " — editalo como quieras; se envía EXACTAMENTE lo que dejes acá.";
    $("#pm-modal").style.display="flex";
    const cerrar=()=>{$("#pm-modal").style.display="none";
      $("#pm-go").onclick=null;$("#pm-cancel").onclick=null;$("#pm-copy").onclick=null;};
    $("#pm-go").onclick=()=>{const v=$("#pm-text").value.trim();cerrar();resolve(v||txt);};
    $("#pm-cancel").onclick=()=>{cerrar();resolve(null);};
    $("#pm-copy").onclick=()=>{
      const t=$("#pm-text");t.select();
      try{document.execCommand("copy");toast("Prompt copiado ✓");}catch(e){toast("Copialo a mano",true);}
    };
  });
}
async function genUnaToma(i){
  if(!GEN_PRODUCTS.length)return toast("Subí al menos una foto del producto.",true);
  const col=(($("#g-tcol"+i)||{}).value||"").trim();
  if(!col)return toast("Poné el color de esta modelo.",true);
  const minChk=document.querySelector('.tmin[data-i="'+i+'"]');
  const minimo=!!(minChk&&minChk.checked);
  const all=gatherAsign();
  const it=all.find(x=>x.color===col)||{};
  const fp=POSE_MAP[it.pose||"frente"]||0;
  const isBack=(fp===3);
  let prods=(isBack&&GEN_PRODUCTS_BACK.length)?GEN_PRODUCTS_BACK:GEN_PRODUCTS;
  const gp=genParams();
  const params=minimo?{color_set:col,modo_minimo:"si",
                       complemento:gp.complemento,
                       ap_etnia:(it.etnia||gp.ap_etnia||""),ap_pelo:(it.pelo||gp.ap_pelo||""),
                       cuerpo_edad:(it.edad||""),cuerpo_contextura:(it.contextura||""),
                       fondo:gp.fondo,tela:gp.tela,aclaraciones:gp.aclaraciones}
                     :{...gp,color_set:col};
  if(!minimo){
    if(it.contextura)params.cuerpo_contextura=it.contextura;
    if(it.busto)params.cuerpo_busto=it.busto;
    if(it.cola)params.cuerpo_cola=it.cola;
    if(it.abdomen)params.cuerpo_abdomen=it.abdomen;
    if(it.edad)params.cuerpo_edad=it.edad;
    if(it.altura)params.cuerpo_altura=it.altura;
    if(!it.avatar_id){if(it.etnia)params.ap_etnia=it.etnia;if(it.pelo)params.ap_pelo=it.pelo;}
    if(it.indicacion){const prev=(params.aclaraciones||"").trim();
      params.aclaraciones=(prev?prev+" ":"")+"DIRECCIÓN DE ESTA TOMA (seguila): "+it.indicacion;}
  }
  const btn=document.querySelector('.tgen[data-i="'+i+'"]');
  const editChk=document.querySelector('.tedit[data-i="'+i+'"]');
  const payload={mode:"on_model",avatar_id:it.avatar_id||null,product_images:prods,
    aspect:"4:5",paneles:1,image_size:GEN_SIZE,reframe:null,style:$("#g-style").value,
    force_pose:fp,no_face_recreate:!!it.avatar_id,params:params};
  if(editChk&&editChk.checked){
    if(btn){btn.disabled=true;btn.textContent="Armando el prompt…";}
    let pv;
    try{ pv=await jpost("/api/preview_prompt",payload); }
    catch(e){ if(btn){btn.disabled=false;btn.textContent="▶ Generar SOLO esta toma";}
              return toast(errMsg(e),true); }
    if(btn){btn.disabled=false;btn.textContent="▶ Generar SOLO esta toma";}
    const editado=await editarPrompt(pv.prompt||"", pv.n_imagenes||0, pv.modelo||"");
    if(editado===null)return;
    payload.prompt_override=editado;
  }
  if(btn){btn.disabled=true;btn.textContent="Generando…";}
  const prog=makeProgress("#gen-out");
  try{
    const jid=await startJob("/api/generate",payload);
    await pollJob(jid,prog);
  }catch(e){prog.fail(errMsg(e));toast(errMsg(e),true);}
  if(btn){btn.disabled=false;btn.textContent="▶ Generar SOLO esta toma";}
}
async function fichaLoad(i){
  const avId=(document.getElementById("g-tav"+i)||{}).value||"";
  if(!avId)return;
  try{
    const d=await jget("/api/avatars/"+avId+"/ficha?t="+Date.now());
    const f=(d&&d.ficha)||{};
    const map={contextura:"g-tcue",busto:"g-tbu",cola:"g-tco",abdomen:"g-tab",edad:"g-ted",altura:"g-tal"};
    let any=false;
    Object.keys(map).forEach(k=>{if(f[k]){const el=document.getElementById(map[k]+i);if(el){el.value=f[k];any=true;}}});
    if(any)toast("Ficha guardada de este avatar cargada ✓");
  }catch(e){}
}
async function fichaSave(i){
  const avId=(document.getElementById("g-tav"+i)||{}).value||"";
  if(!avId)return toast("Elegí un avatar primero: la ficha se guarda por avatar.",true);
  const body={contextura:($("#g-tcue"+i)||{}).value||"",busto:($("#g-tbu"+i)||{}).value||"",
    cola:($("#g-tco"+i)||{}).value||"",abdomen:($("#g-tab"+i)||{}).value||"",
    edad:($("#g-ted"+i)||{}).value||"",altura:($("#g-tal"+i)||{}).value||""};
  try{await jpost("/api/avatars/"+avId+"/ficha",body);toast("Ficha guardada ✓ — se carga sola la próxima vez que elijas este avatar");}
  catch(e){toast("No pude guardar la ficha.",true);}
}

// ---- Generar on_model ----
function noAvatar(){return $("#g-no-avatar")&&$("#g-no-avatar").checked;}
function avatarToSend(){return noAvatar()?null:GEN_AVATAR_ID;}
if($("#g-no-avatar")){$("#g-no-avatar").onchange=()=>{const b=$("#apar-box");if(b)b.style.display=noAvatar()?"block":"none";applyGenderUI();};}

// ---- UI ADAPTATIVA POR GÉNERO: hombre ve opciones de hombre, mujer de mujer ----
let GEN_AVATAR_GENDER="";
function currentGender(){
  if(!noAvatar()&&GEN_AVATAR_GENDER)return GEN_AVATAR_GENDER;
  const g=$("#g-genero");return (g&&g.value)||"mujer";
}
function setSelOpts(sel,opts){const el=$(sel);if(!el)return;const v=el.value;
  el.innerHTML=opts.map(o=>'<option value="'+o[0]+'">'+o[1]+'</option>').join("");
  if([...el.options].some(o=>o.value===v))el.value=v;}
const POSE_LBL_F={0:"De pie (mano en el pelo)",1:"3/4 sobre el hombro",2:"Sentada",3:"De espalda",4:"Caminando",5:"Riéndose",6:"De perfil apoyada",7:"Estirándose / bretel",8:"Primer plano de cara",9:"🔍 Detalle prenda (zoom, sin cara)",10:"🔍 Detalle espalda (atándose)",11:"Fundida con el ambiente",12:"Tirada en el piso / arena",13:"Con utilería (fruta, vaso...)"};
const POSE_LBL_M={0:"De pie (relajado)",1:"3/4 sobre el hombro",2:"Sentado",3:"De espalda",4:"Caminando",5:"Riéndose",6:"De perfil apoyado",7:"Mano por el pelo",8:"Primer plano de cara",9:"🔍 Detalle prenda (zoom, sin cara)",10:"🔍 Detalle espalda (acomodándose)",11:"Fundido con el ambiente",12:"Tirado en el piso",13:"Con utilería (vaso, taza...)"};
function applyGenderUI(){
  const h=currentGender()==="hombre";
  const show=(id,vis)=>{const e=document.getElementById(id);if(e)e.style.display=vis?"":"none";};
  show("wrap-busto",!h);show("wrap-cola",!h);show("wrap-barba",h);
  if(h){const b=$("#g-busto");if(b)b.value="";const c=$("#g-cola");if(c)c.value="";}
  else{const bb=$("#ap-barba");if(bb)bb.value="";}
  setSelOpts("#g-contextura",h
    ?[["","(según avatar)"],["delgado","Delgado"],["atletico","Atlético"],["musculoso","Musculoso"],["robusto","Robusto"],["talle_grande","Talle grande"],["talle_extra_grande","Talle XXL"]]
    :[["","(según avatar)"],["delgada","Delgada"],["atletica","Atlética"],["curvy","Curvy"],["talle_grande","Talle grande"],["talle_extra_grande","Talle XXL (hasta 130+)"]]);
  setSelOpts("#g-abdomen",h
    ?[["","(según avatar)"],["fit","Marcado"],["plano","Plano"],["natural","Natural"],["con_panza","Con panza natural"]]
    :[["","(según avatar)"],["fit","Fit / tonificado"],["plano","Plano"],["natural","Natural"],["con_pancita","Con pancita natural"]]);
  setSelOpts("#g-peinado",h
    ?[["","(según avatar)"],["corto","Corto"],["rapado","Rapado"],["media_melena","Media melena"],["atado","Atado (colita)"]]
    :[["","(según avatar)"],["largo_suelto","Largo suelto"],["largo_ondulado","Largo ondulado"],["media_melena","Media melena"],["corto","Corto"],["atado","Atado (cola)"],["rodete","Rodete/moño"],["trenza","Trenza"]]);
  setSelOpts("#ap-pelo",h
    ?[["","(libre)"],["corto","Pelo corto"],["negro_lacio","Negro lacio"],["castaño_ondulado","Castaño ondulado"],["rubia","Rubio"]]
    :[["","(libre)"],["morocha_largo_ondulado","Largo ondulado oscuro"],["negro_lacio","Negro lacio"],["castaño_largo","Castaño largo"],["castaño_ondulado","Castaño ondulado"],["rubia","Rubia"],["pelirroja","Pelirroja"],["corto","Corto"]]);
  const t=document.getElementById("apar-title");
  if(t)t.textContent=h?"Apariencia del modelo IA (hombre — no queda al azar):":"Apariencia de la modelo IA (no queda al azar):";
  const lx=document.getElementById("lbl-ap-extra");
  if(lx)lx.textContent=h?"Detalle extra del modelo (texto libre)":"Detalle extra de la modelo (texto libre)";
  const ax=$("#ap-extra");
  if(ax)ax.placeholder=h?"ej: canoso, fornido, sonrisa amplia, tatuajes":"ej: bronceada, pecas, fit, cara redonda, sonrisa amplia";
  const lc=document.getElementById("lbl-complemento");
  if(lc)lc.textContent=h?"Si mando solo la parte de arriba, agregar un bóxer haciendo juego":"Si mando solo el corpiño, agregar una bombacha haciendo juego";
  const cd=$("#g-complemento-desc");
  if(cd)cd.placeholder=h?"opcional: cómo querés el bóxer (ej: negro liso, clásico)":"opcional: cómo querés la bombacha (ej: colaless negra, clásica nude)";
  document.querySelectorAll("#pose-pick input[type=checkbox]").forEach(cb=>{
    if(cb.id==="pk-prod")return;
    const L=(h?POSE_LBL_M:POSE_LBL_F)[parseInt(cb.value)];
    if(L!==undefined&&cb.nextSibling)cb.nextSibling.textContent=" "+L;
  });
  // "Regenerar una toma puntual": la lista lleva TODAS las poses (también las nuevas)
  const op=$("#one-pose");
  if(op){
    const cur=op.value;
    const L=h?POSE_LBL_M:POSE_LBL_F;
    op.innerHTML=Object.keys(L).map(k=>'<option value="'+k+'">'+L[k]+'</option>').join("")
      +'<option value="prod">Suelto / colgado</option>';
    if(cur&&[...op.options].some(o=>o.value===cur))op.value=cur;
  }
}
if($("#g-genero"))$("#g-genero").onchange=applyGenderUI;
applyGenderUI();

// ---- UI ADAPTATIVA POR MOTOR: con FLUX se ocultan los ajustes que son de Nano Banana ----
function applyEngineUI(){
  const flux=($("#s-engine")&&$("#s-engine").value==="flux");
  ["box-gemini","row-gemini-params","row-gemini-safety"].forEach(id=>{
    const e=document.getElementById(id);if(e)e.style.display=flux?"none":"";
  });
  const n=document.getElementById("flux-notice");if(n)n.style.display=flux?"":"none";
}
if($("#s-engine"))$("#s-engine").onchange=applyEngineUI;
$("#btn-gen").onclick=async()=>{
  if(!noAvatar() && !GEN_AVATAR_ID)return toast("Elegí un avatar (o tildá 'Sin avatar').",true);
  if(!GEN_PRODUCTS.length)return toast("Subí al menos una foto del producto.",true);
  const b=$("#btn-gen");b.disabled=true;b.textContent="Generando...";
  SET_RESULTS=[];
  const prog=makeProgress("#gen-out");
  try{
    const jid=await startJob("/api/generate",{mode:"on_model",avatar_id:avatarToSend(),product_images:GEN_PRODUCTS,
      aspect:$("#g-aspect").value,paneles:parseInt($("#g-paneles").value),image_size:GEN_SIZE,
      reframe:$("#g-reframe").value||null,style:$("#g-style").value,pose_offset:Math.floor(Math.random()*8),
      params:genParams()});
    await pollJob(jid,prog);
  }catch(e){prog.fail(errMsg(e));toast(errMsg(e),true);}
  b.disabled=false;b.textContent="Generar imágenes";
};

// ---- Set completo: 4 poses de modelo + 1 prenda colgada ----

function genParams(){return {tela:$("#g-tela").value,color:$("#g-color").value,punos:$("#g-punos").value,
  costuras:$("#g-costuras").value,cuello:$("#g-cuello").value,pose:$("#g-pose").value,
  fondo:$("#g-fondo").value,luz:$("#g-luz").value,encuadre:$("#g-encuadre").value,
  temporada:($("#g-temporada")?$("#g-temporada").value:"invierno"),
  cuerpo_busto:($("#g-busto")?$("#g-busto").value:""),cuerpo_cola:($("#g-cola")?$("#g-cola").value:""),
  cuerpo_abdomen:($("#g-abdomen")?$("#g-abdomen").value:""),cuerpo_contextura:($("#g-contextura")?$("#g-contextura").value:""),
  cuerpo_edad:($("#g-edadcorp")?$("#g-edadcorp").value:""),cuerpo_peinado:($("#g-peinado")?$("#g-peinado").value:""),
  cuerpo_altura:($("#g-altura")?$("#g-altura").value:""),
  plantilla:($("#g-plantilla")?$("#g-plantilla").value:""),
  cuerpo_accesorios:($("#g-accesorios")?$("#g-accesorios").value:""),
  complemento:($("#g-complemento")&&$("#g-complemento").checked)?"si":"",
  complemento_desc:($("#g-complemento-desc")?$("#g-complemento-desc").value:""),
  ap_etnia:($("#ap-etnia")?$("#ap-etnia").value:""),ap_edad:($("#ap-edad")?$("#ap-edad").value:""),
  ap_pelo:($("#ap-pelo")?$("#ap-pelo").value:""),ap_ojos:($("#ap-ojos")?$("#ap-ojos").value:""),
  ap_extra:($("#ap-extra")?$("#ap-extra").value:""),
  ap_barba:($("#ap-barba")?$("#ap-barba").value:""),
  genero:($("#g-genero")?$("#g-genero").value:""),
  producto_manual:($("#g-producto-manual")?$("#g-producto-manual").value:""),
  piezas:($("#g-piezas")?$("#g-piezas").value:""),
  fondo_foco:($("#g-foco")?$("#g-foco").value:"desenfocado"),viento:($("#g-viento")&&$("#g-viento").checked?"si":""),
  aclaraciones:$("#g-aclaraciones").value,ficha:($("#g-use-ficha")&&$("#g-use-ficha").checked?(GEN_FICHA||""):"")};}

let SET_JOB=null,ABORT_POLL=false;
function showStop(){const s=$("#btn-stop");s.style.display="";s.textContent="⏹ Frenar set";s.disabled=false;s.dataset.armed="";}
function hideStop(){const s=$("#btn-stop");s.style.display="none";s.dataset.armed="";}
$("#btn-stop").onclick=async()=>{
  if(!SET_JOB)return;
  if($("#btn-stop").dataset.armed){ABORT_POLL=true;return;}   // 2º toque: salir ya
  $("#btn-stop").dataset.armed="1";
  $("#btn-stop").textContent="Frenando… (tocá de nuevo para salir)";
  try{await jpost("/api/jobs/"+SET_JOB+"/stop",{});toast("Frenando — termina la imagen actual y para");}
  catch(e){toast("No pude avisar al server; tocá de nuevo para salir",true);}
};
$("#btn-set").onclick=async()=>{
  if(!noAvatar() && !GEN_AVATAR_ID)return toast("Elegí un avatar (o tildá 'Sin avatar').",true);
  if(!GEN_PRODUCTS.length)return toast("Subí al menos una foto del producto.",true);
  const HQ=$("#set-hq").checked;
  // Poses escritas a mano (una por línea) — si hay, mandan sobre los tildes.
  const posesTxt=($("#g-poses-texto")?$("#g-poses-texto").value:"")
    .split("\n").map(s=>s.trim()).filter(Boolean);
  // Poses elegidas (si el usuario tildó en el selector)
  const poseBoxes=document.querySelectorAll('#pose-pick input[type=checkbox]');
  let poses=[]; let incProd=true;
  poseBoxes.forEach(cb=>{
    if(cb.id==="pk-prod"){incProd=cb.checked;return;}
    if(cb.checked)poses.push(parseInt(cb.value));
  });
  const usaTexto = posesTxt.length>0;
  const usaCustom = !usaTexto && poses.length>0;
  const totalImgs = usaTexto ? (posesTxt.length + (incProd?1:0))
                    : (usaCustom ? (poses.length + (incProd?1:0)) : (HQ?5:3));
  if(totalImgs===0)return toast("Elegí al menos una pose o el producto.",true);
  if(!confirm("Genera "+totalImgs+" imágenes. Corre en el server: si se corta la app o refrescás, sigue solo y lo recuperás al volver. ¿Seguimos?"))return;
  const b=$("#btn-set");b.disabled=true;$("#btn-gen").disabled=true;
  SET_RESULTS=[];
  const prog=makeProgress("#gen-out");
  try{
    const jid=await startJob("/api/set",{hq:HQ,avatar_id:avatarToSend(),product_images:GEN_PRODUCTS,
      product_images_back:GEN_PRODUCTS_BACK,
      poses_texto:(usaTexto?posesTxt:undefined),
      poses:(usaCustom?poses:undefined),include_product:incProd,modo_producto:"suspendida",
      image_size:GEN_SIZE,style:$("#g-style").value,reframe:$("#g-reframe").value||"4:5",
      params:genParams(),save_to_drive:true});
    SET_JOB=jid;showStop();
    await pollJob(jid,prog);
  }catch(e){prog.fail(errMsg(e));toast(errMsg(e),true);}
  SET_JOB=null;hideStop();
  b.disabled=false;$("#btn-gen").disabled=false;b.textContent="🎬 Set completo (5)";
};
// ---- Regenerar UNA toma puntual, respetando fotos/ficha/aclaraciones + imágenes ya creadas ----
$("#btn-one").onclick=async()=>{
  if(!GEN_PRODUCTS.length)return toast("Subí al menos una foto del producto.",true);
  const sel=$("#one-pose").value, isProd=(sel==="prod");
  if(!isProd && !noAvatar() && !GEN_AVATAR_ID)return toast("Elegí un avatar (o tildá 'Sin avatar').",true);
  const b=$("#btn-one");b.disabled=true;b.textContent="Generando...";
  const prog=makeProgress("#gen-out");
  try{
    // anclas = las primeras 2 imágenes ya creadas (misma modelo + prenda)
    let anchors=[];
    for(const u of SET_RESULTS.slice(0,6)){try{anchors.push(await downscaleDataURL(u,1024));}catch(e){}}
    let payload;
    if(isProd){
      // colgado: copia la foto real del producto, sin anclas de modelo
      payload={mode:"product_only",modo_producto:"suspendida",product_images:GEN_PRODUCTS,
        aspect:"4:5",paneles:1,image_size:GEN_SIZE,reframe:null,params:genParams()};
    }else{
      const fp=parseInt(sel), isBack=(fp===3||fp===10);
      const prods=(isBack && GEN_PRODUCTS_BACK.length)?GEN_PRODUCTS_BACK:GEN_PRODUCTS;
      payload={mode:"on_model",avatar_id:avatarToSend(),product_images:prods,
        aspect:"4:5",paneles:1,image_size:GEN_SIZE,reframe:null,style:$("#g-style").value,
        force_pose:fp,consistency_refs:anchors,params:genParams()};
      if(!isBack&&GEN_PRODUCTS_BACK.length)payload.product_images_back=GEN_PRODUCTS_BACK;
    }
    if(!SET_RESULTS.length)toast("Ojo: no hay set previo de guía; sale igual pero sin anclas",false);
    const jid=await startJob("/api/generate",payload);
    await pollJob(jid,prog);
  }catch(e){prog.fail(errMsg(e));toast(errMsg(e),true);}
  b.disabled=false;b.textContent="Generar esta toma";
};
async function reattachJob(){
  let st;try{st=await jget("/api/jobs/active?t="+Date.now());}catch(e){return;}
  if(!st||!st.id||st.status==="none")return;
  if(st.status==="error")return;
  const prog=makeProgress("#gen-out");
  if(st.kind==="set"&&st.status==="running"){SET_JOB=st.id;showStop();}
  try{
    if(st.status==="running")prog.set(5,"Recuperando lo que estaba generando...");
    await pollJob(st.id,prog);
  }catch(e){/* silencioso */}
  SET_JOB=null;hideStop();
}

// ---- Producto ----
$("#btn-prod").onclick=async()=>{
  if(!PROD_PRODUCTS.length)return toast("Subí al menos una foto del producto.",true);
  const b=$("#btn-prod");b.disabled=true;b.textContent="Generando...";
  const prog=makeProgress("#prod-out");
  try{
    prog.bump(12);prog.set(92,"Generando con la IA (30-60s)...");
    const r=await runGenerate({mode:"product_only",modo_producto:PROD_MODO,product_images:PROD_PRODUCTS,
      aspect:$("#pr-aspect").value,paneles:parseInt($("#pr-paneles").value),image_size:PROD_SIZE,
      reframe:$("#pr-reframe").value||null,
      params:{tela:$("#pr-tela").value,color:$("#pr-color").value,punos:$("#pr-punos").value,
        costuras:$("#pr-costuras").value,cuello:$("#pr-cuello").value,fondo:$("#pr-fondo").value,
        luz:$("#pr-luz").value,aclaraciones:$("#pr-aclaraciones").value}});
    prog.bump(95);prog.set(99,"Mostrando...");renderResults("#prod-out",r);prog.done("Listo ✓");
  }catch(e){prog.fail(errMsg(e));toast(errMsg(e),true);}
  b.disabled=false;b.textContent="Generar producto";
};

// ---- Variar color ----
$("#btn-col").onclick=async()=>{
  if(!COL_PRODUCTS.length)return toast("Subí una foto del producto.",true);
  const colors=$("#col-colors").value.split(",").map(c=>c.trim()).filter(Boolean);
  if(!colors.length)return toast("Escribí al menos un color.",true);
  if(!confirm("Genera "+colors.length+" imagen(es), una por color. ¿Seguimos?"))return;
  const b=$("#btn-col");b.disabled=true;
  const prog=makeProgress("#col-out");
  try{
    for(let i=0;i<colors.length;i++){
      b.textContent="Color "+(i+1)+"/"+colors.length+"...";
      const from=i/colors.length*100, to=(i+1)/colors.length*100;
      const r=await runStep(prog,from,to,"Color "+(i+1)+"/"+colors.length+" ("+colors[i]+")...",
        ()=>runGenerate({mode:"recolor",modo_producto:COL_MODO,product_images:COL_PRODUCTS,
          target_color:colors[i],aspect:$("#col-aspect").value,paneles:1,image_size:COL_SIZE,reframe:null,
          params:{fondo:$("#col-fondo").value}}));
      renderResults("#col-out",r);
    }
    prog.done("Colores listos ✓");toast("Colores listos ✓");
  }catch(e){prog.fail(errMsg(e));toast(errMsg(e),true);}
  b.disabled=false;b.textContent="Generar colores";
};

function renderResults(sel,r){
  const ts=Date.now();
  let html='<div class="resblock" style="margin-top:14px"><div><span class="pill">'+r.panels_detected+' imágenes</span><span class="pill">US$'+r.cost.toFixed(3)+' total</span><span class="pill">US$'+r.cost_per_asset.toFixed(3)+' c/u</span><span class="pill">Mes: US$'+r.month_total.toFixed(2)+'</span></div>';
  if(r.descartada){
    const porQC=r.qc&&r.qc.rechazada;
    html+='<div style="font-size:12px;color:var(--bad);font-weight:600;margin:4px 0">'+(porQC
      ?'🚫 DESCARTADA por el inspector: la prenda no llegó a la fidelidad mínima ni tras corregirla. NO la uses — regenerá la toma.'
      :'🚫 Toma descartada en BORRADOR — no se gastó la imagen final. Abajo queda el borrador para que veas por qué.')+'</div>';
  }
  if(r.qc&&r.qc.puntaje){
    const p=r.qc.puntaje, ok=p>=7, col=ok?"var(--ok)":"var(--bad)";
    let t=(ok?"✅ Prenda fiel":"⚠ Prenda con diferencias")+" — fidelidad "+p+"/10";
    if(r.qc.reintentada)t+=" (auto-corregida)";
    html+='<div style="font-size:12px;color:'+col+';font-weight:600;margin:4px 0">'+t+'</div>';
    if(!ok&&(r.qc.diferencias||[]).length)
      html+='<div class="hint" style="margin:2px 0 6px">'+r.qc.diferencias.map(d=>"• "+d).join("<br>")+'</div>';
  }
  if(r.drive_pending){html+='<div style="font-size:12px;color:var(--ok);font-weight:600;margin:4px 0">✅ Guardándose en tu Google Drive (en segundo plano)</div><div class="results">';}
  else if(r.drive_saved){html+='<div style="font-size:12px;color:var(--ok);font-weight:600;margin:4px 0">✅ Guardado en tu Google Drive</div><div class="results">';}
  else{html+='<div style="font-size:12px;color:var(--bad);font-weight:600;margin:4px 0">⬇ Descargá ahora — al recargar se borran</div><div class="results">';}
  r.assets.forEach((a,i)=>{
    const main=a.png||a.optimized;
    html+='<div class="res"><img src="'+main+'"><div class="dl">';
    if(a.png)html+='<a download="luma_'+ts+'_'+i+'.png" href="'+a.png+'">PNG master</a>';
    if(a.optimized)html+='<a download="luma_'+ts+'_'+i+'.jpg" href="'+a.optimized+'">Optimizado</a>';
    html+='</div></div>';
  });
  html+='</div></div>';
  const cont=$(sel);
  // Botón limpiar (una sola vez, arriba)
  if(!cont.querySelector(".clearbtn")){
    cont.insertAdjacentHTML("afterbegin",'<button class="ghost clearbtn" onclick="this.parentNode.innerHTML=\'\'" style="margin-top:10px">Limpiar resultados</button>');
  }
  cont.querySelector(".clearbtn").insertAdjacentHTML("afterend",html);
}

// ---- Google Drive ----
async function loadDrive(){
  const box=$("#drive-status");
  try{
    const d=await jget("/api/drive/status?t="+Date.now());
    if(d.connected){
      box.innerHTML='<div class="kv"><span>Estado</span><b style="color:var(--ok)">✅ Conectado'+(d.email?" ("+d.email+")":"")+'</b></div>'+
        '<div class="kv"><span>Carpeta</span><b>'+d.folder_name+'</b></div>'+
        '<button class="ghost" id="drive-disc" style="margin-top:8px">Desconectar</button>';
      $("#drive-disc").onclick=async()=>{if(confirm("¿Desconectar Drive? Las imágenes dejan de guardarse solas."))
        {await jpost("/api/drive/disconnect",{});toast("Drive desconectado");loadDrive();}};
    }else if(!d.client_id_set){
      box.innerHTML='<div class="note">Todavía no cargaste las credenciales de Google en Railway '+
        '(GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET). Seguí el checklist y después volvé acá.</div>'+
        '<label>Tu redirect URI (la vas a necesitar en Google Cloud)</label>'+
        '<input readonly value="'+d.redirect_uri+'" onclick="this.select()">';
    }else{
      box.innerHTML='<label>Redirect URI (tiene que estar cargada en Google Cloud)</label>'+
        '<input readonly value="'+d.redirect_uri+'" onclick="this.select()" style="margin-bottom:8px">'+
        '<a class="go" style="display:inline-block;text-decoration:none" href="'+BASE+'/api/drive/auth">Conectar Google Drive</a>';
    }
  }catch(e){box.textContent="Error: "+e.message;}
}

// ---- Ajustes ----
async function loadSettings(data){
  SETTINGS=data||await jget("/api/settings?t="+Date.now());
  const asp=$("#s-aspect");asp.innerHTML="";
  ["1:1","3:4","4:5","4:3","16:9","9:16","21:9"].forEach(a=>{const o=document.createElement("option");o.value=a;o.textContent=a;if(a===SETTINGS.aspect_ratio)o.selected=true;asp.appendChild(o);});
  $("#s-size").value=SETTINGS.image_size;$("#s-temp").value=SETTINGS.temperature;$("#s-topp").value=SETTINGS.top_p;
  $("#s-seed").value=SETTINGS.seed??"";$("#s-out").value=SETTINGS.output_format;$("#s-ofmt").value=SETTINGS.optimized_format;
  $("#s-oq").value=SETTINGS.optimized_quality;$("#s-safety").value=SETTINGS.safety;$("#s-sys").value=SETTINGS.system_instruction;
  if($("#s-model")&&SETTINGS.model)$("#s-model").value=SETTINGS.model;
  if($("#g-plantilla")&&SETTINGS.plantilla_prompt)$("#g-plantilla").value=SETTINGS.plantilla_prompt;
  if($("#s-p1k"))$("#s-p1k").value=SETTINGS.precio_1k;
  if($("#s-p2k"))$("#s-p2k").value=SETTINGS.precio_2k;
  if($("#s-p4k"))$("#s-p4k").value=SETTINGS.precio_4k;
  if($("#s-engine"))$("#s-engine").value=SETTINGS.engine||"gemini";
  if($("#s-falkey"))$("#s-falkey").value=SETTINGS.fal_api_key||"";
  if($("#s-fluxtryon"))$("#s-fluxtryon").value=SETTINGS.flux_tryon_model||"";
  if($("#s-fluxedit"))$("#s-fluxedit").value=SETTINGS.flux_edit_model||"";
  if($("#s-precioflux"))$("#s-precioflux").value=SETTINGS.precio_flux;
  if($("#s-qc"))$("#s-qc").value=SETTINGS.qc_prenda||"si";
  if($("#s-qcumbral"))$("#s-qcumbral").value=SETTINGS.qc_umbral||7;
  if($("#s-qcretry"))$("#s-qcretry").value=SETTINGS.qc_reintento||"si";
  if($("#s-borrador"))$("#s-borrador").value=SETTINGS.borrador||"no";
  if($("#s-borrador-size"))$("#s-borrador-size").value=SETTINGS.borrador_size||"1K";
  syncFriendly();
  if(typeof applyEngineUI==="function")applyEngineUI();
}
function syncFriendly(){
  const t=parseFloat($("#s-temp").value);
  if($("#s-creatividad")){let v="0.45";if(t<=0.35)v="0.3";else if(t>=0.6)v="0.7";$("#s-creatividad").value=v;}
  if($("#s-formato")){const a=$("#s-aspect").value;if(["1:1","4:5","9:16","16:9"].includes(a))$("#s-formato").value=a;}
  if($("#s-calidad")){const s=$("#s-size").value;if(["2K","4K"].includes(s))$("#s-calidad").value=s;}
}
function wireFriendly(){
  const c=$("#s-creatividad");if(c)c.onchange=()=>{$("#s-temp").value=c.value;};
  const f=$("#s-formato");if(f)f.onchange=()=>{$("#s-aspect").value=f.value;};
  const q=$("#s-calidad");if(q)q.onchange=()=>{$("#s-size").value=q.value;};
}
wireFriendly();
$("#btn-save-settings").onclick=async()=>{
  const b=$("#btn-save-settings");b.disabled=true;b.textContent="Guardando...";
  try{
    const saved=await jpost("/api/settings",{model:($("#s-model")?$("#s-model").value:undefined),
      precio_1k:($("#s-p1k")?parseFloat($("#s-p1k").value):undefined),
      precio_2k:($("#s-p2k")?parseFloat($("#s-p2k").value):undefined),
      precio_4k:($("#s-p4k")?parseFloat($("#s-p4k").value):undefined),
      engine:($("#s-engine")?$("#s-engine").value:undefined),
      fal_api_key:($("#s-falkey")?$("#s-falkey").value.trim():undefined),
      flux_tryon_model:($("#s-fluxtryon")?$("#s-fluxtryon").value.trim():undefined),
      flux_edit_model:($("#s-fluxedit")?$("#s-fluxedit").value.trim():undefined),
      precio_flux:($("#s-precioflux")?parseFloat($("#s-precioflux").value):undefined),
      qc_prenda:($("#s-qc")?$("#s-qc").value:undefined),
      qc_umbral:($("#s-qcumbral")?parseInt($("#s-qcumbral").value):undefined),
      qc_reintento:($("#s-qcretry")?$("#s-qcretry").value:undefined),
      borrador:($("#s-borrador")?$("#s-borrador").value:undefined),
      borrador_size:($("#s-borrador-size")?$("#s-borrador-size").value:undefined),
      image_size:$("#s-size").value,aspect_ratio:$("#s-aspect").value,
      temperature:parseFloat($("#s-temp").value),top_p:parseFloat($("#s-topp").value),
      seed:$("#s-seed").value?parseInt($("#s-seed").value):null,output_format:$("#s-out").value,
      optimized_format:$("#s-ofmt").value,optimized_quality:parseInt($("#s-oq").value),
      safety:$("#s-safety").value,system_instruction:$("#s-sys").value});
    loadSettings(saved);   // pinta con lo que devolvió el server (sin pasar por caché)
    toast("Ajustes guardados ✓");
  }catch(e){toast(e.message,true);}
  b.disabled=false;b.textContent="Guardar ajustes";
};

// ---- Presupuesto ----
async function loadBudget(){
  const d=await jget("/api/budget?t="+Date.now());
  $("#b-total").textContent="US$"+(d.ledger.total||0).toFixed(2);
  $("#b-assets").textContent=d.ledger.assets||0;
  $("#b-cap").textContent=d.cap!=null?"US$"+d.cap.toFixed(2):"sin tope";
  if(d.cap!=null)$("#cap-input").value=d.cap;
  const rows=(d.ledger.records||[]).map(r=>'<div class="ledrow"><span>'+r.ts.replace("T"," ")+' · '+r.mode+' · '+r.size+'</span><span>US$'+r.cost.toFixed(3)+' ('+r.assets+' img)</span></div>').join("");
  $("#led-rows").innerHTML=rows||'<p class="hint">Todavía no hay movimientos.</p>';
}
$("#btn-cap").onclick=async()=>{
  const v=$("#cap-input").value;
  await jpost("/api/budget/cap",{monthly_usd:v===""?null:parseFloat(v)});
  toast("Tope actualizado ✓");loadBudget();
};

// Diagnóstico de guardado
$("#btn-diag").onclick=async()=>{
  const out=$("#diag-out");out.style.display="block";out.textContent="Probando Redis...";
  try{
    const d=await jget("/api/avatars/debug?t="+Date.now());
    const ok=v=>v?"✅":"❌";
    let txt="Almacenamiento: "+d.backend+"\n";
    txt+="Escritura chica: "+ok(d.write_test_ok)+"  Lectura: "+ok(d.read_back_ok)+"\n";
    txt+="Escritura GRANDE (foto): "+ok(d.big_write_ok)+"  Lectura: "+ok(d.big_read_ok)+"\n";
    if(d.last_error)txt+="Último error: "+d.last_error+"\n";
    txt+="\nAvatares guardados:\n";
    let n=0;
    ["mujer","hombre"].forEach(g=>(d.avatares[g]||[]).forEach(a=>{n++;
      txt+="• "+g+" slot"+a.slot+" ["+a.name+"] → foto: "+a.ref_blob_bytes+" bytes "+(a.recuperable?"✅":"❌ sin foto")+"\n";}));
    if(!n)txt+="(ninguno guardado todavía)\n";
    out.textContent=txt;
  }catch(e){out.textContent="Error: "+e.message;}
};

// ---- Plantillas de artículo ----
const TPL_FIELDS=["g-tela","g-color","g-punos","g-costuras","g-cuello","g-pose","g-fondo","g-luz","g-encuadre","g-aclaraciones"];
async function loadTemplates(){
  try{
    const t=await jget("/api/templates?t="+Date.now());
    const sel=$("#tpl-list");const cur=sel.value;
    sel.innerHTML='<option value="">— elegir —</option>';
    Object.keys(t).forEach(n=>{const o=document.createElement("option");o.value=n;o.textContent=n;sel.appendChild(o);});
    sel.value=cur;sel._data=t;
  }catch(e){}
}
$("#tpl-save").onclick=async()=>{
  const name=prompt("Nombre de la plantilla (ej: Pijama polar panda):");
  if(!name)return;
  const data={style:$("#g-style").value};
  TPL_FIELDS.forEach(id=>data[id]=$("#"+id).value);
  try{await jpost("/api/templates",{name,data});toast("Plantilla guardada ✓");loadTemplates();}
  catch(e){toast(e.message,true);}
};
$("#tpl-load").onclick=()=>{
  const sel=$("#tpl-list");const n=sel.value;if(!n)return toast("Elegí una plantilla.",true);
  const d=(sel._data||{})[n];if(!d)return;
  TPL_FIELDS.forEach(id=>{if(d[id]!==undefined)$("#"+id).value=d[id];});
  if(d.style)$("#g-style").value=d.style;
  toast('Plantilla "'+n+'" cargada — cambiá color, foto y avatar');
};
$("#tpl-del").onclick=async()=>{
  const n=$("#tpl-list").value;if(!n)return toast("Elegí una plantilla.",true);
  if(!confirm('¿Borrar plantilla "'+n+'"?'))return;
  await jdel("/api/templates/"+encodeURIComponent(n));toast("Borrada");loadTemplates();
};

// init
function applyModoFoto(){
  const v=$("#g-temporada")?$("#g-temporada").value:"invierno";
  const esColores=(v==="interior_set");
  const wc=$("#wrap-colores"), wp=$("#wrap-poses"), wb=$("#wrap-gobtns");
  if(wc)wc.style.display=esColores?"block":"none";
  if(wp)wp.style.display=esColores?"none":"block";
  if(wb)wb.style.display=esColores?"none":"flex";
}
if($("#g-temporada"))$("#g-temporada").addEventListener("change",applyModoFoto);
applyModoFoto();
loadSettings();loadGenAvatars();loadTemplates();
async function loadUser(){
  try{
    const r=await fetch(BASE+"/auth/me",{headers:{accept:"application/json"}});
    const u=await r.json();
    const el=$("#userchip");if(!el)return;
    if(u.logged_in){
      el.innerHTML=(u.picture?'<img src="'+u.picture+'" alt="">':'')+
        '<span>'+(u.name||u.email||"")+'</span>'+
        '<span class="mkchip" id="mk-open">Mi marca</span>'+
        '<a href="'+BASE+'/auth/logout">Salir</a>';
      const mk=$("#mk-open");if(mk)mk.onclick=openBrand;
    }
  }catch(e){}
}

let MY_PREFS={};
function fillOnboard(p){
  p=p||{};
  if($("#ob-brand"))$("#ob-brand").value=p.brand||"";
  if(p.rubro&&$("#ob-rubro"))$("#ob-rubro").value=p.rubro;
  if(p.estilo&&$("#ob-estilo"))$("#ob-estilo").value=p.estilo;
  if(p.temporada&&$("#ob-temporada"))$("#ob-temporada").value=p.temporada;
  if(p.modelo&&$("#ob-modelo"))$("#ob-modelo").value=p.modelo;
  if(p.size&&$("#ob-size"))$("#ob-size").value=p.size;
  if($("#ob-fondo"))$("#ob-fondo").value=p.fondo||"";
}
function openBrand(){
  fillOnboard(MY_PREFS);
  $("#ob-title").textContent="Mi marca";
  $("#ob-sub").textContent="Ajustá tus preajustes. Se aplican solos cada vez que generás.";
  $("#onboard").classList.add("on");
}
function applyPrefs(p){
  if(!p)return;
  if(p.temporada&&$("#g-temporada")){$("#g-temporada").value=p.temporada;if(typeof applyModoFoto==="function")applyModoFoto();}
  if(p.estilo&&$("#g-style"))$("#g-style").value=p.estilo;
  if(p.fondo!==undefined&&$("#g-fondo"))$("#g-fondo").value=p.fondo||"";
  if(p.modelo&&$("#g-no-avatar")){const ia=(p.modelo==="ia");$("#g-no-avatar").checked=ia;const bx=$("#apar-box");if(bx)bx.style.display=ia?"block":"none";}
  if(p.size){GEN_SIZE=p.size;document.querySelectorAll('#p-generar .seg .opt[data-size]').forEach(o=>o.classList.toggle('on',o.dataset.size===p.size));}
}
async function loadPrefs(){
  try{
    const r=await fetch(BASE+"/api/prefs",{headers:{accept:"application/json"}});
    const d=await r.json();
    MY_PREFS=d.prefs||{};
    applyPrefs(MY_PREFS);
    if(!d.onboarded){
      $("#ob-title").textContent="Bienvenida a Studio Luma";
      $("#onboard").classList.add("on");
    }
  }catch(e){}
}
async function saveBrand(){
  MY_PREFS={brand:$("#ob-brand").value,rubro:$("#ob-rubro").value,estilo:$("#ob-estilo").value,
    temporada:$("#ob-temporada").value,modelo:$("#ob-modelo").value,size:$("#ob-size").value,
    fondo:$("#ob-fondo").value};
  try{await jpost("/api/prefs",{prefs:MY_PREFS});}catch(e){}
  applyPrefs(MY_PREFS);
  $("#onboard").classList.remove("on");
  toast("Preajustes guardados ✓");
}
if($("#ob-save"))$("#ob-save").onclick=saveBrand;
if($("#ob-skip"))$("#ob-skip").onclick=()=>{$("#onboard").classList.remove("on");};

loadUser();
loadPrefs();
loadMyKey();
checkDrive();
reattachJob();

async function checkDrive(){
  const s=$("#drive-status");if(!s)return;
  const rc=$("#btn-drive-reconnect");if(rc)rc.href=BASE+"/auth/reconnect";
  s.textContent="Verificando…";
  try{
    const r=await fetch(BASE+"/api/mydrive",{headers:{accept:"application/json"}});
    const d=await r.json();
    s.textContent=(d.connected?"✓ ":"⚠ ")+(d.msg||"");
    s.style.borderColor=d.connected?"var(--ok)":"var(--bad)";
  }catch(e){s.textContent="⚠ No pude verificar tu Drive.";}
}
if($("#btn-drive-check"))$("#btn-drive-check").onclick=checkDrive;

async function loadMyKey(){
  try{
    const r=await fetch(BASE+"/api/mykey",{headers:{accept:"application/json"}});
    const d=await r.json();
    renderKeyStatus(!!d.has_key);
  }catch(e){}
}
function renderKeyStatus(has){
  const s=$("#key-status");if(!s)return;
  s.style.display="block";
  if(has){s.textContent="✓ Estás generando con TU propia key. Tu consumo lo factura Google a tu cuenta.";}
  else{s.textContent="Estás usando la cuenta general de Studio Luma (no cargaste key propia).";}
}
function addExtraRow(){
  const cont=$("#trio-extras");if(!cont)return;
  const row=document.createElement("div");row.className="row";row.style.alignItems="end";row.style.marginTop="6px";
  row.innerHTML=
    '<div><label>¿Quiénes?</label><select class="ex-quien">'+
      '<option value="grupal">Las 3 juntas</option>'+
      '<option value="modelo1">Modelo 1</option>'+
      '<option value="modelo2">Modelo 2</option>'+
      '<option value="modelo3">Modelo 3</option></select></div>'+
    '<div><label>Pose</label><select class="ex-pose">'+
      '<option value="frente">De frente</option>'+
      '<option value="perfil">De perfil</option>'+
      '<option value="espalda">De espalda</option>'+
      '<option value="sentada">Sentada</option>'+
      '<option value="caminando">Caminando</option></select></div>'+
    '<div style="flex:0 0 auto"><button class="ghost ex-del" style="border-color:var(--bad);color:var(--bad)">✕</button></div>';
  row.querySelector(".ex-del").onclick=()=>row.remove();
  cont.appendChild(row);
}
if($("#btn-add-extra"))$("#btn-add-extra").onclick=addExtraRow;
function gatherExtras(){
  const out=[];
  document.querySelectorAll("#trio-extras .row").forEach(r=>{
    const q=r.querySelector(".ex-quien"),p=r.querySelector(".ex-pose");
    if(q&&p)out.push({quien:q.value,pose:p.value});
  });
  return out;
}
if($("#inc-prod"))$("#inc-prod").addEventListener("change",()=>{
  const w=$("#prod-modo-wrap");if(w)w.style.display=$("#inc-prod").checked?"block":"none";
});
function gatherAsign(){
  let asign=[];
  for(let i=0;i<3;i++){
    const col=(($("#g-tcol"+i)||{}).value||"").trim();
    if(!col){continue;}
    const avId=($("#g-tav"+i)||{}).value||"";
    const item={color:col,
      pose:($("#g-tpo"+i)||{}).value||"frente",
      contextura:($("#g-tcue"+i)||{}).value||"",
      busto:($("#g-tbu"+i)||{}).value||"",
      cola:($("#g-tco"+i)||{}).value||"",
      abdomen:($("#g-tab"+i)||{}).value||"",
      altura:($("#g-tal"+i)||{}).value||"",
      edad:($("#g-ted"+i)||{}).value||"",
      etnia:($("#g-tet"+i)||{}).value||"",
      pelo:($("#g-tpe"+i)||{}).value||"",
      indicacion:($("#g-tind"+i)||{}).value||""};
    if(avId){const sel=$("#g-tav"+i);item.avatar_id=avId;item.nombre=sel.options[sel.selectedIndex].text;}
    asign.push(item);
  }
  return asign;
}
if($("#btn-plantilla-save"))$("#btn-plantilla-save").onclick=async()=>{
  try{ await jpost("/api/settings",{plantilla_prompt:$("#g-plantilla").value||""});
       toast("Plantilla guardada ✓ — se carga sola la próxima vez"); }
  catch(e){ toast("No pude guardar la plantilla.",true); }
};
if($("#btn-plantilla-clear"))$("#btn-plantilla-clear").onclick=()=>{
  $("#g-plantilla").value="";toast("Plantilla vacía: vuelve el prompt automático");
};
if($("#btn-debug"))$("#btn-debug").onclick=async()=>{
  const out=$("#debug-out");out.style.display="block";out.textContent="Consultando…";
  try{
    const d=await jget("/api/jobs/last_debug?t="+Date.now());
    if(d.info){out.textContent=d.info;return;}
    let t="TRABAJO "+d.trabajo+" · "+(d.tipo||"")+" · estado: "+d.estado_general+" · hechas "+d.hechas+"/"+d.total+"\n";
    if(d.modelo_usado)t+="MODELO: "+d.modelo_usado+" · filtro: "+(d.filtro_usado||"?")+"\n";
    if(d.error_general)t+="ERROR GENERAL: "+d.error_general+"\n";
    (d.tomas||[]).forEach(x=>{
      t+="\nToma "+x.toma+" ("+(x.que_es||"?")+")\n  estado: "+x.estado+" · imagen: "+(x.tiene_imagen?"SÍ":"NO");
      if(x.error)t+="\n  error: "+x.error;
    });
    if(d.pedido&&d.pedido.endpoint)t+="\n===== PEDIDO EXACTO ENVIADO (sin la API key) =====\n"+JSON.stringify(d.pedido,null,2)+"\n";
    if(d.prompt_ultima_bloqueada)t+="\n\n===== PROMPT EXACTO DE LA ÚLTIMA TOMA BLOQUEADA =====\n"+d.prompt_ultima_bloqueada;
    if(d.prompt_ultima_toma)t+="\n\n===== PROMPT DE LA ÚLTIMA TOMA ENVIADA (salga o no) =====\n"+d.prompt_ultima_toma;
    out.textContent=t;
  }catch(e){out.textContent="No pude consultar: "+(e.message||e);}
};
if($("#btn-set-colores"))$("#btn-set-colores").onclick=async()=>{
  if(!GEN_PRODUCTS.length)return toast("Subí al menos una foto del producto.",true);
  // Junta las fichas de cada modelo (con avatar o IA, todas van por 'asign')
  let asign=gatherAsign();
  if(asign.length<1)return toast("Cargá al menos una modelo con su color.",true);
  const extras=gatherExtras();
  const incG=$("#inc-grupal")?$("#inc-grupal").checked:true;
  const incI=$("#inc-ind")?$("#inc-ind").checked:true;
  const incP=$("#inc-prod")?$("#inc-prod").checked:false;
  const modoP=$("#inc-prod-modo")?$("#inc-prod-modo").value:"flat_lay";
  const n=asign.length;
  const total=((incG&&n>=3)?1:0)+(incI?n:0)+extras.length+(incP?1:0);
  if(incG&&n<3)toast("Con "+n+" modelo"+(n>1?"s":"")+" no hay foto grupal (es de a 3): salen las individuales + extras.",false);
  if(total<1)return toast("Elegí al menos una imagen para el set.",true);
  if(!confirm("Genera "+total+" imágenes. ¿Seguimos?"))return;
  $("#btn-set-colores").disabled=true;$("#btn-gen").disabled=true;$("#btn-set").disabled=true;
  SET_RESULTS=[];
  const prog=makeProgress("#gen-out");
  try{
    const simple=!!($("#set-simple")&&$("#set-simple").checked);
    const prm=genParams();
    if(simple)prm.modo_minimo="si";
    const body={avatar_id:null,product_images:GEN_PRODUCTS,image_size:GEN_SIZE,
      style:$("#g-style").value,reframe:"4:5",modo_producto:modoP,
      params:prm,save_to_drive:true,asign:asign,extras:extras,
      inc_grupal:incG,inc_ind:incI,inc_prod:incP,
      grupal_indicacion:($("#g-tgrupal-ind")||{}).value||"",
      product_images_back:GEN_PRODUCTS_BACK};
    const jid=await startJob("/api/set",body);
    SET_JOB=jid;showStop();
    await pollJob(jid,prog);
  }catch(e){prog.fail(errMsg(e));toast(errMsg(e),true);}
  SET_JOB=null;hideStop();
  $("#btn-set-colores").disabled=false;$("#btn-gen").disabled=false;$("#btn-set").disabled=false;
};
let CHAT=[];
if($("#btn-save-key"))$("#btn-save-key").onclick=async()=>{
  const k=$("#my-key").value.trim();
  if(!k)return toast("Pegá tu API key o usá 'Quitar mi key'.",true);
  try{const d=await jpost("/api/mykey",{key:k});renderKeyStatus(!!d.has_key);$("#my-key").value="";toast("Key guardada ✓");}
  catch(e){toast("No se pudo guardar la key.",true);}
};
if($("#btn-clear-key"))$("#btn-clear-key").onclick=async()=>{
  try{await jpost("/api/mykey",{key:""});$("#my-key").value="";renderKeyStatus(false);toast("Key quitada. Volvés a la cuenta general.");}
  catch(e){toast("No se pudo quitar.",true);}
};

// ═══════════ EDITOR DE COLOR (100% local: canvas, sin IA, sin costo) ═══════════
// Estilo "Reemplazar color" de Photoshop: se elige el color base (cuentagotas o
// auto), una tolerancia con borde suave, y el recoloreo conserva la LUZ de cada
// píxel (sombras, arrugas, trama) — por eso el resultado es tela real, no sólido.
let ED={fullImg:null,prevData:null,pw:0,ph:0,base:null,busy:false,srcURL:null,maskCv:null,maskData:null};
let ED_BRUSH=null,ED_DRAWING=false;   // pincel: null | "add" | "del"
const ED_PRESETS=[["Negro","#141414"],["Blanco","#f4f2ee"],["Nude","#d9b49a"],["Rojo","#c62828"],["Bordó","#7b1e3c"],["Fucsia","#e91e8c"],["Rosa bebé","#f2bccb"],["Celeste","#8ec7e8"],["Verde agua","#7fd4c1"],["Lila","#b39ddb"],["Azul noche","#22346b"],["Gris","#9a9a9a"]];
function edRgb2Hsl(r,g,b){r/=255;g/=255;b/=255;const M=Math.max(r,g,b),m=Math.min(r,g,b);let h=0,s=0;const l=(M+m)/2,d=M-m;
  if(d>0){s=l>0.5?d/(2-M-m):d/(M+m);if(M===r)h=(g-b)/d+(g<b?6:0);else if(M===g)h=(b-r)/d+2;else h=(r-g)/d+4;h*=60;}
  return [h,s,l];}
function edHsl2Rgb(h,s,l){h=((h%360)+360)%360;const c=(1-Math.abs(2*l-1))*s,x=c*(1-Math.abs((h/60)%2-1)),m=l-c/2;
  let r=0,g=0,b=0;if(h<60){r=c;g=x;}else if(h<120){r=x;g=c;}else if(h<180){g=c;b=x;}
  else if(h<240){g=x;b=c;}else if(h<300){r=x;b=c;}else{r=c;b=x;}
  return [(r+m)*255,(g+m)*255,(b+m)*255];}
function edHex2Rgb(x){const n=parseInt(x.slice(1),16);return [(n>>16)&255,(n>>8)&255,n&255];}
function edParams(){
  const t=edHex2Rgb($("#ed-target").value),th=edRgb2Hsl(t[0],t[1],t[2]);
  return {tol:parseInt($("#ed-tol").value)/100,soft:parseInt($("#ed-soft").value)/100,
          target:{h:th[0],s:th[1],l:th[2]},satMul:parseInt($("#ed-sat").value)/100,
          luz:parseInt($("#ed-luz").value)/100,mask:$("#ed-mask").checked};}
// Núcleo: procesa un ImageData con los parámetros. La clave de realismo: la
// luminosidad final = la del píxel original (desplazada), NUNCA la del color nuevo.
// M (opcional) = máscara de la prenda (gris 0-255, misma resolución): el cambio
// queda encerrado adentro aunque el fondo sea del mismo color.
function edProcess(src,P,M){
  const d=src.data,out=new Uint8ClampedArray(d.length),B=ED.base;
  const grayB=B.s<0.12,grayT=P.target.s<0.08;
  for(let i=0;i<d.length;i+=4){
    const r=d[i],g=d[i+1],b=d[i+2];
    const hsl=edRgb2Hsl(r,g,b),h=hsl[0],s=hsl[1],l=hsl[2];
    let dh=Math.abs(h-B.h);if(dh>180)dh=360-dh;
    let dist;
    if(grayB){dist=Math.abs(s-B.s)*2.2+Math.abs(l-B.l)*1.6;}
    else{dist=(dh/180)*2.6+Math.abs(s-B.s)*0.9+Math.abs(l-B.l)*0.55;}
    let w=dist<=P.tol?1:(dist>=P.tol+P.soft?0:1-(dist-P.tol)/P.soft);
    if(M&&w>0)w*=M[i]/255;
    if(w<=0){out[i]=r;out[i+1]=g;out[i+2]=b;out[i+3]=d[i+3];continue;}
    if(P.mask){out[i]=r+(255-r)*w*0.75;out[i+1]=g*(1-w*0.7);out[i+2]=b*(1-w*0.7);out[i+3]=d[i+3];continue;}
    const relS=grayB?1:Math.min(1.6,Math.max(0.4,s/Math.max(B.s,0.05)));
    let ns=grayT?P.target.s*P.satMul:Math.min(1,P.target.s*relS*P.satMul);
    let nl=l+(P.target.l-B.l)*0.75+P.luz;
    nl=Math.min(0.985,Math.max(0.015,nl));
    const nh=grayT?h:P.target.h;
    const nrgb=edHsl2Rgb(nh,ns,nl);
    out[i]=r+(nrgb[0]-r)*w;out[i+1]=g+(nrgb[1]-g)*w;out[i+2]=b+(nrgb[2]-b)*w;out[i+3]=d[i+3];
  }
  return new ImageData(out,src.width,src.height);
}
function edRender(){
  if(!ED.prevData||!ED.base)return;
  const cv=$("#ed-canvas"),ctx=cv.getContext("2d");
  ctx.putImageData(edProcess(ED.prevData,edParams(),ED.maskData),0,0);
}
let edTimer=null;
function edRenderSoon(){clearTimeout(edTimer);edTimer=setTimeout(edRender,70);}
function edShowBase(){
  const B=ED.base,rgb=edHsl2Rgb(B.h,B.s,B.l);
  $("#ed-base-sw").style.background="rgb("+Math.round(rgb[0])+","+Math.round(rgb[1])+","+Math.round(rgb[2])+")";
  $("#ed-base-txt").textContent="color tomado ✓ (tocá la foto para cambiarlo)";
}
function edSetBaseFromXY(x,y){
  const cv=$("#ed-canvas"),R=cv.getBoundingClientRect();
  const px=Math.round((x-R.left)/R.width*ED.pw),py=Math.round((y-R.top)/R.height*ED.ph);
  const d=ED.prevData.data;let sr=0,sg=0,sb=0,n=0;
  for(let dy=-2;dy<=2;dy++)for(let dx=-2;dx<=2;dx++){
    const qx=px+dx,qy=py+dy;
    if(qx<0||qy<0||qx>=ED.pw||qy>=ED.ph)continue;
    const k=(qy*ED.pw+qx)*4;sr+=d[k];sg+=d[k+1];sb+=d[k+2];n++;
  }
  if(!n)return;
  const hsl=edRgb2Hsl(sr/n,sg/n,sb/n);
  ED.base={h:hsl[0],s:hsl[1],l:hsl[2]};
  edShowBase();edRender();
}
// Auto: color dominante de la zona central, salteando fondo casi blanco/negro.
function edAutoBase(){
  if(!ED.prevData)return;
  const d=ED.prevData.data,bins={},cx=ED.pw/2,cy=ED.ph/2;
  for(let y=0;y<ED.ph;y+=3)for(let x=0;x<ED.pw;x+=3){
    const k=(y*ED.pw+x)*4,hsl=edRgb2Hsl(d[k],d[k+1],d[k+2]);
    if(hsl[2]>0.93&&hsl[1]<0.12)continue;  // fondo blanco
    if(hsl[2]<0.06)continue;               // sombra negra
    const dc=1-Math.min(1,Math.hypot(x-cx,y-cy)/Math.hypot(cx,cy));
    const key=Math.round(hsl[0]/24)+"_"+Math.round(hsl[1]*3)+"_"+Math.round(hsl[2]*4);
    const e=bins[key]||(bins[key]={w:0,h:0,s:0,l:0,n:0});
    const wt=0.35+dc;e.w+=wt;e.h+=hsl[0]*wt;e.s+=hsl[1]*wt;e.l+=hsl[2]*wt;e.n++;
  }
  let best=null;for(const k in bins){if(!best||bins[k].w>best.w)best=bins[k];}
  if(!best)return;
  ED.base={h:best.h/best.w,s:best.s/best.w,l:best.l/best.w};
  edShowBase();edRender();
}
function edLoadDataURL(u){
  const im=new Image();
  im.onload=()=>{
    ED.fullImg=im;ED.srcURL=u;
    ED.maskCv=null;ED.maskData=null;edSetBrush(null);
    $("#ed-mask-state").style.display="none";
    const MAXP=1100,sc=Math.min(1,MAXP/Math.max(im.width,im.height));
    ED.pw=Math.max(1,Math.round(im.width*sc));ED.ph=Math.max(1,Math.round(im.height*sc));
    const cv=$("#ed-canvas");cv.width=ED.pw;cv.height=ED.ph;
    const ctx=cv.getContext("2d");ctx.drawImage(im,0,0,ED.pw,ED.ph);
    ED.prevData=ctx.getImageData(0,0,ED.pw,ED.ph);
    $("#ed-work").style.display="";
    edAutoBase();
    toast("Foto cargada: tocá la prenda para afinar el color base.");
  };
  im.src=u;
}
// ── Límite de la prenda: detección IA + pinceles manuales ──
function edMaskRefresh(){
  ED.maskData=ED.maskCv?ED.maskCv.getContext("2d").getImageData(0,0,ED.pw,ED.ph).data:null;
  $("#ed-mask-state").style.display=ED.maskCv?"":"none";
}
async function edDetect(){
  if(!ED.srcURL)return;
  const b=$("#ed-detect");b.disabled=true;const old=b.textContent;b.textContent="Detectando...";
  try{
    const r=await jpost("/api/segment_prenda",{image:ED.srcURL});
    const items=(r.items||[]).filter(it=>it.mask&&it.box_2d);
    if(!items.length){toast("No pude detectar la prenda en esta foto.",true);return;}
    const mc=document.createElement("canvas");mc.width=ED.pw;mc.height=ED.ph;
    const mx=mc.getContext("2d");mx.fillStyle="#000";mx.fillRect(0,0,ED.pw,ED.ph);
    let ok=0;
    await Promise.all(items.map(it=>new Promise(res=>{
      const im=new Image();
      im.onload=()=>{
        try{
          const y0=it.box_2d[0],x0=it.box_2d[1],y1=it.box_2d[2],x1=it.box_2d[3];
          const bx=x0/1000*ED.pw,by=y0/1000*ED.ph;
          const bw=Math.max(1,(x1-x0)/1000*ED.pw),bh=Math.max(1,(y1-y0)/1000*ED.ph);
          // normalizo: blanco = prenda (aunque la máscara venga con transparencia)
          const t=document.createElement("canvas");
          t.width=Math.max(1,Math.round(bw));t.height=Math.max(1,Math.round(bh));
          const tx=t.getContext("2d");tx.drawImage(im,0,0,t.width,t.height);
          const td=tx.getImageData(0,0,t.width,t.height);
          for(let k=0;k<td.data.length;k+=4){
            const v=Math.round(td.data[k]*(td.data[k+3]/255));
            td.data[k]=v;td.data[k+1]=v;td.data[k+2]=v;td.data[k+3]=255;
          }
          tx.putImageData(td,0,0);
          mx.globalCompositeOperation="lighten";
          mx.filter="blur(2px)";           // borde suave, sin recorte duro
          mx.drawImage(t,bx,by,bw,bh);
          mx.filter="none";ok++;
        }catch(e){}
        res();
      };
      im.onerror=()=>res();
      im.src=it.mask;
    })));
    if(!ok){toast("No pude leer la máscara del detector.",true);return;}
    ED.maskCv=mc;edMaskRefresh();edRender();
    toast("✓ Prenda detectada: el cambio queda adentro de la silueta. Corregí con los pinceles si hace falta.");
  }catch(e){toast(errMsg(e),true);}
  finally{b.disabled=false;b.textContent=old;}
}
function edEnsureMask(){
  if(ED.maskCv)return;
  const mc=document.createElement("canvas");mc.width=ED.pw;mc.height=ED.ph;
  const mx=mc.getContext("2d");mx.fillStyle="#fff";mx.fillRect(0,0,ED.pw,ED.ph);
  ED.maskCv=mc;
}
function edSetBrush(mode){
  ED_BRUSH=mode;
  const cv=$("#ed-canvas");
  if(cv)cv.style.touchAction=mode?"none":"auto";
  if(cv)cv.style.cursor=mode?"cell":"crosshair";
  const ba=$("#ed-brush-add"),bd=$("#ed-brush-del");
  if(ba)ba.style.outline=(mode==="add")?"2px solid var(--rose)":"";
  if(bd)bd.style.outline=(mode==="del")?"2px solid var(--rose)":"";
}
function edBrushAt(x,y){
  const cv=$("#ed-canvas"),R=cv.getBoundingClientRect();
  const px=(x-R.left)/R.width*ED.pw,py=(y-R.top)/R.height*ED.ph;
  const rad=parseInt($("#ed-brush-size").value)/900*Math.max(ED.pw,ED.ph);
  edEnsureMask();
  const mx=ED.maskCv.getContext("2d");
  mx.globalCompositeOperation="source-over";mx.filter="none";
  const grad=mx.createRadialGradient(px,py,rad*0.55,px,py,rad);
  const col=ED_BRUSH==="add"?"255,255,255":"0,0,0";
  grad.addColorStop(0,"rgba("+col+",1)");grad.addColorStop(1,"rgba("+col+",0)");
  mx.fillStyle=grad;mx.beginPath();mx.arc(px,py,rad,0,Math.PI*2);mx.fill();
}
let edBrushTimer=null;
function edBrushRefreshSoon(){clearTimeout(edBrushTimer);edBrushTimer=setTimeout(()=>{edMaskRefresh();edRender();},60);}
$("#file-ed").onchange=async e=>{
  if(!e.target.files.length)return;
  const u=await fileToDataURL(e.target.files[0]);
  $("#dz-ed").innerHTML='<div>Listo ✓</div><img src="'+u+'" style="max-height:90px;margin:4px;border-radius:6px">';
  edLoadDataURL(u);
};
function edSyncFromGen(){
  const box=$("#ed-from-gen");
  if(!GEN_PRODUCTS.length){box.style.display="none";return;}
  box.style.display="";
  box.innerHTML='<span class="hint">o tocá una ya subida en Generar: </span>'+
    GEN_PRODUCTS.map((u,i)=>'<img src="'+u+'" data-i="'+i+'" style="max-height:64px;margin:3px;border-radius:6px;cursor:pointer;border:1px solid var(--line)">').join("");
  box.querySelectorAll("img").forEach(im=>im.onclick=()=>edLoadDataURL(GEN_PRODUCTS[parseInt(im.dataset.i)]));
}
$("#ed-canvas").addEventListener("click",e=>{if(ED_BRUSH)return;edSetBaseFromXY(e.clientX,e.clientY);});
// Pinceles: pintar con el dedo/mouse suma o saca zona de la máscara
const edCv=$("#ed-canvas");
edCv.addEventListener("pointerdown",e=>{
  if(!ED_BRUSH)return;e.preventDefault();ED_DRAWING=true;
  edCv.setPointerCapture(e.pointerId);
  edBrushAt(e.clientX,e.clientY);edBrushRefreshSoon();
});
edCv.addEventListener("pointermove",e=>{
  if(!ED_BRUSH||!ED_DRAWING)return;e.preventDefault();
  edBrushAt(e.clientX,e.clientY);edBrushRefreshSoon();
});
["pointerup","pointercancel"].forEach(ev=>edCv.addEventListener(ev,()=>{
  if(ED_DRAWING){ED_DRAWING=false;edMaskRefresh();edRender();}
}));
$("#ed-detect").onclick=edDetect;
$("#ed-brush-add").onclick=()=>edSetBrush(ED_BRUSH==="add"?null:"add");
$("#ed-brush-del").onclick=()=>edSetBrush(ED_BRUSH==="del"?null:"del");
$("#ed-mask-clear").onclick=e=>{
  e.preventDefault();
  ED.maskCv=null;ED.maskData=null;edSetBrush(null);
  $("#ed-mask-state").style.display="none";edRender();
};
$("#ed-auto").onclick=edAutoBase;
["ed-tol","ed-soft","ed-sat","ed-luz"].forEach(id=>{const el=$("#"+id);el.oninput=edRenderSoon;});
$("#ed-target").oninput=edRenderSoon;
$("#ed-mask").onchange=edRender;
const edPr=$("#ed-presets");
edPr.innerHTML=ED_PRESETS.map(p=>'<div title="'+p[0]+'" data-c="'+p[1]+'" style="width:26px;height:26px;border-radius:50%;border:1px solid var(--line);background:'+p[1]+';cursor:pointer"></div>').join("");
edPr.querySelectorAll("div").forEach(sw=>sw.onclick=()=>{$("#ed-target").value=sw.dataset.c;edRender();});
// Mantener apretado = ver la foto original
const edOrigBtn=$("#ed-orig");
function edShowOrig(on){
  const cv=$("#ed-canvas");if(!ED.prevData)return;
  if(on)cv.getContext("2d").putImageData(ED.prevData,0,0);else edRender();
}
["mousedown","touchstart"].forEach(ev=>edOrigBtn.addEventListener(ev,e=>{e.preventDefault();edShowOrig(true);}));
["mouseup","mouseleave","touchend","touchcancel"].forEach(ev=>edOrigBtn.addEventListener(ev,()=>edShowOrig(false)));
$("#ed-reset").onclick=()=>{
  $("#ed-tol").value=28;$("#ed-soft").value=14;$("#ed-sat").value=100;$("#ed-luz").value=0;
  $("#ed-mask").checked=false;
  if(ED.fullImg)edLoadDataURL(ED.fullImg.src);
};
// Exporta en la resolución ORIGINAL de la foto (procesa el tamaño completo)
function edExportFull(){
  const im=ED.fullImg;if(!im||!ED.base)return null;
  const MAXF=3200,sc=Math.min(1,MAXF/Math.max(im.width,im.height));
  const w=Math.max(1,Math.round(im.width*sc)),h=Math.max(1,Math.round(im.height*sc));
  const cv=document.createElement("canvas");cv.width=w;cv.height=h;
  const ctx=cv.getContext("2d");ctx.drawImage(im,0,0,w,h);
  const P=edParams();P.mask=false;
  let M=null;
  if(ED.maskCv){   // la máscara de prenda se escala a la resolución de salida
    const s=document.createElement("canvas");s.width=w;s.height=h;
    const sx=s.getContext("2d");sx.imageSmoothingEnabled=true;
    sx.drawImage(ED.maskCv,0,0,w,h);
    M=sx.getImageData(0,0,w,h).data;
  }
  ctx.putImageData(edProcess(ctx.getImageData(0,0,w,h),P,M),0,0);
  return cv.toDataURL("image/jpeg",0.95);
}
$("#ed-down").onclick=()=>{
  if(!ED.base)return toast("Primero elegí el color base tocando la prenda.",true);
  toast("Procesando en alta...");
  setTimeout(()=>{
    const u=edExportFull();if(!u)return;
    const a=document.createElement("a");
    a.href=u;a.download="luma_color_"+$("#ed-target").value.replace("#","")+".jpg";a.click();
  },50);
};
$("#ed-use").onclick=()=>{
  if(!ED.base)return toast("Primero elegí el color base tocando la prenda.",true);
  toast("Procesando en alta...");
  setTimeout(()=>{
    const u=edExportFull();if(!u)return;
    GEN_PRODUCTS.push(u);
    $("#dz-gen").innerHTML=thumbs(GEN_PRODUCTS);
    toast("✓ Agregada a las fotos del producto (pestaña Generar). Si usás la ficha, volvé a tocar Analizar prenda.");
  },50);
};
</script>
<div id="pm-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:999;align-items:center;justify-content:center;padding:12px">
  <div style="background:var(--card);border:1px solid var(--line);border-radius:14px;max-width:820px;width:100%;max-height:92vh;overflow:auto;padding:16px">
    <h3 style="margin:0 0 6px">Prompt que se va a enviar</h3>
    <p class="hint" id="pm-info" style="margin:0 0 10px"></p>
    <textarea id="pm-text" style="width:100%;min-height:340px;font-family:ui-monospace,monospace;font-size:13px;line-height:1.45"></textarea>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
      <button class="go" id="pm-go" style="flex:1">▶ Generar con este prompt</button>
      <button class="ghost" id="pm-copy">📋 Copiar</button>
      <button class="ghost" id="pm-cancel">Cancelar</button>
    </div>
  </div>
</div>
</body>
</html>
"""
HTML_PAGE = HTML_PAGE.replace("%%PREFIX%%", ROUTE_PREFIX).replace("%%VERSION%%", VERSION)


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI(title="LUMA Íntima · Estudio de Imágenes")
    app.include_router(router)

    @app.get("/")
    async def _root():
        return JSONResponse({"ok": True, "ui": ROUTE_PREFIX,
                             "model": MODEL_ID, "kv": kv.backend})

    port = int(os.getenv("IMAGENES_PORT", "8090"))
    print(f"  LUMA Íntima · Estudio de Imágenes  ->  http://localhost:{port}{ROUTE_PREFIX}")
    print(f"  Modelo: {MODEL_ID} | KV: {kv.backend} | GEMINI_API_KEY: {'ok' if GEMINI_API_KEY else 'FALTA'}")
    uvicorn.run(app, host="0.0.0.0", port=port)
