# Escáner diario de Pokémon TCG en tiendas de Perú

Revisa cada día el catálogo Pokémon TCG de tiendas peruanas, guarda el historial
de precios y stock, y genera un reporte con lo que cambió.

## Tiendas

| Tienda | Plataforma | Cómo se lee |
|---|---|---|
| OkamiDex | WooCommerce | API pública `wp-json/wc/store/v1` |
| Ludus Magnus | WooCommerce | API pública `wp-json/wc/store/v1` |
| La Mazmorra TCG | WooCommerce | API pública `wp-json/wc/store/v1` |
| Replay Perú | Shopify | `collections/<handle>/products.json`; si Shopify limita (429), el HTML de la colección |
| Phantom | Magento | HTML del listado de categoría |
| Raymi Store, Dranzer Store | — | Desactivadas: bloquean accesos automáticos (captcha / Cloudflare) |

Agregar o quitar tiendas: editar `tiendas.yaml`. Cualquier otra tienda WooCommerce o
Shopify funciona solo agregando su URL y categorías/colecciones.

## App interactiva

Doble clic en **Pokemon TCG Peru.bat** del Escritorio (o `Abrir app.bat` en esta carpeta).
Abre `http://127.0.0.1:8777` en el navegador; la ventana negra debe quedar abierta
mientras la usas. Solo es accesible desde tu PC.

- **Resumen**: indicadores, cambios del último escaneo, mejores oportunidades de ahorro
  y lo más barato de tu lista de vigilancia.
- **Dónde comprar**: el mismo producto en distintas tiendas (agrupado por set, tipo y
  Pokémon según `equivalencias.yaml`), la tienda recomendada, cuánto ahorras y qué tienda
  sale más barata en general. Filtra por tipo e idioma.
- **Cartas**: pega el link de una carta de MasterSet (`https://www.masterset.pe/card/…`) y la app
  compara su oferta más barata con el precio de mercado de TCGplayer (en soles, al tipo de cambio
  del día). Veredicto: *Buen precio* (hasta 85% del mercado), *Precio justo* (hasta +10%),
  *Caro* (hasta +50%) o *Muy caro*. Avisa si MasterSet enlaza otra versión de la carta en
  TCGplayer, guarda el historial y acepta un precio objetivo. "Actualizar precios" revisa solo
  las cartas; el escaneo completo también las incluye.
- **Subastas**: anota el link de una subasta (Facebook, grupos…), la hora de cierre, la puja
  actual y tu máximo, y elige la carta en TCGplayer con el buscador. La app muestra la cuenta
  regresiva, hasta cuánto conviene pujar (80% del valor de mercado) y si ya pasaron tu máximo.
  Antes del cierre (1 h, 30, 10 o 3 min, a elegir) Windows muestra una ventana con sonido para
  abrir la subasta, aunque la app esté cerrada: son tareas "TCG Subasta …" del Programador de
  tareas que ejecutan `recordatorio.ps1` (la PC debe estar prendida y con tu sesión abierta).
  Facebook no deja leer publicaciones sin iniciar sesión, así que la puja actual la anotas tú.
- **Catálogo**: todo con buscador y filtros. Al tocar un producto se ve su historial de
  precio, el precio en otras tiendas y se puede guardar en favoritos.
- **Favoritos**: precio objetivo por producto; la app (y Telegram) avisan cuando un
  escaneo lo encuentra a ese precio o menos.
- **Ajustes**: editar la lista de vigilancia, pausar tiendas y escanear una sola tienda.
- Botón **Escanear ahora** con el progreso en vivo.

Preferencias guardadas en `data/preferencias.json`.

## Escaneo por consola

Doble clic en **Escanear Pokemon TCG.bat** del Escritorio (o `Escanear.bat`), o:

```bash
pip install -r requirements.txt
python scanner.py
```

Opciones: `--tienda OkamiDex` (solo una), `--tienda cartas` (solo las cartas sueltas),
`--sin-alertas` (no envía Telegram).

Analizar cartas sin guardarlas: `python cartas.py <link de MasterSet> [<link> …]`.

Salida:

- `reportes/ultimo.html`: la misma interfaz, guardada sin servidor (sin botón de escaneo
  ni historial; los favoritos quedan solo en ese navegador). La interfaz está en `ui.html`.
- `reportes/reporte_AAAA-MM-DD.html` y `reportes/productos_AAAA-MM-DD.csv`: copia diaria.
- `data/tcg.db`: SQLite con `productos`, `historial` (precio/stock por día), `escaneos`, `cambios`,
  y para las cartas sueltas `cartas`, `cartas_historial` y `tipo_cambio`.
- `data/escaneo.log`: log de cada ejecución.

Si "Dónde comprar" junta mal un producto o no reconoce un set nuevo, agrega o reordena
sus alias en `equivalencias.yaml`.

El primer escaneo de cada tienda solo crea la base; los cambios aparecen desde el segundo.

## Versión web (GitHub Pages, gratis)

`.github/workflows/escaneo.yml` escanea cada día a las 8:00 a. m. (hora de Perú) en GitHub y
publica la página en GitHub Pages. Para escanear al momento: pestaña **Actions** del repositorio →
*Escaneo diario* → **Run workflow**.

- La página es pública y **no lleva datos personales**: sin subastas, ajustes ni precios objetivo
  (`scanner.py --web` usa `datos_publicos`). Los favoritos que marques ahí quedan solo en tu navegador.
- Sigue las cartas listadas en `cartas:` de `tiendas.yaml`.
- El historial de precios vive en la rama `datos` (un solo commit con `tcg.db`).
- `data/` y `reportes/` no se suben (ver `.gitignore`).
- GitHub pausa los workflows programados tras 60 días sin actividad en el repositorio; si pasa,
  reactívalo desde la pestaña Actions.

## Programar cada día (opcional, Windows)

```powershell
powershell -ExecutionPolicy Bypass -File programar_tarea.ps1 -Hora 09:00
```

Quitar la tarea: `programar_tarea.ps1 -Quitar`.

## Alertas por Telegram (opcional)

1. Crear un bot con @BotFather y copiar el token.
2. Escribirle al bot y obtener tu chat id (por ejemplo con @userinfobot).
3. Definir las variables de entorno de usuario y volver a programar la tarea:

```powershell
setx TELEGRAM_BOT_TOKEN "123456:ABC..."
setx TELEGRAM_CHAT_ID "123456789"
```

Solo se envía mensaje cuando hay cambios, un favorito llega a su precio objetivo o alguna
tienda falla. Los productos de tu lista de vigilancia salen primero, marcados con ⭐.
