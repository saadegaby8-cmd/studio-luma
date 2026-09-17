# Studio Luma — Guía para publicarlo (desde la tablet)

Este paquete es tu app de generación de imágenes, **separada de ML×TN Sync**,
lista para correr sola en su propio host y dominio.

## Archivos que van al repo (los 6, en la RAÍZ del repo)
- `main.py` — el cascarón que levanta la app en la raíz "/"
- `imagenes_ia.py` — la app de fotos (lo que ya venías usando)
- `videos_luma.py` — los videos de producto (pestaña 🎬 Videos)
- `personajes.py` — tu persona digital (pestaña 👤 Personajes)
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

## Fotos con Seedream: por qué salían todas las poses iguales

Seedream es un editor multi-referencia: **copia la composición de la primera
imagen que recibe**. Le mandábamos el retrato entero del avatar (plano medio,
de frente, mirando a cámara) y todas las tomas del set salían con esa misma
pose aunque el texto pidiera otra. Ahora le va **sólo la cara** del avatar:
Gemini ubica el recuadro una vez (queda en caché por avatar), se recorta con
margen para el pelo, y el prompt le aclara que esa imagen es identidad y nada
más. La pose la manda el texto. Se apaga en Ajustes → "Seedream: mandar sólo
la cara del avatar" si preferís el retrato entero.

Y había dos inconsistencias más entre motores, ya corregidas:
- **La "Pose" escrita en el formulario de Generar pisaba las poses del set.**
  Con Nano Banana la pose forzada de cada toma mandaba; con Seedream mandaba el
  texto del formulario, así que si ahí había algo escrito (o venía de una
  plantilla) TODAS las tomas salían con esa pose. Ahora los dos motores usan
  la misma prioridad: pose forzada del plan > pose escrita > pool por sorteo.
  En una toma del set con pose del plan, la "Pose" del formulario no entra.
- **Seedream no hace paneles.** La toma "21:9 con 2 paneles" del set básico
  salía como UNA foto apaisada con una sola pose (siempre la 0) que después se
  recortaba. Con Seedream cada panel pasa a ser una toma 4:5 con su propia
  pose del pool. El motor queda fijado al crear el set.

**El checker de salida de Seedream.** Cuando fal dice "The content could not be
processed because it contained material flagged by a content checker" y tardó
50 a 65 s, es el checker de SALIDA de ByteDance: la imagen se generó, se revisó
y se tiró. No se apaga desde fal. En la prueba del 14/9 pasaba la toma de
frente y rebotaban 4 de 5 con las poses variadas: en lencería, la acostada en
el piso, la de espalda con la mano en la nuca o la de acomodarse el bretel le
disparan el checker. Por eso:
- En lencería y baño, cada pose del pool usa su **versión de catálogo** (misma
  variedad: parada, sentada, de espalda, caminando, perfil…) con menos riesgo.
  Ajuste "Seedream: poses de catálogo en lencería y baño" (sí por defecto).
- Si igual rebota, la toma se **reintenta sola con la pose segura** en el mismo
  Seedream Pro, antes de sanear el prompt y de caer al modelo de respaldo. Y el
  rechazo del checker ya no dispara los 4 reintentos "a ciegas" que había para
  errores de validación (cada uno de ~60 s): eso era la seguidilla de fallos
  de 50 s en el panel de fal.
- El saneado del prompt también baja los superlativos del cuerpo ("extra
  grande y voluminoso", "volumen marcado"): al checker le pesan más que a la
  foto; queda "talle grande", que es lo que importa para el calce.

**¿Hay algo mejor que Seedream para lencería con poses provocativas?** El
checker de salida de Seedream no se apaga, así que para poses provocativas
hay que ir a un modelo de pesos abiertos, que en fal no tiene checker propio.
Las dos opciones probadas en la app (se cambian en Ajustes → Motor FLUX →
"Modelo try-on", sin tocar código):
- `fal-ai/qwen-image-edit-2511` (Alibaba, Apache 2.0): multi-referencia,
  identidad fuerte, acepta LoRA (se le puede entrenar la avatar), US$0,035
  por megapíxel. Es el que usan los generadores de lencería "sin filtro".
- `fal-ai/flux-2/edit` (Black Forest Labs, pesos abiertos): multi-referencia
  hasta 4 imágenes (la persona + 3 vistas de la prenda), US$0,012/MP.
- `bytedance/seedream/v5/pro/edit`: la mejor calidad de imagen, pero con su
  checker de salida. Queda como opción para poses de catálogo.
Ojo: en Qwen y FLUX, apagar el safety checker de fal (`enable_safety_checker`)
**requiere que la cuenta de fal esté habilitada para contenido sin filtro**;
sin eso fal lo revisa igual. Se pide desde el panel de fal. La app ya manda
a cada motor sólo los parámetros que entiende (guidance y safety_tolerance
sólo a FLUX), le manda a FLUX como mucho 4 referencias, y aplica las poses de
catálogo y el reintento con pose segura sólo cuando el motor es Seedream.

**Breteles duplicados / "producto fantasma" con varias fotos del conjunto.**
Los editores de fal reciben las fotos del producto sin ningún texto entre
medio (a diferencia de Nano Banana, donde cada foto viaja con su rótulo). Con
la foto del corpiño, la de la bombacha y la de espalda, el motor entendía que
eran tres prendas y las sumaba: la modelo salía con 4 breteles en vez de 2 y
con un arnés de más encima del real. Desde v2.41.0 el prompt de fal dice por
número qué es cada foto ("image 2 = FRONT view; image 3 = the BOTTOM piece;
image 4 = BACK view"), que son vistas de UN solo conjunto (un top y una parte
de abajo), que un bretel que aparece en dos fotos se dibuja una sola vez y que
la cantidad de breteles es la de la foto de frente. Usa las etiquetas que le
pusiste a cada foto (frente / abajo / espalda / detalle...), así que conviene
etiquetarlas; sin etiquetas, la foto de espalda igual se marca como espalda
por el plan del set. Con FLUX.2 (4 referencias en total) rotula sólo las 3
fotos que de verdad viajan.

**El rechazo del checker que "moría" sin reintento (v2.41.1).** En producción
el 422 de Seedream trae las dos frases juntas: "Error validating the input" y
"flagged by a content checker". La app miraba primero la de "validating" y
cortaba ahí, así que la pose segura y el saneado del prompt nunca llegaban a
correr. Ahora el checker se detecta primero. Además el saneado cambia marcas
de revistas para adultos que se escriban en la pose ("modelo Playboy de los
70" → "1970s glamour magazine"): el checker las lee como pedido de desnudo
aunque la prenda esté puesta. Y el detalle exacto del rechazo queda en "Ver
diagnóstico".

## Nenas / nenes con modelo: por qué "no andaba" (v2.42.0)

Con modelo (pijamas, ponchos, remeras, buzos, vestidos) la toma se bloqueaba o
salía cualquier cosa por tres motivos que se juntaban:

- **El prompt nombraba justo lo que no queríamos.** Decía "PROHIBIDO: malla,
  bikini, ropa interior, poses sexualizadas" al lado de "una nena de 7 años",
  y arriba iba la instrucción de marca de los Ajustes ("LUMA Íntima, ropa
  interior y prendas íntimas"). Los filtros de imagen leen esas palabras como
  si fueran el pedido, aunque estén en una prohibición, y bloquean. Ahora el
  prompt de kids va sin la instrucción de marca, sin nombrar la marca, y dice
  en positivo lo que sí queremos ("la prenda puesta completa, cara lavada,
  como un catálogo de ropa de chicos").
- **Si el motor estaba en FLUX, la nena iba a Seedream.** Seedream recibía el
  prompt de kids (largo y en castellano) y lo rechazaba ("Error validating
  the input") o lo tiraba su checker. Ahora nenas/nenes van SIEMPRE a Nano
  Banana, esté el motor como esté.
- **El reintento tras un bloqueo repetía el mismo prompt.** Ahora reintenta
  con un prompt mínimo y neutro (450 caracteres: quién, la prenda de la foto,
  pose, escenario).

Además, la regla "malla o ropa interior → prenda sola" miraba también la ficha
automática, que podía decir "no es ropa interior" y mandaba el pijama a prenda
sola sin avisar. Ahora mira sólo lo que escribís vos (Producto, Descripción,
Piezas, Aclaraciones), y si lo manda a prenda sola te dice qué palabra fue
("la palabra "malla" en Piezas") al arrancar y en el resultado.

**El menú de nenas/nenes es el mismo de adultos (v2.43.0).** Al elegir "Nenas /
Nenes" en "¿Qué vas a fotografiar?", el panel Generar queda igual que para
adultos: se esconden los avatares y el tilde "Sin avatar" (el chico lo inventa
siempre la IA), y el cajón de apariencia pregunta nena o nene, edad/talle,
etnia, pelo (rubio, castaño, morocho, pelirrojo, rulos...), ojos, altura para
su edad, peinado y un detalle libre. En Opciones avanzadas se esconde lo que
es de cuerpo adulto (busto, cola, abdomen, contextura, edad, altura, bombacha
haciendo juego). "Generar imágenes" hace una toma 4:5 con una pose de chico al
azar; "Set completo" usa el mismo selector de poses de siempre, pero con las
poses de chico (de pie riéndose, corriendo, sentado jugando, de espaldas,
saltando...) más el producto colgado. No hay más cajón ni botón aparte de
kids, ni set de nenas separado. Las prendas solas (mallas, bikinis, o
cualquier prenda sin modelo) van por la pestaña **Producto**, como siempre.

**Kids respeta el motor de Ajustes (v2.45.0).** En v2.42 había forzado Nano
Banana para nenas/nenes porque Seedream rechazaba el prompt de kids (largo y
en castellano). Ahora, si el motor de Ajustes es FLUX/Seedream, la nena va a
fal con un prompt propio en inglés, corto y limpio (quién es, rasgos, la
prenda de las fotos rotuladas por número, la pose de chico en inglés,
escenario y luz), y en automático el rescate a fal también aplica a kids. Y
si un pedido de kids llega sin decir "con modelo" (página vieja en caché,
plantilla guardada), igual va con modelo: sólo cae a "prenda sola" (maniquí
fantasma) cuando lo que escribiste dice malla/bikini/ropa interior, y en ese
caso "Ver diagnóstico" muestra en la nota de la toma qué palabra fue.
Los restos de "prohibido menores" de la pestaña Producto y del prompt de
producto se sacaron: la prenda sola sirve para adultos o chicos por igual.

## Encuadre de la prenda (adultos, v2.44.0)

El "Encuadre" escrito viajaba como un renglón más de la puesta en escena, y
cada pose del set trae su propio tamaño de plano ("CUERPO ENTERO", "PLANO
MEDIO") con la orden de respetarlo "exactamente". La pose siempre le ganaba, y
una bombacha salía de pies a cabeza. En Opciones avanzadas hay ahora un
selector **Encuadre de la prenda**: prenda de ABAJO (de la cintura para abajo,
sin cara), prenda de ABAJO de cerca (cintura a rodillas), prenda de ARRIBA (de
la cintura para arriba, con cara), prenda de ARRIBA de cerca (hombros a
cintura), plano medio, o cuerpo entero siempre. Ese encuadre MANDA sobre el
tamaño de plano de cualquier pose: la pose aporta la postura, la orientación y
el gesto, y a las poses del pool se les saca el "CUERPO ENTERO" / "FULL BODY".
Vale para la foto suelta, el set de poses (incluidos los paneles: todos con el
mismo encuadre) y el set de colores, en Nano Banana y en fal. Se guarda en las
plantillas de artículo. El campo de texto libre sigue existiendo como
"Encuadre extra". Si Nano Banana bloquea una toma sin cara (a veces pasa con la
cintura para abajo en lencería), el reintento seguro va sin el encuadre por
zona; con Qwen o FLUX no hay ese problema.

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
  El renglón muestra el número de orden de esa toma, o un "–" si quedó afuera de
  la lista: escribir el texto no alcanzaba, había que tener el chip prendido, y
  el texto seguía en pantalla igual. Ahora, si escribís en una que estaba
  afuera, vuelve sola; y si igual quedó afuera, al generar te frena y te avisa
  en vez de sacar el video sin ella.
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

- **La prenda deja de cambiar de toma en toma.** Antes de dibujar nada, Luma
  mira tus fotos y escribe una ficha de la prenda: el color exacto de cada
  parte, la tela y los detalles. Esa ficha va como TEXTO en todas las tomas —el
  texto no se desvía, una foto de referencia sí se interpreta— y el inspector
  revisa TODAS las tomas, no sólo la primera. Sin esto, un pijama bordó con
  encaje negro salía con la espalda negra satinada y lisa: otra prenda.
- **Marcá de qué lado es cada foto.** Debajo de cada una dice FRENTE, y
  tocándola cambia a PERFIL o ESPALDA. Con eso, el análisis de la prenda sabe
  cuál es la espalda de verdad (y si no le diste ninguna, avisa "deducida, sin
  foto" en vez de inventarla), y el inspector compara una toma de atrás contra
  las fotos de atrás, no contra el frente.
- **Los NO salen del análisis de TU prenda.** El mismo análisis que usa la app
  de fotos devuelve una lista de errores típicos para esa prenda en particular
  ("no inventar encaje atrás", "mantener la escala de la estampa") y esos NO
  viajan en todas las tomas. Y en el video hay un bloque de NO fijo —no cambies
  la cara, no camines en el lugar, nada de cámara lenta— escrito DENTRO del
  prompt y no sólo en el campo de prompt negativo, porque ese campo lo lee Veo
  pero los motores de fal ni lo reciben.
- **La modelo entiende qué está vendiendo.** Cada toma lleva su intención: la de
  espalda está para mostrar los breteles y el cierre, la de abajo para mostrar
  cómo calza en la cadera. La pose, la mirada y las manos tienen que servir a
  eso. Antes salían poses lindas pero mudas, que no señalaban nada.
- **La caminata deja de parecer una cinta de correr.** La cámara está fija en
  trípode y la modelo se ACERCA: arranca chiquita y termina mucho más cerca. Si
  su tamaño en el cuadro no cambia, la toma está mal — y así salía.
- **Transiciones.** Cortes secos (lo que hacen las marcas, y sigue siendo lo que
  viene puesto), fundido a blanco o fundido cruzado, los dos de 0,35s. Sirven
  cuando una toma entera va pegada a un macro: el salto de tamaño pega feo. El
  fundido a blanco, sobre fondo blanco, casi no se nota y es el que mejor tapa
  ese salto.
- **Los macros ya no terminan en una mancha.** Pedían "la tela llenando todo el
  cuadro" y terminaban en una pared de color sin nada que mirar. Ahora cierran
  en un plano corto pero LEGIBLE, donde el detalle todavía se reconoce.
- **Un trabajo nunca queda colgado.** Termina siempre en "listo" o en "error".
  Si algo falla antes de empezar —el KV que no contesta, el disco lleno— ahora
  se ve el error en vez de quedar en "En cola…" para siempre. Y si pasan más de
  4 minutos sin novedades, el panel lo dice y te sugiere frenar: si ya hay tomas
  hechas, te arma el video con esas.
- **Cada toma dura lo que vos querés.** En la lista "El video, toma por toma",
  al lado del motor, elegís los segundos de ESA toma: con IA 4, 6 u 8 (lo que
  aceptan los motores) y con cámara 2 a 6. Una caminata necesita tiempo para
  que la modelo cruce el cuadro; un macro sobre una foto quieta a los 3 segundos
  ya mostró todo. Con un único número para todo el video, o la caminata quedaba
  corta o los macros eternos. El precio y el largo total se actualizan solos.
- **La toma de cámara se iguala a la calidad del motor.** El cuadro llave sale
  en 2K y Seedance Lite entrega 720p: pegadas una al lado de la otra, la
  diferencia canta y el video parece armado con dos cosas distintas. Ahora la de
  cámara se baja a la misma resolución real que entrega el motor. Si querés las
  dos nítidas, el que sube es el motor (Seedance Pro sale a 1080p).
- **El macro del short se toma DESDE EL COSTADO**, a la altura de la cadera. De
  frente y centrado es la toma que más falla: la rechaza el filtro de contenido
  o sale rara. De costado se ve mejor el calce y el ruedo, y no se traba.
### Sacarle el fondo a tus fotos (en vez de generar nada)

El chip **"Sacarle el fondo a mis fotos"** hace lo que harías en Canva, pero
armado: le saca el fondo a cada foto tuya, la pega sobre blanco y le dibuja la
sombra de contacto. **Eso** es cada toma, en orden.

No se dibuja nada. La prenda y la cara son las de tu foto **pixel por pixel**,
así que no hay forma de que cambien el color, el diseño ni la cara — que es todo
lo que veníamos peleando. Y sale **US$0,004 por foto** contra los ~US$0,10 de
dibujar el cuadro de cero.

La contra es una sola, y es grande: **sólo tenés las poses que ya fotografiaste**.
Si no tenés una foto de espalda, no hay toma de espalda. Generar sirve para
inventar tomas que no existen; recortar sirve para que las que sí existen queden
perfectas.

Combinado con "Sólo cámara" en todas las tomas, un video sale por centavos y no
pasa por ningún modelo generativo.

- **Una toma nunca abre el plano.** El motor de video sólo tiene el PRIMER
  cuadro: todo lo que no está ahí lo tiene que inventar, y lo inventa. Si la
  toma arranca cerrada o de espaldas y el movimiento abre para mostrar más, la
  cara que aparece es una cara NUEVA — otra persona. Por eso ahora las tomas
  sólo se acercan, nunca se alejan, y en las que no se ve la cara (los macros y
  la espalda) está prohibido que aparezca. El giro del 3/4 también se achicó:
  girar hasta quedar de frente obligaba a inventar la mitad de la cara que no
  estaba en la foto.
- **La modelo MODELA.** Cada toma tiene una acción concreta —corre el pelo para
  despejar la espalda, sigue la costura con los dedos, cambia el peso de pierna,
  baja la mirada a la prenda y la vuelve a subir— y las manos siempre hacen algo
  con intención, nunca cruzadas adelante. A una modelo no le pagan por ser
  linda: le pagan por vender la prenda.
- **El inspector ahora se ve.** Cada toma muestra su nota (`prenda 9/10`) al
  costado, verde si aprobó y roja con el detalle de las diferencias si no. Antes
  sólo quedaba registro cuando corregía, así que un inspector que no corría y
  uno que aprobaba una prenda equivocada se veían igual: en blanco. Y podés
  subirle la exigencia desde el panel — flojo (7), normal (9) o exigente (10)—
  sin tocar Ajustes. Si te aprueba una prenda que está mal, ponelo en 10.
- **Botón de frenar.** Mientras el trabajo corre hay un ✋ Frenar. Corta entre
  toma y toma: la que está en curso ya se pidió y ya se paga, pero todo lo que
  venía después no se gasta. Si frenás durante los cuadros, no se toca un peso
  de video y los cuadros hechos quedan guardados. Si frenás durante el video, se
  arma igual con las tomas que ya estaban pagas — se entrega y se sube a Drive,
  en vez de tirar a la basura lo que ya se gastó.
- **Mirá los cuadros primero.** El botón "Ver los cuadros primero" genera sólo
  las fotos (centavos) y no toca el video. Si te gustan, generás el video.
- **Tope por video.** Si el video no entra en el tope que pusiste, saca tomas
  del final y te avisa cuáles. El tope MENSUAL de Presupuesto sigue mandando
  igual, y lo gastado se anota aunque el trabajo falle a la mitad.
- **Audio**: mudo (como los videos de las marcas), o con locución argentina y
  subtítulos. La música se sube una vez y queda para todos tus videos.
- **Precio**: se ve antes de generar. Las 5 tomas de 6s que vienen por defecto
  salen ~US$5 en Veo Fast; el mismo video con Wan (necesita `FAL_KEY`), ~US$2.
  Los motores, de más barato a más caro por segundo (a 1080p, que es lo que se
  pide porque el video se entrega en 1080x1920):

  | Motor | US$/s | 5 tomas de 6s |
  |---|---|---|
  | **Seedance Lite** (el que viene puesto) | **0,036** | **1,08** |
  | LTX 2.3 Fast (fal) | 0,04 | 1,20 |
  | Wan 2.6 (fal) | 0,05 | 1,50 |
  | Veo 3.1 Lite | 0,08 | 2,40 |
  | LTX 2.5 Fast (fal) | 0,13 | 3,90 |
  | Seedance Pro (fal) | 0,148 | 4,44 |
  | Veo 3.1 Fast | 0,15 | 4,50 |
  | LTX 2.5 Pro (fal) | 0,17 | 5,10 |
  | MiniMax H3 (fal) | 0,26 | 7,80 |
  | Veo 3.1 | 0,40 | 12,00 |

  **El que viene puesto es Seedance Lite**, porque en la prueba real salió mejor
  que Veo Fast y que Wan — y sale la cuarta parte que el Veo que estaba puesto
  antes. Manda lo que se vio, no lo que decía la ficha técnica. Se cambia desde
  el panel, o para todos los videos con `VIDEOS_MOTOR_DEFAULT`.

  **Los nuevos no son más baratos: son más caros.** LTX 2.5 Pro y MiniMax H3
  salen MÁS que Veo Fast. Si querés probar uno, probalo en UNA toma antes de
  mandarle el video entero: la diferencia se paga por segundo y por toma.

  **Seedance Pro es el mismo Seedance pero a 1080p** en vez de 720p. Si el Lite
  te gustó, ese es el candidato más obvio a mejorarlo: misma familia, cuatro
  veces el precio, el doble de resolución.
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
- `FAL_MINIMAX_MODEL`, `FAL_LTX_PRO_MODEL`, `FAL_LTX_FAST_MODEL`,
  `FAL_LTX23_MODEL` — sólo si fal les cambia la ruta a esos modelos. Igual que
  `FAL_WAN_MODEL` y `FAL_SEEDANCE_MODEL`: se corrige sin tocar el código.
- `VIDEOS_PRECIO_MINIMAX`, `VIDEOS_PRECIO_LTX_PRO`, `VIDEOS_PRECIO_LTX_FAST`,
  `VIDEOS_PRECIO_LTX23` — si fal les cambia el precio. **Los precios salen de la
  documentación pública, no de una factura**: si ves que no coincide con lo que
  te cobran, corregilo acá y el estimador vuelve a decir la verdad.
- `VIDEOS_PREFIX` — si querés los videos en otra ruta que no sea `/videos`.

Dos cosas para tener en cuenta:
- **Veo necesita una key de Google con facturación habilitada.** Sin eso te
  responde 403 y hay que usar Wan o Seedance.
- **Los videos se guardan solos en tu Google Drive** apenas están listos, y el
  panel te dice si entraron de verdad (con el link) o si falló. Antes no había
  UNA línea de Drive en los videos: quedaban sólo en el disco del server, que
  sin un volumen montado en `/data` se borra en CADA deploy. Un video son varios
  dólares: perderlo es pagarlo dos veces.
- **Si Drive no está conectado, te avisa ANTES de generar**, arriba del botón.
  Enterarse después de que el video ya salió —y ya se pagó— no sirve de nada.
  Se conecta en Ajustes → Google Drive.
- **A Drive va el video Y los cuadros**, siempre. Cada cuadro es una foto de
  campaña en 2K o 4K con la modelo en fondo blanco: se paga aparte y sirve sola
  para la publicación. Un frame arrancado del video de 1080p no es lo mismo.

## Personajes (pestaña 👤 Personajes, en `/personajes`)

Tu **persona digital**: una modelo con cara y cuerpo fijos, personalidad propia,
memoria, humor que cambia con los días, y que genera contenido para la marca.
Le hablás como por WhatsApp, se saca fotos con tu ropa, te manda audios con su
voz, habla a cámara en video y cada día te propone qué publicar.

### Cómo se arma (en este orden)

1. **Crear el personaje.** Nombre, edad, ciudad, marca, personalidad, historia,
   cómo habla, qué le gusta y qué nunca hace. Y su apariencia (piel, pelo,
   ojos, contextura…) para generarle la cara. Todo se puede cambiar después
   desde la Ficha.
2. **Aprobar un retrato** (Ficha). Se genera con IA (una imagen 4K, rehacé
   hasta que te guste), o subís una foto (idealmente la original en 4K: se
   guarda a 3200 px casi sin comprimir), o usás uno de los avatares de la
   pestaña Avatares (ojo: los avatares se guardan a 1536 px aunque los hayas
   generado en 4K; si tenés el original en Drive, subilo como retrato). **Atajo:** al crear el personaje podés elegir uno de tus
   avatares directamente: su cara queda como retrato aprobado, y su ficha de
   cuerpo (contextura, altura, edad) completa la apariencia. Sólo falta la
   hoja del paso 3. Cuando aprobás, la app **estudia la cara** y guarda su
   descripción: eso, más el retrato, va en TODAS las fotos y videos que salen
   después. Por eso la cara no cambia.
3. **Generar la hoja de identidad** (Ficha). UNA imagen 4K de 3 paneles
   mirando el retrato: perfil 3/4, cuerpo entero de frente y de espalda. La
   app la corta en 3 y las guarda. Con esto queda fijo también el cuerpo.

### Qué hace

- **Charla.** Le escribís y contesta como ella, en rioplatense, corto. Si le
  pedís una foto, te contesta y aparece el botón **📸 Sacar la foto** con el
  precio: la foto se genera recién cuando tocás. Adjuntá con 📎 la foto real de
  una prenda y pedile que se la ponga: viaja como referencia de producto, con
  las mismas reglas de fidelidad de prenda que la pestaña Fotos.
- **Memoria.** De cada charla guarda hechos nuevos (hasta 40) que ve en la
  Ficha y podés editar. Los usa en las charlas siguientes.
- **Humor, energía y racha.** La energía baja si pasan días sin hablarle y
  sube con la racha de días seguidos. El humor sale de su diario y de la
  charla. Todo eso entra al cerebro: si hace 4 días que no le hablás, lo nota.
- **Hoy.** La primera vez que la abrís cada día escribe su diario (cómo
  amaneció, qué hizo) y **3 propuestas de contenido** concretas, cada una con
  escena, outfit, encuadre y caption listo, y su botón **Hacelo**.
- **🎙️ Escuchar.** Cada respuesta suya se puede oír con su voz (Gemini TTS,
  acento rioplatense; la voz se elige en la Ficha).
- **🗣️ Que hable a cámara.** Sobre una foto de la galería (o el retrato), un
  clip de 8 segundos donde dice la frase que escribas (hasta 22 palabras): Veo
  3.1 pone la voz y mueve los labios en el mismo clip. Si no sabés qué decir,
  "que lo escriba ella".
- **✨ Que se mueva.** De una foto de la galería sale un clip corto (5 o 10 s)
  con movimiento natural: respirar y mirar a cámara, caminar despacio, girar
  y volver, acomodarse el pelo, selfie en el espejo, la cámara que se acerca,
  o lo que escribas vos (en castellano, se traduce solo). Nada raro ni
  exagerado: el prompt lo prohíbe. Van por los motores de fal (Seedance, Wan,
  MiniMax) porque **no rechazan lencería ni bikinis**; Veo sí. La cara, el
  cuerpo, la prenda y el fondo son los de la foto, y el inspector revisa el
  clip al terminar. Es la forma de hacer esos videos de "modelo en ropa
  interior con movimiento natural" que se ven en Instagram, pero con TU
  personaje, no con la cara de una famosa.
- **🕺 Movete vos.** Tu video es la referencia: de él salen el movimiento,
  los gestos, la cámara y el encuadre. Es Wan 2.2 Animate por fal.ai (la misma
  key de fal que usa Videos). El modal va en 3 pasos: **1) tu video**
  (grabate en calza y remera, celular quieto, luz pareja, hasta 20 s);
  **2) cómo está vestida**: con una prenda real que adjuntás (fotos del
  producto) o con la ropa de una foto de ella; **3) el fondo**: lo describís
  y la IA lo crea, subís una foto de un lugar, el de una foto de ella, o el de
  tu video. Al tocar Generar, la app toma un cuadro de tu video y **arma la
  foto de la escena**: ella con esa ropa, en ese fondo, en tu misma postura y
  encuadre (si estás sentado, la sienta y le pone un asiento acorde). Esa
  foto queda en la galería y es la referencia del video, que sale con el
  fondo quieto. Con "el de mi video" es modo Reemplazo: ella entra en tu
  escena y queda tu audio, pero como el motor redibuja el cuadro entero el
  fondo puede "respirar" si el celular no estaba apoyado.
  **El formato también tiene que calzar.** Si tu video es horizontal y la
  escena sale vertical, el motor mete un video apaisado en un cuadro vertical:
  ella queda chiquita y lejos, aparece el borde de tu escritorio estirado y la
  escena se mezcla con la de tu video (pasó en la tercera prueba). Ahora la
  escena se genera en el formato de tu video, y si tu video es horizontal el
  modal ofrece recortarlo a vertical centrado (9:16) para reels; el cuadro
  guía se recorta igual. Lo más simple: grabate en vertical.
  **Por qué la escena tiene que calzar con tu video.** Si la referencia es de
  cuerpo entero parada y vos estás sentado en plano medio, el motor estira tu
  esqueleto para que entre en la foto y los brazos salen como tubos (pasó en
  la primera prueba). Por eso la escena se arma siempre desde un cuadro de tu
  video.
  **Cuánto tarda y la resolución.** La resolución casi no cambia el tiempo
  (ver "Tiempos reales" arriba): lo que manda son los segundos del video.
  Viene puesta 480p; 720p sale mejor y tarda casi lo mismo. El modal muestra un
  cronómetro con el estimado y el estado real de fal (en la cola, dibujando).
  **Si el server se reinicia** (un deploy) con un trabajo a medias, se retoma
  solo desde fal al volver a la galería. Y si un trabajo se perdió del todo,
  en el modal hay "Recuperar de fal": pegás el request id del panel de fal
  (fal.ai → Requests → Copy request id) y lo termina acá.
  **Motores** (se elige en el paso 1):
  *Wan 2.2 Animate* (~US$0,08/s): arma la foto de la escena primero y después
  el video; acepta lencería. *One-to-All Animation* (~US$0,10/s, a
  confirmar): pesos abiertos, transferencia "sin alineación" para cuando tu
  encuadre y el de la foto no calzan; sólo anima sobre el fondo de su foto.
  *Seedance 2.0* y *rápido*: quedan en la lista pero **rechazan videos con
  personas reales** (política de ByteDance, probado el 14/9: "may contain
  likenesses of real people"); con tu video no sirven. Kling quedó afuera:
  rechaza bikinis y lencería.
  **Tiempos reales.** Wan tardó ~75 s de proceso por cada segundo de video,
  casi igual a 480p (1234 s por 16 s) que a 720p. Un video de 16 s son 20
  minutos; uno de 6 s, unos 8. Grabá corto.
  **Modelos.** Reemplazo: `fal-ai/wan/v2.2-14b/animate/replace` (el último
  Animate que hay en fal). Animación: `fal-ai/wan/v2.2-14b/animate/move`.
  Existe una versión liviana (`fal-ai/wan-motion`, más rápida y barata) que se
  activa con `FAL_ANIMATE_MOVE_MODEL`, pero en la prueba real deformó los
  brazos. Seedance: `bytedance/seedance-2.0/reference-to-video` y
  `…/fast/reference-to-video` (`FAL_SEEDANCE_REF_MODEL`,
  `FAL_SEEDANCE_REF_FAST_MODEL`; precios en `PERSONAJES_PRECIO_SEEDANCE_REF`
  y `…_FAST`).
  **El inspector revisa el video.** Cuando el clip está listo, el mismo
  inspector de prenda de Fotos mira 3 cuadros (principio, medio, final) y te
  da una nota de 1 a 10 con lo que cambió. Adjuntá las fotos reales de la
  prenda en el modal para que compare contra el producto; sin fotos, compara
  contra la foto de referencia. La nota queda en la galería. Lo mismo pasa con
  los clips de "que hable a cámara". (Se apaga con `qc_prenda = no` en
  Ajustes, igual que en Fotos.)
  **Prenda en la mano:** todo lo que queda dentro de tu silueta se redibuja,
  la prenda incluida. Si la querés mostrar sin que la toque el motor, dejala
  colgada en una percha o sobre una mesa AL LADO tuyo, fuera de tu silueta.
- **🎬 Video.** Cualquier foto de la galería se manda a la pestaña Videos con
  un toque y entra como la foto principal del video de vidriera. Mismo flujo
  que con una foto de publicación: cuadros llave y movimiento toma por toma.
- **Galería.** Todo lo que sale (fotos y clips) queda ahí, con su caption
  editable, para rehacer (↻ con una corrección), bajar o borrar. Si Drive está
  conectado, cada foto y clip se sube solo.

### Entrenar un LoRA de ella (Ficha → Entrenamiento)

Le enseña su cara y su cuerpo al modelo de video Wan 2.2 (trainer de fal) con
la hoja, las fotos de la galería y sus videos, todo con su frase gatillo
(`NOMBRE_PJ`). Después los videos salen con ella sin foto de referencia por
pedido, y en foto a video la cara se corre menos.

- **Texto a video** aprende de fotos (y videos si hay). Sirve para el botón
  "Video 5 s" de la ficha: escribís qué hace y sale ella.
- **Foto a video** necesita VIDEOS de ella en la galería (hacé antes un par
  con "Que se mueva" o "Movete vos"). Sirve para "Que se mueva" eligiendo el
  motor "Wan 2.2 con su LoRA".
- Empezá con la **prueba corta de 100 pasos** (~US$0,40): tarda unos 10
  minutos y te dice si vale la pena el completo de 1000 (~US$4, 30 a 60 min).
  Lo ideal son 15 o más fotos; con la hoja y 10 fotos de la galería alcanza
  para probar. Se sigue en Galería → En curso y se retoma solo si el server
  se reinicia. Hasta 6 LoRAs por personaje.
- Variables: `FAL_WAN_TRAINER_T2V`, `FAL_WAN_TRAINER_I2V`, `FAL_WAN_LORA_T2V`,
  `FAL_WAN_LORA_I2V` (rutas en fal), `PERSONAJES_PRECIO_PASO_T2V/I2V` y
  `PERSONAJES_PRECIO_LORA_SEG` (precios).

### Qué cuesta

Los precios salen de los Ajustes (los mismos de Fotos) y todo pasa por el
mismo tope mensual del Presupuesto:

- Retrato: una imagen 4K. Hoja de identidad: una imagen 4K (se cambia con
  `PERSONAJES_CALIDAD_IDENTIDAD`). Los personajes nuevos sacan fotos a la
  calidad de Ajustes (4K por defecto); se baja desde la Ficha.
- Cada foto: una imagen a la calidad de la Ficha (1K, 2K o 4K).
- Charla y diario: centavos por mensaje (no se anotan en el ledger para no
  llenarlo de ruido).
- Audio: ~US$0,02. Clip hablando: 8 segundos del motor elegido (Veo Fast
  ~US$1,20; Veo estándar ~US$3,20). **Veo necesita una key de Google con
  facturación habilitada**, igual que en Videos.
- Que se mueva: los segundos del motor elegido (Seedance ~US$0,18 el clip de
  5 s; Wan ~US$0,25; MiniMax ~US$1,30).
- Movete vos: ~US$0,08 por segundo de tu video (un reel de 15 s, ~US$1,20).
  El precio es el de la documentación de fal: si no coincide con lo que te
  cobran, corregilo con `PERSONAJES_PRECIO_MOVETE`.

### Variables (opcionales)

- `PERSONAJES_PREFIX` — otra ruta que no sea `/personajes`.
- `PERSONAJES_TEXT_MODEL` — el modelo del cerebro (default `gemini-3.6-flash`;
  Google dio de baja `gemini-2.5-flash` para cuentas nuevas el 14/9/2026). Si
  Google jubila otro modelo, la app lee el que sugiere en el error y reintenta
  sola con ese (también en Videos, para el guion y las traducciones).
- `PERSONAJES_TTS_MODEL` — el modelo de voz (default `gemini-2.5-flash-preview-tts`).
- `PERSONAJES_PRECIO_MOVETE` — US$ por segundo de Movete vos (default 0.08).
- `PERSONAJES_MOVETE_MAX_SEG` — tope de segundos por video (default 20).
- `FAL_ANIMATE_REPLACE_MODEL` / `FAL_ANIMATE_MOVE_MODEL` — si fal le cambia
  la ruta a Wan Animate (default `fal-ai/wan/v2.2-14b/animate/replace` y `…/move`).

Dos cosas para tener en cuenta:
- **El personaje es inventado.** No uses la cara de una persona real que no
  sea vos: legalmente y para las plataformas tiene que ser sintético. Instagram
  y TikTok piden etiquetar el contenido generado con IA.
- **La cara en video todavía puede correrse un poco.** Por eso el clip arranca
  de una foto que ya es exactamente ella, y el prompt le prohíbe redibujarla.
  Si en un clip se corre, rehacelo: no cobran distinto por rehacer.

## Reels (pestaña 🎞️ Reels, en `/reels`) — etapa 1

Un reel vertical de Instagram donde el Personaje habla a cámara desde el
local y, entre medio, aparecen tomas de la prenda sola mientras su voz sigue.
Se entra desde Personajes (botón "🎞️ Reel" en la ficha, o el link del
encabezado). Cinco pasos, y no se gasta en video hasta el último:

1. **Producto.** Pegás el link (Tiendanube o Mercado Libre) y "Leer el link"
   trae título, descripción, precio y fotos; ML por su API pública cuando
   responde (con talles y colores), si no por la página. O cargás los datos a
   mano y subís las fotos. Elegís tono, dónde está ella (local, depósito,
   showroom, casa), duración (25/35/45 s) y cómo está vestida.
2. **Guion y voz.** Gemini escribe el guion en 5 o 7 tramos alternados: ella a
   cámara (gancho y cierre con llamado a la acción) y producto (tela, calce,
   colores, talles, precio con datos reales). Lo corregís, sumás o sacás tramos
   y "Generar voces" graba cada tramo con la voz del Personaje: ahí ves cuánto
   dura cada uno.
3. **Escenas.** Por cada tramo de ella, una foto 9:16 con Nano Banana: ella en
   el lugar elegido, la prenda de las fotos reales apoyada al lado en el
   mostrador, con distinto encuadre en cada una. Rehacés la que no te guste o
   subís la tuya.
4. **Reel.** Cada escena va a OmniHuman 1.5 (fal) con su tramo de audio: ella
   habla con labios, cara y manos sincronizados. Los tramos de producto son
   flashes de las fotos de la prenda con zoom lento (ffmpeg, sin costo). Se
   pegan los tramos, se ponen los subtítulos quemados y sale 1080x1920, con
   copia a Drive si está conectado. Si un tramo no te gusta, "Rehacer tramo"
   rehace sólo ese y vuelve a armar.

**Tus videos reales en los tramos de producto (v1.1.0).** En cada tramo de
producto hay "⬆️ Subir mis videos": hasta 3 videos tuyos (primeros planos de
la prenda, costuras, tela, filmados con el celular). Se recortan al vertical
9:16 centrado, a 1080x1920 y 30 fps, sin su audio; al armar el reel se cortan
al largo de la voz de ese tramo (repartido entre los videos si son varios; si
uno es más corto que su parte, se repite) y reemplazan a los flashes de fotos.
Quedan en el disco del servidor: si un deploy los borra, "▶" avisa y los
subís de nuevo.

**Si el server se reinicia a mitad de un reel (v1.2.0).** El trabajo va tramo
por tramo y guarda cada uno apenas sale. Si Railway reinicia (un deploy, un
corte), al volver a abrir el reel o consultar el trabajo, el vigilante lo
retoma donde quedó: los tramos ya hechos se conservan, y si un tramo de ella
estaba en fal se espera ese mismo resultado sin volver a pagarlo (hasta 3
reintentos; después avisa). Las voces, las escenas y las fotos viven en el KV
y sobreviven siempre; los videos (tramos, tus videos propios y el reel final)
viven en el disco del server, que sin un volumen montado en `/data` se borra
en cada deploy: si un tramo ya hecho desaparece, se rehace (y si es de ella,
se vuelve a pagar); el reel final queda además en tu Drive. Con un volumen en
`/data` no se pierde nada. Durante un armado largo el trabajo manda latidos,
así que nadie lo da por muerto mientras trabaja.

**Voz de influencer, mini mic y look de celular (v1.3.0).** Tres cosas que
salieron mal en el primer reel real y se corrigieron:
- *La voz.* Ya no lee "como un audio de WhatsApp": la consigna es una
  influencer argentina joven grabando a cámara, rioplatense marcado (la "y" y
  la "ll" como "sh"), voseo, sin tono de locutora. El **tono** del paso 1
  manda el estilo (canchera = rápida y con onda; cercana; divertida; seria) y
  hay un select de **voz** que arranca en "Leda · joven" (la del personaje
  suele ser más adulta). Si cambiás voz o tono, volvé a generar las voces.
  El guion también habla como influencer (mirá, posta, re, la verdad).
- *El micrófono.* "Micrófono chiquito en la mano" (sí por defecto): en la
  escena ella sostiene cerca de la boca un mini mic inalámbrico negro de
  solapa, y OmniHuman recibe la orden de no soltarlo.
- *El look.* "Look de la imagen de ella": **Celular** (por defecto) pide la
  escena como cuadro de video de celular (luces quemadas, poca nitidez,
  neblina de lente sucio, grano) y además pasa el video de ella por un filtro
  ffmpeg con ese look (bloom, negros levantados, blandura y grano);
  **Celular fuerte** exagera todo; **Limpia** deja la foto prolija sin filtro.
  Las fotos del producto y tus videos propios no se tocan.

**Cada escena con su encuadre, sus detalles y preguntas de aclaración (v1.4.0).**
En el paso 3, cada escena de ella tiene un select de **encuadre** (automático,
que va rotando entre los cuatro, o uno fijo), un campo de **detalles** (lo que
la foto no puede adivinar: qué hace con la prenda, expresión, pelo, qué hay
alrededor) y el botón **❓ Preguntame**: Gemini mira el texto del tramo, el
producto, el lugar y lo ya decidido, y hace 3 o 4 preguntas cortas con
opciones (la primera es la que recomienda). Elegís una opción o escribís la
tuya y la respuesta se suma a los detalles; después "Generar escena". Los
detalles y el encuadre quedan guardados aunque corrijas el texto del tramo.

### Etapa 2 (v2.0.0): clips IA del producto, música, precio y talles en pantalla, plantillas

- **Clips IA de la prenda.** En cada tramo de producto (paso 2) elegís "Flashes
  de las fotos · sin costo" o "Video IA de la prenda": la foto real del
  producto, recortada a 9:16, va a un motor image-to-video de fal (los mismos
  de Videos: Seedance Lite 720p, Wan 2.6 Flash, LTX 2.3 Fast, Seedance Pro;
  el motor se elige en el paso 4) con un paneo lento que muestra tela y
  detalles. Sale un clip de 5 o 10 s (si el tramo dura más, se repite) y el
  costo aparece en la tarjeta, en el resumen y en la tabla del paso 4
  (Seedance Lite ≈ US$0,18 por clip de 5 s; Wan ≈ US$0,25). Si subiste tus
  videos, mandan ellos. Si el server se reinicia a mitad de un clip, se
  retoma igual que con OmniHuman.
- **Música de fondo.** Biblioteca de pistas de tu cuenta (paso 4: "Subir una
  pista", mp3/m4a/wav de hasta 20 MB, se convierten a mp3 y quedan en el KV
  para todos los reels; hasta 8). Elegís la pista y el volumen; va en loop y
  con fade al final, siempre debajo de la voz. Además:
  - **Cómo suena** (v2.5.0): *Encima del video* es la música del reel, limpia
    (22% de volumen). *Como si sonara en el local* la filtra como un parlante
    chico del negocio (corta graves bajo 180 Hz y agudos sobre 3,8 kHz, y le
    suma una reverb cortita de ambiente) y arranca en 14%: se oye como la
    música que hay puesta en el lugar donde ella está grabando, no como una
    pista pegada encima.
  - **Arranca en el segundo** (v2.5.0): elegís desde qué parte del tema entra,
    así podés empezar en el estribillo en vez de en la intro. El botón "▶
    escuchar desde ahí" reproduce la pista desde ese punto para buscarlo.
    La pista se corta y se repite en archivos aparte antes de mezclar, porque
    combinar el salto con la repetición en un solo paso de ffmpeg deja el
    salto sólo en la primera vuelta.
  - **De dónde sacar una canción**: hay un desplegable en el paso 4 que lo
    explica. Resumen: para Instagram conviene generar el reel sin música y
    ponerla desde Instagram (licenciada, y los temas del momento ayudan al
    alcance); si la querés pegada al video, Pixabay Music y la Biblioteca de
    audio de YouTube dan mp3 gratis; y no conviene bajar un tema conocido de
    YouTube o Spotify, porque Instagram lo reconoce y puede silenciar el reel.
- **Precio, talles y llamado a la acción sobre el video.** El precio y los
  talles del producto (paso 1) aparecen en una caja arriba durante los tramos
  de producto ("$ 24.900", "Talles 85 al 100"; el precio se formatea solo:
  "ARS 24900" → "$ 24.900"), y el llamado a la acción (texto editable,
  "Escribinos por DM" por defecto) en una caja clara durante el último tramo.
  Se apagan por separado en el paso 4. Va todo en el mismo archivo de
  subtítulos (ASS), así que no depende de fuentes extra.
- **Plantillas** (arriba de "Cómo es el reel"): Lanzamiento, Oferta/promo,
  Detalle de producto y Un día con la prenda. Al elegir una se llenan tono,
  lugar, duración, look, mic, precio/talles y el llamado a la acción (después
  cambiás lo que quieras) y el guion sigue su enfoque (la de Detalle además
  pide clips IA en los tramos de producto).
- **Ropa interior puesta en la escena.** Si en "Cómo está vestida" ponés
  corpiño, conjunto, malla, etc., la escena lleva el mismo marco de catálogo
  de tienda que usa Fotos (el filtro de Gemini leía "selfie de celular en
  corpiño" como sugerente). Si igual bloquea, se reintenta una vez en modo
  catálogo con el look limpio, y si vuelve a bloquear el aviso dice qué
  probar (nombre de catálogo para la prenda, cambiar el modelo en Fotos →
  Ajustes, o subir tu propia escena).

**El lugar también se pregunta, y las escenas se siguen entre sí (v2.1.0).**
- *Cómo es el lugar* (paso 1, debajo de "Dónde está ella"): un campo de texto
  y el botón **❓ Preguntame sobre el lugar**. Gemini pregunta lo que la foto
  no puede adivinar del lugar: dónde va el producto (colgado en un perchero,
  apoyado en el mostrador, en una caja abierta), qué se ve detrás, cómo es el
  mueble, la luz, si hay cartel de la marca. Elegís una opción por pregunta o
  escribís la tuya y se suma al campo; eso va a todas las escenas del reel.
  No pregunta por la ropa ni por la pose: eso se decide en el paso 3.
- *Seguir la primera escena* (paso 3, tildado por defecto): la primera escena
  que generes queda de referencia, y las siguientes la reciben como imagen
  además de los retratos del personaje, con la orden de mantener idénticos el
  lugar, el fondo, la luz, la ropa y el peinado, cambiando sólo el encuadre y
  la pose. Con esto el reel deja de saltar de un local a otro entre tramos.
  Destildalo si querés que cada escena sea libre. Y cuando hay escena de
  referencia, las preguntas del paso 3 dejan de preguntar por el lugar y la
  ropa: sólo por la pose, la expresión y las manos de ese tramo.

**Que se note menos que es IA (v2.2.0).** Tres cosas del primer reel bueno:
- *El registro.* El tono **chetita** (nuevo y por defecto) es una influencer de
  Palermo: el guion se escribe con sus muletillas ("o sea", "tipo", "nada",
  "literal", "obvio"), sus adjetivos ("divino", "amo", "me muero",
  "obsesionada") y algún anglicismo de moda, y la voz lo lee con las vocales
  alargadas y la entonación que sube al final. El tono ahora manda las dos
  cosas: cómo **escribe** el guion y cómo lo **habla**.
- *La voz.* Además del registro, la consigna pide habla real: ritmo
  desparejo, pausas de verdad en las comas, una respiración entre frases,
  alguna sílaba alargada. Y el audio pasa por un **aire de micrófono real**
  (paso 1, se puede apagar): corte de graves, presencia en los 3,4 kHz,
  compresión de lavalier y una reflexión cortita del ambiente. La voz de
  Gemini sale de estudio, y esa limpieza también suena a IA.
- *La cámara.* OmniHuman devuelve el cuadro clavado: el fondo queda congelado
  píxel a píxel y eso es lo que más delata. Con **Cámara en mano** (por
  defecto) el video de ella se agranda un 5% y se recorta con un
  desplazamiento que cambia con el tiempo (dos ondas de períodos distintos por
  eje, así no se repite ni parece un vaivén): unos 10 píxeles de deriva, como
  un teléfono sostenido con la mano. Y el prompt de OmniHuman pide
  micro-movimientos todo el tiempo: inclinaciones de cabeza en las palabras
  fuertes, cejas, parpadeo, el peso cambiando de pierna, la mano libre
  gesticulando.

**Voz de piba de 19 o 20 que habla de corrido (v2.3.0).** La usuaria mandó
un reel de otra marca como referencia y se midieron las dos voces:

| | la referencia | la nuestra (antes) |
|---|---|---|
| tono de voz (F0 mediana) | 242 Hz | 190 Hz |
| huecos de 150 ms o más | 2 en 20 s | 19 en 20 s |
| el hueco más largo | 0,30 s | 0,65 s |

O sea: la voz de Gemini es de mujer adulta y para una frase por segundo. Con
eso se agregaron dos cosas.
- *Ritmo por tono.* Cada tono trae ahora su propio ritmo para la voz. El de
  **chetita** dice: rápido y de corrido, encadenando una frase con la otra sin
  pausa, arrancando la que sigue antes de que se apague la anterior, casi sin
  respirar. (Antes la consigna pedía lo contrario: una respiración entre
  frases. Servía para "cercana" y arruinaba "chetita".)
- *Energía de la voz* (paso 1): **Tal cual sale**, **De corrido** (la que
  viene puesta), **Un toque más joven** (+5% de tono) y **Bastante más joven**
  (+10%). Todas menos la primera le recortan los silencios largos con
  `silenceremove` (el hueco más largo baja de 0,65 a 0,25 s, igual que la
  referencia) y la aceleran un poco. Las de "+ tono" usan `rubberband` con
  `formant=preserved`: sin eso el tono sube pero el timbre se encoge y queda
  el efecto de cinta acelerada (fue exactamente lo que pasó en la v2.3.0, que
  subía 16% sin preservar formantes). El tratamiento va ANTES de OmniHuman,
  así los labios sincronizan con lo que se oye. Y como habla más rápido, el
  guion escribe más palabras por segundo (2,3 / 2,5 / 2,55 / 2,7) y el reel
  sale un poco más barato, porque OmniHuman cobra por segundo.
- *Probar la voz* (botón ▶ al lado del selector de voz): graba una frase de
  muestra con la voz, el tono y la energía elegidos, para escuchar antes de
  grabar el guion entero. Cuesta lo que un mensaje de voz.
- *El micrófono, corregido.* La primera versión del "aire de micrófono" sonaba
  a radio AM, y estaba medido por qué: sacaba cuerpo en 260 Hz, metía 2,5 dB
  en 3,4 kHz y sumaba un eco de 24 ms que peina el espectro y deja timbre
  metálico. Quedó: cuerpo en 1,3 kHz, una pizca en 3 kHz, menos filo en
  7,5 kHz (ahí teníamos 4 dB de más contra la referencia) y compresión suave,
  sin eco.

**Si el paso 4 no aparece (v2.4.1).** Antes el paso "Generar el reel" se
escondía solo cuando faltaba algo, sin decir qué, y no había forma de
destrabarlo. Ahora al final del paso 3 aparece un cartel que dice exactamente
qué falta y de qué tramo: la voz del tramo N, la escena del tramo N, o acortar
un tramo de ella que pasa los 28 segundos. El paso 4 vuelve a aparecer solo en
cuanto se completa, aunque estés parada en otro paso, y si tocás el "4" del
encabezado sin estar listo te avisa qué falta en vez de no hacer nada.

Costo: la voz y las escenas centavos; OmniHuman US$0,16 por segundo de ella
hablando (unos US$3 para 18 s). El trabajo corre en segundo plano con reloj y
figura también en "En curso" de Personajes; cada tramo de ella tiene tope de
28 s de audio (OmniHuman en 1080p admite 30). Variables: `REELS_PREFIX`
(default `/reels`) y `REELS_OMNI_MODEL`.

## Actualizaciones (igual que ML×TN)
- Cambiás archivos → los subís al repo → Railway redeploya solo → hard refresh.

## Importante
- Esta versión **no tiene login**: cualquiera con el link entra. Ideal para
  probar con tus papás. Antes de promocionar a desconocidos hay que sumar
  login + datos por usuario + medición de consumo + cobro (Mercado Pago).
- Mantené las API keys SOLO en las Variables de Railway, nunca dentro de los
  archivos del repo.
