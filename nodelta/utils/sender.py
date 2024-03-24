import json
import logging
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def __get_content_from_internet(url, method='GET', headers=None, params=None, data=None):
    req = requests.request(method=method, url=url, headers=headers, params=params, data=data)
    return req


def get_content_from_internet(url, method='GET', headers=None, params=None, data=None):
    params = {
        "url": url,
        "method": method,
        "headers": headers,
        "params": params,
        "data": data
    }
    _req = __get_content_from_internet(**params)
    return _req

class Sender:
    
    def __init__():
        pass

    @staticmethod
    def lark(title, text, bot_url) -> None:
        '''
            Params:
                title: str
                text: str
                bot_url: str lark群组机器人的url
        '''
        content = f"{title}\n{text}"
        data = json.dumps({
            "msg_type": "text",
            "content": {
                "text": content
            }
        })
        headers = {
            'Content-Type': 'application/json'
        }
        try:
            target_url = bot_url
            req = get_content_from_internet(url=target_url, method="POST", headers=headers, data=data)
            logging.info(f"lark message sent, status_code:{req.status_code}, content:{req.content}")
        except Exception as e:
            logging.error(f"lark message sent failed, error:{e}")

    @staticmethod
    def wechat(msg, title, url, mentioned_all=False, mentioned_mobile_list=[]):
        headers = {"Content-Type":"application/json"}
        data = {
                "msgtype": "text",
                "text": {
                    # 让群机器人发送的消息
                    "content": title + '\n' + msg,
                    "mentioned_list":[],  # @全体成员，只有自己的话可以不要
                }
            }
        if mentioned_all:
            data["text"]["mentioned_list"].append("@all")
        if mentioned_mobile_list:
            data["text"]["mentioned_mobile_list"] = [str(i) for i in mentioned_mobile_list] 
        try:
            r = requests.post(url,headers=headers,json=data)
            logging.info(f"wechat message sent, status_code:{r.status_code}, content:{r.content}")
        except Exception as e:
            logging.error(f"wechat message sent failed, error:{e}")
        
if __name__ == "__main__":
    pass