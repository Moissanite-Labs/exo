#!/bin/bash
# Deploy site/ to staging (default) or production.
#   scripts/deploy-site.sh              staging.exo.askaconsult.com  (Vercel project exo-site-staging, noindex)
#   scripts/deploy-site.sh --prod       exo.askaconsult.com          (Vercel project exo-site; asks you to type "production")
#   --redeploy   deploy only, skip the project/domain/DNS setup;  --yes   skip the typed confirmation
# DNS is on Cloudflare. Both targets deploy from a git-free copy of site/.
# Tokens are read from the environment or a local env file and never printed.
# Nothing machine-specific lives here. Set these in the environment, or in an untracked config file
# (default ~/.config/exo/deploy.conf, override with EXO_DEPLOY_CONF; plain KEY=value lines):
#   EXO_VERCEL_TEAM   Vercel team id (team_...)
#   EXO_VERCEL_SCOPE  Vercel team slug
#   EXO_DEPLOY_ENV    path to an env file that holds VERCEL_TOKEN and CF_API_TOKEN
set -u
CONF="${EXO_DEPLOY_CONF:-$HOME/.config/exo/deploy.conf}"
[ -r "$CONF" ] && while IFS='=' read -r k v; do
  case "$k" in EXO_VERCEL_TEAM|EXO_VERCEL_SCOPE|EXO_DEPLOY_ENV) [ -z "${!k:-}" ] && export "$k=$(echo "$v" | tr -d "\"'")";; esac
done < "$CONF"
# Tokens come from the environment first, then from the env file named above (never printed).
ENVFILE="${EXO_DEPLOY_ENV:-$HOME/.config/exo/deploy.env}"
pick() { local v; eval "v=\${$1:-}"; [ -n "$v" ] && { echo "$v"; return; }
  [ -r "$ENVFILE" ] && grep -E "^$1=" "$ENVFILE" | head -1 | cut -d= -f2- | tr -d '"'"'"; }
V=$(pick VERCEL_TOKEN); CF=$(pick CF_API_TOKEN)
[ -n "$V" ] && [ -n "$CF" ] || { echo "deploy-site: set VERCEL_TOKEN and CF_API_TOKEN (or EXO_DEPLOY_ENV to an env file)" >&2; exit 1; }
TEAM="${EXO_VERCEL_TEAM:-}"; SCOPE="${EXO_VERCEL_SCOPE:-}"
[ -n "$TEAM" ] && [ -n "$SCOPE" ] || { echo "deploy-site: set EXO_VERCEL_TEAM and EXO_VERCEL_SCOPE (env or $CONF)" >&2; exit 1; }
PROD=0; REDEPLOY=0; YES=0
for a in "$@"; do case "$a" in --prod) PROD=1;; --redeploy) REDEPLOY=1;; --yes) YES=1;;
  *) echo "deploy-site: unknown option $a (use --prod, --redeploy, --yes)" >&2; exit 2;; esac; done
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
SRC="$(cd "$(dirname "$0")/../site" && pwd)"; W=$(mktemp -d); rsync -a --exclude _shots "$SRC/" "$W/"; cd "$W"
if [ $PROD = 0 ]; then  # staging is never indexed: robots.txt plus an X-Robots-Tag header on every response
  printf 'User-agent: *\nDisallow: /\n' > robots.txt
  printf '{"headers":[{"source":"/(.*)","headers":[{"key":"X-Robots-Tag","value":"noindex, nofollow"}]}]}\n' > vercel.json
fi
npx -y vercel@latest deploy --prod --yes --token "$V" --scope "$SCOPE" --name $P 2>&1 | grep -v -i token | grep -E "Production|Aliased|Error|error" | head -5
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
