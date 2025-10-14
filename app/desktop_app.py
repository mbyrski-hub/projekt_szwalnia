import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import socket
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image, ImageWin, ImageDraw
import io
import win32print
import win32ui
import win32con
import sys
import datetime
import logging
import os
import pystray
import win32com.client
import requests
import json
import queue
import pyodbc
import schedule
import time
import platform
import subprocess
import winshell
from os.path import expanduser

# --- Konfiguracja ---
HOST = '0.0.0.0'
PORT_PRINT = 5001
PORT_SYNC = 5002
PRINTER_DPI = 203
APP_NAME = "Szwalnia_Serwer"

# --- Konfiguracja logowania ---
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = 'synchronizator.log'

file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setFormatter(log_formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)

# --- Zmienne globalne ---
flask_thread_print = None
flask_thread_sync = None
tray_icon = None
APP_CONFIG = {"SELECTED_PRINTER": None}
stop_scheduler_thread = threading.Event()
main_queue = queue.Queue()
root = None

# --- Logika Konfiguracji ---
CONFIG_FILE = 'config.json'

def save_config(data):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        return True
    except Exception as e:
        messagebox.showerror("Błąd zapisu", f"Nie można zapisać konfiguracji: {e}")
        return False

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

config = load_config()
APP_CONFIG["SELECTED_PRINTER"] = config.get("selected_printer")


# --- Funkcje autostartu ---
def get_startup_folder():
    return os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming', 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')

def create_shortcut(enable):
    shortcut_path = os.path.join(get_startup_folder(), f"{APP_NAME}.lnk")
    if enable:
        if not os.path.exists(shortcut_path):
            target_path = sys.executable
            winshell.CreateShortcut(Path=shortcut_path, Target=target_path)
    else:
        if os.path.exists(shortcut_path):
            os.remove(shortcut_path)

# --- Logika Aplikacji (SQL i API) ---
def log_message(queue, message, color='black', level='info'):
    timestamp = time.strftime('%H:%M:%S')

    log_entry = message.strip()
    if level == 'info':
        logger.info(log_entry)
    elif level == 'error':
        logger.error(log_entry)
    elif level == 'warning':
        logger.warning(log_entry)

    if queue:
        queue.put({'msg': f"[{timestamp}] {message}\n", 'color': color})

def get_connection_string(config_data):
    return (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={config_data.get('server')};"
            f"DATABASE={config_data.get('database')};"
            f"UID={config_data.get('sql_user')};"
            f"PWD={config_data.get('sql_password')};")

def test_sql_connection(queue, config_data):
    log_message(queue, "Testowanie połączenia z bazą danych SQL...")
    try:
        with pyodbc.connect(get_connection_string(config_data), timeout=5):
            log_message(queue, "SUKCES! Połączenie z bazą danych działa poprawnie.", color='green')
            return True
    except Exception as e:
        log_message(queue, f"BŁĄD! Nie można nawiązać połączenia: {e}", color='red', level='error')
        return False

def get_warehouses_from_sql(queue, config_data):
    log_message(queue, "Pobieranie listy magazynów z bazy danych...")
    query = "SELECT Symbol, Nazwa FROM ModelDanychContainer.Magazyny ORDER BY Symbol"
    warehouses = []
    try:
        with pyodbc.connect(get_connection_string(config_data)) as connection:
            cursor = connection.cursor()
            cursor.execute(query)
            for row in cursor.fetchall():
                warehouses.append(f"{row.Symbol.strip()} ({row.Nazwa.strip()})")
        log_message(queue, f"Pobrano {len(warehouses)} magazynów.", color='green')
        return warehouses
    except Exception as e:
        log_message(queue, f"Błąd podczas pobierania magazynów: {e}", color='red', level='error')
        return []

def get_data_from_warehouse(queue, config_data, warehouse_symbol):
    log_message(queue, f"Pobieranie towarów, cen i dostawców z magazynu: {warehouse_symbol}...")
    data = []
    query = """
        WITH LastPurchasePerSupplier AS (
            SELECT
                p.Asortyment_Id,
                d.PodmiotId,
                k.Wartosc,
                k.Ilosc,
                k.Data,
                ROW_NUMBER() OVER(PARTITION BY p.Asortyment_Id, d.PodmiotId ORDER BY k.Data DESC, k.Lp DESC) as rn
            FROM ModelDanychContainer.Przyjecia AS p
            INNER JOIN ModelDanychContainer.KosztyZakupu AS k ON p.KosztPierwotny_Id = k.Id
            INNER JOIN ModelDanychContainer.PozycjeDokumentu AS poz ON p.Id = poz.Przyjecie_Id
            INNER JOIN ModelDanychContainer.Dokumenty AS d ON poz.Dokument_Id = d.Id
            WHERE d.PodmiotId IS NOT NULL
        )
        SELECT
            a.Symbol,
            a.Nazwa,
            pod.NazwaSkrocona AS NazwaDostawcy,
            lpps.Data AS DataOstatniegoZakupu,
            (lpps.Wartosc / lpps.Ilosc) AS CenaJednostkowaNetto
        FROM ModelDanychContainer.Asortymenty AS a
        INNER JOIN ModelDanychContainer.StanyMagazynowe AS sm ON a.Id = sm.Asortyment_Id
        INNER JOIN ModelDanychContainer.Magazyny AS m ON sm.Magazyn_Id = m.Id
        INNER JOIN LastPurchasePerSupplier lpps ON a.Id = lpps.Asortyment_Id AND lpps.rn = 1
        INNER JOIN ModelDanychContainer.Podmioty AS pod ON lpps.PodmiotId = pod.Id
        WHERE m.Symbol = ?
    """
    try:
        with pyodbc.connect(get_connection_string(config_data)) as connection:
            cursor = connection.cursor()
            cursor.execute(query, warehouse_symbol)
            for row in cursor.fetchall():
                price = float(row.CenaJednostkowaNetto) if row.CenaJednostkowaNetto is not None else 0.0
                data.append({
                    'symbol': row.Symbol.upper().strip(),
                    'name': row.Nazwa.strip(),
                    'price': price,
                    'price_date': row.DataOstatniegoZakupu.strftime('%Y-%m-%d') if row.DataOstatniegoZakupu else None,
                    'supplier': row.NazwaDostawcy.strip() if row.NazwaDostawcy else 'Brak'
                })
        log_message(queue, f"Pobrano {len(data)} wpisów cenowych od dostawców.", color='green')
        return data
    except Exception as e:
        log_message(queue, f"Błąd podczas pobierania danych: {e}", color='red', level='error')
        return None

def send_prices_to_webapp(queue, config_data, data_to_send):
    url = f"{config_data.get('web_app_url')}/api/v1/update-supplier-prices"
    headers = {'Content-Type': 'application/json', 'X-API-KEY': config_data.get('api_key')}
    
    data_for_api = [
        {
            'symbol': item['symbol'],
            'price': item.get('price'),
            'supplier': item.get('supplier'),
            'price_date': item.get('price_date')
        }
        for item in data_to_send if item.get('price') is not None and item.get('supplier')
    ]

    if not data_for_api:
        log_message(queue, "Brak cen od dostawców do zaktualizowania.", color='orange', level='warning')
        return True
        
    log_message(queue, f"Wysyłanie {len(data_for_api)} aktualizacji cen od dostawców...")
    try:
        response = requests.post(url, headers=headers, data=json.dumps(data_for_api), timeout=60)
        if response.status_code == 200:
            msg = response.json().get('message', 'Ceny dostawców zaktualizowane.')
            log_message(queue, f"SUKCES! {msg}", color='green')
            return True
        else:
            log_message(queue, f"BŁĄD CEN DOSTAWCÓW: {response.status_code} - {response.text}", color='red', level='error')
            return False
    except requests.exceptions.RequestException as e:
        log_message(queue, f"KRYTYCZNY BŁĄD CEN DOSTAWCÓW: {e}", color='red', level='error')
        return False

def send_catalog_to_webapp(queue, config_data, data_to_send):
    url = f"{config_data.get('web_app_url')}/api/v1/receive-subiekt-catalog"
    headers = {'Content-Type': 'application/json', 'X-API-KEY': config_data.get('api_key')}
    
    unique_items = {item['symbol']: item for item in data_to_send}.values()
    catalog_for_api = [{'symbol': item['symbol'], 'name': item.get('name')} for item in unique_items]
    
    if not catalog_for_api:
        log_message(queue, "Brak katalogu do wysłania.", color='orange', level='warning')
        return True
    log_message(queue, f"Wysyłanie {len(catalog_for_api)} unikalnych towarów do zmapowania...")
    try:
        response = requests.post(url, headers=headers, data=json.dumps(catalog_for_api), timeout=30)
        if response.status_code == 200:
            msg = response.json().get('message', 'Katalog wysłany.')
            log_message(queue, f"SUKCES! {msg}", color='green')
            return True
        else:
            log_message(queue, f"BŁĄD KATALOGU: {response.status_code} - {response.text}", color='red', level='error')
            return False
    except requests.exceptions.RequestException as e:
        log_message(queue, f"KRYTYCZNY BŁĄD KATALOGU: {e}", color='red', level='error')
        return False

def full_sync_task(queue, config_data):
    warehouse_full_name = config_data.get('default_warehouse')
    if not warehouse_full_name:
        log_message(queue, "BŁĄD: Brak domyślnego magazynu.", color='red', level='error')
        return

    warehouse_symbol = warehouse_full_name.split(' ')[0]
    log_message(queue, f"\n--- Rozpoczynam Pełną Synchronizację ({warehouse_symbol}) ---", color='blue')
    data = get_data_from_warehouse(queue, config_data, warehouse_symbol)
    if data is None:
        log_message(queue, "Synchronizacja przerwana.", color='red', level='error')
        return

    log_message(queue, "\nKrok 1: Wysyłanie katalogu unikalnych towarów...", color='blue')
    catalog_success = send_catalog_to_webapp(queue, config_data, data)

    log_message(queue, "\nKrok 2: Aktualizacja cen od dostawców...", color='blue')
    prices_success = send_prices_to_webapp(queue, config_data, data)

    if catalog_success and prices_success:
        log_message(queue, "\n--- PEŁNA SYNCHRONIZACJA ZAKOŃCZONA SUKCESEM ---", color='green')
    else:
        log_message(queue, "\n--- PEŁNA SYNCHRONIZACJA ZAKOŃCZONA Z BŁĘDAMI ---", color='red', level='error')

def scheduler_thread_func(queue, config_data):
    log_message(queue, "Wątek harmonogramu uruchomiony.", color='gray')
    update_time = config_data.get("update_time", "15:00")
    try:
        schedule.every().day.at(update_time).do(full_sync_task, queue, config_data)
    except schedule.ScheduleError:
        log_message(queue, f"BŁĄD: Nieprawidłowy format czasu '{update_time}'. Użyj formatu HH:MM.", color='red', level='error')

    while not stop_scheduler_thread.is_set():
        schedule.run_pending()
        time.sleep(1)

    log_message(queue, "Wątek harmonogramu zatrzymany.", color='gray')

# --- Funkcje pomocnicze ---
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_available_printers():
    try:
        printers = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL)
        return [printer[2] for printer in printers]
    except Exception as e:
        messagebox.showerror("Błąd Drukarek", f"Nie można było pobrać listy drukarek: {e}")
        return []

# --- Funkcje autostartu ---
def create_shortcut_in_startup(target_path, shortcut_name):
    startup_path = get_startup_folder()
    shortcut_path = os.path.join(startup_path, f"{shortcut_name}.lnk")
    if not os.path.exists(os.path.dirname(target_path)):
        messagebox.showwarning("Błąd", f"Nie można utworzyć skrótu, ponieważ folder docelowy nie istnieje:\n{os.path.dirname(target_path)}\n\nTa funkcja zadziała poprawnie dopiero po skompilowaniu do .exe.")
        return False
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(shortcut_path)
    shortcut.Targetpath = target_path
    shortcut.WorkingDirectory = os.path.dirname(target_path)
    shortcut.save()
    return True

def remove_shortcut_from_startup(shortcut_name):
    startup_path = get_startup_folder()
    shortcut_path = os.path.join(startup_path, f"{shortcut_name}.lnk")
    if os.path.exists(shortcut_path):
        os.remove(shortcut_path)
        return True
    return False

# --- Logika Aplikacji ---
class LogHandler:
    def __init__(self, queue):
        self.queue = queue

    def write(self, message):
        if message.strip():
            log_message(self.queue, message)

    def flush(self):
        pass

# --- Serwery Flask ---
flask_app_print = Flask("print_server")
CORS(flask_app_print)
flask_app_sync = Flask("sync_server")
CORS(flask_app_sync)

@flask_app_print.route('/print_label', methods=['POST'])
def print_label():
    log_handler = flask_app_print.config['LOG_HANDLER']
    log_handler.write("Odebrano nowe żądanie drukowania...")

    selected_printer = APP_CONFIG.get("SELECTED_PRINTER")
    if not selected_printer:
        return jsonify({'error': 'Drukarka nie została wybrana na serwerze druku.'}), 500

    if 'image' not in request.files:
        return jsonify({'error': 'Brak pliku obrazu'}), 400

    try:
        width_mm = float(request.form.get('width_mm'))
        height_mm = float(request.form.get('height_mm'))
        quantity = int(request.form.get('quantity', 1))
    except (TypeError, ValueError):
        return jsonify({'error': 'Brak lub niepoprawne wymiary/ilość w żądaniu.'}), 400

    try:
        img_bytes = request.files['image'].read()
        log_handler.write(f"Odebrano plik ({len(img_bytes)} bajtów) | Wymiary: {width_mm}x{height_mm}mm | Ilość: {quantity}")

        log_handler.write("Rozpoczynanie drukowania przez sterownik Windows (GDI)...")

        h_printer = win32print.OpenPrinter(selected_printer)
        try:
            printer_dc = win32ui.CreateDC()
            printer_dc.CreatePrinterDC(selected_printer)

            printer_dpi_x = printer_dc.GetDeviceCaps(win32con.LOGPIXELSX)
            printer_dpi_y = printer_dc.GetDeviceCaps(win32con.LOGPIXELSY)
            log_handler.write(f"DPI sterownika: {printer_dpi_x}x{printer_dpi_y}")

            width_px = int(width_mm * printer_dpi_x / 25.4)
            height_px = int(height_mm * printer_dpi_y / 25.4)
            log_handler.write(f"Wymiary wydruku w pikselach: {width_px}x{height_px}")

            image = Image.open(io.BytesIO(img_bytes))
            dib = ImageWin.Dib(image)

            for i in range(quantity):
                log_handler.write(f"Drukowanie kopii {i + 1} z {quantity}...")
                printer_dc.StartDoc(f"Etykieta {i+1}/{quantity}")
                printer_dc.StartPage()
                dib.draw(printer_dc.GetHandleOutput(), (0, 0, width_px, height_px))
                printer_dc.EndPage()
                printer_dc.EndDoc()

            log_handler.write("Wszystkie kopie wysłane pomyślnie przez sterownik!")

        finally:
            win32print.ClosePrinter(h_printer)

        return jsonify({'status': f'Wydrukowano pomyślnie {quantity} kopii.'})

    except Exception as e:
        log_handler.write(f"KRYTYCZNY BŁĄD PODCZAS DRUKOWANIA: {e}")
        return jsonify({'error': str(e)}), 500

@flask_app_print.route('/status')
def print_status():
    return jsonify({"status": "online", "service": "print_server"})

@flask_app_sync.route('/status')
def sync_status():
    return jsonify({"status": "online", "service": "sync_server"})

@flask_app_print.route('/trigger-sync', methods=['POST'])
def trigger_sync():
    log_handler = flask_app_print.config['LOG_HANDLER']
    
    api_key_from_request = request.headers.get('X-API-KEY')
    current_config = load_config()
    
    if not api_key_from_request or api_key_from_request != current_config.get('api_key'):
        log_handler.write("Odebrano próbę zdalnego uruchomienia synchronizacji z BŁĘDNYM kluczem API.")
        return jsonify({'error': 'Brak autoryzacji'}), 401

    log_handler.write("Odebrano zdalne polecenie synchronizacji ze strony WWW.")
    
    sync_thread = threading.Thread(
        target=full_sync_task, 
        args=(main_queue, current_config), 
        daemon=True
    )
    sync_thread.start()
    
    return jsonify({'status': 'Synchronizacja została pomyślnie uruchomiona w tle.'}), 202

# --- Interfejs Graficzny (GUI Tkinter) ---
class PrintServerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Lokalny Serwer Drukowania i Synchronizacji")
        self.root.geometry("700x600")
        self.root.minsize(600, 500)
        self.tray_icon = None
        self.queue = main_queue
        flask_app_print.config['LOG_HANDLER'] = LogHandler(self.queue)
        flask_app_sync.config['LOG_HANDLER'] = LogHandler(self.queue)

        self.notebook = ttk.Notebook(root)
        self.print_tab = ttk.Frame(self.notebook)
        self.sync_tab = ttk.Frame(self.notebook)
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.print_tab, text="Serwer Druku")
        self.notebook.add(self.sync_tab, text="Synchronizator")
        self.notebook.add(self.log_tab, text="Główny Log")
        self.notebook.pack(expand=True, fill="both", padx=10, pady=10)

        log_frame = ttk.LabelFrame(self.log_tab, text="Log operacji")
        log_frame.pack(fill='both', expand=True, padx=5, pady=5)
        self.main_log_text = scrolledtext.ScrolledText(log_frame, state='disabled', wrap=tk.WORD, height=10)
        self.main_log_text.pack(padx=5, pady=5, expand=True, fill='both')
        self.main_log_text.tag_config('green', foreground='#4CAF50')
        self.main_log_text.tag_config('red', foreground='#F44336')
        self.main_log_text.tag_config('orange', foreground='#FF9800')
        self.main_log_text.tag_config('gray', foreground='#9E9E9E')
        self.main_log_text.tag_config('blue', foreground='#2196F3')
        ttk.Button(log_frame, text="Otwórz plik logu", command=self.open_log_file).pack(side='bottom', pady=5)

        info_frame = tk.Frame(self.print_tab, pady=10)
        info_frame.pack(fill='x', padx=10)
        printer_label = tk.Label(info_frame, text="Wybierz drukarkę etykiet:")
        printer_label.pack()
        self.available_printers = get_available_printers()
        self.selected_printer = tk.StringVar(root)
        
        saved_printer = config.get('selected_printer')
        if saved_printer and saved_printer in self.available_printers:
            self.selected_printer.set(saved_printer)
        elif self.available_printers:
            self.selected_printer.set(self.available_printers[0])
        else:
            self.available_printers.append("Brak drukarek")
            self.selected_printer.set("Brak drukarek")
        
        APP_CONFIG["SELECTED_PRINTER"] = self.selected_printer.get()
        
        self.printer_menu = tk.OptionMenu(info_frame, self.selected_printer, *self.available_printers, command=self.on_printer_select)
        self.printer_menu.pack(pady=5, fill='x')
        self.ip_label = tk.Label(info_frame, text="Twój adres IP w sieci lokalnej: ?")
        self.ip_label.pack()
        self.ip_button = tk.Button(info_frame, text="Odśwież adres IP", command=self.update_ip)
        self.ip_button.pack(pady=5)
        self.status_label_print = tk.Label(info_frame, text="Status serwera druku: Zatrzymany", fg="red", font=("Helvetica", 10, "bold"))
        self.status_label_print.pack()

        button_frame_print = tk.Frame(self.print_tab, pady=10)
        button_frame_print.pack(fill='x')
        self.start_button_print = tk.Button(button_frame_print, text="Start", command=self.start_print_server, bg="lightgreen", height=2)
        self.start_button_print.pack(side='left', expand=True, fill='x', padx=20)
        self.stop_button_print = tk.Button(button_frame_print, text="Stop", command=self.stop_server, bg="salmon", state=tk.DISABLED, height=2)
        self.stop_button_print.pack(side='right', expand=True, fill='x', padx=20)

        self.config = load_config()
        self.server_var = tk.StringVar(value=self.config.get('server', ''))
        self.database_var = tk.StringVar(value=self.config.get('database', ''))
        self.sql_user_var = tk.StringVar(value=self.config.get('sql_user', ''))
        self.sql_password_var = tk.StringVar(value=self.config.get('sql_password', ''))
        self.web_app_url_var = tk.StringVar(value=self.config.get('web_app_url', ''))
        self.api_key_var = tk.StringVar(value=self.config.get('api_key', ''))
        self.warehouse_var = tk.StringVar(value=self.config.get('default_warehouse', ''))
        self.autostart_var = tk.BooleanVar(value=self.config.get('autostart', False))
        self.update_time_var = tk.StringVar(value=self.config.get('update_time', '15:00'))

        main_frame = tk.Frame(self.sync_tab, padx=10, pady=10)
        main_frame.pack(fill='both', expand=True)

        status_frame_sync = tk.Frame(main_frame)
        status_frame_sync.pack(fill='x', pady=5)
        self.status_label_sync = tk.Label(status_frame_sync, text="Status serwera synchronizacji: Zatrzymany", fg="red", font=("Helvetica", 10, "bold"))
        self.status_label_sync.pack()

        sync_frame = ttk.LabelFrame(main_frame, text="Automatyczna Synchronizacja")
        sync_frame.pack(fill='x', pady=5, ipady=10)
        self.full_sync_button = ttk.Button(sync_frame, text="🚀 Ręczna Pełna Synchronizacja", command=self.run_full_sync)
        self.full_sync_button.pack(expand=True, fill='x', padx=5, pady=5)

        manual_frame = ttk.LabelFrame(main_frame, text="Konfiguracja i Kroki Ręczne")
        manual_frame.pack(fill='x', pady=5)

        config_frame = tk.Frame(manual_frame)
        config_frame.pack(fill='x', padx=5, pady=5)
        ttk.Label(config_frame, text="Serwer SQL:").grid(row=0, column=0, sticky='w', padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.server_var, width=40).grid(row=0, column=1, sticky='ew', padx=5, pady=2)
        ttk.Label(config_frame, text="Nazwa Bazy Danych:").grid(row=1, column=0, sticky='w', padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.database_var, width=40).grid(row=1, column=1, sticky='ew', padx=5, pady=2)
        ttk.Label(config_frame, text="Użytkownik SQL:").grid(row=2, column=0, sticky='w', padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.sql_user_var, width=40).grid(row=2, column=1, sticky='ew', padx=5, pady=2)
        ttk.Label(config_frame, text="Hasło SQL:").grid(row=3, column=0, sticky='w', padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.sql_password_var, show='*').grid(row=3, column=1, sticky='ew', padx=5, pady=2)
        ttk.Label(config_frame, text="URL Aplikacji Web:").grid(row=4, column=0, sticky='w', padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.web_app_url_var, width=40).grid(row=4, column=1, sticky='ew', padx=5, pady=2)
        ttk.Label(config_frame, text="Klucz API:").grid(row=5, column=0, sticky='w', padx=5, pady=2)
        ttk.Entry(config_frame, textvariable=self.api_key_var, show='*').grid(row=5, column=1, sticky='ew', padx=5, pady=2)

        data_source_frame = tk.Frame(manual_frame)
        data_source_frame.pack(fill='x', padx=5, pady=5)
        self.warehouse_combo = ttk.Combobox(data_source_frame, textvariable=self.warehouse_var, state='disabled')
        self.warehouse_combo.pack(side='left', fill='x', expand=True, padx=5, pady=5)
        self.connect_button = ttk.Button(data_source_frame, text="Wczytaj magazyny", command=self.run_load_warehouses)
        self.connect_button.pack(side='left', padx=5, pady=5)

        auto_frame = ttk.LabelFrame(manual_frame, text="Ustawienia Automatyzacji")
        auto_frame.pack(fill='x', pady=(10, 5), ipady=5, padx=5)
        self.startup_check = tk.Checkbutton(auto_frame, text="Uruchom program przy starcie systemu Windows", var=self.autostart_var, command=self.toggle_startup)
        self.startup_check.pack(anchor='w', padx=5)
        time_frame = tk.Frame(auto_frame)
        time_frame.pack(fill='x', padx=5, pady=5)
        ttk.Label(time_frame, text="Synchronizuj codziennie o godzinie:").pack(side='left')
        ttk.Entry(time_frame, textvariable=self.update_time_var, width=10).pack(side='left', padx=5)

        btn_config_frame = tk.Frame(manual_frame)
        btn_config_frame.pack(fill='x', pady=5, padx=5)
        self.test_button = ttk.Button(btn_config_frame, text="Testuj Połączenie", command=self.run_test_connection)
        self.test_button.pack(side='left', padx=5)
        ttk.Button(btn_config_frame, text="Zapisz Konfigurację", command=self.save_current_config).pack(side='left', padx=5)
        self.fetch_button = ttk.Button(btn_config_frame, text="Pobierz i Wyślij Ręcznie...", command=self.run_fetch_data)
        self.fetch_button.pack(side='right', padx=5)

        self.root.after(100, self.process_queue)
        self.update_full_sync_button_state()
        self.update_ip()
        self.start_sync_server()
        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)

    def create_tray_image(self):
        image = Image.new('RGB', (64, 64), 'gray')
        dc = ImageDraw.Draw(image)
        dc.rectangle((10, 10, 54, 54), fill='white')
        dc.text((22, 22), "P", fill="black")
        return image

    def show_window(self, icon, item):
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.deiconify()

    def exit_app(self, icon, item):
        self.on_closing(force=True)

    def minimize_to_tray(self):
        self.root.withdraw()
        image = self.create_tray_image()
        menu = (pystray.MenuItem('Pokaż', self.show_window, default=True),
                pystray.MenuItem('Zakończ', self.exit_app))
        self.tray_icon = pystray.Icon(APP_NAME, image, "Serwer Drukowania i Synchronizacji", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def on_closing(self, force=False):
        if force or messagebox.askokcancel("Zamknij", "Czy na pewno chcesz zamknąć serwer?"):
            if self.tray_icon:
                self.tray_icon.stop()
            self.root.destroy()
            os._exit(0)

    def on_printer_select(self, selected_value):
        APP_CONFIG["SELECTED_PRINTER"] = selected_value
        log_message(self.queue, f"Wybrano drukarkę: {selected_value}")

    def update_ip(self):
        ip = get_local_ip()
        self.ip_label.config(text=f"Twój adres IP w sieci lokalnej: {ip}")
        log_message(self.queue, f"Sprawdzono IP: {ip}. Ten adres należy wpisać na stronie.")

    def start_print_server(self):
        if not APP_CONFIG.get("SELECTED_PRINTER") or APP_CONFIG.get("SELECTED_PRINTER") == "Brak drukarek":
            messagebox.showerror("Błąd", "Nie wybrano poprawnej drukarki!")
            return
        global flask_thread_print
        log_message(self.queue, f"Uruchamianie serwera druku na {HOST}:{PORT_PRINT}...")
        def run_app():
            log = logging.getLogger('werkzeug')
            log.disabled = True
            flask_app_print.logger.disabled = True
            try:
                flask_app_print.run(host=HOST, port=PORT_PRINT, debug=False)
            except Exception as e:
                log_message(self.queue, f"Błąd krytyczny serwera druku: {e}", color='red', level='error')

        flask_thread_print = threading.Thread(target=run_app, daemon=True)
        flask_thread_print.start()
        self.status_label_print.config(text="Status serwera druku: Działa", fg="green")
        self.start_button_print.config(state=tk.DISABLED)
        self.stop_button_print.config(state=tk.NORMAL)
        self.printer_menu.config(state=tk.DISABLED)
        log_message(self.queue, "Serwer druku uruchomiony.", color='green')

    def start_sync_server(self):
        global flask_thread_sync
        log_message(self.queue, f"Uruchamianie serwera synchronizacji na {HOST}:{PORT_SYNC}...")
        def run_app():
            log = logging.getLogger('werkzeug')
            log.disabled = True
            flask_app_sync.logger.disabled = True
            try:
                flask_app_sync.run(host=HOST, port=PORT_SYNC, debug=False)
            except Exception as e:
                log_message(self.queue, f"Błąd krytyczny serwera synchronizacji: {e}", color='red', level='error')
        
        flask_thread_sync = threading.Thread(target=run_app, daemon=True)
        flask_thread_sync.start()
        self.status_label_sync.config(text="Status serwera synchronizacji: Działa", fg="green")
        log_message(self.queue, "Serwer synchronizacji uruchomiony.", color='green')


    def stop_server(self):
        log_message(self.queue, "Zatrzymywanie serwera... Aplikacja zostanie zamknięta.", color='orange')
        self.on_closing(force=True)

    def toggle_startup(self):
        is_enabled = self.autostart_var.get()
        if getattr(sys, 'frozen', False):
            target_path = sys.executable
        else:
            messagebox.showinfo("Informacja", "Funkcja autostartu tworzy skrót do pliku .exe. Aby w pełni przetestować tę funkcję, najpierw skompiluj aplikację.")
            target_path = os.path.abspath(__file__)
        if is_enabled:
            if create_shortcut_in_startup(target_path, APP_NAME):
                log_message(self.queue, "Dodano aplikację do autostartu.")
        else:
            if remove_shortcut_from_startup(APP_NAME):
                log_message(self.queue, "Usunięto aplikację z autostartu.")

    def open_log_file(self):
        try:
            if platform.system() == "Windows":
                os.startfile(log_file)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", log_file])
            else:
                subprocess.Popen(["xdg-open", log_file])
        except Exception as e:
            log_message(self.queue, f"Nie można otworzyć pliku logu: {e}", color='red', level='error')

    def get_current_config(self):
        return {
            'server': self.server_var.get(), 'database': self.database_var.get(), 'sql_user': self.sql_user_var.get(),
            'sql_password': self.sql_password_var.get(), 'web_app_url': self.web_app_url_var.get(), 'api_key': self.api_key_var.get(),
            'default_warehouse': self.warehouse_var.get(),
            'autostart': self.autostart_var.get(), 'update_time': self.update_time_var.get(),
            'selected_printer': self.selected_printer.get()
        }

    def save_current_config(self):
        global config, stop_scheduler_thread
        if not self.warehouse_var.get() and (self.autostart_var.get() or self.update_time_var.get()):
            messagebox.showwarning("Brak magazynu", "Wybierz i zapisz domyślny magazyn, aby włączyć autostart i automatyzację.")
            return

        new_config = self.get_current_config()
        if save_config(new_config):
            config = new_config
            create_shortcut(config.get('autostart'))
            messagebox.showinfo("Sukces", "Konfiguracja została zapisana.")
            self.update_full_sync_button_state()

            stop_scheduler_thread.set()
            time.sleep(1.1)
            stop_scheduler_thread.clear()
            threading.Thread(target=scheduler_thread_func, args=(self.queue, config), daemon=True).start()

    def process_queue(self):
        try:
            while True:
                data = self.queue.get_nowait()
                self.main_log_text.configure(state='normal')
                self.main_log_text.insert(tk.END, data['msg'], data['color'])
                self.main_log_text.configure(state='disabled')
                self.main_log_text.see(tk.END)
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)

    def update_full_sync_button_state(self):
        self.full_sync_button.config(state='normal' if self.config.get('default_warehouse') else 'disabled')

    def run_full_sync(self):
        threading.Thread(target=full_sync_task, args=(self.queue, self.get_current_config()), daemon=True).start()

    def run_load_warehouses(self):
        def task_wrapper():
            warehouses = get_warehouses_from_sql(self.queue, self.get_current_config())
            if warehouses:
                self.warehouse_combo['values'] = warehouses
                self.warehouse_combo.config(state='readonly')
                saved_warehouse = self.config.get('default_warehouse')
                if saved_warehouse in warehouses:
                    self.warehouse_var.set(saved_warehouse)
                elif warehouses:
                    self.warehouse_var.set(warehouses[0])
        threading.Thread(target=task_wrapper, daemon=True).start()

    def run_test_connection(self):
        threading.Thread(target=test_sql_connection, args=(self.queue, self.get_current_config()), daemon=True).start()

    def run_fetch_data(self):
        if not self.warehouse_var.get():
            messagebox.showwarning("Brak magazynu", "Najpierw wczytaj i wybierz magazyn.")
            return

        def task_wrapper():
            data = get_data_from_warehouse(self.queue, self.get_current_config(), self.warehouse_var.get().split(' ')[0])
            if data is not None:
                self.root.after(0, self.show_review_window, data)
        threading.Thread(target=task_wrapper, daemon=True).start()

    def show_review_window(self, data):
        review_window = tk.Toplevel(self.root)
        review_window.title(f"Podgląd Danych ({len(data)} wpisów cenowych)")
        review_window.geometry("950x500")
        
        cols = ('Symbol', 'Nazwa', 'Cena Netto', 'Data Ceny', 'Dostawca')
        tree = ttk.Treeview(review_window, columns=cols, show='headings')
        
        tree.heading('Symbol', text='Symbol'); tree.column('Symbol', width=120)
        tree.heading('Nazwa', text='Nazwa'); tree.column('Nazwa', width=300)
        tree.heading('Cena Netto', text='Cena Netto'); tree.column('Cena Netto', width=100, anchor='e')
        tree.heading('Data Ceny', text='Data Ceny'); tree.column('Data Ceny', width=100, anchor='center')
        tree.heading('Dostawca', text='Ostatni Dostawca'); tree.column('Dostawca', width=200)

        vsb = ttk.Scrollbar(review_window, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        tree.pack(expand=True, fill='both', padx=10, pady=5)
        
        for item in data:
            tree.insert("", "end", values=(
                item['symbol'], 
                item['name'], 
                f"{item.get('price', 0.0):.2f} zł", 
                item.get('price_date', 'Brak'),
                item.get('supplier', 'Brak') 
            ))
            
        def send_catalog_action():
            review_window.destroy()
            threading.Thread(target=send_catalog_to_webapp, args=(self.queue, self.get_current_config(), data), daemon=True).start()
        
        def send_prices_action():
            review_window.destroy()
            threading.Thread(target=send_prices_to_webapp, args=(self.queue, self.get_current_config(), data), daemon=True).start()
            
        button_frame = tk.Frame(review_window)
        button_frame.pack(pady=10)
        ttk.Button(button_frame, text=f"Wyślij katalog unikalnych towarów", command=send_catalog_action).pack(side='left', padx=10)
        ttk.Button(button_frame, text=f"Aktualizuj ceny od dostawców", command=send_prices_action).pack(side='left', padx=10)

if __name__ == "__main__":
    root = tk.Tk()
    app = PrintServerApp(root)
    root.mainloop()