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

def extraer_oportunidades_exactas(page, moneda):
    detalles_capturados = []
    try:
        page.wait_for_timeout(3000)
        
        # 1. Delimitar el contenedor principal del mercado (descarta el pie de página e historial)
        mercado_container = page.query_selector(".v-window-item--active, .market-content, main")
        if not mercado_container:
            mercado_container = page

        # 2. Desplegar tarjetas principales para ver TIR, Margen, etc.
        page.evaluate("""() => {
            const btns = document.querySelectorAll('.v-expansion-panel-title, [class*="chevron"], [class*="arrow"]');
            btns.forEach(b => { try { b.click(); } catch(e){} });
        }""")
        page.wait_for_timeout(1500)

        # 3. Obtener las tarjetas individuales
        tarjetas = mercado_container.query_selector_all(".v-expansion-panel, .v-card")
        print(f"Tarjetas detectadas para {moneda}: {len(tarjetas)}")

        for tarjeta in tarjetas:
            try:
                txt_tarjeta = tarjeta.inner_text()
                
                # Descartar elementos de encabezado o filtros
                if "disponible para ofertas" in txt_tarjeta.lower() or "filtrar por" in txt_tarjeta.lower():
                    continue
                if "librador" in txt_tarjeta.lower() and "valor" in txt_tarjeta.lower() and len(txt_tarjeta.split('\n')) < 5:
                    continue

                # A) Extraer campos de la tarjeta principal (Columnas C a M)
                librador_match = re.search(r"([A-Z0-9\s\.\-]{3,40})\n(?:Cheque|Transferencia Programada|Factura)", txt_tarjeta)
                valor_match = re.search(r"([\d\.\,]{3,15})\n(?:en \d+ días|\d{2}/\d{2}/\d{4})", txt_tarjeta)
                vto_match = re.search(r"(en \d+ días|\d{2}/\d{2}/\d{4})", txt_tarjeta)
                solic_match = re.search(r"Solicitado\s*\n?\s*([\d\.\,]+)", txt_tarjeta, re.IGNORECASE)
                margen_match = re.search(r"Margen [Nn]eto\s*\n?\s*([\d\.\,]+)", txt_tarjeta, re.IGNORECASE)
                tir_match = re.search(r"(\d{1,2}[\,\.]\d{1,2}\%|TIR [Nn]eta\s*\n?\s*[\d\.\,\%]+)", txt_tarjeta, re.IGNORECASE)
                tipo_match = re.search(r"(Cheque|Transferencia Programada|Factura)", txt_tarjeta)
                bcu_match = re.search(r"(\d[A-Z])\s*\n?\s*BCU", txt_tarjeta)
                cobrado_usd_match = re.search(r"Cobrada? USD\s*\n?\s*([\d\.\,]+|-)", txt_tarjeta, re.IGNORECASE)
                banco_trans_match = re.search(r"Bancos? a transferir\s*\n?\s*([A-Za-záéíóúÁÉÍÓÚ\s]+)", txt_tarjeta, re.IGNORECASE)
                mejor_oferta_match = re.search(r"(La mejor oferta es de[^\n]+)", txt_tarjeta, re.IGNORECASE)

                lib_val = limpiar_campo(librador_match.group(1) if librador_match else "")
                if lib_val == "-":
                    continue

                # B) Clic en 'Detalles de la operación' dentro de la tarjeta
                btn_detalle = tarjeta.query_selector("text=/Detalles de la operación/i, text=/Detalle/i, a, button")
                
                det_valor = det_banco = det_emision = det_vto = "-"
                det_nom = det_raz = det_rut = det_dir = det_depto = det_ind = "-"
                det_cob = det_vig = det_desc = det_bancos_man = det_ventas = "-"

                if btn_detalle and btn_detalle.is_visible():
                    btn_detalle.click(force=True)
                    
                    # Sincronización explícita: esperar a que aparezca la ventana emergente
                    try:
                        page.wait_for_selector(".v-dialog--active, div[role='dialog'], .v-overlay__content", timeout=4000)
                        modal = page.query_selector(".v-dialog--active, div[role='dialog'], .v-overlay__content")
                        modal_txt = modal.inner_text() if modal else ""

                        if modal_txt:
                            m_val = re.search(r"Valor\s*\n?\s*([UY$USD\s\d\.\,]+)", modal_txt, re.IGNORECASE)
                            m_banco = re.search(r"Banco\s*\n?\s*([A-Za-záéíóúÁÉÍÓÚ\s]+)", modal_txt, re.IGNORECASE)
                            m_emi = re.search(r"Emisión\s*\n?\s*(\d{2}/\d{2}/\d{4})", modal_txt, re.IGNORECASE)
                            m_vto = re.search(r"Vencimiento\s*\n?\s*(\d{2}/\d{2}/\d{4})", modal_txt, re.IGNORECASE)

                            m_nom = re.search(r"Nombre\s*\n?\s*([^\n]+)", modal_txt, re.IGNORECASE)
                            m_raz = re.search(r"Razón social\s*\n?\s*([^\n]+)", modal_txt, re.IGNORECASE)
                            m_rut = re.search(r"RUT\s*\n?\s*(\d{12})", modal_txt, re.IGNORECASE)
                            m_dir = re.search(r"Dirección\s*\n?\s*([^\n]+)", modal_txt, re.IGNORECASE)
                            m_depto = re.search(r"Departamento\s*\n?\s*([^\n]+)", modal_txt, re.IGNORECASE)
                            m_ind = re.search(r"Industria\s*\n?\s*([^\n]+)", modal_txt, re.IGNORECASE)
                            m_cob = re.search(r"Cobrados\s*\n?\s*([^\n]+)", modal_txt, re.IGNORECASE)
                            m_vig = re.search(r"Vigentes\s*\n?\s*([^\n]+)", modal_txt, re.IGNORECASE)
                            m_desc = re.search(r"Descontado vigente\s*\n?\s*([^\n]+)", modal_txt, re.IGNORECASE)
                            m_bman = re.search(r"Bancos que maneja\s*\n?\s*([^\n]+)", modal_txt, re.IGNORECASE)
                            m_ven = re.search(r"Ventas\s*\n?\s*(\d+)", modal_txt, re.IGNORECASE)

                            det_valor = limpiar_campo(m_val.group(1) if m_val else "")
                            det_banco = limpiar_campo(m_banco.group(1) if m_banco else "")
                            det_emision = limpiar_campo(m_emi.group(1) if m_emi else "")
                            det_vto = limpiar_campo(m_vto.group(1) if m_vto else "")

                            det_nom = limpiar_campo(m_nom.group(1) if m_nom else "")
                            det_raz = limpiar_campo(m_raz.group(1) if m_raz else "")
                            det_rut = limpiar_campo(m_rut.group(1) if m_rut else "")
                            det_dir = limpiar_campo(m_dir.group(1) if m_dir else "")
                            det_depto = limpiar_campo(m_depto.group(1) if m_depto else "")
                            det_ind = limpiar_campo(m_ind.group(1) if m_ind else "")
                            det_cob = limpiar_campo(m_cob.group(1) if m_cob else "")
                            det_vig = limpiar_campo(m_vig.group(1) if m_vig else "")
                            det_desc = limpiar_campo(m_desc.group(1) if m_desc else "")
                            det_bancos_man = limpiar_campo(m_bman.group(1) if m_bman else "")
                            det_ventas = limpiar_campo(m_ven.group(1) if m_ven else "")

                    except Exception:
                        pass
                    
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)

                # Formatear dirección y departamento
                if "," in det_dir and det_depto == "-":
                    partes = det_dir.split(",")
                    det_dir = partes[0].strip()
                    det_depto = partes[1].strip()

                banco_trans_val = limpiar_campo(banco_trans_match.group(1) if banco_trans_match else "")
                if banco_trans_val.lower() in ["comprar", "ofertar", ""]:
                    banco_trans_val = "-"

                item = {
                    "moneda": moneda,
                    "librador": lib_val,
                    "valor": limpiar_campo(valor_match.group(1) if valor_match else ""),
                    "vto": limpiar_campo(vto_match.group(1) if vto_match else ""),
                    "solicitado": limpiar_campo(solic_match.group(1) if solic_match else ""),
                    "margenNeto": limpiar_campo(margen_match.group(1) if margen_match else ""),
                    "tirNeta": limpiar_campo(tir_match.group(1) if tir_match else ""),
                    "tipo": limpiar_campo(tipo_match.group(1) if tipo_match else "Cheque"),
                    "bcu": limpiar_campo(bcu_match.group(1) if bcu_match else ""),
                    "cobradoUSD": limpiar_campo(cobrado_usd_match.group(1) if cobrado_usd_match else ""),
                    "bancosATransferir": banco_trans_val,
                    "mejorOferta": limpiar_campo(mejor_oferta_match.group(1) if mejor_oferta_match else ""),
                    "detValor": det_valor,
                    "detBanco": det_banco,
                    "detEmision": det_emision,
                    "detVencimiento": det_vto,
                    "detNombre": det_nom,
                    "detRazonSocial": det_raz,
                    "detRut": det_rut,
                    "detDireccion": det_dir,
                    "detDepartamento": det_depto,
                    "detIndustria": det_ind,
                    "detCobrados": det_cob,
                    "detVigentes": det_vig,
                    "detDescontadoVigente": det_desc,
                    "detBancosManeja": det_bancos_man,
                    "detVentas": det_ventas
                }
                detalles_capturados.append(item)

            except Exception:
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
        
        detalles_uy = extraer_oportunidades_exactas(page, "UY$")
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
            
            detalles_usd = extraer_oportunidades_exactas(page, "USD")
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
