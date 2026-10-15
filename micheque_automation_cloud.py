import os
import re
import requests
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_ID")

URL_LOGIN = "https://micheque.uy/login"
URL_MARKET = "https://micheque.uy/market"

def procesar_texto_seccion(texto, moneda_default):
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
                    moneda_final = moneda_default
                    if "USD" in monto:
                        moneda_final = "USD"
                    elif "UY$" in monto:
                        moneda_final = "UY$"

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

        # Scroll para renderizar
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # 3. Extraer Pesos
        print("Extrayendo sección Pesos...")
        texto_pesos = page.inner_text("body")
        pesos = procesar_texto_seccion(texto_pesos, "UY$")
        todas_transacciones.extend(pesos)

        # 4. Cambiar a Dólares usando múltiple estrategia de clic
        print("Cambiando a vista de Dólares...")
        try:
            # Buscar selectores/botones de dólares
            pestañas_usd = page.query_selector_all("button, div, span, a")
            hizo_click = False
            
            for elem in pestañas_usd:
                txt = elem.inner_text().strip().lower()
                if txt in ["dólares", "dolares", "usd"]:
                    # Forzar clic por evento directo de JS
                    elem.evaluate("el => el.click()")
                    hizo_click = True
                    print("Clic en Dólares ejecutado con éxito.")
                    break
            
            if hizo_click:
                page.wait_for_timeout(4000)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)
                texto_usd = page.inner_text("body")
                usd_items = procesar_texto_seccion(texto_usd, "USD")
                
                print(f"Se encontraron {len(usd_items)} registros en la vista de Dólares.")
                for u in usd_items:
                    # Garantizar marca en USD si proviene de esta vista
                    if u["Moneda"] == "UY$" and "UY$" not in u["Monto"]:
                        u["Moneda"] = "USD"
                    todas_transacciones.append(u)
            else:
                print("No se encontró el botón/pestaña explícito de Dólares.")
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
