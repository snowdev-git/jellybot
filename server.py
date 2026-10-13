import os
import re
import json
import asyncio
import requests
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
    resetar_item_fila
)
from downloader import buscar_fontes_indexadores, processar_e_hospedar_midia

# --- WORKER AUTOMÁTICO DA FILA DE ESPERA ---
async def worker_fila_processamento():
    logger.info("⚙️ Worker de Fila de Espera iniciado e monitorando novas solicitações...")
    while True:
        try:
            item = obter_proximo_fila()
            if item:
                item_id = item['id']
                titulo = item['titulo']
                logger.info(f"▶️ [FILA] A iniciar processamento do item: {titulo} ({item['qualidade']})")
                
                atualizar_status_fila(item_id, 'PROCESSING', '⏳ A iniciar download...')

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
                else:
                    atualizar_status_fila(item_id, 'FAILED', '❌ Falha ao processar', erro_msg=str(msg_res))
                    logger.error(f"❌ [FILA] Falha no item {titulo}: {msg_res}")

            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"⚠️ Erro no worker da fila: {e}")
            await asyncio.sleep(5)

# --- STREAMING DE VÍDEO ---
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

# --- API DA WEB UI ---
async def api_search(request):
    q = request.query.get('q', '').strip()
    tipo = request.query.get('tipo', 'movie')
    if not q:
        return web.json_response([])
    url = f"https://api.themoviedb.org/3/search/{tipo}?api_key={Config.TMDB_API_KEY}&query={q}&language=pt-BR"
    try:
        res = requests.get(url, timeout=10).json().get("results", [])[:8]
        return web.json_response(res)
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
            opcoes = buscar_fontes_indexadores("movie", imdb_id)
        else:
            opcoes = buscar_fontes_indexadores("series", imdb_id, int(season), int(episode))

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
    return web.json_response({"success": True, "message": f"{titulo} adicionado à fila!"})

async def api_get_queue(request):
    fila = listar_fila()
    salvos = listar_midias()
    return web.json_response({"fila": fila, "salvos": salvos})

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

# --- PÁGINA WEB ---
async def handle_web_app(request):
    html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JellyBot Web — Fila de Automacao</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; }
        .glass { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.08); }
    </style>
</head>
<body class="min-h-screen pb-12">
    <nav class="glass sticky top-0 z-50 px-6 py-4 mb-8 flex justify-between items-center shadow-lg">
        <div class="flex items-center space-x-3">
            <span class="text-3xl">🍿</span>
            <span class="text-xl font-extrabold bg-gradient-to-r from-sky-400 to-blue-600 bg-clip-text text-transparent">JellyBot Manager</span>
        </div>
        <div class="flex space-x-2">
            <button onclick="tab('search')" id="tab-search" class="px-4 py-2 rounded-lg font-semibold transition bg-blue-600 text-white"><i class="fa-solid fa-magnifying-glass mr-2"></i>Pesquisar</button>
            <button onclick="tab('queue')" id="tab-queue" class="px-4 py-2 rounded-lg font-semibold transition bg-slate-800 hover:bg-slate-700"><i class="fa-solid fa-list-check mr-2"></i>Fila & Status</button>
            <button onclick="tab('library')" id="tab-library" class="px-4 py-2 rounded-lg font-semibold transition bg-slate-800 hover:bg-slate-700"><i class="fa-solid fa-film mr-2"></i>Biblioteca</button>
        </div>
    </nav>

    <div class="max-w-6xl mx-auto px-4">
        <!-- SEÇÃO 1: PESQUISA -->
        <div id="sec-search" class="space-y-6">
            <div class="glass p-6 rounded-2xl shadow-xl">
                <div class="flex flex-col md:flex-row gap-4">
                    <select id="search-type" class="bg-slate-800 border border-slate-700 rounded-xl px-4 py-3 font-semibold text-sky-400 focus:outline-none">
                        <option value="movie">🎬 Filme</option>
                        <option value="tv">📺 Série</option>
                    </select>
                    <input type="text" id="search-input" placeholder="Digite o nome do filme ou série..." class="flex-1 bg-slate-800 border border-slate-700 rounded-xl px-5 py-3 text-white placeholder-slate-400 focus:outline-none focus:border-sky-500" onkeypress="if(event.key==='Enter') pesquisarTMDB()">
                    <button onclick="pesquisarTMDB()" class="bg-blue-600 hover:bg-blue-500 font-bold px-8 py-3 rounded-xl transition shadow-lg flex items-center justify-center"><i class="fa-solid fa-search mr-2"></i>Buscar</button>
                </div>
            </div>

            <div id="results-tmdb" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6"></div>

            <div id="sources-modal" class="hidden glass p-6 rounded-2xl border-2 border-sky-500/30 space-y-4">
                <div class="flex justify-between items-center border-b border-slate-700 pb-3">
                    <h3 id="sources-title" class="text-lg font-bold text-sky-400"></h3>
                    <button onclick="fecharSources()" class="text-slate-400 hover:text-white text-xl">&times;</button>
                </div>
                <div id="ep-selector" class="hidden flex gap-4 bg-slate-800/60 p-3 rounded-xl">
                    <div>
                        <label class="text-xs text-slate-400 font-bold block mb-1">Temporada</label>
                        <input type="number" id="ep-season" value="1" min="1" class="w-20 bg-slate-900 border border-slate-700 rounded-lg px-2 py-1 text-center">
                    </div>
                    <div>
                        <label class="text-xs text-slate-400 font-bold block mb-1">Episódio</label>
                        <input type="number" id="ep-num" value="1" min="1" class="w-20 bg-slate-900 border border-slate-700 rounded-lg px-2 py-1 text-center">
                    </div>
                    <button onclick="carregarFontesAtualizada()" class="self-end bg-sky-600 hover:bg-sky-500 text-xs font-bold px-3 py-2 rounded-lg">Buscar EP</button>
                </div>
                <div id="sources-list" class="space-y-2"></div>
                <div class="pt-2 border-t border-slate-700">
                    <p class="text-xs text-slate-400 mb-2">Ou insira um link direto (HTTP/MP4):</p>
                    <div class="flex gap-2">
                        <input type="text" id="manual-url" placeholder="http://servidor.com/video.mp4" class="flex-1 bg-slate-800 border border-slate-700 rounded-xl px-3 py-2 text-sm">
                        <button onclick="adicionarManualFila()" class="bg-emerald-600 hover:bg-emerald-500 text-xs font-bold px-4 py-2 rounded-xl">➕ Adicionar Link</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- SEÇÃO 2: FILA -->
        <div id="sec-queue" class="hidden space-y-4">
            <div class="flex justify-between items-center mb-2">
                <h2 class="text-xl font-bold text-slate-200">⏳ Fila de Automacao & Status</h2>
                <span class="text-xs text-slate-400"><i class="fa-solid fa-rotate mr-1"></i>Atualizando em tempo real</span>
            </div>
            <div id="queue-list" class="space-y-3"></div>
        </div>

        <!-- SEÇÃO 3: BIBLIOTECA -->
        <div id="sec-library" class="hidden space-y-4">
            <h2 class="text-xl font-bold text-slate-200 mb-2">📚 Midias Ativas no Jellyfin</h2>
            <div id="library-list" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </div>
    </div>

    <script>
        let currentTMDB = null;

        function tab(t) {
            ['search', 'queue', 'library'].forEach(s => {
                document.getElementById('sec-' + s).classList.add('hidden');
                document.getElementById('tab-' + s).className = 'px-4 py-2 rounded-lg font-semibold transition bg-slate-800 hover:bg-slate-700';
            });
            document.getElementById('sec-' + t).classList.remove('hidden');
            document.getElementById('tab-' + t).className = 'px-4 py-2 rounded-lg font-semibold transition bg-blue-600 text-white';
            if (t === 'queue' || t === 'library') atualizarFilaEBiblioteca();
        }

        async function pesquisarTMDB() {
            const q = document.getElementById('search-input').value.trim();
            const tipo = document.getElementById('search-type').value;
            if(!q) return;

            document.getElementById('results-tmdb').innerHTML = '<div class="col-span-4 text-center py-8 text-sky-400 font-bold"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Buscando no TMDB...</div>';
            const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&tipo=${tipo}`).then(r => r.json());

            let html = '';
            res.forEach(item => {
                const tit = item.title || item.name;
                const ano = (item.release_date || item.first_air_date || '0000').substring(0,4);
                const poster = item.poster_path ? `https://image.tmdb.org/t/p/w500${item.poster_path}` : 'https://via.placeholder.com/300x450?text=Sem+Capa';
                
                html += `
                <div class="glass rounded-xl overflow-hidden shadow-lg flex flex-col justify-between hover:border-sky-500/50 transition">
                    <img src="${poster}" class="w-full h-64 object-cover">
                    <div class="p-4 flex-1 flex flex-col justify-between">
                        <div>
                            <h4 class="font-bold text-sm text-white line-clamp-1">${tit}</h4>
                            <p class="text-xs text-slate-400 mb-2">${ano} • Nota: ${item.vote_average || 'N/A'}/10</p>
                        </div>
                        <button onclick="selecionarMedia('${item.id}', '${tipo}')" class="w-full bg-sky-600 hover:bg-sky-500 text-xs font-bold py-2 rounded-lg transition mt-2"><i class="fa-solid fa-list mr-1"></i>Ver Opcoes</button>
                    </div>
                </div>`;
            });
            document.getElementById('results-tmdb').innerHTML = html || '<p class="col-span-4 text-center text-slate-400">Nenhum resultado encontrado.</p>';
        }

        async function selecionarMedia(tmdbId, tipo) {
            document.getElementById('sources-modal').classList.remove('hidden');
            document.getElementById('sources-title').innerText = '🔍 Carregando fontes nos indexadores...';
            document.getElementById('sources-list').innerHTML = '<div class="text-center py-4 text-sky-400 font-bold"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Buscando streams...</div>';

            if(tipo === 'tv') {
                document.getElementById('ep-selector').classList.remove('hidden');
            } else {
                document.getElementById('ep-selector').classList.add('hidden');
            }

            carregarFontes(tmdbId, tipo, 1, 1);
        }

        async function carregarFontes(tmdbId, tipo, season, episode) {
            const data = await fetch(`/api/sources?tmdb_id=${tmdbId}&tipo=${tipo}&season=${season}&episode=${episode}`).then(r => r.json());
            currentTMDB = { tmdbId, tipo, info: data.tmdb_info };

            const tit = data.tmdb_info.title || data.tmdb_info.name;
            document.getElementById('sources-title').innerText = `Opcoes para: ${tit} ${tipo==='tv'?`S${String(season).padStart(2,'0')}E${String(episode).padStart(2,'0')}`:''}`;

            let html = '';
            (data.opcoes || []).forEach(opt => {
                html += `
                <div class="flex justify-between items-center bg-slate-800/80 p-3 rounded-xl border border-slate-700/50 hover:border-sky-500/50">
                    <div>
                        <span class="text-xs font-bold text-sky-400">[${opt.indexador}]</span>
                        <span class="text-sm font-semibold text-white ml-2">${opt.qualidade}</span>
                    </div>
                    <button onclick="adicionarFila('${opt.url}', '${opt.qualidade.replace(/'/g,"")}')" class="bg-emerald-600 hover:bg-emerald-500 text-xs font-bold px-4 py-2 rounded-lg transition"><i class="fa-solid fa-plus mr-1"></i>Adicionar a Fila</button>
                </div>`;
            });

            document.getElementById('sources-list').innerHTML = html || '<p class="text-xs text-amber-400 p-2">Nenhuma fonte encontrada automaticamente. Cole um link direto abaixo.</p>';
        }

        function carregarFontesAtualizada() {
            if(!currentTMDB) return;
            const s = document.getElementById('ep-season').value;
            const e = document.getElementById('ep-num').value;
            carregarFontes(currentTMDB.tmdbId, currentTMDB.tipo, s, e);
        }

        function fecharSources() {
            document.getElementById('sources-modal').classList.add('hidden');
        }

        async function adicionarFila(url, qualidade) {
            const season = document.getElementById('ep-season')?.value || 1;
            const ep = document.getElementById('ep-num')?.value || 1;

            const res = await fetch('/api/queue/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    tipo: currentTMDB.tipo,
                    tmdb_info: currentTMDB.info,
                    qualidade: qualidade,
                    url_fonte: url,
                    season: season,
                    episode: ep
                })
            }).then(r => r.json());

            alert(res.message || 'Adicionado a Fila!');
            fecharSources();
            tab('queue');
        }

        async function adicionarManualFila() {
            const url = document.getElementById('manual-url').value.trim();
            if(!url) return alert('Digite uma URL valida!');
            adicionarFila(url, 'LINK MANUAL 🔗');
        }

        async function atualizarFilaEBiblioteca() {
            const data = await fetch('/api/queue').then(r => r.json());

            let qHtml = '';
            (data.fila || []).forEach(item => {
                let badge = 'bg-slate-700 text-slate-300';
                let ico = 'fa-clock';
                if(item.status === 'PROCESSING') { badge = 'bg-blue-600/30 border border-blue-500 text-blue-400'; ico = 'fa-spinner fa-spin'; }
                if(item.status === 'COMPLETED') { badge = 'bg-emerald-600/30 border border-emerald-500 text-emerald-400'; ico = 'fa-check'; }
                if(item.status === 'FAILED') { badge = 'bg-rose-600/30 border border-rose-500 text-rose-400'; ico = 'fa-triangle-exclamation'; }

                qHtml += `
                <div class="glass p-4 rounded-xl flex flex-col md:flex-row justify-between items-start md:items-center gap-3">
                    <div class="space-y-1">
                        <div class="flex items-center space-x-2">
                            <span class="font-bold text-white">${item.titulo}</span>
                            <span class="text-xs px-2 py-0.5 rounded ${badge} font-bold"><i class="fa-solid ${ico} mr-1"></i>${item.status}</span>
                        </div>
                        <p class="text-xs text-sky-400 font-medium">${item.qualidade}</p>
                        <p class="text-xs text-slate-300">${item.progresso_texto || ''}</p>
                        ${item.erro_msg ? `<p class="text-xs text-rose-400 bg-rose-950/50 p-2 rounded mt-1">${item.erro_msg}</p>` : ''}
                    </div>
                    <div class="flex space-x-2 self-end md:self-auto">
                        ${item.status === 'FAILED' ? `<button onclick="retryFila('${item.id}')" class="bg-amber-600 hover:bg-amber-500 text-xs font-bold px-3 py-1.5 rounded-lg"><i class="fa-solid fa-rotate-right mr-1"></i>Tentar Novamente</button>` : ''}
                        <button onclick="deletarFila('${item.id}')" class="bg-slate-800 hover:bg-rose-900 text-xs text-slate-400 hover:text-rose-200 px-3 py-1.5 rounded-lg border border-slate-700"><i class="fa-solid fa-trash"></i></button>
                    </div>
                </div>`;
            });
            document.getElementById('queue-list').innerHTML = qHtml || '<p class="text-slate-400 text-sm">Fila vazia.</p>';

            let lHtml = '';
            (data.salvos || []).forEach(m => {
                lHtml += `
                <div class="glass p-4 rounded-xl flex justify-between items-center">
                    <div>
                        <h4 class="font-bold text-white text-sm">${m.titulo} (${m.ano})</h4>
                        <p class="text-xs text-slate-400">${m.qualidade}</p>
                    </div>
                    <button onclick="deletarMidia('${m.media_id}')" class="text-slate-500 hover:text-rose-400 text-sm p-2"><i class="fa-solid fa-trash"></i></button>
                </div>`;
            });
            document.getElementById('library-list').innerHTML = lHtml || '<p class="text-slate-400 text-sm">Nenhuma mídia salva no Jellyfin.</p>';
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
            if(!confirm('Tem certeza que deseja apagar do Jellyfin?')) return;
            await fetch('/api/queue/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({media_id}) });
            atualizarFilaEBiblioteca();
        }

        setInterval(() => {
            if(!document.getElementById('sec-queue').classList.contains('hidden')) {
                atualizarFilaEBiblioteca();
            }
        }, 3000);
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
    app.router.add_get("/api/sources", api_sources)
    app.router.add_post("/api/queue/add", api_add_queue)
    app.router.add_get("/api/queue", api_get_queue)
    app.router.add_post("/api/queue/retry", api_retry_queue)
    app.router.add_post("/api/queue/delete", api_delete_queue)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", Config.PORTA_STREAM_LOCAL)
    await site.start()
    logger.info(f"🌐 Servidor de Streaming rodando na porta {Config.PORTA_STREAM_LOCAL}")
    logger.info(f"🌐 Painel Web do JellyBot disponível em http://{Config.IP_SERVIDOR}:{Config.PORTA_STREAM_LOCAL}/web")