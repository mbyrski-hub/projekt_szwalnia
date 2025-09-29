import google.generativeai as genai
from PIL import Image
import io
import requests
from flask import current_app

def configure_ai():
    """Konfiguruje klienta AI."""
    api_key = current_app.config.get('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("Klucz API Gemini nie jest ustawiony!")
    genai.configure(api_key=api_key)

def generate_ai_images(original_image_url, prompts):
    """
    Generuje obrazy AI na podstawie jednego obrazu i listy promptów.
    
    Args:
        original_image_url (str): Link do oryginalnego zdjęcia na Dysku Google.
        prompts (dict): Słownik, np. {'catalog': "prompt...", 'model': "prompt..."}

    Returns:
        dict: Słownik z wygenerowanymi obrazami w formie bajtów, np. {'catalog': b'...', 'model': b'...'}
    """
    configure_ai()
    
    # --- Użycie poprawnej nazwy modelu "Nano Banana" ---
    model = genai.GenerativeModel('gemini-2.5-flash-image-preview')
    # ----------------------------------------------------

    try:
        # Pobranie obrazu z Dysku Google
        response = requests.get(original_image_url)
        response.raise_for_status()
        img = Image.open(io.BytesIO(response.content))
    except Exception as e:
        print(f"Błąd podczas pobierania obrazu z Google Drive: {e}")
        return {}

    generated_images = {}
    for key, prompt in prompts.items():
        try:
            print(f"Generowanie obrazu AI typu '{key}'...")
            ai_response = model.generate_content([prompt, img])
            
            if ai_response.parts and ai_response.parts[0].inline_data:
                image_bytes = ai_response.parts[0].inline_data.data
                generated_images[key] = image_bytes
                print(f"Pomyślnie wygenerowano obraz AI typu '{key}'.")
            else:
                 print(f"Ostrzeżenie: Odpowiedź AI dla '{key}' nie zawierała obrazu.")

        except Exception as e:
            print(f"Błąd podczas generowania obrazu AI dla klucza '{key}': {e}")

    return generated_images