"""Exact llama.cpp chat-template/tokenizer capacity checks; no generation API."""
import json
import os
import urllib.request


class LlamaContextGuard:
    def __init__(self,cfg):
        if cfg.model.get('provider')!='openai':
            raise ValueError('This candidate context adapter is verified for the local llama.cpp endpoint only')
        self.base=str(cfg.model.base_url).rstrip('/')
        if self.base.endswith('/v1'):self.base=self.base[:-3]
        self.limit=cfg.agent.shared_plan.context_limit
        self.reserve=cfg.agent.shared_plan.context_reserve
        self.headers={'Content-Type':'application/json'}
        token=os.environ.get(cfg.model.get('api_key_env','LLAMA_API_KEY'))
        if token:self.headers['Authorization']='Bearer '+token
        self.props=self._request('/props')
        actual=self.props.get('default_generation_settings',{}).get('n_ctx')
        if type(actual) is not int or self.limit>actual:
            raise ValueError('Configured context limit exceeds or cannot verify actual server context')
        from pathlib import Path
        if Path(self.props.get('model_path','')).name!=str(cfg.model.model_id):
            raise ValueError('Serving model differs from candidate model id')

    def _request(self,path,body=None):
        request=urllib.request.Request(self.base+path,data=None if body is None else json.dumps(body).encode(),headers=self.headers)
        with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)

    def __call__(self,messages,max_new_tokens,*,chat_template_kwargs=None):
        roles={'human':'user','ai':'assistant','system':'system'}
        packed=[]
        for message in messages:
            if message.type not in roles or not isinstance(message.content,str):
                raise ValueError('Unsupported candidate message in exact context checker')
            packed.append(dict(role=roles[message.type],content=message.content))
        body={'messages':packed,'add_generation_prompt':True}
        if chat_template_kwargs is not None:
            body['chat_template_kwargs']=chat_template_kwargs
        prompt=self._request('/apply-template',body)['prompt']
        count=len(self._request('/tokenize',{'content':prompt,'add_special':False,'parse_special':True})['tokens'])
        state=dict(input_tokens=count,max_new_tokens=max_new_tokens,reserved_template_tokens=self.reserve,
                   context_limit=self.limit,tokenizer='loaded_llama_cpp_model',fits=count+max_new_tokens+self.reserve<=self.limit)
        if not state['fits']:
            raise ValueError('Candidate context budget exhausted before generation: '+json.dumps(state))
        return state
