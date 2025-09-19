import logging
from typing import Callable

from tent.transactive_node import TransactiveNode

logging.basicConfig(filename=f"/home/dmr/Projects/tsp/tent/mock_tn.log", level=logging.DEBUG)
_log = logging.getLogger(__name__)
_log.debug('Running stuff.')
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
            mock_tn._state_machine_iteration
            mock_tn.recipients['peerA'].assert_called_with({'expected': 'signal'})
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recipients: dict[str, Callable] = {}
        self.subscriptions: dict[str, Callable] = {}

    def register_recipient(self, recipient: str, callback: Callable):
        self.recipients[recipient] = callback

    def issue_signal(self, recipient: str, signal_dict: dict):
        if callback := self.recipients[recipient]:
            callback(signal_dict)

    def register_subscription(self, subscribe_key: str, callback: Callable):
        self.subscriptions[subscribe_key] = callback

    def provide_subscription(self, subscribe_key: str, message):
        if callback := self.subscriptions.get(subscribe_key):
            callback(message)

    def _sleep(self):
        """This method should sleep for self.loop_sleep_duration as is appropriate
            to the multitasking system employed by the agent.
        """
        pass

    def state_machine_loop(self):
        """This method should call _state_machine_iteration in a loop until stopped."""
        pass
