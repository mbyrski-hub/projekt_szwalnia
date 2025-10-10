import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'tajny_klucz'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///' + os.path.join(BASE_DIR, '..', 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # maksymalnie 16 MB na upload
    # NOWY KLUCZ DO ZABEZPIECZENIA API
    API_SECRET_KEY = 'jYHGYSjdsaj86H28299..as1237f'
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY') or 'AIzaSyCEacw-zMUVSbNXVGk7CcEpF05k5zOlUAE'
    OPENWEATHERMAP_API_KEY = '90ad5d6d2177502de6fe53e1256c71bc'
    VAPID_PRIVATE_KEY = 'yP56qF2tkpGpk7bHY_Nt1I1DlFIQACWAbFZqnpOO0gE'
    VAPID_PUBLIC_KEY = 'BNUgQV6QrRrZsEaFE5HldFckXSHvDG7jNjBCT2nMc4KbwTeDaDR1R7ydx0USOquy2FTI6feUQbBWLpak4fXLpR8'

    VAPID_CLAIMS = {
        "sub": "mailto:m.byrski@hoxa.pl"

}