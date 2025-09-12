from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, DateField, SelectField, FieldList, FormField, SubmitField, BooleanField, IntegerField, FloatField
from wtforms.validators import DataRequired, NumberRange, Optional
from wtforms import Form



# Formularz dla pojedynczego wariantu produktu (rozmiar + ilość)
class ProductVariantForm(Form):
    size = StringField('Rozmiar', validators=[DataRequired()])
    quantity = IntegerField('Ilość', validators=[DataRequired()])

# Formularz dla jednego produktu w zleceniu – nazwa produktu i lista wariantów
class OrderProductForm(Form):
    product_name = StringField('Nazwa produktu', validators=[DataRequired()])
    variants = FieldList(FormField(ProductVariantForm), min_entries=1, max_entries=10)

# Główny formularz zlecenia – lista produktów (każdy z wariantami)
class OrderForm(FlaskForm):
    client_name = StringField('Nazwa klienta', validators=[DataRequired()])
    description = TextAreaField('Opis zlecenia', validators=[DataRequired()])
    fabric_id = SelectField('Tkanina', coerce=int, validators=[DataRequired()])
    login_info = TextAreaField('Logowanie (opcjonalne)')
    deadline = DateField('Termin realizacji (RRRR-MM-DD)', format='%Y-%m-%d', validators=[DataRequired()])
    products = FieldList(FormField(OrderProductForm), min_entries=1, max_entries=10)
    zlecajacy = SelectField('Zlecający', choices=[
        ('SZEF', 'SZEF'), ('JOLA', 'JOLA'), ('ANIA', 'ANIA'),
        ('WOJTEK', 'WOJTEK'), ('MATEUSZ', 'MATEUSZ'), ('KINGA', 'KINGA'), ('FIRMA', 'FIRMA'), ('MAKS', 'MAKS')
    ], validators=[DataRequired()])
    save_template = BooleanField('Zapisz jako szablon')
    template_name = StringField('Nazwa szablonu (jeśli zapisujesz)')
    submit = SubmitField('Dodaj zlecenie')


# Formularz do dodawania/edycji szablonu
class OrderTemplateForm(FlaskForm):
    template_name = StringField('Nazwa szablonu', validators=[DataRequired()])
    client_name = StringField('Nazwa klienta', validators=[DataRequired()])
    description = TextAreaField('Opis zlecenia', validators=[DataRequired()])
    
    # --- POCZĄTEK POPRAWKI ---
    # Upewnij się, że te dwa pola istnieją w tej klasie
    fabric_id = SelectField('Tkanina (opcjonalnie)', coerce=int)
    login_info = TextAreaField('Logowanie (opcjonalne)')
    # --- KONIEC POPRAWKI ---
    
    submit = SubmitField('Zapisz szablon')


# Formularz dla pojedynczego materiału w produkcie
class ProductMaterialForm(Form):
    material_name = StringField('Materiał', validators=[DataRequired()])
    quantity = StringField('Ilość', validators=[DataRequired()])

# NOWY FORMULARZ DLA KATEGORII
class ProductCategoryForm(FlaskForm):
    name = StringField('Nazwa kategorii', validators=[DataRequired()])
    submit = SubmitField('Zapisz')

class ProductForm(FlaskForm):
    name = StringField('Nazwa produktu', validators=[DataRequired()])
    description = TextAreaField('Opis produktu (opcjonalnie)')
    
    # NOWE POLE WYBORU KATEGORII
    category_id = SelectField('Kategoria', coerce=int, validators=[Optional()])
    production_price = FloatField('Cena Produkcji (np. robocizna)', validators=[DataRequired(), NumberRange(min=0)])
    fabric_usage_meters = FloatField('Zużycie tkaniny (w metrach)', validators=[DataRequired(), NumberRange(min=0)])
    materials_needed = FieldList(FormField(ProductMaterialForm), min_entries=0)
    submit = SubmitField('Zapisz produkt')

class FabricForm(FlaskForm):
    name = StringField('Nazwa tkaniny', validators=[DataRequired()])
    price = FloatField('Cena netto (opcjonalnie)', validators=[Optional()])
    submit = SubmitField('Zapisz')

class MaterialForm(FlaskForm):
    name = StringField('Nazwa materiału', validators=[DataRequired()])
    price = FloatField('Cena netto (opcjonalnie)', validators=[Optional()])
    submit = SubmitField('Zapisz')

    # Zaawansowany, uniwersalny formularz do EDYCJI
class MaterialEditForm(FlaskForm):
    name = StringField('Nazwa', validators=[DataRequired()])
    subiekt_symbol = StringField('Symbol Subiekt (opcjonalnie)')
    price = FloatField('Cena netto (opcjonalnie)', validators=[Optional()])
    material_type = SelectField('Typ', choices=[
        ('fabric', 'Tkanina'),
        ('material', 'Materiał Dodatkowy')
    ], validators=[DataRequired()])
    submit = SubmitField('Zapisz zmiany')