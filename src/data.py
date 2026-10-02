import calendar
import csv
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
import requests


load_dotenv()

POOL_OWNER = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"
START_DATE = "2026-06-30 00:00:00"
END_DATE = "2026-06-30 01:00:00"
LIMIT = 100
REQUEST_TIMEOUT = 30
PAGE_DELAY = 0.25
CSV_COLUMNS = [
    "timestamp",
    "signature",
    "usdc_delta",
    "sol_delta",
    "price",
    "volume_usdc",
    "direction",
]
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "data/historical"
CSV_PATH = OUTPUT_DIR / "sol_usdc_swaps_stream.csv"
CHECKPOINT_PATH = OUTPUT_DIR / "checkpoint.json"


def date_timestamp(date_string):
    parsed = datetime.strptime(date_string, "%Y-%m-%d %H:%M:%S")
    return int(parsed.replace(tzinfo=timezone.utc).timestamp())


def parse_transaction(transaction):
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

    signatures = transaction.get("transaction", {}).get("signatures", [])
    usdc_before = balances_before.get(USDC_MINT, Decimal("0"))
    usdc_after = balances_after.get(USDC_MINT, Decimal("0"))
    sol_before = balances_before.get(SOL_MINT, Decimal("0"))
    sol_after = balances_after.get(SOL_MINT, Decimal("0"))
    return {
        "signature": signatures[0] if signatures else None,
        "block_time": transaction.get("blockTime"),
        "usdc_delta": float(usdc_after - usdc_before),
        "sol_delta": float(sol_after - sol_before),
    }


def classify_swap(parsed):
    usdc_delta = parsed["usdc_delta"]
    sol_delta = parsed["sol_delta"]
    if usdc_delta == 0 or sol_delta == 0:
        return None
    if sol_delta > 0 and usdc_delta < 0:
        direction = "SOL_IN"
    elif sol_delta < 0 and usdc_delta > 0:
        direction = "SOL_OUT"
    else:
        return None
    return {
        "timestamp": datetime.fromtimestamp(
            parsed["block_time"], tz=timezone.utc
        ).isoformat(),
        "signature": parsed["signature"],
        "usdc_delta": usdc_delta,
        "sol_delta": sol_delta,
        "price": abs(usdc_delta) / abs(sol_delta),
        "volume_usdc": abs(usdc_delta),
        "direction": direction,
    }


def write_swaps(rows, append):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("a" if append else "w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        if not append:
            writer.writeheader()
        writer.writerows(rows)


def write_checkpoint(checkpoint):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with CHECKPOINT_PATH.open("w", encoding="utf-8") as output:
        json.dump(checkpoint, output, ensure_ascii=False, indent=2)


def load_checkpoint():
    if not CHECKPOINT_PATH.exists():
        return None
    try:
        with CHECKPOINT_PATH.open(encoding="utf-8") as checkpoint_file:
            checkpoint = json.load(checkpoint_file)
    except (OSError, json.JSONDecodeError):
        return None
    if (
        checkpoint.get("START_DATE") != START_DATE
        or checkpoint.get("END_DATE") != END_DATE
    ):
        return None
    return checkpoint


def collect_stream():
    api_key = os.getenv("HELIUS_API_KEY")
    if not api_key:
        raise RuntimeError("A variável HELIUS_API_KEY não está definida.")

    start_timestamp = date_timestamp(START_DATE)
    end_timestamp = date_timestamp(END_DATE)
    url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    checkpoint = load_checkpoint()
    pagination_token = None
    page_number = 0
    total_transactions = 0
    total_swaps = 0
    total_other = 0
    first_timestamp = None
    last_timestamp = None
    append = False

    if checkpoint:
        answer = input("Continuar a coleta existente? [s/N] ").strip().lower()
        if answer == "s":
            if checkpoint.get("completed"):
                print("A coleta deste período já foi concluída.")
                return
            pagination_token = checkpoint.get("paginationToken")
            page_number = checkpoint.get("page", 0)
            total_transactions = checkpoint.get("total_transactions", 0)
            total_swaps = checkpoint.get("total_swaps", 0)
            total_other = checkpoint.get("total_other", 0)
            first_timestamp = checkpoint.get("first_timestamp")
            last_timestamp = checkpoint.get("last_timestamp")
            append = True
        else:
            checkpoint = None

    started_at = time.monotonic()
    pages_processed = page_number

    while True:
        options = {
            "transactionDetails": "full",
            "sortOrder": "asc",
            "limit": LIMIT,
            "filters": {
                "status": "succeeded",
                "tokenAccounts": "balanceChanged",
                "blockTime": {
                    "gte": start_timestamp,
                    "lte": end_timestamp,
                },
            },
        }
        if pagination_token:
            options["paginationToken"] = pagination_token
        payload = {
            "jsonrpc": "2.0",
            "id": "liquidity-radar",
            "method": "getTransactionsForAddress",
            "params": [POOL_OWNER, options],
        }

        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            response_data = response.json()
        except requests.HTTPError:
            message = response.text.strip()[:200] if response.text else ""
            if api_key in message:
                message = message.replace(api_key, "[redacted]")
            raise RuntimeError(
                f"Erro HTTP {response.status_code}: {message}"
            ) from None
        except requests.Timeout as error:
            raise RuntimeError("Timeout na requisição à Helius.") from error
        except requests.RequestException as error:
            raise RuntimeError("Erro de conexão com a Helius.") from error
        except ValueError as error:
            raise RuntimeError("Resposta JSON inválida da Helius.") from error

        result = response_data.get("result") if isinstance(response_data, dict) else None
        if not isinstance(result, dict):
            raise RuntimeError("Resposta inesperada da Helius.")
        transactions_page = result.get("data", [])
        if not isinstance(transactions_page, list):
            raise RuntimeError("A lista de transações da Helius é inválida.")
        if not transactions_page:
            write_checkpoint(
                {
                    "paginationToken": pagination_token,
                    "page": pages_processed,
                    "total_transactions": total_transactions,
                    "total_swaps": total_swaps,
                    "total_other": total_other,
                    "last_blockTime": None,
                    "START_DATE": START_DATE,
                    "END_DATE": END_DATE,
                    "first_timestamp": first_timestamp,
                    "last_timestamp": last_timestamp,
                    "completed": True,
                }
            )
            break

        next_token = result.get("paginationToken")
        rows = []
        page_last_timestamp = None
        for transaction in transactions_page:
            if not isinstance(transaction, dict):
                raise RuntimeError("Transação inesperada na resposta da Helius.")
            parsed = parse_transaction(transaction)
            block_time = parsed["block_time"]
            if block_time is None:
                continue
            page_last_timestamp = block_time
            swap = classify_swap(parsed)
            if swap is not None:
                rows.append(swap)
            elif parsed["usdc_delta"] != 0 and parsed["sol_delta"] != 0:
                total_other += 1

        write_swaps(rows, append)
        append = True
        pages_processed += 1
        total_transactions += len(transactions_page)
        total_swaps += len(rows)
        if rows:
            if first_timestamp is None:
                first_timestamp = rows[0]["timestamp"]
            last_timestamp = rows[-1]["timestamp"]

        checkpoint = {
            "paginationToken": next_token,
            "page": pages_processed,
            "total_transactions": total_transactions,
            "total_swaps": total_swaps,
            "total_other": total_other,
            "last_blockTime": page_last_timestamp,
            "START_DATE": START_DATE,
            "END_DATE": END_DATE,
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "completed": False,
        }
        write_checkpoint(checkpoint)
        last_time = (
            datetime.fromtimestamp(page_last_timestamp, tz=timezone.utc).strftime(
                "%H:%M:%S"
            )
            if page_last_timestamp is not None
            else "N/A"
        )
        print(
            f"Página {pages_processed} | tx: {len(transactions_page)} | "
            f"swaps: {len(rows)} | total tx: {total_transactions} | "
            f"total swaps: {total_swaps} | último horário: {last_time}"
        )

        if pages_processed % 100 == 0:
            elapsed = time.monotonic() - started_at
            useful_rate = (
                total_swaps / total_transactions * 100
                if total_transactions
                else 0
            )
            print(
                f"Tempo decorrido: {elapsed:.2f}s | páginas: {pages_processed} | "
                f"transações: {total_transactions} | swaps: {total_swaps} | "
                f"swaps por transação: {useful_rate:.2f}%"
            )

        if not next_token or next_token == pagination_token:
            checkpoint["completed"] = True
            write_checkpoint(checkpoint)
            break
        pagination_token = next_token
        time.sleep(PAGE_DELAY)

    elapsed = time.monotonic() - started_at
    useful_rate = total_swaps / total_transactions * 100 if total_transactions else 0
    print(f"Páginas processadas: {pages_processed}")
    print(f"Transações processadas: {total_transactions}")
    print(f"Swaps válidos salvos: {total_swaps}")
    print(f"OTHER: {total_other}")
    print(f"Percentual útil: {useful_rate:.2f}%")
    print(f"Primeiro timestamp: {first_timestamp}")
    print(f"Último timestamp: {last_timestamp}")
    print(f"Tempo total de execução: {elapsed:.2f}s")
    print(f"CSV: {CSV_PATH}")


if __name__ == "__main__":
    try:
        collect_stream()
    except RuntimeError as error:
        print(f"Coleta interrompida: {error}")
