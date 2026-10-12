import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    print("❌ Ficheiro .env não encontrado!")

class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "").strip()
    CANAL_STORAGE_ID = int(os.getenv("CANAL_STORAGE_ID", "0"))
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

    TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
    JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://localhost:8096").rstrip("/")
    JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "").strip()

    PORTA_STREAM_LOCAL = int(os.getenv("PORTA_STREAM_LOCAL", "8081"))
    IP_SERVIDOR = os.getenv("IP_SERVIDOR", "127.0.0.1").strip()
    PASTA_MEDIA = os.path.join(BASE_DIR, os.getenv("PASTA_MEDIA", "Media"))

    @classmethod
    def validar(cls):
        erros = []
        if not cls.TELEGRAM_TOKEN: erros.append("TELEGRAM_TOKEN ausente.")
        if cls.API_ID == 0 or not cls.API_HASH: erros.append("API_ID ou API_HASH ausentes.")
        if cls.CANAL_STORAGE_ID == 0: erros.append("CANAL_STORAGE_ID ausente.")
        if not cls.TMDB_API_KEY: erros.append("TMDB_API_KEY ausente.")

        if erros:
            print("❌ Erros de configuração:")
            for e in erros: print(f"  - {e}")
            sys.exit(1)

Config.validar()