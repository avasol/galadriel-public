"""Zero-inference presentation contract; all fixtures synthetic, no live palace."""
import asyncio
import json
from types import SimpleNamespace as NS
from pathlib import Path
import pytest
from harness import response_status as rs

@pytest.fixture(autouse=True)
def isolated_heading(monkeypatch):
    monkeypatch.setattr(rs, 'heading', lambda a: getattr(a, 'heading', 'Synthetic'))
    monkeypatch.delenv('AGENT_CONTEXT_WINDOW', raising=False)


def response(model='actual-model', inp=100, read=30, write=10):
    return NS(model=model, usage=NS(input_tokens=inp, output_tokens=5,
        cache_read_input_tokens=read, cache_creation_input_tokens=write))

class Agent:
    def __init__(self, root, provider='anthropic'):
        self.memory = NS(memory_dir=root/'memory')
        self.provider = NS(name=provider)
        self.model = 'configured-model'
        self.history = []
        self.calls = []

    @rs.with_status
    async def respond(self, text, raw=None, delay=0):
        self.calls.append(json.dumps(self.history))
        await asyncio.sleep(delay)
        rs.record(self, raw or response())
        self.history.append({'role':'assistant','content':text})
        return text

    @rs.with_stream_status
    async def stream(self):
        yield 'token', 'raw text'
        rs.record(self, response())
        self.history.append({'role':'assistant','content':'raw text'})
        yield 'done', 'raw text'


def test_prefix_never_enters_history_api_payload_or_serialization(tmp_path):
    a=Agent(tmp_path)
    reply=asyncio.run(a.respond('answer'))
    assert isinstance(reply, str) and reply == 'answer'
    assert json.dumps(reply) == '"answer"'
    assert rs.present(reply).startswith('🧭 **Synthetic**')
    assert reply.status['context_tokens'] == 140
    asyncio.run(a.respond('next'))
    assert all('🧭' not in call and 'response_status' not in call for call in a.calls)
    assert a.history[0]['content'] == 'answer'
    assert not (tmp_path/'memory').exists()
    assert (tmp_path/'debug'/'response-status.json').exists()

@pytest.mark.parametrize('provider,expected',[('anthropic',140),('openai',140),('gemini',100)])
def test_provider_cache_accounting(tmp_path,provider,expected):
    a=Agent(tmp_path,provider)
    r=asyncio.run(a.respond('answer'))
    assert r.status['context_tokens']==expected
    assert r.status['measured_at']

def test_fallback_reports_actual_response_not_configured_model(tmp_path):
    a=Agent(tmp_path)
    a.provider=NS(name='fallback',_last_used=NS(name='gemini'))
    r=asyncio.run(a.respond('answer',response('gemini-returned-version')))
    assert r.status['model']=='gemini-returned-version'
    assert r.status['context_tokens']==100

def test_unknown_capacity_no_fabricated_percentage(tmp_path):
    a=Agent(tmp_path);a.context_window=200000
    r=asyncio.run(a.respond('answer'))
    assert r.status['context_window'] is None
    assert '%' not in rs.present(r)

def test_explicit_capacity_only_for_matching_model(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_CONTEXT_WINDOW','1000')
    a=Agent(tmp_path)
    r=asyncio.run(a.respond('answer',response(a.model)))
    assert r.status['context_window']==1000
    assert '14.0%' in rs.present(r)
    r2=asyncio.run(a.respond('fallback',response('other')))
    assert r2.status['context_window'] is None

def test_missing_usage_is_unavailable_not_zero_or_previous_turn(tmp_path):
    a=Agent(tmp_path)
    asyncio.run(a.respond('first'))
    raw=response();raw.status_usage_available=False
    r=asyncio.run(a.respond('second',raw))
    assert r.status['context_tokens'] is None
    assert 'Ctx —' in rs.present(r)

def test_concurrent_turns_keep_their_own_model_and_context(tmp_path):
    a=Agent(tmp_path)
    async def run():
        return await asyncio.gather(a.respond('slow',response('slow',10),.02),a.respond('fast',response('fast',90),.001))
    slow,fast=asyncio.run(run())
    assert (slow.status['model'],slow.status['context_tokens'])==('slow',50)
    assert (fast.status['model'],fast.status['context_tokens'])==('fast',130)

def test_stream_tokens_and_stored_content_are_raw_only_final_has_receipt(tmp_path):
    a=Agent(tmp_path)
    async def run():return [x async for x in a.stream()]
    events=asyncio.run(run())
    assert events[0][0]=='status'
    assert events[1]==('token','raw text')
    assert events[2][1].status['model']=='actual-model'
    assert a.history==[{'role':'assistant','content':'raw text'}]

def test_blank_stays_silent_and_unknown_history_stays_unknown(tmp_path):
    assert rs.present('')==''
    assert rs.status_of('old answer',Agent(tmp_path))['model'] is None
    assert 'Ctx —' in rs.present('old answer')

def test_history_receipt_survives_restart_without_touching_memory(tmp_path):
    a=Agent(tmp_path);r=asyncio.run(a.respond('unique answer'))
    b=Agent(tmp_path)
    assert rs.status_of('unique answer',b)==r.status
    assert rs.present('unique answer',b)==rs.present(r)

def test_ambiguous_repeated_text_is_not_misattributed(tmp_path):
    a=Agent(tmp_path)
    asyncio.run(a.respond('same',response('one')))
    asyncio.run(a.respond('same',response('two')))
    assert rs.status_of('same',a)['model'] is None

def test_heading_is_plain_text_not_mentions_or_markup():
    result=rs.strip({'heading':'@everyone\n<script>*x*','model':'`bad`','context_tokens':None})
    assert '@' not in result and '<' not in result and '\n' not in result


def test_agent_entrypoint_is_wrapped_and_recording_is_wired():
    from harness.agent import GaladrielAgent
    assert hasattr(GaladrielAgent.respond,'__wrapped__')
    import inspect
    assert 'record_response_status(self, response)' in inspect.getsource(GaladrielAgent._log_usage)


def test_existing_transports_use_the_common_presenter():
    root=Path(__file__).resolve().parents[1]
    for name in ('tower/app.py','discord_bot/bot.py','harness/scheduler.py'):
        assert 'present(' in (root/name).read_text(),name
    assert '"status": status_of(response)' in (root/'tower/app.py').read_text()
