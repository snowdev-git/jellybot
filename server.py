import os
import re
import asyncio
from aiohttp import web
from telethon import TelegramClient
from config import Config
from logger import logger
from database import obter_midia

client = TelegramClient(
    os.path.join(Config.BASE_DIR, "jellybot_session"),
    Config.TELEGRAM_API_ID,
    Config.TELEGRAM_API_HASH
)

async def handle_stream(request):
    media_id = request.match_info.get("media_id")
    midia = obter_midia(media_id)

    if not midia:
        return web.Response(status=404, text="Mídia não encontrada na base de dados.")

    msg_ids_raw = str(midia["msg_id_telegram"]).split(",")
    msg_ids = [int(m.strip()) for m in msg_ids_raw if m.strip().isdigit()]

    if not msg_ids:
        return web.Response(status=404, text="Nenhum ID de mensagem registrado no Telegram Storage.")

    try:
        # Obter mensagens e arquivos do Telegram
        mensagens = await client.get_messages(Config.CANAL_STORAGE_ID, ids=msg_ids)
        if not isinstance(mensagens, list):
            mensagens = [mensagens]

        mensagens = [m for m in mensagens if m and m.media]
        if not mensagens:
            return web.Response(status=404, text="Arquivos de mídia não encontrados no canal do Telegram.")

        # Calcular tamanho total somando as partes
        tamanho_total = sum(m.file.size for m in mensagens)
        
        range_header = request.headers.get("Range")
        start = 0
        end = tamanho_total - 1

        if range_header:
            match = re.search(r"bytes=(\d+)-(\d+)?", range_header)
            if match:
                start = int(match.group(1))
                if match.group(2):
                    end = int(match.group(2))

        end = min(end, tamanho_total - 1)
        length = end - start + 1

        headers = {
            "Content-Type": "video/mp4",
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{tamanho_total}",
            "Content-Length": str(length),
            "Access-Control-Allow-Origin": "*"
        }

        response = web.StreamResponse(status=206, headers=headers)
        await response.prepare(request)

        # Gerador sequencial através das partes divididas
        offset_atual = 0
        for msg in mensagens:
            tamanho_parte = msg.file.size
            inicio_parte = offset_atual
            fim_parte = offset_atual + tamanho_parte - 1

            # Verificar se o range solicitado engloba esta parte
            if start <= fim_parte and end >= inicio_parte:
                req_start = max(0, start - inicio_parte)
                req_end = min(tamanho_parte - 1, end - inicio_parte)

                async for chunk in client.iter_download(msg.media, offset=req_start, request_size=512 * 1024):
                    if request.transport.is_closing():
                        break
                    
                    bytes_a_enviar = chunk[:min(len(chunk), req_end - req_start + 1)]
                    await response.write(bytes_a_enviar)
                    req_start += len(bytes_a_enviar)
                    if req_start > req_end:
                        break

            offset_atual += tamanho_parte
            if offset_atual > end:
                break

        await response.write_eof()
        return response

    except Exception as e:
        logger.error(f"❌ Erro ao servir stream {media_id}: {e}")
        return web.Response(status=500, text=f"Erro interno no servidor de streaming: {e}")

async def iniciar_servidor():
    await client.start(bot_token=Config.TELEGRAM_TOKEN)
    logger.info("🤖 Sessão do Telethon iniciada no servidor HTTP.")

    app = web.Application()
    app.router.add_get("/stream/{media_id}", handle_stream)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", Config.PORTA_STREAM_LOCAL)
    await site.start()
    logger.info(f"🌐 Servidor de Streaming rodando na porta {Config.PORTA_STREAM_LOCAL}")