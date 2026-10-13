import os
import re
import time
import math
import asyncio
import requests
from telethon import helpers
from telethon.tl.functions.upload import SaveBigFilePartRequest
from telethon.tl.types import InputFileBig

from config import Config
from logger import logger
from database import salvar_midia, carregar_indexadores
from server import client

# Limite seguro para partes individuais no Telegram (1.8 GB)
LIMITE_PARTE_BYTES = int(1.8 * 1024 * 1024 * 1024)

VIDEO_MAGIC_BYTES = [
    b'\x00\x00\x00\x18ftyp', b'\x00\x00\x00\x20ftyp', b'\x00\x00\x00\x1cftyp',
    b'\x1a\x45\xdf\xa3', b'RIFF'
]

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive"
}

def extrair_info_stream(s: dict) -> str:
    """Extrai qualidade, idioma (Dublado/Legendado) e servidor do stream de forma detalhada."""
    title = str(s.get("title", ""))
    name = str(s.get("name", ""))
    desc = str(s.get("description", ""))
    
    texto_full = f"{title} {name} {desc}".lower()

    # Deteção de idioma / áudio
    is_dub = any(termo in texto_full for termo in [
        "dub", "dublado", "dual", "pt-br", "ptbr", "br", "português", "portugues", "nacional", "multi"
    ])
    
    if "legendado" in texto_full or "subtitled" in texto_full:
        if not ("dub" in texto_full or "dual" in texto_full):
            is_dub = False

    audio = "DUBLADO 🇧🇷" if is_dub else "LEGENDADO 🔤"

    # Deteção de resolução
    if "4k" in texto_full or "2160p" in texto_full:
        qualidade = "4K 🌟"
    elif "1080p" in texto_full or "fhd" in texto_full:
        qualidade = "1080p FHD 🎬"
    elif "720p" in texto_full or "hd" in texto_full:
        qualidade = "720p HD ⚡"
    else:
        qualidade = "1080p FHD 🎬"

    # Deteção de servidor fonte (Astra, Neon, Carbon, Azure, etc.)
    servidor = ""
    for srv in ["astra", "neon", "carbon", "azure", "fast", "s3", "torrentio"]:
        if srv in texto_full:
            servidor = f" [{srv.capitalize()}]"
            break

    return f"{qualidade} ({audio}){servidor}"

def limpar_nome(nome: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", str(nome)).strip()

def resolver_url_direta(url_original: str) -> str:
    if "urltech.gy" in url_original:
        match = re.search(r'/(\d+\.mp4)', url_original)
        if match:
            id_mp4 = match.group(1)
            url_s3 = f"http://89.222.120.130:34871/s3v3/0-uncategorized/{id_mp4}"
            logger.info(f"⚡ Bypass imediato aplicado: {url_original} -> {url_s3}")
            return url_s3

    if "89.222.120.130" in url_original:
        return url_original

    try:
        session = requests.Session()
        session.headers.update(HEADERS_BROWSER)
        with session.get(url_original, allow_redirects=True, stream=True, timeout=8) as r:
            if r.status_code == 200:
                return r.url
    except Exception as e:
        logger.warning(f"⚠️ Não foi possível resolver URL original ({e}). Usando link padrão.")

    return url_original

def buscar_fontes_indexadores(tipo: str, imdb_id: str, season: int = None, episode: int = None):
    indexadores = carregar_indexadores()
    resultados = []

    for idx in indexadores:
        url = (
            f"{idx['url']}/stream/movie/{imdb_id}.json"
            if tipo == "movie"
            else f"{idx['url']}/stream/series/{imdb_id}:{season}:{episode}.json"
        )
        try:
            res = requests.get(url, headers=HEADERS_BROWSER, timeout=10)
            if res.status_code == 200:
                streams = res.json().get("streams", [])
                for s in streams:
                    if "url" in s and s["url"].startswith("http"):
                        url_limpa = resolver_url_direta(s["url"])
                        qualidade_info = extrair_info_stream(s)
                        resultados.append({
                            "indexador": idx["nome"],
                            "qualidade": qualidade_info,
                            "url": url_limpa
                        })
        except Exception as e:
            logger.warning(f"⚠️ Falha ao consultar indexador {idx['nome']}: {e}")

    return resultados

def atualizar_jellyfin():
    if not Config.JELLYFIN_API_KEY:
        return False
    try:
        url = f"{Config.JELLYFIN_URL}/Library/Refresh?api_key={Config.JELLYFIN_API_KEY}"
        res = requests.post(url, timeout=5)
        return res.status_code in [200, 204]
    except Exception as e:
        logger.error(f"❌ Erro ao atualizar biblioteca do Jellyfin: {e}")
        return False

def dividir_arquivo_binario(caminho_arquivo: str, tamanho_max_bytes: int = LIMITE_PARTE_BYTES) -> list:
    tamanho_total = os.path.getsize(caminho_arquivo)
    if tamanho_total <= tamanho_max_bytes:
        return [caminho_arquivo]

    logger.info(f"✂️ Ficheiro bruto de {tamanho_total / (1024**3):.2f} GB. A aplicar divisão binária em blocos de 1.8 GB...")
    partes_criadas = []
    base_dir, file_name = os.path.split(caminho_arquivo)
    name, ext = os.path.splitext(file_name)

    parte_idx = 1
    with open(caminho_arquivo, 'rb') as f_in:
        while True:
            chunk_data = f_in.read(tamanho_max_bytes)
            if not chunk_data:
                break
            caminho_parte = os.path.join(base_dir, f"{name}.part{parte_idx}{ext}")
            with open(caminho_parte, 'wb') as f_out:
                f_out.write(chunk_data)
            partes_criadas.append(caminho_parte)
            logger.info(f"🧩 Bloco binário {parte_idx} criado com sucesso ({len(chunk_data) / (1024**2):.1f} MB)")
            parte_idx += 1

    return partes_criadas

async def upload_paralelo_telegram(caminho_arquivo: str, progress_callback=None, max_workers: int = 8):
    try:
        tamanho_total = os.path.getsize(caminho_arquivo)
        tamanho_parte = 512 * 1024
        total_partes = math.ceil(tamanho_total / tamanho_parte)
        file_id = helpers.generate_random_long()

        fila_partes = asyncio.Queue()
        for i in range(total_partes):
            await fila_partes.put(i)

        bytes_enviados = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal bytes_enviados
            with open(caminho_arquivo, 'rb') as f:
                while not fila_partes.empty():
                    try:
                        idx_parte = fila_partes.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    f.seek(idx_parte * tamanho_parte)
                    chunk = f.read(tamanho_parte)

                    await client(SaveBigFilePartRequest(
                        file_id=file_id,
                        file_part=idx_parte,
                        file_total_parts=total_partes,
                        bytes=chunk
                    ))

                    async with lock:
                        bytes_enviados += len(chunk)
                        if progress_callback:
                            progress_callback(bytes_enviados, tamanho_total)

                    fila_partes.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(max_workers)]
        await asyncio.gather(*workers)

        return InputFileBig(id=file_id, parts=total_partes, name=os.path.basename(caminho_arquivo))
    except Exception as e:
        logger.warning(f"⚠️ Envio paralelo alternado para padrão: {e}")
        return await client.upload_file(caminho_arquivo, progress_callback=progress_callback, part_size_kb=512)

async def processar_e_hospedar_midia(url_fonte: str, tipo: str, tmdb_info: dict, qualidade: str, main_loop, progress_callback=None):
    if tipo == "movie":
        nome_limpo = limpar_nome(tmdb_info.get("title", "Filme"))
        ano = str(tmdb_info.get("release_date", "0000"))[:4]
        nome_slug = re.sub(r'\W+', '_', nome_limpo).lower()
        media_id = f"movie_{tmdb_info.get('id')}_{nome_slug}_{ano}"
        nome_base = f"{nome_limpo} ({ano})"
        pasta_dest = os.path.join(Config.PASTA_MEDIA, "FILMES", nome_base)
    else:
        nome_limpo = limpar_nome(tmdb_info.get("name", "Serie"))
        season_num = int(tmdb_info.get("season_number", 1))
        ep_num = int(tmdb_info.get("episode_number", 1))
        media_id = f"tv_{tmdb_info.get('id')}_s{season_num:02d}e{ep_num:02d}"
        nome_base = f"{nome_limpo} S{season_num:02d}E{ep_num:02d}"
        pasta_dest = os.path.join(Config.PASTA_MEDIA, "SERIES", nome_limpo, f"Season {season_num:02d}")

    os.makedirs(pasta_dest, exist_ok=True)
    caminho_temp = os.path.join(pasta_dest, f"{nome_base}_temp.mp4")
    partes_divididas = []

    try:
        url_final = resolver_url_direta(url_fonte)

        logger.info(f"⏳ A iniciar download temporário: {nome_base}")
        if progress_callback: progress_callback("⏳ **A verificar disponibilidade da mídia...**")

        with requests.get(url_final, headers=HEADERS_BROWSER, stream=True, timeout=30, allow_redirects=True) as r:
            r.raise_for_status()
            content_type = r.headers.get('Content-Type', '').lower()

            if 'html' in content_type or 'text' in content_type:
                raise ValueError(f"URL retornou conteúdo inválido ({content_type}) em vez de vídeo.")

            total_length = r.headers.get('content-length')
            total_bytes = int(total_length) if total_length and total_length.isdigit() else 0

            downloaded_bytes = 0
            dl_start_time = time.time()
            last_dl_log_time = dl_start_time
            last_dl_tg_time = dl_start_time

            with open(caminho_temp, 'wb') as f:
                header_bytes = b""
                for chunk in r.iter_content(chunk_size=2 * 1024 * 1024):
                    if chunk:
                        if len(header_bytes) < 64:
                            header_bytes += chunk[:64]
                            is_valid_video = any(magic in header_bytes for magic in VIDEO_MAGIC_BYTES)
                            if not is_valid_video and len(header_bytes) >= 32:
                                raise ValueError("Ficheiro descarregado não possui formato de vídeo válido.")

                        downloaded_bytes += len(chunk)
                        f.write(chunk)

                        now = time.time()
                        if now - last_dl_log_time >= 2.0 or (total_bytes > 0 and downloaded_bytes == total_bytes):
                            elapsed = now - dl_start_time
                            speed = (downloaded_bytes / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                            dl_mb = downloaded_bytes / (1024 * 1024)

                            if total_bytes > 0:
                                total_mb = total_bytes / (1024 * 1024)
                                pct = (downloaded_bytes / total_bytes) * 100
                                logger.info(f"[{nome_base}] ⬇️ Download: {dl_mb:.1f}/{total_mb:.1f} MB ({pct:.1f}%) - {speed:.2f} MB/s")

                                if progress_callback and (now - last_dl_tg_time >= 5.0):
                                    progress_callback(f"⏳ **A transferir mídia para o servidor...**\n📊 `{pct:.1f}%` ({dl_mb:.1f}/{total_mb:.1f} MB) à `{speed:.1f} MB/s`")
                                    last_dl_tg_time = now
                            else:
                                logger.info(f"[{nome_base}] ⬇️ Download: {dl_mb:.1f} MB baixados - {speed:.2f} MB/s")

                            last_dl_log_time = now

        if not os.path.exists(caminho_temp) or os.path.getsize(caminho_temp) < 3000000:
            raise ValueError("O ficheiro baixado é demasiado pequeno ou ficou incompleto.")

        partes_divididas = dividir_arquivo_binario(caminho_temp, LIMITE_PARTE_BYTES)

        logger.info(f"🚀 A iniciar upload acelerado ({len(partes_divididas)} bloco(s)) para o Telegram: {nome_base}")
        if progress_callback: progress_callback(f"🚀 **A guardar no Telegram Storage ({len(partes_divididas)} bloco(s))...**")

        titulo = tmdb_info.get('title') or tmdb_info.get('name') or nome_base
        data_rel = tmdb_info.get('release_date') or tmdb_info.get('first_air_date') or 'N/A'
        ano = str(data_rel)[:4]
        nota = tmdb_info.get('vote_average', 'N/A')
        sinopse = tmdb_info.get('overview', 'Sem sinopse disponível.')
        sinopse_corta = sinopse[:220] + "..." if len(sinopse) > 220 else sinopse

        msg_ids_salvos = []

        for idx_p, arq_parte in enumerate(partes_divididas):
            tag_parte = f" (Bloco {idx_p+1}/{len(partes_divididas)})" if len(partes_divididas) > 1 else ""
            legenda = (
                f"🎬 **{titulo}**{tag_parte}\n"
                f"📅 **Ano:** {ano} | 🌟 **Nota:** {nota}/10\n"
                f"🎧 **Qualidade:** {qualidade}\n\n"
                f"📝 **Sinopse:** {sinopse_corta}\n\n"
                f"📌 _Ficheiro hospedado permanentemente para o Jellyfin._"
            )

            last_up_log_time = [time.time()]
            last_up_tg_time = [time.time()]
            up_start_time = time.time()

            def upload_progress(current, total):
                now = time.time()
                if now - last_up_log_time[0] >= 2.0 or current == total:
                    elapsed = now - up_start_time
                    speed = (current / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                    up_mb = current / (1024 * 1024)
                    total_mb = total / (1024 * 1024)
                    pct = (current / total) * 100 if total > 0 else 0

                    logger.info(f"[{nome_base}{tag_parte}] ⚡ Upload Paralelo: {up_mb:.1f}/{total_mb:.1f} MB ({pct:.1f}%) - {speed:.2f} MB/s")
                    last_up_log_time[0] = now

                    if progress_callback and (now - last_up_tg_time[0] >= 5.0 or current == total):
                        progress_callback(f"🚀 **A guardar no Telegram Storage{tag_parte}...**\n📊 `{pct:.1f}%` ({up_mb:.1f}/{total_mb:.1f} MB) à `{speed:.1f} MB/s`")
                        last_up_tg_time[0] = now

            async def upload_task(caminho):
                handle_arquivo = await upload_paralelo_telegram(
                    caminho,
                    progress_callback=upload_progress,
                    max_workers=8
                )
                msg = await client.send_file(
                    Config.CANAL_STORAGE_ID,
                    handle_arquivo,
                    caption=legenda,
                    parse_mode='md'
                )
                return msg.id

            future = asyncio.run_coroutine_threadsafe(upload_task(arq_parte), main_loop)
            msg_ids_salvos.append(str(future.result()))

        str_msg_ids = ",".join(msg_ids_salvos)

        url_stream = f"http://{Config.IP_SERVIDOR}:{Config.PORTA_STREAM_LOCAL}/stream/{media_id}"
        caminho_strm = os.path.join(pasta_dest, f"{nome_base}.strm")
        with open(caminho_strm, "w", encoding="utf-8") as f:
            f.write(url_stream + "\n")

        salvar_midia(
            media_id=media_id,
            tmdb_id=str(tmdb_info.get('id', '0')),
            tipo=tipo,
            titulo=titulo,
            ano=ano,
            qualidade=qualidade,
            msg_id_telegram=str_msg_ids,
            caminho_strm=caminho_strm,
            status='COMPLETED'
        )

        atualizar_jellyfin()
        logger.info(f"🎉 Mídia {nome_base} hospedada ({len(msg_ids_salvos)} bloco(s)) e indexada com sucesso!")
        return True, nome_base, caminho_strm

    except Exception as e:
        logger.error(f"❌ Falha ao processar mídia {nome_base}: {e}")
        return False, str(e), None

    finally:
        if os.path.exists(caminho_temp):
            try: os.remove(caminho_temp)
            except: pass
        for p in partes_divididas:
            if os.path.exists(p) and p != caminho_temp:
                try: os.remove(p)
                except: pass