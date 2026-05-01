"""
text_processor.py - 文本处理模块
负责文本的筛选、拼接、格式化、数字修复、英文空格修复等
"""
import re
import time
from typing import Optional


class TextProcessor:
    """
    文本处理器

    处理流程:
    ASR输出 → append(text) → tick() 每3秒 → 发送客户端

    输出逻辑:
    - 停顿2秒 + 有标点 → 换行 + 时间头
    - 未停顿2秒 + 有标点 → 连续输出
    - 未停顿2秒 + 无标点 + 累积>6秒 → 整段输出
    - 未停顿2秒 + 无标点 + 累积<=6秒 → 继续累积
    """

    # 标点符号
    PUNCTUATION = r'[。！？，,.!?]'

    def __init__(self):
        self._buffer = ""  # 累积文本
        self._last_speech_time = time.time()  # 最后有语音输入的时间
        self._CHECK_INTERVAL = 3.0  # 检测间隔 3秒
        self._SILENCE_THRESHOLD = 2.0  # 静默阈值 2秒
        self._MAX_ACCUMULATE = 6.0  # 最大累积时间 6秒
        self._segment_count = 0

        # 英文缩写模式
        self._english_pattern = re.compile(r'\b([a-zA-Z])\s+([a-zA-Z])\s+([a-zA-Z])\b')
        # 数字模式
        self._number_pattern = re.compile(r'[\d]+\s*\.\s*\d+')

    def append(self, text: str):
        """
        添加新的识别文本

        Args:
            text: ASR 识别后的文本（已加标点）
        """
        if not text:
            return
        self._buffer += text
        self._last_speech_time = time.time()

    def tick(self, current_time: float = None) -> tuple[Optional[str], str]:
        """
        定时调用（每3秒），检查是否需要输出

        Returns:
            (output_text, status)
            - output_text: 要输出的文本，None表示不需要输出
            - status: "newline" / "continuous" / ""（无输出）
        """
        if current_time is None:
            current_time = time.time()

        if not self._buffer:
            return None, ""

        # 查找最后一个标点
        last_punct_pos = self._find_last_punct(self._buffer)
        has_punct = last_punct_pos >= 0

        # 计算静音时间（从上次有语音到现在）
        silence_time = current_time - self._last_speech_time

        # 情况1: 停顿2秒 + 有标点 → 换行 + 时间头
        if silence_time >= self._SILENCE_THRESHOLD and has_punct:
            # 找到最后一个标点之前的文本
            output = self._buffer[:last_punct_pos + 1]
            # 剩余文本继续累积
            self._buffer = self._buffer[last_punct_pos + 1:]
            self._segment_count += 1
            return output, "newline"

        # 情况2: 未停顿2秒 + 有标点 → 连续输出
        if has_punct:
            # 输出到最后一个标点
            output = self._buffer[:last_punct_pos + 1]
            # 剩余文本继续累积
            self._buffer = self._buffer[last_punct_pos + 1:]
            self._segment_count += 1
            return output, "continuous"

        # 计算累积时间
        accumulate_time = current_time - self._last_speech_time

        # 情况3: 未停顿2秒 + 无标点 + 累积>6秒 → 整段输出
        if accumulate_time >= self._MAX_ACCUMULATE:
            output = self._buffer
            self._buffer = ""
            self._segment_count += 1
            return output, "continuous"

        # 情况4: 未停顿2秒 + 无标点 + 累积<=6秒 → 继续累积
        return None, ""

    def preprocess(self, text: str) -> str:
        """
        预处理: 清理原始识别结果

        - 去除控制字符
        - 修复英文空格问题 (p p t → ppt)
        - 修复数字格式
        """
        if not text:
            return ""

        # 去除控制字符和非打印字符
        text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)

        # 修复英文缩写空格 (p p t → ppt)
        text = self._fix_english_spaces(text)

        # 修复数字格式 (全角数字转半角，如果需要)
        text = self._fix_number_format(text)

        return text.strip()

    def _find_last_punct(self, text: str) -> int:
        """查找最后一个标点的位置"""
        match = re.search(self.PUNCTUATION, text)
        if match:
            return match.end() - 1  # 返回字符位置，不是end索引
        return -1

    def _fix_english_spaces(self, text: str) -> str:
        """修复英文单词之间的多余空格"""
        def replace_func(match):
            letters = match.group().replace(' ', '')
            return letters
        return self._english_pattern.sub(replace_func, text)

    def _fix_number_format(self, text: str) -> str:
        """修复数字格式"""
        # 全角数字转半角
        text = text.replace('１', '1').replace('２', '2').replace('３', '3')
        text = text.replace('４', '4').replace('５', '5').replace('６', '6')
        text = text.replace('７', '7').replace('８', '8').replace('９', '9').replace('０', '0')
        # 修复数字间多余空格
        text = re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', text)
        return text

    def reload(self):
        """热重载配置"""
        self._buffer = ""
        self._last_speech_time = time.time()
        self._segment_count = 0
        self._english_pattern = re.compile(r'\b([a-zA-Z])\s+([a-zA-Z])\s+([a-zA-Z])\b')
        self._number_pattern = re.compile(r'[\d]+\s*\.\s*\d+')
        print("[TextProcessor] reloaded")

    def reset(self):
        """重置内部状态"""
        self._buffer = ""
        self._last_speech_time = time.time()
        self._segment_count = 0

    def get_status(self) -> dict:
        """获取处理器状态"""
        return {
            "buffer_len": len(self._buffer),
            "segment_count": self._segment_count,
            "last_speech_ago": time.time() - self._last_speech_time,
        }

    def clear_buffer(self) -> str:
        """强制清空缓冲区，返回已累积的文本"""
        output = self._buffer
        self._buffer = ""
        return output


def create_text_processor() -> TextProcessor:
    """工厂函数"""
    return TextProcessor()
