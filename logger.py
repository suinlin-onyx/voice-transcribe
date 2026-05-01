"""
logger.py - 统一日志模块
"""
import sys
import os
import logging
from datetime import datetime
from typing import Optional

# 日志配置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# 当前日志文件
LOG_FILE = os.path.join(LOG_DIR, f"server_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# 日志格式
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(tag)s] %(message)s"
DATE_FORMAT = "%H:%M:%S"


class TagFormatter(logging.Formatter):
    """支持自定义 tag 的格式化器"""
    def __init__(self, tag: str):
        super().__init__(LOG_FORMAT, datefmt=DATE_FORMAT)
        self.tag = tag

    def format(self, record):
        record.tag = self.tag
        return super().format(record)


class UnbufferedFileHandler(logging.FileHandler):
    """无缓冲的文件处理器"""
    def emit(self, record):
        super().emit(record)
        self.flush()


def get_logger(name: str, tag: Optional[str] = None) -> logging.Logger:
    """
    获取带 tag 的 logger

    Args:
        name: logger 名称 (通常用 __name__)
        tag: 日志标签 (用于区分模块，如 "ASR", "VAD", "PUNC")

    Example:
        logger = get_logger(__name__, "ASR")
        logger.info("Model loaded")
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # Tag 默认值
    if tag is None:
        tag = name.split('.')[-1].upper()  # 默认用模块名

    # 文件 handler
    file_handler = UnbufferedFileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(TagFormatter(tag))

    # 控制台 handler (仅 DEBUG 以上)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(TagFormatter(tag))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# 预定义的 logger 实例
def asr_logger():
    return get_logger("asr", "ASR")

def vad_logger():
    return get_logger("vad", "VAD")

def punc_logger():
    return get_logger("punc", "PUNC")

def audio_logger():
    return get_logger("audio", "AUDIO")

def hotword_logger():
    return get_logger("hotword", "HOTWORD")

def server_logger():
    return get_logger("server", "SERVER")

def socket_logger():
    return get_logger("socket", "SOCKET")
