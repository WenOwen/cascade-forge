"""工作区级 pytest 配置：让临时目录在受限文件沙箱（Windows ACL）下可用。

根因：CPython 在 Windows 上把 ``os.mkdir(path, 0o700)`` 翻译成一个**只授予当前用户**
的受限 DACL。本仓库在受限文件沙箱下运行时，进程 token 与该 DACL 不匹配，于是
**创建者自己**失去 ``FILE_LIST_DIRECTORY``，此后任何 ``os.scandir`` 都抛
``PermissionError: [WinError 5]``。实测：

===========  =============
``mkdir`` mode  同进程可枚举
===========  =============
``0o700``      否
``0o755``      是
``0o777``      是
===========  =============

pytest 恰好处处使用 ``0o700``：``TempPathFactory.getbasetemp``（建 basetemp 根）、
``TempPathFactory.mktemp``（非编号路径）、``_pytest.pathlib.make_numbered_dir``
（basetemp 与每个 ``tmp_path``）。后果是**每个使用 ``tmp_path`` 的测试在 setup 阶段
ERROR**，而 session 结束时的 ``cleanup_dead_symlinks`` 还会把退出码和摘要一起吞掉。

这里只做一件事：把 mode ``0o700`` 改写为 ``0o777``（严格更宽松，不影响其他 mode），
并在 ``pytest_sessionfinish`` 里对不可枚举的基础目录跳过 pytest 自己的清理兜底。
正常环境下这些目录本来就在系统临时区，行为差异可忽略。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_RESTRICTIVE_MODE = 0o700
_PERMISSIVE_MODE = 0o777

# 已被历史运行污染的 ``%TEMP%/pytest-of-<user>`` 目录无法枚举，也无法修复 ACL，
# 因此把 pytest 的临时根固定到工作区内的一个自有目录（`PYTEST_DEBUG_TEMPROOT` 是
# pytest 读取该根的唯一环境变量）。显式设置过的值优先。
os.environ.setdefault(
    "PYTEST_DEBUG_TEMPROOT", str(Path(__file__).resolve().parent / ".pytest-temproot")
)
os.makedirs(os.environ["PYTEST_DEBUG_TEMPROOT"], exist_ok=True)


def _relax(mode) -> int:
    if mode is None:
        return _PERMISSIVE_MODE
    try:
        value = int(mode)
    except (TypeError, ValueError):
        return mode
    return _PERMISSIVE_MODE if value == _RESTRICTIVE_MODE else mode


_original_os_mkdir = os.mkdir


def _os_mkdir(path, mode=_PERMISSIVE_MODE, *, dir_fd=None):
    return _original_os_mkdir(path, _relax(mode), dir_fd=dir_fd)


os.mkdir = _os_mkdir

_original_path_mkdir = Path.mkdir


def _path_mkdir(self, mode=_PERMISSIVE_MODE, parents=False, exist_ok=False):
    return _original_path_mkdir(self, _relax(mode), parents=parents, exist_ok=exist_ok)


Path.mkdir = _path_mkdir


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus) -> None:
    factory = getattr(session.config, "_tmp_path_factory", None)
    root = getattr(factory, "_basetemp", None)
    if root is None:
        return
    try:
        os.scandir(root).close()
    except OSError:
        # 基础目录不可枚举：停掉 pytest 自己的临时目录清理，避免它把已经跑完的
        # 测试结果变成 PermissionError 崩溃。
        try:
            factory._basetemp = None  # type: ignore[attr-defined]
        except Exception:
            pass
