"""Fine-tune driver: wraps needle.training.finetune without re-downloading weights.

Usage: python ft.py <family> [--epochs N] [--batch-size N] [--data path.jsonl]
Splits are needle's own per-tool split (seed 42). Emits BASE_EVAL/FINETUNED_EVAL.
"""
import argparse, os, sys, time

LAB = os.path.dirname(os.path.abspath(__file__))
CKPT = os.path.join(LAB, "checkpoints", "needle.pkl")


def main():
    import needle.training.finetune as ftmod
    ftmod._resolve_checkpoint = lambda path: path or CKPT  # skip HF force-download

    p = argparse.ArgumentParser()
    p.add_argument("family")
    p.add_argument("--epochs", type=int, default=None)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--data", type=str, default=None)
    p.add_argument("--max-enc-len", type=int, default=512)
    p.add_argument("--max-dec-len", type=int, default=128)
    p.add_argument("--ckpt-dir", type=str, default=None)
    a = p.parse_args()

    data = a.data or os.path.join(LAB, "data", f"{a.family}.jsonl")
    lines = open(data).read().splitlines()
    n = len(lines)
    # needle PACKS examples into max_enc_len rows; if batch > packed rows the
    # trainer computes total_steps=0 -> NaN schedule (observed). Clamp batch
    # to an estimate of packed-row count (chars/4 ~ tokens).
    import json as _json
    enc_tokens = sum(
        (len(_json.loads(l)["query"]) + len(_json.loads(l)["tools"])) // 4 + 2
        for l in lines if l.strip())
    est_rows = max(1, int(enc_tokens * 0.85) // a.max_enc_len)
    batch = max(2, min(a.batch_size, est_rows // 2)) if est_rows < 2 * a.batch_size else a.batch_size
    if batch != a.batch_size:
        print(f"[ft] batch clamped {a.batch_size} -> {batch} (est packed rows ~{est_rows})")
    a.batch_size = batch
    epochs = a.epochs
    if epochs is None:
        # target ~300 optimizer steps; enum-arg tasks converge well before this
        steps_per_epoch = max(1, est_rows // a.batch_size)
        epochs = max(3, min(80, 300 // steps_per_epoch))

    ckpt_dir = a.ckpt_dir or os.path.join(LAB, "checkpoints", a.family)
    os.makedirs(ckpt_dir, exist_ok=True)

    args = argparse.Namespace(
        jsonl_path=data, checkpoint=CKPT, epochs=epochs, batch_size=a.batch_size,
        checkpoint_dir=ckpt_dir, cache_dir=None,
        max_enc_len=a.max_enc_len, max_dec_len=a.max_dec_len,
    )
    print(f"[ft] family={a.family} n={n} epochs={epochs} batch={a.batch_size} -> {ckpt_dir}")
    t0 = time.time()
    ftmod.finetune_local(args)
    print(f"[ft] wall={time.time()-t0:.0f}s")


# mp.Pool(spawn) inside needle's tokenizer re-imports this module — must be guarded.
if __name__ == "__main__":
    main()
