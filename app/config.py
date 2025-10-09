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
    VAPID_PUBLIC_KEY = 'MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEdGzE+Uqz1ZgV2AAKqfaSzf3JKXS7Zp0usN8BD0cNqmPt21dN6Gfd898o0IEnUkNFmGlyu6svCPBMseHahPXEDg=='
    VAPID_PRIVATE_KEY = 'MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgtcRsMkpZmjcltWx+dBt1CYVoJyhFc6cWyheYYd4SpoOhRANCAAR0bMT5SrPVmBXYAAqp9pLN/ckpdLtmnS6w3wEPRw2qY+3bV03oZ93z3yjQgSdSQ0WYaXK7qy8I8Eyx4dqE9cQO'
    VAPID_CLAIMS = {
        "sub": "m.byrski@hoxa.pl" # Wpisz tutaj swój adres email
}