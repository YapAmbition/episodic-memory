#!/usr/bin/env python3
"""Offline recovery. Never call while Memory Slice / AstrBot is running."""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from memory_slice.transfer import verified_backup, backup_filename
from memory_slice.backup_validation import validate_database
from memory_slice.database_guard import database_guard
from filelock import Timeout


def restore(backup, database):
    backup, database = Path(backup).resolve(), Path(database).resolve()
    if backup == database or not backup.is_file() or not database.is_file():
        raise ValueError('请选择已有的备份与不同的目标数据库文件')
    lock=database_guard(database)
    try:lock.acquire(timeout=0)
    except Timeout as exc:raise ValueError('数据库服务仍在运行或另一个恢复正在进行，已拒绝恢复；请先停止服务') from exc
    try:
        return _restore_locked(backup,database)
    finally:lock.release()


def _restore_locked(backup,database):
    with sqlite3.connect(backup.as_uri()+'?mode=ro', uri=True) as source:
        source.execute('BEGIN')
        validate_database(source)
        with sqlite3.connect(database,timeout=1) as target:
            saved = database.parent/'backups'/backup_filename('restore',database.stem,'whole-database')
            verified_backup(target,saved)
            # SQLite's backup API replaces the database transactionally, including WAL.
            started=time.monotonic()
            def progress(*_):
                if time.monotonic()-started>30:raise RuntimeError('恢复超过 30 秒，已停止；恢复前数据已另存：'+str(saved))
            source.backup(target,pages=1024,progress=progress,sleep=0.05)
            if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise RuntimeError('恢复后检查未通过，当前数据已另存：'+str(saved))
    return saved


def main():
    parser=argparse.ArgumentParser(description='离线恢复记忆数据库；先停止独立后端或整个 AstrBot。恢复前自动另存当前数据。')
    parser.add_argument('--backup',required=True)
    parser.add_argument('--database',required=True)
    parser.add_argument('--service-stopped',action='store_true',help='确认独立后端或 AstrBot 已停止')
    args=parser.parse_args()
    if not args.service_stopped:parser.error('请先停止独立后端或 AstrBot，然后添加 --service-stopped')
    try:print('恢复完成；恢复前的数据保存在：'+str(restore(args.backup,args.database)))
    except (ValueError,RuntimeError,OSError,sqlite3.Error) as exc:parser.exit(1,str(exc)+'\n')

if __name__=='__main__':main()
