"""
FILMADO DE CERO — un REEL COMPLETO de ella filmado por el motor desde cero (no una foto
que cobra vida), con SU voz, armado toma por toma.

Cómo se arma:
  1. Contás qué querés (la prenda, qué mostrar, cuánto dura, datos del producto) y
     CLAUDE TE PREGUNTA lo que le falta para dirigir bien (el gancho, qué destacar, hasta
     dónde mostrar, el cierre…).
  2. Con tus respuestas Claude arma EL PLAN: varias tomas (que hable a cámara, que gire y
     se vea la espalda, la bombacha, el encaje de cerca, el espejo…), cada una con lo que
     dice y lo que hace. Todo se puede editar: lo que escribís vos va tal cual.
  3. Se filma cada toma: primero la VOZ de Reels (y la toma dura lo que dura lo que dice),
     Kling filma sin voz con ella como @Element1 y la prenda como @Element2, y en las
     tomas donde habla a cámara el LIP-SYNC le pone la boca en sincro con su voz. En las
     tomas que muestran (gira, espalda, detalle) su voz va encima, como en los reels.
  4. Se unen las tomas, se pasa el filtro de Reels y Claude revisa cada toma contra la
     prenda (sólo informa). Una toma que no gustó se rehace sola, sin volver a pagar las
     demás.

v2.1 (después de la prueba del reel):
  - Modo "voz de fondo" (por defecto): ella no habla a cámara, su voz va encima. Sin lip-sync:
    más barato y lo que mejor salió. El modo "habla" sigue para quien lo quiera.
  - Con lip-sync, Kling la filma con la BOCA QUIETA: si el motor también la movía, las dos
    bocas se mezclaban. Kling LipSync afuera (anduvo mal); queda Sync.
  - CONTINUIDAD: la primera toma se filma sola y un cuadro suyo va de referencia (@Image1) a
    todas las demás, más una descripción fija del cuarto, la luz y el peinado: antes cada toma
    inventaba otro fondo y otro pelo.
  - El DIRECTOR entiende qué vendemos (qué es, puntos fuertes, para quién), arma un concepto y
    cada toma lleva plano (detalle, primer plano, medio, entero, espejo…), movimiento de cámara
    y ritmo de reel (2–4 s); puede haber tomas de la prenda sola.

v2.2 (mirando otra vez el video de referencia):
  - TOMA LARGA SIN CORTES: una toma puede "seguir sin cortar" a la anterior: Kling arranca en su
    último cuadro (start_image_url) y se encadenan 20–30 s aunque filme 15 s como mucho.
  - JUEGO CON LA CÁMARA Y TRANSICIONES: cada toma dice cómo entra (corte, tapa la cámara con la
    mano, pasa la prenda por la lente, barrido, sigue sin cortar). El final de una y el
    principio de la otra se piden juntos, y en el montaje van con un fundido cortito a negro.
  - VARIOS COLORES de la prenda (hasta 5): cada toma dice cuál tiene puesto; los cambios de color
    van en las transiciones (la tendencia de tapar la cámara y aparecer con otro color).
  - El director conoce todo eso y puede proponer el movimiento de espaldas (se desprende el
    corpiño, lo deja caer, se da vuelta tapándose con el brazo y tapa la cámara): siempre
    sugerido, nunca explícito.

v2.3 (después de la prueba "Tricolor": se perdían prenda y cara, y no hacía lo pedido):
  - El cuadro del cuarto salía de un primer plano del color 1 y Kling copiaba ESA prenda en los
    otros colores. Ahora hay uno por color, de una toma abierta, y el pedido dice que ignore la
    ropa del cuadro.
  - Cuatro tomas encadenadas "sin cortar" terminaban con otra chica y otro top (cada una arranca
    de un cuadro ya copiado). Como mucho dos encadenadas, de 5 a 10 s.
  - Las acciones de 3 s no alcanzaban: el director pide al menos 4 s para una acción o un gesto,
    parte los movimientos de varios pasos en varias tomas, y el recorte no las deja en menos de
    3,5 s. La selfie en el espejo se filma desde el celular (sólo el reflejo), no de espaldas.

v2.3.1: en la prueba, en las tomas del color 2 apareció OTRA chica: la foto del producto la tenía
puesta una modelo y Kling la copiaba. Antes de filmar, a las fotos de la prenda se les saca la
cabeza de quien la lleva (Gemini la ubica) y el pedido dice que de esas fotos sólo copie la ropa.

v3.1: EDICIÓN sobre el video ya filmado (al unir: gratis, se cambia y se vuelve a unir sin filmar):
  subtítulos de la voz, carteles flotantes que entran deslizándose (precio, talles, beneficios),
  zoom por toma (lento o de golpe al detalle), la foto real del producto entrando en una esquina
  y el cartel final (precio + acción). El director la planea con su ficha (general/edicion.md).

v3.0 (otro enfoque: "en Fotos la prenda sale exacta, en video no"):
  - FOTOS CLAVE: cada toma arranca de una foto hecha con el motor de Fotos (Seedream edit con las
    fotos reales de la prenda, Claude la revisa y la rehace una vez si sale floja). La ves antes de
    pagar video; se puede pedir otra con una corrección, arreglar la cara o subir una propia. Kling
    la pone en movimiento (start_image_url) con ella y la prenda como elementos; en los giros
    también termina en la foto final (de espaldas). Todas salen de la misma foto base (mismo lugar
    y pelo), y la primera de cada color ancla las de ese color.
  - SKILLS DEL DIRECTOR (director_skills/): cómo se vende con un reel, qué puede y qué no la IA de
    video (lo aprendido en las pruebas), una ficha por tipo de prenda y una por lugar. Claude
    clasifica la prenda en las preguntas y lee la ficha que corresponde; si la prenda vende mejor
    en otro lugar (malla → pileta o playa), lo pregunta.
  - Lugares nuevos: baño, pileta, playa y living.

v2.4 ("empieza bien y se va": cada toma era una filmación distinta y cada una reinterpretaba
cara y prenda):
  - Con voz de fondo y Kling, las tomas van en BLOQUES de hasta 15 s que Kling filma de UNA vez
    con varias tomas adentro (multi_prompt, hasta 6 de 3 s o más): misma cara, prenda y cuarto
    dentro del bloque. Un bloque nuevo sólo en un cambio de color, donde ella tapa la cámara, al
    pasar 15 s o entre la prenda sola y ella. El bloque se corta en sus tomas (en los cortes
    reales de Kling si los encuentra), así la revisión, rehacer y el montaje siguen igual.
  - FOTO DE ARRANQUE por bloque (opcional): una foto de Fotos con la prenda exacta como primer
    cuadro; Kling arranca de ahí en vez de dibujar la prenda de cero.
  - "Probar este bloque": filma y cobra sólo ese bloque.

La voz ya no se corta: la voz se completa con silencio hasta el largo del video, y si el
motor devolviera un video más corto que la voz, el lip-sync lo alarga ("bounce") en vez de
cortarle la voz (la v1.2 usaba "cut_off", que corta lo que sobra).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import math
import os
import re
import subprocess
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

import claude_director as _claude
import referencias_luma as refs_luma
import motion_luma as motion
from imagenes_ia import (
    ANALYZE_ENDPOINT,
    CURRENT_SUB,
    _current_api_key,
    _img_part,
    _sanear_prompt_fal,
    fal_generate,
    get_settings,
    _al_ingles,
    _compress_ref,
    _pfx,
    _strip_data_url,
    budget_record,
    kv,
    set_current_sub,
)
from personajes import (
    cara_identidad,
    caras_hd,
    COSTO_TTS,
    PJ_DIR,
    VOCES,
    _bind,
    _cobrar,
    _doc,
    _fal_enviar,
    _fal_esperar_y_bajar,
    _fal_key,
    _fal_subir,
    _guardar_en_drive,
    _job_nuevo,
    _job_set,
    _k_job,
    _latir,
    _refs_identidad,
    _revisar_job,
    _slug,
    _texto,
    _tts_mp3,
)
from reels import (
    _ASS_CABECERA,
    _ass_texto,
    _ass_tiempo,
    _precio_sticker,
    _trozos_sub,
    ALTO,
    ANCHO,
    LOOKS,
    _AF_VOZ,
    _FILTRO_LOOK,
    _MANO_CROP,
    _MANO_ESCALA,
    ENERGIAS_VOZ,
    TONOS,
    _cuerpo_en,
    _instruccion_voz,
    _ref_cuerpo,
    _tratar_voz,
    _voz_reel,
)
from videos_luma import _duracion_video, _ffmpeg_bin, _spawn

ROUTE_PREFIX = os.environ.get("FILMADO_PREFIX", "/filmado").rstrip("/")
API = ROUTE_PREFIX + "/api"
VERSION = "3.8.0"   # subí este número cada vez que cambiamos el archivo

SEG_MIN, SEG_MAX = 3, 15            # lo que acepta Kling por clip
SEG_MUESTRA = 4                     # una toma que sólo muestra, sin voz
SEG_CORTE_MIN = 2                   # una toma sin voz se puede cortar a 2 s (Kling filma 3 como mínimo)
DURACIONES = (15, 20, 30)           # el reel entero
MAX_TOMAS = 10
MAX_VARIANTES = 5                   # colores de la prenda en un mismo reel
FUNDIDO = 0.12                      # el negro entre "tapa la cámara" y la toma que sigue
MAX_PROMPT = int(os.getenv("FILMADO_MAX_PROMPT", "2450"))   # Kling acepta 2500 caracteres
# Cada toma que arranca en el último cuadro de otra pierde un poco de cara y de prenda: en la
# prueba, 4 encadenadas terminaron con otra chica y otro top. Como mucho una "sigue" seguida.
MAX_SIGUE = int(os.getenv("FILMADO_MAX_SIGUE", "1"))
SEG_GESTO = 3.5                     # una toma con gesto de transición no se corta a menos de esto
PLANOS_ABIERTOS = ("entero", "americano", "espejo", "medio")   # de dónde sale el cuadro del cuarto
# BLOQUES (modo voz de fondo con Kling): las tomas seguidas del mismo color, sin la mano o la
# prenda tapando la cámara entre ellas, se filman en UNA sola generación de hasta 15 s con
# varias tomas adentro (multi_prompt de Kling: hasta 6 tomas de 3 s o más). Una generación =
# la misma cara, la misma prenda y el mismo cuarto en todas: con una por toma "empezaba bien y
# se iba".
SEG_TOMA_BLOQUE = 3
MAX_PROMPT_TOMA = int(os.getenv("FILMADO_MAX_PROMPT_TOMA", "500"))  # cada toma del multi-toma: 512 como mucho
MAX_TOMAS_BLOQUE = 6
# FOTOS CLAVE: cada toma arranca de una foto hecha con el motor de Fotos (Seedream edit con las
# fotos reales de la prenda: es donde la prenda sale exacta). Kling pone esa foto en movimiento
# (start_image_url) con ella y la prenda como elementos; en los giros, también la foto final.
COSTO_CLAUDE_FOTO = 0.04
# EDICIÓN (sobre el video ya filmado, al unir: gratis y se puede rehacer sin volver a filmar).
EDICION_DEFAULT = {"subtitulos": True, "carteles": True, "zoom": True, "foto_producto": True, "cierre": True}
EDICION_NOMBRES = {"subtitulos": "Subtítulos de la voz", "carteles": "Carteles flotantes",
                   "zoom": "Zooms", "foto_producto": "Foto del producto en una esquina", "cierre": "Cartel final"}
ZOOMS = {"no": "Sin zoom", "lento": "Se acerca despacio", "golpe": "Zoom de golpe al detalle"}
ZOOM_LENTO, ZOOM_GOLPE = 0.10, 0.22
SEG_CIERRE = 2.6
# Estilos extra (en unidades de 1080x1920): Cartel = caja dorada de la marca; Cierre = caja oscura
# grande en el centro. Sub (los subtítulos) viene de Reels.
_ASS_ESTILOS_FILMADO = (
    "Style: Cartel,DejaVu Sans,58,&H00141414,&H00FFFFFF,&H006BA8C9,&H00000000,-1,0,0,0,100,100,1,0,3,18,0,7,0,0,0,1\n"
    "Style: Cierre,DejaVu Sans,92,&H00FFFFFF,&H00FFFFFF,&H50101010,&H00000000,-1,0,0,0,100,100,1,0,3,34,0,5,80,80,0,1\n"
)            # Claude revisando una foto clave (aprox.)
PUNTAJE_REHACER = 7                 # una foto clave con menos que esto se rehace sola UNA vez
PARALELO = 3                        # tomas filmándose a la vez en fal
LUGARES = {
    "dormitorio": ("Su dormitorio",
                   "her own bedroom at home: an unmade bed with rumpled sheets, a bedside lamp switched "
                   "on with warm light, a window with soft daylight and curtains, a dresser with makeup, "
                   "perfume and a full-length standing mirror; an ordinary lived-in room, not a set"),
    "probador": ("El probador de un local",
                 "the fitting room of a small lingerie shop: a curtain, a full-length mirror, a hook with "
                 "hangers, warm shop light"),
    "bano": ("El baño (selfies en el espejo)",
             "a bright bathroom at home: a large mirror with good light, light tiles, towels, a few "
             "toiletries; an ordinary lived-in bathroom"),
    "pileta": ("Una pileta, de día",
               "the edge of a swimming pool at a house on a sunny day: turquoise water, light stone deck, "
               "a sun lounger, plants; hard sunlight with real shadows"),
    "playa": ("La playa",
              "a sandy beach in the late afternoon: the sea behind, golden sunlight, a light breeze"),
    "living": ("El living",
               "a living room at home: a sofa, plants, a rug, a big window with soft daylight"),
}
# ── Las "skills" del director: archivos en director_skills/ (se pueden editar sin tocar código).
SKILLS_DIR = Path(__file__).resolve().parent / "director_skills"


def _skill(nombre: str) -> str:
    try:
        return (SKILLS_DIR / nombre).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def categorias_prenda() -> List[str]:
    try:
        return sorted(p.stem for p in (SKILLS_DIR / "prendas").glob("*.md"))
    except OSError:
        return ["otra"]


def skills_director(categoria: str = "", lugar: str = "") -> str:
    """Lo que el director lee antes de planear: todo general/, la ficha de la prenda y la del lugar."""
    partes = [_skill(f"general/{p.name}") for p in sorted((SKILLS_DIR / "general").glob("*.md"))] \
        if (SKILLS_DIR / "general").exists() else []
    cat = categoria if categoria in categorias_prenda() else "otra"
    partes.append(_skill(f"prendas/{cat}.md"))
    if lugar:
        partes.append(_skill(f"lugares/{lugar}.md"))
    return "\n\n".join(x for x in partes if x)
MOTORES = {
    "kling_pro": {"label": "Kling 3.0 Omni · Pro", "tipo": "kling",
                  "modelo": os.getenv("FAL_KLING_PRO_MODEL", "fal-ai/kling-video/o3/pro/reference-to-video"),
                  "precio_seg": float(os.getenv("COMERCIALES_PRECIO_PRO", "0.112"))},
    "seedance2": {"label": "Seedance 2.0", "tipo": "seedance",
                  "modelo": os.getenv("FAL_SEEDANCE_REF_MODEL", "bytedance/seedance-2.0/reference-to-video"),
                  "precio_seg": float(os.getenv("PERSONAJES_PRECIO_SEEDANCE_REF", "0.30"))},
    # Seedance 2.5: hasta 30 s en UNA toma, hasta 30 imágenes de referencia y respeta los
    # segundos de una línea de tiempo en el prompt (el modo "una toma, segundo a segundo").
    "seedance25": {"label": "Seedance 2.5 (hasta 30 s en una toma)", "tipo": "seedance",
                   "modelo": os.getenv("FAL_SEEDANCE25_REF_MODEL", "bytedance/seedance-2.5/reference-to-video"),
                   "precio_seg": float(os.getenv("FILMADO_PRECIO_SEEDANCE25", "0.473"))},
}
MOTOR_DEFAULT = "kling_pro"
LIPSYNCS = {
    "sync": {"label": "Sync 2 Pro", "modelo": os.getenv("FILMADO_LIPSYNC_MODEL", "fal-ai/sync-lipsync/v2/pro"),
             "precio_seg": float(os.getenv("FILMADO_PRECIO_LIPSYNC", "0.083"))},
}
LIPSYNC_DEFAULT = "sync"
COSTO_REVISION = 0.05               # Claude mirando una toma
COSTO_PLAN = 0.10                   # Claude preguntando y armando el plan
LOOK_DEFAULT = "nitido"             # grano y color de celular sin ablandar: el encaje se sigue viendo
CAMARAS = {"motor": "La que filma el motor", "mano": "Más en mano (se mueve sola, como en Reels)"}
ENERGIA_FILMADO = "natural"         # con sus pausas: la primera prueba "iba tan rápido que parecía IA"
# Cómo cuenta el reel. "Voz de fondo": ella nunca habla a cámara, su voz va encima (sin lip-sync,
# más barato y lo que mejor salió). "Habla": tomas a cámara con lip-sync.
MODOS = {"fondo": "Sin que hable: su voz de fondo (más barato, recomendado)",
         "habla": "Que hable a cámara en algunas tomas (con lip-sync)"}
MODO_DEFAULT = "fondo"
TIPOS = {"habla": "Habla a cámara (lip-sync)", "muestra": "Ella muestra (voz de fondo)",
         "producto": "Sólo la prenda (sin ella)"}
PLANOS = {
    "detalle": ("Detalle (bien de cerca)", "extreme close-up detail shot"),
    "primer": ("Primer plano", "close-up shot"),
    "medio": ("Plano medio", "medium shot, from the waist up"),
    "americano": ("Plano americano", "medium-long shot, from the knees up"),
    "entero": ("Plano entero", "full-body shot, head to feet in frame"),
    "espejo": ("En el espejo (selfie)", "mirror selfie: the camera IS her phone, we see ONLY her reflection in "
                                       "the full-length mirror, she faces the mirror holding the phone at "
                                       "chest height, her front and the whole set visible in the reflection"),
}
# El ÁNGULO de la cámara (aparte del plano): lo que hace que un reel parezca filmado de verdad
# es que cada toma lo cambia.
ANGULOS = {
    "ojos": ("A la altura de los ojos", "camera at her eye level"),
    "bajo": ("Desde abajo (contrapicado)", "low angle, the camera below looking up at her"),
    "alto": ("Desde arriba (picado)", "high angle, the camera above looking down at her"),
    "cenital": ("Cenital (justo desde arriba)", "top-down overhead angle, the camera straight above"),
    "perfil": ("De perfil", "side profile angle, the camera at 90 degrees to her"),
    "tres_cuartos": ("3/4", "three-quarter angle, the camera 45 degrees to her side"),
    "hombro": ("Por sobre el hombro", "over-the-shoulder angle, from just behind her shoulder"),
    "espalda": ("Desde atrás", "from behind her back"),
    "holandes": ("Inclinado (holandés)", "slight dutch angle, the camera tilted a few degrees"),
    "suelo": ("Desde el piso", "ground-level angle, the phone almost on the floor looking up"),
}
# EL RITMO: "segundo" = muchas tomas cortas (1,5–2,5 s), cada una con otro ángulo y plano, y
# una sola voz corrida encima (como los reels con IA que parecen reales); "normal" = tomas de 3–5 s.
RITMOS = {"toma": "Una toma, segundo a segundo: UN video de hasta 30 s con un guion por segundo (Seedance 2.5)",
          "segundo": "Por segundo: muchas tomas cortas (1,5–2,5 s) filmadas por separado, cada una con su foto clave",
          "normal": "Tomas largas (3–5 s): menos cortes, más barato"}
MOTOR_TOMA = "seedance25"           # el único que filma 30 s en una toma siguiendo los segundos
SEG_MAX_TOMA = 30                   # una generación de Seedance 2.5
MAX_TRAMOS_TOMA = 20
RITMO_NUEVO = "toma"               # el de los reels nuevos (los viejos siguen en "normal")
MAX_TOMAS_SEGUNDO = 16
SEG_RITMO_MIN, SEG_RITMO_MAX, SEG_RITMO = 1.2, 3.0, 2.0
MOVIMIENTOS = {
    "mano": ("En mano", "handheld phone camera with small natural movements"),
    "acerca": ("Se acerca despacio", "slow push-in towards the subject"),
    "aleja": ("Se aleja despacio", "slow pull-back revealing more"),
    "sigue": ("La acompaña", "the camera follows her movement smoothly"),
    "orbita": ("Gira alrededor", "the camera slowly arcs around her"),
    "paneo": ("Paneo lento", "slow pan across the subject"),
    "fija": ("Fija", "locked-off camera on a small tripod"),
    "cenital": ("Desde arriba", "top-down shot"),
}
MOSTRAR = {
    "cama": ("La prenda sola sobre la cama", "the set alone laid out on the bed, product shot"),
    "gira": ("Que gire despacio y se vea la espalda", "she turns slowly so the back of the set is seen"),
    "abajo": ("Que se vea bien la bombacha (la parte de abajo)", "the bottom piece (the panty) is clearly seen, front and back"),
    "detalle": ("El encaje / la tela de cerca", "a close-up of the lace and the fabric"),
    "espejo": ("Cuerpo entero en el espejo", "a full-body shot in the mirror"),
    "mano": ("Que primero la muestre en la mano", "first she shows the set in her hand before wearing it"),
    "cambio": ("Cambio de color tapando la cámara (si subís más de un color)",
               "colour changes as transitions: she covers the lens with her hand (or passes the garment over the "
               "lens) and the next shot she wears the next colour"),
    "espalda_top": ("De espaldas se desprende el corpiño, lo deja caer y se da vuelta tapándose con el brazo; "
                    "con la otra mano tapa la cámara y aparece con otro color",
                    "the trend move: seen from the back she unclasps the top and lets it fall, turns around covering "
                    "her bust with one arm and covers the lens with the other hand; the next shot she wears another "
                    "colour. Implied only, never explicit"),
    "secuencia": ("Una toma larga sin cortes (como el video de referencia)",
                  "one long continuous take without visible cuts, chaining the shots with enlace \"sigue\""),
}
# Cómo ENTRA cada toma desde la anterior. "sigue" arranca en el último cuadro de la anterior
# (start_image_url): se encadenan 20–30 s sin corte aunque Kling filme 15 s como mucho.
ENLACES = {
    "corte": ("Corte directo", "", ""),
    "mano": ("Tapa la cámara con la mano",
             " At the very end of the shot she reaches her open hand towards the lens and covers it completely, "
             "the frame goes dark.",
             " The shot starts with her hand covering the lens (dark frame) and pulling away to reveal the scene."),
    "prenda": ("Pasa la prenda por delante de la cámara",
               " At the very end of the shot she brings the garment or her hand close to the lens, covering it.",
               " The shot starts with fabric covering the lens and pulling away to reveal her."),
    "giro": ("Barrido rápido de cámara",
             " At the very end the camera whips quickly to the side (fast whip pan with motion blur).",
             " The shot starts in the middle of a fast whip pan with motion blur that lands on the subject."),
    "sigue": ("Sigue sin cortar (misma toma larga)", "",
              " The shot continues seamlessly from its first frame, the same continuous take without any cut: "
              "same position, same light, same movement flow."),
}
ENLACES_TAPAN = ("mano", "prenda")
# Lo mismo, cortito, para las tomas de un bloque (512 letras cada una).
ENLACES_CORTO = {
    "corte": ("", ""),
    "mano": (" At the end she covers the lens with her open hand.", " Starts with her hand pulling away from the lens."),
    "prenda": (" At the end the garment covers the lens.", " Starts with fabric pulling away from the lens."),
    "giro": (" Ends with a fast whip pan.", " Starts at the end of a fast whip pan."),
    "sigue": ("", " Continues the same action without a cut."),
}      # en el montaje van con un fundido corto a negro
MOSTRAR_DEFAULT = ("gira", "abajo", "detalle", "espejo")
DICE_DEFAULT = ("Chicas, me llegó el conjunto que les dije. Miren este encaje, es divino y re "
                "cómodo. Escríbanme por DM que les paso los talles.")
DIR = PJ_DIR / "filmado"
DIR.mkdir(parents=True, exist_ok=True)

# Lo que hace que se vea FILMADO y no una foto animada.
_FILMADO = (
    "This is REAL phone footage, not an animated photo: the video starts in the MIDDLE of her "
    "movement (never from a still, posed frame), the phone camera is handheld with small natural "
    "shakes and tiny reframing, focus and exposure adjust slightly as she moves, real indoor "
    "light with soft shadows, subtle grain. She moves like a real person: weight shifts, "
    "breathing, blinking, hair moving, small hand gestures, CALM and unhurried, at real-time "
    "speed (never slow motion, never sped up). Real skin with pores and natural shine, no "
    "smoothing, no plastic or waxy look, no doll face. Her body keeps its real proportions and "
    "weight in every frame (do not enlarge or reshape her). No text, no logos, no watermark, no "
    "other people."
)
_NEGATIVO = ("animated photo, static camera, frozen pose, mannequin, plastic skin, waxy, doll, "
             "CGI, 3D render, slow motion, fast motion, walking into frame, morphing face, "
             "different face, exaggerated body, extra fingers, deformed hands, explicit nudity, visible nipples, "
             "text, watermark")
# Con lip-sync la boca la anima DESPUÉS el lip-sync: si el motor también la mueve, las dos se
# mezclan (se vio en la prueba). Que la filme con la boca quieta y la cara bien a cámara.
_HABLA_MUDA = (" She looks straight into the lens with a warm, friendly expression, her face well lit "
               "and frontal the whole time. Her lips stay softly CLOSED and still — she does NOT speak "
               "and does not move her mouth (the mouth is animated afterwards) — with small natural "
               "head movements, blinking and a slight smile in the eyes.")
_MUESTRA = (" She does not talk in this shot (a voice-over goes on top): relaxed natural "
            "expression, lips closed. When her face is seen it is exactly her face.")
_PRODUCTO = (" No person in this shot: only the lingerie set, real fabric that moves slightly with the "
             "air and the light, as filmed with a phone.")
_REF_ESCENA = ("@Image1 is a frame from an earlier shot of this same video, ONLY as a reference for the "
               "room: keep EXACTLY the same room, furniture, wall colour, light and time of day{ella}. "
               "IGNORE the clothes in @Image1: what she wears is ONLY {prenda}. Only the camera angle, the "
               "framing and the action change.")


_REF_LUGAR = ("@Image1 is a photo of the REAL place where this reel is filmed (empty): she is INSIDE "
              "exactly this place — same walls, floor, furniture, objects, colours and light. Only the camera "
              "angle, the framing and the action change. Nobody else is in the place.")
# EL PROBADOR: el cuerpo de ELLA (sin cabeza) con la prenda puesta, sobre gris. Le muestra a
# Seedream y a Kling la prenda YA PUESTA en su cuerpo (el calce real), no en un maniquí.
_PROBADOR = (
    "Studio reference photo for a clothing catalogue, on a plain flat mid-grey seamless background. "
    "Image 1 is the model's body: keep EXACTLY her body, proportions, weight and skin tone{cuerpo}. "
    "Image{s} 2{hasta} = the REAL PRODUCT PHOTOS: she wears EXACTLY that garment — same design, cut, "
    "colour, fabric, lace, straps, trims, pockets and details — and nothing else on top. Ignore any "
    "person, mannequin, hanger or background in the product photos. {vista} view, standing straight, "
    "arms relaxed slightly away from the body so the garment is fully visible, full body from the neck "
    "down to the feet: the head is OUT of the frame, cropped at the neck. Even soft studio light, sharp "
    "fabric detail, true colours. No text, no props, no other people."
)


_TRES_CUARTOS = ("THREE-QUARTER side (her body turned about 45 degrees to her left, so the side of the bust, "
                 "the waist, the hip and the glutes show the garment's side and fit)")


def _k_probador(pid: str, h: str) -> str:
    return _pfx() + f"filmado:probador:{pid}:{h}"


def _k_probador_reel(rid: str, v: int) -> str:
    """El probador que usó este reel en ese color (para mostrarlo en la pantalla)."""
    return _pfx() + f"filmado:reel:{rid}:probador:{v}"


def _k_reel(rid: str) -> str:
    return _pfx() + f"filmado:reel:{rid}"


def _k_reels() -> str:
    return _pfx() + "filmado:reels"


def _k_prenda(rid: str, i: int, v: int = 0) -> str:
    """Las fotos de la prenda: el color 0 con la clave de siempre (los reels viejos siguen andando)."""
    return _pfx() + (f"filmado:reel:{rid}:prenda:{i}" if not v else f"filmado:reel:{rid}:v{v}:prenda:{i}")


def _k_ref(rid: str, v: int = 0) -> str:
    """El cuadro de una toma abierta de ESE color: el lugar, la luz y el peinado que siguen en las
    demás. Uno por color: con uno solo, el color 2 salía con el naranja del color 1."""
    return _pfx() + (f"filmado:reel:{rid}:ref" if not v else f"filmado:reel:{rid}:ref:v{v}")


async def _borrar_refs(rid: str) -> None:
    for v in range(MAX_VARIANTES):
        await kv.delete(_k_ref(rid, v))


def _dir(rid: str) -> Path:
    d = DIR / re.sub(r"[^a-z0-9]", "", rid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clip(rid: str, tid: str) -> Path:
    return _dir(rid) / f"{re.sub(r'[^a-z0-9]', '', tid)}.mp4"


def _final(rid: str) -> Path:
    return _dir(rid) / "reel.mp4"


_LOCKS: Dict[str, asyncio.Lock] = {}


def _lock(rid: str) -> asyncio.Lock:
    return _LOCKS.setdefault(rid, asyncio.Lock())


async def _reel(rid: str) -> Dict[str, Any]:
    r = await kv.get(_k_reel(rid))
    if not isinstance(r, dict):
        raise HTTPException(404, "Ese reel no existe.")
    return r


async def _guardar(reel: Dict[str, Any]) -> None:
    await kv.set(_k_reel(reel["id"]), reel)


def _variantes(reel: Dict[str, Any]) -> List[Dict[str, Any]]:
    return reel.get("variantes") or [{"nombre": "", "n": int(reel.get("n_prendas") or 0)}]


async def _prendas(rid: str, reel: Dict[str, Any]) -> List[List[str]]:
    """Las fotos de cada color de la prenda."""
    out = []
    for v, var in enumerate(_variantes(reel)):
        out.append([b for b in [await kv.get(_k_prenda(rid, i, v)) for i in range(int(var.get("n") or 0))] if b])
    return out


def _ps(reel: Dict[str, Any]) -> float:
    return ENERGIAS_VOZ.get(reel.get("energia"), ENERGIAS_VOZ[ENERGIA_FILMADO])["palabras_seg"]


def _dur_voz(reel: Dict[str, Any], t: Dict[str, Any]) -> float:
    return len((t.get("dice") or "").split()) / _ps(reel)


def _por_segundo(reel: Dict[str, Any]) -> bool:
    """Ritmo por segundo (en tomas separadas o en una sola toma): sólo con voz de fondo (con
    lip-sync una toma que habla dura lo que dice)."""
    return reel.get("ritmo") in ("segundo", "toma") and reel.get("modo", MODO_DEFAULT) == "fondo"


def _por_toma(reel: Dict[str, Any]) -> bool:
    """UNA toma, segundo a segundo: los tramos del plan se filman juntos en un video de Seedance
    2.5 (hasta 30 s) cuyo prompt es la línea de tiempo; después se corta en sus tramos."""
    return reel.get("ritmo") == "toma" and reel.get("modo", MODO_DEFAULT) == "fondo"


def _corta(reel: Dict[str, Any], t: Dict[str, Any]) -> bool:
    """Una toma corta del ritmo por segundo: muda (la voz va corrida encima), se filma lo mínimo
    del motor y se corta a sus segundos."""
    return _por_segundo(reel) and not t.get("dice")


def _seg_tramo(t: Dict[str, Any]) -> float:
    """Los segundos de un tramo de la toma única: enteros (Seedance 2.5 sigue segundos enteros)."""
    try:
        s_ = float(t.get("seg") or SEG_RITMO)
    except (TypeError, ValueError):
        s_ = SEG_RITMO
    return float(max(1, min(3, int(round(s_)))))


def _max_tomas(reel: Dict[str, Any]) -> int:
    return MAX_TOMAS_SEGUNDO if _por_segundo(reel) else MAX_TOMAS


def _angulo(t: Dict[str, Any]) -> str:
    a = ANGULOS.get(t.get("angulo") or "")
    return a[1] if a else ""


def _por_bloques(reel: Dict[str, Any]) -> bool:
    """Bloques multi-toma: APAGADO por defecto. Kling acepta 512 letras por toma en el multi-toma y
    sin la descripción completa de la prenda la revisión bajó de 6/10 a 2/10. Queda como prueba."""
    if _por_toma(reel):
        return True
    return (bool(reel.get("bloques")) and reel.get("modo", MODO_DEFAULT) == "fondo" and not _por_segundo(reel)
            and MOTORES[reel.get("motor", MOTOR_DEFAULT)]["tipo"] == "kling")


def _seg_en_bloque(reel: Dict[str, Any], t: Dict[str, Any], voz: Optional[float] = None) -> int:
    """Segundos de la toma dentro de un bloque (enteros, 3 como mínimo: lo pide Kling). En una
    toma segundo a segundo, los segundos del tramo."""
    if _por_toma(reel) and not t.get("dice"):
        return _seg_tramo(t)  # type: ignore[return-value]
    if t.get("dice"):
        d = voz if voz is not None else _dur_voz(reel, t)
        return max(SEG_TOMA_BLOQUE, min(SEG_MAX, int(math.ceil(d + 0.5))))
    return max(SEG_TOMA_BLOQUE, min(SEG_MAX, int(round(float(t.get("seg") or SEG_MUESTRA)))))


def _bloques(reel: Dict[str, Any], indices: Optional[List[int]] = None,
             voces: Optional[Dict[str, float]] = None) -> List[List[int]]:
    """Agrupa tomas (por índice) en bloques de una sola generación: corta en un cambio de color,
    donde ella tapa la cámara (mano o prenda), donde hay foto de arranque, al pasar 15 s o 6
    tomas, y entre tomas que no van seguidas en `indices`."""
    tomas = reel.get("tomas") or []
    indices = list(range(len(tomas))) if indices is None else indices
    voces = voces or {}
    out: List[List[int]] = []
    total = 0
    for i in indices:
        t = tomas[i]
        s = _seg_en_bloque(reel, t, voces.get(t["id"]))
        nuevo = (not out or out[-1][-1] != i - 1 or t.get("enlace") in ENLACES_TAPAN or t.get("inicio")
                 or (not _por_toma(reel) and (t.get("tipo") == "producto") != (tomas[i - 1].get("tipo") == "producto"))
                 or int(t.get("variante") or 0) != int(tomas[i - 1].get("variante") or 0)
                 or total + s > (SEG_MAX_TOMA if _por_toma(reel) else SEG_MAX)
                 or len(out[-1]) >= (MAX_TRAMOS_TOMA if _por_toma(reel) else MAX_TOMAS_BLOQUE))
        if nuevo:
            out.append([i])
            total = s
        else:
            out[-1].append(i)
            total += s
    return out


def _seg_kling(reel: Dict[str, Any], t: Dict[str, Any]) -> int:
    """Los segundos que filma (y cobra) el motor para esa toma."""
    if _por_bloques(reel):
        return _seg_en_bloque(reel, t)
    if _corta(reel, t):
        return SEG_MIN
    if t.get("dice"):
        return max(SEG_MIN, min(SEG_MAX, int(math.ceil(_dur_voz(reel, t) + 0.8))))
    return max(SEG_MIN, int(math.ceil(float(t.get("seg") or SEG_MUESTRA))))


def _seg_toma(reel: Dict[str, Any], t: Dict[str, Any]) -> float:
    """Lo que dura la toma en el reel: la que habla, lo que filmó; las demás se cortan a lo que
    dura su voz (así la voz de fondo corre sin baches) o a los segundos elegidos."""
    if _por_bloques(reel):
        return float(_seg_en_bloque(reel, t))
    if _corta(reel, t):
        return round(max(SEG_RITMO_MIN, min(SEG_RITMO_MAX, float(t.get("seg") or SEG_RITMO))), 1)
    if t.get("dice"):
        if t.get("tipo") == "habla":
            return float(_seg_kling(reel, t))
        return round(min(_seg_kling(reel, t), max(SEG_CORTE_MIN, _dur_voz(reel, t) + 0.4)), 1)
    return float(max(SEG_CORTE_MIN, min(10, float(t.get("seg") or SEG_MUESTRA))))


def _costo_toma(reel: Dict[str, Any], t: Dict[str, Any]) -> float:
    s = _seg_kling(reel, t)
    c = s * MOTORES[reel.get("motor", MOTOR_DEFAULT)]["precio_seg"] + COSTO_REVISION
    if t.get("dice"):
        c += COSTO_TTS
        if t.get("tipo") == "habla":
            c += LIPSYNCS["sync"]["precio_seg"] * s
    return round(c, 2)


def _aviso_toma(reel: Dict[str, Any], t: Dict[str, Any]) -> str:
    n = len((t.get("dice") or "").split())
    if n and n / _ps(reel) > SEG_MAX - 1:
        return (f"Es largo para una toma: {n} palabras ≈ {n / _ps(reel):.0f} s. Con esta energía entran "
                f"unas {int((SEG_MAX - 1) * _ps(reel))}: partila en dos tomas.")
    return ""


def _vista(reel: Dict[str, Any]) -> Dict[str, Any]:
    """El reel como lo ve la pantalla: con segundos, costos y avisos calculados."""
    out = {k: v for k, v in reel.items()}
    tomas = []
    for t in reel.get("tomas") or []:
        x = dict(t)
        x["seg_est"] = _seg_toma(reel, t)
        x["costo_est"] = _costo_toma(reel, t)
        x["aviso"] = _aviso_toma(reel, t)
        x["filmada"] = _clip(reel["id"], t["id"]).exists()
        tomas.append(x)
    out["por_bloques"] = _por_bloques(reel)
    out["por_segundo"] = _por_segundo(reel)
    out["por_toma"] = _por_toma(reel)
    out["edicion"] = _edicion(reel)
    out["cierre"] = reel.get("cierre") or _limpiar_cierre(None, reel)
    out["fotos_clave_activo"] = _fotos_clave(reel)
    faltan_fotos = sum((0 if t.get("foto_ok") else 1) + (1 if _quiere_final(t) and not t.get("final_ok") else 0)
                       for t in reel.get("tomas") or [])
    out["fotos_faltan"] = faltan_fotos if out["fotos_clave_activo"] else 0
    out["costo_fotos"] = round(out["fotos_faltan"] * _costo_foto(), 2)
    for x in tomas:
        x["quiere_final"] = _quiere_final(x)
    if out["por_bloques"]:
        for k, b in enumerate(_bloques(reel)):
            for n, i in enumerate(b):
                tomas[i]["bloque"], tomas[i]["inicia_bloque"] = k + 1, n == 0
            tomas[b[0]]["bloque_seg"] = sum(tomas[i]["seg_est"] for i in b)
            tomas[b[0]]["bloque_costo"] = round(sum(tomas[i]["costo_est"] for i in b if not tomas[i]["filmada"]), 2)
            tomas[b[0]]["bloque_tomas"] = len(b)
    out["tomas"] = tomas
    out["seg_total"] = round(sum(t["seg_est"] for t in tomas))
    out["costo_total"] = round(sum(t["costo_est"] for t in tomas), 2)
    out["costo_falta"] = round(sum(t["costo_est"] for t in tomas if not t["filmada"]), 2)
    out["listo"] = _final(reel["id"]).exists()
    return out


# ── Claude pregunta y arma el plan ───────────────────────────────────────────

def _contexto(reel: Dict[str, Any]) -> str:
    info = reel.get("info") or {}
    datos = "; ".join(f"{k}: {info[k]}" for k in ("producto", "precio", "talles", "colores", "promo") if info.get(k))
    mostrar = ", ".join(MOSTRAR[k][1] for k in reel.get("mostrar") or [] if k in MOSTRAR)
    modo = reel.get("modo", MODO_DEFAULT)
    return (
        f"Brand: LUMA Íntima (Argentine lingerie). Target length of the whole reel: about {reel.get('duracion', 20)} s. "
        f"Place: {LUGARES.get(reel.get('lugar'), LUGARES['dormitorio'])[1]}. "
        + ("She WEARS the set. " if reel.get("puesta") else "She holds the set in her hands (she wears a casual t-shirt). ")
        + ("She NEVER talks to the camera: her voice goes on top as a voice-over the whole reel. " if modo == "fondo"
           else "She may talk to the camera in 1 or 2 shots (lip-synced); the rest is voice-over. ")
        + (f"What the owner wants to show: {mostrar}. " if mostrar else "")
        + (f"Product data: {datos}. " if datos else "")
        + (f"Owner's notes: {info['notas']}. " if info.get("notas") else "")
        + (f"What you understood of the product: {json.dumps(reel['analisis'], ensure_ascii=False)}. " if reel.get("analisis") else "")
        + (f"The set comes in {len(_variantes(reel))} colours: "
           + ", ".join(f"variante {i} = {v.get('nombre') or 'color ' + str(i + 1)}" for i, v in enumerate(_variantes(reel))) + ". "
           if len(_variantes(reel)) > 1 else "")
        + f"Her voice speaks about {_ps(reel)} words per second."
    )


def _partes_prenda(prendas: List[List[str]], texto: str, reel: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """El texto y las fotos reales de la prenda, color por color (numerados desde 0)."""
    vars_ = _variantes(reel or {}) if reel else [{"nombre": ""}] * len(prendas)
    varios = len([p for p in prendas if p]) > 1
    parts: List[Dict[str, Any]] = [{"type": "text", "text": texto + (
        "\nThe set comes in several colours (variante 0, 1, …). These are the real product photos of each:"
        if varios else "\nThese are the real product photos of the set:")}]
    for v, fotos in enumerate(prendas):
        if not fotos:
            continue
        if varios:
            nombre = (vars_[v].get("nombre") if v < len(vars_) else "") or f"color {v + 1}"
            parts.append({"type": "text", "text": f"variante {v}: {nombre}"})
        for b in fotos[:3 if not varios else 2]:
            parts.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}})
    return parts


_SYSTEM_PREGUNTAS = (
    "You are the creative director of Instagram reels for LUMA Íntima, an Argentine lingerie brand "
    "that sells to real Argentine women (20 to 45). The reel will be filmed by an AI video model, "
    "shot by shot, with the brand's AI model (a woman) and her own voice.\n"
    "FIRST understand what we sell. Study the product photos like a buyer and a stylist: what "
    "exactly the garment is (bra, bralette, body, set with panty — which cut: brazilian, culotte, "
    "high-waist, thong…), the fabric and the lace, the colour, the details (straps, closures, "
    "trims, transparencies, underwire or not), what makes it sell (comfort, support, sexy but "
    "elegant, everyday, special occasion, gift, flattering for curves…) and who buys it.\n"
    "THEN ask the owner what you need to direct a reel that SELLS it. Do NOT ask what you already "
    "know from the photos or from what the owner told you. Ask 3 to 6 short, concrete questions "
    "in Spanish from Argentina (voseo, casual), each with 2 to 4 suggested answers the owner can "
    "tap: the angle of the reel (launch, restock, best seller, gift…), the main benefit to push, "
    "the price/sizes/colours if missing, a promo, how it should end (the call to action), the "
    "vibe (fresh, elegant, playful…), how much to show of the back and the bottom piece. If the "
    "garment sells better in another PLACE than the chosen one (swimwear at a pool or the beach, "
    "a pyjama in the bedroom…), ask where to film it, with these options (use the label text): "
    "{lugares}.\n"
    "Also classify the garment as \"categoria\", one of: {categorias}.\n"
    "What sells reels (the brand's own playbook):\n{venta}\n"
    'Answer in JSON: {{"analisis": {{"que_es": "...", "categoria": "...", "puntos_fuertes": ["...", "..."], '
    '"para_quien": "..."}}, "resumen": "one sentence", "preguntas": [{{"pregunta": "...", '
    '"opciones": ["...", "..."]}}]}} — analisis and resumen in Spanish from Argentina.'
)

_SYSTEM_PLAN = (
    "You are the creative director and director of photography of Instagram reels for LUMA Íntima, "
    "an Argentine lingerie brand. Plan a COMPLETE reel that SELLS this set, as a shot list. Each "
    "shot is filmed separately by an AI video model (Kling) from reference images of the brand's "
    "AI model and of the set, and her voice is recorded separately.\n"
    "CRAFT:\n"
    "- Structure like the best lingerie reels: HOOK (0-2 s, the most striking image: a detail in "
    "motion, the reveal, a turn) → REVEAL of the whole set on her → DETAILS (lace, fabric, straps, "
    "closures) → FIT and BACK (a slow turn, the back, the bottom piece, how it fits her body) → "
    "the BENEFIT → the CALL TO ACTION.\n"
    "- Vary the framing AND the camera angle: never two shots in a row with the same \"plano\" or the "
    "same \"angulo\". Include at least one extreme detail, one full body and one mirror shot. Mix "
    "camera moves; the detail shots move slowly (push-in or pan).\n"
    "{ritmo}"
    "{bloques}"
    "- PLAY WITH THE CAMERA like a real creator filming herself — this is what makes it feel real: "
    "she covers the lens with her hand to change shot or colour, she passes the garment over the "
    "lens as a wipe, a quick whip pan, she walks up to the phone and picks it up, she props the "
    "phone against the mirror, a mirror selfie with the phone visible, she steps back to show the "
    "full body, she adjusts the phone and the frame shakes a little.\n"
    "- TRANSITIONS: \"enlace\" says how each shot ENTERS from the previous one: \"corte\" (straight cut), "
    "\"mano\" (the previous shot ends with her hand covering the lens, this one starts with the hand "
    "pulling away — the classic outfit/colour change), \"prenda\" (the garment passes over the lens), "
    "\"giro\" (whip pan), \"sigue\" (NO cut: this shot starts exactly on the last frame of the previous "
    "one). Every shot that starts from another one's last frame loses a bit of her face and of the "
    "garment, so: at most TWO shots chained (never two \"sigue\" in a row), each chained shot 5 to 10 s, "
    "and prefer ONE long shot of up to 10-12 s over chaining short ones. A \"sigue\" shot keeps the "
    "same colour. Colour changes go on \"mano\" or \"prenda\". The first shot is always \"corte\".\n"
    "- AI video limits: ONE simple action per shot (a transition gesture at the end is fine). If a move "
    "has several steps, split it into several shots. Turns are slow (about 180 degrees). No lying "
    "down, no hands on the face, no fast moves. Detail shots do not need her face.\n"
    "- A mirror selfie (plano espejo) is filmed BY HER PHONE: we only see her reflection, facing the "
    "mirror, phone at chest height — never her back filmed from behind.\n"
    "- Continuity: write \"continuidad\" once, in English: the exact room, furniture, bedding, light "
    "and time of day, her hairstyle, makeup and jewellery. It is the same in every shot.\n"
    "- Sensual, confident and natural like the best lingerie try-on reels, but never explicit: no "
    "visible nipples or genitals. If the owner asked for the top-removal move, split it in TWO shots "
    "of at least 4 s, both IMPLIED: (1) seen from the BACK, she pulls the bow's string and the top "
    "loosens, she holds it against her chest with one arm; (2) she turns towards the camera keeping "
    "her forearm across her bust and covers the lens with her other hand (the next shot enters with "
    "\"mano\" in another colour). Say it plainly and calmly; the video model may soften it.\n"
    "SHOT FIELDS:\n"
    "- \"tipo\": {tipos}.\n"
    "- \"plano\": one of {planos}. \"angulo\" (camera angle): one of {angulos}. \"movimiento\": one of "
    "{movimientos}.\n"
    "- \"enlace\": one of {enlaces}. \"variante\": the colour she wears (or that is shown) in the shot, "
    "0 to {max_var}.\n"
    "{dice}"
    "- \"accion\": what is seen, in Spanish, one short sentence for the owner.\n"
    "- \"toma\": the direction for the video model, in English, max 70 words: the action, her "
    "expression and what must be clearly seen of the set. Do NOT describe the framing or the "
    "camera (they go in plano and movimiento). Refer to her as \"she\" and to the set as \"the set\".\n"
    "{seg}"
    "{fotos}"
    "Also write \"concepto\": the idea of the reel in one sentence, in Spanish.\n"
    "If the owner chose a place in her answers, write \"lugar\" with its key ({lugares}); otherwise leave "
    "it empty and keep the chosen place.\n"
    "EDITING (applied when the reel is assembled): per shot \"cartel\" (floating label, Spanish, max 4 "
    "words, or empty), \"zoom\" (one of {zooms}) and \"foto_producto\" (true/false); and once \"cierre\": "
    "{{\"titulo\": \"...\", \"linea\": \"...\"}} (Spanish). Follow the editing skill below.\n"
    "YOUR PLAYBOOK (the brand's skills for this garment and place — follow it):\n{skills}\n"
    "Use what the owner answered. Do not invent a price, sizes or a promo that nobody told you.\n"
    'Answer in JSON: {{"titulo": "...", "concepto": "...", "continuidad": "...", "guion": "", "tomas": [{{"tipo": "...", '
    '"plano": "...", "angulo": "ojos", "movimiento": "...", "enlace": "corte", "variante": 0, "dice": "...", "accion": "...", '
    '"toma": "...", "foto_es": "...", "foto": "...", "final_es": "", "final": "", "cartel": "", "zoom": "no", '
    '"foto_producto": false, "seg": 0}}], "lugar": "", "cierre": {{"titulo": "...", "linea": "..."}}}}'
)


_RITMO_NORMAL = (
    "- Rhythm: most shots 3 to 5 seconds; the hook can be 2-3 s. A shot with an action or a transition "
    "gesture needs AT LEAST 4 seconds. {n_tomas} shots, about {duracion} s in total.\n"
)
_DICE_NORMAL = (
    "- \"dice\": the voice in that shot, in Spanish from Argentina (Rioplatense, voseo, casual, like a "
    "real influencer talking to her followers; no hashtags, no emojis), tied to what is seen. At "
    "most {max_palabras} words per shot; about {palabras_total} words in the whole reel. It can be "
    "empty (a silent shot). Leave \"guion\" empty.\n"
)
_SEG_NORMAL = "- \"seg\": only for a shot with empty \"dice\": 2 to 6 seconds (up to 10 inside a long take).\n"
# RITMO POR SEGUNDO: lo que hace que los reels con IA parezcan filmados de verdad.
_RITMO_SEGUNDO = (
    "- RHYTHM: SECOND BY SECOND. This is what makes AI reels look REAL (like the viral ones): {n_tomas} "
    "SHORT shots of 1.5 to 2.5 s each (the hook 1-1.5 s), about {duracion} s in total. Think like an "
    "editor cutting a real creator's footage: wide → detail → low angle → over the shoulder → mirror → "
    "macro of the fabric → profile → top-down… EVERY shot changes BOTH the \"angulo\" AND the \"plano\" "
    "versus the previous one, and the reel uses at least 6 different angulos.\n"
    "- Each shot is ONE micro-action that reads in 2 seconds: her hand runs over the lace, she adjusts "
    "a strap, the elastic stretches and snaps back, she turns her hips a quarter, she glances at the "
    "mirror, her hair falls over her shoulder, she lifts the set from its hanger. The motion must be "
    "visible in ANY 2 seconds of a 3 s take (no slow build-up).\n"
    "- At least 4 DETAIL inserts (plano detalle): the fabric texture, the lace, a seam, a strap, the "
    "closure, the waistband, her fingers touching the fabric — the camera very close, shallow depth "
    "of field, the texture razor sharp.\n"
    "- The garment ALWAYS has VOLUME and shape: worn by her, held up in her hand, hanging on a hanger, "
    "on a bust form or coming out of a gift box. NEVER lying flat, never folded in a pile on a counter. "
    "\"producto\" shots (no person) too: on a hanger swaying slightly, on a bust form, held by a hand.\n"
    "- The SAME woman in every shot: never other models, never a group.\n"
    "- Transitions: mostly \"corte\"; \"mano\" or \"prenda\" only for a colour change.\n"
)
_DICE_SEGUNDO = (
    "- \"dice\": ALWAYS EMPTY in this reel. The voice is ONE continuous narration over all the cuts: "
    "write it once in \"guion\", in Spanish from Argentina (Rioplatense, voseo, natural like a real "
    "influencer talking to her followers: short phrases, natural pauses, a hook in the first sentence "
    "and the call to action at the end; no hashtags, no emojis), about {palabras_total} words.\n"
)
_SEG_SEGUNDO = ("- \"seg\": 1.5 to 2.5 for every shot (the hook 1 to 1.5). \"toma\" max 40 words: only the "
                "micro-action of those 2 seconds.\n")
_SEG_TOMA = ("- HOW IT IS FILMED: ALL the shots are filmed together as ONE continuous AI video of up to 30 s whose "
             "prompt is the second-by-second timeline of your shots (the same woman, set and place in all of "
             "them; a new video only when the colour changes). So \"seg\" is a WHOLE number: 1, 2 or 3 "
             "(mostly 2; the hook 1). \"toma\" max 30 words: the framing is in plano/angulo/movimiento, write "
             "only the micro-action of those seconds, concrete and visual.\n")


async def claude_preguntas(reel: Dict[str, Any], prendas: List[List[str]]) -> Tuple[Dict[str, Any], float]:
    system = _SYSTEM_PREGUNTAS.format(lugares="; ".join(f"{k} = {v[0]}" for k, v in LUGARES.items()),
                                      categorias=", ".join(categorias_prenda()), venta=_skill("general/venta.md"))
    data, costo = await _claude.pedir_json(system, _partes_prenda(prendas, _contexto(reel), reel),
                                           max_tokens=6000, esfuerzo="high")
    preguntas = []
    for q in (data.get("preguntas") or [])[:6]:
        if isinstance(q, dict) and str(q.get("pregunta") or "").strip():
            preguntas.append({"pregunta": _texto(q["pregunta"], 300),
                              "opciones": [_texto(o, 200) for o in (q.get("opciones") or [])[:4] if str(o).strip()],
                              "respuesta": ""})
    if not preguntas:
        raise _claude.ClaudeNoDisponible("Claude no devolvió preguntas.")
    a = data.get("analisis") if isinstance(data.get("analisis"), dict) else {}
    analisis = {"que_es": _texto(a.get("que_es"), 300), "para_quien": _texto(a.get("para_quien"), 300),
                "categoria": a.get("categoria") if a.get("categoria") in categorias_prenda() else "otra",
                "puntos_fuertes": [_texto(x, 200) for x in (a.get("puntos_fuertes") or [])[:5] if str(x).strip()]}
    return {"resumen": _texto(data.get("resumen"), 400), "preguntas": preguntas,
            "analisis": analisis if analisis["que_es"] else None}, costo


def _limpiar_toma(reel: Dict[str, Any], t: Dict[str, Any]) -> Dict[str, Any]:
    tipo = t.get("tipo") if t.get("tipo") in TIPOS else "muestra"
    if tipo == "habla" and reel.get("modo", MODO_DEFAULT) == "fondo":
        tipo = "muestra"
    dice = " ".join(_texto(t.get("dice"), 600).split()[:int((SEG_MAX - 1) * _ps(reel))])
    try:
        seg = float(t.get("seg") or 0)
    except (TypeError, ValueError):
        seg = 0
    plano = t.get("plano") if t.get("plano") in PLANOS else ("medio" if tipo == "habla" else "americano")
    mov = t.get("movimiento") if t.get("movimiento") in MOVIMIENTOS else "mano"
    try:
        var = max(0, min(len(_variantes(reel)) - 1, int(t.get("variante") or 0)))
    except (TypeError, ValueError):
        var = 0
    angulo = t.get("angulo") if t.get("angulo") in ANGULOS else "ojos"
    if _por_segundo(reel):
        dice = ""                     # la voz va corrida encima (el "guion" del reel)
        seg = (_seg_tramo({"seg": seg}) if _por_toma(reel)
               else max(SEG_RITMO_MIN, min(SEG_RITMO_MAX, seg or SEG_RITMO)))
    return {"id": "t" + _uuid.uuid4().hex[:7], "tipo": tipo, "plano": plano, "angulo": angulo, "movimiento": mov, "dice": dice,
            "enlace": t.get("enlace") if t.get("enlace") in ENLACES else "corte", "variante": var,
            "accion": _texto(t.get("accion"), 400), "toma": _texto(t.get("toma"), 900),
            "foto_es": _texto(t.get("foto_es"), 500), "foto": _texto(t.get("foto"), 900),
            "final_es": _texto(t.get("final_es"), 400), "final": _texto(t.get("final"), 700),
            "cartel": _texto(t.get("cartel"), 40), "zoom": t.get("zoom") if t.get("zoom") in ZOOMS else "no",
            "foto_producto": bool(t.get("foto_producto")),
            "seg": (seg if _por_segundo(reel) else max(SEG_CORTE_MIN, min(10, seg or SEG_MUESTRA))) if not dice else 0}


async def claude_plan(reel: Dict[str, Any], prendas: List[List[str]]) -> Tuple[Dict[str, Any], float]:
    qa = "\n".join(f"- {q['pregunta']} → {q.get('respuesta') or '(sin respuesta: decidí vos)'}"
                   for q in reel.get("preguntas") or [])
    dur = int(reel.get("duracion", 20))
    fondo = reel.get("modo", MODO_DEFAULT) == "fondo"
    tipos = ('"muestra" = she shows the set (her voice goes on top), "producto" = only the set, no '
             'person (on the bed, on a hanger…)') if fondo else (
            '"habla" = she talks TO THE CAMERA, medium shot or close-up, face frontal (lip-synced; use '
            'it for 1 or 2 shots at most, e.g. the hook or the call to action), "muestra" = she shows '
            'the set (her voice goes on top), "producto" = only the set, no person')
    seg_ritmo = _por_segundo(reel)
    n_tomas = (f"{max(6, round(dur / 2.3))} to {min(MAX_TOMAS_SEGUNDO, round(dur / 1.8))}" if seg_ritmo
               else f"{max(4, dur // 4)} to {min(MAX_TOMAS, max(5, dur // 3))}")
    palabras_total = int(dur * _ps(reel) * (0.8 if seg_ritmo else 0.75))
    system = _SYSTEM_PLAN.format(
                                 ritmo=(_RITMO_SEGUNDO if seg_ritmo else _RITMO_NORMAL).format(n_tomas=n_tomas, duracion=dur),
                                 dice=(_DICE_SEGUNDO if seg_ritmo else _DICE_NORMAL).format(
                                     max_palabras=int(6 * _ps(reel)), palabras_total=palabras_total),
                                 seg=(_SEG_TOMA if _por_toma(reel) else _SEG_SEGUNDO) if seg_ritmo else _SEG_NORMAL,
                                 angulos=", ".join(f"{k} ({v[1]})" for k, v in ANGULOS.items()),
                                 tipos=tipos, planos=", ".join(PLANOS), movimientos=", ".join(MOVIMIENTOS),
                                 enlaces=", ".join(ENLACES), max_var=len(_variantes(reel)) - 1,
                                 bloques=(
        "- HOW IT IS FILMED: consecutive shots of the same colour, with no hand or garment covering the "
        "lens between them, are filmed TOGETHER in ONE generation of up to 15 s (up to 6 shots, each of "
        "AT LEAST 3 s), so the face, the garment and the room stay identical inside it. Plan in BLOCKS of "
        "up to 15 s: a 15 s reel is ONE block; a 30 s reel is TWO blocks (for example one per colour, "
        "joined by her hand covering the lens). Every new block is a new generation, so use few of them. "
        "Inside a block the shots are cuts of the same scene: do not use \"sigue\". In this mode \"toma\" is "
        "at most 40 words (the video model takes about 500 characters per shot).\n") if (_por_bloques(reel) and not _por_toma(reel)) else "",
                                 lugares=", ".join(LUGARES), zooms=", ".join(ZOOMS),
                                 skills=skills_director((reel.get("analisis") or {}).get("categoria", ""),
                                                        reel.get("lugar", "")),
                                 fotos=_GUIA_FOTOS if _fotos_clave(reel) else "")
    texto = _contexto(reel) + (f"\nThe owner's answers to your questions:\n{qa}" if qa else "")
    data, costo = await _claude.pedir_json(system, _partes_prenda(prendas, texto, reel), max_tokens=16000,
                                           esfuerzo="high")
    tomas = [_limpiar_toma(reel, t) for t in (data.get("tomas") or [])[:_max_tomas(reel)] if isinstance(t, dict)]
    tomas = [t for t in tomas if t["toma"] or t["accion"]]
    if len(tomas) < 2:
        raise _claude.ClaudeNoDisponible("Claude no devolvió un plan usable.")
    _ordenar_enlaces(tomas, sin_sigue=_por_bloques(reel) or _fotos_clave(reel))
    guion = _texto(data.get("guion"), 1500) if seg_ritmo else ""
    if seg_ritmo and not guion:
        guion = DICE_DEFAULT
    return {"titulo": _texto(data.get("titulo"), 120), "concepto": _texto(data.get("concepto"), 400),
            "continuidad": _texto(data.get("continuidad"), 900), "tomas": tomas, "guion": guion,
            "lugar_plan": data.get("lugar") if data.get("lugar") in LUGARES else "",
            "cierre": _limpiar_cierre(data.get("cierre"), reel)}, costo


_GUIA_FOTOS = (
    "- FOTOS CLAVE: every shot STARTS from a still photo made first with a photo-editing model that "
    "copies the real product photos exactly; the video model then animates it. So for each shot write "
    "\"foto\" (English, max 60 words): that STARTING frame — her pose caught mid-movement (never a "
    "stiff catalogue pose), the framing, where she is in the place, what of the set is clearly visible "
    "(the product must be the hero of the frame), and \"foto_es\": the same in Spanish for the owner. "
    "\"toma\" then only says what MOVES from that frame (small, slow, 3-5 s). For a turn that must "
    "show the back, also write \"final\"/\"final_es\": the LAST frame (she seen from behind, the back of "
    "the set clearly visible); leave them empty otherwise. A \"producto\" shot's foto describes the set "
    "alone. Do not use \"sigue\" (each shot starts on its own photo).\n"
)


def _limpiar_cierre(c: Any, reel: Dict[str, Any]) -> Dict[str, str]:
    """El cartel final: lo que escribió el director o, si no, el precio y escribinos por DM."""
    c = c if isinstance(c, dict) else {}
    precio = _texto((reel.get("info") or {}).get("precio"), 40)
    return {"titulo": _texto(c.get("titulo"), 40) or (_precio_sticker(precio) if precio else
                                                        _texto((reel.get("info") or {}).get("producto"), 40)),
            "linea": _texto(c.get("linea"), 60) or "Escribinos por DM"}


MOTION_DEFAULT = {"subs": "clasico", "fuente": "moderna", "corte": "ninguno", "logo": False, "esquina": "abajo_der"}


def _motion(reel: Dict[str, Any]) -> Dict[str, Any]:
    m = {**MOTION_DEFAULT, **(reel.get("motion") or {})}
    if m["subs"] not in motion.ESTILOS_SUBS:
        m["subs"] = "clasico"
    if m["fuente"] not in motion.FUENTES:
        m["fuente"] = "moderna"
    if m["corte"] not in motion.EFECTOS_CORTE:
        m["corte"] = "ninguno"
    if m["esquina"] not in motion.ESQUINAS:
        m["esquina"] = "abajo_der"
    m["logo"] = bool(m["logo"])
    return m


def _edicion(reel: Dict[str, Any]) -> Dict[str, bool]:
    return {**EDICION_DEFAULT, **{k: bool(v) for k, v in (reel.get("edicion") or {}).items() if k in EDICION_DEFAULT}}


def _armar_ass_filmado(reel: Dict[str, Any], durs: List[float]) -> str:
    """Subtítulos de la voz, carteles flotantes (entran deslizándose) y el cartel final."""
    ed = _edicion(reel)
    ev: List[str] = []
    t0, n_cartel = 0.0, 0
    tomas = reel.get("tomas") or []
    if ed["subtitulos"] and reel.get("_guion_seg") and reel.get("guion"):
        # La voz corrida: los subtítulos van por su cuenta, encima de los cortes.
        trozos = _trozos_sub(reel["guion"])
        total = sum(len(" ".join(x)) for x in trozos) or 1
        cur, mo = GUION_INICIO, _motion(reel)
        for tr in trozos:
            d = float(reel["_guion_seg"]) * len(" ".join(tr)) / total
            if mo["subs"] != "clasico":
                ev.append(motion.ass_sub(" ".join(tr), cur, cur + d - 0.02, mo["subs"], mo["fuente"], 1080, 1920, y=0.78))
            else:
                ev.append(f"Dialogue: 0,{_ass_tiempo(cur)},{_ass_tiempo(cur + d - 0.02)},Sub,,0,0,0,,{_ass_texto(' '.join(tr))}")
            cur += d
    for k, (t, dur) in enumerate(zip(tomas, durs)):
        t1 = t0 + dur
        if ed["subtitulos"] and t.get("dice"):
            voz = min(dur, float(t.get("voz_seg") or 0) or dur)
            trozos = _trozos_sub(t["dice"])
            total = sum(len(" ".join(x)) for x in trozos) or 1
            cur = t0
            mo = _motion(reel)
            for tr in trozos:
                d = voz * len(" ".join(tr)) / total
                if mo["subs"] != "clasico":
                    ev.append(motion.ass_sub(" ".join(tr), cur, cur + d - 0.02, mo["subs"], mo["fuente"],
                                             1080, 1920, y=0.78))
                else:
                    ev.append(f"Dialogue: 0,{_ass_tiempo(cur)},{_ass_tiempo(cur + d - 0.02)},Sub,,0,0,0,,{_ass_texto(' '.join(tr))}")
                cur += d
        fin_cartel = t1 - (SEG_CIERRE if k == len(tomas) - 1 and ed["cierre"] else 0.1)
        if ed["carteles"] and t.get("cartel") and fin_cartel - t0 > 0.8:
            # Entra deslizándose desde el costado (alternando lado) y se va con un fundido.
            izq = n_cartel % 2 == 0
            x0, x1 = (-700, 70) if izq else (1780, 1010)
            an = 7 if izq else 9
            ev.append(f"Dialogue: 1,{_ass_tiempo(t0 + 0.25)},{_ass_tiempo(fin_cartel)},Cartel,,0,0,0,,"
                      f"{{\\an{an}\\move({x0},330,{x1},330,0,320)\\fad(0,180)}}" + _ass_texto(t["cartel"]))
            n_cartel += 1
        if k == len(tomas) - 1 and ed["cierre"]:
            c = reel.get("cierre") or _limpiar_cierre(None, reel)
            if c.get("titulo") or c.get("linea"):
                ini = max(t0, t1 - SEG_CIERRE)
                ev.append(f"Dialogue: 2,{_ass_tiempo(ini)},{_ass_tiempo(t1)},Cierre,,0,0,0,,"
                          "{\\pos(540,900)\\fad(200,0)\\fscx70\\fscy70\\t(0,260,\\fscx100\\fscy100)}"
                          + _ass_texto(c.get("titulo") or "")
                          + ("{\\fs52}\\N" + _ass_texto(c.get("linea")) if c.get("linea") else ""))
        t0 = t1
    cab = _ASS_CABECERA.replace("\n[Events]", "\n" + _ASS_ESTILOS_FILMADO.rstrip("\n") + "\n\n[Events]", 1)
    return cab + "\n".join(ev) + "\n"


def _fotos_clave(reel: Dict[str, Any]) -> bool:
    """Cada toma arranca de una foto clave (por defecto). Con lip-sync ("habla") sigue de cero."""
    return reel.get("fotos_clave", True) is not False and not _por_bloques(reel) \
        and MOTORES[reel.get("motor", MOTOR_DEFAULT)]["tipo"] == "kling"


def _ordenar_enlaces(tomas: List[Dict[str, Any]], sin_sigue: bool = False) -> None:
    """La primera entra con corte; una "sigue" lleva el color de la anterior y no puede haber más
    de MAX_SIGUE seguidas (pasa a "mano": un corte tapando la cámara). Por bloques no hace falta
    "sigue": las tomas de un bloque ya salen de la misma filmación."""
    seguidas = 0
    for i, t in enumerate(tomas):
        if i == 0 or (sin_sigue and t.get("enlace") == "sigue"):
            t["enlace"] = "corte"
        if t.get("enlace") == "sigue":
            seguidas += 1
            if seguidas > MAX_SIGUE:
                t["enlace"], seguidas = "mano", 0
            else:
                t["variante"] = tomas[i - 1].get("variante", 0)
        else:
            seguidas = 0


_PLAN_SEGUNDO = [   # (plano, ángulo, movimiento, tipo, seg, acción, toma)
    ("detalle", "ojos", "acerca", "muestra", 1.3, "Sus dedos recorren el encaje, bien de cerca.",
     "Her fingertips slowly trace the lace of the set, the fabric texture razor sharp."),
    ("americano", "bajo", "mano", "muestra", 2.0, "Se acomoda el pelo y sonríe, desde abajo.",
     "She flicks her hair back over her shoulder and smiles at the camera."),
    ("detalle", "perfil", "paneo", "muestra", 1.8, "Se acomoda un bretel, de perfil.",
     "She adjusts one strap with two fingers, the strap snaps lightly back into place."),
    ("entero", "tres_cuartos", "mano", "muestra", 2.2, "Cuerpo entero, gira la cadera un cuarto.",
     "She shifts her weight and turns her hips a quarter, showing the side of the set."),
    ("detalle", "cenital", "fija", "producto", 1.8, "La prenda colgada en la percha, se mueve un poco.",
     "The set hangs on a wooden hanger and sways slightly, the fabric catching the light."),
    ("medio", "hombro", "sigue", "muestra", 2.0, "Por sobre el hombro: se mira al espejo.",
     "Seen over her shoulder, she glances at herself in the mirror and smooths the fabric at her waist."),
    ("detalle", "alto", "acerca", "muestra", 1.6, "El elástico de la cintura, lo estira y lo suelta.",
     "She hooks a finger in the waistband, stretches the elastic a little and lets it snap back."),
    ("espejo", "ojos", "mano", "muestra", 2.2, "Selfie en el espejo, cuerpo entero.",
     "Mirror selfie: she tilts her head and shifts her pose, the whole set visible in the reflection."),
    ("americano", "espalda", "orbita", "muestra", 2.2, "Desde atrás: se ve la espalda del conjunto.",
     "Seen from behind, she looks back over her shoulder, the back of the set clearly visible."),
    ("primer", "bajo", "acerca", "muestra", 1.8, "Mira a cámara y sonríe (cierre).",
     "She looks straight into the lens with a confident soft smile, hair moving slightly."),
]


def _plan_sin_claude(reel: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Si Claude no está: un plan fijo con lo que se pidió mostrar."""
    if _por_segundo(reel):
        return [_limpiar_toma(reel, {"plano": p_, "angulo": a, "movimiento": m, "tipo": tp, "seg": sg,
                                     "accion": ac, "toma": tm})
                for p_, a, m, tp, sg, ac, tm in _PLAN_SEGUNDO]
    mostrar = reel.get("mostrar") or []
    base = [{"tipo": "muestra", "plano": "detalle", "movimiento": "acerca", "dice": "Chicas, miren lo que me llegó.",
             "accion": "El encaje bien de cerca.", "toma": "The lace of the set, the fabric moves slightly as she breathes."},
            {"tipo": "muestra", "plano": "entero", "movimiento": "mano", "dice": "",  "seg": 3,
             "accion": "Se ve el conjunto entero puesto.", "toma": "She stands relaxed, weight on one leg, and smiles."}]
    if "gira" in mostrar or "abajo" in mostrar:
        base.append({"tipo": "muestra", "plano": "americano", "movimiento": "fija", "dice": "", "seg": 4,
                     "accion": "Gira despacio y se ve la espalda y la bombacha.",
                     "toma": "She turns slowly about 180 degrees so the back of the set and the bottom piece are seen, then faces the camera again."})
    if "espejo" in mostrar:
        base.append({"tipo": "muestra", "plano": "espejo", "movimiento": "mano", "dice": "Es re cómodo, lo uso todo el día.",
                     "accion": "Selfie en el espejo.", "toma": "She takes a mirror selfie with her phone, relaxed pose."})
    base.append({"tipo": "habla" if reel.get("modo") == "habla" else "muestra", "plano": "medio", "movimiento": "acerca",
                 "dice": "Escríbanme por DM que les paso los talles.",
                 "accion": "Mira a cámara y sonríe.", "toma": "She looks at the camera and smiles."})
    return [_limpiar_toma(reel, t) for t in base]


# ── Filmar una toma ──────────────────────────────────────────────────────────

def _refs_texto(motor: str, n_ref_ella: int, n_prendas: int, con_ref: bool,
                producto: bool) -> Tuple[str, str, str]:
    """Cómo se nombra a ella, a la prenda y al cuadro de referencia del lugar, según el motor
    (y en el mismo orden en que van las imágenes en el pedido)."""
    if MOTORES[motor]["tipo"] == "kling":
        return ("" if producto else "@Element1"), ("@Element1" if producto else "@Element2"), \
            ("@Image1" if con_ref else "")
    k = 1
    escena = ""
    if con_ref:
        escena, k = "@Image1", 2
    ella = ""
    if not producto:
        ella = "the woman of " + " and ".join(f"@Image{k + i}" for i in range(n_ref_ella))
        k += n_ref_ella
    return ella, " and ".join(f"@Image{k + i}" for i in range(n_prendas)), escena


# La piel, como la de la Cara HD: lo que hace que la cara no salga lisa ni de muñeca.
_PIEL_REAL = ("Hyper-real skin: visible pores, fine vellus hair catching the light, small moles and faint "
              "freckles, slight natural redness, natural shine on the T-zone, real lip texture, individual "
              "eyelashes and real catchlights in the eyes; no smoothing, no beauty filter, no airbrush.")
_FILMADO_PRODUCTO = ("This is REAL phone footage, not a render: real indoor light with soft shadows, "
                     "subtle grain, real fabric texture and lace detail in focus. No text, no logos, "
                     "no watermark, no people.")


async def prompt_toma(doc: Dict[str, Any], reel: Dict[str, Any], t: Dict[str, Any], n_ref_ella: int,
                      n_prendas: int, con_ref: bool = False, siguiente: str = "corte", limite: int = 0,
                      con_foto: bool = False, con_fin: bool = False) -> str:
    """El pedido al motor para una toma. La voz NO va: el motor filma mudo. Cómo arranca sale de
    su "enlace" (cómo entra desde la anterior) y cómo termina, del enlace de la que sigue: la mano
    que tapa la cámara al final de una es la que se aparta al principio de la otra."""
    motor = reel.get("motor", MOTOR_DEFAULT)
    producto = t.get("tipo") == "producto"
    ella, prenda, escena = _refs_texto(motor, n_ref_ella, n_prendas, con_ref, producto)
    exacta = ("EXACTLY that design, cut, colour, lace pattern, straps and trims, front and back — "
              "not a generic or plain version")
    toma = t.get("toma") or ""
    if not toma:           # la acción la escribió la persona: se traduce tal cual
        toma = (await _al_ingles({"a": t.get("accion") or ""})).get("a") or t.get("accion") or ""
    plano = PLANOS.get(t.get("plano"), PLANOS["medio"])[1]
    mov = MOVIMIENTOS.get(t.get("movimiento"), MOVIMIENTOS["mano"])[1]
    lugar = reel.get("_lugar_desc") or LUGARES.get(reel.get("lugar"), LUGARES["dormitorio"])[1]
    cont = f" CONTINUITY (identical in every shot of this reel): {reel['continuidad']}." if reel.get("continuidad") else ""
    if escena and reel.get("lugar_ref"):
        ref = " " + _REF_LUGAR
    else:
        ref = (" " + _REF_ESCENA.format(ella="" if producto else ", and her same hairstyle, makeup and jewellery",
                                        prenda=prenda) if escena else "")
    entra = "" if con_foto else ENLACES.get(t.get("enlace") or "corte", ENLACES["corte"])[2]
    sale = ENLACES.get(siguiente or "corte", ENLACES["corte"])[1]
    ang = _angulo(t)
    cabeza = f"Vertical 9:16 Instagram reel shot on a phone. SHOT: {plano}, {ang + ', ' if ang else ''}{mov}."
    if con_foto:
        # La foto clave ES el primer cuadro: la prenda, la cara y el lugar ya están ahí exactos.
        cabeza += (" The video STARTS EXACTLY on the given first frame (the same woman, the same set, the "
                   "same place) and moves naturally and slowly from it, keeping the set exactly as it is "
                   "in that frame." if not producto else " The video STARTS EXACTLY on the given first frame.")
        if con_fin:
            cabeza += " It ENDS exactly on the given last frame."
    if producto:
        sujeto = f" SUBJECT: the lingerie set {prenda} alone ({exacta})."
        boca, realismo, realismo_corto, cuerpo = _PRODUCTO, " " + _FILMADO_PRODUCTO, " Real phone footage, real fabric texture, no text, no people.", ""
    else:
        if reel.get("puesta"):
            ropa = f"wearing the lingerie set {prenda} ({exacta})"
        else:
            ropa = (f"wearing a casual fitted black t-shirt and jeans, and holding the lingerie set {prenda} "
                    f"in her hands to show it ({exacta})")
        sujeto = (f" {ella}, the same exact woman (same face, hair, skin and body), {ropa}. The photos of "
                  f"{prenda} are ONLY for the garment: ignore any person in them, the woman is ONLY {ella}.")
        habla = t.get("tipo") == "habla" and t.get("dice")
        boca = _HABLA_MUDA if habla else _MUESTRA
        realismo, realismo_corto = " " + _FILMADO, (" Real phone footage, not an animated photo: handheld, natural "
                                                   "light, real skin texture, calm real-time movement, no plastic "
                                                   "look, no text, no other people.")
        cuerpo = await _cuerpo_en(doc)
    # Por prioridad: si el pedido se pasa de lo que acepta Kling, se acorta primero lo de abajo
    # de la lista (nunca la toma, la prenda ni la transición).
    partes = [  # (texto, versión corta o "", cuánto se puede sacrificar: más alto = primero)
        (cabeza, cabeza, 0), (sujeto, sujeto, 0), (entra, entra, 1), (" " + toma, " " + toma, 0), (sale, sale, 1),
        (f" Her body: {cuerpo}." if cuerpo else "", "", 7),
        (f" PLACE: {lugar}.", "" if cont else f" PLACE: {lugar[:140]}.", 6),
        (cont, cont[:260] + ("…" if len(cont) > 260 else ""), 4),
        (ref, (" Same room, light" + ("" if producto else " and hairstyle") + " as @Image1 (ignore its clothes).") if ref else "", 3),
        (boca, (" Her lips stay softly closed and still (the mouth is animated later)." if boca is _HABLA_MUDA else ""), 2),
        (realismo, realismo_corto, 5),
    ]
    return _componer(partes, limite)


async def prompt_bloque(reel: Dict[str, Any], t: Dict[str, Any], siguiente: str, ella: str, prenda: str,
                        con_ref: bool, limite: int = 0) -> str:
    """Una toma de un bloque multi-toma: Kling acepta 512 letras por toma, así que va lo esencial
    (plano, quién, la prenda, la acción y la transición) y lo demás sólo si entra."""
    limite = limite or MAX_PROMPT_TOMA
    producto = t.get("tipo") == "producto"
    toma = t.get("toma") or (await _al_ingles({"a": t.get("accion") or ""})).get("a") or t.get("accion") or ""
    plano = PLANOS.get(t.get("plano"), PLANOS["medio"])[1]
    mov = MOVIMIENTOS.get(t.get("movimiento"), MOVIMIENTOS["mano"])[1]
    quien = (f" No person: only the lingerie set {prenda}, exactly as in its photos." if producto
             else f" {ella} (same face, hair, body) wearing EXACTLY the set {prenda} (ignore any person in its photos).")
    entra = ENLACES_CORTO.get(t.get("enlace") or "corte", ("", ""))[1]
    sale = ENLACES_CORTO.get(siguiente or "corte", ("", ""))[0]
    ang = _angulo(t)
    cabeza = f"SHOT: {plano}, {ang + ', ' if ang else ''}{mov}."
    fijo = len(cabeza) + len(quien) + len(entra) + len(sale) + 2
    if len(toma) + fijo > limite:           # la acción se acorta en una palabra, nunca la transición
        toma = toma[:max(60, limite - fijo - 1)].rsplit(" ", 1)[0].rstrip(",;") + "."
    cont = reel.get("continuidad") or ""
    partes = [
        (cabeza, cabeza, 0), (quien, quien, 0), (entra, entra, 0), (" " + toma, " " + toma, 0), (sale, sale, 0),
        (" Same room, light" + ("" if producto else " and hair") + " as @Image1 (ignore its clothes)." if con_ref else "", "", 3),
        (f" {cont[:160]}" if cont else "", "", 4),
        (" Real handheld phone footage, calm real-time movement, real skin and fabric.", "", 5),
    ]
    return _componer(partes, limite)


def _componer(partes: List[Tuple[str, str, int]], limite: int = 0) -> str:
    """Arma el pedido sin pasarse de `limite` caracteres: acorta primero lo más sacrificable."""
    limite = limite or MAX_PROMPT
    textos = [p[0] for p in partes]
    for k in sorted(range(len(partes)), key=lambda k: -partes[k][2]):
        if len("".join(textos)) <= limite:
            break
        if partes[k][2] > 0:
            textos[k] = partes[k][1]
    out = "".join(textos).strip()
    if len(out) > limite:           # último recurso: cortar en una frase
        out = out[:limite]
        out = out[:out.rfind(". ") + 1] if ". " in out[limite // 2:] else out
    return out


async def _voz(doc: Dict[str, Any], reel: Dict[str, Any], texto: str, destino: Path) -> float:
    """La voz de Reels (Gemini, rioplatense) con su energía y el aire de micrófono, en
    `destino`; devuelve los segundos. Va ANTES del lip-sync: la boca sincroniza con esta voz."""
    falso = {"voz": reel.get("voz") or "", "tono": reel.get("tono") or "cercana",
             "voz_energia": reel.get("energia") or ENERGIA_FILMADO}
    await _cobrar(COSTO_TTS)
    mp3 = await _tts_mp3(texto, _voz_reel(doc, falso), doc, instruccion=_instruccion_voz(doc, falso))
    await budget_record("filmado_voz", "mp3", COSTO_TTS, 1, note="filmado: voz")
    af = ",".join(x for x in (ENERGIAS_VOZ.get(falso["voz_energia"], ENERGIAS_VOZ[ENERGIA_FILMADO])["af"],
                              _AF_VOZ if reel.get("mic", True) else "") if x)
    if af:
        try:
            mp3 = await asyncio.to_thread(_tratar_voz, mp3, af, destino.parent, destino.stem + "_t")
        except Exception as e:
            print(f"[filmado] no pude tratar la voz: {e}")
    destino.write_bytes(mp3)
    return round(_duracion_video(destino) or len(texto.split()) / _ps(reel), 2)


GUION_INICIO = 0.25                 # la voz corrida entra apenas arranca el reel
GUION_TEMPO_MAX = 1.12              # si no entra, se apura un poco (más se nota)


async def _voz_guion(doc: Dict[str, Any], reel: Dict[str, Any]) -> Optional[Tuple[Path, float]]:
    """La VOZ CORRIDA del ritmo por segundo: el guion entero en una sola grabación (la entonación
    de una frase no se corta en cada toma). Se guarda por texto y voz: no se vuelve a pagar."""
    g = (reel.get("guion") or "").strip()
    if not (_por_segundo(reel) and g):
        return None
    clave = "|".join(str(reel.get(k) or "") for k in ("voz", "tono", "energia", "mic")) + "|" + g
    p = _dir(reel["id"]) / f"guion_{hashlib.sha1(clave.encode()).hexdigest()[:12]}.mp3"
    if not p.exists():
        for viejo in _dir(reel["id"]).glob("guion_*.mp3"):
            viejo.unlink(missing_ok=True)
        await _voz(doc, reel, g, p)
    return p, _duracion_video(p)


def _poner_voz(video: Path, voz: Path, tempo: float) -> None:
    """La voz corrida sobre el reel ya unido (reemplaza el audio mudo de las tomas). Si la voz
    dura más que el video, el último cuadro se sostiene: la voz nunca se corta."""
    dv, da = _duracion_video(video), _duracion_video(voz) / max(1.0, tempo) + GUION_INICIO
    extra = max(0.0, da + 0.2 - dv)
    ms = int(GUION_INICIO * 1000)
    af = (f"[1:a]{f'atempo={tempo:.3f},' if tempo > 1.001 else ''}adelay={ms}|{ms},aresample=48000,"
          f"aformat=channel_layouts=stereo,apad[a]")
    out = video.with_name(video.stem + "_voz.mp4")
    if extra > 0.05:
        cmd = ["-y", "-i", str(video), "-i", str(voz), "-filter_complex",
               f"[0:v]tpad=stop_mode=clone:stop_duration={extra:.2f}[v];{af}", "-map", "[v]", "-map", "[a]",
               "-c:v", "libx264", "-crf", "17", "-preset", "medium"]
    else:
        cmd = ["-y", "-i", str(video), "-i", str(voz), "-filter_complex", af, "-map", "0:v", "-map", "[a]",
               "-c:v", "copy"]
    _ff(cmd + ["-t", f"{dv + extra:.3f}", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
        timeout=600)
    out.replace(video)


def _ff(cmd: List[str], timeout: int = 600, cwd: Optional[Path] = None) -> None:
    res = subprocess.run([_ffmpeg_bin()] + cmd, capture_output=True, timeout=timeout, cwd=cwd)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg: " + res.stderr.decode(errors="ignore")[-300:])


def _tiene_audio(p: Path) -> bool:
    res = subprocess.run([_ffmpeg_bin(), "-i", str(p)], capture_output=True, timeout=60)
    return "Audio:" in res.stderr.decode(errors="ignore")


def _completar_voz(voz: Path, seg: float, salida: Path) -> None:
    """La voz con silencio al final hasta el largo del video: así nada la corta."""
    _ff(["-y", "-i", str(voz), "-af", "apad", "-t", f"{seg:.3f}", "-ar", "48000", "-ac", "2",
         "-b:a", "192k", str(salida)], timeout=120)


def _normalizar(video: Path, salida: Path, voz: Optional[Path] = None, largo: float = 0.0,
                recorte: str = "centro") -> float:
    """La toma en 1080x1920, 30 fps, con audio estéreo (su voz, la del lip-sync o silencio).
    Con `largo` se corta a eso: "centro" salta el arranque (lo más quieto), "inicio" guarda el
    principio (la toma entra con un gesto o sigue a la anterior sin corte), "final" guarda el
    final (termina tapando la cámara). Si la voz dura más que el video, el último cuadro se
    sostiene: la voz nunca se corta."""
    dv = _duracion_video(video)
    salto = 0.0
    if largo and dv > largo + 0.05:
        salto = {"inicio": 0.0, "final": dv - largo}.get(recorte, min(0.4, dv - largo))
        dv = largo
    da = _duracion_video(voz) if voz else 0.0
    extra = max(0.0, da - dv + 0.15) if voz else 0.0
    vf = (f"scale={ANCHO}:{ALTO}:force_original_aspect_ratio=increase,crop={ANCHO}:{ALTO},fps=30"
          + (f",tpad=stop_mode=clone:stop_duration={extra:.2f}" if extra > 0.05 else "") + ",format=yuv420p")
    total = dv + (extra if extra > 0.05 else 0.0)
    ss = ["-ss", f"{salto:.2f}"] if salto else []
    if voz:
        entradas = ss + ["-i", str(video), "-i", str(voz)]
        audio = "[1:a]aresample=48000,aformat=channel_layouts=stereo,apad[au]"
    elif _tiene_audio(video):
        entradas = ss + ["-i", str(video)]
        audio = "[0:a]aresample=48000,aformat=channel_layouts=stereo,apad[au]"
    else:
        entradas = ss + ["-i", str(video), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        audio = "[1:a]anull[au]"
    _ff(["-y"] + entradas + ["-filter_complex", f"[0:v]{vf}[vo];{audio}", "-map", "[vo]", "-map", "[au]",
                             "-t", f"{total:.3f}", "-c:v", "libx264", "-crf", "17", "-preset", "medium",
                             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(salida)])
    return round(total, 2)


def _recorte(tomas: List[Dict[str, Any]], i: int) -> str:
    """Qué parte de la toma i se guarda si hay que cortarla (ver _normalizar)."""
    entra = (tomas[i].get("enlace") or "corte") if i > 0 else "corte"
    sale = (tomas[i + 1].get("enlace") or "corte") if i + 1 < len(tomas) else "corte"
    guarda_final = sale in ("mano", "prenda", "giro")
    guarda_inicio = entra != "corte"
    if guarda_final and guarda_inicio:
        return "nada"
    return "final" if guarda_final else "inicio" if guarda_inicio else "centro"


def _error_corto(e: Exception) -> str:
    """El error de fal en castellano y sin el pedido entero pegado."""
    txt = str(getattr(e, "detail", "") or e)
    if "string_too_long" in txt or "must not exceed" in txt:
        return "el pedido a Kling quedó más largo de lo que acepta (2.500 letras)"
    if "content" in txt.lower() and ("policy" in txt.lower() or "moderation" in txt.lower() or "safety" in txt.lower()):
        return "el filtro de Kling rechazó esta toma: suavizá lo que hace y rehacela"
    m = re.search(r'"msg"\s*:\s*"([^"]{1,160})', txt)
    return (m.group(1) if m else txt)[:220]


_CABEZA_PROMPT = (
    "Devolvé SOLO un JSON con el recuadro de la CABEZA (con todo el pelo) de la persona que lleva "
    "puesta la prenda, en coordenadas normalizadas de 0 a 1000 sobre la imagen: "
    '{"box_2d": [ymin, xmin, ymax, xmax]}. Si no hay ninguna persona con cabeza visible (la prenda '
    'sola, en percha, en un maniquí sin cabeza), {"box_2d": null}. Sin texto extra.'
)


async def _caja_cabeza(b64: str) -> Optional[Tuple[float, float, float, float]]:
    key = await _current_api_key()
    if not key:
        return None
    body = {"contents": [{"role": "user", "parts": [{"text": _CABEZA_PROMPT}, _img_part(b64)]}],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}}
    try:
        async with httpx.AsyncClient(timeout=60) as cli:
            r = await cli.post(ANALYZE_ENDPOINT, json=body, headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        txt = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
        bb = json.loads(re.sub(r"^```(json)?|```$", "", txt.strip(), flags=re.MULTILINE).strip()).get("box_2d")
    except Exception as e:
        print(f"[filmado] no pude buscar una cabeza en la foto de la prenda: {e}")
        return None
    if not (isinstance(bb, list) and len(bb) == 4):
        return None
    ymin, xmin, ymax, xmax = [max(0.0, min(1000.0, float(v))) / 1000.0 for v in bb]
    return (ymin, xmin, ymax, xmax) if ymax - ymin > 0.02 and xmax - xmin > 0.02 else None


def _sacar_cabeza(b64: str, caja: Tuple[float, float, float, float]) -> str:
    """La foto de la prenda sin la cabeza de quien la lleva: se corta debajo del mentón; si así
    queda muy poca foto, se tapa la cabeza con un gris parejo."""
    from PIL import Image, ImageDraw
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    w, h = img.size
    ymin, xmin, ymax, xmax = caja
    corte = int(min(h, (ymax + 0.02) * h))
    if h - corte >= 0.45 * h:
        img = img.crop((0, corte, w, h))
    else:
        mx, my = (xmax - xmin) * w * 0.15, (ymax - ymin) * h * 0.1
        ImageDraw.Draw(img).rectangle((xmin * w - mx, ymin * h - my, xmax * w + mx, ymax * h + my), fill=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


async def _sin_persona(b64: str) -> str:
    """Si la foto del producto la tiene puesta una modelo, Kling copiaba a ESA modelo (en la
    prueba, en las tomas del color 2 apareció otra chica). A Kling le llega sólo la prenda."""
    k = _pfx() + "filmado:sinpersona:" + hashlib.sha1(b64.encode()).hexdigest()[:24]
    hecho = await kv.get(k)
    if hecho:
        return hecho
    caja = await _caja_cabeza(b64)
    out = await asyncio.to_thread(_sacar_cabeza, b64, caja) if caja else b64
    await kv.set(k, out)
    return out


def _vertical(b64: str) -> bytes:
    """La foto de arranque recortada al centro a 9:16 (como el video), sin achicarla."""
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    w, h = img.size
    if abs(w / h - 9 / 16) > 0.01:
        if w / h > 9 / 16:
            nw = int(h * 9 / 16)
            img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:
            nh = int(w * 16 / 9)
            img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _audio_bloque(partes: List[Tuple[Optional[Path], int]], salida: Path) -> None:
    """La voz del bloque: la de cada toma en su lugar (con silencio hasta el largo de la toma)."""
    entradas: List[str] = []
    fx: List[str] = []
    for k, (voz, seg) in enumerate(partes):
        if voz:
            entradas += ["-i", str(voz)]
        else:
            entradas += ["-f", "lavfi", "-t", str(seg), "-i", "anullsrc=r=48000:cl=stereo"]
        fx.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{seg},asetpts=N/SR/TB[a{k}]")
    grafo = ";".join(fx) + ";" + "".join(f"[a{k}]" for k in range(len(partes))) + f"concat=n={len(partes)}:v=0:a=1[ao]"
    _ff(["-y"] + entradas + ["-filter_complex", grafo, "-map", "[ao]", "-b:a", "192k", str(salida)], timeout=180)


def _cortes_de_escena(video: Path) -> List[float]:
    res = subprocess.run([_ffmpeg_bin(), "-i", str(video), "-vf", "select='gt(scene,0.3)',showinfo", "-f", "null", "-"],
                         capture_output=True, timeout=300)
    return [float(x) for x in re.findall(r"pts_time:([\d.]+)", res.stderr.decode(errors="ignore"))]


def _partir(video: Path, segs: List[int], salidas: List[Path]) -> List[float]:
    """Corta el video del bloque en sus tomas: donde Kling cortó de verdad (si hay un corte cerca
    de lo previsto) o en los segundos previstos. Devuelve lo que dura cada toma."""
    dur = _duracion_video(video)
    previstos, acc = [], 0.0
    for s_ in segs[:-1]:
        acc += s_
        previstos.append(acc)
    escala = dur / max(1e-6, float(sum(segs)))
    reales = _cortes_de_escena(video) if len(segs) > 1 else []
    bordes = [0.0]
    for p_ in previstos:
        p_ *= escala
        cerca = [c for c in reales if abs(c - p_) <= 0.8 and c > bordes[-1] + 1.0]
        bordes.append(min(cerca, key=lambda c: abs(c - p_)) if cerca else p_)
    bordes.append(dur)
    largos = []
    for k, out in enumerate(salidas):
        ini, fin = bordes[k], bordes[k + 1]
        _ff(["-y", "-i", str(video), "-ss", f"{ini:.3f}", "-t", f"{fin - ini:.3f}", "-c:v", "libx264", "-crf", "17",
             "-preset", "medium", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
        largos.append(round(fin - ini, 2))
    return largos


def _ultimo_cuadro(video: Path) -> Optional[str]:
    """El último cuadro de la toma: de ahí arranca la que sigue sin cortar."""
    out = video.with_name(video.stem + "_ultimo.jpg")
    res = subprocess.run([_ffmpeg_bin(), "-y", "-sseof", "-0.4", "-i", str(video), "-update", "1", "-q:v", "2",
                          str(out)], capture_output=True, timeout=60)
    if res.returncode != 0 or not out.exists():
        return None
    try:
        return base64.b64encode(out.read_bytes()).decode()
    finally:
        out.unlink()


def _preparar_edicion(reel: Dict[str, Any], clips: List[Path], prendas: List[List[str]]
                      ) -> Tuple[List[str], str, List[Tuple[Path, float, float]]]:
    """Lo que necesita el montaje para la edición: zoom por toma, el .ass con los textos y la
    foto del producto (con sus tiempos)."""
    ed = _edicion(reel)
    tomas = reel.get("tomas") or []
    durs = [_duracion_video(c) for c in clips]
    zooms = [(t.get("zoom") or "no") if ed["zoom"] else "no" for t in tomas]
    d = _dir(reel["id"])
    texto = _armar_ass_filmado(reel, durs)
    ass = ""
    if "Dialogue:" in texto:
        (d / "reel.ass").write_text(texto, encoding="utf-8")
        ass = "reel.ass"
    pips: List[Tuple[Path, float, float]] = []
    if ed["foto_producto"]:
        t0 = 0.0
        for k, (t, dur) in enumerate(zip(tomas, durs)):
            if t.get("foto_producto") and t.get("tipo") != "producto" and not pips and dur > 1.5:
                v = int(t.get("variante") or 0)
                fotos = prendas[v] if 0 <= v < len(prendas) and prendas[v] else next((p_ for p_ in prendas if p_), [])
                if fotos:
                    foto = d / f"pip_{k}.jpg"
                    foto.write_bytes(base64.b64decode(fotos[0]))
                    pips.append((foto, t0 + 0.3, t0 + dur - 0.15))
            t0 += dur
    return zooms, ass, pips


def _zoom(z: str, dur: float) -> str:
    """El zoom de la toma: "lento" se acerca toda la toma; "golpe" se acerca de golpe a la mitad."""
    n = max(2, int(round(dur * 30)))
    if z == "lento":
        expr = f"1+{ZOOM_LENTO}*on/{n}"
    elif z == "golpe":
        expr = f"if(gte(on,{n // 2}),{1 + ZOOM_GOLPE},1+0.02*on/{max(1, n // 2)})"
    else:
        return ""
    return (f"zoompan=z='{expr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={ANCHO}x{ALTO}:fps=30")


def _unir(clips: List[Path], salida: Path, look: str, camara: str, enlaces: Optional[List[str]] = None,
          zooms: Optional[List[str]] = None, ass: str = "", pips: Optional[List[Tuple[Path, float, float]]] = None,
          cwd: Optional[Path] = None) -> None:
    """Une las tomas y le pasa el filtro de Reels. Donde ella tapa la cámara, un fundido cortito
    a negro entre las dos (la mano ya oscurece: el negro une las dos tomas como en los reels).
    La edición va encima: zoom por toma, la foto del producto entrando en una esquina y los textos
    (subtítulos, carteles y cierre) en un .ass dentro de `cwd`."""
    f = _FILTRO_LOOK.get(look)
    if camara == "mano":
        base = (f"scale={int(ANCHO * _MANO_ESCALA)}:{int(ALTO * _MANO_ESCALA)}:force_original_aspect_ratio=increase,"
                f"{_MANO_CROP}")
    else:
        base = f"scale={ANCHO}:{ALTO}"
    entradas: List[str] = []
    for c in clips:
        entradas += ["-i", str(c.resolve())]
    pips = pips or []
    for foto, _, _ in pips:
        entradas += ["-i", str(foto.resolve())]
    enlaces = enlaces or ["corte"] * len(clips)
    zooms = zooms or ["no"] * len(clips)
    cadenas, pares = [], ""
    for k, c in enumerate(clips):
        fx = []
        z = _zoom(zooms[k] if k < len(zooms) else "no", _duracion_video(c))
        if z:
            fx.append(z)
        if k > 0 and enlaces[k] in ENLACES_TAPAN:
            fx.append(f"fade=t=in:st=0:d={FUNDIDO}")
        if k + 1 < len(clips) and enlaces[k + 1] in ENLACES_TAPAN:
            fx.append(f"fade=t=out:st={max(0.0, _duracion_video(c) - FUNDIDO):.3f}:d={FUNDIDO}")
        if fx:
            cadenas.append(f"[{k}:v]{','.join(fx)}[f{k}]")
            pares += f"[f{k}][{k}:a]"
        else:
            pares += f"[{k}:v][{k}:a]"
    grafo = ("".join(x + ";" for x in cadenas) + f"{pares}concat=n={len(clips)}:v=1:a=1[cv][ca];[cv]{base}"
             + (f",{f}" if f else "") + "[v0]")
    actual = "v0"
    for j, (_, a, b) in enumerate(pips):
        # La foto del catálogo con borde blanco entra deslizándose por la derecha.
        idx = len(clips) + j
        grafo += (f";[{idx}:v]scale=380:-1,pad=iw+16:ih+16:8:8:white[p{j}];[{actual}][p{j}]overlay="
                  f"x='if(lt(t-{a:.2f},0.35),W-(t-{a:.2f})/0.35*(w+50),W-w-50)':y=470:"
                  f"enable='between(t,{a:.2f},{b:.2f})'[o{j}]")
        actual = f"o{j}"
    grafo += f";[{actual}]" + (f"subtitles={ass}:fontsdir={motion.FUENTES_DIR}," if ass else "") + "format=yuv420p[vo]"
    _ff(["-y"] + entradas + ["-filter_complex", grafo, "-map", "[vo]", "-map", "[ca]", "-c:v", "libx264",
                             "-crf", "17", "-preset", "medium", "-c:a", "aac", "-b:a", "160k",
                             "-movflags", "+faststart", str(salida.resolve())], timeout=1200, cwd=cwd)


def _motion_final(reel: Dict[str, Any], clips: List[Path]) -> None:
    """Sobre el reel ya unido, sin mover nada: el golpe en cada corte y el logo."""
    mo = _motion(reel)
    final = _final(reel["id"])
    if not final.exists():
        return
    if mo["corte"] != "ninguno" and len(clips) > 1:
        cortes, acc = [], 0.0
        for c in clips[:-1]:
            acc += _duracion_video(c)
            cortes.append(acc)
        out = final.with_name(final.stem + "_cortes.mp4")
        if motion.efectos_en_cortes(final, out, cortes, mo["corte"], "9:16"):
            out.replace(final)
    lp = motion.logo_path(_pfx())
    if mo["logo"] and lp.exists():
        out = final.with_name(final.stem + "_logo.mp4")
        if motion.con_logo(final, out, lp, "9:16", mo["esquina"]):
            out.replace(final)


def _cuadro(video: Path, t: float) -> Optional[str]:
    out = video.with_name(video.stem + f"_c{int(t * 10)}.jpg")
    res = subprocess.run([_ffmpeg_bin(), "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                          "-vf", "scale=720:-2", str(out)], capture_output=True, timeout=60)
    if res.returncode != 0 or not out.exists():
        return None
    try:
        return base64.b64encode(out.read_bytes()).decode()
    finally:
        out.unlink()


async def _fal_video(cli: httpx.AsyncClient, headers: Dict[str, str], modelo: str, payload: Dict[str, Any],
                     sub_jid: str, opc: Tuple[str, ...], destino: Path) -> None:
    """Manda a fal y baja el video. Cada toma usa su propio sub-trabajo: así varias tomas
    pueden estar en fal a la vez sin pisarse las URLs."""
    await _fal_enviar(cli, headers, modelo, payload, sub_jid, opc)
    job = await kv.get(_k_job(sub_jid)) or {}
    await _fal_esperar_y_bajar(cli, headers, job["fal_status_url"], job["fal_result_url"], destino, sub_jid,
                               inicio=job.get("fal_inicio"))


async def _filmar_toma(jid: str, doc: Dict[str, Any], reel: Dict[str, Any], t: Dict[str, Any],
                       u_ella: List[str], u_prendas: List[str], cara: str, prendas: List[str],
                       cli: httpx.AsyncClient, key: str, headers: Dict[str, str],
                       u_ref: str = "", u_inicio: str = "", siguiente: str = "corte",
                       recorte: str = "centro", u_fin: str = "", con_foto: bool = False) -> Dict[str, Any]:
    """Voz → Kling mudo → lip-sync (habla) o voz encima (muestra) → toma normalizada → revisión."""
    rid, tid = reel["id"], t["id"]
    d = _dir(rid)
    motor = reel.get("motor", MOTOR_DEFAULT)
    m = MOTORES[motor]
    res: Dict[str, Any] = {"costo": 0.0, "lipsync": "", "revision": None, "error": ""}
    voz, voz_seg = None, 0.0
    if t.get("dice"):
        voz = d / f"{tid}_voz.mp3"
        voz_seg = await _voz(doc, reel, t["dice"], voz)
        res["costo"] += COSTO_TTS
        seg = max(SEG_MIN, min(SEG_MAX, int(math.ceil(voz_seg + 0.8))))
    else:
        seg = _seg_kling(reel, t)
    producto = t.get("tipo") == "producto"
    prompt = await prompt_toma(doc, reel, t, len(u_ella), len(u_prendas), bool(u_ref), siguiente,
                               con_foto=con_foto, con_fin=bool(u_fin))
    el_prenda = _elemento(u_prendas)
    if m["tipo"] == "kling":
        # La PRENDA como elemento propio (@Element2): como imagen suelta, Kling la tomaba de
        # inspiración e inventaba otra. El cuadro de la primera toma va como @Image1: el mismo
        # cuarto, la misma luz y el mismo peinado en todas.
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "elements": ([el_prenda] if producto else
                         [_elemento(u_ella), el_prenda]),
            "duration": str(seg), "aspect_ratio": "9:16", "generate_audio": False,
            "negative_prompt": _NEGATIVO, "cfg_scale": 0.5}
        if u_ref:
            payload["image_urls"] = [u_ref]
        if u_inicio:
            # La foto clave (o, en "sigue sin cortar", el último cuadro de la toma anterior).
            payload["start_image_url"] = u_inicio
        if u_fin:
            payload["end_image_url"] = u_fin          # el giro termina de espaldas, como la foto final
        opc: Tuple[str, ...] = ("negative_prompt", "cfg_scale")
    else:
        payload = {"prompt": prompt, "image_urls": ([u_ref] if u_ref else []) + ([] if producto else u_ella) + u_prendas,
                   "resolution": "720p",
                   "duration": str(seg), "aspect_ratio": "9:16", "generate_audio": False,
                   "enable_safety_checker": False}
        opc = ("enable_safety_checker", "resolution")
    crudo = d / f"{tid}_crudo.mp4"
    await _fal_video(cli, headers, m["modelo"], payload, f"{jid}-{tid}", opc, crudo)
    c = round(seg * m["precio_seg"], 3)
    res["costo"] += c
    await budget_record("filmado_toma", motor, c, 1, note=f"{doc.get('nombre', '')}: filmado, toma")
    clip_seg = _duracion_video(crudo)
    fuente, voz_final = crudo, voz
    if voz and t.get("tipo") == "habla":
        # La voz completa con silencio hasta el largo del video (nada se corta); si el video
        # quedó más corto que la voz, el lip-sync lo alarga ("bounce") en vez de cortar la voz.
        pad = d / f"{tid}_voz_pad.mp3"
        if clip_seg >= voz_seg:
            await asyncio.to_thread(_completar_voz, voz, clip_seg, pad)
        else:
            pad = voz
        ls = LIPSYNCS["sync"]
        try:
            u_vid = await _fal_subir(cli, key, crudo.read_bytes(), "video/mp4", f"{tid}.mp4")
            u_voz = await _fal_subir(cli, key, pad.read_bytes(), "audio/mpeg", f"{tid}.mp3")
            sal = d / f"{tid}_lipsync.mp4"
            await _fal_video(cli, headers, ls["modelo"],
                             {"video_url": u_vid, "audio_url": u_voz,
                              "sync_mode": "cut_off" if clip_seg >= voz_seg else "bounce"},
                             f"{jid}-{tid}-ls", ("sync_mode",), sal)
            c = round(max(clip_seg, voz_seg) * ls["precio_seg"], 3)
            res["costo"] += c
            await budget_record("filmado_lipsync", ls["modelo"], c, 1, note="filmado: lip-sync")
            # Si el lip-sync devolvió menos de lo que dura la voz, su voz entera va igual.
            fuente, voz_final = sal, (voz if _duracion_video(sal) + 0.2 < voz_seg else None)
            res["lipsync"] = "ok"
        except Exception as e:
            print(f"[filmado] lip-sync falló: {e}")
            res["lipsync"] = f"falló ({str(getattr(e, 'detail', '') or e)[:120]}): va su voz encima, sin mover la boca"
    # Las que no hablan se cortan a lo que dura su voz (la voz de fondo corre sin baches) o a
    # los segundos elegidos; la que habla queda entera.
    largo = 0.0 if t.get("tipo") == "habla" or t.get("enlace") == "sigue" or u_fin else (
        max(SEG_CORTE_MIN, voz_seg + 0.4) if voz else _seg_toma(reel, t))
    if largo and recorte != "centro":
        largo = max(largo, SEG_GESTO)     # que no se coma la acción ni el gesto de la transición
    res["seg"] = await asyncio.to_thread(_normalizar, fuente, _clip(rid, tid), voz_final, largo, recorte)
    res["voz_seg"], res["clip_seg"] = voz_seg, round(clip_seg, 2)
    for x in d.glob(f"{tid}_*"):
        try:
            x.unlink()
        except OSError:
            pass
    # Claude mira un cuadro de la toma contra la cara y la prenda (sólo informa).
    rev, c = await _revisar_toma(reel, t, _clip(rid, tid), res["seg"], cara, prendas)
    res["revision"], res["costo"] = rev, res["costo"] + c
    return res


async def _revisar_toma(reel: Dict[str, Any], t: Dict[str, Any], clip: Path, seg: float, cara: str,
                        prendas: List[str]) -> Tuple[Optional[Dict[str, Any]], float]:
    """Claude mira un cuadro de la toma contra la cara y la prenda (sólo informa)."""
    if not _claude.disponible():
        return None, 0.0
    producto = t.get("tipo") == "producto"
    cuadro = await asyncio.to_thread(_cuadro, clip, seg * 0.55)
    if not cuadro:
        return None, 0.0
    try:
        pedido = ("La prenda de las fotos del producto tiene que verse EXACTA (diseño, color, encaje, "
                  "breteles; de espalda también) "
                  + ("sola, sin nadie. " if producto else "puesta. " if reel.get("puesta") else "en sus manos. ")
                  + f"En esta toma: {t.get('accion') or ''}. "
                  + ("" if producto else "Es la misma modelo de la cara de referencia. ")
                  + "Es un cuadro de un video de celular: juzgá la prenda"
                  + ("" if producto else ", la cara") + " y que parezca real.")
        rev, c = await _claude.revisar_foto(cuadro, pedido, "" if producto else cara, prendas[:2])
        await budget_record("filmado_claude", _claude.MODELO, c, 1, note="filmado: revisión")
        return {"puntaje": rev.get("puntaje"), "fallas": rev.get("fallas")}, c
    except _claude.ClaudeNoDisponible as e:
        print(f"[filmado] Claude no pudo revisar: {e}")
        return None, 0.0


def _elemento(urls: List[str]) -> Dict[str, Any]:
    """Un elemento de Kling: la foto principal y las de referencia. Kling exige al menos una de
    referencia: con una sola foto, va repetida."""
    return {"frontal_image_url": urls[0], "reference_image_urls": urls[1:] or urls[:1]}


def _k_inicio(rid: str, tid: str) -> str:
    """La foto de arranque de un bloque (opcional): una foto de Fotos con la prenda exacta."""
    return _pfx() + f"filmado:reel:{rid}:inicio:{tid}"


def _tiempos(segs: List[float], total: int) -> List[Tuple[int, int]]:
    """Los segundos ENTEROS de cada tramo en la línea de tiempo ([desde, hasta)), estirados para
    llenar `total` (Seedance 2.5 sigue segundos enteros): cada tramo dura al menos 1 s."""
    escala = total / max(1e-6, sum(segs))
    out, acc, ini = [], 0.0, 0
    for k, s_ in enumerate(segs):
        acc += s_ * escala
        fin = total if k == len(segs) - 1 else max(ini + 1, min(total - (len(segs) - 1 - k), int(round(acc))))
        out.append((ini, fin))
        ini = fin
    return out


async def prompt_linea_de_tiempo(doc: Dict[str, Any], reel: Dict[str, Any], ts: List[Dict[str, Any]],
                                 tiempos: List[Tuple[int, int]], n_ella: int, n_prendas: int, con_ref: bool,
                                 con_probador: bool) -> str:
    """El prompt de la toma única: quiénes son las referencias, la prenda, el lugar y la LÍNEA DE
    TIEMPO segundo a segundo, con un corte seco entre tramo y tramo."""
    motor = reel.get("motor", MOTOR_TOMA)
    producto = ts[0].get("tipo") == "producto"
    ella, prenda, escena = _refs_texto(motor, n_ella, n_prendas, con_ref, producto)
    total = tiempos[-1][1]
    L = [f"Vertical 9:16 Instagram reel filmed on a phone: ONE {total}-second video made of {len(ts)} quick shots "
         "with HARD CUTS between them, edited like a real creator's reel. Follow the TIMELINE exactly: every "
         "shot starts and ends on its second and has its own framing, camera angle and movement."]
    if producto:
        L.append(f"SUBJECT: only the lingerie set of {prenda} — EXACTLY that design, cut, colour, lace pattern, "
                 "straps and trims — with volume and shape (on a hanger, a bust form or held by a hand), no person.")
    else:
        cuerpo = await _cuerpo_en(doc)
        L.append(f"THE WOMAN: {ella} — the SAME exact woman in every shot (same face, hair, skin and body). The "
                 "face close-up is her identity; the body photo is shown without the head: copy only her "
                 "proportions." + (f" Her body: {cuerpo}." if cuerpo else ""))
        if reel.get("puesta", True):
            L.append(f"SHE WEARS the lingerie set of {prenda}"
                     + (" (some of those images show it already on her own body — front, back and side — and "
                        "one is the real product photo for the exact colour)" if con_probador else "")
                     + ": EXACTLY that design, cut, colour, lace pattern, straps and trims, in every shot. "
                       "Ignore any other person in the product photos.")
        else:
            L.append(f"She wears a casual fitted black t-shirt and jeans and holds the lingerie set of {prenda} "
                     "in her hands to show it (EXACTLY that design and colour).")
    if escena and reel.get("lugar_ref"):
        L.append(f"PLACE: the real place of {escena} (empty in the photo): every shot happens inside it — same "
                 "walls, furniture, colours and light; only the camera position changes.")
    elif escena:
        L.append(f"PLACE: the same room, light and time of day as {escena} (ignore the clothes in it).")
    else:
        L.append(f"PLACE: {reel.get('_lugar_desc') or LUGARES.get(reel.get('lugar'), LUGARES['dormitorio'])[1]}.")
    if reel.get("continuidad"):
        L.append(f"CONTINUITY (identical in every shot): {reel['continuidad'][:400]}")
    lineas = []
    for t, (a, b) in zip(ts, tiempos):
        plano = PLANOS.get(t.get("plano"), PLANOS["medio"])[1]
        ang = _angulo(t)
        mov = MOVIMIENTOS.get(t.get("movimiento"), MOVIMIENTOS["mano"])[1]
        accion = t.get("toma") or (await _al_ingles({"a": t.get("accion") or ""})).get("a") or t.get("accion") or ""
        lineas.append(f"[{a}s-{b}s] {plano}, {ang + ', ' if ang else ''}{mov}: {accion[:260]}")
    L.append("TIMELINE:\n" + "\n".join(lineas))
    L.append("REALISM: real phone footage, not an animated photo — each shot starts mid-movement, handheld "
             "micro-shakes, focus and exposure breathing, real light with soft shadows, subtle grain. She moves "
             "like a real person, calm and at real-time speed (never slow motion). " + _PIEL_REAL
             + " Real fabric texture. No text, no logos, no watermark, no other people, no speech, no music.")
    return _sanear_prompt_fal("\n\n".join(L))


async def _filmar_linea_de_tiempo(jid: str, doc: Dict[str, Any], reel: Dict[str, Any], idxs: List[int],
                                  u_ella: List[str], u_prendas: List[str], cara: str, prendas: List[str],
                                  cli: httpx.AsyncClient, headers: Dict[str, str], u_ref: str,
                                  con_probador: bool) -> Dict[str, Dict[str, Any]]:
    """UNA toma de Seedance 2.5 con la línea de tiempo de estos tramos, cortada después en ellos."""
    rid, tomas, d = reel["id"], reel["tomas"], _dir(reel["id"])
    motor = reel.get("motor", MOTOR_TOMA)
    m = MOTORES.get(motor, MOTORES[MOTOR_TOMA])
    ts = [tomas[i] for i in idxs]
    producto = ts[0].get("tipo") == "producto"
    segs = [_seg_tramo(t) for t in ts]
    total = max(4, min(SEG_MAX_TOMA, int(math.ceil(sum(segs)))))
    tiempos = _tiempos(segs, total)
    ella = [] if producto else u_ella
    imgs = ([u_ref] if u_ref else []) + ella + u_prendas
    prompt = await prompt_linea_de_tiempo(doc, reel, ts, tiempos, len(ella), len(u_prendas), bool(u_ref),
                                          con_probador and not producto)
    payload = {"prompt": prompt, "image_urls": imgs[:30], "duration": str(total), "aspect_ratio": "9:16",
               "resolution": os.getenv("FILMADO_RESOLUCION_TOMA", "720p"), "generate_audio": False}
    b = ts[0]["id"]
    crudo, bloque = d / f"b{b}_crudo.mp4", d / f"b{b}_bloque.mp4"
    try:
        await _fal_video(cli, headers, m["modelo"], payload, f"{jid}-{b}", ("resolution",), crudo)
        costo = round(total * m["precio_seg"], 3)
        await budget_record("filmado_toma", motor, costo, 1,
                            note=f"{doc.get('nombre', '')}: filmado, una toma de {total} s ({len(ts)} tramos)")
        await asyncio.to_thread(_normalizar, crudo, bloque, None, 0.0)
        largos = await asyncio.to_thread(_partir, bloque, [b_ - a for a, b_ in tiempos], [_clip(rid, t["id"]) for t in ts])
    finally:
        for x in (crudo, bloque):
            x.unlink(missing_ok=True)
    res: Dict[str, Dict[str, Any]] = {}
    for t, (a, b_), lg in zip(ts, tiempos, largos):
        r = {"costo": round((b_ - a) * m["precio_seg"], 3), "seg": lg, "voz_seg": 0.0, "clip_seg": lg,
             "lipsync": "", "error": ""}
        r["revision"], c = await _revisar_toma(reel, t, _clip(rid, t["id"]), lg, cara, prendas)
        r["costo"] = round(r["costo"] + c, 3)
        res[t["id"]] = r
    return res


async def _filmar_bloque(jid: str, doc: Dict[str, Any], reel: Dict[str, Any], idxs: List[int],
                         u_ella: List[str], u_prendas: List[str], cara: str, prendas: List[str],
                         cli: httpx.AsyncClient, headers: Dict[str, str], u_ref: str,
                         voces: Dict[str, Tuple[Path, float]], u_inicio: str,
                         con_probador: bool = False) -> Dict[str, Dict[str, Any]]:
    """UNA generación de Kling con varias tomas adentro (multi_prompt), cortada después en sus tomas.
    En una toma segundo a segundo, una de Seedance 2.5 con la línea de tiempo."""
    if _por_toma(reel):
        return await _filmar_linea_de_tiempo(jid, doc, reel, idxs, u_ella, u_prendas, cara, prendas, cli, headers,
                                             u_ref, con_probador)
    rid, tomas, d = reel["id"], reel["tomas"], _dir(reel["id"])
    m = MOTORES[reel.get("motor", MOTOR_DEFAULT)]
    ts = [tomas[i] for i in idxs]
    hay_ella = ts[0].get("tipo") != "producto"
    segs = [_seg_en_bloque(reel, t, voces[t["id"]][1] if t["id"] in voces else None) for t in ts]
    sig = [(tomas[i + 1].get("enlace") or "corte") if i + 1 < len(tomas) else "corte" for i in idxs]
    ella_ref, prenda_ref = ("@Element1", "@Element2") if hay_ella else ("", "@Element1")
    if len(ts) == 1:
        prompts = [await prompt_toma(doc, reel, ts[0], len(u_ella), len(u_prendas), bool(u_ref), sig[0])]
    else:
        prompts = [await prompt_bloque(reel, t, sg, ella_ref, prenda_ref, bool(u_ref)) for t, sg in zip(ts, sig)]
    el_prenda = _elemento(u_prendas)
    total = sum(segs)
    payload: Dict[str, Any] = {
        "elements": [_elemento(u_ella), el_prenda] if hay_ella else [el_prenda],
        "duration": str(total), "aspect_ratio": "9:16", "generate_audio": False,
        "negative_prompt": _NEGATIVO, "cfg_scale": 0.5}
    if len(ts) == 1:
        payload["prompt"] = prompts[0]
    else:
        payload["multi_prompt"] = [{"prompt": p_, "duration": str(sg)} for p_, sg in zip(prompts, segs)]
        payload["shot_type"] = "customize"
    if u_ref:
        payload["image_urls"] = [u_ref]
    if u_inicio:
        payload["start_image_url"] = u_inicio
    b = ts[0]["id"]
    crudo, audio, bloque = d / f"b{b}_crudo.mp4", d / f"b{b}_voz.mp3", d / f"b{b}_bloque.mp4"
    try:
        await _fal_video(cli, headers, m["modelo"], payload, f"{jid}-{b}", ("negative_prompt", "cfg_scale"), crudo)
        await budget_record("filmado_toma", reel.get("motor", MOTOR_DEFAULT), round(total * m["precio_seg"], 3), 1,
                            note=f"{doc.get('nombre', '')}: filmado, bloque de {len(ts)} tomas")
        await asyncio.to_thread(_audio_bloque, [(voces[t["id"]][0] if t["id"] in voces else None, sg)
                                                for t, sg in zip(ts, segs)], audio)
        await asyncio.to_thread(_normalizar, crudo, bloque, audio, 0.0)
        largos = await asyncio.to_thread(_partir, bloque, segs, [_clip(rid, t["id"]) for t in ts])
    finally:
        for x in (crudo, audio, bloque):
            x.unlink(missing_ok=True)
    res: Dict[str, Dict[str, Any]] = {}
    for t, sg, lg in zip(ts, segs, largos):
        r = {"costo": round(sg * m["precio_seg"] + (COSTO_TTS if t["id"] in voces else 0.0), 3), "seg": lg,
             "voz_seg": voces[t["id"]][1] if t["id"] in voces else 0.0, "clip_seg": lg, "lipsync": "", "error": ""}
        r["revision"], c = await _revisar_toma(reel, t, _clip(rid, t["id"]), lg, cara, prendas)
        r["costo"] = round(r["costo"] + c, 3)
        res[t["id"]] = r
    return res


async def _filmar_bloques(jid: str, doc: Dict[str, Any], reel: Dict[str, Any], ella: List[str], cara: str,
                          prendas: List[List[str]], solo: str, cli: httpx.AsyncClient, key: str,
                          headers: Dict[str, str], prendas_ella: Optional[List[List[str]]] = None,
                          lugar: Optional[str] = None) -> List[str]:
    """Filma por bloques las tomas que faltan. Devuelve las fallas (las tomas que salieron quedan)."""
    rid, tomas, d = reel["id"], reel["tomas"], _dir(reel["id"])
    faltan = [i for i, t in enumerate(tomas) if not _clip(rid, t["id"]).exists()]
    if solo:
        faltan = next((g for g in _bloques(reel, faltan) if any(tomas[i]["id"] == solo for i in g)), [])
    fallas: List[str] = []
    if not faltan:
        return fallas
    await _job_set(jid, {"estado": "generando", "paso": "Grabando su voz para cada toma…"})
    voces: Dict[str, Tuple[Path, float]] = {}
    for i in faltan:
        t = tomas[i]
        if t.get("dice"):
            p_ = d / f"{t['id']}_voz.mp3"
            voces[t["id"]] = (p_, await _voz(doc, reel, t["dice"], p_))
    grupos = _bloques(reel, faltan, {k: v[1] for k, v in voces.items()})
    await _job_set(jid, {"paso": "Subiendo sus fotos y la prenda a fal…"})
    u_ella = [await _fal_subir(cli, key, base64.b64decode(b), "image/jpeg", f"{rid}-ella{i}.jpg") for i, b in enumerate(ella)]
    u_vars: Dict[int, List[str]] = {}
    subiendo = asyncio.Lock()

    def color(i: int) -> int:
        v = int(tomas[i].get("variante") or 0)
        return v if 0 <= v < len(prendas) and prendas[v] else next(k for k, p_ in enumerate(prendas) if p_)

    async def urls_color(v: int, con_ella: bool = False) -> List[str]:
        # Con ella en la toma, el PROBADOR (su cuerpo con la prenda) + una real, si lo hay.
        fotos = prendas_ella[v] if (con_ella and prendas_ella and v < len(prendas_ella) and prendas_ella[v]) else prendas[v]
        k = v * 2 + (1 if fotos != prendas[v] else 0)
        async with subiendo:
            if k not in u_vars:
                u_vars[k] = [await _fal_subir(cli, key, base64.b64decode(b), "image/jpeg", f"{rid}-v{v}-{k}-prenda{i}.jpg")
                             for i, b in enumerate(fotos)]
            return u_vars[k]

    sem = asyncio.Semaphore(PARALELO)
    hechos: List[int] = []

    async def uno(g: List[int], u_ref: str) -> None:
        nombre = f"Toma {g[0] + 1}" + (f" a {g[-1] + 1}" if len(g) > 1 else "")
        u_inicio = ""
        async with sem:
            try:
                if tomas[g[0]].get("inicio"):
                    foto = await kv.get(_k_inicio(rid, tomas[g[0]]["id"]))
                    if foto:
                        u_inicio = await _fal_subir(cli, key, await asyncio.to_thread(_vertical, foto), "image/jpeg",
                                                    f"{rid}-{tomas[g[0]]['id']}-arranque.jpg")
                v = color(g[0])
                con_ella = tomas[g[0]].get("tipo") != "producto"
                u_pr = await urls_color(v, con_ella and _por_toma(reel))
                res = await _filmar_bloque(jid, doc, reel, g, u_ella, u_pr, cara, prendas[v], cli,
                                           headers, u_ref, voces, u_inicio,
                                           con_probador=bool(con_ella and _por_toma(reel) and prendas_ella
                                                             and v < len(prendas_ella) and prendas_ella[v] != prendas[v]))
            except Exception as e:
                fallas.append(f"{nombre}: {_error_corto(e)}")
                res = {tomas[i]["id"]: {"error": _error_corto(e), "costo": 0.0} for i in g}
        async with _lock(rid):
            fresco = await _reel(rid)
            for x in fresco["tomas"]:
                r = res.get(x["id"])
                if r:
                    x.update({k: r.get(k) for k in ("lipsync", "revision", "error", "seg", "voz_seg", "clip_seg") if k in r})
                    x["costo"] = round(float(x.get("costo") or 0) + float(r.get("costo") or 0), 2)
                    fresco["costo"] = round(float(fresco.get("costo") or 0) + float(r.get("costo") or 0), 2)
            await _guardar(fresco)
        hechos.append(len(g))
        await _job_set(jid, {"paso": f"Filmando por bloques: {len(hechos)} de {len(grupos)} listos"
                                     + (f" ({len(fallas)} fallaron)" if fallas else "") + "…"})

    # El cuadro del cuarto sale del primer bloque con ella (filmado primero); va a los demás.
    # Con "Mi lugar", el lugar real es la referencia de todos.
    ref = lugar or await kv.get(_k_ref(rid))
    if not ref:
        primero = next((g for g in grupos if tomas[g[0]].get("tipo") != "producto"), None)
        if primero and len(grupos) > 1:
            await _job_set(jid, {"paso": f"Filmando el primer bloque ({len(primero)} toma{'s' if len(primero) > 1 else ''} "
                                         "en una sola filmación; de ahí salen el cuarto, la luz y el peinado)…"})
            await uno(primero, "")
            grupos = [g for g in grupos if g is not primero]
            c0 = _clip(rid, tomas[primero[0]]["id"])
            if c0.exists():
                ref = await asyncio.to_thread(_cuadro, c0, _duracion_video(c0) * 0.5)
                if ref:
                    await kv.set(_k_ref(rid), ref)
    u_ref = await _fal_subir(cli, key, base64.b64decode(ref), "image/jpeg", f"{rid}-lugar.jpg") if (ref and grupos) else ""
    if grupos:
        await _job_set(jid, {"paso": f"Filmando {len(grupos)} bloque{'s' if len(grupos) > 1 else ''} "
                                     "(cada uno, varias tomas en una sola filmación)…"})
        await asyncio.gather(*(uno(g, u_ref) for g in grupos))
    for p_, _ in voces.values():
        p_.unlink(missing_ok=True)
    return fallas


# ── Fotos clave: el arranque (y el final de los giros) de cada toma, con el motor de Fotos ──

def _k_foto(rid: str, tid: str, cual: str = "ini") -> str:
    return _pfx() + f"filmado:reel:{rid}:foto:{tid}:{cual}"


def _quiere_final(t: Dict[str, Any]) -> bool:
    return bool((t.get("final") or t.get("final_es") or "").strip()) and t.get("tipo") != "producto"


def _color_de(reel: Dict[str, Any], t: Dict[str, Any], prendas: List[List[str]]) -> int:
    v = int(t.get("variante") or 0)
    return v if 0 <= v < len(prendas) and prendas[v] else next((k for k, p_ in enumerate(prendas) if p_), 0)


async def _prompt_foto(doc: Dict[str, Any], reel: Dict[str, Any], t: Dict[str, Any], final: bool,
                       hay_ancla: bool, con_cuerpo: bool, n_prendas: int, con_lugar: bool = False,
                       con_piel: bool = False) -> str:
    """El pedido a Seedream para la foto clave. Las imágenes van en este orden: ancla (si hay),
    cara, retrato, cuerpo (si hay), fotos de la prenda."""
    producto = t.get("tipo") == "producto"
    if final:
        desc = t.get("final") or (await _al_ingles({"a": t.get("final_es") or ""})).get("a") or t.get("final_es") or ""
    else:
        desc = t.get("foto") or (await _al_ingles({"a": t.get("foto_es") or ""})).get("a") or t.get("foto_es") \
            or t.get("toma") or t.get("accion") or ""
    roles, k = [], 1
    if hay_ancla:
        roles.append(f"Image {k} is " + ("the FIRST frame of this same shot" if final else "a frame of this same reel")
                     + ": keep EXACTLY its place, furniture, light and time of day"
                     + ("" if producto else ", and her same hairstyle, makeup and jewellery")
                     + ". IGNORE the clothes in it.")
        k += 1
    elif con_lugar:
        roles.append(f"Image {k} is a photo of the REAL place (empty): the photo is taken INSIDE exactly this "
                     "place — same walls, floor, furniture, objects, colours and light; only the camera angle may change.")
        k += 1
    if not producto:
        roles.append(f"Image {k} is her face and image {k + 1} her portrait: it must be unmistakably her.")
        k += 2
        if con_piel:
            roles.append(f"Image {k} is a macro close-up of her eyes and skin: copy her REAL skin texture (pores, "
                         "fine hair, moles, natural shine) and her eye detail from it — not its framing.")
            k += 1
        if con_cuerpo:
            roles.append(f"Image {k} is her body (shown without the head): keep her real proportions and weight.")
            k += 1
    roles.append(f"Image{'s' if n_prendas > 1 else ''} {k}" + (f" to {k + n_prendas - 1}" if n_prendas > 1 else "")
                 + " = the REAL PRODUCT PHOTOS: copy the set EXACTLY (design, cut, colours, lace, straps, "
                   "trims, appliques, front and back). Ignore any person in them.")
    plano = PLANOS.get(t.get("plano"), PLANOS["medio"])[1]
    lugar = reel.get("_lugar_desc") or LUGARES.get(reel.get("lugar"), LUGARES["dormitorio"])[1]
    cont = (reel.get("continuidad") or "")[:400]
    if producto:
        quien = "Only the lingerie set, no person."
    elif reel.get("puesta", True):
        quien = "She wears ONLY that set."
    else:
        quien = "She wears a casual fitted black t-shirt and jeans and holds that set in her hands to show it."
    cuerpo = "" if producto else await _cuerpo_en(doc)
    txt = (f"Vertical 9:16 photo: one single frame taken from a real Instagram reel shot on a phone — "
           f"NOT a posed catalogue photo, NOT a studio. {' '.join(roles)} FRAME: {plano}"
           + (f", {_angulo(t)}" if _angulo(t) else "") + f". {desc} {quien}"
           + (f" Her body: {cuerpo}." if cuerpo else "")
           + f" PLACE: {lugar}." + (f" {cont}" if cont else "")
           + " The product is the hero of the frame, sharp and fully visible. " + _PIEL_REAL + " Natural "
             "light with real shadows, slight phone grain, natural colours. No text, no watermark, no other people.")
    return _sanear_prompt_fal(txt)


async def _hacer_foto(doc: Dict[str, Any], reel: Dict[str, Any], t: Dict[str, Any], final: bool,
                      cara: str, retrato: str, cuerpo: Optional[str], prendas: List[str], ancla: Optional[str],
                      correccion: str = "", lugar: Optional[str] = None,
                      refs_prenda: Optional[List[str]] = None,
                      piel: Optional[str] = None) -> Tuple[str, Optional[Dict[str, Any]], float]:
    """Seedream edit (el motor de Fotos) con las fotos reales de la prenda → Claude revisa → si
    sale floja, una sola vez más con la corrección. Devuelve (foto b64, revisión, costo)."""
    settings = dict(await get_settings())
    settings["_fal_sin_adivinar"] = True      # el filtro de salida de Seedream no se adivina a ciegas
    slug = str(settings.get("flux_tryon_model") or "bytedance/seedream/v5/pro/edit")
    precio = float(settings.get("precio_flux", 0.07) or 0.07)
    producto = t.get("tipo") == "producto"
    # Las de la prenda: el PROBADOR (su cuerpo con la prenda, frente y espalda) + una foto real,
    # si las hay; si no, las fotos reales. En las tomas de producto solo, siempre las reales.
    fotos_prenda = (refs_prenda if (refs_prenda and not producto) else prendas)[:3]
    con_lugar = bool(lugar) and not ancla
    con_piel = bool(piel) and not producto
    prompt = await _prompt_foto(doc, reel, t, final, bool(ancla), bool(cuerpo) and not producto, len(fotos_prenda),
                                con_lugar, con_piel)
    imgs: List[str] = ([ancla] if ancla else ([lugar] if con_lugar else [])) \
        + ([] if producto else [cara, retrato] + ([piel] if con_piel else []) + ([cuerpo] if cuerpo else [])) + fotos_prenda
    pedido = ("Foto clave de un reel (el primer cuadro de la toma). " if not final else "Último cuadro de la toma (de espaldas). ") \
        + f"{(t.get('final_es') if final else t.get('foto_es')) or t.get('accion') or ''}. " \
        + ("La prenda sola, sin nadie. " if producto else "Es la misma modelo de la cara de referencia. ") \
        + "La prenda de las fotos del producto tiene que verse EXACTA (diseño, colores, encaje, breteles, apliques)."
    costo, mejor = 0.0, None
    extra = correccion
    for intento in range(2):
        texto = prompt + (f"\n\nCORRECTIONS (fix these and change nothing else): {extra}" if extra else "")
        await _cobrar(precio)
        img = await fal_generate([{"text": texto}] + [_img_part(b) for b in imgs], settings, "9:16", "1K", slug)
        costo += precio
        await budget_record("filmado_foto", slug, precio, 1, note=f"{doc.get('nombre', '')}: foto clave")
        b64 = base64.b64encode(img).decode()
        rev = None
        if _claude.disponible():
            try:
                r, c = await _claude.revisar_foto(b64, pedido, "" if producto else cara, prendas[:2])
                costo += c
                await budget_record("filmado_claude", _claude.MODELO, c, 1, note="filmado: revisión de foto clave")
                rev = {"puntaje": r.get("puntaje"), "fallas": r.get("fallas"), "correccion": r.get("correccion")}
            except _claude.ClaudeNoDisponible as e:
                print(f"[filmado] Claude no pudo revisar la foto clave: {e}")
        if mejor is None or (rev and (mejor[1] or {}).get("puntaje", -1) < rev.get("puntaje", -1)):
            mejor = (b64, rev)
        if not rev or rev.get("puntaje", 0) >= PUNTAJE_REHACER or not rev.get("correccion"):
            break
        extra = rev["correccion"] + (f" Also: {correccion}" if correccion else "")
    b64, rev = mejor
    if rev:
        rev.pop("correccion", None)
    return b64, rev, round(costo, 3)


async def _cuerpos_sin_cara(refs: List[Tuple[str, str]]) -> Tuple[Optional[str], Optional[str]]:
    """Su cuerpo de frente y de espalda SIN la cabeza: la identidad la da sólo la cara (el motor
    no ve "dos caras" para mezclar) y el cuerpo da sólo las proporciones."""
    cuerpo = _ref_cuerpo(refs)
    espalda = next((b for et, b in refs if et.startswith("cuerpo entero de espalda")), None)
    return (await _sin_persona(cuerpo) if cuerpo else None, await _sin_persona(espalda) if espalda else None)


async def _probador(doc: Dict[str, Any], fotos: List[str], cuerpo_sc: str,
                    espalda_sc: Optional[str], forzar: bool = False, correccion: str = "") -> List[str]:
    """El PROBADOR de un color: su cuerpo (sin cabeza) con la prenda puesta, de frente, de
    espalda y de 3/4 (perfil: el calce de costado, para los giros), sobre gris. Se hace una vez
    y queda guardado (por personaje y fotos de la prenda). Orden: [frente, espalda, 3/4]."""
    h = hashlib.sha1(("v3|" + "|".join(f[:4000] for f in fotos[:3]) + "#" + cuerpo_sc[:4000]).encode()).hexdigest()[:20]
    k = _k_probador(str(doc.get("id", "")), h)
    hecho = None if forzar else await kv.get(k)
    if isinstance(hecho, list) and hecho:
        return hecho
    settings = dict(await get_settings())
    settings["_fal_sin_adivinar"] = True
    slug = str(settings.get("flux_tryon_model") or "bytedance/seedream/v5/pro/edit")
    precio = float(settings.get("precio_flux", 0.07) or 0.07)
    cuerpo_txt = await _cuerpo_en(doc)
    n = len(fotos[:3])
    out: List[str] = []
    for vista, base in (("FRONT", cuerpo_sc), ("BACK", espalda_sc or cuerpo_sc), (_TRES_CUARTOS, cuerpo_sc)):
        prompt = _PROBADOR.format(cuerpo=(f" ({cuerpo_txt})" if cuerpo_txt else ""), s="s" if n > 1 else "",
                                  hasta=(f" to {n + 1}" if n > 1 else ""), vista=vista)
        if correccion:
            prompt += f" CORRECTIONS (the previous attempt got these wrong; fix them): {correccion}"
        await _cobrar(precio)
        img = await fal_generate([{"text": _sanear_prompt_fal(prompt)}] + [_img_part(b) for b in [base] + fotos[:3]],
                                 settings, "3:4", "2K", slug)
        await budget_record("filmado_probador", slug, precio, 1, note=f"{doc.get('nombre', '')}: probador {vista.split()[0].lower()}")
        out.append(await _sin_persona(base64.b64encode(img).decode()))   # por si asomó la cabeza
    await kv.set(k, out)
    return out


async def probador_para(doc: Dict[str, Any], fotos: List[str], refs: List[Tuple[str, str]],
                        ficha: Any = None, forzar: bool = False, correccion: str = "") -> List[str]:
    """El probador de una prenda (también para Reels, Cambio de conjunto y Mis prendas): su
    cuerpo sin cabeza con la prenda puesta, [frente, espalda, 3/4]. Con `ficha` ([prenda,
    color] de "Mis prendas") usa el que se vio y se aprobó (o rehizo) ahí, y si no hay lo hace
    y lo deja guardado en la ficha. [] si el personaje no tiene cuerpo entero."""
    ficha = refs_luma.ficha_valida(ficha)
    pid = str(doc.get("id", ""))
    if ficha and not forzar:
        hecho = await refs_luma.probador_de(ficha[0], ficha[1], pid)
        if hecho:
            return hecho
    cuerpo_sc, espalda_sc = await _cuerpos_sin_cara(refs)
    if not cuerpo_sc or not fotos:
        return []
    pb = await _probador(doc, fotos, cuerpo_sc, espalda_sc, forzar=forzar, correccion=correccion)
    if ficha and pb:
        await refs_luma.guardar_probador(ficha[0], ficha[1], pid, pb)
    return pb


async def _kit(jid: str, doc: Dict[str, Any], reel: Dict[str, Any], refs: List[Tuple[str, str]],
               prendas: List[List[str]]) -> Dict[str, Any]:
    """El KIT DE REFERENCIAS de este reel: su cuerpo sin cara (frente y espalda), el probador de
    cada color (si está activado) y el lugar real (si eligió uno de "Mis lugares")."""
    cuerpo_sc, espalda_sc = await _cuerpos_sin_cara(refs) if reel.get("sin_cara", True) else (_ref_cuerpo(refs), None)
    prendas_ella = [list(p) for p in prendas]
    if reel.get("probador", True) and cuerpo_sc:
        hechos: Dict[str, bool] = {}
        for v, fotos in enumerate(prendas):
            if not fotos:
                continue
            await _job_set(jid, {"paso": f"Probador del color {v + 1}: su cuerpo con la prenda puesta (frente y espalda)…"})
            try:
                ficha = ((reel.get("variantes") or [])[v:v + 1] or [{}])[0].get("ficha")
                pb = (await refs_luma.probador_de(*refs_luma.ficha_valida(ficha), str(doc.get("id", "")))
                      if refs_luma.ficha_valida(ficha) else [])
                if not pb:
                    pb = await _probador(doc, fotos, cuerpo_sc, espalda_sc)
                    if refs_luma.ficha_valida(ficha):
                        await refs_luma.guardar_probador(*refs_luma.ficha_valida(ficha), str(doc.get("id", "")), pb)
                prendas_ella[v] = pb[:2] + fotos[:1] + pb[2:]   # frente, espalda, una real (el color verdadero) y 3/4
                await kv.set(_k_probador_reel(reel["id"], v), pb)
                hechos[str(v)] = True
            except Exception as e:
                print(f"[filmado] el probador del color {v + 1} no salió: {e}")
                hechos[str(v)] = False
        async with _lock(reel["id"]):
            fresco = await _reel(reel["id"])
            fresco["probador_hecho"] = hechos
            await _guardar(fresco)
    lugar_meta = await refs_luma.lugar(reel["lugar_ref"]) if reel.get("lugar_ref") else None
    if lugar_meta and lugar_meta.get("desc"):
        reel["_lugar_desc"] = (await _al_ingles({"a": lugar_meta["desc"]})).get("a") or lugar_meta["desc"]
    return {"cuerpo_sc": cuerpo_sc, "espalda_sc": espalda_sc, "prendas_ella": prendas_ella,
            "piel": (await caras_hd(doc)).get("ojos_hd"),       # Cara HD: el macro de ojos y piel
            "lugar": reel.get("lugar_ref") if lugar_meta and lugar_meta.get("vistas") else ""}


async def _procesar_fotos(jid: str, rid: str, sub: Optional[str], solo: str = "", cual: str = "",
                          correccion: str = "") -> None:
    """Hace las fotos clave que faltan: primero la base (la primera toma con ella), después la
    primera de cada color (con la base de ancla: mismo lugar y pelo), después el resto (con la de
    su color), y las finales de los giros (con el arranque de su toma)."""
    set_current_sub(sub)
    parar = asyncio.Event()
    _spawn(_latir(jid, parar))
    try:
        reel = await _reel(rid)
        doc = await _doc(reel["pid"])
        refs = await _refs_identidad(doc)
        if not refs:
            raise RuntimeError("Este personaje todavía no tiene retrato aprobado.")
        retrato = refs[0][1]
        cara = await cara_identidad(doc, retrato) or retrato
        cuerpo = _ref_cuerpo(refs)
        await _job_set(jid, {"estado": "generando", "paso": "Revisando las fotos de la prenda…"})
        prendas = [[await _sin_persona(b) for b in fotos] for fotos in await _prendas(rid, reel)]
        if not any(prendas):
            raise RuntimeError("No encuentro las fotos de la prenda de este reel.")
        kit = await _kit(jid, doc, reel, refs, prendas)
        tomas = reel["tomas"]
        fallas: List[str] = []
        hechas = [0]

        def pendiente(t: Dict[str, Any], c: str) -> bool:
            if solo:
                return t["id"] == solo and (cual or "ini") == c
            return not t.get("foto_ok" if c == "ini" else "final_ok")

        trabajos = [(t, "ini") for t in tomas if pendiente(t, "ini")] + \
                   [(t, "fin") for t in tomas if _quiere_final(t) and pendiente(t, "fin")]
        total = len(trabajos)

        async def una(t: Dict[str, Any], c: str, ancla: Optional[str]) -> None:
            i = tomas.index(t)
            try:
                v = _color_de(reel, t, prendas)
                # De espaldas (la final de un giro) va su espalda; si no, su cuerpo de frente. Sin cara.
                su_cuerpo = (kit["espalda_sc"] if (c == "fin" and kit["espalda_sc"]) else kit["cuerpo_sc"]) or cuerpo
                lugar = (await refs_luma.vista_para(kit["lugar"], t.get("plano") or "")) if (kit["lugar"] and not ancla) else None
                b64, rev, costo = await _hacer_foto(doc, reel, t, c == "fin", cara, retrato, su_cuerpo, prendas[v],
                                                    ancla, correccion if solo else "", lugar=lugar,
                                                    refs_prenda=kit["prendas_ella"][v], piel=kit["piel"])
                await kv.set(_k_foto(rid, t["id"], c), b64)
                cambios = {("foto_ok" if c == "ini" else "final_ok"): True,
                           ("foto_rev" if c == "ini" else "final_rev"): rev}
            except Exception as e:
                fallas.append(f"Foto {'final ' if c == 'fin' else ''}de la toma {i + 1}: {_error_corto(e)}")
                costo, cambios = 0.0, {}
            async with _lock(rid):
                fresco = await _reel(rid)
                for x in fresco["tomas"]:
                    if x["id"] == t["id"]:
                        x.update(cambios)
                        x["costo"] = round(float(x.get("costo") or 0) + costo, 2)
                        if cambios:
                            _clip(rid, x["id"]).unlink(missing_ok=True)   # su video ya no va con esta foto
                fresco["costo"] = round(float(fresco.get("costo") or 0) + costo, 2)
                if cambios:
                    _final(rid).unlink(missing_ok=True)
                await _guardar(fresco)
            hechas[0] += 1
            await _job_set(jid, {"paso": f"Fotos clave: {hechas[0]} de {total}"
                                         + (f" ({len(fallas)} fallaron)" if fallas else "") + "…"})

        async def foto(t: Dict[str, Any]) -> Optional[str]:
            return await kv.get(_k_foto(rid, t["id"], "ini"))

        con_ella = [t for t in tomas if t.get("tipo") != "producto"]
        base = con_ella[0] if con_ella else (tomas[0] if tomas else None)
        await _job_set(jid, {"estado": "generando", "paso": f"Haciendo las fotos clave (0 de {total})…"})
        inis = [t for t, c in trabajos if c == "ini"]
        # 1) la base
        if base is not None and base in inis:
            await una(base, "ini", None)
            inis.remove(base)
        ancla_base = await foto(base) if base is not None else None

        # 2) la primera de cada color, 3) el resto
        def primera_de(v: int) -> Optional[Dict[str, Any]]:
            return next((t for t in con_ella if _color_de(reel, t, prendas) == v), None)

        sem = asyncio.Semaphore(PARALELO)

        async def con_sem(t: Dict[str, Any], c: str, ancla: Optional[str]) -> None:
            async with sem:
                await una(t, c, ancla)

        primeras = [t for t in inis if any(t is primera_de(v) for v in range(len(prendas)))]
        await asyncio.gather(*(con_sem(t, "ini", ancla_base) for t in primeras))
        resto = [t for t in inis if t not in primeras]

        async def ancla_de(t: Dict[str, Any]) -> Optional[str]:
            p0 = primera_de(_color_de(reel, t, prendas))
            return (await foto(p0) if p0 is not None and p0 is not t else None) or ancla_base

        await asyncio.gather(*[con_sem(t, "ini", await ancla_de(t)) for t in resto])
        # 4) las finales de los giros: con el arranque de su toma
        finales = [t for t, c in trabajos if c == "fin"]
        await asyncio.gather(*[con_sem(t, "fin", await foto(t)) for t in finales])
        if fallas:
            raise RuntimeError(" · ".join(fallas) + ". Las que salieron quedaron guardadas.")
        await _job_set(jid, {"estado": "listo", "paso": ""})
    except Exception as e:
        await _job_set(jid, {"estado": "error", "error": str(getattr(e, "detail", "") or e)[:900]})
    finally:
        parar.set()


def _costo_foto() -> float:
    return round(0.07 + COSTO_CLAUDE_FOTO, 2)


async def _procesar(jid: str, rid: str, sub: Optional[str], solo: str = "") -> None:
    set_current_sub(sub)
    parar = asyncio.Event()
    _spawn(_latir(jid, parar))
    try:
        reel = await _reel(rid)
        doc = await _doc(reel["pid"])
        refs = await _refs_identidad(doc)
        if not refs:
            raise RuntimeError("Este personaje todavía no tiene retrato aprobado.")
        retrato = refs[0][1]
        cara = await cara_identidad(doc, retrato)
        prendas = await _prendas(rid, reel)
        if not any(prendas):
            raise RuntimeError("No encuentro las fotos de la prenda de este reel.")
        await _job_set(jid, {"estado": "generando", "paso": "Revisando las fotos de la prenda (si la tiene puesta "
                                                             "una modelo, se le saca la cabeza)…"})
        prendas = [[await _sin_persona(b) for b in fotos] for fotos in prendas]
        kit = await _kit(jid, doc, reel, refs, prendas)
        cuerpo = kit["cuerpo_sc"] or _ref_cuerpo(refs)
        ella = ([cara or retrato] + ([retrato] if cara else []) + ([cuerpo] if cuerpo else []))[:3]
        if _por_bloques(reel):
            key = await _fal_key()
            headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=300) as cli:
                lugar_b64 = await refs_luma.vista_para(kit["lugar"], "entero") if kit["lugar"] else None
                fallas = await _filmar_bloques(jid, doc, reel, ella, cara or retrato, prendas, solo, cli, key, headers,
                                               prendas_ella=kit["prendas_ella"], lugar=lugar_b64)
        else:
            tomas = reel["tomas"]
            faltan = [t for t in tomas if not _clip(rid, t["id"]).exists() and (not solo or t["id"] == solo)]
            key = await _fal_key()
            headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
            total = len(faltan)
            hechas: List[str] = []
            fallas: List[str] = []
            kling = MOTORES[reel.get("motor", MOTOR_DEFAULT)]["tipo"] == "kling"
            fc = _fotos_clave(reel)
            async with httpx.AsyncClient(timeout=300) as cli:
                u_ella: List[str] = []
                if faltan:
                    await _job_set(jid, {"estado": "generando", "paso": "Subiendo sus fotos y la prenda a fal…"})
                    u_ella = [await _fal_subir(cli, key, base64.b64decode(b), "image/jpeg", f"{rid}-ella{i}.jpg")
                              for i, b in enumerate(ella)]
                u_vars: Dict[int, List[str]] = {}
                subiendo = asyncio.Lock()

                def color(t: Dict[str, Any]) -> int:
                    v = int(t.get("variante") or 0)
                    return v if 0 <= v < len(prendas) and prendas[v] else next(k for k, p in enumerate(prendas) if p)

                u_vars_ella: Dict[int, List[str]] = {}

                async def urls_color(v: int, con_ella: bool = False) -> List[str]:
                    """Las fotos de la prenda de un color, subidas una sola vez. En las tomas con ella
                    van las del probador (su cuerpo con la prenda); en las de producto, las reales."""
                    async with subiendo:
                        cache, fotos = (u_vars_ella, kit["prendas_ella"][v]) if con_ella else (u_vars, prendas[v])
                        if v not in cache:
                            cache[v] = [await _fal_subir(cli, key, base64.b64decode(b), "image/jpeg",
                                                         f"{rid}-v{v}-{'ella' if con_ella else 'prenda'}{i}.jpg")
                                        for i, b in enumerate(fotos)]
                        return cache[v]

                sem = asyncio.Semaphore(PARALELO)
                intentadas: set = set()      # cada toma se intenta UNA vez por corrida (no se paga dos veces)

                async def una(t: Dict[str, Any], u_ref: str, u_inicio: str = "") -> None:
                    intentadas.add(t["id"])
                    i = tomas.index(t)
                    v = color(t)
                    siguiente = (tomas[i + 1].get("enlace") or "corte") if i + 1 < len(tomas) else "corte"
                    rc, u_fin = _recorte(tomas, i), ""
                    async with sem:
                        try:
                            if fc:
                                # Arranca en SU foto clave (y en los giros termina en la final).
                                ini = await kv.get(_k_foto(rid, t["id"], "ini"))
                                if not ini:
                                    raise RuntimeError("falta su foto clave")
                                u_inicio = await _fal_subir(cli, key, base64.b64decode(ini), "image/jpeg",
                                                            f"{rid}-{t['id']}-foto.jpg")
                                fin = await kv.get(_k_foto(rid, t["id"], "fin")) if _quiere_final(t) else None
                                if fin:
                                    u_fin = await _fal_subir(cli, key, base64.b64decode(fin), "image/jpeg",
                                                             f"{rid}-{t['id']}-final.jpg")
                                rc = "final" if siguiente in ("mano", "prenda", "giro") else "inicio"
                            r = await _filmar_toma(jid, doc, reel, t, u_ella,
                                                   await urls_color(v, t.get("tipo") != "producto"), cara or retrato,
                                                   prendas[v], cli, key, headers, u_ref, u_inicio, siguiente,
                                                   rc, u_fin, fc)
                        except Exception as e:
                            fallas.append(f"Toma {i + 1}: {_error_corto(e)}")
                            r = {"error": _error_corto(e)}
                    async with _lock(rid):
                        fresco = await _reel(rid)
                        for x in fresco["tomas"]:
                            if x["id"] == t["id"]:
                                x.update({k: r.get(k) for k in ("lipsync", "revision", "error", "seg", "voz_seg",
                                                                "clip_seg") if k in r})
                                x["costo"] = round(float(x.get("costo") or 0) + float(r.get("costo") or 0), 2)
                        fresco["costo"] = round(float(fresco.get("costo") or 0) + float(r.get("costo") or 0), 2)
                        await _guardar(fresco)
                    if not r.get("error"):
                        hechas.append(t["id"])
                    await _job_set(jid, {"paso": f"Filmando las tomas: {len(hechas)} de {total} listas"
                                                 + (f" ({len(fallas)} fallaron)" if fallas else "") + "…"})

                async def cadena(ts: List[Dict[str, Any]]) -> None:
                    """Una toma larga: las que "siguen sin cortar" arrancan en el último cuadro de la
                    anterior, así que van en orden. Las cadenas distintas se filman a la vez."""
                    for t in ts:
                        if _clip(rid, t["id"]).exists() or t["id"] in intentadas or t not in faltan:
                            continue
                        i = tomas.index(t)
                        u_inicio = ""
                        if i > 0 and t.get("enlace") == "sigue" and kling and not fc:
                            previa = _clip(rid, tomas[i - 1]["id"])
                            cuadro = await asyncio.to_thread(_ultimo_cuadro, previa) if previa.exists() else None
                            if not cuadro:
                                fallas.append(f"Toma {i + 1}: sigue a la toma {i}, que no salió")
                                continue
                            u_inicio = await _fal_subir(cli, key, base64.b64decode(cuadro), "image/jpeg",
                                                        f"{rid}-{t['id']}-inicio.jpg")
                        await una(t, u_refs.get(color(t), ""), u_inicio)

                cadenas: List[List[Dict[str, Any]]] = []
                for i, t in enumerate(tomas):
                    if i > 0 and t.get("enlace") == "sigue" and kling and not fc:
                        cadenas[-1].append(t)
                    else:
                        cadenas.append([t])
                # Un cuadro del cuarto POR COLOR, de una toma abierta (nunca de un primer plano: el
                # motor copiaba la prenda del cuadro). Si falta, se filma primero esa toma (los colores
                # a la vez) y de ahí sale para las demás de ese color.
                u_refs: Dict[int, str] = {}

                def abierta(t: Dict[str, Any]) -> bool:
                    return t.get("tipo") != "producto" and t.get("plano") in PLANOS_ABIERTOS

                async def ref_color(v: int) -> None:
                    if kit["lugar"]:
                        # El lugar REAL (Mis lugares) es la referencia del cuarto para todas las tomas.
                        lug = await refs_luma.vista_para(kit["lugar"], "entero")
                        if lug:
                            u_refs[v] = await _fal_subir(cli, key, base64.b64decode(lug), "image/jpeg", f"{rid}-milugar.jpg")
                            return
                    ref = await kv.get(_k_ref(rid, v))
                    if not ref:
                        de_color = [t for t in tomas if color(t) == v and t.get("tipo") != "producto"]
                        hecha = next((t for t in de_color if abierta(t) and _clip(rid, t["id"]).exists()), None)
                        if not hecha:
                            candidata = next((t for t in de_color if abierta(t) and t in faltan
                                              and (tomas.index(t) == 0 or t.get("enlace") != "sigue")), None)
                            if candidata:
                                await una(candidata, "")
                                hecha = candidata if _clip(rid, candidata["id"]).exists() else None
                        if hecha:
                            ref = await asyncio.to_thread(_cuadro, _clip(rid, hecha["id"]),
                                                          _duracion_video(_clip(rid, hecha["id"])) * 0.5)
                            if ref:
                                await kv.set(_k_ref(rid, v), ref)
                    if ref:
                        u_refs[v] = await _fal_subir(cli, key, base64.b64decode(ref), "image/jpeg", f"{rid}-lugar-v{v}.jpg")

                colores_faltan = sorted({color(t) for t in faltan})
                if colores_faltan and not solo and not fc:
                    await _job_set(jid, {"estado": "generando",
                                         "paso": "Filmando primero una toma abierta de cada color (de ahí salen el "
                                                 "cuarto, la luz y el peinado para las demás)…"})
                    await asyncio.gather(*(ref_color(v) for v in colores_faltan))
                pendientes = [c for c in cadenas if any(not _clip(rid, t["id"]).exists() and t in faltan for t in c)]
                if pendientes:
                    n = sum(1 for c in pendientes for t in c if not _clip(rid, t["id"]).exists() and t["id"] not in intentadas)
                    await _job_set(jid, {"estado": "generando",
                                         "paso": f"Filmando {n} toma{'s' if n != 1 else ''} "
                                                 f"(de a {PARALELO}; las que siguen sin cortar, una detrás de otra)…"})
                    await asyncio.gather(*(cadena(c) for c in pendientes))
        reel = await _reel(rid)
        clips = [_clip(rid, t["id"]) for t in reel["tomas"]]
        if solo:
            # Prueba de UNA toma: no se une nada; si salió, se ve en su lugar del plan.
            if fallas:
                raise RuntimeError(" · ".join(fallas))
            if not all(c.exists() for c in clips):
                await _job_set(jid, {"estado": "listo", "paso": "", "solo": True})
                return
        if not all(c.exists() for c in clips):
            _final(rid).unlink(missing_ok=True)
            raise RuntimeError("Algunas tomas no salieron: " + " · ".join(fallas)
                               + ". Las que sí salieron quedaron guardadas: tocá 'Filmar lo que falta'.")
        guion = await _voz_guion(doc, reel)
        tempo = 1.0
        if guion:
            # Si la voz no entra en el video, se apura un poco (hasta un 12 %); si igual no
            # entra, el último cuadro se sostiene.
            dv = sum(_duracion_video(c) for c in clips)
            tempo = max(1.0, min(GUION_TEMPO_MAX, (guion[1] + GUION_INICIO + 0.3) / max(1.0, dv)))
            reel["_guion_seg"] = round(guion[1] / tempo, 2)
        await _job_set(jid, {"paso": "Uniendo las tomas y pasándole el filtro…"})
        zooms, ass, pips = await asyncio.to_thread(_preparar_edicion, reel, clips, prendas)
        await asyncio.to_thread(_unir, clips, _final(rid), reel.get("look", LOOK_DEFAULT), reel.get("camara", "motor"),
                                [(t.get("enlace") or "corte") if k else "corte" for k, t in enumerate(reel["tomas"])],
                                zooms, ass, pips, _dir(rid))
        if guion:
            await _job_set(jid, {"paso": "Poniendo su voz corrida encima…"})
            await asyncio.to_thread(_poner_voz, _final(rid), guion[0], tempo)
        await asyncio.to_thread(_motion_final, reel, clips)
        link = await _guardar_en_drive(f"{_slug(doc.get('nombre', ''))}-reel-{rid}.mp4",
                                       _final(rid).read_bytes(), "video/mp4")
        async with _lock(rid):
            reel = await _reel(rid)
            reel["drive"] = link
            reel["seg_final"] = round(_duracion_video(_final(rid)), 1)
            reel["ts"] = time.strftime("%Y-%m-%d %H:%M")
            await _guardar(reel)
        await _job_set(jid, {"estado": "listo", "paso": "", "drive": link})
    except Exception as e:
        await _job_set(jid, {"estado": "error", "error": str(getattr(e, "detail", "") or e)[:900]})
    finally:
        parar.set()


# ── API ──────────────────────────────────────────────────────────────────────

router = APIRouter(dependencies=[Depends(_bind)])


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(PAGINA.replace("%%API%%", API).replace("%%VERSION%%", VERSION))


@router.get(API + "/config")
async def api_config() -> Dict[str, Any]:
    return {"motores": {k: {"label": v["label"], "precio_seg": v["precio_seg"]} for k, v in MOTORES.items()},
            "motor_default": MOTOR_DEFAULT, "duraciones": list(DURACIONES),
            "modos": MODOS, "modo_default": MODO_DEFAULT,
            "planos": {k: v[0] for k, v in PLANOS.items()}, "movimientos": {k: v[0] for k, v in MOVIMIENTOS.items()},
            "angulos": {k: v[0] for k, v in ANGULOS.items()}, "ritmos": RITMOS, "ritmo_default": RITMO_NUEVO,
            "enlaces": {k: v[0] for k, v in ENLACES.items()}, "max_variantes": MAX_VARIANTES,
            "zooms": ZOOMS, "edicion": EDICION_NOMBRES,
            "motion": {"subs": motion.ESTILOS_SUBS, "fuentes": {k: v[1] for k, v in motion.FUENTES.items()},
                       "cortes": motion.EFECTOS_CORTE, "esquinas": motion.ESQUINAS},
            "logo": motion.logo_path(_pfx()).exists(),
            "lugares": {k: v[0] for k, v in LUGARES.items()}, "voces": VOCES, "tonos": list(TONOS),
            "mis_lugares_api": refs_luma.API, "mis_lugares_url": refs_luma.ROUTE_PREFIX,
            "energias": {k: {"nombre": v["nombre"], "palabras_seg": v["palabras_seg"]} for k, v in ENERGIAS_VOZ.items()},
            "energia_default": ENERGIA_FILMADO, "claude": _claude.disponible(),
            "looks": {k: v for k, v in LOOKS.items() if k in _FILTRO_LOOK or k == "limpio"},
            "look_default": LOOK_DEFAULT, "camaras": CAMARAS, "tipos": TIPOS,
            "mostrar": {k: v[0] for k, v in MOSTRAR.items()}, "mostrar_default": list(MOSTRAR_DEFAULT),
            "costo_plan": COSTO_PLAN, "costo_foto": _costo_foto(), "fal_key": bool(await _fal_key())}


def _ajustes(payload: Dict[str, Any], reel: Dict[str, Any]) -> None:
    """Los ajustes del reel que vienen de la pantalla (sólo lo que llegó)."""
    voces_ok = {v for lst in VOCES.values() for v, _ in lst}
    elegir = {"motor": MOTORES, "modo": MODOS, "lugar": LUGARES, "tono": TONOS, "energia": ENERGIAS_VOZ,
              "look": LOOKS, "camara": CAMARAS, "ritmo": RITMOS}
    for k, validos in elegir.items():
        if payload.get(k) in validos:
            reel[k] = payload[k]
    if reel.get("ritmo") == "toma":
        reel["motor"] = MOTOR_TOMA       # el único que filma 30 s siguiendo los segundos
    if "voz" in payload:
        reel["voz"] = payload["voz"] if payload["voz"] in voces_ok else ""
    for k in ("puesta", "mic", "bloques", "fotos_clave", "probador", "sin_cara"):
        if k in payload:
            reel[k] = bool(payload[k])
    if "lugar_ref" in payload:
        lr = str(payload.get("lugar_ref") or "")
        reel["lugar_ref"] = lr if (len(lr) <= 16 and lr.isalnum()) else ""
    if "guion" in payload:
        reel["guion"] = _texto(payload["guion"], 1500)
    if "duracion" in payload:
        try:
            reel["duracion"] = int(payload["duracion"]) if int(payload["duracion"]) in DURACIONES else 20
        except (TypeError, ValueError):
            pass
    if "mostrar" in payload:
        reel["mostrar"] = [k for k in (payload.get("mostrar") or []) if k in MOSTRAR]
    if isinstance(payload.get("edicion"), dict):
        reel["edicion"] = {k: bool(payload["edicion"].get(k, v)) for k, v in _edicion(reel).items()}
    if isinstance(payload.get("motion"), dict):
        reel["motion"] = _motion({"motion": payload["motion"]})
    if isinstance(payload.get("cierre"), dict):
        reel["cierre"] = {"titulo": _texto(payload["cierre"].get("titulo"), 40),
                          "linea": _texto(payload["cierre"].get("linea"), 60)}
    if isinstance(payload.get("info"), dict):
        reel["info"] = {k: _texto(payload["info"].get(k), 300 if k != "notas" else 800)
                        for k in ("producto", "precio", "talles", "colores", "promo", "notas")}


@router.post(API + "/reel")
async def api_nuevo(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Arranca un reel: guarda la prenda y lo pedido, y Claude pregunta."""
    doc = await _doc(str(payload.get("pid") or ""))
    if not (doc.get("hoja") or {}).get("retrato"):
        raise HTTPException(400, "Ese personaje todavía no tiene retrato aprobado.")
    # Uno o varios colores de la prenda, cada uno con hasta 3 fotos (frente, espalda, detalle).
    crudas = payload.get("variantes")
    if not isinstance(crudas, list) or not crudas:
        crudas = [{"nombre": "", "fotos": payload.get("prendas") or []}]
    prendas: List[List[str]] = []
    variantes: List[Dict[str, Any]] = []
    for var in crudas[:MAX_VARIANTES]:
        if not isinstance(var, dict):
            continue
        fotos = []
        ficha = refs_luma.ficha_valida(var.get("ficha"))
        if ficha:
            # De "Mis prendas": sus fotos tal cual, y el probador que se aprobó ahí.
            fotos = (await refs_luma.fotos_color(*ficha))[:3]
            if not fotos:
                raise HTTPException(400, "Esa prenda de Mis prendas ya no tiene ese color.")
        for a in ([] if ficha else (var.get("fotos") or [])[:3]):
            try:
                fotos.append(_compress_ref(base64.b64decode(_strip_data_url(str(a))), max_dim=1536, q=92))
            except Exception:
                raise HTTPException(400, "No pude leer una foto de la prenda.")
        if fotos:
            prendas.append(fotos)
            variantes.append({"nombre": _texto(var.get("nombre"), 40), "n": len(fotos),
                              **({"ficha": ficha} if ficha else {})})
    if not prendas:
        raise HTTPException(400, "Subí al menos una foto de la prenda (mejor frente y espalda).")
    rid = "r" + _uuid.uuid4().hex[:9]
    reel: Dict[str, Any] = {"id": rid, "pid": doc["id"], "variantes": variantes, "motor": MOTOR_DEFAULT,
                            "modo": MODO_DEFAULT, "ritmo": RITMO_NUEVO, "guion": "", "bloques": False, "fotos_clave": True, "lugar": "dormitorio", "puesta": True, "voz": "",
                            "tono": "cercana", "energia": ENERGIA_FILMADO, "mic": True, "look": LOOK_DEFAULT,
                            "camara": "motor", "duracion": 20, "mostrar": list(MOSTRAR_DEFAULT), "info": {},
                            "preguntas": [], "resumen": "", "analisis": None, "concepto": "", "continuidad": "", "tomas": [], "titulo": "", "costo": 0.0,
                            "creado": time.strftime("%Y-%m-%d %H:%M")}
    _ajustes(payload, reel)
    for v, fotos in enumerate(prendas):
        for i, b in enumerate(fotos):
            await kv.set(_k_prenda(rid, i, v), b)
    aviso = ""
    if _claude.disponible():
        try:
            q, c = await claude_preguntas(reel, prendas)
            reel.update(q)
            reel["costo"] = round(c, 2)
            await budget_record("filmado_claude", _claude.MODELO, c, 1, note="filmado: preguntas")
        except _claude.ClaudeNoDisponible as e:
            aviso = f"Claude no pudo preguntar ({e}): armá el plan directo."
    else:
        aviso = "Claude no está disponible (falta ANTHROPIC_API_KEY): el plan sale de una plantilla."
    await _guardar(reel)
    lista = (await kv.get(_k_reels())) or []
    await kv.set(_k_reels(), ([rid] + [x for x in lista if x != rid])[:30])
    return {"reel": _vista(reel), "aviso": aviso}


@router.get(API + "/reel/{rid}")
async def api_reel(rid: str) -> Dict[str, Any]:
    return {"reel": _vista(await _reel(rid))}


@router.post(API + "/reel/{rid}/plan")
async def api_plan(rid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Con las respuestas, Claude arma las tomas (reemplaza el plan anterior: sólo si lo pedís)."""
    async with _lock(rid):
        reel = await _reel(rid)
        _ajustes(payload, reel)
        for i, r in enumerate(payload.get("respuestas") or []):
            if i < len(reel.get("preguntas") or []):
                reel["preguntas"][i]["respuesta"] = _texto(r, 400)
        aviso = ""
        viejas = reel.get("tomas") or []
        if _claude.disponible():
            try:
                plan, c = await claude_plan(reel, await _prendas(rid, reel))
                reel.update(plan)
                reel["costo"] = round(float(reel.get("costo") or 0) + c, 2)
                await budget_record("filmado_claude", _claude.MODELO, c, 1, note="filmado: plan")
            except _claude.ClaudeNoDisponible as e:
                aviso = f"Claude no pudo armar el plan ({e}): va una plantilla que podés editar."
                reel.update({"tomas": _plan_sin_claude(reel), "concepto": "", "continuidad": "",
                            "cierre": _limpiar_cierre(None, reel), "guion": DICE_DEFAULT if _por_segundo(reel) else ""})
        else:
            reel.update({"tomas": _plan_sin_claude(reel), "concepto": "", "continuidad": "",
                            "cierre": _limpiar_cierre(None, reel), "guion": DICE_DEFAULT if _por_segundo(reel) else ""})
        for t in viejas:
            _clip(rid, t["id"]).unlink(missing_ok=True)
            for c in ("ini", "fin"):
                await kv.delete(_k_foto(rid, t["id"], c))
        if reel.get("lugar_plan"):
            reel["lugar"] = reel["lugar_plan"]       # lo que contestaste en las preguntas
        reel.pop("lugar_plan", None)
        await _borrar_refs(rid)
        _final(rid).unlink(missing_ok=True)
        await _guardar(reel)
    return {"reel": _vista(reel), "aviso": aviso}


@router.put(API + "/reel/{rid}")
async def api_ajustes(rid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Cambia ajustes del reel. Voz, lip-sync o puesta cambian lo filmado: esas tomas se rehacen."""
    async with _lock(rid):
        reel = await _reel(rid)
        antes = {k: reel.get(k) for k in ("voz", "tono", "energia", "mic", "motor", "lugar", "puesta", "modo", "bloques",
                                          "fotos_clave", "ritmo")}
        _ajustes(payload, reel)
        if reel.get("modo") == "fondo":
            for t in reel.get("tomas") or []:
                if t.get("tipo") == "habla":
                    t["tipo"] = "muestra"
        if any(reel.get(k) != v for k, v in antes.items()):
            if any(reel.get(k) != antes[k] for k in ("lugar", "puesta", "motor")):
                await _borrar_refs(rid)
                for i in range(len(reel.get("tomas") or [])):
                    await _borrar_fotos(reel, i)
            for t in reel.get("tomas") or []:
                _clip(rid, t["id"]).unlink(missing_ok=True)
        _final(rid).unlink(missing_ok=True)
        await _guardar(reel)
    return {"reel": _vista(reel)}


def _invalidar(reel: Dict[str, Any], i: int) -> None:
    """Borra lo filmado de la toma i y de las que la siguen SIN CORTAR (arrancan en su último
    cuadro: si ella cambia, ellas también)."""
    tomas = reel.get("tomas") or []
    rid = reel["id"]
    while 0 <= i < len(tomas):
        _clip(rid, tomas[i]["id"]).unlink(missing_ok=True)
        tomas[i]["revision"], tomas[i]["lipsync"], tomas[i]["error"] = None, "", ""
        i += 1
        if i >= len(tomas) or tomas[i].get("enlace") != "sigue":
            break
    _final(rid).unlink(missing_ok=True)


async def _borrar_fotos(reel: Dict[str, Any], i: int) -> None:
    """La foto clave (y la final) de la toma i ya no sirven: se borran, y su video también."""
    t = reel["tomas"][i]
    for c in ("ini", "fin"):
        await kv.delete(_k_foto(reel["id"], t["id"], c))
    t.update({"foto_ok": False, "final_ok": False, "foto_rev": None, "final_rev": None})
    _invalidar(reel, i)


def _fin(tomas: List[Dict[str, Any]], i: int) -> str:
    """Cómo termina la toma i: lo dice el enlace de la que viene después."""
    return (tomas[i + 1].get("enlace") or "corte") if i + 1 < len(tomas) else "corte"


@router.put(API + "/reel/{rid}/toma/{tid}")
async def api_toma(rid: str, tid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Lo que escribís en una toma va TAL CUAL. Si cambia, esa toma se vuelve a filmar."""
    async with _lock(rid):
        reel = await _reel(rid)
        t = next((x for x in reel.get("tomas") or [] if x["id"] == tid), None)
        if not t:
            raise HTTPException(404, "Esa toma no existe.")
        cambio = foto = False
        if payload.get("tipo") in TIPOS and payload["tipo"] != t["tipo"]:
            if payload["tipo"] == "habla" and reel.get("modo", MODO_DEFAULT) == "fondo":
                raise HTTPException(400, "En modo 'voz de fondo' ella no habla a cámara: cambiá el modo arriba.")
            t["tipo"], cambio, foto = payload["tipo"], True, True
        for k, validos in (("plano", PLANOS), ("angulo", ANGULOS), ("movimiento", MOVIMIENTOS)):
            if payload.get(k) in validos and payload[k] != t.get(k):
                t[k], cambio = payload[k], True
                foto = foto or k in ("plano", "angulo")
        for k, en, tope in (("foto_es", "foto", 500), ("final_es", "final", 400)):
            if k in payload and _texto(payload[k], tope) != (t.get(k) or ""):
                # Lo escribiste vos: la foto se hace con TU texto (traducido tal cual), no con el de Claude.
                t[k], t[en], cambio, foto = _texto(payload[k], tope), "", True, True
        if "dice" in payload and _texto(payload["dice"], 600) != t.get("dice"):
            t["dice"], cambio = _texto(payload["dice"], 600), True
        if "accion" in payload and _texto(payload["accion"], 400) != t.get("accion"):
            # La escribiste vos: la toma se arma con TU acción (traducida tal cual), no con la de Claude.
            t["accion"], t["toma"], cambio = _texto(payload["accion"], 400), "", True
        if "seg" in payload:
            try:
                s = (_seg_tramo({"seg": payload["seg"]}) if _por_toma(reel) else
                     max(SEG_RITMO_MIN, min(SEG_RITMO_MAX, float(payload["seg"]))) if _por_segundo(reel)
                     else max(SEG_CORTE_MIN, min(10, float(payload["seg"]))))
            except (TypeError, ValueError):
                s = SEG_RITMO if _por_segundo(reel) else SEG_MUESTRA
            if s != t.get("seg"):
                t["seg"], cambio = s, True
        if "variante" in payload:
            try:
                v = max(0, min(len(_variantes(reel)) - 1, int(payload["variante"])))
            except (TypeError, ValueError):
                v = 0
            if v != t.get("variante", 0):
                t["variante"], cambio, foto = v, True, True
        i = reel["tomas"].index(t)
        # La edición (cartel, zoom, foto del producto) no se filma: sólo hay que volver a unir.
        if "cartel" in payload:
            t["cartel"] = _texto(payload["cartel"], 40)
        if payload.get("zoom") in ZOOMS:
            t["zoom"] = payload["zoom"]
        if "foto_producto" in payload:
            t["foto_producto"] = bool(payload["foto_producto"])
        if any(k in payload for k in ("cartel", "zoom", "foto_producto")):
            _final(rid).unlink(missing_ok=True)
        if payload.get("enlace") in ENLACES and payload["enlace"] != (t.get("enlace") or "corte") and i > 0:
            # Cambia cómo entra ésta Y cómo termina la anterior (la mano que tapa la cámara).
            t["enlace"], cambio = payload["enlace"], True
            if t["enlace"] == "sigue":
                t["variante"] = reel["tomas"][i - 1].get("variante", 0)   # arranca en su último cuadro
            _invalidar(reel, i - 1)
        if foto:
            await _borrar_fotos(reel, i)
        if cambio:
            _invalidar(reel, i)
        await _guardar(reel)
    return {"reel": _vista(reel)}


@router.post(API + "/reel/{rid}/toma")
async def api_toma_nueva(rid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    async with _lock(rid):
        reel = await _reel(rid)
        if len(reel.get("tomas") or []) >= _max_tomas(reel):
            raise HTTPException(400, f"Hasta {_max_tomas(reel)} tomas por reel.")
        t = _limpiar_toma(reel, {"tipo": payload.get("tipo") or "muestra", "dice": payload.get("dice"),
                                 "plano": payload.get("plano") or "medio", "movimiento": payload.get("movimiento") or "mano",
                                 "accion": payload.get("accion") or "Escribí acá qué hace.",
                                 "seg": SEG_RITMO if _por_segundo(reel) else SEG_MUESTRA})
        pos = payload.get("despues")
        ids = [x["id"] for x in reel["tomas"]]
        k = ids.index(pos) + 1 if pos in ids else len(ids)
        if k > 0 and _fin(reel["tomas"], k - 1) != "corte":
            _invalidar(reel, k - 1)          # la anterior ya no termina igual
        reel["tomas"].insert(k, t)
        if k + 1 < len(reel["tomas"]) and reel["tomas"][k + 1].get("enlace") == "sigue":
            _invalidar(reel, k + 1)          # ya no sigue a la misma toma
        _final(rid).unlink(missing_ok=True)
        await _guardar(reel)
    return {"reel": _vista(reel)}


@router.delete(API + "/reel/{rid}/toma/{tid}")
async def api_toma_borrar(rid: str, tid: str) -> Dict[str, Any]:
    async with _lock(rid):
        reel = await _reel(rid)
        tomas = reel.get("tomas") or []
        k = next((i for i, x in enumerate(tomas) if x["id"] == tid), -1)
        if k >= 0:
            fin_antes = _fin(tomas, k - 1) if k > 0 else "corte"
            tomas.pop(k)
            if k > 0 and _fin(tomas, k - 1) != fin_antes:
                _invalidar(reel, k - 1)
            if k < len(tomas) and tomas[k].get("enlace") == "sigue":
                _invalidar(reel, k)
        _clip(rid, tid).unlink(missing_ok=True)
        _final(rid).unlink(missing_ok=True)
        await _guardar(reel)
    return {"reel": _vista(reel)}


async def _lanzar_fotos(rid: str, solo: str = "", cual: str = "", correccion: str = "") -> Dict[str, Any]:
    reel = await _reel(rid)
    if not reel.get("tomas"):
        raise HTTPException(400, "Primero armá el plan.")
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal (FAL_KEY en Railway).")
    v = _vista(reel)
    n = 1 if solo else v["fotos_faltan"]
    if not n:
        raise HTTPException(400, "Ya están todas las fotos clave: si querés otra, tocá 'Otra foto' en esa toma.")
    costo = round(n * _costo_foto(), 2)
    await _cobrar(costo)
    jid = _uuid.uuid4().hex[:10]
    await _job_nuevo(jid, reel["pid"], "filmado_fotos", 90 * max(1, math.ceil(n / PARALELO)) + 60,
                     {"costo": costo, "titulo": "Fotos clave del reel", "rid": rid})
    async with _lock(rid):
        reel = await _reel(rid)
        reel["job"] = jid
        await _guardar(reel)
    _spawn(_procesar_fotos(jid, rid, CURRENT_SUB.get(), solo, cual, correccion))
    return {"job": jid, "costo": costo, "fotos": n}


@router.post(API + "/reel/{rid}/fotos")
async def api_fotos(rid: str) -> Dict[str, Any]:
    """Hace las fotos clave que faltan (con el motor de Fotos: la prenda sale exacta)."""
    return await _lanzar_fotos(rid)


@router.post(API + "/reel/{rid}/toma/{tid}/foto/{cual}/rehacer")
async def api_foto_rehacer(rid: str, tid: str, cual: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Otra foto clave para esa toma ("ini" o "fin"), con lo que querés corregir (va tal cual)."""
    if cual not in ("ini", "fin"):
        raise HTTPException(404, "No existe.")
    reel = await _reel(rid)
    if not any(t["id"] == tid for t in reel.get("tomas") or []):
        raise HTTPException(404, "Esa toma no existe.")
    return await _lanzar_fotos(rid, tid, cual, _texto(payload.get("correccion"), 400))


@router.post(API + "/reel/{rid}/toma/{tid}/foto/{cual}")
async def api_foto_subir(rid: str, tid: str, cual: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Una foto tuya (por ejemplo de Fotos) como foto clave: va tal cual, recortada a vertical."""
    if cual not in ("ini", "fin"):
        raise HTTPException(404, "No existe.")
    try:
        img = await asyncio.to_thread(_vertical, _strip_data_url(str(payload.get("foto") or "")))
    except Exception:
        raise HTTPException(400, "No pude leer esa foto.")
    async with _lock(rid):
        reel = await _reel(rid)
        i = next((k for k, x in enumerate(reel.get("tomas") or []) if x["id"] == tid), -1)
        if i < 0:
            raise HTTPException(404, "Esa toma no existe.")
        await kv.set(_k_foto(rid, tid, cual), base64.b64encode(img).decode())
        reel["tomas"][i].update({("foto_ok" if cual == "ini" else "final_ok"): True,
                                 ("foto_rev" if cual == "ini" else "final_rev"): {"subida": True}})
        _invalidar(reel, i)
        await _guardar(reel)
    return {"reel": _vista(reel)}


@router.post(API + "/reel/{rid}/toma/{tid}/foto/{cual}/cara")
async def api_foto_cara(rid: str, tid: str, cual: str) -> Dict[str, Any]:
    """Arreglar la cara de la foto clave (como en Reels: sólo la cabeza, el resto queda igual)."""
    if cual not in ("ini", "fin"):
        raise HTTPException(404, "No existe.")
    from arreglar_cara import arreglar_cara
    reel = await _reel(rid)
    foto = await kv.get(_k_foto(rid, tid, cual))
    if not foto:
        raise HTTPException(404, "Esa toma todavía no tiene foto clave.")
    doc = await _doc(reel["pid"])
    refs = await _refs_identidad(doc)
    if not refs:
        raise HTTPException(400, "Este personaje todavía no tiene retrato aprobado.")
    retrato = refs[0][1]
    cara = await cara_identidad(doc, retrato) or retrato
    settings = dict(await get_settings())
    precio = float(settings.get("precio_1k", 0.067) or 0.067)
    await _cobrar(precio)
    nueva, motor = await arreglar_cara(base64.b64decode(foto), cara, retrato, settings)
    await budget_record("filmado_foto", motor, precio, 1, note="filmado: arreglar la cara de una foto clave")
    async with _lock(rid):
        reel = await _reel(rid)
        i = next((k for k, x in enumerate(reel.get("tomas") or []) if x["id"] == tid), -1)
        if i < 0:
            raise HTTPException(404, "Esa toma no existe.")
        await kv.set(_k_foto(rid, tid, cual), base64.b64encode(nueva).decode())
        rev = reel["tomas"][i].get("foto_rev" if cual == "ini" else "final_rev") or {}
        rev["cara"] = motor
        reel["tomas"][i]["foto_rev" if cual == "ini" else "final_rev"] = rev
        reel["tomas"][i]["costo"] = round(float(reel["tomas"][i].get("costo") or 0) + precio, 2)
        reel["costo"] = round(float(reel.get("costo") or 0) + precio, 2)
        _invalidar(reel, i)
        await _guardar(reel)
    return {"reel": _vista(reel)}


@router.get(API + "/reel/{rid}/probador/{v}/{i}.jpg")
async def api_probador(rid: str, v: int, i: int):
    """El probador de un color (0 = frente, 1 = espalda)."""
    pb = await kv.get(_k_probador_reel(rid, v))
    if not isinstance(pb, list) or not (0 <= i < len(pb)):
        raise HTTPException(404, "Todavía no hay probador de ese color.")
    return Response(base64.b64decode(pb[i]), media_type="image/jpeg")


@router.get(API + "/reel/{rid}/toma/{tid}/foto/{cual}.jpg")
async def api_foto_ver(rid: str, tid: str, cual: str):
    foto = await kv.get(_k_foto(rid, tid, cual)) if cual in ("ini", "fin") else None
    if not foto:
        raise HTTPException(404, "Esa toma no tiene esa foto.")
    from fastapi.responses import Response
    return Response(base64.b64decode(foto), media_type="image/jpeg")


@router.post(API + "/reel/{rid}/toma/{tid}/inicio")
async def api_inicio(rid: str, tid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Foto de arranque del bloque que empieza en esta toma: el primer cuadro exacto del video
    (por ejemplo una foto de Fotos donde la prenda salió perfecta). Va tal cual, sin achicar."""
    foto = _strip_data_url(str(payload.get("foto") or ""))
    try:
        await asyncio.to_thread(_vertical, foto)
    except Exception:
        raise HTTPException(400, "No pude leer esa foto.")
    async with _lock(rid):
        reel = await _reel(rid)
        i = next((k for k, x in enumerate(reel.get("tomas") or []) if x["id"] == tid), -1)
        if i < 0:
            raise HTTPException(404, "Esa toma no existe.")
        await kv.set(_k_inicio(rid, tid), foto)
        reel["tomas"][i]["inicio"] = True
        _invalidar(reel, i)
        await _guardar(reel)
    return {"reel": _vista(reel)}


@router.delete(API + "/reel/{rid}/toma/{tid}/inicio")
async def api_inicio_borrar(rid: str, tid: str) -> Dict[str, Any]:
    async with _lock(rid):
        reel = await _reel(rid)
        i = next((k for k, x in enumerate(reel.get("tomas") or []) if x["id"] == tid), -1)
        if i < 0:
            raise HTTPException(404, "Esa toma no existe.")
        await kv.delete(_k_inicio(rid, tid))
        reel["tomas"][i]["inicio"] = False
        _invalidar(reel, i)
        await _guardar(reel)
    return {"reel": _vista(reel)}


@router.get(API + "/reel/{rid}/toma/{tid}/inicio.jpg")
async def api_inicio_ver(rid: str, tid: str):
    foto = await kv.get(_k_inicio(rid, tid))
    if not foto:
        raise HTTPException(404, "Esa toma no tiene foto de arranque.")
    from fastapi.responses import Response
    return Response(base64.b64decode(foto), media_type="image/jpeg")


@router.post(API + "/reel/{rid}/toma/{tid}/rehacer")
async def api_rehacer(rid: str, tid: str) -> Dict[str, Any]:
    """Borra lo filmado de ESA toma (las demás quedan) y la vuelve a filmar."""
    async with _lock(rid):
        reel = await _reel(rid)
        t = next((x for x in reel.get("tomas") or [] if x["id"] == tid), None)
        if not t:
            raise HTTPException(404, "Esa toma no existe.")
        _invalidar(reel, reel["tomas"].index(t))
        await _guardar(reel)
    return await api_filmar(rid)


@router.post(API + "/reel/{rid}/filmar")
async def api_filmar(rid: str, solo: Optional[str] = None) -> Dict[str, Any]:
    """Filma las tomas que faltan (las ya filmadas no se vuelven a pagar) y une el reel. Con
    `solo`, filma únicamente esa toma: para probar barato antes de pagar el reel entero."""
    reel = await _reel(rid)
    if solo and not any(t["id"] == solo for t in reel.get("tomas") or []):
        raise HTTPException(404, "Esa toma no existe.")
    if not reel.get("tomas"):
        raise HTTPException(400, "Primero armá el plan.")
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal (FAL_KEY en Railway).")
    avisos = [f"Toma {i + 1}: {_aviso_toma(reel, t)}" for i, t in enumerate(reel["tomas"]) if _aviso_toma(reel, t)]
    if avisos:
        raise HTTPException(400, " · ".join(avisos))
    seguidas, largas = 0, []
    for i, t in enumerate(reel["tomas"]):
        seguidas = seguidas + 1 if i and t.get("enlace") == "sigue" and not _por_bloques(reel) else 0
        if seguidas > MAX_SIGUE:
            largas.append(str(i + 1))
    if largas:
        raise HTTPException(400, f"Toma {', '.join(largas)}: como mucho {MAX_SIGUE + 1} tomas encadenadas 'sin cortar' "
                                 "(con más se pierden la cara y la prenda). Cambiá cómo entra (por ejemplo "
                                 "'Tapa la cámara con la mano') o rearmá el plan.")
    vacias = [str(i + 1) for i, t in enumerate(reel["tomas"]) if not (t.get("toma") or t.get("accion"))]
    if vacias:
        raise HTTPException(400, f"Falta qué hace en la toma {', '.join(vacias)}.")
    if _fotos_clave(reel):
        pend = [i for i, t in enumerate(reel["tomas"]) if not _clip(rid, t["id"]).exists() and (not solo or t["id"] == solo)]
        sin = [str(i + 1) for i in pend if not reel["tomas"][i].get("foto_ok")
               or (_quiere_final(reel["tomas"][i]) and not reel["tomas"][i].get("final_ok"))]
        if sin:
            raise HTTPException(400, f"Primero hacé las fotos clave (falta{'n' if len(sin) > 1 else ''} la de la "
                                     f"toma {', '.join(sin)}): son las que dejan la prenda exacta.")
    v = _vista(reel)
    if solo:
        t = next(x for x in v["tomas"] if x["id"] == solo)
        if t["filmada"]:
            raise HTTPException(400, "Esa toma ya está filmada: para volver a filmarla tocá 'Rehacer'.")
        if v["por_bloques"]:
            # Se prueba el BLOQUE de esa toma (una sola filmación con varias tomas adentro).
            pend = [i for i, x in enumerate(v["tomas"]) if not x["filmada"]]
            g = next((g for g in _bloques(reel, pend) if any(v["tomas"][i]["id"] == solo for i in g)), [])
            costo, faltan = round(sum(v["tomas"][i]["costo_est"] for i in g), 2), len(g)
        else:
            costo, faltan = t["costo_est"], 1
    else:
        costo, faltan = v["costo_falta"], sum(1 for t in v["tomas"] if not t["filmada"])
    await _cobrar(costo)
    jid = _uuid.uuid4().hex[:10]
    await _job_nuevo(jid, reel["pid"], "filmado", 240 * max(1, math.ceil(faltan / PARALELO)) + 60,
                     {"costo": costo, "titulo": "Reel filmado de cero", "rid": rid})
    async with _lock(rid):
        reel = await _reel(rid)
        reel["job"] = jid
        await _guardar(reel)
    _spawn(_procesar(jid, rid, CURRENT_SUB.get(), solo or ""))
    return {"job": jid, "costo": costo, "tomas": faltan}


@router.get(API + "/job/{jid}")
async def api_job(jid: str) -> Dict[str, Any]:
    j = await kv.get(_k_job(jid))
    if not isinstance(j, dict):
        raise HTTPException(404, "Ese trabajo no existe.")
    j = await _revisar_job(j)
    return {k: j.get(k) for k in ("id", "estado", "paso", "error", "costo", "drive", "rid")}


@router.get(API + "/reels")
async def api_reels() -> Dict[str, Any]:
    out = []
    for rid in (await kv.get(_k_reels())) or []:
        r = await kv.get(_k_reel(rid))
        if isinstance(r, dict):
            out.append({"id": rid, "titulo": r.get("titulo") or (r.get("info") or {}).get("producto") or "Reel",
                        "tomas": len(r.get("tomas") or []), "listo": _final(rid).exists(),
                        "costo": r.get("costo"), "creado": r.get("creado"), "ts": r.get("ts")})
    return {"reels": out}


@router.get(API + "/reel/{rid}/mp4")
async def api_mp4(rid: str):
    await _reel(rid)
    if not _final(rid).exists():
        raise HTTPException(404, "Ese reel todavía no está filmado.")
    return FileResponse(str(_final(rid)), media_type="video/mp4", filename=f"reel-{rid}.mp4")


@router.get(API + "/reel/{rid}/toma/{tid}/mp4")
async def api_toma_mp4(rid: str, tid: str):
    reel = await _reel(rid)
    if not any(t["id"] == tid for t in reel.get("tomas") or []) or not _clip(rid, tid).exists():
        raise HTTPException(404, "Esa toma todavía no está filmada.")
    return FileResponse(str(_clip(rid, tid)), media_type="video/mp4", filename=f"toma-{tid}.mp4")


PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Filmado de cero · Studio Luma</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;--rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f}
  *{box-sizing:border-box} body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:16px;line-height:1.55}
  main{max-width:1080px;margin:0 auto;padding:16px} .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:16px}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:22px;margin:0 0 6px} h3{font-size:15px;margin:0;font-weight:500} .hint{color:var(--ink-soft);font-size:13px;margin:4px 0 8px}
  label{display:block;font-size:13px;color:var(--ink-soft);margin:10px 0 4px}
  input,select,textarea{width:100%;background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit;font-size:15px}
  input[type=checkbox]{width:auto;margin-right:8px} .chk{display:flex;align-items:center;font-size:14px;color:var(--ink);margin:6px 0}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:9px 16px;font:inherit;font-size:14px;cursor:pointer}
  button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500} button:disabled{opacity:.5} button.chip{padding:5px 12px;font-size:13px;margin:4px 6px 0 0}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px} .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px} @media(max-width:640px){.row,.row3{grid-template-columns:1fr}}
  video{width:100%;max-width:360px;border-radius:14px;background:#000} .thumbs img{width:64px;height:64px;object-fit:cover;border-radius:8px;margin:6px 6px 0 0}
  .toma{border:1px solid var(--line);border-radius:14px;padding:12px;margin:10px 0;background:var(--card-2)} .toma textarea,.toma select,.toma input{background:var(--card)}
  .toma .cab{display:flex;gap:10px;align-items:center;flex-wrap:wrap} .toma .cab select{width:auto} .toma video{max-width:220px;margin-top:8px}
  .mal{color:var(--bad)} .bien{color:var(--ok)} .preg{margin:12px 0} details summary{cursor:pointer;color:var(--ink-soft);font-size:14px;margin-top:10px}
  .vids{display:flex;gap:10px;flex-wrap:wrap}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid var(--ink-soft);border-top-color:var(--rose);border-radius:50%;animation:g 1s linear infinite;vertical-align:-1px;margin-right:6px}@keyframes g{to{transform:rotate(360deg)}}
</style></head><body><main>
<div class="card">
  <h2>Reel filmado de cero <small style="font-family:Jost,sans-serif;font-size:12px;color:var(--ink-soft);letter-spacing:.08em">V%%VERSION%%</small></h2>
  <p class="hint">Un reel completo para vender la prenda: contás qué querés, <b>Claude entiende qué vendemos</b> (con sus fichas de cada tipo de prenda y de cada lugar), te pregunta lo que le falta y arma el plan como un director. Después, <b>cada toma arranca de una foto clave hecha con el motor de Fotos</b> (la prenda exacta, revisada por Claude, la ves antes de pagar video) y Kling la pone en movimiento con su voz de Reels de fondo. Las tomas que no gusten se rehacen solas, sin pagar las demás.</p>
  <p class="hint mal" id="aviso_claude" style="display:none">Claude no está disponible (falta ANTHROPIC_API_KEY): el plan sale de una plantilla que podés editar.</p>
  <h3>1 · Tu reel</h3>
  <label>Cómo cuenta el reel</label><select id="modo"></select>
  <label>⏱️ Ritmo de las tomas</label><select id="ritmo"></select>
  <p class="hint"><b>Una toma, segundo a segundo</b> (recomendado): el reel entero sale de UN video de Seedance 2.5 de hasta 30 s, con un guion que dice
  qué pasa en cada segundo: cada 1 a 3 s cambia el plano y el ángulo (detalles de la tela, desde abajo, por sobre el hombro, en el espejo…), siempre la
  misma chica, la misma prenda y el mismo lugar. Su voz va corrida encima. ~US$0,47 por segundo (un reel de 30 s ≈ US$14).
  <b>Por segundo</b>: lo mismo pero cada tramo es un video aparte que arranca de su foto clave (~US$0,45 por tramo).</p>
  <div class="row"><div><label>Modelo (personaje)</label><select id="pid"></select></div>
  <div><label>Duración del reel</label><select id="duracion"></select></div></div>
  <label>La prenda: hasta 3 fotos por color (frente, espalda y detalle; con la espalda, cuando gira la copia bien). Con más de un color, Claude arma cambios de color tapando la cámara.</label>
  <div class="row"><div><label>👗 Usar una prenda de Mis prendas <a href="/referencias#prendas" target="_blank" style="color:var(--rose-deep)">administrar</a></label>
    <select id="ficha"><option value="">— No: subo las fotos acá —</option></select></div>
    <div><p class="hint" id="ficha_info" style="margin-top:28px">Con una prenda de Mis prendas van sus colores y el probador que aprobaste ahí (no se vuelve a pagar).</p></div></div>
  <div id="variantes"></div><p><button id="otro_color">＋ Otro color</button></p>
  <div class="row"><div><label>La prenda</label><select id="puesta"><option value="si">La tiene puesta</option><option value="no">La muestra en la mano (vestida de entrecasa)</option></select></div>
  <div><label>Dónde</label><select id="lugar"></select></div></div>
  <div class="row"><div><label>📍 Mi lugar (fotos reales o generadas) <a href="/referencias" target="_blank" style="color:var(--rose-deep)">administrar</a></label>
    <select id="lugar_ref"><option value="">— Ninguno: el lugar sale del texto —</option></select></div>
  <div><label>🧍 Probador: su cuerpo con la prenda, sin cara, fondo gris</label><select id="probador">
    <option value="si">Sí (más fiel en lencería · ~US$0,21 por color, una sola vez)</option><option value="no">No</option></select></div></div>
  <p class="hint">Con <b>Mi lugar</b> todas las tomas pasan en tu lugar real, con la misma luz. El <b>probador</b> le muestra al motor la prenda ya puesta en su cuerpo (frente y espalda) en vez de en un maniquí: el calce y los detalles salen mucho más fieles. Su cuerpo de frente va <b>sin cara</b>, así la identidad la da sólo la cara.</p>
  <label>Qué querés mostrar</label><div id="mostrar"></div>
  <div class="row3"><div><label>Producto</label><input id="i_producto" placeholder="Conjunto Encaje Rojo"></div><div><label>Precio</label><input id="i_precio" placeholder="$ 25.000"></div><div><label>Talles</label><input id="i_talles" placeholder="S a XL"></div></div>
  <div class="row"><div><label>Colores</label><input id="i_colores" placeholder="rojo, negro, nude"></div><div><label>Promo</label><input id="i_promo" placeholder="envío gratis, 3 cuotas…"></div></div>
  <label>Algo más que quieras que sepa Claude</label><textarea id="i_notas" rows="2"></textarea>
  <details><summary>Voz, filtro y motor</summary>
  <div class="row3"><div><label>Voz</label><select id="voz"></select></div><div><label>Tono</label><select id="tono"></select></div><div><label>Energía</label><select id="energia"></select></div></div>
  <div class="row3"><div><label>Aire de micrófono</label><select id="mic"><option value="si">Sí (suena a celular)</option><option value="no">No, voz limpia</option></select></div>
  <div><label>Motor</label><select id="motor"></select></div>
  <div><label>Cómo filma</label><select id="bloques"><option value="no">Toma por toma (mejor prenda, recomendado)</option><option value="si">En bloques de 15 s (prueba: menos detalle de la prenda)</option></select></div></div>
  <label>Cómo arranca cada toma</label><select id="fotos_clave"><option value="si">Desde una foto clave hecha con el motor de Fotos (prenda exacta, recomendado)</option><option value="no">De cero (Kling dibuja la prenda)</option></select>
  <div class="row"><div><label>Filtro (los de Reels)</label><select id="look"></select></div><div><label>Cámara</label><select id="camara"></select></div></div>
  </details>
  <p style="margin-top:14px"><button class="go" id="empezar">💬 Que Claude me pregunte</button></p>
  <p class="hint" id="estado1"></p>
</div>
<div class="card" id="c_preg" style="display:none"><h3>2 · Claude te pregunta</h3><div id="analisis" class="hint"></div><p class="hint" id="resumen"></p><div id="preguntas"></div>
  <p><button class="go" id="plan">🎬 Armar el plan</button></p><p class="hint" id="estado2"></p></div>
<div class="card" id="c_plan" style="display:none"><h3>3 · El plan <span id="titulo" class="hint"></span></h3>
  <p id="concepto"></p><p class="hint" id="continuidad"></p>
  <p class="hint">Todo se puede editar: lo que escribís va tal cual. Si cambiás una toma ya filmada, esa se vuelve a filmar (las demás no). <b>Primero las fotos clave</b> (centavos cada una): cada toma arranca de la suya, con la prenda exacta. Cuando te gusten todas, filmás.</p>
  <div id="probadores" class="thumbs"></div>
  <div id="guion_box" class="toma" style="display:none"><h3>🎙️ Su voz, corrida encima de todos los cortes</h3>
    <p class="hint">En el ritmo por segundo la voz no se corta en cada toma: es una sola narración (así suena natural). Cambiarla no vuelve a filmar nada: se graba de nuevo y se vuelve a unir.</p>
    <textarea id="guion" rows="4"></textarea><div class="hint" id="guion_info"></div></div>
  <div id="tomas"></div><p><button id="agregar">＋ Agregar una toma al final</button></p>
  <div class="toma" id="ed_reel"><h3>✂️ Edición (sobre el video ya filmado: se cambia y se vuelve a unir, sin pagar)</h3>
    <div id="ed_checks"></div>
    <div class="row"><div><label>✨ Subtítulos animados</label><select id="mo_subs" class="mo"></select></div>
    <div><label>✨ Tipografía</label><select id="mo_fuente" class="mo"></select></div></div>
    <div class="row"><div><label>✨ Efecto en los cortes</label><select id="mo_corte" class="mo"></select></div>
    <div><label>✨ Tu logo <span id="mo_logo_nota" style="font-weight:400"></span></label><select id="mo_logo" class="mo"></select></div></div>
    <div class="row"><div><label>Cartel final: título</label><input id="cierre_titulo" placeholder="$ 32.900"></div>
    <div><label>Cartel final: la acción</label><input id="cierre_linea" placeholder="Escribinos por DM"></div></div></div>
  <p class="hint" id="totales"></p><p><button class="go" id="hacer_fotos" style="display:none">📸 Hacer las fotos clave</button></p><button class="go" id="filmar">🎥 Filmar el reel</button><p class="hint" id="estado3"></p></div>
<div class="card" id="c_final" style="display:none"><h3>El reel</h3><div id="final"></div></div>
<div class="card"><h2>Reels anteriores</h2><div id="lista"></div></div>
</main>
<script>
const API = "%%API%%"; let CFG = {}, MIS_PRENDAS = [], VARS = [{nombre: "", fotos: []}], REEL = null, SIGUIENDO = null;
const $ = s => document.querySelector(s);
const esc = t => String(t == null ? "" : t).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
async function api(p, o){ const r = await fetch(API + p, Object.assign({headers: {"Content-Type": "application/json"}}, o || {})); const d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.detail || ("HTTP " + r.status)); return d; }
const leer = f => new Promise((ok, mal) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = mal; r.readAsDataURL(f); });
const guardarRid = rid => { try{ localStorage.setItem("filmado_rid", rid); }catch(e){} };
function ajustes(){ return {pid: $("#pid").value, duracion: +$("#duracion").value, puesta: $("#puesta").value === "si", lugar: $("#lugar").value,
  mostrar: Array.from(document.querySelectorAll("#mostrar input:checked")).map(x => x.value),
  info: {producto: $("#i_producto").value, precio: $("#i_precio").value, talles: $("#i_talles").value, colores: $("#i_colores").value, promo: $("#i_promo").value, notas: $("#i_notas").value},
  voz: $("#voz").value, tono: $("#tono").value, energia: $("#energia").value, mic: $("#mic").value === "si", modo: $("#modo").value, bloques: $("#bloques").value === "si", fotos_clave: $("#fotos_clave").value === "si", motor: $("#motor").value, look: $("#look").value, camara: $("#camara").value, ritmo: $("#ritmo").value,
  lugar_ref: $("#lugar_ref").value, probador: $("#probador").value !== "no"}; }
function cargarAjustes(r){ const set = (k, v) => { if(v != null && $(k)) $(k).value = v; };
  set("#duracion", r.duracion); set("#puesta", r.puesta ? "si" : "no"); set("#lugar", r.lugar); set("#voz", r.voz); set("#tono", r.tono); set("#energia", r.energia);
  set("#mic", r.mic === false ? "no" : "si"); set("#modo", r.modo); set("#bloques", r.bloques ? "si" : "no"); set("#fotos_clave", r.fotos_clave === false ? "no" : "si"); set("#motor", r.motor); set("#look", r.look); set("#camara", r.camara); set("#pid", r.pid);
  set("#lugar_ref", r.lugar_ref || ""); set("#probador", r.probador === false ? "no" : "si"); set("#ritmo", r.ritmo || "normal");
  const inf = r.info || {}; ["producto", "precio", "talles", "colores", "promo", "notas"].forEach(k => set("#i_" + k, inf[k] || ""));
  document.querySelectorAll("#mostrar input").forEach(x => x.checked = (r.mostrar || []).includes(x.value)); }
function notaFoto(rv){ if(!rv) return ""; if(rv.subida) return '<span class="hint">tu foto</span>';
  return `<span class="${rv.puntaje >= 8 ? "bien" : rv.puntaje <= 5 ? "mal" : ""}">Claude: ${esc(rv.puntaje)}/10</span>${(rv.fallas || []).length ? `<ul style="margin:4px 0 0 18px;padding:0;font-size:13px">${rv.fallas.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}${rv.cara ? ' <span class="hint">· cara arreglada</span>' : ""}`; }
function fotoClave(r, t, c){ const ini = c === "ini", ok = ini ? t.foto_ok : t.final_ok, rv = ini ? t.foto_rev : t.final_rev;
  const txt = ini ? t.foto_es : t.final_es;
  if(!ini && !t.quiere_final && !txt) return `<details style="margin-top:6px"><summary>＋ Foto final (para un giro: cómo termina, de espaldas)</summary><textarea data-k="final_es" rows="2" placeholder="De espaldas, se ve la parte de atrás del conjunto…"></textarea></details>`;
  return `<div style="margin-top:8px;padding:8px;border:1px dashed var(--line);border-radius:10px"><label style="margin-top:0">${ini ? "📸 Foto de arranque (el primer cuadro: qué se ve)" : "📸 Foto final (cómo termina el giro)"}</label>
    <textarea data-k="${ini ? "foto_es" : "final_es"}" rows="2">${esc(txt)}</textarea>
    ${ok ? `<div style="display:flex;gap:10px;align-items:flex-start;margin-top:6px"><img src="${API}/reel/${r.id}/toma/${t.id}/foto/${c}.jpg?v=${Date.now()}" style="width:96px;height:171px;object-fit:cover;border-radius:8px"><div style="font-size:14px">${notaFoto(rv)}</div></div>` : '<div class="hint">Todavía sin foto.</div>'}
    <input data-corr="${c}" placeholder="Qué corregir (opcional, va tal cual)" style="margin-top:6px">
    <p style="margin:6px 0 0"><button data-fa="otra" data-c="${c}">${ok ? "↻ Otra foto" : "📸 Hacer esta foto"} (~US$${CFG.costo_foto})</button> ${ok && t.tipo !== "producto" ? `<button data-fa="cara" data-c="${c}">🙂 Arreglar la cara</button> ` : ""}<label style="display:inline-block;margin:0"><input type="file" accept="image/*" data-fs="${c}" style="display:none"><span class="hint" style="cursor:pointer;text-decoration:underline">⬆ Subir la mía</span></label></p></div>`; }
function revision(t){ let h = "";
  if(t.revision){ const f = (t.revision.fallas || []).map(x => `<li>${esc(x)}</li>`).join("");
    h += `<div class="${t.revision.puntaje >= 8 ? "bien" : t.revision.puntaje <= 5 ? "mal" : ""}"><b>Claude: ${esc(t.revision.puntaje)}/10${t.revision.puntaje <= 5 ? " · conviene rehacerla" : ""}</b>${f ? `<ul style="margin:4px 0 0 18px;padding:0">${f}</ul>` : " · no vio fallas"}</div>`; }
  if(t.lipsync && t.lipsync !== "ok") h += `<div class="mal">Lip-sync ${esc(t.lipsync)}</div>`;
  if(t.error && !t.filmada) h += `<div class="mal">Falló: ${esc(t.error)}</div>`;
  return h; }
function pintar(){ const r = REEL; if(!r) return; $("#empezar").textContent = "💬 Empezar un reel nuevo (Claude pregunta de nuevo)";
  $("#c_preg").style.display = (r.preguntas || []).length ? "" : "none";
  $("#resumen").textContent = r.resumen ? "Lo que ve Claude: " + r.resumen : "";
  const a = r.analisis; $("#analisis").innerHTML = a ? `<b>Qué vendemos:</b> ${esc(a.que_es)}${(a.puntos_fuertes || []).length ? `<br><b>Lo que la vende:</b> ${a.puntos_fuertes.map(esc).join(" · ")}` : ""}${a.para_quien ? `<br><b>Para quién:</b> ${esc(a.para_quien)}` : ""}` : "";
  $("#concepto").innerHTML = r.concepto ? `<b>La idea:</b> ${esc(r.concepto)}` : ""; $("#continuidad").textContent = r.continuidad ? "Igual en todas las tomas: " + r.continuidad : "";
  $("#preguntas").innerHTML = (r.preguntas || []).map((q, i) => `<div class="preg"><b>${esc(q.pregunta)}</b><div>${(q.opciones || []).map(o => `<button class="chip" data-q="${i}" data-o="${esc(o)}">${esc(o)}</button>`).join("")}</div><input class="resp" data-q="${i}" value="${esc(q.respuesta || "")}" placeholder="Tu respuesta (o tocá una opción)"></div>`).join("");
  document.querySelectorAll(".chip").forEach(b => b.onclick = () => { document.querySelector(`.resp[data-q="${b.dataset.q}"]`).value = b.dataset.o; });
  $("#plan").textContent = (r.tomas || []).length ? "🎬 Rearmar el plan (reemplaza las tomas)" : "🎬 Armar el plan";
  $("#c_plan").style.display = (r.tomas || []).length ? "" : "none"; $("#titulo").textContent = r.titulo ? "· " + r.titulo : "";
  $("#guion_box").style.display = r.por_segundo ? "" : "none";
  if(r.por_segundo){ if(document.activeElement !== $("#guion")) $("#guion").value = r.guion || "";
    const pal = (r.guion || "").trim().split(/\s+/).filter(Boolean).length, dur = (r.tomas || []).reduce((a, t) => a + (+t.seg_est || 0), 0);
    $("#guion_info").textContent = `${pal} palabras ≈ ${Math.round(pal / 2.6)} s de voz · las tomas suman ~${Math.round(dur)} s` + (pal / 2.6 > dur + 1 ? " · ⚠ la voz es más larga: acortala o sumá tomas" : ""); }
  const ph = Object.entries(r.probador_hecho || {}).filter(([, ok]) => ok);
  $("#probadores").innerHTML = ph.length ? `<span class="hint">🧍 Probador (su cuerpo con la prenda): </span>` + ph.map(([v]) =>
    [0, 1, 2].map(i => `<a href="${API}/reel/${r.id}/probador/${v}/${i}.jpg" target="_blank"><img src="${API}/reel/${r.id}/probador/${v}/${i}.jpg?t=${Date.now()}" title="Color ${+v + 1} · ${["frente", "espalda", "3/4"][i]}" onerror="this.parentNode.remove()" style="width:54px;height:72px"></a>`).join("")).join("") : "";
  const tipos = Object.fromEntries(Object.entries(CFG.tipos).filter(([k]) => k !== "habla" || r.modo === "habla"));
  const colores = (r.variantes || [{nombre: ""}]).map((v, k) => v.nombre || `Color ${k + 1}`);
  const sel = (k, obj, v) => `<select data-k="${k}">${Object.entries(obj).map(([kk, vv]) => `<option value="${kk}" ${kk === v ? "selected" : ""}>${esc(vv)}</option>`).join("")}</select>`;
  const bloque = t => `<div class="card" style="margin:16px 0 4px;padding:12px;background:var(--card)"><b>Bloque ${t.bloque}</b> <span class="hint">· ${t.bloque_tomas} toma${t.bloque_tomas > 1 ? "s" : ""} · ~${t.bloque_seg} s · ${r.por_toma ? "UNA toma de Seedance 2.5 con su línea de tiempo (cortes adentro)" : "una sola filmación de Kling (misma cara, prenda y cuarto)"}</span>
    ${r.por_toma ? "" : `<div class="hint" style="margin-top:6px">Foto de arranque (opcional): una foto de Fotos donde la prenda salió perfecta; el video arranca exactamente ahí.</div>
    ${t.inicio ? `<img src="${API}/reel/${r.id}/toma/${t.id}/inicio.jpg?v=${Date.now()}" style="width:72px;height:128px;object-fit:cover;border-radius:8px;margin:6px 6px 0 0"><button data-bx="${t.id}">Quitar la foto</button>` : `<input type="file" accept="image/*" data-bi="${t.id}">`}`}
    ${t.bloque_costo ? `<p style="margin:8px 0 0"><button data-bp="${t.id}">🎥 Probar este bloque (US$${t.bloque_costo})</button></p>` : ""}</div>`;
  $("#tomas").innerHTML = (r.tomas || []).map((t, i) => `${r.por_bloques && t.inicia_bloque ? bloque(t) : ""}<div class="toma" data-t="${t.id}">
    <div class="cab"><h3>Toma ${i + 1}</h3>${sel("tipo", tipos, t.tipo)}
    <span class="hint">~${t.seg_est} s · US$${t.costo_est}${t.filmada ? ' · <span class="bien">filmada</span>' : ""}</span></div>
    <div class="row"><div><label>Plano</label>${sel("plano", CFG.planos, t.plano)}</div><div><label>Ángulo</label>${sel("angulo", CFG.angulos, t.angulo || "ojos")}</div></div>
    <div class="row"><div><label>Cámara</label>${sel("movimiento", CFG.movimientos, t.movimiento)}</div><div></div></div>
    <div class="row">${i ? `<div><label>Cómo entra desde la anterior</label>${sel("enlace", CFG.enlaces, t.enlace || "corte")}</div>` : ""}${colores.length > 1 ? `<div><label>Color</label>${sel("variante", Object.fromEntries(colores.map((c, k) => [String(k), c])), String(t.variante || 0))}</div>` : ""}</div>
    ${r.por_segundo && !t.dice ? "" : `<label>${t.tipo === "habla" ? "Qué dice a cámara" : "Su voz de fondo (vacío = sin voz)"}</label><textarea data-k="dice" rows="2">${esc(t.dice)}</textarea>`}${t.aviso ? `<div class="mal hint">${esc(t.aviso)}</div>` : ""}
    <label>${r.fotos_clave_activo ? "Qué se mueve (desde la foto)" : "Qué hace"}</label><textarea data-k="accion" rows="2">${esc(t.accion)}</textarea>
    ${r.fotos_clave_activo ? fotoClave(r, t, "ini") + (t.tipo !== "producto" ? fotoClave(r, t, "fin") : "") : ""}
    <div class="row" style="margin-top:6px"><div><label>✂️ Cartel flotante (vacío = sin cartel)</label><input data-k="cartel" value="${esc(t.cartel || "")}" placeholder="Talles 42 al 48"></div>
    <div><label>✂️ Zoom</label>${sel("zoom", CFG.zooms, t.zoom || "no")}</div></div>
    ${t.tipo !== "producto" ? `<label class="chk"><input type="checkbox" data-fp="1" ${t.foto_producto ? "checked" : ""}>✂️ Foto del producto en una esquina</label>` : ""}
    ${!t.dice ? (r.por_toma ? `<label>Segundos del tramo</label><input data-k="seg" type="number" min="1" max="3" step="1" value="${t.seg || 2}" style="width:90px">`
      : r.por_segundo ? `<label>Segundos en el reel</label><input data-k="seg" type="number" min="1.2" max="3" step="0.1" value="${t.seg || 2}" style="width:90px">`
      : `<label>Segundos</label><input data-k="seg" type="number" min="2" max="10" step="0.5" value="${t.seg || 4}" style="width:90px">`) : ""}
    ${t.filmada ? `<div><video src="${API}/reel/${r.id}/toma/${t.id}/mp4?v=${encodeURIComponent(t.clip_seg || "")}${Date.now()}" controls playsinline preload="metadata"></video></div>` : ""}
    ${revision(t)}
    <p style="margin:8px 0 0">${t.filmada ? `<button data-a="rehacer">↻ Rehacer esta toma (US$${t.costo_est})</button> ` : (r.por_bloques ? "" : `<button data-a="probar">🎥 Probar sólo esta toma (US$${t.costo_est})</button> `)}<button data-a="despues">＋ Toma después</button> <button data-a="borrar">🗑</button></p></div>`).join("");
  document.querySelectorAll(".toma").forEach(el => { const tid = el.dataset.t;
    el.querySelectorAll("[data-k]").forEach(c => c.onchange = async () => { try{ const b = {}; b[c.dataset.k] = (c.dataset.k === "seg" || c.dataset.k === "variante") ? +c.value : c.value; REEL = (await api(`/reel/${REEL.id}/toma/${tid}`, {method: "PUT", body: JSON.stringify(b)})).reel; pintar(); }catch(e){ $("#estado3").textContent = "Falló: " + e.message; } });
    el.querySelectorAll("[data-fp]").forEach(x => x.onchange = async () => { try{ REEL = (await api(`/reel/${REEL.id}/toma/${tid}`, {method: "PUT", body: JSON.stringify({foto_producto: x.checked})})).reel; pintar(); }catch(e){ $("#estado3").textContent = "Falló: " + e.message; } });
    el.querySelectorAll("[data-fa]").forEach(b => b.onclick = async () => { try{ const c = b.dataset.c;
      if(b.dataset.fa === "otra"){ const corr = (el.querySelector(`[data-corr="${c}"]`) || {}).value || ""; const d = await api(`/reel/${REEL.id}/toma/${tid}/foto/${c}/rehacer`, {method: "POST", body: JSON.stringify({correccion: corr})}); seguir(d.job); }
      if(b.dataset.fa === "cara"){ b.disabled = true; b.textContent = "Arreglando la cara…"; REEL = (await api(`/reel/${REEL.id}/toma/${tid}/foto/${c}/cara`, {method: "POST"})).reel; pintar(); }
    }catch(e){ $("#estado3").textContent = "Falló: " + e.message; b.disabled = false; } });
    el.querySelectorAll("[data-fs]").forEach(x => x.onchange = async e => { try{ const f = e.target.files[0]; if(!f) return;
      REEL = (await api(`/reel/${REEL.id}/toma/${tid}/foto/${x.dataset.fs}`, {method: "POST", body: JSON.stringify({foto: await leer(f)})})).reel; pintar(); }catch(er){ $("#estado3").textContent = "Falló: " + er.message; } });
    el.querySelectorAll("[data-a]").forEach(b => b.onclick = async () => { try{
      if(b.dataset.a === "borrar"){ if(!confirm("¿Borrar esta toma?")) return; REEL = (await api(`/reel/${REEL.id}/toma/${tid}`, {method: "DELETE"})).reel; pintar(); }
      if(b.dataset.a === "despues"){ REEL = (await api(`/reel/${REEL.id}/toma`, {method: "POST", body: JSON.stringify({despues: tid})})).reel; pintar(); }
      if(b.dataset.a === "rehacer"){ const d = await api(`/reel/${REEL.id}/toma/${tid}/rehacer`, {method: "POST"}); seguir(d.job); }
      if(b.dataset.a === "probar"){ const d = await api(`/reel/${REEL.id}/filmar?solo=${encodeURIComponent(tid)}`, {method: "POST"}); seguir(d.job); }
    }catch(e){ $("#estado3").textContent = "Falló: " + e.message; } }); });
  document.querySelectorAll("[data-bi]").forEach(x => x.onchange = async e => { try{ const f = e.target.files[0]; if(!f) return;
    REEL = (await api(`/reel/${REEL.id}/toma/${x.dataset.bi}/inicio`, {method: "POST", body: JSON.stringify({foto: await leer(f)})})).reel; pintar(); }catch(er){ $("#estado3").textContent = "Falló: " + er.message; } });
  document.querySelectorAll("[data-bx]").forEach(x => x.onclick = async () => { try{ REEL = (await api(`/reel/${REEL.id}/toma/${x.dataset.bx}/inicio`, {method: "DELETE"})).reel; pintar(); }catch(er){ $("#estado3").textContent = "Falló: " + er.message; } });
  document.querySelectorAll("[data-bp]").forEach(x => x.onclick = async () => { try{ const d = await api(`/reel/${REEL.id}/filmar?solo=${encodeURIComponent(x.dataset.bp)}`, {method: "POST"}); seguir(d.job); }catch(er){ $("#estado3").textContent = "Falló: " + er.message; } });
  const falta = (r.tomas || []).filter(t => !t.filmada).length;
  $("#totales").textContent = `${(r.tomas || []).length} tomas · ~${r.seg_total} s en total · ` + (falta ? `filmar ${falta === r.tomas.length ? "todo" : "lo que falta"} cuesta ~US$${r.costo_falta}` : "todas filmadas") + (r.costo ? ` · gastado hasta ahora: US$${r.costo}` : "");
  $("#ed_checks").innerHTML = Object.entries(CFG.edicion).map(([k, v]) => `<label class="chk"><input type="checkbox" data-ed="${k}" ${(r.edicion || {})[k] ? "checked" : ""}>${esc(v)}</label>`).join("");
  document.querySelectorAll("[data-ed]").forEach(x => x.onchange = () => guardarEdicion());
  const mo = r.motion || {}, llenar = (id, obj, val) => { if(!$(id).options.length) $(id).innerHTML = Object.entries(obj).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join(""); $(id).value = val; };
  llenar("#mo_subs", CFG.motion.subs, mo.subs || "clasico"); llenar("#mo_fuente", CFG.motion.fuentes, mo.fuente || "moderna");
  llenar("#mo_corte", CFG.motion.cortes, mo.corte || "ninguno");
  llenar("#mo_logo", Object.fromEntries([["no", "Sin logo"], ...Object.entries(CFG.motion.esquinas).map(([k, v]) => [k, "Sí · " + v])]), mo.logo ? (mo.esquina || "abajo_der") : "no");
  $("#mo_logo_nota").textContent = CFG.logo ? "" : "(se sube en Comerciales → ✨ Motion)";
  document.querySelectorAll(".mo").forEach(x => x.onchange = () => guardarEdicion());
  if(document.activeElement !== $("#cierre_titulo")) $("#cierre_titulo").value = (r.cierre || {}).titulo || "";
  if(document.activeElement !== $("#cierre_linea")) $("#cierre_linea").value = (r.cierre || {}).linea || "";
  $("#hacer_fotos").style.display = r.fotos_faltan ? "" : "none";
  $("#hacer_fotos").textContent = `📸 Hacer las fotos clave (${r.fotos_faltan} · ~US$${r.costo_fotos})`;
  $("#filmar").disabled = !!r.fotos_faltan || !!SIGUIENDO; $("#filmar").title = r.fotos_faltan ? "Primero las fotos clave" : "";
  $("#filmar").textContent = falta === (r.tomas || []).length ? `🎥 Filmar el reel (~US$${r.costo_falta})` : falta ? `🎥 Filmar lo que falta (~US$${r.costo_falta})` : "🎞 Volver a unir el reel";
  $("#c_final").style.display = r.listo ? "" : "none";
  if(r.listo) $("#final").innerHTML = `<video src="${API}/reel/${r.id}/mp4?v=${Date.now()}" controls playsinline></video><p><a href="${API}/reel/${r.id}/mp4" download style="color:var(--rose)">⬇ Bajar el reel</a>${r.drive ? ` · <a href="${esc(r.drive)}" target="_blank" style="color:var(--rose)">Drive</a>` : ""}${r.seg_final ? ` · ${r.seg_final} s` : ""}</p>`;
}
async function seguir(jid){ if(SIGUIENDO === jid) return; SIGUIENDO = jid; $("#filmar").disabled = true;
  try{ for(;;){ const j = await api("/job/" + jid);
      if(j.estado === "listo"){ $("#estado3").textContent = "Listo."; break; }
      if(j.estado === "error"){ $("#estado3").innerHTML = `<span class="mal">${esc(j.error || "Falló")}</span>`; break; }
      $("#estado3").innerHTML = `<span class="spin"></span>${esc(j.paso || "En cola…")}`;
      if(REEL){ try{ REEL = (await api("/reel/" + REEL.id)).reel; pintar(); }catch(e){} }
      await new Promise(res => setTimeout(res, 6000)); } }
  catch(e){ $("#estado3").textContent = "Falló: " + e.message; }
  SIGUIENDO = null; $("#filmar").disabled = false; if(REEL){ REEL = (await api("/reel/" + REEL.id)).reel; pintar(); } lista(); }
async function abrir(rid){ REEL = (await api("/reel/" + rid)).reel; guardarRid(rid); cargarAjustes(REEL); pintar();
  if(REEL.job){ try{ const j = await api("/job/" + REEL.job); if(j.estado === "generando" || j.estado === "en_cola") seguir(REEL.job); }catch(e){} } }
async function lista(){ const l = (await api("/reels")).reels;
  $("#lista").innerHTML = l.length ? l.map(v => `<p><button data-r="${v.id}">Abrir</button> ${esc(v.titulo)} · ${v.tomas} tomas${v.listo ? ' · <span class="bien">filmado</span>' : ""}${v.costo ? " · US$" + esc(v.costo) : ""} · <span class="hint">${esc(v.ts || v.creado)}</span></p>`).join("") : '<span class="hint">Todavía no hiciste ninguno.</span>';
  document.querySelectorAll("#lista [data-r]").forEach(b => b.onclick = () => abrir(b.dataset.r).then(() => window.scrollTo(0, 0))); }
function pintarVars(){ $("#variantes").innerHTML = VARS.map((v, i) => `<div class="toma"><div class="row"><div><label>Color ${i + 1}${v.ficha ? " · 👗 de Mis prendas" : ""}</label><input data-vn="${i}" value="${esc(v.nombre)}" placeholder="rojo, negro, nude…"></div>
    <div><label>Fotos</label><input type="file" data-vf="${i}" accept="image/*" multiple></div></div><div class="thumbs">${v.fotos.map(s => `<img src="${s}">`).join("")}</div>
    ${i ? `<button data-vx="${i}">Quitar este color</button>` : ""}</div>`).join("");
  document.querySelectorAll("[data-vn]").forEach(x => x.oninput = () => { VARS[+x.dataset.vn].nombre = x.value; });
  document.querySelectorAll("[data-vf]").forEach(x => x.onchange = async e => { VARS[+x.dataset.vf].fotos = await Promise.all(Array.from(e.target.files).slice(0, 3).map(leer)); VARS[+x.dataset.vf].ficha = null; pintarVars(); });
  document.querySelectorAll("[data-vx]").forEach(x => x.onclick = () => { VARS.splice(+x.dataset.vx, 1); pintarVars(); });
  $("#otro_color").style.display = VARS.length < (CFG.max_variantes || 5) ? "" : "none"; }
$("#otro_color").onclick = () => { VARS.push({nombre: "", fotos: []}); pintarVars(); };
$("#empezar").onclick = async () => { const b = $("#empezar"); b.disabled = true; $("#estado1").innerHTML = '<span class="spin"></span>Claude está mirando la prenda…';
  try{ const d = await api("/reel", {method: "POST", body: JSON.stringify(Object.assign(ajustes(), {variantes: VARS.filter(v => v.fotos.length).map(v => v.ficha ? {nombre: v.nombre, ficha: v.ficha} : v)}))}); REEL = d.reel; guardarRid(REEL.id);
    $("#estado1").textContent = d.aviso || ""; pintar(); if(!(REEL.preguntas || []).length) $("#plan").click(); else $("#c_preg").scrollIntoView({behavior: "smooth"}); }
  catch(e){ $("#estado1").textContent = "Falló: " + e.message; } b.disabled = false; };
$("#plan").onclick = async () => { if(!REEL) return; if((REEL.tomas || []).length && !confirm("¿Rearmar el plan? Se reemplazan las tomas (y lo filmado).")) return;
  const b = $("#plan"); b.disabled = true; $("#estado2").innerHTML = '<span class="spin"></span>Claude está armando el plan…';
  try{ const d = await api(`/reel/${REEL.id}/plan`, {method: "POST", body: JSON.stringify(Object.assign(ajustes(), {respuestas: Array.from(document.querySelectorAll(".resp")).map(x => x.value)}))});
    REEL = d.reel; $("#estado2").textContent = d.aviso || ""; pintar(); $("#c_plan").scrollIntoView({behavior: "smooth"}); }
  catch(e){ $("#estado2").textContent = "Falló: " + e.message; } b.disabled = false; };
async function guardarEdicion(){ try{ const ed = {}; document.querySelectorAll("[data-ed]").forEach(x => ed[x.dataset.ed] = x.checked);
  REEL = (await api("/reel/" + REEL.id, {method: "PUT", body: JSON.stringify({edicion: ed, cierre: {titulo: $("#cierre_titulo").value, linea: $("#cierre_linea").value},
    motion: {subs: $("#mo_subs").value, fuente: $("#mo_fuente").value, corte: $("#mo_corte").value, logo: $("#mo_logo").value !== "no", esquina: $("#mo_logo").value !== "no" ? $("#mo_logo").value : "abajo_der"}})})).reel; pintar(); }catch(e){ $("#estado3").textContent = "Falló: " + e.message; } }
$("#guion").onchange = async () => { if(!REEL) return; try{ REEL = (await api("/reel/" + REEL.id, {method: "PUT", body: JSON.stringify({guion: $("#guion").value})})).reel; pintar(); }catch(e){ $("#estado3").textContent = "Falló: " + e.message; } };
$("#cierre_titulo").onchange = guardarEdicion; $("#cierre_linea").onchange = guardarEdicion;
$("#hacer_fotos").onclick = async () => { try{ const d = await api(`/reel/${REEL.id}/fotos`, {method: "POST"}); seguir(d.job); }catch(e){ $("#estado3").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#agregar").onclick = async () => { try{ REEL = (await api(`/reel/${REEL.id}/toma`, {method: "POST", body: "{}"})).reel; pintar(); }catch(e){ $("#estado3").textContent = "Falló: " + e.message; } };
$("#filmar").onclick = async () => { try{ const d = await api(`/reel/${REEL.id}/filmar`, {method: "POST"}); seguir(d.job); }catch(e){ $("#estado3").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
(async () => {
  CFG = await api("/config");
  const opts = (sel, obj, def) => { $(sel).innerHTML = Object.entries(obj).map(([k, v]) => `<option value="${k}" ${k === def ? "selected" : ""}>${esc(typeof v === "object" ? (v.label || v.nombre) : v)}</option>`).join(""); };
  opts("#motor", CFG.motores, CFG.motor_default); opts("#modo", CFG.modos, CFG.modo_default); opts("#ritmo", CFG.ritmos, CFG.ritmo_default); opts("#lugar", CFG.lugares, "dormitorio");
  opts("#look", CFG.looks, CFG.look_default); opts("#camara", CFG.camaras, "motor"); opts("#energia", CFG.energias, CFG.energia_default);
  $("#duracion").innerHTML = CFG.duraciones.map(s => `<option value="${s}" ${s === 20 ? "selected" : ""}>${s} s</option>`).join("");
  $("#voz").innerHTML = '<option value="">La del personaje</option>' + CFG.voces.mujer.map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#tono").innerHTML = CFG.tonos.map(t => `<option value="${t}" ${t === "cercana" ? "selected" : ""}>${esc(t)}</option>`).join("");
  $("#mostrar").innerHTML = Object.entries(CFG.mostrar).map(([k, v]) => `<label class="chk"><input type="checkbox" value="${k}" ${CFG.mostrar_default.includes(k) ? "checked" : ""}>${esc(v)}</label>`).join("");
  $("#aviso_claude").style.display = CFG.claude ? "none" : ""; pintarVars();
  ["#voz", "#tono", "#energia", "#mic", "#modo", "#bloques", "#fotos_clave", "#motor", "#look", "#camara", "#puesta", "#lugar", "#lugar_ref", "#probador", "#ritmo"].forEach(k => $(k).addEventListener("change", async () => {
    if(!REEL || !(REEL.tomas || []).length) return;
    try{ REEL = (await api("/reel/" + REEL.id, {method: "PUT", body: JSON.stringify(ajustes())})).reel; pintar(); }catch(e){ $("#estado3").textContent = "Falló: " + e.message; } }));
  try{ const mp = await (await fetch(CFG.mis_lugares_api + "/prendas")).json(); MIS_PRENDAS = mp.prendas || [];
    $("#ficha").innerHTML = '<option value="">— No: subo las fotos acá —</option>' + MIS_PRENDAS.map(p => `<option value="${esc(p.id)}">${esc(p.nombre)} (${p.colores.length} color${p.colores.length > 1 ? "es" : ""})</option>`).join("");
    $("#ficha").onchange = () => { const p = MIS_PRENDAS.find(x => x.id === $("#ficha").value);
      VARS = p ? p.colores.slice(0, CFG.max_variantes || 5).map((c, ci) => ({nombre: c.nombre, fotos: c.urls, ficha: [p.id, ci]})) : [{nombre: "", fotos: []}]; pintarVars(); }; }catch(e){}
  try{ const ml = await (await fetch(CFG.mis_lugares_api + "/lugares")).json();
    $("#lugar_ref").innerHTML = '<option value="">— Ninguno: el lugar sale del texto —</option>' + (ml.lugares || []).filter(l => (l.vistas || []).length).map(l => `<option value="${esc(l.id)}">${esc(l.nombre)} (${l.vistas.length} vistas)</option>`).join(""); }catch(e){}
  try{ const pj = await (await fetch("/personajes/api/lista")).json(); $("#pid").innerHTML = (pj.personajes || []).map(p => `<option value="${esc(p.id)}">${esc(p.nombre)}</option>`).join(""); }catch(e){}
  if(new URLSearchParams(location.search).get("embed")){ const avisar = () => parent.postMessage({cambiosAlto: document.documentElement.scrollHeight, de: "filmado"}, "*"); new ResizeObserver(avisar).observe(document.body); }
  lista();
  let rid = null; try{ rid = localStorage.getItem("filmado_rid"); }catch(e){}
  if(rid){ try{ await abrir(rid); }catch(e){} }
})();
</script></body></html>
"""
