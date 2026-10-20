import os
import json
import asyncio
from ytmusicapi import YTMusic
import aiofiles
from database import salvar_midia, obter_midia
from logger import logger

# Variável global de controle de pausa (Inicia como True para começar pausado por padrão)
MUSICA_PAUSADA = True

def alternar_pausa_musica():
    global MUSICA_PAUSADA
    MUSICA_PAUSADA = not MUSICA_PAUSADA
    logger.info(f"🎵 Estado da sincronização de músicas alterado: {'PAUSADO' if MUSICA_PAUSADA else 'ATIVO'}")
    return MUSICA_PAUSADA

def obter_status_pausa_musica():
    global MUSICA_PAUSADA
    return MUSICA_PAUSADA

async def sincronizar_historico_ytmusic(telegram_client, chat_id, limite=30):
    global MUSICA_PAUSADA
    if MUSICA_PAUSADA:
        return  # Se estiver pausado, nem executa a verificação

    try:
        # Verifica se o browser.json existe na VPS
        if not os.path.exists('browser.json'):
            logger.error("❌ Ficheiro 'browser.json' não encontrado na pasta do JellyBot!")
            return

        yt = YTMusic('browser.json')
        historico = yt.get_history()
        
        if not historico:
            return

        ultimas_musicas = historico[:limite]

        for item in ultimas_musicas:
            if MUSICA_PAUSADA:
                break

            videoId = item.get('videoId')
            titulo = item.get('title')
            artistas = item.get('artists', [{}])
            artista = artistas[0].get('name', 'Artista Desconhecido') if artistas else 'Artista Desconhecido'
            
            if not videoId or not titulo:
                continue

            media_id = f"music_{videoId}"
            
            midia_existente = obter_midia(media_id)
            if midia_existente:
                continue

            logger.info(f"🎵 Nova música detetada no histórico: {artista} - {titulo}")

            nome_arquivo_formatado = f"{artista} - {titulo}".replace("/", "-").replace("\\", "-").replace(":", "-").strip()
            caminho_mp3_final = f"downloads/{nome_arquivo_formatado}.mp3"
            output_template = f"downloads/{nome_arquivo_formatado}.%(ext)s"
            os.makedirs("downloads", exist_ok=True)
            
            import yt_dlp
            
            # Configuração limpa do yt-dlp com fluxo OAuth direto para autorizar na VPS
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': output_template,
                'username': 'oauth2',
                'password': '',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '320',
                }, {
                    'key': 'EmbedThumbnail',
                }, {
                    'key': 'FFmpegMetadata',
                }],
                'quiet': False
            }

            loop = asyncio.get_running_loop()
            def baixar_audio():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([f"https://music.youtube.com/watch?v={videoId}"])

            await loop.run_in_executor(None, baixar_audio)

            if os.path.exists(caminho_mp3_final):
                caminho_mp3 = caminho_mp3_final
            else:
                caminho_mp3_fallback = f"downloads/{videoId}.mp3"
                if os.path.exists(caminho_mp3_fallback):
                    os.rename(caminho_mp3_fallback, caminho_mp3_final)
                    caminho_mp3 = caminho_mp3_final
                else:
                    caminho_mp3 = caminho_mp3_fallback

            if not os.path.exists(caminho_mp3):
                continue

            msg_telegram = await telegram_client.send_file(
                chat_id,
                caminho_mp3,
                caption=f"🎵 **{titulo}**\n👤 {artista}",
                parse_mode='md'
            )

            pasta_strm = "midias/musicas"
            os.makedirs(pasta_strm, exist_ok=True)
            caminho_strm = os.path.join(pasta_strm, f"{videoId}.strm")

            porta_stream = os.getenv("PORTA_STREAM_LOCAL", "8081")
            ip_servidor = os.getenv("IP_SERVIDOR", "localhost")
            url_stream = f"http://{ip_servidor}:{porta_stream}/stream/{msg_telegram.id}"

            async with aiofiles.open(caminho_strm, 'w', encoding='utf-8') as f:
                await f.write(url_stream)

            salvar_midia(
                media_id=media_id,
                tipo="music",
                tmdb_id=0,
                titulo=f"{artista} - {titulo}",
                ano="N/A",
                qualidade="YT Music 320kbps 🎧",
                caminho_strm=caminho_strm,
                msg_id_telegram=str(msg_telegram.id)
            )

            try:
                os.remove(caminho_mp3)
            except:
                pass

            logger.info(f"✅ Música sincronizada e hospedada com sucesso: {artista} - {titulo}")

    except Exception as e:
        logger.error(f"❌ Erro na sincronização do histórico do YouTube Music: {e}")