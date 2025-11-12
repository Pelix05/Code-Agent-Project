import os
from openai import OpenAI

# load .env if present
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

HF_TOKEN = os.getenv('HF_TOKEN') or os.getenv('HUGGINGFACE_API_TOKEN')
if not HF_TOKEN:
    print('HF token not set in environment')
    raise SystemExit(1)

client = OpenAI(base_url="https://router.huggingface.co/v1", api_key=HF_TOKEN)
model = os.getenv('HUGGINGFACE_ROUTER_MODEL') or 'deepseek-ai/DeepSeek-OCR:novita'

messages = [
    {
        'role': 'user',
        'content': [
            {'type': 'text', 'text': 'Describe this image in one sentence.'},
            {'type': 'image_url', 'image_url': {'url': 'https://cdn.britannica.com/61/93061-050-99147DCE/Statue-of-Liberty-Island-New-York-Bay.jpg'}}
        ]
    }
]

try:
    resp = client.chat.completions.create(model=model, messages=messages)
    # print raw response
    import json
    print(json.dumps(resp.__dict__, default=str, indent=2))
    try:
        choice = resp.choices[0].message
        print('\n------ MESSAGE ------\n')
        print(choice)
    except Exception:
        print('\nNo choices or message found; dump above shows raw response object')
except Exception as e:
    print('Exception calling HF Router:', repr(e))
