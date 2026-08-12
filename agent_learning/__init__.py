"""Human-governed learning artifacts derived from audited agent runs.

This package deliberately separates evaluation and dataset curation from the
online agent path.  A successful run is not training data until a reviewer
explicitly approves and labels it.
"""

from agent_learning.trace_evaluation import (
    build_learning_candidate,
    evaluate_agent_run,
    flatten_trace,
)

__all__ = ["build_learning_candidate", "evaluate_agent_run", "flatten_trace"]
