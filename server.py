import os
import re
import asyncio
from aiohttp import web
from telethon import TelegramClient
from config import Config
from logger import logger
from database import obter_midia, listar_midias

BASE_DIR = getattr(Config, 'BASE_DIR', os.path.dirname(os.path.abspath(__file__)))

if not Config.TELEGRAM_API_ID or not Config.TELEGRAM_API_HASH:
    raise ValueError("❌ TELEGRAM_API_ID e TELEGRAM_API_HASH precisam estar definidos no ficheiro .env!")

client = TelegramClient(
    os.path.join(BASE_DIR, "jellybot_session"),
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
        mensagens = await client.get_messages(Config.CANAL_STORAGE_ID, ids=msg_ids)
        if not isinstance(mensagens, list):
            mensagens = [mensagens]

        mensagens = [m for m in mensagens if m and m.media]
        if not mensagens:
            return web.Response(status=404, text="Arquivos de mídia não encontrados no canal do Telegram.")

        tamanho_total = sum(m.file.size for m in mensagens)

        range_header = request.headers.get("Range")
        start = 0
        end = tamanho_total - 1
        status_code = 200

        if range_header:
            status_code = 206
            if range_header.startswith("bytes="):
                range_val = range_header.replace("bytes=", "").strip()
                if range_val.startswith("-"):
                    suffix_length = int(range_val[1:])
                    start = max(0, tamanho_total - suffix_length)
                    end = tamanho_total - 1
                elif "-" in range_val:
                    parts = range_val.split("-")
                    if parts[0].isdigit():
                        start = int(parts[0])
                    if parts[1].isdigit():
                        end = int(parts[1])

        end = min(end, tamanho_total - 1)
        start = max(0, min(start, end))
        length = end - start + 1

        headers = {
            "Content-Type": "video/mp4",
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{tamanho_total}",
            "Content-Length": str(length),
            "Access-Control-Allow-Origin": "*"
        }

        if request.method == "HEAD":
            return web.Response(status=status_code, headers=headers)

        response = web.StreamResponse(status=status_code, headers=headers)
        await response.prepare(request)

        offset_atual = 0
        for msg in mensagens:
            tamanho_parte = msg.file.size
            inicio_parte = offset_atual
            fim_parte = offset_atual + tamanho_parte - 1

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

async def handle_web_dashboard(request):
    """Página Web simples para monitorar as mídias salvas diretamente no navegador."""
    midias = listar_midias()
    
    html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JellyBot — Painel Web</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }
        h1 { color: #38bdf8; text-align: center; }
        .container { max-width: 900px; margin: 0 auto; }
        .card { background: #1e293b; padding: 15px 20px; border-radius: 10px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }
        .badge { background: #0284c7; padding: 4px 10px; border-radius: 6px; font-size: 0.85em; font-weight: bold; }
        .status { color: #4ade80; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🍿 JellyBot Dashboard</h1>
        <p style="text-align:center; color:#94a3b8;">Mídias salvas no Telegram Storage e ativas no Jellyfin</p>
        <div id="midias-list">
    """
    
    for m in midias:
        tipo_ico = "🎬" if m["tipo"] == "movie" else "📺"
        html += f"""
        <div class="card">
            <div>
                <strong>{tipo_ico} {m['titulo']} ({m['ano']})</strong><br>
                <span style="font-size:0.85em; color:#94a3b8;">Qualidade: {m['qualidade']}</span>
            </div>
            <div>
                <span class="badge">{m['tipo'].upper()}</span>
                <span class="status">✓ Concluído</span>
            </div>
        </div>
        """
        
    html += """
        </div>
    </div>
</body>
</html>
    """
    return web.Response(text=html, content_type="text/html")

async def iniciar_servidor():
    await client.start(bot_token=Config.TELEGRAM_TOKEN)
    logger.info("🤖 Sessão do Telethon iniciada no servidor HTTP.")

    app = web.Application()
    app.router.add_route("*", "/stream/{media_id}", handle_stream)
    app.router.add_get("/web", handle_web_dashboard)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", Config.PORTA_STREAM_LOCAL)
    await site.start()
    logger.info(f"🌐 Servidor de Streaming rodando na porta {Config.PORTA_STREAM_LOCAL}")
    logger.info(f"🌐 Painel Web disponível em http://{Config.IP_SERVIDOR}:{Config.PORTA_STREAM_LOCAL}/web")