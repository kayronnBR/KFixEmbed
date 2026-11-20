"""Download de vídeos usando os proxys de fix embed (sem yt-dlp).

Cada site é reescrito para o proxy correspondente e a mídia é obtida de:
- og:video / og:video:secure_url da página do proxy;
- API JSON do FxTwitter (X/Twitter);
- endpoint direto do tnktok (TikTok).

Isto elimina a dependência do yt-dlp para as redes sociais suportadas.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import requests

USER_AGENT = "Mozilla/5.0 (compatible; NerimityVideoBot/1.0)"

# site original -> proxy de embed usado como fonte do download
PROXIES: dict[str, str] = {
    "instagram.com": "oginstagram.com",
    "twitter.com": "fxtwitter.com",
    "x.com": "fxtwitter.com",
    "tiktok.com": "tnktok.com",
    "reddit.com": "vxreddit.com",
}

PROXY_HOSTS = tuple(set(PROXIES.values())) + (
    "api.fxtwitter.com",
    "d.fxtwitter.com",
)

# proxy -> site original (usado pelo fallback do yt-dlp e pela legenda)
REVERSE_PROXIES: dict[str, str] = {
    "oginstagram.com": "www.instagram.com",
    "fxtwitter.com": "x.com",
    "tnktok.com": "www.tiktok.com",
    "vxreddit.com": "www.reddit.com",
}

_META_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
_STATUS_RE = re.compile(r"/(?:status|statuses)/(\d+)")
_TIKTOK_ID_RE = re.compile(r"/(?:video|t)/(\d+)")

# ordem de preferência das meta tags de vídeo
_VIDEO_META_KEYS = (
    "og:video",
    "og:video:secure_url",
    "og:video:url",
    "og:video:type",
)


class NoVideoAvailable(RuntimeError):
    """O proxy não expõe nenhum vídeo (post só com imagem, por exemplo)."""


@dataclass
class Media:
    video_url: str
    title: str
    source: str


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def to_original_url(url: str) -> str:
    """Converte um link de proxy de volta para o domínio original."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    for proxy, original in REVERSE_PROXIES.items():
        if host == proxy or host.endswith("." + proxy):
            netloc = parsed.netloc.replace(proxy, original, 1)
            return urlunparse(parsed._replace(netloc=netloc))
    return url


def to_proxy_url(url: str) -> str:
    """Reescreve o host original para o proxy correspondente."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    # Já é um proxy (ou endpoint da API): mantém como está.
    if any(host == p or host.endswith("." + p) for p in PROXY_HOSTS):
        return url

    for original, proxy in PROXIES.items():
        if host == original or host.endswith("." + original):
            prefix = host[: -len(original)]  # ex.: "www." ou "vm."
            if prefix == "www.":
                prefix = ""  # alguns proxys só servem no domínio raiz
            new_host = prefix + proxy
            if parsed.port:
                new_host += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=new_host))

    raise ValueError(f"site não suportado: {host or url}")


def _meta_tags(page: str) -> dict[str, str]:
    """Extrai metatags Open Graph, aceitando qualquer ordem de atributos."""
    tags: dict[str, str] = {}
    for raw in _META_RE.findall(page):
        attrs = {k.lower(): html.unescape(v) for k, v in _ATTR_RE.findall(raw)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        value = attrs.get("content") or attrs.get("value")
        if key and value and key not in tags:
            tags[key] = value
    return tags


def _og_video(page: str, base_url: str) -> str | None:
    meta = _meta_tags(page)
    for key in _VIDEO_META_KEYS:
        value = meta.get(key, "").strip()
        if not value or value.startswith("video/"):  # ignora og:video:type
            continue
        return urljoin(base_url, value)
    return None


def _og_title(page: str) -> str:
    return _meta_tags(page).get("og:title", "Vídeo").strip()


def _resolve_twitter(url: str) -> Media:
    match = _STATUS_RE.search(url)
    if not match:
        raise ValueError("link do X/Twitter sem ID de status")

    status_id = match.group(1)
    headers = {"User-Agent": USER_AGENT}

    for endpoint in (
        f"https://api.fxtwitter.com/2/status/{status_id}",
        f"https://api.fxtwitter.com/status/{status_id}",
    ):
        try:
            response = requests.get(endpoint, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, json.JSONDecodeError):
            continue

        if data.get("code") not in (None, 200):
            continue

        status = data.get("status") or data.get("tweet") or {}
        media = status.get("media") or {}
        videos = media.get("videos") or []

        for video in videos:
            direct = video.get("url") or video.get("transcode_url")
            if direct:
                return Media(direct, status.get("text", "Vídeo")[:120], endpoint)

        external = media.get("external")
        if external and external.get("url"):
            return Media(external["url"], status.get("text", "Vídeo")[:120], endpoint)

    raise RuntimeError("nenhum vídeo encontrado na API do FxTwitter")


def _resolve_tiktok(proxy_url: str) -> Media:
    try:
        response = requests.get(
            proxy_url, headers={"User-Agent": USER_AGENT}, timeout=20
        )
        response.raise_for_status()
        direct = _og_video(response.text, response.url)
        if direct:
            return Media(direct, _og_title(response.text), "tnktok")
    except requests.RequestException:
        pass

    # Fallback: endpoint direto do tnktok usa apenas o ID numérico do vídeo.
    match = _TIKTOK_ID_RE.search(proxy_url)
    if match:
        return Media(
            f"https://tnktok.com/generate/video/{match.group(1)}.mp4",
            "Vídeo do TikTok",
            "tnktok",
        )
    raise RuntimeError("não foi possível resolver o vídeo do TikTok")


def resolve_media(url: str) -> Media:
    """Descobre a URL direta do MP4 a partir do proxy de embed."""
    proxy_url = to_proxy_url(url)
    host = _host_of(proxy_url)

    # X/Twitter: API JSON é mais estável que a meta tag.
    if "fxtwitter" in host:
        return _resolve_twitter(url)

    if "tnktok" in host or "tiktok" in host:
        return _resolve_tiktok(proxy_url)

    response = requests.get(
        proxy_url, headers={"User-Agent": USER_AGENT}, timeout=20
    )
    response.raise_for_status()
    direct = _og_video(response.text, response.url)
    if not direct:
        raise NoVideoAvailable(f"og:video não encontrado em {host}")
    return Media(direct, _og_title(response.text), host)


def download_from_url(url: str, output_template: str) -> dict[str, Any]:
    """Resolve o proxy, baixa o MP4 e devolve info compatível com o bot."""
    media = resolve_media(url)
    print(f"[download] {media.source}: {media.video_url[:120]}")

    response = requests.get(
        media.video_url,
        stream=True,
        timeout=(20, 120),
        headers={"User-Agent": USER_AGENT, "Referer": media.video_url},
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    ext = "mp4"
    if "webm" in content_type:
        ext = "webm"
    elif "mov" in content_type:
        ext = "mov"

    destination = Path(output_template.replace("%(ext)s", ext))
    with destination.open("wb") as file:
        for chunk in response.iter_content(chunk_size=1 << 20):
            if chunk:
                file.write(chunk)

    return {
        "title": media.title,
        "duration": None,
        "vcodec": "h264",
        "ext": ext,
    }
