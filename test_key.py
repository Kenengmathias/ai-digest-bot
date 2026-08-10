import os
import requests

# 1. Grab your token from environment variables
api_key = os.environ.get("OPENROUTER_API_KEY")

if not api_key:
    print("❌ ERROR: Your 'OPENROUTER_API_KEY' environment variable is completely empty!")
    exit(1)

# List of the models you are troubleshooting
models_to_test = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "openai/gpt-oss-20b:free"
]

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

print(f"🔄 Starting test using API Key ending in: ...{api_key[-6:]}\n")

for model in models_to_test:
    print(f"--- Testing Model: {model} ---")
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Respond with the word 'Success'."}]
    }
    
    try:
        response = requests.post(
            "https://telegram.org" if "telegram" in model else "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=10
        )
        
        # Parse the JSON response
        res_data = response.json()
        
        if response.status_code == 200:
            text_out = res_data['choices'][0]['message']['content'].strip()
            print(f"✅ SUCCESS! Response: \"{text_out}\"\n")
        else:
            print(f"❌ FAILED! HTTP Status Code: {response.status_code}")
            print(f"   API Error Message: {res_data.get('error', {}).get('message', 'No error message provided.')}\n")
            
    except Exception as e:
        print(f"💥 CRITICAL SCRIPT ERROR: {e}\n")
