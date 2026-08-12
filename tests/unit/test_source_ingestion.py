import unittest

from control_plane.contracts import AgentRunResult
from control_plane.source_ingestion import FetchedSource, SourceIngestionWorker
from control_plane.source_registry import SourceDefinition, SourceRegistry


class _Gateway:
    def __init__(self):
        self.events = []

    def dispatch(self, event):
        self.events.append(event)
        return AgentRunResult(run_id="run-1", route="agent_loop", payload={"status": "ok"})


class _Store:
    def __init__(self):
        self.accepted = []

    def ensure_source(self, source):
        return None

    def accept_change(self, source, observation):
        self.accepted.append(observation.dedupe_key)
        return True


class SourceIngestionTests(unittest.TestCase):
    def test_ingest_before_dispatch_and_skip_same_revision(self):
        registry = SourceRegistry()
        registry.register(
            SourceDefinition(
                source_id="cninfo:600519:annual-report",
                source_type="financial_report",
                metadata={"affected_symbols": ["600519"]},
            )
        )
        gateway = _Gateway()
        order = []

        def fetch(_source):
            return FetchedSource(version="2025-annual", content="report")

        def ingest(_source, observation, fetched):
            order.append(("ingest", observation.version, fetched.content))
            return {"document_id": "doc-1", "chunk_count": 3}

        worker = SourceIngestionWorker(registry, gateway, fetch, ingest, _Store())
        first = worker.poll_and_dispatch("cninfo:600519:annual-report")
        second = worker.poll_and_dispatch("cninfo:600519:annual-report")

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(order, [("ingest", "2025-annual", "report")])
        self.assertEqual(len(gateway.events), 1)
        self.assertEqual(gateway.events[0].metadata["ingestion"]["chunk_count"], 3)

    def test_failed_ingestion_does_not_commit_revision(self):
        registry = SourceRegistry()
        registry.register(SourceDefinition("source-1", "news", metadata={"affected_symbols": ["600519"]}))
        gateway = _Gateway()
        attempts = []

        def fetch(_source):
            return FetchedSource(version="v1", content="news")

        def ingest(_source, _observation, _fetched):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("MinerU unavailable")
            return {"document_id": "doc-1"}

        worker = SourceIngestionWorker(registry, gateway, fetch, ingest, _Store())
        with self.assertRaisesRegex(RuntimeError, "MinerU unavailable"):
            worker.poll_and_dispatch("source-1")
        worker.poll_and_dispatch("source-1")

        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(gateway.events), 1)


if __name__ == "__main__":
    unittest.main()
