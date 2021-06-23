# TENT
The **Transactive Energy Network Template (TENT)** metamodel was developed to guide an implementer to establish its own transactive agent within a network of such agents. The object-oriented design of the transactive network template provides basic classes that may be extended to represent local assets. The classes further facilitate the transactive agent’s necessary computations, which are divided among responsibilities to schedule power usage, balance electric supply and demand using price signals, and coordinate the exchange of electricity with the other transactive agents.

This git repository includes the first reference implementation of the transactive energy network template’s classes using Python programming language. This reference implementation has been integrated and tested on Volttron™ platforms to help commercial buildings, a campus, and a municipality transact for electricity. 

Certain VOLTTRON-specific code has been allowed to creep into this reference implementation. However, the reference implementation should be applicable using other computational platforms as well.

Important base classes:
-	Transactive Node
-	Local Asset (an interface)
-	Transactive Neighbor (an interface)
-	Market
    -	Auction
    -	Consensus

