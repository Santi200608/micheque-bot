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
        
        # 1. Obtener todas las tarjetas individuales de ofertas
        cards = page.query_selector_all(".v-expansion-panel, .v-card")
        print(f"Tarjetas encontradas para {moneda}: {len(cards)}")

        for card in cards:
            try:
                txt_card = card.inner_text()
                
                # Descartar paneles que no sean ofertas activas
                if "disponible para ofertas" in txt_card.lower() or "filtrar por" in txt_card.lower():
                    continue

                # Extraer datos de la tarjeta principal
                librador_match = re.search(r"([A-Z0-9\s\.\-]{3,40})\n(?:Cheque|Transferencia Programada|Factura)", txt_card)
                tipo_match = re.search(r"(Cheque|Transferencia Programada|Factura)", txt_card)
                valor_match = re.search(r"([\d\.\,]{3,15})\n(?:en \d+ días|\d{2}/\d{2}/\d{4})", txt_card)
                vto_match = re.search(r"(en \d+ días|\d{2}/\d{2}/\d{4})", txt_card)
                solic_match = re.search(r"Solicitado\s*\n?\s*([\d\.\,]+)", txt_card, re.IGNORECASE)
                margen_match = re.search(r"Margen [Nn]eto\s*\n?\s*([\d\.\,]+)", txt_card, re.IGNORECASE)
                tir_match = re.search(r"(\d{1,2}[\,\.]\d{1,2}\%|TIR [Nn]eta\s*\n?\s*[\d\.\,\%]+)", txt_card, re.IGNORECASE)
                bcu_match = re.search(r"(\d[A-Z])\s*\n?\s*BCU", txt_card)

                lib_limpio = limpiar_campo(librador_match.group(1) if librador_match else "")
                
                if (lib_limpio == "-" or 
                    len(lib_limpio) < 2 or 
                    "%" in lib_limpio or 
                    lib_limpio.replace(".", "").replace(",", "").isdigit() or 
                    "oferta" in lib_limpio.lower()):
                    continue

                valor = limpiar_campo(valor_match.group(1) if valor_match else "")
                vto = limpiar_campo(vto_match.group(1) if vto_match else "")
                solicitado = limpiar_campo(solic_match.group(1) if solic_match else "")
                margen = limpiar_campo(margen_match.group(1) if margen_match else "")
                tir = limpiar_campo(tir_match.group(1) if tir_match else "")
                bcu = limpiar_campo(bcu_match.group(1) if bcu_match else "")
                tipo = limpiar_campo(tipo_match.group(1) if tipo_match else "Cheque")

                # Valores del Modal por defecto
                det_banco = det_emision = det_vto = "-"
                det_razon = det_rut = det_dir = det_depto = det_ind = "-"
                det_cobrados = det_vigentes = det_desc = det_bancos = det_ventas = "-"

                # 2. Abrir el modal de detalles
                btn_modal = card.query_selector("text=/Detalles de la operación/i, text=/Detalle/i, button, a")
                if btn_modal:
                    try:
                        btn_modal.click(force=True)
                        page.wait_for_timeout(1500)

                        # Buscar la ventana flotante
                        modal = page.query_selector(".v-dialog--active, div[role='dialog'], .v-overlay__content")
                        if modal:
                            m_text = modal.inner_text()
                            
                            m_banco = re.search(r"Banco\s*\n?\s*([A-Za-záéíóúÁÉÍÓÚ\s]+)", m_text, re.IGNORECASE)
                            m_emi = re.search(r"Emisión\s*\n?\s*(\d{2}/\d{2}/\d{4})", m_text, re.IGNORECASE)
                            m_venc = re.search(r"Vencimiento\s*\n?\s*(\d{2}/\d{2}/\d{4})", m_text, re.IGNORECASE)
                            m_razon = re.search(r"Razón social\s*\n?\s*([^\n]+)", m_text, re.IGNORECASE)
                            m_rut = re.search(r"RUT\s*\n?\s*(\d{12})", m_text, re.IGNORECASE)
                            m_dir = re.search(r"Dirección\s*\n?\s*([^\n]+)", m_text, re.IGNORECASE)
                            m_depto = re.search(r"Departamento\s*\n?\s*([^\n]+)", m_text, re.IGNORECASE)
                            m_ind = re.search(r"Industria\s*\n?\s*([^\n]+)", m_text, re.IGNORECASE)
                            m_cob = re.search(r"Cobrados\s*\n?\s*([^\n]+)", m_text, re.IGNORECASE)
                            m_vig = re.search(r"Vigentes\s*\n?\s*([^\n]+)", m_text, re.IGNORECASE)
                            m_desc = re.search(r"Descontado vigente\s*\n?\s*([^\n]+)", m_text, re.IGNORECASE)
                            m_bman = re.search(r"Bancos que maneja\s*\n?\s*([^\n]+)", m_text, re.IGNORECASE)
                            m_ven = re.search(r"Ventas\s*\n?\s*(\d+)", m_text, re.IGNORECASE)

                            det_banco = limpiar_campo(m_banco.group(1) if m_banco else "")
                            det_emision = limpiar_campo(m_emi.group(1) if m_emi else "")
                            det_vto = limpiar_campo(m_venc.group(1) if m_venc else "")
                            det_razon = limpiar_campo(m_razon.group(1) if m_razon else "")
                            det_rut = limpiar_campo(m_rut.group(1) if m_rut else "")
                            det_dir = limpiar_campo(m_dir.group(1) if m_dir else "")
                            det_depto = limpiar_campo(m_depto.group(1) if m_depto else "")
                            det_ind = limpiar_campo(m_ind.group(1) if m_ind else "")
                            det_cobrados = limpiar_campo(m_cob.group(1) if m_cob else "")
                            det_vigentes = limpiar_campo(m_vig.group(1) if m_vig else "")
                            det_desc = limpiar_campo(m_desc.group(1) if m_desc else "")
                            det_bancos = limpiar_campo(m_bman.group(1) if m_bman else "")
                            det_ventas = limpiar_campo(m_ven.group(1) if m_ven else "")

                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                    except Exception:
                        page.keyboard.press("Escape")

                # Separar Dirección y Departamento si vienen juntos
                if "," in det_dir and det_depto == "-":
                    partes = det_dir.split(",")
                    det_dir = partes[0].strip()
                    det_depto = partes[1].strip()

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
                    "cobradoUSD": "-",
                    "bancosATransferir": "-",
                    "mejorOferta": "-",
                    "detValor": valor,
                    "detBanco": det_banco,
                    "detEmision": det_emision,
                    "detVencimiento": det_vto if det_vto != "-" else vto,
                    "detNombre": lib_limpio,
                    "detRazonSocial": det_razon,
                    "detRut": det_rut,
                    "detDireccion": det_dir,
                    "detDepartamento": det_depto,
                    "detIndustria": det_ind,
                    "detCobrados": det_cobrados,
                    "detVigentes": det_vigentes,
                    "detDescontadoVigente": det_desc,
                    "detBancosManeja": det_bancos,
                    "detVentas": det_ventas
                }

                if item["librador"] != "-" and item["valor"] != "-":
                    detalles_capturados.append(item)

            except Exception:
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
