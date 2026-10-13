import sqlite3
import os
import json
from config import Config
from logger import logger

BASE_DIR = getattr(Config, 'BASE_DIR', os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "jellybot.db")

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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fila (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id TEXT UNIQUE,
                tipo TEXT,
                tmdb_id TEXT,
                titulo TEXT,
                ano TEXT,
                qualidade TEXT,
                url_fonte TEXT,
                tmdb_json TEXT,
                status TEXT DEFAULT 'PENDING',
                progresso_texto TEXT DEFAULT 'Aguardando na fila...',
                erro_msg TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("SELECT COUNT(*) FROM indexadores")
        if cursor.fetchone()[0] == 0:
            indexadores_padrao = [
                ("SuperFlix / Torrentio", "https://torrentio.strem.fun"),
                ("FrostStream", "https://froststream.fun")
            ]
            cursor.executemany("INSERT INTO indexadores (nome, url) VALUES (?, ?)", indexadores_padrao)
            
        conn.commit()
    logger.info("💾 Base de dados SQLite e Tabela de Fila inicializadas.")

# --- MÍDIAS ---
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
        cursor.execute("DELETE FROM fila WHERE media_id = ?", (media_id,))
        conn.commit()

# --- FILA DE ESPERA ---
def adicionar_a_fila(media_id, tipo, tmdb_id, titulo, ano, qualidade, url_fonte, tmdb_info):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO fila (media_id, tipo, tmdb_id, titulo, ano, qualidade, url_fonte, tmdb_json, status, progresso_texto, erro_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 'Aguardando na fila...', NULL)
        """, (media_id, tipo, str(tmdb_id), titulo, str(ano), qualidade, url_fonte, json.dumps(tmdb_info)))
        conn.commit()

def obter_proximo_fila():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM fila WHERE status = 'PENDING' ORDER BY id ASC LIMIT 1")
        row = cursor.fetchone()
        if row:
            d = dict(row)
            d['tmdb_info'] = json.loads(d['tmdb_json']) if d.get('tmdb_json') else {}
            return d
        return None

def atualizar_status_fila(item_id, status, progresso_texto=None, erro_msg=None):
    with get_connection() as conn:
        cursor = conn.cursor()
        if progresso_texto and erro_msg:
            cursor.execute("UPDATE fila SET status = ?, progresso_texto = ?, erro_msg = ? WHERE id = ?", (status, progresso_texto, erro_msg, item_id))
        elif progresso_texto:
            cursor.execute("UPDATE fila SET status = ?, progresso_texto = ? WHERE id = ?", (status, progresso_texto, item_id))
        elif erro_msg:
            cursor.execute("UPDATE fila SET status = ?, erro_msg = ? WHERE id = ?", (status, erro_msg, item_id))
        else:
            cursor.execute("UPDATE fila SET status = ? WHERE id = ?", (status, item_id))
        conn.commit()

def listar_fila():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM fila ORDER BY id DESC")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

def remover_item_fila(item_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM fila WHERE id = ?", (item_id,))
        conn.commit()

def resetar_item_fila(item_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE fila SET status = 'PENDING', progresso_texto = 'Aguardando na fila...', erro_msg = NULL WHERE id = ?", (item_id,))
        conn.commit()

# --- INDEXADORES ---
def carregar_indexadores():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT nome, url FROM indexadores WHERE ativo = 1")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

def listar_todos_indexadores():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM indexadores")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

def alternar_status_indexador(idx_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE indexadores SET ativo = CASE WHEN ativo = 1 THEN 0 ELSE 1 END WHERE id = ?", (idx_id,))
        conn.commit()