"""
api_server.py - API Server 模块
负责 WebSocket 连接管理和 HTTP REST API
与 ModelServer 通过回调函数通信

协议: 兼容 su-rec 插件客户端
"""
import sys
import os
import asyncio
import logging
import json
import time
import uuid
from datetime import datetime
from typing import Optional, Set
import websockets

# 日志配置
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, f"api_server_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
TRANSCRIPT_LOG_FILE = os.path.join(LOG_DIR, f"transcript_{datetime.now().strftime('%Y%m%d')}.log")

logger = logging.getLogger("api_server")
logger.setLevel(logging.DEBUG)
logger.handlers = []

class UnbufferedFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

file_handler = UnbufferedFileHandler(LOG_FILE, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_format = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
file_handler.setFormatter(file_format)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_format = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
console_handler.setFormatter(console_format)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

log = logger.info
log_debug = logger.debug
log_error = logger.error


def gen_id() -> str:
    """生成 UUID"""
    return str(uuid.uuid4())


def now_ms() -> int:
    """当前时间戳(毫秒)"""
    return int(time.time() * 1000)


class APIServer:
    """
    API Server - WebSocket + HTTP 接口

    职责:
    - WebSocket 连接管理 (端口 9876)
    - HTTP REST API (/health, /control)
    - 消息协议转换 (WS ↔ 内部消息)
    - 状态主动推送 (idle/recognizing/no_audio)

    协议: 兼容 su-rec 插件客户端
    """

    # 状态映射: 内部状态 → 客户端协议状态
    STATUS_MAP = {
        "idle": "idle",           # 空闲
        "loading": "downloading_model",  # 加载中
        "ready": "model_loaded",  # 模型就绪
        "transcribing": "recognizing",  # 正在识别
        "no_audio": "no_audio",  # 无音频
    }

    def __init__(self, host: str = "127.0.0.1", port: int = 9876):
        self._host = host
        self._port = port
        self._running = False
        self._model_server = None
        self._server = None
        self._http_server = None
        self._ws_clients: Set[websockets.WebSocketServerProtocol] = set()
        self._last_status = ""  # Empty string to ensure first status is never dedup-suppressed
        self._heartbeat_timeout = 60  # 60秒无心跳断开

    def set_model_server(self, model_server):
        """设置 Model Server 引用"""
        self._model_server = model_server
        model_server.set_output_callback(self._on_text_output)

    def _to_client_status(self, internal_status: dict) -> str:
        """内部状态转换为客户端协议状态"""
        if not internal_status.get("models_loaded"):
            return "downloading_model"
        if internal_status.get("transcribing"):
            return "recognizing"
        return "idle"

    async def _on_text_output(self, text: str):
        """文本输出回调 - 广播到所有 WebSocket 客户端"""
        log(f">>> TX: {repr(text)}")
        # 透传日志 - 输出到 api_server 日志
        with open(TRANSCRIPT_LOG_FILE, "a", encoding="utf-8") as f: f.write(text)

        await self._broadcast({
            "id": gen_id(),
            "type": "transcription",
            "status": "recognizing",
            "payload": {
                "text": text,
                "isFinal": True,
                "timestamp": now_ms()
            },
            "timestamp": now_ms()
        })

    async def _broadcast(self, message: dict):
        """广播消息到所有客户端"""
        if not self._ws_clients:
            return
        msg_str = json.dumps(message, ensure_ascii=False)
        disconnected = set()
        for client in self._ws_clients:
            try:
                await client.send(msg_str)
            except Exception as e:
                log(f"Broadcast error: {e}")
                disconnected.add(client)
        self._ws_clients -= disconnected

    async def _send_status(self, status: str = None):
        """主动推送状态"""
        if not self._model_server:
            status_str = "idle"
        elif status:
            status_str = status
        else:
            internal = self._model_server.get_status()
            status_str = self._to_client_status(internal)

        if status_str == self._last_status and status_str in ["idle", "model_loaded"]:
            return  # 避免重复推送

        self._last_status = status_str

        await self._broadcast({
            "id": gen_id(),
            "type": "state_update",
            "status": status_str,
            "payload": {},
            "timestamp": now_ms()
        })

    async def ws_handler(self, websocket: websockets.WebSocketServerProtocol):
        """WebSocket 连接处理器"""
        self._ws_clients.add(websocket)
        peer = websocket.remote_address
        log(f"WebSocket client connected: {peer}")

        client_id = None
        last_heartbeat = time.time()

        try:
            # 发送 connected 状态
            await websocket.send(json.dumps({
                "id": gen_id(),
                "type": "state_update",
                "status": "connected",
                "payload": {},
                "timestamp": now_ms()
            }))

            # 等待模型加载完成后再发送状态
            while self._running:
                try:
                    if self._model_server and self._model_server.get_status()["models_loaded"]:
                        # Send model_loaded explicitly so plugin auto-start triggers
                        await websocket.send(json.dumps({
                            "id": gen_id(),
                            "type": "state_update",
                            "status": "model_loaded",
                            "payload": {},
                            "timestamp": now_ms()
                        }))
                        self._last_status = "model_loaded"
                        await self._send_status()
                        break
                    await asyncio.sleep(0.5)
                except Exception as e:
                    log(f"Status check error: {e}")
                    break

            # 消息循环
            async for message in websocket:
                try:
                    data = json.loads(message)
                    client_id = data.get("id")
                    last_heartbeat = time.time()

                    await self._handle_ws_message(websocket, data)

                    # 检查心跳
                    if time.time() - last_heartbeat > self._heartbeat_timeout:
                        log(f"Heartbeat timeout for {peer}")
                        break

                except json.JSONDecodeError:
                    log(f"Invalid JSON: {message}")
                    await websocket.send(json.dumps({
                        "id": gen_id(),
                        "type": "error",
                        "status": "error",
                        "payload": {
                            "errorMessage": "Invalid JSON format",
                            "errorCode": "UNKNOWN"
                        },
                        "timestamp": now_ms()
                    }))
                except Exception as e:
                    log(f"Message handler error: {e}")

        except websockets.ConnectionClosed:
            log(f"WebSocket client disconnected: {peer}")
        except Exception as e:
            log(f"WebSocket handler error: {e}")
        finally:
            self._ws_clients.discard(websocket)

    async def _handle_ws_message(self, websocket, data: dict):
        """处理 WebSocket 消息"""
        action = data.get("action", "").lower()
        msg_id = data.get("id", gen_id())
        log(f"WS action: {action}")

        if not self._model_server:
            await websocket.send(json.dumps({
                "id": msg_id,
                "type": "error",
                "status": "error",
                "payload": {
                    "errorMessage": "ModelServer not initialized",
                    "errorCode": "UNKNOWN"
                },
                "timestamp": now_ms()
            }))
            return

        # 心跳
        if action == "heartbeat":
            await websocket.send(json.dumps({
                "id": msg_id,
                "type": "heartbeat",
                "status": self._last_status,
                "payload": {},
                "timestamp": now_ms()
            }))
            return

        # start_recording
        if action == "start_recording":
            if not self._model_server.get_status()["models_loaded"]:
                await websocket.send(json.dumps({
                    "id": msg_id,
                    "type": "error",
                    "status": "error",
                    "payload": {
                        "errorMessage": "Models still loading",
                        "errorCode": "MODEL_NOT_FOUND"
                    },
                    "timestamp": now_ms()
                }))
            else:
                self._model_server.start_transcribing()
                await websocket.send(json.dumps({
                    "id": msg_id,
                    "type": "state_update",
                    "status": "recognizing",
                    "payload": {},
                    "timestamp": now_ms()
                }))
                await self._send_status()
            return

        # stop_recording
        if action == "stop_recording":
            self._model_server.stop_transcribing()
            await websocket.send(json.dumps({
                "id": msg_id,
                "type": "state_update",
                "status": "idle",
                "payload": {},
                "timestamp": now_ms()
            }))
            await self._send_status()
            return

        # query_state
        if action == "query_state":
            internal = self._model_server.get_status()
            status_str = self._to_client_status(internal)
            await websocket.send(json.dumps({
                "id": msg_id,
                "type": "state_response",
                "status": status_str,
                "payload": {},
                "timestamp": now_ms()
            }))
            return

        # reload_text_processor
        if action == "reload_text_processor":
            result = self._model_server.reload_text_processor()
            await websocket.send(json.dumps({
                "id": msg_id,
                "type": "state_response",
                "status": self._last_status,
                "payload": {"message": result["message"]},
                "timestamp": now_ms()
            }))
            return

        # hotwords_reload
        if action == "hotwords_reload":
            result = self._model_server.reload_hotwords()
            await websocket.send(json.dumps({
                "id": msg_id,
                "type": "state_response",
                "status": self._last_status,
                "payload": {"message": result["message"]},
                "timestamp": now_ms()
            }))
            return

        # 未知 action
        await websocket.send(json.dumps({
            "id": msg_id,
            "type": "error",
            "status": "error",
            "payload": {
                "errorMessage": f"Unknown action: {action}",
                "errorCode": "UNKNOWN"
            },
            "timestamp": now_ms()
        }))

    async def _handle_http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """处理 HTTP 请求"""
        try:
            request_line = await reader.readline()
            if not request_line:
                return

            method, path, _ = request_line.decode().strip().split()

            headers = {}
            while True:
                line = await reader.readline()
                if line in (b'\r\n', b'\n', b''):
                    break
                try:
                    key, value = line.decode().strip().split(': ', 1)
                    headers[key.lower()] = value
                except:
                    break

            response = await self._process_http_request(method, path, headers)
            writer.write(response.encode())
            await writer.drain()

        except Exception as e:
            log(f"HTTP handler error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _process_http_request(self, method: str, path: str, headers: dict) -> str:
        """处理 HTTP 请求"""
        cors_headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }

        if method == "GET" and path == "/health":
            internal = self._model_server.get_status() if self._model_server else {}
            status_str = self._to_client_status(internal)
            body = json.dumps({
                "status": status_str,
                "models_loaded": internal.get("models_loaded", False),
                "transcribing": internal.get("transcribing", False),
            })
            return self._build_http_response(200, "OK", body, cors_headers)

        if method == "GET" and path == "/status":
            internal = self._model_server.get_status() if self._model_server else {}
            status_str = self._to_client_status(internal)
            body = json.dumps({
                "status": status_str,
                "models_loaded": internal.get("models_loaded", False),
                "transcribing": internal.get("transcribing", False),
                "text_processor": internal.get("text_processor", {}),
            })
            return self._build_http_response(200, "OK", body, cors_headers)

        if method == "POST" and path == "/control/start_recording":
            if self._model_server:
                result = self._model_server.start_transcribing()
                return self._build_http_response(200, "OK", json.dumps(result), cors_headers)
            return self._build_http_response(500, "Error", json.dumps({"success": False, "message": "ModelServer not set"}), cors_headers)

        if method == "POST" and path == "/control/stop_recording":
            if self._model_server:
                result = self._model_server.stop_transcribing()
                return self._build_http_response(200, "OK", json.dumps(result), cors_headers)
            return self._build_http_response(500, "Error", json.dumps({"success": False, "message": "ModelServer not set"}), cors_headers)

        if method == "POST" and path == "/control/reload-text-processor":
            if self._model_server:
                result = self._model_server.reload_text_processor()
                return self._build_http_response(200, "OK", json.dumps(result), cors_headers)
            return self._build_http_response(500, "Error", json.dumps({"success": False, "message": "ModelServer not set"}), cors_headers)

        if method == "POST" and path == "/control/reload-hotwords":
            if self._model_server:
                result = self._model_server.reload_hotwords()
                return self._build_http_response(200, "OK", json.dumps(result), cors_headers)
            return self._build_http_response(500, "Error", json.dumps({"success": False, "message": "ModelServer not set"}), cors_headers)

        if method == "OPTIONS":
            return self._build_http_response(200, "OK", "", cors_headers)

        return self._build_http_response(404, "Not Found", json.dumps({"error": "Not found"}), cors_headers)

    def _build_http_response(self, status_code: int, status_text: str, body: str, extra_headers: dict = None) -> str:
        """构建 HTTP 响应"""
        headers = {
            "Content-Type": "application/json",
            "Content-Length": len(body.encode()),
            "Connection": "close",
        }
        if extra_headers:
            headers.update(extra_headers)

        header_lines = [f"HTTP/1.1 {status_code} {status_text}"]
        for key, value in headers.items():
            header_lines.append(f"{key}: {value}")

        return "\r\n".join(header_lines) + "\r\n\r\n" + body

    async def start(self):
        """启动 API Server"""
        self._running = True

        async with websockets.serve(self.ws_handler, self._host, self._port):
            log(f"WebSocket Server started on {self._host}:{self._port}")

            # HTTP 服务器用于 /health 等接口
            self._http_server = await asyncio.start_server(
                self._handle_http,
                self._host,
                self._port + 1,  # HTTP on 端口+1
                reuse_address=True,
            )
            log(f"HTTP Server started on {self._host}:{self._port + 1}")

            await asyncio.Future()

    async def stop(self):
        """停止 API Server"""
        self._running = False
        if self._http_server:
            self._http_server.close()
            await self._http_server.wait_closed()
        log("API Server stopped")
