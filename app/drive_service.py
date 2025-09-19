from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
import os

# Sprawdzamy, czy aplikacja działa na PythonAnywhere
IS_SERVER = 'PYTHONANYWHERE_DOMAIN' in os.environ

# Ustawiamy ścieżki w zależności od środowiska
if IS_SERVER:
    BASE_DIR = '/home/szwalnia/projekt_szwalnia'
    CLIENT_SECRETS_PATH = os.path.join(BASE_DIR, 'client_secrets.json')
    CREDENTIALS_PATH = os.path.join(BASE_DIR, 'credentials.json')
else:
    CLIENT_SECRETS_PATH = 'client_secrets.json'
    CREDENTIALS_PATH = 'credentials.json'


def get_drive_service():
    gauth = GoogleAuth()
    
    # Próba wczytania istniejących danych logowania
    gauth.LoadCredentialsFile(CREDENTIALS_PATH)

    if gauth.credentials is None:
        # Ten fragment zadziała tylko LOKALNIE
        
        # ### POCZĄTEK POPRAWKI ###
        # Ustawiamy parametry przepływu OAuth, aby poprosić o refresh_token
        gauth.GetFlow()
        gauth.flow.params['access_type'] = 'offline'
        gauth.flow.params['prompt'] = 'consent'
        # ### KONIEC POPRAWKI ###

        gauth.LocalWebserverAuth() # Uruchamiamy autoryzację bez dodatkowych argumentów

    elif gauth.access_token_expired:
        gauth.Refresh()
    else:
        gauth.Authorize()
    
    gauth.SaveCredentialsFile(CREDENTIALS_PATH)
    
    drive = GoogleDrive(gauth)
    return drive

# Funkcja do wysyłania plików pozostaje bez zmian
def upload_image_to_drive(file_storage):
    drive = get_drive_service()
    # Pamiętaj, aby wkleić tutaj swoje ID folderu z Google Drive
    folder_id = '1ySRL1RdWK2i3fdHQGuWcxbXoNQO7dhCP' 
    
    file_title = file_storage.filename
    drive_file = drive.CreateFile({
        'title': file_title,
        'parents': [{'id': folder_id}],
        'mimeType': file_storage.mimetype
    })
    
    drive_file.content = file_storage

    drive_file.Upload()
    
    drive_file.InsertPermission({
        'type': 'anyone',
        'value': 'anyone',
        'role': 'reader'
    })
    
    return drive_file['id']