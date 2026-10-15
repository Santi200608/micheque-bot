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
        page.wait_for_timeout(6000)

        # Hacer scroll para forzar la carga de componentes dinámicos
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # 3. Extraer todo el texto visible de la página
        texto_pagina = page.inner_text("body")
        lineas = [l.strip() for l in texto_pagina.split("\n") if l.strip() != ""]

        # Recorrer las líneas buscando patrones de fecha (Ej: 14/10/2026 o 15/10/2026)
        i = 0
        while i < len(lineas):
            linea = lineas[i]
            # Detecta fecha DD/MM/YYYY
            if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", linea):
                try:
                    fecha = linea
                    tipo = lineas[i+1] if i+1 < len(lineas) else ""
                    librador = lineas[i+2] if i+2 < len(lineas) else ""
                    monto = lineas[i+3] if i+3 < len(lineas) else ""
                    plazo = lineas[i+4] if i+4 < len(lineas) else ""

                    # Validar que sea un registro válido (Monto debe contener UY$ o USD)
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
                        if transaccion not in transacciones:
                            transacciones.append(transaccion)
                        i += 4  # Avanzar el índice al siguiente bloque
                except Exception as e:
                    pass
            i += 1

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
