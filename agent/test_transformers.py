from transformers import pipeline

messages = [
    {"role": "system", "content": "You are a pirate chatbot who always responds in pirate speak!"},
    {"role": "user", "content": "Who are you?"},
]

# Create a single prompt from messages (simple concatenation)
prompt = "\n".join([m['content'] for m in messages])

print('Using model from env or default:')
from os import getenv
model = getenv('TRANSFORMERS_MODEL', 'mistralai/Mistral-7B-Instruct-v0.3')
print('MODEL=', model)

chatbot = pipeline("text-generation", model=model, trust_remote_code=True)
res = chatbot(prompt, max_new_tokens=int(getenv('TRANSFORMERS_MAX_TOKENS', '128')))
print('RESULT:')
print(res)

# Attempt to print generated text tidy
try:
    print('\n'.join([r.get('generated_text', str(r)) for r in res]))
except Exception:
    print(res)
