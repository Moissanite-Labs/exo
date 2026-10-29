"""Human CLI reads must not attribute forged plaintext to a seat (desktop audit #53)."""
from exo.bus.cli import _show


def test_plaintext_cli_withholds_a_tampered_body(bus, capsys):
    sender, reader = bus.client('beta'), bus.client('gamma')
    sender.post('gamma', 'signed original')
    env = reader.inbox()[0]
    env['body'] = 'forged body'
    _show(reader, [env], False, reader.registry())
    shown = capsys.readouterr().out
    assert 'forged body' not in shown
    assert '<unverifiable plaintext body>' in shown


def test_signed_plaintext_still_renders(bus, capsys):
    bus.client('beta').post('gamma', 'signed original')
    reader = bus.client('gamma')
    _show(reader, reader.inbox(), False, reader.registry())
    assert 'signed original' in capsys.readouterr().out


def test_json_is_explicit_raw_evidence_even_for_a_bad_signature(bus, capsys):
    bus.client('beta').post('gamma', 'original')
    reader = bus.client('gamma')
    env = reader.inbox()[0]
    env['body'] = 'raw forensic evidence'
    _show(reader, [env], True)
    assert 'raw forensic evidence' in capsys.readouterr().out
