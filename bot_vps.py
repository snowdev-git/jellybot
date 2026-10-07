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

# CAMINHO EXATO DO TERCEIRO PRINT: /DATA/Media
PASTA_BASE = "/DATA/Media"
HISTORICO_ARQUIVO = "/home/ubuntu/jellybot/historico.json"
INDEXADORES_ARQUIVO = "/home/ubuntu/jellybot/indexadores.json"

SESSAO_OPCOES = {}

# ==========================================
# GESTÃO DE DADOS & DISCO
# ==========================================
def carregar_indexadores():
    if os.path.exists(INDEXADORES_ARQUIVO):
        try:
            with open(INDEXADORES_ARQUIVO, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    padrao = [
        {"nome": "FrostStream", "url": "https://froststream.cloutteam.com", "ativo": True},
        {"nome": "MagnetFlix", "url": "https://magnetflix.magnetbr.online", "ativo": True},
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
    cat = "filmes" if tipo == "movie" else "series"
    hist[cat][titulo] = {"indexador": indexador, "acesso": time.time(), "tamanho": tamanho_bytes}
    guardar_historico(hist)

def limpar_nome(nome):
    return re.sub(r'[\\/*?:"<>|]', "", str(nome)).strip()

def obter_info_disco():
    try:
        total, used, free = shutil.disk_usage(PASTA_BASE)
        free_gb = round(free / (1024**3), 2)
        usado_gb = round(used / (1024**3), 2)
        return free_gb, usado_gb
    except:
        return 0.0, 0.0

def extrair_qualidade(texto):
    txt = texto.lower()
    termos_dub = ["dub", "dublado", "dual", "pt-br", "ptbr", "br", "pt", "nacional", "multi", "latino"]
    is_dub = any(termo in txt for termo in termos_dub)
    audio = "DUBLADO 🇧🇷" if is_dub else "LEGENDADO 🔤"

    if "4k" in txt or "2160p" in txt:
        return f"4K 🌟 ({audio})"
    elif "1080p" in txt or "fhd" in txt:
        return f"1080p FHD 🎬 ({audio})"
    elif "720p" in txt or "hd" in txt:
        return f"720p HD ⚡ ({audio})"
    return f"SD 📺 ({audio})"

# ==========================================
# MOTOR DE BUSCA
# ==========================================
def buscar_todas_as_opcoes(tipo, imdb_id, season=None, episode=None):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    indexadores = carregar_indexadores()
    resultados = []

    for idx in indexadores:
        if not idx.get("ativo", True): continue
        if tipo == "movie":
            url = f"{idx['url']}/stream/movie/{imdb_id}.json"
        else:
            url = f"{idx['url']}/stream/series/{imdb_id}:{season}:{episode}.json"

        try:
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 200:
                streams = res.json().get("streams", [])
                validos = [s for s in streams if "url" in s and s["url"].startswith("http")]
                
                for s in validos:
                    txt_label = (s.get("title", "") + " " + s.get("name", ""))
                    qual = extrair_qualidade(txt_label)

                    resultados.append({
                        "indexador": idx["nome"],
                        "qualidade": qual,
                        "url": s["url"],
                        "info": txt_label
                    })
        except Exception as e:
            print(f"   └─ Falha em {idx['nome']}: {e}")

    return resultados

# ==========================================
# MOTOR DE DOWNLOAD COMPLETO
# ==========================================
def monitorar_progresso(caminho_final, chat_id, message_id, stop_event):
    ultimo_tamanho = 0
    while not stop_event.is_set():
        time.sleep(4)
        if os.path.exists(caminho_final):
            tamanho_atual = os.path.getsize(caminho_final)
            if tamanho_atual != ultimo_tamanho and tamanho_atual > 0:
                tam_mb = round(tamanho_atual / (1024 * 1024), 2)
                try:
                    bot.edit_message_text(
                        f"⏳ *A descarregar para `/DATA/Media`...*\n📁 Ficheiro: `{os.path.basename(caminho_final)}`\n📦 *Baixado:* `{tam_mb} MB`",
                        chat_id, message_id, parse_mode="Markdown"
                    )
                except: pass
                ultimo_tamanho = tamanho_atual

def executar_download(url, pasta_destino, nome_arquivo, tipo_mídia, chat_id, message_id):
    try:
        os.makedirs(pasta_destino, exist_ok=True)
        caminho_final = os.path.join(pasta_destino, nome_arquivo)
        caminho_aria2 = caminho_final + ".aria2"

        bot.edit_message_text(f"⏳ *A iniciar conexão na VPS...*\n📁 Ficheiro: `{nome_arquivo}`", chat_id, message_id, parse_mode="Markdown")

        stop_event = threading.Event()
        t_monitor = threading.Thread(target=monitorar_progresso, args=(caminho_final, chat_id, message_id, stop_event))
        t_monitor.start()

        cmd = [
            "aria2c",
            "-x", "16", "-s", "16", "-k", "1M",
            "--connect-timeout=20",
            "--timeout=60",
            "--max-tries=5",
            "-d", pasta_destino,
            "-o", nome_arquivo,
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "--check-certificate=false",
            "--allow-overwrite=true",
            url
        ]

        processo = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        stdout, _ = processo.communicate()

        stop_event.set()
        t_monitor.join()

        # O download só é realmente VÁLIDO se o processo retornar 0 E o ficheiro .aria2 NÃO existir mais
        if processo.returncode == 0 and os.path.exists(caminho_final) and not os.path.exists(caminho_aria2) and os.path.getsize(caminho_final) > 5000000:
            tam_bytes = os.path.getsize(caminho_final)
            tam_gb = round(tam_bytes / (1024 ** 3), 2)
            tam_mb = round(tam_bytes / (1024 * 1024), 2)
            tam_formatado = f"{tam_gb} GB" if tam_gb >= 1 else f"{tam_mb} MB"

            registrar_historico(tipo_mídia, nome_arquivo, "aria2c", tam_bytes)
            livre_gb, _ = obter_info_disco()

            msg_sucesso = (
                f"🎉 *Download Concluído com Sucesso 100%!*\n\n"
                f"🎬 *Ficheiro:* `{nome_arquivo}`\n"
                f"📂 *Guardado em:* `/DATA/Media`\n"
                f"📦 *Tamanho Final:* *{tam_formatado}*\n"
                f"💾 *Espaço Livre em Disco:* *{livre_gb} GB*\n\n"
                f"👉 *Já podes dar Play no Jellyfin!*"
            )
            bot.edit_message_text(msg_sucesso, chat_id, message_id, parse_mode="Markdown")
        else:
            # Se falhou ou ficou pela metade, elimina o ficheiro estragado
            if os.path.exists(caminho_final):
                try: os.remove(caminho_final)
                except: pass
            if os.path.exists(caminho_aria2):
                try: os.remove(caminho_aria2)
                except: pass

            bot.edit_message_text(f"❌ *O download da fonte travou ou ficou incompleto.*\n\nPor favor, tenta escolher outra opção de qualidade/servidor na lista.", chat_id, message_id, parse_mode="Markdown")

    except Exception as err_global:
        print(f"❌ Erro global: {err_global}")

# ==========================================
# MENUS & HANDLERS
# ==========================================
def menu_principal():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("🎬 Novo Filme"), KeyboardButton("📺 Nova Série"),
        KeyboardButton("🗑️ Gerenciar/Apagar"), KeyboardButton("📊 Espaço no Disco")
    )
    return markup

@bot.message_handler(commands=['start'])
def comando_start(message):
    livre_gb, _ = obter_info_disco()
    bot.send_message(
        message.chat.id, 
        f"🍿 *Jellyfin Bot Ativo na VPS!*\n💾 Espaço Livre em `/DATA/Media`: *{livre_gb} GB*\n\nEscolha uma opção no menu abaixo:", 
        reply_markup=menu_principal(), 
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda msg: True)
def escutar_botoes(message):
    texto = message.text
    if texto == "🎬 Novo Filme":
        msg = bot.send_message(message.chat.id, "Escreva o nome do filme:")
        bot.register_next_step_handler(msg, pesquisar_tmdb, "movie")
    elif texto == "📺 Nova Série":
        msg = bot.send_message(message.chat.id, "Escreva o nome da série:")
        bot.register_next_step_handler(msg, pesquisar_tmdb, "tv")
    elif texto == "📊 Espaço no Disco":
        livre_gb, usado_gb = obter_info_disco()
        bot.send_message(message.chat.id, f"📊 *Informações do Disco (`/DATA/Media`):*\n\n💾 Espaço Livre: *{livre_gb} GB*\n📁 Espaço Usado: *{usado_gb} GB*", parse_mode="Markdown")
    elif texto == "🗑️ Gerenciar/Apagar":
        menu_gerenciar_media(message.chat.id)

def menu_gerenciar_media(chat_id):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🎬 Apagar Filmes", callback_data="mgr_filmes"),
        InlineKeyboardButton("📺 Apagar Séries", callback_data="mgr_series")
    )
    bot.send_message(chat_id, "🗑️ *Gerenciar Armazenamento*\nEscolha a categoria que deseja gerenciar:", reply_markup=markup, parse_mode="Markdown")

def pesquisar_tmdb(message, tipo):
    query = message.text
    url = f"https://api.themoviedb.org/3/search/{tipo}?api_key={TMDB_API_KEY}&query={query}&language=pt-PT"
    try:
        res = requests.get(url).json().get("results", [])[:5]
        if not res: return bot.send_message(message.chat.id, "❌ Nenhum resultado encontrado.")
        
        markup = InlineKeyboardMarkup(row_width=1)
        for r in res:
            titulo = r.get('title') if tipo == 'movie' else r.get('name')
            data = r.get('release_date') if tipo == 'movie' else r.get('first_air_date')
            ano = str(data)[:4] if data else "N/A"
            cb = f"m_{r['id']}" if tipo == 'movie' else f"s_{r['id']}"
            markup.add(InlineKeyboardButton(f"{'🎬' if tipo=='movie' else '📺'} {titulo} ({ano})", callback_data=cb))
            
        bot.send_message(message.chat.id, f"👇 Selecione o {'filme' if tipo=='movie' else 'série'}:", reply_markup=markup)
    except Exception as e: bot.send_message(message.chat.id, f"❌ Erro: {e}")

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    bot.answer_callback_query(call.id)
    chat_id, dados = call.message.chat.id, call.data

    if dados == "mgr_filmes":
        pasta_f = os.path.join(PASTA_BASE, "FILMES")
        itens = [d for d in os.listdir(pasta_f) if os.path.isdir(os.path.join(pasta_f, d))] if os.path.exists(pasta_f) else []
        if not itens: return bot.edit_message_text("❌ Nenhum filme salvo na cache.", chat_id, call.message.message_id)
        
        markup = InlineKeyboardMarkup(row_width=1)
        for idx, f in enumerate(itens[:30]):
            markup.add(InlineKeyboardButton(f"🗑️ {f}", callback_data=f"del_f_{idx}"))
        bot.edit_message_text("🎬 *Clique no filme que deseja apagar:*", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("del_f_"):
        idx = int(dados.split("_")[2])
        pasta_f = os.path.join(PASTA_BASE, "FILMES")
        itens = [d for d in os.listdir(pasta_f) if os.path.isdir(os.path.join(pasta_f, d))]
        if idx < len(itens):
            alvo = itens[idx]
            shutil.rmtree(os.path.join(pasta_f, alvo), ignore_errors=True)
            bot.edit_message_text(f"✅ *Filme apagado com sucesso!*\n🎬 `{alvo}`", chat_id, call.message.message_id, parse_mode="Markdown")

    elif dados == "mgr_series":
        pasta_s = os.path.join(PASTA_BASE, "SERIES")
        itens = [d for d in os.listdir(pasta_s) if os.path.isdir(os.path.join(pasta_s, d))] if os.path.exists(pasta_s) else []
        if not itens: return bot.edit_message_text("❌ Nenhuma série salva na cache.", chat_id, call.message.message_id)
        
        markup = InlineKeyboardMarkup(row_width=1)
        for idx, s in enumerate(itens[:30]):
            markup.add(InlineKeyboardButton(f"🗑️ {s}", callback_data=f"del_s_{idx}"))
        bot.edit_message_text("📺 *Clique na série que deseja apagar:*", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("del_s_"):
        idx = int(dados.split("_")[2])
        pasta_s = os.path.join(PASTA_BASE, "SERIES")
        itens = [d for d in os.listdir(pasta_s) if os.path.isdir(os.path.join(pasta_s, d))]
        if idx < len(itens):
            alvo = itens[idx]
            shutil.rmtree(os.path.join(pasta_s, alvo), ignore_errors=True)
            bot.edit_message_text(f"✅ *Série apagada com sucesso!*\n📺 `{alvo}`", chat_id, call.message.message_id, parse_mode="Markdown")

    elif dados.startswith("m_"):
        tmdb_id = dados.split("_")[1]
        bot.edit_message_text("🔍 Consultando indexadores... Aguarde.", chat_id, call.message.message_id)
        
        imdb_id = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids").json().get('external_ids', {}).get('imdb_id')
        if not imdb_id: return bot.edit_message_text("❌ Sem IMDB ID.", chat_id, call.message.message_id)

        opcoes = buscar_todas_as_opcoes("movie", imdb_id)
        if not opcoes: return bot.edit_message_text("❌ Nenhum link encontrado.", chat_id, call.message.message_id)

        chave = f"m_{chat_id}_{tmdb_id}"
        SESSAO_OPCOES[chave] = {"opcoes": opcoes, "tmdb_id": tmdb_id, "tipo": "movie"}

        markup = InlineKeyboardMarkup(row_width=1)
        for i, opt in enumerate(opcoes[:5]):
            btn_txt = f"[{opt['indexador']}] {opt['qualidade']}"
            markup.add(InlineKeyboardButton(btn_txt, callback_data=f"down_{chave}_{i}"))

        bot.edit_message_text(f"🍿 *Opções Encontradas ({len(opcoes[:5])}):*\nEscolha a versão para baixar:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("s_"):
        tmdb_id = dados.split("_")[1]
        res = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}&language=pt-PT").json()
        seasons = res.get("seasons", [])
        
        markup = InlineKeyboardMarkup(row_width=2)
        for s in seasons:
            if s.get("season_number", 0) > 0:
                markup.add(InlineKeyboardButton(f"Temporada {s['season_number']}", callback_data=f"ep_{tmdb_id}_{s['season_number']}"))
        
        bot.edit_message_text(f"📺 *{res.get('name')}*\nEscolha a Temporada:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("ep_"):
        tmdb_id, season = dados.split("_")[1:3]
        res = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}?api_key={TMDB_API_KEY}&language=pt-PT").json()
        episodes = res.get("episodes", [])

        markup = InlineKeyboardMarkup(row_width=3)
        botoes = [InlineKeyboardButton(f"EP {ep['episode_number']}", callback_data=f"seach_ep_{tmdb_id}_{season}_{ep['episode_number']}") for ep in episodes]
        for i in range(0, len(botoes), 3): markup.row(*botoes[i:i+3])

        bot.edit_message_text(f"📺 *Temporada {season}*\nEscolha o Episódio:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("seach_ep_"):
        tmdb_id, season, ep = dados.split("_")[2:5]
        bot.edit_message_text(f"🔍 Consultando fontes para S{int(season):02d}E{int(ep):02d}... Aguarde.", chat_id, call.message.message_id)

        imdb_id = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids").json().get('external_ids', {}).get('imdb_id')
        opcoes = buscar_todas_as_opcoes("series", imdb_id, season, ep)

        if not opcoes: return bot.edit_message_text("❌ Nenhum link encontrado para este episódio.", chat_id, call.message.message_id)

        chave = f"s_{chat_id}_{tmdb_id}_{season}_{ep}"
        SESSAO_OPCOES[chave] = {"opcoes": opcoes, "tmdb_id": tmdb_id, "season": season, "ep": ep, "tipo": "series"}

        markup = InlineKeyboardMarkup(row_width=1)
        for i, opt in enumerate(opcoes[:5]):
            btn_txt = f"[{opt['indexador']}] {opt['qualidade']}"
            markup.add(InlineKeyboardButton(btn_txt, callback_data=f"down_{chave}_{i}"))

        bot.edit_message_text(f"📺 *Opções para S{int(season):02d}E{int(ep):02d}:*\nEscolha a versão para baixar:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("down_"):
        partes = dados.split("_")
        tipo = partes[1]
        
        if tipo == "m":
            chave = f"{partes[1]}_{partes[2]}_{partes[3]}"
            idx_opt = int(partes[4])
        else:
            chave = f"{partes[1]}_{partes[2]}_{partes[3]}_{partes[4]}_{partes[5]}"
            idx_opt = int(partes[6])

        dados_sessao = SESSAO_OPCOES.get(chave)
        if not dados_sessao: return bot.edit_message_text("❌ Sessão expirada.", chat_id, call.message.message_id)

        item = dados_sessao["opcoes"][idx_opt]
        ext = ".mp4" if ".mp4" in item["url"].lower() else ".mkv"

        if dados_sessao["tipo"] == "movie":
            res = requests.get(f"https://api.themoviedb.org/3/movie/{dados_sessao['tmdb_id']}?api_key={TMDB_API_KEY}&language=pt-PT").json()
            nome_limpo = limpar_nome(res.get('title', 'Filme'))
            ano = str(res.get('release_date', '0000'))[:4]
            pasta_dest = os.path.join(PASTA_BASE, "FILMES", f"{nome_limpo} ({ano})")
            nome_file = f"{nome_limpo} ({ano}){ext}"
        else:
            res = requests.get(f"https://api.themoviedb.org/3/tv/{dados_sessao['tmdb_id']}?api_key={TMDB_API_KEY}&language=pt-PT").json()
            nome_limpo = limpar_nome(res.get('name', 'Serie'))
            season_num = int(dados_sessao['season'])
            ep_num = int(dados_sessao['ep'])
            pasta_dest = os.path.join(PASTA_BASE, "SERIES", nome_limpo, f"Season {season_num:02d}")
            nome_file = f"{nome_limpo} S{season_num:02d}E{ep_num:02d}{ext}"

        threading.Thread(
            target=executar_download,
            args=(item["url"], pasta_dest, nome_file, dados_sessao["tipo"], chat_id, call.message.message_id)
        ).start()

if __name__ == "__main__":
    os.makedirs(os.path.join(PASTA_BASE, "FILMES"), exist_ok=True)
    os.makedirs(os.path.join(PASTA_BASE, "SERIES"), exist_ok=True)
    print("🚀 Bot iniciado na VPS travado em /DATA/Media!")
    bot.polling(none_stop=True)