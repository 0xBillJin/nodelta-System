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
import copy




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
        self.__account_value: float = 0 # 账户总资产 = 账户资金 + 持仓价值 update_account 后更新
        self.__fee_map: Dict[str, float] = {} # 手续费映射 {'USDT': 14.2} on_trade 后更新

        self.__backtesting: bool = False # 是否正在回测
        self.__bt_ts: int = None # 回测系统时间戳

        # === 回测时序数据 ===
        self.bt_data = [] # 回测数据

    def on_init(self):
        '''
            pass
        '''
        pass

    def add_main_engine(self, main_engine): # : MainEngine
        self.main_engine = main_engine
        # 函数替换
        # --- main_engine.start --> self.start_backtest
        self.main_engine.start = self.start_backtest

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

        # 设置回测系统时间为北京时间的  start 08:00:00
        self.__bt_ts = int(datetime.strptime(start, '%Y-%m-%d').replace(hour=8, minute=0, second=0, microsecond=0, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)

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
        # --- 推送事件
        self.main_engine.put_event(
            event_type=EventType.ORDER,
            exchange=self.exchange,
            gateway_name=self.gateway_name,
            symbol=symbol,
            data=order_data
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
    
    def connect(self, symbols: List[str]):
        '''
            连接交易所
        '''
        self.subscribed_nd_symbols = symbols.copy()  # 被订阅的nd币对

    def _init_bars(self, symbols):
        '''
            初始化回测数据
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
    
    def start_backtest(self):
        '''
            开始回测
        '''
        # A. 回测时间
        # --- .strategy.get_ts --> self.get_bt_ts
        self.main_engine.strategy.get_ts = self.get_bt_ts
        # B. 回测数据
        symbols = self.main_engine.strategy.subscribe_symbols.get(self.gateway_name, []) # 订阅的合约
        self._init_bars(symbols)

        msg = f"开始回测"
        self.main_engine.write_log(msg=msg, level=LogLevel.INFO.value, source=self.gateway_name)

        # 0. 策略：注册on_finish 运行on_start 事件
        try:
            self.main_engine.register_finish_func()
            self.main_engine.strategy.on_start()
        except Exception as e:
            msg = f"{self.main_engine.strategy.strategy_name} register_finish_func or on_start 异常: {e}"
            self.main_engine.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.main_engine.engine_name)
            raise Exception(msg)
        
        # 1. 连接所有交易所 订阅行情与成交数据
        for gateway_name, gateway in self.main_engine.gateways.copy().items():
            try:
                symbols = self.main_engine.strategy.subscribe_symbols.get(gateway_name, []) # 订阅的合约
                print(f"{gateway_name} 正在订阅 {self.main_engine.strategy.subscribe_symbols}")
                print(f"strategy.subscribe_symbols.keys: {self.main_engine.strategy.subscribe_symbols.keys()}")
                if symbols:
                    # 1. check symbol 命名规范
                    for symbol in symbols:
                        if not self.main_engine._check_symbol_name(symbol):
                            msg = f"symbol 命名规范错误: {symbol}, 请检查合约命名规则"
                            self.main_engine.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.main_engine.engine_name)
                            raise Exception(msg)
                    # 2. 链接交易所
                    gateway.connect(symbols) # 连接交易所
                else:
                    del self.main_engine.gateways[gateway_name]
                    msg = f"Gateway: {gateway_name} 订阅的合约为空, 删除该Gateway"
                    self.main_engine.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.main_engine.engine_name)
            except Exception as e:
                msg = f"Gateway: {gateway_name} 订阅数据失败: {e}"
                self.main_engine.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.main_engine.engine_name)

            # 2. 加载数据启动事件处理循环
            self.main_engine.write_log(msg=f"事件处理循环启动", level=LogLevel.INFO.value, source=self.main_engine.engine_name)
            self.is_event_processing = True

        last_push_bar_open_ts = 0
        while True:

            # === 模拟推送 bar 数据 与撮合 ===
            try:
               
                if EventType.BAR.value in self.main_engine.strategy.topic[self.gateway_name].keys():

                    open_ts, newest_bars = self._backtest_bars_1m.popitem() if self._backtest_bars_1m else (None, None)

                    if newest_bars:

                        self.__bt_ts = int(open_ts + 60 * 1000)

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

            # === 处理所有事件 ===
            while self.main_engine.get_queue_event().qsize():
                try:
                    event : Event = self.main_engine.get_queue_event().get()
                    handler = self.main_engine.event_handlers.get(event.event_type)
                    if handler is not None:
                        handler(event.exchange, event.gateway_name, event.symbol, event.data)
                        # --- 如果是 trade 事件则自动打印 log
                        if event.event_type in [EventType.TRADE]:
                            msg = f"{event.event_type} Event Done; Data: {event.data}"
                            self.main_engine.write_log(msg=msg, level=LogLevel.INFO.value, source=self.main_engine.engine_name)
                        # --- 如果是 bar 事件更新 self.newest_processed_bar_opening_ts
                        if event.event_type == EventType.BAR:
                            self.main_engine.newest_processed_bar_opening_ts = event.data.open_ts
                            # --- 储存回测数据
                            self.sync_bt_data(newest_bars)
                    elif event.event_type == EventType.BACKTESTEND:
                        self.main_engine.strategy.on_finish()
                        msg = f"回测结束"
                        self.main_engine.write_log(msg=msg, level=LogLevel.INFO.value, source=self.main_engine.engine_name)
                        bt_resopnse = event.data
                        return bt_resopnse
                    else:
                        msg = f"事件处理函数不存在: {event.event_type}"
                        self.main_engine.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.main_engine.engine_name)
                except Exception as e:
                    msg = f"{self.main_engine.engine_name} 事件处理异常: {e}"
                    self.main_engine.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.main_engine.engine_name)

    def calculate_report(self):
        '''
            计算回测结果
        return -> dict
        {
            'start': str, # 开始时间
            'end': str,
            'cash_init': float,
            'account_value': float,
            'pnl_ratio': float,
            'sharpe_ratio': float,
            'max_drawdown_ratio': float,
            'max_drawdown_start_ts': int,
            'max_drawdown_end_ts': int,
            'win_ratio': float,
            'win_to_loss_ratio': float,
            'fee': float,
            'trades_count': int,
            'trades': List[TradeData],
            'bt_data': list
        }
        '''
        # 1. 计算 pnl
        pnl = self.__account_value - self.cash_init
        pn_ratio = round(pnl / self.cash_init * 100, 2)
        # 2. 计算 sharpe_ratio
        # --- 取出每日 08:00 的 account_value
        account_value_df = pd.DataFrame(self.bt_data, columns=['ts', 'account_value'])
        account_value_df['date'] = account_value_df['ts'].apply(lambda x: datetime.fromtimestamp(x / 1000, tz=timezone(timedelta(hours=8))).strftime('%Y-%m-%d'))
        account_value_df = account_value_df.groupby('date').apply(lambda x: x.iloc[0]).reset_index(drop=True)
        # --- 计算日收益率
        account_value_df['daily_return'] = account_value_df['account_value'].pct_change()
        # --- 计算 sharpe_ratio
        std_dev = account_value_df['daily_return'].std()
        if std_dev == 0:
            sharpe_ratio = 0
        else:
            sharpe_ratio = round(account_value_df['daily_return'].mean() / std_dev * np.sqrt(365), 2)        # 3. 计算 max_drawdown_ratio
        # --- account_value_df ts(升序) account_value
        account_value_df = pd.DataFrame(self.bt_data, columns=['ts', 'account_value'])
        account_value_df['max2here'] = account_value_df['account_value'].expanding().max()
        account_value_df['dd2here'] = account_value_df['account_value'] / account_value_df['max2here'] - 1
        max_drawdown_end_ts, max_draw_down = tuple(account_value_df.sort_values(by=['dd2here']).iloc[0][['ts', 'dd2here']])
        max_drawdown_start_ts = account_value_df[account_value_df['ts'] <= max_drawdown_end_ts].sort_values(by='account_value', ascending=False).iloc[0]['ts']

        # 5. 计算 fee
        fee = sum(self.__fee_map.values())

        # 6. 计算 trades_count
        trades_count = len(self.trades) if self.trades else 0

        report = {
            'start': self.start,
            'end': self.end,
            'cash_init': self.cash_init,
            'account_value': self.__account_value,
            'pnl': pnl,
            'pnl_ratio': pn_ratio,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown_ratio': max_draw_down,
            'max_drawdown_start_ts': int(max_drawdown_start_ts),
            'max_drawdown_end_ts': int(max_drawdown_end_ts),
            'fee': fee,
            'trades_count': trades_count,
            'trades': self.trades,
            'bt_data': self.bt_data
        }
        return report
    
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
                # --- 是否是上一根K线的订单
                if self.__bt_ts - order.ts == 60 * 1000:
                    _send_from_last_bar = True
                else:
                    _send_from_last_bar = False
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
                    order.ts = self.__bt_ts
                    self.main_engine.put_event(
                        event_type=EventType.ORDER,
                        exchange=self.exchange,
                        gateway_name=self.gateway_name,
                        symbol=symbol,
                        data=order
                    )
                    # 2. 生成 TradeData 并保存到 self.trades ; 推送事件
                    # --- 成交价规则
                    # --- --- 买入订单 
                    # --- --- --- 买入价格 > 开盘价 以开盘价 + 滑点成交
                    # --- --- --- 买入价格 <= 开盘价 以买入价格成交
                    # --- --- 卖出订单
                    # --- --- --- 卖出价格 < 开盘价 以开盘价 - 滑点成交
                    # --- --- --- 卖出价格 >= 开盘价 以卖出价格成交
                    if order.direction == Direction.LONG:
                        if order_price > newest_bar.open_price and _send_from_last_bar:
                            trade_price = round(newest_bar.open_price * (1 + self.slippage), self.price_precision_map[symbol])
                        else:
                            trade_price = order_price
                    else:
                        if order_price < newest_bar.open_price and _send_from_last_bar:
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
                        ts=self.__bt_ts
                    )
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
                            avgPrice=trade.price
                        )
                        self.__positions[symbol] = position
                    else:
                        position.netQty += order.volume if order.direction == Direction.LONG else -order.volume
                        position.netQty = round(position.netQty, self.volume_precision_map[symbol])
                        if position.netQty == 0:
                            position.avgPrice = None
                        else:
                            position.avgPrice = abs((position.avgPrice * (position.netQty - order.volume) + trade.price * order.volume) / position.netQty)


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
        
        # __account_value = __cash + 持仓价值 - fee
        # --- 例如 本金1W USDT 买入 2BTC（price 1W）; cash = 1 - 1*2 = -1W; 持仓价值 = 2*1W = 2W; account_value = -1W + 2W = 1W
        # --- BTC 价格变为 1.1W 时; 持仓价值 = 2*1.1W = 2.2W; account_value = -1W + 2.2W = 1.2W
        # --- 持仓价值 = sum([持仓数量 * 最新价格])
        pos_value = sum([self.__positions[symbol].netQty * newest_bars[symbol].close_price for symbol in self.__positions.keys()])
        self.__account_value = self.__cash + pos_value - sum(self.__fee_map.values())

    def sync_bt_data(self, newest_bars):
        '''
            储存回测数据
        '''
        # 1. 当前回测数据
        positions_dict = {}
        for symbol, position in self.__positions.items():
            positions_dict[symbol] = {
                'netQty': position.netQty,
                'avgPrice': position.avgPrice
                }
        bt_data = {
            'ts': self.__bt_ts,
            'positions_map': positions_dict,
            'upnl_map': copy.deepcopy(self.__upnl_map),
            'fee_map': copy.deepcopy(self.__fee_map),
            'cash': self.__cash,
            'account_value': self.__account_value,
        }
        # 2.币对最新价
        for symbol, newest_bar in newest_bars.items():
            bt_data[f"{symbol}_price"] = newest_bar.close_price
        # 3. 策略variables_name中的所有变量
        for variable_name in self.main_engine.strategy.variables_name:
            bt_data[variable_name] = getattr(self.main_engine.strategy, variable_name)
        # 保存回测数据
        self.bt_data.append(bt_data)

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
            msg = f"获取 {symbol} {interval} {window} {size} bararray 失败, 请检查数据是否足够'\n need_bars_count: {need_bars_count}, 实际获取数量: {len(sym_newest_bars)}\nself._store_bars_1m前10条数据: {list(self._store_bars_1m.items())[:10]}"
            self.main_engine.write_log(msg=msg, level=LogLevel.ERROR.value, source=self.gateway_name)
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

        

