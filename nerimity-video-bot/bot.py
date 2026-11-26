

"""Bot Nerimity: detecta links de vídeo nas mensagens, baixa pelos proxys de
fix embed (com yt-dlp como fallback), comprime com ffmpeg e reenvia no canal.

Uso:
    export NERIMITY_TOKEN="seu_token"
    .venv/bin/python bot.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import quote, urlparse
from uuid import uuid4
from typing import Any

import aiohttp
import nerimity
import websockets
import yt_dlp

import config
import embed_download

# A versao instalada da lib nerimity tem um bug em Embed.__init__: a linha
# `self.style` (solta, sem atribuicao) estoura AttributeError ao deserializar
# QUALQUER mensagem com embed/htmlEmbed, derrubando o bot inteiro.
# Corrigimos por monkeypatch na carga do modulo (sobrevive a pip install -U).
from nerimity.embed import Embed as _Embed
from nerimity._enums import EmbedTypes as _EmbedTypes


def _embed_init_fixed(self, data: str = "", type: int = _EmbedTypes.HTML, **kw) -> None:
    self.type = type
    self.style = kw.get("style")
    self.body = kw.get("body") or (
        _Embed._decode(data) if type == _EmbedTypes.HTML and data else data
    )


_Embed.__init__ = _embed_init_fixed

# requests/urllib3 resolvem DNS em thread e o timeout deles NÃO cobre a
# resolução: no Android isso trava minutos no IPv6. Força IPv4 em todo o
# processo (asyncio, requests, yt-dlp).
_original_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_getaddrinfo

# No Android, a resolução AAAA/IPv6 do asyncio pode travar por minutos e é a
# causa dos "timed out during opening handshake". Força IPv4 (o curl resolve
# rápido) para websocket e aiohttp.
async def _ipv4_getaddrinfo(self, host, port, *, family=0, type=0, proto=0, flags=0):
    # O loop do asyncio herda getaddrinfo de BaseEventLoop (não de
    # AbstractEventLoop) — precisamos sobrescrever as duas classes.
    return await self.run_in_executor(
        None,
        socket.getaddrinfo,
        host,
        port,
        socket.AF_INET,
        type,
        proto,
        flags,
    )


asyncio.BaseEventLoop.getaddrinfo = _ipv4_getaddrinfo
asyncio.AbstractEventLoop.getaddrinfo = _ipv4_getaddrinfo

# A lib chama websockets.connect() sem open_timeout; o handshake TCP até o
# Cloudflare está levando ~23s no Termux e o padrão da lib é 10s.
_original_ws_connect = websockets.connect


def _ws_connect(*args, **kwargs):
    kwargs.setdefault("open_timeout", 60)
    return _original_ws_connect(*args, **kwargs)


websockets.connect = _ws_connect

URL_RE = re.compile(r"https?://[^\s<>\[\]()\"']+", re.IGNORECASE)
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".avif"}
CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".m4v": "video/x-m4v",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".avif": "image/avif",
}


def content_type_of(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")

client = nerimity.Client(token=config.NERIMITY_TOKEN, prefix=config.COMMAND_PREFIX)
queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
_queue_loop = None
in_flight: set[tuple[str, int]] = set()
_workers: list[asyncio.Task] = []
_connected = False


def log(message: str) -> None:
    """Log com horário local — essencial para medir a demora de cada etapa."""
    print(f"[{time.strftime('%F %T')}] {message}", flush=True)


# --------------------------------------------------------------------------- #
# helpers gerais
# --------------------------------------------------------------------------- #
def preflight() -> None:
    if not config.NERIMITY_TOKEN:
        raise SystemExit(
            "ERRO: defina NERIMITY_TOKEN (variável de ambiente ou config.py)."
        )
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ERRO: ffmpeg não encontrado no PATH.")
    if shutil.which("ffprobe") is None:
        raise SystemExit("ERRO: ffprobe não encontrado no PATH.")


def normalize_message(raw: Any) -> nerimity.Message | None:
    """Compatível com versões antigas (payload dict) e novas (objeto Message)."""
    if isinstance(raw, nerimity.Message):
        return raw
    if isinstance(raw, dict):
        payload = raw.get("message", raw)
        try:
            return nerimity.Message.deserialize(payload)
        except Exception:
            traceback.print_exc()
    return None


def normalize_url(url: str) -> str:
    """Mostra sempre o domínio original na legenda, mesmo vindo de um proxy."""
    return embed_download.to_original_url(url)


def build_caption(message_id: int | str | None, original_url: str) -> str:
    """Cita a mensagem que continha o vídeo; usa a URL se não houver ID."""
    if message_id:
        return f"[q:{message_id}]"
    return f"[{original_url}]({original_url})"


def is_instagram_link(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(
        host == domain or host.endswith("." + domain)
        for domain in ("instagram.com", "oginstagram.com")
    )


def is_tiktok_link(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(
        host == domain or host.endswith("." + domain)
        for domain in ("tiktok.com", "tnktok.com")
    )


def is_reddit_link(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(
        host == domain or host.endswith("." + domain)
        for domain in (
            "reddit.com",
            "redd.it",
            "vxreddit.com",
            "i.redd.it",
            "preview.redd.it",
            "external-preview.redd.it",
            "redditmedia.com",
        )
    )


def is_image_social_link(url: str) -> bool:
    """Redes com suporte a download de imagem: TikTok, Instagram e Reddit."""
    return is_instagram_link(url) or is_tiktok_link(url) or is_reddit_link(url)


def is_image_url(url: str) -> bool:
    """Detecta links diretos de imagem, como anexos .jpg do Discord CDN."""
    if not config.IGNORE_IMAGE_URLS:
        return False
    # Mídias do Reddit (i.redd.it etc.) devem ser processadas, não ignoradas.
    if is_reddit_link(url):
        return False
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in config.IMAGE_EXTENSIONS)


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip(".,;:!?)]}")
        if url not in urls:
            urls.append(url)
    return urls[: config.MAX_LINKS_PER_MESSAGE]


def _channel(channel_id: int) -> nerimity.Channel:
    try:
        return nerimity.Channel.get_channel(int(channel_id))
    except (ValueError, KeyError):
        # Fallback: send_message() só usa o id do canal.
        channel = nerimity.Channel()
        channel.id = int(channel_id)
        return channel


async def get_cdn_token(channel_id: int, session: aiohttp.ClientSession) -> str:
    headers = {"Authorization": config.NERIMITY_TOKEN}
    url = f"https://nerimity.com/api/channels/{channel_id}/cdn/token"
    async with session.post(url, headers=headers) as response:
        data = await response.json(content_type=None)
        if response.status != 200 or not data.get("token"):
            raise RuntimeError(f"falha ao gerar token CDN: {data}")
        return data["token"]


async def upload_to_cdn(
    channel_id: int, file_path: Path, session: aiohttp.ClientSession
) -> str:
    token = await get_cdn_token(channel_id, session)
    payload = await asyncio.to_thread(file_path.read_bytes)
    headers = {
        "Authorization": token,
        "File-Name": quote(file_path.name),
        "Content-Type": content_type_of(file_path),
    }
    url = f"https://cdn.nerimity.com/attachments/{channel_id}"
    async with session.post(url, headers=headers, data=payload) as response:
        data = await response.json(content_type=None)
        if response.status != 200 or not data.get("fileId"):
            raise RuntimeError(f"falha no upload CDN: {data}")
        return data["fileId"]


async def send_to_channel(
    channel_id: int, text: str, file_path: Path | None = None
) -> None:
    """Envia mensagem (com anexo opcional) usando a API atual do Nerimity."""
    headers = {
        "Authorization": config.NERIMITY_TOKEN,
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {"content": text, "buttons": []}

    timeout = aiohttp.ClientTimeout(total=config.UPLOAD_TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if file_path is not None:
            body["nerimityCdnFileId"] = await upload_to_cdn(
                channel_id, file_path, session
            )
        url = f"https://nerimity.com/api/channels/{channel_id}/messages"
        async with session.post(url, headers=headers, json=body) as response:
            data = await response.json(content_type=None)
            if response.status != 200:
                raise RuntimeError(f"falha ao enviar mensagem: {data}")


async def safe_send(channel_id: int, text: str) -> None:
    try:
        await send_to_channel(channel_id, text)
    except Exception as exc:
        log(f"[erro ao avisar no canal {channel_id}]: {exc}")


async def open_dm_channel(user_id: int | str, session: aiohttp.ClientSession) -> int:
    """Abre (ou reaproveita) o canal de DM com o usuário e devolve o channelId."""
    headers = {"Authorization": config.NERIMITY_TOKEN}
    url = f"https://nerimity.com/api/users/{user_id}/open-channel"
    async with session.post(url, headers=headers, json={}) as response:
        data = await response.json(content_type=None)
        channel_id = data.get("channelId") if isinstance(data, dict) else None
        if response.status != 200 or not channel_id:
            raise RuntimeError(f"falha ao abrir DM com {user_id}: {data}")
        return int(channel_id)


async def send_dm(user_id: int | str, text: str) -> None:
    """Envia uma mensagem no privado (DM) do usuário."""
    headers = {
        "Authorization": config.NERIMITY_TOKEN,
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {"content": text, "buttons": []}
    async with aiohttp.ClientSession() as session:
        channel_id = await open_dm_channel(user_id, session)
        url = f"https://nerimity.com/api/channels/{channel_id}/messages"
        async with session.post(url, headers=headers, json=body) as response:
            data = await response.json(content_type=None)
            if response.status != 200:
                raise RuntimeError(f"falha ao enviar DM para {user_id}: {data}")


async def safe_send_dm(user_id: int | str | None, text: str) -> None:
    """Envia no privado sem derrubar o job em caso de erro."""
    if not user_id:
        log("[aviso DM]: sem author_id no job; não foi possível enviar no privado")
        return
    try:
        await send_dm(user_id, text)
    except Exception as exc:
        log(f"[erro ao avisar no privado de {user_id}]: {exc}")


# --------------------------------------------------------------------------- #
# download (yt-dlp) e compressão (ffmpeg)
# --------------------------------------------------------------------------- #
def download_sync(url: str, output_template: str) -> dict:
    options = {
        "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 2,
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=True)


def pick_downloaded_file(job_dir: Path) -> Path | None:
    files = [p for p in job_dir.iterdir() if p.is_file()]
    if not files:
        return None
    # Prefere arquivos de vídeo; em caso de dúvida, o maior.
    videos = [p for p in files if p.suffix.lower() in VIDEO_EXTENSIONS]
    return max(videos or files, key=lambda p: p.stat().st_size)


async def probe_media(path: Path) -> tuple[float | None, bool]:
    """Retorna (duração em segundos, arquivo contém stream de vídeo)."""
    # Imagens estáticas aparecem como codec_type=video no ffprobe; evita
    # mandar .webp/.jpg para a etapa de compressão de vídeo.
    if path.suffix.lower() in config.IMAGE_EXTENSIONS:
        return None, False
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "error",
            "-of", "json",
            "-show_entries", "format=duration:stream=codec_type,duration",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        data = json.loads(out.decode())
        streams = data.get("streams") or []
        has_video = any(s.get("codec_type") == "video" for s in streams)

        raw = (data.get("format") or {}).get("duration")
        if raw in (None, "N/A"):
            raw = next(
                (
                    s.get("duration")
                    for s in streams
                    if s.get("duration") not in (None, "N/A")
                ),
                None,
            )
        try:
            duration = float(raw) if raw else None
        except (TypeError, ValueError):
            duration = None
        return duration, has_video
    except Exception:
        return None, False


async def run_ffmpeg(src: Path, dst: Path, args: list[str]) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src),
            *args,
            str(dst),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(
            proc.communicate(), timeout=config.FFMPEG_TIMEOUT_S
        )
        if proc.returncode != 0:
            log(f"[ffmpeg] {err.decode(errors='replace')[-800:]}")
            return False
        return dst.exists() and dst.stat().st_size > 0
    except asyncio.TimeoutError:
        # wait_for() sozinho não mata o ffmpeg: ele continuaria competindo por
        # CPU com os próximos jobs. Mata e espera o processo sair.
        proc.kill()
        await proc.communicate()
        log(f"[ffmpeg] timeout ao processar {src.name} ({config.FFMPEG_TIMEOUT_S}s)")
        return False
    except Exception:
        traceback.print_exc()
        return False


async def compress_until_fits(
    src: Path, job_dir: Path, duration: float | None
) -> tuple[Path | None, int]:
    """Tenta progressivamente até ficar abaixo do limite. Retorna (arquivo, tentativas)."""
    limit_bytes = int(config.TARGET_MAX_MB * 1024 * 1024 * config.SAFETY_FACTOR)
    if src.stat().st_size <= limit_bytes:
        return src, 0

    if not duration or duration <= 0:
        duration, _ = await probe_media(src)
    if not duration or duration <= 0:
        return None, 0

    total_kbps = max(200, int(limit_bytes * 8 / duration / 1000))
    audio_kbps = min(config.AUDIO_BITRATE_KBPS, max(64, int(total_kbps * 0.15)))
    base_video_kbps = max(
        config.MIN_VIDEO_BITRATE_KBPS,
        int(total_kbps - audio_kbps - 64),  # 64 kbps de margem para container/metadata
    )

    attempts = 0
    for height, factor in ((720, 1.0), (480, 0.85), (360, 0.70), (240, 0.55)):
        video_kbps = max(
            config.MIN_VIDEO_BITRATE_KBPS, int(base_video_kbps * factor)
        )
        out = job_dir / f"compressed_{height}_{video_kbps}.mp4"
        args = [
            "-vf", f"scale=-2:'min({height},ih)'",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-b:v", f"{video_kbps}k",
            "-maxrate", f"{int(video_kbps * 1.2)}k",
            "-bufsize", f"{int(video_kbps * 2)}k",
            "-c:a", "aac",
            "-b:a", f"{audio_kbps}k",
            "-ac", "2",
            "-movflags", "+faststart",
        ]
        attempts += 1
        if not await run_ffmpeg(src, out, args):
            continue
        if out.stat().st_size <= limit_bytes:
            return out, attempts

    return None, attempts


# --------------------------------------------------------------------------- #
# processamento dos jobs
# --------------------------------------------------------------------------- #
def looks_like_video(url: str) -> bool:
    """Links com /video/, /reel/, /shorts/ etc. são vídeo — não sonda imagens."""
    path = urlparse(url).path.lower()
    return any(
        marker in path
        for marker in ("/video/", "/reel/", "/reels/", "/shorts/", "/clip/")
    )


async def try_send_images(
    url: str,
    channel_id: int,
    message_id: int | str | None,
    job_dir: Path,
) -> bool:
    """Tenta baixar/enviar imagens. Apenas TikTok e Instagram são aceitos."""
    if not is_image_social_link(url):
        return False
    started = time.monotonic()
    try:
        images = await asyncio.wait_for(
            asyncio.to_thread(embed_download.download_images, url, str(job_dir)),
            timeout=config.IMAGE_TIMEOUT_S,
        )
    except Exception as exc:
        log(
            f"[imagem] falha ao processar {url} em "
            f"{time.monotonic() - started:.1f}s: {exc}"
        )
        return False

    if not images:
        log(f"[imagem] sem imagens em {time.monotonic() - started:.1f}s: {url}")
        return False

    caption = build_caption(message_id, normalize_url(url))
    for image in images:
        await send_to_channel(channel_id, caption, image)
    log(
        f"[imagem] {len(images)} imagem(ns) enviada(s) em "
        f"{time.monotonic() - started:.1f}s de {url}"
    )
    return True


async def process_job(job: dict[str, Any]) -> None:
    url: str = job["url"]
    original_url = normalize_url(url)  # usado apenas se não houver ID da mensagem
    channel_id: int = job["channel_id"]
    message_id = job.get("message_id")
    author_id = job.get("author_id")
    job_dir = Path(config.DOWNLOAD_DIR) / uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    log(f"[job] início canal={channel_id} msg={message_id} {url}")

    output_template = str(job_dir / "source.%(ext)s")

    try:
        # 0) Posts de imagem do TikTok/Instagram são resolvidos antes do vídeo,
        #    mas links obviamente de vídeo (/video/, /reel/) não pagam essa
        #    sondagem — ela custava 30-170s antes de começar o download.
        if not looks_like_video(url) and await try_send_images(
            url, channel_id, message_id, job_dir
        ):
            return

        # 1) tenta baixar pelo proxy de fix embed (og:video / API do FxTwitter);
        # 2) se falhar, usa o yt-dlp como precaução no domínio original.
        info: dict[str, Any] | None = None
        stage = time.monotonic()
        try:
            info = await asyncio.wait_for(
                asyncio.to_thread(
                    embed_download.download_from_url, url, output_template
                ),
                timeout=config.DOWNLOAD_TIMEOUT_S,
            )
            log(f"[proxy] baixou em {time.monotonic() - stage:.1f}s: {url}")
        except Exception as exc:
            log(
                f"[proxy] falhou em {time.monotonic() - stage:.1f}s ({exc}); "
                "usando yt-dlp como fallback..."
            )
            fallback_url = embed_download.to_original_url(url)
            stage = time.monotonic()
            try:
                info = await asyncio.wait_for(
                    asyncio.to_thread(download_sync, fallback_url, output_template),
                    timeout=config.DOWNLOAD_TIMEOUT_S,
                )
                log(f"[yt-dlp] baixou em {time.monotonic() - stage:.1f}s: {url}")
            except Exception as fallback_exc:
                log(
                    f"[yt-dlp] falhou em {time.monotonic() - stage:.1f}s "
                    f"({fallback_exc})"
                )
                # Última tentativa: o proxy pode ter imagens que o og:video
                # não expôs (carrosséis, por exemplo).
                if await try_send_images(url, channel_id, message_id, job_dir):
                    return
                # Avisa apenas no privado; não posta falha no canal.
                if is_image_social_link(url):
                    quote = (
                        f"[q:{message_id}]"
                        if message_id
                        else f"[{original_url}]({original_url})"
                    )
                    await safe_send_dm(
                        author_id,
                        f"{quote}\n⚠️ Unable to download media from this link.",
                    )
                return

        source = pick_downloaded_file(job_dir)
        if source is None:
            if await try_send_images(url, channel_id, message_id, job_dir):
                return
            await safe_send(channel_id, f"❌ Não foi possível baixar a mídia: {url}")
            return

        # Imagem baixada direto do post: envia sem reprocessar/compactar.
        if source.suffix.lower() in config.IMAGE_EXTENSIONS:
            await send_to_channel(
                channel_id, build_caption(message_id, original_url), source
            )
            log(f"[job] imagem enviada em {time.monotonic() - started:.1f}s: {url}")
            return

        probed_duration, has_video = await probe_media(source)
        if not has_video:
            # Arquivo baixado não é vídeo: pode ser uma imagem direta do post.
            if await try_send_images(url, channel_id, message_id, job_dir):
                return
            await safe_send(channel_id, f"⚠️ O link não parece conter vídeo: {url}")
            return

        duration = (info or {}).get("duration") or probed_duration
        stage = time.monotonic()
        final_file, attempts = await compress_until_fits(source, job_dir, duration)
        log(
            f"[ffmpeg] {attempts} tentativa(s) em "
            f"{time.monotonic() - stage:.1f}s; "
            f"{source.stat().st_size / 1048576:.1f} MB -> "
            f"{(final_file.stat().st_size / 1048576 if final_file else 0):.1f} MB"
        )

        if final_file is None:
            await safe_send(
                channel_id,
                "⚠️ Não foi possível compactar o vídeo abaixo do limite de "
                f"**{config.TARGET_MAX_MB:.0f} MB** mesmo no bitrate mínimo. "
                "O arquivo não foi enviado.",
            )
            return

        # Legenda curta: cita a mensagem original; o vídeo vai como anexo.
        caption = build_caption(message_id, original_url)
        await send_to_channel(channel_id, caption, final_file)
        log(f"[job] vídeo enviado em {time.monotonic() - started:.1f}s: {url}")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


async def worker(name: int) -> None:
    while True:
        job = await queue.get()
        key = (job["url"], job["channel_id"])
        try:
            await asyncio.wait_for(
                process_job(job), timeout=config.JOB_TIMEOUT_S
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            log(f"[{name}] job abandonado após {config.JOB_TIMEOUT_S}s: {job['url']}")
        except Exception as exc:
            # Links que não são vídeo (pesquisa, páginas comuns, etc.) falham
            # no downloader. Mantém o erro apenas no log, sem postar no canal.
            log(f"[{name}] falha no job {job['url']}: {exc}")
            traceback.print_exc()
        finally:
            in_flight.discard(key)
            queue.task_done()


# --------------------------------------------------------------------------- #
# eventos do Nerimity
# --------------------------------------------------------------------------- #
@client.listen("on_ready")
async def on_ready(client_info: dict) -> None:
    global queue, _queue_loop, _workers, _connected
    _connected = True
    account = getattr(client.account, "username", "?")
    log(f"Logado como {account}. Limite: {config.TARGET_MAX_MB:.0f} MB")

    loop = asyncio.get_running_loop()
    if _queue_loop is not loop:
        # O restart_always da lib roda asyncio.run(main()) de novo a cada
        # crash, criando um event loop NOVO. A Queue antiga fica presa ao
        # loop morto (get() estoura "bound to a different event loop") e as
        # tasks dos workers morrem com ele. Recriamos os dois aqui.
        for tarefa in _workers:
            if not tarefa.done():
                try:
                    tarefa.cancel()
                except RuntimeError:
                    pass
        queue = asyncio.Queue()
        _queue_loop = loop
        _workers = []
        for index in range(config.MAX_CONCURRENT_DOWNLOADS):
            _workers.append(
                asyncio.create_task(worker(index), name=f"video-worker-{index}")
            )
        log(f"{config.MAX_CONCURRENT_DOWNLOADS} worker(s) iniciado(s).")


@client.listen("on_message_create")
async def on_message_created(raw: Any) -> None:
    message = normalize_message(raw)
    if message is None:
        return

    me = str(getattr(client.account, "id", "") or "")
    if me and str(getattr(message, "author_id", "")) == me:
        return

    content = message.content or ""
    if content.startswith(config.COMMAND_PREFIX):
        return

    channel_id = int(message.channel_id)
    message_id = getattr(message, "id", None)

    for url in extract_urls(content):
        host = (urlparse(url).hostname or "").lower()
        if config.BLOCKED_HOSTS and any(host.endswith(h) for h in config.BLOCKED_HOSTS):
            continue
        if config.ALLOWED_HOSTS and not any(
            host.endswith(h) for h in config.ALLOWED_HOSTS
        ):
            continue
        if is_image_url(url):
            log(f"Ignorando imagem: {url}")
            continue

        key = (url, channel_id)
        if key in in_flight:
            continue
        in_flight.add(key)
        author_id = getattr(message, "author_id", None)
        await queue.put(
            {
                "url": url,
                "channel_id": channel_id,
                "message_id": message_id,
                "author_id": author_id,
            }
        )
        log(f"Na fila: {url} (canal {channel_id}, msg {message_id}, autor {author_id})")


if __name__ == "__main__":
    preflight()
    # A lib com restart_always=True tenta reconectar em loop sem espera, o que
    # incha o log e não deixa a rede estabilizar. Fazemos o backoff aqui: após
    # cada queda, aguardamos antes de abrir uma nova conexão.
    def _watchdog() -> None:
        if not _connected:
            log("Sem conexão com Nerimity em 90s; reiniciando processo...")
            os._exit(1)

    while True:
        _connected = False
        timer = threading.Timer(90, _watchdog)
        timer.daemon = True
        timer.start()
        try:
            # debug_mode=True roda asyncio.run(main()) direto: qualquer falha
            # de handshake volta para o nosso loop, que aplica o backoff.
            client.run(debug_mode=True)
        except SystemExit:
            pass
        except Exception as exc:
            log(f"Cliente encerrado: {type(exc).__name__}: {exc}")
        finally:
            timer.cancel()
        log("Reconectando em 15s...")
        time.sleep(15)