import os
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
        page = browser.new_page()
        
        # Login
        page.goto(URL_LOGIN, wait_until="networkidle")
        page.wait_for_timeout(2000)
        
        page.fill("input[type='email'], input[name='email'], input[type='text']", USERNAME)
        page.fill("input[type='password'], input[name='password']", PASSWORD)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        
        # Ir al mercado
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(5000)

        # Buscar filas de transacciones
        filas = page.query_selector_all("table tbody tr, tr")

        for f in filas:
            cols = [c.inner_text().strip() for c in f.query_selector_all("td")]
            if len(cols) >= 5 and "/" in cols[0]:
                monto_texto = cols[3]
                moneda = "USD" if "USD" in monto_texto else "UY$"
                
                transacciones.append({
                    "Fecha": cols[0],
                    "Tipo": cols[1],
                    "Librador": cols[2],
                    "Monto": cols[3],
                    "Plazo": cols[4],
                    "Moneda": moneda
                })

        browser.close()
    return transacciones

def ejecutar():
    nuevas = obtener_transacciones()
    if nuevas:
        print(f"Se encontraron {len(nuevas)} transacciones. Enviando a Google Sheets...")
        res = requests.post(WEBHOOK_URL, json={"transacciones": nuevas})
        print(f"Respuesta de Google Sheets: {res.text}")
    else:
        print("No se encontraron transacciones en la página.")

if __name__ == "__main__":
    ejecutar()
