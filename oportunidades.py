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

def procesar_tarjetas_oportunidad(page, moneda):
    detalles = []
    
    # 1. Expandir todas las tarjetas primero
    try:
        expand_buttons = page.locator(".v-expansion-panel-header, button:has(.mdi-chevron-down), .v-expansion-panel-title").all()
        for b in expand_buttons:
            try:
                b.click(timeout=1000)
                page.wait_for_timeout(200)
            except Exception:
                pass
    except Exception:
        pass

    page.wait_for_timeout(2000)

    # 2. Localizar tarjetas o paneles de operaciones
    tarjetas = page.locator(".v-expansion-panel, .v-card, div[role='region']").all()
    
    # Si no detecta con esos contenedores, busca por bloques que tengan "TIR" o "Margen"
    if not tarjetas:
        tarjetas = page.locator("div:has-text('TIR'):has-text('Valor')").all()

    print(f"[{moneda}] Contenedores encontrados: {len(tarjetas)}")

    for idx, card in enumerate(tarjetas):
        try:
            texto_card = card.inner_text()
            if not texto_card or ("cheque" not in texto_card.lower() and "factura" not in texto_card.lower() and "transferencia" not in texto_card.lower()):
                continue

            lineas = [l.strip() for l in texto_card.split("\n") if l.strip() != ""]

            # Extracción básica de columnas B a I
            librador, tipo, valor, vto, solicitado, margen, tir = "-", "-", "-", "-", "-", "-", "-"
            bcu, cobrado_usd, bancos_transferir = "-", "-", "-"

            for i, linea in enumerate(lineas):
                l_low = linea.lower()
                if l_low == "cheque": tipo = "Cheque"
                elif "transferencia programada" in l_low: tipo = "Transferencia Programada"
                elif l_low == "factura": tipo = "Factura"

                if (tipo != "-" and librador == "-" and i > 0):
                    cand_lib = limpiar_campo(lineas[i-1])
                    if cand_lib != "-" and not re.match(r"^\d", cand_lib) and "%" not in cand_lib:
                        librador = cand_lib

                if re.match(r"^[\d\.\,]{3,15}$", linea) and valor == "-": valor = linea
                elif ("en " in l_low and "días" in l_low) and vto == "-": vto = linea
                elif re.match(r"^[\d\.\,]{3,15}$", linea) and valor != "-" and solicitado == "-": solicitado = linea
                elif re.match(r"^[\d\.\,]{2,10}$", linea) and solicitado != "-" and margen == "-": margen = linea
                elif ("%" in linea or re.match(r"^\d{1,2}[\,\.]\d{1,2}$", linea)) and tir == "-": tir = linea

                # BCU, Cobrado USD, Bancos a transferir
                if l_low == "bcu" and bcu == "-":
                    for k in range(max(0, i-4), min(len(lineas), i+4)):
                        if re.match(r"^[1-5][A-Za-z]?$", lineas[k]):
                            bcu = lineas[k]
                            break
                elif "cobrado usd" in l_low and cobrado_usd == "-":
                    for k in range(max(0, i-2), min(len(lineas), i+3)):
                        if re.match(r"^[\d\.\,]+$", lineas[k]) and lineas[k] != valor:
                            cobrado_usd = lineas[k]
                            break
                elif "bancos a transferir" in l_low and bancos_transferir == "-":
                    for k in range(max(0, i-2), min(len(lineas), i+3)):
                        cand_b = lineas[k]
                        if cand_b.lower() not in TITULOS_IGNORAR and not re.match(r"^[\d\.\,]+$", cand_b):
                            bancos_transferir = cand_b
                            break

            if librador == "-" and len(lineas) > 0:
                librador = limpiar_campo(lineas[0])

            # Tooltips (hover) en BPS, DGI, Inforcheck, Relacion Comercial
            bps, dgi, inforcheck, rel_comercial = "-", "-", "-", "-"
            for tag_nombre, var_code in [("BPS", "bps"), ("DGI", "dgi"), ("Inforcheck", "inf"), ("Relación", "rel")]:
                try:
                    elem_tag = card.locator(f"text={tag_nombre}").first
                    if elem_tag.is_visible():
                        elem_tag.hover(force=True)
                        page.wait_for_timeout(300)
                        tip = page.locator(".v-tooltip__content:visible, div[role='tooltip']:visible").last
                        if tip.is_visible():
                            txt_tip = tip.inner_text().strip()
                            if var_code == "bps": bps = txt_tip
                            elif var_code == "dgi": dgi = txt_tip
                            elif var_code == "inf": inforcheck = txt_tip
                            elif var_code == "rel": rel_comercial = txt_tip
                        page.mouse.move(0, 0)
                except Exception:
                    pass

            # Modal de Detalles de la operación
            det_valor = valor
            det_banco, det_emision, det_vencimiento = "-", "-", vto
            det_nombre, det_razon, det_rut, det_dir = librador, "-", "-", "-"
            det_depto, det_ind = "-", "-"
            det_cob, det_vig, det_desc, det_bman, det_ven = "-", "-", "-", "-", "-"

            try:
                btn_modal = card.locator("button:has-text('Detalle'), a:has-text('Detalle'), .v-btn:has-text('Detalle')").first
                if not btn_modal.is_visible():
                    btn_modal = card.locator("button:has(.mdi-information), button:has(.mdi-eye)").first

                if btn_modal.is_visible():
                    btn_modal.scroll_into_view_if_needed()
                    btn_modal.click(force=True)
                    page.wait_for_timeout(1000)

                    modal = page.locator(".v-dialog--active, div[role='dialog']:visible").first
                    if modal.is_visible():
                        txt_m = modal.inner_text()
                        l_m = [lm.strip() for lm in txt_m.split("\n") if lm.strip() != ""]

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
            except Exception as e_mod:
                print(f"Error procesando modal en tarjeta {idx}: {e_mod}")
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

            if librador != "-" or valor != "-":
                detalles.append(item)

        except Exception as e_card:
            print(f"Error procesando tarjeta {idx}: {e_card}")

    return detalles

def ejecutar():
    todos_detalles = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})

        print("Iniciando sesión...")
        page.goto(URL_LOGIN, wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.fill("input[type='email'], input[name='email'], input[type='text']", USERNAME)
        page.fill("input[type='password'], input[name='password']", PASSWORD)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)

        print("Cargando mercado...")
        page.goto(URL_MARKET, wait_until="networkidle")
        page.wait_for_timeout(5000)

        # 1. Pesos
        print("Procesando Pesos...")
        detalles_uy = procesar_tarjetas_oportunidad(page, "UY$")
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
            print("Procesando Dólares...")
            detalles_usd = procesar_tarjetas_oportunidad(page, "USD")
            todos_detalles.extend(detalles_usd)
        except Exception as e:
            print(f"Error cambiando a USD: {e}")

        browser.close()

    if todos_detalles:
        print(f"Enviando {len(todos_detalles)} oportunidades a Sheets...")
        requests.post(WEBHOOK_URL, json={"detalles": todos_detalles})

if __name__ == "__main__":
    ejecutar()
