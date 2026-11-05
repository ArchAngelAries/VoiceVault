import asyncio
import base64
import json
from threading import Event, Lock, RLock, Thread
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from api import tts, tts_stream
from engines.base import EngineError, ModelInfo, ModelState, SynthesisCancelled, SynthesisRequest, SynthesisResult
from engines.manager import EngineManager


def test_pcm_chunks_are_little_endian_and_reject_invalid_samples():
    event = tts_stream.pcm_event(np.array([-1., 0., 1.]), 24000)
    assert np.frombuffer(base64.b64decode(event['pcm_s16le']), dtype='<i2').tolist() == [-32767, 0, 32767]
    with pytest.raises(EngineError):
        tts_stream.pcm_event(np.array([np.nan]), 24000)


def fixture_services(monkeypatch, tmp_path, manager):
    info = SimpleNamespace(id='fake:voice', engine_id='fake', state=ModelState.INTEGRATED, family='Fixture')
    manager._engines = {'fake': SimpleNamespace(capabilities=SimpleNamespace(streaming=True))}
    monkeypatch.setattr(tts, '_get_services', lambda: (SimpleNamespace(get=lambda _: info), manager))
    monkeypatch.setattr(tts, '_outputs_dir', lambda: tmp_path)


@pytest.mark.asyncio
async def test_stream_emits_audio_before_complete_record(monkeypatch, tmp_path):
    class Manager:
        def synthesize_stream(self, _, request, emit, cancel, *args):
            emit(np.array([.2, -.2]), 24000)
            return SynthesisResult(np.array([.2, -.2]), 24000, {'mode': 'preset'})
    fixture_services(monkeypatch, tmp_path, Manager())
    request = SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=False))
    response = await tts_stream.stream_speech(request, tts.SynthesizeRequest(model_id='fake:voice', text='Hello'))
    packets = [chunk async for chunk in response.body_iterator]
    assert [chunk.split(b'\n')[0] for chunk in packets] == [b'event: start', b'event: audio', b'event: done']
    assert b'pcm_s16le' in packets[1] and b'/api/tts/audio/' in packets[2]


@pytest.mark.asyncio
async def test_disconnect_cancels_worker_without_saving_partial(monkeypatch, tmp_path):
    stopped = Event()
    class Manager:
        def synthesize_stream(self, _, request, emit, cancel, *args):
            emit(np.array([.2]), 24000)
            assert cancel.wait(2), 'Worker did not receive cancellation'
            stopped.set()
            raise SynthesisCancelled('stopped')
    fixture_services(monkeypatch, tmp_path, Manager())
    disconnected = False
    async def check_disconnect():
        return disconnected
    response = await tts_stream.stream_speech(SimpleNamespace(is_disconnected=check_disconnect),
        tts.SynthesizeRequest(model_id='fake:voice', text='Hello'))
    stream = response.body_iterator
    assert (await anext(stream)).startswith(b'event: start')
    assert (await anext(stream)).startswith(b'event: audio')
    disconnected = True
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert stopped.is_set() and not list(tmp_path.glob('*.wav'))


@pytest.mark.asyncio
async def test_segment_slices_complete_engine_audio_into_bounded_events(monkeypatch, tmp_path):
    samples = np.linspace(-.3, .3, 48001, dtype=np.float32)
    class Manager:
        def synthesize(self, _, request, *args, cancel=None):
            assert cancel is not None and not cancel.is_set()
            return SynthesisResult(samples, 24000, {'mode': 'design'})
    manager = Manager()
    fixture_services(monkeypatch, tmp_path, manager)
    manager._engines['fake'].capabilities.streaming = False
    request = SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=False))
    response = await tts_stream.stream_speech(request,
        tts.SynthesizeRequest(model_id='fake:voice', text='Hello'), allow_complete=True)
    packets = [chunk async for chunk in response.body_iterator]
    starts = [packet for packet in packets if packet.startswith(b'event: start')]
    audio = [packet for packet in packets if packet.startswith(b'event: audio')]
    done = [packet for packet in packets if packet.startswith(b'event: done')]
    assert b'"incremental": false' in starts[0]
    assert len(audio) == 3 and done
    decoded = [json.loads(packet.split(b'data: ', 1)[1]) for packet in audio]
    assert all(item['samples'] <= 24000 for item in decoded)


@pytest.mark.asyncio
async def test_segment_rejects_complete_engine_on_stream_only_route(monkeypatch, tmp_path):
    info = SimpleNamespace(id='fake:voice', engine_id='fake', state=ModelState.INTEGRATED, family='Fixture')
    manager = SimpleNamespace(_engines={'fake': SimpleNamespace(capabilities=SimpleNamespace(streaming=False))})
    monkeypatch.setattr(tts, '_get_services', lambda: (SimpleNamespace(get=lambda _: info), manager))
    with pytest.raises(HTTPException) as exc:
        await tts_stream.stream_speech(SimpleNamespace(),
            tts.SynthesizeRequest(model_id='fake:voice', text='Hello'))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_segment_disconnect_discards_late_complete_result(monkeypatch, tmp_path):
    entered = Event()
    class Manager:
        def synthesize(self, _, speech, *args, cancel=None):
            entered.set()
            assert cancel.wait(2)
            return SynthesisResult(np.array([.3] * 24000), 24000, {'mode': 'design'})
    manager = Manager()
    fixture_services(monkeypatch, tmp_path, manager)
    manager._engines['fake'].capabilities.streaming = False
    disconnected = False
    async def check_disconnect(): return disconnected
    response = await tts_stream.stream_speech(
        SimpleNamespace(is_disconnected=check_disconnect),
        tts.SynthesizeRequest(model_id='fake:voice', text='Hello'), allow_complete=True)
    stream = response.body_iterator
    assert b'"incremental": false' in await anext(stream)
    assert await asyncio.to_thread(entered.wait, 2)
    disconnected = True
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert not list(tmp_path.glob('*.wav'))


def test_manager_cancellation_unloads_and_releases_speech_lock(monkeypatch):
    import asr.service
    monkeypatch.setattr(asr.service, 'unload_transcriber', lambda: None)
    cancel = Event()
    class Engine:
        capabilities = SimpleNamespace(streaming=True, reference_transcript=False)
        display_name = 'Fixture'
        unloaded = 0
        def load(self, *args, **kwargs): pass
        def unload(self): self.unloaded += 1
        def synthesize_stream(self, request, emit, event):
            emit(np.array([.1]), 24000)
            raise SynthesisCancelled('stopped')
    engine = Engine()
    info = SimpleNamespace(id='fake:voice', engine_id='fake', state=ModelState.INTEGRATED, path='fixture')
    manager = EngineManager({'fake': engine}, SimpleNamespace(get=lambda _: info))
    manager._lock = RLock()
    with pytest.raises(SynthesisCancelled):
        manager.synthesize_stream(info.id, SynthesisRequest(text='Hi'), lambda *_: cancel.set(), cancel)
    assert manager.loaded_model_id is None and engine.unloaded == 1
    assert manager._lock.acquire(blocking=False)
    manager._lock.release()


def test_nonstream_manager_cancellation_while_waiting_does_not_load(monkeypatch):
    import asr.service
    monkeypatch.setattr(asr.service, 'unload_transcriber', lambda: None)
    class Engine:
        display_name = 'Fixture'
        capabilities = SimpleNamespace(reference_transcript=False)
        loads = 0
        def load(self, *args, **kwargs): self.loads += 1
        def unload(self): pass
        def synthesize(self, request): raise AssertionError('must not synthesize')
    engine = Engine()
    info = ModelInfo('fake:voice', 'fake', 'Fixture', '', 'fixture', 0, ModelState.INTEGRATED)
    manager = EngineManager({'fake': engine}, SimpleNamespace(get=lambda _: info))
    gate = Lock()
    gate.acquire()
    manager._lock = gate
    cancel = Event()
    outcome = []
    worker = Thread(target=lambda: _capture_cancel(outcome, manager, info.id, cancel))
    worker.start()
    cancel.set()
    worker.join(2)
    gate.release()
    assert not worker.is_alive() and outcome == ['cancelled'] and engine.loads == 0


def test_nonstream_manager_discards_result_if_canceled_during_synthesis(monkeypatch):
    import asr.service
    monkeypatch.setattr(asr.service, 'unload_transcriber', lambda: None)
    entered, release = Event(), Event()
    class Engine:
        display_name = 'Fixture'
        capabilities = SimpleNamespace(reference_transcript=False)
        unloaded = 0
        def load(self, *args, **kwargs): pass
        def unload(self): self.unloaded += 1
        def synthesize(self, request):
            entered.set()
            assert release.wait(2)
            return SynthesisResult(np.array([.3]), 24000, {})
    engine = Engine()
    info = ModelInfo('fake:voice', 'fake', 'Fixture', '', 'fixture', 0, ModelState.INTEGRATED)
    manager = EngineManager({'fake': engine}, SimpleNamespace(get=lambda _: info))
    manager._lock = RLock()
    cancel = Event()
    outcome = []
    worker = Thread(target=lambda: _capture_cancel(outcome, manager, info.id, cancel))
    worker.start()
    assert entered.wait(2)
    cancel.set()
    release.set()
    worker.join(2)
    assert not worker.is_alive() and outcome == ['cancelled'] and engine.unloaded == 1


def _capture_cancel(outcome, manager, model_id, cancel):
    try:
        manager.synthesize(model_id, SynthesisRequest(text='Hi'), 'cpu', 'float32', cancel=cancel)
    except SynthesisCancelled:
        outcome.append('cancelled')
    else:
        outcome.append('returned')


def test_failed_load_releases_partial_model_without_masking_error():
    class Engine:
        allocated = False
        def load(self, *args, **kwargs):
            self.allocated = True
            raise EngineError('checkpoint incompatible')
        def unload(self): self.allocated = False
    engine = Engine()
    manager = EngineManager({'fake': engine}, SimpleNamespace())
    info = SimpleNamespace(id='fake:voice', engine_id='fake', path='fixture')
    with pytest.raises(EngineError, match='checkpoint incompatible'):
        manager._load(info, (info.id, 'cuda', 'bfloat16'))
    assert not engine.allocated and manager.loaded_model_id is None
