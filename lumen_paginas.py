# -*- coding: utf-8 -*-
"""Las páginas del SET 3D PRO (editor) y del CELULAR COMO CÁMARA. Ver cine_set3d.py."""

_ESTILO = r"""
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;--rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f}
  *{box-sizing:border-box} html,body{margin:0;height:100%;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:13.5px}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:6px 12px;font:inherit;font-size:12.5px;cursor:pointer;margin:0 4px 5px 0}
  button:disabled{opacity:.45;cursor:default} button.on{background:var(--rose);color:#17140d;border-color:var(--rose)}
  button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500}
  input,select{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:5px 7px;font:inherit;font-size:12.5px}
  input[type=range]{width:100%;padding:0} input[type=number]{width:100%}
  label{display:block;font-size:11.5px;color:var(--ink-soft);margin:6px 0 2px}
  .hint{color:var(--ink-soft);font-size:12px;line-height:1.45} .mal{color:var(--bad)} .bien{color:var(--ok)}
  .spin{display:inline-block;width:11px;height:11px;border:2px solid var(--ink-soft);border-top-color:var(--rose);border-radius:50%;animation:g 1s linear infinite;vertical-align:-2px} @keyframes g{to{transform:rotate(360deg)}}
"""

PAGINA_SET3D = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lumen · Studio Luma</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>""" + _ESTILO + r"""
  .app{display:grid;grid-template-columns:270px 1fr 280px;grid-template-rows:auto 1fr auto;height:100vh}
  .top{grid-column:1 / span 3;display:flex;gap:4px;align-items:center;flex-wrap:wrap;padding:7px 10px 2px;background:var(--card);border-bottom:1px solid var(--line)}
  .top h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:18px;margin:0 12px 4px 0}
  .sep{width:1px;height:22px;background:var(--line);margin:0 6px 5px}
  .panel{background:var(--card);padding:10px;overflow:auto;border-right:1px solid var(--line)} .panel.der{border-right:none;border-left:1px solid var(--line)}
  h3{font-size:11.5px;margin:12px 0 6px;font-weight:500;color:var(--rose-deep);text-transform:uppercase;letter-spacing:.06em}
  .tabs{display:flex;flex-wrap:wrap;gap:2px;margin-bottom:6px} .tabs button{padding:4px 9px;font-size:12px}
  .grilla{display:grid;grid-template-columns:repeat(3,1fr);gap:6px} .grilla .it{background:var(--card-2);border:1px solid var(--line);border-radius:8px;cursor:pointer;font-size:10.5px;text-align:center;padding:3px;overflow:hidden}
  .grilla .it img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:5px;display:block;background:#0f0e13} .grilla .it:hover{border-color:var(--rose)}
  .escena{position:relative;display:flex;align-items:center;justify-content:center;background:#0d0c11;overflow:hidden}
  #lienzo{display:block;box-shadow:0 0 0 1px var(--line),0 10px 40px #0008;border-radius:3px;touch-action:none}
  .tag{position:absolute;top:8px;left:50%;transform:translateX(-50%);background:#000b;padding:3px 10px;border-radius:999px;font-size:12px;color:var(--ink-soft);pointer-events:none}
  #aviso{position:absolute;bottom:10px;left:50%;transform:translateX(-50%);background:#000c;padding:5px 12px;border-radius:10px;font-size:12.5px;max-width:90%;text-align:center;display:none}
  .lista .fila{display:flex;align-items:center;gap:6px;padding:4px 7px;border-radius:7px;cursor:pointer;font-size:12.5px}
  .lista .fila:hover{background:var(--card-2)} .lista .fila.on{background:#3a3020;color:var(--rose-deep)} .lista .fila small{margin-left:auto;color:var(--ink-soft)}
  .xyz{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
  .tl{grid-column:1 / span 3;background:var(--card);border-top:1px solid var(--line);padding:6px 10px 8px}
  .tlbar{display:flex;gap:4px;align-items:center;flex-wrap:wrap} #tiempo{font-variant-numeric:tabular-nums;color:var(--rose-deep);min-width:92px}
  .pista{position:relative;height:62px;margin-top:4px;background:#121117;border:1px solid var(--line);border-radius:8px;cursor:pointer;user-select:none}
  .regla{position:absolute;left:0;right:0;top:0;height:16px;border-bottom:1px solid var(--line);font-size:10px;color:var(--ink-soft)}
  .regla span{position:absolute;top:1px;transform:translateX(-50%)}
  .fl{position:absolute;left:0;right:0;height:22px} .fl.cam{top:17px} .fl.obj{top:39px}
  .fl .nom{position:absolute;left:4px;top:4px;font-size:10px;color:var(--ink-soft);pointer-events:none}
  .rombo{position:absolute;top:5px;width:11px;height:11px;margin-left:-6px;transform:rotate(45deg);background:var(--rose);border:1px solid #17140d;cursor:pointer}
  .rombo.sel{background:#fff} .fl.obj .rombo{background:#8fc7e0}
  .pto{position:absolute;top:0;height:16px;margin-left:-8px;width:16px;border-radius:4px;background:#5a4a2a;color:#fff;font-size:10px;text-align:center;line-height:16px}
  #cabezal{position:absolute;top:0;bottom:0;width:2px;background:#fff;pointer-events:none}
  .modal{position:fixed;inset:0;background:#000a;display:none;align-items:center;justify-content:center;z-index:9}
  .modal .caja{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;max-width:420px;text-align:center}
  #qr{background:#fff;padding:10px;border-radius:10px;display:inline-block;margin:10px 0}
  @media(max-width:1000px){.app{grid-template-columns:1fr;grid-template-rows:auto 60vh auto auto auto;height:auto}.top,.tl{grid-column:1}.panel{border:none}}
</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
<script src="https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js"></script>
</head><body>
<div class="app">
  <div class="top">
    <a href="%%PREF%%" class="hint" style="margin:0 8px 5px 0;text-decoration:none">◀ Proyectos</a>
    <h2>🧊 Lumen <small style="font-family:Jost;font-size:11px;color:var(--ink-soft)">v%%VERSION%%</small></h2><span id="proyNom" class="hint" style="margin:0 10px 5px 0"></span>
    <button id="vCam" class="on" title="Mirás por la cámara de la película: lo que se ve es la toma">🎥 Por la cámara</button>
    <button id="vLibre" title="Mirás el set desde afuera, con la cámara a la vista">👁 Desde afuera</button>
    <span class="sep"></span>
    <button id="hMover" class="on" title="W">↔ Mover</button><button id="hGirar" title="E">⟳ Girar</button><button id="hEscala" title="R">⤢ Tamaño</button>
    <span class="sep"></span>
    <button id="deshacer" title="Ctrl+Z">↶ Deshacer</button><button id="duplicar">⧉ Duplicar</button><button id="borrar">🗑 Borrar</button>
    <span class="sep"></span>
    <button id="celBtn">📱 Celular como cámara</button><span id="celEstado" class="hint"></span>
    <span style="flex:1"></span>
    <span id="guardado" class="hint"></span>
    <button id="filmarBtn" style="display:none" title="Graba el recorrido y la IA lo filma con tus personajes de verdad">🎬 Filmar con IA</button>
    <button class="go" id="usar">📸 Guardar las tomas</button>
  </div>
  <div id="filmar" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:50;align-items:center;justify-content:center;padding:16px">
    <div style="background:var(--card);border:1px solid var(--line);border-radius:16px;max-width:640px;width:100%;max-height:90vh;overflow:auto;padding:18px">
      <div style="display:flex;align-items:center"><h3 style="flex:1;margin:0">🎬 Filmar con IA</h3><button id="fCerrar">✕</button></div>
      <div class="hint" style="margin:6px 0 10px">Se graba el recorrido (la cámara y cómo se mueve cada maniquí) y la IA lo filma igual, con tus personajes reales y el lugar de verdad.</div>
      <div id="fCuerpo"></div>
    </div>
  </div>

  <div class="panel">
    <div class="tabs"><button data-tab="formas" class="on">Formas</button><button data-tab="biblio">📚 Biblioteca</button><button data-tab="subir">📦 Subir</button>
      <button data-tab="gente">🧍 Personas</button><button data-tab="luces">💡 Luces</button><button data-tab="cielo">🌅 Cielo</button></div>
    <div data-pan="formas"><div class="hint">Tocá una forma y aparece en el centro de la vista.</div><div id="formas" style="margin-top:6px"></div></div>
    <div data-pan="biblio" style="display:none">
      <div class="hint">Modelos 3D gratis y reales (Poly Haven, uso libre). Tocá uno para traerlo.</div>
      <input id="bBuscar" placeholder="buscar: sofa, chair, plant, boat…" style="width:100%;margin:6px 0">
      <div id="bGrilla" class="grilla"></div><div id="bMas"></div>
    </div>
    <div data-pan="subir" style="display:none">
      <div class="hint">Subí un modelo tuyo o bajado de internet (Sketchfab, TurboSquid…): <b>.glb</b> (el mejor), .gltf, .fbx u .obj, hasta 80 MB.</div>
      <input type="file" id="archivo" accept=".glb,.gltf,.fbx,.obj" style="margin-top:8px;width:100%">
      <div id="subEstado" class="hint" style="margin-top:6px"></div>
    </div>
    <div data-pan="gente" style="display:none"><div class="hint">Un maniquí por personaje del elenco. Después elegís su pose a la derecha.</div><div id="gente" style="margin-top:6px"></div></div>
    <div data-pan="luces" style="display:none">
      <div class="hint">Luces de verdad, con sombras. Movelas y giralas como cualquier objeto: alumbran hacia donde apunta la flecha.</div>
      <div style="margin-top:6px"><button data-luz="sol">☀ Sol</button><button data-luz="foco">🔦 Foco</button><button data-luz="lampara">💡 Lámpara</button><button data-luz="ventana">🪟 Ventana</button></div>
      <label>Exposición general: <span id="expoV">1.0</span></label><input type="range" id="expo" min="0.2" max="2.5" step="0.05" value="1">
    </div>
    <div data-pan="cielo" style="display:none">
      <div class="hint">Un color simple o un <b>cielo real 360°</b> (HDRI): ilumina la escena con la luz de ese lugar.</div>
      <div id="cielosSimples" style="margin-top:6px"></div>
      <label>Cielos reales (HDRI)</label><div id="hGrilla" class="grilla"></div><div id="hMas"></div>
    </div>
  </div>

  <div class="escena" id="escena"><canvas id="lienzo"></canvas><div class="tag" id="tag">Cargando…</div><div id="aviso"></div></div>

  <div class="panel der">
    <h3>La escena</h3><div id="lista" class="lista"></div>
    <div id="props"></div>
    <div id="refPanel" style="display:none"><h3>📹 Tu video</h3><img id="refImg" style="width:100%;border-radius:8px;display:block"><div class="hint" id="refTxt"></div></div>
    <h3>La cámara</h3>
    <label>Lente: <b id="lenteV">35</b> mm <span class="hint" id="lenteTxt"></span></label>
    <input type="range" id="lente" min="12" max="200" step="1" value="35">
    <div><button data-mm="16">16</button><button data-mm="24">24</button><button data-mm="35">35</button><button data-mm="50">50</button><button data-mm="85">85</button><button data-mm="135">135</button></div>
    <div class="hint" id="camInfo"></div>
    <div><button id="encuadrar" title="Pone la cámara a la altura de los ojos, con lente 35 mm, mirando a lo elegido (o a la primera persona)">🎯 Encuadrar a la persona</button></div>
    <div id="tomasPanel" style="display:none"><h3>🎞 Tomas</h3><div class="hint">Momentos de la línea de tiempo que se guardan como imagen: la guía de cámara y pose para Fotos, Reels, Storyboard, Cine…</div><div id="tomasLista"></div></div>
    <div id="celPanel" style="display:none"><h3>📱 Celular</h3>
      <label>1 metro del celular = <b id="escV">1</b> m en el set</label><input type="range" id="escMov" min="0.2" max="20" step="0.1" value="1">
      <button id="centrar">🎯 Centrar</button><div class="hint">Movés el celular y la cámara se mueve igual. "Centrar" hace que lo que apuntás ahora sea lo que mira la cámara.</div></div>
  </div>

  <div class="tl">
    <div class="tlbar">
      <button id="inicio" title="Ir al principio">⏮</button><button id="play">▶ Reproducir</button><button id="grabarVid" title="Baja el animatic en video">⏺ Video</button>
      <span id="tiempo">0.0 s</span>
      <button class="go" id="claveCam">◆ Guardar la cámara en este segundo</button>
      <button id="claveObj">◆ Clave del objeto</button><button id="borrarClave">✕ Borrar clave</button>
      <button id="marcar" style="display:none">🎞 Marcar toma acá</button>
      <span class="hint">Duración</span><input type="number" id="dur" min="3" max="180" step="1" style="width:64px">
      <span class="hint" id="tlAyuda">Mové la cámara y guardala en distintos segundos: la película pasa de una a otra.</span>
    </div>
    <div class="pista" id="pista"><div class="regla" id="regla"></div><div class="fl cam" id="flCam"><span class="nom">🎥 cámara</span></div><div class="fl obj" id="flObj"><span class="nom" id="flObjNom">objeto elegido</span></div><div id="cabezal"></div></div>
  </div>
</div>

<div class="modal" id="mCel"><div class="caja">
  <h3 style="margin-top:0">📱 Tu celular como cámara</h3>
  <div class="hint">Escaneá el código con la cámara del celular (tiene que estar con internet). En el celular tocá <b>Empezar</b> y movelo: la cámara del set se mueve igual. Con <b>⏺</b> grabás el recorrido.</div>
  <div id="qr"></div><div class="hint" style="word-break:break-all" id="celUrl"></div>
  <div style="margin-top:10px"><button id="celCerrar">Listo</button></div>
</div></div>

<script type="module">
import * as THREE from "three";
import {OrbitControls} from "three/addons/controls/OrbitControls.js";
import {TransformControls} from "three/addons/controls/TransformControls.js";
import {GLTFLoader} from "three/addons/loaders/GLTFLoader.js";
import {FBXLoader} from "three/addons/loaders/FBXLoader.js";
import {OBJLoader} from "three/addons/loaders/OBJLoader.js";
import {RGBELoader} from "three/addons/loaders/RGBELoader.js";
import {RectAreaLightUniformsLib} from "three/addons/lights/RectAreaLightUniformsLib.js";
import * as SkeletonUtils from "three/addons/utils/SkeletonUtils.js";
const API = "%%API%%";
const QS = new URLSearchParams(location.search);
const CID = QS.get("cine") || QS.get("cid"), PID = QS.get("id");      // película de Cine o proyecto de Lumen
const CINE_API = "/cine/api", DUENO = CID ? "cine-" + CID : PID;
let ASPECT = 9 / 16, PROY = null;
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const R = v => +(+v).toFixed(3), GR = THREE.MathUtils.radToDeg, RG = THREE.MathUtils.degToRad;
function aviso(t, ms){ const a = $("#aviso"); a.innerHTML = t; a.style.display = ""; clearTimeout(aviso.t); aviso.t = setTimeout(() => a.style.display = "none", ms || 3500); }

// ── MOTOR ──
RectAreaLightUniformsLib.init();
const lienzo = $("#lienzo");
const renderer = new THREE.WebGLRenderer({canvas: lienzo, antialias: true, preserveDrawingBuffer: true});
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
const scene = new THREE.Scene();
const AYUDAS = new THREE.Group(); scene.add(AYUDAS);          // todo lo que NO sale en la foto
const camFilm = new THREE.PerspectiveCamera(50, 9 / 16, 0.05, 20000); scene.add(camFilm);
const camLibre = new THREE.PerspectiveCamera(50, 1, 0.05, 40000);
const orbFilm = new OrbitControls(camFilm, lienzo); orbFilm.enableDamping = true; orbFilm.maxDistance = 6000;
// Pellizcar en la tablet alejaba la cámara cientos de metros de un tirón: zoom suave y, en los
// proyectos (escala de personas), un tope de distancia. Cine sigue con su tope de dron.
orbFilm.zoomSpeed = 0.5; orbFilm.minDistance = 0.3;
const orbLibre = new OrbitControls(camLibre, lienzo); orbLibre.enableDamping = true; orbLibre.enabled = false;
const ayudaCam = new THREE.CameraHelper(camFilm); ayudaCam.visible = false; AYUDAS.add(ayudaCam);
const gizmo = new TransformControls(camFilm, lienzo); gizmo.setSize(0.85); scene.add(gizmo);
const hemi = new THREE.HemisphereLight(0xbfd6ff, 0x5a5048, 0.6); scene.add(hemi);
let MODO = "cam";
const camActiva = () => MODO === "cam" ? camFilm : camLibre;
const orbActiva = () => MODO === "cam" ? orbFilm : orbLibre;

%%COMUN%%

// ── ESTADO ──
let PEL = null, ELENCO = [], ELENCO_ID = [], ESC = null, SEL = null, T = 0, KEYSEL = null;
let PUNTOS = [];          // Cine: sus puntos · Lumen: las tomas marcadas ({id, t, nombre})
const nomPunto = (p, i) => CID ? `Punto ${i + 1}: ${p.es || ""}` : (p.nombre || `Toma ${i + 1}`);
const OBJ = new Map();                  // id → THREE.Group del objeto
const nid = () => Math.random().toString(36).slice(2, 9);
const item = id => ESC.items.find(x => x.id === id);

// ── FORMAS SIMPLES (las mismas del set de siempre + básicas) ──
const M_ = c => new THREE.MeshStandardMaterial({color: c, roughness: 0.85});
const FORMAS = {piso: "Piso", mar: "Mar", isla: "Isla", arena: "Arena", palmera: "Palmera", arbol: "Árbol", roca: "Roca", sombrilla: "Sombrilla",
  reposera: "Reposera", cabana: "Cabaña", barco: "Velero", yate: "Yate", tabla_surf: "Tabla de surf", ola: "Ola", muelle: "Muelle", edificio: "Edificio",
  pared: "Pared", mesa: "Mesa", silla: "Silla", cama: "Cama", auto: "Auto", caja: "Caja", esfera: "Esfera", cilindro: "Cilindro", escalon: "Escalón", sol: "Sol (bola)"};
function forma(tipo, color){
  const g = new THREE.Group(); const add = (geo, mat, pos, rot) => { const m = new THREE.Mesh(geo, mat); if(pos) m.position.set(...pos); if(rot) m.rotation.set(...rot); g.add(m); return m; };
  const C = (def) => M_(color != null ? color : def);
  if(tipo === "piso"){ const m = add(new THREE.PlaneGeometry(400, 400), C(0xb9b4ab), [0, 0, 0], [-Math.PI / 2, 0, 0]); m.userData.piso = true; }
  else if(tipo === "mar"){ const m = add(new THREE.PlaneGeometry(4000, 4000), new THREE.MeshStandardMaterial({color: color != null ? color : 0x3a9cb8, roughness: 0.25, metalness: 0.1}), [0, -0.05, 0], [-Math.PI / 2, 0, 0]); m.userData.piso = true; }
  else if(tipo === "isla"){ const s = add(new THREE.SphereGeometry(30, 40, 16), C(0xe8d3a2), [0, -2.65, 0]); s.scale.set(1, 0.09, 1); const v = add(new THREE.SphereGeometry(16, 30, 12), M_(0x4f7d3a), [3, -1.67, -10]); v.scale.set(1, 0.12, 1); }
  else if(tipo === "arena") add(new THREE.BoxGeometry(40, 0.3, 14), C(0xe8d3a2), [0, 0.1, 0]);
  else if(tipo === "palmera"){ add(new THREE.CylinderGeometry(0.18, 0.3, 8, 8), M_(0x7a5a3a), [0.6, 4, 0], [0, 0, -0.15]); for(let i = 0; i < 7; i++){ const h = add(new THREE.ConeGeometry(0.5, 4, 4), C(0x3f7f3a), [1.2 + Math.cos(i) * 1.6, 8, Math.sin(i * 0.9) * 1.6]); h.rotation.set(Math.PI / 2.4, i * 0.9, 0); } }
  else if(tipo === "arbol"){ add(new THREE.CylinderGeometry(0.25, 0.35, 4, 8), M_(0x6a4a2a), [0, 2, 0]); add(new THREE.SphereGeometry(2.4, 14, 10), C(0x3f6f35), [0, 5.2, 0]); }
  else if(tipo === "roca"){ const r = add(new THREE.DodecahedronGeometry(1.4, 0), C(0x6f6a64), [0, 0.6, 0]); r.scale.set(1.2, 0.7, 1); }
  else if(tipo === "sombrilla"){ add(new THREE.CylinderGeometry(0.04, 0.04, 2.4, 6), M_(0xdddddd), [0, 1.2, 0]); add(new THREE.ConeGeometry(1.4, 0.6, 16), C(0xf2f2f2), [0, 2.5, 0]); }
  else if(tipo === "reposera"){ add(new THREE.BoxGeometry(0.7, 0.12, 1.9), C(0xffffff), [0, 0.35, 0]); add(new THREE.BoxGeometry(0.7, 0.12, 0.7), C(0xffffff), [0, 0.65, -0.8], [0.9, 0, 0]); }
  else if(tipo === "cabana"){ add(new THREE.BoxGeometry(4, 3, 4), C(0xb08a5a), [0, 1.5, 0]); add(new THREE.ConeGeometry(3.4, 2, 4), M_(0x8a6a3a), [0, 4, 0], [0, Math.PI / 4, 0]); }
  else if(tipo === "barco"){ add(new THREE.BoxGeometry(2.2, 1, 7), C(0xffffff), [0, 0.4, 0]); add(new THREE.CylinderGeometry(0.08, 0.08, 9, 6), M_(0xdddddd), [0, 5, 0]); add(new THREE.ConeGeometry(2, 7, 3), M_(0xf6f6f6), [0, 5, 0.6]); }
  else if(tipo === "yate"){ add(new THREE.BoxGeometry(6, 2.2, 24), C(0xf4f4f2), [0, 1.1, 0]); add(new THREE.ConeGeometry(3, 5, 4), C(0xf4f4f2), [0, 1.1, 14], [Math.PI / 2, Math.PI / 4, 0]); add(new THREE.BoxGeometry(4.5, 2, 10), M_(0xe8e8e6), [0, 3.2, -2]); add(new THREE.BoxGeometry(4.6, 0.6, 10.2), M_(0x30343a), [0, 3.4, -2]); }
  else if(tipo === "tabla_surf"){ const t = add(new THREE.CapsuleGeometry(0.28, 1.6, 4, 12), C(0xf6f2ea), [0, 0.12, 0], [Math.PI / 2, 0, 0]); t.scale.set(1, 1, 0.18); }
  else if(tipo === "ola") add(new THREE.CylinderGeometry(1.6, 1.6, 14, 24, 1, true, 0, Math.PI), new THREE.MeshStandardMaterial({color: color != null ? color : 0x4ab0c4, roughness: 0.3, side: THREE.DoubleSide}), [0, 0.8, 0], [0, 0, Math.PI / 2]);
  else if(tipo === "muelle") add(new THREE.BoxGeometry(2.5, 0.25, 30), C(0x9a7a52), [0, 0.6, 0]);
  else if(tipo === "edificio") add(new THREE.BoxGeometry(12, 20, 12), C(0xd8d2c8), [0, 10, 0]);
  else if(tipo === "pared") add(new THREE.BoxGeometry(6, 3, 0.2), C(0xe8e2d8), [0, 1.5, 0]);
  else if(tipo === "mesa"){ add(new THREE.BoxGeometry(1.4, 0.06, 0.8), C(0xa07a50), [0, 0.75, 0]); add(new THREE.BoxGeometry(0.08, 0.75, 0.08), C(0xa07a50), [0, 0.37, 0]); }
  else if(tipo === "silla"){ add(new THREE.BoxGeometry(0.45, 0.06, 0.45), C(0xa07a50), [0, 0.45, 0]); add(new THREE.BoxGeometry(0.45, 0.5, 0.06), C(0xa07a50), [0, 0.7, -0.2]); }
  else if(tipo === "cama"){ add(new THREE.BoxGeometry(1.6, 0.5, 2.1), C(0xf2efe8), [0, 0.25, 0]); add(new THREE.BoxGeometry(1.6, 1.1, 0.1), M_(0x8a7a6a), [0, 0.55, -1.05]); }
  else if(tipo === "auto"){ add(new THREE.BoxGeometry(1.8, 0.8, 4.3), C(0xb02a2a), [0, 0.6, 0]); add(new THREE.BoxGeometry(1.6, 0.6, 2.2), M_(0x222222), [0, 1.25, -0.2]); }
  else if(tipo === "esfera") add(new THREE.SphereGeometry(0.5, 32, 16), C(0xcccccc), [0, 0.5, 0]);
  else if(tipo === "cilindro") add(new THREE.CylinderGeometry(0.5, 0.5, 1, 32), C(0xcccccc), [0, 0.5, 0]);
  else if(tipo === "escalon") add(new THREE.BoxGeometry(2, 0.2, 0.35), C(0xd8d2c8), [0, 0.1, 0]);
  else if(tipo === "sol"){ const m = add(new THREE.SphereGeometry(40, 24, 12), new THREE.MeshBasicMaterial({color: 0xffd28a, fog: false}), [0, 60, 0]); m.userData.piso = true; }   // es el dibujo del sol: no tapa la luz
  else add(new THREE.BoxGeometry(1, 1, 1), C(0xcccccc), [0, 0.5, 0]);
  return g;
}

// ── MODELOS 3D (biblioteca y subidos) ──
const CACHE = new Map();
function cargarModelo(m){
  const clave = m.url;
  if(!CACHE.has(clave)) CACHE.set(clave, new Promise((ok, mal) => {
    const man = new THREE.LoadingManager();
    if(m.include) man.setURLModifier(u => { for(const [k, v] of Object.entries(m.include)) if(u.endsWith(k) || u.endsWith(encodeURI(k))) return v; return u; });
    const ext = (m.ext || "glb").toLowerCase();
    const fin = obj => {
      obj.traverse(o => { if(o.isMesh){ o.castShadow = true; o.receiveShadow = true; } });
      // escala: muchos .fbx vienen en centímetros (una silla de 90 "metros")
      const caja = new THREE.Box3().setFromObject(obj), tam = caja.getSize(new THREE.Vector3());
      const g = new THREE.Group(); g.add(obj);
      if(Math.max(tam.x, tam.y, tam.z) > 60) obj.scale.multiplyScalar(0.01);
      const c2 = new THREE.Box3().setFromObject(obj), cen = c2.getCenter(new THREE.Vector3());
      obj.position.x -= cen.x; obj.position.z -= cen.z; obj.position.y -= c2.min.y;
      ok(g);
    };
    if(ext === "fbx") new FBXLoader(man).load(clave, fin, undefined, mal);
    else if(ext === "obj") new OBJLoader(man).load(clave, fin, undefined, mal);
    else new GLTFLoader(man).load(clave, gl => fin(gl.scene), undefined, mal);
  }));
  return CACHE.get(clave).then(t => SkeletonUtils.clone(t));
}

// ── LUCES ──
function kelvin(k){       // temperatura de color → color (aprox. de Tanner Helland)
  const t = k / 100; let r, g, b;
  if(t <= 66){ r = 255; g = 99.47 * Math.log(t) - 161.12; b = t <= 19 ? 0 : 138.52 * Math.log(t - 10) - 305.04; }
  else { r = 329.7 * Math.pow(t - 60, -0.1332); g = 288.12 * Math.pow(t - 60, -0.0755); b = 255; }
  const c = v => Math.max(0, Math.min(255, v)) / 255; return new THREE.Color(c(r), c(g), c(b));
}
const LUZ_DEF = {sol: {k: 5600, int: 3, nombre: "Sol"}, foco: {k: 4500, int: 30, angulo: 30, nombre: "Foco"},
  lampara: {k: 3000, int: 15, nombre: "Lámpara"}, ventana: {k: 6500, int: 8, ancho: 2, alto: 1.5, nombre: "Ventana"}};
// El giro que hace que una luz (que alumbra hacia su -Z) apunte de "desde" a "hacia".
const _apunta = new THREE.PerspectiveCamera();
function apuntar(desde, hacia){ _apunta.position.set(...desde); _apunta.lookAt(new THREE.Vector3(...hacia)); return [_apunta.rotation.x, _apunta.rotation.y, _apunta.rotation.z].map(R); }
function luzObj(it){
  const g = new THREE.Group(), L = it.luz, col = kelvin(L.k || 5600);
  let luz;
  if(L.clase === "sol"){ luz = new THREE.DirectionalLight(col, L.int); const s = luz.shadow; s.mapSize.set(2048, 2048); s.camera.left = s.camera.bottom = -60; s.camera.right = s.camera.top = 60; s.camera.far = 2000; s.bias = -0.0004; s.normalBias = 0.02; }
  else if(L.clase === "foco"){ luz = new THREE.SpotLight(col, L.int, 0, RG(L.angulo || 30), 0.4, 2); luz.shadow.mapSize.set(1024, 1024); luz.shadow.bias = -0.0005; }
  else if(L.clase === "lampara"){ luz = new THREE.PointLight(col, L.int, 0, 2); luz.shadow.mapSize.set(1024, 1024); luz.shadow.bias = -0.0005; }
  else { luz = new THREE.RectAreaLight(col, L.int, L.ancho || 2, L.alto || 1.5); luz.rotation.y = Math.PI; }
  luz.castShadow = !!L.sombras && L.clase !== "ventana";
  g.add(luz);
  if(luz.target){ luz.target.position.set(0, 0, -10); g.add(luz.target); }
  // el ícono: una bolita y una flecha hacia donde alumbra (no sale en la foto)
  const ic = new THREE.Group(); ic.userData.icono = true;
  const mb = new THREE.MeshBasicMaterial({color: col, fog: false});
  ic.add(new THREE.Mesh(L.clase === "ventana" ? new THREE.PlaneGeometry(L.ancho || 2, L.alto || 1.5) : new THREE.SphereGeometry(L.clase === "sol" ? 1.2 : 0.18, 16, 10), mb));
  if(L.clase !== "lampara"){ const fl = new THREE.Mesh(new THREE.ConeGeometry(L.clase === "sol" ? 0.8 : 0.12, L.clase === "sol" ? 3 : 0.5, 10), mb); fl.rotation.x = -Math.PI / 2; fl.position.z = L.clase === "sol" ? -2.5 : -0.4; ic.add(fl); }
  g.add(ic); g.userData.luz = luz; return g;
}

// ── CIELOS ──
const CIELOS = {atardecer: [0xf3a46b, 300, 3500], amanecer: [0xf6c8c8, 300, 3500], mediodia: [0x8cc8f0, 400, 5000], nublado: [0xaab2ba, 200, 2500],
  noche: [0x101830, 200, 2500], estudio: [0x2a2830, 0, 0]};
const N_CIELO = {atardecer: "Atardecer", amanecer: "Amanecer", mediodia: "Mediodía", nublado: "Nublado", noche: "Noche", estudio: "Estudio"};
const rgbe = new RGBELoader(); const HDR = new Map();
async function aplicarCielo(){
  const c = ESC.cielo || {modo: "simple", simple: "atardecer"};
  if(c.modo === "hdri" && c.url){
    try{
      if(!HDR.has(c.url)) HDR.set(c.url, new Promise((ok, mal) => rgbe.load(c.url, t => { t.mapping = THREE.EquirectangularReflectionMapping; ok(t); }, undefined, mal)));
      const t = await HDR.get(c.url); scene.background = t; scene.environment = t; scene.fog = null; hemi.intensity = 0.35;
      scene.backgroundIntensity = c.brillo || 1; return;
    }catch(e){ aviso('<span class="mal">No pude cargar ese cielo.</span>'); }
  }
  const [col, a, b] = CIELOS[c.simple] || CIELOS.atardecer;
  scene.background = new THREE.Color(col); scene.environment = null; scene.fog = a ? new THREE.Fog(col, a, b) : null;
  hemi.color.set(col); hemi.intensity = c.simple === "noche" ? 0.25 : 0.7;
  $$("[data-cielo]").forEach(b => b.classList.toggle("on", b.dataset.cielo === c.simple && c.modo !== "hdri"));
}

// ── CONSTRUIR / APLICAR ──
async function construir(it){
  let g;
  if(it.tipo === "forma") g = forma(it.forma, it.color != null ? it.color : null);
  else if(it.tipo === "modelo"){ g = new THREE.Group(); try{ g.add(await cargarModelo(it.modelo)); }catch(e){ g.add(forma("caja")); aviso(`<span class="mal">No pude cargar "${esc(it.nombre)}" (${esc(e.message || e)}).</span>`, 6000); } }
  else if(it.tipo === "persona"){ const M = maniqui(); g = new THREE.Group(); scene.remove(M.raiz); M.raiz.userData.base = [0, 0, 0]; g.add(M.raiz); g.userData.M = M; pose(M, it.pose || "de_pie");
    const mat = GRIS.clone(); mat.color.setHex(it.color != null ? it.color : 0xd8b878); M.raiz.traverse(m => { if(m.isMesh){ m.material = mat; m.castShadow = true; } }); }
  else if(it.tipo === "luz") g = luzObj(it);
  else g = forma("caja");
  if(it.tipo === "forma") g.traverse(o => { if(o.isMesh){ o.castShadow = !o.userData.piso; o.receiveShadow = true; } });
  g.userData.id = it.id; scene.add(g); OBJ.set(it.id, g); ponerBase(it); return g;
}
function ponerBase(it){ const g = OBJ.get(it.id); if(!g) return; g.position.fromArray(it.pos); g.rotation.set(...it.rot); g.scale.fromArray(it.esc); }
async function reconstruir(){
  gizmo.detach(); for(const g of OBJ.values()) scene.remove(g); OBJ.clear(); PERSONAS.length = 0;
  for(const it of ESC.items) await construir(it);
  await aplicarCielo(); irA(T); pintarLista(); pintarProps(); pintarTL();
  if(SEL && OBJ.get(SEL)) gizmo.attach(OBJ.get(SEL)); else SEL = null;
}

// ── ANIMACIÓN (claves) ──
const lerp = (a, b, f) => a.map((v, i) => v + (b[i] - v) * f);
function trObj(it, t){
  const k = it.keys || []; if(!k.length) return null;
  if(t <= k[0].t) return k[0]; if(t >= k[k.length - 1].t) return k[k.length - 1];
  const i = k.findIndex((x, j) => t >= x.t && t < k[j + 1].t), a = k[i], b = k[i + 1], f = (t - a.t) / Math.max(1e-6, b.t - a.t);
  const qa = new THREE.Quaternion().setFromEuler(new THREE.Euler(...a.rot)), qb = new THREE.Quaternion().setFromEuler(new THREE.Euler(...b.rot));
  const e = new THREE.Euler().setFromQuaternion(qa.slerp(qb, f));
  return {pos: lerp(a.pos, b.pos, f), rot: [e.x, e.y, e.z], esc: lerp(a.esc, b.esc, f)};
}
function grupos(k){ const out = []; let g = []; k.forEach((x, i) => { if(i && x.corte){ out.push(g); g = []; } g.push(x); }); if(g.length) out.push(g); return out; }
function camEn(t){
  const k = ESC.camKeys; if(!k.length) return null;
  if(t <= k[0].t) return k[0];
  const gs = grupos(k); let g = gs[gs.length - 1];
  for(const x of gs) if(t < (gs[gs.indexOf(x) + 1] || [{t: Infinity}])[0].t){ g = x; break; }
  if(t <= g[0].t || g.length === 1) return g[0]; if(t >= g[g.length - 1].t) return g[g.length - 1];
  const i = g.findIndex((x, j) => t >= x.t && t < g[j + 1].t), a = g[i], b = g[i + 1], f = (t - a.t) / Math.max(1e-6, b.t - a.t);
  let cam, mira;
  if(g.length >= 3){ const u = (i + f) / (g.length - 1);
    cam = new THREE.CatmullRomCurve3(g.map(x => new THREE.Vector3(...x.cam)), false, "centripetal").getPoint(u).toArray();
    mira = new THREE.CatmullRomCurve3(g.map(x => new THREE.Vector3(...x.mira)), false, "centripetal").getPoint(u).toArray(); }
  else { cam = lerp(a.cam, b.cam, f); mira = lerp(a.mira, b.mira, f); }
  let q = null; if(a.q && b.q) q = new THREE.Quaternion(...a.q).slerp(new THREE.Quaternion(...b.q), f).toArray();
  return {cam, mira, q, lente: a.lente + (b.lente - a.lente) * f};
}
function ponerCam(c){
  if(!c) return; camFilm.position.fromArray(c.cam);
  if(c.q) camFilm.quaternion.fromArray(c.q); else camFilm.lookAt(new THREE.Vector3(...c.mira));
  orbFilm.target.fromArray(c.mira); lente(Math.round(c.lente || 35), true);
}
let CAM_SUCIA = false;
function irA(t, sinCam){
  T = Math.max(0, Math.min(ESC.dur, t));
  for(const it of ESC.items){ const tr = trObj(it, T); const g = OBJ.get(it.id); if(!g) continue; if(tr){ g.position.fromArray(tr.pos); g.rotation.set(...tr.rot); g.scale.fromArray(tr.esc); } }
  animarPersonas(T);
  if(!sinCam && !CEL.activo){ const c = camEn(T); if(c){ ponerCam(c); CAM_SUCIA = false; } }
  $("#tiempo").textContent = T.toFixed(1) + " s / " + ESC.dur + " s"; pintarCabezal();
}

// ── CÁMARA ──
function lente(mm, sinGuardar){
  // sensor full frame 36×24 mm; en 9:16 el lado largo (36) es el vertical
  // el lado largo del cuadro es el de 36 mm (vertical en 9:16, horizontal en 16:9)
  mm = Math.max(12, Math.min(200, +mm)); camFilm.fov = 2 * GR(Math.atan((ASPECT < 1 ? 36 : 36 / ASPECT) / (2 * mm))); camFilm.updateProjectionMatrix(); ayudaCam.update();
  $("#lenteV").textContent = mm; $("#lente").value = mm;
  $("#lenteTxt").textContent = mm <= 20 ? "gran angular" : mm <= 30 ? "angular" : mm <= 60 ? "normal" : mm <= 105 ? "retrato" : "teleobjetivo";
  if(!sinGuardar) CAM_SUCIA = true;
}
$("#lente").oninput = () => lente(+$("#lente").value);
$$("[data-mm]").forEach(b => b.onclick = () => lente(+b.dataset.mm));
orbFilm.addEventListener("change", () => { if(MODO === "cam" && !REPRO && !CEL.activo) CAM_SUCIA = true; clearTimeout(infoCam.t); infoCam.t = setTimeout(infoCam, 120); });
function infoCam(){ if(!ESC) return; const lejos = !CID && camFilm.position.distanceTo(orbFilm.target) > 40;
  $("#camInfo").innerHTML = `Altura: <b>${camFilm.position.y.toFixed(2)} m</b> · a <b>${camFilm.position.distanceTo(orbFilm.target).toFixed(1)} m</b> de lo que mira`
    + (lejos ? ' · <span class="mal">muy lejos: tocá 🎯 Encuadrar</span>' : "") + (CAM_SUCIA && ESC && ESC.camKeys.length ? ' · <span class="mal">cambiaste la cámara: tocá ◆ para guardarla en este segundo</span>' : ""); }
function modo(m){
  MODO = m; $("#vCam").classList.toggle("on", m === "cam"); $("#vLibre").classList.toggle("on", m === "libre");
  orbFilm.enabled = m === "cam" && !CEL.activo; orbLibre.enabled = m === "libre"; gizmo.camera = camActiva(); ayudaCam.visible = m === "libre";
  if(m === "libre" && !modo.ya){ modo.ya = true; const b = new THREE.Box3(); for(const g of OBJ.values()) if(!g.userData.grande) b.expandByObject(g);
    const c = b.isEmpty() ? new THREE.Vector3() : b.getCenter(new THREE.Vector3()), s = Math.min(400, b.isEmpty() ? 20 : b.getSize(new THREE.Vector3()).length());
    camLibre.position.set(c.x + s * 0.7, c.y + s * 0.5 + 3, c.z + s * 0.7); orbLibre.target.copy(c); }
  $("#tag").textContent = m === "cam" ? "🎥 Por la cámara de la película" : "👁 Desde afuera (la cámara es la pirámide)"; medir();
}
$("#vCam").onclick = () => modo("cam"); $("#vLibre").onclick = () => modo("libre");

// ── ELEGIR Y MOVER ──
function elegir(id){
  SEL = id || null; KEYSEL = null; gizmo.detach(); const g = SEL && OBJ.get(SEL);
  if(g && !(item(SEL) || {}).bloqueado) gizmo.attach(g);
  pintarLista(); pintarProps(); pintarTL();
}
function herramienta(h){ gizmo.setMode(h); $("#hMover").classList.toggle("on", h === "translate"); $("#hGirar").classList.toggle("on", h === "rotate"); $("#hEscala").classList.toggle("on", h === "scale"); }
$("#hMover").onclick = () => herramienta("translate"); $("#hGirar").onclick = () => herramienta("rotate"); $("#hEscala").onclick = () => herramienta("scale");
gizmo.addEventListener("dragging-changed", e => { orbActiva().enabled = !e.value && !(MODO === "cam" && CEL.activo); if(!e.value) soltar(); });
function leerTr(g){ return {pos: g.position.toArray().map(R), rot: [g.rotation.x, g.rotation.y, g.rotation.z].map(R), esc: g.scale.toArray().map(R)}; }
function soltar(){
  const it = SEL && item(SEL), g = SEL && OBJ.get(SEL); if(!it || !g) return;
  const tr = leerTr(g);
  if((it.keys || []).length){ claveObjeto(it, tr); aviso(`◆ Clave de "${esc(it.nombre)}" guardada en ${T.toFixed(1)} s`); }
  else Object.assign(it, tr);
  cambio();
}
const ray = new THREE.Raycaster(), ptr = new THREE.Vector2(); let abajo = null;
lienzo.addEventListener("pointerdown", e => { abajo = [e.clientX, e.clientY]; });
lienzo.addEventListener("pointerup", e => {
  if(REPRO || !abajo || Math.hypot(e.clientX - abajo[0], e.clientY - abajo[1]) > 5 || gizmo.dragging) return;
  const r = lienzo.getBoundingClientRect(); ptr.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ptr, camActiva());
  const metas = []; for(const g of OBJ.values()) g.traverse(m => { if(m.isMesh && m.visible) metas.push(m); });
  const hit = ray.intersectObjects(metas, false).find(h => { let o = h.object; while(o && !o.userData.id) o = o.parent; return o && !(item(o.userData.id) || {}).bloqueado; });
  let o = hit && hit.object; while(o && !o.userData.id) o = o.parent; elegir(o ? o.userData.id : null);
});
const marco = new THREE.BoxHelper(new THREE.Object3D(), 0xd8b878); AYUDAS.add(marco);

// ── DESHACER Y GUARDAR ──
const PILA = []; let tG = null;
function foto(){ return JSON.stringify(ESC); }
function cambio(sinPila){
  if(!sinPila){ PILA.push(cambio.ultimo || foto()); if(PILA.length > 60) PILA.shift(); }
  cambio.ultimo = foto(); pintarLista(); pintarProps(); pintarTL(); guardar();
}
$("#deshacer").onclick = async () => { if(!PILA.length) return aviso("No hay nada para deshacer."); ESC = JSON.parse(PILA.pop()); cambio.ultimo = foto(); await reconstruir(); guardar(); };
function puntosDeClaves(){
  const out = {};
  for(const p of PUNTOS){ const c = camEn(p.t); if(c) out[p.id] = {cam: c.cam.map(R), mira: c.mira.map(R), lente: Math.round(c.lente)}; }
  return out;
}
function guardar(){ clearTimeout(tG); $("#guardado").textContent = "…"; tG = setTimeout(async () => {
  const objetos = ESC.items.filter(x => x.tipo === "forma" && OBJETOS_CINE.includes(x.forma)).map(x => ({tipo: x.forma, x: x.pos[0], z: x.pos[2], escala: x.esc[0], giro: R(GR(x.rot[1]))}));
  const personajes = ESC.items.filter(x => x.tipo === "persona").map(x => ({i: x.i, x: x.pos[0], y: x.pos[1], z: x.pos[2], giro: R(GR(x.rot[1])), pose: x.pose || "de_pie"}));
  const body = {escena3d: ESC, objetos, personajes, puntos: puntosDeClaves(), luz: (ESC.cielo || {}).simple || "atardecer"};
  const url = CID ? `${CINE_API}/${CID}/set` : `${API}/proyectos/${PID}`;
  const cams = puntosDeClaves();
  const cuerpo = CID ? body : {escena3d: ESC, tomas: PUNTOS.map(p => ({id: p.id, nombre: p.nombre, t: p.t, cam: cams[p.id]}))};
  try{ const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(cuerpo)});
    $("#guardado").innerHTML = r.ok ? '<span class="bien">✓ guardado</span>' : '<span class="mal">no se guardó</span>'; }
  catch(e){ $("#guardado").innerHTML = '<span class="mal">sin conexión</span>'; }
}, 700); }

// ── AGREGAR ──
function centroVista(){ const c = camActiva(), d = new THREE.Vector3(); c.getWorldDirection(d);
  const p = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), r = new THREE.Ray(c.position, d), out = new THREE.Vector3();
  return (r.intersectPlane(p, out) && out.distanceTo(c.position) < 80) ? out : c.position.clone().add(d.multiplyScalar(6)).setY(0); }
async function agregar(it, enCentro){
  it.id = it.id || nid(); it.rot = it.rot || [0, 0, 0]; it.esc = it.esc || [1, 1, 1]; it.keys = it.keys || [];
  if(!it.pos){ const c = enCentro === false ? new THREE.Vector3() : centroVista(); it.pos = [R(c.x), 0, R(c.z)]; }
  ESC.items.push(it); await construir(it); irA(T, true); cambio(); elegir(it.id);
}
$("#formas").innerHTML = Object.entries(FORMAS).map(([k, n]) => `<button data-forma="${k}">${n}</button>`).join("");
$$("[data-forma]").forEach(b => b.onclick = () => agregar({tipo: "forma", forma: b.dataset.forma, nombre: FORMAS[b.dataset.forma]}, !["piso", "mar"].includes(b.dataset.forma)));
$$("[data-luz]").forEach(b => b.onclick = () => { const c = b.dataset.luz, d = LUZ_DEF[c]; const v = centroVista();
  const it = {tipo: "luz", nombre: d.nombre, luz: {clase: c, k: d.k, int: d.int, angulo: d.angulo, ancho: d.ancho, alto: d.alto, sombras: c !== "ventana"}};
  if(c === "sol"){ it.pos = [R(v.x + 30), 60, R(v.z + 30)]; it.rot = apuntar(it.pos, [v.x, 0, v.z]); }
  else { it.pos = [R(v.x + 2), c === "lampara" ? 2.2 : 3, R(v.z + 2)]; it.rot = apuntar(it.pos, [v.x, 1, v.z]); }
  agregar(it); });
$$("[data-tab]").forEach(b => b.onclick = () => { $$("[data-tab]").forEach(x => x.classList.toggle("on", x === b)); $$("[data-pan]").forEach(p => p.style.display = p.dataset.pan === b.dataset.tab ? "" : "none");
  if(b.dataset.tab === "biblio") biblio(); if(b.dataset.tab === "cielo") hdris(); });
$("#expo").oninput = () => { renderer.toneMappingExposure = +$("#expo").value; $("#expoV").textContent = (+$("#expo").value).toFixed(2); ESC.expo = +$("#expo").value; };
$("#expo").onchange = () => cambio();

// Biblioteca (Poly Haven)
const TH = id => `https://cdn.polyhaven.com/asset_img/thumbs/${id}.png?width=200&height=200`;
let LIB = null, LIBN = 30;
async function biblio(){
  if(!LIB){ $("#bGrilla").innerHTML = '<span class="hint"><span class="spin"></span> Cargando la biblioteca…</span>';
    try{ const r = await fetch(`${API}/ph/lista?tipo=models`); const d = await r.json(); if(!r.ok) throw new Error(d.detail || r.status); LIB = d.items; }
    catch(e){ $("#bGrilla").innerHTML = `<span class="mal">No pude abrir la biblioteca: ${esc(e.message)}</span>`; return; } }
  const q = $("#bBuscar").value.trim().toLowerCase();
  const xs = LIB.filter(x => !q || (x.nombre + " " + x.tags.join(" ") + " " + x.cat.join(" ")).toLowerCase().includes(q));
  $("#bGrilla").innerHTML = xs.slice(0, LIBN).map(x => `<div class="it" data-ph="${esc(x.id)}" title="${esc(x.nombre)}"><img loading="lazy" src="${TH(x.id)}">${esc(x.nombre)}</div>`).join("") || '<span class="hint">Nada con esa palabra (probá en inglés: sofa, lamp, plant).</span>';
  $("#bMas").innerHTML = xs.length > LIBN ? '<button id="bVer">Ver más</button>' : ""; if($("#bVer")) $("#bVer").onclick = () => { LIBN += 30; biblio(); };
  $$("[data-ph]").forEach(d => d.onclick = async () => { aviso(`<span class="spin"></span> Trayendo "${esc(d.title)}"…`, 20000);
    try{ const r = await fetch(`${API}/ph/archivos/${d.dataset.ph}`); const f = await r.json(); if(!r.ok) throw new Error(f.detail || r.status);
      await agregar({tipo: "modelo", nombre: d.title, modelo: {fuente: "ph", ph: d.dataset.ph, url: f.gltf, ext: "gltf", include: f.include}}); aviso(`✓ ${esc(d.title)}`); }
    catch(e){ aviso(`<span class="mal">No pude traerlo: ${esc(e.message)}</span>`, 6000); } });
}
let tB = null; $("#bBuscar").oninput = () => { clearTimeout(tB); tB = setTimeout(() => { LIBN = 30; biblio(); }, 300); };
let HL = null, HN = 18;
async function hdris(){
  if(!HL){ $("#hGrilla").innerHTML = '<span class="hint"><span class="spin"></span> Cargando…</span>';
    try{ const r = await fetch(`${API}/ph/lista?tipo=hdris`); const d = await r.json(); if(!r.ok) throw new Error(d.detail || r.status); HL = d.items; }
    catch(e){ $("#hGrilla").innerHTML = `<span class="mal">No pude abrir los cielos: ${esc(e.message)}</span>`; return; } }
  $("#hGrilla").innerHTML = HL.slice(0, HN).map(x => `<div class="it" data-hdri="${esc(x.id)}" title="${esc(x.nombre)}"><img loading="lazy" src="${TH(x.id)}">${esc(x.nombre)}</div>`).join("");
  $("#hMas").innerHTML = HL.length > HN ? '<button id="hVer">Ver más</button>' : ""; if($("#hVer")) $("#hVer").onclick = () => { HN += 18; hdris(); };
  $$("[data-hdri]").forEach(d => d.onclick = async () => { aviso(`<span class="spin"></span> Trayendo el cielo "${esc(d.title)}"…`, 20000);
    try{ const r = await fetch(`${API}/ph/archivos/${d.dataset.hdri}?tipo=hdris`); const f = await r.json(); if(!r.ok) throw new Error(f.detail || r.status);
      ESC.cielo = {modo: "hdri", url: f.hdr, nombre: d.title, simple: (ESC.cielo || {}).simple || "mediodia", brillo: 1}; await aplicarCielo(); cambio(); aviso(`✓ Cielo: ${esc(d.title)}`); }
    catch(e){ aviso(`<span class="mal">No pude traerlo: ${esc(e.message)}</span>`, 6000); } });
}
$("#cielosSimples").innerHTML = Object.entries(N_CIELO).map(([k, n]) => `<button data-cielo="${k}">${n}</button>`).join("");
$$("[data-cielo]").forEach(b => b.onclick = async () => { ESC.cielo = {modo: "simple", simple: b.dataset.cielo}; await aplicarCielo(); cambio(); });

// Subir
$("#archivo").onchange = async () => {
  const f = $("#archivo").files[0]; if(!f) return;
  $("#subEstado").innerHTML = `<span class="spin"></span> Subiendo ${esc(f.name)} (${(f.size / 1e6).toFixed(1)} MB)…`;
  try{ const r = await fetch(`${API}/${DUENO}/modelo?nombre=${encodeURIComponent(f.name)}`, {method: "POST", body: f}); const d = await r.json(); if(!r.ok) throw new Error(d.detail || r.status);
    await agregar({tipo: "modelo", nombre: d.nombre, modelo: {fuente: "subido", url: d.url, ext: d.ext}}); $("#subEstado").innerHTML = '<span class="bien">✓ Listo, está en la escena.</span>'; }
  catch(e){ $("#subEstado").innerHTML = `<span class="mal">${esc(e.message)}</span>`; }
  $("#archivo").value = "";
};

// ── LISTA Y PROPIEDADES ──
const ICO = {forma: "◼", modelo: "🪑", persona: "🧍", luz: "💡"};
function pintarLista(){
  $("#lista").innerHTML = ESC.items.map(x => `<div class="fila ${x.id === SEL ? "on" : ""}" data-it="${x.id}">${ICO[x.tipo] || "•"} ${esc(x.nombre)}${(x.keys || []).length ? " ◆" : ""}<small>${x.bloqueado ? "🔒" : ""}</small></div>`).join("") || '<div class="hint">Vacía: agregá cosas desde la izquierda.</div>';
  $$("[data-it]").forEach(d => d.onclick = () => elegir(d.dataset.it));
}
function campo(etq, html){ return `<label>${etq}</label>${html}`; }
// ── MOVER A LAS PERSONAS (fácil) ──
// Cada botón agrega claves desde el segundo actual: adelante y atrás según para dónde mira,
// de costado, hacia la cámara, giros. Mientras se traslada, el maniquí camina solo (pasos()).
const MOV = {dur: 2, dist: 1.5};
const ACCIONES = {adelante: "⬆ Camina adelante", atras: "⬇ Camina para atrás", izq: "⬅ A su izquierda", der: "➡ A su derecha",
  camara: "📷 Viene a la cámara", gira_izq: "↪ Gira a su izquierda", gira_der: "↩ Gira a su derecha", vuelta: "🔄 Se da vuelta", giro360: "🌀 Vuelta entera", quieta: "⏸ Se queda quieta"};
function moverPersona(it, acc){
  const g = OBJ.get(it.id); if(!g) return;
  const dur = Math.max(0.3, +$("#mDur").value || 2), dist = Math.max(0.1, +$("#mDist").value || 1.5); MOV.dur = dur; MOV.dist = dist;
  const t0 = R(T), ini = trObj(it, t0) || leerTr(g), p = ini.pos.slice(), yaw = ini.rot[1];
  const fwd = [Math.sin(yaw), Math.cos(yaw)], izq = [Math.cos(yaw), -Math.sin(yaw)];
  const fin = {pos: p.slice(), rot: ini.rot.slice(), esc: ini.esc.slice()}, medio = [];
  const mueve = v => { fin.pos[0] = R(p[0] + v[0] * dist); fin.pos[2] = R(p[2] + v[1] * dist); };
  const giro = (a, pasos) => { for(let k = 1; k < pasos; k++) medio.push({t: R(t0 + dur * k / pasos), pos: p.slice(), rot: [ini.rot[0], R(yaw + a * k / pasos), ini.rot[2]], esc: ini.esc.slice()});
    fin.rot[1] = R(yaw + a); };
  if(acc === "adelante") mueve(fwd); else if(acc === "atras") mueve(fwd.map(x => -x));
  else if(acc === "izq") mueve(izq); else if(acc === "der") mueve(izq.map(x => -x));
  else if(acc === "camara"){ const d = [camFilm.position.x - p[0], camFilm.position.z - p[2]], L = Math.hypot(...d) || 1, paso = Math.max(0, Math.min(dist, L - 1.2));
    const a = Math.atan2(d[0], d[1]); let da = a - yaw; while(da > Math.PI) da -= 2 * Math.PI; while(da < -Math.PI) da += 2 * Math.PI;
    fin.rot[1] = R(yaw + da); fin.pos[0] = R(p[0] + d[0] / L * paso); fin.pos[2] = R(p[2] + d[1] / L * paso);
    if(Math.abs(da) > 0.3) medio.push({t: R(t0 + Math.min(0.5, dur / 4)), pos: p.slice(), rot: fin.rot.slice(), esc: ini.esc.slice()}); }   // primero se gira, después camina
  else if(acc === "gira_izq") giro(Math.PI / 2, 2); else if(acc === "gira_der") giro(-Math.PI / 2, 2);
  else if(acc === "vuelta") giro(Math.PI, 2); else if(acc === "giro360") giro(2 * Math.PI, 4);
  const t1 = R(t0 + dur), quedan = (it.keys || []).filter(k => k.t > t0 + 0.01).length;
  if(!(it.keys || []).length) Object.assign(it, {pos: ini.pos.slice(), rot: ini.rot.slice(), esc: ini.esc.slice()});
  it.keys = (it.keys || []).filter(k => k.t < t0 - 0.01);
  it.keys.push({t: t0, pos: p.map(R), rot: ini.rot.map(R), esc: ini.esc.map(R)}, ...medio, {t: t1, ...fin}); it.keys.sort((a, b) => a.t - b.t);
  if(t1 > ESC.dur){ ESC.dur = Math.ceil(t1); if($("#durT")) $("#durT").value = ESC.dur; }
  irA(t1); cambio(); pintarTL(); pintarProps();
  aviso(`✓ ${esc(it.nombre)}: ${ACCIONES[acc].slice(2)} de ${t0.toFixed(1)} a ${t1.toFixed(1)} s.` + (quedan ? " (Se borró lo que tenía después.)" : "") + " Seguí con la próxima o tocá ▶.", 5000);
}
function poseEn(it, t){ let p = it.pose || "de_pie"; for(const k of it.poseKeys || []) if(k.t <= t + 1e-6) p = k.pose; return p; }
const CAMINAN = new Set(["de_pie", "mano_cadera", "camina", "presenta", "senala", "pelo", "bretel", "selfie"]);
function pasos(M, t, vel, atras){
  pose(M, "de_pie"); const ph = 2 * Math.PI * t * (0.85 + vel * 0.4) * (atras ? -1 : 1), s = Math.sin(ph), c = Math.cos(ph), A = Math.min(0.55, 0.3 + vel * 0.15);
  M.j.cadera_izq.rotation.x = -A * s; M.j.cadera_der.rotation.x = A * s;
  M.j.rodilla_izq.rotation.x = 0.55 * Math.max(0, -c); M.j.rodilla_der.rotation.x = 0.55 * Math.max(0, c);
  M.j.hombro_izq.rotation.x = 0.4 * A * s * 1.6; M.j.hombro_der.rotation.x = -0.4 * A * s * 1.6;
  M.j.codo_izq.rotation.x = -0.25; M.j.codo_der.rotation.x = -0.25; M.raiz.position.y = 0.025 * Math.abs(c);
}
function animarPersonas(t){
  for(const it of ESC.items){ if(it.tipo !== "persona") continue; const g = OBJ.get(it.id), M = g && g.userData.M; if(!M) continue;
    const pz = poseEn(it, t); let vel = 0, atras = false;
    if((it.keys || []).length > 1){ const a = trObj(it, t), b = trObj(it, t + 0.1); const dx = b.pos[0] - a.pos[0], dz = b.pos[2] - a.pos[2];
      vel = Math.hypot(dx, dz) / 0.1; atras = dx * Math.sin(a.rot[1]) + dz * Math.cos(a.rot[1]) < -0.001; }
    if(vel > 0.12 && CAMINAN.has(pz)){ pasos(M, t, vel, atras); M._camina = true; }
    else if(M._camina || M.pose !== pz){ pose(M, pz); M._camina = false; } }
}
function pintarProps(){
  const it = SEL && item(SEL); if(!it){ $("#props").innerHTML = '<h3>Propiedades</h3><div class="hint">Tocá algo en la escena o en la lista para verlo acá.</div>'; return; }
  const g = OBJ.get(it.id), tr = g ? leerTr(g) : it;
  const n3 = (k, v, paso) => `<div class="xyz">${v.map((x, i) => `<input type="number" step="${paso}" data-p="${k}" data-i="${i}" value="${k === "rot" ? R(GR(x)) : R(x)}">`).join("")}</div>`;
  let h = `<h3>${ICO[it.tipo] || ""} ${esc(it.nombre)}</h3>` + campo("Nombre", `<input id="pNom" value="${esc(it.nombre)}" style="width:100%">`)
    + campo("Posición (metros) X · Altura · Z", n3("pos", tr.pos, 0.1)) + campo("Giro (grados) X · Y · Z", n3("rot", tr.rot, 5));
  if(it.tipo !== "luz") h += campo(`Tamaño: <b>${R(tr.esc[0])}×</b>`, `<input type="range" id="pEsc" min="0.05" max="10" step="0.05" value="${tr.esc[0]}">`);
  if(it.tipo === "forma") h += campo("Color", `<input type="color" id="pCol" value="#${new THREE.Color(it.color != null ? it.color : 0xcccccc).getHexString()}">`);
  if(it.tipo === "persona") h += campo("Pose", `<select id="pPose" style="width:100%">${Object.entries(POSES).map(([k, [n]]) => `<option value="${k}" ${k === it.pose ? "selected" : ""}>${n}</option>`).join("")}</select>`)
    + campo("Color del maniquí", `<input type="color" id="pCol" value="#${new THREE.Color(it.color != null ? it.color : 0xd8b878).getHexString()}">`)
    + `<div class="mov"><h3>🚶 Que se mueva</h3><div class="hint">Arranca en el segundo <b>${T.toFixed(1)}</b> (mové la línea de tiempo para elegir otro). Cada acción sigue desde donde terminó la anterior.</div>
      <div class="xyz" style="margin:6px 0"><label style="margin:0">Dura <input id="mDur" type="number" min="0.5" max="20" step="0.5" value="${MOV.dur}" style="width:56px"> s</label><label style="margin:0">Distancia <input id="mDist" type="number" min="0.2" max="30" step="0.1" value="${MOV.dist}" style="width:56px"> m</label></div>
      <div>${Object.entries(ACCIONES).map(([k, n]) => `<button data-mv="${k}">${n}</button>`).join("")}</div>
      <label>🧍 Cambia de pose en este segundo</label><select id="mPose" style="width:100%"><option value="">— elegí la pose nueva —</option>${Object.entries(POSES).map(([k, [n]]) => `<option value="${k}">${n}</option>`).join("")}</select>
      ${(it.poseKeys || []).length ? `<div class="hint" style="margin-top:4px">${it.poseKeys.map((k, i) => `${k.t.toFixed(1)} s → ${esc(POSES[k.pose] ? POSES[k.pose][0] : k.pose)} <a href="#" data-pq="${i}">✕</a>`).join(" · ")}</div>` : ""}
      ${(it.keys || []).length || (it.poseKeys || []).length ? '<button id="mBorrar" style="margin-top:6px">↺ Borrar todo el movimiento</button>' : ""}</div>`;
  if(it.tipo === "luz"){ const L = it.luz;
    h += campo(`Color de la luz: <b>${L.k} K</b> <span class="hint">(${L.k < 3500 ? "cálida, de lámpara" : L.k < 5000 ? "neutra" : L.k < 7000 ? "luz de día" : "fría, de sombra"})</span>`, `<input type="range" id="pK" min="1800" max="12000" step="100" value="${L.k}">`)
      + campo(`Intensidad: <b>${L.int}</b>`, `<input type="range" id="pInt" min="0" max="${L.clase === "foco" ? 400 : L.clase === "sol" ? 15 : 100}" step="0.1" value="${L.int}">`)
      + (L.clase === "foco" ? campo(`Apertura: <b>${L.angulo}°</b>`, `<input type="range" id="pAng" min="5" max="80" step="1" value="${L.angulo}">`) : "")
      + (L.clase === "ventana" ? campo("Ancho · alto (m)", `<div class="xyz"><input type="number" id="pAn" step="0.1" value="${L.ancho}"><input type="number" id="pAl" step="0.1" value="${L.alto}"></div>`) : "")
      + (L.clase !== "ventana" ? `<label><input type="checkbox" id="pSom" ${L.sombras ? "checked" : ""}> Hace sombras</label>` : ""); }
  h += `<label><input type="checkbox" id="pBloq" ${it.bloqueado ? "checked" : ""}> 🔒 Bloquear (no se mueve sin querer)</label>`
    + `<div class="hint" style="margin-top:6px">${(it.keys || []).length ? `Animado: ${it.keys.length} claves. Lo que muevas queda como clave en el segundo actual.` : "Quieto. Para que se mueva en la película: ponelo en un lugar, tocá ◆ Clave del objeto, andá a otro segundo, movelo y otra ◆."}</div>`
    + ((it.keys || []).length ? '<button id="pSinAnim">Quitar la animación</button>' : "") + (it.tipo === "persona" || it.tipo === "modelo" || it.tipo === "forma" ? '<button id="pPiso">⬇ Apoyar en el piso</button>' : "");
  $("#props").innerHTML = h;
  $("#pNom").onchange = () => { it.nombre = $("#pNom").value.trim() || it.nombre; cambio(); };
  $$("[data-p]").forEach(inp => inp.onchange = () => { const g2 = OBJ.get(it.id); const k = inp.dataset.p, i = +inp.dataset.i, v = +inp.value;
    if(k === "pos") g2.position.setComponent(i, v); else { const r = [g2.rotation.x, g2.rotation.y, g2.rotation.z]; r[i] = RG(v); g2.rotation.set(...r); } soltar(); });
  if($("#pEsc")){ $("#pEsc").oninput = () => { const v = +$("#pEsc").value; OBJ.get(it.id).scale.set(v, v, v); }; $("#pEsc").onchange = () => soltar(); }
  if($("#pCol")) $("#pCol").onchange = async () => { it.color = parseInt($("#pCol").value.slice(1), 16); scene.remove(OBJ.get(it.id)); await construir(it); irA(T, true); elegir(it.id); cambio(); };
  if($("#pPose")) $("#pPose").onchange = () => { it.pose = $("#pPose").value; pose(OBJ.get(it.id).userData.M, it.pose); cambio(); };
  $$("[data-mv]").forEach(b => b.onclick = () => moverPersona(it, b.dataset.mv));
  if($("#mPose")) $("#mPose").onchange = () => { const v = $("#mPose").value; if(!v) return;
    it.poseKeys = (it.poseKeys || []).filter(k => Math.abs(k.t - T) > 0.05); it.poseKeys.push({t: R(T), pose: v}); it.poseKeys.sort((a, b) => a.t - b.t);
    irA(T, true); cambio(); pintarProps(); pintarTL(); aviso(`🧍 Desde ${T.toFixed(1)} s: ${esc(POSES[v][0])}`); };
  $$("[data-pq]").forEach(a => a.onclick = e => { e.preventDefault(); it.poseKeys.splice(+a.dataset.pq, 1); irA(T, true); cambio(); pintarProps(); });
  if($("#mBorrar")) $("#mBorrar").onclick = () => { if(!confirm("¿Borrar todo el movimiento y los cambios de pose de " + it.nombre + "?")) return;
    const k0 = (it.keys || [])[0]; if(k0) Object.assign(it, {pos: k0.pos, rot: k0.rot, esc: k0.esc}); it.keys = []; it.poseKeys = []; ponerBase(it); irA(T, true); cambio(); pintarProps(); pintarTL(); };
  const reluz = async () => { scene.remove(OBJ.get(it.id)); await construir(it); irA(T, true); elegir(it.id); cambio(); };
  if($("#pK")){ $("#pK").oninput = () => { it.luz.k = +$("#pK").value; OBJ.get(it.id).userData.luz.color.copy(kelvin(it.luz.k)); }; $("#pK").onchange = reluz; }
  if($("#pInt")){ $("#pInt").oninput = () => { it.luz.int = +$("#pInt").value; OBJ.get(it.id).userData.luz.intensity = it.luz.int; }; $("#pInt").onchange = () => { pintarProps(); cambio(); }; }
  if($("#pAng")){ $("#pAng").oninput = () => { it.luz.angulo = +$("#pAng").value; OBJ.get(it.id).userData.luz.angle = RG(it.luz.angulo); }; $("#pAng").onchange = () => { pintarProps(); cambio(); }; }
  if($("#pAn")) $("#pAn").onchange = $("#pAl").onchange = () => { it.luz.ancho = +$("#pAn").value; it.luz.alto = +$("#pAl").value; reluz(); };
  if($("#pSom")) $("#pSom").onchange = () => { it.luz.sombras = $("#pSom").checked; OBJ.get(it.id).userData.luz.castShadow = it.luz.sombras; cambio(); };
  $("#pBloq").onchange = () => { it.bloqueado = $("#pBloq").checked; elegir(it.id); cambio(); };
  if($("#pSinAnim")) $("#pSinAnim").onclick = () => { Object.assign(it, leerTr(OBJ.get(it.id))); it.keys = []; cambio(); };
  if($("#pPiso")) $("#pPiso").onclick = () => { const g2 = OBJ.get(it.id); g2.position.y = 0; const b = new THREE.Box3().setFromObject(g2); g2.position.y -= b.min.y; soltar(); };
}
$("#borrar").onclick = () => { if(!SEL) return; ESC.items = ESC.items.filter(x => x.id !== SEL); scene.remove(OBJ.get(SEL)); OBJ.delete(SEL); elegir(null); cambio(); };
$("#duplicar").onclick = async () => { const it = SEL && item(SEL); if(!it) return; const c = JSON.parse(JSON.stringify(it)); c.id = nid(); c.nombre = it.nombre + " (copia)";
  const g = OBJ.get(it.id); Object.assign(c, leerTr(g)); c.pos[0] = R(c.pos[0] + 1); c.keys = []; await agregar(c); };

// ── LÍNEA DE TIEMPO ──
const pista = $("#pista");
const xDe = t => (t / ESC.dur) * pista.clientWidth;
function pintarCabezal(){ $("#cabezal").style.left = xDe(T) + "px"; infoCam(); verRef(); }
// el cuadro de tu video más cercano al segundo actual, para comparar
function verRef(){ const R_ = PROY && PROY.ref; if(!R_ || !R_.length) return; let k = 0; R_.forEach((x, i) => { if(Math.abs(x.t - T) < Math.abs(R_[k].t - T)) k = i; });
  if(verRef.k !== k){ verRef.k = k; $("#refImg").src = `${API}/proyectos/${PID}/ref/${k}.jpg`; } $("#refTxt").textContent = `Tu video en ${R_[k].t} s · la escena en ${T.toFixed(1)} s`; }
function pintarTL(){
  if(!ESC) return; $("#dur").value = ESC.dur;
  const paso = ESC.dur > 60 ? 10 : ESC.dur > 20 ? 5 : 1;
  let h = ""; for(let s = 0; s <= ESC.dur; s += paso) h += `<span style="left:${xDe(s)}px">${s}s</span>`;
  h += PUNTOS.map((p, i) => `<div class="pto" style="left:${xDe(p.t)}px" data-pt="${p.id}" title="${esc(nomPunto(p, i))}">${i + 1}</div>`).join("");
  $("#regla").innerHTML = h;
  $("#flCam").innerHTML = '<span class="nom">🎥 cámara</span>' + ESC.camKeys.map((k, i) => `<div class="rombo ${KEYSEL && KEYSEL.c && KEYSEL.i === i ? "sel" : ""}" style="left:${xDe(k.t)}px" data-kc="${i}" title="${k.t.toFixed(1)} s · ${Math.round(k.lente)} mm"></div>`).join("");
  const it = SEL && item(SEL);
  $("#flObj").innerHTML = `<span class="nom">${it ? esc(it.nombre) : "objeto elegido"}</span>` + (it ? (it.keys || []).map((k, i) => `<div class="rombo ${KEYSEL && !KEYSEL.c && KEYSEL.i === i ? "sel" : ""}" style="left:${xDe(k.t)}px" data-ko="${i}" title="${k.t.toFixed(1)} s"></div>`).join("") : "");
  $$("[data-kc]").forEach(d => d.onpointerdown = e => { e.stopPropagation(); const i = +d.dataset.kc; KEYSEL = {c: true, i}; irA(ESC.camKeys[i].t); pintarTL(); });
  $$("[data-ko]").forEach(d => d.onpointerdown = e => { e.stopPropagation(); const i = +d.dataset.ko; KEYSEL = {c: false, i}; irA(item(SEL).keys[i].t); pintarTL(); });
  $$("[data-pt]").forEach(d => d.onpointerdown = e => { e.stopPropagation(); irA(PUNTOS.find(p => p.id === d.dataset.pt).t); });
  pintarTomas();
  pintarCabezal();
}
let arrastra = false;
const tDe = e => { const r = pista.getBoundingClientRect(); return Math.round(((e.clientX - r.left) / r.width) * ESC.dur * 10) / 10; };
pista.addEventListener("pointerdown", e => { if(REPRO) return; arrastra = true; KEYSEL = null; irA(tDe(e)); pintarTL(); });
window.addEventListener("pointermove", e => { if(arrastra) irA(tDe(e)); });
window.addEventListener("pointerup", () => { arrastra = false; });
$("#dur").onchange = () => { ESC.dur = Math.max(3, Math.min(180, Math.round(+$("#dur").value || ESC.dur))); irA(T); cambio(); };
function claveCamara(t, c){
  const k = {t: R(t), cam: c.cam.map(R), mira: c.mira.map(R), lente: Math.round(c.lente), q: c.q ? c.q.map(v => +v.toFixed(5)) : undefined};
  const i = ESC.camKeys.findIndex(x => Math.abs(x.t - k.t) < 0.05);
  if(i >= 0){ k.corte = ESC.camKeys[i].corte; ESC.camKeys[i] = k; } else { ESC.camKeys.push(k); ESC.camKeys.sort((a, b) => a.t - b.t); }
}
function camActual(){ const d = new THREE.Vector3(); camFilm.getWorldDirection(d); const dist = Math.max(1, camFilm.position.distanceTo(orbFilm.target));
  return {cam: camFilm.position.toArray(), mira: camFilm.position.clone().add(d.multiplyScalar(dist)).toArray(), lente: +$("#lente").value, q: camFilm.quaternion.toArray()}; }
$("#claveCam").onclick = () => { if(MODO !== "cam") modo("cam"); claveCamara(T, camActual()); CAM_SUCIA = false; cambio(); aviso(`◆ Cámara guardada en ${T.toFixed(1)} s`); };
function claveObjeto(it, tr){ it.keys = it.keys || []; const k = {t: R(T), ...tr}; const i = it.keys.findIndex(x => Math.abs(x.t - k.t) < 0.05);
  if(i >= 0) it.keys[i] = k; else { it.keys.push(k); it.keys.sort((a, b) => a.t - b.t); } }
$("#claveObj").onclick = () => { const it = SEL && item(SEL); if(!it) return aviso("Elegí un objeto primero."); claveObjeto(it, leerTr(OBJ.get(it.id))); cambio(); aviso(`◆ Clave de "${esc(it.nombre)}" en ${T.toFixed(1)} s`); };
$("#borrarClave").onclick = () => { if(!KEYSEL) return aviso("Tocá un rombo ◆ de la línea de tiempo para elegir qué clave borrar.");
  if(KEYSEL.c) ESC.camKeys.splice(KEYSEL.i, 1); else { const it = item(SEL); it.keys.splice(KEYSEL.i, 1); if(!it.keys.length) Object.assign(it, leerTr(OBJ.get(it.id))); }
  KEYSEL = null; cambio(); irA(T); };

// ── REPRODUCIR / VIDEO ──
let REPRO = false;
async function reproducir(video){
  if(REPRO){ REPRO = false; return; } REPRO = true; gizmo.detach(); AYUDAS.visible = false; iconos(false); $("#play").textContent = "⏸ Pausa";
  let rec = null, partes = [];
  if(video && lienzo.captureStream && window.MediaRecorder){ if(T >= ESC.dur - 0.1) T = 0; rec = new MediaRecorder(lienzo.captureStream(30), {mimeType: MediaRecorder.isTypeSupported("video/webm;codecs=vp9") ? "video/webm;codecs=vp9" : "video/webm", videoBitsPerSecond: 8e6});
    rec.ondataavailable = ev => { if(ev.data.size) partes.push(ev.data); }; rec.start(); }
  if(T >= ESC.dur - 0.05) T = 0;
  const t0 = performance.now() - T * 1000; const vistaPrevia = MODO; if(video) modo("cam");
  // El tiempo avanza con un reloj propio (no con el refresco de pantalla): si la pestaña queda
  // atrás —la compu quieta mientras filmás con el celular— el navegador frena el refresco y la
  // grabación quedaba con dos cuadros.
  await new Promise(fin => { const reloj = setInterval(() => { if(!REPRO){ clearInterval(reloj); return fin(); }
    const t = (performance.now() - t0) / 1000; irA(t); if(CEL.grabando) muestra(); if(t >= ESC.dur){ clearInterval(reloj); fin(); } }, 33); });
  if(rec){ await new Promise(r => { rec.onstop = r; rec.stop(); }); if(partes.length){ const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(partes, {type: "video/webm"})); a.download = "animatic.webm"; a.click(); } modo(vistaPrevia); }
  REPRO = false; AYUDAS.visible = true; iconos(true); $("#play").textContent = "▶ Reproducir"; if(SEL) elegir(SEL);
  if(CEL.grabando) finGrabacion();
}
$("#play").onclick = () => reproducir(false); $("#grabarVid").onclick = () => reproducir(true); $("#inicio").onclick = () => { REPRO = false; irA(0); };
function iconos(v){ for(const g of OBJ.values()) g.traverse(o => { if(o.userData.icono) o.visible = v; }); }

// ── GUÍAS DE LOS PUNTOS ──
// La foto de la cámara en un segundo: el lado largo a 1280 px, sin íconos ni ayudas.
function renderToma(t){
  irA(t); const [w, h] = ASPECT < 1 ? [Math.round(1280 * ASPECT), 1280] : [1280, Math.round(1280 / ASPECT)];
  renderer.setPixelRatio(1); renderer.setSize(w, h, false); renderer.render(scene, camFilm);
  return lienzo.toDataURL("image/jpeg", 0.9);
}
async function exportar(soloId){
  if(!ESC.camKeys.length) return aviso('Primero guardá al menos una cámara (◆) en la línea de tiempo.');
  if(!PUNTOS.length) return aviso('Marcá al menos una toma (🎞 Marcar toma acá) en la línea de tiempo.');
  clearTimeout(tG); await guardarYa();
  REPRO = false; gizmo.detach(); AYUDAS.visible = false; iconos(false); const mPrev = MODO, tPrev = T; modo("cam");
  const prev = renderer.getSize(new THREE.Vector2()); let ok = 0; const lista = PUNTOS.filter(p => !soloId || p.id === soloId);
  for(const [i, p] of lista.entries()){
    aviso(`<span class="spin"></span> ${CID ? "Guía del punto" : "Toma"} ${i + 1} de ${lista.length}…`, 60000);
    const imagen = renderToma(p.t), c = camEn(p.t);
    const r = CID ? await fetch(`${CINE_API}/${CID}/punto/${p.id}/guia`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({imagen})})
                  : await fetch(`${API}/proyectos/${PID}/toma/${p.id}`, {method: "POST", headers: {"Content-Type": "application/json"},
                      body: JSON.stringify({imagen, cam: c ? {cam: c.cam.map(R), mira: c.mira.map(R), lente: Math.round(c.lente)} : null})});
    if(r.ok){ ok++; if(!CID){ const d = await r.json(); p.img = true; p.url = d.url; } }
  }
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1)); renderer.setSize(prev.x, prev.y, true);
  AYUDAS.visible = true; iconos(true); modo(mPrev); irA(tPrev); if(!CID) guardar(); if(SEL) elegir(SEL); pintarTomas();
  aviso(CID ? `<span class="bien">✓ ${ok} guías guardadas. Volvé a Cine y tocá "🎨 Dibujar los puntos que faltan".</span>`
            : `<span class="bien">✓ ${ok} tomas guardadas. Ya las podés usar como guía en Fotos, Reels, Storyboard, Cine…</span>`, 9000);
  try{ if(window.opener) window.opener.postMessage({de: CID ? "cine_set" : "lumen"}, "*"); }catch(e){}
}
$("#usar").onclick = () => exportar();
async function guardarYa(){ clearTimeout(tG); tG = null; await new Promise(r => { guardar(); setTimeout(r, 900); }); }
// LAS TOMAS (proyectos de Lumen)
$("#marcar").onclick = () => { const nombre = prompt("¿Cómo se llama esta toma?", `Toma ${PUNTOS.length + 1}`); if(nombre === null) return;
  PUNTOS.push({id: nid(), t: R(T), nombre: nombre.trim() || `Toma ${PUNTOS.length + 1}`}); PUNTOS.sort((a, b) => a.t - b.t);
  if(!ESC.camKeys.length){ claveCamara(T, camActual()); aviso("🎞 Toma marcada (y la cámara quedó guardada en este segundo)."); } else aviso("🎞 Toma marcada.");
  cambio(); };
function pintarTomas(){
  if(CID) return;
  $("#tomasLista").innerHTML = PUNTOS.map((p, i) => `<div style="display:flex;gap:6px;align-items:center;margin-top:6px">
      <div data-ir="${p.id}" style="width:44px;height:${ASPECT < 1 ? 66 : 30}px;flex:none;border-radius:6px;cursor:pointer;background:#0f0e13 ${p.url ? `url('${p.url}') center/cover` : ""};border:1px solid var(--line)"></div>
      <div style="flex:1;min-width:0"><input data-nom="${p.id}" value="${esc(p.nombre)}" style="width:100%"><div class="hint">${p.t.toFixed(1)} s ${p.url ? "· ✓ imagen" : "· sin imagen"}</div></div>
      <button data-foto="${p.id}" title="Guardar la imagen de esta toma">📸</button>${p.url ? `<a href="${p.url}" download="${esc(p.nombre)}.jpg" title="Bajar">⬇</a>` : ""}<button data-qui="${p.id}" title="Borrar la toma">✕</button></div>`).join("")
    || '<div class="hint" style="margin-top:6px">Todavía no marcaste ninguna: andá al segundo que quieras y tocá <b>🎞 Marcar toma acá</b>.</div>';
  $$("[data-ir]").forEach(d => d.onclick = () => irA(PUNTOS.find(p => p.id === d.dataset.ir).t));
  $$("[data-nom]").forEach(d => d.onchange = () => { PUNTOS.find(p => p.id === d.dataset.nom).nombre = d.value.trim() || "Toma"; cambio(); });
  $$("[data-foto]").forEach(d => d.onclick = () => exportar(d.dataset.foto));
  $$("[data-qui]").forEach(d => d.onclick = () => { PUNTOS = PUNTOS.filter(p => p.id !== d.dataset.qui); cambio(); });
}

// ── EL CELULAR COMO CÁMARA ──
const CEL = {ws: null, conectado: false, activo: false, q0: null, off: null, p0: null, base: null, grabando: false, muestras: [], tg0: 0, ultimo: 0};
$("#celBtn").onclick = async () => {
  try{ const r = await fetch(`${API}/cel/nuevo`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({cid: DUENO})}); const d = await r.json();
    const q = qrcode(0, "M"); q.addData(d.url); q.make(); $("#qr").innerHTML = q.createSvgTag(5, 0); $("#celUrl").textContent = d.url; $("#mCel").style.display = "flex";
    conectarCel(d.code); }
  catch(e){ aviso(`<span class="mal">No pude crear el código: ${esc(e.message)}</span>`); }
};
$("#celCerrar").onclick = () => $("#mCel").style.display = "none";
function conectarCel(code){
  if(CEL.ws) try{ CEL.ws.close(); }catch(e){}
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + `${API}/cel/ws/${code}`); CEL.ws = ws;
  ws.onmessage = ev => { let m; try{ m = JSON.parse(ev.data); }catch(e){ return; } celMsg(m); };
  ws.onclose = () => { CEL.conectado = false; CEL.activo = false; $("#celEstado").textContent = ""; $("#celPanel").style.display = "none"; orbFilm.enabled = MODO === "cam"; };
}
const enviar = m => { try{ if(CEL.ws && CEL.ws.readyState === 1) CEL.ws.send(JSON.stringify(m)); }catch(e){} };
function celMsg(m){
  if(m.tipo === "conectado" && m.rol === "cel"){ CEL.conectado = true; $("#mCel").style.display = "none"; $("#celEstado").innerHTML = '<span class="bien">📱 conectado</span>'; $("#celPanel").style.display = ""; modo("cam"); aviso("📱 Celular conectado: movelo y la cámara se mueve igual."); }
  else if(m.tipo === "desconectado" && m.rol === "cel"){ CEL.conectado = false; CEL.activo = false; orbFilm.enabled = MODO === "cam"; $("#celEstado").innerHTML = '<span class="mal">📱 desconectado</span>'; }
  else if(m.tipo === "pose") celPose(m);
  else if(m.tipo === "centrar") CEL.q0 = null;
  else if(m.tipo === "grabar") m.on ? inicioGrabacion() : (REPRO = false);
  else if(m.tipo === "lente") lente(m.mm);
  else if(m.tipo === "ir") irA(m.t || 0);
}
$("#centrar").onclick = () => { CEL.q0 = null; };
$("#escMov").oninput = () => $("#escV").textContent = $("#escMov").value;
function celPose(m){
  if(!CEL.activo){ CEL.activo = true; orbFilm.enabled = false; if(MODO !== "cam") modo("cam"); }
  const qc = new THREE.Quaternion(...m.q);
  if(!CEL.q0){
    // CENTRAR: lo que apunta el celular ahora = lo que mira la cámara. Sólo se corrige el
    // rumbo (yaw): la inclinación y el giro siguen siendo los reales del celular.
    const yawCam = new THREE.Euler().setFromQuaternion(camFilm.quaternion, "YXZ").y, yawCel = new THREE.Euler().setFromQuaternion(qc, "YXZ").y;
    CEL.off = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), yawCam - yawCel);
    CEL.q0 = qc.clone(); CEL.p0 = m.p ? new THREE.Vector3(...m.p) : null; CEL.base = camFilm.position.clone();
  }
  camFilm.quaternion.copy(CEL.off).multiply(qc);
  if(m.p && CEL.p0){ const d = new THREE.Vector3(...m.p).sub(CEL.p0).multiplyScalar(+$("#escMov").value).applyQuaternion(CEL.off); camFilm.position.copy(CEL.base).add(d); }
  if(m.mueve){ const [f, s, u] = m.mueve, dt = 1 / 30, v = 1.5 * +$("#escMov").value; const fw = new THREE.Vector3(); camFilm.getWorldDirection(fw); fw.y = 0; fw.normalize();
    const der = new THREE.Vector3().crossVectors(fw, new THREE.Vector3(0, 1, 0)); const delta = fw.multiplyScalar(f * v * dt).add(der.multiplyScalar(s * v * dt)).add(new THREE.Vector3(0, u * v * dt, 0));
    camFilm.position.add(delta); if(CEL.base) CEL.base.add(delta); }
  const d = new THREE.Vector3(); camFilm.getWorldDirection(d); orbFilm.target.copy(camFilm.position).add(d.multiplyScalar(10));
  if(m.mm && Math.round(m.mm) !== +$("#lente").value) lente(m.mm);
}
function inicioGrabacion(){
  if(REPRO) return; CEL.grabando = true; CEL.muestras = []; CEL.tg0 = T; aviso("⏺ Grabando el recorrido con el celular…", 2500); enviar({tipo: "estado", grabando: true});
  reproducir(false);
}
function muestra(){ const ya = performance.now(); if(ya - CEL.ultimo < 100) return; CEL.ultimo = ya; CEL.muestras.push({t: T, ...camActual()}); }
function finGrabacion(){
  CEL.grabando = false; enviar({tipo: "estado", grabando: false});
  const ms = CEL.muestras; if(ms.length < 2) return aviso("La grabación fue muy corta.");
  const t1 = ms[ms.length - 1].t;
  ESC.camKeys = ESC.camKeys.filter(k => k.t < CEL.tg0 - 0.01 || k.t > t1 + 0.01);
  let ult = -1; for(const m of ms) if(m.t - ult >= 0.25 || m === ms[ms.length - 1]){ claveCamara(m.t, m); ult = m.t; }
  cambio(); aviso(`<span class="bien">✓ Recorrido grabado: ${CEL.tg0.toFixed(1)} s a ${t1.toFixed(1)} s (${ms.length} cuadros). Tocá ▶ para verlo.</span>`, 7000);
}
// la vista de la cámara llega al celular (como el visor de una cámara de verdad)
const chico = document.createElement("canvas"); chico.width = 180; chico.height = 320;
setInterval(() => { if(!CEL.ws || CEL.ws.readyState !== 1 || !CEL.conectado || MODO !== "cam") return;
  try{ chico.getContext("2d").drawImage(lienzo, 0, 0, 180, 320); enviar({tipo: "visor", img: chico.toDataURL("image/jpeg", 0.55), t: T, dur: ESC.dur, grabando: CEL.grabando}); }catch(e){} }, 180);

// ── TECLADO ──
window.addEventListener("keydown", e => { if(/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  if(e.key === "w") herramienta("translate"); else if(e.key === "e") herramienta("rotate"); else if(e.key === "r") herramienta("scale");
  else if(e.key === "Delete" || e.key === "Backspace") $("#borrar").onclick(); else if(e.key === " "){ e.preventDefault(); reproducir(false); }
  else if((e.ctrlKey || e.metaKey) && e.key === "z") $("#deshacer").onclick(); });

// ── TAMAÑO ──
function medir(){ const caja = $("#escena").getBoundingClientRect(); let w, h;
  if(MODO === "cam"){ h = caja.height - 20; w = h * ASPECT; if(w > caja.width - 20){ w = caja.width - 20; h = w / ASPECT; } }
  else { w = caja.width - 20; h = caja.height - 20; camLibre.aspect = w / h; camLibre.updateProjectionMatrix(); }
  renderer.setSize(Math.floor(w), Math.floor(h), true); camFilm.aspect = ASPECT; camFilm.updateProjectionMatrix(); if(ESC) pintarTL(); }
window.addEventListener("resize", medir);
(function loop(){ requestAnimationFrame(loop); if(!REPRO){ orbActiva().update(); }
  ayudaCam.update(); if(SEL && OBJ.get(SEL) && !REPRO){ marco.setFromObject(OBJ.get(SEL)); marco.visible = true; } else marco.visible = false;
  renderer.render(scene, camActiva()); })();

// acceso para diagnóstico desde la consola (y para las pruebas)
window.SET3D = {get ESC(){ return ESC; }, get SEL(){ return SEL; }, get PEL(){ return PEL; }, get PUNTOS(){ return PUNTOS; }, OBJ, THREE, camFilm, orbFilm, irA, soltar, item, modo};

// ── ARRANQUE: la escena guardada, o la del set de siempre convertida ──
let OBJETOS_CINE = [];
function desdeViejo(P){
  const items = [{id: nid(), tipo: "forma", forma: "mar", nombre: "Mar", pos: [0, 0, 0], rot: [0, 0, 0], esc: [1, 1, 1], keys: [], bloqueado: true}];
  for(const o of P.objetos || []) items.push({id: nid(), tipo: "forma", forma: o.tipo, nombre: FORMAS[o.tipo] || o.tipo, pos: [o.x || 0, 0, o.z || 0], rot: [0, RG(o.giro || 0), 0], esc: Array(3).fill(o.escala || 1), keys: []});
  for(const x of P.personajes || []) items.push({id: nid(), tipo: "persona", i: x.i, nombre: ELENCO[x.i] || `Personaje ${x.i + 1}`, pose: x.pose || "de_pie", pos: [x.x || 0, x.y || 0, x.z || 0], rot: [0, RG(x.giro || 0), 0], esc: [1, 1, 1], keys: []});
  items.push({id: nid(), tipo: "luz", nombre: "Sol", luz: {clase: "sol", k: P.luz === "atardecer" ? 3200 : P.luz === "amanecer" ? 3800 : 5600, int: P.luz === "noche" ? 0.4 : 3, sombras: true}, pos: [-60, 50, -80], rot: apuntar([-60, 50, -80], [0, 0, 0]), esc: [1, 1, 1], keys: []});
  const camKeys = (P.puntos || []).filter(p => p.cam && p.mira).map(p => ({t: p.t, cam: p.cam, mira: p.mira, lente: p.lente || 35, corte: !!p.corte}));
  return {v: 1, items, cielo: {modo: "simple", simple: P.luz || "atardecer"}, camKeys, dur: Math.max(P.duracion || 10, ...(P.puntos || []).map(p => p.t + 2)), expo: 1};
}
// 🎯 La cámara vuelve a escala humana: a la altura de los ojos, lente 35 mm, desde donde
// estaba mirando (sólo cambia la distancia) y con lo elegido —o la primera persona— entero en cuadro.
function encuadrar(){
  const it = (SEL && item(SEL) && item(SEL).tipo !== "luz" && !(item(SEL).forma === "piso")) ? item(SEL) : ESC.items.find(x => x.tipo === "persona");
  const g = it && OBJ.get(it.id); const caja = new THREE.Box3();
  if(g) caja.setFromObject(g); if(!g || caja.isEmpty()) caja.set(new THREE.Vector3(-0.3, 0, -0.3), new THREE.Vector3(0.3, 1.75, 0.3));
  const c = caja.getCenter(new THREE.Vector3()), tam = caja.getSize(new THREE.Vector3());
  lente(35); const vfov = THREE.MathUtils.degToRad(camFilm.fov), alto = Math.max(tam.y, tam.x / ASPECT, 0.5) * 1.25;
  const dist = Math.min(30, Math.max(1.2, (alto / 2) / Math.tan(vfov / 2)));
  const dir = camFilm.position.clone().sub(orbFilm.target); dir.y = 0; if(dir.lengthSq() < 1e-6) dir.set(0, 0, 1); dir.normalize();
  orbFilm.target.copy(c); camFilm.position.set(c.x + dir.x * dist, Math.min(c.y + 0.45, 1.6), c.z + dir.z * dist);
  orbFilm.update(); CAM_SUCIA = true; infoCam(); aviso("🎯 Cámara encuadrada. Si te gusta, tocá ◆ para guardarla en este segundo.");
}
// ── FILMAR CON IA: el recorrido como guía de movimiento ──
let MOTORES = null, FJOB = null, FBOOK = {};
function personasEnOrden(t){
  // de izquierda a derecha como se ven en la cámara al arrancar: así el pedido dice "maniquí 1, 2…"
  irA(t); return ESC.items.filter(x => x.tipo === "persona").map(it => { const v = OBJ.get(it.id).position.clone().project(camFilm); return {it, x: v.x}; })
    .sort((a, b) => a.x - b.x).map(o => o.it);
}
async function abrirFilmar(){
  if(!ESC.camKeys.length) return aviso("Primero armá la cámara: guardala (◆) en al menos un segundo.");
  if(!MOTORES){ try{ MOTORES = await (await fetch(`${API}/motores`)).json(); }catch(e){ return aviso('<span class="mal">No pude ver los motores.</span>'); } }
  const ps = personasEnOrden(0), M = PROY.mundo || {}, lug = (MUNDO.lugares[M.lugar] || {}).n || "";
  const desc = [lug.replace(/^\S+\s/, ""), M.idea || ""].filter(Boolean).join(". ");
  const quien = it => `<select data-fp="${it.id}" style="width:100%"><option value="">Otra persona (la describo)</option>${ELENCO.map((n, i) => `<option value="${ELENCO_ID[i]}" ${it.i === i ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
    <input data-fd="${it.id}" placeholder="¿Quién es? ej: un chico de 25, rubio, remera blanca" style="width:100%;margin-top:4px;${it.i != null && ELENCO_ID[it.i] ? "display:none" : ""}">
    <select data-fr="${it.id}" style="width:100%;margin-top:4px"><option value="">Prenda: la de su look (sin prenda de Mis prendas)</option></select>
    <div style="margin-top:4px"><button data-fb="${it.id}">👁 Ver el book</button><label style="display:inline-block;margin:0;cursor:pointer" class="hint"><input type="file" accept="image/*" data-fu="${it.id}" style="display:none"><u>⬆ Usar mi book</u></label>
      <span class="hint" data-fbe="${it.id}"></span></div><div data-fbv="${it.id}"></div>`;
  $("#fCuerpo").innerHTML = `<div class="xyz"><label style="margin:0">Desde <input id="fT0" type="number" min="0" step="0.5" value="0" style="width:64px"> s</label>
      <label style="margin:0">hasta <input id="fT1" type="number" min="1" step="0.5" value="${Math.min(ESC.dur, MOTORES.max)}" style="width:64px"> s</label></div>
    <div class="hint">Entre ${MOTORES.min} y ${MOTORES.max} segundos por filmación (lo más largo, en partes).</div>
    <h3 style="margin-top:12px">Quién es cada uno <span class="hint">(de izquierda a derecha en cámara)</span></h3>
    ${ps.length ? ps.slice(0, MOTORES.max_personas).map((it, k) => `<div style="display:grid;grid-template-columns:24px 1fr;gap:6px;margin-top:6px"><b>${k + 1}</b><div>${quien(it)}</div></div>`).join("") : '<div class="hint">No hay personas: se filma sólo el lugar.</div>'}
    ${ps.length > MOTORES.max_personas ? `<div class="mal hint">Entran ${MOTORES.max_personas} personas con sus fotos; las demás salen como gente sin cara definida.</div>` : ""}
    <h3 style="margin-top:12px">📍 El lugar</h3><select id="fLug" style="width:100%"><option value="">Lo inventa la IA con la descripción</option></select>
    <h3 style="margin-top:12px">📹 Tu video original <span class="hint">(opcional: copia tu gesto real — manos, cadera, cabeza)</span></h3>
    <input type="file" id="fOrig" accept="video/*"><div class="hint">Se recorta al mismo tramo. Si el motor no acepta dos videos, filma sólo con el recorrido.</div>
    <h3 style="margin-top:12px">El lugar y la ropa</h3><textarea id="fDesc" rows="3" style="width:100%" placeholder="ej: playa de arena blanca al atardecer; ella con bikini turquesa, él con short negro">${esc(desc)}</textarea>
    <h3 style="margin-top:12px">Motor</h3><select id="fMot" style="width:100%">${Object.entries(MOTORES.motores).map(([k, v]) => `<option value="${k}">${esc(v.n)} · US$${v.precio}/s</option>`).join("")}</select>
    <p style="margin-top:14px"><button class="go" id="fGo">🎬 Grabar el recorrido y filmar</button> <span class="hint" id="fEst"></span></p>
    <div id="fEstado" class="hint"></div><div id="fRes"></div>
    ${(PROY.videos || []).length ? `<h3 style="margin-top:14px">Filmados</h3>` + PROY.videos.map(v => `<div class="hint">${new Date(v.ts * 1000).toLocaleString()} · ${esc(v.motor)} · ${v.seg} s · <a href="${API}/proyectos/${PID}/video/${v.id}.mp4" target="_blank" style="color:var(--rose-deep)">▶ ver / ⬇ bajar</a></div>`).join("") : ""}`;
  const est = () => { const d = Math.max(0, +$("#fT1").value - +$("#fT0").value), m = MOTORES.motores[$("#fMot").value];
    $("#fEst").textContent = `${d.toFixed(1)} s · ~US$${(d * m.precio).toFixed(2)}` + (d < MOTORES.min || d > MOTORES.max ? ` · ⚠ tiene que ser de ${MOTORES.min} a ${MOTORES.max} s` : ""); };
  ["#fT0", "#fT1", "#fMot"].forEach(k => $(k).oninput = $(k).onchange = est); est();
  const prendasDe = async sl => { const id = sl.dataset.fp, rs = $(`[data-fr="${id}"]`); $(`[data-fd="${id}"]`).style.display = sl.value ? "none" : "";
    rs.innerHTML = '<option value="">Prenda: la de su look (sin prenda de Mis prendas)</option>'; if(!sl.value) return;
    try{ const d = await (await fetch(`/referencias/api/prendas?pid=${encodeURIComponent(sl.value)}`)).json();
      rs.innerHTML += (d.prendas || []).map(x => (x.colores || []).map((c, ci) => `<option value="${esc(x.id)}|${ci}">${esc(x.nombre)}${c.nombre ? " · " + esc(c.nombre) : ""}${c.probador && c.probador.n ? (c.probador.aprobado ? " · probador ✓" : " · probador sin aprobar") : " · sin probador"}</option>`).join("")).join(""); }catch(e){} };
  $$("[data-fp]").forEach(sl => { sl.onchange = () => prendasDe(sl); prendasDe(sl); });
  FBOOK = {};
  $$("[data-fu]").forEach(inp => inp.onchange = async () => { const f = inp.files[0]; if(!f) return; const id = inp.dataset.fu;
    FBOOK[id] = await new Promise(ok => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(f); });
    $(`[data-fbv="${id}"]`).innerHTML = `<img src="${FBOOK[id]}" style="width:100%;border-radius:8px;margin-top:4px">`; $(`[data-fbe="${id}"]`).textContent = "✓ tu book"; });
  $$("[data-fb]").forEach(b => b.onclick = async () => { const id = b.dataset.fb, pj = $(`[data-fp="${id}"]`).value, pr = $(`[data-fr="${id}"]`).value;
    if(!pj) return aviso("Elegí el personaje (o subí su book).");
    $(`[data-fbe="${id}"]`).innerHTML = '<span class="spin"></span>';
    try{ const r = await fetch(`${API}/book`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({personaje: pj, prenda: pr ? {fid: pr.split("|")[0], ci: +pr.split("|")[1]} : null})});
      const d = await r.json(); if(!r.ok) throw new Error(d.detail || r.status);
      $(`[data-fbv="${id}"]`).innerHTML = `<img src="${d.book}" style="width:100%;border-radius:8px;margin-top:4px">`; $(`[data-fbe="${id}"]`).textContent = d.vistas.join(" · "); }
    catch(e){ $(`[data-fbe="${id}"]`).innerHTML = `<span class="mal">${esc(e.message)}</span>`; } });
  try{ const d = await (await fetch("/referencias/api/lugares")).json();
    $("#fLug").innerHTML += (d.lugares || []).filter(x => (x.vistas || []).length).map(x => `<option value="${esc(x.id)}">${esc(x.nombre || "Mi lugar")}</option>`).join(""); }catch(e){}
  $("#fGo").onclick = filmar;
  $("#filmar").style.display = "flex";
}
async function grabarRecorrido(t0, t1){
  // al tamaño del formato (lado corto 720 px: lo mínimo que piden los motores) y en tiempo real
  const mPrev = MODO; REPRO = true; gizmo.detach(); AYUDAS.visible = false; iconos(false); modo("cam");
  const [w, h] = ASPECT < 1 ? [720, Math.round(720 / ASPECT / 2) * 2] : [Math.round(720 * ASPECT / 2) * 2, 720];
  renderer.setPixelRatio(1); renderer.setSize(w, h, false); camFilm.aspect = ASPECT; camFilm.updateProjectionMatrix();
  const partes = [], rec = new MediaRecorder(lienzo.captureStream(30), {mimeType: MediaRecorder.isTypeSupported("video/webm;codecs=vp9") ? "video/webm;codecs=vp9" : "video/webm", videoBitsPerSecond: 8e6});
  rec.ondataavailable = ev => { if(ev.data.size) partes.push(ev.data); };
  irA(t0); renderer.render(scene, camFilm); rec.start(); const ini = performance.now();
  await new Promise(fin => { const reloj = setInterval(() => { const t = t0 + (performance.now() - ini) / 1000; irA(Math.min(t, t1)); renderer.render(scene, camFilm);
    $("#fEstado").textContent = `⏺ Grabando el recorrido… ${Math.max(0, t1 - t).toFixed(1)} s`; if(t >= t1){ clearInterval(reloj); fin(); } }, 33); });
  await new Promise(r => { rec.onstop = r; rec.stop(); });
  REPRO = false; AYUDAS.visible = true; iconos(true); renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1)); modo(mPrev); medir();
  return new Blob(partes, {type: "video/webm"});
}
async function filmar(){
  const t0 = Math.max(0, +$("#fT0").value), t1 = Math.min(ESC.dur, +$("#fT1").value), d = t1 - t0;
  if(d < MOTORES.min || d > MOTORES.max) return aviso(`La filmación tiene que durar entre ${MOTORES.min} y ${MOTORES.max} segundos.`);
  if(!(lienzo.captureStream && window.MediaRecorder)) return aviso('<span class="mal">Este navegador no puede grabar el recorrido: probá con Chrome.</span>', 6000);
  const m = MOTORES.motores[$("#fMot").value];
  if(!confirm(`¿Filmar ${d.toFixed(1)} s con ${m.n}? Cuesta ~US$${(d * m.precio).toFixed(2)}.`)) return;
  const personas = personasEnOrden(t0).slice(0, MOTORES.max_personas).map(it => { const pr = ($(`[data-fr="${it.id}"]`) || {}).value || "";
    return {personaje: ($(`[data-fp="${it.id}"]`) || {}).value || "", describe: ($(`[data-fd="${it.id}"]`) || {}).value || "",
            prenda: pr ? {fid: pr.split("|")[0], ci: +pr.split("|")[1]} : null, book: FBOOK[it.id] || ""}; });
  const datos = {motor: $("#fMot").value, personas, descripcion: $("#fDesc").value.trim(), lugar: $("#fLug").value, t0, t1};
  $("#fGo").disabled = true; await guardarYa();
  try{
    const video = await grabarRecorrido(t0, t1);
    $("#fEstado").textContent = "Subiendo el recorrido…";
    const fd = new FormData(); fd.append("video", video, "recorrido.webm"); fd.append("datos", JSON.stringify(datos));
    if($("#fOrig").files[0]) fd.append("original", $("#fOrig").files[0]);
    const r = await fetch(`${API}/proyectos/${PID}/filmar`, {method: "POST", body: fd}); const j = await r.json(); if(!r.ok) throw new Error(j.detail || r.status);
    const rf = j.referencias || {};
    $("#fEstado").textContent = `Enviado: ${rf.personas ? rf.personas.map((r, i) => `persona ${i + 1}: ${r.length ? r.join(" + ") : "descripta"}`).join(" · ") : ""}${rf.lugar ? " · tu lugar" : ""}${rf.video_original ? " · tu video original" : ""}`;
    FJOB = j.job; seguirFilmar(j.job);
  }catch(e){ $("#fEstado").innerHTML = `<span class="mal">${esc(e.message)}</span>`; $("#fGo").disabled = false; }
}
async function seguirFilmar(jid){
  const t0 = Date.now();
  for(;;){ let j; try{ j = await (await fetch(`${API}/filmar/${jid}`)).json(); }catch(e){ await new Promise(r => setTimeout(r, 5000)); continue; }
    if(FJOB !== jid) return;
    if(j.estado === "listo"){ $("#fEstado").textContent = "✓ ¡Filmado!" + (j.aviso ? " · " + j.aviso : ""); $("#fRes").innerHTML = `<video src="${j.url}" controls autoplay playsinline style="width:100%;max-height:60vh;border-radius:10px;margin-top:8px"></video><p><a href="${j.url}" download style="color:var(--rose-deep)">⬇ Bajar el video</a></p>`;
      $("#fGo").disabled = false; try{ PROY = (await (await fetch(`${API}/proyectos/${PID}`)).json()).proyecto; }catch(e){} return; }
    if(j.estado === "error"){ $("#fEstado").innerHTML = `<span class="mal">Falló: ${esc(j.error || "")}</span>`; $("#fGo").disabled = false; return; }
    $("#fEstado").textContent = `${j.paso || "Filmando…"} · ${Math.round((Date.now() - t0) / 1000)} s (suele tardar varios minutos: podés seguir trabajando)`;
    await new Promise(r => setTimeout(r, 6000)); }
}
$("#filmarBtn").onclick = abrirFilmar; $("#fCerrar").onclick = () => { $("#filmar").style.display = "none"; };

// ── EL MUNDO DEL ASISTENTE ──
// Arma la escena con las respuestas del asistente (PROY.mundo). La gente en el centro, la
// cámara del lado +Z, los objetos en abanico por detrás y a los costados, más lejos los grandes.
const MUNDO = %%MUNDO%%;
const RADIO = {palmera: 1.6, arbol: 2.6, roca: 1.6, sombrilla: 1.4, reposera: 1.1, cabana: 3, barco: 4, yate: 12, tabla_surf: 1,
  ola: 7, muelle: 15, edificio: 8, pared: 3, mesa: 0.9, silla: 0.4, cama: 1.3, auto: 2.4, caja: 0.6, esfera: 0.6, cilindro: 0.6, escalon: 0.8};
const COLORES = [0xd8b878, 0x9fd0e0, 0xe0a0b0, 0xb0e0a0, 0xc0b0f0, 0xf0c090];
function lugaresLibres(medio, suelo){
  // ángulos desde atrás (-Z), alternando izquierda y derecha; nunca del lado de la cámara
  const ang = [-50, 50, -88, 88, -22, 22, -68, 68, 0, -105, 105], usados = [];
  return r => { const k = usados.length, a = RG(ang[k % ang.length]), vuelta = Math.floor(k / ang.length);
    let d = medio + 1.2 + r + vuelta * 2.5 + (r >= 2.4 ? r * 1.5 + 2 : 0) + (r > 4 ? 6 : 0);   // lo grande, de fondo if(suelo > 0 && r < 2) d = Math.min(d, 2.4);
    usados.push(1); return [R(Math.sin(a) * d), R(-Math.cos(a) * d), R(GR(-a))]; };
}
function lucesDe(L, cielo, cx, cz, suelo){
  const items = [], m = {cielo};
  const noche = m.cielo === "noche";
  items.push({id: nid(), tipo: "luz", nombre: "Sol", luz: {clase: "sol", k: m.cielo === "atardecer" || m.cielo === "amanecer" ? 3800 : noche ? 7000 : 5600,
              int: noche ? 0.4 : m.cielo === "nublado" ? 1.6 : L.luces === "estudio" ? 1 : 3, sombras: true},
              pos: [30, m.cielo === "atardecer" || m.cielo === "amanecer" ? 14 : 45, 25], rot: apuntar([30, m.cielo === "atardecer" || m.cielo === "amanecer" ? 14 : 45, 25], [0, 0, 0]), esc: [1, 1, 1], keys: []});
  const luz = (clase, p, mira, extra) => { const d = LUZ_DEF[clase]; items.push({id: nid(), tipo: "luz", nombre: d.nombre, luz: Object.assign({clase, k: d.k, int: d.int, angulo: d.angulo, ancho: d.ancho, alto: d.alto, sombras: clase !== "ventana"}, extra || {}),
    pos: p, rot: apuntar(p, mira), esc: [1, 1, 1], keys: []}); };
  const blanco = [cx, suelo + 1.2, cz];
  if(L.luces === "estudio"){ luz("foco", [cx + 2.5, suelo + 3, cz + 2.5], blanco); luz("foco", [cx - 3, suelo + 2.2, cz + 2], blanco, {int: 12, sombras: false}); luz("foco", [cx, suelo + 3, cz - 2.5], blanco, {int: 15, k: 6500}); }
  if(L.luces === "interior" || noche) luz("lampara", [cx + 1.8, suelo + 2.2, cz + 1], blanco);
  if(L.luces === "interior" && !noche) luz("ventana", [cx - 3, suelo + 1.8, cz + 1.5], blanco);
  return items;
}
function fijosDe(m){
  const L = MUNDO.lugares[m.lugar] || MUNDO.lugares.vacio, items = [];
  for(const [f, x, y, z, giro, e] of L.fijos)
    items.push({id: nid(), tipo: "forma", forma: f, nombre: FORMAS[f] || f, pos: [x, y, z], rot: [0, RG(giro || 0), 0], esc: [e || 1, e || 1, e || 1], keys: [],
                bloqueado: ["piso", "mar", "arena", "isla"].includes(f), color: f === "piso" ? ({calle: 0x8d8b88, campo: 0x6f8a4a, estudio: 0xd9d6d0})[m.lugar] : undefined});
  return items;
}
// ── DESDE UN VIDEO O UNA FOTO ── la escena que armó Claude mirando la referencia (PROY.plan_video)
function escenaDeVideo(P, dur){
  const L = MUNDO.lugares[P.lugar] || MUNDO.lugares.vacio, suelo = L.suelo || 0, items = fijosDe({lugar: P.lugar});
  for(const o of P.objetos || []) items.push({id: nid(), tipo: "forma", forma: o.forma, nombre: FORMAS[o.forma] || o.forma, pos: [o.x, ["mar", "isla", "arena", "piso"].includes(o.forma) ? 0 : suelo, o.z],
    rot: [0, RG(o.giro || 0), 0], esc: [o.escala || 1, o.escala || 1, o.escala || 1], keys: []});
  (P.personas || []).forEach((p, k) => {
    const ks = p.keys, k0 = ks[0], tr = x => ({t: x.t, pos: [x.x, suelo, x.z], rot: [0, RG(x.giro || 0), 0], esc: [1, 1, 1]});
    // giros sin saltos: cada clave toma el camino corto desde la anterior
    let prev = k0.giro || 0; const giros = ks.map(x => { let g = x.giro || 0; while(g - prev > 180) g -= 360; while(g - prev < -180) g += 360; prev = g; return g; });
    const keys = ks.length > 1 ? ks.map((x, i) => tr({...x, giro: giros[i]})) : [];
    const poseKeys = []; let ult = k0.pose; for(const x of ks.slice(1)) if(x.pose !== ult){ poseKeys.push({t: x.t, pose: x.pose}); ult = x.pose; }
    items.push({id: nid(), tipo: "persona", i: null, nombre: p.ref ? p.ref.charAt(0).toUpperCase() + p.ref.slice(1) : `Persona ${k + 1}`, ref: p.ref,
                pose: k0.pose || "de_pie", poseKeys, color: COLORES[k % COLORES.length], pos: [k0.x, suelo, k0.z], rot: [0, RG(giros[0]), 0], esc: [1, 1, 1], keys});
  });
  const c0 = (P.personas || [])[0] ? P.personas[0].keys[0] : {x: 0, z: 0};
  items.push(...lucesDe(L, P.cielo, c0.x, c0.z, suelo));
  const camKeys = (P.camara || []).map(c => ({t: c.t, cam: [c.cam[0], c.cam[1] + suelo, c.cam[2]], mira: [c.mira[0], c.mira[1] + suelo, c.mira[2]], lente: c.lente}));
  return {v: 1, dur, expo: P.cielo === "noche" ? 1.3 : 1, cielo: {modo: "simple", simple: P.cielo}, camKeys, items};
}
function escenaDeMundo(m, dur){
  const L = MUNDO.lugares[m.lugar] || MUNDO.lugares.vacio, suelo = L.suelo || 0, items = fijosDe(m);
  // protagonistas
  const ps = m.protas || [], n = ps.length, pos = [];
  for(let k = 0; k < n; k++){
    let x = 0, z = 0, giro = 0;
    if(m.ronda === "ronda" && n > 1){ // en arco por detrás de un centro, todos mirando al centro (y la cámara los ve a todos)
      const r = Math.max(0.8, 0.34 * n), a = RG(-75 + 150 * k / (n - 1)); x = r * Math.sin(a); z = -r * Math.cos(a) + r * 0.5; giro = GR(Math.atan2(-x, r * 0.5 - z)); }
    else if(m.ronda === "profundidad" && n > 1){ x = (k % 2 ? 0.45 : -0.45) * Math.min(1, k); z = -k * 1.3; giro = 0; }
    else { x = (k - (n - 1) / 2) * 0.9; giro = n > 1 ? -x * 8 : 0; }
    pos.push([x, z]);
    const q = ps[k].quien;
    items.push({id: nid(), tipo: "persona", i: q >= 0 ? q : null, nombre: q >= 0 ? (ELENCO[q] || `Personaje ${q + 1}`) : (n > 1 ? `Persona ${k + 1}` : "Persona"),
                pose: ps[k].pose || "de_pie", color: COLORES[(q >= 0 ? q : k) % COLORES.length], pos: [R(x), suelo, R(z)], rot: [0, RG(giro), 0], esc: [1, 1, 1], keys: []});
  }
  const xs = pos.map(p => p[0]), zs = pos.map(p => p[1]);
  const cx = n ? (Math.min(...xs) + Math.max(...xs)) / 2 : 0, cz = n ? (Math.min(...zs) + Math.max(...zs)) / 2 : 0;
  const ancho = n ? Math.max(...xs) - Math.min(...xs) + 0.7 : 1.5, hondo = n ? Math.max(...zs) - Math.min(...zs) : 0;
  // objetos y modelos alrededor
  const libre = lugaresLibres(Math.max(ancho / 2, hondo / 2, 0.6), suelo);
  for(const f of m.objetos || []){
    if(f === "sol"){ items.push({id: nid(), tipo: "forma", forma: "sol", nombre: FORMAS.sol, pos: [-150, 0, -600], rot: [0, 0, 0], esc: [1, 1, 1], keys: []}); continue; }
    const r = RADIO[f] || 1, [x, z, g] = libre(r), grande = r >= 4;
    items.push({id: nid(), tipo: "forma", forma: f, nombre: FORMAS[f] || f, pos: [R(cx + x), grande ? 0 : suelo, R(cz + z)], rot: [0, RG(g), 0], esc: [1, 1, 1], keys: []});
  }
  const lugaresModelos = (m.modelos || []).map(() => libre(1.2));
  items.push(...lucesDe(L, m.cielo, cx, cz, suelo)); const noche = m.cielo === "noche";
  // la cámara
  const C = MUNDO.camaras[m.camara] || MUNDO.camaras.tripode, mm = C.lente;
  const sentados = n && ps.every(p => ["sentada", "rodillas", "acostada"].includes(p.pose));
  const plano = m.plano || "entero";
  const alto = {general: 5 + ancho, entero: sentados ? 1.6 : 2.25, medio: 1.15, primer: 0.48}[plano] || 2.25;
  const mitad = {general: 1.0, entero: sentados ? 0.6 : 0.95, medio: sentados ? 0.75 : 1.2, primer: sentados ? 0.95 : 1.55}[plano] || 1;
  const vs = ASPECT < 1 ? 36 : 36 / ASPECT, hs = vs * ASPECT;
  let dist = Math.max(alto * mm / vs, (plano === "primer" ? 0.4 : ancho + 0.5) * mm / hs, 0.8) + (plano === "general" ? hondo : hondo / 2);
  const mira = [R(cx), R(suelo + (n ? mitad : 1)), R(cz)];
  const h0 = suelo + Math.min(C.h, plano === "primer" ? mira[1] - suelo + 0.1 : C.h);
  const enA = (d, a, h) => [R(cx + Math.sin(RG(a)) * d), R(h), R(cz + Math.cos(RG(a)) * d)];
  const K = (t, cam, mi, l) => ({t: R(t), cam, mira: mi || mira, lente: l || mm});
  const keys = [], D = dur;
  switch(m.mov){
    case "acercarse": keys.push(K(0, enA(dist * 1.9, 0, h0)), K(D, enA(dist, 0, h0))); break;
    case "alejarse": keys.push(K(0, enA(dist, 0, h0)), K(D, enA(dist * 1.9, 0, h0))); break;
    case "alrededor": keys.push(K(0, enA(dist, -45, h0)), K(D / 2, enA(dist, 0, h0)), K(D, enA(dist, 45, h0))); break;
    case "lateral": keys.push(K(0, [R(cx - 1.8), R(h0), R(cz + dist)], [R(cx - 0.6), mira[1], mira[2]]), K(D, [R(cx + 1.8), R(h0), R(cz + dist)], [R(cx + 0.6), mira[1], mira[2]])); break;
    case "dron": keys.push(K(0, enA(dist + 45, 0, suelo + 38)), K(D * 0.75, enA(dist + 2, 0, h0 + 1.2)), K(D, enA(dist, 0, h0))); break;
    case "grua": keys.push(K(0, enA(dist, 0, suelo + 0.6)), K(D, enA(dist * 1.3, 0, suelo + 4.5))); break;
    default: keys.push(K(0, enA(dist, 0, h0)));
  }
  return {v: 1, dur, expo: noche ? 1.3 : 1, cielo: {modo: "simple", simple: m.cielo || L.cielo}, camKeys: keys, items,
          _modelos: (m.modelos || []).map((x, k) => ({ph: x.ph, nombre: x.nombre, pos: [R(cx + lugaresModelos[k][0]), suelo, R(cz + lugaresModelos[k][1])], giro: lugaresModelos[k][2]}))};
}
async function traerModelosDelMundo(){
  const lista = ESC._modelos || []; if(!lista.length) return;
  delete ESC._modelos; let ok = 0;
  for(const x of lista){ aviso(`<span class="spin"></span> Trayendo "${esc(x.nombre)}" (${ok + 1}/${lista.length})…`, 30000);
    try{ const r = await fetch(`${API}/ph/archivos/${encodeURIComponent(x.ph)}`); const f = await r.json(); if(!r.ok) throw new Error(f.detail || r.status);
      await agregar({tipo: "modelo", nombre: x.nombre, pos: x.pos, rot: [0, RG(x.giro || 0), 0], modelo: {fuente: "ph", ph: x.ph, url: f.gltf, ext: "gltf", include: f.include}}); ok++; }
    catch(e){ aviso(`<span class="mal">No pude traer "${esc(x.nombre)}": ${esc(e.message)}</span>`, 5000); } }
  elegir(null); irA(0); cambio(); aviso(`✓ El mundo está listo${ok ? ` (con ${ok} modelo${ok > 1 ? "s" : ""} de la biblioteca)` : ""}. Tocá ▶ para ver la cámara.`, 6000);
}
function escenaNueva(dur){
  return {v: 1, dur, expo: 1, cielo: {modo: "simple", simple: "mediodia"}, camKeys: [], items: [
    {id: nid(), tipo: "forma", forma: "piso", nombre: "Piso", pos: [0, 0, 0], rot: [0, 0, 0], esc: [1, 1, 1], keys: [], bloqueado: true},
    {id: nid(), tipo: "luz", nombre: "Sol", luz: {clase: "sol", k: 5600, int: 3, sombras: true}, pos: [30, 45, 25], rot: apuntar([30, 45, 25], [0, 0, 0]), esc: [1, 1, 1], keys: []},
    {id: nid(), tipo: "persona", i: null, nombre: "Persona", pose: "de_pie", color: 0xd8b878, pos: [0, 0, 0], rot: [0, 0, 0], esc: [1, 1, 1], keys: []}]};
}
(async () => {
  medir(); lente(35, true); herramienta("translate");
  if(!CID && !PID){ location.href = "%%PREF%%"; return; }
  try{
   if(CID){
    const r = await fetch(`${CINE_API}/${CID}/set`); const d = await r.json(); if(!r.ok) throw new Error(d.detail || "No encuentro la película.");
    PEL = d.pelicula; ELENCO = d.nombres || []; OBJETOS_CINE = d.objetos || []; PUNTOS = PEL.puntos;
    $("#proyNom").textContent = "🎞 Cine: " + (PEL.titulo || "la película"); $("#usar").textContent = "✓ Usar como guía de los puntos";
    ESC = (PEL.escena3d && PEL.escena3d.items) ? PEL.escena3d : desdeViejo(PEL);
    ESC.camKeys = ESC.camKeys || []; ESC.dur = ESC.dur || 10;
    // puntos nuevos del director que no tienen cámara en la escena: su cámara de siempre
    for(const p of PEL.puntos) if(p.cam && p.mira && !ESC.camKeys.some(k => Math.abs(k.t - p.t) < 0.05) && !(PEL.escena3d && PEL.escena3d.items)) claveCamara(p.t, {cam: p.cam, mira: p.mira, lente: p.lente || 35});
    renderer.toneMappingExposure = ESC.expo || 1; $("#expo").value = ESC.expo || 1; $("#expoV").textContent = (ESC.expo || 1).toFixed(2);
   } else {
    const r = await fetch(`${API}/proyectos/${PID}`); const d = await r.json(); if(!r.ok) throw new Error(d.detail || "No encuentro el proyecto.");
    PROY = d.proyecto; PUNTOS = PROY.tomas || []; ASPECT = ({"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1, "4:5": 4 / 5})[PROY.formato] || 9 / 16;
    $("#proyNom").textContent = `${PROY.nombre} · ${PROY.formato}`; document.title = `${PROY.nombre} · Lumen`;
    $("#marcar").style.display = ""; $("#tomasPanel").style.display = "";
    try{ const rp = await fetch("/personajes/api/lista"); if(rp.ok){ const L = (await rp.json()).personajes || []; ELENCO = L.map(x => x.nombre); ELENCO_ID = L.map(x => x.id); } }catch(e){}
    $("#filmarBtn").style.display = ""; if((PROY.ref || []).length) $("#refPanel").style.display = "";
    ESC = (PROY.escena3d && PROY.escena3d.items) ? PROY.escena3d : PROY.plan_video ? escenaDeVideo(PROY.plan_video, PROY.dur || 10)
      : PROY.mundo ? escenaDeMundo(PROY.mundo, PROY.dur || 10) : escenaNueva(PROY.dur || 10);
    renderer.toneMappingExposure = ESC.expo || 1; $("#expo").value = ESC.expo || 1; $("#expoV").textContent = (ESC.expo || 1).toFixed(2);
   }
    $("#gente").innerHTML = ELENCO.map((n, i) => `<button data-gente="${i}">＋ ${esc(n)}</button>`).join("") + '<button data-gente="-1">＋ Persona</button>';
    $$("[data-gente]").forEach(b => b.onclick = () => { const i = +b.dataset.gente; agregar({tipo: "persona", i: i >= 0 ? i : null, nombre: i >= 0 ? ELENCO[i] : "Persona", pose: "de_pie", color: [0xd8b878, 0x9fd0e0, 0xe0a0b0, 0xb0e0a0, 0xc0b0f0][Math.max(0, i) % 5]}); });
    cambio.ultimo = foto(); medir(); lente(35, true); await reconstruir(); irA(PUNTOS.length ? PUNTOS[0].t : 0); modo("cam");
    if(!CID){ orbFilm.maxDistance = 150; orbLibre.maxDistance = 400; }
    if(!CID && !ESC.camKeys.length){ camFilm.position.set(0, 1.6, 6); orbFilm.target.set(0, 1.1, 0); orbFilm.update(); }
    if(!CID && camFilm.position.distanceTo(orbFilm.target) > 150) encuadrar();     // quedó perdida de antes
    $("#encuadrar").onclick = () => { if(MODO !== "cam") modo("cam"); encuadrar(); };
    if(!CID && ESC._modelos) traerModelosDelMundo();
    else if(!CID && PROY.mundo && !(PROY.escena3d && PROY.escena3d.items)){ cambio();
      aviso(PROY.plan_video ? "✓ Armé el mundo copiando tu video: tocá ▶ y comparalo con el cuadro de la derecha. Corregí lo que haga falta." : "✓ El mundo está listo. Tocá ▶ para ver la cámara.", 7000); }
    $("#tag").textContent = CID ? `🎥 ${PEL.titulo || "La película"} · ${PUNTOS.length} puntos` : "🎥 Por la cámara";
  }catch(e){ $("#tag").innerHTML = `<span class="mal">${esc(e.message)}</span>`; }
})();
</script></body></html>
"""

PAGINA_CEL = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>📱 Cámara · Set 3D</title>
<style>""" + _ESTILO + r"""
  body{overflow:hidden;touch-action:none;-webkit-user-select:none;user-select:none}
  #visor{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:#000}
  #visor img{height:100%;max-width:100%;object-fit:contain;image-rendering:auto}
  .hud{position:fixed;left:0;right:0;padding:10px;display:flex;gap:6px;align-items:center;justify-content:center;flex-wrap:wrap}
  .arriba{top:0;background:linear-gradient(#000c,#0000)} .abajo{bottom:0;background:linear-gradient(#0000,#000c);padding-bottom:18px}
  .rec{width:64px;height:64px;border-radius:50%;border:4px solid #fff;background:#e0443e;padding:0;margin:0 14px} .rec.on{border-radius:14px;background:#fff}
  .pad{position:fixed;bottom:100px;width:120px;height:120px;border-radius:50%;background:#ffffff18;border:1px solid #fff4;touch-action:none}
  .pad .bola{position:absolute;left:40px;top:40px;width:40px;height:40px;border-radius:50%;background:#fff8}
  #padI{left:14px} #padD{right:14px}
  .etq{position:fixed;bottom:226px;font-size:11px;color:#fffa;width:120px;text-align:center} #etI{left:14px} #etD{right:14px}
  #inicio{position:fixed;inset:0;background:#131218f2;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:28px;text-align:center;z-index:5}
  #inicio button{font-size:17px;padding:14px 28px;margin-top:16px}
  #t{font-variant-numeric:tabular-nums}
</style></head><body>
<div id="visor"><img id="img" alt=""></div>
<div class="hud arriba"><span id="estado" class="hint">Conectando…</span><span id="t"></span>
  <button id="centrar">🎯 Centrar</button><button id="arBtn" style="display:none">🚶 Seguir mis pasos (AR)</button></div>
<div class="etq" id="etI">caminar</div><div class="etq" id="etD">subir / bajar</div>
<div class="pad" id="padI"><div class="bola"></div></div><div class="pad" id="padD"><div class="bola"></div></div>
<div class="hud abajo">
  <span class="hint">Zoom</span><input type="range" id="mm" min="12" max="200" value="35" style="width:120px"><b id="mmV">35</b>
  <button class="rec" id="rec" title="Grabar el recorrido"></button>
  <button id="cero">⏮ 0 s</button>
</div>
<div id="inicio">
  <h2 style="font-family:Georgia,serif;margin:0">📱 Tu celular es la cámara</h2>
  <p class="hint" style="font-size:14px">Sostené el celular como si filmaras. Movelo y girá: la cámara del set hace lo mismo. Con los círculos de abajo caminás y subís. <b>⏺</b> graba el recorrido mientras corre la película.</p>
  <button class="go" id="empezar">▶ Empezar</button>
  <p class="hint" id="permiso"></p>
</div>
<script>
const CODE = "%%CODE%%";
const $ = s => document.querySelector(s);
let ws = null, q = [0, 0, 0, 1], pXR = null, mueve = [0, 0, 0], mm = 35, grabando = false, ultimoEnvio = 0;
function conectar(){
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "%%WS%%");
  ws.onopen = () => $("#estado").textContent = "Esperando la computadora…";
  ws.onmessage = ev => { let m; try{ m = JSON.parse(ev.data); }catch(e){ return; }
    if(m.tipo === "visor"){ $("#img").src = m.img; $("#estado").textContent = "🎥 en vivo"; $("#t").textContent = `${(m.t || 0).toFixed(1)} / ${m.dur || 0} s`; grabando = !!m.grabando; $("#rec").classList.toggle("on", grabando); }
    else if(m.tipo === "conectado") $("#estado").textContent = "✓ Conectado con la computadora";
    else if(m.tipo === "desconectado") $("#estado").textContent = "La computadora se desconectó";
    else if(m.tipo === "estado"){ grabando = !!m.grabando; $("#rec").classList.toggle("on", grabando); } };
  ws.onclose = () => { $("#estado").textContent = "Reconectando…"; setTimeout(conectar, 1500); };
}
const enviar = m => { if(ws && ws.readyState === 1) ws.send(JSON.stringify(m)); };

// ORIENTACIÓN → cuaternión de cámara (la misma cuenta que DeviceOrientationControls de three.js)
function quatDeOrientacion(alpha, beta, gamma, orient){
  const d = Math.PI / 180, a = alpha * d, b = beta * d, g = -gamma * d, o = orient * d;
  // euler YXZ (b, a, g) → cuaternión
  const c1 = Math.cos(b / 2), c2 = Math.cos(a / 2), c3 = Math.cos(g / 2), s1 = Math.sin(b / 2), s2 = Math.sin(a / 2), s3 = Math.sin(g / 2);
  let x = s1 * c2 * c3 + c1 * s2 * s3, y = c1 * s2 * c3 - s1 * c2 * s3, z = c1 * c2 * s3 - s1 * s2 * c3, w = c1 * c2 * c3 + s1 * s2 * s3;
  const mul = (A, B) => [A[3] * B[0] + A[0] * B[3] + A[1] * B[2] - A[2] * B[1], A[3] * B[1] - A[0] * B[2] + A[1] * B[3] + A[2] * B[0],
                         A[3] * B[2] + A[0] * B[1] - A[1] * B[0] + A[2] * B[3], A[3] * B[3] - A[0] * B[0] - A[1] * B[1] - A[2] * B[2]];
  let Q = mul([x, y, z, w], [-Math.SQRT1_2, 0, 0, Math.SQRT1_2]);       // la cámara mira por la espalda del celular
  Q = mul(Q, [0, 0, -Math.sin(o / 2), Math.cos(o / 2)]);                 // celular acostado (horizontal)
  return Q;
}
function alGirar(e){ if(e.alpha == null) return; const o = (screen.orientation && screen.orientation.angle) || window.orientation || 0;
  q = quatDeOrientacion(e.alpha, e.beta, e.gamma, o); }
function bucle(){ const ya = performance.now(); if(ya - ultimoEnvio > 33){ ultimoEnvio = ya; enviar({tipo: "pose", q, p: pXR, mueve, mm}); } requestAnimationFrame(bucle); }

$("#empezar").onclick = async () => {
  try{ if(typeof DeviceOrientationEvent !== "undefined" && DeviceOrientationEvent.requestPermission){ const r = await DeviceOrientationEvent.requestPermission(); if(r !== "granted") throw new Error("Sin permiso para el movimiento"); } }
  catch(e){ $("#permiso").textContent = "Necesito el permiso de movimiento del celular para mover la cámara (" + e.message + ")."; return; }
  window.addEventListener("deviceorientation", alGirar, true);
  $("#inicio").style.display = "none"; bucle();
  if(navigator.xr && navigator.xr.isSessionSupported) navigator.xr.isSessionSupported("immersive-ar").then(ok => { if(ok) $("#arBtn").style.display = ""; }).catch(() => {});
  try{ if(screen.orientation && screen.orientation.lock) await screen.orientation.lock("portrait"); }catch(e){}
};
$("#centrar").onclick = () => enviar({tipo: "centrar"});
$("#rec").onclick = () => enviar({tipo: "grabar", on: !grabando});
$("#cero").onclick = () => enviar({tipo: "ir", t: 0});
$("#mm").oninput = () => { mm = +$("#mm").value; $("#mmV").textContent = mm; };

// JOYSTICKS: izquierdo camina (adelante/atrás, costados), derecho sube y baja
function pad(el, fn){ const bola = el.querySelector(".bola"); let id = null;
  const mover = e => { const r = el.getBoundingClientRect(); let dx = (e.clientX - r.left - 60) / 50, dy = (e.clientY - r.top - 60) / 50; const n = Math.hypot(dx, dy); if(n > 1){ dx /= n; dy /= n; }
    bola.style.left = (40 + dx * 40) + "px"; bola.style.top = (40 + dy * 40) + "px"; fn(dx, dy); };
  el.addEventListener("pointerdown", e => { id = e.pointerId; el.setPointerCapture(id); mover(e); });
  el.addEventListener("pointermove", e => { if(e.pointerId === id) mover(e); });
  const fin = e => { if(e.pointerId !== id) return; id = null; bola.style.left = bola.style.top = "40px"; fn(0, 0); };
  el.addEventListener("pointerup", fin); el.addEventListener("pointercancel", fin); }
pad($("#padI"), (x, y) => { mueve[0] = -y; mueve[1] = x; });
pad($("#padD"), (x, y) => { mueve[2] = -y; });

// AR (Android con Chrome): el celular también manda DÓNDE está, no sólo hacia dónde apunta
$("#arBtn").onclick = async () => {
  try{
    const cv = document.createElement("canvas"), gl = cv.getContext("webgl", {xrCompatible: true});
    const ses = await navigator.xr.requestSession("immersive-ar", {requiredFeatures: ["local"], optionalFeatures: ["dom-overlay"], domOverlay: {root: document.body}});
    await gl.makeXRCompatible(); ses.updateRenderState({baseLayer: new XRWebGLLayer(ses, gl)});
    const esp = await ses.requestReferenceSpace("local");
    window.removeEventListener("deviceorientation", alGirar, true);
    $("#arBtn").textContent = "🚶 AR activo"; $("#arBtn").disabled = true;
    const cuadro = (t, fr) => { const v = fr.getViewerPose(esp);
      if(v){ const o = v.transform.orientation, p = v.transform.position; q = [o.x, o.y, o.z, o.w]; pXR = [p.x, p.y, p.z]; }
      gl.bindFramebuffer(gl.FRAMEBUFFER, ses.renderState.baseLayer.framebuffer); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
      ses.requestAnimationFrame(cuadro); };
    ses.requestAnimationFrame(cuadro);
    ses.addEventListener("end", () => { pXR = null; window.addEventListener("deviceorientation", alGirar, true); $("#arBtn").textContent = "🚶 Seguir mis pasos (AR)"; $("#arBtn").disabled = false; });
    enviar({tipo: "centrar"});
  }catch(e){ $("#estado").textContent = "AR no disponible: " + e.message; }
};
conectar();
</script></body></html>
"""

PAGINA_INICIO = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lumen · Studio Luma</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>""" + _ESTILO + r"""
  main{max-width:1180px;margin:0 auto;padding:18px} .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:16px}
  h1{font-family:'Bodoni Moda',serif;font-weight:600;font-size:30px;margin:0} h3{font-size:12px;margin:0 0 10px;font-weight:500;color:var(--rose-deep);text-transform:uppercase;letter-spacing:.06em}
  .nuevo{display:grid;grid-template-columns:2fr 1.4fr 0.8fr auto;gap:10px;align-items:end} .nuevo input,.nuevo select{width:100%;padding:9px}
  .grilla{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px}
  .pr{background:var(--card-2);border:1px solid var(--line);border-radius:14px;overflow:hidden;cursor:pointer;position:relative}
  .pr:hover{border-color:var(--rose)} .pr .img{aspect-ratio:4/3;background:#0f0e13 center/cover no-repeat;display:flex;align-items:center;justify-content:center;font-size:34px}
  .pr .tx{padding:8px 10px;font-size:13px} .pr .tx div{color:var(--ink-soft);font-size:11.5px} .pr .x{position:absolute;top:6px;right:6px;margin:0;padding:3px 8px;background:#000a}
  .tom{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px} .tom a{display:block;border-radius:10px;overflow:hidden;border:1px solid var(--line)}
  .tom img{width:100%;display:block} .tom span{display:block;font-size:11px;color:var(--ink-soft);padding:3px 6px;background:var(--card-2)}
  @media(max-width:700px){.nuevo{grid-template-columns:1fr}}
  .asis{border:1px solid var(--rose);background:linear-gradient(180deg,#221f27,var(--card))}
  .pasos{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0 14px}.pasos span{font-size:11.5px;padding:3px 10px;border-radius:99px;border:1px solid var(--line);color:var(--ink-soft)}
  .pasos span.on{border-color:var(--rose);color:var(--rose-deep)}.pasos span.ok{color:var(--ok)}
  .op{display:flex;flex-wrap:wrap;gap:6px}.op button.on{background:var(--rose);color:#131218;border-color:var(--rose)}
  .q{font-family:'Bodoni Moda',serif;font-size:19px;margin:0 0 10px}.fila2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .prota{display:grid;grid-template-columns:28px 1.3fr 1.3fr;gap:8px;align-items:center;margin-top:6px}.prota b{color:var(--rose-deep)}
  .lib{display:grid;grid-template-columns:repeat(auto-fill,minmax(92px,1fr));gap:8px;max-height:320px;overflow:auto;margin-top:8px}
  .lib div{border:1px solid var(--line);border-radius:10px;overflow:hidden;cursor:pointer;font-size:11px;text-align:center;background:var(--card-2)}
  .lib div.on{border-color:var(--rose);box-shadow:0 0 0 2px var(--rose)} .lib img{width:100%;aspect-ratio:1;object-fit:cover;display:block;background:#0f0e13}
  .nav{display:flex;justify-content:space-between;margin-top:16px}.res{line-height:1.8;font-size:13.5px}.res b{color:var(--rose-deep)}
  @media(max-width:700px){.fila2,.prota{grid-template-columns:1fr}}
</style></head><body><main>
<div class="card">
  <a href="/" class="hint" style="text-decoration:none">◀ Studio Luma</a>
  <h1>🧊 Lumen <small style="font-family:Jost;font-size:12px;color:var(--ink-soft)">v%%VERSION%%</small></h1>
  <p class="hint" style="font-size:13.5px">Tu programa 3D: armás el lugar (modelos reales, luces, cielo), ponés a tus personajes, movés la cámara —con el mouse o
  <b>con tu celular como si filmaras</b>— y marcás las <b>tomas</b>. Cada toma es la guía de cámara y pose para Fotos, Reels, Storyboard, Cine, Filmado o Comerciales.</p>
  <p style="margin:14px 0 0"><button class="go" id="abrirAsis" style="font-size:14px;padding:9px 18px">✨ Armar un mundo nuevo (te pregunto todo)</button>
    <label class="ghost" style="display:inline-block;cursor:pointer;margin:0;padding:9px 16px;border:1px solid var(--rose);border-radius:999px;font-size:14px">📹 Armarlo desde mi video o una foto
      <input type="file" id="desdeVid" accept="video/*,image/*" style="display:none"></label></p>
  <div id="vidBox" style="display:none;margin-top:12px;padding:12px;border:1px dashed var(--rose);border-radius:12px">
    <div class="hint">Filmate haciendo el reel (o subí una foto): Lumen copia el lugar, cuántas personas hay, por dónde caminan y giran, sus poses y cómo se mueve la cámara.</div>
    <div class="fila2" style="margin-top:8px"><div><label>Nombre</label><input id="vNom" style="width:100%;padding:9px" placeholder="ej: Reel probador"></div>
      <div><label>¿Algo que tenga que saber? (opcional)</label><input id="vNota" style="width:100%;padding:9px" placeholder="ej: es un probador; la de la izquierda soy yo"></div></div>
    <div id="vCuadros" style="display:flex;gap:4px;overflow-x:auto;margin-top:8px"></div>
    <p style="margin:8px 0 0"><button class="go" id="vGo">🧊 Armar el mundo con esto</button> <span class="hint" id="vEst"></span></p>
  </div>
  <details style="margin-top:10px"><summary class="hint" style="cursor:pointer">o un proyecto vacío, sin preguntas</summary>
  <div class="nuevo" style="margin-top:8px"><div><label>Nombre</label><input id="nNom" placeholder="ej: Campaña verano · playa"></div>
    <div><label>Formato</label><select id="nFmt"></select></div>
    <div><label>Duración (s)</label><input id="nDur" type="number" min="3" max="180" value="10"></div>
    <div><button class="go" id="crear">Crear y abrir</button></div></div></details>
</div>
<div class="card asis" id="asis" style="display:none">
  <h3>✨ El asistente · armemos el mundo</h3>
  <div class="pasos" id="pasos"></div>
  <div id="paso"></div>
  <div class="nav"><button id="atras">◀ Atrás</button><span><button id="cerrarAsis">Cancelar</button><button class="go" id="sigue">Siguiente ▶</button></span></div>
</div>
<div class="card"><h3>Mis proyectos</h3><div id="proys" class="grilla"><span class="hint"><span class="spin"></span> Cargando…</span></div></div>
<div class="card"><h3>🎞 Películas de Cine</h3><div class="hint" style="margin-bottom:8px">Abrí el set 3D de una película de Cine (sus puntos de cámara pasan a la línea de tiempo).</div><div id="pelis" class="grilla"></div></div>
<div class="card"><h3>📸 Mis tomas</h3><div class="hint" style="margin-bottom:8px">Todas las tomas guardadas como imagen. Desde Fotos, Reels, Storyboard y los demás las elegís como guía.</div><div id="tomas" class="tom"></div></div>
</main>
<script>
const API = "%%API%%", PREF = "%%PREF%%";
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const ICO = {"9:16": "📱", "16:9": "🎬", "1:1": "◼", "4:5": "🖼"};
async function cargar(){
  const r = await fetch(`${API}/proyectos`); const d = await r.json();
  $("#nFmt").innerHTML = Object.entries(d.formatos || {}).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#proys").innerHTML = (d.proyectos || []).map(p => `<div class="pr" data-p="${p.id}"><div class="img" style="${p.portada ? `background-image:url('${p.portada}')` : ""}">${p.portada ? "" : ICO[p.formato] || "🧊"}</div>
      <div class="tx"><b>${esc(p.nombre)}</b><div>${p.formato} · ${p.dur} s · ${p.tomas} tomas · ${esc((p.actualizado || "").slice(0, 16))}</div></div><button class="x" data-b="${p.id}" title="Borrar">🗑</button></div>`).join("")
    || '<span class="hint">Todavía no hay proyectos: creá el primero arriba.</span>';
  $$("[data-p]").forEach(x => x.onclick = () => location.href = `${PREF}/editor?id=${x.dataset.p}`);
  $$("[data-b]").forEach(x => x.onclick = async e => { e.stopPropagation(); if(!confirm("¿Borrar este proyecto y sus tomas?")) return;
    await fetch(`${API}/proyectos/${x.dataset.b}`, {method: "DELETE"}); cargar(); });
  try{ const rc = await fetch("/cine/api/lista"); const c = await rc.json();
    $("#pelis").innerHTML = (c.peliculas || []).map(p => `<div class="pr" data-c="${p.id}"><div class="img">🎞</div><div class="tx"><b>${esc(p.titulo || "Película")}</b><div>${esc(p.creado || "")}</div></div></div>`).join("") || '<span class="hint">No hay películas de Cine.</span>';
    $$("[data-c]").forEach(x => x.onclick = () => location.href = `${PREF}/editor?cine=${x.dataset.c}`); }catch(e){ $("#pelis").innerHTML = ""; }
  const rt = await fetch(`${API}/tomas`); const t = await rt.json();
  $("#tomas").innerHTML = (t.tomas || []).map(x => `<a href="${x.url}" target="_blank" title="${esc(x.proyecto_nombre)}"><img loading="lazy" src="${x.url}"><span>${esc(x.nombre)} · ${esc(x.proyecto_nombre)}</span></a>`).join("") || '<span class="hint">Todavía no guardaste tomas.</span>';
}
// ── EL ASISTENTE: pregunta todo antes de crear el mundo ──
const MUNDO = %%MUNDO%%, POSES = %%POSES%%;
const PASOS = ["La idea", "El lugar", "Protagonistas", "Objetos", "La cámara", "Listo"];
let A = null, P = 0, ELENCO = [], LIB = null, LIBQ = "";
const ops = (obj, val, attr) => `<div class="op">${Object.entries(obj).map(([k, v]) => `<button data-${attr}="${k}" class="${k === val ? "on" : ""}">${esc(typeof v === "string" ? v : v.n)}</button>`).join("")}</div>`;
function nuevoA(){
  return {nombre: "", tipo: "reel", formato: "9:16", dur: 15, idea: "", lugar: "playa", cielo: "atardecer",
          protas: [{quien: -1, pose: "de_pie"}], ronda: "fila", objetos: [...MUNDO.lugares.playa.sugeridos], modelos: [],
          camara: "celular", plano: "entero", mov: "quieta"};
}
function pasar(){ /* lee lo escrito en el paso antes de moverse */
  const v = id => { const e = $(id); return e ? e.value : null; };
  if(P === 0){ A.nombre = v("#aNom").trim(); A.idea = v("#aIdea").trim(); A.dur = Math.max(3, Math.min(180, +v("#aDur") || A.dur)); }
  if(P === 2) A.protas = $$(".prota").map(r => ({quien: +r.querySelector("[data-quien]").value, pose: r.querySelector("[data-pose]").value}));
}
function pintarPaso(){
  $("#pasos").innerHTML = PASOS.map((n, i) => `<span class="${i === P ? "on" : i < P ? "ok" : ""}">${i < P ? "✓ " : ""}${i + 1}. ${n}</span>`).join("");
  $("#atras").style.visibility = P ? "" : "hidden"; $("#sigue").textContent = P === PASOS.length - 1 ? "✨ Crear el mundo y abrir" : "Siguiente ▶";
  const el = $("#paso");
  if(P === 0){
    el.innerHTML = `<p class="q">¿Qué vamos a hacer?</p>${ops(MUNDO.tipos, A.tipo, "tipo")}
      <div class="fila2" style="margin-top:12px"><div><label>Nombre del proyecto</label><input id="aNom" value="${esc(A.nombre)}" placeholder="ej: Comercial de verano" style="width:100%;padding:9px"></div>
      <div class="fila2"><div><label>Formato</label><select id="aFmt" style="width:100%;padding:9px">${Object.keys({"9:16": 1, "16:9": 1, "1:1": 1, "4:5": 1}).map(f => `<option ${f === A.formato ? "selected" : ""}>${f}</option>`).join("")}</select></div>
      <div><label>Duración (s)</label><input id="aDur" type="number" min="3" max="180" value="${A.dur}" style="width:100%;padding:9px"></div></div></div>
      <label style="margin-top:10px;display:block">Contame la idea en dos líneas (opcional)</label><textarea id="aIdea" rows="2" style="width:100%;padding:9px" placeholder="ej: dos chicas caminan por la playa al atardecer y una se da vuelta a cámara">${esc(A.idea)}</textarea>`;
    $$("[data-tipo]").forEach(b => b.onclick = () => { pasar(); A.tipo = b.dataset.tipo; const t = MUNDO.tipos[A.tipo]; A.formato = t.formato; A.dur = t.dur; pintarPaso(); });
    $("#aFmt").onchange = () => A.formato = $("#aFmt").value;
  }
  if(P === 1){
    el.innerHTML = `<p class="q">¿Dónde pasa?</p>${ops(MUNDO.lugares, A.lugar, "lugar")}
      <p class="q" style="margin-top:16px">¿A qué hora / con qué cielo?</p>${ops(MUNDO.cielos, A.cielo, "cielo")}
      <div class="hint" style="margin-top:8px">Después, en el editor, podés poner un cielo real 360° (HDRI) que ilumina la escena.</div>`;
    $$("[data-lugar]").forEach(b => b.onclick = () => { A.lugar = b.dataset.lugar; const L = MUNDO.lugares[A.lugar]; A.cielo = L.cielo; A.objetos = [...L.sugeridos]; pintarPaso(); });
    $$("[data-cielo]").forEach(b => b.onclick = () => { A.cielo = b.dataset.cielo; pintarPaso(); });
  }
  if(P === 2){
    const quien = q => `<select data-quien style="width:100%;padding:8px"><option value="-1">Persona (sin nombre)</option>${ELENCO.map((n, i) => `<option value="${i}" ${q === i ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>`;
    const pose = p => `<select data-pose style="width:100%;padding:8px">${Object.entries(POSES).map(([k, n]) => `<option value="${k}" ${k === p ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>`;
    el.innerHTML = `<p class="q">¿Cuántos protagonistas?</p><div class="op">${[0, 1, 2, 3, 4, 5, 6].map(n => `<button data-n="${n}" class="${n === A.protas.length ? "on" : ""}">${n === 0 ? "Ninguno (sólo el lugar o el producto)" : n}</button>`).join("")}</div>
      ${A.protas.map((p, i) => `<div class="prota"><b>${i + 1}</b><div>${quien(p.quien)}</div><div>${pose(p.pose)}</div></div>`).join("")}
      ${ELENCO.length ? "" : '<div class="hint" style="margin-top:6px">Tus personajes (de 👥 Personajes) aparecen acá para elegirlos por nombre.</div>'}
      ${A.protas.length > 1 ? `<p class="q" style="margin-top:16px">¿Cómo se ubican?</p>${ops(MUNDO.rondas, A.ronda, "ronda")}` : ""}`;
    $$("[data-n]").forEach(b => b.onclick = () => { pasar(); const n = +b.dataset.n;
      while(A.protas.length < n) A.protas.push({quien: ELENCO.length > A.protas.length ? A.protas.length : -1, pose: "de_pie"}); A.protas.length = n; pintarPaso(); });
    $$("[data-ronda]").forEach(b => b.onclick = () => { pasar(); A.ronda = b.dataset.ronda; pintarPaso(); });
  }
  if(P === 3){
    const FORMAS = %%FORMAS%%;
    el.innerHTML = `<p class="q">¿Qué objetos hay?</p><div class="hint" style="margin-bottom:6px">Ya están los de ${esc(MUNDO.lugares[A.lugar].n)}. Tocá para sumar o sacar.</div>
      <div class="op">${Object.entries(FORMAS).map(([k, n]) => `<button data-obj="${k}" class="${A.objetos.includes(k) ? "on" : ""}">${esc(n)}</button>`).join("")}</div>
      <p class="q" style="margin-top:16px">¿Modelos 3D reales? <small class="hint">(biblioteca gratis Poly Haven · elegí hasta 8)</small></p>
      <input id="aBus" value="${esc(LIBQ)}" placeholder="Buscar (en inglés: sofa, chair, plant, lamp, table, bed…)" style="width:100%;padding:9px">
      <div class="hint" id="aSel" style="margin-top:6px"></div><div class="lib" id="aLib"><span class="hint"><span class="spin"></span> Cargando la biblioteca…</span></div>`;
    $$("[data-obj]").forEach(b => b.onclick = () => { const k = b.dataset.obj; A.objetos = A.objetos.includes(k) ? A.objetos.filter(x => x !== k) : [...A.objetos, k]; b.classList.toggle("on"); });
    let tq = null; $("#aBus").oninput = () => { clearTimeout(tq); tq = setTimeout(() => { LIBQ = $("#aBus").value.trim().toLowerCase(); pintarLib(); }, 250); };
    pintarLib();
  }
  if(P === 4){
    el.innerHTML = `<p class="q">¿Con qué cámara filmamos?</p>${ops(MUNDO.camaras, A.camara, "cam")}
      <p class="q" style="margin-top:16px">¿Qué tan cerca?</p>${ops(MUNDO.planos, A.plano, "plano")}
      <p class="q" style="margin-top:16px">¿Cómo se mueve?</p>${ops(MUNDO.movs, A.mov, "mov")}
      <div class="hint" style="margin-top:8px">Queda armado en la línea de tiempo. Después la movés con el mouse o con tu 📱 celular como cámara.</div>`;
    $$("[data-cam]").forEach(b => b.onclick = () => { A.camara = b.dataset.cam; A.mov = MUNDO.camaras[A.camara].mov; if(A.camara === "dron") A.plano = "general"; pintarPaso(); });
    $$("[data-plano]").forEach(b => b.onclick = () => { A.plano = b.dataset.plano; pintarPaso(); });
    $$("[data-mov]").forEach(b => b.onclick = () => { A.mov = b.dataset.mov; pintarPaso(); });
  }
  if(P === 5){
    const FORMAS = %%FORMAS%%, nom = q => q >= 0 ? ELENCO[q] || "Persona" : "Persona";
    el.innerHTML = `<p class="q">Así queda el mundo</p><div class="res">
      <div>🎬 <b>${esc(A.nombre || "Proyecto nuevo")}</b> · ${esc(MUNDO.tipos[A.tipo].n)} · ${A.formato} · ${A.dur} s</div>
      ${A.idea ? `<div>💭 ${esc(A.idea)}</div>` : ""}
      <div>📍 ${esc(MUNDO.lugares[A.lugar].n)} · ${esc(MUNDO.cielos[A.cielo])}</div>
      <div>🧍 ${A.protas.length ? A.protas.map(p => `${esc(nom(p.quien))} (${esc((POSES[p.pose] || "").toLowerCase())})`).join(", ") + (A.protas.length > 1 ? " · " + esc(MUNDO.rondas[A.ronda].toLowerCase()) : "") : "Sin protagonistas"}</div>
      <div>🪑 ${A.objetos.length ? A.objetos.map(k => esc(FORMAS[k] || k)).join(", ") : "Sin objetos extra"}${A.modelos.length ? " · modelos: " + A.modelos.map(m => esc(m.nombre)).join(", ") : ""}</div>
      <div>${esc(MUNDO.camaras[A.camara].n)} · ${esc(MUNDO.planos[A.plano])} · ${esc(MUNDO.movs[A.mov])}</div></div>
      <div class="hint" style="margin-top:8px">Todo se puede cambiar después en el editor.</div>`;
  }
}
async function pintarLib(){
  const sel = () => { $("#aSel").innerHTML = A.modelos.length ? "Elegidos: " + A.modelos.map(m => `<b>${esc(m.nombre)}</b>`).join(", ") : "Ninguno elegido."; };
  sel();
  if(!LIB){ try{ const r = await fetch(`${API}/ph/lista?tipo=models`); const d = await r.json(); if(!r.ok) throw new Error(d.detail || r.status); LIB = d.items; }
    catch(e){ if($("#aLib")) $("#aLib").innerHTML = `<span class="hint">No pude abrir la biblioteca (${esc(e.message)}). Podés sumar modelos después, desde el editor.</span>`; return; } }
  if(!$("#aLib")) return;
  const xs = LIB.filter(x => !LIBQ || (x.nombre + " " + x.tags.join(" ") + " " + x.cat.join(" ")).toLowerCase().includes(LIBQ)).slice(0, 48);
  $("#aLib").innerHTML = xs.map(x => `<div data-m="${esc(x.id)}" title="${esc(x.nombre)}" class="${A.modelos.some(m => m.ph === x.id) ? "on" : ""}"><img loading="lazy" src="https://cdn.polyhaven.com/asset_img/thumbs/${esc(x.id)}.png?width=160&height=160">${esc(x.nombre)}</div>`).join("") || '<span class="hint">Nada con esa palabra.</span>';
  $$("[data-m]").forEach(d => d.onclick = () => { const id = d.dataset.m;
    if(A.modelos.some(m => m.ph === id)) A.modelos = A.modelos.filter(m => m.ph !== id);
    else if(A.modelos.length < 8) A.modelos.push({ph: id, nombre: d.title}); else return alert("Hasta 8 modelos (después podés sumar más en el editor).");
    d.classList.toggle("on"); sel(); });
}
$("#abrirAsis").onclick = async () => { A = nuevoA(); P = 0; $("#asis").style.display = ""; $("#asis").scrollIntoView({behavior: "smooth"});
  try{ const r = await fetch("/personajes/api/lista"); if(r.ok) ELENCO = ((await r.json()).personajes || []).map(x => x.nombre); }catch(e){}
  if(ELENCO.length) A.protas = [{quien: 0, pose: "de_pie"}];
  pintarPaso(); };
$("#cerrarAsis").onclick = () => { $("#asis").style.display = "none"; };
$("#atras").onclick = () => { pasar(); P = Math.max(0, P - 1); pintarPaso(); };
$("#sigue").onclick = async () => { pasar();
  if(P < PASOS.length - 1){ P++; return pintarPaso(); }
  const b = $("#sigue"); b.disabled = true; b.textContent = "Creando…";
  const mundo = {tipo: A.tipo, idea: A.idea, lugar: A.lugar, cielo: A.cielo, protas: A.protas, ronda: A.ronda, objetos: A.objetos, modelos: A.modelos,
                 camara: A.camara, plano: A.plano, mov: A.mov};
  const r = await fetch(`${API}/proyectos`, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({nombre: A.nombre || "Proyecto nuevo", formato: A.formato, dur: A.dur, mundo})});
  const d = await r.json(); if(!r.ok){ b.disabled = false; pintarPaso(); return alert(d.detail || "No se pudo crear."); }
  location.href = `${PREF}/editor?id=${d.proyecto.id}`;
};
// ── DESDE UN VIDEO O UNA FOTO ── los cuadros se sacan acá (el video no se sube entero)
let VID = null;
async function cuadrosDe(f){
  const lado = (w, h) => { const k = 768 / Math.max(w, h); return [Math.round(w * Math.min(1, k)), Math.round(h * Math.min(1, k))]; };
  const cv = document.createElement("canvas"), cx = cv.getContext("2d");
  const foto = el => { const [w, h] = lado(el.videoWidth || el.naturalWidth, el.videoHeight || el.naturalHeight); cv.width = w; cv.height = h; cx.drawImage(el, 0, 0, w, h); return cv.toDataURL("image/jpeg", 0.8); };
  const url = URL.createObjectURL(f);
  if(f.type.startsWith("image/")){ const im = new Image(); im.src = url; await im.decode(); return {ancho: im.naturalWidth, alto: im.naturalHeight, dur: 0, cuadros: [{t: 0, img: foto(im)}]}; }
  const v = document.createElement("video"); v.muted = true; v.playsInline = true; v.preload = "auto"; v.src = url;
  const abre = await Promise.race([new Promise(ok => { v.onloadeddata = () => ok(true); v.onerror = () => ok(false); }), new Promise(ok => setTimeout(() => ok(false), 8000))]);
  if(!abre || !v.videoWidth){   // este navegador no lo decodifica (HEVC del iPhone…): los cuadros se sacan en el servidor
    $("#vEst").innerHTML = '<span class="spin"></span> Tu navegador no abre este video: lo subo y saco los cuadros allá…';
    const fd = new FormData(); fd.append("video", f, f.name || "video");
    const r = await fetch(`${API}/cuadros_video`, {method: "POST", body: fd}); const d = await r.json(); if(!r.ok) throw new Error(d.detail || "No pude abrir ese video.");
    return d; }
  const dur = Math.min(60, v.duration || 0), n = Math.max(2, Math.min(16, Math.round(dur * 1.2) + 1)), out = [];
  for(let i = 0; i < n; i++){ const t = Math.min(dur - 0.05, i * dur / (n - 1)); v.currentTime = Math.max(0, t);
    await new Promise(r => { v.onseeked = r; }); out.push({t: Math.round(t * 10) / 10, img: foto(v)}); $("#vEst").textContent = `Sacando cuadros… ${i + 1}/${n}`; }
  return {ancho: v.videoWidth, alto: v.videoHeight, dur: v.duration || 0, cuadros: out, largo: (v.duration || 0) > 60};
}
$("#desdeVid").onchange = async e => { const f = e.target.files[0]; e.target.value = ""; if(!f) return;
  $("#vidBox").style.display = ""; $("#vCuadros").innerHTML = ""; $("#vGo").disabled = true; $("#vNom").value = $("#vNom").value || f.name.replace(/\.[^.]+$/, "").slice(0, 60);
  try{ VID = await cuadrosDe(f);
    $("#vCuadros").innerHTML = VID.cuadros.map(c => `<div style="flex:0 0 auto;text-align:center"><img src="${c.img}" style="height:110px;border-radius:6px;display:block"><span class="hint">${c.t}s</span></div>`).join("");
    $("#vEst").textContent = VID.dur ? `${VID.dur.toFixed(1)} s · ${VID.cuadros.length} cuadros` + (VID.largo ? " · ⚠ uso los primeros 60 s" : "") : "Una foto: un solo momento.";
    $("#vGo").disabled = false; }
  catch(err){ $("#vEst").innerHTML = `<span class="mal">${esc(err.message)}</span>`; } };
$("#vGo").onclick = async () => { if(!VID) return; const b = $("#vGo"); b.disabled = true;
  $("#vEst").innerHTML = '<span class="spin"></span> Claude está mirando tu video y armando el mundo (un minuto, más o menos)…';
  try{ const r = await fetch(`${API}/desde_video`, {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({nombre: $("#vNom").value.trim() || "Desde mi video", nota: $("#vNota").value.trim(), ancho: VID.ancho, alto: VID.alto, dur: Math.min(60, VID.dur), cuadros: VID.cuadros})});
    const d = await r.json(); if(!r.ok) throw new Error(d.detail || r.status);
    location.href = `${PREF}/editor?id=${d.proyecto.id}`; }
  catch(err){ $("#vEst").innerHTML = `<span class="mal">${esc(err.message)}</span>`; b.disabled = false; } };
$("#crear").onclick = async () => {
  const r = await fetch(`${API}/proyectos`, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({nombre: $("#nNom").value.trim() || "Proyecto nuevo", formato: $("#nFmt").value, dur: +$("#nDur").value || 10})});
  const d = await r.json(); if(!r.ok) return alert(d.detail || "No se pudo crear.");
  location.href = `${PREF}/editor?id=${d.proyecto.id}`;
};
cargar();
</script></body></html>
"""

# El selector de tomas que cargan las otras pantallas (<script src="/lumen/elegir.js">):
#   const t = await Lumen.elegir({titulo, formato});   // null si cancela
#   t = {proyecto, id, nombre, proyecto_nombre, formato, camara, url}
#   const img = await Lumen.dataURL(t.url);            // "data:image/jpeg;base64,…"
ELEGIR_JS = r"""(function(){
if(window.Lumen) return;
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const css = `.lmn-fondo{position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:99999;display:flex;align-items:center;justify-content:center;padding:16px;font-family:Jost,system-ui,sans-serif}
.lmn-caja{background:#1c1b22;color:#eee;border:1px solid #3a3842;border-radius:16px;max-width:900px;width:100%;max-height:86vh;display:flex;flex-direction:column}
.lmn-cab{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid #3a3842}.lmn-cab b{flex:1;font-size:15px}
.lmn-cab a,.lmn-cab button{color:#e9c58b;background:none;border:1px solid #3a3842;border-radius:8px;padding:5px 10px;cursor:pointer;font:inherit;font-size:13px;text-decoration:none}
.lmn-grilla{overflow:auto;padding:14px 16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.lmn-t{background:#26242d;border:1px solid #3a3842;border-radius:10px;overflow:hidden;cursor:pointer;text-align:left;padding:0;color:inherit;font:inherit}
.lmn-t:hover{border-color:#e9c58b}.lmn-t img{width:100%;display:block;max-height:220px;object-fit:contain;background:#0f0e13}
.lmn-t span{display:block;padding:5px 8px;font-size:12px}.lmn-t small{display:block;color:#aaa;font-size:11px}
.lmn-vacio{padding:24px;color:#bbb;font-size:14px;line-height:1.5}`;
function elegir(op){
  op = op || {};
  if(!document.getElementById("lmn-css")){ const st = document.createElement("style"); st.id = "lmn-css"; st.textContent = css; document.head.appendChild(st); }
  return new Promise(res => {
    const f = document.createElement("div"); f.className = "lmn-fondo";
    f.innerHTML = `<div class="lmn-caja"><div class="lmn-cab"><b>🧊 ${esc(op.titulo || "Elegí una toma de Lumen")}</b>
      <a href="/lumen" target="_blank">Abrir Lumen</a><button data-x>✕</button></div><div class="lmn-grilla"><div class="lmn-vacio">Cargando…</div></div></div>`;
    const fin = v => { f.remove(); document.removeEventListener("keydown", tecla); res(v); };
    const tecla = e => { if(e.key === "Escape") fin(null); };
    document.addEventListener("keydown", tecla);
    f.onclick = e => { if(e.target === f || e.target.closest("[data-x]")) fin(null); };
    document.body.appendChild(f);
    fetch("/lumen/api/tomas" + (op.formato ? "?formato=" + encodeURIComponent(op.formato) : ""), {credentials: "same-origin"})
      .then(r => r.json()).then(d => {
        const g = f.querySelector(".lmn-grilla"), L = d.tomas || [];
        if(!L.length){ g.innerHTML = `<div class="lmn-vacio">Todavía no hay tomas${op.formato ? " en formato " + esc(op.formato) : ""}.<br>
          Abrí <a href="/lumen" target="_blank" style="color:#e9c58b">Lumen</a>, armá la escena, marcá las tomas (🎞) y tocá "📸 Guardar las tomas".</div>`; return; }
        g.innerHTML = L.map((t, i) => `<button class="lmn-t" data-i="${i}" title="${esc(t.camara || "")}"><img loading="lazy" src="${t.url}">
          <span>${esc(t.nombre)}<small>${esc(t.proyecto_nombre)} · ${esc(t.formato)}</small></span></button>`).join("");
        g.querySelectorAll("[data-i]").forEach(b => b.onclick = () => fin(L[+b.dataset.i]));
      }).catch(() => { f.querySelector(".lmn-grilla").innerHTML = '<div class="lmn-vacio">No pude cargar las tomas.</div>'; });
  });
}
async function dataURL(url){
  const b = await (await fetch(url, {credentials: "same-origin"})).blob();
  return await new Promise((ok, mal) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = mal; r.readAsDataURL(b); });
}
window.Lumen = {elegir, dataURL};
})();
"""
