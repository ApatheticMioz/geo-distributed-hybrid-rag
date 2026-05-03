"""Evaluate Node C on WikiQA validation split using EM and F1."""

import argparse
import asyncio
import collections
import re
import string
from typing import Dict, List

import httpx

try:
    from datasets import load_dataset
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "The 'datasets' package is required. Install it with `pip install datasets` and retry."
    ) from exc


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = " ".join(text.split())
    return text


def exact_match(prediction: str, gold_answers: List[str]) -> float:
    normalized_prediction = normalize_text(prediction)
    return float(any(normalized_prediction == normalize_text(answer) for answer in gold_answers))


def f1_score(prediction: str, gold_answers: List[str]) -> float:
    prediction_tokens = normalize_text(prediction).split()
    if not prediction_tokens:
        return 0.0

    best_f1 = 0.0
    for answer in gold_answers:
        gold_tokens = normalize_text(answer).split()
        if not gold_tokens:
            continue

        overlap = collections.Counter(prediction_tokens) & collections.Counter(gold_tokens)
        common = sum(overlap.values())
        if common == 0:
            continue

        precision = common / len(prediction_tokens)
        recall = common / len(gold_tokens)
        score = (2 * precision * recall) / (precision + recall)
        best_f1 = max(best_f1, score)

    return best_f1


def build_examples(limit: int = 100) -> List[Dict[str, object]]:
    dataset = load_dataset("wiki_qa", split="validation")
    grouped: Dict[str, List[str]] = collections.OrderedDict()

    for row in dataset:
        question = str(row.get("question", "")).strip()
        answer = str(row.get("answer", "")).strip()
        label = row.get("label", 0)
        if not question or not answer:
            continue
        if int(label) != 1:
            continue
        grouped.setdefault(question, []).append(answer)

    examples: List[Dict[str, object]] = []
    for question, answers in grouped.items():
        if not answers:
            continue
        examples.append({"question": question, "answers": answers})
        if len(examples) >= limit:
            break
    return examples


async def generate_answer(client: httpx.AsyncClient, base_url: str, question: str, top_k: int) -> str:
    chunks: List[str] = []
    async with client.stream(
        "POST",
        f"{base_url}/query",
        json={"query": question, "top_k": top_k},
    ) as response:
        if response.status_code != 200:
            body = await response.aread()
            raise RuntimeError(f"HTTP {response.status_code}: {body.decode('utf-8', errors='ignore')}")

        async for chunk in response.aiter_text():
            if chunk:
                chunks.append(chunk)
    return "".join(chunks).strip()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000", help="Node C base URL")
    parser.add_argument("--limit", type=int, default=100, help="Number of WikiQA questions to evaluate")
    parser.add_argument("--top-k", type=int, default=10, help="top_k request value")
    args = parser.parse_args()

    examples = build_examples(limit=args.limit)
    if not examples:
        raise SystemExit("No WikiQA validation examples were loaded.")

    total_em = 0.0
    total_f1 = 0.0

    async with httpx.AsyncClient(timeout=120.0) as client:
        for index, example in enumerate(examples, 1):
            question = str(example["question"])
            answers = [str(answer) for answer in example["answers"]]
            generated = await generate_answer(client, args.base_url, question, args.top_k)
            em = exact_match(generated, answers)
            f1 = f1_score(generated, answers)
            total_em += em
            total_f1 += f1
            print(f"[{index:03d}] EM={em:.0f} F1={f1:.3f}")

    count = len(examples)
    print("=" * 72)
    print(f"Questions evaluated: {count}")
    print(f"Final EM: {total_em / count:.4f}")
    print(f"Final F1: {total_f1 / count:.4f}")


if __name__ == "__main__":
    asyncio.run(main())