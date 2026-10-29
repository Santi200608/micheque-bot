import os
import re
import requests
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_ID")

URL_LOGIN = "https://micheque.uy/login"
URL_HISTORICO = "https://micheque.uy/market"

TITULOS_IGNORAR = [
    "fecha", "moneda", "librador", "valor", "vto", "tir", "monto invertido",
    "tipo", "bcu", "cobrado usd", "bancos", "bps", "dgi", "inforcheck",
    "relación", "relación comercial", "filtrar por", "comprar", "ofertar",
    "pesos uruguayos", "dólares", "dolares"
]

def limpiar_campo(val, default="-"):
    if not val:
        return default
    v = val.strip()
    if v.lower() in TITULOS_IGNORAR:
        return default
    return v

def extraer_operaciones_historicas(page, moneda):
    operaciones = []
    page.wait_for_timeout(3000)

    # Lectura del texto visible en pantalla
    body_text = page.inner_text("body")
    lineas = [l.strip() for l in body_text.split("\n") if l.strip() != ""]

    i = 0
    while i < len(lineas):
        linea = lineas[i]
        linea_lower = linea.lower()

        # Detección de operaciones por tipo de documento
        es_cheque = linea_lower == "cheque"
        es_transf = "transferencia programada" in linea_lower
        es_factura = linea_lower == "factura"

        if es_cheque or es_transf or es_factura:
            try:
                tipo = "Transferencia Programada" if es_transf else ("Factura" if es_factura else "Cheque")
                librador = lineas[i-1] if i-1 >= 0 else ""
                lib_limpio = limpiar_campo(librador)

                # Validar que sea un librador legítimo
                if (lib_limpio == "-" or 
                    len(lib_limpio) < 2 or 
                    "%" in lib_limpio or 
                    re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", lib_limpio) or 
                    lib_limpio.replace(".", "").replace(",", "").isdigit()):
                    i += 1
                    continue

                fecha = "-"
                valor = "-"
                vto = "-"
                tir = "-"
                monto_inv = "-"
                bcu = "-"
                cobrado_usd = "-"
                bancos = "-"
                bps = "-"
                dgi = "-"
                inforcheck = "-"
                relacion = "-"

                # Explorar las líneas adyacentes para armar los campos de la operación
                for j in range(max(0, i-4), min(len(lineas), i+30)):
                    sub = lineas[j]
                    sub_low = sub.lower()

                    if re.match(r"^\d{2}/\d{2}/\d{4}$", sub) and fecha == "-":
                        fecha = sub
                    elif re.match(r"^[\d\.\,]{3,15}$", sub) and valor == "-":
                        valor = sub
                    elif ("en " in sub_low and "días" in sub_low) and vto == "-":
                        vto = sub
                    elif ("%" in sub or re.match(r"^\d{1,2}[\,\.]\d{1,2}$", sub)) and tir == "-":
                        tir = sub
                    elif re.match(r"^[\d\.\,]{3,15}$", sub) and valor != "-" and monto_inv == "-":
                        monto_inv = sub
                    elif sub_low == "bcu" and bcu == "-":
                        for k in range(j-1, max(0, j-4), -1):
                            if re.match(r"^[1-5][A-Za-z]?$", lineas[k]):
                                bcu = lineas[k]
                                break

                op = {
                    "fecha": fecha,
                    "moneda": moneda,
                    "librador": lib_limpio,
                    "valor": valor,
                    "vto": vto,
                    "tir": tir,
                    "montoInvertido": monto_inv,
                    "tipo": tipo,
                    "bcu": bcu,
                    "cobradoUSD": cobrado_usd,
                    "bancos": bancos,
                    "bps": bps,
                    "dgi": dgi,
                    "inforcheck": inforcheck,
                    "relacion": relacion
                }

                if op["librador"] != "-" and op["valor"] != "-":
                    operaciones.append(op)
            except Exception:
                pass
        i += 1

    print(f"[{moneda}] Operaciones históricas leídas: {len(operaciones)}")
    return operaciones

def ejecutar():
    todas_operaciones = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900}
        )
        page = context.new_page()

        print("Iniciando sesión en MiCheque...")
        page.goto(URL_LOGIN, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)

        # Llenar credenciales
        inputs_email = page.locator("input[type='email'], input[name='email'], input[type='text']")
        if inputs_email.count() > 0:
            inputs_email.first.fill(USERNAME)

        inputs_pass = page.locator("input[type='password'], input[name='password']")
        if inputs_pass.count() > 0:
            inputs_pass.first.fill(PASSWORD)

        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)

        print("Navegando a la sección del mercado / histórico...")
        page.goto(URL_HISTORICO, wait_until="load", timeout=60000)
        page.wait_for_timeout(6000)

        # 1. Pesos
        print("Extrayendo histórico en Pesos...")
        ops_uy = extraer_operaciones_historicas(page, "UY$")
        todas_operaciones.extend(ops_uy)

        # 2. Dólares
        print("Cambiando a Dólares...")
        try:
            tab_usd = page.locator(".v-tab, .v-btn, [role='tab'], div").filter(has_text=re.compile(r"^D[oó]lares", re.IGNORECASE)).first
            if tab_usd.is_visible():
                tab_usd.click(force=True)
                page.wait_for_timeout(5000)
                print("Extrayendo histórico en Dólares...")
                ops_usd = extraer_operaciones_historicas(page, "USD")
                todas_operaciones.extend(ops_usd)
            else:
                print("No se encontró pestaña de Dólares.")
        except Exception as e:
            print(f"Error alternando a USD: {e}")

        browser.close()

    print(f"Total de operaciones históricas a enviar: {len(todas_operaciones)}")
    if todas_operaciones and WEBHOOK_URL:
        try:
            resp = requests.post(WEBHOOK_URL, json={"operaciones": todas_operaciones}, timeout=30)
            print(f"Respuesta Sheets Histórico: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"Error al enviar datos: {e}")
    else:
        print("No se enviaron datos (sin registros o WEBHOOK_URL no configurado).")

if __name__ == "__main__":
    ejecutar()
