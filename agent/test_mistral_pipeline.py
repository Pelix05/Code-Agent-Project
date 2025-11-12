from transformers import pipeline

messages = [
    {"role": "system", "content": "You are a pirate chatbot who always responds in pirate speak!"},
    {"role": "user", "content": "Who are you?"},
]

chatbot = pipeline("text-generation", model="mistralai/Mistral-7B-Instruct-v0.3", trust_remote_code=True)
# Concatenate messages into a single prompt for text-generation pipeline
prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
resp = chatbot(prompt, max_new_tokens=128)
print(resp[0].get('generated_text') if isinstance(resp, list) and isinstance(resp[0], dict) else resp)
