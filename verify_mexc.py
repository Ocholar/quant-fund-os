import ccxt
import os
from dotenv import load_dotenv

def verify():
    load_dotenv()
    apiKey = os.getenv("MEXC_API_KEY")
    secret = os.getenv("MEXC_API_SECRET")
    
    print(f"Testing MEXC connection with key: {apiKey[:5]}...")
    
    mexc = ccxt.mexc({
        'apiKey': apiKey,
        'secret': secret,
        'enableRateLimit': True,
    })
    
    try:
        balance = mexc.fetch_balance()
        print("Success! Connection verified.")
        print(f"Total USDT: {balance['total'].get('USDT', 0)}")
    except Exception as e:
        print(f"Connection Failed: {e}")

if __name__ == "__main__":
    verify()
