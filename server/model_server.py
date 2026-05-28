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
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
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

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_format = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
console_handler.setFormatter(console_format)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

log = logger.info
log_debug = logger.debug
log_error = logger.error

# 环境设置
os.environ['MODELSCOPE_CACHE'] = "D:/arvin/obsidian_workpace/models"
os.environ['MODELSCOPE_VERBOSE'] = '0'

import logging as third_party_log
for logger_name in ['modelscope', 'funasr', 'root', 'torch']:
    third_party_log.getLogger(logger_name).setLevel(logging.CRITICAL)

from processors.audio_source import MicrophoneSource, MultiSource, LoopbackSource
from processors.vad_processor import VADConfig, create_vad_processor
from processors.asr_processor import create_asr_processor
from processors.punc_processor import create_punc_processor
from processors.hotword_manager import HotwordManager
from processors.text_processor import TextProcessor
from config import load_config


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
        self.config = config or load_config()

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

        # Text Processor (从配置加载)
        tp_config = self.config.get("text_processor", {})
        self._text_processor = TextProcessor(
            punctuation=tp_config.get("punctuation", "。！？.!?"),
            split_punctuation=tp_config.get("split_punctuation", "，。！？.!?"),
            silence_threshold=tp_config.get("silence_threshold", 2.0),
            max_accumulate=tp_config.get("max_accumulate", 6.0),
            check_interval=tp_config.get("check_interval", 3.0),
            dedup_window=tp_config.get("dedup_window", 5.0),
        )

        # 状态
        self._running = False
        self._transcribing = False
        self._models_loaded = False
        self._load_start: Optional[float] = None
        self._is_first_line = True  # 首行标志

        # 回调 (API Server 设置)
        self._on_text_output: Optional[callable] = None
        self._on_progress: Optional[callable] = None

        # 终端状态显示
        self._term_status = ""  # 当前终端状态，避免重复打印

        # 任务
        self._tasks: list = []

    def set_output_callback(self, callback: callable):
        """设置文本输出回调 (API Server 调用)"""
        self._on_text_output = callback

    def set_progress_callback(self, callback: callable):
        """设置加载进度回调 (API Server 调用)"""
        self._on_progress = callback

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
            asyncio.create_task(self._text_processor_timer()),
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

        source_type = self.config["audio"].get("source", "microphone")
        sample_rate = self.config["audio"]["sample_rate"]
        channels = self.config["audio"]["channels"]

        if source_type == "loopback":
            self._audio_source = LoopbackSource(
                sample_rate=sample_rate,
                channels=channels,
            )
            log("Audio: LoopbackSource (system audio output)")
        elif source_type == "microphone":
            devices = self.config["audio"].get("devices")
            if devices:
                self._audio_source = MultiSource()
                for device in devices:
                    source = MicrophoneSource(
                        sample_rate=sample_rate,
                        channels=channels,
                        device=device,
                    )
                    self._audio_source.add_source(source)
                log(f"Audio: MultiSource with {len(devices)} devices")
            else:
                self._audio_source = MicrophoneSource(
                    sample_rate=sample_rate,
                    channels=channels,
                    device=self.config["audio"]["device"],
                )
                log(f"Audio: MicrophoneSource (device={self.config['audio']['device']})")
        else:
            raise ValueError(f"Unknown audio source type: {source_type}")

        self._audio_source.on_audio(on_audio)
        self._hotword_manager = HotwordManager()

    async def load_models(self):
        """加载所有模型，通过 _on_progress 回调推送进度"""
        self._init_audio()

        self._load_start = time.time()
        SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

        def progress_line(step: str, done: bool = False):
            elapsed = int(time.time() - self._load_start)
            if done:
                print(f"\r  ✓ {step} ({elapsed}s)")
            else:
                i = int(time.time() * 10) % len(SPINNER)
                print(f"\r  {SPINNER[i]} {step} ({elapsed}s)", end="", flush=True)

        def push_progress(step: str, message: str):
            """终端进度 + WS 推送"""
            elapsed = int(time.time() - self._load_start)
            progress_line(message)
            if self._on_progress:
                asyncio.ensure_future(self._on_progress(step, message, elapsed))

        loop = asyncio.get_event_loop()

        try:
            # ASR
            engine = self.config["asr"].get("engine", "funasr")
            engine_label = "ONNX" if engine == "onnx" else "PyTorch"
            push_progress("asr_loading", f"加载 ASR 模型 ({engine_label})...")
            self._asr = create_asr_processor(
                self.config["asr"]["model_path"],
                self.config["asr"]["device"],
                engine=engine,
            )
            await loop.run_in_executor(self._executor, self._asr.load_model)
            progress_line(f"ASR 模型就绪 ({engine_label})", done=True)

            # VAD
            vad_config = VADConfig(
                mode="fsmn_vad",
                min_speech_duration=self.config["vad"]["min_speech_duration"],
                max_speech_duration=self.config["vad"]["max_speech_duration"],
                silence_timeout=self.config["vad"]["silence_timeout"],
                pre_roll_duration=self.config["vad"].get("pre_roll_duration", 0.1),
                post_roll_duration=self.config["vad"].get("post_roll_duration", 0.1),
            )
            self._vad = create_vad_processor(
                "fsmn_vad",
                model_path=self.config["vad"].get("model_path", ""),
                asr_model=self._asr.model,
                config=vad_config,
            )

            # PUNC
            push_progress("punc_loading", "加载标点模型...")
            self._punc = create_punc_processor(
                self.config["punc"]["model_path"],
                self.config["punc"]["enabled"]
            )
            await loop.run_in_executor(self._executor, self._punc.load_model)
            progress_line("标点模型就绪", done=True)

            # 热词
            if self.config["hotwords"]["load_from_notes"]:
                notes_path = self.config["hotwords"]["notes_path"]
                if os.path.exists(notes_path):
                    push_progress("hotwords_loading", "加载热词...")
                    await loop.run_in_executor(
                        self._executor,
                        self._hotword_manager.load_from_investment_notes,
                        notes_path
                    )
                    progress_line("热词就绪", done=True)

            self._models_loaded = True
            total_time = int(time.time() - self._load_start)
            print(f"\n  全部就绪 ({total_time}s)")

        except Exception as e:
            print()  # 换行，避免覆盖 spinner
            log(f"Model loading error: {e}")
            raise

    def start_transcribing(self):
        """开始转写"""
        if not self._models_loaded:
            elapsed = int(time.time() - self._load_start) if self._load_start else 0
            return {"success": False, "message": f"Models still loading ({elapsed}s elapsed)"}

        self._transcribing = True
        self._is_first_line = True
        if self._vad:
            self._vad.reset()
        self._audio_source.start()
        return {"success": True, "message": "Transcription started"}

    def stop_transcribing(self):
        """停止转写"""
        self._transcribing = False
        self._is_first_line = True

        # 1. 停止音频采集
        self._audio_source.stop()

        # 2. 清空队列
        while not self._asr_queue.empty():
            try:
                self._asr_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        while not self._punc_queue.empty():
            try:
                self._punc_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # 3. 重置 VAD / TextProcessor，清除残留音频缓冲
        if self._vad:
            self._vad.reset()
        self._text_processor.reset()

        log("Transcription stopped and pipeline cleared")
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
        chunk_count = 0
        silence_count = 0  # 连续静音计数
        last_status = None

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

                        segments = self._vad.process(chunk.data)

                        if segments:
                            if last_status != "speaking":
                                print("🔊 语音中")
                                last_status = "speaking"
                            silence_count = 0

                            for seg in segments:
                                if self._asr_queue.full():
                                    try:
                                        self._asr_queue.get_nowait()
                                    except asyncio.QueueEmpty:
                                        pass
                                await self._asr_queue.put(seg)
                        else:
                            silence_count += 1
                            if last_status == "speaking" and silence_count > 50:
                                print("🔇 等待语音...")
                                last_status = "silent"

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
                        # 传递 (文本, 是否截断) 元组
                        await self._punc_queue.put((result.text, segment.force_ended))
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

        while self._running:
            try:
                try:
                    # 接收 (文本, 是否截断) 元组
                    item = await asyncio.wait_for(
                        self._punc_queue.get(), timeout=0.05
                    )
                    if item:
                        text, force_ended = item  # 解包
                        log(f"PUNC_WORKER: got text len={len(text)}, force_ended={force_ended}")

                        if not text:
                            #如果未返回文本. 则跳过
                            continue

                        # 拼接上一次语别留下的不完整行尾作行首.
                        log(f"拼接前: text: '{text}', force_ended='{force_ended}'")
                        text = self._text_processor.getHeader() + text
                        self._text_processor.clear_header()
                        log(f"拼接后: text: '{text}', force_ended='{force_ended}'")

                        # 拼接完成后, 进行punc处理, 更新标点
                        punctuated = await loop.run_in_executor(
                            self._executor,
                            self._punc_process,
                            text
                        )

                        log(f"更新标点后: text: '{punctuated}', force_ended='{force_ended}'")
                        current_time = time.time()

                        # 分割句子中的最后一句(最后两个标点间的内容), 作为下一句的行首, 确保语义完整.
                        if force_ended:
                            punctuated, status = self._text_processor.append_truncated(punctuated, current_time)
                            log(f"分割句子行尾做header后: punctuated: '{punctuated}', handler: '{self._text_processor.getHeader()}', status='{status}'")

                        # TextProcessor 清理以及整理字串的错误字符, 格式等.
                        cleaned = self._text_processor.preprocess(punctuated)

                        # 跨 segment 重叠去重（修复 VAD pre-roll 导致的文本重复）
                        cleaned = self._text_processor.dedup_overlap(cleaned)

                        # 检查是否需要换行
                        output_text, status = self._text_processor.tick(cleaned, current_time)
                        log(f"预备输出: output_text: '{output_text}', status='{status}'")

                        if output_text:
                            # 检查是否需要添加行首, 是否需要换行
                            output = self._build_output(output_text, status, current_time)
                            log(f"  PUNC out({status}): {repr(output_text[:80])}, output: '{output}'")
                            await self._send_output(output)

                except asyncio.TimeoutError:
                    pass

            except Exception as e:
                log(f"Punc worker error: {e}")

    async def _text_processor_timer(self):
        """TextProcessor 定时器 - 每3秒强制检查输出"""
        log("TEXT_PROCESSOR_TIMER: started")
        TEXT_CHECK_INTERVAL = 3.0

        while self._running:
            try:
                await asyncio.sleep(TEXT_CHECK_INTERVAL)

                current_time = time.time()
                # 仅做强制输出，不参与正常条件判断
                output_text, status = self._text_processor.tick_force(current_time)

                if output_text:
                    output = self._build_output(output_text, status, current_time)
                    log(f"  TIMER out({status}): {repr(output_text[:80])}")
                    await self._send_output(output)

            except Exception as e:
                log(f"Text processor timer error: {e}")

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
        """构建输出内容

        Args:
            text: 要输出的文本
            status: "newline" / "continuous"
            current_time: 当前时间戳

        Returns:
            - "newline": \n + 时间头 + 文本
            - "continuous": 文本（无时间头，无换行）
        """
        import time as time_module
        text = text.strip()
        if not text:
            return ""

        log(f"_build_output, 准备为下一行换行做冷血 status: '{status}', text:'{text}', is_first={self._is_first_line}")
        if status == "newline":
            time_header = time_module.strftime("[%H:%M:%S]", time_module.localtime(current_time))
            if self._is_first_line:
                self._is_first_line = False
                return f"{time_header} {text}"  # 首行：时间头在前
            else:
                return f"{text}\n{time_header} "  # 后续行：时间头在上一行末尾
        else:
            # continuous: 不加时间头，不加换行
            return text

    async def _send_output(self, text: str):
        """发送文本输出 (通过回调)"""
        if text and self._on_text_output:
            try:
                # 去除时间头显示纯文本
                display = text.replace("\n", " ").strip()
                print(f"📝 {display}")
                await self._on_text_output(text)
            except Exception as e:
                log(f"Send output error: {e}")
