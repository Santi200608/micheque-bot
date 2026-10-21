import os
import re
import requests
from playwright.sync_api import sync_playwright

USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_ID")

URL_LOGIN = "https://micheque.uy/login"
URL_MARKET = "https://micheque.uy/market"

TITULOS_A_IGNORAR = ["valor", "vencimiento", "solicitado", "margen neto", "tir neta", "nombre", "razón social", "rut", "dirección", "departamento", "industria", "cobrados", "vigentes", "descontado vigente", "bancos que maneja", "ventas", "detalles de la operación"]

def limpiar(txt, default=""):
    if not txt:
        return default
    t = txt.strip()
    if t.lower() in TITULOS_A_IGNORAR:
        return default
    return t

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
        
        # 1. Forzar la apertura de todos los desplegables via JS
        page.evaluate("""() => {
            const elms = document.querySelectorAll('*');
            elms.forEach(el => {
                if (el.children.length === 0) {
                    const txt = (el.innerText || '').toLowerCase();
                    if (txt.includes('detalles') || txt.includes('tir') || txt.includes('vencimiento')) {
                        try { el.click(); } catch(e){}
                    }
                }
            });
        }""")
        page.wait_for_timeout(2000)

        # 2. Extraer todos los bloques de texto de los modales/tarjetas presentes en el DOM
        bloques_texto = page.evaluate("""() => {
            const resultados = [];
            
            // Buscar todos los botones o enlaces que digan 'Detalles de la operación'
            const links = Array.from(document.querySelectorAll('*')).filter(el => 
                el.children.length === 0 && (el.innerText || '').toLowerCase().includes('detalles de la operación')
            );

            links.forEach(link => {
                try {
                    link.click();
                } catch(e){}
            });

            // Retornar los textos de los modales y tarjetas
            const modales = document.querySelectorAll('.v-dialog, div[role="dialog"], .modal-content, .v-expansion-panel-text, .v-card');
            modales.forEach(m => {
                if (m.innerText && m.innerText.length > 50) {
                    resultados.push(m.innerText);
                }
            });

            if (resultados.length === 0) {
                resultados.push(document.body.innerText);
            }

            return resultados;
        }""")

        print(f"Bloques de contenido capturados para {moneda}: {len(bloques_texto)}")

        for modal_text in bloques_texto:
            try:
                librador_m = re.search(r"(?:Nombre|Deudor|Librador)\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                razon_m = re.search(r"Razón social\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                rut_m = re.search(r"RUT\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                bcu_m = re.search(r"(\d[A-Z])\s*\n\s*BCU", modal_text, re.IGNORECASE)
                
                val_m = re.search(r"Valor\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                if not val_m:
                    val_m = re.search(r"Monto\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)

                solic_m = re.search(r"Solicitado\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                banco_m = re.search(r"Banco\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                emision_m = re.search(r"Emisión\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                vto_m = re.search(r"Vencimiento\s*\n\s*(\d{2}/\d{2}/\d{4}|en \d+ días|[^\n]+)", modal_text, re.IGNORECASE)

                dir_m = re.search(r"Dirección\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                dir_txt = limpiar(dir_m.group(1) if dir_m else "")
                depto_txt = ""
                if "," in dir_txt:
                    partes = dir_txt.split(",")
                    dir_txt = partes[0].strip()
                    depto_txt = partes[1].strip()
                else:
                    depto_m = re.search(r"Departamento\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                    depto_txt = limpiar(depto_m.group(1) if depto_m else "")

                ind_m = re.search(r"Industria\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                cob_m = re.search(r"Cobrados\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                vig_m = re.search(r"Vigentes\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                desc_m = re.search(r"Descontado vigente\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                
                bman_m = re.search(r"Bancos que maneja\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)
                ven_m = re.search(r"Ventas\s*\n\s*([^\n]+)", modal_text, re.IGNORECASE)

                lib_nombre = limpiar(librador_m.group(1) if librador_m else "")
                lib_razon = limpiar(razon_m.group(1) if razon_m else "")
                librador_final = lib_nombre if lib_nombre else lib_razon

                if not librador_final or librador_final.lower() in TITULOS_A_IGNORAR:
                    continue

                item = {
                    "moneda": moneda,
                    "librador": librador_final,
                    "valor": limpiar(val_m.group(1) if val_m else ""),
                    "vto": limpiar(vto_m.group(1) if vto_m else ""),
                    "solicitado": limpiar(solic_m.group(1) if solic_m else ""),
                    "margenNeto": "-",
                    "tirNeta": "-",
                    "tipo": "Transferencia Programada" if "Transferencia Programada" in modal_text else "Cheque",
                    "bcu": limpiar(bcu_m.group(1) if bcu_m else ""),
                    "cobradoUSD": "-",
                    "bancosATransferir": limpiar(banco_m.group(1) if banco_m else "-", "-"),
                    "mejorOferta": "-",
                    "detValor": limpiar(val_m.group(1) if val_m else ""),
                    "detBanco": limpiar(banco_m.group(1) if banco_m else ""),
                    "detEmision": limpiar(emision_m.group(1) if emision_m else ""),
                    "detVencimiento": limpiar(vto_m.group(1) if vto_m else ""),
                    "detNombre": lib_nombre,
                    "detRazonSocial": lib_razon,
                    "detRut": limpiar(rut_m.group(1) if rut_m else ""),
                    "detDireccion": dir_txt,
                    "detDepartamento": depto_txt,
                    "detIndustria": limpiar(ind_m.group(1) if ind_m else ""),
                    "detCobrados": limpiar(cob_m.group(1) if cob_m else "-", "-"),
                    "detVigentes": limpiar(vig_m.group(1) if vig_m else ""),
                    "detDescontadoVigente": limpiar(desc_m.group(1) if desc_m else "-", "-"),
                    "detBancosManeja": limpiar(bman_m.group(1) if bman_m else ""),
                    "detVentas": limpiar(ven_m.group(1) if ven_m else "")
                }

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
