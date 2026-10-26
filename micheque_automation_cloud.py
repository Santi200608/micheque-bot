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
    "cobrado usd", "bancos a transferir", "bancos que maneja"
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
            page.wait_for_timeout(400)
            tooltip = page.locator(".v-tooltip__content:visible, .tooltip:visible").last
            if tooltip.is_visible():
                txt = tooltip.inner_text().strip()
                page.mouse.move(0, 0)
                page.wait_for_timeout(150)
                return txt
    except Exception:
        pass
    page.mouse.move(0, 0)
    return "-"

def extraer_detalles_desplegados(page, moneda):
    detalles_capturados = []
    try:
        page.wait_for_timeout(3000)
        
        # 1. Desplegar información extendida en pantalla (TU LÓGICA BASE)
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

        # 2. TU MOTOR BASE INTOCABLE (Columnas A - J)
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

                    # Filtro estricto original
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

                    for j in range(i+1, min(i+14, len(lineas))):
                        sub = lineas[j]
                        if re.match(r"^[\d\.\,]{3,15}$", sub) and valor == "-":
                            valor = sub
                        elif ("en " in sub.lower() and "días" in sub.lower() or re.match(r"^\d{2}/\d{2}/\d{4}$", sub)) and vto == "-":
                            vto = sub
                        elif re.match(r"^[\d\.\,]{3,15}$", sub) and valor != "-" and solicitado == "-":
                            solicitado = sub
                        elif re.match(r"^[\d\.\,]{2,10}$", sub) and solicitado != "-" and margen == "-":
                            margen = sub
                        elif ("%" in sub or re.match(r"^\d{1,2}[\,\.]\d{1,2}$", sub)) and tir == "-":
                            tir = sub
                        elif re.match(r"^\d[A-Z]$", sub) and bcu == "-":
                            bcu = sub

                    # 3. EXTRACCIÓN J-L (Cobrado USD y Bancos a transferir)
                    cobrado_usd = "-"
                    bancos_transferir = "-"
                    bloque_texto = " \n ".join(lineas[i:min(i+25, len(lineas))])

                    m_cob = re.search(r"([\d\.\,]+)\s*\n\s*cobrado usd", bloque_texto, re.IGNORECASE)
                    if m_cob:
                        cobrado_usd = m_cob.group(1).strip()

                    m_ban = re.search(r"([A-Za-z\s]+)\s*\n\s*bancos? a transferir", bloque_texto, re.IGNORECASE)
                    if m_ban:
                        cand = m_ban.group(1).strip()
                        if len(cand) > 1 and cand.lower() not in TITULOS_IGNORAR:
                            bancos_transferir = cand

                    # Si el BCU no entró en el bucle original, intento de respaldo
                    if bcu == "-":
                        m_bcu = re.search(r"([0-9][A-Za-z]?)\s*\n\s*bcu", bloque_texto, re.IGNORECASE)
                        if m_bcu:
                            bcu = m_bcu.group(1).strip()

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

        # 4. HOVER (BPS, DGI, Inforcheck, Relación comercial) + MODALES (Q a AF)
        try:
            botones_detalle = page.locator("text=/Detalles de la operación/i, text=/Ver detalle/i").all()
            for btn in botones_detalle:
                try:
                    if not btn.is_visible():
                        continue

                    # Extraer tooltips desde el contenedor de la tarjeta
                    m_bps, m_dgi, m_inf, m_rel = "-", "-", "-", "-"
                    try:
                        tarjeta = btn.locator("xpath=ancestor::*[contains(@class, 'v-expansion-panel') or contains(@class, 'card') or contains(@class, 'panel')]").first
                        if tarjeta.is_visible():
                            m_bps = extraer_tooltip(tarjeta, page, "BPS")
                            m_dgi = extraer_tooltip(tarjeta, page, "DGI")
                            m_inf = extraer_tooltip(tarjeta, page, "Inforcheck")
                            m_rel = extraer_tooltip(tarjeta, page, "Relación comercial")
                    except Exception:
                        pass

                    btn.click(force=True)
                    page.wait_for_timeout(1000)

                    modal = page.locator(".v-dialog--active, .v-overlay__content").first
                    if modal.is_visible():
                        texto_modal = modal.inner_text()
                        lineas_m = [l.strip() for l in texto_modal.split("\n") if l.strip() != ""]

                        m_nom, m_banco, m_emi, m_razon = "-", "-", "-", "-"
                        m_rut, m_dir, m_depto, m_ind = "-", "-", "-", "-"
                        m_cob, m_vig, m_desc, m_bman, m_ven = "-", "-", "-", "-", "-"

                        for idx_m, lm in enumerate(lineas_m):
                            l_low = lm.lower()
                            if l_low == "nombre" and idx_m+1 < len(lineas_m): m_nom = lineas_m[idx_m+1]
                            elif l_low == "banco" and idx_m+1 < len(lineas_m): m_banco = lineas_m[idx_m+1]
                            elif "emisión" in l_low and idx_m+1 < len(lineas_m): m_emi = lineas_m[idx_m+1]
                            elif "razón social" in l_low and idx_m+1 < len(lineas_m): m_razon = lineas_m[idx_m+1]
                            elif l_low == "rut" and idx_m+1 < len(lineas_m): m_rut = lineas_m[idx_m+1]
                            elif "dirección" in l_low and idx_m+1 < len(lineas_m): m_dir = lineas_m[idx_m+1]
                            elif l_low == "departamento" and idx_m+1 < len(lineas_m): m_depto = lineas_m[idx_m+1]
                            elif l_low == "industria" and idx_m+1 < len(lineas_m): m_ind = lineas_m[idx_m+1]
                            elif "cobrados" in l_low and idx_m+1 < len(lineas_m): m_cob = lineas_m[idx_m+1]
                            elif "vigentes" in l_low and idx_m+1 < len(lineas_m): m_vig = lineas_m[idx_m+1]
                            elif "descontado" in l_low and idx_m+1 < len(lineas_m): m_desc = lineas_m[idx_m+1]
                            elif "maneja" in l_low and idx_m+1 < len(lineas_m): m_bman = lineas_m[idx_m+1]
                            elif l_low == "ventas" and idx_m+1 < len(lineas_m): m_ven = lineas_m[idx_m+1]

                        # Asignar a la tarjeta correspondiente
                        for item in detalles_capturados:
                            if m_nom != "-" and (m_nom.lower() in item['librador'].lower() or item['librador'].lower() in m_nom.lower()):
                                if m_bps != "-": item['bps'] = limpiar_campo(m_bps)
                                if m_dgi != "-": item['dgi'] = limpiar_campo(m_dgi)
                                if m_inf != "-": item['inforcheck'] = limpiar_campo(m_inf)
                                if m_rel != "-": item['relacionComercial'] = limpiar_campo(m_rel)
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
            print(f"Error en enriquecimiento de modales: {e}")

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
