from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Tuple
import logging

from .constant import (
    LogLevel, Direction, Offset, Status, Exchange, Product, GatewayName, EventType, Interval
)

@dataclass
class ContractData:
    """
    Contract data contains basic information about each contract traded.
    
    note:
        计算最小下单base数: size * min_qty
    """
    gw_symbol: str # 交易所合约代码
    exchange: Exchange
    gateway_name: str # 网关名称
    product: Product
    base: str = "" # 基础币种
    quote: str = "" # 计价币种
    size: float = 0 # 合约面值

    price_precision: int = 0 # 价格小数点位数
    qty_precision: int = 0 # 数量小数点位数
    tick_size: float = 0 # 最小价格变动单位
    step_size: float = 0 # 最小数量变动单位
    min_qty: float = 0 # 最小下单数量
    min_notional: float = 0 # 最小下单金额

    delivery_ts: int = 0 # 交割时间戳 永续为-1
    delivery_date: str = "" # 交割日期 230929 永续为 ""

    @property
    def symbol(self):
        if self.product == Product.SPOT:
            return f"{self.base.upper()}-{self.quote.upper()}-SPOT"
        elif self.product == Product.MARGIN:
            return f"{self.base.upper()}-{self.quote.upper()}-MARGIN"
        elif self.product == Product.SWAP: # 永续合约 BTC-USDT-SWAP
            return f"{self.base.upper()}-{self.quote.upper()}-SWAP"
        elif self.product == Product.FUTURES: # 交割合约 BTC-USD-FUTURES-210625
            return f"{self.base.upper()}-{self.quote.upper()}-FUTURES-{self.delivery_date}"
        elif self.product == Product.OPTION:
            return f"{self.base.upper()}-{self.quote.upper()}-OPTION-{self.delivery_date}" # TODO 期权合约应有行权价&期权类型 BTC-USD-OPTION-210625-40000-C
        else:
            return ""

    @property
    def min_base_trade(self):
        '''
            计算最小下单base数
        '''
        return self.size * self.min_qty

    @property
    def min_base_trade_precision(self):
        '''
            计算最小下单base数精度
        '''
        if '.' not in str(self.min_base_trade):
            return 0
        else:
            return len(str(self.min_base_trade).split('.')[1])
        
@dataclass
class OrderData:
    """
    Order data contains information for tracking lastest status
    of a specific order.
    """

    symbol: str
    exchange: Exchange
    orderid: str

    direction: Direction
    offset: Offset
    price: float or None
    volume: float = 0  # 订单量 正数
    traded: float = 0  # 已成交量 正数
    status: Status = Status.SUBMITTING
    ts : int = 0

@dataclass
class TradeData:
    """
    Trade data contains information for tracking trades of a specific order.
    """

    symbol: str
    exchange: Exchange
    orderid: str
    tradeid: str

    direction: Direction
    offset: Offset
    price: float = 0
    volume: float = 0 # 此次成交量 正数
    ts : int = 0

@dataclass
class PositionData:
    '''
        仓位数据 
        symbol: 合约代码 nd_symbol
        netQty: 净持仓 带方向

    '''
    symbol: str
    netQty: float # 净持仓
    avgPrice: float or None # 开仓均价

@dataclass
class AssetData:
    '''
        资产数据
        name: 资产名称
        total: 资产余额 可用余额 + 冻结余额
        available: 可用余额
    '''
    name: str # 资产名称 BTC USDT 均需大写
    total: float # 资产余额
    available: float # 可用余额


@dataclass
class AccountData:
    '''
        账户与持仓信息
        assets : {
            AssetData.name : AssetData
        }
        positions : {
            PositionData.symbol : PositionData
        }
    '''
    exchange: Exchange
    gateway_name : str
    assets: Dict[str, AssetData]
    positions: Dict[str, PositionData]


@dataclass
class DepthData:
    """
    交易所order book数据
    asks : 卖单 价格从低到高 (price, volume)
    bids : 买单 价格从高到低 (price, volume)
    """

    symbol: str
    exchange: Exchange
    ts : int = 0 # 13位时间戳

    asks: tuple = (())
    bids: tuple = (())

    @property
    def best_ask(self) -> Tuple[float, float] or None:
        if self.asks:
            return self.asks[0]
        else:
            return None
    
    @property
    def best_bid(self) -> Tuple[float, float] or None:
        if self.bids:
            return self.bids[0]
        else:
            return None

@dataclass
class BarData:
    """
    Candlestick bar data of a certain trading period.
    """

    symbol: str # nd_symbol
    exchange: Exchange
    gateway_name : str
    datetime: datetime # utc + 8
    open_ts: int = 0 # 开盘时间戳13位

    interval: Interval = None
    volume: float = 0
    turnover: float = 0
    open_price: float = 0
    high_price: float = 0
    low_price: float = 0
    close_price: float = 0

@dataclass
class Event:
    
    event_type: EventType
    exchange: Exchange
    gateway_name : str
    symbol : str
    data : Any = None