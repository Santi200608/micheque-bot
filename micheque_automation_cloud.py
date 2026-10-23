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
    "tir neta", "tipo", "bcu", "detalles de la operación", "comprar", "ofertar",
    "disponible para ofertas", "filtrar por", "banco a transferir", "la mejor oferta"
]

def limpiar_campo(val, default="-"):
    if not val:
        return default
    v = val.strip()
    if v.lower() in TITULOS_IGNORAR or "la mejor oferta" in v.lower():
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
        
        # 1. Desplegar primero las tarjetas básicas (para TIR, BCU, etc.)
        page.evaluate("""() => {
            const btns = document.querySelectorAll('*');
            btns.forEach(b => {
                const txt = (b.innerText || '').toLowerCase();
                if (txt.includes('tir') || (b.className && typeof b.className === 'string' && (b.className.includes('chevron') || b.className.includes('arrow')))) {
                    try { b.click(); } catch(e){}
                }
            });
        }""")
        page.wait_for_timeout(2000)

        # 2. Localizar botones o enlaces de "Detalles de la operación"
        botones_detalles = page.query_selector_all("text=/Detalles de la operación/i, text=/Ver detalle/i, .v-btn")
        print(f"Botones de detalles detectados ({moneda}): {len(botones_detalles)}")

        # Forzar clic en cada modal si existen
        for btn in botones_detalles:
            try:
                if btn.is_visible():
                    btn.click(force=True)
                    page.wait_for_timeout(1000)
            except Exception:
                pass

        # Capturar el texto completo de la página tras los clics
        body_text = page.inner_text("body")
        lineas = [l.strip() for l in body_text.split("\n") if l.strip() != ""]

        i = 0
        while i < len(lineas):
            linea = lineas[i]
            linea_lower = linea.lower()

            es_cheque = linea_lower == "cheque"
            es_transf = "transferencia programada" in linea_lower
            es_factura = linea_lower == "factura"

            if es_cheque or es_transf or es_factura:
                try:
                    tipo = "Transferencia Programada" if es_transf else ("Factura" if es_factura else "Cheque")
                    librador = lineas[i-1] if i-1 >= 0 else ""
                    lib_limpio = limpiar_campo(librador)

                    # Filtro de seguridad
                    if (lib_limpio == "-" or 
                        len(lib_limpio) < 2 or 
                        "%" in lib_limpio or 
                        re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", lib_limpio) or 
                        lib_limpio.replace(".", "").replace(",", "").isdigit() or 
                        "mínimo" in lib_limpio.lower() or 
                        "máximo" in lib_limpio.lower() or 
                        "oferta" in lib_limpio.lower() or 
                        "filtrar por" in lib_limpio.lower()):
                        i += 1
                        continue

                    valor = "-"
                    vto = "-"
                    solicitado = "-"
                    margen = "-"
                    tir = "-"
                    bcu = "-"
                    cobrado_usd = "-"
                    bancos_transferir = "-"
                    mejor_oferta = "-"

                    # Campos específicos del Modal
                    det_banco = "-"
                    det_emision = "-"
                    det_vencimiento = "-"
                    det_razon_social = "-"
                    det_rut = "-"
                    det_direccion = "-"
                    det_departamento = "-"
                    det_industria = "-"
                    det_cobrados = "-"
                    det_vigentes = "-"
                    det_descontado_vigente = "-"
                    det_bancos_maneja = "-"
                    det_ventas = "-"

                    # Recorrido del bloque para extraer datos de la oportunidad y del modal
                    for j in range(i+1, min(i+25, len(lineas))):
                        sub = lineas[j]
                        sub_lower = sub.lower()
                        
                        if re.match(r"^[\d\.\,]{3,15}$", sub) and valor == "-":
                            valor = sub
                        elif ("en " in sub_lower and "días" in sub_lower or re.match(r"^\d{2}/\d{2}/\d{4}$", sub)) and vto == "-":
                            vto = sub
                        elif re.match(r"^[\d\.\,]{3,15}$", sub) and valor != "-" and solicitado == "-":
                            solicitado = sub
                        elif re.match(r"^[\d\.\,]{2,10}$", sub) and solicitado != "-" and margen == "-":
                            margen = sub
                        elif ("%" in sub or re.match(r"^\d{1,2}[\,\.]\d{1,2}$", sub)) and tir == "-":
                            tir = sub
                        elif re.match(r"^\d[A-Z]$", sub) and bcu == "-":
                            bcu = sub
                        
                        # Lectura del modal si el texto está desplegado
                        elif "rut" in sub_lower and j+1 < len(lineas) and det_rut == "-":
                            cand = lineas[j+1]
                            if re.match(r"^\d{12}$", cand):
                                det_rut = cand
                        elif ("razón social" in sub_lower or "razon social" in sub_lower) and j+1 < len(lineas) and det_razon_social == "-":
                            det_razon_social = limpiar_campo(lineas[j+1])
                        elif "banco" in sub_lower and j+1 < len(lineas) and det_banco == "-":
                            det_banco = limpiar_campo(lineas[j+1])
                        elif "emisión" in sub_lower or "emision" in sub_lower and j+1 < len(lineas) and det_emision == "-":
                            cand = lineas[j+1]
                            if re.match(r"^\d{2}/\d{2}/\d{4}$", cand):
                                det_emision = cand
                        elif "dirección" in sub_lower or "direccion" in sub_lower and j+1 < len(lineas) and det_direccion == "-":
                            det_direccion = limpiar_campo(lineas[j+1])
                        elif "departamento" in sub_lower and j+1 < len(lineas) and det_departamento == "-":
                            det_departamento = limpiar_campo(lineas[j+1])

                    item = {
                        "moneda": moneda,
                        "librador": lib_limpio,
                        "valor": valor,
                        "vto": vto,
                        "solicitado": solicitado,
                        "margenNeto": margen,
                        "tirNeta": tir,
                        "tipo": tipo,
                        "bcu": bcu,
                        "cobradoUSD": cobrado_usd,
                        "bancosATransferir": bancos_transferir,
                        "mejorOferta": mejor_oferta,
                        "detValor": valor,
                        "detBanco": det_banco,
                        "detEmision": det_emision,
                        "detVencimiento": det_vencimiento if det_vencimiento != "-" else vto,
                        "detNombre": lib_limpio,
                        "detRazonSocial": det_razon_social,
                        "detRut": det_rut,
                        "detDireccion": det_direccion,
                        "detDepartamento": det_departamento,
                        "detIndustria": det_industria,
                        "detCobrados": det_cobrados,
                        "detVigentes": det_vigentes,
                        "detDescontadoVigente": det_descontado_vigente,
                        "detBancosManeja": det_bancos_maneja,
                        "detVentas": det_ventas
                    }
                    
                    if item["librador"] != "-" and item["valor"] != "-":
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
