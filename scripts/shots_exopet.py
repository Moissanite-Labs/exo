"""Capture Exopet with synthetic counts and conversations, never a real core or store.

    PYTHONPATH=src:plugins/exo-command python scripts/shots_exopet.py OUTDIR
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "plugins/exo-command"),
               str(ROOT / "plugins/exo-command/tests")]
from browser import Browser, Served  # noqa: E402
from shots_workspace import SNAP, Workspace, stub_providers  # noqa: E402
from exo_command import providers  # noqa: E402


class PetWorkspace(Workspace):
    async def call(self, body):
        if type(body).TYPE == "command.run" and body.name == "pet":
            return {"output": {
                "schema_version": 1, "scoring_version": 1, "state": "ready",
                "verification": {"state": "verified", "source": "legacy", "checked": 123,
                                 "anchored": True, "reason": None},
                "personal": {"xp": 320.5, "xp_half_units": 641, "level": 6, "earned_form": "cell"},
                "form": "egg", "next_form": "cell", "next_form_xp": 260, "progress": 1.0,
                "learned_count": 4, "community": {"state": "pending", "hatch_at": None},
                "stale": False,
                "limitations": ["delegation_completion_not_recorded",
                                "unlinked_promotion_notes_are_not_scored"],
            }}
        return await super().call(body)


FORMS = """(async()=>{
  const {createPetRenderer,PET_FORMS}=await import('/static/pet-renderer.js');
  document.querySelector('.xc-pet-mode').value='off';
  document.querySelector('.xc-pet-mode').dispatchEvent(new Event('change'));
  const main=document.getElementById('xc-main');main.replaceChildren();
  const title=document.createElement('h1');title.textContent='Exopet · form studies';
  const note=document.createElement('p');
  note.textContent='Synthetic form preview. The workspace stays an egg while the community hatch is pending.';
  main.style.cssText='padding:32px;display:block;overflow:auto';main.append(title,note);
  const grid=document.createElement('div');grid.style.cssText='display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:20px;margin-top:28px';main.append(grid);
  window.previewPets=[];
  for(const form of PET_FORMS){
    const card=document.createElement('section');card.className='xc-pet-card';
    const host=document.createElement('div');host.className='xc-pet-figure';
    host.style.cssText='width:100%;height:190px';
    const name=document.createElement('h2');name.textContent=form;
    name.style.cssText='font-size:16px;text-align:center';card.append(host,name);grid.append(card);
    const renderer=createPetRenderer(host,{reducedMotion:true});
    renderer.render({form,state:'idle',learned:4,active:false,reducedMotion:true});window.previewPets.push(renderer);
  }
  return PET_FORMS.length;
})()"""


def render(out: Path) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    previous = providers.handle
    providers.handle = stub_providers(True)
    try:
        for empty in (False, True):
            srv = Served(PetWorkspace(empty), token="synthetic-pet-preview")
            srv.centre.snapshot = lambda *args, **kwargs: SNAP
            try:
                for width in (1440, 390):
                    with Browser(width=width, height=1000) as b:
                        b.goto(srv.url())
                        b.wait("!!document.querySelector('.xc-pet-mode') && !!document.querySelector('.xc-chat')")
                        b.eval("(()=>{const s=document.querySelector('.xc-pet-mode');s.value='full';s.dispatchEvent(new Event('change'));})()")
                        b.wait("document.querySelector('.xc-pet-card')?.textContent.includes('320.5')")
                        for theme in ("dark", "light"):
                            b.eval(f"(()=>{{const s=document.querySelector('.xc-appearance');s.value='{theme}';s.dispatchEvent(new Event('change'));}})()")
                            name = f"{'empty' if empty else 'chat'}-{theme}-{width}"
                            b.shot(out / (name + ".png"))
                            rows.append({"screen": name, **b.eval("({viewport:innerWidth,page:document.documentElement.scrollWidth})")})
                        if not empty and width == 1440:
                            assert b.eval(FORMS) == 10
                            for theme in ("dark", "light"):
                                b.eval(f"(()=>{{const s=document.querySelector('.xc-appearance');s.value='{theme}';s.dispatchEvent(new Event('change'));}})()")
                                b.shot(out / f"forms-{theme}.png")
            finally:
                srv.close()
    finally:
        providers.handle = previous
    (out / "geometry.json").write_text(json.dumps(rows, indent=2) + "\n")
    return rows


if __name__ == "__main__":
    print(json.dumps(render(Path(sys.argv[1])), indent=2))
