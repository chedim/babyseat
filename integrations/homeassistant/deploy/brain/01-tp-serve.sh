#!/data/data/com.termux/files/usr/bin/sh
# 01-tp-serve.sh — ThinkPhone sticky boot service (Termux:Boot):
# wake-lock, sshd, then the membrane supervisor (keeps `membrane serve`
# on :4096 alive; chat+embed backend = the mac's ollama over LAN)
termux-wake-lock
sleep 12
pgrep -x sshd >/dev/null || sshd
nohup sh ~/membrane/membrane-supervise.sh >/dev/null 2>&1 &
