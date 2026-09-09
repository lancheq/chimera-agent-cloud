<p align="center">
  <img src="docs/images/thumbnail.png" alt="CHIMERA Agent" width="250">
</p>

# Chimera Agent Baseline

Baseline agent for the
[CHIMERA-Agent challenge](https://chimera-agent.grand-challenge.org/chimera-agent/).
A LangGraph ReAct loop calls clinical tools served via MCP, retrieves
guidelines via RAG, and emits a structured per-case decision through a
terminal form-fill node.

## Quick start

```bash
uv venv && source .venv/bin/activate
cp .env.example .env                    # add HF_TOKEN
make install
make test
```

Download the LLM and embedding model (requires accepted licenses on
[Gemma 4](https://huggingface.co/google/gemma-4-E2B-it) and
[embeddinggemma](https://huggingface.co/google/embeddinggemma-300m)):

```bash
python -c "from huggingface_hub import snapshot_download; snapshot_download('google/gemma-4-E2B-it', local_dir='model/')"
make fetch-embedding-model    # downloads embeddinggemma-300m (~1.2 GB) into resources/embedding_model/
```

The guidelines vector DB (`resources/guidelines_db/`) ships pre-built in
the repo, so RAG works once the embedding model above is in place — no PDF
processing required. To rebuild the corpus from the source PDF instead, see
[Model Configuration → Embedding model (RAG)](docs/models.md#embedding-model-rag).

Get the per-case agent inputs (`structured-prompt.json` + `*-clinical-data.json` per
patient, organised by task):

```bash
# TODO: replace with the actual download once published.
# Expected to extract into data/task1/agent_input/ and
# data/task2/agent_input/, each with one case subdirectory per patient.
```

Run the agent (NVIDIA GPU with ≥24 GB VRAM). The in-process vLLM backend is a
heavy, GPU-only dependency, so it is **not** part of `make install` — install it
once on the GPU box:

```bash
make install-vllm                                            # adds vLLM (Linux GPU only)
make run                                                     # all tasks under data/
make run RUN_ARGS="agent.tasks=[2]"                          # just task 2
make run RUN_ARGS="agent.limit=5"                            # first 5 cases per task
```

`make run` walks `data/task<N>/agent_input/` for every task present and writes
the task-specific Grand Challenge output files under `test/output/task<N>/<case_id>/`.


On a 24 GB card, lower vLLM's memory fraction if startup OOMs (a desktop session
or other process can hold a few GB): `make run RUN_ARGS="generation.gpu_memory_utilization=0.80"`.
Prefer the Docker path below if you don't want vLLM in your local environment.

## Layout

| Local path | Container path | Contents |
|---|---|---|
| `data/task<N>/agent_input/<case>/{structured-prompt,*-clinical-data,prostate-modality-level-neural-representations}.json` | `/input/task<N>/agent_input/<case>/…` | Per-case agent inputs |
| `test/output/` | `/output` | Two task-specific output files per patient written by the agent |
| `model/` | `/opt/ml/model` | LLM weights, including `gemma-4-E2B-it/` and `embedding_model/` |
| `resources/` | `/opt/app/resources` | Config, guidelines DB |

The input root (`data/` locally, `/input` in the container) holds the same
`task<N>/agent_input/<case>/` tree for every task; the agent runs all tasks
present. The participant ships `structured-prompt.json`; `*-clinical-data.json` is read only by
the MCP server and reaches the agent through tool calls.

To test in the GC Docker container (mounts the whole data root, runs all tasks):

```bash
make gc-build
make gc-test                                                  # all tasks under data/
```

## Per-case I/O

Each case is a directory under `/input/task<N>/agent_input/` (read-only).
The agent writes two task-specific JSON output files for each case under
`/output/task<N>/<case_id>/`.

```
/input/task<N>/agent_input/<case_id>/structured-prompt.json    # patient context rendered into the agent prompt
/input/task<N>/agent_input/<case_id>/*-clinical-data.json      # served by the MCP server through tool calls
/input/task<N>/agent_input/<case_id>/prostate-modality-level-neural-representations.json  # frozen foundation-model embeddings (optional)
/output/task<N>/<case_id>/  # two task-specific output files written by the agent; see Outputs below for details
```

`structured-prompt.json` + `*-clinical-data.json` mirror the urologist forms: `structured-prompt.json` is
the read-only "Clinical data" panel the urologist saw up front, and
`*-clinical-data.json` holds the form's masked "Extended EHR view" documents —
revealed only on request, i.e. via MCP tool calls. `prostate-modality-level-neural-representations.json` holds
precomputed image embeddings (see [Feature embeddings](#feature-embeddings)).


### Inputs

`structured-prompt.json` — the flat structured record the agent always sees (no tool
call needed): identifiers, encounter, headline values, and the structured
clinical panel. Tasks 1 and 2 use the same general structure; Task 2 adds the biopsy fields. Task 3 uses a simplified structure described below.

| Key | Type | Tasks | Notes |
|---|---|---|---|
| `case_id` | str | 1, 2 | matches the directory name |
| `task` | int | 1, 2 | `1` (biopsy decision) or `2` (treatment decision) |
| `psa`, `age`, `months` | num | 1, 2 | headline PSA, age, months since last PSA |
| `pirads`, `psad`, `psav`, `psap`, `vol`, `cspca` | mixed | 1, 2 | PI-RADS, PSA density / velocity / prior, prostate volume, csPCa prob |
| `dre`, `bx` | str | 1, 2 | DRE findings, prior-biopsy bucket |
| `medhx`, `meds`, `notes`, `pmhx`, `allergies` | mixed | 1, 2 | medical history, medication, summary notes |
| `vitals` | object | 1, 2 | `weight`, `height`, `bmi`, `bp`, `hr`, `smoking`, … |
| `enc_dept`, `enc_date`, `enc_ref`, `enc_type` | str | 1, 2 | encounter |
| `note_sections` | list | 1, 2 | `[{s: "Chief complaint", t: "..."}, ...]` |
| `occupation`, `marital`, `living`, `next_of_kin`, `alcohol`, `exercise`, `ipss`, `recent_other`, `admin` | mixed | 1, 2 | social / lifestyle / admin |
| `ct`, `bx_isup`, `bx_gl_prim`, `bx_gl_sec`, `bx_gl_tert` | mixed | 2 | clinical stage + biopsy Gleason / ISUP |

There is no `query` field — the task question is fixed per task in the
prompt template (`templates/prompts/agent_prompt.j2`).

`*-clinical-data.json` — the task-specific clinical data file used by the MCP server when the agent calls a tool. The agent only sees these if it actually invokes the
matching tool (see [docs/architecture.md](docs/architecture.md)).

| Key | Type | Tasks | Served by |
|---|---|---|---|
| `radiology_report` | str | 1, 2, 3 | `get_mri_report` |
| `pathology_report` | str | 2, 3 | `get_pathology_report` (task 1: no prior pathology → "no data") |
| `surgical_pathology_report` | str | 3 | `get_surgical_pathology_report` |
| `previous_notes` | list | 1, 2, 3 | `get_previous_notes` |
| `laboratory_results` | list | 1, 2 | `get_lab_results` |
| `psa_trend` | list | 1, 2 | `get_psa_trend` |
| `family_history` | str | 1, 2, 3 | `get_family_history` |

Tasks 1 and 2 share the same six tools, while Task 3 uses five tools. To **add** a clinical tool, register a new `ToolSpec` in
`src/chimera_agent_baseline/tools/definitions.py` and append it to the
appropriate `TASK1_TOOLS`, `TASK2_TOOLS`, or `TASK3_TOOLS` registry. The new
tool's `fields` map to one or more `*-clinical-data.json` keys.

### Feature embeddings

Each patient ships a single `prostate-modality-level-neural-representations.json` with frozen foundation-model
embeddings, separated by origin (JSON attribute). Each origin holds a
**list of feature vectors** (a list of JSON arrays) — including MRI, which
is a single-element list — so loading is uniform:

```jsonc
{
  "MRI image":           [[...]],                  // one vector, all tasks; otherwise []
  "Biopsy slide":        [[...], [...], [...]],   // one to three vectors, tasks 2 & 3; otherwise []
  "Prostatectomy slide": [[...], [...], [...]]    // one to three vectors, task 3 only; otherwise []
}
```
| Origin | Vectors | Tasks |
|---|---|---|
| `MRI image` | one feature vector | 1, 2, 3 |
| `Biopsy slide` | one to three feature vectors | 2, 3 |
| `Prostatectomy slide` | one to three feature vectors | 3 |

The vectors are raw foundation-model output (e.g. 960-d) and are **not**
meant to enter the LLM context directly — build a predictor or tool on top
and feed the agent a compact score/label. The **baseline does not consume
features**; a decoupled loader is provided for participants who want to:
`chimera_agent_baseline.features.FeatureStore` (indexes `prostate-modality-level-neural-representations.json` by
`case_id`; `get(case_id)` / `get_origin(case_id, origin)`).

An **opt-in predictor tool template** (`tools/predictor.py`, off by default)
shows the full no-leak wiring end to end. Enable it with
`agent.predictor.enabled=true` to expose a `get_image_predictor` MCP tool that
loads the embeddings, runs the model, and returns only a compact score. The
stub `run_predictor` receives **all** of a patient's embeddings (MRI / Biopsy slide /
Prostatectomy slide, whichever are present), so you can use one origin or fuse them
multimodally — replace it with your trained head (e.g. a Cox head over the
fused features for task 3, a classifier for tasks 1 / 2).

### Outputs

The structured submission contract is the Pydantic models in
`src/chimera_agent_baseline/output/schema.py` (`Task1Output`,
`Task2Output`, `Task3Output`). Each case produces two task-specific JSON output files;
outputs that don't validate are rejected. Enum tokens are lowercase, to
match the urologist forms.

#### Task 1: Biopsy decision

Each Task 1 case produces two JSON files under
`/output/task1/<case_id>/`.

```jsonc
// /output/task1/PT-XXXX/prostate-biopsy-decision.json

"yes" // yes | no
```

```jsonc
// /output/task1/PT-XXXX/prostate-biopsy-decision-reasoning.json

{
  "confidence": "clear", // clear | borderline | uncertain

  "variable_weights": {
    "age": "important",       // not_used | noted | important | decisive
    "fh": "noted",            // not_used | noted | important | decisive
    "cspca": "not_used",      // not_used | noted | important | decisive
    "pirads": "important",    // not_used | noted | important | decisive
    "vol": "noted",           // not_used | noted | important | decisive
    "psa": "noted",           // not_used | noted | important | decisive
    "comorbidity": "noted",   // not_used | noted | important | decisive
    "psad": "not_used",       // not_used | noted | important | decisive
    "dre": "noted",           // not_used | noted | important | decisive
    "bx": "decisive"          // not_used | noted | important | decisive
  },

  "free_text": "The decision is driven by three critical factors: the PI-RADS 5 score, the extremely high csPCa predicted probability (0.96), and the frankly elevated PSA level (187.0 ng/mL) with a rapid upward trend." // free-text explanation of the main factors driving the decision
}
```

#### Task 2: Treatment decision

Each Task 2 case produces two JSON files under
`/output/task2/<case_id>/`.

```jsonc
// /output/task2/PT-XXXX/prostate-treatment-decision.json

"active_surveillance" // active_surveillance | continued_surveillance | watchful_waiting | active_treatment
```

```jsonc
// /output/task2/PT-XXXX/prostate-treatment-decision-reasoning.json

{
  "confidence": "clear", // clear | borderline | uncertain

  "variable_weights": {
    "bx_gl_prim": "important",  // not_used | noted | important | decisive
    "pirads": "decisive",       // not_used | noted | important | decisive
    "bx_isup": "decisive",      // not_used | noted | important | decisive
    "ct": "important",          // not_used | noted | important | decisive
    "fh": "noted",              // not_used | noted | important | decisive
    "comorbidity": "noted",     // not_used | noted | important | decisive
    "psa": "important",         // not_used | noted | important | decisive
    "bx_gl_sec": "important",   // not_used | noted | important | decisive
    "age": "not_used",          // not_used | noted | important | decisive
    "psad": "important",        // not_used | noted | important | decisive
    "cspca": "not_used"         // not_used | noted | important | decisive
  },

  "free_text": "Patient with GG 1 on active surveillance, negative follow-up imaging." // free-text explanation of the main factors driving the treatment decision
}
```

#### Task 3: Time to recurrence or last follow-up

Each Task 3 case produces two JSON files under `/output/task3/<case_id>/`.

```jsonc
// /output/task3/PT-XXXX/prostate-time-to-recurrence-or-last-follow-up.json

{
  "months_to_recurrence": 65.7, // predicted time in months
  "event": 0                     // 0 | 1
}
```

```jsonc
// /output/task3/PT-XXXX/prostate-time-to-recurrence-or-last-follow-up-reasoning.json

"The estimated time is based on the patient's clinical history, PSA measurements, imaging findings, and available pathology reports."
```
Task 3 (recurrence prognosis) uses a simplified
`structured-prompt.json` containing `case_id`, `task`, `age`, `psa`, `dre`,
and `active_treatment_prior_to_surgery`.

For each patient, the clinical information for Task 3 is stored in
`prostate-time-to-recurrence-or-last-follow-up-clinical-data.json` and is
made available to the agent through five clinical tools. The file contains
`radiology_report`, `pathology_report` (biopsy),
`surgical_pathology_report`, `previous_notes`, and `family_history`.
Fields may be `null` when the corresponding information is unavailable.

### Docker invocation

The Grand Challenge container reads the input root at `/input` (holding
`task<N>/agent_input/<case>/`) and writes `/output`:

```bash
docker run --rm --gpus all \
    -v $PWD/data:/input:ro \
    -v $PWD/test/output:/output \
    chimera-agent-baseline
```

`make gc-test` is exactly this with the housekeeping (cleans
`test/output/`, sets perms, tags the image). The `:ro` on `/input`
matches the GC platform — your container must not write to it.

### Grand Challenge platform execution

The Docker command above tests the baseline with the full internal task tree
mounted at `/input`. In this local/full-tree mode, inputs are expected under
`/input/task<N>/agent_input/<case_id>/`, and outputs are written under
`/output/task<N>/<case_id>/`.

On the Grand Challenge platform, the algorithm is executed for one case at a
time. The platform provides `inputs.json` and the task-specific JSON files
directly under `/input`. The adapted `inference.py` reads these flat GC input
files, creates the internal `task<N>/agent_input/<case_id>/` structure
temporarily, runs the baseline, and copies the required task-specific output
JSON files back to `/output`.

## Documentation

| Document | What it covers |
|---|---|
| [User Manual](docs/user-manual.md) | Setup, commands, troubleshooting |
| [Architecture](docs/architecture.md) | Components, ReAct + form-fill graph |
| [Model Configuration](docs/models.md) | Swapping models, providers, experiment overlays |
| [Challenge Tasks](docs/chimera.md) | Task definitions, inputs, outputs |

## What to change

| Goal | Where |
|---|---|
| Change the system prompt | `src/chimera_agent_baseline/agent/prompts.py` |
| Edit the case prompt template | `templates/prompts/agent_prompt.j2` |
| Add a clinical tool | `src/chimera_agent_baseline/tools/definitions.py` (`TASK1_TOOLS` / `TASK2_TOOLS` / `TASK3_TOOLS`) |
| Wire an embedding predictor | `src/chimera_agent_baseline/tools/predictor.py` (`run_predictor`) + `agent.predictor.*` |
| Swap the LLM (vLLM) | `configs/config.yaml` → `model.model_id`, `model.tool_parser` |
| Swap the LLM (OpenAI-compatible) | `configs/experiment/qwen_local.yaml` (`+experiment=qwen_local`) |
| Change the agent loop | `src/chimera_agent_baseline/agent/graph.py` |
| Tune the form-fill prompt / retry | `src/chimera_agent_baseline/agent/form_fill.py` |
| Rebuild the RAG corpus | `scripts/process_guidelines.py` |

## What NOT to change

One piece is part of the challenge contract — submissions that violate it
are rejected. Everything else (system prompt, tools, models, agent graph,
form-fill node, configs, even the entry-point if you want) is fair game.
Only the final structured output is evaluated; tool use is not scored.

| File | Why it's locked |
|---|---|
| `src/chimera_agent_baseline/output/schema.py` | The Pydantic submission shape. The final JSON must validate against `Task1Output` / `Task2Output` / `Task3Output`. |
