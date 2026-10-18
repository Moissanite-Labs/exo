#!/usr/bin/env bash
# install.sh: install Exo for any user on macOS or Linux.
#
#   one venv + exo (with the terminal client) + `exo` on PATH + the Exo plugins and skill pack + an `exo models` check
#   Hermes is NOT installed by default. Exo runs on its own core. `--with-hermes` adds the
#   pinned Hermes Agent and its patch queue (kept until the wave 4 cutover).
#
# Usage: scripts/install.sh [options]
#
#   --dry-run            print every action, change nothing
#   --exo-home DIR       Exo's home (default $EXO_HOME, else ~/.exo)
#   --hermes-home DIR    Also copy plugins to this HERMES_HOME. Default with --with-hermes: $EXO_HOME/hermes-home.
#                        An existing ~/.hermes is used only when passed here explicitly.
#   --with-hermes        also pin Hermes (scripts/pin-hermes.sh), install it in the venv, write the
#                        exo-hermes launcher. Prints a warning summary of every patch that did not
#                        apply and exits non-zero if any failed.
#   --tag TAG            Hermes release tag (default: latest stable). Needs --with-hermes.
#   --exo-source SRC     a local exo checkout or a git URL (default: the repo holding this script)
#   --python PY          interpreter for the venv (default: newest python3.11+ on PATH)
#   --plugins LIST       comma list of plugins to install (default and `all`: every plugins/*/plugin.yaml). Remembered
#                        for re-runs and `exo update`; removes the other Exo-installed plugins from $EXO_HOME/plugins.
#   --principal ID       principal id for the interview
#   --answers FILE       answers YAML for a non-interactive interview
#   --no-pin             with --with-hermes: do not pin (use an existing checkout, or skip Hermes)
#   --no-pip             do not create the venv or pip install anything
#   --no-interview       do not offer the interview
#   --strict-patches     with --with-hermes: fail at once when a patch does not apply
#   --editable           pip install exo in editable mode (for development)
#   --bin-dir DIR        where the `exo` command is linked (default $EXO_BIN_DIR, else ~/.local/bin)
#   --modify-path        append the PATH line to your shell rc file when BIN_DIR is not on PATH
#                        (default: print the line and leave your rc files alone)
#   -y, --yes            do not prompt
#
# Environment: EXO_HOME, EXO_HERMES_DIR, EXO_HERMES_HOME, EXO_PYTHON, EXO_PATCH_SKIP, and the
# pin-hermes.sh variables. HERMES_HOME is ignored on purpose: it usually points at a live install.
#
# After it: type `exo`. Re-run it (or `exo update`) to upgrade. Keys are never read or written here.
#
# Idempotent: a second run with the same inputs only re-checks. Never touches ~/.hermes unless
# --hermes-home names it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

MODIFY_PATH=0 BIN_DIR_ARG="" PLUGINS_EXPLICIT=0
DRY=0 WITH_HERMES=0 PIN=1 PIP=1 INTERVIEW=1 STRICT=0 EDITABLE=0 YES=0
EXO_HOME_ARG="" HERMES_HOME_ARG="" HERMES_HOME_EXPLICIT=0
TAG="${EXO_HERMES_TAG:-}" EXO_SOURCE="" PY="${EXO_PYTHON:-}" PLUGINS="" PRINCIPAL="" ANSWERS=""

die() { echo "install: $*" >&2; exit 1; }
say() { echo "install: $*"; }
warn() { echo "install: WARNING: $*" >&2; }

need_arg() { [ $# -ge 2 ] && [ -n "$2" ] || die "$1 needs a value"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --exo-home) need_arg "$@"; EXO_HOME_ARG="$2"; shift ;;
    --hermes-home) need_arg "$@"; HERMES_HOME_ARG="$2"; HERMES_HOME_EXPLICIT=1; shift ;;
    --with-hermes) WITH_HERMES=1 ;;
    --tag) need_arg "$@"; TAG="$2"; shift ;;
    --exo-source) need_arg "$@"; EXO_SOURCE="$2"; shift ;;
    --python) need_arg "$@"; PY="$2"; shift ;;
    --plugins) need_arg "$@"; PLUGINS="$2"; PLUGINS_EXPLICIT=1; shift ;;
    --principal) need_arg "$@"; PRINCIPAL="$2"; shift ;;
    --answers) need_arg "$@"; ANSWERS="$2"; shift ;;
    --no-pin) PIN=0 ;;
    --no-pip) PIP=0 ;;
    --no-interview) INTERVIEW=0 ;;
    --strict-patches) STRICT=1 ;;
    --editable) EDITABLE=1 ;;
    --bin-dir) need_arg "$@"; BIN_DIR_ARG="$2"; shift ;;
    --modify-path) MODIFY_PATH=1 ;;
    -y|--yes) YES=1 ;;
    -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done

FAILED_PATCHES=()
[ "$WITH_HERMES" -eq 1 ] || PIN=0
if [ "$WITH_HERMES" -eq 0 ] && { [ -n "$TAG" ] || [ "$STRICT" -eq 1 ]; }; then
  die "--tag and --strict-patches only apply with --with-hermes"
fi

# --- helpers ------------------------------------------------------------------
q() { printf '%q ' "$@"; }
run() {  # print and (unless dry run) execute
  echo "+ $(q "$@")"
  [ "$DRY" -eq 1 ] || "$@"
}
expand() { case "$1" in "~") echo "$HOME" ;; "~/"*) echo "$HOME/${1#\~/}" ;; *) echo "$1" ;; esac; }
# Absolute path without creating anything: resolve the deepest existing parent.
abspath() {
  local p rest=""
  p="$(expand "$1")"
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  while [ ! -d "$p" ]; do
    rest="/$(basename "$p")$rest"
    p="$(dirname "$p")"
  done
  echo "$(cd "$p" && pwd -P)$rest"
}
write_if_changed() {  # PATH CONTENT: leave the file (and its mtime) alone when nothing changed
  if [ -f "$1" ] && [ "$(cat "$1")" = "$2" ]; then return 0; fi
  printf '%s\n' "$2" > "$1"
}
inside() {  # inside CHILD PARENT
  case "$1/" in "$2"/*) return 0 ;; esac
  return 1
}

# --- 1. platform, python, git ------------------------------------------------
case "$(uname -s)" in
  Darwin|Linux) ;;
  *) die "unsupported OS $(uname -s): Exo installs on macOS and Linux" ;;
esac
command -v git >/dev/null || die "git not found; install git first"

py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; }
if [ -n "$PY" ]; then
  command -v "$PY" >/dev/null || die "python not found: $PY"
  py_ok "$PY" || die "$PY is older than Python 3.11"
else
  for cand in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null && py_ok "$cand"; then PY="$cand"; break; fi
  done
  [ -n "$PY" ] || die "Python 3.11 or newer not found; install it or pass --python"
fi
PY="$(command -v "$PY")"
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info < (3, 15) else 1)' \
  || warn "Python $PYVER is newer than Hermes supports (<3.15); pass --python python3.14"

# --- 2. locations ----------------------------------------------------------
EXO_HOME="$(abspath "${EXO_HOME_ARG:-${EXO_HOME:-$HOME/.exo}}")"
EXO_HERMES_DIR="$(abspath "${EXO_HERMES_DIR:-$EXO_HOME/hermes}")"
if [ "$HERMES_HOME_EXPLICIT" -eq 1 ]; then
  TARGET_HOME="$(abspath "$HERMES_HOME_ARG")"
else
  TARGET_HOME="$(abspath "${EXO_HERMES_HOME:-$EXO_HOME/hermes-home}")"
fi
VENV="$(abspath "${EXO_VENV:-$EXO_HOME/venv}")"
BIN_DIR="$(abspath "${BIN_DIR_ARG:-${EXO_BIN_DIR:-$HOME/.local/bin}}")"
LIVE_HERMES="$(abspath "$HOME/.hermes")"

if [ "$HERMES_HOME_EXPLICIT" -eq 0 ] && { inside "$TARGET_HOME" "$LIVE_HERMES" || inside "$LIVE_HERMES" "$TARGET_HOME"; }; then
  die "refusing: $TARGET_HOME overlaps the live Hermes home $LIVE_HERMES. Pass --hermes-home explicitly to use it."
fi
for p in "$EXO_HOME" "$EXO_HERMES_DIR" "$VENV"; do
  inside "$p" "$LIVE_HERMES" && die "refusing: $p is inside the live Hermes home $LIVE_HERMES"
done

# Exo source: a local checkout, or a git URL cloned into $EXO_HOME/src/exo.
if [ -z "$EXO_SOURCE" ]; then EXO_SOURCE="$REPO_DIR"; fi
case "$EXO_SOURCE" in
  http://*|https://*|git@*|ssh://*|git+*)
    EXO_URL="${EXO_SOURCE#git+}"
    EXO_SRC="$EXO_HOME/src/exo" ;;
  *)
    EXO_URL=""
    EXO_SRC="$(abspath "$EXO_SOURCE")"
    [ -f "$EXO_SRC/pyproject.toml" ] && grep -q '^name = "exo"' "$EXO_SRC/pyproject.toml" \
      || die "$EXO_SRC is not an exo checkout (no pyproject.toml naming exo)" ;;
esac

say "plan$([ "$DRY" -eq 1 ] && echo ' (dry run: nothing will change)')"
cat <<EOF
  python        $PY ($PYVER)
  EXO_HOME      $EXO_HOME
  exo source    ${EXO_URL:-$EXO_SRC}
  hermes        $([ "$WITH_HERMES" -eq 1 ] && echo "$EXO_HERMES_DIR  (pin: $([ "$PIN" -eq 1 ] && echo "${TAG:-latest stable tag}" || echo skipped))" || echo "not installed (pass --with-hermes to add it)")
  venv          $VENV  ($([ "$PIP" -eq 1 ] && echo "$([ "$WITH_HERMES" -eq 1 ] && echo 'Hermes + ')exo" || echo skipped))
  command       $BIN_DIR/exo  (link to $VENV/bin/exo)
  core plugins  $EXO_HOME/plugins
  skill pack    $EXO_HOME/skills
  Hermes home   $TARGET_HOME$([ "$HERMES_HOME_EXPLICIT" -eq 1 ] && echo '  (explicit)' || echo '  (optional, Exo-owned)')
EOF

# --- 3. EXO_HOME ------------------------------------------------------------
if [ ! -d "$EXO_HOME" ]; then run mkdir -p "$EXO_HOME"; fi
run chmod 700 "$EXO_HOME"
for d in keys principals bin; do
  [ -d "$EXO_HOME/$d" ] || run mkdir -p "$EXO_HOME/$d"
done

if [ -n "$EXO_URL" ]; then
  if [ -d "$EXO_SRC/.git" ]; then
    run git -C "$EXO_SRC" pull -q --ff-only
  else
    run git clone -q --depth 1 "$EXO_URL" "$EXO_SRC"
  fi
fi

# --- 4. pin Hermes ------------------------------------------------------------
PIN_SKIP="${EXO_PATCH_SKIP:-}"
pin_hermes() {
  local log tries=0 failed
  log="$(mktemp "${TMPDIR:-/tmp}/exo-pin.XXXXXX")"
  while :; do
    tries=$((tries + 1))
    echo "+ EXO_HOME=$EXO_HOME EXO_HERMES_DIR=$EXO_HERMES_DIR EXO_PATCH_SKIP=$PIN_SKIP $EXO_SRC/scripts/pin-hermes.sh $TAG"
    if EXO_HOME="$EXO_HOME" EXO_HERMES_DIR="$EXO_HERMES_DIR" EXO_PATCH_DIR="$EXO_SRC/patches" \
       EXO_PATCH_SKIP="$PIN_SKIP" bash "$EXO_SRC/scripts/pin-hermes.sh" ${TAG:+"$TAG"} 2>"$log"; then
      cat "$log" >&2; rm -f "$log"; return 0
    fi
    cat "$log" >&2
    failed="$(sed -n 's/^pin-hermes: patch FAILED: //p' "$log" | head -n1)"
    if [ -z "$failed" ] || [ "$STRICT" -eq 1 ] || [ "$tries" -gt 5 ]; then
      rm -f "$log"; die "pin-hermes.sh failed$([ -n "$failed" ] && echo " on patch $failed (--strict-patches)")"
    fi
    warn "patch $failed does not apply to this tag; skipping it. PINNED records the skip."
    case ",$PIN_SKIP," in *",$failed,"*) rm -f "$log"; die "patch $failed still fails after skipping it" ;; esac
    FAILED_PATCHES+=("$failed")
    PIN_SKIP="${PIN_SKIP:+$PIN_SKIP,}$failed"   # full file name: pin-hermes.sh matches it exactly
  done
}
if [ "$PIN" -eq 1 ]; then
  if [ "$DRY" -eq 1 ]; then
    echo "+ EXO_HOME=$EXO_HOME EXO_HERMES_DIR=$EXO_HERMES_DIR EXO_PATCH_SKIP=$PIN_SKIP $EXO_SRC/scripts/pin-hermes.sh $TAG"
    echo "  (a patch that does not apply is skipped and recorded in PINNED$([ "$STRICT" -eq 1 ] && echo '; --strict-patches: fail instead'))"
  else
    pin_hermes
  fi
fi
HAVE_HERMES=0
[ "$WITH_HERMES" -eq 1 ] && [ -f "$EXO_HERMES_DIR/pyproject.toml" ] && HAVE_HERMES=1
[ "$DRY" -eq 1 ] && [ "$PIN" -eq 1 ] && HAVE_HERMES=1 || true

# --- 5. venv: Hermes + exo ------------------------------------------------------
# Upstream's own setup (setup-hermes.sh / install.sh) is a pm/uv-managed environment that
# installs into ~/.hermes. Exo needs a separate home, so it builds a plain venv and installs
# the pinned checkout with pip. Hermes exact-pins its dependencies, so the result matches.
VPY="$VENV/bin/python"
if [ "$PIP" -eq 1 ]; then
  if [ ! -x "$VPY" ]; then
    run "$PY" -m venv "$VENV"
  else
    say "venv exists: $VENV"
  fi
  STAMP="$VENV/.exo-install-stamp"
  want="hermes=$(sed -n 's/^patched_tree: //p' "$EXO_HERMES_DIR/PINNED" 2>/dev/null || echo none);"
  want="${want}exo=$(git -C "$EXO_SRC" rev-parse HEAD 2>/dev/null || echo local)-$EDITABLE-tui"
  if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$want" ] && [ "$EDITABLE" -eq 0 ] && [ -z "$(git -C "$EXO_SRC" status --porcelain 2>/dev/null || echo dirty)" ]; then
    say "Hermes and exo already installed at these versions"
  else
    run "$VPY" -m pip install -q --upgrade pip
    if [ "$HAVE_HERMES" -eq 1 ]; then
      run "$VPY" -m pip install -q -e "$EXO_HERMES_DIR"
    elif [ "$WITH_HERMES" -eq 1 ]; then
      warn "no Hermes checkout at $EXO_HERMES_DIR; installing exo only"
    fi
    if [ "$EDITABLE" -eq 1 ]; then
      run "$VPY" -m pip install -q -e "$EXO_SRC[tui]"
    else
      run "$VPY" -m pip install -q "$EXO_SRC[tui]"
    fi
    if [ "$DRY" -eq 0 ]; then echo "$want" > "$STAMP"; else echo "+ write $STAMP"; fi
  fi
fi

# --- 6. standalone plugins, plus Hermes plugins when requested --------------------
# config.json remembers a --plugins list (plugin_selection) and the Hermes home the plugins went to (hermes_home).
# A re-run without those options, such as the one `exo update` makes, refreshes the same plugins in the same places.
CFG_SEL="" CFG_HH=""
if [ -f "$EXO_HOME/config.json" ]; then
  { IFS= read -r CFG_SEL && IFS= read -r CFG_HH; } < <("$PY" -I -c 'import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8")); s, h = d.get("plugin_selection"), d.get("hermes_home")
print(",".join(map(str, s)) if isinstance(s, list) else "")
print(h if isinstance(h, str) else "")' "$EXO_HOME/config.json" 2>/dev/null) || true
fi
if [ "$PLUGINS_EXPLICIT" -eq 0 ] && [ -n "$CFG_SEL" ]; then
  PLUGINS="$CFG_SEL"
  say "plugins: keeping the --plugins selection in config.json ($CFG_SEL); pass --plugins all for every plugin"
fi
[ "$PLUGINS" != all ] || PLUGINS=""
SELECTED=()
for d in "$EXO_SRC"/plugins/*/; do
  [ -f "$d/plugin.yaml" ] || continue
  name="$(basename "$d")"
  if [ -n "$PLUGINS" ]; then
    case ",$PLUGINS," in *",$name,"*) ;; *) continue ;; esac
  fi
  SELECTED+=("$name")
done
[ "${#SELECTED[@]}" -gt 0 ] || die "no plugins selected from $EXO_SRC/plugins${PLUGINS:+ (asked for: $PLUGINS; see --plugins)}"

# The standalone core discovers EXO_HOME/plugins without launcher environment setup.
PDIRS=("$EXO_HOME/plugins") HPLUG="" HERMES_ASKED=0
if [ "$WITH_HERMES" -eq 1 ] || [ "$HERMES_HOME_EXPLICIT" -eq 1 ] || [ -n "${EXO_HERMES_HOME:-}" ]; then
  HERMES_ASKED=1 HPLUG="$TARGET_HOME/plugins"
elif [ -n "$CFG_HH" ] && [ -d "$CFG_HH/plugins" ]; then  # copies an earlier run made; ~/.hermes still needs --hermes-home
  CFG_HH="$(abspath "$CFG_HH")"
  if inside "$CFG_HH" "$LIVE_HERMES" || inside "$LIVE_HERMES" "$CFG_HH"; then
    warn "plugins in $CFG_HH/plugins not refreshed: it overlaps $LIVE_HERMES. Re-run with --hermes-home $CFG_HH."
  elif [ "$CFG_HH/plugins" != "$EXO_HOME/plugins" ]; then
    HPLUG="$CFG_HH/plugins"
    say "plugins: also refreshing the Hermes copies in $HPLUG (config.json hermes_home)"
  fi
fi
[ -z "$HPLUG" ] || [ "$HPLUG" = "$EXO_HOME/plugins" ] || PDIRS+=("$HPLUG")
# The core loads every folder in $EXO_HOME/plugins, so a selection removes the ones an earlier run installed (marked
# .exo-managed) that it leaves out. Folders you added and links stay. A Hermes home is left as is: config.yaml decides.
if [ -n "$PLUGINS" ]; then
  for d in "$EXO_HOME"/plugins/*/; do
    case " ${SELECTED[*]} " in *" $(basename "$d") "*) continue ;; esac
    if [ ! -L "${d%/}" ] && [ -f "$d.exo-managed" ]; then run rm -rf "${d%/}"; fi
  done
fi
MARK=".exo-managed"
foreign() { [ -e "$1" ] && [ ! -f "$1/$MARK" ]; }  # something is at $1 and Exo did not install it
# Exo installed $1 under this name: its marker names it. cp -r copies the hidden marker too, so a copy or a renamed
# folder still names the folder it came from, and it is yours.
managed_as() { [ -f "$1/$MARK" ] && grep -qxF "name: $(basename "$1")" "$1/$MARK"; }
mark_managed() {  # mark_managed DST SRC
  if [ "$DRY" -eq 1 ]; then echo "+ write $1/$MARK"; else
    printf 'installed by exo scripts/install.sh from %s\nname: %s\n' "$2" "$(basename "$1")" > "$1/$MARK"
  fi
}
# copy_managed SRC DST LABEL [keep-tests]: copy a folder Exo ships to DST without caches (and without tests/ unless
# keep-tests), then mark DST as Exo's. Says LABEL is up to date when nothing changed. Check foreign() first.
copy_managed() {
  local src="$1" dst="$2" label="$3" skip=tests
  [ "${4:-}" = keep-tests ] && skip=.pytest_cache
  if [ -f "$dst/$MARK" ] && diff -rq -x "$MARK" -x "$skip" -x __pycache__ -x .pytest_cache "$src" "$dst" >/dev/null 2>&1; then
    managed_as "$dst" || mark_managed "$dst" "$src"  # an adopted copy, or a marker from before markers named it
    [ -z "$label" ] || say "$label is up to date"
    return 0
  fi
  [ -e "$dst" ] && run rm -rf "$dst"
  run mkdir -p "$dst"
  if [ "$DRY" -eq 1 ]; then
    echo "+ copy $src -> $dst (without $([ "$skip" = tests ] && echo 'tests, ')caches)"
  else
    (cd "$src" && find . \( -name "$skip" -o -name __pycache__ -o -name .pytest_cache \) -prune -o -type f -print) |
      while IFS= read -r f; do
        mkdir -p "$dst/$(dirname "$f")"
        cp -p "$src/$f" "$dst/$f"
      done
  fi
  mark_managed "$dst" "$src"
}
for PDIR in "${PDIRS[@]}"; do
  [ -d "$PDIR" ] || run mkdir -p "$PDIR"
  for name in "${SELECTED[@]}"; do
    foreign "$PDIR/$name" &&
      die "refusing: $PDIR/$name exists and was not installed by Exo. Move it away or choose another install home."
    copy_managed "$EXO_SRC/plugins/$name" "$PDIR/$name" "plugin $name"
  done
done

# The skill pack (skills/ with MANIFEST.json) goes to $EXO_HOME/skills, where the core's skill index and exo-orchestra
# find it; /intro needs its exo-intro skill. Each pack skill is its own marked folder, as plugins are, so a skill you
# add there is never touched, and a folder of yours with a pack skill's name is left alone and wins (an unmarked
# exact copy of the pack's, say from a hand copy of skills/, is adopted). A skill the pack no longer has is removed
# only when its marker names it: a copy of a pack skill made to change it carries the marker of the skill it came
# from, so it is yours, kept with a warning. The root marker says MANIFEST.json is Exo's too. A link (the skills
# folder, a skill or the manifest) is never adopted, replaced or removed: it is yours, one into the checkout keeps
# the pack live there, and rm -rf through it would delete what it points at.
SKDIR="$EXO_HOME/skills"
SKMAN="$EXO_SRC/skills/MANIFEST.json"
if [ -f "$SKMAN" ] && [ "$SKDIR" -ef "$EXO_SRC/skills" ]; then
  say "skill pack: $SKDIR resolves to $EXO_SRC/skills, so the pack is live there; left alone"
elif [ -f "$SKMAN" ] && [ -L "$SKDIR" ]; then
  warn "$SKDIR is a link Exo did not make; left alone, so the skill pack is not installed"
elif [ -f "$SKMAN" ]; then
  [ -d "$SKDIR" ] || run mkdir -p "$SKDIR"
  count=0
  for d in "$EXO_SRC"/skills/*/; do
    name="$(basename "$d")"
    dst="$SKDIR/$name"
    if [ "${d%/}" -ef "$dst" ]; then
      say "skill $name: $dst links to the pack's; left alone"
      continue
    elif [ -L "$dst" ] || { [ -e "$dst" ] && ! managed_as "$dst" &&
                              ! diff -rq -x "$MARK" -x __pycache__ -x .pytest_cache "${d%/}" "$dst" >/dev/null 2>&1; }; then
      warn "$dst was not installed by Exo; left alone (it stands in for the pack's $name skill)"
      continue
    fi
    copy_managed "${d%/}" "$dst" "" keep-tests
    count=$((count + 1))
  done
  for d in "$SKDIR"/*/; do
    d="${d%/}"
    [ ! -L "$d" ] && [ -f "$d/$MARK" ] && [ ! -d "$EXO_SRC/skills/$(basename "$d")" ] || continue
    if managed_as "$d"; then
      run rm -rf "$d"
    else
      warn "$d was not installed by Exo under this name (its $MARK came with a copy); left alone. Delete $d/$MARK to stop this warning."
    fi
  done
  man="$SKDIR/MANIFEST.json"
  if [ -L "$man" ] || { [ -e "$man" ] && [ ! -f "$SKDIR/$MARK" ] && ! cmp -s "$SKMAN" "$man"; }; then
    cmp -s "$SKMAN" "$man" || warn "$man was not written by Exo; left alone"
  elif [ ! -f "$SKDIR/$MARK" ] || ! cmp -s "$SKMAN" "$man"; then
    run cp -p "$SKMAN" "$man"
    if [ "$DRY" -eq 1 ]; then echo "+ write $SKDIR/$MARK"; else
      echo "installed by exo scripts/install.sh from $EXO_SRC/skills: MANIFEST.json and each folder whose $MARK names it." \
        "Other skills here are yours." > "$SKDIR/$MARK"
    fi
  fi
  say "skill pack: $count skills in $SKDIR"
fi

# Hermes plugins are opt-in. A config.yaml Exo creates enables them; an existing one is not edited.
# Only written with --with-hermes: the default install has nothing that reads it.
HCFG="$TARGET_HOME/config.yaml"
enabled="$(IFS=,; echo "${SELECTED[*]}" | sed 's/,/, /g')"
has() { case " ${SELECTED[*]} " in *" $1 "*) return 0 ;; esac; return 1; }
if [ "$WITH_HERMES" -eq 0 ]; then
  :
elif [ ! -f "$HCFG" ]; then
  if [ "$DRY" -eq 1 ]; then
    echo "+ write $HCFG (plugins.enabled: [$enabled])"
  else
    {
      echo "# Written by exo scripts/install.sh. Edit freely; the installer never overwrites this file."
      echo "plugins:"
      echo "  enabled: [$enabled]"
      has exo-memory && printf 'memory:\n  provider: exo-memory\n'
      has exo-context && printf 'context:\n  engine: exo-context\n'
    } > "$HCFG"
  fi
elif head -n1 "$HCFG" | grep -q '^# Written by exo scripts/install.sh'; then
  say "kept $HCFG (written by an earlier Exo install; edit it by hand to change plugins)"
else
  say "kept existing $HCFG. To enable Exo, add to it:"
  echo "    plugins: {enabled: [$enabled]}"
  has exo-memory && echo "    memory: {provider: exo-memory}"
  has exo-context && echo "    context: {engine: exo-context}"
fi

# --- 7. launchers and config.json ----------------------------------------------------
# `hermes` alone would use ~/.hermes. exo-hermes always runs with Exo's HERMES_HOME.
LAUNCH="$EXO_HOME/bin/exo-hermes"
ENVSH="$EXO_HOME/env.sh"
if [ "$DRY" -eq 1 ]; then
  [ "$WITH_HERMES" -eq 1 ] && echo "+ write $LAUNCH (runs $VENV/bin/hermes with HERMES_HOME=$TARGET_HOME)"
  echo "+ write $ENVSH"
else
  if [ "$WITH_HERMES" -eq 1 ]; then
    write_if_changed "$LAUNCH" "$(printf '%s\n' '#!/usr/bin/env bash' \
    '# Written by exo scripts/install.sh. Runs the pinned Hermes with the Exo home.' \
    "export EXO_HOME=\"$EXO_HOME\"" \
    "export HERMES_HOME=\"$TARGET_HOME\"" \
    "exec \"$VENV/bin/hermes\" \"\$@\"")"
    chmod 755 "$LAUNCH"
  fi
  write_if_changed "$ENVSH" "$(printf '%s\n' \
    "# Written by exo scripts/install.sh. Source it to use Exo in this shell:  . \"$ENVSH\"" \
    "export EXO_HOME=\"$EXO_HOME\"" \
    "export PATH=\"$EXO_HOME/bin:$VENV/bin:\$PATH\"")"
fi
if [ -x "$VENV/bin/exo" ] && [ ! -e "$EXO_HOME/bin/exo" ]; then run ln -s "$VENV/bin/exo" "$EXO_HOME/bin/exo"; fi

# The `exo` command: ~/.local/bin/exo -> the venv's exo, so typing `exo` works from anywhere.
LINKED=0
if [ "$PIP" -eq 1 ]; then
  [ -d "$BIN_DIR" ] || run mkdir -p "$BIN_DIR"
  if [ -L "$BIN_DIR/exo" ] || [ ! -e "$BIN_DIR/exo" ]; then
    if [ -L "$BIN_DIR/exo" ] && [ "$(readlink "$BIN_DIR/exo")" = "$VENV/bin/exo" ]; then
      say "command exists: $BIN_DIR/exo"
    else
      run ln -sfn "$VENV/bin/exo" "$BIN_DIR/exo"
    fi
    LINKED=1
  else
    warn "$BIN_DIR/exo exists and is not a link made by this installer; left alone. Remove it, then re-run."
  fi
fi
NEED_PATH=0
if [ "$LINKED" -eq 1 ]; then
  case ":$PATH:" in *":$BIN_DIR:"*) ;; *) NEED_PATH=1 ;; esac
fi
if [ "$NEED_PATH" -eq 1 ]; then
  case "$(basename "${SHELL:-sh}")" in
    zsh)  RC="$HOME/.zshrc";  LINE="export PATH=\"$BIN_DIR:\$PATH\"" ;;
    bash) RC="$HOME/.bashrc"; [ "$(uname -s)" = Darwin ] && [ ! -f "$RC" ] && RC="$HOME/.bash_profile"
          LINE="export PATH=\"$BIN_DIR:\$PATH\"" ;;
    fish) RC="$HOME/.config/fish/config.fish"; LINE="fish_add_path $BIN_DIR" ;;
    *)    RC="$HOME/.profile"; LINE="export PATH=\"$BIN_DIR:\$PATH\"" ;;
  esac
  if [ "$MODIFY_PATH" -eq 1 ]; then
    if [ -f "$RC" ] && grep -qF "$LINE" "$RC"; then
      say "$RC already has the PATH line"
    elif [ "$DRY" -eq 1 ]; then
      echo "+ append to $RC: $LINE"
    else
      mkdir -p "$(dirname "$RC")"
      printf '\n# added by exo scripts/install.sh\n%s\n' "$LINE" >> "$RC"
      say "added the PATH line to $RC"
    fi
    PATH_NOTE="open a new terminal (or: . $RC), then type: exo"
  else
    say "$BIN_DIR is not on your PATH. Add this line to $RC (or re-run with --modify-path):"
    echo "    $LINE"
    PATH_NOTE="after adding that line and opening a new terminal, type: exo   (until then: $BIN_DIR/exo)"
  fi
fi

CFG_PY="$VPY"
[ -x "$CFG_PY" ] && "$CFG_PY" -c 'import exo.config' 2>/dev/null || CFG_PY=""
plugins_json="[$(printf '"%s",' "${SELECTED[@]}" | sed 's/,$//')]"
set_cfg() {  # key json-value
  if [ "$DRY" -eq 1 ]; then echo "+ config.json: $1 = $2"; return; fi
  if [ -n "$CFG_PY" ]; then
    EXO_HOME="$EXO_HOME" "$CFG_PY" -m exo.config set "$1" "$2"
  else
    EXO_HOME="$EXO_HOME" PYTHONPATH="$EXO_SRC/src" "$PY" -m exo.config set "$1" "$2"
  fi
}
if [ "$WITH_HERMES" -eq 1 ]; then set_cfg hermes_dir "\"$EXO_HERMES_DIR\""; fi
if [ "$HERMES_ASKED" -eq 1 ]; then set_cfg hermes_home "\"$TARGET_HOME\""; fi
set_cfg venv "\"$VENV\""
if [ "$PIP" -eq 1 ]; then  # `exo update` reads this to know how to upgrade
  set_cfg install "{\"mode\": \"venv\", \"source\": \"${EXO_URL:-$EXO_SRC}\", \"checkout\": \"$EXO_SRC\", \"venv\": \"$VENV\", \"python\": \"$VPY\"}"
fi
set_cfg plugins "$plugins_json"
if [ "$PLUGINS_EXPLICIT" -eq 1 ]; then set_cfg plugin_selection "$([ -z "$PLUGINS" ] && echo '"all"' || echo "$plugins_json")"; fi
if [ -n "$PIN_SKIP" ]; then set_cfg hermes_patch_skip "\"$PIN_SKIP\""; fi

# --- 8. interview -------------------------------------------------------------------
EXO_RUN=("$VPY" -m exo.onboard)
[ -x "$VPY" ] || EXO_RUN=(env PYTHONPATH="$EXO_SRC/src" "$PY" -m exo.onboard)
if [ "$INTERVIEW" -eq 1 ]; then
  if [ -z "$PRINCIPAL" ] && [ "$YES" -eq 0 ] && [ -t 0 ] && [ "$DRY" -eq 0 ]; then
    printf 'Create your principal store and run the onboarding interview now? [Y/n] '
    read -r reply || reply=n
    case "$reply" in n|N|no) INTERVIEW=0 ;; *)
      printf 'Principal id (lowercase, for example your first name): '
      read -r PRINCIPAL || PRINCIPAL="" ;;
    esac
  fi
fi
if [ "$INTERVIEW" -eq 1 ] && [ -n "$PRINCIPAL" ]; then
  if [ -d "$EXO_HOME/principals/$PRINCIPAL" ]; then
    say "principal store $PRINCIPAL exists; skipping init (run: exo interview --principal $PRINCIPAL)"
  else
    args=(init "$PRINCIPAL" --interview)
    [ -n "$ANSWERS" ] && args+=(--answers "$(abspath "$ANSWERS")")
    echo "+ EXO_HOME=$EXO_HOME $(q "${EXO_RUN[@]}" "${args[@]}")"
    [ "$DRY" -eq 1 ] || EXO_HOME="$EXO_HOME" "${EXO_RUN[@]}" "${args[@]}"
  fi
elif [ "$INTERVIEW" -eq 1 ]; then
  say "next: run exo to start the terminal client and first-run orientation; optional: exo init <id> --interview or exo import-store <path> --id <id>"
fi

# --- 9. exo models check, summary -------------------------------------------------
if [ "$DRY" -eq 1 ]; then
  echo "+ EXO_HOME=$EXO_HOME $VENV/bin/exo models   (check providers, keys and routes)"
elif [ "$PIP" -eq 1 ] && [ -x "$VPY" ]; then
  echo "+ $VENV/bin/exo models"
  EXO_HOME="$EXO_HOME" "$VPY" -m exo.cli models || warn "exo models check failed (see above)"
fi

if [ "${#FAILED_PATCHES[@]}" -gt 0 ]; then
  {
    echo "install: ================ WARNING: Hermes patches that did not apply ================"
    for f in "${FAILED_PATCHES[@]}"; do echo "install:   - $f (skipped)"; done
    echo "install: ${#FAILED_PATCHES[@]} patch(es) skipped. Hermes runs without them. PINNED and config.json record the skip."
    echo "install: ============================================================================"
  } >&2
fi

if [ "$WITH_HERMES" -eq 1 ]; then
  say "done$([ "$DRY" -eq 1 ] && echo ' (dry run)'). Start with:  . $ENVSH && exo-hermes"
else
  say "done$([ "$DRY" -eq 1 ] && echo ' (dry run)'). ${PATH_NOTE:-Start with:  exo}   (Hermes not installed; add it with --with-hermes)"
fi
[ "${#FAILED_PATCHES[@]}" -eq 0 ] || exit 1
