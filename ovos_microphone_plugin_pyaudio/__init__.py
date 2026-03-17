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
import re
from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import Thread
from typing import Optional

import pyaudio
from ovos_config import Configuration
from ovos_plugin_manager.templates.microphone import Microphone
from ovos_utils.log import LOG

try:
    import audioop
except ImportError:
    audioop = None


@dataclass
class PyAudioMicrophone(Microphone):
    """Microphone plugin backed by PyAudio (PortAudio).

    Supports int16, int32, and float32 capture without any dependency on
    ``speech_recognition``.  Set *float32_output* to ``True`` when using
    with consumers that require IEEE 754 float32 bytes (e.g. ggwave).

    Args:
        device: Device name, regex pattern, or ``"default"``.
        period_size: Frames per read call (internal buffer granularity).
        timeout: Seconds to block in :meth:`read_chunk` before returning
            ``None``.
        multiplier: Gain factor applied to each chunk.  Ignored when
            *float32_output* is ``True`` (audioop does not support float32).
        float32_output: When ``True``, opens the stream with
            ``pyaudio.paFloat32`` and delivers raw float32 bytes.
        muted: If ``True``, enqueue silence instead of captured audio.
    """

    device: str = field(default_factory=lambda: Configuration().get("listener", {}).get("device") or "default")
    period_size: int = 1024
    timeout: float = 5.0
    multiplier: float = 1.0
    float32_output: bool = False
    muted: bool = False

    _thread: Optional[Thread] = field(default=None, init=False, repr=False)
    _queue: "Queue[Optional[bytes]]" = field(default_factory=Queue, init=False, repr=False)
    _is_running: bool = field(default=False, init=False, repr=False)
    _chunk_buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)

    # ------------------------------------------------------------------
    # Device discovery
    # ------------------------------------------------------------------

    @staticmethod
    def list_input_devices():
        """Return ``(index, info_dict)`` pairs for all input-capable devices."""
        pa = pyaudio.PyAudio()
        try:
            return [
                (i, pa.get_device_info_by_index(i))
                for i in range(pa.get_device_count())
                if pa.get_device_info_by_index(i)["maxInputChannels"] > 0
            ]
        finally:
            pa.terminate()

    @classmethod
    def find_input_device(cls, device_name: str) -> Optional[int]:
        """Return the device index for *device_name*, or ``None`` for default.

        Matching order: exact name → substring → regex.

        Args:
            device_name: Device name, regex pattern, or ``"default"``.

        Returns:
            Device index, or ``None`` if not found / default requested.
        """
        if not device_name or device_name.lower() == "default":
            return None
        if str(device_name).isdigit():
            return int(device_name)

        devices = cls.list_input_devices()
        lowered = device_name.lower()
        LOG.debug("Searching for input device: %s", device_name)

        for idx, dev in devices:
            if dev["name"].lower() == lowered:
                return idx
        for idx, dev in devices:
            if lowered in dev["name"].lower():
                return idx

        try:
            pattern = re.compile(device_name, re.IGNORECASE)
            for idx, dev in devices:
                if pattern.search(dev["name"]):
                    return idx
        except re.error:
            LOG.warning("Invalid device regex: %s", device_name)

        LOG.warning("Input device '%s' not found, using default", device_name)
        return None

    # ------------------------------------------------------------------
    # Format helpers
    # ------------------------------------------------------------------

    def _pyaudio_format(self) -> int:
        """Return the PyAudio format constant for the current configuration.

        When *float32_output* is ``True`` returns ``pyaudio.paFloat32``
        regardless of *sample_width*.  Otherwise maps *sample_width* (bytes)
        to the matching integer PCM format.
        """
        if self.float32_output:
            return pyaudio.paFloat32
        mapping = {
            1: pyaudio.paInt8,
            2: pyaudio.paInt16,
            3: pyaudio.paInt24,
            4: pyaudio.paInt32,
        }
        fmt = mapping.get(self.sample_width)
        if fmt is None:
            raise ValueError(f"Unsupported sample_width: {self.sample_width}")
        return fmt

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the audio stream and start the capture thread."""
        assert self._thread is None, "Already started"
        self._is_running = True
        self._chunk_buffer.clear()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def read_chunk(self) -> Optional[bytes]:
        """Return one chunk of audio bytes, or ``None`` on timeout."""
        assert self._is_running, "Not running"
        try:
            return self._queue.get(timeout=self.timeout)
        except Empty:
            return None

    def stop(self) -> None:
        """Stop capture and join the background thread."""
        assert self._thread is not None, "Not started"
        self._is_running = False
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(None)
        self._thread.join()
        self._thread = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _enqueue(self, in_data: bytes) -> None:
        """Apply gain, buffer *in_data*, and enqueue complete chunks."""
        if self.muted:
            in_data = bytes(len(in_data))

        if self.multiplier != 1.0 and not self.float32_output and audioop is not None:
            in_data = audioop.mul(in_data, self.sample_width, self.multiplier)

        self._chunk_buffer.extend(in_data)
        while len(self._chunk_buffer) >= self.chunk_size:
            self._queue.put_nowait(bytes(self._chunk_buffer[: self.chunk_size]))
            del self._chunk_buffer[: self.chunk_size]

    def _run(self) -> None:
        """Capture thread: open stream, read blocks, enqueue chunks."""
        pa = pyaudio.PyAudio()
        stream = None
        try:
            device_index = self.find_input_device(self.device)
            fmt = self._pyaudio_format()
            LOG.debug(
                "Opening PyAudio microphone (device=%s, rate=%s, channels=%s, "
                "sample_width=%s, float32=%s)",
                self.device,
                self.sample_rate,
                self.sample_channels,
                self.sample_width,
                self.float32_output,
            )
            stream = pa.open(
                format=fmt,
                channels=self.sample_channels,
                rate=self.sample_rate,
                frames_per_buffer=self.period_size,
                input_device_index=device_index,
                input=True,
            )
            stream.start_stream()
            while self._is_running:
                data = stream.read(self.period_size, exception_on_overflow=False)
                self._enqueue(data)
        except Exception:
            LOG.exception("PyAudio microphone error")
        finally:
            if stream is not None:
                stream.stop_stream()
                stream.close()
            pa.terminate()
            self._is_running = False
