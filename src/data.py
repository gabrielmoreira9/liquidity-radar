"""Incremental daily backfill with durable, period-independent checkpoints.

HISTORY_END is exclusive, in UTC. Complete days are read-only and skipped;
incomplete days resume their own pagination token. CSVs are append-only:
missing checkpoints, inconsistent offsets or source metadata stop collection
without deletion/truncation. Legacy daily and global checkpoints are supported
without changing their original files merely to change the requested period.
Transient requests retry the identical payload with exponential backoff, then
pause 30 seconds and start another recovery cycle. No cursor or CSV changes
occur until a valid page is received. Cycles continue until recovery or Ctrl+C.
CLI exit codes: 0 full completion, 1 permanent error, 130 user interruption.
"""
import csv
import json
import math
import os
import sys
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
HISTORY_START = "2026-07-01"
HISTORY_END = "2026-07-04"  # Exclusive UTC boundary.
LIMIT = 100
REQUEST_TIMEOUT = 30
PAGE_DELAY = 0.25
REQUEST_ATTEMPTS = 5  # Initial attempt plus four retries per recovery cycle.
BACKOFF_INITIAL = 1.0
BACKOFF_MAX = 30.0
RECOVERY_DELAY = 30.0
# JSON-RPC internal/server-unavailable and node-unhealthy responses.
TEMPORARY_RPC_CODES = {-32603, -32005, 429}
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


def checkpoint_path(day):
    day_state(day)  # Validate the date before constructing a filename.
    return OUTPUT_DIR / f"backfill_checkpoint_{day}.json"


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
    if start.strftime("%Y-%m-%d") != day:
        raise ValueError("Dia deve estar no formato YYYY-MM-DD.")
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
    path = checkpoint_path(checkpoint["current_date"])
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(checkpoint, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _read_checkpoint(path):
    try:
        with path.open(encoding="utf-8") as source:
            checkpoint = json.load(source)
        if not isinstance(checkpoint, dict):
            raise ValueError("objeto JSON inválido")
        return checkpoint
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError(f"Checkpoint inválido ({path.name}); arquivos preservados: {error}") from None


def _validate_checkpoint(checkpoint, day):
    try:
        expected = day_state(day)
        if not set(expected) <= checkpoint.keys() or checkpoint["current_date"] != day:
            raise ValueError("campos ausentes ou dia diferente")
        for key in ("current_start_timestamp", "current_end_timestamp"):
            if checkpoint[key] != expected[key]:
                raise ValueError("intervalo diário inválido")
        for key in ("page_number", "total_transactions_current_day", "total_swaps_current_day", "csv_offset"):
            value = checkpoint[key]
            if key == "total_transactions_current_day" and value is None and checkpoint.get("legacy_completion_only"):
                continue  # Old global files recorded no per-day transaction count.
            if type(value) is not int or value < 0:
                raise ValueError(f"contador inválido: {key}")
        if type(checkpoint["day_exhausted"]) is not bool:
            raise ValueError("day_exhausted inválido")
        token = checkpoint["paginationToken"]
        if token is not None and (not isinstance(token, str) or not token):
            raise ValueError("paginationToken inválido")
        if checkpoint["day_exhausted"] and token is not None:
            raise ValueError("dia concluído com token de continuação")
        if not checkpoint["day_exhausted"] and checkpoint["page_number"] > 0 and not token:
            raise ValueError("dia incompleto sem token de continuação")
        transactions = checkpoint["total_transactions_current_day"]
        if transactions is not None and checkpoint["total_swaps_current_day"] > transactions:
            raise ValueError("mais swaps que transações")
        last = checkpoint["last_block_time"]
        if last is not None and (type(last) is not int or not expected["current_start_timestamp"] <= last < expected["current_end_timestamp"]):
            raise ValueError("last_block_time fora do dia")
        elapsed = checkpoint["elapsed_seconds_current_day"]
        if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("duração inválida")
        if checkpoint["csv_offset"] == 0 and any((checkpoint["page_number"], transactions,
                                                  checkpoint["total_swaps_current_day"], last,
                                                  checkpoint["day_exhausted"])):
            raise ValueError("progresso confirmado sem bytes CSV")
        for key, value in (("pool_owner", POOL_OWNER), ("usdc_mint", USDC_MINT), ("sol_mint", SOL_MINT)):
            if key in checkpoint and checkpoint[key] != value:
                raise ValueError(f"origem diferente: {key}")
        # Old history_start/end describe the old run, not daily validity.
        fields = (*expected, "pool_owner", "usdc_mint", "sol_mint", "legacy_completion_only")
        return {key: checkpoint[key] for key in fields if key in checkpoint}
    except (ValueError, TypeError, KeyError) as error:
        raise RuntimeError(f"Checkpoint inválido para {day}; arquivos preservados: {error}") from None


def _legacy_completed_checkpoint(day):
    """Recover only a completed-day marker from an old multi-day checkpoint."""
    path = csv_path(day)
    if not path.exists():
        raise RuntimeError(f"Dia {day} marcado concluído, mas CSV ausente; arquivos preservados.")
    state = day_state(day)
    count = 0
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != CSV_COLUMNS:
            raise RuntimeError(f"Cabeçalho inválido para {day}; arquivos preservados.")
        for row in reader:
            timestamp = datetime.fromisoformat(row["timestamp"])
            if timestamp.tzinfo is None or not state["current_start_timestamp"] <= timestamp.timestamp() < state["current_end_timestamp"]:
                raise RuntimeError(f"CSV fora do dia {day}; arquivos preservados.")
            if None in row or any(row[key] is None for key in CSV_COLUMNS):
                raise RuntimeError(f"Linha CSV incompleta para {day}; arquivos preservados.")
            count += 1
    state.update(day_exhausted=True, csv_offset=path.stat().st_size,
                 total_transactions_current_day=None, total_swaps_current_day=count,
                 legacy_completion_only=True)
    return state


def load_checkpoint(day=None):
    day = HISTORY_START if day is None else day
    path = checkpoint_path(day)
    if path.exists():
        return _validate_checkpoint(_read_checkpoint(path), day)
    legacy_path = OUTPUT_DIR / "backfill_checkpoint.json"
    if legacy_path.exists():
        legacy = _read_checkpoint(legacy_path)
        if legacy.get("current_date") == day:
            return _validate_checkpoint(legacy, day)
        if day in legacy.get("completed_days", []):
            return _validate_checkpoint(_legacy_completed_checkpoint(day), day)
    return None


def validate_csv(checkpoint):
    """Check consistency read-only. Unconfirmed bytes are never discarded."""
    day = checkpoint["current_date"]
    path = csv_path(day)
    offset = checkpoint["csv_offset"]
    if not path.exists():
        if offset or checkpoint["day_exhausted"]:
            raise RuntimeError(f"CSV ausente para {day}, mas checkpoint confirma dados; arquivos preservados.")
        return
    if path.stat().st_size != offset:
        raise RuntimeError(
            f"Conflito em {day}: CSV tem {path.stat().st_size} bytes, checkpoint confirma {offset}. "
            "Pode haver uma página não confirmada ou edição externa. "
            "Revise CSV/checkpoint antes de retomar; nenhum arquivo será apagado ou truncado."
        )
    if offset == 0:
        if checkpoint["day_exhausted"]:
            raise RuntimeError(f"CSV concluído vazio/sem cabeçalho para {day}; arquivos preservados.")
        return
    with path.open(newline="", encoding="utf-8") as source:
        if next(csv.reader(source), None) != CSV_COLUMNS:
            raise RuntimeError(f"Cabeçalho CSV incompatível para {day}; arquivos preservados.")
    with path.open("rb") as source:
        source.seek(-1, os.SEEK_END)
        if source.read(1) != b"\n":
            raise RuntimeError(f"CSV com linha final incompleta para {day}; arquivos preservados.")


def requested_days():
    start = datetime.strptime(HISTORY_START, "%Y-%m-%d")
    end = datetime.strptime(HISTORY_END, "%Y-%m-%d")
    if start.strftime("%Y-%m-%d") != HISTORY_START or end.strftime("%Y-%m-%d") != HISTORY_END or start >= end:
        raise ValueError("HISTORY_START deve ser anterior a HISTORY_END, em YYYY-MM-DD (UTC).")
    return [(start + timedelta(days=index)).strftime("%Y-%m-%d") for index in range((end-start).days)]


def duration(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class TemporaryHeliusError(RuntimeError):
    """Retryable failure without any change to committed pagination state."""


def _safe_message(message, api_key):
    return str(message).replace(api_key, "[redacted]").strip()[:200]


def _request_page_once(url, payload, api_key):
    response = None
    try:
        try:
            response = requests.post(
                url, json=payload, headers={"Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            response_data = response.json()
        except requests.HTTPError:
            status = response.status_code
            message = _safe_message(response.text or "", api_key)
            if status == 429 or 500 <= status <= 599:
                raise TemporaryHeliusError(f"HTTP temporário {status}: {message}") from None
            raise RuntimeError(f"Erro HTTP permanente {status}: {message}") from None
        except requests.exceptions.SSLError:
            raise RuntimeError("Erro TLS/certificado na conexão à Helius; verifique a configuração.") from None
        except requests.Timeout:
            raise TemporaryHeliusError("Timeout na requisição à Helius.") from None
        except requests.ConnectionError:
            raise TemporaryHeliusError("Erro temporário de conexão com a Helius.") from None
        except requests.RequestException:
            raise RuntimeError("Erro não recuperável na requisição à Helius; verifique URL/configuração.") from None
        except ValueError:
            raise RuntimeError("Resposta JSON inválida da Helius.") from None
        if isinstance(response_data, dict) and response_data.get("error") is not None:
            error = response_data["error"]
            if not isinstance(error, dict):
                raise RuntimeError("Resposta de erro JSON-RPC inválida da Helius.")
            code = error.get("code")
            message = _safe_message(error.get("message", "Erro sem descrição"), api_key)
            if isinstance(code, int) and (code in TEMPORARY_RPC_CODES or 500 <= code <= 599):
                raise TemporaryHeliusError(f"Erro JSON-RPC temporário {code}: {message}")
            raise RuntimeError(f"Erro JSON-RPC não recuperável {code}: {message}")
        return response_data
    finally:
        if response is not None:
            response.close()


def request_page(url, payload, api_key, day, page_number):
    """Retry only the request; never advance tokens or re-append a page."""
    cycle = 1
    while True:
        for attempt in range(1, REQUEST_ATTEMPTS + 1):
            try:
                return _request_page_once(url, payload, api_key)
            except TemporaryHeliusError as error:
                print(
                    f"{day} | pág {page_number} | recuperação {cycle} | "
                    f"tentativa {attempt}/{REQUEST_ATTEMPTS}: {error}", flush=True,
                )
                if attempt < REQUEST_ATTEMPTS:
                    delay = min(BACKOFF_MAX, BACKOFF_INITIAL * 2 ** (attempt - 1))
                    print(f"Nova tentativa da MESMA página em {delay:g}s.", flush=True)
                    time.sleep(delay)
        print(
            f"{day} | pág {page_number}: tentativas esgotadas; checkpoint preservado. "
            f"Aguardando {RECOVERY_DELAY:g}s para outro ciclo da MESMA página (Ctrl+C interrompe).",
            flush=True,
        )
        time.sleep(RECOVERY_DELAY)
        cycle += 1


def collect_stream():
    # Validate the whole selected interval before writing files or making requests.
    plan = []
    for day in requested_days():
        checkpoint = load_checkpoint(day)
        if checkpoint is None:
            if csv_path(day).exists():
                raise RuntimeError(
                    f"CSV existente para {day} sem checkpoint correspondente. "
                    "Não é possível comprovar conclusão ou cursor de retomada; arquivos preservados."
                )
            checkpoint = {**day_state(day), "pool_owner": POOL_OWNER,
                          "usdc_mint": USDC_MINT, "sol_mint": SOL_MINT}
        else:
            validate_csv(checkpoint)
        plan.append(checkpoint)
    pending = [checkpoint for checkpoint in plan if not checkpoint["day_exhausted"]]
    for checkpoint in plan:
        if checkpoint["day_exhausted"]:
            print(f"{checkpoint['current_date']} já completo; CSV preservado, coleta pulada.")
    if not pending:
        print("Todos os dias solicitados já estão completos.")
        return
    api_key = os.getenv("HELIUS_API_KEY")
    if not api_key:
        raise RuntimeError("A variável HELIUS_API_KEY não está definida.")
    SWAPS_DIR.mkdir(parents=True, exist_ok=True)
    url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    for checkpoint in pending:
        day = checkpoint["current_date"]
        path = csv_path(day)
        validate_csv(checkpoint)
        write_checkpoint(checkpoint)
        offset = checkpoint["csv_offset"]
        started_at = time.monotonic()
        elapsed_before = checkpoint["elapsed_seconds_current_day"]
        if checkpoint["page_number"]:
            print(f"Retomando {day} | próxima página {checkpoint['page_number'] + 1} | checkpoint diário.")
        with path.open("a", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
            if offset == 0:
                writer.writeheader()
                output.flush()
                os.fsync(output.fileno())
                checkpoint = dict(checkpoint, csv_offset=os.fstat(output.fileno()).st_size)
                write_checkpoint(checkpoint)
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
                response_data = request_page(url, payload, api_key, day, checkpoint["page_number"] + 1)

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
                    ("total_swaps_current_day", len(rows)),
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
                del rows, transactions_page, result, response_data
                if not checkpoint["day_exhausted"]:
                    time.sleep(PAGE_DELAY)
        print_day_summary(checkpoint, "Dia concluído")
    print(f"Período concluído | dias solicitados: {len(plan)} | já completos: {len(plan)-len(pending)} | coletados/retomados: {len(pending)}")


def print_day_summary(checkpoint, label):
    transactions = checkpoint["total_transactions_current_day"]
    swaps = checkpoint["total_swaps_current_day"]
    useful = swaps / transactions * 100 if transactions else 0
    print(
        f"{label}: {checkpoint['current_date']} | tx: {transactions} | "
        f"swaps: {swaps} | útil: {useful:.2f}% | "
        f"tempo: {duration(checkpoint['elapsed_seconds_current_day'])}"
    )


def main():
    try:
        collect_stream()
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, ArithmeticError) as error:
        print(f"Coleta interrompida: {error} Último checkpoint confirmado preservado.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Coleta interrompida por Ctrl+C; retome pelo último checkpoint confirmado.", file=sys.stderr)
        return 130
    except EOFError:
        print("Coleta interrompida; último checkpoint confirmado preservado.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
