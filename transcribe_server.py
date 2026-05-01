"""
FunASR Transcription Server with ct-punc Punctuation
后台服务，监听端口接收命令并返回转写结果
"""
import sys
import os
import socket
import threading
import signal
import time
import queue
import re

# 设置模型缓存目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_CACHE_DIR = os.path.join(SCRIPT_DIR, "models")
os.environ['MODELSCOPE_CACHE'] = MODEL_CACHE_DIR
os.environ['MODELSCOPE_VERBOSE'] = '0'
os.environ['FUNASR_VERBOSE'] = '0'

# 静默日志
import logging
for logger_name in ['modelscope', 'funasr', 'root', 'torch']:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

# 添加路径并导入 logger
sys.path.insert(0, SCRIPT_DIR)
from logger import get_logger

logger = get_logger(__name__, "SERVER")

# 配置
HOST = '127.0.0.1'
PORT = 9876

class TranscriptionServer:
    def __init__(self):
        self.server_socket = None
        self.client_socket = None
        self.running = False
        self.transcribing = False
        self.asr_model = None  # SenseVoice
        self.punc_model = None  # ct-punc
        self.stream = None
        self.buffer = None
        self.q = None
        self.last_text = ""
        self.punc_enabled = True  # 默认启用标点

    def load_models(self):
        """加载模型"""
        if self.asr_model is None:
            logger.info("Loading ASR model...")
            try:
                from funasr import AutoModel
                self.asr_model = AutoModel(
                    model="iic/SenseVoiceSmall",
                    disable_update=True,
                    ncpu=4,
                )
                logger.info("ASR model loaded")
            except Exception as e:
                logger.error(f"ASR model error: {e}")
                raise

        if self.punc_model is None:
            logger.info("Loading punctuation model...")
            try:
                from funasr import AutoModel
                self.punc_model = AutoModel(
                    model="ct-punc",
                    model_revision="v2.0.4",
                    disable_update=True,
                    ncpu=4,
                )
                logger.info("Punctuation model loaded")
            except Exception as e:
                logger.error(f"Punc model error: {e}")

    def add_punctuation(self, text):
        """使用 ct-punc 添加标点"""
        if not text:
            return text

        if not self.punc_enabled or self.punc_model is None:
            return text

        try:
            result = self.punc_model.generate(input=text, batch_size_s=300)
            if result:
                punctuated = result[0].get('text', '')
                if punctuated:
                    return punctuated
        except Exception as e:
            logger.error(f"Punctuation error: {e}")

        return text

    def start_transcription(self):
        """开始转写"""
        if self.asr_model is None:
            logger.error("ASR model not loaded!")
            return

        import sounddevice as sd
        import numpy as np

        if self.transcribing:
            return

        self.transcribing = True
        self.q = queue.Queue(maxsize=200)
        self.buffer = np.array([], dtype=np.float32)
        self.last_text = ""
        self.last_output_time = time.time()  # 上次输出时间
        self.SILENCE_TIMEOUT = 2.0  # 2秒停顿换行

        def callback(indata, frames, time_info, status):
            if self.transcribing:
                try:
                    self.q.put_nowait(indata.copy())
                except:
                    pass

        SAMPLE_RATE = 16000
        MIN_AUDIO_LEN = 3
        min_samples = SAMPLE_RATE * MIN_AUDIO_LEN

        try:
            self.stream = sd.InputStream(
                device=0,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype='float32',
                callback=callback
            )
            self.stream.start()
            logger.info("Transcription started")
        except Exception as e:
            logger.error(f"Audio stream error: {e}")
            self.transcribing = False
            return

        while self.transcribing:
            try:
                while True:
                    chunk = self.q.get_nowait()
                    self.buffer = np.concatenate([self.buffer, chunk.flatten()])
            except:
                pass

            while len(self.buffer) >= min_samples:
                audio = self.buffer[:min_samples * 2]
                self.buffer = self.buffer[min_samples:]

                try:
                    result = self.asr_model.generate(
                        input=audio.flatten() if audio.ndim > 1 else audio
                    )
                    if result:
                        text = result[0].get("text", "")
                        if text and text.strip():
                            text = re.sub(r'<\|[^|]*\|>', '', text).strip()
                            if text and text != self.last_text:
                                current_time = time.time()
                                time_since_last = current_time - self.last_output_time

                                # 检查是否需要换行（停顿超过2秒）
                                if time_since_last >= self.SILENCE_TIMEOUT and self.last_output_time > 0:
                                    # 发送换行
                                    if self.client_socket:
                                        try:
                                            self.client_socket.send(("\n").encode('utf-8'))
                                        except:
                                            pass

                                # 添加标点
                                punctuated = self.add_punctuation(text)

                                # 发送到客户端
                                if self.client_socket:
                                    try:
                                        self.client_socket.send((punctuated).encode('utf-8'))
                                    except:
                                        pass

                                self.last_text = text
                                self.last_output_time = current_time

                except Exception as e:
                    pass

            time.sleep(0.05)

        # 停止
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except:
                pass
            self.stream = None

        logger.info("Transcription stopped")

    def stop_transcription(self):
        self.transcribing = False

    def handle_client(self, client_socket, addr):
        logger.info(f"Client connected: {addr}")

        self.client_socket = client_socket
        client_socket.settimeout(60)

        # 检查模型是否已加载
        if self.asr_model is None:
            client_socket.send(b"ERROR: ASR model not loaded\n")
            client_socket.close()
            return

        try:
            while True:
                try:
                    data = client_socket.recv(1024).decode('utf-8')
                    if not data:
                        break

                    command = data.strip()
                    logger.debug(f"Command: {command}")

                    if command == "start":
                        thread = threading.Thread(target=self.start_transcription)
                        thread.daemon = True
                        thread.start()
                        client_socket.send(b"OK\n")

                    elif command == "stop":
                        self.stop_transcription()
                        client_socket.send(b"OK\n")

                    elif command == "status":
                        status = "recording" if self.transcribing else "stopped"
                        punc_status = "punc_on" if self.punc_enabled else "punc_off"
                        client_socket.send(f"OK: {status} {punc_status}\n".encode('utf-8'))

                    elif command == "punc_on":
                        self.punc_enabled = True
                        client_socket.send(b"OK\n")

                    elif command == "punc_off":
                        self.punc_enabled = False
                        client_socket.send(b"OK\n")

                    elif command == "quit":
                        self.stop_transcription()
                        client_socket.send(b"OK\n")
                        break

                except socket.timeout:
                    continue

        except Exception as e:
            logger.error(f"Client error: {e}")

        finally:
            self.client_socket = None
            client_socket.close()
            logger.info(f"Client disconnected: {addr}")

    def run(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.server_socket.bind((HOST, PORT))
            self.server_socket.listen(1)
            self.running = True
            logger.info(f"Server listening on {HOST}:{PORT}")

            # 后台加载模型
            loader_thread = threading.Thread(target=self.load_models)
            loader_thread.daemon = True
            loader_thread.start()

            while self.running:
                try:
                    self.server_socket.settimeout(1.0)
                    try:
                        client_socket, addr = self.server_socket.accept()
                        self.handle_client(client_socket, addr)
                    except socket.timeout:
                        continue
                except Exception as e:
                    if self.running:
                        logger.error(f"Server error: {e}")
                        break

        except Exception as e:
            logger.error(f"Server error: {e}")

        finally:
            if self.server_socket:
                self.server_socket.close()

        logger.info("Server stopped")

    def stop(self):
        self.running = False
        self.stop_transcription()
        if self.server_socket:
            self.server_socket.close()

# 全局
server = None

def signal_handler(s, f):
    global server
    logger.info("Shutting down...")
    if server:
        server.stop()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == "__main__":
    server = TranscriptionServer()
    server.run()
