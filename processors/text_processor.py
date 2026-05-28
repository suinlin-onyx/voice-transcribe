"""
text_processor.py - 文本处理模块
负责文本的筛选、拼接、格式化、数字修复、英文空格修复等
"""
import os
import re
import time
import logging
from typing import Optional
from datetime import datetime

# 日志配置 - 与 model_server 保持一致
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else "."
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, f"text_processor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logger = logging.getLogger("text_processor")
logger.setLevel(logging.DEBUG)
logger.handlers = []

# 文件 handler
class UnbufferedFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

file_handler = UnbufferedFileHandler(LOG_FILE, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_format = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
file_handler.setFormatter(file_format)
logger.addHandler(file_handler)

log = logger.info


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

    def __init__(self,
                 punctuation: str = "。！？.!?",
                 split_punctuation: str = "，。！？.!?",
                 silence_threshold: float = 2.0,
                 max_accumulate: float = 6.0,
                 check_interval: float = 3.0,
                 dedup_window: float = 5.0):
        self._buffer = ""  # 累积文本
        self._last_speech_time = time.time()  # 最后有语音输入的时间
        self.interval_time = 0
        self._CHECK_INTERVAL = check_interval  # 检测间隔
        self._SILENCE_THRESHOLD = silence_threshold  # 静默阈值
        self._MAX_ACCUMULATE = max_accumulate  # 最大累积时间
        self._segment_count = 0

        # 待拼接的前缀（截断文本的后半部分）
        self._header = ""

        # 去重: 最近输出记录 (句子 → 时间戳)
        self._recent_outputs: dict[str, float] = {}
        self._DEDUP_WINDOW = dedup_window  # 去重窗口

        # 跨 segment 重叠去重
        self._prev_segment_tail = ""  # 上一个 ASR segment 的尾部文本
        self._TAIL_WINDOW = 20  # 尾部窗口大小（字符）
        self._HEAD_WINDOW = 20  # 头部窗口大小（字符）
        self._MIN_OVERLAP = 2   # 最小重叠字符数

        # 标点符号 (从配置加载)
        self._punctuation = punctuation  # 用于换行判断
        self._split_punctuation = split_punctuation  # 用于截断分割

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

    def getIntervalTime(self):
        c_time = time.time()
        self.interval_time = c_time - self._last_speech_time
        # 得到间隔时间后, 再刷新最后间隔
        self._last_speech_time = c_time
        return self.interval_time

    def tick(self, text: str, current_time: float = None) -> tuple[Optional[str], str]:
        """
        输出逻辑（简化版）：
        1. 停顿 >= silence_threshold + 最后一个是换行标点 → newline
        2. 其他情况（有标点） → continuous
        3. 无标点 → None（继续累积）
        """

        if current_time is None:
            current_time = time.time()

        # 添加到累积缓冲区
        self.append(text)

        # 返回间隔时间.
        silence_time = self.getIntervalTime()

        log(f"[TextProcessor] tick called, buffer='{self._buffer}', header='{self._header}' ")

        if not self._buffer:
            return None, ""

        # 查找最后一个换行标点（句号、问号、叹号）
        is_end = self._buffer and self._buffer[-1] in self._punctuation

        log(f"[TextProcessor] silence={silence_time:.2f}s, punct_pos={is_end}, buffer_len={len(self._buffer)}")

        output = self._buffer
        self._segment_count += 1

        # # 去重检查
        if self._is_duplicate(output, current_time):
           return None, ""
        self._record_output(output, current_time)

        # 判断换行
        if is_end and silence_time >= self._SILENCE_THRESHOLD:
            return output, "newline"
            # 找到最后一个标点之前的文本

        return output, "continuous"


    def tick_force(self, current_time: float = None) -> tuple[Optional[str], str]:
        """
        强制输出 - 仅供定时器调用
        当内容等待过长时，强制输出所有累积内容

        Returns:
            (output_text, status)
            - output_text: 要输出的文本，None表示无内容
            - status: "newline" / "continuous" / ""（无输出）
        """
        if current_time is None:
            current_time = time.time()

        if not self._buffer:
            return None, ""

        # 强制输出所有 buffer 内容
        output = self._buffer
        self._buffer = ""

        # 去重检查
        if self._is_duplicate(output, current_time):
            return None, ""

        self._segment_count += 1
        self._record_output(output, current_time)
        return output, "continuous"

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

    def _find_last_punct(self, punctuation: str, text: str) -> int:
        """查找最后一个标点的位置"""
        match = re.search(f'[{re.escape(punctuation)}]', text)
        if match:
            return match.end() - 1  # 返回字符位置，不是end索引
        return -1

    def _is_duplicate(self, text: str, current_time: float) -> bool:
        """
        检查文本是否重复（最近5秒内输出过相同文本）

        Args:
            text: 待检查文本
            current_time: 当前时间

        Returns:
            True 表示重复，应跳过输出
        """
        # 清理历史记录（删除5秒前的记录）
        expired = [t for t, ts in self._recent_outputs.items() if current_time - ts > self._DEDUP_WINDOW]
        for t in expired:
            del self._recent_outputs[t]

        # 检查是否重复
        return text in self._recent_outputs

    def _record_output(self, text: str, current_time: float):
        """
        记录已输出的文本，用于去重检测

        Args:
            text: 已输出的文本
            current_time: 输出时间
        """
        self._recent_outputs[text] = current_time

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

    def dedup_overlap(self, text: str) -> str:
        """
        跨 segment 重叠去重：检测新 segment 开头与上一个 segment 结尾的重叠

        算法：
        1. 取上一个 segment 的最后 TAIL_WINDOW 个字符
        2. 取新 segment 的前 HEAD_WINDOW 个字符
        3. 找尾部后缀与头部前缀的最长公共子串
        4. 如果重叠 ≥ MIN_OVERLAP 个字符，从新 segment 切掉重叠部分

        Example:
            prev: "...翻面。去皮"
            new:  "皮皮叮叮椒王一根..."
            → 重叠 = "皮" (len=1, < MIN_OVERLAP, 不处理)

            prev: "...去皮皮"
            new:  "皮皮叮叮椒王..."
            → 重叠 = "皮皮" (len=2, ≥ MIN_OVERLAP, 切掉)
            → result: "叮叮椒王..."
        """
        if not text:
            return text

        if not self._prev_segment_tail:
            # 第一个 segment，记录尾部后直接返回
            self._prev_segment_tail = text[-self._TAIL_WINDOW:] if len(text) > self._TAIL_WINDOW else text
            return text

        tail = self._prev_segment_tail[-self._TAIL_WINDOW:]
        head = text[:self._HEAD_WINDOW]

        # 找最长重叠：tail 的后缀 匹配 head 的前缀
        overlap_len = 0
        max_check = min(len(tail), len(head))
        for k in range(max_check, self._MIN_OVERLAP - 1, -1):
            if tail[-k:] == head[:k]:
                overlap_len = k
                break

        if overlap_len >= self._MIN_OVERLAP:
            stripped = text[overlap_len:]
            log(f"[TextProcessor] dedup_overlap: removed {overlap_len} char(s) '{text[:overlap_len]}' from segment head")
            text = stripped

        # 更新尾部
        self._prev_segment_tail = text[-self._TAIL_WINDOW:] if len(text) > self._TAIL_WINDOW else text
        return text

    def reload(self):
        """热重载配置"""
        self._buffer = ""
        self._header = ""
        self._prev_segment_tail = ""  # 清空重叠去重状态
        self._last_speech_time = time.time()
        self._segment_count = 0
        self._recent_outputs = {}  # 清空去重历史
        self._english_pattern = re.compile(r'\b([a-zA-Z])\s+([a-zA-Z])\s+([a-zA-Z])\b')
        self._number_pattern = re.compile(r'[\d]+\s*\.\s*\d+')
        print("[TextProcessor] reloaded")

    def reset(self):
        """重置内部状态"""
        self._buffer = ""
        self._header = ""
        self._prev_segment_tail = ""  # 清空重叠去重状态
        self._last_speech_time = time.time()
        self._segment_count = 0
        self._recent_outputs = {}  # 清空去重历史

    def get_status(self) -> dict:
        """获取处理器状态"""
        return {
            "buffer_len": len(self._buffer),
            "segment_count": self._segment_count,
            "last_speech_ago": time.time() - self._last_speech_time,
        }

    def clear_header(self):
        """清空待拼接前缀（自然结束时调用）"""
        self._header = ""

    def getHeader(self):
        return self._header

    def append_truncated(self, text: str, current_time: float) -> tuple[Optional[str], str]:
        """
        处理截断的文本（force_ended=True）

        逻辑：
        - 拼接 header + text
        - 根据标点数量决定输出：
          - 1个标点：整个输出到buffer，header清空
          - 多个标点：倒2分割，前半输出，后半更新header
        """
        combined = text

        # 查找所有标点（使用分割标点，包含逗号）
        punct_positions = [m.start() for m in re.finditer(f'[{re.escape(self._split_punctuation)}]', text)]

        if not punct_positions:
            # 无标点：整个存为header
            self._header = combined
            log(f"[TextProcessor] _header='{self._header}', punct_positions='{punct_positions}'")

        elif len(punct_positions) == 1:
            # 只有1个标点：整个输出到buffer，header清空
            output = combined
            self._header = ""
            if output:
                self._segment_count += 1
                return output, "continuous"
        else:
            # 多个标点：倒2分割
            second_last_punct_pos = punct_positions[-2]

            # 前半部分（到倒2标点，含标点）
            output = combined[:second_last_punct_pos + 1]

            # 后半部分（倒2标点之后，去标点）
            self._header = combined[second_last_punct_pos + 1:].rstrip(self._punctuation)

            log(f"[TextProcessor] 更新当前,下次的行首, _header = '{self._header}'")
            if output:
                self._segment_count += 1
                return output, "continuous"
        return None, ""


def create_text_processor() -> TextProcessor:
    """工厂函数"""
    return TextProcessor()
