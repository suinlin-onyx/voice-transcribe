"""
processors/vad_processor.py - VAD语音检测层
支持 SenseVoice 内置VAD、FSMN-VAD、SimpleVAD
"""
import sys
import os
import time
import numpy as np
from typing import List, Optional
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from interfaces import IVADProcessor, SpeechSegment
from logger import get_logger
from processors._model_path import resolve_model_path

logger = get_logger(__name__, "VAD")


@dataclass
class VADConfig:
    """VAD配置"""
    mode: str = "sensevoice"
    min_speech_duration: float = 0.3
    max_speech_duration: float = 4.0
    silence_timeout: float = 2.0
    sample_rate: int = 16000
    pre_roll_duration: float = 0.1
    post_roll_duration: float = 0.1


class SenseVoiceVAD(IVADProcessor):
    """使用 SenseVoice 内置 VAD"""

    def __init__(self, asr_model, config: VADConfig = None):
        self.asr_model = asr_model
        self.config = config or VADConfig()
        self._buffer = np.array([], dtype=np.float32)
        self._speech_buffer = np.array([], dtype=np.float32)
        self._in_speech = False
        self._speech_start_sample = 0
        self._last_speech_sample = 0
        self._sample_rate = self.config.sample_rate
        self._total_samples_processed = 0

        # Pre-roll: 历史缓冲区，用于存储最近收到的音频
        self._history_buffer = np.array([], dtype=np.float32)
        self._history_max_samples = int(self.config.pre_roll_duration * self._sample_rate)

        # Post-roll: 后导缓冲状态
        self._post_roll_waiting = False  # 是否处于后导缓冲等待状态
        self._post_roll_speech_end_sample = 0  # 语音结束时的样本位置
        self._post_roll_max_samples = int(self.config.post_roll_duration * self._sample_rate)

    def _is_valid_speech(self, text: str) -> bool:
        """检查是否是真的语音文本（而非特殊标记）"""
        if not text:
            return False
        import re
        # 过滤特殊标记如 <|en|>, <|EMO_UNKNOWN|>, <|zh|>
        cleaned = re.sub(r'<\|[^|]*\|>', '', text).strip()
        return len(cleaned) > 0

    def process(self, audio: np.ndarray) -> List[SpeechSegment]:
        """处理音频，返回语音段落"""
        segments = []

        # 确保音频是 float32
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # 1. 先把音频加入历史缓冲区（用于 pre-roll）
        self._history_buffer = np.concatenate([self._history_buffer, audio])
        # 保持历史缓冲不超过 pre_roll_duration
        if len(self._history_buffer) > self._history_max_samples:
            self._history_buffer = self._history_buffer[-self._history_max_samples:]

        # 2. 添加到主缓冲区
        self._buffer = np.concatenate([self._buffer, audio])
        self._sample_rate = getattr(self.asr_model, 'sample_rate', 16000)

        # 3. Post-roll 等待状态处理
        if self._post_roll_waiting:
            samples_in_post_roll = self._total_samples_processed - self._post_roll_speech_end_sample
            if samples_in_post_roll >= self._post_roll_max_samples:
                # Post-roll 等待完成，输出 segment
                self._flush_post_roll(segments)
                self._post_roll_waiting = False

        min_samples = int(self.config.min_speech_duration * self._sample_rate)

        while len(self._buffer) >= min_samples:
            # 取一个chunk进行分析
            chunk = self._buffer[:min_samples].astype(np.float32)
            self._buffer = self._buffer[min_samples:]
            current_sample_pos = self._total_samples_processed
            self._total_samples_processed += min_samples

            try:
                # 使用 SenseVoice 检测 (不用 merge_vad, 只检测语音活动)
                result = self.asr_model.generate(
                    input=chunk,
                    batch_size_s=300,
                )

                text = result[0].get("text", "").strip() if result else ""

                # Debug: log raw VAD output for first 10 chunks
                if not hasattr(self, '_vad_debug_count'):
                    self._vad_debug_count = 0
                if self._vad_debug_count < 10:
                    self._vad_debug_count += 1
                    rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
                    logger.info(f"VAD debug #{self._vad_debug_count}: rms={rms:.6f}, text={repr(text[:50])}")

                if self._is_valid_speech(text):
                    # 检测到语音活动
                    if not self._in_speech:
                        # 开始新语音
                        self._in_speech = True
                        self._speech_start_sample = current_sample_pos
                        self._speech_buffer = chunk.copy()
                        logger.debug(f"VAD: speech started, text='{text[:30]}'")
                    else:
                        self._speech_buffer = np.concatenate([self._speech_buffer, chunk])

                    self._last_speech_sample = self._total_samples_processed

                    # 如果之前在 post-roll 等待，现在检测到语音了，取消等待
                    if self._post_roll_waiting:
                        self._post_roll_waiting = False

                    # 检查是否超时（强制截断）
                    speech_dur = len(self._speech_buffer) / self._sample_rate
                    if speech_dur > self.config.max_speech_duration:
                        logger.debug(f"VAD: max duration reached, output {speech_dur:.2f}s")
                        self._output_segment(segments, force_ended=True)

                else:
                    # 检测到静音
                    if self._in_speech:
                        # 语音中断，进入 post-roll 等待
                        self._post_roll_waiting = True
                        self._post_roll_speech_end_sample = self._total_samples_processed

            except Exception as e:
                logger.error(f"VAD processing error: {e}")

        # 检查静音超时 - 基于音频位置而非时间
        if self._in_speech and self._last_speech_sample > 0 and not self._post_roll_waiting:
            samples_since_last_speech = self._total_samples_processed - self._last_speech_sample
            silence_dur_samples = samples_since_last_speech
            silence_dur_sec = silence_dur_samples / self._sample_rate

            if silence_dur_sec > self.config.silence_timeout:
                # 进入 post-roll 等待
                self._post_roll_waiting = True
                self._post_roll_speech_end_sample = self._total_samples_processed
                # 立即检查是否已经满足 post-roll 时长
                if self._post_roll_waiting:
                    samples_in_post_roll = 0
                    if samples_in_post_roll >= self._post_roll_max_samples:
                        self._flush_post_roll(segments)
                        self._post_roll_waiting = False

        return segments

    def _output_segment(self, segments: List[SpeechSegment], force_ended: bool = False) -> None:
        """输出一个 segment（带 pre-roll）"""
        # 获取 pre-roll 音频
        pre_roll_samples = min(self._history_max_samples, len(self._history_buffer))
        pre_roll_audio = self._history_buffer[-pre_roll_samples:] if pre_roll_samples > 0 else np.array([], dtype=np.float32)

        # 组合: pre_roll + speech_buffer
        segment_audio = np.concatenate([pre_roll_audio, self._speech_buffer])
        speech_dur = len(self._speech_buffer) / self._sample_rate
        total_dur = len(segment_audio) / self._sample_rate

        logger.debug(f"VAD: output segment {total_dur:.2f}s (pre_roll={pre_roll_samples/self._sample_rate:.2f}s)")

        segment = SpeechSegment(
            audio=segment_audio,
            start_time=0,
            end_time=total_dur,
            is_final=True,
            force_ended=force_ended,
        )
        segments.append(segment)

        # 消费了历史缓冲区中已使用的部分
        # 保留超出 pre_roll 的历史数据（如果有的话）
        if len(self._history_buffer) > pre_roll_samples:
            self._history_buffer = self._history_buffer[-pre_roll_samples:]
        else:
            self._history_buffer = np.array([], dtype=np.float32)

        self._in_speech = False
        self._speech_buffer = np.array([], dtype=np.float32)

    def _flush_post_roll(self, segments: List[SpeechSegment]) -> None:
        """Post-roll 等待结束后，输出 segment"""
        if len(self._speech_buffer) > 0:
            self._output_segment(segments, force_ended=False)

    def reset(self) -> None:
        self._buffer = np.array([], dtype=np.float32)
        self._speech_buffer = np.array([], dtype=np.float32)
        self._in_speech = False
        self._speech_start_sample = 0
        self._last_speech_sample = 0
        self._total_samples_processed = 0

        # Pre-roll 历史缓冲区
        self._history_buffer = np.array([], dtype=np.float32)

        # Post-roll 状态
        self._post_roll_waiting = False
        self._post_roll_speech_end_sample = 0


class FSMNVAD(IVADProcessor):
    """独立 FSMN-VAD — 离线窗口 + 状态机，合并连续语音段

    - 1s 离线窗口检测（无 cache/chunk_size，每次独立推理）
    - 状态机逻辑与 SenseVoiceVAD 一致
    - 合并连续语音段，避免微小片段导致的重复识别
    """

    def __init__(self, config: VADConfig = None, model_path: str = ""):
        self.config = config or VADConfig()
        self._model_path = model_path
        self._vad_model = None
        self._sample_rate = self.config.sample_rate

        # 音频缓冲
        self._buffer = np.array([], dtype=np.float32)

        # 语音状态跟踪
        self._in_speech = False
        self._speech_buffer = np.array([], dtype=np.float32)
        self._last_speech_sample = 0
        self._total_samples = 0

        # Pre-roll 历史缓冲
        self._history = np.array([], dtype=np.float32)
        self._history_max = int(self.config.pre_roll_duration * self._sample_rate)

        # Post-roll 等待
        self._post_roll_waiting = False
        self._post_roll_end_sample = 0
        self._post_roll_max = int(self.config.post_roll_duration * self._sample_rate)

    def load_model(self):
        if self._vad_model is None:
            from funasr import AutoModel
            vad_model_path = self._model_path or resolve_model_path("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch")
            logger.info(f"Loading FSMN-VAD model: {vad_model_path}")
            self._vad_model = AutoModel(
                model=vad_model_path,
                disable_update=True,
                ncpu=4,
            )
            logger.info("FSMN-VAD model loaded (offline mode, window=1s)")

    def process(self, audio: np.ndarray) -> List[SpeechSegment]:
        if self._vad_model is None:
            self.load_model()

        segments = []

        # Pre-roll history
        self._history = np.concatenate([self._history, audio])
        if len(self._history) > self._history_max:
            self._history = self._history[-self._history_max:]

        # Buffer audio
        self._buffer = np.concatenate([self._buffer, audio])

        # Process in 1s offline windows
        window_samps = self._sample_rate  # 1 second
        while len(self._buffer) >= window_samps:
            chunk = self._buffer[:window_samps]
            self._buffer = self._buffer[window_samps:]
            self._total_samples += len(chunk)

            # Offline VAD classification
            has_speech = self._classify(chunk)

            if has_speech:
                if not self._in_speech:
                    self._in_speech = True
                    self._speech_buffer = np.array([], dtype=np.float32)

                self._speech_buffer = np.concatenate([self._speech_buffer, chunk])
                self._last_speech_sample = self._total_samples

                if self._post_roll_waiting:
                    self._post_roll_waiting = False

                speech_dur = len(self._speech_buffer) / self._sample_rate
                if speech_dur > self.config.max_speech_duration:
                    seg = self._emit(force_ended=True)
                    if seg:
                        segments.append(seg)

            else:
                if self._in_speech:
                    silence_samps = self._total_samples - self._last_speech_sample
                    silence_sec = silence_samps / self._sample_rate

                    if silence_sec > self.config.silence_timeout:
                        if not self._post_roll_waiting:
                            self._post_roll_waiting = True
                            self._post_roll_end_sample = self._total_samples

                if self._post_roll_waiting:
                    post_roll_samps = self._total_samples - self._post_roll_end_sample
                    if post_roll_samps >= self._post_roll_max:
                        seg = self._emit(force_ended=False)
                        if seg:
                            segments.append(seg)
                        self._post_roll_waiting = False

        return segments

    def _classify(self, chunk: np.ndarray) -> bool:
        """Run offline FSMN-VAD on a 1s window. Returns True if speech detected."""
        try:
            result = self._vad_model.generate(input=chunk)
            if result and "value" in result[0]:
                for vad_seg in result[0]["value"]:
                    dur_ms = vad_seg[1] - vad_seg[0]
                    if dur_ms >= self.config.min_speech_duration * 1000:
                        return True
        except Exception as e:
            logger.error(f"VAD classify error: {e}")
        return False

    def _emit(self, force_ended: bool) -> Optional[SpeechSegment]:
        """输出累积的语音段"""
        min_samps = int(self.config.min_speech_duration * self._sample_rate)
        if len(self._speech_buffer) < min_samps:
            self._speech_buffer = np.array([], dtype=np.float32)
            self._in_speech = False
            return None

        # Pre-roll audio
        pre_roll_samps = min(self._history_max, len(self._history))
        pre_roll = self._history[-pre_roll_samps:] if pre_roll_samps > 0 else np.array([], dtype=np.float32)
        audio_out = np.concatenate([pre_roll, self._speech_buffer])
        dur = len(audio_out) / self._sample_rate

        seg = SpeechSegment(
            audio=audio_out,
            start_time=0,
            end_time=dur,
            is_final=True,
            force_ended=force_ended,
        )

        logger.debug(f"FSMN-VAD emit: {dur:.2f}s (force_ended={force_ended})")
        self._speech_buffer = np.array([], dtype=np.float32)
        self._in_speech = False
        self._history = np.array([], dtype=np.float32)
        return seg

    def reset(self) -> None:
        self._buffer = np.array([], dtype=np.float32)
        self._speech_buffer = np.array([], dtype=np.float32)
        self._in_speech = False
        self._last_speech_sample = 0
        self._total_samples = 0
        self._history = np.array([], dtype=np.float32)
        self._post_roll_waiting = False


class SimpleVAD(IVADProcessor):
    """简单能量检测 VAD (无模型)"""

    def __init__(self, config: VADConfig = None):
        self.config = config or VADConfig()
        self._buffer = np.array([], dtype=np.float32)
        self._speech_buffer = np.array([], dtype=np.float32)
        self._in_speech = False
        self._silence_frames = 0
        self._speech_frames = 0
        self._sample_rate = self.config.sample_rate

    def process(self, audio: np.ndarray) -> List[SpeechSegment]:
        self._buffer = np.concatenate([self._buffer, audio])
        segments = []

        frame_size = int(0.03 * self._sample_rate)  # 30ms frame
        energy_threshold = 0.02

        while len(self._buffer) >= frame_size:
            frame = self._buffer[:frame_size]
            self._buffer = self._buffer[frame_size:]

            energy = np.sqrt(np.mean(frame ** 2))

            if energy > energy_threshold:
                # 语音帧
                self._speech_buffer = np.concatenate([self._speech_buffer, frame])
                self._speech_frames += 1
                self._silence_frames = 0
            else:
                # 静音帧
                self._silence_frames += 1

                # 检测语音结束
                if self._in_speech and self._silence_frames >= self.config.silence_timeout * self._sample_rate / frame_size:
                    if len(self._speech_buffer) > 0:
                        segment = SpeechSegment(
                            audio=self._speech_buffer.copy(),
                            start_time=0,
                            end_time=len(self._speech_buffer) / self._sample_rate,
                            is_final=True,
                        )
                        segments.append(segment)
                    self._speech_buffer = np.array([], dtype=np.float32)
                    self._in_speech = False
                    self._speech_frames = 0

            # 开启检测
            if self._speech_frames >= self.config.min_speech_duration * self._sample_rate / frame_size:
                self._in_speech = True

            # 超时检测
            if self._in_speech and self._speech_frames >= self.config.max_speech_duration * self._sample_rate / frame_size:
                segment = SpeechSegment(
                    audio=self._speech_buffer.copy(),
                    start_time=0,
                    end_time=len(self._speech_buffer) / self._sample_rate,
                    is_final=True,
                )
                segments.append(segment)
                self._speech_buffer = np.array([], dtype=np.float32)
                self._in_speech = False
                self._speech_frames = 0

        return segments

    def reset(self) -> None:
        self._buffer = np.array([], dtype=np.float32)
        self._speech_buffer = np.array([], dtype=np.float32)
        self._in_speech = False
        self._silence_frames = 0
        self._speech_frames = 0


def create_vad_processor(mode: str, asr_model=None, config: VADConfig = None, model_path: str = "") -> IVADProcessor:
    """工厂函数"""
    if mode == "sensevoice":
        return SenseVoiceVAD(asr_model, config)
    elif mode == "fsmn_vad":
        return FSMNVAD(config, model_path=model_path)
    elif mode == "simple":
        return SimpleVAD(config)
    else:
        raise ValueError(f"Unknown VAD mode: {mode}")
