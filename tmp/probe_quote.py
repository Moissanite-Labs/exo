import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
os.environ["HOME"] = os.path.join(os.getcwd(), "home")
os.environ["TMPDIR"] = os.path.join(os.getcwd(), "tmp")
from exo import gate
for w in [".env.'$X'", ".env.\\$X", ".env.$X"]:
    print(repr(w), "trailing_form=", gate._trailing_glob_form(w))
    print("   unknown_forms=", gate._unknown_forms(w))
    c = gate.classify({"tool": "bash", "args": {"command": "cat " + w}})
    print("   parts:", [(p.category, getattr(p, 'name', None)) for p in c.parts] if hasattr(c, 'parts') else c)
