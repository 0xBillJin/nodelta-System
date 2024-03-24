import json
import time

import nodelta # 需要先导入 nodelta 模块 自动配置环境变量
from nodelta.trader.constant import (
    LogLevel, Direction, Offset, Status, Exchange, Product, GatewayName, EventType
)
from nodelta.trader.object import ContractData, OrderData, TradeData, PositionData, AssetData, AccountData, DepthData, Event
from nodelta.trader.strategy_template import StrategyTemplate
from nodelta.trader.engine import MainEngine
from nodelta.gateway.binance_gateway import BinanceUmGateway

class TestBnStrategy(StrategyTemplate):
    '''
        测试币安交易所接口网关
        验证：
        1. symbol_contract_map、gw2nd_symbol_map、nd2gw_symbol_map 是否正确
        2. 单向、双向持仓下 
            a. 买卖
            b. 撤单
            c. 查询订单
            d. 查询全部订单
            e. 查询账户
            f. 查询持仓
    '''
    def __init__(self, name: str, subscribe_symbols):
        super().__init__(name, subscribe_symbols)

    def on_start(self):
        self.write_log(f"on_start 回调")

        self.ts_lastPrintDepth: int = 0
        self.check_symbol: str = "ETH-USDT-SWAP" # 测试交易的币对

        self.send_order_count = {
            "buy": {'num':0, 'orderIds':[]},
            "sell": {'num':0, 'orderIds':[]},
            "short": {'num':0, 'orderIds':[]},
            "cover": {'num':0, 'orderIds':[]}
        }

        # 1. symbol_contract_map、gw2nd_symbol_map、nd2gw_symbol_map 是否正确
        GATEWAY_NAME = GatewayName.BINANCE_UM.value
        # self.symbol_contract_map = self.main_engine.gateways[GATEWAY_NAME].symbol_contract_map
        # self.write_log(f"【待验证】symbol_contract_map: {self.check_symbol}: {self.symbol_contract_map[self.check_symbol]}")
        # self.write_log(f"【待验证】symbol_contract_map: 'ETH-USDT-FUTURES-230929': {self.symbol_contract_map['ETH-USDT-FUTURES-230929']}")

        # self.gw2nd_symbol_map = self.main_engine.gateways[GATEWAY_NAME].gw2nd_symbol_map
        # self.write_log(f"【待验证】gw2nd_symbol_map: {self.gw2nd_symbol_map}")
        # self.nd2gw_symbol_map = self.main_engine.gateways[GATEWAY_NAME].nd2gw_symbol_map
        # self.write_log(f"【待验证】nd2gw_symbol_map: {self.nd2gw_symbol_map}")

    def on_depth(self, exchange: Exchange, gateway_name: str, symbol: str, depth: DepthData):

        if symbol != self.check_symbol:
            return
        
        if self.get_ts() - self.ts_lastPrintDepth > 1000 * 30:
            self.write_log(f"on_depth 回调正常 {depth}")
            self.ts_lastPrintDepth = self.get_ts()

        # 测试下单
        if self.send_order_count['buy']['num'] < 1:
            self.write_log(f"测试下单: buy")
            orderId = self.first_orderId = self.buy(gateway_name, symbol, 1600, 0.005)
            self.send_order_count['buy']['num'] += 1
            self.send_order_count['buy']['orderIds'].append(orderId)
            self.write_log(f"测试下单: buy orderId: {orderId}")
            return
        
        if self.send_order_count['sell']['num'] < 1:
            self.write_log(f"测试下单: sell")
            orderId = self.sell(gateway_name, symbol, 3000, 0.005)
            self.send_order_count['sell']['num'] += 1
            self.send_order_count['sell']['orderIds'].append(orderId)
            self.write_log(f"测试下单: sell orderId: {orderId}")

            # 测试撤单
            self.write_log(f"测试撤单: {orderId}")
            cancel_bool = self.cancel_order(gateway_name, symbol, orderId)
            self.write_log(f"测试撤单: {orderId} {cancel_bool}")
            return
        
        if self.send_order_count['short']['num'] < 1:
            self.write_log(f"测试下单: short")
            orderId = self.short(gateway_name, symbol, depth.bids[0][0], 0.005)
            self.send_order_count['short']['num'] += 1
            self.send_order_count['short']['orderIds'].append(orderId)
            self.write_log(f"测试下单: short orderId: {orderId}")

            # 测试查询订单
            self.write_log(f"测试查询订单: {orderId}")
            order = self.query_order(gateway_name, symbol, orderId)
            self.write_log(f"测试查询订单: {orderId} {order}")
            return
        
        if self.send_order_count['cover']['num'] < 1:
            self.write_log(f"测试下单: cover")
            orderId = self.cover(gateway_name, symbol, 1100, 0.005)
            self.send_order_count['cover']['num'] += 1
            self.send_order_count['cover']['orderIds'].append(orderId)
            self.write_log(f"测试下单: cover orderId: {orderId}")

            # 测试查询全部订单
            self.write_log(f"测试查询全部订单")
            orders = self.query_active_orders(gateway_name, symbol)
            self.write_log(f"测试查询全部订单: {orders}")
            return

    def on_order(self, exchange: Exchange, gateway_name: str, symbol: str, order: OrderData):
        
        self.write_log(f"on_order 回调 {order}")

    def on_trade(self, exchange: Exchange, gateway_name: str, symbol: str, trade: TradeData):
        
        self.write_log(f"on_trade 回调 {trade}")

        # 测试查询持仓
        self.write_log(f"测试查询持仓")
        position = self.query_position(gateway_name, self.check_symbol)
        self.write_log(f"测试查询持仓: {position}")
        
        # 测试查询账户
        self.write_log(f"测试查询账户")
        account = self.query_account(gateway_name)
        self.write_log(f"测试查询账户: {account}")

if __name__ == "__main__":

    # 1. 创建Gateway实例
    bn_UsdSwap_gateway = BinanceUmGateway(key, secret)
    # 2. 创建策略实例
    tset_strategy = TestBnStrategy(name="TestBnStrategy_dudalPos",\
                                 subscribe_symbols={GatewayName.BINANCE_UM.value :[
                                    "ETH-USDT-SWAP",
                                    "ETHUSDT-USDT-FUTURES-230929",
                                 ]})
    # 3. 创建MainEngine实例
    engine = MainEngine("TestBnEngine_dudalPos", path=r'E:\HftStrategy\NodeltaHftSystem\OutPut')
    engine.add_gateways([bn_UsdSwap_gateway])
    engine.add_strategy(tset_strategy)

    engine.start()