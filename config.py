import os
from dotenv import load_dotenv

load_dotenv()

# Variável de módulo global
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class Config:
    BASE_DIR = BASE_DIR
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
    TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
    CANAL_STORAGE_ID = int(os.getenv("CANAL_STORAGE_ID", "0"))
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
    
    TMDB_API_KEY = os.getenv("TMDB_API_KEY")
    JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://127.0.0.1:8096")
    JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY")
    
    IP_SERVIDOR = os.getenv("IP_SERVIDOR", "127.0.0.1")
    PORTA_STREAM_LOCAL = int(os.getenv("PORTA_STREAM_LOCAL", "8081"))
    PASTA_MEDIA = os.getenv("PASTA_MEDIA", "/DATA/Media")