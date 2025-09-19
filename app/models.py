from app import db
from datetime import datetime
from sqlalchemy.orm import validates

class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    orders = db.relationship('Order', backref='client', lazy=True)

    def __repr__(self):
        return f'<Client {self.name}>'

class OrderFabric(db.Model):
    __tablename__ = 'order_fabric'
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), primary_key=True)
    fabric_id = db.Column(db.Integer, db.ForeignKey('fabric.id'), primary_key=True)
    fabric = db.relationship('Fabric')

class Fabric(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    price = db.Column(db.Float, nullable=True)
    subiekt_symbol = db.Column(db.String(50), nullable=True, unique=True, index=True)
    
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
    
    fabrics = db.relationship('OrderFabric', backref='order', cascade="all, delete-orphan")

    assigned_team = db.Column(db.String(50), nullable=True)
    cutting_table = db.Column(db.String(50), nullable=True)
    team1_completed = db.Column(db.Boolean, default=False, nullable=False)
    team2_completed = db.Column(db.Boolean, default=False, nullable=False)
    
    # --- NOWE POLA DO ŚLEDZENIA CZASU PRODUKCJI ---
    cutting_started_at = db.Column(db.DateTime, nullable=True)
    cutting_finished_at = db.Column(db.DateTime, nullable=True)
    sewing_started_at = db.Column(db.DateTime, nullable=True)
    sewing_finished_at = db.Column(db.DateTime, nullable=True)
    # ---------------------------------------------
    
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

class ProductFabric(db.Model):
    __tablename__ = 'product_fabric'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    fabric_id = db.Column(db.Integer, db.ForeignKey('fabric.id'), nullable=False)
    usage_meters = db.Column(db.Float, nullable=False, default=0.0)
    fabric = db.relationship('Fabric')


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True) 
    
    production_price = db.Column(db.Float, nullable=False, default=0.0)
    image_id = db.Column(db.String(100), nullable=True) # dla google drive
    category_id = db.Column(db.Integer, db.ForeignKey('product_category.id'), nullable=True)
    category = db.relationship('ProductCategory', backref='products')
    
    fabrics_needed = db.relationship('ProductFabric', backref='product', lazy=True, cascade="all, delete-orphan")

    materials_needed = db.relationship('ProductMaterial', backref='product', lazy=True, cascade="all, delete-orphan")
    order_items = db.relationship('OrderItem', backref='product')

    @validates('production_price')
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

class TemplateFabric(db.Model):
    __tablename__ = 'template_fabric'
    template_id = db.Column(db.Integer, db.ForeignKey('order_template.id'), primary_key=True)
    fabric_id = db.Column(db.Integer, db.ForeignKey('fabric.id'), primary_key=True)
    fabric = db.relationship('Fabric')


class OrderTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    template_name = db.Column(db.String(100), nullable=False, unique=True)
    client_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    login_info = db.Column(db.Text, nullable=True)
    
    fabrics = db.relationship('TemplateFabric', cascade="all, delete-orphan")

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

class SystemInfo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(200))

    def __repr__(self):
        return f'<SystemInfo {self.key}={self.value}>'