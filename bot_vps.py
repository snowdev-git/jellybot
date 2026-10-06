import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import requests
import os
import re
import json
import time
import shutil

# ==========================================
# CONFIGURAÇÕES DEFINITIVAS
# ==========================================
TELEGRAM_TOKEN = "8994962973:AAHSi_9Pu952FyaA6mc_Ugcqls9_htrkze0"
TMDB_API_KEY = "3755e8749d79c3d9b395e2041c281a52"

# 🔒 SEGURANÇA: Coloca o teu ID do Telegram aqui
ADMIN_ID = 1289593084 

bot = telebot.TeleBot(TELEGRAM_TOKEN)

INDEXADORES = [
    {"nome": "FrostStream", "url": "https://froststream.cloutteam.com", "strikes": 0, "ativo": True},
    {"nome": "MagnetFlix", "url": "https://magnetflix.magnetbr.online/qualities%3D4k%2C1080p%2C720p%2Csd%7Caudio%3Ddublado%2Clegendado%7Ccatalogs%3Dpopulares_movie%2Cpopulares_series%2Crecentes_servidor_movie%2Crecentes_servidor_series%2Cemalta_movie%2Cemalta_series", "strikes": 0, "ativo": True},
    {"nome": "FenixFlix", "url": "https://fenixflix.fenixhub.online", "strikes": 0, "ativo": True},
    {"nome": "KingVOD", "url": "https://kingvod.wasmer.app/index.php", "strikes": 0, "ativo": True},
    {"nome": "BestCine", "url": "https://bestcine.dpdns.org", "strikes": 0, "ativo": True}
]
MAX_STRIKES = 5

DIRETORIO_SCRIPT = os.path.dirname(os.path.abspath(__file__))
# Usando a pasta global do CasaOS para o Jellyfin detetar automaticamente
PASTA_BASE = "/Media/Jellyfin_Local"
HISTORICO_ARQUIVO = os.path.join(DIRETORIO_SCRIPT, "historico.json")

# ==========================================
# GESTÃO DE HISTÓRICO ANTI-ERROS
# ==========================================
def carregar_historico():
    if os.path.exists(HISTORICO_ARQUIVO):
        try:
            with open(HISTORICO_ARQUIVO, "r", encoding="utf-8") as f:
                dados = json.load(f)
                if not isinstance(dados.get("filmes"), dict): dados["filmes"] = {}
                if not isinstance(dados.get("series"), dict): dados["series"] = {}
                return dados
        except:
            pass
    return {"filmes": {}, "series": {}}

def guardar_historico(historico):
    with open(HISTORICO_ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=4)

def registrar_historico(tipo, titulo, indexador, temporada=None, episodio=None):
    hist = carregar_historico()
    
    if not isinstance(hist.get("filmes"), dict): hist["filmes"] = {}
    if not isinstance(hist.get("series"), dict): hist["series"] = {}
    
    if tipo == "movie":
        hist["filmes"][titulo] = {"indexador": indexador}
    else:
        if titulo not in hist["series"]: hist["series"][titulo] = {}
        temp_str = str(temporada)
        if not isinstance(hist["series"][titulo], dict): hist["series"][titulo] = {}
        if temp_str not in hist["series"][titulo]: hist["series"][titulo][temp_str] = {}
        hist["series"][titulo][temp_str][str(episodio)] = {"indexador": indexador}
        
    guardar_historico(hist)

def limpar_nome(nome):
    return re.sub(r'[\\/*?:"<>|]', "", str(nome))

# ==========================================
# MOTOR DE BUSCA & VERIFICAÇÃO DE FONTES
# ==========================================
def verificar_fontes_ativas(tipo, imdb_id, season=None, episode=None):
    fontes_encontradas = []
    headers = {"User-Agent": "Mozilla/5.0"}
    
    for idx_pos, idx in enumerate(INDEXADORES):
        if not idx["ativo"]: continue
        
        url = f"{idx['url']}/stream/movie/{imdb_id}.json" if tipo == "movie" else f"{idx['url']}/stream/series/{imdb_id}:{season}:{episode}.json"
        
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                streams = res.json().get("streams", [])
                validos = [s for s in streams if "url" in s and s["url"].startswith("http")]
                if validos:
                    fontes_encontradas.append({"id": idx_pos, "nome": idx["nome"]})
        except:
            continue
            
    return fontes_encontradas

def obter_stream_com_idioma(tipo, imdb_id, season=None, episode=None, indexador_alvo="auto"):
    headers = {"User-Agent": "Mozilla/5.0"}
    
    for idx_pos, idx in enumerate(INDEXADORES):
        if indexador_alvo != "auto" and str(idx_pos) != str(indexador_alvo): continue
        if not idx["ativo"] and indexador_alvo == "auto": continue
        
        url = f"{idx['url']}/stream/movie/{imdb_id}.json" if tipo == "movie" else f"{idx['url']}/stream/series/{imdb_id}:{season}:{episode}.json"
        
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                streams = res.json().get("streams", [])
                validos = [s for s in streams if "url" in s and s["url"].startswith("http")]
                
                if validos:
                    idx["strikes"] = 0
                    for s in validos:
                        txt = (s.get("title", "") + " " + s.get("name", "")).lower()
                        if "dub" in txt or "dual" in txt or "pt-br" in txt:
                            return s["url"], "Dublado 🇧🇷", idx["nome"]
                    for s in validos:
                        txt = (s.get("title", "") + " " + s.get("name", "")).lower()
                        if "leg" in txt or "sub" in txt:
                            return s["url"], "Legendado 🇺🇸", idx["nome"]
                    return validos[0]["url"], "Desconhecido", idx["nome"]
            else:
                if indexador_alvo == "auto": idx["strikes"] += 1
        except:
            if indexador_alvo == "auto": idx["strikes"] += 1
            
        if idx["strikes"] >= MAX_STRIKES and ADMIN_ID:
            idx["ativo"] = False
            bot.send_message(ADMIN_ID, f"⚠️ O indexador {idx['nome']} falhou repetidas vezes e foi desativado.")
            
    return None, None, None

# ==========================================
# MENUS PRINCIPAIS
# ==========================================
def menu_principal():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("🎬 Novo Filme"), KeyboardButton("📺 Nova Série"),
        KeyboardButton("📚 Minha Biblioteca"), KeyboardButton("🔄 Procurar p/ Renovar"),
        KeyboardButton("📊 Status do Sistema")
    )
    return markup

@bot.message_handler(commands=['start'])
def comando_start(message):
    if ADMIN_ID and message.chat.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Acesso negado.")
        return
    elif not ADMIN_ID:
        bot.send_message(message.chat.id, f"🔒 O teu ID do Telegram é: `{message.chat.id}`\nColoca na variável ADMIN_ID.", parse_mode="Markdown")
        
    bot.send_message(message.chat.id, "🍿 *Jellyfin Bot a postos!*", reply_markup=menu_principal(), parse_mode="Markdown")

@bot.message_handler(func=lambda msg: True)
def escutar_botoes(message):
    if ADMIN_ID and message.chat.id != ADMIN_ID: return
    
    texto = message.text
    if texto == "🎬 Novo Filme":
        msg = bot.send_message(message.chat.id, "Escreve o nome do filme:")
        bot.register_next_step_handler(msg, pesquisar_tmdb, "movie")
    elif texto == "📺 Nova Série":
        msg = bot.send_message(message.chat.id, "Escreve o nome da série:")
        bot.register_next_step_handler(msg, pesquisar_tmdb, "tv")
    elif texto == "🔄 Procurar p/ Renovar":
        msg = bot.send_message(message.chat.id, "Escreve o nome do título a renovar:")
        bot.register_next_step_handler(msg, pesquisar_tmdb, "renovar")
    elif texto == "📊 Status do Sistema":
        comando_status(message)
    elif texto == "📚 Minha Biblioteca":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("🎬 Meus Filmes", callback_data="hist_menu_filmes"), InlineKeyboardButton("📺 Minhas Séries", callback_data="hist_menu_series"))
        bot.send_message(message.chat.id, "O que queres gerir?", reply_markup=markup)

def comando_status(message):
    hist = carregar_historico()
    texto = f"📊 *ESTADO*\n🎬 Filmes: {len(hist.get('filmes', {}))}\n📺 Séries: {len(hist.get('series', {}))}\n\n*SAÚDE:*\n"
    for idx in INDEXADORES:
        estado = "✅ Online" if idx["ativo"] else "❌ Offline"
        texto += f"• {idx['nome']}: {estado}\n"
    bot.send_message(message.chat.id, texto, parse_mode="Markdown")

def pesquisar_tmdb(message, tipo_busca):
    query = message.text
    tipo_tmdb = "movie" if tipo_busca == "movie" else "tv" 
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={query}&language=pt-PT" if tipo_busca == "renovar" else f"https://api.themoviedb.org/3/search/{tipo_tmdb}?api_key={TMDB_API_KEY}&query={query}&language=pt-PT"

    try:
        res = requests.get(url).json().get("results", [])
        resultados = [r for r in res if r.get('media_type', tipo_tmdb) in ['movie', 'tv']][:5]
        
        if not resultados:
            bot.send_message(message.chat.id, "❌ Nenhum resultado encontrado.")
            return
            
        markup = InlineKeyboardMarkup(row_width=1)
        for r in resultados:
            media = r.get('media_type', tipo_tmdb)
            titulo = r.get('title') if media == "movie" else r.get('name')
            ano = str(r.get('release_date', r.get('first_air_date', 'N/A')))[:4]
            prefixo = "m_" if media == "movie" else "s_"
            markup.add(InlineKeyboardButton(f"{'🎬' if media == 'movie' else '📺'} {titulo} ({ano})", callback_data=f"{prefixo}{r['id']}"))
            
        bot.send_message(message.chat.id, "👇 Escolhe o título:", reply_markup=markup)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Erro: {e}")

# ==========================================
# CALLBACKS E LÓGICA INTELIGENTE
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    bot.answer_callback_query(call.id)
    chat_id = call.message.chat.id
    dados = call.data
    
    # --- BIBLIOTECA (FILMES) ---
    if dados == "hist_menu_filmes":
        hist = carregar_historico()
        filmes = list(hist.get("filmes", {}).keys())
        if not filmes: return bot.edit_message_text("❌ Sem filmes.", chat_id, call.message.message_id)
        markup = InlineKeyboardMarkup(row_width=1)
        for i, f in enumerate(filmes[:50]): markup.add(InlineKeyboardButton(f"🎬 {f}", callback_data=f"lib_m_{i}"))
        bot.edit_message_text("🎬 *Os teus Filmes:*", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # --- BIBLIOTECA (SÉRIES) ---
    elif dados == "hist_menu_series":
        hist = carregar_historico()
        series = list(hist.get("series", {}).keys())
        if not series: return bot.edit_message_text("❌ Sem séries.", chat_id, call.message.message_id)
        markup = InlineKeyboardMarkup(row_width=1)
        for i, s in enumerate(series[:50]): markup.add(InlineKeyboardButton(f"📺 {s}", callback_data=f"lib_s_{i}"))
        bot.edit_message_text("📺 *As tuas Séries:*", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # --- OPÇÕES SOBRE UM FILME DA BIBLIOTECA (RENOVAR OU APAGAR) ---
    elif dados.startswith("lib_m_"):
        idx = int(dados.split("_")[2])
        titulo_completo = list(carregar_historico().get("filmes", {}).keys())[idx]
        
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🔄 Renovar Link", callback_data=f"ren_m_{idx}"),
            InlineKeyboardButton("🗑️ Apagar Filme", callback_data=f"del_m_{idx}")
        )
        bot.edit_message_text(f"🎬 *{titulo_completo}*\nO que pretendes fazer?", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # --- EXECUTAR RENOVAÇÃO DE FILME ---
    elif dados.startswith("ren_m_"):
        idx = int(dados.split("_")[2])
        titulo_completo = list(carregar_historico().get("filmes", {}).keys())[idx]
        bot.edit_message_text(f"⏳ A ligar '{titulo_completo}' aos indexadores...", chat_id, call.message.message_id)
        
        busca_limpa = re.sub(r' \(\d{4}\)$', '', titulo_completo)
        url_busca = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={busca_limpa}&language=pt-PT"
        res = requests.get(url_busca).json().get("results", [])
        
        if res:
            call.data = f"m_{res[0]['id']}"
            callback_handler(call)
        else:
            bot.edit_message_text("❌ Erro no TMDB.", chat_id, call.message.message_id)

    # --- EXECUTAR APAGAR FILME ---
    elif dados.startswith("del_m_"):
        idx = int(dados.split("_")[2])
        hist = carregar_historico()
        filmes_lista = list(hist.get("filmes", {}).keys())
        if idx < len(filmes_lista):
            titulo_completo = filmes_lista[idx]
            
            # Apagar pasta física
            pasta_filme = os.path.join(PASTA_BASE, "Filmes", titulo_completo)
            if os.path.exists(pasta_filme):
                shutil.rmtree(pasta_filme, ignore_errors=True)
                
            # Remover do histórico
            del hist["filmes"][titulo_completo]
            guardar_historico(hist)
            
            bot.edit_message_text(f"🗑️ *Filme Apagado com Sucesso!*\n🎬 {titulo_completo}", chat_id, call.message.message_id, parse_mode="Markdown")
        else:
            bot.edit_message_text("❌ Erro ao localizar o filme.", chat_id, call.message.message_id)

    # --- OPÇÕES SOBRE UMA SÉRIE DA BIBLIOTECA (RENOVAR OU APAGAR) ---
    elif dados.startswith("lib_s_"):
        idx = int(dados.split("_")[2])
        titulo_completo = list(carregar_historico().get("series", {}).keys())[idx]
        
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🔄 Renovar / Temporadas", callback_data=f"ren_s_{idx}"),
            InlineKeyboardButton("🗑️ Apagar Série Inteira", callback_data=f"del_s_{idx}")
        )
        bot.edit_message_text(f"📺 *{titulo_completo}*\nO que pretendes fazer?", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # --- EXECUTAR RENOVAÇÃO DE SÉRIE ---
    elif dados.startswith("ren_s_"):
        idx = int(dados.split("_")[2])
        titulo_completo = list(carregar_historico().get("series", {}).keys())[idx]
        bot.edit_message_text(f"⏳ A carregar '{titulo_completo}'...", chat_id, call.message.message_id)
        
        busca_limpa = re.sub(r' \(\d{4}\)$', '', titulo_completo)
        url_busca = f"https://api.themoviedb.org/3/search/tv?api_key={TMDB_API_KEY}&query={busca_limpa}&language=pt-PT"
        res = requests.get(url_busca).json().get("results", [])
        
        if res:
            call.data = f"s_{res[0]['id']}"
            callback_handler(call)
        else:
            bot.edit_message_text("❌ Erro no TMDB.", chat_id, call.message.message_id)

    # --- EXECUTAR APAGAR SÉRIE INTEIRA ---
    elif dados.startswith("del_s_"):
        idx = int(dados.split("_")[2])
        hist = carregar_historico()
        series_lista = list(hist.get("series", {}).keys())
        if idx < len(series_lista):
            titulo_completo = series_lista[idx]
            
            # Apagar pasta física
            pasta_serie = os.path.join(PASTA_BASE, "Series", titulo_completo)
            if os.path.exists(pasta_serie):
                shutil.rmtree(pasta_serie, ignore_errors=True)
                
            # Remover do histórico
            del hist["series"][titulo_completo]
            guardar_historico(hist)
            
            bot.edit_message_text(f"🗑️ *Série Apagada com Sucesso!*\n📺 {titulo_completo}", chat_id, call.message.message_id, parse_mode="Markdown")
        else:
            bot.edit_message_text("❌ Erro ao localizar a série.", chat_id, call.message.message_id)

    # --- MOSTRAR COLEÇÃO (FILMES SEGUINTES) ---
    elif dados.startswith("col_"):
        col_id = dados.split("_")[1]
        bot.edit_message_text("⏳ A carregar a lista de filmes da coleção...", chat_id, call.message.message_id)
        
        res = requests.get(f"https://api.themoviedb.org/3/collection/{col_id}?api_key={TMDB_API_KEY}&language=pt-PT").json()
        partes = res.get("parts", [])
        partes.sort(key=lambda x: x.get('release_date', '9999'))
        
        markup = InlineKeyboardMarkup(row_width=1)
        for p in partes:
            titulo = p.get('title')
            ano = str(p.get('release_date', 'N/A'))[:4]
            markup.add(InlineKeyboardButton(f"🎬 {titulo} ({ano})", callback_data=f"m_{p['id']}"))
            
        bot.edit_message_text(f"📚 *{res.get('name')}*\n👇 Escolhe qual queres descarregar a seguir:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    # --- FLUXO DE FILMES (COM VERIFICAÇÃO PRÉVIA) ---
    elif dados.startswith("m_"):
        tmdb_id = dados.split("_")[1]
        bot.edit_message_text("⏳ A varrer todos os indexadores à procura deste filme...", chat_id, call.message.message_id)
        
        res = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids").json()
        imdb_id = res.get('external_ids', {}).get('imdb_id')
        
        fontes = verificar_fontes_ativas("movie", imdb_id)
        
        if not fontes:
            bot.edit_message_text("❌ Nenhum indexador tem este filme de momento.", chat_id, call.message.message_id)
            return
            
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("🌟 Automático (Melhor Fonte)", callback_data=f"run_m_{tmdb_id}_auto"))
        for f in fontes:
            markup.add(InlineKeyboardButton(f"✅ {f['nome']}", callback_data=f"run_m_{tmdb_id}_{f['id']}"))
            
        bot.edit_message_text("👇 Filme encontrado! Escolhe a fonte:", chat_id, call.message.message_id, reply_markup=markup)

    # --- FLUXO DE SÉRIES (ESCOLHER TEMPORADA) ---
    elif dados.startswith("s_"):
        tmdb_id = dados.split("_")[1]
        res = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}").json()
        temporadas = [t for t in res.get('seasons', []) if t['season_number'] > 0]
        markup = InlineKeyboardMarkup(row_width=2)
        for t in temporadas:
            markup.add(InlineKeyboardButton(f"Temporada {t['season_number']}", callback_data=f"t_{tmdb_id}_{t['season_number']}_{t['episode_count']}"))
        bot.edit_message_text("📺 Escolhe a temporada:", chat_id, call.message.message_id, reply_markup=markup)

    # --- FLUXO DE SÉRIES (VERIFICAR FONTES DA TEMPORADA) ---
    elif dados.startswith("t_"):
        partes = dados.split("_")
        tmdb_id, season_num, ep_count = partes[1], partes[2], partes[3]
        
        bot.edit_message_text(f"⏳ A verificar quem tem a Temporada {season_num} disponível...", chat_id, call.message.message_id)
        
        res = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids").json()
        imdb_id = res.get('external_ids', {}).get('imdb_id')
        
        fontes = verificar_fontes_ativas("series", imdb_id, season_num, 1)
        
        if not fontes:
            bot.edit_message_text(f"❌ Nenhum indexador parece ter a Temporada {season_num}.", chat_id, call.message.message_id)
            return
            
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("🌟 Automático (Melhor Fonte)", callback_data=f"run_s_{tmdb_id}_{season_num}_{ep_count}_auto"))
        for f in fontes:
            markup.add(InlineKeyboardButton(f"✅ {f['nome']}", callback_data=f"run_s_{tmdb_id}_{season_num}_{ep_count}_{f['id']}"))
            
        bot.edit_message_text(f"👇 Temporada {season_num} validada! Escolhe a fonte:", chat_id, call.message.message_id, reply_markup=markup)

    # --- DOWNLOAD FINAL (FILME + VERIFICAÇÃO DE COLEÇÃO) ---
    elif dados.startswith("run_m_"):
        partes = dados.split("_")
        tmdb_id, indexador_escolhido = partes[2], partes[3] 
        bot.edit_message_text("⏳ A extrair links finais...", chat_id, call.message.message_id)
        
        res = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids&language=pt-PT").json()
        imdb_id = res.get('external_ids', {}).get('imdb_id')
        id_nome = f"{limpar_nome(res.get('title', ''))} ({res.get('release_date', '0000')[:4]})"
        
        link, idioma, nome_idx = obter_stream_com_idioma("movie", imdb_id, indexador_alvo=indexador_escolhido)
        if link:
            pasta = os.path.join(PASTA_BASE, "Filmes", id_nome)
            os.makedirs(pasta, exist_ok=True)
            with open(os.path.join(pasta, f"{id_nome}.strm"), "w", encoding="utf-8") as f: f.write(link)
            registrar_historico("movie", id_nome, nome_idx)
            bot.edit_message_text(f"✅ *Filme Pronto!*\n🎬 {id_nome}\n🔊 {idioma}\n📡 {nome_idx}", chat_id, call.message.message_id, parse_mode="Markdown")
            
            colecao = res.get('belongs_to_collection')
            if colecao:
                col_id = colecao.get('id')
                col_nome = colecao.get('name')
                markup_col = InlineKeyboardMarkup()
                markup_col.add(InlineKeyboardButton(f"📚 Ver os outros filmes", callback_data=f"col_{col_id}"))
                bot.send_message(chat_id, f"🍿 Este filme faz parte da coleção **{col_nome}**!", reply_markup=markup_col, parse_mode="Markdown")
        else:
            bot.edit_message_text("❌ Nenhum link encontrado com esse indexador.", chat_id, call.message.message_id)

    # --- DOWNLOAD FINAL (SÉRIE) ---
    elif dados.startswith("run_s_"):
        partes = dados.split("_")
        tmdb_id, season_num, ep_count, indexador_escolhido = partes[2], int(partes[3]), int(partes[4]), partes[5]
        bot.edit_message_text(f"⏳ A processar Temporada {season_num}...", chat_id, call.message.message_id)
        
        res = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids").json()
        imdb_id, id_nome = res.get('external_ids', {}).get('imdb_id'), f"{limpar_nome(res.get('name', ''))} ({res.get('first_air_date', '0000')[:4]})"
        pasta_temp = os.path.join(PASTA_BASE, "Series", id_nome, f"Season {season_num:02d}")
        os.makedirs(pasta_temp, exist_ok=True)
        
        sucessos = 0
        for ep in range(1, ep_count + 1):
            link, _, nome_idx = obter_stream_com_idioma("series", imdb_id, season_num, ep, indexador_alvo=indexador_escolhido)
            if link:
                with open(os.path.join(pasta_temp, f"{id_nome} S{season_num:02d}E{ep:02d}.strm"), "w", encoding="utf-8") as f: f.write(link)
                registrar_historico("series", id_nome, nome_idx, season_num, ep)
                sucessos += 1
                
        bot.edit_message_text(f"✅ *Temporada Concluída!*\n📺 {id_nome} - S{season_num:02d}\n📥 {sucessos}/{ep_count} ficheiros gerados.\n📡 Fonte: {nome_idx}", chat_id, call.message.message_id, parse_mode="Markdown")

# ==========================================
# INICIAR O BOT COM BLINDAGEM ANTI-CRASH
# ==========================================
if __name__ == "__main__":
    os.makedirs(os.path.join(PASTA_BASE, "Filmes"), exist_ok=True)
    os.makedirs(os.path.join(PASTA_BASE, "Series"), exist_ok=True)
    print("🤖 Bot iniciado com sucesso e blindado contra falhas!")
    
    while True:
        try:
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"⚠️ Erro de conexão detetado: {e}")
            print("🔄 A reiniciar o bot em 5 segundos...")
            time.sleep(5)