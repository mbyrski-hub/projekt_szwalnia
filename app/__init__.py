from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from .config import Config
import os
import re
from markupsafe import Markup, escape
from datetime import datetime
import pytz

app = Flask(__name__)
app.config.from_object(Config)

db = SQLAlchemy(app)
#csrf = CSRFProtect(app)
migrate = Migrate(app, db)  # <- inicjalizacja Flask-Migrate

# Upewnij się, że folder na uploady istnieje
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

_paragraph_re = re.compile(r'(?:\r\n|\r|\n){2,}')

@app.template_filter()
def nl2br(value):
    """
    Konwertuje znaki nowej linii w stringu na tagi <p> i <br> w HTML.
    """
    if value is None:
        return ""
    # Użycie escape do zabezpieczenia danych wejściowych
    escaped_value = escape(value)
    # Zamiana znaków nowej linii na <br> i opakowanie w <p>
    result = u'\n\n'.join(u'<p>%s</p>' % p.replace('\n', Markup('<br>\n')) for p in _paragraph_re.split(escaped_value))
    return Markup(result)

@app.template_filter()
def to_local_time(utc_dt):
    """Konwertuje datę z UTC na czas lokalny (dla Polski)."""
    if not utc_dt:
        return ""
    local_tz = pytz.timezone('Europe/Warsaw')
    local_dt = utc_dt.replace(tzinfo=pytz.utc).astimezone(local_tz)
    return local_dt.strftime('%Y-%m-%d %H:%M')


from app import routes, models
