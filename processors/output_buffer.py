"""
processors/output_buffer.py - 输出缓冲层
批量发送 + 确认机制
"""
import time
import threading
from typing import List, Optional, Callable

from interfaces import IOutputBuffer


class OutputBuffer(IOutputBuffer):
    """输出缓冲区"""

    def __init__(
        self,
        batch_interval: float = 0.3,
        batch_size: int = 50,
        retry_count: int = 3,
        retry_interval: float = 0.5,
    ):
        self.batch_interval = batch_interval
        self.batch_size = batch_size
        self.retry_count = retry_count
        self.retry_interval = retry_interval

        self._buffer: List[str] = []
        self._buffer_lock = threading.Lock()
        self._last_flush_time = time.time()
        self._ack_callback: Optional[Callable[[], None]] = None
        self._send_callback: Optional[Callable[[str], bool]] = None
        self._pending_text = ""  # 待确认的文本
        self._pending_lock = threading.Lock()

    def set_send_callback(self, callback: Callable[[str], bool]) -> None:
        """设置发送回调"""
        self._send_callback = callback

    def push(self, text: str) -> None:
        """添加文本到缓冲区"""
        with self._buffer_lock:
            self._buffer.append(text)
            self._buffer.append("")  # 空格分隔

    def push_with_newline(self, text: str) -> None:
        """添加文本并换行"""
        with self._buffer_lock:
            self._buffer.append(text)
            self._buffer.append("\n")

    def flush(self) -> str:
        """强制刷新，返回缓冲内容"""
        with self._buffer_lock:
            if not self._buffer:
                return ""

            text = "".join(self._buffer)
            self._buffer = []
            self._last_flush_time = time.time()

            return text

    def should_flush(self) -> bool:
        """检查是否需要刷新"""
        with self._buffer_lock:
            if not self._buffer:
                return False

            # 检查时间
            if time.time() - self._last_flush_time >= self.batch_interval:
                return True

            # 检查字数
            total_len = sum(len(s) for s in self._buffer)
            if total_len >= self.batch_size:
                return True

            return False

    def get_pending_text(self) -> str:
        """获取待确认文本"""
        with self._pending_lock:
            return self._pending_text

    def mark_sent(self, text: str) -> None:
        """标记已发送"""
        with self._pending_lock:
            self._pending_text = text

    def on_ack(self, callback: Callable[[], None]) -> None:
        """注册确认回调"""
        self._ack_callback = callback

    def trigger_ack(self) -> None:
        """触发确认"""
        with self._pending_lock:
            self._pending_text = ""

        if self._ack_callback:
            try:
                self._ack_callback()
            except Exception:
                pass

    def resend_pending(self) -> bool:
        """重发待确认内容"""
        with self._pending_lock:
            if not self._pending_text:
                return False

            text = self._pending_text

        if self._send_callback:
            return self._send_callback(text)

        return False


class GuaranteedOutputBuffer(OutputBuffer):
    """带确认保证的输出缓冲区"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._unconfirmed_count = 0
        self._max_unconfirmed = 5  # 最多5条未确认

    def can_send(self) -> bool:
        """检查是否可以发送"""
        return self._unconfirmed_count < self._max_unconfirmed

    def on_send(self) -> None:
        """发送后调用"""
        self._unconfirmed_count += 1

    def on_ack_received(self) -> None:
        """收到确认后调用"""
        if self._unconfirmed_count > 0:
            self._unconfirmed_count -= 1
        self.trigger_ack()
