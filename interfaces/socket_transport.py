"""
interfaces/socket_transport.py - Socket 通信层
"""
import socket
import threading
import sys
from typing import Optional, Callable
from interfaces import ITransport, TransportCommand

# 添加父目录到路径以便导入 logger
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import get_logger

logger = get_logger(__name__, "SOCKET")


class SocketTransport(ITransport):
    """Socket 传输层"""

    def __init__(self, socket: socket.socket):
        self._socket = socket
        self._running = False
        self._command_callback: Optional[Callable[[TransportCommand], None]] = None
        self._lock = threading.Lock()

    def set_command_callback(self, callback: Callable[[TransportCommand], None]) -> None:
        """设置命令回调"""
        self._command_callback = callback

    def send(self, data: str) -> bool:
        """发送数据"""
        try:
            self._socket.sendall(data.encode('utf-8'))
            return True
        except Exception as e:
            logger.error(f"Send error: {e}")
            return False

    def recv(self) -> Optional[TransportCommand]:
        """接收命令 (非阻塞)"""
        try:
            self._socket.setblocking(False)
            data = self._socket.recv(1024)
            if data:
                text = data.decode('utf-8').strip()
                if text:
                    return self._parse_command(text)
        except BlockingIOError:
            pass
        except Exception as e:
            logger.error(f"Recv error: {e}")
        return None

    def _parse_command(self, text: str) -> TransportCommand:
        """解析命令"""
        parts = text.split(maxsplit=1)
        command = parts[0]
        args = {}

        if len(parts) > 1:
            # 解析参数 key=value
            for arg in parts[1].split():
                if '=' in arg:
                    k, v = arg.split('=', 1)
                    args[k] = v

        return TransportCommand(command=command, args=args)

    def close(self) -> None:
        """关闭连接"""
        try:
            self._socket.close()
        except Exception:
            pass


class ServerSocket:
    """服务器Socket管理"""

    def __init__(self, host: str = "127.0.0.1", port: int = 9876):
        self.host = host
        self.port = port
        self._server_socket: Optional[socket.socket] = None
        self._running = False

    def start(self) -> None:
        """启动服务器"""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self.host, self.port))
        self._server_socket.listen(1)
        self._running = True
        logger.info(f"Server listening on {self.host}:{self.port}")

    def accept(self) -> Optional[SocketTransport]:
        """接受连接"""
        if not self._server_socket:
            return None

        try:
            self._server_socket.settimeout(1.0)
            client_socket, addr = self._server_socket.accept()
            return SocketTransport(client_socket)
        except socket.timeout:
            return None
        except Exception as e:
            logger.error(f"Accept error: {e}")
            return None

    def stop(self) -> None:
        """停止服务器"""
        self._running = False
        if self._server_socket:
            self._server_socket.close()
            self._server_socket = None
