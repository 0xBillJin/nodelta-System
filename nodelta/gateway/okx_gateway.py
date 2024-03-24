'''
    OKX V5 Gateway
'''
from re import T
import time
import json
from typing import Any, Dict, List, Tuple
import threading
from datetime import datetime, timedelta, timezone

from .gateway import BaseGateway
from ..trader.constant import (
    LogLevel, Direction, Offset, Status, Exchange, Product, GatewayName, EventType, Interval
)
from ..trader.object import ContractData, OrderData, TradeData, PositionData, AssetData, AccountData, DepthData, BarData, Event
from .SDK.okx_sdk.okx import PublicData, Account, Trade
from .SDK.okx_sdk.okx.websocket.WsPublic import WsPublic
from .SDK.okx_sdk.okx.websocket.WsPrivate import WsPrivate

# 产品类别映射
instType2product: Dict[str, Product] = {
    "SPOT": Product.SPOT,
    "MARGIN": Product.MARGIN,
    "SWAP": Product.SWAP,
    "FUTURES": Product.FUTURES,
    "OPTION": Product.OPTION
}

# 现价委托类型
LIMIT_ORDER_TYPES = ["limit", "post_only", "fok", "ioc"]
# 市价委托类型
MARKET_ORDER_TYPES = ["market", "fok", "ioc"]

# 交易所方向映射
DIRECTION_OKX2ND: Dict[str, Direction] = {
    "buy": Direction.LONG,
    "sell": Direction.SHORT
}

# 交易所状态映射
STATUS_OKX2ND: Dict[str, Status] = {
    "canceled": Status.CANCELLED,
    "live": Status.NOTTRADED,
    "partially_filled": Status.PARTTRADED,
    "filled": Status.ALLTRADED,
    "mmp_canceled": Status.CANCELLED
}


class OkxGateway(BaseGateway):
    '''
        OKX V5 Gateway
        需要手动将账户模式设置为以下三类保证金模式之一：
            '2'单币种保证金模式 '3'跨币种保证金模式 '4'组合保证金模式

        TODO: 
            1. 暂不支持杠杠、期权交易
    '''

    def __init__(self, api: dict) -> None:
        # === Gateway 基础信息 ===
        self.gateway_name : str = GatewayName.OKX.value
        self.exchange = Exchange.OKX
        super().__init__(api['key'], api['secret'], self.exchange)

        self.key, self.secret, self.passphrase = api['key'], api['secret'], api['passphrase']
        self.ws_public_url = "wss://wsaws.okx.com:8443/ws/v5/business"
        self.ws_private_url = "wss://wsaws.okx.com:8443/ws/v5/private"

        # 计时
        self.ts_last_depth: int = 0 # 上一次获取深度时的时间戳
        self.ts_last_bar: int = 0 # 上一次获取K线时的时间戳
        self.ts_last_subcribe: int = 0 # 上一次订阅深度的时间戳

        self.on_init()

    def on_init(self):

        # flag : live trading: 0, demo trading: 1
        self.publicDataClient = PublicData.PublicAPI(api_key=self.key, api_secret_key=self.secret, passphrase=self.passphrase, use_server_time=False, flag='0', debug=False)
        self.accountClient = Account.AccountAPI(api_key=self.key, api_secret_key=self.secret, passphrase=self.passphrase, use_server_time=False, flag='0', debug=False)
        self.tradeClient = Trade.TradeAPI(api_key=self.key, api_secret_key=self.secret, passphrase=self.passphrase, use_server_time=False, flag='0', debug=False)

        # === 初始化工作 此前必须 init rest clint ===
        self.on_rest_init() # 获取价格精度等信息  

    def init_ws_client(self):
        '''
            初始化 client
        '''
        # 根据 strategy 选择的 topic 确定 ws url
            # OK 公有频道： 
            # wss://ws.okx.com:8443/ws/v5/public : 无depth 无bar
            # wss://ws.okx.com:8443/ws/v5/business: 无depth 无bar
            # wss://wsaws.okx.com:8443/ws/v5/public : 有depth 无bar
            # wss://wsaws.okx.com:8443/ws/v5/business:无depth 有bar
        # 不允许同时订阅bar & depth
        if EventType.DEPTH.value in self.main_engine.strategy.topic[self.gateway_name].keys() and \
            EventType.BAR.value in self.main_engine.strategy.topic[self.gateway_name].keys():
            raise ValueError("不允许同时订阅深度和K线")
        if EventType.DEPTH.value in self.main_engine.strategy.topic[self.gateway_name].keys():
            self.ws_public_url = "wss://wsaws.okx.com:8443/ws/v5/public"
        elif EventType.BAR.value in self.main_engine.strategy.topic[self.gateway_name].keys():
            self.ws_public_url = "wss://wsaws.okx.com:8443/ws/v5/business"

        # WS client
        self.wsPublicClient = WsPublic(url=self.ws_public_url)
        self.WsPrivateClient = WsPrivate(apiKey=self.key, passphrase=self.passphrase, secretKey=self.secret, url=self.ws_private_url, useServerTime=False)

    def on_rest_init(self):
        '''
            1. query_contracts() 获取合约信息
        '''
        # 1. 获取合约信息
        self.query_contracts()
        # 2. 获取账户配置
        self.query_account_config()

    def query_contracts(self):
        '''
            获取合约信息
        '''
        # 1. 获取合约信息
        total_insts = []
        for instType_ in ["SPOT", "MARGIN", "SWAP", "FUTURES"]: # TODO: 期货合约信息获取
            insts = self.publicDataClient.get_instruments(instType=instType_)['data']
            total_insts.extend(insts)
            time.sleep(0.25)
        # 2. 解析合约信息
        self.symbol_contract_map: Dict[str, ContractData] = {} # nd_symbol: ContractData
        
        for inst in total_insts:

            if inst['expTime']:
                delivery_ts = int(inst['expTime'])
                delivery_str = self.ts_to_utc(delivery_ts)
                year, month, day = delivery_str[:10].split('-')
                delivery_date = f"{year[-2:]}{month}{day}"
            else:
                delivery_ts = -1
                delivery_date = ''

            # --- 解析合约信息 ---  
            contract = ContractData(
                gw_symbol = inst['instId'], # 交易所合约代码
                exchange = self.exchange, # 交易所代码
                gateway_name = self.gateway_name, # Gateway
                product = instType2product[inst['instType']], # 产品类型
                base = inst['instId'].split('-')[0].upper(), # 基础币种
                quote = inst['instId'].split('-')[1].upper(), # 计价币种
                size =  float(inst['ctVal']) if inst['ctVal'] else 1, # 适用于交割/永续/期权
                
                price_precision = self.count_precision_num(inst['tickSz']), # 价格精度
                qty_precision = self.count_precision_num(inst['minSz']), # 数量精度 下单数量精度，如 BTC-USDT-SWAP：1
                tick_size = float(inst['tickSz']), # 最小变动价位 如 0.0001
                step_size = float(inst['minSz']), # 最小数量变动
                min_qty = float(inst['minSz']), # 合约的数量单位是“张”，现货的数量单位是“交易货币”
                min_notional = 0, # 最小下单金额 作废

                delivery_ts = delivery_ts, # 交割时间戳
                delivery_date = delivery_date # 交割日期
            )
            # 保存合约信息
            self.symbol_contract_map[contract.symbol] = contract

            self.gw_total_symbols = tuple([c.gw_symbol for c in self.symbol_contract_map.values()])

            self.nd2gw_symbol_map = {contract.symbol: contract.gw_symbol for contract in self.symbol_contract_map.values()}
            self.gw2nd_symbol_map = {contract.gw_symbol: contract.symbol for contract in self.symbol_contract_map.values()}

    def query_account_config(self) -> None:
        '''
            查询账户配置
            'acctLv', 'autoLoan', 'ctIsoMode', 'greeksType', 'ip', 'kycLv', 
            'label', 'level', 'levelTmp', 'liquidationGear', 'mainUid', 
            'mgnIsoMode', 'opAuth', 'perm', 'posMode', 'roleType', 'spotOffsetType', 'traderInsts', 'uid'
        
            acctLv: 账户层级 '1'简单交易模式 '2'单币种保证金模式 '3'跨币种保证金模式 '4'组合保证金模式

        '''
        res = self.accountClient.get_account_config()
        if res['code'] == '0':
            self.account_config: dict = res['data'][0]
            self.acctLv: str = self.account_config['acctLv'] # '2'单币种保证金模式 '3'跨币种保证金模式 '4'组合保证金模式
            self.posMode: str = self.account_config['posMode'] # 仓位模式 'long_short_mode' 'net_mode'
            assert self.acctLv in ['2', '3', '4'], "仅支持 '2'单币种保证金模式 '3'跨币种保证金模式 '4'组合保证金模式"

    def switch_gw_symbol(self, symbol: str) -> str:
        '''
            将nd_symbol转化为交易所symbol
            Parameters:
                symbol: nd_symbol
            Return:
                交易所 symbol
        '''
        return self.nd2gw_symbol_map.get(symbol, symbol)
    
    def swich_nd_symbol(self, symbol: str) -> str:
        '''
            将交易所symbol转化为nd_symbol
            Parameters:
                symbol: 交易所symbol
            Return:
                nd_symbol
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

            Note
                1. 若kwargs无特殊指定: 杠杠类产品均为逐仓
                2. amount 为base下单数量 自动转换为传参数量

        '''
        try:
            # 转化为交易所symbol -- instId
            gw_symbol = self.switch_gw_symbol(symbol)

            # 检查币对是否存在
            if gw_symbol not in self.gw_total_symbols:
                msg = f"send_order error: {symbol} not find in exchange_symbols"
                self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
                return ""
            
            product: Product = self.symbol_contract_map[symbol].product # 产品类型
            
            if 'tdMode' not in kwargs: # --tdMode
                tdMode = self._get_defult_tdMode(product)
            else:
                tdMode = kwargs['tdMode']

            side = 'buy' if direction == Direction.LONG else 'sell' # --side
            
            if self.posMode == 'long_short_mode': # --posSide
                if (direction == Direction.LONG and offset == Offset.OPEN) or\
                (direction == Direction.SHORT and offset == Offset.CLOSE):
                    posSide = 'LONG'
                elif (direction == Direction.LONG and offset == Offset.CLOSE) or\
                (direction == Direction.SHORT and offset == Offset.OPEN):
                    posSide = 'SHORT'
            else:
                posSide =''

            ordType = kwargs.get('ordType', 'limit') # --ordType

            price_precision = self.symbol_contract_map[symbol].price_precision
            px = self._float_precision(price, price_precision) if ordType in LIMIT_ORDER_TYPES else '' # --px
            tgtCcy = '' # --tgtCcy
            # --sz
            if product in [Product.FUTURES, Product.SWAP, Product.OPTION]:
                contract_num = amount / self.symbol_contract_map[symbol].size
                sz = self._float_precision(contract_num, self.symbol_contract_map[symbol].qty_precision)
            elif product == Product.SPOT:
                if ordType in LIMIT_ORDER_TYPES:
                    sz = self._float_precision(amount, self.symbol_contract_map[symbol].qty_precision)
                elif ordType in MARKET_ORDER_TYPES:
                    tgtCcy = 'base_ccy'
                    sz = self._float_precision(amount, self.symbol_contract_map[symbol].qty_precision)
            elif product == Product.MARGIN:
                if ordType in LIMIT_ORDER_TYPES:
                    sz = self._float_precision(amount, self.symbol_contract_map[symbol].qty_precision)
                elif ordType in MARKET_ORDER_TYPES:
                    if direction == Direction.LONG:
                        sz = self._float_precision(amount * price, self.symbol_contract_map[symbol].qty_precision)
                    elif direction == Direction.SHORT:
                        sz = self._float_precision(amount, self.symbol_contract_map[symbol].qty_precision)
            
            # 去掉kwargs中的 instId tdMode side posSide ordType px sz tgtCcy
            kwargs_ = {k: v for k, v in kwargs.items() if k not in ['instId', 'tdMode', 'side', 'posSide', 'ordType', 'px', 'sz', 'tgtCcy']}
            
            # 下单
            # # --临时debug
            # msg = f"{'<' *24}]nsend_order debug:\ninstId@{gw_symbol} tdMode@{tdMode} side@{side} posSide@{posSide} ordType@{ordType} px@{px} sz@{sz} tgtCcy@{tgtCcy} kwargs@{kwargs_}"
            # self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)

            res = self.tradeClient.place_order(instId=gw_symbol, tdMode=tdMode, side=side, posSide=posSide, ordType=ordType, px=px, sz=sz, tgtCcy=tgtCcy, **kwargs_)
            
            # # --临时debug
            # msg = f"res@{res}\n{'>' *24}"
            # self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)

            if res['code'] != '0':
                msg = f"send_order error: {res}"
                self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
                return ""
            else:
                # order_data = OrderData(
                #     symbol=symbol,
                #     exchange=self.exchange,
                #     orderid=str(res['data'][0]['ordId']),
                #     direction=direction,
                #     offset=offset,
                #     price=price,
                #     volume=amount,
                #     traded=0,
                #     status=Status.SUBMITTING,
                #     ts=int(time.time() * 1000)
                # )
                # self.main_engine.put_event(event_type=EventType.ORDER, exchange=self.exchange, gateway_name=self.gateway_name, symbol=symbol, data=order_data)
                return res['data'][0]['ordId']

        except Exception as e:
            msg = f"send_order error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            return ""
    
    def cancel_order(self, symbol: str, orderid: str):
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
            res = self.tradeClient.cancel_order(instId=gw_symbol, ordId=orderid)
            if res['code'] == '0' and res['data'][0]['sCode'] == '0':
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
            res = self.tradeClient.get_order(instId=gw_symbol, ordId=orderid)
            if res['code'] == '0':
                
                order_posSide = res['data'][0]['posSide']
                if order_posSide == 'LONG':
                    offset = Offset.OPEN if res['data'][0]['side'] == 'buy' else Offset.CLOSE
                elif order_posSide == 'SHORT':
                    offset = Offset.CLOSE if res['data'][0]['side'] == 'buy' else Offset.OPEN
                else:
                    offset = Offset.OPEN if res['data'][0]['side'] == 'buy' else Offset.CLOSE

                order_data = OrderData(
                    symbol=symbol,
                    exchange=self.exchange,
                    orderid=str(res['data'][0]['ordId']),
                    direction= DIRECTION_OKX2ND[res['data'][0]['side']],
                    offset=offset,
                    price=float(res['data'][0]['px']) if res['data'][0]['px'] else None,
                    volume= self._sz2ndvolune(symbol, res['data'][0]['sz']),
                    traded= self._sz2ndvolune(symbol, res['data'][0]['accFillSz']),
                    status=STATUS_OKX2ND[res['data'][0]['state']],
                    ts=int(time.time() * 1000)
                )
                return order_data
            else:
                return None
        except Exception as e:
            msg = f"query_order error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            return None
        
    def query_active_orders(self, symbol: str) -> List[OrderData] or None:
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
            res = self.tradeClient.get_order_list(instId=gw_symbol)
            if res['code'] == '0':
                order_datas = []
                for order in res['data']:
                    order_posSide = order['posSide']
                    if order_posSide == 'LONG':
                        offset = Offset.OPEN if order['side'] == 'buy' else Offset.CLOSE
                    elif order_posSide == 'SHORT':
                        offset = Offset.CLOSE if order['side'] == 'buy' else Offset.OPEN
                    else:
                        offset = Offset.OPEN if order['side'] == 'buy' else Offset.CLOSE

                    order_data = OrderData(
                        symbol=symbol,
                        exchange=self.exchange,
                        orderid=str(order['ordId']),
                        direction= DIRECTION_OKX2ND[order['side']],
                        offset=offset,
                        price=float(order['px']) if order['px'] else None,
                        volume= self._sz2ndvolune(symbol, order['sz']),
                        traded= self._sz2ndvolune(symbol, order['accFillSz']),
                        status=STATUS_OKX2ND[order['state']],
                        ts=int(time.time() * 1000)
                    )
                    order_datas.append(order_data)
                return order_datas
            else:
                return []
        except Exception as e:
            msg = f"query_active_orders error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            return []
        
    def query_account(self) -> AccountData or None:
        '''
            查询账户信息
            Return:
                AccountData
                None: 查询失败
        '''
        try:
            # 1. 查资产
            res = self.accountClient.get_account_balance()
            if res['code'] == '0':
                assets = {}
                for ass_info in res['data'][0]['details']:
                    coin_ass = AssetData(
                        name = ass_info['ccy'].upper(),
                        total = float(ass_info['eq']),
                        available= float(ass_info['eq']) - float(ass_info['frozenBal']) # 可用作保证金的数量
                    )
                    assets[ass_info['ccy'].upper()] = coin_ass
            else:
                return None
            # 2. 查持仓
            res = self.accountClient.get_positions()
            if res['code'] == '0':
                positions = {}
                for instId in [x['instId'] for x in res['data']]:
                    instId_pos = [x for x in res['data'] if x['instId'] == instId]
                    gw_symbol = instId
                    nd_symbol = self.swich_nd_symbol(gw_symbol)
                    netQty = 0
                    cost = 0
                    for data_ in instId_pos:
                        # 计算正确的净持仓
                        data_pos = self._sz2ndvolune(nd_symbol, float(data_['pos']))
                        if not data_pos:
                            continue
                        if data_['posSide'] == 'net':
                            netQty += data_pos
                            _cost = -1 * data_pos * float(data_['avgPx']) if data_pos > 0 else (data_pos * float(data_['avgPx']))
                            cost += _cost
                        elif data_['posSide'] == 'long':
                            netQty += data_pos
                            _cost = -1 * data_pos * float(data_['avgPx'])
                            cost += _cost
                        elif data_['posSide'] == 'short':
                            netQty -= data_pos
                            _cost = data_pos * float(data_['avgPx'])
                            cost += _cost
                    pos_data = PositionData(
                        symbol=nd_symbol,
                        netQty=netQty,
                        avgPrice=abs(cost / netQty) if netQty else None,
                    )
                    positions[nd_symbol] = pos_data
            else:
                return None
            # 3. 返回
            account_data = AccountData(
                exchange=self.exchange,
                gateway_name=self.gateway_name,
                assets=assets,
                positions=positions
            )

            return account_data
        except Exception as e:
            msg = f"query_account error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            return None
        
    def query_position(self, symbol: str) -> PositionData or None:
        ''''
            查询单个持仓信息 返回 PositionData 永续、期货、期权、杠杠#TODO
            现货持仓量需要查询账户信息
        '''
        gw_symbol = self.switch_gw_symbol(symbol)
        product: Product = self.symbol_contract_map[symbol].product
        try:
            res = self.accountClient.get_positions(instId=gw_symbol)
            if res['code'] == '0':
                if res['data']:
                    netQty = 0
                    cost = 0
                    for data_ in res['data']:
                        data_pos = self._sz2ndvolune(symbol, float(data_['pos']))
                        if not data_pos:
                            continue
                        # 计算正确的净持仓
                        if data_['posSide'] == 'net':
                            netQty += data_pos
                            _cost = -1 * data_pos * float(data_['avgPx']) if data_pos > 0 else (data_pos * float(data_['avgPx']))
                            cost += _cost
                        elif data_['posSide'] == 'long':
                            netQty += data_pos
                            _cost = -1 * data_pos * float(data_['avgPx'])
                            cost += _cost
                        elif data_['posSide'] == 'short':
                            netQty -= data_pos
                            _cost = data_pos * float(data_['avgPx'])
                            cost += _cost
                    pos_data = PositionData(
                        symbol=symbol,
                        netQty=netQty,
                        avgPrice=abs(cost / netQty) if netQty else None,
                    )
                else:
                    pos_data = PositionData(
                        symbol=symbol,
                        netQty=0,
                        avgPrice=None,
                    )
                return pos_data
            else:
                return None
        except Exception as e:
            msg = f"query_position error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            return None
    
    # === websocket 推送回调函数 ===
    def on_message(self, message: dict):
        try:
            channel = message.get('arg', {'channel': ''})['channel']
            event = message.get('event', '')
            is_data = True if 'data' in message else False

            if channel == "books5" and is_data:
                self.ts_last_depth = self.get_ts() # 更新最新的depth时间戳
                self.on_depth_callback(message)
            elif channel == "orders" and is_data:
                self.on_order_trade_callback(message)
            elif channel == 'candle1m' and is_data and len(message['data']) > 0:
                self.ts_last_bar = self.get_ts()
                self.on_bar_callback(message)
            if event:
                msg = f"{event}: {message}"
                self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)

        except Exception as e:
            msg = f"on_message error{e}: {message}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)

    def on_depth_callback(self, message: dict):
        '''
            深度数据返回的是一个 dict
            {'arg': {'channel': 'books5', 'instId': 'ETH-USDT-SWAP'}, 
            'data': [{
            'asks': [['1608.32', '865', '0', '14'], ['1608.33', '11', '0', '2'], ['1608.35', '31', '0', '1'], ['1608.36', '46', '0', '1'], ['1608.4', '39', '0', '2']], 
            'bids': [['1608.31', '552', '0', '17'], ['1608.3', '7', '0', '2'], ['1608.29', '6', '0', '2'], ['1608.28', '14', '0', '1'], ['1608.27', '277', '0', '3']], 
            'instId': 'ETH-USDT-SWAP', 'ts': '1694422506102', 'seqId': 9653444247}
            ]}
        '''

        gw_swmbol = message.get('arg', {}).get('instId', '').upper()
        symbol = self.swich_nd_symbol(gw_swmbol)
        contract_ = self.symbol_contract_map.get(symbol, None)
        if not contract_:
            return
        size = contract_.size
        asks_list = message.get('data', [])[0].get('asks', None)
        bids_list = message.get('data', [])[0].get('bids', None)
        sorted_asks_list = sorted(asks_list, key=lambda x: float(x[0]))
        sorted_asks = tuple([(float(x[0]), float(x[1]) * size) for x in sorted_asks_list])
        sorted_bids_list = sorted(bids_list, key=lambda x: float(x[0]), reverse=True)
        sorted_bids = tuple([(float(x[0]), float(x[1]) * size) for x in sorted_bids_list])

        depth_data = DepthData(
            symbol=symbol,
            exchange=self.exchange,
            asks=sorted_asks,
            bids=sorted_bids,
            ts=int(time.time() * 1000)
        )
        self.main_engine.put_event(event_type=EventType.DEPTH, exchange=self.exchange, gateway_name=self.gateway_name, symbol=symbol, data=depth_data)

    def on_bar_callback(self, message: dict):
        '''
            data 需要是字典; EventType.BAR 永远仅推送已经走完的K线
            {
            "arg": {
                "channel": "candle1m",
                "instId": "BTC-USDT"
            },
            "data": [
                [
                "1629993600000",
                "42500",
                "48199.9",
                "41006.1",
                "41006.1",
                "3587.41204591",
                "166741046.22583129",
                "166741046.22583129",
                "0"
                ]
            ]
            }
        '''
        is_bar_closed = True if message['data'][0][8] == '1' else False
        if not is_bar_closed:
            return
        symbol = self.swich_nd_symbol(message['arg']['instId'].upper())
        bar_data = BarData(
            symbol=symbol,
            exchange=self.exchange,
            gateway_name=self.gateway_name,
            datetime=datetime.fromtimestamp(int(message['data'][0][0])/1000, tz=timezone(timedelta(hours=8))),
            open_ts=int(message['data'][0][0]),
            interval=Interval.MINUTE,
            volume=float(message['data'][0][6]),
            turnover=float(message['data'][0][7]),
            open_price=float(message['data'][0][1]),
            high_price=float(message['data'][0][2]),
            low_price=float(message['data'][0][3]),
            close_price=float(message['data'][0][4]),

        )
        self.main_engine.put_event(event_type=EventType.BAR, exchange=self.exchange, gateway_name=self.gateway_name, symbol=symbol, data=bar_data)


    def on_order_trade_callback(self, message: dict):
        '''

        '''
        order_dict = message['data'][0]
        offset = Offset.CLOSE if order_dict['reduceOnly'] == 'true' else Offset.OPEN

        gw_symbol = order_dict['instId'].upper()
        symbol = self.swich_nd_symbol(gw_symbol)
        volume = self._sz2ndvolune(symbol, order_dict['sz'])
        order : OrderData = OrderData(
            symbol = symbol,
            exchange = self.exchange,
            orderid = str(order_dict['ordId']),
            direction = DIRECTION_OKX2ND[order_dict['side']],
            offset = offset,
            price = float(order_dict['px']) if order_dict['px'] else None,
            volume = volume,
            traded = self._sz2ndvolune(symbol, order_dict['accFillSz']),
            status = STATUS_OKX2ND[order_dict['state']],
            ts = int(order_dict['uTime'])
        )
        self.main_engine.put_event(event_type=EventType.ORDER, exchange=self.exchange, gateway_name=self.gateway_name, symbol=symbol, data=order)

        # 成交推送
        if order_dict['accFillSz'] != '0':
            trade : TradeData = TradeData(
                symbol = symbol,
                exchange = self.exchange,
                orderid = str(order_dict['ordId']),
                tradeid = str(order_dict['tradeId']),
                direction = DIRECTION_OKX2ND[order_dict['side']],
                offset = offset,
                price = float(order_dict['fillPx']) if order_dict['fillPx'] else None,
                volume = self._sz2ndvolune(symbol, float(order_dict['fillSz'])) if float(order_dict['fillSz']) else None,
                ts = int(order_dict['uTime'])
            )
            self.main_engine.put_event(event_type=EventType.TRADE, exchange=self.exchange, gateway_name=self.gateway_name, symbol=symbol, data=trade)

    def subscribe_data(self, gw_symbols: list):
        '''
            订阅成交 深度行情
            一定要先订阅账户数据再订阅行情数据

            Parameters:
                gw_symbols: 交易所币对
        '''
        # 1. 订阅成交
        if self.WsPrivateClient.is_alive() is False:
            self.WsPrivateClient.start()
        self.order_trade_args = [{"channel": "orders", "instType": "ANY", "instId": _} for _ in gw_symbols]
        self.WsPrivateClient.subscribe(self.order_trade_args, self.on_message)
        time.sleep(0.5)

        # 2. 订阅深度
        if EventType.DEPTH.value in self.main_engine.strategy.topic[self.gateway_name].keys():
            if self.wsPublicClient.is_alive() is False:
                self.wsPublicClient.start()
            self.books5_args = [{"channel": "books5", "instId": _} for _ in gw_symbols]
            self.wsPublicClient.subscribe(self.books5_args, self.on_message)
            time.sleep(0.5)

        # 3. 订阅K线
        if EventType.BAR.value in self.main_engine.strategy.topic[self.gateway_name].keys():
            if self.wsPublicClient.is_alive() is False:
                self.wsPublicClient.start()
            self.bar_args = [{"channel": "candle1m", "instId": _} for _ in gw_symbols]
            self.wsPublicClient.subscribe(self.bar_args, self.on_message)
            time.sleep(0.5)

        self.ts_last_subcribe = self.get_ts() # 记录最后一次订阅时间

    def connect(self, symbols: list):
        '''
            用于 main_engine 中的 connect 理论上是只会调用一次 是gateway的入口函数
            会记录下所要求的数据
            Parameters:
                symbols: nd币对
        '''

        # == 实例化 clint ===
        self.init_ws_client()

        # === 启动 Websocket 监控线程 ===
        self.thread_ws_monitor = threading.Thread(target=self.ws_monitor)

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
            取消所有订阅 重新订阅 websocket
        '''
        try:
            self.WsPrivateClient.unsubscribe(self.order_trade_args, self.on_message) # 取消订阅
            if EventType.DEPTH.value in self.main_engine.strategy.topic[self.gateway_name].keys():
                self.wsPublicClient.unsubscribe(self.books5_args, self.on_message)
            if EventType.BAR.value in self.main_engine.strategy.topic[self.gateway_name].keys():
                self.wsPublicClient.unsubscribe(self.bar_args, self.on_message)
            msg = f"_reconnect: websocket 取消订阅"
            self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
        except Exception as e:
            msg = f"_reconnect error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)

        try:
            self.WsPrivateClient.subscribe(self.order_trade_args, self.on_message) # 重新订阅
            time.sleep(0.5)
            if EventType.DEPTH.value in self.main_engine.strategy.topic[self.gateway_name].keys():
                self.wsPublicClient.subscribe(self.books5_args, self.on_message)
            if EventType.BAR.value in self.main_engine.strategy.topic[self.gateway_name].keys():
                self.wsPublicClient.subscribe(self.bar_args, self.on_message)
            msg = f"_reconnect: websocket 重新订阅"
            self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
        except Exception as e:
            msg = f"_reconnect error: {e}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)

    def ws_monitor(self):
        '''
            监控 websocket 连接状态 检测到断链接自动重新链接
        '''
        msg = f"{self.gateway_name} Websocket 连接监控已启动 正在监控... 行情3秒未更新重连"
        self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
        while True:
            try:
                # 每隔1秒检测一次
                time.sleep(5)

                # ==== 如果订阅深度 重连条件判断 1. 深度数据超过3秒未更新
                if EventType.DEPTH.value in self.main_engine.strategy.topic[self.gateway_name].keys():
                    now_ts = self.get_ts()
                    is_reconnect_1 = True if self.ts_last_depth and now_ts - self.ts_last_depth > self.reconnect_seconds_after_lost_depth * 1000 else False # 深度数据超过N秒未更新

                    # 重连
                    if is_reconnect_1:
                        reason = f"行情{self.reconnect_seconds_after_lost_depth}秒未更新"
                        msg = f"ws_monitor: websocket {reason} 【重连中】..."
                        self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
                        self._reconnect()
                        msg = f"ws_monitor: websocket 【已重连】"
                        self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)

                # ==== 如果订阅K线 重连条件判断 1. K线数据超过3秒未更新
                if EventType.BAR.value in self.main_engine.strategy.topic[self.gateway_name].keys():
                    now_ts = self.get_ts()
                    is_reconnect_2 = True if self.ts_last_bar and now_ts - self.ts_last_bar > 63 * 1000 else False # K线数据超过N秒未更新

                    # 重连
                    if is_reconnect_2:
                        reason = f"K线{self.reconnect_seconds_after_lost_bar}秒未更新"
                        msg = f"ws_monitor: websocket {reason} 【重连中】..."
                        self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)
                        self._reconnect()
                        msg = f"ws_monitor: websocket 【已重连】"
                        self.main_engine.write_log(msg, level=LogLevel.INFO.value, source=self.gateway_name)

            except Exception as e:
                msg = f"ws_monitor error: {e}"
                self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
    
    def get_ts(self):
        '''
            获取13位时间戳
        '''
        return int(time.time() * 1000)

    def _sz2ndvolune(self, symbol: str, sz: str) -> float:
        '''
            将sz转化为nd_volume
            Parameters:
                symbol: nd_symbol 币对
                sz: 交易所下单数量 str
            Return:
                nd_volume
        '''
        product = self.symbol_contract_map[symbol].product
        if not sz:
            return 0
        if product in [Product.FUTURES, Product.SWAP, Product.OPTION]:
            return float(sz) * self.symbol_contract_map[symbol].size
        elif product == Product.SPOT:
            return float(sz)
        elif product == Product.MARGIN:
            raise NotImplementedError("暂不支持杠杠交易") # TODO: 杠杠交易
        
    def _get_defult_tdMode(self, product) -> str:
        '''
            获取某类产品的默认下单模式
        '''
        if product == Product.SPOT:
            tdMode = 'cash' if self.acctLv in ['1', '2'] else 'cross'
        elif product == Product.MARGIN:
            tdMode = 'isolated'
        elif product in [Product.FUTURES, Product.SWAP, Product.OPTION]:
            tdMode = 'isolated'
        else:
            msg = f"_get_defult_tdMode() 传入未知产品类型: {product}"
            self.main_engine.write_log(msg, level=LogLevel.ERROR.value, source=self.gateway_name)
            tdMode = ''
        return tdMode

    def count_precision_num(self, s: str):
        """
        判断一个字符串中数的数字精度
        :param s: 要判断的字符串
        :return: 小数点位数 int
        example: '1' -> 0; '0.1' -> 1; '0.10' -> 2
        """
        try:
            num = float(s)
        except ValueError:
            raise ValueError("输入必须是整数或浮点数的字符串")
        if '.' not in str(s):
            return 0
        else:
            return len(str(s).split('.')[1])
            
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