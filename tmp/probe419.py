import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
os.environ["HOME"] = os.path.join(os.getcwd(), "home")
os.environ["TMPDIR"] = os.path.join(os.getcwd(), "tmp")
os.makedirs(os.path.join(os.getcwd(), "home", "proj"), exist_ok=True)
open(os.path.join(os.getcwd(), "home", "proj", ".env.local"), "w").close()
from exo import gate
POL = gate.DEFAULT_POLICIES
def out(cmd, tool="bash"):
    c = gate.classify({"tool": tool, "args": {"command": cmd}})
    return gate.decide(c, POL).outcome, c.category
rows = [
    # finding 1: suffix globs
    "cat .env.prod*", "cat .env.p*", "cat proj/.env.prod*", "cat .env.???", "cat .env.[pd]rod",
    "cat .env.{a,b}*", "cat .env.prod",
    # finding 2: earlier expansion disables trailing reading
    'cat $DIR/.env.$STAGE', 'cat "${PROJECT_DIR}/.env.${STAGE}"', "cat $PWD/.env.$STAGE",
    "cat .env.$A$B", 'cat "$APP_DIR/.env.$STAGE"', "cat $dir/.env.$X", 'cat "$PWD/.env.$STAGE"',
    "cat .env.$STAGE$REGION", "cat .env.$X$(hostname)",
    # should-fix: HOME-anchored glob fall-through
    "cat ~/*/.env.*", "cat $HOME/*/.env.*", "cat ~/*/server.*", "cat ~/proj/*/.env.*",
    # pinned existing rows
    "cat proj/server.*", "cat proj/credentials.*", "cat src/config.*", "cat proj/hosts.*",
    "cat proj/.e*", "cat proj/*", "cat build-$(date +%s).log", "cat notes-$(date +%F).md",
    "cat report-$USER",
    # friction rows (reviewers' minor)
    "tail -f logs/server.*", "cat build/keys.*", "ls tests/*secret*", "cat config.$ENV",
    # host/url/message over-ask nits
    "ssh server.$DOMAIN", "curl https://auth.$DOMAIN", 'git commit -m "bump auth.$VERSION"',
    "echo id_$X", "ping server.*",
    # quoting nit
    "cat .env.'$X'", r"cat .env.\$X",
    # already-asking controls
    "cat $dir/.env.*", "cat $dir/.env.local", 'cat "$(pwd)/.env.$STAGE"',
]
for r in rows:
    o, cat = out(r)
    print(f"{o:6} {cat:12} {r}")
