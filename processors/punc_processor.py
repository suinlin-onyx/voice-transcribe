"""
processors/punc_processor.py - 标点恢复层
"""
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from interfaces import IPuncProcessor, PunctuatedText
from logger import get_logger
from processors._model_path import resolve_model_path

logger = get_logger(__name__, "PUNC")


class CtPuncProcessor(IPuncProcessor):
    """ct-punc 标点模型"""

    def __init__(self, model_name: str = "ct-punc", revision: str = "v2.0.4"):
        self.model_name = model_name
        self.revision = revision
        self.model = None

    def load_model(self) -> None:
        """加载标点模型"""
        if self.model is None:
            model_path = resolve_model_path(self.model_name)
            logger.info(f"Loading punctuation model: {model_path}...")
            from funasr import AutoModel
            self.model = AutoModel(
                model=model_path,
                model_revision=self.revision,
                disable_update=True,
                ncpu=4,
            )
            logger.info("Punctuation model loaded")

    def punctuate(self, text: str) -> PunctuatedText:
        """添加标点"""
        if not text:
            return PunctuatedText(text=text)

        if self.model is None:
            self.load_model()

        try:
            result = self.model.generate(input=text, batch_size_s=300)
            if result:
                punctuated = result[0].get('text', '')
                if punctuated:
                    return PunctuatedText(text=punctuated)
        except Exception as e:
            logger.error(f"Punctuation error: {e}")

        return PunctuatedText(text=text)


class NoOpPuncProcessor(IPuncProcessor):
    """无操作标点处理器"""

    def load_model(self) -> None:
        pass

    def punctuate(self, text: str) -> PunctuatedText:
        return PunctuatedText(text=text)


def create_punc_processor(model_name: str = "ct-punc", enabled: bool = True) -> IPuncProcessor:
    """工厂函数"""
    if not enabled:
        return NoOpPuncProcessor()

    if model_name == "ct-punc":
        return CtPuncProcessor(model_name)
    else:
        return CtPuncProcessor(model_name)
