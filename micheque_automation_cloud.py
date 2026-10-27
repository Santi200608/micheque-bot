import os
import re
import requests
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_ID")

URL_LOGIN = "https://micheque.uy/login"
URL_MARKET = "https://micheque.uy/market"

def procesar_texto_seccion(texto, moneda_esperada):
    transacciones = []
    lineas = [l.strip() for l in texto.split("\n") if l.strip() != ""]
    i = 0
    while i < len(lineas):
        linea = lineas[i]
        if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", linea):
            try:
                fecha = linea
                tipo = lineas[i+1] if i+1 < len(lineas) else ""
                librador = lineas[i+2] if i+2 < len(lineas) else ""
                monto = lineas[i+3] if i+3 < len(lineas) else ""
                plazo = lineas[i+4] if i+4 < len(lineas) else ""

                if any(char.isdigit() for char in monto):
                    es_usd = "USD" in monto
                    es_uys = "UY$" in monto

                    if moneda_esperada == "USD" and es_uys and not es_usd:
                        i += 4
                        continue

                    moneda_final = "USD" if es_usd else ("UY$" if es_uys else moneda_esperada)

                    t = {
                        "Fecha": fecha,
                        "Tipo": tipo,
                        "Librador": librador,
                        "Monto": monto,
                        "Plazo": plazo,
                        "Moneda": moneda_final
                    }
                    transacciones.append(t)
                    i += 4
            except Exception:
                pass
        i += 1
    return transacciones

def ejecutar():
    todas_transacciones = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        
        print("Iniciando sesión en MiCheque...")
        page.goto(URL_LOGIN, wait_until="networkidle")
        page.wait_for_timeout(2000)
        
        page.fill("input[type='email'], input[name='email'], input[type='text']", USERNAME)
        page.fill("input[type='password'], input[name='password']", PASSWORD)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        
        print("Navegando al mercado...")
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(5000)

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # 1. Pesos
        print("Extrayendo histórico en Pesos...")
        texto_pesos = page.inner_text("body")
        todas_transacciones.extend(procesar_texto_seccion(texto_pesos, "UY$"))

        # 2. Dólares
        print("Cambiando vista a Dólares...")
        try:
            elementos_usd = page.query_selector_all("button, div, span, a")
            for elem in elementos_usd:
                try:
                    txt = (elem.inner_text() or '').strip()
                    if "dólares" in txt.lower() or "dolares" in txt.lower():
                        if elem.is_visible():
                            elem.click(force=True)
                            elem.evaluate("el => el.click()")
                except Exception:
                    continue

            page.wait_for_timeout(5000)
            print("Extrayendo histórico en Dólares...")
            texto_usd = page.inner_text("body")
            todas_transacciones.extend(procesar_texto_seccion(texto_usd, "USD"))

        except Exception as e:
            print(f"Error al cambiar a Dólares: {e}")

        browser.close()

    if todas_transacciones:
        print(f"Enviando {len(todas_transacciones)} registros históricos a Google Sheets...")
        requests.post(WEBHOOK_URL, json={"transacciones": todas_transacciones})

if __name__ == "__main__":
    ejecutar()
