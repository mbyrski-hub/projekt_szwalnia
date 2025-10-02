# app/routes.py

from flask import render_template, request, redirect, url_for, flash, send_from_directory, current_app, make_response, jsonify, send_file, after_this_request
from app import app, db
from app.models import (Order, Client, Product, OrderItem, Attachment,
                        OrderTemplate, Fabric, MaterialUsage, ProductMaterial,
                        SubiektProductCache, Material, ProductCategory,
                        OrderFabric, TemplateFabric, ProductFabric, SystemInfo, ProductImage, LabelTemplate, PriceUpdateLog)
from app.forms import (OrderForm, OrderTemplateForm, ProductForm, FabricForm,
                       MaterialForm, ProductCategoryForm, MaterialEditForm, )
from werkzeug.utils import secure_filename
import os
import re
from datetime import datetime, date, timedelta
import pdfkit
import imgkit
from sqlalchemy import extract, func, distinct
from app.doc_generator import save_order_as_word
import platform
from PIL import Image
from collections import defaultdict
import csv
import io
import pandas as pd
import json
import math
from .drive_service import upload_image_to_drive, delete_image_from_drive
from .ai_image_service import generate_ai_images
from werkzeug.datastructures import FileStorage
import threading
from .models import AiImageTask
import requests
import locale
import pathlib


if platform.system() == 'Windows':
    config_pdf = pdfkit.configuration(wkhtmltopdf=r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe')
else:
    config_pdf = pdfkit.configuration(wkhtmltopdf='/usr/bin/wkhtmltopdf')

# NOWA, OSOBNA konfiguracja dla OBRAZÓW przy użyciu imgkit
if platform.system() == 'Windows':
    # --- ZMIANA: Użycie imgkit.config ---
    config_img = imgkit.config(wkhtmltoimage=r'C:\Program Files\wkhtmltopdf\bin\wkhtmltoimage.exe')
else:
    config_img = imgkit.config(wkhtmltoimage='/usr/bin/wkhtmltoimage')

# --- KONIEC POPRAWIONEJ KONFIGURACJI ---

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return redirect(url_for('kokpit'))

def calculate_material_summary(order):
    summary = defaultdict(float)
    units = {}
    product_fabric_usage = defaultdict(float)
    for item in order.order_items:
        if item.product:
            for pf_link in item.product.fabrics_needed:
                product_fabric_usage[pf_link.fabric.name.upper()] += pf_link.usage_meters * item.quantity
    structured_summary = []
    for name, total_usage in sorted(product_fabric_usage.items()):
        total_val_str = f"{int(total_usage)}" if total_usage == int(total_usage) else f"{total_usage:.2f}"
        structured_summary.append({'name': name, 'quantity': f"{total_val_str} metra"})
    for item in order.order_items:
        if not item.product or not item.product.materials_needed:
            continue
        for pm_link in item.product.materials_needed:
            material_name = pm_link.material.name
            quantity_str = pm_link.quantity
            match = re.match(r'^\s*(\d+\.?\d*)\s*(.*)', quantity_str)
            if not match: continue
            value_str, unit_str = match.groups()
            key = (material_name.strip().upper(), unit_str.strip())
            if key not in units: units[key] = unit_str.strip()
            summary[key] += float(value_str) * item.quantity
    for (name, unit_key), total_value in sorted(summary.items()):
        total_val_str = f"{int(total_value)}" if total_value == int(total_value) else f"{total_value:.2f}"
        unit = units.get((name, unit_key), unit_key)
        structured_summary.append({'name': name, 'quantity': f"{total_val_str} {unit}"})
    return structured_summary

# pogoda
# Wklej ten kod w app/routes.py, zastępując poprzednią funkcję get_weather_forecast

def map_wmo_code_to_icon(wmo_code):
    """Mapuje kody pogodowe WMO na animowane ikony SVG."""
    if wmo_code == 0:
        return "day.svg"  # Czyste niebo
    elif wmo_code == 1:
        return "cloudy-day-1.svg"
    elif wmo_code == 2:
        return "cloudy-day-2.svg"
    elif wmo_code == 3:
        return "cloudy.svg"  # Pochmurno
    elif wmo_code in [45, 48]:
        return "snowy-2.svg"  # Mgła
    elif wmo_code in [51, 53, 55, 56, 57]:
        return "rainy-2.svg"  # Mżawka
    elif wmo_code in [61, 63, 65, 66, 67]:
        return "rainy-3.svg"  # Deszcz
    elif wmo_code in [71, 73, 75, 77, 85, 86]:
        return "snowy-3.svg"  # Śnieg
    elif wmo_code in [80, 81, 82]:
        return "rainy-1.svg"  # Przelotne opady deszczu
    elif wmo_code in [95, 96, 99]:
        return "thunder.svg"  # Burza
    else:
        return "day.svg"  # Domyślnie

def get_weather_forecast():
    """Pobiera 4-dniową prognozę pogody dla Bielska-Białej z Open-Meteo."""
    lat, lon = 49.8225, 19.0444
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=weathercode,temperature_2m_max&timezone=Europe/Warsaw&forecast_days=4"
    
    # --- POCZĄTEK ZMIANY ---
    # Słownik do tłumaczenia nazw dni
    day_names_pl = {
        'Mon': 'Pon', 'Tue': 'Wt', 'Wed': 'Śr', 'Thu': 'Czw', 'Fri': 'Pt', 'Sat': 'Sob', 'Sun': 'Ndz'
    }
    # --- KONIEC ZMIANY ---

    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        forecast = []
        daily_data = data['daily']
        for i in range(4):
            day_dt = datetime.strptime(daily_data['time'][i], '%Y-%m-%d')
            
            # --- POCZĄTEK ZMIANY ---
            # Pobranie skróconej nazwy dnia po angielsku (np. 'Mon') i tłumaczenie
            day_abbr_en = day_dt.strftime('%a')
            day_name = day_names_pl.get(day_abbr_en, day_abbr_en) # Użyj tłumaczenia lub oryginału
            # --- KONIEC ZMIANY ---

            forecast.append({
                'is_today': i == 0,
                'date': 'Dziś' if i == 0 else day_dt.strftime('%d.%m'),
                'day_name': day_name, # <-- DODANA NOWA DANA
                'temp': round(daily_data['temperature_2m_max'][i]),
                'icon': map_wmo_code_to_icon(daily_data['weathercode'][i]),
                'description': f"Kod pogody: {daily_data['weathercode'][i]}"
            })
            
        return forecast
    except requests.exceptions.RequestException as e:
        print(f"Błąd podczas pobierania pogody z Open-Meteo: {e}")
        return None
    except (KeyError, IndexError) as e:
        print(f"Błąd przetwarzania danych pogodowych: {e}")
        return None
    
@app.route('/orders/new', methods=['GET', 'POST'])
def new_order():
    form = OrderForm()
    all_fabrics = Fabric.query.order_by('name').all()
    fabric_choices = [(f.id, f.name) for f in all_fabrics]
    for fabric_form in form.fabrics:
        fabric_form.fabric_id.choices = fabric_choices
    template_id = request.args.get('template_id', type=int)
    if request.method == 'GET' and template_id:
        order_template = OrderTemplate.query.get(template_id)
        if order_template:
            form.client_name.data = order_template.client_name
            form.description.data = order_template.description
            form.login_info.data = order_template.login_info
            form.fabrics.entries = []
            for template_fabric in order_template.fabrics:
                form.fabrics.append_entry({'fabric_id': template_fabric.fabric_id})
    if form.validate_on_submit():
        try:
            client_name = form.client_name.data.strip().upper()
            client = Client.query.filter_by(name=client_name).first()
            if not client:
                client = Client(name=client_name)
                db.session.add(client)
                db.session.flush()
            order = Order(
                client_id=client.id, description=form.description.data.strip().upper(),
                login_info=form.login_info.data.strip().upper() if form.login_info.data else None,
                deadline=form.deadline.data, status='NOWE', zlecajacy=form.zlecajacy.data.upper()
            )
            db.session.add(order)
            for fabric_data in form.fabrics.data:
                db.session.add(OrderFabric(order=order, fabric_id=fabric_data['fabric_id']))
            for prod_data in form.products.data:
                product_name = prod_data['product_name'].strip().upper()
                if not product_name: continue
                product = Product.query.filter_by(name=product_name).first()
                if not product:
                    product = Product(name=product_name)
                    db.session.add(product)
                    db.session.flush()
                for variant in prod_data['variants']:
                    size = variant['size'].strip().upper()
                    try: quantity = int(variant['quantity'])
                    except (ValueError, TypeError): quantity = 0
                    if quantity > 0 and size:
                        db.session.add(OrderItem(order_id=order.id, product_id=product.id, size=size, quantity=quantity))
            db.session.flush()
            today = date.today()
            order.order_code = f"{today.year}/{today.month:02d}/{today.day:02d}-{order.id}"
            if 'attachments' in request.files:
                files = request.files.getlist('attachments')
                for file in files:
                    if file and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                        filename = f"{timestamp}_{order.id}_{filename}"
                        file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                        db.session.add(Attachment(order_id=order.id, filename=filename))
            if form.save_template.data and form.template_name.data:
                template_name = form.template_name.data.strip().upper()
                if not OrderTemplate.query.filter_by(template_name=template_name).first():
                    new_template = OrderTemplate(
                        template_name=template_name, client_name=client.name,
                        description=order.description, login_info=order.login_info
                    )
                    for fabric_link in order.fabrics:
                        new_template.fabrics.append(TemplateFabric(fabric_id=fabric_link.fabric_id))
                    db.session.add(new_template)
                    flash('Szablon został zapisany.', 'info')
                else:
                    flash('Szablon o tej nazwie już istnieje.', 'warning')
            db.session.commit()
            flash('Zlecenie zostało dodane.', 'success')
            
            # --- POPRAWKA TUTAJ ---
            # Usunięto generowanie i wysyłanie pliku, zastąpiono je przekierowaniem.
            return redirect(url_for('orders_list'))
            # --- KONIEC POPRAWKI ---

        except Exception as e:
            db.session.rollback()
            flash(f'Wystąpił nieoczekiwany błąd: {e}', 'danger')
            return redirect(url_for('new_order'))
            
    existing_clients = Client.query.all()
    all_categories = ProductCategory.query.order_by(ProductCategory.name).all()
    all_templates = OrderTemplate.query.order_by(OrderTemplate.template_name).all()
    products_for_js = [
        {
            'id': p.id, 'name': p.name, 'category_id': p.category_id,
            'fabrics': [pf.fabric_id for pf in p.fabrics_needed]
        }
        for p in Product.query.order_by(Product.name).all()
    ]
    return render_template('order_form.html', form=form, clients=existing_clients, categories=all_categories,
                           templates=all_templates, products_json=json.dumps(products_for_js), fabric_choices=fabric_choices)

@app.route('/order_templates')
def order_templates():
    templates = OrderTemplate.query.order_by(OrderTemplate.created_at.desc()).all()
    return render_template('order_templates_list.html', templates=templates)

@app.route('/order_templates/new', methods=['GET', 'POST'])
def new_template():
    form = OrderTemplateForm()
    # Przeniesiono pobieranie opcji wyboru na początek
    fabric_choices = [(f.id, f.name) for f in Fabric.query.order_by('name').all()]
    
    # Pętla przypisująca opcje wyboru do każdego podformularza tkaniny
    for fabric_form in form.fabrics:
        fabric_form.fabric_id.choices = fabric_choices
        
    clients = Client.query.all()
    if form.validate_on_submit():
        template_name = form.template_name.data.strip().upper()
        if OrderTemplate.query.filter_by(template_name=template_name).first():
            flash('Szablon o tej nazwie już istnieje.', 'danger')
        else:
            template = OrderTemplate(
                template_name=template_name, client_name=form.client_name.data.strip().upper(),
                description=form.description.data.strip().upper(), login_info=form.login_info.data.strip().upper()
            )
            for fabric_data in form.fabrics.data:
                template.fabrics.append(TemplateFabric(fabric_id=fabric_data['fabric_id']))
            db.session.add(template)
            db.session.commit()
            flash('Szablon został utworzony.', 'success')
            return redirect(url_for('order_templates'))
            
    return render_template('order_template_form.html', form=form, clients=clients, fabric_choices=fabric_choices)

@app.route('/order_templates/edit/<int:template_id>', methods=['GET', 'POST'])
def edit_template(template_id):
    template = OrderTemplate.query.get_or_404(template_id)
    form = OrderTemplateForm(obj=template)
    clients = Client.query.all()
    fabric_choices = [(f.id, f.name) for f in Fabric.query.order_by('name').all()]

    # --- POCZĄTEK KLUCZOWEJ ZMIANY ---
    # Wczytaj zapisane tkaniny do formularza przy żądaniu GET
    if request.method == 'GET':
        form.fabrics.entries = [] # Wyczyść listę, aby uniknąć duplikatów
        for tf in template.fabrics:
            form.fabrics.append_entry({'fabric_id': tf.fabric_id})

    # Ustaw opcje wyboru dla wszystkich (także tych już wczytanych) podformularzy tkanin
    for fabric_form in form.fabrics:
        fabric_form.fabric_id.choices = fabric_choices
    # --- KONIEC KLUCZOWEJ ZMIANY ---

    if form.validate_on_submit():
        template.template_name = form.template_name.data.strip().upper()
        template.client_name = form.client_name.data.strip().upper()
        template.description = form.description.data.strip().upper()
        template.login_info = form.login_info.data.strip().upper()
        
        # Usuń stare powiązania i dodaj nowe na podstawie danych z formularza
        TemplateFabric.query.filter_by(template_id=template.id).delete()
        for fabric_data in form.fabrics.data:
            if fabric_data['fabric_id']: # Upewnij się, że ID tkaniny nie jest puste
                template.fabrics.append(TemplateFabric(fabric_id=fabric_data['fabric_id']))
                
        db.session.commit()
        flash('Szablon został zaktualizowany.', 'success')
        return redirect(url_for('order_templates'))

    return render_template('order_template_form.html', form=form, clients=clients, fabric_choices=fabric_choices)

# ### POCZĄTEK OSTATECZNEJ POPRAWKI w app/routes.py ###

def calculate_order_total_cost(order):
    """Oblicza całkowity koszt zlecenia na podstawie produktów."""
    total_fabric_cost = 0.0
    total_material_cost = 0.0
    # Zmieniamy nazwę, aby było jasne, że to suma przeliczonych kosztów
    adjusted_production_cost = 0.0 

    for item in order.order_items:
        product = item.product
        if not product:
            continue

        # KROK 1: Obliczamy przeliczony koszt dla JEDNEJ SZTUKI produktu
        # (koszt produkcji * 2.5) + 2
        single_item_adjusted_production_cost = (product.production_price or 0.0) * 2.5 + 2
        
        # KROK 2: Mnożymy koszt jednostkowy przez ilość sztuk i dodajemy do sumy
        adjusted_production_cost += single_item_adjusted_production_cost * item.quantity

        # Obliczenia dla tkanin i materiałów pozostają bez zmian
        for pf in product.fabrics_needed:
            fabric_price = pf.fabric.price or 0.0
            total_fabric_cost += (pf.usage_meters * fabric_price) * item.quantity
        for pm in product.materials_needed:
            material_price = pm.material.price or 0.0
            try:
                quantity_val = float(re.match(r'^\s*(\d+\.?\d*)', pm.quantity).group(1))
                total_material_cost += (quantity_val * material_price) * item.quantity
            except (ValueError, AttributeError):
                continue
    
    # Używamy nowej, poprawnie obliczonej sumy kosztów produkcji
    base_subtotal = adjusted_production_cost + total_fabric_cost + total_material_cost
    final_total_cost = base_subtotal * 1.15
    
    return {
        'fabric_cost': round(total_fabric_cost, 2),
        'material_cost': round(total_material_cost, 2),
        'production_cost': round(adjusted_production_cost, 2),
        'total_cost': round(final_total_cost, 2)
    }

# ### KONIEC OSTATECZNEJ POPRAWKI ###





# app/routes.py

# app/routes.py

@app.route('/orders')
def orders_list():
    client_filter = request.args.get('client', '').strip().upper()
    status_filter = request.args.get('status', '').strip().upper()
    
    # --- POCZĄTEK NOWEJ LOGIKI FILTROWANIA ---
    # Domyślnie ustawiamy widok na bieżący rok i miesiąc
    year_filter = request.args.get('year', 'current')
    month_filter = request.args.get('month', 'current')

    orders_query = Order.query.join(Client)

    # Aplikuj filtry (klient, status)
    if client_filter:
        orders_query = orders_query.filter(Client.name == client_filter)
    if status_filter:
        orders_query = orders_query.filter(Order.status == status_filter)

    # Przetłumacz i aplikuj filtry daty
    # Ustal rok do filtrowania
    if year_filter == 'current':
        orders_query = orders_query.filter(extract('year', Order.created_at) == datetime.utcnow().year)
    elif year_filter != 'all':
        try:
            orders_query = orders_query.filter(extract('year', Order.created_at) == int(year_filter))
        except (ValueError, TypeError):
            pass # Ignoruj niepoprawną wartość, jeśli ktoś wpisze ją ręcznie w URL

    # Ustal miesiąc do filtrowania
    if month_filter == 'current':
        orders_query = orders_query.filter(extract('month', Order.created_at) == datetime.utcnow().month)
    elif month_filter != 'all':
        try:
            orders_query = orders_query.filter(extract('month', Order.created_at) == int(month_filter))
        except (ValueError, TypeError):
            pass # Ignoruj niepoprawną wartość

    all_orders = orders_query.order_by(Order.created_at.desc()).all()
    # --- KONIEC NOWEJ LOGIKI FILTROWANIA ---
    
    for order in all_orders:
        order.planned_materials = calculate_material_summary(order)
        order.cost_details = calculate_order_total_cost(order)
        order.has_images = any(item.product.images for item in order.order_items if item.product)

    in_progress_orders = [o for o in all_orders if o.status == 'W REALIZACJI']
    new_orders = [o for o in all_orders if o.status == 'NOWE']
    completed_orders = [o for o in all_orders if o.status == 'ZREALIZOWANE']
    
    years_query = db.session.query(extract('year', Order.created_at)).distinct().all()
    years = sorted({int(y[0]) for y in years_query})
    clients = Client.query.order_by(Client.name).all()
    
    return render_template('orders_list.html', in_progress_orders=in_progress_orders, new_orders=new_orders,
                           completed_orders=completed_orders, clients=clients, years=years,
                           # Przekaż aktualne wartości filtrów do szablonu
                           current_year_filter=year_filter,
                           current_month_filter=month_filter)

@app.route('/orders/history')
def orders_history():
    client_filter = request.args.get('client', '').strip().upper()
    year_filter = request.args.get('year', type=int)
    month_filter = request.args.get('month', type=int)

    # --- POCZĄTEK ZMIANY ---

    # Zapytanie do podsumowania miesięcznego
    summary_query = db.session.query(
        extract('year', Order.created_at).label('year'),
        extract('month', Order.created_at).label('month'),
        func.sum(OrderItem.quantity * Product.production_price).label('total_production_value'),
        func.count(distinct(Order.id)).label('order_count')
    ).join(OrderItem).join(Product).filter(Order.status == 'ZREALIZOWANE')

    # Zapytanie do pełnej listy zleceń
    orders_query = Order.query.join(Client).filter(Order.status == 'ZREALIZOWANE')

    # Aplikowanie filtrów do obu zapytań
    if client_filter:
        summary_query = summary_query.join(Client).filter(Client.name == client_filter)
        orders_query = orders_query.filter(Client.name == client_filter)
    if year_filter:
        summary_query = summary_query.filter(extract('year', Order.created_at) == year_filter)
        orders_query = orders_query.filter(extract('year', Order.created_at) == year_filter)
    if month_filter:
        summary_query = summary_query.filter(extract('month', Order.created_at) == month_filter)
        orders_query = orders_query.filter(extract('month', Order.created_at) == month_filter)

    monthly_summary = summary_query.group_by('year', 'month').order_by(extract('year', Order.created_at).desc(), extract('month', Order.created_at).desc()).all()
    all_orders = orders_query.order_by(Order.created_at.desc()).all()
    
    # --- KONIEC ZMIANY ---

    clients = Client.query.order_by(Client.name).all()
    years_query = db.session.query(extract('year', Order.created_at)).distinct().all()
    years = sorted([y[0] for y in years_query], reverse=True)
    
    return render_template('orders_history.html', 
                           monthly_summary=monthly_summary, 
                           orders=all_orders, # Przekazanie pełnej listy zleceń
                           clients=clients, 
                           years=years)

@app.route('/api/monthly_production_details/<int:year>/<int:month>')
def monthly_production_details(year, month):
    orders_in_month = Order.query.filter(
        extract('year', Order.created_at) == year,
        extract('month', Order.created_at) == month,
        Order.status == 'ZREALIZOWANE'
    ).all()

    product_details = defaultdict(lambda: {'quantity': 0, 'total_value': 0.0})

    for order in orders_in_month:
        for item in order.order_items:
            product_name = item.product.name
            price = item.product.production_price or 0.0
            quantity = item.quantity
            
            product_details[product_name]['quantity'] += quantity
            product_details[product_name]['total_value'] += quantity * price
            product_details[product_name]['price'] = price


    details_list = [
        {
            'name': name,
            'quantity': data['quantity'],
            'price': data['price'],
            'total_value': data['total_value']
        }
        for name, data in product_details.items()
    ]

    return jsonify(sorted(details_list, key=lambda x: x['name']))


@app.route('/orders/<int:order_id>')
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    # ### POCZĄTEK ZMIANY ###
    # Dodajemy obliczanie i przekazywanie podsumowania materiałów
    material_summary = calculate_material_summary(order)
    cost_details = calculate_order_total_cost(order)
    return render_template('order_detail.html', order=order, material_summary=material_summary, cost_details=cost_details)
    # ### KONIEC ZMIANY ###

@app.route('/orders/<int:order_id>/status', methods=['POST'])
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    new_status = request.form.get('status')
    if new_status:
        order.status = new_status
        db.session.commit()
        flash('Status zlecenia został zaktualizowany.', 'success')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(success=True, order_id=order.id, status=order.status)
    return redirect(url_for('order_detail', order_id=order.id))

@app.route('/orders/<int:order_id>/pdf')
def order_pdf(order_id):
    order = Order.query.get_or_404(order_id)
    material_summary = calculate_material_summary(order)
    
    with_images = request.args.get('with_images', 'false') == 'true'
    
    rendered = render_template('order_pdf.html', order=order, material_summary=material_summary, with_images=with_images)
    
    # --- POPRAWKA: Dodanie opcji 'enable-local-file-access' ---
    options = {
        "enable-local-file-access": ""
    }
    pdf = pdfkit.from_string(rendered, False, configuration=config_pdf, options=options)
    
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=zlecenie_{order.id}.pdf'
    return response

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)

@app.route('/products')
def products_list():
    categories = ProductCategory.query.order_by(ProductCategory.name).all()
    category_filter_id = request.args.get('category_id', type=int)
    query = Product.query
    if category_filter_id:
        query = query.filter_by(category_id=category_filter_id)
    products = query.order_by(Product.name).all()
    form = ProductCategoryForm()
    return render_template('products_list.html', products=products, categories=categories,
                           form=form, current_category_id=category_filter_id)

def save_product_picture(form_picture):
    random_hex = os.urandom(8).hex()
    _, f_ext = os.path.splitext(form_picture.filename)
    picture_fn = random_hex + f_ext
    picture_path = os.path.join(current_app.root_path, 'static/product_pics', picture_fn)
    
    # Upewnij się, że folder istnieje
    os.makedirs(os.path.dirname(picture_path), exist_ok=True)
    
    form_picture.save(picture_path)
    return picture_fn

@app.route('/products/new', methods=['GET', 'POST'])
def add_product():
    form = ProductForm()
    form.category_id.choices = [(c.id, c.name) for c in ProductCategory.query.order_by('name').all()]
    form.category_id.choices.insert(0, (0, '--- Brak ---'))
    form.label_template_id.choices = [(lt.id, lt.name) for lt in LabelTemplate.query.order_by('name').all()]
    form.label_template_id.choices.insert(0, (0, '--- Brak ---'))
    
    available_materials = Material.query.order_by(Material.name).all()
    fabric_choices = [(f.id, f.name) for f in Fabric.query.order_by('name').all()]
    for f_form in form.fabrics_needed:
        f_form.fabric_id.choices = fabric_choices
        
    if form.validate_on_submit():
        new_product = Product(
            name=form.name.data.strip().upper(),
            description=form.description.data.strip(),
            production_price=form.production_price.data,
            category_id=form.category_id.data if form.category_id.data != 0 else None,
            label_template_id=form.label_template_id.data if form.label_template_id.data != 0 else None
        )
        db.session.add(new_product)
        db.session.flush()

        new_images_uploaded = []
        if form.images.data:
            for image_file in form.images.data:
                if image_file and image_file.filename:
                    drive_image_id = upload_image_to_drive(image_file)
                    if drive_image_id:
                        new_image = ProductImage(image_id=drive_image_id, product=new_product, image_type='original')
                        db.session.add(new_image)
                        new_images_uploaded.append(new_image)
        
        for fabric_data in form.fabrics_needed.data:
            db.session.add(ProductFabric(product=new_product, fabric_id=fabric_data['fabric_id'], usage_meters=fabric_data['usage_meters']))
        
        for material_data in form.materials_needed.data:
            material_name = material_data['material_name'].strip().upper()
            quantity = material_data['quantity'].strip()
            if material_name and quantity:
                material = Material.query.filter_by(name=material_name).first()
                if not material:
                    material = Material(name=material_name)
                    db.session.add(material)
                    db.session.flush()
                db.session.add(ProductMaterial(product=new_product, material_id=material.id, quantity=quantity))
        
        db.session.commit()

        # --- SEKCJA URUCHAMIANIA ZADANIA AI W TLE ---
        if new_images_uploaded:
            base_image_id = new_images_uploaded[0].image_id
            
            # Tworzymy zadanie w bazie danych zamiast uruchamiać wątek bezpośrednio
            new_task = AiImageTask(
                product_id=new_product.id,
                original_image_id=base_image_id,
                status='pending'
            )
            db.session.add(new_task)
            db.session.commit()
            
            flash('Produkt został dodany. Zdjęcia AI zostaną wygenerowane w tle.', 'info')
        else:
            flash('Produkt został dodany.', 'success')

        return redirect(url_for('products_list'))
        
    return render_template('product_form.html', form=form, title="Dodaj Nowy Produkt",
                           available_materials=available_materials, fabric_choices=fabric_choices)

@app.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    form = ProductForm(obj=product)
    
    form.category_id.choices = [(c.id, c.name) for c in ProductCategory.query.order_by('name').all()]
    form.category_id.choices.insert(0, (0, '--- Brak ---'))
    form.label_template_id.choices = [(lt.id, lt.name) for lt in LabelTemplate.query.order_by('name').all()]
    form.label_template_id.choices.insert(0, (0, '--- Brak ---'))
    
    available_materials = Material.query.order_by(Material.name).all()
    fabric_choices = [(f.id, f.name) for f in Fabric.query.order_by('name').all()]
    for f_form in form.fabrics_needed:
        f_form.fabric_id.choices = fabric_choices

    if form.validate_on_submit():
        product.name = form.name.data.strip().upper()
        product.description = form.description.data.strip()
        product.production_price = form.production_price.data
        product.category_id = form.category_id.data if form.category_id.data != 0 else None
        product.label_template_id = form.label_template_id.data if form.label_template_id.data != 0 else None
        
        ProductFabric.query.filter_by(product_id=product.id).delete()
        ProductMaterial.query.filter_by(product_id=product.id).delete()
        
        for fabric_data in form.fabrics_needed.data:
            if fabric_data.get('fabric_id') and fabric_data.get('usage_meters') is not None:
                db.session.add(ProductFabric(product_id=product.id, fabric_id=fabric_data['fabric_id'], usage_meters=fabric_data['usage_meters']))
        
        for material_data in form.materials_needed.data:
            material_name = material_data['material_name'].strip().upper()
            quantity = material_data['quantity'].strip()
            if material_name and quantity:
                material = Material.query.filter_by(name=material_name).first()
                if not material:
                    material = Material(name=material_name)
                    db.session.add(material)
                    db.session.flush()
                db.session.add(ProductMaterial(product_id=product.id, material_id=material.id, quantity=quantity))

        new_images_uploaded = []
        if form.images.data:
            for image_file in form.images.data:
                if hasattr(image_file, 'filename') and image_file.filename:
                    drive_image_id = upload_image_to_drive(image_file)
                    if drive_image_id:
                        new_image = ProductImage(image_id=drive_image_id, product_id=product.id, image_type='original')
                        db.session.add(new_image)
                        new_images_uploaded.append(new_image)
                        
        db.session.commit()

        # --- SEKCJA URUCHAMIANIA ZADANIA AI W TLE ---
        if new_images_uploaded:
            base_image_id = new_images_uploaded[0].image_id
            
            new_task = AiImageTask(
                product_id=product.id,
                original_image_id=base_image_id,
                status='pending'
            )
            db.session.add(new_task)
            db.session.commit()
            
            flash('Produkt został zaktualizowany. Generowanie nowych zdjęć AI rozpoczęło się w tle.', 'info')
        else:
            flash('Produkt został zaktualizowany.', 'success')
            
        return redirect(url_for('products_list'))

    if request.method == 'GET':
        form.materials_needed.entries = []
        for pm_link in product.materials_needed:
            form.materials_needed.append_entry({'material_name': pm_link.material.name, 'quantity': pm_link.quantity})

    return render_template('product_form.html', form=form, title="Edytuj Produkt",
                           product=product,
                           available_materials=available_materials, fabric_choices=fabric_choices)

@app.route('/products/delete/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    if product.order_items:
        flash('Nie można usunąć produktu, ponieważ jest częścią istniejących zleceń.', 'danger')
        return redirect(url_for('products_list'))
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
    material_summary = calculate_material_summary(order)
    return render_template('order_print.html', order=order, material_summary=material_summary)

@app.route('/orders/<int:order_id>/labels')
def order_labels(order_id):
    order = Order.query.get_or_404(order_id)
    template_choice = request.args.get('template', 'cotton')
    page_height = get_label_page_height(template_choice, target_width_mm=30)
    rendered_html = render_template('label_template.html', order=order, template_choice=template_choice, page_height=page_height)
    options = {
        'page-width': '30mm', 'page-height': page_height, 'margin-top': '0mm',
        'margin-bottom': '0mm', 'margin-left': '0mm', 'margin-right': '0mm',
        'disable-smart-shrinking': '', 'enable-local-file-access': ''
    }
    pdf = pdfkit.from_string(rendered_html, False, configuration=config_pdf, options=options)
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=labels.pdf'
    return response

@app.route('/orders/<int:order_id>/choose_label')
def choose_label(order_id):
    order = Order.query.get_or_404(order_id)
    order_template_images = {
        'cotton': '/static/images/cotton.jpg', 'polyester': '/static/images/polyester.jpg', 'mixed': '/static/images/mixed.jpg'
    }
    return render_template('choose_label.html', order=order, order_template_images=order_template_images)

@app.route('/orders/<int:order_id>/download_doc')
def download_doc(order_id):
    order = Order.query.get_or_404(order_id)
    material_summary = calculate_material_summary(order)
    filepath = save_order_as_word(order, material_summary, folder_path='app/order_docs')
    return send_file(filepath, as_attachment=True, download_name=os.path.basename(filepath),
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

@app.route('/api/fabrics')
def api_fabrics():
    fabrics = Fabric.query.order_by(Fabric.name).all()
    return jsonify([f.name for f in fabrics])

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
    defined_materials = [m[0] for m in db.session.query(Material.name).distinct().all()]
    fabric_names = [f.name for f in Fabric.query.all()]
    usage_materials = [m[0] for m in db.session.query(MaterialUsage.material_name).distinct().all()]
    all_possible_materials = sorted(list(set(defined_materials + fabric_names + usage_materials)))
    return render_template('edit_material_usage.html', order=order, materials=materials_to_display, existing_materials=all_possible_materials)

@app.route('/kanban')
def kanban():
    orders = Order.query.filter(Order.cutting_table == 'skrojone', Order.status.in_(['NOWE', 'W REALIZACJI'])).order_by(Order.created_at.desc()).all()
    team1_orders = [o for o in orders if o.assigned_team in ['zespol-1', 'OBA']]
    team2_orders = [o for o in orders if o.assigned_team in ['zespol-2', 'OBA']]
    unassigned_orders = [o for o in orders if o.assigned_team is None]
    return render_template('kanban.html', team1_orders=team1_orders, team2_orders=team2_orders, unassigned_orders=unassigned_orders)

@app.route('/kanban_partial')
def kanban_partial():
    orders = Order.query.filter(Order.cutting_table == 'skrojone', Order.status.in_(['NOWE', 'W REALIZACJI'])).order_by(Order.created_at.desc()).all()
    team1_orders = [o for o in orders if o.assigned_team in ['zespol-1', 'OBA']]
    team2_orders = [o for o in orders if o.assigned_team in ['zespol-2', 'OBA']]
    unassigned_orders = [o for o in orders if o.assigned_team is None]
    return render_template('kanban_partial.html', team1_orders=team1_orders, team2_orders=team2_orders, unassigned_orders=unassigned_orders)

@app.route('/assign_team', methods=['POST'])
def assign_team():
    data = request.get_json()
    order_id = data.get('order_id')
    team = data.get('team')
    order = Order.query.get_or_404(order_id)
    if team and not order.sewing_started_at:
        order.sewing_started_at = datetime.utcnow()
    if team in ['zespol-1', 'zespol-2', 'OBA']:
        order.assigned_team = team
    else:
        order.assigned_team = None
    db.session.commit()
    return jsonify(success=True, team=order.assigned_team)

@app.route('/krojownia')
def krojownia():
    orders = Order.query.filter(Order.status.in_(['NOWE', 'W REALIZACJI']), Order.assigned_team.is_(None)).order_by(Order.created_at.desc()).all()
    stol_1_orders = [o for o in orders if o.cutting_table == 'stol-1']
    stol_2_orders = [o for o in orders if o.cutting_table == 'stol-2']
    stol_3_orders = [o for o in orders if o.cutting_table == 'stol-3']
    skrojone_orders = [o for o in orders if o.cutting_table == 'skrojone']
    unassigned_orders = [o for o in orders if o.cutting_table is None]
    return render_template('krojownia.html', stol_1_orders=stol_1_orders, stol_2_orders=stol_2_orders,
                           stol_3_orders=stol_3_orders, skrojone_orders=skrojone_orders, unassigned_orders=unassigned_orders)

@app.route('/assign_cutting_table', methods=['POST'])
def assign_cutting_table():
    data = request.get_json()
    order_id = data.get('order_id')
    table = data.get('table')
    order = Order.query.get_or_404(order_id)
    if order.status == 'NOWE' and table is not None:
        order.status = 'W REALIZACJI'
    if table and not order.cutting_started_at:
        order.cutting_started_at = datetime.utcnow()
    if table == 'skrojone':
        order.cutting_finished_at = datetime.utcnow()
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
    images = {'cotton': 'cotton.jpg', 'polyester': 'polyester.jpg', 'mixed': 'mixed.jpg'}
    image_file = images.get(template_choice, 'default_background.jpg')
    image_path = os.path.join(current_app.static_folder, 'images', image_file)
    
    with Image.open(image_path) as img:
        width_px, height_px = img.size
        ratio = height_px / width_px
        target_height_mm = target_width_mm * ratio
        return f"{round(target_height_mm, 1)}mm"
    
@app.route('/orders/<int:order_id>/labels_debug')
def order_labels_debug(order_id):
    order = Order.query.get_or_404(order_id)
    template_choice = request.args.get('template', 'cotton')
    page_height = get_label_page_height(template_choice, target_width_mm=30)
    rendered_html = render_template('label_template.html', 
                                    order=order, 
                                    template_choice=template_choice, 
                                    page_height=page_height)
    return rendered_html

@app.route('/materials-management')
def materials_management():
    fabrics = Fabric.query.order_by(Fabric.name).all()
    materials = Material.query.order_by(Material.name).all()
    return render_template('materials_management.html', fabrics=fabrics, materials=materials)

@app.route('/fabrics/new', methods=['GET', 'POST'])
def add_fabric():
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
    if OrderFabric.query.filter_by(fabric_id=fabric.id).first() or \
       ProductFabric.query.filter_by(fabric_id=fabric.id).first() or \
       TemplateFabric.query.filter_by(fabric_id=fabric.id).first():
        flash('Nie można usunąć tkaniny, jest używana w zleceniach, produktach lub szablonach.', 'danger')
        return redirect(url_for('materials_management'))
    
    db.session.delete(fabric)
    db.session.commit()
    flash('Tkanina została usunięta.', 'success')
    return redirect(url_for('materials_management'))

@app.route('/materials/add', methods=['GET', 'POST'])
def add_material():
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

    all_fabric_names = {f.name.upper() for f in Fabric.query.all()}
    
    final_fabric_summary = defaultdict(float)
    final_material_summary = defaultdict(lambda: defaultdict(float))

    completed_orders = Order.query.filter_by(status='ZREALIZOWANE').all()

    for order in completed_orders:
        if order.materials_used:
            for usage in order.materials_used:
                name = usage.material_name.strip().upper()
                match = re.match(r'^\s*(\d+\.?\d*)\s*(.*)', usage.quantity)
                if match:
                    value, unit = float(match.groups()[0]), match.groups()[1].strip()
                    if name in all_fabric_names:
                        final_fabric_summary[name] += value
                    else:
                        final_material_summary[name][unit] += value
        else:
            planned_summary = calculate_material_summary(order)
            for item in planned_summary:
                name = item['name'].strip().upper()
                match = re.match(r'^\s*(\d+\.?\d*)\s*(.*)', item['quantity'])
                if match:
                    value, unit = float(match.groups()[0]), match.groups()[1].strip()
                    if name in all_fabric_names:
                        final_fabric_summary[name] += value
                    else:
                        final_material_summary[name][unit] += value
    
    if material_filter:
        filtered_fabric_summary = {k: v for k, v in final_fabric_summary.items() if material_filter in k}
        filtered_material_summary = {k: v for k, v in final_material_summary.items() if material_filter in k}
    else:
        filtered_fabric_summary = final_fabric_summary
        filtered_material_summary = final_material_summary
    
    all_materials_query = set([r.material_name.upper() for r in MaterialUsage.query.all()] + [f.name.upper() for f in Fabric.query.all()])
    all_materials_list = sorted(list(all_materials_query))

    return render_template('reports.html', 
                           fabric_summary=filtered_fabric_summary, 
                           material_summary=filtered_material_summary,
                           all_materials=all_materials_list,
                           current_filter=material_filter)

@app.context_processor
def inject_in_progress_orders():
    all_in_progress = Order.query.filter_by(status='W REALIZACJI').order_by(Order.deadline).all()
    krojownia_orders = []
    szwalnia_orders = []
    for order in all_in_progress:
        order.total_quantity = sum(item.quantity for item in order.order_items)
        if order.cutting_table == 'skrojone' and order.assigned_team is not None:
            szwalnia_orders.append(order)
        elif order.cutting_table is not None and order.cutting_table != 'skrojone':
            krojownia_orders.append(order)
    return dict(krojownia_in_progress=krojownia_orders, szwalnia_in_progress=szwalnia_orders)

# app/routes.py

# ... (importy - upewnij się, że PriceUpdateLog jest zaimportowany z modeli)
from app.models import (Order, Client, Product, OrderItem, Attachment,
                        OrderTemplate, Fabric, MaterialUsage, ProductMaterial,
                        SubiektProductCache, Material, ProductCategory,
                        OrderFabric, TemplateFabric, ProductFabric, SystemInfo, ProductImage, LabelTemplate, PriceUpdateLog) # Dodano PriceUpdateLog
# ...

@app.route('/api/v1/update-prices', methods=['POST'])
def receive_price_update():
    auth_key = request.headers.get('X-API-KEY')
    if auth_key != app.config['API_SECRET_KEY']:
        return jsonify({'error': 'Brak autoryzacji'}), 401
    price_data = request.get_json()
    if not price_data:
        return jsonify({'error': 'Brak danych'}), 400
    
    changed_prices_count = 0
    try:
        for item_data in price_data:
            symbol = item_data.get('symbol')
            new_price = item_data.get('price')

            if not (symbol and new_price is not None):
                continue

            item = Fabric.query.filter_by(subiekt_symbol=symbol).first()
            item_type_str = 'Tkanina'
            if not item:
                item = Material.query.filter_by(subiekt_symbol=symbol).first()
                item_type_str = 'Materiał'

            if item:
                if item.price is None or not math.isclose(item.price, new_price):
                    # --- NOWY KOD: Zapis do logu ---
                    log_entry = PriceUpdateLog(
                        item_type=item_type_str,
                        item_name=item.name,
                        old_price=item.price,
                        new_price=new_price
                    )
                    db.session.add(log_entry)
                    # --- KONIEC NOWEGO KODU ---
                    
                    item.price = new_price
                    changed_prices_count += 1

        if changed_prices_count > 0:
            last_update_info = SystemInfo.query.filter_by(key='last_price_update').first()
            if not last_update_info:
                last_update_info = SystemInfo(key='last_price_update')
                db.session.add(last_update_info)
            last_update_info.value = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

            update_count_info = SystemInfo.query.filter_by(key='last_price_update_count').first()
            if not update_count_info:
                update_count_info = SystemInfo(key='last_price_update_count')
                db.session.add(update_count_info)
            update_count_info.value = str(changed_prices_count)

        db.session.commit()
        message = f'Sprawdzono ceny. Zaktualizowano {changed_prices_count} pozycji, których cena uległa zmianie.'
        return jsonify({'status': 'success', 'message': message}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# --- ZAKTUALIZOWANA FUNKCJA ---
@app.route('/api/v1/receive-subiekt-catalog', methods=['POST'])
def receive_subiekt_catalog():
    auth_key = request.headers.get('X-API-KEY')
    if auth_key != app.config['API_SECRET_KEY']:
        return jsonify({'error': 'Brak autoryzacji'}), 401

    subiekt_products = request.get_json()
    if not subiekt_products:
        return jsonify({'error': 'Brak danych'}), 400

    try:
        # Krok 1: Zbierz wszystkie istniejące symbole z bazy danych
        existing_symbols_in_cache = {s[0] for s in db.session.query(SubiektProductCache.symbol).all()}
        existing_symbols_in_fabrics = {s[0] for s in db.session.query(Fabric.subiekt_symbol).filter(Fabric.subiekt_symbol.isnot(None)).all()}
        existing_symbols_in_materials = {s[0] for s in db.session.query(Material.subiekt_symbol).filter(Material.subiekt_symbol.isnot(None)).all()}
        
        all_existing_symbols = existing_symbols_in_cache.union(existing_symbols_in_fabrics).union(existing_symbols_in_materials)

        added_count = 0
        # Krok 2: Iteruj przez otrzymaną listę i dodawaj tylko nowe towary
        for product_data in subiekt_products:
            symbol = product_data.get('symbol')
            # Jeśli symbol istnieje i nie ma go jeszcze w naszej bazie, dodaj go do cache
            if symbol and symbol not in all_existing_symbols:
                cached_product = SubiektProductCache(
                    symbol=symbol,
                    name=product_data.get('name'),
                    is_mapped=False
                )
                db.session.add(cached_product)
                all_existing_symbols.add(symbol) # Dodaj do seta, aby uniknąć duplikatów w tej samej sesji
                added_count += 1
        
        db.session.commit()
        
        return jsonify({
            'status': 'success', 
            'message': f'Pomyślnie sprawdzono katalog. Dodano {added_count} nowych towarów do zmapowania.'
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
    
@app.route('/subiekt-mapping')
def subiekt_mapping():
    unmapped_products = SubiektProductCache.query.filter_by(is_mapped=False).order_by(SubiektProductCache.symbol).all()
    
    # --- POCZĄTEK ZMIANY ---
    # Pobranie daty ostatniej aktualizacji
    last_update_info = SystemInfo.query.filter_by(key='last_price_update').first()
    last_update_timestamp = last_update_info.value if last_update_info else None
    
    # Pobranie liczby zaktualizowanych cen
    update_count_info = SystemInfo.query.filter_by(key='last_price_update_count').first()
    last_update_count = update_count_info.value if update_count_info else None
    # --- KONIEC ZMIANY ---
    
    return render_template('subiekt_mapping.html', 
                           products=unmapped_products,
                           last_update_timestamp=last_update_timestamp,
                           last_update_count=last_update_count) # <-- Przekazanie do szablonu

@app.route('/subiekt-mapping/map', methods=['POST'])
def map_subiekt_product():
    symbol = request.form.get('symbol')
    name = request.form.get('name')
    map_type = request.form.get('map_type')
    product_cache = SubiektProductCache.query.filter_by(symbol=symbol).first()
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

@app.route('/api/v1/get-mapped-symbols', methods=['GET'])
def get_mapped_symbols():
    auth_key = request.headers.get('X-API-KEY')
    if auth_key != app.config['API_SECRET_KEY']:
        return jsonify({'error': 'Brak autoryzacji'}), 401
    fabric_symbols = [f.subiekt_symbol for f in Fabric.query.filter(Fabric.subiekt_symbol.isnot(None)).all()]
    material_symbols = [m.subiekt_symbol for m in Material.query.filter(Material.subiekt_symbol.isnot(None)).all()]
    all_symbols = list(set(fabric_symbols + material_symbols))
    return jsonify(all_symbols), 200

@app.route('/subiekt-mapping/import-csv', methods=['POST'])
def import_subiekt_csv():
    if 'csv_file' not in request.files:
        flash('Nie znaleziono pliku w formularzu.', 'danger')
        return redirect(url_for('subiekt_mapping'))
    file = request.files['csv_file']
    if file.filename == '':
        flash('Nie wybrano żadnego pliku.', 'danger')
        return redirect(url_for('subiekt_mapping'))
    if not (file.filename.endswith('.csv') or file.filename.endswith('.xlsx')):
        flash('Niepoprawny format pliku. Proszę wybrać plik .csv lub .xlsx', 'danger')
        return redirect(url_for('subiekt_mapping'))
    try:
        db.session.query(SubiektProductCache).delete()
        if file.filename.endswith('.xlsx'):
            df = pd.read_excel(file)
        else:
            try:
                df = pd.read_csv(file, encoding='utf-8-sig')
            except UnicodeDecodeError:
                file.stream.seek(0)
                df = pd.read_csv(file, encoding='cp1250')
        if 'Symbol' not in df.columns or 'Nazwa' not in df.columns:
            flash('Błąd: Plik musi zawierać kolumny "Symbol" oraz "Nazwa".', 'danger')
            return redirect(url_for('subiekt_mapping'))
        count = 0
        for index, row in df.iterrows():
            symbol = row.get('Symbol')
            name = row.get('Nazwa')
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

@app.route('/materials-management/edit/<string:item_type>/<int:item_id>', methods=['GET', 'POST'])
def edit_mapped_item(item_type, item_id):
    if item_type == 'fabric':
        item = Fabric.query.get_or_404(item_id)
    elif item_type == 'material':
        item = Material.query.get_or_404(item_id)
    else:
        return "Nieznany typ", 404
    form = MaterialEditForm(obj=item)
    if request.method == 'GET':
        form.material_type.data = item_type
    if form.validate_on_submit():
        new_type = form.material_type.data
        if new_type == item_type:
            item.name = form.name.data.strip().upper()
            item.subiekt_symbol = form.subiekt_symbol.data.strip().upper() or None
            item.price = form.price.data
            flash(f'Zaktualizowano {item.name}.', 'success')
        else:
            if (item_type == 'material' and item.product_links) or \
               (item_type == 'fabric' and (OrderFabric.query.filter_by(fabric_id=item.id).first() or ProductFabric.query.filter_by(fabric_id=item.id).first())):
                flash('Nie można zmienić typu, ponieważ ten element jest już używany.', 'danger')
                return redirect(url_for('materials_management'))
            if new_type == 'fabric':
                new_item = Fabric()
            else:
                new_item = Material()
            new_item.name = form.name.data.strip().upper()
            new_item.subiekt_symbol = form.subiekt_symbol.data.strip().upper() or None
            new_item.price = form.price.data
            db.session.delete(item)
            db.session.add(new_item)
            flash(f'Przeniesiono {new_item.name} do nowej kategorii.', 'success')
        db.session.commit()
        return redirect(url_for('materials_management'))
    return render_template('material_edit_form.html', form=form, item=item)

@app.route('/products/import', methods=['POST'])
def import_products_xlsx():
    if 'xlsx_file' not in request.files:
        flash('Nie znaleziono pliku w formularzu.', 'danger')
        return redirect(url_for('products_list'))
    file = request.files['xlsx_file']
    category_id = request.form.get('category_id_import')
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
    try:
        df = pd.read_excel(file)
        if 'Nazwa' not in df.columns or 'Cena Produkcji' not in df.columns:
            flash('Błąd: Plik musi zawierać kolumny "Nazwa" oraz "Cena Produkcji".', 'danger')
            return redirect(url_for('products_list'))
        imported_count = 0
        skipped_count = 0
        for index, row in df.iterrows():
            name_from_file = str(row['Nazwa']).strip()
            production_price = float(row['Cena Produkcji'])
            prefixed_name = f"{category.name}_{name_from_file}".upper()
            existing_product = Product.query.filter_by(name=prefixed_name).first()
            if existing_product:
                skipped_count += 1
                continue
            new_product = Product(
                name=prefixed_name,
                production_price=production_price,
                category_id=category.id,
                description=""
            )
            db.session.add(new_product)
            imported_count += 1
        db.session.commit()
        flash(f'Import zakończony! Dodano {imported_count} nowych produktów. Pominięto {skipped_count} duplikatów.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Wystąpił nieoczekiwany błąd podczas importu: {e}', 'danger')
    return redirect(url_for('products_list'))

# --- NOWA TRASA DO POMIJANIA ---
@app.route('/subiekt-mapping/skip', methods=['POST'])
def skip_subiekt_product():
    symbol = request.form.get('symbol')
    product_to_skip = SubiektProductCache.query.filter_by(symbol=symbol).first()
    
    if product_to_skip:
        db.session.delete(product_to_skip)
        db.session.commit()
        flash(f'Pominięto towar o symbolu {symbol}.', 'info')
    else:
        flash(f'Nie znaleziono towaru o symbolu {symbol} do pominięcia.', 'warning')
        
    return redirect(url_for('subiekt_mapping'))


@app.route('/calculator')
def calculator():
    products = Product.query.order_by(Product.name).all()
    fabrics = Fabric.query.order_by(Fabric.name).all()
    materials = Material.query.order_by(Material.name).all()
    categories = ProductCategory.query.order_by(ProductCategory.name).all()
    products_data = {}
    for p in products:
        products_data[p.id] = {
            'name': p.name,
            'production_price': p.production_price,
            'category_id': p.category_id,
            'fabrics_needed': [
                {'id': pf.fabric.id, 'usage': pf.usage_meters} 
                for pf in p.fabrics_needed
            ],
            'materials_needed': [
                {'id': pm.material.id, 'name': pm.material.name, 'quantity': pm.quantity} 
                for pm in p.materials_needed
            ]
        }
    return render_template('calculator.html',
                           products=products, fabrics=fabrics, materials=materials,
                           categories=categories, products_json=json.dumps(products_data))

# =================================================
# === API DLA APLIKACJI MOBILNEJ ==================
# =================================================
@app.route('/api/krojownia/orders', methods=['GET'])
def get_krojownia_orders():
    orders = Order.query.filter(
        Order.status.in_(['NOWE', 'W REALIZACJI']),
        Order.assigned_team.is_(None) 
    ).order_by(Order.created_at.desc()).all()
    orders_list = []
    for order in orders:
        orders_list.append({
            'id': order.id, 'order_code': order.order_code, 'client_name': order.client.name,
            'status': order.status, 'cutting_table': order.cutting_table
        })
    return jsonify(orders_list)

@app.route('/api/order/<int:order_id>/assign_table', methods=['POST'])
def api_assign_cutting_table(order_id):
    order = Order.query.get_or_404(order_id)
    data = request.get_json()
    if not data or 'table' not in data:
        return jsonify({'error': 'Brak nazwy stołu w zapytaniu'}), 400
    
    new_table = data.get('table')

    if order.status == 'NOWE' and new_table is not None:
        order.status = 'W REALIZACJI'
    
    # Ustaw datę rozpoczęcia krojenia
    if new_table and not order.cutting_started_at:
        order.cutting_started_at = datetime.utcnow()
        
    # Ustaw datę zakończenia krojenia
    if new_table == 'skrojone':
        order.cutting_finished_at = datetime.utcnow()

    if new_table in ['stol-1', 'stol-2', 'stol-3', 'skrojone']:
        order.cutting_table = new_table
    else:
        order.cutting_table = None

    db.session.commit()
    return jsonify({'success': True, 'message': f'Zlecenie {order.order_code} przypisano do {order.cutting_table or "Nieprzypisane"}.'})

@app.route('/api/szwalnia/orders', methods=['GET'])
def get_szwalnia_orders():
    orders = Order.query.filter(
        Order.cutting_table == 'skrojone', 
        Order.status.in_(['NOWE', 'W REALIZACJI'])
    ).order_by(Order.created_at.desc()).all()
    orders_list = []
    for order in orders:
        orders_list.append({
            'id': order.id, 'order_code': order.order_code, 'client_name': order.client.name, 'status': order.status,
            'assigned_team': order.assigned_team, 'team1_completed': order.team1_completed, 'team2_completed': order.team2_completed
        })
    return jsonify(orders_list)

@app.route('/api/order/<int:order_id>/assign_team', methods=['POST'])
def api_assign_team(order_id):
    order = Order.query.get_or_404(order_id)
    data = request.get_json()
    if not data or 'team' not in data:
        return jsonify({'error': 'Brak nazwy zespołu w zapytaniu'}), 400

    new_team = data.get('team')
    
    # Ustaw datę rozpoczęcia szycia
    if new_team and not order.sewing_started_at:
        order.sewing_started_at = datetime.utcnow()

    if new_team in ['zespol-1', 'zespol-2', 'OBA']:
        order.assigned_team = new_team
    else:
        order.assigned_team = None
        
    db.session.commit()
    return jsonify({'success': True, 'message': f'Zlecenie {order.order_code} przypisano do {order.assigned_team or "Nieprzypisane"}.'})

# ZMIANA: Dodajemy logikę zapisu daty zakończenia szycia
@app.route('/api/order/<int:order_id>/complete', methods=['POST'])
def complete_order_part(order_id):
    order = Order.query.get_or_404(order_id)
    data = request.get_json()
    completed_by_team = data.get('completed_by')

    if order.assigned_team != 'OBA':
        order.status = 'ZREALIZOWANE'
        order.sewing_finished_at = datetime.utcnow() # Ustaw datę zakończenia
        flash(f'Zlecenie {order.order_code} zostało ukończone.', 'success')
    else:
        if completed_by_team == 'zespol-1':
            order.team1_completed = True
        elif completed_by_team == 'zespol-2':
            order.team2_completed = True
        
        if order.team1_completed and order.team2_completed:
            order.status = 'ZREALIZOWANE'
            order.sewing_finished_at = datetime.utcnow() # Ustaw datę zakończenia
            flash(f'Zlecenie {order.order_code} zostało ukończone przez oba zespoły.', 'success')
        else:
            flash(f'Część zlecenia {order.order_code} została ukończona przez {completed_by_team}.', 'info')

    db.session.commit()
    return jsonify({'success': True, 'status': order.status})
    
@app.route('/api/order/<int:order_id>/details')
def get_order_details(order_id):
    order = Order.query.get_or_404(order_id)
    details = {
        'id': order.id, 'order_code': order.order_code, 'client_name': order.client.name, 'description': order.description,
        'deadline': order.deadline.strftime('%Y-%m-%d'), 'fabrics': [of.fabric.name for of in order.fabrics],
        'products': [ { 'name': item.product.name, 'size': item.size, 'quantity': item.quantity } for item in order.order_items ]
    }
    return jsonify(details)

# =================================================
# === WIDOKI DLA APLIKACJI MOBILNEJ ===============
# =================================================
@app.route('/mobile/krojownia')
def mobile_krojownia():
    return render_template('krojownia_mobile.html')

@app.route('/mobile/szwalnia')
def mobile_szwalnia():
    return render_template('szwalnia_mobile.html')

# =================================================
# === PLIKI DO POBRANIA ===========================
# =================================================
@app.route('/download/synchronizator')
def download_synchronizator():
    return send_from_directory(
        os.path.join(current_app.static_folder, 'synchronizator'),
        'Synchronizator 3.5.exe',
        as_attachment=True
    )

@app.route('/download/config')
def download_config():
    return send_from_directory(
        os.path.join(current_app.static_folder, 'synchronizator'),
        'config.json',
        as_attachment=True
    )

@app.route('/print_test')
def print_test():
    return render_template('print_test.html', title='Test Drukowania')

@app.route('/products/delete_image/<int:image_id>', methods=['POST'])
def delete_product_image(image_id):
    image_to_delete = ProductImage.query.get_or_404(image_id)
    product_id = image_to_delete.product_id
    delete_image_from_drive(image_to_delete.image_id)
    db.session.delete(image_to_delete)
    db.session.commit()
    flash('Zdjęcie zostało usunięte.', 'success')
    return redirect(url_for('edit_product', product_id=product_id))

@app.route('/download_print_server')
def download_print_server():
    return send_from_directory('static/synchronizator', 'SerwerDrukuSzwalnia.exe', as_attachment=True)

@app.route('/label-designer')
def label_designer():
    """Wyświetla stronę kreatora metek."""
    all_templates = LabelTemplate.query.order_by(LabelTemplate.name).all()
    return render_template('label_designer.html', title="Kreator Metek", templates=all_templates)

@app.route('/api/label-templates', methods=['POST'])
def create_label_template():
    """Odbiera dane JSON z kreatora i zapisuje szablon metki."""
    data = request.get_json()

    if not data or 'name' not in data or 'content_json' not in data:
        return jsonify({'status': 'error', 'message': 'Brak wymaganych danych.'}), 400

    template_name = data['name'].strip().upper()
    if not template_name:
        return jsonify({'status': 'error', 'message': 'Nazwa szablonu nie może być pusta.'}), 400

    # Sprawdzenie, czy szablon o tej nazwie już istnieje
    if LabelTemplate.query.filter_by(name=template_name).first():
        return jsonify({'status': 'error', 'message': 'Szablon o tej nazwie już istnieje.'}), 409

    try:
        new_template = LabelTemplate(
            name=template_name,
            content_json=json.dumps(data['content_json']) # Zapisujemy dane jako string JSON
        )
        db.session.add(new_template)
        db.session.commit()
        flash('Szablon metki został pomyślnie zapisany!', 'success')
        return jsonify({'status': 'success', 'message': 'Szablon zapisany.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- POCZĄTEK NOWEGO KODU ---
@app.route('/api/label-templates/<int:template_id>', methods=['PUT'])
def update_label_template(template_id):
    """Odbiera dane JSON i aktualizuje istniejący szablon metki."""
    template = LabelTemplate.query.get_or_404(template_id)
    data = request.get_json()

    if not data or 'name' not in data or 'content_json' not in data:
        return jsonify({'status': 'error', 'message': 'Brak wymaganych danych.'}), 400

    template_name = data['name'].strip().upper()
    if not template_name:
        return jsonify({'status': 'error', 'message': 'Nazwa szablonu nie może być pusta.'}), 400

    # Sprawdzenie, czy inna metka nie ma już takiej nazwy
    existing = LabelTemplate.query.filter(LabelTemplate.name == template_name, LabelTemplate.id != template_id).first()
    if existing:
        return jsonify({'status': 'error', 'message': 'Inny szablon o tej nazwie już istnieje.'}), 409

    try:
        template.name = template_name
        template.content_json = json.dumps(data['content_json'])
        db.session.commit()
        flash('Szablon metki został pomyślnie zaktualizowany!', 'success')
        return jsonify({'status': 'success', 'message': 'Szablon zaktualizowany.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
# --- KONIEC NOWEGO KODU ---




@app.route('/api/label-templates/<int:template_id>')
def get_label_template(template_id):
    """Zwraca dane JSON dla konkretnego szablonu metki."""
    template = LabelTemplate.query.get_or_404(template_id)
    
    # Parsujemy string JSON z bazy danych na prawdziwy obiekt JSON
    content = json.loads(template.content_json)
    
    return jsonify({
        'id': template.id,
        'name': template.name,
        'content_json': content
    })

@app.route('/api/label-templates/<int:template_id>', methods=['DELETE'])
def delete_label_template(template_id):
    """Usuwa szablon metki z bazy danych."""
    template = LabelTemplate.query.get_or_404(template_id)
    try:
        db.session.delete(template)
        db.session.commit()
        flash('Szablon został usunięty.', 'success')
        return jsonify({'status': 'success', 'message': 'Szablon usunięty.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    
    # --- POCZĄTEK ZMIANY: ZAKTUALIZOWANE I POPRAWIONE TRASY API DO DRUKOWANIA ---

@app.route('/api/order/<int:order_id>/prepare_labels')
def prepare_labels_for_order(order_id):
    order = Order.query.get_or_404(order_id)
    
    products_with_labels = []
    products_without_labels = []
    
    # Używamy seta, aby uniknąć duplikatów produktów
    unique_products = {item.product for item in order.order_items}

    for product in unique_products:
        if product.label_template_id:
            products_with_labels.append({
                'product_name': product.name,
                'template_id': product.label_template_id,
                'template_name': product.label_template.name
            })
        else:
            products_without_labels.append({
                'product_name': product.name,
                'product_id': product.id
            })

    all_templates = LabelTemplate.query.order_by(LabelTemplate.name).all()
    templates_list = [{'id': t.id, 'name': t.name} for t in all_templates]

    return jsonify({
        'with_labels': products_with_labels,
        'without_labels': products_without_labels,
        'all_templates': templates_list
    })

@app.route('/api/order/<int:order_id>/prepare_labels_for_printing', methods=['POST'])
def prepare_labels_for_printing(order_id):
    order = Order.query.get_or_404(order_id)
    assigned_templates = request.json.get('assigned_templates', {})
    
    labels_to_render = []
    
    for item in order.order_items:
        template_id = item.product.label_template_id or assigned_templates.get(str(item.product.id))
        
        if not template_id:
            continue

        template = LabelTemplate.query.get(template_id)
        if template:
            labels_to_render.append({
                'quantity': item.quantity,
                'size': item.size,
                'template_json': json.loads(template.content_json)
            })
            
    return jsonify(labels_to_render)

@app.route('/api/product/<int:product_id>/ai_images')
def get_ai_images(product_id):
    ai_images = ProductImage.query.filter(
        ProductImage.product_id == product_id,
        ProductImage.image_type.like('%_ai')
    ).all()
    
    images_data = {
        'catalog': [],
        'model': []
    }

    for img in ai_images:
        url = f"https://drive.google.com/thumbnail?id={img.image_id}&sz=w1000"
        if 'catalog' in img.image_type:
            images_data['catalog'].append(url)
        elif 'model' in img.image_type:
            images_data['model'].append(url)
            
    return jsonify(images_data)

# app/routes.py

def generate_and_save_ai_images_task(app, product_id, original_image_id, product_name):
    """
    Funkcja uruchamiana w osobnym wątku do generowania i zapisywania obrazów AI.
    """
    with app.app_context():
        print(f"Rozpoczynanie zadania AI w tle dla produktu ID: {product_id}")
        
        base_image_url = f"https://drive.google.com/thumbnail?id={original_image_id}&sz=w1024"
        
        # Prompty instruujące model, by bazował na wgranym zdjęciu
        prompts = {
            'catalog_ai': (
                f"Analyze the provided image of a piece of clothing. Recreate it as a professional, photorealistic e-commerce catalog photo. "
                f"The garment is: '{product_name.replace('_', ' ')}'. "
                f"Place the *exact same garment* on a clean, uniform, light grey background (#f2f2f2). "
                f"It must be perfectly ironed and laid flat. The lighting should be soft and professional. "
                f"Do not change the design, color, or texture of the clothing from the original image."
            ),
            'model_ai': (
                f"Analyze the provided image of the garment: '{product_name.replace('_', ' ')}'. "
                f"Create a new, full-body, photorealistic image of a male model wearing this *exact* piece of clothing. "
                f"The model is standing in a natural, confident pose inside a bright, modern warehouse with a blurred background. "
                f"Ensure the clothing's color, design, and details from the original image are accurately represented on the model."
            )
        }

        generated_images = generate_ai_images(base_image_url, prompts)

        if not generated_images:
            print(f"Nie udało się wygenerować obrazów AI dla produktu ID: {product_id}")
            task = AiImageTask.query.filter_by(product_id=product_id, original_image_id=original_image_id).first()
            if task:
                task.status = 'error'
                db.session.commit()
            return

        product = Product.query.get(product_id)
        for image_type, image_bytes in generated_images.items():
            image_file = FileStorage(
                stream=io.BytesIO(image_bytes),
                filename=f"{product.name.lower()}_{image_type}.png",
                content_type='image/png'
            )
            
            ai_drive_id = upload_image_to_drive(image_file)
            if ai_drive_id:
                ai_image_record = ProductImage(
                    image_id=ai_drive_id,
                    product_id=product.id,
                    image_type=image_type
                )
                db.session.add(ai_image_record)
        
        task = AiImageTask.query.filter_by(product_id=product_id, original_image_id=original_image_id).first()
        if task:
            task.status = 'complete'
        
        db.session.commit()
        print(f"Zakończono zadanie AI w tle i zapisano obrazy dla produktu ID: {product_id}")

@app.route('/kokpit')
def kokpit():
    # Statystyki zleceń
    stats = {
        'nowe': Order.query.filter_by(status='NOWE').count(),
        'w_realizacji': Order.query.filter_by(status='W REALIZACJI').count(),
        'zrealizowane': Order.query.filter_by(status='ZREALIZOWANE').count()
    }

    # Zużycie tkanin w bieżącym miesiącu (POPRAWIONA LOGIKA)
    current_month = datetime.utcnow().month
    current_year = datetime.utcnow().year

    # Pobierz nazwy wszystkich tkanin, aby odróżnić je od innych materiałów
    all_fabric_names = {f.name.upper() for f in Fabric.query.all()}
    fabric_summary_dict = defaultdict(float)

    # Pobierz zrealizowane zlecenia z bieżącego miesiąca
    completed_orders_this_month = Order.query.filter(
        Order.status == 'ZREALIZOWANE',
        extract('month', Order.created_at) == current_month,
        extract('year', Order.created_at) == current_year
    ).all()

    # Przetwarzaj każde zlecenie
    for order in completed_orders_this_month:
        # 1. Sprawdź, czy istnieje ręcznie wprowadzone zużycie
        if order.materials_used:
            for usage in order.materials_used:
                name = usage.material_name.strip().upper()
                # Jeśli materiał jest tkaniną, dodaj go do podsumowania
                if name in all_fabric_names:
                    # Wyodrębnij wartość liczbową z ilości (np. "2.5 metra")
                    match = re.match(r'^\s*(\d+\.?\d*)', usage.quantity)
                    if match:
                        value = float(match.groups()[0])
                        fabric_summary_dict[name] += value
        # 2. Jeśli nie, oblicz zużycie planowane
        else:
            planned_summary = calculate_material_summary(order)
            for item in planned_summary:
                name = item['name'].strip().upper()
                if name in all_fabric_names:
                    match = re.match(r'^\s*(\d+\.?\d*)', item['quantity'])
                    if match:
                        value = float(match.groups()[0])
                        fabric_summary_dict[name] += value
    
    # Zaokrąglij wartości na końcu
    fabric_summary_dict = {name: round(total, 2) for name, total in fabric_summary_dict.items()}
    
    # --- NOWY KOD: Obliczanie wartości produkcji ---
    
    # Wartość produkcji w bieżącym miesiącu
    monthly_production_value = db.session.query(
        func.sum(OrderItem.quantity * Product.production_price)
    ).join(Product).join(Order)\
     .filter(Order.status == 'ZREALIZOWANE')\
     .filter(extract('month', Order.created_at) == current_month)\
     .filter(extract('year', Order.created_at) == current_year).scalar() or 0.0

    # Wartość produkcji z 3 ostatnich miesięcy
    last_months_production = []
    for i in range(1, 4):
        month = current_month - i
        year = current_year
        if month < 1:
            month += 12
            year -= 1
        
        value = db.session.query(
            func.sum(OrderItem.quantity * Product.production_price)
        ).join(Product).join(Order)\
         .filter(Order.status == 'ZREALIZOWANE')\
         .filter(extract('month', Order.created_at) == month)\
         .filter(extract('year', Order.created_at) == year).scalar() or 0.0
        
        last_months_production.append({
            'month': month,
            'year': year,
            'value': round(value, 2)
        })

    # --- KONIEC NOWEGO KODU ---

    # Ostatnie aktywności, dochodowe produkty, etc. (bez zmian)
    recent_activities = Order.query.order_by(Order.created_at.desc()).limit(5).all()
    most_profitable_products_query = db.session.query(
        Product.name,
        func.sum(OrderItem.quantity * Product.production_price).label('total_profit')
    ).join(OrderItem).join(Order)\
    .filter(Order.status == 'ZREALIZOWANE')\
    .filter(extract('month', Order.created_at) == current_month)\
    .filter(extract('year', Order.created_at) == current_year)\
    .group_by(Product.name).order_by(func.sum(OrderItem.quantity * Product.production_price).desc()).limit(5).all()

    # --- NOWY KOD: Konwersja danych na listę ---
    most_profitable_products = [list(row) for row in most_profitable_products_query]
    # --- KONIEC NOWEGO KODU -----
    bottlenecks = Order.query.filter_by(status='W REALIZACJI').order_by(Order.created_at.asc()).limit(5).all()
    # --- NOWY KOD: Pobranie TOP 5 klientów ---
    top_clients_query = db.session.query(
        Client.name,
        func.count(Order.id).label('order_count')
    ).join(Order, Client.id == Order.client_id)\
    .filter(Order.status == 'ZREALIZOWANE')\
    .group_by(Client.name)\
    .order_by(func.count(Order.id).desc())\
    .limit(5).all()
    # --- KONIEC NOWEGO KODU ---
    upcoming_deadlines = Order.query.filter(
        Order.deadline.between(datetime.utcnow().date(), datetime.utcnow().date() + timedelta(days=7))
    ).order_by(Order.deadline.asc()).all()

# --- NOWY KOD: Pobranie ostatnich aktualizacji cen ---
    recent_price_updates = PriceUpdateLog.query.order_by(PriceUpdateLog.changed_at.desc()).limit(8).all()
    # --- KONIEC NOWEGO KODU ---
    weather_forecast = get_weather_forecast()

    return render_template(
        'kokpit.html',
        stats=stats,
        fabric_summary=fabric_summary_dict,
        monthly_production_value=round(monthly_production_value, 2),
        last_months_production=last_months_production, # Dodane
        recent_activities=recent_activities,
        most_profitable_products=most_profitable_products,
        bottlenecks=bottlenecks,
        upcoming_deadlines=upcoming_deadlines,
        recent_price_updates=recent_price_updates,  
        top_clients=top_clients_query,  # <-- DODAJ TĘ LINIĘ
        api_key=app.config.get('API_SECRET_KEY'), 
        weather_forecast=weather_forecast  # Dodajemy pogodę
    )


@app.route('/label_gallery_content')
def label_gallery_content():
    """Renderuje samą zawartość galerii metek do wstrzyknięcia w modal."""
    templates = LabelTemplate.query.order_by(LabelTemplate.name).all()
    return render_template('_label_gallery_content.html', templates=templates)

@app.route('/trigger-local-sync', methods=['POST'])
def trigger_local_sync():
    """
    Endpoint pośredniczący (proxy), który bezpiecznie wywołuje synchronizację
    w lokalnej aplikacji desktopowej.
    """
    # Adres, pod którym aplikacja desktopowa nasłuchuje na polecenia
    local_sync_url = 'http://localhost:5001/trigger-sync'

    # Pobranie klucza API z konfiguracji serwera (zgodnie z Twoją strukturą)
    api_key = current_app.config.get('API_SECRET_KEY')

    if not api_key:
        return jsonify({'error': 'Brak skonfigurowanego klucza API po stronie serwera.'}), 500

    headers = {
        'X-API-KEY': api_key
    }

    try:
        response = requests.post(local_sync_url, headers=headers, timeout=5)
        return jsonify(response.json()), response.status_code

    except requests.exceptions.ConnectionError:
        return jsonify({
            'error': 'Nie można nawiązać połączenia z lokalnym serwerem. Upewnij się, że aplikacja "Szwalnia_Serwer" jest uruchomiona na tym komputerze.'
        }), 503
    except Exception as e:
        return jsonify({'error': f'Wystąpił nieoczekiwany błąd: {str(e)}'}), 500
    
    # --- NOWA TRASA DO MASOWEJ ZMIANY CEN ---
@app.route('/products/update_prices_by_category', methods=['POST'])
def update_prices_by_category():
    """
    Masowo aktualizuje ceny produktów w danej kategorii o podany procent.
    """
    try:
        category_id = int(request.form.get('category_id_mass_update'))
        percentage_change = float(request.form.get('percentage_change'))
    except (ValueError, TypeError):
        flash('Błędne dane. Upewnij się, że kategoria jest wybrana, a procent jest liczbą.', 'danger')
        return redirect(url_for('products_list'))

    if not category_id:
        flash('Musisz wybrać kategorię, dla której chcesz zaktualizować ceny.', 'danger')
        return redirect(url_for('products_list'))

    # Znajdź wszystkie produkty w danej kategorii
    products_to_update = Product.query.filter_by(category_id=category_id).all()

    if not products_to_update:
        flash('W wybranej kategorii nie ma żadnych produktów do zaktualizowania.', 'info')
        return redirect(url_for('products_list', category_id=category_id))

    try:
        updated_count = 0
        multiplier = 1 + (percentage_change / 100)
        
        for product in products_to_update:
            # Upewnij się, że cena nie jest None
            old_price = product.production_price or 0.0
            # Oblicz nową cenę i zaokrąglij do 2 miejsc po przecinku
            new_price = round(old_price * multiplier, 2)
            
            product.production_price = new_price
            updated_count += 1
        
        db.session.commit()
        flash(f'Pomyślnie zaktualizowano ceny dla {updated_count} produktów w wybranej kategorii.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Wystąpił nieoczekiwany błąd podczas aktualizacji cen: {e}', 'danger')

    return redirect(url_for('products_list', category_id=category_id))
# --- KONIEC NOWEJ TRASY ---

@app.route('/api/v1/get-recent-price-updates')
def get_recent_price_updates():
    """
    Zwraca ostatnie aktualizacje cen zarejestrowane w logach.
    Pobiera zmiany, które nastąpiły od czasu podanego w parametrze 'since'.
    """
    since_param = request.args.get('since')
    if not since_param:
        return jsonify({'error': 'Brak parametru "since"'}), 400

    try:
        # Konwertujemy czas z formatu ISO i dodajemy mały margines, by uniknąć problemów z precyzją
        since_dt = datetime.fromisoformat(since_param.replace('Z', '+00:00')) - timedelta(seconds=2)
        
        # Pobieramy wszystkie nowsze logi
        updates = PriceUpdateLog.query.filter(PriceUpdateLog.changed_at > since_dt).order_by(PriceUpdateLog.changed_at.desc()).all()

        updates_list = [
            {
                'item_name': update.item_name,
                'item_type': update.item_type,
                'old_price': update.old_price,
                'new_price': update.new_price
            } for update in updates
        ]
        return jsonify(updates_list)
        
    except ValueError:
        return jsonify({'error': 'Niepoprawny format daty dla parametru "since".'}), 400