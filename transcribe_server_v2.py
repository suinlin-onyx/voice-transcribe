"""
transcribe_server_v2.py - 分层架构转录服务器
全解耦合设计
"""
import sys
import os
import socket
import threading
import signal
import time
import queue
import logging

# 静默日志
for logger_name in ['modelscope', 'funasr', 'root', 'torch']:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

# 添加路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# 导入 logger
from logger import get_logger

logger = get_logger(__name__, "SERVER")

# 设置模型缓存
MODEL_CACHE_DIR = os.path.join(SCRIPT_DIR, "models")
os.environ['MODELSCOPE_CACHE'] = MODEL_CACHE_DIR
os.environ['MODELSCOPE_VERBOSE'] = '0'

from interfaces import (
    AudioChunk, SpeechSegment, TextResult, PunctuatedText,
    TransportCommand
)
from interfaces.socket_transport import ServerSocket, SocketTransport

from processors.audio_source import MicrophoneSource
from processors.vad_processor import VADConfig, create_vad_processor
from processors.asr_processor import create_asr_processor
from processors.punc_processor import create_punc_processor
from processors.hotword_manager import HotwordManager
from processors.output_buffer import OutputBuffer


class TranscribeServerV2:
    """分层架构转录服务器"""

    def __init__(self, config: dict = None):
        # 配置
        self.config = config or self._default_config()

        # 各层处理器 (延迟初始化)
        self.audio_source: MicrophoneSource = None
        self.vad_processor = None
        self.asr_processor = None
        self.punc_processor = None
        self.hotword_manager = None
        self.output_buffer: OutputBuffer = None

        # 通信层
        self.server_socket = ServerSocket(
            self.config["server"]["host"],
            self.config["server"]["port"]
        )
        self.transport: SocketTransport = None

        # 状态
        self.running = False
        self.transcribing = False
        self.models_loaded = False

        # 处理队列
        self.result_queue = queue.Queue(maxsize=100)

        # 锁
        self._lock = threading.Lock()

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
                "enabled": True,
                "mode": "sensevoice",  # sensevoice | fsmn_vad | simple
                "threshold": 0.5,
                "min_speech_duration": 0.3,
                "max_speech_duration": 10.0,
                "silence_timeout": 2.0,
            },
            "asr": {
                "model": "iic/SenseVoiceSmall",
                "device": "cuda",
                "hotwords_enabled": True,
            },
            "punc": {
                "enabled": True,
                "model": "ct-punc",
            },
            "output": {
                "batch_interval": 0.3,
                "batch_size": 50,
            },
            "hotwords": {
                "load_from_notes": True,
                "notes_path": "D:/arvin/obsidian_workpace/arvin-notes/00.raw/01.投资研究/",
            },
        }

    def _init_processors(self):
        """初始化各层处理器"""
        # 音频源
        self.audio_source = MicrophoneSource(
            sample_rate=self.config["audio"]["sample_rate"],
            channels=self.config["audio"]["channels"],
            device=self.config["audio"]["device"],
        )

        # 输出缓冲
        self.output_buffer = OutputBuffer(
            batch_interval=self.config["output"]["batch_interval"],
            batch_size=self.config["output"]["batch_size"],
        )

        # 热词管理
        self.hotword_manager = HotwordManager()

        # 标点处理 (立即初始化，因为它很快)
        self.punc_processor = create_punc_processor(
            self.config["punc"]["model"],
            self.config["punc"]["enabled"]
        )

    def load_models(self):
        """加载模型 (后台执行)"""
        logger.info("Loading models...")

        try:
            # ASR 模型
            self.asr_processor = create_asr_processor(
                self.config["asr"]["model"],
                self.config["asr"]["device"]
            )
            self.asr_processor.load_model()

            # VAD 处理器
            vad_config = VADConfig(
                mode=self.config["vad"]["mode"],
                silence_timeout=self.config["vad"]["silence_timeout"],
                max_speech_duration=self.config["vad"]["max_speech_duration"],
            )
            self.vad_processor = create_vad_processor(
                self.config["vad"]["mode"],
                asr_model=self.asr_processor.model,
                config=vad_config
            )

            # 标点模型
            self.punc_processor.load_model()

            # 热词
            if self.config["hotwords"]["load_from_notes"]:
                notes_path = self.config["hotwords"]["notes_path"]
                if os.path.exists(notes_path):
                    count = self.hotword_manager.load_from_investment_notes(notes_path)
                    logger.info(f"Loaded {count} hotwords from notes")

                    # 注入 ASR
                    hotwords = self.hotword_manager.get_hotwords()
                    self.asr_processor.set_hotwords(hotwords)

            self.models_loaded = True
            logger.info("All models loaded")

        except Exception as e:
            logger.error(f"Model loading error: {e}")
            raise

    def _setup_pipeline(self):
        """设置处理管道"""

        def on_audio(chunk: AudioChunk):
            """音频数据 -> VAD -> ASR -> 标点 -> 输出"""
            if not self.transcribing:
                return

            try:
                # VAD 处理
                segments = self.vad_processor.process(chunk.data)

                for segment in segments:
                    # ASR 识别
                    result = self.asr_processor.recognize(segment)

                    if result.text:
                        # 标点处理
                        punctuated = self.punc_processor.punctuate(result.text)

                        # 添加到输出缓冲
                        self.output_buffer.push(punctuated.text)

                        # 发送换行信号 (段落结束)
                        if segment.is_final:
                            self.output_buffer.push_with_newline("")

            except Exception as e:
                logger.error(f"Pipeline error: {e}")

        # 注册音频回调
        self.audio_source.on_audio(on_audio)

        # 设置发送回调
        def send_to_client(text: str) -> bool:
            if self.transport:
                return self.transport.send(text)
            return False

        self.output_buffer.set_send_callback(send_to_client)

    def start_transcription(self):
        """开始转写"""
        if not self.models_loaded:
            logger.warning("Models not loaded!")
            return False

        with self._lock:
            if self.transcribing:
                return False

            self.transcribing = True
            self.audio_source.start()
            logger.info("Transcription started")

            # 启动输出处理线程
            self._output_thread = threading.Thread(target=self._process_output, daemon=True)
            self._output_thread.start()

        return True

    def _process_output(self):
        """处理输出队列"""
        last_flush = time.time()

        while self.transcribing:
            try:
                # 检查缓冲
                if self.output_buffer.should_flush():
                    text = self.output_buffer.flush()
                    if text and self.transport:
                        self.transport.send(text)

                # 换行信号处理 (静音超时)
                if self.transcribing and self.vad_processor:
                    # 简单检查：每100ms检查一次
                    pass

                time.sleep(0.05)

            except Exception as e:
                logger.error(f"Output error: {e}")

    def stop_transcription(self):
        """停止转写"""
        with self._lock:
            if not self.transcribing:
                return

            self.transcribing = False

            if self.audio_source:
                self.audio_source.stop()

            # 刷新缓冲
            if self.output_buffer:
                text = self.output_buffer.flush()
                if text and self.transport:
                    self.transport.send(text)

            logger.info("Transcription stopped")

    def handle_client(self, transport: SocketTransport):
        """处理客户端连接"""
        self.transport = transport
        logger.info("Client connected")

        # 设置命令回调
        def on_command(cmd: TransportCommand):
            if cmd.command == "start":
                success = self.start_transcription()
                transport.send("OK\n" if success else "ERROR\n")
            elif cmd.command == "stop":
                self.stop_transcription()
                transport.send("OK\n")
            elif cmd.command == "hotwords_reload":
                self._reload_hotwords()
                transport.send("OK\n")
            elif cmd.command == "quit":
                self.stop_transcription()
                transport.send("OK\n")

        transport.set_command_callback(on_command)

        # 循环处理命令
        while self.running:
            cmd = transport.recv()
            if cmd:
                on_command(cmd)
                if cmd.command == "quit":
                    break

            time.sleep(0.1)

        transport.close()

    def _reload_hotwords(self):
        """重新加载热词"""
        if self.hotword_manager and self.asr_processor:
            notes_path = self.config["hotwords"]["notes_path"]
            if os.path.exists(notes_path):
                self.hotword_manager.clear()
                count = self.hotword_manager.load_from_investment_notes(notes_path)
                hotwords = self.hotword_manager.get_hotwords()
                self.asr_processor.set_hotwords(hotwords)
                logger.info(f"Reloaded {count} hotwords")

    def run(self):
        """运行服务器"""
        self._init_processors()

        # 后台加载模型
        loader_thread = threading.Thread(target=self.load_models, daemon=True)
        loader_thread.start()

        # 启动服务器
        try:
            self.server_socket.start()
            self.running = True

            while self.running:
                transport = self.server_socket.accept()
                if transport:
                    self.handle_client(transport)

        except Exception as e:
            logger.error(f"Server error: {e}")
        finally:
            self.stop()
            self.server_socket.stop()

    def stop(self):
        """停止服务器"""
        self.running = False
        self.stop_transcription()


# 全局实例
server = None


def signal_handler(s, f):
    global server
    logger.info("Shutting down...")
    if server:
        server.stop()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    server = TranscribeServerV2()
    server.run()
