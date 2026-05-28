# single_instance.py
"""
跨平台单例检查模块

Windows: 使用 msvcrt 文件锁 (标准库)
Unix/macOS: 使用 fcntl.flock 文件锁

使用方式:
    from utils.single_instance import acquire_single_instance

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


def _is_process_running(pid: int) -> bool:
    """检查进程是否存活"""
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        # psutil 不可用时，用 os.kill 信号检测
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _acquire_windows():
    """Windows: 使用 msvcrt 文件锁"""
    import msvcrt
    import atexit

    pid_file = _get_pid_file_path()

    # Step 1: 检查是否有旧的 PID 文件和残留锁
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f:
                old_pid = int(f.read().strip())
            # 检查旧进程是否还在运行
            if _is_process_running(old_pid):
                print(f"Error: 服务已在运行 (PID: {old_pid})，请先关闭现有实例")
                return False
            else:
                # 旧进程已死，清理残留文件
                print(f"Warning: 发现残留 PID 文件 (PID: {old_pid})，已清理")
                try:
                    os.remove(pid_file)
                except:
                    pass
        except (ValueError, IOError):
            # 文件为空或损坏，删除重建
            try:
                os.remove(pid_file)
            except:
                pass

    # Step 2: 获取新锁
    try:
        f = open(pid_file, 'w+')
    except Exception as e:
        print(f"Error: 无法创建 PID 文件 ({e})")
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
