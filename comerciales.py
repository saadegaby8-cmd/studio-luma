# -*- coding: utf-8 -*-
"""
comerciales.py — Videos comerciales (estilo campaña de marca, tipo Rip Curl)
============================================================================

Módulo STANDALONE de Studio Luma (mismo molde que videos_luma.py).

Qué hace
--------
De un puñado de fotos de campaña —la modelo con la prenda, en locación— arma un
VIDEO COMERCIAL: cámara lenta, luz natural, grade de película, música y una
placa de cierre con la marca. Dos maneras, y se eligen en la pantalla:

1. "KLING ARMA EL COMERCIAL": las fotos van como REFERENCIA (hasta 7) a Kling
   3.0 Omni (fal), que inventa las tomas manteniendo a la misma modelo y la
   misma prenda. Sale una pieza con continuidad, como filmada, de hasta 15 s
   por pedido (6 tomas). Un comercial de 30 s son dos tandas pegadas.
   Las tomas salen de una PLANTILLA (surf, playa, urbano) o las escribe la
   usuaria; cada una con su duración.
2. "FOTO POR FOTO": cada foto ES una toma, en su orden. El movimiento lo hace
   la IA (image-to-video de fal, con un prompt de LOCACIÓN: conserva el fondo
   real, cámara lenta, viento, olas) o la cámara de edición (una deriva lenta
   sobre la foto, gratis). Fidelidad exacta a cada foto.

Después, para las dos: corte o fundidos (cortos o largos), GRADE de película
(teal y naranja, cálido, frío, blanco y negro), grano fino, viñeta, franjas de
cine opcionales, la placa final con la marca y la cortina musical (la misma
que se sube en Videos).

Variables de entorno
--------------------
  FAL_KEY               (obligatoria)  -> Kling y los motores image-to-video
  FAL_KLING_STD_MODEL   (opcional)     -> ruta del modelo Standard en fal
  FAL_KLING_PRO_MODEL   (opcional)     -> ruta del modelo Pro en fal
  COMERCIALES_PRECIO_STD / _PRO (opcional) -> USD por segundo, para estimar
  COMERCIALES_PREFIX    (opcional)     -> default "/comerciales"
"""

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
from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from imagenes_ia import (
    _compress_ref,
    _pfx,
    _strip_data_url,
    budget_check,
    budget_record,
    get_settings,
    kv,
    session_sub_from_request,
    set_current_sub,
)
from videos_luma import (
    FAL_MODELS,
    MOTOR_LABEL,
    PRECIO_SEG,
    _dims,
    _duracion_video,
    _ffmpeg_bin,
    _font_path,
    _generar_fal,
    _musica_path,
    _spawn,
    _traducir_libres,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

ROUTE_PREFIX = os.environ.get("COMERCIALES_PREFIX", "/comerciales").rstrip("/")
VERSION = "1.0.0"   # subí este número cada vez que cambiamos el archivo

FAL_KEY = os.getenv("FAL_KEY", "") or os.getenv("FAL_API_KEY", "")
FAL_BASE = "https://queue.fal.run"

# Kling 3.0 Omni en fal ("O3"): referencia a video, con multi-shot. Si fal le
# cambia la ruta, se corrige con la variable de entorno sin tocar el código.
KLING = {
    "kling_std": {
        "modelo": os.getenv("FAL_KLING_STD_MODEL",
                            "fal-ai/kling-video/o3/standard/reference-to-video"),
        "label": "Kling 3.0 Omni · Standard",
        "precio_seg": float(os.getenv("COMERCIALES_PRECIO_STD", "0.084")),
    },
    "kling_pro": {
        "modelo": os.getenv("FAL_KLING_PRO_MODEL",
                            "fal-ai/kling-video/o3/pro/reference-to-video"),
        "label": "Kling 3.0 Omni · Pro (más calidad)",
        "precio_seg": float(os.getenv("COMERCIALES_PRECIO_PRO", "0.112")),
    },
}
KLING_DEFAULT = "kling_std"
TANDA_SEG = 15                  # tope de Kling por pedido
TOMAS_POR_TANDA = 6             # tope de tomas por pedido (multi-shot)
DURACIONES_TOTAL = (15, 30, 45)
MAX_REFS_KLING = 7              # 1 frontal + 3 de la modelo + 3 del lugar
MAX_FOTOS = 16
MAX_TOMAS_LIBRES = 18
TOMA_SEG_MIN, TOMA_SEG_MAX = 1, 8
KLING_TIMEOUT = 20 * 60
IA_TIMEOUT = 12 * 60

MODOS = {
    "kling": "Kling arma el comercial (tus fotos son la referencia)",
    "fotos": "Foto por foto (cada foto es una toma, en su orden)",
}
MOTORES_FOTO = ("ia", "camara")
SEG_CAMARA_OK = (2.0, 2.5, 3.0, 4.0, 5.0, 6.0)
SEG_IA_OK = (3.0, 4.0, 5.0, 6.0, 8.0)

GRADES = {
    "pelicula": "Película · teal y naranja (el look de campaña)",
    "calido": "Cálido · atardecer dorado",
    "frio": "Frío · azulado, de invierno",
    "bn": "Blanco y negro",
    "ninguno": "Sin grade (colores de la foto)",
}
GRADE_DEFAULT = "pelicula"
TRANSICIONES = {
    "corte": "Cortes secos (como las marcas)",
    "fundido": "Fundido cruzado corto (0,5 s)",
    "fundido_largo": "Fundido cruzado largo (1 s, más lento)",
    "negro": "Pasa por negro (0,6 s)",
}
TRANSICION_SEG = {"corte": 0.0, "fundido": 0.5, "fundido_largo": 1.0, "negro": 0.6}
FORMATOS = ("9:16", "16:9")
PLACA_SEG = 2.5
PLACA_DEFAULT = "LUMA Íntima"

JOB_TTL = 7 * 24 * 3600
JOBS_INDICE = 40
WORK_DIR = (Path("/data/comerciales_luma") if Path("/data").exists()
            else Path("/tmp/comerciales_luma"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# PLANTILLAS: la lista de tomas de un comercial. "es" es lo que se lee y edita
# en la pantalla; "en" es lo que viaja al motor (rinden mejor en inglés).
# @Element1 es la modelo (con SU prenda), tal como Kling nombra a las referencias.
# ─────────────────────────────────────────────────────────────────────────────

_CINE = ("Cinematic slow-motion brand film, natural light, shallow depth of field, "
         "steady gimbal glide, subtle wind in the hair. ")

PLANTILLAS: Dict[str, Dict[str, Any]] = {
    "surf": {
        "nombre": "Surf · estilo Rip Curl",
        "desc": "Camioneta en la arena, shack de tablas, leash, olas. Cámara lenta y luz de mañana.",
        "lugar_es": "una playa de surf: un shack de madera con tablas, una camioneta en la arena, olas",
        "lugar_en": "a surf beach: a wooden surf shack with boards, a pickup truck on the sand, breaking waves",
        "tomas": [
            {"es": "Desde arriba: las manos apoyadas en la caja de la camioneta, el mar detrás", "seg": 3,
             "en": "Top-down shot: @Element1 rests both hands on the open tailgate of a pickup parked on the sand, the ocean behind, hair moving in the wind."},
            {"es": "Saca la tabla de la camioneta, de costado", "seg": 2,
             "en": "Side angle: @Element1 slides a surfboard out of the truck bed in slow motion, dunes and sea behind her."},
            {"es": "Sentada en el banco del shack atándose el leash al tobillo", "seg": 3,
             "en": "Inside the wooden surf shack: @Element1 sits on a bench and fastens the leash around her ankle, boards and wetsuits behind, the sea through the window."},
            {"es": "Se acomoda el pelo y mira a cámara, tablas en la pared", "seg": 2,
             "en": "Three-quarter back view: @Element1 runs a hand through her hair and turns her gaze to the lens, surfboards against the wall, warm side light."},
            {"es": "Cierra el cierre del traje en la orilla", "seg": 3,
             "en": "Medium shot at the shoreline: @Element1 zips up the front zipper of her suit, looking down, waves foaming behind."},
            {"es": "Camina hacia el agua con la tabla bajo el brazo", "seg": 2,
             "en": "Wide shot: @Element1 walks toward the water with the board under her arm, slow motion, wet sand reflecting the sky."},
            {"es": "De rodillas en la arena mojada, ajusta el leash", "seg": 3,
             "en": "Low angle: @Element1 kneels on the wet sand next to her board and tightens the leash, foam sliding around her feet."},
            {"es": "Mirada firme a cámara, olas detrás", "seg": 2,
             "en": "Close-up portrait: @Element1 looks straight into the lens with a calm, confident expression, waves breaking out of focus behind her."},
            {"es": "Sonrisa sobre el hombro", "seg": 3,
             "en": "Back three-quarter shot: @Element1 looks over her shoulder and smiles, hand on her neck, the beach hut and flags far behind."},
            {"es": "Entra al agua, la espuma en los pies", "seg": 2,
             "en": "Tracking shot from behind: @Element1 walks into the shallow water, foam rushing around her ankles, slow motion."},
            {"es": "Detalle del estampado del lateral de la prenda", "seg": 3,
             "en": "Slow push-in detail: the printed side panel and the zipper of @Element1's suit, fabric texture visible, sunlight glancing off it."},
            {"es": "Se aleja hacia el mar, plano abierto", "seg": 2,
             "en": "Wide cinematic shot: @Element1 walks away toward the sea with the board, small in the frame, the horizon wide and bright."},
        ],
    },
    "playa": {
        "nombre": "Playa · verano",
        "desc": "Orilla, arena, reposera, atardecer. Sin equipo de surf.",
        "lugar_es": "una playa al atardecer: arena clara, la orilla, una reposera de madera",
        "lugar_en": "a beach at golden hour: pale sand, the shoreline, a wooden lounger",
        "tomas": [
            {"es": "Camina por la orilla, plano entero", "seg": 3,
             "en": "Wide shot: @Element1 walks along the shoreline in slow motion, the water touching her feet."},
            {"es": "Gira hacia cámara y sonríe", "seg": 2,
             "en": "Medium shot: @Element1 turns toward the lens and smiles softly, hair in the wind."},
            {"es": "Sentada en la reposera, mirada al mar", "seg": 3,
             "en": "Side view: @Element1 sits on a wooden lounger looking at the sea, warm backlight."},
            {"es": "Detalle de la prenda: tela y tiras", "seg": 2,
             "en": "Slow push-in detail: the fabric, straps and trims of @Element1's garment in the sun."},
            {"es": "Se acomoda el pelo, primer plano", "seg": 3,
             "en": "Close-up: @Element1 tucks her hair behind her ear and looks down, then up to the lens."},
            {"es": "Corre hacia el agua, de espaldas", "seg": 2,
             "en": "Tracking shot from behind: @Element1 runs into the shallow water in slow motion, splashes catching the light."},
            {"es": "De espaldas, mirando el horizonte", "seg": 3,
             "en": "Back view: @Element1 stands still facing the horizon, the wind moving her hair and the fabric."},
            {"es": "Se da vuelta sobre el hombro", "seg": 2,
             "en": "Over-the-shoulder look: @Element1 glances back at the camera with a half smile."},
            {"es": "Acostada en la arena, desde arriba", "seg": 3,
             "en": "Top-down shot: @Element1 lies on the sand, eyes closed, sunlight on her skin."},
            {"es": "Camina de frente, plano medio", "seg": 2,
             "en": "Medium shot: @Element1 walks toward the camera with a relaxed stride."},
            {"es": "Detalle de la espalda de la prenda", "seg": 3,
             "en": "Detail from behind: the back of @Element1's garment, straps and closure, slow drift."},
            {"es": "Se aleja por la orilla, atardecer", "seg": 2,
             "en": "Wide shot at sunset: @Element1 walks away along the shore, silhouette against the golden water."},
        ],
    },
    "urbano": {
        "nombre": "Urbano · calle",
        "desc": "Vereda, pared de ladrillo, luz de tarde, paso firme.",
        "lugar_es": "una calle de ciudad: vereda ancha, una pared de ladrillo, luz de tarde",
        "lugar_en": "a city street: a wide sidewalk, a brick wall, late-afternoon light",
        "tomas": [
            {"es": "Camina por la vereda hacia cámara", "seg": 3,
             "en": "Wide shot: @Element1 walks down the sidewalk toward the camera with a confident stride, slow motion."},
            {"es": "Apoyada en la pared de ladrillo, mira a cámara", "seg": 2,
             "en": "Medium shot: @Element1 leans against a brick wall and looks into the lens."},
            {"es": "Detalle de la prenda con la luz de costado", "seg": 3,
             "en": "Slow push-in detail: the fabric and cut of @Element1's garment, side light raking across it."},
            {"es": "Cruza la calle, plano abierto", "seg": 2,
             "en": "Wide shot: @Element1 crosses the street, city blurred behind her."},
            {"es": "Gira y el pelo se mueve", "seg": 3,
             "en": "Close-up: @Element1 turns her head, hair swinging in slow motion."},
            {"es": "De espaldas, se aleja por la vereda", "seg": 2,
             "en": "Back view: @Element1 walks away down the sidewalk, long shadows on the ground."},
            {"es": "Sentada en un escalón, relajada", "seg": 3,
             "en": "Medium shot: @Element1 sits on a doorstep, relaxed, looking off to the side."},
            {"es": "Primer plano, media sonrisa", "seg": 2,
             "en": "Close-up portrait: @Element1 gives a half smile to the lens."},
            {"es": "Camina en diagonal, la cámara la sigue", "seg": 3,
             "en": "Tracking shot: the camera follows @Element1 walking diagonally across the frame."},
            {"es": "Detalle de la espalda de la prenda", "seg": 2,
             "en": "Detail from behind: the back of @Element1's garment, slow drift."},
            {"es": "Se acomoda la prenda, plano medio", "seg": 3,
             "en": "Medium shot: @Element1 adjusts her garment with both hands, looking down, then up."},
            {"es": "Plano abierto final, luz dorada", "seg": 2,
             "en": "Wide final shot: @Element1 stands still in golden light, the street empty around her."},
        ],
    },
    "libre": {
        "nombre": "Libre · escribo yo las tomas",
        "desc": "Vos escribís cada toma y cuánto dura.",
        "lugar_es": "",
        "lugar_en": "",
        "tomas": [],
    },
}
PLANTILLA_DEFAULT = "surf"

# Movimientos para el modo FOTO POR FOTO con IA: rotan con la toma, y la usuaria
# puede escribir el suyo. Todos conservan el fondo real de la foto.
MOVIMIENTOS_FOTO = [
    {"es": "Gira despacio la cabeza hacia cámara", "en": "she slowly turns her head toward the lens"},
    {"es": "Camina dos pasos lentos", "en": "she takes two slow steps forward"},
    {"es": "La cámara flota de costado, ella respira quieta", "en": "the camera glides slowly sideways while she holds the pose, breathing"},
    {"es": "Levanta la mirada de la prenda al horizonte", "en": "she lifts her gaze from the garment to the horizon"},
    {"es": "El viento le mueve el pelo, sonríe apenas", "en": "the wind moves her hair and she smiles slightly"},
    {"es": "La cámara se acerca lento a la prenda", "en": "the camera pushes in slowly toward the garment"},
]

# ─────────────────────────────────────────────────────────────────────────────
# PROMPTS
# ─────────────────────────────────────────────────────────────────────────────

_NEGATIVO = ("text, captions, subtitles, logos, watermark, extra people, morphing, "
             "warping, face change, different person, different garment, redesigned "
             "product, extra fingers, distorted hands, cgi, cartoon")


def _prompt_kling_toma(req: Dict[str, Any], toma: Dict[str, Any]) -> str:
    """Una toma del multi-shot de Kling: estilo + lugar + la acción."""
    lugar = (req.get("lugar_en") or "").strip()
    lugar_txt = f"Location: {lugar}. " if lugar else ""
    return (_CINE + lugar_txt
            + "@Element1 is the model from the references and wears EXACTLY the garment "
              "shown there — same design, colors, print and cut; never change it. "
            + str(toma.get("en") or toma.get("es") or "").strip()
            + " Real skin texture, no text, no logos.")


def _prompt_foto_ia(req: Dict[str, Any], i: int, toma: Dict[str, Any]) -> str:
    """Image-to-video sobre UNA foto en locación: el fondo real se conserva."""
    mov_txt = (toma.get("en") or "").strip()
    if not mov_txt:
        mov_txt = MOVIMIENTOS_FOTO[i % len(MOVIMIENTOS_FOTO)]["en"]
    return (
        "Cinematic fashion campaign footage. The first frame is the reference image: "
        "keep EXACTLY this person, this garment, this location and this framing. "
        f"Action: {mov_txt}. "
        "SLOW MOTION at half speed, steady gimbal glide, shallow depth of field, natural "
        "light. Gentle life in the background: waves, leaves, light, wind in the hair. "
        "Keep the real background of the photo — do not change the location, the time of "
        "day or the weather. IDENTITY LOCK: same face, same body, same hair, same garment "
        "with the same colors and print from the first frame to the last. No text, no "
        "logos, no morphing, no extra people, no new objects."
    )


# ─────────────────────────────────────────────────────────────────────────────
# FAL: Kling 3.0 Omni (referencia a video, multi-shot)
# ─────────────────────────────────────────────────────────────────────────────

def _uri(b64: str) -> str:
    return f"data:image/jpeg;base64,{b64}"


def _payload_kling(req: Dict[str, Any], tomas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """El pedido a Kling para UNA tanda (hasta 6 tomas, hasta 15 s).

    Las fotos van como referencia: la primera es la cara/frente de la modelo
    (`frontal_image_url`), las tres siguientes son más vistas de ella
    (`reference_image_urls`) y hasta tres más van como referencia de estilo y
    lugar (`image_urls`). En el prompt ella es @Element1."""
    fotos = req["fotos"][:MAX_REFS_KLING]
    elemento = {"frontal_image_url": _uri(fotos[0]),
                "reference_image_urls": [_uri(f) for f in fotos[1:4]]}
    payload: Dict[str, Any] = {
        "multi_prompt": [{"prompt": _prompt_kling_toma(req, t), "duration": int(t["seg"])}
                         for t in tomas],
        "elements": [elemento],
        "duration": int(sum(int(t["seg"]) for t in tomas)),
        "aspect_ratio": req["formato"],
        "generate_audio": False,
        "cfg_scale": 0.5,
        "negative_prompt": _NEGATIVO,
    }
    extras = [_uri(f) for f in fotos[4:7]]
    if extras:
        payload["image_urls"] = extras
    return payload


def _fallbacks_kling(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Variantes del pedido, de la más completa a la más simple. fal rechaza con
    422 lo que un modelo no acepta; se prueba la siguiente en vez de fallar."""
    out = [payload]
    p = dict(payload)
    for campo in ("negative_prompt", "image_urls", "cfg_scale"):
        if campo in p:
            p = {k: v for k, v in p.items() if k != campo}
            out.append(p)
    if "multi_prompt" in p:
        # Sin multi-shot: un solo prompt con las tomas encadenadas.
        q = {k: v for k, v in p.items() if k != "multi_prompt"}
        q["prompt"] = " Then: ".join(x["prompt"] for x in p["multi_prompt"])
        out.append(q)
        p = q
    if "aspect_ratio" in p:
        out.append({k: v for k, v in p.items() if k != "aspect_ratio"})
    return out


async def _fal_key() -> str:
    settings = await get_settings()
    return FAL_KEY or str(settings.get("fal_api_key") or "").strip()


async def _kling_tanda(jid: str, req: Dict[str, Any], k: int,
                       tomas: List[Dict[str, Any]], destino: Path) -> float:
    """Manda una tanda a Kling, espera y baja el video. Devuelve el costo."""
    key = await _fal_key()
    if not key:
        raise RuntimeError("Falta la API key de fal (Fotos → Ajustes, o FAL_KEY en Railway).")
    motor = KLING[req["motor"]]
    url = f"{FAL_BASE}/{motor['modelo']}"
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    payload = _payload_kling(req, tomas)
    seg = int(payload["duration"])
    await _job_set(jid, {"paso": f"Tanda {k + 1}: mandando {len(tomas)} tomas ({seg} s) a "
                                 f"{motor['label']}…"})
    async with httpx.AsyncClient(timeout=240) as cli:
        r = None
        for intento, p in enumerate(_fallbacks_kling(payload)):
            r = await cli.post(url, headers=headers, json=p)
            if r.status_code in (200, 201):
                if intento:
                    print(f"[comerciales] Kling aceptó la variante {intento} del pedido")
                break
            if r.status_code not in (400, 422):
                break
            print(f"[comerciales] Kling rechazó la variante {intento}: {r.text[:200]}")
        assert r is not None
        if r.status_code == 404:
            raise RuntimeError(
                f"fal HTTP 404: el modelo '{motor['modelo']}' no existe con esa ruta. Si fal "
                "se la cambió, cargá la nueva en FAL_KLING_STD_MODEL / FAL_KLING_PRO_MODEL.")
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Kling no aceptó el pedido (HTTP {r.status_code}): {r.text[:300]}")
        data = r.json()
        rid = data.get("request_id") or data.get("requestId")
        status_url = data.get("status_url") or (f"{url}/requests/{rid}/status" if rid else None)
        result_url = data.get("response_url") or (f"{url}/requests/{rid}" if rid else None)
        if not status_url or not result_url:
            raise RuntimeError(f"fal no devolvió status_url: {json.dumps(data)[:200]}")
        inicio = time.time()
        ultimo = ""
        while True:
            if time.time() - inicio > KLING_TIMEOUT:
                raise RuntimeError(f"Kling no terminó la tanda {k + 1} en {KLING_TIMEOUT // 60} minutos.")
            if await _frenado(jid):
                raise RuntimeError("Frenado por la usuaria.")
            rs = await cli.get(status_url, headers=headers)
            d = rs.json() if rs.status_code == 200 else {}
            st = str(d.get("status", ""))
            if st in ("COMPLETED", "Completed", "succeeded", "OK"):
                break
            if st in ("FAILED", "Error", "CANCELLED"):
                raise RuntimeError(f"Kling falló en la tanda {k + 1}: {rs.text[:300]}")
            if st == "IN_QUEUE":
                pos = d.get("queue_position")
                paso = f"Tanda {k + 1}: en la cola de fal" + (f", puesto {pos}" if pos is not None else "") + "…"
            else:
                paso = f"Tanda {k + 1}: {motor['label']} está filmando ({seg} s)…"
            if paso != ultimo:
                await _job_set(jid, {"paso": paso})
                ultimo = paso
            await asyncio.sleep(8)
        rr = await cli.get(result_url, headers=headers)
        if rr.status_code != 200:
            raise RuntimeError(f"fal result HTTP {rr.status_code}: {rr.text[:200]}")
        res = rr.json()
        vurl = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
        if not vurl and res.get("videos"):
            vurl = (res["videos"][0] or {}).get("url")
        vurl = vurl or res.get("video_url") or res.get("url")
        if not vurl:
            raise RuntimeError(f"Kling no devolvió video: {json.dumps(res)[:300]}")
        dl = await cli.get(vurl, follow_redirects=True)
        if dl.status_code != 200:
            raise RuntimeError(f"fal descarga HTTP {dl.status_code}")
        destino.write_bytes(dl.content)
    costo = round(motor["precio_seg"] * seg, 3)
    await budget_record("comercial_kling", motor["modelo"], costo, 1,
                        note=f"comercial tanda {k + 1} ({seg} s, {len(tomas)} tomas)")
    return costo


# ─────────────────────────────────────────────────────────────────────────────
# FFMPEG: deriva lenta sobre una foto, normalizado, transiciones, grade, placa, música
# ─────────────────────────────────────────────────────────────────────────────

def _ff(cmd: List[str], timeout: int = 600) -> Tuple[bool, str]:
    binario = _ffmpeg_bin()
    if not binario:
        return False, "no hay ffmpeg"
    try:
        res = subprocess.run([binario, "-y"] + cmd, capture_output=True, timeout=timeout)
        if res.returncode == 0:
            return True, ""
        err = (res.stderr or b"").decode(errors="replace")[-400:]
        print(f"[comerciales] ffmpeg falló: {err}")
        return False, err
    except Exception as e:
        return False, str(e)[:200]


def _clip_deriva(foto: Path, salida: Path, formato: str, seg: float, idx: int) -> bool:
    """La cámara de edición: una deriva LENTA sobre la foto quieta, distinta en
    cada toma (entra, sale, se corre de costado, baja, va a la cara). Gratis."""
    w, h = _dims(formato)
    cuadros = max(int(round(seg * 24)), 24)
    ult = max(cuadros - 1, 1)
    z = 1.10
    zmax = 1.12
    w2 = min(int(w * 1.16) // 2 * 2, 4096)
    h2 = min(int(h * 1.16) // 2 * 2, 4096)
    t = f"(on/{ult})"
    modos = [
        # z, x, y como expresiones de zoompan
        (f"min(1.0+{zmax - 1:.4f}*{t},{zmax:.4f})", "(iw-iw/zoom)*0.5", "(ih-ih/zoom)*0.40"),   # entra
        (f"max({zmax:.4f}-{zmax - 1:.4f}*{t},1.0)", "(iw-iw/zoom)*0.5", "(ih-ih/zoom)*0.45"),   # sale
        (f"{z:.4f}", f"(iw-iw/zoom)*{t}", "(ih-ih/zoom)*0.45"),                               # derecha
        (f"{z:.4f}", "(iw-iw/zoom)*0.5", f"(ih-ih/zoom)*{t}"),                                # baja
        (f"min(1.0+{0.15:.4f}*{t},1.15)", "(iw-iw/zoom)*0.5", "(ih-ih/zoom)*0.30"),           # a la cara
        (f"{z:.4f}", f"(iw-iw/zoom)*(1-{t})", "(ih-ih/zoom)*0.55"),                           # izquierda
    ]
    zexp, xexp, yexp = modos[idx % len(modos)]
    vf = (f"scale={w2}:{h2}:force_original_aspect_ratio=increase,crop={w2}:{h2},"
          f"zoompan=z='{zexp}':d={cuadros}:x='{xexp}':y='{yexp}':s={w}x{h}:fps=24,"
          "format=yuv420p")
    ok, _ = _ff(["-loop", "1", "-i", str(foto), "-vf", vf, "-frames:v", str(cuadros), "-an",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "19", str(salida)], 300)
    return ok and salida.exists()


def _normalizar_clip(src: Path, dst: Path, formato: str, seg: Optional[float] = None,
                     ralenti: float = 1.0) -> bool:
    """Tamaño exacto (crop-to-fill), 24 fps, mudo; opcionalmente cámara lenta
    (ralenti > 1 estira el tiempo) y recorte a `seg` segundos."""
    w, h = _dims(formato)
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    if ralenti and ralenti > 1.0:
        vf = f"setpts={ralenti:.3f}*PTS," + vf
    vf += ",fps=24,format=yuv420p"
    cmd = ["-i", str(src), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19"]
    if seg:
        cmd += ["-t", f"{seg:.2f}"]
    cmd += [str(dst)]
    ok, _ = _ff(cmd, 600)
    return ok and dst.exists()


def _concatenar(clips: List[Path], salida: Path, formato: str, modo: str) -> bool:
    """Une las tomas. Corte seco con el demuxer; con fundido, xfade (cruzado o
    por negro), con un largo que nunca se come más de un tercio de la toma corta."""
    if not clips:
        return False
    if len(clips) == 1:
        import shutil
        shutil.copy(clips[0], salida)
        return True
    d = TRANSICION_SEG.get(modo, 0.0)
    if modo == "corte" or d <= 0:
        lista = salida.parent / (salida.stem + "_lista.txt")
        lista.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
        binario = _ffmpeg_bin()
        if not binario:
            return False
        res = subprocess.run([binario, "-y", "-f", "concat", "-safe", "0", "-i", lista.name,
                              "-c", "copy", salida.name], capture_output=True,
                             cwd=salida.parent, timeout=600)
        if res.returncode == 0 and salida.exists():
            return True
        res = subprocess.run([binario, "-y", "-f", "concat", "-safe", "0", "-i", lista.name,
                              "-c:v", "libx264", "-preset", "fast", "-crf", "19",
                              "-pix_fmt", "yuv420p", salida.name],
                             capture_output=True, cwd=salida.parent, timeout=600)
        return res.returncode == 0 and salida.exists()
    w, h = _dims(formato)
    durs = [max(_duracion_video(c), 0.1) for c in clips]
    cmd: List[str] = []
    for c in clips:
        cmd += ["-i", str(c)]
    filtros = [f"[{i}:v]scale={w}:{h},setsar=1,fps=24,format=yuv420p[v{i}]"
               for i in range(len(clips))]
    trans = "fadeblack" if modo == "negro" else "fade"
    prev, reloj = "[v0]", durs[0]
    for i in range(1, len(clips)):
        di = max(min(d, min(durs[i - 1], durs[i]) / 3.0), 0.05)
        offset = max(reloj - di, 0)
        etq = f"[x{i}]"
        filtros.append(f"{prev}[v{i}]xfade=transition={trans}:duration={di:.3f}:offset={offset:.3f}{etq}")
        prev = etq
        reloj = offset + durs[i]
    filtros.append(f"{prev}format=yuv420p[vout]")
    ok, _ = _ff(cmd + ["-filter_complex", ";".join(filtros), "-map", "[vout]", "-an",
                       "-c:v", "libx264", "-preset", "fast", "-crf", "19",
                       "-movflags", "+faststart", str(salida)], 900)
    return ok and salida.exists()


def _vf_grade(req: Dict[str, Any], w: int, h: int) -> str:
    """El look de campaña: color, grano, viñeta y (opcional) franjas de cine."""
    partes: List[str] = []
    g = req.get("grade", GRADE_DEFAULT)
    if g == "pelicula":
        # Sombras hacia el teal, luces hacia el naranja, un poco menos saturado y
        # con la curva apenas en S: el grade que usan las campañas de surf.
        # Medido sobre las fotos reales: con más naranja en las luces el cielo y la
        # espuma salían amarillos, sepia. Así queda cálido en la piel y frío en las
        # sombras sin teñir el mar.
        partes.append("curves=r='0/0 0.25/0.23 0.5/0.5 0.75/0.77 1/1':b='0/0.03 0.5/0.5 1/0.97'")
        partes.append("colorbalance=rs=-0.06:gs=-0.01:bs=0.07:rh=0.03:gh=0.01:bh=-0.03")
        partes.append("eq=contrast=1.04:saturation=0.95")
    elif g == "calido":
        partes.append("colorbalance=rs=0.08:gs=0.03:bs=-0.08:rh=0.06:bh=-0.06")
        partes.append("eq=contrast=1.03:saturation=1.05")
    elif g == "frio":
        partes.append("colorbalance=rs=-0.06:bs=0.09:rh=-0.03:bh=0.05")
        partes.append("eq=contrast=1.04:saturation=0.9")
    elif g == "bn":
        partes.append("hue=s=0")
        partes.append("eq=contrast=1.12:brightness=-0.01")
    if req.get("vineta", True):
        partes.append("vignette=angle=PI/5")
    if req.get("grano", True):
        partes.append("noise=alls=7:allf=t+u")
    if req.get("cine"):
        # Franjas arriba y abajo (11 % cada una): el cuadro de cine dentro del vertical.
        hb = int(h * 0.11)
        partes.append(f"drawbox=x=0:y=0:w={w}:h={hb}:color=black:t=fill")
        partes.append(f"drawbox=x=0:y={h - hb}:w={w}:h={hb}:color=black:t=fill")
    partes.append("format=yuv420p")
    return ",".join(partes)


def _aplicar_grade(src: Path, dst: Path, req: Dict[str, Any]) -> bool:
    w, h = _dims(req["formato"])
    ok, _ = _ff(["-i", str(src), "-vf", _vf_grade(req, w, h), "-an", "-c:v", "libx264",
                 "-preset", "fast", "-crf", "18", str(dst)], 900)
    return ok and dst.exists()


def _placa_png(texto: str, sub: str, w: int, h: int, destino: Path) -> bool:
    """La placa final: la marca en serif sobre negro, y una línea chica abajo."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return False
    img = Image.new("RGB", (w, h), (8, 8, 10))
    dr = ImageDraw.Draw(img)
    fp = _font_path()
    try:
        f1 = ImageFont.truetype(fp, int(w * 0.085)) if fp else ImageFont.load_default()
        f2 = ImageFont.truetype(fp, int(w * 0.03)) if fp else ImageFont.load_default()
    except Exception:
        f1 = f2 = ImageFont.load_default()
    texto = (texto or "").strip() or PLACA_DEFAULT
    b = dr.textbbox((0, 0), texto, font=f1)
    tw, th = b[2] - b[0], b[3] - b[1]
    dr.text(((w - tw) / 2 - b[0], (h - th) / 2 - b[1] - int(h * 0.02)), texto,
            font=f1, fill=(236, 226, 205))
    if sub.strip():
        b2 = dr.textbbox((0, 0), sub.strip(), font=f2)
        dr.text(((w - (b2[2] - b2[0])) / 2 - b2[0], (h + th) / 2 + int(h * 0.02)),
                sub.strip(), font=f2, fill=(150, 145, 160))
    img.save(destino, "PNG")
    return True


def _clip_placa(req: Dict[str, Any], d: Path) -> Optional[Path]:
    w, h = _dims(req["formato"])
    png = d / "placa.png"
    if not _placa_png(req.get("placa_texto", ""), req.get("placa_sub", ""), w, h, png):
        return None
    out = d / "placa.mp4"
    cuadros = int(PLACA_SEG * 24)
    ok, _ = _ff(["-loop", "1", "-framerate", "24", "-i", str(png), "-frames:v", str(cuadros),
                 "-vf", f"fade=t=in:st=0:d=0.7,fade=t=out:st={PLACA_SEG - 0.5:.2f}:d=0.5,"
                        "format=yuv420p",
                 "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19", str(out)], 300)
    return out if ok and out.exists() else None


def _con_musica(src: Path, dst: Path, musica: Path, vol: float = 0.5) -> bool:
    dur = _duracion_video(src) or 1.0
    tope = f"{dur:.2f}"
    ok, _ = _ff(["-i", str(src), "-stream_loop", "-1", "-t", tope, "-i", str(musica),
                 "-filter_complex",
                 f"[1:a]volume={vol:.2f},afade=t=in:st=0:d=1.0,"
                 f"afade=t=out:st={max(dur - 1.5, 0):.2f}:d=1.5[a]",
                 "-map", "0:v", "-map", "[a]", "-t", tope, "-c:v", "copy",
                 "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)], 600)
    return ok and dst.exists()


# ─────────────────────────────────────────────────────────────────────────────
# JOBS
# ─────────────────────────────────────────────────────────────────────────────

def _k_job(jid: str) -> str:
    return _pfx() + "comjob:" + jid


def _k_indice() -> str:
    return _pfx() + "comjobs"


async def _job_set(jid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    job = (await kv.get(_k_job(jid))) or {"job_id": jid}
    job.update(patch)
    job["latido"] = time.time()
    await kv.set(_k_job(jid), job, ttl=JOB_TTL)
    return job


async def _job_get(jid: str) -> Optional[Dict[str, Any]]:
    return await kv.get(_k_job(jid))


async def _frenado(jid: str) -> bool:
    job = await _job_get(jid)
    return bool(job and job.get("frenar"))


async def _indice_agregar(jid: str) -> None:
    lst = (await kv.get(_k_indice())) or []
    lst = [jid] + [x for x in lst if x != jid]
    await kv.set(_k_indice(), lst[:JOBS_INDICE])


def _dir(jid: str) -> Path:
    d = WORK_DIR / jid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _purgar_viejos() -> None:
    import shutil
    limite = time.time() - JOB_TTL
    try:
        for d in WORK_DIR.iterdir():
            if d.is_dir() and d.stat().st_mtime < limite:
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# PEDIDO, ESTIMACIÓN, PROCESO
# ─────────────────────────────────────────────────────────────────────────────

def _tandas(tomas: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Parte la lista de tomas en tandas de hasta 6 tomas y 15 s para Kling."""
    out: List[List[Dict[str, Any]]] = []
    actual: List[Dict[str, Any]] = []
    seg = 0
    for t in tomas:
        s = int(t["seg"])
        if actual and (len(actual) >= TOMAS_POR_TANDA or seg + s > TANDA_SEG):
            out.append(actual)
            actual, seg = [], 0
        actual.append(t)
        seg += s
    if actual:
        out.append(actual)
    return out


def _tomas_plantilla(plantilla: str, total: int) -> List[Dict[str, Any]]:
    """Las tomas de la plantilla que entran en `total` segundos, en orden."""
    lista = list(PLANTILLAS.get(plantilla, PLANTILLAS[PLANTILLA_DEFAULT])["tomas"])
    out: List[Dict[str, Any]] = []
    seg = 0
    for t in lista:
        if seg + int(t["seg"]) > total:
            break
        out.append(dict(t))
        seg += int(t["seg"])
    return out


def _ia_seg(seg: float) -> int:
    return 5 if seg <= 5.5 else 10


def _estimar(req: Dict[str, Any]) -> Dict[str, Any]:
    if req["modo"] == "kling":
        tandas = _tandas(req["tomas"])
        seg = sum(int(t["seg"]) for t in req["tomas"])
        usd = round(KLING[req["motor"]]["precio_seg"] * seg, 3)
        return {"usd_total": usd, "segundos": seg, "tandas": len(tandas),
                "detalle": f"{len(tandas)} tanda(s) de Kling · {seg} s de video · "
                           f"USD {usd:.2f} (+ placa y música gratis)"}
    usd = 0.0
    seg_total = 0.0
    n_ia = 0
    for t in req["tomas"]:
        seg_total += float(t["seg"])
        if t["motor"] == "ia":
            n_ia += 1
            usd += PRECIO_SEG.get(req["motor_ia"], 0.05) * _ia_seg(float(t["seg"]))
    usd = round(usd, 3)
    return {"usd_total": usd, "segundos": round(seg_total, 1), "tandas": 0,
            "detalle": f"{len(req['tomas'])} tomas ({n_ia} con IA, "
                       f"{len(req['tomas']) - n_ia} de cámara) · {seg_total:.0f} s · USD {usd:.2f}"}


def _normalizar_pedido(payload: Dict[str, Any]) -> Dict[str, Any]:
    req: Dict[str, Any] = {}
    fotos = [_strip_data_url(str(f)) for f in (payload.get("fotos") or []) if f][:MAX_FOTOS]
    if not fotos:
        raise HTTPException(400, "Subí al menos una foto de la modelo con la prenda.")
    livianas = []
    for f in fotos:
        try:
            livianas.append(_compress_ref(base64.b64decode(f), max_dim=2000, q=90))
        except Exception:
            livianas.append(f)
    req["fotos"] = livianas
    req["modo"] = payload.get("modo") if payload.get("modo") in MODOS else "kling"
    req["formato"] = payload.get("formato") if payload.get("formato") in FORMATOS else "9:16"
    req["motor"] = payload.get("motor") if payload.get("motor") in KLING else KLING_DEFAULT
    req["motor_ia"] = (payload.get("motor_ia") if payload.get("motor_ia") in FAL_MODELS
                       else "seedance")
    req["plantilla"] = (payload.get("plantilla") if payload.get("plantilla") in PLANTILLAS
                        else PLANTILLA_DEFAULT)
    try:
        total = int(payload.get("duracion_total") or 15)
    except (TypeError, ValueError):
        total = 15
    req["duracion_total"] = total if total in DURACIONES_TOTAL else 15
    req["grade"] = payload.get("grade") if payload.get("grade") in GRADES else GRADE_DEFAULT
    req["grano"] = payload.get("grano") is not False
    req["vineta"] = payload.get("vineta") is not False
    req["cine"] = bool(payload.get("cine"))
    req["transicion"] = (payload.get("transicion") if payload.get("transicion") in TRANSICIONES
                         else "corte")
    req["placa"] = payload.get("placa") is not False
    req["placa_texto"] = str(payload.get("placa_texto") or PLACA_DEFAULT).strip()[:40]
    req["placa_sub"] = str(payload.get("placa_sub") or "").strip()[:60]
    req["musica"] = bool(payload.get("musica"))
    req["ralenti"] = bool(payload.get("ralenti", True))
    pl = PLANTILLAS[req["plantilla"]]
    req["lugar_es"] = str(payload.get("lugar") or pl["lugar_es"]).strip()[:300]
    req["lugar_en"] = pl["lugar_en"] if req["lugar_es"] == pl["lugar_es"] else ""

    tomas_in = payload.get("tomas") or []
    tomas: List[Dict[str, Any]] = []
    if req["modo"] == "kling":
        base = _tomas_plantilla(req["plantilla"], req["duracion_total"])
        if tomas_in:
            por_es = {t["es"]: t for t in pl["tomas"]}
            for t in tomas_in[:MAX_TOMAS_LIBRES]:
                txt = str((t or {}).get("texto") or "").strip()[:300]
                if not txt:
                    continue
                try:
                    seg = int(round(float((t or {}).get("seg") or 3)))
                except (TypeError, ValueError):
                    seg = 3
                seg = max(TOMA_SEG_MIN, min(TOMA_SEG_MAX, seg))
                orig = por_es.get(txt)
                tomas.append({"es": txt, "en": (orig["en"] if orig else ""), "seg": seg})
        else:
            tomas = base
        if not tomas:
            raise HTTPException(400, "Escribí al menos una toma (o elegí una plantilla).")
        # No más de lo que entra en las tandas de la duración pedida.
        tope = req["duracion_total"]
        rec: List[Dict[str, Any]] = []
        seg = 0
        for t in tomas:
            if seg + t["seg"] > tope:
                break
            rec.append(t)
            seg += t["seg"]
        tomas = rec or tomas[:1]
    else:
        n = len(req["fotos"])
        for i in range(n):
            t = tomas_in[i] if i < len(tomas_in) and isinstance(tomas_in[i], dict) else {}
            motor = t.get("motor") if t.get("motor") in MOTORES_FOTO else "camara"
            try:
                seg = float(t.get("seg") or 3.0)
            except (TypeError, ValueError):
                seg = 3.0
            ok = SEG_IA_OK if motor == "ia" else SEG_CAMARA_OK
            seg = min(ok, key=lambda v: abs(v - seg))
            tomas.append({"motor": motor, "seg": seg,
                          "es": str(t.get("texto") or "").strip()[:200], "en": ""})
    req["tomas"] = tomas
    return req


async def _traducir_tomas(req: Dict[str, Any]) -> None:
    """Las tomas escritas por ella (sin versión en inglés) se traducen en UNA
    llamada; si falla, viajan en castellano, que los motores también entienden."""
    faltan = {str(i): t["es"] for i, t in enumerate(req["tomas"]) if t.get("es") and not t.get("en")}
    if req["modo"] == "kling" and req["lugar_es"] and not req["lugar_en"]:
        faltan["lugar"] = req["lugar_es"]
    if not faltan:
        return
    try:
        tr = await _traducir_libres(faltan)
    except Exception:
        tr = {}
    for k, v in tr.items():
        if k == "lugar":
            req["lugar_en"] = v
        else:
            req["tomas"][int(k)]["en"] = v
    if req["modo"] == "kling" and req["lugar_es"] and not req["lugar_en"]:
        req["lugar_en"] = req["lugar_es"]
    for t in req["tomas"]:
        if t.get("es") and not t.get("en"):
            t["en"] = t["es"]


async def _procesar(jid: str, req: Dict[str, Any]) -> None:
    d = _dir(jid)
    costo = 0.0
    try:
        set_current_sub(req.get("user_sub"))
        await _job_set(jid, {"estado": "trabajando", "paso": "Preparando las fotos…"})
        fotos_disco: List[Path] = []
        for i, b in enumerate(req["fotos"]):
            p = d / f"foto_{i}.jpg"
            p.write_bytes(base64.b64decode(b))
            fotos_disco.append(p)
        await _traducir_tomas(req)
        clips: List[Path] = []
        if req["modo"] == "kling":
            tandas = _tandas(req["tomas"])
            for k, tomas in enumerate(tandas):
                if await _frenado(jid):
                    raise RuntimeError("Frenado por la usuaria.")
                crudo = d / f"tanda_{k}.mp4"
                costo += await _kling_tanda(jid, req, k, tomas, crudo)
                await _job_set(jid, {"paso": f"Tanda {k + 1}: acomodando el clip…", "costo": costo,
                                     "tandas_listas": k + 1})
                norm = d / f"clip_{k}.mp4"
                ral = 1.0   # Kling ya filma en cámara lenta cuando el prompt lo pide
                if not _normalizar_clip(crudo, norm, req["formato"], None, ral):
                    raise RuntimeError(f"No pude acomodar el clip de la tanda {k + 1}.")
                clips.append(norm)
        else:
            for i, t in enumerate(req["tomas"]):
                if await _frenado(jid):
                    raise RuntimeError("Frenado por la usuaria.")
                foto = fotos_disco[i]
                norm = d / f"clip_{i}.mp4"
                if t["motor"] == "ia":
                    await _job_set(jid, {"paso": f"Toma {i + 1}: {MOTOR_LABEL.get(req['motor_ia'], req['motor_ia'])} "
                                                 f"está filmando la foto ({_ia_seg(t['seg'])} s)…"})
                    crudo = d / f"ia_{i}.mp4"
                    frame = base64.b64encode(foto.read_bytes()).decode()
                    await _generar_fal(_prompt_foto_ia(req, i, t), frame, req["motor_ia"],
                                       crudo, _ia_seg(t["seg"]))
                    c = round(PRECIO_SEG.get(req["motor_ia"], 0.05) * _ia_seg(t["seg"]), 3)
                    costo += c
                    await budget_record("comercial_ia", FAL_MODELS[req["motor_ia"]], c, 1,
                                        note=f"comercial toma {i + 1} ({_ia_seg(t['seg'])} s)")
                    ral = 1.5 if req.get("ralenti") else 1.0
                    if not _normalizar_clip(crudo, norm, req["formato"], float(t["seg"]), ral):
                        raise RuntimeError(f"No pude acomodar el clip de la toma {i + 1}.")
                else:
                    await _job_set(jid, {"paso": f"Toma {i + 1}: cámara lenta sobre la foto…"})
                    if not await asyncio.to_thread(_clip_deriva, foto, norm, req["formato"],
                                                   float(t["seg"]), i):
                        raise RuntimeError(f"No pude armar la toma {i + 1} con la cámara.")
                clips.append(norm)
                await _job_set(jid, {"costo": costo, "tomas_listas": i + 1})

        await _job_set(jid, {"paso": "Pegando las tomas…"})
        unido = d / "unido.mp4"
        if not await asyncio.to_thread(_concatenar, clips, unido, req["formato"], req["transicion"]):
            raise RuntimeError("No pude pegar las tomas.")
        await _job_set(jid, {"paso": "Aplicando el grade de película…"})
        con_grade = d / "grade.mp4"
        if not await asyncio.to_thread(_aplicar_grade, unido, con_grade, req):
            con_grade = unido
        partes = [con_grade]
        if req.get("placa"):
            placa = await asyncio.to_thread(_clip_placa, req, d)
            if placa:
                partes.append(placa)
        mudo = d / "mudo.mp4"
        if len(partes) > 1:
            modo_placa = "negro" if req["transicion"] == "corte" else "fundido"
            if not await asyncio.to_thread(_concatenar, partes, mudo, req["formato"], modo_placa):
                mudo = con_grade
        else:
            mudo = con_grade
        final = d / "final.mp4"
        mus = _musica_path()
        if req.get("musica") and mus.exists():
            await _job_set(jid, {"paso": "Sumando la música…"})
            if not await asyncio.to_thread(_con_musica, mudo, final, mus):
                import shutil
                shutil.copy(mudo, final)
        else:
            import shutil
            shutil.copy(mudo, final)
        dur = _duracion_video(final)
        await _job_set(jid, {"estado": "listo", "paso": "Listo.", "costo": round(costo, 3),
                             "duracion": round(dur, 1), "final": True,
                             "terminado": time.time()})
    except Exception as e:
        print(f"[comerciales] job {jid} falló: {e}")
        await _job_set(jid, {"estado": "error", "detalle": str(e)[:400], "costo": round(costo, 3)})


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

async def _bind(request: Request) -> None:
    set_current_sub(session_sub_from_request(request))


router = APIRouter(dependencies=[Depends(_bind)])


def _publico(job: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in job.items() if k not in ("fotos",)}


@router.get(ROUTE_PREFIX + "/api/config")
async def api_config() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "modos": MODOS,
        "motores_kling": {k: {"label": v["label"], "precio_seg": v["precio_seg"]} for k, v in KLING.items()},
        "motores_ia": {k: MOTOR_LABEL.get(k, k) for k in FAL_MODELS},
        "plantillas": {k: {"nombre": v["nombre"], "desc": v["desc"], "lugar": v["lugar_es"],
                           "tomas": [{"texto": t["es"], "seg": t["seg"]} for t in v["tomas"]]}
                       for k, v in PLANTILLAS.items()},
        "grades": GRADES, "transiciones": TRANSICIONES, "duraciones": DURACIONES_TOTAL,
        "movimientos_foto": [m["es"] for m in MOVIMIENTOS_FOTO],
        "seg_camara": SEG_CAMARA_OK, "seg_ia": SEG_IA_OK,
        "musica": _musica_path().exists(), "max_fotos": MAX_FOTOS, "max_refs": MAX_REFS_KLING,
        "fal_key": bool(await _fal_key()),
    }


@router.post(ROUTE_PREFIX + "/api/estimar")
async def api_estimar(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    req = _normalizar_pedido(payload)
    est = _estimar(req)
    est["tomas"] = [{"texto": t.get("es", ""), "seg": t["seg"], "motor": t.get("motor", "kling")}
                    for t in req["tomas"]]
    return est


@router.post(ROUTE_PREFIX + "/api/generar")
async def api_generar(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    req = _normalizar_pedido(payload)
    req["user_sub"] = session_sub_from_request(request)
    necesita_fal = req["modo"] == "kling" or any(t["motor"] == "ia" for t in req["tomas"])
    if necesita_fal and not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal: cargala en Fotos → Ajustes (API key de "
                                 "fal) o como FAL_KEY en Railway.")
    if req["modo"] == "kling" and len(req["fotos"]) < 1:
        raise HTTPException(400, "Kling necesita al menos una foto de referencia.")
    est = _estimar(req)
    permitido, motivo, _t, _c = await budget_check(est["usd_total"])
    if not permitido:
        raise HTTPException(402, motivo)
    _purgar_viejos()
    jid = _uuid.uuid4().hex[:12]
    await _job_set(jid, {
        "job_id": jid, "estado": "encolado", "paso": "En cola…", "creado": time.time(),
        "modo": req["modo"], "motor": (KLING[req["motor"]]["label"] if req["modo"] == "kling"
                                       else MOTOR_LABEL.get(req["motor_ia"], req["motor_ia"])),
        "plantilla": PLANTILLAS[req["plantilla"]]["nombre"] if req["modo"] == "kling" else "Foto por foto",
        "formato": req["formato"], "estimado": est, "n_fotos": len(req["fotos"]),
        "tomas": [{"texto": t.get("es", ""), "seg": t["seg"], "motor": t.get("motor", "kling")}
                  for t in req["tomas"]],
        "tandas": len(_tandas(req["tomas"])) if req["modo"] == "kling" else 0,
        "costo": 0.0,
    })
    await _indice_agregar(jid)
    _spawn(_procesar(jid, req))
    return {"job_id": jid, "estimado": est}


@router.get(ROUTE_PREFIX + "/api/estado/{jid}")
async def api_estado(jid: str) -> Dict[str, Any]:
    job = await _job_get(jid)
    if not job:
        raise HTTPException(404, "Ese trabajo no existe (o ya venció).")
    return _publico(job)


@router.post(ROUTE_PREFIX + "/api/frenar/{jid}")
async def api_frenar(jid: str) -> Dict[str, Any]:
    job = await _job_get(jid)
    if not job:
        raise HTTPException(404, "Ese trabajo no existe.")
    await _job_set(jid, {"frenar": True})
    return {"ok": True}


@router.get(ROUTE_PREFIX + "/api/jobs")
async def api_jobs() -> Dict[str, Any]:
    ids = (await kv.get(_k_indice())) or []
    out = []
    for jid in ids[:JOBS_INDICE]:
        j = await _job_get(jid)
        if j:
            out.append({k: j.get(k) for k in ("job_id", "estado", "paso", "modo", "motor", "plantilla",
                                              "costo", "duracion", "creado", "final", "detalle")})
    return {"jobs": out}


@router.get(ROUTE_PREFIX + "/api/final/{jid}")
async def api_final(jid: str):
    job = await _job_get(jid)
    if not job:
        raise HTTPException(404, "Ese trabajo no existe.")
    p = _dir(jid) / "final.mp4"
    if not p.exists():
        raise HTTPException(404, "Todavía no hay video final.")
    return FileResponse(str(p), media_type="video/mp4", filename=f"comercial_{jid}.mp4")


@router.get(ROUTE_PREFIX + "/api/clip/{jid}/{n}")
async def api_clip(jid: str, n: int):
    p = _dir(jid) / f"clip_{n}.mp4"
    if not p.exists():
        raise HTTPException(404, "Ese clip no existe todavía.")
    return FileResponse(str(p), media_type="video/mp4")


@router.post(ROUTE_PREFIX + "/api/musica")
async def api_musica(archivo: UploadFile = File(...)) -> Dict[str, Any]:
    """La misma cortina musical que usa Videos (una por cuenta)."""
    data = await archivo.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, "La música pesa más de 25 MB.")
    _musica_path().write_bytes(data)
    return {"ok": True}


@router.get(ROUTE_PREFIX + "/api/health")
async def api_health() -> Dict[str, Any]:
    return {"ok": True, "version": VERSION, "ffmpeg": bool(_ffmpeg_bin()),
            "kling": {k: v["modelo"] for k, v in KLING.items()}}


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    html = (HTML_PAGE
            .replace("%%API%%", ROUTE_PREFIX + "/api")
            .replace("%%HOME%%", os.environ.get("IMAGENES_PREFIX", "") or "/")
            .replace("%%VIDEOS%%", os.environ.get("VIDEOS_PREFIX", "/videos"))
            .replace("%%VERSION%%", VERSION))
    return HTMLResponse(html)


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="es-AR">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Studio Luma · Comerciales</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%23161419'/%3E%3Crect x='2.5' y='2.5' width='59' height='59' rx='12' fill='none' stroke='%23c9a86b' stroke-width='2'/%3E%3Ctext x='32' y='44' font-family='Georgia,serif' font-size='34' font-weight='600' fill='%23d8b878' text-anchor='middle'%3ESL%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:opsz,wght@6..96,400;6..96,500;6..96,600&family=Jost:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;
    --rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f;
    --shadow:0 1px 2px rgba(0,0,0,.5),0 12px 34px rgba(0,0,0,.4)}
  *{box-sizing:border-box}
  body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:17px;line-height:1.55;-webkit-font-smoothing:antialiased}
  a{color:var(--rose-deep);text-decoration:none}
  header{padding:16px 16px 12px;border-bottom:1px solid var(--line);background:rgba(19,18,24,.9);backdrop-filter:blur(8px);position:sticky;top:0;z-index:20}
  .brandrow{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .mono{width:40px;height:40px;border-radius:11px;border:1px solid var(--rose);display:flex;align-items:center;justify-content:center;flex:none;background:linear-gradient(150deg,#221f27,#161419);font-family:'Bodoni Moda',serif;font-weight:600;font-size:19px;color:var(--rose-deep)}
  .brand{font-family:'Bodoni Moda',serif;font-size:23px;font-weight:600;line-height:1}
  .brand small{font-family:Jost;font-weight:400;font-size:11px;color:var(--ink-soft);letter-spacing:.22em;text-transform:uppercase;display:block;margin-top:5px}
  .volver{font-size:13px;border:1px solid var(--line);border-radius:99px;padding:6px 12px;background:var(--card)}
  .links{margin-left:auto;display:flex;gap:6px}
  main{max-width:900px;margin:0 auto;padding:16px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:16px;box-shadow:var(--shadow)}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:23px;margin:0 0 6px}
  h3{font-family:'Bodoni Moda',serif;font-weight:500;font-size:18px;margin:0 0 4px}
  .hint{color:var(--ink-soft);font-size:14.5px;margin:0 0 12px}
  label{display:block;font-size:14px;font-weight:500;color:var(--ink-soft);margin:12px 0 6px}
  input,select,textarea{width:100%;padding:12px 13px;border:1px solid var(--line);border-radius:11px;background:var(--card-2);color:var(--ink);font-family:Jost,sans-serif;font-size:16px}
  textarea{min-height:70px;resize:vertical}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
  @media(max-width:640px){.row,.row3{grid-template-columns:1fr}}
  .chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
  .chip{padding:9px 14px;border-radius:999px;border:1px solid var(--line);background:var(--card-2);cursor:pointer;font-size:14.5px;color:var(--ink-soft);user-select:none}
  .chip.on{border-color:var(--rose);color:var(--rose-deep);background:rgba(201,168,107,.1)}
  .btn{display:inline-flex;align-items:center;gap:8px;padding:13px 20px;border-radius:12px;border:1px solid var(--rose);background:linear-gradient(150deg,#2a2431,#1b1a21);color:var(--rose-deep);font-family:Jost;font-size:16px;font-weight:500;cursor:pointer}
  .btn.sec{border-color:var(--line);color:var(--ink-soft)}
  .btn:disabled{opacity:.5;cursor:default}
  .fotos{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:8px;margin-top:10px}
  .foto{position:relative;aspect-ratio:4/5;border-radius:10px;overflow:hidden;border:1px solid var(--line);background:#000}
  .foto img{width:100%;height:100%;object-fit:cover;display:block}
  .foto .n{position:absolute;top:5px;left:5px;background:rgba(0,0,0,.65);color:#fff;font-size:12px;border-radius:99px;padding:1px 8px}
  .foto .x{position:absolute;top:4px;right:4px;background:rgba(0,0,0,.65);color:#fff;border:0;border-radius:99px;width:22px;height:22px;cursor:pointer;font-size:13px}
  .foto .mv{position:absolute;bottom:4px;left:4px;right:4px;display:flex;justify-content:space-between}
  .foto .mv button{background:rgba(0,0,0,.65);color:#fff;border:0;border-radius:6px;padding:1px 7px;cursor:pointer;font-size:12px}
  .tomas{display:flex;flex-direction:column;gap:8px;margin-top:8px}
  .toma{display:grid;grid-template-columns:28px 1fr 84px 30px;gap:8px;align-items:center}
  .toma.foto-toma{grid-template-columns:28px 1fr 110px 84px}
  .toma .idx{color:var(--ink-soft);font-size:13px;text-align:right}
  .toma input,.toma select{padding:9px 10px;font-size:14.5px}
  .toma button{background:none;border:0;color:var(--bad);cursor:pointer;font-size:16px}
  .sw{display:flex;align-items:center;gap:10px;margin:10px 0;font-size:15px;cursor:pointer}
  .sw input{width:auto}
  .est{font-size:15px;color:var(--rose-deep);margin-top:10px}
  .prog{background:var(--card-2);border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:12px;font-size:15px}
  .bar{height:6px;background:var(--line);border-radius:99px;overflow:hidden;margin-top:8px}
  .bar i{display:block;height:100%;background:var(--rose);width:0;transition:.4s}
  video{width:100%;max-height:70vh;border-radius:12px;background:#000;margin-top:12px}
  .err{color:var(--bad);font-size:14.5px;margin-top:8px}
  .hist{display:flex;flex-direction:column;gap:8px}
  .hist .it{display:flex;justify-content:space-between;gap:10px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card-2);font-size:14.5px;align-items:center}
  .hist .it span{color:var(--ink-soft)}
  .hidden{display:none}
  .q{display:inline-block;width:18px;height:18px;border-radius:99px;border:1px solid var(--line);color:var(--ink-soft);font-size:12px;text-align:center;line-height:16px;margin-left:5px;cursor:help}
</style>
</head>
<body>
<header>
  <div class="brandrow">
    <div class="mono">SL</div>
    <div class="brand">Comerciales<small>Studio Luma · v%%VERSION%%</small></div>
    <div class="links">
      <a class="volver" href="%%HOME%%">← Fotos</a>
      <a class="volver" href="%%VIDEOS%%">🎬 Videos</a>
    </div>
  </div>
</header>
<main>
  <div class="card">
    <h2>Un comercial con tus fotos</h2>
    <p class="hint">Estilo campaña: cámara lenta, luz natural, grade de película, música y la placa de la marca al final. Subí las fotos de la modelo con la prenda, en locación.</p>
    <input type="file" id="f-fotos" accept="image/*" multiple>
    <div class="fotos" id="fotos"></div>
    <p class="hint" id="fotos-ayuda" style="margin-top:8px"></p>
  </div>

  <div class="card">
    <h3>Cómo se arma</h3>
    <div class="chips" id="modos"></div>
    <p class="hint" id="modo-ayuda" style="margin-top:8px"></p>

    <div id="panel-kling">
      <div class="row">
        <div><label>Plantilla</label><select id="plantilla"></select></div>
        <div><label>Duración total</label><select id="duracion"></select></div>
      </div>
      <div class="row">
        <div><label>Motor</label><select id="motor"></select></div>
        <div><label>Formato</label><select id="formato"><option value="9:16">Vertical 9:16 (reel)</option><option value="16:9">Horizontal 16:9</option></select></div>
      </div>
      <label>Lugar <span class="q" title="Dónde transcurre el comercial. Se llena con la plantilla; podés cambiarlo.">?</span></label>
      <input id="lugar" placeholder="una playa de surf: un shack de madera con tablas, una camioneta en la arena, olas">
      <label>Las tomas, en orden <span class="q" title="Cada toma tiene su texto y sus segundos. Kling filma hasta 6 tomas y 15 segundos por tanda; un comercial de 30 son dos tandas pegadas.">?</span></label>
      <div class="tomas" id="tomas-kling"></div>
      <div style="margin-top:8px"><button class="btn sec" id="add-toma">+ toma</button></div>
    </div>

    <div id="panel-fotos" class="hidden">
      <div class="row">
        <div><label>Motor para las tomas con IA</label><select id="motor-ia"></select></div>
        <div><label>Formato</label><select id="formato2"><option value="9:16">Vertical 9:16 (reel)</option><option value="16:9">Horizontal 16:9</option></select></div>
      </div>
      <label class="sw"><input type="checkbox" id="ralenti" checked> Cámara lenta en las tomas con IA (se estiran 1,5×)</label>
      <label>Cada foto, en su orden <span class="q" title="Cámara: una deriva lenta sobre la foto, gratis. IA: la foto cobra vida (viento, olas, ella se mueve), conservando el fondo real. Podés escribir qué hace en esa toma.">?</span></label>
      <div class="tomas" id="tomas-fotos"></div>
    </div>
  </div>

  <div class="card">
    <h3>Terminación</h3>
    <div class="row3">
      <div><label>Grade de película</label><select id="grade"></select></div>
      <div><label>Entre tomas</label><select id="transicion"></select></div>
      <div><label>Placa final (texto)</label><input id="placa-texto" value="LUMA Íntima" maxlength="40"></div>
    </div>
    <div class="row">
      <div><label>Placa final (línea chica)</label><input id="placa-sub" placeholder="@lumaintima · nueva colección" maxlength="60"></div>
      <div><label>Música <span class="q" title="La misma cortina que usás en Videos. Subila acá o allá; es una por cuenta.">?</span></label><input type="file" id="f-musica" accept="audio/*"></div>
    </div>
    <label class="sw"><input type="checkbox" id="placa" checked> Placa final con la marca</label>
    <label class="sw"><input type="checkbox" id="musica"> Sumar la música <span id="musica-estado" style="color:var(--ink-soft)"></span></label>
    <label class="sw"><input type="checkbox" id="grano" checked> Grano fino de película</label>
    <label class="sw"><input type="checkbox" id="vineta" checked> Viñeta suave</label>
    <label class="sw"><input type="checkbox" id="cine"> Franjas de cine (arriba y abajo)</label>
  </div>

  <div class="card">
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      <button class="btn sec" id="estimar">Calcular costo</button>
      <button class="btn" id="generar">🎬 Armar el comercial</button>
      <button class="btn sec hidden" id="frenar">Frenar</button>
    </div>
    <div class="est" id="est"></div>
    <div class="err" id="err"></div>
    <div class="prog hidden" id="prog"><div id="paso"></div><div class="bar"><i id="bar"></i></div></div>
    <video id="video" class="hidden" controls playsinline></video>
    <div id="descarga" class="hidden" style="margin-top:8px"><a class="btn sec" id="dl" download>⬇ Descargar</a></div>
  </div>

  <div class="card">
    <h3>Comerciales anteriores</h3>
    <div class="hist" id="hist"></div>
  </div>
</main>
<script>
const API = "%%API%%";
const $ = s => document.querySelector(s);
let CFG = null, FOTOS = [], MODO = "kling", JOB = null, TIMER = null;
const esc = s => String(s || "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function api(path, opts) {
  const r = await fetch(API + path, opts);
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((d && d.detail) || ("HTTP " + r.status));
  return d;
}
function leer(file) {
  return new Promise((ok, no) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = no; fr.readAsDataURL(file); });
}
function pintarFotos() {
  const c = $("#fotos"); c.innerHTML = "";
  FOTOS.forEach((f, i) => {
    const d = document.createElement("div"); d.className = "foto";
    d.innerHTML = `<img src="${f}"><span class="n">${i + 1}</span><button class="x" title="Sacar">×</button>
      <div class="mv"><button title="Antes">‹</button><button title="Después">›</button></div>`;
    d.querySelector(".x").onclick = () => { FOTOS.splice(i, 1); pintarFotos(); };
    const [a, b] = d.querySelectorAll(".mv button");
    a.onclick = () => { if (i > 0) { [FOTOS[i - 1], FOTOS[i]] = [FOTOS[i], FOTOS[i - 1]]; pintarFotos(); } };
    b.onclick = () => { if (i < FOTOS.length - 1) { [FOTOS[i + 1], FOTOS[i]] = [FOTOS[i], FOTOS[i + 1]]; pintarFotos(); } };
    c.appendChild(d);
  });
  const n = FOTOS.length;
  $("#fotos-ayuda").textContent = !n ? "Todavía no hay fotos." :
    (MODO === "kling" ? `${n} foto(s). Kling usa hasta ${CFG.max_refs} como referencia: la 1 es la cara de la modelo, la 2 a la 4 más vistas de ella, la 5 a la 7 el lugar. Las demás no viajan.`
                      : `${n} toma(s), una por foto, en este orden.`);
  if (MODO === "fotos") pintarTomasFotos();
}
$("#f-fotos").onchange = async e => {
  for (const f of Array.from(e.target.files || [])) { if (FOTOS.length >= CFG.max_fotos) break; FOTOS.push(await leer(f)); }
  e.target.value = ""; pintarFotos();
};
function opciones(sel, obj, val) {
  sel.innerHTML = "";
  for (const [k, v] of Object.entries(obj)) { const o = document.createElement("option"); o.value = k; o.textContent = typeof v === "string" ? v : (v.label || v.nombre || k); sel.appendChild(o); }
  if (val !== undefined) sel.value = val;
}
function setModo(m) {
  MODO = m;
  document.querySelectorAll("#modos .chip").forEach(c => c.classList.toggle("on", c.dataset.m === m));
  $("#panel-kling").classList.toggle("hidden", m !== "kling");
  $("#panel-fotos").classList.toggle("hidden", m !== "fotos");
  $("#modo-ayuda").textContent = m === "kling"
    ? "Kling 3.0 Omni inventa las tomas con tus fotos de guía: sale una sola pieza con continuidad, como filmada. Hasta 15 s por tanda; 30 s son dos tandas."
    : "Cada foto es una toma, en su orden. Fidelidad exacta a cada foto: la cámara de edición es gratis, la IA la hace vivir (se paga por toma).";
  pintarFotos();
}
function tomasPlantilla() {
  const p = CFG.plantillas[$("#plantilla").value]; const total = parseInt($("#duracion").value || "15", 10);
  const out = []; let seg = 0;
  for (const t of p.tomas) { if (seg + t.seg > total) break; out.push({texto: t.texto, seg: t.seg}); seg += t.seg; }
  $("#lugar").value = p.lugar || "";
  pintarTomasKling(out);
}
function pintarTomasKling(lista) {
  const c = $("#tomas-kling"); c.innerHTML = "";
  (lista || leerTomasKling()).forEach((t, i) => {
    const d = document.createElement("div"); d.className = "toma";
    d.innerHTML = `<span class="idx">${i + 1}</span><input value="${esc(t.texto)}" placeholder="Qué pasa en esta toma">
      <select>${[1,2,3,4,5,6,8].map(s => `<option value="${s}" ${s == t.seg ? "selected" : ""}>${s} s</option>`).join("")}</select><button title="Sacar">×</button>`;
    d.querySelector("button").onclick = () => { const l = leerTomasKling(); l.splice(i, 1); pintarTomasKling(l); };
    c.appendChild(d);
  });
  tandasAyuda();
}
function leerTomasKling() {
  return Array.from(document.querySelectorAll("#tomas-kling .toma")).map(d => ({texto: d.querySelector("input").value.trim(), seg: parseInt(d.querySelector("select").value, 10)})).filter(t => t.texto);
}
function tandasAyuda() {
  const l = leerTomasKling(); const seg = l.reduce((a, t) => a + t.seg, 0); const total = parseInt($("#duracion").value || "15", 10);
  $("#est").textContent = l.length ? `${l.length} tomas · ${seg} s de ${total}` + (seg > total ? " — te pasás: se cortan las últimas" : "") : "";
}
$("#add-toma").onclick = () => { const l = leerTomasKling(); l.push({texto: "", seg: 3}); pintarTomasKling(l); };
function pintarTomasFotos() {
  const c = $("#tomas-fotos"); const prev = leerTomasFotos(); c.innerHTML = "";
  FOTOS.forEach((f, i) => {
    const t = prev[i] || {motor: "camara", seg: 3, texto: ""};
    const d = document.createElement("div"); d.className = "toma foto-toma";
    d.innerHTML = `<span class="idx">${i + 1}</span><input value="${esc(t.texto)}" placeholder="${esc(CFG.movimientos_foto[i % CFG.movimientos_foto.length])} (opcional)">
      <select class="m"><option value="camara" ${t.motor === "camara" ? "selected" : ""}>Cámara (gratis)</option><option value="ia" ${t.motor === "ia" ? "selected" : ""}>IA (cobra vida)</option></select>
      <select class="s"></select>`;
    const s = d.querySelector(".s"); const llenar = () => { const ok = d.querySelector(".m").value === "ia" ? CFG.seg_ia : CFG.seg_camara; s.innerHTML = ok.map(v => `<option value="${v}" ${Math.abs(v - t.seg) < 0.01 ? "selected" : ""}>${v} s</option>`).join(""); if (!ok.some(v => Math.abs(v - t.seg) < 0.01)) s.value = String(ok[Math.min(1, ok.length - 1)]); };
    llenar(); d.querySelector(".m").onchange = llenar;
    c.appendChild(d);
  });
}
function leerTomasFotos() {
  return Array.from(document.querySelectorAll("#tomas-fotos .toma")).map(d => ({texto: d.querySelector("input").value.trim(), motor: d.querySelector(".m").value, seg: parseFloat(d.querySelector(".s").value)}));
}
function pedido() {
  const p = {fotos: FOTOS, modo: MODO, grade: $("#grade").value, transicion: $("#transicion").value,
    placa: $("#placa").checked, placa_texto: $("#placa-texto").value, placa_sub: $("#placa-sub").value,
    musica: $("#musica").checked, grano: $("#grano").checked, vineta: $("#vineta").checked, cine: $("#cine").checked};
  if (MODO === "kling") {
    Object.assign(p, {plantilla: $("#plantilla").value, duracion_total: parseInt($("#duracion").value, 10), motor: $("#motor").value,
      formato: $("#formato").value, lugar: $("#lugar").value, tomas: leerTomasKling()});
  } else {
    Object.assign(p, {motor_ia: $("#motor-ia").value, formato: $("#formato2").value, ralenti: $("#ralenti").checked, tomas: leerTomasFotos()});
  }
  return p;
}
$("#estimar").onclick = async () => {
  $("#err").textContent = "";
  try { const e = await api("/estimar", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(pedido())}); $("#est").textContent = e.detalle; }
  catch (e) { $("#err").textContent = e.message; }
};
$("#generar").onclick = async () => {
  $("#err").textContent = "";
  if (!FOTOS.length) { $("#err").textContent = "Subí al menos una foto."; return; }
  $("#generar").disabled = true;
  try {
    const r = await api("/generar", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(pedido())});
    JOB = r.job_id; $("#est").textContent = r.estimado.detalle; $("#prog").classList.remove("hidden"); $("#frenar").classList.remove("hidden");
    $("#video").classList.add("hidden"); $("#descarga").classList.add("hidden");
    seguir();
  } catch (e) { $("#err").textContent = e.message; $("#generar").disabled = false; }
};
$("#frenar").onclick = async () => { if (JOB) { try { await api("/frenar/" + JOB, {method: "POST"}); $("#paso").textContent = "Frenando al terminar la toma en curso…"; } catch (e) {} } };
function seguir() {
  clearTimeout(TIMER);
  TIMER = setTimeout(async () => {
    try {
      const j = await api("/estado/" + JOB);
      const hechas = j.tandas ? (j.tandas_listas || 0) / j.tandas : (j.tomas ? (j.tomas_listas || 0) / j.tomas.length : 0);
      $("#paso").textContent = (j.paso || j.estado) + (j.costo ? ` · USD ${Number(j.costo).toFixed(2)}` : "");
      $("#bar").style.width = (j.estado === "listo" ? 100 : Math.round(hechas * 85)) + "%";
      if (j.estado === "listo") { mostrar(JOB, j); return; }
      if (j.estado === "error") { $("#err").textContent = j.detalle || "Falló."; $("#generar").disabled = false; $("#frenar").classList.add("hidden"); cargarHist(); return; }
      seguir();
    } catch (e) { $("#err").textContent = e.message; seguir(); }
  }, 4000);
}
function mostrar(jid, j) {
  $("#generar").disabled = false; $("#frenar").classList.add("hidden");
  const v = $("#video"); v.src = API + "/final/" + jid + "?t=" + Date.now(); v.classList.remove("hidden");
  $("#dl").href = API + "/final/" + jid; $("#descarga").classList.remove("hidden");
  $("#paso").textContent = `Listo · ${j.duracion || ""} s · USD ${Number(j.costo || 0).toFixed(2)}`;
  cargarHist();
}
async function cargarHist() {
  try {
    const d = await api("/jobs"); const c = $("#hist"); c.innerHTML = "";
    if (!d.jobs.length) { c.innerHTML = '<p class="hint">Todavía ninguno.</p>'; return; }
    for (const j of d.jobs) {
      const it = document.createElement("div"); it.className = "it";
      const f = j.creado ? new Date(j.creado * 1000).toLocaleString("es-AR", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"}) : "";
      it.innerHTML = `<div>${esc(j.plantilla || j.modo)} · ${esc(j.motor || "")}<br><span>${f} · ${esc(j.estado)}${j.duracion ? " · " + j.duracion + " s" : ""}${j.costo ? " · USD " + Number(j.costo).toFixed(2) : ""}${j.detalle ? " · " + esc(j.detalle) : ""}</span></div>` +
        (j.estado === "listo" ? `<button class="btn sec" data-j="${j.job_id}">Ver</button>` : (j.estado === "trabajando" || j.estado === "encolado" ? `<button class="btn sec" data-s="${j.job_id}">Seguir</button>` : ""));
      const b = it.querySelector("button");
      if (b && b.dataset.j) b.onclick = () => mostrar(b.dataset.j, j);
      if (b && b.dataset.s) b.onclick = () => { JOB = b.dataset.s; $("#prog").classList.remove("hidden"); seguir(); };
      c.appendChild(it);
    }
  } catch (e) {}
}
$("#f-musica").onchange = async e => {
  const f = e.target.files && e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("archivo", f);
  try { await api("/musica", {method: "POST", body: fd}); $("#musica-estado").textContent = "(subida)"; $("#musica").checked = true; }
  catch (err) { $("#err").textContent = err.message; }
};
(async () => {
  CFG = await api("/config");
  const modos = $("#modos");
  for (const [k, v] of Object.entries(CFG.modos)) { const c = document.createElement("div"); c.className = "chip"; c.dataset.m = k; c.textContent = v; c.onclick = () => setModo(k); modos.appendChild(c); }
  opciones($("#plantilla"), CFG.plantillas, "surf");
  const dur = $("#duracion"); CFG.duraciones.forEach(d => { const o = document.createElement("option"); o.value = d; o.textContent = d + " segundos" + (d > 15 ? ` (${Math.ceil(d / 15)} tandas)` : ""); dur.appendChild(o); });
  opciones($("#motor"), CFG.motores_kling, "kling_std");
  opciones($("#motor-ia"), CFG.motores_ia, "seedance");
  opciones($("#grade"), CFG.grades, "pelicula");
  opciones($("#transicion"), CFG.transiciones, "corte");
  $("#plantilla").onchange = tomasPlantilla; $("#duracion").onchange = tomasPlantilla;
  $("#tomas-kling").addEventListener("change", tandasAyuda); $("#tomas-kling").addEventListener("input", tandasAyuda);
  $("#musica-estado").textContent = CFG.musica ? "(hay una subida)" : "(no hay música subida)";
  if (CFG.musica) $("#musica").checked = true;
  if (!CFG.fal_key) $("#err").textContent = "No hay API key de fal cargada: Kling y las tomas con IA no van a andar hasta que la cargues en Fotos → Ajustes.";
  tomasPlantilla(); setModo("kling"); cargarHist();
})();
</script>
</body>
</html>
"""
