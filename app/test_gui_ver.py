import PySimpleGUI as sg
import sys

try:
    print(f"Wersja PySimpleGUI: {sg.ver}")
    print(f"Lokalizacja pliku biblioteki: {sg.sys.modules['PySimpleGUI'].__file__}")
    print(f"\nWersja Pythona: {sys.version}")

    # Sprawdzamy, czy funkcja .theme() istnieje
    if hasattr(sg, 'theme'):
        print("\nFunkcja sg.theme() JEST DOSTĘPNA. :)")
    else:
        print("\nBŁĄD: Funkcja sg.theme() NIE JEST DOSTĘPNA. :(")

except Exception as e:
    print(f"Wystąpił nieoczekiwany błąd: {e}")

input("\nNaciśnij Enter, aby zakończyć...")