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
MAX_CONCURRENT_DOWNLOADS = 1  # downloads simultâneos (1 = sequencial, mais seguro)
DOWNLOAD_DIR = "downloads"    # pasta temporária (limpa após cada job)

# ---------------------------------------------------------------- tempo (s)
DOWNLOAD_TIMEOUT_S = 300
FFMPEG_TIMEOUT_S = 600

# ---------------------------------------------------------------- filtros
# Deixe vazio para aceitar qualquer site suportado pelo yt-dlp.
ALLOWED_HOSTS: list[str] = []
BLOCKED_HOSTS: list[str] = []
