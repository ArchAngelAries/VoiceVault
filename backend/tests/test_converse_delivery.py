import asyncio
import json

import pytest

import api.converse as converse
from llm.delivery import DeliveryEnvelope


def test_envelope_split_header_is_validated_and_separate():
    parser = DeliveryEnvelope()
    assert parser.feed('<del') == ''
    assert parser.feed('ivery>{"emotion":"calm",') == ''
    assert parser.feed('"style":"natural"}</delivery>\nHello. *smiles*') == 'Hello. *smiles*'
    assert parser.delivery == {"emotion": "calm", "style": "natural", "expression": None}
    assert not parser.warnings
    assert parser.feed(' Welcome.') == ' Welcome.'


@pytest.mark.parametrize('header', [
    '{"emotion":"secret-command"}',
    '{"expression":"../../file"}',
    '{"speaker_id":"other", "emotion":"calm"}',
    'not json',
])
def test_invalid_control_values_use_saved_fallback_without_leaking(header):
    parser = DeliveryEnvelope({"emotion": "sad"})
    assert parser.feed(f'<delivery>{header}</delivery>Dialogue.') == 'Dialogue.'
    assert parser.delivery['emotion'] == 'sad'
    assert parser.warnings


def test_missing_partial_and_oversized_headers_are_safe():
    plain = DeliveryEnvelope()
    assert plain.feed('Ordinary dialogue.') == 'Ordinary dialogue.'
    assert plain.warnings
    partial = DeliveryEnvelope()
    assert partial.feed('<delivery>{"emotion":') == ''
    assert partial.feed('', final=True) == ''
    assert partial.warnings
    oversized = DeliveryEnvelope()
    with pytest.raises(ValueError, match='limit'):
        oversized.feed('<delivery>' + 'x' * 513)


def test_converse_delivery_event_and_persistence_keep_selected_speaker(tmp_path, monkeypatch):
    monkeypatch.setattr(converse, 'STORE_DIR', tmp_path / 'sessions')
    monkeypatch.setattr(converse, '_active_turns', set())
    captured = []

    async def model(*args):
        captured.append(args[2])
        for chunk in ['<delivery>{"emotion":"angry"', '}</delivery>\n', 'Hello from Bea.']:
            yield chunk

    monkeypatch.setattr(converse, 'stream_chat', model)

    async def run():
        session = await converse.create_session(converse.NewSession(characters=[
            converse.Character(id='primary', profile=converse.Profile(name='Ada')),
            converse.Character(id='bea', profile=converse.Profile(name='Bea', voice_settings=converse.VoiceSettings(
                model_id='voice-bea', delivery={"emotion": "calm"}))),
        ]))
        response = await converse.send_message(session['id'], converse.MessageRequest(
            text='Hi', model='fake', speaker_id='bea', propose_delivery=True))
        events = [packet.decode() async for packet in response.body_iterator]
        saved = await converse.get_session(session['id'])
        return events, saved

    events, saved = asyncio.run(run())
    assert events[0].startswith('event: delivery')
    assert '"speaker_id":"bea"' in events[0]
    assert events[1].startswith('event: delta')
    assert events[-1].startswith('event: done')
    reply = saved['messages'][-1]
    assert reply['speaker_id'] == 'bea'
    assert reply['content'] == 'Hello from Bea.'
    assert reply['delivery']['emotion'] == 'angry'
    assert '<delivery>' not in reply['content']
    assert '<delivery>' in captured[0][0]['content']
    assert saved['characters'][1]['profile']['voice_settings']['delivery']['emotion'] == 'calm'


def test_delivery_preferences_round_trip_and_reject_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(converse, 'STORE_DIR', tmp_path / 'sessions')

    async def run():
        profile = converse.Profile(name='Ada', voice_settings=converse.VoiceSettings(
            model_id='exact-voice', delivery={"emotion": "cheerful", "style": "dramatic"}))
        session = await converse.create_session(converse.NewSession(profile=profile))
        loaded = await converse.get_session(session['id'])
        assert loaded['profile']['voice_settings']['delivery']['style'] == 'dramatic'

    asyncio.run(run())
    with pytest.raises(ValueError):
        converse.VoiceSettings(delivery={"path": "arbitrary command"})
