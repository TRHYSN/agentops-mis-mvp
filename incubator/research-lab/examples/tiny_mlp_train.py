from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any

from research_lab.runtime import artifacts_dir, log_metric, record_actuals


INPUT_SIZE = 2
HIDDEN_SIZE = 8
OUTPUT_SIZE = 2
TRAIN_SIZE = 192
VALIDATION_SIZE = 64
EPOCHS = 250
LEARNING_RATE = 0.2
DATASET_VERSION = "synthetic-xor-v1"
MODEL_ARCHITECTURE = "mlp-2x8x2-tanh-softmax"
INITIALIZATION_MODE = "from_scratch"
TRAINING_SCOPE = "synthetic_classification"
CHECKPOINT_SELECTION_RULE = "final_epoch"
METRIC_CODE_VERSION = "accuracy-v1"


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _make_dataset(seed: int, size: int) -> list[tuple[list[float], int]]:
    rng = random.Random(seed)
    samples: list[tuple[list[float], int]] = []
    for index in range(size):
        quadrant = index % 4
        magnitude_x = rng.uniform(0.25, 1.0)
        magnitude_y = rng.uniform(0.25, 1.0)
        sign_x = 1.0 if quadrant in {0, 1} else -1.0
        sign_y = 1.0 if quadrant in {0, 2} else -1.0
        features = [sign_x * magnitude_x, sign_y * magnitude_y]
        label = int(sign_x == sign_y)
        samples.append((features, label))
    rng.shuffle(samples)
    return samples


def _initialize_matrix(
    rng: random.Random,
    rows: int,
    columns: int,
    limit: float,
) -> list[list[float]]:
    return [
        [rng.uniform(-limit, limit) for _ in range(columns)]
        for _ in range(rows)
    ]


def _softmax(logits: list[float]) -> list[float]:
    maximum = max(logits)
    exponentials = [math.exp(value - maximum) for value in logits]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def _forward(
    features: list[float],
    weights_input: list[list[float]],
    bias_hidden: list[float],
    weights_output: list[list[float]],
    bias_output: list[float],
) -> tuple[list[float], list[float]]:
    hidden = [
        math.tanh(
            sum(weight * feature for weight, feature in zip(row, features, strict=True))
            + bias
        )
        for row, bias in zip(weights_input, bias_hidden, strict=True)
    ]
    logits = [
        sum(weight * value for weight, value in zip(row, hidden, strict=True)) + bias
        for row, bias in zip(weights_output, bias_output, strict=True)
    ]
    return hidden, _softmax(logits)


def _accuracy(
    samples: list[tuple[list[float], int]],
    weights_input: list[list[float]],
    bias_hidden: list[float],
    weights_output: list[list[float]],
    bias_output: list[float],
) -> float:
    correct = 0
    for features, label in samples:
        _, probabilities = _forward(
            features,
            weights_input,
            bias_hidden,
            weights_output,
            bias_output,
        )
        correct += int(max(range(OUTPUT_SIZE), key=probabilities.__getitem__) == label)
    return correct / len(samples)


def train(seed: int) -> dict[str, Any]:
    dataset = _make_dataset(seed, TRAIN_SIZE + VALIDATION_SIZE)
    training = dataset[:TRAIN_SIZE]
    validation = dataset[TRAIN_SIZE:]

    rng = random.Random(seed + 10_000)
    input_limit = math.sqrt(6.0 / (INPUT_SIZE + HIDDEN_SIZE))
    output_limit = math.sqrt(6.0 / (HIDDEN_SIZE + OUTPUT_SIZE))
    weights_input = _initialize_matrix(rng, HIDDEN_SIZE, INPUT_SIZE, input_limit)
    bias_hidden = [0.0] * HIDDEN_SIZE
    weights_output = _initialize_matrix(rng, OUTPUT_SIZE, HIDDEN_SIZE, output_limit)
    bias_output = [0.0] * OUTPUT_SIZE
    final_loss = 0.0

    for epoch in range(1, EPOCHS + 1):
        gradient_input = [[0.0] * INPUT_SIZE for _ in range(HIDDEN_SIZE)]
        gradient_hidden_bias = [0.0] * HIDDEN_SIZE
        gradient_output = [[0.0] * HIDDEN_SIZE for _ in range(OUTPUT_SIZE)]
        gradient_output_bias = [0.0] * OUTPUT_SIZE
        loss = 0.0

        for features, label in training:
            hidden, probabilities = _forward(
                features,
                weights_input,
                bias_hidden,
                weights_output,
                bias_output,
            )
            loss -= math.log(max(probabilities[label], 1e-12))
            output_delta = list(probabilities)
            output_delta[label] -= 1.0

            for output_index in range(OUTPUT_SIZE):
                gradient_output_bias[output_index] += output_delta[output_index]
                for hidden_index in range(HIDDEN_SIZE):
                    gradient_output[output_index][hidden_index] += (
                        output_delta[output_index] * hidden[hidden_index]
                    )

            for hidden_index in range(HIDDEN_SIZE):
                hidden_delta = (
                    sum(
                        weights_output[output_index][hidden_index]
                        * output_delta[output_index]
                        for output_index in range(OUTPUT_SIZE)
                    )
                    * (1.0 - hidden[hidden_index] ** 2)
                )
                gradient_hidden_bias[hidden_index] += hidden_delta
                for input_index in range(INPUT_SIZE):
                    gradient_input[hidden_index][input_index] += (
                        hidden_delta * features[input_index]
                    )

        scale = LEARNING_RATE / len(training)
        for hidden_index in range(HIDDEN_SIZE):
            bias_hidden[hidden_index] -= scale * gradient_hidden_bias[hidden_index]
            for input_index in range(INPUT_SIZE):
                weights_input[hidden_index][input_index] -= (
                    scale * gradient_input[hidden_index][input_index]
                )
        for output_index in range(OUTPUT_SIZE):
            bias_output[output_index] -= scale * gradient_output_bias[output_index]
            for hidden_index in range(HIDDEN_SIZE):
                weights_output[output_index][hidden_index] -= (
                    scale * gradient_output[output_index][hidden_index]
                )

        final_loss = loss / len(training)
        if epoch == 1 or epoch % 25 == 0 or epoch == EPOCHS:
            log_metric("train_loss", final_loss, step=epoch)
            log_metric(
                "validation_accuracy",
                _accuracy(
                    validation,
                    weights_input,
                    bias_hidden,
                    weights_output,
                    bias_output,
                ),
                step=epoch,
            )

    train_accuracy = _accuracy(
        training,
        weights_input,
        bias_hidden,
        weights_output,
        bias_output,
    )
    validation_accuracy = _accuracy(
        validation,
        weights_input,
        bias_hidden,
        weights_output,
        bias_output,
    )
    model = {
        "format": "research-lab-safe-mlp-v1",
        "architecture": MODEL_ARCHITECTURE,
        "activation": "tanh",
        "output": "softmax",
        "seed": seed,
        "weights_input": weights_input,
        "bias_hidden": bias_hidden,
        "weights_output": weights_output,
        "bias_output": bias_output,
    }
    return {
        "model": model,
        "train_loss": final_loss,
        "train_accuracy": train_accuracy,
        "validation_accuracy": validation_accuracy,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a dependency-free tiny MLP.")
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()

    result = train(args.seed)
    output_dir = artifacts_dir()
    model_path = output_dir / "tiny_mlp_model.json"
    summary_path = output_dir / "tiny_mlp_summary.json"
    model_hash = _sha256_json(result["model"])

    _write_json(model_path, result["model"])
    summary = {
        "artifact_format": "research-lab-safe-summary-v1",
        "dataset_version": DATASET_VERSION,
        "epochs": EPOCHS,
        "model_architecture": MODEL_ARCHITECTURE,
        "model_sha256": model_hash,
        "seed": args.seed,
        "train_accuracy": result["train_accuracy"],
        "train_loss": result["train_loss"],
        "validation_accuracy": result["validation_accuracy"],
    }
    _write_json(summary_path, summary)

    record_actuals(
        seed=args.seed,
        initialization_mode=INITIALIZATION_MODE,
        training_scope=TRAINING_SCOPE,
        dataset_version=DATASET_VERSION,
        model_architecture=MODEL_ARCHITECTURE,
        checkpoint_selection_rule=CHECKPOINT_SELECTION_RULE,
        metric_code_hash=f"sha256:{hashlib.sha256(METRIC_CODE_VERSION.encode()).hexdigest()}",
        train_size=TRAIN_SIZE,
        validation_size=VALIDATION_SIZE,
        epochs=EPOCHS,
        learning_rate=LEARNING_RATE,
        train_accuracy=result["train_accuracy"],
        train_loss=result["train_loss"],
        validation_accuracy=result["validation_accuracy"],
        model_artifact=model_path.name,
        model_sha256=model_hash,
        summary_artifact=summary_path.name,
        raw_samples_omitted=True,
        credentials_omitted=True,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "seed": args.seed,
                "validation_accuracy": result["validation_accuracy"],
                "model_artifact": model_path.name,
                "summary_artifact": summary_path.name,
                "raw_samples_omitted": True,
                "credentials_omitted": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
