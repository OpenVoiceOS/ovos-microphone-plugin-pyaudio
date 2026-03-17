# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Unit tests for PyAudioMicrophone.

All tests mock pyaudio entirely so no real audio hardware is required.
"""
import sys
import types
import unittest
from queue import Full, Queue
from threading import Thread
from typing import Optional
from unittest.mock import MagicMock, call, patch

# ---------------------------------------------------------------------------
# Stub pyaudio before the plugin module is imported
# ---------------------------------------------------------------------------
_pa = types.ModuleType("pyaudio")
_pa.PyAudio = MagicMock()
_pa.paFloat32 = 8
_pa.paInt8 = 1
_pa.paInt16 = 2
_pa.paInt24 = 4
_pa.paInt32 = 16
sys.modules.setdefault("pyaudio", _pa)

from ovos_microphone_plugin_pyaudio import PyAudioMicrophone  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mic(**kwargs) -> PyAudioMicrophone:
    """Create a PyAudioMicrophone with sensible test defaults."""
    defaults = dict(
        sample_rate=16000,
        sample_width=2,
        sample_channels=1,
        chunk_size=4096,
        device="default",
    )
    defaults.update(kwargs)
    return PyAudioMicrophone(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPyAudioFormat(unittest.TestCase):
    """_pyaudio_format() mapping."""

    def test_pyaudio_format_int16(self) -> None:
        """sample_width=2, float32_output=False → paInt16."""
        mic = _make_mic(sample_width=2, float32_output=False)
        self.assertEqual(mic._pyaudio_format(), _pa.paInt16)

    def test_pyaudio_format_float32(self) -> None:
        """float32_output=True → paFloat32 regardless of sample_width."""
        mic = _make_mic(sample_width=2, float32_output=True)
        self.assertEqual(mic._pyaudio_format(), _pa.paFloat32)

    def test_pyaudio_format_unsupported_raises(self) -> None:
        """sample_width=5 with float32_output=False → ValueError."""
        mic = _make_mic(sample_width=5, float32_output=False)
        with self.assertRaises(ValueError):
            mic._pyaudio_format()


class TestFindInputDevice(unittest.TestCase):
    """find_input_device() resolution logic."""

    def test_find_input_device_integer_passthrough(self) -> None:
        """Integer device index is returned as-is."""
        self.assertEqual(PyAudioMicrophone.find_input_device(3), 3)

    def test_find_input_device_default_returns_none(self) -> None:
        """'default' resolves to None."""
        self.assertIsNone(PyAudioMicrophone.find_input_device("default"))

    def _device_list(self):
        return [
            (0, {"name": "Built-in Microphone", "maxInputChannels": 2}),
            (1, {"name": "USB Audio Device", "maxInputChannels": 1}),
            (2, {"name": "Webcam Mic", "maxInputChannels": 1}),
        ]

    def test_find_input_device_exact_match(self) -> None:
        """Exact name match (case-insensitive) returns correct index."""
        with patch.object(PyAudioMicrophone, "list_input_devices", return_value=self._device_list()):
            result = PyAudioMicrophone.find_input_device("usb audio device")
        self.assertEqual(result, 1)

    def test_find_input_device_substring_match(self) -> None:
        """Substring match returns first matching device."""
        with patch.object(PyAudioMicrophone, "list_input_devices", return_value=self._device_list()):
            result = PyAudioMicrophone.find_input_device("Webcam")
        self.assertEqual(result, 2)

    def test_find_input_device_regex_match(self) -> None:
        """Regex pattern is matched case-insensitively."""
        with patch.object(PyAudioMicrophone, "list_input_devices", return_value=self._device_list()):
            result = PyAudioMicrophone.find_input_device("built.*mic")
        self.assertEqual(result, 0)


class TestLifecycle(unittest.TestCase):
    """start() / stop() lifecycle guards."""

    def test_start_already_started_raises(self) -> None:
        """Calling start() twice raises RuntimeError."""
        mic = _make_mic()
        # Simulate already-started state
        mic._thread = MagicMock()
        with self.assertRaises(RuntimeError):
            mic.start()


class TestEnqueue(unittest.TestCase):
    """_enqueue() internal buffering logic."""

    def test_enqueue_buffers_and_splits(self) -> None:
        """Bytes larger than chunk_size are split into correct-sized chunks."""
        mic = _make_mic(chunk_size=4)
        # 10 bytes → 2 complete chunks of 4, remainder 2 bytes buffered
        mic._enqueue(bytes(range(10)))
        self.assertEqual(mic._queue.qsize(), 2)
        chunk0 = mic._queue.get_nowait()
        chunk1 = mic._queue.get_nowait()
        self.assertEqual(len(chunk0), 4)
        self.assertEqual(len(chunk1), 4)
        self.assertEqual(len(mic._chunk_buffer), 2)

    def test_enqueue_applies_gain(self) -> None:
        """Gain multiplier is applied via audioop when available."""
        mic = _make_mic(chunk_size=4, multiplier=2.0, float32_output=False)
        fake_audioop = MagicMock()
        fake_audioop.mul.return_value = bytes(4)
        with patch("ovos_microphone_plugin_pyaudio.audioop", fake_audioop):
            mic._enqueue(bytes(4))
        fake_audioop.mul.assert_called_once_with(bytes(4), mic.sample_width, 2.0)

    def test_enqueue_muted_produces_silence(self) -> None:
        """Muted mode replaces audio data with zero bytes."""
        mic = _make_mic(chunk_size=4, muted=True)
        mic._enqueue(bytes([1, 2, 3, 4]))
        chunk = mic._queue.get_nowait()
        self.assertEqual(chunk, bytes(4))

    def test_enqueue_queue_eviction_when_full(self) -> None:
        """When queue is full, oldest chunk is dropped and new chunk is kept."""
        mic = _make_mic(chunk_size=4, queue_maxsize=2)
        # Fill queue to maxsize
        first_chunk = bytes([0xAA] * 4)
        second_chunk = bytes([0xBB] * 4)
        new_chunk = bytes([0xCC] * 4)
        mic._queue.put_nowait(first_chunk)
        mic._queue.put_nowait(second_chunk)
        self.assertTrue(mic._queue.full())

        # Enqueue one more — oldest should be dropped
        mic._enqueue(new_chunk)

        items = []
        while not mic._queue.empty():
            items.append(mic._queue.get_nowait())

        self.assertNotIn(first_chunk, items)
        self.assertIn(new_chunk, items)


class TestRun(unittest.TestCase):
    """_run() thread behaviour."""

    def _make_mock_pa(self, side_effect=None):
        """Return a mock PyAudio instance with a mock stream."""
        mock_stream = MagicMock()
        if side_effect:
            mock_stream.read.side_effect = side_effect
        else:
            mock_stream.read.side_effect = StopIteration("done")

        mock_pa_instance = MagicMock()
        mock_pa_instance.open.return_value = mock_stream
        return mock_pa_instance, mock_stream

    def test_run_opens_stream_with_correct_format(self) -> None:
        """_run() calls pa.open with the correct format constant."""
        mic = _make_mic(sample_width=2, float32_output=False)
        mock_pa_instance, mock_stream = self._make_mock_pa()

        with patch("ovos_microphone_plugin_pyaudio.pyaudio") as mock_pyaudio:
            mock_pyaudio.PyAudio.return_value = mock_pa_instance
            mock_pyaudio.paFloat32 = _pa.paFloat32
            mock_pyaudio.paInt8 = _pa.paInt8
            mock_pyaudio.paInt16 = _pa.paInt16
            mock_pyaudio.paInt24 = _pa.paInt24
            mock_pyaudio.paInt32 = _pa.paInt32
            mic._is_running = True
            mic._run()

        mock_pa_instance.open.assert_called_once()
        call_kwargs = mock_pa_instance.open.call_args[1]
        self.assertEqual(call_kwargs["format"], _pa.paInt16)
        self.assertEqual(call_kwargs["input"], True)

    def test_run_calls_pa_terminate_on_exception(self) -> None:
        """pa.terminate() is called even when stream.read raises."""
        mic = _make_mic()
        mock_pa_instance, mock_stream = self._make_mock_pa(side_effect=RuntimeError("boom"))

        with patch("ovos_microphone_plugin_pyaudio.pyaudio") as mock_pyaudio:
            mock_pyaudio.PyAudio.return_value = mock_pa_instance
            mock_pyaudio.paFloat32 = _pa.paFloat32
            mock_pyaudio.paInt8 = _pa.paInt8
            mock_pyaudio.paInt16 = _pa.paInt16
            mock_pyaudio.paInt24 = _pa.paInt24
            mock_pyaudio.paInt32 = _pa.paInt32
            mic._is_running = True
            mic._run()

        mock_pa_instance.terminate.assert_called_once()


class TestReadChunk(unittest.TestCase):
    """read_chunk() timeout behaviour."""

    def test_read_chunk_returns_none_on_timeout(self) -> None:
        """read_chunk() returns None when no audio arrives within timeout."""
        mic = _make_mic(timeout=0.01)
        mic._is_running = True
        result = mic.read_chunk()
        self.assertIsNone(result)


class TestStop(unittest.TestCase):
    """stop() teardown."""

    def test_stop_clears_buffer_and_joins(self) -> None:
        """stop() drains the queue, sends sentinel, and joins the thread."""
        mic = _make_mic()
        mock_thread = MagicMock()
        mic._thread = mock_thread
        mic._is_running = True

        # Pre-fill queue with a chunk
        mic._queue.put_nowait(bytes(4))

        mic.stop()

        mock_thread.join.assert_called_once()
        self.assertIsNone(mic._thread)
        # Queue should only contain the sentinel None (already consumed by join mock)
        # After stop() the queue was cleared then sentinel was added — thread is mocked
        # so we just verify join was called and _thread is None.


if __name__ == "__main__":
    unittest.main()
