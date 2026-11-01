import logging
import threading
import time
from typing import Callable

from archivable import NullDataManager
from tent.transactive_node import TransactiveNode
from tent.utils.timer import Timer

_log = logging.getLogger(__name__)


class MockTN(TransactiveNode):
    """
    Mock TN useful for integration testing.
        Usage:
            configs = {} # Should contain actual configs for TN and components.
            mock_tn = MockTN()
            mock_tn.configure_node(configs)
            # We will assume that the configurations contained a neighbor called 'peerA' and a subscription to 'my_data'.
            mock_tn.register_recipient('peerA', mock.Mock())
            # Set up conditions for next iteration (multiple iterations may be required to advance state machine):
            mock_tn.provide_subscription('my_data', {'my_message': 'Spam and Eggs'})
            mock_tn.step()  # Single step through state machine
            mock_tn.recipients['peerA'].assert_called_with({'expected': 'signal'})
            
            # Or run continuous loop:
            mock_tn.start_state_machine()
            # ... do test setup ...
            mock_tn.stop_state_machine()
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recipients: dict[str, Callable] = {}
        self.subscriptions: dict[str, Callable] = {}
        self._running = False
        self._step_count = 0
        # Background thread used when start_state_machine() is called
        self._thread: threading.Thread | None = None
        # Lock to protect start/stop and step count in concurrent scenarios
        self._lock = threading.Lock()
        # Slice-assignable list of markets used by Market.events() when removing expired markets
        self.markets: list = []
        # Components built directly in tests archive through the node before configure_node runs; configure_node
        # replaces this with the configured manager (or another NullDataManager when the config has none).
        self._data_manager = NullDataManager(self)

    def register_recipient(self, recipient: str, callback: Callable):
        """Register a callback function to receive signals sent to a recipient."""
        self.recipients[recipient] = callback

    def issue_signal(self, recipient: str, serialized_signal: dict):
        """Send a signal to a registered recipient."""
        if callback := self.recipients.get(recipient):
            _log.debug(f"Sending signal to {recipient}: {serialized_signal}")
            callback(serialized_signal)
        else:
            _log.warning(f"No recipient registered for: {recipient}")

    def connect_peer(self, peer: 'MockTN') -> None:
        """
        Wire this node's signals to a peer node in the same process, as a message bus would: a signal this node issues to
        the neighbor named like the peer is delivered to the peer's neighbor object that represents this node. Call once
        in each direction. Both nodes share the class-level Timer, so their clocks agree.
        """
        mine = next(n for n in self.neighbors if n.name == peer.name)
        theirs = next(n for n in peer.neighbors if n.name == self.name)
        self.register_recipient(mine.name, lambda message: theirs.receive_transactive_signal(None, None, message))

    def register_subscription(self, subscribe_key: str, callback: Callable):
        """Register a subscription callback for receiving data."""
        self.subscriptions[subscribe_key] = callback

    def provide_subscription(self, subscribe_key: str, message):
        """Deliver a message to a subscription callback with the signature TransactiveNode.register_subscription
        documents: (time received, subscribe key, message)."""
        if callback := self.subscriptions.get(subscribe_key):
            _log.debug(f"Providing subscription data for {subscribe_key}: {message}")
            callback(Timer.now(), subscribe_key, message)
        else:
            _log.warning(f"No subscription registered for: {subscribe_key}")

    def _sleep(self):
        """The node's cooperative yield: nothing to yield to here. (The background loop paces itself; a pause here would
        be taken once per market per step, which a test driving step() by hand would pay for nothing.)"""

    def step(self):
        """Execute a single state machine iteration."""
        _log.debug(f"Executing state machine step {self._step_count}")
        # Protect the real step execution and step counter from concurrent callers
        with self._lock:
            self._state_machine_iteration()
            self._step_count += 1
        
    def step_n(self, n: int):
        """Execute n state machine iterations."""
        for _ in range(n):
            self.step()

    def start_state_machine(self):
        """Start the state machine loop (for testing scenarios that need continuous operation)."""
        with self._lock:
            if self._running:
                return
            self._running = True
            # Spawn a background thread to run the state machine loop
            self._thread = threading.Thread(target=self.state_machine_loop, daemon=True)
            self._thread.start()
        
    def stop_state_machine(self):
        """Stop the state machine loop."""
        with self._lock:
            self._running = False
        # Join the background thread if it exists (short timeout to avoid hanging tests)
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def state_machine_loop(self):
        """Run the state machine in a loop until stopped. For testing, use step() instead."""
        # Run until stop_state_machine() clears the running flag. Use _sleep() to pace the loop.
        while True:
            with self._lock:
                if not self._running:
                    break
            # Execute one state machine iteration
            try:
                self.step()
            except Exception:
                # Log and continue; tests may want the loop to keep running even on transient errors
                _log.exception('Exception during state machine step')
            # Pace the loop to avoid busy spin
            time.sleep(self.loop_sleep_duration)
                
    @property
    def is_running(self):
        """Check if the state machine is running."""
        return self._running
        
    @property 
    def step_count(self):
        """Get the number of steps executed."""
        return self._step_count
        
    def reset_step_count(self):
        """Reset the step counter."""
        with self._lock:
            self._step_count = 0

    def run_for_steps(self, n: int, timeout: float | None = None) -> bool:
        """Run the real stepping code for n iterations in a dedicated thread and wait.

        Returns True if the runner thread completed within timeout, False if it is still alive.
        """
        def _runner(count: int):
            for _ in range(count):
                try:
                    self.step()
                except Exception:
                    _log.exception('Exception in run_for_steps runner')
                    break

        t = threading.Thread(target=_runner, args=(n,), daemon=True)
        t.start()
        t.join(timeout=timeout)
        return not t.is_alive()

    def publish(self, topic: str, message):
        """Record a published topic/message for tests and log it."""
        try:
            _log.debug(f"Publishing topic={topic}: {message}")
        except Exception:
            # Avoid failing tests if message isn't fully loggable
            _log.debug(f"Publishing topic={topic}")

        if not hasattr(self, 'published'):
            self.published = []
        self.published.append((topic, message))

    def configure_node(self, configs):
        """Wrap TransactiveNode.configure_node to populate a slice-assignable `markets` list for tests."""
        super().configure_node(configs)
        # Populate a simple list view of markets for compatibility with code that expects `tn.markets`.
        markets = []
        for series in getattr(self, 'market_series', []):
            try:
                markets.extend(list(series))
            except Exception:
                continue
        # ensure slice-assignable list exists and is in sync
        self.markets[:] = markets
