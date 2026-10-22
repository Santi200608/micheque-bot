import os
import re
import requests
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_ID")

URL_LOGIN = "https://micheque.uy/login"
URL_MARKET = "https://micheque.uy/market"

TITULOS_IGNORAR = [
    "librador", "valor", "vencimiento", "solicitado", "margen neto", 
    "tir neta", "tipo", "bcu", "detalles de la operación", "comprar", "ofertar"
]

def limpiar_campo(val, default="-"):
    if not val:
        return default
    v = val.strip()
    if v.lower() in TITULOS_IGNORAR:
        return default
    return v

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
        page.wait_for_timeout(3000)
        
        # Expandir tarjetas para ver TIR, Margen y detalles
        page.evaluate("""() => {
            const btns = document.querySelectorAll('*');
            btns.forEach(b => {
                const txt = (b.innerText || '').toLowerCase();
                if (txt.includes('detalles') || txt.includes('tir') || (b.className && typeof b.className === 'string' && (b.className.includes('chevron') || b.className.includes('arrow')))) {
                    try { b.click(); } catch(e){}
                }
            });
        }""")
        page.wait_for_timeout(2000)

        # Capturar el cuerpo de la página
        body_text = page.inner_text("body")
        lineas = [l.strip() for l in body_text.split("\n") if l.strip() != ""]

        i = 0
        while i < len(lineas):
            linea = lineas[i]
            linea_lower = linea.lower()
            
            # Frenar la lectura si llegamos al pie de página / historial inferior
            if "disponible para ofertas" in linea_lower or "mi estado de cuenta" in linea_lower:
                break

            # Identificar tipo de documento de forma flexible
            es_cheque = "cheque" in linea_lower
            es_transf = "transferencia" in linea_lower
            es_factura = "factura" in linea_lower

            if es_cheque or es_transf or es_factura:
                try:
                    tipo = "Transferencia Programada" if es_transf else ("Factura" if es_factura else "Cheque")
                    librador = lineas[i-1] if i-1 >= 0 else ""
                    
                    if limpiar_campo(librador) == "-":
                        i += 1
                        continue

                    valor = ""
                    vto = ""
                    solicitado = ""
                    margen = ""
                    tir = ""
                    bcu = ""

                    # Búsqueda posicional hacia adelante dentro de la misma tarjeta
                    for j in range(i+1, min(i+12, len(lineas))):
                        sub = lineas[j]
                        
                        # Valor Nominal
                        if re.match(r"^[\d\.\,]{3,15}$", sub) and not valor:
                            valor = sub
                        # Vencimiento (ej: en 25 días o fecha exacta)
                        elif ("en " in sub.lower() and "días" in sub.lower() or re.match(r"^\d{2}/\d{2}/\d{4}$", sub)) and not vto:
                            vto = sub
                        # Solicitado / Margen
                        elif re.match(r"^[\d\.\,]{3,15}$", sub) and valor and not solicitado:
                            solicitado = sub
                        elif re.match(r"^[\d\.\,]{2,10}$", sub) and solicitado and not margen:
                            margen = sub
                        # TIR Neta (% o decimal)
                        elif ("%" in sub or re.match(r"^\d{1,2}[\,\.]\d{1,2}$", sub)) and not tir:
                            tir = sub
                        # BCU
                        elif re.match(r"^\d[A-Z]$", sub) and not bcu:
                            bcu = sub

                    item = {
                        "moneda": moneda,
                        "librador": librador,
                        "valor": valor,
                        "vto": vto,
                        "solicitado": solicitado,
                        "margenNeto": margen,
                        "tirNeta": tir,
                        "tipo": tipo,
                        "bcu": bcu,
                        "cobradoUSD": "-",
                        "bancosATransferir": "-",
                        "mejorOferta": "-",
                        "detValor": valor,
                        "detBanco": "-",
                        "detEmision": "-",
                        "detVencimiento": vto,
                        "detNombre": librador,
                        "detRazonSocial": "-",
                        "detRut": "-",
                        "detDireccion": "-",
                        "detDepartamento": "-",
                        "detIndustria": "-",
                        "detCobrados": "-",
                        "detVigentes": "-",
                        "detDescontadoVigente": "-",
                        "detBancosManeja": "-",
                        "detVentas": "-"
                    }
                    
                    if item["librador"] and item["valor"]:
                        detalles_capturados.append(item)
                except Exception:
                    pass
            i += 1

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
        
        # 2. Mercado
        print("Navegando al mercado...")
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(5000)

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # 3. Pesos
        print("Extrayendo sección Pesos...")
        texto_pesos = page.inner_text("body")
        pesos = procesar_texto_seccion(texto_pesos, "UY$")
        todas_transacciones.extend(pesos)
        
        detalles_uy = extraer_detalles_desplegados(page, "UY$")
        todos_detalles.extend(detalles_uy)

        # 4. Dólares
        print("Cambiando vista a Dólares...")
        try:
            elementos_usd = page.query_selector_all("button, div, span, a")
            for elem in elementos_usd:
                try:
                    txt = (elem.inner_text() or '').strip()
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
    
    if nuevas:
        print(f"Enviando {len(nuevas)} transacciones históricas a Google Sheets...")
        requests.post(WEBHOOK_URL, json={"transacciones": nuevas})
    
    if detalles:
        print(f"Enviando {len(detalles)} detalles de oportunidades a Google Sheets...")
        requests.post(WEBHOOK_URL, json={"tipo_evento": "detalles_oportunidades", "detalles": detalles})

if __name__ == "__main__":
    ejecutar()
