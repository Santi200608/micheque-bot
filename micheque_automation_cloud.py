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
        # Buscar todas las filas o tarjetas desplegables
        filas = page.query_selector_all("div, tr, .card")
        
        # Buscar botones o iconos de desplegar
        desplegables = page.query_selector_all("svg, i, span.arrow, button")
        for elem in desplegables:
            try:
                # Si el elemento hace desplegar detalles
                txt = elem.inner_text().strip()
                if "detalles de la operación" in txt.lower() or "ver más" in txt.lower() or elem.is_visible():
                    elem.click(timeout=1000)
                    page.wait_for_timeout(500)
            except Exception:
                continue

        # Buscar enlaces de "Detalles de la operación"
        links_detalles = page.get_by_text("Detalles de la operación")
        count = links_detalles.count()
        
        for idx in range(count):
            try:
                link = links_detalles.nth(idx)
                if link.is_visible():
                    link.click(force=True)
                    page.wait_for_timeout(1500)
                    
                    modal_text = page.inner_text("body")
                    
                    # Parsear datos de la ventana emergente / modal
                    librador = re.search(r"Nombre\s*\n\s*([^\n]+)", modal_text)
                    razon = re.search(r"Razón social\s*\n\s*([^\n]+)", modal_text)
                    rut = re.search(r"RUT\s*\n\s*([^\n]+)", modal_text)
                    bcu = re.search(r"(\d[A-Z])\s*\n\s*BCU", modal_text)
                    monto = re.search(r"Monto\s*\n\s*([^\n]+)", modal_text)
                    solicitado = re.search(r"Solicitado\s*\n\s*([^\n]+)", modal_text)
                    vencimiento = re.search(r"Vencimiento\s*\n\s*([^\n]+)", modal_text)
                    cobrados = re.search(r"Cobrados\s*\n\s*([^\n]+)", modal_text)
                    vigentes = re.search(r"Vigentes\s*\n\s*([^\n]+)", modal_text)
                    banco = re.search(r"Bancos que maneja\s*\n\s*([^\n]+)", modal_text)
                    ventas = re.search(r"Ventas\s*\n\s*([^\n]+)", modal_text)
                    ubicacion = re.search(r"Dirección\s*\n\s*([^\n]+)", modal_text)
                    
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
                    detalles_capturados.append(item)
                    
                    # Cerrar modal
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
            except Exception as e:
                print(f"Error al procesar modal {idx}: {e}")
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

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # 3. Extraer Pesos
        print("Extrayendo sección Pesos...")
        texto_pesos = page.inner_text("body")
        pesos = procesar_texto_seccion(texto_pesos, "UY$")
        todas_transacciones.extend(pesos)
        
        detalles_uy = extraer_detalles_desplegados(page, "UY$")
        todos_detalles.extend(detalles_uy)

        # 4. Cambiar a Dólares
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
    
    # 1. Enviar transacciones históricas
    if nuevas:
        print(f"Enviando {len(nuevas)} transacciones históricas a Google Sheets...")
        requests.post(WEBHOOK_URL, json={"transacciones": nuevas})
    
    # 2. Enviar detalles de oportunidades
    if detalles:
        print(f"Enviando {len(detalles)} detalles de oportunidades a Google Sheets...")
        requests.post(WEBHOOK_URL, json={"tipo_evento": "detalles_oportunidades", "detalles": detalles})

if __name__ == "__main__":
    ejecutar()
