"""Lexical-retrieval baseline for the memory-card benchmark (audit check).

Reproduces the audit's TF-IDF baselines against the revived contrastive
head's 60% top-1 on the identical benchmark: 50 held-out messy queries
(data/memory_pairs.json "test") over the 10 memory cards from
revive_contrastive.py. Pure standard library, fully deterministic.

Two analyzers x two idf-fit corpora are printed; the audit's original
figures (word 86%, char-3gram 92%) correspond to word/fit=cards and
char3/fit=cards+train. Every variant beats the revived head's 60%.

Run from evals/needle_lab/:  python tfidf_baseline.py
"""

import json
import math
import os
import re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))

# The 10 memory cards, verbatim from revive_contrastive.py (docs are
# "<id>: <text>", matching the retrieval corpus used for the head's 60%).
CARDS = {
    "auth": "fixed OAuth refresh loop in src/credentials.py by pinning token expiry",
    "routing": "planning tasks route to grok-4.5; coding stays on composer unless escalated",
    "docker": "CLI plane runs in docker; auth persists in unigrok-cli-auth volume",
    "evals": "golden tasks live in evals/tasks; baseline gate is --check-baseline",
    "budget": "agent loop hard-stops at $0.50; caller budgets via UNIGROK_CALLER_BUDGETS",
    "sessions": "native CLI sessions via -s id are the continuity mechanism",
    "ui": "test bench UI at /ui/ shows live route receipts",
    "git": "git writes need UNIGROK_RUNTIME=local and ENABLE_GIT_WRITE=1",
    "needle": "needle projections are built by build_needle_tools_context, 1024 tokens max",
    "plane": "same_plane policy forbids crossing billing boundary on fallback",
}


def words(text):
    # sklearn TfidfVectorizer default token pattern, lowercased
    return re.findall(r"(?u)\b\w\w+\b", text.lower())


def char_wb3(text):
    # sklearn analyzer="char_wb", ngram_range=(3,3): pad each word with
    # spaces, n-grams never cross word boundaries
    grams = []
    for w in re.split(r"\s+", text.lower()):
        if not w:
            continue
        padded = f" {w} "
        if len(padded) < 3:
            grams.append(padded)
            continue
        for i in range(len(padded) - 2):
            grams.append(padded[i : i + 3])
    return grams


def top1_accuracy(analyzer, fit_texts, docs, ids, test):
    # smooth idf (ln((1+n)/(1+df))+1), raw tf, l2 norm, cosine — the
    # sklearn TfidfVectorizer defaults
    fitted = [Counter(analyzer(t)) for t in fit_texts]
    n = len(fit_texts)
    df = Counter(g for c in fitted for g in c)
    idf = {g: math.log((1 + n) / (1 + d)) + 1 for g, d in df.items()}

    def vec(text):
        counts = Counter(g for g in analyzer(text) if g in idf)
        v = {g: tf * idf[g] for g, tf in counts.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {g: x / norm for g, x in v.items()}

    doc_vecs = [vec(d) for d in docs]
    correct = 0
    for query, gold in test:
        qv = vec(query)
        sims = [sum(qv.get(g, 0.0) * dv.get(g, 0.0) for g in qv) for dv in doc_vecs]
        correct += ids[max(range(len(ids)), key=lambda i: sims[i])] == gold
    return correct


def main():
    mp = json.load(open(os.path.join(HERE, "data", "memory_pairs.json")))
    test = [(x["query"], x["card"]) for x in mp["test"]]
    train_queries = [x["query"] for x in mp["train"]]
    ids = list(CARDS)
    docs = [f"{cid}: {CARDS[cid]}" for cid in ids]

    print(f"benchmark: {len(test)} held-out queries / {len(ids)} cards "
          f"(same as revived head's 60% top-1)")
    for label, analyzer in (("word", words), ("char3", char_wb3)):
        for fit_label, fit in (("cards", docs), ("cards+train", docs + train_queries)):
            c = top1_accuracy(analyzer, fit, docs, ids, test)
            print(f"tfidf {label:5s} / idf-fit={fit_label:11s}: "
                  f"top-1 {c}/{len(test)} = {c / len(test):.0%}")


if __name__ == "__main__":
    main()
