"""Signed temporary ledger through builtin, HTTP API and rendered growth text."""
import json
import sqlite3
from types import SimpleNamespace

from browser import Browser, Served
from test_pet_web import PetSource, mode, needs_browser
from exo import crypto, diagnostics
from exo.config import Config
from exo.keyring import KeyRegistry
from exo.ledger import Ledger
from exo.principal import CALLER, Caller


@needs_browser
def test_signed_ledger_builtin_http_browser_and_integrity_freeze(tmp_path, monkeypatch):
    home, root = tmp_path / 'home', tmp_path / 'principal'
    home.mkdir(); root.mkdir()
    monkeypatch.setenv('EXO_HOME', str(home))
    monkeypatch.delenv('EXO_LEDGER_AUTHORITY', raising=False)
    monkeypatch.delenv('EXO_LEDGER_AUTHORITY_REQUIRED', raising=False)
    Config({'learn': {'store': str(root)}}, home).save()
    signer = crypto.Signer('agent', crypto.keygen())
    registry = KeyRegistry.path_for(home)
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({'v': 1, 'keys': {'agent': [signer.pub]}, 'principals': []}))
    with Ledger(root / 'ledger.sqlite', signer) as ledger:
        user = ledger.append('utterance', {'role': 'user', 'text': 'PRIVATE QUESTION'}, 'principal', session='s1')
        ledger.append('utterance', {'role': 'assistant', 'text': 'PRIVATE ANSWER'}, 'agent', session='s1', refs=[user['id']])
    engine = SimpleNamespace(home=home)

    class SignedSource(PetSource):
        async def call(self, body):
            if type(body).TYPE == 'command.run' and body.name == 'pet':
                self.calls.append(('command.run', 'pet'))
                token = CALLER.set(Caller('principal'))
                try:
                    return {'output': await diagnostics.builtin_command(engine, body)}
                finally:
                    CALLER.reset(token)
            return await super().call(body)

    source = SignedSource(); srv = Served(source)
    try:
        with Browser(width=390, height=900) as b:
            b.goto(srv.url()); mode(b, 'full')
            b.wait("document.querySelector('.xc-pet-growth').textContent.includes('10 XP banked')")
            assert 'PRIVATE' not in b.eval('document.body.textContent')
            assert b.eval("document.querySelector('.xc-pet-hero svg').dataset.form") == 'egg'
            # Corrupt only the isolated fixture; a subsequent real read must freeze verified XP.
            with sqlite3.connect(root / 'ledger.sqlite') as db:
                db.execute('DROP TRIGGER events_no_delete')
                db.execute('DELETE FROM events WHERE seq=2')
            mode(b, 'off'); mode(b, 'full')
            b.wait("document.querySelector('.xc-pet-growth').textContent.includes('10 XP banked')")
            b.wait("document.querySelector('.xc-pet-card').textContent.toLowerCase().includes('growth frozen')")
            assert not any(kind == 'turn.send' for kind, _ in source.calls)
    finally:
        srv.close()
