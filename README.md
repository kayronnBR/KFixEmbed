# Nerimity Video Bot

Bot em Python para **Nerimity** que detecta links de vídeo nas mensagens, baixa, compacta com `ffmpeg` até caber no limite do servidor e responde no mesmo canal enviando **apenas o arquivo de vídeo**, com o link original em uma legenda curta.

Exemplo de mensagem enviada pelo bot:

```text
[https://www.instagram.com/reels/Df8NHYXGoLr/](https://www.instagram.com/reels/Df8NHYXGoLr/)
```

> Não reenvia links separados, não mexe em embeds e não contém a ponte com o Astroid.

## ✨ Funcionalidades

- Detecta automaticamente URLs em mensagens do Nerimity (`on_message_create`).
- Baixa vídeos com `yt-dlp` (YouTube, TikTok, X, etc.).
- Suporte especial a links **OGInstagram / Instagram**: download direto pelo `og:video`, contornando a falta de suporte do yt-dlp ao domínio `oginstagram.com`.
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
| `MAX_CONCURRENT_DOWNLOADS` | `1` | Downloads simultâneos (1 = sequencial) |
| `DOWNLOAD_DIR` | `downloads` | Pasta temporária (limpa automaticamente) |
| `DOWNLOAD_TIMEOUT_S` | `300` | Timeout do download |
| `FFMPEG_TIMEOUT_S` | `600` | Timeout de cada tentativa do ffmpeg |
| `ALLOWED_HOSTS` / `BLOCKED_HOSTS` | vazios | Filtros opcionais de domínio |

## ▶️ Executando

```bash
python bot.py
```

Ou, usando o venv sem ativá-lo:

```bash
.venv/bin/python -u bot.py
```

Para rodar em segundo plano:

```bater
nohup .venv/bin/python -u bot.py > bot.log 2>&1 &
```

Saúde esperada na inicialização:

```texto
Logado como <seu_bot>. Limite: 50 MB
1 trabalhador(es) iniciado(s).
O bot agora está ouvindo as conexões recebidas.
```

## 🧠 Como função

1. `on_message_create` URLs extras do conteúdo da mensagem.
2. Ignora mensagens invejadas pelo preço bot e comandos que vêm com `ab!`.
3. Cada URL entra numa fila assíncrona.
4. Ó trabalhador baixa o vídeo:
   - `yt-dlp` para sites suportados;
   - baixar direto fazer `og:vídeo` para links `oginstagram`.
5. O arquivo é verificado com `sonda ff` para confirmar que contém vídeo e obter a duração.
6. Se passar de `TARGET_MAX_MB * FATOR_DE SEGURANÇA`, o `ffmpeg` recompensa em tentativas progressivas.
7. O bot envia o vídeo anexo com a legenda `[url](url)`.
8. Uma massa temporária é removida ao final.

## 🔧 Notas técnicas

- O upload usa uma API atual do Nerimity, não uma implementação antiga da lib `nerimidade`:
  1. `POST/api/canais/{id}/cdn/token`
  2. carregar binário em `https://cdn.nerimity.com/attachments/{id}` com `Autorização`, `Nome do arquivo` e `Tipo de conteúdo`
  3. `POST/api/canais/{id}/mensagens` com `nerimityCdnFileId`
- Canais privados podem exigir apoiador/moderador do Nerimity para anos.
- Sites suportados dependem dos extratores do `yt-dlp`, exceto o atalho direto do OGInstagram.

## 🛠️ Resolução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| `ERRO: defina NERIMITY_TOKEN` | `.ambiente` ausente ou vazio | Copiar `.exemplo env` parágrafo `.ambiente` e preencha |
| `ERRO: ffmpeg não encontrado` | ffmpeg fora do `CAMINHO` | Instalar e reiniciar o terminal |
| `falha ao gerar token CDN` | Token invadido ou sem permissão | Gere outro token sem Nerimity |
| `não parece conter vídeo` | Link sem stream de vídeo | Use um link de vídeo |
| `Não foi possível compactar...` | Arquivo grande demais mesmo no mínimo | Aumente `ALVO_MÁX.MB` ou use o vídeo menor |

## 📄 Licença

Use como quiser, nos termos do repositório. O `yt-dlp` e o `nerimidade` possuem suas proprias licenças.
