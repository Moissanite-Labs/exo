import asyncio

import pytest

from exo.core.context import DefaultCompressor, SummaryError
from exo.core.models.fake import FakeProvider
from exo.core.types import Message, ProviderEvent


def run(provider, *, retries=0):
    return asyncio.run(DefaultCompressor(provider, retries=retries, retry_delay=0).summarize('original history'))


def test_partial_text_before_terminal_error_is_not_summary():
    provider = FakeProvider([[ProviderEvent(kind='text', text='partial'),
                              ProviderEvent(kind='error', error='refused', retryable=False)]])
    with pytest.raises(SummaryError, match='refused') as caught:
        run(provider)
    assert caught.value.retryable is False
    assert len(provider.calls) == 1


def test_retry_discards_partial_text_and_commits_only_successful_attempt():
    provider = FakeProvider([[ProviderEvent(kind='text', text='partial'),
                              ProviderEvent(kind='error', error='offline', retryable=True)], 'complete'])
    assert run(provider, retries=1) == 'complete'
    assert len(provider.calls) == 2


def test_stream_without_done_is_not_summary():
    with pytest.raises(SummaryError, match='completion'):
        run(FakeProvider([[ProviderEvent(kind='text', text='partial')]]))


@pytest.mark.parametrize('reason', ['length', 'tool_use', 'interrupted'])
def test_incomplete_done_is_not_summary(reason):
    with pytest.raises(SummaryError, match='completion'):
        run(FakeProvider([[ProviderEvent(kind='text', text='partial'),
                           ProviderEvent(kind='done', stop_reason=reason)]]))


def test_error_followed_by_done_cannot_erase_failure():
    with pytest.raises(SummaryError, match='refused'):
        run(FakeProvider([[ProviderEvent(kind='text', text='partial'),
                           ProviderEvent(kind='error', error='refused', retryable=False),
                           ProviderEvent(kind='done', stop_reason='end')]]))


def test_normal_complete_summary_and_legacy_done_are_accepted():
    assert run(FakeProvider(['complete'])) == 'complete'
    assert run(FakeProvider([[ProviderEvent(kind='done', message=Message.assistant('assembled'))]])) == 'assembled'
