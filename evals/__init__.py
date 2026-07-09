# evals/ — self-feeding eval harness for the UniGrok MCP server.
#
# Evals here are not a report humans read: `python -m evals run` scores golden
# tasks (evals/tasks/*.json) against cassette-scripted fake SDK responses
# (evals/cassettes/*.json) and aggregates the outcomes into the store's
# routing_calibration table, which the RoutingAdvisor consults ahead of raw
# telemetry for borderline routing decisions. See evals/runner.py.
