# Cypher query analysis in Python

This project analyzes a Cypher query and a supplied graph schema without
requiring graph records. Each supported clause transforms an intermediate bag
of symbolic rows. The same operator chain feeds mathematical expressions,
plain-text reports and the optional finite-graph test evaluator. An advisory
model can generate and revise queries using these reports.

The core lives in `src/graph_query_analysis`. The small `src/pipeline` package
loads data, parses the original query, and stores reports. A schema catalog
describes possible graphs. It does not establish actual counts or guarantee
that a generated query answers a question.

## Run without a model or graph server

Set `CONFIG_NAME` in `src/pipeline/main.py` to `offline`, then run these commands.

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
python -m pipeline.main
```

`CONFIG_NAME` in `src/pipeline/main.py` selects a file under `configs/`.
`configs/offline.json` compiles the
saved local queries without model calls. `configs/default.json` gives each
fresh report to `CypherAgent` for a revised reference query. To
run it, install `pip install -e '.[llm]'`, configure the model credentials, and
set `CONFIG_NAME` to `default`. `configs/mtq_agent.json` instead
generates queries for Mind-the-Query; that requires both the `datasets` and
`llm` extras. The records file holds questions; each config holds the
settings used to process them. Generation and review share one agent when
review is enabled.

`"query_source": "reference"` uses each record's existing `cypher` as the
first query; `"agent"` generates the first query from its question and schema.
The three local records are three prewritten alternatives to the same
question, analyzed independently. Source indices are assigned automatically
for a records file and retained from dataset selections.

`"improve_without_critic": true` sends the newest report directly to the
generator for each revision, up to `max_report_chars`. With `false`, the agent
reviews each report first and revises only when the critic requests it. Each
candidate receives a fresh report. `max_revisions` is a limit, so critic mode
may stop before using every revision.

The `llm` section controls the agent used for generation, revision, and review.

- `service` selects `open_router` or `open_ai`.
- `model` selects the model. `null` reads `OPENROUTER_MODEL` or `OPENAI_MODEL`
  for the chosen service. The API key comes from the corresponding environment variable.
- `max_output_tokens` caps output for each model request. `max_report_chars`
  separately caps the feedback text sent in the prompt.
- `reasoning_effort` is sent to the model when set. `null` uses the provider default.

You can add `"generation_prompt"` with your prompt text to override the
built-in prompt. Reports always go under `outputs/` by default.

The last command writes one fresh `outputs/run-.../` folder. Its `summary.json`
and `errors.json` cover the batch. Each question folder has `history.json`;
each trial stores `query.cypher`, the exact parsed `request.json` and a
`report.txt`. The command never overwrites earlier runs. Set `"show_formulas"`
to `true` in the config to render the mathematical formulas in the reports.

The Python API accepts a list of records:

```python
from pipeline.main import run_pipeline

result = run_pipeline([
    {'question': 'How many accounts?', 'schema': schema,
     'cypher': 'MATCH (b:BankAccount) RETURN count(b) AS total'}
])
print(result['run_dir'])
```

Load selected dataset records with
`pipeline.main.main(dataset_name='mtq', indices=(0,), query_source='reference')`
after installing `pip install -e '.[datasets]'`. Mind-the-Query preprocessing
reads the JSON schema and converts its `datatype` property field to `type`.
CypherBench schemas use a different format and are converted separately. You
can also call `pipeline.data.neo4j_schema.normalize_schema(raw_schema)` directly
when converting a raw schema yourself.

Dataset configs use `dataset_name` and `split` to choose the data. Set `limit`
to a positive integer to take that many rows from the start of the split, or
`"total"` to use the whole split. Use `indices` instead of `limit` to choose
specific rows.

Set `CONFIG_NAME` to `mtq_eval` to evaluate the first Mind-the-Query test row.
The config allows one revision without a critic and caps each request at
512 output tokens.

```powershell
python -m pipeline.main
```

The same options are available through `evaluate_on_dataset()` in
`pipeline.main`. Dataset runs print initial and final exact-match accuracy,
compile rates, and the number of matches gained and lost after revision.
The run's `evaluation.json` includes the metrics and each query comparison.
Accuracy divides the number of exact matches by the number of rows with a
reference `cypher` query, including failed generations in that denominator.
Exact match compares normalized query text. The evaluation config also
records provider token usage.

For model use, pass one `CypherAgent` instance as `generator` and optionally
as `critic`. Set `query_source='agent'` to generate an initial query and
`max_revisions=2` to permit revisions. Without a reviewer, set
`improve_without_critic=True` to use the latest report as revision feedback.
Failed revisions keep the last successfully compiled query selected.

An OpenAI-compatible Responses API adapter is available with the optional
`llm` extra and environment settings `OPENROUTER_API_KEY`,
`OPENROUTER_MODEL` (or the corresponding `OPENAI_` settings):

```python
from pipeline.agent.cypher_agent import CypherAgent
from pipeline.main import run_pipeline

agent = CypherAgent(service='open_router')
result = run_pipeline(records, query_source='agent', max_revisions=2,
    generator=agent, critic=agent)
```

See [the active Python plan](docs/python-plan.md) for implemented features and
limitations. The former Lean code is kept as a historical reference; the
default Python pipeline does not use it.
