from flask import render_template, request, redirect, url_for, flash, send_from_directory, current_app, make_response, jsonify, send_file, after_this_request
from app import app, db
from app.models import Order, Client, Product, OrderItem, Attachment, OrderTemplate, Fabric, MaterialUsage, ProductMaterial, SubiektProductCache, Material, SubiektProductCache, ProductCategory
from app.forms import OrderForm, OrderTemplateForm, ProductForm, FabricForm, MaterialForm, ProductCategoryForm, MaterialEditForm
from werkzeug.utils import secure_filename
import os
import re
from datetime import datetime, date, timedelta
import pdfkit
from sqlalchemy import extract
from app.doc_generator import save_order_as_word
import platform
from PIL import Image
from collections import defaultdict
from sqlalchemy import func
import csv
import io # Potrzebny do odczytu pliku w pamięci
import pandas as pd
import json

if platform.system() == 'Windows':
    config = pdfkit.configuration(wkhtmltopdf=r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe')
else:
    config = pdfkit.configuration(wkhtmltopdf='/usr/bin/wkhtmltopdf')


# Dozwolone rozszerzenia dla załączników
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return redirect(url_for('orders_list'))

# NOWA FUNKCJA POMOCNICZA (wklej ją gdzieś na górze pliku, np. po importach)
# app/routes.py

def calculate_material_summary(order):
    """Oblicza sumaryczne zużycie materiałów i zwraca listę słowników."""
    summary = defaultdict(float)
    units = {}
    total_fabric_usage = 0.0

    # 1. Oblicz zużycie tkaniny (ta część jest poprawna)
    for item in order.order_items:
        if item.product:
            total_fabric_usage += item.product.fabric_usage_meters * item.quantity
    
    # 2. Oblicz zużycie dodatkowych materiałów
    for item in order.order_items:
        if not item.product or not item.product.materials_needed:
            continue
        
        # 'pm_link' to obiekt łączący Produkt z Materiałem
        for pm_link in item.product.materials_needed:
            
            # --- POCZĄTEK POPRAWKI ---
            # Zamiast nieistniejącego 'pm_link.material_name',
            # używamy nowej ścieżki przez relację: pm_link.material.name
            material_name = pm_link.material.name
            # --- KONIEC POPRAWKI ---

            quantity_str = pm_link.quantity
            
            match = re.match(r'^\s*(\d+\.?\d*)\s*(.*)', quantity_str)
            if not match: continue
            
            value_str, unit_str = match.groups()
            key = (material_name.strip().upper(), unit_str.strip())
            if key not in units: units[key] = unit_str.strip()
            summary[key] += float(value_str) * item.quantity
            
    # 3. Sformatuj wynik (ta część jest poprawna)
    structured_summary = []
    if order.fabric and total_fabric_usage > 0:
        total_val_str = f"{int(total_fabric_usage)}" if total_fabric_usage == int(total_fabric_usage) else f"{total_fabric_usage:.2f}"
        structured_summary.append({
            'name': order.fabric.name.upper(),
            'quantity': f"{total_val_str} metra"
        })

    for (name, unit_key), total_value in sorted(summary.items()):
        total_val_str = f"{int(total_value)}" if total_value == int(total_value) else f"{total_value:.2f}"
        unit = units.get((name, unit_key), unit_key)
        structured_summary.append({
            'name': name,
            'quantity': f"{total_val_str} {unit}"
        })
        
    return structured_summary

@app.route('/orders/new', methods=['GET', 'POST'])
def new_order():
    form = OrderForm()
    form.fabric_id.choices = [(f.id, f.name) for f in Fabric.query.order_by('name').all()]
    
    template_id = request.args.get('template_id', type=int)
    if request.method == 'GET' and template_id:
        order_template = OrderTemplate.query.get(template_id)
        if order_template:
            form.client_name.data = order_template.client_name
            form.description.data = order_template.description
            form.login_info.data = order_template.login_info
            if order_template.fabric_id:
                form.fabric_id.data = order_template.fabric_id

    if form.validate_on_submit():
        try:
            # 1. Obsługa klienta
            client_name = form.client_name.data.strip().upper()
            client = Client.query.filter_by(name=client_name).first()
            if not client:
                client = Client(name=client_name)
                db.session.add(client)
                db.session.flush() # Pobiera ID klienta bez zamykania transakcji

            # 2. Tworzenie obiektu Order
            order = Order(
                client_id=client.id,
                description=form.description.data.strip().upper(),
                fabric_id=form.fabric_id.data,
                login_info=form.login_info.data.strip().upper() if form.login_info.data else None,
                deadline=form.deadline.data,
                status='NOWE',
                zlecajacy=form.zlecajacy.data.upper()
            )
            db.session.add(order)
            db.session.flush() # Pobiera ID zlecenia bez zamykania transakcji

            # 3. Dodawanie produktów i pozycji zlecenia
            for prod_data in form.products.data:
                product_name = prod_data['product_name'].strip().upper()
                if not product_name: continue

                product = Product.query.filter_by(name=product_name).first()
                if not product:
                    product = Product(name=product_name)
                    db.session.add(product)
                    db.session.flush() # Pobiera ID produktu bez zamykania transakcji
                
                for variant in prod_data['variants']:
                    size = variant['size'].strip().upper()
                    try:
                        quantity = int(variant['quantity'])
                    except (ValueError, TypeError):
                        quantity = 0
                    
                    if quantity > 0 and size:
                        order_item = OrderItem(
                            order_id=order.id,
                            product_id=product.id,
                            size=size,
                            quantity=quantity
                        )
                        db.session.add(order_item)

            # 4. Generowanie kodu zlecenia
            today = date.today()
            order.order_code = f"{today.year}/{today.month:02d}/{today.day:02d}-{order.id}"

            # 5. Obsługa załączników
            if 'attachments' in request.files:
                files = request.files.getlist('attachments')
                for file in files:
                    if file and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                        filename = f"{timestamp}_{order.id}_{filename}"
                        file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                        attachment = Attachment(order_id=order.id, filename=filename)
                        db.session.add(attachment)

            # 6. Zapis szablonu, jeśli zaznaczono
            if form.save_template.data and form.template_name.data:
                template_name = form.template_name.data.strip().upper()
                if not OrderTemplate.query.filter_by(template_name=template_name).first():
                    new_template = OrderTemplate(
                        template_name=template_name,
                        client_name=client.name,
                        description=order.description,
                        fabric_id=order.fabric_id,
                        login_info=order.login_info
                    )
                    db.session.add(new_template)
                    flash('Szablon został zapisany.', 'info')
                else:
                    flash('Szablon o tej nazwie już istnieje.', 'warning')
            
            # 7. Finalny zapis wszystkich zmian do bazy danych
            db.session.commit()
            flash('Zlecenie zostało dodane.', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'Wystąpił nieoczekiwany błąd: {e}', 'danger')
            return redirect(url_for('new_order'))

        # 8. Generowanie i wysyłanie pliku Word po pomyślnym zapisie
        material_summary = calculate_material_summary(order)
        filepath = save_order_as_word(order, material_summary)
        
        return send_file(
            filepath,
            as_attachment=True,
            download_name=os.path.basename(filepath),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
 # --- POCZĄTEK ZMIANY ---
    # Pobieramy dane potrzebne do formularza
    existing_clients = Client.query.all()
    all_categories = ProductCategory.query.order_by(ProductCategory.name).all()
    all_templates = OrderTemplate.query.order_by(OrderTemplate.template_name).all() # <-- NOWA LINIA
    
    products_for_js = [
        {'id': p.id, 'name': p.name, 'category_id': p.category_id}
        for p in Product.query.order_by(Product.name).all()
    ]
    # --- KONIEC ZMIANY ---
    # Przygotowanie danych dla żądania GET
    existing_products = Product.query.all()
    existing_clients = Client.query.all()

    # --- POCZĄTEK NOWEJ LOGIKI DLA FILTROWANIA ---
    # Przygotowujemy dane do przekazania do szablonu
    existing_clients = Client.query.all()
    all_categories = ProductCategory.query.order_by(ProductCategory.name).all()
    
    # Tworzymy listę produktów w formacie JSON dla JavaScriptu
    products_for_js = [
        {'id': p.id, 'name': p.name, 'category_id': p.category_id}
        for p in Product.query.order_by(Product.name).all()
    ]
    # --- KONIEC NOWEJ LOGIKI ---

    return render_template('order_form.html',
                           form=form,
                           clients=existing_clients,
                           categories=all_categories,
                           templates=all_templates, # <-- Przekazujemy listę szablonów
                           products_json=json.dumps(products_for_js))

# --- Nowe widoki dla szablonów ---

@app.route('/order_templates')
def order_templates():
    templates = OrderTemplate.query.order_by(OrderTemplate.created_at.desc()).all()
    return render_template('order_templates_list.html', templates=templates)

@app.route('/order_templates/new', methods=['GET', 'POST'])
def new_template():
    form = OrderTemplateForm()
    # Wypełnij listę wyboru tkanin
    form.fabric_id.choices = [(0, '--- Brak ---')] + [(f.id, f.name) for f in Fabric.query.order_by('name').all()]
    
    clients = Client.query.all()
    if form.validate_on_submit():
        template_name = form.template_name.data.strip().upper()
    else:
            template = OrderTemplate(
                template_name=template_name,
                client_name=form.client_name.data.strip().upper(),
                description=form.description.data.strip().upper(),
                # Zapisz nowe dane
                fabric_id=form.fabric_id.data if form.fabric_id.data != 0 else None,
                login_info=form.login_info.data.strip().upper()
            )
            db.session.add(template)
            db.session.commit()
            flash('Szablon został utworzony.', 'success')
            return redirect(url_for('order_templates'))
    return render_template('order_template_form.html', form=form, clients=clients)

@app.route('/order_templates/edit/<int:template_id>', methods=['GET', 'POST'])
def edit_template(template_id):
    template = OrderTemplate.query.get_or_404(template_id)
    form = OrderTemplateForm(obj=template)
    
    # Wypełnij listę wyboru tkanin
    form.fabric_id.choices = [(0, '--- Brak ---')] + [(f.id, f.name) for f in Fabric.query.order_by('name').all()]

    clients = Client.query.all()
    if form.validate_on_submit():
        template.template_name = form.template_name.data.strip().upper()
        template.client_name = form.client_name.data.strip().upper()
        template.description = form.description.data.strip().upper()
        # Zaktualizuj nowe dane
        template.fabric_id = form.fabric_id.data if form.fabric_id.data != 0 else None
        template.login_info = form.login_info.data.strip().upper()
        
        db.session.commit()
        flash('Szablon został zaktualizowany.', 'success')
        return redirect(url_for('order_templates'))
    return render_template('order_template_form.html', form=form, clients=clients)

@app.route('/orders')
def orders_list():
    # Pobieranie filtrów (ta część pozostaje bez zmian)
    client_filter = request.args.get('client', '').strip().upper()
    status_filter = request.args.get('status', '').strip().upper()
    year_filter = request.args.get('year', '')
    month_filter = request.args.get('month', '')
    
    orders_query = Order.query.join(Client)
    
    if client_filter:
        orders_query = orders_query.filter(Client.name == client_filter)
    if status_filter:
        orders_query = orders_query.filter(Order.status == status_filter)
    if year_filter:
        orders_query = orders_query.filter(extract('year', Order.created_at) == int(year_filter))
    if month_filter:
        orders_query = orders_query.filter(extract('month', Order.created_at) == int(month_filter))
    
    all_orders = orders_query.order_by(Order.created_at.desc()).all()

    # --- POCZĄTEK NOWEGO FRAGMENTU ---
    # Dla każdego zlecenia obliczamy planowane zużycie i dołączamy je jako nowy atrybut
    for order in all_orders:
        planned_summary = calculate_material_summary(order)
        # Tworzymy nowy, dynamiczny atrybut w obiekcie order
        order.planned_materials = planned_summary
    # --- KONIEC NOWEGO FRAGMENTU ---

    # --- POCZĄTEK ZMIANY ---
    # Grupujemy zlecenia na trzy osobne listy
    in_progress_orders = [o for o in all_orders if o.status == 'W REALIZACJI']
    new_orders = [o for o in all_orders if o.status == 'NOWE']
    completed_orders = [o for o in all_orders if o.status == 'ZREALIZOWANE']
    
    # Dane do filtrów (bez zmian)
    years_query = db.session.query(extract('year', Order.created_at)).distinct().all()
    years = sorted({int(y[0]) for y in years_query})
    clients = Client.query.order_by(Client.name).all()

    # Przekazujemy do szablonu trzy nowe, pogrupowane listy
    return render_template('orders_list.html', 
                           in_progress_orders=in_progress_orders,
                           new_orders=new_orders,
                           completed_orders=completed_orders,
                           clients=clients, 
                           years=years)

@app.route('/orders/<int:order_id>')
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template('order_detail.html', order=order)



@app.route('/orders/<int:order_id>/status', methods=['POST'])
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    new_status = request.form.get('status')
    if new_status:
        order.status = new_status
        db.session.commit()
        flash('Status zlecenia został zaktualizowany.', 'success')
        # Jeśli żądanie jest AJAX, zwróć JSON:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(success=True, order_id=order.id, status=order.status)
    # W przypadku zwykłego żądania wykonaj przekierowanie
    return redirect(url_for('order_detail', order_id=order.id))


@app.route('/orders/<int:order_id>/pdf')
def order_pdf(order_id):
    order = Order.query.get_or_404(order_id)
    # NOWA LINIA
    material_summary = calculate_material_summary(order)
    
    # ZAKTUALIZOWANA LINIA
    rendered = render_template('order_pdf.html', order=order, material_summary=material_summary)
    
    pdf = pdfkit.from_string(rendered, False, configuration=config)
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=zlecenie_{order.id}.pdf'
    return response

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)



# UPEWNIJ SIĘ, ŻE CAŁY TEN BLOK ZNAJDUJE SIĘ W PLIKU:
@app.route('/products')
def products_list():
    # Pobieramy kategorie do filtra i do panelu zarządzania
    categories = ProductCategory.query.order_by(ProductCategory.name).all()
    
    # Pobieramy wybrany filtr kategorii z URL
    category_filter_id = request.args.get('category_id', type=int)
    
    query = Product.query
    if category_filter_id:
        query = query.filter_by(category_id=category_filter_id)
        
    products = query.order_by(Product.name).all()
    
    # Formularz do szybkiego dodawania nowej kategorii
    form = ProductCategoryForm()

    return render_template('products_list.html', 
                           products=products, 
                           categories=categories,
                           form=form,
                           current_category_id=category_filter_id)

@app.route('/products/new', methods=['GET', 'POST'])
def add_product():
    form = ProductForm()
    # Wypełniamy listę wyboru kategoriami
    form.category_id.choices = [(c.id, c.name) for c in ProductCategory.query.order_by('name').all()]
    form.category_id.choices.insert(0, (0, '--- Brak ---')) # Opcja domyślna
    available_materials = Material.query.order_by(Material.name).all()

    if form.validate_on_submit():
        new_product = Product(
            name=form.name.data.strip().upper(),
            description=form.description.data.strip(),
            fabric_usage_meters=form.fabric_usage_meters.data,
            production_price=form.production_price.data, # <-- DODAJ TĘ LINIĘ
            category_id=form.category_id.data if form.category_id.data != 0 else None
    )
        db.session.add(new_product)
        
        # --- POCZĄTEK POPRAWKI ---
        # Przetwarzamy dodatkowe materiały
        for material_data in form.materials_needed.data:
            material_name = material_data['material_name'].strip().upper()
            quantity = material_data['quantity'].strip()

            if material_name and quantity:
                # Znajdź materiał w bazie po nazwie
                material = Material.query.filter_by(name=material_name).first()
                # Jeśli nie istnieje, stwórz go "w locie"
                if not material:
                    material = Material(name=material_name)
                    db.session.add(material)
                    db.session.flush() # Użyj flush, aby uzyskać ID bez kończenia transakcji

                # Stwórz obiekt łączący, używając ID materiału
                product_material_link = ProductMaterial(
                    product=new_product,
                    material_id=material.id,
                    quantity=quantity
                )
                db.session.add(product_material_link)
        # --- KONIEC POPRAWKI ---
        
        db.session.commit()
        flash('Produkt został dodany.', 'success')
        return redirect(url_for('products_list'))
        
    return render_template('product_form.html', form=form, title="Dodaj Nowy Produkt", 
                           available_materials=available_materials)

@app.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    form = ProductForm(obj=product)
    # Wypełniamy listę wyboru kategoriami
    form.category_id.choices = [(c.id, c.name) for c in ProductCategory.query.order_by('name').all()]
    form.category_id.choices.insert(0, (0, '--- Brak ---'))
    available_materials = Material.query.order_by(Material.name).all()

    if form.validate_on_submit():
        product.name = form.name.data.strip().upper()
        product.description = form.description.data.strip()
        product.fabric_usage_meters = form.fabric_usage_meters.data
        product.production_price = form.production_price.data # <-- DODAJ TĘ LINIĘ
        product.category_id = form.category_id.data if form.category_id.data != 0 else None
        
        # Usuń stare powiązania z materiałami, aby dodać nowe
        ProductMaterial.query.filter_by(product_id=product.id).delete()
        
        # --- POCZĄTEK POPRAWKI ---
        # Ta sama logika, co przy dodawaniu nowego produktu
        for material_data in form.materials_needed.data:
            material_name = material_data['material_name'].strip().upper()
            quantity = material_data['quantity'].strip()

            if material_name and quantity:
                material = Material.query.filter_by(name=material_name).first()
                if not material:
                    material = Material(name=material_name)
                    db.session.add(material)
                    db.session.flush()

                product_material_link = ProductMaterial(
                    product_id=product.id,
                    material_id=material.id,
                    quantity=quantity
                )
                db.session.add(product_material_link)
        # --- KONIEC POPRAWKI ---
                
        db.session.commit()
        flash('Produkt został zaktualizowany.', 'success')
        return redirect(url_for('products_list'))
        
    # Uzupełnianie formularza istniejącymi danymi
    if request.method == 'GET':
        form.materials_needed.entries = []
        for pm_link in product.materials_needed:
            form.materials_needed.append_entry({
                'material_name': pm_link.material.name,
                'quantity': pm_link.quantity
            })
            
    return render_template('product_form.html', form=form, title="Edytuj Produkt", 
                           available_materials=available_materials)

@app.route('/products/delete/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)

    # --- POCZĄTEK POPRAWKI ---
    # Dodajemy sprawdzenie, czy produkt jest używany w jakichkolwiek zleceniach
    if product.order_items:
        flash('Nie można usunąć produktu, ponieważ jest częścią istniejących zleceń.', 'danger')
        return redirect(url_for('products_list'))
    # --- KONIEC POPRAWKI ---

    # Jeśli produkt nie jest używany w zleceniach, możemy go bezpiecznie usunąć.
    # Kaskada w bazie danych automatycznie usunie też jego "recepturę" materiałową.
    db.session.delete(product)
    db.session.commit()
    flash('Produkt został usunięty.', 'success')
    return redirect(url_for('products_list'))

@app.route('/orders/<int:order_id>/delete', methods=['POST'])
def delete_order(order_id):
    order = Order.query.get_or_404(order_id)
    db.session.delete(order)
    db.session.commit()
    flash('Zlecenie zostało usunięte.', 'success')
    return redirect(url_for('orders_list'))

@app.route('/orders/<int:order_id>/print')
def order_print(order_id):
    order = Order.query.get_or_404(order_id)
    # NOWA LINIA
    material_summary = calculate_material_summary(order)
    
    # ZAKTUALIZOWANA LINIA
    return render_template('order_print.html', order=order, material_summary=material_summary)

@app.route('/orders/<int:order_id>/labels')
def order_labels(order_id):
    order = Order.query.get_or_404(order_id)
    template_choice = request.args.get('template', 'cotton')
    
    # Obliczamy dynamiczną wysokość etykiety
    page_height = get_label_page_height(template_choice, target_width_mm=30)
    print("DEBUG: Obliczona wysokość etykiety =", page_height)
    
    rendered_html = render_template('label_template.html', 
                                    order=order, 
                                    template_choice=template_choice, 
                                    page_height=page_height)

    options = {
        'page-width': '30mm',
        'page-height': page_height,
        'margin-top': '0mm',
        'margin-bottom': '0mm',
        'margin-left': '0mm',
        'margin-right': '0mm',
        'disable-smart-shrinking': '',
        'enable-local-file-access': ''
    }

    pdf = pdfkit.from_string(rendered_html, False, configuration=config, options=options)

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=labels.pdf'
    return response


@app.route('/orders/<int:order_id>/choose_label')
def choose_label(order_id):
    order = Order.query.get_or_404(order_id)
    order_template_images = {
        'cotton': '/static/images/cotton.jpg',
        'polyester': '/static/images/polyester.jpg',
        'mixed': '/static/images/mixed.jpg'
    }
    return render_template('choose_label.html', order=order, order_template_images=order_template_images)


@app.route('/orders/<int:order_id>/download_doc')
def download_doc(order_id):
    order = Order.query.get_or_404(order_id)
    
    # --- POCZĄTEK POPRAWKI ---
    # Upewnij się, że te dwie linie istnieją i są w tej kolejności
    material_summary = calculate_material_summary(order)
    filepath = save_order_as_word(order, material_summary, folder_path='order_docs')
    # --- KONIEC POPRAWKI ---
    
    return send_file(
        filepath,
        as_attachment=True,
        download_name=os.path.basename(filepath),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

@app.route('/api/fabrics')
def api_fabrics():
    fabrics = Fabric.query.order_by(Fabric.name).all()
    return jsonify([f.name for f in fabrics])

# app/routes.py

@app.route('/orders/<int:order_id>/material_usage', methods=['GET', 'POST'])
def edit_material_usage(order_id):
    order = Order.query.get_or_404(order_id)

    if request.method == 'POST':
        MaterialUsage.query.filter_by(order_id=order_id).delete()
        materials = request.form.getlist('material_name[]')
        quantities = request.form.getlist('quantity[]')

        for name, qty in zip(materials, quantities):
            if name.strip() and qty.strip():
                db.session.add(MaterialUsage(order_id=order_id, material_name=name.strip().upper(), quantity=qty.strip()))
        
        db.session.commit()
        flash('Zużycie materiałów zostało zaktualizowane.', 'success')
        return redirect(url_for('orders_list'))

    materials_to_display = []
    if order.materials_used:
        materials_to_display = order.materials_used
    else:
        planned_summary = calculate_material_summary(order)
        materials_to_display = [{'material_name': item['name'], 'quantity': item['quantity']} for item in planned_summary]

    # --- POCZĄTEK POPRAWKI ---
    # Zmieniamy zapytanie z `ProductMaterial.material_name` na `Material.name`
    defined_materials = [m[0] for m in db.session.query(Material.name).distinct().all()]
    fabric_names = [f.name for f in Fabric.query.all()]
    usage_materials = [m[0] for m in db.session.query(MaterialUsage.material_name).distinct().all()]
    
    all_possible_materials = sorted(list(set(defined_materials + fabric_names + usage_materials)))
    # --- KONIEC POPRAWKI ---

    return render_template(
        'edit_material_usage.html', 
        order=order, 
        materials=materials_to_display, 
        existing_materials=all_possible_materials
    )


# app/routes.py

@app.route('/kanban')
def kanban():
    orders = Order.query.filter(
        Order.cutting_table == 'skrojone', 
        Order.status.in_(['NOWE', 'W REALIZACJI'])
    ).order_by(Order.created_at.desc()).all()

    # --- POCZĄTEK ZMIANY ---
    # Zlecenie z 'OBA' trafia do obu list
    team1_orders = [o for o in orders if o.assigned_team in ['zespol-1', 'OBA']]
    team2_orders = [o for o in orders if o.assigned_team in ['zespol-2', 'OBA']]
    unassigned_orders = [o for o in orders if o.assigned_team is None]
    # --- KONIEC ZMIANY ---

    return render_template('kanban.html',
                           team1_orders=team1_orders,
                           team2_orders=team2_orders,
                           unassigned_orders=unassigned_orders)

@app.route('/kanban_partial')
def kanban_partial():
    orders = Order.query.filter(
        Order.cutting_table == 'skrojone', 
        Order.status.in_(['NOWE', 'W REALIZACJI'])
    ).order_by(Order.created_at.desc()).all()

    # --- POCZĄTEK ZMIANY ---
    team1_orders = [o for o in orders if o.assigned_team in ['zespol-1', 'OBA']]
    team2_orders = [o for o in orders if o.assigned_team in ['zespol-2', 'OBA']]
    unassigned_orders = [o for o in orders if o.assigned_team is None]
    # --- KONIEC ZMIANY ---

    return render_template('kanban_partial.html',
                           team1_orders=team1_orders,
                           team2_orders=team2_orders,
                           unassigned_orders=unassigned_orders)

@app.route('/assign_team', methods=['POST'])
def assign_team():
    data = request.get_json()
    order_id = data.get('order_id')
    team = data.get('team')

    order = Order.query.get_or_404(order_id)

    # --- POCZĄTEK ZMIANY ---
    # Dodajemy 'OBA' jako nową, prawidłową wartość
    if team in ['zespol-1', 'zespol-2', 'OBA']:
        order.assigned_team = team
    # --- KONIEC ZMIANY ---
    else:
        order.assigned_team = None

    db.session.commit()
    return jsonify(success=True, team=order.assigned_team)

@app.route('/krojownia')
def krojownia():
     # ZMIANA: Dodajemy warunek, aby pokazywać tylko zlecenia, które nie są jeszcze przypisane do zespołu
    orders = Order.query.filter(
        Order.status.in_(['NOWE', 'W REALIZACJI']),
        Order.assigned_team.is_(None)
    ).order_by(Order.created_at.desc()).all()
    

    stol_1_orders = [o for o in orders if o.cutting_table == 'stol-1']
    stol_2_orders = [o for o in orders if o.cutting_table == 'stol-2']
    stol_3_orders = [o for o in orders if o.cutting_table == 'stol-3']
    skrojone_orders = [o for o in orders if o.cutting_table == 'skrojone']
    unassigned_orders = [o for o in orders if o.cutting_table is None]

    return render_template('krojownia.html',
                           stol_1_orders=stol_1_orders,
                           stol_2_orders=stol_2_orders,
                           stol_3_orders=stol_3_orders,
                           skrojone_orders=skrojone_orders,
                           unassigned_orders=unassigned_orders)

@app.route('/assign_cutting_table', methods=['POST'])
def assign_cutting_table():
    data = request.get_json()
    order_id = data.get('order_id')
    table = data.get('table')

    order = Order.query.get_or_404(order_id)

    # --- POCZĄTEK ZMIANY ---
    # Jeśli zlecenie jest nowe i zostaje ruszone, zmień status.
    if order.status == 'NOWE' and table is not None:
        order.status = 'W REALIZACJI'
    # --- KONIEC ZMIANY ---

    if table in ['stol-1', 'stol-2', 'stol-3', 'skrojone']:
        order.cutting_table = table
    else:
        order.cutting_table = None

    db.session.commit()
    return jsonify(success=True, table=order.cutting_table)

@app.route('/order_summary/<int:order_id>')
def order_summary(order_id):
    order = Order.query.get_or_404(order_id)

    summary = {}
    for item in order.order_items:
        name = item.product.name
        summary[name] = summary.get(name, 0) + item.quantity

    return jsonify({
        'order_code': order.order_code,
        'client': order.client.name,
        'summary': summary
    })

def get_label_page_height(template_choice, target_width_mm=30):
    """
    Oblicza wysokość etykiety (w mm) na podstawie szerokości (target_width_mm)
    oraz proporcji obrazu tła, który zależy od wybranego szablonu.
    """
    # Mapowanie szablonów na nazwy plików obrazków
    images = {
        'cotton': 'cotton.jpg',
        'polyester': 'polyester.jpg',
        'mixed': 'mixed.jpg'
    }
    # Jeśli dla danego szablonu nie mamy obrazka, użyjemy domyślnego
    image_file = images.get(template_choice, 'default_background.jpg')
    # Ścieżka do katalogu z obrazkami (zakładamy, że znajdują się w /static/images/)
    image_path = os.path.join(current_app.static_folder, 'images', image_file)
    
    # Wczytujemy obrazek
    with Image.open(image_path) as img:
        width_px, height_px = img.size
        # Obliczamy proporcję (wysokość/szerokość)
        ratio = height_px / width_px
        # Obliczamy wysokość etykiety w mm
        target_height_mm = target_width_mm * ratio
        # Zaokrąglamy do jednego miejsca po przecinku
        return f"{round(target_height_mm, 1)}mm"
    
@app.route('/orders/<int:order_id>/labels_debug')
def order_labels_debug(order_id):
    order = Order.query.get_or_404(order_id)
    template_choice = request.args.get('template', 'cotton')
    page_height = get_label_page_height(template_choice, target_width_mm=30)
    print("DEBUG: Obliczona wysokość etykiety =", page_height)
    # Przekazujemy page_height do szablonu
    rendered_html = render_template('label_template.html', 
                                    order=order, 
                                    template_choice=template_choice, 
                                    page_height=page_height)
    return rendered_html

# NOWE FUNKCJE DO ZARZĄDZANIA TKANINAMI
# NOWA, ZINTEGROWANA STRONA DO ZARZĄDZANIA MATERIAŁAMI
@app.route('/materials-management')
def materials_management():
    fabrics = Fabric.query.order_by(Fabric.name).all()
    materials = Material.query.order_by(Material.name).all()
    return render_template('materials_management.html', fabrics=fabrics, materials=materials)

@app.route('/fabrics/new', methods=['GET', 'POST'])
def add_fabric():
    # Używamy prostego formularza FabricForm
    form = FabricForm()
    if form.validate_on_submit():
        fabric_name = form.name.data.strip().upper()
        existing_fabric = Fabric.query.filter_by(name=fabric_name).first()
        if existing_fabric:
            flash(f'Tkanina o nazwie "{fabric_name}" już istnieje.', 'danger')
            return redirect(url_for('add_fabric'))
        
        new_fabric = Fabric(name=fabric_name, price=form.price.data)
        db.session.add(new_fabric)
        db.session.commit()
        flash('Nowa tkanina została dodana.', 'success')
        return redirect(url_for('materials_management'))
    # Renderujemy dedykowany szablon do dodawania
    return render_template('fabric_add_form.html', form=form, title="Dodaj Tkaninę")

@app.route('/fabrics/edit/<int:fabric_id>', methods=['GET', 'POST'])
def edit_fabric(fabric_id):
    fabric = Fabric.query.get_or_404(fabric_id)
    form = FabricForm(obj=fabric)
    if form.validate_on_submit():
        fabric.name = form.name.data.strip().upper()
        fabric.price = form.price.data
        db.session.commit()
        flash('Tkanina została zaktualizowana.', 'success')
        return redirect(url_for('materials_management'))
    return render_template('fabric_form.html', form=form, title="Edytuj Tkaninę")

@app.route('/fabrics/delete/<int:fabric_id>', methods=['POST'])
def delete_fabric(fabric_id):
    fabric = Fabric.query.get_or_404(fabric_id)
    # Sprawdzenie, czy tkanina nie jest używana w żadnym zleceniu
    if fabric.orders:
        flash('Nie można usunąć tkaniny, ponieważ jest przypisana do istniejących zleceń.', 'danger')
        return redirect(url_for('materials_management'))
    
    db.session.delete(fabric)
    db.session.commit()
    flash('Tkanina została usunięta.', 'success')
    return redirect(url_for('materials_management'))

# Dodaj kompletny zestaw tras dla Materiałów


@app.route('/materials/add', methods=['GET', 'POST'])
def add_material():
    # Używamy prostego formularza MaterialForm
    form = MaterialForm()
    if form.validate_on_submit():
        material_name = form.name.data.strip().upper()
        existing_material = Material.query.filter_by(name=material_name).first()
        if existing_material:
            flash(f'Materiał o nazwie "{material_name}" już istnieje.', 'danger')
            return redirect(url_for('add_material'))

        new_material = Material(name=material_name, price=form.price.data)
        db.session.add(new_material)
        db.session.commit()
        flash('Nowy materiał został dodany.', 'success')
        return redirect(url_for('materials_management'))
    # Renderujemy dedykowany szablon do dodawania
    return render_template('material_add_form.html', form=form, title="Dodaj Materiał")

@app.route('/materials/edit/<int:material_id>', methods=['GET', 'POST'])
def edit_material(material_id):
    material = Material.query.get_or_404(material_id)
    form = MaterialForm(obj=material)
    if form.validate_on_submit():
        material.name = form.name.data.strip().upper()
        material.price = form.price.data
        db.session.commit()
        flash('Materiał został zaktualizowany.', 'success')
        return redirect(url_for('materials_management'))
    return render_template('material_form.html', form=form, title="Edytuj Materiał")

@app.route('/materials/delete/<int:material_id>', methods=['POST'])
def delete_material(material_id):
    material = Material.query.get_or_404(material_id)
    if material.product_links:
        flash('Nie można usunąć materiału, jest używany w definicjach produktów.', 'danger')
        return redirect(url_for('materials_management'))
    db.session.delete(material)
    db.session.commit()
    flash('Materiał został usunięty.', 'success')
    return redirect(url_for('materials_management'))

@app.route('/reports')
def reports():
    material_filter = request.args.get('material', '').strip().upper()

    # Pobieramy zbiór nazw wszystkich zdefiniowanych tkanin dla łatwego rozróżniania
    all_fabric_names = {f.name.upper() for f in Fabric.query.all()}
    
    # --- POCZĄTEK NOWEJ LOGIKI ---

    # Przygotowujemy puste słowniki na finalne, zagregowane wyniki
    final_fabric_summary = defaultdict(float)
    final_material_summary = defaultdict(lambda: defaultdict(float))

    # 1. Pobieramy wszystkie zlecenia, które zostały zrealizowane
    completed_orders = Order.query.filter_by(status='ZREALIZOWANE').all()

    # 2. Iterujemy przez każde zrealizowane zlecenie
    for order in completed_orders:
        
        # Sprawdzamy, czy dla zlecenia zapisano ręcznie "rzeczywiste zużycie"
        if order.materials_used:
            # Jeśli TAK - używamy tych danych jako źródła prawdy
            for usage in order.materials_used:
                name = usage.material_name.strip().upper()
                match = re.match(r'^\s*(\d+\.?\d*)\s*(.*)', usage.quantity)
                if match:
                    value, unit = float(match.groups()[0]), match.groups()[1].strip()
                    # Rozdzielamy tkaniny od reszty materiałów
                    if name in all_fabric_names:
                        final_fabric_summary[name] += value
                    else:
                        final_material_summary[name][unit] += value
        else:
            # Jeśli NIE - używamy "planowanego zużycia" z receptur
            planned_summary = calculate_material_summary(order)
            for item in planned_summary:
                name = item['name'].strip().upper()
                match = re.match(r'^\s*(\d+\.?\d*)\s*(.*)', item['quantity'])
                if match:
                    value, unit = float(match.groups()[0]), match.groups()[1].strip()
                    # Rozdzielamy tkaniny od reszty materiałów
                    if name in all_fabric_names:
                        final_fabric_summary[name] += value
                    else:
                        final_material_summary[name][unit] += value
    
    # --- KONIEC NOWEJ LOGIKI ---

    # 3. Logika filtrowania (bez zmian, działa na finalnych wynikach)
    if material_filter:
        filtered_fabric_summary = {k: v for k, v in final_fabric_summary.items() if material_filter in k}
        filtered_material_summary = {k: v for k, v in final_material_summary.items() if material_filter in k}
    else:
        filtered_fabric_summary = final_fabric_summary
        filtered_material_summary = final_material_summary
    
    # Przygotowanie listy do filtra (bez zmian)
    all_materials_query = set([r.material_name.upper() for r in MaterialUsage.query.all()] + [f.name.upper() for f in Fabric.query.all()])
    all_materials_list = sorted(list(all_materials_query))

    return render_template('reports.html', 
                           fabric_summary=filtered_fabric_summary, 
                           material_summary=filtered_material_summary,
                           all_materials=all_materials_list,
                           current_filter=material_filter)

# pokazywanie aktywnych zleceń
# app/routes.py

# app/routes.py

@app.context_processor
def inject_in_progress_orders():
    """
    Udostępnia do wszystkich szablonów listy zleceń w realizacji,
    podzielone na te w krojowni i te w szwalni.
    """
    # Pobieramy wszystkie zlecenia, które są aktualnie w trakcie realizacji
    all_in_progress = Order.query.filter_by(status='W REALIZACJI').order_by(Order.deadline).all()
    
    krojownia_orders = []
    szwalnia_orders = []

    for order in all_in_progress:
        order.total_quantity = sum(item.quantity for item in order.order_items)
        
        # Pokaż w Szwalni, jeśli jest skrojone I przypisane do zespołu
        if order.cutting_table == 'skrojone' and order.assigned_team is not None:
            szwalnia_orders.append(order)
        # Pokaż w Krojowni, jeśli jest przypisane do stołu, ale jeszcze nie skrojone
        elif order.cutting_table is not None and order.cutting_table != 'skrojone':
            krojownia_orders.append(order)
    
    return dict(
        krojownia_in_progress=krojownia_orders,
        szwalnia_in_progress=szwalnia_orders
    )

@app.route('/api/v1/update-prices', methods=['POST'])
def receive_price_update():
    auth_key = request.headers.get('X-API-KEY')
    if auth_key != app.config['API_SECRET_KEY']:
        return jsonify({'error': 'Brak autoryzacji'}), 401

    price_data = request.get_json()
    if not price_data:
        return jsonify({'error': 'Brak danych'}), 400

    updated_count = 0
    try:
        for item in price_data:
            symbol = item.get('symbol')
            price = item.get('price')
            
            if symbol and price is not None:
                # Zaktualizuj cenę w tabeli Tkanin
                updated_fabrics = Fabric.query.filter_by(subiekt_symbol=symbol).update({'price': price})
                # Zaktualizuj cenę w tabeli Materiałów
                updated_materials = Material.query.filter_by(subiekt_symbol=symbol).update({'price': price})
                
                if updated_fabrics > 0 or updated_materials > 0:
                    updated_count += 1

        db.session.commit()
        
        message = f'Pomyślnie zaktualizowano ceny dla {updated_count} symboli.'
        print(message)
        return jsonify({'status': 'success', 'message': message}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Błąd podczas aktualizacji cen: {e}")
        return jsonify({'error': str(e)}), 500


# NOWA TRASA - do odbierania pełnego katalogu towarów z Subiekta
@app.route('/api/v1/receive-subiekt-catalog', methods=['POST'])
def receive_subiekt_catalog():
    # 1. Sprawdź, czy żądanie jest autoryzowane (ten sam klucz co wcześniej)
    auth_key = request.headers.get('X-API-KEY')
    if auth_key != app.config['API_SECRET_KEY']:
        return jsonify({'error': 'Brak autoryzacji'}), 401

    # 2. Odbierz listę towarów w formacie JSON
    subiekt_products = request.get_json()
    if not subiekt_products:
        return jsonify({'error': 'Brak danych'}), 400

    try:
        # 3. Wyczyść starą tabelę tymczasową przed dodaniem nowych danych
        db.session.query(SubiektProductCache).delete()

        # 4. Dodaj wszystkie nowe towary do tabeli tymczasowej
        for product_data in subiekt_products:
            cached_product = SubiektProductCache(
                symbol=product_data.get('symbol'),
                name=product_data.get('name'),
                is_mapped=False # Domyślnie oznaczamy jako niezmapowane
            )
            db.session.add(cached_product)
        
        db.session.commit()
        
        return jsonify({
            'status': 'success', 
            'message': f'Pomyślnie zaimportowano {len(subiekt_products)} towarów z Subiekta.'
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"Błąd podczas importu katalogu Subiekta: {e}")
        return jsonify({'error': str(e)}), 500
    
    # NOWA TRASA DO WYŚWIETLANIA STRONY MAPOWANIA
@app.route('/subiekt-mapping')
def subiekt_mapping():
    # Pobieramy tylko towary, które nie zostały jeszcze zmapowane
    unmapped_products = SubiektProductCache.query.filter_by(is_mapped=False).order_by(SubiektProductCache.symbol).all()
    return render_template('subiekt_mapping.html', products=unmapped_products)

# NOWA TRASA DO OBSŁUGI AKCJI MAPOWANIA
# app/routes.py

@app.route('/subiekt-mapping/map', methods=['POST'])
def map_subiekt_product():
    symbol = request.form.get('symbol')
    name = request.form.get('name')
    map_type = request.form.get('map_type')

    # --- POCZĄTEK POPRAWKI ---
    # Zamiast .get(symbol), używamy .filter_by(symbol=symbol).first()
    product_cache = SubiektProductCache.query.filter_by(symbol=symbol).first()
    # --- KONIEC POPRAWKI ---
    
    if not product_cache:
        flash(f'Błąd: Nie znaleziono towaru o symbolu {symbol} w pamięci podręcznej.', 'danger')
        return redirect(url_for('subiekt_mapping'))

    try:
        if map_type == 'fabric':
            existing = Fabric.query.filter_by(subiekt_symbol=symbol).first()
            if not existing:
                new_fabric = Fabric(name=name, subiekt_symbol=symbol)
                db.session.add(new_fabric)
                flash(f'Utworzono nową tkaninę: {name}', 'success')
            else:
                flash(f'Tkanina {name} jest już zmapowana.', 'info')

        elif map_type == 'material':
            existing = Material.query.filter_by(subiekt_symbol=symbol).first()
            if not existing:
                new_material = Material(name=name, subiekt_symbol=symbol)
                db.session.add(new_material)
                flash(f'Utworzono nowy materiał dodatkowy: {name}', 'success')
            else:
                flash(f'Materiał {name} jest już zmapowany.', 'info')
        
        product_cache.is_mapped = True
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        flash(f'Wystąpił błąd: {e}', 'danger')

    return redirect(url_for('subiekt_mapping'))
# app/routes.py

# NOWA TRASA - udostępnia listę zmapowanych symboli Subiekta
@app.route('/api/v1/get-mapped-symbols', methods=['GET'])
def get_mapped_symbols():
    # Sprawdź klucz autoryzacyjny
    auth_key = request.headers.get('X-API-KEY')
    if auth_key != app.config['API_SECRET_KEY']:
        return jsonify({'error': 'Brak autoryzacji'}), 401

    # Zbierz symbole z tkanin i materiałów
    fabric_symbols = [f.subiekt_symbol for f in Fabric.query.filter(Fabric.subiekt_symbol.isnot(None)).all()]
    material_symbols = [m.subiekt_symbol for m in Material.query.filter(Material.subiekt_symbol.isnot(None)).all()]
    
    # Połącz w unikalną listę
    all_symbols = list(set(fabric_symbols + material_symbols))
    
    return jsonify(all_symbols), 200

# ZASTĄP TĘ FUNKCJĘ
@app.route('/subiekt-mapping/import-csv', methods=['POST'])
def import_subiekt_csv():
    if 'csv_file' not in request.files:
        flash('Nie znaleziono pliku w formularzu.', 'danger')
        return redirect(url_for('subiekt_mapping'))
    
    file = request.files['csv_file']
    
    if file.filename == '':
        flash('Nie wybrano żadnego pliku.', 'danger')
        return redirect(url_for('subiekt_mapping'))

    # Sprawdzamy, czy plik ma poprawne rozszerzenie
    if not (file.filename.endswith('.csv') or file.filename.endswith('.xlsx')):
        flash('Niepoprawny format pliku. Proszę wybrać plik .csv lub .xlsx', 'danger')
        return redirect(url_for('subiekt_mapping'))

    try:
        # Wyczyść starą tabelę tymczasową
        db.session.query(SubiektProductCache).delete()
        
        # Logika odczytu pliku za pomocą PANDAS
        if file.filename.endswith('.xlsx'):
            # Dla plików Excela
            df = pd.read_excel(file)
        else:
            # Dla plików CSV (z obsługą różnych kodowań)
            try:
                df = pd.read_csv(file, encoding='utf-8-sig')
            except UnicodeDecodeError:
                file.stream.seek(0)
                df = pd.read_csv(file, encoding='cp1250')

        # Sprawdzamy, czy plik ma wymagane kolumny
        if 'Symbol' not in df.columns or 'Nazwa' not in df.columns:
            flash('Błąd: Plik musi zawierać kolumny "Symbol" oraz "Nazwa".', 'danger')
            return redirect(url_for('subiekt_mapping'))

        count = 0
        for index, row in df.iterrows():
            symbol = row.get('Symbol')
            name = row.get('Nazwa')
            
            # Konwertujemy symbol na tekst, aby uniknąć problemów z liczbami
            if symbol:
                symbol = str(symbol).strip().upper()
            
            if symbol and name:
                cached_product = SubiektProductCache(
                    symbol=symbol,
                    name=str(name).strip(),
                    is_mapped=False
                )
                db.session.add(cached_product)
                count += 1
        
        db.session.commit()
        flash(f'Pomyślnie zaimportowano {count} towarów z pliku.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Wystąpił nieoczekiwany błąd podczas importu: {e}', 'danger')

    return redirect(url_for('subiekt_mapping'))

@app.route('/product-categories/add', methods=['POST'])
def add_product_category():
    form = ProductCategoryForm()
    if form.validate_on_submit():
        category_name = form.name.data.strip().upper()
        if not ProductCategory.query.filter_by(name=category_name).first():
            new_category = ProductCategory(name=category_name)
            db.session.add(new_category)
            db.session.commit()
            flash('Nowa kategoria została dodana.', 'success')
        else:
            flash('Kategoria o tej nazwie już istnieje.', 'danger')
    return redirect(url_for('products_list'))

@app.route('/product-categories/delete/<int:category_id>', methods=['POST'])
def delete_product_category(category_id):
    category = ProductCategory.query.get_or_404(category_id)
    if category.products:
        flash('Nie można usunąć kategorii, ponieważ jest przypisana do produktów.', 'danger')
    else:
        db.session.delete(category)
        db.session.commit()
        flash('Kategoria została usunięta.', 'success')
    return redirect(url_for('products_list'))

# NOWA, UNIWERSALNA FUNKCJA DO EDYCJI
@app.route('/materials-management/edit/<string:item_type>/<int:item_id>', methods=['GET', 'POST'])
def edit_mapped_item(item_type, item_id):
    # Wczytujemy odpowiedni obiekt (tkaninę lub materiał)
    if item_type == 'fabric':
        item = Fabric.query.get_or_404(item_id)
    elif item_type == 'material':
        item = Material.query.get_or_404(item_id)
    else:
        return "Nieznany typ", 404

    form = MaterialEditForm(obj=item)
    # Ustawiamy domyślną wartość w liście wyboru typu
    if request.method == 'GET':
        form.material_type.data = item_type

    if form.validate_on_submit():
        new_type = form.material_type.data
        
        # Jeśli typ się nie zmienił, po prostu aktualizujemy dane
        if new_type == item_type:
            item.name = form.name.data.strip().upper()
            item.subiekt_symbol = form.subiekt_symbol.data.strip().upper() or None
            item.price = form.price.data
            flash(f'Zaktualizowano {item.name}.', 'success')
        
        # Jeśli typ się zmienił, musimy przenieść dane między tabelami
        else:
            # Sprawdzamy, czy przenoszony element nie jest używany w produktach
            if (item_type == 'material' and item.product_links) or \
               (item_type == 'fabric' and item.orders): # Proste sprawdzenie dla tkanin
                flash('Nie można zmienić typu, ponieważ ten element jest już używany w zleceniach lub produktach.', 'danger')
                return redirect(url_for('materials_management'))

            # Tworzymy nowy obiekt docelowy
            if new_type == 'fabric':
                new_item = Fabric()
            else: # new_type == 'material'
                new_item = Material()

            # Kopiujemy dane
            new_item.name = form.name.data.strip().upper()
            new_item.subiekt_symbol = form.subiekt_symbol.data.strip().upper() or None
            new_item.price = form.price.data
            
            # Usuwamy stary obiekt i dodajemy nowy
            db.session.delete(item)
            db.session.add(new_item)
            flash(f'Przeniesiono {new_item.name} do nowej kategorii.', 'success')

        db.session.commit()
        return redirect(url_for('materials_management'))

    return render_template('material_edit_form.html', form=form, item=item)

# NOWA TRASA DO OBSŁUGI IMPORTU PRODUKTÓW Z XLSX
@app.route('/products/import', methods=['POST'])
def import_products_xlsx():
    # 1. Pobranie danych z formularza
    if 'xlsx_file' not in request.files:
        flash('Nie znaleziono pliku w formularzu.', 'danger')
        return redirect(url_for('products_list'))
    
    file = request.files['xlsx_file']
    category_id = request.form.get('category_id_import')

    # 2. Walidacja danych
    if file.filename == '' or not category_id:
        flash('Musisz wybrać plik oraz kategorię.', 'danger')
        return redirect(url_for('products_list'))

    category = ProductCategory.query.get(category_id)
    if not category:
        flash('Wybrana kategoria nie istnieje.', 'danger')
        return redirect(url_for('products_list'))

    if not file.filename.endswith('.xlsx'):
        flash('Niepoprawny format pliku. Proszę wybrać plik .xlsx', 'danger')
        return redirect(url_for('products_list'))

    # 3. Przetwarzanie pliku i zapis do bazy
    try:
        df = pd.read_excel(file)
        
        # Sprawdzenie, czy plik ma wymagane kolumny
        if 'Nazwa' not in df.columns or 'Cena Produkcji' not in df.columns:
            flash('Błąd: Plik musi zawierać kolumny "Nazwa" oraz "Cena Produkcji".', 'danger')
            return redirect(url_for('products_list'))

        imported_count = 0
        skipped_count = 0
        
        # Iterujemy przez wiersze w pliku Excel
        for index, row in df.iterrows():
            name_from_file = str(row['Nazwa']).strip()
            production_price = float(row['Cena Produkcji'])
            
            # Tworzymy nową, prefiksowaną nazwę
            prefixed_name = f"{category.name}_{name_from_file}".upper()
            
            # Sprawdzamy, czy produkt o takiej nazwie już nie istnieje
            existing_product = Product.query.filter_by(name=prefixed_name).first()
            if existing_product:
                skipped_count += 1
                continue # Pomiń, jeśli już istnieje

            # Tworzymy nowy produkt
            new_product = Product(
                name=prefixed_name,
                production_price=production_price,
                category_id=category.id,
                # Ustawiamy domyślne wartości dla pozostałych pól
                description="",
                fabric_usage_meters=0.0
            )
            db.session.add(new_product)
            imported_count += 1
        
        db.session.commit()
        flash(f'Import zakończony! Dodano {imported_count} nowych produktów. Pominięto {skipped_count} duplikatów.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Wystąpił nieoczekiwany błąd podczas importu: {e}', 'danger')

    return redirect(url_for('products_list'))

# NOWA TRASA DLA KALKULATORA
@app.route('/calculator')
def calculator():
    # Pobieramy wszystkie dane potrzebne do działania kalkulatora
    products = Product.query.order_by(Product.name).all()
    fabrics = Fabric.query.order_by(Fabric.name).all()
    materials = Material.query.order_by(Material.name).all()
    categories = ProductCategory.query.order_by(ProductCategory.name).all() # <-- NOWA LINIA

    # Przygotowujemy dane o produktach w formacie JSON dla JavaScriptu
    products_data = {}
    for p in products:
        products_data[p.id] = {
            'name': p.name,
            'fabric_usage': p.fabric_usage_meters,
            'production_price': p.production_price,
            'category_id': p.category_id, # <-- Upewniamy się, że ID kategorii jest w danych
            'materials_needed': [
                {'id': pm.material.id, 'name': pm.material.name, 'quantity': pm.quantity} 
                for pm in p.materials_needed
            ]
        }

    return render_template('calculator.html',
                           products=products,
                           fabrics=fabrics,
                           materials=materials,
                           categories=categories, # <-- PRZEKAZUJEMY KATEGORIE DO SZABLONU
                           products_json=json.dumps(products_data))



# =================================================
# === API DLA APLIKACJI MOBILNEJ - KROJOWNIA ======
# =================================================

@app.route('/api/krojownia/orders', methods=['GET'])
def get_krojownia_orders():
    """Zwraca listę zleceń dla krojowni (status NOWE lub W REALIZACJI)."""
    
    # --- POCZĄTEK POPRAWKI ---
    # Dodajemy warunek, aby pokazywać tylko zlecenia, 
    # które nie są jeszcze przypisane do żadnego zespołu w szwalni.
    orders = Order.query.filter(
        Order.status.in_(['NOWE', 'W REALIZACJI']),
        Order.assigned_team.is_(None) 
    ).order_by(Order.created_at.desc()).all()
    # --- KONIEC POPRAWKI ---
    
    orders_list = []
    for order in orders:
        orders_list.append({
            'id': order.id,
            'order_code': order.order_code,
            'client_name': order.client.name,
            'status': order.status,
            'cutting_table': order.cutting_table
        })
        
    return jsonify(orders_list)
@app.route('/api/order/<int:order_id>/assign_table', methods=['POST'])
def api_assign_cutting_table(order_id):
    order = Order.query.get_or_404(order_id)
    data = request.get_json()

    if not data or 'table' not in data:
        return jsonify({'error': 'Brak nazwy stołu w zapytaniu'}), 400

    new_table = data.get('table')

    # --- POCZĄTEK ZMIANY ---
    if order.status == 'NOWE' and new_table is not None:
        order.status = 'W REALIZACJI'
    # --- KONIEC ZMIANY ---

    if new_table in ['stol-1', 'stol-2', 'stol-3', 'skrojone']:
        order.cutting_table = new_table
    else:
        order.cutting_table = None

    db.session.commit()
    
    return jsonify({
        'success': True, 
        'message': f'Zlecenie {order.order_code} przypisano do {order.cutting_table or "Nieprzypisane"}.'
    })

# =================================================
# === API DLA APLIKACJI MOBILNEJ - SZWALNIA =======
# =================================================

@app.route('/api/szwalnia/orders', methods=['GET'])
def get_szwalnia_orders():
    """Zwraca listę zleceń dla szwalni (tylko te oznaczone jako 'skrojone')."""
    orders = Order.query.filter(
        Order.cutting_table == 'skrojone', 
        Order.status.in_(['NOWE', 'W REALIZACJI'])
    ).order_by(Order.created_at.desc()).all()
    
    orders_list = []
    for order in orders:
        orders_list.append({
            'id': order.id,
            'order_code': order.order_code,
            'client_name': order.client.name,
            'status': order.status,
            'assigned_team': order.assigned_team,
            # --- DODANE NOWE POLA ---
            'team1_completed': order.team1_completed,
            'team2_completed': order.team2_completed
        })
        
    return jsonify(orders_list)

@app.route('/api/order/<int:order_id>/assign_team', methods=['POST'])
def api_assign_team(order_id):
    order = Order.query.get_or_404(order_id)
    data = request.get_json()

    if not data or 'team' not in data:
        return jsonify({'error': 'Brak nazwy zespołu w zapytaniu'}), 400

    new_team = data.get('team')

    # --- POCZĄTEK ZMIANY ---
    if new_team in ['zespol-1', 'zespol-2', 'OBA']:
        order.assigned_team = new_team
    # --- KONIEC ZMIANY ---
    else:
        order.assigned_team = None
        
    db.session.commit()
    
    return jsonify({
        'success': True, 
        'message': f'Zlecenie {order.order_code} przypisano do {order.assigned_team or "Nieprzypisane"}.'
    })

# =================================================
# === WIDOKI DLA APLIKACJI MOBILNEJ ===============
# =================================================

@app.route('/mobile/krojownia')
def mobile_krojownia():
    """Renderuje mobilny interfejs dla krojowni."""
    return render_template('krojownia_mobile.html')

# app/routes.py

@app.route('/mobile/szwalnia')
def mobile_szwalnia():
    """Renderuje mobilny interfejs dla szwalni."""
    return render_template('szwalnia_mobile.html')


# app/routes.py

# NOWA TRASA DO KOŃCZENIA ZLECEŃ
@app.route('/api/order/<int:order_id>/complete', methods=['POST'])
def complete_order_part(order_id):
    order = Order.query.get_or_404(order_id)
    data = request.get_json()
    # Pobieramy informację, z której kolumny przeciągnięto zlecenie
    completed_by_team = data.get('completed_by')

    # Proste zlecenie - od razu zmieniamy status
    if order.assigned_team != 'OBA':
        order.status = 'ZREALIZOWANE'
        flash(f'Zlecenie {order.order_code} zostało ukończone.', 'success')
    
    # Zlecenie podzielone - bardziej złożona logika
    else:
        if completed_by_team == 'zespol-1':
            order.team1_completed = True
        elif completed_by_team == 'zespol-2':
            order.team2_completed = True
        
        # Jeśli OBA zespoły już skończyły, zmień główny status
        if order.team1_completed and order.team2_completed:
            order.status = 'ZREALIZOWANE'
            flash(f'Zlecenie {order.order_code} zostało ukończone przez oba zespoły.', 'success')
        else:
            flash(f'Część zlecenia {order.order_code} została ukończona przez {completed_by_team}.', 'info')

    db.session.commit()
    return jsonify({'success': True, 'status': order.status})