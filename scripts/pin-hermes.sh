#!/usr/bin/env bash
# pin-hermes.sh: pin a Hermes Agent checkout to an upstream release tag and
# apply Exo's patch queue (patches/*.patch) in order.
#
# Usage: scripts/pin-hermes.sh [TAG]
#
# Environment:
#   EXO_HOME          default ~/.exo
#   EXO_HERMES_DIR    default $EXO_HOME/hermes   (the checkout this script owns)
#   EXO_HERMES_REPO   default https://github.com/NousResearch/hermes-agent
#   EXO_PATCH_DIR     default <repo>/patches
#   EXO_HERMES_TAG    used when no TAG argument is given
#   EXO_PATCH_SKIP    optional comma list of patch numbers or file names to
#                     leave out (for example "0001,0004"). Skipped patches are
#                     recorded in PINNED. Use only for a canary while a patch
#                     is being rebased; the default is to carry every patch.
#
# Behaviour:
#   - With no tag, picks the latest stable release tag from `git ls-remote`.
#   - Builds the patched tree in a temporary worktree first. If any patch
#     fails, it refuses and leaves the existing checkout untouched.
#   - On success, moves the checkout to the tag, applies the same patches and
#     writes $EXO_HERMES_DIR/PINNED.
#   - Idempotent: a second run with the same tag and patches does nothing.
#   - Never touches a live Hermes install: refuses any path inside
#     ${HERMES_HOME:-~/.hermes}.
set -euo pipefail

die() { echo "pin-hermes: $*" >&2; exit 1; }
log() { echo "pin-hermes: $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXO_REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

EXO_HOME="${EXO_HOME:-$HOME/.exo}"
EXO_HERMES_DIR="${EXO_HERMES_DIR:-$EXO_HOME/hermes}"
EXO_HERMES_REPO="${EXO_HERMES_REPO:-https://github.com/NousResearch/hermes-agent}"
EXO_PATCH_DIR="${EXO_PATCH_DIR:-$EXO_REPO_DIR/patches}"
TAG="${1:-${EXO_HERMES_TAG:-}}"

command -v git >/dev/null || die "git not found"

# Resolve a path without requiring it to exist yet.
abspath() {
  local p="$1" parent
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  parent="$(dirname "$p")"
  mkdir -p "$parent"
  echo "$(cd "$parent" && pwd -P)/$(basename "$p")"
}

sha256() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

# --- Safety: never operate inside a live Hermes home -----------------------
TARGET="$(abspath "$EXO_HERMES_DIR")"
LIVE_HOME="${HERMES_HOME:-$HOME/.hermes}"
for guard in "$HOME/.hermes" "$LIVE_HOME"; do
  [ -d "$guard" ] || continue
  g="$(cd "$guard" && pwd -P)"
  case "$TARGET/" in
    "$g"/*) die "refusing: $TARGET is inside the live Hermes home $g" ;;
  esac
done

# --- Resolve the tag --------------------------------------------------------
# Stable means a plain vMAJOR.MINOR.PATCH tag, no rc, canary or suffix.
# Hermes moved from calendar tags (v2026.9.24) to semver (v0.21.6); semver
# wins when present, calendar tags are the fallback.
latest_stable_tag() {
  local tags semver
  tags="$(git ls-remote --tags --refs "$EXO_HERMES_REPO" | awk '{print $2}' | sed 's#^refs/tags/##')" \
    || die "git ls-remote failed for $EXO_HERMES_REPO"
  semver="$(echo "$tags" | grep -E '^v[0-9]{1,3}\.[0-9]+\.[0-9]+$' | sort -V | tail -n1 || true)"
  if [ -n "$semver" ]; then echo "$semver"; return; fi
  echo "$tags" | grep -E '^v[0-9]{4}\.[0-9]+\.[0-9]+(\.[0-9]+)?$' | sort -V | tail -n1
}

if [ -z "$TAG" ]; then
  TAG="$(latest_stable_tag)"
  [ -n "$TAG" ] || die "could not find a stable release tag"
  log "latest stable tag: $TAG"
fi

# --- Patch list -------------------------------------------------------------
PATCHES=()
SKIPPED=()
is_skipped() {  # $1 = patch basename
  local item
  [ -n "${EXO_PATCH_SKIP:-}" ] || return 1
  for item in ${EXO_PATCH_SKIP//,/ }; do
    case "$1" in "$item"|"$item"-*) return 0 ;; esac
  done
  return 1
}
if [ -d "$EXO_PATCH_DIR" ]; then
  while IFS= read -r f; do
    if is_skipped "$(basename "$f")"; then SKIPPED+=("$(basename "$f")"); else PATCHES+=("$f"); fi
  done < <(find "$EXO_PATCH_DIR" -maxdepth 1 -name '*.patch' -type f | sort)
fi
[ "${#SKIPPED[@]}" -eq 0 ] || log "skipping by request: ${SKIPPED[*]}"
QUEUE=$(( ${#PATCHES[@]} + ${#SKIPPED[@]} ))
[ "$QUEUE" -le 5 ] || die "patch queue has $QUEUE patches; the limit is 5 (D-005)"

patch_manifest() {
  local f
  for f in ${PATCHES[@]+"${PATCHES[@]}"}; do
    echo "$(basename "$f") $(sha256 "$f")"
  done
  for f in ${SKIPPED[@]+"${SKIPPED[@]}"}; do
    echo "$f skipped"
  done
}
MANIFEST="$(patch_manifest)"

# --- Create or adopt the checkout -------------------------------------------
if [ -e "$TARGET" ] && [ ! -d "$TARGET/.git" ]; then
  if [ -n "$(ls -A "$TARGET" 2>/dev/null)" ]; then
    die "refusing: $TARGET exists, is not empty and is not an Exo-managed checkout"
  fi
fi

if [ ! -d "$TARGET/.git" ]; then
  log "creating checkout at $TARGET"
  mkdir -p "$TARGET"
  git -C "$TARGET" init -q
  git -C "$TARGET" remote add origin "$EXO_HERMES_REPO"
  git -C "$TARGET" config exo.managed true
fi

[ "$(git -C "$TARGET" config --get exo.managed || true)" = "true" ] \
  || die "refusing: $TARGET is a git repo not created by pin-hermes.sh (no exo.managed flag)"

git -C "$TARGET" remote set-url origin "$EXO_HERMES_REPO"

# --- Idempotency check ------------------------------------------------------
pinned_value() { sed -n "s/^$1: //p" "$TARGET/PINNED" 2>/dev/null | head -n1; }

if [ -f "$TARGET/PINNED" ] && [ "$(pinned_value tag)" = "$TAG" ]; then
  want_manifest="$(sed -n '/^patches:$/,/^end-patches$/p' "$TARGET/PINNED" | sed '1d;$d' | sed 's/^  //')"
  want_tree="$(pinned_value patched_tree)"
  if [ "$want_manifest" = "$MANIFEST" ] \
     && [ "$(git -C "$TARGET" rev-parse HEAD 2>/dev/null)" = "$(pinned_value tag_commit)" ] \
     && [ "$(git -C "$TARGET" write-tree 2>/dev/null)" = "$want_tree" ] \
     && git -C "$TARGET" diff --quiet 2>/dev/null; then
    log "already pinned to $TAG with the same patches; nothing to do"
    exit 0
  fi
fi

# --- Fetch the tag (shallow) ------------------------------------------------
log "fetching $TAG from $EXO_HERMES_REPO"
git -C "$TARGET" fetch -q --depth 1 --no-tags origin "refs/tags/$TAG:refs/tags/$TAG" \
  || die "could not fetch tag $TAG"
TAG_COMMIT="$(git -C "$TARGET" rev-parse "refs/tags/$TAG^{commit}")"
TAG_OBJECT="$(git -C "$TARGET" rev-parse "refs/tags/$TAG")"

# --- Dry run in a temporary worktree ----------------------------------------
apply_patches() {  # $1 = work tree; prints applied names; returns non-zero on failure
  local wt="$1" f
  for f in ${PATCHES[@]+"${PATCHES[@]}"}; do
    if ! git -C "$wt" apply --check --index "$f" 2>"$wt.err"; then
      log "patch FAILED: $(basename "$f")"
      sed 's/^/    /' "$wt.err" | head -n 20 >&2
      rm -f "$wt.err"
      return 1
    fi
    git -C "$wt" apply --index "$f"
    log "applied $(basename "$f")"
  done
  rm -f "$wt.err"
}

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/exo-pin-stage.XXXXXX")"
rmdir "$STAGE"
cleanup() {
  git -C "$TARGET" worktree remove --force "$STAGE" >/dev/null 2>&1 || true
  rm -rf "$STAGE" "$STAGE.err"
  git -C "$TARGET" worktree prune >/dev/null 2>&1 || true
}
trap cleanup EXIT

git -C "$TARGET" worktree add -q --detach "$STAGE" "$TAG_COMMIT"
log "dry run: applying ${#PATCHES[@]} patch(es) to $TAG in a temporary worktree"
if ! apply_patches "$STAGE"; then
  die "refusing to pin $TAG: a patch does not apply. Checkout left unchanged."
fi
STAGE_TREE="$(git -C "$STAGE" write-tree)"
cleanup
trap - EXIT

# --- Real run on the checkout -----------------------------------------------
log "moving $TARGET to $TAG"
git -C "$TARGET" checkout -q --force --detach "$TAG_COMMIT"
git -C "$TARGET" reset -q --hard "$TAG_COMMIT"
git -C "$TARGET" clean -q -fd -e PINNED
apply_patches "$TARGET" || die "patch failed on the real checkout after a clean dry run (unexpected)"
TREE="$(git -C "$TARGET" write-tree)"
[ "$TREE" = "$STAGE_TREE" ] || die "patched tree differs from dry run ($TREE vs $STAGE_TREE)"

# --- PINNED -----------------------------------------------------------------
{
  echo "# Written by exo scripts/pin-hermes.sh. Do not edit by hand."
  echo "tag: $TAG"
  echo "tag_object: $TAG_OBJECT"
  echo "tag_commit: $TAG_COMMIT"
  echo "patched_tree: $TREE"
  echo "repo: $EXO_HERMES_REPO"
  echo "exo_patch_dir: $EXO_PATCH_DIR"
  echo "pinned_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "patches:"
  [ -n "$MANIFEST" ] && echo "$MANIFEST" | sed 's/^/  /'
  echo "end-patches"
} > "$TARGET/PINNED"

# Keep PINNED out of `git status` noise.
grep -qx 'PINNED' "$TARGET/.git/info/exclude" 2>/dev/null || echo 'PINNED' >> "$TARGET/.git/info/exclude"

log "pinned $TARGET to $TAG ($TAG_COMMIT) with ${#PATCHES[@]} patch(es)"
