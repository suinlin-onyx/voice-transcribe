"""
run.py - 启动脚本
支持 v3 (asyncio) 和 v4 (三层分离架构)
"""
import sys
import os

# 添加项目根目录到路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# 设置环境变量
os.environ['MODELSCOPE_CACHE'] = os.path.join(SCRIPT_DIR, "models")
os.environ['MODELSCOPE_VERBOSE'] = '0'

import logging

# 静默日志
for logger_name in ['modelscope', 'funasr', 'root', 'torch']:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)


def main():
    import signal
    import asyncio

    # 单例检查
    from single_instance import acquire_single_instance
    if not acquire_single_instance():
        sys.exit(1)

    version = os.environ.get('TRANSCRIBE_VERSION', 'v4')

    if version == 'v2':
        print("Starting v2 (threading)...")
        from transcribe_server_v2 import TranscribeServerV2
        from config import DEFAULT_CONFIG

        server = TranscribeServerV2(DEFAULT_CONFIG)

        def signal_handler(s, f):
            print("\nShutting down...")
            server.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        server.run()

    elif version == 'v3':
        print("Starting v3 (asyncio)...")
        from transcribe_server_v3 import TranscribeServerV3
        from config import DEFAULT_CONFIG

        server = TranscribeServerV3(DEFAULT_CONFIG)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def signal_handler(s, f):
            print("\nShutting down...")
            for task in asyncio.all_tasks(loop):
                task.cancel()
            loop.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        print("=" * 50)
        print("FunASR Transcription Server v3.0 (asyncio)")
        print("=" * 50)

        try:
            loop.run_until_complete(server.run())
        finally:
            loop.close()

    elif version == 'v4':
        print("Starting v4 (三层分离架构)...")

        async def run_v4():
            from api_server import APIServer
            from model_server import ModelServer

            model_server = ModelServer()
            api_server = APIServer(host="127.0.0.1", port=9876)
            api_server.set_model_server(model_server)

            tasks = [
                asyncio.create_task(api_server.start()),
                asyncio.create_task(model_server.load_models()),
            ]

            print("=" * 50)
            print("FunASR Transcription Server v4.0 (三层分离架构)")
            print("=" * 50)
            print("WebSocket: ws://127.0.0.1:9876")
            print("HTTP:     http://127.0.0.1:9877")
            print("等待模型加载...")

            try:
                await tasks[1]  # 等待模型加载
                print("模型加载完成")
                model_server.start()
                await tasks[0]  # 保持运行
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"Server error: {e}")
            finally:
                await api_server.stop()
                await model_server.stop()
                print("Server stopped")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def signal_handler(s, f):
            print("\nShutting down...")
            for task in asyncio.all_tasks(loop):
                task.cancel()
            loop.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        try:
            loop.run_until_complete(run_v4())
        finally:
            loop.close()

    else:
        print(f"Unknown version: {version}")
        sys.exit(1)


if __name__ == "__main__":
    main()
