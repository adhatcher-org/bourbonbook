from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from bourbonbook.analysis import analyze_bottle
from bourbonbook.config import Settings

IMAGE_PATTERN = re.compile(r"^image:\s*!\[[^]]*]\(([^)]+)\)\s*$", re.IGNORECASE)
VALUE_PATTERN = re.compile(r"^([a-z_]+):\s*(.*?)\s*$", re.IGNORECASE)
SCORED_FIELDS = {"name", "brand", "fill_level", "status"}
NUMERIC_FIELDS = {"proof", "abv", "fill_level"}


def parse_validation(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    current_image: str | None = None
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        image_match = IMAGE_PATTERN.match(line)
        if image_match:
            current_image = image_match.group(1)
            records[current_image] = {}
            continue
        value_match = VALUE_PATTERN.match(line)
        if current_image and value_match and value_match.group(1).lower() != "image":
            records[current_image][value_match.group(1).lower()] = value_match.group(2)
    return records


def numeric(value: Any) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def normalized(value: Any) -> str:
    value = str(value).lower().replace("’", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def values_match(field: str, expected: Any, actual: Any) -> bool:
    if field in NUMERIC_FIELDS:
        expected_number, actual_number = numeric(expected), numeric(actual)
        return (
            expected_number is not None
            and actual_number is not None
            and abs(expected_number - actual_number) <= (10 if field == "fill_level" else 0.5)
        )
    expected_text, actual_text = normalized(expected), normalized(actual)
    if field == "mash_bill":
        expected_tokens = set(expected_text.split()) - {"buffale", "trace", "mash", "bill"}
        actual_tokens = set(actual_text.split()) - {"buffalo", "trace", "mash", "bill"}
        return bool(expected_tokens) and expected_tokens <= actual_tokens
    if field in {"name", "spirit_type"}:
        expected_tokens, actual_tokens = set(expected_text.split()), set(actual_text.split())
        return expected_tokens <= actual_tokens or actual_tokens <= expected_tokens
    return expected_text == actual_text


async def evaluate(images_dir: Path, settings: Settings) -> dict[str, Any]:
    records = parse_validation(images_dir / "ImageTestValidation.md")
    present_images = {path.name for path in images_dir.glob("*.jpeg")}
    missing_images = sorted(set(records) - present_images)
    unvalidated_images = sorted(present_images - set(records))
    results = []
    total_correct = 0
    total_fields = 0
    for image_name, expected in records.items():
        image_path = images_dir / image_name
        if not image_path.exists():
            continue
        print(f"Analyzing {image_name}…", flush=True)
        actual, status = await analyze_bottle(image_path, settings)
        comparisons = {}
        for field, expected_value in expected.items():
            if field not in SCORED_FIELDS:
                continue
            if field == "status":
                expected_fill = numeric(expected.get("fill_level"))
                if expected_fill is not None:
                    expected_value = "Unopened" if expected_fill >= 100 else "Opened"
            matches = values_match(field, expected_value, actual.get(field))
            comparisons[field] = {
                "expected": expected_value,
                "actual": actual.get(field),
                "match": matches,
            }
            total_fields += 1
            total_correct += int(matches)
        results.append(
            {
                "image": image_name,
                "status": status,
                "score": sum(item["match"] for item in comparisons.values()),
                "possible": len(comparisons),
                "comparisons": comparisons,
                "actual": actual,
            }
        )
    return {
        "provider": settings.analysis_provider,
        "model": (
            settings.openai_model
            if settings.analysis_provider == "openai"
            else settings.ollama_model
        ),
        "score": total_correct,
        "possible": total_fields,
        "accuracy": round(total_correct / total_fields, 4) if total_fields else 0,
        "scored_fields": sorted(SCORED_FIELDS),
        "missing_images": missing_images,
        "unvalidated_images": unvalidated_images,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate bottle extraction against fixtures")
    parser.add_argument("--images", type=Path, default=Path("tests/images"))
    parser.add_argument("--provider", choices=("ollama", "openai"))
    parser.add_argument("--model", help="Override the selected provider's model")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.provider:
        settings = replace(settings, analysis_provider=args.provider)
    if args.model:
        model_field = (
            "openai_model" if settings.analysis_provider == "openai" else "ollama_model"
        )
        settings = replace(settings, **{model_field: args.model})
    report = asyncio.run(evaluate(args.images, settings))
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
