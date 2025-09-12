from flask import render_template, request, redirect, url_for, flash, send_from_directory, current_app, make_response, jsonify, send_file, after_this_request
from app import app, db
from app.models import (Order, Client, Product, OrderItem, Attachment, 
                        OrderTemplate, Fabric, MaterialUsage, ProductMaterial, 
                        SubiektProductCache, Material, ProductCategory,
                        OrderFabric, TemplateFabric, ProductFabric) # Dodano importy nowych modeli
from app.forms import (OrderForm, OrderTemplateForm, ProductForm, FabricForm, 
                       MaterialForm, ProductCategoryForm, MaterialEditForm)
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
import io
import pandas as pd
import json

if platform.system() == 'Windows':
    config = pdfkit.configuration(wkhtmltopdf=r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe')
else:
    config = pdfkit.configuration(wkhtmltopdf='/usr/bin/wkhtmltopdf')

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return redirect(url_for('orders_list'))

def calculate_material_summary(order):
    """Oblicza sumaryczne zużycie materiałów i zwraca listę słowników."""
    summary = defaultdict(float)
    units = {}
    
    # --- NOWA LOGIKA DLA TKANIN ---
    # 1. Oblicz sumaryczne zużycie dla każdego produktu na podstawie jego "receptury"
    product_fabric_usage = defaultdict(float)
    for item in order.order_items:
        if item.product:
            # Iterujemy przez tkaniny potrzebne dla danego produktu
            for pf_link in item.product.fabrics_needed:
                # Sumujemy zużycie (ilość sztuk * zużycie na sztukę)
                product_fabric_usage[pf_link.fabric.name.upper()] += pf_link.usage_meters * item.quantity

    structured_summary = []
    # Dodajemy zsumowane tkaniny do finalnego podsumowania
    for name, total_usage in sorted(product_fabric_usage.items()):
        total_val_str = f"{int(total_usage)}" if total_usage == int(total_usage) else f"{total_usage:.2f}"
        structured_summary.append({
            'name': name,
            'quantity': f"{total_val_str} metra"
        })

    # 2. Oblicz zużycie dodatkowych materiałów (logika bez zmian)
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
            
    # 3. Sformatuj wynik materiałów i dodaj do finalnej listy
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
    all_fabrics = Fabric.query.order_by('name').all()
    fabric_choices = [(f.id, f.name) for f in all_fabrics]
    
    # Ustawiamy wybór dla wszystkich pól tkanin w formularzu
    for fabric_form in form.fabrics:
        fabric_form.fabric_id.choices = fabric_choices

    template_id = request.args.get('template_id', type=int)
    if request.method == 'GET' and template_id:
        order_template = OrderTemplate.query.get(template_id)
        if order_template:
            form.client_name.data = order_template.client_name
            form.description.data = order_template.description
            form.login_info.data = order_template.login_info
            
            # Wypełnianie wielu tkanin z szablonu
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
                client_id=client.id,
                description=form.description.data.strip().upper(),
                login_info=form.login_info.data.strip().upper() if form.login_info.data else None,
                deadline=form.deadline.data,
                status='NOWE',
                zlecajacy=form.zlecajacy.data.upper()
            )
            db.session.add(order)

            # Zapisywanie wielu tkanin
            for fabric_data in form.fabrics.data:
                order_fabric_link = OrderFabric(order=order, fabric_id=fabric_data['fabric_id'])
                db.session.add(order_fabric_link)

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
                        attachment = Attachment(order_id=order.id, filename=filename)
                        db.session.add(attachment)

            if form.save_template.data and form.template_name.data:
                template_name = form.template_name.data.strip().upper()
                if not OrderTemplate.query.filter_by(template_name=template_name).first():
                    new_template = OrderTemplate(
                        template_name=template_name,
                        client_name=client.name,
                        description=order.description,
                        login_info=order.login_info
                    )
                    for fabric_link in order.fabrics:
                        new_template.fabrics.append(TemplateFabric(fabric_id=fabric_link.fabric_id))
                    db.session.add(new_template)
                    flash('Szablon został zapisany.', 'info')
                else:
                    flash('Szablon o tej nazwie już istnieje.', 'warning')
            
            db.session.commit()
            flash('Zlecenie zostało dodane.', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'Wystąpił nieoczekiwany błąd: {e}', 'danger')
            return redirect(url_for('new_order'))

        material_summary = calculate_material_summary(order)
        filepath = save_order_as_word(order, material_summary)
        
        return send_file(
            filepath,
            as_attachment=True,
            download_name=os.path.basename(filepath),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    
    existing_clients = Client.query.all()
    all_categories = ProductCategory.query.order_by(ProductCategory.name).all()
    all_templates = OrderTemplate.query.order_by(OrderTemplate.template_name).all()
    
    products_for_js = [
        {'id': p.id, 'name': p.name, 'category_id': p.category_id}
        for p in Product.query.order_by(Product.name).all()
    ]

    return render_template('order_form.html',
                           form=form,
                           clients=existing_clients,
                           categories=all_categories,
                           templates=all_templates,
                           products_json=json.dumps(products_for_js),
                           fabric_choices=fabric_choices)

@app.route('/order_templates')
def order_templates():
    templates = OrderTemplate.query.order_by(OrderTemplate.created_at.desc()).all()
    return render_template('order_templates_list.html', templates=templates)

@app.route('/order_templates/new', methods=['GET', 'POST'])
def new_template():
    form = OrderTemplateForm()
    fabric_choices = [(f.id, f.name) for f in Fabric.query.order_by('name').all()]
    for fabric_form in form.fabrics:
        fabric_form.fabric_id.choices = fabric_choices
    
    clients = Client.query.all()
    if form.validate_on_submit():
        template_name = form.template_name.data.strip().upper()
        if OrderTemplate.query.filter_by(template_name=template_name).first():
            flash('Szablon o tej nazwie już istnieje.', 'danger')
        else:
            template = OrderTemplate(
                template_name=template_name,
                client_name=form.client_name.data.strip().upper(),
                description=form.description.data.strip().upper(),
                login_info=form.login_info.data.strip().upper()
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
    
    fabric_choices = [(f.id, f.name) for f in Fabric.query.order_by('name').all()]
    for fabric_form in form.fabrics:
        fabric_form.fabric_id.choices = fabric_choices

    clients = Client.query.all()
    if form.validate_on_submit():
        template.template_name = form.template_name.data.strip().upper()
        template.client_name = form.client_name.data.strip().upper()
        template.description = form.description.data.strip().upper()
        template.login_info = form.login_info.data.strip().upper()
        
        TemplateFabric.query.filter_by(template_id=template.id).delete()
        for fabric_data in form.fabrics.data:
            template.fabrics.append(TemplateFabric(fabric_id=fabric_data['fabric_id']))

        db.session.commit()
        flash('Szablon został zaktualizowany.', 'success')
        return redirect(url_for('order_templates'))

    if request.method == 'GET':
        form.fabrics.entries = []
        for tf in template.fabrics:
            form.fabrics.append_entry({'fabric_id': tf.fabric_id})

    return render_template('order_template_form.html', form=form, clients=clients, fabric_choices=fabric_choices)

@app.route('/orders')
def orders_list():
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

    for order in all_orders:
        planned_summary = calculate_material_summary(order)
        order.planned_materials = planned_summary

    in_progress_orders = [o for o in all_orders if o.status == 'W REALIZACJI']
    new_orders = [o for o in all_orders if o.status == 'NOWE']
    completed_orders = [o for o in all_orders if o.status == 'ZREALIZOWANE']
    
    years_query = db.session.query(extract('year', Order.created_at)).distinct().all()
    years = sorted({int(y[0]) for y in years_query})
    clients = Client.query.order_by(Client.name).all()

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
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(success=True, order_id=order.id, status=order.status)
    return redirect(url_for('order_detail', order_id=order.id))

@app.route('/orders/<int:order_id>/pdf')
def order_pdf(order_id):
    order = Order.query.get_or_404(order_id)
    material_summary = calculate_material_summary(order)
    
    rendered = render_template('order_pdf.html', order=order, material_summary=material_summary)
    
    pdf = pdfkit.from_string(rendered, False, configuration=config)
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

    return render_template('products_list.html', 
                           products=products, 
                           categories=categories,
                           form=form,
                           current_category_id=category_filter_id)

@app.route('/products/new', methods=['GET', 'POST'])
def add_product():
    form = ProductForm()
    form.category_id.choices = [(c.id, c.name) for c in ProductCategory.query.order_by('name').all()]
    form.category_id.choices.insert(0, (0, '--- Brak ---'))
    available_materials = Material.query.order_by(Material.name).all()
    fabric_choices = [(f.id, f.name) for f in Fabric.query.order_by('name').all()]
    for f_form in form.fabrics_needed:
        f_form.fabric_id.choices = fabric_choices

    if form.validate_on_submit():
        new_product = Product(
            name=form.name.data.strip().upper(),
            description=form.description.data.strip(),
            production_price=form.production_price.data,
            category_id=form.category_id.data if form.category_id.data != 0 else None
        )
        db.session.add(new_product)
        
        for fabric_data in form.fabrics_needed.data:
            pf_link = ProductFabric(
                product=new_product,
                fabric_id=fabric_data['fabric_id'],
                usage_meters=fabric_data['usage_meters']
            )
            db.session.add(pf_link)
        
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
                    product=new_product,
                    material_id=material.id,
                    quantity=quantity
                )
                db.session.add(product_material_link)
        
        db.session.commit()
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
    available_materials = Material.query.order_by(Material.name).all()
    fabric_choices = [(f.id, f.name) for f in Fabric.query.order_by('name').all()]
    for f_form in form.fabrics_needed:
        f_form.fabric_id.choices = fabric_choices

    if form.validate_on_submit():
        product.name = form.name.data.strip().upper()
        product.description = form.description.data.strip()
        product.production_price = form.production_price.data
        product.category_id = form.category_id.data if form.category_id.data != 0 else None
        
        ProductFabric.query.filter_by(product_id=product.id).delete()
        ProductMaterial.query.filter_by(product_id=product.id).delete()
        
        for fabric_data in form.fabrics_needed.data:
            pf_link = ProductFabric(
                product_id=product.id,
                fabric_id=fabric_data['fabric_id'],
                usage_meters=fabric_data['usage_meters']
            )
            db.session.add(pf_link)

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
                
        db.session.commit()
        flash('Produkt został zaktualizowany.', 'success')
        return redirect(url_for('products_list'))
        
    if request.method == 'GET':
        form.fabrics_needed.entries = []
        for pf_link in product.fabrics_needed:
            form.fabrics_needed.append_entry({
                'fabric_id': pf_link.fabric_id,
                'usage_meters': pf_link.usage_meters
            })
        form.materials_needed.entries = []
        for pm_link in product.materials_needed:
            form.materials_needed.append_entry({
                'material_name': pm_link.material.name,
                'quantity': pm_link.quantity
            })
            
    return render_template('product_form.html', form=form, title="Edytuj Produkt", 
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
    
    rendered_html = render_template('label_template.html', 
                                    order=order, 
                                    template_choice=template_choice, 
                                    page_height=page_height)

    options = {
        'page-width': '30mm', 'page-height': page_height, 'margin-top': '0mm',
        'margin-bottom': '0mm', 'margin-left': '0mm', 'margin-right': '0mm',
        'disable-smart-shrinking': '', 'enable-local-file-access': ''
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
    material_summary = calculate_material_summary(order)
    filepath = save_order_as_word(order, material_summary, folder_path='app/order_docs')
    
    return send_file(
        filepath, as_attachment=True,
        download_name=os.path.basename(filepath),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

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

    return render_template(
        'edit_material_usage.html', 
        order=order, 
        materials=materials_to_display, 
        existing_materials=all_possible_materials
    )

@app.route('/kanban')
def kanban():
    orders = Order.query.filter(
        Order.cutting_table == 'skrojone', 
        Order.status.in_(['NOWE', 'W REALIZACJI'])
    ).order_by(Order.created_at.desc()).all()

    team1_orders = [o for o in orders if o.assigned_team in ['zespol-1', 'OBA']]
    team2_orders = [o for o in orders if o.assigned_team in ['zespol-2', 'OBA']]
    unassigned_orders = [o for o in orders if o.assigned_team is None]

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

    team1_orders = [o for o in orders if o.assigned_team in ['zespol-1', 'OBA']]
    team2_orders = [o for o in orders if o.assigned_team in ['zespol-2', 'OBA']]
    unassigned_orders = [o for o in orders if o.assigned_team is None]

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

    if team in ['zespol-1', 'zespol-2', 'OBA']:
        order.assigned_team = team
    else:
        order.assigned_team = None

    db.session.commit()
    return jsonify(success=True, team=order.assigned_team)

@app.route('/krojownia')
def krojownia():
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

    if order.status == 'NOWE' and table is not None:
        order.status = 'W REALIZACJI'
    
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
                updated_fabrics = Fabric.query.filter_by(subiekt_symbol=symbol).update({'price': price})
                updated_materials = Material.query.filter_by(subiekt_symbol=symbol).update({'price': price})
                if updated_fabrics > 0 or updated_materials > 0:
                    updated_count += 1
        db.session.commit()
        message = f'Pomyślnie zaktualizowano ceny dla {updated_count} symboli.'
        return jsonify({'status': 'success', 'message': message}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/v1/receive-subiekt-catalog', methods=['POST'])
def receive_subiekt_catalog():
    auth_key = request.headers.get('X-API-KEY')
    if auth_key != app.config['API_SECRET_KEY']:
        return jsonify({'error': 'Brak autoryzacji'}), 401
    subiekt_products = request.get_json()
    if not subiekt_products:
        return jsonify({'error': 'Brak danych'}), 400
    try:
        db.session.query(SubiektProductCache).delete()
        for product_data in subiekt_products:
            cached_product = SubiektProductCache(
                symbol=product_data.get('symbol'),
                name=product_data.get('name'),
                is_mapped=False
            )
            db.session.add(cached_product)
        db.session.commit()
        return jsonify({
            'status': 'success', 
            'message': f'Pomyślnie zaimportowano {len(subiekt_products)} towarów z Subiekta.'
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
    
@app.route('/subiekt-mapping')
def subiekt_mapping():
    unmapped_products = SubiektProductCache.query.filter_by(is_mapped=False).order_by(SubiektProductCache.symbol).all()
    return render_template('subiekt_mapping.html', products=unmapped_products)

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
# === API DLA APLIKACJI MOBILNEJ - KROJOWNIA ======
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
    if order.status == 'NOWE' and new_table is not None:
        order.status = 'W REALIZACJI'
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
    if new_team in ['zespol-1', 'zespol-2', 'OBA']:
        order.assigned_team = new_team
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
    return render_template('krojownia_mobile.html')

@app.route('/mobile/szwalnia')
def mobile_szwalnia():
    return render_template('szwalnia_mobile.html')

@app.route('/api/order/<int:order_id>/complete', methods=['POST'])
def complete_order_part(order_id):
    order = Order.query.get_or_404(order_id)
    data = request.get_json()
    completed_by_team = data.get('completed_by')
    if order.assigned_team != 'OBA':
        order.status = 'ZREALIZOWANE'
        flash(f'Zlecenie {order.order_code} zostało ukończone.', 'success')
    else:
        if completed_by_team == 'zespol-1':
            order.team1_completed = True
        elif completed_by_team == 'zespol-2':
            order.team2_completed = True
        if order.team1_completed and order.team2_completed:
            order.status = 'ZREALIZOWANE'
            flash(f'Zlecenie {order.order_code} zostało ukończone przez oba zespoły.', 'success')
        else:
            flash(f'Część zlecenia {order.order_code} została ukończona przez {completed_by_team}.', 'info')
    db.session.commit()
    return jsonify({'success': True, 'status': order.status})