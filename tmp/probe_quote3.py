import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
os.environ["HOME"] = os.path.join(os.getcwd(), "home")
os.environ["TMPDIR"] = os.path.join(os.getcwd(), "tmp")
from exo import gate
POL = gate.DEFAULT_POLICIES
def out(cmd):
    c = gate.classify({"tool": "bash", "args": {"command": cmd}})
    return c.category, gate.decide(c, POL).outcome
for cmd in ["cat '.env.$X'", 'cat ".env.$X"', "cat .env.'$X'", r"cat .env.\$X", "cat .env.$X",
            "cat foo'$X'", "cat '.env.local'", "cat .env.local"]:
    print(out(cmd), cmd)
