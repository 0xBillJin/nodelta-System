from typing import Dict, Any, List, Tuple, Union, Callable
import os
import sys
import logging
from logging import StreamHandler
# from logging.handlers import RotatingFileHandler
from datetime import datetime
from queue import Empty, Queue
import threading
import signal
import traceback

from ..gateway.gateway import BaseGateway
from .strategy_template import StrategyTemplate
from .constant import (
    LogLevel, Direction, Offset, Status, Exchange, Product, GatewayName, Interval, EventType
)
from .object import ContractData, OrderData, TradeData, PositionData, AssetData, AccountData, DepthData, BarData, Event
from ..utils.sender import Sender

class MainEngine:

    def __init__(self):
        '''
            Params:
                name: Engin名称
                path: 数据路径 会自动创建f"EngineData_{self.engine_name}" 文件夹
        '''
        # === 全局变量 ===
        self.gateways = {}  # gateway_name: gateway
        self.engine_name = 'MainEngine'
        # self.path = path
        self.is_event_processing = False # 事件处理循环是否正在运行
        self.newest_processed_bar_opening_ts: int = 0 # 最新处理的K线时间戳; 初始值0

        # # === 数据路径 ===
        # # engine 文件夹
        # self.path_engineDir = os.path.join(path, f"EngineData_{self.engine_name}")
        # if os.path.exists(self.path_engineDir) == False:
        #     os.mkdir(self.path_engineDir)
        # # engine log文件路径
        # self.path_engine_log = os.path.join(self.path_engineDir, f"EngineLog_{self.engine_name}.log")
        # # 若 self.path_engine_log 文件不存在 则创建
        # if not os.path.exists(os.path.dirname(self.path_engine_log)):
        #     os.makedirs(os.path.dirname(self.path_engine_log))
        # with open(self.path_engine_log, "w") as f:
        #     f.write(f"{datetime.now()} - {self.engine_name} - {self.engine_name} Engine init")
        
        # === 日志配置 ===
        self.logger = logging.getLogger(__name__)
        self.logger.propagate = False
        self.logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s')
        stream_handler = StreamHandler(sys.stderr)
        stream_handler.setLevel(logging.DEBUG)
        stream_handler.setFormatter(formatter)
        self.logger.addHandler(stream_handler)

        # === 事件队列 ===
        self.__queue_event = Queue()

        # === 事件处理函数 ===
        self.event_handlers = {
            EventType.DEPTH: self.__on_depth,
            EventType.TRADE: self.__on_trade,
            EventType.ORDER: self.__on_order,
            EventType.BAR: self.__on_bar,
        }
    
    def add_gateways(self, gateways: List[BaseGateway]):
        '''
            添加gateway
        '''
        for gateway in gateways:
            gateway.add_main_engine(self)  # 传入MainEngine
            self.gateways[gateway.gateway_name] = gateway

    def add_strategy(self, strategy: StrategyTemplate):
        '''
            添加策略
        '''
        self.strategy = strategy
        self.strategy.add_main_engine(self)  # 传入MainEngine
    
    def send_order(self, gateway_name, symbol: str, direction: Direction, offset: Offset, price: float, amount: float, **kwargs) -> str:
        gateway = self.gateways[gateway_name]
        try:
            orderId = gateway.send_order(symbol=symbol, direction=direction, offset=offset, price=price, amount=amount, **kwargs)
            return orderId
        except Exception as e:
            error_msg = traceback.format_exc()
            msg = f"send_order {gateway_name} 下单失败 : {e}; 合约: {symbol} 方向: {direction.value} 开平: {offset.value} 价格: {price} 数量: {amount} 报错信息:\n{error_msg}"
            self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)
            return ''
    
    def cancel_order(self, gateway_name, symbol: str, orderid: str) -> bool:
        gateway = self.gateways[gateway_name]
        try:
            cancel_bool: bool = gateway.cancel_order(symbol=symbol, orderid=orderid)
            return cancel_bool
        except Exception as e:
            error_msg = traceback.format_exc()
            msg = f"cancel_order {gateway_name} 撤单失败 : {e}; 合约: {symbol} 订单号: {orderid} 报错信息:\n{error_msg}"
            return False
        
    def query_order(self, gateway_name, symbol: str, orderid: str) -> OrderData or None:
        '''
            查询订单 
            return: OrderData or None
        '''
        gateway = self.gateways[gateway_name]
        try:
            order = gateway.query_order(symbol=symbol, orderid=orderid)
            return order
        except Exception as e:
            error_msg = traceback.format_exc()
            msg = f"query_order {gateway_name} 查询订单失败 : {e}; 合约: {symbol} 订单号: {orderid} 报错信息:\n{error_msg}"
            self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)
            return None
    
    def query_active_orders(self, gateway_name, symbol: str) -> List[OrderData] or List:
        '''
            查询所有活跃订单
            return: List[OrderData]
        '''
        gateway = self.gateways[gateway_name]
        try:
            orders = gateway.query_active_orders(symbol=symbol)
            return orders
        except Exception as e:
            error_msg = traceback.format_exc()
            msg = f"query_active_orders {gateway_name} 查询所有订单失败 : {e}; 合约: {symbol} 报错信息:\n{error_msg}"
            self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)
            return []
        
    def query_account(self, gateway_name) -> AccountData or None:
        '''
            查询账户
            return: AccountData or None
        '''
        gateway = self.gateways[gateway_name]
        try:
            account = gateway.query_account()
            return account
        except Exception as e:
            error_msg = traceback.format_exc()
            msg = f"query_account {gateway_name} 查询账户失败 : {e} 报错信息:\n{error_msg}"
            self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)
            return None
        
    def query_position(self, gateway_name, symbol: str) -> PositionData or None:
        '''
            查询持仓
            return: PositionData or None
        '''
        gateway = self.gateways[gateway_name]
        try:
            position = gateway.query_position(symbol=symbol)
            return position
        except Exception as e:
            error_msg = traceback.format_exc()
            msg = f"query_position {gateway_name} 查询持仓失败 : {e}; 合约: {symbol} 报错信息:\n{error_msg}"
            self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)
            return None

    def __on_order(self, exchange: Exchange, gateway_name: str, symbol: str, order: OrderData):
        '''
            交易所订单更新事件
        '''
        try:
            if symbol not in self.strategy.subscribe_symbols.get(gateway_name, []):
                return
            self.strategy.on_order(exchange=exchange, gateway_name=gateway_name, symbol=symbol, order=order)
        except Exception as e:
            error_msg = traceback.format_exc()
            msg = f"on_order 异常: {e} Gateway: {gateway_name} strategy:{self.strategy.strategy_name} 合约: {symbol} 订单: {order} 报错信息:\n{error_msg}"
            self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)

    def __on_depth(self, exchange: Exchange, gateway_name: str, symbol: str, depth: DepthData):
        '''
            交易所深度更新事件
        '''
        try:
            if symbol not in self.strategy.subscribe_symbols.get(gateway_name, []):
                return
            self.strategy.on_depth(exchange=exchange, gateway_name=gateway_name, symbol=symbol, depth=depth)
        except Exception as e:
            error_msg = traceback.format_exc()
            msg = f"on_depth 异常: {e} Gateway: {gateway_name} strategy:{self.strategy.strategy_name} 合约: {symbol} 深度: {depth} 报错信息:\n{error_msg}"
            self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)

    def __on_trade(self, exchange: Exchange, gateway_name: str, symbol: str, trade: TradeData):
        '''
            交易所成交更新事件
        '''
        try:
            if symbol not in self.strategy.subscribe_symbols.get(gateway_name, []):
                return
            self.strategy.on_trade(exchange, gateway_name, symbol, trade)
        except Exception as e:
            error_msg = traceback.format_exc()
            msg = f"on_trade 异常: {e} Gateway: {gateway_name} strategy:{self.strategy.strategy_name} 合约: {symbol} 成交: {trade} 报错信息:\n{error_msg}"
            self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)

    def __on_bar(self, exchange: Exchange, gateway_name: str, symbol: str, bar: BarData):
        '''
            交易所K线更新事件
        '''
        try:
            if symbol not in self.strategy.subscribe_symbols.get(gateway_name, []):
                return
            self.strategy.on_bar(exchange, gateway_name, symbol, bar)
        except Exception as e:
            error_msg = traceback.format_exc()
            msg = f"on_bar 异常: {e} Gateway: {gateway_name} strategy:{self.strategy.strategy_name} 合约: {symbol} K线: {bar} 报错信息:\n{error_msg}"
            self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)

    def write_log(self, msg: str, level:int = logging.INFO, source: str = '', lark_url = None):
        '''
            写入日志
            Params:
                msg: 日志内容
                level: 日志等级
                source: 日志来源 strategy, gateway, engine
        '''
        try:
            if source:
                _msg = f"{source} : {msg}"
            self.logger.log(level, _msg)
            if lark_url:
                Sender.lark(title=f"{self.strategy.strategy_name}", text=msg, bot_url=lark_url)
        except Exception as e:
            error_msg = traceback.format_exc()
            msg = f"写入日志异常: {e} 报错信息:\n{error_msg}"
            print(msg)

    def register_finish_func(self):
        '''
            注册策略结束事件
        '''
        signal.signal(signal.SIGINT, self.on_exit) # Ctrl + C
        signal.signal(signal.SIGTERM, self.on_exit) # kill pid superviosr 终止进程
        if hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, self.on_exit) # Ctrl + Break
        elif hasattr(signal, 'SIGQUIT'):
            signal.signal(signal.SIGQUIT, self.on_exit) # Ctrl + \

    def on_exit(self, signum, frame) -> None:
        '''
            进程结束前调用策略 on_finish 事件后退出程序
        '''
        self.strategy.on_finish()
        os._exit(0) # 结束所有线程退出程序
    
    def set_gateway_params(self, gateway_name: str, params: Dict[str, Any]):
        '''
            设置gateway 属性
        '''
        gateway = self.gateways[gateway_name]
        for key, value in params.items():
            setattr(gateway, key, value)
        msg = f"设置gateway属性成功: {gateway_name} {params}"
        self.write_log(msg=msg, level=LogLevel.INFO.value, source=self.engine_name)
        
    def start(self):
        '''
            启动引擎

        note:
            1. strategy engine 中的 symbol 都有统一的命名规范; 
                gateway 中接收到的都是规范 smyobl 需要转化成 gateway 所需的 gateway_symbol, 传递回 main_engine 时需要转化成统一的 symbol
        '''

        # 0. 策略：注册on_finish 运行on_start 事件
        try:
            self.register_finish_func()
            self.strategy.on_start()
        except Exception as e:
            msg = f"{self.strategy.strategy_name} register_finish_func or on_start 异常: {e}"
            self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)
            raise Exception(msg)

        # 1. 连接所有交易所 订阅行情与成交数据
        for gateway_name, gateway in self.gateways.copy().items():
            try:
                symbols = self.strategy.subscribe_symbols.get(gateway_name, []) # 订阅的合约
                print(f"{gateway_name} 正在订阅 {self.strategy.subscribe_symbols}")
                print(f"strategy.subscribe_symbols.keys: {self.strategy.subscribe_symbols.keys()}")
                if symbols:
                    # 1. check symbol 命名规范
                    for symbol in symbols:
                        if not self._check_symbol_name(symbol):
                            msg = f"symbol 命名规范错误: {symbol}, 请检查合约命名规则"
                            self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)
                            raise Exception(msg)
                    # 2. 链接交易所
                    gateway.connect(symbols) # 连接交易所
                else:
                    del self.gateways[gateway_name]
                    msg = f"Gateway: {gateway_name} 订阅的合约为空, 删除该Gateway"
                    self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)
            except Exception as e:
                msg = f"Gateway: {gateway_name} 订阅数据失败: {e}"
                self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)

        # 2. 启动事件处理循环
        self.write_log(msg=f"事件处理循环启动", level=LogLevel.INFO.value, source=self.engine_name)
        self.is_event_processing = True
        while True:
            try:
                event : Event = self.__queue_event.get(block=True)
                handler = self.event_handlers.get(event.event_type)
                if handler is not None:
                    handler(event.exchange, event.gateway_name, event.symbol, event.data)
                    # --- 如果是 trade 事件则自动打印 log
                    if event.event_type in [EventType.TRADE]:
                        msg = f"{event.event_type} Event Done; Data: {event.data}"
                        self.write_log(msg=msg, level=LogLevel.INFO.value, source=self.engine_name)
                    # --- 如果是 bar 事件更新 self.newest_processed_bar_opening_ts
                    if event.event_type == EventType.BAR:
                        self.newest_processed_bar_opening_ts = event.data.open_ts
                else:
                    msg = f"事件处理函数不存在: {event.event_type}"
                    self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)
            except Exception as e:
                msg = f"{self.engine_name} 事件处理异常: {e}"
                self.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.engine_name)
    
    def put_event(self, event_type: EventType, exchange: Exchange, gateway_name: str, symbol: str, data: Any):
        '''
            据合事件信息 并将事件推送至事件队列
        '''
        event = Event(
            event_type=event_type,
            exchange=exchange,
            gateway_name=gateway_name,
            symbol=symbol,
            data=data

        )

        self.__queue_event.put(event)

    def _check_symbol_name(self, symbol: str) -> bool:
        '''
            检查合约命名规范
            Params:
            "BASE-QUOTE-instType" ; "BASE-QUOTE-instType-到期日"
            instType : SPOT MARGIN SWAP FUTURES OPTION
            example : "BTC-USDT-SWAP"; "BTC-USD-FUTURES-200529"
        '''
        symbol_tumple = tuple(symbol.split('-'))
        if len(symbol_tumple) == 3:
            base, quote, instType = symbol_tumple
            if instType in ['SPOT', 'MARGIN', 'SWAP']:
                return True
            else:
                return False
        elif len(symbol_tumple) == 4:
            base, quote, instType, expirDate = symbol_tumple
            if instType in ['FUTURES', 'OPTION']:
                return True
            else:
                return False
        else:
            return False

    def get_queue_event(self) -> Queue:
        '''
            获取事件队列
        '''
        return self.__queue_event

if __name__ == '__main__':
    pass