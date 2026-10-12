import sqlite3
import json
import os
from pathlib import Path
from config import BASE_DIR
from logger import logger

DB_PATH = BASE_DIR / "jellybot.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Ativa modo WAL (Write-Ahead Logging) para alta concorrência e segurança contra corrupção
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Tabela principal de mapeamento de mídias e status
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS media_mapping (
                media_id TEXT PRIMARY KEY,
                tmdb_id TEXT,
                tipo TEXT,
                titulo TEXT,
                ano TEXT,
                qualidade TEXT,
                msg_id_telegram INTEGER,
                caminho_strm TEXT,
                status TEXT DEFAULT 'COMPLETED',
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Tabela para indexadores configurados
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indexadores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT UNIQUE,
                url TEXT,
                ativo INTEGER DEFAULT 1
            );
        """)
        
        # Inserir indexadores padrão se a tabela estiver vazia
        cursor.execute("SELECT COUNT(*) FROM indexadores;")
        if cursor.fetchone()[0] == 0:
            indexadores_padrao = [
                ("FrostStream", "https://froststream.cloutteam.com", 1),
                ("MagnetFlix", "https://magnetflix.magnetbr.online", 1),
                ("FenixFlix", "https://fenixflix.fenixhub.online", 1)
            ]
            cursor.executemany(
                "INSERT INTO indexadores (nome, url, ativo) VALUES (?, ?, ?);",
                indexadores_padrao
            )
            
        conn.commit()
    logger.info("💾 Banco de dados SQLite inicializado com sucesso (Modo WAL ativo).")

def salvar_midia(media_id: str, tmdb_id: str, tipo: str, titulo: str, ano: str, qualidade: str, msg_id_telegram: int, caminho_strm: str, status: str = 'COMPLETED'):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO media_mapping (media_id, tmdb_id, tipo, titulo, ano, qualidade, msg_id_telegram, caminho_strm, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_id) DO UPDATE SET
                msg_id_telegram=excluded.msg_id_telegram,
                caminho_strm=excluded.caminho_strm,
                status=excluded.status,
                qualidade=excluded.qualidade;
        """, (media_id, str(tmdb_id), tipo, titulo, str(ano), qualidade, msg_id_telegram, caminho_strm, status))
        conn.commit()

def obter_midia(media_id: str):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM media_mapping WHERE media_id = ?;", (media_id,)).fetchone()
        return dict(row) if row else None

def listar_midias(tipo: str = None):
    with get_connection() as conn:
        if tipo:
            rows = conn.execute("SELECT * FROM media_mapping WHERE tipo = ? ORDER BY criado_em DESC;", (tipo,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM media_mapping ORDER BY criado_em DESC;").fetchall()
        return [dict(r) for r in rows]

def remover_midia(media_id: str):
    with get_connection() as conn:
        conn.execute("DELETE FROM media_mapping WHERE media_id = ?;", (media_id,))
        conn.commit()

def carregar_indexadores():
    with get_connection() as conn:
        rows = conn.execute("SELECT nome, url, ativo FROM indexadores WHERE ativo = 1;").fetchall()
        return [dict(r) for r in rows]