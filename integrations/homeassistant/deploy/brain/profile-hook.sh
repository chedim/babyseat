# >>> membrane brain (ThinkPhone) >>>
# any Termux session (re)arms the brain: agdroid relaunches Termux over adb
# when the serve is down; the supervisor's lock makes this idempotent
pgrep -x sshd >/dev/null 2>&1 || sshd
[ -f ~/membrane/membrane-supervise.sh ] && \
  setsid nohup sh ~/membrane/membrane-supervise.sh >/dev/null 2>&1 </dev/null &
# <<< membrane brain <<<
