# Gateway 简介

    (1) 功能：
        a. 订阅账户与行情，将事件推送至 MainEngine
        b. 交易相关功能： 订单的增删查，下单时自动处理精度问题
        c. 账户、持仓相关信息查询

    (2) gateway 与 MainEngine 交互函数
        gateway.connect() # MainEngine订阅账户与行情推送

    
# Gateway 的编写规范：

    1. 交易函数 例如 buy sell short cover 精度自动处理 下单成功返回orderId 下单失败返回 ''
    2. 事件回调函数 on_depth on_trade on_order 自动向MainEngine发送事件
    3. 当MainEngine 调用启动函数 connect() 时 gatewayy 【依次】订阅 账户数据 深度数据 并启动websocket监控线程
    4. WebSocket 断线自动重连： 3秒未更新行情重连 & 30分钟定时重连

    
# 订单全生命周期管理
    （1）一笔订单正确的回调顺序 SUBMITTING -> NOTTRADED -> (ALLTRADED or CANCELLED):  
        即 3 次 on_order 回调 1 次 on_trade 回调
        示例：
```
2023-07-23 12:11:26,192.192 - nodelta.trader.engine - INFO - TestBnStrategy_dudalPos : on_order 回调 OrderData(symbol='ETH-USDT-SWAP', exchange=<Exchange.BINANCE: 'BINANCE'>, orderid='8389765609655635305', direction=<Direction.SHORT: '空'>, offset=<Offset.OPEN: '开'>, price=1869.54, volume=0.005, traded=0, status=<Status.SUBMITTING: '提交中'>, ts=1690085485599)
2023-07-23 12:11:26,193.193 - nodelta.trader.engine - INFO - TestBnStrategy_dudalPos : on_order 回调 OrderData(symbol='ETH-USDT-SWAP', exchange=<Exchange.BINANCE: 'BINANCE'>, orderid='8389765609655635305', direction=<Direction.SHORT: '空'>, offset=<Offset.OPEN: '开'>, price=1869.54, volume=0.005, traded=0.0, status=<Status.NOTTRADED: '未成交'>, ts=1690085485599)
2023-07-23 12:11:26,194.194 - nodelta.trader.engine - INFO - TestBnStrategy_dudalPos : on_order 回调 OrderData(symbol='ETH-USDT-SWAP', exchange=<Exchange.BINANCE: 'BINANCE'>, orderid='8389765609655635305', direction=<Direction.SHORT: '空'>, offset=<Offset.OPEN: '开'>, price=1869.54, volume=0.005, traded=0.005, status=<Status.ALLTRADED: '全部成交'>, ts=1690085485599)
```

# MainEngine 与 gateway 中的 symbol

    (1) MainEngine 中的 symbol 为标准化的 symbol 例如 BTC-USDT-SWAP; gateway传回MainEngine的symbol需要转化为标准化symbol

    (2) gateway 中的 symbol 为交易所格式的 gw_symbol 例如币安为 BTCUSDT; 与交易所进行交互时使用此 gw_symbol；从MainEnine接收到symbol需要转化为gw_symbol


# gatewany 详情（基于BinanceUMGateway）

    (1) __inint__:

```
    def __init__(self, key='', secret='', exchange = Exchange.BINANCE):
        super().__init__(key, secret, exchange)

        # === Gateway 基础信息 ===
        self.gateway_name: str = GatewayName.BINANCE_UM.value # Gateway名称不需要改
        self.exchange = exchange # 交易所名称不需要改
        self.key, self.secret = key, secret
        
        # 计时
        self.ts_last_depth: int = 0 # 上一次获取深度时的时间戳
        self.ts_last_subcribe: int = 0 # 上一次订阅深度的时间戳
        
        # == 实例化 clint ===
        self.init_client()

        # === 初始化工作 此前必须 init clint ===
        self.on_init() # 获取价格精度等信息

        # === 启动 Websocket 监控线程 ===
        self.thread_ws_monitor = threading.Thread(target=self.ws_monitor)
```

    (2) on_init
    
        需要获取交易所标的信息每个gateway总共需要三个map
        1. symbol_contract_map
        self.symbol_contract_map: Dict[str, ContractData] = {} # nd_symbol: ContractData
        2. gw_symbol -> nd_symbol 映射字典
        self.gw2nd_symbol_map = {v.gw_symbol: k for k, v in self.symbol_contract_map.items()}
        3. nd_symbol -> gw_symbol 映射字典
        self.nd2gw_symbol_map = {k: v.gw_symbol for k, v in self.symbol_contract_map.items()}

```
        # 1. 获取合约信息
        self.query_contracts()
        # 2. 获取单边 双边持仓模式
        self.is_dualSidePosition = self.http_client.get_position_mode()['dualSidePosition']
```

# send_order 关键字参数

    1. gateway 下到交易所的订单均是限价单

    2. 在继承 StrategyTemplate 类的策略中，交易函数可传递关键字参数，每个gateway可传递的关键词参数不同，参考交易所文档。

    ## BinanceUmGateway:

        除这些关键字以外的参数可以捕获： ['symbol', 'side', 'positionSide', 'type', 'quantity', 'price'] timeInForce 可能较为常用

        有效方式 (timeInForce):
            GTC - Good Till Cancel 成交为止
            IOC - Immediate or Cancel 无法立即成交(吃单)的部分就撤销
            FOK - Fill or Kill 无法全部立即成交就撤销
            GTX - Good Till Crossing 无法成为挂单方就撤销

    ## OKX Gateway

        如果使用市价订单，有可能返回的volume不准确，请使用限价单实现市价单功能

        ordType:
            订单类型，创建新订单时必须指定，您指定的订单类型将影响需要哪些订单参数和撮合系统如何执行您的订单，以下是有效的ordType：
            普通委托：
            limit：限价单，要求指定sz 和 px
            market：市价单，币币和币币杠杆，是市价委托吃单；交割合约和永续合约，是自动以最高买/最低卖价格委托，遵循限价机制；期权合约不支持市价委托；由于市价委托无法确定成交价格，为确保有足够的资产买入设定数量的交易币种，会多冻结5%的计价币资产
            高级委托：
            post_only：限价委托，在下单那一刻只做maker，如果该笔订单的任何部分会吃掉当前挂单深度，则该订单将被全部撤销。
            fok：限价委托，全部成交或立即取消，如果无法全部成交该笔订单，则该订单将被全部撤销。
            ioc：限价委托，立即成交并取消剩余，立即按照委托价格撮合成交，并取消该订单剩余未完成数量，不会在深度列表上展示委托数量。
            optimal_limit_ioc：市价委托，立即成交并取消剩余，仅适用于交割合约和永续合约。
