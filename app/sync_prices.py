import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
import requests
import json
import threading
import queue
import os
import pyodbc

# --- LOGIKA KONFIGURACJI ---
CONFIG_FILE = 'config.json'

def save_config(data):
    """Zapisuje dane konfiguracyjne do pliku JSON."""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        return True
    except Exception as e:
        messagebox.showerror("Błąd zapisu", f"Nie można zapisać konfiguracji: {e}")
        return False

def load_config():
    """Wczytuje dane konfiguracyjne z pliku JSON."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

# --- LOGIKA APLIKACJI (SQL i API) ---

def log_message(queue, message, color='black'):
    """Wysyła wiadomość do kolejki, aby GUI mogło ją bezpiecznie wyświetlić."""
    queue.put({'msg': message + '\n', 'color': color})

def get_connection_string(config):
    """Tworzy connection string na podstawie konfiguracji."""
    return (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={config.get('server')};"
        f"DATABASE={config.get('database')};"
        f"UID={config.get('sql_user')};"
        f"PWD={config.get('sql_password')};"
    )

def test_sql_connection(queue, config):
    """Testuje połączenie z bazą danych SQL."""
    log_message(queue, "Testowanie połączenia z bazą danych SQL...")
    try:
        conn_str = get_connection_string(config)
        with pyodbc.connect(conn_str, timeout=5) as connection:
            log_message(queue, "SUKCES! Połączenie z bazą danych działa poprawnie.", color='green')
    except Exception as e:
        log_message(queue, f"BŁĄD! Nie można nawiązać połączenia.", color='red')
        log_message(queue, str(e), color='red')

def get_warehouses_from_sql(queue, config):
    """Pobiera listę magazynów z bazy danych."""
    log_message(queue, "Pobieranie listy magazynów z bazy danych...")
    query = "SELECT Symbol, Nazwa FROM ModelDanychContainer.Magazyny ORDER BY Symbol"
    warehouses = []
    try:
        conn_str = get_connection_string(config)
        with pyodbc.connect(conn_str) as connection:
            cursor = connection.cursor()
            cursor.execute(query)
            for row in cursor.fetchall():
                warehouses.append(f"{row.Symbol.strip()} ({row.Nazwa.strip()})")
        log_message(queue, f"Pobrano {len(warehouses)} magazynów.", color='green')
        return warehouses
    except Exception as e:
        log_message(queue, f"Błąd podczas pobierania magazynów: {e}", color='red')
        return []

def get_data_from_warehouse(queue, config, warehouse_symbol):
    """Pobiera towary, ich ostatnią cenę zakupu i datę tej ceny."""
    log_message(queue, f"Pobieranie towarów i cen zakupu z magazynu: {warehouse_symbol}...")
    data_to_update = []
    
    query = """
        WITH LastPurchaseCost AS (
            SELECT 
                p.Asortyment_Id,
                k.Wartosc,
                k.Ilosc,
                k.Data,
                ROW_NUMBER() OVER(PARTITION BY p.Asortyment_Id ORDER BY k.Data DESC, k.Lp DESC) as rn
            FROM ModelDanychContainer.Przyjecia AS p
            INNER JOIN ModelDanychContainer.KosztyZakupu AS k ON p.KosztPierwotny_Id = k.Id
        )
        SELECT 
            a.Symbol, 
            a.Nazwa,
            lpc.Data AS DataOstatniegoZakupu,
            (lpc.Wartosc / lpc.Ilosc) AS CenaJednostkowaNetto
        FROM ModelDanychContainer.Asortymenty AS a
        INNER JOIN ModelDanychContainer.StanyMagazynowe AS sm ON a.Id = sm.Asortyment_Id
        INNER JOIN ModelDanychContainer.Magazyny AS m ON sm.Magazyn_Id = m.Id
        LEFT JOIN LastPurchaseCost lpc ON a.Id = lpc.Asortyment_Id AND lpc.rn = 1
        WHERE m.Symbol = ?
    """
    
    try:
        conn_str = get_connection_string(config)
        with pyodbc.connect(conn_str) as connection:
            cursor = connection.cursor()
            cursor.execute(query, warehouse_symbol)
            for row in cursor.fetchall():
                price = float(row.CenaJednostkowaNetto) if row.CenaJednostkowaNetto is not None else 0.0
                price_date = row.DataOstatniegoZakupu.strftime('%Y-%m-%d') if row.DataOstatniegoZakupu is not None else None
                
                data_to_update.append({
                    'symbol': row.Symbol.upper().strip(),
                    'name': row.Nazwa.strip(),
                    'price': price,
                    'price_date': price_date
                })

        log_message(queue, f"Pobrano dane dla {len(data_to_update)} towarów.", color='green')
        return data_to_update
    except Exception as e:
        log_message(queue, f"Błąd podczas pobierania danych: {e}", color='red')
        return None

def send_data_to_webapp(queue, config, data_to_send):
    """Wysyła dane do API aplikacji webowej."""
    web_app_url = config.get('web_app_url')
    api_key = config.get('api_key')
    url = f"{web_app_url}/api/v1/update-prices"
    headers = {'Content-Type': 'application/json', 'X-API-KEY': api_key}
    
    # --- POCZĄTEK POPRAWKI ---
    # Tworzymy nową listę, zawierającą tylko 'symbol' i 'price'
    data_for_api = [
        {'symbol': item['symbol'], 'price': item.get('price')}
        for item in data_to_send
        if item.get('price') is not None
    ]
    # --- KONIEC POPRAWKI ---

    if not data_for_api:
        log_message(queue, "Brak danych z cenami do wysłania.", color='orange')
        return
    
    log_message(queue, f"Wysyłanie {len(data_for_api)} pozycji do aplikacji webowej...")
    try:
        # Wysyłamy nową, przefiltrowaną listę
        response = requests.post(url, headers=headers, data=json.dumps(data_for_api), timeout=30)
        
        if response.status_code == 200:
            log_message(queue, "SUKCES! Aplikacja webowa potwierdziła aktualizację.", color='green')
            log_message(queue, response.json().get('message'))
        else:
            log_message(queue, f"BŁĄD: Serwer odpowiedział z kodem {response.status_code}.", color='red')
            log_message(queue, f"Odpowiedź serwera: {response.text}")
            
    except requests.exceptions.RequestException as e:
        log_message(queue, f"KRYTYCZNY BŁĄD: Nie można połączyć się z serwerem. Błąd: {e}", color='red')

# --- INTERFEJS GRAFICZNY (GUI) z Tkinter ---
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Synchronizator Szwalnia-Subiekt (SQL Edition)")
        
        self.config = load_config()
        self.server_var = tk.StringVar(value=self.config.get('server', 'HOXA-SERWER\\INSERTNEXO'))
        self.database_var = tk.StringVar(value=self.config.get('database', ''))
        self.sql_user_var = tk.StringVar(value=self.config.get('sql_user', 'sa'))
        self.sql_password_var = tk.StringVar(value=self.config.get('sql_password', ''))
        self.web_app_url_var = tk.StringVar(value=self.config.get('web_app_url', 'http://127.0.0.1:5000'))
        self.api_key_var = tk.StringVar(value=self.config.get('api_key', 'super-tajne-haslo-do-zmiany-123'))
        self.warehouse_var = tk.StringVar()
        
        main_frame = tk.Frame(root, padx=10, pady=10)
        main_frame.pack(fill='both', expand=True)
        
        config_frame = ttk.LabelFrame(main_frame, text="Konfiguracja Połączeń")
        config_frame.pack(fill='x', pady=5)
        
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

        btn_config_frame = tk.Frame(config_frame)
        btn_config_frame.grid(row=6, column=1, sticky='e', pady=5)
        self.test_button = ttk.Button(btn_config_frame, text="Testuj Połączenie", command=self.run_test_connection)
        self.test_button.pack(side='left', padx=5)
        ttk.Button(btn_config_frame, text="Zapisz Konfigurację", command=self.save_current_config).pack(side='left', padx=5)
        
        data_source_frame = ttk.LabelFrame(main_frame, text="Krok 1: Wybór Magazynu")
        data_source_frame.pack(fill='x', pady=5)
        self.warehouse_combo = ttk.Combobox(data_source_frame, textvariable=self.warehouse_var, state='disabled')
        self.warehouse_combo.pack(side='left', fill='x', expand=True, padx=5, pady=5)
        self.connect_button = ttk.Button(data_source_frame, text="Wczytaj magazyny", command=self.run_load_warehouses)
        self.connect_button.pack(side='left', padx=5, pady=5)
        
        action_frame = ttk.LabelFrame(main_frame, text="Krok 2: Pobranie i Wysyłka Danych")
        action_frame.pack(fill='x', pady=5)
        self.fetch_button = ttk.Button(action_frame, text="Pobierz dane z magazynu i przygotuj do wysyłki", command=self.run_fetch_data)
        self.fetch_button.pack(expand=True, fill='x', padx=5, pady=5)
        
        log_frame = ttk.LabelFrame(main_frame, text="Log operacji")
        log_frame.pack(fill='both', expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, state='disabled', wrap=tk.WORD, height=10)
        self.log_text.pack(padx=5, pady=5, expand=True, fill='both')
        self.log_text.tag_config('green', foreground='#4CAF50'); self.log_text.tag_config('red', foreground='#F44336'); self.log_text.tag_config('orange', foreground='#FF9800'); self.log_text.tag_config('gray', foreground='#9E9E9E'); self.log_text.tag_config('blue', foreground='#2196F3')

        self.queue = queue.Queue()
        self.root.after(100, self.process_queue)

    def get_current_config(self):
        return {'server': self.server_var.get(), 'database': self.database_var.get(), 'sql_user': self.sql_user_var.get(), 'sql_password': self.sql_password_var.get(),
                'web_app_url': self.web_app_url_var.get(), 'api_key': self.api_key_var.get()}
    
    def save_current_config(self):
        if save_config(self.get_current_config()): messagebox.showinfo("Sukces", "Konfiguracja została zapisana.")
    
    def process_queue(self):
        try:
            while True:
                data = self.queue.get_nowait()
                self.log_text.configure(state='normal'); self.log_text.insert(tk.END, data['msg'], data['color']); self.log_text.configure(state='disabled'); self.log_text.see(tk.END)
        except queue.Empty: pass
        self.root.after(100, self.process_queue)

    def set_buttons_state(self, state):
        current_state = 'normal' if state else 'disabled'
        self.test_button.config(state=current_state)
        self.connect_button.config(state=current_state)
        self.fetch_button.config(state=current_state)

    def run_task_in_thread(self, target_func, *args):
        self.set_buttons_state(False)
        def task_wrapper():
            target_func(self.queue, *args)
            log_message(self.queue, "--- Gotowe ---", color='gray')
            self.set_buttons_state(True)
        thread = threading.Thread(target=task_wrapper, daemon=True); thread.start()

    def run_test_connection(self):
        self.run_task_in_thread(test_sql_connection, self.get_current_config())

    def run_load_warehouses(self):
        def task_wrapper():
            warehouses = get_warehouses_from_sql(self.queue, self.get_current_config())
            if warehouses:
                self.warehouse_combo['values'] = warehouses
                self.warehouse_combo.config(state='readonly')
                if warehouses: self.warehouse_var.set(warehouses[0])
            log_message(self.queue, "--- Gotowe ---", color='gray')
            self.set_buttons_state(True)
        self.set_buttons_state(False); thread = threading.Thread(target=task_wrapper, daemon=True); thread.start()
        
    def run_fetch_data(self):
        warehouse_full_name = self.warehouse_var.get()
        if not warehouse_full_name: 
            messagebox.showwarning("Brak magazynu", "Najpierw wczytaj i wybierz magazyn."); 
            return
        warehouse_symbol = warehouse_full_name.split(' ')[0]
        
        def task_wrapper():
            data = get_data_from_warehouse(self.queue, self.get_current_config(), warehouse_symbol)
            if data is not None:
                self.root.after(0, self.show_review_window, data)
            self.set_buttons_state(True)
        
        log_message(self.queue, "\n--- Rozpoczynam pobieranie danych ---")
        self.set_buttons_state(False); 
        thread = threading.Thread(target=task_wrapper, daemon=True); 
        thread.start()
        
    def show_review_window(self, data):
        review_window = tk.Toplevel(self.root)
        review_window.title(f"Podgląd Danych do Wysyłki ({len(data)} pozycji)")
        review_window.geometry("800x500")
        
        cols = ('Symbol', 'Nazwa', 'Cena Netto', 'Data Ceny')
        tree = ttk.Treeview(review_window, columns=cols, show='headings')
        tree.heading('Symbol', text='Symbol'); tree.column('Symbol', width=150)
        tree.heading('Nazwa', text='Nazwa'); tree.column('Nazwa', width=350)
        tree.heading('Cena Netto', text='Cena Netto'); tree.column('Cena Netto', width=100, anchor='e')
        tree.heading('Data Ceny', text='Data Ceny'); tree.column('Data Ceny', width=100, anchor='center')
        tree.pack(expand=True, fill='both', padx=10, pady=5)

        for item in data:
            price_str = f"{item.get('price', 0.0):.2f} zł"
            date_str = item.get('price_date', 'Brak')
            tree.insert("", "end", values=(item['symbol'], item['name'], price_str, date_str))
        
        def send_action():
            review_window.destroy()
            self.run_task_in_thread(send_data_to_webapp, self.get_current_config(), data)

        send_button = ttk.Button(review_window, text=f"Wyślij {len(data)} pozycji na serwer", command=send_action)
        send_button.pack(pady=10)

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()