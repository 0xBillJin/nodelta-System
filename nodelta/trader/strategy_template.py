from abc import ABC, abstractmethod
from datetime import datetime
import time
from typing import Any, Dict, List, Tuple
import signal

from .constant import (
    LogLevel, Direction, Offset, Status, Exchange, Product, GatewayName, EventType
)
from .object import ContractData, OrderData, TradeData, PositionData, AssetData, AccountData, DepthData, BarData, Event


'''
    策略模板原则:

    1. 回调函数 例如 on_depth on_trade on_order on_account 参数位置固定 exchange gateway_name symbol EventData
    2. 交易函数 例如 buy sell short cover 参数位置固定 gateway_name symbol price amount
    3. 接受到 trade 事件后 引擎会自动记录 trade log

'''

class StrategyTemplate(ABC):

    def __init__(self, name: str, subscribe_symbols: dict):
        '''
            Params:
                name: 策略名称
                subscribe_symbols : 订阅的币对 仅接收到订阅的币对 gateway_name: [symbol1, symbol2]
        '''
        
        self.strategy_name = name
        self.subscribe_symbols = subscribe_symbols
        self.topic: dict = {} # 订阅的topic 用于记录订阅的topic {gateway_name: {event_type.value: {params}}}

        # 参数与变量
        self.params_name: List[str] = [] # 参数名称
        self.variables_name: List[str] = [] # 变量名称 会保存至bt_data中

    def add_main_engine(self, main_engine): #: MainEngine
        self.main_engine = main_engine
    
    def buy(self, gateway_name: str, symbol: str, price: float, amount: float, **kwargs):
        '''
            如果是单向持仓那么就是买 与 cover功能相同
            如果是双向持仓那么就是开多
        '''
        orderId = self.main_engine.send_order(gateway_name=gateway_name, symbol=symbol, direction=Direction.LONG, offset=Offset.OPEN, price=price, amount=amount, **kwargs)
        
        return orderId
    
    def sell(self, gateway_name: str, symbol: str, price: float, amount: float, **kwargs):
        '''
            如果是单向持仓那么就是卖 与 short功能相同
            如果是双向持仓那么就是平多
        '''
        orderId = self.main_engine.send_order(gateway_name=gateway_name, symbol=symbol, direction=Direction.SHORT, offset=Offset.CLOSE, price=price, amount=amount, **kwargs)
        
        return orderId
    
    def short(self, gateway_name: str, symbol: str, price: float, amount: float, **kwargs):
        '''
            如果是单向持仓那么就是卖 与 sell功能相同
            如果是双向持仓那么就是开空
        '''
        orderId = self.main_engine.send_order(gateway_name=gateway_name, symbol=symbol, direction=Direction.SHORT, offset=Offset.OPEN, price=price, amount=amount, **kwargs)
        
        return orderId
    
    def cover(self, gateway_name: str, symbol: str, price: float, amount: float, **kwargs):
        '''
            如果是单向持仓那么就是买 与 buy功能相同
            如果是双向持仓那么就是平空
        '''
        orderId = self.main_engine.send_order(gateway_name=gateway_name, symbol=symbol, direction=Direction.LONG, offset=Offset.CLOSE, price=price, amount=amount, **kwargs)
        
        return orderId
    
    def cancel_order(self, gateway_name: str, symbol: str, orderid: str) -> bool:
        '''
            撤销订单
        '''
        info = self.main_engine.cancel_order(gateway_name=gateway_name, symbol=symbol, orderid=orderid)

        return info

    def query_order(self, gateway_name: str, symbol: str, orderid: str)-> OrderData or None:
        '''
            查询订单
        '''
        info = self.main_engine.query_order(gateway_name=gateway_name, symbol=symbol, orderid=orderid)

        return info
    
    def query_active_orders(self, gateway_name: str, symbol: str)-> List[OrderData]:
        '''
            查询所有活跃订单
        '''
        info = self.main_engine.query_active_orders(gateway_name=gateway_name, symbol=symbol)

        return info
    
    def query_account(self, gateway_name: str)-> AccountData or None:
        '''
            查询账户 账户级别的查询会返回资产与所有币对的持仓信息
        '''
        info = self.main_engine.query_account(gateway_name=gateway_name)

        return info
    
    def query_position(self, gateway_name: str, symbol: str)-> PositionData or None:
        '''
            查询持仓
        '''
        info = self.main_engine.query_position(gateway_name=gateway_name, symbol=symbol)

        return info
    
    def write_log(self, msg: str, level=LogLevel.INFO, lark_url=None):
        '''
            记录日志
        '''
        self.main_engine.write_log(msg=msg, level=level.value, source=f"{self.strategy_name}", lark_url=lark_url)

    def get_ts(self):
        '''
            获取13位时间戳
        '''
        return int(time.time() * 1000)
    
    def get_contract(self, gateway_name: str, symbol: str)-> ContractData or None:
        '''
            获取合约信息
        '''
        return self.main_engine.gateways[gateway_name].symbol_contract_map.get(symbol, None)

    def set_gateway_param(self, gateway_name: str, params: dict):
        '''
            设置gateway参数
        '''
        self.main_engine.set_gateway_params(gateway_name=gateway_name, params=params)

    def set_topic(self, gateway_name: str, event_type: EventType, params: dict):
        '''
            设置共有数据订阅
        '''
        if gateway_name not in self.topic:
            self.topic[gateway_name] = {}
        self.topic[gateway_name][event_type.value] = params
        
    @abstractmethod
    def on_start(self):
        '''
            策略启动事件
        '''
        pass

    @abstractmethod
    def on_order(self, exchange: Exchange, gateway_name: str, symbol: str, order: OrderData):
        '''
            订单更新事件
        '''
        pass

    @abstractmethod
    def on_trade(self, exchange: Exchange, gateway_name: str, symbol: str, trade: TradeData):
        '''
            成交事件
        '''
        pass
    
    @abstractmethod
    def on_depth(self, exchange: Exchange, gateway_name: str, symbol: str, depth: DepthData):
        '''
            行情更新事件
        '''
        pass

    def on_bar(self, exchange: Exchange, gateway_name: str, symbol: str, bar: BarData):
        '''
            K线更新事件
        '''
        pass

    @abstractmethod
    def on_finish(self):
        '''
            策略结束事件 进程结束前会运行
        '''
        pass
