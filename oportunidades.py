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

def procesar_seccion(page, moneda):
    detalles = []
    page.wait_for_timeout(3000)

    # Diagnóstico: ver qué URL y qué texto general ve el robot
    url_actual = page.url
    print(f"[{moneda}] URL actual: {url_actual}")
    
    body_text = page.inner_text("body")
    lineas = [l.strip() for l in body_text.split("\n") if l.strip() != ""]
    print(f"[{moneda}] Total de líneas de texto leídas en la página: {len(lineas)}")

    # Imprimir un extracto si hay pocas líneas (indica que no pasó el login o no cargó)
    if len(lineas) < 30:
        print(f"[{moneda}] AVISO: El contenido parece vacío o bloqueado. Texto visible:\n" + "\n".join(lineas[:15]))
        return detalles

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

                for j in range(i+1, min(i+40, len(lineas))):
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

    print(f"[{moneda}] Oportunidades válidas extraídas: {len(detalles)}")
    return detalles

def ejecutar():
    todos_detalles = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900}
        )
        page = context.new_page()

        print("1. Accediendo al Login...")
        page.goto(URL_LOGIN, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)
        
        # Llenar credenciales
        inputs_email = page.locator("input[type='email'], input[name='email'], input[type='text']")
        if inputs_email.count() > 0:
            inputs_email.first.fill(USERNAME)
        
        inputs_pass = page.locator("input[type='password'], input[name='password']")
        if inputs_pass.count() > 0:
            inputs_pass.first.fill(PASSWORD)
            
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)

        print("2. Navegando al Mercado...")
        page.goto(URL_MARKET, wait_until="load", timeout=60000)
        page.wait_for_timeout(6000)

        # 1. Pesos
        print("3. Extrayendo Oportunidades en Pesos...")
        detalles_uy = procesar_seccion(page, "UY$")
        todos_detalles.extend(detalles_uy)

        # 2. Dólares
        print("4. Cambiando a Dólares...")
        try:
            btn_usd = page.locator("button:has-text('Dólares'), div:has-text('Dólares'), a:has-text('Dólares')").first
            if btn_usd.is_visible():
                btn_usd.click(force=True)
                page.wait_for_timeout(4000)
                detalles_usd = procesar_seccion(page, "USD")
                todos_detalles.extend(detalles_usd)
            else:
                print("No se encontró el botón directo de Dólares visible.")
        except Exception as e:
            print(f"Aviso al alternar moneda: {e}")

        browser.close()

    print(f"Total de registros listos para enviar: {len(todos_detalles)}")
    if todos_detalles:
        try:
            resp = requests.post(WEBHOOK_URL, json={"detalles": todos_detalles}, timeout=30)
            print(f"Respuesta de Google Sheets: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"Error al enviar datos al Webhook: {e}")
    else:
        print("No se enviaron datos porque no se detectaron operaciones.")

if __name__ == "__main__":
    ejecutar()
