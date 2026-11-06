"""Subastas de cartas (Facebook, grupos, etc.): seguimiento y avisos antes del cierre.

Facebook no deja leer publicaciones sin iniciar sesión, así que el link, la hora de
cierre y la puja actual los anotas tú en la app. La app guarda el valor de la carta
en TCGplayer para decirte hasta cuánto conviene pujar, y programa avisos en Windows
(tareas "TCG Subasta …" que muestran recordatorio.ps1 con sonido) aunque la app esté cerrada.

Cada cambio se publica (sin tu máximo) en subastas.json de la rama "subastas" del
repositorio, que la versión web lee al abrirse y cada 2 minutos.
"""
import base64
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import threading
import unicodedata
import uuid

import cartas
from scanner import BASE, guardar_prefs, log, session

BUSCAR_API = "https://mp-search-api.tcgplayer.com/v1/search/request"
RE_TCG = re.compile(r"tcgplayer\.com/(?:[a-z-]+/)?product/(\d+)", re.I)
AVISOS = (60, 30, 10, 3)        # minutos antes del cierre que se pueden elegir
TOPE = 0.8                      # pujar hasta 80% del mercado sigue siendo buena compra
PREFIJO_TAREA = "TCG Subasta"
RAMA_WEB = "subastas"           # rama de GitHub con subastas.json, que lee la versión web
CAMPOS_PUBLICOS = ("id", "titulo", "url", "cierre", "puja_actual", "pujas", "resultado", "tcg_nombre",
                   "tcg_set", "tcg_url", "mercado_usd", "mercado_pen", "imagen",
                   "ms_precio", "ms_url", "ms_ofertas", "ms_detalle", "lote")  # nunca mi_maximo


def buscar_tcgplayer(q, cuantos=12):
    """Cartas de Pokémon (inglés y japonés) en TCGplayer que coinciden con el texto."""
    cuerpo = {
        "algorithm": "sales_synonym_v2", "from": 0, "size": cuantos,
        "filters": {"term": {"productLineName": ["pokemon", "pokemon-japan"]}, "range": {}, "match": {}},
        "listingSearch": {"context": {"cart": {}}, "filters": {
            "term": {"sellerStatus": "Live", "channelId": 0}, "range": {"quantity": {"gte": 1}},
            "exclude": {"channelExclusion": 0}}},
        "context": {"cart": {}, "shippingCountry": "US"}, "settings": {"useFuzzySearch": True}, "sort": {},
    }
    r = session.post(BUSCAR_API, params={"q": q, "isList": "false"}, json=cuerpo, timeout=30)
    r.raise_for_status()
    out = []
    for x in r.json()["results"][0]["results"]:
        pid = str(int(x["productId"]))
        out.append({"id": pid, "nombre": x.get("productName"), "set": x.get("setName"),
                    "linea": x.get("productLineName"), "rareza": x.get("rarityName"),
                    "mercado_usd": x.get("marketPrice"),
                    "imagen": f"https://tcgplayer-cdn.tcgplayer.com/product/{pid}_in_200x200.jpg"})
    return out


def tcg_id_de(texto):
    """Id de TCGplayer desde un id, un link de TCGplayer o un link de MasterSet."""
    texto = str(texto or "").strip()
    if texto.isdigit():
        return texto
    m = RE_TCG.search(texto)
    if m:
        return m.group(1)
    card_id = cartas.id_de(texto)
    return cartas.ficha_masterset(card_id)["tcgplayer_id"] if card_id else None


def referencia(db, tcg_id):
    """Valor de la carta en TCGplayer, en dólares y soles, y su oferta más barata en MasterSet."""
    t = cartas.precio_tcgplayer(tcg_id)
    tc = cartas.tipo_cambio(db)
    return {**t, "tcgplayer_id": str(tcg_id), "usd_pen": tc,
            "mercado_pen": round(t["mercado_usd"] * tc, 2) if t["mercado_usd"] else None,
            "imagen": f"https://tcgplayer-cdn.tcgplayer.com/product/{tcg_id}_in_400x400.jpg",
            "ref_ts": dt.datetime.now().isoformat(timespec="seconds"),
            **precio_masterset(tcg_id, t.get("tcg_nombre"), t.get("tcg_linea"))}


CAMPOS_LOTE = ("tcgplayer_id", "tcg_nombre", "tcg_set", "tcg_url", "tcg_linea", "mercado_usd", "mercado_pen",
               "imagen", "ms_precio", "ms_url", "ms_ofertas", "ms_detalle", "ms_ts")


def _suma(valores):
    """Suma si todas las cartas del lote tienen el dato; si falta alguna, None."""
    return round(sum(valores), 2) if valores and all(v is not None for v in valores) else None


def resumen_lote(lote, tc=None):
    """Campos de la subasta para un lote de varias cartas: mercado y MasterSet sumados,
    nombre "A + B". Cada carta sigue con sus propios precios en "lote"."""
    sets = list(dict.fromkeys(c.get("tcg_set") for c in lote if c.get("tcg_set")))
    falta_ms = [c.get("tcg_nombre") or "?" for c in lote if c.get("ms_precio") is None]
    return {"lote": lote, "tcg_nombre": " + ".join(c.get("tcg_nombre") or "?" for c in lote),
            "tcg_set": " / ".join(sets), "tcg_url": lote[0].get("tcg_url"), "imagen": lote[0].get("imagen"),
            "mercado_usd": _suma([c.get("mercado_usd") for c in lote]),
            "mercado_pen": _suma([c.get("mercado_pen") for c in lote]),
            "ms_precio": _suma([c.get("ms_precio") for c in lote]), "ms_url": None,
            "ms_ofertas": None, "ms_detalle": f"Falta en MasterSet: {', '.join(falta_ms)}" if falta_ms else None,
            **({"usd_pen": tc} if tc else {}), "ref_ts": dt.datetime.now().isoformat(timespec="seconds")}


def referencia_lote(db, tcg_ids):
    """Referencia de un lote (2 o más cartas que se subastan juntas)."""
    lote = []
    for tcg_id in tcg_ids:
        r = referencia(db, tcg_id)
        lote.append({k: r.get(k) for k in CAMPOS_LOTE})
    return resumen_lote(lote, cartas.tipo_cambio(db))


def precio_masterset(tcg_id, tcg_nombre, tcg_linea):
    """Oferta más barata en MasterSet de la misma carta (mismo id de TCGplayer y mismo idioma:
    japonés si es de Pokemon Japan, si no inglés). Vacío si no hay o si MasterSet no responde."""
    import buscador  # aquí y no arriba: buscador importa este módulo
    base = re.sub(r"\s*-\s*[\w/]+$", "", tcg_nombre or "").strip()
    num = re.search(r"-\s*([a-z]*\d+)(?:/[\w]+)?$", tcg_nombre or "", re.I)
    if not base:
        return {}
    numero = cartas._numero(num.group(1))[0] if num else None
    idioma = "ja" if tcg_linea == "Pokemon Japan" else "en"
    try:
        ofertas = buscador.masterset(base, numero, None, idioma, {str(tcg_id)})
    except Exception as ex:
        log.warning("MasterSet no respondió para %s: %s", tcg_nombre, ex)
        return {}
    if not ofertas:
        return {"ms_precio": None, "ms_ts": dt.datetime.now().isoformat(timespec="seconds")}
    o = min(ofertas, key=lambda x: x["precio"])
    return {"ms_precio": o["precio"], "ms_url": o["url"], "ms_ofertas": o["ofertas"], "ms_detalle": o["detalle"],
            "ms_ts": dt.datetime.now().isoformat(timespec="seconds")}


def refrescar_masterset(prefs, horas=6):
    """Vuelve a mirar MasterSet en las subastas abiertas con referencia de hace más de `horas`."""
    limite = (dt.datetime.now() - dt.timedelta(hours=horas)).isoformat()
    cambio = False
    for s in prefs.get("subastas", {}).values():
        if s.get("lote") and not s.get("resultado") and s["cierre"] > dt.datetime.now().isoformat():
            viejas = [c for c in s["lote"] if (c.get("ms_ts") or "") < limite]
            for c in viejas:
                for k in [k for k in c if k.startswith("ms_")]:
                    del c[k]
                c.update(precio_masterset(c["tcgplayer_id"], c.get("tcg_nombre"), c.get("tcg_linea")))
            if viejas:
                s.update(resumen_lote(s["lote"]))
                cambio = True
            continue
        if (s.get("tcgplayer_id") and not s.get("resultado") and s["cierre"] > dt.datetime.now().isoformat()
                and (s.get("ms_ts") or "") < limite):
            for k in [k for k in s if k.startswith("ms_")]:
                del s[k]
            s.update(precio_masterset(s["tcgplayer_id"], s.get("tcg_nombre"), s.get("tcg_linea")))
            cambio = True
    if cambio:
        guardar_prefs(prefs)
    return cambio


def _anotar_puja(s, monto, cuando=None):
    """Agrega la puja al historial de la subasta si cambió (máximo 60 anotaciones)."""
    pujas = s.get("pujas") or []
    if monto and monto != s.get("puja_actual") and (not pujas or pujas[-1]["monto"] != monto):
        s.setdefault("pujas", []).append({"ts": cuando or dt.datetime.now().isoformat(timespec="seconds"),
                                          "monto": monto})
        del s["pujas"][:-60]


def _num(x):
    try:
        v = float(x)
        return round(v, 2) if v > 0 else None
    except (TypeError, ValueError):
        return None


def guardar(db, prefs, datos):
    """Crea o edita una subasta y reprograma sus avisos. Devuelve la subasta."""
    url = str(datos.get("url", "")).strip()[:500]
    if not re.match(r"https?://", url):
        raise ValueError("Pega el link de la subasta (https://…)")
    try:
        cierre = dt.datetime.fromisoformat(str(datos.get("cierre", ""))).replace(tzinfo=None, microsecond=0)
    except ValueError:
        raise ValueError("Indica la fecha y hora de cierre") from None
    subastas = prefs.setdefault("subastas", {})
    sid = str(datos.get("id") or "")
    previa = subastas.get(sid, {})
    if not previa:
        if len(subastas) >= 100:
            raise ValueError("Máximo 100 subastas: quita alguna cerrada")
        sid = uuid.uuid4().hex[:8]
    _anotar_puja(previa, _num(datos.get("puja_actual")))
    s = {**previa, "id": sid, "url": url, "cierre": cierre.isoformat(timespec="seconds"),
         "titulo": str(datos.get("titulo", "")).strip()[:120] or previa.get("titulo") or "Subasta",
         "puja_actual": _num(datos.get("puja_actual")), "mi_maximo": _num(datos.get("mi_maximo")),
         "avisos": sorted({int(m) for m in datos.get("avisos", AVISOS[1:]) if int(m) in AVISOS}, reverse=True),
         "resultado": previa.get("resultado")}
    s.setdefault("creada", dt.datetime.now().isoformat(timespec="seconds"))

    tcg = datos.get("tcg")
    if tcg is not None:  # "" quita la referencia; un id o link la (re)calcula; "id1 + id2" es un lote
        partes = [p for p in re.split(r"\s*[+,]\s*|\s+", str(tcg).strip()) if p]
        ids = [tcg_id_de(p) for p in partes]
        if partes and not all(ids):
            raise ValueError("No reconozco esa carta: elige un resultado de TCGplayer o pega su link")
        for k in [k for k in s if k.startswith(("tcg_", "ms_")) or k in ("tcgplayer_id", "mercado_usd", "minimo_usd",
                                                                 "mercado_pen", "usd_pen", "imagen", "ref_ts", "lote")]:
            del s[k]
        if len(ids) > 1:
            s.update(referencia_lote(db, ids))
        elif ids:
            s.update(referencia(db, ids[0]))
    subastas[sid] = s
    guardar_prefs(prefs)
    s["avisos_programados"] = programar_avisos(s)
    publicar(prefs)
    return s


def marcar(prefs, sid, **cambios):
    """Cambios rápidos: puja actual o resultado ("ganada"/"perdida"/None)."""
    s = prefs.get("subastas", {}).get(sid)
    if not s:
        raise ValueError("Esa subasta ya no existe")
    if "puja_actual" in cambios:
        _anotar_puja(s, _num(cambios["puja_actual"]))
        s["puja_actual"] = _num(cambios["puja_actual"])
    if "resultado" in cambios:
        s["resultado"] = cambios["resultado"] if cambios["resultado"] in ("ganada", "perdida") else None
    guardar_prefs(prefs)
    programar_avisos(s)  # el texto del aviso lleva la puja actual
    publicar(prefs)
    return s


def quitar(prefs, sid):
    prefs.get("subastas", {}).pop(sid, None)
    guardar_prefs(prefs)
    publicar(prefs)
    _powershell(f"Get-ScheduledTask -TaskName '{PREFIJO_TAREA} {sid} *' -ErrorAction SilentlyContinue | "
                "Unregister-ScheduledTask -Confirm:$false")


# --------------------------------------------------------------------------
# Avisos en Windows (Programador de tareas)
# --------------------------------------------------------------------------

def _ascii(s):
    """Texto sin tildes ni comillas para pasarlo como argumento de la tarea."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[\"'`$]", "", s)


def _powershell(comando):
    if os.name != "nt":
        return None
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
                       capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        log.warning("PowerShell falló: %s", r.stderr.strip()[:300])
    return r


def programar_avisos(s):
    """Borra los avisos de la subasta y crea uno por cada minuto elegido que siga en el futuro.
    Devuelve las horas programadas (ISO)."""
    cierre = dt.datetime.fromisoformat(s["cierre"])
    ahora = dt.datetime.now()
    horas = [] if s.get("resultado") else [
        (m, cierre - dt.timedelta(minutes=m)) for m in s.get("avisos", []) if cierre - dt.timedelta(minutes=m) > ahora]
    script = BASE / "recordatorio.ps1"
    lineas = [f"Get-ScheduledTask -TaskName '{PREFIJO_TAREA} {s['id']} *' -ErrorAction SilentlyContinue | "
              "Unregister-ScheduledTask -Confirm:$false"]
    for m, cuando in horas:
        mensaje = f"{_ascii(s['titulo'])}: cierra a las {cierre:%H:%M}, faltan {m} minutos."
        if s.get("puja_actual"):
            mensaje += f" Ultima puja anotada: S/ {s['puja_actual']:g}."
        if s.get("mi_maximo"):
            mensaje += f" Tu maximo: S/ {s['mi_maximo']:g}."
        args = (f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script}" '
                f'-Titulo "Subasta Pokemon - faltan {m} min" -Mensaje "{mensaje}" -Url "{_ascii(s["url"])}"')
        lineas.append(
            f"$t = New-ScheduledTaskTrigger -Once -At ([datetime]'{cuando:%Y-%m-%dT%H:%M:%S}'); "
            f"$t.EndBoundary = '{cierre + dt.timedelta(minutes=5):%Y-%m-%dT%H:%M:%S}'; "
            f"Register-ScheduledTask -TaskName '{PREFIJO_TAREA} {s['id']} {m:02d}m' -Force "
            f"-Description 'Aviso de subasta: {_ascii(s['titulo'])}' "
            f"-Action (New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '{args}') -Trigger $t "
            "-Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
            "-WakeToRun -DeleteExpiredTaskAfter (New-TimeSpan -Hours 1)) | Out-Null")
    r = _powershell("; ".join(lineas))
    if r is None or r.returncode != 0:
        return []
    return [c.isoformat(timespec="seconds") for _, c in horas]


def datos_subastas(prefs):
    """Subastas para la app, cada una con los avisos que siguen pendientes."""
    ahora = dt.datetime.now()
    out = []
    for s in prefs.get("subastas", {}).values():
        cierre = dt.datetime.fromisoformat(s["cierre"])
        pendientes = [] if s.get("resultado") else [
            (cierre - dt.timedelta(minutes=m)).isoformat(timespec="seconds")
            for m in s.get("avisos", []) if cierre - dt.timedelta(minutes=m) > ahora]
        tope = round(s["mercado_pen"] * TOPE) if s.get("mercado_pen") else None
        out.append({**s, "avisos_pendientes": pendientes, "tope": tope})
    return sorted(out, key=lambda s: (bool(s.get("resultado")) or s["cierre"] < ahora.isoformat(), s["cierre"]))


# --------------------------------------------------------------------------
# Versión web: subastas.json en la rama "subastas" del repositorio
# --------------------------------------------------------------------------

def publicas(prefs):
    """Subastas sin datos personales (sin tu máximo ni tus avisos). Las cerradas hace más
    de 3 días ya no se publican."""
    limite = (dt.datetime.now() - dt.timedelta(days=3)).isoformat()
    return [{**{k: s.get(k) for k in CAMPOS_PUBLICOS}, "tope": s["tope"]}
            for s in datos_subastas(prefs) if s["cierre"] >= limite]


def _repo():
    r = subprocess.run(["git", "-C", str(BASE), "remote", "get-url", "origin"], capture_output=True, text=True)
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", r.stdout)
    return m.group(1) if m else None


_lock = threading.Lock()


def _gh(*args, entrada=None):
    r = subprocess.run(["gh", "api", *args], input=entrada, capture_output=True, text=True, timeout=60,
                       encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300] or r.stdout.strip()[:300])
    return json.loads(r.stdout or "null")


def _subir(contenido):
    """Deja subastas.json como único archivo de la rama (un commit sin historial)."""
    with _lock:
        repo = _repo()
        if not repo:
            raise RuntimeError("no encuentro el repositorio de GitHub (git remote origin)")
        arbol = _gh("-X", "POST", f"repos/{repo}/git/trees", "--input", "-", entrada=json.dumps(
            {"tree": [{"path": "subastas.json", "mode": "100644", "type": "blob", "content": contenido}]}))
        commit = _gh("-X", "POST", f"repos/{repo}/git/commits", "--input", "-", entrada=json.dumps(
            {"message": "Actualizar subastas", "tree": arbol["sha"]}))
        try:
            _gh("-X", "PATCH", f"repos/{repo}/git/refs/heads/{RAMA_WEB}", "--input", "-",
                entrada=json.dumps({"sha": commit["sha"], "force": True}))
        except RuntimeError:  # la rama todavía no existe
            _gh("-X", "POST", f"repos/{repo}/git/refs", "--input", "-",
                entrada=json.dumps({"ref": f"refs/heads/{RAMA_WEB}", "sha": commit["sha"]}))


def publicar(prefs, esperar=False):
    """Publica las subastas para la versión web (en segundo plano). Usa el gh CLI con tu sesión
    de GitHub; si no está, no hace nada. En GitHub Actions no corre."""
    if os.environ.get("GITHUB_ACTIONS") or not shutil.which("gh"):
        return
    contenido = json.dumps({"actualizado": dt.datetime.now().isoformat(timespec="seconds"),
                            "subastas": publicas(prefs)}, ensure_ascii=False, indent=1)

    def tarea():
        try:
            _subir(contenido)
            log.info("Subastas publicadas en la web")
        except Exception as ex:  # noqa: BLE001 - publicar nunca debe romper la app
            log.warning("No se pudieron publicar las subastas: %s", ex)

    hilo = threading.Thread(target=tarea, daemon=True)
    hilo.start()
    if esperar:
        hilo.join(90)
