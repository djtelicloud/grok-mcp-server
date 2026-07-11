# src/swarm/ — Swarm Code Optimizer (contributor-only feature).
#
# Bounded stochastic search over semantically-equivalent rewrites of a single
# focus function, evaluated against an OBJECTIVE oracle (the user's tests +
# benchmark), presenting a Pareto front for a human to apply. Design doc:
# the swarm implementation plan (rev 2, post-Grok review); rollout ladder
# UNIGROK_SWARM=off|dry_run|active with off as the default.
