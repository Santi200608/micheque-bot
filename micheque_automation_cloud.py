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

# ==========================================================
# EXTRACCIÓN HISTÓRICA (MOTOR ORIGINAL)
# ==========================================================
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

# ==========================================================
# EXTRACCIÓN DE OPORTUNIDADES (LECTURA SECUENCIAL Y TOTAL)
# ==========================================================
def extraer_detalles_desplegados(page, moneda):
    detalles_capturados = []
    try:
        page.wait_for_timeout(3000)
        
        # 1. Desplegar los datos de la vista previa de todas las tarjetas
        page.evaluate("""() => {
            const btns = document.querySelectorAll('.v-expansion-panel-header, .v-expansion-panel-title, *');
            btns.forEach(b => {
                const txt = (b.innerText || '').toLowerCase();
                if (txt.includes('tir') || txt.includes('%') || (b.className && typeof b.className === 'string' && (b.className.includes('chevron') || b.className.includes('arrow')))) {
                    try { b.click(); } catch(e){}
                }
            });
        }""")
        page.wait_for_timeout(2000)

        # 2. Iterar tarjeta por tarjeta de forma estricta
        tarjetas = page.locator(".v-expansion-panel").all()
        
        for tarjeta in tarjetas:
            try:
                if not tarjeta.is_visible():
                    continue
                    
                texto_tarjeta = tarjeta.inner_text()
                lineas = [l.strip() for l in texto_tarjeta.split("\n") if l.strip() != ""]
                
                if len(lineas) < 5:
                    continue
                
                # BLOQUE A-I: Extracción Base
                librador = lineas[0]
                if librador.lower() in TITULOS_IGNORAR:
                    librador = lineas[1] if len(lineas) > 1 else "-"
                
                lib_limpio = limpiar_campo(librador)
                
                if (lib_limpio == "-" or 
                    len(lib_limpio) < 2 or 
                    "%" in lib_limpio or 
                    re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", lib_limpio) or 
                    lib_limpio.replace(".", "").replace(",", "").isdigit()):
                    continue

                valor = "-"
                vto = "-"
                solicitado = "-"
                margen = "-"
                tir = "-"
                bcu = "-"
                cobrado_usd = "-"
                bancos_transferir = "-"
                tipo = "Cheque"

                for idx, sub in enumerate(lineas):
                    sub_lower = sub.lower()
                    if sub_lower in ["cheque", "factura", "transferencia programada"]: tipo = sub.title()
                    elif re.match(r"^[\d\.\,]{3,15}$", sub) and valor == "-": valor = sub
                    elif ("en " in sub_lower and "días" in sub_lower or re.match(r"^\d{2}/\d{2}/\d{4}$", sub)) and vto == "-": vto = sub
                    elif re.match(r"^[\d\.\,]{3,15}$", sub) and valor != "-" and solicitado == "-": solicitado = sub
                    elif re.match(r"^[\d\.\,]{2,10}$", sub) and solicitado != "-" and margen == "-": margen = sub
                    elif ("%" in sub or re.match(r"^\d{1,2}[\,\.]\d{1,2}$", sub)) and tir == "-": tir = sub
                    
                    # BLOQUE J-M: Busca la etiqueta y lee HACIA ARRIBA
                    elif sub_lower == "bcu" and bcu == "-":
                        for k in range(idx-1, max(-1, idx-4), -1):
                            if re.match(r"^\d[A-Za-z]?$", lineas[k]):
                                bcu = lineas[k]
                                break
                    elif sub_lower == "cobrado usd" and cobrado_usd == "-":
                        for k in range(idx-1, max(-1, idx-4), -1):
                            if re.match(r"^[\d\.\,]+$", lineas[k]):
                                cobrado_usd = lineas[k]
                                break
                    elif sub_lower == "bancos a transferir" and bancos_transferir == "-":
                        for k in range(idx-1, max(-1, idx-4), -1):
                            cand = lineas[k]
                            if len(cand) > 1 and cand.lower() not in TITULOS_IGNORAR and not re.match(r"^[\d\.\,]+$", cand):
                                bancos_transferir = cand
                                break

                # BLOQUE N-AB: Apertura del Modal en ESTA tarjeta específica
                detBanco = "-"
                detEmision = "-"
                detVencimiento = vto if re.match(r"^\d{2}/\d{2}/\d{4}$", vto) else "-"
                detRazonSocial = "-"
                detRut = "-"
                detDireccion = "-"
                detDepartamento = "-"
                detIndustria = "-"
                detCobrados = "-"
                detVigentes = "-"
                detDescontadoVigente = "-"
                detBancosManeja = "-"
                detVentas = "-"

                try:
                    btn_modal = tarjeta.locator("button, a, div").filter(has_text=re.compile(r"detalles de la operaci[oó]n|ver detalle", re.IGNORECASE)).first
                    if btn_modal.is_visible():
                        btn_modal.click(force=True)
                        page.wait_for_timeout(1500)
                        
                        modal = page.locator(".v-dialog--active, .v-overlay__content").first
                        if modal.is_visible():
                            texto_modal = modal.inner_text()
                            lineas_m = [l.strip() for l in texto_modal.split("\n") if l.strip() != ""]
                            
                            # En el modal, el título está ARRIBA y el valor ABAJO
                            for idx_m, lm in enumerate(lineas_m):
                                l_low = lm.lower()
                                if l_low == "banco" and idx_m+1 < len(lineas_m): detBanco = limpiar_campo(lineas_m[idx_m+1])
                                elif l_low in ["emisión", "emision"] and idx_m+1 < len(lineas_m): detEmision = limpiar_campo(lineas_m[idx_m+1])
                                elif l_low == "vencimiento" and idx_m+1 < len(lineas_m): 
                                    v_cand = limpiar_campo(lineas_m[idx_m+1])
                                    if re.match(r"^\d{2}/\d{2}/\d{4}$", v_cand): detVencimiento = v_cand
                                elif l_low in ["razón social", "razon social"] and idx_m+1 < len(lineas_m): detRazonSocial = limpiar_campo(lineas_m[idx_m+1])
                                elif l_low == "rut" and idx_m+1 < len(lineas_m): detRut = limpiar_campo(lineas_m[idx_m+1])
                                elif l_low in ["dirección", "direccion"] and idx_m+1 < len(lineas_m): detDireccion = limpiar_campo(lineas_m[idx_m+1])
                                elif l_low == "departamento" and idx_m+1 < len(lineas_m): detDepartamento = limpiar_campo(lineas_m[idx_m+1])
                                elif l_low == "industria" and idx_m+1 < len(lineas_m): detIndustria = limpiar_campo(lineas_m[idx_m+1])
                                elif "cobrados" in l_low and idx_m+1 < len(lineas_m): detCobrados = limpiar_campo(lineas_m[idx_m+1])
                                elif "vigentes" in l_low and idx_m+1 < len(lineas_m): detVigentes = limpiar_campo(lineas_m[idx_m+1])
                                elif "descontado vigente" in l_low and idx_m+1 < len(lineas_m): detDescontadoVigente = limpiar_campo(lineas_m[idx_m+1])
                                elif l_low == "bancos que maneja" and idx_m+1 < len(lineas_m): detBancosManeja = limpiar_campo(lineas_m[idx_m+1])
                                elif l_low == "ventas" and idx_m+1 < len(lineas_m): detVentas = limpiar_campo(lineas_m[idx_m+1])

                        page.keyboard.press("Escape")
                        page.wait_for_timeout(500)
                except Exception:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)

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
                    "mejorOferta": "-",
                    "detValor": valor,
                    "detBanco": detBanco,
                    "detEmision": detEmision,
                    "detVencimiento": detVencimiento,
                    "detNombre": lib_limpio,
                    "detRazonSocial": detRazonSocial,
                    "detRut": detRut,
                    "detDireccion": detDireccion,
                    "detDepartamento": detDepartamento,
                    "detIndustria": detIndustria,
                    "detCobrados": detCobrados,
                    "detVigentes": detVigentes,
                    "detDescontadoVigente": detDescontadoVigente,
                    "detBancosManeja": detBancosManeja,
                    "detVentas": detVentas
                }
                
                if item["librador"] != "-" and item["valor"] != "-":
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
