import os
import re
import requests
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_OPORTUNIDADES_URL") or os.environ.get("GOOGLE_SHEET_ID")

URL_LOGIN = "https://micheque.uy/login"
URL_MARKET = "https://micheque.uy/market"

PALABRAS_PROHIBIDAS_LIBRADOR = [
    "seleccione", "seleccione...", "cheque", "factura", "transferencia", 
    "transferencia programada", "librador", "valor", "vencimiento", "solicitado", 
    "margen neto", "tir neta", "tipo", "bcu", "detalles de la operación", "comprar", 
    "ofertar", "disponible para ofertas", "filtrar por", "todos", "moneda", "banco",
    "la mejor oferta", "cobrado usd", "bancos a transferir", "bancos que maneja",
    "pesos uruguayos", "dólares", "dolares"
]

def es_librador_valido(nombre):
    if not nombre:
        return False
    nom = nombre.strip().lower()
    if len(nom) < 3:
        return False
    if any(nom == p or nom.startswith("seleccione") or nom.startswith("pesos") or nom.startswith("dólar") for p in PALABRAS_PROHIBIDAS_LIBRADOR):
        return False
    if re.match(r"^\d", nom) or "%" in nom or re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", nom):
        return False
    return True

def limpiar_campo(val, default="-"):
    if not val:
        return default
    v = val.strip()
    if v.lower() in PALABRAS_PROHIBIDAS_LIBRADOR or "la mejor oferta" in v.lower():
        return default
    return v

def procesar_seccion(page, moneda):
    detalles = []
    page.wait_for_timeout(2500)

    # 1. Identificar cada tarjeta/panel de oportunidad en la lista
    tarjetas = page.locator(".v-expansion-panel").all()
    if not tarjetas:
        tarjetas = page.locator("div.v-card:has-text('%'), div:has-text('TIR neta'):has(button)").all()

    print(f"[{moneda}] Total de tarjetas detectadas: {len(tarjetas)}")

    for idx, card in enumerate(tarjetas):
        try:
            # Asegurar scroll a la tarjeta
            card.scroll_into_view_if_needed()

            # Desplegar la tarjeta con el chevron o encabezado
            btn_expand = card.locator(".v-expansion-panel-header, button:has(.mdi-chevron-down), button:has(i[class*='chevron'])").first
            if btn_expand.is_visible():
                btn_expand.click(force=True)
                page.wait_for_timeout(600)

            texto_card = card.inner_text()
            lineas = [l.strip() for l in texto_card.split("\n") if l.strip() != ""]

            tipo, librador, valor, vto, solicitado, margen, tir = "-", "-", "-", "-", "-", "-", "-"
            bcu, cobrado_usd, bancos_transferir = "-", "-", "-"

            for i, linea in enumerate(lineas):
                l_low = linea.lower()
                if l_low == "cheque": tipo = "Cheque"
                elif "transferencia programada" in l_low: tipo = "Transferencia Programada"
                elif l_low == "factura": tipo = "Factura"

                if tipo != "-" and librador == "-" and i > 0:
                    cand = lineas[i-1].strip()
                    if es_librador_valido(cand):
                        librador = cand

                if re.match(r"^[\d\.\,]{3,15}$", linea) and valor == "-": valor = linea
                elif ("en " in l_low and "días" in l_low) and vto == "-": vto = linea
                elif re.match(r"^[\d\.\,]{3,15}$", linea) and valor != "-" and solicitado == "-": solicitado = linea
                elif re.match(r"^[\d\.\,]{2,10}$", linea) and solicitado != "-" and margen == "-": margen = linea
                elif ("%" in linea or re.match(r"^\d{1,2}[\,\.]\d{1,2}$", linea)) and tir == "-": tir = linea

                # BCU, Cobrado USD, Bancos a transferir
                if l_low == "bcu" and bcu == "-":
                    for k in range(max(0, i-4), min(len(lineas), i+5)):
                        if re.match(r"^[1-5][A-Za-z]?$", lineas[k]):
                            bcu = lineas[k]
                            break
                elif "cobrado usd" in l_low and cobrado_usd == "-":
                    for k in range(max(0, i-3), min(len(lineas), i+5)):
                        if re.match(r"^[\d\.\,]+$", lineas[k]) and lineas[k] != valor:
                            cobrado_usd = lineas[k]
                            break
                elif "bancos a transferir" in l_low and bancos_transferir == "-":
                    for k in range(max(0, i-3), min(len(lineas), i+5)):
                        cand_b = lineas[k]
                        if cand_b.lower() not in PALABRAS_PROHIBIDAS_LIBRADOR and not re.match(r"^[\d\.\,]+$", cand_b):
                            bancos_transferir = cand_b
                            break

            if librador == "-" and len(lineas) > 0 and es_librador_valido(lineas[0]):
                librador = lineas[0]

            if not es_librador_valido(librador) or valor == "-":
                continue

            # Tooltips de BPS, DGI, Inforcheck
            bps, dgi, inforcheck, rel_comercial = "-", "-", "-", "-"
            for tag_nombre, var_code in [("BPS", "bps"), ("DGI", "dgi"), ("Inforcheck", "inf"), ("Relación", "rel")]:
                try:
                    elem_tag = card.locator(f"text={tag_nombre}").first
                    if elem_tag.is_visible():
                        elem_tag.hover(force=True)
                        page.wait_for_timeout(200)
                        tip = page.locator(".v-tooltip__content:visible").last
                        if tip.is_visible():
                            txt_tip = tip.inner_text().strip()
                            if var_code == "bps": bps = txt_tip
                            elif var_code == "dgi": dgi = txt_tip
                            elif var_code == "inf": inforcheck = txt_tip
                            elif var_code == "rel": rel_comercial = txt_tip
                        page.mouse.move(0, 0)
                except Exception:
                    pass

            # Modal: Clic directo en "Detalles de la operación"
            det_valor = valor
            det_banco, det_emision, det_vencimiento = "-", "-", vto
            det_nombre, det_razon, det_rut, det_dir = librador, "-", "-", "-"
            det_depto, det_ind = "-", "-"
            det_cob, det_vig, det_desc, det_bman, det_ven = "-", "-", "-", "-", "-"

            try:
                btn_modal = card.locator("text=/Detalles de la operación/i, button:has-text('Detalles')").first
                if btn_modal.is_visible():
                    btn_modal.scroll_into_view_if_needed()
                    btn_modal.click(force=True)
                    page.wait_for_timeout(1000)

                    modal = page.locator(".v-dialog--active:visible, div[role='dialog']:visible").first
                    if modal.is_visible():
                        texto_m = modal.inner_text()
                        l_m = [lm.strip() for lm in texto_m.split("\n") if lm.strip() != ""]

                        for im, lm in enumerate(l_m):
                            lm_low = lm.lower()
                            if lm_low == "nombre" and im+1 < len(l_m): det_nombre = l_m[im+1]
                            elif lm_low == "banco" and im+1 < len(l_m): det_banco = l_m[im+1]
                            elif "emisión" in lm_low and im+1 < len(l_m): det_emision = l_m[im+1]
                            elif "vencimiento" in lm_low and im+1 < len(l_m): det_vencimiento = l_m[im+1]
                            elif "razón social" in lm_low and im+1 < len(l_m): det_razon = l_m[im+1]
                            elif lm_low == "rut" and im+1 < len(l_m): det_rut = l_m[im+1]
                            elif "dirección" in lm_low and im+1 < len(l_m): det_dir = l_m[im+1]
                            elif "departamento" in lm_low and im+1 < len(l_m): det_depto = l_m[im+1]
                            elif "industria" in lm_low and im+1 < len(l_m): det_ind = l_m[im+1]
                            elif "cobrados" in lm_low and im+1 < len(l_m): det_cob = l_m[im+1]
                            elif "vigentes" in lm_low and im+1 < len(l_m): det_vig = l_m[im+1]
                            elif "descontado" in lm_low and im+1 < len(l_m): det_desc = l_m[im+1]
                            elif "maneja" in lm_low and im+1 < len(l_m): det_bman = l_m[im+1]
                            elif "ventas" in lm_low and im+1 < len(l_m): det_ven = l_m[im+1]

                    page.keyboard.press("Escape")
                    page.wait_for_timeout(400)
            except Exception as e_m:
                print(f"Aviso modal tarjeta {idx}: {e_m}")
                page.keyboard.press("Escape")

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
                "cobradoUSD": cobrado_usd,
                "bancosATransferir": bancos_transferir,
                "bps": bps,
                "dgi": dgi,
                "inforcheck": inforcheck,
                "relacionComercial": rel_comercial,
                "mejorOferta": "-",
                "detValor": det_valor,
                "detBanco": det_banco,
                "detEmision": det_emision,
                "detVencimiento": det_vencimiento,
                "detNombre": det_nombre,
                "detRazonSocial": det_razon,
                "detRut": det_rut,
                "detDireccion": det_dir,
                "detDepartamento": det_depto,
                "detIndustria": det_ind,
                "detCobrados": det_cob,
                "detVigentes": det_vig,
                "detDescontadoVigente": det_desc,
                "detBancosManeja": det_bman,
                "detVentas": det_ven
            }

            detalles.append(item)

        except Exception as e_c:
            print(f"Error procesando tarjeta {idx}: {e_c}")

    return detalles

def cambiar_a_dolares(page):
    """Hace clic en la pestaña de Dólares y espera que la grilla cambie de contenido"""
    print("Cambiando pestaña a Dólares...")
    try:
        # Selector enfocado en el botón/pestaña lateral izquierda
        tab_dolares = page.locator(".v-tab, .v-btn, [role='tab'], div").filter(has_text=re.compile(r"^D[oó]lares", re.IGNORECASE)).first
        if tab_dolares.is_visible():
            tab_dolares.click(force=True)
        else:
            page.evaluate("""() => {
                const tabs = Array.from(document.querySelectorAll('*'));
                for (const t of tabs) {
                    const txt = (t.innerText || '').trim().toLowerCase();
                    if (txt.startsWith('dólares') || txt.startsWith('dolares')) {
                        t.click();
                        break;
                    }
                }
            }""")

        page.wait_for_timeout(5000)
    except Exception as e:
        print(f"Error en cambiar_a_dolares: {e}")

def ejecutar():
    todos_detalles = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900}
        )
        page = context.new_page()

        print("1. Iniciando sesión...")
        page.goto(URL_LOGIN, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)
        
        inputs_email = page.locator("input[type='email'], input[name='email'], input[type='text']")
        if inputs_email.count() > 0:
            inputs_email.first.fill(USERNAME)
        
        inputs_pass = page.locator("input[type='password'], input[name='password']")
        if inputs_pass.count() > 0:
            inputs_pass.first.fill(PASSWORD)
            
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)

        print("2. Accediendo al Mercado...")
        page.goto(URL_MARKET, wait_until="load", timeout=60000)
        page.wait_for_timeout(6000)

        # 1. Pesos uruguayos
        print("3. Extrayendo oportunidades en Pesos uruguayos...")
        detalles_uy = procesar_seccion(page, "UY$")
        todos_detalles.extend(detalles_uy)

        # 2. Dólares
        print("4. Cambiando a Dólares...")
        cambiar_a_dolares(page)
        print("5. Extrayendo oportunidades en Dólares...")
        detalles_usd = procesar_seccion(page, "USD")
        todos_detalles.extend(detalles_usd)

        browser.close()

    print(f"Total de registros listos para enviar: {len(todos_detalles)}")
    if todos_detalles:
        try:
            resp = requests.post(WEBHOOK_URL, json={"detalles": todos_detalles}, timeout=30)
            print(f"Respuesta de Google Sheets: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"Error al enviar datos: {e}")
    else:
        print("No se encontraron registros.")

if __name__ == "__main__":
    ejecutar()
