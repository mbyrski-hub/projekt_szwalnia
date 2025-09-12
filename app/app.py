from flask import Flask
from config import Config
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
import os

app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)
csrf = CSRFProtect(app)

# Upewnij się, że folder na uploady istnieje
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Import modeli oraz tras
from models import *
import routes

# Sprawdzenie czy baza danych istnieje; jeśli nie, tworzymy ją
db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'app.db')
if not os.path.exists(db_path):
    with app.app_context():
        db.create_all()
        print("Baza danych została utworzona.")

if __name__ == '__main__':
    app.run(debug=True)
