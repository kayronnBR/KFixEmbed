# Nerimity Video Bot

Bot em Python para **Nerimity** que detecta links de vídeo nas mensagens, baixa, compacta com `ffmpeg` até caber no limite do servidor e responde no mesmo canal enviando **apenas o arquivo de vídeo**, com o link original em uma legenda curta.

Exemplo de mensagem enviada pelo bot:

```text
[https://www.instagram.com/reels/Df8NHYXGoLr/](https://www.instagram.com/reels/Df8NHYXGoLr/)
```

> Não reenvia links separados, não mexe em embeds e não contém a ponte com o Astroid.

## ✨ Funcionalidades

- Detecta automaticamente URLs em mensagens do Nerimity (`on_message_create`).
- Baixa vídeos usando **proxys de fix embed**: lê o `og:video` da página corrigida (ou a API do FxTwitter) e puxa o MP4 direto, sem depender do site original.
- Redes atendidas pelo proxy: **Instagram** (`oginstagram`), **X/Twitter** (`fxtwitter` API), **TikTok** (`tnktok`) e **Reddit** (`vxreddit`).
- `yt-dlp` continua instalado como **fallback**: se o proxy falhar, o bot tenta o link original com ele.
- Compactação progressiva com `ffmpeg` se o arquivo passar do limite:
  - 720p → 480p → 360p → 240p, reduzindo o bitrate a cada tentativa.
- Envia o vídeo como anexo real no canal, usando a API atual do Nerimity.
- Se nem no bitrate mínimo couber, avisa no canal em vez de falhar no upload.
- Fila assíncrona (não trava o bot durante downloads).
- Limpeza automática dos arquivos temporários.

## 📋 Requisitos

| Item | Versão / observação |
|---|---|
| Python | 3.10 ou superior |
| ffmpeg | Instalado no sistema e no `PATH` |
| ffprobe | Vem junto com o ffmpeg |
| Conta Nerimity | Um token de bot/usuário válido |
| Conexão com a internet | Para baixar os vídeos e falar com a API do Nerimity |

### Instalar o ffmpeg

Linux (Debian/Ubuntu):

```bash
sudo apt update
sudo apt install -y ffmpeg
```

Windows:

```bash
winget install Gyan.FFmpeg
```

ou baixe em <https://www.gyan.dev/ffmpeg/builds/> e adicione a pasta `bin` ao `PATH`.

Verifique:

```bash
ffmpeg -version
ffprobe -version
```

## 🚀 Instalação

```bash
git clone <URL_DO_SEU_REPOSITORIO>.git nerimity-video-bot
cd nerimity-video-bot

python3 -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

## ⚙️ Configuração

Copie o exemplo e preencha o token:

```bash
cp .env.example .env
```

Edite `.env`:

```env
NERIMITY_TOKEN=seu_token_aqui
```

> ⚠️ **Nunca commite o arquivo `.env`**. Ele já está no `.gitignore`. Se o token vazar, gere outro nas configurações do Nerimity.

Ajuste os limites em `config.py`:

| Variável | Padrão | O que faz |
|---|---:|---|
| `TARGET_MAX_MB` | `50.0` | Limite real de tamanho do seu servidor/canal no Nerimity (em MB) |
| `SAFETY_FACTOR` | `0.90` | Margem de segurança: mira 90% do limite |
| `MAX_HEIGHT` | `720` | Resolução máxima na primeira tentativa de compressão |
| `AUDIO_BITRATE_KBPS` | `96` | Bitrate de áudio na recompressão |
| `MIN_VIDEO_BITRATE_KBPS` | `100` | Bitrate mínimo; abaixo disso o bot avisa que não coube |
| `MAX_LINKS_PER_MESSAGE` | `3` | Máximo de links processados por mensagem |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | Downloads simultâneos (um link travado não bloqueia a fila) |
| `DOWNLOAD_DIR` | `downloads` | Pasta temporária (limpa automaticamente) |
| `DOWNLOAD_TIMEOUT_S` | `120` | Timeout do proxy ou do `yt-dlp`, por tentativa |
| `FFMPEG_TIMEOUT_S` | `240` | Timeout de cada tentativa do ffmpeg |
| `UPLOAD_TIMEOUT_S` | `60` | Token CDN + upload + POST da mensagem |
| `IMAGE_TIMEOUT_S` | `20` | Sondagem de imagem (links `/video/` pulam esta etapa) |
| `JOB_TIMEOUT_S` | `300` | Limite total por job |
| `ALLOWED_HOSTS` / `BLOCKED_HOSTS` | vazios | Filtros opcionais de domínio |
| `IGNORE_IMAGE_URLS` / `IMAGE_EXTENSIONS` | `True` / jpg, png, etc. | Ignora links diretos de imagem (ex.: anexos do Discord CDN), sem afetar `.webm`/`.mp4` |

## ▶️ Executando

```bash
python bot.py
```

Ou, usando o venv sem ativá-lo:

```bash
.venv/bin/python -u bot.py
```

Para rodar em segundo plano:

```bash
nohup .venv/bin/python -u bot.py > bot.log 2>&1 &
```

Saída esperada na inicialização:

```text
[2026-11-26 20:00:10] Logado como <seu_bot>. Limite: 50 MB
[2026-11-26 20:00:10] 2 worker(s) iniciado(s).
The bot is now listening to incoming connections.
```

## 🧠 Como funciona

1. `on_message_create` extrai URLs do conteúdo da mensagem.
2. Ignora mensagens enviadas pelo próprio bot e comandos que começam com `ab!`.
3. Ignora links de imagem (por extensão, ex.: `.jpg` do Discord CDN); vídeos continuam na fila.
4. Cada URL entra numa fila assíncrona.
5. O worker baixa o vídeo:
   - primeiro via proxy de fix embed (`og:video` / API do FxTwitter);
   - se falhar, usa `yt-dlp` no domínio original como precaução.
6. O arquivo é verificado com `ffprobe` para confirmar que contém vídeo e obter a duração.
7. Se passar de `TARGET_MAX_MB * SAFETY_FACTOR`, o `ffmpeg` recompensa em tentativas progressivas.
8. O bot envia o vídeo anexo com a legenda `[q:id-da-mensagem]`, citando a mensagem onde o link foi postado (se o ID não estiver disponível, usa `[url](url)`).
9. A pasta temporária é removida ao final.

## 🔧 Notas técnicas

- O upload usa a API atual do Nerimity, não a implementação antiga da lib `nerimity`:
  1. `POST /api/channels/{id}/cdn/token`
  2. upload binário em `https://cdn.nerimity.com/attachments/{id}` com `Authorization`, `File-Name` e `Content-Type`
  3. `POST /api/channels/{id}/messages` com `nerimityCdnFileId`
- Canais privados podem exigir supporter/moderador do Nerimity para anexos.
- Pelo proxy: Instagram, X/Twitter, TikTok e Reddit. Pelo fallback `yt-dlp`: qualquer site suportado pelo extractor dele (incluindo YouTube).
- Em Android/Termux a resolução IPv6 pode travar tanto o WebSocket quanto o
  `requests` (o timeout dele não cobre DNS). O bot força `getaddrinfo` para
  IPv4 e usa `open_timeout=60` no websocket, com reconexão com backoff.
- Os logs de job incluem horário e duração de cada etapa (`[proxy]`,
  `[yt-dlp]`, `[ffmpeg]`, `[job]`).

## 🛠️ Solução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| `ERRO: defina NERIMITY_TOKEN` | `.env` ausente ou vazio | Copie `.env.example` para `.env` e preencha |
| `ERRO: ffmpeg não encontrado` | ffmpeg fora do `PATH` | Instale e reinicie o terminal |
| `falha ao gerar token CDN` | Token inválido ou sem permissão | Gere outro token no Nerimity |
| `não parece conter vídeo` | Link sem stream de vídeo | Use um link de vídeo válido |
| `Não foi possível compactar...` | Arquivo grande demais mesmo no mínimo | Aumente `TARGET_MAX_MB` ou use vídeo menor |
| `Unable to download media from this link.` | Post do Instagram sem vídeo público (só imagem ou requer login) | Nada a fazer; use um link com vídeo público |

## 📱 Rodando no Termux (Android)

No celular, instale as dependências uma vez:

```bash
pkg update -y
pkg install -y python ffmpeg tmux termux-api
pip install -r requirements.txt
```

Copie a pasta do bot para `~/video-bot`, entre nela e configure o token:

```bash
cd ~/video-bot
cp .env.example .env   # edite e cole seu token
chmod 600 .env
```

Use os scripts inclusos:

```bash
./start.sh     # inicia em tmux (session "videobot") com wake-lock e auto-restart
./stop.sh      # para o bot
tail -f bot.log             # acompanha os logs
tmux attach -t videobot     # abre a sessão ao vivo
```

> O `start.sh` usa `termux-wake-lock` para evitar que o Android suspenda o processo e reinicia o bot automaticamente se ele cair.

## 📄 Licença

Use como quiser, nos termos do repositório. O `yt-dlp` e o `nerimity` possuem suas próprias licenças.
