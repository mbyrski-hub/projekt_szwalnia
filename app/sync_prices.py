import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import requests
import json
import threading
import queue
import os
import pyodbc
import sys
import schedule
import time
from PIL import Image, ImageDraw
import winshell
from os.path import expanduser
from pystray import Icon as icon, MenuItem as item
import logging
import subprocess
import platform

# --- KONFIGURACJA LOGOWANIA ---
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = 'synchronizator.log'

# Handler do pliku
file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setFormatter(log_formatter)

# Handler do konsoli (dla debugowania)
# stream_handler = logging.StreamHandler()
# stream_handler.setFormatter(log_formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
# logger.addHandler(stream_handler)


# --- ZMIENNE GLOBALNE ---
stop_scheduler_thread = threading.Event()
main_queue = queue.Queue()
root = None
tray_icon = None

# --- LOGIKA KONFIGURACJI ---
CONFIG_FILE = 'config.json'
AUTOSTART_PATH = os.path.join(expanduser("~"), "AppData", "Roaming", "Microsoft", "Windows", "Start Menu", "Programs", "Startup")

def save_config(data):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)
        return True
    except Exception as e:
        messagebox.showerror("Błąd zapisu", f"Nie można zapisać konfiguracji: {e}")
        return False

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except json.JSONDecodeError: return {}
    return {}

config = load_config()

# --- FUNKCJE AUTOSTARTU ---
def create_shortcut(enable):
    shortcut_path = os.path.join(AUTOSTART_PATH, "SynchronizatorSzwalnia.lnk")
    if enable:
        if not os.path.exists(shortcut_path):
            target_path = sys.executable
            winshell.CreateShortcut(Path=shortcut_path, Target=target_path, Arguments="--background")
    else:
        if os.path.exists(shortcut_path): os.remove(shortcut_path)

# --- LOGIKA APLIKACJI (SQL i API) ---
def log_message(queue, message, color='black', level='info'):
    timestamp = time.strftime('%H:%M:%S')
    
    # Zapis do pliku log
    log_entry = message.strip()
    if level == 'info': logger.info(log_entry)
    elif level == 'error': logger.error(log_entry)
    elif level == 'warning': logger.warning(log_entry)
    
    # Wysłanie do GUI
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
    log_message(queue, f"Pobieranie towarów i cen zakupu z magazynu: {warehouse_symbol}...")
    data = []
    query = """
        WITH LastPurchaseCost AS (
            SELECT p.Asortyment_Id, k.Wartosc, k.Ilosc, k.Data,
                   ROW_NUMBER() OVER(PARTITION BY p.Asortyment_Id ORDER BY k.Data DESC, k.Lp DESC) as rn
            FROM ModelDanychContainer.Przyjecia AS p
            INNER JOIN ModelDanychContainer.KosztyZakupu AS k ON p.KosztPierwotny_Id = k.Id
        )
        SELECT a.Symbol, a.Nazwa, lpc.Data AS DataOstatniegoZakupu, (lpc.Wartosc / lpc.Ilosc) AS CenaJednostkowaNetto
        FROM ModelDanychContainer.Asortymenty AS a
        INNER JOIN ModelDanychContainer.StanyMagazynowe AS sm ON a.Id = sm.Asortyment_Id
        INNER JOIN ModelDanychContainer.Magazyny AS m ON sm.Magazyn_Id = m.Id
        LEFT JOIN LastPurchaseCost lpc ON a.Id = lpc.Asortyment_Id AND lpc.rn = 1
        WHERE m.Symbol = ?
    """
    try:
        with pyodbc.connect(get_connection_string(config_data)) as connection:
            cursor = connection.cursor()
            cursor.execute(query, warehouse_symbol)
            for row in cursor.fetchall():
                price = float(row.CenaJednostkowaNetto) if row.CenaJednostkowaNetto is not None else 0.0
                data.append({
                    'symbol': row.Symbol.upper().strip(), 'name': row.Nazwa.strip(), 'price': price,
                    'price_date': row.DataOstatniegoZakupu.strftime('%Y-%m-%d') if row.DataOstatniegoZakupu else None
                })
        log_message(queue, f"Pobrano dane dla {len(data)} towarów.", color='green')
        return data
    except Exception as e:
        log_message(queue, f"Błąd podczas pobierania danych: {e}", color='red', level='error')
        return None

def send_prices_to_webapp(queue, config_data, data_to_send):
    url = f"{config_data.get('web_app_url')}/api/v1/update-prices"
    headers = {'Content-Type': 'application/json', 'X-API-KEY': config_data.get('api_key')}
    data_for_api = [{'symbol': item['symbol'], 'price': item.get('price')} for item in data_to_send if item.get('price') is not None]
    if not data_for_api:
        log_message(queue, "Brak cen do zaktualizowania.", color='orange', level='warning'); return True
    log_message(queue, f"Wysyłanie {len(data_for_api)} aktualizacji cen...")
    try:
        response = requests.post(url, headers=headers, data=json.dumps(data_for_api), timeout=30)
        if response.status_code == 200:
            msg = response.json().get('message', 'Ceny zaktualizowane.')
            log_message(queue, f"SUKCES! {msg}", color='green')
            return True
        else:
            log_message(queue, f"BŁĄD CEN: {response.status_code} - {response.text}", color='red', level='error'); return False
    except requests.exceptions.RequestException as e:
        log_message(queue, f"KRYTYCZNY BŁĄD CEN: {e}", color='red', level='error'); return False

def send_catalog_to_webapp(queue, config_data, data_to_send):
    url = f"{config_data.get('web_app_url')}/api/v1/receive-subiekt-catalog"
    headers = {'Content-Type': 'application/json', 'X-API-KEY': config_data.get('api_key')}
    catalog_for_api = [{'symbol': item['symbol'], 'name': item.get('name')} for item in data_to_send]
    if not catalog_for_api:
        log_message(queue, "Brak katalogu do wysłania.", color='orange', level='warning'); return True
    log_message(queue, f"Wysyłanie {len(catalog_for_api)} towarów do zmapowania...")
    try:
        response = requests.post(url, headers=headers, data=json.dumps(catalog_for_api), timeout=30)
        if response.status_code == 200:
            msg = response.json().get('message', 'Katalog wysłany.')
            log_message(queue, f"SUKCES! {msg}", color='green')
            return True
        else:
            log_message(queue, f"BŁĄD KATALOGU: {response.status_code} - {response.text}", color='red', level='error'); return False
    except requests.exceptions.RequestException as e:
        log_message(queue, f"KRYTYCZNY BŁĄD KATALOGU: {e}", color='red', level='error'); return False

def full_sync_task(queue, config_data):
    warehouse_full_name = config_data.get('default_warehouse')
    if not warehouse_full_name:
        log_message(queue, "BŁĄD: Brak domyślnego magazynu.", color='red', level='error'); return
    
    warehouse_symbol = warehouse_full_name.split(' ')[0]
    log_message(queue, f"\n--- Rozpoczynam Pełną Synchronizację ({warehouse_symbol}) ---", color='blue')
    data = get_data_from_warehouse(queue, config_data, warehouse_symbol)
    if data is None:
        log_message(queue, "Synchronizacja przerwana.", color='red', level='error'); return

    log_message(queue, "\nKrok 1: Wysyłanie katalogu...", color='blue')
    catalog_success = send_catalog_to_webapp(queue, config_data, data)
    
    log_message(queue, "\nKrok 2: Aktualizacja cen...", color='blue')
    prices_success = send_prices_to_webapp(queue, config_data, data)
    
    if catalog_success and prices_success:
        log_message(queue, "\n--- PEŁNA SYNCHRONIZACJA ZAKOŃCZONA SUKCESEM ---", color='green')
    else:
        log_message(queue, "\n--- PEŁNA SYNCHRONIZACJA ZAKOŃCZONA Z BŁĘDAMI ---", color='red', level='error')

def scheduler_thread_func(queue, config_data):
    log_message(queue, "Wątek harmonogramu uruchomiony.", color='gray')
    update_time = config_data.get("update_time", "12:00")
    try:
        schedule.every().day.at(update_time).do(full_sync_task, queue, config_data)
    except schedule.ScheduleError:
        log_message(queue, f"BŁĄD: Nieprawidłowy format czasu '{update_time}'. Użyj formatu HH:MM.", color='red', level='error')

    while not stop_scheduler_thread.is_set():
        schedule.run_pending()
        time.sleep(1)
    
    log_message(queue, "Wątek harmonogramu zatrzymany.", color='gray')

def create_tray_image():
    width, height = 64, 64
    image = Image.new('RGB', (width, height), '#333333')
    dc = ImageDraw.Draw(image)
    dc.line([(10, 22), (32, 44), (54, 22)], fill='#4CAF50', width=8)
    return image

def show_window():
    if root: root.after(0, root.deiconify)

def exit_app():
    stop_scheduler_thread.set()
    if tray_icon: tray_icon.stop()
    if root: root.destroy()

class App:
    def __init__(self, tk_root, config_data):
        self.root = tk_root
        self.root.title("Synchronizator Szwalnia-Subiekt")
        
        self.config = config_data
        self.server_var = tk.StringVar(value=self.config.get('server', ''))
        self.database_var = tk.StringVar(value=self.config.get('database', ''))
        self.sql_user_var = tk.StringVar(value=self.config.get('sql_user', ''))
        self.sql_password_var = tk.StringVar(value=self.config.get('sql_password', ''))
        self.web_app_url_var = tk.StringVar(value=self.config.get('web_app_url', ''))
        self.api_key_var = tk.StringVar(value=self.config.get('api_key', ''))
        self.warehouse_var = tk.StringVar(value=self.config.get('default_warehouse', ''))
        self.autostart_var = tk.BooleanVar(value=self.config.get('autostart', False))
        self.update_time_var = tk.StringVar(value=self.config.get('update_time', '12:00'))
        
        main_frame = tk.Frame(root, padx=10, pady=10)
        main_frame.pack(fill='both', expand=True)
        
        sync_frame = ttk.LabelFrame(main_frame, text="Automatyczna Synchronizacja")
        sync_frame.pack(fill='x', pady=5, ipady=10)
        self.full_sync_button = ttk.Button(sync_frame, text="🚀 Ręczna Pełna Synchronizacja", command=self.run_full_sync)
        self.full_sync_button.pack(expand=True, fill='x', padx=5, pady=5)
        
        manual_frame = ttk.LabelFrame(main_frame, text="Konfiguracja i Kroki Ręczne")
        manual_frame.pack(fill='x', pady=5)
        
        config_frame = tk.Frame(manual_frame); config_frame.pack(fill='x', padx=5, pady=5)
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
        
        data_source_frame = tk.Frame(manual_frame); data_source_frame.pack(fill='x', padx=5, pady=5)
        self.warehouse_combo = ttk.Combobox(data_source_frame, textvariable=self.warehouse_var, state='disabled')
        self.warehouse_combo.pack(side='left', fill='x', expand=True, padx=5, pady=5)
        self.connect_button = ttk.Button(data_source_frame, text="Wczytaj magazyny", command=self.run_load_warehouses)
        self.connect_button.pack(side='left', padx=5, pady=5)
        
        auto_frame = ttk.LabelFrame(manual_frame, text="Ustawienia Automatyzacji")
        auto_frame.pack(fill='x', pady=(10,5), ipady=5, padx=5)
        ttk.Checkbutton(auto_frame, text="Uruchom program przy starcie systemu Windows", variable=self.autostart_var).pack(anchor='w', padx=5)
        time_frame = tk.Frame(auto_frame); time_frame.pack(fill='x', padx=5, pady=5)
        ttk.Label(time_frame, text="Synchronizuj codziennie o godzinie:").pack(side='left')
        ttk.Entry(time_frame, textvariable=self.update_time_var, width=10).pack(side='left', padx=5)

        btn_config_frame = tk.Frame(manual_frame); btn_config_frame.pack(fill='x', pady=5, padx=5)
        self.test_button = ttk.Button(btn_config_frame, text="Testuj Połączenie", command=self.run_test_connection)
        self.test_button.pack(side='left', padx=5)
        ttk.Button(btn_config_frame, text="Zapisz Konfigurację", command=self.save_current_config).pack(side='left', padx=5)
        self.fetch_button = ttk.Button(btn_config_frame, text="Pobierz i Wyślij Ręcznie...", command=self.run_fetch_data)
        self.fetch_button.pack(side='right', padx=5)

        log_frame = ttk.LabelFrame(main_frame, text="Log operacji")
        log_frame.pack(fill='both', expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, state='disabled', wrap=tk.WORD, height=10)
        self.log_text.pack(padx=5, pady=5, expand=True, fill='both')
        self.log_text.tag_config('green', foreground='#4CAF50'); self.log_text.tag_config('red', foreground='#F44336'); self.log_text.tag_config('orange', foreground='#FF9800'); self.log_text.tag_config('gray', foreground='#9E9E9E'); self.log_text.tag_config('blue', foreground='#2196F3')
        
        # NOWY PRZYCISK DO OTWIERANIA LOGU
        ttk.Button(log_frame, text="Otwórz plik logu", command=self.open_log_file).pack(side='bottom', pady=5)

        self.queue = main_queue
        self.root.after(100, self.process_queue)
        self.update_full_sync_button_state()

    def open_log_file(self):
        try:
            if platform.system() == "Windows": os.startfile(log_file)
            elif platform.system() == "Darwin": subprocess.Popen(["open", log_file])
            else: subprocess.Popen(["xdg-open", log_file])
        except Exception as e:
            log_message(self.queue, f"Nie można otworzyć pliku logu: {e}", color='red', level='error')

    def get_current_config(self):
        return {
            'server': self.server_var.get(), 'database': self.database_var.get(), 'sql_user': self.sql_user_var.get(), 
            'sql_password': self.sql_password_var.get(), 'web_app_url': self.web_app_url_var.get(), 'api_key': self.api_key_var.get(),
            'default_warehouse': self.warehouse_var.get(),
            'autostart': self.autostart_var.get(), 'update_time': self.update_time_var.get()
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
                self.log_text.configure(state='normal'); self.log_text.insert(tk.END, data['msg'], data['color']); self.log_text.configure(state='disabled'); self.log_text.see(tk.END)
        except queue.Empty: pass
        self.root.after(100, self.process_queue)
    
    def update_full_sync_button_state(self):
        self.full_sync_button.config(state='normal' if config.get('default_warehouse') else 'disabled')

    def run_full_sync(self):
        threading.Thread(target=full_sync_task, args=(self.queue, self.get_current_config()), daemon=True).start()

    def run_load_warehouses(self):
        def task_wrapper():
            warehouses = get_warehouses_from_sql(self.queue, self.get_current_config())
            if warehouses:
                self.warehouse_combo['values'] = warehouses
                self.warehouse_combo.config(state='readonly')
                saved_warehouse = config.get('default_warehouse')
                if saved_warehouse in warehouses: self.warehouse_var.set(saved_warehouse)
                elif warehouses: self.warehouse_var.set(warehouses[0])
        threading.Thread(target=task_wrapper, daemon=True).start()

    def run_test_connection(self):
        threading.Thread(target=test_sql_connection, args=(self.queue, self.get_current_config()), daemon=True).start()

    def run_fetch_data(self):
        if not self.warehouse_var.get(): 
            messagebox.showwarning("Brak magazynu", "Najpierw wczytaj i wybierz magazyn."); return
        
        def task_wrapper():
            data = get_data_from_warehouse(self.queue, self.get_current_config(), self.warehouse_var.get().split(' ')[0])
            if data is not None:
                self.root.after(0, self.show_review_window, data)
        threading.Thread(target=task_wrapper, daemon=True).start()
        
    def show_review_window(self, data):
        review_window = tk.Toplevel(self.root)
        review_window.title(f"Podgląd Danych ({len(data)} pozycji)")
        review_window.geometry("800x500")
        cols = ('Symbol', 'Nazwa', 'Cena Netto', 'Data Ceny')
        tree = ttk.Treeview(review_window, columns=cols, show='headings')
        tree.heading('Symbol', text='Symbol'); tree.column('Symbol', width=150)
        tree.heading('Nazwa', text='Nazwa'); tree.column('Nazwa', width=350)
        tree.heading('Cena Netto', text='Cena Netto'); tree.column('Cena Netto', width=100, anchor='e')
        tree.heading('Data Ceny', text='Data Ceny'); tree.column('Data Ceny', width=100, anchor='center')
        tree.pack(expand=True, fill='both', padx=10, pady=5)
        for item in data:
            tree.insert("", "end", values=(item['symbol'], item['name'], f"{item.get('price', 0.0):.2f} zł", item.get('price_date', 'Brak')))
        def send_catalog_action():
            review_window.destroy(); threading.Thread(target=send_catalog_to_webapp, args=(self.queue, self.get_current_config(), data), daemon=True).start()
        def send_prices_action():
            review_window.destroy(); threading.Thread(target=send_prices_to_webapp, args=(self.queue, self.get_current_config(), data), daemon=True).start()
        button_frame = tk.Frame(review_window); button_frame.pack(pady=10)
        ttk.Button(button_frame, text=f"Wyślij katalog ({len(data)}) do zmapowania", command=send_catalog_action).pack(side='left', padx=10)
        ttk.Button(button_frame, text=f"Aktualizuj ceny istniejących", command=send_prices_action).pack(side='left', padx=10)

if __name__ == "__main__":
    menu = (item('Pokaż', show_window, default=True), item('Zakończ', exit_app))
    tray_icon = icon('Synchronizator', create_tray_image(), "Synchronizator Szwalnia", menu)

    if '--background' not in sys.argv:
        root = tk.Tk()
        app = App(root, config)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        threading.Thread(target=scheduler_thread_func, args=(main_queue, config), daemon=True).start()
        threading.Thread(target=tray_icon.run, daemon=True).start()
        root.mainloop()
    else:
        threading.Thread(target=scheduler_thread_func, args=(main_queue, config), daemon=True).start()
        tray_icon.run()