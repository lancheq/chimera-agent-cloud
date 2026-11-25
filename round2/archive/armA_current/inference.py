"""Grand Challenge entrypoint.

Reads inputs from ``/input`` (the same ``task<N>/agent_input/<case>/``
hierarchy used locally), runs every task present, and writes structured
predictions to ``/output/task<N>/<case>/prediction.json``. Thin wrapper around
:func:`chimera_agent_baseline.run.run_agent` that loads the same
``configs/config.yaml`` Hydra reads locally and overrides the path fields to
the GC container's mount points.

For local development, use ``make run`` instead.
"""

"""
The following is a simple example algorithm.

It is meant to run within a container.

To run the container locally, you can call the following bash script:

  ./do_test_run.sh

This will start the inference and reads from ./test/input and writes to ./test/output

To save the container and prep it for upload to Grand-Challenge.org you can call:

  ./do_save.sh

Any container that shows the same behaviour will do, this is purely an example of how one COULD do it.

Reference the documentation to get details on the runtime environment on the platform:
https://grand-challenge.org/documentation/runtime-environment/

Happy programming!
"""

import json
from pathlib import Path

import torch

import asyncio
import logging
from pathlib import Path
import tempfile

from omegaconf import OmegaConf

from chimera_agent_baseline.rag import start_embedding_service
from chimera_agent_baseline.run import run_agent
from chimera_agent_baseline.utils import setup_logging



log = logging.getLogger(__name__)
INPUT_PATH = Path("/input")
OUTPUT_PATH = Path("/output")
#RESOURCE_PATH = Path("resources")

CONFIG_PATH = Path("/opt/app/configs/config.yaml")
RESOURCE_PATH = Path("/opt/app/resources")

MODEL_PATH = Path("/opt/ml/model/gemma-4-E2B-it")
EMBEDDING_MODEL_PATH = Path("/opt/ml/model/embedding_model")

RUN_CHIMERA_BASELINE = True

def run():
    # The key is a tuple of the slugs of the input sockets
    interface_key = get_interface_key()

    # Lookup the handler for this particular set of sockets (i.e. the interface)
    handler = {
        (
            "prostate-biopsy-decision-clinical-data",
            "prostate-modality-level-neural-representations",
            "structured-prompt",
        ): interf0_handler,
        (
            "prostate-modality-level-neural-representations",
            "prostate-treatment-decision-clinical-data",
            "structured-prompt",
        ): interf1_handler,
        (
            "prostate-modality-level-neural-representations",
            "prostate-time-to-recurrence-or-last-follow-up-clin",
            "structured-prompt",
        ): interf2_handler,
    }[interface_key]

    # Call the handler
    return handler()


def interf0_handler():
    # Read the input

    input_structured_prompt = load_json_file(
        location=INPUT_PATH / "structured-prompt.json",
    )

    input_prostate_modality_level_neural_representations = load_json_file(
        location=INPUT_PATH / "prostate-modality-level-neural-representations.json",
    )

    input_prostate_biopsy_decision_clinical_data = load_json_file(
        location=INPUT_PATH / "prostate-biopsy-decision-clinical-data.json",
    )
    # Run the real CHIMERA baseline. If set to False, the original GC dummy
    # example below can still be used as a minimal reference implementation.
    if RUN_CHIMERA_BASELINE:
        return run_baseline_for_gc_interface(
            task=1,
            structured_prompt=input_structured_prompt,
            clinical_data=input_prostate_biopsy_decision_clinical_data,
            neural_representations=input_prostate_modality_level_neural_representations,
        )
    
    # Process the inputs: any way you'd like, here we show-case torch
    _show_torch_cuda_info()

    # Example how to set torch to use the GPU (if available)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    
    model = torch.nn.Linear(10, 1).to(device)
    input = torch.randn(1, 10).to(device)
    output = model(input)

    # Your model will be extracted to the `model_dir` at runtime on Grand Challenge
    # Note: when testing locally, the local `./model` directory is mounted here.
    # Eventually, you should upload it as a tarball to Grand Challenge!
    # Go to Algorithm and upload it under Models.
    model_dir = Path("/opt/ml/model")
    with open(
        model_dir / "a_tarball_subdirectory" / "some_tarball_resource.txt", "r"
    ) as f:
        print(f.read())

    # For now, let us make bogus predictions

    output_prostate_biospy_decision = "yes"

    output_prostate_biospy_decision_reasoning = {
        "free_text": "The decision is driven by three critical factors: the PI-RADS 5 score, the extremely high csPCa predicted probability (0.96), and the frankly elevated PSA level (187.0 ng/mL) with a rapid upward trend.",
        "confidence": "clear",
        "variable_weights": {
            "bx": "decisive",
            "fh": "noted",
            "age": "important",
            "dre": "noted",
            "psa": "noted",
            "vol": "noted",
            "psad": "not_used",
            "cspca": "not_used",
            "pirads": "important",
            "comorbidity": "noted",
        },
    }

    # Save your output

    write_json_file(
        location=OUTPUT_PATH / "prostate-biospy-decision.json",
        content=output_prostate_biospy_decision,
    )

    write_json_file(
        location=OUTPUT_PATH / "prostate-biospy-decision-reasoning.json",
        content=output_prostate_biospy_decision_reasoning,
    )

    return 0


def interf1_handler():
    # Read the input

    input_structured_prompt = load_json_file(
        location=INPUT_PATH / "structured-prompt.json",
    )

    input_prostate_modality_level_neural_representations = load_json_file(
        location=INPUT_PATH / "prostate-modality-level-neural-representations.json",
    )

    input_prostate_treatment_decision_clinical_data = load_json_file(
        location=INPUT_PATH / "prostate-treatment-decision-clinical-data.json",
    )
    # Run the real CHIMERA baseline. If set to False, the original GC dummy
    # example below can still be used as a minimal reference implementation.
    if RUN_CHIMERA_BASELINE:
        return run_baseline_for_gc_interface(
            task=2,
            structured_prompt=input_structured_prompt,
            clinical_data=input_prostate_treatment_decision_clinical_data,
            neural_representations=input_prostate_modality_level_neural_representations,
        )
    
    # Process the inputs: any way you'd like, here we show-case torch
    _show_torch_cuda_info()

    # Example how to set torch to use the GPU (if available)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    model = torch.nn.Linear(10, 1).to(device)
    input = torch.randn(1, 10).to(device)
    output = model(input)

    # Your model will be extracted to the `model_dir` at runtime on Grand Challenge
    # Note: when testing locally, the local `./model` directory is mounted here.
    # Eventually, you should upload it as a tarball to Grand Challenge!
    # Go to Algorithm and upload it under Models.
    model_dir = Path("/opt/ml/model")
    with open(
        model_dir / "a_tarball_subdirectory" / "some_tarball_resource.txt", "r"
    ) as f:
        print(f.read())

    # For now, let us make bogus predictions

    output_prostate_treatment_decision = "watchful_waiting"

    output_prostate_treatment_decision_reasoning = {
        "free_text": "The decision to proceed to active treatment is driven primarily by the confluence of high-grade pathology (Gleason 4+4 with PNI), high-risk imaging findings (PI-RADS 5 with seminal vesicle invasion), and aggressive biochemical progression indicated by the rapid PSA escalation.",
        "confidence": "clear",
        "variable_weights": {
            "ct": "important",
            "fh": "noted",
            "age": "important",
            "psa": "important",
            "psad": "noted",
            "cspca": "noted",
            "pirads": "decisive",
            "bx_isup": "decisive",
            "bx_gl_sec": "noted",
            "bx_gl_prim": "noted",
            "comorbidity": "noted",
        },
    }

    # Save your output

    write_json_file(
        location=OUTPUT_PATH / "prostate-treatment-decision.json",
        content=output_prostate_treatment_decision,
    )

    write_json_file(
        location=OUTPUT_PATH / "prostate-treatment-decision-reasoning.json",
        content=output_prostate_treatment_decision_reasoning,
    )

    return 0


def interf2_handler():
    # Read the input

    input_structured_prompt = load_json_file(
        location=INPUT_PATH / "structured-prompt.json",
    )

    input_prostate_modality_level_neural_representations = load_json_file(
        location=INPUT_PATH / "prostate-modality-level-neural-representations.json",
    )

    input_prostate_time_to_recurrence_or_last_follow_up_clin = load_json_file(
        location=INPUT_PATH
        / "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
    )

    # Run the real CHIMERA baseline. If set to False, the original GC dummy
    # example below can still be used as a minimal reference implementation.
    if RUN_CHIMERA_BASELINE:
        return run_baseline_for_gc_interface(
            task=3,
            structured_prompt=input_structured_prompt,
            clinical_data=input_prostate_time_to_recurrence_or_last_follow_up_clin,
            neural_representations=input_prostate_modality_level_neural_representations,
        )

    # Process the inputs: any way you'd like, here we show-case torch
    _show_torch_cuda_info()

    # Example how to set torch to use the GPU (if available)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    model = torch.nn.Linear(10, 1).to(device)
    input = torch.randn(1, 10).to(device)
    output = model(input)

    # Your model will be extracted to the `model_dir` at runtime on Grand Challenge
    # Note: when testing locally, the local `./model` directory is mounted here.
    # Eventually, you should upload it as a tarball to Grand Challenge!
    # Go to Algorithm and upload it under Models.
    model_dir = Path("/opt/ml/model")
    with open(
        model_dir / "a_tarball_subdirectory" / "some_tarball_resource.txt", "r"
    ) as f:
        print(f.read())

    # For now, let us make bogus predictions

    output_prostate_time_to_recurrence_or_last_follow_up_reas = "High-risk pathology (lymph node metastasis, pT4b), positive surgical margins, and seminal vesicle invasion drive the prediction. These aggressive features significantly elevate the immediate risk of biochemical recurrence post-radical prostatectomy."

    output_prostate_time_to_recurrence_or_last_follow_up = {
        "event": 0,
        "months_to_recurrence": 31.4,
    }

    # Save your output

    write_json_file(
        location=OUTPUT_PATH
        / "prostate-time-to-recurrence-or-last-follow-up-reasoning.json",
        content=output_prostate_time_to_recurrence_or_last_follow_up_reas,
    )

    write_json_file(
        location=OUTPUT_PATH / "prostate-time-to-recurrence-or-last-follow-up.json",
        content=output_prostate_time_to_recurrence_or_last_follow_up,
    )

    return 0


def get_interface_key():
    # The inputs.json is a system generated file that contains information about
    # the inputs that interface with the algorithm
    inputs = load_json_file(
        location=INPUT_PATH / "inputs.json",
    )
    socket_slugs = [sv["socket"]["slug"] for sv in inputs]
    return tuple(sorted(socket_slugs))


def load_json_file(*, location):
    # Reads a json file
    with open(location) as f:
        return json.loads(f.read())


def write_json_file(*, location, content):
    # Writes a json file
    with open(location, "w") as f:
        f.write(json.dumps(content, indent=4))


def _show_torch_cuda_info():
    print("=+=" * 10)
    print("Collecting Torch CUDA information")
    print(f"Torch CUDA is available: {(available := torch.cuda.is_available())}")
    if available:
        print(f"\tnumber of devices: {torch.cuda.device_count()}")
        print(f"\tcurrent device: { (current_device := torch.cuda.current_device())}")
        print(f"\tproperties: {torch.cuda.get_device_properties(current_device)}")
    print("=+=" * 10)


def load_config(internal_input_root: Path, internal_output_root: Path, task: int):
    """Load config and override paths for one GC interface run."""
    cfg = OmegaConf.load(CONFIG_PATH)

    OmegaConf.update(cfg, "paths.data_root", str(internal_input_root))
    OmegaConf.update(cfg, "paths.output_dir", str(internal_output_root))
    OmegaConf.update(cfg, "paths.resource_dir", str(RESOURCE_PATH))
    OmegaConf.update(cfg, "paths.model_dir", str(MODEL_PATH))
    OmegaConf.update(cfg, "paths.embedding_model_dir", str(EMBEDDING_MODEL_PATH))

    OmegaConf.update(cfg, "agent.tasks", [task])
    OmegaConf.update(cfg, "agent.pids", None)
    OmegaConf.update(cfg, "agent.limit", None)
    OmegaConf.update(cfg, "agent.step_timeout", 900)

    return cfg



def run_baseline_for_gc_interface(
    *,
    task: int,
    structured_prompt: dict,
    clinical_data: dict,
    neural_representations: dict,
) -> int:
    """Run the baseline from flat GC /input files and write flat /output files."""

    setup_logging("INFO")

    case_id = structured_prompt.get("case_id", "gc-case")

    clinical_filename_by_task = {
        1: "prostate-biopsy-decision-clinical-data.json",
        2: "prostate-treatment-decision-clinical-data.json",
        3: "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
    }
    clinical_filename = clinical_filename_by_task[task]
    structured_prompt = dict(structured_prompt)
    clinical_data = dict(clinical_data)
    neural_representations = dict(neural_representations)

    structured_prompt.setdefault("case_id", case_id)
    structured_prompt.setdefault("task", task)
    clinical_data.setdefault("case_id", case_id)
    neural_representations.setdefault("case_id", case_id)

    with tempfile.TemporaryDirectory(prefix="chimera-gc-") as tmp:
        tmp_root = Path(tmp)

        internal_input_root = tmp_root / "input"
        internal_output_root = tmp_root / "output"

        case_dir = internal_input_root / f"task{task}" / "agent_input" / case_id
        case_dir.mkdir(parents=True, exist_ok=True)

        write_json_file(
            location=case_dir / "structured-prompt.json",
            content=structured_prompt,
        )
        write_json_file(
            location=case_dir / clinical_filename,
            content=clinical_data,
        )
        write_json_file(
            location=case_dir / "prostate-modality-level-neural-representations.json",
            content=neural_representations,
        )

        cfg = load_config(
            internal_input_root=internal_input_root,
            internal_output_root=internal_output_root,
            task=task,
        )

        log.info("Running CHIMERA baseline for task=%s case_id=%s", task, case_id)

        embed_svc = start_embedding_service(cfg.paths.embedding_model_dir)
        try:
            asyncio.run(run_agent(cfg))
        finally:
            if embed_svc:
                embed_svc.stop()

        produced_dir = internal_output_root / f"task{task}" / case_id
        if not produced_dir.is_dir():
            raise FileNotFoundError(f"No output produced at {produced_dir}")

        for json_file in produced_dir.glob("*.json"):
            content = load_json_file(location=json_file)
            write_json_file(
                location=OUTPUT_PATH / json_file.name,
                content=content,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(run())






