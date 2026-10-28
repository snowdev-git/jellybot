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
    obter_midia, listar_midias, remover_midia, adicionar_a_fila,
    obter_proximo_fila, atualizar_status_fila, listar_fila,
    remover_item_fila, resetar_item_fila, obter_mapa_status_midias
)
from downloader import buscar_fontes_indexadores, processar_e_hospedar_midia

FILA_PAUSADA = False

def e_fonte_dublada(qualidade_str: str) -> bool:
    q = str(qualidade_str).upper()
    return "DUBLADO" in q and "LEGENDADO" not in q

async def notificar_telegram_admin(texto: str, caminho_file: str = None):
    if Config.ADMIN_ID and Config.ADMIN_ID != 0:
        try:
            if caminho_file and os.path.exists(caminho_file):
                await client.send_file(Config.ADMIN_ID, caminho_file, caption=texto, parse_mode='md')
            else:
                await client.send_message(Config.ADMIN_ID, texto, parse_mode='md')
        except Exception as e:
            logger.warning(f"⚠️ Não foi possível enviar notificação: {e}")

async def worker_fila_processamento():
    global FILA_PAUSADA
    logger.info("⚙️ Worker de Fila iniciado...")
    while True:
        try:
            if not FILA_PAUSADA:
                item = obter_proximo_fila()
                if item:
                    item_id, titulo, qualidade, url_fonte = item['id'], item['titulo'], item['qualidade'], item['url_fonte']
                    tipo, tmdb_info = item['tipo'], item['tmdb_info']

                    if url_fonte == "AUTO_SEARCH_DUBLADO" or not url_fonte:
                        atualizar_status_fila(item_id, 'PROCESSING', '🔍 Buscando fonte DUBLADA atualizada...')
                        tmdb_id = tmdb_info.get('id')
                        season_num = tmdb_info.get('season_number', 1)
                        ep_num = tmdb_info.get('episode_number', 1)

                        url_tmdb = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR&append_to_response=external_ids"
                        try:
                            res_tv = requests.get(url_tmdb, timeout=10).json()
                            imdb_id = res_tv.get('external_ids', {}).get('imdb_id')
                        except Exception:
                            imdb_id = None

                        if not imdb_id:
                            atualizar_status_fila(item_id, 'FAILED', '❌ IMDB ID não encontrado', erro_msg='Sem IMDB ID')
                            await asyncio.sleep(2)
                            continue

                        opcoes = await buscar_fontes_indexadores("series", imdb_id, int(season_num), int(ep_num))
                        dublados = [opt for opt in opcoes if e_fonte_dublada(opt.get('qualidade', ''))]

                        if not dublados:
                            atualizar_status_fila(item_id, 'FAILED', '❌ Nenhuma fonte DUBLADA encontrada', erro_msg='Sem links dublados')
                            await asyncio.sleep(2)
                            continue

                        url_fonte = dublados[0]['url']
                        qualidade = dublados[0]['qualidade']

                    await notificar_telegram_admin(f"⏳ **Processamento Iniciado:**\n🎬 `{titulo}`\n🎧 Qualidade: `{qualidade}`")
                    atualizar_status_fila(item_id, 'PROCESSING', '⏳ A verificar fonte de vídeo...')

                    sucesso, msg_res, caminho_strm = await processar_e_hospedar_midia(
                        url_fonte=url_fonte, tipo=tipo, tmdb_info=tmdb_info, qualidade=qualidade,
                        progress_callback=lambda st: atualizar_status_fila(item_id, 'PROCESSING', str(st))
                    )

                    if sucesso:
                        atualizar_status_fila(item_id, 'COMPLETED', '✅ Processado com sucesso!')
                        await notificar_telegram_admin(f"🎉 **Concluído:** `{titulo}`\n📂 `{caminho_strm}`", caminho_file=caminho_strm)
                    else:
                        atualizar_status_fila(item_id, 'FAILED', '❌ Falha ao processar', erro_msg=str(msg_res))
                        await notificar_telegram_admin(f"❌ **Falha:** `{titulo}`\n⚠️ `{msg_res}`")
            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"⚠️ Erro no worker: {e}")
            await asyncio.sleep(5)

async def handle_stream(request):
    media_id = request.match_info.get("media_id")
    midia = obter_midia(media_id)
    if not midia:
        msg_ids_raw = media_id if media_id and media_id.isdigit() else None
        if not msg_ids_raw: return web.Response(status=404, text="Mídia não encontrada.")
    else:
        msg_ids_raw = str(midia["msg_id_telegram"])

    msg_ids = [int(m.strip()) for m in msg_ids_raw.split(",") if m.strip().isdigit()]
    if not msg_ids: return web.Response(status=404, text="Sem IDs.")

    try:
        mensagens = await client.get_messages(Config.CANAL_STORAGE_ID, ids=msg_ids)
        mensagens = [m for m in (mensagens if isinstance(mensagens, list) else [mensagens]) if m and m.media]
        if not mensagens: return web.Response(status=404, text="Arquivos não encontrados.")

        tamanho_total = sum(m.file.size for m in mensagens)
        start, end, status_code = 0, tamanho_total - 1, 200

        range_header = request.headers.get("Range")
        if range_header:
            status_code = 206
            range_val = range_header.replace("bytes=", "").strip()
            if range_val.startswith("-"):
                start = max(0, tamanho_total - int(range_val[1:]))
            elif "-" in range_val:
                parts = range_val.split("-")
                start = int(parts[0]) if parts[0].isdigit() else start
                end = int(parts[1]) if parts[1].isdigit() else end

        end = min(end, tamanho_total - 1)
        start = max(0, min(start, end))

        headers = {
            "Content-Type": "application/octet-stream",
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{tamanho_total}",
            "Content-Length": str(end - start + 1),
            "Access-Control-Allow-Origin": "*"
        }

        if request.method == "HEAD": return web.Response(status=status_code, headers=headers)
        response = web.StreamResponse(status=status_code, headers=headers)
        await response.prepare(request)

        offset_atual = 0
        for msg in mensagens:
            tamanho_parte = msg.file.size
            if start <= offset_atual + tamanho_parte - 1 and end >= offset_atual:
                req_start = max(0, start - offset_atual)
                req_end = min(tamanho_parte - 1, end - offset_atual)
                async for chunk in client.iter_download(msg.media, offset=req_start, request_size=512*1024):
                    if request.transport.is_closing(): break
                    bytes_a_enviar = chunk[:min(len(chunk), req_end - req_start + 1)]
                    await response.write(bytes_a_enviar)
                    req_start += len(bytes_a_enviar)
                    if req_start > req_end: break
            offset_atual += tamanho_parte
            if offset_atual > end: break

        await response.write_eof()
        return response
    except Exception as e:
        return web.Response(status=500, text=f"Erro: {e}")

async def api_search(request):
    q = request.query.get('q', '').strip()
    tipo = request.query.get('tipo', 'movie')
    if not q: return web.json_response([])
    try:
        res = requests.get(f"https://api.themoviedb.org/3/search/{tipo}?api_key={Config.TMDB_API_KEY}&query={q}&language=pt-BR", timeout=10).json().get("results", [])[:12]
        mapa = obter_mapa_status_midias()
        for item in res:
            if tipo == 'movie':
                nome_limpo = re.sub(r'[\\/*?:"<>|]', "", str(item.get("title", "Filme"))).strip()
                ano = str(item.get("release_date", "0000"))[:4]
                item['status_local'] = mapa.get(f"movie_{item.get('id')}_{re.sub(r'\W+', '_', nome_limpo).lower()}_{ano}")
            else:
                item['status_local'] = mapa.get(f"tv_show_{item.get('id')}")
        return web.json_response(res)
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def api_details(request):
    tmdb_id, tipo = request.query.get('tmdb_id'), request.query.get('tipo', 'movie')
    try:
        res = requests.get(f"https://api.themoviedb.org/3/{tipo}/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR&append_to_response=external_ids", timeout=10).json()
        if tipo == 'movie':
            nome_limpo = re.sub(r'[\\/*?:"<>|]', "", str(res.get("title", "Filme"))).strip()
            ano = str(res.get("release_date", "0000"))[:4]
            res['status_local'] = obter_mapa_status_midias().get(f"movie_{res.get('id')}_{re.sub(r'\W+', '_', nome_limpo).lower()}_{ano}")
        return web.json_response(res)
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def api_episodes(request):
    tmdb_id, season = request.query.get('tmdb_id'), request.query.get('season', '1')
    try:
        episodes = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}?api_key={Config.TMDB_API_KEY}&language=pt-BR", timeout=10).json().get('episodes', [])
        mapa = obter_mapa_status_midias()
        for ep in episodes:
            ep['status_local'] = mapa.get(f"tv_{tmdb_id}_s{int(season):02d}e{int(ep.get('episode_number', 1)):02d}")
        return web.json_response(episodes)
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def api_sources(request):
    tmdb_id, tipo, season, episode = request.query.get('tmdb_id'), request.query.get('tipo', 'movie'), request.query.get('season'), request.query.get('episode')
    try:
        res_tmdb = requests.get(f"https://api.themoviedb.org/3/{tipo}/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR&append_to_response=external_ids", timeout=10).json()
        imdb_id = res_tmdb.get('external_ids', {}).get('imdb_id')
        if not imdb_id: return web.json_response({"error": "IMDB ID não encontrado."}, status=404)
        
        opcoes = await buscar_fontes_indexadores("movie", imdb_id) if tipo == 'movie' else await buscar_fontes_indexadores("series", imdb_id, int(season), int(episode))
        dublados = [o for o in opcoes if e_fonte_dublada(o.get('qualidade', ''))]
        return web.json_response({"tmdb_info": res_tmdb, "opcoes": dublados + [o for o in opcoes if o not in dublados]})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def api_add_queue(request):
    data = await request.json()
    tipo, tmdb_info, qualidade, url_fonte = data.get("tipo"), data.get("tmdb_info"), data.get("qualidade"), data.get("url_fonte")

    if tipo == "movie":
        nome_limpo = re.sub(r'[\\/*?:"<>|]', "", str(tmdb_info.get("title", "Filme"))).strip()
        ano = str(tmdb_info.get("release_date", "0000"))[:4]
        media_id = f"movie_{tmdb_info.get('id')}_{re.sub(r'\W+', '_', nome_limpo).lower()}_{ano}"
        titulo = f"{nome_limpo} ({ano})"
    else:
        nome_limpo = re.sub(r'[\\/*?:"<>|]', "", str(tmdb_info.get("name", "Serie"))).strip()
        s_num, ep_num = int(data.get("season", 1)), int(data.get("episode", 1))
        tmdb_info['season_number'], tmdb_info['episode_number'] = s_num, ep_num
        media_id = f"tv_{tmdb_info.get('id')}_s{s_num:02d}e{ep_num:02d}"
        titulo = f"{nome_limpo} S{s_num:02d}E{ep_num:02d}"
        ano = str(tmdb_info.get("first_air_date", "0000"))[:4]

    adicionar_a_fila(media_id, tipo, tmdb_info.get('id'), titulo, ano, qualidade, url_fonte, tmdb_info)
    return web.json_response({"success": True, "message": f"{titulo} adicionado à fila!"})

async def api_add_season_queue(request):
    try:
        data = await request.json()
        tmdb_id, season_num = data.get("tmdb_id"), int(data.get("season", 1))
        res_tv = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR", timeout=10).json()
        episodes = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season_num}?api_key={Config.TMDB_API_KEY}&language=pt-BR", timeout=10).json().get('episodes', [])
        
        if not episodes: return web.json_response({"success": False, "message": "Nenhum episódio."}, status=404)
        mapa_status = obter_mapa_status_midias()
        adicionados, ja_existentes = 0, 0
        nome_limpo = re.sub(r'[\\/*?:"<>|]', "", str(res_tv.get("name", "Serie"))).strip()
        ano = str(res_tv.get("first_air_date", "0000"))[:4]

        for ep in episodes:
            ep_num = int(ep.get('episode_number', 1))
            media_id = f"tv_{tmdb_id}_s{season_num:02d}e{ep_num:02d}"
            if mapa_status.get(media_id) in ['COMPLETED', 'PROCESSING', 'PENDING']:
                ja_existentes += 1
                continue

            tmdb_info_ep = dict(res_tv)
            tmdb_info_ep.update({'season_number': season_num, 'episode_number': ep_num})
            adicionar_a_fila(media_id, "series", tmdb_id, f"{nome_limpo} S{season_num:02d}E{ep_num:02d}", ano, "DUBLADO (Busca Automática)", "AUTO_SEARCH_DUBLADO", tmdb_info_ep)
            adicionados += 1

        return web.json_response({"success": True, "adicionados": adicionados, "ja_existentes": ja_existentes, "message": f"Temporada {season_num}: {adicionados} adicionados!"})
    except Exception as e: return web.json_response({"success": False, "message": str(e)}, status=500)

async def api_toggle_queue_pause(request):
    global FILA_PAUSADA
    FILA_PAUSADA = not FILA_PAUSADA
    return web.json_response({"success": True, "fila_pausada": FILA_PAUSADA})

async def api_get_queue(request):
    return web.json_response({"fila": listar_fila(), "salvos": listar_midias(), "fila_pausada": FILA_PAUSADA})

async def api_retry_queue(request):
    resetar_item_fila((await request.json()).get("id"))
    return web.json_response({"success": True})

async def api_delete_queue(request):
    data = await request.json()
    if data.get("id"): remover_item_fila(data.get("id"))
    if data.get("media_id"): remover_midia(data.get("media_id"))
    return web.json_response({"success": True})

async def api_catalog_movies(request):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.themoviedb.org/3/movie/now_playing?api_key={Config.TMDB_API_KEY}&language=pt-BR&page=1", timeout=10) as resp:
                if resp.status == 200: return web.json_response({"status": "success", "results": (await resp.json()).get("results", [])})
                return web.json_response({"status": "error", "results": []}, status=resp.status)
    except Exception as e: return web.json_response({"status": "error", "message": str(e)}, status=500)

async def api_catalog_series(request):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.themoviedb.org/3/tv/on_the_air?api_key={Config.TMDB_API_KEY}&language=pt-BR&page=1", timeout=10) as resp:
                if resp.status == 200: return web.json_response({"status": "success", "results": (await resp.json()).get("results", [])})
                return web.json_response({"status": "error", "results": []}, status=resp.status)
    except Exception as e: return web.json_response({"status": "error", "message": str(e)}, status=500)

async def api_series_continue(request):
    series_map = {}
    for item in listar_midias("series"):
        match = re.match(r"tv_(\d+)_s(\d+)e(\d+)", item.get("media_id", ""))
        if match:
            tmdb_id, s_num, ep_num = match.groups()
            s_num, ep_num = int(s_num), int(ep_num)
            if tmdb_id not in series_map or (s_num > series_map[tmdb_id]["ultimo_s"]) or (s_num == series_map[tmdb_id]["ultimo_s"] and ep_num > series_map[tmdb_id]["ultimo_ep"]):
                series_map[tmdb_id] = {"tmdb_id": tmdb_id, "titulo": item.get("titulo", "").split(" S")[0], "ultimo_s": s_num, "ultimo_ep": ep_num}

    resultado = []
    async with aiohttp.ClientSession() as session:
        for tmdb_id, info in series_map.items():
            try:
                async with session.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR", timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        info.update({"poster_path": data.get("poster_path"), "nome": data.get("name", info["titulo"]), "vote_average": data.get("vote_average", 0)})
            except Exception: pass
            resultado.append(info)
    return web.json_response({"status": "success", "results": resultado})

async def handle_web_app(request):
    html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JellyBot — Cinema & Automação</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #0b1329; color: #f8fafc; font-family: system-ui, sans-serif; }
        .glass { background: rgba(23, 32, 54, 0.85); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.08); }
        .glass-card { background: rgba(15, 23, 42, 0.75); border: 1px solid rgba(255,255,255,0.06); }
        .glass-card:hover { border-color: rgba(56, 189, 248, 0.4); transform: translateY(-2px); transition: all 0.2s ease; }
    </style>
</head>
<body class="min-h-screen pb-16">
    <nav class="glass sticky top-0 z-50 px-6 py-4 mb-8 flex flex-col md:flex-row justify-between items-center shadow-2xl gap-4">
        <div class="flex items-center space-x-3 cursor-pointer" onclick="voltarParaPesquisa()">
            <span class="text-3xl">🍿</span><span class="text-xl font-black bg-gradient-to-r from-sky-400 via-blue-500 to-indigo-500 bg-clip-text text-transparent">JellyBot Web</span>
        </div>
        <div class="flex flex-wrap justify-center space-x-2">
            <button onclick="tab('search')" id="tab-search" class="px-4 py-2 rounded-xl font-bold transition bg-blue-600 text-white shadow-lg"><i class="fa-solid fa-house mr-2"></i>Início</button>
            <button onclick="tab('queue')" id="tab-queue" class="px-4 py-2 rounded-xl font-bold transition bg-slate-800 hover:bg-slate-700 text-slate-300"><i class="fa-solid fa-list-check mr-2"></i>Fila</button>
            <button onclick="tab('library')" id="tab-library" class="px-4 py-2 rounded-xl font-bold transition bg-slate-800 hover:bg-slate-700 text-slate-300"><i class="fa-solid fa-film mr-2"></i>Biblioteca</button>
        </div>
    </nav>
    <div class="max-w-6xl mx-auto px-4">
        <div id="sec-search" class="space-y-8">
            <div class="glass p-6 rounded-2xl shadow-xl flex flex-col md:flex-row gap-4">
                <select id="search-type" class="bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 font-bold text-sky-400 focus:outline-none">
                    <option value="movie">🎬 Filme</option><option value="tv">📺 Série</option>
                </select>
                <input type="text" id="search-input" placeholder="Digite o nome..." class="flex-1 bg-slate-900 border border-slate-700 rounded-xl px-5 py-3 text-white focus:outline-none" oninput="checarLimpezaBusca()" onkeypress="if(event.key==='Enter') pesquisarTMDB()">
                <button onclick="pesquisarTMDB()" class="bg-blue-600 hover:bg-blue-500 font-extrabold px-8 py-3 rounded-xl transition shadow-lg"><i class="fa-solid fa-search mr-2"></i>Buscar</button>
            </div>
            <div id="home-dashboard" class="space-y-8">
                <div id="container-continue-series" class="hidden space-y-4"><h3 class="text-xl font-extrabold text-emerald-400">Continuar</h3><div id="home-continue-grid" class="grid grid-cols-2 md:grid-cols-4 gap-6"></div></div>
                <div class="space-y-4"><h3 class="text-xl font-extrabold text-sky-400">Filmes</h3><div id="home-movies-grid" class="grid grid-cols-2 md:grid-cols-4 gap-6"></div></div>
                <div class="space-y-4"><h3 class="text-xl font-extrabold text-indigo-400">Séries</h3><div id="home-series-grid" class="grid grid-cols-2 md:grid-cols-4 gap-6"></div></div>
            </div>
            <div id="results-tmdb" class="hidden grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6"></div>
        </div>
        <div id="sec-detail" class="hidden space-y-6">
            <button onclick="voltarParaPesquisa()" class="bg-slate-800 hover:bg-slate-700 text-sky-400 font-bold px-4 py-2 rounded-xl text-xs"><i class="fa-solid fa-arrow-left"></i> Voltar</button>
            <div id="detail-header" class="glass p-6 rounded-3xl flex flex-col md:flex-row gap-6 items-start"></div>
            <div id="detail-tv-content" class="hidden space-y-6">
                <div class="flex flex-col sm:flex-row justify-between items-center gap-4 glass p-4 rounded-2xl">
                    <div class="flex items-center space-x-3 overflow-x-auto w-full pb-1" id="season-tabs"></div>
                    <button id="btn-download-season" onclick="baixarTemporadaCompleta()" class="w-full sm:w-auto bg-emerald-600 hover:bg-emerald-500 text-white font-extrabold text-xs px-5 py-3 rounded-xl"><i class="fa-solid fa-bolt text-amber-300"></i> Baixar Temporada</button>
                </div>
                <div id="episodes-grid" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
            </div>
            <div id="detail-movie-content" class="hidden space-y-4 glass p-6 rounded-3xl">
                <h3 class="text-lg font-extrabold text-sky-400"><i class="fa-solid fa-download mr-2"></i>Fontes</h3>
                <div id="movie-sources-list" class="space-y-3"></div>
            </div>
        </div>
        <div id="sec-queue" class="hidden space-y-4">
            <div class="glass p-4 rounded-2xl flex flex-col lg:flex-row justify-between items-center gap-4">
                <div class="flex items-center gap-3"><h2 class="text-xl font-extrabold text-slate-100">Fila</h2><span id="queue-status-badge" class="text-xs px-3 py-1 rounded-full font-bold"></span></div>
                <div class="flex flex-wrap bg-slate-900 p-1.5 rounded-2xl border border-slate-800 gap-1 text-xs font-bold">
                    <button onclick="filtrarFila('active')" id="q-filter-active" class="px-4 py-2 rounded-xl transition bg-sky-600 text-white">⏳ Em Fila</button>
                    <button onclick="filtrarFila('failed')" id="q-filter-failed" class="px-4 py-2 rounded-xl transition text-slate-400">❌ Erros</button>
                    <button onclick="filtrarFila('completed')" id="q-filter-completed" class="px-4 py-2 rounded-xl transition text-slate-400">✅ Concluídos</button>
                </div>
                <button id="btn-toggle-pause" onclick="alternarPausaFila()" class="px-5 py-2.5 rounded-xl font-extrabold text-xs shadow-lg"></button>
            </div>
            <div id="queue-list" class="space-y-4"></div>
        </div>
        <div id="sec-library" class="hidden space-y-6">
            <div class="flex flex-col sm:flex-row justify-between items-center gap-4">
                <h2 class="text-xl font-extrabold text-slate-100">Biblioteca</h2>
                <div class="flex bg-slate-900 p-1.5 rounded-2xl border border-slate-800 gap-1 text-xs font-bold">
                    <button onclick="filtrarBiblioteca('all')" id="lib-filter-all" class="px-4 py-2 rounded-xl transition bg-sky-600 text-white">Todos</button>
                    <button onclick="filtrarBiblioteca('movie')" id="lib-filter-movie" class="px-4 py-2 rounded-xl transition text-slate-400">Filmes</button>
                    <button onclick="filtrarBiblioteca('series')" id="lib-filter-series" class="px-4 py-2 rounded-xl transition text-slate-400">Séries</button>
                </div>
            </div>
            <div id="library-list" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"></div>
        </div>
    </div>
    <div id="ep-sources-modal" class="hidden fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4">
        <div class="glass p-6 rounded-3xl max-w-2xl w-full max-h-[90vh] overflow-y-auto space-y-4">
            <div class="flex justify-between items-center border-b border-slate-700 pb-3">
                <h3 id="ep-sources-title" class="text-lg font-extrabold text-sky-400"></h3>
                <button onclick="document.getElementById('ep-sources-modal').classList.add('hidden')" class="text-white text-2xl">&times;</button>
            </div>
            <div id="ep-sources-list" class="space-y-2"></div>
            <div class="pt-4 border-t border-slate-700">
                <div class="flex gap-2">
                    <input type="text" id="ep-manual-url" placeholder="http://video.mp4" class="flex-1 bg-slate-900 border border-slate-700 rounded-xl px-4 py-2 text-sm text-white">
                    <button onclick="adicionarManualEp()" class="bg-emerald-600 hover:bg-emerald-500 text-xs font-bold px-4 py-2 rounded-xl">➕ Manual</button>
                </div>
            </div>
        </div>
    </div>
    <div id="toast-container" class="fixed bottom-5 right-5 z-50 flex flex-col gap-2 pointer-events-none"></div>
    <script>
        let currentDetailMedia = null, currentSelectedEp = null, currentSelectedSeason = 1;
        let isQueuePaused = false, filtroBibliotecaAtual = 'all', filtroFilaAtual = 'active', lastQueueState = {};

        function showToast(msg, tipo='info') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `pointer-events-auto glass px-4 py-3 rounded-2xl shadow-2xl flex items-center gap-3 text-sm text-white font-bold max-w-md`;
            toast.innerHTML = `<span>${msg}</span>`;
            container.appendChild(toast);
            setTimeout(() => toast.remove(), 4000);
        }

        async function monitorarNotificacoesFila() {
            try {
                const data = await fetch('/api/queue').then(r => r.json());
                (data.fila || []).forEach(item => {
                    const prevStatus = lastQueueState[item.id];
                    if (prevStatus && prevStatus !== item.status) {
                        if (item.status === 'PROCESSING') showToast(`🚀 Iniciado: ${item.titulo}`, 'warning');
                        else if (item.status === 'COMPLETED') showToast(`🎉 Concluído: ${item.titulo}`, 'success');
                        else if (item.status === 'FAILED') showToast(`❌ Falha: ${item.titulo}`, 'error');
                    }
                    lastQueueState[item.id] = item.status;
                });
            } catch (e) {}
        }

        function tab(t) {
            ['search', 'detail', 'queue', 'library'].forEach(s => {
                document.getElementById('sec-' + s).classList.add('hidden');
                document.getElementById('tab-' + s).className = 'px-4 py-2 rounded-xl font-bold bg-slate-800 text-slate-300';
            });
            document.getElementById('sec-' + t).classList.remove('hidden');
            document.getElementById('tab-' + t).className = 'px-4 py-2 rounded-xl font-bold bg-blue-600 text-white';
            if (t === 'search') carregarDashboardHome();
            if (t === 'queue' || t === 'library') atualizarFilaEBiblioteca();
        }

        function voltarParaPesquisa() { tab('search'); checarLimpezaBusca(); }
        function checarLimpezaBusca() {
            if (!document.getElementById('search-input').value.trim()) {
                document.getElementById('results-tmdb').classList.add('hidden');
                document.getElementById('home-dashboard').classList.remove('hidden');
            }
        }

        async function carregarDashboardHome() {
            const resM = await fetch('/api/catalog/movies').then(r => r.json()).catch(()=>({}));
            if (resM.results) {
                let hM = '';
                resM.results.slice(0, 8).forEach(i => {
                    hM += `<div onclick="abrirDetalhes('${i.id}', 'movie')" class="glass-card rounded-2xl overflow-hidden cursor-pointer group">
                        <img src="https://image.tmdb.org/t/p/w500${i.poster_path}" class="w-full h-64 object-cover">
                        <div class="p-3 bg-slate-950/80"><h4 class="font-bold text-xs text-white">${i.title}</h4></div></div>`;
                });
                document.getElementById('home-movies-grid').innerHTML = hM;
            }
            const resS = await fetch('/api/catalog/series').then(r => r.json()).catch(()=>({}));
            if (resS.results) {
                let hS = '';
                resS.results.slice(0, 8).forEach(i => {
                    hS += `<div onclick="abrirDetalhes('${i.id}', 'tv')" class="glass-card rounded-2xl overflow-hidden cursor-pointer group">
                        <img src="https://image.tmdb.org/t/p/w500${i.poster_path}" class="w-full h-64 object-cover">
                        <div class="p-3 bg-slate-950/80"><h4 class="font-bold text-xs text-white">${i.name}</h4></div></div>`;
                });
                document.getElementById('home-series-grid').innerHTML = hS;
            }
        }

        async function pesquisarTMDB() {
            const q = document.getElementById('search-input').value.trim(), tipo = document.getElementById('search-type').value;
            if(!q) return;
            document.getElementById('home-dashboard').classList.add('hidden');
            const elRes = document.getElementById('results-tmdb');
            elRes.classList.remove('hidden'); elRes.innerHTML = '<div class="col-span-4 text-center text-sky-400">Consultando...</div>';
            const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&tipo=${tipo}`).then(r => r.json());
            let html = '';
            res.forEach(item => {
                let badge = item.status_local === 'COMPLETED' ? '<span class="absolute top-2 right-2 bg-emerald-500 text-xs px-2 py-1 rounded-full">BAIXADO</span>' : '';
                html += `<div onclick="abrirDetalhes('${item.id}', '${tipo}')" class="glass-card rounded-2xl overflow-hidden relative cursor-pointer group">
                    ${badge}<img src="https://image.tmdb.org/t/p/w500${item.poster_path}" class="w-full h-72 object-cover">
                    <div class="p-4 bg-slate-950/80"><h4 class="font-bold text-sm text-white line-clamp-1">${item.title||item.name}</h4></div></div>`;
            });
            elRes.innerHTML = html || '<p class="col-span-4 text-center">Nada encontrado.</p>';
        }

        async function abrirDetalhes(tmdbId, tipo) {
            tab('detail');
            document.getElementById('detail-header').innerHTML = '<div class="w-full text-center text-sky-400">Carregando...</div>';
            document.getElementById('detail-tv-content').classList.add('hidden'); document.getElementById('detail-movie-content').classList.add('hidden');
            const media = await fetch(`/api/details?tmdb_id=${tmdbId}&tipo=${tipo}`).then(r => r.json());
            currentDetailMedia = { tmdbId, tipo, info: media };
            document.getElementById('detail-header').innerHTML = `<img src="https://image.tmdb.org/t/p/w500${media.poster_path}" class="w-44 rounded-2xl"><div class="space-y-3"><h2 class="text-2xl font-black text-white">${media.title||media.name}</h2><p class="text-xs text-slate-300">${media.overview||''}</p></div>`;
            if(tipo === 'tv') { document.getElementById('detail-tv-content').classList.remove('hidden'); carregarTemporadas(media.seasons || []); }
            else { document.getElementById('detail-movie-content').classList.remove('hidden'); carregarFontesFilme(tmdbId); }
        }

        function carregarTemporadas(seasons) {
            const valid = seasons.filter(s => s.season_number > 0);
            let h = '';
            valid.forEach((s, i) => h += `<button onclick="selecionarTemporada(${s.season_number}, this)" class="season-btn ${i===0?'bg-sky-600 text-white':'bg-slate-800 text-slate-400'} px-4 py-2 rounded-xl text-xs">${s.season_number}</button>`);
            document.getElementById('season-tabs').innerHTML = h;
            if(valid.length>0) carregarEpisodios(valid[0].season_number);
        }

        function selecionarTemporada(num, btn) {
            currentSelectedSeason = num;
            document.querySelectorAll('.season-btn').forEach(b => b.className = 'season-btn bg-slate-800 text-slate-400 px-4 py-2 rounded-xl text-xs');
            btn.className = 'season-btn bg-sky-600 text-white px-4 py-2 rounded-xl text-xs';
            carregarEpisodios(num);
        }

        async function carregarEpisodios(seasonNum) {
            currentSelectedSeason = seasonNum;
            const episodes = await fetch(`/api/episodes?tmdb_id=${currentDetailMedia.tmdbId}&season=${seasonNum}`).then(r => r.json());
            let html = '';
            episodes.forEach(ep => {
                let t = ep.status_local==='COMPLETED'?'text-emerald-400':(ep.status_local?'text-sky-400':'text-slate-400');
                html += `<div class="glass p-4 rounded-2xl flex flex-col gap-2"><div class="flex justify-between"><h4 class="font-bold text-white text-sm">EP ${ep.episode_number} - ${ep.name}</h4><span class="${t} text-xs font-bold">${ep.status_local||'Baixar'}</span></div><button onclick="abrirModalFontesEp(${seasonNum}, ${ep.episode_number}, '${ep.name.replace(/'/g,"")}')" class="bg-slate-800 text-xs font-bold py-2 rounded-xl">Opções de Download</button></div>`;
            });
            document.getElementById('episodes-grid').innerHTML = html;
        }

        async function baixarTemporadaCompleta() {
            if(!currentDetailMedia || currentDetailMedia.tipo !== 'tv') return;
            showToast('Enviando...');
            await fetch('/api/queue/add_season', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({tmdb_id: currentDetailMedia.tmdbId, season: currentSelectedSeason}) });
            carregarEpisodios(currentSelectedSeason);
        }

        async function abrirModalFontesEp(s, e, name) {
            currentSelectedEp = { season:s, ep:e, epName:name };
            document.getElementById('ep-sources-modal').classList.remove('hidden');
            document.getElementById('ep-sources-list').innerHTML = '<div class="text-white">Buscando...</div>';
            const data = await fetch(`/api/sources?tmdb_id=${currentDetailMedia.tmdbId}&tipo=tv&season=${s}&episode=${e}`).then(r => r.json());
            let html = '';
            (data.opcoes || []).forEach(opt => {
                html += `<div class="flex justify-between bg-slate-900 p-3 rounded-2xl"><span class="text-xs text-white">${opt.indexador} | ${opt.qualidade}</span><button onclick="adicionarEpAFila('${opt.url}', '${opt.qualidade.replace(/'/g,"")}')" class="bg-emerald-600 px-4 py-2 rounded-xl text-xs text-white">Adicionar</button></div>`;
            });
            document.getElementById('ep-sources-list').innerHTML = html || '<p class="text-xs text-white">Nenhuma fonte. Cole link.</p>';
        }

        async function adicionarEpAFila(url, qual) {
            await fetch('/api/queue/add', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({tipo: 'series', tmdb_info: currentDetailMedia.info, qualidade: qual, url_fonte: url, season: currentSelectedEp.season, episode: currentSelectedEp.ep}) });
            document.getElementById('ep-sources-modal').classList.add('hidden'); carregarEpisodios(currentSelectedSeason);
        }
        function adicionarManualEp() { adicionarEpAFila(document.getElementById('ep-manual-url').value, 'LINK MANUAL'); }

        async function carregarFontesFilme(tmdbId) {
            document.getElementById('movie-sources-list').innerHTML = '<div class="text-white">Buscando...</div>';
            const data = await fetch(`/api/sources?tmdb_id=${tmdbId}&tipo=movie`).then(r => r.json());
            let html = '';
            (data.opcoes || []).forEach(opt => {
                html += `<div class="flex justify-between bg-slate-900 p-4 rounded-2xl"><span class="text-xs text-white">${opt.indexador} | ${opt.qualidade}</span><button onclick="adicionarFilmeAFila('${opt.url}', '${opt.qualidade.replace(/'/g,"")}')" class="bg-emerald-600 px-5 py-2.5 rounded-xl text-xs text-white">Adicionar</button></div>`;
            });
            document.getElementById('movie-sources-list').innerHTML = html || '<p class="text-white text-xs">Sem fontes.</p>';
        }

        async function adicionarFilmeAFila(url, qual) {
            await fetch('/api/queue/add', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({tipo: 'movie', tmdb_info: currentDetailMedia.info, qualidade: qual, url_fonte: url}) });
            showToast('Filme adicionado!', 'success');
        }

        async function alternarPausaFila() { await fetch('/api/queue/toggle_pause', { method: 'POST' }); atualizarFilaEBiblioteca(); }
        function filtrarBiblioteca(c) { filtroBibliotecaAtual = c; atualizarFilaEBiblioteca(); }
        function filtrarFila(c) { filtroFilaAtual = c; atualizarFilaEBiblioteca(); }

        async function atualizarFilaEBiblioteca() {
            const data = await fetch('/api/queue').then(r => r.json());
            const btnPause = document.getElementById('btn-toggle-pause');
            if (btnPause) {
                btnPause.className = data.fila_pausada ? 'px-5 py-2.5 rounded-xl font-extrabold text-xs bg-emerald-600 text-white' : 'px-5 py-2.5 rounded-xl font-extrabold text-xs bg-amber-600 text-white';
                btnPause.innerText = data.fila_pausada ? '▶ Iniciar Fila' : '⏸ Pausar Fila';
            }

            let f = (data.fila || []).filter(i => {
                if(filtroFilaAtual==='active') return i.status==='PENDING'||i.status==='PROCESSING';
                if(filtroFilaAtual==='failed') return i.status==='FAILED';
                if(filtroFilaAtual==='completed') return i.status==='COMPLETED';
            }).slice(0,20);

            let qHtml = '';
            f.forEach(item => {
                let badge = item.status==='PROCESSING'?'bg-blue-600':(item.status==='COMPLETED'?'bg-emerald-500':'bg-rose-500');
                qHtml += `<div class="glass p-5 rounded-2xl flex flex-col gap-3">
                    <div class="flex justify-between items-start"><div><span class="font-extrabold text-white">${item.titulo}</span> <span class="text-xs px-2 py-1 rounded-full text-white ${badge}">${item.status}</span></div>
                    <div class="flex gap-2">${item.status==='FAILED'?`<button onclick="retryFila('${item.id}')" class="bg-amber-600 px-3 py-1.5 rounded-xl text-xs font-bold text-white">🔄</button>`:''}<button onclick="deletarFila('${item.id}')" class="bg-slate-800 text-rose-400 px-3 py-1.5 rounded-xl">❌</button></div></div>
                    <p class="text-xs text-slate-300">${(item.progresso_texto||'').substring(0,80)}...</p></div>`;
            });
            const qList = document.getElementById('queue-list'); if(qList) qList.innerHTML = qHtml || '<p class="text-slate-400 text-sm py-4">Vazio.</p>';

            let s = (data.salvos || []).filter(m => filtroBibliotecaAtual==='all'||m.tipo===filtroBibliotecaAtual);
            let lHtml = '';
            s.forEach(m => {
                lHtml += `<div class="glass p-4 rounded-2xl flex justify-between items-center"><div class="flex items-center gap-3"><div><h4 class="font-bold text-white text-sm">${m.titulo}</h4><p class="text-xs text-sky-400">${m.qualidade||'Ativo'}</p></div></div><button onclick="deletarMidia('${m.media_id}')" class="text-slate-500 hover:text-rose-400 p-2">🗑</button></div>`;
            });
            const lList = document.getElementById('library-list'); if(lList) lList.innerHTML = lHtml || '<p class="text-slate-400 text-sm py-4">Vazio.</p>';
        }

        async function retryFila(id) { await fetch('/api/queue/retry', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}) }); atualizarFilaEBiblioteca(); }
        async function deletarFila(id) { await fetch('/api/queue/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}) }); atualizarFilaEBiblioteca(); }
        async function deletarMidia(media_id) { if(confirm('Apagar do Jellyfin?')){ await fetch('/api/queue/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({media_id}) }); atualizarFilaEBiblioteca(); } }

        document.addEventListener('DOMContentLoaded', () => { carregarDashboardHome(); monitorarNotificacoesFila(); });
        setInterval(monitorarNotificacoesFila, 3000);
        setInterval(() => { if(!document.getElementById('sec-queue').classList.contains('hidden') || !document.getElementById('sec-library').classList.contains('hidden')) atualizarFilaEBiblioteca(); }, 2000);
    </script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")

async def iniciar_servidor():
    await client.start(bot_token=Config.TELEGRAM_TOKEN)
    logger.info("🤖 Sessão iniciada.")
    asyncio.create_task(worker_fila_processamento())
    app = web.Application()
    app.router.add_route("*", "/stream/{media_id}", handle_stream)
    app.router.add_get("/web", handle_web_app)
    app.router.add_get("/api/search", api_search)
    app.router.add_get("/api/details", api_details)
    app.router.add_get("/api/episodes", api_episodes)
    app.router.add_get("/api/sources", api_sources)
    app.router.add_post("/api/queue/add", api_add_queue)
    app.router.add_post("/api/queue/add_season", api_add_season_queue)
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
    logger.info(f"🌐 Server rodando na porta {Config.PORTA_STREAM_LOCAL}")