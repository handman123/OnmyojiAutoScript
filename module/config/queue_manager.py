# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
# 跨进程排队管理器
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

from filelock import FileLock
from module.logger import logger


class QueueManager:
    """跨进程任务执行排队管理器。

    使用共享的 JSON 状态文件 + FileLock 实现跨进程协调。
    每个启用排队模式的脚本实例通过此管理器获取/释放"执行权"，
    确保同一时间只有一个实例在执行任务。

    状态文件格式 (config/.queue_state.json):
    {
        "current": "oas1",              // 当前持有执行权的实例名
        "timestamp": "2026-07-08T14:30:00",  // 持有者心跳时间戳
        "queue": ["oas2", "oas3"]       // 等待队列 (FCFS)
    }
    """

    STATE_FILE = Path.cwd() / "log" / ".queue_state.json"
    STALE_TIMEOUT = 300  # 心跳超时 5 分钟，超时视为持有者崩溃

    def __init__(self, config_name: str):
        self.config_name = config_name
        self._lock = FileLock(str(self.STATE_FILE) + ".lock", timeout=5)

    # ==================== 状态文件读写 ====================

    def _read_state(self) -> dict:
        """读取状态文件。调用方需持有 _lock。"""
        if not self.STATE_FILE.exists():
            return {"current": None, "timestamp": None, "queue": []}
        try:
            with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            logger.warning("[QueueManager] State file corrupted, resetting")
            return {"current": None, "timestamp": None, "queue": []}

    def _write_state(self, state: dict) -> None:
        """写入状态文件。调用方需持有 _lock。"""
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def _is_stale(self, state: dict) -> bool:
        """检查当前持有者的心跳是否超时（崩溃检测）。"""
        ts_str = state.get("timestamp")
        if not ts_str:
            return False
        try:
            ts = datetime.fromisoformat(ts_str)
            elapsed = (datetime.now() - ts).total_seconds()
            return elapsed > self.STALE_TIMEOUT
        except (ValueError, TypeError):
            return True

    # ==================== 公开接口 ====================

    def try_acquire(self) -> bool:
        """尝试获取执行权。

        如果当前无持有者或自己是持有者，返回 True。
        如果被其他实例持有，将自己加入等待队列并返回 False。

        Returns:
            True: 已获得执行权，可以执行任务
            False: 执行权被其他实例持有，已加入等待队列
        """
        with self._lock:
            state = self._read_state()

            # 1. 如果自己已经是持有者，刷新心跳即可
            if state["current"] == self.config_name:
                state["timestamp"] = datetime.now().isoformat()
                self._write_state(state)
                return True

            # 2. 检查当前持有者是否崩溃（心跳超时）
            if state["current"] and self._is_stale(state):
                logger.warning(
                    f"[QueueManager] Holder '{state['current']}' "
                    f"heartbeat stale (> {self.STALE_TIMEOUT}s), taking over"
                )
                state["current"] = None
                state["timestamp"] = None

            # 3. 无持有者，自己成为持有者
            if state["current"] is None:
                state["current"] = self.config_name
                state["timestamp"] = datetime.now().isoformat()
                # 从等待队列中移除自己（如果存在）
                if self.config_name in state["queue"]:
                    state["queue"].remove(self.config_name)
                self._write_state(state)
                logger.info(f"[QueueManager] '{self.config_name}' acquired execution token")
                return True

            # 4. 执行权被他人持有，加入等待队列
            if self.config_name not in state["queue"]:
                state["queue"].append(self.config_name)
                self._write_state(state)
                logger.info(
                    f"[QueueManager] '{self.config_name}' waiting, "
                    f"holder: '{state['current']}', "
                    f"queue: {state['queue']}"
                )
            return False

    def release(self) -> None:
        """释放执行权。

        如果等待队列中有其他实例，自动移交给下一个。
        如果队列为空，清空持有者。
        """
        with self._lock:
            state = self._read_state()
            if state["current"] != self.config_name:
                return

            if state["queue"]:
                next_holder = state["queue"].pop(0)
                state["current"] = next_holder
                state["timestamp"] = datetime.now().isoformat()
                logger.info(
                    f"[QueueManager] '{self.config_name}' released token "
                    f"→ '{next_holder}'"
                )
            else:
                state["current"] = None
                state["timestamp"] = None
                logger.info(
                    f"[QueueManager] '{self.config_name}' released token, "
                    f"queue empty"
                )
            self._write_state(state)

    def heartbeat(self) -> None:
        """更新心跳时间戳，防止被误判为崩溃。

        应在每次任务执行完成后调用。
        """
        with self._lock:
            state = self._read_state()
            if state["current"] == self.config_name:
                state["timestamp"] = datetime.now().isoformat()
                self._write_state(state)

    def should_release(self, pending_task: list, waiting_task: list,
                       idle_threshold_minutes: int = 10) -> bool:
        """判断当前是否应该释放执行权。

        条件：
        1. pending_task 为空（当前时间节点的任务全部完成）
        2. waiting_task 为空，或最早的下一个任务距离现在 > idle_threshold_minutes 分钟

        Args:
            pending_task: 当前到期的任务列表
            waiting_task: 未来的任务列表（按 next_run 升序排列）
            idle_threshold_minutes: 空闲阈值（分钟），默认 10

        Returns:
            True: 应该释放执行权
            False: 应该继续持有
        """
        if pending_task:
            return False

        if not waiting_task:
            return True

        next_task_time = waiting_task[0].next_run
        idle_seconds = (next_task_time - datetime.now()).total_seconds()
        threshold_seconds = idle_threshold_minutes * 60

        should = idle_seconds > threshold_seconds
        if should:
            logger.info(
                f"[QueueManager] Next task at {next_task_time}, "
                f"idle {idle_seconds:.0f}s > threshold {threshold_seconds}s, "
                f"releasing token"
            )
        else:
            logger.info(
                f"[QueueManager] Next task at {next_task_time}, "
                f"idle {idle_seconds:.0f}s <= threshold {threshold_seconds}s, "
                f"keeping token"
            )
        return should

    def remove_from_queue(self) -> None:
        """从排队系统中完全移除自己。

        进程停止时调用，确保不会留下孤儿状态。
        如果自己是持有者，将执行权移交给下一个等待者。
        """
        with self._lock:
            state = self._read_state()

            # 从等待队列中移除
            if self.config_name in state["queue"]:
                state["queue"].remove(self.config_name)
                logger.info(
                    f"[QueueManager] '{self.config_name}' removed from waiting queue"
                )

            # 如果自己是持有者，移交或清空
            if state["current"] == self.config_name:
                if state["queue"]:
                    next_holder = state["queue"].pop(0)
                    state["current"] = next_holder
                    state["timestamp"] = datetime.now().isoformat()
                    logger.info(
                        f"[QueueManager] '{self.config_name}' removed, "
                        f"token → '{next_holder}'"
                    )
                else:
                    state["current"] = None
                    state["timestamp"] = None
                    logger.info(
                        f"[QueueManager] '{self.config_name}' removed, "
                        f"queue empty"
                    )
                self._write_state(state)
                return

            self._write_state(state)
