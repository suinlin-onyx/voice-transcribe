"""
interfaces/base.py - 基础接口定义
定义各层的抽象接口，确保解耦
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Callable, Any
from enum import Enum
import numpy as np


class AudioSourceType(Enum):
    MICROPHONE = "microphone"
    FILE = "file"
    STREAM = "stream"


@dataclass
class AudioChunk:
    """音频数据块"""
    data: np.ndarray  # float32, shape (samples,)
    sample_rate: int
    timestamp: float  # 相对时间戳


@dataclass
class SpeechSegment:
    """语音段落"""
    audio: np.ndarray
    start_time: float
    end_time: float
    is_final: bool = False


@dataclass
class TextResult:
    """识别结果"""
    text: str
    timestamp: float
    confidence: float = 1.0
    segment_id: Optional[int] = None


@dataclass
class PunctuatedText:
    """带标点的文本"""
    text: str
    timestamps: Optional[List[dict]] = None  # 词级时间戳


@dataclass
class TransportCommand:
    """传输命令"""
    command: str
    args: dict = None


# ============ 接口定义 ============

class IAudioSource(ABC):
    """音频源接口"""

    @abstractmethod
    def start(self) -> None:
        """开始采集"""
        pass

    @abstractmethod
    def stop(self) -> None:
        """停止采集"""
        pass

    @abstractmethod
    def is_active(self) -> bool:
        """是否正在采集"""
        pass

    @abstractmethod
    def on_audio(self, callback: Callable[[AudioChunk], None]) -> None:
        """注册音频回调"""
        pass


class IVADProcessor(ABC):
    """VAD处理接口"""

    @abstractmethod
    def process(self, audio: np.ndarray) -> List[SpeechSegment]:
        """处理音频，返回语音段落"""
        pass

    @abstractmethod
    def reset(self) -> None:
        """重置状态"""
        pass


class IASRProcessor(ABC):
    """ASR处理接口"""

    @abstractmethod
    def recognize(self, segment: SpeechSegment) -> TextResult:
        """识别单个段落"""
        pass

    @abstractmethod
    def recognize_batch(self, segments: List[SpeechSegment]) -> List[TextResult]:
        """批量识别"""
        pass

    @abstractmethod
    def set_hotwords(self, hotwords: List[str]) -> None:
        """设置热词"""
        pass

    @abstractmethod
    def load_model(self) -> None:
        """加载模型"""
        pass


class IPuncProcessor(ABC):
    """标点处理接口"""

    @abstractmethod
    def punctuate(self, text: str) -> PunctuatedText:
        """添加标点"""
        pass

    @abstractmethod
    def load_model(self) -> None:
        """加载模型"""
        pass


class IOutputBuffer(ABC):
    """输出缓冲接口"""

    @abstractmethod
    def push(self, text: str) -> None:
        """添加文本到缓冲区"""
        pass

    @abstractmethod
    def flush(self) -> str:
        """强制刷新，返回缓冲内容"""
        pass

    @abstractmethod
    def on_ack(self, callback: Callable[[], None]) -> None:
        """注册确认回调"""
        pass


class ITransport(ABC):
    """传输层接口"""

    @abstractmethod
    def send(self, data: str) -> bool:
        """发送数据"""
        pass

    @abstractmethod
    def recv(self) -> Optional[TransportCommand]:
        """接收命令"""
        pass

    @abstractmethod
    def close(self) -> None:
        """关闭连接"""
        pass
