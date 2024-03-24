import json
import os
import time
import sys
import pathlib
ndSys_PATH = str(pathlib.Path(__file__).parent.parent)
print(f"ndSys_PATH: {ndSys_PATH}")
if ndSys_PATH not in sys.path:
    sys.path.append(ndSys_PATH)
import nodelta # 需要先导入 nodelta 模块 自动配置环境变量
from nodelta.trader.constant import (
    LogLevel, Direction, Offset, Status, Exchange, Product, GatewayName, EventType
)
from nodelta.trader.object import BarData, ContractData, OrderData, TradeData, PositionData, AssetData, AccountData, DepthData, Interval, Event
from nodelta.utils.utility import BarGenerator, ArrayManager
from nodelta.trader.strategy_template import StrategyTemplate
from nodelta.trader.engine import MainEngine
from nodelta.gateway.backtest_cta_gateway import BacktestCtaGateway
import config
from typing import Any, Dict, List, Tuple
import pandas as pd

class BacktestExample(StrategyTemplate):

    def __init__(self, name: str, subscribe_symbols):
        super().__init__(name, subscribe_symbols)

        # 参数与变量
        self.params_name: List[str] = [] # 参数名称
        self.variables_name: List[str] = ['mid_price'] # 变量名称

        self.mid_price = None

        self.minute_bg = BarGenerator(interval = Interval.MINUTE, on_window_bar=self.on_minute_bar, window=60)
        self.hour_array_manager = ArrayManager(100)

    def on_start(self):
        self.write_log(f"{self.get_ts()} on_start 回调")

        # 测试 get_bararray
        bt_gateway = self.main_engine.gateways[GatewayName.BACKTEST_CTA.value]
        bar_array = bt_gateway.get_bararray(symbol='ETH-USDT-SWAP', interval=Interval.MINUTE, window=60, size=5)
        self.write_log(f"====== {self.get_ts()} bar_array: ====== \nopen_ts_array: {bar_array.open_ts_array}\n open_price_array: {bar_array.open}\n high_price_array: {bar_array.high}\n low_price_array: {bar_array.low}\n close_price_array: {bar_array.close}\n volume_array: {bar_array.volume}\n turnover_array: {bar_array.turnover}\n")
        self.on_finish()

    def on_depth(self, exchange: Exchange, gateway_name: str, symbol: str, depth: DepthData):
        pass

    def on_order(self, exchange: Exchange, gateway_name: str, symbol: str, order: OrderData):
        
        self.write_log(f"{self.get_ts()} on_order 回调 {order}")

    def on_trade(self, exchange: Exchange, gateway_name: str, symbol: str, trade: TradeData):
        
        self.param_pos += trade.volume if trade.direction == Direction.LONG else -trade.volume

    def on_finish(self):
        pos = self.query_position(gateway_name=GatewayName.BACKTEST_CTA.value, symbol="ETH-USDT-SWAP")
        self.write_log(f"{self.get_ts()} on_finish 回调 {pos}")
    
    def on_bar(self, exchange: Exchange, gateway_name: str, symbol: str, bar: BarData):

        self.mid_price = (bar.close_price + bar.open_price) / 2
        self.minute_bg.update_bar(bar)

    def on_minute_bar(self, bar: BarData):
        '''
            
        '''
        self.write_log(f"{self.get_ts()} on_minute_bar 回调 {bar}")

    def on_hour_bar(self, bar: BarData):
        '''
            
        '''
        self.write_log(f"{self.get_ts()} on_hour_bar 回调 {bar}")

        self.hour_array_manager.update_bar(bar)

        if not self.hour_array_manager.inited:
            pass
        else:
            msg = f"{self.get_ts()} hour_array_manager:\n open_ts_array: {self.hour_array_manager.open_ts_array}\n open_price_array: {self.hour_array_manager.open}\n high_price_array: {self.hour_array_manager.high}\n low_price_array: {self.hour_array_manager.low}\n close_price_array: {self.hour_array_manager.close}\n volume_array: {self.hour_array_manager.volume}\n turnover_array: {self.hour_array_manager.turnover}\n"
            self.write_log(msg)