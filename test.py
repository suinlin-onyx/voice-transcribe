"""
test.py - FunASR 服务测试脚本
启动服务、等待模型加载、发送测试音频、验证转录结果
"""
import sys
import os
import socket
import time
import glob
import signal

# 添加项目根目录到路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


def log(msg: str) -> None:
    print(msg)


def kill_existing_server() -> None:
    """清理已存在的服务"""
    log("[Cleanup] Checking port 9876...")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            result = s.connect_ex(('127.0.0.1', 9876))
            if result == 0:
                # 端口被占用，查找并关闭进程
                log("    Port 9876 is in use, attempting to close...")
                import subprocess
                result = subprocess.run(
                    ['netstat', '-ano'],
                    capture_output=True,
                    text=True
                )
                for line in result.stdout.split('\n'):
                    if ':9876' in line and 'LISTENING' in line:
                        parts = line.split()
                        if parts:
                            pid = parts[-1]
                            log(f"    Killing process {pid}")
                            subprocess.run(['taskkill', '/F', '/PID', pid],
                                         capture_output=True)
                        break
    except Exception:
        pass
    time.sleep(1)


def wait_for_models(timeout: int = 90) -> bool:
    """等待模型加载完成"""
    log("[Waiting] Models loading (this takes ~30s)...")
    start = time.time()

    while time.time() - start < timeout:
        # 检查日志文件
        log_files = glob.glob(os.path.join(SCRIPT_DIR, "logs", "server_*.log"))
        if log_files:
            latest = max(log_files, key=os.path.getmtime)
            try:
                with open(latest, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if "All models loaded" in content:
                        elapsed = int(time.time() - start)
                        log(f"[OK] All models loaded ({elapsed}s)")
                        return True
            except Exception:
                pass
        time.sleep(1)

    log("[WARN] Timeout waiting for models")
    return False


def wait_and_test_server(timeout: int = 90) -> bool:
    """等待服务器就绪并验证"""
    log("[Waiting] Models loading (this takes ~30s)...")
    start = time.time()

    while time.time() - start < timeout:
        # 检查日志
        log_files = glob.glob(os.path.join(SCRIPT_DIR, "logs", "server_*.log"))
        ready = False
        if log_files:
            latest = max(log_files, key=os.path.getmtime)
            try:
                with open(latest, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if "All models loaded" in content:
                        ready = True
            except Exception:
                pass

        if ready:
            # 额外等2秒确保就绪
            time.sleep(2)
            elapsed = int(time.time() - start)
            log(f"[OK] Server ready ({elapsed}s)")

            # 发送测试命令验证
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect(('127.0.0.1', 9876))
                sock.sendall(b'start\n')
                resp = sock.recv(1024).decode().strip()
                sock.close()

                if "OK" in resp:
                    log("[OK] Server accepts commands")
                    return True
                else:
                    log(f"[WARN] Server responded: {resp}")
            except Exception as e:
                log(f"[WARN] Server test failed: {e}")

        time.sleep(1)

    log("[ERROR] Server not ready")
    return False


def run_socket_test(timeout: int = 60) -> list:
    """运行 Socket 测试"""
    log("[Test] Connecting to 127.0.0.1:9876...")
    received = []

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(('127.0.0.1', 9876))
        log("[OK] Connected")

        # 发送开始命令
        log("\n[Test] Sending START command...")
        sock.sendall(b'start\n')
        resp = sock.recv(1024).decode().strip()
        log(f"    Response: {resp}")

        # 等待转录数据
        log(f"\n[Test] Waiting for transcription ({timeout}s)...")
        log("    Speak into your microphone now!")

        sock.settimeout(timeout)
        start = time.time()

        while True:
            try:
                data = sock.recv(4096)
                if data:
                    text = data.decode('utf-8').strip()
                    if text:
                        elapsed = int(time.time() - start)
                        log(f"    [{elapsed}s] RX: {text[:100]}")
                        received.append(text)
                else:
                    break
            except socket.timeout:
                log("    (timeout - no more data)")
                break

        sock.close()

    except ConnectionRefusedError:
        log("[ERROR] Connection refused - server not running")
    except Exception as e:
        log(f"[ERROR] {e}")

    return received


def main():
    log("=" * 50)
    log("  FunASR Voice Transcription Test")
    log("=" * 50)
    log("")

    # 1. 清理已存在的服务
    kill_existing_server()

    # 2. 启动服务
    log("\n[Starting] FunASR Transcription Server...")
    import subprocess

    server_process = subprocess.Popen(
        [sys.executable, "run.py"],
        cwd=SCRIPT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    log(f"[OK] Server started (PID: {server_process.pid})")

    try:
        # 3. 等待服务器就绪并验证
        if not wait_and_test_server():
            log("\n[ERROR] Server not ready")
            return

        # 4. 运行测试
        received = run_socket_test(timeout=15)

        # 7. 输出结果
        log("\n" + "=" * 50)
        if received:
            log("  [SUCCESS] Received transcription!")
            log("=" * 50)
            log("\nFinal text:")
            for i, text in enumerate(received, 1):
                log(f"  {i}. {text}")
        else:
            log("  [WARNING] No transcription received")
            log("=" * 50)
            log("  Possible reasons:")
            log("  - No audio detected (speak louder)")
            log("  - VAD threshold too high")
            log("  - silence_timeout still too short")
            log("  - Microphone not working")

    finally:
        # 8. 清理
        log("\n[Cleanup] Stopping server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
        log("[OK] Server stopped")


if __name__ == "__main__":
    main()
