from __future__ import annotations

from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest import mock

from dohwaji.locking import StateLock
from dohwaji.server import DohwajiApp, create_server


class StateLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.state = self.root / "state"
        self.source.mkdir()
        self.app = DohwajiApp(self.source, self.state)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_shared_locks_coexist_and_block_exclusive_lock(self) -> None:
        with StateLock(self.state, shared=True):
            with StateLock(self.state, shared=True):
                with self.assertRaisesRegex(ValueError, "Stop Dohwaji"):
                    with StateLock(self.state):
                        pass

        with StateLock(self.state):
            self.assertEqual(self.state.resolve() / "runtime.lock", StateLock(self.state).path)

    def test_exclusive_lock_blocks_server_creation(self) -> None:
        with StateLock(self.state):
            with self.assertRaisesRegex(ValueError, "Stop Dohwaji"):
                create_server(self.app, 0)

    def test_servers_can_share_the_state_lock(self) -> None:
        first = create_server(self.app, 0)
        try:
            second = create_server(self.app, 0)
            try:
                with self.assertRaisesRegex(ValueError, "Stop Dohwaji"):
                    with StateLock(self.state):
                        pass
            finally:
                second.server_close()
        finally:
            first.server_close()

    def test_server_close_releases_shared_lock(self) -> None:
        server = create_server(self.app, 0)
        try:
            with self.assertRaisesRegex(ValueError, "Stop Dohwaji"):
                with StateLock(self.state):
                    pass
        finally:
            server.server_close()

        with StateLock(self.state):
            pass

    def test_server_close_waits_for_active_request_before_releasing_lock(self) -> None:
        server = create_server(self.app, 0)
        server.quiet = True
        request_started = threading.Event()
        release_request = threading.Event()
        close_started = threading.Event()
        close_finished = threading.Event()
        close_errors: list[BaseException] = []
        original_save_layout = self.app.save_layout

        def blocking_save_layout(value: object, relative_folder: str = "") -> object:
            request_started.set()
            release_request.wait()
            return original_save_layout(value, relative_folder)

        def close_server() -> None:
            try:
                server.shutdown()
                close_started.set()
                server.server_close()
            except BaseException as error:
                close_errors.append(error)
            finally:
                close_finished.set()

        serving_thread = threading.Thread(target=server.serve_forever, daemon=True)
        close_thread = threading.Thread(target=close_server, daemon=True)
        client: socket.socket | None = None
        serving_thread.start()
        try:
            with mock.patch.object(self.app, "save_layout", side_effect=blocking_save_layout):
                client = socket.create_connection(server.server_address, timeout=2)
                client.settimeout(2)
                body = b'{"items": {}}'
                client.sendall(
                    b"POST /api/layout HTTP/1.0\r\n"
                    b"Host: 127.0.0.1\r\n"
                    b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                    + body
                )
                self.assertTrue(request_started.wait(2), "request handler did not start")

                close_thread.start()
                self.assertTrue(close_started.wait(2), "server shutdown did not complete")
                self.assertFalse(
                    close_finished.wait(0.1),
                    "server_close returned while the request handler was active",
                )
                with self.assertRaisesRegex(ValueError, "Stop Dohwaji"):
                    with StateLock(self.state):
                        pass

                release_request.set()
                self.assertTrue(close_finished.wait(2), "server_close did not wait cleanly")

            self.assertEqual([], close_errors)
            with StateLock(self.state):
                pass
        finally:
            release_request.set()
            if close_thread.ident is None:
                server.shutdown()
                server.server_close()
            else:
                close_thread.join(timeout=2)
            serving_thread.join(timeout=2)
            if client is not None:
                client.close()

    def test_bind_failure_releases_shared_lock(self) -> None:
        occupied = socket.socket()
        try:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]

            with self.assertRaises(OSError):
                create_server(self.app, port)

            with StateLock(self.state):
                pass
        finally:
            occupied.close()


if __name__ == "__main__":
    unittest.main()
