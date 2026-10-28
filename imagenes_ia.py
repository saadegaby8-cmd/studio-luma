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
  NANO_BANANA_MODEL         (opcional)     -> default: gemini-nano-banana-2.1 (Nano Banana 2.1;
                                              si no responde, Nano Banana 2)
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
import foto_perfil as _fp
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

import claude_director as _claude

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

ROUTE_PREFIX = os.environ.get("IMAGENES_PREFIX", "/imagenes").rstrip("/")
VERSION = "2.88.1"   # subí este número cada vez que cambiamos el archivo

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FAL_API_KEY = os.getenv("FAL_KEY", "") or os.getenv("FAL_API_KEY", "")
MODEL_ID = os.getenv("NANO_BANANA_MODEL", "gemini-nano-banana-2.1")  # Nano Banana 2.1
# Si el modelo nuevo no responde por generateContent (recién salido, o la cuenta todavía no
# lo tiene), la imagen sale igual con Nano Banana 2.
MODEL_RESPALDO = "gemini-3.1-flash-image"
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
    "modelo_21_visto": False,       # ya pasó una vez a Nano Banana 2.1 (ver get_settings)
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
    "higgsfield_key_id": "",            # Higgsfield (Storyboard): o HIGGSFIELD_KEY_ID en Railway
    "higgsfield_key_secret": "",        # o HIGGSFIELD_KEY_SECRET en Railway
    "flux_tryon_model": "bytedance/seedream/v5/pro/edit",   # avatar + prenda (FLUX.2 dev: calidad + moder. liviana)
    "flux_edit_model": "bytedance/seedream/v5/pro/edit",    # sin avatar / solo producto (dev, multi-referencia)
    # Respaldo si el checker de Seedream rechaza: Qwen Image 3 edit (Alibaba, pesos
    # abiertos en fal): no tiene checker propio, identidad fuerte, 1 a 3 referencias,
    # US$0,075 la imagen. Antes era Seedream Lite, que tiene el MISMO checker de ByteDance.
    "flux_fallback_model": "alibaba/qwen-image-3/edit",
    "precio_flux": 0.07,                # US$ por imagen con FLUX (editable)
    "flux_guidance": 5.0,               # + alto = FLUX obedece más el prompt (pose/ambiente)
    "seedream_final_4k": "si",          # tras Seedream: Nano Banana rehace la imagen en 4K (no bloquea retoques)
    "seedream_cara_recortada": "si",    # a Seedream le va SOLO la cara del avatar (no copia la pose del retrato)
    "claude_seedream": "si",            # Claude escribe el pedido de Seedream y revisa la foto (si hay clave)
    "seedream_poses_seguras": "si",     # lencería/baño en Seedream: poses de catálogo (su checker de salida tira las otras)
    "variacion_auto": "si",             # cámara, recorrido y encuadre rotando solos por toma; "no" = como antes de la v2.48
    # ── Control de fidelidad de prenda (inspector automático post-generación) ──
    "qc_prenda": "si",                  # inspecciona cada imagen vs las fotos reales
    "qc_model": "gemini-3.1-flash",     # modelo del inspector (flash entiende mejor que lite)
    "qc_anatomia": "si",                # 2da pasada del inspector: anatomía y física
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
            "ESTILO: foto real estilo Instagram, tomada al pasar en el lugar de la escena, "
            "NO una producción. Que parezca una foto de verdad, no una imagen "
            "generada. Poses naturales y espontáneas, sin posar: postura relajada, sonrisa "
            "auténtica, pelo movido, gestos casuales. Piel con textura real y natural (poros, "
            "brillos suaves), tonos de piel auténticos. Iluminación ambiental de día, colores "
            "equilibrados, look de foto tomada por un amigo. Enfoque natural en la cara y fondo "
            "levemente desenfocado. Evitá el aspecto de render/CGI, la piel plástica, la "
            "sobre-nitidez y la perfección de catálogo: buscá una foto casual creíble."
        ),
        "flux": ("LOOK: candid real-life photo, natural available light, relaxed unposed "
                 "posture, real skin texture with pores, no studio polish, no CGI sheen."),
    },
    "catalogo": {
        "label": "Catálogo sobrio",
        "text": (
            "ESTILO: foto de catálogo de e-commerce, estudio profesional, sobria y elegante. "
            "Pose limpia de catálogo, fondo neutro, luz de estudio pareja y suave, colores "
            "fieles, texturas nítidas. Apta para tienda online y redes."
        ),
        "flux": ("LOOK: clean e-commerce catalog photo, neutral background, soft even studio "
                 "light, true colors, sharp fabric texture."),
    },
    "editorial": {
        "label": "Editorial / campaña",
        "text": (
            "ESTILO: fotografía editorial de moda, dirección de arte cuidada, iluminación "
            "con intención, composición con carácter pero elegante. Realista, sin exagerar, "
            "texturas y piel naturales. Para campaña de marca."
        ),
        "flux": ("LOOK: fashion editorial, art-directed composition, intentional lighting, "
                 "natural skin and fabric texture, elegant without exaggeration."),
    },
    # El grano y los colores lavados del film son lo que más rápido saca a una imagen del
    # lugar de "render": las cámaras digitales no ensucian, el film sí.
    "vintage": {
        "label": "Vintage de revista (film, grano)",
        "text": (
            "ESTILO: fotografía analógica de revista de moda, tomada en película de 35 mm "
            "(tipo Kodak Portra 400 o Gold 200) y escaneada. GRANO DE FILM visible y parejo "
            "en toda la imagen, sobre todo en las sombras y en las zonas planas. Colores "
            "LAVADOS y desaturados, nada de saturación digital: los rojos tiran a terracota, "
            "los blancos a crema y las sombras a verde o marrón, con dominante cálida. "
            "Contraste suave, negros levantados y grises lechosos en vez de negros puros. "
            "Las luces fuertes se desbordan un poco (halación), con un halo suave alrededor. "
            "Foco natural de lente antiguo, sin la nitidez exagerada de lo digital, y bordes "
            "del cuadro apenas más oscuros. Piel con textura y poros de verdad, sin retoque "
            "ni alisado. Luz existente del lugar, como una producción real de los años 90. "
            "NO agregues marco blanco de foto, bordes de película, perforaciones, fecha "
            "impresa, sellos, texto ni marcas de agua: es la imagen sola, de borde a borde."
        ),
        "flux": ("LOOK: shot on 35mm film (Kodak Portra 400) and scanned. Visible even FILM "
                 "GRAIN across the whole frame, washed-out desaturated colors with a warm "
                 "cast, soft contrast, lifted milky blacks instead of pure black, halation "
                 "glowing around bright highlights, vintage fashion-magazine feel, no digital "
                 "sharpening, real skin texture, slightly darker frame edges. NO white photo "
                 "border, no film sprockets, no date stamp, no text, no watermark."),
    },
    "vintage_suave": {
        "label": "Vintage suave (apenas de film)",
        "text": (
            "ESTILO: foto de moda con un toque analógico, no un vintage marcado. Grano de "
            "film fino y discreto, apenas visible en las sombras. Colores un poco bajados de "
            "saturación y con un leve tono cálido, sin virar a sepia. Contraste medio, con "
            "los negros apenas levantados. Nitidez natural, sin filo digital. Piel con "
            "textura real, sin retoque. Queda moderna y limpia, pero con cuerpo de fotografía "
            "de verdad. NO agregues marcos, bordes de película, fecha impresa ni texto."
        ),
        "flux": ("LOOK: subtle analog film touch — fine discreet grain, slightly desaturated "
                 "warm colors, medium contrast with barely lifted blacks, natural sharpness, "
                 "real skin texture. NO borders, no date stamp, no text."),
    },
}


def _style_text(style: str, settings: Dict[str, Any]) -> str:
    key = style or settings.get("default_style", "instagram_real")
    return STYLE_PRESETS.get(key, STYLE_PRESETS["instagram_real"])["text"]


def _style_flux(style: str, settings: Dict[str, Any]) -> str:
    """La versión corta y en inglés del estilo, para Seedream/FLUX: el prompt de ese motor
    tiene que ser breve, así que el bloque largo en castellano no sirve."""
    key = style or settings.get("default_style", "instagram_real")
    preset = STYLE_PRESETS.get(key, STYLE_PRESETS["instagram_real"])
    return preset.get("flux", "")

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


# Lo que se sabe de ESTA toma y los bloques sueltos necesitan para no contradecirse:
# si ella fijó el encuadre, si la prenda es de abajo, el lugar, el género. Lo carga
# _do_generate al empezar (cada pedido corre en su propio contexto).
TOMA_CTX: "contextvars.ContextVar[Dict[str, Any]]" = contextvars.ContextVar("toma_ctx", default={})


def _ctx(k: str, default: Any = None) -> Any:
    return (TOMA_CTX.get() or {}).get(k, default)


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


# Rutas que se migran solas a Seedream: las que dieron mala calidad o bloqueos, las
# de texto a imagen (no editan) y las mal escritas. OJO: "fal-ai/flux-2/edit" ya NO
# está acá: es la opción abierta para probar contra el checker de Seedream, y la
# migración silenciosa la pisaba con Seedream sin avisar.
_FLUX_SLUGS_VIEJOS = {"fal-ai/flux-2/lora", "fal-ai/flux-2-pro/edit",
                      "fal-ai/flux-2-lora-gallery/virtual-tryon", "fal-ai/flux-2/dev",
                      "fal-ai/bytedance/seedream/v4.5/edit",
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
    # Nano Banana 2.1 (oct 2026): una sola vez, quien tenía Nano Banana 2 pasa al nuevo. Si
    # después vuelve a elegir Nano Banana 2 a mano, se respeta (queda la marca de que ya pasó).
    if isinstance(s, dict) and not s.get("modelo_21_visto"):
        if str(merged.get("model") or "").strip() in ("", "gemini-3.1-flash-image"):
            merged["model"] = "gemini-nano-banana-2.1"
        merged["modelo_21_visto"] = True
    merged["borrador"] = "no"             # borradores ELIMINADOS (eran para FLUX): nunca más
    if str(merged.get("flux_fallback_model", "")).strip() in (
            "fal-ai/flux-2/lora", "fal-ai/flux-2-lora-gallery/virtual-tryon",
            "fal-ai/bytedance/seedream/v5/lite/edit", "bytedance/seedream/v5/lite/edit"):
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


# MIS PERSONAJES COMO AVATAR: los personajes (Personajes → ficha) aparecen en Fotos con id
# "pj:<pid>". La referencia principal es su retrato aprobado; además viajan su Cara HD (cara,
# ojos, perfil) y su cuerpo entero, rotulados como la MISMA modelo: rasgos mucho más fieles
# que con una sola foto de avatar.
PJ_AVATAR = "pj:"


async def _personaje_avatar(avatar_id: str, con_extras: bool = True) -> Optional[Dict[str, Any]]:
    if not str(avatar_id or "").startswith(PJ_AVATAR):
        return None
    try:
        from personajes import _doc, _refs_identidad, caras_hd   # import tardío: personajes importa de acá
        doc = await _doc(avatar_id[len(PJ_AVATAR):])
        refs = await _refs_identidad(doc)
    except Exception:
        return None
    if not refs:
        return None
    av: Dict[str, Any] = {"id": avatar_id, "name": doc.get("nombre") or "Personaje",
                          "gender": "hombre" if doc.get("genero") == "hombre" else "mujer",
                          "locked": True, "personaje": True, "ref_b64": refs[0][1],
                          "desc": doc.get("desc_cara") or ""}
    if con_extras:
        extras: List[Tuple[str, str]] = []
        try:
            hd = await caras_hd(doc)
        except Exception:
            hd = {}
        if hd.get("cara_hd"):
            av["cara_recorte"] = hd["cara_hd"]     # para Seedream: su cara HD, sin recortar nada
        for k, et in (("cara_hd", "su CARA de cerca en alta definición (rasgos, piel, pecas y lunares)"),
                      ("ojos_hd", "sus OJOS y su piel en macro"),
                      ("perfil_hd", "su cara de PERFIL / tres cuartos")):
            if hd.get(k):
                extras.append((et, hd[k]))
        for et, b in refs[1:]:
            if "cuerpo" in et:
                extras.append(("su CUERPO entero (contextura y proporciones; su ropa y fondo NO se copian)", b))
        av["extra_refs"] = extras[:4]
    return av


async def personajes_como_avatares() -> List[Dict[str, Any]]:
    """Los personajes con retrato, para el selector de modelo de Fotos."""
    try:
        from personajes import _doc, _indice
        out = []
        for pid in await _indice():
            try:
                d = await _doc(pid)
            except Exception:
                continue
            if (d.get("hoja") or {}).get("retrato"):
                out.append({"id": PJ_AVATAR + pid, "name": d.get("nombre") or "Personaje",
                            "gender": "hombre" if d.get("genero") == "hombre" else "mujer",
                            "locked": True, "has_ref": True, "personaje": True})
        return out
    except Exception:
        return []


def partes_extra_avatar(av: Optional[Dict[str, Any]], idx: int) -> Tuple[List[Dict[str, Any]], int]:
    """Las referencias extra de un personaje (Cara HD, cuerpo), rotuladas, después del avatar."""
    partes: List[Dict[str, Any]] = []
    for et, b in (av or {}).get("extra_refs") or []:
        partes.append({"text": f"IMAGEN {idx} — LA MISMA MODELO del avatar: {et}. Copiá su identidad EXACTA:"})
        partes.append(_img_part(b))
        idx += 1
    return partes, idx


async def get_avatar_ref(avatar_id: str) -> Optional[str]:
    """Devuelve el b64 de la referencia (blob separado).
    Compatibilidad: si no existe el blob, busca la foto embebida en el índice
    (formato viejo) y la migra al blob separado para futuras lecturas."""
    if str(avatar_id or "").startswith(PJ_AVATAR):
        pj = await _personaje_avatar(avatar_id, con_extras=False)
        return pj["ref_b64"] if pj else None
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
    if str(avatar_id or "").startswith(PJ_AVATAR):
        return await _personaje_avatar(avatar_id)
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


def _bloque_consistencia(n: int, era: bool = False, caras: bool = False) -> str:
    if n <= 0:
        return ""
    if caras:
        # Las tomas previas llegan como RECORTE DE LA CARA (sin fondo ni cuerpo).
        return (
            "\n\nMISMA PERSONA (CRÍTICO): las imágenes rotuladas «CARA DE LA MISMA PERSONA» son "
            "recortes de la cara de tomas anteriores de esta sesión. Es EXACTAMENTE esa persona: "
            "misma cara y rasgos, mismo color y corte de pelo, mismo maquillaje y tono de piel; "
            "no la reinventes. Su cuerpo es el descripto en el texto. Todo lo demás de esta toma "
            "(la pose, el encuadre, el ÁNGULO DE CÁMARA y la PARTE DEL LUGAR) sale del texto de "
            "ESTA TOMA, y tiene que verse DISTINTO de las tomas anteriores: otro ángulo y otro "
            "rincón del mismo lugar, con otro fondo detrás. La prenda sale SIEMPRE de las «FOTO "
            "REAL DEL PRODUCTO»."
        ) + (_ESTAV_CONS_ES if era else "")
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
        "indicada más abajo.\n"
        "• Y MUCHO MENOS copies el FONDO ni la parte del lugar donde estaban paradas. Es el "
        "MISMO lugar, pero esta toma transcurre en OTRA PARTE de ese lugar: lo que se ve "
        "detrás de ella tiene que ser CLARAMENTE DISTINTO de lo que se ve en las tomas "
        "previas. Si el fondo de esta toma se parece al de las anteriores, la toma está MAL."
    ) + (_ESTAV_CONS_ES if era else "")


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
    "PLANO MEDIO/AMERICANO, sentada de forma relajada en lo que el lugar tenga (un sillón, la cama, un banco, un escalón, la arena o el piso), torso erguido "
    "y en leve giro, manos apoyadas con naturalidad, piernas fuera de cuadro o apenas insinuadas",
    "DE ESPALDA mostrando la parte de atrás de la prenda, cabeza girada hacia la cámara, "
    "una mano en la nuca, cuerpo en contrapposto",
    "CUERPO ENTERO, caminando hacia la cámara en pleno paso, una pierna adelante, brazos "
    "sueltos en movimiento, dinámica y natural",
    "PLANO MEDIO, en plena risa genuina con la cabeza apenas hacia atrás, mirada fuera de "
    "cuadro, gesto totalmente desprevenido",
    "CUERPO ENTERO, DE TRES CUARTOS (el cuerpo girado apenas hacia la cámara, NO de perfil "
    "puro: el FRENTE de la prenda tiene que verse entero, con SU diseño y SUS colores "
    "exactos de las fotos del producto), apoyada de costado contra un elemento REAL del "
    "escenario pedido (lo que ese lugar tenga de verdad: una palmera, una roca, una "
    "baranda, el marco de una puerta, una columna, la pared de la casa si la escena es "
    "interior), una pierna cruzada y un pie en punta, mirada relajada a lo lejos. "
    "PROHIBIDO inventar una "
    "pared suelta que no pertenece al lugar (una pared en medio de la playa no existe): si "
    "el escenario es exterior y no hay dónde apoyarse, va de perfil apoyando el peso en "
    "una pierna, sin apoyarse en nada. PROHIBIDO agregar un mueble, una pared, una baranda o un objeto que el lugar no tenga para que ella tenga dónde apoyarse: movela HASTA DONDE de verdad hay algo (la pared, el marco de una puerta, una columna, el mostrador, la cabecera de la cama). El lugar NO SE AMUEBLA. Si en todo el lugar no hay nada, no se apoya en nada",
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
    "elemento, mirada lejos de la cámara, como un descuido editorial. PROHIBIDO agregar un mueble, una pared, una baranda o un objeto que el lugar no tenga para que ella tenga dónde apoyarse: movela HASTA DONDE de verdad hay algo (la pared, el marco de una puerta, una columna, el mostrador, la cabecera de la cama). El lugar NO SE AMUEBLA. Si en todo el lugar no hay nada, no se apoya en nada",
    "TIRADA O SENTADA EN EL PISO de la escena (la arena, el pasto, una alfombra, la cama — lo "
    "que corresponda al ambiente), cuerpo relajado en el suelo, apoyada en los antebrazos o de "
    "costado, el entorno tocando la piel, mirada perdida o a cámara desde abajo",
    "PLANO MEDIO con UTILERÍA casual coherente con la escena en la mano (una fruta, una flor, "
    "un vaso o botella, un sombrero, una taza — UN solo objeto simple), interactuando con el "
    "objeto de forma desprevenida, mirada al objeto o fuera de cuadro, momento robado real",
]

# ── CUÁNTO CUERPO ENTRA EN EL CUADRO ──
# Las poses del pool traen su propio tamaño de plano ("PLANO MEDIO", "CUERPO ENTERO"),
# pero los renglones de CÁMARA y de PARTE DEL LUGAR empujan los dos hacia lo abierto ("todo
# el ambiente por delante", "el piso ocupa la mayor parte del cuadro", "de lejos con zoom")
# y le ganaban: un set de cuatro salía con tres cuerpos enteros. Ahora el tamaño de plano
# es un renglón propio y obligatorio que ROTA con la toma, y a la pose se le saca el suyo
# para que no haya dos órdenes peleando.
PLANO_POOL = [
    {"lenceria": True, "lbl": "Cuerpo entero", "ayuda": "De pies a cabeza, con el lugar alrededor.",
     "es": "CUERPO ENTERO: entra de la cabeza a los pies, con aire arriba y abajo",
     "en": "FULL BODY: head to feet in frame, with air above and below"},
    {"lenceria": True, "corta_arriba": True, "lbl": "De la cintura para arriba", "ayuda": "Plano medio: se ve bien la cara y la parte de arriba de la prenda.",
     "es": "PLANO MEDIO: el cuadro CORTA A LA ALTURA DE LA CINTURA o un poco más abajo; "
           "los pies y las piernas NO entran",
     "en": "MEDIUM SHOT: the frame CUTS AT THE WAIST or slightly below; feet and legs are "
           "NOT in frame"},
    {"lenceria": True, "lbl": "De la rodilla para arriba", "ayuda": "Plano americano: entra casi todo el conjunto pero más cerca.",
     "es": "PLANO AMERICANO: el cuadro CORTA POR ENCIMA DE LAS RODILLAS; los pies NO entran",
     "en": "COWBOY SHOT: the frame CUTS ABOVE THE KNEES; the feet are NOT in frame"},
    {"lenceria": True, "lbl": "Ella chica en el lugar", "ayuda": "Plano general: ella ocupa poco y se ve todo el ambiente.",
     "es": "PLANO GENERAL: ella ocupa POCO del cuadro (como un tercio de la altura) y el "
           "ambiente es casi toda la foto; se la reconoce igual y la prenda se lee",
     "en": "WIDE ESTABLISHING SHOT: she takes up LITTLE of the frame (about a third of its "
           "height) and the place is almost the whole photo; she is still recognizable and "
           "the garment still reads"},
    {"lenceria": False, "corta_arriba": True, "lbl": "Del pecho para arriba", "ayuda": "Plano corto: cara, hombros y el escote de la prenda.",
     "es": "PLANO CORTO: el cuadro CORTA A LA ALTURA DEL PECHO; se ven la cara, los hombros "
           "y la parte de arriba de la prenda, nada más",
     "en": "CLOSE SHOT: the frame CUTS AT CHEST HEIGHT; face, shoulders and the top of the "
           "garment only"},
    {"lenceria": False, "lbl": "Cuerpo entero, bien pegado", "ayuda": "Entera pero llenando el cuadro, casi sin aire.",
     "es": "CUERPO ENTERO AJUSTADO: entra de la cabeza a los pies pero LLENANDO el cuadro, "
           "con muy poco aire alrededor",
     "en": "TIGHT FULL BODY: head to feet, but FILLING the frame with very little air "
           "around her"},
]
_PLANO_NOTA_ES = (" Este tamaño de plano se COMBINA con la cámara y la parte del lugar: el "
                  "ángulo de cámara se respeta TAL CUAL, y este renglón sólo dice cuánto "
                  "cuerpo entra en el cuadro desde ese ángulo.")
_PLANO_NOTA_EN = " Keep the camera angle exactly as given; this line only sets how much of her body is in frame from that angle."


# Poses que SON su encuadre (primer plano de cara, detalle de prenda, detalle de espalda):
# ahí el tamaño de plano es la pose misma y no se le impone otro.
_POSES_CON_PLANO_PROPIO = (8, 9, 10)


def _plano_aparte(idx_pose: int, idx_cam: int, zona: bool,
                  lenc: bool = False, elegida: bool = False) -> bool:
    """¿Corresponde poner el tamaño de plano en su propio renglón? No, si manda el encuadre
    por zona, si la pose ya ES un encuadre, o si la cámara de esta toma ya decide el tamaño
    (bien lejos / muy cerca)."""
    if zona or idx_pose % len(POSE_POOL) in _POSES_CON_PLANO_PROPIO:
        return False
    return not _cam_entry(idx_cam, lenc, elegida).get("plano_propio")


def _plano(idx: int, en: bool = False, lenceria: bool = False) -> str:
    """El tamaño de plano que le toca a esta toma. En lencería y baño se rota SÓLO entre
    los que no cierran sobre el torso: un plano corto a la altura del pecho o un cuerpo
    entero pegado, en ropa interior, es justo lo que hace saltar el filtro de imagen."""
    pool = [x for x in PLANO_POOL if x["lenceria"]] if lenceria else PLANO_POOL
    if _ctx("abajo"):
        # Prenda de abajo (short, bombacha, malla short): un plano que corta en la cintura o
        # en el pecho la deja AFUERA del cuadro. Sólo los planos donde se ve.
        pool = [x for x in pool if not x.get("corta_arriba")] or pool
    c = pool[idx % len(pool)]
    return (("SHOT SIZE: " + c["en"] + "." + _PLANO_NOTA_EN) if en
            else ("TAMAÑO DE PLANO: " + c["es"] + "." + _PLANO_NOTA_ES))


# ── EN QUÉ PARTE DEL LUGAR ──
# Con una puesta en escena armada, las seis tomas del set salían todas en el mismo rincón:
# cambiaba la pose y la cámara, pero el lugar entero quedaba sin recorrer. Y cuando la pose
# pedía "apoyada", el modelo le METÍA UN MUEBLE AL LADO en vez de llevarla hasta donde de
# verdad hay algo. Estos rincones rotan con la toma y son RELATIVOS: no nombran objetos,
# describen una parte del mismo lugar. Sólo se usan cuando la usuaria describió un
# escenario (con un fondo liso de estudio no hay nada que recorrer).
RINCON_POOL = [
    # Cada rincón tiene que dar un FONDO distinto de verdad. La primera versión decía
    # cosas como "contra una pared", "en un borde" o "en el medio del ambiente": en una
    # foto las tres se ven casi igual, y el set seguía pareciendo el mismo rincón repetido.
    # Ahora cada uno nombra qué tiene que VERSE detrás de ella.
    {"es": "PEGADA A UNA PARED del lugar, bien cerca: esa pared llena el fondo de la foto "
           "(su textura, su color, lo que cuelgue de ella) y casi no se ve profundidad",
     "en": "RIGHT AGAINST A WALL of the place, very close: that wall fills the background "
           "(its texture, its color, whatever hangs on it) and there is almost no depth"},
    {"es": "EN EL MARCO DE UNA PUERTA o en la entrada: el marco la encuadra a ella y "
           "DETRÁS se ve otro ambiente o la calle, más claro o más oscuro que donde está",
     "en": "IN A DOORWAY or the entrance: the frame frames her and BEHIND her another room "
           "or the street is visible, brighter or darker than where she stands"},
    {"es": "CONTRA LA VENTANA: la ventana se VE en el cuadro, con lo que haya afuera, y la "
           "luz le entra de costado recortándole la silueta",
     "en": "AT THE WINDOW: the window is VISIBLE in frame, with whatever is outside, and "
           "the light comes in from the side rimming her silhouette"},
    {"es": "EN EL PUNTO MÁS LEJANO del lugar, mirando hacia adentro: entre ella y la cámara "
           "queda TODO el ambiente, y detrás se ve la pared del fondo",
     "en": "AT THE FARTHEST POINT of the place, facing inward: the WHOLE room lies between "
           "her and the camera, and the far wall is behind her"},
    {"quieta": True, "es": "APOYADA EN EL MUEBLE MÁS GRANDE que ese lugar YA tenga (el mostrador, la mesa, "
           "el respaldo del sillón, la cama, un estante), y ese mueble ocupa medio cuadro",
     "en": "LEANING ON THE LARGEST piece of furniture the place ALREADY has (the counter, "
           "the table, the sofa back, the bed, a shelf), and it fills half the frame"},
    {"quieta": True, "es": "EN EL PISO del lugar: el piso (la alfombra, la madera, la arena, el pasto) "
           "ocupa la MAYOR PARTE del cuadro y se le ve la textura de cerca",
     "en": "ON THE FLOOR of the place: the floor (rug, wood, sand, grass) takes up MOST of "
           "the frame and its texture reads up close"},
    {"es": "EN UNA ESQUINA, en el vértice donde se JUNTAN DOS PAREDES: se ven las dos "
           "paredes yéndose para lados distintos detrás de ella",
     "en": "IN A CORNER, at the vertex where TWO WALLS MEET: both walls are seen running "
           "off in different directions behind her"},
    {"es": "EN LA ESCALERA, el escalón o el desnivel que el lugar tenga: los escalones se "
           "ven en el cuadro yéndose hacia arriba o hacia abajo y ella queda a otra altura "
           "que el piso; si el lugar no tiene escalera, SUBIDA a algo real (una tarima, un "
           "banquito, el borde de la pileta)",
     "en": "ON THE STAIRS, step or level change of the place: the steps are visible in "
           "frame running up or down and she is at a different height from the floor; if "
           "the place has no stairs, STANDING UP ON something real (a platform, a stool, "
           "the pool edge)"},
    {"es": "EN EL PASILLO o el paso entre dos partes del lugar: el ambiente se ALARGA a los "
           "dos costados de ella y se ve hacia el fondo",
     "en": "IN THE HALLWAY or the passage between two parts of the place: the room STRETCHES "
           "away on both sides of her and you can see into the distance"},
    {"tapa": True, "es": "ENTRE ELEMENTOS DEL LUGAR que quedan DELANTE de ella (plantas, una cortina, ropa "
           "colgada, un perchero): esos elementos entran desenfocados en el borde del cuadro",
     "en": "AMONG ELEMENTS of the place that sit IN FRONT of her (plants, a curtain, hanging "
           "clothes, a rack): those elements enter the frame blurred at the edge"},
]
_RINCON_NOTA_ES = (
    " Es el MISMO lugar de las demás tomas, visto en otra parte: NO cambies de local ni de "
    "escenario. EL FONDO DE ESTA FOTO TIENE QUE VERSE CLARAMENTE DISTINTO al de las otras "
    "tomas del set: si detrás de ella se ve lo mismo de siempre, la toma está MAL. Ojo: "
    "el rincón dice DÓNDE está parada, NO cuánto cuerpo entra en el cuadro — eso lo fija "
    "el TAMAÑO DE PLANO. Y el "
    "lugar NO SE AMUEBLA: está PROHIBIDO agregar muebles, paredes, biombos u objetos que el "
    "lugar no tenga para que a ella le quede algo cerca. Si eso que se nombra no existe en "
    "este lugar, movela a otro rincón REAL de este mismo lugar.")
# Las notas en inglés van COMPACTAS: el prompt de Seedream tiene 4.500 caracteres de tope
# y éstas viajan como esenciales, así que cada palabra de más echa afuera un bloque real.
_RINCON_NOTA_EN = (
    " Same place as the other shots, another part of it: the background MUST look clearly "
    "different. Do not add furniture the place does not have; if that spot does not exist "
    "here, use another real corner.")


# Los rincones de arriba son de INTERIOR (pared, puerta, ventana, mueble, esquina de dos
# paredes, pasillo). En una pileta o una playa "contra la ventana" no significa nada y el
# modelo inventaba una ventana o ignoraba el renglón. Con un escenario exterior se rota
# por estos, que también nombran qué tiene que VERSE detrás de ella.
RINCON_POOL_EXT = [
    {"agua": True, "es": "AL BORDE DEL AGUA (la pileta, el mar, el río que ese lugar tenga): el agua ocupa "
           "el fondo del cuadro y se ven sus reflejos",
     "en": "AT THE WATER'S EDGE (the pool, the sea, the river the place has): the water "
           "fills the background with its reflections"},
    {"es": "EN LA ENTRADA O EL ACCESO del lugar, mirando hacia adentro: detrás de ella se "
           "abre TODO el ambiente",
     "en": "AT THE ENTRANCE or access to the place, facing inward: the WHOLE place opens up "
           "behind her"},
    {"veg": True, "es": "BAJO UN ÁRBOL O UNA PALMERA real del lugar: el tronco a su lado y el follaje "
           "arriba llenan el cuadro, con la luz filtrada entre las hojas",
     "en": "UNDER A REAL TREE OR PALM of the place: the trunk beside her and the foliage "
           "above fill the frame, with dappled light through the leaves"},
    {"es": "EN EL PUNTO MÁS ABIERTO del lugar: el cielo y el horizonte (o la línea del mar, "
           "o el paisaje lejano) llenan el fondo, sin nada cerca detrás de ella",
     "en": "AT THE MOST OPEN SPOT of the place: the sky and the horizon (or the sea line, or "
           "the distant landscape) fill the background, nothing close behind her"},
    {"quieta": True, "es": "EN LA REPOSERA, EL BANCO O EL ASIENTO que ese lugar YA tenga (una reposera, un "
           "banco, un murete, el borde de la pileta), y ese asiento ocupa medio cuadro",
     "en": "ON THE LOUNGER, BENCH OR SEAT the place ALREADY has (a lounger, a bench, a low "
           "wall, the pool edge), and that seat fills half the frame"},
    {"quieta": True, "es": "EN EL PISO del lugar: la arena, el pasto, el deck o las baldosas ocupan la MAYOR "
           "PARTE del cuadro y se les ve la textura de cerca",
     "en": "ON THE GROUND of the place: the sand, grass, deck or tiles take up MOST of the "
           "frame and their texture reads up close"},
    {"es": "PEGADA A UNA FACHADA O MURO del lugar (la pared del edificio, del bar, un murete "
           "de piedra): ese muro llena el fondo con su textura y su color",
     "en": "RIGHT AGAINST A FACADE OR WALL of the place (the building wall, the bar wall, a "
           "stone wall): it fills the background with its texture and color"},
    {"es": "EN LA ESCALERA O EL DESNIVEL del lugar (los escalones de la pileta, del deck, de "
           "la entrada, una duna): los escalones se ven en el cuadro y ella queda a otra "
           "altura que el piso",
     "en": "ON THE STAIRS OR LEVEL CHANGE of the place (pool steps, deck steps, the entrance "
           "steps, a dune): the steps are in frame and she is at another height"},
    {"es": "EN EL CAMINO, SENDERO O PASARELA del lugar: el camino se ALEJA en perspectiva "
           "detrás de ella hacia el fondo",
     "en": "ON THE PATH, WALKWAY OR BOARDWALK of the place: the path RECEDES in perspective "
           "behind her toward the distance"},
    {"veg": True, "tapa": True, "es": "ENTRE PLANTAS O ELEMENTOS DEL LUGAR que quedan DELANTE de ella (hojas, una "
           "sombrilla, una baranda, flores), fuera de foco en primer plano",
     "en": "BETWEEN PLANTS OR ELEMENTS of the place that sit IN FRONT of her (leaves, a "
           "parasol, a railing, flowers), out of focus in the foreground"},
]
_EXT_KW = ("pileta", "piscina", "playa", "mar", "océano", "oceano", "jardín", "jardin",
           "parque", "plaza", "terraza", "azotea", "calle", "callejón", "callejon", "vereda",
           "campo", "bosque", "muelle", "río", "rio", "lago", "laguna", "patio", "exterior",
           "aire libre", "montaña", "montana", "desierto", "puerto", "ruta", "camino",
           "sendero", "quinta", "viñedo", "vinedo", "arena", "palmera", "palmeras", "costa",
           "acantilado", "deck", "solárium", "solarium", "balcón", "balcon", "pool", "beach",
           "garden", "park", "street", "outdoor", "outdoors", "rooftop")
_INT_KW = ("living", "habitación", "habitacion", "dormitorio", "cocina", "baño", "bano",
           "local", "tienda", "showroom", "oficina", "estudio", "cuarto", "sala", "salón",
           "salon", "vestidor", "probador", "negocio", "interior", "loft", "departamento",
           "lobby", "bedroom", "room", "store", "shop", "indoor", "indoors")


def _es_exterior(p: Dict[str, Any]) -> bool:
    """True si el escenario descripto es al aire libre (pileta, playa, jardín, calle…).
    Si nombra un interior (living, local, habitación…) gana el interior: "living con
    ventanal al jardín" es un living."""
    txt = " ".join(str(p.get(k, "")) for k in ("fondo", "escenario")).strip().lower()
    if not txt:
        return False
    def _hay(kws):
        return any(re.search(r"(?<![\wáéíóúñ])" + re.escape(k) + r"(?![\wáéíóúñ])", txt)
                   for k in kws)
    return _hay(_EXT_KW) and not _hay(_INT_KW)


_MUEVE_KW = ("caminando", "camina", "corriendo", "corre ", "trotando", "saltando", "salto",
             "girando", "bailando", "walking", "running", "jumping", "spinning", "mid-step",
             "en pleno paso", "zancada")


def _rincon(idx: int, hay_lugar: bool, en: bool = False, ext: bool = False,
            pose: str = "", cam: Optional[int] = None) -> str:
    """En qué parte del lugar transcurre esta toma. Vacío si no hay escenario descripto, o si
    la cámara (dron, bien lejos) ya decide qué se ve del lugar. Sólo rincones que no
    contradigan la pose (caminando ≠ apoyada) ni el lugar (sin vegetación ≠ bajo un árbol)."""
    if not hay_lugar:
        return ""
    if cam is not None and CAMARA_POOL[cam % len(CAMARA_POOL)].get("sin_rincon"):
        return ""
    pool = RINCON_POOL_EXT if ext else RINCON_POOL
    pl = " " + str(pose or "").lower() + " "
    fondo = _ctx("fondo", "") or ""
    _sin_veg = any(w in fondo for w in ("sin vegetación", "sin vegetacion", "sin plantas",
                                        "sin árboles", "sin arboles", "desierto", "médano", "medano"))
    _hay_agua = any(w in fondo for w in ("agua", "mar", "pileta", "piscina", "playa", "río", "rio ",
                                         "lago", "laguna", "costa", "orilla", "pool", "sea", "beach"))
    pool = [c for c in pool
            if not (c.get("quieta") and any(w in pl for w in _MUEVE_KW))
            and not (c.get("veg") and _sin_veg)
            and not (c.get("agua") and fondo and not _hay_agua)
            and not (c.get("tapa") and _ctx("kids"))] or pool
    c = pool[idx % len(pool)]
    return (("PLACE IN THE SCENE: " + ("he" if _ctx("hombre") else "she") + " is " + c["en"] + "."
             + _RINCON_NOTA_EN) if en
            else ("PARTE DEL LUGAR: está " + c["es"] + "." + _RINCON_NOTA_ES))


# Lugares que una pose escrita a mano suele nombrar. Si ella ya dijo dónde está, el
# recorrido por el lugar NO se mete: decirle "sentada en el mostrador" y abajo "está en el
# umbral de una puerta" es una contradicción, y el modelo terminaba tirando una de las dos.
_LUGAR_KW = (
    "mostrador", "espejo", "ventana", "vidriera", "puerta", "umbral", "pared", "piso",
    "suelo", "cama", "sillón", "sillon", "sofá", "sofa", "silla", "banco", "banqueta",
    "escalera", "escalón", "escalon", "perchero", "percha", "mesa", "mesada", "entrada",
    "rincón", "rincon", "esquina", "columna", "baranda", "alfombra", "barra", "probador",
    "cortina", "balcón", "balcon", "terraza", "arena", "pasto", "césped", "cesped",
    "agua", "pileta", "orilla", "borde", "estante", "góndola", "gondola", "pasillo",
    # Sólo giros que de verdad ubican. Nada de "sobre el" o "detrás de" sueltos: pescaban
    # "girando de 3/4 sobre el hombro" o "el pelo detrás de la oreja", que son del cuerpo.
    "fondo del", "al lado de", "junto a", "apoyada en", "apoyado en", "adentro de",
    "afuera de", "en el medio",
)


def _pose_dice_lugar(txt: str) -> bool:
    """True si la pose que escribió la usuaria ya dice EN QUÉ PARTE del lugar transcurre."""
    t = " " + str(txt or "").lower().strip() + " "
    return any(k in t for k in _LUGAR_KW)


def _hay_lugar(p: Dict[str, Any]) -> bool:
    """True si la usuaria describió una puesta en escena (y no un fondo liso de estudio)."""
    txt = " ".join(str(p.get(k, "")) for k in ("fondo", "escenario")).strip().lower()
    if len(txt) < 4:
        return False
    liso = ("fondo liso", "fondo blanco", "fondo negro", "fondo gris", "pared lisa",
            "estudio liso", "ciclorama", "infinito", "fondo plano", "fondo de estudio")
    return not any(w in txt for w in liso)


# ── DÓNDE ESTÁ LA CÁMARA ──
# El pool de poses dice qué hace la modelo y cuánto cuerpo entra en el cuadro, pero nunca
# dónde está parado el fotógrafo: sin esa indicación el modelo pone siempre la cámara a la
# altura de los ojos y de frente, así que la modelo rotaba y la cámara quedaba clavada.
# Estas posiciones rotan con el MISMO índice que la pose, así en un set de seis fotos hay
# seis cámaras distintas. `lenceria` marca las que no despiertan al checker de salida de
# Seedream en ropa interior: los contrapicados marcados sí lo despiertan.
CAMARA_POOL = [
    # "lbl" es lo que se lee en el desplegable y "ayuda" lo que explica el listado de la
    # pantalla: nombres de persona, no de fotógrafo (nada de "contrapicado" ni "cenital").
    {"lenceria": True, "lbl": "Normal, de frente", "ayuda": "La de siempre: el fotógrafo parado enfrente de ella.",
     "es": "Cámara a la altura de los ojos, de frente, a distancia normal.",
     "en": "Camera at eye level, straight on, normal distance."},
    {"lenceria": False, "lbl": "Desde más abajo", "ayuda": "El fotógrafo se agacha hasta la cintura y apunta un poco para arriba: las piernas se ven largas.",
     "es": "Cámara BAJA, a la altura de la cintura, apuntando apenas hacia arriba "
           "(contrapicado suave): la figura se ve más alta e imponente.",
     "en": "LOW camera at waist height, tilted slightly up (subtle low angle): the figure "
           "looks taller and commanding."},
    {"lenceria": True, "lbl": "De costado, en diagonal", "ayuda": "El fotógrafo se corre bastante a un costado y la toma en diagonal: se ve el fondo en perspectiva.",
     "es": "Cámara CORRIDA A UN COSTADO, en diagonal a unos 45 grados. La PERSPECTIVA "
           "CAMBIA DE VERDAD: las líneas del piso, de las paredes y de los muebles se van "
           "en DIAGONAL hacia un punto de fuga a un costado del cuadro, se ven DOS CARAS "
           "de las cosas (el frente y el lateral), un hombro de ella queda más cerca de la "
           "cámara que el otro y su cuerpo NO queda simétrico ni centrado de frente. "
           "PROHIBIDO que salga una foto frontal y simétrica con las líneas rectas.",
     "en": "Camera moved OFF TO ONE SIDE, a true 45-degree diagonal. The PERSPECTIVE REALLY "
           "CHANGES: the lines of the floor, walls and furniture run DIAGONALLY toward a "
           "vanishing point off to one side, TWO FACES of objects are visible (front and "
           "side), one of her shoulders is closer to the camera than the other, and her body "
           "is NOT symmetric or squarely frontal. A flat, symmetric, straight-on shot with "
           "level horizontal lines is FORBIDDEN."},
    {"lenceria": True, "lbl": "Desde más arriba", "ayuda": "El fotógrafo la saca desde un poco más alto que su cara, mirando para abajo. Es como cuando la foto te la saca alguien más alto.",
     "es": "Cámara ALTA, apenas por encima de su cara, mirando un poco hacia abajo (picado "
           "suave), como cuando alguien más alto saca la foto.",
     "en": "HIGH camera just above her face, tilted slightly down (subtle high angle), like "
           "a taller person taking the photo."},
    {"lenceria": True, "lbl": "De lejos, con zoom", "ayuda": "El fotógrafo se aleja y usa zoom: el fondo queda pegadito atrás y todo se ve más prolijo.",
     "es": "Cámara LEJOS con teleobjetivo: la perspectiva queda comprimida y el fondo "
           "aplanado y cerca de ella, como una foto sacada de lejos con zoom.",
     "en": "Camera FAR AWAY with a telephoto lens: compressed perspective, background "
           "flattened and pulled close behind her."},
    {"lenceria": True, "plano_propio": True, "lbl": "Muy cerca, a un paso", "ayuda": "El fotógrafo se le pone casi encima, a un paso: foto íntima, como sacada por una amiga.",
     "es": "Cámara MUY CERCA, a un paso de ella, a la altura de los ojos: foto íntima y "
           "cercana, como sacada por una amiga que está al lado.",
     "es_z": "Cámara CERCA de ella (lo más cerca que permita el encuadre pedido), a la altura "
             "de los ojos: foto íntima, como sacada por una amiga.",
     "en_z": "Camera CLOSE to her (as close as the requested framing allows), at eye level: an "
             "intimate photo, like one taken by a friend.",
     "en": "Camera VERY CLOSE, one step away, at eye level: an intimate, close photo, like "
           "one taken by a friend standing next to her."},
    {"lenceria": False, "lbl": "Desde el piso", "ayuda": "El fotógrafo apoya la cámara casi en el piso y apunta para arriba: se ve el techo o el cielo detrás.",
     "es": "Cámara MUY BAJA, casi apoyada en el piso, apuntando hacia arriba: se ve el "
           "techo o el cielo detrás de ella.",
     "en": "VERY LOW camera almost on the floor, pointing up: the ceiling or sky shows "
           "behind her."},
    {"lenceria": True, "plano_propio": True, "sin_rincon": True, "lbl": "Bien lejos, todo el lugar", "ayuda": "El fotógrafo se va bien atrás y entra todo el ambiente: ella chiquita adentro del lugar.",
     "es": "Cámara BIEN LEJOS, plano general: entra TODO el ambiente y ella queda CHICA "
           "adentro del lugar, con aire alrededor. Se sigue reconociendo la prenda.",
     "es_z": "Cámara ALEJADA con zoom: el fotógrafo se aleja y cierra con el teleobjetivo hasta "
             "el encuadre pedido; el lugar se ve comprimido detrás de ella.",
     "en_z": "Camera FAR BACK with a zoom: the photographer steps back and zooms in to the "
             "requested framing; the place looks compressed behind her.",
     "en": "Camera VERY FAR, wide establishing shot: the WHOLE room is in frame and she is "
           "SMALL inside it, with air around her. The garment is still readable."},
    {"lenceria": True, "sin_rincon": True, "lbl": "Desde el cielo (dron)", "ayuda": "La foto se saca desde MUY arriba, apuntando para abajo, como un dron. Si está acostada en el piso, se la ve desde el cielo hacia abajo, con el piso de fondo.",
     "es": "Cámara COMO UN DRON, muy por encima de ella y apuntando HACIA ABAJO. Si está "
           "acostada, sentada o en el piso, es CENITAL PURO: la cámara está justo encima, "
           "perpendicular al piso, se la ve de arriba hacia abajo y el FONDO DE LA FOTO ES "
           "EL PISO (la arena, el pasto, la alfombra, las sábanas); NO se ve el horizonte, "
           "ni el techo, ni las paredes. Si está de pie, la cámara igual está muy por "
           "encima de su cabeza mirando fuerte hacia abajo y se ve mucho piso alrededor.",
     "en": "DRONE-LIKE camera, far above her and pointing DOWN. If she is lying, sitting or "
           "on the ground, it is a PURE TOP-DOWN (bird's eye): the camera is directly above, "
           "perpendicular to the floor, she is seen from above and THE BACKGROUND OF THE "
           "PHOTO IS THE GROUND (sand, grass, rug, bedsheets); no horizon, no ceiling, no "
           "walls. If she is standing, the camera is still far above her head looking "
           "steeply down, with a lot of floor visible around her."},
    {"lenceria": True, "lbl": "Un paso al costado", "ayuda": "El fotógrafo da un paso al costado: queda casi de frente pero no enfrentado, y el lugar deja de verse plano.",
     "es": "Cámara CORRIDA UN PASO A UN COSTADO (unos 20 grados), a la altura de los ojos: "
           "queda casi de frente pero no enfrentada. Las líneas del piso y de las paredes "
           "ya salen apenas inclinadas, no rectas ni paralelas al borde de la foto.",
     "en": "Camera moved ONE STEP TO THE SIDE (about 20 degrees), at eye level: almost "
           "frontal but not head-on. The lines of the floor and walls already run slightly "
           "tilted, not straight and parallel to the edge of the photo."},
    {"lenceria": True, "lbl": "De cerca, se ve el lugar", "ayuda": "El fotógrafo se acerca con un lente que agarra más ancho: se ve más del ambiente alrededor.",
     "es": "Cámara CERCA con un gran angular leve: se exagera un poco la profundidad y se "
           "ve más del lugar alrededor.",
     "en": "Camera CLOSE with a slightly wide lens: depth is a bit exaggerated and more of "
           "the room shows around her."},
    {"lenceria": True, "lbl": "De costado del todo", "ayuda": "El fotógrafo se para justo al lado de ella y la toma de perfil, sin cambiarle la pose.",
     "es": "Cámara CORRIDA hasta quedar DE COSTADO (unos 90 grados), a la altura de los "
           "ojos: la misma pose vista de perfil desde afuera. Ella NO cambia de "
           "orientación: se movió el fotógrafo, no ella.",
     "en": "Camera moved until it is FULLY TO THE SIDE (about 90 degrees), at eye level: "
           "the same pose seen from the side. She does NOT turn: the photographer moved, "
           "not her."},
    {"lenceria": False, "lbl": "De abajo y de costado", "ayuda": "Las dos cosas juntas: agachado y corrido a un costado. Es el ángulo más 'de revista'.",
     "es": "Cámara BAJA (a la altura de la cadera) Y CORRIDA a un costado en diagonal al "
           "mismo tiempo: mira hacia arriba y desde afuera, como en las fotos de revista.",
     "en": "Camera LOW (hip height) AND moved diagonally to one side at the same time: "
           "looking up and from outside, magazine style."},
    {"lenceria": True, "lbl": "Agachado, al pecho", "ayuda": "El fotógrafo se agacha un poco, a la altura del pecho de ella. Un cambio chiquito que ya se nota.",
     "es": "Cámara apenas AGACHADA, a la altura del pecho de ella, casi derecha: un cambio "
           "chico pero que ya se nota en el fondo.",
     "en": "Camera slightly CROUCHED, at her chest height, nearly level: a small change "
           "that already shows in the background."},
    {"lenceria": True, "lbl": "Espiando, medio tapada", "ayuda": "La foto se saca desde atrás de unas plantas, una puerta o una percha: parece robada, no posada.",
     "es": "Cámara DETRÁS DE UN ELEMENTO REAL del lugar (unas hojas, el marco de una "
           "puerta, una percha con ropa), que aparece desenfocado en el borde del cuadro: "
           "foto robada, no posada.",
     "en": "Camera shooting THROUGH A REAL ELEMENT of the place (leaves, a doorframe, a "
           "clothing rack) blurred in the edge of the frame: a stolen shot, not posed."},
]
# Nunca cambia QUÉ parte del cuerpo entra en el cuadro: eso lo fija el encuadre.
_CAMARA_NOTA_ES = (" La cámara sólo cambia DESDE DÓNDE se mira, no qué parte del cuerpo "
                   "entra en el cuadro ni la pose.")
_CAMARA_NOTA_EN = " The camera only changes where it is shot from, not the framing or the pose."


def cam_idx(v: Any) -> Optional[int]:
    """Lo que eligió la usuaria en el selector de ángulo: None = automático (rota solo)."""
    if v is None:
        return None
    t = str(v).strip()
    if t in ("", "auto", "-1"):
        return None
    try:
        return int(t) % len(CAMARA_POOL)
    except (TypeError, ValueError):
        return None


def _auto_off(settings: Optional[Dict[str, Any]]) -> bool:
    """True si la usuaria apagó la variación automática: entonces la cámara sólo va si la
    eligió a mano, y no se agregan ni recorrido por el lugar ni tamaño de plano (la pose
    conserva el suyo, como antes de la v2.48)."""
    return str((settings or {}).get("variacion_auto", "si")).lower() in ("no", "0", "off", "false")


def _cam_i(cam: Optional[int], cam_auto: Optional[int], idx: int) -> int:
    """Qué número de cámara usa esta toma: la elegida a mano, la que le tocó al rotar en
    el set, o (si no hay ninguna) la que sale del número de pose."""
    if cam is not None:
        return cam
    if cam_auto is not None:
        return cam_auto
    return idx


# Normal, en diagonal, un paso al costado, agachado (a la altura del chico), de costado,
# desde más arriba, bien lejos, cerca con el lugar, dron.
_CAMS_KIDS = (0, 2, 9, 13, 11, 3, 7, 10, 8)


# El ángulo seguro más parecido a uno que el filtro bloquea con ropa interior.
_CAM_SEGURA_DE = {1: 13, 6: 13, 12: 2}


# Versión de catálogo de una pose escrita o copiada de una foto: lo que el filtro lee como
# sugerente (ojos cerrados, boca entreabierta, cabeza atrás, rodilla hacia la cámara,
# reclinada) pasa a su versión neutra. Sólo se usa para reintentar una toma bloqueada.
_POSE_SUAVE = (
    (r"ojos cerrados", "ojos abiertos"), (r"boca entreabierta|labios entreabiertos", "boca cerrada, sonrisa suave"),
    (r"cabeza echada (hacia )?atr[aá]s", "cabeza erguida"), (r"ment[oó]n (hacia )?arriba", "mentón natural"),
    (r"(rodilla|pierna) (derecha |izquierda )?(flexionada )?con la rodilla alta hacia (la )?c[aá]mara",
     "piernas juntas y dobladas hacia un costado"),
    (r"rodilla alta( hacia (la )?c[aá]mara)?", "rodillas juntas hacia un costado"),
    (r"reclinad[ao] hacia atr[aá]s", "derecho"), (r"recostad[ao]", "sentada derecha"),
    (r"acostad[ao]( boca arriba| boca abajo)?", "sentada derecha"),
    (r"piernas abiertas|piernas separadas", "piernas juntas"),
    (r"arqueand[oa] la espalda|espalda arqueada", "espalda derecha"),
    (r",? ?(levemente |apenas )?contrapicad[ao]", ""), (r",? ?casi al ras de la superficie", ""),
    (r"a la altura de la cintura", "a la altura del pecho"),
    (r"mano (derecha |izquierda )?en la base del cuello", "mano relajada sobre el muslo"),
    (r"expresi[oó]n [^,.;]*(sensual|placer|éxtasis|extasis|seductor[ae]?)[^,.;]*", "expresión serena y natural"),
)


def _pose_suave(t: str) -> str:
    for a, b in _POSE_SUAVE:
        t = re.sub(a, b, t, flags=re.I)
    return t


def _cam_entry(idx: int, lenceria: bool = False, elegida: bool = False) -> Dict[str, Any]:
    """La posición de cámara que de verdad va a usar esta toma. En lencería, cuando rota
    sola, se rota SÓLO entre las seguras, así que la entrada no es CAMARA_POOL[idx]."""
    if _ctx("kids") and not elegida:
        # CHICOS: cuando la cámara rota sola, sólo ángulos neutros. Desde abajo, desde el
        # piso, con zoom de lejos, "muy cerca, foto íntima" o "espiando, medio tapada" con un
        # chico en malla hacen que el filtro bloquee la toma (y no son fotos de catálogo
        # infantil). Si ella elige uno a mano, se respeta.
        safe = [CAMARA_POOL[i] for i in _CAMS_KIDS]
        return safe[idx % len(safe)]
    if lenceria and not elegida:
        safe = [x for x in CAMARA_POOL if x["lenceria"]]
        return safe[idx % len(safe)]
    return CAMARA_POOL[idx % len(CAMARA_POOL)]


def _camara(idx: int, lenceria: bool = False, en: bool = False,
            elegida: bool = False) -> str:
    """La posición de cámara de esa toma. Cuando la cámara ROTA sola, en lencería se rota
    SOLO entre las que no despiertan al checker de Seedream (mandarlas todas a la neutra
    hacía que dos tomas del set salieran desde el mismo lugar, que es justo lo que se
    quería arreglar); si la eligió la usuaria a mano se respeta lo que pidió: es su
    decisión, no la nuestra."""
    c = _cam_entry(idx, lenceria, elegida)
    if c.get("plano_propio"):
        if _ctx("zona"):
            # Ella fijó el encuadre: esta cámara aporta sólo distancia y ángulo.
            return ("CAMERA: " + c["en_z"] + _CAMARA_NOTA_EN) if en else ("CÁMARA: " + c["es_z"] + _CAMARA_NOTA_ES)
        # Esta cámara SÍ decide cuánto entra: decir "no cambia el encuadre" era contradecirla.
        return ("CAMERA: " + c["en"] + " This camera also sets how much of her is in frame.") if en else (
            "CÁMARA: " + c["es"] + " Esta cámara también fija cuánto cuerpo entra en el cuadro.")
    return ("CAMERA: " + c["en"] + _CAMARA_NOTA_EN) if en else ("CÁMARA: " + c["es"] + _CAMARA_NOTA_ES)


def _cam_fija_plano(idx: Optional[int], lenceria: bool = False, elegida: bool = False) -> bool:
    """True si la cámara de esta toma decide ella misma el tamaño (muy cerca / bien lejos) y
    no hay un encuadre elegido: entonces la pose no puede traer otro tamaño de plano."""
    return (idx is not None and not _ctx("zona")
            and bool(_cam_entry(idx, lenceria, elegida).get("plano_propio")))


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


_MIRA_KW = ("mirando", "mira ", "mira a", "mirada", "a cámara", "a la cámara", "hacia la cámara",
            "por encima del hombro", "ojos cerrados", "de espaldas")


def _expr(pose: str = "") -> str:
    """Una expresión al azar. Si la pose ya dice hacia dónde mira, sólo las que no dicen
    nada de la mirada ("mirando a cámara" + "mirada perdida fuera de cuadro" se peleaban)."""
    pl = " " + str(pose or "").lower() + " "
    pool = EXPRESION_VARIANTS
    if any(w in pl for w in _MIRA_KW):
        pool = [e for e in EXPRESION_VARIANTS if "mirada" not in e and "ojos" not in e] or pool
    return random.choice(pool)


# ── Poses masculinas (mismo índice/tamaño de plano que POSE_POOL, en gramática varón) ──
POSE_POOL_H = [
    "CUERPO ENTERO, de pie con el peso en una pierna, una mano relajada al costado y la otra "
    "suelta, hombros relajados, cuerpo en leve torsión, actitud segura y desprevenida",
    "PLANO MEDIO, girado en 3/4 mirando por encima del hombro hacia la cámara, expresión "
    "tranquila y natural",
    "PLANO MEDIO/AMERICANO, sentado de forma relajada en lo que el lugar tenga (un sillón, la cama, un banco, un escalón, la arena o el piso), torso "
    "erguido y en leve giro, antebrazos apoyados con naturalidad, piernas fuera de cuadro o "
    "apenas insinuadas",
    "DE ESPALDA mostrando la parte de atrás de la prenda, cabeza girada hacia la cámara, "
    "una mano al costado, cuerpo en contrapposto",
    "CUERPO ENTERO, caminando hacia la cámara en pleno paso, una pierna adelante, brazos "
    "sueltos en movimiento, dinámico y natural",
    "PLANO MEDIO, riéndose de forma genuina con la cabeza apenas hacia atrás, mirada fuera de "
    "cuadro, gesto totalmente desprevenido",
    "CUERPO ENTERO, DE TRES CUARTOS (el cuerpo girado apenas hacia la cámara, NO de perfil "
    "puro: el FRENTE de la prenda tiene que verse entero, con SU diseño y SUS colores "
    "exactos de las fotos del producto), apoyado de costado contra un elemento REAL del "
    "escenario pedido (lo que ese lugar tenga de verdad: una palmera, una roca, una "
    "baranda, el marco de una puerta, una columna, la pared de la casa si la escena es "
    "interior), un pie cruzado sobre el otro, mirada relajada a lo lejos. PROHIBIDO "
    "inventar una pared "
    "suelta que no pertenece al lugar (una pared en medio de la playa no existe): si el "
    "escenario es exterior y no hay dónde apoyarse, va de perfil apoyando el peso en una "
    "pierna, sin apoyarse en nada. PROHIBIDO agregar un mueble, una pared, una baranda o un objeto que el lugar no tenga para que ella tenga dónde apoyarse: movela HASTA DONDE de verdad hay algo (la pared, el marco de una puerta, una columna, el mostrador, la cabecera de la cama). El lugar NO SE AMUEBLA. Si en todo el lugar no hay nada, no se apoya en nada",
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
    "elemento, mirada lejos de la cámara, como un descuido editorial. PROHIBIDO agregar un mueble, una pared, una baranda o un objeto que el lugar no tenga para que ella tenga dónde apoyarse: movela HASTA DONDE de verdad hay algo (la pared, el marco de una puerta, una columna, el mostrador, la cabecera de la cama). El lugar NO SE AMUEBLA. Si en todo el lugar no hay nada, no se apoya en nada",
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
    "MEDIUM SHOT, SITTING relaxed on whatever the place has (a couch, the bed, a bench, a step, the sand or the floor), upright torso with a "
    "slight turn, hands resting naturally",
    "SEEN FROM BEHIND, showing the BACK of the garment, head turned toward the camera, "
    "one hand on the nape, contrapposto",
    "FULL BODY, WALKING toward the camera mid-step, one leg forward, arms loose and in "
    "motion, dynamic and natural",
    "MEDIUM SHOT, genuine laugh with the head slightly back, looking off-camera, candid "
    "caught moment",
    "FULL BODY in THREE-QUARTER view (body turned slightly toward the camera, NOT a pure "
    "side profile: the FRONT of the garment must be fully visible, with its exact design "
    "and colors from the product photos), leaning sideways on a REAL element of the "
    "requested setting (whatever that place actually has: a palm tree, a rock, a railing, "
    "a door frame, a column, the house wall if the scene is indoors), one leg crossed, "
    "relaxed gaze into the distance. Never invent a wall, furniture, a railing or any "
    "object the place does not have (a wall in the middle of a beach does not exist): MOVE "
    "HER TO WHERE something really is; the place is NOT furnished. If it has nothing to "
    "lean on, she stands three-quarter with the weight on one leg, leaning on nothing",
    "MEDIUM SHOT, stretching naturally or adjusting a strap, loose shoulders, fresh "
    "real-moment expression",
    "EXTREME CLOSE-UP of face and shoulders, the face fills the frame, slight head "
    "turn, eyes to camera, focus on skin detail and expression",
    "PRODUCT DETAIL SHOT (zoom): close-up catalog crop of the GARMENT itself, framed "
    "from the shoulders down like a standard e-commerce fabric detail photo, the "
    "garment fills the frame, fabric and seams tack-sharp",
    "BACK DETAIL SHOT (zoom): close-up catalog crop of the BACK of the garment, framed "
    "from the shoulders down, hands adjusting the garment behind the back, focus on the "
    "back closure and finish of the garment",
    "FULL BODY OR 3/4, physically INTERACTING with the scene: leaning or reclining on a "
    "real element of the set (a palm tree, a rock, a wall, a door frame, a couch), body "
    "in natural contact with it, gaze away from the camera, editorial candid. It is FORBIDDEN to add furniture, a wall, a railing or any object the place does not have so she has something to lean on: MOVE HER TO WHERE something really is (the wall, a door frame, a column, the counter). The place is NOT furnished. If the place has nothing, she leans on nothing",
    "LYING OR SITTING ON THE GROUND of the scene (the sand, the grass, a rug, the bed), "
    "body relaxed on the floor, propped on the forearms or on one side, the environment "
    "touching the skin, gaze lost or up toward the camera",
    "MEDIUM SHOT holding ONE simple casual prop coherent with the scene (a fruit, a "
    "glass or bottle, a hat, a mug), interacting with it candidly, gaze at the prop or "
    "off-camera, stolen-moment feel",
]

# Versión "de catálogo" de cada pose del pool para LENCERÍA/BAÑO en Seedream. Su
# checker de SALIDA (ByteDance, no se apaga desde fal) tira la imagen ya generada
# cuando la pose muestra demasiado: acostada en el piso, de espalda con la mano en
# la nuca, acomodándose el bretel. En la prueba real del 14/9 pasaba la toma de
# frente y rebotaban 4 de 5 con las poses variadas. Misma variedad, menos riesgo.
POSE_SEGURA_FLUX = {
    0: "FULL BODY, standing straight and relaxed, weight evenly on both feet, arms loose at "
       "the sides, natural smile at the camera, like a mainstream underwear catalog",
    2: "MEDIUM SHOT, sitting upright on whatever the place has (a chair, the edge of the bed, a step, the sand), knees together, hands "
       "resting on the lap, calm catalog attitude",
    3: "SEEN FROM BEHIND, standing straight with the arms relaxed at the sides, head turned "
       "toward the camera, showing the BACK of the garment as a catalog back view",
    7: "MEDIUM SHOT, standing relaxed, hands loose, fresh natural expression, catalog framing",
    11: "FULL BODY OR 3/4, standing next to a real element of the setting with one hand "
        "resting on it (a door frame, a railing, the back of a chair), gaze away from the "
        "camera, editorial but composed",
    12: "SITTING on the ground of the scene (the sand, a rug, the bed) with the legs folded "
        "to one side, upright torso, hands resting, calm gaze toward the camera",
}


# Lo mismo para Nano Banana. Las poses del pool que en lencería Gemini lee como
# sugerentes ("cadera quebrada… media sonrisa cómplice", "tirada en el piso o la cama,
# el entorno tocando la piel, mirada a cámara desde abajo") tienen acá su versión de
# catálogo. NO se usan de entrada: sólo cuando Gemini bloquea la toma por IMAGE_SAFETY,
# en un reintento. Si la pose original pasa, queda la original.
POSE_SEGURA_ES = {
    0: "CUERPO ENTERO, de pie derecha y relajada, el peso repartido en los dos pies, los "
       "brazos sueltos al costado del cuerpo, sonrisa natural a cámara, como en el catálogo "
       "de ropa interior de una marca masiva",
    2: "PLANO MEDIO, sentada derecha en una silla o un banco, las rodillas juntas, las manos "
       "apoyadas sobre las piernas, actitud tranquila de catálogo",
    3: "DE ESPALDA, parada derecha con los brazos relajados al costado, la cabeza girada "
       "hacia la cámara, mostrando la ESPALDA de la prenda como en la vista trasera de un "
       "catálogo",
    7: "PLANO MEDIO, parada y relajada, las manos sueltas, expresión fresca y natural, "
       "encuadre de catálogo",
    11: "CUERPO ENTERO O AMERICANO, parada junto a un elemento real del lugar con una mano "
        "apoyada en él (el marco de una puerta, una baranda, el respaldo de una silla), "
        "mirada fuera de cámara, editorial pero compuesta",
    12: "SENTADA en el piso de la escena (una alfombra, la arena, el pasto) con las piernas "
        "plegadas hacia un costado, el torso derecho, las manos apoyadas, mirada tranquila "
        "a cámara",
}
_EXPR_SEGURA = "Expresión: sonrisa natural y tranquila, mirada serena."


def _pose_segura_prompt(prompt: str) -> Optional[str]:
    """Si el prompt lleva una pose del pool que tiene versión segura, la cambia."""
    for idx, seguro in POSE_SEGURA_FLUX.items():
        original = POSE_POOL_FLUX[idx]
        if original in prompt and seguro not in prompt:
            return prompt.replace(original, seguro)
    return None


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


_TR_PFX = "imagenes:tr:"          # caché global de traducciones (por texto, no por usuaria)
_TR_TTL = 30 * 86400


async def _al_ingles(textos: Dict[str, str]) -> Dict[str, str]:
    """Traduce al inglés, literal y técnico, lo que escribió la usuaria para una toma de
    Seedream/Qwen/FLUX (la toma, el escenario, la luz, el encuadre). Esos motores leen
    inglés: con el castellano entero y dos palabras sueltas en inglés hacían lo que
    querían. Una llamada por lote, con caché por texto (la misma toma escrita no se vuelve
    a traducir). Si algo falla devuelve lo que pudo (o {}) y el que llama usa el
    castellano con las pistas por palabras clave, como antes."""
    pend = {k: str(v).strip() for k, v in (textos or {}).items() if str(v or "").strip()}
    out: Dict[str, str] = {}
    for k, v in list(pend.items()):
        try:
            c = await kv.get(_TR_PFX + hashlib.sha1(v.lower().encode("utf-8")).hexdigest()[:20])
        except Exception:
            c = None
        if isinstance(c, dict) and str(c.get("en", "")).strip():
            out[k] = str(c["en"]).strip()
            pend.pop(k)
    if not pend:
        return out
    key = await _current_api_key()
    if not key:
        return out
    pedido = (
        "Traducí al inglés estos textos escritos por una fotógrafa de moda para UNA toma de "
        "catálogo: son instrucciones para un modelo de imagen. Traducilos LITERALES y "
        "completos, sin agregar ni sacar nada, sin adornar ni interpretar, con vocabulario "
        "de fotografía (medium shot, close-up, full body, seen from behind, side profile, "
        "leaning on, looking over her shoulder, low angle, waistband, strap…). Si un texto "
        "ya está en inglés, devolvelo igual. Devolvé SOLO un JSON con las MISMAS claves:\n"
        + json.dumps(pend, ensure_ascii=False)
    )
    try:
        body = {"contents": [{"role": "user", "parts": [{"text": pedido}]}],
                "generationConfig": {"temperature": 0.1,
                                     "responseMimeType": "application/json"}}
        async with httpx.AsyncClient(timeout=45) as cli:
            r = await cli.post(ANALYZE_ENDPOINT, json=body,
                               headers={"x-goog-api-key": key,
                                        "Content-Type": "application/json"})
        if r.status_code != 200:
            print(f"[imagenes_ia] traducción HTTP {r.status_code}: {r.text[:200]}")
            return out
        raw = "".join(pt.get("text", "") for pt in
                      r.json()["candidates"][0]["content"]["parts"])
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return out
        for k, v in pend.items():
            en = str(data.get(k) or "").strip()
            if not en or len(en) > max(60, 4 * len(v)):
                continue
            out[k] = en
            try:
                await kv.set(_TR_PFX + hashlib.sha1(v.lower().encode("utf-8")).hexdigest()[:20],
                             {"es": v, "en": en, "ts": int(time.time())}, ttl=_TR_TTL)
            except Exception:
                pass
    except Exception as e:
        print(f"[imagenes_ia] traducción error: {e}")
    return out


def _pose_en_hints(txt: str) -> str:
    """Suma equivalentes en inglés a una pose escrita en castellano (Seedream lee inglés).
    Es el respaldo cuando `_al_ingles` no pudo traducir la toma entera."""
    low = txt.lower()
    hits = [en for kws, en in _POSE_EN_KW if any(k in low for k in kws)]
    return (" (in English: " + "; ".join(hits) + ")") if hits else ""


_MASC = ("hombre", "varon", "varón", "masculino", "hombres", "h", "m", "male")


def _es_hombre(genero: Optional[str]) -> bool:
    return str(genero or "").strip().lower() in _MASC


def _pose_pool(genero: Optional[str]) -> List[str]:
    return POSE_POOL_H if _es_hombre(genero) else POSE_POOL


def _a_modelo(gw: Dict[str, str]) -> str:
    """"a la modelo" / "al modelo" (no "a el modelo")."""
    return "al modelo" if gw["modelo"] == "el modelo" else "a " + gw["modelo"]


# Los bloques fijos en castellano están escritos para una mujer ("ella PROYECTA SU SOMBRA",
# "la modelo", "inclinada"): con un modelo varón o un nene se pasan a masculino.
_MASC_ES = (
    (r"\bLa modelo\b", "El modelo"), (r"\bla modelo\b", "el modelo"), (r"\bLA MODELO\b", "EL MODELO"),
    (r"\bde la modelo\b", "del modelo"), (r"\ba la modelo\b", "al modelo"),
    (r"\bde el modelo\b", "del modelo"), (r"\ba el modelo\b", "al modelo"),
    (r"\bde ella\b", "de él"), (r"\ba ella\b", "a él"), (r"\bella\b", "él"), (r"\bElla\b", "Él"),
    (r"\bELLA\b", "ÉL"), (r"\bél nítida\b", "él nítido"), (r"\bfotografiada\b", "fotografiado"),
)
# Adjetivos de ELLA sólo detrás de un verbo ("está sentada", "queda inclinada"): sueltos
# pueden ser de otra cosa ("la cabeza inclinada", "la malla sola", "postura relajada").
_ADJ_ELLA = ("sentada", "parada", "apoyada", "inclinada", "suspendida", "acostada", "recostada",
             "girada", "captada", "agachada", "arrodillada", "vestida", "quieta", "sola",
             "desprevenida", "concentrada", "orgullosa", "tranquila", "dormida", "inventada",
             "relajada", "pegada", "mojada", "peinada", "chica")
_RE_ADJ_ELLA = re.compile(r"\b(est[áa]|esté|queda|quede|quedar|parezca|parece|sale|salga|"
                          r"estar|sea|es|aparece|ve|vea|luce)( (?:bien |muy |apenas |un poco )?)("
                          + "|".join(_ADJ_ELLA) + r")\b", re.I)


def _masc_es(t: str) -> str:
    for a, b in _MASC_ES:
        t = re.sub(a, b, t)
    return _RE_ADJ_ELLA.sub(lambda m: m.group(1) + m.group(2) + m.group(3)[:-1]
                            + ("O" if m.group(3).isupper() else "o"), t)


def _gwords(genero: Optional[str]) -> Dict[str, str]:
    """Palabras que cambian según el género de la modelo, para no pedir 'mujer' a un hombre."""
    if _es_hombre(genero):
        return {"modelo": "el modelo", "modelo_cap": "El modelo",
                "persona": "un varón adulto", "una_sola": "un solo modelo adulto (varón)",
                "genero_n": "varón", "genero_p": "varones", "gen_adj": "masculino"}
    return {"modelo": "la modelo", "modelo_cap": "La modelo",
            "persona": "una mujer adulta", "una_sola": "una sola modelo adulta (mujer)",
            "genero_n": "mujer", "genero_p": "mujeres", "gen_adj": "femenina"}


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
    "fondo sin tocar nada.\n"
    "COHERENCIA DEL LUGAR (importante): todo lo que aparece en la escena — paredes, muebles, "
    "objetos, aquello en lo que se apoya — tiene que PERTENECER de verdad a ese escenario. Si "
    "el lugar es la playa, se apoya en una palmera, una roca, una baranda de madera o el borde "
    "de una construcción que sí esté ahí; PROHIBIDO plantar una pared suelta, un sillón o un "
    "mueble de interior en medio de un exterior: queda irreal al instante."
)

# Para la toma individual, donde la pose es OBLIGATORIA: el bloque de arriba le pedía
# "manos en el pelo o en la cintura", "variá la mirada", "se apoya, se sienta o se
# recuesta" — otra pose encima de la pedida. Acá la energía va DENTRO de la pose.
VIDA_BLOCK_TOMA = (
    "ENERGÍA Y NATURALIDAD (muy importante): es FOTOGRAFÍA EDITORIAL DE CAMPAÑA real y "
    "espontánea (como un catálogo impreso de una marca top), NO catálogo rígido ni pose de "
    "maniquí. La pose es LA DE ESTA TOMA (bloque de arriba): no la cambies ni la "
    "'mejores'. DENTRO de esa pose, que se sienta un instante real captado en pleno gesto: "
    "cuerpo relajado, nada tieso ni perfectamente simétrico, manos con intención y no "
    "pegadas al cuerpo, la mirada con la actitud de la expresión pedida. La luz de la escena "
    "le pega de verdad al cuerpo: sombras reales, brillos donde da la luz; vida y "
    "movimiento en el pelo y la tela. EVITÁ: sonrisa fija de catálogo, expresión congelada, "
    "brazos tiesos, persona 'pegada' sobre el fondo sin tocar nada.\n"
    "COHERENCIA DEL LUGAR (importante): todo lo que aparece en la escena — paredes, muebles, "
    "objetos, aquello en lo que se apoya — tiene que PERTENECER de verdad a ese escenario. Si "
    "el lugar es la playa, se apoya en una palmera, una roca, una baranda de madera o el borde "
    "de una construcción que sí esté ahí; PROHIBIDO plantar una pared suelta, un sillón o un "
    "mueble de interior en medio de un exterior: queda irreal al instante."
)

CALIDAD_BLOCK = (
    "NATURALIDAD (que NO parezca imagen generada): tiene que verse como una foto real, no como "
    "un render. Piel con textura natural, poros y algún matiz o brillo real donde pega la luz; "
    "ni plástica/cerosa ni con imperfecciones exageradas — piel sana y creíble. Ojos con detalle "
    "y brillo natural. Pelo con algunos pelitos sueltos, no un bloque sólido. Manos y pies "
    "correctos: cinco dedos por mano y por pie, sin dedos de más o de menos ni manos deformadas. "
    "BRAZOS BIEN PUESTOS (revisalo antes de dar la imagen por buena): la persona tiene UN brazo "
    "izquierdo y UN brazo derecho, cada uno saliendo de SU hombro, con su codo doblando para el "
    "lado correcto y con la mano que le corresponde (el pulgar del lado que va según cómo esté "
    "girada). PROHIBIDO: dos brazos izquierdos o dos derechos, manos espejadas o dadas vuelta, un "
    "brazo de más, o un brazo que nace de un lugar que no es el hombro. Lo mismo para piernas y "
    "pies. "
    "Proporciones humanas naturales y postura coherente. Evitá el aspecto 3D/CGI, el "
    "sobre-nitidez artificial y la simetría perfecta."
)

# Errores que se repetían: gente flotando o apoyada en el aire, y fondos a escala
# equivocada (palmeras enanas detrás = la modelo parece gigante).
FISICA_BLOCK = (
    "\n\nFÍSICA Y APOYO (crítico, mirá la foto antes de darla por buena):\n"
    "• GRAVEDAD: el cuerpo está SOSTENIDO de verdad. Si está de pie, las DOS plantas de "
    "los pies (o al menos la de la pierna que carga el peso) apoyan completas en el "
    "suelo, a la altura del suelo; si está sentada, la cola y los muslos apoyan en la "
    "superficie; si se apoya en algo, esa parte del cuerpo TOCA el objeto y el peso "
    "descansa ahí. Si se apoya DE COSTADO contra algo vertical (una pared, el marco de "
    "una puerta o de una ventana, una columna), el hombro y el brazo APOYAN de verdad "
    "contra esa superficie, aplastándose un poco contra ella, y el cuerpo queda inclinado "
    "HACIA ese apoyo — no puede quedar inclinada al aire, a un palmo de la superficie, "
    "como si el apoyo no existiera. PROHIBIDO que flote, que quede suspendida contra un tronco o una "
    "pared, que los pies queden en el aire o hundidos dentro del piso (SALVO en pleno salto "
    "o carrera, si la toma lo pide: ahí el salto es real y su sombra queda en el piso, "
    "separada de los pies).\n"
    "• PUNTO DE APOYO CREÍBLE: la postura tiene que poder sostenerse en la vida real "
    "(centro de gravedad sobre el pie que carga). Nada de poses imposibles de equilibrio.\n"
    "• ESCALA DEL FONDO: todo lo del entorno va en su TAMAÑO REAL respecto de la persona. "
    "Por ejemplo, un árbol, un edificio o un médano detrás de ella la superan MUCHO en "
    "altura, no pueden verse enanos (sólo si el lugar los tiene: no los agregues). PROHIBIDO que la persona parezca gigante "
    "sobre un paisaje en miniatura o parada sobre una loma con el fondo muy abajo.\n"
    "• SOMBRA PROPIA: ella PROYECTA SU SOMBRA sobre el piso, la pared o el mueble que "
    "tenga al lado, con la forma, la dirección y la dureza que le corresponden a la luz de "
    "la escena (sol fuerte = sombra marcada y definida; interior nublado = sombra suave y "
    "difusa). Donde el cuerpo toca una superficie, ahí hay una sombra de contacto más "
    "oscura. PROHIBIDO que quede sin sombra, como pegada encima del fondo.\n"
    "• MARCAS AJENAS: NINGÚN objeto de la escena lleva logos, isotipos, etiquetas ni "
    "nombres de marcas reales (ni gaseosas, ni ropa, ni electrónica, ni carteles de "
    "negocios). Las botellas, vasos, toallas, bolsos y revistas van LISOS o con un diseño "
    "inventado sin texto legible. La única marca que puede aparecer es la de la prenda, y "
    "sólo si la foto real del producto la muestra.\n"
    "• CÁMARA: la altura y el lugar los fija el renglón CÁMARA de esta toma; si no hay "
    "ninguno, va a la altura de los ojos de un fotógrafo parado (~1,60 m). Sea cual sea "
    "la altura, la LÍNEA DEL HORIZONTE y las perspectivas tienen que corresponder A ESA "
    "altura: si la cámara está baja se ve poco piso y aparece el techo o el cielo; si "
    "está alta se ve más piso y el horizonte queda arriba."
)

# Los cortes de color (dónde termina la estampa y empieza el liso) son la firma de la
# prenda: si se corren, la prenda deja de ser la misma aunque la tela esté bien.
CORTES_BLOCK = (
    "\n\nCORTES Y PANELES DE LA PRENDA (mirá bien las fotos reales): respetá EXACTAMENTE "
    "dónde EMPIEZA y dónde TERMINA cada tela y cada color sobre el cuerpo. Si la parte "
    "estampada llega hasta justo debajo del busto y de ahí para abajo la prenda es lisa, "
    "el corte va exactamente ahí — ni más arriba ni más abajo. Lo mismo con las bandas, "
    "vivos, franjas de tul/mesh, elásticos y ribetes: van a la MISMA altura, del MISMO "
    "ancho y en el MISMO orden que en la foto real. PROHIBIDO correr el corte, agrandar "
    "o achicar el panel estampado, saltear una banda intermedia o cambiar el orden de "
    "los paneles."
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
                    genero: Optional[str] = None, zona: bool = False,
                    lenc: bool = False, cam: Optional[int] = None,
                    cam_auto: Optional[int] = None, lugar: bool = False,
                    ext: bool = False,
                    auto: bool = True) -> str:
    if n <= 1:
        return ""
    pool = _pose_pool(genero)
    filas = []
    for i in range(n):
        k = pose_offset + i
        pose = pool[k % len(pool)]
        _ic = _cam_i(cam, cam_auto, k)
        _ip = _cam_i(None, cam_auto, k)
        # Cada panel con su tamaño de plano propio; a la pose se le saca el suyo para que
        # no queden dos órdenes de encuadre peleando en el mismo renglón.
        _ap = auto and _plano_aparte(k, _ic, zona, lenc, cam is not None)
        if zona or _ap or _cam_fija_plano(_ic, lenc, cam is not None):
            pose = _pose_sin_plano(pose)
        # Con la variación apagada, la cámara va sólo si la eligió ella; nada de recorrido.
        cam_txt = (_camara(_ic, lenc, elegida=cam is not None)
                   if (auto or cam is not None) else "")
        filas.append(f"  · Panel {i + 1}: {pose}. {_expr(pose)} "
                     + (_plano(_ip, lenceria=lenc) + " " if _ap else "")
                     + (cam_txt + " " if cam_txt else "")
                     + (_rincon(_ip, lugar, ext=ext, pose=pose, cam=_ic) if auto else ""))
    detalle = "\n".join(filas)
    encuadre = (
        "TODOS los paneles con el MISMO tamaño de plano: el del ENCUADRE OBLIGATORIO de "
        "arriba (cambia la pose y el gesto, no lo que entra en el cuadro)."
        if zona else
        "Cada panel DEBE tener una pose CLARAMENTE distinta — distinta orientación del cuerpo, "
        "distinto gesto y, MUY IMPORTANTE, distinto ENCUADRE: cada panel usa EXACTAMENTE el "
        "TAMAÑO DE PLANO que se le indica abajo y NO puede repetir el del panel de al lado."
    )
    return (
        f"\nIMPORTANTE — {n} TOMAS DISTINTAS EN UNA SOLA IMAGEN ({aspect}):\n"
        f"Generá {n} fotos de la MISMA modelo con la MISMA prenda, lado a lado, separadas por "
        f"una LÍNEA BLANCA VERTICAL limpia, recta y pareja (blanco puro, ~1.5% del ancho).\n"
        f"{encuadre} NO repitas la "
        f"misma pose con cambios mínimos. Cada panel además tiene su PROPIA POSICIÓN DE "
        f"CÁMARA — la cámara se mueve de panel a panel (no queda siempre a la altura de los "
        f"ojos y de frente), y el lugar se ve desde un ángulo distinto en cada uno. "
        f"Asigná exactamente estas poses y cámaras:\n{detalle}\n"
        f"Poses espontáneas y desprevenidas (estilo Instagram), no acartonadas. Sin texto entre paneles."
    )


_LENC_KW = ("lenceria", "lencería", "corpiño", "corpino", "bralette", "bombacha", "tanga",
            "colaless", "encaje", "brasier", "brasiere", "sostén", "soften", "underwear",
            "lingerie", "bikini", "malla", "traje de baño", "conjunto de encaje",
            "boxer", "bóxer", "calzoncillo", "slip", "bombachón", "culotte")


def _es_ropa_interior(p: Dict[str, Any]) -> bool:
    """True si la toma es de ropa interior/lencería (corpiño/top solo, set de lencería, o
    cuando el texto del producto/tela lo indica)."""
    if str(p.get("no_lenceria", "")).lower() in ("si", "sí", "true", "1", "on"):
        return False      # set armado desde una escena de ropa (ej. deportiva): NO es lencería
    # OJO: la casilla "completar" (tildada por defecto) es la señal de "lencería" que usa
    # toda la app para quedarse en ángulos, planos y poses que el filtro acepta. Si se le
    # saca (v2.75.0 lo hizo), un conjunto o una malla vuelven a la lista completa —desde el
    # piso, plano al pecho— y el filtro bloquea. La bombacha extra sí se decide aparte
    # (_complemento_aplica); acá la casilla sigue contando como lencería.
    if str(p.get("complemento", "")).lower() in ("si", "sí", "true", "1", "on", "auto"):
        return True
    if str(p.get("color_set", "")).strip():
        return True
    if str(p.get("lenceria", "")).lower() in ("si", "sí", "true", "1", "on"):
        return True
    txt = " ".join(str(p.get(k, "")) for k in
                   ("producto_manual", "prenda_desc", "tela", "aclaraciones", "piezas",
                    "ficha")).lower()
    return any(kw in txt for kw in _LENC_KW)


# ── ENCUADRE POR ZONA DE LA PRENDA ──
# El "Encuadre" escrito iba como un renglón más de la puesta en escena, y cada pose del
# pool trae su propio tamaño de plano ("CUERPO ENTERO", "PLANO MEDIO") con la orden de
# respetarlo "exactamente": la pose siempre le ganaba al encuadre, y una bombacha salía
# de pies a cabeza. Ahora el encuadre por zona MANDA sobre el tamaño de plano de la pose
# (la pose aporta postura, orientación y gesto), en Nano Banana, en fal y en el set.
ENCUADRE_ZONA = {
    "abajo": (
        "PRENDA DE ABAJO — plano de la CINTURA PARA ABAJO: el cuadro va desde la cintura "
        "(el ombligo) hasta los pies, o hasta las rodillas si está sentada; la cadera, la "
        "cola y las piernas bien a la vista y la prenda de abajo como protagonista. La cara "
        "y el torso QUEDAN FUERA DE CUADRO, cortados por el borde superior, como en las "
        "fotos de bombachas y bóxers de un catálogo real (no es un error, es el encuadre).",
        "BOTTOM GARMENT FRAMING — WAIST-DOWN crop: the frame runs from the waist (navel) "
        "down to the feet, or to the knees if seated; hips and legs fully visible and the "
        "bottom garment is the subject. The face and upper torso are OUT OF FRAME, cut by "
        "the top edge, like a real catalog panty or boxer photo (this is intended)."),
    "abajo_cerca": (
        "PRENDA DE ABAJO, DE CERCA — de la cintura a las rodillas: la prenda de abajo llena "
        "el cuadro, la cadera y la parte alta de los muslos a la vista; cara, torso y pies "
        "fuera de cuadro, como un plano detalle de catálogo (no es un error).",
        "BOTTOM GARMENT CLOSE FRAMING — WAIST-TO-KNEES crop: the bottom garment fills the "
        "frame, hips and upper thighs visible; face, torso and feet out of frame, like a "
        "catalog detail shot (this is intended)."),
    "arriba": (
        "PRENDA DE ARRIBA — plano de la CINTURA PARA ARRIBA: de la cabeza a la cintura o la "
        "cadera, con la cara visible y la prenda de arriba como protagonista; las piernas "
        "quedan fuera de cuadro.",
        "TOP GARMENT FRAMING — WAIST-UP crop: from the head down to the waist or hips, face "
        "visible and the top garment is the subject; the legs are out of frame."),
    "arriba_cerca": (
        "PRENDA DE ARRIBA, DE CERCA — de los hombros a la cintura: la prenda de arriba llena "
        "el cuadro; la cara puede quedar cortada a la altura del mentón, como un plano "
        "detalle de catálogo (no es un error).",
        "TOP GARMENT CLOSE FRAMING — SHOULDERS-TO-WAIST crop: the top garment fills the "
        "frame; the face may be cropped at the chin, like a catalog detail shot (intended)."),
    "americano": ("PLANO AMERICANO — de la cabeza a mitad del muslo (corta por encima de las "
                  "rodillas), con la cara visible; los pies no entran.",
                  "COWBOY SHOT — from the head to mid-thigh (cut above the knees), face visible; "
                  "the feet are out of frame."),
    "general": ("PLANO GENERAL — ella de cuerpo entero y CHICA en el cuadro (un tercio del alto, "
                "más o menos), con mucho lugar alrededor; la prenda se sigue leyendo.",
                "WIDE SHOT — she is full body and SMALL in the frame (about a third of its "
                "height), with lots of the place around her; the garment still reads."),
    "corto": ("PLANO CORTO — de la cabeza al pecho: cara, hombros y la parte de arriba de la "
              "prenda.",
              "CLOSE SHOT — head to chest: face, shoulders and the top of the garment."),
    "medio": ("PLANO MEDIO — de la cabeza a la cadera, con la cara visible.",
              "MEDIUM SHOT — from the head to the hips, face visible."),
    "entero": ("CUERPO ENTERO — de pies a cabeza, sin recortar ninguna parte del cuerpo.",
               "FULL BODY — head to toe, no part of the body cropped."),
}
_RE_PLANO_ES = re.compile(r"^(CUERPO ENTERO( O AMERICANO)?|PLANO MEDIO(/AMERICANO)?( CORTO)?"
                          r"|PRIMER PLANO( / PLANO MEDIO CORTO)?|PLANO AMERICANO)"
                          r"(,\s*|\s+(?=de |DE ))")
_RE_PLANO_EN = re.compile(r"^(FULL BODY( OR 3/4)?|MEDIUM SHOT)(,\s*|\s+(?=in ))")


# Lo que se escribe en "Encuadre" → el encuadre fijo que le corresponde. Antes ese texto iba
# como un renglón más ("- Encuadre: cuerpo entero") y la rotación automática ponía arriba
# "TAMAÑO DE PLANO: PLANO MEDIO… las piernas NO entran": dos órdenes opuestas en la misma toma.
_ENCUADRE_A_ZONA = (
    ("abajo_cerca", ("cintura a las rodillas", "cintura a la rodilla")),
    ("abajo", ("cintura para abajo", "de la cintura a los pies", "solo la parte de abajo")),
    ("arriba_cerca", ("hombros a la cintura",)),
    ("corto", ("plano corto", "primer plano", "pecho para arriba", "del pecho", "close up",
               "close-up", "retrato")),
    ("americano", ("americano", "rodilla", "mitad del muslo", "medio muslo")),
    ("general", ("plano general", "plano abierto", "gran plano", "bien lejos", "chica en el lugar",
                 "ella chica", "todo el lugar", "todo el ambiente")),
    ("entero", ("cuerpo entero", "pies a cabeza", "pie a cabeza", "cabeza a los pies",
                "cuerpo completo", "entera", "entero", "full body")),
    ("medio", ("plano medio", "cintura para arriba", "cadera para arriba", "medio cuerpo")),
)


_PLANO_EXPLICITO = ("cuerpo entero", "plano medio", "plano americano", "plano general",
                    "plano corto", "primer plano", "pies a cabeza", "pie a cabeza",
                    "cintura para arriba", "cintura para abajo", "pecho para arriba")


def _zona_de_encuadre(txt: str, estricto: bool = False) -> str:
    """estricto: el texto es una POSE; sólo cuentan los nombres de plano explícitos
    ("apoyada sobre una rodilla" no es un plano americano)."""
    t = " " + str(txt or "").lower().strip() + " "
    if len(t.strip()) < 3:
        return ""
    if estricto and not any(k in t for k in _PLANO_EXPLICITO):
        return ""
    for z, kws in _ENCUADRE_A_ZONA:
        if any(k in t for k in kws):
            return z
    return ""


# Prendas de ABAJO solas: ahí el plano medio o corto la dejan fuera del cuadro.
_ABAJO_KW = ("short", "bombacha", "boxer", "bóxer", "slip", "calza", "pantal", "bermuda",
             "culotte", "tanga", "colaless", "vedetina", "malla short", "traje de baño de hombre")
_ARRIBA_KW = ("corpiño", "corpino", "top", "remera", "conjunto", "set ", "bikini", "enteriza",
              "pijama", "piyama", "buzo", "camisa", "body", "vestido", "camisón", "camison",
              "bralette", "musculosa", "sweater", "campera")


def _prenda_de_abajo(p: Dict[str, Any]) -> bool:
    txt = " ".join(str(p.get(k, "")) for k in
                   ("producto_manual", "prenda_desc", "ficha", "tela")).lower()
    return any(k in txt for k in _ABAJO_KW) and not any(k in txt for k in _ARRIBA_KW)


def _zona(p: Dict[str, Any]) -> str:
    z = str(p.get("encuadre_zona", "")).strip().lower()
    return z if z in ENCUADRE_ZONA else ""


def _pose_sin_plano(pose: str, en: bool = False) -> str:
    """Le saca a la pose del pool su tamaño de plano ("CUERPO ENTERO, ") cuando el encuadre
    por zona manda: queda la postura y el gesto."""
    return (_RE_PLANO_EN if en else _RE_PLANO_ES).sub("", pose, count=1)


def _bloque_encuadre_zona(p: Dict[str, Any]) -> str:
    z = _zona(p)
    if not z:
        return ""
    return ("\n\nENCUADRE OBLIGATORIO (máxima prioridad, manda sobre el tamaño de plano de "
            "cualquier pose): " + ENCUADRE_ZONA[z][0] + " La pose indica sólo la postura, la "
            "orientación del cuerpo y el gesto; QUÉ PARTE DEL CUERPO ENTRA EN EL CUADRO la "
            "fija este encuadre y nada más.\n")


def _bloque_pose_unica(idx: int, genero: Optional[str] = None,
                       zona: bool = False, lenc: bool = False,
                       cam: Optional[int] = None, cam_auto: Optional[int] = None,
                       lugar: bool = False, segura: bool = False,
                       auto: bool = True, ext: bool = False) -> str:
    pool = _pose_pool(genero)
    pose = pool[idx % len(pool)]
    _expr_txt = _expr(pose)
    if not auto:
        lugar = False
    # Reintento después de un bloqueo: la misma toma con su versión de catálogo.
    if segura and not _es_hombre(genero) and (idx % len(pool)) in POSE_SEGURA_ES:
        pose = POSE_SEGURA_ES[idx % len(pool)]
        _expr_txt = _EXPR_SEGURA
    if (idx % len(pool)) in (3, 10) and not zona and cam is None:
        # TOMA DE ESPALDA: encuadre FIJO. La rotación de planos le daba "plano general,
        # cámara lejos, en el punto más lejano del lugar" y la espalda de la prenda quedaba
        # chiquita: el motor la inventaba.
        _plano_esp = ("" if (idx % len(pool)) == 10 else
                      "\nTAMAÑO DE PLANO (toma de espalda): plano americano, de la cabeza a mitad "
                      "del muslo; la ESPALDA ocupa buena parte del cuadro para que se lean todas "
                      "las tiras, cierres y terminaciones de atrás.")
        return (f"\nPOSE DE ESTA TOMA (obligatoria, máxima prioridad): {_pose_sin_plano(pose)}. "
                f"{_expr_txt} Respetá esa orientación del cuerpo y ese gesto.{_plano_esp}"
                "\nCÁMARA: a la altura del pecho, a unos 2 metros detrás de ella. "
                "Sin espejos ni reflejos en la toma (mostrarían el frente).")
    _ic = _cam_i(cam, cam_auto, idx)          # qué cámara le toca
    _ip = _cam_i(None, cam_auto, idx)         # la POSICIÓN de la toma en el set
    # El rincón NO se ata al ángulo elegido: sigue la posición de la toma, así una misma
    # cámara puede caer en partes distintas del lugar.
    cam_txt = ((_camara(_ic, lenc, elegida=cam is not None)
                if (auto or cam is not None) else "")
               + ("\n" + _rincon(_ip, lugar, ext=ext, pose=pose, cam=_ic) if lugar else ""))
    if zona:
        return (
            f"\nPOSE DE ESTA TOMA (obligatoria): {_pose_sin_plano(pose)}. {_expr_txt} "
            "Respetá esa orientación del cuerpo y ese gesto; el TAMAÑO DE PLANO lo fija el "
            "ENCUADRE OBLIGATORIO de arriba, no la pose. Pose espontánea y desprevenida "
            f"estilo Instagram, con vida, no acartonada.\n{cam_txt}"
        )
    if auto and _plano_aparte(idx, _ic, zona, lenc, cam is not None):
        # El tamaño de plano va en su propio renglón y se le saca el suyo a la pose: con
        # los dos puestos, la cámara y el rincón le ganaban al de la pose y todo salía
        # cuerpo entero.
        return (
            f"\nPOSE DE ESTA TOMA (obligatoria, máxima prioridad): {_pose_sin_plano(pose)}. "
            f"{_expr_txt} Respetá esa orientación del cuerpo y ese gesto; CUÁNTO CUERPO entra "
            "en el cuadro lo fija el TAMAÑO DE PLANO de acá abajo. Pose espontánea y "
            f"desprevenida estilo Instagram, con vida, no acartonada."
            f"\n{_plano(_ip, lenceria=lenc)}\n{cam_txt}"
        )
    if _cam_fija_plano(_ic, lenc, cam is not None):
        # La cámara de esta toma (muy cerca / bien lejos) ya decide cuánto entra.
        return (
            f"\nPOSE DE ESTA TOMA (obligatoria, máxima prioridad): {_pose_sin_plano(pose)}. "
            f"{_expr_txt} Respetá esa orientación del cuerpo y ese gesto; CUÁNTO CUERPO entra "
            "lo fija la CÁMARA de acá abajo. Pose espontánea y desprevenida estilo Instagram, "
            f"con vida, no acartonada.\n{cam_txt}"
        )
    return (
        f"\nPOSE Y ENCUADRE DE ESTA TOMA (obligatorio, máxima prioridad): {pose}. {_expr_txt} "
        "Respetá exactamente esa orientación del cuerpo y ese tamaño de plano. "
        f"Pose espontánea y desprevenida estilo Instagram, con vida, no acartonada.\n{cam_txt}"
    )


# Va pegado a la descripción del producto: ahí el modelo ya sabe QUÉ foto mirar.
_SOLO_LO_QUE_SE_VE = (
    "La prenda lleva SOLO los detalles que se ven en esa(s) foto(s): si un detalle no está en "
    "la foto, no existe en la prenda. Si el pantalón se ve liso y sin bolsillos, va liso y sin "
    "bolsillos" + "{_MANOS}" + "."
)


def _bloque_producto_ref(n_prod: int, primera_idx: int, con_persona: bool = True) -> str:
    """Describe las imágenes de referencia del producto (pueden ser varias vistas).
    con_persona=False (producto solo): nada de "las manos de la modelo"."""
    _sl = _SOLO_LO_QUE_SE_VE.replace("{_MANOS}", ", y las manos de la modelo quedan fuera de la "
                                     "prenda (no metidas en ningún lado)" if con_persona else "")
    if n_prod <= 1:
        return (
            f"IMAGEN {primera_idx}: es EL PRODUCTO REAL de LUMA Íntima y es la FUENTE PRINCIPAL "
            "y la VERDAD ABSOLUTA de la prenda. Copiá la prenda EXACTAMENTE como se ve en esa "
            "foto: mismo diseño, mismo escote y forma, color real, largo, estampa, detalles y "
            "terminaciones. Mirá bien la foto y reproducí ESA prenda puntual, no una parecida ni "
            "una versión genérica. Cualquier texto o ficha es SECUNDARIO y NO puede contradecir "
            "lo que muestra esta foto. NO inventes ni modifiques nada.\n"
            + _sl
        )
    ult = primera_idx + n_prod - 1
    return (
        f"IMÁGENES {primera_idx} a {ult}: son VARIAS VISTAS del MISMO producto real de "
        "LUMA Íntima (por ejemplo: la parte de arriba, el pantalón, y/o un detalle de la "
        "tela). Juntas son la VERDAD ABSOLUTA de la prenda COMPLETA. Combiná todas las "
        "vistas para reproducir el conjunto entero EXACTAMENTE: mismo diseño, color real, "
        "largo, estampa, detalles y terminaciones en cada parte. NO inventes ninguna parte "
        "que no esté en las fotos ni modifiques nada.\n"
        + _sl
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
    "HERRAJES Y REGULADORES (argollas, aros, hebillas, ganchos, sliders): van SOLO donde se "
    "ven en las fotos reales y del mismo color. Si en las fotos los reguladores están "
    "ÚNICAMENTE en la ESPALDA, entonces en la vista de FRENTE los breteles van LISOS, sin "
    "ningún herraje. PROHIBIDO agregar argollas, aros o hebillas en el frente si la prenda "
    "real no los tiene ahí.\n"
    "ESCOTE: la forma y la profundidad del escote son EXACTAS a las de la foto real (mismo "
    "tipo de V o corte, misma apertura, mismo ancho entre breteles). No lo abras, no lo "
    "cierres ni le cambies la forma.\n"
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
    "lacio": "pelo lacio, suelto y prolijo",
    "ondas_playa": "ondas sueltas tipo playa, naturales y apenas despeinadas",
    "brushing": "brushing con volumen y puntas hacia afuera",
    "semirecogido": "semirecogido (media cola) con mechones sueltos al frente",
    "recogido_despeinado": "recogido despeinado, con mechones sueltos que enmarcan la cara",
    "cola_alta": "cola alta y tirante",
    "cola_baja": "cola baja, suelta y elegante",
    "rodete_bajo": "rodete bajo y prolijo en la nuca",
    "rodete_alto": "rodete alto despeinado (top knot)",
    "trenzas": "dos trenzas",
    "mojado": "pelo mojado peinado hacia atrás (efecto wet)",
    "flequillo": "suelto con flequillo cortina abierto al medio",
    "panuelo": "pelo atado con un pañuelo de seda",
}
TIPO_PEINADO_EN = {
    "largo_suelto": "long loose hair", "largo_ondulado": "long, loose, wavy hair",
    "corto": "short hair", "media_melena": "shoulder-length hair", "atado": "hair in a ponytail",
    "rodete": "hair in a bun", "trenza": "hair in a single braid", "rapado": "buzz-cut hair",
    "lacio": "sleek straight loose hair", "ondas_playa": "loose, natural, slightly messy beach waves",
    "brushing": "voluminous blow-dry with flipped-out ends",
    "semirecogido": "half-up half-down hair with loose strands in front",
    "recogido_despeinado": "messy updo with loose face-framing strands",
    "cola_alta": "sleek high ponytail", "cola_baja": "elegant loose low ponytail",
    "rodete_bajo": "neat low bun at the nape", "rodete_alto": "messy high top knot",
    "trenzas": "two braids", "mojado": "wet-look hair slicked back",
    "flequillo": "loose hair with curtain bangs parted in the middle",
    "panuelo": "hair tied back with a silk scarf",
}


def _peinado(p: Dict[str, Any], en: bool = False) -> str:
    """El peinado pedido: el de la lista más lo que haya escrito (peinado_libre)."""
    k = str(p.get("cuerpo_peinado", "")).strip().lower()
    base = (TIPO_PEINADO_EN if en else TIPO_PEINADO).get(k, "")
    libre = str(p.get("peinado_libre", "")).strip()[:200]
    return "; ".join(x for x in (base, libre) if x)

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


def _cuerpo_lista(p: Dict[str, Any], genero: Optional[str] = None) -> str:
    """Solo la lista de rasgos del cuerpo (sin encabezado), para el bloque del TOPE."""
    if _es_hombre(genero):
        pares = (("cuerpo_contextura", TIPO_CONTEXTURA_H), ("cuerpo_abdomen", TIPO_ABDOMEN_H),
                 ("cuerpo_altura", TIPO_ALTURA))
    else:
        pares = (("cuerpo_contextura", TIPO_CONTEXTURA), ("cuerpo_busto", TIPO_BUSTO),
                 ("cuerpo_cola", TIPO_COLA), ("cuerpo_abdomen", TIPO_ABDOMEN),
                 ("cuerpo_altura", TIPO_ALTURA))
    out = []
    for key, mapa in pares:
        v = str(p.get(key, "")).strip().lower()
        if v and v in mapa:
            out.append(mapa[v])
    return ", ".join(out)


# ── LA ONDA DE LA MODELO (época / estética) ──
# La ficha ya tenía peinado y accesorios sueltos, pero para hacer una producción "de los
# 80" había que escribir a mano el pelo, el maquillaje, las uñas y los aros, y aun así
# salía a medias. Cada estilo es un paquete coherente. Cambia SÓLO cómo está ARREGLADA
# la modelo: la prenda es la real del producto y no se toca, y la cara sigue siendo la
# misma persona del avatar.
ESTILOS_AVATAR = {
    # Un look FIJO por estilo. La primera versión ofrecía alternativas ("pelo batido… o una
    # cola alta con scrunchie… o…") y el modelo elegía una distinta en cada toma: la modelo
    # cambiaba de peinado foto a foto dentro del mismo set.
    "": {"lbl": "Natural (como viene el avatar)", "ayuda": "Sin época: el pelo y el maquillaje quedan como en la ficha.",
         "es": "", "en": ""},
    "80": {
        "lbl": "Años 80",
        "ayuda": "Pelo batido con mucho volumen, sombras de colores fuertes, aros grandes.",
        "es": "PELO: suelto, batido con MUCHO volumen y cuerpo, ondas marcadas, la raíz "
              "levantada y el flequillo parado con laca. MAQUILLAJE de los 80: sombra celeste "
              "difuminada hasta la ceja, rubor marcado en diagonal hacia la sien, labios "
              "fucsia bien cubiertos, cejas gruesas y naturales. ACCESORIOS: aros grandes de "
              "argolla dorados y varias pulseras finas juntas. UÑAS largas fucsia.",
        "en": "1980s HAIR AND MAKEUP: big teased volumized hair worn loose, strong waves, "
              "lifted roots, hairsprayed bangs. Light-blue eyeshadow blended up to the brow, "
              "strong diagonal blush toward the temple, bold fuchsia lips, thick natural "
              "brows. Large gold hoop earrings, stacked thin bracelets. Long fuchsia nails."},
    "90": {
        "lbl": "Años 90",
        "ayuda": "Pelo lacio con raya al medio, labios marrones, choker fino.",
        "es": "PELO: lacio con RAYA AL MEDIO y caída natural, dos mechones finos sueltos "
              "adelante. MAQUILLAJE de los 90: labios marrón con el contorno delineado más "
              "oscuro, cejas finas, sombra tierra suave, piel mate sin brillo. ACCESORIOS: "
              "choker fino negro y aros chicos. UÑAS cortas marrones.",
        "en": "1990s HAIR AND MAKEUP: straight hair with a CENTER PART, two thin loose front "
              "strands. Brown lips with a darker lip liner, thin brows, soft earthy "
              "eyeshadow, matte skin. Thin black choker, small earrings. Short brown nails."},
    "y2k": {
        "lbl": "Y2K / años 2000",
        "ayuda": "Mechas claras, gloss, lentes chiquitos, todo con brillo.",
        "es": "PELO: lacio y brillante con MECHAS CLARAS alrededor de la cara y raya al "
              "costado. MAQUILLAJE Y2K: gloss transparente muy brillante, sombras con "
              "GLITTER, delineado finito, iluminador fuerte en los pómulos, cejas finas. "
              "ACCESORIOS: lentes de sol chiquitos y ovalados, argollas, collar de cuentas "
              "de colores. UÑAS con brillo.",
        "en": "Y2K / early-2000s HAIR AND MAKEUP: shiny straight hair with lighter "
              "face-framing highlights and a side part. Very glossy lips, GLITTER "
              "eyeshadow, thin eyeliner, strong highlighter, thin brows. Tiny oval "
              "sunglasses, hoops, a colourful beaded necklace. Glossy nails."},
    "70": {
        "lbl": "Años 70",
        "ayuda": "Ondas suaves, tonos tierra y dorados, argollas grandes.",
        "es": "PELO: largo con ONDAS SUAVES y raya al medio. MAQUILLAJE de los 70: párpados "
              "en tonos TIERRA y dorado difuminados hasta la cuenca, labios terracota "
              "brillantes, piel bronceada y luminosa, cejas finas y arqueadas. ACCESORIOS: "
              "aros de argolla grandes, vincha finita sobre la frente, anillos con piedras. "
              "UÑAS ovaladas en tonos tierra.",
        "en": "1970s HAIR AND MAKEUP: long soft waves with a center part. EARTH-TONE and "
              "gold blended eyeshadow, glossy terracotta lips, sun-tanned glowing skin, thin "
              "arched brows. Big hoop earrings, a thin headband across the forehead, stone "
              "rings. Oval earth-tone nails."},
    "50": {
        "lbl": "Años 50 / pin-up",
        "ayuda": "Rulos marcados, delineado con alita, labios rojos mate.",
        "es": "PELO: RULOS GRANDES Y MARCADOS con rolls levantados adelante y un pañuelo "
              "atado con moño arriba de la cabeza. MAQUILLAJE de los 50: DELINEADO NEGRO con "
              "la alita bien marcada, labios ROJO INTENSO mate, cejas arqueadas y definidas, "
              "piel pareja con rubor rosado. ACCESORIOS: pañuelo al pelo y aros de perla. "
              "UÑAS ovaladas rojas.",
        "en": "1950s pin-up HAIR AND MAKEUP: big set curls with victory rolls and a scarf "
              "tied in a bow on top of the head. Bold BLACK WINGED eyeliner, matte true-RED "
              "lips, defined arched brows, even skin with rosy cheeks. Hair scarf, pearl "
              "earrings. Oval red nails."},
    "rock": {
        "lbl": "Rockera",
        "ayuda": "Pelo despeinado con textura, ojos ahumados, anillos de plata.",
        "es": "PELO: despeinado con TEXTURA, raíz con volumen, ondas rotas y mechones en la "
              "cara. MAQUILLAJE: delineado negro DIFUMINADO (ojos ahumados), pestañas "
              "cargadas, labios nude mate, piel mate. ACCESORIOS: aros de argolla negros, "
              "VARIOS anillos de plata, choker de cuero. UÑAS cortas negras.",
        "en": "ROCK HAIR AND MAKEUP: messy textured hair with volume at the roots, broken "
              "waves, strands on the face. Smudged black SMOKEY eyes, heavy lashes, matte "
              "nude lips, matte skin. Black hoop earrings, SEVERAL silver rings, a leather "
              "choker. Short black nails."},
    "glam": {
        "lbl": "Glam de noche",
        "ayuda": "Ondas grandes con brillo, ojos ahumados, aros que brillan.",
        "es": "PELO: ONDAS GRANDES y brillantes, bien peinadas hacia un lado. MAQUILLAJE: "
              "ojos ahumados prolijos, pestañas largas, iluminador marcado en pómulos y "
              "nariz, labios nude satinados, cejas peinadas y definidas. ACCESORIOS: aros "
              "largos que brillan y anillos finos. UÑAS largas prolijas.",
        "en": "EVENING GLAM HAIR AND MAKEUP: big glossy well-styled waves swept to one side. "
              "Polished smokey eyes, long lashes, strong highlighter on cheekbones and nose, "
              "satin nude lips, groomed defined brows. Long sparkling earrings, thin rings. "
              "Long neat nails."},
    "minimal": {
        "lbl": "Minimal / actual",
        "ayuda": "Pelo prolijo, cara lavada, aritos chicos. Lo de ahora.",
        "es": "PELO: prolijo y natural, lacio con raya al medio. MAQUILLAJE mínimo: piel "
              "natural con brillo propio, cejas peinadas, labios nude, apenas máscara de "
              "pestañas. ACCESORIOS: aritos chicos dorados y una cadenita fina al cuello. "
              "UÑAS cortas naturales.",
        "en": "MINIMAL CURRENT HAIR AND MAKEUP: neat natural straight hair with a center "
              "part. Bare natural skin, groomed brows, nude lips, a touch of mascara. Small "
              "gold studs, one thin chain. Short natural nails."},
    "deportiva": {
        "lbl": "Deportiva",
        "ayuda": "Pelo atado, cara lavada, vincha. Para ropa de entrenar.",
        "es": "PELO: ATADO en una cola alta con dos mechones sueltos en la cara y una vincha "
              "fina. MAQUILLAJE: cara lavada, piel fresca con brillo natural de la actividad, "
              "cejas peinadas, sin labial. ACCESORIOS: vincha, reloj deportivo y aritos "
              "chicos. UÑAS cortas al natural.",
        "en": "SPORTY HAIR AND MAKEUP: hair TIED UP in a high ponytail with two loose face "
              "strands and a thin headband. Bare face, fresh naturally dewy skin, groomed "
              "brows, no lipstick. Headband, sports watch, small studs. Short bare nails."},
}
_ESTILO_AV_REGLA_ES = (
    " Este estilo cambia SÓLO cómo está ARREGLADA la modelo: el pelo, el maquillaje, las "
    "uñas y los accesorios. LA PRENDA NO SE TOCA: es la del producto real, tal cual sus "
    "fotos, y está PROHIBIDO agregarle ropa de época encima (nada de camperas, calentadores, "
    "chalecos ni prendas que no estén en las fotos del producto). La CARA y los rasgos "
    "siguen siendo los de ESA misma persona: cambia el arreglo, no la identidad. Si en la "
    "ficha se eligió un peinado o se escribieron accesorios, ESOS mandan sobre los del "
    "estilo. Y es UN SOLO look para toda la sesión: el MISMO peinado, el mismo maquillaje "
    "y los mismos accesorios en TODAS las tomas del set, idénticos a los de las tomas "
    "previas — no cambies de peinado de una foto a otra. Es una LISTA CERRADA: cada ítem "
    "(el peinado, CADA elemento del maquillaje —sombra, rubor, labios, cejas—, cada "
    "accesorio y las uñas) aparece en TODAS las tomas, con la MISMA intensidad y el mismo "
    "color; ninguno es opcional, ninguno se saca en una toma ni se agrega en otra. Si una "
    "toma tiene rubor, todas tienen ese rubor; si tiene argollas, todas tienen esas "
    "argollas. La modelo NO puede estar arreglada distinto en cada foto.")
_ESTILO_AV_REGLA_EN = (
    " This styling changes ONLY how she is GROOMED: hair, makeup, nails and accessories. "
    "THE GARMENT IS UNTOUCHED: it is the real product exactly as its photos show, and adding "
    "any period clothing over it is FORBIDDEN (no jackets, legwarmers or vests that are not "
    "in the product photos). Her FACE and features stay the SAME person: the styling "
    "changes, not the identity. ONE look for the whole session, identical to the previous "
    "shots: every item above (hair, each makeup element, each accessory, nails) is in "
    "EVERY shot, same intensity and color; nothing dropped or added between shots.")


def _hay_estilo_avatar(p: Dict[str, Any]) -> bool:
    e = ESTILOS_AVATAR.get(str(p.get("estilo_avatar", "")).strip().lower())
    return bool(e and e.get("es"))


def _estilo_avatar(p: Dict[str, Any], en: bool = False) -> str:
    """El paquete de pelo, maquillaje, uñas y accesorios de la época elegida."""
    k = str(p.get("estilo_avatar", "")).strip().lower()
    e = ESTILOS_AVATAR.get(k)
    if not e or not e.get("en" if en else "es"):
        return ""
    return (("STYLING: " + e["en"] + _ESTILO_AV_REGLA_EN) if en
            else ("\n\nONDA DE LA MODELO (época elegida por la usuaria): " + e["es"]
                  + _ESTILO_AV_REGLA_ES))


# Con una época elegida, el pedido tenía DOS órdenes sobre el pelo que se contradecían:
# "copiá EXACTO el pelo del avatar / de las tomas previas" y "peinala como en los 80".
# Gemini resolvía la pelea al azar, toma por toma: en un set de 4 salían 2 con la onda
# y 2 con el pelo natural del avatar. Estos textos dejan una sola lectura posible:
# de la foto del avatar (y de las tomas previas) sale la PERSONA; cómo está ARREGLADA
# la fija la ONDA DE LA MODELO.
_ESTAV_IDENT_ES = (
    " OJO CON EL PELO Y EL ARREGLO: de esta foto sacá la cara, el color natural del pelo, "
    "el tono de piel y el físico. NO copies de la foto cómo está peinada, maquillada ni "
    "qué accesorios lleva: el PEINADO, el MAQUILLAJE, las UÑAS y los ACCESORIOS los "
    "define el bloque ONDA DE LA MODELO de arriba, y en esta toma va arreglada EXACTAMENTE "
    "así aunque en la foto de referencia esté al natural.")
_ESTAV_CONS_ES = (
    "\n• ONDA DE LA MODELO: las tomas previas ya la muestran arreglada según la época "
    "elegida (peinado, maquillaje, uñas y accesorios). En esta toma va arreglada "
    "EXACTAMENTE igual que en esas tomas previas — mismo peinado, mismo maquillaje, mismos "
    "accesorios. Compará ÍTEM POR ÍTEM con la toma previa: si ahí tiene rubor, acá tiene el "
    "mismo rubor; si tiene sombra celeste, la misma sombra; si tiene argollas y pulseras, "
    "las mismas argollas y pulseras. Nada de más ni de menos. Si alguna toma previa saliera "
    "al natural o con algún ítem faltante, NO la imites en eso: manda el bloque ONDA DE LA "
    "MODELO completo.")


def _rotulo_avatar(p: Dict[str, Any], idx: int) -> str:
    """Rótulo que viaja pegado a la foto del avatar."""
    if _hay_estilo_avatar(p):
        return (f"IMAGEN {idx} — AVATAR (solo identidad: cara, color de pelo, piel y físico. "
                "Su PEINADO, maquillaje y accesorios de esta foto NO se copian: van como dice "
                "la ONDA DE LA MODELO del texto. Su ropa, pose y fondo NO existen en esta "
                "toma):")
    if _peinado(p):
        return (f"IMAGEN {idx} — AVATAR (solo identidad: cara, color de pelo y físico. Su "
                "PEINADO de esta foto NO se copia: va como dice PEINADO OBLIGATORIO del texto. "
                "Su ropa, pose y fondo NO existen en esta toma):")
    return (f"IMAGEN {idx} — AVATAR (solo identidad: cara, pelo y físico; su ropa, pose y "
            "fondo NO existen en esta toma):")


def _rotulo_toma_previa(p: Dict[str, Any], idx: int) -> str:
    """Rótulo que viaja pegado a cada toma previa del set (ancla)."""
    if p.get("_anclas_cara"):
        return (f"IMAGEN {idx} — CARA DE LA MISMA PERSONA en una toma previa del set (recorte: "
                "sólo su cara, pelo y maquillaje; el cuerpo, la pose, el encuadre, la cámara y el "
                "lugar NO salen de acá):")
    if _hay_estilo_avatar(p):
        return (f"IMAGEN {idx} — TOMA PREVIA DEL SET (solo para mantener a la MISMA persona "
                "con el MISMO peinado, maquillaje y accesorios de la ONDA DE LA MODELO; NO es "
                "referencia de prenda, pose ni encuadre — la prenda sale de las FOTOS REALES "
                "DEL PRODUCTO):")
    return (f"IMAGEN {idx} — TOMA PREVIA DEL SET (solo para mantener a la MISMA persona; NO "
            "es referencia de prenda, pose ni encuadre — la prenda sale de las FOTOS REALES "
            "DEL PRODUCTO):")


def _bloque_cuerpo_top(p: Dict[str, Any], genero: Optional[str] = None) -> str:
    """Va al TOPE del prompt. El cuerpo enterrado en el medio perdía contra la foto del
    avatar: la PRIMERA toma del set salía flaca aunque se pidiera talle grande (recién
    se acercaba al talle pedido en las tomas siguientes, gracias a las anclas)."""
    lista = _cuerpo_lista(p, genero)
    if not lista:
        return ""
    gw = _gwords(genero)
    return (
        f"⚠ CÓMO ES {gw['modelo_cap'].upper()} DE ESTA CAMPAÑA (definilo ANTES que nada, "
        f"desde el primer trazo): es {gw['persona']} de {lista}.\n"
        f"Dibujá directamente ESE cuerpo: NO arranques de una modelo estándar delgada. La "
        f"foto de referencia aporta ÚNICAMENTE la CARA — el cuerpo es el que se describe "
        f"acá. Si esta es la primera toma de la sesión, este cuerpo queda definido para "
        f"todas las siguientes.\n\n"
    )


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
    pein = _peinado(p)
    if pein:
        partes.append("PEINADO OBLIGATORIO: " + pein + " (con SU color de pelo natural; NO "
                      "copies cómo está peinada en la foto de referencia)")
    # Los accesorios van en su propio renglón: "TIPO DE CUERPO…: accesorios: joyas de oro"
    # los mezclaba con el cuerpo.
    accs = str(p.get("cuerpo_accesorios", "")).strip()
    _acc_txt = ("\n\nACCESORIOS (sólo estos, ninguno más): " + accs + ".") if accs else ""
    if not partes:
        return _acc_txt
    if p.get("_cuerpo_prioritario"):
        encabezado += (" (así ES el cuerpo de la modelo en esta sesión — usá este bloque "
                       "como única definición del cuerpo)")
    return (
        "\n\n" + encabezado + " (respetá estas proporciones reales y naturales, sin "
        "exagerar ni deformar): " + ", ".join(partes) + ". Proporciones humanas creíbles y "
        "anatómicamente correctas; la prenda calza bien sobre ESE tipo de cuerpo."
        + _acc_txt
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
    "hiperrealista en EXTERIOR con luz natural, con los reflejos cálidos del sol que unifican a "
    "la modelo con el paisaje (la hora y la luz exactas son las de la escena). Tonos de piel reales y balanceados, color equilibrado, textura "
    "nítida de la tela, calidad editorial de moda. Es una producción de catálogo PROFESIONAL, "
    "elegante y de buen gusto: la prenda se ve con su calce real, favorecedor y prolijo, "
    "resaltando las curvas de manera natural; actitud segura y relajada, estilo campaña de "
    "marca para una tienda online. La cámara, el lente y los ajustes los fija el bloque "
    "CÁMARA Y ÓPTICA."
)


FONDO_NITIDO = (
    "ENFOQUE DEL FONDO: el fondo va NÍTIDO y detallado (gran profundidad de campo, tipo f/11–"
    "f/16, todo en foco). Se ve con claridad la TEXTURA de todo lo que tiene el lugar (la arena, "
    "el agua, la vegetación, las paredes, el paisaje — lo que esa escena tenga). NO desenfoques ni difumines el fondo: ambiente "
    "y modelo, todo nítido."
)

VIENTO_BLOCK = (
    "SENSACIÓN VENTOSA: es un día con viento. El pelo y la tela se mueven con la brisa, mechones "
    "al viento, leve movimiento en la ropa; si hay agua, con olas y espuma; ambiente fresco, dinámico "
    "y natural."
)


# Vocabulario de las etiquetas que la usuaria le pone a cada foto del producto. La clave
# es lo que manda la pantalla; el valor, la frase que entiende el motor y el inspector.
_ETIQUETAS_PRENDA = {
    "frente": "VISTA DE FRENTE de la prenda",
    "espalda": "VISTA DE ESPALDA (parte de atrás) de la prenda",
    "perfil": "VISTA DE COSTADO/PERFIL de la prenda (se ve en escorzo: es perspectiva, "
              "no un diseño distinto)",
    "detalle": "DETALLE de cerca de la tela o de una terminación",
    "colgada": "la prenda COLGADA o PLANA, sin cuerpo",
    "arriba": "la PARTE DE ARRIBA del conjunto (top/remera/corpiño)",
    "abajo": "la PARTE DE ABAJO del conjunto (pantalón/short/bombacha)",
    "complemento": "una PIEZA COMPLEMENTARIA del conjunto (bata, kimono, accesorio)",
    "color": ("SOLO REFERENCIA DE COLOR de esta toma: de esta foto sale ÚNICAMENTE el color y "
              "el tono exacto. El DISEÑO (cantidad de tiras, costuras, moños, argollas, encaje, "
              "frente, dorso y costados) sale de las DEMÁS fotos del producto, no de esta"),
}


def _etiqueta_txt(tag: str) -> str:
    """Devuelve la etiqueta lista para el prompt, o vacío si no se especificó."""
    t = str(tag or "").strip()
    return _ETIQUETAS_PRENDA.get(t.lower(), t)


def _es_tag_espalda(tag: str) -> bool:
    t = str(tag or "").strip().lower()
    return t == "espalda" or "espalda" in t or "atrás" in t or "atras" in t


FRENTE_ESPALDA_DISTINTOS = (
    "\n\nEL FRENTE Y LA ESPALDA SON DISTINTOS (crítico, mirá las dos fotos): esta prenda "
    "NO tiene el mismo diseño de los dos lados. El frente es el de las fotos de frente y "
    "la espalda es el de la foto de espalda — pueden ser telas y estampas completamente "
    "diferentes (por ejemplo: frente liso con un dibujo grande y espalda entera a "
    "cuadros).\n"
    "DÓNDE CAMBIA: el diseño cambia justo en las COSTURAS — la de los hombros y las de "
    "los costados (debajo de las axilas). De esa línea para adelante va el diseño del "
    "frente; de esa línea para atrás, el de la espalda. Las mangas siguen lo que muestran "
    "las fotos.\n"
    "AUNQUE LA TOMA SEA DE FRENTE: todo pedacito de la espalda que se llegue a ver — por "
    "encima de los hombros, al costado del torso, debajo de los brazos, o si la modelo "
    "está girada — tiene que mostrar el diseño de la ESPALDA, no el del frente. "
    "PROHIBIDO que la espalda salga lisa o repitiendo el frente cuando la foto de espalda "
    "muestra otra cosa."
)


ESPALDA_GUARD = (
    "\nORIENTACIÓN DE ESTA TOMA: la modelo está DE ESPALDAS a la cámara y se ve la PARTE DE ATRÁS "
    "de la prenda. Si hay tomas previas de referencia (de FRENTE), seguí copiando de ellas la "
    "prenda EXACTA (misma estampa, mismos colores, misma tela) y la misma modelo, pero girá el "
    "cuerpo: en ESTA imagen se ve la espalda, no el frente. La parte de atrás debe ser la de la "
    "prenda real de las fotos."
)

ESPALDA_VERANO_SOFT = (
    "\nTOMA DE ESPALDA (estilo catálogo de moda): NO un primer plano de la cola; actitud natural "
    "y de buen gusto, campaña de marca de trajes de baño. La pose y el tamaño de plano son los "
    "de ESTA TOMA."
)


def _modelo_spec(item: Dict[str, str], letra: str, img_idx: Optional[int],
                 genero: Optional[str] = None, prod_idx: Optional[int] = None) -> str:
    """Describe una modelo del set: cara (avatar o etnia IA) + todas sus características.
    `prod_idx` = número de IMAGEN con SU prenda, cuando cada pieza del set es una prenda
    distinta (no el mismo modelo en otro color)."""
    gw = _gwords(genero)
    partes = []
    if img_idx is not None:
        partes.append(f"cara/identidad = la de la IMAGEN {img_idx} (respetá sus rasgos exactos)")
    else:
        et = APAR_ETNIA.get(str(item.get('etnia', '')).lower())
        pe = APAR_PELO.get(str(item.get('pelo', '')).lower())
        if et:
            partes.append(f"{gw['genero_n']} {et}")
        if pe:
            partes.append(pe)
    for key, mapa in (("contextura", TIPO_CONTEXTURA), ("edad", TIPO_EDAD_CORP),
                      ("busto", TIPO_BUSTO), ("cola", TIPO_COLA),
                      ("abdomen", TIPO_ABDOMEN), ("altura", TIPO_ALTURA)):
        v = mapa.get(str(item.get(key, '')).lower())
        if v:
            partes.append(v)
    _pn = _peinado({"cuerpo_peinado": item.get("peinado", ""),
                    "peinado_libre": item.get("peinado_libre", "")})
    if _pn:
        partes.append("peinado: " + _pn)
    desc = "; ".join(partes) if partes else gw["persona"]
    col = str(item.get("color", "")).strip()
    if prod_idx is not None:
        # Cada pieza es OTRA prenda (otra estampa), no el mismo modelo en otro color.
        return (f"MODELO {letra} ({desc}) lleva la prenda de la IMAGEN {prod_idx}"
                + (f" (color {col})" if col else "")
                + " — esa imagen es la ÚNICA verdad de SU prenda")
    return f"MODELO {letra} ({desc}) lleva la prenda en color {col}"


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

# La misma idea para modelos varones. Sin esto, un set de bóxers salía con una
# composición escrita en femenino ("abrazadas", "riéndose entre ellas") y el
# motor terminaba dibujando mujeres aunque el resto del prompt pidiera varones.
_COMPOSICIONES_GRUPAL_H = [
    ("Composición: parados uno al lado del otro pero a distintas profundidades (uno apenas "
     "adelante), relajados, riéndose entre ellos; solo el del medio mira a cámara."),
    ("Composición: caminando juntos hacia la cámara como saliendo de una sesión, en "
     "movimiento natural, uno acomodándose el pelo, conversando entre risas."),
    ("Composición: el del medio de frente mirando a cámara; los de los costados girados en "
     "3/4 hacia él, con una mano en su hombro, sonrientes."),
    ("Composición: en ronda abierta como charlando, cuerpos levemente girados entre sí, uno "
     "gesticulando con las manos, risas genuinas; ninguno posa para la cámara."),
    ("Composición: apoyados contra la pared en distintas posturas relajadas (uno de frente, "
     "uno de 3/4, uno casi de perfil mirando a los otros), estilo backstage de campaña."),
    ("Composición: uno sentado en un banco alto y los otros parados a sus costados "
     "inclinados hacia él, a distinta altura, charla distendida."),
]

# Cuántas piezas puede tener un set. Era fijo en 3 (venía de "set de colores de a
# tres"), pero un pack se vende de a 2, de a 5 o de a 6 igual de seguido.
SET_MAX_MODELOS = 6
_LETRAS = "ABCDEF"


def _n_txt(n: int) -> str:
    """El número escrito, que es como se lo entiende un modelo de imagen: 'TRES
    modelos' sale mucho más parejo que '3 modelos'."""
    return {1: "UNA", 2: "DOS", 3: "TRES", 4: "CUATRO",
            5: "CINCO", 6: "SEIS"}.get(int(n), str(n))


def _outfit_grupal(p: Dict[str, Any], asign: List[Dict[str, Any]], en: bool = False) -> str:
    """QUÉ MÁS LLEVA CADA MODELO en la foto grupal (buzo, zapatillas…). Sin esto el prompt
    grupal sólo decía "la misma prenda en su color" y la IA les inventaba un top al tono."""
    a = list(asign or [])
    lineas = [f"{_LETRAS[k]} ({str(it.get('color', '')).strip()}): {str(it.get('outfit', '')).strip()}"
              for k, it in enumerate(a) if str(it.get("outfit", "")).strip()]
    gen = str(p.get("piezas", "")).strip()
    if not lineas and not gen:
        return ""
    if en:
        return ("WHAT EACH ONE WEARS besides the product garment (mandatory; the product is ONLY the "
                "garment of the product photos, no matching top or set is invented): "
                + ("; ".join(lineas) if lineas else gen) + ".")
    return ("QUÉ MÁS LLEVA CADA UNA además de la prenda del producto (obligatorio; el producto es "
            "SÓLO la prenda de las fotos, NO inventes un top ni un conjunto al tono): "
            + ("; ".join(f"modelo {x}" for x in lineas) if lineas else gen) + ".")


def _grupal(t: str, es_h: bool = False) -> str:
    """Un bloque escrito para UNA persona, dicho para varias."""
    cada = "cada uno" if es_h else "cada una"
    for a, b in (("La persona", "Cada persona"), ("la persona", "cada persona"),
                 ("ella PROYECTA SU SOMBRA", cada + " PROYECTA SU SOMBRA"),
                 ("Si está de pie", "Quien está de pie"), ("si está sentada", "quien está sentada"),
                 ("no puede quedar inclinada", "nadie puede quedar inclinado")):
        t = t.replace(a, b)
    return t


def build_prompt_trio(p: Dict[str, Any], settings: Dict[str, Any], asign: List[Dict[str, str]],
                      aspect: str, style: str = "", n_prod: int = 1,
                      img_map: Optional[List[Optional[int]]] = None, prod_primera: int = 1,
                      seguro: bool = False, full_refs: bool = False,
                      genero: Optional[str] = None,
                      prod_map: Optional[List[Optional[int]]] = None) -> str:
    """Foto grupal del set: de 2 a 6 modelos (antes eran siempre 3).
    img_map[k] = nro de IMAGEN de referencia del modelo k (o None).
    prod_map[k] = nro de IMAGEN con la prenda PROPIA del modelo k, cuando cada pieza del
    set es una prenda distinta (otra estampa) y no el mismo modelo en otro color.
    full_refs=True → las referencias son TOMAS INDIVIDUALES completas ya aprobadas (cara +
    cuerpo + prenda + color), no solo caras. prod_primera = dónde empiezan las fotos del
    producto."""
    sysi = settings.get("system_instruction", "").strip()
    estilo = _style_text(style, settings)
    gw = _gwords(genero)
    es_h = _es_hombre(genero)
    a = (asign or [])[:SET_MAX_MODELOS]
    if not a:
        a = [{"nombre": "", "color": "blanco"}]
    while len(a) < 2:
        a.append({"nombre": "", "color": a[-1].get("color", "blanco")})
    N = len(a)
    n_txt = _n_txt(N)
    # "las TRES" / "los TRES", "juntas" / "juntos": una foto grupal de varones escrita
    # en femenino terminaba dibujando mujeres.
    las = "los" if es_h else "las"
    juntas = "juntos" if es_h else "juntas"
    distintas = "distintos" if es_h else "distintas"
    todas = f"{las} {n_txt.lower()}"
    pm = list(prod_map or [])
    pm += [None] * (N - len(pm))
    prendas_propias = any(x is not None for x in pm)
    if str(p.get("modo_minimo", "")).lower() in ("si", "sí", "true", "1", "on"):
        # MODO SIMPLE (grupal) — misma lógica que la individual: quiénes son, cuerpo entero,
        # escenario y prenda. Sin medidas de cuerpo ni encuadres cerrados.
        im = list(img_map or [])
        im += [None] * (N - len(im))
        quienes = []
        for k in range(N):
            col_k = str(a[k].get("color", "")).strip()
            prenda_k = (f"la prenda de la IMAGEN {pm[k]}" if pm[k] is not None
                        else f"la prenda en color {col_k}")
            if im[k] is not None:
                quienes.append(f"{gw['modelo']} de la IMAGEN {im[k]} con {prenda_k}")
            else:
                et = APAR_ETNIA.get(str(a[k].get("etnia", "")).lower(), "")
                quienes.append((gw["persona"] + (f" {et}" if et else "")
                                + f" con {prenda_k}"))
        rango_s = (str(prod_primera) if n_prod <= 1
                   else f"{prod_primera} a {prod_primera + n_prod - 1}")
        fondo_s = str(p.get("fondo", "")).strip() or "un estudio claro con luz natural"
        det = str(p.get("tela", "")).strip()
        compl = ""
        if _complemento_aplica(p):
            compl = (f"{todas.capitalize()} llevan también "
                     + ("un bóxer" if es_h else "una bombacha") + " haciendo juego. ")
        ind = str(p.get("aclaraciones", "")).strip()
        return (
            estilo + "\n\n"
            + f"1) Foto de campaña con {n_txt} modelos {juntas}: " + "; ".join(quienes) + ". "
            + f"Son {n_txt.lower()} {gw['genero_p']} {distintas}, no gemel"
            + ("os" if es_h else "as") + ". "
            + (f"2) ENCUADRE OBLIGATORIO: {ENCUADRE_ZONA[_zona(p)][0]} {todas.capitalize()} "
               f"{juntas} y relajad" + ("os" if es_h else "as") + ", poses naturales y "
               "espontáneas estilo Instagram, no rígidas. "
               if _zona(p) else
               f"2) Foto de CUERPO ENTERO de {todas}, de pies a cabeza, {juntas} y "
               "relajad" + ("os" if es_h else "as") + ", poses naturales y espontáneas "
               "estilo Instagram, no rígidas. ")
            + f"3) Están en {fondo_s}. "
            + (f"4) Cada un{'o' if es_h else 'a'} lleva SU prenda, la de SU imagen: son "
               "prendas DISTINTAS entre sí (otra estampa), no la misma en otro color. "
               if prendas_propias else
               f"4) {todas.capitalize()} llevan la MISMA prenda de las IMÁGENES {rango_s} "
               "(mismo diseño y calce), cada un" + ("o" if es_h else "a") + " en su color. ")
            + (f"Detalle de la tela: {det}. " if det else "")
            + ((_outfit_grupal(p, a) + " ") if _outfit_grupal(p, a) else "")
            + compl
            + "5) Recordá: cuerpo entero, mucha luz, la prenda nítida y fiel a la foto real."
            + (f"\n\n{ind}" if ind else "")
        )
    verano = str(p.get("temporada", "")).strip().lower() == "verano"
    fondo_def = ("playa al aire libre, día soleado" if verano else "pared clara y luminosa")
    cuerpo = _bloque_cuerpo(p, genero)
    img_map = list(img_map or [])
    img_map += [None] * (N - len(img_map))
    tiene_caras = any(x is not None for x in img_map)
    prod_ref = _bloque_producto_ref(n_prod, primera_idx=prod_primera)
    rango = (str(prod_primera) if n_prod <= 1
             else f"{prod_primera} a {prod_primera + n_prod - 1}")
    specs = "\n".join(_modelo_spec(a[k], _LETRAS[k], img_map[k], genero, pm[k])
                      for k in range(N))
    if tiene_caras and full_refs:
        caras = ", ".join(f"IMAGEN {img_map[k]} = modelo {_LETRAS[k]}"
                          for k in range(N) if img_map[k] is not None)
        ident = (
            f"Las siguientes imágenes son TOMAS INDIVIDUALES YA APROBADAS de cada modelo "
            f"({caras}). Cada una muestra EXACTAMENTE cómo es {gw['modelo']}: su cara, su "
            "cuerpo, su peinado, su prenda y el color de su prenda. Tu trabajo es REUNIR a "
            f"ESAS {n_txt} personas, tal cual se ven en sus tomas (misma cara, mismo cuerpo, "
            "misma prenda, mismo color, mismo peinado), en UNA foto grupal nueva. NO "
            f"{las} cambies en nada; solo cambian la pose y la interacción entre ell"
            + ("os" if es_h else "as") + ".\n"
            "DEFINICIÓN DE CADA MODELO (coincide con su toma):\n" + specs + "\n\n"
        )
    elif tiene_caras:
        caras = ", ".join(f"IMAGEN {img_map[k]} = modelo {_LETRAS[k]}"
                          for k in range(N) if img_map[k] is not None)
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
        comp = random.choice(_COMPOSICIONES_GRUPAL_H if es_h else _COMPOSICIONES_GRUPAL)
    letras_txt = ", ".join(_LETRAS[:N])
    tarea = (
        f"TAREA: generá UNA foto de campaña de catálogo REAL con {las} {n_txt} modelos "
        f"({letras_txt}) {juntas}, en actitud espontánea, relajada y cálida, con gestos "
        "naturales. "
        + comp + " Nada rígido ni artificial; que no parezca posado de estudio. "
        + (f"Cada un{'o' if es_h else 'a'} con SU prenda, la de SU imagen (son prendas "
           "distintas entre sí, no la misma en otro color). "
           if prendas_propias else
           f"Cada un{'o' if es_h else 'a'} con la MISMA prenda de la(s) IMAGEN(es) {rango} "
           "(mismo diseño y calce) pero en SU color. ")
        + ("Encuadre: el del ENCUADRE OBLIGATORIO de más abajo, " if _zona(p) else
           (f"Encuadre de cuerpo entero (la prenda es de abajo), " if _ctx("abajo") else
            "Encuadre aproximado de la cadera para arriba, "))
        + f"{todas} bien visibles.\n\n"
    )
    return (
        (sysi + "\n\n" if sysi else "")
        + (_estilo_avatar(p).strip() + "\n\n" if _hay_estilo_avatar(p) else "")
        + estilo + "\n\n"
        + ident
        + prod_ref + "\n\n"
        + tarea
        + ((_outfit_grupal(p, a) + "\n\n") if _outfit_grupal(p, a) else "")
        + (f"Cada modelo lleva una prenda DISTINTA: copiá cada una EXACTA de SU imagen "
           "(estampa, colores, cortes y terminaciones). No mezcles las estampas entre "
           f"ell{'os' if es_h else 'as'} ni le pongas a un{'o' if es_h else 'a'} el diseño "
           f"de otr{'o' if es_h else 'a'}.\n\n"
           if prendas_propias else
           f"Lo ÚNICO que cambia entre {todas} es el color de la prenda; el modelo de prenda "
           f"es idéntico en {todas}.\n\n")
        + FIDELITY_FABRIC + "\n\n"
        + (_bloque_complemento(p, genero) + "\n\n" if _complemento_aplica(p) else "")
        + f"Fondo/escenario: {p.get('fondo') or fondo_def}. "
        + f"Iluminación: {str(p.get('luz') or '').strip() or 'natural y pareja'}.\n"
        + ((_bloque_camara_foto(p) + "\n") if _bloque_camara_foto(p) else "")
        + _bloque_encuadre_zona(p)
        + cuerpo
        + (("\n\nACLARACIONES DE LA USUARIA (respetalas): "
            + str(p.get("aclaraciones", "")).strip())
           if str(p.get("aclaraciones", "")).strip() else "")
        + CORTES_BLOCK
        # Los bloques de una sola persona, dichos para el grupo ("ella PROYECTA SU SOMBRA").
        + "\n\n" + _grupal(VIDA_BLOCK, es_h)
        + "\n\n" + _grupal(CALIDAD_BLOCK, es_h)
        + _grupal(FISICA_BLOCK, es_h)
        + "\n\nFOTOGRAFÍA REAL (muy importante): esta imagen tiene que ser indistinguible de "
        "una foto REAL tomada con cámara profesional (la cámara, el lente y la luz son los de "
        "más arriba). NO una imagen generada. Exigencias:\n"
        + f"• PIEL REAL EN {todas.upper()}: cada un{'o' if es_h else 'a'} con textura de piel "
        "visible y distinta (poros, pecas, vello suave, pliegues naturales al moverse, brillo "
        f"natural en zonas de luz). Nada de piel lisa/plástica/porcelana en NINGUN"
        + ("O" if es_h else "A") + ".\n"
        "• MOMENTO ESPONTÁNEO: capturad" + ("os" if es_h else "as") + " en un instante real "
        "entre risas, no posad" + ("os" if es_h else "as") + " en fila. Asimetría natural: "
        "alturas distintas, hombros relajados, algun" + ("o" if es_h else "a") + " en medio "
        "gesto.\n"
        "• Imperfecciones de foto real: profundidad de campo (fondo levemente desenfocado), "
        "leve grano fotográfico, luz no perfectamente pareja.\n"
        "Evitá el aspecto de render/IA a toda costa."
        + ("\n\nENCUADRE EDITORIAL SEGURO: foto de catálogo de moda profesional y respetuosa. "
           f"Plano de la cadera para arriba (no se ve de la cintura para abajo). {las.capitalize()} "
           f"{n_txt} modelos llevan "
           + ("un BÓXER liso" if es_h else "una BOMBACHA lisa")
           + " de talle clásico que combina (nunca sin la parte de abajo). "
           "Poses relajadas y elegantes, actitud natural, estética limpia tipo campaña de ropa "
           "interior de tienda. Estética de catálogo comercial, limpia y prolija."
           if seguro else "")
        + "\n"
        f"PROHIBIDO: cambiar el diseño de la prenda; que {todas} sean gemel"
        + ("os" if es_h else "as") + "; logos, marcas de agua o texto. "
        f"Exactamente {n_txt} {gw['genero_p']}."
    )


_COMPLETO_KW = ("pijama", "piyama", "conjunto", " set", "pantal", "bombacha", "short", "enteriza",
                "malla", "bikini", "vestido", "camisón", "camison", "body", "bóxer", "boxer",
                "calza", "pollera", "jogging", "enterito", "mono", "bata", "tanga", "culotte")


def _complemento_aplica(p: Dict[str, Any]) -> bool:
    """"Completar con bombacha" sólo si la prenda es SOLO la parte de arriba. Viene tildado
    por defecto, y a un pijama con pantalón le agregaba una bombacha encima."""
    if str(p.get("complemento", "")).lower() not in ("si", "sí", "true", "1", "on", "auto"):
        return False
    txt = " " + " ".join(str(p.get(k, "")) for k in
                         ("ficha", "producto_manual", "prenda_desc", "tela")).lower()
    return not any(k in txt for k in _COMPLETO_KW)


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
                          genero: Optional[str] = None,
                          camara: Optional[int] = None,
                          camara_auto: Optional[int] = None) -> str:
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
        if _complemento_aplica(p):
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
    _toma_espalda = (paneles <= 1 and (force_pose in (3, 10) or _toma_atras_pose(_pose_low)))
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
            "fusiones la IMAGEN 1 con las fotos del producto: son cosas distintas."
            + (_ESTAV_IDENT_ES if _hay_estilo_avatar(p) else "") + "\n\n"
        )
        tarea = (
            f"TAREA: vestí {_a_modelo(gw)} de la IMAGEN 1 con la prenda COMPLETA de la(s) "
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
            "(misma cara, mismo pelo, mismo cuerpo) consistente en todas las tomas."
            + (" Y arreglada (peinado, maquillaje, uñas y accesorios) EXACTAMENTE como "
               "dice el bloque ONDA DE LA MODELO de arriba, en todas las tomas."
               if _hay_estilo_avatar(p) else "") + "\n\n"
        )
        tarea = (
            f"TAREA: vestí {_a_modelo(gw)} con la prenda COMPLETA de la(s) IMAGEN(es) {rango}, "
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
    _zn = bool(_zona(p))
    _lc = _es_ropa_interior(p)
    _cm = cam_idx(camara)
    _ca = cam_idx(camara_auto)
    _auto = not _auto_off(settings)
    _lug = _hay_lugar(p) and _auto
    _ext = _es_exterior(p)
    if paneles > 1:
        pose_block = _bloque_paneles(paneles, aspect, pose_offset, genero, zona=_zn,
                                     lenc=_lc, cam=_cm, cam_auto=_ca, lugar=_lug, auto=_auto,
                                     ext=_ext)
    elif force_pose is not None:
        pose_block = _bloque_pose_unica(force_pose, genero, zona=_zn, lenc=_lc,
                                        cam=_cm, cam_auto=_ca, lugar=_lug,
                                        segura=bool(p.get("_pose_segura")), auto=_auto,
                                        ext=_ext)
    elif not user_pose:
        pose_block = _bloque_pose_unica(pose_offset, genero, zona=_zn, lenc=_lc,
                                        cam=_cm, cam_auto=_ca, lugar=_lug, auto=_auto,
                                        ext=_ext)
    else:
        # POSE ESCRITA POR ELLA. Antes viajaba sólo como un renglón más de la puesta en
        # escena ("- Pose: …"), sin ninguna marca de prioridad, y el ángulo y el rincón
        # quedaban flotando sueltos más abajo: los modelos los ignoraban. Ahora los tres
        # van JUNTOS y con el mismo encabezado obligatorio que una pose del listado.
        _tr = [f"\nPOSE DE ESTA TOMA (obligatoria, máxima prioridad — la escribió la "
               f"usuaria): {user_pose}. Respetala TAL CUAL, es lo que ella pidió."]
        _ci = _cm if _cm is not None else (_ca if _auto else None)
        if _ci is not None:
            _tr.append(_camara(_ci, _lc, elegida=_cm is not None))
        # Si su pose ya dice dónde está, el recorrido se calla: manda lo que ella escribió.
        if _lug and _ca is not None and not _pose_dice_lugar(user_pose):
            _tr.append(_rincon(_ca, True, ext=_ext, pose=user_pose, cam=_ci))
        pose_block = "\n".join(_tr)
    _enc_zona = _bloque_encuadre_zona(p)
    _enc_linea = (ENCUADRE_ZONA[_zona(p)][0] if _zn
                  else (p.get("encuadre")
                        or ("el TAMAÑO DE PLANO indicado en ESTA TOMA (bloque de arriba)"
                            if _auto else "el indicado en ESTA TOMA (bloque de arriba)")))
    return (
        (sysi + "\n\n" if sysi else "")
        + (_bloque_cuerpo_top(p, genero) if _hay_cuerpo else "")
        # La ONDA va AL TOPE, como el cuerpo: enterrada al final perdía contra la foto del
        # avatar ("copiá su pelo") y salían tomas al natural dentro del mismo set.
        + (_estilo_avatar(p).strip() + "\n\n" if _hay_estilo_avatar(p) else "")
        + estilo + "\n\n"
        + pedido_top
        + (_enc_zona.lstrip("\n") + "\n" if _enc_zona else "")
        # ESTA TOMA al TOPE: la pose, el tamaño de plano, la cámara y el rincón quedaban a
        # la mitad de un pedido de 20.000 caracteres, después de renglones que los
        # contradecían ("Pose: natural", "Encuadre: cuerpo entero"): salían todas parecidas.
        + "ESTA TOMA — lo que la distingue de las demás del set (obligatorio; la modelo y "
          "la prenda NO cambian, cambia sólo esto):"
        + pose_block.rstrip("\n") + "\n\n"
        + (BEACHWEAR_BLOCK + "\n\n" if verano else "")
        + identidad
        + prod_ref + "\n\n"
        + tarea
        # Las aclaraciones ya van arriba como PEDIDO DE LA USUARIA: no se repiten acá.
        + f"Detalles de la prenda a respetar:\n{_bloque_detalles({**p, 'aclaraciones': ''})}\n\n"
        + (_bloque_complemento(p, genero) + "\n\n" if _complemento_aplica(p) else "")
        + FIDELITY_FABRIC + "\n\n"
        "Puesta en escena:\n"
        f"- Pose: {user_pose or 'la indicada en ESTA TOMA (bloque de arriba)'}\n"
        f"- Fondo/escenario: {p.get('fondo') or fondo_def}\n"
        f"- Iluminación: {p.get('luz') or luz_def}\n"
        + (f"- {_bloque_camara_foto({**p, '_estilo': style}, pose_txt=user_pose + ' ' + str(pose_block))}\n"
           if _bloque_camara_foto({**p, '_estilo': style}, pose_txt=user_pose + ' ' + str(pose_block)) else "")
        + f"- Encuadre: {_enc_linea}\n"
        + (f"- Encuadre extra pedido: {p.get('encuadre')}\n"
           if _zn and str(p.get('encuadre', '')).strip() else "")
        + cuerpo
        + ("\n\n" + FONDO_NITIDO if str(p.get("fondo_foco", "")).lower() == "nitido" else "")
        + ("\n\n" + VIENTO_BLOCK
           if str(p.get("viento", "")).lower() in ("si", "sí", "true", "1", "on") else "")
        + "\nREVISIÓN FINAL (antes de entregar la imagen): la POSE, el ENCUADRE, la CÁMARA "
          "y el RINCÓN (si lo hay) son EXACTAMENTE los del bloque ESTA TOMA de arriba "
          "— si alguno no se cumple, la toma está mal. Y la modelo y la prenda son las de "
          "las referencias, sin cambios."
        + ("\nRECORDÁ: qué parte del cuerpo entra en el cuadro lo fija el ENCUADRE "
           "OBLIGATORIO, no la pose." if _zn else "")
        + (FRENTE_ESPALDA_DISTINTOS if p.get("_espalda_distinta") else "")
        + (ESPALDA_GUARD if _toma_espalda else "")
        + ("\nPROHIBIDO EN LA ESPALDA: copiar el moño, el encaje, el estampado, bolsillo, botones o escote del "
           "FRENTE en la parte de atrás. Si no hay foto de la espalda del producto, la "
           "espalda va simple y coherente con la prenda (misma tela y color), SIN inventar "
           "el diseño del frente atrás." if _toma_espalda else "")
        + (ESPALDA_VERANO_SOFT if (verano and _toma_espalda) else "")
        + CORTES_BLOCK
        + "\n\n" + VIDA_BLOCK_TOMA
        + "\n\n" + CALIDAD_BLOCK
        + FISICA_BLOCK
        + (CLOSEUP_BLOCK if (paneles <= 1 and force_pose == 8) else "")
        + (DETALLE_BLOCK if (paneles <= 1 and force_pose in (9, 10)) else "")
        + "\n"
        "PROHIBIDO: cambiar el diseño o el color de la prenda; agregar logos, marcas de agua "
        "o texto; agregar otra persona; poses o encuadres sugerentes; "
        + ("inventar accesorios que NO sean los de la ONDA DE LA MODELO (ésos sí van, en "
           "todas las tomas; nada de reloj, piercings, tatuajes ni gorras que no estén ahí)"
           if _hay_estilo_avatar(p) else
           "inventar accesorios (reloj, pulseras, aros, piercings, tatuajes, gorras)")
        + " o calzado que no se haya pedido — con pijama/ropa de descanso va descalzo o "
        "con medias. "
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


# ─────────────────────────────────────────────────────────────────────────────
# NENAS / NENES — la prenda SOLA
# Acá nunca hay una persona: son tomas de producto (maniquí fantasma, flat-lay,
# percha, colgada, doblada, en la arena). Lo único que hace falta que el motor
# entienda es que la prenda es de TALLE INFANTIL: sin esto dibuja una malla de
# mujer achicada —con copas, escote profundo y cadera marcada— que no se parece
# en nada a lo que se vende.
# ─────────────────────────────────────────────────────────────────────────────

KIDS_TALLES = {
    "0-1": "de bebé de 6 meses a 1 año (talle de bebé: body, enterito, ranita, gorrito; "
           "mínimo, blando y redondito)",
    "1-2": "de nena/nene de 1 a 2 años (talle de bebé grande, de deambulador)",
    "2-4": "de nena/nene de 2 a 4 años (talle muy chico, de bebé grande)",
    "4-6": "de nena/nene de 4 a 6 años (talle chico)",
    "6-8": "de nena/nene de 6 a 8 años",
    "8-10": "de nena/nene de 8 a 10 años",
    "10-12": "de nena/nene de 10 a 12 años (talle preadolescente)",
    "12-14": "de nena/nene de 12 a 14 años (talle junior)",
    "14-16": "de adolescente de 14 a 16 años (talle adolescente: ya con las proporciones "
             "de un cuerpo joven, pero juvenil y sencillo)",
    "16-18": "de adolescente de 16 a 18 años (talle adolescente grande, casi de adulto joven)",
}

# Las etapas (v2.62): un bebé no corre ni salta, y una chica de 17 no juega con un balde.
# La etapa decide las poses, cómo se lo nombra y cómo se describe la prenda sola.
KIDS_ETAPAS = {"0-1": "bebe", "1-2": "deambulador", "2-4": "chico", "4-6": "chico",
               "6-8": "chico", "8-10": "chico", "10-12": "chico", "12-14": "chico",
               "14-16": "adolescente", "16-18": "adolescente"}


def _kids_etapa(p: Dict[str, Any]) -> str:
    return KIDS_ETAPAS.get(str(p.get("kids_talle", "")).strip(), "chico")


def _es_kids(p: Dict[str, Any]) -> bool:
    if str(p.get("temporada", "")).strip().lower() == "kids":
        return True
    if str(p.get("kids", "")).strip().lower() in ("si", "sí", "true", "1", "on"):
        return True
    txt = " ".join(str(p.get(k, "")) for k in
                   ("producto_manual", "prenda_desc", "aclaraciones", "piezas")).lower()
    return any(k in txt for k in ("de nena", "de nene", "para nena", "para nene",
                                  "infantil", "kids", "niña", "nina", "niño", "nino",
                                  "bebé", "bebe", "beba", "adolescente"))


def _bloque_kids(p: Dict[str, Any]) -> str:
    """Proporciones de talle infantil para una toma de PRODUCTO (sin persona)."""
    talle = KIDS_TALLES.get(str(p.get("kids_talle", "")).strip(), "")
    quien = ("de nena" if str(p.get("kids_quien", "")).strip().lower() == "nena"
             else "de nene" if str(p.get("kids_quien", "")).strip().lower() == "nene"
             else "")
    etapa = _kids_etapa(p)
    if etapa == "bebe":
        forma = ("Se ve de BEBÉ: chiquita, corta y ancha, redondita, el cuello amplio, las "
                 "mangas y las piernas cortitas y anchas, broches o botones grandes (en la "
                 "entrepierna si es un body o un enterito), tela suave. La proporción es la "
                 "de un bebé: torso corto y redondo, nada de cintura.\n")
    elif etapa == "adolescente":
        forma = ("Se ve JUVENIL: el molde ya tiene el largo y el ancho de un cuerpo adolescente "
                 "(entre el de un chico y el de un adulto), la silueta es sencilla y deportiva, "
                 "sin exagerar ninguna curva. La proporción entre el ancho y el alto es la de "
                 "una prenda de adolescente, no la de una mujer adulta.\n")
    else:
        forma = ("Se ve CHICA: el molde es corto y angosto, los hombros/breteles están juntos, "
                 "el torso es corto, la silueta es RECTA de arriba a abajo y las aberturas de "
                 "brazos y piernas son chiquitas. La proporción entre el ancho y el alto es la "
                 "de una prenda de chico, no la de una de adulto.\n")
    return (
        "PRENDA DE TALLE INFANTIL (importante, define TODA la forma de la prenda): esta es "
        "una prenda " + (talle or ("infantil " + quien if quien else "infantil")) + ". "
        + forma
        + "PROHIBIDO que parezca una prenda de mujer adulta achicada: NADA de copas, aros, "
        "push-up, taza moldeada, relleno, escote pronunciado, tiritas finas de lencería, "
        "corte alto de pierna estilo brasileño, ni cadera o cintura marcadas. El corte es "
        "simple, deportivo y prolijo, de ropa de "
        + ("bebé" if etapa == "bebe" else "adolescentes" if etapa == "adolescente" else "chicos")
        + ".\n"
        "La prenda está SOLA: no hay ninguna persona, ni chico, ni chica, ni bebé, ni "
        "cuerpo, ni parte de un cuerpo, ni maniquí visible en la imagen."
    )


# ── Nenas / nenes CON MODELO ────────────────────────────────────────────────
# Para la ropa que cubre: ponchos de toalla, pijamas, remeras, buzos, vestidos,
# camperas, bikinis, ropa interior. El chico lo inventa la IA (nunca un avatar: ninguna cara real). La
# regla de qué va con modelo y qué va solo la decide LA PRENDA, no el menú:

KIDS_EDAD = {"0-1": "8 meses", "1-2": "1 año y medio", "2-4": "3 años", "4-6": "5 años",
             "6-8": "7 años", "8-10": "9 años", "10-12": "11 años", "12-14": "13 años",
             "14-16": "15 años", "16-18": "17 años"}
KIDS_EDAD_EN = {"0-1": "8-month-old", "1-2": "18-month-old", "2-4": "3-year-old",
                "4-6": "5-year-old", "6-8": "7-year-old", "8-10": "9-year-old",
                "10-12": "11-year-old", "12-14": "13-year-old", "14-16": "15-year-old",
                "16-18": "17-year-old"}

def _kids_con_modelo(p: Dict[str, Any]) -> bool:
    """Desde v2.43 el panel Generar de kids es siempre con modelo (las prendas solas van
    por la pestaña Producto). Sólo un pedido que diga "sola" explícito (una página vieja
    cacheada, una plantilla guardada) queda sin modelo."""
    return str(p.get("kids_modo", "")).strip().lower() != "sola"


def _kids_prenda_cubre(p: Dict[str, Any]) -> bool:
    """La regla la decide LA PRENDA, no el menú: pijamas, ponchos, remeras, buzos,
    vestidos y camperas CUBREN y pueden ir con modelo. Mallas, bikinis y ropa
    interior NO: esas tomas salen siempre como prenda sola (sin persona), por
    diseño y por seguridad."""
    txt = " ".join(str(p.get(k, "")) for k in
                   ("producto_manual", "prenda_desc", "piezas", "aclaraciones",
                    "tela")).lower()
    descubre = ("malla", "bikini", "traje de baño", "traje de bano", "trikini",
                "enteriza", "swimwear", "beachwear", "ropa interior", "bombacha",
                "corpiño", "corpino", "underwear", "lencería", "lenceria",
                "culotte", "tanga", "conjunto interior")
    if any(k in txt for k in descubre):
        return False
    if str(p.get("temporada", "")).strip().lower() == "verano":
        return False   # temporada verano en kids = mallas
    return True


def _kids_modelo_ok(p: Dict[str, Any]) -> bool:
    """Compatibilidad: refleja únicamente la elección explícita del modo."""
    return _es_kids(p) and _kids_con_modelo(p)


def _aviso_kids_sola(p: Dict[str, Any]) -> str:
    """Texto del aviso cuando un pedido kids "con modelo" se convierte en toma de
    producto (la prenda no cubre)."""
    q = _kq(p)
    return ("La toma salió como PRENDA SOLA (sin modelo): las mallas y la ropa "
            f"interior de {q['chicos']} van siempre sin persona, por diseño y por "
            "seguridad. Las prendas que cubren (pijamas, ponchos, remeras, buzos, "
            "vestidos...) sí pueden ir con modelo.")


_KIDS_CAMPOS_PRENDA = (("producto_manual", "Producto"), ("prenda_desc", "Descripción"),
                       ("piezas", "Piezas"), ("aclaraciones", "Aclaraciones"))





def _kq(p: Dict[str, Any]) -> Dict[str, str]:
    """Palabras que cambian entre nena y nene (y según la etapa: beba/bebé, chica/chico
    adolescente)."""
    nene = str(p.get("kids_quien", "")).strip().lower() == "nene"
    etapa = _kids_etapa(p)
    q = ({"o": "o", "un": "un", "quien": "nene", "el": "el", "lo": "lo",
          "chico": "chico", "chicos": "nenes"}
         if nene else
         {"o": "a", "un": "una", "quien": "nena", "el": "la", "lo": "lo",
          "chico": "chica", "chicos": "nenas"})
    if etapa == "bebe":
        q["quien"], q["chicos"] = ("bebé", "bebés") if nene else ("beba", "bebas")
        q["chico"] = "bebé"
    elif etapa == "adolescente":
        q["quien"] = "chico adolescente" if nene else "chica adolescente"
        q["chicos"] = "chicos adolescentes" if nene else "chicas adolescentes"
    return q


# Cómo es una pose "de verdad" en cada etapa (va después de la pose obligatoria).
_KIDS_POSE_FRASE = {
    "bebe": "Pose de BEBÉ de verdad — acostad{o}, sentad{o} con apoyo o gateando, nunca "
            "de pie sol{o} —, espontánea, como en una foto de familia",
    "deambulador": "Pose de {chico} de 1 a 2 años de verdad — pasitos inseguros, "
                   "agarrándose de algo, sentad{o} jugando —, espontánea, como en una "
                   "foto de familia",
    "chico": "Pose de {chico} de verdad — jugando, moviéndose, riéndose —, espontánea, "
             "como en una foto de familia",
    "adolescente": "Pose de ADOLESCENTE de verdad — relajad{o}, canchera, riéndose con "
                   "amigos o mirando el mar —, espontánea, como en una foto de vacaciones; "
                   "nunca una pose de modelo adulta",
}
_KIDS_POSE_FRASE_EN = {
    "bebe": "A real baby's pose: lying down, sitting with support or crawling, never "
            "standing alone; spontaneous, like a family photo.",
    "deambulador": "A real toddler's pose: wobbly steps, holding on to something, sitting "
                   "and playing; spontaneous, like a family photo.",
    "chico": "A real kid's pose, playful and spontaneous, like a family photo.",
    "adolescente": "A real teenager's pose: relaxed, cool, laughing with friends or looking "
                   "at the sea; spontaneous, like a holiday photo, never an adult model pose.",
}


def _kids_pose_frase(p: Dict[str, Any]) -> str:
    q = _kq(p)
    return _KIDS_POSE_FRASE[_kids_etapa(p)].format(o=q["o"], chico=q["chico"].upper())


# Poses de CHICO: jugando, moviéndose, riéndose. Ninguna es una pose de adulto en
# miniatura, y todas muestran la prenda entera o su parte importante.
_KIDS_POSES = [
    "de pie, de frente a cámara, riéndose a carcajadas, la prenda entera a la vista de la "
    "cabeza a los pies",
    "corriendo hacia la cámara, en pleno movimiento, la prenda "
    "flameando",
    "abrazándose con los brazos cruzados y una sonrisa pícara, el pelo despeinado, "
    "cara feliz, plano medio",
    "sentad{o} en el piso con las piernas cruzadas, jugando con un juguete acorde al lugar (balde y palita en la playa, bloques o un muñeco adentro), "
    "mirando lo que hace",
    "de espaldas a la cámara, mirando algo del lugar, la prenda entera a la vista por detrás",
    "saltando con los brazos abiertos, capturad{o} en el aire, riéndose",
    "de perfil, caminando tranquil{o}, mirando algo en el suelo",
    "abrazando una pelota grande, mirando a cámara con una sonrisa tímida",
    "de pie con las manos en la cintura, orgullos{o}, sonrisa enorme, plano entero",
    "primer plano de la cara y los hombros, riéndose, con la capucha o el cuello de la "
    "prenda bien a la vista",
]


# Las mismas poses, en inglés, para Seedream/Qwen/FLUX (ignoran el castellano).
_KIDS_POSES_EN = [
    "standing, facing the camera, laughing out loud, the whole garment visible head to toe",
    "running toward the camera, mid-motion, the garment "
    "fluttering",
    "hugging themself with crossed arms and a cheeky smile, messy hair, happy face, "
    "medium shot",
    "sitting on the ground cross-legged, playing with a toy that suits the place (bucket and shovel at the beach, blocks or a doll indoors), "
    "looking at what they are doing",
    "seen from BEHIND, back to the camera, looking at something in the place, the whole back of the "
    "garment visible",
    "jumping with the arms wide open, caught mid-air, laughing",
    "in side profile, walking calmly, looking at something on the ground",
    "hugging a big ball, looking at the camera with a shy smile",
    "standing with the hands on the hips, proud, huge smile, full body",
    "close-up of the face and shoulders, laughing, with the hood or the collar of the "
    "garment clearly visible",
]
# Bebés (6 meses a 1 año): acostado, sentado con apoyo, gateando. La 4 sigue siendo de
# espaldas (el set la usa para la vista trasera de la prenda).
_KIDS_POSES_BEBE = [
    "acostad{o} boca arriba sobre una manta clara, mirando a cámara desde arriba y "
    "riéndose, la prenda entera a la vista",
    "sentad{o} con apoyo sobre una manta, agarrando un sonajero con las dos manos, cara "
    "de sorpresa",
    "gateando hacia la cámara sobre el piso, levantando la cabeza, con una sonrisa sin "
    "dientes",
    "acostad{o} boca abajo levantando la cabeza y el pecho, apoyad{o} en los antebrazos, "
    "plano medio",
    "de espaldas a la cámara, sentad{o} en la manta mirando un juguete, la prenda entera a "
    "la vista por detrás",
    "acostad{o} de costado sobre la manta con las piernitas recogidas, riéndose",
    "de perfil, sentad{o}, mirando un peluche que tiene delante",
    "abrazando un peluche grande, mirando a cámara con los ojos bien abiertos",
    "sentad{o} agarrándose los pies con las manos, jugando con sus propios pies, plano "
    "entero",
    "primer plano de la cara y los hombros, sonriendo, con el cuello o la capucha de la "
    "prenda bien a la vista",
]
_KIDS_POSES_BEBE_EN = [
    "lying on the back on a light blanket, looking up at the camera and laughing, the "
    "whole garment visible",
    "sitting with support on a blanket, holding a rattle with both hands, surprised face",
    "crawling toward the camera on the floor, head up, with a toothless smile",
    "lying on the tummy lifting the head and chest, propped on the forearms, medium shot",
    "seen from BEHIND, sitting on the blanket looking at a toy, the whole back of the "
    "garment visible",
    "lying on the side on the blanket with the little legs tucked in, laughing",
    "in side profile, sitting, looking at a plush toy in front of them",
    "hugging a big plush toy, looking at the camera with wide-open eyes",
    "sitting and holding their own feet with the hands, playing with them, full body",
    "close-up of the face and shoulders, smiling, with the collar or the hood of the "
    "garment clearly visible",
]
# 1 a 2 años: pasitos, agarrado de algo, sentado jugando.
_KIDS_POSES_DEAMBULADOR = [
    "de pie agarrad{o} del borde de un sillón, de frente a cámara, riéndose, la prenda "
    "entera a la vista",
    "caminando con pasitos inseguros hacia la cámara, los brazos abiertos para no caerse",
    "abrazándose con los brazos cruzados, el pelo despeinado, cara feliz, "
    "plano medio",
    "sentad{o} en el piso con las piernas abiertas, apilando cubos de colores, mirando lo "
    "que hace",
    "de espaldas a la cámara, parad{o} mirando algo del lugar, la prenda entera a la "
    "vista por detrás",
    "en cuclillas mirando algo en el pasto, curios{o}",
    "de perfil, caminando de la mano de un adulto que queda fuera de cuadro (sólo entra la "
    "mano)",
    "abrazando un peluche grande, mirando a cámara con una sonrisa tímida",
    "de pie con los dos brazos arriba pidiendo upa, riéndose, plano entero",
    "primer plano de la cara y los hombros, riéndose, con la capucha o el cuello de la "
    "prenda bien a la vista",
]
_KIDS_POSES_DEAMBULADOR_EN = [
    "standing while holding on to the edge of a sofa, facing the camera, laughing, the "
    "whole garment visible",
    "walking with wobbly little steps toward the camera, arms out for balance",
    "hugging themself with crossed arms, messy hair, happy face, medium shot",
    "sitting on the floor with the legs apart, stacking colored blocks, looking at what "
    "they are doing",
    "seen from BEHIND, standing and looking at something in the place, the whole back of "
    "the garment visible",
    "squatting down to look at something in the grass, curious",
    "in side profile, walking while holding the hand of an adult who stays out of frame "
    "(only the hand is visible)",
    "hugging a big plush toy, looking at the camera with a shy smile",
    "standing with both arms up asking to be picked up, laughing, full body",
    "close-up of the face and shoulders, laughing, with the hood or the collar of the "
    "garment clearly visible",
]
# 14 a 18 años: relajado, canchero, de vacaciones. Nunca una pose de modelo adulta.
_KIDS_POSES_ADOLESCENTE = [
    "de pie, de frente a cámara, riéndose relajad{o}, la prenda entera a la vista de la "
    "cabeza a los pies",
    "caminando hacia la cámara con una mochila al hombro, en "
    "movimiento",
    "sentad{o} en un murito, un escalón o el piso con las rodillas dobladas, mirando a lo lejos, plano "
    "medio",
    "apoyad{o} contra una pared o una palmera con los brazos cruzados, sonrisa canchera",
    "de espaldas a la cámara, mirando algo del lugar, la prenda entera a la vista por detrás",
    "saltando con los brazos abiertos, capturad{o} en el aire, riéndose",
    "de perfil, caminando tranquil{o} con los auriculares puestos, mirando adelante",
    "riéndose con una mano en el pelo, mirando a cámara, plano tres cuartos",
    "de pie con las manos en los bolsillos o en la cintura, plano entero, sonrisa natural",
    "primer plano de la cara y los hombros, riéndose, con la capucha o el cuello de la "
    "prenda bien a la vista",
]
_KIDS_POSES_ADOLESCENTE_EN = [
    "standing, facing the camera, laughing and relaxed, the whole garment visible head to "
    "toe",
    "walking toward the camera with a backpack on one "
    "shoulder, in motion",
    "sitting on a low wall, a step or the floor with the knees bent, looking into the distance, medium "
    "shot",
    "leaning against a wall or a palm tree with the arms crossed, cool easy smile",
    "seen from BEHIND, back to the camera, looking at something in the place, the whole back of the "
    "garment visible",
    "jumping with the arms wide open, caught mid-air, laughing",
    "in side profile, walking calmly with headphones on, looking ahead",
    "laughing with one hand in the hair, looking at the camera, three-quarter shot",
    "standing with the hands in the pockets or on the hips, full body, natural smile",
    "close-up of the face and shoulders, laughing, with the hood or the collar of the "
    "garment clearly visible",
]
_KIDS_POSES_ETAPA = {"bebe": _KIDS_POSES_BEBE, "deambulador": _KIDS_POSES_DEAMBULADOR,
                     "chico": _KIDS_POSES, "adolescente": _KIDS_POSES_ADOLESCENTE}
_KIDS_POSES_ETAPA_EN = {"bebe": _KIDS_POSES_BEBE_EN, "deambulador": _KIDS_POSES_DEAMBULADOR_EN,
                        "chico": _KIDS_POSES_EN, "adolescente": _KIDS_POSES_ADOLESCENTE_EN}
assert all(len(v) == len(_KIDS_POSES) for v in _KIDS_POSES_ETAPA.values())
assert all(len(v) == len(_KIDS_POSES) for v in _KIDS_POSES_ETAPA_EN.values())


def _kids_poses_es(p: Dict[str, Any]) -> List[str]:
    return _KIDS_POSES_ETAPA[_kids_etapa(p)]


def _kids_poses_en(p: Dict[str, Any]) -> List[str]:
    return _KIDS_POSES_ETAPA_EN[_kids_etapa(p)]


_KIDS_EN = {
    "etnia": {"latina": "Latina", "morocha_tez_oscura": "olive-skinned Latina",
              "caucasica": "fair-skinned", "afro": "Black, dark-skinned", "asiatica": "Asian",
              "mediterranea": "Mediterranean", "arabe": "Middle Eastern", "mestiza": "mixed"},
    "pelo": {"rubio": "blond hair", "castano": "brown hair", "morocho": "dark hair",
             "pelirrojo": "red hair", "negro_lacio": "straight black hair",
             "rulos": "curly hair", "rubia": "blond hair", "castaño_largo": "long brown hair",
             "castaño_ondulado": "wavy brown hair", "pelirroja": "red hair",
             "negro_lacio ": "straight black hair", "corto": "short hair",
             "morocha_largo_ondulado": "long wavy dark hair"},
    "peinado": {"corto": "short", "largo_suelto": "long and loose", "largo_ondulado": "long "
                "and wavy", "media_melena": "shoulder-length", "atado": "in a ponytail",
                "rodete": "in a bun", "trenza": "in a braid", "rapado": "buzz cut"},
    "ojos": {"marrones": "brown eyes", "negros": "dark eyes", "claros": "light eyes",
             "verdes": "green eyes", "celestes": "blue eyes"},
    "altura": {"bajo": "small for their age", "medio": "average height for their age",
               "alto": "tall for their age"},
}


def build_prompt_flux_kids(p: Dict[str, Any], pose_en: str, n_prod: int,
                           prod_tags: Optional[List[str]] = None, n_back_last: int = 0,
                           con_ancla: bool = False, estilo: str = "", toma: str = "",
                           guia: bool = False, pose_es: str = "") -> str:
    """Kids con modelo para Seedream/Qwen/FLUX: corto, en inglés, en positivo (sin nombrar
    ropa interior ni la marca) y con las fotos del producto rotuladas por número."""
    nene = str(p.get("kids_quien", "")).strip().lower() == "nene"
    etapa = _kids_etapa(p)
    child = {"bebe": "baby ", "deambulador": "toddler ", "chico": "",
             "adolescente": "teenage "}[etapa] + ("boy" if nene else "girl")
    age = _edad_kids(p, True)
    rasgos = []
    for key, mapa in (("ap_etnia", "etnia"), ("ap_pelo", "pelo"), ("cuerpo_peinado", "peinado"),
                      ("ap_ojos", "ojos"), ("kids_altura", "altura")):
        v = _KIDS_EN[mapa].get(str(p.get(key, "")).strip().lower(), "")
        if v:
            rasgos.append(v)
    extra = str(p.get("ap_extra", "")).strip()
    if extra:
        rasgos.append(extra)
    piezas = str(p.get("piezas", "")).strip()
    fondo = str(p.get("fondo", "")).strip() or _kids_fondo_def(p, pose_es or pose_en, True)
    luz = str(p.get("luz", "")).strip() or "natural daylight, bright and even"
    # orden de las imágenes: cara (si hay), guía (si hay), y después la prenda
    primera = (2 if con_ancla else 1) + (1 if guia else 0)
    L = [
        f"A realistic children's clothing catalog photo of a {age} {child}, an AI-invented "
        "child (not a real person)" + (", " + ", ".join(rasgos) if rasgos else "")
        + ", happy and natural, the age matching the garment size.",
        "The child wears EXACTLY the garment from the product photo(s): same design, cut, "
        "color, fabric texture and trims, nothing invented; fully dressed as in any kids "
        "catalog, and if the garment is a poncho or a robe it is worn closed."
        + (f" The garment and what else the child wears (not part of the product): {piezas}." if piezas else "")
        + (f" Accessories (only these): {str(p.get('cuerpo_accesorios', '')).strip()}."
           if str(p.get("cuerpo_accesorios", "")).strip() else ""),
        _bloque_vistas_flux(n_prod, primera, prod_tags, n_back_last),
        (f"POSE: {pose_en}. The body pose, camera and framing come from the POSE AND CAMERA "
         f"GUIDE image (see below). " if guia else
         f"MANDATORY POSE (the only pose; do not copy any pose or framing from the reference "
         f"images): {pose_en}. ") + _KIDS_POSE_FRASE_EN[etapa],
        f"Setting: {fondo}. Lighting: {luz}.",
        toma,
        estilo,
        "Photorealistic like a real camera photo, real skin, correct proportions and flawless "
        "anatomy. No makeup, no text, no logos, no watermark. Exactly one child.",
    ]
    if con_ancla:
        L.append("The FIRST reference image is a FACE CROP of the SAME child from this set: keep "
                 "the same child (face, hair, skin tone); nothing else comes from it.")
    acl = str(p.get("aclaraciones", "")).strip()
    if acl:
        L.append(f"Also respect: {acl[:400]}")
    return " ".join(x for x in L if x)


# Ficha de una prenda del pack de kids → campos del chico de ESA prenda (sólo los que
# la usuaria completó; los vacíos toman lo elegido arriba en "Cómo es el nene/nena").
_KID_FICHA = (("quien", "kids_quien"), ("talle", "kids_talle"), ("etnia", "ap_etnia"),
              ("pelo", "ap_pelo"), ("ojos", "ap_ojos"), ("altura_kids", "kids_altura"),
              ("extra", "ap_extra"), ("peinado", "cuerpo_peinado"))


def _kid_de_ficha(it: Dict[str, Any]) -> Dict[str, str]:
    return {dst: str(it.get(k, "")).strip() for k, dst in _KID_FICHA
            if str(it.get(k, "")).strip()}


def _kids_pose(idx: int, p: Dict[str, Any]) -> str:
    poses = _kids_poses_es(p)
    return poses[int(idx) % len(poses)].format(**_kq(p))


# Pelo y altura con las palabras de un chico (el menú de kids es el mismo de adultos,
# pero con estas opciones en vez de las de mujer/hombre).
KIDS_PELO = {
    "rubio": "pelo rubio", "castano": "pelo castaño", "morocho": "pelo oscuro",
    "pelirrojo": "pelo colorado", "negro_lacio": "pelo negro lacio",
    "rulos": "pelo con rulos",
}
KIDS_ALTURA = {
    "bajo": "bajit{o} para su edad", "medio": "de estatura normal para su edad",
    "alto": "alt{o} para su edad",
}


def _kids_persona(p: Dict[str, Any]) -> str:
    q = _kq(p)
    edad = _edad_kids(p, False)
    et = APAR_ETNIA.get(str(p.get("ap_etnia", "")).lower(), "")
    _pv = str(p.get("ap_pelo", "")).lower()
    pe = KIDS_PELO.get(_pv) or APAR_PELO.get(_pv, "")
    pein = TIPO_PEINADO.get(str(p.get("cuerpo_peinado", "")).strip().lower(), "")
    if pe and pein:                     # "pelo rubio" + "pelo corto" → "pelo rubio, corto"
        pe, pein = pe + ", " + pein.replace("pelo ", "", 1), ""
    oj = APAR_OJOS.get(str(p.get("ap_ojos", "")).lower(), "")
    alt = KIDS_ALTURA.get(str(p.get("kids_altura", "")).lower(), "").format(**q)
    extra = str(p.get("ap_extra", "")).strip()
    partes = [x for x in (et, pe, pein, oj, alt, extra) if x]
    return f"{q['un']} {q['quien']} de {edad}" + "".join(f", {x}" for x in partes)


# OJO: acá NO se nombra lo que no queremos (sex, sexy, poses de
# adulto...). Los filtros de imagen leen esas palabras al lado de "una nena de 7 años"
# como si fueran el pedido, aunque estén en una prohibición, y bloquean la toma. Se
# dice en positivo lo que SÍ queremos, y con eso alcanza.
_KIDS_PROHIBIDO = (
    "Foto de catálogo de ropa de chicos, alegre y natural, como las de cualquier marca "
    "infantil: la prenda puesta completa, cara lavada sin maquillaje, sin texto, logos ni "
    "marcas de agua."
)


def _kids_toma(p: Dict[str, Any], settings: Dict[str, Any], pose_idx: int,
               cam: Optional[int], pose: str, en: bool, pose_es: str = "",
               guia: bool = False, cam_mano: bool = False) -> str:
    """Lo que el prompt de chicos no tenía y el de adultos sí: la cámara que ROTA en cada toma,
    el TAMAÑO DE PLANO (o el encuadre que eligió ella), el RINCÓN del lugar, la cámara/lente/
    película elegidas y la soltura. Con una GUÍA de pose copiada de una foto no rota nada:
    la cámara y el encuadre son los de la guía (salvo un ángulo elegido a mano).
    pose_es: la pose en castellano (los lugares de la pose se buscan en castellano)."""
    out = []
    _pz = (pose_es or pose)
    z = _zona(p)
    if z in ("abajo", "abajo_cerca", "arriba_cerca", "corto"):
        # Con chicos nada de recortes sobre una parte del cuerpo sin la cara: cuerpo entero.
        z = "entero"
    if z and not guia:
        out.append(("MANDATORY FRAMING (overrides the shot size of the pose): " + ENCUADRE_ZONA[z][1])
                   if en else ("ENCUADRE OBLIGATORIO (manda sobre el plano de la pose): "
                               + ENCUADRE_ZONA[z][0]))
    if (not guia and not _auto_off(settings)) or cam is not None:
        i = int(cam if cam is not None else pose_idx)
        out.append(_camara(i, en=en, elegida=cam_mano))
        if (not guia and not z and not _zona_de_encuadre(_pz, estricto=True)
                and not _cam_fija_plano(i, False, cam_mano)
                and not re.search(r"plano|primer plano|close-up|medium shot|full body|head to toe|"
                                  r"cabeza a los pies|de pies a cabeza", _pz, re.I)):
            out.append(_plano(i, en=en, lenceria=True))
        if not guia and _hay_lugar(p) and not _pose_dice_lugar(_pz):
            out.append(_rincon(i, True, en=en, ext=_es_exterior(p), pose=_pz, cam=i))
    cf = _bloque_camara_foto(p, en=en, pose_txt=_pz)
    if cf:
        out.append(cf)
    if not en:
        out.append("Las tomas previas del set (si vienen) sirven SÓLO para que sea el mismo chico: "
                   "esta toma va desde OTRO ángulo y en OTRO rincón del lugar, con otro fondo.")
    _mueve = (any(w in (" " + _pz.lower() + " ") for w in _MUEVE_KW)
              or str(p.get("movimiento") or "") not in ("", "auto", "ninguno"))
    if _mueve:
        out.append("CANDID, NOT POSED: a real moment of a child playing, caught mid-movement, loose "
                   "arms, natural expression, never a stiff catalog stance; real skin and fabric, no "
                   "CGI look." if en else
                   "FOTO ESPONTÁNEA, NO POSADA: un momento real de un chico jugando, captado en pleno "
                   "movimiento, brazos sueltos, expresión natural, nunca parado tieso de catálogo; piel "
                   "y tela reales, nada de look de imagen generada.")
    else:
        # Una pose quieta (sentado jugando, de pie con las manos en la cintura) no se pide
        # "en pleno movimiento": eso la contradecía.
        out.append("NATURAL, NOT STIFF: the pose as described, relaxed and real, loose hands, a "
                   "genuine expression; real skin and fabric, no CGI look." if en else
                   "NATURAL, SIN RIGIDEZ: la pose tal cual se describe, relajada y real, manos "
                   "sueltas, expresión genuina; piel y tela reales, nada de look de imagen generada.")
    txt = ("\n" if not en else " ").join(x for x in out if x)
    if _ctx("hombre"):
        txt = _masc_en(txt) if en else _masc_es(txt)
    return txt


def _edad_kids(p: Dict[str, Any], en: bool) -> str:
    """La edad del chico según el talle. La pantalla manda rangos ("6-8"); un talle suelto
    ("4", de una plantilla vieja o la API) salía siempre "7 años": un talle N es para un chico
    de unos N años."""
    t = str(p.get("kids_talle", "") or "").strip()
    tabla = KIDS_EDAD_EN if en else KIDS_EDAD
    if t in tabla:
        return tabla[t]
    try:
        n = int(float(t))
    except (TypeError, ValueError):
        return "7-year-old" if en else "7 años"
    n = max(1, min(17, n))
    return (f"{n}-year-old" if en else (f"{n} año" if n == 1 else f"{n} años"))


def _kids_fondo_def(p: Dict[str, Any], pose: str, en: bool) -> str:
    """El lugar cuando ella no escribió ninguno. Antes era SIEMPRE "el borde de una pileta":
    un pijama salía en una pileta y "saltando en la cama" también. Si la pose ya dice dónde
    está, manda la pose; si no, un lugar acorde a la prenda."""
    if _pose_dice_lugar(pose):
        return "the place described in the pose" if en else "el lugar que dice la pose"
    txt = " ".join(str(p.get(k, "")) for k in ("producto_manual", "prenda_desc", "ficha", "piezas",
                                               "tela")).lower()
    if any(w in txt for w in ("malla", "bikini", "traje de baño", "short de baño", "enteriza",
                              "swim", "rashguard", "uv")):
        return ("the edge of a swimming pool on a sunny day" if en
                else "el borde de una pileta en un día de sol")
    if any(w in txt for w in ("pijama", "piyama", "camisón", "camison", "bata", "pantufla")):
        return ("a bright, cozy child's bedroom" if en else "un cuarto infantil luminoso y cálido")
    return ("a bright, simple place that suits the garment" if en
            else "un lugar luminoso y simple, acorde a la prenda")


def _fisica_kids() -> str:
    """La física de adultos con la cámara a la altura de un chico (la de un fotógrafo parado,
    1,60 m, a un nene de 1 m lo mira desde arriba)."""
    return FISICA_BLOCK.replace("va a la altura de los ojos de un fotógrafo parado (~1,60 m)",
                                "va a la altura de los ojos del chico (el fotógrafo agachado)")


def _kids_sin_adulto(t: str, p: Dict[str, Any]) -> str:
    """Palabras de lencería/adulto que traen los bloques compartidos y no van en una foto de
    chicos (y que además hacen saltar los filtros): modelo, breteles, busto, escote."""
    for a, b in (("las manos de la modelo", "las manos"), ("la modelo", "el chico" if _ctx("hombre") else "la chica"),
                 ("breteles", "tiras"), ("bretel", "tira"), ("justo debajo del busto", "a la altura del pecho"),
                 ("del busto", "del pecho"), ("el busto", "el pecho"), ("ESCOTE", "CUELLO"), ("escote", "cuello")):
        t = t.replace(a, b)
    return _masc_es(t) if _ctx("hombre") else t


def build_prompt_kids_modelo(p: Dict[str, Any], settings: Dict[str, Any], style: str,
                             n_prod: int, pose_idx: int = 0, pose_txt: str = "",
                             cam: Optional[int] = None, guia: bool = False,
                             cam_mano: bool = False) -> str:
    """Toma de UN chico con la prenda puesta (poncho, pijama, remera, buzo, vestido).
    Va SIN la instrucción de marca de los Ajustes (habla de ropa interior y prendas
    íntimas): al lado de un chico, esa frase sola alcanza para que el filtro bloquee."""
    estilo = _style_text(style, settings)
    q = _kq(p)
    persona = _kids_persona(p)
    pose = pose_txt.strip() or _kids_pose(pose_idx, p)
    fondo = str(p.get("fondo", "")).strip() or _kids_fondo_def(p, pose, False)
    luz = str(p.get("luz", "")).strip() or "luz natural de día, alegre y pareja"
    _txt = (
        estilo + "\n\n"
        + f"FOTO DE CATÁLOGO DE ROPA INFANTIL, real y espontánea, de {persona}. Es "
          f"{q['un']} {q['quien']} INVENTAD{q['o'].upper()} por la IA — no es ninguna persona "
          f"real —, con cara de {q['chico']} alegre y natural y la edad que corresponde a "
          "ese talle, ni más grande ni más chic" + q['o'] + ".\n\n"
        + _bloque_producto_ref(n_prod, primera_idx=1).replace(" de LUMA Íntima", "") + "\n\n"
        + "LA PRENDA PUESTA: lleva puesta EXACTAMENTE la prenda de la(s) foto(s), como le "
          f"queda a un chico de esa edad, y la prenda {q['lo']} CUBRE tal como cubre en la "
          "foto real. Si la prenda es un poncho o una bata, se ve el poncho cerrado y nada "
          f"más. Está vestid{q['o']} de forma completa y normal, como en cualquier "
          "catálogo de ropa de chicos.\n\n"
        + f"POSE Y ENCUADRE (obligatorio): {pose}. {_kids_pose_frase(p)}.\n\n"
        + (f"Qué prenda es y qué más lleva: {str(p.get('piezas', '')).strip()} (lo que lleva además NO es "
           "parte del producto).\n\n"
           if str(p.get("piezas", "")).strip() else "")
        + (f"Accesorios (sólo estos): {str(p.get('cuerpo_accesorios', '')).strip()}.\n\n"
           if str(p.get("cuerpo_accesorios", "")).strip() else "")
        + f"Detalles a respetar:\n{_bloque_detalles(p)}\n\n"
        + FIDELITY_FABRIC
        + CORTES_BLOCK
        + f"\n\nEscenario: {fondo}. Luz: {luz}.\n"
        + _kids_toma(p, settings, pose_idx, cam, pose, en=False, guia=guia, cam_mano=cam_mano)
        + "\n" + CALIDAD_BLOCK
        + _fisica_kids()
        + "\n\n" + _KIDS_PROHIBIDO
        + f" Exactamente {q['un']} {q['quien']}."
    )
    return _kids_sin_adulto(_txt, p)


def build_prompt_kids_minimo(p: Dict[str, Any], n_prod: int, pose_idx: int = 0,
                             pose_txt: str = "") -> str:
    """Reintento tras un bloqueo: la misma toma con el prompt más corto y neutro posible
    (sin bloques de fidelidad ni de física, que son largos y hablan de cuerpo y escote)."""
    q = _kq(p)
    persona = _kids_persona(p)
    pose = pose_txt.strip() or _kids_pose(pose_idx, p)
    fondo = str(p.get("fondo", "")).strip() or "el borde de una pileta en un día de sol"
    rango = "la IMAGEN 1" if n_prod <= 1 else f"las IMÁGENES 1 a {n_prod}"
    return (
        f"Foto real de catálogo de ropa infantil: {persona}, inventad{q['o']} por la IA, "
        f"sonriendo, con la prenda de {rango} puesta tal cual es (mismo diseño, color, "
        f"estampa y detalles, sin inventar nada). Pose: {pose}. Escenario: {fondo}. Luz "
        "natural de día. Foto tomada con una cámara común, sin texto ni logos. "
        f"Exactamente {q['un']} {q['quien']}."
    )


_KIDS_COMPOSICIONES = [
    "corriendo en fila, riéndose, el de atrás tratando de alcanzar a los demás",
    "abrazados de los hombros, de frente a cámara, muertos de risa",
    "sentados juntos en lo que el lugar tenga (un borde, un escalón, el piso), charlando entre ellos",
    "en ronda, jugando a algo con las manos, ninguno mira a cámara",
    "saltando todos a la vez con los brazos arriba, capturados en el aire",
    "caminando juntos hacia la cámara, uno señalando algo y los demás mirando",
]


def build_prompt_kids_grupal(p: Dict[str, Any], settings: Dict[str, Any],
                             asign: List[Dict[str, Any]], style: str, n_prod: int,
                             prod_primera: int = 1,
                             prod_map: Optional[List[Optional[int]]] = None) -> str:
    """Foto grupal del set de kids: N chicos juntos, cada uno con SU prenda o SU color.
    Sin la instrucción de marca, por lo mismo que en build_prompt_kids_modelo."""
    estilo = _style_text(style, settings)
    q = _kq(p)
    a = (asign or [])[:SET_MAX_MODELOS]
    N = max(len(a), 2)
    pm = list(prod_map or []) + [None] * N
    rango = (str(prod_primera) if n_prod <= 1
             else f"{prod_primera} a {prod_primera + n_prod - 1}")
    quienes = []
    for k in range(len(a)):
        col = str(a[k].get("color", "")).strip()
        pk_ = {**p, **_kid_de_ficha(a[k])}      # nena/nene, edad, pelo... de ESTA ficha
        base_k = f"{_kq(pk_)['chico'].upper()} {_LETRAS[k]}: {_kids_persona(pk_)}"
        if pm[k] is not None:
            quienes.append(f"{base_k}, lleva puesta la prenda de la IMAGEN {pm[k]}"
                           + (f" (color {col})" if col else "")
                           + " — es SU prenda y solo suya")
        else:
            quienes.append(f"{base_k}, lleva puesta la prenda de la(s) IMAGEN(es) {rango}"
                           + (f" en color {col}" if col else ""))
    comp = random.choice(_KIDS_COMPOSICIONES)
    fondo = str(p.get("fondo", "")).strip() or _kids_fondo_def(p, "", False)
    luz = str(p.get("luz", "")).strip() or "luz natural de día, alegre y pareja"
    # Cámara, lente y película también en la grupal (antes no llevaba ninguna de las elegidas).
    _cf = _bloque_camara_foto(p, pose_txt=comp)
    _cm = cam_idx(p.get("_camara_mano"))
    _txt = (
        estilo + "\n\n"
        + f"FOTO DE CATÁLOGO DE ROPA INFANTIL con {_n_txt(N)} {q['chicos']} junt{q['o']}s, "
          f"tod{q['o']}s INVENTAD{q['o'].upper()}S por la IA (ningun{q['o']} es una persona real), "
          f"distint{q['o']}s entre sí (no gemel{q['o']}s), cada un{q['o']} con su cara y su pelo:\n"
        + "\n".join(quienes) + "\n\n"
        + _bloque_producto_ref(n_prod, primera_idx=prod_primera).replace(" de LUMA Íntima", "")
        + "\n\n"
        + "LAS PRENDAS PUESTAS: cada uno lleva SU prenda EXACTA, que lo cubre tal como cubre "
          "en la foto real. Todos vestidos de forma completa y normal. "
          + ("Son prendas DISTINTAS entre sí: no mezcles los diseños ni le pongas a uno la "
             "prenda de otro. " if any(x is not None for x in pm) else
             "Es la MISMA prenda en distintos colores: mismo diseño y calce en todos. ")
        + f"\n\nCOMPOSICIÓN: {comp}. Momento real de juego, espontáneo; alturas y gestos "
          "distintos, cada uno a lo suyo.\n\n"
        + f"Detalles a respetar:\n{_bloque_detalles(p)}\n\n"
        + FIDELITY_FABRIC
        + f"\n\nEscenario: {fondo}. Luz: {luz}.\n"
        + ((_camara(_cm, elegida=True) + "\n") if _cm is not None else "")
        + ((_cf + "\n") if _cf else "")
        + (("ENCUADRE OBLIGATORIO: " + ENCUADRE_ZONA[_zona(p)][0] + "\n") if _zona(p) else "")
        + "\n" + CALIDAD_BLOCK
        + _fisica_kids()
        + "\n\n" + _KIDS_PROHIBIDO
        + f" Exactamente {_n_txt(N)} {q['chicos']}."
    )
    return _kids_sin_adulto(_txt, p)


def build_prompt_product_only(p: Dict[str, Any], settings: Dict[str, Any],
                              modo: str, paneles: int, aspect: str,
                              n_prod: int = 1) -> str:
    sysi = settings.get("system_instruction", "").strip()
    modo_txt = PRESENTACION_MODOS.get(modo, PRESENTACION_MODOS["flat_lay"])
    prod_ref = _bloque_producto_ref(n_prod, primera_idx=1, con_persona=False)
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
    _fondo_user = str(p.get("fondo", "") or "fondo liso y claro de estudio")
    if _cenital:
        # La regla cenital va PRIMERA y el fondo se traduce a SUPERFICIE: si queda la
        # línea "Fondo: playa con palmeras y mar", el modelo pinta el paisaje en
        # perspectiva y la prenda queda como recorte pegado encima.
        encabezado = (
            "FOTOGRAFÍA CENITAL DE PRODUCTO (REGLA número 1, por encima de todo): la cámara "
            "apunta PERPENDICULAR al suelo, como un dron mirando para abajo o un flat-lay. "
            "El encuadre COMPLETO, de borde a borde, es la SUPERFICIE vista desde arriba. "
            "PROHIBIDO: horizonte, cielo, mar u olas 'de costado', montañas, paisaje en "
            "perspectiva o cualquier vista frontal. Si el escenario pedido es una playa, lo "
            "que se ve es SOLO LA ARENA desde arriba llenando todo el cuadro (a lo sumo "
            "caracolas, hojas o el borde de una toalla APOYADOS en la arena); si es un "
            "living, solo la alfombra o el piso; si es una cama, solo la sábana. Mezclar la "
            "prenda vista desde arriba con un paisaje de frente queda como una figurita "
            "recortada y pegada: INACEPTABLE.\n\n"
            "Fotografía de producto de e-commerce, fotorrealista. SIN PERSONAS.\n\n")
        fondo_linea = (f"Superficie (todo el cuadro, vista desde arriba): el suelo de este "
                       f"lugar — {_fondo_user} — y nada más (sin horizonte, sin cielo, sin "
                       f"paisaje).")
    else:
        encabezado = ("Fotografía de producto de e-commerce, calidad de estudio, "
                      "fotorrealista. SIN PERSONAS.\n\n")
        fondo_linea = f"Fondo: {_fondo_user}"
    return (
        (sysi + "\n\n" if sysi else "")
        + encabezado
        + prod_ref + "\n\n"
        f"Presentación: {modo_txt}\n\n"
        + ((_bloque_kids(p) + "\n\n") if _es_kids(p) else "")
        + f"Detalles a respetar:\n{_bloque_detalles(p)}\n\n"
        + FIDELITY_FABRIC + "\n\n"
        + fondo_linea + "\n"
        f"Iluminación: {str(p.get('luz') or '').strip() or 'luz de estudio pareja y suave'}\n"
        + extra_panel
        + "\n\nINTEGRACIÓN REAL CON LA ESCENA (nada de 'figurita recortada'): la prenda está "
        "FÍSICAMENTE en la escena — apoya con contacto real sobre la superficie, con "
        "micro-sombras de contacto en los bordes, la superficie se hunde o deforma "
        "levemente bajo la tela, y la prenda recibe la MISMA luz, dirección de sombras, "
        "perspectiva y balance de color que todo el entorno. "
        + ("Si la escena lo amerita, partículas del entorno sobre la tela (granitos de arena, "
           "pelusa) suman realismo. " if str(p.get("fondo") or "").strip() else "")
        + "PROHIBIDO que la prenda parezca recortada y pegada sobre un fondo."
        + ("\nRECORDATORIO FINAL (cenital): todo el cuadro es la superficie desde arriba; "
           "cero horizonte, cero cielo, cero mar de costado." if _cenital else "")
        + "\n\nSin ninguna persona, maniquí visible ni parte del cuerpo en la imagen: SOLO la "
        "prenda. Sin texto, logos ni marca de agua."
    )


def build_prompt_recolor(p: Dict[str, Any], settings: Dict[str, Any], modo: str,
                         target_color: str, aspect: str, n_prod: int = 1) -> str:
    """Reproduce la MISMA prenda cambiando ÚNICAMENTE el color."""
    sysi = settings.get("system_instruction", "").strip()
    modo_txt = PRESENTACION_MODOS.get(modo, PRESENTACION_MODOS["suspendida"])
    prod_ref = _bloque_producto_ref(n_prod, primera_idx=1, con_persona=False)
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
        f"Iluminación: {str(p.get('luz') or '').strip() or 'luz de estudio pareja y suave'}\n\n"
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

        # Modelo que esta cuenta o este método todavía no tiene (404, o 400 "not found /
        # not supported"): se repite con Nano Banana 2 en vez de fallar.
        if (modelo != MODEL_RESPALDO and r.status_code in (400, 404)
                and any(w in r.text.lower() for w in ("not found", "not supported", "is not supported",
                                                       "unsupported", "unknown model"))):
            print(f"[gemini] {modelo} no respondió ({r.status_code}); va con {MODEL_RESPALDO}")
            modelo = MODEL_RESPALDO
            endpoint = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                        f"{modelo}:generateContent")
            r = await cli.post(endpoint, json=body, headers=headers)

    if r.status_code != 200:
        detail = r.text[:500]
        raise HTTPException(r.status_code, f"Nano Banana ({modelo}) devolvió error: {detail}")

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


# Palabras que la moderación de fal lee como riesgo (en inglés Y castellano): ante un
# bloqueo se reintenta UNA vez con el prompt saneado antes de caer al modelo de respaldo.
_FAL_RIESGO = [
    (r"\bprovocativ\w*\b", ""), (r"\bsensual\w*\b", ""), (r"\bsedu\w*\b", ""),
    (r"\bsexy\b", ""), (r"\ber[oó]tic\w*\b", ""), (r"\binsinuant\w*\b", ""),
    (r"\bsugerente\w*\b", ""), (r"\batrevid\w*\b", ""), (r"\bhot\b", ""),
    # Marcas de revistas para adultos en la pose escrita ("modelo Playboy de los 70"):
    # el checker las lee como pedido de desnudo aunque la prenda esté puesta.
    (r"\bplayboy\b", "1970s glamour magazine"), (r"\bpenthouse\b", "glamour magazine"),
    (r"\bhustler\b", "glamour magazine"), (r"\bplaymate\b", "glamour model"),
    (r"\bconejita\w*\b", "modelo de revista"), (r"\bonlyfans\b", "social media"),
    (r"\bthong back\b", "back straps"), (r"\bthong\b", "matching bottom"),
    (r"\bg-string\b", "matching bottom"), (r"\bhilo dental\b", "bombacha"),
    (r"\bcolaless\b", "bombacha"), (r"NOT explicit, NOT sexual, NOT nude",
                                    "modest and professional"),
    # Superlativos del cuerpo: al checker le pesan más que a la foto.
    (r"extra grande y voluminoso \(talle grande, tipo copa DD/E o mayor\), proporcionado y natural",
     "grande, talle grande"),
    (r"glúteos y caderas extra grandes y anchas \(talle grande\), volumen marcado y natural",
     "caderas anchas, talle grande"),
    (r"\bvoluminos\w*\b", ""), (r"\bvolumen marcado\b", "natural"),
]


def _sanear_prompt_fal(t: str) -> str:
    out = t or ""
    for pat, rep in _FAL_RIESGO:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip()


async def fal_generate(parts: List[Dict[str, Any]], settings: Dict[str, Any],
                       aspect: str, image_size: str, model_slug: str,
                       _deadline: Optional[float] = None) -> bytes:
    # PRESUPUESTO DE TIEMPO GLOBAL: fal_generate se llama a sí misma (prompt saneado →
    # modelo de respaldo) y cada nivel tenía su propio tope, así que 3 niveles podían
    # sumar más de media hora con la pantalla girando. El deadline se hereda: pase lo
    # que pase, el pedido entero corta a los 7 minutos.
    if _deadline is None:
        _deadline = time.time() + 420
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
    # FLUX.2 [dev] edit acepta 4 como mucho: la persona + 3 vistas de la prenda.
    _slug_low = model_slug.lower()
    # Qwen Image 3 / 2.0 aceptan de 1 a 3 referencias: van las primeras (la persona
    # primero, después la prenda), y el resto se queda afuera.
    if "qwen-image-3" in _slug_low or "qwen-image-2" in _slug_low:
        raws = raws[:3]
    elif "flux" in _slug_low:
        raws = raws[:4]
    else:
        raws = raws[:8]
    image_urls = []
    for i, b in enumerate(raws):
        md = 1600 if i == 0 else 1280
        try:
            chico = _compress_ref(base64.b64decode(b), max_dim=md, q=90)
        except Exception:
            chico = b
        image_urls.append(f"data:image/jpeg;base64,{chico}")
    # El tamaño de salida sigue a la calidad pedida: "1K" son ~2,1 MP (1072x1920 en 9:16,
    # lo que necesita un reel), "2K" y "4K" ~3,4 MP (el tope de Seedream). Antes iba
    # siempre a 3,4 MP: un 60% más de píxeles que se pagan y se esperan.
    w, h = _flux_dims(aspect, 2_100_000 if str(image_size).upper() == "1K" else 3_400_000)
    body: Dict[str, Any] = {
        "prompt": prompt,
        "image_urls": image_urls,
        "num_images": 1,
        "output_format": "jpeg",
        "sync_mode": True,
        "image_size": {"width": w, "height": h},
        "enable_safety_checker": False,
    }
    # safety_tolerance y guidance_scale son parámetros de FLUX/BFL: Seedream NO los tiene
    # en su esquema y fal responde "Error validating the input" (422) — tardando ~70s,
    # porque valida DESPUÉS de subir las imágenes. Solo se mandan a modelos FLUX.
    # safety_tolerance y guidance_scale son de FLUX (BFL): Seedream y Qwen NO los
    # tienen en su esquema y fal responde 422 "Error validating the input" (a los ~70 s,
    # porque valida DESPUÉS de subir las imágenes). Sólo van a modelos FLUX.
    if "flux" in _slug_low:
        body["safety_tolerance"] = "6"
        # guidance más alto = FLUX obedece MÁS al prompt (pose, ambiente, pedidos).
        body["guidance_scale"] = float(settings.get("flux_guidance", 5.0) or 5.0)
    _preset = {"4:5": "portrait_4_3", "3:4": "portrait_4_3", "2:3": "portrait_4_3",
               "9:16": "portrait_16_9", "1:1": "square_hd", "4:3": "landscape_4_3",
               "5:4": "landscape_4_3", "3:2": "landscape_4_3", "16:9": "landscape_16_9",
               "21:9": "landscape_16_9"}.get(aspect, "portrait_4_3")
    _size_fallbacks = [{"image_size": _preset}, {"aspect_ratio": aspect}]
    if "qwen-image" in _slug_low:
        # Qwen Image 3 / 2.0 (Alibaba en fal): acepta el image_size ancho/alto (medido en
        # el panel de fal: 1152x2048). Su LLM reescribe el prompt si no se lo apaga: con
        # eso la cara y la prenda exacta llegaban cambiadas.
        body["enable_prompt_expansion"] = False
        body["negative_prompt"] = ""
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
                  "enable_safety_checker", "safety_tolerance", "guidance_scale",
                  "enable_prompt_expansion", "negative_prompt"]
    # Tope de espera por intento: 300 s (Qwen Image 3 tardó 160-190 s en el panel de fal;
    # con 150 s se cortaba del lado nuestro y el pedido seguía corriendo allá, pago). El
    # corte duro total sigue en los 7 minutos del _deadline.
    _resta = max(10.0, _deadline - time.time())
    async with httpx.AsyncClient(timeout=min(300.0, _resta)) as cli:
        r = None
        for intento in range(4):
            if time.time() >= _deadline:         # corte duro compartido por TODOS los niveles
                raise HTTPException(504, "FLUX: fal.ai tardó demasiado en responder (más de "
                                         "7 minutos). Probá de nuevo en un rato o generá "
                                         "esta toma con Nano Banana.")
            try:
                r = await cli.post(endpoint, json=body, headers=headers,
                                   timeout=min(300.0, max(10.0, _deadline - time.time())))
            except httpx.TimeoutException:
                raise HTTPException(504, "FLUX: fal.ai no respondió a tiempo. Su servicio "
                                         "puede estar saturado: probá de nuevo o generá "
                                         "con Nano Banana mientras tanto.")
            if r.status_code != 422:
                break
            low = r.text.lower()
            # Rechazo del CHECKER de contenido: no es un campo inválido, así que sacar
            # campos y reintentar el mismo prompt era quemar 4 x 60 s para nada (en el
            # panel de fal se veía una seguidilla de fallos de ~50 s). Se corta acá y
            # pasa directo a la lógica de rechazo (pose segura → saneado → respaldo).
            _es_checker = any(t in low for t in ("flagged", "content checker", "nsfw",
                                                 "content policy", "content_policy"))
            # Se GUARDA el motivo exacto que devuelve fal: en su panel el detalle se
            # borra a las horas, pero acá lo tenemos y aparece en "Ver diagnóstico".
            try:
                await kv.set(_pfx() + "lastfalerror",
                             {"modelo": model_slug, "http": r.status_code,
                              "detalle": r.text[:1500], "intento": intento + 1,
                              "n_imagenes": len(body.get("image_urls") or []),
                              "campos": sorted(body.keys()), "ts": int(time.time())},
                             ttl=7200)
            except Exception:
                pass
            if _es_checker:
                break
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
                        if (k == "enable_safety_checker"
                                and not any(w in low for w in ("extra", "not permitted",
                                                               "unexpected", "not allowed"))):
                            continue      # que lo nombre no quiere decir que sobre: no se saca
                        body.pop(k)
                        opcionales.remove(k)
                        quitado = True
                        break
            if not quitado:
                # 422 SIN pista de qué campo falla (ej: "Error validating the input", que
                # tarda ~68s en volver). Antes se probaba sacando campos opcionales a
                # ciegas: 8 intentos inútiles. Ahora se ataca lo más probable —
                # demasiadas fotos de referencia o salida muy grande — y si eso no
                # alcanza, se corta en vez de seguir quemando minutos.
                # Orden de sospechosos: primero los campos que más varían entre modelos
                # de fal, después el tamaño de salida, y al final menos fotos.
                # Quien llama con "_fal_sin_adivinar" (Cambio de conjunto) sabe que el
                # pedido es válido: a los ~50 s ese 422 es el filtro de SALIDA de Seedream,
                # y probar sacando campos eran 4 vueltas de ~1 minuto para nada.
                if settings.get("_fal_sin_adivinar"):
                    break
                # OJO: "enable_safety_checker" NUNCA se saca a ciegas. Sacarlo no lo apaga: lo
                # PRENDE (es lo que fal hace por defecto), y en el reintento el checker
                # rechazaba nuestras propias fotos de referencia ("image_urls 0 … content
                # checker"). Sólo se va si fal dice explícitamente que ese campo no existe.
                if "sync_mode" in body:
                    body.pop("sync_mode", None)
                    quitado = True
                elif isinstance(body.get("image_size"), dict):
                    body.pop("image_size", None)
                    body["image_size"] = _preset
                    quitado = True
                elif len(body.get("image_urls") or []) > 3:
                    body["image_urls"] = body["image_urls"][:3]
                    quitado = True
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
            _rechazo_checker = any(t in low for t in ("nsfw", "safety", "content policy",
                                                      "flagged", "content_policy"))
            # OJO: el rechazo del checker de Seedream viene como 422 con "Error validating
            # the input" Y "flagged by a content checker" en el mismo cuerpo. Si se mira
            # primero "validating the input", el pedido muere acá sin pasar por la pose
            # segura ni el saneado (así pasó en producción el 14/9).
            if r.status_code == 422 and "validating the input" in low and not _rechazo_checker:
                raise HTTPException(422, "FLUX: fal rechazó el pedido por un valor inválido "
                                         "('Error validating the input'), aun mandando menos "
                                         "fotos y salida más chica. El detalle exacto quedó "
                                         "guardado en 'Ver diagnóstico'. Mientras tanto, "
                                         f"generá esta toma con Nano Banana. {r.text[:200]}")
            if _rechazo_checker:
                # 0) Si la toma lleva una pose del pool con versión de catálogo, se
                # reintenta con ESA pose (el checker de salida rebota la pose, no la prenda).
                if not settings.get("_fal_pose_segura") and "seedream" in _slug_low:
                    seguro = _pose_segura_prompt(prompt)
                    if seguro:
                        s2 = dict(settings)
                        s2["_fal_pose_segura"] = True
                        parts2 = ([{"text": seguro}]
                                  + [pt for pt in parts
                                     if pt.get("inlineData") or pt.get("inline_data")])
                        return await fal_generate(parts2, s2, aspect, image_size, model_slug,
                                                  _deadline=_deadline)
                # 1er intento tras bloqueo: MISMO modelo con el prompt SANEADO (sin las
                # palabras que la moderación lee como riesgo).
                if not settings.get("_fal_saneado"):
                    limpio = _sanear_prompt_fal(prompt)
                    if limpio and limpio != prompt:
                        s2 = dict(settings)
                        s2["_fal_saneado"] = True
                        parts2 = ([{"text": limpio}]
                                  + [pt for pt in parts
                                     if pt.get("inlineData") or pt.get("inline_data")])
                        return await fal_generate(parts2, s2, aspect, image_size, model_slug,
                                                  _deadline=_deadline)
                fb = str(settings.get("flux_fallback_model") or "").strip()
                if fb and fb != model_slug:
                    # 2do intento: el modelo de respaldo sin checker (Qwen Image 3).
                    s2 = dict(settings)
                    s2["flux_fallback_model"] = ""
                    return await fal_generate(parts, s2, aspect, image_size, fb,
                                              _deadline=_deadline)
                raise HTTPException(422, "FLUX bloqueó la imagen por su filtro de contenido "
                                         "(incluso saneando el prompt y con el modelo de "
                                         "respaldo). Probá reformular el pedido sin palabras "
                                         f"de connotación. Detalle: {r.text[:200]}")
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
    "shop): elegant, classy and modest, professional retail photography; the adult model "
    "wears the full matching set (top and bottom), well fitted and fully covered, exactly "
    "like a mainstream underwear store shows its products."
)


# ── SKILLS por categoría: dirección de arte basada en cómo shootean las marcas reales ──
# (lencería tipo Intimissimi/Aerie · baño tipo campaña resort · pijama tipo loungewear cozy)
def _categoria(p: Dict[str, Any]) -> str:
    # Kids manda por encima de todo: una malla de nena no se fotografía con el
    # estilo de una de mujer, y además nunca lleva persona.
    if _es_kids(p):
        return "kids"
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
                      sin_pose: bool = False, con_lugar: bool = True) -> str:
    """sin_pose=True: hay una POSE OBLIGATORIA pedida → el bloque de estilo NO sugiere
    ninguna pose (antes su 'three-quarter turn showing front' peleaba contra la pose
    de espalda pedida y Seedream terminaba haciendo el frente)."""
    h = _es_hombre(genero)
    modelo = "the male model" if h else "the model"
    if cat == "kids":
        # Sin pose ni modelo: en kids la toma es SIEMPRE de la prenda sola.
        return ("Style: children's swimwear e-commerce catalog: bright even daylight, clean "
                "and cheerful, crisp colours, simple uncluttered surface, the garment "
                "photographed on its own with no person and no visible mannequin, playful "
                "but tidy product styling.")
    if cat == "lenceria":
        pose = ("confident relaxed stance, weight on one leg, gentle three-quarter turn showing "
                "front and side of the set, one hand at the hip or in the hair" if not h else
                "confident relaxed stance, weight on one leg, subtle three-quarter turn, calm "
                "assured expression")
        _p = "" if sin_pose else f" {modelo} in a {pose},"
        # Con un lugar pedido, el estilo NO trae su propio fondo ni su luz: ganaba la playa o
        # el dormitorio del estilo sobre el desierto o el living que ella escribió.
        if not con_lugar:
            return f"Style: modern lingerie catalog (Intimissimi / Aerie),{_p} body-positive elegant mood."
        return (f"Style: modern lingerie catalog (Intimissimi / Aerie): soft even daylight that "
                f"reveals the lace texture, clean neutral or soft bedroom backdrop,{_p} "
                f"body-positive elegant mood.")
    if cat == "bano":
        _p = "" if sin_pose else (f" {modelo} in a relaxed confident pose with natural "
                                  f"movement,")
        if not con_lugar:
            return f"Style: swimwear resort campaign;{_p} aspirational vacation mood, sun-kissed skin."
        return (f"Style: swimwear resort campaign, outdoors on a sunny beach with sea softly "
                f"blurred behind (or clean poolside), warm golden sunlight, gentle breeze;"
                f"{_p} aspirational vacation mood, sun-kissed skin.")
    if cat == "pijama":
        if not con_lugar:
            return (f"Style: cozy loungewear/sleepwear lifestyle; {modelo} in relaxed candid "
                    f"at-home poses, warm inviting mood.")
        return (f"Style: cozy loungewear/sleepwear lifestyle, warm bright bedroom or living room "
                f"with soft window light, a bed or sofa as gentle context; {modelo} in relaxed "
                f"candid at-home poses, warm inviting mood.")
    return ""


# Rótulo en inglés de cada foto del producto para los editores de fal (que no aceptan
# texto entre imagen e imagen: la única forma de decirles qué es cada foto es por número).
_ETIQUETAS_PRENDA_EN = {
    "frente": "FRONT view",
    "espalda": "BACK view (only for the back)",
    "perfil": "SIDE view (same design in perspective)",
    "detalle": "CLOSE-UP of the fabric or a trim",
    "colgada": "the garment hanging or laid flat, no body",
    "arriba": "the TOP piece of the set",
    "abajo": "the BOTTOM piece of the set",
    "complemento": "a complementary piece of the set (robe, kimono, accessory)",
    "color": "COLOUR ONLY (take just its colour; the design comes from the other photos)",
}


def _tag_en(tag: str) -> str:
    """Traduce la etiqueta de una foto (clave o texto ya rotulado) al inglés del prompt."""
    t = str(tag or "").strip()
    if not t:
        return ""
    for k, v in _ETIQUETAS_PRENDA.items():
        if t.lower() == k or t == v:
            return _ETIQUETAS_PRENDA_EN[k]
    if _es_tag_espalda(t):
        return _ETIQUETAS_PRENDA_EN["espalda"]
    return f'"{t}" (as labelled by the user)'


def _bloque_vistas_flux(n_prod: int, primera: int, prod_tags: Optional[List[str]],
                        n_back_last: int) -> str:
    """Explica a los editores de fal que las fotos del producto son VISTAS de UN mismo
    conjunto (no productos distintos) y cuál es cada una. Sin esto, Seedream/Qwen/FLUX
    suman las vistas — frente + espalda — y la modelo sale con el doble de breteles y
    un 'producto fantasma' encima del real."""
    if n_prod <= 0:
        return ""
    if n_prod == 1:
        return (f"Image {primera} is the ONLY product photo: it shows the one real garment "
                "(or set) to copy.")
    ult = primera + n_prod - 1
    tags = list(prod_tags or []) + [""] * n_prod
    desc = []
    for j in range(n_prod):
        t = _tag_en(tags[j])
        if not t and n_back_last and j >= n_prod - n_back_last:
            t = _ETIQUETAS_PRENDA_EN["espalda"]
        desc.append(f"image {primera + j} = {t or 'another view of the same set'}")
    # Corto a propósito (antes ~900 caracteres): comía el tope de Seedream y se cortaban
    # las aclaraciones de ella. Las reglas son las mismas.
    return (f"Product photos {primera}-{ult} are VIEWS of ONE same set, not different "
            "products: " + "; ".join(desc) + ". Draw each strap, band, ring or panel ONCE "
            "(never stack views, no ghost second straps or harness); the number of straps "
            "and how they run is EXACTLY as in the front photo.")


def _camara_flux(payload: Dict[str, Any], params: Dict[str, Any],
                 pool_idx: Optional[int], settings: Optional[Dict[str, Any]] = None) -> str:
    """El renglón de cámara en inglés para Seedream/FLUX. Si la usuaria eligió el ángulo
    vale aunque la pose la haya escrito ella; si no, rota con la pose."""
    elegida = cam_idx(payload.get("camara"))
    if elegida is not None:
        return _camara(elegida, False, en=True, elegida=True)
    if _auto_off(settings):
        return ""
    if pool_idx in (3, 10):
        return "CAMERA (back shot): chest height, about 2 m behind her."
    # La toma la escribió ella y no eligió ángulo: su texto decide también dónde está la
    # cámara. El ángulo que rotaba solo ("cámara baja a la altura de la cintura, piernas
    # largas") le peleaba a "primer plano de la cara" y salía cualquier cosa.
    if pool_idx is None and str(params.get("pose", "")).strip():
        return ""
    auto = cam_idx(payload.get("camara_auto"))
    if auto is None:
        auto = pool_idx
    if auto is None:
        return ""
    return _camara(auto, _es_ropa_interior(params), en=True)


def _plano_flux(payload: Dict[str, Any], params: Dict[str, Any],
                pool_idx: Optional[int], settings: Optional[Dict[str, Any]] = None) -> str:
    """El tamaño de plano en inglés. No se pone si manda el encuadre por zona, si la pose la
    escribió ella (su texto decide), si la pose YA es un encuadre, o si la cámara elegida ya
    fija el tamaño."""
    if _zona(params) or pool_idx is None or _auto_off(settings):
        return ""
    if pool_idx in (3, 10) and cam_idx(payload.get("camara")) is None:
        return ("" if pool_idx == 10 else
                "SHOT SIZE (back shot): medium-long shot from the head to mid-thigh; her BACK "
                "fills a good part of the frame so every back strap, closure and trim reads.")
    idx = cam_idx(payload.get("camara_auto"))
    if idx is None:
        idx = pool_idx
    _ic = cam_idx(payload.get("camara"))
    if _ic is None:
        _ic = idx
    _lc = _es_ropa_interior(params)
    return (_plano(idx, en=True, lenceria=_lc)
            if _plano_aparte(pool_idx, _ic, False, _lc,
                             cam_idx(payload.get("camara")) is not None) else "")


def _rincon_flux(payload: Dict[str, Any], params: Dict[str, Any],
                 pool_idx: Optional[int], settings: Optional[Dict[str, Any]] = None,
                 pose: str = "") -> str:
    """El renglón de "en qué parte del lugar" en inglés. Sigue la POSICIÓN de la toma en el
    set, no el ángulo elegido: así la misma cámara puede caer en rincones distintos."""
    if not _hay_lugar(params) or _auto_off(settings):
        return ""
    if pool_idx in (3, 10):
        return ""        # toma de espalda: nada de "el punto más lejano del lugar"
    # Su pose ya dice dónde está → no se le contradice con otro rincón.
    if _pose_dice_lugar(str(params.get("pose", ""))):
        return ""
    idx = cam_idx(payload.get("camara_auto"))
    if idx is None:
        idx = pool_idx
    _cm = cam_idx(payload.get("camara"))
    return "" if idx is None else _rincon(idx, True, en=True, ext=_es_exterior(params),
                                          pose=pose, cam=(_cm if _cm is not None else idx))


# Colores en castellano → inglés para Seedream (un "verde" suelto en un pedido en inglés
# perdía siempre contra el color de la foto de referencia).
_COLORES_EN = {
    "verde agua": "mint green (aqua green)", "verde menta": "mint green", "verde": "green",
    "verde oliva": "olive green", "verde militar": "military olive green",
    "lila": "lilac (soft lavender purple)", "lavanda": "lavender", "violeta": "violet",
    "rosa chicle": "bubblegum pink", "rosa viejo": "dusty rose", "rosa": "pink",
    "fucsia": "fuchsia", "rojo": "red", "bordo": "burgundy", "borravino": "burgundy",
    "celeste": "light sky blue", "azul marino": "navy blue", "azul": "blue",
    "turquesa": "turquoise", "amarillo": "yellow", "naranja": "orange", "coral": "coral",
    "negro": "black", "blanco": "white", "crema": "cream", "beige": "beige", "nude": "nude",
    "gris": "grey", "marrón": "brown", "marron": "brown", "chocolate": "chocolate brown",
    "camel": "camel", "dorado": "gold", "plateado": "silver", "salmón": "salmon",
    "salmon": "salmon", "durazno": "peach", "terracota": "terracotta",
}


def _color_en(col: str) -> str:
    c = (col or "").strip().lower()
    for k in sorted(_COLORES_EN, key=len, reverse=True):
        if c == k or c.startswith(k + " ") or c.endswith(" " + k):
            return _COLORES_EN[k] + (f' ("{col}")' if c != k else "")
    return col


def _espejo_pedido(p: Dict[str, Any], pose_txt: str = "") -> bool:
    t = " ".join(str(p.get(k, "")) for k in ("fondo", "aclaraciones", "pose")) + " " + (pose_txt or "")
    t = t.lower()
    return any(w in t for w in ("espejo", "mirror", "reflejo", "reflection"))


_VE_ESPALDA_KW = ("acostada", "acostado", "boca abajo", "tumbada", "tumbado", "recostada",
                  "recostado", "tirada", "tirado", "perfil", "de costado", "de lado",
                  "lying", "prone", "on her stomach", "profile", "side view", "reclining")


# Una toma DE ESPALDA (la parte de atrás de la prenda hacia la cámara). Antes bastaba la
# palabra "espalda": las poses copiadas de una foto dicen "espalda arqueada", "apoya la
# espalda en el respaldo", "espalda recta"… y una toma de FRENTE se trataba como de espalda
# (la foto de espalda primero, las reglas de espalda, y el frente salía mal).
_RE_TOMA_ESPALDA = re.compile(
    r"\bde\s+espaldas?\b|\bvista\s+(?:de\s+espalda|trasera)\b|\bdetalle\s+de\s+(?:la\s+)?espalda\b"
    r"|\b(?:desde|por)\s+(?:atr[aá]s|detr[aá]s)\b|\bde\s+atr[aá]s\b"
    r"|^\W*espaldas?\b|\b(?:mostrando|muestra|se\s+ve)\s+(?:la\s+|su\s+)?espalda\b|\bparte\s+de\s+atr[aá]s\b"
    r"|\bbehind\b|\bfrom\s+the\s+back\b|\bback\s+(?:view|detail|shot)\b|\bespalda\s+(?:a|hacia)\s+(?:la\s+)?c[aá]mara\b")


def _toma_atras_pose(pose_txt: str) -> bool:
    t = (pose_txt or "").lower()
    t = re.sub(r"\b(?:luz|contraluz|iluminad[ao]|ilumina)\b[^.,;]{0,25}", " ", t)   # "luz desde atrás" no es la toma
    return bool(_RE_TOMA_ESPALDA.search(t))


def _ve_espalda(pose_txt: str, pool_idx: Optional[int] = None) -> bool:
    """Poses que no son "de espalda" pero en las que la espalda de la prenda se ve
    (acostada boca abajo, de perfil, de costado). Sin esto se mandaban sólo las fotos de
    frente y Seedream inventaba la espalda copiando el frente (encaje donde no hay)."""
    t = (pose_txt or "").lower()
    return pool_idx in (6, 12) or any(w in t for w in _VE_ESPALDA_KW)


def _elegir_fotos_flux(tags: List[str], n_back_last: int, cupo: int,
                       espalda: bool, mixta: bool = False) -> Tuple[List[int], int]:
    """Qué fotos del producto entran a Seedream cuando son más que su cupo. Antes iban
    las primeras N y la de ESPALDA (siempre última) era la primera en caerse: la toma de
    espalda salía inventada. Ahora: la primera de frente, la de COLOR y, si la toma es de
    espalda, la(s) de espalda van siempre; después el resto en orden. Devuelve los
    índices (las de espalda al final, así se rotulan) y cuántas de espalda quedaron. En la
    toma de espalda las de espalda van PRIMERAS y el número vuelve NEGATIVO (-cuántas)."""
    n = len(tags)
    es_back = [bool((n_back_last and j >= n - n_back_last) or _es_tag_espalda(tags[j])) for j in range(n)]
    es_color = [tags[j] == _ETIQUETAS_PRENDA["color"] or tags[j] == "color" for j in range(n)]
    if espalda and any(es_back):
        # TOMA DE ESPALDA: la foto de espalda es LA referencia y va PRIMERA (Seedream le hace
        # más caso a las primeras imágenes; última, copiaba el frente). Del frente va una
        # sola (tela y color) y la de color; las demás de frente compiten y se quedan afuera.
        atras = [j for j in range(n) if es_back[j]][:2]
        color = [j for j in range(n) if es_color[j]][:1]
        fr = [j for j in range(n) if not es_back[j] and not es_color[j]][:1]
        return (atras + fr + color)[:cupo], -len(atras)
    if n <= cupo:
        orden = list(range(n))
    else:
        prio = []
        fr = [j for j in range(n) if not es_back[j]]
        if fr:
            prio.append(fr[0])
        if mixta:
            # se ve la espalda: una de espalda entra sí o sí
            prio += [j for j in range(n) if es_back[j] and j not in prio][:1]
        prio += [j for j in range(n) if es_color[j] and j not in prio]
        if espalda:
            prio += [j for j in range(n) if es_back[j] and j not in prio][:2]
        prio += [j for j in range(n) if j not in prio and not es_back[j]]
        prio += [j for j in range(n) if j not in prio]
        orden = sorted(prio[:cupo])
    frente = [j for j in orden if not es_back[j]]
    atras = [j for j in orden if es_back[j]]
    return frente + atras, len(atras)


# ── FUNCIÓN CÁMARA: la óptica, la cámara de fotos y el movimiento, a elección. Cada opción
# tiene su texto para Nano Banana (es) y para Seedream (en). Vacío = como siempre.
LENTES_FOTO: Dict[str, Dict[str, str]] = {
    'gran_angular': {"lbl": 'Gran angular 24 mm (se ve mucho lugar)',
        "es": 'lente gran angular de 24 mm: campo amplio, se ve mucho del lugar, perspectiva dinámica; ella lejos de los bordes del cuadro para que su cuerpo no se deforme',
        "en": '24mm wide-angle lens: wide field of view showing lots of the location, dynamic perspective; keep her away from the frame edges so her body is not distorted'},
    'documental_35': {"lbl": '35 mm documental / de calle',
        "es": 'lente de 35 mm a f/2.8, mirada documental de foto de calle: natural, cercana, el lugar se lee',
        "en": '35mm lens at f/2.8, documentary street-photo look: natural and close, the place still readable'},
    'normal_50': {"lbl": '50 mm normal (como el ojo)',
        "es": 'lente normal de 50 mm a f/2: perspectiva natural como la del ojo, fondo apenas desenfocado',
        "en": '50mm normal lens at f/2: natural eye-like perspective, background slightly soft'},
    'retrato_85': {"lbl": '85 mm retrato · fondo bien desenfocado',
        "es": 'lente de retrato de 85 mm a f/1.4: fondo muy desenfocado y cremoso, compresión que favorece el cuerpo, ella nítida',
        "en": '85mm portrait lens at f/1.4: creamy, strongly blurred background, flattering compression, she is tack sharp'},
    'tele_135': {"lbl": '135 mm tele corto · fondo comprimido y borroso',
        "es": 'teleobjetivo corto de 135 mm a f/2 desde unos pasos más lejos: el fondo se acerca y se comprime detrás de ella y queda muy desenfocado, perspectiva plana que favorece el cuerpo, ella nítida',
        "en": '135mm short telephoto lens at f/2 from a few steps further back: compressed background pulled close behind her and strongly blurred, flat flattering perspective, she is tack sharp'},
    'bokeh_circular': {"lbl": 'Bokeh circular (luces en círculos)',
        "es": 'lente luminoso de 50 mm a f/1.2 con BOKEH CIRCULAR: las luces y brillos del fondo se vuelven círculos grandes, redondos y suaves; ella nítida',
        "en": 'fast 50mm f/1.2 lens with CIRCULAR BOKEH: background lights and highlights turn into large, round, soft bokeh circles; she stays sharp'},
    'tele_200': {"lbl": 'Teleobjetivo 200 mm (fondo pegado y borroso)',
        "es": 'teleobjetivo de 200 mm a f/2.8 desde lejos: el fondo queda comprimido, pegado a ella y totalmente desenfocado',
        "en": '200mm telephoto at f/2.8 from far away: background compressed close behind her and completely blurred'},
    'todo_nitido': {"lbl": 'Todo nítido (f/8)',
        "es": 'apertura f/8: todo en foco, ella y el lugar nítidos de adelante hacia atrás',
        "en": 'aperture f/8: deep focus, she and the whole location sharp front to back'},
    'primer_plano_borroso': {"lbl": 'Algo desenfocado adelante (a través de…)',
        "es": 'foto sacada a través de algo cercano (hojas, una baranda, el borde de una puerta) que queda MUY desenfocado en primer plano y enmarca a la modelo, nítida',
        "en": 'shot through something close to the lens (leaves, a railing, a door edge) that is heavily blurred in the foreground and frames her; she is sharp'},
    'macro': {"lbl": 'Macro 100 mm (detalle de tela)',
        "es": 'lente macro de 100 mm: detalle muy cercano de la tela, la textura y las costuras, profundidad de campo mínima',
        "en": '100mm macro lens: very close detail of the fabric, texture and seams, razor-thin depth of field'},
    'xcd_90': {"lbl": 'Hasselblad XCD 90 mm f/2.5 (retrato editorial, plano medio)',
        "es": 'lente Hasselblad XCD 90 mm f/2.5 de medio formato: retrato editorial, foco preciso en la modelo y la prenda, fondo suavemente desenfocado que conserva las formas reconocibles del lugar',
        "en": 'Hasselblad XCD 90mm f/2.5 medium-format lens: editorial portrait, precise focus on the model and the garment, background softly blurred while keeping the shapes of the place recognisable'},
    'xcd_55': {"lbl": 'Hasselblad XCD 55 mm f/2.5 (cuerpo entero con el paisaje)',
        "es": 'lente Hasselblad XCD 55 mm f/2.5 de medio formato: cuerpo entero con el paisaje imponente atrás, más profundidad de campo para que se lean las formas del lugar',
        "en": 'Hasselblad XCD 55mm f/2.5 medium-format lens: full body with the monumental landscape behind, deeper depth of field so the shapes of the place stay readable'},
    'ojo_de_pez': {"lbl": 'Ojo de pez (deformado, divertido)',
        "es": 'lente ojo de pez: imagen curva y redondeada, look de skate/streetwear divertido; la prenda sigue reconocible',
        "en": 'fisheye lens: curved, rounded distortion, fun skate/streetwear look; the garment stays recognisable'},
}
CAMARAS_FOTO: Dict[str, Dict[str, str]] = {
    'hasselblad': {"lbl": 'Hasselblad X2D 100C (medio formato)',
        "es": 'sacada con una Hasselblad X2D 100C de medio formato: detalle altísimo, color natural y rico, transiciones de tono suaves, look de campaña de lujo',
        "en": 'shot on a Hasselblad X2D 100C medium-format camera: extreme detail, rich natural colour, smooth tonal transitions, luxury campaign look'},
    'phase_one': {"lbl": 'Phase One IQ4 150MP (campaña de revista)',
        "es": 'sacada con una Phase One IQ4 de 150 megapíxeles: nitidez extrema, piel y tela con detalle real, rango dinámico enorme, look de campaña de revista',
        "en": 'shot on a Phase One IQ4 150MP: extreme sharpness, real skin and fabric detail, huge dynamic range, high-end magazine campaign look'},
    'fuji_gfx': {"lbl": 'Fujifilm GFX100 II (color de Fuji)',
        "es": 'sacada con una Fujifilm GFX100 II de medio formato con simulación de película Classic Chrome: colores sobrios y elegantes, sombras suaves',
        "en": 'shot on a Fujifilm GFX100 II medium format with Classic Chrome film simulation: muted, elegant colours, soft shadows'},
    'leica_m11': {"lbl": 'Leica M11 (look Leica, volumen 3D)',
        "es": 'sacada con una Leica M11 y un Summilux 50 mm: microcontraste Leica, sensación de volumen 3D, colores cálidos y naturales',
        "en": 'shot on a Leica M11 with a Summilux 50mm: Leica micro-contrast, 3D pop, warm natural colours'},
    'sony_a1': {"lbl": 'Sony A1 II (rápida, ideal movimiento)',
        "es": 'sacada con una Sony A1 II: enfoque perfecto en los ojos, nitidez de acción, ideal para movimiento',
        "en": 'shot on a Sony A1 II: perfect eye autofocus, crisp action sharpness, ideal for motion'},
    'canon_r5': {"lbl": 'Canon EOS R5 II (piel cálida)',
        "es": 'sacada con una Canon EOS R5 II: tonos de piel cálidos y favorecedores, color Canon',
        "en": 'shot on a Canon EOS R5 II: warm, flattering skin tones, Canon colour science'},
    'nikon_z9': {"lbl": 'Nikon Z9 (nítida y neutra)',
        "es": 'sacada con una Nikon Z9: imagen nítida y neutra, color fiel',
        "en": 'shot on a Nikon Z9: crisp, neutral image, faithful colour'},
    'contax_t2': {"lbl": 'Contax T2 · película con flash directo',
        "es": 'sacada con una cámara compacta de película Contax T2 con flash directo: grano de película, sombra dura detrás, look de editorial de moda 2000s',
        "en": 'shot on a Contax T2 35mm film compact with direct on-camera flash: film grain, hard shadow behind her, 2000s fashion editorial look; REAL ANALOG FILM: visible fine grain, soft highlight roll-off, gentle contrast, slightly muted colours; never HDR, never over-sharpened, never glossy retouched skin'},
    'portra': {"lbl": 'Película Kodak Portra 400',
        "es": 'fotografiada en película Kodak Portra 400 de 35 mm: grano fino, piel cálida y suave, colores pastel; FOTO ANALÓGICA REAL: grano visible, luces altas suaves, contraste moderado; nunca HDR, nunca sobre-enfocada ni con piel retocada brillante',
        "en": 'shot on Kodak Portra 400 35mm film: fine grain, warm soft skin, gentle pastel colours; REAL ANALOG FILM: visible fine grain, soft highlight roll-off, gentle contrast, slightly muted colours; never HDR, never over-sharpened, never glossy retouched skin'},
    'polaroid': {"lbl": 'Polaroid (instantánea)',
        "es": 'foto instantánea tipo Polaroid: colores lavados y cálidos, contraste suave, look casero y nostálgico (sin el marco blanco)',
        "en": 'Polaroid instant photo look: washed-out warm colours, soft contrast, homemade nostalgic feel (no white frame)'},
    'iphone': {"lbl": 'iPhone 16 Pro (foto de celular)',
        "es": 'foto sacada con un iPhone 16 Pro: look de celular real y espontáneo, todo bastante nítido, color vivo',
        "en": 'photo taken with an iPhone 16 Pro: real spontaneous phone-photo look, mostly sharp, vivid colour'},
}
MOVIMIENTOS_FOTO: Dict[str, Dict[str, str]] = {
    'congelado': {"lbl": 'Congelado (1/2000 s): todo quieto en el aire',
        "es": 'movimiento CONGELADO a 1/2000 s: ella en plena acción, el pelo y la tela suspendidos en el aire, todo perfectamente nítido',
        "en": 'FROZEN motion at 1/2000s: she is mid-action, hair and fabric suspended in the air, everything perfectly sharp'},
    'paneo': {"lbl": 'Barrido / paneo: ella nítida, fondo corrido',
        "es": 'BARRIDO (paneo): la cámara la sigue mientras camina o corre; ella queda nítida y el fondo sale corrido en líneas horizontales de movimiento',
        "en": 'PANNING shot: the camera tracks her as she walks or runs; she stays sharp while the background is streaked with horizontal motion blur'},
    'estela': {"lbl": 'Estela de movimiento (1/15 s, editorial)',
        "es": 'exposición lenta de 1/15 s: leve estela de movimiento en el pelo, las manos y los bordes; la cara y la prenda se leen claras, look editorial artístico',
        "en": 'slow 1/15s exposure: slight motion trails on hair, hands and edges, while face and garment remain readable, artistic editorial look'},
    'flash_arrastre': {"lbl": 'Flash + arrastre (nocturno, fantasma de luz)',
        "es": 'flash con arrastre (cortina trasera): ella congelada por el flash con una suave estela fantasma de las luces del lugar, look de noche y fiesta',
        "en": 'rear-curtain flash with slow shutter: she is frozen by the flash with soft ghosting trails from ambient lights, night-out look'},
    'caminando': {"lbl": 'Caminando hacia cámara (en paso)',
        "es": 'caminando hacia la cámara en pleno paso, un pie apenas levantado, braceo natural, el pelo y la tela se mueven con el andar',
        "en": 'walking towards the camera mid-stride, one foot just lifted, natural arm swing, hair and fabric moving with the walk'},
    'salto': {"lbl": 'Saltando (los dos pies en el aire)',
        "es": 'saltando, los dos pies en el aire, foto congelada en el punto más alto, expresión de alegría',
        "en": 'jumping with both feet off the ground, frozen at the peak, joyful expression'},
    'giro': {"lbl": 'Girando (pelo y tela al vuelo)',
        "es": 'girando sobre sí misma, el pelo y la tela se abren al vuelo hacia afuera, movimiento congelado',
        "en": 'spinning around, hair and fabric flaring outward, motion frozen'},
    'corriendo': {"lbl": 'Corriendo (deportivo)',
        "es": 'corriendo, zancada larga, un pie en el aire, cuerpo inclinado hacia adelante, energía deportiva, congelado y nítido',
        "en": 'running with a long stride, one foot in the air, body leaning forward, sporty energy, frozen and sharp'},
}


_VIDA_FX = ("BODY LANGUAGE: alive and relaxed, caught between moments — weight on one leg, "
            "soft knees, loose shoulders, a slight twist of the torso, hands soft and doing "
            "something, a real expression (relaxed half-smile or easy focus, soft eyes). Never a "
            "stiff mannequin stance, never arms hanging straight and glued to the body, never a "
            "blank stare.")


_HDR_BLOQUE = re.compile(r"^[⚠•\- ]*[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9 /,.'()\-]{3,}[^:\n]{0,80}:")


def bloques_de(txt: str, en_linea: bool = False) -> List[Dict[str, str]]:
    """Un pedido largo en bloques {titulo, texto}: por párrafo, y dentro de un párrafo cada
    renglón que empieza con un TÍTULO EN MAYÚSCULAS es otro bloque (antes "TIPO DE CUERPO" +
    "REVISIÓN FINAL" + "TOMA DE ESPALDA" quedaban juntos como "regla 14"). Con en_linea, también
    corta donde una oración nueva arranca con "TÍTULO:" (los pedidos en inglés de un solo párrafo)."""
    if en_linea:
        txt = re.sub(r"(?<=[.!?)]) +(?=[A-Z][A-Z /'()\-]{3,}:)", "\n", str(txt or ""))
    trozos: List[str] = []
    for par in [x.strip() for x in re.split(r"\n\s*\n", str(txt or "")) if x.strip()]:
        cur: List[str] = []
        for ln in par.split("\n"):
            if cur and _HDR_BLOQUE.match(ln):
                trozos.append("\n".join(cur))
                cur = []
            cur.append(ln)
        if cur:
            trozos.append("\n".join(cur))
    bloques = []
    for i, b in enumerate(trozos):
        cab, _, resto = b.partition(":")
        if resto.strip() and 2 < len(cab) <= 160 and "\n" not in cab:
            # el título corto, para que "los del bloque ESTA TOMA" siga encontrándolo
            t = re.split(r" \(| — | - ", cab.strip(" -•⚠"))[0].strip()[:70]
            bloques.append({"titulo": t, "texto": resto.strip()})
        else:
            bloques.append({"titulo": f"regla {i + 1}", "texto": b})
    return bloques


def a_json_generico(texto: str, pedido: Dict[str, Any]) -> str:
    """El pedido de Storyboard o Reels en JSON, igual que el de Fotos: lo elegido arriba de todo
    y el resto en bloques con título. No se saca nada."""
    return json.dumps({
        "task": "Generate ONE image following ALL of this JSON. No field is optional or decorative.",
        "requested (highest priority)": {k: v for k, v in (pedido or {}).items() if v},
        "full_instructions": bloques_de(texto, en_linea=True),
        "final_check": "Before delivering, check every field of 'requested' and that the garment is IDENTICAL to the real product photos.",
    }, ensure_ascii=False, indent=1)


# ── PROMPT EN JSON + RESCATE ──
# Con el pedido en JSON (una ficha técnica ordenada en campos) Nano Banana bloquea menos POR EL
# PEDIDO. El filtro de la IMAGEN ya generada (IMAGE_SAFETY) no mira cómo se pidió: ahí no hay
# rescate en JSON (siguen los respaldos de siempre). Con nenes, no hay rescate.
def es_bloqueo_del_pedido(e: Exception) -> bool:
    if getattr(e, "status_code", 0) != 422:
        return False
    d = str(getattr(e, "detail", "") or "")
    if "Motivo del prompt" in d:
        return True
    m = re.search(r"motivo: ([A-Z_]+)", d)
    return bool(m) and m.group(1) in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "OTHER", "SPII")


def _k_json_stats() -> str:
    return _pfx() + "json_stats"


async def anotar_json(modulo: str, formato: str, ok: bool, rescate: bool = False) -> None:
    """Cuántas salen y cuántas se bloquean con cada formato, por módulo (para saber si sirve)."""
    try:
        st = await kv.get(_k_json_stats()) or {}
        m = st.setdefault(modulo, {})
        f = m.setdefault(formato, {"ok": 0, "bloq": 0})
        f["ok" if ok else "bloq"] += 1
        if rescate:
            m["rescates"] = int(m.get("rescates") or 0) + 1
        await kv.set(_k_json_stats(), st)
    except Exception:
        pass


async def gemini_con_json(parts: List[Dict[str, Any]], settings: Dict[str, Any], aspect: str, image_size: str, *,
                          formato: str, a_json: Any, modulo: str, rescate: bool = True,
                          **kw: Any) -> Tuple[bytes, str]:
    """Nano Banana con el pedido en JSON (formato="json": ya viene convertido) o en texto, y si
    el texto se bloquea POR EL PEDIDO, una vez más en JSON. Devuelve (imagen, nota)."""
    try:
        img = await gemini_generate(parts, settings, aspect, image_size, **kw)
        await anotar_json(modulo, formato, True)
        return img, ("prompt JSON" if formato == "json" else "")
    except HTTPException as e:
        if e.status_code == 422:
            await anotar_json(modulo, formato, False)
        if formato == "json" or not rescate or not es_bloqueo_del_pedido(e):
            raise
        print(f"[json] {modulo}: bloqueado por el pedido en texto; va en JSON")
    pj = [dict(x) for x in parts]
    for x in pj:
        if x.get("text"):
            x["text"] = a_json(x["text"])
            break
    est = _pricing(settings).get(image_size, 0.10)
    await budget_record(f"{modulo}_json", image_size, est, 1, note=f"{modulo}: reintento en JSON (el texto se bloqueó)")
    try:
        img = await gemini_generate(pj, settings, aspect, image_size, **kw)
    except HTTPException as e2:
        if e2.status_code == 422:
            await anotar_json(modulo, "json", False)
        raise
    await anotar_json(modulo, "json", True, rescate=True)
    return img, "bloqueado en texto: salió en JSON"


def _prompt_a_json(texto: str, p: Dict[str, Any]) -> str:
    """El pedido para Nano Banana en formato JSON. Con un texto corrido tan largo el modelo
    elige qué tomar y qué no; acá lo pedido por la usuaria va primero, cada cosa en su campo,
    y el resto de las reglas como una lista de bloques con título. No se saca nada."""
    def _v(*ks):
        for k in ks:
            x = str(p.get(k) or "").strip()
            if x:
                return x
        return ""
    txt = str(texto or "")

    def _linea(prefijo: str) -> str:
        m = re.search(r"^" + re.escape(prefijo) + r"[^\n]*", txt, re.M)
        return m.group(0).split(":", 1)[1].strip() if m else ""
    # La cámara y la óptica SÓLO si ella las eligió (lo automático no es "pedido por ella"), y
    # tal cual quedaron en el texto: antes se recalculaban acá y podían salir distintas.
    _eligio_cam = any(str(p.get(k) or "").strip() not in ("", "auto")
                      for k in ("lente", "camara_foto", "movimiento", "ajustes"))
    _z = _zona(p)
    oblig = {
        "ficha_tecnica_de_la_toma": _linea("FICHA TÉCNICA DE LA TOMA:"),
        "prenda": _v("aclaraciones", "producto_manual"),
        "color_de_la_prenda": _v("color_set", "color"),
        "pose": _v("pose"),
        "encuadre": (ENCUADRE_ZONA[_z][0] if _z else _v("encuadre")),
        "angulo_de_camara": _linea("CÁMARA:"),
        "lugar": _v("fondo"),
        "luz": _v("luz"),
        "camara_y_optica": _linea("CÁMARA Y ÓPTICA") if _eligio_cam else "",
        "lleva_ademas": _v("piezas"),
        "accesorios": _v("cuerpo_accesorios", "accesorios"),
    }
    if "ESTA TOMA COPIA UNA FOTO DE REFERENCIA" in txt or "ESTA TOMA COPIA LA POSE DE UNA FOTO" in txt:
        oblig["copiar_de_la_guia"] = ("la pose, el ángulo y la altura de la cámara, la distancia y el encuadre salen de la "
                                     "imagen rotulada GUÍA DE POSE Y CÁMARA (su foto): copialos igual"
                                     if "La CÁMARA (ángulo y distancia) NO" not in txt else
                                     "la pose sale de la imagen rotulada GUÍA DE POSE Y CÁMARA; la cámara es la elegida por ella")
    oblig = {k: v for k, v in oblig.items() if v}
    bloques = bloques_de(txt)
    d = {
        "tarea": ("Generá UNA foto siguiendo TODO este JSON. Ningún campo es opcional ni decorativo: "
                  "si algo no entra, no lo inventes ni lo saltees."),
        "lo_pedido_por_la_usuaria (máxima prioridad)": oblig,
        "instrucciones_completas": bloques,
        "control_final": ("Antes de entregar, revisá uno por uno los campos de 'lo_pedido_por_la_usuaria' "
                          "y que la prenda sea IDÉNTICA a las fotos reales del producto."),
    }
    return json.dumps(d, ensure_ascii=False, indent=1)


_GUIA_VIVA_EN = ('The guide is only a still sketch of the pose: the photo must be the REAL moment, MORE ALIVE than the sketch — elbows softly bent, wrists and fingers loose, weight on one leg, a slight twist of the torso, hair and any fabric moving with the wind, as if caught mid-movement. Never copy the stiffness of the sketch.')
_GUIA_VIVA_ES = ('La guía es sólo un boceto quieto de la pose: la foto tiene que ser el momento REAL, MÁS VIVO que el boceto — codos apenas flexionados, muñecas y dedos sueltos, peso en una pierna, leve giro del torso, el pelo y la tela moviéndose con el viento, como captada en pleno movimiento. Nunca copies la rigidez del boceto. ')


def _prompt_con_guia(prompt: str, enc: Optional[Dict[str, int]], cam_mano: bool = False,
                     perfil: Optional[Dict[str, Any]] = None) -> str:
    """Nano Banana con una pose COPIADA DE FOTO: se sacan del pedido las órdenes de cámara y
    de pose que se pelean con la guía (el "lente 85 mm" del modo verano, la toma de espalda
    "girada 3/4 mirando por encima del hombro", la cámara "a la altura de los ojos", el
    "tamaño de plano de esta toma") y la guía pasa AL TOPE del pedido."""
    if cam_mano:
        # Ella eligió el ángulo a mano: ese ángulo y su distancia mandan. La escala medida y la
        # cámara del perfil decían otra cosa ("cámara en el piso, ocupa el 85% del alto") y el
        # pedido quedaba con tres órdenes distintas.
        enc = None
        if perfil:
            perfil = {**perfil, "modules": {**(perfil.get("modules") or {}),
                                            "camera": False, "framing": False}}
    for viejo, nuevo in (
        (ESPALDA_VERANO_SOFT, ""),
        ("la altura y el lugar los fija el renglón CÁMARA de esta toma; si no hay ninguno, va a la "
         "altura de los ojos de un fotógrafo parado (~1,60 m).",
         "la altura y el ángulo los fija el renglón CÁMARA de esta toma (elegido por ella); la "
         "pose sale de la imagen rotulada GUÍA DE POSE Y CÁMARA." if cam_mano else
         "la altura, el ángulo y la distancia los fija la imagen rotulada GUÍA DE POSE Y CÁMARA."),
        ("el TAMAÑO DE PLANO indicado en ESTA TOMA (bloque de arriba)",
         "el de la imagen rotulada GUÍA DE POSE Y CÁMARA: mismo tamaño de ella en el cuadro"),
        ("el indicado en ESTA TOMA (bloque de arriba)",
         "el de la imagen rotulada GUÍA DE POSE Y CÁMARA: mismo tamaño de ella en el cuadro"),
    ):
        prompt = prompt.replace(viejo, nuevo)
    tope = (("⚠ ESTA TOMA COPIA LA POSE DE UNA FOTO DE REFERENCIA: la pose y la energía del gesto "
             "salen de la imagen rotulada GUÍA DE POSE Y CÁMARA. La CÁMARA (ángulo y distancia) NO "
             "sale de la guía: es la del renglón CÁMARA de esta toma, que eligió ella. " if cam_mano else
             "⚠ ESTA TOMA COPIA UNA FOTO DE REFERENCIA: la pose, la cámara (altura, ángulo y DISTANCIA), "
             "el encuadre y la energía del gesto salen de la imagen rotulada GUÍA DE POSE Y CÁMARA y "
             "mandan sobre cualquier otra indicación de plano, lente, cámara o pose de este texto. ")
            + ((_texto_escala(enc, False) + " ") if enc else "")
            + _GUIA_VIVA_ES + "\n"
            + (("PERFIL FOTOGRÁFICO DE LA REFERENCIA (JSON medido de la foto; reproducí cada valor: "
                "tamaño de ella en el cuadro, altura y ángulo de cámara, distancia, lente, foco, luz, "
                "movimiento): " + _perfil_json(perfil) + "\n") if perfil else "")
            + "\n")
    return tope + prompt


# FICHA TÉCNICA DE LA TOMA: el ángulo dicho como lo dice un director de fotografía, en los términos
# estándar en inglés con los que se entrenan los modelos de imagen ("rear three-quarter view",
# "low-angle shot", "cowboy shot", "85mm"). En criollo y largo ("cámara a la altura de la cintura,
# corrida en diagonal…") el modelo lo leía como una sugerencia más y volvía al frente.
_FT_VISTA = {
    "frente": "FRONT VIEW: she faces the camera squarely",
    "tres_cuartos": "THREE-QUARTER FRONT VIEW: her body turned ~45° from the lens",
    "perfil": "SIDE PROFILE VIEW (90°): the lens sees her side",
    "espalda_34": ("REAR THREE-QUARTER VIEW (back 3/4, ~135°): her BACK and the BACK of the garment face the lens, the front of "
                   "her body is NOT visible; she looks back OVER HER SHOULDER at the camera"),
    "espalda": "REAR VIEW / BACK SHOT (180°): she faces AWAY from the camera, the lens sees only her back",
}
_FT_CAM = {
    0: "EYE-LEVEL SHOT, straight on", 1: "LOW-ANGLE SHOT: camera at hip height, tilted UP at her",
    2: "THREE-QUARTER ANGLE: camera ~45° off-axis", 3: "HIGH-ANGLE SHOT: camera above her head, tilted DOWN",
    4: "LONG-LENS SHOT from a distance (telephoto compression)", 5: "CLOSE SHOT: camera one step from her",
    6: "WORM'S-EYE VIEW: camera on the ground pointing UP", 7: "EXTREME WIDE SHOT: she is small in the location",
    8: "AERIAL BIRD'S-EYE VIEW (drone, top-down)", 9: "SLIGHTLY OFF-AXIS ANGLE: camera one step to the side",
    10: "MEDIUM CLOSE SHOT with the location visible", 11: "90° SIDE ANGLE: camera fully to her side",
    12: "LOW-ANGLE SIDE SHOT: camera near the ground, from the side, tilted UP",
    13: "CHEST-LEVEL CROUCHED CAMERA", 14: "FOREGROUND OCCLUSION: shot through out-of-focus foreground elements",
}
_FT_PLANO = {
    0: "FULL SHOT (FS): head to toe", 1: "MEDIUM SHOT (MS): frame cuts at the waist",
    2: "COWBOY SHOT / MEDIUM LONG SHOT: frame cuts at mid-thigh, above the knees",
    3: "EXTREME LONG SHOT (ELS): she fills about a third of the frame height",
    4: "MEDIUM CLOSE-UP (MCU): frame cuts at the chest", 5: "TIGHT FULL SHOT: head to toe, filling the frame",
}
_FT_ALTURA = {"ground": "camera at ground level", "knee": "camera at knee height", "waist": "camera at hip height",
              "chest": "camera at chest height", "eye": "camera at eye level", "above_head": "camera above her head",
              "drone": "drone height"}
_FT_SHOT = {"extreme_close_up": "EXTREME CLOSE-UP (ECU)", "close_up": "CLOSE-UP (CU)", "medium": "MEDIUM SHOT (MS)",
            "three_quarter": "COWBOY SHOT / MEDIUM LONG SHOT (cut at mid-thigh)", "full_body": "FULL SHOT (FS): head to toe",
            "wide": "WIDE SHOT (WS)"}


def _ficha_tecnica(man: Optional[Dict[str, Any]], perfil: Optional[Dict[str, Any]],
                   cam: Optional[int], lente: str = "") -> str:
    """La toma en términos de dirección de fotografía. Lo fijado a mano manda; lo que falta sale de lo
    medido en la foto (si el ángulo no se eligió a mano)."""
    man = man or {}
    c = (perfil or {}).get("camera") or {} if _fp.modulo_on(perfil, "camera") else {}
    fr = (perfil or {}).get("framing") or {} if _fp.modulo_on(perfil, "framing") else {}
    partes = []
    # 1) hacia dónde mira el cuerpo
    if man.get("vista"):
        partes.append(_FT_VISTA[man["vista"]])
    elif cam is None and c:
        az = _fp._num(c.get("azimuth"))
        hz = str(c.get("horizontal_angle") or "")
        k = ("espalda" if (hz == "back" or (az is not None and az >= 160)) else
             "espalda_34" if (hz == "three_quarter_back" or (az is not None and az >= 110)) else
             "perfil" if (hz == "side" or (az is not None and az >= 65)) else
             "tres_cuartos" if (hz == "three_quarter" or (az is not None and az >= 20)) else
             "frente" if (hz == "front" or az is not None) else "")
        if k:
            partes.append(_FT_VISTA[k])
    # 2) ángulo y altura de cámara
    if cam is not None:
        partes.append(_FT_CAM.get(cam % len(CAMARA_POOL), ""))
    elif c:
        e = _fp._num(c.get("elevation"))
        alt = _FT_ALTURA.get(str(c.get("height_relative") or ""), "")
        if e is not None and e <= -8:
            partes.append(f"LOW-ANGLE SHOT, {alt or 'camera low'}, tilted UP ~{abs(round(e))}°")
        elif e is not None and e >= 8:
            partes.append(f"HIGH-ANGLE SHOT, {alt or 'camera high'}, tilted DOWN ~{round(e)}°")
        elif alt:
            partes.append(f"{alt}, level (no tilt)")
        d = _fp._num(c.get("distance_estimate_m"))
        if d:
            partes.append(f"camera-to-subject distance ~{d:g} m")
    # 3) tamaño de plano
    if man.get("plano") is not None:
        partes.append(_FT_PLANO.get(man["plano"], ""))
    elif cam is None and fr.get("shot_type") in _FT_SHOT:
        partes.append(_FT_SHOT[fr["shot_type"]])
    # 4) lente
    if lente and lente in LENTES_FOTO:
        partes.append(LENTES_FOTO[lente]["en"].split(":")[0])
    elif cam is None and _fp._num(c.get("focal_length_estimate_mm")):
        partes.append(f"~{round(_fp._num(c.get('focal_length_estimate_mm')))}mm lens")
    partes = [x for x in partes if x]
    if not partes:
        return ""
    return ("SHOT SPEC (professional cinematography terms; MANDATORY, overrides any other camera, angle or framing "
            "in this request" + (", and the guide image only gives the body pose" if perfil is not None else "") + "): " + ". ".join(partes) + ".")


def _guia_solo_pose(man: Optional[Dict[str, Any]], en: bool = False) -> str:
    """Rótulo extra de la guía en modo manual: qué NO sale de ella."""
    if not man:
        return ""
    no = []
    if man.get("camara") is not None:
        no.append("the camera angle and distance" if en else "el ángulo y la distancia de la cámara")
    if man.get("plano") is not None:
        no.append("the framing / shot size" if en else "el encuadre (cuánto cuerpo entra)")
    if man.get("lente"):
        no.append("the lens" if en else "el lente")
    if man.get("vista"):
        no.append("which way her body faces" if en else "hacia dónde mira el cuerpo (de frente, de perfil o de espaldas)")
    if not no:
        return ""
    if en:
        return ("MANUAL MODE: from the guide copy ONLY the pose (arms, hands, legs, weight, head tilt). NOT from "
                "the guide: " + ", ".join(no) + " — the user set them in the text of this shot. ")
    return ("MODO MANUAL: de esta guía se copia SÓLO la pose (brazos, manos, piernas, peso, inclinación de la "
            "cabeza). NO salen de la guía: " + ", ".join(no) + " — los fijó ella en el texto de ESTA TOMA. ")


def _camara_medida(prompt: str, perfil: Optional[Dict[str, Any]], cam_mano: bool,
                   kids: bool = False) -> str:
    """Con una foto copiada (y sin ángulo elegido a mano), el renglón CÁMARA del pedido pasa a decir
    la cámara MEDIDA de la foto (altura, inclinación, costado, distancia, lente). Antes decía el texto
    de uno de los ángulos fijos —muchas veces "a la altura de los ojos, de frente"—, y con el pedido
    en JSON ese renglón es el campo de máxima prioridad: la foto salía siempre de frente."""
    if cam_mano:
        return prompt
    if not kids and _fp.vista_de_atras(perfil):
        # "Encuadres sugerentes" hacía que una toma de espaldas de malla se diera vuelta de frente.
        prompt = prompt.replace(
            "poses o encuadres sugerentes;",
            "poses o encuadres sugerentes (una toma de espaldas que muestra la parte de atrás de la "
            "prenda es una foto de catálogo normal y NO cuenta como sugerente: va de espaldas, como la guía);", 1)
    txt = _fp.camara_en_palabras(perfil)
    if not txt:
        return prompt
    nuevo, n = re.subn(r"^CÁMARA:[^\n]*", lambda _m: "CÁMARA: " + txt, prompt, count=1, flags=re.M)
    return nuevo if n else prompt + "\nCÁMARA: " + txt


def _cam_de_perfil(perfil: Optional[Dict[str, Any]]) -> Optional[int]:
    return _fp.camara_de_perfil(perfil)


def _perfil_json(perfil: Optional[Dict[str, Any]]) -> str:
    """El perfil para el generador: sin los 33 puntos por persona (no los usa y ocupan mucho)."""
    pp = _fp.perfil_para_prompt(perfil)
    return json.dumps(pp, ensure_ascii=False, separators=(",", ":")) if pp else ""


def _encuadre_guia(e: Any) -> Optional[Dict[str, int]]:
    """Cuánto ocupa la persona en la foto de referencia (en % del cuadro), medido en el navegador."""
    if not isinstance(e, dict):
        return None
    try:
        d = {k: max(0, min(100, int(e.get(k)))) for k in ("arriba", "abajo", "izq", "der", "alto")}
    except (TypeError, ValueError):
        return None
    return d if d["abajo"] > d["arriba"] else None


def _texto_escala(e: Optional[Dict[str, int]], en: bool) -> str:
    """La distancia de la foto en números: con "lejos" en un texto largo el motor igual se acercaba."""
    if not e:
        return ""
    lejos = e["alto"] <= 55
    if en:
        return (f"SCALE AND DISTANCE (copy from the guide, mandatory): she fills only {e['alto']}% of the "
                f"frame height, from {e['arriba']}% to {e['abajo']}% from the top, between {e['izq']}% and "
                f"{e['der']}% of the width"
                + ("; she is SMALL and FAR, with lots of landscape and empty foreground around her — do NOT "
                   "zoom in to show the garment" if lejos else "") + ".")
    return (f"ESCALA Y DISTANCIA (copiadas de la guía, obligatorio): ella ocupa sólo el {e['alto']}% del alto "
            f"del cuadro, desde el {e['arriba']}% al {e['abajo']}% contando desde arriba, entre el {e['izq']}% y "
            f"el {e['der']}% del ancho"
            + ("; se ve CHICA y LEJOS, con mucho paisaje y primer plano vacío alrededor: NO te acerques para "
               "mostrar la prenda" if lejos else "") + ".")


def _auto_camara(p: Dict[str, Any], pose_txt: str = "") -> Tuple[str, str, str]:
    """✨ AUTOMÁTICO: la lente, la cámara y el movimiento que mejor le van a esta prenda, esta
    escena, este estilo y la pose de ESTA toma (reglas de fotógrafo, sin costo)."""
    pose = (str(pose_txt or "") + " " + str(p.get("pose") or "")).lower()
    fondo = (str(p.get("fondo") or "") + " " + str(p.get("luz") or "")).lower()
    temp = str(p.get("temporada") or "").lower()
    if temp == "interior_set" and str(p.get("no_lenceria") or "").lower() == "si":
        temp = "invierno"       # set de ropa (ej. deportiva), no de lencería
    est = str(p.get("_estilo") or p.get("style") or "").lower()
    hay = lambda t, ws: any(w in t for w in ws)
    # MOVIMIENTO: sólo si la pose se mueve de verdad
    mov = ""
    if hay(pose, ("corriendo", "trotando", "running")):
        mov = "corriendo"
    elif hay(pose, ("saltando", "salto", "jumping")):
        mov = "salto"
    elif hay(pose, ("tirándose", "tirandose", "zambull", "clavado", "diving")):
        mov = "congelado"
    elif hay(pose, ("girando", "gira sobre", "spinning")):
        mov = "giro"
    elif hay(pose, ("caminando", "camina hacia", "cruzando la calle", "saliendo por", "walking")):
        # "caminando hacia la cámara" en una toma de espaldas la daba vuelta: ahí no se agrega
        mov = "" if _toma_atras_pose(pose) else "caminando"
    # LENTE
    if hay(pose, ("detalle", "de cerca de la tela", "costura")) and not hay(pose.replace("sin cara", ""), ("cara",)) or \
       hay(pose, ("textura", "macro")):
        lente = "macro"
    elif hay(pose, ("primer plano de la cara", "primer plano de cara", "close-up", "retrato")):
        lente = "retrato_85"
    elif str(p.get("fondo_foco") or "").lower() == "nitido":
        lente = "todo_nitido"
    elif hay(fondo, ("luces", "guirnalda", "navidad", "de noche", "nocturn", "neón", "neon", "velas")):
        lente = "bokeh_circular"
    elif mov in ("corriendo", "caminando") and hay(fondo, ("calle", "vereda", "ciudad", "costanera", "avenida")):
        lente = "documental_35"
    elif hay(fondo, ("calle", "vereda", "ciudad", "urbano", "barrio")):
        lente = "documental_35"
    elif temp == "interior_set" or hay(est, ("editorial",)):
        lente = "retrato_85"
    elif temp == "verano":
        lente = "retrato_85" if str(p.get("fondo_foco") or "desenfocado") == "desenfocado" else "todo_nitido"
    elif temp == "kids" or hay(est, ("catalogo",)):
        lente = "normal_50"
    else:
        lente = "normal_50"
    # Con la Hasselblad elegida a mano van sus lentes XCD: 55 mm para cuerpo entero o planos
    # generales, 90 mm para retrato y plano medio (el macro y el detalle quedan como están).
    if str(p.get("camara_foto") or "") == "hasselblad" and lente != "macro":
        lente = ("xcd_55" if hay(pose, ("cuerpo entero", "plano general", "de lejos", "de cuerpo completo",
                                         "pies a cabeza", "caminando", "full body"))
                 else "xcd_90")
    # LA POSICIÓN DE CÁMARA DE ESTA TOMA manda sobre el lente: desde un dron o bien lejos un
    # 85 mm "con fondo cremoso" no tiene sentido (el fondo es el piso o todo el lugar), y en
    # una toma de espalda o de cuerpo entero con la Hasselblad va el 55, no el 90 de retrato.
    _cl = str(p.get("_cam_lbl") or "")
    _lejos = _cl in ("Bien lejos, todo el lugar", "Desde el cielo (dron)") or _ctx("zona_lbl") == "general"
    if _lejos and lente in ("retrato_85", "bokeh_circular", "xcd_90"):
        lente = "xcd_55" if str(p.get("camara_foto") or "") == "hasselblad" else "normal_50"
    if lente == "xcd_90" and (_toma_atras_pose(pose) or p.get("_espalda_toma")
                              or _ctx("zona_lbl") in ("entero", "americano", "abajo")):
        lente = "xcd_55"
    # CÁMARA
    if hay(fondo, ("boliche", "fiesta", "flash")):
        cam = "contax_t2"
    elif mov:
        cam = "sony_a1"
    elif hay(est, ("vintage",)):
        cam = "portra"
    elif lente == "macro":
        cam = "phase_one"
    elif temp == "interior_set":
        cam = "hasselblad"
    elif temp == "verano":
        cam = "canon_r5"
    elif temp == "kids":
        cam = "canon_r5"
    elif hay(est, ("editorial",)):
        cam = "phase_one"
    elif hay(est, ("instagram",)):
        cam = "leica_m11"
    else:
        cam = "hasselblad"
    return lente, cam, mov


def _bloque_camara_foto(p: Dict[str, Any], en: bool = False, pose_txt: str = "") -> str:
    """La óptica, la cámara y el movimiento que eligió ella (o ✨ automático), vacío si nada."""
    k = "en" if en else "es"
    elegido = {c: str(p.get(c) or "").strip() for c in ("lente", "camara_foto", "movimiento")}
    if "auto" in elegido.values():
        a = dict(zip(("lente", "camara_foto", "movimiento"), _auto_camara(p, pose_txt)))
        elegido = {c: (a[c] if v == "auto" else v) for c, v in elegido.items()}
    partes = []
    for campo, cat in (("lente", LENTES_FOTO), ("camara_foto", CAMARAS_FOTO),
                       ("movimiento", MOVIMIENTOS_FOTO)):
        op = cat.get(elegido[campo])
        if op:
            partes.append(op[k])
    # AJUSTES TÉCNICOS de la escena (obturación, ISO, balance de blancos, profundidad de campo)
    _aj = str(p.get("ajustes_en" if en else "ajustes") or p.get("ajustes") or "").strip()
    if _aj:
        partes.append(("camera settings: " if en else "ajustes de cámara: ") + _aj)
    if not partes:
        return ""
    if en:
        return ("CAMERA AND LENS (mandatory): " + "; ".join(partes)
                + ". The garment keeps its exact design and colour.")
    return ("CÁMARA Y ÓPTICA (obligatorio): " + "; ".join(partes)
            + ". La prenda mantiene exactos su diseño y su color.")


def _opts_html(cat: Dict[str, Dict[str, str]], vacio: str) -> str:
    return ('<option value="auto" selected>✨ Automático (lo mejor para esta prenda y escena)</option>'
            + f'<option value="">{vacio}</option>'
            + "".join(f'<option value="{k}">{v["lbl"]}</option>' for k, v in cat.items()))


def build_prompt_flux(p: Dict[str, Any], pose_txt: str, con_persona: bool,
                      n_prod: int, genero: Optional[str] = None,
                      estilo: str = "", prod_tags: Optional[List[str]] = None,
                      n_back_last: int = 0, camara: str = "", rincon: str = "",
                      plano: str = "", escrita: bool = False, n_caras: int = 1,
                      reserva: int = 0, tope: Optional[int] = None,
                      ve_espalda: bool = False, n_previas: Optional[int] = None,
                      guia: bool = False) -> str:
    """Prompt LEAN para FLUX.2/edit: corto, en inglés y sin contradicciones. Los prompts
    largos y apilados (estilo Gemini) confunden a FLUX y bajan la fidelidad."""
    h = _es_hombre(genero)
    subj = "the man" if h else "the woman"
    col = str(p.get("color_set", "")).strip()
    fondo_user = str(p.get("fondo", "")).strip()
    cat = _categoria(p)
    L = []
    # 0) EL CUERPO PRIMERO: enterrado al final perdía contra la foto de referencia y la
    # modelo salía delgada aunque se pidiera talle grande.
    _lista_cuerpo = _cuerpo_lista(p, genero)
    if _lista_cuerpo:
        L.append(f"THE MODEL'S BODY (define it FIRST, before anything else): {subj} is "
                 f"{_lista_cuerpo}. Draw THAT body from the start — do not start from a "
                 f"standard slim model. The reference photo provides ONLY the FACE.")
    # 1) Identidad + prenda (lo esencial de un editor multi-referencia)
    if con_persona:
        L.append(f"A realistic fashion catalog photo of {subj} from the FIRST reference image. "
                 "KEEP HER EXACT FACE — it must be recognizably the SAME specific person: same "
                 "facial features, same face shape, same eyes, nose and mouth, same hair color "
                 "and skin tone. Do NOT make a different lookalike, do NOT beautify or average "
                 "the face."
                 + (" Her HAIRSTYLE, makeup and accessories are NOT taken from the reference "
                    "photo: they follow the STYLING line below, exactly, in every shot."
                    if _hay_estilo_avatar(p) else ""))
    _pein_en = _peinado(p, en=True)
    if _pein_en:
        # Sin esto, Seedream copiaba el peinado de la foto de la cara.
        L.append(f"HAIRSTYLE (mandatory, NOT copied from the reference photo; keep her natural "
                 f"hair colour): {_pein_en}.")
    _est_av = _estilo_avatar(p, en=True)
    if _est_av:
        # Pegada a la identidad, no al final: así la orden "same hair" queda aclarada
        # en el renglón siguiente y no vuelve el pelo natural de la referencia.
        L.append(_est_av)
    elif not con_persona:
        # Con persona ya está dicho arriba ("A realistic fashion catalog photo of…"): antes se
        # repetía y ocupaba lugar del tope.
        ap = _bloque_apariencia(p, genero)
        L.append(f"A realistic fashion catalog photo of {ap or (subj + '.')}")
    # La toma ESCRITA va acá, al tope, antes de los bloques de la prenda: Seedream lee
    # más fuerte lo primero, y enterrada después de seis renglones de prenda perdía.
    _pos_toma = len(L)
    _PANELES_FX = ("GARMENT PANELS: keep EXACTLY where each fabric and color starts and ends "
                   "on the body; bands, mesh strips and trims at the same height and order. "
                   "HARDWARE only where the real photos show it. NECKLINE exactly as the "
                   "photos.")
    L.append("She wears EXACTLY the garment from the product reference photo(s): same design, "
             + ("cut, lace/fabric texture, straps and trims (its COLOUR is set below, not by the "
                "photos). " if col else "cut, color, lace/fabric texture, straps and trims. ")
             + "Copy that exact garment; do NOT "
             "invent details it does not have (no extra seams, no logos, no lace or trims that "
             "are not in the photo). If the product reference is a CATALOG sheet showing several "
             "garments, copy ONLY the single one that matches the color/description and IGNORE "
             "the other garments, the text, prices, magazine and props around it.")
    _vistas = _bloque_vistas_flux(n_prod, (1 + n_previas) if n_previas is not None else
                                  ((1 + max(1, n_caras)) if con_persona else 1),
                                  prod_tags, n_back_last)
    if _vistas:
        L.append(_vistas)
    L.append(_PANELES_FX)
    if col:
        _ce = _color_en(col)
        # Corto a propósito: va entre los esenciales y el pedido de Seedream tiene tope.
        L.append(f"GARMENT COLOUR (overrides the photos): main fabric and lace are {_ce.upper()}. "
                 "Copy the photos' design, NOT their colour; straps, rings and trims keep their "
                 "photo colour.")
    if not _espejo_pedido(p, pose_txt):
        # Los reflejos son la fuente nº1 de brazos y cuerpos fantasma.
        L.append("No mirrors or reflections in the scene; one person, two arms.")
    pm = str(p.get("producto_manual", "")).strip()
    if pm:
        L.append(f"Product description: {pm}.")
    _acc = str(p.get("cuerpo_accesorios", "")).strip()
    pz = str(p.get("piezas", "")).strip()
    if pz:
        L.append(f"Pieces worn: {pz}.")
    if _acc:
        # esencial: si viajaba dentro del bloque de cuerpo (extra) se caía con el tope
        L.append(f"Accessories (only these, nothing else): {_acc}.")
    # 2) POSE y ESCENARIO de la usuaria PRIMERO y con prioridad sobre el estilo
    _zf = _zona(p)
    if _zf:
        L.append(f"MANDATORY FRAMING (top priority, overrides the shot size of any pose): "
                 f"{ENCUADRE_ZONA[_zf][1]} The pose only sets the posture, body orientation "
                 "and gesture; WHICH PART OF THE BODY IS IN FRAME is set by this framing.")
        pose_txt = _pose_sin_plano(pose_txt, en=True) if pose_txt else pose_txt
    T: List[str] = []          # el bloque de la toma; si es escrita, sube al tope
    if pose_txt and escrita:
        # La escribió la fotógrafa: se sigue al pie de la letra, entera, y nada de lo que
        # sigue en el prompt la reemplaza ni le agrega otro encuadre.
        T.append("MANDATORY POSE — THE SHOT, written by the photographer (top priority; "
                 "follow it LITERALLY and COMPLETELY: posture, gesture, hands, gaze, "
                 "framing, what is in frame and where she is; do NOT swap any part of it "
                 "for a generic catalog pose, do NOT add another framing or camera angle, "
                 + ("and copy the body pose from the POSE AND CAMERA GUIDE image (never from the "
                    "face or product photos)" if guia else
                    "and ignore any pose shown in the reference images")
                 + ("; posture and gesture only — the crop is the MANDATORY FRAMING above"
                    if _zf else "")
                 + f"): {pose_txt}.")
    elif pose_txt:
        T.append("MANDATORY POSE (top priority, overrides any pose mentioned later — except the "
                 "user's own notes ('Also respect'), which may adjust gaze or attitude — AND any "
                 + ("pose in the face or product photos; the body pose is the one in the POSE AND "
                    "CAMERA GUIDE image" if guia else "pose shown in the reference images")
                 + ("; posture and gesture only — the crop is the MANDATORY FRAMING above"
                    if _zf else "")
                 + f"): {pose_txt}.")
    if pose_txt:
        low_p = pose_txt.lower()
        if _toma_atras_pose(low_p):
            T.append("No mirror and no reflection in a back shot (a reflection would show the "
                     "front). The BACK-view product photo is the MAIN reference for the garment's back (its design "
                     "only — never copy its framing, crop or distance). "
                     "Seen from BEHIND: the BACK of the garment exactly as in the BACK-view "
                     "photo (straps, elastics, closures, trims). Never copy the FRONT design "
                     "(bows, lace panels, neckline) onto the back; without a back photo, a "
                     "simple coherent back in the same fabric and colour.")
        elif (("perfil" in low_p or "profile" in low_p)
              and "three-quarter" not in low_p and "tres cuartos" not in low_p):
            # La apoyada dice "NOT a pure side profile": no es una toma de perfil.
            T.append("She is seen in PROFILE (side view), body turned sideways to the camera.")
    if escrita and camara.strip():
        # El ángulo que eligió a mano para ESTA toma va pegado a ella, no al final.
        T.append(camara.strip())
        camara = ""
    if ve_espalda and not _toma_atras_pose(pose_txt):
        T.append("In this pose her BACK shows: every visible part of the back of the garment "
                 "(back band, straps, the back of the bottom) follows the BACK-view photo, "
                 "never the front design (no front lace panel repeated on the back).")
    acl = str(p.get("aclaraciones", "")).strip()
    if acl:
        # Su pedido va pegado a la toma: al final era lo primero que cortaba el tope.
        T.append(f"Also respect: {acl[:600]}")
    if escrita and not _zf:
        L[_pos_toma:_pos_toma] = T
    else:
        L.extend(T)
    _toma_atras = _toma_atras_pose(pose_txt)
    if p.get("_espalda_distinta") and not _toma_atras:
        # Va con los ESENCIALES: sin esto, lo que se asoma de la espalda sale liso. (En la
        # toma de espalda sobra: la regla de la espalda ya está arriba.)
        L.append("FRONT AND BACK ARE DIFFERENT: this garment does NOT have the same design "
                 "on both sides — the front is the front photos, the back is the back "
                 "photo (they can be completely different fabrics and prints). The design "
                 "switches exactly at the SHOULDER and SIDE seams. Even in a front shot, "
                 "any sliver of the back that shows (over the shoulders, at the sides, "
                 "under the arms) must show the BACK design, never plain or a repeat of "
                 "the front.")
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
    cuerpo = _bloque_cuerpo({**p, "cuerpo_accesorios": ""}, genero).strip()
    # ── De acá para abajo, EXTRAS: valiosos pero prescindibles. Seedream (ByteDance)
    # rechaza el pedido con "Error validating the input" cuando el prompt se pasa de
    # largo, y el nuestro habia llegado a 5.264 caracteres sumando bloque tras bloque.
    # Los extras se van descartando de atrás para adelante hasta entrar en el límite.
    # Cada extra lleva su PRIORIDAD (10 = lo último que se corta) y un nombre para el
    # diagnóstico. Antes se cortaban de atrás para adelante, sin importar qué eran.
    X: List[Any] = []
    if cuerpo:
        X.append((9, "cuerpo", cuerpo))
    if cat == "lenceria":
        X.append((7, "catálogo de lencería", _LENCERIA_FLUX))
    X.append((10, "anatomía y realismo", "Photorealistic like a real camera photo, natural daylight, real skin "
             "with an EVEN skin tone (no orange cast), correct proportions and FLAWLESS "
             "ANATOMY: one left arm and one right arm from their own shoulders with the "
             "matching hands, five fingers each. Avoid 3D render and plastic skin."))
    # GARMENT PANELS es ESENCIAL (va con la prenda, más arriba): con una época elegida el
    # prompt crece y este bloque —el que cuida dónde va cada color— era el primero en caerse;
    # así salió la apoyada con otra bikini.
    X.append((5, "física y escala", "PHYSICS: feet really touch the ground (weight on one leg is fine); when she leans on something, that body part "
             "really TOUCHES it and presses slightly against it, with her weight tilted "
             "INTO the support — never leaning on thin air a hand's width away. Never "
             "floating. SCALE: surroundings at true size — never a miniature landscape."))
    X.append((3, "sombras", "SHADOW: she casts her own shadow on the floor or wall beside her, matching "
             "the scene light, with a darker contact shadow where she touches a surface. "
             "Never shadowless."))
    X.append((1, "sin marcas ajenas", "NO THIRD-PARTY BRANDS: no logos, labels or real brand names on anything in "
             "the scene. Bottles, towels, bags and magazines are plain, with no readable "
             "text."))
    cb = _bloque_categoria(cat, genero, sin_pose=bool(pose_txt.strip()),
                           con_lugar=not str(p.get("fondo", "")).strip())
    # Se guarda para el final de los esenciales (ver más abajo): es ESENCIAL y no extra,
    # pero no puede meterse entre la pose y su ángulo.
    X.append((4, "coherencia del lugar", "SETTING COHERENCE: everything in the frame must genuinely belong to that "
             "location; never an isolated wall or indoor furniture outdoors."))
    # Las aclaraciones de la usuaria son PEDIDO, no adorno: van con los esenciales.
    # El estilo elegido por la usuaria: llegaba como parámetro y no se usaba, así que el
    # selector de estilo no hacía nada con este motor.
    # Dónde está parada la cámara: sin este renglón Seedream la deja siempre a la altura
    # de los ojos y de frente, y el lugar se veía igual en todas las tomas.
    # El tamaño de plano va ANTES de la cámara y del lugar: esos dos empujan hacia lo
    # abierto y sin este renglón todas las tomas salían cuerpo entero.
    if plano.strip():
        L.append(plano.strip())
    if cb:
        # ESENCIAL, no extra: en lencería y baño este bloque es el que pone el tono de
        # catálogo ("modern lingerie catalog… elegant, body-positive"). Estaba entre los
        # extras y, apenas el prompt crecía con una descripción real de producto, se caía
        # — justo el renglón que mantiene la toma del lado del catálogo.
        L.append(cb)
    if camara.strip():
        L.append(camara.strip())
    _cf = _bloque_camara_foto(p, en=True, pose_txt=pose_txt)
    if _cf:
        L.append(_cf)
    if con_persona:
        # ESENCIAL: el "relaxed unposed" vivía sólo en el estilo (extra) y se recortaba; con
        # poses que muestran la prenda salía tiesa, cara seria y brazos pegados, como maniquí.
        L.append(_VIDA_FX)
    # En qué parte del lugar: sin esto, todas las tomas del set salían en el mismo rincón.
    if rincon.strip():
        L.append(rincon.strip())
    if estilo.strip():
        # El estilo de la casa es largo: pasa a extra (prioridad media) para que, si no
        # entra, se recorte él y no el pedido de ella.
        X.append((6, "estilo", estilo.strip()))
    out = _armar_prompt_flux(L, X, tope=(tope if tope else FLUX_PROMPT_MAX - max(0, int(reserva))))
    return _masc_en(out) if _es_hombre(genero) else out


_MASC_OBJ = re.compile(r"\bher(?=\s*(?:[.,;:)]|$|\s+(?:in|into|to|from|on|at|with|by|for|and|so|behind|beside|alone|away|back)\b))")


def _masc_en(t: str) -> str:
    """Los bloques en inglés de Seedream están escritos para una mujer ("she", "her face",
    "Put her in…"): con un modelo varón se pasan a masculino. "her" objeto → "him", posesivo → "his"."""
    t = re.sub(r"\bShe\b", "He", t)
    t = re.sub(r"\bshe\b", "he", t)
    t = re.sub(r"\bherself\b", "himself", t)
    t = re.sub(r"\bHERSELF\b", "HIMSELF", t)
    t = _MASC_OBJ.sub("him", t)
    t = re.sub(r"\bher\b", "his", t)
    t = re.sub(r"\bHer\b", "His", t)
    t = re.sub(r"\bHER\b", "HIS", t)
    t = re.sub(r"\bwoman's\b", "man's", t)
    t = re.sub(r"\bwoman\b", "man", t)
    t = re.sub(r"\bwomen\b", "men", t)
    t = re.sub(r"\bgirl\b", "guy", t)
    return t


# Límite de caracteres del prompt para Seedream. El corte real de ByteDance está en
# ~5.000: se comprobó midiendo el historial — hasta 4.641 caracteres (v2.30.2) andaba
# bien y con 5.224 (v2.31.1) empezó el 422 "Error validating the input" a los ~60s.
# 4.200 dejaba TODOS los bloques de calidad adentro cuando el prompt completo medía
# ~3.760. Con la ONDA DE LA MODELO (época) y el bloque de paneles de la prenda entre los
# esenciales, el pedido llega a ~4.350 y a 4.200 se cortaban las aclaraciones de la
# usuaria: 4.500 sigue por debajo de los 4.641 que se midieron andando bien.
FLUX_PROMPT_MAX = 4600     # medido andando bien hasta 4.641; el corte real ~5.000
_FLUX_PROMPT_ALERTA = 4600     # si algún día se acerca al corte real, queda avisado
# Último armado: se muestra en el diagnóstico para no volver a cruzar el límite a ciegas.
_FLUX_PROMPT_STATS: Dict[str, Any] = {}


def _armar_prompt_flux(esenciales: List[str], extras: List[Any],
                       tope: int = FLUX_PROMPT_MAX) -> str:
    """Junta esenciales + extras respetando el tope. Los extras son (prioridad, nombre,
    texto): si no entran, se cae primero el de MENOR prioridad (no el último), y el
    orden del texto se mantiene. Los esenciales (identidad, prenda, pose, escenario y
    pedidos) nunca se tocan; si solos ya pasan el tope, se recorta al final."""
    base = " ".join(x for x in esenciales if x).strip()
    items = []
    for j, x in enumerate(extras):
        if not x:
            continue
        pr, nom, txt = x if isinstance(x, tuple) else (5, "extra", x)
        if txt:
            items.append([j, pr, nom, txt, True])
    largo = lambda: len(base) + sum(len(it[3]) + 1 for it in items if it[4])
    cortados: List[str] = []
    for it in sorted(items, key=lambda i: (i[1], -i[0])):   # menor prioridad primero
        if largo() <= tope:
            break
        it[4] = False
        cortados.append(it[2])
    out = " ".join([base] + [it[3] for it in items if it[4]]).strip()
    if len(out) > tope:                       # los esenciales solos ya se pasan
        out = out[:tope].rsplit(" ", 1)[0]
        cortados.append("final del pedido (no entraba)")
    _FLUX_PROMPT_STATS.update({"largo": len(out), "descartados": len(cortados),
                               "tope": tope, "cortados": cortados})
    return out


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
    '  "costuras_detalles": "costuras, vivos, cintura, bolsillos u otros detalles visibles. Para CADA borde decí de qué color es su terminación (no generalices \'todos los bordes\' si no es así)",\n'
    '  "lenceria": "SOLO si es lencería o malla (si no, \'n/a\'). CORPIÑO: tipo de copa (triangular, balconette, push-up, deportivo…), si tiene TAZA ARMADA (copa moldeada/preformada: superficie lisa y redondeada que mantiene su forma sola, sin frunces ni pliegues, borde con espesor; en lencería argentina es lo más común — ante la duda entre armada y blanda, elegí ARMADA) o es copa blanda de tela sola (se arruga, se pliega o se adapta plana al cuerpo), y si tiene push-up/relleno extra, con o sin aro, si las copas se unen en un vértice o tienen puente central, si hay banda bajo busto ancha o solo un ribete, breteles (ancho, color, material) y herrajes (argollas, reguladores, su color y metal). BOMBACHA/PARTE DE ABAJO: corte visto de FRENTE (bikini, vedetina, culotte, tiro alto…), altura del tiro (bajo/medio/alto respecto del ombligo y la cadera), ancho de los laterales, cavado de pierna. La cola (colaless, hilo, cola less, cobertura media/total) SOLO si se ve la parte de atrás",\n'
    '  "calce": "tipo de calce (holgado, oversize, ajustado)",\n'
    '  "espalda": "cómo es la PARTE DE ATRÁS de la prenda. Si hay fotos rotuladas como ESPALDA, describila EXACTA de esas fotos (estampa o lisa, cierres, tiras, elásticos, terminaciones). Si NO hay foto de la espalda, deducí la espalda MÁS PROBABLE y CONSERVADORA para este tipo de prenda (normalmente: misma tela y color base, LISA y SIN el estampado/estampado principal del frente, sin bolsillos ni botones; en lencería: cierre simple estándar SIN contar ganchos ni niveles, y bombacha de cobertura media, NO colaless/hilo) y empezá OBLIGATORIAMENTE con \'(deducida, sin foto)\'. Al final agregá SIEMPRE una traducción corta al inglés entre paréntesis, ej: (back: plain sheer tulle, thin straps, no lace, no bow)",\n'
    '  "ficha_para_render": "UN párrafo denso y preciso que describa la prenda entera para guiar una generación de imagen fiel, mencionando tela, color base, estampa con su escala/densidad, terminaciones Y cómo es la espalda",\n'
    '  "no_tiene": ["detalles que este TIPO de prenda suele traer y que ESTA prenda NO tiene (mirando las fotos). Pensá en lo típico de su categoría: costuras o pinzas en la copa, aro, relleno, moño, encaje, puntilla, logo/etiqueta visible, botones, bolsillos, cordón, volados, transparencias, estampa, brillo, herrajes de otro color, costura central, etc. Solo lo que estás seguro que NO está. 4 a 10 ítems cortos, ej: \'costuras en la copa\', \'moño en el centro\', \'encaje\'"],\n'
    '  "negativos_sugeridos": ["errores típicos a evitar dado lo que ves, ej: no inventar tejido cable, no agregar puños de otro color, mantener la estampa a la misma escala"]\n'
    "}\n"
    "Sé fiel a lo que VES en las fotos. No inventes detalles que no aparezcan.\n"
    "REGLAS:\n"
    "- Ignorá el soporte: maniquí, percha, manchas o marcas del maniquí, piso y fondo NO son parte de la prenda.\n"
    "- Tela: describí la textura que se ve (acanalado/rib, liso, encaje, tul, microfibra brillante…). "
    "La FIBRA (algodón, poliamida…) solo si hay etiqueta legible; si no, no la afirmes.\n"
    "- Color: nombre preciso + hex aproximado entre paréntesis, ej: 'marrón caramelo/canela (#9C6B52)'. "
    "Fijate en el subtono real (no digas terracota si no tira a naranja/rojo).\n"
    "- Escote: describí su forma real (en un triángulo, el escote lo forman las copas; no digas 'profundo' si no lo es).\n"
    "- Lo que NO se ve en ninguna foto, decí 'no visible' en vez de inventarlo."
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
    "FRENTE Y ESPALDA PUEDEN SER DISTINTOS: muchas prendas tienen una tela o estampa "
    "atrás y otra adelante (ej: frente liso con un dibujo grande y espalda entera a "
    "cuadros). Si hay foto de ESPALDA y la generada muestra algo de la parte de atrás "
    "(por encima del hombro, al costado, o girada), esa parte tiene que coincidir con la "
    "foto de espalda: si sale lisa o repitiendo el frente, ES un error y va en "
    "diferencias.\n"
    "Si la toma es de ESPALDA (o un detalle de la espalda), compará contra las fotos "
    "rotuladas ESPALDA — esa es la única verdad de la parte trasera — y NO la castigues "
    "por no coincidir con el diseño del frente (la espalda de una prenda normalmente ES "
    "distinta del frente). Si muestra la espalda y NO hay foto de espalda, evaluá solo que "
    "la tela, el color y las terminaciones sean coherentes con el producto, sin inventar "
    "diferencias del diseño trasero.\n"
    "OJO CON LAS FOTOS REALES: son fotos de depósito sacadas con celular — pueden venir "
    "ROTADAS o de costado, con la prenda arrugada, puesta en un maniquí o mostrando solo "
    "una parte. Interpretalas con criterio (rotalas mentalmente si hace falta) y usá TODAS "
    "las vistas JUNTAS: si un elemento (un moño, un encaje, una tira, una bombacha) "
    "aparece en CUALQUIERA de las fotos reales, ese elemento EXISTE en el producto y NO es "
    "un invento de la foto generada. Reportá SOLO diferencias de las que estés SEGURO "
    "después de mirar todas las vistas; si una diferencia puede deberse al ángulo, a una "
    "arruga o a que la foto real es parcial o está rotada, NO la cuentes como diferencia "
    "ni bajes el puntaje por eso.\n"
    "FOTOS REALES DE PERFIL O DE COSTADO (muy importante): varias fotos del producto están "
    "tomadas de LADO — la prenda se ve en escorzo, angosta, con una sola copa/manga "
    "visible, el otro lado tapado, y la estampa aparece comprimida o parcial. Eso es "
    "PERSPECTIVA, no un diseño distinto: reconstruí mentalmente cómo se ve esa prenda de "
    "frente antes de comparar. NUNCA reportes como diferencia que 'falta una copa/manga/"
    "tira', que 'la prenda es más angosta', que 'la estampa está incompleta o corrida' o "
    "que 'el escote es distinto' cuando la foto real está de costado y la generada de "
    "frente (o al revés). Una diferencia solo cuenta si estás seguro de que existe MIRANDO "
    "LA MISMA PARTE de la prenda en un ángulo comparable.\n"
    "MIRÁ LA IMAGEN CON LUPA, zona por zona, antes de responder: breteles y sus herrajes, "
    "escote, costuras y cortes, bandas y elásticos, borde inferior, y la estampa de cerca.\n"
    "Compará SOLO la prenda (ignorá cara, pose, fondo e iluminación):\n"
    "1) diseño/molde y largo; 2) color; 3) estampa: motivos, ESCALA, distribución y COLORES "
    "(si las hojas son verdes y las flores lilas, no pueden salir rosas); 4) CORTES Y "
    "PANELES: a qué altura del cuerpo termina cada tela y empieza la otra (ej: el panel "
    "estampado termina justo debajo del busto), y que las bandas, vivos y franjas de "
    "tul/mesh estén a la misma altura, ancho y orden; 5) HERRAJES: argollas, aros, "
    "reguladores, hebillas y ganchos SOLO donde los tiene la prenda real — si los "
    "reguladores van en la espalda y en la foto generada aparecen en los breteles del "
    "frente, ES un error; 6) terminaciones: puños, cuello/ESCOTE (misma forma y "
    "profundidad), cierres, breteles; 7) detalles INVENTADOS que la prenda real no tiene "
    "(bolsillos, botones, capucha, tajos o huecos que en la prenda real son banda "
    "cerrada); 8) piezas de más o de menos; 9) si la estampa tiene LETRAS o monogramas: "
    "las mismas letras, legibles y con la misma tipografía (no garabatos); 10) accesorios "
    "o calzado que nadie pidió (reloj, aros, tatuajes, zapatillas con pijama) — EXCEPCIÓN: "
    "un objeto simple de escena sostenido EN LA MANO (una fruta, un vaso, una flor, una "
    "taza) es utilería editorial válida y NO baja puntaje.\n"
    "Devolvé SOLO un JSON válido, sin texto extra:\n"
    '{"puntaje": <1-10, 10 = prenda idéntica>, '
    '"diferencias": ["diferencia concreta y accionable", ...]}\n'
    "Si la prenda es fiel, diferencias = []. Sé estricto pero justo: diferencias de pose, "
    "iluminación o arrugas naturales NO bajan puntaje."
)


# Segunda pasada: SOLO anatomía y física. Separarla de la prenda es lo que hace que se
# vean errores como 'dos brazos izquierdos', que antes quedaban tapados por la lista larga.
QC_PROMPT_ANATOMIA = (
    "Sos supervisor de retoque fotográfico. Te paso UNA foto generada con IA para un "
    "catálogo de indumentaria. Tu ÚNICO trabajo es cazar errores de ANATOMÍA y de FÍSICA. "
    "No opines de la prenda, ni del estilo, ni del encuadre, ni de la pose elegida.\n"
    "Recorré la imagen por partes, con lupa, y verificá:\n"
    "1) BRAZOS: hay exactamente UN brazo izquierdo y UN brazo derecho, cada uno saliendo "
    "de SU hombro. Fijate en la mano de cada brazo: el pulgar tiene que estar del lado que "
    "corresponde según cómo esté girado el cuerpo. Dos brazos izquierdos, manos espejadas, "
    "un brazo de más o un brazo que nace fuera del hombro son ERRORES GRAVES.\n"
    "2) MANOS Y PIES: cinco dedos por mano y por pie, del largo correcto, sin dedos "
    "fusionados, de más o de menos; muñecas y tobillos que existen.\n"
    "3) ARTICULACIONES: codos, rodillas y cuello doblando para donde puede doblar un "
    "cuerpo real; nada de miembros de goma, torcidos o fundidos entre sí.\n"
    "4) PROPORCIONES: cabeza, torso, brazos y piernas coherentes entre sí; sin miembros "
    "estirados ni encogidos.\n"
    "5) APOYO Y GRAVEDAD: el cuerpo está sostenido de verdad — plantas de los pies "
    "apoyadas en el suelo si está de pie, cola y muslos apoyados si está sentada, contacto "
    "real con aquello en lo que se apoya. Si flota, si queda suspendida contra un tronco o "
    "pared, o si los pies están en el aire o hundidos en el piso, es ERROR GRAVE.\n"
    "6) ESCALA DEL ENTORNO: lo que está alrededor tiene su tamaño real respecto de la "
    "persona (una palmera adulta la supera MUCHO en altura). Si el paisaje parece en "
    "miniatura y la persona gigante, es error.\n"
    "7) CARA: dos ojos coherentes, mirada normal, dientes y orejas sin deformar.\n"
    "8) ENTORNO ROTO: objetos del fondo derretidos o imposibles, texto o logos inventados.\n"
    "Devolvé SOLO un JSON válido, sin texto extra:\n"
    '{"puntaje": <1-10, 10 = anatomía y física impecables>, '
    '"diferencias": ["error concreto y dónde está, ej: la mano derecha tiene el pulgar del '
    'lado equivocado", ...]}\n'
    "Si no encontrás errores, diferencias = [] y puntaje 10. Un ERROR GRAVE (brazo "
    "duplicado, persona flotando, mano deforme) nunca puede tener puntaje mayor a 5."
)


async def _qc_call(parts: List[Dict[str, Any]], api_key: str,
                   modelo: str) -> Optional[Dict[str, Any]]:
    """Una pasada del inspector. Devuelve {puntaje, diferencias} o None (nunca rompe).
    Usa razonamiento alto: es lo que le permite encontrar errores finos."""
    endpoint = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{modelo}:generateContent")
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    cfg_think = {"temperature": 0.1, "responseMimeType": "application/json",
                 "thinkingConfig": {"thinkingLevel": "high"}}
    cfg_plain = {"temperature": 0.1, "responseMimeType": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=180) as cli:
            r = await cli.post(endpoint, json={"contents": [{"role": "user", "parts": parts}],
                                               "generationConfig": cfg_think}, headers=headers)
            if r.status_code == 400:      # el modelo no acepta thinkingConfig
                r = await cli.post(endpoint,
                                   json={"contents": [{"role": "user", "parts": parts}],
                                         "generationConfig": cfg_plain}, headers=headers)
            if r.status_code == 404 and modelo != ANALYZE_MODEL:
                r = await cli.post(ANALYZE_ENDPOINT,
                                   json={"contents": [{"role": "user", "parts": parts}],
                                         "generationConfig": cfg_plain}, headers=headers)
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


async def verificar_prenda(gen_bytes: bytes, prod_b64s: List[str],
                           back_b64s: Optional[List[str]] = None,
                           prod_tags: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Inspección en DOS PASADAS que corren en paralelo:
      1) PRENDA: fidelidad contra las fotos reales (frente y espalda, rotuladas).
      2) ANATOMÍA/FÍSICA: brazos, manos, apoyo, escala.
    Una sola pasada con 10 ítems reportaba solo lo más obvio y dejaba pasar errores
    graves de anatomía. El puntaje final es el PEOR de los dos.
    Devuelve {puntaje, diferencias} o None si no se pudo verificar (nunca rompe)."""
    try:
        api_key = await _current_api_key()
        if not api_key or not prod_b64s:
            return None
        _st = await get_settings()
        _qm = str(_st.get("qc_model") or "").strip() or ANALYZE_MODEL
        # ALTA RESOLUCIÓN: a 1024px no se ven una argollita ni un pulgar al revés.
        gen_b64 = _compress_ref(gen_bytes, max_dim=2048, q=92)
        p_prenda = [{"text": QC_PROMPT},
                    {"text": "IMAGEN GENERADA (la que hay que inspeccionar):"},
                    _img_part(gen_b64)]
        for _j, _b in enumerate(prod_b64s[:3]):
            # Si la usuaria etiquetó la foto, el inspector sabe qué mira y deja de
            # confundir una vista de perfil o de espalda con "otro diseño".
            _t = _etiqueta_txt((prod_tags or [""] * 9)[_j] if prod_tags else "")
            p_prenda.append({"text": (f"FOTO REAL DEL PRODUCTO — {_t} (lo indicó la "
                                      "usuaria; evaluá esta vista como tal):"
                                      if _t else
                                      f"FOTO REAL DEL PRODUCTO (vista {_j + 1}):")})
            p_prenda.append(_img_part(_b))
        for _j, _b in enumerate((back_b64s or [])[:2]):
            p_prenda.append({"text": f"FOTO REAL — ESPALDA del producto (vista trasera "
                                     f"{_j + 1}; única verdad de la parte de atrás):"})
            p_prenda.append(_img_part(_b))
        p_anat = [{"text": QC_PROMPT_ANATOMIA}, _img_part(gen_b64)]

        _anat_on = str(_st.get("qc_anatomia", "si")).lower() in ("si", "sí", "1", "on", "true")
        tareas = [_qc_call(p_prenda, api_key, _qm)]
        if _anat_on:
            tareas.append(_qc_call(p_anat, api_key, _qm))
        res = await asyncio.gather(*tareas, return_exceptions=True)
        vals = [x for x in res if isinstance(x, dict)]
        if not vals:
            return None
        peor = min(int(v.get("puntaje", 10)) for v in vals)
        difs: List[str] = []
        for v in vals:
            for d in (v.get("diferencias") or []):
                if d not in difs:
                    difs.append(d)
        return {"puntaje": peor, "diferencias": difs[:8]}
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


_CARA_PROMPT = (
    "Devolvé SOLO un JSON con el recuadro de la CARA de la persona (incluyendo el pelo de "
    "arriba y el mentón), en coordenadas normalizadas de 0 a 1000 sobre la imagen: "
    '{"box_2d": [ymin, xmin, ymax, xmax]}. Sin texto extra.'
)


def _recorte_cara_heuristico(ref_b64: str) -> str:
    """Sin detector: el retrato del avatar es plano medio con la cara arriba y al centro."""
    img = Image.open(io.BytesIO(base64.b64decode(ref_b64))).convert("RGB")
    w, h = img.size
    box = (int(w * 0.15), 0, int(w * 0.85), int(h * 0.58))
    return _compress_ref(_pil_bytes(img.crop(box)), max_dim=1024, q=92)


def _pil_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


async def _recorte_cara_toma(b64: str) -> str:
    """La cara (con pelo y margen) de una TOMA PREVIA del set, sin el fondo. Vacío si no se
    encuentra una cara (toma de espalda, detalle): ahí no se usa esa toma."""
    ck = _pfx() + "tomaface:" + hashlib.sha1(b64[:20000].encode()).hexdigest()[:24]
    try:
        c = await kv.get(ck)
    except Exception:
        c = None
    if isinstance(c, str):
        return c
    out = ""
    try:
        api_key = await _current_api_key()
        if api_key:
            body = {"contents": [{"role": "user", "parts": [{"text": _CARA_PROMPT}, _img_part(b64)]}],
                    "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}}
            async with httpx.AsyncClient(timeout=60) as cli:
                r = await cli.post(ANALYZE_ENDPOINT, json=body,
                                   headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})
            if r.status_code == 200:
                txt = "".join(pt.get("text", "") for pt in r.json()["candidates"][0]["content"]["parts"])
                data = json.loads(txt.strip().strip("`").replace("json", "", 1) if txt.strip().startswith("`") else txt)
                bb = data.get("box_2d") if isinstance(data, dict) else None
                if isinstance(bb, list) and len(bb) == 4:
                    ymin, xmin, ymax, xmax = [max(0.0, min(1000.0, float(v))) / 1000.0 for v in bb]
                    if xmax - xmin > 0.03 and ymax - ymin > 0.03:
                        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
                        w, h = img.size
                        bw, bh = (xmax - xmin) * w, (ymax - ymin) * h
                        mx, my = bw * 0.45, bh * 0.45            # pelo, orejas, cuello
                        box = (int(max(0, xmin * w - mx)), int(max(0, ymin * h - my * 1.2)),
                               int(min(w, xmax * w + mx)), int(min(h, ymax * h + my)))
                        out = _compress_ref(_pil_bytes(img.crop(box)), max_dim=1024, q=92)
    except Exception as e:
        print(f"[imagenes_ia][cara-toma] {e}")
    try:
        await kv.set(ck, out)
    except Exception:
        pass
    return out


async def recorte_cara_avatar(av: Dict[str, Any]) -> str:
    """SOLO la cara del avatar (con pelo y margen), para los editores tipo Seedream.

    Un editor multi-referencia copia la COMPOSICIÓN de la primera imagen que recibe:
    con el retrato entero (plano medio, de frente, mirando a cámara) todas las tomas
    del set salían con esa misma pose aunque el texto pidiera otra. Con un recuadro
    de cara no hay cuerpo ni encuadre que copiar: la identidad viene de la cara y la
    pose la manda el prompt. El recuadro lo ubica Gemini una sola vez y queda en caché."""
    if av.get("cara_recorte"):
        return av["cara_recorte"]
    ref = av.get("ref_b64") or await get_avatar_ref(av.get("id", ""))
    if not ref:
        return ""
    ck = _pfx() + "avface:" + str(av.get("id", ""))
    cache = await kv.get(ck)
    if cache:
        return cache
    out = ""
    try:
        api_key = await _current_api_key()
        if api_key:
            body = {"contents": [{"role": "user", "parts": [{"text": _CARA_PROMPT}, _img_part(ref)]}],
                    "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}}
            async with httpx.AsyncClient(timeout=60) as cli:
                r = await cli.post(ANALYZE_ENDPOINT, json=body,
                                   headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})
            if r.status_code == 200:
                txt = "".join(pt.get("text", "") for pt in r.json()["candidates"][0]["content"]["parts"])
                data = json.loads(txt.strip().strip("`").replace("json", "", 1) if txt.strip().startswith("`") else txt)
                bb = data.get("box_2d") if isinstance(data, dict) else None
                if isinstance(bb, list) and len(bb) == 4:
                    ymin, xmin, ymax, xmax = [max(0.0, min(1000.0, float(v))) / 1000.0 for v in bb]
                    if xmax - xmin > 0.08 and ymax - ymin > 0.08:
                        img = Image.open(io.BytesIO(base64.b64decode(ref))).convert("RGB")
                        w, h = img.size
                        bw, bh = (xmax - xmin) * w, (ymax - ymin) * h
                        mx, my = bw * 0.35, bh * 0.35            # margen: pelo, orejas, cuello
                        box = (int(max(0, xmin * w - mx)), int(max(0, ymin * h - my * 1.2)),
                               int(min(w, xmax * w + mx)), int(min(h, ymax * h + my)))
                        out = _compress_ref(_pil_bytes(img.crop(box)), max_dim=1024, q=92)
    except Exception as e:
        print(f"[imagenes_ia][cara] {e}")
    if not out:
        try:
            out = _recorte_cara_heuristico(ref)
        except Exception:
            return ref
    await kv.set(ck, out)
    return out


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
    if desc and av.get("personaje"):
        return desc                       # un personaje no se guarda como avatar
    if desc:
        try:
            av["desc"] = desc
            await save_avatar({**av, "ref_b64": ref})
        except Exception:
            pass
    return desc


def _lista_no_tiene(v: Any) -> str:
    """'no_tiene' llega como lista (análisis) o texto (campo que edita ella)."""
    items = v if isinstance(v, list) else str(v or "").replace(";", ",").split(",")
    return ", ".join(x for x in (str(i).strip(" .") for i in items) if x)


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
    _len = str(f.get("lenceria") or "").strip()
    if _len and _len.lower() not in ("n/a", "na", "-", "no aplica"):
        extra.append(f"Construcción: {_len}")
    if f.get("espalda"):
        extra.append(f"ESPALDA de la prenda: {f['espalda']}")
    _nt = _lista_no_tiene(f.get("no_tiene"))
    if _nt:
        extra.append(f"La prenda NO tiene: {_nt}.")
    # Los "errores a evitar" NO se repiten acá: el análisis los escribe en el campo
    # Aclaraciones (como bloque automático que se reemplaza), así la usuaria los ve y los
    # puede editar. Duplicarlos en la ficha solo alargaba el prompt.
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


K_DRIVE_ULT = "imagenes:drive:ultimo"   # cómo salió la última subida a Drive


async def _save_panels_to_drive(panels: List[Any], mode: str,
                                user_sub: Optional[str] = None) -> None:
    """Sube los paneles a Drive en PNG, en segundo plano (no bloquea la generación).

    Y DEJA ANOTADO cómo salió. La subida es de fondo, así que la respuesta se va
    antes de que termine: el panel anunciaba "✅ guardándose en tu Drive" apenas
    largaba la tarea, y una subida que fallaba se veía exactamente igual que una
    que funcionaba. El error sólo aparecía en el log del server, que nadie mira.
    Con esto, Ajustes → Google Drive puede decir si la última anduvo."""
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    ok, fallaron, ultimo_error = 0, 0, ""
    for idx, panel in enumerate(panels):
        try:
            buf = io.BytesIO()
            await asyncio.to_thread(panel.save, buf, "PNG")
            link = await drive_upload(f"studioluma_{mode}_{ts}_{idx + 1}.png",
                                      buf.getvalue(), "image/png", user_sub=user_sub)
            if link:
                ok += 1
            else:
                fallaron += 1
                ultimo_error = "Drive rechazó la subida (mirá el log del server)"
        except Exception as e:
            fallaron += 1
            ultimo_error = str(e)[:200]
            print(f"[imagenes_ia][drive bg] {e}")
    try:
        await kv.set(K_DRIVE_ULT, {
            "cuando": time.time(), "modo": mode,
            "subidas": ok, "fallaron": fallaron, "error": ultimo_error,
        }, ttl=30 * 24 * 3600)
    except Exception:
        pass





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
        if sdef.get("kids_juntas"):
            return f"pack de {sdef['kids_juntas']} prendas juntas"
        if sdef.get("pieza_idx") is not None:
            return f"prenda {int(sdef['pieza_idx']) + 1} sola"
        return "producto solo"
    if sdef.get("kids"):
        pk = {0: "de pie riéndose", 1: "corriendo", 2: "recién salido/a del agua",
              3: "sentado/a jugando", 4: "de espaldas", 5: "saltando", 6: "de perfil",
              7: "con la pelota", 8: "manos en la cintura", 9: "primer plano"}
        return "nene/nena " + pk.get(int(sdef.get("force_pose", 0) or 0), "individual")
    poses = {0: "de frente", 6: "de perfil", 3: "de espalda", 2: "sentada", 4: "caminando",
             8: "primer plano", 9: "detalle de prenda", 10: "detalle de espalda",
             11: "fundida con el ambiente", 12: "en el piso", 13: "con utilería"}
    d = ("pose copiada" if sdef.get("pose_txt") and sdef.get("force_pose") is None
         else poses.get(int(sdef.get("force_pose", 0) or 0), "individual"))
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
    steps = _con_posicion(_plan_para_motor(base.get("plan") or _set_plan(bool(base.get("hq"))),
                                           base.get("engine") or (await get_settings()).get("engine", "gemini")))
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


@router.get(ROUTE_PREFIX + "/pose3d.js")
async def pose3d_js() -> Response:
    from pose3d_luma import POSE3D_JS
    return Response(content=POSE3D_JS, media_type="text/javascript",
                    headers={"Cache-Control": "no-store"})


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
    # Las fotos etiquetadas como ESPALDA por la usuaria también valen como vista trasera.
    _tags = [str(t).strip().lower() for t in (payload.get("product_tags") or [])]
    for _i, _t in enumerate(_tags[:len(prod_b64s)]):
        if _es_tag_espalda(_t) and prod_b64s[_i] not in back_b64s:
            back_b64s.append(prod_b64s[_i])
    ficha = await gemini_analyze(prod_b64s, back_b64s[:3])
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
    out: Dict[str, Any] = dict(await list_avatars())
    out["personajes"] = await personajes_como_avatares()
    return out


def k_avficha(avatar_id: str) -> str:
    return _pfx() + f"avficha:{avatar_id}"


@router.get(ROUTE_PREFIX + "/api/avatars/{avatar_id}/ficha")
async def api_avatar_ficha_get(avatar_id: str) -> Dict[str, Any]:
    """Ficha guardada del avatar (cuerpo/edad/altura) para autocompletar el set."""
    return {"ficha": (await kv.get(k_avficha(avatar_id))) or {}}


@router.post(ROUTE_PREFIX + "/api/avatars/{avatar_id}/ficha")
async def api_avatar_ficha_set(avatar_id: str,
                               payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    campos = ("contextura", "busto", "cola", "abdomen", "edad", "altura", "pose", "peinado")
    f = {k: str(payload.get(k, ""))[:40] for k in campos}
    # La TARJETA entera de esa modelo: también su peinado escrito, la pose de sus fotos y
    # las indicaciones (antes sólo se guardaba el cuerpo).
    for k, largo in (("peinado_libre", 200), ("pose_guardada", MIS_POSE_LARGO), ("indicacion", 300)):
        f[k] = str(payload.get(k, ""))[:largo]
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


MIS_POSES_MAX = 40
MIS_POSE_LARGO = 800      # una toma copiada de una foto (cámara + encuadre + pose + cara) no entra en 300


def k_mis_poses() -> str:
    return _pfx() + "poses_propias"


async def mis_poses() -> List[Dict[str, str]]:
    """Las poses que escribió la usuaria, guardadas en su cuenta."""
    d = await kv.get(k_mis_poses())
    if not isinstance(d, list):
        return []
    out = []
    for it in d:
        if isinstance(it, dict) and str(it.get("texto", "")).strip():
            x = {"id": str(it.get("id") or _uuid.uuid4().hex[:8]), "texto": str(it["texto"]).strip()}
            if isinstance(it.get("manual"), dict) and any(v not in (None, "") for v in it["manual"].values()):
                x["manual"] = it["manual"]
            out.append(x)
    return out[:MIS_POSES_MAX]


# MODO MANUAL DE UNA POSE GUARDADA: ella fija hacia dónde mira el cuerpo, el ángulo y el encuadre, y
# de la foto (la guía) se copia SÓLO la pose. Lo medido en automático no alcanzaba: con la foto de
# espaldas el dibujo o el maniquí no muestran para dónde mira el cuerpo, y salía siempre de frente.
VISTAS_POSE = {
    "frente": ("De frente", "DE FRENTE a la cámara: se ve el frente del cuerpo y de la prenda"),
    "tres_cuartos": ("En 3/4", "GIRADA EN 3/4 respecto de la cámara: el cuerpo en diagonal, se ve el frente y un costado"),
    "perfil": ("De perfil", "DE PERFIL a la cámara: se ve el costado del cuerpo"),
    "espalda_34": ("De espaldas en 3/4", "DE ESPALDA a la cámara, girada en 3/4 (se ve la parte de atrás de la "
                   "prenda: la espalda y la cola; el frente del cuerpo NO se ve), con la cabeza girada por encima "
                   "del hombro"),
    "espalda": ("De espaldas", "DE ESPALDA a la cámara (se ve la parte de atrás de la prenda; el frente del "
                "cuerpo NO se ve)"),
}


def _manual_limpio(m: Any) -> Dict[str, Any]:
    m = m if isinstance(m, dict) else {}
    v = str(m.get("vista") or "").strip()
    return {"vista": v if v in VISTAS_POSE else "",
            "camara": cam_idx(m.get("camara")),
            "plano": (int(m["plano"]) % len(PLANO_POOL)
                      if str(m.get("plano") or "").strip().lstrip("-").isdigit() and int(m["plano"]) >= 0 else None),
            "lente": (str(m.get("lente") or "").strip() if str(m.get("lente") or "").strip() in LENTES_FOTO else ""),
            "guia": "sin" if str(m.get("guia") or "") == "sin" else ""}


def _man_activo(m: Dict[str, Any]) -> bool:
    return bool(m.get("vista") or m.get("lente") or m.get("guia") or m.get("camara") is not None or m.get("plano") is not None)


async def _pose_manual(texto: str) -> Dict[str, Any]:
    """Lo fijado a mano para esta pose guardada ({} si no tiene nada fijado)."""
    t = str(texto or "").strip().lower()
    if not t:
        return {}
    for x in await mis_poses():
        if x["texto"].lower() == t and x.get("manual"):
            m = _manual_limpio(x["manual"])
            return m if _man_activo(m) else {}
    return {}


_RE_FRASE_CAMARA = re.compile(
    r"^(?:c[áa]mara|lente|vertical|horizontal|cuadrad|formato|plano|encuadre|toma)\b|% del alto|"
    r"\b(?:contrapicado|picado|lente|mm|ocupa (?:el|la|casi)|entra (?:de|del|desde)|cortad[ao]s? en el borde|"
    r"corta (?:en|a|por)|aire arriba)\b", re.I)


def _pose_sin_camara(pose: str) -> str:
    """La toma copiada empieza con la cámara y el encuadre de la foto ("Cámara a la altura de la cadera…
    Vertical, de la cabeza a mitad del muslo…"). En modo manual esas frases se pelean con lo elegido:
    se sacan y queda sólo la pose."""
    frases = re.split(r"(?<=\.)\s+", str(pose or "").strip())
    quedan = [f for f in frases if f.strip() and not _RE_FRASE_CAMARA.search(f)]
    return " ".join(quedan).strip() or str(pose or "").strip()


@router.post(ROUTE_PREFIX + "/api/poses_propias/{pid}/manual")
async def api_mis_poses_manual(pid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    actual = await mis_poses()
    for x in actual:
        if x["id"] == pid:
            m = _manual_limpio(payload)
            if _man_activo(m):
                x["manual"] = m
            else:
                x.pop("manual", None)
            break
    else:
        raise HTTPException(404, "No encontré esa pose.")
    if not await kv.set(k_mis_poses(), actual):
        raise HTTPException(500, f"No pude guardar (almacenamiento: {kv.backend}).")
    return {"ok": True, "poses": actual}


@router.get(ROUTE_PREFIX + "/api/json_stats")
async def api_json_stats() -> Dict[str, Any]:
    return {"stats": await kv.get(_k_json_stats()) or {}}


@router.get(ROUTE_PREFIX + "/api/poses_propias")
async def api_mis_poses() -> Dict[str, Any]:
    poses = await mis_poses()
    for x in poses:
        g = await _pose_guia(x["texto"])
        x["guia"] = bool(g)
        if g:
            x["personas"] = int(g.get("personas") or 1)
            if g.get("ref"):
                x["ref"] = "data:image/jpeg;base64," + g["ref"]
            if g.get("man"):
                x["man"] = "data:image/jpeg;base64," + g["man"]
            _ph = ((g.get("perfil") or {}).get("source") or {}).get("image_hash")
            if _ph:
                x["perfil_hash"] = _ph
    return {"poses": poses, "max": MIS_POSES_MAX}


@router.post(ROUTE_PREFIX + "/api/poses_propias")
async def api_mis_poses_add(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Agrega una pose por línea. Se puede pegar varias de una."""
    txt = str(payload.get("texto") or "")
    tid = str(payload.get("guia_tmp") or "").strip()
    if tid:     # copiada de una foto: es UNA toma, aunque tenga renglones o punto y coma
        lineas = [_una_linea(txt)] if txt.strip() else []
    else:
        lineas = [ln.strip() for ln in txt.replace(";", "\n").split("\n") if ln.strip()]
    if not lineas:
        raise HTTPException(400, "Escribí al menos una pose.")
    actual = await mis_poses()
    if tid and len(lineas) == 1:
        g = await kv.get(_pfx() + "poseguia_tmp:" + tid)
        if isinstance(g, dict) and g.get("img"):
            await kv.set(_k_pose_guia(lineas[0][:MIS_POSE_LARGO]), g)
    ya = {x["texto"].lower() for x in actual}
    for ln in lineas:
        if len(actual) >= MIS_POSES_MAX:
            break
        if ln.lower() in ya:      # no repetimos la misma pose dos veces en la lista
            continue
        actual.append({"id": _uuid.uuid4().hex[:8], "texto": ln[:MIS_POSE_LARGO]})
        ya.add(ln.lower())
    if not await kv.set(k_mis_poses(), actual):
        raise HTTPException(500, f"No pude guardar tus poses (almacenamiento: {kv.backend}).")
    return {"ok": True, "poses": actual}


@router.post(ROUTE_PREFIX + "/api/pose_desde_lumen")
async def api_pose_desde_lumen(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Una toma de LUMEN como pose propia: el render 3D (maniquí en su lugar, con la cámara de
    la toma) es la guía, y la cámara en palabras va en el texto. Mismo circuito que copiar de
    una foto: queda en una guía temporal hasta que ella toca ➕ Agregar."""
    import lumen as _lumen
    ref = _lumen.leer_ref(payload)
    info = await _lumen.toma_info(*ref) if ref else None
    if not info:
        raise HTTPException(404, "Esa toma de Lumen no tiene imagen: abrila en Lumen y tocá 📸 Guardar las tomas.")
    raw = base64.b64decode(info["img"])
    asp = info["formato"] if info["formato"] in ASPECTOS_VALIDOS else "9:16"
    n = max(1, min(4, int(info.get("personas") or 1)))
    tid = _uuid.uuid4().hex[:12]
    await kv.set(_pfx() + "poseguia_tmp:" + tid,
                 {"img": _compress_ref(raw, max_dim=1280, q=86), "aspect": asp, "personas": n,
                  "ref": _compress_ref(raw, max_dim=360, q=80), "man": _compress_ref(raw, max_dim=240, q=78),
                  "encuadre": None, "perfil": None, "lumen": {"proyecto": ref[0], "toma": ref[1]}}, ttl=7200)
    pose = _una_linea(f"Toma de Lumen «{info['nombre']}»: la pose, el encuadre y el punto de vista "
                      f"del maniquí de la guía. {info['camara']}")
    return {"pose": pose[:MIS_POSE_LARGO], "guia_tmp": tid, "guia_preview": "data:image/jpeg;base64," + _compress_ref(raw, max_dim=480, q=80),
            "aspect": asp, "personas": n}


def _una_linea(t: str) -> str:
    """Una toma en un solo renglón (los renglones pasan a '. ' y el punto y coma a ', ')."""
    t = re.sub(r"\s*\n+\s*", ". ", str(t or "").strip()).replace(";", ",")
    return re.sub(r"\.\s*\.", ".", re.sub(r"[ \t]+", " ", t)).strip()


def _k_pose_guia(texto: str) -> str:
    """La guía (maniquí) de una pose guardada, por el TEXTO de la pose: así cualquier toma
    que use esa pose la encuentra, sin cambiar cómo viajan las poses por el set."""
    h = hashlib.sha1(str(texto or "").strip().lower().encode("utf-8")).hexdigest()[:20]
    return _pfx() + "poseguia:" + h


def _aspect_cercano(w: int, h: int) -> str:
    r = (w / float(h)) if h else 0.8
    return min(ASPECTOS_VALIDOS, key=lambda a: abs(RATIO_NUM.get(a, 1.0) - r))


async def _pose_guia(texto: str) -> Optional[Dict[str, str]]:
    if not str(texto or "").strip():
        return None
    try:
        g = await kv.get(_k_pose_guia(texto))
    except Exception:
        return None
    return g if isinstance(g, dict) and g.get("img") else None


_MANIQUI_PROMPT = (
    "Recreá EXACTAMENTE esta misma foto pero con CADA persona reemplazada por un MANIQUÍ de "
    "estudio gris claro, liso y mate, SIN cara (sin ojos, nariz ni boca), sin pelo, sin ropa, "
    "sin tatuajes, sin maquillaje ni accesorios (si hay varias personas, un maniquí por cada "
    "una, en el mismo lugar y la misma pose que cada una). Cada maniquí tiene EXACTAMENTE la misma pose: "
    "misma orientación del cuerpo (si la persona está DE ESPALDAS, el maniquí también, y que se note: omóplatos, columna y cola hacia la cámara), mismo torso, mismas piernas y pies, mismos brazos y manos "
    "con los mismos dedos, misma inclinación y giro de la cabeza. MISMA cámara: misma altura, "
    "ángulo, distancia y lente; MISMO encuadre y recorte, el maniquí en el mismo lugar y del "
    "mismo tamaño en el cuadro. Lo que sostiene el cuerpo (piso, cama, silla) queda como una "
    "forma gris simple en el mismo lugar. El resto del fondo, liso gris medio. Sin texto."
)


_MANIQUI_PROMPT_EN = (
    "Recreate EXACTLY this same photo with EACH person replaced by a smooth matte light-grey "
    "studio MANNEQUIN: no face (no eyes, nose or mouth), no hair, no clothes, no tattoos, no "
    "makeup, no accessories (one mannequin per person, in the same place and pose). Each "
    "mannequin keeps EXACTLY the same pose: body orientation (if the person is seen FROM BEHIND, so is the mannequin, and it must show: shoulder blades, spine and bottom towards the camera), torso, legs and feet, arms, hands "
    "and fingers, head tilt and turn. SAME camera: height, angle, distance and lens; SAME framing "
    "and crop, each mannequin in the same place and size in the frame. What supports the body "
    "(floor, sofa, bed, chair) stays as a simple grey shape in the same place. Everything else is "
    "a plain mid-grey background. No text."
)
_MANIQUI_DESDE_TEXTO = (
    "Foto de estudio de referencia de pose: uno o varios MANIQUÍES de estudio gris claro, lisos y "
    "mate, sin cara, sin pelo y sin ropa, sobre fondo gris liso; los muebles que sostienen el "
    "cuerpo, como formas grises simples. Reproducí EXACTAMENTE esta toma (cámara, encuadre, pose "
    "y ubicación de cada uno). Sin texto. LA TOMA: "
)


_POSE_FOTO_SYS = (
    "Sos director de fotografía. Mirás una foto de referencia y escribís LA TOMA COMPLETA para "
    "que otra IA la reproduzca IGUAL con otra modelo, otra ropa y otro lugar: misma cámara, "
    "misma distancia, mismo encuadre, misma pose, misma postura y mismo gesto. NO describas "
    "quién es la persona (cara, pelo, cuerpo), ni la ropa, ni el lugar, ni los colores. "
    "Describí todo esto, concreto y medible:\n"
    "1) CÁMARA: altura (a la altura del piso, de las rodillas, de la cintura, del pecho, de los "
    "ojos, por encima de la cabeza), ángulo (picado, contrapicado, a nivel, cenital), desde qué "
    "lado (de frente, 3/4 desde su izquierda o derecha, de costado, desde atrás), distancia "
    "aproximada en metros y lente (gran angular, normal 50 mm, teleobjetivo).\n"
    "2) ENCUADRE: vertical u horizontal; qué entra del cuerpo y dónde corta (ej.: de la cabeza a "
    "mitad del muslo); dónde está ella en el cuadro (centrada, tercio izquierdo…) y cuánto del "
    "alto ocupa; si la cabeza queda cerca del borde de arriba o con aire.\n"
    "3) CUERPO: orientación respecto de la cámara; parada, sentada, arrodillada, acostada boca "
    "arriba o boca abajo, de costado; dónde carga el peso; torso (inclinado, girado, arqueado), "
    "hombros y cadera; qué hace cada pierna y cada pie.\n"
    "4) BRAZOS Y MANOS: qué hace el brazo y la mano IZQUIERDA de ella y la DERECHA, dónde se "
    "apoyan o qué tocan, dedos.\n"
    "5) CABEZA Y CARA: giro e inclinación de la cabeza, mentón, hacia dónde mira (a cámara, "
    "afuera del cuadro, abajo), ojos (abiertos, entrecerrados), boca (cerrada, entreabierta, "
    "sonrisa) y la expresión en pocas palabras.\n"
    "6) APOYOS: si se apoya en algo, el TIPO de apoyo y su altura (\"apoyada en una superficie a "
    "la altura de la cadera\"), no el mueble.\n"
    "7) OBJETOS en las manos: nombralos SIN palabras ambiguas y con su forma (\"el tubo de un "
    "teléfono antiguo con cable, apoyado en la oreja\", nunca \"auricular\"; \"una taza de té "
    "con su platito\"; \"un libro abierto\"), y cuánto se juntan o se apoyan las cabezas.\n"
    "8) MOVIMIENTO Y SOLTURA: qué se mueve (pelo o tela al viento, caminando, girando, estirándose), qué "
    "tan sueltos están los brazos y las manos (codos flexionados, muñecas relajadas, dedos abiertos o "
    "cerrados), dónde carga el peso y la energía del instante (relajada, eufórica, libre).\n"
    "Si hay VARIAS personas: la cámara y el encuadre una vez, y después cada persona de "
    "IZQUIERDA a DERECHA (\"la de la izquierda…; la del centro…; la de la derecha…\"), con su "
    "pose, dónde está y cómo se tocan o se apoyan entre ellas.\n"
    "Castellano rioplatense, en un solo párrafo corrido, de máximo 750 caracteres, sin palabras "
    "sugerentes (es un catálogo de ropa). "
    'Respondé SOLO JSON: {"pose": "...", "personas": cantidad de personas en la foto, '
    '"vista": "frente" | "espalda" | "perfil" (qué lado del cuerpo de la modelo principal ve la '
    'cámara: "espalda" si se ve la parte de atrás del torso, aunque gire la cabeza), '
    '"perfil": PERFIL FOTOGRÁFICO de la foto. Son ESTIMACIONES: si algo no se puede ver, poné "unknown" '
    '(o null en los números) y bajá su confianza; no inventes. {'
    '"framing": {"shot_type": "extreme_close_up"|"close_up"|"medium"|"three_quarter"|"full_body"|"wide", '
    '"crop": "head"|"chest"|"waist"|"mid_thigh"|"knees"|"feet"|"none"}, '
    '"camera": {"height": "ground"|"knee"|"waist"|"chest"|"eye"|"above_head"|"drone", '
    '"vertical_angle": "very_low"|"low"|"level"|"high"|"very_high", '
    '"elevation_deg": grados (+ desde arriba, - desde abajo), '
    '"horizontal_angle": "front"|"three_quarter"|"side"|"back"|"three_quarter_back", '
    '"azimuth_deg": grados (0 = de frente, 90 = de costado, 180 = de espaldas), '
    '"roll_deg": grados que está girada la cámara respecto del horizonte (0 si está derecha), '
    '"distance_class": "very_close"|"close"|"medium"|"far"|"very_far", "distance_m": número o null, '
    '"focal_length_mm": número o null, "perspective": "wide"|"natural"|"compressed"}, '
    '"pose": {"weight": "left_leg"|"right_leg"|"both"|"sitting"|"lying"|"airborne", '
    '"motion": "still"|"walking"|"running"|"jumping"|"spinning"|"stretching", '
    '"arms": texto corto, "gaze": "camera"|"away"|"down"|"up"|"eyes_closed"}, '
    '"focus": {"target": texto corto, "depth_of_field": "shallow"|"moderate"|"deep", '
    '"background_blur": "none"|"subtle"|"strong"}, '
    '"lighting": {"direction": texto corto, "hardness": "soft"|"hard", "contrast": "low"|"moderate"|"high", '
    '"color_temperature": "warm"|"neutral"|"cool", "shadow_direction": texto corto}, '
    '"background": texto corto (qué se ve detrás: cielo, mar, pared…), '
    '"people": [una entrada por persona de IZQUIERDA a DERECHA: {"position": "left"|"center"|"right", '
    '"pose": texto corto, "gaze": "camera"|"away"|"down"|"up"|"eyes_closed"}], '
    '"confidence": {"framing": 0..1, "camera": 0..1, "pose": 0..1, "focus": 0..1, "lighting": 0..1}}}'
)


_FP_LOCKS: Dict[str, asyncio.Lock] = {}


def _k_fotoperfil(image_hash: str) -> str:
    return _pfx() + "fotoperfil:" + _fp.cache_key(image_hash)


async def _analizar_referencia(img: str, image_hash: str) -> Dict[str, Any]:
    """El análisis visual de la referencia (Claude; Gemini si no hay Claude), UNA vez por foto: con la
    misma huella, la misma versión del esquema y de los analizadores, se devuelve el guardado sin
    volver a pagar. Dos pedidos simultáneos de la misma foto esperan al primero (no se paga doble)."""
    k = _k_fotoperfil(image_hash)
    lock = _FP_LOCKS.setdefault(k, asyncio.Lock())
    async with lock:
        try:
            c = await kv.get(k)
        except Exception:
            c = None
        if isinstance(c, dict) and str(c.get("pose", "")).strip():
            return {**c, "desde_cache": True}
        pose, personas, vista = "", 1, ""
        perfil: Optional[Dict[str, Any]] = None
        if _claude.disponible():
            try:
                data, costo = await _claude.pedir_json(
                    _POSE_FOTO_SYS, [{"text": "FOTO DE REFERENCIA:"}, _img_part(img)],
                    max_tokens=5000, esfuerzo="medium")
                pose = str(data.get("pose") or "").strip()
                personas = data.get("personas")
                vista = str(data.get("vista") or "").strip().lower()
                perfil = data.get("perfil") if isinstance(data.get("perfil"), dict) else None
                await budget_record("pose_foto", _claude.MODELO, costo, 1, note="copiar pose de una foto")
            except _claude.ClaudeNoDisponible as e:
                print(f"[imagenes_ia][pose-foto] Claude: {e}")
        if not pose:
            api_key = await _current_api_key()
            if not api_key:
                raise HTTPException(500, "Falta la API key de Google.")
            body = {"contents": [{"role": "user", "parts": [{"text": _POSE_FOTO_SYS}, _img_part(img)]}],
                    "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
            async with httpx.AsyncClient(timeout=90) as cli:
                r = await cli.post(ANALYZE_ENDPOINT, json=body,
                                   headers={"x-goog-api-key": api_key, "Content-Type": "application/json"})
            if r.status_code != 200:
                raise HTTPException(502, f"No pude leer la pose: {r.text[:200]}")
            try:
                txt = "".join(pt.get("text", "") for pt in r.json()["candidates"][0]["content"]["parts"])
                a, b = txt.find("{"), txt.rfind("}")
                _d = json.loads(txt[a:b + 1])
                pose = str(_d.get("pose") or "").strip()
                personas = _d.get("personas")
                vista = str(_d.get("vista") or "").strip().lower()
                perfil = _d.get("perfil") if isinstance(_d.get("perfil"), dict) else None
            except Exception:
                pose = ""
            await budget_record("pose_foto", ANALYZE_MODEL, 0.002, 1, note="copiar pose de una foto")
        out = {"pose": pose, "personas": personas, "vista": vista, "claude": perfil,
               "image_hash": image_hash, "schema_version": _fp.SCHEMA_VERSION,
               "analyzer_versions": dict(_fp.ANALYZER_VERSIONS)}
        if pose:
            try:
                await kv.set(k, out)
            except Exception:
                pass
        return out


@router.post(ROUTE_PREFIX + "/api/pose_desde_foto")
async def api_pose_desde_foto(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Copiar una pose de una foto: el inspector la mira y la escribe como pose de texto.
    La foto NO va al generador (copiaría también la persona, la ropa y el fondo)."""
    raw = _strip_data_url(str(payload.get("imagen") or ""))
    if not raw:
        raise HTTPException(400, "Subí una foto con la pose.")
    try:
        img = _compress_ref(base64.b64decode(raw), max_dim=1280, q=90)
    except Exception:
        raise HTTPException(400, "No pude leer esa imagen.")
    # HUELLA: la del archivo original (la manda el navegador) o, si no vino, la de la imagen recibida.
    image_hash = re.sub(r"[^0-9a-f]", "", str(payload.get("image_hash") or "").lower())[:64] or \
        hashlib.sha256(base64.b64decode(raw)).hexdigest()
    an = await _analizar_referencia(img, image_hash)
    pose, personas, vista, perfil = an["pose"], an["personas"], an["vista"], an.get("claude")
    _desde_cache = bool(an.get("desde_cache"))
    if not pose:
        raise HTTPException(422, "No pude describir la pose de esa foto. Probá con otra más clara.")
    # LA GUÍA: un maniquí gris en la misma pose, cámara y encuadre (1K, una sola vez). Seedream
    # copia la composición de una imagen: con el maniquí copia la toma sin copiar a la persona.
    # Una sola línea: si viniera en renglones (1) cámara, 2) encuadre…), al guardarla se partía
    # en varias poses y se perdía su foto y su guía.
    pose = _una_linea(pose)
    # Que la toma de espalda SE SEPA (usa la foto de espalda de la prenda y sus reglas), y que una
    # de frente que dice "espalda arqueada" no pase por de espalda.
    if vista == "espalda" and not _toma_atras_pose(pose):
        pose = "DE ESPALDA a la cámara (se ve la parte de atrás de la prenda): " + pose
    elif vista == "perfil" and "perfil" not in pose.lower():
        pose = "DE PERFIL: " + pose
    out: Dict[str, Any] = {"ok": True, "pose": pose[:MIS_POSE_LARGO], "vista": vista}
    try:
        _im = Image.open(io.BytesIO(base64.b64decode(img)))
        asp = _aspect_cercano(*_im.size)
        st = await get_settings()
        man, via = None, ""
        # MANIQUÍ 3D hecho en el navegador (MediaPipe + three.js): gratis y sin filtros.
        _g3 = _strip_data_url(str(payload.get("guia_3d") or ""))
        if _g3:
            man, via = base64.b64decode(_g3), "3d"
            try:
                # Manda la cuenta de Claude (mira la foto entera); la del detector del navegador
                # se equivoca con gente encimada y la foto grupal no aparecía para el grupo.
                personas = int(personas or payload.get("personas") or 1)
            except (TypeError, ValueError):
                pass
        # La guía de IA ya hecha para esta misma foto: no se vuelve a pagar.
        if man is None:
            try:
                _gc = await kv.get(_k_fotoperfil(image_hash) + ":guia")
                if isinstance(_gc, dict) and _gc.get("img"):
                    man, via = base64.b64decode(_gc["img"]), str(_gc.get("via") or "cache")
            except Exception:
                pass
        _guia_ia_nueva = man is None
        # 1) Nano Banana con la foto. Con fotos de personas reales (y ropa transparente)
        #    su filtro a veces bloquea "reemplazá a la persona": PROHIBITED_CONTENT.
        try:
            if man is not None:
                raise StopIteration     # ya hay guía 3D: no se paga ninguna IA
            man = await gemini_generate([{"text": _MANIQUI_PROMPT}, _img_part(img)], st, asp, "1K",
                                        save_prompt=False)
            via = "gemini"
            await budget_record("pose_guia", str(st.get("model") or MODEL_ID),
                                _pricing(st)["1K"], 1, note="maniquí guía de una pose")
        except StopIteration:
            pass
        except HTTPException as e1:
            print(f"[imagenes_ia][pose-guia] Gemini: {getattr(e1, 'detail', e1)}")
        # 2) Seedream (fal) con la misma foto: sin el filtro de Google.
        if man is None and str(st.get("fal_api_key") or FAL_API_KEY).strip():
            try:
                man = await fal_generate([{"text": _MANIQUI_PROMPT_EN}, _img_part(img)], st, asp, "1K",
                                         str(st.get("flux_edit_model") or "bytedance/seedream/v5/pro/edit"))
                via = "seedream"
                await budget_record("pose_guia", "seedream", float(st.get("precio_flux", 0.05) or 0.05),
                                    1, note="maniquí guía de una pose (Seedream)")
            except Exception as e2:
                print(f"[imagenes_ia][pose-guia] Seedream: {getattr(e2, 'detail', e2)}")
        # 3) Sin la foto: el maniquí dibujado sólo desde la descripción (menos exacto).
        if man is None:
            man = await gemini_generate([{"text": _MANIQUI_DESDE_TEXTO + pose}], st, asp, "1K",
                                        save_prompt=False)
            via = "texto"
            await budget_record("pose_guia", str(st.get("model") or MODEL_ID),
                                _pricing(st)["1K"], 1, note="maniquí guía desde el texto")
        man_b64 = _compress_ref(man, max_dim=1024, q=88)
        if _guia_ia_nueva and via in ("gemini", "seedream", "texto"):
            try:
                await kv.set(_k_fotoperfil(image_hash) + ":guia", {"img": man_b64, "via": via})
            except Exception:
                pass
        try:
            n_pers = max(1, min(6, int(personas or 1)))
        except (TypeError, ValueError):
            n_pers = 1
        # PERFIL FOTOGRÁFICO (esquema 1.0): esqueleto por persona del navegador + análisis visual.
        _w0, _h0 = _im.size
        try:
            _wi, _hi = int(payload.get("img_w") or _w0), int(payload.get("img_h") or _h0)
        except (TypeError, ValueError):
            _wi, _hi = _w0, _h0
        _perfil_ref = _fp.build_photo_profile(
            image_hash=image_hash, width=_wi, height=_hi,
            sujetos=payload.get("sujetos") if isinstance(payload.get("sujetos"), list) else None,
            claude=perfil, personas_claude=(int(personas) if str(personas or "").isdigit() else None),
            medidas=payload.get("medidas") if isinstance(payload.get("medidas"), dict) else None)
        # Correcciones que ella ya había hecho a ESTA foto: se conservan (mandan sobre lo estimado).
        try:
            _prev = await kv.get(_k_fotoperfil(image_hash) + ":perfil")
            if isinstance(_prev, dict) and (_prev.get("user_overrides") or _prev.get("modules")):
                _perfil_ref = _fp.aplicar_correcciones(
                    _perfil_ref, {k: v.get("value") for k, v in (_prev.get("user_overrides") or {}).items()},
                    _prev.get("modules"))
                _perfil_ref["user_overrides"] = _prev.get("user_overrides") or {}
        except Exception:
            pass
        try:
            await kv.set(_k_fotoperfil(image_hash) + ":perfil", _perfil_ref)
        except Exception:
            pass
        tid = _uuid.uuid4().hex[:12]
        # Se guardan la guía (maniquí) y la FOTO de referencia chica, para elegir la pose
        # tocando su imagen.
        await kv.set(_pfx() + "poseguia_tmp:" + tid,
                     {"img": man_b64, "aspect": asp, "personas": n_pers,
                      "ref": _compress_ref(base64.b64decode(img), max_dim=360, q=80),
                      "man": _compress_ref(man, max_dim=240, q=78),
                      # con esqueletos encimados la escala medida no sirve: no se manda en números
                      "encuadre": (None if (_perfil_ref.get("confidence") or {}).get("framing_measured", 1) < 0.5
                                   else _encuadre_guia(payload.get("encuadre_guia"))),
                      "perfil": _perfil_ref}, ttl=7200)
        out.update({"guia_tmp": tid, "guia_preview": "data:image/jpeg;base64," + man_b64,
                    "aspect": asp, "personas": n_pers, "guia_via": via,
                    "perfil_id": _perfil_ref.get("profile_id"), "perfil_avisos": _perfil_ref.get("warnings", [])[:6],
                    "image_hash": image_hash,
                    "desde_cache": _desde_cache})
    except Exception as e:      # sin guía igual queda la pose escrita
        out["guia_error"] = str(getattr(e, "detail", e))[:200]
    return out


@router.get(ROUTE_PREFIX + "/api/foto_perfil/{image_hash}")
async def api_foto_perfil(image_hash: str) -> Dict[str, Any]:
    """El perfil fotográfico guardado de una referencia (por su huella)."""
    h = re.sub(r"[^0-9a-f]", "", image_hash.lower())[:64]
    p = await kv.get(_k_fotoperfil(h) + ":perfil") if h else None
    if not isinstance(p, dict):
        raise HTTPException(404, "No hay perfil guardado para esa foto.")
    return {"ok": True, "perfil": p}


@router.post(ROUTE_PREFIX + "/api/foto_perfil/{image_hash}")
async def api_foto_perfil_corregir(image_hash: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Correcciones a mano del perfil (y módulos prendidos/apagados): quedan guardadas con lo que se había
    estimado antes y mandan en todas las tomas que usen esa foto."""
    h = re.sub(r"[^0-9a-f]", "", image_hash.lower())[:64]
    k = _k_fotoperfil(h) + ":perfil"
    p = await kv.get(k) if h else None
    if not isinstance(p, dict):
        raise HTTPException(404, "No hay perfil guardado para esa foto.")
    p = _fp.aplicar_correcciones(p, payload.get("cambios") if isinstance(payload.get("cambios"), dict) else {},
                                 payload.get("modulos") if isinstance(payload.get("modulos"), dict) else None,
                                 cuando=time.strftime("%Y-%m-%d %H:%M"))
    await kv.set(k, p)
    return {"ok": True, "perfil": p}


@router.get(ROUTE_PREFIX + "/api/foto_perfil_editables")
async def api_foto_perfil_editables() -> Dict[str, Any]:
    return {"editables": {k: list(v) if v else None for k, v in _fp.EDITABLES.items()},
            "modulos": list(_fp.MODULOS)}


@router.delete(ROUTE_PREFIX + "/api/poses_propias/{pid}")
async def api_mis_poses_del(pid: str) -> Dict[str, Any]:
    for x in await mis_poses():
        if x["id"] == pid:
            try:
                await kv.delete(_k_pose_guia(x["texto"]))
            except Exception:
                pass
    actual = [x for x in await mis_poses() if x["id"] != pid]
    await kv.set(k_mis_poses(), actual)
    return {"ok": True, "poses": actual}


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
    ult = await kv.get(K_DRIVE_ULT)
    return {
        "connected": bool(s.get("refresh_token")),
        "email": s.get("email", ""),
        "folder_name": s.get("folder_name", DRIVE_FOLDER_NAME),
        "redirect_uri": _drive_redirect_uri(request),
        "client_id_set": bool(GOOGLE_CLIENT_ID),
        # Estar conectado no quiere decir que la última subida haya andado.
        "ultima_subida": ult if isinstance(ult, dict) else None,
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


async def _flux_en(txt: str) -> str:
    """Un pedido armado en castellano (grupal, producto) para Seedream: traducido al inglés y
    dentro del tope. Antes viajaba en castellano y de hasta 9.000 caracteres: Seedream lo
    rechazaba ("Error validating the input") o ignoraba lo que no entendía."""
    en = (await _al_ingles({"pedido": txt})).get("pedido") or txt
    if len(en) > FLUX_PROMPT_MAX:
        corte = en[:FLUX_PROMPT_MAX]
        en = corte[:max(corte.rfind(". "), corte.rfind("\n"), FLUX_PROMPT_MAX // 2) + 1]
    return _sanear_prompt_fal(en)


def _cargar_ctx(params: Dict[str, Any]) -> None:
    TOMA_CTX.set({"zona": bool(_zona(params)), "zona_lbl": _zona(params),
                  "abajo": _prenda_de_abajo(params),
                  "fondo": " ".join(str(params.get(k, "")) for k in ("fondo", "aclaraciones")).lower(),
                  "kids": _es_kids(params),
                  "hombre": (str(params.get("kids_quien", "")).strip().lower() in ("nene", "nenes")
                             if _es_kids(params) else
                             _es_hombre(str(params.get("_genero") or params.get("genero") or "")))})


def _con_no_tiene(params: Dict[str, Any]) -> Dict[str, Any]:
    """Los motores le agregan a la prenda lo típico de su categoría (costura en la copa,
    moño, encaje...). La lista 'La prenda NO tiene' de la ficha, editable por ella, viaja
    en las aclaraciones porque es lo único que llega a todos los motores y builders."""
    nt = _lista_no_tiene(params.get("no_tiene"))
    if not nt or "LA PRENDA NO TIENE" in str(params.get("aclaraciones", "")):
        return params
    prm = dict(params)
    prev = str(prm.get("aclaraciones", "")).strip()
    prm["aclaraciones"] = ((prev.rstrip(" .") + ". ") if prev else "") + (
        f"LA PRENDA NO TIENE (no agregar nada de esto, aunque sea típico de este tipo de "
        f"prenda): {nt}.")
    return prm


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
    params = _con_no_tiene(payload.get("params", {}) or {})
    # Lo que ELLA escribió para esta toma (en castellano) y la cara que viaja a Seedream:
    # Claude pone el pedido primero y después revisa la foto contra esto.
    _pedido_claude = ""
    _cara_claude = ""
    _rev_claude: Optional[Dict[str, Any]] = None
    # NENAS / NENES: con modelo si la prenda cubre (pijama, poncho, remera...). Una
    # malla o ropa interior pedida con modelo se convierte acá en toma de producto, y
    # el motivo viaja en el aviso y en el diagnóstico.
    # El programa no decide qué contenido puede generar el motor.
    # Se respeta el mode solicitado por el usuario y el motor de IA aplica su propio safety.
    _kids_forzado = False                 # la toma nunca se convierte en producto por ser kids
    _kids_m = _kids_modelo_ok(params)     # kids con modelo → prompt de chicos (no de adultos)
    # Formato / tamaño / estilo del pedido (con defaults de Ajustes)
    aspect = str(payload.get("aspect") or settings.get("aspect_ratio", "4:5"))
    try:
        paneles = max(1, min(8, int(payload.get("paneles", 1) or 1)))
    except (TypeError, ValueError):
        paneles = 1
    image_size = str(payload.get("image_size")
                     or settings.get("image_size", "4K")).upper()
    style = str(payload.get("style")
                or settings.get("default_style", "instagram_real"))
    params = {**params, "_estilo": style}      # para la cámara ✨ automática
    # PAREO SÓLO EN LAS TOMAS QUE LO PIDEN: si va en todas parece que se vende en conjunto.
    # Va cuando la pose de ESTA toma dice "pareo"; en las demás, la malla sola.
    _pareo = str(params.get("pareo") or "").strip()
    if _pareo:
        _pz = str(params.get("piezas") or "").strip()
        # Va cuando SU pose lo nombra; si no escribió pose (poses automáticas o de la
        # escena), va en una toma sí y una no — antes no salía en NINGUNA y el pareo elegido
        # se perdía.
        _up = str(params.get("pose") or "").strip().lower()
        _ixp = payload.get("force_pose") if payload.get("force_pose") is not None else payload.get("pose_offset", 0)
        try:
            _ixp = int(_ixp or 0)
        except (TypeError, ValueError):
            _ixp = 0
        if "pareo" in _up or (not _up and _ixp % 2 == 1):
            params["piezas"] = ((_pz + "; ") if _pz else "") + (
                "además lleva " + _pareo + ". El pareo se lleva puesto SOBRE LOS HOMBROS como una camisa "
                "larga abierta, con mangas, suelto: NUNCA cosido, atado ni pegado a la malla, NUNCA como "
                "pollera ni falda desde la cintura. La malla se ve entera por la abertura (NO es parte del producto)")
        else:
            params["piezas"] = ((_pz + "; ") if _pz else "") + "sin pareo ni salida de playa: la malla sola"
    reframe = payload.get("reframe") or None
    if reframe and reframe not in ASPECTOS_VALIDOS:
        reframe = None
    # EL ENCUADRE QUE ELIGIÓ ELLA MANDA: "cuerpo entero", "plano medio", "plano americano"…
    # se convierte en el encuadre fijo de la toma (el mismo camino que el encuadre por zona),
    # así la rotación automática de planos no le pone otro encima.
    if not _zona(params):
        # Si no escribió encuadre pero SU pose nombra el plano ("…, cuerpo entero"), ese plano.
        _zq = (_zona_de_encuadre(str(params.get("encuadre", "")))
               or _zona_de_encuadre(str(params.get("pose", "")), estricto=True))
        if _zq:
            params = {**params, "encuadre_zona": _zq}
    _cargar_ctx(params)

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
    _tags_in = list(payload.get("product_tags") or [])
    if len(prods) > 6:
        # Tope de 6: antes se cortaban las ÚLTIMAS y la de espalda (siempre última) se
        # perdía sin aviso. Ahora se van primero las de frente repetidas; la primera, las de
        # espalda y la de color se quedan.
        _t7 = [str(x or "") for x in _tags_in] + [""] * (len(prods) - len(_tags_in))
        _nbl7 = int(payload.get("n_back_last", 0) or 0)
        _cuida = {0} | {j for j in range(len(prods))
                        if _es_tag_espalda(_t7[j]) or _t7[j].lower() == "color"
                        or (_nbl7 and j >= len(prods) - _nbl7)}
        _sobran = len(prods) - 6
        _fuera = [j for j in range(len(prods) - 1, -1, -1) if j not in _cuida][:_sobran]
        _keep = [j for j in range(len(prods)) if j not in _fuera][:6]
        prods = [prods[j] for j in _keep]
        _tags_in = [_t7[j] for j in _keep]
    prods = prods[:6]
    # ETIQUETAS que escribe la usuaria para cada foto ("frente", "espalda", "detalle de
    # la tela"...). Con ellas ni el generador ni el inspector tienen que adivinar qué
    # muestra cada imagen, que era la causa de que la espalda aplanara la prenda o de
    # que el inspector leyera una foto de perfil como "diseño distinto".
    prod_tags = [_ETIQUETAS_PRENDA.get(str(t).strip().lower(), str(t).strip())
                 for t in _tags_in][:6]
    prod_tags += [""] * (len(prods) - len(prod_tags))
    # ¿Tenemos foto de la espalda? Entonces el diseño de atrás puede ser distinto del de
    # adelante y hay que explicarle al motor dónde cambia (costuras de hombros y costados).
    _hay_espalda = (bool(payload.get("product_images_back"))
                    or any(_es_tag_espalda(t) for t in prod_tags))
    if _hay_espalda:
        params = {**params, "_espalda_distinta": True}
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
    if (mode == "on_model" and cons_b64s and not use_flux
            and str(settings.get("anclas_cara", "si")).lower() not in ("no", "0", "off", "false")):
        # LAS TOMAS PREVIAS VIAJAN SÓLO COMO CARA. Iban enteras (hasta 8, con su fondo y su
        # encuadre) y Nano Banana copiaba ese fondo y ese ángulo aunque el texto pidiera otra
        # parte del lugar: todo el set salía en el mismo rincón y desde la misma cámara. Con
        # el recorte de la cara (pelo y maquillaje incluidos) no hay fondo que copiar.
        _caras_prev = []
        for _cb in cons_b64s[:4]:
            _cr = await _recorte_cara_toma(_cb)
            if _cr:
                _caras_prev.append(_cr)
            if len(_caras_prev) >= 2:
                break
        if _caras_prev:
            cons_b64s = _caras_prev
            n_cons = len(cons_b64s)
            params = {**params, "_anclas_cara": True}
    if mode == "on_model":
        avatar_id = payload.get("avatar_id")
        con_avatar = bool(avatar_id) and str(avatar_id).lower() not in ("none", "null", "")
        av = await get_avatar(avatar_id) if con_avatar else None
        if con_avatar and (not av or not av.get("ref_b64")):
            raise HTTPException(400, "Elegí un avatar válido y lockeado.")
        fp = payload.get("force_pose")
        fp = int(fp) if fp is not None else None
        # POSE COPIADA DE UNA FOTO: si la pose escrita tiene su maniquí guía, la toma usa
        # además su formato (una referencia cuadrada sale cuadrada).
        _guia = await _pose_guia(str(params.get("pose", ""))) if fp is None else None
        # ¿Ella eligió el ángulo A MANO? (después se completa con el del perfil, y ya no se sabría)
        _cam_mano = cam_idx(payload.get("camara")) is not None
        # MODO MANUAL de la pose guardada: lo que ella fijó manda; de la guía sale sólo la pose.
        _man = (await _pose_manual(str(params.get("pose", "")))) if fp is None else {}
        if _man.get("camara") is not None and not _cam_mano:
            payload = {**payload, "camara": _man["camara"]}
            _cam_mano = True
        if _guia and _man.get("guia") == "sin":
            # "Sin guía": la imagen guía (dibujo, calco o maniquí) arrastraba la foto a su ángulo. Va sólo
            # con la ficha técnica y la pose escrita; del guía queda sólo el formato.
            if _guia.get("aspect") in ASPECTOS_VALIDOS and paneles <= 1:
                aspect = _guia["aspect"]
            _guia = None
        if _guia:
            # El perfil MÁS RECIENTE de esa foto (con las correcciones a mano), no la copia de cuando se guardó.
            _h = ((_guia.get("perfil") or {}).get("source") or {}).get("image_hash")
            if _h:
                try:
                    _pv = await kv.get(_k_fotoperfil(_h) + ":perfil")
                    if isinstance(_pv, dict):
                        _guia = {**_guia, "perfil": _pv}
                except Exception:
                    pass
            if not _fp.modulo_on(_guia.get("perfil"), "framing"):
                _guia = {**_guia, "encuadre": None}
            if _man and isinstance(_guia.get("perfil"), dict):
                _pm = dict(_guia["perfil"])
                if _man.get("plano") is not None:
                    _pm["modules"] = {**(_pm.get("modules") or {}), "framing": False}
                    _guia = {**_guia, "encuadre": None}
                if _man.get("lente"):
                    _pm["camera"] = {k: v for k, v in (_pm.get("camera") or {}).items()
                                     if k != "focal_length_estimate_mm"}
                if _man.get("vista"):
                    # hacia dónde mira el cuerpo lo dice ella: sin el costado ni el torso medidos
                    _pm["camera"] = {k: v for k, v in (_pm.get("camera") or {}).items()
                                     if k not in ("azimuth", "horizontal_angle")}
                    _pm["subjects"] = [{k: v for k, v in s.items() if k not in ("torso_orientation", "head_orientation")}
                                       for s in (_pm.get("subjects") or []) if isinstance(s, dict)]
                _guia = {**_guia, "perfil": _pm}
        if _guia and _guia.get("aspect") in ASPECTOS_VALIDOS and paneles <= 1:
            aspect = _guia["aspect"]
        if _guia:
            # CON GUÍA DE FOTO, LA CÁMARA ES LA DE LA FOTO: nada de ángulo/rincón/plano de la
            # rotación, ni lente o movimiento automáticos (el "plano medio" del 90 mm, el "2 m detrás"
            # de la toma de espaldas). Antes se respetaba sólo el formato y no la distancia ni el gesto.
            # Lo elegido a mano (cámara/película, ajustes) sigue.
            params = {**params,
                      "lente": "" if str(params.get("lente") or "") == "auto" else params.get("lente", ""),
                      "movimiento": "" if str(params.get("movimiento") or "") == "auto" else params.get("movimiento", ""),
                      "encuadre": "", "encuadre_zona": ""}
            # El ángulo ELEGIDO A MANO (el selector de la pose o de la tarjeta) sí se respeta: es la
            # forma de forzar "desde el piso" o "muy cerca" cuando la guía sola no alcanza.
            payload = {**payload, "camara_auto": None}
            _cargar_ctx(params)
            # Con PERFIL FOTOGRÁFICO, el ángulo sale de él (salvo que ella eligiera uno a mano).
            if cam_idx(payload.get("camara")) is None and _cam_de_perfil(_guia.get("perfil")) is not None:
                _cp = _cam_de_perfil(_guia.get("perfil"))
                # El ángulo MEDIDO de su foto se usa tal cual, también en lencería y mallas: es lo que
                # ella pidió al copiar la foto. Si Google lo bloquea, entra el reintento de siempre
                # (la pose en versión catálogo, con la cámara a la altura del pecho). Antes se pasaba
                # al ángulo seguro de entrada y, encima, el pedido se contradecía (el perfil decía
                # "desde abajo" y la línea CÁMARA "a la altura del pecho"). Con nenes, sí de entrada.
                if _es_kids(params) and not CAMARA_POOL[_cp % len(CAMARA_POOL)]["lenceria"]:
                    _cp = _CAM_SEGURA_DE.get(_cp, 0)
                payload["camara"] = _cp
        if _man:
            _pman = str(params.get("pose", ""))
            _pman = _pose_sin_camara(_pman)
            if _man.get("vista"):
                _pman = VISTAS_POSE[_man["vista"]][1] + ". " + _pman
            params = {**params, "pose": _pman}
            if _man.get("plano") is not None:
                params = {**params, "encuadre": PLANO_POOL[_man["plano"]]["es"]}
            if _man.get("lente"):
                params = {**params, "lente": _man["lente"]}
            _cargar_ctx(params)
        # Género: con avatar sale del avatar; sin avatar, del pedido (default mujer).
        genero = (av.get("gender") if con_avatar and av
                  else str(params.get("genero", "") or payload.get("genero", "")).strip())
        # Qué cámara le toca a ESTA toma y si es de espalda: el lente automático se elige
        # con eso (un 85 mm "cremoso" desde un dron o el 90 de retrato en una espalda no van).
        _cm0 = cam_idx(payload.get("camara"))
        _ca0 = cam_idx(payload.get("camara_auto"))
        _up0 = str(params.get("pose", "")).strip()
        _ix0 = fp if fp is not None else int(payload.get("pose_offset", 0) or 0)
        _ce0 = (_cm0 if _cm0 is not None else (_ca0 if _ca0 is not None else
                                                (None if (_up0 or _guia) else _ix0)))
        params = {**params, "_genero": genero,
                  "_cam_lbl": (_cam_entry(_ce0, _es_ropa_interior(params), _cm0 is not None)["lbl"]
                               if _ce0 is not None else ""),
                  "_espalda_toma": bool((fp is not None and fp % len(POSE_POOL) in (3, 10))
                                        or (_up0 and _toma_atras_pose(_up0.lower()))
                                        or (not _up0 and fp is None and _ix0 % len(POSE_POOL) in (3, 10)))}
        _cargar_ctx(params)
        prompt = build_prompt_on_model(params, settings, paneles, aspect, style, n_prod,
                                       int(payload.get("pose_offset", 0)), force_pose=fp,
                                       con_avatar=con_avatar, genero=genero,
                                       camara=payload.get("camara"),
                                       camara_auto=payload.get("camara_auto"))
        if _guia:
            prompt = _prompt_con_guia(prompt, _guia.get("encuadre"), cam_mano=_cam_mano,
                                      perfil=_guia.get("perfil"))
            prompt = _camara_medida(prompt, _guia.get("perfil"), _cam_mano, _es_kids(params))
        if (_guia or _man) and not _kids_m:
            _ft = _ficha_tecnica(_man, (_guia or {}).get("perfil"),
                                 cam_idx(payload.get("camara")) if _cam_mano else None,
                                 str(params.get("lente") or ""))
            if _ft:
                prompt = "FICHA TÉCNICA DE LA TOMA: " + _ft + "\n\n" + prompt
        if _man.get("vista"):
            _vt = VISTAS_POSE[_man["vista"]][1]
            prompt, _nv = re.subn(r"^(CÁMARA:[^\n]*)", lambda _m: _m.group(1).rstrip() + " Desde esta cámara ella está "
                                  + _vt + ".", prompt, count=1, flags=re.M)
        
        if _kids_m:
            # Kids con modelo: prompt propio, sin cuerpo de adulto, sin lencería, sin
            # complemento. Y nunca con avatar (ninguna cara real): se ignora si vino.
            con_avatar, av = False, None
            prompt = build_prompt_kids_modelo(
                params, settings, style, n_prod,
                pose_idx=(fp if fp is not None else int(payload.get("pose_offset", 0))),
                pose_txt=str(params.get("pose", "")).strip(),
                cam=(cam_idx(payload.get("camara")) if _guia else cam_idx(payload.get("camara") if payload.get("camara") is not None
                                                else payload.get("camara_auto"))),
                guia=bool(_guia), cam_mano=_cam_mano)
            if _guia:
                prompt = _prompt_con_guia(prompt, _guia.get("encuadre"), cam_mano=_cam_mano,
                                      perfil=_guia.get("perfil"))
        _cons_txt = _bloque_consistencia(n_cons, era=_hay_estilo_avatar(params),
                                         caras=bool(params.get("_anclas_cara"))).strip()
        if _cons_txt and any(str(params.get(k, "")).strip() for k in
                             ("cuerpo_contextura", "cuerpo_busto", "cuerpo_cola",
                              "cuerpo_abdomen")):
            _cons_txt += ("\n• CUERPO: la modelo de esta sesión ES del tipo de cuerpo "
                          "descripto en el bloque TIPO DE CUERPO de más abajo; mantené esa "
                          "misma contextura en todas las tomas del set.")
        if _cons_txt:
            prompt = _cons_txt + "\n\n" + prompt   # al TOPE: enterrada al final se ignoraba
        if not _kids_m and _es_hombre(genero):
            # los bloques fijos están escritos para una mujer ("ella", "la modelo", "sentada")
            prompt = _masc_es(prompt)
        # Cada imagen viaja con un RÓTULO de texto justo antes: sin rótulos Gemini tenía
        # que contar posiciones ("las últimas N") y desde la toma 2 confundía qué imagen
        # era la prenda real y cuál el ancla → inventaba otra estampa o cambiaba la cara.
        parts = [{"text": prompt}]
        _idx = 1
        if con_avatar:
            parts.append({"text": _rotulo_avatar(params, _idx)})
            parts.append(_img_part(av["ref_b64"]))
            _idx += 1
            _ex, _idx = partes_extra_avatar(av, _idx)
            parts += _ex
        _nbl = int(payload.get("n_back_last", 0) or 0)     # cuántas del final son ESPALDA
        _nfr = n_prod - _nbl
        for _j, _b in enumerate(prod_b64s):
            _tag = _etiqueta_txt(prod_tags[_j] if _j < len(prod_tags) else "")
            _es_atras = (_nbl and _j >= _nfr) or _es_tag_espalda(
                prod_tags[_j] if _j < len(prod_tags) else "")
            if _es_atras:
                parts.append({"text": f"IMAGEN {_idx} — SOLO PARA LA PARTE DE ATRÁS: así "
                                      "se ve la ESPALDA de esta misma prenda. Usala "
                                      "ÚNICAMENTE para la espalda. OJO: que la espalda "
                                      "sea lisa NO significa que la prenda sea lisa — el "
                                      "FRENTE conserva EXACTO el diseño y la estampa de "
                                      "las otras fotos:"})
            elif _tag:
                # La usuaria etiquetó esta foto: se le dice al motor exactamente qué es.
                parts.append({"text": f"IMAGEN {_idx} — FOTO REAL DEL PRODUCTO · {_tag} "
                                      "(dicho por la usuaria). Es verdad de la prenda: "
                                      "copiá EXACTOS diseño, estampa, color y "
                                      "terminaciones de la parte que muestra:"})
            else:
                parts.append({"text": f"IMAGEN {_idx} — FOTO REAL DEL PRODUCTO (vista "
                                      f"{_j + 1} de {_nfr}"
                                      + (", del FRENTE: ESTA define cómo es la prenda "
                                         "— estampa, colores, cortes y terminaciones"
                                         if _nbl else "")
                                      + "; ÚNICA verdad de la prenda: copiá EXACTOS "
                                        "diseño, estampa, color y terminaciones):"})
            parts.append(_img_part(_b))
            _idx += 1
        for _b in cons_b64s:
            parts.append({"text": _rotulo_toma_previa(params, _idx)})
            parts.append(_img_part(_b))
            _idx += 1
        if _guia:
            parts.append({"text": f"IMAGEN {_idx} — GUÍA DE POSE Y CÁMARA (sin "
                                  "caras: maniquí, calco o dibujo de líneas de la foto de referencia; si es "
                                  "un dibujo de toda la escena, los muebles sólo marcan dónde se sienta o "
                                  "apoya, y el lugar y la decoración salen del texto; si muestra el "
                                  "volumen de alguna ropa, como un vestido, NO es la prenda: la prenda es "
                                  "la del producto): copiá EXACTAMENTE su pose (cuerpo, brazos, manos, "
                                  "piernas, inclinación y giro de la cabeza), la altura, el "
                                  "ángulo y la DISTANCIA de la cámara (qué tan grande o chica se ve "
                                  "ella y cuánto lugar hay alrededor), el encuadre (qué entra "
                                  "y dónde corta, dónde queda ella en el cuadro) y la energía del "
                                  "gesto (brazos igual de abiertos y altos, el cuerpo igual de estirado "
                                  "y en movimiento). "
                                  + ("La CÁMARA no sale de acá: es la que eligió ella en el renglón "
                                     "CÁMARA (de la guía sólo la pose y el gesto). " if _cam_mano else
                                     "Esto manda sobre cualquier plano, cámara o lente escrito en otro lado. ")
                                  + ((_texto_escala(_guia.get("encuadre"), False) + " ")
                                     if _guia.get("encuadre") and not _cam_mano else "")
                                  + ("" if _cam_mano else _fp.aviso_guia_de_atras(_guia.get("perfil")))
                                  + _guia_solo_pose(_man)
                                  + _GUIA_VIVA_ES
                                  + "De esta "
                                  "imagen NO se copia nada más: ni el gris ni el color de la figura, "
                                  "ni el fondo, ni la falta de cara o de ropa."})
            parts.append(_img_part(_guia["img"]))
            _idx += 1
        quien = av.get("name") if con_avatar else "modelo IA (sin avatar)"
        note = f"on_model · {quien} · {n_prod} fotos prod"
        if use_flux or _flux_on_block:
            # FLUX: prompt corto + persona primera (avatar o ancla) y después la(s) prenda(s).
            # Misma prioridad que Nano Banana: pose FORZADA por el plan > pose escrita >
            # pose del pool por offset. Antes la pose escrita ganaba incluso en las tomas
            # del set con pose forzada, y salían todas iguales.
            _pose_txt = str(params.get("pose", "")).strip()
            _slug_prev = str((settings.get("flux_tryon_model") if con_avatar
                              else settings.get("flux_edit_model")) or "").lower()
            _poses_seguras = (str(settings.get("seedream_poses_seguras", "si")).lower()
                              not in ("no", "0", "off", "false")
                              and "seedream" in _slug_prev
                              and _categoria(params) in ("lenceria", "bano"))
            _pool_idx = None
            _escrita = fp is None and bool(_pose_txt)
            # Lo que escribió ella (la toma, el escenario, la luz, el encuadre) va al
            # inglés de verdad, con Gemini y caché. Antes viajaba en castellano con dos
            # palabras sueltas en inglés y Seedream hacía lo que quería.
            _tr = await _al_ingles({"pose": _pose_txt if _escrita else "",
                                    "fondo": str(params.get("fondo", "")).strip(),
                                    "peinado_libre": str(params.get("peinado_libre", "")).strip(),
                                    "luz": str(params.get("luz", "")).strip(),
                                    "ajustes_en": str(params.get("ajustes", "")).strip(),
                                    "encuadre": str(params.get("encuadre", "")).strip(),
                                    "aclaraciones": str(params.get("aclaraciones", "")).strip(),
                                    "piezas": str(params.get("piezas", "")).strip(),
                                    "cuerpo_accesorios": str(params.get("cuerpo_accesorios", "")).strip()})
            _params_fx = {**params, **{k: v for k, v in _tr.items() if k != "pose"}}
            if _tr:
                note += " · en inglés"
            if fp is not None:
                _pool_idx = int(fp) % len(POSE_POOL_FLUX)
            elif _pose_txt:
                # toma escrita → traducida entera; si no se pudo, el castellano más las
                # pistas por palabras clave, como antes
                _pose_txt = _tr.get("pose") or (_pose_txt + _pose_en_hints(_pose_txt))
            else:
                _pool_idx = int(payload.get("pose_offset", 0)) % len(POSE_POOL_FLUX)
            if _pool_idx is not None:
                # pose del pool → versión en INGLÉS (Seedream ignoraba el castellano); en
                # lencería/baño, la versión de catálogo que no rebota el checker de salida.
                _pose_txt = (POSE_SEGURA_FLUX.get(_pool_idx, POSE_POOL_FLUX[_pool_idx])
                             if _poses_seguras else POSE_POOL_FLUX[_pool_idx])
                # Si el tamaño de plano viaja en su propio renglón, la pose no lleva el suyo.
                _cmf = cam_idx(payload.get("camara"))
                if (_plano_flux(payload, params, _pool_idx, settings)
                        or _cam_fija_plano(_cmf if _cmf is not None else
                                           (cam_idx(payload.get("camara_auto")) if cam_idx(payload.get("camara_auto")) is not None else _pool_idx),
                                           _es_ropa_interior(params), _cmf is not None)):
                    _pose_txt = _pose_sin_plano(_pose_txt, en=True)
            _solo_cara = (con_avatar and str(settings.get("seedream_cara_recortada", "si")).lower()
                          not in ("no", "0", "off", "false"))
            persona_b64 = (await recorte_cara_avatar(av) if _solo_cara else
                           (av["ref_b64"] if con_avatar else (cons_b64s[0] if cons_b64s else None)))
            if not con_avatar and cons_b64s and persona_b64 == cons_b64s[0]:
                # Sin avatar (kids, modelo IA) el ancla era la TOMA PREVIA ENTERA: Seedream copiaba
                # su composición y todas las tomas salían iguales. Va sólo la cara.
                persona_b64 = await recorte_cara_avatar(
                    {"ref_b64": cons_b64s[0],
                     "id": "prev" + hashlib.sha1(cons_b64s[0][:4000].encode()).hexdigest()[:16]})
            # PERSONAJE con Cara HD: además de la cara de frente viaja su PERFIL HD. Con una
            # sola foto de frente, en las tomas de costado o 3/4 Seedream inventaba los rasgos
            # que no veía y la cara salía "parecida", no ella. (FLUX.2 tiene 4 lugares: no.)
            _cara2 = None
            if persona_b64 and con_avatar and "flux" not in _slug_prev:
                _cara2 = next((b for et, b in (av.get("extra_refs") or [])
                               if "PERFIL" in et.upper()), None)
                if _cara2:
                    _cara2 = _compress_ref(base64.b64decode(_strip_data_url(_cara2)),
                                           max_dim=2048, q=92)
            _desc_cara = ""
            if persona_b64 and con_avatar and str(av.get("desc") or "").strip():
                _desc_cara = ((await _al_ingles({"cara": str(av["desc"])[:400]})).get("cara")
                              or str(av["desc"])[:400])[:160]
            # FLUX.2 acepta 4 referencias en total (cara incluida): el prompt tiene que
            # describir SOLO las fotos que de verdad viajan, si no rotula fotos que el
            # motor nunca ve.
            _nprods_flux = (4 - (1 if persona_b64 else 0)) if "flux" in _slug_prev else 5
            _n_caras = (1 if persona_b64 else 0) + (1 if _cara2 else 0)
            _guia_fx = _guia
            _idx_guia = _n_caras + 1                      # la guía va justo después de las caras
            _n_prev = _n_caras + (1 if _guia_fx else 0)
            _es_toma_espalda = (fp in (3, 10)) or _toma_atras_pose(str(_pose_txt))
            _ve_esp = (not _es_toma_espalda) and _ve_espalda(
                " ".join([str(_pose_txt), str(params.get("pose", "")),
                          str(params.get("aclaraciones", ""))]), _pool_idx)
            _sel_fx, _nbl_fx = _elegir_fotos_flux(list(prod_tags[:n_prod]), _nbl, _nprods_flux,
                                                  _es_toma_espalda, mixta=_ve_esp)
            _prods_fx = [prod_b64s[j] for j in _sel_fx]
            _tags_fx = [prod_tags[j] for j in _sel_fx]
            if _nbl_fx < 0:
                # espalda primero: se rotulan por etiqueta (el "últimas N" ya no aplica)
                for _q in range(-_nbl_fx):
                    _tags_fx[_q] = _ETIQUETAS_PRENDA["espalda"]
                _nbl_fx = 0
            # Lo que se agrega DESPUÉS del armado (caras, regla de pose) también cuenta para el
            # tope de Seedream: se le reserva lugar y se recortan extras en vez de pasarse.
            _apx = []
            if _cara2:
                _apx.append("Image 2 = the SAME woman's face in profile (her side features).")
            if _desc_cara:
                _apx.append(f"Her face (keep it exactly): {_desc_cara}")
            if persona_b64:
                _apx.append("Faces in the product photos are NOT her.")
            if _solo_cara and persona_b64:
                _apx.append("Image 1 is a FACE CROP: identity only; body and pose come from the text.")
            if _guia_fx:
                _apx.insert(0, f"Image {_idx_guia} is a POSE AND CAMERA GUIDE (faceless: "
                               "mannequin, trace or line drawing of the reference; in a whole-scene line "
                               "drawing the furniture only shows where she sits or leans, the setting comes from the text; any clothing volume "
                               "in it is NOT the garment): copy EXACTLY its body pose, hands, head tilt, camera "
                               "height, angle and DISTANCE (how big or small she is in the frame and how "
                               "much of the place is around her), its framing and crop, and the energy of "
                               "the gesture (arms as open and as high, body as stretched and in motion). "
                               + ("EXCEPT the camera: the CAMERA line was chosen by the user and its angle "
                                  "and distance win over the guide. " if _cam_mano else
                                  "It overrides any shot size, camera or lens written elsewhere. ")
                               + "Take nothing else from it (not its grey or colour, not the background).")
                _apx.append(f"POSE RULE: copy the pose and framing ONLY from the guide (image "
                            f"{_idx_guia}); never from the face or product photos.")
                if _guia_solo_pose(_man, en=True):
                    _apx.insert(1, _guia_solo_pose(_man, en=True))
                _ftx = _ficha_tecnica(_man, _guia_fx.get("perfil"), cam_idx(payload.get("camara")) if _cam_mano else None,
                                      str(params.get("lente") or ""))
                if _ftx:
                    _apx.insert(0, _ftx)
                if _guia_fx.get("encuadre") and not _cam_mano:
                    _apx.insert(1, _texto_escala(_guia_fx.get("encuadre"), True))
                if _guia_fx.get("perfil"):
                    _pf = _guia_fx.get("perfil")
                    if _cam_mano:
                        _pf = {**_pf, "modules": {**(_pf.get("modules") or {}), "camera": False, "framing": False}}
                    _apx.insert(1, "PHOTO PROFILE of the reference (measured JSON, reproduce every value): "
                                   + _perfil_json(_pf)[:900])
                    if not _cam_mano and _fp.camara_en_palabras(_pf, en=True):
                        _apx.insert(1, _fp.camara_en_palabras(_pf, en=True))
                    if not _cam_mano and _fp.aviso_guia_de_atras(_pf, en=True):
                        _apx.insert(1, _fp.aviso_guia_de_atras(_pf, en=True))
                _apx.append(_GUIA_VIVA_EN)
            else:
                _apx.append("POSE RULE: the MANDATORY POSE above is the ONLY pose. Do NOT "
                            "copy the pose, framing or composition from any reference image.")
            if _es_hombre(genero):
                _apx = [_masc_en(x) for x in _apx]
            _fprompt = build_prompt_flux(_params_fx, _pose_txt, con_persona=bool(persona_b64),
                                         n_prod=len(_prods_fx), genero=genero,
                                         estilo=_style_flux(style, settings),
                                         prod_tags=_tags_fx,
                                         n_back_last=_nbl_fx,
                                         camara=("" if (_guia_fx and cam_idx(payload.get("camara")) is None) else _camara_flux(payload, params, _pool_idx, settings)),
                                         rincon=("" if _guia_fx else _rincon_flux(payload, params, _pool_idx, settings, pose=_pose_txt)),
                                         plano=("" if _guia_fx else _plano_flux(payload, params, _pool_idx, settings)),
                                         escrita=_escrita, n_caras=max(1, _n_caras),
                                         reserva=sum(len(x) + 1 for x in _apx), ve_espalda=_ve_esp,
                                         n_previas=(_n_prev if persona_b64 or _guia_fx else None),
                                         guia=bool(_guia_fx))
            # Para Claude, el pedido COMPLETO (sin recortar): igual lo reescribe corto, y con el
            # recortado no veía ni la anatomía ni las reglas que se habían caído.
            _stats_corte = dict(_FLUX_PROMPT_STATS)
            _fprompt_entero = build_prompt_flux(
                _params_fx, _pose_txt, con_persona=bool(persona_b64), n_prod=len(_prods_fx),
                genero=genero, estilo=_style_flux(style, settings), prod_tags=_tags_fx,
                n_back_last=_nbl_fx, camara=("" if (_guia_fx and cam_idx(payload.get("camara")) is None) else _camara_flux(payload, params, _pool_idx, settings)),
                rincon=("" if _guia_fx else _rincon_flux(payload, params, _pool_idx, settings, pose=_pose_txt)),
                plano=("" if _guia_fx else _plano_flux(payload, params, _pool_idx, settings)), escrita=_escrita,
                n_caras=max(1, _n_caras), tope=100000, ve_espalda=_ve_esp,
                n_previas=(_n_prev if persona_b64 or _guia_fx else None),
                guia=bool(_guia_fx)) + "".join("\n" + x for x in _apx)
            _FLUX_PROMPT_STATS.clear()
            _FLUX_PROMPT_STATS.update(_stats_corte)       # el diagnóstico muestra el recortado
            _fprompt += "".join("\n" + x for x in _apx[:-1])
            # OJO: a Seedream NO se le manda el ancla de tomas previas. Es un modelo de
            # EDICIÓN: cuando ve una foto ya lista, copia su composición ENTERA (pose,
            # encuadre, escena) aunque el texto pida otra pose — así salían todas las
            # tomas del set idénticas a la primera. La identidad viaja en la foto del
            # avatar; la pose la define solo el prompt.
            _fprompt += "\n" + _apx[-1]          # la regla de pose (con guía, copia de la guía)
            if _kids_m:
                # Kids: prompt propio en inglés, corto y limpio (el de adultos habla de
                # cuerpo y lencería; el de Nano Banana es largo y en castellano, y
                # Seedream lo rechazaba con "Error validating the input").
                _kidx = int(fp if fp is not None else payload.get("pose_offset", 0))
                _pose_k = (_tr.get("pose") or (str(params.get("pose", "")).strip()
                                                + _pose_en_hints(str(params.get("pose", "")).strip()))
                           if _escrita
                           else _kids_poses_en(params)[_kidx % len(_KIDS_POSES_EN)])
                _pose_kes = (str(params.get("pose", "")).strip() if _escrita
                             else _kids_pose(_kidx, params))
                _fprompt = build_prompt_flux_kids(_params_fx, _pose_k, len(_prods_fx),
                                                  prod_tags=_tags_fx,
                                                  n_back_last=_nbl_fx,
                                                  con_ancla=bool(persona_b64),
                                                  estilo=_style_flux(style, settings),
                                                  toma=_kids_toma(_params_fx, settings, _kidx,
                                                                  cam_idx(payload.get("camara") if payload.get("camara") is not None
                                                                          else payload.get("camara_auto")),
                                                                  _pose_k, en=True, pose_es=_pose_kes,
                                                                  guia=bool(_guia_fx),
                                                                  cam_mano=_cam_mano),
                                                  guia=bool(_guia_fx), pose_es=_pose_kes)
                if _guia_fx:
                    # la guía (y su perfil y escala) también para chicos: va después de la cara
                    _fprompt += "".join(
                        "\n" + (_masc_en(x) if _ctx("hombre") else x) for x in _apx
                        if x.startswith((f"Image {_idx_guia} is a POSE", "PHOTO PROFILE", "SCALE AND",
                                         "POSE RULE")) or x == _GUIA_VIVA_EN)
            if not _kids_m:
                _ped = []
                if _escrita:
                    _ped.append("Toma (pose, acción, encuadre): " + str(params.get("pose", "")).strip())
                for _kk, _lbl in (("encuadre", "Encuadre"), ("fondo", "Lugar / fondo"),
                                  ("luz", "Luz"), ("aclaraciones", "Indicaciones")):
                    _vv = str(params.get(_kk, "")).strip()
                    if _vv:
                        _ped.append(f"{_lbl}: {_vv}")
                _cuerpo_c = _cuerpo_lista(_params_fx, genero) if _ped else ""
                if _cuerpo_c:
                    _ped.append(f"Su cuerpo (tiene que verse así, sin adelgazarla): {_cuerpo_c}")
                _pedido_claude = "\n".join(_ped)
                _cara_claude = persona_b64 or ""
                # Claude reordena el pedido (corto, en inglés, SU toma primero) sólo si ella
                # escribió algo. Nunca en la vista previa (no gasta) ni si editó el prompt.
                if (_pedido_claude and use_flux and not payload.get("solo_prompt")
                        and not str(payload.get("prompt_override", "") or "").strip()
                        and _claude.seedream_con_claude(settings)):
                    try:
                        _nuevo, _cc = await _claude.reescribir_prompt(_fprompt_entero, _pedido_claude)
                        if len(_nuevo) > FLUX_PROMPT_MAX:
                            raise _claude.ClaudeNoDisponible(
                                f"su pedido salió de {len(_nuevo)} caracteres (tope {FLUX_PROMPT_MAX})")
                        _fprompt = _sanear_prompt_fal(_nuevo)
                        await budget_record("claude_seedream", _claude.MODELO, _cc, 1,
                                            note="Claude escribió el pedido de Seedream")
                        note += " · Claude escribió el pedido"
                    except _claude.ClaudeNoDisponible as _e:
                        print(f"[imagenes_ia][claude-seedream] {_e}")
            flux_parts = [{"text": _fprompt}]
            if persona_b64:
                flux_parts.append(_img_part(persona_b64))
            if _cara2 and not _kids_m:
                flux_parts.append(_img_part(_cara2))
            if _guia_fx:
                flux_parts.append(_img_part(_guia_fx["img"]))
            flux_parts += [_img_part(b) for b in _prods_fx]
            flux_slug = str((settings.get("flux_tryon_model") if persona_b64
                             else settings.get("flux_edit_model")) or "bytedance/seedream/v5/pro/edit")
            if use_flux:
                _parts_gemini = parts          # por si Seedream rechaza una referencia
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
            prompt = await _flux_en(build_prompt_product_only(params, settings, modo_p, paneles,
                                                              aspect, n_prod))
            parts = [{"text": prompt}] + [_img_part(b) for b in prod_b64s]
            flux_slug = str(settings.get("flux_edit_model") or "bytedance/seedream/v5/pro/edit")
            note = "FLUX · " + note
    elif mode == "trio":
        _guia3 = await _pose_guia(str(payload.get("pose_guia_txt") or ""))
        if _guia3 and _guia3.get("aspect") in ASPECTOS_VALIDOS:
            aspect = _guia3["aspect"]
        asign = payload.get("asign") or []
        colores = payload.get("colores") or []
        if isinstance(colores, str):
            colores = [c for c in colores.replace(";", ",").split(",") if c.strip()]
        if not asign and colores:
            asign = [{"nombre": "", "color": c} for c in colores[:SET_MAX_MODELOS]]
        asign = (asign or [])[:SET_MAX_MODELOS]
        while len(asign) < 2:
            asign.append({"nombre": "", "color": (asign[-1]["color"] if asign else "blanco")})
        n_set = len(asign)
        gen_set = str(params.get("genero", "") or payload.get("genero", "")).strip()
        # La grupal: las caras de los avatares como identidad (y, abajo, sus individuales ya hechas).
        av_parts, img_map, nxt = [], [None] * n_set, 1
        full_refs = False
        for k in range(n_set):
            aid = asign[k].get("avatar_id")
            aref = await get_avatar_ref(aid) if aid else None
            if aref:
                av_parts.append({"text": f"IMAGEN {nxt} — CARA DE"
                                         + (f"L MODELO {_LETRAS[k]}" if _es_hombre(gen_set)
                                            else f" LA MODELO {_LETRAS[k]}")
                                         + " (solo identidad: cara y rasgos; su ropa NO "
                                           "existe en esta toma):"})
                av_parts.append(_img_part(aref))
                img_map[k] = nxt
                nxt += 1
        # Las INDIVIDUALES ya hechas de cada modelo (el set ahora las hace antes): la grupal
        # las copia, así la prenda sale igual que en su foto sola (tiras, costuras, color).
        _ind = payload.get("ind_refs") or []
        for k in range(n_set):
            ref = _strip_data_url(str(_ind[k])) if k < len(_ind) and _ind[k] else ""
            if not ref:
                continue
            av_parts.append({"text": f"IMAGEN {nxt} — LA MODELO {_LETRAS[k]} EN SU FOTO INDIVIDUAL "
                                     "YA APROBADA: en la foto grupal es ESTA misma persona (cara, "
                                     "pelo, cuerpo) con ESTA misma prenda y ESTE mismo color, con "
                                     "todos sus detalles (cantidad de tiras, costuras, moños, "
                                     "argollas). Copiá la prenda de acá; la pose y el lugar los "
                                     "define la foto grupal."})
            av_parts.append(_img_part(_compress_ref(base64.b64decode(ref), max_dim=1344, q=90)))
            nxt += 1
        prod_primera = nxt
        # PRENDA PROPIA POR MODELO: cuando cada pieza del set es otra prenda (otra
        # estampa, no el mismo modelo en otro color), cada modelo trae SU foto. Van
        # después de las fotos compartidas y cada una rotulada con su modelo, si no
        # el motor no sabe cuál es de quién y termina mezclando las estampas.
        prod_map: List[Optional[int]] = [None] * n_set
        propias: List[Tuple[int, str]] = []
        _sig = prod_primera + n_prod
        for k in range(n_set):
            f = _strip_data_url(str(asign[k].get("foto") or ""))
            if not f:
                continue
            prod_map[k] = _sig
            propias.append((k, f))
            _sig += 1
        # Fotos de cada pieza = SÓLO COLOR (con fotos principales): el prompt es "la misma
        # prenda, cada una en su color" y esas fotos se rotulan como color.
        _solo_color = bool(propias) and n_prod > 0 and payload.get("fotos_modo", "color") == "color"
        prompt = build_prompt_trio(params, settings, asign, aspect, style, n_prod,
                                   img_map=img_map, prod_primera=prod_primera,
                                   full_refs=full_refs, genero=gen_set,
                                   prod_map=(None if _solo_color else prod_map))
        # No se decide el modo por ser kids ni por el tipo de prenda.
        # El mode solicitado por el usuario ya llegó hasta acá.
        _kids_g = _kids_modelo_ok(params)
        if _kids_g:
            # Grupal de kids: sin avatares (ninguna cara real), prompt propio de chicos.
            av_parts, img_map = [], [None] * n_set
            prod_primera = 1
            _sig = prod_primera + n_prod
            prod_map = [None] * n_set
            for k in range(n_set):
                if _strip_data_url(str(asign[k].get("foto") or "")):
                    prod_map[k] = _sig
                    _sig += 1
            prompt = build_prompt_kids_grupal(params, settings, asign, style, n_prod,
                                              prod_primera=prod_primera, prod_map=prod_map)
        parts = [{"text": prompt}] + av_parts
        for _j, _b in enumerate(prod_b64s):
            # Con prendas propias, estas fotos dejan de ser "la verdad de la prenda":
            # son de una de las piezas y el rótulo de siempre hacía que el motor se la
            # pusiera a todos.
            parts.append({"text": f"IMAGEN {prod_primera + _j} — FOTO REAL DEL PRODUCTO "
                                  f"(vista {_j + 1} de {n_prod}; "
                                  + ("referencia general del molde y la tela; la prenda "
                                     "de cada modelo sale de SU propia imagen"
                                     if (propias and not _solo_color) else
                                     "ÚNICA verdad de la prenda: copiá EXACTOS diseño, "
                                     "estampa, color y terminaciones")
                                  + "):"})
            parts.append(_img_part(_b))
        for _k, _b in propias:
            parts.append({"text": (f"IMAGEN {prod_map[_k]} — SOLO EL COLOR de la prenda "
                                   f"de{'l' if _es_hombre(gen_set) else ' la'} MODELO {_LETRAS[_k]}: "
                                   "de acá sale únicamente su color exacto; el diseño, las "
                                   "tiras, costuras y detalles salen de las fotos del producto:")
                                  if _solo_color else
                                  f"IMAGEN {prod_map[_k]} — FOTO REAL DE LA PRENDA DEL "
                                  f"MODELO {_LETRAS[_k]} (es SU prenda y solo suya: copiá "
                                  "EXACTOS su estampa, colores, cortes y terminaciones; "
                                  "no se la pongas a otro modelo):"})
            parts.append(_img_part(_b))
        if _guia3:
            _n_img3 = sum(1 for _pt in parts if "text" not in _pt)
            parts.append({"text": f"IMAGEN {_n_img3 + 1} — GUÍA DE POSE Y CÁMARA DE LA FOTO GRUPAL "
                                  "(sin caras: figuras de un color cada una, o un dibujo de líneas de toda la escena; "
                                  "si es un dibujo, los muebles sólo marcan dónde se sientan o apoyan y el lugar sale del texto; los volúmenes "
                                  "de ropa que se vean NO son las prendas): copiá EXACTAMENTE la cámara "
                                  "(altura, ángulo, distancia), el encuadre y la pose y ubicación de "
                                  "cada figura: la de la IZQUIERDA (rosa si tiene color) es la MODELO A, la CELESTE la B, "
                                  "la VERDE la C, la AMARILLA la D, de izquierda a derecha. De esta imagen "
                                  "NO se copia nada más (ni los colores de las figuras, que sólo dicen quién "
                                  "es quién, ni el fondo, ni la falta de cara o de ropa)."})
            parts.append(_img_part(_guia3["img"]))
        note = "trio · " + ", ".join(f"{i.get('color', '')}" for i in asign)
        if use_flux or _flux_on_block:
            estilo_s = _style_text(style, settings)
            _gw3 = _gwords(gen_set)
            _h3 = _es_hombre(gen_set)
            _n3 = _n_txt(n_set)
            _las3 = "los" if _h3 else "las"
            _jun3 = "juntos" if _h3 else "juntas"
            _tod3 = f"{_las3} {_n3.lower()}"
            quien = (f"{_tod3} {_gw3['genero_p']} de las primeras {len(av_parts)} imágenes "
                     "(respetá sus caras y rasgos exactos)" if av_parts
                     else f"{_n3.lower()} {_gw3['genero_p']} adult"
                          + ("os" if _h3 else "as") + " distint"
                          + ("os (no gemelos)" if _h3 else "as (no gemelas)"))
            cols = ", ".join(str(i.get("color", "")) for i in asign)
            _cat3 = _categoria(params)
            lenc = (_LENCERIA_FLUX + "\n\n") if _cat3 == "lenceria" else ""
            dir3 = _bloque_categoria(_cat3, gen_set, con_lugar=not str(params.get("fondo", "")).strip())
            # Con prenda propia por modelo, la ÚLTIMA imagen ya no es "la prenda": son
            # varias, una por modelo, y hay que decir cuál es de quién.
            # A Seedream le va OTRA lista de imágenes que a Gemini (una sola foto
            # compartida, no todas), así que los números de IMAGEN se recalculan: con el
            # mapa de Gemini, el prompt le pedía al modelo B una imagen que no existía.
            _pmap3: List[Optional[int]] = [None] * n_set
            _sig3 = prod_primera + (0 if propias else 1)
            for _k, _ in propias:
                _pmap3[_k] = _sig3
                _sig3 += 1
            if propias:
                _cuales = "; ".join(
                    f"{_gw3['modelo']} {_LETRAS[_k]} lleva la prenda de la IMAGEN {_pmap3[_k]}"
                    for _k, _ in propias)
                _prenda3 = ("Cada un" + ("o" if _h3 else "a") + " lleva SU prenda, que es "
                            "DISTINTA de las otras (otra estampa): " + _cuales
                            + ". Copiá cada prenda EXACTA de SU imagen y no mezcles las "
                              "estampas entre ell" + ("os" if _h3 else "as") + ". ")
            else:
                _prenda3 = (f"{_tod3.capitalize()} llevan la MISMA prenda de la ÚLTIMA imagen "
                            "— copiala EXACTA: mismo diseño, calce y terminaciones — cada "
                            "un" + ("o" if _h3 else "a") + " en su color: " + cols + ". ")
            _fprompt3 = (estilo_s + "\n\n" + lenc
                         + f"Foto de campaña con {_n3} modelos {_jun3}: " + quien + ". "
                         + _prenda3
                         + ((_outfit_grupal(params, asign) + " ") if _outfit_grupal(params, asign) else "")
                         + (("POSE, CÁMARA Y ENCUADRE: los de la ÚLTIMA imagen (sin caras: figuras "
                             "de un color cada una, o un dibujo de líneas de toda la escena; en el dibujo "
                             "los muebles sólo marcan dónde se sientan o apoyan): copiá exactamente la altura, el ángulo y la "
                             "distancia de la cámara, el encuadre y la pose y ubicación de cada figura "
                             "(rosa = modelo A, celeste = B, verde = C, amarilla = D, de izquierda a "
                             "derecha). De esa imagen no se copian los colores de las figuras ni nada más. ")
                            if _guia3 else
                            ("Poses naturales, espontáneas y distintas entre sí, "
                             f"encuadre de la cadera para arriba, {_tod3} bien visibles. "))
                         + "Proporciones humanas correctas, piel natural con detalle sutil, luz "
                         "suave y pareja. PROHIBIDO: que sean gemel"
                         + ("os" if _h3 else "as") + "; logos, marcas de agua o "
                         f"texto; accesorios no pedidos. Exactamente {_n3} {_gw3['genero_p']}."
                         + (("\n\n" + dir3) if dir3 else "")
                         + (("\n" + _bloque_camara_foto({**params, "_estilo": style}, pose_txt=str(_guia3 and payload.get("pose_guia_txt") or ""))) if _bloque_camara_foto(params) else "")
                         + ((f"\nMANDATORY SETTING (top priority): {str(params.get('fondo','')).strip()}. "
                             "Put them in that exact environment.")
                            if str(params.get('fondo','')).strip() else "")
                         + ((f"\nLighting: {str(params.get('luz','')).strip()}.")
                            if str(params.get('luz','')).strip() else "")
                         + ((f"\nMANDATORY DIRECTION from the user (follow it exactly): "
                             f"{str(params.get('aclaraciones','')).strip()}")
                            if str(params.get('aclaraciones','')).strip() else ""))
            _specs3 = "\n".join(_modelo_spec(asign[k], _LETRAS[k], img_map[k], gen_set,
                                             _pmap3[k]) for k in range(n_set))
            _fprompt3 += ("\nDEFINICIÓN DE CADA MODELO (cuerpo y cara, respetala tal cual):\n"
                          + _specs3)
            # Con prendas propias, la foto compartida NO va: sería una cuarta prenda que
            # no le corresponde a nadie y el motor se la termina poniendo a alguno.
            if _kids_g:
                _fprompt3 = build_prompt_kids_grupal(params, settings, asign, style,
                                                     0 if propias else 1,
                                                     prod_primera=prod_primera,
                                                     prod_map=_pmap3)
            if use_flux:
                _fprompt3 = await _flux_en(_fprompt3)
            flux_parts = ([{"text": _fprompt3}] + av_parts
                          + ([] if propias else [_img_part(prod_b64s[0])])
                          + [_img_part(_b) for _, _b in propias]
                          + ([_img_part(_guia3["img"])] if _guia3 and not _kids_g else []))
            flux_slug = str(settings.get("flux_edit_model") or "bytedance/seedream/v5/pro/edit")
            if use_flux:
                _parts_gemini = parts          # por si Seedream rechaza una referencia
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
        try:
            img_bytes = await fal_generate(parts, settings, aspect, image_size, flux_slug)
        except HTTPException as _efal:
            # fal rechazó una IMAGEN DE REFERENCIA (no la foto generada): su filtro marca a veces
            # una cara realista o un retrato en lencería como entrada. Reintentar con la misma
            # imagen no sirve: la toma se hace con Nano Banana en vez de frenar el set.
            _txt = str(getattr(_efal, "detail", _efal))
            _pg = locals().get("_parts_gemini")
            if (_pg and getattr(_efal, "status_code", 0) == 422
                    and ("image_urls" in _txt or "content_policy" in _txt or "filtro" in _txt)):
                print(f"[imagenes_ia] Seedream rechazó una referencia; va con Nano Banana: {_txt[:160]}")
                img_bytes = await gemini_generate(_pg, settings, aspect, image_size)
                use_flux, flux_slug = False, None
                est = precios[image_size]
                note += " · Seedream rechazó una foto de referencia → Nano Banana"
            else:
                raise
        paneles = 1     # FLUX genera una imagen por llamada (sin trucos de paneles)
        _vino_de_fal = bool(use_flux)     # False si terminó haciéndola Nano Banana
        # CLAUDE REVISA: ¿cumplió la toma que ella escribió? Si no, se pide UNA vez más con
        # la corrección y queda la mejor de las dos. Antes del acabado 4K (no se paga dos veces).
        # Sólo con el INSPECTOR prendido y su reintento activo: con el inspector apagado
        # esto pedía una segunda foto a fal igual (y se cobraban las dos).
        _insp_on = (str(settings.get("qc_prenda", "si")).lower() in ("si", "sí", "1", "on", "true")
                    and str(settings.get("qc_reintento", "si")).lower() in ("si", "sí", "1", "on", "true")
                    and not payload.get("qc_off"))
        if (_insp_on and use_flux and _pedido_claude and not override
                and _claude.seedream_con_claude(settings)):
            try:
                _prods_rev = [_compress_ref(base64.b64decode(b), max_dim=768, q=85) for b in prod_b64s[:2]]
                _rev_claude, _cc = await _claude.revisar_foto(
                    _compress_ref(img_bytes, max_dim=1024, q=88), _pedido_claude, _cara_claude, _prods_rev)
                await budget_record("claude_seedream", _claude.MODELO, _cc, 1,
                                    note=f"Claude revisó la foto ({_rev_claude['puntaje']}/10)")
                if not _rev_claude["cumple"] and _rev_claude["correccion"]:
                    _parts_c = list(parts)
                    for _i, _pt in enumerate(_parts_c):
                        if _pt.get("text"):
                            _parts_c[_i] = {"text": _pt["text"] + "\n\nCORRECTIONS (the previous "
                                            "attempt got these wrong; fix them and change nothing "
                                            "else): " + _rev_claude["correccion"]}
                            break
                    try:
                        _img2 = await fal_generate(_parts_c, settings, aspect, image_size, flux_slug)
                    except Exception as _e2:
                        # el reintento nunca rompe: queda la primera foto, que sí salió
                        print(f"[imagenes_ia][claude-reintento] {getattr(_e2, 'detail', _e2)}")
                        _img2 = None
                    if _img2 is not None:
                        est += float(settings.get("precio_flux", 0.07) or 0.07)
                        _rev2, _cc2 = await _claude.revisar_foto(
                            _compress_ref(_img2, max_dim=1024, q=88), _pedido_claude, _cara_claude, _prods_rev)
                        await budget_record("claude_seedream", _claude.MODELO, _cc2, 1,
                                            note=f"Claude revisó el reintento ({_rev2['puntaje']}/10)")
                        if _rev2["puntaje"] >= _rev_claude["puntaje"]:
                            img_bytes, _rev_claude = _img2, _rev2
                        _rev_claude["reintento"] = True
                note += f" · Claude revisó {_rev_claude['puntaje']}/10" + (
                    " (pedida de nuevo)" if _rev_claude.get("reintento") else "")
            except _claude.ClaudeNoDisponible as _e:
                print(f"[imagenes_ia][claude-revisa] {_e}")
    else:
      _con_json = (str(params.get("prompt_json", "")).lower() in ("si", "sí", "1", "on", "true")
                   and not override)
      if _con_json:
          # PROMPT EN JSON (opción de Fotos, prendida por defecto): el mismo pedido, ordenado en campos
          for _i, _pt in enumerate(parts):
              if _pt.get("text"):
                  parts[_i] = {"text": _prompt_a_json(_pt["text"], params)}
                  break
      try:
        img_bytes, _nota_json = await gemini_con_json(
            parts, settings, aspect, image_size, formato="json" if _con_json else "texto",
            a_json=lambda t: _prompt_a_json(t, params), modulo="fotos",
            rescate=not override and not _es_kids(params))
        if _nota_json:
            note += " · " + _nota_json
      except HTTPException as ge:
        blocked = getattr(ge, "status_code", 0) == 422
        _seg_ok = (blocked and mode == "on_model" and fp is not None
                   and not params.get("_pose_segura") and _es_ropa_interior(params)
                   and not _es_hombre(genero)
                   and (int(fp) % len(POSE_POOL)) in POSE_SEGURA_ES)
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
        elif (blocked and mode == "on_model" and fp is None and not _kids_m
              and str(params.get("pose", "")).strip() and not params.get("_pose_suave")
              and (_es_ropa_interior(params) or _categoria(params) == "bano")):
            # POSE ESCRITA O COPIADA DE UNA FOTO, BLOQUEADA: la misma toma en su versión de
            # catálogo (ojos abiertos, cabeza erguida, cámara a la altura del pecho), sin la
            # guía ni las tomas previas. Antes este caso quedaba bloqueado sin reintento.
            _params_s = {**params, "pose": _pose_suave(str(params.get("pose", ""))), "_pose_suave": True,
                         "aclaraciones": (str(params.get("aclaraciones", "")).strip() + " "
                                          "ENCUADRE EDITORIAL SEGURO: foto de catálogo de moda profesional y "
                                          "respetuosa, actitud natural y segura, mirada a cámara o a un costado "
                                          "con los ojos abiertos.").strip()}
            prompt_s = build_prompt_on_model(_params_s, settings, 1, aspect, style, n_prod, 0,
                                             force_pose=None, con_avatar=con_avatar, genero=genero,
                                             camara=13, camara_auto=None)
            parts_s = [{"text": prompt_s}]
            _ix = 1
            if con_avatar:
                parts_s.append({"text": _rotulo_avatar(_params_s, _ix)})
                parts_s.append(_img_part(av["ref_b64"]))
                _ix += 1
                _ex3, _ix = partes_extra_avatar(av, _ix)
                parts_s += _ex3
            for _b3 in prod_b64s:
                parts_s.append({"text": f"IMAGEN {_ix} — FOTO REAL DEL PRODUCTO (ÚNICA verdad de la "
                                        "prenda: copiá EXACTOS diseño, estampa, color y terminaciones):"})
                parts_s.append(_img_part(_b3))
                _ix += 1
            img_bytes = await gemini_generate(parts_s, settings, aspect, image_size)
            parts = parts_s
            note += " · reintento con la pose en versión de catálogo (Gemini bloqueó la original)"
        elif blocked and mode == "on_model" and (
                (con_avatar and n_cons > 0) or _seg_ok):
            # En lencería, además de sacar las anclas, la pose pasa a su versión de
            # catálogo: sacar las anclas solo no alcanzaba, volvía a bloquear igual.
            _params_r = {**params, "_pose_segura": True} if _seg_ok else params
            prompt_na = build_prompt_on_model(_params_r, settings, paneles, aspect, style,
                                              n_prod, int(payload.get("pose_offset", 0)),
                                              force_pose=fp, con_avatar=con_avatar,
                                              camara=payload.get("camara"),
                                              camara_auto=payload.get("camara_auto"),
                                              genero=genero)
            parts_na = [{"text": prompt_na}]
            _idx2 = 1
            if con_avatar:
                parts_na.append({"text": _rotulo_avatar(_params_r, _idx2)})
                parts_na.append(_img_part(av["ref_b64"]))
                _idx2 += 1
                _ex2, _idx2 = partes_extra_avatar(av, _idx2)
                parts_na += _ex2
            for _j2, _b2 in enumerate(prod_b64s):
                parts_na.append({"text": f"IMAGEN {_idx2} — FOTO REAL DEL PRODUCTO (vista "
                                         f"{_j2 + 1} de {n_prod}; ÚNICA verdad de la "
                                         "prenda: copiá EXACTOS diseño, estampa, color y "
                                         "terminaciones):"})
                parts_na.append(_img_part(_b2))
                _idx2 += 1
            img_bytes = await gemini_generate(parts_na, settings, aspect, image_size)
            parts = parts_na    # el inspector corrige sobre este prompt si hace falta
            note += (" · reintento con pose de catálogo (Gemini bloqueó la original)"
                     if _seg_ok else " · reintento-sin-ancla (bloqueo)")
        # AVATAR SAGRADO: si una toma CON avatar se bloquea, queda bloqueada. NUNCA se
        # recrea la cara ni el cuerpo de la modelo (el avatar es la cara de la marca).
        elif blocked and mode == "trio":
            # La grupal se bloqueó: reintentamos UNA vez con encuadre editorial seguro.
            params_s = {**params, "complemento": "si"}
            prompt_s = build_prompt_trio(params_s, settings, asign, aspect, style, n_prod,
                                         img_map=img_map, prod_primera=prod_primera,
                                         seguro=True, full_refs=full_refs)
            if _kids_modelo_ok(params):
                prompt_s = prompt
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
        qc = await verificar_prenda(img_bytes, prod_b64s, back_qc_b64s, prod_tags)
        umbral = int(settings.get("qc_umbral", 7) or 7)
        _retry_on = str(settings.get("qc_reintento", "si")).lower() in ("si", "sí", "1",
                                                                        "on", "true")
        _rondas = 0
        while (qc and _retry_on and int(qc.get("puntaje", 10)) < umbral
               and qc.get("diferencias") and _rondas < 2):
            _rondas += 1
            corr = ("\n\nCORRECCIONES OBLIGATORIAS: una inspección revisó tu imagen anterior "
                    "contra las fotos reales del producto y también su anatomía, y encontró "
                    "estos errores. Corregilos TODOS en esta nueva imagen, sin cambiar nada "
                    "más (misma modelo, misma pose, mismo escenario):\n• "
                    + "\n• ".join(qc["diferencias"]))
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
                qc2 = await verificar_prenda(img2, prod_b64s, back_qc_b64s, prod_tags)
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
        # Conectado o no: si no lo está, la imagen que se acaba de pagar queda
        # sólo en la pantalla, y eso hay que decirlo en el momento.
        "drive_conectado": await _drive_connected_for(_usub),
        "drive_saved": False,
        "qc": qc,
        **({"revision_claude": {k: _rev_claude.get(k) for k in ("puntaje", "fallas", "reintento")}}
           if _rev_claude else {}),
        "motor": ("fal" if (use_flux and flux_slug) or " FLUX" in (" " + note) else "gemini"),
        "note": note,
        "descartada": bool(qc and qc.get("rechazada")),
        **({"aviso": _aviso_kids_sola(params)} if _kids_forzado else {}),
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
                               "panels_requested", "qc", "descartada", "note", "motor",
                               "aviso") if k in res}
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


def _plan_para_motor(steps: List[Dict[str, Any]], engine: str) -> List[Dict[str, Any]]:
    """Seedream/FLUX generan UNA imagen por llamada y no saben de paneles: la toma
    "21:9 con 2 paneles" del set básico salía como UNA foto apaisada con una sola
    pose (la 0, siempre la misma) que después se recortaba. Con esos motores, cada
    panel pasa a ser una toma 4:5 con su propia pose del pool."""
    if str(engine or "gemini").lower() not in ("flux", "fal"):
        return steps
    out: List[Dict[str, Any]] = []
    for st in steps:
        n = int(st.get("paneles", 1) or 1)
        if st.get("mode") == "on_model" and n > 1:
            off = int(st.get("pose_offset", 0) or 0)
            for i in range(n):
                nuevo = {k: v for k, v in st.items() if k not in ("paneles", "pose_offset", "aspect")}
                nuevo.update({"aspect": "4:5", "paneles": 1,
                              "force_pose": (off + i) % len(POSE_POOL_FLUX)})
                if nuevo["force_pose"] in (3, 10):
                    nuevo["use_back"] = True
                out.append(nuevo)
        else:
            out.append(st)
    return out


_POSE_EXTRA = {"frente": 0, "perfil": 6, "espalda": 3, "sentada": 2, "caminando": 4}
# "Perfil" elegido a mano es un PERFIL DE VERDAD (la pose 6 del pool es un 3/4 que dice
# "NO de perfil puro", y la usuaria pedía perfil y le salía de frente).
_POSE_PERFIL = ("DE PERFIL: cuerpo entero de costado, girado 90° respecto de la cámara, de pie "
                "con el peso en una pierna; se ve el LATERAL de la prenda (cómo van las tiras, "
                "los breteles y las costuras del costado, cuántas son y por dónde pasan, igual "
                "que en las fotos del producto). La cabeza apenas girada hacia la cámara o "
                "mirando al frente. Nada de 3/4: de costado.")
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
          **({"pose_txt": _POSE_PERFIL} if pose_raw == "perfil" else {}),
          "color_set": it.get("color", ""), "avatar_id": it.get("avatar_id"),
          "modelo_idx": k, "no_face_recreate": bool(it.get("avatar_id")),
          "indicacion": str(it.get("indicacion", "")).strip(),
          "outfit": str(it.get("outfit", "")).strip(),
          "modelo_spec": {
              "cuerpo_contextura": it.get("contextura", ""),
              "cuerpo_edad": it.get("edad", ""),
              "cuerpo_busto": it.get("busto", ""),
              "cuerpo_cola": it.get("cola", ""),
              "cuerpo_abdomen": it.get("abdomen", ""),
              "cuerpo_peinado": it.get("peinado", ""),
              "peinado_libre": it.get("peinado_libre", ""),
              "cuerpo_altura": it.get("altura", ""),
              "ap_etnia": it.get("etnia", ""),
              "ap_pelo": it.get("pelo", ""),
          }}
    # La prenda PROPIA de esta modelo, cuando el set no es "el mismo modelo en varios
    # colores" sino piezas distintas (otra estampa cada una). Si la tiene, esa foto —y
    # solo esa— es la verdad de SU toma.
    if pose_raw == "perfil":
        st.pop("force_pose", None)      # la pose de la lista le ganaba al perfil escrito
    guardada = str(it.get("pose_guardada") or "").strip()
    if guardada:
        # POSE ELEGIDA DE "MIS POSES" (copiada de una foto): su texto es la toma y su
        # maniquí guía lo encuentra el generador por ese texto.
        st["pose_txt"] = guardada
        st.pop("force_pose", None)
        pose = -1
    foto = str(it.get("foto") or "").strip()
    if foto:
        st["prod_img"] = foto
    if pose == 3:
        st["use_back"] = True
    return st


def _set_plan_trio(asign: List[Dict[str, str]], colores: List[str],
                   modo_producto: str = "suspendida",
                   extras: Optional[List[Dict[str, Any]]] = None,
                   inc_grupal: bool = True, inc_ind: bool = True,
                   inc_prod: bool = True,
                   grupal_indicacion: str = "",
                   grupal_pose: str = "") -> List[Dict[str, Any]]:
    """Set de lencería COMPONIBLE. Orden: PRIMERO las INDIVIDUALES (cada modelo sola, con las
    fotos reales del producto: es donde la prenda sale con todo su detalle), DESPUÉS la
    GRUPAL, que copia a las modelos ya hechas. Antes iba al revés: la grupal primero y
    cada individual anclada a su recorte, y como en la grupal la prenda sale chiquita, sus
    errores (2 tiras en vez de 3, costuras perdidas) se copiaban a todo el set."""
    if not asign and colores:
        asign = [{"color": c.strip()} for c in colores if c.strip()][:SET_MAX_MODELOS]
    a = (asign or [])[:SET_MAX_MODELOS]
    steps: List[Dict[str, Any]] = []
    # La GRUPAL necesita al menos DOS: con una sola no hay foto de grupo. Antes pedía 3
    # porque el set era siempre de a tres y el modo trío rellenaba e inventaba modelos.
    if inc_ind:
        for k, it in enumerate(a):
            steps.append(_mk_ind_step(it, k))
    if inc_grupal and len(a) >= 2:
        # Sólo es "crítica" (frena el set si falla) cuando no hay individuales antes.
        steps.append({"mode": "trio", "aspect": "4:5", "paneles": 1, "asign": a,
                      "indicacion": grupal_indicacion, "critical": not inc_ind,
                      "pose_guardada": grupal_pose})
    for ex in (extras or []):
        quien = str(ex.get("quien", "")).lower()
        if quien in ("grupal", "3", "todas", "todos"):
            if len(a) >= 2:
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


def _set_plan_kids(asign: List[Dict[str, Any]], modo_producto: str = "flat_lay",
                    inc_juntas: bool = True, inc_ind: bool = True,
                    con_modelo: bool = True) -> List[Dict[str, Any]]:
    """Set kids: respeta el modo solicitado; no clasifica prendas ni aplica safety propio."""
    a = (asign or [])[:SET_MAX_MODELOS]
    steps: List[Dict[str, Any]] = []

    if con_modelo:
        if inc_juntas and len(a) >= 2:
            steps.append({
                "mode": "trio", "aspect": "4:5", "paneles": 1,
                "asign": a, "indicacion": "", "critical": True
            })
        if inc_ind:
            for k, it in enumerate(a):
                st: Dict[str, Any] = {
                    "mode": "on_model", "aspect": "4:5", "paneles": 1,
                    "force_pose": k % len(_KIDS_POSES), "avatar_id": None,
                    "no_face_recreate": False, "modelo_idx": k,
                    "color_set": str(it.get("color", "")).strip(),
                    "indicacion": str(it.get("indicacion", "")).strip(),
                    "modelo_spec": _kid_de_ficha(it),   # nena/nene, edad, pelo... de su ficha
                }
                foto = str(it.get("foto") or "").strip()
                if foto:
                    st["prod_img"] = foto
                steps.append(st)
        return steps

    # Producto solo: únicamente porque el usuario eligió ese modo.
    if inc_juntas and len(a) >= 2:
        cols = ", ".join(str(x.get("color", "")).strip() for x in a if str(x.get("color", "")).strip())
        fotos = [str(x.get("foto") or "") for x in a if x.get("foto")]
        st: Dict[str, Any] = {
            "mode": "product_only", "aspect": "4:5", "paneles": 1,
            "modo_producto": modo_producto, "critical": True,
            "kids_juntas": len(a),
            "indicacion": (
                f"FOTO DE PACK: las {len(a)} prendas del set, TODAS juntas en la misma imagen, "
                "acomodadas prolijas una al lado de la otra sobre la misma superficie, vistas "
                "desde arriba, a la misma escala y con la misma luz. "
                + (f"Los colores son: {cols}. " if cols else "")
                + "Son prendas DISTINTAS entre sí: cada una conserva su propio diseño y su "
                  "propio color, no las repitas ni las mezcles."
            ),
        }
        if fotos:
            st["prod_imgs"] = fotos
        steps.append(st)

    if inc_ind:
        for k, it in enumerate(a):
            paso: Dict[str, Any] = {
                "mode": "product_only", "aspect": "4:5", "paneles": 1,
                "modo_producto": modo_producto, "pieza_idx": k,
                "color_set": str(it.get("color", "")).strip(),
                "indicacion": str(it.get("indicacion", "")).strip(),
            }
            foto = str(it.get("foto") or "").strip()
            if foto:
                paso["prod_img"] = foto
            steps.append(paso)
    return steps

def _set_plan_kids_modelo(poses: List[int], include_product: bool,
                          modo_producto: str = "suspendida") -> List[Dict[str, Any]]:
    """Set de nenas/nenes con modelo IA: el MISMO set de adultos (una toma 4:5 por pose
    elegida + producto opcional), pero con las poses de chico. Sin poses elegidas van
    cuatro de siempre: de pie riéndose, sentado jugando, de espaldas y saltando."""
    if not poses:
        poses = [0, 3, 4, 5]
    steps: List[Dict[str, Any]] = []
    for k in poses:
        s: Dict[str, Any] = {"mode": "on_model", "aspect": "4:5", "paneles": 1,
                             "force_pose": int(k) % len(_KIDS_POSES), "avatar_id": None,
                             "kids": True}
        if int(k) % len(_KIDS_POSES) == 4:       # _KIDS_POSES[4] = de espaldas
            s["use_back"] = True
        steps.append(s)
    if include_product:
        steps.append({"mode": "product_only", "aspect": "4:5", "paneles": 1,
                      "modo_producto": modo_producto})
    return steps


def _set_plan_custom(poses: List[int], include_product: bool,
                     modo_producto: str = "suspendida",
                     camaras: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Set a medida: una imagen 4K por pose elegida (+ producto opcional). `camaras` viene
    alineada con `poses`: es el ángulo de cámara que la usuaria eligió para cada una."""
    cams = list(camaras or [])
    steps: List[Dict[str, Any]] = []
    for j, k in enumerate(poses):
        s: Dict[str, Any] = {"mode": "on_model", "aspect": "4:5", "paneles": 1,
                             "force_pose": int(k)}
        # La posición en el set: el ángulo en "Variado" y la parte del lugar salen de acá.
        # (Por número de pose no servía: tildar la 0 y la 9 daba el mismo ángulo.)
        s["camara_auto"] = j % len(CAMARA_POOL)
        _c = cam_idx(cams[j]) if j < len(cams) else None
        if _c is not None:
            s["camara"] = _c                      # ángulo elegido a mano: se respeta
        # 3 = DE ESPALDA, 10 = DETALLE DE ESPALDA → usan la foto de espalda si hay
        if int(k) in (3, 10):
            s["use_back"] = True
        steps.append(s)
    if include_product:
        steps.append({"mode": "product_only", "aspect": "4:5", "paneles": 1,
                      "modo_producto": modo_producto})
    return steps


def _set_plan_items(items: List[Dict[str, Any]], include_product: bool,
                    modo_producto: str = "suspendida") -> List[Dict[str, Any]]:
    """Set a medida donde cada toma dice de dónde sale su pose —del listado o escrita por
    la usuaria— y con qué ángulo de cámara. Antes eran dos caminos separados: o tildabas
    poses del listado, o escribías las tuyas y las escritas MANDABAN sobre los tildes. No
    se podían combinar ni elegirles el ángulo."""
    steps: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        txt = str(it.get("texto") or "").strip()
        if txt:
            hechos = _set_plan_poses_txt([txt], False)   # reusa "producto solo" y "espalda"
            if not hechos:
                continue
            s = hechos[0]
        else:
            k = int(it.get("pose") or 0)
            s = {"mode": "on_model", "aspect": "4:5", "paneles": 1, "force_pose": k}
            if k in (3, 10):     # 3 = DE ESPALDA, 10 = DETALLE DE ESPALDA
                s["use_back"] = True
        if s["mode"] == "on_model":
            # La POSICIÓN de la toma entre las que llevan modelo: de ahí salen el ángulo
            # en "Variado" y la parte del lugar que le toca recorrer.
            s["camara_auto"] = sum(1 for x in steps if x["mode"] == "on_model") % len(CAMARA_POOL)
            c = cam_idx(it.get("camara"))
            if c is not None:
                s["camara"] = c                      # el ángulo elegido a mano lo pisa
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
        if _toma_atras_pose(low):
            st["use_back"] = True
        steps.append(st)
    if include_product:
        steps.append({"mode": "product_only", "aspect": "4:5", "paneles": 1,
                      "modo_producto": modo_producto})
    return steps


def _con_posicion(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cada toma con modelo lleva su POSICIÓN en el set (camara_auto): de ahí salen el ángulo
    de cámara en "Variado" y la parte del lugar. Varios armados de set (individuales de cada
    modelo, packs de kids, poses escritas) no la ponían, y TODAS las tomas caían en la cámara
    0 ("a la altura de los ojos, de frente") y en el mismo rincón: el set no recorría nada."""
    out, pos = [], 0
    for sd in steps:
        if sd.get("mode") == "on_model":
            if sd.get("camara_auto") is None:
                sd = {**sd, "camara_auto": pos % len(CAMARA_POOL)}
            pos += 1
        out.append(sd)
    return out


def _build_step_payload(base: Dict[str, Any], sdef: Dict[str, Any],
                        anchors: Optional[List[str]]) -> Dict[str, Any]:
    p: Dict[str, Any] = {
        "mode": sdef["mode"],
        "user_sub": base.get("user_sub"),
        "image_size": base.get("image_size", "4K"),
        "aspect": sdef["aspect"],
        "paneles": sdef["paneles"],
        "product_images": base.get("product_images") or [],
        "product_tags": base.get("product_tags") or [],
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
        if sdef.get("camara") is not None:
            p["camara"] = sdef["camara"]
        if sdef.get("camara_auto") is not None:
            p["camara_auto"] = sdef["camara_auto"]
        extra = {}
        if sdef.get("pose_txt"):
            extra["pose"] = sdef["pose_txt"]   # pose escrita a mano por la usuaria
            # Su toma escrita decide también el encuadre: el "Encuadre de la prenda" del
            # formulario (el desplegable) iba ANTES de su texto diciendo "manda sobre el
            # tamaño de plano de cualquier pose", y el "Encuadre" libre lo repetía después.
            # En una toma escrita los dos se apagan; las del listado los siguen usando.
            extra["encuadre_zona"] = ""
            extra["encuadre"] = ""
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
        if sdef.get("outfit"):
            # Lo que ESTA modelo lleva además de la prenda (su buzo, sus zapatillas): pisa las
            # "Piezas" generales, que son de todas.
            extra["piezas"] = ("usa la prenda del producto con " + sdef["outfit"]
                               + " (eso NO es parte del producto)")
        ind = str(sdef.get("indicacion", "")).strip()
        if ind:
            prev = str((base.get("params") or {}).get("aclaraciones", "")).strip()
            extra["aclaraciones"] = ((prev + " ") if prev else "") + \
                "DIRECCIÓN DE ESTA TOMA (seguila): " + ind
        if extra:
            p["params"] = {**(base.get("params") or {}), **extra}
        # La pose de ESTA toma la fija el plan (force_pose / pose_offset). La "Pose"
        # escrita en el formulario de Generar es para la foto suelta: si se colara
        # acá, todas las tomas del set saldrían con esa misma pose (con Seedream pasaba).
        if ("force_pose" in sdef or "pose_offset" in sdef) and not sdef.get("pose_txt"):
            p["params"] = {k: v for k, v in (p.get("params") or base.get("params") or {}).items()
                           if k != "pose"}
    elif sdef["mode"] == "trio":
        p["fotos_modo"] = base.get("fotos_modo", "color")
        if sdef.get("pose_guardada"):
            p["pose_guia_txt"] = sdef["pose_guardada"]
            _prev = str((p.get("params") or base.get("params") or {}).get("aclaraciones", "")).strip()
            p["params"] = {**(p.get("params") or base.get("params") or {}),
                           "aclaraciones": ((_prev + " ") if _prev else "")
                           + "TOMA GRUPAL (copiala tal cual, con la guía): " + sdef["pose_guardada"]}
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
        extra_p: Dict[str, Any] = {}
        # El color de ESTA pieza. Sin esto, un set de kids —donde cada toma es de
        # producto y no hay modelo— salía entero del color de la foto original.
        if sdef.get("color_set"):
            extra_p["color_set"] = sdef["color_set"]
        ind = str(sdef.get("indicacion", "")).strip()
        if ind:
            prev = str((base.get("params") or {}).get("aclaraciones", "")).strip()
            extra_p["aclaraciones"] = ((prev + " ") if prev else "") + \
                "PRESENTACIÓN PEDIDA POR LA USUARIA (seguila tal cual): " + ind
        if extra_p:
            p["params"] = {**(base.get("params") or {}), **extra_p}
    # Si es la toma de espalda y hay foto de espalda, esa pasa a ser la verdad
    back = base.get("product_images_back") or []
    # La foto de espalda SOLO se usa en tomas de una sola pose (nunca en paneles
    # múltiples, donde arruinaría las poses de frente que comparten la imagen).
    if sdef.get("use_back") and back and int(sdef.get("paneles", 1)) <= 1:
        # Los FRENTES van PRIMERO (definen la prenda: estampa, colores, cortes) y la
        # ESPALDA va ÚLTIMA, rotulada. Al revés (espalda primera) el motor la tomaba como
        # "así es la prenda" y sacaba una prenda lisa, sin el estampado del frente.
        _all = base.get("product_images") or []
        _tags_all = list(base.get("product_tags") or [])
        _tags_all += [""] * (len(_all) - len(_tags_all))
        _keep = [(f, t) for f, t in zip(_all, _tags_all) if f not in back]
        _front = [f for f, _ in _keep]
        p["product_images"] = _front + back
        # Las etiquetas se reordenan IGUAL que las fotos, si no quedarían corridas.
        p["product_tags"] = [t for _, t in _keep] + ["espalda"] * len(back)
        p["product_images_back"] = back        # para el inspector
        p["n_back_last"] = len(back)           # rotula cuáles son de espalda
    elif back:
        # En las tomas de FRENTE la espalda TAMBIÉN viaja al generador (antes iba solo al
        # inspector): si la prenda tiene un diseño atrás distinto del frente, lo que se
        # asoma de la espalda sobre los hombros y los costados tiene que salir con ESE
        # diseño. Sin la foto, el motor lo pintaba liso.
        _base_imgs = list(base.get("product_images") or [])
        _base_tags = list(base.get("product_tags") or [])
        _base_tags += [""] * (len(_base_imgs) - len(_base_tags))
        _extra = [b for b in back if b not in _base_imgs][:2]
        if _extra:
            p["product_images"] = (_base_imgs + _extra)[:6]
            p["product_tags"] = (_base_tags + ["espalda"] * len(_extra))[:6]
        p["product_images_back"] = back
    # PRENDA PROPIA DE ESTA MODELO. Va al final para pisar todo lo de arriba: si esta
    # pieza del set es OTRA prenda (otra estampa), las fotos compartidas —y la de
    # espalda, que es de otra pieza— no son su verdad y le cambiarían el diseño.
    propia = str(sdef.get("prod_img") or "").strip()
    if propia and base.get("fotos_modo", "color") == "color" and base.get("product_images"):
        # SÓLO EL COLOR: las fotos principales (frente, espalda, perfil) siguen siendo la
        # verdad del diseño; la de esta pieza entra rotulada "color", antes de las espaldas
        # (que tienen que quedar últimas, así se rotulan).
        _imgs = list(p.get("product_images") or [])
        _tg = list(p.get("product_tags") or [])
        _tg += [""] * (len(_imgs) - len(_tg))
        _nb = int(p.get("n_back_last") or 0)
        _pos = max(1, len(_imgs) - _nb)
        _imgs.insert(_pos, propia)
        _tg.insert(_pos, "color")
        if len(_imgs) > 6:          # el generador usa hasta 6: se cae una de frente, nunca la de color
            _quita = next((j for j in range(len(_imgs) - 1, 0, -1)
                           if _tg[j] not in ("color",) and not _es_tag_espalda(_tg[j])), None)
            if _quita is not None:
                _imgs.pop(_quita)
                _tg.pop(_quita)
        p["product_images"], p["product_tags"] = _imgs[:6], _tg[:6]
        propia = ""
    if propia:
        p["product_images"] = [propia]
        p["product_tags"] = ["frente"]
        p["product_images_back"] = []
        p["n_back_last"] = 0
    # Foto de PACK del set de kids: van TODAS las piezas juntas a la misma toma,
    # porque la imagen tiene que mostrarlas a las N y cada una con su diseño.
    varias = [x for x in (sdef.get("prod_imgs") or []) if x]
    if varias:
        p["product_images"] = varias[:6]
        p["product_tags"] = ["frente"] * len(varias[:6])
        p["product_images_back"] = []
        p["n_back_last"] = 0
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
        settings_job = await get_settings()
        steps = _con_posicion(_plan_para_motor(base.get("plan") or _set_plan(bool(base.get("hq"))),
                                               base.get("engine") or settings_job.get("engine", "gemini")))
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
                # La toma de ESPALDA (o detalle de espalda) va sin la referencia de frente:
                # la confundía y sacaba la espalda con el diseño del frente.
                use_anchors = ([_dataurl_to_anchor(group_crops[k])]
                               if (group_crops and k < len(group_crops) and group_crops[k]
                                   and sdef.get("force_pose") not in (3, 10)) else None)
            elif base.get("no_anchors"):
                use_anchors = None
            else:
                use_anchors = (_anchors_para_envio(anchors)
                               if (i > 0 and sdef["mode"] == "on_model") else None)
            payload = _build_step_payload(base, sdef, use_anchors)
            if sdef["mode"] == "trio" and any(group_crops):
                # La GRUPAL copia a cada modelo de SU individual ya hecha (cara, cuerpo, prenda).
                payload["ind_refs"] = [(c if c else "") for c in group_crops]
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
            if sdef["mode"] == "trio" and not any(group_crops):
                # Grupal sin individuales antes (sólo grupal + tomas extra): sus recortes
                # siguen siendo la referencia de cada modelo.
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
                         or _toma_atras_pose(_pt_a)
                         or any(w in _pt_a for w in ("detalle", "sin cara")))
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
    out: Dict[str, Any] = {"job_id": jid, "status": "running"}
    _pk = payload.get("params") or {}
    return out
 

@router.post(ROUTE_PREFIX + "/api/set")
async def api_set(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Genera el SET completo en segundo plano (encadenado en el server, resumible)."""
    hq = bool(payload.get("hq"))
    _settings_set = await get_settings()
    base = {
        "hq": hq,
        # El motor queda fijado al crear el set: el plan y las tomas se leen con él.
        "engine": str(payload.get("engine") or _settings_set.get("engine") or "gemini").strip().lower(),
        "user_sub": session_sub_from_request(request),
        "avatar_id": payload.get("avatar_id"),
        "product_images": _shrink_products(payload.get("product_images")),
        "product_images_back": _shrink_products(payload.get("product_images_back")),
        # Las etiquetas de cada foto (frente / espalda / perfil...). Antes el set no las
        # guardaba y cada toma llegaba sin saber cuál foto era la espalda.
        "product_tags": [str(t or "") for t in (payload.get("product_tags") or [])][:6],
        # La foto de cada pieza del set: "color" = sólo el color (el diseño sale de las
        # fotos principales); "prenda" = es otra prenda (otra estampa).
        "fotos_modo": ("prenda" if str(payload.get("fotos_modo") or "") == "prenda" else "color"),
        "image_size": payload.get("image_size", "4K"),
        "style": payload.get("style", ""),
        "reframe": payload.get("reframe"),
        "params": payload.get("params") or {},
        "save_to_drive": payload.get("save_to_drive", True),
    }
    total = len(_plan_para_motor(_set_plan(hq), base["engine"]))
    colores = payload.get("colores")
    asign = payload.get("asign")
    poses = payload.get("poses")
    poses_txt = payload.get("poses_texto")
    items = payload.get("items")
    items = [x for x in items if isinstance(x, dict)][:20] if isinstance(items, list) else None
    if items and poses_txt is None:
        # Para el camino de kids, que arma su propio plan: las poses escritas de la lista.
        _t = [str(x.get("texto") or "").strip() for x in items]
        poses_txt = [x for x in _t if x] or None
        if poses_txt is None:
            poses = [int(x.get("pose") or 0) for x in items if not str(x.get("texto") or "").strip()]
    if isinstance(poses_txt, str):
        poses_txt = [ln for ln in poses_txt.replace(";", "\n").split("\n") if ln.strip()]
    if isinstance(colores, str):
        colores = [c for c in colores.replace(";", ",").split(",") if c.strip()]
    if isinstance(asign, list) and len(asign) > 0:
        # La foto propia de cada modelo se achica igual que las compartidas: viaja
        # adentro del plan, que se guarda entero en el KV, y en crudo lo revienta.
        asign = [dict(it) for it in asign]
        for it in asign:
            if it.get("foto"):
                _f = _shrink_products([it["foto"]])
                it["foto"] = _f[0] if _f else ""
    if isinstance(asign, list) and len(asign) > 0 and _es_kids(base["params"]):
        # PACK DE NENAS/NENES (de 2 a 6 prendas): la foto de pack (todas juntas) más una
        # por prenda, con las prendas solas o con un chico por prenda, según eligió la
        # usuaria en el panel. Mismo panel que el set de colores, sin menú aparte.
        con_modelo = str(payload.get("kids_pack") or "solas").strip().lower() == "modelo"
        plan = _set_plan_kids(asign, str(payload.get("modo_producto") or "flat_lay"),
                              inc_juntas=payload.get("inc_grupal", True),
                              inc_ind=payload.get("inc_ind", True),
                              con_modelo=con_modelo)
        base["plan"] = plan
        base["avatar_id"] = None
        if con_modelo:
            base["group_anchor_mode"] = True   # cada chico usa SU toma como referencia
        total = len(plan)
    elif isinstance(asign, list) and len(asign) > 0:
        plan = _set_plan_trio(asign, [], payload.get("modo_producto", "suspendida"),
                              extras=payload.get("extras"),
                              inc_grupal=payload.get("inc_grupal", True),
                              inc_ind=payload.get("inc_ind", True),
                              inc_prod=payload.get("inc_prod", True),
                              grupal_indicacion=str(payload.get("grupal_indicacion", "")),
                              grupal_pose=str(payload.get("grupal_pose", "") or "").strip())
        base["plan"] = plan
        base["group_anchor_mode"] = True    # cada modelo usa SU individual como referencia
        total = len(plan)
    elif isinstance(colores, list) and len(colores) > 0:
        plan = _set_plan_trio([], colores, payload.get("modo_producto", "suspendida"),
                              extras=payload.get("extras"),
                              inc_grupal=payload.get("inc_grupal", True),
                              inc_ind=payload.get("inc_ind", True),
                              inc_prod=payload.get("inc_prod", True),
                              grupal_indicacion=str(payload.get("grupal_indicacion", "")),
                              grupal_pose=str(payload.get("grupal_pose", "") or "").strip())
        base["plan"] = plan
        base["group_anchor_mode"] = True
        total = len(plan)
    elif _es_kids(base["params"]):
        # NENAS/NENES: el mismo set de adultos (poses tildadas o escritas + producto),
        # con el chico inventado por la IA. Siempre Nano Banana.
        incp = bool(payload.get("include_product", True))
        modo_p = payload.get("modo_producto", "suspendida")
        base["avatar_id"] = None
        if isinstance(poses_txt, list) and len(poses_txt) > 0:
            plan = _set_plan_poses_txt([str(x) for x in poses_txt][:9], incp, modo_p)
        else:
            plan = _set_plan_kids_modelo([int(x) for x in (poses or [])][:14], incp, modo_p)
        base["plan"] = plan
        total = len(plan)
    elif items:
        incp = bool(payload.get("include_product", True))
        plan = _set_plan_items(items, incp, payload.get("modo_producto", "suspendida"))
        base["plan"] = plan
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
        plan = _set_plan_custom([int(x) for x in poses][:14], incp, modo_p,
                                camaras=payload.get("camaras"))
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
    out = {"job_id": jid, "status": "running", "total": total}
    if base.get("aviso"):
        out["aviso"] = base["aviso"]
    return out


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
            **({"motor": opt["motor"]} if opt.get("motor") else {}),
            **({"nota": str(opt["note"])[:300]} if opt.get("note") else {}),
            **({"aviso": str(opt["aviso"])[:300]} if opt.get("aviso") else {}),
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
            # "memoria" = SIN Redis: los trabajos viven en la RAM del server y si Railway
            # lo reinicia se pierden a mitad de camino (pantalla colgada).
            "almacenamiento": kv.backend,
            "prompt_flux": dict(_FLUX_PROMPT_STATS),
            "error_fal": (await kv.get(_pfx() + "lastfalerror")) or {},
            # Último error NO previsto de un endpoint (el "500" pelado): tipo, mensaje y
            # traceback, guardados por el manejador global de main.py.
            "error_servidor": (await kv.get(_pfx() + "lasterror")) or {},
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
  /* Cada toma del set con su propio ángulo de cámara al lado */
  .pkrow{display:flex;align-items:center;gap:6px;min-width:0}
  .pkrow .pk{flex:1 1 auto;min-width:0}
  .pkrow .camsel{flex:0 0 50%;min-width:0;font-size:12px;padding:3px 4px;height:auto}
  .camayuda{margin:10px 0;border:1px solid var(--line);border-radius:10px;padding:6px 10px;background:var(--card-2)}
  .camayuda summary{cursor:pointer;font-size:13px;font-weight:500}
  .camayuda ul{margin:6px 0 0;padding-left:20px}
  .camayuda li{font-size:13px;color:var(--muted);margin:3px 0;line-height:1.45}
  .camayuda li b{color:var(--ink);font-weight:500}
  .tcard{border-top:1px solid var(--line);padding-top:4px;margin-top:8px}
  .pfm,.pfe{font-size:11px;border-radius:5px;padding:0 5px;margin-left:4px}
  .pfm{background:rgba(80,180,120,.2);color:#3a9a62}.pfe{background:rgba(220,160,60,.2);color:#b07a1e}
  .tcard:first-child{border-top:none;margin-top:0}
  details.adv{background:var(--card-2)!important}
  .note{background:rgba(201,168,107,.08);border:1px solid var(--line);border-radius:10px;padding:10px 12px;
    font-size:12.5px;color:var(--ink-soft);margin-top:10px}
  /* En celular, las tomas del set van una por renglón: con dos columnas el selector
     de ángulo quedaba tan angosto que se leía "🎲 Va…". */
  @media(max-width:560px){#pose-pick,#pose-pick-kids{grid-template-columns:1fr !important}
    .pkrow .camsel{flex:0 0 46%}
    .row,.row3{grid-template-columns:1fr}.grid-av{grid-template-columns:repeat(2,1fr)}
    main{padding:12px}.card{padding:16px}
    /* EL ENCABEZADO EN EL CELULAR. Medido en un iPhone (390x664): las 9 solapas se
       apilaban en 3 renglones y el encabezado, que es sticky, ocupaba 292 px — el 44%
       de la pantalla. Con eso, abrir "Opciones avanzadas" no mostraba casi nada.
       Ahora las solapas van en UN renglón que se corre con el dedo. */
    header{padding:9px 12px 7px}
    .mono{width:32px;height:32px;border-radius:9px;font-size:15px}
    .brand{font-size:18px}
    .brand small{font-size:9px;letter-spacing:.12em;margin-top:2px}
    .brandrow{gap:9px}
    .tabs{margin-top:8px;flex-wrap:nowrap;overflow-x:auto;padding-bottom:3px;
      scrollbar-width:none;-webkit-overflow-scrolling:touch}
    .tabs::-webkit-scrollbar{display:none}
    .tab{flex:0 0 auto;padding:7px 13px;font-size:14px}}
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
    <a class="tab" href="%%VIDEOS%%">🎬 Videos</a>
    <a class="tab" href="%%COMERCIALES%%">🎥 Comerciales</a>
    <a class="tab" href="%%PERSONAJES%%">👤 Personajes</a>
    <a class="tab" href="/reels?modo=storyboard">🎬 Storyboard</a>
    <a class="tab" href="/cine">🎞 Cine</a>
    <a class="tab" href="/lumen">🧊 Lumen</a>
    <a class="tab" href="/reels?modo=referencias">🧍 Referencias</a>
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
          <option value="vintage">Vintage de revista</option>
          <option value="vintage_suave">Vintage suave</option>
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
      <option value="interior_set">Ropa interior — set de colores (de 2 a 6 modelos, mujer u hombre)</option>
      <option value="kids">Nenas / Nenes — ropa, pijamas, ponchos (modelo nene/nena IA)</option>
    </select>
    <div id="wrap-noavatar">
    <label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin:6px 0">
      <input type="checkbox" id="g-no-avatar" style="width:auto;margin:0">
      <span>Sin avatar — que la IA invente la modelo (recomendado para bikini/lencería; usa el tipo de cuerpo elegido)</span>
    </label>
    </div>
    <div id="apar-box" style="display:none;border:1px dashed var(--line);border-radius:10px;padding:10px;margin:6px 0">
      <p class="hint" style="margin-top:0" id="apar-title">Apariencia de la modelo IA (no queda al azar):</p>
      <div class="row" id="wrap-genero">
        <div><label>Género</label>
          <select id="g-genero"><option value="mujer">Mujer</option><option value="hombre">Hombre</option></select>
        </div>
        <div></div>
      </div>
      <div class="row" id="wrap-kids-quien" style="display:none">
        <div><label>¿Nena o nene?</label>
          <select id="kids-quien">
            <option value="nena">Nena</option>
            <option value="nene">Nene</option>
          </select>
        </div>
        <div><label>Edad / talle</label>
          <select id="kids-talle">
            <option value="0-1">Bebé: 6 meses a 1 año</option>
            <option value="1-2">1 a 2 años</option>
            <option value="2-4">2 a 4 años</option>
            <option value="4-6">4 a 6 años</option>
            <option value="6-8" selected>6 a 8 años</option>
            <option value="8-10">8 a 10 años</option>
            <option value="10-12">10 a 12 años</option>
            <option value="12-14">12 a 14 años</option>
            <option value="14-16">14 a 16 años</option>
            <option value="16-18">16 a 18 años</option>
          </select>
        </div>
      </div>
      <div class="row">
        <div><label>Etnia / tez</label>
          <select id="ap-etnia"><option value="">(libre)</option><option value="latina">Latina</option><option value="morocha_tez_oscura">Morocha tez oscura</option><option value="caucasica">Caucásica clara</option><option value="afro">Afro piel oscura</option><option value="asiatica">Asiática</option><option value="mediterranea">Mediterránea</option><option value="arabe">Árabe / medio-oriente</option><option value="mestiza">Mestiza</option></select>
        </div>
        <div id="wrap-ap-edad"><label>Edad aprox.</label>
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
      <div class="row" id="wrap-kids-altura" style="display:none">
        <div><label>Altura para su edad</label>
          <select id="ap-altura-kids"><option value="">(libre)</option><option value="bajo">Bajito/a</option><option value="medio">Normal</option><option value="alto">Alto/a</option></select>
        </div>
        <div></div>
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

    <label>🎬 Escena lista <span class="q" title="Una producción ya armada: lugar, luz, estilo y poses con su ángulo. Elegís el avatar, subís las fotos de la prenda y tildás las poses que quieras en 'Elegir poses del set'.">?</span></label>
    <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      <select id="escena-list" style="flex:1;min-width:140px"><option value="">— ninguna —</option>%%ESCENASOPTS%%</select>
      <select id="escena-color" style="display:none;flex:1;min-width:120px"></select>
      <button class="ghost" id="escena-load">Cargar</button>
    </div>
    <input type="hidden" id="g-no-lenceria">
    <input type="hidden" id="g-pareo">
    <input type="hidden" id="g-ajustes">
    <div id="escena-set" style="display:none;border:1px dashed var(--rose-deep);border-radius:9px;padding:8px;margin:6px 0">
      <label style="margin:0">🎨 Esta escena como <b>set de 3</b> (una modelo por color + foto grupal)</label>
      <div class="row3" id="escena-set-cols"></div>
      <button class="go" id="escena-set-load" style="margin-top:6px;width:100%">🎨 Armar set de 3 con esta escena</button>
    </div>
    <p class="hint" id="escena-hint" style="margin:4px 0 0;display:none"></p>

    <label>Plantilla de artículo (llená la ficha una vez y reusala para cada color)</label>
    <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      <select id="tpl-list" style="flex:1;min-width:140px"><option value="">— elegir —</option></select>
      <button class="ghost" id="tpl-load">Cargar</button>
      <button class="ghost" id="tpl-save">Guardar</button>
      <button class="ghost" id="tpl-del">🗑</button>
    </div>

    <div id="wrap-avatar">
    <label>Avatar (modelo)</label>
    <div class="grid-av" id="gen-avatars" style="grid-template-columns:repeat(4,1fr)"></div>
    </div>

    <label>2) Subí la foto de tu prenda (podés subir varias: arriba, pantalón, detalle)</label>
    <div class="dz" id="dz-gen" onclick="document.getElementById('file-gen').click()">
      <div>Tocá para subir una o varias fotos de la prenda</div>
      <input type="file" id="file-gen" accept="image/*" multiple hidden>
    </div>
    <p class="hint" style="margin:6px 0 0">💡 Abajo de cada foto elegí <b>qué muestra</b> (frente, espalda, perfil, detalle…). Con eso la IA sabe qué copiar en cada toma y el inspector deja de confundir una foto de costado con “otro diseño”. Es opcional, pero mejora mucho el resultado.<br>👉 Si tu prenda tiene <b>distinto diseño adelante y atrás</b> (ej: frente liso y espalda a cuadros), subí la foto de <b>espalda</b> y etiquetala: así el diseño de atrás aparece bien hasta en las tomas de frente (en el hombro y los costados). Sumar una de <b>perfil</b> ayuda a que clave dónde cambia un diseño y empieza el otro.</p>

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
      <div><label>Luz</label><input id="g-luz" placeholder="sol natural, mucha luz"></div>
    </div>
    <div class="row3">
      <div><label>Puños</label><input id="g-punos" placeholder="elastizado / sin puño"></div>
      <div><label>Costuras</label><input id="g-costuras" placeholder="planas / overlock / sin costura"></div>
      <div><label>Cuello/escote</label><input id="g-cuello" placeholder="redondo / v / corazón"></div>
    </div>
    <label>La prenda NO tiene <span class="q" title="Lo que el generador suele inventar y tu prenda no tiene. Lo completa el análisis; si una foto sale con algo agregado (una costura, un moño), sumalo acá separado por comas.">?</span></label>
    <input id="g-notiene" placeholder="costuras en la copa, moño, encaje, aro...">
    <div class="row3">
      <div><label>Pose</label><input id="g-pose" placeholder="natural, espontánea"></div>
      <div><label>Ángulo de cámara <span class="q" title="Desde dónde mira la cámara. 'Variado' rota solo y no repite ángulo en el set. Si elegís uno a mano se respeta tal cual, incluso en lencería (ahí los contrapicados fuertes pueden hacer rebotar la imagen en Seedream). El ángulo nunca cambia el encuadre de la prenda: eso lo sigue mandando el encuadre.">?</span></label><select id="g-camara">%%CAMOPTS%%</select></div>
      <div><label>Fondo / escenario</label><input id="g-fondo" placeholder="pared mármol gris, alfombra, cálido"></div>
    </div>
    %%CAMAYUDA%%
    <div class="row3">
      <div><label>📷 Lente / enfoque <span class="q" title="La óptica: gran angular, retrato con fondo desenfocado, bokeh circular, teleobjetivo, todo nítido, macro… Vacío = como siempre.">?</span></label><select id="g-lente">%%LENTEOPTS%%</select></div>
      <div><label>📸 Cámara <span class="q" title="El look de una de las mejores cámaras del mundo (medio formato, Leica, película, celular…). Vacío = como siempre.">?</span></label><select id="g-camara-foto">%%CAMFOTOOPTS%%</select></div>
      <div><label>🏃 Movimiento <span class="q" title="Fotos de personas en movimiento: congelado, barrido con el fondo corrido, estela, flash con arrastre, caminando, saltando, girando, corriendo. Vacío = quieta, como siempre.">?</span></label><select id="g-movimiento">%%MOVOPTS%%</select></div>
    </div>
    <label class="pk" style="margin:2px 0 6px"><input type="checkbox" id="g-prompt-json" checked> 🧩 Mandar el prompt en JSON (sólo Nano Banana) <span class="q" title="El mismo pedido, ordenado en campos: lo que pediste (prenda, color, pose, lugar, luz, cámara) arriba de todo y el resto de las reglas en bloques con título. Sirve cuando sentís que con un texto tan largo elige qué tomar y qué no. Seedream sigue con texto corrido, que es como mejor anda. Si el texto se bloquea por el pedido, se reintenta solo en JSON (no con nenes). Vale también para Storyboard y Reels.">?</span></label>
    <div class="hint" id="g-json-stats" style="margin:-4px 0 6px"></div>
    <p class="hint" style="margin:2px 0 8px">✨ En <b>Automático</b> la app elige, para cada toma, la lente, la cámara y el movimiento que mejor le van a la prenda, la escena, el estilo y la pose (ej.: calle y caminando → 35 mm + Sony A1; lencería → 85 mm retrato + Hasselblad; detalle de tela → macro). Si sabés lo que querés, elegilo a mano.</p>
    <div class="row3" id="wrap-cuerpo-a">
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
      <div id="wrap-contextura">
        <label>Contextura</label>
        <select id="g-contextura"><option value="">(según avatar)</option><option value="delgada">Delgada</option><option value="atletica">Atlética</option><option value="curvy">Curvy</option><option value="talle_grande">Talle grande</option><option value="talle_extra_grande">Talle XXL (hasta 130+)</option></select>
      </div>
      <div id="wrap-edadcorp">
        <label>Edad corporal / física</label>
        <select id="g-edadcorp"><option value="">(según avatar)</option><option value="20">~20 (joven)</option><option value="30">~30</option><option value="40">~40</option><option value="50">~50</option></select>
      </div>
      <div id="wrap-altura">
        <label>Altura</label>
        <select id="g-altura"><option value="">(según avatar)</option><option value="baja">Baja ~1,55</option><option value="media">Media ~1,65</option><option value="alta">Alta ~1,75</option><option value="muy_alta">Muy alta 1,80+</option></select>
      </div>
      <div>
        <label>Peinado <span class="q" title="El color de pelo se mantiene del avatar; acá elegís el estilo.">?</span></label>
        <select id="g-peinado"><option value="">(según avatar)</option><option value="largo_suelto">Largo suelto</option><option value="largo_ondulado">Largo ondulado</option><option value="media_melena">Media melena</option><option value="corto">Corto</option><option value="atado">Atado (cola)</option><option value="rodete">Rodete/moño</option><option value="trenza">Trenza</option></select>
        <input id="g-peinado-libre" placeholder="o escribilo: ej. recogido bajo con mechones sueltos" style="margin-top:6px">
      </div>
    </div>
    <div style="margin-bottom:10px">
      <label>Onda de la modelo (época) <span class="q" title="Arregla a la modelo como en esa época: pelo, maquillaje, uñas y accesorios. NO toca la prenda (sigue siendo la del producto real) ni la cara del avatar. Si elegís peinado o escribís accesorios acá abajo, esos mandan sobre los del estilo. El lugar y la decoración salen de lo que escribas en Fondo/escenario.">?</span></label>
      <select id="g-estilo-avatar">%%ESTAVOPTS%%</select>
      <p class="hint" id="estav-ayuda" style="margin:4px 0 0"></p>
    </div>
    <div>
      <label>Accesorios (texto libre) <span class="q" title="Lo que quieras sumarle a la modelo: sombrero, aritos, pulseras, collar, tatuajes, anteojos de sol, etc.">?</span></label>
      <input id="g-accesorios" placeholder="ej: sombrero de playa, aritos dorados, tatuaje en el brazo">
    </div>
    <div style="margin-top:12px" id="wrap-complemento">
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

    <div class="row3">
      <div>
        <label>Estilo</label>
        <select id="g-style">
          <option value="instagram_real" selected>Instagram casual realista</option>
          <option value="catalogo">Catálogo sobrio</option>
          <option value="editorial">Editorial / campaña</option>
          <option value="vintage">Vintage de revista (film, grano)</option>
          <option value="vintage_suave">Vintage suave (apenas de film)</option>
        </select>
      </div>
      <div>
        <label>Encuadre de la prenda <span class="q" title="Manda sobre el tamaño de plano de cada pose: la pose pone la postura y el gesto, este encuadre decide qué parte del cuerpo entra en el cuadro. Vale para la foto suelta, el set de poses y el set de colores. Para una bombacha o un bóxer elegí 'Prenda de abajo'; para un corpiño o top, 'Prenda de arriba'.">?</span></label>
        <select id="g-encuadre-zona">
          <option value="" selected>(según la pose)</option>
          <option value="abajo">Prenda de ABAJO: de la cintura para abajo (sin cara)</option>
          <option value="abajo_cerca">Prenda de ABAJO, de cerca: de la cintura a las rodillas</option>
          <option value="arriba">Prenda de ARRIBA: de la cintura para arriba (con cara)</option>
          <option value="arriba_cerca">Prenda de ARRIBA, de cerca: de los hombros a la cintura</option>
          <option value="medio">Plano medio: de la cabeza a la cadera</option>
          <option value="entero">Cuerpo entero siempre</option>
        </select>
      </div>
      <div>
        <label>Encuadre extra (texto libre)</label>
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
      <p class="hint" style="margin:8px 0">Tildá las tomas que querés en tu set y, al lado de
      cada una, desde dónde la mira la cámara (🎲 Variado = rota sola y no repite ángulo). Cada una sale en 4K. Si no tocás nada, el set usa las 4 poses de siempre + producto.</p>
      <div id="pose-pick" style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
        <div class="pkrow"><label class="pk"><input type="checkbox" value="0" checked> De pie (mano en el pelo)</label><select class="camsel" data-p="0">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="1" checked> 3/4 sobre el hombro</label><select class="camsel" data-p="1">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="2" checked> Sentada</label><select class="camsel" data-p="2">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="3" checked> De espalda</label><select class="camsel" data-p="3">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="4"> Caminando</label><select class="camsel" data-p="4">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="5"> Riéndose</label><select class="camsel" data-p="5">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="6"> De perfil apoyada</label><select class="camsel" data-p="6">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="7"> Estirándose / bretel</label><select class="camsel" data-p="7">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="8"> Primer plano de cara</label><select class="camsel" data-p="8">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="9"> 🔍 Detalle prenda (zoom, sin cara)</label><select class="camsel" data-p="9">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="10"> 🔍 Detalle espalda (atándose)</label><select class="camsel" data-p="10">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="11"> Fundida con el ambiente</label><select class="camsel" data-p="11">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="12"> Tirada en el piso / arena</label><select class="camsel" data-p="12">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" value="13"> Con utilería (fruta, vaso...)</label><select class="camsel" data-p="13">%%CAMOPTS%%</select></div>
        <div class="pkrow"><label class="pk"><input type="checkbox" id="pk-prod" checked> Producto (colgado)</label></div>
      </div>
      <div id="pose-pick-kids" style="display:none;grid-template-columns:1fr 1fr;gap:6px">
        <label class="pk"><input type="checkbox" value="0" checked> De pie riéndose (cuerpo entero)</label>
        <label class="pk"><input type="checkbox" value="1"> Corriendo hacia la cámara</label>
        <label class="pk"><input type="checkbox" value="2"> Envuelto/a, recién salido/a del agua</label>
        <label class="pk"><input type="checkbox" value="3" checked> Sentado/a en el piso jugando</label>
        <label class="pk"><input type="checkbox" value="4" checked> De espaldas mirando el agua</label>
        <label class="pk"><input type="checkbox" value="5" checked> Saltando con los brazos abiertos</label>
        <label class="pk"><input type="checkbox" value="6"> De perfil caminando</label>
        <label class="pk"><input type="checkbox" value="7"> Abrazando una pelota inflable</label>
        <label class="pk"><input type="checkbox" value="8"> Manos en la cintura, orgulloso/a</label>
        <label class="pk"><input type="checkbox" value="9"> Primer plano de cara (capucha / cuello)</label>
        <label class="pk"><input type="checkbox" id="pk-prod-kids" checked> Producto (colgado)</label>
      </div>
      %%CAMAYUDA%%
      <div id="wrap-escena-poses" style="display:none">
        <label style="margin-top:14px" id="escena-poses-titulo">🎬 Poses de la escena</label>
        <p class="hint" style="margin:4px 0 8px">Cada una ya trae su ángulo (lo podés cambiar). Tildá las que quieras.</p>
        <div id="escena-poses" style="display:grid;gap:6px"></div>
      </div>
      <label style="margin-top:14px">✍️ Mis poses</label>
      <p class="hint" style="margin:4px 0 8px">Las que escribís vos. <b>Quedan guardadas en tu cuenta</b>,
      así que las tildás igual que las de arriba y podés combinarlas con ellas, cada una con su ángulo.
      Se respeta tal cual lo que escribís. Si una dice "las prendas colgadas solas" o algo parecido, esa toma sale sin modelo.</p>
      <div id="mis-poses" style="display:grid;gap:6px"></div>
      <div style="display:flex;gap:6px;align-items:flex-start;margin-top:8px">
        <textarea id="g-pose-nueva" rows="2" placeholder="Ej: sentada en un sillón, de 3/4, mirando por la ventana&#10;(podés pegar varias, una por línea)" style="flex:1;min-width:0"></textarea>
        <button class="ghost" id="btn-pose-add" style="white-space:nowrap">➕ Agregar</button>
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-top:6px;flex-wrap:wrap">
        <label class="ghost" style="cursor:pointer;margin:0;padding:6px 10px;border:1px solid var(--line);border-radius:9px">📷 Copiar toma de una foto
          <input type="file" id="g-pose-foto" accept="image/*" style="display:none"></label>
        <button type="button" class="ghost" id="g-pose-lumen" style="margin:0;padding:6px 10px">🧊 Traer toma de Lumen</button>
        <div id="g-pose-guia" style="width:100%;display:flex;gap:8px;align-items:center"></div>
        <div id="g-pose-perfil" style="width:100%"></div>
        <span class="hint" id="g-pose-foto-est" style="margin:0">El inspector copia la TOMA entera: cámara (altura, ángulo, distancia, lente), encuadre, pose, postura y gesto de la cara. No copia a la persona, la ropa ni el fondo. Revisala y tocá Agregar.</span>
      </div>
    </details>
    <details id="wrap-colores" style="display:none;margin:6px 0 10px;border:1px solid var(--rose-deep);border-radius:10px;padding:8px 12px;background:var(--card-2)" open>
      <summary style="cursor:pointer;font-weight:500" id="set-titulo">🎨 Set de colores (seamless / ropa interior)</summary>
      <div class="row" style="margin:8px 0">
        <div>
          <label>¿De cuántas piezas es el set?</label>
          <select id="set-n">
            <option value="2">Set de 2</option>
            <option value="3" selected>Set de 3</option>
            <option value="4">Set de 4</option>
            <option value="5">Set de 5</option>
            <option value="6">Set de 6</option>
          </select>
        </div>
        <div id="set-genero-wrap">
          <label>¿Los modelos del set son…?</label>
          <select id="set-genero">
            <option value="mujer">Mujeres</option>
            <option value="hombre">Hombres</option>
          </select>
        </div>
      </div>
      <div id="colores-hints-adulto">
      <p class="hint" style="margin:0 0 8px">Elegí las dos cosas <b>antes</b> de generar: abajo aparece una ficha por pieza, con los campos del género que pediste. Si la prenda es solo la parte de abajo (un bóxer, por ejemplo) y querés decidir qué va arriba, escribilo en <b>Piezas</b> (ej. "torso desnudo" o "una remera blanca lisa").</p>
      <p class="hint" style="margin:8px 0">Una ficha por pieza (cada una con su color). Si elegís un avatar, la cara sale del avatar (etnia y pelo se toman de él). Si dejás "modelo IA", completá etnia y pelo. Cuerpo y edad valen siempre. El set sale con el género que elegiste arriba (mujer u hombre). La foto GRUPAL sale con 2 o más.</p>
      <p class="hint" style="margin:8px 0">¿Las piezas <b>no</b> son la misma prenda en otro color, sino <b>estampas distintas</b> (como un pack de 3 bóxers)? Subile a cada ficha <b>su propia foto</b>: esa pasa a ser la única verdad de esa pieza.</p>
      </div>
      <div id="kids-pack-wrap" style="display:none">
        <p class="hint" style="margin:0 0 8px">De 2 a 6 prendas de nenas/nenes: sale la <b>foto de pack</b> (todas juntas) más <b>una por prenda</b>. Una ficha por prenda, con su color y, si son estampas distintas, su propia foto.</p>
        <div class="row">
          <div><label>¿Cómo sale el pack?</label>
            <select id="kids-pack">
              <option value="solas" selected>Las prendas solas (pack + una por prenda)</option>
              <option value="modelo">Con modelo nene/nena (grupal + una por prenda)</option>
            </select>
          </div>
          <div id="kids-pack-prod-wrap"><label>Cómo se muestran las prendas</label>
            <select id="kids-pack-prod">
              <option value="flat_lay" selected>Flat-lay (acostadas prolijas, desde arriba)</option>
              <option value="maniqui_fantasma">Maniquí fantasma</option>
              <option value="tirada_piso">Tiradas en la arena / el piso</option>
              <option value="doblada">Dobladas</option>
              <option value="percha">En percha</option>
              <option value="suspendida">Colgadas de tanza invisible</option>
            </select>
          </div>
        </div>
      </div>
      <div id="trio-cards"></div>
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
        <label class="pk"><input type="checkbox" id="inc-grupal" checked><span id="lbl-inc-grupal"> Foto grupal (las 3)</span></label>
        <label class="pk"><input type="checkbox" id="inc-ind" checked><span id="lbl-inc-ind"> Fotos individuales</span></label>
        <label class="pk" id="inc-prod-wrap"><input type="checkbox" id="inc-prod"> Producto solo</label>
        <div style="grid-column:1/-1"><label>La foto de cada pieza es… <span class="q" title="Solo el color: las fotos principales de arriba (frente, espalda, perfil) son el diseño de TODAS las piezas, y la foto de cada pieza da sólo su color. Otra prenda: cada pieza es una prenda distinta (otra estampa) y su foto es todo su diseño.">?</span></label>
          <select id="set-fotos-modo"><option value="color" selected>🎨 Solo el color (el diseño sale de las fotos principales)</option><option value="prenda">🧩 Otra prenda distinta (otra estampa)</option></select></div>
        <label class="pk"><input type="checkbox" id="set-simple"> ✅ Prompt simple (estilo Playground) <span class="q" title="Manda un pedido corto en TODAS las tomas del set (grupal incluida): quiénes son las modelos, cuerpo entero, escenario y prenda. Sin medidas de cuerpo ni encuadres cerrados. Es la estructura que te funciona a mano.">?</span></label>
                      </div>
      <div style="margin-top:6px">
        <label>Indicaciones para la foto grupal (opcional)</label>
        <input id="g-tgrupal-ind" placeholder="ej: abrazadas riéndose, una despeinando a la otra">
        <label style="margin-top:8px">📷 Pose de la foto grupal (de tus fotos copiadas)</label>
        <input type="hidden" id="g-tgrupal-pose">
        <div data-selpose="g-tgrupal-pose" data-grupal="1"></div>
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
        <select id="one-pose" style="flex:1;min-width:140px"><!-- se llena por JS con TODAS las poses --></select>
        <select id="one-camara" style="flex:1;min-width:140px">%%CAMOPTS%%</select>
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
    <p class="hint">La prenda sola, sin personas: colgada, tirada en el piso vista desde arriba, flat-lay, doblada, percha o maniquí fantasma. Sirve para cualquier prenda, de adultos o de chicos (mallas y bikinis de nenas/nenes van por acá; la ropa de chicos con modelo va por Generar → Nenas / Nenes).</p>

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
    <label>La prenda NO tiene</label>
    <input id="pr-notiene" placeholder="costuras en la copa, moño, encaje...">
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
        <div><label>Modelo try-on (con avatar) <span class="q" title="Opciones probadas en fal: bytedance/seedream/v5/pro/edit (la mejor calidad, pero su checker de SALIDA tira poses provocativas en lencería) · alibaba/qwen-image-3/edit (el más nuevo de pesos abiertos: sin checker propio, identidad fuerte, hasta 3 referencias; US$0,075 la imagen; es el respaldo cuando el checker de Seedream rechaza) · fal-ai/qwen-image-edit-2511 (pesos abiertos, sin checker propio, identidad fuerte, acepta LoRA; US$0,035/MP) · fal-ai/flux-2/edit (pesos abiertos, hasta 4 referencias; US$0,012/MP). Ojo: en Qwen y FLUX, apagar el safety checker de fal requiere que tu cuenta de fal esté habilitada para contenido sin filtro.">?</span></label><input id="s-fluxtryon" placeholder="bytedance/seedream/v5/pro/edit"></div>
        <div><label>Modelo sin avatar / producto</label><input id="s-fluxedit" placeholder="bytedance/seedream/v5/pro/edit"></div>
      </div>
      <div><label>Precio por imagen FLUX (US$)</label><input id="s-precioflux" type="number" step="0.005" min="0"></div>
      <label style="margin-top:8px">Acabado 4K con Nano Banana <span class="q" title="Después de cada imagen de Seedream, Nano Banana la rehace idéntica en 4K (retocar una imagen existente no dispara su filtro). Suma calidad pero cuesta el precio de una imagen 4K extra (~US$0,15) por toma. Apagalo si preferís quedarte con la imagen directa de Seedream (2K, ~3MB).">?</span></label>
      <select id="s-seedream4k">
        <option value="si">Sí — rehacer cada imagen de Seedream en 4K (+US$0,15 c/u)</option>
        <option value="no">No — entregar la imagen directa de Seedream (2K)</option>
      </select>
      <label style="margin-top:8px">Variación automática por toma (cámara, recorrido, encuadre) <span class="q" title="Con 'Sí', cada toma del set rota sola el ángulo de cámara, la parte del lugar y el tamaño de plano. Con 'No' vuelve a como era antes: la pose trae su propio encuadre, la cámara va sólo si la elegís a mano en el selector, y no se recorre el lugar.">?</span></label>
      <select id="s-variacion">
        <option value="si">Sí — rota sola en cada toma</option>
        <option value="no">No — como antes (sólo lo que elijo a mano)</option>
      </select>
      <label style="margin-top:8px">Seedream: mandar sólo la cara del avatar <span class="q" title="Seedream es un editor: copia la composición de la primera imagen que recibe. Con el retrato entero (plano medio, de frente) todas las tomas del set salían con esa misma pose. Con sólo la cara, la identidad viene de la cara y la pose la manda el texto.">?</span></label>
      <select id="s-seedreamcara">
        <option value="si">Sí — sólo la cara (recomendado: respeta las poses)</option>
        <option value="no">No — el retrato entero (como antes)</option>
      </select>
      <label style="margin-top:8px">Claude con Seedream (escribe tu pedido y revisa la foto) <span class="q" title="Cuando escribís la toma, el fondo, la luz o indicaciones: Claude arma el pedido para Seedream corto, en inglés y con TU toma primero (Seedream agarraba lo que quería de un pedido largo), y después mira la foto. Si no cumplió, la pide una vez más con la corrección y queda la mejor. Cuesta unos centavos de Claude por foto, más una foto de Seedream cuando la repite. Si editás el prompt a mano, Claude no lo toca. Necesita ANTHROPIC_API_KEY en Railway.">?</span></label>
      <select id="s-claudeseedream">
        <option value="si">Sí — Claude escribe y revisa (recomendado)</option>
        <option value="no">No — el pedido de siempre, sin revisión</option>
      </select>
      <label style="margin-top:8px">Seedream: poses de catálogo en lencería y baño <span class="q" title="El checker de salida de ByteDance (no se apaga desde fal) tira la imagen ya generada cuando la pose muestra demasiado: acostada en el piso, de espalda con la mano en la nuca, acomodándose el bretel. Con esto, en lencería y baño cada pose del pool usa su versión de catálogo (misma variedad: parada, sentada, de espalda, caminando, perfil…). Si igual rebota, la toma se reintenta sola con la versión segura.">?</span></label>
      <select id="s-seedreamposes">
        <option value="si">Sí — poses de catálogo (recomendado: menos rebotes)</option>
        <option value="no">No — el pool completo (más rebotes del checker)</option>
      </select>
      <p class="hint" style="margin:6px 0 0">Con FLUX: el avatar va como "persona" y tus fotos del producto como "prenda" (try-on de verdad). El set de 3 modelos (trío) por ahora sigue en Nano Banana. Si un modelo de fal no existe o cambia de nombre, pegá acá el nuevo (fal.ai → Models).</p>
    </div>

    <div style="border:1px solid var(--rose-deep);border-radius:10px;padding:10px;margin:10px 0;background:var(--card-2)">
      <label style="margin:0">🔍 Inspector de fidelidad de prenda <span class="q" title="Después de cada generación, una IA de visión compara la prenda generada contra tus fotos reales: diseño, color, estampa, terminaciones, detalles inventados. Le pone nota 1-10 y si no llega a la mínima, regenera UNA vez pasándole las correcciones.">?</span></label>
      <div class="row3">
        <div><label>Inspector</label><select id="s-qc"><option value="si">Activado</option><option value="no">Apagado</option></select></div>
        <div><label>Nota mínima (1-10)</label><input id="s-qcumbral" type="number" min="1" max="10"></div>
        <div><label>Auto-corrección</label><select id="s-qcretry"><option value="si">Sí (1 reintento)</option><option value="no">No</option></select></div>
      </div>
      <div class="row">
        <div><label>2da revisión: anatomía y física <span class="q" title="Una segunda pasada que mira SOLO el cuerpo: brazos duplicados, manos deformes, gente flotando, escala del fondo. Separarla de la revisión de prenda es lo que hace que esos errores se detecten. Suma ~US$0,001 por imagen.">?</span></label>
          <select id="s-qcanat"><option value="si">Activada (recomendado)</option><option value="no">Apagada</option></select></div>
        <div><label>Modelo del inspector <span class="q" title="Flash: rápido y barato, va bien. Pro: más caro y lento pero ve detalles finos (una argolla, un pulgar al revés). Si el modelo elegido no está disponible, cae solo al que funciona.">?</span></label>
          <select id="s-qcmodel">
            <option value="gemini-3.1-flash">Flash — rápido y barato (recomendado)</option>
            <option value="gemini-3.1-pro">Pro — máxima precisión (más caro y lento)</option>
            <option value="gemini-3.1-flash-lite">Lite — el más barato</option>
          </select></div>
      </div>
      <p class="hint" style="margin:6px 0 0">La inspección cuesta centavos por imagen. Ahora corre en <b>dos pasadas</b> (prenda + anatomía) sobre la imagen en alta resolución y con razonamiento alto: la nota final es la PEOR de las dos. Si dispara la auto-corrección, se cobra una imagen más. La nota aparece con cada resultado: "✅ Prenda fiel — 9/10" o "⚠ con diferencias" y el detalle.</p>
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
          <option value="gemini-nano-banana-2.1">Nano Banana 2.1 (gemini-nano-banana-2.1) — el nuevo, recomendado</option>
          <option value="gemini-3.1-flash-image">Nano Banana 2 (gemini-3.1-flash-image)</option>
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
let GEN_PRODUCT_TAGS = [];   // qué muestra cada foto del producto (lo elige la usuaria)
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
async function startJob(endpoint,payload){const s=await jpost(endpoint,payload);if(!s||!s.job_id)throw new Error("No se pudo iniciar la generación");
  // El server puede haber cambiado algo del pedido (ej: kids con malla -> prenda sola).
  if(s.aviso&&typeof toast==="function")toast(s.aviso,false);
  return s.job_id;}
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
  const t0=Date.now();let unknownSince=0,netFails=0;
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
    // BUG viejo: si el server no respondía, el catch hacía "continue" y se SALTEABA el
    // control de tiempo de más abajo → la pantalla giraba para siempre.
    let st;
    try{st=await jget("/api/jobs/"+jid+"?t="+Date.now());netFails=0;}
    catch(e){
      netFails++;
      if(prog)prog.set(2,"El servidor no responde ("+netFails+")… reintentando");
      if(netFails>=12||Date.now()-t0>900000){
        if(prog)prog.fail("El servidor dejó de responder. Recargá la página: si el trabajo siguió, lo vas a ver al volver.");
        CURRENT_JOB=null;throw new Error("server_sin_respuesta");
      }
      continue;
    }
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
  if(!t.dataset.p)return;   // el chip de Videos es un link a otra pantalla
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
// ── Zonas de carga con BORRAR por foto y SUMAR sin perder las anteriores ──
// (antes cada carga reemplazaba todo y el innerHTML se comía el <input file>)
let DZ_SUMAR=false;   // true solo cuando se toca el botón "➕ Sumar"
const DZ_MAP={
  "dz-gen":{get:()=>GEN_PRODUCTS,set:v=>{GEN_PRODUCTS=v;},max:6,tags:true,
            txt:"Tocá para subir una o varias fotos de la prenda",
            after:()=>{GEN_FICHA="";const b=$("#ficha-box");if(b)b.style.display="none";
                       const s=$("#ficha-status");if(s)s.textContent="";}},
  "dz-gen-back":{get:()=>GEN_PRODUCTS_BACK,set:v=>{GEN_PRODUCTS_BACK=v;},max:3,
                 txt:"Tocá para subir la espalda de la prenda"},
  "dz-prod":{get:()=>PROD_PRODUCTS,set:v=>{PROD_PRODUCTS=v;},max:6,
             txt:"Tocá para subir una o varias fotos de la prenda"},
  "dz-col":{get:()=>COL_PRODUCTS,set:v=>{COL_PRODUCTS=v;},max:6,
            txt:"Tocá para subir la foto de la prenda"},
};
// ── ETIQUETAS POR FOTO: la usuaria dice qué muestra cada imagen del producto. Así el
// motor sabe cuál usar para la espalda y el inspector deja de leer un perfil como
// "otro diseño".
const TAG_OPTS=[["","(qué muestra…)"],["frente","Frente"],["espalda","Espalda"],
  ["perfil","Perfil / costado"],["detalle","Detalle de la tela"],
  ["colgada","Colgada / plana"],["arriba","Parte de arriba"],
  ["abajo","Parte de abajo"],["complemento","Pieza complementaria"]];
function tagSelectHTML(id,i){
  const v=(GEN_PRODUCT_TAGS[i]||"");
  return '<select class="dztag" data-dz="'+id+'" data-i="'+i+'" '
    +'style="width:100%;margin-top:4px;font-size:11px;padding:3px 4px;border-radius:6px;'
    +'border:1px solid '+(v?"var(--rose-deep)":"var(--line)")+';background:var(--card-2);color:var(--ink)">'
    +TAG_OPTS.map(o=>'<option value="'+o[0]+'"'+(o[0]===v?" selected":"")+'>'+o[1]+'</option>').join("")
    +'</select>';
}
function renderDZ(id){
  const d=DZ_MAP[id],zone=document.getElementById(id);if(!d||!zone)return;
  const inp=zone.querySelector('input[type=file]');   // hay que conservarlo
  const arr=d.get()||[];
  zone.innerHTML=(!arr.length?'<div>'+d.txt+'</div>'
    :'<div>Listo ✓ ('+arr.length+') — la ✕ borra una foto · tocar el recuadro CAMBIA todas '
     +'<button type="button" class="dzadd" data-dz="'+id+'" style="margin-left:6px;padding:4px 10px;'
     +'border-radius:8px;border:1px solid var(--line);background:var(--card-2);color:var(--ink);'
     +'font-size:12px;cursor:pointer">➕ Sumar</button></div>'
     +arr.map((u,i)=>'<span style="position:relative;display:inline-block;margin:6px;vertical-align:top">'
       +'<img src="'+u+'" style="max-height:90px;border-radius:6px;display:block">'
       +'<button type="button" class="dzx" data-dz="'+id+'" data-i="'+i+'" title="Borrar esta foto" '
       +'style="position:absolute;top:-7px;right:-7px;width:24px;height:24px;padding:0;line-height:1;'
       +'border-radius:50%;border:none;background:var(--bad);color:#fff;font-size:14px;cursor:pointer">✕</button>'
       +(d.tags?tagSelectHTML(id,i):"")
       +'</span>').join(''));
  if(inp)zone.appendChild(inp);
  zone.querySelectorAll(".dzx").forEach(b=>b.onclick=ev=>{
    ev.stopPropagation();ev.preventDefault();
    const dd=DZ_MAP[b.dataset.dz],a=(dd.get()||[]).slice();
    const _i=parseInt(b.dataset.i);a.splice(_i,1);
    if(b.dataset.dz==="dz-gen")GEN_PRODUCT_TAGS.splice(_i,1);
    dd.set(a);renderDZ(b.dataset.dz);
    if(dd.after)dd.after();
    toast("Foto borrada ✓");
  });
  const add=zone.querySelector(".dzadd");
  if(add&&inp)add.onclick=ev=>{ev.stopPropagation();ev.preventDefault();DZ_SUMAR=true;inp.click();};
  zone.querySelectorAll(".dztag").forEach(s=>{
    s.onclick=ev=>ev.stopPropagation();          // no abrir el selector de archivos
    s.onchange=ev=>{
      ev.stopPropagation();
      GEN_PRODUCT_TAGS[parseInt(s.dataset.i)]=s.value;
      s.style.borderColor=s.value?"var(--rose-deep)":"var(--line)";
    };
  });
}
// Elegir fotos REEMPLAZA lo que había (comportamiento de siempre: si cambiás de
// producto, no quedan mezcladas las fotos del anterior). Para sumar sin perder, está
// el botón "➕ Sumar" de cada zona.
async function dzAdd(id,inputEl,sumar){
  const d=DZ_MAP[id];if(!d||!inputEl.files.length)return;
  const nuevos=await readFiles(inputEl.files);
  if(id==="dz-gen"&&!sumar)GEN_PRODUCT_TAGS=[];   // producto nuevo: etiquetas de cero
  const total=(sumar?(d.get()||[]).slice():[]).concat(nuevos);
  if(total.length>d.max)toast("Máximo "+d.max+" fotos acá: dejé las primeras "+d.max+".",false);
  d.set(total.slice(0,d.max));
  inputEl.value="";                 // permite volver a elegir la misma foto
  renderDZ(id);
  if(d.after)d.after();
}
function dzOnChange(id){return e=>{const s=DZ_SUMAR;DZ_SUMAR=false;return dzAdd(id,e.target,s);};}
$("#file-gen").onchange=dzOnChange("dz-gen");
$("#file-gen-back").onchange=dzOnChange("dz-gen-back");
// Marca del bloque de "errores a evitar" que agrega el análisis (se reemplaza solo).
const AUTO_NEG_TAG="⚠ Evitar (del análisis de esta prenda):";
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
    const r=await jpost("/api/analyze",{product_images:imgs,product_images_back:backs,product_tags:GEN_PRODUCT_TAGS});
    GEN_FICHA=r.ficha_text||"";
    const f=r.ficha||{};
    // Autocompletar los campos con lo que dice ESTE análisis. Antes solo escribía si el
    // campo estaba VACÍO, así que al analizar otro producto no se actualizaba nada y
    // quedaban los datos de la prenda anterior.
    const setF=(id,v)=>{const el=$(id);if(el&&v)el.value=v;};
    setF("#g-tela",f.tela);
    setF("#g-color",f.color_base);
    setF("#g-cuello",f.cuello);
    setF("#g-punos",f.punos);
    setF("#g-costuras",f.costuras_detalles);
    if(Array.isArray(f.no_tiene)){const nt=f.no_tiene.map(x=>String(x).trim()).filter(Boolean).join(", ");
      if($("#g-notiene"))$("#g-notiene").value=nt; if($("#pr-notiene")&&!$("#pr-notiene").value)$("#pr-notiene").value=nt;}
    // "Errores a evitar": vuelven a verse en Aclaraciones, pero como BLOQUE AUTOMÁTICO
    // que se REEMPLAZA en cada análisis (antes se acumulaban los de todos los productos).
    // Lo que escribiste vos a mano queda intacto arriba del bloque.
    const el_ac=$("#g-aclaraciones");
    if(el_ac){
      const manual=(el_ac.value||"").split(AUTO_NEG_TAG)[0].trim();
      const negs=(Array.isArray(f.negativos_sugeridos)?f.negativos_sugeridos:[])
                   .filter(x=>String(x).trim());
      const auto=negs.length?(AUTO_NEG_TAG+" "+negs.join(", ")):"";
      el_ac.value=(manual+((manual&&auto)?"\n":"")+auto).trim();
    }
    $("#ficha-box").style.display="";
    $("#ficha-box").textContent=GEN_FICHA||"(sin texto)";
    $("#ficha-status").textContent="✓ ficha cargada — se usa en cada generación";
    toast("Ficha técnica lista ✓");
  }catch(e){$("#ficha-status").textContent="";toast(errMsg(e),true);}
  b.disabled=false;b.textContent=old;
};
$("#file-prod").onchange=dzOnChange("dz-prod");
$("#file-col").onchange=dzOnChange("dz-col");

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
  // Mis personajes (Personajes → ficha) también son modelos de Fotos: van primero.
  ["mujer","hombre"].forEach(g=>{data[g]=((data.personajes||[]).filter(p=>p.gender===g)).concat(data[g]||[]);});
  let any=false;
  ["mujer","hombre"].forEach(g=>data[g].forEach(av=>{
    if(!av)return;any=true;
    const d=document.createElement("div");d.className="slot filled";
    d.innerHTML='<img src="'+BASE+"/api/avatars/"+encodeURIComponent(av.id)+'/ref?t='+Date.now()+'" loading="lazy"><div class="meta"><span>'+(av.personaje?"🧍 ":"")+av.name+'</span></div>';
    d.onclick=()=>{GEN_AVATAR_ID=av.id;GEN_AVATAR_GENDER=g;$$("#gen-avatars .slot").forEach(x=>x.style.outline="");d.style.outline="3px solid var(--rose)";applyGenderUI();};
    cont.appendChild(d);
  }));
  if(!any)cont.innerHTML='<p class="hint">Todavía no tenés avatares. Creálos en la pestaña Avatares.</p>';
  AVATARES_SET=data;
  renderTrioCards();
}
// "Mis poses" se declara ACÁ ARRIBA: las fichas del set la leen apenas se dibujan, y
// declarada más abajo cortaba la carga de toda la página (sin avatares ni set).
var MIS_POSES=[];
// Los avatares que hay, para poder repintar las fichas del set cuando cambia el
// género o la cantidad de piezas sin volver a pedirlos al server.
let AVATARES_SET={mujer:[],hombre:[]};
// La foto propia de cada pieza: solo cuando el set son prendas DISTINTAS (otra
// estampa), no el mismo modelo en varios colores.
let SET_FOTOS={};
const COLOR_PH=["blanco","negro","nude","gris","azul","bordo"];
function setN(){const e=document.getElementById("set-n");const n=e?parseInt(e.value||"3"):3;
  return Math.max(2,Math.min(6,isNaN(n)?3:n));}
// El género del set. Manda el selector de siempre (#g-genero) —que es lo que viaja en
// el pedido— pero AHORA se puede tocar desde el panel del set: antes vivía adentro de
// "Apariencia de la modelo IA", que solo aparece con "Sin avatar" tildado, así que
// haciendo un set no había forma de pedir hombres y siempre salían mujeres.
// Ojo: no se usa currentGender() acá, porque ése devuelve el género del avatar GLOBAL
// y en el set el avatar global no pinta nada (cada ficha elige el suyo).
function setGenero(){const g=document.getElementById("g-genero");return (g&&g.value)||"mujer";}
function setGeneroPoner(v){
  const g=document.getElementById("g-genero");if(g)g.value=v;
  const s2=document.getElementById("set-genero");if(s2)s2.value=v;
  if(typeof applyGenderUI==="function")applyGenderUI(); else renderTrioCards();
}
function renderKidsCards(){
  const cards=document.getElementById("trio-cards");if(!cards)return;
  const N=setN();
  const keep=id=>{const e=document.getElementById(id);return e?e.value:"";};
  const guard=[];
  for(let i=0;i<6;i++)guard.push({col:keep("g-tcol"+i),ind:keep("g-tind"+i)});
  const conModelo=($("#kids-pack")&&$("#kids-pack").value==="modelo");
  const kguard=[];
  for(let i=0;i<6;i++)kguard.push({q:keep("g-tkq"+i),t:keep("g-tkt"+i),et:keep("g-tet"+i),pe:keep("g-tpe"+i),oj:keep("g-tojo"+i),al:keep("g-tkal"+i),x:keep("g-tkx"+i)});
  const opts=(arr,sel)=>arr.map(o=>'<option value="'+o[0]+'"'+(o[0]===sel?' selected':'')+'>'+o[1]+'</option>').join("");
  const ETNIA_K=[["","(como arriba)"],["latina","Latino/a"],["caucasica","Caucásico/a"],["morocha_tez_oscura","Trigueño/a"],["afro","Afro"],["asiatica","Asiático/a"],["mediterranea","Mediterráneo/a"],["mestiza","Mestizo/a"]];
  const OJOS_K=[["","(como arriba)"],["marrones","Marrones"],["negros","Negros"],["claros","Claros"],["verdes","Verdes"],["celestes","Celestes"]];
  const TALLE_K=[["","(como arriba)"],["0-1","Bebé: 6 meses a 1 año"],["1-2","1 a 2 años"],["2-4","2 a 4 años"],["4-6","4 a 6 años"],["6-8","6 a 8 años"],["8-10","8 a 10 años"],["10-12","10 a 12 años"],["12-14","12 a 14 años"],["14-16","14 a 16 años"],["16-18","16 a 18 años"]];
  const ALT_K=[["","(como arriba)"],["bajo","Bajito/a"],["medio","Normal"],["alto","Alto/a"]];
  const PELO_K=[["","(como arriba)"]].concat((typeof PELO_KIDS!=="undefined"?PELO_KIDS:[]).filter(o=>o[0]));
  cards.innerHTML="";
  for(let i=0;i<N;i++){
    const c=document.createElement("div");c.className="tcard";c.setAttribute("data-i",i);
    const foto=SET_FOTOS[i]||"";
    const g=kguard[i];
    const ficha=conModelo?(
      '<div style="border:1px dashed var(--line);border-radius:9px;padding:8px;margin:6px 0">'+
        '<p class="hint" style="margin:0 0 6px"><b>El nene/nena de esta prenda.</b> Lo que dejes en "(como arriba)" toma lo elegido en "Cómo es el nene/nena".</p>'+
        '<div class="row"><div><label>¿Nena o nene?</label><select id="g-tkq'+i+'">'+opts([["","(como arriba)"],["nena","Nena"],["nene","Nene"]],g.q)+'</select></div>'+
        '<div><label>Edad / talle</label><select id="g-tkt'+i+'">'+opts(TALLE_K,g.t)+'</select></div></div>'+
        '<div class="row"><div><label>Etnia / tez</label><select id="g-tet'+i+'">'+opts(ETNIA_K,g.et)+'</select></div>'+
        '<div><label>Pelo</label><select id="g-tpe'+i+'">'+opts(PELO_K,g.pe)+'</select></div></div>'+
        '<div class="row"><div><label>Ojos</label><select id="g-tojo'+i+'">'+opts(OJOS_K,g.oj)+'</select></div>'+
        '<div><label>Altura para su edad</label><select id="g-tkal'+i+'">'+opts(ALT_K,g.al)+'</select></div></div>'+
        '<div><label>Detalle extra (texto libre)</label><input id="g-tkx'+i+'" placeholder="ej: pecas, rulos, sonrisa con dientes de leche" value="'+String(g.x||"").replace(/"/g,"&quot;")+'"></div>'+
      '</div>'):"";
    c.innerHTML=
      '<div style="font-weight:600;margin:10px 0 4px;color:var(--rose-deep)">Prenda '+(i+1)+(conModelo?' y su nene/nena':'')+'</div>'+
      '<div><label>Color</label><input id="g-tcol'+i+'" placeholder="'+(COLOR_PH[i]||"color")+'"></div>'+
      ficha+
      '<div style="border:1px dashed var(--rose-deep);border-radius:9px;padding:8px;margin:6px 0">'+
        '<label style="margin:0">Foto de ESTA prenda (opcional)</label>'+
        '<p class="hint" style="margin:3px 0 6px">Si las piezas son estampas distintas, subí acá la foto de ésta. Si no, se usa la de arriba y solo cambia el color.</p>'+
        (foto?'<div style="display:flex;gap:8px;align-items:center"><img src="'+foto+'" style="width:64px;height:64px;object-fit:cover;border-radius:8px">'+
             '<button class="ghost tfotox" data-i="'+i+'">Quitar esta foto</button></div>'
            :'<input type="file" accept="image/*" class="tfoto" data-i="'+i+'">')+
      '</div>'+
      '<div><label>Indicaciones para esta toma (opcional)</label>'+
      '<input id="g-tind'+i+'" placeholder="ej: sobre la arena, con una caracola al lado"></div>';
    cards.appendChild(c);
    const ic=document.getElementById("g-tcol"+i);if(ic)ic.value=guard[i].col||"";
    const ii=document.getElementById("g-tind"+i);if(ii)ii.value=guard[i].ind||"";
    const fi=c.querySelector(".tfoto");
    if(fi)fi.onchange=async e=>{
      const f=e.target.files[0];if(!f)return;
      try{ SET_FOTOS[i]=await downscaleImage(f,2000); renderTrioCards();
           toast("Foto de la prenda "+(i+1)+" cargada ✓"); }
      catch(err){ toast("No pude leer la foto: "+(err.message||err),true); }
    };
    const fx=c.querySelector(".tfotox");
    if(fx)fx.onclick=()=>{delete SET_FOTOS[i];renderTrioCards();};
  }
  const km=($("#kids-pack")&&$("#kids-pack").value==="modelo");
  const lg=document.getElementById("lbl-inc-grupal");
  if(lg)lg.textContent=km?" Foto grupal (los "+N+" chicos juntos)":" Foto de pack (las "+N+" juntas)";
  const li=document.getElementById("lbl-inc-ind");
  if(li)li.textContent=km?" Una foto por prenda, cada una con su chico":" Una foto por prenda";
}

function renderTrioCards(){
  const cards=document.getElementById("trio-cards");if(!cards)return;
  // KIDS: la ficha es otra cosa. No hay modelo que definir —la toma es de la prenda
  // sola—, así que de cada pieza solo hace falta su color y, si es otra estampa, su
  // foto. Poner acá busto, cola o etnia no tendría ningún sentido.
  if(typeof esKids==="function"&&esKids())return renderKidsCards();
  const h=setGenero()==="hombre";
  const sg=document.getElementById("set-genero");if(sg)sg.value=setGenero();
  // Los avatares que se ofrecen son los del género elegido arriba: con la lista de
  // mujeres fija, un set de bóxers no tenía de dónde sacar un modelo varón.
  const avs=((h?AVATARES_SET.hombre:AVATARES_SET.mujer)||[]).filter(Boolean);
  const avOpts='<option value="">(modelo IA)</option>'+
    avs.map(av=>'<option value="'+av.id+'">'+av.name+'</option>').join("");
  const cont=h
    ?'<option value="">(cuerpo)</option><option value="delgado">Delgado</option><option value="atletico">Atlético</option><option value="musculoso">Musculoso</option><option value="robusto">Robusto</option><option value="talle_grande">Talle grande</option><option value="talle_extra_grande">Talle XXL</option>'
    :'<option value="">(cuerpo)</option><option value="delgada">Delgada</option><option value="atletica">Atlética</option><option value="curvy">Curvy</option><option value="talle_grande">Talle grande</option><option value="talle_extra_grande">Talle XXL</option>';
  const busto='<option value="">(busto)</option><option value="chico">Chico</option><option value="mediano">Mediano</option><option value="grande">Grande</option><option value="extra_grande">XXL</option>';
  const cola='<option value="">(cola)</option><option value="chica">Chica</option><option value="mediana">Mediana</option><option value="grande">Grande</option><option value="extra_grande">XXL</option>';
  const abd=h
    ?'<option value="">(abdomen)</option><option value="fit">Marcado</option><option value="plano">Plano</option><option value="natural">Natural</option><option value="con_panza">Con panza natural</option>'
    :'<option value="">(abdomen)</option><option value="fit">Fit</option><option value="plano">Plano</option><option value="natural">Natural</option><option value="con_pancita">Con pancita</option>';
  const edad='<option value="">(edad)</option><option value="20">~20</option><option value="30">~30</option><option value="40">~40</option><option value="50">~50</option>';
  const altura='<option value="">(altura)</option><option value="baja">Baja ~1,55</option><option value="media">Media ~1,65</option><option value="alta">Alta ~1,75</option><option value="muy_alta">Muy alta 1,80+</option>';
  const posesel='<option value="frente">De frente</option><option value="perfil">De perfil</option><option value="espalda">De espalda</option><option value="sentada">Sentada</option><option value="caminando">Caminando</option>';
  const etnia='<option value="">(etnia IA)</option><option value="latina">Latin'+(h?'o':'a')+'</option><option value="caucasica">Caucásic'+(h?'o':'a')+'</option><option value="morocha_tez_oscura">Trigueñ'+(h?'o':'a')+'</option><option value="afro">Afro</option><option value="asiatica">Asiátic'+(h?'o':'a')+'</option><option value="mediterranea">Mediterráne'+(h?'o':'a')+'</option><option value="mestiza">Mestiz'+(h?'o':'a')+'</option>';
  const pelo=h
    ?'<option value="">(pelo IA)</option><option value="corto">Corto</option><option value="negro_lacio">Negro lacio</option><option value="castaño_ondulado">Castaño ondulado</option><option value="rubia">Rubio</option>'
    :'<option value="">(pelo IA)</option><option value="rubia">Rubia</option><option value="castaño_largo">Castaño largo</option><option value="morocha_largo_ondulado">Morocha ondulado</option><option value="negro_lacio">Negro lacio</option><option value="pelirroja">Pelirroja</option><option value="corto">Corto</option>';
  const POSE_PH=["frente","perfil","espalda","frente","perfil","espalda"];
  const N=setN();
  // Las fichas se arman acá (antes eran tres divs fijos en el HTML): la cantidad la
  // elige ella antes de generar.
  const keep=id=>{const e=document.getElementById(id);return e?e.value:"";};
  const guardados=[];
  for(let i=0;i<6;i++)guardados.push({av:keep("g-tav"+i),col:keep("g-tcol"+i),cue:keep("g-tcue"+i),
    ed:keep("g-ted"+i),et:keep("g-tet"+i),pe:keep("g-tpe"+i),bu:keep("g-tbu"+i),co:keep("g-tco"+i),
    ab:keep("g-tab"+i),al:keep("g-tal"+i),po:keep("g-tpo"+i),ind:keep("g-tind"+i),
    pn:keep("g-tpn"+i),pnl:keep("g-tpnl"+i),pg:keep("g-tpg"+i),ou:keep("g-tout"+i)});
  cards.innerHTML="";
  for(let i=0;i<N;i++){
    const c=document.createElement("div");c.className="tcard";c.setAttribute("data-i",i);
    cards.appendChild(c);
  }
  for(let i=0;i<N;i++){
    const c=cards.querySelector('.tcard[data-i="'+i+'"]');if(!c)continue;
    const g=guardados[i];
    const v={av:g.av,col:g.col||COLOR_PH[i],cue:g.cue,ed:g.ed,et:g.et,pe:g.pe,
      bu:g.bu,co:g.co,ab:g.ab,al:g.al,po:g.po||POSE_PH[i],ind:g.ind};
    const foto=SET_FOTOS[i]||"";
    c.innerHTML=
      '<div style="font-weight:600;margin:10px 0 4px;color:var(--rose-deep)">'+(h?"Modelo":"Modelo")+' '+(i+1)+'</div>'+
      '<div class="row"><div><label>Avatar</label><select id="g-tav'+i+'">'+avOpts+'</select></div>'+
      '<div><label>Color</label><input id="g-tcol'+i+'" placeholder="'+COLOR_PH[i]+'"></div></div>'+
      '<div><label>Lleva además (outfit) <span class="q" title="Lo que tiene puesto además de la prenda del producto: buzo, top, zapatillas. Va en su toma y en la foto grupal; si queda vacío, sale con lo de Piezas.">?</span></label>'+
      '<input id="g-tout'+i+'" placeholder="ej: buzo crop gris melange y zapatillas blancas"></div>'+
      '<div style="border:1px dashed var(--rose-deep);border-radius:9px;padding:8px;margin:6px 0">'+
        '<label style="margin:0">Foto de ESTA pieza (opcional) <span class="q" title="Solo si esta pieza es OTRA prenda: otra estampa, otro diseño. Si la subís, esa foto es la única verdad de esta toma y no se usan las fotos de arriba.">?</span></label>'+
        '<p class="hint" style="margin:3px 0 6px">Si el set son estampas distintas (ej. un pack de bóxers), subí acá la foto de esta pieza.</p>'+
        (foto?'<div style="display:flex;gap:8px;align-items:center"><img src="'+foto+'" style="width:64px;height:64px;object-fit:cover;border-radius:8px">'+
             '<button class="ghost tfotox" data-i="'+i+'">Quitar esta foto</button></div>'
            :'<input type="file" accept="image/*" class="tfoto" data-i="'+i+'">')+
      '</div>'+
      '<div class="row"><div><label>Pose de su foto</label><select id="g-tpo'+i+'">'+posesel+'</select></div>'+
      '<div><label>Cuerpo</label><select id="g-tcue'+i+'">'+cont+'</select></div></div>'+
      (h?'<div class="row"><div><label>Abdomen</label><select id="g-tab'+i+'">'+abd+'</select></div>'+
         '<div><label>Altura</label><select id="g-tal'+i+'">'+altura+'</select></div></div>'+
         '<select id="g-tbu'+i+'" style="display:none">'+busto+'</select>'+
         '<select id="g-tco'+i+'" style="display:none">'+cola+'</select>'
        :'<div class="row3"><div><label>Busto</label><select id="g-tbu'+i+'">'+busto+'</select></div>'+
         '<div><label>Cola</label><select id="g-tco'+i+'">'+cola+'</select></div>'+
         '<div><label>Abdomen</label><select id="g-tab'+i+'">'+abd+'</select></div></div>')+
      '<div class="row3"><div><label>Edad</label><select id="g-ted'+i+'">'+edad+'</select></div>'+
      (h?'':'<div><label>Altura</label><select id="g-tal'+i+'">'+altura+'</select></div>')+
      '<div><label>Etnia (IA)</label><select id="g-tet'+i+'">'+etnia+'</select></div></div>'+
      '<label style="margin-top:6px">📷 Pose de mis fotos (opcional: copia esa toma)</label>'+
      '<input type="hidden" id="g-tpg'+i+'">'+
      '<div data-selpose="g-tpg'+i+'" data-grupal="0"></div>'+
      '<div class="row"><div><label>Peinado de esta toma</label><select id="g-tpn'+i+'">'+
        (h?PEINADOS_M:PEINADOS_F).map((o,j)=>'<option value="'+o[0]+'">'+(j===0?"(como arriba)":o[1])+'</option>').join("")+'</select></div>'+
      '<div><label>o escribilo</label><input id="g-tpnl'+i+'" placeholder="ej: cola baja con raya al costado"></div></div>'+
      '<div class="row"><div><label>Pelo (IA)</label><select id="g-tpe'+i+'">'+pelo+'</select></div>'+
      '<div style="display:flex;align-items:flex-end"><button class="ghost tsave" data-i="'+i+'" style="width:100%">💾 Guardar tarjeta de esta modelo</button></div></div>'+
      '<div><label>Indicaciones para su foto (opcional, texto libre)</label>'+
      '<input id="g-tind'+i+'" placeholder="ej: brazos cruzados, media risa, mirando por la ventana"></div>'+
      '<button class="go tgen" data-i="'+i+'" style="margin-top:8px;width:100%">▶ Generar SOLO esta toma</button>'+
      '<label class="pk" style="margin-top:6px"><input type="checkbox" class="tmin" data-i="'+i+'"> ✅ Prompt simple (el que te funciona en el Playground)</label>'+
      '<label class="pk" style="margin-top:4px"><input type="checkbox" class="tedit" data-i="'+i+'"> ✏️ Ver y editar el prompt antes de generar</label>'+
      '<p class="hint" style="margin:4px 0 0">Manda un pedido corto tipo Playground: quién es '+(h?"el modelo":"la modelo")+', cuerpo entero, escenario y prenda. Sin medidas de cuerpo ni encuadres cerrados.</p>';
    ["g-tav","g-tcol","g-tcue","g-ted","g-tet","g-tpe","g-tbu","g-tco","g-tab","g-tal","g-tpo","g-tind"].forEach((p,j)=>{
      const vals=[v.av,v.col,v.cue,v.ed,v.et,v.pe,v.bu,v.co,v.ab,v.al,v.po,v.ind];
      const el=document.getElementById(p+i);if(el)el.value=vals[j];
    });
    [["g-tpn",g.pn],["g-tpnl",g.pnl],["g-tpg",g.pg],["g-tout",g.ou]].forEach(([p,val])=>{
      const el=document.getElementById(p+i);if(el&&val)el.value=val;
    });
    const avSel=document.getElementById("g-tav"+i);
    if(avSel)avSel.addEventListener("change",()=>fichaLoad(i));
    const sv=c.querySelector(".tsave");
    if(sv)sv.onclick=()=>fichaSave(i);
    const gn=c.querySelector(".tgen");
    if(gn)gn.onclick=()=>genUnaToma(i);
    const fi=c.querySelector(".tfoto");
    if(fi)fi.onchange=async e=>{
      const f=e.target.files[0];if(!f)return;
      try{ SET_FOTOS[i]=await downscaleImage(f,2000); renderTrioCards();
           toast("Foto de la pieza "+(i+1)+" cargada ✓ — esa es su prenda"); }
      catch(err){ toast("No pude leer la foto: "+(err.message||err),true); }
    };
    const fx=c.querySelector(".tfotox");
    if(fx)fx.onclick=()=>{delete SET_FOTOS[i];renderTrioCards();};
  }
  const lg=document.getElementById("lbl-inc-grupal");
  if(lg)lg.textContent=" Foto grupal ("+(h?"los ":"las ")+N+")";
  if(typeof refrescarSelectoresPose==="function")refrescarSelectoresPose();
}
if(document.getElementById("set-n"))
  document.getElementById("set-n").onchange=()=>{renderTrioCards();};
if(document.getElementById("set-genero"))
  document.getElementById("set-genero").onchange=e=>setGeneroPoner(e.target.value);
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
  if(!GEN_PRODUCTS.length&&!SET_FOTOS[i])
    return toast("Subí al menos una foto del producto (o la foto de esta pieza).",true);
  const col=(($("#g-tcol"+i)||{}).value||"").trim();
  if(!col)return toast("Poné el color de esta modelo.",true);
  const minChk=document.querySelector('.tmin[data-i="'+i+'"]');
  const minimo=!!(minChk&&minChk.checked);
  const all=gatherAsign();
  const it=all.find(x=>x.color===col)||{};
  const guardada=(it.pose_guardada||"").trim();
  const fp=guardada?null:(POSE_MAP[it.pose||"frente"]||0);   // la pose de mis fotos manda
  const isBack=(fp===3)||(guardada&&/de espaldas?|desde atr[aá]s|parte de atr[aá]s/i.test(guardada));
  let prods=(isBack&&GEN_PRODUCTS_BACK.length)?GEN_PRODUCTS_BACK:GEN_PRODUCTS;
  // Si esta pieza tiene SU foto (otra estampa), esa es la única verdad de su toma:
  // las fotos de arriba son de otra prenda y le cambiarían el diseño.
  const propia=SET_FOTOS[i]||"";
  const soloColor=!!propia&&GEN_PRODUCTS.length&&((($("#set-fotos-modo")||{}).value||"color")==="color");
  let tagsT=(isBack?[]:GEN_PRODUCT_TAGS);
  if(soloColor){ prods=prods.slice(0,5).concat([propia]);
    tagsT=prods.slice(0,-1).map((_,j)=>isBack?"espalda":(GEN_PRODUCT_TAGS[j]||"")).concat(["color"]); }
  else if(propia)prods=[propia];
  const gp=genParams();
  gp.genero=setGenero();
  const params=minimo?{color_set:col,modo_minimo:"si",genero:gp.genero,
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
    if(it.peinado)params.cuerpo_peinado=it.peinado;
    if(it.peinado_libre)params.peinado_libre=it.peinado_libre;
    if(!it.avatar_id){if(it.etnia)params.ap_etnia=it.etnia;if(it.pelo)params.ap_pelo=it.pelo;}
    if(it.outfit)params.piezas="usa la prenda del producto con "+it.outfit+" (eso NO es parte del producto)";
    if(guardada){params.pose=guardada;params.encuadre="";params.encuadre_zona="";}
    if(it.indicacion){const prev=(params.aclaraciones||"").trim();
      params.aclaraciones=(prev?prev+" ":"")+"DIRECCIÓN DE ESTA TOMA (seguila): "+it.indicacion;}
  }
  if(guardada)params.pose=guardada;      // también en el prompt simple
  const btn=document.querySelector('.tgen[data-i="'+i+'"]');
  const editChk=document.querySelector('.tedit[data-i="'+i+'"]');
  const payload={mode:"on_model",avatar_id:it.avatar_id||null,product_images:prods,
    product_tags:(soloColor?tagsT:(propia?["frente"]:(isBack?[]:GEN_PRODUCT_TAGS))),
    aspect:"4:5",paneles:1,image_size:GEN_SIZE,reframe:null,style:$("#g-style").value,
    ...(fp===null?{}:{force_pose:fp}),no_face_recreate:!!it.avatar_id,params:params};
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
    const map={contextura:"g-tcue",busto:"g-tbu",cola:"g-tco",abdomen:"g-tab",edad:"g-ted",altura:"g-tal",
      pose:"g-tpo",peinado:"g-tpn",peinado_libre:"g-tpnl",pose_guardada:"g-tpg",indicacion:"g-tind"};
    let any=false;
    Object.keys(map).forEach(k=>{if(f[k]){const el=document.getElementById(map[k]+i);if(el){el.value=f[k];any=true;}}});
    if(f.pose_guardada&&typeof refrescarSelectoresPose==="function")refrescarSelectoresPose();
    if(any)toast("Tarjeta guardada de esta modelo cargada ✓");
  }catch(e){}
}
async function fichaSave(i){
  const avId=(document.getElementById("g-tav"+i)||{}).value||"";
  if(!avId)return toast("Elegí un avatar primero: la ficha se guarda por avatar.",true);
  const v=id=>(document.getElementById(id+i)||{}).value||"";
  const body={contextura:v("g-tcue"),busto:v("g-tbu"),cola:v("g-tco"),abdomen:v("g-tab"),
    edad:v("g-ted"),altura:v("g-tal"),pose:v("g-tpo"),peinado:v("g-tpn"),peinado_libre:v("g-tpnl"),
    pose_guardada:v("g-tpg"),indicacion:v("g-tind")};
  try{await jpost("/api/avatars/"+avId+"/ficha",body);toast("Tarjeta guardada ✓ — cuerpo, peinado, pose e indicaciones se cargan solos la próxima vez que elijas esta modelo");}
  catch(e){toast("No pude guardar la ficha.",true);}
}

// ---- Generar on_model ----
function noAvatar(){return (typeof esKids==="function"&&esKids())||($("#g-no-avatar")&&$("#g-no-avatar").checked);}
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
const PEINADOS_F=[["","(según avatar)"],["largo_suelto","Largo suelto"],["largo_ondulado","Largo ondulado"],["lacio","Lacio prolijo"],["ondas_playa","Ondas playeras"],["brushing","Brushing con volumen"],["flequillo","Con flequillo cortina"],["media_melena","Media melena"],["corto","Corto"],["semirecogido","Semirecogido"],["recogido_despeinado","Recogido despeinado"],["atado","Atado (cola)"],["cola_alta","Cola alta"],["cola_baja","Cola baja"],["rodete","Rodete/moño"],["rodete_bajo","Rodete bajo"],["rodete_alto","Rodete alto despeinado"],["trenza","Trenza"],["trenzas","Dos trenzas"],["mojado","Mojado hacia atrás"],["panuelo","Con pañuelo"]];
const PEINADOS_M=[["","(según avatar)"],["corto","Corto"],["rapado","Rapado"],["media_melena","Media melena"],["atado","Atado (colita)"],["mojado","Mojado hacia atrás"],["rodete_alto","Rodete alto"]];
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
  setSelOpts("#g-peinado",h?PEINADOS_M:PEINADOS_F);
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
  // Las fichas del set siguen al género: sin esto, un set de bóxers seguía ofreciendo
  // avatares mujer y campos de busto/cola, y salía con modelos mujeres.
  if(typeof renderTrioCards==="function")renderTrioCards();
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
    // Kids: siempre UNA toma 4:5 (el prompt de chicos no arma paneles) con una pose de chico al azar.
    const kidsG=esKids();
    const jid=await startJob("/api/generate",{mode:"on_model",avatar_id:avatarToSend(),product_images:GEN_PRODUCTS,product_tags:GEN_PRODUCT_TAGS,
      aspect:kidsG?"4:5":$("#g-aspect").value,paneles:kidsG?1:parseInt($("#g-paneles").value),image_size:GEN_SIZE,
      reframe:kidsG?null:($("#g-reframe").value||null),style:$("#g-style").value,pose_offset:Math.floor(Math.random()*(kidsG?10:8)),
      camara:($("#g-camara")?$("#g-camara").value:""),
      params:genParams()});
    await pollJob(jid,prog);
  }catch(e){prog.fail(errMsg(e));toast(errMsg(e),true);}
  b.disabled=false;b.textContent="Generar imágenes";
};

// ---- Set completo: 4 poses de modelo + 1 prenda colgada ----

const EST_AV_AYUDA = %%ESTAVAYUDA%%;
if($("#g-estilo-avatar")){
  const pintarEstAv=()=>{ $("#estav-ayuda").textContent = EST_AV_AYUDA[$("#g-estilo-avatar").value] || ""; };
  $("#g-estilo-avatar").onchange = pintarEstAv; pintarEstAv();
}
function genParams(){return {tela:$("#g-tela").value, estilo_avatar:$("#g-estilo-avatar")?$("#g-estilo-avatar").value:"",color:$("#g-color").value,punos:$("#g-punos").value,
  costuras:$("#g-costuras").value,cuello:$("#g-cuello").value,pose:$("#g-pose").value,
  no_tiene:($("#g-notiene")?$("#g-notiene").value:""),
  fondo:$("#g-fondo").value,luz:$("#g-luz").value,encuadre:$("#g-encuadre").value,
  encuadre_zona:($("#g-encuadre-zona")?$("#g-encuadre-zona").value:""),
  temporada:($("#g-temporada")?$("#g-temporada").value:"invierno"),
  cuerpo_busto:($("#g-busto")?$("#g-busto").value:""),cuerpo_cola:($("#g-cola")?$("#g-cola").value:""),
  cuerpo_abdomen:($("#g-abdomen")?$("#g-abdomen").value:""),cuerpo_contextura:($("#g-contextura")?$("#g-contextura").value:""),
  cuerpo_edad:($("#g-edadcorp")?$("#g-edadcorp").value:""),cuerpo_peinado:($("#g-peinado")?$("#g-peinado").value:""),peinado_libre:($("#g-peinado-libre")?$("#g-peinado-libre").value:""),
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
  kids_talle:($("#kids-talle")?$("#kids-talle").value:""),
  kids_quien:($("#kids-quien")?$("#kids-quien").value:""),
  kids_modo:(esKids()?"modelo":"sola"),
  kids_altura:($("#ap-altura-kids")?$("#ap-altura-kids").value:""),
  producto_manual:($("#g-producto-manual")?$("#g-producto-manual").value:""),
  piezas:($("#g-piezas")?$("#g-piezas").value:""),
  prompt_json:($("#g-prompt-json")&&$("#g-prompt-json").checked?"si":""),
  pareo:($("#g-pareo")?$("#g-pareo").value:""),
  ajustes:($("#g-ajustes")?$("#g-ajustes").value:""),
  lente:($("#g-lente")?$("#g-lente").value:"auto"),camara_foto:($("#g-camara-foto")?$("#g-camara-foto").value:"auto"),
  movimiento:($("#g-movimiento")?$("#g-movimiento").value:"auto"),
  no_lenceria:($("#g-no-lenceria")?$("#g-no-lenceria").value:""),
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
  // Una sola lista ordenada de tomas: las tildadas del listado y las tuyas, cada una con
  // su ángulo. Antes eran dos caminos y las escritas pisaban a las tildadas.
  let items=[]; let incProd=true;
  document.querySelectorAll((esKids()?'#pose-pick-kids':'#pose-pick')+' input[type=checkbox]').forEach(cb=>{
    if(cb.id==="pk-prod"||cb.id==="pk-prod-kids"){incProd=cb.checked;return;}
    if(!cb.checked)return;
    const fila=cb.closest(".pkrow"), sel=fila?fila.querySelector(".camsel"):null;
    items.push({pose:parseInt(cb.value),camara:sel?sel.value:""});
  });
  document.querySelectorAll("#mis-poses .pkrow, #escena-poses .pkrow").forEach(fila=>{
    const cb=fila.querySelector("input[type=checkbox]"), sel=fila.querySelector(".camsel");
    if(cb&&cb.checked)items.push({texto:cb.dataset.txt,camara:sel?sel.value:""});
  });
  const usaCustom = items.length>0;
  const totalImgs = usaCustom ? (items.length + (incProd?1:0)) : (esKids()?5:(HQ?5:3));
  if(totalImgs===0)return toast("Elegí al menos una pose o el producto.",true);
  if(!confirm("Genera "+totalImgs+" imágenes. Corre en el server: si se corta la app o refrescás, sigue solo y lo recuperás al volver. ¿Seguimos?"))return;
  const b=$("#btn-set");b.disabled=true;$("#btn-gen").disabled=true;
  SET_RESULTS=[];
  const prog=makeProgress("#gen-out");
  try{
    const jid=await startJob("/api/set",{hq:HQ,avatar_id:avatarToSend(),product_images:GEN_PRODUCTS,product_tags:GEN_PRODUCT_TAGS,
      product_images_back:GEN_PRODUCTS_BACK,
      items:(usaCustom?items:undefined),include_product:incProd,modo_producto:"suspendida",
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
      payload={mode:"product_only",modo_producto:"suspendida",product_images:GEN_PRODUCTS,product_tags:GEN_PRODUCT_TAGS,
        aspect:"4:5",paneles:1,image_size:GEN_SIZE,reframe:null,params:genParams()};
    }else{
      const fp=parseInt(sel), isBack=(fp===3||fp===10);
      const prods=(isBack && GEN_PRODUCTS_BACK.length)?GEN_PRODUCTS_BACK:GEN_PRODUCTS;
      payload={mode:"on_model",avatar_id:avatarToSend(),product_images:prods,product_tags:(isBack?[]:GEN_PRODUCT_TAGS),
        aspect:"4:5",paneles:1,image_size:GEN_SIZE,reframe:null,style:$("#g-style").value,
        force_pose:fp,consistency_refs:anchors,params:genParams(),
        camara:($("#one-camara")?$("#one-camara").value:"")};
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
        luz:$("#pr-luz").value,aclaraciones:$("#pr-aclaraciones").value,
        no_tiene:($("#pr-notiene")?$("#pr-notiene").value:"")}});
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
  if(r.revision_claude){
    const rc=r.revision_claude, okc=rc.puntaje>=8, colc=okc?"var(--ok)":"var(--bad)";
    html+='<div style="font-size:12px;color:'+colc+';font-weight:600;margin:4px 0">'+(okc?"✅":"⚠")+" Claude revisó tu pedido: "+rc.puntaje+"/10"+(rc.reintento?" (la pidió de nuevo)":"")+'</div>';
    if((rc.fallas||[]).length)html+='<div class="hint" style="margin:2px 0 6px">'+rc.fallas.map(d=>"• "+esc(d)).join("<br>")+'</div>';
  }
  if(r.drive_pending){html+='<div style="font-size:12px;color:var(--ok);font-weight:600;margin:4px 0">⬆️ Subiéndose a tu Google Drive… (confirmá en Ajustes → Google Drive)</div><div class="results">';}
  else if(r.drive_conectado===false){html+='<div style="font-size:12px;color:var(--bad);font-weight:600;margin:4px 0">⚠️ Google Drive NO está conectado: esta imagen no se guarda en ningún lado. Bajala ahora o conectá Drive en Ajustes.</div><div class="results">';}
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
  if($("#s-seedream4k"))$("#s-seedream4k").value=(String(SETTINGS.seedream_final_4k||"si").toLowerCase()==="no")?"no":"si";
  if($("#s-seedreamcara"))$("#s-seedreamcara").value=(String(SETTINGS.seedream_cara_recortada||"si").toLowerCase()==="no")?"no":"si";
  if($("#s-claudeseedream"))$("#s-claudeseedream").value=(String(SETTINGS.claude_seedream||"si").toLowerCase()==="no")?"no":"si";
  if($("#s-seedreamposes"))$("#s-seedreamposes").value=(String(SETTINGS.seedream_poses_seguras||"si").toLowerCase()==="no")?"no":"si";
  if($("#s-variacion"))$("#s-variacion").value=(String(SETTINGS.variacion_auto||"si").toLowerCase()==="no")?"no":"si";
  if($("#s-qc"))$("#s-qc").value=SETTINGS.qc_prenda||"si";
  if($("#s-qcumbral"))$("#s-qcumbral").value=SETTINGS.qc_umbral||7;
  if($("#s-qcretry"))$("#s-qcretry").value=SETTINGS.qc_reintento||"si";
  if($("#s-qcanat"))$("#s-qcanat").value=(String(SETTINGS.qc_anatomia||"si").toLowerCase()==="no")?"no":"si";
  if($("#s-qcmodel")){const mv=SETTINGS.qc_model||"gemini-3.1-flash";
    if([...$("#s-qcmodel").options].some(o=>o.value===mv))$("#s-qcmodel").value=mv;}
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
      seedream_final_4k:($("#s-seedream4k")?$("#s-seedream4k").value:undefined),
      seedream_cara_recortada:($("#s-seedreamcara")?$("#s-seedreamcara").value:undefined),
      claude_seedream:($("#s-claudeseedream")?$("#s-claudeseedream").value:undefined),
      seedream_poses_seguras:($("#s-seedreamposes")?$("#s-seedreamposes").value:undefined),
      variacion_auto:($("#s-variacion")?$("#s-variacion").value:undefined),
      qc_prenda:($("#s-qc")?$("#s-qc").value:undefined),
      qc_umbral:($("#s-qcumbral")?parseInt($("#s-qcumbral").value):undefined),
      qc_reintento:($("#s-qcretry")?$("#s-qcretry").value:undefined),
      qc_anatomia:($("#s-qcanat")?$("#s-qcanat").value:undefined),
      qc_model:($("#s-qcmodel")?$("#s-qcmodel").value:undefined),
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

// ---- Al abrir un panel grande, llevarlo arriba ----
// En el celular, tocar "Opciones avanzadas" abría el panel POR DEBAJO de lo que se ve:
// la pantalla no se movía y parecía que no había pasado nada.
document.querySelectorAll("details.adv, #wrap-poses, #wrap-colores").forEach(d=>{
  d.addEventListener("toggle",()=>{
    if(!d.open || innerWidth > 560)return;
    const h=document.querySelector("header");
    const alto=h?h.getBoundingClientRect().height:0;
    const y=d.getBoundingClientRect().top + scrollY - alto - 6;
    scrollTo({top:Math.max(0,y),behavior:"smooth"});
  });
});

// ---- Mis poses: las que escribe la usuaria, guardadas en su cuenta ----
// Lo que ella escribe va a parar al HTML: se escapa siempre (comillas incluidas, que
// viajan en un atributo data-).
function esc(t){return String(t==null?"":t).replace(/[&<>"']/g,c=>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
MIS_POSES=MIS_POSES||[];
// MODO MANUAL por pose: hacia dónde mira el cuerpo, ángulo y encuadre fijados por ella (se guardan en
// la pose). Con cualquiera de los tres puesto, de la foto se copia sólo la pose.
var POSE_MANUAL_OPTS=%%MANUALJS%%;
function manualPoseHTML(p){
  const m=p.manual||{},sel=(k,ops,v)=>'<select data-k="'+k+'" style="flex:1 1 22%;min-width:0;font-size:12px;padding:3px 4px;height:auto">'
    +'<option value="">'+({vista:"Cuerpo: como la foto",camara:"Ángulo: como la foto",plano:"Encuadre: como la foto",lente:"Lente: como la foto",guia:"Guía: con la imagen guía"})[k]+'</option>'
    +ops.map(o=>'<option value="'+o[0]+'"'+(String(v??"")===String(o[0])?" selected":"")+'>'+esc(o[1])+'</option>').join("")+'</select>';
  return '<div class="mispose-man" data-id="'+p.id+'" style="display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin:2px 0 10px 26px">'
    +sel("vista",POSE_MANUAL_OPTS.vistas,m.vista)+sel("camara",POSE_MANUAL_OPTS.camaras,m.camara)+sel("plano",POSE_MANUAL_OPTS.planos,m.plano)+sel("lente",POSE_MANUAL_OPTS.lentes,m.lente)+sel("guia",[["sin","Sin guía: sólo la ficha y la pose escrita"]],m.guia)
    +'<span class="hint mispose-man-est" style="flex:1 1 100%;margin:0">'+(p.manual?"✋ manual: de la foto copia sólo la pose":"automático: copia todo de la foto")+'</span></div>';
}
function pintarMisPoses(){
  const cont=$("#mis-poses"); if(!cont)return;
  if(!MIS_POSES.length){
    cont.innerHTML='<p class="hint" style="margin:0">Todavía no guardaste ninguna. Escribí una abajo y tocá ➕ Agregar.</p>';
    return;
  }
  // Misma fila que las poses del listado: tilde + ángulo, más el tachito para borrarla.
  cont.innerHTML=MIS_POSES.map(p=>
    '<div class="pkrow"><label class="pk"><input type="checkbox" data-id="'+p.id+'" data-txt="'+esc(p.texto)+'"> '+(p.ref?'<img src="'+p.ref+'" title="Foto de referencia de esta pose" style="height:54px;border-radius:6px;vertical-align:middle;margin-right:4px">':'')+(p.guia?'<span title="Tiene maniquí guía: copia la toma de la foto">🧍 </span>':'')+esc(p.texto)+'</label>'
    +'<select class="camsel">%%CAMOPTS%%</select>'
    +(p.perfil_hash?'<button class="ghost mispose-pf" data-h="'+p.perfil_hash+'" title="Ver y corregir el perfil fotográfico" style="padding:2px 8px;flex:0 0 auto">📐</button>':'')
    +'<button class="ghost mispose-del" data-id="'+p.id+'" title="Borrar esta pose" style="padding:2px 8px;flex:0 0 auto">🗑</button></div>'
    +manualPoseHTML(p)).join("")
    +'<div id="mispose-perfil"></div>';
  if(typeof refrescarSelectoresPose==="function")refrescarSelectoresPose();
  cont.querySelectorAll(".mispose-man select").forEach(sel=>sel.onchange=async()=>{
    const w=sel.closest(".mispose-man"),q=k=>w.querySelector('[data-k="'+k+'"]').value;
    try{const r=await jpost("/api/poses_propias/"+encodeURIComponent(w.dataset.id)+"/manual",
          {vista:q("vista"),camara:q("camara"),plano:q("plano"),lente:q("lente"),guia:q("guia")});
        MIS_POSES=r.poses||[];
        const man=(MIS_POSES.find(x=>x.id===w.dataset.id)||{}).manual;
        w.querySelector(".mispose-man-est").textContent=man?"✋ manual: de la foto copia sólo la pose":"automático: copia todo de la foto";
        toast("Guardado en esta pose ✓");}
    catch(e){toast(e.message,true);}
  });
  cont.querySelectorAll(".mispose-pf").forEach(b=>b.onclick=()=>abrirPerfilFoto(b.dataset.h,document.getElementById("mispose-perfil")));
  cont.querySelectorAll(".mispose-del").forEach(b=>b.onclick=async()=>{
    if(!confirm("¿Borrar esa pose de tu lista?"))return;
    try{const r=await jdel("/api/poses_propias/"+encodeURIComponent(b.dataset.id));
        MIS_POSES=r.poses||[];pintarMisPoses();}
    catch(e){toast(e.message,true);}
  });
}
async function cargarMisPoses(){
  try{const r=await jget("/api/poses_propias?t="+Date.now());MIS_POSES=r.poses||[];}
  catch(e){MIS_POSES=[];}
  pintarMisPoses();
}
if($("#btn-pose-add"))$("#btn-pose-add").onclick=async()=>{
  const ta=$("#g-pose-nueva"), txt=(ta.value||"").trim();
  if(!txt)return toast("Escribí la pose primero.",true);
  try{
    const r=await jpost("/api/poses_propias",{texto:txt,guia_tmp:POSE_GUIA_TMP||""});
    POSE_GUIA_TMP="";const gp=$("#g-pose-guia");if(gp)gp.innerHTML="";
    MIS_POSES=r.poses||[];ta.value="";pintarMisPoses();
    // recién agregadas: ya tildadas, que es lo que quiere quien acaba de escribirlas
    const nuevas=txt.split("\n").map(x=>x.trim()).filter(Boolean).map(x=>x.toLowerCase());
    document.querySelectorAll("#mis-poses input[type=checkbox]").forEach(cb=>{
      if(nuevas.includes((cb.dataset.txt||"").toLowerCase()))cb.checked=true;});
    toast("Guardada en tus poses ✓");
  }catch(e){toast(e.message,true);}
};


// ---- PERFIL FOTOGRÁFICO: revisar lo medido/estimado, apagar módulos y corregir a mano ----
const PF_LBL={framing:"📐 Encuadre",camera:"🎥 Cámara",pose:"🧍 Pose",focus:"🔍 Foco",lighting:"💡 Luz"};
const PF_TXT={"framing.shot_type":"Plano","framing.crop":"Corte","camera.height_relative":"Altura de cámara",
  "camera.vertical_angle":"Ángulo vertical","camera.horizontal_angle":"Ángulo horizontal","camera.distance_class":"Distancia",
  "camera.perspective_strength":"Perspectiva","pose.motion":"Movimiento","focus.depth_of_field":"Profundidad de campo",
  "focus.background_blur":"Desenfoque del fondo","lighting.direction":"Dirección de la luz","lighting.hardness":"Dureza",
  "lighting.contrast":"Contraste","lighting.color_temperature":"Temperatura de color"};
let PF_EDIT=null;
async function abrirPerfilFoto(hash,cont){
  cont=cont||$("#g-pose-perfil"); if(!cont||!hash)return;
  cont.innerHTML='<p class="hint">Cargando el perfil…</p>';
  try{
    if(!PF_EDIT)PF_EDIT=await jget("/api/foto_perfil_editables");
    const p=(await jget("/api/foto_perfil/"+hash)).perfil;
    const conf=p.confidence||{}, ov=p.user_overrides||{}, mods=p.modules||{};
    const pct=v=>v==null?"":(" · "+Math.round(v*100)+"%");
    const etiq=(grupo)=>{
      const med={framing:conf.framing_measured,focus:conf.focus_measured,lighting:conf.lighting_measured}[grupo];
      return (med!=null?'<span class="pfm">medido'+pct(med)+'</span> ':'')+(conf[grupo]!=null?'<span class="pfe">estimado'+pct(conf[grupo])+'</span>':'');};
    let h='<div style="border:1px solid var(--line);border-radius:10px;padding:8px;margin-top:6px">'
      +'<div style="font-weight:600;margin-bottom:4px">Perfil fotográfico de la referencia</div>'
      +'<p class="hint" style="margin:0 0 6px">Lo <b>medido</b> sale de la foto (esqueleto, nitidez, luz); lo <b>estimado</b> lo calcula el análisis visual. Corregí lo que no esté bien: tu corrección manda en todas las tomas con esta foto.</p>';
    for(const g of PF_EDIT.modulos){
      h+='<div style="margin:6px 0 2px"><label class="pk" style="display:inline-flex"><input type="checkbox" class="pf-mod" data-m="'+g+'"'+(mods[g]===false?"":" checked")+'> <b>'+PF_LBL[g]+'</b></label> '+etiq(g)+'</div><div class="row3">';
      for(const ruta of Object.keys(PF_EDIT.editables).filter(r=>r.startsWith(g+"."))){
        const campo=ruta.split(".")[1], val=(p[g]||{})[campo]||"unknown", ops=PF_EDIT.editables[ruta];
        const corr=ov[ruta]?' style="outline:2px solid var(--rose-deep)" title="Corregido a mano (antes: '+esc(String(ov[ruta].estimated_was))+')"':"";
        h+='<div><label style="font-size:12px">'+PF_TXT[ruta]+'</label>'+(ops
          ?'<select class="pf-ed" data-r="'+ruta+'"'+corr+'>'+['unknown'].concat(ops).map(o=>'<option'+(o===val?" selected":"")+'>'+o+'</option>').join("")+'</select>'
          :'<input class="pf-ed" data-r="'+ruta+'" value="'+esc(val)+'"'+corr+'>')+'</div>';
      }
      h+='</div>';
      if(g==="framing"&&p.framing&&p.framing.subject_height_ratio!=null)h+='<p class="hint" style="margin:2px 0">Medido: ocupa el '+Math.round(p.framing.subject_height_ratio*100)+'% del alto · '+(p.subjects||[]).length+' persona(s)</p>';
      if(g==="pose")h+='<p class="hint" style="margin:2px 0">'+(p.subjects||[]).map((s,i)=>String.fromCharCode(65+i)+': torso '+(((s.torso_orientation||{}).class)||"?")+', cabeza '+(((s.head_orientation||{}).class)||"?")).join(" · ")+'</p>';
      if(g==="camera"&&p.camera&&p.camera.skeleton_hint)h+='<p class="hint" style="margin:2px 0">Pista del esqueleto: ángulo '+p.camera.skeleton_hint.vertical_angle_hint+' (piernas/torso '+p.camera.skeleton_hint.leg_to_torso+')</p>';
    }
    if((p.warnings||[]).length)h+='<details style="margin-top:6px"><summary class="hint">⚠ '+p.warnings.length+' aviso(s)</summary><ul style="margin:4px 0 0;padding-left:18px">'+p.warnings.map(w=>'<li class="hint">'+esc(w)+'</li>').join("")+'</ul></details>';
    h+='<button class="ghost pf-save" style="margin-top:8px">💾 Guardar correcciones</button></div>';
    cont.innerHTML=h;
    cont.querySelector(".pf-save").onclick=async()=>{
      const cambios={}, modulos={};
      cont.querySelectorAll(".pf-ed").forEach(el=>{
        const ruta=el.dataset.r, orig=String(((p[ruta.split(".")[0]]||{})[ruta.split(".")[1]])||"unknown");
        if(el.value&&el.value!=="unknown"&&el.value!==orig)cambios[ruta]=el.value;});
      cont.querySelectorAll(".pf-mod").forEach(el=>{modulos[el.dataset.m]=el.checked;});
      try{await jpost("/api/foto_perfil/"+hash,{cambios,modulos});toast("Perfil corregido ✓");abrirPerfilFoto(hash,cont);}
      catch(e){toast(errMsg(e),true);}
    };
  }catch(e){cont.innerHTML='<p class="hint">No hay perfil para esta foto (copiala de nuevo).</p>';}
}
// ---- Elegir una pose copiada de foto TOCANDO SU IMAGEN (set de colores y grupal) ----
function selectorPoseHTML(hid,grupal){
  // Todas las poses copiadas de foto, primero las que van (grupales en la grupal, de una
  // persona en las individuales): la cuenta de personas puede fallar y no tiene que esconder ninguna.
  const lista=(MIS_POSES||[]).filter(p=>p.ref).slice().sort((a,b)=>{
    const ga=(a.personas||1)>=2, gb=(b.personas||1)>=2;
    return grupal?(gb-ga):(ga-gb);});
  if(!lista.length)return '<p class="hint" style="margin:2px 0">'+(grupal
    ?'Todavía no copiaste ninguna foto grupal: hacelo en ✍️ Mis poses → 📷 Copiar toma de una foto.'
    :'Todavía no copiaste ninguna pose de una foto (✍️ Mis poses → 📷 Copiar toma de una foto).')+'</p>';
  return '<div class="selpose" data-hid="'+hid+'" style="display:flex;gap:6px;flex-wrap:wrap">'
    +'<button type="button" class="ghost selpose-x" style="height:64px">Ninguna</button>'
    +lista.map(p=>'<span style="position:relative;display:inline-block">'
      +'<img src="'+p.ref+'" data-txt="'+esc(p.texto)+'" title="'+esc(p.texto)+'" style="height:64px;border-radius:7px;cursor:pointer;border:3px solid transparent">'
      +((p.personas||1)>=2?'<span style="position:absolute;left:4px;bottom:4px;background:rgba(0,0,0,.65);color:#fff;font-size:11px;border-radius:5px;padding:0 4px">👥'+p.personas+'</span>':'')
      +'</span>').join("")
    +'</div>';
}
function activarSelectorPose(cont){
  cont.querySelectorAll(".selpose").forEach(box=>{
    const hid=document.getElementById(box.dataset.hid);if(!hid)return;
    const marcar=()=>{
      box.querySelectorAll("img").forEach(im=>im.style.borderColor=(im.dataset.txt===hid.value?"var(--rose-deep)":"transparent"));
      // En la tarjeta de una modelo, con una pose de mis fotos elegida, el selector frente/
      // perfil/espalda no corre: se apaga para que se vea cuál manda.
      const m=/^g-tpg(\d+)$/.exec(hid.id||""); const sel=m&&document.getElementById("g-tpo"+m[1]);
      if(sel){sel.disabled=!!hid.value; sel.title=hid.value?"Manda la pose de tus fotos elegida abajo":"";}
    };
    box.querySelectorAll("img").forEach(im=>im.onclick=()=>{hid.value=im.dataset.txt;marcar();});
    const x=box.querySelector(".selpose-x");if(x)x.onclick=()=>{hid.value="";marcar();};
    marcar();
  });
}
function refrescarSelectoresPose(){
  // Nunca puede romper la página: si algo falla acá, el resto sigue cargando.
  try{
    document.querySelectorAll("[data-selpose]").forEach(w=>{
      w.innerHTML=selectorPoseHTML(w.dataset.selpose, w.dataset.grupal==="1");
      activarSelectorPose(w);
    });
  }catch(e){console.warn("selector de poses",e);}
}
let POSE_GUIA_TMP="";
// JSON: prendido por defecto; la elección se recuerda (la leen también Storyboard y Reels)
(function(){const cb=$("#g-prompt-json");if(!cb)return;
  try{cb.checked=localStorage.getItem("luma_prompt_json")!=="0";}catch(e){}
  cb.addEventListener("change",()=>{try{localStorage.setItem("luma_prompt_json",cb.checked?"1":"0");}catch(e){}});
  jget("/api/json_stats").then(r=>{const st=r.stats||{},t={texto:{ok:0,bloq:0},json:{ok:0,bloq:0}};let res=0;
    Object.values(st).forEach(m=>{["texto","json"].forEach(f=>{if(m[f]){t[f].ok+=m[f].ok||0;t[f].bloq+=m[f].bloq||0;}});res+=m.rescates||0;});
    const pct=f=>{const n=t[f].ok+t[f].bloq;return n?`${t[f].ok} de ${n} salieron (${Math.round(100*t[f].ok/n)}%)`:"todavía sin datos";};
    const el=$("#g-json-stats");if(el)el.textContent=`Medición: en texto ${pct("texto")} · en JSON ${pct("json")}`+(res?` · ${res} rescatada${res>1?"s":""} en JSON`:"");}).catch(()=>{});})();
if($("#g-pose-lumen"))$("#g-pose-lumen").onclick=async()=>{
  if(!window.Lumen)return toast("No cargó Lumen: recargá la página.",true);
  const t=await window.Lumen.elegir({titulo:"Elegí la toma: su maniquí y su cámara son la guía de la foto"});
  if(!t)return;
  const est=$("#g-pose-foto-est");if(est)est.textContent="Trayendo la toma de Lumen…";
  try{
    const r=await jpost("/api/pose_desde_lumen",{lumen:{proyecto:t.proyecto,toma:t.id}});
    const ta=$("#g-pose-nueva");ta.value=r.pose;POSE_GUIA_TMP=r.guia_tmp||"";
    const gp=$("#g-pose-guia");
    if(gp)gp.innerHTML='<img src="'+r.guia_preview+'" style="height:110px;border-radius:8px;border:1px solid var(--line)"> <span class="hint">Guía de Lumen ('+esc(r.aspect||"")+'): la foto copia la pose, el encuadre y la cámara de esta toma; el lugar sale de lo que elegiste.</span>';
    if(est)est.textContent="Listo: revisala y tocá ➕ Agregar para guardarla con su guía de Lumen.";
    ta.focus();
  }catch(err){if(est)est.textContent="";toast(errMsg(err),true);}
};
if($("#g-pose-foto"))$("#g-pose-foto").onchange=async e=>{
  const f=e.target.files[0];if(!f)return;
  const est=$("#g-pose-foto-est");
  // Primero el MANIQUÍ 3D (gratis, en el navegador). Si no carga o ella pide IA, el de IA.
  let g3=null;
  if(window.Pose3D){
    try{ g3=await window.Pose3D.copiarToma(f); }catch(err){ g3={modo:"ia"}; }
    if(g3===null){ e.target.value=""; return; }          // canceló
  }
  if(est)est.textContent=(g3&&g3.modo==="3d")?"Escribiendo la toma…":"Mirando la pose y armando el maniquí con IA…";
  try{
    const img=await downscaleImage(f,1600);
    const body={imagen:img};
    if(g3&&g3.modo==="3d"){body.guia_3d=g3.guia;body.personas=g3.personas;}
    if(g3){ // lo medido en el navegador (también si eligió el maniquí con IA)
      if(g3.encuadre)body.encuadre_guia=g3.encuadre;
      if(g3.sujetos)body.sujetos=g3.sujetos;
      if(g3.image_hash)body.image_hash=g3.image_hash;
      if(g3.img_w){body.img_w=g3.img_w;body.img_h=g3.img_h;}
      if(g3.medidas)body.medidas=g3.medidas;
    }
    const r=await jpost("/api/pose_desde_foto",body);
    const ta=$("#g-pose-nueva");
    ta.value=r.pose;          // una sola toma por foto: así la guía queda atada a esta pose
    POSE_GUIA_TMP=r.guia_tmp||"";
    const gp=$("#g-pose-guia");
    if(r.image_hash)setTimeout(()=>abrirPerfilFoto(r.image_hash),0);
    if(gp)gp.innerHTML=r.guia_preview?'<img src="'+r.guia_preview+'" style="height:110px;border-radius:8px;border:1px solid var(--line)"> <span class="hint">Maniquí guía ('+esc(r.aspect||"")+'): la toma va a copiar esta pose, cámara y encuadre.</span>':"";
    const _extra=(r.desde_cache?" · foto ya analizada: se reusó su perfil, sin costo":"")
      +((r.perfil_avisos&&r.perfil_avisos.length)?" · ⚠ "+r.perfil_avisos.slice(0,2).join(" · "):"");
    if(est)est.textContent=(r.guia_tmp?("Listo: revisala y tocá ➕ Agregar para guardarla (con su maniquí guía"+(r.guia_via==="3d"?": calco gratis":r.guia_via==="seedream"?", hecho con Seedream porque Google bloqueó la foto":r.guia_via==="texto"?", dibujado desde la descripción porque la foto se bloqueó: revisá que se parezca":"")+")."):"Listo (sin guía: "+(r.guia_error||"no se pudo hacer el maniquí")+"). Revisala y tocá ➕ Agregar.")+_extra;
    ta.focus();
  }catch(err){if(est)est.textContent="";toast(errMsg(err),true);}
  e.target.value="";
};
// ---- Escenas listas: lugar, luz, estilo y poses con su ángulo, ya armados ----
const ESCENAS=%%ESCENAS%%;
// El color del short: cada escena puede traer sus colores con parte de arriba y accesorios.
function escenaColores(){
  const e=ESCENAS[$("#escena-list").value], sel=$("#escena-color");
  const cols=(e&&e.colores)||{};
  const ks=Object.keys(cols);
  sel.innerHTML='<option value="">— '+esc((e&&e.etiqueta_color)||"color del short")+' —</option>'
    +ks.map(k=>'<option value="'+k+'">'+esc(cols[k].nombre)+'</option>').join("");
  sel.style.display=ks.length?"":"none";
}
function aplicarColorEscena(){
  const e=ESCENAS[$("#escena-list").value], k=$("#escena-color").value;
  const c=e&&e.colores&&e.colores[k]; if(!c)return false;
  Object.keys(c).forEach(id=>{if(id==="nombre")return;const el=$("#"+id);if(el)el.value=c[id];});
  return true;
}
function escenaReset(){
  // Cada escena arranca limpia: lo que no trae vuelve a lo de siempre (si no, el "hombre" o la
  // cámara de película de la escena anterior quedaban pegados en la siguiente).
  ["g-lente","g-camara-foto","g-movimiento"].forEach(id=>{const el=$("#"+id);if(el)el.value="auto";});
  if($("#g-pareo"))$("#g-pareo").value="";
  if($("#g-ajustes"))$("#g-ajustes").value="";
  const g=$("#g-genero");if(g&&g.value!=="mujer"){g.value="mujer";g.dispatchEvent(new Event("change"));}
}
function escenaSetPanel(){
  const e=ESCENAS[$("#escena-list").value], box=$("#escena-set");
  if(!box)return;
  if(!e||!e.set||!e.colores){box.style.display="none";return;}
  const ks=Object.keys(e.colores);
  $("#escena-set-cols").innerHTML=e.set.modelos.map((m,i)=>
    '<div><label>Modelo '+(i+1)+'</label><select id="escena-set-c'+i+'">'
    +ks.map(k=>'<option value="'+k+'"'+(k===m.color?" selected":"")+'>'+esc(e.colores[k].nombre)+'</option>').join("")
    +'</select></div>').join("");
  box.style.display="";
}
$("#escena-list").onchange=()=>{escenaColores();escenaSetPanel();};
if($("#escena-set-load"))$("#escena-set-load").onclick=()=>{
  const e=ESCENAS[$("#escena-list").value]; if(!e||!e.set)return;
  escenaReset();
  // 1) el lugar, la luz, el estilo y la prenda de la escena (sin la temporada)
  const c=e.campos||{};
  Object.keys(c).forEach(id=>{ if(id==="g-temporada")return; const el=$("#"+id); if(!el)return;
    if(el.type==="checkbox")el.checked=!!c[id]; else el.value=c[id];
    if(id==="g-genero")el.dispatchEvent(new Event("change")); });
  // 2) modo set de colores, 3 modelos
  $("#g-temporada").value="interior_set"; $("#g-temporada").dispatchEvent(new Event("change"));
  if($("#g-no-lenceria"))$("#g-no-lenceria").value=e.set.lenceria?"":"si";   // es ropa, no lencería
  if($("#set-genero")){$("#set-genero").value=e.set.genero||"mujer";$("#set-genero").dispatchEvent(new Event("change"));}
  const sn=$("#set-n"); if(sn){sn.value=String(e.set.n||3); sn.dispatchEvent(new Event("change"));}
  // 3) cada modelo: su color, su pose, y en las indicaciones su outfit + accesorios + gesto
  e.set.modelos.forEach((m,i)=>{
    const sel=$("#escena-set-c"+i), col=e.colores[(sel&&sel.value)||m.color]||{};
    const put=(id,v)=>{const el=document.getElementById(id+i); if(el)el.value=v||"";};
    put("g-tcol",col["g-color"]); if(m.pose)put("g-tpo",m.pose);   // sin pose en la escena: la elige ella
    put("g-tout",m.outfit||((col["g-piezas"]||"").match(/ con (.*)$/)||["",""])[1]);
    put("g-tind",[col["g-accesorios"],m.ind].filter(Boolean).join("; "));
  });
  if(e.set.piezas!==undefined&&$("#g-piezas"))$("#g-piezas").value=e.set.piezas;
  if(e.set.accesorios!==undefined&&$("#g-accesorios"))$("#g-accesorios").value=e.set.accesorios;
  if($("#g-tgrupal-ind"))$("#g-tgrupal-ind").value=e.set.grupal||"";
  const w=$("#wrap-colores"); if(w&&w.tagName==="DETAILS")w.open=true;
  const hint=$("#escena-hint");
  hint.innerHTML="✓ Set de 3 armado con la escena. Ahora: <b>subí las fotos de la prenda</b> (frente, costado y espalda), elegí el <b>avatar de cada modelo</b> en sus tarjetas y tocá <b>🎨 Generar set de colores</b>.";
  hint.style.display="";
  toast("Set de 3 armado ✓");
};
$("#escena-color").onchange=()=>{
  // Con la escena ya cargada, cambiar el color sólo cambia color, arriba y accesorios.
  if(ESCENA_CARGADA&&ESCENA_CARGADA===$("#escena-list").value&&aplicarColorEscena())
    toast("Color "+$("#escena-color").selectedOptions[0].textContent+" cargado ✓");
};
let ESCENA_CARGADA="";
$("#escena-load").onclick=()=>{
  if($("#g-no-lenceria"))$("#g-no-lenceria").value="";
  escenaReset();
  const k=$("#escena-list").value, e=ESCENAS[k];
  const wrap=$("#wrap-escena-poses"), hint=$("#escena-hint");
  if(!e){wrap.style.display="none";$("#escena-poses").innerHTML="";hint.style.display="none";
         return toast("Elegí una escena.",true);}
  const c=e.campos||{};
  // La temporada primero: su "change" reacomoda la pantalla y no tiene que pisar lo demás.
  if(c["g-temporada"]!==undefined&&$("#g-temporada")){
    $("#g-temporada").value=c["g-temporada"];
    $("#g-temporada").dispatchEvent(new Event("change"));
  }
  Object.keys(c).forEach(id=>{
    if(id==="g-temporada")return;
    const el=$("#"+id); if(!el)return;
    if(el.type==="checkbox")el.checked=!!c[id]; else el.value=c[id];
    if(id==="g-genero")el.dispatchEvent(new Event("change"));
  });
  // Las poses del listado de siempre (y el producto colgado) se destildan: el set sale
  // sólo con las de la escena que ella tilde.
  document.querySelectorAll("#pose-pick input[type=checkbox]").forEach(cb=>cb.checked=false);
  $("#escena-poses-titulo").textContent="🎬 Poses de la escena: "+e.nombre;
  $("#escena-poses").innerHTML=(e.poses||[]).map(p=>
    '<div class="pkrow"><label class="pk"><input type="checkbox" data-txt="'+esc(p.texto)+'"'+(p.on?" checked":"")+'> '+esc(p.texto)+'</label>'
    +'<select class="camsel">%%CAMOPTS%%</select></div>').join("");
  const sels=document.querySelectorAll("#escena-poses .camsel");
  (e.poses||[]).forEach((p,i)=>{if(sels[i]&&p.camara!==undefined&&p.camara!==null)sels[i].value=String(p.camara);});
  ESCENA_CARGADA=k;
  const conColor=aplicarColorEscena(), conPoses=(e.poses||[]).length>0, qe=esc((e.etiqueta_color)||"color del short");
  wrap.style.display=conPoses?"":"none";
  $("#wrap-poses").open=true;
  hint.innerHTML="✓ Escena cargada. Ahora: <b>elegí el avatar</b>, <b>subí las fotos del producto</b> y "
    +(conPoses?"tildá las poses en <b>Elegir poses del set</b>.":"<b>agregá tus poses</b> (esta escena no trae: escribilas o tildá las de Mis poses).");
  hint.style.display="";
  if(e.colores&&!conColor)hint.innerHTML+=" 🎨 Elegí también el <b>"+qe+"</b> (arriba): carga lo que le va.";
  toast('Escena "'+e.nombre+'" cargada');
};

// ---- Plantillas de artículo ----
const TPL_FIELDS=["g-tela","g-color","g-punos","g-costuras","g-cuello","g-notiene","g-pose","g-fondo","g-luz","g-encuadre","g-encuadre-zona","g-aclaraciones"];
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
function esKids(){return ($("#g-temporada")?$("#g-temporada").value:"")==="kids";}
const PELO_ADULTO=[["","(libre)"],["morocha_largo_ondulado","Largo ondulado oscuro"],["negro_lacio","Negro lacio"],["castaño_largo","Castaño largo"],["castaño_ondulado","Castaño ondulado"],["rubia","Rubia"],["pelirroja","Pelirroja"],["corto","Corto"]];
const PELO_KIDS=[["","(libre)"],["rubio","Rubio/a"],["castano","Castaño"],["morocho","Morocho/a (pelo oscuro)"],["pelirrojo","Pelirrojo/a"],["negro_lacio","Negro lacio"],["rulos","Con rulos"]];
let PELO_MODO="";
function applyModoFoto(){
  const v=$("#g-temporada")?$("#g-temporada").value:"invierno";
  const kids=(v==="kids");
  const show=(id,vis)=>{const e=document.getElementById(id);if(e)e.style.display=vis?"":"none";};
  // Set de colores: para ropa interior. En kids va el MISMO set de poses de adultos, y
  // ADEMÁS el mismo panel del set sirve de PACK (de 2 a 6 prendas, solas o con modelo).
  const esColores=(v==="interior_set");
  const wc=$("#wrap-colores"), wp=$("#wrap-poses"), wb=$("#wrap-gobtns");
  if(wc)wc.style.display=(esColores||kids)?"block":"none";
  if(wp)wp.style.display=esColores?"none":"block";
  if(wb)wb.style.display=esColores?"none":"flex";
  show("colores-hints-adulto",!kids);show("kids-pack-wrap",kids);
  ["set-genero-wrap","trio-extras-wrap","inc-prod-wrap"].forEach(id=>show(id,!kids));
  const st=$("#set-titulo");if(st)st.textContent=kids?"👧 Pack de nenas/nenes (de 2 a 6 prendas)":"🎨 Set de colores (seamless / ropa interior)";
  const bs=$("#btn-set-colores");if(bs)bs.textContent=kids?"👧 Generar pack":"🎨 Generar set de colores";
  const kp=$("#kids-pack"), kpw=$("#kids-pack-prod-wrap");
  if(kpw)kpw.style.display=(kids&&kp&&kp.value==="modelo")?"none":"";
  // NENAS/NENES: mismo menú que adultos, pero sin avatares (el chico lo inventa la IA):
  // la apariencia pregunta nena/nene, edad, etnia, pelo, ojos y altura; se esconde lo
  // que es de cuerpo adulto (busto, cola, contextura, edad, altura de mujer, bombacha).
  show("wrap-avatar",!kids);show("wrap-noavatar",!kids);
  const ab=$("#apar-box");if(ab)ab.style.display=(kids||noAvatar())?"block":"none";
  show("wrap-genero",!kids);show("wrap-kids-quien",kids);show("wrap-ap-edad",!kids);show("wrap-kids-altura",kids);
  const at=$("#apar-title");if(at)at.textContent=kids?"Cómo es el nene/nena (lo inventa la IA, nunca un avatar):":"Apariencia de la modelo IA (no queda al azar):";
  const ax=$("#ap-extra");if(ax)ax.placeholder=kids?"ej: pecas, rulos, sonrisa con dientes de leche":"ej: bronceada, pecas, fit, cara redonda, sonrisa amplia";
  const lx=$("#lbl-ap-extra");if(lx)lx.textContent=kids?"Detalle extra del nene/nena (texto libre)":"Detalle extra de la modelo (texto libre)";
  const pn=$("#g-peinado");if(pn&&pn.options.length)pn.options[0].textContent=kids?"(libre)":"(según avatar)";
  const modo=kids?"kids":"adulto";
  if(modo!==PELO_MODO){setSelOpts("#ap-pelo",kids?PELO_KIDS:PELO_ADULTO);PELO_MODO=modo;}
  ["wrap-cuerpo-a","wrap-contextura","wrap-edadcorp","wrap-altura","wrap-complemento"].forEach(id=>show(id,!kids));
  const pp=$("#pose-pick"), pk=$("#pose-pick-kids");
  if(pp)pp.style.display=kids?"none":"grid";
  if(pk)pk.style.display=kids?"grid":"none";
  if(typeof renderTrioCards==="function")renderTrioCards();
}
if($("#g-temporada"))$("#g-temporada").addEventListener("change",applyModoFoto);
if($("#kids-pack"))$("#kids-pack").addEventListener("change",applyModoFoto);
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
cargarMisPoses();
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
  const N=(typeof setN==="function")?setN():3;
  const kids=(typeof esKids==="function")&&esKids();
  for(let i=0;i<N;i++){
    const col=(($("#g-tcol"+i)||{}).value||"").trim();
    // En kids una pieza vale con su foto aunque no le pongas color: la foto YA dice
    // de qué color es. Con modelo, en cambio, el color es lo que define la toma.
    if(!col&&!(kids&&SET_FOTOS[i])){continue;}
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
      indicacion:($("#g-tind"+i)||{}).value||"",
      outfit:(($("#g-tout"+i)||{}).value||"").trim(),
      peinado:($("#g-tpn"+i)||{}).value||"",peinado_libre:($("#g-tpnl"+i)||{}).value||"",
      pose_guardada:($("#g-tpg"+i)||{}).value||"",
      // pack de kids con modelo: cómo es el nene/nena de ESTA prenda
      quien:($("#g-tkq"+i)||{}).value||"",talle:($("#g-tkt"+i)||{}).value||"",
      ojos:($("#g-tojo"+i)||{}).value||"",altura_kids:($("#g-tkal"+i)||{}).value||"",
      extra:($("#g-tkx"+i)||{}).value||""};
    // La foto propia de esta pieza (cuando el set son estampas distintas).
    if(SET_FOTOS[i])item.foto=SET_FOTOS[i];
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
    if(d.almacenamiento)t+="ALMACENAMIENTO: "+d.almacenamiento+(d.almacenamiento==="memoria"
      ?"  ⚠ SIN REDIS: los trabajos viven en la RAM y se pierden si el server se reinicia (pantalla colgada). Revisá REDIS_URL en Railway."
      :"  ✓ ok")+"\n";
    if(d.error_general)t+="ERROR GENERAL: "+d.error_general+"\n";
    (d.tomas||[]).forEach(x=>{
      t+="\nToma "+x.toma+" ("+(x.que_es||"?")+")\n  estado: "+x.estado+" · imagen: "+(x.tiene_imagen?"SÍ":"NO");
      if(x.error)t+="\n  error: "+x.error;
    });
    if(d.prompt_flux&&d.prompt_flux.largo)t+="PROMPT SEEDREAM: "+d.prompt_flux.largo+" caracteres (tope "
      +d.prompt_flux.tope+", el corte real de ByteDance es ~5000)"
      +(d.prompt_flux.descartados?" · se recortaron "+d.prompt_flux.descartados+" bloque(s) para entrar"
        +((d.prompt_flux.cortados||[]).length?" ("+d.prompt_flux.cortados.join(", ")+")":""):"")+"\n";
    if(d.error_fal&&d.error_fal.detalle)t+="\n===== ÚLTIMO RECHAZO DE FAL (lo que respondió) =====\n"
      +"modelo: "+(d.error_fal.modelo||"?")+" · HTTP "+(d.error_fal.http||"?")
      +" · intento "+(d.error_fal.intento||"?")+" · imágenes enviadas: "+(d.error_fal.n_imagenes||"?")+"\n"
      +"campos: "+((d.error_fal.campos||[]).join(", "))+"\n"+d.error_fal.detalle+"\n";
    if(d.pedido&&d.pedido.endpoint)t+="\n===== PEDIDO EXACTO ENVIADO (sin la API key) =====\n"+JSON.stringify(d.pedido,null,2)+"\n";
    if(d.prompt_ultima_bloqueada)t+="\n\n===== PROMPT EXACTO DE LA ÚLTIMA TOMA BLOQUEADA =====\n"+d.prompt_ultima_bloqueada;
    if(d.prompt_ultima_toma)t+="\n\n===== PROMPT DE LA ÚLTIMA TOMA ENVIADA (salga o no) =====\n"+d.prompt_ultima_toma;
    out.textContent=t;
  }catch(e){out.textContent="No pude consultar: "+(e.message||e);}
};
if($("#btn-set-colores"))$("#btn-set-colores").onclick=async()=>{
  // Junta las fichas de cada modelo (con avatar o IA, todas van por 'asign')
  let asign=gatherAsign();
  if(asign.length<1)return toast(((typeof esKids==="function")&&esKids())
    ?"Cargá al menos una prenda (ponele el color o subile su foto)."
    :"Cargá al menos una pieza con su color.",true);
  // Las fotos compartidas de arriba. Si el set son estampas distintas y cada pieza
  // trae la suya, no hacen falta: se usa la de la primera pieza como referencia
  // general (el prompt igual manda a cada modelo a SU imagen).
  const propias=asign.filter(x=>x.foto);
  let prodsSet=GEN_PRODUCTS;
  if(!prodsSet.length){
    if(propias.length<asign.length)
      return toast("Subí las fotos del producto, o la foto propia de CADA pieza.",true);
    prodsSet=[propias[0].foto];
  }
  const extras=gatherExtras();
  const incG=$("#inc-grupal")?$("#inc-grupal").checked:true;
  const incI=$("#inc-ind")?$("#inc-ind").checked:true;
  const incP=$("#inc-prod")?$("#inc-prod").checked:false;
  let modoP=$("#inc-prod-modo")?$("#inc-prod-modo").value:"flat_lay";
  const kids=(typeof esKids==="function")&&esKids();
  if(kids&&$("#kids-pack-prod"))modoP=$("#kids-pack-prod").value;   // cómo se muestran las prendas del pack
  const n=asign.length;
  const total=kids?(((incG&&n>=2)?1:0)+(incI?n:0))
                  :(((incG&&n>=2)?1:0)+(incI?n:0)+extras.length+(incP?1:0));
  if(incG&&n<2)toast("Con una sola pieza no hay foto de conjunto: sale la individual.",false);
  if(total<1)return toast("Elegí al menos una imagen para el set.",true);
  if(!confirm("Genera "+total+" imágenes. ¿Seguimos?"))return;
  $("#btn-set-colores").disabled=true;$("#btn-gen").disabled=true;$("#btn-set").disabled=true;
  SET_RESULTS=[];
  const prog=makeProgress("#gen-out");
  try{
    const simple=!!($("#set-simple")&&$("#set-simple").checked);
    const prm=genParams();
    prm.genero=setGenero();
    if(simple)prm.modo_minimo="si";
    const body={avatar_id:null,product_images:prodsSet,image_size:GEN_SIZE,
      style:$("#g-style").value,reframe:"4:5",modo_producto:modoP,
      params:prm,save_to_drive:true,asign:asign,extras:extras,
      inc_grupal:incG,inc_ind:incI,inc_prod:incP,
      kids_pack:(kids&&$("#kids-pack"))?$("#kids-pack").value:"",
      grupal_indicacion:($("#g-tgrupal-ind")||{}).value||"",
      grupal_pose:($("#g-tgrupal-pose")||{}).value||"",
      product_tags:(GEN_PRODUCTS.length?GEN_PRODUCT_TAGS:[]),
      fotos_modo:(GEN_PRODUCTS.length?(($("#set-fotos-modo")||{}).value||"color"):"prenda"),
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
    renderDZ("dz-gen");
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
<script type="module" src="%%PREFIX%%/pose3d.js"></script>
<script src="/lumen/elegir.js"></script>
</body>
</html>
"""
# Las opciones del selector de ángulo salen del mismo CAMARA_POOL que arma el prompt:
# si mañana se agrega una posición, aparece sola en los tres selectores de la página.
CAM_OPTS_HTML = ('<option value="">🎲 Variado</option>'
                 + "".join(f'<option value="{i}">{c["lbl"]}</option>'
                           for i, c in enumerate(CAMARA_POOL)))
# Los nombres solos no alcanzan si no sos fotógrafa: cada uno explicado en criollo,
# armado del mismo pool para que un ángulo nuevo aparezca solo también acá.
CAM_AYUDA_HTML = (
    '<details class="camayuda"><summary>📷 ¿Qué es cada ángulo? (tocá para ver)</summary>'
    '<p class="hint" style="margin:6px 0">🎲 <b>Variado</b> — es lo que viene puesto: la '
    'cámara se va moviendo sola y no repite ángulo dentro del mismo set.</p><ul>'
    + "".join(f'<li><b>{c["lbl"]}</b> — {c["ayuda"]}</li>' for c in CAMARA_POOL)
    + '</ul><p class="hint" style="margin:6px 0 0">Ninguno de estos cambia qué parte del '
      'cuerpo entra en la foto: eso lo sigue mandando el <b>Encuadre de la prenda</b>. '
      'Los tres que miran desde abajo pueden hacer rebotar la imagen en Seedream cuando '
      'es lencería o malla — si los elegís igual, se respetan.</p></details>')

# Las opciones del selector de época salen del mismo listado que arma el prompt.
EST_AV_OPTS_HTML = "".join(f'<option value="{k}">{v["lbl"]}</option>'
                           for k, v in ESTILOS_AVATAR.items())
EST_AV_AYUDA_JS = "{" + ",".join(f'"{k}":"{v["ayuda"]}"' for k, v in ESTILOS_AVATAR.items()) + "}"

# ESCENAS LISTAS: una producción armada de antemano (lugar, luz, estilo, qué más lleva
# puesto) con sus poses, cada una con el ángulo de cámara que le va (el número es la
# posición en CAMARA_POOL). Ella elige el avatar, sube las fotos de la prenda y tilda
# las poses que quiere: nada más. Para sumar otra escena, otra entrada acá.
ESCENAS_LISTAS: Dict[str, Dict[str, Any]] = {
    # Conjunto de encaje guipur LP3519 (corpiño push-up con aro + tanga de tiras regulables), en 4
    # colores, para vender en SET DE 3. Un solo lugar para todos: el dormitorio blanco, completo
    # (molduras, muebles, rincones) para que cada toma recorra una parte distinta. SIN poses.
    "conjunto_guipur_dormitorio_blanco": {
        "nombre": "Conjunto de encaje guipur · dormitorio blanco con molduras (set de 3)",
        "etiqueta_color": "color del conjunto",
        "campos": {
            "g-temporada": "invierno",
            "g-fondo": ("dormitorio amplio y luminoso de estilo parisino, todo en blanco, crudo y madera clara: paredes "
                        "con molduras clásicas (boiserie) y zócalos altos, techo alto con moldura y una araña chica de "
                        "cristal, piso de madera clara en espiga con una alfombra de lana crema. Una cama grande con "
                        "respaldo capitoné blanco, sábanas de lino blanco apenas arrugadas y almohadones; mesas de luz "
                        "de madera clara con un velador de tela y un florero con peonías blancas; un ventanal francés "
                        "alto con cortinas de lino blanco; un sillón bergère tapizado en lino junto a la ventana; un "
                        "tocador con un espejo oval de marco dorado envejecido; una chimenea de mármol blanco con "
                        "velas; un biombo de madera con una bata de satén colgada. CADA TOMA EN UN RINCÓN DISTINTO "
                        "del cuarto (la cama, el ventanal, el sillón, el espejo del tocador, la chimenea, el biombo)"),
            "g-luz": ("luz natural de la mañana que entra por el ventanal, suave y difusa a través de las cortinas de "
                      "lino, sombras suaves; blancos limpios, crema y madera clara, piel luminosa; nada de flash"),
            "g-style": "editorial",
            "g-piezas": "usa el conjunto completo: el corpiño y la tanga, los dos del mismo color",
            "g-aclaraciones": ("conjunto de encaje: CORPIÑO con aro y taza armada push-up (balconette), las tazas cubiertas "
                               "de encaje de guipur floral en relieve, un moñito chico en el centro, laterales anchos de "
                               "encaje con el borde festoneado, prendido atrás de 3 ganchos y breteles finos regulables; "
                               "TANGA colaless con el frente de guipur con el borde festoneado arriba, cola de tul con "
                               "encaje y tiras laterales finas REGULABLES (con regulador y argolla). Las dos piezas del "
                               "MISMO color y el mismo encaje"),
            "g-accesorios": ("aritos chicos dorados, pelo suelto con ondas suaves, maquillaje natural luminoso, uñas nude, "
                             "actitud serena y elegante"),
            "g-peinado": "largo_ondulado",
            "g-foco": "desenfocado",
            "g-encuadre-zona": "",
        },
        # El mismo lugar para todos los colores: cada color sólo cambia el color del conjunto.
        "colores": {
            "blanco": {"nombre": "🤍 Blanco", "g-color": "blanco marfil"},
            "nude": {"nombre": "🤎 Nude", "g-color": "nude (beige rosado claro)"},
            "negro": {"nombre": "🖤 Negro", "g-color": "negro"},
            "rojo": {"nombre": "❤️ Rojo", "g-color": "rojo intenso"},
        },
        # SET DE 3: una modelo por color (se cambian en el panel). Las poses las pone ella.
        "set": {
            "n": 3,
            "lenceria": True,
            "modelos": [{"color": "blanco"}, {"color": "negro"}, {"color": "rojo"}],
            "piezas": "el conjunto completo: el corpiño y la tanga, los dos del mismo color",
            "accesorios": "aritos chicos dorados, pelo suelto con ondas suaves, maquillaje natural luminoso, uñas nude",
            "grupal": "",
        },
    },
    # Toallón playero redondo con flecos (WTJ-53995, 6 diseños). Estilo de las referencias que
    # eligió: playa con luz natural, look de película analógica, el toallón abierto con el
    # viento y los flecos volando, actitud espontánea. SIN poses: las agrega ella.
    "toallon_redondo_playa": {
        "nombre": "Toallón redondo con flecos · playa estilo película",
        "etiqueta_color": "diseño del toallón",
        "campos": {
            "g-temporada": "verano",
            "g-fondo": "playa con rocas oscuras y el mar al atardecer, arena húmeda que refleja el cielo",
            "g-luz": ("luz natural de sol de la tarde, a contraluz o de costado: el sol atraviesa los flecos y "
                      "el borde del toallón y brilla en el pelo; colores cálidos y suaves de película analógica "
                      "(tipo Kodak Portra), grano fino; nada de flash ni luz de estudio"),
            "g-style": "editorial",
            "g-lente": "documental_35",
            "g-camara-foto": "portra",
            "g-viento": True,
            "g-piezas": ("la modelo usa una malla lisa que combina con el toallón; el TOALLÓN es el protagonista "
                         "de la foto: se ve entero o casi entero, con el estampado hacia la cámara y los flecos "
                         "a la vista (lo sostiene abierto, se envuelve con él, lo sacude al viento o está sobre "
                         "la arena, según la pose)"),
            "g-aclaraciones": ("TOALLÓN PLAYERO REDONDO (el producto de la foto, no es ropa) de 150 cm de diámetro: "
                               "un círculo grande donde entra una persona acostada. Rizo fino estampado de UN SOLO "
                               "LADO con el diseño de las fotos de referencia, que ocupa todo el círculo; el REVERSO "
                               "es BLANCO LISO, sin estampa (si se ve el revés, es blanco). Todo el borde lleva FLECOS "
                               "BLANCOS de algodón TRENZADOS, de unos 8 cm, parejos alrededor del círculo. Copiá el "
                               "diseño EXACTO de las fotos (dibujo, colores y posición); no inventes otro estampado "
                               "ni agregues texto o logos que no estén en la estampa"),
            "g-accesorios": ("anteojos de sol, pelo con ondas de playa movido por el viento, piel natural con un "
                             "poco de sal y sol, sonrisa o gesto espontáneo"),
            "g-peinado": "largo_ondulado",
            "g-foco": "desenfocado",
            "g-encuadre-zona": "",
        },
        # Cada diseño con su estampa y la malla que le va (pisa Color y Piezas). El lugar es el mismo para
        # todos: el de las garzas (rocas oscuras y el mar al atardecer), que es el que más le gustó.
        "colores": {
            "tortuga": {"nombre": "🐢 Tortuga", "g-color": "estampa de una tortuga marina marrón y naranja en acuarela sobre agua celeste y azul con olas",
                        "g-piezas": "la modelo usa un bikini liso turquesa; el TOALLÓN es el protagonista: se ve entero o casi entero, con el estampado hacia la cámara y los flecos a la vista"},
            "delfin": {"nombre": "🐬 Delfín mandala", "g-color": "estampa de un delfín azul con dibujos de mandala sobre fondo azul liso con ondas celestes",
                       "g-piezas": "la modelo usa una malla enteriza lisa negra; el TOALLÓN es el protagonista: se ve entero o casi entero, con el estampado hacia la cámara y los flecos a la vista"},
            "flamenco_cocos": {"nombre": "🌴 Flamenco con cocos", "g-color": "estampa de un flamenco rosa entre hojas de palmera verdes y cocos sobre fondo blanco",
                               "g-piezas": "la modelo usa un bikini liso verde oscuro; el TOALLÓN es el protagonista: se ve entero o casi entero, con el estampado hacia la cámara y los flecos a la vista"},
            "flamenco_stand_tall": {"nombre": "🦩 Flamenco 'Stand Tall'", "g-color": "estampa de un flamenco hecho de flores rosas sobre fondo blanco, con las letras 'Stand Tall' y 'flamingo' que SON PARTE de la estampa (copialas igual, en el mismo lugar)",
                                    "g-piezas": "la modelo usa una malla enteriza lisa rosa; el TOALLÓN es el protagonista: se ve entero o casi entero, con el estampado hacia la cámara y los flecos a la vista"},
            "cisnes": {"nombre": "🦢 Cisnes", "g-color": "estampa de un cisne negro y uno blanco enfrentados sobre agua azul noche con luna y estrellas, fondo azul francia",
                       "g-piezas": "la modelo usa un bikini liso blanco; el TOALLÓN es el protagonista: se ve entero o casi entero, con el estampado hacia la cámara y los flecos a la vista"},
            "garzas": {"nombre": "🪶 Garzas sobre negro", "g-color": "estampa de garzas blancas, hojas lilas y flores celestes y fucsias sobre fondo NEGRO",
                       "g-piezas": "la modelo usa una malla enteriza lisa negra; el TOALLÓN es el protagonista: se ve entero o casi entero, con el estampado hacia la cámara y los flecos a la vista"},
        },
    },
    "short_deportivo_pelicula": {
        "nombre": "Short deportivo con bolsillos · estilo película",
        "campos": {
            "g-temporada": "invierno",
            "g-fondo": ("escaleras de cemento de un estadio vacío y la costanera de la ciudad "
                        "al amanecer, sin gente; sol bajo y rasante, sombras largas, cielo "
                        "entre naranja y azul"),
            "g-luz": ("sol del amanecer bajo y rasante, a contraluz o de costado con un flare "
                      "suave; look de película: sombras azuladas (teal) y luz naranja en la "
                      "piel, grano fino"),
            "g-style": "editorial",
            "g-piezas": ("usa el short deportivo con un top deportivo liso que combine y "
                         "zapatillas de running"),
            "g-aclaraciones": ("short deportivo (NO calza biker) con un bolsillo a cada lado, "
                               "izquierdo y derecho, los dos iguales y visibles"),
            "g-accesorios": "piel con brillo de entrenamiento, actitud seria y concentrada",
            "g-peinado": "atado",
            "g-foco": "desenfocado",
            "g-encuadre-zona": "",
        },
        # Cada color del short con su parte de arriba y sus accesorios: al elegir el
        # color se pisan "Color real", "Piezas" y "Accesorios" (lo demás no cambia).
        "colores": {
            "negro": {"nombre": "Negro", "g-color": "negro",
                      "g-piezas": "usa el short deportivo negro con un top deportivo blanco liso y zapatillas de running blancas",
                      "g-accesorios": "gorra negra, auriculares inalámbricos, piel con brillo de entrenamiento, actitud seria y concentrada"},
            "bordo": {"nombre": "Bordó", "g-color": "bordó",
                      "g-piezas": "usa el short deportivo bordó con un top deportivo color crema liso y zapatillas de running blancas",
                      "g-accesorios": "colita atada con una gomita bordó, aritos chicos dorados, piel con brillo de entrenamiento, actitud seria y concentrada"},
            "celeste": {"nombre": "Celeste", "g-color": "celeste",
                        "g-piezas": "usa el short deportivo celeste con un top deportivo blanco liso y zapatillas de running blancas con detalles celestes",
                        "g-accesorios": "botella de agua transparente en la mano, piel con brillo de entrenamiento, actitud seria y concentrada"},
            "azul_electrico": {"nombre": "Azul eléctrico", "g-color": "azul eléctrico (azul francia intenso)",
                               "g-piezas": "usa el short deportivo azul eléctrico con un top deportivo negro liso y zapatillas de running negras",
                               "g-accesorios": "reloj deportivo negro, piel con brillo de entrenamiento, actitud seria y concentrada"},
            "azul_oscuro": {"nombre": "Azul oscuro", "g-color": "azul oscuro (azul marino)",
                            "g-piezas": "usa el short deportivo azul oscuro con un top deportivo gris melange liso y zapatillas de running blancas",
                            "g-accesorios": "gorra azul oscuro, piel con brillo de entrenamiento, actitud seria y concentrada"},
            "beige": {"nombre": "Beige", "g-color": "beige",
                      "g-piezas": "usa el short deportivo beige con un top deportivo marrón chocolate liso y zapatillas de running color crema",
                      "g-accesorios": "medias blancas cortas, anteojos de sol deportivos sobre la cabeza, piel con brillo de entrenamiento, actitud seria y concentrada"},
            "chocolate": {"nombre": "Chocolate", "g-color": "marrón chocolate",
                          "g-piezas": "usa el short deportivo chocolate con un top deportivo beige liso y zapatillas de running color crema",
                          "g-accesorios": "anteojos de sol deportivos, aritos chicos dorados, piel con brillo de entrenamiento, actitud seria y concentrada"},
            "gris_oscuro": {"nombre": "Gris oscuro", "g-color": "gris oscuro (gris topo)",
                            "g-piezas": "usa el short deportivo gris oscuro con un top deportivo negro liso y zapatillas de running grises",
                            "g-accesorios": "auriculares inalámbricos, piel con brillo de entrenamiento, actitud seria y concentrada"},
        },
        "poses": [
            {"texto": "Parada firme de frente, piernas al ancho de los hombros, brazos relajados, cuerpo entero: se ve el calce y el largo del short",
             "camara": 1, "on": False},
            {"texto": "Las dos manos metidas en los dos bolsillos del short, mirada a cámara, de la rodilla para arriba: se ven los dos bolsillos a la vez",
             "camara": 0, "on": True},
            {"texto": "De costado, guardando el celular en el bolsillo derecho del short, plano medio",
             "camara": 11, "on": True},
            {"texto": "Girada hacia el otro lado, sacando las llaves del bolsillo izquierdo del short, plano medio",
             "camara": 2, "on": True},
            {"texto": "Detalle de la cadera con el celular guardado en el bolsillo del short, sin cara, luz rasante: el bolsillo sostiene y no se deforma",
             "camara": 5, "on": False},
            {"texto": "Subiendo un escalón de la escalera en zancada larga, de espalda en 3/4: el short no se sube y se ve cómo queda atrás",
             "camara": 12, "on": True},
            {"texto": "Corriendo por la costanera, de perfil, cuerpo entero, a contraluz",
             "camara": 4, "on": False},
            {"texto": "Estocada / elongación con un pie sobre el escalón, de frente en 3/4: el short estira sin transparentar",
             "camara": 1, "on": False},
            {"texto": "Sentada en un escalón, codos en las rodillas, respirando después de entrenar, plano medio",
             "camara": 13, "on": False},
            {"texto": "Arriba de todo de la escalera, manos en la cintura, el sol saliendo detrás y la ciudad, cuerpo entero",
             "camara": 6, "on": False},
            {"texto": "Primer plano de la cara, mirada fija a cámara, transpirada, fondo desenfocado",
             "camara": 4, "on": False},
            {"texto": "Detalle bien de cerca de la tela y la costura del short con el sol rasante, sin cara",
             "camara": 5, "on": False},
        ],
    },
    "short_tiro_alto_urbano": {
        "nombre": "Short tiro alto sin bolsillos · pared de hormigón al sol",
        "campos": {
            "g-temporada": "invierno",
            "g-fondo": ("una pared ALTA de hormigón crudo de fondo, gastada y medio marcada (manchas de "
                        "humedad, marcas del encofrado, alguna línea vieja de pintura), con un piso de "
                        "cemento liso y una columna de hormigón a un costado; espacio amplio, vacío y "
                        "minimalista, sin gente ni carteles"),
            "g-luz": ("sol directo de la tarde que entra en diagonal: recortes de luz y sombras "
                      "geométricas marcadas sobre la pared y el piso, reflejos cálidos del sol que "
                      "rebotan en el cemento, piel con brillo dorado; contraste alto pero natural"),
            "g-style": "instagram_real",
            "g-piezas": ("usa el short con un buzo corto tipo crop oversize de algodón, abierto o "
                         "levantado lo justo para que se vea entera la cintura alta del short, y "
                         "zapatillas urbanas blancas"),
            "g-aclaraciones": ("biker short SIN costuras y SIN bolsillos, de tiro MUY alto: la faja "
                               "acanalada es ancha y llega por encima del ombligo. A los costados, del "
                               "muslo a la cadera, un panel con textura de panal (puntitos calados) que "
                               "termina en una costura curva que sube hacia la cola. Frente y espalda "
                               "lisos, tejido melange, largo a mitad del muslo, sin cordón ni frunce atrás"),
            "g-accesorios": "aritos chicos, medias blancas cortas, actitud relajada y canchera",
            "g-peinado": "",
            "g-foco": "desenfocado",
            "g-encuadre-zona": "",
        },
        # Cada color del short con su buzo crop y sus accesorios.
        "colores": {
            'verde_petroleo': {"nombre": 'Verde petróleo', "g-color": 'verde petróleo melange (verde azulado grisáceo)',
                'g-piezas': 'usa el short verde petróleo con un buzo corto crop oversize gris melange claro y zapatillas urbanas blancas',
                'g-accesorios': 'aritos chicos plateados, colita alta, medias blancas cortas, actitud relajada y canchera'},
            'verde_claro': {"nombre": 'Verde claro', "g-color": 'verde claro (verde agua suave)',
                'g-piezas': 'usa el short verde claro con un buzo corto crop oversize blanco y zapatillas urbanas blancas',
                'g-accesorios': 'clip de pelo blanco, aritos chicos dorados, medias blancas cortas, actitud relajada y canchera'},
            'verde_oscuro': {"nombre": 'Verde oscuro', "g-color": 'verde oscuro (verde bosque)',
                'g-piezas': 'usa el short verde oscuro con un buzo corto crop oversize color crema y zapatillas urbanas color crema',
                'g-accesorios': 'gorra beige, aritos chicos dorados, medias blancas cortas, actitud relajada y canchera'},
            'fucsia': {"nombre": 'Fucsia', "g-color": 'fucsia',
                'g-piezas': 'usa el short fucsia con un buzo corto crop oversize gris melange y zapatillas urbanas blancas',
                'g-accesorios': 'anteojos de sol negros, aritos chicos plateados, medias blancas cortas, actitud relajada y canchera'},
            'bordo': {"nombre": 'Bordó', "g-color": 'bordó',
                'g-piezas': 'usa el short bordó con un buzo corto crop oversize rosa viejo claro y zapatillas urbanas color crema',
                'g-accesorios': 'colita atada con una gomita bordó, aritos chicos dorados, medias blancas cortas, actitud relajada y canchera'},
            'gris': {"nombre": 'Gris', "g-color": 'gris melange',
                'g-piezas': 'usa el short gris con un buzo corto crop oversize blanco y zapatillas urbanas blancas',
                'g-accesorios': 'gorra negra, aritos chicos plateados, medias blancas cortas, actitud relajada y canchera'},
            'chocolate': {"nombre": 'Chocolate', "g-color": 'marrón chocolate',
                'g-piezas': 'usa el short chocolate con un buzo corto crop oversize color crema y zapatillas urbanas blancas',
                'g-accesorios': 'anteojos de sol marrones, aritos chicos dorados, medias blancas cortas, actitud relajada y canchera'},
            'rojo': {"nombre": 'Rojo', "g-color": 'rojo',
                'g-piezas': 'usa el short rojo con un buzo corto crop oversize blanco y zapatillas urbanas blancas',
                'g-accesorios': 'gorra blanca, aritos chicos dorados, medias blancas cortas, actitud relajada y canchera'},
            'azul': {"nombre": 'Azul', "g-color": 'azul (azul francia)',
                'g-piezas': 'usa el short azul con un buzo corto crop oversize gris melange claro y zapatillas urbanas blancas',
                'g-accesorios': 'aritos chicos plateados, colita alta, medias blancas cortas, actitud relajada y canchera'},
            'violeta': {"nombre": 'Violeta', "g-color": 'violeta',
                'g-piezas': 'usa el short violeta con un buzo corto crop oversize lila claro y zapatillas urbanas blancas',
                'g-accesorios': 'clip de pelo, aritos chicos plateados, medias blancas cortas, actitud relajada y canchera'},
            'lila': {"nombre": 'Lila', "g-color": 'lila (lavanda)',
                'g-piezas': 'usa el short lila con un buzo corto crop oversize blanco y zapatillas urbanas blancas',
                'g-accesorios': 'anteojos de sol finos, aritos chicos plateados, medias blancas cortas, actitud relajada y canchera'},
            'celeste': {"nombre": 'Celeste', "g-color": 'celeste',
                'g-piezas': 'usa el short celeste con un buzo corto crop oversize blanco y zapatillas urbanas blancas con detalles celestes',
                'g-accesorios': 'gorra blanca, aritos chicos plateados, medias blancas cortas, actitud relajada y canchera'},
        },
        "poses": [
            {"texto": "De espaldas a cámara, estirándose con los brazos cruzados detrás de la cabeza como al terminar de entrenar, peso en una pierna y la cadera apenas corrida, girando un poco la cabeza: se ve la cola lisa, sin frunce",
             "camara": 0, "on": True},
            {"texto": "Apoyada con una mano en la columna de hormigón, la otra acomodándose la colita, una pierna cruzada por delante, sonrisa suave mirando hacia el sol, cuerpo entero con zapatillas",
             "camara": 9, "on": True},
            {"texto": "De perfil caminando despacio frente a la pared, a mitad de paso, una mano bajando por la faja como acomodándola, mirada hacia abajo y sonrisa chiquita: se ve lo alta que llega la cintura y el panal del costado",
             "camara": 11, "on": True},
            {"texto": "De frente, riéndose mientras toma un trago del shaker de proteína, el bolso deportivo en el piso, el buzo crop abierto, peso en una pierna: se ve entera la cintura alta, de la rodilla para arriba",
             "camara": 0, "on": True},
            {"texto": "Sentada en el piso de cemento con la espalda contra la pared, rodillas flexionadas, soplando un café para llevar, relajada, sombras geométricas del sol sobre ella",
             "camara": 13, "on": False},
            {"texto": "Elongando una pierna apoyada en un escalón de cemento, inclinándose hacia adelante con las manos en el muslo, de costado en 3/4, respirando tranquila: el short estira sin transparentar",
             "camara": 2, "on": False},
            {"texto": "Caminando a lo largo de la pared entre los recortes de sol, de perfil, cuerpo entero, la colita moviéndose con el paso",
             "camara": 4, "on": False},
            {"texto": "Bajándose el buzo crop con las dos manos mientras se ríe mirando a un costado, de frente, de la rodilla para arriba: la faja ancha a la vista",
             "camara": 0, "on": False},
            {"texto": "Detalle de la cadera de costado, sin cara, con la mano apoyada suelta en la faja: la faja acanalada y la textura de panal con la costura curva, con el sol rasante marcando la textura",
             "camara": 5, "on": False},
            {"texto": "Primer plano de la cara con una franja de sol sobre los ojos, entrecerrándolos y sonriendo, pared desenfocada",
             "camara": 4, "on": False},
        ],
        # SET DE 3: cada modelo con su color (y el buzo y accesorios de ese color), su pose y su
        # gesto; más la foto grupal. Los colores se cambian en el panel de la escena.
        "set": {
            "n": 3,
            "modelos": [
                {"color": "verde_petroleo", "pose": "espalda",
                 "ind": "de espaldas a cámara, estirándose con los brazos cruzados detrás de la cabeza como al terminar de entrenar, peso en una pierna, girando un poco la cabeza: se ve la cola lisa"},
                {"color": "fucsia", "pose": "perfil",
                 "ind": "de perfil a mitad de paso frente a la pared, una mano acomodando la faja, sonrisa chiquita mirando hacia abajo: se ve lo alta que llega la cintura y el panal del costado"},
                {"color": "lila", "pose": "frente",
                 "ind": "de frente riéndose mientras toma un trago del shaker de proteína, el bolso en el piso, el buzo crop abierto, peso en una pierna"},
            ],
            "piezas": "el short con un buzo corto crop oversize y zapatillas urbanas",
            "accesorios": "medias blancas cortas, actitud relajada y canchera",
            "grupal": ("las tres juntas contra la pared de hormigón al sol, charlando y riéndose después de "
                       "entrenar: una apoyada en la columna, otra con el shaker, otra acomodándose la colita, "
                       "cuerpos sueltos y naturales, las sombras geométricas del sol cruzándolas, cuerpo entero"),
        },
    },
    "malla_hombre_costa_italiana": {
        "nombre": "Malla short de hombre · costa italiana y velero (película)",
        "campos": {
            "g-temporada": "verano",
            "g-genero": "hombre",
            "g-fondo": ("costa rocosa de Amalfi o Capri: rocas claras, escaleritas de metal al mar, "
                        "acantilados con casitas pastel, mar turquesa transparente y veleros de madera. "
                        "Siempre AL AIRE LIBRE (rocas, agua o velero), nunca bajo techo ni en pasillos; cada toma "
                        "con un fondo distinto"),
            "g-luz": ("sol directo y cálido de las 5 de la tarde, como en una película de verano de los 60: "
                      "sombras marcadas pero suaves en los bordes, piel bronceada mojada, destellos en el mar; "
                      "nada de luz nublada ni de look HDR"),
            "g-style": "vintage_suave",
            "g-piezas": "usa la malla con el torso descubierto y descalzo",
            "g-aclaraciones": ("malla short de baño de HOMBRE (no bóxer): cintura elástica con cordón "
                               "blanco y dos ojalillos metálicos, UN bolsillo a cada costado y UNO atrás, largo "
                               "a mitad del muslo, forro de red blanca; la estampa es la de la foto. FOTO SIN "
                               "POSE: fotograma de película, él en plena acción y distraído, casi no mira a cámara"),
            "g-accesorios": "anteojos de sol retro, reloj de acero, actitud relajada de vacaciones",
            "g-peinado": "",
            "g-foco": "desenfocado",
            "g-encuadre-zona": "",
            "g-lente": "documental_35",
            "g-camara-foto": "portra",
            "g-movimiento": "auto",
        },
        "colores": {
            "celeste_turquesa": {"nombre": "Palmeras celestes sobre turquesa",
                "g-color": "estampa de palmeras celeste claro sobre fondo turquesa que se oscurece hacia abajo a azul petróleo, con franjas de puntitos",
                "g-piezas": "usa la malla con el torso descubierto y descalzo",
                "g-accesorios": "sombrero de paja en la mano, anteojos de sol retro, actitud relajada de vacaciones"},
            "azul_francia": {"nombre": "Palmeras azul francia sobre celeste",
                "g-color": "estampa de palmeras azul francia sobre fondo celeste aqua, con franjas de lunares azules",
                "g-piezas": "usa la malla con el torso descubierto y descalzo",
                "g-accesorios": "anteojos de sol retro de carey, reloj de acero, actitud relajada de vacaciones"},
            "negro_azul": {"nombre": "Palmeras negras sobre azul",
                "g-color": "estampa de palmeras negras sobre fondo celeste que se oscurece hacia abajo a azul marino, con franjas de puntitos",
                "g-piezas": "usa la malla con una camisa de lino blanca abierta y descalzo",
                "g-accesorios": "cadenita fina plateada, anteojos de sol negros, actitud relajada de vacaciones"},
            "verde": {"nombre": "Palmeras azules sobre verde",
                "g-color": "estampa de palmeras azul marino sobre fondo verde esmeralda que se aclara a verde agua arriba, con franjas de puntitos",
                "g-piezas": "usa la malla con el torso descubierto y descalzo",
                "g-accesorios": "gorra de tela clara, toalla de rayas al hombro, actitud relajada de vacaciones"},
        },
        "poses": [
            {"texto": "Caminando descalzo por las rocas calientes hacia el mar, a mitad de paso y mirando dónde pisa, de espaldas en 3/4; de fondo el mar abierto y el horizonte: se ve el bolsillo de atrás",
             "camara": 4, "on": True},
            {"texto": "Saliendo del agua por la escalerita de metal, sacudiéndose el pelo mojado con una mano mientras se ríe con alguien fuera de cuadro; de fondo el pueblo de casitas pastel en el acantilado",
             "camara": 9, "on": True},
            {"texto": "Tirándose de cabeza desde la roca al agua turquesa, congelado en el aire con el cuerpo estirado; de fondo el acantilado y el cielo",
             "camara": 12, "on": True},
            {"texto": "Sentado al borde de la plataforma de roca secándose la cara con una toalla, una pierna colgando sobre el agua, de costado; de fondo un velero de madera de cerca: se ve el bolsillo del costado",
             "camara": 2, "on": True},
            {"texto": "En la cubierta de un velero de madera navegando, tirando de un cabo con las dos manos, el viento en el pelo, inclinado por el esfuerzo; de fondo el mar y la costa lejos",
             "camara": 10, "on": False},
            {"texto": "Acostado boca arriba en la cubierta del velero, dormido al sol con un brazo sobre los ojos, tomado desde arriba",
             "camara": 3, "on": False},
            {"texto": "Flotando boca arriba en el agua transparente, se ven las piedras del fondo y la malla bajo el agua, tomado desde arriba de la roca",
             "camara": 8, "on": False},
            {"texto": "Caminando por una escalera de piedra del pueblo con una toalla al hombro, comiéndose un durazno, de perfil; de fondo paredes pastel y buganvilias",
             "camara": 11, "on": False},
            {"texto": "Detalle de la cintura con el cordón blanco y los ojalillos, y el bolsillo del costado, con gotas de agua y sombra dura del sol, sin cara",
             "camara": 5, "on": False},
            {"texto": "Primer plano de la cara mojada entrecerrando los ojos al sol, riéndose, con el mar desenfocado y brillando atrás",
             "camara": 4, "on": False},
        ],
    },
    "malla_enteriza_desierto_emiratos": {
        "nombre": "Malla enteriza con apliques dorados · desierto de Emiratos (lujo)",
        "campos": {
            "g-temporada": "verano",
            "g-genero": "mujer",
            "g-fondo": ("desierto de Emiratos Árabes: médanos gigantes de arena naranja cobriza y dorada que "
                        "forman curvas suaves y elegantes hasta el horizonte, pendientes pronunciadas, crestas "
                        "bien definidas y sombras largas que dibujan texturas naturales; cielo despejado beige "
                        "cálido; minimalista, exclusivo y cinematográfico, campaña de moda de lujo internacional. "
                        "Sin vegetación, edificios, vehículos ni elementos turísticos"),
            "g-luz": ("última hora de la tarde: sol lateral a 45°, cálido y direccional, reflejos ámbar sobre la "
                      "arena, contraste entre zonas iluminadas y sombras profundas, rebote dorado de la arena sobre "
                      "la piel, altas luces controladas, contraste cinematográfico; paleta naranja quemado, cobre, "
                      "ámbar y dorado con tonos de piel naturales; rango dinámico amplio, sin efecto HDR ni "
                      "apariencia de imagen generada por IA"),
            "g-style": "editorial",
            "g-piezas": "usa la malla",
            "g-aclaraciones": ("malla ENTERIZA con recortes: escote corazón con fruncido y nudo al frente, breteles "
                               "finos con eslabones dorados, DOS recortes en el abdomen separados por bandas, TRES "
                               "apliques dorados en forma de caracola (busto, centro y abajo), espalda abierta con lazo "
                               "atado a media espalda, tela texturada con relieve tipo trenzado fino y leve brillo satinado, taza con esponja, cavado alto. Foco preciso "
                               "y máxima definición en la textura de la tela, los recortes y los apliques dorados"),
            "g-accesorios": "joyas de oro, actitud de campaña de lujo",
            "g-peinado": "",
            "g-foco": "desenfocado",
            "g-encuadre-zona": "",
            "g-lente": "auto",
            "g-camara-foto": "hasselblad",
            "g-movimiento": "auto",
            "g-ajustes": ("obturación 1/500 s para congelar el pelo y el movimiento; ISO 100-200, imagen limpia "
                          "y con mucho detalle; balance de blancos 5.500-6.000 K: arena naranja sin que la piel "
                          "se vuelva rojiza; sombras marcadas que conservan detalle en las zonas oscuras; "
                          "profundidad de campo MODERADA: el fondo apenas desenfocado, sin perder la "
                          "majestuosidad ni las formas de las dunas"),
        },
        # El pareo (salida larga tipo camisa) NO va en todas las tomas: sólo en las que su pose dice
        # "pareo"; el resto sale con la malla sola y sus accesorios.
        "colores": {
            "bordo": {"nombre": "Bordó", "g-color": "bordó (vino profundo)",
                "g-piezas": "usa la malla",
                "g-pareo": "un pareo largo hasta los tobillos tipo camisa de seda color champagne, abierto adelante, mangas largas sueltas, flotando con el viento",
                "g-accesorios": "vincha de raso bordó, aros dorados grandes de caracola, brazaletes de oro apilados, clutch de rafia dorada en la mano, descalza sobre la arena"},
            "blanco": {"nombre": "Blanco", "g-color": "blanco",
                "g-piezas": "usa la malla",
                "g-pareo": "un pareo largo hasta los tobillos tipo camisa de gasa blanca transparente, abierto adelante, mangas amplias, flotando con el viento",
                "g-accesorios": "vincha de raso dorada, collar de caracolas de oro, aros dorados en forma de sol, brazalete rígido de oro, sandalias doradas finas"},
            "azul": {"nombre": "Azul francia", "g-color": "azul francia (azul eléctrico intenso)",
                "g-piezas": "usa la malla",
                "g-pareo": "un pareo largo hasta los tobillos tipo camisa de seda blanca con ribetes dorados, abierto adelante, flotando con el viento",
                "g-accesorios": "vincha de raso azul francia, aros dorados largos, anteojos de sol cat-eye con marco dorado, clutch de metal dorado, pulseras finas de oro, descalza"},
            "avellana": {"nombre": "Marrón avellana", "g-color": "marrón avellana (marrón tostado cálido, con leve brillo satinado; NO verde ni caqui)",
                "g-piezas": "usa la malla",
                "g-pareo": "un pareo largo hasta los tobillos tipo camisa de lino color arena, abierto adelante, mangas arremangadas, flotando con el viento",
                "g-accesorios": "vincha de raso color arena, cinturón fino de cadena dorada en la cintura, argollas de oro grandes, cartera de rafia con asa de bambú, sandalias de cuero camel"},
            "negro": {"nombre": "Negro", "g-color": "negro",
                "g-piezas": "usa la malla",
                "g-pareo": "un pareo largo hasta los tobillos tipo camisa de chiffon negro semitransparente, abierto adelante, mangas amplias, flotando con el viento",
                "g-accesorios": "vincha de raso negra, choker de oro, brazalete rígido de oro ancho, aros dorados escultóricos, anteojos de sol negros, descalza"},
            "fucsia": {"nombre": "Fucsia", "g-color": "fucsia",
                "g-piezas": "usa la malla",
                "g-pareo": "un pareo largo hasta los tobillos tipo camisa de seda fucsia y naranja, abierto adelante, flotando con el viento",
                "g-accesorios": "vincha de raso fucsia, aros dorados con piedras, brazaletes de oro apilados, cartera baguette dorada, anteojos de sol con marco dorado, descalza"},
        },
        # Las poses las elige ella: la escena trae lugar, luz, cámara, color y accesorios.
        "poses": [],
    },
    "malla_nene_patio_pileta": {
        "nombre": "Malla short de nene · patio con pileta vintage (Portra)",
        "campos": {
            "g-temporada": "kids",
            "kids-quien": "nene",
            "kids-talle": "6-8",
            "g-fondo": ("patio de una casa de verano vintage costera de los 70, rodeado de vegetación tropical: "
                        "bananeros, palmeras y helechos. Pileta rectangular de material celeste y turquesa con borde "
                        "de piedra clara gastada y un trampolín clásico en un extremo. A un lado una pérgola rústica "
                        "de cañas de bambú con enredaderas; al otro, un solárium de piedra y baldosas terracota con "
                        "reposeras tubulares vintage y toallas a rayas azules y blancas. Atrás, la casa de revoque "
                        "blanco con aberturas de madera y senderos de piedra entre las plantas"),
            "g-luz": ("tarde de verano, sol cálido lateral filtrado entre las hojas y el bambú, sombras orgánicas en "
                      "el piso, reflejos del agua en la piedra; contraste moderado"),
            "g-style": "vintage_suave",
            "g-piezas": "malla short de baño de nene",
            "g-aclaraciones": ("malla short de baño de NENE, tela liviana de secado rápido, cintura elástica fruncida "
                               "con cordón blanco y dos ojalillos metálicos, largo a mitad del muslo; la ESTAMPA es "
                               "EXACTA la de la foto (dibujo, tamaño y colores). Foto espontánea de un chico jugando, "
                               "nada posado"),
            "g-accesorios": "descalzo",
            "g-peinado": "",
            "g-foco": "desenfocado",
            "g-encuadre-zona": "",
            "g-lente": "auto",
            "g-camara-foto": "portra",
            "g-movimiento": "auto",
            "g-ajustes": ("obturación 1/500 s para congelar el movimiento y las gotas de agua; profundidad de campo "
                          "moderada que deja reconocer el patio"),
        },
        "colores": {
            "celeste_paltas": {"nombre": "Paltas sobre celeste",
                "g-color": "estampa de paltas ilustradas (verde lima, blanco, carozo azul marino o naranja) y hojas azul marino sobre fondo celeste",
                "g-piezas": "malla short de baño de nene con una camisa de lino blanca abierta, mangas arremangadas",
                "g-accesorios": "anteojos de sol redondos blancos, descalzo"},
            "negro_frutas": {"nombre": "Frutas sobre negro (sandía, ananá, banana)",
                "g-color": "estampa de sandías rojas, ananás naranjas con hojas verdes, bananas amarillas y cerezas rojas sobre fondo negro",
                "g-piezas": "malla short de baño de nene con una remera de algodón blanca lisa",
                "g-accesorios": "gorro piluso de tela blanco, descalzo"},
            "negro_paltas": {"nombre": "Paltas sobre negro",
                "g-color": "estampa de mitades de palta verde lima con carozo marrón rojizo y rayitas blancas sobre fondo negro; bolsillo atrás",
                "g-piezas": "malla short de baño de nene con una camisa de lino verde oliva abierta",
                "g-accesorios": "anteojos de sol de carey, toalla a rayas al hombro, descalzo"},
            "azul_limones": {"nombre": "Limones sobre azul",
                "g-color": "estampa de limones amarillo pastel y durazno con hojas verde agua y azahares blancos sobre fondo azul francia",
                "g-piezas": "malla short de baño de nene con una camisa de lino blanca abierta",
                "g-accesorios": "flotador inflable color crema en la mano, descalzo"},
            "verde_pepinos": {"nombre": "Pepinitos con anteojos sobre verde",
                "g-color": "estampa de pepinitos verde claro con anteojitos amarillos sobre fondo verde botella con puntitos amarillos",
                "g-piezas": "malla short de baño de nene con una remera de algodón amarilla pastel",
                "g-accesorios": "anteojos de sol amarillos, pelota de playa en la mano, descalzo"},
        },
        # Las poses son ACCIONES, sin decir el lugar: así cada toma cae en otro rincón del patio.
        "poses": [
            {"texto": "Corriendo a toda velocidad, a mitad de zancada, riéndose, los brazos sueltos en el aire", "camara": 4, "on": True},
            {"texto": "Saltando con las rodillas dobladas y los brazos arriba, congelado en el aire, salpicaduras", "camara": 1, "on": True},
            {"texto": "Sentado con las piernas cruzadas comiendo una rodaja de sandía, distraído mirando a un costado", "camara": 13, "on": True},
            {"texto": "Caminando de espaldas a cámara con la toalla arrastrando, mirando por encima del hombro: se ve la parte de atrás de la malla", "camara": 9, "on": True},
            {"texto": "Agachado mirando con curiosidad un bichito, de costado", "camara": 2, "on": False},
            {"texto": "Sacudiéndose el pelo mojado con las dos manos, los ojos cerrados y riéndose", "camara": 0, "on": False},
            {"texto": "Acostado boca arriba con los brazos abiertos tomando sol, tomado desde arriba", "camara": 8, "on": False},
            {"texto": "Tirándose de bomba al agua, congelado en el aire abrazando las rodillas", "camara": 12, "on": False},
            {"texto": "Detalle de la estampa y el cordón de la malla, sin cara, con gotas de agua", "camara": 5, "on": False},
        ],
    },
}
ESCENAS_JS = json.dumps(ESCENAS_LISTAS, ensure_ascii=False).replace("</", "<\\/")
ESCENAS_OPTS_HTML = "".join(f'<option value="{k}">{v["nombre"]}</option>'
                            for k, v in ESCENAS_LISTAS.items())

HTML_PAGE = (HTML_PAGE.replace("%%PREFIX%%", ROUTE_PREFIX)
             .replace("%%ESCENASOPTS%%", ESCENAS_OPTS_HTML)
             .replace("%%ESCENAS%%", ESCENAS_JS)
             .replace("%%ESTAVOPTS%%", EST_AV_OPTS_HTML)
             .replace("%%ESTAVAYUDA%%", EST_AV_AYUDA_JS)
             .replace("%%CAMAYUDA%%", CAM_AYUDA_HTML)
             .replace("%%CAMOPTS%%", CAM_OPTS_HTML)
             .replace("%%MANUALJS%%", json.dumps({
                 "vistas": [[k, v[0]] for k, v in VISTAS_POSE.items()],
                 "camaras": [[i, c["lbl"]] for i, c in enumerate(CAMARA_POOL)],
                 "planos": [[i, c["lbl"]] for i, c in enumerate(PLANO_POOL)],
                 "lentes": [[k, v["lbl"]] for k, v in LENTES_FOTO.items()]}, ensure_ascii=False))
             .replace("%%LENTEOPTS%%", _opts_html(LENTES_FOTO, "(como siempre)"))
             .replace("%%CAMFOTOOPTS%%", _opts_html(CAMARAS_FOTO, "(como siempre)"))
             .replace("%%MOVOPTS%%", _opts_html(MOVIMIENTOS_FOTO, "(quieta, como siempre)"))
             .replace("%%VERSION%%", VERSION)
             .replace("%%VIDEOS%%", os.environ.get("VIDEOS_PREFIX", "/videos"))
             .replace("%%COMERCIALES%%", os.environ.get("COMERCIALES_PREFIX", "/comerciales"))
             .replace("%%PERSONAJES%%", os.environ.get("PERSONAJES_PREFIX", "/personajes")))


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
