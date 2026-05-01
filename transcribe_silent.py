"""
Silent transcription - FunASR with punctuation
"""
import sys
import os
import signal
import time
import queue
import threading
import re

# 设置模型缓存目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_CACHE_DIR = os.path.join(SCRIPT_DIR, "models")
os.environ['MODELSCOPE_CACHE'] = MODEL_CACHE_DIR
os.environ['MODELSCOPE_VERBOSE'] = '0'
os.environ['FUNASR_VERBOSE'] = '0'

# 完全静默所有日志
sys.stdout = open(os.devnull, 'w')
sys.stderr = open(os.devnull, 'w')

import logging
for logger_name in ['modelscope', 'funasr', 'root', 'torch']:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

stop_event = threading.Event()

def signal_handler(s, f):
    stop_event.set()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def main():
    import sounddevice as sd
    import numpy as np

    # 加载 FunASR 模型（包含 VAD + Punctuation）
    from funasr import AutoModel

    print("Loading model...", file=sys.__stderr__)
    model = AutoModel(
        model="iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8405-pytorch",
        vad_model="fsmn-vad",
        vad_model_revision="v2.0.4",
        punc_model="ct-punc",
        punc_model_revision="v2.0.4",
        disable_update=True,
        ncpu=4,
    )
    print("Model loaded", file=sys.__stderr__)

    SAMPLE_RATE = 16000
    MIN_AUDIO_LEN = 2  # 缩短到2秒，更及时
    min_samples = SAMPLE_RATE * MIN_AUDIO_LEN

    q = queue.Queue(maxsize=200)
    buffer = np.array([], dtype=np.float32)
    last_text = ""

    def callback(indata, frames, time, status):
        if not stop_event.is_set():
            try:
                q.put_nowait(indata.copy())
            except:
                pass

    stream = sd.InputStream(device=0, samplerate=SAMPLE_RATE,
                           channels=1, dtype='float32', callback=callback)
    stream.start()

    while not stop_event.is_set():
        try:
            while True:
                chunk = q.get_nowait()
                buffer = np.concatenate([buffer, chunk.flatten()])
        except:
            pass

        if len(buffer) >= min_samples:
            audio = buffer[:min_samples * 3]  # 取3秒
            buffer = buffer[min_samples:]

            try:
                result = model.generate(input=audio.flatten() if audio.ndim > 1 else audio)
                if result:
                    text = result[0].get("text", "")
                    if text and text.strip():
                        # 清理标签
                        text = re.sub(r'<\|[^|]*\|>', '', text).strip()
                        if text and text != last_text:
                            # 输出到管道
                            sys.stdout = sys.__stdout__
                            print(text, flush=True)
                            sys.stdout = open(os.devnull, 'w')
                            last_text = text
            except Exception as e:
                pass

        time.sleep(0.05)

    stream.stop()
    stream.close()

if __name__ == "__main__":
    main()
