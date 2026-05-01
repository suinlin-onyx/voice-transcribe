"""
transcribe_server_ws.py - WebSocket 版 FunASR 转录服务器

支持 WebSocket 协议，实现状态主动推送
"""
import sys
import os
import time
import asyncio
import logging
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any
from datetime import datetime
import uuid

# 日志配置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, f"server_ws_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# 创建 logger
logger = logging.getLogger("transcribe_server_ws")
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

console_handler = logging.StreamHandler(sys.stderr)
console_handler.setLevel(logging.INFO)
console_format = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
console_handler.setFormatter(console_format)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

log = logger.info
log_debug = logger.debug
log_error = logger.error

# 设置环境
sys.path.insert(0, SCRIPT_DIR)
os.environ['MODELSCOPE_CACHE'] = os.path.join(SCRIPT_DIR, "models")
os.environ['MODELSCOPE_VERBOSE'] = '0'

# 静默日志
import logging as _logging
for logger_name in ['modelscope', 'funasr', 'root', 'torch']:
    _logging.getLogger(logger_name).setLevel(_logging.CRITICAL)

from processors.audio_source import MicrophoneSource, MultiSource
from processors.vad_processor import VADConfig, create_vad_processor
from processors.asr_processor import create_asr_processor
from processors.punc_processor import create_punc_processor
from processors.hotword_manager import HotwordManager


def get_model_memory_info(model) -> dict:
    """获取模型内存占用信息"""
    import torch

    info = {"params_count": 0, "ram_mb": 0, "vram_mb": 0}

    try:
        if hasattr(model, 'model') and hasattr(model.model, 'parameters'):
            params = list(model.model.parameters())
        elif hasattr(model, 'parameters'):
            params = list(model.parameters())
        else:
            params = []

        info["params_count"] = sum(p.numel() for p in params)
        info["ram_mb"] = info["params_count"] * 4 / (1024 ** 2)

        if torch.cuda.is_available():
            info["vram_mb"] = torch.cuda.memory_allocated() / (1024 ** 2)
    except Exception as e:
        info["error"] = str(e)

    return info


def log_memory_usage(prefix: str = ""):
    """记录当前内存占用"""
    import torch

    lines = []
    if prefix:
        lines.append(f"{prefix}")

    try:
        import psutil
        process = psutil.Process()
        ram_mb = process.memory_info().rss / (1024 ** 2)
        lines.append(f"RAM: {ram_mb:.1f} MB")
    except ImportError:
        lines.append("RAM: (psutil not installed)")

    if torch.cuda.is_available():
        vram_mb = torch.cuda.memory_allocated() / (1024 ** 2)
        vram_cached = torch.cuda.memory_reserved() / (1024 ** 2)
        lines.append(f"VRAM: {vram_mb:.1f} MB (cached: {vram_cached:.1f} MB)")

    log(" | ".join(lines))


class MessageBuilder:
    """WebSocket 消息构建器"""

    @staticmethod
    def build(type: str, status: str = None, action: str = None, payload: Dict[str, Any] = None) -> str:
        """构建消息 JSON"""
        msg = {
            "id": str(uuid.uuid4()),
            "type": type,
            "timestamp": int(time.time() * 1000)
        }
        if status:
            msg["status"] = status
        if action:
            msg["action"] = action
        if payload:
            msg["payload"] = payload

        return json.dumps(msg, ensure_ascii=False)

    @staticmethod
    def state_update(status: str, payload: Dict[str, Any] = None) -> str:
        return MessageBuilder.build("state_update", status=status, payload=payload)

    @staticmethod
    def transcription(text: str, is_final: bool = True) -> str:
        return MessageBuilder.build(
            "transcription",
            status="recognizing",
            payload={"text": text, "isFinal": is_final}
        )

    @staticmethod
    def error(message: str, error_code: str = "UNKNOWN") -> str:
        return MessageBuilder.build(
            "error",
            status="error",
            payload={"errorMessage": message, "errorCode": error_code}
        )


class TranscribeServerWS:
    """
    WebSocket 版 FunASR 转录服务器

    特性:
    - WebSocket 协议支持
    - 状态主动推送
    - 心跳保活
    """

    def __init__(self, config: dict = None):
        self.config = config or self._default_config()

        max_workers = self.config.get("threading", {}).get("max_workers", 4)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

        asr_queue_size = self.config.get("queue", {}).get("asr_size", 20)
        punc_queue_size = self.config.get("queue", {}).get("punc_size", 50)

        self._asr_queue: asyncio.Queue = None
        self._punc_queue: asyncio.Queue = None
        self._asr_queue_size = asr_queue_size
        self._punc_queue_size = punc_queue_size

        self._audio_source: Optional[MicrophoneSource] = None
        self._vad = None
        self._asr = None
        self._punc = None
        self._hotword_manager: Optional[HotwordManager] = None

        self._running = False
        self._transcribing = False
        self._models_loaded = False
        self._load_start = None

        # WebSocket 连接
        self._ws_client = None

        self._tasks: list = []

        # 心跳
        self._heartbeat_interval = 30  # 秒

    def _default_config(self) -> dict:
        """默认配置"""
        return {
            "server": {
                "host": "127.0.0.1",
                "port": 9876,
            },
            "audio": {
                "sample_rate": 16000,
                "channels": 1,
                "device": None,
            },
            "vad": {
                "mode": "sensevoice",
                "threshold": 0.3,
                "min_speech_duration": 1.5,
                "max_speech_duration": 5.0,
                "silence_timeout": 4.0,
            },
            "asr": {
                "model": "iic/SenseVoiceSmall",
                "device": "cuda",
            },
            "punc": {
                "model": "ct-punc",
                "enabled": True,
            },
            "hotwords": {
                "load_from_notes": True,
                "notes_path": "D:/arvin/obsidian_workpace/arvin-notes/00.raw/01.投资研究/",
            },
            "threading": {
                "max_workers": 4,
            },
            "queue": {
                "asr_size": 20,
                "punc_size": 50,
            },
        }

    def _init_processors(self):
        """初始化处理器"""
        import queue

        self._audio_queue: queue.Queue = queue.Queue(maxsize=100)

        def on_audio(chunk):
            try:
                self._audio_queue.put_nowait(chunk)
            except queue.Full:
                pass

        devices = self.config["audio"].get("devices")
        if devices:
            self._audio_source = MultiSource()
            for device in devices:
                source = MicrophoneSource(
                    sample_rate=self.config["audio"]["sample_rate"],
                    channels=self.config["audio"]["channels"],
                    device=device,
                )
                self._audio_source.add_source(source)
            log(f"Audio: MultiSource with {len(devices)} devices")
        else:
            self._audio_source = MicrophoneSource(
                sample_rate=self.config["audio"]["sample_rate"],
                channels=self.config["audio"]["channels"],
                device=self.config["audio"]["device"],
            )
            log(f"Audio: MicrophoneSource (device={self.config['audio']['device']})")

        self._audio_source.on_audio(on_audio)
        self._hotword_manager = HotwordManager()

    async def _load_models_async(self):
        """异步加载模型"""
        import time
        self._load_start = time.time()

        async def progress_reporter():
            """每5秒报告一次加载进度"""
            while not self._models_loaded:
                await asyncio.sleep(5)
                elapsed = int(time.time() - self._load_start)
                # 发送下载中状态
                await self._send_state_update("downloading_model", {"downloadProgress": 0.5})
                log(f"Still loading... ({elapsed}s elapsed)")

        log("Loading models in background...")

        loop = asyncio.get_event_loop()
        reporter_task = asyncio.create_task(progress_reporter())

        try:
            # ASR 模型
            log("Loading ASR model...")
            self._asr = create_asr_processor(
                self.config["asr"]["model"],
                self.config["asr"]["device"]
            )
            await loop.run_in_executor(self._executor, self._asr.load_model)
            asr_info = get_model_memory_info(self._asr.model)
            log(f"ASR model loaded: {asr_info['params_count']:,} params, ~{asr_info['ram_mb']:.0f} MB RAM")

            # VAD
            vad_config = VADConfig(
                mode=self.config["vad"]["mode"],
                threshold=self.config["vad"]["threshold"],
                min_speech_duration=self.config["vad"]["min_speech_duration"],
                max_speech_duration=self.config["vad"]["max_speech_duration"],
                silence_timeout=self.config["vad"]["silence_timeout"],
            )
            self._vad = create_vad_processor(
                self.config["vad"]["mode"],
                asr_model=self._asr.model,
                config=vad_config
            )

            # Punc
            log("Loading Punctuation model...")
            self._punc = create_punc_processor(
                self.config["punc"]["model"],
                self.config["punc"]["enabled"]
            )
            await loop.run_in_executor(self._executor, self._punc.load_model)
            log("Punctuation model loaded")

            # 热词
            if self.config["hotwords"]["load_from_notes"]:
                notes_path = self.config["hotwords"]["notes_path"]
                if os.path.exists(notes_path):
                    count = await loop.run_in_executor(
                        self._executor,
                        self._hotword_manager.load_from_investment_notes,
                        notes_path
                    )
                    log(f"Loaded {count} hotwords")

                    hotwords = self._hotword_manager.get_hotwords()
                    self._asr.set_hotwords(hotwords)

            self._models_loaded = True
            reporter_task.cancel()
            total_time = int(time.time() - self._load_start)
            log(f"All models loaded - ready for transcription (total: {total_time}s)")

            # 发送模型加载完成状态
            await self._send_state_update("model_loaded")

            # 启动音频处理任务
            self._tasks.extend([
                asyncio.create_task(self._audio_loop()),
                asyncio.create_task(self._asr_worker()),
                asyncio.create_task(self._punc_worker()),
            ])

            # 如果客户端已连接，自动开始转写
            if self._ws_client:
                log("Auto-starting transcription for connected client")
                self._transcribing = True
                self._audio_source.start()
                await self._send_state_update("recognizing")

        except Exception as e:
            reporter_task.cancel()
            log(f"Model loading error: {e}")
            await self._send_error(str(e), "MODEL_LOAD_FAILED")
            raise

    async def _send_state_update(self, status: str, payload: Dict[str, Any] = None):
        """发送状态更新到客户端"""
        if self._ws_client:
            try:
                msg = MessageBuilder.state_update(status, payload)
                await self._ws_client.send(msg)
                log(f"Sent state_update: {status}")
            except Exception as e:
                log_error(f"Failed to send state update: {e}")

    async def _send_transcription(self, text: str, is_final: bool = True):
        """发送识别结果到客户端"""
        if self._ws_client:
            try:
                msg = MessageBuilder.transcription(text, is_final)
                await self._ws_client.send(msg)
                log_debug(f"Sent transcription: {text[:50]}...")
            except Exception as e:
                log_error(f"Failed to send transcription: {e}")

    async def _send_error(self, message: str, error_code: str = "UNKNOWN"):
        """发送错误到客户端"""
        if self._ws_client:
            try:
                msg = MessageBuilder.error(message, error_code)
                await self._ws_client.send(msg)
            except Exception as e:
                log_error(f"Failed to send error: {e}")

    async def run(self):
        """主事件循环"""
        self._running = True

        self._init_processors()

        self._asr_queue = asyncio.Queue(maxsize=self._asr_queue_size)
        self._punc_queue = asyncio.Queue(maxsize=self._punc_queue_size)

        # 尝试导入 websockets
        try:
            import websockets
            self._websockets = websockets
        except ImportError:
            log_error("websockets library not found. Please install: pip install websockets")
            log_error("Falling back to TCP mode...")
            # 回退到 TCP 模式（使用原有逻辑）
            self._tasks.append(asyncio.create_task(self._tcp_server()))
        else:
            # WebSocket 模式
            self._tasks.append(asyncio.create_task(self._websocket_server()))

        log(f"Server starting on {self.config['server']['host']}:{self.config['server']['port']}")

        # 在后台加载模型
        asyncio.create_task(self._load_models_async())

        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log(f"Server error: {e}")
        finally:
            await self.shutdown()

    async def shutdown(self):
        """关闭服务器"""
        self._running = False
        self._transcribing = False

        for task in self._tasks:
            if not task.done():
                task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)

        self._executor.shutdown(wait=True)

        if self._audio_source:
            self._audio_source.stop()

        log("Server stopped")

    async def _websocket_server(self):
        """WebSocket 服务器"""
        import websockets

        server = await websockets.serve(
            self._handle_websocket_client,
            self.config["server"]["host"],
            self.config["server"]["port"],
        )

        log(f"WebSocket server listening on {self.config['server']['host']}:{self.config['server']['port']}")

        async with server:
            await asyncio.Future()

    async def _handle_websocket_client(self, websocket, path):
        """处理 WebSocket 客户端连接"""
        self._ws_client = websocket
        peer = websocket.remote_address
        log(f"WebSocket client connected: {peer}")

        # 发送已连接状态
        await self._send_state_update("connected")

        # 如果模型已加载，发送模型状态
        if self._models_loaded:
            await self._send_state_update("model_loaded")
        else:
            await self._send_state_update("downloading_model")

        # 启动心跳任务
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(websocket))

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self._process_message(data)
                except json.JSONDecodeError:
                    log_error(f"Invalid JSON: {message}")
                    await self._send_error("Invalid JSON format", "PARSE_ERROR")

        except self._websockets.exceptions.ConnectionClosed:
            log("WebSocket client disconnected normally")
        except Exception as e:
            log_error(f"WebSocket client error: {e}")
        finally:
            heartbeat_task.cancel()
            self._ws_client = None
            log(f"WebSocket client disconnected: {peer}")

    async def _heartbeat_loop(self, websocket):
        """心跳循环"""
        try:
            while self._running:
                await asyncio.sleep(self._heartbeat_interval)
                if websocket.open:
                    msg = MessageBuilder.build("heartbeat", action="heartbeat")
                    await websocket.send(msg)
                    log_debug("Sent heartbeat")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log_error(f"Heartbeat error: {e}")

    async def _process_message(self, data: dict):
        """处理来自客户端的消息"""
        action = data.get("action")
        msg_id = data.get("id", "")

        log(f"Received action: {action}")

        if action == "start_recording":
            if not self._models_loaded:
                await self._send_state_update("downloading_model")
                await self._send_error("Models still loading", "NOT_READY")
            else:
                self._transcribing = True
                self._audio_source.start()
                await self._send_state_update("recognizing")

        elif action == "stop_recording":
            self._transcribing = False
            self._audio_source.stop()
            await self._send_state_update("idle")

        elif action == "query_state":
            if not self._models_loaded:
                await self._send_state_update("downloading_model")
            elif self._transcribing:
                await self._send_state_update("recognizing")
            else:
                await self._send_state_update("idle")

        elif action == "heartbeat":
            # 心跳响应
            log_debug("Received heartbeat")

        else:
            log_error(f"Unknown action: {action}")

    # ============ TCP 回退模式 ============

    async def _tcp_server(self):
        """TCP Socket 服务器（回退模式）"""
        server = await asyncio.start_server(
            self._handle_tcp_client,
            self.config["server"]["host"],
            self.config["server"]["port"],
            reuse_address=True,
        )

        async with server:
            await server.serve_forever()

    async def _handle_tcp_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """处理 TCP 客户端连接"""
        self._tcp_writer = writer
        peer = writer.get_extra_info('peername')
        log(f"TCP client connected: {peer}")

        try:
            while self._running:
                try:
                    data = await asyncio.wait_for(reader.readline(), timeout=1.0)
                    if not data:
                        break

                    cmd = data.decode().strip()
                    await self._process_tcp_command(cmd, writer)

                except asyncio.TimeoutError:
                    continue
                except ConnectionResetError:
                    break
                except Exception as e:
                    log(f"TCP client error: {e}")
                    break

        finally:
            self._tcp_writer = None
            writer.close()
            await writer.wait_closed()
            log(f"TCP client disconnected: {peer}")

    async def _process_tcp_command(self, cmd: str, writer: asyncio.StreamWriter):
        """处理 TCP 命令"""
        cmd = cmd.strip().lower()
        log(f"TCP CMD: {cmd}")

        if cmd == "start":
            if not self._models_loaded:
                writer.write(f"ERROR: Models still loading\n".encode())
            else:
                self._transcribing = True
                self._audio_source.start()
                writer.write(b"OK\n")
            await writer.drain()

        elif cmd == "stop":
            self._transcribing = False
            self._audio_source.stop()
            writer.write(b"OK\n")
            await writer.drain()

        elif cmd == "quit":
            writer.write(b"OK\n")
            await writer.drain()
            self._running = False

    # ============ 音频处理任务 ============

    async def _audio_loop(self):
        """音频采集循环"""
        log("AUDIO_LOOP: started")
        loop = asyncio.get_event_loop()
        chunk_count = 0

        while self._running:
            if self._transcribing and self._audio_source:
                try:
                    try:
                        chunk = self._audio_queue.get_nowait()
                    except:
                        await asyncio.sleep(0.01)
                        continue

                    if chunk is not None and len(chunk.data) > 0:
                        chunk_count += 1
                        if chunk_count % 100 == 0:
                            log(f"AUDIO: got chunk #{chunk_count}")

                        segments = self._vad.process(chunk.data)

                        if segments:
                            log(f"AUDIO: VAD found {len(segments)} segments")

                        for seg in segments:
                            if self._asr_queue.full():
                                try:
                                    self._asr_queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    pass

                            await self._asr_queue.put(seg)

                except Exception as e:
                    log(f"Audio loop error: {e}")

            await asyncio.sleep(0.01)

    async def _asr_worker(self):
        """ASR 工作线程"""
        log("ASR_WORKER: started")
        loop = asyncio.get_event_loop()
        asr_count = 0

        while self._running:
            try:
                segment = await asyncio.wait_for(
                    self._asr_queue.get(), timeout=0.1
                )

                asr_count += 1
                log_debug(f"ASR_WORKER: got segment #{asr_count}")

                result = await loop.run_in_executor(
                    self._executor,
                    self._asr_recognize,
                    segment
                )

                if result and result.text:
                    log_debug(f"ASR_WORKER: recognized '{result.text[:50]}...'")
                    if not self._punc_queue.full():
                        await self._punc_queue.put(result.text)

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log(f"ASR worker error: {e}")

    def _asr_recognize(self, segment):
        """ASR 识别"""
        try:
            result = self._asr.recognize(segment)
            if result and result.text:
                log_debug(f"    ASR raw: {repr(result.text[:100])}")
            return result
        except Exception as e:
            log(f"ASR error: {e}")
            return None

    async def _punc_worker(self):
        """Punc 工作线程"""
        import re
        import time as time_module

        log("PUNC_WORKER: started")
        loop = asyncio.get_event_loop()
        pending_text = ""
        self._last_output_time = 0.0
        self._SILENCE_TIMEOUT = 2.0
        self._MIN_CHUNK = 30

        PUNC_PATTERN = re.compile(r'[。！？,]')

        while self._running:
            try:
                try:
                    text = await asyncio.wait_for(
                        self._punc_queue.get(), timeout=0.05
                    )
                    if text:
                        pending_text += text
                except asyncio.TimeoutError:
                    pass

                current_time = time_module.time()
                should_send = False
                newline_type = None

                if pending_text:
                    if PUNC_PATTERN.search(pending_text):
                        should_send = True
                        newline_type = "punct"
                    elif (self._last_output_time > 0 and
                          current_time - self._last_output_time >= self._SILENCE_TIMEOUT):
                        should_send = True
                        newline_type = "silence"
                    elif len(pending_text) >= self._MIN_CHUNK * 2:
                        should_send = True
                        newline_type = "forced"

                if should_send:
                    punctuated = await loop.run_in_executor(
                        self._executor,
                        self._punc_process,
                        pending_text
                    )

                    if punctuated:
                        output = self._build_output(punctuated, newline_type, current_time)
                        log(f"  PUNC out({newline_type}): {repr(punctuated[:80])}")
                        await self._send_transcription(output)
                        self._last_output_time = current_time

                    pending_text = ""

            except Exception as e:
                log(f"Punc worker error: {e}")

    def _build_output(self, text: str, newline_type: str, current_time) -> str:
        """构建输出内容"""
        import time as time_module
        text = text.strip()
        if not text:
            return ""
        time_header = time_module.strftime("[%H:%M:%S] ",
                                         time_module.localtime(current_time))
        return f"{time_header}{text}\n"

    def _punc_process(self, text: str) -> str:
        """标点处理"""
        try:
            if text and self._punc:
                result = self._punc.punctuate(text)
                return result.text if result else text
        except Exception as e:
            log(f"Punc error: {e}")
        return text


# ============ 启动入口 ============

def main():
    """启动服务器"""
    import signal

    server = TranscribeServerWS()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def signal_handler(s, f):
        log("\nShutting down...")
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        log("=" * 50)
        log("FunASR Transcription Server v4.0 (WebSocket)")
        log("=" * 50)
        loop.run_until_complete(server.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
