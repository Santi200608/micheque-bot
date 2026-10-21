import os
import re
import requests
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_ID")

URL_LOGIN = "https://micheque.uy/login"
URL_MARKET = "https://micheque.uy/market"

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
        
        # Desplegar tarjetas principales (flechas C, I, J, D, E, L, etc.)
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

        # Buscar enlaces de "Detalles de la operación ->"
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

                modal_text = page.evaluate("() => document.body.innerText")

                # Extracción según tus letras A -> AB
                librador = re.search(r"(?:Librador|Nombre)\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                valor = re.search(r"Valor\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                vto = re.search(r"Vencimiento\s*\n\s*(en \d+ días|[^\n]+)", modal_text, re.IGNORECASE)
                solicitado = re.search(r"Solicitado\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                margen = re.search(r"Margen [Nn]eto\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                tir = re.search(r"TIR [Nn]eta\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                tipo = re.search(r"(Cheque|Transferencia Programada|Factura)", modal_text, re.IGNORECASE)
                bcu = re.search(r"(\d[A-Z])\s*\n\s*BCU", modal_text, re.IGNORECASE)
                cobrado_usd = re.search(r"Cobrado USD\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                bancos_trans = re.search(r"Bancos a transferir\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                mejor_oferta = re.search(r"(La mejor oferta es de[^\n]+)", modal_text, re.IGNORECASE)

                # Detalles de la operación (Modal N a Q)
                det_valor = re.search(r"Datos del (?:cheque|factura|documento)\s*\n\s*Valor\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                if not det_valor:
                    det_valor = re.search(r"Valor\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_banco = re.search(r"Banco\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_emision = re.search(r"Emisión\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_vencimiento = re.search(r"Vencimiento\s*\n\s*(\d{2}/\d{2}/\d{4})", modal_text, re.IGNORECASE)

                # Datos del Librador (Modal R a Z)
                det_nombre = re.search(r"Nombre\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_razon = re.search(r"Razón social\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_rut = re.search(r"RUT\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_dir = re.search(r"Dirección\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_depto = re.search(r"Departamento\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_ind = re.search(r"Industria\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_cobrados = re.search(r"Cobrados\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_vigentes = re.search(r"Vigentes\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_descontado = re.search(r"Descontado vigente\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)

                # Vendedor (Modal AA a AB)
                det_bancos_man = re.search(r"Bancos que maneja\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                det_ventas = re.search(r"Ventas\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)

                item = {
                    "moneda": moneda,
                    "librador": librador.group(1).strip() if librador else "",
                    "valor": valor.group(1).strip() if valor else "",
                    "vto": vto.group(1).strip() if vto else "",
                    "solicitado": solicitado.group(1).strip() if solicitado else "",
                    "margenNeto": margen.group(1).strip() if margen else "",
                    "tirNeta": tir.group(1).strip() if tir else "",
                    "tipo": tipo.group(1).strip() if tipo else "",
                    "bcu": bcu.group(1).strip() if bcu else "",
                    "cobradoUSD": cobrado_usd.group(1).strip() if cobrado_usd else "-",
                    "bancosATransferir": bancos_trans.group(1).strip() if bancos_trans else "",
                    "mejorOferta": mejor_oferta.group(1).strip() if mejor_oferta else "",
                    "detValor": det_valor.group(1).strip() if det_valor else "",
                    "detBanco": det_banco.group(1).strip() if det_banco else "",
                    "detEmision": det_emision.group(1).strip() if det_emision else "",
                    "detVencimiento": det_vencimiento.group(1).strip() if det_vencimiento else "",
                    "detNombre": det_nombre.group(1).strip() if det_nombre else "",
                    "detRazonSocial": det_razon.group(1).strip() if det_razon else "",
                    "detRut": det_rut.group(1).strip() if det_rut else "",
                    "detDireccion": det_dir.group(1).strip() if det_dir else "",
                    "detDepartamento": det_depto.group(1).strip() if det_depto else "",
                    "detIndustria": det_ind.group(1).strip() if det_ind else "",
                    "detCobrados": det_cobrados.group(1).strip() if det_cobrados else "-",
                    "detVigentes": det_vigentes.group(1).strip() if det_vigentes else "",
                    "detDescontadoVigente": det_descontado.group(1).strip() if det_descontado else "-",
                    "detBancosManeja": det_bancos_man.group(1).strip() if det_bancos_man else "",
                    "detVentas": det_ventas.group(1).strip() if det_ventas else ""
                }
                
                if item["librador"] or item["detRut"] or item["detRazonSocial"]:
                    detalles_capturados.append(item)

                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
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
