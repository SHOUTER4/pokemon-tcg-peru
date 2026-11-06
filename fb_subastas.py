"""Lee las pujas de las subastas de Facebook sin Claude, solo LEYENDO (nunca comenta ni reacciona).

Usa un Chrome aparte (perfil en data/fb-perfil) donde inicias sesión una vez con --login; tu
sesión de Facebook se queda solo en esta PC (nunca va a GitHub). Una tarea de Windows lo corre
cada 5 minutos, pero solo abre Facebook cuando toca:

  - cada ~40 minutos por subasta;
  - cada ~10 minutos en su última hora;
  - ~10 minutos antes del cierre (y te avisa al celular: puja, si es tuya y el tope);
  - 2 minutos después del cierre (lectura final: ganada o perdida).

Si alguien supera tu puja también te avisa. Las pujas se anotan en la app y en la web como
siempre (subastas.marcar), sin nombres de otros pujadores.

Uso:
  python fb_subastas.py --login        abre Chrome para iniciar sesión en Facebook (una vez)
  python fb_subastas.py --instalar     crea la tarea de Windows (cada 5 min)
  python fb_subastas.py --desinstalar  la quita
  python fb_subastas.py --todas        lee ahora todas las subastas abiertas
  python fb_subastas.py                lo que hace la tarea: lee solo las que tocan
"""
import datetime as dt
import random
import subprocess
import sys
import time

import notificar
import scanner
import subastas
from scanner import BASE, log

PERFIL = BASE / "data" / "fb-perfil"
TAREA = "TCG Subastas Facebook"
CADA_NORMAL = 40          # minutos entre lecturas de una subasta
CADA_FINAL = 10           # en su última hora
AVISO_ANTES = 11          # minutos antes del cierre: aviso "faltan ~10 min"
FINAL_TRAS = 2            # minutos después del cierre: lectura final

LEER_JS = r"""
async () => {
  const espera = ms => new Promise(r => setTimeout(r, ms));
  await espera(3000);
  for (let i = 0; i < 10; i++) {   // "Ver N comentarios más": solo despliega, no escribe nada
    const b = [...document.querySelectorAll('div[role=button],span[role=button]')]
      .find(x => /^Ver (\d+ )?(comentarios?|respuestas?) m[aá]s|^Ver m[aá]s comentarios|^View (\d+ )?more comments/i.test(x.innerText.trim()));
    if (!b) break;
    b.click(); await espera(1800 + Math.random() * 1200);
  }
  const yo = ((document.body.innerText.match(/(?:Comentar como|Comment as) ([^\n]+)/) || [])[1] || '').trim();
  const com = [...document.querySelectorAll('div[role=article][aria-label^="Comentario"], div[role=article][aria-label^="Comment"]')].map(a => {
    const autor = ((a.getAttribute('aria-label') || '').match(/^(?:Comentario de|Comment by) (.+?) (?:hace|\d)/) || [])[1] || '';
    const lineas = a.innerText.split('\n').map(s => s.trim()).filter(Boolean);
    const m = lineas.map(l => l.match(/^(?:S\/\.?\s*)?(\d{1,4}(?:[.,]\d{1,2})?)(?:\s*soles?)?$/i)).find(Boolean);
    return { autor, monto: m ? parseFloat(m[1].replace(',', '.')) : null };
  });
  const login = /\/login|checkpoint/.test(location.href) || !!document.querySelector('form[action*="login"]') || (!yo && !com.length);
  return { yo, com, login };
}
"""


def _ahora():
    return dt.datetime.now().replace(microsecond=0)


def _hace(ts):
    return (_ahora() - dt.datetime.fromisoformat(ts)).total_seconds() / 60 if ts else 1e9


def pendientes(prefs, todas=False):
    """Subastas de Facebook que toca leer ahora, con el motivo ("normal", "aviso", "final")."""
    out = []
    for s in prefs.get("subastas", {}).values():
        if "facebook.com" not in s.get("url", "") or s.get("fb_final") or s.get("resultado"):
            continue
        falta = (dt.datetime.fromisoformat(s["cierre"]) - _ahora()).total_seconds() / 60
        hace = _hace(s.get("fb_leida"))
        if falta < -FINAL_TRAS:
            out.append((s, "final"))
        elif falta < 0:
            continue                       # recién cerró: espera la lectura final
        elif falta <= AVISO_ANTES and not s.get("fb_aviso"):
            out.append((s, "aviso"))
        elif todas or hace >= (CADA_FINAL if falta <= 60 else CADA_NORMAL) - 1:
            out.append((s, "normal"))
    return out


def _soles(x):
    return f"S/ {x:g}" if x is not None else "—"


def procesar(sid, motivo, lectura):
    """Anota lo leído en la subasta y manda los avisos que correspondan."""
    prefs = scanner.cargar_prefs()
    s = prefs["subastas"].get(sid)
    if not s:
        return
    yo = (lectura["yo"] or prefs.get("fb_nombre") or "").strip()
    if lectura["yo"] and prefs.get("fb_nombre") != lectura["yo"]:
        prefs["fb_nombre"] = lectura["yo"]
    montos = [c for c in lectura["com"] if c["monto"]]
    maximo = max((c["monto"] for c in montos), default=None)
    mio = max((c["monto"] for c in montos if yo and c["autor"] == yo), default=None)
    titulo, tope = s["titulo"], (round(s["mercado_pen"] * subastas.TOPE) if s.get("mercado_pen") else None)
    if maximo and maximo > (s.get("puja_actual") or 0):
        subastas.marcar(prefs, sid, puja_actual=maximo)
        prefs = scanner.cargar_prefs()
        s = prefs["subastas"][sid]
    s["fb_leida"] = _ahora().isoformat()
    s["fb_mia"] = mio
    s["fb_va_ganando"] = bool(mio and maximo and mio >= maximo)
    nota_tope = f" · tope {_soles(tope)}" + (" (ya lo pasó)" if tope and maximo and maximo > tope else "") if tope else ""

    if motivo == "aviso":
        s["fb_aviso"] = True
        quien = "tuya" if s["fb_va_ganando"] else ("te superaron" if mio else "no has pujado")
        notificar.enviar(f"Faltan ~10 min: {titulo}", f"Va en {_soles(maximo)} ({quien}){nota_tope}."
                         + (f" Tu máximo: {_soles(s['mi_maximo'])}." if s.get("mi_maximo") else ""),
                         s["url"], windows=False, etiqueta=sid)
    elif mio and maximo and maximo > mio and s.get("fb_superado") != maximo:
        s["fb_superado"] = maximo
        notificar.enviar(f"Te superaron: {titulo}", f"Va en {_soles(maximo)}; tu puja {_soles(mio)}{nota_tope}.",
                         s["url"], etiqueta=sid)

    if motivo == "final":
        s["fb_final"] = True
        unico = sum(1 for c in montos if c["monto"] == maximo) == 1
        if mio and mio == maximo and unico:
            s["resultado"] = "ganada"
            texto = f"¡La ganaste por {_soles(maximo)}!" + (" Pasó el tope." if tope and maximo > tope else "")
        elif mio and mio == maximo:
            texto = f"Empate en {_soles(maximo)}: revisa quién pujó primero."
        else:
            s["resultado"] = "perdida" if mio else s.get("resultado")
            texto = f"Cerró en {_soles(maximo)}" + (f"; tu puja fue {_soles(mio)}." if mio else " (no pujaste).")
        notificar.enviar(f"Cerró: {titulo}", texto, s["url"], etiqueta=sid)
    scanner.guardar_prefs(prefs)
    log.info("Facebook %s (%s): máx %s, tuya %s", titulo, motivo, maximo, mio)


def _abrir(p, headless=True):
    PERFIL.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        str(PERFIL), channel="chrome", headless=headless, locale="es-PE", timezone_id="America/Lima",
        viewport={"width": 1280, "height": 900}, args=["--disable-blink-features=AutomationControlled"])


def login():
    """Abre Chrome (perfil aparte) en Facebook para que TÚ inicies sesión. Se cierra al cerrar la ventana."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _abrir(p, headless=False)
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto("https://www.facebook.com/")
        print("Inicia sesión en la ventana de Chrome que se abrió y luego ciérrala.")
        while ctx.pages:
            time.sleep(1)
        ctx.close()


def correr(todas=False):
    prefs = scanner.cargar_prefs()
    notificar.recoger()
    tareas = pendientes(prefs, todas)
    if not tareas:
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _abrir(p)
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for s, motivo in tareas:
                try:
                    pagina.goto(s["url"], wait_until="domcontentloaded", timeout=45000)
                    lectura = pagina.evaluate(LEER_JS)
                except Exception as ex:  # noqa: BLE001 - una subasta que falla no frena las demás
                    log.warning("No pude leer %s: %s", s["titulo"], ex)
                    continue
                if lectura["login"]:
                    _sesion_vencida(prefs)
                    return
                procesar(s["id"], motivo, lectura)
                time.sleep(random.uniform(4, 9))   # sin prisa: parecer una persona
        finally:
            ctx.close()
    subastas.publicar(scanner.cargar_prefs(), esperar=True)


def _sesion_vencida(prefs):
    if _hace(prefs.get("fb_sesion_aviso")) < 12 * 60:
        return
    prefs = scanner.cargar_prefs()
    prefs["fb_sesion_aviso"] = _ahora().isoformat()
    scanner.guardar_prefs(prefs)
    notificar.enviar("Facebook pide iniciar sesión",
                     "No pude leer las subastas. En la PC abre «Facebook - iniciar sesión.bat» (o python fb_subastas.py --login).")


def instalar():
    exe = sys.executable.replace("python.exe", "pythonw.exe")   # sin ventana de consola
    accion = (f"New-ScheduledTaskAction -Execute '{exe}' -Argument '\"{BASE / 'fb_subastas.py'}\"' "
              f"-WorkingDirectory '{BASE}'")
    comando = (f"$a = {accion}; "
               "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
               "-RepetitionInterval (New-TimeSpan -Minutes 5); "
               "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
               "-StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15); "
               f"Register-ScheduledTask -TaskName '{TAREA}' -Action $a -Trigger $t -Settings $s -Force "
               "-Description 'Lee las pujas de tus subastas de Facebook (solo lectura)' | Out-Null")
    r = subastas._powershell(comando)
    print("Tarea creada: corre cada 5 minutos." if r and r.returncode == 0 else "No se pudo crear la tarea.")


def desinstalar():
    subastas._powershell(f"Unregister-ScheduledTask -TaskName '{TAREA}' -Confirm:$false -ErrorAction SilentlyContinue")
    print("Tarea quitada.")


def main():
    import logging
    if sys.stdout:   # con pythonw (la tarea) no hay consola: solo data/escaneo.log
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    scanner.configurar_log(*([logging.StreamHandler(sys.stdout)] if sys.stdout else []))
    if "--login" in sys.argv:
        login()
    elif "--instalar" in sys.argv:
        instalar()
    elif "--desinstalar" in sys.argv:
        desinstalar()
    elif "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
    else:
        correr(todas="--todas" in sys.argv)


if __name__ == "__main__":
    main()
