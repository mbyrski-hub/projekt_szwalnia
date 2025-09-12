from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from .config import Config
import os

app = Flask(__name__)
app.config.from_object(Config)

db = SQLAlchemy(app)
#csrf = CSRFProtect(app)
migrate = Migrate(app, db)  # <- inicjalizacja Flask-Migrate

# Upewnij się, że folder na uploady istnieje
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Importujemy na końcu, aby upewnić się, że app, db itp. już istnieją
from app import routes, models
