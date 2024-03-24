from abc import ABC, abstractmethod
from ..trader.constant import (
    LogLevel, Direction, Offset, Status, Exchange, Product, GatewayName, Interval, EventType
)
from ..trader.object import ContractData, OrderData, TradeData, PositionData, AssetData, AccountData, DepthData, Event


class BaseGateway(ABC):
    '''
        基础网关类 所有的网关都需要继承此类
    '''

    def __init__(self, key: str, secret: str, exchange: Exchange) -> None:
        '''
            实例化 clint 获取精度信息
        '''
        self.reconnect_seconds_after_lost_depth: int = 3


    def add_main_engine(self, main_engine): # : MainEngine
        self.main_engine = main_engine

    @abstractmethod
    def connect(self, symbols: list):
        '''
            务必先订阅账户数据 再订阅深度等行情数据
        '''
        pass

    @abstractmethod
    def on_init(self):
        '''
        连接成功后的回调 获取价格精度信息
        '''
        pass
    
    @abstractmethod
    def send_order(self, symbol: str, direction: Direction, offset: Offset, price: float, amount: float):
        '''
            报单成功后 需要回调 on_order SUBMITTING
        '''
        pass

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str):
        '''
            撤单成功后 需要回调 on_order CANCELLED
            成功返回True 失败返回False
        '''
        pass

    @abstractmethod
    def query_order(self, symbol: str, order_id: str):
        '''
            查询订单
            查询成功返回 OrderData
            查询失败返回 None
        '''
        pass

    @abstractmethod
    def query_active_orders(self, symbol: str):
        '''
            查询所有订单
            查询成功返回 list[OrderData]
            查询失败返回 []
        '''
        pass

    @abstractmethod
    def query_account(self):
        # 查询账户
        pass

    @abstractmethod
    def query_position(self, symbol: str):
        # 查询持仓
        pass
