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
const MP_URL = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs";
const MP_WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
const MP_MODEL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task";
let detector = null;

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

function abrirModal(){
  let ov = document.getElementById("pose3d-ov");
  if(ov) ov.remove();
  ov = document.createElement("div"); ov.id = "pose3d-ov";
  ov.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.82);display:flex;align-items:center;justify-content:center;padding:12px";
  ov.innerHTML = '<div style="background:var(--card,#1e1b1b);color:inherit;border-radius:14px;padding:14px;max-width:980px;width:100%;max-height:96vh;overflow:auto">'
    + '<div style="font-weight:600;margin-bottom:6px">✏️ Copiar toma calcando la foto (gratis)</div>'
    + '<p class="hint" id="p3-est" style="margin:0 0 8px">Cargando el detector…</p>'
    + '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start">'
    + '<img id="p3-foto" style="max-width:48%;max-height:62vh;border-radius:10px">'
    + '<div id="p3-lienzo" style="max-width:48%"></div></div>'
    + '<p class="hint" style="margin:8px 0">El calco copia la pose, la cámara, el encuadre y las sombras de la foto, sin la cara, el maquillaje, los tatuajes ni la ropa. De izquierda a derecha son la modelo A, B, C.</p>'
    + '<div style="display:flex;gap:8px;flex-wrap:wrap">'
    + '<button class="go" id="p3-usar" disabled>✅ Usar este calco (gratis)</button>'
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
      const cv = calcar(img, masks, caras, W, H);
      cv.style.cssText = "width:100%;height:auto;border-radius:10px";
      $("p3-lienzo").appendChild(cv);
      est("Listo: " + personas.length + (personas.length === 1 ? " persona calcada." : " personas calcadas."));
      $("p3-usar").disabled = false;
      $("p3-usar").onclick = () => {
        const guia = cv.toDataURL("image/jpeg", .9);
        ov.remove(); resolve({modo:"3d", guia, personas: personas.length});
      };
    }catch(err){
      console.warn("calco", err);
      est("No pude calcar en este navegador (" + (err && err.message || err) + "). Usá ✨ Maniquí con IA.");
    }
  });
}
window.Pose3D = {copiarToma};
"""
