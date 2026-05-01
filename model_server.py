"""
model_server.py - Model Server 模块
负责 ASR/VAD/PUNC 模型加载和推理
与 API Server 通过 asyncio.Queue 通信
"""
import sys
import os
import time
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from datetime import datetime

# 日志配置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, f"server_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logger = logging.getLogger("model_server")
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

# 环境设置
os.environ['MODELSCOPE_CACHE'] = os.path.join(SCRIPT_DIR, "models")
os.environ['MODELSCOPE_VERBOSE'] = '0'

import logging as third_party_log
for logger_name in ['modelscope', 'funasr', 'root', 'torch']:
    third_party_log.getLogger(logger_name).setLevel(logging.CRITICAL)

from processors.audio_source import MicrophoneSource, MultiSource
from processors.vad_processor import VADConfig, create_vad_processor
from processors.asr_processor import create_asr_processor
from processors.punc_processor import create_punc_processor
from processors.hotword_manager import HotwordManager
from text_processor import TextProcessor


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
    import psutil

    lines = []
    if prefix:
        lines.append(prefix)

    try:
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


class ModelServer:
    """
    Model Server - 负责 ASR/VAD/PUNC 模型推理

    架构:
    - 接收音频流 → VAD检测 → ASR识别 → PUNC标点 → TextProcessor后处理 → 输出
    - 与 API Server 通过 asyncio.Queue 通信
    - 内部使用 ThreadPoolExecutor 处理 ASR/PUNC (绕过 GIL)
    """

    def __init__(self, config: dict = None):
        self.config = config or self._default_config()

        max_workers = self.config.get("threading", {}).get("max_workers", 4)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

        # 队列 (与 API Server 通信)
        asr_queue_size = self.config.get("queue", {}).get("asr_size", 20)
        punc_queue_size = self.config.get("queue", {}).get("punc_size", 50)
        self._asr_queue: asyncio.Queue = asyncio.Queue(maxsize=asr_queue_size)
        self._punc_queue: asyncio.Queue = asyncio.Queue(maxsize=punc_queue_size)

        # 处理器
        self._audio_source: Optional[MicrophoneSource] = None
        self._vad = None
        self._asr = None
        self._punc = None
        self._hotword_manager: Optional[HotwordManager] = None

        # Text Processor (新增)
        self._text_processor = TextProcessor()

        # 状态
        self._running = False
        self._transcribing = False
        self._models_loaded = False
        self._load_start: Optional[float] = None

        # 回调 (API Server 设置)
        self._on_text_output: Optional[callable] = None

        # 任务
        self._tasks: list = []

    def _default_config(self) -> dict:
        """默认配置"""
        return {
            "audio": {
                "sample_rate": 16000,
                "channels": 1,
                "device": None,
                "devices": None,
            },
            "vad": {
                "mode": "sensevoice",
                "threshold": 0.5,
                "min_speech_duration": 0.3,
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

    def set_output_callback(self, callback: callable):
        """设置文本输出回调 (API Server 调用)"""
        self._on_text_output = callback

    def start(self):
        """启动 Model Server"""
        self._running = True
        asyncio.create_task(self._run_async())

    async def _run_async(self):
        """异步运行主循环"""
        loop = asyncio.get_event_loop()

        # 初始化处理器
        self._init_audio()

        # 启动后台任务
        self._tasks = [
            asyncio.create_task(self._audio_loop()),
            asyncio.create_task(self._asr_worker()),
            asyncio.create_task(self._punc_worker()),
        ]

        log("ModelServer started")

    async def stop(self):
        """停止 Model Server"""
        self._running = False
        self._transcribing = False

        for task in self._tasks:
            if not task.done():
                task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._executor.shutdown(wait=True)

        if self._audio_source:
            self._audio_source.stop()

        log("ModelServer stopped")

    def _init_audio(self):
        """初始化音频源"""
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

    async def load_models(self):
        """加载所有模型"""
        # 初始化音频和热词管理器
        self._init_audio()

        self._load_start = time.time()
        last_report = [0]

        async def progress_reporter():
            while not self._models_loaded:
                await asyncio.sleep(5)
                elapsed = int(time.time() - self._load_start)
                log(f"Still loading... ({elapsed}s elapsed)")

        log("Loading models in background...")

        loop = asyncio.get_event_loop()
        reporter_task = asyncio.create_task(progress_reporter())

        try:
            # ASR
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

            # PUNC
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

        except Exception as e:
            reporter_task.cancel()
            log(f"Model loading error: {e}")
            raise

    def start_transcribing(self):
        """开始转写"""
        if not self._models_loaded:
            elapsed = int(time.time() - self._load_start) if self._load_start else 0
            return {"success": False, "message": f"Models still loading ({elapsed}s elapsed)"}

        self._transcribing = True
        self._audio_source.start()
        return {"success": True, "message": "Transcription started"}

    def stop_transcribing(self):
        """停止转写"""
        self._transcribing = False
        self._audio_source.stop()
        return {"success": True, "message": "Transcription stopped"}

    def reload_text_processor(self):
        """热重启 TextProcessor"""
        self._text_processor.reload()
        log("TextProcessor reloaded via hot reload")
        return {"success": True, "message": "TextProcessor reloaded"}

    def reload_hotwords(self):
        """重新加载热词"""
        notes_path = self.config["hotwords"]["notes_path"]

        if os.path.exists(notes_path):
            self._hotword_manager.clear()
            count = self._hotword_manager.load_from_investment_notes(notes_path)
            hotwords = self._hotword_manager.get_hotwords()
            self._asr.set_hotwords(hotwords)
            log(f"Hotwords reloaded: {count} words")
            return {"success": True, "message": f"Reloaded {count} hotwords"}

        return {"success": False, "message": "Notes path not found"}

    def get_status(self) -> dict:
        """获取状态"""
        return {
            "models_loaded": self._models_loaded,
            "transcribing": self._transcribing,
            "text_processor": self._text_processor.get_status(),
        }

    # ============ 内部任务 ============

    async def _audio_loop(self):
        """音频采集循环"""
        log("AUDIO_LOOP: started")
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
                            log(f"AUDIO: got chunk #{chunk_count}, samples={len(chunk.data)}")

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
                log(f"ASR_WORKER: got segment #{asr_count}, dur={segment.end_time - segment.start_time:.2f}s")

                result = await loop.run_in_executor(
                    self._executor,
                    self._asr_recognize,
                    segment
                )

                if result and result.text:
                    log(f"ASR_WORKER: recognized '{result.text[:50]}...'")
                    if not self._punc_queue.full():
                        await self._punc_queue.put(result.text)
                else:
                    log("ASR_WORKER: no text result")

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log(f"ASR worker error: {e}")

    def _asr_recognize(self, segment):
        """ASR 识别"""
        try:
            result = self._asr.recognize(segment)
            if result and result.text:
                log(f"    ASR raw: {repr(result.text[:100])}")
            else:
                log("    ASR raw: (empty)")
            return result
        except Exception as e:
            log(f"ASR error: {e}")
            return None

    async def _punc_worker(self):
        """PUNC 工作线程 - 使用 TextProcessor"""
        log("PUNC_WORKER: started (using TextProcessor)")
        loop = asyncio.get_event_loop()
        punc_count = 0

        while self._running:
            try:
                try:
                    text = await asyncio.wait_for(
                        self._punc_queue.get(), timeout=0.05
                    )
                    if text:
                        log(f"PUNC_WORKER: got text len={len(text)}")
                        punc_count += 1

                        # PUNC 处理
                        punctuated = await loop.run_in_executor(
                            self._executor,
                            self._punc_process,
                            text
                        )

                        if punctuated:
                            # TextProcessor 预处理 + 后处理
                            current_time = time.time()
                            cleaned = self._text_processor.preprocess(punctuated)
                            output_text, status = self._text_processor.postprocess(
                                cleaned, current_time
                            )

                            if output_text:
                                # 构建输出
                                output = self._build_output(output_text, status, current_time)
                                log(f"  PUNC out({status}): {repr(output_text[:80])}")
                                await self._send_output(output)

                except asyncio.TimeoutError:
                    pass

            except Exception as e:
                log(f"Punc worker error: {e}")

    def _punc_process(self, text: str) -> str:
        """标点处理"""
        try:
            if text and self._punc:
                result = self._punc.punctuate(text)
                log(f"PUNC: processed '{text[:30]}...' -> '{result.text[:30]}...'")
                return result.text if result else text
        except Exception as e:
            log(f"Punc error: {e}")
        return text

    def _build_output(self, text: str, status: str, current_time) -> str:
        """构建输出内容"""
        import time as time_module
        text = text.strip()
        if not text:
            return ""
        time_header = time_module.strftime("[%H:%M:%S]", time_module.localtime(current_time))
        return f"{time_header} {text}\n"

    async def _send_output(self, text: str):
        """发送文本输出 (通过回调)"""
        if text and self._on_text_output:
            try:
                log(f">>> TX: {repr(text[:200])}")
                await self._on_text_output(text)
            except Exception as e:
                log(f"Send output error: {e}")
