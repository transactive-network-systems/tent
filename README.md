# TENT
The **Transactive Energy Network Template (TENT)** metamodel was developed to guide an implementer to establish its own transactive agent within a network of such agents. The object-oriented design of the transactive network template provides basic classes that may be extended to represent local assets. The classes further facilitate the transactive agent’s necessary computations, which are divided among responsibilities to schedule power usage, balance electric supply and demand using price signals, and coordinate the exchange of electricity with the other transactive agents.

This git repository includes the first reference implementation of the transactive energy network template’s classes using Python programming language. This reference implementation has been integrated and tested on Volttron™ platforms (https://github.com/VOLTTRON/volttron-pnnl-applications) to help commercial buildings, a campus, and a municipality transact for electricity. 

Certain VOLTTRON-specific code has been allowed to creep into this reference implementation. However, the reference implementation should be applicable using other computational platforms as well.

Important base classes:
-	Transactive Node
-	Local Asset (an interface)
-	Transactive Neighbor (an interface)
-	Market
    -	Auction
    -	Consensus


Development
-----------

Dependencies are declared once, in `pyproject.toml` (PEP 621). Python 3.11 or newer is required.

Install for development with pip:

    pip install -e ".[dev]"

or with Poetry:

    poetry install --with dev

or with pixi (https://pixi.sh), which also provisions the interpreter from conda-forge:

    pixi install
    pixi run test            # whole suite; also test-unit and test-integration

The legacy plotting scripts under `scripts/` need the optional `scripts` extra (`pip install -e ".[scripts]"`).


