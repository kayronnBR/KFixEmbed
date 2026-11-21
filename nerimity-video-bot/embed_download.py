"""Download de mídia (vídeo e imagem) usando os proxys de fix embed.

Cada site é reescrito para o proxy correspondente e a mídia é obtida de:
- og:video / og:video:secure_url da página do proxy (vídeos);
- API JSON do FxTwitter (X/Twitter);
- endpoint direto do tnktok (TikTok);
- og:image / JSON interno da página do proxy (imagens de TikTok e Instagram).

Somente TikTok e Instagram têm suporte a imagens; as demais redes continuam
sendo tratadas apenas como vídeo.
"""

from __future__ import annotations

import hashlib
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
    "redd.it": "vxreddit.com",
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

# redes com suporte a download de imagem
IMAGE_NETWORKS = ("instagram.com", "tiktok.com", "reddit.com", "redd.it")

# CDNs de imagem do Reddit (evita pegar logo/favicon da página do proxy)
REDDIT_IMAGE_HOSTS = (
    "i.redd.it",
    "preview.redd.it",
    "external-preview.redd.it",
    "b.thumbs.redditmedia.com",
    "redditmedia.com",
)

_IMAGE_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_IMAGE_JSON_RE = re.compile(
    r'"(?:display_url|thumbnail_src|thumbnail_url|image_url|urlList|url)"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)
_IMAGE_CDN_HOSTS = (
    "cdninstagram.com",
    "fbcdn.net",
    "tiktokcdn.com",
    "hiphina.com",
    "byteimg.com",
    "byteimg.org",
)
# endpoint de imagem do proxy tnktok (não é um host de CDN, é um caminho)
_IMAGE_PROXY_PATH = "/generate/image/"
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".avif")
_PAGE_CACHE: dict[str, tuple[str, str]] = {}


class NoVideoAvailable(RuntimeError):
    """O proxy não expõe nenhum vídeo (post só com imagem, por exemplo)."""


class NoImageAvailable(RuntimeError):
    """O proxy não expõe nenhuma imagem utilisável no post."""


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


def is_image_network(url: str) -> bool:
    """True apenas para TikTok/Instagram (e seus proxys)."""
    host = _host_of(url)
    allowed = IMAGE_NETWORKS + ("oginstagram.com", "tnktok.com", "vxreddit.com")
    return any(host == network or host.endswith("." + network) for network in allowed)


def _get_proxy_page(proxy_url: str) -> tuple[str, str]:
    """Busca a página do proxy uma vez por URL (evita request duplicado)."""
    cached = _PAGE_CACHE.get(proxy_url)
    if cached is not None:
        return cached

    response = requests.get(
        proxy_url, headers={"User-Agent": USER_AGENT}, timeout=20
    )
    response.raise_for_status()
    result = (response.text, response.url)
    _PAGE_CACHE[proxy_url] = result
    if len(_PAGE_CACHE) > 32:
        _PAGE_CACHE.pop(next(iter(_PAGE_CACHE)))
    return result


def _all_meta_values(page: str, key: str) -> list[str]:
    """Devolve TODAS as metatags com a chave dada (carrosséis têm várias)."""
    values: list[str] = []
    for raw in _META_RE.findall(page):
        attrs = {k.lower(): html.unescape(v) for k, v in _ATTR_RE.findall(raw)}
        meta_key = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content") or attrs.get("value")
        if meta_key == key and content:
            cleaned = content.strip()
            if cleaned and cleaned not in values:
                values.append(cleaned)
    return values


def _clean_media_url(value: str, base_url: str) -> str:
    value = html.unescape(value)
    value = (
        value.replace("\\/", "/")
        .replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("\\u0026", "&")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
    )
    value = value.rstrip("\\,;:!?)}]'\"")
    return urljoin(base_url, value)


def _looks_like_image(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()

    # Nunca aceitar a página do próprio TikTok/Instagram (gera HTML, não imagem).
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return False
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return False

    # Endpoint de imagem do proxy tnktok: /generate/image/<id>?index=N
    if (host == "tnktok.com" or host.endswith(".tnktok.com")) and path.startswith(
        _IMAGE_PROXY_PATH
    ):
        return True

    # Endpoint de imagem do proxy oginstagram: /offload/<shortcode>/<N>
    # (ignora /offload/<shortcode>/avatar e o favicon do site)
    if (host == "oginstagram.com" or host.endswith(".oginstagram.com")) and path.startswith(
        "/offload/"
    ):
        index = path[len("/offload/"):].strip("/").split("/")[-1]
        return index.isdigit()

    if any(host == cdn or host.endswith("." + cdn) for cdn in _IMAGE_CDN_HOSTS):
        return True
    if any(host == cdn or host.endswith("." + cdn) for cdn in REDDIT_IMAGE_HOSTS):
        return True
    return path.endswith(_IMAGE_EXTS)


def _extract_image_urls(page: str, base_url: str, max_images: int) -> list[str]:
    """Coleta URLs de imagem da página do proxy (meta tags + JSON/CDN)."""
    prioritized: list[str] = []
    prioritized.extend(_all_meta_values(page, "og:image"))
    prioritized.extend(_all_meta_values(page, "twitter:image"))
    for match in _IMAGE_JSON_RE.finditer(page):
        prioritized.append(match.group(1))

    def collect(raws: list[str]) -> list[str]:
        found: list[str] = []
        for raw in raws:
            url = _clean_media_url(raw, base_url)
            if not _looks_like_image(url) or url in found:
                continue
            found.append(url)
            if len(found) >= max_images:
                break
        return found

    urls = collect(prioritized)
    if urls:
        return urls

    # Só varre todas as URLs da página se não houver og:image/JSON confiável;
    # assim evita pegar favicon, avatar ou logo do proxy.
    return collect([match.group(0) for match in _IMAGE_URL_RE.finditer(page)])


def resolve_images(url: str, max_images: int = 10) -> list[str]:
    """Devolve URLs de imagem para TikTok, Instagram e Reddit.

    Se o post tiver vídeo (og:video), devolve lista vazia para que o fluxo
    normal de vídeo continue responsável por ele.
    """
    if not is_image_network(url):
        return []

    host = _host_of(url)

    # Link direto de imagem (ex.: https://i.redd.it/abc123.jpg).
    if _looks_like_image(url) and (
        host.endswith(".redd.it")
        or any(host == cdn or host.endswith("." + cdn) for cdn in REDDIT_IMAGE_HOSTS)
        or host.endswith((".cdninstagram.com", ".tiktokcdn.com", ".hiphina.com"))
    ):
        return [url]

    proxy_url = to_proxy_url(url)
    page, final_url = _get_proxy_page(proxy_url)
    if _og_video(page, final_url):
        return []

    urls = _extract_image_urls(page, final_url, max_images)

    # No Reddit, aceita apenas as CDNs oficiais de mídia: a página do proxy
    # tem logos (redditstatic.com) que nunca devem ser enviadas.
    if any(host == h or host.endswith("." + h) for h in ("reddit.com", "redd.it", "vxreddit.com")):
        filtered: list[str] = []
        for u in urls:
            uhost = (urlparse(u).hostname or "").lower()
            if any(uhost == cdn or uhost.endswith("." + cdn) for cdn in REDDIT_IMAGE_HOSTS):
                filtered.append(u)
        urls = filtered
    return urls


def _image_ext_from_bytes(data: bytes) -> str | None:
    """Descobre a extensão pelos bytes reais (não confia no Content-Type)."""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:16]
        return ".avif" if b"avif" in brand else ".heic"
    return None


def download_images(url: str, job_dir: str, max_images: int = 10) -> list[Path]:
    """Baixa as imagens do post para job_dir e devolve os caminhos.

    Só são aceitos arquivos cujos bytes sejam realmente uma imagem conhecida;
    HTML/JSON de páginas são descartados para não enviar imagem corrompida.
    """
    image_urls = resolve_images(url, max_images)
    if not image_urls:
        raise NoImageAvailable("nenhuma imagem encontrada no post")

    folder = Path(job_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    seen: set[str] = set()

    for index, image_url in enumerate(image_urls, 1):
        try:
            response = requests.get(
                image_url,
                stream=True,
                timeout=(20, 60),
                headers={"User-Agent": USER_AGENT, "Referer": image_url},
            )
            response.raise_for_status()
            data = b"".join(
                chunk for chunk in response.iter_content(chunk_size=1 << 20) if chunk
            )
        except requests.RequestException as exc:
            print(f"[imagem] falha no download {index}: {exc}")
            continue

        ext = _image_ext_from_bytes(data)
        if ext is None or len(data) < 1024:
            print(
                f"[imagem] descartado {index}: não é imagem "
                f"({response.headers.get('Content-Type', '?')}, {len(data)} bytes)"
            )
            continue

        fingerprint = hashlib.md5(data).hexdigest()
        if fingerprint in seen:
            print(f"[imagem] duplicata {index} descartada")
            continue
        seen.add(fingerprint)

        destination = folder / f"image_{index:02d}{ext}"
        destination.write_bytes(data)
        paths.append(destination)
        print(f"[imagem] {index}: {destination.name} ({len(data)} bytes)")

    if not paths:
        raise NoImageAvailable("nenhuma imagem válida baixada")
    return paths


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
        page, final_url = _get_proxy_page(proxy_url)
        direct = _og_video(page, final_url)
        if direct:
            return Media(direct, _og_title(page), "tnktok")
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

    try:
        page, final_url = _get_proxy_page(proxy_url)
    except requests.RequestException as exc:
        raise NoVideoAvailable(f"falha ao acessar {host}: {exc}") from exc
    direct = _og_video(page, final_url)
    if not direct:
        raise NoVideoAvailable(f"og:video não encontrado em {host}")
    return Media(direct, _og_title(page), host)


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
