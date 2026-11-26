# Changelog

## 2026-11-26

### Corrigido: bot demorava 30-60 min para enviar os vídeos

Vários fatores somavam atraso na fila. Os links chegavam às 09:32-09:40 e as
respostas saíam só entre 10:05 e 10:43 (33-63 min).

1. **Fila sequencial** — `MAX_CONCURRENT_DOWNLOADS = 1` fazia um link travado
   bloquear todos os seguintes.
   - **Fix:** 2 workers por event loop e timeout total de 300 s por job.

2. **Timeouts enormes e threads órfãs** — download 300 s, ffmpeg 600 s por
   tentativa (até 4 tentativas). `wait_for()` com `to_thread()`/`communicate()`
   não cancelava o trabalho e o ffmpeg continuava competindo por CPU.
   - **Fix:** timeouts curtos (download/yt-dlp 120 s, ffmpeg 240 s, upload 60 s,
     sondagem de imagem 20 s) e `proc.kill()` no timeout do ffmpeg.

3. **Sondagem de imagem antes do vídeo** — posts com `/video/`, `/reel/` etc.
   perdiam 30-170 s tentando extrair imagens antes de baixar o MP4.
   - **Fix:** `looks_like_video()` pula a sondagem nesses links; dedupe de URLs
     repetidas de carrossel antes de baixar.

4. **DNS IPv6 travando no Android/Termux** — o `asyncio` resolvia AAAA primeiro
   e o handshake WebSocket nunca completava; o `requests` também travava na
   resolução (o timeout dele não cobre DNS).
   - **Fix:** `socket.getaddrinfo` e `loop.getaddrinfo` forçados para IPv4,
     `open_timeout` do websockets elevado para 60 s e reconexão com backoff de
     15 s + watchdog de 90 s.

5. **Logs sem horário** — impossível medir cada etapa.
   - **Fix:** helper `log()` com timestamp e duração de proxy, yt-dlp, ffmpeg e
     do job inteiro. Imagens baixadas direto não passam mais pela compressão.

### Arquivos alterados

- `bot.py` — 2 workers, timeouts, watchdog/reconexão, IPv4, logs, skip de
  sondagem em links de vídeo.
- `config.py` — novos timeouts e concorrência.
- `embed_download.py` — IPv4 global, timeouts HTTP, dedupe de imagens.

### Verificação

- `python3 -m py_compile bot.py config.py embed_download.py` — OK.
- Download do mesmo TikTok no Termux: 24-59 s (às vezes >4 min) → **1,1 s**.
- Bot reconectado: `Logado como KFixEmbed`, `2 worker(s) iniciado(s)`.

## 2026-11-21

### Corrigido: bot parava de processar links após um crash

Dois bugs combinados faziam os links entrarem na fila ("Na fila: ...") mas
nunca serem baixados/enviados:

1. **Crash ao deserializar mensagens com embed** — a versão instalada da
   biblioteca `nerimity` tem `self.style` solto (sem atribuição) em
   `Embed.__init__`, o que estoura `AttributeError` e derruba o processo
   inteiro quando qualquer mensagem com `embed`/`htmlEmbed` chega.
   - **Fix:** monkeypatch de `Embed.__init__` no início de `bot.py`,
     preservando `type`, `style` e `body`. Compatível com futuros
     `pip install -U nerimity`.

2. **Workers e fila presos ao event loop morto** — com `restart_always=True`,
   a lib chama `asyncio.run(main())` de novo a cada crash, criando um novo
   event loop. A flag `_workers_started` continuava `True` e a `asyncio.Queue`
   antiga ficava presa ao loop anterior (`get()` estoura
   `RuntimeError: bound to a different event loop`), então nenhum worker
   voltava a consumir a fila.
   - **Fix:** `on_ready` agora detecta a troca de loop, recria a fila e
     reinicia os workers.

### Arquivos alterados

- `bot.py` — monkeypatch do `Embed` + recriação da fila/workers por event loop.

### Verificação

- `python3 -m py_compile bot.py` — OK.
- `Message.deserialize()` com `htmlEmbed` — OK (antes crashava).
- Bot reiniciado: `1 worker(s) iniciado(s).` e nenhum erro novo no log.
- 5 links do TikTok que estavam presos foram reprocessados e enviados com
  sucesso aos canais.
