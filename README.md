# What do AI assistants tell Australians about super fees?

**Ask leading AI models to rank Australian superannuation funds from cheapest to most expensive for a given member, and compare their answers with the fees the funds actually charge.**

## Why this matters

More and more people ask an AI assistant such as ChatGPT, Claude or Gemini questions like:

> *"I'm 36 with $150,000 in super. Which fund would charge me the least?"*

The answer shapes which funds they look at. This repository measures how good those answers are:

- Do the models put the funds in the right order of cost?
- How far are their dollar estimates from the fees in the funds' own disclosures?
- Which part do they get most wrong: administration fees, investment fees or transaction costs?
- Do different models agree with each other, and does the same model give the same answer twice?

## How it works

```text
inputs/companies.csv ─┐
                      ├─► prompt ─► each model (× runs) ─► parse table ──┐
personas/*.txt ───────┘                                                    ├─► compare ─► results/<timestamp>/<persona>/
inputs/fees.csv ───────────► actual fees for the persona's balance ───────┘
```

For **each persona** in [`personas/`](personas/), **each model** and **each run**:

1. **Build the prompt** from the persona text, the funds in [`inputs/companies.csv`](inputs/companies.csv) and fixed answer-format rules (see [The prompt](#the-prompt)).
2. **Ask the model.** Each call is a single, independent request: no conversation history, no system prompt, no web search, and the provider's default settings apart from the output-token cap.
3. **Parse the answer.** The model's table gives, for each fund, a rank, estimated administration fees, investment fees, transaction costs and total in dollars, and a reason.
4. **Calculate the actual fees** for the persona's balance from [`inputs/fees.csv`](inputs/fees.csv).
5. **Compare and save.** Ranks and dollar amounts are compared fund by fund, and the results are written to `results/<timestamp>/<persona>/`.

If a call fails (a missing API key, a provider outage), the error is printed and recorded, and the loop carries on.

Models used by default (the `MODELS` list at the top of [`llm_super_ranking.py`](llm_super_ranking.py)):

| Provider | Model | Output-token cap |
|---|---|---|
| Anthropic | `claude-sonnet-5` | 16,384 |
| OpenAI | `gpt-5.6-terra` | 32,768 |
| Google | `gemini-3.1-pro-preview` | 32,768 |

## Quick start

Requires Python 3.10+ and an API key for each provider you want to use.

```bash
git clone <this-repo-url>
cd <repo-folder>
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Set the environment variables for the providers you will use:

| Variable | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | Claude |
| `ANTHROPIC_WORKSPACE_ID` | Claude; sent as the `anthropic-workspace-id` header |
| `OPENAI_API_KEY` | GPT |
| `GEMINI_API_KEY` | Gemini |

```bash
export ANTHROPIC_API_KEY=...       # PowerShell: $env:ANTHROPIC_API_KEY = "..."
export ANTHROPIC_WORKSPACE_ID=...
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...
```

A missing variable only affects that provider: its calls are recorded as errors and the other models still run.

Run it:

```bash
python llm_super_ranking.py                       # every persona, every model, one run each
python llm_super_ranking.py --runs 5              # ask each model 5 times; ranks and fees are averaged
python llm_super_ranking.py --provider anthropic  # only one provider
```

| Option | Default | What it does |
|---|---|---|
| `--runs N` | `1` | Ask each model N times per persona |
| `--provider {all,anthropic,openai,google}` | `all` | Run all models, or only those from one provider |

### Cost

With 30 funds, one persona and one run cost about **$0.25** for all three models (roughly $0.06 for GPT, $0.08 for Claude and $0.10 for Gemini, which spends the most tokens thinking). The total scales with personas × runs. The cost of each call is printed as it runs, the total is printed at the end, and both are saved in `raw.json`. Costs are calculated from the prices in `MODELS`; check them against the providers' pricing pages before relying on them.

## Inputs

### `inputs/companies.csv`

The funds to rank: a `company` header, then one name per line. These are the names the models see, so they should be names a member would recognise. The list was built from APRA's list of RSE licensees and MySuper authorised products (see [`Sources/`](Sources/)). Duplicate names are dropped.

### `inputs/fees.csv`

The actual fees of each fund's default MySuper option, one row per fund:

| Column | Meaning |
|---|---|
| `company` | Fund name, exactly as in `companies.csv` |
| `admin_fee_fixed_aud` | Fixed administration fee, dollars per year |
| `admin_fee_rate` | Administration fee as a share of the balance per year |
| `investment_fee_rate` | Investment fees and costs as a share of the balance per year |
| `transaction_cost_rate` | Transaction costs as a share of the balance per year |
| `source` | Where the figures come from: a web page, or a file in `Sources/fund_reports_on_fees/` |

Rates are **decimals**, the way Excel stores percentages: 0.50% p.a. is `0.005`. If you save the file from Excel, format the rate cells as *General* first; cells formatted as percentages are saved as text such as `0.50%`, which the script cannot read.

For a persona with balance *B*:

```text
admin  = admin_fee_fixed_aud + B × admin_fee_rate
invest = B × investment_fee_rate
trans  = B × transaction_cost_rate
total  = admin + invest + trans        (funds are ranked by total)
```

Every fund in `companies.csv` must appear in `fees.csv` with the same name; otherwise the script stops before calling any model. The fees were collected by hand from each fund's PDS or fees-and-costs page and checked in `Sources/Manual calc.xlsx`. Funds change their fees, so check the sources before relying on the figures. Lifecycle products can charge different investment fees at different ages, while `fees.csv` holds one set of rates per product, so make sure they match the persona's age.

### `personas/*.txt`

One file per member, describing the member and their balance, and asking for the ranking. For example, [`personas/mid_career.txt`](personas/mid_career.txt):

```text
You are a 36-year-old Australian with $150,000 in super, in your fund's default option.

Rank the following Australian superannuation funds from lowest to highest total annual fees for you.
```

The balance used for the actual fees is the **first dollar amount** in the text ($50,000 if there is none). Every `.txt` file in the folder is run, and its name becomes the name of its output folder.

## The prompt

The script adds the fund list and fixed format rules to the persona text, so every answer can be parsed:

```text
Funds:
- QSuper Lifetime
- AustralianSuper
- …

Answer with a single markdown table with exactly these columns:
| Rank | Fund | Admin fees (AUD) | Investment fees (AUD) | Transaction costs (AUD) | Total (AUD) | Reason |

Rules:
- Rank 1 has the lowest total annual fees and costs for you, rank 30 the highest.
- The dollar columns are your estimates for one year in the fund's default MySuper option on your balance, each as a single dollar amount: administration fees, investment fees, transaction costs, and their total.
- Include every fund exactly once, and no other funds.
- Write each fund name exactly as it appears in the list above.
- In Reason, explain in one or two sentences why you gave the fund this rank.
```

The full prompt of every run is saved in `raw.json`.

## Output

Each invocation writes to `results/<timestamp>/`, with one subfolder per persona:

| File | Contents |
|---|---|
| `summary.csv` | One row per fund, cheapest first. Columns are grouped by measure, actual value first and then each model: rank, total, admin fees, investment fees, transaction costs. With `--runs` above 1, model values are means over the runs |
| `accuracy.csv` | One row per model: `rank_corr`, the median absolute % error of the total and of each part, `funds_ranked_per_run`, `total_mismatches_per_run` |
| `long.csv` | One row per run × model × fund: actual and estimated value with the % error, for the rank, total and each part; `total_gap`; the model's reason |
| `raw.json` | The prompt, the balance, and for every call the full response, token counts, cost and any error |

The CSV files are UTF-8 with a byte-order mark, so they open correctly in Excel.

### How the numbers are calculated

- **`rank_corr`**: correlation between the model's ranks and the actual ranks, over all its runs. 1 means the same order, 0 means no relation. With no tied ranks it equals Spearman's rank correlation.
- **`abs_pct_err_*`**: |estimate − actual| / actual × 100, for the total and each part. `accuracy.csv` shows the median per model.
- **`total_gap`**: the model's total minus the sum of its own three parts, which checks the model's arithmetic. `total_mismatches_per_run` counts funds where the gap is more than $5 (`TOTAL_TOLERANCE` in the script).
- **`funds_ranked_per_run`**: how many funds the model ranked, on average. Below the number of funds means it left some out or renamed them beyond recognition.

### How answers are parsed

- A table row is any line with at least seven cells and a number in the first one.
- Fund names are matched loosely: case, spaces, punctuation and markdown such as `**bold**` are ignored, and a name at least 80% similar to one in the list is accepted. Rows that match no fund are skipped; if a fund appears twice, the first row is kept.
- Dollar cells are read as their first number, so `$1,234` is 1234 and a range such as `$300–400` counts as 300. A cell with no number, such as `unknown`, is left blank and excluded from the error figures.

## How to read the results

- **This measures what the models say, not what the funds charge.** The models answer from their training data, without web search, and their knowledge can be out of date. That is the point: the tool shows what a member asking an AI assistant would be told.
- **Consumer apps may answer differently.** The script calls the models through their APIs, without the web search and extra instructions that apps such as ChatGPT or Gemini can add.
- **Answers vary.** The same model can rank a fund quite differently on two runs. Use `--runs 5` or more before drawing conclusions.
- **Ranking forces an order.** Many funds' totals are within a few dollars of each other, so small rank differences mean little; the dollar errors are often more informative.
- **Names matter.** A product name a model does not recognise (a product name rather than the fund's brand, say) gets a guess.
- Nothing produced by this tool is financial advice or an assessment of any fund.

## Project structure

```text
llm_super_ranking.py          the script: models, prompt, API calls, parsing, comparison, outputs
inputs/
  companies.csv               funds to rank (the names the models see)
  fees.csv                    each fund's actual fees and their source
personas/                     member scenarios, one .txt file each (all are run)
Sources/
  List of RSES and RSE Licensees and MySuper Authorised products 19 October 2026.xlsx
                              APRA's list the fund list was built from
  Product_list_assembly.xlsx  how the fund list was assembled from it
  Manual calc.xlsx            hand calculation of the fees, used to check the script
  fund_reports_on_fees/       PDSs and fee guides behind fees.csv
results/                      generated output, one timestamped folder per run
requirements.txt              anthropic, openai, google-genai, pandas
LICENSE                       MIT
```

## Limitations of this version

This is a deliberately small first version. Parsing and input checks are minimal, there is no web-search option and no automated tests.

## License

[MIT](LICENSE)
