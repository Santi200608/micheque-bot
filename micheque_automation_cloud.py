import os
import re
import requests
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_ID")

URL_LOGIN = "https://micheque.uy/login"
URL_MARKET = "https://micheque.uy/market"

def extraer_lista_desde_texto(texto):
    transacciones = []
    lineas = [l.strip() for l in texto.split("\n") if l.strip() != ""]
    i = 0
    while i < len(lineas):
        linea = lineas[i]
        # Detectar fecha en formato DD/MM/YYYY
        if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", linea):
            try:
                fecha = linea
                tipo = lineas[i+1] if i+1 < len(lineas) else ""
                librador = lineas[i+2] if i+2 < len(lineas) else ""
                monto = lineas[i+3] if i+3 < len(lineas) else ""
                plazo = lineas[i+4] if i+4 < len(lineas) else ""

                if "UY$" in monto or "USD" in monto:
                    moneda = "USD" if "USD" in monto else "UY$"
                    transaccion = {
                        "Fecha": fecha,
                        "Tipo": tipo,
                        "Librador": librador,
                        "Monto": monto,
                        "Plazo": plazo,
                        "Moneda": moneda
                    }
                    transacciones.append(transaccion)
                    i += 4
            except Exception:
                pass
        i += 1
    return transacciones

def obtener_transacciones():
    todas_transacciones = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        
        # 1. Login
        print("Iniciando sesión en MiCheque...")
        page.goto(URL_LOGIN, wait_until="networkidle")
        page.wait_for_timeout(2000)
        
        page.fill("input[type='email'], input[name='email'], input[type='text']", USERNAME)
        page.fill("input[type='password'], input[name='password']", PASSWORD)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        
        # 2. Navegar al mercado
        print("Navegando al mercado...")
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(5000)

        # Extraer Pesos
        print("Buscando transacciones en Pesos...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)
        texto_pesos = page.inner_text("body")
        todas_transacciones.extend(extraer_lista_desde_texto(texto_pesos))

        # Cambiar a Dólares y extraer
        try:
            print("Cambiando a pestaña Dólares...")
            btn_usd = page.query_selector("button:has-text('Dólares'), div:has-text('Dólares'), span:has-text('Dólares')")
            if btn_usd:
                btn_usd.click()
                page.wait_for_timeout(3000)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)
                texto_usd = page.inner_text("body")
                t_usd = extraer_lista_desde_texto(texto_usd)
                
                for t in t_usd:
                    if t not in todas_transacciones:
                        todas_transacciones.append(t)
        except Exception as e:
            print(f"Error al cambiar a dólares: {e}")

        browser.close()
    return todas_transacciones

def ejecutar():
    nuevas = obtener_transacciones()
    if nuevas:
        print(f"¡Éxito! Se encontraron {len(nuevas)} transacciones. Enviando a Google Sheets...")
        res = requests.post(WEBHOOK_URL, json={"transacciones": nuevas})
        print(f"Respuesta de Google Sheets: {res.text}")
    else:
        print("No se encontraron transacciones en la página.")

if __name__ == "__main__":
    ejecutar()
