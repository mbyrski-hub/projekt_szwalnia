import sys
import os

# --- POCZĄTEK POPRAWKI ---
# Dodaj ścieżkę do głównego folderu projektu, aby Python mógł znaleźć pakiet 'app'
project_path = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_path)
# --- KONIEC POPRAWKI ---

from app import app, db
from app.models import Product, ProductImage, AiImageTask
from app.ai_image_service import generate_ai_images
from app.drive_service import upload_image_to_drive
from werkzeug.datastructures import FileStorage
import io

def process_pending_tasks():
    """
    Przetwarza zadania generowania obrazów AI ze statusu 'pending'.
    """
    with app.app_context():
        task = AiImageTask.query.filter_by(status='pending').first()
        
        if not task:
            print("Brak zadań AI do wykonania.")
            return

        print(f"Rozpoczynam przetwarzanie zadania ID: {task.id} dla produktu ID: {task.product_id}")
        task.status = 'processing'
        db.session.commit()

        try:
            product = Product.query.get(task.product_id)
            if not product:
                raise ValueError("Nie znaleziono produktu.")

            base_image_url = f"https://drive.google.com/thumbnail?id={task.original_image_id}&sz=w1024"
            product_description = product.name.replace('_', ' ')
            
            prompts = {
                'catalog_ai': (
                    f"Analyze the provided image of a piece of clothing. Recreate it as a professional, photorealistic e-commerce catalog photo. "
                    f"The garment is: '{product_description}'. "
                    f"Place the *exact same garment* on a clean, uniform, light grey background (#f2f2f2). "
                    f"It must be perfectly ironed and laid flat. The lighting should be soft and professional. "
                    f"Do not change the design, color, or texture of the clothing from the original image."
                ),
                'model_ai': (
                    f"Analyze the provided image of the garment: '{product_description}'. "
                    f"Create a new, full-body, photorealistic image of a male model wearing this *exact* piece of clothing. "
                    f"The model is standing in a natural, confident pose inside a bright, modern warehouse with a blurred background. "
                    f"Ensure the clothing's color, design, and details from the original image are accurately represented on the model."
                )
            }

            generated_images = generate_ai_images(base_image_url, prompts)

            for image_type, image_bytes in generated_images.items():
                image_file = FileStorage(
                    stream=io.BytesIO(image_bytes),
                    filename=f"{product.name.lower()}_{image_type}.png",
                    content_type='image/png'
                )
                
                ai_drive_id = upload_image_to_drive(image_file)
                if ai_drive_id:
                    ai_image_record = ProductImage(
                        image_id=ai_drive_id,
                        product_id=product.id,
                        image_type=image_type
                    )
                    db.session.add(ai_image_record)
            
            task.status = 'complete'
            db.session.commit()
            print(f"Pomyślnie zakończono zadanie ID: {task.id}")

        except Exception as e:
            print(f"Błąd podczas przetwarzania zadania ID: {task.id}. Szczegóły: {e}")
            task.status = 'error'
            db.session.commit()

if __name__ == "__main__":
    process_pending_tasks()