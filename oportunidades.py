import os
import re
import requests
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_OPORTUNIDADES_URL") or os.environ.get("GOOGLE_SHEET_ID")

URL_LOGIN = "https://micheque.uy/login"
URL_MARKET = "https://micheque.uy/market"

TITULOS_IGNORAR = [
    "librador", "valor", "vencimiento", "solicitado", "margen neto", 
    "tir neta", "tipo", "bcu", "detalles de la operación", "comprar", "ofertar",
    "disponible para ofertas", "filtrar por", "banco a transferir", "la mejor oferta",
    "cobrado usd", "bancos a transferir", "bancos que maneja", "ver detalle", "detalles"
]

def limpiar_campo(val, default="-"):
    if not val:
        return default
    v = val.strip()
    if v.lower() in TITULOS_IGNORAR or "la mejor oferta" in v.lower():
        return default
    return v

def procesar_oportunidades_pantalla(page, moneda):
    detalles_capturados = []
    page.wait_for_timeout(3000)

    # 1. Forzar apertura de todos los paneles/tarjetas colapsadas vía JS
    print(f"[{moneda}] Expandiendo tarjetas...")
    page.evaluate("""() => {
        // Clic en todos los botones con chevrons, flechas o encabezados de expansión
        const headers = document.querySelectorAll('.v-expansion-panel-header, button i.mdi-chevron-down, button i.mdi-arrow-down, [aria-expanded="false"]');
        headers.forEach(h => {
            try { h.click(); } catch(e){}
        });
        // Clic en elementos que contengan texto como TIR o % si no estaban abiertos
        const allElements = document.querySelectorAll('*');
        allElements.forEach(el => {
            if (el.className && typeof el.className === 'string' && el.className.includes('expansion-panel-header')) {
                try { el.click(); } catch(e){}
            }
        });
    }""")
    page.wait_for_timeout(2500)

    # 2. Lectura secuencial de las operaciones visibles
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

                # Ventana de búsqueda extendida hacia adelante
                limite_j = min(i + 45, len(lineas))
                for j in range(i + 1, limite_j):
                    sub = lineas[j]
                    sub_low = sub.lower()
                    
                    if re.match(r"^[\d\.\,]{3,15}$", sub) and valor == "-": valor = sub
                    elif ("en " in sub_low and "días" in sub_low or re.match(r"^\d{2}/\d{2}/\d{4}$", sub)) and vto == "-": vto = sub
                    elif re.match(r"^[\d\.\,]{3,15}$", sub) and valor != "-" and solicitado == "-": solicitado = sub
                    elif re.match(r"^[\d\.\,]{2,10}$", sub) and solicitado != "-" and margen == "-": margen = sub
                    elif ("%" in sub or re.match(r"^\d{1,2}[\,\.]\d{1,2}$", sub)) and tir == "-": tir = sub
                    
                    # BCU: buscar valores 1, 1A, 2, 2A, 3, 4, 5
                    elif sub_low == "bcu" and bcu == "-":
                        for k in range(j-1, max(i-1, j-4), -1):
                            if re.match(r"^[1-5][A-Za-z]?$", lineas[k]):
                                bcu = lineas[k]
                                break
                    # Cobrado USD
                    elif "cobrado usd" in sub_low and cobrado_usd == "-":
                        for k in range(j-1, max(i-1, j-4), -1):
                            if re.match(r"^[\d\.\,]+$", lineas[k]) and lineas[k] != valor:
                                cobrado_usd = lineas[k]
                                break
                    # Bancos a transferir
                    elif "bancos a transferir" in sub_low and bancos_transferir == "-":
                        for k in range(j-1, max(i-1, j-4), -1):
                            cand = lineas[k]
                            if len(cand) > 1 and cand.lower() not in TITULOS_IGNORAR and not re.match(r"^[\d\.\,]+$", cand):
                                bancos_transferir = cand
                                break

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

    print(f"[{moneda}] Oportunidades base leídas: {len(detalles_capturados)}")

    # 3. Extraer Tooltips (BPS, DGI, Inforcheck) pasando el cursor por encima
    try:
        for tag in ["BPS", "DGI", "Inforcheck", "Relación"]:
            elementos_tag = page.locator(f"text={tag}").all()
            for el in elementos_tag:
                try:
                    if el.is_visible():
                        el.hover(force=True)
                        page.wait_for_timeout(250)
                except Exception:
                    pass
    except Exception:
        pass

    # 4. Modales de información detallada de la empresa
    try:
        # Detectar botones de detalle por texto o icono
        selectores_botones = [
            "button:has-text('Detalle')",
            "button:has-text('Ver detalle')",
            "a:has-text('Detalle')",
            ".v-btn:has(.mdi-information)",
            ".v-btn:has(.mdi-eye)",
            "button:has(i.mdi-information-outline)"
        ]
        
        botones = []
        for sel in selectores_botones:
            found = page.locator(sel).all()
            for f in found:
                if f.is_visible() and f not in botones:
                    botones.append(f)

        print(f"[{moneda}] Botones de detalle interactivos encontrados: {len(botones)}")

        for btn in botones:
            try:
                btn.scroll_into_view_if_needed()
                btn.click(force=True)
                page.wait_for_timeout(1000)

                # Buscar el diálogo activo
                dialog = page.locator(".v-dialog--active, div[role='dialog']").first
                if dialog.is_visible():
                    texto_d = dialog.inner_text()
                    lines_d = [ld.strip() for ld in texto_d.split("\n") if ld.strip() != ""]

                    m_nom, m_banco, m_emi, m_razon = "-", "-", "-", "-"
                    m_rut, m_dir, m_depto, m_ind = "-", "-", "-", "-"
                    m_cob, m_vig, m_desc, m_bman, m_ven = "-", "-", "-", "-"

                    for idx_m, lm in enumerate(lines_d):
                        l_low = lm.lower()
                        if l_low == "nombre" and idx_m+1 < len(lines_d): m_nom = lines_d[idx_m+1]
                        elif l_low == "banco" and idx_m+1 < len(lines_d): m_banco = lines_d[idx_m+1]
                        elif "emisión" in l_low and idx_m+1 < len(lines_d): m_emi = lines_d[idx_m+1]
                        elif "razón social" in l_low and idx_m+1 < len(lines_d): m_razon = lines_d[idx_m+1]
                        elif l_low == "rut" and idx_m+1 < len(lines_d): m_rut = lines_d[idx_m+1]
                        elif "dirección" in l_low and idx_m+1 < len(lines_d): m_dir = lines_d[idx_m+1]
                        elif "departamento" in l_low and idx_m+1 < len(lines_d): m_depto = lines_d[idx_m+1]
                        elif "industria" in l_low and idx_m+1 < len(lines_d): m_ind = lines_d[idx_m+1]
                        elif "cobrados" in l_low and idx_m+1 < len(lines_d): m_cob = lines_d[idx_m+1]
                        elif "vigentes" in l_low and idx_m+1 < len(lines_d): m_vig = lines_d[idx_m+1]
                        elif "descontado" in l_low and idx_m+1 < len(lines_d): m_desc = lines_d[idx_m+1]
                        elif "maneja" in l_low and idx_m+1 < len(lines_d): m_bman = lines_d[idx_m+1]
                        elif "ventas" in l_low and idx_m+1 < len(lines_d): m_ven = lines_d[idx_m+1]

                    # Asociar los datos del modal al registro correspondiente
                    for item in detalles_capturados:
                        # Si no tiene RUT asignado aún y coincide el nombre
                        if item.get("detRut") == "-" and m_nom != "-" and (m_nom.lower() in item['librador'].lower() or item['librador'].lower() in m_nom.lower()):
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
                page.wait_for_timeout(400)
            except Exception:
                page.keyboard.press("Escape")
    except Exception as e:
        print(f"Aviso modales: {e}")

    return detalles_capturados

def ejecutar():
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

        print("Accediendo al mercado...")
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(5000)

        # 1. Pesos
        print("Buscando oportunidades en Pesos...")
        detalles_uy = procesar_oportunidades_pantalla(page, "UY$")
        todos_detalles.extend(detalles_uy)

        # 2. Dólares
        print("Cambiando a Dólares...")
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

            page.wait_for_timeout(4000)
            print("Buscando oportunidades en Dólares...")
            detalles_usd = procesar_oportunidades_pantalla(page, "USD")
            todos_detalles.extend(detalles_usd)
        except Exception as e:
            print(f"Error al cambiar a USD: {e}")

        browser.close()

    if todos_detalles:
        print(f"Enviando {len(todos_detalles)} oportunidades al webhook...")
        resp = requests.post(WEBHOOK_URL, json={"detalles": todos_detalles})
        print(f"Respuesta Sheets: {resp.text}")
    else:
        print("No se encontraron oportunidades en esta corrida.")

if __name__ == "__main__":
    ejecutar()
