# What do AI assistants tell Australians about super fund fees?

Asks several AI models to rank Australian super funds from lowest to highest fees for a given customer (a *persona*), and compares their answers with the fees in `fees.csv`.

**Minimal version.** `fees.csv` holds **dummy data**, and the code skips most edge-case checks.

## How it works

For each persona in `personas/`, each model and each run:

1. **Prompt** = the persona text + the funds in `companies.csv` + fixed answer-format rules.
2. **Call** the model once: no history, no system prompt, no web search.
3. **Parse** its `| Rank | Fund | Admin fees (AUD) | Investment fees (AUD) | Transaction costs (AUD) | Total (AUD) | Reason |` table. Fund names are matched loosely (case, spaces, punctuation and `**bold**` are ignored).
4. **Compare** with the actual fees for the persona's balance. Admin fees, investment fees and transaction costs are kept separate, as funds disclose them, so each can be checked against other sources:

   ```text
   admin  = admin_fee_fixed_aud + balance × admin_fee_pct / 100
   invest = balance × investment_fee_pct / 100
   trans  = balance × transaction_cost_pct / 100
   total  = admin + invest + trans        (funds are ranked by total)
   ```

The balance is the first dollar amount in the persona text, e.g. `$150,000`. A persona with no amount uses $50,000.

## Files

| File | Contents |
|---|---|
| `companies.csv` | Funds to rank: one `company` column |
| `fees.csv` | `company, admin_fee_fixed_aud, admin_fee_pct, investment_fee_pct, transaction_cost_pct, source`. Every fund in `companies.csv` must be here, with the same name |
| `personas/*.txt` | One customer per file: the scenario, its balance and the request to rank. All are run |
| `llm_super_ranking.py` | The script |
| `archive/it_life_insurance/` | The earlier Italian life-insurer version |

## Run

```bash
pip install -r requirements.txt
# set ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID, OPENAI_API_KEY, GEMINI_API_KEY
python llm_super_ranking.py                       # all models, 1 run each
python llm_super_ranking.py --runs 5              # 5 runs per model, averaged
python llm_super_ranking.py --provider anthropic  # one provider only
```

A failed call (for example, a missing key) is printed and recorded, and the loop carries on.

## Output: `results/<timestamp>/<persona>/`

| File | Contents |
|---|---|
| `summary.csv` | One row per fund, cheapest first: actual rank, total, admin fees, investment fees and transaction costs, then each model's mean rank and mean estimate of each |
| `accuracy.csv` | One row per model: `rank_corr` (rank correlation with the actual ranking, 1 = perfect), median absolute % error for the total, admin fees, investment fees and transaction costs, `funds_ranked_per_run`, `total_mismatches_per_run` (funds whose total differs from the sum of the model's own three parts by more than $5) |
| `long.csv` | One row per run × model × fund: rank, estimated admin fees, investment fees, transaction costs and total, reason, actual rank and fees, % error per fee, `total_gap` (the model's total minus the sum of its own three parts) |
| `raw.json` | The prompt, the balance, and every call's full response, tokens, cost and any error |

Fee estimates written as ranges (`$300–400`) count as the first number. A fund the model left out, or wrote under a name that doesn't match, is missing from that run.
