import csv
import json
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
import requests


load_dotenv()

POOL_OWNER = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"
HISTORY_START = "2026-06-30"
HISTORY_END = "2026-07-01"  # Exclusive UTC boundary.
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
SWAPS_DIR = OUTPUT_DIR / "swaps"
CHECKPOINT_PATH = OUTPUT_DIR / "backfill_checkpoint.json"


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


def day_state(day):
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return {
        "current_date": day,
        "current_start_timestamp": int(start.timestamp()),
        "current_end_timestamp": int((start + timedelta(days=1)).timestamp()),
        "paginationToken": None,
        "page_number": 0,
        "total_transactions_current_day": 0,
        "total_swaps_current_day": 0,
        "last_block_time": None,
        "csv_offset": 0,
        "day_exhausted": False,
        "elapsed_seconds_current_day": 0,
    }


def csv_path(day):
    return SWAPS_DIR / f"sol_usdc_swaps_{day}.csv"


def write_checkpoint(checkpoint):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT_PATH.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(checkpoint, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, CHECKPOINT_PATH)


def load_checkpoint():
    if not CHECKPOINT_PATH.exists():
        return None
    try:
        with CHECKPOINT_PATH.open(encoding="utf-8") as source:
            checkpoint = json.load(source)
        required = set(day_state(HISTORY_START)) | {
            "total_transactions_all_days", "total_swaps_all_days",
            "completed_days", "history_start", "history_end", "completed",
        }
        if not isinstance(checkpoint, dict) or not required <= checkpoint.keys():
            raise ValueError("campos ausentes")
        if (checkpoint["history_start"], checkpoint["history_end"]) != (
            HISTORY_START, HISTORY_END
        ):
            raise ValueError("período diferente")
        expected = day_state(checkpoint["current_date"])
        for key in ("current_start_timestamp", "current_end_timestamp"):
            if checkpoint[key] != expected[key]:
                raise ValueError("intervalo diário inválido")
        if not HISTORY_START <= checkpoint["current_date"] < HISTORY_END:
            raise ValueError("dia fora do período")
        return checkpoint
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise RuntimeError(f"Checkpoint inválido; arquivos preservados: {error}") from None


def duration(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def collect_stream():
    checkpoint = load_checkpoint()
    if checkpoint:
        print(
            f"Checkpoint: dia {checkpoint['current_date']} | "
            f"próxima página {checkpoint['page_number'] + 1} | "
            f"dias concluídos: {len(checkpoint['completed_days'])}"
        )
        if input("Continuar a coleta existente? [s/N] ").strip().lower() == "s":
            if checkpoint["completed"]:
                print("O histórico deste período já foi concluído.")
                return
        else:
            checkpoint = None

    api_key = os.getenv("HELIUS_API_KEY")
    if not api_key:
        raise RuntimeError("A variável HELIUS_API_KEY não está definida.")
    if checkpoint is None:
        existing = list(SWAPS_DIR.glob("sol_usdc_swaps_????-??-??.csv"))
        if existing or CHECKPOINT_PATH.exists():
            if input(
                "Recomeçar apagará os CSVs históricos diários e o checkpoint. "
                "Confirma? [s/N] "
            ).strip().lower() != "s":
                print("Coleta cancelada; arquivos preservados.")
                return
            for path in existing:
                path.unlink()
            CHECKPOINT_PATH.unlink(missing_ok=True)
        checkpoint = {
            **day_state(HISTORY_START),
            "total_transactions_all_days": 0,
            "total_swaps_all_days": 0,
            "completed_days": [],
            "history_start": HISTORY_START,
            "history_end": HISTORY_END,
            "completed": False,
        }
        write_checkpoint(checkpoint)

    SWAPS_DIR.mkdir(parents=True, exist_ok=True)
    url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    while not checkpoint["completed"]:
        day = checkpoint["current_date"]
        path = csv_path(day)
        offset = checkpoint["csv_offset"]
        if offset and (not path.exists() or path.stat().st_size < offset):
            raise RuntimeError("CSV menor que a posição confirmada; arquivos preservados.")
        # Roll back only uncommitted bytes, including an interrupted page append.
        with path.open("r+b" if path.exists() else "w+b") as output:
            output.truncate(offset)
        started_at = time.monotonic()
        elapsed_before = checkpoint["elapsed_seconds_current_day"]
        with path.open("a", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
            if offset == 0:
                writer.writeheader()
            while not checkpoint["day_exhausted"]:
                start_timestamp = checkpoint["current_start_timestamp"]
                end_timestamp = checkpoint["current_end_timestamp"]
                pagination_token = checkpoint["paginationToken"]
                options = {
                    "transactionDetails": "full",
                    "sortOrder": "asc",
                    "limit": LIMIT,
                    "filters": {
                        "status": "succeeded",
                        "tokenAccounts": "balanceChanged",
                        # Helius uses an inclusive lte; integer seconds implement [start, end).
                        "blockTime": {"gte": start_timestamp, "lte": end_timestamp - 1},
                    },
                }
                if pagination_token:
                    options["paginationToken"] = pagination_token
                payload = {
                    "jsonrpc": "2.0", "id": "liquidity-radar",
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
                if "data" not in result or len(transactions_page) > LIMIT:
                    raise RuntimeError("Página inesperada da Helius.")
                next_token = result.get("paginationToken")
                if next_token is not None and not isinstance(next_token, str):
                    raise RuntimeError("paginationToken inválido; checkpoint preservado.")
                if next_token and next_token == pagination_token:
                    raise RuntimeError("paginationToken repetido; checkpoint preservado.")
                if not transactions_page and next_token:
                    raise RuntimeError("Página vazia com paginationToken; checkpoint preservado.")
                rows = []
                page_last_time = checkpoint["last_block_time"]
                for transaction in transactions_page:
                    if not isinstance(transaction, dict):
                        raise RuntimeError("Transação inesperada na resposta da Helius.")
                    block_time = transaction.get("blockTime")
                    if not isinstance(block_time, int):
                        raise RuntimeError("blockTime inválido; checkpoint preservado.")
                    if not start_timestamp <= block_time < end_timestamp:
                        raise RuntimeError("Transação fora do intervalo [start, end).")
                    parsed = parse_transaction(transaction)
                    page_last_time = block_time
                    swap = classify_swap(parsed)
                    if swap is not None:
                        rows.append(swap)
                writer.writerows(rows)
                output.flush()
                os.fsync(output.fileno())
                updated = dict(checkpoint)
                updated.update({
                    "paginationToken": next_token,
                    "page_number": checkpoint["page_number"] + bool(transactions_page),
                    "last_block_time": page_last_time,
                    "csv_offset": os.fstat(output.fileno()).st_size,
                    "day_exhausted": not transactions_page or not next_token,
                    "elapsed_seconds_current_day": elapsed_before + time.monotonic() - started_at,
                })
                for key, count in (
                    ("total_transactions_current_day", len(transactions_page)),
                    ("total_transactions_all_days", len(transactions_page)),
                    ("total_swaps_current_day", len(rows)),
                    ("total_swaps_all_days", len(rows)),
                ):
                    updated[key] += count
                write_checkpoint(updated)
                checkpoint = updated
                last_time = datetime.fromtimestamp(page_last_time, timezone.utc).strftime(
                    "%H:%M:%S"
                ) if page_last_time is not None else "N/A"
                if transactions_page:
                    print(
                        f"{day} | pág {checkpoint['page_number']} | tx {len(transactions_page)} | "
                        f"swaps {len(rows)} | total dia "
                        f"{checkpoint['total_transactions_current_day']}/"
                        f"{checkpoint['total_swaps_current_day']} | último {last_time}"
                    )
                    if checkpoint["page_number"] % 100 == 0:
                        print_day_summary(checkpoint, "Progresso")
                del rows, transactions_page, result, response_data, response
                if not checkpoint["day_exhausted"]:
                    time.sleep(PAGE_DELAY)
        # CSV is closed before committing completion and advancing the day.
        updated = dict(checkpoint)
        updated["completed_days"] = [*checkpoint["completed_days"], day]
        next_day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        updated["completed"] = next_day >= HISTORY_END
        if not updated["completed"]:
            updated.update(day_state(next_day))
        write_checkpoint(updated)
        print_day_summary(checkpoint, "Dia concluído")
        checkpoint = updated
    print(
        f"Histórico concluído | dias: {len(checkpoint['completed_days'])} | "
        f"tx: {checkpoint['total_transactions_all_days']} | "
        f"swaps: {checkpoint['total_swaps_all_days']}"
    )


def print_day_summary(checkpoint, label):
    transactions = checkpoint["total_transactions_current_day"]
    swaps = checkpoint["total_swaps_current_day"]
    useful = swaps / transactions * 100 if transactions else 0
    print(
        f"{label}: {checkpoint['current_date']} | tx: {transactions} | "
        f"swaps: {swaps} | útil: {useful:.2f}% | "
        f"tempo: {duration(checkpoint['elapsed_seconds_current_day'])}"
    )


if __name__ == "__main__":
    try:
        collect_stream()
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, ArithmeticError) as error:
        print(f"Coleta interrompida: {error}")
    except (KeyboardInterrupt, EOFError):
        print("Coleta interrompida; retome pelo último checkpoint confirmado.")
