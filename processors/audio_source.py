"""
processors/audio_source.py - 音频采集层实现
"""
import sys
import os
import queue
import time
import threading
import numpy as np

# 设置模型缓存
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

from interfaces import IAudioSource, AudioChunk, AudioSourceType
from logger import get_logger

logger = get_logger(__name__, "AUDIO")


class MicrophoneSource(IAudioSource):
    """麦克风音频源"""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        device: int = None,
        chunk_duration: float = 0.1,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self.chunk_duration = chunk_duration
        self.samples_per_chunk = int(sample_rate * chunk_duration)

        self._stream = None
        self._q = queue.Queue(maxsize=100)
        self._running = False
        self._callbacks = []
        self._start_time = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._running:
            return

        import sounddevice as sd

        self._running = True
        self._start_time = time.time()
        self._q = queue.Queue(maxsize=100)

        def callback(indata, frames, time_info, status):
            if status:
                logger.warning(f"Audio status: {status}")
            if self._running:
                audio = indata[:, 0]  # 取第一通道
                chunk = AudioChunk(
                    data=audio,
                    sample_rate=self.sample_rate,
                    timestamp=time.time() - self._start_time,
                )
                try:
                    self._q.put_nowait(chunk)
                except queue.Full:
                    pass  # 丢包处理

        try:
            self._stream = sd.InputStream(
                device=self.device,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='float32',
                blocksize=self.samples_per_chunk,
                callback=callback,
            )
            self._stream.start()
        except Exception as e:
            logger.error(f"Failed to start audio stream: {e}")
            self._running = False
            raise

        # 启动消费线程
        self._consumer_thread = threading.Thread(target=self._consume, daemon=True)
        self._consumer_thread.start()

    def _consume(self):
        """消费音频数据"""
        while self._running:
            try:
                chunk = self._q.get(timeout=0.1)
                with self._lock:
                    for cb in self._callbacks:
                        try:
                            cb(chunk)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")
            except queue.Empty:
                continue

    def stop(self) -> None:
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def is_active(self) -> bool:
        return self._running

    def on_audio(self, callback):
        with self._lock:
            self._callbacks.append(callback)


class MultiSource(IAudioSource):
    """多音频源 - 同时监听多个设备并合并音频流"""

    def __init__(self, sources: list = None):
        self._sources = sources or []
        self._running = False
        self._callbacks = []
        self._lock = threading.Lock()

    def add_source(self, source: IAudioSource) -> None:
        """添加音频源"""
        with self._lock:
            self._sources.append(source)
            if self._running:
                source.on_audio(self._on_audio)
                source.start()

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        for source in self._sources:
            source.on_audio(self._on_audio)
            source.start()

    def _on_audio(self, chunk: AudioChunk) -> None:
        """接收任意源的音频，转发给所有回调"""
        with self._lock:
            for cb in self._callbacks:
                try:
                    cb(chunk)
                except Exception as e:
                    logger.error(f"MultiSource callback error: {e}")

    def stop(self) -> None:
        self._running = False
        for source in self._sources:
            source.stop()

    def is_active(self) -> bool:
        return self._running

    def on_audio(self, callback):
        with self._lock:
            self._callbacks.append(callback)


class FileSource(IAudioSource):
    """音频文件源 (用于测试)"""

    def __init__(self, file_path: str, sample_rate: int = 16000):
        self.file_path = file_path
        self.sample_rate = sample_rate
        self._running = False
        self._callbacks = []

    def start(self) -> None:
        import soundfile as sf

        self._running = True
        data, sr = sf.read(self.file_path, dtype='float32')

        # 转为单通道
        if data.ndim > 1:
            data = data[:, 0]

        # 重采样
        if sr != self.sample_rate:
            import librosa
            data = librosa.resample(data, orig_sr=sr, target_sr=self.sample_rate)

        # 分块发送
        chunk_size = int(self.sample_rate * 0.1)  # 100ms
        total_chunks = len(data) // chunk_size

        def send_chunks():
            for i in range(total_chunks):
                if not self._running:
                    break
                chunk_data = data[i * chunk_size : (i + 1) * chunk_size]
                chunk = AudioChunk(
                    data=chunk_data,
                    sample_rate=self.sample_rate,
                    timestamp=i * 0.1,
                )
                for cb in self._callbacks:
                    cb(chunk)
                time.sleep(0.1)  # 模拟实时

            # 发送结束信号
            for cb in self._callbacks:
                cb(AudioChunk(data=np.array([]), sample_rate=self.sample_rate, timestamp=-1))

        self._thread = threading.Thread(target=send_chunks, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def is_active(self) -> bool:
        return self._running

    def on_audio(self, callback):
        self._callbacks.append(callback)
