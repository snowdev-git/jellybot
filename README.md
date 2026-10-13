# 🍿 JellyBot — Telegram Cloud Storage & Streaming Manager

O **JellyBot** é uma solução completa de automação de media que transforma o Telegram num servidor de armazenamento em nuvem de alta capacidade integrado diretamente com o **Jellyfin**. 

O sistema permite pesquisar, descarregar e organizar filmes e séries diretamente pelo Telegram, contornando a restrição de 2,0 GB por ficheiro através de fatiamento binário contínuo e streaming por concorrência de blocos via HTTP `Range Requests`.

---

## ✨ Principais Funcionalidades

- 🔍 **Pesquisa Inteligente & Indexadores:** Integração com a API do TMDB e indexadores Stremio (Torrentio, FrostStream) com deteção automática de resolução, servidores e faixas de áudio (Dublado 🇧🇷 / Legendado 🔤).
- 📦 **Bypass do Limite de 2 GB (Fatiamento Binário):** Ficheiros com mais de 1.8 GB são divididos em blocos binários brutos instantâneos antes do upload e re-concatenados em tempo real no streaming, mantendo a duração total contínua do vídeo e suporte a avanço/recuo no leitor.
- ⚡ **Upload Paralelo de Alta Velocidade:** Utilização de workers paralelos via `Telethon` e aceleração criptográfica com `cryptg`.
- 🌐 **Servidor de Streaming Dedicado (`aiohttp`):** Servidor HTTP integrado na porta `8081` compatível com cabeçalhos `Range` (byte-range streaming) e requisições `HEAD` exigidas pelo `ffprobe` e clientes Jellyfin.
- 📁 **Organização Automática & `.strm`:** Estruturação automática de pastas (`/FILMES` e `/SERIES`), geração dinâmica de ficheiros `.strm` e atualização imediata da biblioteca do Jellyfin via API.

---

## 🏗️ Arquitetura do Sistema

```text
[ Telegram Bot ] ---> [ TMDB / Indexadores ]
       |
       v
[ Download Temp ] ---> [ Fatiamento Binário ] ---> [ Telegram Storage Channel ]
                                                        |
[ Jellyfin Client ] <--- [ Ficheiro .strm ] <--- [ Servidor HTTP (server.py) ]