"""Configurações do bot de vídeos para o Nerimity.

Defina o token via variável de ambiente:
    export NERIMITY_TOKEN="seu_token_aqui"
(ou edite NERIMITY_TOKEN abaixo diretamente)
"""

import os
from pathlib import Path

# Carrega um arquivo .env simples (KEY=VALUE) sem dependências extras.
_env_path = Path(__file__).with_name(".env")
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _value = _line.split("=", 1)
        os.environ.setdefault(
            _key.strip(), _value.strip().strip('"').strip("'")
        )

# ---------------------------------------------------------------- auth
NERIMITY_TOKEN = os.getenv("NERIMITY_TOKEN", "").strip()
COMMAND_PREFIX = "ab!"

# ---------------------------------------------------------------- tamanho
# Limite MÁXIMO real do seu servidor/canal no Nerimity (em MB).
# O bot usa SAFETY_FACTOR para deixar margem e evitar erro no upload.
TARGET_MAX_MB = 50.0
SAFETY_FACTOR = 0.90          # 90% do limite => margem de segurança no upload

# ---------------------------------------------------------------- vídeo
MAX_HEIGHT = 720              # altura máxima na primeira tentativa de compressão
AUDIO_BITRATE_KBPS = 96       # bitrate de áudio na recompressão
MIN_VIDEO_BITRATE_KBPS = 100  # bitrate mínimo de vídeo (se ainda passar do limite, avisa)

# ---------------------------------------------------------------- fila / links
MAX_LINKS_PER_MESSAGE = 3     # quantos links por mensagem são processados
MAX_CONCURRENT_DOWNLOADS = 2  # 2 workers: um link travado não bloqueia a fila inteira
DOWNLOAD_DIR = "downloads"    # pasta temporária (limpa após cada job)

# ---------------------------------------------------------------- tempo (s)
# Timeouts curtos: um link ruim não pode prender a fila por vários minutos.
DOWNLOAD_TIMEOUT_S = 120      # proxy OU yt-dlp, por tentativa
FFMPEG_TIMEOUT_S = 240        # por tentativa de compressão
UPLOAD_TIMEOUT_S = 60         # token CDN + upload + POST da mensagem
IMAGE_TIMEOUT_S = 20          # sondagem de imagem (posts de vídeo não devem pagar isso)
JOB_TIMEOUT_S = 300           # limite total por job ( proxies + compressão )

# ---------------------------------------------------------------- filtros
# Deixe vazio para aceitar qualquer site suportado pelo yt-dlp.
ALLOWED_HOSTS: list[str] = []
BLOCKED_HOSTS: list[str] = []

# Links diretos de imagem são ignorados (ex.: anexos .jpg do Discord CDN).
# Vídeos (.mp4, .webm, .mov, ...) continuam sendo processados normalmente.
IGNORE_IMAGE_URLS = True
IMAGE_EXTENSIONS: list[str] = [
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
    ".heic", ".heif", ".avif", ".tif", ".tiff",
]
