import json
from decimal import Decimal
from pathlib import Path


POOL_OWNER = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"
input_path = Path("src/data/raw/orca_sol_usdc_transactions.json")

with input_path.open(encoding="utf-8") as input_file:
    transactions = json.load(input_file)

processed_transactions = []

for transaction in transactions:
    balances_before = {}
    balances_after = {}

    for balance in transaction.get("meta", {}).get("preTokenBalances", []):
        if balance.get("owner") == POOL_OWNER:
            mint = balance["mint"]
            amount = Decimal(balance["uiTokenAmount"]["uiAmountString"])
            balances_before[mint] = balances_before.get(mint, Decimal("0")) + amount

    for balance in transaction.get("meta", {}).get("postTokenBalances", []):
        if balance.get("owner") == POOL_OWNER:
            mint = balance["mint"]
            amount = Decimal(balance["uiTokenAmount"]["uiAmountString"])
            balances_after[mint] = balances_after.get(mint, Decimal("0")) + amount

    usdc_before = balances_before.get(USDC_MINT, Decimal("0"))
    usdc_after = balances_after.get(USDC_MINT, Decimal("0"))
    sol_before = balances_before.get(SOL_MINT, Decimal("0"))
    sol_after = balances_after.get(SOL_MINT, Decimal("0"))

    processed_transactions.append(
        {
            "signature": transaction["transaction"]["signatures"][0],
            "block_time": transaction.get("blockTime"),
            "usdc_before": float(usdc_before),
            "usdc_after": float(usdc_after),
            "usdc_delta": float(usdc_after - usdc_before),
            "sol_before": float(sol_before),
            "sol_after": float(sol_after),
            "sol_delta": float(sol_after - sol_before),
        }
    )

print(json.dumps(processed_transactions[:10], ensure_ascii=False, indent=2))
