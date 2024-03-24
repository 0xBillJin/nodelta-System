import time
import json
from typing import Any, Dict, List, Tuple
import threading
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np

from .gateway import BaseGateway
from .SDK.binance_sdk.binance.um_futures import UMFutures
from .SDK.binance_sdk.binance.websocket.um_futures.websocket_client import UMFuturesWebsocketClient
from ..trader.engine import MainEngine
from ..trader.constant import (
    LogLevel, Direction, Offset, Status, Exchange, Product, GatewayName, EventType, Interval
)
from ..trader.object import ContractData, OrderData, TradeData, PositionData, AssetData, AccountData, DepthData, BarData, Event
from ..utils.utility import BarGenerator, ArrayManager
import traceback

active_status = [Status.SUBMITTING, Status.NOTTRADED, Status.PARTTRADED]  # 活跃委托状态 可撤单
end_status = [Status.ALLTRADED, Status.CANCELLED, Status.REJECTED]  # 结束委托状态 不可撤单

class BacktestCtaGateway(BaseGateway):
    '''
        回测网关
        不考虑资金能否开仓
    '''
    def __init__(self, api: dict = {'key': 'bt', 'secret': 'secret'}): # key='', secret='',
        # === Gateway 基础信息 ===
        self.gateway_name: str = GatewayName.BACKTEST_CTA.value # Gateway名称不需要改
        self.exchange = Exchange.BACKTEST_CTA # 交易所名称不需要改
        super().__init__(api['key'], api['secret'], self.exchange)

        # --- self._backtest_bars_1m 便利用的bar数据
        self._backtest_bars_1m: Dict[int, Dict[str, BarData]] = {} # {ts: {symbol: BarData}} {开盘时间: {合约: BarData}}
        # --- self._store_bars_1m 存储的bar数据 用于get_bararray
        self._store_bars_1m: Dict[int, Dict[str, BarData]] = {} # {ts: {symbol: BarData}} {开盘时间: {合约: BarData}}

        self.sent_order_count: int = 0 # 已发送订单数量
        self.sent_orders: List[OrderData] = [] # 已发送的订单
        self.trades_count: int = 0 # 成交数量
        self.trades: List[TradeData] = [] # 成交记录

        # === 持仓与资产数据 ===
        self.__positions: Dict[str, PositionData] = {} # 持仓数据 {symbol: PositionData}
        self.__upnl_map: Dict[str, float] = {} # 未实现盈亏映射 {symbol: 14.2} update_account 后更新
        self.__cash: float = 0 # 账户资金 买入花钱 卖出得钱 on_trade 后更新 
        self.__account_value: float = 0 # 账户总资产 = 账户资金 + 未实现盈亏 update_account 后更新
        self.__fee_map: Dict[str, float] = {} # 手续费映射 {'USDT': 14.2} on_trade 后更新

        self.__backtesting: bool = False # 是否正在回测
        self.__bt_ts: int = None # 回测系统时间戳
        self.thread_imitate_push_match = threading.Thread(target=self.imitate_push_match, daemon=True)
        self.thread_imitate_push_match.name = 'thread_imitate_push_match'

    def on_init(self):
        '''
            pass
        '''
        pass

    def set_bt_params(
        self,
        start: str, # 开始时间 e.g 2024-01-01
        end: str, # 结束时间 e.g 2024-01-01
        cash_init: float, # 初始资金
        data_path: str, # 数据路径 e.g E:\CoinDatabase\Data
        data_gateway_name: str, # 数据网关名称
        preheat_days: int = 7, # 预热天数
        rate: float = 5 / 10000, # 手续费率
        slippage: float = 0.5 / 10000,  # 滑点
    ):
        '''
            设置回测参数
        '''
        self.start = start
        self.end = end
        self.cash_init, self.__cash = cash_init, cash_init
        self.data_path = data_path
        self.data_gateway_name = data_gateway_name
        self.preheat_days = preheat_days
        self.rate = rate
        self.slippage = slippage

    def load_bar_data(self, start: str, end: str, gateway_name: str, symbols: List[str]) -> Dict[int, Dict[str, List[BarData]]]:
        '''
            读取数据合成1分钟 BarData 列表
        Params:
            start: str, # 开始时间 e.g 2024-01-01
            end: str, # 结束时间 e.g 2024-01-01
            gateway_name: 数据网关名称
            symbols: 订阅的合约列表
        Return:
            Dict[int, Dict[str, List[BarData]]] : {ts: {symbol: BarData}} {开盘时间: {合约: BarData}}
        '''
        bars = {}
        # 根据 close 小数点数量最大值 确定 self.price_precision_map
        self.price_precision_map = {}
        # 根据 volume 小数点数量最大值 确定 self.volume_precision_map
        self.volume_precision_map = {}
        if gateway_name == GatewayName.BINANCE_UM.value:
            # 读取所有订阅币对数据
            for symbol in symbols:
                symbol_dfs = []
                market = symbol.split('-')[-1].upper()
                # E:\CoinDatabase\Data\BINANCE\SWAP\ETHUSDT\ETHUSDT-1m-2023-01-01.csv
                gw_symbol = symbol.split('-')[0].upper() + symbol.split('-')[1].upper()
                data_range = pd.date_range(start, end, freq='1D')
                paths = [f"{self.data_path}\\BINANCE\\{market}\\{gw_symbol}\\{gw_symbol}-1m-{date.strftime('%Y-%m-%d')}.csv" for date in data_range]
                symbol_dfs = [pd.read_csv(path) for path in paths]
                symbol_df = pd.concat(symbol_dfs, axis=0)
                # open_time(13位时间戳)升序
                symbol_df.sort_values(by='open_time', inplace=True)
                # symbol_df 数量是否正确
                bar_1m_should_count = ((datetime.strptime(end, '%Y-%m-%d') - datetime.strptime(start, '%Y-%m-%d')).days + 1) * 24 * 60
                if len(symbol_df) != bar_1m_should_count:
                    msg = f"回测数据数量不正确, 应有数据量{bar_1m_should_count},  实际数据量{len(symbol_df)}"
                    self.main_engine.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.gateway_name)
                # symbol_df --> BarData
                symbol_bars = [BarData(
                    symbol=symbol,
                    exchange=self.exchange,
                    gateway_name=self.data_gateway_name,
                    # open_time13位时间戳转东八区时间
                    datetime=datetime.fromtimestamp(int(bar['open_time']) / 1000, tz=timezone(timedelta(hours=8))),
                    open_ts=int(bar['open_time']),
                    interval=Interval.MINUTE,
                    volume=bar['volume'],
                    turnover=bar['quote_volume'],
                    open_price=bar['open'],
                    high_price=bar['high'],
                    low_price=bar['low'],
                    close_price=bar['close'],
                ) for _, bar in symbol_df.iterrows()]
                # 根据 close 小数点数量最大值 确定 self.price_precision_map
                self.price_precision_map[symbol] = max([self.get_price_precision(bar.close_price) for bar in symbol_bars[:1000]])
                # 根据 volume 小数点数量最大值 确定 self.volume_precision_map
                self.volume_precision_map[symbol] = max([self.get_price_precision(bar.volume) for bar in symbol_bars[:1000]])
                # symbol_bars --> bars
                for bar in symbol_bars:
                    if bar.open_ts not in bars.keys():
                        bars[bar.open_ts] = {}
                    bars[bar.open_ts][symbol] = bar
            # bars 按照时间升序
            bars = dict(sorted(bars.items(), key=lambda x: x[0]))
            # 设置回测系统时间
            self.__bt_ts = list(bars.keys())[0]
        return bars

    def get_price_precision(self, price: float) -> int:
        '''
            获取价格精度
        '''
        price_str = str(price)
        if '.' in price_str:
            return len(price_str.split('.')[1])
        else:
            return 0
        
    def send_order(self, symbol: str, direction: Direction, offset: Offset, price: float, amount: float, **kwargs) -> str:
        '''
            发送订单
            Parameters:
                symbol: nd_symbol 币对
                direction: 方向
                offset: 开平
                price: 价格
                amount: 数量
            Return: 
                报单成功返回 orderId 否则返回空字符串
        '''

        status = Status.NOTTRADED
        self.sent_order_count += 1
        orderid = f"BacktestGateway-ORDER-{int(self.sent_order_count)}"

        # 2. 生成 order_data
        order_data = OrderData(
            symbol=symbol,
            exchange=self.exchange,
            orderid=orderid,
            direction=direction,
            offset=offset,
            price=round(price, self.price_precision_map[symbol]),
            volume=round(amount, self.volume_precision_map[symbol]),
            traded=0,
            status=status,
            ts=self.get_bt_ts()
        )
        # 3. 保存 order_data
        self.sent_orders.append(order_data)
        # 4. 返回 orderid
        return orderid
    
    def get_bt_ts(self) -> int:
        '''
            获取回测系统的13位时间戳
        '''
        return self.__bt_ts
    
    def cancel_order(self, symbol: str, orderid: str):
        '''
            如果订单active_status 则修改为 CANCELLED
            否则不处理
        '''
        for order in self.sent_orders:
            if order.orderid == orderid and order.symbol == symbol:
                if order.status in active_status:
                    order.status = Status.CANCELLED
                return True
        return False
    
    def query_order(self, symbol: str, orderid: str) -> OrderData:
        '''
            查询订单
            查询成功返回 OrderData
            查询失败返回 None
        '''
        for order in self.sent_orders:
            if order.orderid == orderid and order.symbol == symbol:
                return order
        return None
    
    def query_active_orders(self, symbol: str) -> List[OrderData]:
        '''
            查询所有订单
            查询成功返回 list[OrderData]
            查询失败返回 []
        '''
        return [order for order in self.sent_orders if order.status in active_status and order.symbol == symbol]
    
    def query_account(self) -> AccountData or None:
        '''
            查询账户
        '''
        return AccountData(
            exchange=self.exchange,
            gateway_name=self.gateway_name,
            assets={
                'USDT': AssetData(name='USDT', total=self.cash_init, available=self.cash_init), # TODO 总资产与可用资产计算
            },
            positions=self.__positions
        )
    
    def query_position(self, symbol: str) -> PositionData or None:
        '''
            查询持仓
        '''
        return self.__positions.get(symbol, None)
    
    def connect(self, symbols):
        '''
            连接 模拟推送bar数据
        '''
        # 1. 加载数据
        # --- 回测K线
        self._backtest_bars_1m = self.load_bar_data(start=self.start, end=self.end, gateway_name=self.data_gateway_name, symbols=symbols)
        self._backtest_bars_1m = dict(sorted(self._backtest_bars_1m.items(), key=lambda x: x[0], reverse=True)) # ---self._backtest_bars_1m 按照时间降序
        msg = f"回测数据加载完毕, 共{len(self._backtest_bars_1m)}条数据"
        self.main_engine.write_log(msg=msg, level=LogLevel.INFO.value, source=self.gateway_name)
        # --- store_bars_1m
        store_date = datetime.strptime(self.start, '%Y-%m-%d') - timedelta(days=self.preheat_days)
        self._store_bars_1m = self.load_bar_data(start=store_date.strftime('%Y-%m-%d'), end=self.end, gateway_name=self.data_gateway_name, symbols=symbols)
        self._store_bars_1m = dict(sorted(self._store_bars_1m.items(), key=lambda x: x[0], reverse=True)) # ---self._store_bars_1m 按照时间降序
        msg = f"预热数据加载完毕, 共{len(self._store_bars_1m)}条数据"
        self.main_engine.write_log(msg=msg, level=LogLevel.INFO.value, source=self.gateway_name)
        # 2. 策略获取时间戳的方式改为回测系统时间戳
        self.main_engine.strategy.get_ts = self.get_bt_ts
        msg = f"{self.get_bt_ts()} 主引擎：{self.main_engine}"
        self.main_engine.write_log(msg=msg, level=LogLevel.INFO.value, source=self.gateway_name)
        # 3. 回放数据
        self.thread_imitate_push_match.start()

    def imitate_push_match(self):
        '''
            模拟推送公有私有数据 撮合数据
        '''
        last_push_bar_open_ts = 0  # 上一次推送的 bar 开盘时间 初始值为0
        while True:

            try:
                if not self.main_engine.is_event_processing:
                    time.sleep(0.1)
                    msg = f"模拟推送线程等待事件处理循环启动...ing"
                    self.main_engine.write_log(msg=msg, level=LogLevel.INFO.value, source=self.gateway_name)
                    continue

                # === 模拟推送 bar 数据 与撮合 ===
                if EventType.BAR.value in self.main_engine.strategy.topic[self.gateway_name].keys():

                    # --- 如果 last_push_bar_open_ts == self.main_engine.newest_processed_bar_opening_ts 说明可以推送数据
                    if last_push_bar_open_ts != self.main_engine.newest_processed_bar_opening_ts:
                        continue

                    self.__bt_ts, newest_bars = self._backtest_bars_1m.popitem() if self._backtest_bars_1m else (None, None)

                    if newest_bars:

                        self.__backtesting = True

                        # --- 撮合订单 更新持仓
                        self.match_trade(newest_bars)

                        # --- 更新账户与资产等数据
                        self.update_account(newest_bars)

                        # ---推送数据
                        for symbol, newest_bar in newest_bars.items():
                            self.main_engine.put_event(
                                event_type=EventType.BAR,
                                exchange=self.exchange,
                                gateway_name=self.gateway_name,
                                symbol=symbol,
                                data=newest_bar
                            )
                            last_push_bar_open_ts = newest_bar.open_ts
                    # --- 数据推送完毕
                    else:
                        self.__backtesting = False
                        self.main_engine.write_log(f"回测数据推送完毕", level=LogLevel.INFO.value, source=self.gateway_name)
                        self.main_engine.put_event(
                            event_type=EventType.BACKTESTEND,
                            exchange=self.exchange,
                            gateway_name=self.gateway_name,
                            symbol='',
                            data={
                                'gateway_name': self.gateway_name,
                                'report': self.calculate_report(),
                                'msg': True
                            }
                        )
                        self.__backtesting = False
                        break

            except:
                msg = f"模拟推送 bar 数据异常: {traceback.format_exc()}"
                self.main_engine.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.gateway_name)
                self.main_engine.put_event(
                    event_type=EventType.BACKTESTEND,
                    exchange=self.exchange,
                    gateway_name=self.gateway_name,
                    symbol='',
                    data={
                        'gateway_name': self.gateway_name,
                        'report': self.calculate_report(),
                        'msg': False
                    }
                )
                self.__backtesting = False
                break

    def calculate_report(self):
        '''
            计算回测结果
        '''
        return self.sent_orders

    def match_trade(self, newest_bars):
        '''
        撮合订单
        newest_bars: Dict[str, BarData] = {} # {symbol: BarData}

        如果有成交
        1. 修改如下数据：
        self.__positions: Dict[str, PositionData] = {} # 持仓数据 {symbol: PositionData}
        self.__upnl: float = 0 # 未实现盈亏
        self.cash: float = 0 # 账户资金
        self.cash_available: float = 0 # 可用资金
        self.cash_frozen: float = 0 # 冻结资金
        2. 生成 TradeData 并保存到 self.trades ; 推送事件
        3. 修改 self.sent_orders 中的订单状态 以及 traded 字段; 推送事件
        '''
        # 1. 撮合订单
        for order in self.sent_orders:

            if order.status in active_status:
                order_price = order.price
                symbol = order.symbol
                newest_bar = newest_bars[symbol]
                # --- 判断是否成交
                is_order_match = False
                if order.direction == Direction.LONG:
                    if order_price > newest_bar.low_price:
                        is_order_match = True
                else:
                    if order_price < newest_bar.high_price:
                        is_order_match = True
                if is_order_match:
                    # --- 成交
                    # 1. 修改 self.sent_orders 中的订单状态 以及 traded 字段; 推送事件
                    order.status = Status.ALLTRADED
                    order.traded = order.volume
                    self.main_engine.write_log(msg=f"Before put ORDER event, order: {order}", level=LogLevel.INFO.value, source=self.gateway_name)
                    self.main_engine.put_event(
                        event_type=EventType.ORDER,
                        exchange=self.exchange,
                        gateway_name=self.gateway_name,
                        symbol=symbol,
                        data=order
                    )
                    self.main_engine.write_log(msg=f"After put ORDER event, order: {order}", level=LogLevel.INFO.value, source=self.gateway_name)
                    # 2. 生成 TradeData 并保存到 self.trades ; 推送事件
                    # --- 成交价规则
                    # --- --- 买入订单 
                    # --- --- --- 买入价格 > 开盘价 以开盘价 + 滑点成交
                    # --- --- --- 买入价格 <= 开盘价 以买入价格成交
                    # --- --- 卖出订单
                    # --- --- --- 卖出价格 < 开盘价 以开盘价 - 滑点成交
                    # --- --- --- 卖出价格 >= 开盘价 以卖出价格成交
                    if order.direction == Direction.LONG:
                        if order_price > newest_bar.open_price:
                            trade_price = round(newest_bar.open_price * (1 + self.slippage), self.price_precision_map[symbol])
                        else:
                            trade_price = order_price
                    else:
                        if order_price < newest_bar.open_price:
                            trade_price = round(newest_bar.open_price * (1 - self.slippage), self.price_precision_map[symbol])
                        else:
                            trade_price = order_price
                    trade = TradeData(
                        symbol=symbol,
                        exchange=self.exchange,
                        orderid=order.orderid,
                        tradeid=f"BacktestGateway-TRADE-{int(self.trades_count)}",
                        direction=order.direction,
                        offset=order.offset,
                        price=trade_price,
                        volume=order.volume,
                        ts=newest_bar.open_ts
                    )
                    msg = f"{self.gateway_name} 成交记录 {trade}"
                    self.main_engine.write_log(msg=msg, level=LogLevel.INFO.value, source=self.gateway_name)
                    self.trades_count += 1
                    self.trades.append(trade)
                    self.main_engine.put_event(
                        event_type=EventType.TRADE,
                        exchange=self.exchange,
                        gateway_name=self.gateway_name,
                        symbol=symbol,
                        data=trade
                    )
                    # --- 修改如下数据：
                    # self.__cash
                    if order.direction == Direction.LONG:
                        self.__cash -= trade.price * trade.volume
                    else:
                        self.__cash += trade.price * trade.volume
                    # self.__fee_map
                    fee_symbol = trade.symbol.split('-')[1].upper()
                    fee = trade.price * trade.volume * self.rate
                    if fee_symbol not in self.__fee_map.keys():
                        self.__fee_map[fee_symbol] = 0
                    self.__fee_map[fee_symbol] += fee

                    # 3. 修改如下数据：
                    # self.__positions: Dict[str, PositionData] = {} # 持仓数据 {symbol: PositionData}
                    position = self.__positions.get(symbol, None)
                    if not position:
                        position = PositionData(
                            symbol=symbol,
                            netQty=order.volume if order.direction == Direction.LONG else -order.volume,
                            avgPrice=order.price
                        )
                        self.__positions[symbol] = position
                    else:
                        _pos_cost = -1 * ( position.avgPrice * position.netQty + order.price * (order.volume if order.direction == Direction.LONG else -order.volume ) )
                        position.netQty += order.volume if order.direction == Direction.LONG else -order.volume
                        if position.netQty == 0:
                            position.avgPrice = None
                        else:
                            position.avgPrice = abs(_pos_cost / position.netQty)

    def update_account(self, newest_bars):
        '''
            更新账户与资产等数据
        '''

        # __upnl_map
        for symbol, position in self.__positions.items():
            newest_bar = newest_bars[symbol]
            if position.netQty:
                self.__upnl_map[symbol] = (newest_bar.close_price - position.avgPrice) * position.netQty
            else:
                self.__upnl_map[symbol] = 0
        
        # __account_value
        self.__account_value = self.__cash + sum(self.__upnl_map.values())

    def get_bararray(self, symbol: str, interval: Interval, window: int, size: int) -> ArrayManager or None:
        '''
            获取当前最新的 bararray 所有已经完成的 bar
        :param symbol: 合约 nd symbol
        :param interval: 周期
        :param window: 周期数 比如15分钟周期 15
        :param size: ArrayManager size
        Return:
            ArrayManager 如果成功获取
            None 如果获取失败
        '''
        # 根据 interval 与 window 确定 所需的1分钟bar数量
        interval_minutes_map = {
            Interval.MINUTE: 1,
            Interval.HOUR: 60,
            Interval.DAILY: 60 * 24,
            Interval.WEEKLY: 60 * 24 * 7,
        }
        interval_minutes = interval_minutes_map.get(interval, None)
        if not interval_minutes:
            return None
        need_bars_count = int(interval_minutes * window * size)
        # 根据当前时间戳获取self._store_bars_1m 中最新的 need_bars_count 个 bar
        now_ts = self.get_bt_ts()
        #--- self._store_bars_1m 按照时间降序
        self._store_bars_1m = dict(sorted(self._store_bars_1m.items(), key=lambda x: x[0], reverse=True))
        sym_newest_bars: List[BarData] = []
        for ts, bars in self._store_bars_1m.items():
            if ts < now_ts:
                sym_bar = bars.get(symbol, None)
                if sym_bar:
                    sym_newest_bars.append(sym_bar)
            if len(sym_newest_bars) == need_bars_count:
                break
        if len(sym_newest_bars) != need_bars_count:
            return None
        # newest_bars --> ArrayManager
        # sym_newest_bars 按照时间升序
        sym_newest_bars = sorted(sym_newest_bars, key=lambda x: x.open_ts)
        array_manager = ArrayManager(size=size)
        def __update_bar(bar: BarData):
            array_manager.update_bar(bar)
        bar_generator = BarGenerator(interval=interval, on_window_bar=__update_bar, window=window)
        for bar in sym_newest_bars:
            bar_generator.update_bar(bar)
        if array_manager.inited:
            return array_manager
        else:
            return None
