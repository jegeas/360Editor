"""Rutas del editor, tanto ejecutado desde el código como instalado (.exe).

- Desde el código (python server.py): todo vive en la carpeta del proyecto, como siempre.
- Instalado: el programa está en «Archivos de programa» (solo lectura), así que las claves
  (config.json), el modelo local y el registro se guardan en %APPDATA%\\Editor 360,
  una carpeta propia de cada usuario de Windows.
"""

import os
import sys
from pathlib import Path

APP_NAME = "Editor 360"
VERSION = "1.1.1"

FROZEN = getattr(sys, "frozen", False)          # True dentro del ejecutable de PyInstaller

# Archivos que acompañan al programa (index.html)
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

if FROZEN:
    DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
else:
    DATA_DIR = Path(__file__).resolve().parent
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"
MODELS_DIR = DATA_DIR / "modelos"
LOG_FILE = DATA_DIR / "editor.log"
