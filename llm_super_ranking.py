#!/usr/bin/env python3
"""
Ask several LLMs to rank Australian super funds by fees for a customer (persona)
and compare their answers with the fees in fees.csv.

For every persona in personas/*.txt, every model and every run, the prompt is the
persona text + the funds in companies.csv + the answer-format rules below. The
model's | Rank | Fund | Admin | Investment | Transaction | Total | Reason | table is parsed per fund.

The persona's balance is the first dollar amount in its text ($50,000 if none). Actual fees and costs:
    admin  = admin_fee_fixed_aud + balance * admin_fee_pct / 100
    invest = balance * investment_fee_pct / 100
    trans  = balance * transaction_cost_pct / 100
    total  = admin + invest + trans        (funds are ranked by total)

Outputs in results/<timestamp>/<persona>/:
    summary.csv    one row per fund: actual rank and fees, each model's mean rank and fees
    accuracy.csv   one row per model: rank correlation with the actual ranking, median % error per fee,
                   and how many of its totals don't equal its own admin + investment + transaction
    long.csv       one row per run x model x fund, with the model's reason and total_gap
    raw.json       prompt, full responses, tokens and cost

Setup:  pip install -r requirements.txt
        set ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID, OPENAI_API_KEY, GEMINI_API_KEY
Run:    python llm_super_ranking.py [--runs 5] [--provider anthropic]
"""
import argparse
import difflib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent

# Prices in USD per 1M tokens; re-check before relying on the cost figures.
# Gemini's thinking counts against max_tokens, so it gets a higher cap.
MODELS = [
    {"provider": "anthropic", "model": "claude-sonnet-5", "in": 2.00, "out": 10.00, "max_tokens": 8192},
    {"provider": "openai", "model": "gpt-5.6-terra", "in": 2.00, "out": 12.00, "max_tokens": 8192},
    {"provider": "google", "model": "gemini-3.1-pro-preview", "in": 2.00, "out": 12.00, "max_tokens": 32768},
]

FORMAT = """

Funds:
{funds}

Answer with a single markdown table with exactly these columns:
| Rank | Fund | Admin fees (AUD) | Investment fees (AUD) | Transaction costs (AUD) | Total (AUD) | Reason |

Rules:
- Rank 1 has the lowest total annual fees and costs for you, rank {n} the highest.
- The dollar columns are your estimates for one year in the fund's default MySuper option on your balance, each as a single dollar amount: administration fees, investment fees, transaction costs, and their total.
- Include every fund exactly once, and no other funds.
- Write each fund name exactly as it appears in the list above.
- In Reason, explain in one or two sentences why you gave the fund this rank.
"""


# Each call returns (text, input_tokens, output_tokens). API keys are read from the environment.
def call_anthropic(model, prompt, max_tokens):
    import anthropic
    client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]})
    r = client.messages.create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in r.content if b.type == "text"), r.usage.input_tokens, r.usage.output_tokens


def call_openai(model, prompt, max_tokens):
    from openai import OpenAI
    r = OpenAI().responses.create(model=model, input=prompt, max_output_tokens=max_tokens)
    return r.output_text, r.usage.input_tokens, r.usage.output_tokens


def call_google(model, prompt, max_tokens):
    from google import genai
    r = genai.Client().models.generate_content(model=model, contents=prompt, config={"max_output_tokens": max_tokens})
    u = r.usage_metadata  # thinking tokens are billed as output
    return r.text or "", u.prompt_token_count or 0, (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)


CALLERS = {"anthropic": call_anthropic, "openai": call_openai, "google": call_google}
PARTS = ["total", "admin", "invest", "trans"]  # fee columns, in output order
TOTAL_TOLERANCE = 5  # AUD; a model's total may differ from the sum of its parts by this much (rounding)


def read_csv(name, **kw):
    # utf-8-sig reads Excel's "CSV UTF-8"; stray Windows bytes (e.g. non-breaking spaces) are dropped
    return pd.read_csv(ROOT / name, encoding="utf-8-sig", encoding_errors="ignore", **kw)


def norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def dollars(cell):
    """First number in a cell: '$1,234' -> 1234.0, '$300-400' -> 300.0, 'unknown' -> None."""
    m = re.search(r"\d[\d,]*(\.\d+)?", cell)
    return float(m.group().replace(",", "")) if m else None


def parse(text, funds):
    """The model's table as {fund: {rank, est_admin, est_invest, est_trans, est_total, reason}}; names are matched loosely."""
    by_norm = {norm(f): f for f in funds}
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rank = re.search(r"\d+", cells[0]) if len(cells) >= 7 else None
        if not rank:
            continue  # not a table row, or the header / separator row
        hit = difflib.get_close_matches(norm(cells[1]), by_norm, n=1, cutoff=0.8)
        if hit and by_norm[hit[0]] not in out:
            out[by_norm[hit[0]]] = {"rank": int(rank.group()), "est_admin": dollars(cells[2]),
                                   "est_invest": dollars(cells[3]), "est_trans": dollars(cells[4]),
                                   "est_total": dollars(cells[5]), "reason": cells[6]}
    return out


def main():
    p = argparse.ArgumentParser(description="Rank Australian super funds by fees with several LLMs.")
    p.add_argument("--runs", type=int, default=1, help="times to ask each model (default 1)")
    p.add_argument("--provider", choices=["all", *CALLERS], default="all")
    args = p.parse_args()

    funds = read_csv("companies.csv").company.str.strip().drop_duplicates().tolist()
    fees = read_csv("fees.csv", index_col="company").loc[funds]  # KeyError here = fund missing from fees.csv
    models = [m for m in MODELS if args.provider in ("all", m["provider"])]
    run_dir = ROOT / "results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    total_cost = 0.0

    for persona in sorted((ROOT / "personas").glob("*.txt")):
        persona_text = persona.read_text(encoding="utf-8").strip()
        m = re.search(r"\$([\d,]+)", persona_text)
        balance = float(m.group(1).replace(",", "")) if m else 50_000.0
        admin = fees.admin_fee_fixed_aud + balance * fees.admin_fee_pct / 100
        invest = balance * fees.investment_fee_pct / 100
        trans = balance * fees.transaction_cost_pct / 100
        total = admin + invest + trans
        actual = pd.DataFrame({"actual_rank": total.rank(method="min").astype(int), "actual_total": total,
                               "actual_admin": admin, "actual_invest": invest, "actual_trans": trans}).round(2)
        prompt = persona_text + FORMAT.format(funds="\n".join(f"- {f}" for f in funds), n=len(funds))
        print(f"\n=== {persona.stem}: balance ${balance:,.0f} ===")

        rows, calls = [], []
        for cfg in models:
            for run in range(1, args.runs + 1):
                print(f"{cfg['model']} run {run} ... ", end="", flush=True)
                try:
                    answer, tin, tout = CALLERS[cfg["provider"]](cfg["model"], prompt, cfg["max_tokens"])
                    error = ""
                except Exception as e:  # keep going if one call fails
                    answer, tin, tout, error = "", 0, 0, f"{type(e).__name__}: {e}"
                cost = (tin * cfg["in"] + tout * cfg["out"]) / 1e6
                total_cost += cost
                parsed = parse(answer, funds)
                print(error or f"ranked {len(parsed)}/{len(funds)}, ${cost:.4f}")
                calls.append({"model": cfg["model"], "run": run, "error": error, "input_tokens": tin,
                              "output_tokens": tout, "cost_usd": cost, "response": answer})
                rows += [{"run": run, "model": cfg["model"], "fund": f, **v} for f, v in parsed.items()]

        out = run_dir / persona.stem
        out.mkdir(parents=True)
        (out / "raw.json").write_text(json.dumps({"balance": balance, "prompt": prompt, "calls": calls},
                                                 indent=2, ensure_ascii=False), encoding="utf-8")
        if not rows:
            continue
        df = pd.DataFrame(rows).join(actual, on="fund")
        for part in PARTS:
            est, act = df[f"est_{part}"], df[f"actual_{part}"]
            df[f"abs_pct_err_{part}"] = ((est - act).abs() / act * 100).round(1)
        df["total_gap"] = (df.est_total - df.est_admin - df.est_invest - df.est_trans).round(2)  # model's total minus its own parts
        df.to_csv(out / "long.csv", index=False, encoding="utf-8-sig")

        cols = ["rank", *(f"est_{part}" for part in PARTS)]
        means = df.pivot_table(index="fund", columns="model", values=cols)[cols].round(1)
        means.columns = [f"{value}_{model}" for value, model in means.columns]
        summary = actual.join(means).sort_values("actual_rank")
        summary.to_csv(out / "summary.csv", index_label="fund", encoding="utf-8-sig")

        by_model = df.groupby("model")
        accuracy = pd.DataFrame({
            "rank_corr": {m: g["rank"].corr(g.actual_rank) for m, g in by_model},  # Spearman when no ties
            **{f"median_abs_pct_err_{part}": by_model[f"abs_pct_err_{part}"].median() for part in PARTS},
            "funds_ranked_per_run": by_model.size() / args.runs,
            "total_mismatches_per_run": (df.total_gap.abs() > TOTAL_TOLERANCE).groupby(df.model).sum() / args.runs,
        }).round(2)
        accuracy.to_csv(out / "accuracy.csv", index_label="model", encoding="utf-8-sig")
        print(f"\n{summary.to_string()}\n\n{accuracy.to_string()}\nSaved to {out}")

    print(f"\nTotal cost: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
