import json
from tests.mock_tn import MockTN

tn = MockTN()
with open('sample_configs/tn.campus.json') as f:
    configs = json.load(f)
tn.configure_node(configs)

# TODO: Step through the state machine.

