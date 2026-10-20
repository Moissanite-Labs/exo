#!/bin/bash
# Deploy site/ to staging (default) or production.
#   scripts/deploy-site.sh              staging.exo.askaconsult.com  (Vercel project exo-site-staging, noindex)
#   scripts/deploy-site.sh --prod       exo.askaconsult.com          (Vercel project exo-site; asks you to type "production")
#   --version V  production needs site-vX.Y (refused if that tag exists); staging may give site-vX.Y-rc.N.
#                The deployed commit is tagged V and the tag pushed. See docs/VERSIONING.md.
#                A failed copy or deploy exits non-zero and tags nothing. A versioned deploy needs a clean site/.
#   Any deploy, versioned or not, refuses when site/ holds untracked files (so a private draft folder such as
#   site/pitch/ can never ship). Commit them or move them out of site/. There is no override.
#   It also refuses a submodule under site/, a site/ that is itself a symlink (rsync would follow it and copy files git
#   never listed), and refuses when git cannot list the files or report the tree's status (a corrupt index).
#   The Vercel CLI is pinned (VERCEL_CLI_VERSION below). EXO_VERCEL_CLI may name another exact X.Y.Z, never a tag or an empty value.
#   --redeploy   deploy only, skip the project/domain/DNS setup;  --yes   skip the typed confirmation
# DNS is on Cloudflare. Both targets deploy from a git-free copy of site/.
# Tokens are read from the environment or a local env file and never printed.
# Nothing machine-specific lives here. Set these in the environment, or in an untracked config file
# (default ~/.config/exo/deploy.conf, override with EXO_DEPLOY_CONF; plain KEY=value lines):
#   EXO_VERCEL_TEAM   Vercel team id (team_...)
#   EXO_VERCEL_SCOPE  Vercel team slug
#   EXO_DEPLOY_ENV    path to an env file that holds VERCEL_TOKEN and CF_API_TOKEN
set -u
VERCEL_CLI_VERSION="64.1.0"
CONF="${EXO_DEPLOY_CONF:-$HOME/.config/exo/deploy.conf}"
[ -r "$CONF" ] && while IFS='=' read -r k v; do
  case "$k" in
    EXO_VERCEL_CLI) [ -z "${!k+x}" ] && export "$k=$(echo "$v" | tr -d "\"'")";;  # set-but-empty stays empty and is refused below
    EXO_VERCEL_TEAM|EXO_VERCEL_SCOPE|EXO_DEPLOY_ENV) [ -z "${!k:-}" ] && export "$k=$(echo "$v" | tr -d "\"'")";; esac
done < "$CONF"
# Tokens come from the environment first, then from the env file named above (never printed).
ENVFILE="${EXO_DEPLOY_ENV:-$HOME/.config/exo/deploy.env}"
pick() { local v; eval "v=\${$1:-}"; [ -n "$v" ] && { echo "$v"; return; }
  [ -r "$ENVFILE" ] && grep -E "^$1=" "$ENVFILE" | head -1 | cut -d= -f2- | tr -d '"'"'"; }
V=$(pick VERCEL_TOKEN); CF=$(pick CF_API_TOKEN)
[ -n "$V" ] && [ -n "$CF" ] || { echo "deploy-site: set VERCEL_TOKEN and CF_API_TOKEN (or EXO_DEPLOY_ENV to an env file)" >&2; exit 1; }
TEAM="${EXO_VERCEL_TEAM:-}"; SCOPE="${EXO_VERCEL_SCOPE:-}"
[ -n "$TEAM" ] && [ -n "$SCOPE" ] || { echo "deploy-site: set EXO_VERCEL_TEAM and EXO_VERCEL_SCOPE (env or $CONF)" >&2; exit 1; }
CLI="${EXO_VERCEL_CLI-$VERCEL_CLI_VERSION}"  # no colon: an empty value is malformed, not "use the pin"
[[ "$CLI" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "deploy-site: the Vercel CLI version must be an exact X.Y.Z, got '$CLI'" >&2; exit 2; }
PROD=0; REDEPLOY=0; YES=0; VER=""
while [ $# -gt 0 ]; do case "$1" in --prod) PROD=1;; --redeploy) REDEPLOY=1;; --yes) YES=1;;
  --version) [ $# -ge 2 ] || { echo "deploy-site: --version needs a value" >&2; exit 2; }; VER="$2"; shift;;
  *) echo "deploy-site: unknown option $1 (use --prod, --redeploy, --yes, --version)" >&2; exit 2;; esac; shift; done
REPO="$(cd "$(dirname "$0")/.." && pwd)"
command -v rsync >/dev/null || { echo "deploy-site: rsync is required to copy site/ out of git" >&2; exit 1; }
# The source root must be a real directory. git does not descend through a symlink, so it would list nothing, but
# rsync follows a source argument that ends in a slash and would copy the target's files, private ones included.
SRC="$REPO/site"
if [ -L "$SRC" ] || [ ! -d "$SRC" ]; then
  echo "deploy-site: $SRC is a symlink or not a directory; nothing deployed. The site source must be a real directory inside the repository." >&2
  exit 1
fi
# The tag names HEAD, so a versioned deploy must ship exactly what HEAD holds. An unversioned one is marked -dirty.
# A failed status is not a clean tree: an empty answer from a failed git would tag HEAD for bytes it does not hold.
DIRTY=$(git -C "$REPO" status --porcelain -- site ':(exclude)site/_shots'); RC=$?
if [ $RC != 0 ]; then
  echo "deploy-site: could not read the status of site/ (git exit $RC); nothing deployed. Check the repository (git status, git fsck)." >&2
  exit 1
fi
# Untracked files (ignored ones too) would be copied by rsync and shipped. Refuse before anything else happens.
UNTRACKED=$(git -C "$REPO" ls-files --others -- site ':(exclude)site/_shots' ':(exclude)site/.DS_Store' ':(exclude)site/**/.DS_Store'); RC=$?
if [ $RC != 0 ]; then
  echo "deploy-site: could not list untracked files in site/ (git exit $RC); nothing deployed. Check the repository (git status, git fsck)." >&2
  exit 1
fi
if [ -n "$UNTRACKED" ]; then
  echo "deploy-site: site/ has untracked files; nothing deployed. Commit them or move them out of site/:" >&2
  printf '%s\n' "$UNTRACKED" | head -10 | sed 's/^/  /' >&2
  exit 1
fi
# A submodule is one entry in the parent index, so the inventory above never sees the files inside it, but rsync copies
# them (untracked and ignored ones too). Refuse any submodule under site/ rather than trust what its working tree holds.
STAGED=$(git -C "$REPO" ls-files --stage -- site); RC=$?
if [ $RC != 0 ]; then
  echo "deploy-site: could not list the tracked files in site/ (git exit $RC); nothing deployed." >&2
  exit 1
fi
GITLINKS=$(printf '%s\n' "$STAGED" | sed -n 's/^160000 [0-9a-f]* [0-9]'"$(printf '\t')"'//p')
if [ -n "$GITLINKS" ]; then
  echo "deploy-site: site/ contains a submodule; nothing deployed. Its working tree can hold files git does not list. Vendor its files into site/ or move it out:" >&2
  printf '%s\n' "$GITLINKS" | head -10 | sed 's/^/  /' >&2
  exit 1
fi
# A tracked entry for site/ itself with mode 120000 is a symlink in HEAD or the index: refuse it even if the working
# tree now shows a directory. Symlinks inside site/ are fine: rsync -a keeps them as links and copies no targets.
if printf '%s\n' "$STAGED" | grep -q "^120000 [0-9a-f]* [0-9]$(printf '\t')site\$"; then
  echo "deploy-site: site/ is tracked as a symlink; nothing deployed. The site source must be a real directory." >&2
  exit 1
fi
if [ $PROD = 1 ]; then
  [ -n "$VER" ] || { echo "deploy-site: --prod needs --version site-vX.Y" >&2; exit 2; }
  [[ "$VER" =~ ^site-v[0-9]+\.[0-9]+$ ]] || { echo "deploy-site: production version must look like site-vX.Y, got $VER" >&2; exit 2; }
elif [ -n "$VER" ]; then
  [[ "$VER" =~ ^site-v[0-9]+\.[0-9]+(-rc\.[0-9]+)?$ ]] || { echo "deploy-site: staging version must look like site-vX.Y-rc.N, got $VER" >&2; exit 2; }
fi
if [ -n "$VER" ]; then
  [ -z "$DIRTY" ] || { echo "deploy-site: site/ has uncommitted changes; commit them before deploying $VER" >&2; exit 1; }
  git -C "$REPO" fetch -q --tags 2>/dev/null
  if git -C "$REPO" rev-parse -q --verify "refs/tags/$VER" >/dev/null || git -C "$REPO" ls-remote --exit-code --tags origin "refs/tags/$VER" >/dev/null 2>&1; then
    echo "deploy-site: tag $VER already exists; versions are never reused" >&2; exit 1
  fi
fi
if [ $PROD = 1 ]; then P=exo-site; D=exo.askaconsult.com; SUB=exo; TARGET=PRODUCTION
else P=exo-site-staging; D=staging.exo.askaconsult.com; SUB=staging.exo; TARGET=staging; fi
echo "deploy-site: target $TARGET ($D, Vercel project $P)"
if [ $PROD = 1 ] && [ $YES = 0 ] && [ -t 0 ]; then
  read -r -p "This deploys to the live public site. Type 'production' to continue: " ans
  [ "$ans" = "production" ] || { echo "deploy-site: cancelled" >&2; exit 1; }
fi
vc() { curl -s -H "Authorization: Bearer $V" -H "Content-Type: application/json" "$@"; }
cf() { curl -s -H "Authorization: Bearer $CF" -H "Content-Type: application/json" "$@"; }
# Deploy from a copy outside git: Vercel reads the commit author and blocks emails that are not on the
# team (a machine's git identity is often not a Vercel team login).
W=$(mktemp -d)
rsync -a --exclude _shots --exclude .DS_Store "$SRC/" "$W/" && [ -f "$W/index.html" ] || { echo "deploy-site: copying $SRC failed or found no index.html; nothing deployed" >&2; exit 1; }
# version.json at the site root, for the footer. Archive the outgoing production version before this step (docs/VERSIONING.md).
COMMIT=$(git -C "$REPO" rev-parse HEAD); EXOV=$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -1)
printf '{"site":"%s","exo":"%s","commit":"%s","date":"%s"}\n' "${VER:-unversioned}" "$EXOV" "${COMMIT:0:7}${DIRTY:+-dirty}" "$(date -u +%Y-%m-%d)" > "$W/version.json"
cd "$W"
if [ $PROD = 0 ]; then  # staging is never indexed: robots.txt plus an X-Robots-Tag header on every response
  printf 'User-agent: *\nDisallow: /\n' > robots.txt
  printf '{"headers":[{"source":"/(.*)","headers":[{"key":"X-Robots-Tag","value":"noindex, nofollow"}]}]}\n' > vercel.json
fi
OUT=$(npx -y "vercel@$CLI" deploy --prod --yes --token "$V" --scope "$SCOPE" --name $P 2>&1); RC=$?
printf '%s\n' "$OUT" | grep -v -i token | grep -E "Production|Aliased|Error|error" | head -5
[ $RC = 0 ] || { echo "deploy-site: vercel deploy failed (exit $RC)${VER:+; $VER is not tagged}" >&2; exit 1; }
if [ -n "$VER" ]; then  # tag the deployed commit and push the tag
  git -C "$REPO" tag -a "$VER" "$COMMIT" -m "$VER ($TARGET deploy, exo $EXOV)" && git -C "$REPO" push -q origin "refs/tags/$VER" && echo "deploy-site: tagged $VER at ${COMMIT:0:7}" \
    || { echo "deploy-site: deployed, but tagging $VER at ${COMMIT:0:7} or pushing the tag failed; finish it by hand" >&2; exit 1; }
fi
[ $REDEPLOY = 1 ] && exit 0
vc -X PATCH "https://api.vercel.com/v9/projects/$P?teamId=$TEAM" -d '{"ssoProtection":null}' | python3 -c 'import sys,json;d=json.load(sys.stdin);print("sso",d.get("ssoProtection"),d.get("error"))'
R=$(vc -X POST "https://api.vercel.com/v10/projects/$P/domains?teamId=$TEAM" -d "{\"name\":\"$D\"}")
echo "$R" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("domain",d.get("name"),"verified",d.get("verified"),d.get("error"))'
Z=$(cf "https://api.cloudflare.com/client/v4/zones?name=askaconsult.com" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"][0]["id"])')
echo "$R" | python3 -c 'import sys,json
for v in json.load(sys.stdin).get("verification") or []: print(v["type"],v["domain"],v["value"])' | while read t n val; do
  cf -X POST "https://api.cloudflare.com/client/v4/zones/$Z/dns_records" -d "{\"type\":\"$t\",\"name\":\"$n\",\"content\":\"$val\",\"ttl\":300}" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("txt",d["success"],d.get("errors"))'
done
cf -X POST "https://api.cloudflare.com/client/v4/zones/$Z/dns_records" -d "{\"type\":\"CNAME\",\"name\":\"$SUB\",\"content\":\"cname.vercel-dns.com\",\"proxied\":false,\"ttl\":300}" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("cname",d["success"],d.get("errors"))'
for i in 1 2 3 4 5 6; do
  r=$(vc -X POST "https://api.vercel.com/v9/projects/$P/domains/$D/verify?teamId=$TEAM" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("verified"),d.get("error",{}).get("code",""))')
  echo "verify $i: $r"; [[ $r == True* ]] && break; sleep 20
done
