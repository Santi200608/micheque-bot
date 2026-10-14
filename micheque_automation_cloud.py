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
        
        # Cargar página de login
        page.goto(URL_LOGIN, wait_until="networkidle")
        page.wait_for_timeout(2000)
        
        # Completar credenciales
        page.fill("input[type='email'], input[name='email'], input[type='text']", USERNAME)
        page.fill("input[type='password'], input[name='password']", PASSWORD)
        
        # Enviar formulario presionando Enter
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        
        # Navegar a la sección del mercado
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(3000)

        # Transacciones en Pesos
        btn_pesos = page.query_selector("button:has-text('Pesos uruguayos'), div:has-text('Pesos uruguayos'), span:has-text('Pesos uruguayos')")
        if btn_pesos:
            btn_pesos.click()
            page.wait_for_timeout(1500)
        
        filas = page.query_selector_all("table tbody tr")
        for f in filas:
            cols = [c.inner_text().strip() for c in f.query_selector_all("td")]
            if len(cols) >= 5:
                transacciones.append({
                    "Fecha": cols[0], "Tipo": cols[1], "Librador": cols[2], 
                    "Monto": cols[3], "Plazo": cols[4], "Moneda": "UY$"
                })

        # Transacciones en Dólares
        btn_usd = page.query_selector("button:has-text('Dólares'), div:has-text('Dólares'), span:has-text('Dólares')")
        if btn_usd:
            btn_usd.click()
            page.wait_for_timeout(1500)
        
        filas_usd = page.query_selector_all("table tbody tr")
        for f in filas_usd:
            cols = [c.inner_text().strip() for c in f.query_selector_all("td")]
            if len(cols) >= 5:
                transacciones.append({
                    "Fecha": cols[0], "Tipo": cols[1], "Librador": cols[2], 
                    "Monto": cols[3], "Plazo": cols[4], "Moneda": "USD"
                })

        browser.close()
    return transacciones

def ejecutar():
    nuevas = obtener_transacciones()
    if nuevas:
        res = requests.post(WEBHOOK_URL, json={"transacciones": nuevas})
        print(f"Respuesta de Google Sheets: {res.text}")
    else:
        print("No se encontraron transacciones en la página.")

if __name__ == "__main__":
    ejecutar()
