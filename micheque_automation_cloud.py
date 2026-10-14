import os
import json
import time
import pandas as pd
from playwright.sync_api import sync_playwright
import gspread
from google.oauth2.service_account import Credentials

# Cargar variables desde los Secrets de GitHub
USERNAME = os.environ.get("MICHEQUE_USER")
PASSWORD = os.environ.get("MICHEQUE_PASS")
SPREADSHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS")

URL_LOGIN = "https://micheque.uy/login"
URL_MARKET = "https://micheque.uy/market"

def conectar_google_sheets():
    creds_dict = json.loads(CREDS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(credentials)
    sh = gc.open_by_key(SPREADSHEET_ID)
    return sh.worksheet("Datos sin TIR Neta")

def obtener_transacciones():
    transacciones = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL_LOGIN)
        
        page.fill("input[type='email'], input[name='email']", USERNAME)
        page.fill("input[type='password'], input[name='password']", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_timeout(3000)
        
        page.goto(URL_MARKET)
        page.wait_for_timeout(3000)

        # Transacciones en Pesos
        btn_pesos = page.query_selector("button:has-text('Pesos uruguayos'), div:has-text('Pesos uruguayos')")
        if btn_pesos:
            btn_pesos.click()
            page.wait_for_timeout(1500)
        
        filas = page.query_selector_all("table tbody tr")
        for f in filas:
            cols = [c.inner_text().strip() for c in f.query_selector_all("td")]
            if len(cols) >= 5:
                transacciones.append([cols[0], cols[1], cols[2], cols[3], cols[4], "UY$"])

        # Transacciones en Dólares
        btn_usd = page.query_selector("button:has-text('Dólares'), div:has-text('Dólares')")
        if btn_usd:
            btn_usd.click()
            page.wait_for_timeout(1500)
        
        filas_usd = page.query_selector_all("table tbody tr")
        for f in filas_usd:
            cols = [c.inner_text().strip() for c in f.query_selector_all("td")]
            if len(cols) >= 5:
                transacciones.append([cols[0], cols[1], cols[2], cols[3], cols[4], "USD"])

        browser.close()
    return transacciones

def ejecutar():
    ws = conectar_google_sheets()
    datos_actuales = ws.get_all_values()
    
    # Crear claves de los registros que ya existen para no duplicar
    existentes = set()
    for r in datos_actuales[1:]:
        if len(r) >= 10:
            clave = f"{r[0]}_{r[6]}_{r[7]}_{r[8]}_{r[9]}"
            existentes.add(clave)

    nuevas = obtener_transacciones()
    n_agregados = 0
    
    for t in nuevas:
        # t = [Fecha, Tipo, Librador, Monto, Plazo, Moneda]
        # Calcular valor numérico
        monto_clean = t[3].replace("UY$", "").replace("USD", "").replace(".", "").replace(",", ".").strip()
        clave = f"{t[0]}_{t[2]}_{t[3]}_{t[4]}_{t[5]}"
        
        if clave not in existentes:
            # Fórmulas para las columnas de TC y Equivalente en USD
            row_idx = len(ws.get_all_values()) + 1
            formula_tc = f"=VLOOKUP(A{row_idx}, TC!A:B, 2, FALSE)"
            formula_eq = f"=IF(J{row_idx}=\"USD\", K{row_idx}, K{row_idx}/H{row_idx})"
            
            # Nueva fila
            nueva_fila = [t[0], t[1], t[2], t[3], t[4], t[0], t[1], t[2], t[3], t[4], t[5], float(monto_clean), formula_tc, formula_eq]
            ws.append_row(nueva_fila, value_input_option="USER_ENTERED")
            n_agregados += 1

    print(f"Proceso finalizado. Registros nuevos agregados: {n_agregados}")

if __name__ == "__main__":
    ejecutar()
