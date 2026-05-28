"""
run.py - 启动脚本 (v4 三层分离架构)
"""
import sys
import os
import asyncio
import logging
import signal

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

os.environ['MODELSCOPE_CACHE'] = "D:/arvin/obsidian_workpace/models"
os.environ['MODELSCOPE_VERBOSE'] = '0'

for logger_name in ['modelscope', 'funasr', 'root', 'torch']:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)


async def run_v4():
    from server.api_server import APIServer
    from server.model_server import ModelServer

    model_server = ModelServer()
    api_server = APIServer(host="127.0.0.1", port=9886)
    api_server.set_model_server(model_server)

    tasks = [
        asyncio.create_task(api_server.start()),
        asyncio.create_task(model_server.load_models()),
    ]

    print("FunASR Transcription Server v4.0")
    print(f"WebSocket: ws://127.0.0.1:9886  HTTP: http://127.0.0.1:9887")
    print()

    try:
        await tasks[1]  # 等待模型加载（终端有进度条）
        model_server.start()
        await tasks[0]
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Server error: {e}")
    finally:
        await api_server.stop()
        await model_server.stop()
        print("Server stopped")


def main():
    from utils.single_instance import acquire_single_instance
    if not acquire_single_instance():
        sys.exit(1)

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


if __name__ == "__main__":
    main()
