# Third-party components

This repository contains our submission to the
[CHIMERA-agent challenge](https://chimera-agent.grand-challenge.org/). It is
released under the Apache License 2.0 (`LICENSE`). It builds on the organizers'
baseline and on third-party models and libraries, listed here for licence
compliance (challenge rule: all external data and pre-trained models must be
declared, and must be freely and publicly available under a permissive or
open-weight licence).

## External models and data used at inference time

| Component | Role | Licence / terms | Source |
|---|---|---|---|
| Qwen3.6-35B-A3B (Q4\_K\_M GGUF) | decision + reasoning language model, served locally by llama.cpp on loopback | Qwen licence (open weights) | model slot, shipped separately from the image |
| embeddinggemma-300m | text embedding model for the local guideline vector index | Gemma terms of use (open weights) | model slot |
| EAU Guidelines on Prostate Cancer | guideline corpus, chunked into the local vector index | external clinical guideline, redistributed by the challenge organizers / cited to source | `resources/guidelines_db/` |
| PI-RADS v2.1 | risk-stratification rules referenced in prompts | published guideline (cited, not redistributed) | --- |
| CHIMERA-agent official MCP tool interface | required tool access layer | provided by the organizers | used as-is, unmodified communication interface |

No CHIMERA-agent training or evaluation data is redistributed in this
repository. Model weights are **not** committed here; they travel in the
separate model archive that the platform mounts at `/opt/ml/model`.

## Libraries

| Library | Licence |
|---|---|
| llama.cpp / llama-cpp-python | MIT |
| LangGraph / LangChain | MIT |
| scikit-learn | BSD-3-Clause |
| ChromaDB | Apache-2.0 |
| PyTorch | BSD-3-Clause |
| MCP Python SDK | MIT |
| Hydra / OmegaConf | MIT / Apache-2.0 |

A CPU backend shared object is vendored under `vendor/` to make the container
run on the platform's older instruction set (see `docs/`); it is a build of the
MIT-licensed llama.cpp CPU backend.

## Upstream baseline

The initial code structure, the per-case input/output contract and parts of the
agent graph derive from the organizers' baseline repository
(`DIAGNijmegen/chimera-agent-baseline`). Those parts remain subject to their
original terms; our modifications are licensed under Apache-2.0 as above.
