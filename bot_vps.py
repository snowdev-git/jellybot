import os
import re
import asyncio
import threading
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import requests

from config import Config
from logger import logger
from database import (
    init_db,
    salvar_midia,
    obter_midia,
    listar_midias,
    remover_midia,
    carregar_indexadores
)
from server import iniciar_servidor, client
from downloader import (
    buscar_fontes_indexadores,
    processar_e_hospedar_midia,
    limpar_nome
)

bot = telebot.TeleBot(Config.TELEGRAM_TOKEN)
SESSAO_OPCOES = {}
SESSAO_MANUAL = {}
MAIN_LOOP = asyncio.new_event_loop()

# ==========================================
# MENUS DE NAVEGAÇÃO E REGRAS DE SEGURANÇA
# ==========================================
def verificar_admin(message) -> bool:
    if Config.ADMIN_ID != 0 and message.from_user.id != Config.ADMIN_ID:
        bot.reply_to(message, "⛔ Acesso negado. Apenas o administrador configurado pode utilizar este bot.")
        return False
    return True

def menu_principal():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("🎬 Novo Filme"), KeyboardButton("📺 Nova Série"),
        KeyboardButton("🗑️ Gerenciar/Apagar"), KeyboardButton("📊 Espaço no Disco")
    )
    return markup

@bot.message_handler(commands=['start'])
def comando_start(message):
    if not verificar_admin(message): return
    bot.send_message(
        message.chat.id,
        f"🍿 **JellyBot + Telegram Cloud Storage**\n"
        f"📌 Servidor vinculado: `{Config.IP_SERVIDOR}:{Config.PORTA_STREAM_LOCAL}`\n\n"
        f"Escolha uma opção no menu abaixo para pesquisar e indexar mídias diretamente no Jellyfin:",
        reply_markup=menu_principal(),
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda msg: True)
def escutar_botoes(message):
    if not verificar_admin(message): return
    txt = message.text

    if txt == "🎬 Novo Filme":
        msg = bot.send_message(message.chat.id, "Escreva o nome do filme:")
        bot.register_next_step_handler(msg, pesquisar_tmdb, "movie")
    elif txt == "📺 Nova Série":
        msg = bot.send_message(message.chat.id, "Escreva o nome da série:")
        bot.register_next_step_handler(msg, pesquisar_tmdb, "tv")
    elif txt == "📊 Espaço no Disco":
        try:
            import shutil
            total, used, free = shutil.disk_usage(Config.PASTA_MEDIA)
            free_gb = round(free / (1024**3), 2)
            used_gb = round(used / (1024**3), 2)
            bot.send_message(message.chat.id, f"📊 **Armazenamento Local:**\n💾 Livre: *{free_gb} GB* | 📁 Usado: *{used_gb} GB*", parse_mode="Markdown")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Erro ao obter informações de disco: {e}")
    elif txt == "🗑️ Gerenciar/Apagar":
        menu_gerenciar_media(message.chat.id)

def menu_gerenciar_media(chat_id):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🎬 Apagar Filmes", callback_data="mgr_filmes"),
        InlineKeyboardButton("📺 Apagar Séries", callback_data="mgr_series")
    )
    bot.send_message(chat_id, "🗑️ **Gerenciar Mídias Salvas**\nEscolha a categoria:", reply_markup=markup, parse_mode="Markdown")

def pesquisar_tmdb(message, tipo):
    query = message.text
    url = f"https://api.themoviedb.org/3/search/{tipo}?api_key={Config.TMDB_API_KEY}&query={query}&language=pt-BR"
    try:
        res = requests.get(url, timeout=10).json().get("results", [])[:5]
        if not res:
            return bot.send_message(message.chat.id, "❌ Nenhum resultado encontrado no TMDB.")

        markup = InlineKeyboardMarkup(row_width=1)
        for r in res:
            tit = r.get('title') if tipo == 'movie' else r.get('name')
            ano = str(r.get('release_date') if tipo == 'movie' else r.get('first_air_date'))[:4]
            cb = f"m_{r['id']}" if tipo == 'movie' else f"s_{r['id']}"
            markup.add(InlineKeyboardButton(f"{'🎬' if tipo=='movie' else '📺'} {tit} ({ano})", callback_data=cb))

        bot.send_message(message.chat.id, "👇 Selecione o resultado correto:", reply_markup=markup)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Erro ao consultar TMDB: {e}")

# Handler para captura de link manual enviado por texto
def receber_link_manual(message):
    chat_id = message.chat.id
    url_link = message.text.strip()

    if not url_link.startswith("http://") and not url_link.startswith("https://"):
        msg = bot.send_message(chat_id, "❌ Link inválido. Certifique-se de que o link começa com `http://` ou `https://`. Envie novamente:")
        return bot.register_next_step_handler(msg, receber_link_manual)

    dados_sessao = SESSAO_MANUAL.get(chat_id)
    if not dados_sessao:
        return bot.send_message(chat_id, "❌ Sessão expirada. Por favor, pesquise a mídia novamente.")

    msg_status = bot.send_message(chat_id, "⏳ **Link recebido! A iniciar processamento...**", parse_mode="Markdown")

    def atualizar_status_bot(texto_status):
        try: bot.edit_message_text(texto_status, chat_id, msg_status.message_id, parse_mode="Markdown")
        except: pass

    def tarefa_download_manual():
        loop_async = asyncio.new_event_loop()
        asyncio.set_event_loop(loop_async)

        sucesso, msg_resultado, caminho_strm = loop_async.run_until_complete(
            processar_e_hospedar_midia(
                url_fonte=url_link,
                tipo=dados_sessao["tipo"],
                tmdb_info=dados_sessao["tmdb_info"],
                qualidade="LINK MANUALL 🔗",
                main_loop=MAIN_LOOP,
                progress_callback=atualizar_status_bot
            )
        )

        if sucesso:
            msg_sucesso = (
                f"🎉 **Mídia Processada e Salva no Telegram!**\n\n"
                f"🎬 **Nome:** `{msg_resultado}`\n"
                f"🎧 **Fonte:** `Link Manual Direct`\n"
                f"📂 **Caminho Local:** `{caminho_strm}`\n\n"
                f"👉 **Acesse o Jellyfin para reproduzir ou baixe o arquivo .strm anexado abaixo!**"
            )
            bot.edit_message_text(msg_sucesso, chat_id, msg_status.message_id, parse_mode="Markdown")

            markup_proximo = None
            if dados_sessao["tipo"] == "series":
                tmdb_id = dados_sessao.get("tmdb_id")
                season_num = int(dados_sessao.get("season", 1))
                next_ep_num = int(dados_sessao.get("ep", 1)) + 1

                markup_proximo = InlineKeyboardMarkup(row_width=2)
                markup_proximo.add(
                    InlineKeyboardButton(f"⏭️ Próximo EP (S{season_num:02d}E{next_ep_num:02d})", callback_data=f"seach_ep_{tmdb_id}_{season_num}_{next_ep_num}"),
                    InlineKeyboardButton("🔙 Lista de Episódios", callback_data=f"ep_{tmdb_id}_{season_num}")
                )

            if caminho_strm and os.path.exists(caminho_strm):
                try:
                    with open(caminho_strm, 'rb') as doc:
                        bot.send_document(
                            chat_id, doc, caption=f"📄 **Arquivo `.strm` do conteúdo:** `{msg_resultado}`",
                            reply_markup=markup_proximo, parse_mode="Markdown"
                        )
                except Exception as err_doc:
                    logger.warning(f"⚠️ Erro ao enviar documento .strm: {err_doc}")
        else:
            bot.edit_message_text(f"❌ **Falha ao processar link manual:** `{msg_resultado}`\n\nVerifique se o link é direto para o vídeo.", chat_id, msg_status.message_id, parse_mode="Markdown")

    threading.Thread(target=tarefa_download_manual, daemon=True).start()

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    bot.answer_callback_query(call.id)
    chat_id, dados = call.message.chat.id, call.data

    if dados == "mgr_filmes":
        filmes = listar_midias("movie")
        if not filmes:
            return bot.edit_message_text("❌ Nenhum filme registado na base de dados.", chat_id, call.message.message_id)

        markup = InlineKeyboardMarkup(row_width=1)
        for f in filmes[:25]:
            markup.add(InlineKeyboardButton(f"🗑️ {f['titulo']} ({f['ano']})", callback_data=f"del_{f['media_id']}"))
        bot.edit_message_text("🎬 **Selecione o filme que deseja remover:**", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("del_"):
        media_id = dados[4:]
        midia = obter_midia(media_id)
        if midia:
            if midia.get("caminho_strm") and os.path.exists(midia["caminho_strm"]):
                try: os.remove(midia["caminho_strm"])
                except: pass
            remover_midia(media_id)
            bot.edit_message_text(f"✅ **Mídia removida da base de dados e arquivo .strm apagado!**\n🎬 `{midia['titulo']}`", chat_id, call.message.message_id, parse_mode="Markdown")

    elif dados.startswith("m_"):
        tmdb_id = dados.split("_")[1]
        bot.edit_message_text("🔍 Consultando fontes e qualidades nos indexadores...", chat_id, call.message.message_id)

        res_tmdb = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR&append_to_response=external_ids", timeout=10).json()
        imdb_id = res_tmdb.get('external_ids', {}).get('imdb_id')

        opcoes = buscar_fontes_indexadores("movie", imdb_id) if imdb_id else []
        chave = f"m_{chat_id}_{tmdb_id}"
        SESSAO_OPCOES[chave] = {"opcoes": opcoes, "tmdb_info": res_tmdb, "tipo": "movie"}

        markup = InlineKeyboardMarkup(row_width=1)
        for i, opt in enumerate(opcoes[:6]):
            btn_txt = f"[{opt['indexador']}] {opt['qualidade']}"
            markup.add(InlineKeyboardButton(btn_txt, callback_data=f"down_{chave}_{i}"))

        # Opção para inserir link direto manualmente
        markup.add(InlineKeyboardButton("🔗 Inserir Link Direto (HTTP/MP4)", callback_data=f"input_link_m_{tmdb_id}"))

        bot.edit_message_text("🍿 **Escolha a qualidade ou insira um link direto:**", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("s_"):
        tmdb_id = dados.split("_")[1]
        res = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR", timeout=10).json()
        seasons = res.get("seasons", [])

        markup = InlineKeyboardMarkup(row_width=2)
        for s in seasons:
            if s.get("season_number", 0) > 0:
                markup.add(InlineKeyboardButton(f"Temporada {s['season_number']}", callback_data=f"ep_{tmdb_id}_{s['season_number']}"))
        bot.edit_message_text(f"📺 **{res.get('name')}**\nEscolha a Temporada:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("ep_"):
        tmdb_id, season = dados.split("_")[1:3]
        res = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}?api_key={Config.TMDB_API_KEY}&language=pt-BR", timeout=10).json()
        episodes = res.get("episodes", [])

        markup = InlineKeyboardMarkup(row_width=3)
        botoes = [InlineKeyboardButton(f"EP {ep['episode_number']}", callback_data=f"seach_ep_{tmdb_id}_{season}_{ep['episode_number']}") for ep in episodes]
        for i in range(0, len(botoes), 3):
            markup.row(*botoes[i:i+3])

        markup.add(InlineKeyboardButton("🔙 Voltar para Temporadas", callback_data=f"s_{tmdb_id}"))
        bot.edit_message_text(f"📺 **Temporada {season}**\nEscolha o Episódio:", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("seach_ep_"):
        tmdb_id, season, ep = dados.split("_")[2:5]
        season_num, ep_num = int(season), int(ep)
        bot.edit_message_text(f"🔍 Consultando fontes para S{season_num:02d}E{ep_num:02d}...", chat_id, call.message.message_id)

        res_tv = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR&append_to_response=external_ids", timeout=10).json()
        imdb_id = res_tv.get('external_ids', {}).get('imdb_id')

        opcoes = buscar_fontes_indexadores("series", imdb_id, season_num, ep_num) if imdb_id else []

        chave = f"s_{chat_id}_{tmdb_id}_{season}_{ep}"
        res_tv['season_number'] = season
        res_tv['episode_number'] = ep
        SESSAO_OPCOES[chave] = {"opcoes": opcoes, "tmdb_info": res_tv, "tipo": "series", "tmdb_id": tmdb_id, "season": season, "ep": ep}

        markup = InlineKeyboardMarkup(row_width=1)
        for i, opt in enumerate(opcoes[:6]):
            btn_txt = f"[{opt['indexador']}] {opt['qualidade']}"
            markup.add(InlineKeyboardButton(btn_txt, callback_data=f"down_{chave}_{i}"))

        # Opção para colar link direto
        markup.add(InlineKeyboardButton("🔗 Inserir Link Direto (HTTP/MP4)", callback_data=f"input_link_s_{tmdb_id}_{season}_{ep}"))

        next_ep_num = ep_num + 1
        markup.row(
            InlineKeyboardButton(f"⏭️ Ir para EP {next_ep_num:02d}", callback_data=f"seach_ep_{tmdb_id}_{season_num}_{next_ep_num}"),
            InlineKeyboardButton("🔙 Lista de Episódios", callback_data=f"ep_{tmdb_id}_{season_num}")
        )

        bot.edit_message_text(f"📺 **S{season_num:02d}E{ep_num:02d} - Escolha a qualidade ou cole um link:**", chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif dados.startswith("input_link_"):
        partes = dados.split("_")
        tipo_str = partes[2]

        if tipo_str == "m":
            tmdb_id = partes[3]
            res_tmdb = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR", timeout=10).json()
            SESSAO_MANUAL[chat_id] = {"tipo": "movie", "tmdb_info": res_tmdb}
            tit = res_tmdb.get('title', 'Filme')
        else:
            tmdb_id, season, ep = partes[3:6]
            res_tv = requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={Config.TMDB_API_KEY}&language=pt-BR", timeout=10).json()
            res_tv['season_number'] = season
            res_tv['episode_number'] = ep
            SESSAO_MANUAL[chat_id] = {"tipo": "series", "tmdb_info": res_tv, "tmdb_id": tmdb_id, "season": season, "ep": ep}
            tit = f"{res_tv.get('name', 'Série')} S{int(season):02d}E{int(ep):02d}"

        msg = bot.send_message(chat_id, f"🔗 **Envie o link direto (HTTP/HTTPS) do vídeo para `{tit}`:**\n\n_Exemplo: http://site.com/video.mp4_", parse_mode="Markdown")
        bot.register_next_step_handler(msg, receber_link_manual)

    elif dados.startswith("down_"):
        partes = dados.split("_")
        tipo_str = partes[1]

        if tipo_str == "m":
            chave = f"{partes[1]}_{partes[2]}_{partes[3]}"
            idx_opt = int(partes[4])
        else:
            chave = f"{partes[1]}_{partes[2]}_{partes[3]}_{partes[4]}_{partes[5]}"
            idx_opt = int(partes[6])

        dados_sessao = SESSAO_OPCOES.get(chave)
        if not dados_sessao:
            return bot.edit_message_text("❌ Sessão expirada. Por favor, pesquise novamente.", chat_id, call.message.message_id)

        item = dados_sessao["opcoes"][idx_opt]

        def atualizar_status_bot(texto_status):
            try: bot.edit_message_text(texto_status, chat_id, call.message.message_id, parse_mode="Markdown")
            except: pass

        def tarefa_download():
            loop_async = asyncio.new_event_loop()
            asyncio.set_event_loop(loop_async)

            sucesso, msg_resultado, caminho_strm = loop_async.run_until_complete(
                processar_e_hospedar_midia(
                    url_fonte=item["url"],
                    tipo=dados_sessao["tipo"],
                    tmdb_info=dados_sessao["tmdb_info"],
                    qualidade=item["qualidade"],
                    main_loop=MAIN_LOOP,
                    progress_callback=atualizar_status_bot
                )
            )

            if sucesso:
                msg_sucesso = (
                    f"🎉 **Mídia Processada e Salva no Telegram!**\n\n"
                    f"🎬 **Nome:** `{msg_resultado}`\n"
                    f"🎧 **Qualidade Escolhida:** `{item['qualidade']}`\n"
                    f"📂 **Caminho Local:** `{caminho_strm}`\n\n"
                    f"👉 **Acesse o Jellyfin para reproduzir ou baixe o arquivo .strm anexado abaixo!**"
                )
                bot.edit_message_text(msg_sucesso, chat_id, call.message.message_id, parse_mode="Markdown")

                markup_proximo = None
                if dados_sessao["tipo"] == "series":
                    tmdb_id = dados_sessao.get("tmdb_id")
                    season_num = int(dados_sessao.get("season", 1))
                    next_ep_num = int(dados_sessao.get("ep", 1)) + 1

                    markup_proximo = InlineKeyboardMarkup(row_width=2)
                    markup_proximo.add(
                        InlineKeyboardButton(f"⏭️ Próximo EP (S{season_num:02d}E{next_ep_num:02d})", callback_data=f"seach_ep_{tmdb_id}_{season_num}_{next_ep_num}"),
                        InlineKeyboardButton("🔙 Lista de Episódios", callback_data=f"ep_{tmdb_id}_{season_num}")
                    )

                if caminho_strm and os.path.exists(caminho_strm):
                    try:
                        with open(caminho_strm, 'rb') as doc:
                            bot.send_document(
                                chat_id, doc, caption=f"📄 **Arquivo `.strm` do conteúdo:** `{msg_resultado}`",
                                reply_markup=markup_proximo, parse_mode="Markdown"
                            )
                    except Exception as err_doc:
                        logger.warning(f"⚠️ Não foi possível enviar documento .strm: {err_doc}")
            else:
                markup_fail = None
                if dados_sessao["tipo"] == "series":
                    tmdb_id = dados_sessao.get("tmdb_id")
                    season_num = int(dados_sessao.get("season", 1))
                    ep_num = int(dados_sessao.get("ep", 1))
                    markup_fail = InlineKeyboardMarkup(row_width=1)
                    markup_fail.add(
                        InlineKeyboardButton("🔗 Colocar Link Direto Manualmente", callback_data=f"input_link_s_{tmdb_id}_{season_num}_{ep_num}"),
                        InlineKeyboardButton("🔄 Tentar Novamente", callback_data=f"seach_ep_{tmdb_id}_{season_num}_{ep_num}"),
                        InlineKeyboardButton("🔙 Lista de Episódios", callback_data=f"ep_{tmdb_id}_{season_num}")
                    )

                bot.edit_message_text(f"❌ **Falha ao processar mídia:** `{msg_resultado}`\n\nPor favor, tente outra opção ou cole o link direto.", chat_id, call.message.message_id, reply_markup=markup_fail, parse_mode="Markdown")

        threading.Thread(target=tarefa_download, daemon=True).start()

# ==========================================
# INICIALIZAÇÃO UNIFICADA E ROBUSTA
# ==========================================
def rodar_loop_principal():
    asyncio.set_event_loop(MAIN_LOOP)
    init_db()
    MAIN_LOOP.run_until_complete(iniciar_servidor())
    MAIN_LOOP.run_forever()

if __name__ == "__main__":
    logger.info("🚀 A iniciar JellyBot...")
    threading.Thread(target=rodar_loop_principal, daemon=True).start()
    bot.infinity_polling(timeout=10, long_polling_timeout=5)