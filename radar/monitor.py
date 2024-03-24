import psutil
import numpy as np
import socket
import time
from datetime import datetime
import traceback
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
import signal
import os
import sys
import pathlib
ndSys_PATH = str(pathlib.Path(__file__).parent.parent)
print(f"ndSys_PATH: {ndSys_PATH}")
if ndSys_PATH not in sys.path:
    sys.path.append(ndSys_PATH)

from nodelta.utils.sender import Sender

class ServerMonitor:
    def __init__(self, lark_url):
        self.cpu_usage = []
        self.mem_usage = []
        self.disk_io = []
        self.lark_url = lark_url

        # 过高资源使用率报警阈值
        self.cpu_usage_warn = 85
        self.last_cpu_usage_warn_ts = 0
        self.mem_usage_warn = 85
        self.last_mem_usage_warn_ts = 0
        self.lark_warn_cool_ts = 1000 * 60 * 15 # lark 警报冷却 15分钟

        # 初始磁盘读写量
        self.start_counters = psutil.disk_io_counters()
        self.start_counters_ts = int(time.time() * 1000)

        # 服务器IP
        host_ip = socket.gethostbyname(socket.gethostname())
        self.host_ip = f"{host_ip.split('.')[0]}.**.{host_ip.split('.')[-1]}"

        # 退出信号
        signal.signal(signal.SIGINT, self.on_exit) # Ctrl + C
        signal.signal(signal.SIGTERM, self.on_exit) # kill pid superviosr 终止进程
        if hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, self.on_exit) # Ctrl + Break
        elif hasattr(signal, 'SIGQUIT'):
            signal.signal(signal.SIGQUIT, self.on_exit) # Ctrl + \

    def get_system_info(self):

        # cpu 与 内存 使用率
        self.cpu_usage.append(psutil.cpu_percent())
        self.mem_usage.append(psutil.virtual_memory().percent)

        # 计算磁盘读写量
        now_counters = psutil.disk_io_counters()
        now_counters_ts = int(time.time() * 1000)
        kbps = (now_counters.read_bytes + now_counters.write_bytes - self.start_counters.read_bytes - self.start_counters.write_bytes) / (now_counters_ts - self.start_counters_ts) / 1024
        kbps = np.around(kbps, decimals=2)
        self.disk_io.append(kbps)
        self.start_counters = now_counters
        self.start_counters_ts = now_counters_ts

        now_ts = int(time.time() * 1000)
        if self.cpu_usage[-1] > self.cpu_usage_warn:
            msg = f"CPU 使用率过高: {self.cpu_usage[-1]}%"
            logging.warning(msg)
            if now_ts - self.last_cpu_usage_warn_ts > self.lark_warn_cool_ts:
                Sender.lark(title=f"服务器报警: {self.host_ip}", text=msg + f"\n报送将冷却{int(self.lark_warn_cool_ts / (1000 * 60))}分钟", bot_url=self.lark_url)
                self.last_cpu_usage_warn_ts = now_ts
        if self.mem_usage[-1] > self.mem_usage_warn:
            msg = f"内存 使用率过高: {self.mem_usage[-1]}%"
            logging.warning(msg)
            if now_ts - self.last_mem_usage_warn_ts > self.lark_warn_cool_ts:
                Sender.lark(title=f"服务器报警: {self.host_ip}", text=msg + f"\n报送将冷却{int(self.lark_warn_cool_ts / (1000 * 60))}分钟", bot_url=self.lark_url)
                self.last_mem_usage_warn_ts = now_ts

        # 打印日志记录当前资源使用情况
        msg = f"当前资源使用情况:\n CPU: {self.cpu_usage[-1]}%; 内存: {self.mem_usage[-1]}%; 磁盘IO: {self.disk_io[-1]}kb/s"
        logging.info(msg)

    def calculate_stats(self, data):
        # 保留两位小数
        data = np.around(data, decimals=2)
        return np.min(data), np.percentile(data, 25), np.median(data), np.percentile(data, 75), np.max(data)

    def send_report(self):
        cpu_stats = self.calculate_stats(self.cpu_usage)
        mem_stats = self.calculate_stats(self.mem_usage)
        disk_stats = self.calculate_stats(self.disk_io)

        msg = "\n"
        msg += f"CPU usage(%):\n min@{cpu_stats[0]}; 25%@{cpu_stats[1]}; median@{cpu_stats[2]}; 75%@{cpu_stats[3]}; max@{cpu_stats[4]}\n"
        msg += f"Memory usage(%):\n min@{mem_stats[0]}; 25%@{mem_stats[1]}; median@{mem_stats[2]}; 75%@{mem_stats[3]}; max@{mem_stats[4]}\n"
        msg += f"Disk IO(kb/s):\n min@{disk_stats[0]}; 25%@{disk_stats[1]}; median@{disk_stats[2]}; 75%@{disk_stats[3]}; max@{disk_stats[4]}\n"

        Sender.lark(title=f"服务器定时报送: {self.host_ip}", text=msg, bot_url=self.lark_url)

        self.cpu_usage = []
        self.mem_usage = []
        self.disk_io = []

    def on_exit(self, signum, frame):
        logging.info(f"服务器【监控停止】: {self.host_ip}")
        Sender.lark(title=f"服务器【监控停止】: {self.host_ip}", text='', bot_url=self.lark_url)
        os._exit(0)

    def run(self):
        
        Sender.lark(title=f"服务器【监控启动】: {self.host_ip}", text='', bot_url=self.lark_url)
        last_send_report_ts = 0

        while True:
            
            try:
                self.get_system_info()

                if (datetime.now().minute == 0 or datetime.now().minute == 30) and (int(time.time() * 1000) - last_send_report_ts) > 1000 * 60:
                    self.send_report()
                    last_send_report_ts = int(time.time() * 1000)

            except Exception as e:
                error_msg = traceback.format_exc()
                msg = f"monitor 报错{e}:\n {error_msg}"
                logging.error(msg)
            finally:
                time.sleep(10)

if __name__ == "__main__":

    lark_url = 'https://open.larksuite.com/open-apis/bot/v2/hook/a6c0a6b3-f2c6-4ded-af6b-16bd79fabe31'

    monitor = ServerMonitor(lark_url=lark_url)
    monitor.run()