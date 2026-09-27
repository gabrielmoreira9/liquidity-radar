import os
from datetime import datetime, timedelta, timezone

import requests


START_DATE = "2026-06-30"
END_DATE = "2026-09-22"

start_timestamp = int(
    datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
)
end_timestamp = int(
    (
        datetime.strptime(END_DATE, "%Y-%m-%d") + timedelta(days=1)
    ).replace(tzinfo=timezone.utc).timestamp()
)

api_key = os.environ["HELIUS_API_KEY"]
url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"

payload = {
    "jsonrpc": "2.0",
    "id": "1",
    "method": "getTransactionsForAddress",
    "params": [
        "Vote111111111111111111111111111111111111111",
        {
            "transactionDetails": "signatures",
            "limit": 50,
            "sortOrder": "desc",
            "filters": {
                "status": "succeeded",
                "blockTime": {
                    "gte": start_timestamp,
                    "lt": end_timestamp,
                },
                "tokenTransfer": {
                    "direction": "in",
                    "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                },
            },
        },
    ],
}
headers = {"Content-Type": "application/json"}

response = requests.post(url, json=payload, headers=headers, timeout=30)
response.raise_for_status()

print(response.text)
