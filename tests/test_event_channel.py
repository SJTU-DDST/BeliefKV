import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import zmq

from beliefkv.control.controller import BeliefKVController
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.event_channel import (
    JsonlRuntimeEventSink,
    QueuedRuntimeEventSink,
    RuntimeEventDatagramServer,
    UnixDatagramRuntimeEventSink,
)


def event(event_id, kind, *, ts_ms, **kwargs):
    return RuntimeEvent(
        event_id=event_id,
        ts_ms=ts_ms,
        kind=kind,
        workflow_id="wf",
        **kwargs,
    )


class RuntimeEventChannelTest(unittest.TestCase):
    def test_tool_start_delivery_does_not_block_tool_but_tool_end_waits_for_ack(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingSink:
            def __init__(self):
                self.events = []
                self.closed = False

            def emit_batch(self, events):
                if events[0].kind == RuntimeEventKind.TOOL_START:
                    started.set()
                    self.assert_released()
                self.events.extend(events)

            def assert_released(self):
                if not release.wait(timeout=2.0):
                    raise TimeoutError("test did not release the tool-start ACK")

            def close(self):
                self.closed = True

        transport = BlockingSink()
        queued = QueuedRuntimeEventSink(transport)
        tool_start = event("tool-start", RuntimeEventKind.TOOL_START, ts_ms=1.0)
        tool_end = event("tool-end", RuntimeEventKind.TOOL_END, ts_ms=2.0)
        try:
            before = time.monotonic()
            queued.emit_tool_start((tool_start,))
            self.assertLess(time.monotonic() - before, 0.5)
            self.assertTrue(started.wait(timeout=1.0))
            finished = threading.Event()

            def send_tool_end():
                queued.emit_batch((tool_end,))
                finished.set()

            sender = threading.Thread(target=send_tool_end)
            sender.start()
            self.assertFalse(finished.wait(timeout=0.05))
            release.set()
            sender.join(timeout=2.0)
            self.assertFalse(sender.is_alive())
            self.assertEqual(transport.events, [tool_start, tool_end])
            self.assertEqual(queued.timing_summary()["tool_start_count"], 1)
        finally:
            release.set()
            queued.close()
        self.assertTrue(transport.closed)

    def test_tool_start_delivery_failure_invalidates_following_control(self):
        class FailingSink:
            def __init__(self):
                self.events = []

            def emit_batch(self, events):
                self.events.extend(events)
                raise ConnectionError("ACK unavailable")

            def close(self):
                pass

        transport = FailingSink()
        queued = QueuedRuntimeEventSink(transport)
        try:
            queued.emit_tool_start(
                (event("start", RuntimeEventKind.TOOL_START, ts_ms=1.0),)
            )
            with self.assertRaisesRegex(ConnectionError, "ACK unavailable"):
                queued.emit_batch(
                    (event("end", RuntimeEventKind.TOOL_END, ts_ms=2.0),)
                )
            self.assertEqual(len(transport.events), 1)
        finally:
            queued.close()

    def test_event_wire_roundtrip(self):
        original = event(
            "create",
            RuntimeEventKind.INVOCATION_CREATE,
            ts_ms=1.0,
            invocation_id="root",
            context_id="ctx",
            context_epoch=2,
            attributes={"persistent": True},
        )
        self.assertEqual(RuntimeEvent.from_dict(original.to_dict()), original)

    def test_acknowledged_batch_reaches_controller(self):
        controller = BeliefKVController()
        events = (
            event("start", RuntimeEventKind.WORKFLOW_START, ts_ms=0.0),
            event(
                "create",
                RuntimeEventKind.INVOCATION_CREATE,
                ts_ms=1.0,
                invocation_id="root",
                context_id="ctx",
                context_epoch=0,
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self._server_or_skip(
                Path(temporary) / "server.sock", controller
            ) as server:
                worker = threading.Thread(target=self._drain_one, args=(server,))
                worker.start()
                with UnixDatagramRuntimeEventSink(server.path) as sink:
                    sink.emit_batch(events)
                worker.join(timeout=2.0)
                self.assertFalse(worker.is_alive())
        self.assertIn("wf", controller.graph.workflows)
        self.assertIn("root", controller.graph.invocations)

    def test_rejected_batch_is_reported_to_client(self):
        controller = BeliefKVController()
        invalid = event(
            "tool",
            RuntimeEventKind.TOOL_START,
            ts_ms=1.0,
            invocation_id="missing",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self._server_or_skip(
                Path(temporary) / "server.sock", controller
            ) as server:
                worker = threading.Thread(target=self._drain_one, args=(server,))
                worker.start()
                with UnixDatagramRuntimeEventSink(server.path) as sink:
                    with self.assertRaisesRegex(RuntimeError, "rejected"):
                        sink.emit_batch((invalid,))
                worker.join(timeout=2.0)
                self.assertFalse(worker.is_alive())

    def test_event_fd_wakes_an_idle_poller_before_scheduler_drain(self):
        controller = BeliefKVController()
        runtime_event = event(
            "start",
            RuntimeEventKind.WORKFLOW_START,
            ts_ms=0.0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self._server_or_skip(
                Path(temporary) / "server.sock", controller
            ) as server:
                poller = zmq.Poller()
                poller.register(server.fileno(), zmq.POLLIN)
                sink = UnixDatagramRuntimeEventSink(
                    server.path,
                    ack_timeout_s=1.0,
                    client_directory=temporary,
                )
                worker = threading.Thread(
                    target=sink.emit_batch,
                    args=((runtime_event,),),
                )
                worker.start()
                ready = dict(poller.poll(500))
                self.assertEqual(ready.get(server.fileno()), zmq.POLLIN)
                deliveries = server.drain()
                worker.join(timeout=2.0)
                sink.close()
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(deliveries), 1)
                self.assertTrue(deliveries[0].accepted)

    def test_idle_poller_drains_64_client_ack_burst(self):
        controller = BeliefKVController()
        with tempfile.TemporaryDirectory() as temporary:
            with self._server_or_skip(
                Path(temporary) / "server.sock", controller
            ) as server:
                poller = zmq.Poller()
                poller.register(server.fileno(), zmq.POLLIN)
                sinks = [
                    UnixDatagramRuntimeEventSink(
                        server.path,
                        ack_timeout_s=0.5,
                        retries=3,
                        client_directory=temporary,
                    )
                    for _ in range(64)
                ]
                errors = []

                def publish(index):
                    try:
                        sinks[index].emit_batch(
                            (
                                RuntimeEvent(
                                    event_id=f"start-{index}",
                                    ts_ms=float(index),
                                    kind=RuntimeEventKind.WORKFLOW_START,
                                    workflow_id=f"wf-{index}",
                                ),
                            )
                        )
                    except BaseException as error:
                        errors.append(error)

                workers = [
                    threading.Thread(target=publish, args=(index,))
                    for index in range(64)
                ]
                for worker in workers:
                    worker.start()
                deliveries = []
                deadline = time.monotonic() + 3.0
                while (
                    any(worker.is_alive() for worker in workers)
                    and time.monotonic() < deadline
                ):
                    if poller.poll(100):
                        deliveries.extend(server.drain())
                for worker in workers:
                    worker.join(timeout=1.0)
                for sink in sinks:
                    sink.close()

                self.assertFalse(errors)
                self.assertFalse(any(worker.is_alive() for worker in workers))
                first_deliveries = [
                    delivery for delivery in deliveries if not delivery.duplicate
                ]
                self.assertEqual(len(first_deliveries), 64)
                self.assertTrue(all(item.accepted for item in first_deliveries))

    def test_jsonl_sink_records_ordered_roundtrippable_events(self):
        events = (
            event("start", RuntimeEventKind.WORKFLOW_START, ts_ms=0.0),
            event("end", RuntimeEventKind.WORKFLOW_END, ts_ms=2.0),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            with JsonlRuntimeEventSink(path) as sink:
                sink.emit_batch(events)
            records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([item["sequence"] for item in records], [1, 2])
        self.assertEqual(
            tuple(RuntimeEvent.from_dict(item) for item in records),
            events,
        )

    def test_missing_control_socket_is_reported_as_connection_failure(self):
        events = (event("end", RuntimeEventKind.WORKFLOW_END, ts_ms=2.0),)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing-server.sock"
            try:
                sink = UnixDatagramRuntimeEventSink(
                    path,
                    ack_timeout_s=0.01,
                    retries=2,
                    client_directory=temporary,
                )
            except PermissionError as error:
                self.skipTest(f"Unix sockets are blocked by the test sandbox: {error}")
            with sink:
                with self.assertRaises(ConnectionError) as captured:
                    sink.emit_batch(events)
        self.assertIsInstance(captured.exception.__cause__, FileNotFoundError)

    @staticmethod
    def _drain_one(server: RuntimeEventDatagramServer) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if server.drain():
                return
            time.sleep(0.001)
        raise TimeoutError("server did not receive a runtime event batch")

    def _server_or_skip(
        self, path: Path, controller: BeliefKVController
    ) -> RuntimeEventDatagramServer:
        try:
            return RuntimeEventDatagramServer(
                path,
                controller.process_runtime_events,
            )
        except PermissionError as error:
            self.skipTest(f"Unix sockets are blocked by the test sandbox: {error}")


if __name__ == "__main__":
    unittest.main()
