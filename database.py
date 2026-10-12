import sqlite3
import os
from config import Config
from logger import logger

DB_PATH = os.path.join(Config.BASE_DIR, "jellybot.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS midias (
                media_id TEXT PRIMARY KEY,
                tmdb_id TEXT,
                tipo TEXT,
                titulo TEXT,
                ano TEXT,
                qualidade TEXT,
                msg_id_telegram TEXT,
                caminho_strm TEXT,
                status TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indexadores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT UNIQUE,
                url TEXT,
                ativo INTEGER DEFAULT 1
            )
        """)
        
        # Inserir indexadores padrão se a tabela estiver vazia
        cursor.execute("SELECT COUNT(*) FROM indexadores")
        if cursor.fetchone()[0] == 0:
            indexadores_padrao = [
                ("SuperFlix / Torrentio", "https://torrentio.strem.fun"),
                ("FrostStream", "https://froststream.fun")
            ]
            cursor.executemany("INSERT INTO indexadores (nome, url) VALUES (?, ?)", indexadores_padrao)
            
        conn.commit()
    logger.info("💾 Base de dados SQLite inicializada.")

def salvar_midia(media_id, tmdb_id, tipo, titulo, ano, qualidade, msg_id_telegram, caminho_strm, status='COMPLETED'):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO midias (media_id, tmdb_id, tipo, titulo, ano, qualidade, msg_id_telegram, caminho_strm, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (media_id, str(tmdb_id), tipo, titulo, ano, qualidade, str(msg_id_telegram), caminho_strm, status))
        conn.commit()

def obter_midia(media_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM midias WHERE media_id = ?", (media_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def listar_midias(tipo=None):
    with get_connection() as conn:
        cursor = conn.cursor()
        if tipo:
            cursor.execute("SELECT * FROM midias WHERE tipo = ? ORDER BY criado_em DESC", (tipo,))
        else:
            cursor.execute("SELECT * FROM midias ORDER BY criado_em DESC")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

def remover_midia(media_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM midias WHERE media_id = ?", (media_id,))
        conn.commit()

def carregar_indexadores():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT nome, url FROM indexadores WHERE ativo = 1")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]