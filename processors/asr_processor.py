"""
processors/asr_processor.py - ASR识别层
支持热词注入
"""
import sys
import os
import time
import re
import numpy as np
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from interfaces import IASRProcessor, TextResult, SpeechSegment
from logger import get_logger
from processors._model_path import resolve_model_path

logger = get_logger(__name__, "ASR")


class SenseVoiceASR(IASRProcessor):
    """SenseVoice 语音识别"""

    def __init__(self, model_name: str = "iic/SenseVoiceSmall", device: str = "cuda"):
        self.model_name = model_name
        self.device = device
        self.model = None
        self._hotwords = []
        self._sample_rate = 16000

    def load_model(self) -> None:
        """加载模型"""
        if self.model is None:
            model_path = resolve_model_path(self.model_name)
            logger.info(f"Loading model: {model_path}")
            from funasr import AutoModel
            self.model = AutoModel(
                model=model_path,
                disable_update=True,
                ncpu=4,
            )
            logger.info("Model loaded")

    def recognize(self, segment: SpeechSegment) -> TextResult:
        """识别单个语音段落"""
        if self.model is None:
            self.load_model()

        try:
            result = self.model.generate(
                input=segment.audio,
                batch_size_s=300,
                hotwords=self._hotwords if self._hotwords else None,
            )

            if result:
                text = result[0].get("text", "")
                text = re.sub(r'<\|[^|]*\|>', '', text).strip()

                # 过滤非语音内容
                text = self.filter_noise(text)

                if text:
                    return TextResult(
                        text=text,
                        timestamp=segment.start_time,
                        confidence=1.0,
                    )

        except Exception as e:
            logger.error(f"Recognition error: {e}")

        return TextResult(text="", timestamp=segment.start_time)

    def filter_noise(self, text: str) -> str:
        """过滤非语音内容（键盘声、打字声等）"""
        if not text:
            return ""

        # 清理空白
        text = text.strip()
        if not text:
            return ""

        # 过滤纯标点符号
        if re.match(r'^[\.\,\!\?\。\，\！\？\s]+$', text):
            return ""

        # 过滤单字
        if len(text) <= 1:
            return ""

        # 过滤纯数字
        if re.match(r'^[\d\s\.\,\-]+$', text):
            return ""

        # 过滤纯中文短词（少于3个字）
        if re.match(r'^[一-鿿]+$', text) and len(text) < 3:
            return ""

        # 过滤短英文（少于2个字母）
        # 检查是否全是字母/空格/标点（英文模式）
        if re.match(r'^[a-zA-Z\s\.\,\!\?]+$', text):
            # 提取纯字母计算长度
            letters = re.sub(r'[^a-zA-Z]', '', text)
            if len(letters) < 2:
                return ""

        return text

    def recognize_batch(self, segments: List[SpeechSegment]) -> List[TextResult]:
        """批量识别"""
        results = []
        for seg in segments:
            results.append(self.recognize(seg))
        return results

    def set_hotwords(self, hotwords: List[str]) -> None:
        """设置热词"""
        self._hotwords = hotwords
        logger.info(f"Hotwords updated: {len(hotwords)} words")

    def get_sample_rate(self) -> int:
        return self._sample_rate


class ParaformerASR(IASRProcessor):
    """Paraformer 语音识别"""

    def __init__(self, model_name: str = "iic/paraformer-zh", device: str = "cuda"):
        self.model_name = model_name
        self.device = device
        self.model = None
        self._hotwords = []

    def load_model(self) -> None:
        if self.model is None:
            model_path = resolve_model_path(self.model_name)
            logger.info(f"Loading model: {model_path}")
            from funasr import AutoModel
            self.model = AutoModel(
                model=model_path,
                disable_update=True,
                ncpu=4,
            )
            logger.info("Model loaded")

    def recognize(self, segment: SpeechSegment) -> TextResult:
        if self.model is None:
            self.load_model()

        try:
            result = self.model.generate(
                input=segment.audio,
                hotwords=self._hotwords if self._hotwords else None,
            )

            if result:
                text = result[0].get("text", "")
                return TextResult(text=text, timestamp=segment.start_time)

        except Exception as e:
            logger.error(f"Recognition error: {e}")

        return TextResult(text="", timestamp=segment.start_time)

    def recognize_batch(self, segments: List[SpeechSegment]) -> List[TextResult]:
        return [self.recognize(seg) for seg in segments]

    def set_hotwords(self, hotwords: List[str]) -> None:
        self._hotwords = hotwords


def create_asr_processor(model_name: str, device: str = "cuda") -> IASRProcessor:
    """工厂函数"""
    if "sensevoice" in model_name.lower():
        return SenseVoiceASR(model_name, device)
    elif "paraformer" in model_name.lower():
        return ParaformerASR(model_name, device)
    else:
        return SenseVoiceASR(model_name, device)
