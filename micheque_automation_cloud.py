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
        page.wait_for_timeout(3000)
        
        # 1. Expandir desplegables en la lista principal
        page.evaluate("""() => {
            const btns = document.querySelectorAll('.v-expansion-panel-title, [class*="chevron"], [class*="arrow"]');
            btns.forEach(b => { try { b.click(); } catch(e){} });
        }""")
        page.wait_for_timeout(1500)

        # 2. Obtener todas las tarjetas/filas de oportunidades
        filas = page.query_selector_all("div.v-expansion-panel, .v-card, tr, .opportunity-item")
        print(f"Filas/Tarjetas encontradas para {moneda}: {len(filas)}")

        for fila in filas:
            try:
                txt_fila = fila.inner_text()
                
                # Ignorar encabezados o elementos vacíos
                if "librador" in txt_fila.lower() and "valor" in txt_fila.lower() and len(txt_fila.split('\n')) < 5:
                    continue

                # Extraer datos de la fila de la vista principal
                librador = re.search(r"([A-Z0-9\s\.\-]{3,40})\n(?:Cheque|Transferencia|Factura)", txt_fila)
                monto_val = re.search(r"([\d\.\,]{3,15})\n(?:en \d+ días|\d{2}/\d{2}/\d{4})", txt_fila)
                vto_dias = re.search(r"(en \d+ días|\d{2}/\d{2}/\d{4})", txt_fila)
                monto_solic = re.search(r"Solicitado\n?([\d\.\,]+)", txt_fila)
                margen_neto = re.search(r"Margen neto\n?([\d\.\,]+)", txt_fila)
                tir_neta = re.search(r"TIR neta\n?([\d\.\,\%]+)", txt_fila)
                tipo_doc = re.search(r"(Cheque|Transferencia Programada|Factura)", txt_fila)
                bcu_val = re.search(r"(\d[A-Z])", txt_fila)
                banco_trans = re.search(r"(Santander|BROU|Itaú|BBVA|Scotiabank|HSBC|Bandes)", txt_fila, re.IGNORECASE)

                # Intentar hacer clic en 'Detalles de la operación' dentro de ESTA fila
                btn_detalle = fila.query_selector("text=/Detalles de la operación/i, text=/Detalle/i, a, button")
                
                det_monto = det_banco = det_emision = det_vto = ""
                det_nom = det_raz = det_rut = det_dir = det_depto = det_ind = ""
                det_cob = det_vig = det_desc = det_bancos_man = det_ventas = ""

                if btn_detalle and btn_detalle.is_visible():
                    btn_detalle.click(force=True)
                    page.wait_for_timeout(2000)

                    # Leer SOLO el contenedor del modal abierto
                    modal = page.query_selector("div[role='dialog'], .v-dialog, .v-overlay__content, .modal-content")
                    txt_modal = modal.inner_text() if modal else page.evaluate("() => document.body.innerText")

                    # Extraer campos del modal
                    dm = re.search(r"Monto\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    db = re.search(r"Banco\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    de = re.search(r"Emisión\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    dv = re.search(r"Vencimiento\s*\n\s*(\d{2}/\d{2}/\d{4})", txt_modal, re.IGNORECASE)

                    dn = re.search(r"Nombre\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    dr = re.search(r"Razón social\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    drut = re.search(r"RUT\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    ddir = re.search(r"Dirección\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    ddepto = re.search(r"Departamento\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    dind = re.search(r"Industria\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    dcob = re.search(r"Cobrados\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    dvig = re.search(r"Vigentes\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    ddesc = re.search(r"Descontado vigente\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    dbman = re.search(r"Bancos que maneja\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)
                    dven = re.search(r"Ventas\s*\n\s*([^\n]+)", txt_modal, re.IGNORECASE)

                    det_monto = dm.group(1).strip() if dm else ""
                    det_banco = db.group(1).strip() if db else ""
                    det_emision = de.group(1).strip() if de else ""
                    det_vto = dv.group(1).strip() if dv else ""

                    det_nom = dn.group(1).strip() if dn else ""
                    det_raz = dr.group(1).strip() if dr else ""
                    det_rut = drut.group(1).strip() if drut else ""
                    det_dir = ddir.group(1).strip() if ddir else ""
                    det_depto = ddepto.group(1).strip() if ddepto else ""
                    det_ind = dind.group(1).strip() if dind else ""
                    det_cob = dcob.group(1).strip() if dcob else "-"
                    det_vig = dvig.group(1).strip() if dvig else ""
                    det_desc = ddesc.group(1).strip() if ddesc else "-"
                    det_bancos_man = dbman.group(1).strip() if dbman else ""
                    det_ventas = dven.group(1).strip() if dven else ""

                    # Cerrar el modal
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(600)

                # Separar departamento si viene en la dirección
                dir_final = det_dir
                depto_final = det_depto
                if "," in dir_final:
                    p_dir = dir_final.split(",")
                    dir_final = p_dir[0].strip()
                    depto_final = p_dir[1].strip()

                lib_final = det_nom if det_nom else (det_raz if det_raz else (librador.group(1).strip() if librador else ""))

                if lib_final and lib_final.lower() not in ["valor", "vencimiento", "solicitado", "librador"]:
                    item = {
                        "moneda": moneda,
                        "librador": lib_final,
                        "valor": monto_val.group(1).strip() if monto_val else "",
                        "vto": vto_dias.group(1).strip() if vto_dias else "",
                        "solicitado": monto_solic.group(1).strip() if monto_solic else "",
                        "margenNeto": margen_neto.group(1).strip() if margen_neto else "-",
                        "tirNeta": tir_neta.group(1).strip() if tir_neta else "-",
                        "tipo": tipo_doc.group(1).strip() if tipo_doc else "Cheque",
                        "bcu": bcu_val.group(1).strip() if bcu_val else "",
                        "cobradoUSD": "-",
                        "bancosATransferir": banco_trans.group(1).strip() if banco_trans else "-",
                        "mejorOferta": "-",
                        "detValor": det_monto,
                        "detBanco": det_banco,
                        "detEmision": det_emision,
                        "detVencimiento": det_vto,
                        "detNombre": det_nom,
                        "detRazonSocial": det_raz,
                        "detRut": det_rut,
                        "detDireccion": dir_final,
                        "detDepartamento": depto_final,
                        "detIndustria": det_ind,
                        "detCobrados": det_cob,
                        "detVigentes": det_vig,
                        "detDescontadoVigente": det_desc,
                        "detBancosManeja": det_bancos_man,
                        "detVentas": det_ventas
                    }
                    detalles_capturados.append(item)

            except Exception as e_fila:
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
