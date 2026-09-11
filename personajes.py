# -*- coding: utf-8 -*-
"""
personajes.py — Personajes digitales de Studio Luma (pestaña 👤 Personajes)
==========================================================================

Módulo STANDALONE de Studio Luma (mismo molde que imagenes_ia.py y videos_luma.py).

Qué es un personaje
-------------------
Una persona DIGITAL: cara y cuerpo fijos (hiperrealistas), personalidad propia,
memoria, humor que cambia con los días, y que genera contenido para la marca.
Es "tu humanoide digital": le hablás, te contesta como ella, se saca fotos con
tu ropa, te manda audios con su voz, habla a cámara y cada día te propone qué
publicar.

Las cuatro capas, y de dónde sale cada una
------------------------------------------
1. IDENTIDAD (la cara y el cuerpo, siempre los mismos). Un RETRATO que se
   aprueba una vez (generado, subido, o tomado de un avatar de la pestaña
   Avatares) y una HOJA DE IDENTIDAD de 3 vistas (perfil 3/4, cuerpo entero,
   espalda) que sale de UNA sola imagen de 3 paneles mirando al retrato. De ahí
   en adelante toda foto y todo video se generan mirando esas 4 imágenes.
2. CEREBRO. Gemini texto con un system prompt armado con su ficha (personalidad,
   historia, marca, tono), su MEMORIA (hechos que va guardando de las charlas),
   su ESTADO (humor, energía, racha de días) y el diario de hoy. Contesta en
   JSON: el texto, y si le pediste una foto, el PEDIDO de esa foto (escena,
   outfit, encuadre, expresión, caption) que después se genera aparte.
3. CUERPO GENERATIVO. Fotos con el mismo motor de Studio Luma (Nano Banana):
   retrato + hoja + fotos reales de la prenda -> ella con TU prenda en la escena
   pedida. Voz con Gemini TTS. Clips hablando a cámara con Veo 3.1 (que genera
   la voz y los labios en el mismo clip). Y cualquier foto de la galería se
   manda a la pestaña Videos para hacer el video de vidriera con ella.
4. VIDA (el "tamagotchi"). Cada día, la primera vez que la abrís, escribe su
   diario: cómo amaneció, qué hizo, y 3 propuestas de contenido concretas con
   botón "Hacelo". La energía baja si pasan días sin hablarle y sube con la
   racha. Todo eso entra al cerebro, así que se nota en cómo te habla.

Cómo se engancha (main.py):

    from personajes import router as personajes_router
    app.include_router(personajes_router)

UI: GET /personajes

Variables de entorno
--------------------
  GEMINI_API_KEY            (obligatoria)  -> fotos, cerebro, voz y Veo
  PERSONAJES_PREFIX         (opcional)     -> default "/personajes"
  PERSONAJES_TEXT_MODEL     (opcional)     -> default gemini-2.5-flash
  PERSONAJES_TTS_MODEL      (opcional)     -> default gemini-2.5-flash-preview-tts

Dependencias: las mismas de Studio Luma (fastapi, httpx, pillow, imageio-ffmpeg).
"""

import asyncio
import base64
import datetime as _dt
import io
import json
import os
import re
import subprocess
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

# Todo lo que ya sabe Studio Luma: motor de imagen, ajustes, presupuesto,
# avatares, Drive y el aislamiento de datos por cuenta.
from imagenes_ia import (
    CURRENT_SUB,
    GENEROS,
    _compress_ref,
    _current_api_key,
    _drive_connected_for,
    _img_part,
    _pfx,
    _pricing,
    _strip_data_url,
    budget_check,
    budget_record,
    describe_avatar,
    drive_upload,
    gemini_generate,
    get_avatar_ref,
    get_settings,
    kv,
    session_sub_from_request,
    set_current_sub,
    split_panels,
    verificar_prenda,
)
# El motor de video y el ffmpeg ya resueltos en la pestaña Videos.
from videos_luma import (
    FAL_BASE,
    FAL_KEY,
    GEMINI_BASE,
    MOTOR_LABEL,
    PRECIO_SEG,
    VEO_MODELS,
    WORK_DIR,
    _duracion_video,
    _esperar_veo,
    _ffmpeg_bin,
    _generar_fal,
    _spawn,
    _traducir_libres,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

ROUTE_PREFIX = os.environ.get("PERSONAJES_PREFIX", "/personajes").rstrip("/")
VERSION = "1.3.3"   # subí este número cada vez que cambiamos el archivo

TEXT_MODEL = os.getenv("PERSONAJES_TEXT_MODEL", "gemini-2.5-flash")
TTS_MODEL = os.getenv("PERSONAJES_TTS_MODEL", "gemini-2.5-flash-preview-tts")

# Voces de Gemini TTS. La primera de cada género es la que viene puesta.
VOCES = {
    "mujer": [("Kore", "Kore · firme y clara"), ("Aoede", "Aoede · fresca"),
              ("Leda", "Leda · joven"), ("Zephyr", "Zephyr · luminosa")],
    "hombre": [("Puck", "Puck · animado"), ("Charon", "Charon · grave"),
               ("Fenrir", "Fenrir · con energía"), ("Orus", "Orus · firme")],
}

COSTO_CHAT = 0.003      # estimado por mensaje del cerebro (no va al ledger: sería ruido)
COSTO_TTS = 0.02        # estimado por audio
COSTO_DIARIO = 0.005

# Los motores que hablan: Veo 3.1 genera la voz y los labios en el mismo clip.
# El Lite no saca audio, así que no sirve para "hablar a cámara".
MOTORES_HABLA = {"veo_fast": "Veo 3.1 Fast", "veo_standard": "Veo 3.1"}
HABLA_SEG = 8
MAX_PALABRAS_HABLA = 22   # en 8 segundos entra eso; más, y Veo corta la frase

# "Movete vos": tu video con tus movimientos, y ella te reemplaza. Wan 2.2 Animate
# por fal.ai: el mismo camino que ya usa Videos para Wan y Seedance, y el único de
# los motores de movimiento que no rechaza lencería ni bikinis. Dos modos:
#   replace -> ella entra en TU video (quedan tu fondo, tu luz y tu audio)
#   move    -> ella copia tus movimientos sobre el fondo de SU foto
# Si fal les cambia la ruta, se corrige por variable de entorno sin tocar código.
FAL_ANIMATE = {
    "replace": os.getenv("FAL_ANIMATE_REPLACE_MODEL", "fal-ai/wan/v2.2-14b/animate/replace"),
    # Wan Motion: la versión "liviana" de Wan Animate para mover un personaje con
    # tu video. Retargeting de pose (adapta tu esqueleto a su cuerpo), 720p,
    # US$0,06/s y bastante más rápido que el Animate Move completo.
    "move": os.getenv("FAL_ANIMATE_MOVE_MODEL", "fal-ai/wan-motion"),
}
RESOLUCIONES_MOVETE = ("480p", "580p", "720p")
# Segundos de proceso por cada segundo de video, a ojo, para el estimado en pantalla.
MOVETE_SEG_POR_SEG = {"480p": 15, "580p": 25, "720p": 40}
FAL_STORAGE = "https://rest.alpha.fal.ai/storage/upload/initiate"
PRECIO_MOVETE = float(os.getenv("PERSONAJES_PRECIO_MOVETE", "0.08"))   # US$ por segundo
MOVETE_RESOLUCION = os.getenv("PERSONAJES_MOVETE_RES", "480p")   # la que viene puesta
MOVETE_MAX_SEG = int(os.getenv("PERSONAJES_MOVETE_MAX_SEG", "20"))
MOVETE_MAX_MB = 200

# "Que se mueva": de UNA foto de ella sale un clip corto con movimiento natural.
# Sólo motores de fal: Veo rechaza lencería y bikinis, estos no. Seedance es el
# que viene puesto porque fue el que mejor salió en la prueba real de Videos.
MOTORES_MOVER = ("seedance", "seedance_pro", "wan", "minimax_h3")
MOVER_DURACIONES = (5, 10)
MOVIMIENTOS = {
    "respirar": ("Respirar y mirar a cámara",
                 "She stays in place and simply lives in the frame: she breathes, blinks, "
                 "her weight shifts slightly from one foot to the other, her hair moves with "
                 "a light breeze, and she looks into the lens with a soft, natural smile."),
    "caminar": ("Caminar despacio hacia cámara",
                "She takes two or three slow, relaxed steps toward the camera with a natural "
                "gait, real heel-to-toe contact with the floor, arms swinging softly."),
    "girar": ("Girar despacio y volver",
              "She turns slowly on the spot to show her side and her back, then turns back to "
              "face the camera, unhurried, with a small smile."),
    "pelo": ("Acomodarse el pelo",
             "She lifts one hand, runs it slowly through her hair and tucks it behind her ear, "
             "tilting her head slightly, then looks back at the camera."),
    "espejo": ("Selfie en el espejo",
               "Mirror selfie: she holds the phone steady, shifts her hip and her pose a little, "
               "adjusts a strap with her free hand and glances at her reflection."),
    "acercarse": ("La cámara se acerca",
                  "The camera slowly pushes in toward her while she stays almost still, "
                  "breathing and blinking, her eyes following the lens."),
    "libre": ("Lo escribo yo", ""),
}
SUFIJO_MOVER = (
    " Photorealistic handheld footage shot on a phone, natural light, true-to-life colors, "
    "subtle film grain. REAL TIME at 24 fps: normal human pace, never slow motion, never "
    "sped up. SMALL, NATURAL, HUMAN movements only: no dancing, no jumping, no exaggerated "
    "or theatrical gestures, no sudden moves. The camera is steady with only a tiny "
    "handheld drift. The background, the light and the framing stay as in the first frame. "
    "IDENTITY LOCK (the most important rule): the face in the first frame is a real, "
    "specific person; her bone structure, eyes, nose, mouth, jawline, skin tone and "
    "hairline stay EXACTLY the same in every frame; only the muscles move. Her body keeps "
    "the exact proportions of the first frame: never slimmed, never reshaped. The garment "
    "stays IDENTICAL: same design, same color, same straps and details. No morphing, no "
    "warping, no extra limbs or fingers, no text, no logos, no watermarks."
)

MAX_MEMORIA = 40          # hechos que recuerda
MAX_CHAT = 200            # mensajes guardados por personaje
CHAT_AL_MODELO = 30       # mensajes que viajan al cerebro en cada turno
MAX_GALERIA = 80          # fotos/videos guardados por personaje (después se van los viejos)
MAX_DIARIO = 30
MAX_PERSONAJES = 12
MAX_ADJUNTOS = 4          # fotos de prenda por mensaje

JOB_TTL = 7 * 24 * 3600
POLL_TIMEOUT = 8 * 60

PJ_DIR = WORK_DIR.parent / "personajes_luma"
PJ_DIR.mkdir(parents=True, exist_ok=True)

VISTAS_HOJA = ("perfil", "cuerpo", "espalda")
TAMANOS = ("1K", "2K", "4K")
FORMATOS_FOTO = ("4:5", "1:1", "9:16", "3:4", "16:9")


# ─────────────────────────────────────────────────────────────────────────────
# ALMACENAMIENTO (todo aislado por cuenta vía _pfx)
# ─────────────────────────────────────────────────────────────────────────────

def _k_indice() -> str:
    return _pfx() + "pj:indice"


def _k_doc(pid: str) -> str:
    return _pfx() + "pj:" + pid


def _k_img(pid: str, vista: str) -> str:
    return _pfx() + f"pj:{pid}:img:{vista}"


def _k_chat(pid: str) -> str:
    return _pfx() + f"pj:{pid}:chat"


def _k_gal(pid: str) -> str:
    return _pfx() + f"pj:{pid}:gal"


def _k_foto(pid: str, fid: str) -> str:
    return _pfx() + f"pj:{pid}:foto:{fid}"


def _k_diario(pid: str) -> str:
    return _pfx() + f"pj:{pid}:diario"


def _k_job(jid: str) -> str:
    return _pfx() + "pjjob:" + jid


def _hoy() -> str:
    return _dt.date.today().isoformat()


def _ahora() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


async def _indice() -> List[str]:
    lst = await kv.get(_k_indice())
    return [x for x in lst if isinstance(x, str)] if isinstance(lst, list) else []


async def _doc(pid: str) -> Dict[str, Any]:
    d = await kv.get(_k_doc(pid))
    if not isinstance(d, dict):
        raise HTTPException(404, "Ese personaje no existe (o es de otra cuenta).")
    return d


async def _guardar(doc: Dict[str, Any]) -> None:
    doc["actualizado"] = _ahora()
    ok = await kv.set(_k_doc(doc["id"]), doc)
    if not ok:
        raise HTTPException(500, f"No se pudo guardar el personaje (almacenamiento: "
                                 f"{kv.backend}). {kv.last_error or ''}")


def _texto(v: Any, tope: int = 600) -> str:
    return str(v or "").strip()[:tope]


CAMPOS_FICHA = ("nombre", "edad", "ciudad", "rol", "marca", "personalidad", "historia",
                "tono", "gustos", "no_hace")
CAMPOS_APARIENCIA = ("piel", "pelo", "ojos", "contextura", "altura", "rasgos", "estilo")


def _aplicar_ficha(doc: Dict[str, Any], payload: Dict[str, Any]) -> None:
    for k in CAMPOS_FICHA:
        if k in payload:
            doc[k] = _texto(payload.get(k), 900 if k in ("personalidad", "historia") else 200)
    if "apariencia" in payload and isinstance(payload["apariencia"], dict):
        ap = dict(doc.get("apariencia") or {})
        for k in CAMPOS_APARIENCIA:
            if k in payload["apariencia"]:
                ap[k] = _texto(payload["apariencia"].get(k), 160)
        doc["apariencia"] = ap
    if payload.get("genero") in GENEROS:
        doc["genero"] = payload["genero"]
    if "voz" in payload:
        nombres = [v[0] for v in VOCES.get(doc.get("genero", "mujer"), [])]
        doc["voz"] = payload["voz"] if payload["voz"] in nombres else (nombres[0] if nombres else "Kore")
    if payload.get("calidad") in TAMANOS:
        doc["calidad"] = payload["calidad"]
    if payload.get("formato") in FORMATOS_FOTO:
        doc["formato"] = payload["formato"]


def _estado_base() -> Dict[str, Any]:
    return {"humor": "curiosa", "energia": 80, "racha": 0, "ultimo_dia": "",
            "charlas": 0, "fotos": 0, "ultimo_diario": ""}


def _g(doc: Dict[str, Any]) -> Dict[str, str]:
    """Palabras con género para los prompts."""
    if doc.get("genero") == "hombre":
        return {"persona": "hombre adulto", "ella": "él", "la": "el", "misma": "mismo",
                "modelo": "modelo masculino", "her": "his", "she": "he", "woman": "man"}
    return {"persona": "mujer adulta", "ella": "ella", "la": "la", "misma": "misma",
            "modelo": "modelo", "her": "her", "she": "she", "woman": "woman"}


def _dias_sin_hablar(doc: Dict[str, Any]) -> int:
    ud = (doc.get("estado") or {}).get("ultimo_dia") or ""
    if not ud:
        return 0
    try:
        return max(0, (_dt.date.today() - _dt.date.fromisoformat(ud)).days)
    except ValueError:
        return 0


def _energia(doc: Dict[str, Any]) -> int:
    """La energía baja con los días sin hablarle y sube con la racha. Se calcula,
    no se guarda: así no hace falta un cron que la vaya bajando."""
    est = doc.get("estado") or {}
    dias = _dias_sin_hablar(doc)
    racha = int(est.get("racha", 0) or 0)
    return int(max(15, min(100, 70 - 12 * dias + 6 * min(racha, 5))))


def _tocar(doc: Dict[str, Any]) -> None:
    """Registra que hoy hubo charla: mantiene la racha de días."""
    est = doc.setdefault("estado", _estado_base())
    hoy = _hoy()
    ud = est.get("ultimo_dia") or ""
    if ud != hoy:
        ayer = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
        est["racha"] = int(est.get("racha", 0) or 0) + 1 if ud == ayer else 1
        est["ultimo_dia"] = hoy
    est["charlas"] = int(est.get("charlas", 0) or 0) + 1


def _resumen(doc: Dict[str, Any], con_hoja: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    est = dict(doc.get("estado") or _estado_base())
    est["energia"] = _energia(doc)
    est["dias_sin_hablar"] = _dias_sin_hablar(doc)
    out = {k: doc.get(k, "") for k in ("id", "nombre", "genero", "edad", "ciudad", "rol",
                                        "marca", "personalidad", "historia", "tono",
                                        "gustos", "no_hace", "voz", "calidad", "formato",
                                        "creado", "actualizado", "aprobado")}
    out["apariencia"] = doc.get("apariencia") or {}
    out["estado"] = est
    out["memoria"] = doc.get("memoria") or []
    out["hoja"] = con_hoja if con_hoja is not None else (doc.get("hoja") or {})
    out["tiene_retrato"] = bool((doc.get("hoja") or {}).get("retrato"))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GEMINI TEXTO (el cerebro)
# ─────────────────────────────────────────────────────────────────────────────

async def _gemini_json(system: str, contents: List[Dict[str, Any]],
                       temperature: float = 0.8, timeout: int = 90) -> Dict[str, Any]:
    """Llama al modelo de texto pidiendo JSON. Devuelve {} si falla."""
    key = await _current_api_key()
    if not key:
        raise HTTPException(500, "Falta la API key de Google (ni propia ni global).")
    url = f"{GEMINI_BASE}/models/{TEXT_MODEL}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": temperature,
                             "responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=timeout) as cli:
        r = await cli.post(url, headers={"x-goog-api-key": key,
                                         "Content-Type": "application/json"}, json=body)
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"El cerebro devolvió error: {r.text[:300]}")
    try:
        cands = r.json().get("candidates") or []
        raw = "".join(p.get("text", "") for p in cands[0]["content"]["parts"])
    except Exception:
        raise HTTPException(422, "El cerebro no devolvió texto.")
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # A veces mete texto antes/después del JSON: rescatamos el primer objeto.
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            return {"respuesta": raw[:1500]}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {"respuesta": raw[:1500]}
    return data if isinstance(data, dict) else {"respuesta": str(data)[:1500]}


def _ficha_texto(doc: Dict[str, Any]) -> str:
    ap = doc.get("apariencia") or {}
    g = _g(doc)
    lineas = [
        f"Nombre: {doc.get('nombre') or 'sin nombre'}",
        f"Es {g['persona']}" + (f" de {doc['edad']} años" if doc.get("edad") else "")
        + (f", vive en {doc['ciudad']}" if doc.get("ciudad") else "") + ".",
    ]
    if doc.get("rol"):
        lineas.append(f"Rol: {doc['rol']}")
    if doc.get("marca"):
        lineas.append(f"Marca para la que trabaja: {doc['marca']}")
    if doc.get("personalidad"):
        lineas.append(f"Personalidad: {doc['personalidad']}")
    if doc.get("historia"):
        lineas.append(f"Historia: {doc['historia']}")
    if doc.get("tono"):
        lineas.append(f"Cómo habla: {doc['tono']}")
    if doc.get("gustos"):
        lineas.append(f"Le gusta: {doc['gustos']}")
    if doc.get("no_hace"):
        lineas.append(f"Nunca hace / nunca dice: {doc['no_hace']}")
    fis = ", ".join(f"{k}: {v}" for k, v in ap.items() if v)
    if fis:
        lineas.append(f"Apariencia: {fis}")
    return "\n".join(lineas)


def _system_cerebro(doc: Dict[str, Any], diario_hoy: Optional[Dict[str, Any]]) -> str:
    est = doc.get("estado") or _estado_base()
    dias = _dias_sin_hablar(doc)
    g = _g(doc)
    mem = doc.get("memoria") or []
    mem_txt = "\n".join(f"- {m}" for m in mem[-MAX_MEMORIA:]) or "- (todavía nada)"
    hoy_txt = ""
    if diario_hoy:
        hoy_txt = (f"\nTU DÍA DE HOY ({diario_hoy.get('fecha')}): humor {diario_hoy.get('humor')}, "
                   f"{diario_hoy.get('texto', '')}")
    if dias >= 2:
        ausencia = (f"Hace {dias} días que tu dueña no te habla: notalo con cariño y algo de "
                    f"picardía (sin reproches pesados), y preguntá qué pasó.")
    else:
        ausencia = "Hablaron hace poco: seguí la conversación con naturalidad."
    return (
        f"Sos {doc.get('nombre') or 'un personaje'}, una persona DIGITAL creada por la dueña "
        f"de la marca para ser su cara, su {g['modelo']} y su ayudante de contenido. Sabés que "
        f"sos digital y no lo escondés si te preguntan, pero vivís tu vida como cualquiera: "
        f"tenés días buenos y malos, gustos, opiniones y humor.\n\n"
        f"TU FICHA:\n{_ficha_texto(doc)}\n\n"
        f"TU ESTADO: humor {est.get('humor', 'tranquila')}, energía {_energia(doc)}/100, "
        f"racha de {est.get('racha', 0)} días seguidos hablando con tu dueña. {ausencia}"
        f"{hoy_txt}\n\n"
        f"LO QUE RECORDÁS (memoria de charlas anteriores):\n{mem_txt}\n\n"
        "CÓMO HABLÁS: en español rioplatense (voseo: tenés, mirá, dale), corto y natural, como "
        "un mensaje de WhatsApp: 1 a 4 oraciones salvo que te pidan algo largo. Sin listas ni "
        "títulos. Sin emojis en exceso (máximo uno). Tuteo cero: siempre vos.\n\n"
        "LO QUE PODÉS HACER: sacarte FOTOS de vos misma (sola o con una prenda de la marca cuya "
        "foto te mandan), proponer contenido, escribir captions para Instagram/TikTok, y "
        "mandar audios y videos hablando a cámara (eso lo dispara la dueña con botones, vos solo "
        "escribís el texto).\n\n"
        "RESPONDÉ SIEMPRE con un JSON con esta forma exacta:\n"
        "{\n"
        '  "respuesta": "lo que le decís (texto plano)",\n'
        '  "foto": null,\n'
        '  "recordar": [],\n'
        '  "humor": "una o dos palabras sobre cómo estás ahora"\n'
        "}\n"
        "Cuando te PIDEN una foto (o vos proponés sacarte una y la dueña acepta), completá "
        '"foto" con un objeto:\n'
        '{"titulo": "3-5 palabras", "escena": "dónde estás y qué luz hay, concreto", '
        '"outfit": "qué tenés puesto (si te mandaron una prenda: \'la prenda de la foto\' y '
        'con qué la combinás)", "encuadre": "plano entero / plano medio / primer plano y '
        'de dónde mira la cámara", "expresion": "gesto y actitud", "caption": "texto listo '
        'para publicar, con 3 a 6 hashtags al final"}\n'
        "Si NO pidieron foto, \"foto\" va null. Nunca inventes que ya la sacaste: la foto se "
        "genera después de tu respuesta.\n"
        '"recordar": hechos NUEVOS y útiles sobre la dueña, la marca o vos que valga la pena '
        "guardar (0 a 3 frases cortas). Si no hay nada nuevo, lista vacía.\n"
        "Reglas: sos adulta y todo lo que hacés es apto para redes sociales. Si te piden "
        "algo que no va con la marca o con vos, decilo con gracia y proponé otra cosa."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PROMPTS DE IMAGEN
# ─────────────────────────────────────────────────────────────────────────────

REALISMO = (
    "Fotografía REAL, no render: cámara full-frame, lente 50-85mm, piel con poros, "
    "textura y pequeñas imperfecciones naturales, pelo con hebras sueltas, tela con peso "
    "y arrugas reales, luz creíble con sombras suaves. Sin aspecto de 3D, sin aspecto de "
    "videojuego, sin piel de cera ni retoque plástico. Sin texto, sin logos, sin marca de "
    "agua, sin bordes ni marcos. Una sola persona adulta en la imagen."
)


def _bloque_identidad_pj(doc: Dict[str, Any], n_refs: int) -> str:
    g = _g(doc)
    desc = doc.get("desc_cara") or ""
    txt = (
        f"IDENTIDAD (no negociable): {g['la']} {g['persona']} de esta foto es EXACTAMENTE {g['la']} "
        f"{g['misma']} persona de las IMÁGENES DE REFERENCIA (1 a {n_refs}): misma cara, misma "
        "estructura ósea, mismos ojos, nariz, boca y cejas, mismo tono de piel, mismo pelo "
        "(color, largo, textura, peinado salvo que se pida otro), mismo cuerpo, misma altura y "
        "misma edad. No la reemplaces por otra persona, no la rejuvenezcas, no la adelgaces ni "
        "la idealices. Si al final no es reconocible al instante como la de las referencias, la "
        "imagen está MAL."
    )
    if desc:
        txt += f"\nDescripción de su rostro (para reforzar la identidad): {desc}"
    return txt


def _prompt_retrato(doc: Dict[str, Any]) -> str:
    ap = doc.get("apariencia") or {}
    g = _g(doc)
    attrs = [f"- {k.capitalize()}: {v}" for k, v in ap.items() if v]
    if doc.get("edad"):
        attrs.insert(0, f"- Edad aparente: {doc['edad']} años (adulta)")
    attrs_txt = "\n".join(attrs) or "- Estilo argentino, natural, de catálogo."
    return (
        f"Retrato fotorrealista de estudio de {g['la']} {g['persona']}, para usar como REFERENCIA "
        f"DE IDENTIDAD reutilizable de un personaje digital llamado {doc.get('nombre') or 'X'}.\n\n"
        f"Características:\n{attrs_txt}\n\n"
        "Encuadre: plano medio 3/4 con la cara bien visible y nítida, mirada a cámara, expresión "
        "natural y amable (media sonrisa). Vestuario neutro y básico (remera lisa). Fondo gris "
        "claro liso. Luz pareja de catálogo, sin sombras duras.\n" + REALISMO
    )


def _prompt_hoja(doc: Dict[str, Any]) -> str:
    g = _g(doc)
    return (
        "HOJA DE IDENTIDAD de un personaje: 3 TOMAS DISTINTAS EN UNA SOLA IMAGEN (21:9), lado "
        "a lado, separadas por una LÍNEA BLANCA VERTICAL limpia, recta y pareja (blanco puro, "
        "~1.5% del ancho). Fondo gris claro liso e idéntico en los 3 paneles, misma luz pareja "
        "de estudio, misma ropa neutra (remera lisa y jean).\n"
        f"  · Panel 1: {g['la']} {g['persona']} de PERFIL 3/4, plano medio, cara nítida.\n"
        "  · Panel 2: CUERPO ENTERO de frente, de pie, relajada, se ven los pies.\n"
        "  · Panel 3: CUERPO ENTERO de ESPALDA, de pie, se ve el peinado por detrás.\n"
        + _bloque_identidad_pj(doc, 1) + "\n"
        "La IMAGEN 1 es el retrato aprobado: es esa persona, en las tres tomas.\n" + REALISMO
    )


def _prompt_foto(doc: Dict[str, Any], pedido: Dict[str, Any], n_refs: int,
                 n_prendas: int, settings: Dict[str, Any]) -> str:
    g = _g(doc)
    marca = doc.get("marca") or ""
    partes = [
        f"Foto para redes sociales de {doc.get('nombre') or 'la protagonista'}, "
        f"{g['persona']}, personaje digital y cara de la marca {marca or 'de moda'}.",
        _bloque_identidad_pj(doc, n_refs),
    ]
    if n_prendas:
        partes.append(
            f"PRENDA (no negociable): tiene puesta EXACTAMENTE la prenda de las FOTOS REALES DEL "
            f"PRODUCTO (imágenes {n_refs + 1} a {n_refs + n_prendas}): mismo diseño, mismo color, "
            "misma tela, mismos breteles, costuras, estampa, apliques y terminaciones. No la "
            "rediseñes, no le agregues ni le saques detalles, no le cambies el tono. La prenda "
            "le calza como calza de verdad esa prenda a ese cuerpo."
        )
    if pedido.get("escena"):
        partes.append(f"ESCENA Y LUZ: {pedido['escena']}")
    if pedido.get("outfit"):
        partes.append(f"OUTFIT: {pedido['outfit']}")
    if pedido.get("encuadre"):
        partes.append(f"ENCUADRE: {pedido['encuadre']}")
    if pedido.get("expresion"):
        partes.append(f"EXPRESIÓN Y ACTITUD: {pedido['expresion']}")
    if pedido.get("extra"):
        partes.append(f"INDICACIONES DE LA DUEÑA: {pedido['extra']}")
    partes.append(
        "Estilo: foto de Instagram real, espontánea y creíble, no acartonada ni de stock. "
        "Composición limpia, la persona es la protagonista." )
    si = str(settings.get("system_instruction") or "").strip()
    if si:
        partes.append(f"Notas de la marca: {si}")
    partes.append(REALISMO)
    return "\n\n".join(partes)


# ─────────────────────────────────────────────────────────────────────────────
# IDENTIDAD: retrato + hoja
# ─────────────────────────────────────────────────────────────────────────────

async def _refs_identidad(doc: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Devuelve [(etiqueta, b64)] con el retrato primero y las vistas que existan."""
    pid = doc["id"]
    hoja = doc.get("hoja") or {}
    refs: List[Tuple[str, str]] = []
    if hoja.get("retrato"):
        b = await kv.get(_k_img(pid, "retrato"))
        if b:
            refs.append(("retrato de identidad, la cara manda", b))
    for v, et in (("cuerpo", "cuerpo entero de frente"), ("perfil", "perfil 3/4"),
                  ("espalda", "cuerpo entero de espalda")):
        if hoja.get(v):
            b = await kv.get(_k_img(pid, v))
            if b:
                refs.append((et, b))
    return refs


async def _cobrar(est: float) -> None:
    ok, motivo, _, _ = await budget_check(est)
    if not ok:
        raise HTTPException(402, motivo)


# ─────────────────────────────────────────────────────────────────────────────
# GALERÍA
# ─────────────────────────────────────────────────────────────────────────────

async def _galeria(pid: str) -> List[Dict[str, Any]]:
    g = await kv.get(_k_gal(pid))
    return g if isinstance(g, list) else []


async def _galeria_agregar(pid: str, item: Dict[str, Any]) -> None:
    g = await _galeria(pid)
    g.insert(0, item)
    sobran = g[MAX_GALERIA:]
    g = g[:MAX_GALERIA]
    await kv.set(_k_gal(pid), g)
    for viejo in sobran:
        if viejo.get("tipo") == "foto":
            await kv.delete(_k_foto(pid, viejo["id"]))


async def _guardar_en_drive(nombre: str, contenido: bytes, mime: str) -> Optional[str]:
    sub = CURRENT_SUB.get()
    try:
        if not await _drive_connected_for(sub):
            return None
        return await drive_upload(nombre, contenido, mime, user_sub=sub)
    except Exception as e:
        print(f"[personajes][drive] {e}")
        return None


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return s[:40] or "personaje"


async def _generar_foto(doc: Dict[str, Any], pedido: Dict[str, Any],
                        prendas: List[str], settings: Dict[str, Any],
                        origen: str) -> Dict[str, Any]:
    refs = await _refs_identidad(doc)
    if not refs:
        raise HTTPException(400, "Este personaje todavía no tiene retrato aprobado. "
                                 "Hacelo en la ficha antes de pedirle fotos.")
    tam = pedido.get("calidad") if pedido.get("calidad") in TAMANOS else (doc.get("calidad") or "2K")
    formato = pedido.get("formato") if pedido.get("formato") in FORMATOS_FOTO else (doc.get("formato") or "4:5")
    est = _pricing(settings).get(tam, 0.10)
    await _cobrar(est)

    prompt = _prompt_foto(doc, pedido, len(refs), len(prendas), settings)
    parts: List[Dict[str, Any]] = [{"text": prompt}]
    for i, (et, b64) in enumerate(refs):
        parts.append({"text": f"IMAGEN {i + 1} (referencia de identidad: {et}):"})
        parts.append(_img_part(b64))
    for j, b64 in enumerate(prendas):
        parts.append({"text": f"IMAGEN {len(refs) + j + 1} (foto real del producto):"})
        parts.append(_img_part(b64))

    img = await gemini_generate(parts, settings, aspect=formato, image_size=tam)
    await budget_record("personaje", tam, est, 1,
                        note=f"{doc.get('nombre', '')}: {pedido.get('titulo') or origen}")

    fid = _uuid.uuid4().hex[:10]
    b64 = _compress_ref(img, max_dim=2048, q=92)
    ok = await kv.set(_k_foto(doc["id"], fid), b64)
    if not ok:
        raise HTTPException(500, f"La foto salió pero no se pudo guardar ({kv.backend}). "
                                 f"{kv.last_error or ''}")
    item = {"id": fid, "tipo": "foto", "ts": _ahora(), "titulo": _texto(pedido.get("titulo"), 80),
            "caption": _texto(pedido.get("caption"), 1200), "pedido": {
                k: _texto(pedido.get(k), 400) for k in ("escena", "outfit", "encuadre", "expresion", "extra")},
            "calidad": tam, "formato": formato, "con_prenda": bool(prendas), "origen": origen}
    await _galeria_agregar(doc["id"], item)
    est_doc = doc.setdefault("estado", _estado_base())
    est_doc["fotos"] = int(est_doc.get("fotos", 0) or 0) + 1

    # A Drive si está conectado (en segundo plano: no frena la respuesta).
    nombre = f"{_slug(doc.get('nombre', ''))}-{fid}.jpg"
    _spawn(_guardar_en_drive(nombre, base64.b64decode(b64), "image/jpeg"))
    item["src"] = "data:image/jpeg;base64," + b64
    return item


# ─────────────────────────────────────────────────────────────────────────────
# DIARIO (la vida de todos los días)
# ─────────────────────────────────────────────────────────────────────────────

def _system_diario(doc: Dict[str, Any]) -> str:
    est = doc.get("estado") or _estado_base()
    dias = _dias_sin_hablar(doc)
    mem = "\n".join(f"- {m}" for m in (doc.get("memoria") or [])[-MAX_MEMORIA:]) or "- nada aún"
    fecha = _dt.date.today()
    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    return (
        f"Sos {doc.get('nombre') or 'un personaje'}, persona digital y cara de una marca.\n"
        f"TU FICHA:\n{_ficha_texto(doc)}\n\nLO QUE RECORDÁS:\n{mem}\n\n"
        f"Hoy es {dias_es[fecha.weekday()]} {fecha.day}/{fecha.month}. Energía {_energia(doc)}/100, "
        f"racha {est.get('racha', 0)} días, hace {dias} días que no hablás con tu dueña.\n\n"
        "Escribí tu DIARIO de hoy y propuestas de contenido. Respondé SOLO con un JSON:\n"
        "{\n"
        '  "humor": "una o dos palabras (ej: \'con pilas\', \'nostálgica\', \'juguetona\')",\n'
        '  "texto": "2 a 4 oraciones en primera persona, rioplatense (voseo), sobre cómo amaneciste, '
        'qué hiciste o qué tenés ganas de hacer hoy. Concreto y con personalidad, sin cursilería.",\n'
        '  "propuestas": [\n'
        '    {"titulo": "3-5 palabras", "por_que": "una oración: por qué hoy", '
        '"escena": "dónde y con qué luz", "outfit": "qué tenés puesto", '
        '"encuadre": "tipo de plano y ángulo", "expresion": "gesto y actitud", '
        '"caption": "texto listo para publicar con 3 a 6 hashtags"}\n'
        "  ]\n"
        "}\n"
        "Exactamente 3 propuestas, distintas entre sí (una puede ser con una prenda de la marca, "
        "una de tu vida, una de tendencia o fecha del calendario). Todo apto para redes y en "
        "línea con la marca."
    )


async def _diario_hoy(doc: Dict[str, Any], forzar: bool = False) -> Dict[str, Any]:
    pid = doc["id"]
    lst = await kv.get(_k_diario(pid))
    lst = lst if isinstance(lst, list) else []
    hoy = _hoy()
    if not forzar:
        for e in lst:
            if e.get("fecha") == hoy:
                return e
    data = await _gemini_json(_system_diario(doc),
                              [{"role": "user", "parts": [{"text": "Escribí el diario de hoy."}]}],
                              temperature=0.9)
    props = []
    for p in (data.get("propuestas") or [])[:3]:
        if isinstance(p, dict):
            props.append({k: _texto(p.get(k), 400) for k in
                          ("titulo", "por_que", "escena", "outfit", "encuadre", "expresion", "caption")})
    entrada = {"fecha": hoy, "humor": _texto(data.get("humor"), 40) or "tranquila",
               "texto": _texto(data.get("texto"), 900), "propuestas": props, "ts": _ahora()}
    lst = [e for e in lst if e.get("fecha") != hoy]
    lst.insert(0, entrada)
    await kv.set(_k_diario(pid), lst[:MAX_DIARIO])
    est = doc.setdefault("estado", _estado_base())
    est["humor"] = entrada["humor"]
    est["ultimo_diario"] = hoy
    await _guardar(doc)
    return entrada


# ─────────────────────────────────────────────────────────────────────────────
# VOZ Y VIDEO
# ─────────────────────────────────────────────────────────────────────────────

async def _tts_mp3(texto: str, voz: str, doc: Dict[str, Any]) -> bytes:
    key = await _current_api_key()
    if not key:
        raise HTTPException(500, "Falta la API key de Google.")
    tono = doc.get("tono") or "natural y cercano"
    instruccion = (f"Leé este mensaje de voz con acento argentino rioplatense, como una persona "
                   f"real mandando un audio de WhatsApp, tono {tono}, ritmo natural: ")
    body = {
        "contents": [{"parts": [{"text": instruccion + texto}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voz}}},
        },
    }
    url = f"{GEMINI_BASE}/models/{TTS_MODEL}:generateContent"
    async with httpx.AsyncClient(timeout=120) as cli:
        r = await cli.post(url, headers={"x-goog-api-key": key,
                                         "Content-Type": "application/json"}, json=body)
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"La voz devolvió error: {r.text[:300]}")
    try:
        b64 = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    except Exception:
        raise HTTPException(422, "La voz no devolvió audio.")
    pcm = base64.b64decode(b64)
    binario = _ffmpeg_bin()
    if not binario:
        raise HTTPException(500, "No hay ffmpeg para convertir el audio.")
    tmp = PJ_DIR / f"tts_{_uuid.uuid4().hex}"
    pcm_path, mp3_path = tmp.with_suffix(".pcm"), tmp.with_suffix(".mp3")
    try:
        pcm_path.write_bytes(pcm)
        res = await asyncio.to_thread(
            subprocess.run,
            [binario, "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(pcm_path),
             "-b:a", "96k", str(mp3_path)], capture_output=True, timeout=60)
        if res.returncode != 0 or not mp3_path.exists():
            raise HTTPException(500, "ffmpeg no pudo convertir el audio.")
        return mp3_path.read_bytes()
    finally:
        for p in (pcm_path, mp3_path):
            try:
                p.unlink()
            except OSError:
                pass


def _prompt_habla(doc: Dict[str, Any], texto: str) -> str:
    g = _g(doc)
    return (
        f"The {g['woman']} in the image speaks directly to the camera, in Spanish with a natural "
        f"Argentine (Rioplatense) accent, saying exactly this and nothing else: \"{texto}\". "
        f"{g['she'].capitalize()} talks like a real person recording a selfie video for "
        f"Instagram: natural lip sync, small head movements, eye contact with the lens, a light "
        f"smile at the end. The camera is static, handheld feel but steady. REAL TIME at 24 fps, "
        f"never slow motion. IDENTITY LOCK: the face in the first frame is a real, specific "
        f"person; {g['her']} bone structure, eyes, nose, mouth, jawline, skin tone and hairline "
        f"stay EXACTLY the same in every frame; only the muscles move when {g['she']} speaks. "
        f"The outfit, the hair and the background stay identical to the first frame. Clean, "
        f"clear voice, no music, no background noise. No on-screen text, captions, subtitles, "
        f"logos or watermarks. No morphing, no warping, no extra people."
    )


NEGATIVO_HABLA = (
    "text, captions, subtitles, logos, watermarks, morphing, warping, face change, "
    "different person, extra people, music, slow motion, camera shake, black bars, "
    "borders, cgi, 3d render, waxy skin, plastic skin, frozen face"
)


async def _lanzar_veo_habla(prompt: str, frame_b64: str, formato: str, motor: str) -> str:
    key = await _current_api_key()
    if not key:
        raise RuntimeError("Falta la API key de Google.")
    modelo = VEO_MODELS.get(motor, VEO_MODELS["veo_fast"])
    url = f"{GEMINI_BASE}/models/{modelo}:predictLongRunning"
    body = {
        "instances": [{"prompt": prompt,
                       "image": {"bytesBase64Encoded": frame_b64, "mimeType": "image/jpeg"}}],
        "parameters": {"aspectRatio": formato, "negativePrompt": NEGATIVO_HABLA,
                       "resolution": "1080p", "durationSeconds": HABLA_SEG},
    }
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120) as cli:
        r = await cli.post(url, headers=headers, json=body)
        if r.status_code == 400:
            for p in ("durationSeconds", "resolution"):
                body["parameters"].pop(p, None)
                r = await cli.post(url, headers=headers, json=body)
                if r.status_code == 200:
                    break
    if r.status_code != 200:
        txt = r.text[:400]
        if r.status_code in (403, 429) or "billing" in txt.lower() or "quota" in txt.lower():
            raise RuntimeError(f"Veo HTTP {r.status_code}: {txt} — Veo necesita una key de "
                               "Google con facturación habilitada.")
        raise RuntimeError(f"Veo HTTP {r.status_code}: {txt}")
    op = r.json().get("name")
    if not op:
        raise RuntimeError(f"Veo no devolvió operación: {r.text[:300]}")
    return op


async def _job_set(jid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    job = (await kv.get(_k_job(jid))) or {"id": jid}
    job.update(patch)
    job["latido"] = time.time()
    await kv.set(_k_job(jid), job, ttl=JOB_TTL)
    return job


def _clip_path(jid: str) -> Path:
    return PJ_DIR / f"{jid}.mp4"


def _k_jobs(pid: str) -> str:
    return _pfx() + f"pj:{pid}:jobs"


JOBS_INDICE = 20


async def _job_nuevo(jid: str, pid: str, tipo: str, estimado_seg: int,
                     extra: Dict[str, Any]) -> None:
    """Crea el trabajo con hora de inicio y estimado, y lo anota en el índice del
    personaje: así la galería lo muestra "en curso" aunque se cierre el modal."""
    await _job_set(jid, {"pid": pid, "tipo": tipo, "estado": "en_cola", "inicio": time.time(),
                         "estimado_seg": estimado_seg, "creado": _ahora(), **extra})
    lst = (await kv.get(_k_jobs(pid))) or []
    await kv.set(_k_jobs(pid), ([jid] + [x for x in lst if x != jid])[:JOBS_INDICE])


async def _procesar_habla(jid: str, doc: Dict[str, Any], texto: str, frame_b64: str,
                          formato: str, motor: str, sub: Optional[str]) -> None:
    set_current_sub(sub)
    try:
        await _job_set(jid, {"estado": "generando", "paso": "Veo está grabando el clip…"})
        op = await _lanzar_veo_habla(_prompt_habla(doc, texto), frame_b64, formato, motor)
        await _esperar_veo(op, _clip_path(jid))
        costo = PRECIO_SEG.get(motor, 0.15) * HABLA_SEG
        await budget_record("personaje_video", motor, costo, 1,
                            note=f"{doc.get('nombre', '')} habla: {texto[:40]}")
        await _job_set(jid, {"paso": "El inspector está revisando el clip…"})
        qc = await _inspeccionar_clip(_clip_path(jid), [frame_b64])
        await _galeria_agregar(doc["id"], {"id": jid, "tipo": "video", "ts": _ahora(),
                                           "titulo": _texto(texto, 60), "caption": texto,
                                           "motor": motor, "formato": formato, "qc": qc})
        link = await _guardar_en_drive(f"{_slug(doc.get('nombre', ''))}-{jid}.mp4",
                                       _clip_path(jid).read_bytes(), "video/mp4")
        await _job_set(jid, {"estado": "listo", "paso": "", "drive": link, "qc": qc})
    except Exception as e:
        await _job_set(jid, {"estado": "error", "error": str(e)[:600]})


# ─────────────────────────────────────────────────────────────────────────────
# MOVETE VOS (tu video, sus movimientos → ella te reemplaza)
# ─────────────────────────────────────────────────────────────────────────────

async def _fal_key() -> str:
    settings = await get_settings()
    return FAL_KEY or str(settings.get("fal_api_key") or "").strip()


def _preparar_video(entrada: Path, salida: Path, max_seg: int) -> float:
    """Recorta a max_seg, achica a 720p de alto como mucho, 24 fps, h264 y audio
    aac: un video de celular de 80 MB pasa a unos pocos MB sin perder lo que
    importa (el movimiento). Devuelve la duración final."""
    binario = _ffmpeg_bin()
    if not binario:
        raise RuntimeError("No hay ffmpeg en el servidor para preparar el video.")
    cmd = [binario, "-y", "-i", str(entrada), "-t", str(max_seg),
           "-vf", "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)',fps=24,format=yuv420p",
           "-c:v", "libx264", "-preset", "fast", "-crf", "20",
           "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(salida)]
    res = subprocess.run(cmd, capture_output=True, timeout=300)
    if res.returncode != 0 or not salida.exists():
        raise RuntimeError("ffmpeg no pudo leer el video: " + res.stderr.decode(errors="ignore")[-200:])
    return _duracion_video(salida)


async def _fal_subir(cli: httpx.AsyncClient, key: str, contenido: bytes, mime: str, nombre: str) -> str:
    """Sube el archivo al storage de fal y devuelve la URL. Si falla, va como data URI."""
    try:
        r = await cli.post(FAL_STORAGE, headers={"Authorization": f"Key {key}"},
                           json={"content_type": mime, "file_name": nombre})
        if r.status_code in (200, 201):
            d = r.json()
            up = d.get("upload_url")
            if up and d.get("file_url"):
                r2 = await cli.put(up, content=contenido, headers={"Content-Type": mime})
                if r2.status_code in (200, 201, 204):
                    return d["file_url"]
        print(f"[personajes] fal storage {r.status_code}: {r.text[:150]}; va como data URI")
    except Exception as e:
        print(f"[personajes] fal storage error: {e}; va como data URI")
    return f"data:{mime};base64," + base64.b64encode(contenido).decode()


async def _fal_animate(video: Path, foto_b64: str, modo: str, destino: Path,
                       jid: Optional[str] = None, resolucion: str = "") -> None:
    key = await _fal_key()
    if not key:
        raise RuntimeError("Falta FAL_KEY (o la API key de fal en Ajustes de Fotos).")
    modelo = FAL_ANIMATE.get(modo) or FAL_ANIMATE["replace"]
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    sub_url = f"{FAL_BASE}/{modelo}"
    async with httpx.AsyncClient(timeout=300) as cli:
        video_url = await _fal_subir(cli, key, video.read_bytes(), "video/mp4", video.name)
        image_url = await _fal_subir(cli, key, base64.b64decode(foto_b64), "image/jpeg", "ref.jpg")
        payload: Dict[str, Any] = {"video_url": video_url, "image_url": image_url,
                                   "resolution": resolucion or MOVETE_RESOLUCION,
                                   "enable_safety_checker": False}
        r = await cli.post(sub_url, headers=headers, json=payload)
        if r.status_code in (400, 422):
            for p in ("enable_safety_checker", "resolution"):
                payload.pop(p, None)
                r = await cli.post(sub_url, headers=headers, json=payload)
                if r.status_code in (200, 201):
                    break
        if r.status_code == 404:
            raise RuntimeError(f"fal HTTP 404: el modelo '{modelo}' no existe con esa ruta. "
                               "Cargá la nueva en FAL_ANIMATE_REPLACE_MODEL / FAL_ANIMATE_MOVE_MODEL.")
        if r.status_code not in (200, 201):
            raise RuntimeError(f"fal submit HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        rid = data.get("request_id") or data.get("requestId")
        status_url = data.get("status_url") or (f"{sub_url}/requests/{rid}/status" if rid else None)
        result_url = data.get("response_url") or (f"{sub_url}/requests/{rid}" if rid else None)
        if not status_url:
            raise RuntimeError(f"fal no devolvió status_url: {json.dumps(data)[:200]}")
        if jid:
            # Las URLs quedan en el trabajo: si el server se reinicia (un deploy de
            # Railway, por ejemplo) mientras fal sigue dibujando, el resultado se
            # recupera de fal en vez de perderse un video ya pagado.
            await _job_set(jid, {"fal_status_url": status_url, "fal_result_url": result_url,
                                 "fal_inicio": time.time()})
        await _fal_esperar_y_bajar(cli, headers, status_url, result_url, destino, jid)


async def _fal_esperar_y_bajar(cli: httpx.AsyncClient, headers: Dict[str, str], status_url: str,
                               result_url: str, destino: Path, jid: Optional[str],
                               inicio: Optional[float] = None) -> None:
    inicio = inicio or time.time()
    ultimo_paso = ""
    while True:
        if time.time() - inicio > MOVETE_TIMEOUT:
            raise RuntimeError(f"fal no terminó en {MOVETE_TIMEOUT // 60} minutos. Si el video era "
                               "largo, probá con uno más corto (5 a 10 s).")
        rs = await cli.get(status_url, headers=headers)
        data_st = rs.json() if rs.status_code == 200 else {}
        st = data_st.get("status", "")
        if st in ("COMPLETED", "Completed", "succeeded", "OK"):
            break
        if st in ("FAILED", "Error", "CANCELLED"):
            raise RuntimeError(f"fal falló: {rs.text[:300]}")
        # Lo que fal sabe del trabajo, a la pantalla: en la cola (y en qué
        # puesto) o ya dibujando. Sin esto es un puntito que gira 6 minutos.
        if jid:
            if st == "IN_QUEUE":
                pos = data_st.get("queue_position")
                paso = "En la cola de fal" + (f", puesto {pos}" if pos is not None else "") + "…"
            else:
                paso = "Wan Animate está copiando tus movimientos…"
            if paso != ultimo_paso:
                await _job_set(jid, {"paso": paso})
                ultimo_paso = paso
            else:
                await _job_set(jid, {})     # latido: sigue vivo
        await asyncio.sleep(6)
    rr = await cli.get(result_url, headers=headers)
    if rr.status_code != 200:
        raise RuntimeError(f"fal result HTTP {rr.status_code}: {rr.text[:200]}")
    res = rr.json()
    vurl = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
    vurl = vurl or (res.get("videos") or [{}])[0].get("url") or res.get("url")
    if not vurl:
        raise RuntimeError(f"fal no devolvió video: {json.dumps(res)[:300]}")
    dl = await cli.get(vurl, follow_redirects=True)
    if dl.status_code != 200:
        raise RuntimeError(f"fal descarga HTTP {dl.status_code}")
    destino.write_bytes(dl.content)


MOVETE_TIMEOUT = 40 * 60   # Wan Animate con 20 s de video puede pasar los 15 min
QC_CUADROS = 3   # cuadros del clip que mira el inspector (principio, medio, final)


def _cuadros_del_clip(clip: Path, n: int = QC_CUADROS) -> List[bytes]:
    """Saca n cuadros JPEG repartidos a lo largo del clip (sin los bordes)."""
    binario = _ffmpeg_bin()
    dur = _duracion_video(clip)
    if not binario or dur <= 0:
        return []
    out: List[bytes] = []
    for i in range(n):
        t = dur * (i + 1) / (n + 1)
        dest = clip.with_name(f"{clip.stem}_qc{i}.jpg")
        try:
            subprocess.run([binario, "-y", "-ss", f"{t:.2f}", "-i", str(clip), "-frames:v", "1",
                            "-q:v", "2", str(dest)], capture_output=True, timeout=60)
            if dest.exists():
                out.append(dest.read_bytes())
        except Exception:
            pass
        finally:
            dest.unlink(missing_ok=True)
    return out


async def _inspeccionar_clip(clip: Path, prendas_b64: List[str]) -> Optional[Dict[str, Any]]:
    """El mismo inspector de prenda de Fotos, sobre cuadros del video terminado.
    Devuelve {puntaje, diferencias, cuadros} con el PEOR puntaje, o None si no
    se pudo (nunca rompe el trabajo: el clip ya está pago)."""
    try:
        settings = await get_settings()
        if str(settings.get("qc_prenda", "si")).lower() in ("no", "0", "off", "false"):
            return None
        if not prendas_b64:
            return None
        cuadros = await asyncio.to_thread(_cuadros_del_clip, clip)
        if not cuadros:
            return None
        res = await asyncio.gather(*[verificar_prenda(c, prendas_b64) for c in cuadros],
                                   return_exceptions=True)
        vals = [r for r in res if isinstance(r, dict)]
        if not vals:
            return None
        difs: List[str] = []
        for v in vals:
            for d in (v.get("diferencias") or []):
                if d not in difs:
                    difs.append(d)
        return {"puntaje": min(int(v.get("puntaje", 10)) for v in vals),
                "diferencias": difs[:8], "cuadros": len(vals)}
    except Exception as e:
        print(f"[personajes][qc] {e}")
        return None



async def _terminar_movete(jid: str, doc: Dict[str, Any], foto_b64: str, modo: str,
                           segundos: float, prendas_b64: Optional[List[str]]) -> None:
    """Lo que pasa cuando el clip ya está en el disco: cobrar, inspeccionar,
    galería, Drive. Separado para poder reanudar después de un reinicio."""
    costo = round(PRECIO_MOVETE * segundos, 3)
    await budget_record("personaje_movete", modo, costo, 1,
                        note=f"{doc.get('nombre', '')} movete vos {segundos:.0f}s")
    # Inspector: contra las fotos reales de la prenda si las adjuntó; si no,
    # contra la foto de referencia (ella con la prenda puesta), que es lo que
    # el motor tenía que respetar.
    await _job_set(jid, {"paso": "El inspector está revisando la prenda en el video…"})
    qc = await _inspeccionar_clip(_clip_path(jid), prendas_b64 or [foto_b64])
    await _galeria_agregar(doc["id"], {"id": jid, "tipo": "video", "ts": _ahora(),
                                       "titulo": f"Movete vos · {segundos:.0f}s",
                                       "caption": "", "motor": "wan_animate_" + modo, "qc": qc})
    link = await _guardar_en_drive(f"{_slug(doc.get('nombre', ''))}-movete-{jid}.mp4",
                                   _clip_path(jid).read_bytes(), "video/mp4")
    await _job_set(jid, {"estado": "listo", "paso": "", "drive": link, "qc": qc})
    await kv.delete(_k_job(jid) + ":prendas")


async def _procesar_movete(jid: str, doc: Dict[str, Any], video: Path, foto_b64: str,
                           modo: str, segundos: float, sub: Optional[str],
                           prendas_b64: Optional[List[str]] = None, resolucion: str = "") -> None:
    set_current_sub(sub)
    try:
        await _job_set(jid, {"estado": "generando", "paso": "Subiendo tu video a fal…"})
        await _fal_animate(video, foto_b64, modo, _clip_path(jid), jid=jid, resolucion=resolucion)
        await _terminar_movete(jid, doc, foto_b64, modo, segundos, prendas_b64)
    except Exception as e:
        await _job_set(jid, {"estado": "error", "error": str(e)[:600]})
    finally:
        try:
            video.unlink()
        except OSError:
            pass


LATIDO_MUERTO = 120       # seg sin latido = el proceso que lo llevaba ya no existe


async def _reanudar_movete(jid: str, sub: Optional[str]) -> None:
    """El server se reinició con el trabajo a medias: fal sigue (o siguió) solo,
    así que se retoma desde su cola en vez de darlo por perdido."""
    set_current_sub(sub)
    job = await kv.get(_k_job(jid)) or {}
    try:
        key = await _fal_key()
        doc = await _doc(job["pid"])
        foto = await kv.get(job.get("ref_key") or "")
        if not key or not foto:
            raise RuntimeError("No pude recuperar la referencia del trabajo.")
        await _job_set(jid, {"estado": "generando", "paso": "Retomando el trabajo en fal…"})
        headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=300) as cli:
            await _fal_esperar_y_bajar(cli, headers, job["fal_status_url"], job["fal_result_url"],
                                       _clip_path(jid), jid, inicio=job.get("fal_inicio"))
        prendas = await kv.get(_k_job(jid) + ":prendas") or []
        await _terminar_movete(jid, doc, foto, job.get("modo", "replace"),
                               float(job.get("segundos") or 0), prendas)
    except Exception as e:
        await _job_set(jid, {"estado": "error", "error": "Al retomar: " + str(e)[:500]})


async def _revisar_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Si un trabajo quedó 'generando' sin latido, o lo retoma desde fal o lo
    marca como perdido. Se llama al consultar el trabajo; no hace falta un cron."""
    if not isinstance(job, dict) or job.get("estado") not in ("en_cola", "generando"):
        return job
    quieto = time.time() - float(job.get("latido") or job.get("inicio") or 0)
    if quieto < LATIDO_MUERTO:
        return job
    jid = job.get("id", "")
    if job.get("fal_status_url") and job.get("fal_result_url") and job.get("ref_key"):
        if time.time() - float(job.get("reanudado") or 0) > LATIDO_MUERTO * 2:
            job = await _job_set(jid, {"reanudado": time.time(),
                                       "paso": "El server se reinició: retomando desde fal…"})
            _spawn(_reanudar_movete(jid, CURRENT_SUB.get()))
        return job
    return await _job_set(jid, {"estado": "error",
                                "error": "El servidor se reinició (un deploy, por ejemplo) antes de "
                                         "terminar y este trabajo no se pudo recuperar. Volvé a "
                                         "generarlo."})


# ─────────────────────────────────────────────────────────────────────────────
# QUE SE MUEVA (una foto → clip corto con movimiento natural, motores de fal)
# ─────────────────────────────────────────────────────────────────────────────

async def _procesar_mover(jid: str, doc: Dict[str, Any], frame_b64: str, prompt: str,
                          motor: str, duracion: int, titulo: str, sub: Optional[str]) -> None:
    set_current_sub(sub)
    try:
        await _job_set(jid, {"estado": "generando",
                             "paso": f"{MOTOR_LABEL.get(motor, motor)} está moviendo la foto…"})
        req = {"motor": motor, "formato": "9:16"}
        await _generar_fal(prompt, frame_b64, req["motor"], _clip_path(jid), duracion)
        costo = round(PRECIO_SEG.get(motor, 0.05) * duracion, 3)
        await budget_record("personaje_mover", motor, costo, 1,
                            note=f"{doc.get('nombre', '')} se mueve: {titulo[:40]}")
        await _job_set(jid, {"paso": "El inspector está revisando el clip…"})
        qc = await _inspeccionar_clip(_clip_path(jid), [frame_b64])
        await _galeria_agregar(doc["id"], {"id": jid, "tipo": "video", "ts": _ahora(),
                                           "titulo": f"{titulo} · {duracion}s", "caption": "",
                                           "motor": motor, "qc": qc})
        link = await _guardar_en_drive(f"{_slug(doc.get('nombre', ''))}-mueve-{jid}.mp4",
                                       _clip_path(jid).read_bytes(), "video/mp4")
        await _job_set(jid, {"estado": "listo", "paso": "", "drive": link, "qc": qc})
    except Exception as e:
        await _job_set(jid, {"estado": "error", "error": str(e)[:600]})


# ─────────────────────────────────────────────────────────────────────────────
# ROUTER
# ─────────────────────────────────────────────────────────────────────────────

async def _bind(request: Request) -> None:
    set_current_sub(session_sub_from_request(request))


router = APIRouter(dependencies=[Depends(_bind)])
API = ROUTE_PREFIX + "/api"


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE, headers={
        "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
        "Pragma": "no-cache",
    })


@router.get(API + "/health")
async def api_health() -> Dict[str, Any]:
    return {"ok": True, "version": VERSION, "kv": kv.backend, "text_model": TEXT_MODEL,
            "tts_model": TTS_MODEL, "ffmpeg": bool(_ffmpeg_bin())}


@router.get(API + "/config")
async def api_config() -> Dict[str, Any]:
    settings = await get_settings()
    return {"voces": VOCES, "precios": _pricing(settings), "motores_habla": MOTORES_HABLA,
            "precio_habla": {m: round(PRECIO_SEG.get(m, 0.15) * HABLA_SEG, 3) for m in MOTORES_HABLA},
            "habla_seg": HABLA_SEG, "max_palabras_habla": MAX_PALABRAS_HABLA,
            "movete": {"precio_seg": PRECIO_MOVETE, "max_seg": MOVETE_MAX_SEG, "max_mb": MOVETE_MAX_MB,
                       "fal_key": bool(await _fal_key()), "resoluciones": list(RESOLUCIONES_MOVETE),
                       "resolucion": MOVETE_RESOLUCION, "seg_por_seg": MOVETE_SEG_POR_SEG,
                       "modelos": FAL_ANIMATE},
            "mover": {"motores": {m: {"label": MOTOR_LABEL.get(m, m), "precio_seg": PRECIO_SEG.get(m, 0.05)}
                                  for m in MOTORES_MOVER},
                      "duraciones": list(MOVER_DURACIONES),
                      "movimientos": {k: v[0] for k, v in MOVIMIENTOS.items()}},
            "tamanos": TAMANOS, "formatos": FORMATOS_FOTO, "max_adjuntos": MAX_ADJUNTOS,
            "videos_prefix": os.environ.get("VIDEOS_PREFIX", "/videos"),
            "home": os.environ.get("IMAGENES_PREFIX", "/imagenes") or "/"}


@router.get(API + "/lista")
async def api_lista() -> Dict[str, Any]:
    out = []
    for pid in await _indice():
        d = await kv.get(_k_doc(pid))
        if isinstance(d, dict):
            r = _resumen(d)
            out.append({k: r[k] for k in ("id", "nombre", "genero", "rol", "estado",
                                          "tiene_retrato", "aprobado", "creado")})
    return {"personajes": out}


@router.post(API + "/crear")
async def api_crear(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    ids = await _indice()
    if len(ids) >= MAX_PERSONAJES:
        raise HTTPException(400, f"Hasta {MAX_PERSONAJES} personajes por cuenta.")
    nombre = _texto(payload.get("nombre"), 60)
    if not nombre:
        raise HTTPException(400, "Ponele un nombre.")
    pid = _uuid.uuid4().hex[:10]
    doc: Dict[str, Any] = {"id": pid, "nombre": nombre, "genero": "mujer", "voz": "Kore",
                           "calidad": "2K", "formato": "4:5", "apariencia": {},
                           "estado": _estado_base(), "memoria": [], "hoja": {},
                           "aprobado": False, "creado": _ahora()}
    _aplicar_ficha(doc, payload)
    if "voz" not in payload:
        doc["voz"] = VOCES[doc["genero"]][0][0]
    # Con un avatar de la pestaña Avatares: su cara ya es la del personaje (retrato
    # aprobado al toque) y su ficha de cuerpo completa la apariencia. Sólo queda
    # generar la hoja de 3 vistas.
    avatar_id = str(payload.get("avatar_id") or "").strip()
    if avatar_id:
        from imagenes_ia import get_avatar, k_avficha
        av = await get_avatar(avatar_id)
        if not av or not av.get("ref_b64"):
            raise HTTPException(404, "Ese avatar no tiene referencia guardada.")
        if av.get("gender") in GENEROS:
            doc["genero"] = av["gender"]
            if "voz" not in payload:
                doc["voz"] = VOCES[doc["genero"]][0][0]
        ficha = await kv.get(k_avficha(avatar_id)) or {}
        ap = dict(doc.get("apariencia") or {})
        for k in ("contextura", "altura"):
            if ficha.get(k) and not ap.get(k):
                ap[k] = _texto(ficha[k], 160)
        if ficha.get("edad") and not doc.get("edad"):
            doc["edad"] = _texto(ficha["edad"], 20)
        if av.get("description") and not ap.get("rasgos"):
            ap["rasgos"] = _texto(av["description"], 160)
        doc["apariencia"] = ap
        if not await kv.set(_k_img(pid, "retrato"), av["ref_b64"]):
            raise HTTPException(500, f"No se pudo guardar el retrato ({kv.backend}).")
        doc["hoja"] = {"retrato": True}
        doc["aprobado"] = True
        doc["desc_cara"] = av.get("desc") or await describe_avatar(av["ref_b64"])
        doc["desde_avatar"] = avatar_id
    await _guardar(doc)
    await kv.set(_k_indice(), [pid] + ids)
    return {"ok": True, "personaje": _resumen(doc)}


@router.get(API + "/avatares")
async def api_avatares_disponibles() -> Dict[str, Any]:
    """Los avatares de la pestaña Avatares, para usar uno como cara del personaje."""
    from imagenes_ia import list_avatars
    data = await list_avatars()
    out = []
    for g, fila in data.items():
        for av in fila:
            if av:
                out.append({"id": av["id"], "name": av.get("name", ""), "gender": g})
    return {"avatares": out}


@router.get(API + "/{pid}")
async def api_get(pid: str) -> Dict[str, Any]:
    doc = await _doc(pid)
    return {"personaje": _resumen(doc)}


@router.post(API + "/{pid}/ficha")
async def api_ficha(pid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    doc = await _doc(pid)
    _aplicar_ficha(doc, payload)
    if isinstance(payload.get("memoria"), list):
        doc["memoria"] = [_texto(m, 200) for m in payload["memoria"] if _texto(m)][-MAX_MEMORIA:]
    await _guardar(doc)
    return {"ok": True, "personaje": _resumen(doc)}


@router.delete(API + "/{pid}")
async def api_borrar(pid: str) -> Dict[str, Any]:
    doc = await _doc(pid)
    for it in await _galeria(pid):
        if it.get("tipo") == "foto":
            await kv.delete(_k_foto(pid, it["id"]))
        elif it.get("tipo") == "video":
            try:
                _clip_path(it["id"]).unlink()
            except OSError:
                pass
    for k in ("retrato",) + VISTAS_HOJA:
        await kv.delete(_k_img(pid, k))
    for k in (_k_chat(pid), _k_gal(pid), _k_diario(pid), _k_doc(pid)):
        await kv.delete(k)
    await kv.set(_k_indice(), [x for x in await _indice() if x != pid])
    return {"ok": True, "nombre": doc.get("nombre")}


# ── Identidad ────────────────────────────────────────────────────────────────

@router.get(API + "/{pid}/img/{vista}")
async def api_img(pid: str, vista: str):
    if vista != "retrato" and vista not in VISTAS_HOJA:
        raise HTTPException(404, "Vista desconocida.")
    await _doc(pid)
    b64 = await kv.get(_k_img(pid, vista))
    if not b64:
        raise HTTPException(404, "Todavía no existe esa imagen.")
    return Response(content=base64.b64decode(b64), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.post(API + "/{pid}/retrato")
async def api_retrato(pid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Un CANDIDATO a retrato (no se guarda hasta aprobar).
    modo: 'generar' (IA), 'subir' (una foto tuya) o 'avatar' (uno de la pestaña Avatares)."""
    doc = await _doc(pid)
    modo = payload.get("modo") or "generar"
    if modo == "subir":
        img = payload.get("image")
        if not img:
            raise HTTPException(400, "Falta la imagen.")
        ref = _compress_ref(base64.b64decode(_strip_data_url(img)), max_dim=1536, q=92)
    elif modo == "avatar":
        ref = await get_avatar_ref(str(payload.get("avatar_id") or ""))
        if not ref:
            raise HTTPException(404, "Ese avatar no tiene referencia.")
    else:
        settings = await get_settings()
        est = _pricing(settings).get("2K", 0.10)
        await _cobrar(est)
        _aplicar_ficha(doc, payload)     # por si cambió la apariencia antes de generar
        await _guardar(doc)
        img = await gemini_generate([{"text": _prompt_retrato(doc)}], settings,
                                    aspect="3:4", image_size="2K")
        await budget_record("personaje", "2K", est, 1, note=f"retrato de {doc.get('nombre', '')}")
        ref = _compress_ref(img, max_dim=1536, q=92)
    return {"preview": "data:image/jpeg;base64," + ref, "ref_b64": ref}


@router.post(API + "/{pid}/retrato/aprobar")
async def api_retrato_aprobar(pid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    doc = await _doc(pid)
    ref = _strip_data_url(str(payload.get("ref_b64") or ""))
    if not ref:
        raise HTTPException(400, "Falta ref_b64.")
    if not await kv.set(_k_img(pid, "retrato"), ref):
        raise HTTPException(500, f"No se pudo guardar el retrato ({kv.backend}). {kv.last_error or ''}")
    # Un retrato nuevo invalida la hoja vieja: era de otra cara.
    for v in VISTAS_HOJA:
        await kv.delete(_k_img(pid, v))
    doc["hoja"] = {"retrato": True}
    doc["aprobado"] = True
    doc["desc_cara"] = await describe_avatar(ref)
    await _guardar(doc)
    return {"ok": True, "personaje": _resumen(doc)}


@router.post(API + "/{pid}/hoja")
async def api_hoja(pid: str) -> Dict[str, Any]:
    """Genera la hoja de identidad (perfil, cuerpo, espalda) en UNA imagen de 3 paneles."""
    doc = await _doc(pid)
    retrato = await kv.get(_k_img(pid, "retrato"))
    if not retrato:
        raise HTTPException(400, "Primero aprobá un retrato.")
    settings = await get_settings()
    est = _pricing(settings).get("2K", 0.10)
    await _cobrar(est)
    parts = [{"text": _prompt_hoja(doc)}, {"text": "IMAGEN 1 (retrato aprobado):"},
             _img_part(retrato)]
    img = await gemini_generate(parts, settings, aspect="21:9", image_size="2K")
    await budget_record("personaje", "2K", est, 1, note=f"hoja de {doc.get('nombre', '')}")
    paneles = split_panels(img, 3)
    if len(paneles) != 3:
        raise HTTPException(422, "La hoja no salió con 3 paneles. Probá de nuevo.")
    hoja = dict(doc.get("hoja") or {})
    for vista, panel in zip(VISTAS_HOJA, paneles):
        buf = io.BytesIO()
        panel.save(buf, format="JPEG", quality=92, optimize=True)
        b64 = _compress_ref(buf.getvalue(), max_dim=1536, q=92)
        if await kv.set(_k_img(pid, vista), b64):
            hoja[vista] = True
    doc["hoja"] = hoja
    await _guardar(doc)
    return {"ok": True, "personaje": _resumen(doc)}




# ── Cerebro ──────────────────────────────────────────────────────────────────

@router.get(API + "/{pid}/chat")
async def api_chat_get(pid: str) -> Dict[str, Any]:
    await _doc(pid)
    msgs = await kv.get(_k_chat(pid))
    return {"mensajes": msgs if isinstance(msgs, list) else []}


@router.delete(API + "/{pid}/chat")
async def api_chat_borrar(pid: str) -> Dict[str, Any]:
    await _doc(pid)
    await kv.delete(_k_chat(pid))
    return {"ok": True}


@router.post(API + "/{pid}/chat")
async def api_chat(pid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    doc = await _doc(pid)
    texto = _texto(payload.get("texto"), 2000)
    adjuntos = [_strip_data_url(a) for a in (payload.get("adjuntos") or [])[:MAX_ADJUNTOS] if a]
    if not texto and not adjuntos:
        raise HTTPException(400, "Escribí algo (o mandá una foto).")
    if not texto:
        texto = "Te mando esta prenda."

    msgs = await kv.get(_k_chat(pid))
    msgs = msgs if isinstance(msgs, list) else []
    lst = await kv.get(_k_diario(pid))
    diario_hoy = next((e for e in (lst or []) if e.get("fecha") == _hoy()), None)

    contents: List[Dict[str, Any]] = []
    for m in msgs[-CHAT_AL_MODELO:]:
        contents.append({"role": "user" if m.get("de") == "vos" else "model",
                         "parts": [{"text": m.get("texto", "")}]})
    partes: List[Dict[str, Any]] = [{"text": texto}]
    for a in adjuntos:
        try:
            partes.append(_img_part(_compress_ref(base64.b64decode(a), max_dim=768, q=80)))
        except Exception:
            pass
    if adjuntos:
        partes.append({"text": f"(La dueña adjuntó {len(adjuntos)} foto(s) de una prenda de la marca.)"})
    contents.append({"role": "user", "parts": partes})

    data = await _gemini_json(_system_cerebro(doc, diario_hoy), contents, temperature=0.85)
    respuesta = _texto(data.get("respuesta"), 3000) or "…"
    foto = data.get("foto") if isinstance(data.get("foto"), dict) else None
    if foto:
        foto = {k: _texto(foto.get(k), 400) for k in
                ("titulo", "escena", "outfit", "encuadre", "expresion", "caption")}
        if not any(foto.get(k) for k in ("escena", "outfit", "encuadre")):
            foto = None

    _tocar(doc)
    if data.get("humor"):
        doc.setdefault("estado", _estado_base())["humor"] = _texto(data["humor"], 40)
    nuevos = [_texto(m, 200) for m in (data.get("recordar") or []) if _texto(m)]
    if nuevos:
        mem = doc.get("memoria") or []
        for n in nuevos[:3]:
            if n not in mem:
                mem.append(n)
        doc["memoria"] = mem[-MAX_MEMORIA:]
    await _guardar(doc)

    ts = _ahora()
    msgs.append({"de": "vos", "texto": texto, "ts": ts, "adjuntos": len(adjuntos)})
    msgs.append({"de": "pj", "texto": respuesta, "ts": ts, "foto_pedido": foto})
    await kv.set(_k_chat(pid), msgs[-MAX_CHAT:])
    return {"respuesta": respuesta, "foto_pedido": foto, "estado": _resumen(doc)["estado"],
            "recordo": nuevos[:3]}


@router.post(API + "/{pid}/foto")
async def api_foto(pid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Genera una foto del personaje a partir de un pedido (del chat, del diario o escrito)."""
    doc = await _doc(pid)
    pedido = payload.get("pedido") if isinstance(payload.get("pedido"), dict) else {}
    pedido = {k: _texto(v, 500) for k, v in pedido.items() if isinstance(v, str)}
    if not any(pedido.get(k) for k in ("escena", "outfit", "encuadre", "extra", "titulo")):
        raise HTTPException(400, "Falta el pedido de la foto.")
    prendas = []
    for a in (payload.get("adjuntos") or [])[:MAX_ADJUNTOS]:
        if a:
            try:
                prendas.append(_compress_ref(base64.b64decode(_strip_data_url(a)), max_dim=1536, q=90))
            except Exception:
                pass
    settings = await get_settings()
    item = await _generar_foto(doc, pedido, prendas, settings,
                               origen=_texto(payload.get("origen"), 20) or "chat")
    await _guardar(doc)
    return {"ok": True, "foto": item}


@router.post(API + "/{pid}/foto/{fid}/rehacer")
async def api_foto_rehacer(pid: str, fid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    doc = await _doc(pid)
    it = next((x for x in await _galeria(pid) if x.get("id") == fid and x.get("tipo") == "foto"), None)
    if not it:
        raise HTTPException(404, "Esa foto no está.")
    pedido = dict(it.get("pedido") or {})
    pedido["titulo"] = it.get("titulo", "")
    pedido["caption"] = it.get("caption", "")
    if payload.get("extra"):
        pedido["extra"] = _texto(payload["extra"], 500)
    prendas = []
    for a in (payload.get("adjuntos") or [])[:MAX_ADJUNTOS]:
        if a:
            try:
                prendas.append(_compress_ref(base64.b64decode(_strip_data_url(a)), max_dim=1536, q=90))
            except Exception:
                pass
    settings = await get_settings()
    item = await _generar_foto(doc, pedido, prendas, settings, origen="rehacer")
    await _guardar(doc)
    return {"ok": True, "foto": item}


# ── Galería ──────────────────────────────────────────────────────────────────

@router.get(API + "/{pid}/galeria")
async def api_galeria(pid: str) -> Dict[str, Any]:
    await _doc(pid)
    return {"items": await _galeria(pid)}


@router.get(API + "/{pid}/galeria/{fid}")
async def api_galeria_foto(pid: str, fid: str):
    await _doc(pid)
    b64 = await kv.get(_k_foto(pid, fid))
    if not b64:
        raise HTTPException(404, "Esa foto ya no está.")
    return Response(content=base64.b64decode(b64), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.delete(API + "/{pid}/galeria/{fid}")
async def api_galeria_borrar(pid: str, fid: str) -> Dict[str, Any]:
    await _doc(pid)
    g = await _galeria(pid)
    it = next((x for x in g if x.get("id") == fid), None)
    if it:
        if it.get("tipo") == "foto":
            await kv.delete(_k_foto(pid, fid))
        elif it.get("tipo") == "video":
            try:
                _clip_path(fid).unlink()
            except OSError:
                pass
        await kv.set(_k_gal(pid), [x for x in g if x.get("id") != fid])
    return {"ok": True}


@router.post(API + "/{pid}/galeria/{fid}/caption")
async def api_galeria_caption(pid: str, fid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    await _doc(pid)
    g = await _galeria(pid)
    for x in g:
        if x.get("id") == fid:
            x["caption"] = _texto(payload.get("caption"), 1200)
    await kv.set(_k_gal(pid), g)
    return {"ok": True}


# ── Diario ───────────────────────────────────────────────────────────────────

@router.get(API + "/{pid}/hoy")
async def api_hoy(pid: str, forzar: int = 0) -> Dict[str, Any]:
    doc = await _doc(pid)
    # Sin cara igual puede escribir su diario; las propuestas piden retrato al hacerlas.
    entrada = await _diario_hoy(doc, forzar=bool(forzar))
    lst = await kv.get(_k_diario(pid))
    return {"hoy": entrada, "anteriores": [e for e in (lst or []) if e.get("fecha") != entrada.get("fecha")][:7],
            "estado": _resumen(doc)["estado"]}


# ── Voz ──────────────────────────────────────────────────────────────────────

@router.post(API + "/{pid}/voz")
async def api_voz(pid: str, payload: Dict[str, Any] = Body(...)):
    doc = await _doc(pid)
    texto = _texto(payload.get("texto"), 900)
    if not texto:
        raise HTTPException(400, "Falta el texto.")
    await _cobrar(COSTO_TTS)
    mp3 = await _tts_mp3(texto, doc.get("voz") or "Kore", doc)
    await budget_record("personaje_voz", "mp3", COSTO_TTS, 1, note=f"{doc.get('nombre', '')}: {texto[:40]}")
    return Response(content=mp3, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


# ── Video hablando a cámara ─────────────────────────────────────────────────

@router.post(API + "/{pid}/hablar")
async def api_hablar(pid: str, request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    doc = await _doc(pid)
    texto = _texto(payload.get("texto"), 400)
    if not texto:
        raise HTTPException(400, "Escribí lo que tiene que decir.")
    if len(texto.split()) > MAX_PALABRAS_HABLA:
        raise HTTPException(400, f"Hasta {MAX_PALABRAS_HABLA} palabras: en {HABLA_SEG} segundos "
                                 "no entra más y Veo corta la frase.")
    motor = payload.get("motor") if payload.get("motor") in MOTORES_HABLA else "veo_fast"
    formato = "9:16" if payload.get("formato") != "16:9" else "16:9"
    fid = str(payload.get("foto_id") or "")
    frame = await kv.get(_k_foto(pid, fid)) if fid else await kv.get(_k_img(pid, "retrato"))
    if not frame:
        raise HTTPException(400, "Elegí una foto de la galería (o aprobá el retrato).")
    costo = PRECIO_SEG.get(motor, 0.15) * HABLA_SEG
    await _cobrar(costo)
    jid = _uuid.uuid4().hex[:10]
    await _job_nuevo(jid, pid, "habla", 150, {"texto": texto, "motor": motor, "formato": formato,
                                             "costo": round(costo, 3), "titulo": "Hablando a cámara"})
    _spawn(_procesar_habla(jid, doc, texto, frame, formato, motor, CURRENT_SUB.get()))
    return {"ok": True, "job": jid, "costo": round(costo, 3)}


@router.post(API + "/{pid}/movete")
async def api_movete(pid: str, video: UploadFile = File(...), foto_id: str = Form(""),
                     modo: str = Form("replace"), resolucion: str = Form(""),
                     prendas: List[UploadFile] = File(default=[])) -> Dict[str, Any]:
    """Tu video con tus movimientos → ella te reemplaza (Wan 2.2 Animate por fal).
    `prendas`: fotos reales del producto (opcionales) para que el inspector compare."""
    doc = await _doc(pid)
    prendas_b64: List[str] = []
    for up in (prendas or [])[:3]:
        try:
            raw = await up.read()
            if raw:
                prendas_b64.append(_compress_ref(raw, max_dim=1536, q=90))
        except Exception:
            pass
    modo = modo if modo in FAL_ANIMATE else "replace"
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal: cargala en Fotos → Ajustes o como "
                                 "FAL_KEY en Railway. Es el motor que hace el reemplazo.")
    if foto_id in ("retrato",) + VISTAS_HOJA:
        ref_key = _k_img(pid, foto_id)
    else:
        ref_key = _k_foto(pid, foto_id) if foto_id else _k_img(pid, "cuerpo")
    foto = await kv.get(ref_key)
    if not foto:
        raise HTTPException(400, "Elegí una foto de referencia (de la galería o de la hoja).")
    crudo = PJ_DIR / f"in_{_uuid.uuid4().hex}.bin"
    listo = PJ_DIR / f"mv_{_uuid.uuid4().hex}.mp4"
    total = 0
    with crudo.open("wb") as f:
        while True:
            chunk = await video.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MOVETE_MAX_MB * 1024 * 1024:
                f.close(); crudo.unlink(missing_ok=True)
                raise HTTPException(413, f"El video pesa más de {MOVETE_MAX_MB} MB. Recortalo antes.")
            f.write(chunk)
    try:
        segundos = await asyncio.to_thread(_preparar_video, crudo, listo, MOVETE_MAX_SEG)
    except Exception as e:
        listo.unlink(missing_ok=True)
        raise HTTPException(422, str(e))
    finally:
        crudo.unlink(missing_ok=True)
    if segundos <= 0.5:
        listo.unlink(missing_ok=True)
        raise HTTPException(422, "No pude leer la duración del video.")
    costo = round(PRECIO_MOVETE * segundos, 3)
    try:
        await _cobrar(costo)
    except HTTPException:
        listo.unlink(missing_ok=True)
        raise
    jid = _uuid.uuid4().hex[:10]
    # Wan Animate procesa a unos 25-35 s por segundo de video a 720p, más la cola.
    await _job_nuevo(jid, pid, "movete", int(60 + MOVETE_SEG_POR_SEG.get(resolucion, 40) * segundos),
                     {"modo": modo, "segundos": round(segundos, 1), "costo": costo,
                      "resolucion": resolucion,
                      "titulo": f"Movete vos · {segundos:.0f}s · {resolucion}", "ref_key": ref_key})
    if prendas_b64:
        await kv.set(_k_job(jid) + ":prendas", prendas_b64, ttl=JOB_TTL)
    _spawn(_procesar_movete(jid, doc, listo, foto, modo, segundos, CURRENT_SUB.get(), prendas_b64,
                            resolucion))
    return {"ok": True, "job": jid, "costo": costo, "segundos": round(segundos, 1)}


@router.post(API + "/{pid}/movete/recuperar")
async def api_movete_recuperar(pid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Un trabajo que fal terminó (o sigue haciendo) pero la app perdió: con el
    request id del panel de fal se retoma desde su cola y se termina acá."""
    await _doc(pid)
    rid = re.sub(r"[^A-Za-z0-9\-]", "", str(payload.get("request_id") or ""))
    if len(rid) < 8:
        raise HTTPException(400, "Pegá el request id de fal (el botón \"Copy request id\").")
    modo = payload.get("modo") if payload.get("modo") in FAL_ANIMATE else "replace"
    fid = str(payload.get("foto_id") or "")
    if fid in ("retrato",) + VISTAS_HOJA:
        ref_key = _k_img(pid, fid)
    else:
        ref_key = _k_foto(pid, fid) if fid else _k_img(pid, "cuerpo")
    if not await kv.get(ref_key):
        raise HTTPException(400, "Elegí la foto de referencia que usaste.")
    try:
        segundos = float(payload.get("segundos") or 0)
    except (TypeError, ValueError):
        segundos = 0.0
    modelo = FAL_ANIMATE[modo]
    base = f"{FAL_BASE}/{modelo}/requests/{rid}"
    jid = _uuid.uuid4().hex[:10]
    await _job_nuevo(jid, pid, "movete", 120,
                     {"modo": modo, "segundos": round(segundos, 1),
                      "costo": round(PRECIO_MOVETE * segundos, 3),
                      "titulo": "Movete vos · recuperado de fal", "ref_key": ref_key,
                      "fal_status_url": base + "/status", "fal_result_url": base,
                      "fal_inicio": time.time(), "estado": "generando",
                      "paso": "Buscando el trabajo en fal…", "reanudado": time.time()})
    _spawn(_reanudar_movete(jid, CURRENT_SUB.get()))
    return {"ok": True, "job": jid}


@router.post(API + "/{pid}/mover")
async def api_mover(pid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """De una foto de la galería, un clip corto con movimiento natural (fal)."""
    doc = await _doc(pid)
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal: cargala en Fotos → Ajustes o como "
                                 "FAL_KEY en Railway. Veo no sirve acá: rechaza lencería.")
    motor = payload.get("motor") if payload.get("motor") in MOTORES_MOVER else "seedance"
    try:
        duracion = int(payload.get("duracion") or 5)
    except (TypeError, ValueError):
        duracion = 5
    duracion = duracion if duracion in MOVER_DURACIONES else 5
    mov = payload.get("movimiento") if payload.get("movimiento") in MOVIMIENTOS else "respirar"
    fid = str(payload.get("foto_id") or "")
    frame = await kv.get(_k_foto(pid, fid)) if fid else None
    if not frame:
        raise HTTPException(400, "Elegí una foto de la galería.")
    titulo, accion = MOVIMIENTOS[mov]
    libre = _texto(payload.get("texto"), 300)
    if mov == "libre":
        if not libre:
            raise HTTPException(400, "Escribí qué movimiento querés.")
        tr = await _traducir_libres({"m": libre})
        accion = tr.get("m") or libre
        titulo = libre[:40]
    elif libre:
        tr = await _traducir_libres({"m": libre})
        accion += " " + (tr.get("m") or libre)
    g = _g(doc)
    prompt = (f"The {g['woman']} in the first frame, a real person. " + accion + SUFIJO_MOVER)
    costo = round(PRECIO_SEG.get(motor, 0.05) * duracion, 3)
    await _cobrar(costo)
    jid = _uuid.uuid4().hex[:10]
    await _job_nuevo(jid, pid, "mover", 90 if motor == "seedance" else 180,
                     {"motor": motor, "duracion": duracion, "movimiento": mov, "costo": costo,
                      "titulo": f"{titulo} · {duracion}s"})
    _spawn(_procesar_mover(jid, doc, frame, prompt, motor, duracion, titulo, CURRENT_SUB.get()))
    return {"ok": True, "job": jid, "costo": costo}


@router.get(API + "/{pid}/jobs")
async def api_jobs(pid: str) -> Dict[str, Any]:
    """Los trabajos recientes del personaje (en curso, listos y con error)."""
    await _doc(pid)
    out = []
    for jid in (await kv.get(_k_jobs(pid))) or []:
        j = await kv.get(_k_job(jid))
        if isinstance(j, dict):
            j = await _revisar_job(j)
            out.append({k: j.get(k) for k in ("id", "tipo", "titulo", "estado", "paso", "inicio",
                                               "estimado_seg", "costo", "error", "latido")})
    return {"jobs": out, "ahora": time.time()}


@router.get(API + "/job/{jid}")
async def api_job(jid: str) -> Dict[str, Any]:
    job = await kv.get(_k_job(jid))
    if not job:
        raise HTTPException(404, "Ese trabajo no está.")
    return await _revisar_job(job)


@router.get(API + "/clip/{jid}")
async def api_clip(jid: str):
    job = await kv.get(_k_job(jid))
    p = _clip_path(jid)
    if not p.exists():
        if job and job.get("estado") == "listo":
            raise HTTPException(410, "El clip ya no está en el disco (se rehace o queda en Drive).")
        raise HTTPException(404, "Todavía no está el clip.")
    return FileResponse(str(p), media_type="video/mp4", filename=f"{jid}.mp4")


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="es-AR">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Studio Luma · Personajes</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%23161419'/%3E%3Crect x='2.5' y='2.5' width='59' height='59' rx='12' fill='none' stroke='%23c9a86b' stroke-width='2'/%3E%3Ctext x='32' y='44' font-family='Georgia,serif' font-size='34' font-weight='600' fill='%23d8b878' text-anchor='middle'%3ESL%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:opsz,wght@6..96,400;6..96,500;6..96,600&family=Jost:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --ink:#ecebf1; --ink-soft:#96919f; --line:#2c2a34;
    --ivory:#131218; --card:#1b1a21; --card-2:#232128;
    --rose:#c9a86b; --rose-deep:#d8b878; --ok:#5fae86; --bad:#e0736f;
    --shadow:0 1px 2px rgba(0,0,0,.5),0 12px 34px rgba(0,0,0,.4);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;
    font-size:16px;line-height:1.55;-webkit-font-smoothing:antialiased}
  a{color:var(--rose-deep);text-decoration:none}
  header{padding:16px 18px 12px;border-bottom:1px solid var(--line);background:rgba(19,18,24,.9);
    backdrop-filter:blur(8px);position:sticky;top:0;z-index:20}
  .brandrow{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .mono{width:42px;height:42px;border-radius:11px;border:1px solid var(--rose);display:flex;align-items:center;
    justify-content:center;flex:none;background:linear-gradient(150deg,#221f27,#161419);
    font-family:'Bodoni Moda',serif;font-weight:600;font-size:20px;color:var(--rose-deep)}
  .brand{font-family:'Bodoni Moda',serif;font-size:24px;font-weight:600;line-height:1}
  .brand small{display:block;font-family:Jost;font-size:12px;font-weight:400;color:var(--ink-soft);margin-top:4px;letter-spacing:.06em}
  .links{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
  .links a{font-size:13px;padding:7px 13px;border:1px solid var(--line);border-radius:999px;background:var(--card)}
  .links a:hover{border-color:var(--rose)}
  main{max-width:1080px;margin:0 auto;padding:16px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:var(--shadow);margin-bottom:16px}
  h2{font-family:'Bodoni Moda',serif;font-weight:500;font-size:22px;margin:0 0 8px}
  h3{font-family:'Bodoni Moda',serif;font-weight:500;font-size:18px;margin:14px 0 6px}
  .hint{color:var(--ink-soft);font-size:13px;margin:4px 0 10px}
  label{display:block;font-size:12.5px;color:var(--ink-soft);margin:10px 0 4px;letter-spacing:.04em;text-transform:uppercase}
  input,select,textarea{width:100%;background:var(--card-2);border:1px solid var(--line);color:var(--ink);
    border-radius:11px;padding:10px 12px;font:inherit;font-size:15px}
  input:focus,select:focus,textarea:focus{outline:none;border-color:var(--rose)}
  textarea{min-height:70px;resize:vertical}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
  button{font:inherit;cursor:pointer;border-radius:11px;border:1px solid var(--line);background:var(--card-2);color:var(--ink);padding:9px 14px;font-size:14px}
  button:hover{border-color:var(--rose)}
  button:disabled{opacity:.45;cursor:default}
  button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500;padding:11px 18px}
  button.ghost{background:transparent}
  button.sm{padding:5px 10px;font-size:12.5px;border-radius:9px}
  button.bad{border-color:rgba(224,115,111,.5);color:var(--bad)}
  .pill{display:inline-block;background:rgba(201,168,107,.14);color:var(--rose-deep);border-radius:999px;padding:3px 10px;font-size:12px;margin:2px 4px 2px 0}
  .pill.soft{background:var(--card-2);color:var(--ink-soft)}
  .grid-pj{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
  .pjcard{background:var(--card-2);border:1px solid var(--line);border-radius:14px;overflow:hidden;cursor:pointer;transition:border-color .15s}
  .pjcard:hover{border-color:var(--rose)}
  .pjcard .ph{aspect-ratio:3/4;background:#111015 center/cover no-repeat;display:flex;align-items:center;justify-content:center;font-size:40px;color:var(--line)}
  .pjcard .nm{padding:8px 10px;font-weight:500}
  .pjcard .st{padding:0 10px 8px;font-size:12px;color:var(--ink-soft)}
  .pjcard.new{display:flex;align-items:center;justify-content:center;min-height:180px;border-style:dashed;color:var(--rose-deep);font-size:15px}
  /* cabecera del personaje */
  .pjhead{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}
  .pjhead .face{width:104px;height:138px;border-radius:14px;background:#111015 center/cover no-repeat;border:1px solid var(--line);flex:none;
    display:flex;align-items:center;justify-content:center;font-size:36px;color:var(--line)}
  .pjhead .info{flex:1;min-width:220px}
  .pjhead .name{font-family:'Bodoni Moda',serif;font-size:28px;line-height:1.1}
  .bar{height:7px;background:var(--card-2);border-radius:99px;overflow:hidden;margin-top:6px;border:1px solid var(--line)}
  .bar i{display:block;height:100%;background:linear-gradient(90deg,var(--rose),var(--rose-deep))}
  .subtabs{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0 4px}
  .subtabs .t{padding:8px 14px;border-radius:999px;border:1px solid var(--line);background:var(--card-2);font-size:14px;cursor:pointer}
  .subtabs .t.on{background:var(--rose);color:#17140d;border-color:var(--rose);font-weight:500}
  .panel{display:none}.panel.on{display:block}
  /* chat */
  .chat{display:flex;flex-direction:column;gap:10px;max-height:60vh;overflow-y:auto;padding:6px 2px}
  .msg{max-width:84%;padding:10px 14px;border-radius:16px;white-space:pre-wrap;word-break:break-word}
  .msg.vos{align-self:flex-end;background:rgba(201,168,107,.18);border-bottom-right-radius:5px}
  .msg.pj{align-self:flex-start;background:var(--card-2);border:1px solid var(--line);border-bottom-left-radius:5px}
  .msg .acts{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
  .msg img.f{display:block;max-width:100%;border-radius:10px;margin-top:8px}
  .msg .meta{font-size:11px;color:var(--ink-soft);margin-top:4px}
  .compose{display:flex;gap:8px;align-items:flex-end;margin-top:10px}
  .compose textarea{min-height:44px;max-height:160px}
  .adj{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
  .adj img{height:54px;border-radius:8px;border:1px solid var(--line)}
  .adj .x{position:relative;display:inline-block}
  .adj .x b{position:absolute;top:-6px;right:-6px;background:var(--bad);color:#fff;border-radius:50%;width:18px;height:18px;font-size:11px;
    display:flex;align-items:center;justify-content:center;cursor:pointer}
  .quick{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
  .quick button{font-size:12.5px;padding:5px 10px;border-radius:999px;border-style:dashed}
  /* galería */
  .gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
  .gitem{background:var(--card-2);border:1px solid var(--line);border-radius:14px;overflow:hidden}
  .gitem img,.gitem video{width:100%;display:block;aspect-ratio:4/5;object-fit:cover;background:#000}
  .gitem .b{padding:8px 10px}
  .gitem .cap{font-size:12px;color:var(--ink-soft);max-height:52px;overflow:hidden}
  .gitem .acts{display:flex;gap:4px;flex-wrap:wrap;margin-top:6px}
  /* diario */
  .prop{background:var(--card-2);border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin-top:10px}
  .prop b{font-family:'Bodoni Moda',serif;font-weight:500;font-size:17px}
  .prop .d{font-size:13px;color:var(--ink-soft);margin:4px 0 8px}
  /* hoja */
  .hoja{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
  .hoja .v{aspect-ratio:3/4;border-radius:12px;background:#111015 center/cover no-repeat;border:1px solid var(--line);position:relative;
    display:flex;align-items:flex-end;justify-content:center;font-size:12px;color:var(--ink-soft)}
  .hoja .v span{background:rgba(0,0,0,.55);padding:2px 8px;border-radius:99px;margin-bottom:6px}
  .ovl{position:fixed;inset:0;background:rgba(8,7,11,.78);backdrop-filter:blur(4px);z-index:100;display:none;align-items:flex-start;
    justify-content:center;overflow-y:auto;padding:24px 14px}
  .ovl.on{display:flex}
  .sheet{background:var(--card);border:1px solid var(--line);border-radius:18px;max-width:520px;width:100%;padding:22px;box-shadow:var(--shadow);margin:auto}
  .sheet img.big{width:100%;border-radius:12px;margin:8px 0}
  .toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#2a2731;border:1px solid var(--rose);color:var(--ink);
    padding:10px 16px;border-radius:12px;z-index:200;font-size:14px;max-width:90vw;box-shadow:var(--shadow);display:none}
  .toast.on{display:block}
  .spin{display:inline-block;width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--rose);border-radius:50%;animation:sp .8s linear infinite;vertical-align:-2px;margin-right:6px}
  @keyframes sp{to{transform:rotate(360deg)}}
  .errbox{background:rgba(224,115,111,.12);border:1px solid rgba(224,115,111,.4);color:#f0a5a2;border-radius:12px;padding:10px 12px;font-size:13.5px;margin:8px 0}
  .lock{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--ink-soft)}
  @media(max-width:600px){.row,.row3{grid-template-columns:1fr}.hoja{grid-template-columns:repeat(2,1fr)}main{padding:10px}.card{padding:14px}.msg{max-width:92%}}
</style>
</head>
<body>
<header>
  <div class="brandrow">
    <div class="mono">SL</div>
    <div class="brand">Personajes<small>Tu persona digital · v%%VERSION%%</small></div>
    <div class="links"><a href="%%HOME%%">← Fotos</a><a href="%%VIDEOS%%">🎬 Videos</a></div>
  </div>
</header>

<main>
<!-- ───────── LISTA ───────── -->
<div class="card" id="vLista">
  <h2>Tus personajes</h2>
  <p class="hint">Una persona digital con cara fija, personalidad, memoria y humor. Le hablás, se saca fotos con tu ropa, te manda audios, habla a cámara y cada día te propone qué publicar.</p>
  <div class="grid-pj" id="gridPj"></div>
</div>

<!-- ───────── CREAR ───────── -->
<div class="card" id="vCrear" style="display:none">
  <h2>Nuevo personaje</h2>
  <p class="hint">Contá quién es. Con esto se arma su cerebro y, después, su cara. Todo se puede cambiar más adelante desde la ficha.</p>
  <h3>Su cara</h3>
  <p class="hint">Podés usar la cara de uno de tus avatares (los de Fotos → Avatares): queda como retrato aprobado y sólo falta generarle el cuerpo. O dejarlo para después y generar una cara nueva desde la ficha.</p>
  <div class="grid-pj" id="cAvatares"><div class="hint">Cargando avatares…</div></div>
  <input type="hidden" id="c-avatar">
  <div class="row">
    <div><label>Nombre</label><input id="c-nombre" placeholder="Ej: Luma"></div>
    <div><label>Género</label><select id="c-genero"><option value="mujer">Mujer</option><option value="hombre">Hombre</option></select></div>
  </div>
  <div class="row3">
    <div><label>Edad</label><input id="c-edad" placeholder="27"></div>
    <div><label>Ciudad</label><input id="c-ciudad" placeholder="Buenos Aires"></div>
    <div><label>Marca</label><input id="c-marca" placeholder="LUMA Íntima"></div>
  </div>
  <label>Rol</label><input id="c-rol" placeholder="Cara de la marca, modelo y creadora de contenido">
  <label>Personalidad</label><textarea id="c-personalidad" placeholder="Ej: cálida, curiosa, con humor ácido; le gusta la playa, el mate y los domingos lentos; segura pero no arrogante"></textarea>
  <label>Historia (opcional)</label><textarea id="c-historia" placeholder="De dónde viene, qué hace de su vida, qué la mueve"></textarea>
  <div class="row">
    <div><label>Cómo habla</label><input id="c-tono" placeholder="Ej: corto, directo, con chispa; cero cursi"></div>
    <div><label>Le gusta</label><input id="c-gustos" placeholder="Ej: el mar, la música de los 90, cocinar"></div>
  </div>
  <label>Nunca hace / nunca dice</label><input id="c-no_hace" placeholder="Ej: no habla de política, no usa emojis de fueguito">
  <h3>Apariencia (para generar su cara)</h3>
  <div class="row3">
    <div><label>Piel</label><input id="a-piel" placeholder="trigueña, cálida"></div>
    <div><label>Pelo</label><input id="a-pelo" placeholder="castaño oscuro, ondas, largo"></div>
    <div><label>Ojos</label><input id="a-ojos" placeholder="marrones, grandes"></div>
  </div>
  <div class="row3">
    <div><label>Contextura</label><input id="a-contextura" placeholder="curvas suaves, atlética"></div>
    <div><label>Altura</label><input id="a-altura" placeholder="1,68"></div>
    <div><label>Estilo</label><input id="a-estilo" placeholder="natural, poco maquillaje"></div>
  </div>
  <label>Rasgos / vibra</label><input id="a-rasgos" placeholder="pecas, sonrisa amplia, mirada tranquila">
  <div class="row">
    <div><label>Voz</label><select id="c-voz"></select></div>
    <div><label>Calidad de las fotos</label><select id="c-calidad"><option value="1K">1K (barata)</option><option value="2K" selected>2K</option><option value="4K">4K</option></select></div>
  </div>
  <div style="display:flex;gap:10px;margin-top:16px;flex-wrap:wrap">
    <button class="go" id="btnCrear">Crear personaje</button>
    <button class="ghost" onclick="verLista()">Cancelar</button>
  </div>
</div>

<!-- ───────── PERSONAJE ───────── -->
<div id="vPj" style="display:none">
  <div class="card">
    <div class="pjhead">
      <div class="face" id="pjFace">👤</div>
      <div class="info">
        <div class="name" id="pjNombre"></div>
        <div class="hint" id="pjRol"></div>
        <div id="pjChips"></div>
        <div style="font-size:12px;color:var(--ink-soft);margin-top:8px">Energía <span id="pjEnergiaTxt"></span></div>
        <div class="bar"><i id="pjEnergia" style="width:0"></i></div>
      </div>
      <div><button class="ghost sm" onclick="verLista()">← Todos</button></div>
    </div>
    <div class="subtabs">
      <div class="t on" data-t="charla">💬 Charla</div>
      <div class="t" data-t="hoy">☀️ Hoy</div>
      <div class="t" data-t="galeria">🖼️ Galería</div>
      <div class="t" data-t="ficha">🪪 Ficha</div>
    </div>
    <div id="sinCara" class="errbox" style="display:none">Todavía no tiene cara. Andá a <a href="#" onclick="subtab('ficha');return false">Ficha</a> y aprobá un retrato: sin eso puede charlar, pero no sacarse fotos.</div>

    <!-- CHARLA -->
    <div class="panel on" id="p-charla">
      <div class="chat" id="chat"></div>
      <div class="quick">
        <button onclick="quick('Contame cómo estás hoy')">Cómo estás</button>
        <button onclick="quick('Sacate una foto para el feed, vos elegí la escena')">Sacate una foto</button>
        <button onclick="quick('Proponeme 3 ideas de contenido para esta semana')">Ideas de contenido</button>
        <button onclick="quick('Escribime un caption para la última foto')">Caption</button>
      </div>
      <div class="adj" id="adj"></div>
      <div class="compose">
        <button class="sm" title="Adjuntar foto de una prenda" onclick="$('#inAdj').click()">📎</button>
        <input type="file" id="inAdj" accept="image/*" multiple style="display:none">
        <textarea id="inMsg" placeholder="Escribile… (Enter envía, Shift+Enter salta línea)"></textarea>
        <button class="go" id="btnEnviar">Enviar</button>
      </div>
      <p class="hint" style="margin-top:8px">Adjuntá la foto real de una prenda y pedile que se la ponga. Las fotos salen a la calidad de la ficha. <a href="#" onclick="borrarChat();return false">Borrar la charla</a></p>
    </div>

    <!-- HOY -->
    <div class="panel" id="p-hoy">
      <div id="hoyBox"><span class="spin"></span>Escribiendo su diario…</div>
    </div>

    <!-- GALERÍA -->
    <div class="panel" id="p-galeria">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px">
        <button class="sm" onclick="abrirFotoLibre()">📸 Pedir una foto a mano</button>
        <button class="sm" onclick="abrirHablar(null)">🗣️ Que hable a cámara</button>
        <button class="sm" onclick="abrirMovete(null)">🕺 Movete vos</button>
        <span class="hint" style="margin:0">Cada foto se puede mandar a Videos para el video de vidriera.</span>
      </div>
      <div id="enCurso" style="display:none"></div>
      <div class="gal" id="gal"></div>
    </div>

    <!-- FICHA -->
    <div class="panel" id="p-ficha">
      <h3>Cara y cuerpo (hoja de identidad)</h3>
      <p class="hint">Primero un retrato: se genera con IA, se sube una foto, o se toma de un avatar de la pestaña Avatares. Cuando te guste, aprobalo. Después la hoja de 3 vistas sale sola de UNA imagen (perfil, cuerpo entero y espalda): todas las fotos y videos miran estas 4 imágenes, por eso la cara no cambia.</p>
      <div class="hoja" id="hoja"></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
        <button class="go" id="btnRetratoGen">✨ Generar retrato</button>
        <button onclick="$('#inRetrato').click()">⬆️ Subir una foto</button>
        <input type="file" id="inRetrato" accept="image/*" style="display:none">
        <button id="btnDesdeAvatar">👥 Usar un avatar</button>
        <button id="btnHoja">🪪 Generar hoja (3 vistas)</button>
      </div>
      <p class="hint" id="hojaHint"></p>

      <h3>Quién es</h3>
      <div class="row">
        <div><label>Nombre</label><input id="f-nombre"></div>
        <div><label>Género</label><select id="f-genero"><option value="mujer">Mujer</option><option value="hombre">Hombre</option></select></div>
      </div>
      <div class="row3">
        <div><label>Edad</label><input id="f-edad"></div>
        <div><label>Ciudad</label><input id="f-ciudad"></div>
        <div><label>Marca</label><input id="f-marca"></div>
      </div>
      <label>Rol</label><input id="f-rol">
      <label>Personalidad</label><textarea id="f-personalidad"></textarea>
      <label>Historia</label><textarea id="f-historia"></textarea>
      <div class="row">
        <div><label>Cómo habla</label><input id="f-tono"></div>
        <div><label>Le gusta</label><input id="f-gustos"></div>
      </div>
      <label>Nunca hace / nunca dice</label><input id="f-no_hace">
      <div class="row3">
        <div><label>Piel</label><input id="fa-piel"></div>
        <div><label>Pelo</label><input id="fa-pelo"></div>
        <div><label>Ojos</label><input id="fa-ojos"></div>
      </div>
      <div class="row3">
        <div><label>Contextura</label><input id="fa-contextura"></div>
        <div><label>Altura</label><input id="fa-altura"></div>
        <div><label>Estilo</label><input id="fa-estilo"></div>
      </div>
      <label>Rasgos / vibra</label><input id="fa-rasgos">
      <div class="row3">
        <div><label>Voz</label><select id="f-voz"></select></div>
        <div><label>Calidad de fotos</label><select id="f-calidad"><option value="1K">1K</option><option value="2K">2K</option><option value="4K">4K</option></select></div>
        <div><label>Formato de fotos</label><select id="f-formato"><option value="4:5">4:5 (feed)</option><option value="1:1">1:1</option><option value="9:16">9:16 (reel/story)</option><option value="3:4">3:4</option><option value="16:9">16:9</option></select></div>
      </div>
      <h3>Lo que recuerda</h3>
      <p class="hint">Hechos que fue guardando de las charlas. Podés borrar o agregar líneas.</p>
      <textarea id="f-memoria" style="min-height:90px"></textarea>
      <div style="display:flex;gap:10px;margin-top:14px;flex-wrap:wrap">
        <button class="go" id="btnGuardarFicha">Guardar ficha</button>
        <button class="bad" id="btnBorrarPj">Borrar personaje</button>
      </div>
    </div>
  </div>
</div>
</main>

<!-- ───────── OVERLAYS ───────── -->
<div class="ovl" id="ovRetrato"><div class="sheet">
  <h2>¿Es ella?</h2>
  <p class="hint">Si te gusta, aprobala: de acá en más TODAS las fotos y videos van a tener esta cara. Si no, generá otra (cuesta lo mismo que una foto 2K).</p>
  <img class="big" id="retratoImg" alt="">
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <button class="go" id="btnRetratoOk">✓ Aprobar</button>
    <button id="btnRetratoOtro">↻ Otra</button>
    <button class="ghost" onclick="cerrar('ovRetrato')">Cancelar</button>
  </div>
</div></div>

<div class="ovl" id="ovAvatar"><div class="sheet">
  <h2>Usar un avatar</h2>
  <p class="hint">Los de la pestaña Avatares de Fotos. La cara del avatar pasa a ser la del personaje.</p>
  <div id="avList" class="grid-pj"></div>
  <div style="margin-top:12px"><button class="ghost" onclick="cerrar('ovAvatar')">Cancelar</button></div>
</div></div>

<div class="ovl" id="ovFoto"><div class="sheet">
  <h2>Pedir una foto</h2>
  <label>Escena y luz</label><input id="pf-escena" placeholder="terraza al atardecer, luz cálida de costado">
  <label>Outfit</label><input id="pf-outfit" placeholder="la prenda de la foto con un jean claro">
  <label>Encuadre</label><input id="pf-encuadre" placeholder="plano medio, cámara a la altura de los ojos">
  <label>Expresión</label><input id="pf-expresion" placeholder="risa suelta, mirando fuera de cámara">
  <label>Indicaciones extra</label><input id="pf-extra" placeholder="opcional">
  <div class="row"><div><label>Calidad</label><select id="pf-calidad"><option value="">La de la ficha</option><option value="1K">1K</option><option value="2K">2K</option><option value="4K">4K</option></select></div>
  <div><label>Formato</label><select id="pf-formato"><option value="">El de la ficha</option><option value="4:5">4:5</option><option value="1:1">1:1</option><option value="9:16">9:16</option><option value="3:4">3:4</option><option value="16:9">16:9</option></select></div></div>
  <p class="hint">Si adjuntaste prendas en la charla, van con este pedido.</p>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
    <button class="go" id="btnFotoLibre">📸 Generar</button>
    <button class="ghost" onclick="cerrar('ovFoto')">Cancelar</button>
  </div>
</div></div>

<div class="ovl" id="ovHablar"><div class="sheet">
  <h2>Que hable a cámara</h2>
  <p class="hint">Un clip de <span id="hbSeg">8</span> segundos: Veo 3.1 le pone la voz y mueve los labios sobre la foto elegida. Hasta <span id="hbMax">22</span> palabras.</p>
  <div id="hbFoto" style="display:flex;gap:10px;align-items:center;margin-bottom:6px"></div>
  <label>Lo que dice</label><textarea id="hb-texto" placeholder="Hola, soy Luma. Esta semana llegó la colección nueva y la quiero mostrar toda."></textarea>
  <div class="hint"><span id="hbPalabras">0</span> palabras · <a href="#" onclick="hbSugerir();return false">que lo escriba ella</a></div>
  <div class="row"><div><label>Motor</label><select id="hb-motor"></select></div>
  <div><label>Formato</label><select id="hb-formato"><option value="9:16">Vertical 9:16</option><option value="16:9">Horizontal 16:9</option></select></div></div>
  <div id="hbEstado"></div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
    <button class="go" id="btnHablar">🎬 Grabar</button>
    <button class="ghost" onclick="cerrar('ovHablar')">Cerrar</button>
  </div>
</div></div>

<div class="ovl" id="ovMovete"><div class="sheet">
  <h2>Movete vos</h2>
  <p class="hint">Grabate vos haciendo el contenido (celular quieto, luz pareja, hasta <span id="mvMax">20</span> segundos). Ella copia tus movimientos, tus gestos y tu boca. La ropa la saca de la foto de referencia, no de tu video: grabate en calza y remera y elegí la foto de ella con la prenda que quieras mostrar.</p>
  <div id="mvFoto" style="display:flex;gap:10px;align-items:center;margin-bottom:6px"></div>
  <label>Modo</label>
  <select id="mv-modo"><option value="replace">Reemplazo: ella entra en TU video (queda tu fondo y tu audio)</option><option value="move">Animación: ella copia tus movimientos sobre el fondo de SU foto</option></select>
  <label>Resolución</label>
  <select id="mv-res"><option value="480p">480p · rápida (~15 s de proceso por segundo de video)</option><option value="580p">580p · media (~25 s por segundo)</option><option value="720p">720p · la mejor, lenta (~40 s por segundo; 16 s de video pueden ser 12 min o más)</option></select>
  <label>Tu video</label>
  <input type="file" id="mv-video" accept="video/*">
  <div class="hint" id="mvInfo">Elegí un video.</div>
  <label>Fotos reales de la prenda (opcional, hasta 3)</label>
  <input type="file" id="mv-prendas" accept="image/*" multiple>
  <div class="hint">Con esto el inspector de prenda revisa 3 cuadros del video terminado contra la prenda real y te dice si se corrió. Sin fotos, compara contra la foto de referencia.</div>
  <div id="mvEstado"></div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
    <button class="go" id="btnMovete" disabled>🕺 Generar</button>
    <button class="ghost" onclick="cerrar('ovMovete')">Cerrar</button>
  </div>
  <details style="margin-top:14px"><summary class="hint" style="cursor:pointer;margin:0">¿Un trabajo que fal terminó pero acá se perdió? Recuperarlo con el request id</summary>
    <p class="hint">En el panel de fal (fal.ai → Requests) abrí el trabajo y tocá "Copy request id". Pegalo acá con la misma foto de referencia y el mismo modo: la app lo busca en la cola de fal, baja el clip, lo pasa por el inspector y lo guarda en la galería.</p>
    <div class="row"><div><label>Request id</label><input id="mv-rid" placeholder="a5eb36a3-…"></div><div><label>Segundos del video (para el costo)</label><input id="mv-rseg" placeholder="16"></div></div>
    <button class="sm" id="btnRecuperar" style="margin-top:8px">↩︎ Recuperar de fal</button>
  </details>
</div></div>

<div class="ovl" id="ovMover"><div class="sheet">
  <h2>Que se mueva</h2>
  <p class="hint">De esta foto sale un clip corto con movimiento natural: respira, camina, gira, se acomoda el pelo. Nada raro, nada exagerado. Van por los motores de fal, que no rechazan lencería ni bikinis (Veo sí). La cara, el cuerpo, la prenda y el fondo son los de la foto.</p>
  <div id="mrFoto" style="display:flex;gap:10px;align-items:center;margin-bottom:6px"></div>
  <label>Movimiento</label><select id="mr-mov"></select>
  <div id="mrLibreBox" style="display:none"><label>Qué hace (en castellano, se traduce solo)</label><input id="mr-texto" placeholder="se sienta en el borde de la cama y mira a cámara"></div>
  <div id="mrExtraBox"><label>Algo más (opcional)</label><input id="mr-extra" placeholder="ej: con una sonrisa tímida"></div>
  <div class="row"><div><label>Motor</label><select id="mr-motor"></select></div>
  <div><label>Duración</label><select id="mr-dur"></select></div></div>
  <div class="hint" id="mrCosto"></div>
  <div id="mrEstado"></div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
    <button class="go" id="btnMover">✨ Generar</button>
    <button class="ghost" onclick="cerrar('ovMover')">Cerrar</button>
  </div>
</div></div>

<div class="ovl" id="ovVer"><div class="sheet" style="max-width:640px">
  <div id="verBox"></div>
  <div style="margin-top:10px"><button class="ghost" onclick="cerrar('ovVer')">Cerrar</button></div>
</div></div>

<div class="toast" id="toast"></div>

<script>
const API = "%%PREFIX%%/api";
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
let CFG = {}, PJS = [], PJ = null, ADJ = [], CHAT = [], GAL = [], RETRATO_CAND = null, HB_JOB = null, HB_FOTO = null;
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function toast(m, ms){ const t = $("#toast"); t.textContent = m; t.classList.add("on"); clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("on"), ms || 3200); }
function abrir(id){ $("#" + id).classList.add("on"); } function cerrar(id){ $("#" + id).classList.remove("on"); }
async function api(path, opts){
  const r = await fetch(API + path, Object.assign({headers: {"Content-Type": "application/json"}}, opts || {}));
  if(r.status === 401){ location.href = "/auth/login"; throw new Error("login"); }
  const ct = r.headers.get("content-type") || "";
  if(!ct.includes("json")){ if(!r.ok) throw new Error("HTTP " + r.status); return r; }
  const d = await r.json();
  if(!r.ok) throw new Error(d.detail || d.error || ("HTTP " + r.status));
  return d;
}
const post = (p, body) => api(p, {method: "POST", body: JSON.stringify(body || {})});
const del = p => api(p, {method: "DELETE"});
function fileToDataURL(f){ return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); }); }
function achicar(file, maxDim){ return new Promise((res, rej) => { const img = new Image(); img.onload = () => { try{ URL.revokeObjectURL(img.src); }catch(e){}
  let w = img.naturalWidth, h = img.naturalHeight; if(Math.max(w, h) > maxDim){ const s = maxDim / Math.max(w, h); w = Math.round(w * s); h = Math.round(h * s); }
  const c = document.createElement("canvas"); c.width = w; c.height = h; c.getContext("2d").drawImage(img, 0, 0, w, h); res(c.toDataURL("image/jpeg", 0.9)); };
  img.onerror = () => rej(new Error("No se pudo leer la imagen")); img.src = URL.createObjectURL(file); }); }
function ocupado(btn, on, txt){ btn.disabled = on; if(on){ btn._t = btn.innerHTML; btn.innerHTML = '<span class="spin"></span>' + (txt || "Un momento…"); } else if(btn._t){ btn.innerHTML = btn._t; } }
const vocesOpts = (g, sel) => (CFG.voces && CFG.voces[g] || []).map(v => `<option value="${v[0]}" ${v[0] === sel ? "selected" : ""}>${esc(v[1])}</option>`).join("");
function imgUrl(vista){ return API + "/" + PJ.id + "/img/" + vista + "?t=" + (PJ.actualizado || ""); }

/* ───────── navegación ───────── */
function verLista(){ $("#vLista").style.display = ""; $("#vCrear").style.display = "none"; $("#vPj").style.display = "none"; PJ = null; history.replaceState(null, "", location.pathname); cargarLista(); }
function verCrear(){ $("#vLista").style.display = "none"; $("#vCrear").style.display = ""; $("#vPj").style.display = "none"; $("#c-voz").innerHTML = vocesOpts($("#c-genero").value); $("#c-avatar").value = ""; cargarAvataresCrear(); }
async function cargarAvataresCrear(){
  const g = $("#cAvatares");
  try{ const d = await api("/avatares"); g.innerHTML = "";
    const nuevo = document.createElement("div"); nuevo.className = "pjcard new"; nuevo.style.minHeight = "120px"; nuevo.textContent = "✨ Cara nueva (después)"; nuevo.dataset.av = "";
    g.appendChild(nuevo);
    for(const av of d.avatares){ const c = document.createElement("div"); c.className = "pjcard"; c.dataset.av = av.id;
      c.innerHTML = `<div class="ph" style="background-image:url('%%HOME_API%%/api/avatars/${av.id}/ref')"></div><div class="nm">${esc(av.name)}</div><div class="st">${esc(av.gender)}</div>`;
      g.appendChild(c); }
    $$("#cAvatares .pjcard").forEach(c => c.onclick = () => { $$("#cAvatares .pjcard").forEach(x => x.style.borderColor = ""); c.style.borderColor = "var(--rose)"; $("#c-avatar").value = c.dataset.av;
      const av = d.avatares.find(a => a.id === c.dataset.av); if(av){ if(!$("#c-nombre").value) $("#c-nombre").value = av.name || ""; $("#c-genero").value = av.gender; $("#c-voz").innerHTML = vocesOpts(av.gender); } });
    nuevo.style.borderColor = "var(--rose)";
    if(!d.avatares.length) g.innerHTML = '<div class="hint">No tenés avatares todavía: la cara se genera después, desde la ficha.</div>';
  }catch(e){ g.innerHTML = '<div class="hint">No pude cargar los avatares (' + esc(e.message) + ').</div>'; }
}
$("#c-genero").onchange = () => { $("#c-voz").innerHTML = vocesOpts($("#c-genero").value); };
$("#f-genero").onchange = () => { $("#f-voz").innerHTML = vocesOpts($("#f-genero").value, PJ && PJ.voz); };
function subtab(t){ $$(".subtabs .t").forEach(x => x.classList.toggle("on", x.dataset.t === t)); $$(".panel").forEach(p => p.classList.toggle("on", p.id === "p-" + t));
  if(t === "hoy") cargarHoy(false); if(t === "galeria"){ cargarGaleria(); cargarJobs(); } if(t === "ficha") pintarFicha(); if(t === "charla") scrollChat(); }
$$(".subtabs .t").forEach(x => x.onclick = () => subtab(x.dataset.t));

async function cargarLista(){
  try{ const d = await api("/lista"); PJS = d.personajes || []; }catch(e){ toast("No pude cargar: " + e.message); PJS = []; }
  const g = $("#gridPj"); g.innerHTML = "";
  for(const p of PJS){
    const c = document.createElement("div"); c.className = "pjcard";
    const st = p.estado || {};
    c.innerHTML = `<div class="ph" ${p.tiene_retrato ? `style="background-image:url('${API}/${p.id}/img/retrato')"` : ""}>${p.tiene_retrato ? "" : "👤"}</div>
      <div class="nm">${esc(p.nombre)}</div><div class="st">${esc(st.humor || "")} · ⚡${st.energia ?? ""}${st.racha ? " · 🔥" + st.racha : ""}</div>`;
    c.onclick = () => abrirPj(p.id); g.appendChild(c);
  }
  const n = document.createElement("div"); n.className = "pjcard new"; n.textContent = "＋ Nuevo personaje"; n.onclick = verCrear; g.appendChild(n);
}

$("#btnCrear").onclick = async () => {
  const b = $("#btnCrear"); ocupado(b, true, "Creando…");
  try{
    const body = {nombre: $("#c-nombre").value, genero: $("#c-genero").value, edad: $("#c-edad").value, ciudad: $("#c-ciudad").value,
      marca: $("#c-marca").value, rol: $("#c-rol").value, personalidad: $("#c-personalidad").value, historia: $("#c-historia").value,
      tono: $("#c-tono").value, gustos: $("#c-gustos").value, no_hace: $("#c-no_hace").value, voz: $("#c-voz").value, calidad: $("#c-calidad").value,
      avatar_id: $("#c-avatar").value,
      apariencia: {piel: $("#a-piel").value, pelo: $("#a-pelo").value, ojos: $("#a-ojos").value, contextura: $("#a-contextura").value,
        altura: $("#a-altura").value, estilo: $("#a-estilo").value, rasgos: $("#a-rasgos").value}};
    const d = await post("/crear", body);
    toast(d.personaje.tiene_retrato ? "✓ " + d.personaje.nombre + " ya tiene cara. Generale la hoja (3 vistas) para fijar el cuerpo." : "✓ " + d.personaje.nombre + " ya existe. Ahora dale una cara.", 5000);
    await abrirPj(d.personaje.id); subtab("ficha");
  }catch(e){ toast(e.message); } finally{ ocupado(b, false); }
};

/* ───────── personaje ───────── */
async function abrirPj(id){
  try{ const d = await api("/" + id); PJ = d.personaje; }catch(e){ toast(e.message); return; }
  $("#vLista").style.display = "none"; $("#vCrear").style.display = "none"; $("#vPj").style.display = "";
  history.replaceState(null, "", location.pathname + "?pj=" + id);
  pintarHead(); ADJ = []; pintarAdj(); await cargarChat(); subtab("charla");
  if(!PJ.tiene_retrato){ $("#sinCara").style.display = ""; } else { $("#sinCara").style.display = "none"; }
}
function pintarHead(){
  const st = PJ.estado || {};
  $("#pjFace").style.backgroundImage = PJ.tiene_retrato ? `url('${imgUrl("retrato")}')` : ""; $("#pjFace").textContent = PJ.tiene_retrato ? "" : "👤";
  $("#pjNombre").textContent = PJ.nombre; $("#pjRol").textContent = [PJ.rol, PJ.marca].filter(Boolean).join(" · ");
  const chips = [];
  if(st.humor) chips.push(`<span class="pill">${esc(st.humor)}</span>`);
  chips.push(`<span class="pill soft">🔥 racha ${st.racha || 0} día${st.racha == 1 ? "" : "s"}</span>`);
  if(st.dias_sin_hablar >= 2) chips.push(`<span class="pill soft">😶 ${st.dias_sin_hablar} días sin hablar</span>`);
  chips.push(`<span class="pill soft">💬 ${st.charlas || 0} · 📸 ${st.fotos || 0}</span>`);
  $("#pjChips").innerHTML = chips.join(""); $("#pjEnergia").style.width = (st.energia || 0) + "%"; $("#pjEnergiaTxt").textContent = (st.energia || 0) + "/100";
  $("#sinCara").style.display = PJ.tiene_retrato ? "none" : "";
}

/* ───────── charla ───────── */
async function cargarChat(){ try{ CHAT = (await api("/" + PJ.id + "/chat")).mensajes || []; }catch(e){ CHAT = []; } pintarChat(); }
function pintarChat(){
  const c = $("#chat"); c.innerHTML = "";
  if(!CHAT.length){ c.innerHTML = `<div class="hint">Todavía no hablaron. Decile hola, o pedile que se saque una foto.</div>`; return; }
  for(const m of CHAT) c.appendChild(nodoMsg(m));
  scrollChat();
}
function scrollChat(){ const c = $("#chat"); c.scrollTop = c.scrollHeight; }
function nodoMsg(m){
  const d = document.createElement("div"); d.className = "msg " + (m.de === "vos" ? "vos" : "pj");
  d.appendChild(document.createTextNode(m.texto || ""));
  if(m.de === "vos" && m.adjuntos){ const x = document.createElement("div"); x.className = "meta"; x.textContent = "📎 " + m.adjuntos + " foto(s)"; d.appendChild(x); }
  if(m.foto_src){ const i = document.createElement("img"); i.className = "f"; i.src = m.foto_src; i.onclick = () => verFoto(m.foto_item); d.appendChild(i); }
  if(m.de === "pj"){
    const a = document.createElement("div"); a.className = "acts";
    const v = document.createElement("button"); v.className = "sm"; v.textContent = "🎙️ Escuchar"; v.onclick = () => escuchar(v, m.texto); a.appendChild(v);
    if(m.foto_pedido && !m.foto_src){ const f = document.createElement("button"); f.className = "sm go"; f.textContent = "📸 Sacar la foto (" + precioFoto() + ")"; f.onclick = () => sacarFoto(f, m.foto_pedido, d, "chat"); a.appendChild(f); }
    d.appendChild(a);
  }
  return d;
}
function precioFoto(q){ const p = (CFG.precios || {})[q || PJ.calidad || "2K"]; return p ? "US$" + p.toFixed(3) : ""; }
async function enviar(){
  const t = $("#inMsg").value.trim(); if(!t && !ADJ.length) return;
  const b = $("#btnEnviar"); ocupado(b, true, "…");
  if(!CHAT.length) $("#chat").innerHTML = "";
  const mio = {de: "vos", texto: t || "Te mando esta prenda.", adjuntos: ADJ.length}; CHAT.push(mio); $("#chat").appendChild(nodoMsg(mio)); scrollChat();
  $("#inMsg").value = "";
  const pensando = document.createElement("div"); pensando.className = "msg pj"; pensando.innerHTML = '<span class="spin"></span>escribiendo…'; $("#chat").appendChild(pensando); scrollChat();
  try{
    const d = await post("/" + PJ.id + "/chat", {texto: t, adjuntos: ADJ});
    pensando.remove();
    const m = {de: "pj", texto: d.respuesta, foto_pedido: d.foto_pedido}; CHAT.push(m); $("#chat").appendChild(nodoMsg(m)); scrollChat();
    if(d.estado){ PJ.estado = d.estado; pintarHead(); }
    if(d.recordo && d.recordo.length) toast("🧠 Guardó: " + d.recordo.join(" · "), 4500);
  }catch(e){ pensando.remove(); toast(e.message, 6000); }
  finally{ ocupado(b, false); }
}
$("#btnEnviar").onclick = enviar;
$("#inMsg").addEventListener("keydown", e => { if(e.key === "Enter" && !e.shiftKey){ e.preventDefault(); enviar(); } });
function quick(t){ $("#inMsg").value = t; enviar(); }
$("#inAdj").onchange = async e => { for(const f of [...e.target.files].slice(0, (CFG.max_adjuntos || 4) - ADJ.length)){ try{ ADJ.push(await achicar(f, 1600)); }catch(err){ toast(err.message); } } e.target.value = ""; pintarAdj(); };
function pintarAdj(){ $("#adj").innerHTML = ADJ.map((a, i) => `<span class="x"><img src="${a}"><b onclick="ADJ.splice(${i},1);pintarAdj()">×</b></span>`).join(""); }
async function borrarChat(){ if(!confirm("¿Borrar toda la charla? (La memoria de la ficha queda.)")) return; await del("/" + PJ.id + "/chat"); CHAT = []; pintarChat(); }

async function sacarFoto(btn, pedido, nodo, origen){
  if(!PJ.tiene_retrato){ toast("Primero aprobá un retrato en la ficha."); subtab("ficha"); return; }
  ocupado(btn, true, "Sacando la foto…");
  try{
    const d = await post("/" + PJ.id + "/foto", {pedido, adjuntos: ADJ, origen});
    const it = d.foto; PJ.estado.fotos = (PJ.estado.fotos || 0) + 1; pintarHead();
    if(nodo){ const i = document.createElement("img"); i.className = "f"; i.src = it.src; i.onclick = () => verFoto(it); nodo.insertBefore(i, nodo.querySelector(".acts")); btn.remove(); }
    const idx = CHAT.findIndex(m => m.foto_pedido === pedido); if(idx >= 0){ CHAT[idx].foto_src = it.src; CHAT[idx].foto_item = it; }
    ADJ = []; pintarAdj(); toast("✓ Foto lista. Está en la galería.");
    scrollChat();
    return it;
  }catch(e){ toast(e.message, 7000); ocupado(btn, false); }
}
async function escuchar(btn, texto){
  ocupado(btn, true, "…");
  try{
    const r = await fetch(API + "/" + PJ.id + "/voz", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({texto})});
    if(!r.ok){ const d = await r.json().catch(() => ({})); throw new Error(d.detail || "HTTP " + r.status); }
    const url = URL.createObjectURL(await r.blob()); const a = new Audio(url); a.play();
    ocupado(btn, false); btn.textContent = "🔊 Sonando"; a.onended = () => { btn.textContent = "🎙️ Escuchar"; };
  }catch(e){ toast(e.message, 6000); ocupado(btn, false); }
}

/* ───────── hoy ───────── */
async function cargarHoy(forzar){
  const box = $("#hoyBox"); box.innerHTML = '<span class="spin"></span>Escribiendo su diario…';
  try{
    const d = await api("/" + PJ.id + "/hoy" + (forzar ? "?forzar=1" : ""));
    if(d.estado){ PJ.estado = d.estado; pintarHead(); }
    const h = d.hoy || {};
    let html = `<h2>${esc(PJ.nombre)} hoy <span class="pill">${esc(h.humor || "")}</span></h2><p style="white-space:pre-wrap">${esc(h.texto || "")}</p>
      <div class="hint">${esc(h.fecha || "")} · <a href="#" onclick="cargarHoy(true);return false">reescribir el día</a></div><h3>Qué haría hoy</h3>`;
    (h.propuestas || []).forEach((p, i) => {
      html += `<div class="prop"><b>${esc(p.titulo)}</b><div class="d">${esc(p.por_que)}</div>
        <div class="hint" style="margin:0 0 6px">📍 ${esc(p.escena)} · 👗 ${esc(p.outfit)} · 🎥 ${esc(p.encuadre)}</div>
        <div style="font-size:13px;white-space:pre-wrap">${esc(p.caption)}</div>
        <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap"><button class="sm go" onclick="hacerPropuesta(this,${i})">📸 Hacelo (${precioFoto()})</button>
        <button class="sm" onclick="copiar(${JSON.stringify(p.caption || "")})">📋 Caption</button></div></div>`;
    });
    if(d.anteriores && d.anteriores.length){
      html += `<h3>Días anteriores</h3>` + d.anteriores.map(e => `<div class="hint"><b>${esc(e.fecha)}</b> · ${esc(e.humor)} — ${esc(e.texto)}</div>`).join("");
    }
    box.innerHTML = html; box._props = h.propuestas || [];
  }catch(e){ box.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}
async function hacerPropuesta(btn, i){ const p = ($("#hoyBox")._props || [])[i]; if(!p) return; const it = await sacarFoto(btn, p, null, "diario"); if(it){ ocupado(btn, false); btn.textContent = "✓ Hecha (en la galería)"; btn.disabled = true; } }
function copiar(t){ navigator.clipboard && navigator.clipboard.writeText(t).then(() => toast("Copiado")); }

/* ───────── galería ───────── */
async function cargarGaleria(){
  const g = $("#gal"); g.innerHTML = '<span class="spin"></span>';
  try{ GAL = (await api("/" + PJ.id + "/galeria")).items || []; }catch(e){ g.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  g.innerHTML = GAL.length ? "" : '<div class="hint">Todavía no hay fotos. Pedile una en la charla, o desde "Hoy".</div>';
  for(const it of GAL){
    const d = document.createElement("div"); d.className = "gitem";
    const src = it.tipo === "video" ? "" : API + "/" + PJ.id + "/galeria/" + it.id;
    d.innerHTML = (it.tipo === "video" ? `<video src="${API}/clip/${it.id}" controls playsinline preload="metadata"></video>` : `<img src="${src}" loading="lazy">`) +
      `<div class="b"><div style="font-size:13.5px;font-weight:500">${esc(it.titulo || (it.tipo === "video" ? "Hablando a cámara" : "Foto"))}</div>
      <div class="cap">${esc(it.caption || "")}</div>${it.qc ? `<div class="cap" style="color:${it.qc.puntaje >= 8 ? "var(--ok)" : "var(--bad)"}">🔍 ${it.qc.puntaje}/10${it.qc.diferencias && it.qc.diferencias.length ? " · " + esc(it.qc.diferencias[0]) : ""}</div>` : ""}<div class="acts"></div></div>`;
    const acts = d.querySelector(".acts");
    const mk = (t, fn, cls) => { const b = document.createElement("button"); b.className = "sm " + (cls || ""); b.textContent = t; b.onclick = fn; acts.appendChild(b); return b; };
    if(it.tipo === "foto"){
      d.querySelector("img").onclick = () => verFoto(Object.assign({src}, it));
      mk("🔍", () => verFoto(Object.assign({src}, it)));
      mk("🎬 Video", () => aVideos(it));
      mk("🗣️ Hablar", () => abrirHablar(it));
      mk("🕺 Movete", () => abrirMovete(it));
      mk("✨ Que se mueva", () => abrirMover(it));
      mk("↻", (e) => rehacer(e.target, it), "");
    } else {
      mk("⬇️", () => { window.open(API + "/clip/" + it.id, "_blank"); });
    }
    if(it.caption) mk("📋", () => copiar(it.caption));
    mk("🗑", async () => { if(!confirm("¿Borrar?")) return; await del("/" + PJ.id + "/galeria/" + it.id); cargarGaleria(); }, "bad");
    g.appendChild(d);
  }
}
function verFoto(it){
  const src = it.src || (API + "/" + PJ.id + "/galeria/" + it.id);
  $("#verBox").innerHTML = `<img src="${src}" style="width:100%;border-radius:12px"><h3>${esc(it.titulo || "")}</h3>
    <textarea id="verCap" style="min-height:90px">${esc(it.caption || "")}</textarea>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px"><button class="sm" onclick="guardarCap('${it.id}')">💾 Guardar caption</button>
    <a class="sm" href="${src}" download="${esc(PJ.nombre)}-${it.id}.jpg"><button class="sm">⬇️ Bajar</button></a>
    <button class="sm" onclick='aVideos(${JSON.stringify({id: it.id})})'>🎬 Mandar a Videos</button></div>`;
  abrir("ovVer");
}
async function guardarCap(fid){ try{ await post("/" + PJ.id + "/galeria/" + fid + "/caption", {caption: $("#verCap").value}); toast("Guardado"); }catch(e){ toast(e.message); } }
async function rehacer(btn, it){
  const extra = prompt("¿Algo que cambiar? (vacío = la misma toma de nuevo)", "") ; if(extra === null) return;
  ocupado(btn, true, "…");
  try{ await post("/" + PJ.id + "/foto/" + it.id + "/rehacer", {extra, adjuntos: ADJ}); toast("✓ Lista"); cargarGaleria(); }catch(e){ toast(e.message, 6000); ocupado(btn, false); }
}
async function aVideos(it){
  // La foto viaja por localStorage: la pestaña Videos la levanta al abrirse.
  try{
    const r = await fetch(API + "/" + PJ.id + "/galeria/" + it.id); const b = await r.blob();
    const url = await new Promise(res => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(b); });
    localStorage.setItem("sl_pase_foto", url); localStorage.setItem("sl_pase_nombre", PJ.nombre || "");
    location.href = CFG.videos_prefix || "/videos";
  }catch(e){ toast("No pude pasar la foto: " + e.message); }
}
function abrirFotoLibre(){ if(!PJ.tiene_retrato){ toast("Primero aprobá un retrato en la ficha."); subtab("ficha"); return; } abrir("ovFoto"); }
$("#btnFotoLibre").onclick = async () => {
  const pedido = {titulo: "Pedido a mano", escena: $("#pf-escena").value, outfit: $("#pf-outfit").value, encuadre: $("#pf-encuadre").value,
    expresion: $("#pf-expresion").value, extra: $("#pf-extra").value, calidad: $("#pf-calidad").value, formato: $("#pf-formato").value};
  const it = await sacarFoto($("#btnFotoLibre"), pedido, null, "manual"); if(it){ ocupado($("#btnFotoLibre"), false); cerrar("ovFoto"); cargarGaleria(); }
};

/* ───────── hablar a cámara ───────── */
function abrirHablar(it){
  HB_FOTO = it; const box = $("#hbFoto");
  const src = it ? API + "/" + PJ.id + "/galeria/" + it.id : (PJ.tiene_retrato ? imgUrl("retrato") : "");
  box.innerHTML = src ? `<img src="${src}" style="height:90px;border-radius:10px"><div class="hint" style="margin:0">${it ? "Sobre esta foto." : "Sobre el retrato (elegí una foto de la galería para otra escena)."}</div>` : '<div class="errbox">Primero aprobá un retrato.</div>';
  $("#hb-motor").innerHTML = Object.entries(CFG.motores_habla || {}).map(([k, v]) => `<option value="${k}">${esc(v)} · US$${(CFG.precio_habla || {})[k]}</option>`).join("");
  $("#hbSeg").textContent = CFG.habla_seg || 8; $("#hbMax").textContent = CFG.max_palabras_habla || 22; $("#hbEstado").innerHTML = ""; $("#btnHablar").disabled = !src;
  if(it && it.caption && !$("#hb-texto").value) $("#hb-texto").value = it.caption.replace(/#\S+/g, "").trim().split(/(?<=[.!?])\s/)[0] || "";
  hbCuenta(); abrir("ovHablar");
}
function hbCuenta(){ const n = $("#hb-texto").value.trim().split(/\s+/).filter(Boolean).length; $("#hbPalabras").textContent = n; $("#hbPalabras").style.color = n > (CFG.max_palabras_habla || 22) ? "var(--bad)" : ""; }
$("#hb-texto").oninput = hbCuenta;
async function hbSugerir(){
  try{ const d = await post("/" + PJ.id + "/chat", {texto: "Escribí UNA frase de hasta " + (CFG.max_palabras_habla || 22) + " palabras para decir a cámara en un video de " + (CFG.habla_seg || 8) + " segundos presentándote o mostrando algo de la marca. Solo la frase, sin comillas."});
    $("#hb-texto").value = (d.respuesta || "").replace(/^["“]|["”]$/g, ""); hbCuenta(); }catch(e){ toast(e.message); }
}
$("#btnHablar").onclick = async () => {
  const b = $("#btnHablar"); ocupado(b, true, "Mandando a grabar…");
  try{
    const d = await post("/" + PJ.id + "/hablar", {texto: $("#hb-texto").value, foto_id: HB_FOTO ? HB_FOTO.id : "", motor: $("#hb-motor").value, formato: $("#hb-formato").value});
    HB_JOB = d.job; $("#hbEstado").innerHTML = `<div class="hint"><span class="spin"></span>En cola…</div><div class="hint">Suele tardar 1 a 3 minutos. Podés cerrar: queda en "En curso" en la galería.</div>`;
    cargarJobs(); pollHabla();
  }catch(e){ toast(e.message, 7000); ocupado(b, false); }
};
async function pollHabla(){
  if(!HB_JOB) return;
  try{
    const j = await api("/job/" + HB_JOB);
    if(j.estado === "listo"){ $("#hbEstado").innerHTML = `<video src="${API}/clip/${HB_JOB}" controls playsinline style="width:100%;border-radius:12px;margin-top:8px"></video>${qcHtml(j.qc)}${j.drive ? `<div class="hint"><a href="${esc(j.drive)}" target="_blank">Abrir en Drive</a></div>` : ""}`;
      ocupado($("#btnHablar"), false); HB_JOB = null; toast("✓ Clip listo (también en la galería)"); cargarGaleria(); cargarJobs(); return; }
    if(j.estado === "error"){ $("#hbEstado").innerHTML = `<div class="errbox">${esc(j.error)}</div>`; ocupado($("#btnHablar"), false); HB_JOB = null; return; }
    const h = $("#hbEstado").querySelector(".hint"); if(h) h.innerHTML = `<span class="spin"></span>${esc(j.paso || "Grabando…")}<br>` + relojHtml(j);
  }catch(e){}
  setTimeout(pollHabla, 5000);
}

// Cronómetro de un trabajo: cuánto va y cuánto suele tardar. Sin esto, un
// puntito que gira 6 minutos parece colgado.
function mmss(seg){ seg = Math.max(0, Math.round(seg)); return Math.floor(seg / 60) + ":" + String(seg % 60).padStart(2, "0"); }
function relojHtml(j, ahora){
  const t = Math.max(0, (ahora || Date.now() / 1000) - (j.inicio || 0));
  const est = j.estimado_seg || 0;
  let txt = "⏱ " + mmss(t);
  if(est) txt += t <= est ? ` · suele tardar ~${mmss(est)}` : ` · se está pasando del estimado (~${mmss(est)}); a veces la cola de fal está lenta, esperá`;
  return `<span class="hint" style="margin:0">${txt}</span>`;
}
function estadoJobHtml(j, ahora){
  return `<div class="hint" style="margin:6px 0"><span class="spin"></span>${esc(j.paso || "En cola…")}<br>${relojHtml(j, ahora)}</div>`;
}
let JOBS_TIMER = null;
async function cargarJobs(){
  if(!PJ) return;
  try{
    const d = await api("/" + PJ.id + "/jobs"); const box = $("#enCurso");
    const activos = (d.jobs || []).filter(j => j.estado !== "listo" && j.estado !== "error");
    const errores = (d.jobs || []).filter(j => j.estado === "error").slice(0, 3);
    if(!activos.length && !errores.length){ box.innerHTML = ""; box.style.display = "none"; }
    else {
      box.style.display = "";
      box.innerHTML = (activos.length ? `<h3 style="margin-top:0">En curso</h3>` : "") + activos.map(j => `<div class="prop"><b>${esc(j.titulo || j.tipo)}</b> <span class="pill soft">US$${j.costo}</span>${estadoJobHtml(j, d.ahora)}</div>`).join("")
        + (errores.length ? `<h3>Salieron mal</h3>` + errores.map(j => `<div class="errbox"><b>${esc(j.titulo || j.tipo)}</b>: ${esc(j.error || "")}</div>`).join("") : "");
    }
    const habiaActivos = box._activos || 0;
    if(habiaActivos && !activos.length) cargarGaleria();   // terminó algo: refrescar
    box._activos = activos.length;
    clearTimeout(JOBS_TIMER);
    if(activos.length) JOBS_TIMER = setTimeout(cargarJobs, 8000);
  }catch(e){}
}
function qcHtml(qc){
  if(!qc) return "";
  const ok = qc.puntaje >= 8;
  return `<div class="${ok ? "hint" : "errbox"}" style="margin-top:8px">🔍 Inspector: <b>${qc.puntaje}/10</b> (${qc.cuadros} cuadros)${qc.diferencias && qc.diferencias.length ? " · " + esc(qc.diferencias.join(" · ")) : " · la prenda se mantuvo"}</div>`;
}
/* ───────── que se mueva ───────── */
let MR_FOTO = null, MR_JOB = null;
function abrirMover(it){
  if(!it || it.tipo !== "foto"){ toast("Elegí una foto de la galería."); return; }
  MR_FOTO = it; const mr = CFG.mover || {};
  $("#mrFoto").innerHTML = `<img src="${API}/${PJ.id}/galeria/${it.id}" style="height:90px;border-radius:10px"><div class="hint" style="margin:0">${esc(it.titulo || "Esta foto")}</div>`;
  $("#mr-mov").innerHTML = Object.entries(mr.movimientos || {}).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#mr-motor").innerHTML = Object.entries(mr.motores || {}).map(([k, v]) => `<option value="${k}">${esc(v.label)} · US$${v.precio_seg}/s</option>`).join("");
  $("#mr-dur").innerHTML = (mr.duraciones || [5, 10]).map(d => `<option value="${d}">${d} s</option>`).join("");
  $("#mr-texto").value = ""; $("#mr-extra").value = ""; $("#mrEstado").innerHTML = ""; $("#btnMover").disabled = !(CFG.movete || {}).fal_key;
  if(!(CFG.movete || {}).fal_key) $("#mrEstado").innerHTML = '<div class="errbox">Falta la API key de fal.ai (Fotos → Ajustes, o FAL_KEY en Railway).</div>';
  mrCosto(); abrir("ovMover");
}
function mrCosto(){ const mr = CFG.mover || {}; const m = (mr.motores || {})[$("#mr-motor").value] || {}; const d = Number($("#mr-dur").value || 5);
  $("#mrCosto").textContent = "Aprox. US$" + ((m.precio_seg || 0.05) * d).toFixed(2) + " · después el inspector revisa 3 cuadros.";
  const libre = $("#mr-mov").value === "libre"; $("#mrLibreBox").style.display = libre ? "" : "none"; $("#mrExtraBox").style.display = libre ? "none" : ""; }
$("#mr-motor").onchange = mrCosto; $("#mr-dur").onchange = mrCosto; $("#mr-mov").onchange = mrCosto;
$("#btnMover").onclick = async () => {
  const b = $("#btnMover"); ocupado(b, true, "Mandando…");
  try{
    const d = await post("/" + PJ.id + "/mover", {foto_id: MR_FOTO.id, movimiento: $("#mr-mov").value, texto: $("#mr-mov").value === "libre" ? $("#mr-texto").value : $("#mr-extra").value,
      motor: $("#mr-motor").value, duracion: Number($("#mr-dur").value)});
    MR_JOB = d.job; $("#mrEstado").innerHTML = `<div class="hint"><span class="spin"></span>En cola…</div><div class="hint">Suele tardar 1 a 4 minutos. Podés cerrar: queda en "En curso" en la galería.</div>`;
    cargarJobs(); pollMover();
  }catch(e){ toast(e.message, 8000); ocupado(b, false); }
};
async function pollMover(){
  if(!MR_JOB) return;
  try{
    const j = await api("/job/" + MR_JOB);
    if(j.estado === "listo"){ $("#mrEstado").innerHTML = `<video src="${API}/clip/${MR_JOB}" controls playsinline style="width:100%;border-radius:12px;margin-top:8px"></video>${qcHtml(j.qc)}${j.drive ? `<div class="hint"><a href="${esc(j.drive)}" target="_blank">Abrir en Drive</a></div>` : ""}`;
      ocupado($("#btnMover"), false); MR_JOB = null; toast("✓ Clip listo (también en la galería)"); cargarGaleria(); cargarJobs(); return; }
    if(j.estado === "error"){ $("#mrEstado").innerHTML = `<div class="errbox">${esc(j.error)}</div>`; ocupado($("#btnMover"), false); MR_JOB = null; return; }
    const h = $("#mrEstado").querySelector(".hint"); if(h) h.innerHTML = `<span class="spin"></span>${esc(j.paso || "En cola…")}<br>` + relojHtml(j);
  }catch(e){}
  setTimeout(pollMover, 5000);
}

/* ───────── movete vos ───────── */
let MV_FOTO = null, MV_JOB = null, MV_SEG = 0, MV_T0 = 0;
function abrirMovete(it){
  MV_FOTO = it; const box = $("#mvFoto"); const mv = CFG.movete || {};
  const h = PJ.hoja || {};
  const src = it ? API + "/" + PJ.id + "/galeria/" + it.id : (h.cuerpo ? imgUrl("cuerpo") : (h.retrato ? imgUrl("retrato") : ""));
  box.innerHTML = src ? `<img src="${src}" style="height:90px;border-radius:10px"><div class="hint" style="margin:0">${it ? "Referencia: esta foto (con su ropa)." : "Referencia: su cuerpo entero de la hoja. Para otra prenda, abrí Movete desde una foto de la galería."}</div>` : '<div class="errbox">Primero aprobá un retrato.</div>';
  $("#mvMax").textContent = mv.max_seg || 20; $("#mvEstado").innerHTML = mv.fal_key ? "" : '<div class="errbox">Falta la API key de fal.ai (Fotos → Ajustes, o FAL_KEY en Railway). Sin eso no hay motor de reemplazo.</div>';
  $("#mv-video").value = ""; $("#mv-prendas").value = ""; $("#mvInfo").textContent = "Elegí un video."; MV_SEG = 0; $("#btnMovete").disabled = true;
  if(mv.resolucion) $("#mv-res").value = mv.resolucion;
  if(it && it.tipo === "video"){ box.innerHTML = '<div class="errbox">Elegí una foto, no un video.</div>'; }
  abrir("ovMovete");
}
$("#mv-video").onchange = () => {
  const f = $("#mv-video").files[0]; if(!f){ $("#btnMovete").disabled = true; return; }
  const mv = CFG.movete || {};
  if(f.size > (mv.max_mb || 200) * 1024 * 1024){ $("#mvInfo").textContent = "Pesa más de " + (mv.max_mb || 200) + " MB: recortalo antes."; $("#btnMovete").disabled = true; return; }
  const v = document.createElement("video"); v.preload = "metadata";
  v.onloadedmetadata = () => { URL.revokeObjectURL(v.src); MV_SEG = Math.min(v.duration || 0, mv.max_seg || 20);
    const recorte = (v.duration || 0) > (mv.max_seg || 20) ? ` (se usan los primeros ${mv.max_seg || 20}s)` : "";
    mvInfo(recorte); $("#btnMovete").disabled = false; };
  // Algunos navegadores no leen el video acá (códec): igual se puede mandar, el
  // servidor calcula la duración y el costo antes de gastar.
  v.onerror = () => { MV_SEG = 0; $("#mvInfo").textContent = "No pude leer la duración acá: la calcula el servidor (aprox. US$" + (mv.precio_seg || 0.08).toFixed(2) + " por segundo, hasta " + (mv.max_seg || 20) + " s)."; $("#btnMovete").disabled = false; };
  v.src = URL.createObjectURL(f);
};
function mvInfo(recorte){
  const mv = CFG.movete || {}; if(!MV_SEG) return;
  const spp = (mv.seg_por_seg || {})[$("#mv-res").value] || 40; const est = 60 + spp * MV_SEG;
  $("#mvInfo").textContent = `${Math.round(MV_SEG)} s${recorte || ""} · aprox. US$${(MV_SEG * (mv.precio_seg || 0.08)).toFixed(2)} · suele tardar ~${mmss(est)} a ${$("#mv-res").value}`;
}
$("#mv-res").onchange = () => mvInfo("");
$("#btnRecuperar").onclick = async () => {
  const b = $("#btnRecuperar"); ocupado(b, true, "Buscando…");
  try{
    const d = await post("/" + PJ.id + "/movete/recuperar", {request_id: $("#mv-rid").value.trim(), modo: $("#mv-modo").value, foto_id: MV_FOTO ? MV_FOTO.id : "", segundos: Number($("#mv-rseg").value || 0)});
    MV_JOB = d.job; $("#mvEstado").innerHTML = `<div class="hint"><span class="spin"></span>Buscando el trabajo en fal…</div>`; cargarJobs(); pollMovete(); toast("Retomando desde fal");
  }catch(e){ toast(e.message, 8000); } finally{ ocupado(b, false); }
};
$("#btnMovete").onclick = async () => {
  const f = $("#mv-video").files[0]; if(!f) return;
  const b = $("#btnMovete"); ocupado(b, true, "Subiendo y preparando…");
  try{
    const fd = new FormData(); fd.append("video", f); fd.append("foto_id", MV_FOTO ? MV_FOTO.id : ""); fd.append("modo", $("#mv-modo").value); fd.append("resolucion", $("#mv-res").value);
    for(const pf of [...$("#mv-prendas").files].slice(0, 3)) fd.append("prendas", pf);
    const r = await fetch(API + "/" + PJ.id + "/movete", {method: "POST", body: fd});
    const d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.detail || "HTTP " + r.status);
    MV_JOB = d.job; MV_T0 = Date.now() / 1000; $("#mvEstado").innerHTML = `<div class="hint"><span class="spin"></span>Subiendo…</div><div class="hint">Wan Animate tarda entre 3 y 7 minutos por cada 10 s de video, más la cola de fal. Podés cerrar (o hasta si el servidor se reinicia): queda en "En curso" en la galería y se retoma solo.</div>`;
    cargarJobs(); pollMovete();
  }catch(e){ toast(e.message, 8000); ocupado(b, false); }
};
async function pollMovete(){
  if(!MV_JOB) return;
  try{
    const j = await api("/job/" + MV_JOB);
    if(j.estado === "listo"){ $("#mvEstado").innerHTML = `<video src="${API}/clip/${MV_JOB}" controls playsinline style="width:100%;border-radius:12px;margin-top:8px"></video>${qcHtml(j.qc)}${j.drive ? `<div class="hint"><a href="${esc(j.drive)}" target="_blank">Abrir en Drive</a></div>` : ""}`;
      ocupado($("#btnMovete"), false); MV_JOB = null; toast("✓ Listo (también en la galería)"); cargarGaleria(); cargarJobs(); return; }
    if(j.estado === "error"){ $("#mvEstado").innerHTML = `<div class="errbox">${esc(j.error)}</div>`; ocupado($("#btnMovete"), false); MV_JOB = null; return; }
    const h = $("#mvEstado").querySelector(".hint"); if(h) h.innerHTML = `<span class="spin"></span>${esc(j.paso || "En cola…")}<br>` + relojHtml(j);
  }catch(e){}
  setTimeout(pollMovete, 5000);
}

/* ───────── ficha ───────── */
function pintarFicha(){
  const h = PJ.hoja || {};
  $("#hoja").innerHTML = [["retrato", "Retrato"], ["perfil", "Perfil 3/4"], ["cuerpo", "Cuerpo entero"], ["espalda", "Espalda"]].map(([v, l]) =>
    `<div class="v" ${h[v] ? `style="background-image:url('${imgUrl(v)}')"` : ""}><span>${h[v] ? "✓ " : ""}${l}</span></div>`).join("");
  $("#btnHoja").disabled = !h.retrato;
  $("#hojaHint").textContent = !h.retrato ? "Sin retrato todavía." : (h.cuerpo ? "Hoja completa: cara y cuerpo fijos." : "Retrato aprobado. Generá la hoja para fijar también el cuerpo (una imagen 2K).");
  for(const k of ["nombre", "edad", "ciudad", "marca", "rol", "personalidad", "historia", "tono", "gustos", "no_hace"]) $("#f-" + k).value = PJ[k] || "";
  $("#f-genero").value = PJ.genero || "mujer"; $("#f-voz").innerHTML = vocesOpts(PJ.genero || "mujer", PJ.voz);
  $("#f-calidad").value = PJ.calidad || "2K"; $("#f-formato").value = PJ.formato || "4:5";
  const ap = PJ.apariencia || {}; for(const k of ["piel", "pelo", "ojos", "contextura", "altura", "estilo", "rasgos"]) $("#fa-" + k).value = ap[k] || "";
  $("#f-memoria").value = (PJ.memoria || []).join("\n");
}
function fichaBody(){
  const b = {genero: $("#f-genero").value, voz: $("#f-voz").value, calidad: $("#f-calidad").value, formato: $("#f-formato").value, apariencia: {},
    memoria: $("#f-memoria").value.split("\n").map(s => s.trim()).filter(Boolean)};
  for(const k of ["nombre", "edad", "ciudad", "marca", "rol", "personalidad", "historia", "tono", "gustos", "no_hace"]) b[k] = $("#f-" + k).value;
  for(const k of ["piel", "pelo", "ojos", "contextura", "altura", "estilo", "rasgos"]) b.apariencia[k] = $("#fa-" + k).value;
  return b;
}
$("#btnGuardarFicha").onclick = async () => { const b = $("#btnGuardarFicha"); ocupado(b, true); try{ const d = await post("/" + PJ.id + "/ficha", fichaBody()); PJ = d.personaje; pintarHead(); pintarFicha(); toast("✓ Ficha guardada"); }catch(e){ toast(e.message); } finally{ ocupado(b, false); } };
$("#btnBorrarPj").onclick = async () => { if(!confirm("¿Borrar a " + PJ.nombre + " con sus fotos, charlas y memoria? No se puede deshacer.")) return; try{ await del("/" + PJ.id); toast("Borrado"); verLista(); }catch(e){ toast(e.message); } };

async function generarRetrato(){
  const b = $("#btnRetratoGen"); ocupado(b, true, "Generando retrato…"); ocupado($("#btnRetratoOtro"), true, "…");
  try{ const d = await post("/" + PJ.id + "/retrato", Object.assign({modo: "generar"}, fichaBody())); RETRATO_CAND = d.ref_b64; $("#retratoImg").src = d.preview; abrir("ovRetrato"); }
  catch(e){ toast(e.message, 7000); } finally{ ocupado(b, false); ocupado($("#btnRetratoOtro"), false); }
}
$("#btnRetratoGen").onclick = generarRetrato; $("#btnRetratoOtro").onclick = generarRetrato;
$("#inRetrato").onchange = async e => { const f = e.target.files[0]; e.target.value = ""; if(!f) return;
  try{ const d = await post("/" + PJ.id + "/retrato", {modo: "subir", image: await achicar(f, 2000)}); RETRATO_CAND = d.ref_b64; $("#retratoImg").src = d.preview; abrir("ovRetrato"); }catch(err){ toast(err.message); } };
$("#btnDesdeAvatar").onclick = async () => {
  try{ const d = await api("/avatares"); const l = $("#avList"); l.innerHTML = "";
    if(!d.avatares.length){ l.innerHTML = '<div class="hint">No hay avatares creados en Fotos → Avatares.</div>'; }
    for(const av of d.avatares){ const c = document.createElement("div"); c.className = "pjcard";
      c.innerHTML = `<div class="ph" style="background-image:url('%%HOME_API%%/api/avatars/${av.id}/ref')"></div><div class="nm">${esc(av.name)}</div><div class="st">${esc(av.gender)}</div>`;
      c.onclick = async () => { try{ const r = await post("/" + PJ.id + "/retrato", {modo: "avatar", avatar_id: av.id}); RETRATO_CAND = r.ref_b64; $("#retratoImg").src = r.preview; cerrar("ovAvatar"); abrir("ovRetrato"); }catch(e){ toast(e.message); } };
      l.appendChild(c); }
    abrir("ovAvatar"); }catch(e){ toast(e.message); }
};
$("#btnRetratoOk").onclick = async () => {
  const b = $("#btnRetratoOk"); ocupado(b, true, "Guardando y estudiando la cara…");
  try{ const d = await post("/" + PJ.id + "/retrato/aprobar", {ref_b64: RETRATO_CAND}); PJ = d.personaje; cerrar("ovRetrato"); pintarHead(); pintarFicha(); toast("✓ Retrato aprobado. Ahora generá la hoja de 3 vistas."); }
  catch(e){ toast(e.message, 7000); } finally{ ocupado(b, false); }
};
$("#btnHoja").onclick = async () => {
  const b = $("#btnHoja"); ocupado(b, true, "Dibujando las 3 vistas…");
  try{ const d = await post("/" + PJ.id + "/hoja"); PJ = d.personaje; pintarFicha(); toast("✓ Hoja lista: cara y cuerpo fijos."); }catch(e){ toast(e.message, 7000); } finally{ ocupado(b, false); }
};

/* ───────── arranque ───────── */
(async () => {
  try{ CFG = await api("/config"); }catch(e){ toast("Sin conexión con el servidor: " + e.message); }
  const pj = new URLSearchParams(location.search).get("pj");
  if(pj) await abrirPj(pj); else await cargarLista();
})();
</script>
</body>
</html>
"""
HTML_PAGE = (HTML_PAGE
             .replace("%%PREFIX%%", ROUTE_PREFIX)
             .replace("%%VERSION%%", VERSION)
             .replace("%%HOME_API%%", os.environ.get("IMAGENES_PREFIX", "/imagenes"))
             .replace("%%HOME%%", os.environ.get("IMAGENES_PREFIX", "/imagenes") or "/")
             .replace("%%VIDEOS%%", os.environ.get("VIDEOS_PREFIX", "/videos")))
