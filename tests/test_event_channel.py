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
    MAX_DATAGRAM_BYTES,
    MAX_EVENTS_PER_MESSAGE,
    QueuedRuntimeEventSink,
    RuntimeEventDatagramServer,
    UnixDatagramRuntimeEventSink,
    _encode_event_message,
)
from beliefkv.runtime.hotpath_timing import HotpathTiming


def event(event_id, kind, *, ts_ms, **kwargs):
    return RuntimeEvent(
        event_id=event_id,
        ts_ms=ts_ms,
        kind=kind,
        workflow_id="wf",
        **kwargs,
    )


class QueuedTransportFixture:
    def __init__(self, *, hold_second=False):
        self.started = threading.Event()
        self.release = threading.Event()
        self.second_started = threading.Event()
        self.second_release = threading.Event()
        self.hold_second = hold_second
        self.messages = []
        self.closed = False

    def emit_batch(self, events):
        self.emit_batches((events,))

    def emit_batches(self, batches):
        payload = _encode_event_message("0" * 32, batches)
        if sum(map(len, batches)) > MAX_EVENTS_PER_MESSAGE:
            raise ValueError("event batch exceeds channel limit")
        if len(payload) > MAX_DATAGRAM_BYTES:
            raise ValueError("serialized event batch exceeds datagram limit")
        self.messages.append(batches)
        if len(self.messages) == 1:
            self.started.set()
            if not self.release.wait(timeout=3.0):
                raise TimeoutError("first fixture ACK was not released")
        elif len(self.messages) == 2 and self.hold_second:
            self.second_started.set()
            if not self.second_release.wait(timeout=3.0):
                raise TimeoutError("second fixture ACK was not released")

    def close(self):
        self.closed = True


class RuntimeEventChannelTest(unittest.TestCase):
    def test_queued_batches_share_ack_without_changing_order_or_close_boundary(self):
        transport = QueuedTransportFixture(hold_second=True)
        queued = QueuedRuntimeEventSink(transport)
        closer = None
        first = queued.submit_batch(
            (event("first", RuntimeEventKind.WORKFLOW_START, ts_ms=0.0),),
        )
        try:
            self.assertTrue(transport.started.wait(timeout=1.0))
            batches = [
                (event("tool", RuntimeEventKind.TOOL_START, ts_ms=1.0),),
                (event("result", RuntimeEventKind.LLM_RESULT, ts_ms=2.0),),
                (
                    event("return", RuntimeEventKind.RETURN, ts_ms=3.0),
                    event("join", RuntimeEventKind.JOIN_SATISFIED, ts_ms=4.0),
                ),
            ]
            deliveries = [
                queued.submit_batch(batch, tool_start=index == 0)
                for index, batch in enumerate(batches)
            ]
            closed = threading.Event()

            def close():
                queued.close()
                closed.set()

            closer = threading.Thread(target=close)
            closer.start()
            self.assertFalse(closed.wait(timeout=0.02))
            transport.release.set()
            self.assertTrue(transport.second_started.wait(timeout=1.0))
            first.wait()
            self.assertFalse(any(item.finished.is_set() for item in deliveries))
            self.assertFalse(closed.is_set())
            transport.second_release.set()
            closer.join(timeout=2.0)
            self.assertFalse(closer.is_alive())
            for delivery in deliveries:
                delivery.wait()
            self.assertEqual(transport.messages[1], tuple(batches))
            self.assertEqual(len(transport.messages), 2)
            self.assertTrue(transport.closed)
            timing = queued.timing_summary()
            self.assertEqual(timing["transport_message_count"], 2)
            self.assertEqual(timing["coalesced_message_count"], 1)
            self.assertEqual(timing["accepted_batch_count"], 4)
            for name in ("queue_p50_ms", "reentry_queue_p50_ms", "llm_result_queue_p50_ms"):
                self.assertGreaterEqual(timing[name], 0.0)
        finally:
            transport.release.set()
            transport.second_release.set()
            if closer is not None:
                closer.join(timeout=2.0)
            queued.close()

    def test_queued_coalescing_splits_at_event_and_encoded_byte_limits(self):
        for count, text in ((65, ""), (5, "\u6d4b" * 4000)):
            with self.subTest(count=count):
                transport = QueuedTransportFixture()
                queued = QueuedRuntimeEventSink(transport, max_pending=70)
                first = queued.submit_batch(
                    (event("first", RuntimeEventKind.WORKFLOW_START, ts_ms=0.0),),
                )
                try:
                    self.assertTrue(transport.started.wait(timeout=1.0))
                    batches = [
                        (event(
                            str(index), RuntimeEventKind.STRUCTURED_ACTION,
                            ts_ms=float(index + 1), attributes={"text": text},
                        ),)
                        for index in range(count)
                    ]
                    deliveries = [queued.submit_batch(batch) for batch in batches]
                    transport.release.set()
                    queued.flush()
                    first.wait()
                    for delivery in deliveries:
                        delivery.wait()
                    self.assertEqual(
                        [batch for message in transport.messages[1:] for batch in message],
                        batches,
                    )
                    self.assertTrue(all(
                        sum(map(len, message)) <= MAX_EVENTS_PER_MESSAGE
                        and len(_encode_event_message("0" * 32, message)) <= MAX_DATAGRAM_BYTES
                        for message in transport.messages
                    ))
                    if not text:
                        self.assertEqual(
                            [len(message) for message in transport.messages], [1, 64, 1],
                        )
                    else:
                        self.assertEqual(
                            [len(message) for message in transport.messages], [1, 2, 2, 1],
                        )
                finally:
                    transport.release.set()
                    queued.close()

    def test_oversized_delivery_preserves_accepted_prefix_and_fails_followers(self):
        transport = QueuedTransportFixture()
        queued = QueuedRuntimeEventSink(transport)
        queued.submit_batch((event("first", RuntimeEventKind.WORKFLOW_START, ts_ms=0.0),))
        try:
            self.assertTrue(transport.started.wait(timeout=1.0))
            accepted = queued.submit_batch(
                (event("accepted", RuntimeEventKind.WORKFLOW_START, ts_ms=1.0),),
            )
            oversized = queued.submit_batch((event(
                "oversized", RuntimeEventKind.STRUCTURED_ACTION, ts_ms=2.0,
                attributes={"text": "x" * MAX_DATAGRAM_BYTES},
            ),))
            following = queued.submit_batch(
                (event("following", RuntimeEventKind.WORKFLOW_START, ts_ms=3.0),),
            )
            transport.release.set()
            with self.assertRaisesRegex(ValueError, "datagram limit"):
                queued.flush()
            accepted.wait()
            for delivery in (oversized, following):
                with self.assertRaisesRegex(ValueError, "datagram limit"):
                    delivery.wait()
            self.assertEqual(
                [batch[0].event_id for message in transport.messages for batch in message],
                ["first", "accepted"],
            )
        finally:
            transport.release.set()
            queued.close()

    def test_coalesced_wire_preserves_handler_boundaries_and_partial_failure_ack(self):
        controller = BeliefKVController()
        batches = (
            (event("start", RuntimeEventKind.WORKFLOW_START, ts_ms=0.0),),
            (event(
                "invalid", RuntimeEventKind.TOOL_START, ts_ms=1.0,
                invocation_id="missing",
            ),),
            (event("end", RuntimeEventKind.WORKFLOW_END, ts_ms=2.0),),
        )
        calls = []

        def apply(events):
            calls.append(events)
            controller.process_runtime_events(events)

        with tempfile.TemporaryDirectory() as temporary:
            with RuntimeEventDatagramServer(Path(temporary) / "server.sock", apply) as server:
                payload = _encode_event_message("partial", batches)
                delivery, ack = server._handle(payload)
                self.assertFalse(delivery.accepted)
                self.assertEqual(calls, list(batches[:2]))
                self.assertEqual(delivery.event_count, 1)
                self.assertEqual(json.loads(ack)["accepted_batch_count"], 1)
                duplicate, second_ack = server._handle(payload)
                self.assertTrue(duplicate.duplicate)
                self.assertEqual(second_ack, ack)
                self.assertEqual(calls, list(batches[:2]))

    def test_queued_partial_rejection_completes_only_the_accepted_prefix(self):
        controller = BeliefKVController()
        started = threading.Event()
        release = threading.Event()
        stop = threading.Event()
        calls = []

        def apply(events):
            calls.append(events)
            if events[0].event_id == "first":
                started.set()
                if not release.wait(timeout=3.0):
                    raise TimeoutError("first handler was not released")
            controller.process_runtime_events(events)

        with tempfile.TemporaryDirectory() as temporary:
            with RuntimeEventDatagramServer(Path(temporary) / "server.sock", apply) as server:
                transport = UnixDatagramRuntimeEventSink(
                    server.path, client_directory=temporary, ack_timeout_s=1.0,
                )
                queued = QueuedRuntimeEventSink(transport)

                def drain():
                    while not stop.is_set():
                        server.drain()
                        stop.wait(timeout=0.001)

                worker = threading.Thread(target=drain)
                worker.start()
                queued.submit_batch((event("first", RuntimeEventKind.WORKFLOW_START, ts_ms=0.0),))
                try:
                    self.assertTrue(started.wait(timeout=1.0))
                    accepted = queued.submit_batch((event(
                        "create", RuntimeEventKind.INVOCATION_CREATE, ts_ms=1.0,
                        invocation_id="root", context_id="ctx", context_epoch=0,
                    ),))
                    rejected = queued.submit_batch((event(
                        "invalid", RuntimeEventKind.TOOL_START, ts_ms=2.0,
                        invocation_id="missing",
                    ),))
                    following = queued.submit_batch((event(
                        "end", RuntimeEventKind.WORKFLOW_END, ts_ms=3.0,
                    ),))
                    release.set()
                    with self.assertRaisesRegex(RuntimeError, "rejected"):
                        queued.flush()
                    accepted.wait()
                    for delivery in (rejected, following):
                        with self.assertRaisesRegex(RuntimeError, "rejected"):
                            delivery.wait()
                    self.assertEqual([batch[0].event_id for batch in calls], [
                        "first", "create", "invalid",
                    ])
                    self.assertEqual(queued.timing_summary()["accepted_batch_count"], 2)
                finally:
                    release.set()
                    queued.close()
                    stop.set()
                    worker.join(timeout=2.0)
                    self.assertFalse(worker.is_alive())

    def test_acknowledged_coalesced_batches_reach_controller_in_original_batches(self):
        controller = BeliefKVController()
        batches = (
            (event("start", RuntimeEventKind.WORKFLOW_START, ts_ms=0.0),),
            (event(
                "create", RuntimeEventKind.INVOCATION_CREATE, ts_ms=1.0,
                invocation_id="root", context_id="ctx", context_epoch=0,
            ),),
            (event(
                "tool-start", RuntimeEventKind.TOOL_START, ts_ms=2.0, invocation_id="root",
            ),),
            (event(
                "tool-end", RuntimeEventKind.TOOL_END, ts_ms=3.0, invocation_id="root",
            ),),
        )
        received = []

        def apply(events):
            received.append(events)
            controller.process_runtime_events(events)

        with tempfile.TemporaryDirectory() as temporary:
            with RuntimeEventDatagramServer(Path(temporary) / "server.sock", apply) as server:
                worker = threading.Thread(target=self._drain_one, args=(server,))
                worker.start()
                with UnixDatagramRuntimeEventSink(
                    server.path, client_directory=temporary,
                ) as sink:
                    sink.emit_batches(batches)
                worker.join(timeout=2.0)
                self.assertFalse(worker.is_alive())
                self.assertEqual(received, list(batches))

    def test_sparse_control_timing_preserves_delivery_rejection_and_duplicate_ack(self):
        timing = HotpathTiming()
        controller = BeliefKVController()
        packets = [
            json.dumps({
                "schema_version": 1,
                "message_id": f"packet-{index}",
                "events": [RuntimeEvent(
                    event_id=f"start-{index}", workflow_id=f"wf-{index}",
                    kind=RuntimeEventKind.WORKFLOW_START, ts_ms=float(index),
                ).to_dict()],
            }).encode()
            for index in range(128)
        ]
        packets.extend((b"{malformed", packets[0]))

        class SocketFixture:
            def __init__(self):
                self.packets = iter(packets)
                self.acks = []

            def recvfrom(self, _limit):
                try:
                    return next(self.packets), "client"
                except StopIteration:
                    raise BlockingIOError from None

            def sendto(self, ack, _reply_to):
                self.acks.append(ack)

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as temporary:
            with RuntimeEventDatagramServer(
                Path(temporary) / "server.sock", controller.process_runtime_events,
                timing=timing,
            ) as server:
                server._socket.close()
                server._socket = SocketFixture()
                with timing.measure("control_outer"):
                    deliveries = server.drain()
                self.assertEqual(len(controller.graph.workflows), 128)
                self.assertFalse(deliveries[-2].accepted)
                self.assertTrue(deliveries[-1].duplicate)
                self.assertEqual(server._socket.acks[-1], server._socket.acks[0])
                self.assertEqual(
                    json.loads(server._socket.acks[-2])["status"], "rejected",
                )
        phases = timing.snapshot()["phases"]
        for phase in (
            "control_socket_receive_sample", "control_json_decode_sample",
            "control_ack_encode_sample", "control_socket_send_sample",
        ):
            self.assertEqual(phases[phase]["count"], 3)
        self.assertEqual(phases["control_event_decode_sample"]["count"], 2)
        self.assertEqual(timing._stack, [])

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
        started = threading.Event()
        release = threading.Event()

        class FailingSink:
            def __init__(self):
                self.events = []

            def emit_batch(self, events):
                self.events.extend(events)
                started.set()
                if not release.wait(timeout=2.0):
                    raise TimeoutError("failed ACK was not released")
                raise ConnectionError("ACK unavailable")

            def close(self):
                pass

        transport = FailingSink()
        queued = QueuedRuntimeEventSink(transport)
        try:
            queued.emit_tool_start(
                (event("start", RuntimeEventKind.TOOL_START, ts_ms=1.0),)
            )
            self.assertTrue(started.wait(timeout=1.0))
            release.set()
            with self.assertRaisesRegex(ConnectionError, "ACK unavailable"):
                queued.emit_batch(
                    (event("end", RuntimeEventKind.TOOL_END, ts_ms=2.0),)
                )
            self.assertEqual(len(transport.events), 1)
        finally:
            release.set()
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
