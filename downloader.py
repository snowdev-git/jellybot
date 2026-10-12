import os
import re
import time
import asyncio
import requests
from config import Config
from logger import logger
from database import salvar_midia, carregar_indexadores
from server import client

# Limite estrito do Telegram para upload (2 GB em bytes)
LIMITE_TELEGRAM_BYTES = 2 * 1024 * 1024 * 1024  # 2.147.483.648 bytes (~2,00 GB)

# Assinaturas (Magic Bytes) válidas para vídeos
VIDEO_MAGIC_BYTES = [
    b'\x00\x00\x00\x18ftyp', b'\x00\x00\x00\x20ftyp', b'\x00\x00\x00\x1cftyp', # MP4 / MOV
    b'\x1a\x45\xdf\xa3',                                                       # MKV / WEBM
    b'RIFF'                                                                   # AVI
]

def extrair_qualidade(texto: str) -> str:
    txt = texto.lower()
    is_dub = any(termo in txt for termo in ["dub", "dublado", "dual", "pt-br", "ptbr", "br", "pt", "nacional", "multi"])
    audio = "DUBLADO 🇧🇷" if is_dub else "LEGENDADO 🔤"
    if "4k" in txt or "2160p" in txt: return f"4K 🌟 ({audio})"
    elif "1080p" in txt or "fhd" in txt: return f"1080p FHD 🎬 ({audio})"
    elif "720p" in txt or "hd" in txt: return f"720p HD ⚡ ({audio})"
    return f"SD 📺 ({audio})"

def limpar_nome(nome: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", str(nome)).strip()

def buscar_fontes_indexadores(tipo: str, imdb_id: str, season: int = None, episode: int = None):
    indexadores = carregar_indexadores()
    resultados = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    for idx in indexadores:
        url = f"{idx['url']}/stream/movie/{imdb_id}.json" if tipo == "movie" else f"{idx['url']}/stream/series/{imdb_id}:{season}:{episode}.json"
        try:
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 200:
                streams = res.json().get("streams", [])
                for s in streams:
                    if "url" in s and s["url"].startswith("http"):
                        txt = (s.get("title", "") + " " + s.get("name", ""))
                        resultados.append({
                            "indexador": idx["nome"],
                            "qualidade": extrair_qualidade(txt),
                            "url": s["url"]
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

    try:
        logger.info(f"⏳ A iniciar download temporário: {nome_base}")
        if progress_callback: progress_callback("⏳ **A verificar disponibilidade da mídia...**")

        headers = {"User-Agent": "Mozilla/5.0"}

        # 1. Download monitorado com verificação antecipada de limite (2 GB)
        with requests.get(url_fonte, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            content_type = r.headers.get('Content-Type', '').lower()

            if 'html' in content_type or 'text' in content_type:
                raise ValueError(f"URL retornou conteúdo inválido ({content_type}) em vez de vídeo.")

            total_length = r.headers.get('content-length')
            total_bytes = int(total_length) if total_length and total_length.isdigit() else 0

            # 🛑 CHECAGEM ANTECIPADA: Aborta antes de baixar se o tamanho for > 2 GB
            if total_bytes > LIMITE_TELEGRAM_BYTES:
                tamanho_gb = total_bytes / (1024**3)
                msg_limite = f"Esta mídia tem {tamanho_gb:.2f} GB e ultrapassa o limite máximo de 2.0 GB do Telegram."
                logger.warning(f"⚠️ [{nome_base}] {msg_limite}")
                raise ValueError(msg_limite)

            downloaded_bytes = 0
            dl_start_time = time.time()
            last_dl_log_time = dl_start_time
            last_dl_tg_time = dl_start_time

            with open(caminho_temp, 'wb') as f:
                header_bytes = b""
                for chunk in r.iter_content(chunk_size=1 * 1024 * 1024):
                    if chunk:
                        if len(header_bytes) < 64:
                            header_bytes += chunk[:64]
                            is_valid_video = any(magic in header_bytes for magic in VIDEO_MAGIC_BYTES)
                            if not is_valid_video and len(header_bytes) >= 32:
                                raise ValueError("Ficheiro descarregado não possui formato de vídeo válido.")

                        downloaded_bytes += len(chunk)

                        # 🛑 CHECAGEM DURANTE O DOWNLOAD: Aborta se passar de 2 GB em tempo real
                        if downloaded_bytes > LIMITE_TELEGRAM_BYTES:
                            msg_limite = "O download foi interrompido porque o arquivo ultrapassou o limite de 2.0 GB do Telegram."
                            logger.warning(f"⚠️ [{nome_base}] {msg_limite}")
                            raise ValueError(msg_limite)

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

        logger.info(f"☁️ A iniciar upload para o Telegram: {nome_base}")
        if progress_callback: progress_callback("☁️ **A iniciar upload para o Telegram Storage...**")

        # 2. Formatar legenda catalogada
        titulo = tmdb_info.get('title') or tmdb_info.get('name') or nome_base
        data_rel = tmdb_info.get('release_date') or tmdb_info.get('first_air_date') or 'N/A'
        ano = str(data_rel)[:4]
        nota = tmdb_info.get('vote_average', 'N/A')
        sinopse = tmdb_info.get('overview', 'Sem sinopse disponível.')
        sinopse_corta = sinopse[:220] + "..." if len(sinopse) > 220 else sinopse

        legenda = (
            f"🎬 **{titulo}**\n"
            f"📅 **Ano:** {ano} | 🌟 **Nota:** {nota}/10\n"
            f"🎧 **Qualidade:** {qualidade}\n\n"
            f"📝 **Sinopse:** {sinopse_corta}\n\n"
            f"📌 _Ficheiro hospedado permanentemente para o Jellyfin._"
        )

        # 3. Upload para o Telegram monitorado com callback de velocidade
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

                logger.info(f"[{nome_base}] ⬆️ Upload Telegram: {up_mb:.1f}/{total_mb:.1f} MB ({pct:.1f}%) - {speed:.2f} MB/s")
                last_up_log_time[0] = now

                if progress_callback and (now - last_up_tg_time[0] >= 5.0 or current == total):
                    progress_callback(f"☁️ **A guardar no Telegram Storage...**\n📊 `{pct:.1f}%` ({up_mb:.1f}/{total_mb:.1f} MB) à `{speed:.1f} MB/s`")
                    last_up_tg_time[0] = now

        async def upload_task():
            try:
                msg = await client.send_file(
                    Config.CANAL_STORAGE_ID,
                    caminho_temp,
                    caption=legenda,
                    parse_mode='md',
                    progress_callback=upload_progress
                )
            except Exception as err_md:
                logger.warning(f"⚠️ Erro ao formatar legenda em Markdown ({err_md}). A enviar em texto simples...")
                msg = await client.send_file(
                    Config.CANAL_STORAGE_ID,
                    caminho_temp,
                    caption=f"🎬 {nome_base}\n\nSalvo para o Jellyfin.",
                    progress_callback=upload_progress
                )
            return msg.id

        future = asyncio.run_coroutine_threadsafe(upload_task(), main_loop)
        msg_id_telegram = future.result()

        # 4. Criar ficheiro .strm
        url_stream = f"http://{Config.IP_SERVIDOR}:{Config.PORTA_STREAM_LOCAL}/stream/{media_id}"
        caminho_strm = os.path.join(pasta_dest, f"{nome_base}.strm")
        with open(caminho_strm, "w", encoding="utf-8") as f:
            f.write(url_stream + "\n")

        # 5. Registar na base de dados SQLite
        salvar_midia(
            media_id=media_id,
            tmdb_id=str(tmdb_info.get('id', '0')),
            tipo=tipo,
            titulo=titulo,
            ano=ano,
            qualidade=qualidade,
            msg_id_telegram=msg_id_telegram,
            caminho_strm=caminho_strm,
            status='COMPLETED'
        )

        atualizar_jellyfin()
        logger.info(f"🎉 Mídia {nome_base} hospedada e indexada com sucesso!")
        return True, nome_base, caminho_strm

    except Exception as e:
        logger.error(f"❌ Falha ao processar mídia {nome_base}: {e}")
        return False, str(e), None

    finally:
        if os.path.exists(caminho_temp):
            try:
                os.remove(caminho_temp)
                logger.info(f"🧹 Ficheiro temporário removido: {caminho_temp}")
            except Exception as e:
                logger.warning(f"⚠️ Não foiível remover ficheiro temporário: {e}")