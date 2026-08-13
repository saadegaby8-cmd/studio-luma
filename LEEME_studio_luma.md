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
con el mismo motor de imágenes de Luma (plano entero, medio 3/4, espalda, los
macros y el hero). Después cada una de esas fotos es el PRIMER CUADRO
literal del clip, y al modelo de video sólo se le pide el movimiento de cámara.
Los modelos de video no saben ponerle tu prenda a una modelo: si el clip arranca
de un flat-lay, inventan la prenda y la cara. Arrancando de un cuadro que ya es
exactamente lo que querés, sólo tienen que moverlo.

La PRIMERA toma es el ancla: las demás se generan mirándola, y por eso la cara,
la luz y el blanco no cambian de toma en toma. El inspector de prenda (el mismo
de las fotos) revisa esa toma ancla: si la prenda salió distinta de la real, la
rehace antes de gastar un peso en video.

### Varias modelos, varios colores (los "looks")

Un **look** es una modelo con su color. Si querés un video con cuatro modelos,
cada una con la prenda en otro color y cada una haciendo su toma, se prende
**"Varias modelos / varios colores"** arriba de las fotos:

- Cada foto muestra un numerito arriba a la izquierda: tocalo para mandarla al
  look 1, 2, 3 o 4. Tocando la foto (no el numerito) la ponés como la que
  **manda** en su look: de ella salen la cara, el cuerpo y el color.
- A cada look le podés poner nombre ("Coral", "Azul"). Ese nombre viaja al
  prompt como el color de esa toma, que es lo que evita que se le escape el
  color de la toma de al lado.
- Abajo de las tomas aparece **"De qué look sale cada toma"**: una fila por
  toma, en orden, y elegís de qué look sale.
- Hasta 4 looks y 12 fotos por video.

**Por qué hace falta y no alcanza con subir las fotos juntas.** El ancla es una
sola por look, no una por video: sin esto, las cuatro modelos salían con la cara
de la principal, porque todas las tomas miran a la primera. Y las fotos que no
son la principal entran al prompt como *la verdad del diseño de la prenda*, así
que pasarle cuatro colores juntos es pedirle una prenda de cuatro colores a la
vez — de ahí salían los tonos raros. Ahora cada toma ve SÓLO las fotos de su
look.

Ojo con el gasto: el inspector de prenda revisa **una vez por look** (antes era
una sola vez en todo el video), y cada revisión que sale mal paga un cuadro
extra. Con 4 looks eso son, como mucho, 4 cuadros más.

- **Las tomas las elegís vos.** Tocás las que querés y quedan en el orden en que
  las tocaste (el numerito del chip). Hasta 8 por video. Además del plano
  entero, el 3/4, la espalda, la caminata y el hero, están los macros: el del
  **frente** (escote, drapeado, costura), el de la **espalda** (breteles,
  cierre, terminación de atrás) y el de **abajo** (short, bombacha o bikini:
  cintura, ruedo, cómo calza).
- **Y si el detalle que querés no está en la lista, lo escribís vos.** El botón
  "+ Una toma mía" agrega un renglón donde ponés qué se ve, en castellano ("primer
  plano del ruedo del short, de costado"). Ese texto es el encuadre del cuadro
  llave, y para el clip se traduce solo al inglés, que es el idioma en el que los
  motores de video entienden mejor. Hasta 4 tomas tuyas por video.
### Bajarle el precio: quién mueve cada toma

**El motor de video es el 90% de lo que sale un video.** Las 5 tomas de 6s por
defecto son US$0,51 de cuadros y US$4,50 de video. Por eso lo que baja la cuenta
no es ahorrar en las imágenes, es elegir bien quién mueve cada toma. En la lista
**"El video, toma por toma"** cada una tiene su motor:

- **Con IA** (Veo / Wan): mueve a la modelo de verdad — respira, camina, la tela
  se sacude. Es lo único que sirve donde el cuerpo se mueve.
- **Sólo cámara** (US$0): el movimiento lo hace ffmpeg recortando el cuadro,
  como en una mesa de edición. Tarda segundos, no cuesta nada, y el detalle sale
  pixel por pixel de la foto: no hay forma de que invente una costura. La contra
  es que mueve la CÁMARA, no a la modelo — en un macro no se nota, en un plano
  entero la modelo queda congelada y sí se nota (el panel te avisa en esas
  tomas).

Y con **"Mis fotos YA son las tomas"** no se dibuja ningún cuadro: la foto 1 es
la toma 1, la foto 2 la toma 2, y no pagás imágenes. Sirve cuando ya tenés las
fotos hechas en Luma y sólo querés el armado.

Las dos cosas juntas, en un video de 6 tomas con 2 de IA y 4 de cámara:
**US$1,80 contra US$6,01**. El estimador ya lo muestra desglosado antes de
generar.

- **Mirá los cuadros primero.** El botón "Ver los cuadros primero" genera sólo
  las fotos (centavos) y no toca el video. Si te gustan, generás el video.
- **Tope por video.** Si el video no entra en el tope que pusiste, saca tomas
  del final y te avisa cuáles. El tope MENSUAL de Presupuesto sigue mandando
  igual, y lo gastado se anota aunque el trabajo falle a la mitad.
- **Audio**: mudo (como los videos de las marcas), o con locución argentina y
  subtítulos. La música se sube una vez y queda para todos tus videos.
- **Precio**: se ve antes de generar. Las 5 tomas de 6s que vienen por defecto
  salen ~US$5 en Veo Fast; el mismo video con Wan (necesita `FAL_KEY`), ~US$2.
  Los motores, de más barato a más caro por segundo: Wan US$0,05 · Veo Lite
  US$0,08 · Seedance US$0,09 · **Veo Fast US$0,15** (el que viene puesto) ·
  MiniMax H3 US$0,26 · Veo 3.1 US$0,40. MiniMax H3 es el nuevo de fal y llega a
  2K, pero sale casi el doble que Veo Fast: conviene probarlo en UNA toma antes
  de mandarle el video entero.
- **Si elegís un motor y no se usa, ahora te lo dice.** Antes, un motor que este
  archivo no conocía —el caso típico es el panel viejo que quedó en la caché del
  navegador— caía a Veo Fast en silencio, y el síntoma era "cambié el motor y me
  siguió usando Gemini". Y si elegís un motor de fal sin la key cargada, el
  trabajo se frena ANTES de dibujar los cuadros, que son los que se pagan.

Las tomas que vienen marcadas (caminata → giro → espalda → macro → hero, 30
segundos) salen de medir un video de catálogo real: 6 clips de 5 segundos,
cortes secos y sin audio. El zoom pasa ADENTRO de cada clip, no sólo al cortar:
la toma de espalda arranca con la modelo entera y termina en primer plano de
los breteles.

**Por qué antes se veía "de IA".** Los modelos embellecen solos: alisan la piel,
emparejan la cara, afinan el cuerpo y planchan la tela, y ahí la modelo deja de
parecer una persona. Ahora se les pide lo contrario con nombre y apellido —
poros, lunares, líneas de expresión, el mismo cuerpo de la foto, la tela con sus
arrugas—. En el movimiento pasaba lo mismo por otro lado: salía en cámara lenta,
con la modelo dura como maniquí y los pies patinando sobre el piso. Ahora el clip
va a velocidad real, la modelo respira y parpadea, los pies apoyan de verdad
(talón y punta) y la cámara va sobre slider, firme, en vez del temblequeo de
cámara en mano que le pedíamos antes.

Variables opcionales en Railway:
- `FAL_KEY` — habilita los motores de fal (Wan, Seedance, MiniMax H3).
- `FAL_MINIMAX_MODEL` — sólo si fal le cambia la ruta al modelo de MiniMax H3
  (hoy `minimax/h3/image-to-video`). Igual que `FAL_WAN_MODEL` y
  `FAL_SEEDANCE_MODEL`: se corrige sin tocar el código.
- `VIDEOS_PRECIO_MINIMAX` — si fal le cambia el precio (hoy US$0,26/s).
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
