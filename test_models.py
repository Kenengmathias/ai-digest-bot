import os
import requests

api_key = os.environ.get("OPENROUTER_API_KEY")

if not api_key:
    print("❌ ERROR: Your 'OPENROUTER_API_KEY' environment variable is empty!")
    exit(1)

models_to_test = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "openai/gpt-oss-20b:free"
]

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

print(f"🔄 Starting GitHub environment test using key ending in: ...{api_key[-4:] if len(api_key) > 4 else '???'} \n")

for model in models_to_test:
    print(f"--- Testing Model: {model} ---")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Respond with the word 'Success'."}]
    }
    try:
        response = requests.post(
            "https://openrouter.ai",
            json=payload,
            headers=headers,
            timeout=15
        )
        res_data = response.json()
        if response.status_code == 200:
            text_out = res_data['choices'][0]['message']['content'].strip()
            print(f"✅ SUCCESS! Response: \"{text_out}\"\n")
        else:
            print(f"❌ FAILED! HTTP Status Code: {response.status_code}")
            print(f"   API Error Message: {res_data.get('error', {}).get('message', 'No error details.')}\n")
    except Exception as e:
        print(f"💥 SCRIPT ERROR: {e}\n")
