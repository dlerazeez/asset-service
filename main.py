import os
from app.factory import create_app

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Render / uvicorn should still run with: uvicorn main:app
app = create_app(base_dir=BASE_DIR)
