# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
# 跨进程排队管理器
import json
import threading
from pathlib import Path
from datetime import datetime

from filelock import FileLock
from module.logger import logger


class QueueManager:
    """跨进程任务执行排队管理器。

    使用共享的 JSON 状态文件 + FileLock 实现跨进程协调。
    每个启用排队模式的脚本实例通过此管理器获取/释放"执行权"，
    确保同一时间只有一个实例在执行任务。
    """

    STATE_FILE = Path.cwd() / "log" / ".queue_state.json"
    STALE_TIMEOUT = 300  # 心跳超时 5 分钟，超时视为持有者崩溃
    HEARTBEAT_INTERVAL = 180  # 心跳线程更新间隔 3 分钟

    def __init__(self, config_name: str):
        self.config_name = config_name
        self._lock = FileLock(str(self.STATE_FILE) + ".lock", timeout=5)
        self._heartbeat_thread: threading.Thread = None
        self._heartbeat_stop: threading.Event = None
        self._token_lost = threading.Event()

    @property
    def token_lost(self) -> bool:
        return self._token_lost.is_set()

    # ==================== 心跳线程 ====================

    def _start_heartbeat(self):
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._token_lost.clear()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self._heartbeat_thread.start()
        logger.info(f"[QueueManager] Heartbeat thread started for '{self.config_name}'")

    def _stop_heartbeat(self):
        if self._heartbeat_stop:
            self._heartbeat_stop.set()
        self._heartbeat_thread = None
        self._heartbeat_stop = None

    def _heartbeat_loop(self):
        while not self._heartbeat_stop.wait(timeout=self.HEARTBEAT_INTERVAL):
            try:
                with self._lock:
                    state = self._read_state()
                    if state["current"] == self.config_name:
                        state["timestamp"] = datetime.now().isoformat()
                        self._write_state(state)
                    elif self.config_name not in state.get("queue", []):
                        logger.warning(
                            f"[QueueManager] '{self.config_name}' lost token, "
                            f"current holder: {state['current']}"
                        )
                        self._token_lost.set()
                        return
            except Exception as e:
                logger.error(f"[QueueManager] Heartbeat error: {e}")

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
                if self.config_name in state["queue"]:
                    state["queue"].remove(self.config_name)
                self._write_state(state)
                logger.info(f"[QueueManager] '{self.config_name}' acquired execution token")
                self._start_heartbeat()
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
        """释放执行权。"""
        with self._lock:
            state = self._read_state()
            if state["current"] != self.config_name:
                return

            self._stop_heartbeat()

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
        """心跳线程自动维护，此方法保留接口兼容性。"""
        pass

    def should_release(self, pending_task: list, waiting_task: list,
                       idle_threshold_minutes: int = 10) -> bool:
        """判断当前是否应该释放执行权。

        条件：
        1. pending_task 为空
        2. waiting_task 为空，或最早的下一个任务距离现在 > idle_threshold_minutes 分钟
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
        """从排队系统中完全移除自己。进程停止时调用。"""
        with self._lock:
            state = self._read_state()

            if self.config_name in state["queue"]:
                state["queue"].remove(self.config_name)
                logger.info(
                    f"[QueueManager] '{self.config_name}' removed from waiting queue"
                )

            if state["current"] == self.config_name:
                self._stop_heartbeat()
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
