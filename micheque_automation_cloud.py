import os
import re
import requests
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_ID")

URL_LOGIN = "https://micheque.uy/login"
URL_MARKET = "https://micheque.uy/market"

TITULOS_A_IGNORAR = ["valor", "vencimiento", "solicitado", "margen neto", "tir neta", "nombre", "razón social", "rut", "dirección", "departamento", "industria", "cobrados", "vigentes", "descontado vigente", "bancos que maneja", "ventas"]

def limpiar_valor(texto, default=""):
    if not texto:
        return default
    txt = texto.strip()
    if txt.lower() in TITULOS_A_IGNORAR:
        return default
    return txt

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
        
        # 1. Expandir las filas/tarjetas principales
        page.evaluate("""() => {
            const elementos = document.querySelectorAll('*');
            elementos.forEach(el => {
                const txt = (el.innerText || '').toLowerCase();
                const cls = (el.className && typeof el.className === 'string') ? el.className.toLowerCase() : '';
                if (el.children.length === 0 && (txt.includes('detalles') || txt.includes('tir') || cls.includes('chevron') || cls.includes('arrow'))) {
                    try { el.click(); } catch(e){}
                }
            });
        }""")
        page.wait_for_timeout(1500)

        # 2. Localizar enlaces específicos de "Detalles de la operación"
        elementos_detalles = page.query_selector_all("a, button, span, div")
        elementos_validos = []
        for e in elementos_detalles:
            try:
                txt = (e.inner_text() or '').strip().lower()
                if "detalles de la operación" in txt or "detalle de la operacion" in txt:
                    if e.is_visible():
                        elementos_validos.append(e)
            except Exception:
                continue

        print(f"Se encontraron {len(elementos_validos)} enlaces de detalles para {moneda}")

        for i, elem in enumerate(elementos_validos):
            try:
                elem.click(force=True)
                page.wait_for_timeout(2000)

                # AISLAMIENTO: Obtener ÚNICAMENTE el texto del modal flotante activo
                modal_element = page.query_selector("div[role='dialog'], div.v-dialog, div.modal-content, .v-overlay__content")
                if modal_element:
                    modal_text = modal_element.inner_text()
                else:
                    modal_text = page.evaluate("() => document.body.innerText")

                # Extraer datos descartando palabras que sean títulos
                nom = re.search(r"Nombre\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                raz = re.search(r"Razón social\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                librador_val = limpiar_valor(nom.group(1) if nom else (raz.group(1) if raz else ""))

                # Si capturó un título genérico en lugar de la empresa, omitir
                if not librador_val or librador_val.lower() in TITULOS_A_IGNORAR:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                    continue

                monto_solic = re.search(r"Solicitado\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                val_m = re.search(r"Monto\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                vto_dias = re.search(r"Vencimiento\s*\n\s*(en \d+ días|[^\n]+)", modal_text, re.IGNORECASE)
                bcu_m = re.search(r"(\d[A-Z])\s*\n\s*BCU", modal_text, re.IGNORECASE)

                # Banco a transferir (filtrando los botones 'Comprar' u 'Ofertar')
                banco_trans = re.search(r"Bancos a transferir\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                banco_trans_val = limpiar_valor(banco_trans.group(1) if banco_trans else "-", "-")
                if banco_trans_val.lower() in ["comprar", "ofertar", ""]:
                    banco_trans_val = "-"

                cobrado_usd_m = re.search(r"Cobrada USD\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)

                # Modal: Detalles de la Operación (N a Q)
                det_val = re.search(r"Monto\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_banco = re.search(r"Banco\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_emision = re.search(r"Emisión\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_vto = re.search(r"Vencimiento\s*\n\s*(\d{2}/\d{2}/\d{4})", modal_text, re.IGNORECASE)

                # Modal: Datos del Librador (R a Z)
                det_nom = re.search(r"Nombre\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_raz = re.search(r"Razón social\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_rut = re.search(r"RUT\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)

                det_dir_full = re.search(r"Dirección\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                dir_txt = limpiar_valor(det_dir_full.group(1) if det_dir_full else "")
                depto_txt = ""
                if "," in dir_txt:
                    partes_dir = dir_txt.split(",")
                    dir_txt = partes_dir[0].strip()
                    depto_txt = partes_dir[1].strip()
                else:
                    det_depto = re.search(r"Departamento\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    depto_txt = limpiar_valor(det_depto.group(1) if det_depto else "")

                det_ind = re.search(r"Industria\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_cobrados = re.search(r"Cobrados\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_vigentes = re.search(r"Vigentes\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_desc = re.search(r"Descontado vigente\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)

                # Modal: Vendedor (AA a AB)
                det_bancos_man = re.search(r"Bancos que maneja\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_ventas = re.search(r"Ventas\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)

                item = {
                    "moneda": moneda,
                    "librador": librador_val,
                    "valor": limpiar_valor(val_m.group(1) if val_m else ""),
                    "vto": limpiar_valor(vto_dias.group(1) if vto_dias else ""),
                    "solicitado": limpiar_valor(monto_solic.group(1) if monto_solic else ""),
                    "margenNeto": "-",
                    "tirNeta": "-",
                    "tipo": "Transferencia Programada" if "Transferencia Programada" in modal_text else "Cheque",
                    "bcu": limpiar_valor(bcu_m.group(1) if bcu_m else ""),
                    "cobradoUSD": limpiar_valor(cobrado_usd_m.group(1) if cobrado_usd_m else "-", "-"),
                    "bancosATransferir": banco_trans_val,
                    "mejorOferta": "-",
                    "detValor": limpiar_valor(det_val.group(1) if det_val else ""),
                    "detBanco": limpiar_valor(det_banco.group(1) if det_banco else ""),
                    "detEmision": limpiar_valor(det_emision.group(1) if det_emision else ""),
                    "detVencimiento": limpiar_valor(det_vto.group(1) if det_vto else ""),
                    "detNombre": limpiar_valor(det_nom.group(1) if det_nom else ""),
                    "detRazonSocial": limpiar_valor(det_raz.group(1) if det_raz else ""),
                    "detRut": limpiar_valor(det_rut.group(1) if det_rut else ""),
                    "detDireccion": dir_txt,
                    "detDepartamento": depto_txt,
                    "detIndustria": limpiar_valor(det_ind.group(1) if det_ind else ""),
                    "detCobrados": limpiar_valor(det_cobrados.group(1) if det_cobrados else "-", "-"),
                    "detVigentes": limpiar_valor(det_vigentes.group(1) if det_vigentes else ""),
                    "detDescontadoVigente": limpiar_valor(det_desc.group(1) if det_desc else "-", "-"),
                    "detBancosManeja": limpiar_valor(det_bancos_man.group(1) if det_bancos_man else ""),
                    "detVentas": limpiar_valor(det_ventas.group(1) if det_ventas else "")
                }

                detalles_capturados.append(item)

                # Forzar el cierre efectivo del modal antes del siguiente
                btn_cerrar = page.query_selector("button.v-btn--icon, button.close, .modal-close")
                if btn_cerrar and btn_cerrar.is_visible():
                    btn_cerrar.click()
                else:
                    page.keyboard.press("Escape")
                page.wait_for_timeout(800)

            except Exception as ex:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
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
