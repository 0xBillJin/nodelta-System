from typing import Any, Dict, List, Tuple
import time
import numpy as np
import threading
import os
import sys
import pathlib
ndSys_PATH = str(pathlib.Path(__file__).parent.parent)
print(f"ndSys_PATH: {ndSys_PATH}")
if ndSys_PATH not in sys.path:
    sys.path.append(ndSys_PATH)

import nodelta # 需要先导入 nodelta 模块 自动配置环境变量
import config
from nodelta.trader.constant import (
    LogLevel, Direction, Offset, Status, Exchange, Product, GatewayName, EventType
)
from nodelta.trader.object import ContractData, OrderData, TradeData, PositionData, AssetData, AccountData, DepthData, Event
from nodelta.trader.strategy_template import StrategyTemplate
from nodelta.trader.engine import MainEngine
from nodelta.gateway.okx_gateway import OkxGateway
from nodelta.gateway.binance_gateway import BinanceUmGateway


def main():
    # 1. 创建 GateWay
    OK_API = config.get_api("OKX", 'test')
    okx_gateway = OkxGateway(key=OK_API['key'], secret=OK_API['secret'], passphrase=OK_API['passphrase'])
    
    BINANCE_API = config.get_api("BINANCE", 'test')
    binance_gateway = BinanceUmGateway(key=BINANCE_API['key'], secret=BINANCE_API['secret'])

    # 2. 打印 symbol_contract_map
    symbol = "ETH-USDT-FUTURES-240329"
    # 2.1 打印 okx symbol_contract_map
    print('------------------')
    print("okx symbol_contract_map:")
    # print(okx_gateway.symbol_contract_map[symbol])
    print(f"OKX POSITION: {okx_gateway.query_position(symbol)}")
    print(f"OKX ACCOUNT: {okx_gateway.query_account()}")
    
    # 2.2 打印 binance symbol_contract_map
    # print('------------------')
    # print("binance symbol_contract_map:")
    # print(binance_gateway.symbol_contract_map[symbol])

if __name__ == "__main__":
    main()


    