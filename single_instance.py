# single_instance.py
"""
跨平台单例检查模块

Windows: 使用 msvcrt 文件锁 (标准库)
Unix/macOS: 使用 fcntl.flock 文件锁

使用方式:
    from single_instance import acquire_single_instance

    if not acquire_single_instance():
        sys.exit(1)
"""
import sys
import os


def acquire_single_instance():
    """
    获取单例锁

    Returns:
        True - 获取锁成功，服务可以启动
        False - 已有实例运行，服务不应启动
    """
    if sys.platform == "win32":
        return _acquire_windows()
    else:
        return _acquire_unix()


def _get_pid_file_path():
    """获取 PID 文件路径"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".funasr.pid")


def _acquire_windows():
    """Windows: 使用 msvcrt 文件锁"""
    import msvcrt
    import atexit

    pid_file = _get_pid_file_path()

    try:
        f = open(pid_file, 'r+')
    except FileNotFoundError:
        f = open(pid_file, 'w+')

    try:
        # LK_NBLCK = non-blocking lock
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        f.seek(0)
        f.write(str(os.getpid()))
        f.flush()

        # 清理函数
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
        print("Error: 服务已在运行，请先关闭现有实例")
        return False


def _acquire_unix():
    """Unix/macOS: 使用 flock 文件锁"""
    import fcntl
    import atexit

    pid_file = _get_pid_file_path()

    try:
        f = open(pid_file, 'w')
    except Exception as e:
        print(f"Warning: 无法打开 PID 文件 ({e})")
        return True  # 降级，不阻止启动

    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(str(os.getpid()))
        f.flush()

        # 清理函数
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
        print("Error: 服务已在运行，请先关闭现有实例")
        return False


if __name__ == "__main__":
    # 测试
    if acquire_single_instance():
        print("单例检查通过，服务可以启动")
    else:
        print("单例检查失败，服务已在运行")
