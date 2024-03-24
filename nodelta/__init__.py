import os
import sys

# === 第三方SDK环境变量配置 ===
# BINANCE
binance_sdk_path = os.path.join(os.path.dirname(__file__), "gateway", "SDK", "binance_sdk")
sys.path.append(binance_sdk_path)
# OKX
okx_sdk_path = os.path.join(os.path.dirname(__file__), "gateway", "SDK", "okx_sdk")
sys.path.append(okx_sdk_path)