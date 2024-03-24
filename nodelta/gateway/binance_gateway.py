'''
    Binance Gateway
'''
import time
import json
from typing import Any, Dict, List, Tuple
import threading
from datetime import datetime, timedelta, timezone

from .gateway import BaseGateway
from .SDK.binance_sdk.binance.um_futures import UMFutures
from .SDK.binance_sdk.binance.websocket.um_futures.websocket_client import UMFuturesWebsocketClient
from ..trader.engine import MainEngine
from ..trader.constant import (
    LogLevel, Direction, Offset, Status, Exchange, Product, GatewayName, EventType, Interval
)
from ..trader.object import ContractData, OrderData, TradeData, PositionData, AssetData, AccountData, DepthData, BarData, Event

# 订单状态映射
STATUS_BINANCES2VT: Dict[str, Status] = {
    "NEW": Status.NOTTRADED,
    "PARTIALLY_FILLED": Status.PARTTRADED,
    "FILLED": Status.ALLTRADED,
    "CANCELED": Status.CANCELLED,
    "REJECTED": Status.REJECTED,
    "EXPIRED": Status.CANCELLED
}

# 买卖方向映射
DIRECTION_VT2BINANCES: Dict[Direction, str] = {
    Direction.LONG: "BUY",
    Direction.SHORT: "SELL"
}
DIRECTION_BINANCES2VT: Dict[str, Direction] = {v: k for k, v in DIRECTION_VT2BINANCES.items()}


class BinanceUmGateway(BaseGateway):
    '''
        BinanceUmGateway 币安U本位合约网关
    '''

    def __init__(self, api: dict): # key='', secret='',
        # === Gateway 基础信息 ===
        self.gateway_name: str = GatewayName.BINANCE_UM.value # Gateway名称不需要改
        self.exchange = Exchange.BINANCE # 交易所名称不需要改
        super().__init__(api['key'], api['secret'], self.exchange)

        self.key, self.secret = api['key'], api['secret'] # 交易所key secret
        
        # 计时
        self.ts_last_depth: int = 0 # 上一次获取深度时的时间戳
        self.ts_last_bar: int = 0 # 上一次获取K线时的时间戳
        self.ts_last_subcribe: int = 0 # 上一次订阅深度的时间戳
        
        # == 实例化 clint ===
        self.init_client()

        # === 初始化工作 此前必须 init clint ===
        self.on_init() # 获取价格精度等信息

        # === 启动 Websocket 监控线程 ===
        self.thread_ws_monitor = threading.Thread(target=self.ws_monitor)

    def init_client(self):
        # === http ws client ===
        if self.key and self.secret:
            self.http_client = UMFutures(key=self.key, secret=self.secret) # HTTP Client
        else:
            self.http_client = UMFutures()
        self.ws_client = UMFuturesWebsocketClient(on_message=self.on_message) # Websocket Client
    
    def on_init(self):
        '''
            1. query_contracts() 获取合约信息
            2. query_position_mode() 获取持仓模式
        '''
        # 1. 获取合约信息
        self.query_contracts()
        # 2. 获取单边 双边持仓模式
        self.is_dualSidePosition = self.http_client.get_position_mode()['dualSidePosition']

    def query_contracts(self):
        '''
            获取合约信息
            定义全局变量 self.symbol_contract_map [nd_symbol: ContractData]
        '''
        # 1. 获取合约信息
        exchange_infos = self.http_client.exchange_info()
        # 2. 解析合约信息
        self.symbol_contract_map: Dict[str, ContractData] = {} # nd_symbol: ContractData

        for symbol_info in exchange_infos['symbols']:
            # 2.1 解析合约信息
            # --- 解析合约类型 ---
            if symbol_info['contractType'] == 'PERPETUAL':
                product_ = Product.SWAP # 永续合约
            elif symbol_info['contractType'] == 'CURRENT_QUARTER':
                product_ = Product.FUTURES # 当季交割合约
            elif symbol_info['contractType'] == 'NEXT_QUARTER':
                product_ = Product.FUTURES
            else:
                continue
            # --- 解析到期时间 ---
            if product_ != Product.SWAP:
                delivery_ts = int(symbol_info['deliveryDate'])
                delivery_str = self.ts_to_utc(delivery_ts)
                year, month, day = delivery_str[:10].split('-')
                delivery_date = f"{year[-2:]}{month}{day}"
            else: # 永续合约
                delivery_ts = -1
                delivery_date = ''
                
            # --- 解析合约信息 ---  
            contract = ContractData(
                gw_symbol = symbol_info['symbol'], # 交易所symbol
                exchange = self.exchange, # 交易所
                gateway_name = self.gateway_name, # Gateway名称
                product = product_, # 合约类型
                base = symbol_info['baseAsset'].upper(), # 基础币种
                quote = symbol_info['quoteAsset'].upper(), # 计价币种
                size=self.extract_number_from_string(symbol_info['baseAsset']), # 合约面值

                price_precision = int(symbol_info['pricePrecision']), # 价格精度
                qty_precision = int(symbol_info['quantityPrecision']), # 数量精度
                tick_size = float(symbol_info['filters'][0]['tickSize']), # 最小价格变动
                step_size = float(symbol_info['filters'][1]['stepSize']), # 最小数量变动
                min_qty = float(symbol_info['filters'][2]['minQty']), # 最小下单数量
                min_notional = float(symbol_info['filters'][5]['notional']), # 最小下单金额

                delivery_ts = delivery_ts, # 交割日期
                delivery_date = delivery_date # 交割日期
            )
            # 保存合约信息
            self.symbol_contract_map[contract.symbol] = contract


        # 3. gw_symbol -> nd_symbol 映射字典
        self.gw2nd_symbol_map = {v.gw_symbol: k for k, v in self.symbol_contract_map.items()}
        self.gw_total_symbols = tuple(self.gw2nd_symbol_map.keys()) # gw_symbol tuple

        # 4. nd_symbol -> gw_symbol 映射字典
        self.nd2gw_symbol_map = {k: v.gw_symbol for k, v in self.symbol_contract_map.items()}

    def extract_number_from_string(self, s: str) -> int:
        '''
            从baseAsset中提取合约面值 例如 1000LUNC -> 1000 BTC -> 1
            Parameters:
                s: baseAsset
            Return:
                合约面值
        '''
        num_str = ""
        for c in s:
            if c.isdigit():
                num_str += c
            else:
                break
        if num_str:
            return int(num_str)
        else:
            return 1
        
    def switch_gw_symbol(self, symbol: str) -> str:
        '''
            将nd_symbol转化为交易所symbol
            Parameters:
                symbol: nd_symbol
            Return:
                交易所 symbol
        '''
        return self.nd2gw_symbol_map.get(symbol, symbol)
    
    def switch_nd_symbol(self, symbol: str) -> str:
        '''
            将交易所symbol转化为规范symbol
            Parameters:
                symbol: 交易所 symbol
            Return:
                nd 规范 symbol
        '''
        return self.gw2nd_symbol_map.get(symbol, symbol)
    
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
        try:
            # 转化为交易所symbol
            gw_symbol = self.switch_gw_symbol(symbol)

            # 检查币对是否存在
            if gw_symbol not in self.gw_total_symbols:
                msg = f"send_order error: {symbol} not find in exchange_symbols"
                self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
                return ""
            # 下单
            side_ = DIRECTION_VT2BINANCES[direction]
            if self.is_dualSidePosition == False: # 单向持仓
                positionSide_ = 'BOTH'
            else:   # 双向持仓
                if (direction == Direction.LONG and offset == Offset.OPEN) or\
                (direction == Direction.SHORT and offset == Offset.CLOSE):
                    positionSide_ = 'LONG'
                elif (direction == Direction.LONG and offset == Offset.CLOSE) or\
                (direction == Direction.SHORT and offset == Offset.OPEN):
                    positionSide_ = 'SHORT'
            type_ = 'LIMIT'
            timeInForce_ = kwargs.get('timeInForce', 'GTC')
            quantity_precision = self.symbol_contract_map[symbol].qty_precision
            quantity_ = self._float_precision(amount, quantity_precision)
            price_precision = self.symbol_contract_map[symbol].price_precision
            price_ = self._float_precision(price, price_precision)
            kwargs_ = {k: v for k, v in kwargs.items() if k not in ['symbol', 'side', 'positionSide', 'timeInForce', 'type', 'quantity', 'price']}

            res = self.http_client.new_order(symbol=gw_symbol, side=side_, positionSide=positionSide_, timeInForce=timeInForce_,\
                                        type=type_, quantity=quantity_, price=price_, **kwargs_)
            if res['orderId']:  # 只有成功下单才会返回orderId 回调 on_order
                # order_data = OrderData(
                #     symbol=symbol,
                #     exchange=self.exchange,
                #     orderid=str(res['orderId']),
                #     direction=direction,
                #     offset=offset,
                #     price=price,
                #     volume=amount,
                #     traded=0,
                #     status=Status.SUBMITTING,
                #     ts= res['updateTime']
                # )
                # self.main_engine.put_event(event_type=EventType.ORDER, exchange=self.exchange, gateway_name=self.gateway_name, symbol=symbol, data=order_data)
                return str(res['orderId'])
            else:
                return ''
        except Exception as e:
            msg = f"send_order error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            return ''
    
    def cancel_order(self, symbol: str, orderid: str) -> bool:
        '''
            取消交易所订单
            Parameters:
                symbol: nd_symbol 币对
                orderid: 订单号
            Return:
                True: 取消成功
                False: 取消失败 或已经取消
        '''
        gw_symbol = self.switch_gw_symbol(symbol)
        try:
            res = self.http_client.cancel_order(symbol=gw_symbol, orderId=int(orderid))
            if res and res['status'] == 'CANCELED':
                return True
            else:
                return False
        except Exception as e:
            msg = f"cancel_order error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            return False

    def query_order(self, symbol: str, orderid: str) -> OrderData or None:
        '''
            查询订单
            Parameters:
                symbol: nd_symbol 币对
                orderid: 订单号
            Return:
                OrderData: 订单信息
                None: 订单不存在 或查询失败
        '''
        gw_symbol = self.switch_gw_symbol(symbol)
        try:
            res = self.http_client.query_order(symbol=gw_symbol, orderId=int(orderid))
            if res:
                # 根据 positionSide 与 side 确认 offset
                if res['positionSide'] == 'BOTH': # 单项持仓模式
                    offset = Offset.BOTH
                else: # 双向持仓模式
                    if res['side'] == 'BUY':
                        offset = Offset.OPEN if res['positionSide'] == 'LONG' else Offset.CLOSE
                    else: # SELL
                        offset = Offset.OPEN if res['positionSide'] == 'SHORT' else Offset.CLOSE
                order_data = OrderData(
                    symbol=symbol,
                    exchange=self.exchange,
                    orderid=str(res['orderId']),
                    direction=DIRECTION_BINANCES2VT[res['side']],
                    offset=offset,
                    price=float(res['price']),
                    volume=float(res['origQty']),
                    traded=float(res['executedQty']),
                    status=STATUS_BINANCES2VT[res['status']],
                    ts=res['updateTime']
                )
                return order_data
            else:
                return None
        except Exception as e:
            msg = f"query_order error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            return None
    
    def query_active_orders(self, symbol: str) -> List[OrderData] or List:
        '''
            查询所有活跃订单
            Parameters:
                symbol: nd_symbol 币对
            Return:
                List[OrderData]: 订单信息列表
                List: 订单不存在 或查询失败
        '''
        gw_symbol = self.switch_gw_symbol(symbol)
        try:
            res = self.http_client.get_orders(symbol=gw_symbol)
            if res:
                orders_data = []
                for order in res:
                    # 根据 positionSide 与 side 确认 offset
                    if order['positionSide'] == 'BOTH': # 单项持仓模式
                        offset = Offset.BOTH
                    else: # 双向持仓模式
                        if order['side'] == 'BUY':
                            offset = Offset.OPEN if order['positionSide'] == 'LONG' else Offset.CLOSE
                        else: # SELL
                            offset = Offset.OPEN if order['positionSide'] == 'SHORT' else Offset.CLOSE
                    order_data = OrderData(
                        symbol=symbol,
                        exchange=self.exchange,
                        orderid=str(order['orderId']),
                        direction=DIRECTION_BINANCES2VT[order['side']],
                        offset=offset,
                        price=float(order['price']),
                        volume=float(order['origQty']),
                        traded=float(order['executedQty']),
                        status=STATUS_BINANCES2VT[order['status']],
                        ts=order['updateTime']
                    )
                    orders_data.append(order_data)
                return orders_data
            else:
                return []
        except Exception as e:
            msg = f"query_active_orders error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            return []

    def query_account(self) -> AccountData or None:
        '''
            查询账户信息 返回 AccountData
        '''
        try:
            res = self.http_client.account()
            if res:
                # 账户资产情况
                assets_list = res['assets'] # 交易所返回的资产信息
                account_assets_dict = {} # AccountData 数据
                for data_ in assets_list:
                    if float(data_['walletBalance']) == 0:
                        continue
                    assets_data = AssetData(
                        name=data_['asset'].upper(),
                        total=float(data_['walletBalance']),
                        available=float(data_['availableBalance'])
                    )
                    account_assets_dict[assets_data.name] = assets_data
                # 账户持仓情况
                positions_list = res['positions'] # 交易所返回的持仓信息
                account_positions_dict = {} # AccountData 数据
                for data_ in positions_list:
                    if float(data_['positionAmt']) == 0:
                        continue
                    symbol = self.switch_nd_symbol(data_['symbol'])
                    if symbol not in account_positions_dict.keys(): # 尚未记录持仓
                        if data_['positionSide'] == 'BOTH':
                            netQty = float(data_['positionAmt'])
                        else:
                            netQty = float(data_['positionAmt']) if data_['positionSide'] == 'LONG' else - abs(float(data_['positionAmt']))
                        pos_data = PositionData(
                            symbol=symbol,
                            netQty=netQty,
                            avgPrice=float(data_['entryPrice']),
                        )
                        account_positions_dict[symbol] = pos_data
                    else: # 一般是双向持仓模式 会分别出现 LONG SHORT 两个持仓
                        this_qty = float(data_['positionAmt']) if ((data_['positionSide'] == 'LONG') or (data_['positionSide'] == 'BOTH')) else - abs(float(data_['positionAmt']))
                        this_cost = -1 * float(data_['entryPrice']) * this_qty
                        pos_cost = -1 * account_positions_dict[symbol].avgPrice * account_positions_dict[symbol].netQty
                        new_qty = account_positions_dict[symbol].netQty + this_qty
                        new_cost = pos_cost + this_cost
                        new_avg_price = abs(new_cost / new_qty)
                        account_positions_dict[symbol].netQty = new_qty
                        account_positions_dict[symbol].avgPrice = new_avg_price
                # 账户信息
                account_data = AccountData(
                    exchange=self.exchange,
                    gateway_name=self.gateway_name,
                    assets=account_assets_dict,
                    positions=account_positions_dict
                )
                return account_data
            else:
                return None
        except Exception as e:
            msg = f"query_account error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            return None

    def query_position(self, symbol: str) -> PositionData or None:
        ''''
            查询单个持仓信息 返回 PositionData
        '''
        gw_symbol = self.switch_gw_symbol(symbol)
        try:
            res = self.http_client.get_position_risk(symbol=gw_symbol)
            if res:
                netQty = 0
                cost = 0
                for data_ in res:
                    this_qty = float(data_['positionAmt']) if ((data_['positionSide'] == 'LONG') or (data_['positionSide'] == 'BOTH')) else - abs(float(data_['positionAmt']))
                    this_cost = -1 * float(data_['entryPrice']) * this_qty
                    netQty += this_qty
                    cost += this_cost
                pos_data = PositionData(
                    symbol=symbol,
                    netQty=netQty,
                    avgPrice=abs(cost / netQty) if netQty != 0 else None,
                )
                return pos_data
            else:
                return None
        except Exception as e:
            msg = f"query_position error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            return None
        
    # === websocket 推送回调函数 ===
    def on_message(self, _, message):
        try:
            data =json.loads(message)

            if not data.get('e'):  # 首次链接某个频道，会返回 {"id":1689143922513,"result":null}
                msg = f"WebSocket message: {data}"
                self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
            elif data['e'] == 'depthUpdate':
                self.ts_last_depth = self.get_ts() # 更新最新的depth时间戳
                self.on_depth_callback(data)
            elif data['e'] =='kline':
                self.ts_last_bar = self.get_ts()
                self.on_bar_callback(data)
            elif data['e'] == 'ORDER_TRADE_UPDATE':
                # 当有新订单创建、订单有新成交或者新的状态变化时会推送此类事件 事件类型统一为 ORDER_TRADE_UPDATE
                self.on_order_trade_callback(data)
            elif data['e'] == 'listenKeyExpired':
                # listenKey 有效期为 60 分钟，当出现 listenKeyExpired 时需要重新创建 listenKey
                self.on_listenKeyExpired_callback()
        except Exception as e:
            msg = f"on_message error: {e}; message: {message}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)

    def on_depth_callback(self, data):
        '''
            data 需要是字典
        '''
        bids = data['b']
        sorted_bids = sorted(bids, key=lambda x: float(x[0]), reverse=True)
        bids_tuple = tuple((float(price), float(amount)) for price, amount in sorted_bids)
        asks = data['a']
        sorted_asks = sorted(asks, key=lambda x: float(x[0]))
        asks_tuple = tuple((float(price), float(amount)) for price, amount in sorted_asks)

        symbol = self.switch_nd_symbol(data['s'])

        depth_data = DepthData(
            symbol=symbol,
            exchange=self.exchange,
            ts=data['E'],
            asks=asks_tuple,
            bids=bids_tuple
        )
        self.main_engine.put_event(event_type=EventType.DEPTH, exchange=self.exchange, gateway_name=self.gateway_name, symbol=symbol, data=depth_data)
    
    def on_bar_callback(self, data):
        '''
            data 需要是字典; EventType.BAR 永远仅推送已经走完的K线
            {
            "e": "kline",     // 事件类型
            "E": 123456789,   // 事件时间
            "s": "BNBUSDT",    // 交易对
            "k": {
                "t": 123400000, // 这根K线的起始时间
                "T": 123460000, // 这根K线的结束时间
                "s": "BNBUSDT",  // 交易对
                "i": "1m",      // K线间隔
                "f": 100,       // 这根K线期间第一笔成交ID
                "L": 200,       // 这根K线期间末一笔成交ID
                "o": "0.0010",  // 这根K线期间第一笔成交价
                "c": "0.0020",  // 这根K线期间末一笔成交价
                "h": "0.0025",  // 这根K线期间最高成交价
                "l": "0.0015",  // 这根K线期间最低成交价
                "v": "1000",    // 这根K线期间成交量
                "n": 100,       // 这根K线期间成交笔数
                "x": false,     // 这根K线是否完结(是否已经开始下一根K线)
                "q": "1.0000",  // 这根K线期间成交额
                "V": "500",     // 主动买入的成交量
                "Q": "0.500",   // 主动买入的成交额
                "B": "123456"   // 忽略此参数
            }
            }
        '''
        is_bar_closed = data['k']['x'] # 是否是已经走完的K线
        if not is_bar_closed:
            return
        symbol = self.switch_nd_symbol(data['s'])
        bar_data = BarData(
            symbol=symbol,
            exchange=self.exchange,
            gateway_name=self.gateway_name,
            datetime=datetime.fromtimestamp(int(data['k']['t'])/1000, tz=timezone(timedelta(hours=8))),
            open_ts=int(data['k']['t']),
            interval=Interval.MINUTE,
            volume=float(data['k']['v']),
            turnover=float(data['k']['q']),
            open_price=float(data['k']['o']),
            high_price=float(data['k']['h']),
            low_price=float(data['k']['l']),
            close_price=float(data['k']['c']),
        )
        self.main_engine.put_event(event_type=EventType.BAR, exchange=self.exchange, gateway_name=self.gateway_name, symbol=symbol, data=bar_data)

    def on_order_trade_callback(self, data):
        '''
            成交回调函数 data 需要是字典
            数据中的net
        '''
        order_dict = data['o']
        offset = Offset.CLOSE if order_dict['R'] == True else Offset.OPEN # 只减仓订单会判断为 close 其他为开仓订单
        
        symbol = self.switch_nd_symbol(order_dict['s'])
        order : OrderData = OrderData(
            symbol = symbol,
            exchange = self.exchange,
            orderid = str(order_dict['i']),
            direction = DIRECTION_BINANCES2VT[order_dict['S']], # Direction.LONG if order_dict['S'] == 'BUY' else Direction.SHORT,
            offset = offset,
            price = float(order_dict["p"]),
            volume = float(order_dict["q"]),
            traded = float(order_dict["z"]),
            status = STATUS_BINANCES2VT[order_dict["X"]],
            ts = int(order_dict["T"])
        )
        self.main_engine.put_event(event_type=EventType.ORDER, exchange=self.exchange, gateway_name=self.gateway_name, symbol=symbol, data=order)

        # === 成交事件推送
        trade_volume : float = float(order_dict["l"])
        if not trade_volume:
            return
        trade : TradeData = TradeData(
            symbol = symbol,
            exchange = self.exchange,
            orderid = str(order_dict['i']),
            tradeid = str(order_dict['t']),
            direction = DIRECTION_BINANCES2VT[order_dict['S']],
            offset = offset,
            price = float(order_dict["L"]),
            volume = trade_volume,
            ts = int(order_dict["T"])
        )
        self.main_engine.put_event(event_type=EventType.TRADE, exchange=self.exchange, gateway_name=self.gateway_name, symbol=symbol, data=trade)

    def on_listenKeyExpired_callback(self):
        '''
            listenKey 有效期为 60 分钟，当出现 listenKeyExpired 时重新链接 ws
        '''
        self._reconnect()

    def subscribe_data(self, gw_symbols: list):
        '''
            订阅成交 深度行情
            一定要先订阅账户数据再订阅行情数据

            Parameters:
                gw_symbols: 交易所币对
        '''

        # 1. 订阅成交
        self.listenKey = self.http_client.new_listen_key().get('listenKey', '')
        self.ws_client.user_data(listen_key=self.listenKey)
        self.main_engine.write_log(f"subscribe trade", level=LogLevel.INFO.value, source=self.gateway_name)

        # 2. 订阅深度
        if EventType.DEPTH.value in self.main_engine.strategy.topic[self.gateway_name].keys():
            depth_params = self.main_engine.strategy.topic[self.gateway_name][EventType.DEPTH.value]
            level = depth_params.get('level', 20)
            speed = depth_params.get('speed', 100)
            _count = 0
            for symbol in gw_symbols:
                self.ws_client.partial_book_depth(
                    symbol=symbol.lower(),
                    level=level,
                    speed=speed,
                )
                _count += 1
                if _count % 5 == 0 and _count != 0: # 每订阅5个币对休眠1s
                    time.sleep(1)
                self.main_engine.write_log(f"subscribe {symbol} depth", level=LogLevel.INFO.value, source=self.gateway_name)

        # 3. 订阅K线
        if EventType.BAR.value in self.main_engine.strategy.topic[self.gateway_name].keys():
            bar_params = self.main_engine.strategy.topic[self.gateway_name][EventType.BAR.value]
            interval = '1m'
            _count = 0
            for symbol in gw_symbols:
                self.ws_client.kline(
                    symbol=symbol.lower(),
                    interval=interval,
                )
                _count += 1
                if _count % 5 == 0 and _count != 0:
                    time.sleep(1)
                    
        self.ts_last_subcribe = self.get_ts() # 记录最后一次订阅时间

    def connect(self, symbols: list):
        '''
            用于 main_engine 中的 connect 理论上是只会调用一次 是gateway的入口函数
            会记录下所要求的数据
            Parameters:
                symbols: nd币对
        '''
        self.subscribed_nd_symbols = symbols.copy()  # 被订阅的nd币对
        self.subscribed_gw_symbols = [self.switch_gw_symbol(symbol) for symbol in symbols] # 被订阅的gw币对

        try:
            self.subscribe_data(self.subscribed_gw_symbols)
            msg = f"connect success"
            self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
            
            self.thread_ws_monitor.start() # 启动 ws 监控线程

        except Exception as e:
            msg = f"subscribe_data error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)

    def _reconnect(self):
        '''
            取消所有订阅 重新连接 websocket
        '''

        try:
            self.ws_client.stop()
            msg = f"_reconnect WebSocket 服务正在重启"
            self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)

        except Exception as e:
            msg = f"_reconnect stop error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
        
        try:
            self.init_client()
            self.subscribe_data(self.subscribed_gw_symbols)
            msg = f"reconnect success"
            self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
       
        except Exception as e:
            msg = f"_reconnect error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
        
    def get_ts(self):
        '''
            获取13位时间戳
        '''
        return int(time.time() * 1000)
    
    def ws_monitor(self):
        '''
            监控 websocket 连接状态 检测到断链接自动重新链接
        '''
        msg = f"{self.gateway_name} Websocket 连接监控已启动 正在监控... 行情3秒未更新重连"
        self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
        
        while True:
            try:
                # 每隔5秒检测一次
                time.sleep(5)

                # ==== 如果订阅深度 重连条件判断 1. 深度数据超过3秒未更新
                if EventType.DEPTH.value in self.main_engine.strategy.topic[self.gateway_name].keys():
                    now_ts = self.get_ts()
                    is_reconnect_1 = True if self.ts_last_depth and now_ts - self.ts_last_depth > self.reconnect_seconds_after_lost_depth * 1000 else False # 深度数据超过3秒未更新
                    
                    # 重连
                    if is_reconnect_1:
                        reason = f"行情{self.reconnect_seconds_after_lost_depth}秒未更新"
                        msg = f"ws_monitor: websocket {reason} 【重连中】..."
                        self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
                        self._reconnect()
                        msg = f"ws_monitor: websocket 【已重连】"
                        self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
                
                # ==== 如果订阅K线 重连条件判断 1. K线数据超过63秒未更新
                if EventType.BAR.value in self.main_engine.strategy.topic[self.gateway_name].keys():
                    now_ts = self.get_ts()
                    is_reconnect_2 = True if self.ts_last_bar !=0 and now_ts - self.ts_last_bar > 63 * 1000 else False

                    # 重连
                    if is_reconnect_2:
                        reason = f"K线63秒未更新"
                        msg = f"ws_monitor: websocket {reason} 【重连中】..."
                        self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
                        self._reconnect()
                        msg = f"ws_monitor: websocket 【已重连】"
                        self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)

            except Exception as e:
                msg = f"ws_monitor error: {e}"
                self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
    
    # 将毫秒时间戳转换为UTC时间
    def ts_to_utc(self, ts) -> str:
        '''
            将毫秒时间戳转换为UTC时间 str '2023-09-29 08:00:00'
        '''
        return datetime.utcfromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
    
    def _float_precision(self, num: float, precision: int) -> int or float:
        '''
        根据指定的小数点位数保留小数
        '''
        if precision == 0:
            return int(num)
        else:
            return round(num, precision)

if __name__ == "__main__":
    pass