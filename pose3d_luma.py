"""Copiar una toma CALCANDO la foto, gratis y en el navegador.

La foto de referencia pasa por el detector de poses de MediaPipe (corre en el navegador de
la usuaria, sin costo y sin filtros), que además de los puntos del cuerpo da la MÁSCARA exacta
de cada persona. Con eso se arma un CALCO: dentro de cada máscara, la misma foto pasada a gris
y suavizada (se borran detalles finos: rasgos, maquillaje, tatuajes, texturas de la ropa; quedan
las luces y sombras, el volumen, las manos y los dedos), la cara más difuminada, el contorno
marcado y el fondo gris liso. Es la pose, la cámara y el encuadre EXACTOS de la foto, sin copiar
a la persona. Esa imagen es la guía que reciben Seedream y Nano Banana.

Si no carga (sin internet a los CDN) o no encuentra a nadie, se usa el maniquí con IA."""

POSE3D_JS = r"""
const THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js";
const MP_URL = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs";
const MP_WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
const MP_MODEL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task";
let detector = null, THREE = null;
async function cargarThree(){ if(!THREE) THREE = await import(THREE_URL); return THREE; }

async function cargarDetector(){
  if(detector) return detector;
  const v = await import(MP_URL);
  const fs = await v.FilesetResolver.forVisionTasks(MP_WASM);
  const opts = d => ({baseOptions:{modelAssetPath:MP_MODEL, delegate:d}, runningMode:"IMAGE",
                      numPoses:4, minPoseDetectionConfidence:0.3, minPosePresenceConfidence:0.3,
                      outputSegmentationMasks:true});
  try { detector = await v.PoseLandmarker.createFromOptions(fs, opts("GPU")); }
  catch(e){ detector = await v.PoseLandmarker.createFromOptions(fs, opts("CPU")); }
  return detector;
}

function lienzo(W, H){ const c = document.createElement("canvas"); c.width = W; c.height = H; return c; }

// El CALCO: la foto en gris "yeso" dentro de las máscaras de las personas.
function calcar(img, masks, caras, W, H){
  // 1) La foto suavizada (se van texturas, rasgos y tatuajes; quedan luces y sombras)
  const suave = lienzo(W, H), cs = suave.getContext("2d");
  cs.filter = "grayscale(1) blur(" + Math.max(1.5, W/300).toFixed(1) + "px) contrast(1.15)";
  cs.drawImage(img, 0, 0, W, H);
  // 2) Las caras, mucho más difuminadas (nada de rasgos)
  const muy = lienzo(W, H), cm = muy.getContext("2d");
  cm.filter = "grayscale(1) blur(" + Math.max(4, W/70).toFixed(1) + "px)";
  cm.drawImage(img, 0, 0, W, H);
  cs.filter = "none";
  for(const c of caras){
    cs.save(); cs.beginPath(); cs.ellipse(c.x, c.y, c.r*0.8, c.r, 0, 0, Math.PI*2); cs.clip();
    cs.drawImage(muy, 0, 0); cs.restore();
  }
  const ds = cs.getImageData(0, 0, W, H).data;
  // 3) La máscara unida de todas las personas (a la resolución del calco)
  const m = new Float32Array(W*H);
  for(const mk of masks){
    const mw = mk.width, mh = mk.height, a = mk.data;
    for(let y = 0; y < H; y++){ const sy = Math.min(mh-1, Math.floor(y*mh/H));
      for(let x = 0; x < W; x++){ const v = a[sy*mw + Math.min(mw-1, Math.floor(x*mw/W))];
        if(v > m[y*W+x]) m[y*W+x] = v; } }
  }
  // 4) Componer: yeso gris claro con sus sombras dentro, fondo gris liso, contorno marcado
  const out = lienzo(W, H), co = out.getContext("2d");
  const od = co.createImageData(W, H), o = od.data;
  let lmin = 255, lmax = 0;
  for(let i = 0; i < W*H; i++) if(m[i] > .5){ const l = ds[i*4]; if(l < lmin) lmin = l; if(l > lmax) lmax = l; }
  const rango = Math.max(30, lmax - lmin);
  for(let i = 0; i < W*H; i++){
    const a = Math.max(0, Math.min(1, (m[i] - .35) / .3));        // borde suave
    const yeso = 95 + 140 * (ds[i*4] - lmin) / rango;              // 95..235: volumen con sombras
    const fondo = 128;
    const v = fondo*(1-a) + yeso*a;
    o[i*4] = o[i*4+1] = o[i*4+2] = Math.round(v); o[i*4+3] = 255;
  }
  // contorno: donde la máscara cruza 0.5
  for(let y = 1; y < H-1; y++) for(let x = 1; x < W-1; x++){
    const i = y*W+x, adentro = m[i] > .5;
    if(adentro && (m[i-1] <= .5 || m[i+1] <= .5 || m[i-W] <= .5 || m[i+W] <= .5)){
      o[i*4] = o[i*4+1] = o[i*4+2] = 45; }
  }
  co.putImageData(od, 0, 0);
  return out;
}


// ── MANIQUÍ 3D de mujer, liso, con sombras ──
function puntos(lm, W, H){
  const P = lm.map(p => ({x:p.x*W, y:-p.y*H, z:-(p.z||0)*W*.6, v:(p.visibility==null?1:p.visibility)}));
  const mid = (a,b) => ({x:(a.x+b.x)/2, y:(a.y+b.y)/2, z:(a.z+b.z)/2, v:Math.min(a.v,b.v)});
  P.m15 = mid(P[17], P[19]); P.m16 = mid(P[18], P[20]);
  P.hombros = mid(P[11], P[12]); P.cadera = mid(P[23], P[24]); P.orejas = mid(P[7], P[8]);
  return P;
}
const dist = (a,b) => Math.hypot(a.x-b.x, a.y-b.y, a.z-b.z);
const lerp = (a,b,t) => ({x:a.x+(b.x-a.x)*t, y:a.y+(b.y-a.y)*t, z:a.z+(b.z-a.z)*t});
function malla(T, g, mat){ const m = new T.Mesh(g, mat); m.castShadow = true; m.receiveShadow = true; return m; }
// Segmento que se afina: de radio ra (en a) a rb (en b), con las puntas redondeadas.
function segmento(T, mat, a, b, ra, rb){
  const L = dist(a,b); if(!(L > 1)) return [];
  const m = malla(T, new T.CylinderGeometry(rb, ra, L, 22, 1, true), mat);
  m.position.set((a.x+b.x)/2, (a.y+b.y)/2, (a.z+b.z)/2);
  m.quaternion.setFromUnitVectors(new T.Vector3(0,1,0), new T.Vector3(b.x-a.x, b.y-a.y, b.z-a.z).normalize());
  return [m, bola(T, mat, a, ra), bola(T, mat, b, rb)];
}
function bola(T, mat, p, r){ const m = malla(T, new T.SphereGeometry(r, 24, 16), mat); m.position.set(p.x,p.y,p.z); return m; }
function ovalo(T, mat, c, dir, rx, ry, rz){
  const m = malla(T, new T.SphereGeometry(1, 32, 24), mat);
  m.position.set(c.x, c.y, c.z); m.quaternion.setFromUnitVectors(new T.Vector3(0,1,0), dir); m.scale.set(rx, ry, rz); return m;
}
function armarManiqui(T, P, mat){
  const g = new T.Group(), add = o => { if(!o) return; (Array.isArray(o) ? o : [o]).forEach(x => x && g.add(x)); };
  const s = Math.max(20, Math.hypot(P[11].x-P[12].x, P[11].y-P[12].y));
  const ok = (...js) => js.every(j => P[j] && P[j].v >= .3);   // lo que no se ve (fuera de cuadro) no se inventa
  // brazos (se afinan hacia la muñeca)
  if(ok(11,13)) add(segmento(T, mat, P[11], P[13], .12*s, .09*s));
  if(ok(13,15)) add(segmento(T, mat, P[13], P[15], .09*s, .065*s));
  if(ok(12,14)) add(segmento(T, mat, P[12], P[14], .12*s, .09*s));
  if(ok(14,16)) add(segmento(T, mat, P[14], P[16], .09*s, .065*s));
  if(ok(15)) add(segmento(T, mat, P[15], P.m15, .065*s, .05*s));
  if(ok(16)) add(segmento(T, mat, P[16], P.m16, .065*s, .05*s));
  // piernas (muslo ancho → rodilla → tobillo fino)
  if(ok(23,25)) add(segmento(T, mat, P[23], P[25], .2*s, .12*s));
  if(ok(25,27)) add(segmento(T, mat, P[25], P[27], .11*s, .065*s));
  if(ok(24,26)) add(segmento(T, mat, P[24], P[26], .2*s, .12*s));
  if(ok(26,28)) add(segmento(T, mat, P[26], P[28], .11*s, .065*s));
  if(ok(27,31)) add(segmento(T, mat, P[27], P[31], .065*s, .045*s));
  if(ok(28,32)) add(segmento(T, mat, P[28], P[32], .065*s, .045*s));
  // torso: pecho, cintura y cadera, alineados con la columna
  const largo = dist(P.hombros, P.cadera);
  if(largo > 1){
    const col = new T.Vector3(P.hombros.x-P.cadera.x, P.hombros.y-P.cadera.y, P.hombros.z-P.cadera.z).normalize();
    const anchoH = Math.max(.6*s, Math.hypot(P[11].x-P[12].x, P[11].y-P[12].y));
    const anchoC = Math.max(.55*s, Math.hypot(P[23].x-P[24].x, P[23].y-P[24].y));
    add(ovalo(T, mat, lerp(P.hombros, P.cadera, .22), col, anchoH*.47, largo*.36, .27*s));   // pecho
    add(ovalo(T, mat, lerp(P.hombros, P.cadera, .55), col, anchoH*.36, largo*.34, .22*s));    // cintura
    add(ovalo(T, mat, lerp(P.hombros, P.cadera, .9), col, anchoC*.8, largo*.3, .28*s));   // cadera
    // busto suave, hacia donde mira el pecho (hacia la cámara si está de frente)
    for(const lado of [11, 12]){
      const b = lerp(lerp(P.hombros, P[lado], .5), P.cadera, .3);
      add(bola(T, mat, {x:b.x, y:b.y, z:b.z + .17*s}, .15*s));
    }
    add(segmento(T, mat, P[11], P[12], .11*s, .11*s));                                      // hombros
  }
  // cuello y cabeza (óvalo, con una nariz chiquita que marca hacia dónde mira)
  add(segmento(T, mat, P.hombros, lerp(P.hombros, P.orejas, .6), .11*s, .1*s));
  const rc = Math.max(dist(P[7],P[8])*.68, .3*s);
  add(ovalo(T, mat, P.orejas, new T.Vector3(0,1,0), rc*.82, rc*1.05, rc*.9));
  const hacia = new T.Vector3(P[0].x-P.orejas.x, P[0].y-P.orejas.y, P[0].z-P.orejas.z);
  if(hacia.length() > 1){ hacia.setLength(rc*.85); add(bola(T, mat, {x:P.orejas.x+hacia.x, y:P.orejas.y+hacia.y, z:P.orejas.z+hacia.z}, rc*.16)); }
  return g;
}
function renderManiqui(T, personas, W, H){
  const renderer = new T.WebGLRenderer({antialias:true, preserveDrawingBuffer:true});
  renderer.setSize(W, H); renderer.setPixelRatio(1);
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = T.PCFSoftShadowMap;
  const scene = new T.Scene(); scene.background = new T.Color(0x8e8e8e);
  const cam = new T.OrthographicCamera(0, W, 0, -H, 0.1, 40000); cam.position.set(0, 0, 10000);
  scene.add(new T.HemisphereLight(0xffffff, 0x555555, .9));
  const sol = new T.DirectionalLight(0xffffff, 2.2);
  sol.position.set(W*.15, H*.2, 6000); sol.target.position.set(W*.55, -H*.6, -2000);
  sol.castShadow = true; sol.shadow.mapSize.set(2048, 2048);
  const sc = sol.shadow.camera; sc.left = -W*1.5; sc.right = W*1.5; sc.top = H*1.5; sc.bottom = -H*1.5; sc.near = 1; sc.far = 40000;
  sol.shadow.bias = -0.0005; scene.add(sol); scene.add(sol.target);
  const mat = new T.MeshStandardMaterial({color:0xe6e1dc, roughness:.6, metalness:0});
  let zmin = 1e9;
  personas.forEach(lm => { const P = puntos(lm, W, H); const g = armarManiqui(T, P, mat); scene.add(g);
    for(const p of lm) zmin = Math.min(zmin, -(p.z||0)*W*.6); });
  // pared del fondo: recibe las sombras (dan la profundidad: qué va adelante y qué atrás)
  const pared = new T.Mesh(new T.PlaneGeometry(W*4, H*4), new T.MeshStandardMaterial({color:0x9a9a9a, roughness:1}));
  pared.position.set(W/2, -H/2, zmin - W*.35); pared.receiveShadow = true; scene.add(pared);
  renderer.render(scene, cam);
  return renderer.domElement;
}

function abrirModal(){
  let ov = document.getElementById("pose3d-ov");
  if(ov) ov.remove();
  ov = document.createElement("div"); ov.id = "pose3d-ov";
  ov.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.82);display:flex;align-items:center;justify-content:center;padding:12px";
  ov.innerHTML = '<div style="background:var(--card,#1e1b1b);color:inherit;border-radius:14px;padding:14px;max-width:980px;width:100%;max-height:96vh;overflow:auto">'
    + '<div style="font-weight:600;margin-bottom:6px">Copiar la toma de la foto (gratis): calco o maniquí 3D</div>'
    + '<p class="hint" id="p3-est" style="margin:0 0 8px">Cargando el detector…</p>'
    + '<div style="display:flex;gap:6px;margin:0 0 8px"><button class="ghost" id="p3-m-calco">✏️ Calco</button>'
    + '<button class="ghost" id="p3-m-3d">🧍 Maniquí 3D</button></div>'
    + '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start">'
    + '<img id="p3-foto" style="max-width:48%;max-height:62vh;border-radius:10px">'
    + '<div id="p3-lienzo" style="max-width:48%"></div></div>'
    + '<p class="hint" style="margin:8px 0">El calco copia la pose, la cámara, el encuadre y las sombras de la foto, sin la cara, el maquillaje, los tatuajes ni la ropa. El maniquí 3D es una figura de mujer lisa, con sombras, armada con los puntos del cuerpo. De izquierda a derecha son la modelo A, B, C.</p>'
    + '<div style="display:flex;gap:8px;flex-wrap:wrap">'
    + '<button class="go" id="p3-usar" disabled>✅ Usar esta guía (gratis)</button>'
    + '<button class="ghost" id="p3-ia">✨ Maniquí con IA (~US$0,07)</button>'
    + '<button class="ghost" id="p3-no">Cancelar</button></div></div>';
  document.body.appendChild(ov);
  return ov;
}

// Devuelve una promesa: {modo:"3d", guia, personas} | {modo:"ia"} | null (cancelado)
async function copiarToma(file){
  const ov = abrirModal();
  const $ = id => ov.querySelector("#"+id);
  const est = t => { $("p3-est").textContent = t; };
  $("p3-foto").src = URL.createObjectURL(file);
  return new Promise(async resolve => {
    $("p3-no").onclick = () => { ov.remove(); resolve(null); };
    $("p3-ia").onclick = () => { ov.remove(); resolve({modo:"ia"}); };
    try{
      const img = $("p3-foto"); await img.decode();
      const det = await cargarDetector();
      est("Calcando…");
      const res = det.detect(img);
      const personas = (res && res.landmarks) || [];
      const crudas = (res && res.segmentationMasks) || [];
      if(!personas.length || !crudas.length){ est("No encontré a nadie en la foto. Probá ✨ Maniquí con IA."); return; }
      const esc = Math.min(1, 1024 / Math.max(img.naturalWidth, img.naturalHeight));
      const W = Math.round(img.naturalWidth*esc), H = Math.round(img.naturalHeight*esc);
      const masks = crudas.map(mk => { const d = {width:mk.width, height:mk.height, data:mk.getAsFloat32Array()};
                                       try{ mk.close && mk.close(); }catch(e){} return d; });
      // Las caras: entre las orejas, del tamaño de la cabeza
      const caras = personas.map(lm => {
        const a = lm[7], b = lm[8], n = lm[0];
        const r = Math.max(Math.hypot((a.x-b.x)*W, (a.y-b.y)*H) * 0.85, W*0.03);
        return {x:(a.x+b.x+n.x)/3*W, y:(a.y+b.y+n.y)/3*H, r};
      });
      // ¿El detector entendió los cuerpos? En fotos con varias personas muy encimadas a veces junta
      // todo en un esqueleto encogido (hombros del ancho de la cabeza): eso no sirve de guía.
      const malas = personas.filter(lm => {
        const d = (a,b) => Math.hypot((lm[a].x-lm[b].x)*W, (lm[a].y-lm[b].y)*H);
        const cab = Math.max(d(7,8), 1), torso = Math.hypot(((lm[11].x+lm[12].x)-(lm[23].x+lm[24].x))*W/2, ((lm[11].y+lm[12].y)-(lm[23].y+lm[24].y))*H/2);
        return d(11,12) < 1.4*cab || torso < 1.3*cab;
      }).length;
      const aviso = malas ? " ⚠️ El detector no entendió bien " + (malas === personas.length ? "los cuerpos" : malas + " de los cuerpos")
                          + " (pasa con personas muy encimadas). Para esta foto conviene ✨ Maniquí con IA." : "";
      // De izquierda a derecha: A, B, C… (para el maniquí)
      const orden = personas.map(lm => lm.map(p => ({...p}))).sort((a,b) => ((a[23].x+a[24].x) - (b[23].x+b[24].x)));
      const vistas = {calco: calcar(img, masks, caras, W, H), m3d: null};
      let modo = "calco";
      const mostrar = async m => {
        if(m === "m3d" && !vistas.m3d){
          est("Armando el maniquí 3D…");
          try{ vistas.m3d = renderManiqui(await cargarThree(), orden, W, H); }
          catch(e){ est("No pude armar el maniquí 3D (" + (e && e.message || e) + "). Usá el calco."); return; }
        }
        modo = m; const cv = vistas[m];
        cv.style.cssText = "width:100%;height:auto;border-radius:10px";
        $("p3-lienzo").innerHTML = ""; $("p3-lienzo").appendChild(cv);
        $("p3-m-calco").style.outline = m === "calco" ? "2px solid var(--rose-deep,#e0607e)" : "";
        $("p3-m-3d").style.outline = m === "m3d" ? "2px solid var(--rose-deep,#e0607e)" : "";
        est((m === "calco" ? "Calco" : "Maniquí 3D") + " de " + personas.length + (personas.length === 1 ? " persona." : " personas.")
            + " Elegí el que mejor copie la toma." + aviso);
      };
      $("p3-m-calco").onclick = () => mostrar("calco");
      $("p3-m-3d").onclick = () => mostrar("m3d");
      await mostrar("calco");
      $("p3-usar").disabled = false;
      $("p3-usar").onclick = () => {
        const guia = vistas[modo].toDataURL("image/jpeg", .9);
        ov.remove(); resolve({modo:"3d", guia, personas: personas.length, tipo: modo});
      };
    }catch(err){
      console.warn("calco", err);
      est("No pude calcar en este navegador (" + (err && err.message || err) + "). Usá ✨ Maniquí con IA.");
    }
  });
}
window.Pose3D = {copiarToma};
"""
