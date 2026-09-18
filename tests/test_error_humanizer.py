import httpx
import anthropic
from harness.error_humanizer import humanize_anthropic_error


def test_humanizer_translates_credit_balance_exhaustion():
    req = httpx.Request('POST', 'https://api.anthropic.com/v1/messages')
    resp = httpx.Response(400, request=req, json={
        'type': 'error',
        'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'},
    })
    exc = anthropic.BadRequestError(message='err', response=resp, body=resp.json())
    msg = humanize_anthropic_error(exc)
    assert msg is not None
    assert 'credit balance exhausted' in msg.lower()
    assert 'console.anthropic.com/settings/plans' in msg
    assert '/compact' not in msg


def test_humanizer_translates_standard_bad_request():
    req = httpx.Request('POST', 'https://api.anthropic.com/v1/messages')
    resp = httpx.Response(400, request=req, json={
        'type': 'error',
        'error': {'type': 'invalid_request_error', 'message': 'prompt is too long: 250000 tokens > 200000 maximum'},
    })
    exc = anthropic.BadRequestError(message='err', response=resp, body=resp.json())
    msg = humanize_anthropic_error(exc)
    assert msg is not None
    assert 'malformed' in msg
    assert '/compact' in msg
