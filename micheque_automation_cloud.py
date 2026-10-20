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

def extraer_detalles_desplegados(page, moneda):
    detalles_capturados = []
    try:
        page.wait_for_timeout(2000)
        
        # 1. Hacer clic en todos los elementos que puedan desplegar información en la lista
        botones_desplegar = page.query_selector_all("span, button, div.v-expansion-panel-title, .fa-chevron-down, [class*='chevron']")
        for btn in botones_desplegar:
            try:
                if btn.is_visible():
                    btn.click(force=True)
                    page.wait_for_timeout(300)
            except Exception:
                continue

        page.wait_for_timeout(1000)

        # 2. Buscar enlaces o botones de "Detalles de la operación"
        elementos_detalles = page.locator("text=/Detalles de la operación/i")
        cantidad = elementos_detalles.count()
        print(f"Se encontraron {cantidad} enlaces de detalles para {moneda}")

        for i in range(cantidad):
            try:
                elem = elementos_detalles.nth(i)
                if elem.is_visible():
                    elem.click(force=True)
                    page.wait_for_timeout(2000)  # Esperar apertura del modal

                    # Capturar el texto completo del diálogo/modal
                    modal = page.locator("div[role='dialog'], div.v-dialog, .modal-content, body")
                    modal_text = modal.inner_text()

                    # Expresiones regulares para extraer los campos del modal
                    librador = re.search(r"(?:Nombre|Deudor|Librador)\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    razon = re.search(r"Razón social\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    rut = re.search(r"RUT\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    bcu = re.search(r"(\d[A-Z])\s*\n\s*BCU", modal_text, re.IGNORECASE)
                    monto = re.search(r"Monto\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    solicitado = re.search(r"Solicitado\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    vencimiento = re.search(r"Vencimiento\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    cobrados = re.search(r"Cobrados\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    vigentes = re.search(r"Vigentes\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    banco = re.search(r"Bancos que maneja\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    ventas = re.search(r"Ventas\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    ubicacion = re.search(r"Dirección\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)

                    item = {
                        "moneda": moneda,
                        "librador": librador.group(1).strip() if librador else "",
                        "razonSocial": razon.group(1).strip() if razon else "",
                        "rut": rut.group(1).strip() if rut else "",
                        "bcu": bcu.group(1).strip() if bcu else "",
                        "tipo": "Transferencia Programada" if "Transferencia Programada" in modal_text else "Cheque/Factura",
                        "vencimiento": vencimiento.group(1).strip() if vencimiento else "",
                        "monto": monto.group(1).strip() if monto else "",
                        "solicitado": solicitado.group(1).strip() if solicitado else "",
                        "cobrados": cobrados.group(1).strip() if cobrados else "",
                        "vigentes": vigentes.group(1).strip() if vigentes else "",
                        "descontadoVigente": "-",
                        "bancosVendedor": banco.group(1).strip() if banco else "",
                        "ventas": ventas.group(1).strip() if ventas else "",
                        "ubicacion": ubicacion.group(1).strip() if ubicacion else ""
                    }
                    
                    if item["librador"] or item["rut"]:
                        detalles_capturados.append(item)

                    # Cierra el modal emergente antes de seguir con el siguiente
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(800)
            except Exception as ex:
                print(f"Error procesando modal {i}: {ex}")
                page.keyboard.press("Escape")
                continue

    except Exception as e:
        print(f"Error general en extracción de detalles: {e}")

    return detalles_capturados

def obtener_transacciones():
    todas_transacciones = []
    todos_detalles = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        
        # 1. Inicio de Sesión
        print("Iniciando sesión en MiCheque...")
        page.goto(URL_LOGIN, wait_until="networkidle")
        page.wait_for_timeout(2000)
        
        page.fill("input[type='email'], input[name='email'], input[type='text']", USERNAME)
        page.fill("input[type='password'], input[name='password']", PASSWORD)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        
        # 2. Navegación al Mercado
        print("Navegando al mercado...")
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(5000)

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # 3. Sección Pesos
        print("Extrayendo sección Pesos...")
        texto_pesos = page.inner_text("body")
        pesos = procesar_texto_seccion(texto_pesos, "UY$")
        todas_transacciones.extend(pesos)
        
        detalles_uy = extraer_detalles_desplegados(page, "UY$")
        todos_detalles.extend(detalles_uy)

        # 4. Sección Dólares
        print("Cambiando vista a Dólares...")
        try:
            elementos_usd = page.query_selector_all("button, div, span, a")
            for elem in elementos_usd:
                try:
                    txt = elem.inner_text().strip()
                    if "dólares" in txt.lower() or "dolares" in txt.lower():
                        if elem.is_visible():
                            elem.click(force=True)
                            elem.evaluate("el => el.click()")
                except Exception:
                    continue

            page.wait_for_timeout(5000)
            texto_usd = page.inner_text("body")
            usd_items = procesar_texto_seccion(texto_usd, "USD")
            todas_transacciones.extend(usd_items)
            
            detalles_usd = extraer_detalles_desplegados(page, "USD")
            todos_detalles.extend(detalles_usd)

        except Exception as e:
            print(f"Error al cambiar a Dólares: {e}")

        browser.close()
    return todas_transacciones, todos_detalles

def ejecutar():
    nuevas, detalles = obtener_transacciones()
    
    # 1. Envío al histórico
    if nuevas:
        print(f"Enviando {len(nuevas)} transacciones históricas a Google Sheets...")
        requests.post(WEBHOOK_URL, json={"transacciones": nuevas})
    
    # 2. Envío a Detalles de Oportunidades
    if detalles:
        print(f"Enviando {len(detalles)} detalles de oportunidades a Google Sheets...")
        requests.post(WEBHOOK_URL, json={"tipo_evento": "detalles_oportunidades", "detalles": detalles})

if __name__ == "__main__":
    ejecutar()
