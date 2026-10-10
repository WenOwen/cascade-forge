from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_ROOT = PACKAGE_DIR / "templates"
STATIC_ROOT = PACKAGE_DIR / "static"
