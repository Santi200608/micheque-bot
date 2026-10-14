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
        
        # 1. Login
        page.goto(URL_LOGIN, wait_until="networkidle")
        page.wait_for_timeout(2000)
        
        page.fill("input[type='email'], input[name='email'], input[type='text']", USERNAME)
        page.fill("input[type='password'], input[name='password']", PASSWORD)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        
        # 2. Navegar a la página con las transacciones
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(5000)

        # Función para extraer filas de las tablas o bloques de transacciones
        def extraer_seccion(moneda_label):
            # Buscar filas de tabla o elementos estructurados
            filas = page.query_selector_all("table tbody tr, div.grid-cols-5, div.grid-cols-6, div:has-text('OSE')")
            
            # Si no hay tablas tradicionales, buscar todas las filas visuales por texto/elementos
            filas_genericas = page.query_selector_all("tr, div.row, div.flex-row")
            elementos = filas if len(filas) > 0 else filas_genericas

            for f in elementos:
                texto_fila = f.inner_text().split("\n")
                # Limpiar saltos de línea vacíos
                cols = [t.strip() for t in texto_fila if t.strip() != ""]
                
                # Una fila de "Últimas transacciones" tiene 5 campos: Fecha, Tipo, Librador, Monto, Plazo
                if len(cols) >= 5 and "/" in cols[0]:
                    transacciones.append({
                        "Fecha": cols[0],
                        "Tipo": cols[1],
                        "Librador": cols[2],
                        "Monto": cols[3],
                        "Plazo": cols[4],
                        "Moneda": moneda_label
                    })

        # Extraer Pesos
        btn_pesos = page.query_selector("button:has-text('Pesos'), div:has-text('Pesos uruguayos'), span:has-text('Pesos')")
        if btn_pesos:
            btn_pesos.click()
            page.wait_for_timeout(2500)
        extraer_seccion("UY$")

        # Extraer Dólares
        btn_usd = page.query_selector("button:has-text('Dólares'), div:has-text('Dólares'), span:has-text('Dólares')")
        if btn_usd:
            btn_usd.click()
            page.wait_for_timeout(2500)
            extraer_seccion("USD")

        browser.close()
    return transacciones

def ejecutar():
    nuevas = obtener_transacciones()
    if nuevas:
        print(f"Se encontraron {len(nuevas)} transacciones en la página. Enviando a Google Sheets...")
        res = requests.post(WEBHOOK_URL, json={"transacciones": nuevas})
        print(f"Respuesta de Google Sheets: {res.text}")
    else:
        print("No se encontraron transacciones en la página.")

if __name__ == "__main__":
    ejecutar()
