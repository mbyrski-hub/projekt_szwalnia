import tkinter as tk
from tkinter import scrolledtext, messagebox
import socket
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
from PIL import Image, ImageDraw
import io
import win32print
import sys
import datetime
import logging
import os
import pystray
import win32com.client
from zebrafy import ZebrafyImage

# --- Konfiguracja ---
HOST = '0.0.0.0'
PORT = 5001
PRINTER_DPI = 203 # Nie zmieniamy, to stała wartość dla drukarki
APP_NAME = "SerwerDrukuSzwalnia"

# --- Zmienne globalne ---
flask_thread = None
tray_icon = None
APP_CONFIG = {"SELECTED_PRINTER": None}

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

# ... (funkcje autostartu bez zmian) ...
def get_startup_folder():
    return os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming', 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')

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
    # ... (bez zmian) ...
    def __init__(self, text_widget):
        self.text_widget = text_widget
    def write(self, message):
        if message.strip():
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.text_widget.after(0, self._insert_text, f"[{timestamp}] {message}\n")
    def _insert_text(self, message):
        self.text_widget.insert(tk.END, message)
        self.text_widget.see(tk.END)
    def flush(self):
        pass

# --- Rdzeń serwera drukowania (logika Flask) ---
flask_app = Flask(__name__)
CORS(flask_app)

@flask_app.route('/print_label', methods=['POST'])
def print_label():
    log_handler = flask_app.config['LOG_HANDLER']
    log_handler.write("Odebrano nowe żądanie drukowania...")
    
    selected_printer = APP_CONFIG.get("SELECTED_PRINTER")
    if not selected_printer:
        return jsonify({'error': 'Drukarka nie została wybrana na serwerze druku.'}), 500

    if 'image' not in request.files:
        return jsonify({'error': 'Brak pliku obrazu'}), 400

    # ### POCZĄTEK POPRAWKI ###
    # Odczytujemy wymiary z przesłanego formularza
    try:
        width_mm = float(request.form.get('width_mm'))
        height_mm = float(request.form.get('height_mm'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Brak lub niepoprawne wymiary etykiety w żądaniu.'}), 400
    # ### KONIEC POPRAWKI ###
    
    try:
        img_bytes = request.files['image'].read()
        log_handler.write(f"Odebrano plik ({len(img_bytes)} bajtów) z wymiarami: {width_mm}x{height_mm}mm")
        
        # Obliczamy wymiary w "kropkach"
        print_width_dots = int((width_mm / 25.4) * PRINTER_DPI)
        label_height_dots = int((height_mm / 25.4) * PRINTER_DPI)

        log_handler.write(f"Wymiary w kropkach: {print_width_dots}x{label_height_dots} przy {PRINTER_DPI} DPI")
        
        # Tworzymy nagłówek ZPL z komendami konfiguracyjnymi
        zpl_header = f"""
        ^XA
        ^PW{print_width_dots}
        ^LL{label_height_dots}
        """

        z_image = ZebrafyImage(img_bytes)
        image_zpl = z_image.to_zpl().replace("^XA", "").replace("^XZ", "")
        zpl_code = zpl_header + image_zpl + "^XZ"
        # ### POCZĄTEK ZMIANY - TUTAJ WKLEJ LINIĘ ###
        log_handler.write(f"--- KOD ZPL DO WERYFIKACJI ---\n{zpl_code}\n---------------------------------")
        # ### KONIEC ZMIANY ###
        log_handler.write(f"Wysyłanie do drukarki: {selected_printer}...")
        h_printer = win32print.OpenPrinter(selected_printer)
        try:
            win32print.StartDocPrinter(h_printer, 1, ("Label ZPL", None, "RAW"))
            win32print.WritePrinter(h_printer, zpl_code.encode())
            win32print.EndDocPrinter(h_printer)
        finally:
            win32print.ClosePrinter(h_printer)
        
        log_handler.write("Wydruk wysłany pomyślnie!")
        return jsonify({'status': 'Wydrukowano pomyślnie'})
        
    except Exception as e:
        log_handler.write(f"KRYTYCZNY BŁĄD PODCZAS DRUKOWANIA: {e}")
        return jsonify({'error': str(e)}), 500

# --- Interfejs Graficzny (GUI Tkinter) ---
class PrintServerApp:
    # ... (cała reszta klasy GUI pozostaje bez zmian) ...
    def __init__(self, root):
        self.root = root
        self.root.title("Lokalny Serwer Drukowania Etykiet")
        self.root.geometry("600x500")
        self.root.minsize(500, 400)
        self.tray_icon = None
        info_frame = tk.Frame(root, pady=10)
        info_frame.pack(fill='x', padx=10)
        printer_label = tk.Label(info_frame, text="Wybierz drukarkę etykiet:")
        printer_label.pack()
        self.available_printers = get_available_printers()
        self.selected_printer = tk.StringVar(root)
        if self.available_printers:
            self.selected_printer.set(self.available_printers[0])
            APP_CONFIG["SELECTED_PRINTER"] = self.available_printers[0]
        else:
            self.available_printers.append("Brak drukarek")
            self.selected_printer.set("Brak drukarek")
        self.printer_menu = tk.OptionMenu(info_frame, self.selected_printer, *self.available_printers, command=self.on_printer_select)
        self.printer_menu.pack(pady=5, fill='x')
        self.ip_label = tk.Label(info_frame, text="Twój adres IP w sieci lokalnej: ?")
        self.ip_label.pack()
        self.ip_button = tk.Button(info_frame, text="Odśwież adres IP", command=self.update_ip)
        self.ip_button.pack(pady=5)
        self.status_label = tk.Label(info_frame, text="Status serwera: Zatrzymany", fg="red", font=("Helvetica", 10, "bold"))
        self.status_label.pack()
        self.startup_var = tk.BooleanVar()
        startup_shortcut_path = os.path.join(get_startup_folder(), f"{APP_NAME}.lnk")
        self.startup_var.set(os.path.exists(startup_shortcut_path))
        self.startup_check = tk.Checkbutton(info_frame, text="Uruchom przy starcie systemu Windows", var=self.startup_var, command=self.toggle_startup)
        self.startup_check.pack(pady=5)
        button_frame = tk.Frame(root, pady=10)
        button_frame.pack(fill='x')
        self.start_button = tk.Button(button_frame, text="Start", command=self.start_server, bg="lightgreen", height=2)
        self.start_button.pack(side='left', expand=True, fill='x', padx=20)
        self.stop_button = tk.Button(button_frame, text="Stop", command=self.stop_server, bg="salmon", state=tk.DISABLED, height=2)
        self.stop_button.pack(side='right', expand=True, fill='x', padx=20)
        log_frame = tk.Frame(root, padx=10, pady=10)
        log_frame.pack(expand=True, fill='both')
        self.log_widget = scrolledtext.ScrolledText(log_frame, state='normal', wrap=tk.WORD, bg="#f0f0f0")
        self.log_widget.pack(expand=True, fill='both')
        self.log_handler = LogHandler(self.log_widget)
        flask_app.config['LOG_HANDLER'] = self.log_handler
        self.update_ip()
        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)
        self.log_handler.write("Aplikacja gotowa. Wybierz drukarkę i wciśnij 'Start'.")

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
        self.tray_icon = pystray.Icon(APP_NAME, image, "Serwer Drukowania", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
        
    def on_closing(self, force=False):
        if force or messagebox.askokcancel("Zamknij", "Czy na pewno chcesz zamknąć serwer drukowania?"):
            if self.tray_icon:
                self.tray_icon.stop()
            self.root.destroy()
            os._exit(0)

    def on_printer_select(self, selected_value):
        APP_CONFIG["SELECTED_PRINTER"] = selected_value
        self.log_handler.write(f"Wybrano drukarkę: {selected_value}")

    def update_ip(self):
        ip = get_local_ip()
        self.ip_label.config(text=f"Twój adres IP w sieci lokalnej: {ip}")
        self.log_handler.write(f"Sprawdzono IP: {ip}. Ten adres należy wpisać na stronie.")

    def start_server(self):
        if not APP_CONFIG.get("SELECTED_PRINTER") or APP_CONFIG.get("SELECTED_PRINTER") == "Brak drukarek":
            messagebox.showerror("Błąd", "Nie wybrano poprawnej drukarki!")
            return
        global flask_thread
        self.log_handler.write(f"Uruchamianie serwera na {HOST}:{PORT}...")
        def run_app():
            log = logging.getLogger('werkzeug')
            log.disabled = True
            flask_app.logger.disabled = True
            try:
                flask_app.run(host=HOST, port=PORT, debug=False)
            except Exception as e:
                self.log_handler.write(f"Błąd krytyczny serwera: {e}")
        flask_thread = threading.Thread(target=run_app, daemon=True)
        flask_thread.start()
        self.status_label.config(text="Status serwera: Działa", fg="green")
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.printer_menu.config(state=tk.DISABLED)
        self.log_handler.write("Serwer uruchomiony. Oczekuje na zlecenia wydruku...")

    def stop_server(self):
        self.log_handler.write("Zatrzymywanie serwera... Aplikacja zostanie zamknięta.")
        self.on_closing(force=True)

    def toggle_startup(self):
        is_enabled = self.startup_var.get()
        if getattr(sys, 'frozen', False):
             target_path = sys.executable
        else:
            messagebox.showinfo("Informacja", "Funkcja autostartu tworzy skrót do pliku .exe. Aby w pełni przetestować tę funkcję, najpierw skompiluj aplikację.")
            target_path = os.path.abspath(__file__)
        if is_enabled:
            if create_shortcut_in_startup(target_path, APP_NAME):
                self.log_handler.write("Dodano aplikację do autostartu.")
        else:
            if remove_shortcut_from_startup(APP_NAME):
                self.log_handler.write("Usunięto aplikację z autostartu.")

if __name__ == "__main__":
    root = tk.Tk()
    app = PrintServerApp(root)
    root.mainloop()