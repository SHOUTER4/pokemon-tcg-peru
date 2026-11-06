"""Avisos al celular (Web Push) y en Windows.

La web instalada en el celular (PWA) se suscribe a los avisos y manda su suscripción
CIFRADA en un issue "Avisos: …" del repositorio (solo se atienden los del dueño). Esta PC
la descifra con su llave privada, la guarda en data/push.json y cierra el issue. Desde
ahí fb_subastas.py puede mandar avisos al celular aunque la web esté cerrada.

Las llaves privadas (data/vapid_privada.pem y data/buzon_privada.pem) nunca salen de la PC;
las públicas van en web/push.json, que se publica con la web.

Uso:
  python notificar.py --claves          crea las llaves (una sola vez) y escribe web/push.json
  python notificar.py --recoger         lee los issues "Avisos:" y guarda las suscripciones
  python notificar.py --probar          manda un aviso de prueba a los celulares suscritos
"""
import base64
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

from scanner import BASE, log

DATA = BASE / "data"
VAPID_PRIVADA = DATA / "vapid_privada.pem"
BUZON_PRIVADA = DATA / "buzon_privada.pem"
SUSCRIPCIONES = DATA / "push.json"
PUBLICAS = BASE / "web" / "push.json"
SITIO = "https://shouter4.github.io/pokemon-tcg-peru/"
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def crear_claves():
    """Llaves VAPID (firmar los avisos) y RSA (recibir la suscripción cifrada). No pisa las existentes."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    DATA.mkdir(exist_ok=True)
    pem = serialization.PrivateFormat.PKCS8
    if not VAPID_PRIVADA.exists():
        k = ec.generate_private_key(ec.SECP256R1())
        VAPID_PRIVADA.write_bytes(k.private_bytes(serialization.Encoding.PEM, pem, serialization.NoEncryption()))
    if not BUZON_PRIVADA.exists():
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        BUZON_PRIVADA.write_bytes(k.private_bytes(serialization.Encoding.PEM, pem, serialization.NoEncryption()))
    vapid = serialization.load_pem_private_key(VAPID_PRIVADA.read_bytes(), None)
    buzon = serialization.load_pem_private_key(BUZON_PRIVADA.read_bytes(), None)
    publicas = {
        "vapid": _b64url(vapid.public_key().public_bytes(serialization.Encoding.X962,
                                                          serialization.PublicFormat.UncompressedPoint)),
        "rsa": base64.b64encode(buzon.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode(),
    }
    PUBLICAS.parent.mkdir(exist_ok=True)
    PUBLICAS.write_text(json.dumps(publicas, indent=1) + "\n", encoding="utf-8")
    return publicas


def descifrar(texto):
    """Suscripción que cifró la web: RSA-OAEP(SHA-256) envuelve una llave AES-GCM."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    sobre = json.loads(base64.b64decode(texto.strip()))
    buzon = serialization.load_pem_private_key(BUZON_PRIVADA.read_bytes(), None)
    llave = buzon.decrypt(base64.b64decode(sobre["k"]), padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    plano = AESGCM(llave).decrypt(base64.b64decode(sobre["iv"]), base64.b64decode(sobre["c"]), None)
    return json.loads(plano)


def suscripciones():
    try:
        return json.loads(SUSCRIPCIONES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _guardar_suscripciones(subs):
    SUSCRIPCIONES.write_text(json.dumps(subs, indent=1), encoding="utf-8")


def _gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", timeout=60,
                       cwd=BASE, creationflags=SIN_VENTANA)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout


def recoger():
    """Guarda las suscripciones que llegaron en issues "Avisos:" del dueño del repositorio."""
    if not BUZON_PRIVADA.exists():
        return 0
    try:
        dueno = json.loads(_gh("repo", "view", "--json", "owner"))["owner"]["login"]
        issues = json.loads(_gh("issue", "list", "--state", "open", "--author", dueno, "--search",
                                "Avisos: in:title", "--json", "number,title,body", "--limit", "20"))
    except Exception as ex:  # noqa: BLE001 - sin internet o sin gh: se intenta en la próxima
        log.warning("No pude revisar los pedidos de avisos: %s", ex)
        return 0
    nuevas = 0
    for i in issues:
        if not i["title"].startswith("Avisos:"):
            continue
        cifrado = next((b for b in i["body"].split("```") if b.strip().startswith("eyJ")), "")
        try:
            sub = descifrar(cifrado.replace("cifrado", "").strip())
            if not sub.get("endpoint", "").startswith("https://"):
                raise ValueError("suscripción inválida")
        except Exception as ex:  # noqa: BLE001
            log.warning("Issue #%s: no pude leer la suscripción (%s)", i["number"], ex)
            _gh("issue", "close", str(i["number"]), "--comment", "No pude leer este pedido de avisos. Vuelve a intentarlo desde la web.")
            continue
        subs = [s for s in suscripciones() if s["endpoint"] != sub["endpoint"]]
        nombre = i["title"].split(":", 1)[1].strip()[:40] or "celular"
        subs.append({**sub, "nombre": nombre, "desde": dt.datetime.now().isoformat(timespec="seconds")})
        _guardar_suscripciones(subs)
        nuevas += 1
        enviar("Avisos activados", "Te avisaré aquí de tus subastas.", SITIO + "#subastas", windows=False)
        _gh("issue", "close", str(i["number"]), "--comment", f"Avisos activados en «{nombre}».")
    return nuevas


def pythonw():
    """pythonw.exe junto al Python actual: corre scripts sin abrir consola."""
    w = Path(sys.executable).with_name("pythonw.exe")
    return str(w if w.exists() else sys.executable)


def _windows(titulo, cuerpo, url):
    """Ventana con sonido en la PC (no bloquea: la abre otro proceso, sin consola)."""
    if os.name != "nt":
        return
    args = [pythonw(), str(BASE / "recordatorio.py"), "--titulo", titulo, "--mensaje", cuerpo] + (
        ["--url", url] if url else [])
    subprocess.Popen(args, creationflags=SIN_VENTANA)


def enviar(titulo, cuerpo, url=None, windows=True, etiqueta=None, respaldo_pc=True):
    """Aviso a cada celular suscrito; con windows=True también en la PC, y si ningún celular lo
    recibió, en la PC salvo respaldo_pc=False. Devuelve cuántos celulares lo recibieron."""
    from pywebpush import WebPushException, webpush

    log.info("Aviso: %s — %s", titulo, cuerpo)
    ok, vivas = 0, []
    for s in suscripciones():
        try:
            webpush({"endpoint": s["endpoint"], "keys": s["keys"]},
                    json.dumps({"titulo": titulo, "cuerpo": cuerpo, "url": url or SITIO + "#subastas",
                                "etiqueta": etiqueta}, ensure_ascii=False),
                    vapid_private_key=str(VAPID_PRIVADA), vapid_claims={"sub": SITIO}, ttl=3600, timeout=20)
            ok += 1
            vivas.append(s)
        except WebPushException as ex:
            codigo = getattr(ex.response, "status_code", None)
            if codigo in (404, 410):   # el celular la dio de baja
                log.info("Suscripción vencida (%s): la quito", s.get("nombre"))
                continue
            log.warning("Aviso push falló (%s): %s", s.get("nombre"), ex)
            vivas.append(s)
        except Exception as ex:  # noqa: BLE001
            log.warning("Aviso push falló (%s): %s", s.get("nombre"), ex)
            vivas.append(s)
    if len(vivas) != len(suscripciones()):
        _guardar_suscripciones(vivas)
    if windows or (not ok and respaldo_pc):
        _windows(titulo, cuerpo, url)
    return ok


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--claves" in sys.argv:
        print(json.dumps(crear_claves(), indent=1))
    elif "--recoger" in sys.argv:
        print(f"{recoger()} suscripción(es) nueva(s); total {len(suscripciones())}")
    elif "--probar" in sys.argv:
        print(f"Enviado a {enviar('Prueba', 'Si ves esto, los avisos funcionan.', windows=False)} celular(es)")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
