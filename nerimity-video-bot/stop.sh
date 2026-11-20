#!/data/data/com.termux/files/usr/bin/bash
SESSION="videobot"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
    termux-wake-unlock 2>/dev/null || true
    echo "Bot parado."
else
    echo "Bot nao estava rodando."
fi
