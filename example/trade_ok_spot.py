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


class TradeOkSpotStrategy(StrategyTemplate):
    '''
        测试OKX现货交易
        挂撤单 订单与成交回报
    '''
    def on_start(self):
        self.write_log(f"on_start 回调")   

        # 挂撤单标记
        self._is_send_test_order: bool = False
        self.buy_orderid, self.sell_orderid = "", ""
        self.last_send_order_ts: int = 0
        self._is_cancel_order: bool = False

        # on_depth 中间价统计
        self.mid_price_list: list = []

    def on_depth(self, exchange: Exchange, gateway_name: str, symbol: str, depth: DepthData):
        
        mid_price = (depth.bids[0].price + depth.asks[0].price) / 2
        self.mid_price_list.append(mid_price)
        if len(self.mid_price_list) > 60:
            self.mid_price_list.pop(0)
            msg = f"过去60tick中间价均值: {sum(self.mid_price_list) / len(self.mid_price_list)}\t最新中间价: {mid_price}"
            self.write_log(msg)

        if not self._is_send_test_order:
            self._is_send_test_order = True
            self.buy_orderid = self.buy(gateway_name=gateway_name, symbol=symbol, price=mid_price*1.2, amount=0.01)
            self.sell_orderid = self.sell(gateway_name=gateway_name, symbol=symbol, price=mid_price*0.8, amount=0.01)
            self.write_log(f"远端挂单测试: buy_orderid: {self.buy_orderid}, sell_orderid: {self.sell_orderid}")
            self.last_send_order_ts = self.get_ts()

            # 查询订单
            if self.buy_orderid:
                buy_info = self.query_order(gateway_name=gateway_name, symbol=symbol, orderid=self.buy_orderid)
                self.write_log(f"查询订单 buy_info: {buy_info}")
            if self.sell_orderid:
                sell_info = self.query_order(gateway_name=gateway_name, symbol=symbol, orderid=self.sell_orderid)
                self.write_log(f"查询订单 sell_info: {sell_info}")

        if not self._is_cancel_order and \
            (self.buy_orderid is not None and self.sell_orderid is not None) and \
            self.get_ts() - self.last_send_order_ts > 1000 * 20:
            self._is_cancel_order = True
            self.write_log(f"开始撤单测试")
            is_cancel_buy = self.cancel_order(gateway_name=gateway_name, symbol=symbol, orderid=self.buy_orderid)
            is_cancel_sell = self.cancel_order(gateway_name=gateway_name, symbol=symbol, orderid=self.sell_orderid)
            msg = f"撤单测试结果: is_cancel_buy: {is_cancel_buy}, is_cancel_sell: {is_cancel_sell}"
            self.write_log(msg)
        
    def on_bar(self, exchange: Exchange, gateway_name: str, symbol: str, bar: BarData):
        msg = f"on_bar 回调 {bar}"
        self.write_log(msg)

    def on_order(self, exchange: Exchange, gateway_name: str, symbol: str, order: OrderData):
        self.write_log(f"on_order 回调 {order}")

    def on_trade(self, exchange: Exchange, gateway_name: str, symbol: str, trade: TradeData):
        self.write_log(f"on_trade 回调 {trade}")

    def on_finish(self):
        self.write_log(f"on_finish 策略结束")


def main(api: dict,
        name: str,
        subscribe_symbols: list,
        path: str
        ):
    '''
        入口函数
    '''
    # 1. 创建 GateWay
    okx_gateway = OkxGateway(api)
    # 2. 创建 策略实例
    strategy = TradeOkSpotStrategy(name=name, subscribe_symbols={
        GatewayName.OKX.value: subscribe_symbols
    })
    # strategy.set_topic(gateway_name=GatewayName.OKX.value, event_type=EventType.DEPTH, params={})
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
        name="test_TradeOkSpotStrategy",
        subscribe_symbols=["SOL-USDT-SPOT"],
        path=r"./log_example"
        )
    
