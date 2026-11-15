#!/bin/sh
# babyseat add-on: the add-on's options -> the seat's launch line (#1143). Usage: options.sh <options.json>   (the Supervisor writes /data/options.json before every start)
# It PRINTS shell for run.sh to eval, and nothing else on stdout:
#   set -- /opt/babyseat/bin/cliffvm serve --port 4096 --bind 0.0.0.0 --data-dir /data [--fact name=<JSON> ...]
#   NAME=$(jq -r '.<option>' '<options.json>'); export NAME      (a secret: the line names the option, the value is read when run.sh evals it, so it is never in this output, a log or argv)
# The options (config.yaml `schema`; DOCS.md says what each does):
#   seat_name             -> --fact seat_name="<name>"  (empty: not told, so the cutover may carry the 0.7 seat's name over)
#   provider claude       -> CLAUDE_BIN=<claude_path> (an executable the user supplies: the image has no claude CLI), ANTHROPIC_API_KEY=<anthropic_api_key>; the seat's own defaults (launcher.c1f)
#   provider openai       -> --fact cloud_models=[{name: <model>, backend: openai, base_url: <base_url>, api_key_env: OPENAI_API_KEY}] + --fact minds={... <model> ...}; OPENAI_API_KEY=<api_key>
#   provider ollama       -> --fact seat_backend=<ollama_url> + --fact minds={... <model> ...}; no key
# A bad value is refused on stderr ("babyseat add-on: ..."), exit 2, nothing on stdout. Needs jq (in every Home Assistant base image).
set -eu

die() { printf 'babyseat add-on: %s\n' "$*" >&2; exit 2; }
[ $# -eq 1 ] || die "usage: options.sh <options.json>"
opts=$1
command -v jq >/dev/null 2>&1 || die "jq is needed to read the options"
[ -r "$opts" ] || die "cannot read the options file $opts"
jq -e 'type == "object"' "$opts" >/dev/null 2>&1 || die "the options file $opts is not a JSON object"

# get <option>: its text ('' when absent or null). NEVER used for a secret: those go through has_value/secret_line only.
get() { jq -r --arg k "$1" '.[$k] // "" | if type == "string" then . else tostring end' "$opts"; }
has_value() { jq -e --arg k "$1" '(.[$k] // "") | tostring | length > 0' "$opts" >/dev/null 2>&1; }
# q <text>: one shell word, single-quoted
q() { jq -nr --arg v "$1" '$v | @sh'; }
# fact <name> <JSON>: one --fact argument, quoted
fact() { printf ' --fact %s' "$(q "$1=$2")"; }
# secret_line <VAR> <option>: the export line that reads the option at eval time
secret_line() { printf '%s=$(jq -r %s %s); export %s\n' "$1" "$(q ".$2")" "$(q "$opts")" "$1"; }
is_url() { case $1 in http://?*|https://?*) return 0 ;; *) return 1 ;; esac; }
minds() { jq -cn --arg m "$1" '{loops: $m, cortex: $m, workers: $m, seat_default: $m, loops_auth: "chat", cortex_auth: "chat", workers_auth: "chat", think: "auto"}'; }

provider=$(get provider)
[ -n "$provider" ] || provider=claude
model=$(get model)
args=""
exports=""

name=$(get seat_name)
[ -z "$name" ] || args="$args$(fact seat_name "$(jq -cn --arg v "$name" '$v')")"

case $provider in
  claude)
    path=$(get claude_path)
    if [ -n "$path" ]; then
      case $path in /*) ;; *) die "claude_path must be an absolute path inside the add-on (e.g. /config/claude/claude), got: $path" ;; esac
      [ -f "$path" ] && [ -x "$path" ] || die "claude_path $path is not an executable file in the add-on (the Home Assistant config folder is mounted read-only at /config)"
      exports="${exports}CLAUDE_BIN=$(q "$path"); export CLAUDE_BIN
"
    else
      printf '%s\n' "babyseat add-on: provider claude without claude_path: the seat starts, and the chat answers with how to supply the claude CLI (or pick provider openai or ollama)" >&2
    fi
    ! has_value anthropic_api_key || exports="$exports$(secret_line ANTHROPIC_API_KEY anthropic_api_key)
"
    ;;
  openai)
    base=$(get base_url)
    [ -n "$base" ] || die "provider openai needs base_url (the endpoint's root, e.g. https://api.openai.com)"
    is_url "$base" || die "base_url must start with http:// or https://, got: $base"
    [ -n "$model" ] || die "provider openai needs model (the model id the endpoint is asked for)"
    if has_value api_key; then
      entry=$(jq -cn --arg n "$model" --arg b "$base" '[{name: $n, backend: "openai", base_url: $b, api_key_env: "OPENAI_API_KEY"}]')
      exports="$exports$(secret_line OPENAI_API_KEY api_key)
"
    else
      entry=$(jq -cn --arg n "$model" --arg b "$base" '[{name: $n, backend: "openai", base_url: $b}]')
    fi
    args="$args$(fact cloud_models "$entry")$(fact minds "$(minds "$model")")"
    ;;
  ollama)
    url=$(get ollama_url)
    [ -n "$url" ] || die "provider ollama needs ollama_url (e.g. http://192.168.1.10:11434)"
    is_url "$url" || die "ollama_url must start with http:// or https://, got: $url"
    [ -n "$model" ] || die "provider ollama needs model (a model the Ollama server has pulled, e.g. llama3.2)"
    args="$args$(fact seat_backend "$(jq -cn --arg v "$url" '$v')")$(fact minds "$(minds "$model")")"
    ;;
  *) die "provider must be claude, openai or ollama, got: $provider" ;;
esac

printf 'set -- /opt/babyseat/bin/cliffvm serve --port 4096 --bind 0.0.0.0 --data-dir /data%s\n' "$args"
printf '%s' "$exports"
