"""Copiar una toma con un MANIQUÍ 3D, gratis y en el navegador.

La foto de referencia pasa por el detector de poses de MediaPipe (corre en el navegador de
la usuaria, sin costo y sin filtros): por cada persona da 33 puntos del cuerpo (posición en
la foto y profundidad). Con esos puntos se arma un maniquí gris de cápsulas y esferas en
three.js, con una cámara ortográfica del tamaño de la foto: así el maniquí queda EXACTO en el
mismo lugar y encuadre que la persona. Los puntos se pueden arrastrar para corregir la pose.
La captura del maniquí es la guía que después reciben Seedream y Nano Banana.

Si no carga (sin internet a los CDN) o no encuentra a nadie, se usa el maniquí con IA."""

POSE3D_JS = r"""
const THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js";
const MP_URL = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs";
const MP_WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
const MP_MODEL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task";
let THREE = null, detector = null;

async function cargarThree(){ if(!THREE) THREE = await import(THREE_URL); return THREE; }
async function cargarDetector(){
  if(detector) return detector;
  const v = await import(MP_URL);
  const fs = await v.FilesetResolver.forVisionTasks(MP_WASM);
  const opts = d => ({baseOptions:{modelAssetPath:MP_MODEL, delegate:d}, runningMode:"IMAGE",
                      numPoses:4, minPoseDetectionConfidence:0.3, minPosePresenceConfidence:0.3});
  try { detector = await v.PoseLandmarker.createFromOptions(fs, opts("GPU")); }
  catch(e){ detector = await v.PoseLandmarker.createFromOptions(fs, opts("CPU")); }
  return detector;
}

// Huesos del maniquí: [desde, hasta, grosor relativo al ancho de hombros]
const HUESOS = [
  [11,13,.13],[13,15,.11],[12,14,.13],[14,16,.11],          // brazos
  [23,25,.19],[25,27,.14],[24,26,.19],[26,28,.14],          // piernas
  [27,29,.08],[29,31,.07],[28,30,.08],[30,32,.07],          // pies
  [15,"m15",.07],[16,"m16",.07],                            // manos
  [11,12,.18],[23,24,.22],                                  // hombros y cadera
];
const ARTICULACIONES = [0,11,12,13,14,15,16,23,24,25,26,27,28];

function puntos(lm, W, H){
  const P = lm.map(p => ({x:p.x*W, y:-p.y*H, z:-(p.z||0)*W, v:(p.visibility==null?1:p.visibility)}));
  const mid = (a,b) => ({x:(a.x+b.x)/2, y:(a.y+b.y)/2, z:(a.z+b.z)/2, v:Math.min(a.v,b.v)});
  P.m15 = mid(P[17], P[19]); P.m16 = mid(P[18], P[20]);
  P.hombros = mid(P[11], P[12]); P.cadera = mid(P[23], P[24]); P.orejas = mid(P[7], P[8]);
  return P;
}
const dist = (a,b) => Math.hypot(a.x-b.x, a.y-b.y, a.z-b.z);

function capsula(T, mat, a, b, r){
  const L = dist(a,b); if(!(L > 1)) return null;
  const g = new T.CapsuleGeometry(r, L, 6, 14);
  const m = new T.Mesh(g, mat);
  m.position.set((a.x+b.x)/2, (a.y+b.y)/2, (a.z+b.z)/2);
  const dir = new T.Vector3(b.x-a.x, b.y-a.y, b.z-a.z).normalize();
  m.quaternion.setFromUnitVectors(new T.Vector3(0,1,0), dir);
  return m;
}
function esfera(T, mat, p, r){ const m = new T.Mesh(new T.SphereGeometry(r, 20, 14), mat); m.position.set(p.x,p.y,p.z); return m; }

function armarManiqui(T, P, mat){
  const g = new T.Group();
  const s = Math.max(20, Math.hypot(P[11].x-P[12].x, P[11].y-P[12].y));
  const add = o => { if(o) g.add(o); };
  for(const [a,b,k] of HUESOS){
    const A = P[a], B = P[b];
    if(!A || !B || A.v < .15 || B.v < .15) continue;
    add(capsula(T, mat, A, B, k*s));
  }
  // Torso: dos óvalos lisos (pecho y cadera) alineados con la columna, más la columna.
  const lerp = (a,b,t) => ({x:a.x+(b.x-a.x)*t, y:a.y+(b.y-a.y)*t, z:a.z+(b.z-a.z)*t});
  const largo = dist(P.hombros, P.cadera);
  const col = new T.Vector3(P.hombros.x-P.cadera.x, P.hombros.y-P.cadera.y, P.hombros.z-P.cadera.z).normalize();
  const ancho = (a, b) => Math.max(.6*s, Math.hypot(a.x-b.x, a.y-b.y));
  const ovalo = (c, rx, ry, rz) => { if(!(largo > 1)) return null;
    const m = new T.Mesh(new T.SphereGeometry(1, 28, 20), mat);
    m.position.set(c.x, c.y, c.z); m.quaternion.setFromUnitVectors(new T.Vector3(0,1,0), col);
    m.scale.set(rx, ry, rz); return m; };
  add(ovalo(lerp(P.hombros, P.cadera, .3), ancho(P[11],P[12])*.5, largo*.36, .3*s));   // pecho
  add(ovalo(lerp(P.hombros, P.cadera, .82), ancho(P[23],P[24])*.62, largo*.28, .3*s));  // cadera
  add(capsula(T, mat, P.hombros, P.cadera, .26*s));                                     // columna
  add(capsula(T, mat, P.hombros, P.orejas, .12*s));                 // cuello
  const rc = Math.max(dist(P[7],P[8])*.62, .3*s);
  add(esfera(T, mat, P.orejas, rc));                                // cabeza
  const nariz = P[0];                                              // nariz: muestra hacia dónde mira
  const hacia = new T.Vector3(nariz.x-P.orejas.x, nariz.y-P.orejas.y, nariz.z-P.orejas.z);
  if(hacia.length() > 1){ hacia.setLength(rc*.95);
    add(esfera(T, mat, {x:P.orejas.x+hacia.x, y:P.orejas.y+hacia.y, z:P.orejas.z+hacia.z}, rc*.22)); }
  for(const j of [11,12,13,14,23,24,25,26]){ if(P[j].v >= .15) add(esfera(T, mat, P[j], .1*s)); }
  return g;
}

function abrirModal(){
  let ov = document.getElementById("pose3d-ov");
  if(ov) ov.remove();
  ov = document.createElement("div"); ov.id = "pose3d-ov";
  ov.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.82);display:flex;align-items:center;justify-content:center;padding:12px";
  ov.innerHTML = '<div style="background:var(--card,#1e1b1b);color:inherit;border-radius:14px;padding:14px;max-width:980px;width:100%;max-height:96vh;overflow:auto">'
    + '<div style="font-weight:600;margin-bottom:6px">🧍 Copiar toma con maniquí 3D (gratis)</div>'
    + '<p class="hint" id="p3-est" style="margin:0 0 8px">Cargando el detector de poses…</p>'
    + '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start">'
    + '<img id="p3-foto" style="max-width:46%;max-height:62vh;border-radius:10px">'
    + '<div id="p3-lienzo" style="max-width:52%;touch-action:none"></div></div>'
    + '<p class="hint" style="margin:8px 0">Arrastrá los puntos rosas para corregir la pose. De izquierda a derecha son la modelo A, B, C.</p>'
    + '<div style="display:flex;gap:8px;flex-wrap:wrap">'
    + '<button class="go" id="p3-usar" disabled>✅ Usar esta guía (gratis)</button>'
    + '<button class="ghost" id="p3-ia">✨ Mejorar con IA (~US$0,07)</button>'
    + '<button class="ghost" id="p3-no">Cancelar</button></div></div>';
  document.body.appendChild(ov);
  return ov;
}

// Devuelve una promesa: {modo:"3d", guia, personas} | {modo:"ia"} | null (cancelado)
async function copiarToma(file){
  const ov = abrirModal();
  const $ = id => ov.querySelector("#"+id);
  const est = t => { $("p3-est").textContent = t; };
  const url = URL.createObjectURL(file);
  $("p3-foto").src = url;
  return new Promise(async resolve => {
    $("p3-no").onclick = () => { ov.remove(); resolve(null); };
    $("p3-ia").onclick = () => { ov.remove(); resolve({modo:"ia"}); };
    try{
      const img = $("p3-foto"); await img.decode();
      const T = await cargarThree();
      const det = await cargarDetector();
      est("Buscando las poses…");
      const res = det.detect(img);
      let personas = (res && res.landmarks) || [];
      if(!personas.length){ est("No encontré a nadie en la foto. Probá ✨ Mejorar con IA."); return; }
      const esc = Math.min(1, 1024 / Math.max(img.naturalWidth, img.naturalHeight));
      const W = Math.round(img.naturalWidth*esc), H = Math.round(img.naturalHeight*esc);
      // De izquierda a derecha: A, B, C…
      personas = personas.map(lm => lm.map(p => ({...p})))
        .sort((a,b) => ((a[23].x+a[24].x) - (b[23].x+b[24].x)));
      const renderer = new T.WebGLRenderer({antialias:true, preserveDrawingBuffer:true});
      renderer.setSize(W, H); renderer.setPixelRatio(1);
      renderer.domElement.style.cssText = "width:100%;height:auto;border-radius:10px";
      $("p3-lienzo").appendChild(renderer.domElement);
      const scene = new T.Scene(); scene.background = new T.Color(0x8c8c8c);
      const cam = new T.OrthographicCamera(0, W, 0, -H, 0.1, 20000); cam.position.set(0,0,8000);
      scene.add(new T.HemisphereLight(0xffffff, 0x666666, 1.1));
      const sol = new T.DirectionalLight(0xffffff, 1.6); sol.position.set(-W*.4, H*.6, 6000); scene.add(sol);
      const mat = new T.MeshStandardMaterial({color:0xd6d6d6, roughness:.75, metalness:0});
      const matPunto = new T.MeshBasicMaterial({color:0xe0607e});
      let grupo = new T.Group(), puntosVis = new T.Group(), editando = true;
      scene.add(grupo); scene.add(puntosVis);
      const rearmar = () => {
        scene.remove(grupo); scene.remove(puntosVis);
        grupo = new T.Group(); puntosVis = new T.Group();
        personas.forEach((lm, k) => {
          const P = puntos(lm, W, H);
          grupo.add(armarManiqui(T, P, mat));
          if(editando) for(const j of ARTICULACIONES){
            const m = esfera(T, matPunto, {x:P[j].x, y:P[j].y, z:4000}, Math.max(4, W/160));
            m.userData = {k, j}; puntosVis.add(m);
          }
        });
        scene.add(grupo); scene.add(puntosVis);
        renderer.render(scene, cam);
      };
      rearmar();
      // Arrastrar puntos (cámara ortográfica: la pantalla es la foto)
      const cv = renderer.domElement; let tomado = null;
      const aFoto = e => { const r = cv.getBoundingClientRect();
        return {x:(e.clientX-r.left)/r.width, y:(e.clientY-r.top)/r.height}; };
      cv.addEventListener("pointerdown", e => {
        const q = aFoto(e); let mejor = null, md = 1e9;
        personas.forEach((lm,k) => ARTICULACIONES.forEach(j => {
          const d = Math.hypot(lm[j].x-q.x, (lm[j].y-q.y)*H/W); if(d < md){ md = d; mejor = {k,j}; } }));
        if(mejor && md < .04){ tomado = mejor; cv.setPointerCapture(e.pointerId); }
      });
      cv.addEventListener("pointermove", e => { if(!tomado) return; const q = aFoto(e);
        const p = personas[tomado.k][tomado.j]; p.x = q.x; p.y = q.y; rearmar(); });
      cv.addEventListener("pointerup", () => { tomado = null; });
      est("Encontré " + personas.length + (personas.length === 1 ? " persona" : " personas")
          + ". Si algo no coincide, arrastrá los puntos; las manos y los dedos no los copia: eso lo describe el texto.");
      $("p3-usar").disabled = false;
      $("p3-usar").onclick = () => {
        editando = false; rearmar();
        const guia = renderer.domElement.toDataURL("image/jpeg", .9);
        ov.remove(); resolve({modo:"3d", guia, personas: personas.length});
      };
    }catch(err){
      console.warn("pose3d", err);
      est("No pude cargar el maniquí 3D en este navegador (" + (err && err.message || err) + "). Usá ✨ Mejorar con IA.");
    }
  });
}
window.Pose3D = {copiarToma};
"""
