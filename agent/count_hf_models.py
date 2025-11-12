import json
p='d:/semester5/ai-agent-project/agent/hf_router_models.json'
with open(p,'r',encoding='utf-8') as f:
    j=json.load(f)
data=j.get('data',[])
print(len(data))
text_capable=sum(1 for m in data if isinstance(m, dict) and 'architecture' in m and 'text' in m.get('architecture',{}).get('output_modalities',[]))
print('text_capable', text_capable)
print('\nTop 8 model ids:')
for m in data[:8]:
    if isinstance(m, dict):
        print('-', m.get('id'))
    else:
        print('-', m)
