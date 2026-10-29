"""A second writer's node must not be mistaken for a new consolidation target (#54)."""
import pytest

from exo import consolidate as C
from exo.ledger import Ledger


def external_node(store, nid):
    sample = store.nodes_dir / 'mind/communication-style.md'
    text = sample.read_text().replace('id: mind/communication-style', f'id: {nid}')
    path = store.nodes_dir / f'{nid}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path, text


def test_new_run_observes_nodes_written_after_store_cache(template_store, signer):
    store = template_store
    store.reload()
    with Ledger(store.ledger_path, signer) as ledger:
        run = C._Run(store, ledger, None, None, None, False)
        nid = run.new_node_id('mind', 'unusual imported preference', 'p-collision')
        external_node(store, nid)
        fresh = C._Run(store, ledger, None, None, None, False)
        assert fresh.new_node_id('mind', 'unusual imported preference', 'p-collision') != nid


def test_accept_refuses_a_create_target_taken_after_proposal(template_store, signer):
    store = template_store
    with Ledger(store.ledger_path, signer) as ledger:
        event = ledger.append('ruling', {'text': 'From now on, prefer compact reproducible evidence.'}, 'principal')
        rows = C.propose_typed(store, ledger, [{'type': 'fact', 'claim': 'Prefer compact reproducible evidence',
                                              'evidence': [event['id']]}])
        p = C.load_state(ledger).proposals[rows[0]['id']]
        assert p.action == 'create'
        path, text = external_node(store, p.node_id)
        with pytest.raises(C.ConsolidateError, match='already exists'):
            C.proposal_accept(store, ledger, p.id)
        assert path.read_text() == text
        assert not list(ledger.iter(kind='belief_accepted'))
