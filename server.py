import asyncio
import re
from aiohttp import web
from telethon import TelegramClient
from config import Config
from logger import logger
from database import obter_midia

# Cliente do Telethon unificado
client = TelegramClient('jelly_session', Config.API_ID, Config.API_HASH)

async def handle_stream(request):
    media_id = request.match_info.get('media_id')
    midia = obter_midia(media_id)

    if not midia or not midia.get("msg_id_telegram"):
        logger.warning(f"⚠️ Pedido de stream para ID inexistente: {media_id}")
        return web.Response(status=404, text="Mídia não encontrada na base de dados.")

    try:
        msg_id = int(midia["msg_id_telegram"])
        msg = await client.get_messages(Config.CANAL_STORAGE_ID, ids=msg_id)

        if not msg or not msg.media:
            logger.error(f"❌ Mídia ausente no canal do Telegram para a mensagem ID: {msg_id}")
            return web.Response(status=404, text="Ficheiro não encontrado no canal do Telegram.")

        file_size = msg.file.size if msg.file else 0
        range_header = request.headers.get('Range')

        # Processamento de HTTP Range Requests (Seek / Suporte a salto no tempo)
        if range_header:
            match = re.search(r'bytes=(\d+)-(\d+)?', range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1

                if start >= file_size:
                    return web.Response(status=416, headers={'Content-Range': f'bytes */{file_size}'})

                content_length = end - start + 1
                headers = {
                    'Content-Type': 'video/mp4',
                    'Content-Range': f'bytes {start}-{end}/{file_size}',
                    'Content-Length': str(content_length),
                    'Accept-Ranges': 'bytes',
                }

                response = web.StreamResponse(status=206, headers=headers)
                await response.prepare(request)

                async for chunk in client.iter_download(msg.media, offset=start, limit=content_length):
                    await response.write(chunk)

                return response

        # Download completo padrão (Sem cabeçalho Range)
        headers = {
            'Content-Type': 'video/mp4',
            'Content-Length': str(file_size),
            'Accept-Ranges': 'bytes',
        }
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)

        async for chunk in client.iter_download(msg.media):
            await response.write(chunk)

        return response

    except Exception as e:
        logger.error(f"❌ Erro durante o streaming de {media_id}: {e}")
        return web.Response(status=500, text=f"Erro interno no servidor de streaming: {e}")

async def iniciar_servidor():
    logger.info("⚡ A iniciar sessão no Telegram (Telethon)...")
    await client.start(bot_token=Config.TELEGRAM_TOKEN)
    logger.info("✅ Telethon ligado com sucesso!")

    app = web.Application()
    app.router.add_get('/stream/{media_id}', handle_stream)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', Config.PORTA_STREAM_LOCAL)
    await site.start()
    
    logger.info(f"📡 Servidor de Streaming HTTP ativo em http://{Config.IP_SERVIDOR}:{Config.PORTA_STREAM_LOCAL}/stream/")