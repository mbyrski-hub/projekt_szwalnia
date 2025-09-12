from app import db
from datetime import datetime
from sqlalchemy.orm import validates

class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    orders = db.relationship('Order', backref='client', lazy=True)

    def __repr__(self):
        return f'<Client {self.name}>'

class Fabric(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    price = db.Column(db.Float, nullable=True)
    subiekt_symbol = db.Column(db.String(50), nullable=True, unique=True, index=True)
    orders = db.relationship('Order', backref='fabric')

    def __repr__(self):
        return f'<Fabric {self.name}>'

class Material(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    price = db.Column(db.Float, nullable=True)
    subiekt_symbol = db.Column(db.String(50), nullable=True, unique=True, index=True)

    def __repr__(self):
        return f'<Material {self.name}>'

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_code = db.Column(db.String(50), nullable=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    description = db.Column(db.Text, nullable=False)
    login_info = db.Column(db.Text, nullable=True)
    deadline = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(50), nullable=False, default='NOWE')
    zlecajacy = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    fabric_id = db.Column(db.Integer, db.ForeignKey('fabric.id'), nullable=True)
    assigned_team = db.Column(db.String(50), nullable=True)
    cutting_table = db.Column(db.String(50), nullable=True)
    # --- NOWE POLA ---
    # Będą śledzić, czy dany zespół ukończył swoją część podzielonego zlecenia
    team1_completed = db.Column(db.Boolean, default=False, nullable=False)
    team2_completed = db.Column(db.Boolean, default=False, nullable=False)
    
    order_items = db.relationship('OrderItem', backref='order', lazy=True, cascade="all, delete-orphan")
    attachments = db.relationship('Attachment', backref='order', lazy=True, cascade="all, delete-orphan")
    materials_used = db.relationship('MaterialUsage', backref='order', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Order {self.id}>'

class ProductCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)

    def __repr__(self):
        return f'<ProductCategory {self.name}>'

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True) 
    fabric_usage_meters = db.Column(db.Float, nullable=False, default=0.0)
    
    # NOWE POLE - CENA PRODUKCJI (np. koszt robocizny)
    production_price = db.Column(db.Float, nullable=False, default=0.0)
    
    category_id = db.Column(db.Integer, db.ForeignKey('product_category.id'), nullable=True)
    category = db.relationship('ProductCategory', backref='products')
    
    materials_needed = db.relationship('ProductMaterial', backref='product', lazy=True, cascade="all, delete-orphan")
    order_items = db.relationship('OrderItem', backref='product')

    @validates('fabric_usage_meters', 'production_price')
    def validate_positive_values(self, key, value):
        if value < 0:
            raise ValueError(f"{key} nie może być wartością ujemną.")
        return value

    def __repr__(self):
        return f'<Product {self.name}>'

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    size = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    def __repr__(self):
        return f'<OrderItem Order:{self.order_id}>'

class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Attachment {self.filename}>'

class OrderTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    template_name = db.Column(db.String(100), nullable=False, unique=True)
    client_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    login_info = db.Column(db.Text, nullable=True)
    fabric_id = db.Column(db.Integer, db.ForeignKey('fabric.id'), nullable=True)
    fabric = db.relationship('Fabric')

    def __repr__(self):
        return f'<OrderTemplate {self.template_name}>'
    
class MaterialUsage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    material_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.String(50), nullable=False)

    def __repr__(self):
        return f"<MaterialUsage {self.material_name}>"

class ProductMaterial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quantity = db.Column(db.String(50), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('material.id'), nullable=False)
    
    material = db.relationship('Material', backref='product_links')
    
    def __repr__(self):
        return f'<ProductMaterial link: product {self.product_id} to material {self.material_id}>'

class SubiektProductCache(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(50), unique=True)
    name = db.Column(db.String(200), nullable=False)
    is_mapped = db.Column(db.Boolean, default=False)