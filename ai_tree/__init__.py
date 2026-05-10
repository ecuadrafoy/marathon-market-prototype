"""Generic behaviour-tree engine.

Game-agnostic: registry of leaf nodes, composite node types, JSON loader/walker,
and the publish gate. Game-specific leaves are registered by importing modules
that decorate functions with @bt_condition / @bt_action.
"""
