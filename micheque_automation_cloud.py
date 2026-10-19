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

                    # Si estamos buscando USD pero el renglón dice UY$, lo salteamos
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
        
        # 2. Ir al mercado
        print("Navegando al mercado...")
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(5000)

        # Scroll para renderizar Pesos
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # 3. Extraer Pesos
        print("Extrayendo sección Pesos...")
        texto_pesos = page.inner_text("body")
        pesos = procesar_texto_seccion(texto_pesos, "UY$")
        todas_transacciones.extend(pesos)
        print(f"Pesos extraídos correctamente: {len(pesos)}")

        # 4. Cambiar a Dólares
        print("Localizando solapa de Dólares...")
        try:
            # Buscar el botón por coincidencia de texto 'Dólares' o 'Dolares'
            btn_usd = page.locator("button, div, a, span").filter(has_text=re.compile(r"Dólares|Dolares", re.I)).first
            
            if btn_usd.count() > 0:
                print("Botón encontrado. Disparando clic...")
                btn_usd.click(force=True)
                
                # Esperar hasta 12 segundos a que aparezca 'USD' o el título de la tabla de Dólares
                tabla_usd_cargada = False
                for intento in range(6):
                    page.wait_for_timeout(2000)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    texto_temp = page.inner_text("body")
                    if "USD" in texto_temp or "transacciones en Dólares" in texto_temp:
                        tabla_usd_cargada = True
                        print(f"Tabla de Dólares confirmada en la pantalla (intento {intento+1}).")
                        break
                
                if not tabla_usd_cargada:
                    print("Reintentando clic en la solapa de Dólares por evento JS...")
                    btn_usd.evaluate("el => el.click()")
                    page.wait_for_timeout(5000)

                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)
                
                texto_usd = page.inner_text("body")
                usd_items = procesar_texto_seccion(texto_usd, "USD")
                
                print(f"Transacciones en Dólares detectadas: {len(usd_items)}")
                for u in usd_items:
                    todas_transacciones.append(u)
            else:
                print("No se encontró la solapa de Dólares en la pantalla.")
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
