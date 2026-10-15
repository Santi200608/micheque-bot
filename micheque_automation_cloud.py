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
        # Detectar patrón de fecha DD/MM/YYYY
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

                    # Si estamos extrayendo la sección Dólares pero la tabla todavía muestra UY$, descartamos esa línea
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

def obtener_transacciones():
    todas_transacciones = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        
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

        # Scroll para renderizado
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # 3. Extraer Pesos
        print("Extrayendo sección Pesos...")
        texto_pesos = page.inner_text("body")
        pesos = procesar_texto_seccion(texto_pesos, "UY$")
        todas_transacciones.extend(pesos)
        print(f"Pesos extraídos: {len(pesos)}")

        # 4. Cambiar a la pestaña de Dólares
        print("Cambiando a la vista de Dólares...")
        try:
            elementos = page.query_selector_all("button, div, span, a")
            target_usd = None
            
            for elem in elementos:
                try:
                    txt = elem.inner_text().strip()
                    if re.search(r"^dólares|^dolares", txt, re.IGNORECASE):
                        target_usd = elem
                        break
                except Exception:
                    continue

            if target_usd:
                target_usd.evaluate("el => el.click()")
                print("Clic realizado. Esperando actualización de la tabla de dólares...")
                
                # Espera extendida de 8 segundos para dar tiempo al renderizado de la tabla inferior
                page.wait_for_timeout(8000)
                
                # Intentar esperar dinámicamente si aparece el texto 'USD'
                try:
                    page.wait_for_function("document.body.innerText.includes('USD')", timeout=5000)
                except Exception:
                    print("Tiempo de espera dinámico agotado, continuando con lectura...")

                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)
                
                texto_usd = page.inner_text("body")
                usd_items = procesar_texto_seccion(texto_usd, "USD")
                print(f"Dólares extraídos reales: {len(usd_items)}")
                
                for u in usd_items:
                    todas_transacciones.append(u)
            else:
                print("No se encontró el botón de Dólares.")
        except Exception as e:
            print(f"Error al cambiar a Dólares: {e}")

        browser.close()
    return todas_transacciones

def ejecutar():
    nuevas = obtener_transacciones()
    if nuevas:
        print(f"¡Éxito! Se extrajeron {len(nuevas)} transacciones en total. Enviando a Google Sheets...")
        res = requests.post(WEBHOOK_URL, json={"transacciones": nuevas})
        print(f"Respuesta de Google Sheets: {res.text}")
    else:
        print("No se encontraron transacciones en la página.")

if __name__ == "__main__":
    ejecutar()
