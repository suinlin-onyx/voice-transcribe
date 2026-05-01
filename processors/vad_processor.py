"""
processors/vad_processor.py - VAD语音检测层
支持 SenseVoice 内置VAD 和独立 FSMN-VAD
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

logger = get_logger(__name__, "VAD")


@dataclass
class VADConfig:
    """VAD配置"""
    # 模式: sensevoice | fsmn_vad | simple
    mode: str = "sensevoice"

    # 检测阈值
    threshold: float = 0.5

    # 最小语音时长 (秒)
    min_speech_duration: float = 0.3

    # 最大语音时长 (秒)
    max_speech_duration: float = 5.0

    # 静音超时 (秒) - 用于触发输出 (ASR需要约2s最小音频)
    silence_timeout: float = 4.0

    # 采样率
    sample_rate: int = 16000


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

        # 添加到缓冲区
        self._buffer = np.concatenate([self._buffer, audio])
        self._sample_rate = getattr(self.asr_model, 'sample_rate', 16000)

        min_samples = int(self.config.min_speech_duration * self._sample_rate)
        max_samples = int(self.config.max_speech_duration * self._sample_rate)

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

                if self._is_valid_speech(text):
                    if not self._in_speech:
                        # 开始新语音
                        self._in_speech = True
                        self._speech_start_sample = current_sample_pos
                        self._speech_buffer = chunk.copy()
                        logger.debug(f"VAD: speech started, text='{text[:30]}'")
                    else:
                        self._speech_buffer = np.concatenate([self._speech_buffer, chunk])
                        logger.debug(f"VAD: speech continued, buf={len(self._speech_buffer)/self._sample_rate:.2f}s")

                    self._last_speech_sample = self._total_samples_processed

                    # 检查是否超时
                    speech_dur = len(self._speech_buffer) / self._sample_rate
                    if speech_dur > self.config.max_speech_duration:
                        logger.debug(f"VAD: max duration reached, output {speech_dur:.2f}s")
                        segment = SpeechSegment(
                            audio=self._speech_buffer.copy(),
                            start_time=0,
                            end_time=speech_dur,
                            is_final=True,
                        )
                        segments.append(segment)
                        self._in_speech = False
                        self._speech_buffer = np.array([], dtype=np.float32)

            except Exception as e:
                logger.error(f"VAD processing error: {e}")

        # 检查静音超时 - 基于音频位置而非时间
        if self._in_speech and self._last_speech_sample > 0:
            samples_since_last_speech = self._total_samples_processed - self._last_speech_sample
            silence_dur_samples = samples_since_last_speech
            silence_dur_sec = silence_dur_samples / self._sample_rate

            if silence_dur_sec > self.config.silence_timeout:
                speech_dur = len(self._speech_buffer) / self._sample_rate
                logger.debug(f"VAD: silence timeout ({silence_dur_sec:.2f}s), output {speech_dur:.2f}s")
                if speech_dur >= self.config.min_speech_duration:
                    segment = SpeechSegment(
                        audio=self._speech_buffer.copy(),
                        start_time=0,
                        end_time=speech_dur,
                        is_final=True,
                    )
                    segments.append(segment)
                self._in_speech = False
                self._speech_buffer = np.array([], dtype=np.float32)

        return segments

    def reset(self) -> None:
        self._buffer = np.array([], dtype=np.float32)
        self._speech_buffer = np.array([], dtype=np.float32)
        self._in_speech = False
        self._speech_start_sample = 0
        self._last_speech_sample = 0
        self._total_samples_processed = 0


class FSMNVAD(IVADProcessor):
    """独立 FSMN-VAD"""

    def __init__(self, config: VADConfig = None):
        self.config = config or VADConfig()
        self._vad_model = None
        self._buffer = np.array([], dtype=np.float32)
        self._speech_segments = []
        self._sample_rate = self.config.sample_rate

    def load_model(self):
        """加载 VAD 模型"""
        if self._vad_model is None:
            from funasr import AutoModel
            self._vad_model = AutoModel(
                model="fsmn-vad",
                disable_update=True,
                ncpu=4,
            )
            logger.info("FSMN-VAD model loaded")

    def process(self, audio: np.ndarray) -> List[SpeechSegment]:
        if self._vad_model is None:
            self.load_model()

        self._buffer = np.concatenate([self._buffer, audio])
        segments = []

        # 批量检测
        if len(self._buffer) >= self._sample_rate:  # 至少1秒
            try:
                result = self._vad_model.generate(
                    input=self._buffer,
                    batch_size_s=300,
                )

                if result and "segments" in result[0]:
                    for seg in result[0]["segments"]:
                        if seg["offset"] + seg["duration"] <= len(self._buffer) / self._sample_rate:
                            segment = SpeechSegment(
                                audio=self._buffer[
                                    int(seg["offset"] * self._sample_rate):
                                    int((seg["offset"] + seg["duration"]) * self._sample_rate)
                                ],
                                start_time=seg["offset"],
                                end_time=seg["offset"] + seg["duration"],
                                is_final=True,
                            )
                            segments.append(segment)

                    # 保留未使用的音频
                    if segments:
                        last_seg = segments[-1]
                        keep_start = int(last_seg.end_time * self._sample_rate)
                        self._buffer = self._buffer[keep_start:]

            except Exception as e:
                logger.error(f"VAD processing error: {e}")

        return segments

    def reset(self) -> None:
        self._buffer = np.array([], dtype=np.float32)
        self._speech_segments = []


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


def create_vad_processor(mode: str, asr_model=None, config: VADConfig = None) -> IVADProcessor:
    """工厂函数"""
    if mode == "sensevoice":
        return SenseVoiceVAD(asr_model, config)
    elif mode == "fsmn_vad":
        return FSMNVAD(config)
    elif mode == "simple":
        return SimpleVAD(config)
    else:
        raise ValueError(f"Unknown VAD mode: {mode}")
