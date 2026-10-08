"""Diagnostics without dataset changes; optional individual Helius lookups."""
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
SWAPS_PATH = BASE_DIR / "data/historical/swaps/sol_usdc_swaps_2026-06-30.csv"
LIQUIDITY_PATH = BASE_DIR / "data/processed/liquidity_1m.csv"
RTOL = 1e-9
ATOL = 1e-8


def show(title, frame):
    print(f"\n{title}")
    print(frame.to_string(index=False, float_format=lambda value: f"{value:.12g}"))


def main():
    swaps = pd.read_csv(SWAPS_PATH)
    windows = pd.read_csv(LIQUIDITY_PATH)
    for frame in (swaps, windows):
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame.sort_values("timestamp", kind="stable", inplace=True)
        frame.reset_index(drop=True, inplace=True)
    print(f"Swaps: {len(swaps)} | janelas: {len(windows)}")
    print(f"Duplicatas exatas presentes (preservadas): {swaps.duplicated().sum()}")
    print("Empates de timestamp preservam a ordem original do CSV.")
    print("\nEstatísticas descritivas de price:")
    print(swaps["price"].describe(percentiles=[0.01, 0.05, 0.5, 0.95, 0.99]).to_string())

    swaps["previous_price"] = swaps["price"].shift(1)
    swaps["absolute_price_change"] = (swaps["price"] - swaps["previous_price"]).abs()
    with np.errstate(divide="ignore", invalid="ignore"):
        swaps["log_return"] = np.log(swaps["price"] / swaps["previous_price"])
        expected_price = (swaps["usdc_delta"] / swaps["sol_delta"]).abs()
    consistent = (
        np.isfinite(swaps["price"]) & np.isfinite(expected_price)
        & np.isclose(swaps["price"], expected_price, rtol=RTOL, atol=ATOL)
    )
    print(
        f"\nConsistência price ≈ abs(usdc_delta / sol_delta): "
        f"{int((~consistent).sum())} linhas inconsistentes | rtol={RTOL} | atol={ATOL}"
    )
    show(
        "20 maiores mudanças absolutas de preço consecutivas (abs(price - previous_price)):",
        swaps.nlargest(20, "absolute_price_change")[[
            "timestamp", "signature", "direction", "price", "previous_price",
            "absolute_price_change", "log_return", "usdc_delta", "sol_delta", "volume_usdc",
        ]],
    )
    top_windows = windows.nlargest(20, "volatility")
    show("20 janelas com maior volatility:", top_windows[[
        "timestamp", "swap_count", "volume_usdc", "price_open", "price_close",
        "price_min", "price_max", "volatility",
    ]])
    for start in top_windows.head(5)["timestamp"]:
        end = start + pd.Timedelta(minutes=1)
        selected = swaps.loc[(swaps["timestamp"] >= start) & (swaps["timestamp"] < end)]
        returns = np.log(selected["price"] / selected["price"].shift(1)).dropna()
        calculated = returns.std() if len(returns) > 1 else np.nan
        saved = windows.loc[windows["timestamp"] == start, "volatility"].iloc[0]
        show(f"Todos os swaps da janela [{start}, {end}):", selected[[
            "timestamp", "signature", "price", "usdc_delta", "sol_delta", "direction",
        ]])
        print(f"Volatility recalculada: {calculated:.12g} | salva: {saved:.12g}")

    print("\nFórmula atual: net_flow_usdc = soma(usdc_delta)")
    print("volume_usdc = soma(abs(usdc_delta))")
    print("flow_imbalance = net_flow_usdc / volume_usdc (NaN se volume zero)")
    print(
        "Verificação volume_usdc por swap = abs(usdc_delta): "
        f"{int((~np.isclose(swaps['volume_usdc'], swaps['usdc_delta'].abs(), rtol=RTOL, atol=ATOL)).sum())} divergências"
    )
    check = swaps.assign(
        absolute_usdc=swaps["usdc_delta"].abs(),
        sol_in=swaps["direction"].eq("SOL_IN"),
        sol_out=swaps["direction"].eq("SOL_OUT"),
    ).groupby(pd.Grouper(key="timestamp", freq="1min", closed="left", label="left")).agg(
        net_flow_usdc=("usdc_delta", "sum"),
        volume_usdc=("absolute_usdc", "sum"),
        sol_in_count=("sol_in", "sum"),
        sol_out_count=("sol_out", "sum"),
    )
    check["flow_imbalance"] = check["net_flow_usdc"] / check["volume_usdc"].replace(0, np.nan)
    check = check.reindex(pd.DatetimeIndex(windows["timestamp"]))
    for column in check:
        equal = np.isclose(windows[column], check[column], rtol=RTOL, atol=ATOL, equal_nan=True)
        print(f"Comparação CSV/agregação bruta — {column}: {int((~equal).sum())} divergências")

    flow = windows["flow_imbalance"]
    print("\nDistribuição de flow_imbalance:")
    print(flow.describe(percentiles=[0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]).to_string())
    print(f"Janelas exatamente +1: {int(flow.eq(1).sum())}")
    print(f"Janelas exatamente -1: {int(flow.eq(-1).sum())}")
    print(f"Janelas entre -0.9 e +0.9 (inclusive): {int(flow.between(-0.9, 0.9).sum())}")
    print(f"Janelas NaN: {int(flow.isna().sum())}")
    print("Contagens próximas de ±1 usam rtol=1e-9 e atol=1e-8, separadas das contagens exatas.")
    only_in = (check["sol_in_count"] > 0) & (check["sol_out_count"] == 0)
    only_out = (check["sol_out_count"] > 0) & (check["sol_in_count"] == 0)
    both = (check["sol_in_count"] > 0) & (check["sol_out_count"] > 0)
    flow = pd.Series(flow.to_numpy(), index=check.index)
    for label, mask in (("apenas SOL_IN", only_in), ("apenas SOL_OUT", only_out), ("ambas as direções", both)):
        subset = flow.loc[mask]
        print(
            f"Janelas {label}: {int(mask.sum())} | exatamente +1: {int(subset.eq(1).sum())} | "
            f"exatamente -1: {int(subset.eq(-1).sum())} | "
            f"próximas de ±1: {int(np.isclose(subset.abs(), 1, rtol=RTOL, atol=ATOL).sum())} | "
            f"abs(flow) >= 0.9: {int(subset.abs().ge(0.9).sum())}"
        )
    in_volume = windows["sol_in_volume_usdc"]
    out_volume = windows["sol_out_volume_usdc"]
    dominance = pd.concat([in_volume, out_volume], axis=1).max(axis=1) / windows["volume_usdc"].replace(0, np.nan)
    expected_abs_flow = 2 * dominance - 1
    print(
        "Relação abs(flow_imbalance) = 2 * fração do volume da direção dominante - 1: "
        f"{int((~np.isclose(flow.abs().to_numpy(), expected_abs_flow, rtol=RTOL, atol=ATOL, equal_nan=True)).sum())} divergências"
    )
    print("Uma direção única gera ±1; com ambas, abs(flow) >= 0.9 implica >=95% do volume numa direção.")


# Optional signature-level investigation; the existing CSV diagnosis stays read-only.
def decode_whirlpool_instructions(instructions):
    """Identify Anchor discriminators even when optimized handlers omit name logs."""
    import hashlib

    names = (
        "swap", "swap_v2", "two_hop_swap", "two_hop_swap_v2",
        "increase_liquidity", "increase_liquidity_v2",
        "increase_liquidity_by_token_amounts_v2", "reposition_liquidity_v2",
        "decrease_liquidity", "decrease_liquidity_v2",
        "collect_fees", "collect_fees_v2", "update_fees_and_rewards",
        "open_position_with_token_extensions", "close_position_with_token_extensions",
        "open_position", "open_position_with_metadata", "close_position",
        "collect_reward", "collect_reward_v2", "collect_protocol_fees", "collect_protocol_fees_v2",
    )
    discriminators = {hashlib.sha256(f"global:{name}".encode()).digest()[:8]: name for name in names}
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    decoded = []
    for instruction in instructions:
        if instruction.get("programId") != "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc":
            continue
        encoded = instruction.get("data", "")
        number = 0
        for character in encoded:
            number = number * 58 + alphabet.index(character)
        raw = b"\0" * (len(encoded) - len(encoded.lstrip("1"))) + number.to_bytes((number.bit_length() + 7) // 8, "big")
        decoded.append(discriminators.get(raw[:8], f"unknown:{raw[:8].hex()}"))
    return decoded


def investigate_transactions():
    import json
    import os
    import tempfile
    from collections import Counter

    from dotenv import load_dotenv
    import requests

    load_dotenv(BASE_DIR / ".env")
    api_key = os.getenv("HELIUS_API_KEY")
    if not api_key:
        raise RuntimeError("HELIUS_API_KEY ausente.")
    swaps = pd.read_csv(SWAPS_PATH)
    selected = pd.concat([
        swaps.nsmallest(10, "price").assign(extreme="lowest"),
        swaps.nlargest(10, "price").assign(extreme="highest"),
    ]).drop_duplicates("signature")
    cache_dir = Path(tempfile.gettempdir()) / "liquidity_radar_transaction_diagnostics"
    cache_dir.mkdir(exist_ok=True)
    rows = []
    for record in selected.to_dict("records"):
        signature = record["signature"]
        cache_path = cache_dir / f"{signature}.json"
        if cache_path.exists():
            transaction = json.loads(cache_path.read_text())
        else:
            try:
                response = requests.post(
                    "https://mainnet.helius-rpc.com/",
                    params={"api-key": api_key},
                    json={"jsonrpc": "2.0", "id": signature, "method": "getTransaction",
                          "params": [signature, {"encoding": "jsonParsed", "commitment": "finalized",
                                                 "maxSupportedTransactionVersion": 0}]},
                    timeout=30,
                )
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                payload = response.json()
                transaction = payload.get("result")
                if not isinstance(transaction, dict):
                    raise RuntimeError("Transação indisponível ou resposta RPC inválida")
            except (requests.RequestException, ValueError, RuntimeError) as error:
                # Never print requests exception text: it can contain the authenticated URL.
                reason = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                print(f"Falha em {signature}: {reason}")
                continue
            cache_path.write_text(json.dumps(transaction, ensure_ascii=False, indent=2))
        meta = transaction.get("meta", {})
        message = transaction.get("transaction", {}).get("message", {})
        outer = message.get("instructions", [])
        inner = [instruction for group in meta.get("innerInstructions", [])
                 for instruction in group.get("instructions", [])]
        instructions = outer + inner
        programs = Counter(instruction.get("programId", "unknown") for instruction in instructions)
        parsed_types = Counter(
            instruction["parsed"].get("type", "unknown") for instruction in instructions
            if isinstance(instruction.get("parsed"), dict)
        )
        names = [line.split("Instruction: ", 1)[1] for line in meta.get("logMessages", [])
                 if "Instruction: " in line]
        transfers = [instruction for instruction in instructions
                     if isinstance(instruction.get("parsed"), dict)
                     and instruction["parsed"].get("type") in ("transfer", "transferChecked")]
        token_transfers = [instruction for instruction in transfers
                           if instruction.get("program") in ("spl-token", "spl-token-2022")]
        sol_transfers = [instruction for instruction in transfers if instruction.get("program") == "system"]
        keys = [key.get("pubkey") if isinstance(key, dict) else key for key in message.get("accountKeys", [])]
        balances = meta.get("preTokenBalances", []) + meta.get("postTokenBalances", [])
        mint_by_account = {keys[b["accountIndex"]]: b["mint"] for b in balances}
        mint_counts = Counter()
        transfer_details = []
        for instruction in token_transfers:
            info = instruction["parsed"].get("info", {})
            mint = info.get("mint") or mint_by_account.get(info.get("source")) or mint_by_account.get(info.get("destination"))
            mint_counts[mint or "unknown"] += 1
            transfer_details.append({"mint": mint, **info})
        decoded = decode_whirlpool_instructions(instructions)
        labels = " ".join(names).lower()
        liquidity = any("liquidity" in name for name in decoded)
        swaps_count = sum("swap" in name for name in decoded)
        pattern = ("fees collection + swap + liquidity reinvestment" if "collectandcompoundfees" in labels else
                   "liquidity withdrawal + fees + swap + position close" if "decreasetunalp" in labels else
                   "Tuna position opening + swap + liquidity deposit" if "openandincreasetunalp" in labels else
                   "liquidity repositioning (no swap instruction)" if "reposition_liquidity_v2" in decoded else
                   "swap + position opening + liquidity deposit" if liquidity and swaps_count else
                   "liquidity add/remove" if liquidity else
                   "multiple swaps / complex route" if swaps_count > 1 else
                   "single swap + ancillary operations" if swaps_count == 1 else
                   "other / undetermined")
        row = {**record, "pattern": pattern, "token_transfers": len(token_transfers),
               "sol_transfers": len(sol_transfers), "swap_instructions": swaps_count,
               "liquidity_instructions": liquidity, "instruction_names": names,
               "decoded_whirlpool_instructions": decoded,
               "programs": dict(programs), "parsed_instruction_types": dict(parsed_types),
               "token_transfer_mints": dict(mint_counts),
               "token_transfer_details": transfer_details,
               "sol_transfer_details": [i["parsed"]["info"] for i in sol_transfers],
               "multiple_sol_usdc_movements": any(mint_counts[mint] > 1 for mint in (
                   "So11111111111111111111111111111111111111112",
                   "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")) or len(sol_transfers) > 1,
               "simple_swap_candidate": swaps_count == 1 and not liquidity and len(token_transfers) == 2,
               "complex_route_evidence": swaps_count > 1 or any("two_hop" in name for name in decoded),
               "arbitrage": "not established from these instructions",
               "transaction_error": meta.get("err")}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2))
    (cache_dir / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print("\nPadrões (hipóteses baseadas em instructions/logs; não são regras de filtragem):")
    if rows:
        print(pd.DataFrame(rows)[["signature", "extreme", "price", "pattern", "swap_instructions",
                                   "token_transfers", "sol_transfers", "simple_swap_candidate"]].to_string(index=False))
        print("\nContagem por padrão e extremo:")
        print(pd.crosstab(pd.DataFrame(rows)["pattern"], pd.DataFrame(rows)["extreme"]).to_string())
    print("SOL transfers conta apenas instruções System transfer; WSOL está em token transfers.")
    print("Criação/fechamento de contas e rent não são contados como System transfer.")
    print(f"\nConsultadas/analisadas: {len(rows)}/{len(selected)} | detalhes: {cache_dir}")


def classify_pool_transaction(transaction):
    """Provisional diagnostic categories based on target vaults, not price."""
    pool = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
    whirlpool = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
    mints = {"So11111111111111111111111111111111111111112",
             "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}
    meta = transaction.get("meta") or {}
    message = transaction.get("transaction", {}).get("message", {})
    keys = [key.get("pubkey") if isinstance(key, dict) else key
            for key in message.get("accountKeys", [])]
    balances = meta.get("preTokenBalances", []) + meta.get("postTokenBalances", [])
    vaults = {keys[b["accountIndex"]] for b in balances
              if b.get("owner") == pool and b.get("mint") in mints}
    outer = message.get("instructions", [])
    inner_by_index = {group["index"]: group["instructions"]
                      for group in meta.get("innerInstructions", [])}
    all_instructions = outer + [i for group in inner_by_index.values() for i in group]
    programs = sorted({i.get("programId", "unknown") for i in all_instructions})
    token_transfers = []
    sol_transfers = []
    for instruction in all_instructions:
        parsed = instruction.get("parsed")
        if not isinstance(parsed, dict) or parsed.get("type") not in ("transfer", "transferChecked"):
            continue
        if instruction.get("program") in ("spl-token", "spl-token-2022"):
            token_transfers.append(instruction)
        elif instruction.get("program") == "system":
            sol_transfers.append(instruction)
    evidence = []
    unknown = len(vaults) != 2 or meta.get("err") is not None
    for index, instruction in enumerate(outer):
        sequence = [instruction, *inner_by_index.get(index, [])]
        for position, item in enumerate(sequence):
            if item.get("programId") != whirlpool:
                continue
            name = decode_whirlpool_instructions([item])[0]
            accounts = set(item.get("accounts", []))
            height = item.get("stackHeight") or (1 if position == 0 else None)
            descendants = []
            if height is not None:
                for child in sequence[position + 1:]:
                    child_height = child.get("stackHeight")
                    if child_height is None or child_height <= height:
                        break
                    descendants.append(child)
            touched = set()
            for child in descendants:
                parsed = child.get("parsed")
                if isinstance(parsed, dict) and parsed.get("type") in ("transfer", "transferChecked"):
                    info = parsed.get("info", {})
                    touched.update({info.get("source"), info.get("destination")} & vaults)
            target = pool in accounts
            management = any(word in name for word in (
                "liquidity", "collect", "open_position", "close_position"))
            affects_vaults = bool(touched or accounts & vaults)
            if target and name.startswith("unknown:"):
                unknown = True
            evidence.append({"instruction": name, "outer_index": index,
                             "target_pool": target, "accounts_vaults": sorted(accounts & vaults),
                             "transfer_vaults": sorted(touched),
                             "management_affects_target_vaults": target and management and affects_vaults,
                             "target_swap": target and name in ("swap", "swap_v2")
                                            and vaults <= accounts and len(vaults) == 2})
    swaps_present = any(e["target_swap"] for e in evidence)
    management_present = any(e["management_affects_target_vaults"] for e in evidence)
    category = ("UNKNOWN" if unknown else
                "COMPOSITE_SWAP" if swaps_present and management_present else
                "SIMPLE_SWAP" if swaps_present else
                "LIQUIDITY_OPERATION" if management_present else "UNKNOWN")
    return {"category": category, "programs": programs,
            "orca_instructions": [e["instruction"] for e in evidence],
            "target_vaults": sorted(vaults), "instruction_evidence": evidence,
            "has_target_swap": swaps_present, "has_target_liquidity_management": management_present,
            "token_transfers": len(token_transfers), "sol_transfers": len(sol_transfers)}


def investigate_control_group():
    import json
    import os
    import tempfile

    from dotenv import load_dotenv
    import requests

    cache_dir = Path(tempfile.gettempdir()) / "liquidity_radar_transaction_diagnostics"
    swaps = pd.read_csv(SWAPS_PATH)
    median = swaps["price"].median()
    controls = swaps.assign(distance_to_median=(swaps["price"] - median).abs()).sort_values(
        "distance_to_median", kind="stable").drop_duplicates("signature").head(20)
    extremes = pd.concat([swaps.nsmallest(10, "price"), swaps.nlargest(10, "price")]).drop_duplicates("signature")
    # Never refetch extremes: fail before any requests if previous evidence is missing.
    for signature in extremes["signature"]:
        if not (cache_dir / f"{signature}.json").exists():
            raise RuntimeError("Cache de extremos incompleto; nenhuma consulta realizada.")
    load_dotenv(BASE_DIR / ".env")
    api_key = os.getenv("HELIUS_API_KEY")
    if not api_key:
        raise RuntimeError("HELIUS_API_KEY ausente.")
    print(f"Mediana global: {median:.12g} | controles: {len(controls)}")
    rows = []
    calls = 0
    for group, selected in (("extremes", extremes), ("median_control", controls)):
        for record in selected.to_dict("records"):
            signature = record["signature"]
            cache_path = cache_dir / f"{signature}.json"
            if cache_path.exists():
                transaction = json.loads(cache_path.read_text())
            else:
                if group != "median_control" or calls >= 20:
                    raise RuntimeError("Limite de consultas atingido.")
                calls += 1
                try:
                    response = requests.post(
                        "https://mainnet.helius-rpc.com/", params={"api-key": api_key},
                        json={"jsonrpc": "2.0", "id": signature, "method": "getTransaction",
                              "params": [signature, {"encoding": "jsonParsed", "commitment": "finalized",
                                                     "maxSupportedTransactionVersion": 0}]}, timeout=30)
                    if response.status_code != 200:
                        raise RuntimeError(f"HTTP {response.status_code}")
                    transaction = response.json().get("result")
                    if not isinstance(transaction, dict):
                        raise RuntimeError("Resposta RPC inválida ou transação indisponível")
                except (requests.RequestException, ValueError, RuntimeError) as error:
                    reason = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                    row = {**record, "group": group, "category": "UNKNOWN", "error": reason}
                    rows.append(row)
                    print(json.dumps(row, ensure_ascii=False))
                    continue
                cache_path.write_text(json.dumps(transaction, ensure_ascii=False, indent=2))
            row = {**record, "group": group, **classify_pool_transaction(transaction)}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False, indent=2))
    counts = pd.crosstab(pd.DataFrame(rows)["category"], pd.DataFrame(rows)["group"]).reindex(
        ["SIMPLE_SWAP", "COMPOSITE_SWAP", "LIQUIDITY_OPERATION", "UNKNOWN"], fill_value=0)
    print("\nComparação estrutural provisória:")
    print(counts.to_string())
    print(f"Novas consultas nesta execução: {calls} (máximo 20; sem retries)")
    print("SOL transfers: apenas System transfer; WSOL incluído em token transfers.")
    print("Grupo de controle selecionado por preço: comparação exploratória, não validação de um filtro.")
    (cache_dir / "control_comparison.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--transactions", action="store_true", help="Investigate 20 extreme signatures via Helius")
    parser.add_argument("--control", action="store_true", help="Compare 20 median-price controls against cached extremes")
    arguments = parser.parse_args()
    if arguments.control:
        investigate_control_group()
    elif arguments.transactions:
        investigate_transactions()
    else:
        main()
