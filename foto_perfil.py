"""PERFIL FOTOGRÁFICO de una foto de referencia ("Copiar fotografía", esquema 1.0).

Junta en un solo objeto lo que ya miden otras partes de Studio Luma, sin volver a analizar:
- el ESQUELETO del navegador (MediaPipe Pose, 33 puntos por persona, ver pose3d_luma.py): caja y
  puntos de cada persona, escala y márgenes del sujeto, inclinación (roll), orientación del torso y
  de la cabeza;
- el ANÁLISIS DE CLAUDE (el mismo pedido que escribe la toma, _POSE_FOTO_SYS): tipo de plano, altura
  y ángulo de cámara, distancia, focal, foco, luz y mirada. Son ESTIMACIONES y van con su confianza.

Convenciones:
- Coordenadas normalizadas 0..1 respecto del ancho y el alto de la imagen; cajas [x_min, y_min,
  x_max, y_max]; el origen es arriba a la izquierda.
- Ángulos en grados. azimuth: 0 = la cámara ve el frente del sujeto, + = la cámara está a la
  izquierda DEL SUJETO, 180 = de espaldas. elevation: + = la cámara mira desde arriba, - = desde
  abajo. roll: + = la imagen está girada en sentido horario (la línea de hombros baja a la derecha).
- Lo que no se puede saber queda "unknown" / None y se anota en warnings: no se inventa.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0"
# Subir el número del analizador que cambie: invalida sólo los perfiles hechos con la versión vieja.
ANALYZER_VERSIONS = {"esqueleto": "mediapipe-pose-0.10.14", "claude": "pose_foto-2", "perfil": "2",
                     "imagen": "medir-1"}
MODULOS = ("framing", "camera", "pose", "focus", "lighting")

# Lo que se puede corregir a mano (fase 5): ruta → valores permitidos (None = texto libre corto)
EDITABLES: Dict[str, Optional[tuple]] = {
    "framing.shot_type": ("extreme_close_up", "close_up", "medium", "three_quarter", "full_body", "wide"),
    "framing.crop": ("head", "chest", "waist", "mid_thigh", "knees", "feet", "none"),
    "camera.height_relative": ("ground", "knee", "waist", "chest", "eye", "above_head", "drone"),
    "camera.vertical_angle": ("very_low", "low", "level", "high", "very_high"),
    "camera.horizontal_angle": ("front", "three_quarter", "side", "three_quarter_back", "back"),
    "camera.distance_class": ("very_close", "close", "medium", "far", "very_far"),
    "camera.perspective_strength": ("wide", "natural", "compressed"),
    "pose.motion": ("still", "walking", "running", "jumping", "spinning", "stretching"),
    "focus.depth_of_field": ("shallow", "moderate", "deep"),
    "focus.background_blur": ("none", "subtle", "strong"),
    "lighting.direction": None,
    "lighting.hardness": ("soft", "hard"),
    "lighting.contrast": ("low", "moderate", "high"),
    "lighting.color_temperature": ("warm", "neutral", "cool"),
}

# Índices de MediaPipe Pose
NARIZ, OREJA_I, OREJA_D, HOMBRO_I, HOMBRO_D, CADERA_I, CADERA_D = 0, 7, 8, 11, 12, 23, 24
_NOMBRES = {0: "nariz", 7: "oreja izq.", 8: "oreja der.", 11: "hombro izq.", 12: "hombro der.",
            13: "codo izq.", 14: "codo der.", 15: "muñeca izq.", 16: "muñeca der.",
            23: "cadera izq.", 24: "cadera der.", 25: "rodilla izq.", 26: "rodilla der.",
            27: "tobillo izq.", 28: "tobillo der."}
VIS_MIN = 0.5            # por debajo, el punto se considera tapado o fuera de cuadro

_ELEV = {"very_low": -45, "low": -20, "level": 0, "high": 20, "very_high": 60}
_AZIM = {"front": 0, "three_quarter": 40, "side": 90, "three_quarter_back": 140, "back": 180}


def cache_key(image_hash: str) -> str:
    """image_hash + schema_version + versiones de los analizadores."""
    v = "-".join(f"{k}{ANALYZER_VERSIONS[k]}" for k in sorted(ANALYZER_VERSIONS))
    return f"{image_hash}:{SCHEMA_VERSION}:{v}"


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _txt(v: Any, ok: Optional[tuple] = None) -> str:
    t = str(v or "").strip()
    if not t:
        return "unknown"
    if ok and t not in ok:
        return "unknown"
    return t[:120]


def _kp(s: Dict[str, Any], i: int) -> Optional[List[float]]:
    try:
        p = s["pose_keypoints"][i]
        return [float(p[0]), float(p[1]), float(p[2]), float(p[3])]
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _visible(p: Optional[List[float]]) -> bool:
    return bool(p) and p[3] >= VIS_MIN


def _orientacion_torso(s: Dict[str, Any], W: int, H: int) -> Optional[Dict[str, Any]]:
    """Hacia dónde mira el torso respecto de la cámara, con los hombros y las caderas: el ancho
    aparente comparado con el largo del torso, y la profundidad (z) de un lado contra el otro."""
    hi, hd, ci, cd = (_kp(s, i) for i in (HOMBRO_I, HOMBRO_D, CADERA_I, CADERA_D))
    if not (_visible(hi) and _visible(hd)):
        return None
    ancho = math.hypot((hi[0] - hd[0]) * W, (hi[1] - hd[1]) * H)
    largo = None
    if _visible(ci) and _visible(cd):
        mh = ((hi[0] + hd[0]) / 2 * W, (hi[1] + hd[1]) / 2 * H)
        mc = ((ci[0] + cd[0]) / 2 * W, (ci[1] + cd[1]) / 2 * H)
        largo = math.hypot(mh[0] - mc[0], mh[1] - mc[1])
    rel = (ancho / largo) if largo else None
    # En la imagen, el hombro IZQUIERDO del sujeto queda a la DERECHA si está de frente.
    de_espaldas = hi[0] < hd[0]
    if rel is None:
        clase = "back" if de_espaldas else "unknown"
    elif rel < 0.35:
        clase = "side"
    elif rel < 0.62:
        clase = "three_quarter_back" if de_espaldas else "three_quarter"
    else:
        clase = "back" if de_espaldas else "front"
    lado = None
    if clase in ("side", "three_quarter", "three_quarter_back"):
        lado = "left" if hi[2] < hd[2] else "right"     # el hombro más cerca de la cámara
    return {"class": clase, "shoulder_width_to_torso": round(rel, 2) if rel else None,
            "near_side": lado, "estimated": True}


def _orientacion_cabeza(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Giro de la cabeza: dónde cae la nariz entre las dos orejas (0 = centrada)."""
    n, oi, od = (_kp(s, i) for i in (NARIZ, OREJA_I, OREJA_D))
    if not _visible(n):
        return None
    if _visible(oi) and _visible(od) and abs(oi[0] - od[0]) > 1e-4:
        giro = ((n[0] - (oi[0] + od[0]) / 2) / abs(oi[0] - od[0]))
        clase = "toward_camera" if abs(giro) < 0.2 else ("turned_left" if giro > 0 else "turned_right")
        return {"class": clase, "yaw_ratio": round(giro, 2), "estimated": True}
    if _visible(oi) or _visible(od):
        return {"class": "profile", "yaw_ratio": None, "estimated": True}
    return None


def _inclinacion_hombros(s: Dict[str, Any], W: int, H: int) -> Optional[float]:
    """Inclinación de la línea de hombros de UNA persona, en grados (+ baja hacia la derecha de la
    imagen). Es la postura, no el roll de la cámara: una persona que se inclina también la cambia."""
    hi, hd = _kp(s, HOMBRO_I), _kp(s, HOMBRO_D)
    if not (_visible(hi) and _visible(hd)):
        return None
    a = math.degrees(math.atan2((hi[1] - hd[1]) * H, (hi[0] - hd[0]) * W))
    if a > 90:
        a -= 180
    if a < -90:
        a += 180
    return round(a, 1)


def _iou(a: List[float], b: List[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def _limpiar_sujeto(s: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(s, dict):
        return None
    kps = s.get("pose_keypoints")
    if not isinstance(kps, list) or len(kps) != 33:
        return None
    try:
        kps = [[round(min(1.5, max(-0.5, float(p[0]))), 3), round(min(1.5, max(-0.5, float(p[1]))), 3),
                round(float(p[2]), 3), round(min(1.0, max(0.0, float(p[3]))), 3)] for p in kps]
    except (TypeError, ValueError, IndexError):
        return None
    bb = s.get("bbox")
    try:
        bb = [round(min(1.0, max(0.0, float(v))), 3) for v in bb] if bb and len(bb) == 4 else None
    except (TypeError, ValueError):
        bb = None
    return {"subject_id": str(s.get("subject_id") or "")[:20], "bbox": bb, "pose_keypoints": kps,
            "pose_confidence": _num(s.get("pose_confidence"))}


def _pista_camara_esqueleto(s: Dict[str, Any], W: int, H: int) -> Optional[Dict[str, Any]]:
    """PISTA de altura de cámara por el escorzo: parada y de cuerpo entero, con la cámara baja las piernas
    se ven más largas respecto del torso, y con la cámara alta más cortas. Es una pista débil."""
    pts = {i: _kp(s, i) for i in (HOMBRO_I, HOMBRO_D, CADERA_I, CADERA_D, 27, 28)}
    if not all(_visible(p) for p in pts.values()):
        return None
    mid = lambda a, b: ((pts[a][0] + pts[b][0]) / 2 * W, (pts[a][1] + pts[b][1]) / 2 * H)
    h, c, t = mid(HOMBRO_I, HOMBRO_D), mid(CADERA_I, CADERA_D), mid(27, 28)
    torso, pierna = math.hypot(h[0] - c[0], h[1] - c[1]), math.hypot(c[0] - t[0], c[1] - t[1])
    if torso < 1 or c[1] < h[1] or t[1] < c[1]:
        return None                       # acostada, sentada o al revés: la pista no aplica
    r = pierna / torso
    clase = "low" if r > 1.85 else ("high" if r < 1.2 else "level")
    return {"vertical_angle_hint": clase, "leg_to_torso": round(r, 2), "confidence": 0.35}


def _aplicar_medidas(foc: Dict[str, Any], luz: Dict[str, Any], med: Optional[Dict[str, Any]],
                     conf: Dict[str, Any], warnings: List[str]) -> None:
    """Junta lo MEDIDO en la imagen con lo ESTIMADO por el análisis visual: si coinciden sube la
    confianza; si no, queda el medido (es un dato de la foto) y se avisa."""
    if not isinstance(med, dict):
        return
    r = _num(med.get("background_sharpness_ratio"))
    if r is not None:
        m = "none" if r > 0.6 else ("subtle" if r > 0.22 else "strong")
        foc["background_blur_measured"] = m
        foc["background_sharpness_ratio"] = round(r, 3)
        if foc.get("background_blur") in (None, "unknown"):
            foc["background_blur"] = m
        elif foc["background_blur"] != m:
            warnings.append(f"desenfoque del fondo: el análisis dice {foc['background_blur']} y la medida da {m}; "
                            "se usa la medida")
            foc["background_blur"] = m
        conf["focus_measured"] = 0.8
    lr, tb = _num(med.get("light_left_right")), _num(med.get("light_top_bottom"))
    if lr is not None and tb is not None:
        lados = []
        if abs(lr) >= 0.06:
            lados.append("left" if lr > 0 else "right")
        if abs(tb) >= 0.06:
            lados.append("top" if tb > 0 else "bottom")
        luz["direction_measured"] = ("from_" + "_".join(lados) + " (of the image)") if lados else "even / frontal"
        luz["light_balance"] = {"left_minus_right": lr, "top_minus_bottom": tb}
    ct = _num(med.get("contrast"))
    if ct is not None:
        m = "low" if ct < 0.22 else ("high" if ct > 0.45 else "moderate")
        luz["contrast_measured"] = m
        if luz.get("contrast") in (None, "unknown"):
            luz["contrast"] = m
    rb = _num(med.get("red_blue_ratio"))
    if rb is not None:
        m = "warm" if rb > 1.15 else ("cool" if rb < 0.92 else "neutral")
        luz["color_temperature_measured"] = m
        if luz.get("color_temperature") in (None, "unknown"):
            luz["color_temperature"] = m
        elif luz["color_temperature"] != m:
            warnings.append(f"temperatura de color: el análisis dice {luz['color_temperature']} y la medida da {m}; "
                            "se usa la medida")
            luz["color_temperature"] = m
    if any(k.endswith("_measured") for k in luz):
        conf["lighting_measured"] = 0.7


def aplicar_correcciones(p: Dict[str, Any], cambios: Dict[str, Any], modulos: Optional[Dict[str, Any]] = None,
                         cuando: str = "") -> Dict[str, Any]:
    """Correcciones a mano (fase 5): mandan sobre lo estimado y quedan trazadas (valor anterior y fecha)."""
    p = json.loads(json.dumps(p, ensure_ascii=False))
    ov = p.setdefault("user_overrides", {})
    for ruta, val in (cambios or {}).items():
        if ruta not in EDITABLES:
            continue
        ok = EDITABLES[ruta]
        val = str(val or "").strip()[:80]
        if not val or (ok and val not in ok):
            continue
        grupo, campo = ruta.split(".", 1)
        g = p.setdefault(grupo, {})
        if not isinstance(g, dict):
            continue
        antes = ov.get(ruta, {}).get("estimated_was", g.get(campo))
        g[campo] = val
        ov[ruta] = {"value": val, "estimated_was": antes, "at": cuando}
        # los grados siguen a la corrección (si no, quedaban contradiciendo el valor corregido)
        if ruta == "camera.vertical_angle":
            g["elevation"] = _ELEV.get(val, g.get("elevation"))
        if ruta == "camera.horizontal_angle":
            g["azimuth"] = _AZIM.get(val, g.get("azimuth"))
    if isinstance(modulos, dict):
        p["modules"] = {m: bool(modulos.get(m, True)) for m in MODULOS}
    return p


def modulo_on(p: Optional[Dict[str, Any]], m: str) -> bool:
    return bool(((p or {}).get("modules") or {}).get(m, True))


def build_photo_profile(*, image_hash: str, width: int, height: int,
                        sujetos: Optional[List[Any]] = None,
                        claude: Optional[Dict[str, Any]] = None,
                        personas_claude: Optional[int] = None,
                        medidas: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Combina, normaliza y valida el perfil (esquema 1.0)."""
    W, H = max(1, int(width or 1)), max(1, int(height or 1))
    cl = claude if isinstance(claude, dict) else {}
    cfr, ccam = cl.get("framing") or {}, cl.get("camera") or {}
    cpose, cfoc, cluz = cl.get("pose") or {}, cl.get("focus") or {}, cl.get("lighting") or {}
    cconf = cl.get("confidence") or {}
    gente = [x for x in (_limpiar_sujeto(s) for s in (sujetos or [])) if x]
    warnings: List[str] = []

    # ── Sujetos: uno por persona, nunca fusionados ──
    personas_cl = cl.get("people") if isinstance(cl.get("people"), list) else []
    subjects = []
    for k, s in enumerate(gente):
        tapados = [_NOMBRES[i] for i in _NOMBRES if not _visible(_kp(s, i))]
        if tapados:
            warnings.append(f"{s['subject_id'] or 'subject_' + str(k + 1)}: puntos tapados o fuera de cuadro "
                            f"({', '.join(tapados[:8])}{'…' if len(tapados) > 8 else ''}); no se inventan")
        pc = personas_cl[k] if k < len(personas_cl) and isinstance(personas_cl[k], dict) else {}
        subjects.append({
            "subject_id": s["subject_id"] or f"subject_{k + 1}",
            "bbox": s["bbox"],
            "pose_keypoints": s["pose_keypoints"],
            "keypoint_format": "mediapipe_pose_33 [x, y, z, visibility]",
            "torso_orientation": (_to := _orientacion_torso(s, W, H)),
            # De espaldas la nariz no se ve y el detector la inventa: la cabeza no se mide.
            "head_orientation": ({"class": "away_from_camera", "yaw_ratio": None, "estimated": True}
                                 if (_to or {}).get("class") in ("back", "three_quarter_back")
                                 else _orientacion_cabeza(s)),
            "shoulder_tilt_deg": _inclinacion_hombros(s, W, H),
            "gaze_direction": _txt(pc.get("gaze") or (cpose.get("gaze") if k == 0 else None)),
            "pose_description": _txt(pc.get("pose")),
            "pose_confidence": s["pose_confidence"],
        })
    _encimados = any(_iou(a["bbox"], b["bbox"]) > 0.5 for i, a in enumerate(subjects)
                     for b in subjects[i + 1:] if a["bbox"] and b["bbox"])
    if _encimados:
        warnings.append("esqueletos superpuestos (personas encimadas): el esqueleto y el encuadre medido "
                        "son poco confiables; vale más el análisis visual")
    if personas_claude and personas_claude > len(subjects):
        warnings.append(f"el análisis visual ve {personas_claude} personas y el esqueleto encontró "
                        f"{len(subjects)}: hay personas tapadas o encimadas")

    # ── Encuadre (medido) ──
    cajas = [s["bbox"] for s in subjects if s["bbox"]]
    if cajas:
        bb = [min(c[0] for c in cajas), min(c[1] for c in cajas), max(c[2] for c in cajas), max(c[3] for c in cajas)]
        framing = {
            "shot_type": _txt(cfr.get("shot_type")),
            "subject_bbox": bb,
            "subject_center": [round((bb[0] + bb[2]) / 2, 3), round((bb[1] + bb[3]) / 2, 3)],
            "subject_height_ratio": round(bb[3] - bb[1], 3),
            "headroom_ratio": bb[1],
            "crop": _txt(cfr.get("crop")),
            # dónde quedan los bordes del sujeto dentro de la foto (0..1)
            "crop_boundaries": {"top": bb[1], "right": bb[2], "bottom": bb[3], "left": bb[0]},
            "touches_edges": [lado for lado, v in (("top", bb[1] <= 0.01), ("bottom", bb[3] >= 0.99),
                                                   ("left", bb[0] <= 0.01), ("right", bb[2] >= 0.99)) if v],
            "measured": True,
        }
    else:
        framing = {"shot_type": _txt(cfr.get("shot_type")), "subject_bbox": None, "subject_center": None,
                   "subject_height_ratio": None, "headroom_ratio": None, "crop": _txt(cfr.get("crop")),
                   "crop_boundaries": None, "measured": False}
        warnings.append("sin esqueleto: el encuadre es sólo la estimación visual")

    # ── Cámara (estimada) ──
    elev = _num(ccam.get("elevation_deg"))
    if elev is None:
        elev = _ELEV.get(str(ccam.get("vertical_angle", "")))
    azim = _num(ccam.get("azimuth_deg"))
    if azim is None:
        azim = _AZIM.get(str(ccam.get("horizontal_angle", "")))
    camera = {
        "height_relative": _txt(ccam.get("height")),
        "vertical_angle": _txt(ccam.get("vertical_angle")),
        "horizontal_angle": _txt(ccam.get("horizontal_angle")),
        "azimuth": azim, "elevation": elev, "roll": _num(ccam.get("roll_deg")),
        "focal_length_estimate_mm": _num(ccam.get("focal_length_mm")),
        "distance_estimate_m": _num(ccam.get("distance_m")),
        "distance_class": _txt(ccam.get("distance_class")),
        "perspective_strength": _txt(ccam.get("perspective")),
        "estimated": True,
    }
    if camera["focal_length_estimate_mm"] is None or camera["distance_estimate_m"] is None:
        warnings.append("focal o distancia física sin evidencia suficiente: quedan sin valor")

    conf = {k: _num(v) for k, v in cconf.items()} if isinstance(cconf, dict) else {}
    # Pista de la cámara por el escorzo del esqueleto (persona principal): confirma o discute la estimación.
    if gente:
        pista = _pista_camara_esqueleto(gente[0], W, H)
        if pista:
            camera["skeleton_hint"] = pista
            est = camera.get("vertical_angle", "unknown")
            grupo = {"very_low": "low", "low": "low", "level": "level", "high": "high", "very_high": "high"}.get(est)
            if grupo and grupo != pista["vertical_angle_hint"]:
                warnings.append(f"ángulo de cámara: el análisis dice {est} y el escorzo del cuerpo sugiere "
                                f"{pista['vertical_angle_hint']} (pista débil)")
            elif grupo and conf.get("camera") is not None:
                conf["camera"] = round(min(1.0, conf["camera"] + 0.1), 2)
    focus = {"focus_target": _txt(cfoc.get("target")), "depth_of_field": _txt(cfoc.get("depth_of_field")),
             "background_blur": _txt(cfoc.get("background_blur")), "estimated": True}
    lighting = {"direction": _txt(cluz.get("direction")), "hardness": _txt(cluz.get("hardness")),
                "contrast": _txt(cluz.get("contrast")),
                "color_temperature": _txt(cluz.get("color_temperature")),
                "shadow_direction": _txt(cluz.get("shadow_direction")), "estimated": True}
    _aplicar_medidas(focus, lighting, medidas, conf, warnings)
    if subjects:
        conf["pose_skeleton"] = round(sum((s["pose_confidence"] or 0) for s in subjects) / len(subjects), 2)
        conf["framing_measured"] = 0.3 if _encimados else 1.0
    for k, v in conf.items():
        if v is not None and v < 0.5:
            warnings.append(f"confianza baja en {k} ({v})")

    return {
        "schema_version": SCHEMA_VERSION,
        "analyzer_versions": dict(ANALYZER_VERSIONS),
        "profile_id": f"{image_hash[:16]}-{SCHEMA_VERSION}" if image_hash else None,
        "source": {"image_hash": image_hash or None, "width": W, "height": H,
                   "aspect_ratio": round(W / H, 4)},
        "framing": framing,
        "camera": camera,
        "subjects": subjects,
        "pose": {"weight": _txt(cpose.get("weight")), "motion": _txt(cpose.get("motion")),
                 "arms": _txt(cpose.get("arms"))},
        "focus": focus,
        "lighting": lighting,
        "image_measurements": medidas if isinstance(medidas, dict) else None,
        "background": _txt(cl.get("background")),
        "confidence": conf,
        "warnings": warnings,
        "user_overrides": {},
        "modules": {m: True for m in MODULOS},
    }


def _poco_fiable(p: Dict[str, Any]) -> bool:
    return (_num((p.get("confidence") or {}).get("framing_measured")) or 1) < 0.5


def perfil_para_prompt(p: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Lo que va al generador, en el MODO "Alta fidelidad + Flexible": BLOQUEADO (plano, recorte, escala,
    posición, cámara), ADAPTABLE (pose y peso, con ajustes anatómicos para el nuevo sujeto), OBJETIVOS
    VISUALES (foco y luz, adaptables al nuevo fondo). Sin los 33 puntos. Respeta los módulos apagados."""
    if not isinstance(p, dict):
        return {}
    limpio = lambda d: {k: v for k, v in (d or {}).items() if v not in (None, "unknown", True, {}, [])}
    fr, cam = p.get("framing") or {}, p.get("camera") or {}
    bloq, adap, obj = {}, {}, {}
    if modulo_on(p, "framing"):
        ks = ("shot_type", "crop") if _poco_fiable(p) else ("shot_type", "subject_bbox", "subject_center",
                                                            "subject_height_ratio", "headroom_ratio", "crop")
        bloq["framing"] = {k: fr.get(k) for k in ks if fr.get(k) is not None}
    if modulo_on(p, "camera"):
        bloq["camera"] = {k: cam.get(k) for k in ("height_relative", "vertical_angle", "horizontal_angle", "azimuth",
                                                  "elevation", "roll", "focal_length_estimate_mm",
                                                  "distance_class", "perspective_strength")
                          if cam.get(k) not in (None, "unknown")}
    if modulo_on(p, "pose"):
        adap["subjects"] = [{k: v for k, v in (("id", s.get("subject_id")), ("bbox", s.get("bbox")),
                                               ("torso", (s.get("torso_orientation") or {}).get("class")),
                                               ("head", (s.get("head_orientation") or {}).get("class")),
                                               ("gaze", s.get("gaze_direction")),
                                               ("pose", s.get("pose_description"))) if v not in (None, "unknown")}
                            for s in (p.get("subjects") or [])]
        adap["pose"] = limpio(p.get("pose"))
    if modulo_on(p, "focus"):
        obj["focus"] = {k: v for k, v in limpio(p.get("focus")).items() if k in ("focus_target", "depth_of_field", "background_blur")}
    if modulo_on(p, "lighting"):
        l = limpio(p.get("lighting"))
        obj["lighting"] = {k: l[k] for k in ("direction", "direction_measured", "hardness", "contrast",
                                             "color_temperature", "shadow_direction") if k in l}
    out = {}
    if any(bloq.values()):
        out["LOCKED (copy exactly)"] = {k: v for k, v in bloq.items() if v}
    if any(adap.values()):
        out["ADAPTABLE (keep, adjust anatomy to the new subject)"] = {k: v for k, v in adap.items() if v}
    if any(obj.values()):
        out["VISUAL GOALS (adapt to the new background)"] = {k: v for k, v in obj.items() if v}
    if out:
        out["EDITABLE (from the job, not from the reference)"] = "model identity, garment, colour, texture, background, scene details"
    return out


_ALTURA_ES = {"ground": "casi apoyada en el piso", "knee": "a la altura de las rodillas de ella",
              "waist": "a la altura de la cintura de ella", "chest": "a la altura del pecho de ella",
              "eye": "a la altura de los ojos de ella", "above_head": "por encima de la cabeza de ella",
              "drone": "muy alta, como un dron"}
_TORSO_ES = {"front": "de frente a la cámara", "three_quarter": "girada en 3/4 respecto de la cámara",
             "side": "de perfil respecto de la cámara", "three_quarter_back": "de espaldas en 3/4",
             "back": "de espaldas a la cámara"}


_ALTURA_EN = {"ground": "almost on the floor", "knee": "at her knee height", "waist": "at her waist height",
              "chest": "at her chest height", "eye": "at her eye level", "above_head": "above her head",
              "drone": "very high, like a drone"}
_TORSO_EN = {"front": "facing the camera", "three_quarter": "turned three-quarters to the camera",
             "side": "in profile to the camera", "three_quarter_back": "three-quarters from behind",
             "back": "with her back to the camera"}


def vista_de_atras(p: Optional[Dict[str, Any]]) -> bool:
    """¿La foto la muestra DE ESPALDAS (de atrás o de atrás en diagonal)?"""
    if not isinstance(p, dict) or not modulo_on(p, "camera"):
        return False
    c = p.get("camera") or {}
    az = _num(c.get("azimuth"))
    torso = next((((s.get("torso_orientation") or {}).get("class")) for s in (p.get("subjects") or [])
                  if isinstance(s, dict) and (s.get("torso_orientation") or {}).get("class")), None)
    return (az is not None and az >= 110) or torso in ("back", "three_quarter_back") \
        or str(c.get("horizontal_angle") or "") in ("back", "three_quarter_back")


def aviso_guia_de_atras(p: Optional[Dict[str, Any]], en: bool = False) -> str:
    """El maniquí guía no tiene cara ni pelo: de espaldas se ve IGUAL que de frente, y el generador
    lo leía siempre de frente. Si la foto es de espaldas, se le dice con todas las letras."""
    if not vista_de_atras(p):
        return ""
    if en:
        return ("WARNING: in the guide the mannequin is seen FROM BEHIND (its back faces the camera). A "
                "faceless mannequin looks the same from the front and from behind: do NOT read it as "
                "facing the camera. In the photo the camera sees HER BACK and the BACK of the garment "
                "(the front of her body is not visible); only her head turns towards the camera, over "
                "her shoulder, if the guide's head is turned. ")
    return ("OJO: en esta guía el maniquí está DE ESPALDAS (la espalda hacia la cámara). Un maniquí sin "
            "cara se ve igual de frente que de espaldas: NO lo leas de frente. En la foto la cámara ve SU "
            "ESPALDA y la PARTE DE ATRÁS de la prenda (el frente del cuerpo no se ve); sólo la cabeza gira "
            "hacia la cámara, por encima del hombro, si en la guía la cabeza está girada. ")


def camara_en_palabras(p: Optional[Dict[str, Any]], en: bool = False) -> str:
    """La cámara MEDIDA de la foto, en palabras y números, para el renglón CÁMARA del pedido. Antes
    se pasaba a uno de los ángulos fijos de la lista (muchas veces "normal, de frente") y se perdían
    el costado, la inclinación y la distancia. "" si no hay perfil o el módulo de cámara está apagado."""
    if not isinstance(p, dict) or not modulo_on(p, "camera"):
        return ""
    c, fr = p.get("camera") or {}, p.get("framing") or {}
    E = lambda es, en_: en_ if en else es
    partes = []
    alt = (_ALTURA_EN if en else _ALTURA_ES).get(str(c.get("height_relative") or ""))
    if alt:
        partes.append(E(f"cámara {alt}", f"camera {alt}"))
    elev = _num(c.get("elevation"))
    if elev is not None:
        if elev <= -8:
            partes.append(E(f"apuntando HACIA ARRIBA (contrapicado de unos {abs(round(elev))}°)",
                            f"pointing UP at her (low angle of about {abs(round(elev))}°)"))
        elif elev >= 8:
            partes.append(E(f"apuntando HACIA ABAJO (picado de unos {round(elev)}°)",
                            f"pointing DOWN at her (high angle of about {round(elev)}°)"))
        else:
            partes.append(E("derecha, sin inclinar", "level, not tilted"))
    az = _num(c.get("azimuth"))
    if az is not None:
        if az >= 150:
            partes.append(E("DETRÁS de ella: se ve su espalda, el frente del cuerpo NO se ve",
                            "BEHIND her: her back is seen, the front of her body is NOT visible"))
        elif az >= 110:
            # Antes este tramo caía en "de perfil" y la foto de espaldas en 3/4 salía de costado o de frente.
            partes.append(E(f"DETRÁS de ella en diagonal (unos {round(az)}°): se ve su espalda y la cola, "
                            "el frente del cuerpo NO se ve",
                            f"BEHIND her, diagonally (about {round(az)}°): her back and bottom are seen, "
                            "the front of her body is NOT visible"))
        elif az >= 65:
            partes.append(E(f"corrida a un costado (unos {round(az)}°): se la ve de perfil",
                            f"moved to her side (about {round(az)}°): she is seen in profile"))
        elif az >= 20:
            partes.append(E(f"corrida en diagonal (unos {round(az)}°), NO enfrentada",
                            f"moved diagonally (about {round(az)}°), NOT facing her straight on"))
        else:
            partes.append(E("enfrentada", "straight on"))
    torso = next((((s.get("torso_orientation") or {}).get("class")) for s in (p.get("subjects") or [])
                  if isinstance(s, dict) and (s.get("torso_orientation") or {}).get("class")), None)
    atras = vista_de_atras(p)
    if atras and torso not in ("back", "three_quarter_back"):
        torso = None                     # los hombros medidos "de perfil" contradecían la cámara de atrás
    tz = (_TORSO_EN if en else _TORSO_ES).get(str(torso or ""))
    if tz:
        partes.append(E(f"ella {tz}", f"her body {tz}"))
    if atras:
        cab = next((((s.get("head_orientation") or {}).get("class")) for s in (p.get("subjects") or [])
                    if isinstance(s, dict)), None)
        mira = " ".join(str(s.get("gaze_direction") or "") for s in (p.get("subjects") or [])
                        if isinstance(s, dict)).lower()
        if cab in ("toward_camera", "turned_left", "turned_right", "profile") or "cám" in mira \
                or "cam" in mira or "lente" in mira or "hombro" in mira or "shoulder" in mira:
            partes.append(E("ella gira la cabeza y mira a cámara por encima del hombro",
                            "she turns her head and looks at the camera over her shoulder"))
    d = _num(c.get("distance_estimate_m"))
    if d:
        partes.append(E(f"a unos {d:g} m de ella", f"about {d:g} m from her"))
    f = _num(c.get("focal_length_estimate_mm"))
    if f:
        partes.append(E(f"lente de unos {round(f)} mm", f"lens of about {round(f)} mm"))
    alto = _num(fr.get("subject_height_ratio"))
    if alto and (_num((p.get("confidence") or {}).get("framing_measured")) or 0) >= 0.5:
        partes.append(E(f"ella ocupa el {round(alto * 100)}% del alto del cuadro",
                        f"she fills {round(alto * 100)}% of the frame height"))
    if not partes:
        return ""
    if en:
        return ("CAMERA COPIED FROM HER PHOTO (measured, mandatory; overrides any other angle in this text): "
                + ", ".join(partes) + ". Copy it exactly as in the POSE AND CAMERA GUIDE image.")
    return ("COPIADA DE SU FOTO (medida, obligatoria; manda sobre cualquier otro ángulo del texto): "
            + ", ".join(partes) + ". Copiala igual que en la imagen rotulada GUÍA DE POSE Y CÁMARA.")


def camara_de_perfil(p: Optional[Dict[str, Any]]) -> Optional[int]:
    """El ángulo de la lista de cámaras de Studio Luma (CAMARA_POOL) que mejor corresponde al perfil."""
    if not isinstance(p, dict) or not modulo_on(p, "camera"):
        return None
    c, fr = p.get("camera") or {}, p.get("framing") or {}
    h, v = str(c.get("height_relative", "")), str(c.get("vertical_angle", ""))
    hz, st = str(c.get("horizontal_angle", "")), str(fr.get("shot_type", ""))
    elev = _num(c.get("elevation"))
    dist = _num(c.get("distance_estimate_m")) or 0
    alto = _num(fr.get("subject_height_ratio"))
    if (_num((p.get("confidence") or {}).get("framing_measured")) or 0) < 0.5:
        alto = None                      # esqueletos encimados: la escala medida no sirve
    if h == "drone" or v == "very_high" or (elev is not None and elev >= 45):
        return 8
    if h == "ground" or v == "very_low" or (elev is not None and elev <= -35):
        return 12 if hz in ("side", "three_quarter") else 6
    if st in ("extreme_close_up", "close_up") or (0 < dist <= 1.0):
        return 5
    if h in ("knee", "waist") or v == "low" or (elev is not None and elev <= -10):
        return 12 if hz == "side" else 1
    if h == "above_head" or v == "high" or (elev is not None and elev >= 15):
        return 3
    if st == "wide" or dist >= 8 or (alto is not None and alto < 0.3):
        return 7
    if dist >= 4 or (alto is not None and alto < 0.55):
        return 4
    if hz == "side":
        return 11
    if hz == "three_quarter":
        return 2
    return 0
