"""
Real-time voice transcription - Silent mode
Only outputs recognized text, no progress bars or logs
"""

import sys
import os
import signal
import argparse
import time
import threading

# 设置模型缓存目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from processors._model_path import resolve_model_path

# 静默模式 - 禁用所有日志
import logging
logging.basicConfig(level=logging.CRITICAL)

stop_event = threading.Event()

def signal_handler(signum, frame):
    stop_event.set()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mic-only", action="store_true")
    parser.add_argument("--system-device", type=int)
    parser.add_argument("--mic-device", type=int)
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--min-audio-len", type=int, default=3)
    args = parser.parse_args()

    import sounddevice as sd
    import numpy as np
    from funasr import AutoModel

    # 加载模型
    model_path = resolve_model_path("iic/SenseVoiceSmall")
    print(f"Using model: {model_path}")
    model = AutoModel(
        model=model_path,
        disable_update=True,
        ncpu=4,
    )

    # 音频配置
    SAMPLE_RATE = args.sample_rate
    min_samples = SAMPLE_RATE * args.min_audio_len

    # 查找设备
    devices = sd.query_devices()
    mic_device = args.mic_device or 0

    mic_queue = queue.Queue(maxsize=200) if False else __import__('queue').Queue(maxsize=200)
    mic_buffer = np.array([], dtype=np.float32)

    def mic_callback(indata, frames, time, status):
        if not stop_event.is_set():
            try:
                mic_queue.put_nowait(indata.copy())
            except:
                pass

    # 启动录音
    stream = sd.InputStream(device=mic_device, samplerate=SAMPLE_RATE, channels=1,
                          dtype='float32', callback=mic_callback)
    stream.start()

    output_count = 0

    while not stop_event.is_set():
        # 收集音频
        try:
            while True:
                chunk = mic_queue.get_nowait()
                mic_buffer = np.concatenate([mic_buffer, chunk.flatten()])
        except:
            pass

        # 达到最小长度则识别
        if len(mic_buffer) >= min_samples:
            audio = mic_buffer[:min_samples * 2]
            mic_buffer = mic_buffer[min_samples:]

            try:
                result = model.generate(input=audio.flatten() if audio.ndim > 1 else audio)
                if result:
                    text = result[0].get("text", "")
                    if text and text.strip():
                        # 只输出纯文本
                        print(text, flush=True)
                        output_count += 1
            except:
                pass

        time.sleep(0.05)

    # 清理
    stream.stop()
    stream.close()

if __name__ == "__main__":
    main()
