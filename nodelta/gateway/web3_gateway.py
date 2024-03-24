from web3 import Web3


class Web3Gateway:

    def __init__(self, rpc_url):
        self.web3 = Web3(Web3.HTTPProvider(rpc_url))

    def get_price(self, token_pair):
        raise NotImplementedError

    def trade(self, token_pair, amount, slippage):
        raise NotImplementedError