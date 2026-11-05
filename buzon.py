"""Buzón de GitHub para la versión web: procesa los pedidos (issues) que abre el dueño del repo.

La web no puede escribir en el repositorio, así que abre un issue ya escrito y este script
(lo corre .github/workflows/buzon.yml) hace el trabajo. Títulos que entiende:

    Buscar: Dhelmise 091/084 [en]       busca la carta en Perú y TCGplayer (en, ja o es)
    Seguir: https://www.masterset.pe/card/<id>    la agrega a "cartas" de tiendas.yaml
    Dejar: https://www.masterset.pe/card/<id>     la quita de "cartas" de tiendas.yaml

Lee TITULO y NUMERO (número del issue) del entorno. Escribe la respuesta para el issue en
RESPUESTA (respuesta.md) y guarda las búsquedas en BUSQUEDAS (busquedas.json, rama
"busquedas", que lee la web). Sale con código 0 aunque el pedido falle: el error va en la
respuesta.
"""
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import buscador
import cartas
from scanner import BASE, soles

RE_PEDIDO = re.compile(r"^\s*(buscar|seguir|dejar)\s*:\s*(.+?)\s*$", re.I)
RE_IDIOMA = re.compile(r"\s*[\[(]?\b(en|ja|es|ingles|inglés|japones|japonés|espanol|español)\b[\])]?\s*$", re.I)
IDIOMAS = {"en": "en", "ingles": "en", "inglés": "en", "ja": "ja", "japones": "ja", "japonés": "ja",
           "es": "es", "espanol": "es", "español": "es"}
TIENDAS = BASE / "tiendas.yaml"
MAX_BUSQUEDAS = 15


def separar_idioma(texto):
    m = RE_IDIOMA.search(texto)
    if not m:
        return texto.strip(), "en"
    return texto[:m.start()].strip(), IDIOMAS[m.group(1).lower()]


def respuesta_busqueda(r):
    lineas = [f"**{r['consulta']}** ({cartas.IDIOMA.get(r['idioma'], r['idioma'])}) · "
              f"tipo de cambio S/ {r['usd_pen']:.3f}", ""]
    for v in r["versiones"]:
        lineas.append(f"### {v.get('tcg_nombre') or v['nombre']}" + (f" · {v['tcg_set']}" if v.get("tcg_set") else ""))
        lineas.append(f"TCGplayer: {soles(v.get('mercado_pen'))}" +
                      (f" (US$ {v['mercado_usd']:.2f})" if v.get("mercado_usd") else "") + f" → **{v['veredicto_txt']}**")
        lineas.append("")
        if v["peru"]:
            lineas += ["| Tienda | Precio | |", "|---|---|---|"]
            for o in v["peru"]:
                marca = "mejor precio" if o["tienda"] == v.get("mejor_tienda") and o["stock"] else ("agotada" if not o["stock"] else "")
                lineas.append(f"| [{o['tienda']}]({o['url']}) | {soles(o['precio']) if o['stock'] else '—'} | {marca} |")
        else:
            lineas.append("No está en ninguna tienda de Perú.")
        lineas.append("")
    return "\n".join(lineas)


def editar_cartas(card_id, nombre=None):
    """Agrega (si hay nombre) o quita la carta de la lista "cartas:" de tiendas.yaml, sin tocar lo demás."""
    lineas = TIENDAS.read_text(encoding="utf-8").splitlines(keepends=True)
    try:
        ini = next(i for i, l in enumerate(lineas) if re.match(r"^cartas:\s*(\[\s*\])?\s*$", l))
    except StopIteration:
        raise RuntimeError("tiendas.yaml no tiene la lista cartas:")
    lineas[ini] = "cartas:\n"
    fin = ini + 1
    while fin < len(lineas) and (lineas[fin].startswith("  ") or not lineas[fin].strip()):
        fin += 1
    while fin > ini + 1 and not lineas[fin - 1].strip():
        fin -= 1   # las líneas en blanco antes de la siguiente sección se quedan donde están
    ya = [i for i in range(ini + 1, fin) if card_id in lineas[i].lower()]
    if nombre is None:
        if not ya:
            return False
        for i in reversed(ya):
            del lineas[i]
    else:
        if ya:
            return False
        lineas.insert(fin, f"  - {cartas.MASTERSET.format(card_id)}  # {nombre}\n")
    TIENDAS.write_text("".join(lineas), encoding="utf-8")
    return True


def procesar(titulo):
    """Devuelve (respuesta en markdown, búsqueda para busquedas.json o None)."""
    m = RE_PEDIDO.match(titulo or "")
    if not m:
        return "No entendí el pedido. Usa `Buscar: <nombre número>`, `Seguir: <link>` o `Dejar: <link>`.", None
    accion, texto = m.group(1).lower(), m.group(2)[:200]
    if accion == "buscar":
        consulta, idioma = separar_idioma(texto)
        try:
            r = buscador.buscar(consulta, idioma)
        except Exception as ex:
            return f"No se pudo buscar **{consulta}**: {ex}", {"consulta": consulta, "idioma": idioma, "error": str(ex)[:200]}
        return respuesta_busqueda(r), r
    card_id = cartas.id_de(texto)
    if not card_id:
        return "Ese no es un link de MasterSet (https://www.masterset.pe/card/…).", None
    if accion == "seguir":
        f = cartas.ficha_masterset(card_id)
        hecho = editar_cartas(card_id, f["nombre"])
        return (f"Listo: ahora se sigue **{f['nombre']}** ({f['set']}). Sale en la web cuando termine el escaneo (unos minutos)."
                if hecho else f"**{f['nombre']}** ya estaba en la lista."), None
    hecho = editar_cartas(card_id)
    return ("Listo: esa carta ya no se sigue. Desaparece de la web cuando termine el escaneo (unos minutos)."
            if hecho else "Esa carta no estaba en la lista."), None


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    titulo, numero = os.environ.get("TITULO", ""), os.environ.get("NUMERO")
    respuesta, busqueda = procesar(titulo)
    Path(os.environ.get("RESPUESTA", "respuesta.md")).write_text(respuesta + "\n", encoding="utf-8")
    if busqueda is not None:
        ruta = Path(os.environ.get("BUSQUEDAS", "busquedas.json"))
        try:
            previas = json.loads(ruta.read_text(encoding="utf-8")).get("busquedas", [])
        except (FileNotFoundError, json.JSONDecodeError):
            previas = []
        busqueda.update({"issue": int(numero) if (numero or "").isdigit() else None,
                         "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
        datos = {"actualizado": busqueda["ts"], "busquedas": ([busqueda] + previas)[:MAX_BUSQUEDAS]}
        ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    print(respuesta)


if __name__ == "__main__":
    main()
