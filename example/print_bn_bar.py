import json
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
from nodelta.trader.object import BarData, ContractData, OrderData, TradeData, PositionData, AssetData, AccountData, DepthData, Event
from nodelta.trader.strategy_template import StrategyTemplate
from nodelta.trader.engine import MainEngine
from nodelta.gateway.binance_gateway import BinanceUmGateway
import config

class PrintBarStrategy(StrategyTemplate):
    '''
    用于打印币安的深度数据 可用于 Linux 服务器测试
    '''
    def __init__(self, name: str, subscribe_symbols):
        super().__init__(name, subscribe_symbols)

    def on_start(self):
        self.write_log(f"on_start 回调")
        # bn_symbol_contract_map = self.main_engine.gateways[GatewayName.BINANCE_UM.value].symbol_contract_map
        # self.write_log(f"【待验证】symbol_contract_map: {bn_symbol_contract_map}")

        self.ts_lastPrintDepth: int = 0
        self.is_send_test_order: bool = False

    def on_depth(self, exchange: Exchange, gateway_name: str, symbol: str, depth: DepthData):

        pass
        
        # if self.get_ts() - self.ts_lastPrintDepth > 1000 * 10:
        #     self.write_log(f"on_depth 回调正常 {depth}")
        #     self.ts_lastPrintDepth = self.get_ts()
        # if not self.is_send_test_order:
        #     self.is_send_test_order = True
        #     buy_orderid = self.buy(gateway_name=gateway_name, symbol=symbol, price=666.5, amount=0.01, timeInForce="GTC")
        #     sell_orderid = self.sell(gateway_name=gateway_name, symbol=symbol, price=11111.5, amount=0.01, timeInForce="IOC")
        #     self.write_log(f"buy_orderid: {buy_orderid}, sell_orderid: {sell_orderid}")

    def on_bar(self, exchange: Exchange, gateway_name: str, symbol: str, bar: BarData):
        msg = f"on_bar 回调 {bar}"
        self.write_log(msg)

    def on_order(self, exchange: Exchange, gateway_name: str, symbol: str, order: OrderData):
        
        self.write_log(f"on_order 回调 {order}")

    def on_trade(self, exchange: Exchange, gateway_name: str, symbol: str, trade: TradeData):
        
        self.write_log(f"on_trade 回调 {trade}")

    def on_finish(self):
        
        self.write_log(f"on_finish 策略结束")

def main(
        name: str,
        subscribe_symbols: list,
        ):
    '''
        入口函数
    '''
    # 1. 创建 GateWay
    API = config.get_api('BINANCE', 'gwsacount_read')
    binance_gateway = BinanceUmGateway(API)
    # 2. 创建 策略实例
    strategy = PrintBarStrategy(name=name, subscribe_symbols={
        GatewayName.BINANCE_UM.value: subscribe_symbols
    })
    strategy.set_topic(gateway_name=GatewayName.BINANCE_UM.value, event_type=EventType.BAR, params={})

    # 3. 创建 MainEngine
    main_engine = MainEngine()
    main_engine.add_gateways([binance_gateway])
    main_engine.add_strategy(strategy)

    # 4. 启动 MainEngine
    main_engine.start()

if __name__ == '__main__':

    main(
        name="PrintDepthStrategy",
        subscribe_symbols=["ETH-USDT-SWAP"],
        )
