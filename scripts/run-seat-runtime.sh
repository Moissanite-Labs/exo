#!/bin/bash
# Generic manual TUI launcher. The caller supplies runtime, seat and environment.
# Does not stop or replace a core. Only run after arranging the core handoff.
set -e
: "${EXO_RUNTIME_EXO:?Set EXO_RUNTIME_EXO to the selected runtime executable}"
: "${EXO_SEAT:?Set EXO_SEAT to the selected seat}"
[ -x "$EXO_RUNTIME_EXO" ] || { echo 'Selected runtime is not executable.' >&2; exit 72; }
launcher=${EXO_RESTART_LAUNCHER:-$0}
if [ "${1:-}" = restart ]; then
  printf -v quoted_launcher '%q' "$launcher"
  printf -v quoted_dir '%q' "$PWD"
  export EXO_RESTART_CMD="$quoted_launcher $quoted_dir"
  cmd="$EXO_RESTART_CMD --resume"
  if [ "$(uname)" = Darwin ]; then
    if [ "${TERM_PROGRAM:-}" = iTerm.app ]; then
      osascript -e 'on run argv' -e 'tell application "iTerm2" to create window with default profile command (item 1 of argv)' -e 'end run' "$cmd"
    else
      osascript -e 'on run argv' -e 'tell application "Terminal" to do script (item 1 of argv)' -e 'end run' "$cmd"
    fi
  else
    printf 'Open a new terminal and run: %s\n' "$cmd"
  fi
  exit 0
fi
case "${1:-}" in
  "") dir="$HOME"; rest=() ;;
  -*) dir="$PWD"; rest=("$@") ;;
  resume)
    shift
    case "${1:-}" in ""|-*) dir="$PWD"; rest=("$@") ;; *) dir="$1"; rest=("${@:2}") ;; esac
    rest=(--resume "${rest[@]}") ;;
  *) dir="$1"; rest=("${@:2}") ;;
esac
printf -v quoted_launcher '%q' "$launcher"
printf -v quoted_dir '%q' "$dir"
export EXO_RESTART_CMD="$quoted_launcher $quoted_dir"
cd "$dir"
exec "$EXO_RUNTIME_EXO" tui --seat "$EXO_SEAT" "${rest[@]}"
