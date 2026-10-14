import os
import re
import json
import asyncio
import requests
import aiohttp
from aiohttp import web
from config import Config
from logger import logger
from telegram_client import client
from database import (
    obter_midia,
    listar_midias,
    remover_midia,
    adicionar_a_fila,
    obter_proximo_fila,
    atualizar_status_fila,
    listar_fila,
    remover_item_fila,
    resetar_item_fila,
    obter_mapa_status_midias
)
from downloader import buscar_fontes_indexadores, processar_e_hospedar_midia

FILA_PAUSADA = False

async def notificar_telegram_admin(texto: str, caminho_file: str = None):
    if Config.ADMIN_ID and Config.ADMIN_ID != 0:
        try:
            if caminho_file and os.path.exists(caminho_file):
                await client.send_file(Config.ADMIN_ID, caminho_file, caption=texto, parse_mode='md')
            else:
                await client.send_message(Config.ADMIN_ID, texto, parse_mode='md')
        except Exception as e:
            logger.warning(f"⚠️ Não foi possível enviar notificação ao Admin ({Config.ADMIN_ID}): {e}")

async def worker_fila_processamento():
    global FILA_PAUSADA
    logger.info("⚙️ Worker de Fila de Espera iniciado e monitorando novas solicitações...")
    while True:
        try:
            if not FILA_PAUSADA:
                item = obter_proximo_fila()
                if item:
                    item_id = item['id']
                    titulo = item['titulo']
                    logger.info(f"▶️ [FILA] A iniciar processamento do item: {titulo} ({item['qualidade']})")
                    
                    msg_inicio = f"⏳ **[JellyBot Web] Processamento Iniciado:**\n🎬 `{titulo}`\n🎧 Qualidade: `{item['qualidade']}`"
                    await notificar_telegram_admin(msg_inicio)
                    
                    atualizar_status_fila(item_id, 'PROCESSING', '⏳ A verificar fonte de vídeo...')

                    def callback_progresso(msg_status):
                        atualizar_status_fila(item_id, 'PROCESSING', str(msg_status))

                    sucesso, msg_res, caminho_strm = await processar_e_hospedar_midia(
                        url_fonte=item['url_fonte'],
                        tipo=item['tipo'],
                        tmdb_info=item['tmdb_info'],
                        qualidade=item['qualidade'],
                        progress_callback=callback_progresso
                    )

                    if sucesso:
                        atualizar_status_fila(item_id, 'COMPLETED', '✅ Processado com sucesso e ativo no Jellyfin!')
                        logger.info(f"🎉 [FILA] Item {titulo} concluído com sucesso!")
                        msg_sucesso = (
                            f"🎉 **[JellyBot Web] Mídia Concluída com Sucesso!**\n\n"
                            f"🎬 **Mídia:** `{titulo}`\n"
                            f"🎧 **Qualidade:** `{item['qualidade']}`\n"
                            f"📂 **Caminho .strm:** `{caminho_strm}`\n\n"
                            f"👉 _Disponível no Jellyfin!_"
                        )
                        await notificar_telegram_admin(msg_sucesso, caminho_file=caminho_strm)
                    else:
                        atualizar_status_fila(item_id, 'FAILED', '❌ Falha ao processar', erro_msg=str(msg_res))
                        logger.error(f"❌ [FILA] Falha no item {titulo}: {msg_res}")
                        msg_erro = f"❌ **[JellyBot Web] Falha ao Processar:**\n🎬 `{titulo}`\n⚠️ Erro: `{msg_res}`"
                        await notificar_telegram_admin(msg_erro)

            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"⚠️ Erro no worker da fila: {e}")
            await asyncio.sleep(5)

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

async def api_search(request):
    q = request.query.get('q', '').strip()
    tipo = request.query.get('tipo', 'movie')
    if not q:
        return web.json_response([])
    url = f"https://api.themoviedb.org/3/search/{tipo}?api_key={Config.TMDB_API_KEY}&query={q}&language=pt-BR"
    try:
        res = requests.get(url, timeout=10).json().get("results", [])[:12]
        mapa_status = obter_mapa_status_midias()
        
        for item in res:
            if tipo == 'movie':
                nome_limpo = re.sub(r'[\\/*?:"<>|]', "", str(item.get("title", "Filme"))).strip()
                ano = str(item.get("release_date", "0000"))[:4]
                nome_slug = re.sub(r'\W+', '_', nome_limpo).lower()
                media_id = f"movie_{item.get('id')}_{nome_slug}_{ano}"
            else:
                media_id = f"tv_show_{item.get('id')}"
            
            item['status_local'] = mapa_status.get(media_id)
            
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_details(request):
    tmdb_id = request.query.get('tmdb_id')
    tipo = request.query.get('tipo', 'movie')
    
    url = f"https://api.themoviedb.org/3/{tipo}/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR&append_to_response=external_ids"
    try:
        res = requests.get(url, timeout=10).json()
        mapa_status = obter_mapa_status_midias()

        if tipo == 'movie':
            nome_limpo = re.sub(r'[\\/*?:"<>|]', "", str(res.get("title", "Filme"))).strip()
            ano = str(res.get("release_date", "0000"))[:4]
            nome_slug = re.sub(r'\W+', '_', nome_limpo).lower()
            media_id = f"movie_{res.get('id')}_{nome_slug}_{ano}"
            res['status_local'] = mapa_status.get(media_id)

        return web.json_response(res)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_episodes(request):
    tmdb_id = request.query.get('tmdb_id')
    season = request.query.get('season', '1')
    
    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}?api_key={Config.TMDB_API_KEY}&language=pt-BR"
    try:
        res = requests.get(url, timeout=10).json()
        episodes = res.get('episodes', [])
        mapa_status = obter_mapa_status_midias()

        for ep in episodes:
            ep_num = ep.get('episode_number', 1)
            media_id = f"tv_{tmdb_id}_s{int(season):02d}e{int(ep_num):02d}"
            ep['status_local'] = mapa_status.get(media_id)

        return web.json_response(episodes)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_sources(request):
    tmdb_id = request.query.get('tmdb_id')
    tipo = request.query.get('tipo', 'movie')
    season = request.query.get('season')
    episode = request.query.get('episode')

    if tipo == 'movie':
        url_tmdb = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR&append_to_response=external_ids"
    else:
        url_tmdb = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR&append_to_response=external_ids"

    try:
        res_tmdb = requests.get(url_tmdb, timeout=10).json()
        imdb_id = res_tmdb.get('external_ids', {}).get('imdb_id')
        if not imdb_id:
            return web.json_response({"error": "IMDB ID não encontrado no TMDB."}, status=404)

        if tipo == 'movie':
            opcoes = await buscar_fontes_indexadores("movie", imdb_id)
        else:
            opcoes = await buscar_fontes_indexadores("series", imdb_id, int(season), int(episode))

        return web.json_response({"tmdb_info": res_tmdb, "opcoes": opcoes})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_add_queue(request):
    data = await request.json()
    tipo = data.get("tipo")
    tmdb_info = data.get("tmdb_info")
    qualidade = data.get("qualidade")
    url_fonte = data.get("url_fonte")

    if tipo == "movie":
        nome_limpo = re.sub(r'[\\/*?:"<>|]', "", str(tmdb_info.get("title", "Filme"))).strip()
        ano = str(tmdb_info.get("release_date", "0000"))[:4]
        nome_slug = re.sub(r'\W+', '_', nome_limpo).lower()
        media_id = f"movie_{tmdb_info.get('id')}_{nome_slug}_{ano}"
        titulo = f"{nome_limpo} ({ano})"
    else:
        nome_limpo = re.sub(r'[\\/*?:"<>|]', "", str(tmdb_info.get("name", "Serie"))).strip()
        season_num = int(data.get("season", 1))
        ep_num = int(data.get("episode", 1))
        tmdb_info['season_number'] = season_num
        tmdb_info['episode_number'] = ep_num
        media_id = f"tv_{tmdb_info.get('id')}_s{season_num:02d}e{ep_num:02d}"
        titulo = f"{nome_limpo} S{season_num:02d}E{ep_num:02d}"
        ano = str(tmdb_info.get("first_air_date", "0000"))[:4]

    adicionar_a_fila(media_id, tipo, tmdb_info.get('id'), titulo, ano, qualidade, url_fonte, tmdb_info)
    
    msg_add = f"📥 **[Web UI] Novo Item Adicionado à Fila:**\n🎬 `{titulo}`\n🎧 Qualidade: `{qualidade}`"
    await notificar_telegram_admin(msg_add)

    return web.json_response({"success": True, "message": f"{titulo} adicionado à fila!"})

async def api_toggle_queue_pause(request):
    global FILA_PAUSADA
    FILA_PAUSADA = not FILA_PAUSADA
    status_str = "PAUSADA ⏸️" if FILA_PAUSADA else "RODANDO ▶️"
    await notificar_telegram_admin(f"⚙️ **[JellyBot Web] Estado da Fila:** `{status_str}`")
    return web.json_response({"success": True, "fila_pausada": FILA_PAUSADA})

async def api_get_queue(request):
    fila = listar_fila()
    salvos = listar_midias()
    return web.json_response({"fila": fila, "salvos": salvos, "fila_pausada": FILA_PAUSADA})

async def api_retry_queue(request):
    data = await request.json()
    item_id = data.get("id")
    resetar_item_fila(item_id)
    return web.json_response({"success": True})

async def api_delete_queue(request):
    data = await request.json()
    item_id = data.get("id")
    media_id = data.get("media_id")
    if item_id:
        remover_item_fila(item_id)
    if media_id:
        remover_midia(media_id)
    return web.json_response({"success": True})

async def api_catalog_movies(request):
    api_key = Config.TMDB_API_KEY
    url = f"https://api.themoviedb.org/3/movie/now_playing?api_key={api_key}&language=pt-BR&page=1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return web.json_response({"status": "success", "results": data.get("results", [])})
                return web.json_response({"status": "error", "results": []}, status=resp.status)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def api_catalog_series(request):
    api_key = Config.TMDB_API_KEY
    url = f"https://api.themoviedb.org/3/tv/on_the_air?api_key={api_key}&language=pt-BR&page=1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return web.json_response({"status": "success", "results": data.get("results", [])})
                return web.json_response({"status": "error", "results": []}, status=resp.status)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def api_series_continue(request):
    salvos = listar_midias("series")
    series_map = {}
    
    for item in salvos:
        mid = item.get("media_id", "")
        match = re.match(r"tv_(\d+)_s(\d+)e(\d+)", mid)
        if match:
            tmdb_id, s_num, ep_num = match.groups()
            s_num, ep_num = int(s_num), int(ep_num)
            if tmdb_id not in series_map:
                series_map[tmdb_id] = {
                    "tmdb_id": tmdb_id,
                    "titulo": item.get("titulo", "").split(" S")[0],
                    "ultimo_s": s_num,
                    "ultimo_ep": ep_num
                }
            else:
                curr = series_map[tmdb_id]
                if (s_num > curr["ultimo_s"]) or (s_num == curr["ultimo_s"] and ep_num > curr["ultimo_ep"]):
                    curr["ultimo_s"] = s_num
                    curr["ultimo_ep"] = ep_num

    resultado = []
    api_key = Config.TMDB_API_KEY
    async with aiohttp.ClientSession() as session:
        for tmdb_id, info in series_map.items():
            try:
                url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={api_key}&language=pt-BR"
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        info["poster_path"] = data.get("poster_path")
                        info["nome"] = data.get("name", info["titulo"])
                        info["vote_average"] = data.get("vote_average", 0)
                        resultado.append(info)
            except Exception as e:
                resultado.append(info)

    return web.json_response({"status": "success", "results": resultado})

async def handle_web_app(request):
    html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JellyBot — Cinema & Automação</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🍿</text></svg>">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #0b1329; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; }
        .glass { background: rgba(23, 32, 54, 0.85); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.08); }
        .glass-card { background: rgba(15, 23, 42, 0.75); border: 1px solid rgba(255,255,255,0.06); }
        .glass-card:hover { border-color: rgba(56, 189, 248, 0.4); transform: translateY(-2px); transition: all 0.2s ease; }
    </style>
</head>
<body class="min-h-screen pb-16">
    <nav class="glass sticky top-0 z-50 px-6 py-4 mb-8 flex justify-between items-center shadow-2xl">
        <div class="flex items-center space-x-3 cursor-pointer" onclick="voltarParaPesquisa()">
            <span class="text-3xl">🍿</span>
            <span class="text-xl font-black bg-gradient-to-r from-sky-400 via-blue-500 to-indigo-500 bg-clip-text text-transparent">JellyBot Web</span>
        </div>
        <div class="flex space-x-2">
            <button onclick="tab('search')" id="tab-search" class="px-4 py-2 rounded-xl font-bold transition bg-blue-600 text-white shadow-lg shadow-blue-500/20"><i class="fa-solid fa-house mr-2"></i>Início & Pesquisa</button>
            <button onclick="tab('queue')" id="tab-queue" class="px-4 py-2 rounded-xl font-bold transition bg-slate-800 hover:bg-slate-700 text-slate-300"><i class="fa-solid fa-list-check mr-2"></i>Fila & Status</button>
            <button onclick="tab('library')" id="tab-library" class="px-4 py-2 rounded-xl font-bold transition bg-slate-800 hover:bg-slate-700 text-slate-300"><i class="fa-solid fa-film mr-2"></i>Biblioteca</button>
        </div>
    </nav>

    <div class="max-w-6xl mx-auto px-4">
        <div id="sec-search" class="space-y-8">
            <div class="glass p-6 rounded-2xl shadow-xl">
                <div class="flex flex-col md:flex-row gap-4">
                    <select id="search-type" class="bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 font-bold text-sky-400 focus:outline-none focus:border-sky-500">
                        <option value="movie">🎬 Filme</option>
                        <option value="tv">📺 Série</option>
                    </select>
                    <input type="text" id="search-input" placeholder="Digite o nome do filme ou série..." class="flex-1 bg-slate-900 border border-slate-700 rounded-xl px-5 py-3 text-white placeholder-slate-500 focus:outline-none focus:border-sky-500" oninput="checarLimpezaBusca()" onkeypress="if(event.key==='Enter') pesquisarTMDB()">
                    <button onclick="pesquisarTMDB()" class="bg-blue-600 hover:bg-blue-500 font-extrabold px-8 py-3 rounded-xl transition shadow-lg shadow-blue-600/30 flex items-center justify-center"><i class="fa-solid fa-search mr-2"></i>Buscar</button>
                </div>
            </div>

            <div id="home-dashboard" class="space-y-8">
                <div id="container-continue-series" class="hidden space-y-4">
                    <div class="flex items-center justify-between">
                        <h3 class="text-xl font-extrabold text-emerald-400 flex items-center gap-2">
                            <i class="fa-solid fa-play-circle"></i> Séries em Andamento / Continuar
                        </h3>
                        <span class="text-xs text-slate-400 font-semibold">Baseado no acervo local</span>
                    </div>
                    <div id="home-continue-grid" class="grid grid-cols-2 md:grid-cols-4 gap-6"></div>
                </div>

                <div class="space-y-4">
                    <h3 class="text-xl font-extrabold text-sky-400 flex items-center gap-2">
                        <i class="fa-solid fa-fire text-amber-400"></i> Filmes em Cartaz / Lançamentos
                    </h3>
                    <div id="home-movies-grid" class="grid grid-cols-2 md:grid-cols-4 gap-6"></div>
                </div>

                <div class="space-y-4">
                    <h3 class="text-xl font-extrabold text-indigo-400 flex items-center gap-2">
                        <i class="fa-solid fa-tv"></i> Séries Populares em Exibição
                    </h3>
                    <div id="home-series-grid" class="grid grid-cols-2 md:grid-cols-4 gap-6"></div>
                </div>
            </div>

            <div id="results-tmdb" class="hidden grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6"></div>
        </div>

        <div id="sec-detail" class="hidden space-y-6">
            <button onclick="voltarParaPesquisa()" class="bg-slate-800 hover:bg-slate-700 text-sky-400 font-bold px-4 py-2 rounded-xl border border-slate-700 text-xs flex items-center gap-2">
                <i class="fa-solid fa-arrow-left"></i> Voltar
            </button>

            <div id="detail-header" class="glass p-6 rounded-3xl flex flex-col md:flex-row gap-6 items-start"></div>

            <div id="detail-tv-content" class="hidden space-y-6">
                <div class="flex items-center space-x-3 overflow-x-auto pb-2" id="season-tabs"></div>
                <div id="episodes-grid" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
            </div>

            <div id="detail-movie-content" class="hidden space-y-4 glass p-6 rounded-3xl">
                <h3 class="text-lg font-extrabold text-sky-400 mb-2"><i class="fa-solid fa-cloud-arrow-down mr-2"></i>Opcoes de Download e Fontes</h3>
                <div id="movie-sources-list" class="space-y-3"></div>
            </div>
        </div>

        <div id="sec-queue" class="hidden space-y-4">
            <div class="glass p-4 rounded-2xl flex flex-col sm:flex-row justify-between items-center gap-4">
                <div class="flex items-center gap-3">
                    <h2 class="text-xl font-extrabold text-slate-100 flex items-center gap-2"><i class="fa-solid fa-list-check text-sky-400"></i> Fila de Automacao</h2>
                    <span id="queue-status-badge" class="text-xs px-3 py-1 rounded-full font-bold"></span>
                </div>
                <button id="btn-toggle-pause" onclick="alternarPausaFila()" class="px-5 py-2.5 rounded-xl font-extrabold text-xs transition flex items-center gap-2 shadow-lg">
                </button>
            </div>
            <div id="queue-list" class="space-y-4"></div>
        </div>

        <div id="sec-library" class="hidden space-y-4">
            <h2 class="text-xl font-extrabold text-slate-100 mb-2 flex items-center gap-2"><i class="fa-solid fa-film text-emerald-400"></i> Midias Ativas no Jellyfin</h2>
            <div id="library-list" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </div>
    </div>

    <div id="ep-sources-modal" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="glass p-6 rounded-3xl max-w-2xl w-full border border-sky-500/30 space-y-4 max-h-[90vh] overflow-y-auto">
            <div class="flex justify-between items-center border-b border-slate-700/60 pb-3">
                <h3 id="ep-sources-title" class="text-lg font-extrabold text-sky-400"></h3>
                <button onclick="fecharModalEp()" class="text-slate-400 hover:text-white text-2xl font-bold">&times;</button>
            </div>
            <div id="ep-sources-list" class="space-y-2"></div>
            <div class="pt-4 border-t border-slate-700/60">
                <p class="text-xs font-bold text-slate-400 mb-2">Ou cole um Link Direto de Vídeo (HTTP/MP4):</p>
                <div class="flex gap-2">
                    <input type="text" id="ep-manual-url" placeholder="http://servidor.com/video.mp4" class="flex-1 bg-slate-900 border border-slate-700 rounded-xl px-4 py-2 text-sm text-white focus:outline-none focus:border-sky-500">
                    <button onclick="adicionarManualEp()" class="bg-emerald-600 hover:bg-emerald-500 text-xs font-bold px-4 py-2 rounded-xl shadow-lg">➕ Adicionar Link</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Contêiner de Notificações Toast Flutuantes -->
    <div id="toast-container" class="fixed bottom-5 right-5 z-50 flex flex-col gap-2 pointer-events-none"></div>

    <script>
        let currentDetailMedia = null;
        let currentSelectedEp = null;
        let isQueuePaused = false;
        let lastQueueState = {};

        function showToast(mensagem, tipo = 'info') {
            const container = document.getElementById('toast-container');
            if(!container) return;

            const toast = document.createElement('div');
            let icon = 'fa-circle-info text-sky-400';
            let border = 'border-sky-500/40';

            if (tipo === 'success') { icon = 'fa-circle-check text-emerald-400'; border = 'border-emerald-500/40'; }
            if (tipo === 'warning') { icon = 'fa-bell text-amber-400'; border = 'border-amber-500/40'; }
            if (tipo === 'error') { icon = 'fa-triangle-exclamation text-rose-400'; border = 'border-rose-500/40'; }

            toast.className = `pointer-events-auto glass px-4 py-3 rounded-2xl shadow-2xl border ${border} flex items-center gap-3 text-sm text-white font-bold transform translate-y-4 opacity-0 transition-all duration-300 max-w-md`;
            toast.innerHTML = `<i class="fa-solid ${icon} text-lg"></i> <span>${mensagem}</span>`;

            container.appendChild(toast);

            requestAnimationFrame(() => {
                toast.classList.remove('translate-y-4', 'opacity-0');
            });

            setTimeout(() => {
                toast.classList.add('translate-y-4', 'opacity-0');
                setTimeout(() => toast.remove(), 300);
            }, 4000);
        }

        async function monitorarNotificacoesFila() {
            try {
                const data = await fetch('/api/queue').then(r => r.json());
                (data.fila || []).forEach(item => {
                    const prevStatus = lastQueueState[item.id];
                    if (prevStatus && prevStatus !== item.status) {
                        if (item.status === 'PROCESSING') {
                            showToast(`🚀 Download iniciado: ${item.titulo}`, 'warning');
                        } else if (item.status === 'COMPLETED') {
                            showToast(`🎉 Concluído e ativo no Jellyfin: ${item.titulo}`, 'success');
                        } else if (item.status === 'FAILED') {
                            showToast(`❌ Falha no download: ${item.titulo}`, 'error');
                        }
                    }
                    lastQueueState[item.id] = item.status;
                });
            } catch (e) {}
        }

        function tab(t) {
            ['search', 'detail', 'queue', 'library'].forEach(s => {
                const el = document.getElementById('sec-' + s);
                if(el) el.classList.add('hidden');
                const btn = document.getElementById('tab-' + s);
                if(btn) btn.className = 'px-4 py-2 rounded-xl font-bold transition bg-slate-800 hover:bg-slate-700 text-slate-300';
            });
            document.getElementById('sec-' + t).classList.remove('hidden');
            const activeBtn = document.getElementById('tab-' + t);
            if(activeBtn) activeBtn.className = 'px-4 py-2 rounded-xl font-bold transition bg-blue-600 text-white shadow-lg shadow-blue-500/20';

            if (t === 'search') carregarDashboardHome();
            if (t === 'queue' || t === 'library') atualizarFilaEBiblioteca();
        }

        function voltarParaPesquisa() {
            tab('search');
            document.getElementById('search-input').value = '';
            checarLimpezaBusca();
        }

        function checarLimpezaBusca() {
            const q = document.getElementById('search-input').value.trim();
            if (!q) {
                document.getElementById('results-tmdb').classList.add('hidden');
                document.getElementById('home-dashboard').classList.remove('hidden');
            }
        }

        async function carregarDashboardHome() {
            try {
                const resCont = await fetch('/api/series/continue').then(r => r.json());
                if (resCont.results && resCont.results.length > 0) {
                    let hC = '';
                    resCont.results.forEach(item => {
                        const poster = item.poster_path ? `https://image.tmdb.org/t/p/w500${item.poster_path}` : 'https://via.placeholder.com/300x450';
                        const ultEpText = `S${String(item.ultimo_s).padStart(2,'0')}E${String(item.ultimo_ep).padStart(2,'0')}`;
                        hC += `
                        <div onclick="abrirDetalhes('${item.tmdb_id}', 'tv')" class="glass-card rounded-2xl overflow-hidden cursor-pointer group relative border border-emerald-500/30">
                            <span class="absolute top-2 right-2 bg-emerald-500 text-slate-950 font-black text-[10px] px-2 py-0.5 rounded-full shadow-lg">Parou no ${ultEpText}</span>
                            <img src="${poster}" class="w-full h-64 object-cover group-hover:scale-105 transition duration-300">
                            <div class="p-3 bg-slate-950/80">
                                <h4 class="font-bold text-xs text-white line-clamp-1">${item.nome}</h4>
                                <button class="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-extrabold text-[11px] py-1.5 rounded-lg mt-2 transition flex items-center justify-center gap-1">
                                    <i class="fa-solid fa-play text-[9px]"></i> Baixar Próximo
                                </button>
                            </div>
                        </div>`;
                    });
                    document.getElementById('home-continue-grid').innerHTML = hC;
                    document.getElementById('container-continue-series').classList.remove('hidden');
                }
            } catch (e) {
                console.error("Erro ao carregar séries em andamento:", e);
            }

            try {
                const resM = await fetch('/api/catalog/movies').then(r => r.json());
                if (resM.results) {
                    let hM = '';
                    resM.results.slice(0, 8).forEach(item => {
                        const poster = item.poster_path ? `https://image.tmdb.org/t/p/w500${item.poster_path}` : 'https://via.placeholder.com/300x450';
                        hM += `
                        <div onclick="abrirDetalhes('${item.id}', 'movie')" class="glass-card rounded-2xl overflow-hidden cursor-pointer group">
                            <img src="${poster}" class="w-full h-64 object-cover group-hover:scale-105 transition duration-300">
                            <div class="p-3 bg-slate-950/80">
                                <h4 class="font-bold text-xs text-white line-clamp-1">${item.title}</h4>
                                <p class="text-[10px] text-slate-400 mt-0.5">⭐ ${item.vote_average ? item.vote_average.toFixed(1) : 'N/A'}</p>
                            </div>
                        </div>`;
                    });
                    document.getElementById('home-movies-grid').innerHTML = hM;
                }
            } catch(e) {}

            try {
                const resS = await fetch('/api/catalog/series').then(r => r.json());
                if (resS.results) {
                    let hS = '';
                    resS.results.slice(0, 8).forEach(item => {
                        const poster = item.poster_path ? `https://image.tmdb.org/t/p/w500${item.poster_path}` : 'https://via.placeholder.com/300x450';
                        hS += `
                        <div onclick="abrirDetalhes('${item.id}', 'tv')" class="glass-card rounded-2xl overflow-hidden cursor-pointer group">
                            <img src="${poster}" class="w-full h-64 object-cover group-hover:scale-105 transition duration-300">
                            <div class="p-3 bg-slate-950/80">
                                <h4 class="font-bold text-xs text-white line-clamp-1">${item.name}</h4>
                                <p class="text-[10px] text-slate-400 mt-0.5">⭐ ${item.vote_average ? item.vote_average.toFixed(1) : 'N/A'}</p>
                            </div>
                        </div>`;
                    });
                    document.getElementById('home-series-grid').innerHTML = hS;
                }
            } catch(e) {}
        }

        async function pesquisarTMDB() {
            const q = document.getElementById('search-input').value.trim();
            const tipo = document.getElementById('search-type').value;
            if(!q) return;

            document.getElementById('home-dashboard').classList.add('hidden');
            const elRes = document.getElementById('results-tmdb');
            elRes.classList.remove('hidden');
            elRes.innerHTML = '<div class="col-span-4 text-center py-12 text-sky-400 font-bold"><i class="fa-solid fa-spinner fa-spin text-2xl mb-2 block"></i>Consultando TMDB...</div>';

            const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&tipo=${tipo}`).then(r => r.json());

            let html = '';
            res.forEach(item => {
                const tit = item.title || item.name;
                const ano = (item.release_date || item.first_air_date || '0000').substring(0,4);
                const poster = item.poster_path ? `https://image.tmdb.org/t/p/w500${item.poster_path}` : 'https://via.placeholder.com/300x450?text=Sem+Capa';
                
                let badge = '';
                if(item.status_local === 'COMPLETED') {
                    badge = '<span class="absolute top-3 right-3 bg-emerald-500 text-slate-950 font-black text-[10px] px-2.5 py-1 rounded-full shadow-lg"><i class="fa-solid fa-check mr-1"></i>BAIXADO</span>';
                } else if(item.status_local) {
                    badge = '<span class="absolute top-3 right-3 bg-sky-500 text-slate-950 font-black text-[10px] px-2.5 py-1 rounded-full shadow-lg"><i class="fa-solid fa-clock mr-1"></i>NA FILA</span>';
                }

                html += `
                <div onclick="abrirDetalhes('${item.id}', '${tipo}')" class="glass-card rounded-2xl overflow-hidden shadow-xl flex flex-col justify-between cursor-pointer relative group">
                    ${badge}
                    <img src="${poster}" class="w-full h-72 object-cover group-hover:scale-105 transition duration-300">
                    <div class="p-4 flex-1 flex flex-col justify-between bg-slate-950/80">
                        <div>
                            <h4 class="font-bold text-sm text-white line-clamp-1 group-hover:text-sky-400 transition">${tit}</h4>
                            <p class="text-xs text-slate-400 mt-1">${ano} • ⭐ ${item.vote_average ? item.vote_average.toFixed(1) : 'N/A'}</p>
                        </div>
                        <button class="w-full bg-slate-800 hover:bg-sky-600 text-xs font-bold py-2.5 rounded-xl transition mt-3 border border-slate-700/60"><i class="fa-solid fa-list mr-1.5"></i>Ver Detalhes</button>
                    </div>
                </div>`;
            });
            elRes.innerHTML = html || '<p class="col-span-4 text-center text-slate-400 py-8">Nenhum resultado encontrado.</p>';
        }

        async function abrirDetalhes(tmdbId, tipo) {
            tab('detail');
            document.getElementById('detail-header').innerHTML = '<div class="w-full text-center py-8 text-sky-400 font-bold"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Carregando detalhes...</div>';
            document.getElementById('detail-tv-content').classList.add('hidden');
            document.getElementById('detail-movie-content').classList.add('hidden');

            const media = await fetch(`/api/details?tmdb_id=${tmdbId}&tipo=${tipo}`).then(r => r.json());
            currentDetailMedia = { tmdbId, tipo, info: media };

            const tit = media.title || media.name;
            const ano = (media.release_date || media.first_air_date || '0000').substring(0,4);
            const poster = media.poster_path ? `https://image.tmdb.org/t/p/w500${media.poster_path}` : 'https://via.placeholder.com/300x450?text=Sem+Capa';
            
            let statusBadge = '';
            if(media.status_local === 'COMPLETED') {
                statusBadge = '<span class="bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 px-3 py-1 rounded-full text-xs font-bold"><i class="fa-solid fa-circle-check mr-1"></i>Já Disponível no Jellyfin</span>';
            } else if(media.status_local) {
                statusBadge = '<span class="bg-sky-500/20 text-sky-400 border border-sky-500/40 px-3 py-1 rounded-full text-xs font-bold"><i class="fa-solid fa-clock mr-1"></i>Em Processamento / Fila</span>';
            }

            document.getElementById('detail-header').innerHTML = `
                <img src="${poster}" class="w-44 rounded-2xl shadow-2xl object-cover self-center md:self-start">
                <div class="space-y-3 flex-1">
                    <div class="flex flex-wrap items-center gap-2">
                        <h2 class="text-2xl font-black text-white">${tit}</h2>
                        <span class="text-sm font-bold text-slate-400">(${ano})</span>${statusBadge}
                    </div>
                    <p class="text-xs text-amber-400 font-bold">⭐ ${media.vote_average ? media.vote_average.toFixed(1) : 'N/A'} / 10</p>
                    <p class="text-xs text-slate-300 leading-relaxed">${media.overview || 'Sem sinopse disponível.'}</p>
                </div>
            `;

            if(tipo === 'tv') {
                document.getElementById('detail-tv-content').classList.remove('hidden');
                carregarTemporadas(media.seasons || []);
            } else {
                document.getElementById('detail-movie-content').classList.remove('hidden');
                carregarFontesFilme(tmdbId);
            }
        }

        function carregarTemporadas(seasons) {
            let tabsHtml = '';
            const validSeasons = seasons.filter(s => s.season_number > 0);
            
            validSeasons.forEach((s, idx) => {
                const active = idx === 0 ? 'bg-sky-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700';
                tabsHtml += `<button onclick="selecionarTemporada(${s.season_number}, this)" class="season-btn ${active} px-4 py-2 rounded-xl font-bold text-xs whitespace-nowrap transition">Temporada ${s.season_number}</button>`;
            });

            document.getElementById('season-tabs').innerHTML = tabsHtml || '<p class="text-slate-400 text-xs">Nenhuma temporada encontrada.</p>';
            if(validSeasons.length > 0) {
                carregarEpisodios(validSeasons[0].season_number);
            }
        }

        async function selecionarTemporada(seasonNum, btn) {
            document.querySelectorAll('.season-btn').forEach(b => b.className = 'season-btn bg-slate-800 text-slate-400 hover:bg-slate-700 px-4 py-2 rounded-xl font-bold text-xs whitespace-nowrap transition');
            btn.className = 'season-btn bg-sky-600 text-white px-4 py-2 rounded-xl font-bold text-xs whitespace-nowrap transition';
            carregarEpisodios(seasonNum);
        }

        async function carregarEpisodios(seasonNum) {
            document.getElementById('episodes-grid').innerHTML = '<div class="col-span-2 text-center py-8 text-sky-400 font-bold"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Carregando episódios...</div>';
            
            const episodes = await fetch(`/api/episodes?tmdb_id=${currentDetailMedia.tmdbId}&season=${seasonNum}`).then(r => r.json());

            let html = '';
            episodes.forEach(ep => {
                let statusTag = '<span class="text-xs bg-slate-800 text-slate-400 px-2.5 py-1 rounded-lg font-bold"><i class="fa-solid fa-download mr-1"></i>Baixar</span>';
                if(ep.status_local === 'COMPLETED') {
                    statusTag = '<span class="text-xs bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-2.5 py-1 rounded-lg font-bold"><i class="fa-solid fa-check mr-1"></i>Já Baixado</span>';
                } else if(ep.status_local) {
                    statusTag = '<span class="text-xs bg-sky-500/20 text-sky-400 border border-sky-500/30 px-2.5 py-1 rounded-lg font-bold"><i class="fa-solid fa-clock mr-1"></i>Na Fila</span>';
                }

                html += `
                <div class="glass p-4 rounded-2xl flex flex-col justify-between space-y-3 hover:border-sky-500/40 transition">
                    <div class="flex justify-between items-start gap-2">
                        <div>
                            <span class="text-xs font-black text-sky-400">EP ${ep.episode_number}</span>
                            <h4 class="font-bold text-white text-sm line-clamp-1">${ep.name}</h4>
                        </div>
                        ${statusTag}
                    </div>
                    <p class="text-xs text-slate-400 line-clamp-2">${ep.overview || 'Sem descrição.'}</p>
                    <button onclick="abrirModalFontesEp(${seasonNum}, ${ep.episode_number}, '${ep.name.replace(/'/g,"")}')" class="w-full bg-slate-800 hover:bg-sky-600 text-xs font-bold py-2 rounded-xl transition text-slate-200 border border-slate-700/60"><i class="fa-solid fa-magnifying-glass mr-1"></i>Opções de Download</button>
                </div>`;
            });

            document.getElementById('episodes-grid').innerHTML = html || '<p class="col-span-2 text-center text-slate-400">Nenhum episódio retornado.</p>';
        }

        async function abrirModalFontesEp(season, ep, epName) {
            currentSelectedEp = { season, ep, epName };
            document.getElementById('ep-sources-modal').classList.remove('hidden');
            document.getElementById('ep-sources-title').innerText = `Opções para: S${String(season).padStart(2,'0')}E${String(ep).padStart(2,'0')} —${epName}`;
            document.getElementById('ep-sources-list').innerHTML = '<div class="text-center py-6 text-sky-400 font-bold"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Pesquisando fontes...</div>';

            const data = await fetch(`/api/sources?tmdb_id=${currentDetailMedia.tmdbId}&tipo=tv&season=${season}&episode=${ep}`).then(r => r.json());

            let html = '';
            (data.opcoes || []).forEach(opt => {
                html += `
                <div class="flex justify-between items-center bg-slate-900/90 p-3 rounded-2xl border border-slate-800 hover:border-sky-500/50">
                    <div>
                        <span class="text-xs font-black text-sky-400">[${opt.indexador}]</span>
                        <span class="text-xs font-bold text-white ml-2">${opt.qualidade}</span>
                    </div>
                    <button onclick="adicionarEpAFila('${opt.url}', '${opt.qualidade.replace(/'/g,"")}')" class="bg-emerald-600 hover:bg-emerald-500 text-xs font-bold px-4 py-2 rounded-xl transition shadow-lg shadow-emerald-600/20"><i class="fa-solid fa-plus mr-1"></i>Adicionar à Fila</button>
                </div>`;
            });

            document.getElementById('ep-sources-list').innerHTML = html || '<p class="text-xs text-amber-400 p-2 text-center">Nenhuma fonte automática encontrada. Cole um link direto abaixo.</p>';
        }

        function fecharModalEp() {
            document.getElementById('ep-sources-modal').classList.add('hidden');
        }

        async function adicionarEpAFila(url, qualidade) {
            const res = await fetch('/api/queue/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    tipo: 'series',
                    tmdb_info: currentDetailMedia.info,
                    qualidade: qualidade,
                    url_fonte: url,
                    season: currentSelectedEp.season,
                    episode: currentSelectedEp.ep
                })
            }).then(r => r.json());

            showToast(res.message || 'Episódio adicionado à fila!', 'success');
            fecharModalEp();
        }

        async function adicionarManualEp() {
            const url = document.getElementById('ep-manual-url').value.trim();
            if(!url) return showToast('Digite uma URL válida!', 'warning');
            adicionarEpAFila(url, 'LINK MANUAL 🔗');
        }

        async function carregarFontesFilme(tmdbId) {
            document.getElementById('movie-sources-list').innerHTML = '<div class="text-center py-6 text-sky-400 font-bold"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Buscando fontes do filme...</div>';
            
            const data = await fetch(`/api/sources?tmdb_id=${tmdbId}&tipo=movie`).then(r => r.json());

            let html = '';
            (data.opcoes || []).forEach(opt => {
                html += `
                <div class="flex justify-between items-center bg-slate-900/90 p-4 rounded-2xl border border-slate-800 hover:border-sky-500/50">
                    <div>
                        <span class="text-xs font-black text-sky-400">[${opt.indexador}]</span>
                        <span class="text-sm font-bold text-white ml-2">${opt.qualidade}</span>
                    </div>
                    <button onclick="adicionarFilmeAFila('${opt.url}', '${opt.qualidade.replace(/'/g,"")}')" class="bg-emerald-600 hover:bg-emerald-500 text-xs font-bold px-5 py-2.5 rounded-xl transition shadow-lg shadow-emerald-600/20"><i class="fa-solid fa-plus mr-1"></i>Adicionar à Fila</button>
                </div>`;
            });

            document.getElementById('movie-sources-list').innerHTML = html || '<p class="text-xs text-amber-400 p-2">Nenhuma fonte encontrada automaticamente nos indexadores.</p>';
        }

        async function adicionarFilmeAFila(url, qualidade) {
            const res = await fetch('/api/queue/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    tipo: 'movie',
                    tmdb_info: currentDetailMedia.info,
                    qualidade: qualidade,
                    url_fonte: url
                })
            }).then(r => r.json());

            showToast(res.message || 'Filme adicionado à fila!', 'success');
        }

        async function alternarPausaFila() {
            const res = await fetch('/api/queue/toggle_pause', { method: 'POST' }).then(r => r.json());
            atualizarFilaEBiblioteca();
        }

        async function atualizarFilaEBiblioteca() {
            const data = await fetch('/api/queue').then(r => r.json());
            isQueuePaused = data.fila_pausada;

            const badge = document.getElementById('queue-status-badge');
            const btnPause = document.getElementById('btn-toggle-pause');

            if(isQueuePaused) {
                badge.className = 'text-xs px-3 py-1 rounded-full font-bold bg-amber-500/20 text-amber-400 border border-amber-500/30';
                badge.innerHTML = '<i class="fa-solid fa-pause mr-1"></i>FILA PAUSADA';
                btnPause.className = 'px-5 py-2.5 rounded-xl font-extrabold text-xs transition flex items-center gap-2 shadow-lg bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/30';
                btnPause.innerHTML = '<i class="fa-solid fa-play"></i> Iniciar Fila';
            } else {
                badge.className = 'text-xs px-3 py-1 rounded-full font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
                badge.innerHTML = '<i class="fa-solid fa-play mr-1"></i>FILA ATIVA';
                btnPause.className = 'px-5 py-2.5 rounded-xl font-extrabold text-xs transition flex items-center gap-2 shadow-lg bg-amber-600 hover:bg-amber-500 text-white shadow-amber-600/30';
                btnPause.innerHTML = '<i class="fa-solid fa-pause"></i> Pausar Fila';
            }

            let qHtml = '';
            (data.fila || []).forEach(item => {
                let badgeItem = 'bg-slate-800 text-slate-300';
                let ico = 'fa-clock';
                if(item.status === 'PROCESSING') { badgeItem = 'bg-blue-600/30 border border-blue-500/50 text-blue-400 animate-pulse'; ico = 'fa-spinner fa-spin'; }
                if(item.status === 'COMPLETED') { badgeItem = 'bg-emerald-500/20 border border-emerald-500/40 text-emerald-400'; ico = 'fa-circle-check'; }
                if(item.status === 'FAILED') { badgeItem = 'bg-rose-500/20 border border-rose-500/40 text-rose-400'; ico = 'fa-triangle-exclamation'; }

                let progText = (item.progresso_texto || '').replace(/\\n/g, '<br>').replace(/`/g, '');

                qHtml += `
                <div class="glass p-5 rounded-2xl flex flex-col justify-between gap-3 border border-slate-700/50">
                    <div class="flex justify-between items-start">
                        <div>
                            <div class="flex items-center space-x-2">
                                <span class="font-extrabold text-white text-base">${item.titulo}</span>
                                <span class="text-xs px-3 py-1 rounded-full ${badgeItem} font-bold flex items-center gap-1"><i class="fa-solid ${ico}"></i>${item.status}</span>
                            </div>
                            <p class="text-xs text-sky-400 font-bold mt-1">${item.qualidade}</p>
                        </div>
                        <div class="flex space-x-2">
                            ${item.status === 'FAILED' ? `<button onclick="retryFila('${item.id}')" class="bg-amber-600 hover:bg-amber-500 text-xs font-bold px-3 py-1.5 rounded-xl"><i class="fa-solid fa-rotate-right mr-1"></i>Reenviar</button>` : ''}
                            <button onclick="deletarFila('${item.id}')" class="bg-slate-800 hover:bg-rose-900 text-xs text-slate-400 hover:text-rose-200 px-3 py-1.5 rounded-xl border border-slate-700/60"><i class="fa-solid fa-trash"></i></button>
                        </div>
                    </div>
                    <div class="bg-slate-950/80 p-3 rounded-xl border border-slate-800/80">
                        <p class="text-xs text-slate-300 font-mono leading-relaxed">${progText}</p>
                    </div>
                    ${item.erro_msg ? `<p class="text-xs text-rose-400 bg-rose-950/60 p-3 rounded-xl border border-rose-900/50">${item.erro_msg}</p>` : ''}
                </div>`;
            });
            document.getElementById('queue-list').innerHTML = qHtml || '<p class="text-slate-400 text-sm py-4">A fila de espera está vazia.</p>';

            let lHtml = '';
            (data.salvos || []).forEach(m => {
                lHtml += `
                <div class="glass p-4 rounded-2xl flex justify-between items-center border border-slate-800">
                    <div>
                        <h4 class="font-bold text-white text-sm">${m.titulo} (${m.ano})</h4>
                        <p class="text-xs text-sky-400 font-semibold mt-0.5">${m.qualidade}</p>
                    </div>
                    <button onclick="deletarMidia('${m.media_id}')" class="text-slate-500 hover:text-rose-400 p-2 transition text-sm"><i class="fa-solid fa-trash"></i></button>
                </div>`;
            });
            document.getElementById('library-list').innerHTML = lHtml || '<p class="text-slate-400 text-sm py-4">Nenhuma mídia ativa no Jellyfin.</p>';
        }

        async function retryFila(id) {
            await fetch('/api/queue/retry', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}) });
            atualizarFilaEBiblioteca();
        }

        async function deletarFila(id) {
            await fetch('/api/queue/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}) });
            atualizarFilaEBiblioteca();
        }

        async function deletarMidia(media_id) {
            if(!confirm('Tem certeza que deseja apagar esta mídia do Jellyfin?')) return;
            await fetch('/api/queue/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({media_id}) });
            atualizarFilaEBiblioteca();
        }

        document.addEventListener('DOMContentLoaded', () => {
            carregarDashboardHome();
            monitorarNotificacoesFila();
        });

        setInterval(monitorarNotificacoesFila, 3000);

        setInterval(() => {
            if(!document.getElementById('sec-queue').classList.contains('hidden')) {
                atualizarFilaEBiblioteca();
            }
        }, 2000);
    </script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")

async def iniciar_servidor():
    await client.start(bot_token=Config.TELEGRAM_TOKEN)
    logger.info("🤖 Sessão do Telethon iniciada no servidor HTTP.")

    asyncio.create_task(worker_fila_processamento())

    app = web.Application()
    
    app.router.add_route("*", "/stream/{media_id}", handle_stream)
    app.router.add_get("/web", handle_web_app)
    
    app.router.add_get("/api/search", api_search)
    app.router.add_get("/api/details", api_details)
    app.router.add_get("/api/episodes", api_episodes)
    app.router.add_get("/api/sources", api_sources)
    app.router.add_post("/api/queue/add", api_add_queue)
    app.router.add_post("/api/queue/toggle_pause", api_toggle_queue_pause)
    app.router.add_get("/api/queue", api_get_queue)
    app.router.add_post("/api/queue/retry", api_retry_queue)
    app.router.add_post("/api/queue/delete", api_delete_queue)
    
    app.router.add_get("/api/catalog/movies", api_catalog_movies)
    app.router.add_get("/api/catalog/series", api_catalog_series)
    app.router.add_get("/api/series/continue", api_series_continue)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", Config.PORTA_STREAM_LOCAL)
    await site.start()
    logger.info(f"🌐 Servidor de Streaming rodando na porta {Config.PORTA_STREAM_LOCAL}")
    logger.info(f"🌐 Painel Web do JellyBot disponível em http://{Config.IP_SERVIDOR}:{Config.PORTA_STREAM_LOCAL}/web")