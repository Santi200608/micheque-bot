import os
import re
import requests
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_ID")

URL_LOGIN = "https://micheque.uy/login"
URL_MARKET = "https://micheque.uy/market"

def obtener_transacciones():
    transacciones = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Pantalla con resolución de escritorio para cargar todo el layout
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        
        # 1. Login
        print("Iniciando sesión en MiCheque...")
        page.goto(URL_LOGIN, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        
        page.fill("input[type='email'], input[name='email'], input[type='text']", USERNAME)
        page.fill("input[type='password'], input[name='password']", PASSWORD)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        
        # 2. Navegar a la página con las transacciones
        print("Navegando al mercado...")
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(5000)

        # Esperar a que cargue el contenido
        try:
            page.wait_for_selector("text=Últimas transacciones", timeout=10000)
        except Exception:
            print("No se encontró el texto de cabecera, intentando lectura directa...")

        # Guardar scroll para asegurar renderizado
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # Extraer todas las filas visuales que contengan información
        elementos = page.query_selector_all("tr, div.flex, div.grid")

        for el in elementos:
            texto = el.inner_text().strip()
            lineas = [l.strip() for l in texto.split("\n") if l.strip() != ""]
            
            # Formato esperado: [Fecha, Tipo, Librador, Monto, Plazo]
            # Ejemplo: ['14/10/2026', 'Cheque', 'CASMU', 'UY$ 1.000.000', '43']
            if len(lineas) >= 5 and re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", lineas[0]):
                monto_str = lineas[3] if len(lineas) > 3 else ""
                moneda = "USD" if "USD" in monto_str else "UY$"
                
                transaccion = {
                    "Fecha": lineas[0],
                    "Tipo": lineas[1],
                    "Librador": lineas[2],
                    "Monto": monto_str,
                    "Plazo": lineas[4] if len(lineas) > 4 else "",
                    "Moneda": moneda
                }
                
                # Evitar duplicados dentro del mismo scraping
                if transaccion not in transacciones:
                    transacciones.append(transaccion)

        browser.close()
    return transacciones

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
