import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import requests
import os
import re
import json
import time
import shutil
import threading

# ==========================================
# CONFIGURAÇÕES DEFINITIVAS
# ==========================================
TELEGRAM_TOKEN = "8994962973:AAHSi_9Pu952FyaA6mc_Ugcqls9_htrkze0"
TMDB_API_KEY = "3755e8749d79c3d9b395e2041c281a52"

# 🔒 SEGURANÇA: Coloca o teu ID do Telegram aqui
ADMIN_ID = 1289593084 

bot = telebot.TeleBot(TELEGRAM_TOKEN)
MAX_STRIKES = 5

DIRETORIO_SCRIPT = os.path.dirname(os.path.abspath(__file__))
PASTA_BASE = "/DATA/Media" 
HISTORICO_ARQUIVO = os.path.join(DIRETORIO_SCRIPT, "historico.json")
INDEXADORES_ARQUIVO = os.path.join(DIRETORIO_SCRIPT, "indexadores.json")

# ==========================================
# GESTÃO DE DADOS (HISTÓRICO E INDEXADORES)
# ==========================================
def carregar_indexadores():
    if os.path.exists(INDEXADORES_ARQUIVO):
        try:
            with open(INDEXADORES_ARQUIVO, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    padrao = [
        {"nome": "FrostStream", "url": "https://froststream.cloutteam.com", "strikes": 0, "ativo": True},
        {"nome": "MagnetFlix", "url": "https://magnetflix.magnetbr.online/qualities%3D4k%2C1080p%2C720p%2Csd%7Caudio%3Ddublado%2Clegendado%7Ccatalogs%3Dpopulares_movie%2Cpopulares_series%2Crecentes_servidor_movie%2Crecentes_servidor_series%2Cemalta_movie%2Cemalta_series", "strikes": 0, "ativo": True},
        {"nome": "FenixFlix", "url": "https://fenixflix.fenixhub.online", "strikes": 0, "ativo": True},
        {"nome": "KingVOD", "url": "https://kingvod.wasmer.app/index.php", "strikes": 0, "ativo": True},
        {"nome": "BestCine", "url": "https://bestcine.dpdns.org", "strikes": 0, "ativo": True}
    ]
    guardar_indexadores(padrao)
    return padrao

def guardar_indexadores(lista):
    with open(INDEXADORES_ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=4)

def carregar_historico():
    if os.path.exists(HISTORICO_ARQUIVO):
        try:
            with open(HISTORICO_ARQUIVO, "r", encoding="utf-8") as f:
                dados = json.load(f)
                if not isinstance(dados.get("filmes"), dict): dados["filmes"] = {}
                if not isinstance(dados.get("series"), dict): dados["series"] = {}
                return dados
        except: pass
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
# MOTOR DE BUSCA & VERIFICAÇÃO DE FONTES (APENAS PT-BR)
# ==========================================
def verificar_fontes_ativas(tipo, imdb_id, season=None, episode=None):
    fontes_encontradas = []
    headers = {"User-Agent": "Mozilla/5.0"}
    indexadores = carregar_indexadores()
    for idx_pos, idx in enumerate(indexadores):
        if not idx.get("ativo", True): continue
        url = f"{idx['url']}/stream/movie/{imdb_id}.json" if tipo == "movie" else f"{idx['url']}/stream/series/{imdb_id}:{season}:{episode}.json"
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                streams = res.json().get("streams", [])
                validos = [s for s in streams if "url" in s and s["url"].startswith("http")]
                # Filtra apenas se encontrar indício de áudio em PT-BR
                pt_validos = []
                for s in validos:
                    txt = (s.get("title", "") + " " + s.get("name", "")).lower()
                    if "dub" in txt or "dual" in txt or "pt-br" in txt or "portugues" in txt:
                        pt_validos.append(s)
                if pt_validos: 
                    fontes_encontradas.append({"id": idx_pos, "nome": idx["nome"]})
        except: continue
    return fontes_encontradas

def obter_stream_com_idioma(tipo, imdb_id, season=None, episode=None, indexador_alvo="auto"):
    headers = {"User-Agent": "Mozilla/5.0"}
    indexadores = carregar_indexadores()
    houve_alteracao = False

    for idx_pos, idx in enumerate(indexadores):
        if indexador_alvo != "auto" and str(idx_pos) != str(indexador_alvo): continue
        if not idx.get("ativo", True) and indexador_alvo == "auto": continue
        
        url = f"{idx['url']}/stream/movie/{imdb_id}.json" if tipo == "movie" else f"{idx['url']}/stream/series/{imdb_id}:{season}:{episode}.json"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                streams = res.json().get("streams", [])
                validos = [s for s in streams if "url" in s and s["url"].startswith("http")]
                if validos:
                    idx["strikes"] = 0
                    houve_alteracao = True
                    for s in validos:
                        txt = (s.get("title", "") + " " + s.get("name", "")).lower()
                        if "dub" in txt or "dual" in txt or "pt-br" in txt or "portugues" in txt:
                            if houve_alteracao: guardar_indexadores(indexadores)
                            return s["url"], "Dublado 🇧🇷", idx["nome"]
            else:
                if indexador_alvo == "auto": 
                    idx["strikes"] = idx.get("strikes", 0) + 1
                    houve_alteracao = True
        except:
            if indexador_alvo == "auto": 
                idx["strikes"] = idx.get("strikes", 0) + 1
                houve_alteracao = True
                
        if idx.get("strikes", 0) >= MAX_STRIKES and idx.get("ativo", True):
            idx["ativo"] = False
            houve_alteracao = True
            if ADMIN_ID: bot.send_message(ADMIN_ID, f"⚠️ O indexador {idx['nome']} falhou repetidas vezes e foi desativado.")
            
    if houve_alteracao: guardar_indexadores(indexadores)
    return None, None, None

# ==========================================
# RENOVAÇÃO EM LOTE (BACKGROUND THREAD)
# ==========================================
def tarefa_renovacao_lote(chat_id, mensagem_id, escopo, indexador_escolhido):
    hist = carregar_historico()
    alvos = []
    
    if escopo in ["filmes", "tudo"]:
        for f in hist.get("filmes", {}).keys(): alvos.append(("movie", f))
    if escopo in ["series", "tudo"]:
        for s in hist.get("series", {}).keys(): alvos.append(("series", s))

    total = len(alvos)
    if total == 0:
        bot.edit_message_text("❌ Não há nada no histórico para renovar.", chat_id, mensagem_id)
        return

    sucessos, falhas = 0, []
    indexadores = carregar_indexadores()
    nome_idx_display = "Automático" if indexador_escolhido == "auto" else indexadores[int(indexador_escolhido)]["nome"]
    
    for i, (tipo, titulo) in enumerate(alvos, 1):
        if i % 3 == 0 or i == 1 or i == total:
            percent = int((i / total) * 100)
            txt = f"🔄 *Renovação em Lote* ⏳\n📡 Fonte: {nome_idx_display}\n\nProgresso: {percent}% ({i}/{total})\nA processar: `{titulo}`..."
            try: bot.edit_message_text(txt, chat_id, mensagem_id, parse_mode="Markdown")
            except: pass

        try:
            busca_limpa = re.sub(r' \(\d{4}\)$', '', titulo)
            url_busca = f"https://api.themoviedb.org/3/search/{'movie' if tipo == 'movie' else 'tv'}?api_key={TMDB_API_KEY}&query={busca_limpa}&language=pt-PT"
            res = requests.get(url_busca, timeout=10).json().get("results", [])
            
            if not res:
                falhas.append(f"[{'Filme' if tipo == 'movie' else 'Série'}] {titulo} - Não encontrado no TMDB")
                continue
                
            tmdb_id = res[0]['id']
            res_ext = requests.get(f"https://api.themoviedb.org/3/{'movie' if tipo == 'movie' else 'tv'}/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids").json()
            imdb_id = res_ext.get('external_ids', {}).get('imdb_id')
            
            if tipo == "movie":
                link, _, nome_idx = obter_stream_com_idioma("movie", imdb_id, indexador_alvo=indexador_escolhido)
                if link:
                    pasta = os.path.join(PASTA_BASE, "FILMES", titulo)
                    os.makedirs(pasta, exist_ok=True)
                    with open(os.path.join(pasta, f"{titulo}.strm"), "w", encoding="utf-8") as f: f.write(link)
                    registrar_historico("movie", titulo, nome_idx)
                    sucessos += 1
                else: falhas.append(f"[Filme] {titulo} - Sem link PT-BR no indexador")
            else:
                ep_sucessos, eps_totais = 0, 0
                temporadas_hist = hist["series"].get(titulo, {})
                for temp_str, episodios in temporadas_hist.items():
                    season_num = int(temp_str)
                    pasta_temp = os.path.join(PASTA_BASE, "SERIES", titulo, f"Season {season_num:02d}")
                    os.makedirs(pasta_temp, exist_ok=True)
                    for ep_str in episodios.keys():
                        eps_totais += 1
                        link, _, nome_idx = obter_stream_com_idioma("series", imdb_id, season_num, int(ep_str), indexador_alvo=indexador_escolhido)
                        if link:
                            with open(os.path.join(pasta_temp, f"{titulo} S{season_num:02d}E{int(ep_str):02d}.strm"), "w", encoding="utf-8") as f: f.write(link)
                            registrar_historico("series", titulo, nome_idx, season_num, int(ep_str))
                            ep_sucessos += 1
                if ep_sucessos > 0:
                    sucessos += 1
                    if ep_sucessos < eps_totais: falhas.append(f"[Série] {titulo} - Incompleta ({ep_sucessos}/{eps_totais})")
                else: falhas.append(f"[Série] {titulo} - Nenhum link PT-BR encontrado")
        except:
            falhas.append(f"[{'Filme' if tipo == 'movie' else 'Série'}] {titulo} - Erro interno")
        time.sleep(2)

    relatorio = f"✅ *Renovação Concluída!*\n📡 Fonte: {nome_idx_display}\n\n🎯 Sucesso: {sucessos}/{total} itens atualizados.\n"
    if falhas:
        relatorio += f"\n⚠️ *Problemas ({len(falhas)}):*\n" + "\n".join([f"• {e}" for e in falhas[:15]])
        if len(falhas) > 15: relatorio += f"\n• ... e mais {len(falhas) - 15} erros."
    try: bot.edit_message_text(relatorio, chat_id, mensagem_id, parse_mode="Markdown")
    except: bot.send_message(chat_id, relatorio, parse_mode="Markdown")

# ==========================================
# MENUS PRINCIPAIS
# ==========================================
def menu_principal():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("🎬 Novo Filme"), KeyboardButton("📺 Nova Série"),
        KeyboardButton("📚 Minha Biblioteca"), KeyboardButton("🔄 Renovação em Lote"),
        KeyboardButton("📊 Status do Sistema"), KeyboardButton("⚙️ Gerir Indexadores")
    )
    return markup

@bot.message_handler(commands=['start'])
def comando_start(message):
    if ADMIN_ID and message.chat.id != ADMIN_ID: return bot.send_message(message.chat.id, "⛔ Acesso negado.")
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
    elif texto == "📊 Status do Sistema":
        hist = carregar_historico()
        idx_lista = carregar_indexadores()
        txt = f"📊 *ESTADO*\n🎬 Filmes: {len(hist.get('filmes', {}))}\n📺 Séries: {len(hist.get('series', {}))}\n\n*SAÚDE DOS INDEXADORES:*\n"
        for idx in idx_lista: txt += f"• {idx['nome']}: {'✅ Online' if idx.get('ativo', True) else '❌ Offline'}\n"
        bot.send_message(message.chat.id, txt, parse_mode="Markdown")
    elif texto == "📚 Minha Biblioteca":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("🎬 Meus Filmes", callback_data="hist_menu_filmes"), InlineKeyboardButton("📺 Minhas Séries", callback_data="hist_menu_series"))
        bot.send_message(message.chat.id, "O que queres gerir?", reply_markup=markup)
    elif texto == "🔄 Renovação em Lote":
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("🎬 Apenas Filmes", callback_data="lote_filmes"),
            InlineKeyboardButton("📺 Apenas Séries", callback_data="lote_series"),
            InlineKeyboardButton("🍿 Tudo (Filmes e Séries)", callback_data="lote_tudo")
        )
        bot.send_message(message.chat.id, "⚠️ *Atenção:* Demora alguns minutos.\nO que pretendes renovar?", reply_markup=markup, parse_mode="Markdown")
    elif texto == "⚙️ Gerir Indexadores":
        idx_lista = carregar_indexadores()
        markup = InlineKeyboardMarkup(row_width=1)
        for i, idx in enumerate(idx_lista):
            estado = "✅" if idx.get('ativo', True) else "❌"
            markup.add(InlineKeyboardButton(f"{estado} {idx['nome']}", callback_data=f"g_idx_{i}"))
        markup.add(InlineKeyboardButton("➕ Adicionar Novo", callback_data="add_idx_1"))
        bot.send_message(message.chat.id, "⚙ *Gestão de Indexadores*\nEscolhe um para editar/remover ou adiciona um novo:", reply_markup=markup, parse_mode="Markdown")

def pesquisar_tmdb(message, tipo_busca):
    query = message.text
    url = f"https://api.themoviedb.org/3/search/{tipo_busca}?api_key={TMDB_API_KEY}&query={query}&language=pt-PT"
    try:
        res = requests.get(url).json().get("results", [])[:5]
        if not res: return bot.send_message(message.chat.id, "❌ Nenhum resultado encontrado.")
        markup = InlineKeyboardMarkup(row_width=1)
        for r in res:
            titulo = r.get('title') if tipo_busca == "movie" else r.get('name')
            ano = str(r.get('release_date', r.get('first_air_date', 'N/A')))[:4]
            markup.add(InlineKeyboardButton(f"{'🎬' if tipo_busca == 'movie' else '📺'} {titulo} ({ano})", callback_data=f"{'m_' if tipo_busca == 'movie' else 's_'}{r['id']}"))
        bot.send_message(message.chat.id, "👇 Escolhe o título:", reply_markup=markup)
    except Exception as e: bot.send_message(message.chat.id, f"❌ Erro: {e}")

# ==========================================
# CALLBACKS E LÓGICA DE EDIÇÃO
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    bot.answer_callback_query(call.id)
    chat_id, dados = call.message.chat.id, call.data

    if dados.startswith("g_idx_"):
        i = int(dados.split("_")[2])
        idx = carregar_indexadores()[i]
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("✏️ Editar URL", callback_data=f"e_idx_{i}"), InlineKeyboardButton("🗑️ Remover", callback_data=f"d_idx_{i}"))
        markup.add(InlineKeyboardButton("🔄 Ligar/Desligar", callback_data=f"t_idx_{i}"))
        bot.edit_message_text(f"⚙️ *{idx['nome']}*\n🔗 URL: `{idx['url']}`\nEstado: {'Ativo' if idx.get('ativo', True) else 'Inativo'}", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        
    elif dados.startswith("t_idx_"):
        i = int(dados.split("_")[2])
        indexadores = carregar_indexadores()
        indexadores[i]["ativo"] = not indexadores[i].get("ativo", True)
        indexadores[i]["strikes"] = 0
        guardar_indexadores(indexadores)
        bot.edit_message_text("✅ Estado alterado com sucesso!", chat_id, call.message.message_id)

    elif dados.startswith("d_idx_"):
        i = int(dados.split("_")[2])
        indexadores = carregar_indexadores()
        removido = indexadores.pop(i)
        guardar_indexadores(indexadores)
        bot.edit_message_text(f"🗑️ Indexador '{removido['nome']}' removido!", chat_id, call.message.message_id)

    elif dados.startswith("e_idx_"):
        i = int(dados.split("_")[2])
        msg = bot.send_message(chat_id, "Envia o novo link base para este indexador (ex: https://froststream.cloutteam.com):")
        bot.register_next_step_handler(msg, salvar_url_idx, i)

    elif dados == "add_idx_1":
        msg = bot.send_message(chat_id, "Envia o nome do novo indexador:")
        bot.register_next_step_handler(msg, passo2_add_idx)

    elif dados.startswith("lote_"):
        escopo = dados.split("_")[1]
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("🌟 Automático", callback_data=f"runlote_{escopo}_auto"))
        for i, idx in enumerate(carregar_indexadores()):
            if idx.get("ativo", True): markup.add(InlineKeyboardButton(f"✅ {idx['nome']}", callback_data=f"runlote_{escopo}_{i}"))
        bot.edit_message_text("👇 Escolhe qual indexador queres usar para atualizar tudo:", chat_id, call.message.message_id, reply_markup=markup)

    elif dados.startswith("runlote_"):
        escopo, idx_esc = dados.split("_")[1], dados.split("_")[2]
        msg_progresso = bot.edit_message_text("⏳ A iniciar lote...", chat_id, call.message.message_id)
        threading.Thread(target=tarefa_renovacao_lote, args=(chat_id, msg_progresso.message_id, escopo, idx_esc)).start()

    elif dados == "hist_menu_filmes":
        filmes = list(carregar_historico().get("filmes", {}).keys())
        if not filmes: return bot.edit_message_text("❌ Sem filmes.", chat_id, call.message.message_id)
        markup = InlineKeyboardMarkup(row_width=1)
        for i, f in enumerate(filmes[:50]): markup.add(InlineKeyboardButton(f"🎬 {f}", callback_data=f"lib_m_{i}"))
        bot.edit_message_text("🎬 *Os teus Filmes:*", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados == "hist_menu_series":
        series = list(carregar_historico().get("series", {}).keys())
        if not series: return bot.edit_message_text("❌ Sem séries.", chat_id, call.message.message_id)
        markup = InlineKeyboardMarkup(row_width=1)
        for i, s in enumerate(series[:50]): markup.add(InlineKeyboardButton(f"📺 {s}", callback_data=f"lib_s_{i}"))
        bot.edit_message_text("📺 *As tuas Séries:*", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("lib_m_"):
        idx = int(dados.split("_")[2])
        titulo = list(carregar_historico().get("filmes", {}).keys())[idx]
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("🔄 Renovar Link", callback_data=f"ren_m_{idx}"), InlineKeyboardButton("🗑️ Apagar Filme", callback_data=f"del_m_{idx}"))
        bot.edit_message_text(f"🎬 *{titulo}*\nO que fazer?", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("del_m_"):
        idx = int(dados.split("_")[2])
        hist = carregar_historico()
        titulo = list(hist.get("filmes", {}).keys())[idx]
        shutil.rmtree(os.path.join(PASTA_BASE, "FILMES", titulo), ignore_errors=True)
        del hist["filmes"][titulo]
        guardar_historico(hist)
        bot.edit_message_text(f"🗑️ *Filme Apagado!*\n🎬 {titulo}", chat_id, call.message.message_id, parse_mode="Markdown")

    elif dados.startswith("lib_s_"):
        idx = int(dados.split("_")[2])
        titulo = list(carregar_historico().get("series", {}).keys())[idx]
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("🔄 Renovar Séries", callback_data=f"ren_s_{idx}"), InlineKeyboardButton("🗑️ Apagar", callback_data=f"del_s_{idx}"))
        bot.edit_message_text(f"📺 *{titulo}*\nO que fazer?", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("del_s_"):
        idx = int(dados.split("_")[2])
        hist = carregar_historico()
        titulo = list(hist.get("series", {}).keys())[idx]
        shutil.rmtree(os.path.join(PASTA_BASE, "SERIES", titulo), ignore_errors=True)
        del hist["series"][titulo]
        guardar_historico(hist)
        bot.edit_message_text(f"🗑️ *Série Apagada!*\n📺 {titulo}", chat_id, call.message.message_id, parse_mode="Markdown")

    elif dados.startswith("ren_m_"):
        idx = int(dados.split("_")[2])
        titulo = list(carregar_historico().get("filmes", {}).keys())[idx]
        res = requests.get(f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={re.sub(r' \(\d{4}\)$', '', titulo)}&language=pt-PT").json().get("results", [])
        if res: callback_handler(call._replace(data=f"m_{res[0]['id']}"))
    
    elif dados.startswith("ren_s_"):
        idx = int(dados.split("_")[2])
        titulo = list(carregar_historico().get("series", {}).keys())[idx]
        res = requests.get(f"https://api.themoviedb.org/3/search/tv?api_key={TMDB_API_KEY}&query={re.sub(r' \(\d{4}\)$', '', titulo)}&language=pt-PT").json().get("results", [])
        if res: callback_handler(call._replace(data=f"s_{res[0]['id']}"))

    elif dados.startswith("m_"):
        tmdb_id = dados.split("_")[1]
        imdb_id = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids").json().get('external_ids', {}).get('imdb_id')
        fontes = verificar_fontes_ativas("movie", imdb_id)
        if not fontes: return bot.edit_message_text("❌ Sem indexadores com áudio PT-BR para este filme.", chat_id, call.message.message_id)
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("🌟 Automático", callback_data=f"run_m_{tmdb_id}_auto"))
        for f in fontes: markup.add(InlineKeyboardButton(f"✅ {f['nome']}", callback_data=f"run_m_{tmdb_id}_{f['id']}"))
        bot.edit_message_text("👇 Filme encontrado! Escolhe a fonte:", chat_id, call.message.message_id, reply_markup=markup)

    elif dados.startswith("run_m_"):
        tmdb_id, idx_esc = dados.split("_")[2:4]
        res = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids&language=pt-PT").json()
        imdb_id, id_nome = res.get('external_ids', {}).get('imdb_id'), f"{limpar_nome(res.get('title', ''))} ({res.get('release_date', '0000')[:4]})"
        link, idioma, nome_idx = obter_stream_com_idioma("movie", imdb_id, indexador_alvo=idx_esc)
        if link:
            pasta = os.path.join(PASTA_BASE, "FILMES", id_nome)
            os.makedirs(pasta, exist_ok=True)
            with open(os.path.join(pasta, f"{id_nome}.strm"), "w", encoding="utf-8") as f: f.write(link)
            registrar_historico("movie", id_nome, nome_idx)
            bot.edit_message_text(f"✅ *Filme Pronto!*\n🎬 {id_nome}\n🔊 {idioma}\n📡 {nome_idx}", chat_id, call.message.message_id, parse_mode="Markdown")
        else: bot.edit_message_text("❌ Nenhum link PT-BR funcional encontrado.", chat_id, call.message.message_id)

    elif dados.startswith("s_"):
        tmdb_id = dados.split("_")[1]
        temporadas = [t for t in requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}").json().get('seasons', []) if t['season_number'] > 0]
        markup = InlineKeyboardMarkup(row_width=2)
        for t in temporadas: markup.add(InlineKeyboardButton(f"T{t['season_number']}", callback_data=f"t_{tmdb_id}_{t['season_number']}_{t['episode_count']}"))
        bot.edit_message_text("📺 Escolhe a temporada:", chat_id, call.message.message_id, reply_markup=markup)

    elif dados.startswith("t_"):
        tmdb_id, season_num, ep_count = dados.split("_")[1:4]
        imdb_id = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids").json().get('external_ids', {}).get('imdb_id')
        fontes = verificar_fontes_ativas("series", imdb_id, season_num, 1)
        if not fontes: return bot.edit_message_text(f"❌ Sem indexadores com fontes PT-BR para a T{season_num}.", chat_id, call.message.message_id)
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("🌟 Automático", callback_data=f"run_s_{tmdb_id}_{season_num}_{ep_count}_auto"))
        for f in fontes: markup.add(InlineKeyboardButton(f"✅ {f['nome']}", callback_data=f"run_s_{tmdb_id}_{season_num}_{ep_count}_{f['id']}"))
        bot.edit_message_text("👇 Escolhe a fonte:", chat_id, call.message.message_id, reply_markup=markup)

    elif dados.startswith("run_s_"):
        tmdb_id, season_num, ep_count, idx_esc = dados.split("_")[2:6]
        res = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids").json()
        imdb_id, id_nome = res.get('external_ids', {}).get('imdb_id'), f"{limpar_nome(res.get('name', ''))} ({res.get('first_air_date', '0000')[:4]})"
        pasta_temp = os.path.join(PASTA_BASE, "SERIES", id_nome, f"Season {int(season_num):02d}")
        os.makedirs(pasta_temp, exist_ok=True)
        sucessos = 0
        for ep in range(1, int(ep_count) + 1):
            link, _, nome_idx = obter_stream_com_idioma("series", imdb_id, int(season_num), ep, indexador_alvo=idx_esc)
            if link:
                with open(os.path.join(pasta_temp, f"{id_nome} S{int(season_num):02d}E{ep:02d}.strm"), "w", encoding="utf-8") as f: f.write(link)
                registrar_historico("series", id_nome, nome_idx, int(season_num), ep)
                sucessos += 1
        bot.edit_message_text(f"✅ *Concluído!*\n📺 {id_nome} - S{int(season_num):02d}\n📥 {sucessos}/{ep_count} links PT-BR.\n📡 {nome_idx}", chat_id, call.message.message_id, parse_mode="Markdown")

def salvar_url_idx(message, idx_id):
    nova_url = message.text.strip().replace("/manifest.json", "")
    indexadores = carregar_indexadores()
    indexadores[idx_id]["url"] = nova_url
    indexadores[idx_id]["strikes"] = 0
    indexadores[idx_id]["ativo"] = True
    guardar_indexadores(indexadores)
    bot.send_message(message.chat.id, "✅ URL do indexador atualizada!")

def passo2_add_idx(message):
    nome = message.text.strip()
    msg = bot.send_message(message.chat.id, f"Nome '{nome}' guardado. Envia a URL base (sem /manifest.json):")
    bot.register_next_step_handler(msg, finalizar_add_idx, nome)

def finalizar_add_idx(message, nome):
    url = message.text.strip().replace("/manifest.json", "")
    indexadores = carregar_indexadores()
    indexadores.append({"nome": nome, "url": url, "strikes": 0, "ativo": True})
    guardar_indexadores(indexadores)
    bot.send_message(message.chat.id, f"✅ Novo indexador '{nome}' criado e ativo!")

# ==========================================
# INICIAR O BOT
# ==========================================
if __name__ == "__main__":
    os.makedirs(os.path.join(PASTA_BASE, "FILMES"), exist_ok=True)
    os.makedirs(os.path.join(PASTA_BASE, "SERIES"), exist_ok=True)
    while True:
        try: bot.polling(none_stop=True, timeout=60)
        except Exception as e: time.sleep(5)