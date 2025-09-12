from app import app, db
from os import path

# Jeśli bazy nie ma, utwórz ją
db_path = path.join(path.abspath(path.dirname(__file__)), 'app.db')
if not path.exists(db_path):
    with app.app_context():
        db.create_all()
        print("Baza danych została utworzona.")

if __name__ == '__main__':
    app.run(debug=True)
