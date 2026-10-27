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
    "cobrado usd", "bancos a transferir", "bancos que maneja"
]

def limpiar_campo(val, default="-"):
    if not val:
        return default
    v = val.strip()
    if v.lower() in TITULOS_IGNORAR or "la mejor oferta" in v.lower():
        return default
    return v

def extraer_oportunidades_completas(page, moneda):
    detalles_capturados = []
    page.wait_for_timeout(3000)

    # 1. Expandir paneles con clics en flechas o texto TIR
    try:
        page.evaluate("""() => {
            const btns = document.querySelectorAll('*');
            btns.forEach(b => {
                const txt = (b.innerText || '').toLowerCase();
                if (txt.includes('tir') || txt.includes('%') || (b.className && typeof b.className === 'string' && (b.className.includes('chevron') || b.className.includes('arrow')))) {
                    try { b.click(); } catch(e){}
                }
            });
        }""")
    except Exception:
        pass
    page.wait_for_timeout(2500)

    # 2. Lectura garantizada por texto de la página
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

                # Evitar títulos o cabeceras
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

                for j in range(i+1, min(i+25, len(lineas))):
                    sub = lineas[j]
                    sub_low = sub.lower()
                    
                    if re.match(r"^[\d\.\,]{3,15}$", sub) and valor == "-": valor = sub
                    elif ("en " in sub_low and "días" in sub_low or re.match(r"^\d{2}/\d{2}/\d{4}$", sub)) and vto == "-": vto = sub
                    elif re.match(r"^[\d\.\,]{3,15}$", sub) and valor != "-" and solicitado == "-": solicitado = sub
                    elif re.match(r"^[\d\.\,]{2,10}$", sub) and solicitado != "-" and margen == "-": margen = sub
                    elif ("%" in sub or re.match(r"^\d{1,2}[\,\.]\d{1,2}$", sub)) and tir == "-": tir = sub
                    elif sub_low == "bcu" and bcu == "-":
                        for k in range(j-1, max(i-1, j-4), -1):
                            if re.match(r"^[1-5][A-Za-z]?$", lineas[k]):
                                bcu = lineas[k]
                                break
                    elif sub_low == "cobrado usd" and cobrado_usd == "-":
                        for k in range(j-1, max(i-1, j-4), -1):
                            if re.match(r"^[\d\.\,]+$", lineas[k]) and lineas[k] != valor:
                                cobrado_usd = lineas[k]
                                break
                    elif sub_low == "bancos a transferir" and bancos_transferir == "-":
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
                
                # Evitar registros vacíos o duplicados en el mismo array
                if item["librador"] != "-" and item["valor"] != "-":
                    ya_existe = any(x["librador"] == item["librador"] and x["valor"] == item["valor"] for x in detalles_capturados)
                    if not ya_existe:
                        detalles_capturados.append(item)
            except Exception:
                pass
        i += 1

    print(f"[{moneda}] Oportunidades base encontradas: {len(detalles_capturados)}")

    # 3. Interacción con Tooltips y Modales
    try:
        botones_detalle = page.locator("button:has-text('Detalle'), a:has-text('Detalle'), .v-btn:has-text('Detalles')").all()
        print(f"[{moneda}] Botones de detalle encontrados: {len(botones_detalle)}")

        for b_idx, btn in enumerate(botones_detalle):
            try:
                if not btn.is_visible():
                    continue

                btn.scroll_into_view_if_needed()

                # Hover en tooltips
                m_bps, m_dgi, m_inf, m_rel = "-", "-", "-", "-"
                try:
                    contenedor = btn.locator("xpath=./ancestor::div[contains(@class, 'v-expansion-panel') or contains(@class, 'v-card') or contains(@class, 'card')]").first
                    if contenedor.is_visible():
                        for tag, var_name in [("BPS", "bps"), ("DGI", "dgi"), ("Inforcheck", "inf"), ("Relación", "rel")]:
                            elem = contenedor.locator(f"text={tag}").first
                            if elem.is_visible():
                                elem.hover(force=True)
                                page.wait_for_timeout(300)
                                tip = page.locator(".v-tooltip__content:visible, div[role='tooltip']:visible").last
                                if tip.is_visible():
                                    t_txt = tip.inner_text().strip()
                                    if var_name == "bps": m_bps = t_txt
                                    elif var_name == "dgi": m_dgi = t_txt
                                    elif var_name == "inf": m_inf = t_txt
                                    elif var_name == "rel": m_rel = t_txt
                                page.mouse.move(0, 0)
                except Exception:
                    pass

                # Clic en modal
                btn.click(force=True)
                page.wait_for_timeout(1000)

                modal = page.locator(".v-dialog--active:visible, div[role='dialog']:visible").first
                if modal.is_visible():
                    texto_m = modal.inner_text()
                    lineas_m = [l.strip() for l in texto_m.split("\n") if l.strip() != ""]
                    
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
                        elif "departamento" in l_low and idx_m+1 < len(lineas_m): m_depto = lineas_m[idx_m+1]
                        elif "industria" in l_low and idx_m+1 < len(lineas_m): m_ind = lineas_m[idx_m+1]
                        elif "cobrados" in l_low and idx_m+1 < len(lineas_m): m_cob = lineas_m[idx_m+1]
                        elif "vigentes" in l_low and idx_m+1 < len(lineas_m): m_vig = lineas_m[idx_m+1]
                        elif "descontado" in l_low and idx_m+1 < len(lineas_m): m_desc = lineas_m[idx_m+1]
                        elif "maneja" in l_low and idx_m+1 < len(lineas_m): m_bman = lineas_m[idx_m+1]
                        elif "ventas" in l_low and idx_m+1 < len(lineas_m): m_ven = lineas_m[idx_m+1]

                    # Cruzar datos con el ítem correspondiente
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
                page.wait_for_timeout(400)
            except Exception:
                page.keyboard.press("Escape")
    except Exception as e:
        print(f"Error procesando modales: {e}")

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
        print("Extrayendo oportunidades en Pesos...")
        detalles_uy = extraer_oportunidades_completas(page, "UY$")
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
            print("Extrayendo oportunidades en Dólares...")
            detalles_usd = extraer_oportunidades_completas(page, "USD")
            todos_detalles.extend(detalles_usd)
        except Exception as e:
            print(f"Error al cambiar a USD: {e}")

        browser.close()

    if todos_detalles:
        print(f"Enviando {len(todos_detalles)} registros únicos a la planilla de Oportunidades...")
        requests.post(WEBHOOK_URL, json={"detalles": todos_detalles})
    else:
        print("No se encontraron oportunidades.")

if __name__ == "__main__":
    ejecutar()
