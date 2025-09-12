# app/doc_generator.py

from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
import os
from datetime import datetime

def set_run_properties(run, bold=False, size=11):
    run.font.name = 'Calibri'
    run.font.size = Pt(size)
    run.bold = bold

def save_order_as_word(order, material_summary, folder_path='app/order_docs'):
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    
    document = Document()
    
    # Nagłówek
    header = document.add_paragraph()
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = header.add_run(f"Zlecenie Produkcyjne: {order.order_code}")
    set_run_properties(run, bold=True, size=16)

    # Informacje podstawowe
    p = document.add_paragraph()
    set_run_properties(p.add_run('Klient: '), bold=True)
    set_run_properties(p.add_run(f'{order.client.name}\n'))
    set_run_properties(p.add_run('Termin realizacji: '), bold=True)
    set_run_properties(p.add_run(f'{order.deadline.strftime("%Y-%m-%d")}\n'))
    set_run_properties(p.add_run('Zlecający: '), bold=True)
    set_run_properties(p.add_run(f'{order.zlecajacy}'))

    # --- ZMIANA: Obsługa wielu tkanin ---
    p = document.add_paragraph()
    set_run_properties(p.add_run('Tkaniny:\n'), bold=True)
    # Tworzymy listę z nazwami tkanin
    fabric_names = [of.fabric.name for of in order.fabrics]
    # Dołączamy je jako string z przecinkami
    set_run_properties(p.add_run(', '.join(fabric_names)))
    # --- KONIEC ZMIANY ---

    # Opis i logowanie
    document.add_heading('Opis zlecenia', level=1)
    document.add_paragraph(order.description)

    if order.login_info:
        document.add_heading('Informacje do logowania', level=1)
        document.add_paragraph(order.login_info)

    # Tabela z produktami
    document.add_heading('Zamówione produkty', level=1)
    table = document.add_table(rows=1, cols=3)
    table.style = 'Table Grid'
    hdr_cells = table.rows[0].cells
    set_run_properties(hdr_cells[0].paragraphs[0].add_run('Produkt'), bold=True)
    set_run_properties(hdr_cells[1].paragraphs[0].add_run('Rozmiar'), bold=True)
    set_run_properties(hdr_cells[2].paragraphs[0].add_run('Ilość'), bold=True)

    for item in order.order_items:
        row_cells = table.add_row().cells
        set_run_properties(row_cells[0].paragraphs[0].add_run(item.product.name))
        set_run_properties(row_cells[1].paragraphs[0].add_run(item.size))
        set_run_properties(row_cells[2].paragraphs[0].add_run(str(item.quantity)))

    # Tabela z materiałami
    if material_summary:
        document.add_heading('Planowane zużycie materiałów', level=1)
        mat_table = document.add_table(rows=1, cols=2)
        mat_table.style = 'Table Grid'
        mat_hdr_cells = mat_table.rows[0].cells
        set_run_properties(mat_hdr_cells[0].paragraphs[0].add_run('Materiał'), bold=True)
        set_run_properties(mat_hdr_cells[1].paragraphs[0].add_run('Ilość'), bold=True)

        for material in material_summary:
            row_cells = mat_table.add_row().cells
            set_run_properties(row_cells[0].paragraphs[0].add_run(material['name']))
            set_run_properties(row_cells[1].paragraphs[0].add_run(material['quantity']))

    # Zapis pliku
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    filename = f"{order.order_code.replace('/', '_')}_{timestamp}.docx"
    filepath = os.path.join(folder_path, filename)
    document.save(filepath)
    
    return filepath