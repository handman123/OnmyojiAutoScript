# 排队模式 — 开发文档

## 1. 设计目标

在多个脚本实例之间实现**跨进程的任务执行排队**，使得同一时间只有一个实例在执行任务，消除多实例并发导致的 CPU/内存/IO 峰值。

---

## 2. 整体方案

### 2.1 核心思路

使用**文件系统作为共享状态介质**，结合项目已有的 `filelock.FileLock` 实现跨进程的互斥协调。

每个实例在准备执行任务前，必须先获取"执行权"（execution token）。执行权由一个共享的 JSON 状态文件记录，读写受 `FileLock` 保护。

### 2.2 为什么选文件锁而非 multiprocessing.Lock

| 方案 | 优点 | 缺点 |
|------|------|------|
| `multiprocessing.Lock` | 真正的 OS 级互斥锁，低延迟 | 必须在 fork 前创建；子进程不是同时启动的；无法跨 server/GUI/standalone 模式 |
| `filelock.FileLock` + 状态文件 | 任何进程都能访问；与项目现有模式一致；可持久化队列状态 | 每次操作涉及磁盘 I/O |

**选文件方案**，理由：
- 项目已大量使用 `FileLock`（`module/config/utils.py` 对所有 config JSON 读写都加了锁）
- 队列状态需要持久化（崩溃恢复），JSON 文件天然支持
- 不受进程启动顺序限制

### 2.3 架构图

```
config/
  .queue_state.json        ← 排队状态（谁持有执行权、等待队列）
  .queue_state.json.lock   ← FileLock 保护文件读写

┌─────────────────────────────────────────────────────┐
│                  QueueManager                       │
│  ┌───────────────────────────────────────────────┐  │
│  │  try_acquire()   尝试获取执行权               │  │
│  │  release()       释放执行权                   │  │
│  │  heartbeat()     更新心跳时间戳               │  │
│  │  should_release() 判断是否应释放              │  │
│  │  remove_from_queue() 退出排队                 │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
         │                    │
    ┌────▼────┐          ┌───▼─────┐
    │ 实例 A  │          │ 实例 B  │
    │ (持有者) │          │ (等待中) │
    └─────────┘          └─────────┘
```

---

## 3. 新增文件

### 3.1 `module/config/queue_manager.py`

新建文件，实现 `QueueManager` 类。

```python
# module/config/queue_manager.py
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
    """

    STATE_FILE = Path("config/.queue_state.json")
    STALE_TIMEOUT = 300  # 持有者心跳超时（秒），超过视为崩溃

    def __init__(self, config_name: str):
        self.config_name = config_name
        self._lock = FileLock(str(self.STATE_FILE) + ".lock")

    # ========== 状态文件读写 ==========

    def _read_state(self) -> dict:
        """读取状态文件（调用方需持有锁）"""
        if not self.STATE_FILE.exists():
            return {"current": None, "timestamp": None, "queue": []}
        with open(self.STATE_FILE, "r") as f:
            return json.load(f)

    def _write_state(self, state: dict) -> None:
        """写入状态文件（调用方需持有锁）"""
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    def _is_stale(self, state: dict) -> bool:
        """检查当前持有者是否心跳超时（崩溃）"""
        if not state.get("timestamp"):
            return False
        try:
            ts = datetime.fromisoformat(state["timestamp"])
            return (datetime.now() - ts).total_seconds() > self.STALE_TIMEOUT
        except (ValueError, TypeError):
            return True

    # ========== 公开接口 ==========

    def try_acquire(self) -> bool:
        """尝试获取执行权。

        Returns:
            True: 获取成功，可以执行任务
            False: 执行权被其他实例持有，已加入等待队列
        """
        with self._lock:
            state = self._read_state()

            # 1. 如果自己是当前持有者，刷新心跳即可
            if state["current"] == self.config_name:
                state["timestamp"] = datetime.now().isoformat()
                self._write_state(state)
                return True

            # 2. 检查当前持有者是否崩溃（心跳超时）
            if state["current"] and self._is_stale(state):
                logger.warning(
                    f"[QueueManager] Current holder '{state['current']}' "
                    f"heartbeat stale (> {self.STALE_TIMEOUT}s), taking over"
                )
                state["current"] = None
                state["timestamp"] = None

            # 3. 没有持有者，自己成为持有者
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
                    f"[QueueManager] '{self.config_name}' added to queue, "
                    f"current holder: '{state['current']}', "
                    f"queue: {state['queue']}"
                )
            return False

    def release(self) -> None:
        """释放执行权，下一个等待者自动获得。"""
        with self._lock:
            state = self._read_state()
            if state["current"] != self.config_name:
                return  # 自己不是持有者，无需操作

            if state["queue"]:
                # 有等待者，移交执行权
                next_holder = state["queue"].pop(0)
                state["current"] = next_holder
                state["timestamp"] = datetime.now().isoformat()
                logger.info(
                    f"[QueueManager] '{self.config_name}' released token → "
                    f"'{next_holder}'"
                )
            else:
                # 无等待者，清空
                state["current"] = None
                state["timestamp"] = None
                logger.info(
                    f"[QueueManager] '{self.config_name}' released token, "
                    f"queue empty"
                )
            self._write_state(state)

    def heartbeat(self) -> None:
        """更新心跳时间戳，防止被误判为崩溃。"""
        with self._lock:
            state = self._read_state()
            if state["current"] == self.config_name:
                state["timestamp"] = datetime.now().isoformat()
                self._write_state(state)

    def should_release(self, pending_task: list, waiting_task: list,
                       idle_threshold_minutes: int = 10) -> bool:
        """判断是否应释放执行权。

        条件：
        1. 没有待执行任务（pending_task 为空）
        2. 未来 threshold 分钟内没有任务

        Args:
            pending_task: 当前到期的任务列表
            waiting_task: 未来的任务列表（按 next_run 升序）
            idle_threshold_minutes: 空闲阈值（分钟）

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
                f"will release token"
            )
        return should

    def remove_from_queue(self) -> None:
        """从排队系统中完全移除自己（进程停止时调用）。"""
        with self._lock:
            state = self._read_state()

            # 从等待队列移除
            if self.config_name in state["queue"]:
                state["queue"].remove(self.config_name)

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
                self._write_state(state)
                return

            self._write_state(state)
```

---

## 4. 修改文件

### 4.1 `tasks/Script/config_optimization.py` — 新增配置字段

在 `ScriptOption`（或对应配置类）中新增两个字段：

```python
# 排队模式
queue_mode: bool = False
# 释放执行权的空闲阈值（分钟），仅 queue_mode=True 时生效
queue_idle_threshold: int = 10
```

中文名映射（在对应的 i18n/config_manual 中）：
```python
"queue_mode": "排队模式"
"queue_idle_threshold": "排队空闲阈值"
```

### 4.2 `script.py` — 核心集成

#### 4.2.1 `Script.__init__()` 新增

```python
# 在 __init__ 末尾添加：
from module.config.queue_manager import QueueManager
self.queue_manager: Optional[QueueManager] = None
```

`QueueManager` 实例化推迟到首次使用（lazy init），避免非排队模式的实例也创建状态文件。或直接在 `__init__` 中判断：

```python
if self.config.model.script.script_option.queue_mode:
    self.queue_manager = QueueManager(self.config.config_name)
else:
    self.queue_manager = None
```

#### 4.2.2 `Script.get_next_task()` 修改

在返回 task 之前插入排队逻辑。当前代码（约第 304-321 行）：

```python
def get_next_task(self) -> str:
    while True:
        task = self.config.get_next()
        self.config.task = task
        if self.state_queue:
            self.state_queue.put({"schedule": self.config.get_schedule_data()})
        now = datetime.now()
        if task.next_run <= now:
            return task.command          # ← 任务就绪，直接返回
        if not self._handle_wait_during_idle(task.next_run):
            del_cached_property(self, "config")
```

修改为：

```python
def get_next_task(self) -> str:
    while True:
        task = self.config.get_next()
        self.config.task = task
        if self.state_queue:
            self.state_queue.put({"schedule": self.config.get_schedule_data()})
        now = datetime.now()
        if task.next_run <= now:
            # ===== 排队模式：尝试获取执行权 =====
            if not self._try_acquire_queue_token():
                # 未获取到执行权，等待后重新评估
                del_cached_property(self, "config")
                continue
            # ===== 排队逻辑结束 =====
            return task.command
        if not self._handle_wait_during_idle(task.next_run):
            del_cached_property(self, "config")
```

#### 4.2.3 新增 `Script._try_acquire_queue_token()`

```python
def _try_acquire_queue_token(self) -> bool:
    """尝试获取排队执行权。

    Returns:
        True: 可以执行任务
        False: 需要等待（调用方应重新加载配置后重试）
    """
    if self.queue_manager is None:
        return True  # 未启用排队模式，直接放行

    # 尝试获取执行权
    if self.queue_manager.try_acquire():
        return True

    # 未获取到，进入等待循环
    logger.info(f"[Queue] Waiting for execution token...")
    self.config.start_watching()
    while True:
        time.sleep(5)

        # 检查配置是否被修改（用户关闭了排队模式或修改了任务）
        if self.config.should_reload():
            logger.info(f"[Queue] Config changed, re-evaluating")
            return False

        # 检查排队模式是否被关闭
        # （重新读取配置）
        try:
            del_cached_property(self, "config")
            if not self.config.model.script.script_option.queue_mode:
                logger.info(f"[Queue] Queue mode disabled, proceeding without token")
                self.queue_manager.remove_from_queue()
                self.queue_manager = None
                return True
        except Exception:
            pass

        # 重试获取
        if self.queue_manager.try_acquire():
            return True
```

#### 4.2.4 `Script.loop()` 修改 — 任务完成后检查是否释放

在 `loop()` 中，任务执行成功后（`success = True` 的分支），新增释放检查：

```python
# 在 loop() 中 success == True 之后：
if success:
    # ===== 排队模式：心跳 + 释放检查 =====
    if self.queue_manager:
        self.queue_manager.heartbeat()
        if self.queue_manager.should_release(
            pending_task=self.config.pending_task,
            waiting_task=self.config.waiting_task,
            idle_threshold_minutes=self.config.model.script.script_option.queue_idle_threshold
        ):
            self.queue_manager.release()
    # ===== 排队逻辑结束 =====
    del_cached_property(self, 'config')
    continue
```

### 4.3 `module/server/script_process.py` — 进程停止时清理

在 `func()` 的 `signal_handler` 中和异常退出路径中，添加队列清理：

```python
def signal_handler(signum, frame):
    logger.info(f'Script {config} received signal {signum}, exiting gracefully')
    # 清理排队状态
    try:
        from module.config.queue_manager import QueueManager
        qm = QueueManager(config)
        qm.remove_from_queue()
    except Exception:
        pass
    log_pipe_in.close()
    state_queue.close()
    sys.exit(0)
```

---

## 5. 完整流程

### 5.1 正常排队流程

```
实例 A (oas1)                         实例 B (oas2)
─────────────                         ─────────────
09:00 任务到期
try_acquire() → 无人持有，获取成功 ✓
执行任务...
                                       09:01 任务到期
                                       try_acquire() → A 持有
                                       加入等待队列 []
                                       每 5s 轮询...
09:03 任务完成
heartbeat()
should_release():
  pending=[] ✓
  waiting=[09:15] → 距现在 12min > 10min
  → release()
                                       09:03 轮询
释放执行权 → 队列下一个 → oas2
                                       try_acquire() → 获取成功 ✓
                                       开始执行任务...
```

### 5.2 短间隔任务：不释放执行权

```
实例 A (oas1)                         实例 B (oas2)
─────────────                         ─────────────
09:00 任务到期
try_acquire() → 获取成功 ✓
执行任务...
                                       09:01 任务到期
                                       try_acquire() → A 持有
                                       加入等待队列 []
09:02 任务完成
heartbeat()
should_release():
  pending=[] ✓
  waiting=[09:08] → 距现在 6min < 10min
  → 不释放 ✗
进入 idle 等待...
                                       09:02 轮询，仍然被持有...
09:08 下一个任务到期
执行任务...
09:10 任务完成
heartbeat()
should_release():
  waiting=[18:00] → 距现在 470min > 10min
  → release()
                                       09:10 轮询
释放执行权 → oas2
                                       try_acquire() → 获取成功 ✓
                                       执行任务...
```

### 5.3 崩溃恢复流程

```
实例 A (持有者) 崩溃                    实例 B (等待中)
─────────────────                      ─────────────
执行任务中...
进程异常退出（未调用 remove_from_queue）
                                       轮询 try_acquire() → A 持有
                                       轮询 try_acquire() → A 持有
                                       ...
                                       5 分钟后：
                                       try_acquire() → A 心跳过期
                                       → 接管执行权 ✓
                                       开始执行任务...
```

---

## 6. 边界情况处理

| 场景 | 处理方式 |
|------|----------|
| 未启用排队的实例 | `queue_manager` 为 `None`，`_try_acquire_queue_token()` 直接返回 `True`，行为不变 |
| 仅一个实例启用排队 | 始终持有执行权，等同于未启用，无额外开销 |
| 运行中关闭排队模式 | `should_reload()` 检测到 config 变化，重新读取后 `queue_manager` 置 `None`，`remove_from_queue()` 清理 |
| 等待中手动停止进程 | `signal_handler` → `remove_from_queue()` 清理自己在队列中的位置 |
| 等待中修改了任务时间 | `should_reload()` 返回 `True` → 退出等待循环 → 重新调用 `get_next_task()` |
| 持有者完成任务后退出 | `signal_handler` → `remove_from_queue()` → 移交执行权给下一个等待者 |
| 队列状态文件损坏 | `_read_state()` 返回默认空状态，所有实例重新竞争 |
| 多个实例同时 try_acquire | `FileLock` 保证串行读写，先获得锁的实例成为持有者 |

---

## 7. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `module/config/queue_manager.py` | **新增** | 排队管理器核心类 |
| `tasks/Script/config_optimization.py` | 修改 | 新增 `queue_mode`、`queue_idle_threshold` 配置字段 |
| `script.py` | 修改 | 在 `__init__`、`get_next_task()`、`loop()` 中集成排队逻辑 |
| `module/server/script_process.py` | 修改 | 进程退出时清理排队状态 |
| `tasks/Script/config_manual.py` | 修改 | 添加配置项的中文描述 |
| `config/template.json` | 修改 | 添加默认配置值 |

---

## 8. 测试建议

1. **单元测试**：`QueueManager` 的状态读写、获取/释放/心跳/过期逻辑
2. **双实例测试**：启动两个实例，设置相同时间点的任务，验证排队顺序
3. **崩溃恢复测试**：kill -9 持有者进程，验证等待者 5 分钟后接管
4. **配置热更新测试**：运行中关闭排队模式，验证实例恢复正常独立执行
5. **向后兼容测试**：未启用排队的实例是否行为不变
6. **长稳测试**：7×24 小时运行，验证不会死锁
