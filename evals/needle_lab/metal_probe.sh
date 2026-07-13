#!/bin/zsh
# Time-boxed jax-metal feasibility probe: can the Apple GPU run Needle's graph?
# Evidence for the report either way. Separate venv; does not touch the lab env.
set -u
cd "$(dirname "$0")"
uv venv --python 3.12 .venv-metal 2>&1 | tail -1
source .venv-metal/bin/activate
# jax-metal pins to jax 0.4.x era; install its known-good pairing
uv pip install "jax-metal==0.1.1" "jax==0.4.34" "jaxlib==0.4.34" "flax==0.10.2" sentencepiece huggingface_hub 2>&1 | tail -2
python - <<'EOF'
import time
import jax
print("[metal] devices:", jax.devices())
import sys
sys.path.insert(0, "../needle")
try:
    import jax.numpy as jnp
    from needle.model.run import load_checkpoint
    from needle.model.architecture import SimpleAttentionNetwork, make_padding_mask, make_causal_mask
    params, config = load_checkpoint("checkpoints/needle.pkl")
    model = SimpleAttentionNetwork(config)
    src = jnp.ones((2, 64), dtype=jnp.int32)
    tgt = jnp.ones((2, 16), dtype=jnp.int32)
    t0 = time.time()
    out = model.apply({"params": params}, src, tgt,
                      src_mask=make_padding_mask(src, 0),
                      tgt_mask=make_causal_mask(16))
    out.block_until_ready() if hasattr(out, "block_until_ready") else None
    print(f"[metal] FORWARD OK shape={out.shape} {time.time()-t0:.1f}s")
    # one grad step of the decode loss
    def loss_fn(p):
        lo = model.apply({"params": p}, src, tgt,
                         src_mask=make_padding_mask(src, 0), tgt_mask=make_causal_mask(16))
        return jnp.mean(lo ** 2)
    t0 = time.time()
    g = jax.grad(loss_fn)(params)
    jax.block_until_ready(g)
    print(f"[metal] BACKWARD OK {time.time()-t0:.1f}s")
    print("[metal] VERDICT: usable")
except Exception as e:
    print(f"[metal] VERDICT: FAILED — {type(e).__name__}: {str(e)[:300]}")
EOF
