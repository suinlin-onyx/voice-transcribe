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
    ASR输出 → preprocess() → PUNC模型 → postprocess() → 发送客户端

    功能:
    - 筛选: 噪声过滤
    - 拼接: 分段文字合并
    - 格式化: 标点、换行
    - 数字修复: 6.3 → 6.3
    - 英文空格修复: p p t → ppt
    """

    def __init__(self):
        self._buffer = ""  # 累积文本
        self._last_output_time = 0.0
        self._SILENCE_TIMEOUT = 2.0  # 静音超时(秒)
        self._MIN_CHUNK = 30  # 最小累积字符
        self._last_process_time = time.time()
        self._segment_count = 0

        # 英文缩写模式 (连续单字母加空格，如 p p t)
        self._english_pattern = re.compile(r'\b([a-zA-Z]\s){2,}[a-zA-Z]\b')
        # 数字模式 (可能是全角数字或带空格)
        self._number_pattern = re.compile(r'[\d]+\s*\.\s*\d+')

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

    def postprocess(self, text: str, current_time: float = None) -> tuple[Optional[str], str]:
        """
        后处理: 决定是否输出以及输出格式

        Args:
            text: 处理后的文本
            current_time: 当前时间戳

        Returns:
            (output_text, status)
            - output_text: 需要输出的文本，None表示不输出
            - status: "punct"/"silence"/"forced"/""
        """
        if current_time is None:
            current_time = time.time()

        if not text:
            return None, ""

        self._buffer += text
        self._last_process_time = current_time

        # 1. 检查标点 - 有标点立即输出
        if re.search(r'[。！？,]', self._buffer):
            output = self._buffer
            self._buffer = ""
            self._segment_count += 1
            return output, "punct"

        # 2. 检查静音超时 - 无标点但超时
        if current_time - self._last_process_time > self._SILENCE_TIMEOUT:
            if self._buffer:
                output = self._buffer
                self._buffer = ""
                self._segment_count += 1
                return output, "silence"

        # 3. 检查强制输出 - 累积太长
        if len(self._buffer) >= self._MIN_CHUNK * 2:
            output = self._buffer
            self._buffer = ""
            self._segment_count += 1
            return output, "forced"

        return None, ""

    def _fix_english_spaces(self, text: str) -> str:
        """
        修复英文单词之间的多余空格

        例如: p p t → ppt, H e l l o → Hello
        """
        def replace_func(match):
            # 移除空格，保留字母
            letters = match.group().replace(' ', '')
            return letters

        return self._english_pattern.sub(replace_func, text)

    def _fix_number_format(self, text: str) -> str:
        """
        修复数字格式

        例如: 6 . 3 → 6.3, １.５ → 1.5
        """
        # 全角数字转半角
        text = text.replace('１', '1').replace('２', '2').replace('３', '3')
        text = text.replace('４', '4').replace('５', '5').replace('６', '6')
        text = text.replace('７', '7').replace('８', '8').replace('９', '9').replace('０', '0')

        # 修复数字间多余空格 6 . 3 → 6.3
        text = re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', text)

        return text

    def reload(self):
        """
        热重载配置 - 重新加载配置并重置状态

        用于在不重启服务的情况下更新处理逻辑
        """
        # 重新初始化内部状态
        self._buffer = ""
        self._last_output_time = 0.0
        self._last_process_time = time.time()
        self._segment_count = 0

        # 重新编译正则 (如果有外部配置，可以重新加载)
        self._english_pattern = re.compile(r'\b([a-zA-Z]\s){2,}[a-zA-Z]\b')
        self._number_pattern = re.compile(r'[\d]+\s*\.\s*\d+')

        print("[TextProcessor] reloaded")

    def reset(self):
        """
        重置内部状态 - 清空缓冲区

        用于切换场景或清理状态
        """
        self._buffer = ""
        self._last_output_time = 0.0
        self._last_process_time = time.time()

    def get_status(self) -> dict:
        """获取处理器状态"""
        return {
            "buffer_len": len(self._buffer),
            "segment_count": self._segment_count,
            "last_process_ago": time.time() - self._last_process_time,
        }

    def clear_buffer(self):
        """强制清空缓冲区，返回已累积的文本"""
        output = self._buffer
        self._buffer = ""
        return output


def create_text_processor() -> TextProcessor:
    """工厂函数"""
    return TextProcessor()
