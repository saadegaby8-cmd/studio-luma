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

import math
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0"
# Subir el número del analizador que cambie: invalida sólo los perfiles hechos con la versión vieja.
ANALYZER_VERSIONS = {"esqueleto": "mediapipe-pose-0.10.14", "claude": "pose_foto-2", "perfil": "1"}

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


def build_photo_profile(*, image_hash: str, width: int, height: int,
                        sujetos: Optional[List[Any]] = None,
                        claude: Optional[Dict[str, Any]] = None,
                        personas_claude: Optional[int] = None) -> Dict[str, Any]:
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
        "focus": {"focus_target": _txt(cfoc.get("target")), "depth_of_field": _txt(cfoc.get("depth_of_field")),
                  "background_blur": _txt(cfoc.get("background_blur")), "estimated": True},
        "lighting": {"direction": _txt(cluz.get("direction")), "hardness": _txt(cluz.get("hardness")),
                     "contrast": _txt(cluz.get("contrast")),
                     "color_temperature": _txt(cluz.get("color_temperature")),
                     "shadow_direction": _txt(cluz.get("shadow_direction")), "estimated": True},
        "background": _txt(cl.get("background")),
        "confidence": conf,
        "warnings": warnings,
        "user_overrides": {},
    }


def _poco_fiable(p: Dict[str, Any]) -> bool:
    return (_num((p.get("confidence") or {}).get("framing_measured")) or 1) < 0.5


def perfil_para_prompt(p: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """La versión que va al generador: sin los 33 puntos (no los usa y ocupan mucho lugar)."""
    if not isinstance(p, dict):
        return {}
    fr, cam = p.get("framing") or {}, p.get("camera") or {}
    out = {
        "framing": {k: fr.get(k) for k in (("shot_type", "crop") if _poco_fiable(p) else
                                           ("shot_type", "subject_bbox", "subject_center",
                                            "subject_height_ratio", "headroom_ratio", "crop"))
                    if fr.get(k) is not None},
        "camera": {k: cam.get(k) for k in ("height_relative", "vertical_angle", "horizontal_angle", "azimuth",
                                           "elevation", "roll", "focal_length_estimate_mm",
                                           "distance_class", "perspective_strength")
                   if cam.get(k) not in (None, "unknown")},
        "subjects": [{k: v for k, v in (("id", s.get("subject_id")), ("bbox", s.get("bbox")),
                                        ("torso", (s.get("torso_orientation") or {}).get("class")),
                                        ("head", (s.get("head_orientation") or {}).get("class")),
                                        ("gaze", s.get("gaze_direction")),
                                        ("pose", s.get("pose_description"))) if v not in (None, "unknown")}
                     for s in (p.get("subjects") or [])],
        "pose": {k: v for k, v in (p.get("pose") or {}).items() if v not in (None, "unknown")},
        "focus": {k: v for k, v in (p.get("focus") or {}).items() if v not in (None, "unknown", True)},
        "lighting": {k: v for k, v in (p.get("lighting") or {}).items() if v not in (None, "unknown", True)},
    }
    return {k: v for k, v in out.items() if v}


def camara_de_perfil(p: Optional[Dict[str, Any]]) -> Optional[int]:
    """El ángulo de la lista de cámaras de Studio Luma (CAMARA_POOL) que mejor corresponde al perfil."""
    if not isinstance(p, dict):
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
