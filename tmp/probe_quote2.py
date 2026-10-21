import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
os.environ["HOME"] = os.path.join(os.getcwd(), "home")
os.environ["TMPDIR"] = os.path.join(os.getcwd(), "tmp")
from exo import gate
for cmd in ["cat .env.'$X'", "cat .env.\\$X", "cat .env.$X"]:
    c = gate.classify({"tool": "bash", "args": {"command": cmd}})
    print(cmd, "-> category:", c.category)
    for attr in ("readings", "words", "hits", "reasons"):
        if hasattr(c, attr):
            print("  ", attr, "=", getattr(c, attr))
    print("   as_dict keys:", list(c.as_dict().keys()) if hasattr(c, "as_dict") else None)
