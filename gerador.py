import requests
import os
import re
import sys
import json
import time

# ==========================================
# CONFIGURAÇÕES DEFINITIVAS
# ==========================================
TMDB_API_KEY = "3755e8749d79c3d9b395e2041c281a52" 

# Lista de Indexadores / Addons disponíveis (com fallback automático)
INDEXADORES = [
    {
        "nome": "FrostStream",
        "url": "https://froststream.cloutteam.com"
    },
    {
        "nome": "MagnetFlix",
        "url": "https://magnetflix.magnetbr.online/qualities%3D4k%2C1080p%2C720p%2Csd%7Caudio%3Ddublado%2Clegendado%7Ccatalogs%3Dpopulares_movie%2Cpopulares_series%2Crecentes_servidor_movie%2Crecentes_servidor_series%2Cemalta_movie%2Cemalta_series"
    },
    {
        "nome": "FenixFlix",
        "url": "https://fenixflix.fenixhub.online"
    },
    {
        "nome": "KingVOD",
        "url": "https://kingvod.wasmer.app/index.php"
    },
    {
        "nome": "BestCine",
        "url": "https://bestcine.dpdns.org"
    }
]

# Diretório exato onde o script e o historico.json vão residir
DIRETORIO_SCRIPT = os.path.dirname(os.path.abspath(__file__))
PASTA_BASE = os.path.join(DIRETORIO_SCRIPT, "Jellyfin_Local")
HISTORICO_ARQUIVO = os.path.join(DIRETORIO_SCRIPT, "historico.json")

GENEROS_IDS = {
    "1": (28, "Ação"),
    "2": (35, "Comédia"),
    "3": (27, "Terror"),
    "4": (878, "Ficção Científica"),
    "5": (18, "Drama"),
    "6": (10749, "Romance"),
    "7": (16, "Animação"),
    "8": (53, "Suspense")
}

# ==========================================
# GESTÃO DE HISTÓRICO E SINCRONIZAÇÃO LOCAL
# ==========================================
def carregar_historico():
    if os.path.exists(HISTORICO_ARQUIVO):
        try:
            with open(HISTORICO_ARQUIVO, "r", encoding="utf-8") as f:
                dados = json.load(f)
                # Compatibilidade com estruturas antigas do histórico
                if "filmes" not in dados:
                    dados["filmes"] = {}
                if "series" not in dados:
                    dados["series"] = {}
                return dados
        except:
            return {"filmes": {}, "series": {}}
    return {"filmes": {}, "series": {}}

def guardar_historico(historico):
    with open(HISTORICO_ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=4)

def registrar_filme_historico(titulo_ano, indexador_nome):
    hist = carregar_historico()
    hist["filmes"][titulo_ano] = {"indexador": indexador_nome}
    guardar_historico(hist)

def registrar_episodio_historico(titulo_serie, temporada, episodio, indexador_nome):
    hist = carregar_historico()
    if titulo_serie not in hist["series"]:
        hist["series"][titulo_serie] = {}
    
    temp_str = str(temporada)
    if temp_str not in hist["series"][titulo_serie]:
        hist["series"][titulo_serie][temp_str] = {}
        
    hist["series"][titulo_serie][temp_str][str(episodio)] = {"indexador": indexador_nome}
    guardar_historico(hist)

def varrer_e_sincronizar_arquivos_locais():
    """Varre a pasta Jellyfin_Local ao iniciar e atualiza o historico.json automaticamente"""
    if not os.path.exists(PASTA_BASE):
        return

    hist = carregar_historico()
    alterado = False

    # 1. Varre Filmes Locais
    pasta_filmes = os.path.join(PASTA_BASE, "Filmes")
    if os.path.exists(pasta_filmes):
        for nome_pasta in os.listdir(pasta_filmes):
            caminho_filme = os.path.join(pasta_filmes, nome_pasta)
            if os.path.isdir(caminho_filme):
                caminho_strm = os.path.join(caminho_filme, f"{nome_pasta}.strm")
                if os.path.exists(caminho_strm) and os.path.getsize(caminho_strm) > 0:
                    if nome_pasta not in hist["filmes"]:
                        hist["filmes"][nome_pasta] = {"indexador": "Desconhecido (Sincronizado Local)"}
                        alterado = True

    # 2. Varre Séries Locais
    pasta_series = os.path.join(PASTA_BASE, "Series")
    if os.path.exists(pasta_series):
        for nome_serie in os.listdir(pasta_series):
            caminho_serie = os.path.join(pasta_series, nome_serie)
            if os.path.isdir(caminho_serie):
                for raiz, _, ficheiros in os.walk(caminho_serie):
                    for f in ficheiros:
                        if f.endswith(".strm"):
                            match = re.search(r'S(\d+)E(\d+)', f, re.IGNORECASE)
                            if match:
                                temp_num = int(match.group(1))
                                ep_num = int(match.group(2))
                                
                                if nome_serie not in hist["series"]:
                                    hist["series"][nome_serie] = {}
                                temp_str = str(temp_num)
                                if temp_str not in hist["series"][nome_serie]:
                                    hist["series"][nome_serie][temp_str] = {}
                                
                                ep_str = str(ep_num)
                                if ep_str not in hist["series"][nome_serie][temp_str]:
                                    hist["series"][nome_serie][temp_str][ep_str] = {"indexador": "Desconhecido (Sincronizado Local)"}
                                    alterado = True

    if alterado:
        guardar_historico(hist)

def exibir_historico():
    hist = carregar_historico()
    filmes = hist.get("filmes", {})
    series = hist.get("series", {})

    print("\n" + "="*50)
    print("📋 HISTÓRICO DE DOWNLOADS REGISTADOS")
    print("="*50)

    if not filmes and not series:
        print("⚠️ O histórico ainda está vazio. Nenhum item foi registado.")
        print("="*50)
        return

    if filmes:
        print(f"\n🎬 FILMES REGISTADOS ({len(filmes)}):")
        for f, info in filmes.items():
            print(f"  • {f} [Fonte: {info.get('indexador', 'N/A')}]")
    else:
        print("\n🎬 FILMES REGISTADOS: Nenhum.")

    if series:
        print(f"\n📺 SÉRIES REGISTADAS ({len(series)}):")
        for serie, temporadas in series.items():
            print(f"  • {serie}:")
            for temp, eps in temporadas.items():
                print(f"      └─ Temporada {temp}: {len(eps)} episódio(s) registados")
    else:
        print("\n📺 SÉRIES REGISTADAS: Nenhuma.")

    print("="*50)

# ==========================================
# VERIFICAÇÃO CRUZADA
# ==========================================
def filme_ja_existe(titulo_ano):
    hist = carregar_historico()
    if titulo_ano in hist["filmes"]:
        return True
        
    pasta_filme = os.path.join(PASTA_BASE, "Filmes", titulo_ano)
    caminho_arquivo = os.path.join(pasta_filme, f"{titulo_ano}.strm")
    if os.path.exists(caminho_arquivo) and os.path.getsize(caminho_arquivo) > 0:
        return True
        
    return False

def episodio_ja_existe(titulo_serie, temporada, episodio):
    hist = carregar_historico()
    try:
        if str(episodio) in hist["series"].get(titulo_serie, {}).get(str(temporada), {}):
            return True
    except:
        pass
        
    nome_ep = f"{titulo_serie} S{temporada:02d}E{episodio:02d}.strm"
    caminho_ep = os.path.join(PASTA_BASE, "Series", titulo_serie, f"Season {temporada:02d}", nome_ep)
    if os.path.exists(caminho_ep) and os.path.getsize(caminho_ep) > 0:
        return True
        
    return False

# ==========================================
# FUNÇÕES DE SUPORTE E BUSCA MULTI-INDEXADOR
# ==========================================
def limpar_nome(nome):
    return re.sub(r'[\\/*?:"<>|]', "", str(nome))

def desenhar_barra_progresso(atual, total, prefixo='', tamanho=30):
    percentual = float(atual) / float(total)
    preenchido = int(round(tamanho * percentual))
    barreira = '█' * preenchido + '-' * (tamanho - preenchido)
    sys.stdout.write(f'\r{prefixo} |{barreira}| {atual}/{total} ({int(percentual * 100)}%)')
    sys.stdout.flush()

def obter_stream_com_idioma(tipo, imdb_id, season=None, episode=None):
    """
    Percorre os indexadores em sequência (Fallback). 
    Se o primeiro falhar ou não tiver o link, testa o próximo.
    Retorna: (link, idioma, nome_do_indexador)
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://web.stremio.com"
    }

    for indexador in INDEXADORES:
        addon_url = indexador["url"]
        if tipo == "movie":
            url = f"{addon_url}/stream/movie/{imdb_id}.json"
        else:
            url = f"{addon_url}/stream/series/{imdb_id}:{season}:{episode}.json"
            
        for tentativa in range(1, 3): # 2 tentativas rápidas por indexador
            try:
                res = requests.get(url, headers=headers, timeout=10)
                if res.status_code == 200:
                    streams = res.json().get("streams", [])
                    streams_validos = [s for s in streams if "url" in s and s["url"].startswith("http")]
                    
                    if streams_validos:
                        # Prioridade Dublado/Dual
                        for s in streams_validos:
                            texto = (s.get("title", "") + " " + s.get("name", "")).lower()
                            if "dub" in texto or "dual" in texto or "pt-br" in texto or "português" in texto:
                                return s["url"], "Dublado 🇧🇷", indexador["nome"]
                                
                        # Prioridade Legendado
                        for s in streams_validos:
                            texto = (s.get("title", "") + " " + s.get("name", "")).lower()
                            if "leg" in texto or "sub" in texto:
                                return s["url"], "Legendado 🇺🇸", indexador["nome"]
                                
                        # Qualquer outro stream válido
                        return streams_validos[0]["url"], "Idioma não especificado", indexador["nome"]
                break
            except:
                if tentativa < 2:
                    time.sleep(1)
                    
    return None, None, None

def limpar_duplicatas_e_orfaos():
    print("\n🧹 A varrer a biblioteca à procura de duplicados e ficheiros órfãos...")
    if not os.path.exists(PASTA_BASE):
        print("❌ A pasta base ainda não existe.")
        return

    liberados = 0
    encontrados = set()

    for raiz, _, ficheiros in os.walk(PASTA_BASE):
        for f in ficheiros:
            if f.endswith(".strm"):
                caminho_completo = os.path.join(raiz, f)
                if os.path.getsize(caminho_completo) == 0:
                    print(f"🗑️ A remover ficheiro vazio: {caminho_completo}")
                    os.remove(caminho_completo)
                    liberados += 1
                    continue

                if f in encontrados:
                    print(f"🗑️ Ficheiro duplicado detetado e removido: {caminho_completo}")
                    os.remove(caminho_completo)
                    liberados += 1
                else:
                    encontrados.add(f)

    print(f"\n✨ Limpeza concluída! Foram limpos {liberados} ficheiros problemáticos ou duplicados.")

# ==========================================
# PROCESSAMENTO DE FILMES E SÉRIES
# ==========================================
def processar_filme(item):
    titulo = limpar_nome(item.get('title', item.get('name', '')))
    ano = item.get('release_date', '0000')[:4]
    identificador = f"{titulo} ({ano})"
    
    pasta_filme = os.path.join(PASTA_BASE, "Filmes", identificador)
    caminho_arquivo = os.path.join(pasta_filme, f"{identificador}.strm")
    
    if filme_ja_existe(identificador):
        print(f"\n⏭️ O filme '{identificador}' já existe nas pastas locais ou no histórico! A ignorar...")
        return
        
    print(f"\n⏳ A procurar links nos indexadores para: {identificador}...")
    
    url_tmdb = f"https://api.themoviedb.org/3/movie/{item['id']}?api_key={TMDB_API_KEY}&append_to_response=external_ids"
    res = requests.get(url_tmdb).json()
    imdb_id = res.get('external_ids', {}).get('imdb_id')
    
    if not imdb_id:
        print(f"❌ Erro: IMDb ID não encontrado.")
        return

    link, idioma, indexador_usado = obter_stream_com_idioma("movie", imdb_id)
    
    if link:
        os.makedirs(pasta_filme, exist_ok=True)
        with open(caminho_arquivo, "w", encoding="utf-8") as f:
            f.write(link)
        registrar_filme_historico(identificador, indexador_usado)
        print(f"✅ SUCESSO! Salvo via [{indexador_usado}] [{idioma}] em: {caminho_arquivo}")
    else:
        print(f"⚠️ Sem links diretos em nenhum dos indexadores.")

def processar_serie(item):
    titulo = limpar_nome(item.get('name', item.get('title', '')))
    ano = item.get('first_air_date', '0000')[:4]
    identificador_serie = f"{titulo} ({ano})"
    
    url_tmdb = f"https://api.themoviedb.org/3/tv/{item['id']}?api_key={TMDB_API_KEY}&append_to_response=external_ids"
    res = requests.get(url_tmdb).json()
    imdb_id = res.get('external_ids', {}).get('imdb_id')
    temporadas = [t for t in res.get('seasons', []) if t['season_number'] > 0]
    
    if not imdb_id or not temporadas:
        print(f"❌ Erro: Dados da série não encontrados.")
        return

    pasta_serie = os.path.join(PASTA_BASE, "Series", identificador_serie)
    
    print(f"\n📺 Série: {identificador_serie}")
    print("Temporadas disponíveis:")
    for t in temporadas:
        print(f"  [{t['season_number']}] Temporada {t['season_number']} ({t['episode_count']} eps)")
        
    escolha_temp = input("\nDigita o número da temporada para transferir (ex: 1), 'T' para TODAS, ou 'C' para cancelar: ").strip().upper()
    
    if escolha_temp == 'C':
        print("Operação cancelada para esta série.")
        return

    for temp in temporadas:
        season_num = temp['season_number']
        
        if escolha_temp != 'T' and str(season_num) != escolha_temp:
            continue
            
        pasta_temp = os.path.join(pasta_serie, f"Season {season_num:02d}")
        os.makedirs(pasta_temp, exist_ok=True)
        
        qtde_ep = temp['episode_count']
        print(f"\n📺 A processar Temporada {season_num} ({qtde_ep} episódios):")
        
        for ep_num in range(1, qtde_ep + 1):
            nome_ep = f"{identificador_serie} S{season_num:02d}E{ep_num:02d}.strm"
            caminho_ep = os.path.join(pasta_temp, nome_ep)
            
            desenhar_barra_progresso(ep_num, qtde_ep, prefixo=f"T{season_num:02d}")
            
            if episodio_ja_existe(identificador_serie, season_num, ep_num):
                continue
                
            link, idioma, indexador_usado = obter_stream_com_idioma("series", imdb_id, season_num, ep_num)
            
            if link:
                with open(caminho_ep, "w", encoding="utf-8") as f:
                    f.write(link)
                registrar_episodio_historico(identificador_serie, season_num, ep_num, indexador_usado)
        
        print() 

def exibir_menu_busca(resultados, tipo):
    print("\n🔍 Seleciona os títulos da lista:")
    for i, item in enumerate(resultados[:10]):
        titulo = item.get('title') if tipo == "movie" else item.get('name')
        ano = item.get('release_date', item.get('first_air_date', 'N/A'))[:4]
        print(f"[{i+1}] {titulo} ({ano})")
    
    print("[0] Cancelar")
    print("\n💡 Podes escolher vários separados por vírgula (ex: 1, 3, 4) ou um intervalo (ex: 1-3)")
    escolha_usuario = input("Digita os números escolhidos: ").strip()
    
    if not escolha_usuario or escolha_usuario == '0':
        print("Operação cancelada.")
        return

    indices_selecionados = set()
    partes = escolha_usuario.split(',')
    for parte in partes:
        parte = parte.strip()
        if '-' in parte:
            try:
                inicio, fim = map(int, parte.split('-'))
                for n in range(inicio, fim + 1):
                    if 1 <= n <= len(resultados[:10]):
                        indices_selecionados.add(n - 1)
            except ValueError:
                continue
        elif parte.isdigit():
            n = int(parte)
            if 1 <= n <= len(resultados[:10]):
                indices_selecionados.add(n - 1)

    if not indices_selecionados:
        print("❌ Nenhuma opção válida selecionada.")
        return

    for idx in sorted(indices_selecionados):
        selecionado = resultados[idx]
        if tipo == "movie":
            processar_filme(selecionado)
        else:
            processar_serie(selecionado)

def escolher_categoria_menu(tipo_midia):
    print("\n" + "="*30)
    print(f"📂 ESCOLHE A CATEGORIA DE {'FILMES' if tipo_midia == 'movie' else 'SÉRIES'}")
    print("="*30)
    for chave, (_, nome) in GENEROS_IDS.items():
        print(f"[{chave}] {nome}")
    print("[0] Voltar")
    
    cat_escolhida = input("\nEscolhe o número da categoria: ").strip()
    if cat_escolhida in GENEROS_IDS:
        genre_id, nome_cat = GENEROS_IDS[cat_escolhida]
        print(f"\n⏳ A procurar por categoria: {nome_cat}...")
        url = f"https://api.themoviedb.org/3/discover/{tipo_midia}?api_key={TMDB_API_KEY}&language=pt-PT&with_genres={genre_id}&sort_by=popularity.desc"
        try:
            resultados = requests.get(url).json().get("results", [])
            if resultados:
                exibir_menu_busca(resultados, tipo_midia)
            else:
                print("❌ Nenhum resultado encontrado para esta categoria.")
        except Exception as e:
            print(f"❌ Erro na API: {e}")

def descobrir_categoria(url, tipo):
    print("\n⏳ A carregar a lista...")
    try:
        resultados = requests.get(url).json().get("results", [])
        if resultados:
            exibir_menu_busca(resultados, tipo)
        else:
            print("❌ Nada encontrado.")
    except Exception as e:
        print(f"❌ Erro na API: {e}")

def main():
    print("🔄 A verificar pastas locais e a sincronizar o histórico...")
    varrer_e_sincronizar_arquivos_locais()

    while True:
        print("\n" + "="*45)
        print("🍿 GERADOR DE BIBLIOTECA JELLYFIN (MULTI-INDEXADOR)")
        print("="*45)
        print("[1] 🎬 Procurar Filme específico")
        print("[2] 📺 Procurar Série específica")
        print("[3] 📂 Explorar Categorias de Filmes")
        print("[4] 📂 Explorar Categorias de Séries")
        print("[5] 🔥 Ver Top Filmes Populares")
        print("[6] 🌟 Ver Top Séries Populares")
        print("[7] ⛩️  Ver Top Animes Populares")
        print("[8] 🇰🇷 Ver Top Doramas Populares")
        print("[9] 🧹 Limpar Ficheiros Duplicados/Vazios")
        print("[10] 📋 Ver Histórico de Downloads (com Indexadores)")
        print("[0] ❌ Sair")
        
        opcao = input("\nEscolhe uma opção: ")
        
        if opcao == "0":
            break
            
        elif opcao in ["1", "2"]:
            tipo_tmdb = "movie" if opcao == "1" else "tv"
            busca = input(f"Digita o nome: ")
            url = f"https://api.themoviedb.org/3/search/{tipo_tmdb}?api_key={TMDB_API_KEY}&query={busca}&language=pt-PT"
            descobrir_categoria(url, tipo_tmdb)
            
        elif opcao == "3":
            escolher_categoria_menu("movie")
            
        elif opcao == "4":
            escolher_categoria_menu("tv")
            
        elif opcao == "5":
            url = f"https://api.themoviedb.org/3/movie/popular?api_key={TMDB_API_KEY}&language=pt-PT&page=1"
            descobrir_categoria(url, "movie")
            
        elif opcao == "6":
            url = f"https://api.themoviedb.org/3/tv/popular?api_key={TMDB_API_KEY}&language=pt-PT&page=1"
            descobrir_categoria(url, "tv")
            
        elif opcao == "7":
            url = f"https://api.themoviedb.org/3/discover/tv?api_key={TMDB_API_KEY}&language=pt-PT&with_original_language=ja&with_genres=16&sort_by=popularity.desc"
            descobrir_categoria(url, "tv")
            
        elif opcao == "8":
            url = f"https://api.themoviedb.org/3/discover/tv?api_key={TMDB_API_KEY}&language=pt-PT&with_original_language=ko&sort_by=popularity.desc"
            descobrir_categoria(url, "tv")
            
        elif opcao == "9":
            limpar_duplicatas_e_orfaos()
            
        elif opcao == "10":
            exibir_historico()

if __name__ == "__main__":
    main()