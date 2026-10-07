import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import requests
import os
import re
import json
import time
import shutil
import threading
import subprocess

# ==========================================
# CONFIGURAÇÕES DEFINITIVAS
# ==========================================
TELEGRAM_TOKEN = "8994962973:AAHSi_9Pu952FyaA6mc_Ugcqls9_htrkze0"
TMDB_API_KEY = "3755e8749d79c3d9b395e2041c281a52"
ADMIN_ID = 1289593084 

bot = telebot.TeleBot(TELEGRAM_TOKEN)

DIRETORIO_SCRIPT = os.path.dirname(os.path.abspath(__file__))
PASTA_BASE = "/DATA/Media" 
HISTORICO_ARQUIVO = os.path.join(DIRETORIO_SCRIPT, "historico.json")
INDEXADORES_ARQUIVO = os.path.join(DIRETORIO_SCRIPT, "indexadores.json")

# Limite máximo da pasta de cache (em GB)
LIMITE_CACHE_GB = 30 

SESSAO_QUALIDADES = {}

# ==========================================
# GESTÃO DE DADOS & CACHE AUTOMÁTICA
# ==========================================
def carregar_indexadores():
    if os.path.exists(INDEXADORES_ARQUIVO):
        try:
            with open(INDEXADORES_ARQUIVO, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    padrao = [
        {"nome": "FrostStream", "url": "https://froststream.cloutteam.com", "ativo": True},
        {"nome": "MagnetFlix", "url": "https://magnetflix.magnetbr.online/qualities%3D4k%2C1080p%2C720p%2Csd%7Caudio%3Ddublado%2Clegendado%7Ccatalogs%3Dpopulares_movie%2Cpopulares_series%2Crecentes_servidor_movie%2Crecentes_servidor_series%2Cemalta_movie%2Cemalta_series", "ativo": True},
        {"nome": "FenixFlix", "url": "https://fenixflix.fenixhub.online", "ativo": True}
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

def registrar_historico(tipo, titulo, indexador, tamanho_bytes=0):
    hist = carregar_historico()
    if tipo == "movie":
        hist["filmes"][titulo] = {"indexador": indexador, "acesso": time.time(), "tamanho": tamanho_bytes}
    guardar_historico(hist)

def limpar_nome(nome):
    return re.sub(r'[\\/*?:"<>|]', "", str(nome))

def calcular_tamanho_pasta(caminho):
    total = 0
    for root, dirs, files in os.walk(caminho):
        for f in files:
            fp = os.path.join(root, f)
            if not os.path.islink(fp):
                total += os.path.getsize(fp)
    return total

def limpar_cache_disco(tamanho_novo_bytes=0):
    """
    Verifica o tamanho total da pasta /DATA/Media.
    Se ultrapassar LIMITE_CACHE_GB, apaga os ficheiros mais antigos (LRU).
    """
    limite_bytes = LIMITE_CACHE_GB * 1024 * 1024 * 1024
    tamanho_atual = calcular_tamanho_pasta(PASTA_BASE)
    
    if (tamanho_atual + tamanho_novo_bytes) <= limite_bytes:
        return

    hist = carregar_historico()
    # Junta filmes para ordenar por tempo de último acesso
    itens = []
    for titulo, info in hist.get("filmes", {}).items():
        pasta = os.path.join(PASTA_BASE, "FILMES", titulo)
        if os.path.exists(pasta):
            itens.append(("movie", titulo, pasta, info.get("acesso", 0)))

    # Ordena dos mais antigos para os mais recentes
    itens.sort(key=lambda x: x[3])

    for tipo, titulo, caminho_pasta, _ in itens:
        if (calcular_tamanho_pasta(PASTA_BASE) + tamanho_novo_bytes) <= limite_bytes:
            break
        shutil.rmtree(caminho_pasta, ignore_errors=True)
        if tipo == "movie" and titulo in hist["filmes"]:
            del hist["filmes"][titulo]
    
    guardar_historico(hist)

# ==========================================
# MOTOR DE BUSCA & DOWNLOAD COM ARIA2C
# ==========================================
def extrair_qualidade(texto):
    txt = texto.lower()
    if "4k" in txt or "2160p" in txt: return "4K / 2160p 🌟"
    elif "1080p" in txt or "fhd" in txt: return "1080p Full HD 🎬"
    elif "720p" in txt or "hd" in txt: return "720p HD ⚡"
    return "Qualidade Padrão 📺"

def verificar_fontes_ativas(tipo, imdb_id):
    fontes_encontradas = []
    headers = {"User-Agent": "Mozilla/5.0"}
    indexadores = carregar_indexadores()
    for idx_pos, idx in enumerate(indexadores):
        if not idx.get("ativo", True): continue
        url = f"{idx['url']}/stream/movie/{imdb_id}.json" if tipo == "movie" else f"{idx['url']}/stream/series/{imdb_id}.json"
        try:
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 200:
                streams = res.json().get("streams", [])
                validos = [s for s in streams if "url" in s and s["url"].startswith("http")]
                if validos: fontes_encontradas.append({"id": idx_pos, "nome": idx["nome"]})
        except: continue
    return fontes_encontradas

def obter_streams_por_qualidade(tipo, imdb_id, indexador_alvo="auto"):
    headers = {"User-Agent": "Mozilla/5.0"}
    indexadores = carregar_indexadores()

    for idx_pos, idx in enumerate(indexadores):
        if indexador_alvo != "auto" and str(idx_pos) != str(indexador_alvo): continue
        if not idx.get("ativo", True) and indexador_alvo == "auto": continue
        
        url = f"{idx['url']}/stream/movie/{imdb_id}.json"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                streams = res.json().get("streams", [])
                validos = [s for s in streams if "url" in s and s["url"].startswith("http")]
                if validos:
                    opcoes_qualidade = {}
                    for s in validos:
                        txt_label = (s.get("title", "") + " " + s.get("name", ""))
                        qual = extrair_qualidade(txt_label)
                        if qual not in opcoes_qualidade:
                            opcoes_qualidade[qual] = {"url": s["url"], "indexador": idx["nome"], "info": txt_label}
                    return opcoes_qualidade, idx["nome"]
        except: continue
    return {}, None

def executar_download_aria2(url, pasta_destino, nome_arquivo, chat_id, message_id):
    """
    Executa o download direto em background via aria2c para a pasta local da VPS
    """
    limpar_cache_disco()
    os.makedirs(pasta_destino, exist_ok=True)
    caminho_final = os.path.join(pasta_destino, nome_arquivo)

    cmd = [
        "aria2c",
        "-x", "8", "-s", "8",
        "-d", pasta_destino,
        "-o", nome_arquivo,
        "--allow-overwrite=true",
        url
    ]

    try:
        bot.edit_message_text(f"⏳ *A iniciar download real em background...*\n📁 Ficheiro: `{nome_arquivo}`", chat_id, message_id, parse_mode="Markdown")
        processo = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        
        # Aguarda o término do download
        stdout, _ = processo.communicate()

        if processo.returncode == 0 and os.path.exists(caminho_final):
            tam_mb = round(os.path.getsize(caminho_final) / (1024 * 1024), 2)
            registrar_historico("movie", nome_arquivo.replace(".mp4", "").replace(".mkv", ""), "aria2c", os.path.getsize(caminho_final))
            bot.edit_message_text(f"🎉 *Download Concluído com Sucesso!*\n🎬 `{nome_arquivo}`\n📦 Tamanho: *{tam_mb} MB*\n\n👉 *Já disponível no Jellyfin em Direct Play!*", chat_id, message_id, parse_mode="Markdown")
        else:
            bot.edit_message_text("❌ Falha ao descarregar o ficheiro real da fonte.", chat_id, message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Erro durante o download: {e}", chat_id, message_id)

# ==========================================
# MENUS PRINCIPAIS
# ==========================================
def menu_principal():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("🎬 Novo Filme"), KeyboardButton("📚 Minha Biblioteca"),
        KeyboardButton("📊 Status da Cache"), KeyboardButton("⚙️ Gerir Indexadores")
    )
    return markup

@bot.message_handler(commands=['start'])
def comando_start(message):
    if ADMIN_ID and message.chat.id != ADMIN_ID: return bot.send_message(message.chat.id, "⛔ Acesso negado.")
    bot.send_message(message.chat.id, "🍿 *Jellyfin Bot com Download Local Ativo!*", reply_markup=menu_principal(), parse_mode="Markdown")

@bot.message_handler(func=lambda msg: True)
def escutar_botoes(message):
    if ADMIN_ID and message.chat.id != ADMIN_ID: return
    texto = message.text
    if texto == "🎬 Novo Filme":
        msg = bot.send_message(message.chat.id, "Escreve o nome do filme:")
        bot.register_next_step_handler(msg, pesquisar_tmdb, "movie")
    elif texto == "📊 Status da Cache":
        tam_bytes = calcular_tamanho_pasta(PASTA_BASE)
        tam_gb = round(tam_bytes / (1024 * 1024 * 1024), 2)
        txt = f"📊 *ESTADO DA CACHE LOCAL*\n\n💾 Espaço Ocupado: *{tam_gb} GB* / {LIMITE_CACHE_GB} GB\n📁 Localização: `/DATA/Media`\n\n*Regra:* Quando atingir {LIMITE_CACHE_GB} GB, o bot apaga automaticamente os filmes mais antigos assistidos."
        bot.send_message(message.chat.id, txt, parse_mode="Markdown")
    elif texto == "📚 Minha Biblioteca":
        filmes = list(carregar_historico().get("filmes", {}).keys())
        if not filmes: return bot.send_message(message.chat.id, "❌ Sem filmes descarregados na cache.")
        markup = InlineKeyboardMarkup(row_width=1)
        for i, f in enumerate(filmes[:50]): markup.add(InlineKeyboardButton(f"🎬 {f}", callback_data=f"lib_m_{i}"))
        bot.send_message(message.chat.id, "🎬 *Filmes em Cache:*", reply_markup=markup, parse_mode="Markdown")

def pesquisar_tmdb(message, tipo_busca):
    query = message.text
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}&language=pt-PT"
    try:
        res = requests.get(url).json().get("results", [])[:5]
        if not res: return bot.send_message(message.chat.id, "❌ Nenhum resultado encontrado.")
        markup = InlineKeyboardMarkup(row_width=1)
        for r in res:
            titulo = r.get('title')
            ano = str(r.get('release_date', 'N/A'))[:4]
            markup.add(InlineKeyboardButton(f"🎬 {titulo} ({ano})", callback_data=f"m_{r['id']}"))
        bot.send_message(message.chat.id, "👇 Escolhe o filme:", reply_markup=markup)
    except Exception as e: bot.send_message(message.chat.id, f"❌ Erro: {e}")

# ==========================================
# CALLBACKS & DOWNLOAD LOCAL
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    bot.answer_callback_query(call.id)
    chat_id, dados = call.message.chat.id, call.data

    if dados.startswith("m_"):
        tmdb_id = dados.split("_")[1]
        imdb_id = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids").json().get('external_ids', {}).get('imdb_id')
        fontes = verificar_fontes_ativas("movie", imdb_id)
        if not fontes: return bot.edit_message_text("❌ Sem indexadores ativos para este filme.", chat_id, call.message.message_id)
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("🌟 Automático (Melhor Qualidade)", callback_data=f"q_m_{tmdb_id}_auto"))
        for f in fontes: markup.add(InlineKeyboardButton(f"✅ {f['nome']}", callback_data=f"q_m_{tmdb_id}_{f['id']}"))
        bot.edit_message_text("👇 Filme encontrado! Escolhe a fonte:", chat_id, call.message.message_id, reply_markup=markup)

    elif dados.startswith("q_m_"):
        tmdb_id, idx_esc = dados.split("_")[2:4]
        res = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids&language=pt-PT").json()
        imdb_id = res.get('external_ids', {}).get('imdb_id')
        
        opcoes_q, nome_idx = obter_streams_por_qualidade("movie", imdb_id, indexador_alvo=idx_esc)
        if not opcoes_q:
            return bot.edit_message_text("❌ Não foram encontradas qualidades válidas.", chat_id, call.message.message_id)

        chave_sessao = f"m_{chat_id}_{tmdb_id}"
        SESSAO_QUALIDADES[chave_sessao] = {"qualidades": opcoes_q, "nome_idx": nome_idx, "tmdb_id": tmdb_id}

        markup = InlineKeyboardMarkup(row_width=1)
        for i, (nome_qual, item) in enumerate(opcoes_q.items()):
            markup.add(InlineKeyboardButton(f"📥 Descarregar {nome_qual}", callback_data=f"runq_m_{chave_sessao}_{i}"))

        bot.edit_message_text(f"⚙️ *Escolhe a Qualidade para Download Local*\n📡 Fonte: {nome_idx}", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("runq_m_"):
        partes = dados.split("_")
        chave_sessao = f"{partes[2]}_{partes[3]}_{partes[4]}"
        idx_q = int(partes[5])

        dados_sessao = SESSAO_QUALIDADES.get(chave_sessao)
        if not dados_sessao:
            return bot.edit_message_text("❌ Sessão expirada. Tenta pesquisar novamente.", chat_id, call.message.message_id)

        qual_nome = list(dados_sessao["qualidades"].keys())[idx_q]
        item_escolhido = dados_sessao["qualidades"][qual_nome]

        res = requests.get(f"https://api.themoviedb.org/3/movie/{dados_sessao['tmdb_id']}?api_key={TMDB_API_KEY}&language=pt-PT").json()
        id_nome = f"{limpar_nome(res.get('title', ''))} ({res.get('release_date', '0000')[:4]})"

        pasta_destino = os.path.join(PASTA_BASE, "FILMES", id_nome)
        
        # Determina a extensão (.mp4 por padrão para Direct Play)
        ext = ".mp4" if ".mp4" in item_escolhido["url"].lower() else ".mkv"
        nome_arquivo = f"{id_nome}{ext}"

        # Dispara o download em thread separada para não travar o bot
        threading.Thread(
            target=executar_download_aria2,
            args=(item_escolhido["url"], pasta_destino, nome_arquivo, chat_id, call.message.message_id)
        ).start()

    elif dados.startswith("lib_m_"):
        idx = int(dados.split("_")[2])
        titulo = list(carregar_historico().get("filmes", {}).keys())[idx]
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("🗑️ Apagar da Cache", callback_data=f"del_m_{idx}"))
        bot.edit_message_text(f"🎬 *{titulo}*\nO ficheiro está descarregado localmente na tua VPS.", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("del_m_"):
        idx = int(dados.split("_")[2])
        hist = carregar_historico()
        titulo = list(hist.get("filmes", {}).keys())[idx]
        shutil.rmtree(os.path.join(PASTA_BASE, "FILMES", titulo), ignore_errors=True)
        del hist["filmes"][titulo]
        guardar_historico(hist)
        bot.edit_message_text(f"🗑️ *Filme Apagado da Cache!*\n🎬 {titulo}", chat_id, call.message.message_id, parse_mode="Markdown")

if __name__ == "__main__":
    os.makedirs(os.path.join(PASTA_BASE, "FILMES"), exist_ok=True)
    os.makedirs(os.path.join(PASTA_BASE, "SERIES"), exist_ok=True)
    while True:
        try: bot.polling(none_stop=True, timeout=60)
        except Exception as e: time.sleep(5)