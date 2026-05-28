"""
run.py - 启动脚本 (v4 三层分离架构)

用法:
    python run.py [--port PORT] [--debug]
"""
import sys
import os
import argparse
import asyncio
import logging
import signal

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

os.environ['MODELSCOPE_CACHE'] = "D:/arvin/obsidian_workpace/models"
os.environ['MODELSCOPE_VERBOSE'] = '0'

for logger_name in ['modelscope', 'funasr', 'root', 'torch']:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)


def parse_args():
    parser = argparse.ArgumentParser(description="FunASR Transcription Server v4")
    parser.add_argument("--port", type=int, default=None,
                        help="WebSocket 端口 (HTTP 端口 = port + 1)")
    parser.add_argument("--debug", action="store_true", default=None,
                        help="调试模式，启用控制台日志")
    return parser.parse_args()


def apply_args_to_config(config: dict, args) -> dict:
    """将 CLI 参数写入 config，并持久化到 settings.json"""
    changed = False

    if args.port is not None:
        config["server"]["port"] = args.port
        changed = True

    if args.debug is not None:
        config["debug"] = args.debug
        changed = True

    if changed:
        import json
        config_path = os.path.join(SCRIPT_DIR, "config", "settings.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    return config


async def run_v4():
    from config import load_config
    from server.api_server import APIServer
    from server.model_server import ModelServer

    args = parse_args()
    config = load_config()
    config = apply_args_to_config(config, args)

    port = config["server"]["port"]
    debug = config.get("debug", True)

    model_server = ModelServer(config=config)
    api_server = APIServer(host=config["server"]["host"], port=port)
    api_server.set_model_server(model_server)

    # 如果非 debug 模式，降低控制台日志级别
    if not debug:
        logging.getLogger("model_server").setLevel(logging.WARNING)
        logging.getLogger("api_server").setLevel(logging.WARNING)

    tasks = [
        asyncio.create_task(api_server.start()),
        asyncio.create_task(model_server.load_models()),
    ]

    print(f"FunASR Transcription Server v4.0")
    print(f"WebSocket: ws://{config['server']['host']}:{port}  "
          f"HTTP: http://{config['server']['host']}:{port + 1}")
    print(f"Debug: {'ON' if debug else 'OFF'}")
    print()

    try:
        await tasks[1]  # 等待模型加载
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

    args = parse_args()
    from config import load_config
    config = load_config()
    config = apply_args_to_config(config, args)
    port = config["server"]["port"]

    if not acquire_single_instance(port=port):
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
