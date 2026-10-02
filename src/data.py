import calendar
import json
import os
import time
from dotenv import load_dotenv
from decimal import Decimal
from pathlib import Path

import pandas as pd
import requests

load_dotenv()

POOL_OWNER = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"
START_DATE = "2026-06-30"
END_DATE = "2026-09-22"
LIMIT = 100
MAX_PAGES = 10
REQUEST_TIMEOUT = 30
PAGE_DELAY = 0.25


def date_timestamp(date_string):
    return calendar.timegm(time.strptime(date_string, "%Y-%m-%d"))


def collect_transactions():
    api_key = os.getenv("HELIUS_API_KEY")
    if not api_key:
        raise RuntimeError("A variável HELIUS_API_KEY não está definida.")

    start_time = date_timestamp(START_DATE)
    end_time = date_timestamp(END_DATE) + 24 * 60 * 60 - 1
    url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    transactions_by_signature = {}
    pagination_token = None
    pages_collected = 0

    while pages_collected < MAX_PAGES:
        query_options = {
            "limit": LIMIT,
            "transactionDetails": "full",
            "sortOrder": "asc",
            "filters": {
                "status": "succeeded",
                "blockTime": {
                    "gte": start_time,
                    "lte": end_time,
                },
            },
        }
        if pagination_token:
            query_options["paginationToken"] = pagination_token
        payload = {
            "jsonrpc": "2.0",
            "id": "liquidity-radar",
            "method": "getTransactionsForAddress",
            "params": [POOL_OWNER, query_options],
        }
        headers = {"Content-Type": "application/json"}
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.HTTPError:
            message = response.text.strip()[:200] if response.text else ""
            if api_key and api_key in message:
                message = message.replace(api_key, "[redacted]")
            raise RuntimeError(
                f"Erro HTTP {response.status_code}: {message}"
            ) from None
        except requests.Timeout as error:
            raise RuntimeError("Timeout ou erro de conexão com a Helius.") from error
        except requests.RequestException as error:
            raise RuntimeError("Erro de conexão com a Helius.") from error

        try:
            response_payload = response.json()
        except ValueError as error:
            raise RuntimeError("Resposta JSON inesperada da Helius.") from error

        if not isinstance(response_payload, dict):
            raise RuntimeError("Resposta JSON inesperada da Helius.")
        if isinstance(response_payload, dict) and "error" in response_payload:
            error_message = response_payload["error"].get("message", "")
            raise RuntimeError(f"Erro na resposta da Helius: {error_message}")

        result = response_payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("O campo result da Helius não é um dicionário.")

        transactions_page = result.get("data", [])
        if not isinstance(transactions_page, list):
            raise RuntimeError("Resposta JSON inesperada da Helius.")
        next_pagination_token = result.get("paginationToken")
        if not transactions_page:
            break

        pages_collected += 1
        page_times = []
        added_count = 0
        for transaction in transactions_page:
            if not isinstance(transaction, dict):
                raise RuntimeError("Transação inesperada na resposta da Helius.")

            signatures = transaction.get("transaction", {}).get("signatures", [])
            signature = signatures[0] if signatures else None
            block_time = transaction.get("blockTime")
            if block_time is not None:
                page_times.append(block_time)
            status_succeeded = (
                transaction.get("transactionError") is None
                and transaction.get("meta", {}).get("err") is None
            )
            if (
                signature
                and block_time is not None
                and status_succeeded
                and start_time <= block_time <= end_time
                and signature not in transactions_by_signature
            ):
                transactions_by_signature[signature] = transaction
                added_count += 1

        page_min = min(page_times) if page_times else None
        page_max = max(page_times) if page_times else None
        print(
            f"Página {pages_collected} | recebidas: {len(transactions_page)} | "
            f"adicionadas: {added_count} | total: {len(transactions_by_signature)} | "
            f"blockTime: {page_min} - {page_max}"
        )
        if not next_pagination_token or next_pagination_token == pagination_token:
            break
        pagination_token = next_pagination_token
        time.sleep(PAGE_DELAY)

    transactions = sorted(
        transactions_by_signature.values(),
        key=lambda transaction: transaction["blockTime"],
    )
    return transactions, pages_collected


def save_raw_transactions(transactions):
    output_path = (
        Path(__file__).resolve().parent.parent
        / "data/raw/orca_sol_usdc_transactions.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(transactions, output_file, ensure_ascii=False, indent=2)
    return output_path


def parse_transactions(transactions):
    processed_transactions = []
    for transaction in transactions:
        balances_before = {}
        balances_after = {}

        for balance in transaction.get("meta", {}).get("preTokenBalances", []):
            if balance.get("owner") == POOL_OWNER:
                mint = balance["mint"]
                amount = Decimal(balance["uiTokenAmount"]["uiAmountString"])
                balances_before[mint] = (
                    balances_before.get(mint, Decimal("0")) + amount
                )

        for balance in transaction.get("meta", {}).get("postTokenBalances", []):
            if balance.get("owner") == POOL_OWNER:
                mint = balance["mint"]
                amount = Decimal(balance["uiTokenAmount"]["uiAmountString"])
                balances_after[mint] = (
                    balances_after.get(mint, Decimal("0")) + amount
                )

        usdc_before = balances_before.get(USDC_MINT, Decimal("0"))
        usdc_after = balances_after.get(USDC_MINT, Decimal("0"))
        sol_before = balances_before.get(SOL_MINT, Decimal("0"))
        sol_after = balances_after.get(SOL_MINT, Decimal("0"))
        signatures = transaction.get("transaction", {}).get("signatures", [])
        processed_transactions.append(
            {
                "signature": signatures[0] if signatures else None,
                "block_time": transaction.get("blockTime"),
                "usdc_before": float(usdc_before),
                "usdc_after": float(usdc_after),
                "usdc_delta": float(usdc_after - usdc_before),
                "sol_before": float(sol_before),
                "sol_after": float(sol_after),
                "sol_delta": float(sol_after - sol_before),
            }
        )
    return processed_transactions


if __name__ == "__main__":
    input_path = (
        Path(__file__).resolve().parent.parent
        / "data/raw/orca_sol_usdc_transactions.json"
    )
    with input_path.open(encoding="utf-8") as input_file:
        raw_transactions = json.load(input_file)

    processed_transactions = parse_transactions(raw_transactions)
    total_transactions = len(processed_transactions)
    moved_transactions = [
        transaction
        for transaction in processed_transactions
        if transaction["usdc_delta"] != 0 or transaction["sol_delta"] != 0
    ]
    both_moved = [
        transaction
        for transaction in processed_transactions
        if transaction["usdc_delta"] != 0 and transaction["sol_delta"] != 0
    ]
    one_moved = [
        transaction
        for transaction in moved_transactions
        if not (
            transaction["usdc_delta"] != 0 and transaction["sol_delta"] != 0
        )
    ]
    no_movement = total_transactions - len(moved_transactions)

    swap_rows = []
    for transaction in both_moved:
        usdc_delta = transaction["usdc_delta"]
        sol_delta = transaction["sol_delta"]
        if sol_delta > 0 and usdc_delta < 0:
            direction = "SOL_IN"
        elif sol_delta < 0 and usdc_delta > 0:
            direction = "SOL_OUT"
        else:
            direction = "OTHER"
        swap_rows.append(
            {
                "timestamp": pd.to_datetime(
                    transaction["block_time"], unit="s", utc=True
                ),
                "signature": transaction["signature"],
                "usdc_delta": usdc_delta,
                "sol_delta": sol_delta,
                "price": abs(usdc_delta) / abs(sol_delta),
                "volume_usdc": abs(usdc_delta),
                "direction": direction,
            }
        )

    swaps = pd.DataFrame(
        swap_rows,
        columns=[
            "timestamp",
            "signature",
            "usdc_delta",
            "sol_delta",
            "price",
            "volume_usdc",
            "direction",
        ],
    )
    print(f"Total de transações brutas: {total_transactions}")
    print(f"Transações com algum delta: {len(moved_transactions)}")
    print(f"Transações com ambos os deltas: {len(both_moved)}")
    print(f"Transações com apenas um ativo movimentado: {len(one_moved)}")
    print(f"Transações sem movimentação: {no_movement}")
    print("Contagem por direção:")
    print(swaps["direction"].value_counts().reindex(
        ["SOL_IN", "SOL_OUT", "OTHER"], fill_value=0
    ))
    print("Estatísticas de price e volume_usdc:")
    print(swaps[["price", "volume_usdc"]].describe())
    price_columns = [
        "timestamp",
        "signature",
        "usdc_delta",
        "sol_delta",
        "price",
        "volume_usdc",
        "direction",
    ]
    print("5 menores valores de price:")
    print(swaps.nsmallest(5, "price")[price_columns])
    print("5 maiores valores de price:")
    print(swaps.nlargest(5, "price")[price_columns])
