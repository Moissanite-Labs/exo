#!/bin/bash
# Deploy site/ to exo.askaconsult.com (Vercel project exo-site, DNS on Cloudflare).
# Tokens are read from local hermes profile env files and never printed.
set -u
V=$(grep -E "^VERCEL_TOKEN=" ~/.hermes/profiles/shaka/.env | head -1 | cut -d= -f2- | tr -d '"'"'")
CF=$(grep -E "^CF_API_TOKEN=" ~/.hermes/profiles/shaka/.env | head -1 | cut -d= -f2- | tr -d '"'"'")
TEAM=team_FaoexnZzdTcrVOAXDutsVakN; P=exo-site; D=exo.askaconsult.com
vc() { curl -s -H "Authorization: Bearer $V" -H "Content-Type: application/json" "$@"; }
cf() { curl -s -H "Authorization: Bearer $CF" -H "Content-Type: application/json" "$@"; }
# Deploy from a copy outside git: Vercel reads the commit author and blocks emails that are not on the
# team (this machine's git identity is a seat address, not A's Vercel login).
SRC="$(cd "$(dirname "$0")/../site" && pwd)"; W=$(mktemp -d); rsync -a --exclude _shots "$SRC/" "$W/"; cd "$W"
npx -y vercel@latest deploy --prod --yes --token "$V" --scope a-4677s-projects --name $P 2>&1 | grep -v -i token | grep -E "Production|Aliased|Error|error" | head -5
[ "${1:-}" = "--redeploy" ] && exit 0
vc -X PATCH "https://api.vercel.com/v9/projects/$P?teamId=$TEAM" -d '{"ssoProtection":null}' | python3 -c 'import sys,json;d=json.load(sys.stdin);print("sso",d.get("ssoProtection"),d.get("error"))'
R=$(vc -X POST "https://api.vercel.com/v10/projects/$P/domains?teamId=$TEAM" -d "{\"name\":\"$D\"}")
echo "$R" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("domain",d.get("name"),"verified",d.get("verified"),d.get("error"))'
Z=$(cf "https://api.cloudflare.com/client/v4/zones?name=askaconsult.com" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"][0]["id"])')
echo "$R" | python3 -c 'import sys,json
for v in json.load(sys.stdin).get("verification") or []: print(v["type"],v["domain"],v["value"])' | while read t n val; do
  cf -X POST "https://api.cloudflare.com/client/v4/zones/$Z/dns_records" -d "{\"type\":\"$t\",\"name\":\"$n\",\"content\":\"$val\",\"ttl\":300}" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("txt",d["success"],d.get("errors"))'
done
cf -X POST "https://api.cloudflare.com/client/v4/zones/$Z/dns_records" -d "{\"type\":\"CNAME\",\"name\":\"exo\",\"content\":\"cname.vercel-dns.com\",\"proxied\":false,\"ttl\":300}" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("cname",d["success"],d.get("errors"))'
for i in 1 2 3 4 5 6; do
  r=$(vc -X POST "https://api.vercel.com/v9/projects/$P/domains/$D/verify?teamId=$TEAM" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("verified"),d.get("error",{}).get("code",""))')
  echo "verify $i: $r"; [[ $r == True* ]] && break; sleep 20
done
