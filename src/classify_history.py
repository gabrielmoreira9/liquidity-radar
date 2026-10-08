"""Daily threaded/resumable classification; diagnostic rules are unchanged.

CSV rows are authoritative, with per-day checkpoints for counters/timing.
Completed days are read-only. Transient failures retry the same signature with
exponential backoff; exhausted cycles pause 30s and repeat until recovery or
Ctrl+C. No network failure can produce UNKNOWN or advance the signature.
Workers own independent HTTP sessions and return results in completion order.
Only the main thread persists CSV/checkpoints; resume uses persisted signatures.
429 cooldown is shared and retries have jitter. Active workers include tasks
waiting on recovery. Shutdown cancels recovery waits; in-flight HTTP requests
may take up to TIMEOUT to return before their sessions are safely closed.
"""
import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import re
import threading
import time
from urllib.parse import quote, quote_plus

from dotenv import load_dotenv
import requests

from diagnostics import classify_pool_transaction


BASE_DIR = Path(__file__).resolve().parent.parent
SWAPS_DIR = BASE_DIR / "data/historical/swaps"
INPUT_PATH = BASE_DIR / "data/historical/swaps/sol_usdc_swaps_2026-06-30.csv"
OUTPUT_DIR = BASE_DIR / "data/historical/classified"
OUTPUT_PATH = OUTPUT_DIR / "sol_usdc_classified_2026-06-30.csv"
CHECKPOINT_PATH = OUTPUT_DIR / "classification_checkpoint.json"
CATEGORIES = ("SIMPLE_SWAP", "COMPOSITE_SWAP", "LIQUIDITY_OPERATION", "UNKNOWN")
COLUMNS = ["signature", "transaction_type"]
TIMEOUT = 30
MAX_RETRIES = 6
BACKOFF_BASE = 1
BACKOFF_MAX = 60
RECOVERY_DELAY = 30
MAX_WORKERS = 5
RETRY_JITTER = 0.5
RATE_LIMIT_SPACING = 0.1
PROGRESS_INTERVAL = 5.0
TEMPORARY_HTTP = {408, 425, 429, 500, 502, 503, 504}
TEMPORARY_RPC = {-32603, -32000, -32004, -32005, -32007, -32009, -32014, -32016}


class CollectionError(RuntimeError):
    """Provider diagnostics are allowed only after redacting credentials."""


def redact_credentials(message, api_key):
    """Keep provider text while masking raw/encoded keys and credential fields."""
    text = str(message)
    if api_key:
        variants = {api_key, quote(api_key, safe=""), quote_plus(api_key, safe=""),
                    json.dumps(api_key, ensure_ascii=True)[1:-1]}
        for secret in sorted(variants, key=len, reverse=True):
            text = text.replace(secret, "[redacted]")
        for encoded in (quote(api_key, safe=""), quote_plus(api_key, safe="")):
            if "%" in encoded:
                text = re.sub(re.escape(encoded), "[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r'''(?i)(api[-_]?key["']?\s*(?:=|:)\s*["']?)([^\s&"'<>]+)''',
                  r"\1[redacted]", text)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)([^\s,\"'<>]+)",
                  r"\1[redacted]", text)
    return text


def request_diagnostic(signature, api_key, http_code=None, rpc_code=None,
                       message="Mensagem indisponível", temporary=False):
    """Escape controls/newlines so provider messages cannot forge log entries."""
    safe_message = json.dumps(redact_credentials(message, api_key), ensure_ascii=True)
    details = (f"{'TEMPORÁRIO' if temporary else 'PERMANENTE'} | signature {signature} | "
               f"HTTP {http_code if http_code is not None else 'N/A'} | "
               f"RPC {rpc_code if rpc_code is not None else 'N/A'} | mensagem Helius: {safe_message}")
    return redact_credentials(details, api_key)


class WorkerStopped(Exception):
    """Cooperative cancellation; this signature remains unpersisted/pending."""


class WorkerRuntime:
    """Thread-safe counters, per-thread sessions and shared 429 request gate."""

    def __init__(self, counters):
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.local = threading.local()
        self.sessions = []
        self.counters = dict(counters)
        self.active = 0
        self.next_request_at = 0.0
        self.throttled = False

    def record(self, field):
        with self.lock:
            self.counters[field] = self.counters.get(field, 0) + 1

    def snapshot(self):
        with self.lock:
            return dict(self.counters), self.active

    def check_stop(self):
        if self.stop.is_set():
            raise WorkerStopped()

    def pause(self, seconds):
        if self.stop.wait(seconds):
            raise WorkerStopped()

    def rate_limit(self, seconds):
        with self.lock:
            self.throttled = True
            self.next_request_at = max(self.next_request_at, time.monotonic() + seconds)

    def before_request(self):
        while True:
            self.check_stop()
            with self.lock:
                now = time.monotonic()
                delay = max(0.0, self.next_request_at - now)
                if delay == 0:
                    if self.throttled:
                        self.next_request_at = now + RATE_LIMIT_SPACING
                    return
            self.pause(delay)

    def session(self):
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            with self.lock:
                self.sessions.append(self.local.session)
        return self.local.session

    def close(self):
        # Called by main only after executor shutdown: sessions are no longer used.
        for session in self.sessions:
            session.close()


def output_path(day):
    return OUTPUT_DIR / f"sol_usdc_classified_{day}.csv"


def checkpoint_path(day):
    return OUTPUT_DIR / f"classification_checkpoint_{day}.json"


def discover_days():
    days = []
    prefix = "sol_usdc_swaps_"
    for path in sorted(SWAPS_DIR.glob(f"{prefix}????-??-??.csv")):
        day = path.stem[len(prefix):]
        try:
            parsed = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise CollectionError(f"Data inválida no nome do CSV: {path.name}.") from None
        if parsed.strftime("%Y-%m-%d") != day or not path.is_file():
            raise CollectionError(f"Arquivo diário inválido: {path.name}.")
        days.append((day, path))
    if not days:
        raise CollectionError("Nenhum CSV diário sol_usdc_swaps_YYYY-MM-DD.csv encontrado.")
    return days


def duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def read_signatures(input_path=None):
    # Only signatures are kept in memory, never transaction payloads.
    signatures = []
    seen = set()
    with (input_path or INPUT_PATH).open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if "signature" not in (reader.fieldnames or []):
            raise CollectionError("CSV de entrada sem coluna signature.")
        for row in reader:
            signature = (row.get("signature") or "").strip()
            if not signature:
                raise CollectionError("Signature vazia no CSV de entrada.")
            if signature not in seen:
                signatures.append(signature)
                seen.add(signature)
    return signatures


def read_completed(path=None):
    """CSV is authoritative; ignore only an interrupted, non-newline-terminated tail."""
    completed = {}
    offset = 0
    path = path or OUTPUT_PATH
    if not path.exists():
        return completed, offset
    with path.open("rb") as source:
        header = source.readline()
        if not header or not header.endswith(b"\n"):
            return completed, offset
        if next(csv.reader([header.decode("utf-8")])) != COLUMNS:
            raise CollectionError("Cabeçalho inválido no CSV classificado; arquivo preservado.")
        offset = source.tell()
        while True:
            line = source.readline()
            if not line or not line.endswith(b"\n"):
                break
            fields = next(csv.reader([line.decode("utf-8")]))
            if len(fields) != 2 or not fields[0] or fields[1] not in CATEGORIES:
                raise CollectionError("Linha inválida no CSV classificado; arquivo preservado.")
            signature, category = fields
            if signature in completed:
                raise CollectionError("Signature duplicada/conflitante no CSV; arquivo preservado.")
            completed[signature] = category
            offset = source.tell()
    return completed, offset


def read_checkpoint(input_path=None, path=None):
    input_path = input_path or INPUT_PATH
    path = path or CHECKPOINT_PATH
    legacy = False
    if not path.exists() and CHECKPOINT_PATH.exists():
        path = CHECKPOINT_PATH
        legacy = True
    if not path.exists():
        return {}
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(checkpoint, dict):
            raise ValueError()
        if checkpoint.get("input_path") != str(input_path):
            if legacy:
                return {}  # Legacy checkpoint belongs to another completed day.
            raise CollectionError("Checkpoint pertence a outro dataset; arquivo preservado.")
        for field in ("elapsed_seconds", "retries", "failures", "http_429"):
            value = checkpoint.get(field, 0)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError()
        return checkpoint
    except (ValueError, TypeError):
        print("Checkpoint inválido: retomada pelo CSV; contadores históricos indisponíveis.")
        return {}


def save_checkpoint(state, path=None):
    path = path or CHECKPOINT_PATH
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(state, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def fetch_transaction(session, signature, api_key, counters, runtime=None):
    """Retry the SAME signature indefinitely on transient failures, never UNKNOWN."""
    cycle = 1
    payload_request = {
        "jsonrpc": "2.0", "id": signature, "method": "getTransaction",
        "params": [signature, {"encoding": "jsonParsed", "commitment": "finalized",
                               "maxSupportedTransactionVersion": 0}],
    }
    def record(field):
        counters[field] = counters.get(field, 0) + 1
        if runtime is not None:
            runtime.record(field)

    def pause(delay):
        if runtime is None:
            time.sleep(delay)
        else:
            runtime.pause(delay)

    while True:
        for attempt in range(MAX_RETRIES + 1):
            retry_after = 0
            reason = None
            limited = False
            if runtime is not None:
                runtime.before_request()
            try:
                with session.post(
                    "https://mainnet.helius-rpc.com/", params={"api-key": api_key},
                    json=payload_request, timeout=TIMEOUT,
                ) as response:
                    http_code = response.status_code
                    json_error = None
                    try:
                        payload = response.json()
                    except ValueError as error:
                        payload = None
                        json_error = error
                    rpc_error = payload.get("error") if isinstance(payload, dict) else None
                    rpc_code = rpc_error.get("code") if isinstance(rpc_error, dict) else None
                    if isinstance(rpc_error, dict) and rpc_error.get("message") is not None:
                        original_message = rpc_error["message"]
                    elif rpc_error is not None:
                        original_message = rpc_error
                    elif isinstance(payload, dict) and payload.get("message") is not None:
                        original_message = payload["message"]
                    else:
                        original_message = getattr(response, "text", "") or "Mensagem não fornecida pela Helius"
                    if response.status_code in TEMPORARY_HTTP or 500 <= response.status_code <= 599:
                        reason = request_diagnostic(signature, api_key, http_code, rpc_code, original_message, temporary=True)
                        limited = response.status_code == 429
                        if limited:
                            record("http_429")
                        try:
                            delay = float(response.headers.get("Retry-After", 0))
                            if math.isfinite(delay):
                                retry_after = max(0, delay)
                        except (ValueError, TypeError):
                            pass
                    elif response.status_code != 200:
                        record("failures")
                        raise CollectionError(request_diagnostic(signature, api_key, http_code, rpc_code, original_message))
                    else:
                        if json_error is not None:
                            reason = request_diagnostic(signature, api_key, http_code, message=f"JSON inválido: {original_message}", temporary=True)
                        elif not isinstance(payload, dict):
                            reason = request_diagnostic(signature, api_key, http_code, message=f"Resposta RPC inesperada: {original_message}", temporary=True)
                        elif rpc_error is not None:
                            # Unknown/malformed codes are not silently added to the
                            # recoverable allowlist: expose the provider's reason.
                            if type(rpc_code) is not int or rpc_code not in TEMPORARY_RPC:
                                record("failures")
                                raise CollectionError(request_diagnostic(signature, api_key, http_code, rpc_code, original_message))
                            reason = request_diagnostic(signature, api_key, http_code, rpc_code, original_message, temporary=True)
                        elif not isinstance(payload.get("result"), dict):
                            reason = request_diagnostic(signature, api_key, http_code, message="Transação indisponível", temporary=True)
                        else:
                            transaction = payload["result"]
                            body = transaction.get("transaction")
                            returned = body.get("signatures", []) if isinstance(body, dict) else []
                            if not returned or returned[0] != signature or not isinstance(transaction.get("meta"), dict):
                                reason = request_diagnostic(signature, api_key, http_code, message="Detalhes incompletos ou signature divergente", temporary=True)
                            else:
                                return transaction
            except requests.exceptions.SSLError as error:
                record("failures")
                raise CollectionError(request_diagnostic(signature, api_key, message=f"Erro TLS/certificado: {error}")) from None
            except requests.Timeout as error:
                reason = request_diagnostic(signature, api_key, message=f"Timeout: {error}", temporary=True)
            except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as error:
                reason = request_diagnostic(signature, api_key, message=f"Falha de conexão: {error}", temporary=True)
            except requests.RequestException as error:
                record("failures")
                raise CollectionError(request_diagnostic(signature, api_key, message=f"Erro não recuperável de requisição: {error}")) from None
            record("failures")
            record("retries")
            base_delay = RECOVERY_DELAY if attempt == MAX_RETRIES else min(BACKOFF_MAX, BACKOFF_BASE * 2 ** attempt)
            delay = max(retry_after, base_delay)
            if runtime is not None:
                delay += random.uniform(0, RETRY_JITTER)
                if limited:
                    runtime.rate_limit(delay)
            if attempt == MAX_RETRIES:
                break
            print(f"Ciclo {cycle} | retry {attempt + 1}/{MAX_RETRIES} | {signature} | {reason} | espera {delay:.0f}s", flush=True)
            pause(delay)
        print(f"{signature} | ciclo {cycle} esgotado ({reason}); espera {RECOVERY_DELAY}s e tenta a MESMA signature. Ctrl+C interrompe.", flush=True)
        pause(delay)
        cycle += 1


def classify_signature(signature, api_key, runtime):
    """Worker: fetch/classify only. Never opens CSVs or saves checkpoints."""
    runtime.check_stop()
    with runtime.lock:
        runtime.active += 1
    try:
        transaction = fetch_transaction(runtime.session(), signature, api_key, {}, runtime)
        runtime.check_stop()
        try:
            category = classify_pool_transaction(transaction)["category"]
        except (KeyError, ValueError, TypeError, IndexError, AttributeError):
            # A fetched transaction with insufficient structure is UNKNOWN.
            category = "UNKNOWN"
        if category not in CATEGORIES:
            raise CollectionError("Categoria inválida retornada pelo diagnóstico.")
        return signature, category
    finally:
        with runtime.lock:
            runtime.active -= 1


def print_progress(total, counts, elapsed, speed, label="Classificação", retries=0, http_429=0, active=0):
    done = sum(counts.values())
    percent = done / total * 100 if total else 100
    eta = duration((total - done) / speed) if speed > 0 else "N/A"
    print(
        f"{label} | classificadas {done} / {total} | SIMPLE {counts['SIMPLE_SWAP']} | "
        f"COMPOSITE {counts['COMPOSITE_SWAP']} | LIQUIDITY {counts['LIQUIDITY_OPERATION']} | "
        f"UNKNOWN {counts['UNKNOWN']} | tempo {duration(elapsed)} | "
        f"{percent:.2f}% | {speed:.2f} tx/s | retries {retries} | HTTP 429 {http_429} | "
        f"ETA {eta} | workers ativos {active}", flush=True,
    )


def classify_day(job, api_key, global_counts, global_total, run, workers=MAX_WORKERS):
    day, input_path, path, cp_path = job["day"], job["input_path"], job["output_path"], job["checkpoint_path"]
    signatures, completed, offset = job["signatures"], job["completed"], job["offset"]
    previous = job["previous"]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    counts = job["counts"]
    counters = {field: previous.get(field, 0) for field in ("retries", "failures", "http_429")}
    runtime = WorkerRuntime(counters)
    elapsed_before = previous.get("elapsed_seconds", 0)
    started = time.monotonic()
    processed_this_run = 0
    last_signature = previous.get("last_signature")
    state = {}

    def checkpoint(finished=False):
        counters.update(runtime.snapshot()[0])
        state.update({
            "day": day, "input_path": str(input_path), "output_path": str(path),
            "total_signatures": len(signatures), "classified": sum(counts.values()),
            "counts": {category: counts[category] for category in CATEGORIES},
            "last_signature": last_signature, "csv_offset": offset,
            "elapsed_seconds": elapsed_before + time.monotonic() - started,
            **counters, "workers": workers, "completed": finished,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        save_checkpoint(state, cp_path)

    def report_progress():
        current, active = runtime.snapshot()
        counters.update(current)
        job.update(current)
        run_elapsed = time.monotonic() - started
        print_progress(len(signatures), counts, elapsed_before + run_elapsed,
                       processed_this_run / run_elapsed if run_elapsed else 0, label=f"Dia {day}",
                       retries=counters["retries"], http_429=counters["http_429"], active=active)
        total_elapsed = time.monotonic() - run["started"]
        print_progress(global_total, global_counts, total_elapsed,
                       run["processed"] / total_elapsed if total_elapsed else 0, label="Global",
                       retries=sum(item["retries"] for item in run["jobs"]),
                       http_429=sum(item["http_429"] for item in run["jobs"]), active=active)

    try:
        # Recover an interrupted row append. Complete CSV rows are always retained,
        # even if the checkpoint write was interrupted immediately afterwards.
        with path.open("r+b" if path.exists() else "w+b") as output:
            output.truncate(offset)
        with path.open("a", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=COLUMNS)
            if offset == 0:
                writer.writeheader()
                output.flush()
                os.fsync(output.fileno())
                offset = os.fstat(output.fileno()).st_size
            checkpoint()
            pending = iter(signature for signature in signatures if signature not in completed)
            executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"classify-{day}")
            futures = {}
            def submit_next():
                signature = next(pending, None)
                if signature is not None:
                    futures[executor.submit(classify_signature, signature, api_key, runtime)] = signature
            try:
                for _ in range(workers):
                    submit_next()
                last_report = time.monotonic()
                while futures:
                    ready, _ = wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)
                    for future in ready:
                        expected = futures.pop(future)
                        signature, category = future.result()
                        if signature != expected or signature in completed:
                            raise CollectionError("Resultado divergente/duplicado; signature não será gravada.")
                        writer.writerow({"signature": signature, "transaction_type": category})
                        output.flush()
                        os.fsync(output.fileno())
                        offset = os.fstat(output.fileno()).st_size
                        completed[signature] = category
                        counts[category] += 1
                        global_counts[category] += 1
                        processed_this_run += 1
                        run["processed"] += 1
                        last_signature = signature
                        checkpoint()
                        submit_next()
                    now = time.monotonic()
                    if now - last_report >= PROGRESS_INTERVAL or (ready and sum(counts.values()) % 100 == 0):
                        report_progress()
                        last_report = now
            finally:
                runtime.stop.set()
                for future in futures:
                    future.cancel()
                executor.shutdown(wait=True, cancel_futures=True)
                runtime.close()
            checkpoint(finished=True)
    except (KeyboardInterrupt, CollectionError):
        checkpoint()
        raise
    job["elapsed_seconds"] = elapsed_before + time.monotonic() - started
    job.update(counters)
    job["finished"] = True
    print_day_summary(job)


def print_day_summary(job):
    counts = job["counts"]
    total = len(job["signatures"])
    print(f"Dia {job['day']} | total analisado: {sum(counts.values())}", flush=True)
    for category in CATEGORIES:
        percentage = counts[category] / total * 100 if total else 0
        print(f"{category}: {counts[category]} ({percentage:.2f}%)")
    print(f"Tempo acumulado: {duration(job['elapsed_seconds'])} | retries: {job['retries']} | "
          f"HTTP 429: {job['http_429']} | falhas: {job['failures']}", flush=True)


def main(validate_only=False, workers=MAX_WORKERS):
    if type(workers) is not int or workers < 1:
        raise CollectionError("workers deve ser um inteiro positivo.")
    # Preflight all daily files before opening sessions or modifying outputs.
    jobs = []
    global_counts = Counter()
    for day, input_path in discover_days():
        path, cp_path = output_path(day), checkpoint_path(day)
        signatures = read_signatures(input_path)
        completed, offset = read_completed(path)
        if not set(completed) <= set(signatures):
            raise CollectionError(f"Dia {day}: CSV classificado contém signatures de outro dataset; arquivos preservados.")
        previous = read_checkpoint(input_path, cp_path)
        counts = Counter(completed.values())
        finished = path.exists() and offset > 0 and len(completed) == len(signatures)
        jobs.append(dict(day=day, input_path=input_path, output_path=path, checkpoint_path=cp_path,
                         signatures=signatures, completed=completed, offset=offset, previous=previous,
                         counts=counts, finished=finished, elapsed_seconds=previous.get("elapsed_seconds", 0),
                         retries=previous.get("retries", 0), failures=previous.get("failures", 0),
                         http_429=previous.get("http_429", 0)))
        global_counts.update(counts)
        print(f"Dia {day}: {len(signatures)} signatures únicas | já classificadas: {len(completed)} | "
              f"pendentes: {len(signatures)-len(completed)}" + (" | 100% completo; será pulado." if finished else ""), flush=True)
    global_total = sum(len(job["signatures"]) for job in jobs)
    print_progress(global_total, global_counts, 0, 0, label="Global inicial",
                   retries=sum(job["retries"] for job in jobs), http_429=sum(job["http_429"] for job in jobs))
    print(f"Workers configurados: {workers}; CSV/checkpoint gravados exclusivamente pela thread principal.", flush=True)
    if validate_only:
        print("Inicialização de todos os dias validada. Nenhuma consulta à API ou escrita de resultados.")
        return
    pending = [job for job in jobs if not job["finished"]]
    api_key = None
    if pending:
        load_dotenv(BASE_DIR / ".env")
        api_key = os.getenv("HELIUS_API_KEY")
        if not api_key:
            raise CollectionError("HELIUS_API_KEY ausente no ambiente/.env.")
    run = {"started": time.monotonic(), "processed": 0, "jobs": jobs}
    for job in jobs:
        if job["finished"]:
            print(f"Dia {job['day']}: concluído; CSV e checkpoint preservados integralmente.", flush=True)
            print_day_summary(job)
        else:
            print(f"Iniciando/retomando dia {job['day']}.", flush=True)
            classify_day(job, api_key, global_counts, global_total, run, workers)
        elapsed = time.monotonic() - run["started"]
        print_progress(global_total, global_counts, elapsed,
                       run["processed"] / elapsed if elapsed else 0, label="Global",
                       retries=sum(item["retries"] for item in jobs), http_429=sum(item["http_429"] for item in jobs))
    print(f"Resumo agregado: {len(jobs)} dias | total analisado: {sum(global_counts.values())}")
    for category in CATEGORIES:
        percentage = global_counts[category] / global_total * 100 if global_total else 0
        print(f"{category}: {global_counts[category]} ({percentage:.2f}%)")
    print(f"Tempo desta execução: {duration(time.monotonic()-run['started'])} | "
          f"tempo acumulado dos dias: {duration(sum(job['elapsed_seconds'] for job in jobs))}")
    print(f"Retries acumulados: {sum(job['retries'] for job in jobs)} | falhas: {sum(job['failures'] for job in jobs)}")
    print(f"HTTP 429 acumulados: {sum(job['http_429'] for job in jobs)} | workers configurados: {workers}")
    print("Falhas contam tentativas de requisição malsucedidas, incluindo as recuperadas por retry.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true", help="Validate startup without API calls or writes")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Concurrent I/O workers (default: 5)")
    arguments = parser.parse_args()
    try:
        main(validate_only=arguments.validate_only, workers=arguments.workers)
    except KeyboardInterrupt:
        print("Interrompido com Ctrl+C. Retome com o mesmo comando; CSV confirmado será preservado.")
        raise SystemExit(130)
    except (CollectionError, OSError, UnicodeError, csv.Error) as error:
        # Exception classes for I/O avoid leaking data from arbitrary response text.
        message = str(error) if isinstance(error, CollectionError) else type(error).__name__
        print(f"Classificação interrompida: {message}. Retome pelo CSV existente.")
        raise SystemExit(1)
