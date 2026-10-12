import os
from dotenv import load_dotenv

# Caminho absoluto da pasta do projeto
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Carrega estritamente o ficheiro .env localizado na pasta do projeto
load_dotenv(os.path.join(BASE_DIR, ".env"))

class Config:
    BASE_DIR = BASE_DIR
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    
    _api_id = os.getenv("TELEGRAM_API_ID")
    TELEGRAM_API_ID = int(_api_id) if _api_id and _api_id.strip().isdigit() else 0
    TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
    
    CANAL_STORAGE_ID = int(os.getenv("CANAL_STORAGE_ID") or 0)
    ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)
    
    TMDB_API_KEY = os.getenv("TMDB_API_KEY")
    JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://127.0.0.1:8096")
    JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY")
    
    IP_SERVIDOR = os.getenv("IP_SERVIDOR", "127.0.0.1")
    PORTA_STREAM_LOCAL = int(os.getenv("PORTA_STREAM_LOCAL") or 8081)
    PASTA_MEDIA = os.getenv("PASTA_MEDIA", "/DATA/Media")