#!/data/data/com.termux/files/usr/bin/bash
BOT_DIR="$HOME/video-bot"
SESSION="videobot"
LOG="$BOT_DIR/bot.log"

cd "$BOT_DIR" || exit 1

# Evita que o Android durma e corte a conexao/rede
termux-wake-lock 2>/dev/null || true

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Bot ja esta rodando na sessao tmux '$SESSION'."
    tmux list-sessions
    exit 0
fi

CMD="while true; do echo \"[\$(date '+%F %T')] Iniciando bot...\"; python3 -u bot.py; echo \"[\$(date '+%F %T')] Bot encerrado. Reiniciando em 10s...\"; sleep 10; done 2>&1 | tee -a $LOG"

tmux new-session -d -s "$SESSION" -c "$BOT_DIR" "$CMD"

echo "Bot iniciado na sessao tmux '$SESSION'."
echo "Logs: tail -f $LOG"
echo "Ver ao vivo: tmux attach -t $SESSION"
