import os
import subprocess

from module.base.decorator import cached_property, run_once
from module.base.timer import Timer
from module.device.platform2.platform_base import PlatformBase, serial_to_id
from module.device.platform2.emulator_base import EmulatorInstanceBase
from module.logger import logger

# MuMu Mac CLI 默认路径（配置中未设置 emulatorinfo_path_mac 时使用）
MUMU_CLI_DEFAULT = '/Applications/MuMuPlayer.app/Contents/MacOS/mumu-cli'


class PlatformMacOS(PlatformBase):
    """
    macOS 平台模拟器控制（仅 MuMu）
    直接继承 PlatformBase，避免导入 platform_windows 里的 ctypes 代码
    """

    @cached_property
    def emulator_instance(self):
        """
        从配置 emulatorinfo_path_mac 读取 CLI 路径，
        若未配置则使用默认路径并回写配置
        """
        serial = self.config.script.device.serial
        instance_id = serial_to_id(serial)
        if instance_id is None:
            instance_id = 0

        cli_path = self.config.script.device.emulatorinfo_path_mac or MUMU_CLI_DEFAULT

        # 如果配置路径不存在（如首次运行），使用默认值
        if not os.path.exists(cli_path):
            cli_path = MUMU_CLI_DEFAULT

        if cli_path != self.config.script.device.emulatorinfo_path_mac:
            logger.info(f'Set macOS emulator path: {cli_path}')
            self.config.script.device.emulatorinfo_path_mac = cli_path
            self.config.save()

        return EmulatorInstanceBase(
            serial=serial,
            name=f'MuMuPlayer-12.0-{instance_id}',
            path=cli_path,
        )

    # ---- 以下方法从 PlatformWindows 复制，不包含任何 Windows 专有代码 ----

    def _emulator_function_wrapper(self, func: callable):
        """统一的错误包装，与 Windows 版逻辑一致"""
        try:
            func(self.emulator_instance)
            return True
        except OSError as e:
            msg = str(e)
            if 'WinError 740' in msg:
                logger.error('To start/stop emulator, run as administrator')
        except Exception as e:
            logger.exception(e)

        logger.error(f'Emulator function {func.__name__}() failed')
        return False

    def _emulator_start(self, instance: EmulatorInstanceBase):
        """macOS 版启动：使用配置路径执行 open <index>"""
        index = instance.MuMuPlayer12_id or 0
        subprocess.run([instance.path, 'open', str(index)])
        logger.info(f'MuMu instance {index} started')
        return True

    def _emulator_stop(self, instance: EmulatorInstanceBase):
        """macOS 版停止：使用配置路径执行 close <index>"""
        index = instance.MuMuPlayer12_id or 0
        subprocess.run([instance.path, 'close', str(index)])
        logger.info(f'MuMu instance {index} stopped')
        return True

    def emulator_start_watch(self):
        """
        等待 ADB 连接就绪（去掉 Windows 窗口检测部分）
        与 Windows 版逻辑一致，仅跳过 ctypes.windll 窗口操作
        """
        serial = self.emulator_instance.serial
        logger.info(f'Waiting for emulator: {serial}')

        interval = Timer(1).start()
        timeout = Timer(120).start()

        @run_once
        def show_online(m):
            logger.info(f'Emulator online: {m}')

        @run_once
        def show_ping(m):
            logger.info(f'Command ping: {m}')

        @run_once
        def show_package(m):
            logger.info(f'Found packages: {m}')

        while 1:
            interval.wait()
            interval.reset()
            if timeout.reached():
                logger.warning('Emulator start timeout')
                return False

            try:
                devices = self.list_device().select(serial=serial)
                if devices:
                    device = devices.first_or_none()
                    if device.status == 'device':
                        pass
                    if device.status == 'offline':
                        self.adb_client.disconnect(serial)
                        self.adb_client.connect(serial)
                        continue
                else:
                    self.adb_client.connect(serial)
                    continue
            except Exception as e:
                logger.warning(f'Error during adb_connect in watch loop: {e}')
                return False

            show_online(devices.first_or_none())

            # 检查 adb shell 是否可用
            try:
                pong = self.adb_shell(['echo', 'pong'])
            except Exception as e:
                logger.info(e)
                continue
            show_ping(pong)

            # 检查游戏包是否加载
            packages = self.list_app_packages(show_log=False)
            if len(packages):
                pass
            else:
                continue
            show_package(packages)

            break

        logger.info('Emulator start completed')
        return True

    def emulator_start(self):
        """启动模拟器（3 次重试）"""
        logger.hr('Emulator start', level=1)
        for i in range(3):
            if not self._emulator_function_wrapper(self._emulator_stop):
                return False
            if self._emulator_function_wrapper(self._emulator_start):
                if self.emulator_start_watch():
                    return True
                logger.attr(2 - i, 'Failed to connect or start, try again')
                continue
            else:
                if self._emulator_function_wrapper(self._emulator_stop):
                    continue
                else:
                    return False

        logger.error('Failed to start emulator 3 times, stopped')
        return False

    def emulator_stop(self):
        """停止模拟器（3 次重试）"""
        logger.hr('Emulator stop', level=1)
        for _ in range(3):
            if self._emulator_function_wrapper(self._emulator_stop):
                return True
            else:
                if self._emulator_function_wrapper(self._emulator_start):
                    continue
                else:
                    return False

        logger.error('Failed to stop emulator 3 times, stopped')
        return False
