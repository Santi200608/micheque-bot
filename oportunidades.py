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
    "la mejor oferta", "cobrado usd", "bancos a transferir", "bancos que maneja"
]

def es_librador_valido(nombre):
    if not nombre:
        return False
    nom = nombre.strip().lower()
    if len(nom) < 3:
        return False
    if any(nom == p or nom.startswith("seleccione") for p in PALABRAS_PROHIBIDAS_LIBRADOR):
        return False
    if re.match(r"^\d", nom) or "%" in nom or re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", nom):
        return False
    return True

def procesar_oportunidades(page, moneda):
    detalles = []
    page.wait_for_timeout(3000)

    # 1. Localizar los contenedores de operaciones reales
    # En Vuetify los paneles de oportunidades son expansion-panels o tarjetas de mercado
    tarjetas = page.locator(".v-expansion-panel, .v-card:has-text('TIR'), tr:has-text('TIR')").all()
    print(f"[{moneda}] Contenedores encontrados: {len(tarjetas)}")

    for idx, card in enumerate(tarjetas):
        try:
            texto_inicial = (card.inner_text() or "").strip()
            if not texto_inicial or ("cheque" not in texto_inicial.lower() and "factura" not in texto_inicial.lower() and "transferencia" not in texto_inicial.lower()):
                continue

            # Desplegar tarjeta si está colapsada
            try:
                btn_abrir = card.locator(".v-expansion-panel-header, button:has(.mdi-chevron-down), .v-btn--icon").first
                if btn_abrir.is_visible():
                    aria_exp = btn_abrir.get_attribute("aria-expanded")
                    if aria_exp != "true":
                        btn_abrir.click(force=True)
                        page.wait_for_timeout(400)
            except Exception:
                pass

            # Texto completo con la tarjeta ya expandida
            texto_completo = card.inner_text()
            lineas = [l.strip() for l in texto_completo.split("\n") if l.strip() != ""]

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

                # BCU, Cobrado USD, Bancos
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

            # Si el librador quedó al principio de la tarjeta
            if librador == "-" and len(lineas) > 0 and es_librador_valido(lineas[0]):
                librador = lineas[0]

            # Si no es un librador válido, ignoramos esta fila fantasma
            if not es_librador_valido(librador):
                continue

            # Tooltips (BPS, DGI, Inforcheck) dentro de la tarjeta
            bps, dgi, inforcheck, rel_comercial = "-", "-", "-", "-"
            for tag_nombre, var_code in [("BPS", "bps"), ("DGI", "dgi"), ("Inforcheck", "inf"), ("Relación", "rel")]:
                try:
                    elem_tag = card.locator(f"text={tag_nombre}").first
                    if elem_tag.is_visible():
                        elem_tag.hover(force=True)
                        page.wait_for_timeout(250)
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

            # Modal de Detalles de la Operación
            det_valor = valor
            det_banco, det_emision, det_vencimiento = "-", "-", vto
            det_nombre, det_razon, det_rut, det_dir = librador, "-", "-", "-"
            det_depto, det_ind = "-", "-"
            det_cob, det_vig, det_desc, det_bman, det_ven = "-", "-", "-", "-", "-"

            try:
                btn_modal = card.locator("button:has-text('Detalle'), a:has-text('Detalle'), .v-btn:has-text('Ver')").first
                if not btn_modal.is_visible():
                    btn_modal = card.locator("button:has(.mdi-information), button:has(.mdi-eye)").first

                if btn_modal.is_visible():
                    btn_modal.scroll_into_view_if_needed()
                    btn_modal.click(force=True)
                    page.wait_for_timeout(1000)

                    modal = page.locator(".v-dialog--active:visible, div[role='dialog']:visible").first
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
            except Exception:
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

        except Exception as e_card:
            print(f"Aviso en tarjeta {idx}: {e_card}")

    return detalles

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
        detalles_uy = procesar_oportunidades(page, "UY$")
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
            detalles_usd = procesar_oportunidades(page, "USD")
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
