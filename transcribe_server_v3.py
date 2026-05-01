"""
transcribe_server_v3.py - asyncio 异步架构转录服务器
推荐方案：高并发、稳定、高效
"""
import sys
import os
import time
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable
from datetime import datetime

# 日志配置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, f"server_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# 创建 logger
logger = logging.getLogger("transcribe_server")
logger.setLevel(logging.DEBUG)
logger.handlers = []  # 清空已有 handlers

# 文件 handler - 实时写入，不缓冲
class UnbufferedFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

file_handler = UnbufferedFileHandler(LOG_FILE, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_format = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
file_handler.setFormatter(file_format)

# 控制台 handler - 调试时可见
console_handler = logging.StreamHandler(sys.stderr)
console_handler.setLevel(logging.INFO)
console_format = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
console_handler.setFormatter(console_format)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# 简化日志调用
log = logger.info
log_debug = logger.debug
log_error = logger.error

# 设置环境
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

os.environ['MODELSCOPE_CACHE'] = os.path.join(SCRIPT_DIR, "models")
os.environ['MODELSCOPE_VERBOSE'] = '0'

# 静默日志
import logging
for logger_name in ['modelscope', 'funasr', 'root', 'torch']:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

from processors.audio_source import MicrophoneSource, MultiSource
from processors.vad_processor import VADConfig, create_vad_processor
from processors.asr_processor import create_asr_processor
from processors.punc_processor import create_punc_processor
from processors.hotword_manager import HotwordManager


def get_model_memory_info(model) -> dict:
    """获取模型内存占用信息"""
    import torch
    import sys

    info = {
        "params_count": 0,
        "ram_mb": 0,
        "vram_mb": 0,
    }

    try:
        # 计算参数量
        if hasattr(model, 'model') and hasattr(model.model, 'parameters'):
            # FunASR AutoModel 包装
            params = list(model.model.parameters())
        elif hasattr(model, 'parameters'):
            params = list(model.parameters())
        else:
            params = []

        info["params_count"] = sum(p.numel() for p in params)

        # 计算 RAM 占用 (float32 = 4 bytes)
        info["ram_mb"] = info["params_count"] * 4 / (1024 ** 2)

        # 计算 VRAM 占用
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



class TranscribeServerV3:
    """
    asyncio 异步架构转录服务器

    架构:
    - asyncio 事件循环处理音频流、Socket I/O
    - ThreadPoolExecutor 处理 ASR/Punc (绕过 GIL)
    - 队列自动背压控制
    """

    def __init__(self, config: dict = None):
        self.config = config or self._default_config()

        # 线程池 (ASR/Punc 在线程中执行)
        max_workers = self.config.get("threading", {}).get("max_workers", 4)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

        # asyncio 队列 (带背压控制)
        asr_queue_size = self.config.get("queue", {}).get("asr_size", 20)
        punc_queue_size = self.config.get("queue", {}).get("punc_size", 50)

        self._asr_queue: asyncio.Queue = None
        self._punc_queue: asyncio.Queue = None
        self._asr_queue_size = asr_queue_size
        self._punc_queue_size = punc_queue_size

        # 处理器
        self._audio_source: Optional[MicrophoneSource] = None
        self._vad = None
        self._asr = None
        self._punc = None
        self._hotword_manager: Optional[HotwordManager] = None

        # 状态
        self._running = False
        self._transcribing = False
        self._models_loaded = False
        self._load_start = None  # 模型加载开始时间

        # Socket 客户端
        self._writer: Optional[asyncio.StreamWriter] = None

        # 任务
        self._tasks: list = []

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
                "device": None,  # 单设备模式
                # "devices": [None, 5],  # 多设备模式 (None=默认麦克风, 5=声卡设备号)
            },
            "vad": {
                "mode": "sensevoice",
                "threshold": 0.3,
                "min_speech_duration": 1.5,
                "max_speech_duration": 5.0,  # 5s max per segment
                "silence_timeout": 4.0,  # 4s silence to trigger output (ASR needs ~2s min audio)
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

        # 创建音频队列用于回调
        self._audio_queue: queue.Queue = queue.Queue(maxsize=100)

        # 注册音频回调 - 将音频块放入队列供 _audio_loop 消费
        def on_audio(chunk):
            try:
                self._audio_queue.put_nowait(chunk)
            except queue.Full:
                pass  # 背压控制

        # 多设备模式
        devices = self.config["audio"].get("devices")
        if devices:
            # 多音频源模式
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
            # 单设备模式
            self._audio_source = MicrophoneSource(
                sample_rate=self.config["audio"]["sample_rate"],
                channels=self.config["audio"]["channels"],
                device=self.config["audio"]["device"],
            )
            log(f"Audio: MicrophoneSource (device={self.config['audio']['device']})")

        self._audio_source.on_audio(on_audio)

        # 热词管理
        self._hotword_manager = HotwordManager()

    async def _load_models_async(self):
        """异步加载模型，加载完成后启动其他任务"""
        import time
        self._load_start = time.time()
        last_report = [0]  # 已加载时间记录

        async def progress_reporter():
            """每5秒报告一次加载进度"""
            while not self._models_loaded:
                await asyncio.sleep(5)
                elapsed = int(time.time() - self._load_start)
                log(f"Still loading... ({elapsed}s elapsed)")

        log("Loading models in background...")

        loop = asyncio.get_event_loop()
        reporter_task = asyncio.create_task(progress_reporter())

        try:
            # ASR 模型 (在线程池加载)
            log("Loading ASR model...")
            self._asr = create_asr_processor(
                self.config["asr"]["model"],
                self.config["asr"]["device"]
            )
            await loop.run_in_executor(self._executor, self._asr.load_model)
            asr_info = get_model_memory_info(self._asr.model)
            log(f"ASR model loaded: {asr_info['params_count']:,} params, ~{asr_info['ram_mb']:.0f} MB RAM")
            log_memory_usage("  After ASR:")

            # VAD
            vad_config = VADConfig(
                mode=self.config["vad"]["mode"],
                threshold=self.config["vad"]["threshold"],
                min_speech_duration=self.config["vad"]["min_speech_duration"],
                max_speech_duration=self.config["vad"]["max_speech_duration"],
                silence_timeout=self.config["vad"]["silence_timeout"],
            )
            log(f"VAD config: {vad_config}")
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
            log_memory_usage("  After PUNC:")

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
            log_memory_usage("  Total memory:")

            # 模型加载完成后，启动音频处理任务
            self._tasks.extend([
                asyncio.create_task(self._audio_loop()),
                asyncio.create_task(self._asr_worker()),
                asyncio.create_task(self._punc_worker()),
            ])

            # 如果客户端已连接，自动开始转写
            if self._writer:
                log("Auto-starting transcription for connected client")
                self._transcribing = True
                self._audio_source.start()
                self._writer.write(b"OK\n")
                await self._writer.drain()
                log("Auto-start: transcription started")

        except Exception as e:
            reporter_task.cancel()
            log(f"Model loading error: {e}")
            raise

    # ============ asyncio 任务 ============

    async def run(self):
        """主事件循环"""
        self._running = True

        # 初始化处理器
        self._init_processors()

        # 初始化队列
        self._asr_queue = asyncio.Queue(maxsize=self._asr_queue_size)
        self._punc_queue = asyncio.Queue(maxsize=self._punc_queue_size)

        # 立即启动 Socket 监听（不等待模型加载）
        self._tasks = [
            asyncio.create_task(self._socket_server()),
        ]
        log(f"Server listening on {self.config['server']['host']}:{self.config['server']['port']}")

        # 在后台加载模型
        asyncio.create_task(self._load_models_async())

        try:
            # 等待所有任务
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log(f"Server error: {e}")
        finally:
            await self.shutdown()

        try:
            # 等待所有任务
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

        # 取消所有任务
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # 等待任务完成
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # 关闭线程池
        self._executor.shutdown(wait=True)

        # 关闭音频源
        if self._audio_source:
            self._audio_source.stop()

        # 关闭 Socket
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()

        log("Server stopped")

    async def _socket_server(self):
        """Socket 服务器 (异步)"""
        server = await asyncio.start_server(
            self._handle_client,
            self.config["server"]["host"],
            self.config["server"]["port"],
            reuse_address=True,
        )

        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """处理客户端连接"""
        self._writer = writer
        peer = writer.get_extra_info('peername')
        log(f"Client connected: {peer}")

        try:
            while self._running:
                try:
                    # 读取命令 (带超时)
                    data = await asyncio.wait_for(reader.readline(), timeout=1.0)
                    if not data:
                        break

                    cmd = data.decode().strip()
                    await self._process_command(cmd, writer)

                except asyncio.TimeoutError:
                    continue
                except ConnectionResetError:
                    break
                except Exception as e:
                    log(f"Client error: {e}")
                    break

        finally:
            self._writer = None
            writer.close()
            await writer.wait_closed()
            log(f"Client disconnected: {peer}")

    async def _process_command(self, cmd: str, writer: asyncio.StreamWriter):
        """处理命令"""
        cmd = cmd.strip().lower()
        log(f"CMD: {cmd}")

        if cmd == "start":
            if not self._models_loaded:
                elapsed = int(time.time() - self._load_start) if self._load_start else 0
                log(f"CMD start: Models still loading ({elapsed}s elapsed)")
                writer.write(f"ERROR: Models still loading ({elapsed}s)\n".encode())
            else:
                log("CMD start: OK - starting transcription")
                self._transcribing = True
                self._audio_source.start()
                writer.write(b"OK\n")
            await writer.drain()

        elif cmd == "stop":
            log("CMD stop: OK")
            self._transcribing = False
            self._audio_source.stop()
            writer.write(b"OK\n")
            await writer.drain()

        elif cmd == "quit":
            log("CMD quit")
            writer.write(b"OK\n")
            await writer.drain()
            self._running = False

        elif cmd == "status":
            status = "recording" if self._transcribing else "stopped"
            log(f"CMD status: {status}")
            writer.write(f"OK: {status}\n".encode())
            await writer.drain()

        elif cmd == "hotwords_reload":
            await self._reload_hotwords()
            log("CMD hotwords_reload: OK")
            writer.write(b"OK\n")
            await writer.drain()
        else:
            log(f"CMD unknown: {cmd}")

    async def _audio_loop(self):
        """音频采集循环"""
        log("AUDIO_LOOP: started")
        loop = asyncio.get_event_loop()
        last_chunk_time = 0
        chunk_count = 0

        while self._running:
            if self._transcribing and self._audio_source:
                try:
                    # 从音频队列读取 (回调方式，避免 _consume 线程竞争)
                    try:
                        chunk = self._audio_queue.get_nowait()
                    except:
                        await asyncio.sleep(0.01)
                        continue

                    if chunk is not None and len(chunk.data) > 0:
                        chunk_count += 1
                        if chunk_count % 100 == 0:  # 每100个chunk打一次日志
                            log(f"AUDIO: got chunk #{chunk_count}, samples={len(chunk.data)}")

                        # VAD 处理 (在事件循环中执行，很快)
                        segments = self._vad.process(chunk.data)

                        if segments:
                            log(f"AUDIO: VAD found {len(segments)} segments")

                        for seg in segments:
                            # 背压控制：队列满时丢弃最旧的
                            if self._asr_queue.full():
                                try:
                                    self._asr_queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    pass

                            await self._asr_queue.put(seg)

                except Exception as e:
                    log(f"Audio loop error: {e}")

            await asyncio.sleep(0.01)  # 10ms 采样间隔

    def _get_audio_chunk(self):
        """获取音频块 (在线程中执行)"""
        try:
            if hasattr(self._audio_source, '_q') and not self._audio_source._q.empty():
                return self._audio_source._q.get_nowait()
        except:
            pass
        return None

    async def _asr_worker(self):
        """ASR 工作线程"""
        log("ASR_WORKER: started")
        loop = asyncio.get_event_loop()
        asr_count = 0

        while self._running:
            try:
                # 从队列获取 (带超时)
                segment = await asyncio.wait_for(
                    self._asr_queue.get(), timeout=0.1
                )

                asr_count += 1
                log(f"ASR_WORKER: got segment #{asr_count}, dur={segment.end_time - segment.start_time:.2f}s")

                # 在线程池中执行 ASR
                result = await loop.run_in_executor(
                    self._executor,
                    self._asr_recognize,
                    segment
                )

                if result and result.text:
                    log(f"ASR_WORKER: recognized '{result.text[:50]}...'")
                    # 发送到 Punc 队列
                    if not self._punc_queue.full():
                        await self._punc_queue.put(result.text)
                else:
                    log("ASR_WORKER: no text result")

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log(f"ASR worker error: {e}")

    def _asr_recognize(self, segment):
        """ASR 识别 (在线程中执行)"""
        try:
            result = self._asr.recognize(segment)
            # 调试日志：显示 ASR 原始结果
            if result and result.text:
                log(f"    ASR raw: {repr(result.text[:100])}")
            else:
                log("    ASR raw: (empty)")
            return result
        except Exception as e:
            log(f"ASR error: {e}")
            return None

    async def _punc_worker(self):
        """
        Punc 工作线程 - 标点+静音组合换行

        逻辑：
        1. 累积文本
        2. 遇到标点(。！？,) → 立即输出 + <br>
        3. 无标点等 2 秒静音 → 输出 + <br>
        4. 否则继续累积
        """
        import re
        import time as time_module

        log("PUNC_WORKER: started")
        loop = asyncio.get_event_loop()
        pending_text = ""  # 累积文本
        self._last_output_time = 0.0  # 上次输出时间
        self._SILENCE_TIMEOUT = 2.0    # 2秒静音换行
        self._MIN_CHUNK = 30           # 最小累积字符
        punc_count = 0

        # 标点符号正则
        PUNC_PATTERN = re.compile(r'[。！？,]')

        while self._running:
            try:
                # 获取新文本
                try:
                    text = await asyncio.wait_for(
                        self._punc_queue.get(), timeout=0.05
                    )
                    if text:
                        log(f"PUNC_WORKER: got text len={len(text)}")
                        pending_text += text
                except asyncio.TimeoutError:
                    pass

                current_time = time_module.time()

                # 检查是否需要输出
                should_send = False
                newline_type = None  # None, "punct", "silence", "forced"

                if pending_text:
                    # 1. 检查标点 - 有标点立即输出
                    if PUNC_PATTERN.search(pending_text):
                        should_send = True
                        newline_type = "punct"
                        log("PUNC_WORKER: trigger by punct")
                    # 2. 检查静音超时 - 无标点但超时
                    elif (self._last_output_time > 0 and
                          current_time - self._last_output_time >= self._SILENCE_TIMEOUT):
                        should_send = True
                        newline_type = "silence"
                        log("PUNC_WORKER: trigger by silence timeout")
                    # 3. 强制发送 - 累积够多
                    elif len(pending_text) >= self._MIN_CHUNK * 2:
                        should_send = True
                        newline_type = "forced"
                        log("PUNC_WORKER: trigger by forced (too long)")

                if should_send:
                    # 处理标点
                    punctuated = await loop.run_in_executor(
                        self._executor,
                        self._punc_process,
                        pending_text
                    )

                    if punctuated:
                        # 构建输出
                        output = self._build_output(punctuated, newline_type, current_time)
                        # 调试日志：Punc 输出
                        log(f"  PUNC out({newline_type}): {repr(punctuated[:80])}")
                        await self._send_to_client(output)
                        self._last_output_time = current_time

                    pending_text = ""

            except Exception as e:
                log(f"Punc worker error: {e}")

    def _build_output(self, text: str, newline_type: str, current_time) -> str:
        """
        构建输出内容

        格式：[HH:MM:SS] 文本
        """
        import time as time_module
        text = text.strip()
        if not text:
            return ""
        time_header = time_module.strftime("[%H:%M:%S] ",
                                         time_module.localtime(current_time))
        return f"{time_header}{text}\n"

    def _punc_process(self, text: str) -> str:
        """标点处理 (在线程中执行)"""
        try:
            if text and self._punc:
                result = self._punc.punctuate(text)
                log(f"PUNC: processed '{text[:30]}...' -> '{result.text[:30]}...'")
                return result.text if result else text
        except Exception as e:
            log(f"Punc error: {e}")
        return text

    async def _send_to_client(self, text: str):
        """发送文本到客户端"""
        if text and self._writer:
            try:
                # 调试日志：显示发送的内容
                preview = repr(text)
                log(f">>> TX: {preview[:200]}")

                self._writer.write(text.encode('utf-8'))
                await self._writer.drain()
            except Exception as e:
                log(f"Send error: {e}")

    async def _reload_hotwords(self):
        """重新加载热词"""
        loop = asyncio.get_event_loop()

        if self._hotword_manager and self._asr:
            notes_path = self.config["hotwords"]["notes_path"]
            if os.path.exists(notes_path):
                await loop.run_in_executor(
                    self._executor,
                    self._hotword_manager.clear
                )

                count = await loop.run_in_executor(
                    self._executor,
                    self._hotword_manager.load_from_investment_notes,
                    notes_path
                )

                hotwords = self._hotword_manager.get_hotwords()
                self._asr.set_hotwords(hotwords)

                log(f"Reloaded {count} hotwords")


# ============ 启动入口 ============

def main():
    """启动服务器"""
    import signal

    server = TranscribeServerV3()

    # 信号处理
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
        log("FunASR Transcription Server v3.0 (asyncio)")
        log("=" * 50)
        loop.run_until_complete(server.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
