# Studio Luma — Guía para publicarlo (desde la tablet)

Este paquete es tu app de generación de imágenes, **separada de ML×TN Sync**,
lista para correr sola en su propio host y dominio.

## Archivos que van al repo (los 5, en la RAÍZ del repo)
- `main.py` — el cascarón que levanta la app en la raíz "/"
- `imagenes_ia.py` — la app de fotos (lo que ya venías usando)
- `videos_luma.py` — los videos de producto (pestaña 🎬 Videos)
- `requirements.txt` — las librerías que instala Railway
- `Procfile` — cómo se arranca la app

## Pasos (todo desde el navegador de la tablet)

### 1) Crear el repositorio en GitHub
- Entrá a GitHub → New repository → nombre `studio-luma` → Create.
- Subí los 4 archivos a la raíz (Add file → Upload files).

### 2) Crear el proyecto en Railway
- Railway → New Project → Deploy from GitHub repo → elegí `studio-luma`.
- **NO lo pongas dentro del proyecto de ML×TN.** Es un proyecto nuevo.
- Railway detecta Python y hace el build solo.

### 3) Agregar Redis (nuevo, propio de Studio Luma)
- Dentro del proyecto → New → Database → Redis.
- Copiá su URL de conexión y cargala como variable `REDIS_URL` (ver paso 4).

### 4) Cargar las variables de entorno (Settings → Variables)
- `REDIS_URL`      = (la del Redis nuevo)
- `GEMINI_API_KEY` = tu API key de Google — **usá una NUEVA, separada** de la
  de tu negocio, así medís el gasto de Studio Luma aparte.
- (Opcionales de Google Drive, solo si vas a usar la galería.)
- `IMAGENES_PREFIX` NO hace falta tocarla: el `main.py` ya la deja en "" (raíz).

### 5) Generar dominio de prueba
- Settings → Networking → Generate Domain.
- Te da algo tipo `studio-luma-production.up.railway.app`. Entrá y probá.

### 6) Conectar tu dominio propio
- Settings → Networking → Custom Domain → escribí tu dominio
  (ej. `app.studioluma.com`).
- Railway te da un registro **CNAME**. Cargalo en el panel DNS de donde
  compraste el dominio. En minutos/horas queda con HTTPS automático.

## Videos de producto (pestaña 🎬 Videos, en `/videos`)

Hace el video de vidriera blanca: tu modelo con tu prenda parada en un limbo
blanco infinito —sin paredes, sin esquinas, sin horizonte, sólo la sombra de
contacto en el piso— y la cámara entrando hacia el detalle de la prenda.

Subís la foto de tu publicación (la que YA tiene a la modelo con la prenda
puesta) y sale de ahí: misma cara, mismo cuerpo, misma prenda.

**Son dos etapas, y por eso funciona.** Primero cada toma se dibuja como FOTO
con el mismo motor de imágenes de Luma (plano entero, medio 3/4, macro del
detalle, espalda, hero). Después cada una de esas fotos es el PRIMER CUADRO
literal del clip, y al modelo de video sólo se le pide el movimiento de cámara.
Los modelos de video no saben ponerle tu prenda a una modelo: si el clip arranca
de un flat-lay, inventan la prenda y la cara. Arrancando de un cuadro que ya es
exactamente lo que querés, sólo tienen que moverlo.

La PRIMERA toma es el ancla: las demás se generan mirándola, y por eso la cara,
la luz y el blanco no cambian de toma en toma. El inspector de prenda (el mismo
de las fotos) revisa esa toma ancla: si la prenda salió distinta de la real, la
rehace antes de gastar un peso en video.

- **Mirá los cuadros primero.** El botón "Ver los cuadros primero" genera sólo
  las fotos (centavos) y no toca el video. Si te gustan, generás el video.
- **Tope por video.** Si el video no entra en el tope que pusiste, saca tomas
  del final y te avisa cuáles. El tope MENSUAL de Presupuesto sigue mandando
  igual, y lo gastado se anota aunque el trabajo falle a la mitad.
- **Audio**: mudo (como los videos de las marcas), o con locución argentina y
  subtítulos. La música se sube una vez y queda para todos tus videos.
- **Precio**: se ve antes de generar. Con 4 tomas de 6s en Veo Fast son ~US$4;
  con Wan (necesita `FAL_KEY`) el mismo video sale ~US$1,60.

Variables opcionales en Railway:
- `FAL_KEY` — habilita los motores baratos (Wan / Seedance).
- `VIDEOS_PREFIX` — si querés los videos en otra ruta que no sea `/videos`.

Dos cosas para tener en cuenta:
- **Veo necesita una key de Google con facturación habilitada.** Sin eso te
  responde 403 y hay que usar Wan o Seedance.
- **Los videos viven en el disco del server.** Sin un volumen montado en
  `/data`, se borran en cada deploy: bajá el que te gustó.

## Actualizaciones (igual que ML×TN)
- Cambiás archivos → los subís al repo → Railway redeploya solo → hard refresh.

## Importante
- Esta versión **no tiene login**: cualquiera con el link entra. Ideal para
  probar con tus papás. Antes de promocionar a desconocidos hay que sumar
  login + datos por usuario + medición de consumo + cobro (Mercado Pago).
- Mantené las API keys SOLO en las Variables de Railway, nunca dentro de los
  archivos del repo.
