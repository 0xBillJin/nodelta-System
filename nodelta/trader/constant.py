"""
General constant enums used in the trading platform.
"""

from enum import Enum
import logging

class LogLevel(Enum):
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL

class Direction(Enum):
    """
    Direction of order/trade/position.
    """
    LONG = "多"
    SHORT = "空"

class Offset(Enum):
    """
    Offset of order/trade.
    """
    OPEN = "开"
    CLOSE = "平"
    BOTH = "BOTH" # 不区分开平 一般是单向持仓可开可平

class Status(Enum):
    """
    Order status.
    """
    SUBMITTING = "提交中"
    NOTTRADED = "未成交"
    PARTTRADED = "部分成交"
    ALLTRADED = "全部成交"
    CANCELLED = "已撤销"
    REJECTED = "拒单"

class Exchange(Enum):

    BACKTEST_CTA = "BacktestCtaGateway"
    BINANCE = "BINANCE"
    BITFINEX = "BITFINEX"
    BITSTAMP = "BITSTAMP"
    BYBIT = "BYBIT"
    COINBASE = "COINBASE"
    DERIBIT = "DERIBIT"
    DYDX = "DYDX"
    FTX = "FTX"
    GATEIO = "GATEIO"
    HUOBI = "HUOBI"
    OKX = "OKX"

class Product(Enum):
    """
    Product class.
    """
    SPOT = "SPOT" # "币币"
    MARGIN = "MARGIN" # "币币杠杆"
    SWAP = "SWAP" # "永续合约"
    FUTURES = "FUTURES" # "交割合约"
    OPTION = "OPTION" # "期权"

class GatewayName(Enum):
    '''
        Gateway Name 同一家交易所有可能有多个 GateWay
        instType 类型
            SPOT 币币
            MARGIN 币币杠杆
            SWAP 永续合约
            FUTURES 交割合约
            OPTION 期权
    '''
    BACKTEST_CTA = "BacktestCtaGateway"
    BINANCE_UM = "BinanceUmGateway"
    OKX = "OkxV5Gateway"

class EventType(Enum):
    """
        事件类型
    """
    TIMER = "timer"
    ORDER = "order"
    TRADE = "trade"
    DEPTH = "depth"
    BAR = "bar"
    POSITION = "position"
    ACCOUNT = "account"
    BACKTESTEND = "backtestend"


class Interval(Enum):
    """
    Interval of bar data.
    """
    MINUTE = "1m"
    HOUR = "1h"
    DAILY = "d"
    WEEKLY = "w"
    TICK = "tick"