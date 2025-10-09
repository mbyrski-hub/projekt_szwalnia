# generate_keys.py
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

try:
    # 1. Wygeneruj klucz prywatny
    private_key = ec.generate_private_key(ec.SECP256R1())

    # 2. Wygeneruj klucz publiczny
    public_key = private_key.public_key()

    # 3. Zakoduj klucz prywatny do formatu DER, a następnie do Base64 URL-safe
    private_key_str = base64.urlsafe_b64encode(
        private_key.private_numbers().private_value.to_bytes(32, byteorder="big")
    ).rstrip(b'=').decode('utf-8')

    # 4. Zakoduj klucz publiczny do formatu X9.62 nieskompresowanego, a następnie do Base64 URL-safe
    public_key_str = base64.urlsafe_b64encode(
        public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        )
    ).rstrip(b'=').decode('utf-8')

    print("\n" + "="*60)
    print("UDAŁO SIĘ! Skopiuj poniższe dwie linie do swojego pliku config.py:")
    print("="*60 + "\n")
    print(f"VAPID_PRIVATE_KEY = '{private_key_str}'")
    print(f"VAPID_PUBLIC_KEY = '{public_key_str}'")
    print("\n" + "="*60 + "\n")

except Exception as e:
    print(f"\nWystąpił nieoczekiwany błąd: {e}")
    print("Upewnij się, że masz zainstalowaną bibliotekę 'cryptography'.")
    print("Możesz ją zainstalować komendą: pip install cryptography\n")