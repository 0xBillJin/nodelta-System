import sys
import pathlib
ndSys_PATH = str(pathlib.Path(__file__).parent.parent)
print(f"ndSys_PATH: {ndSys_PATH}")
if ndSys_PATH not in sys.path:
    sys.path.append(ndSys_PATH)

import nodelta # 需要先导入 nodelta 模块 自动配置环境变量
import config
from nodelta.trader.constant import (
    LogLevel, Direction, Offset, Status, Exchange, Product, GatewayName, EventType, Interval
)
from nodelta.trader.object import ContractData, OrderData, TradeData, PositionData, AssetData, AccountData, DepthData, BarData, Event
from nodelta.trader.strategy_template import StrategyTemplate
from nodelta.trader.engine import MainEngine
from nodelta.gateway.okx_gateway import OkxGateway

class PrintOkxStrategy(StrategyTemplate):

    def on_start(self):
        self.write_log(f"on_start 回调")
        self.spread_list = []
        self.ask_bid_qty_list = []
        self.ts_lastPrintDepth = 0

        contract = self.get_contract(gateway_name=GatewayName.OKX.value, symbol="ETH-USDT-FUTURES-231229")
        self.write_log(f"on_start 回调contract: {contract}")

    def on_depth(self, exchange: Exchange, gateway_name: str, symbol: str, depth: DepthData):
        
        self.ts_lastPrintDepth = self.get_ts()

        if depth.bids[0][1] * depth.asks[0][1] == 0:
            return
        
        self.spread_list.append(depth.asks[0][0] - depth.bids[0][0])
        self.ask_bid_qty_list.append(depth.asks[0][1] / depth.bids[0][1])
        if len(self.spread_list) > 30:
            price_diff = self.spread_list[-1] - self.spread_list[0]
            mean_ = sum(self.ask_bid_qty_list) / len(self.ask_bid_qty_list)
            max_ = max(self.ask_bid_qty_list)
            min_ = min(self.ask_bid_qty_list)
            msg = f"最近30个depth 价格变化{price_diff} 平均买卖量比: {mean_}, 最大买卖量比: {max_}, 最小买卖量比: {min_}"
            self.write_log(msg)
            self.ask_bid_qty_list.clear()
            self.spread_list.clear()
        self.write_log(f"on_depth 回调 {depth}")

    def on_order(self, exchange: Exchange, gateway_name: str, symbol: str, order: OrderData):
        
        self.write_log(f"on_order 回调 {order}")

    def on_trade(self, exchange: Exchange, gateway_name: str, symbol: str, trade: TradeData):
        
        self.write_log(f"on_trade 回调 {trade}")

    def on_finish(self):
        self.write_log(f"on_finish 策略结束")

def main(api: dict,
        name: str,
        subscribe_symbols: list,
        ):
    '''
        入口函数
    '''
    # 1. 创建 GateWay
    okx_gateway = OkxGateway(api)
    # 2. 创建 策略实例
    strategy = PrintOkxStrategy(name=name, subscribe_symbols={
        GatewayName.OKX.value: subscribe_symbols
    })
    strategy.set_topic(gateway_name=GatewayName.OKX.value, event_type=EventType.DEPTH, params={})
    # 3. 创建 MainEngine
    main_engine = MainEngine()
    main_engine.add_gateways([okx_gateway])
    main_engine.add_strategy(strategy)

    # 4. 启动 MainEngine
    main_engine.start()

if __name__ == '__main__':

    API = config.get_api("OKX", 'test')

    main(api=API,
        name="PrintOKXDepthStrategy",
        subscribe_symbols=["ETH-USDT-SWAP"],
        )
