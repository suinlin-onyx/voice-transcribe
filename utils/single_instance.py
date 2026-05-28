# single_instance.py
"""
跨平台单例检查模块（按端口锁定）

Windows: 使用 msvcrt 文件锁 (标准库)
Unix/macOS: 使用 fcntl.flock 文件锁

使用方式:
    from utils.single_instance import acquire_single_instance

    if not acquire_single_instance(port=9876):
        sys.exit(1)

锁文件命名: .voice-transcribe-{port}.lock
不同端口可以同时运行，同一端口只允许一个实例。
"""
import sys
import os


def acquire_single_instance(port: int = 0):
    """
    获取单例锁（按端口）

    Args:
        port: 服务端口号，用于区分不同实例

    Returns:
        True - 获取锁成功，服务可以启动
        False - 已有实例运行，服务不应启动
    """
    if sys.platform == "win32":
        return _acquire_windows(port)
    else:
        return _acquire_unix(port)


def _get_pid_file_path(port: int = 0):
    """获取 PID 文件路径（按端口）"""
    suffix = f"-{port}" if port else ""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f".voice-transcribe{suffix}.lock")


def _is_process_running(pid: int) -> bool:
    """检查进程是否存活"""
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _acquire_windows(port: int):
    """Windows: 使用 msvcrt 文件锁"""
    import msvcrt
    import atexit

    pid_file = _get_pid_file_path(port)

    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f:
                old_pid = int(f.read().strip())
            if _is_process_running(old_pid):
                print(f"Error: 服务已在运行 (PID: {old_pid}, port: {port})，请先关闭现有实例")
                return False
            else:
                print(f"Warning: 发现残留锁文件 (PID: {old_pid})，已清理")
                try:
                    os.remove(pid_file)
                except:
                    pass
        except (ValueError, IOError):
            try:
                os.remove(pid_file)
            except:
                pass

    try:
        f = open(pid_file, 'w+')
    except Exception as e:
        print(f"Error: 无法创建锁文件 ({e})")
        return False

    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        f.seek(0)
        f.write(str(os.getpid()))
        f.flush()

        def cleanup():
            try:
                f.close()
            except:
                pass
            try:
                if os.path.exists(pid_file):
                    os.remove(pid_file)
            except:
                pass

        atexit.register(cleanup)
        return True

    except IOError:
        f.close()
        print(f"Error: 服务已在运行 (port: {port})，请先关闭现有实例")
        return False


def _acquire_unix(port: int):
    """Unix/macOS: 使用 flock 文件锁"""
    import fcntl
    import atexit

    pid_file = _get_pid_file_path(port)

    try:
        f = open(pid_file, 'w')
    except Exception as e:
        print(f"Warning: 无法打开锁文件 ({e})")
        return True

    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(str(os.getpid()))
        f.flush()

        def cleanup():
            try:
                f.close()
                if os.path.exists(pid_file):
                    os.remove(pid_file)
            except:
                pass

        atexit.register(cleanup)
        return True

    except IOError:
        f.close()
        print(f"Error: 服务已在运行 (port: {port})，请先关闭现有实例")
        return False


if __name__ == "__main__":
    if acquire_single_instance(port=9876):
        print("单例检查通过，服务可以启动")
    else:
        print("单例检查失败，服务已在运行")
