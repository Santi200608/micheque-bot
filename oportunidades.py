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

def desplegar_filas_una_a_una(page):
    """Abre secuencialmente cada panel o fila de oportunidad"""
    try:
        # Busca los encabezados de expansión o filas clickeables
        headers = page.locator(".v-expansion-panel-header, div[role='button']:has-text('%')")
        total = headers.count()
        print(f"Desplegando {total} tarjetas de operaciones...")
        for idx in range(total):
            try:
                h = headers.nth(idx)
                if h.is_visible():
                    # Si no está abierto, hacer clic
                    aria_exp = h.get_attribute("aria-expanded")
                    if aria_exp != "true":
                        h.scroll_into_view_if_needed()
                        h.click(force=True)
                        page.wait_for_timeout(400)
            except Exception:
                pass
    except Exception as e:
        print(f"Aviso desplegando filas: {e}")
    page.wait_for_timeout(1500)

def procesar_seccion(page, moneda):
    detalles = []
    page.wait_for_timeout(2000)

    # 1. Abrir tarjetas
    desplegar_filas_una_a_una(page)

    # 2. Lectura secuencial de las tarjetas en pantalla
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

                if not es_librador_valido(librador):
                    i += 1
                    continue

                lib_limpio = librador.strip()
                valor = "-"
                vto = "-"
                solicitado = "-"
                margen = "-"
                tir = "-"
                bcu = "-"
                cobrado_usd = "-"
                bancos_transferir = "-"

                for j in range(i+1, min(i+45, len(lineas))):
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
                            if len(cand) > 1 and cand.lower() not in PALABRAS_PROHIBIDAS_LIBRADOR and not re.match(r"^[\d\.\,]+$", cand):
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
                    detalles.append(item)
            except Exception:
                pass
        i += 1

    print(f"[{moneda}] Oportunidades encontradas: {len(detalles)}")

    # 3. Interacción con modales de detalles
    try:
        botones_detalle = page.locator("button:has-text('Detalles de la operación'), a:has-text('Detalles de la operación'), text=/Detalles de la operación/i").all()
        for btn in botones_detalle:
            try:
                if not btn.is_visible():
                    continue

                btn.scroll_into_view_if_needed()
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

                    for item in detalles:
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

    return detalles

def cambiar_a_dolares(page):
    """Fuerza el cambio a la pestaña de Dólares mediante JavaScript nativo"""
    print("Intentando cambiar a Dólares...")
    exito = page.evaluate("""() => {
        const elementos = Array.from(document.querySelectorAll('button, div, a, span'));
        for (const el of elementos) {
            const txt = (el.innerText || '').trim().toLowerCase();
            if (txt.includes('dólares') || txt.includes('dolares')) {
                // Si tiene borde o apariencia de botón lateral
                if (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button' || el.className.includes('btn') || el.className.includes('tab') || txt.includes('(')) {
                    el.click();
                    return true;
                }
            }
        }
        return false;
    }""")
    if not exito:
        # Fallback con Playwright directo
        btn = page.locator("text=/D[oó]lares/i").first
        if btn.is_visible():
            btn.click(force=True)
            exito = True

    page.wait_for_timeout(5000)
    return exito

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
        try:
            cambiar_a_dolares(page)
            print("5. Extrayendo oportunidades en Dólares...")
            detalles_usd = procesar_seccion(page, "USD")
            todos_detalles.extend(detalles_usd)
        except Exception as e:
            print(f"Error cambiando a Dólares: {e}")

        browser.close()

    print(f"Total de registros a enviar: {len(todos_detalles)}")
    if todos_detalles:
        try:
            resp = requests.post(WEBHOOK_URL, json={"detalles": todos_detalles}, timeout=30)
            print(f"Respuesta Sheets: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"Error al enviar datos: {e}")
    else:
        print("No se encontraron registros.")

if __name__ == "__main__":
    ejecutar()
