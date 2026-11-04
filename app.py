"""App local del escáner de Pokémon TCG Perú.

Abre en el navegador una interfaz interactiva (http://127.0.0.1:8777) para:
escanear con progreso en vivo, comparar precios entre tiendas, ver historial,
guardar favoritos con precio objetivo, editar la lista de vigilancia, seguir
cartas sueltas de MasterSet comparadas con TCGplayer y anotar subastas con avisos
antes del cierre.

Uso:
    python app.py              # abre el navegador
    python app.py --sin-abrir  # solo levanta el servidor
"""
import argparse
import datetime as dt
import json
import logging
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cartas
import scanner
import subastas

HOST, PUERTO = "127.0.0.1", 8777
log = logging.getLogger("tcg")

estado = {"corriendo": False, "tienda": None, "inicio": None, "fin": None,
          "lineas": [], "resultado": None, "error": None}
candado = threading.Lock()


class Captura(logging.Handler):
    """Guarda las últimas líneas del log para mostrarlas en la app mientras escanea."""
    def emit(self, record):
        with candado:
            estado["lineas"].append(self.format(record))
            del estado["lineas"][:-150]


def escanear(tienda):
    try:
        estado["resultado"] = scanner.ejecutar_escaneo(tienda)
    except Exception as ex:  # se muestra en la app en vez de matar el hilo en silencio
        log.exception("El escaneo falló")
        estado["error"] = str(ex)
    finally:
        with candado:
            estado["corriendo"] = False
            estado["fin"] = dt.datetime.now().isoformat(timespec="seconds")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # sin ruido en la consola por cada petición
        pass

    def _enviar(self, cuerpo, tipo="application/json; charset=utf-8", codigo=200):
        datos = cuerpo.encode("utf-8") if isinstance(cuerpo, str) else cuerpo
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(datos)

    def _json(self, obj, codigo=200):
        self._enviar(json.dumps(obj, ensure_ascii=False), codigo=codigo)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in ("/", "/index.html"):
            return self._enviar(scanner.pagina_ui(None, "app"), "text/html; charset=utf-8")
        if url.path == "/api/datos":
            db = scanner.abrir_db()
            try:
                return self._json(scanner.armar_datos(db))
            finally:
                db.close()
        if url.path == "/api/historial":
            db = scanner.abrir_db()
            try:
                return self._json(scanner.historial(db, q.get("tienda", ""), q.get("pid", "")))
            finally:
                db.close()
        if url.path == "/api/tcgplayer":
            texto = q.get("q", "").strip()[:100]
            if len(texto) < 2:
                return self._json([])
            try:
                return self._json(subastas.buscar_tcgplayer(texto))
            except Exception as ex:
                return self._json({"error": f"TCGplayer no respondió: {ex}"}, 502)
        if url.path == "/api/estado":
            with candado:
                return self._json(estado)
        self._json({"error": "no existe"}, 404)

    def do_POST(self):
        # Exige una cabecera propia: un sitio externo no puede enviarla sin
        # permiso CORS, así que solo esta app puede lanzar escaneos o editar.
        if self.headers.get("X-TCG") != "1":
            return self._json({"error": "prohibido"}, 403)
        largo = int(self.headers.get("Content-Length", 0) or 0)
        try:
            cuerpo = json.loads(self.rfile.read(largo) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "JSON inválido"}, 400)
        url = urlparse(self.path)

        if url.path == "/api/escanear":
            with candado:
                if estado["corriendo"]:
                    return self._json({"error": "Ya hay un escaneo en curso"}, 409)
                estado.update(corriendo=True, tienda=cuerpo.get("tienda"), lineas=[], resultado=None,
                              error=None, fin=None, inicio=dt.datetime.now().isoformat(timespec="seconds"))
            threading.Thread(target=escanear, args=(cuerpo.get("tienda"),), daemon=True).start()
            return self._json({"ok": True})

        if url.path == "/api/preferencias":
            prefs = scanner.cargar_prefs()
            vigilar = cuerpo.get("vigilar", prefs["vigilar"])
            favoritos = cuerpo.get("favoritos", prefs["favoritos"])
            pausadas = cuerpo.get("pausadas", prefs["pausadas"])
            if not (isinstance(vigilar, list) and isinstance(favoritos, dict) and isinstance(pausadas, list)):
                return self._json({"error": "formato inválido"}, 400)
            limpios = {}
            for clave, fav in list(favoritos.items())[:500]:
                fav = fav if isinstance(fav, dict) else {}
                objetivo = fav.get("objetivo")
                limpios[str(clave)] = {
                    "nombre": str(fav.get("nombre", ""))[:200],
                    "objetivo": float(objetivo) if isinstance(objetivo, (int, float)) and objetivo > 0 else None,
                }
            prefs.update(vigilar=[str(v).strip() for v in vigilar if str(v).strip()][:100],
                         favoritos=limpios, pausadas=[str(p) for p in pausadas])
            scanner.guardar_prefs(prefs)
            return self._json(prefs)

        if url.path == "/api/cartas":
            prefs, accion, card_id = scanner.cargar_prefs(), cuerpo.get("accion"), str(cuerpo.get("id", ""))
            seguidas = prefs.setdefault("cartas", {})
            db = scanner.abrir_db()
            try:
                if accion == "agregar":
                    if len(seguidas) >= 200:
                        return self._json({"error": "Máximo 200 cartas"}, 400)
                    try:
                        ficha = cartas.agregar(db, prefs, str(cuerpo.get("url", ""))[:300])
                    except ValueError as ex:
                        return self._json({"error": str(ex)}, 400)
                    except Exception as ex:  # MasterSet o TCGplayer no respondieron
                        log.warning("No se pudo agregar la carta: %s", ex)
                        return self._json({"error": f"No se pudo leer la carta: {ex}"}, 502)
                    card_id = ficha["id"]
                elif accion == "quitar" and card_id in seguidas:
                    del seguidas[card_id]
                    scanner.guardar_prefs(prefs)
                elif accion == "objetivo" and card_id in seguidas:
                    objetivo = cuerpo.get("objetivo")
                    seguidas[card_id]["objetivo"] = (float(objetivo) if isinstance(objetivo, (int, float))
                                                     and objetivo > 0 else None)
                    scanner.guardar_prefs(prefs)
                else:
                    return self._json({"error": "acción inválida"}, 400)
                return self._json({"id": card_id, "prefs": prefs, "cartas": cartas.datos_cartas(db, prefs)})
            finally:
                db.close()

        if url.path == "/api/subastas":
            prefs, accion, sid = scanner.cargar_prefs(), cuerpo.get("accion"), str(cuerpo.get("id", ""))
            db = scanner.abrir_db()
            try:
                if accion == "guardar":
                    sid = subastas.guardar(db, prefs, cuerpo)["id"]
                elif accion == "puja":
                    subastas.marcar(prefs, sid, puja_actual=cuerpo.get("puja_actual"))
                elif accion == "resultado":
                    subastas.marcar(prefs, sid, resultado=cuerpo.get("resultado"))
                elif accion == "quitar":
                    subastas.quitar(prefs, sid)
                else:
                    return self._json({"error": "acción inválida"}, 400)
            except ValueError as ex:
                return self._json({"error": str(ex)}, 400)
            except Exception as ex:  # TCGplayer/MasterSet no respondieron
                log.warning("Subasta: %s", ex)
                return self._json({"error": f"No se pudo guardar: {ex}"}, 502)
            finally:
                db.close()
            return self._json({"id": sid, "prefs": prefs, "subastas": subastas.datos_subastas(prefs)})

        self._json({"error": "no existe"}, 404)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sin-abrir", action="store_true", help="no abrir el navegador")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    captura = Captura()
    captura.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    scanner.configurar_log(logging.StreamHandler(sys.stdout), captura)

    direccion = f"http://{HOST}:{PUERTO}/"
    try:
        servidor = ThreadingHTTPServer((HOST, PUERTO), Handler)
    except OSError:
        # Ya hay una app abierta: solo se abre el navegador.
        print(f"La app ya está abierta en {direccion}")
        if not args.sin_abrir:
            webbrowser.open(direccion)
        return
    print(f"App lista en {direccion}  (cierra esta ventana para apagarla)")
    if not args.sin_abrir:
        threading.Timer(0.6, webbrowser.open, args=(direccion,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
