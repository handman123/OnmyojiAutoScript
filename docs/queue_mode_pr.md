# 排队模式

## 背景

多实例场景下，所有模拟器的周期性任务会在同一时间点（如服务器刷新 09:00/17:00）同时触发。5~6 个实例并发执行时，ADB 截图、OCR、图像匹配等操作瞬时争抢 CPU/内存/IO，导致任务超时失败。

## 功能

新增 `queue_mode` 配置项，启用后排队的实例依次串行执行任务，同一时间只有一个实例操作模拟器。当前实例完成所有待执行任务且下一个任务在 N 分钟内不触发（`queue_idle_threshold`，默认 10 分钟），释放执行权给下一个实例。

## 配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `queue_mode` | bool | false | 是否启用排队 |
| `queue_idle_threshold` | int | 10 | 空闲阈值（分钟），无任务时长超过此值释放执行权 |

位于 `config.script.optimization` 下。

## 实现

### 跨进程锁

使用共享 JSON 文件（`log/.queue_state.json`）+ `filelock.FileLock` 实现跨进程协调，不依赖 `multiprocessing` 原语，兼容 server/GUI/standalone 三种运行模式。

### 集成点

| 位置 | 职责 |
|------|------|
| `get_next_task()` | 进入 idle wait 前释放 token + 确认持有 token |
| `run()` | 所有任务执行的统一入口，执行前确认持有 token |
| `loop()` success 后 | 心跳保活 |
| `func()` finally | 进程退出时清理排队状态，防止死锁 |

### 崩溃恢复

- 正常退出：`finally` + `signal_handler` 双重保障
- 异常崩溃：心跳超时 5 分钟后自动接管

### ADB 容错

排队期间关闭模拟器，重新获得 token 后需重启。`adb.py` retry wrapper 新增 `EmulatorNotRunningError` 处理——ADB 操作发现模拟器未运行，自动调 `emulator_start()` 重启后重试，避免因模拟器状态异常导致的 `RequestHumanTakeover` 崩溃。

## 边界

- 未启用 `queue_mode` 的实例完全不受影响
- Restart 任务参与排队
- 等待期间可自动关闭模拟器节省资源（跟随 `when_task_queue_empty` 策略）
