"""Cross-process service/restore exclusion, supported on Windows and macOS."""
from functools import wraps
from pathlib import Path
from filelock import FileLock, Timeout


def database_guard(database):
    return FileLock(str(Path(database).resolve()) + '.service.lock', timeout=0)


def initializing(function):
    @wraps(function)
    def guarded(self, data_dir, *args, **kwargs):
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        try:
            with database_guard(Path(data_dir)/'memory.sqlite3'):
                return function(self, data_dir, *args, **kwargs)
        except Timeout as exc:
            raise RuntimeError('此数据库已有服务运行或正在恢复，请先停止原服务') from exc
    return guarded
