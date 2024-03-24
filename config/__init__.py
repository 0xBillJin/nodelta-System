import pathlib
import tomli
import os

def get_api(exchange: str, name: str) -> dict:
    '''
        获取api
    Params:
        exchange: 交易所名称
        name: API账户名称
    '''
    toml_path = pathlib.Path(__file__).parent / "API_params.toml"
    assert os.path.exists(toml_path), f"{toml_path} 不存在"
    with toml_path.open(mode="rb") as fp:
        config = tomli.load(fp)

        exc_config = {k.upper(): v for k, v in config.items()}.get(exchange.upper(), None)
        if exc_config is None:
            raise ValueError(f"exchange: {exchange} not in API_params.toml, please check it")
        name_config = {k.upper(): v for k, v in exc_config.items()}.get(name.upper(), None)
        if name_config is None:
            raise ValueError(f"name: {name} not in API_params.toml, please check it")
    return name_config

def get_strategy_params(strategy: str, strategy_name: str) -> dict:
    '''
        获取策略参数
    Params:
        strategy: 策略名称 .py文件名称
        strategy_name: 策略名称
    '''
    toml_path = pathlib.Path(__file__).parent / "strategy_params.toml"
    assert os.path.exists(toml_path), f"{toml_path} 不存在"
    with toml_path.open(mode="rb") as fp:
        config = tomli.load(fp)

        strategy_config = {k.upper(): v for k, v in config.items()}.get(strategy.upper(), None)
        if strategy_config is None:
            raise ValueError(f"strategy: {strategy} not in strategy_params.toml, please check it")
        strategy_name_config = {k.upper(): v for k, v in strategy_config.items()}.get(strategy_name.upper(), None)
        if strategy_name_config is None:
            raise ValueError(f"strategy_name: {strategy_name} not in strategy_params.toml, please check it")
    return strategy_name_config

def get_sender_params(channel='LARK'):
    '''
        获取消息发送参数
    Params:
        channel: 消息发送渠道
    '''
    toml_path = pathlib.Path(__file__).parent / "sender_params.toml"
    assert os.path.exists(toml_path), f"{toml_path} 不存在"
    with toml_path.open(mode="rb") as fp:
        config = tomli.load(fp)
        channel_config = {k.upper(): v for k, v in config.items()}.get(channel.upper(), None)
        if channel_config is None:
            raise ValueError(f"channel: {channel} not in sender_params.toml, please check it")
    return channel_config

if __name__ == "__main__":
    pass