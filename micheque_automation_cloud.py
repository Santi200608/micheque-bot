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
    "disponible para ofertas", "filtrar por", "banco a transferir", "la mejor oferta",
    "cobrado usd", "seleccione...", "seleccione", "cheque", "factura", "transferencia programada"
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

def extraer_tooltip(tarjeta, page, texto_buscar):
    try:
        el = tarjeta.locator(f"xpath=.//*[contains(text(), '{texto_buscar}')]").first
        if el.is_visible():
            el.hover(force=True)
            page.wait_for_timeout(500) # Espera a que el globo negro se despliegue
            tooltip = page.locator(".v-tooltip__content:visible").last
            if tooltip.is_visible():
                valor = tooltip.inner_text().strip()
                page.mouse.move(0, 0) # Corre el mouse para cerrar el globo
                page.wait_for_timeout(200)
                return valor
    except Exception:
        pass
    return "-"

def extraer_detalles_desplegados(page, moneda):
    detalles_capturados = []
    try:
        page.wait_for_timeout(3000)
        
        # ==========================================================
        # FASE 1: ABRIR FLECHAS Y LEER BASE
        # ==========================================================
        try:
            flechas = page.locator(".v-expansion-panel-header, .v-expansion-panel-title, i.mdi-chevron-down, button").all()
            for flecha in flechas:
                try:
                    if flecha.is_visible():
                        txt = (flecha.inner_text() or "").lower()
                        if "comprar" not in txt and "ofertar" not in txt:
                            flecha.click(force=True)
                except Exception:
                    pass
        except Exception:
            pass
            
        page.wait_for_timeout(2500)

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

                    if (lib_limpio == "-" or 
                        len(lib_limpio) < 2 or 
                        "%" in lib_limpio or 
                        re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", lib_limpio) or 
                        lib_limpio.replace(".", "").replace(",", "").isdigit() or
                        lib_limpio.lower() in ["seleccione...", "cheque", "factura", "transferencia programada"]):
                        i += 1
                        continue

                    valor = "-"
                    vto = "-"
                    solicitado = "-"
                    margen = "-"
                    tir = "-"
                    
                    for j in range(i+1, min(i+16, len(lineas))):
                        sub = lineas[j]
                        sub_lower = sub.lower()
                        if re.match(r"^[\d\.\,]{3,15}$", sub) and valor == "-": valor = sub
                        elif ("en " in sub_lower and "días" in sub_lower or re.match(r"^\d{2}/\d{2}/\d{4}$", sub)) and vto == "-": vto = sub
                        elif re.match(r"^[\d\.\,]{3,15}$", sub) and valor != "-" and solicitado == "-": solicitado = sub
                        elif re.match(r"^[\d\.\,]{2,10}$", sub) and solicitado != "-" and margen == "-": margen = sub
                        elif ("%" in sub or re.match(r"^\d{1,2}[\,\.]\d{1,2}$", sub)) and tir == "-": tir = sub

                    bcu = "-"
                    cobrado_usd = "-"
                    bancos_transferir = "-"
                    bloque_25_lineas = " \n ".join(lineas[i:min(i+25, len(lineas))])
                    
                    m_bcu = re.search(r"([0-9][A-Za-z]?)\s*\n\s*bcu", bloque_25_lineas, re.IGNORECASE)
                    if m_bcu: bcu = m_bcu.group(1).strip()
                    
                    m_cob = re.search(r"([\d\.\,]+)\s*\n\s*cobrado usd", bloque_25_lineas, re.IGNORECASE)
                    if m_cob: cobrado_usd = m_cob.group(1).strip()
                    
                    m_ban = re.search(r"([A-Za-z\s]+)\s*\n\s*bancos a transferir", bloque_25_lineas, re.IGNORECASE)
                    if m_ban: 
                        c = m_ban.group(1).strip()
                        if len(c) > 2 and c.lower() not in TITULOS_IGNORAR:
                            bancos_transferir = c

                    # Estructura alineada exactamente de la A a la AF
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
                        "bps": "-",
                        "dgi": "-",
                        "inforcheck": "-",
                        "relacionComercial": "-",
                        "mejorOferta": "-",
                        "detValor": valor,
                        "detBanco": "-",
                        "detEmision": "-",
                        "detVencimiento": vto if re.match(r"^\d{2}/\d{2}/\d{4}$", vto) else "-",
                        "detNombre": lib_limpio,
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
                    if item["librador"] != "-" and item["valor"] != "-":
                        detalles_capturados.append(item)
                except Exception:
                    pass
            i += 1

        # ==========================================================
        # FASE 2: CLIC EN MODALES Y HOVER (BPS, DGI, etc)
        # ==========================================================
        try:
            botones_detalle = page.locator("text=/Detalles de la operación/i, text=/Ver detalle/i").all()
            for btn in botones_detalle:
                try:
                    if not btn.is_visible():
                        continue

                    # Extraer Tooltips flotantes antes de abrir el modal
                    m_bps, m_dgi, m_inf, m_rel = "-", "-", "-", "-"
                    try:
                        tarjeta = btn.locator("xpath=ancestor::*[contains(@class, 'v-expansion-panel')]").first
                        if tarjeta.is_visible():
                            m_bps = extraer_tooltip(tarjeta, page, "BPS")
                            m_dgi = extraer_tooltip(tarjeta, page, "DGI")
                            m_inf = extraer_tooltip(tarjeta, page, "Inforcheck")
                            m_rel = extraer_tooltip(tarjeta, page, "Relación comercial")
                    except Exception:
                        pass

                    # Abrir Modal
                    btn.click(force=True)
                    page.wait_for_timeout(1000)

                    modal = page.locator(".v-dialog--active, .v-overlay__content").first
                    if modal.is_visible():
                        texto_modal = modal.inner_text()
                        lineas_m = [l.strip() for l in texto_modal.split("\n") if l.strip() != ""]
                        
                        m_nom, m_banco, m_emi, m_razon = "-", "-", "-", "-"
                        m_rut, m_dir, m_depto, m_ind = "-", "-", "-", "-"
                        m_cob, m_vig, m_desc, m_bman, m_ven = "-", "-", "-", "-", "-"

                        for idx, lm in enumerate(lineas_m):
                            l_low = lm.lower()
                            if l_low == "nombre" and idx+1 < len(lineas_m): m_nom = lineas_m[idx+1]
                            elif l_low == "banco" and idx+1 < len(lineas_m): m_banco = lineas_m[idx+1]
                            elif "emisión" in l_low and idx+1 < len(lineas_m): m_emi = lineas_m[idx+1]
                            elif "razón social" in l_low and idx+1 < len(lineas_m): m_razon = lineas_m[idx+1]
                            elif l_low == "rut" and idx+1 < len(lineas_m): m_rut = lineas_m[idx+1]
                            elif "dirección" in l_low and idx+1 < len(lineas_m): m_dir = lineas_m[idx+1]
                            elif l_low == "departamento" and idx+1 < len(lineas_m): m_depto = lineas_m[idx+1]
                            elif l_low == "industria" and idx+1 < len(lineas_m): m_ind = lineas_m[idx+1]
                            elif "cobrados" in l_low and idx+1 < len(lineas_m): m_cob = lineas_m[idx+1]
                            elif "vigentes" in l_low and idx+1 < len(lineas_m): m_vig = lineas_m[idx+1]
                            elif "descontado" in l_low and idx+1 < len(lineas_m): m_desc = lineas_m[idx+1]
                            elif "maneja" in l_low and idx+1 < len(lineas_m): m_bman = lineas_m[idx+1]
                            elif l_low == "ventas" and idx+1 < len(lineas_m): m_ven = lineas_m[idx+1]

                        # Inyectar toda la data en la fila correspondiente
                        for item in detalles_capturados:
                            if m_nom != "-" and (m_nom.lower() in item['librador'].lower() or item['librador'].lower() in m_nom.lower()):
                                item['bps'] = limpiar_campo(m_bps)
                                item['dgi'] = limpiar_campo(m_dgi)
                                item['inforcheck'] = limpiar_campo(m_inf)
                                item['relacionComercial'] = limpiar_campo(m_rel)
                                item['detBanco'] = limpiar_campo(m_banco)
                                item['detEmision'] = limpiar_campo(m_emi)
                                item['detRazonSocial'] = limpiar_campo(m_razon)
                                item['detRut'] = limpiar_campo(m_rut)
                                item['detDireccion'] = limpiar_campo(m_dir)
                                item['detDepartamento'] = limpiar_campo(m_depto)
                                item['detIndustria'] = limpiar_campo(m_ind)
                                item['detCobrados'] = limpiar_campo(m_cob)
                                item['detVigentes'] = limpiar_campo(m_vig)
                                item['detDescontadoVigente'] = limpiar_campo(m_desc)
                                item['detBancosManeja'] = limpiar_campo(m_bman)
                                item['detVentas'] = limpiar_campo(m_ven)
                                break

                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                except Exception:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
        except Exception as e:
            print(f"Error en modales: {e}")

    except Exception as e:
        print(f"Error general en extracción de detalles: {e}")

    return detalles_capturados

def obtener_transacciones():
    todas_transacciones = []
    todos_detalles = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        
        print("Iniciando sesión en MiCheque...")
        page.goto(URL_LOGIN, wait_until="networkidle")
        page.wait_for_timeout(2000)
        
        page.fill("input[type='email'], input[name='email'], input[type='text']", USERNAME)
        page.fill("input[type='password'], input[name='password']", PASSWORD)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)
        
        print("Navegando al mercado...")
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(5000)

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        print("Extrayendo sección Pesos...")
        texto_pesos = page.inner_text("body")
        pesos = procesar_texto_seccion(texto_pesos, "UY$")
        todas_transacciones.extend(pesos)
        
        detalles_uy = extraer_detalles_desplegados(page, "UY$")
        todos_detalles.extend(detalles_uy)

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

def eliminar_duplicados(lista):
    vistos = set()
    resultado = []
    for item in lista:
        lib = item.get('librador') or item.get('Librador', '')
        val = item.get('valor') or item.get('Monto', '')
        moneda = item.get('moneda') or item.get('Moneda', '')
        firma_unica = f"{lib}_{val}_{moneda}"
        
        if firma_unica not in vistos:
            vistos.add(firma_unica)
            resultado.append(item)
    return resultado

def ejecutar():
    nuevas, detalles = obtener_transacciones()
    
    nuevas_limpias = eliminar_duplicados(nuevas)
    detalles_limpios = eliminar_duplicados(detalles)
    
    if nuevas_limpias:
        print(f"Enviando {len(nuevas_limpias)} transacciones históricas a Google Sheets...")
        requests.post(WEBHOOK_URL, json={"transacciones": nuevas_limpias})
    
    if detalles_limpios:
        print(f"Enviando {len(detalles_limpios)} detalles de oportunidades a Google Sheets...")
        requests.post(WEBHOOK_URL, json={"tipo_evento": "detalles_oportunidades", "detalles": detalles_limpios})

if __name__ == "__main__":
    ejecutar()
