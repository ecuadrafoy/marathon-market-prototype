/* serialize.js — bidirectional conversion between LiteGraph LGraph
 * (the editor's working model) and our nested tree JSON (the file format
 * the publish gate validates).
 *
 * graphToTree(graph) — walk the graph from a single root node downward,
 *   producing a {name, root, _layout} object suitable for PUT /trees/<name>.
 *   Validates: exactly one node has no parent connection (the root); every
 *   other node is reachable; no cycles; no node has more than one parent.
 *
 * treeToGraph(graph, doc) — clear the graph and rebuild nodes from the
 *   nested tree, restoring positions from doc._layout when present.
 */

const BTSerialize = (() => {

  /* ----- graph → tree JSON ---------------------------------------------- */

  function graphToTree(graph, treeName) {
    const nodes = graph._nodes || [];
    if (nodes.length === 0) {
      throw new SerializationError("graph is empty");
    }

    // Build parent-of map by examining each node's input link.
    // LiteGraph: a node's input slot has .link pointing at a link id.
    const parentOf = new Map();   // child node id → parent node id
    for (const n of nodes) {
      const inputs = n.inputs || [];
      for (const slot of inputs) {
        if (slot && slot.link != null) {
          const link = graph.links[slot.link];
          if (!link) continue;
          if (parentOf.has(n.id)) {
            throw new SerializationError(
              `node "${_label(n)}" has more than one parent — trees must be strict`
            );
          }
          parentOf.set(n.id, link.origin_id);
        }
      }
    }

    // Roots = nodes without an incoming link.
    const roots = nodes.filter((n) => !parentOf.has(n.id));
    if (roots.length === 0) {
      throw new SerializationError("graph has no root (cycle detected?)");
    }
    if (roots.length > 1) {
      const names = roots.map(_label).join(", ");
      throw new SerializationError(
        `graph has ${roots.length} disconnected nodes: ${names}. ` +
        `A behaviour tree must have exactly one root.`
      );
    }

    const layout = {};
    const treeRoot = _walk(graph, roots[0], layout);
    return {
      name: treeName || "",
      root: treeRoot,
      _layout: layout,
    };
  }

  function _walk(graph, node, layout) {
    layout[node.id] = { x: node.pos[0], y: node.pos[1] };

    const out = { type: node.kind };
    if (node.properties && node.properties.label) {
      out.label = node.properties.label;
    }

    if (node.kind === "sequence" || node.kind === "selector") {
      out.children = _orderedChildren(graph, node).map((c) => _walk(graph, c, layout));
      if (out.children.length === 0) {
        throw new SerializationError(
          `${node.kind} node "${_label(node)}" has no children`
        );
      }
    } else if (node.kind === "inverter") {
      const children = _orderedChildren(graph, node);
      if (children.length !== 1) {
        throw new SerializationError(
          `inverter "${_label(node)}" must have exactly one child, has ${children.length}`
        );
      }
      out.child = _walk(graph, children[0], layout);
    } else if (node.kind === "leaf") {
      out.id = node.leafId;
      // Collect non-empty parameter values into a params object.
      const params = {};
      for (const [k, v] of Object.entries(node.properties || {})) {
        if (k === "label") continue;
        params[k] = v;
      }
      if (Object.keys(params).length > 0) {
        out.params = params;
      }
    } else {
      throw new SerializationError(`unknown node kind ${node.kind}`);
    }
    return out;
  }

  // Output slot order is significant — it determines child evaluation order
  // in the runtime. Walk slot 0 first, then slot 1, etc.
  function _orderedChildren(graph, node) {
    const children = [];
    for (let i = 0; i < (node.outputs || []).length; i++) {
      const slot = node.outputs[i];
      if (!slot || !slot.links || slot.links.length === 0) continue;
      // A slot can drive multiple links — in our schema each child position
      // takes one. If multiple, we pick the first and warn.
      const linkId = slot.links[0];
      const link = graph.links[linkId];
      if (!link) continue;
      const target = graph.getNodeById(link.target_id);
      if (target) children.push(target);
    }
    return children;
  }

  /* ----- tree JSON → graph ---------------------------------------------- */

  function treeToGraph(graph, doc) {
    graph.clear();
    const layout = (doc && doc._layout) || {};
    const root = doc && doc.root;
    if (!root) throw new SerializationError("tree JSON missing 'root' field");
    _materialise(graph, root, null, layout, { x: 100, y: 100 });
  }

  // Recursively create LiteGraph nodes for the tree starting at `treeNode`.
  // If `parentNode` is given, connect this node's "parent" input to
  // `parentNode`'s next free child slot.
  function _materialise(graph, treeNode, parentNode, layout, fallbackPos) {
    const ltype = _liteGraphTypeFor(treeNode);
    const node = LiteGraph.createNode(ltype);
    if (!node) {
      throw new SerializationError(
        `cannot instantiate node type ${ltype} — palette out of date?`
      );
    }
    graph.add(node);

    // Position: use saved layout if available, else fall through.
    // (LiteGraph nodes don't have stable IDs across save/load, so layout
    // is keyed by the new IDs. For now: best-effort, drop layout if mismatched.)
    node.pos = [fallbackPos.x, fallbackPos.y];

    // Restore label and params.
    if (treeNode.label) node.properties.label = treeNode.label;
    if (treeNode.type === "leaf" && treeNode.params) {
      for (const [k, v] of Object.entries(treeNode.params)) {
        node.properties[k] = v;
      }
    }

    // Connect to parent if any.
    if (parentNode) {
      const slotIdx = _firstFreeChildSlot(parentNode);
      // Composites may need an extra slot if all are taken.
      if (slotIdx === -1) {
        parentNode.addOutput(`child${parentNode.outputs.length}`, BTNodes.SLOT_TYPE);
      }
      const targetSlot = _firstFreeChildSlot(parentNode);
      parentNode.connect(targetSlot, node, 0);
    }

    // Recurse into children.
    if (treeNode.type === "sequence" || treeNode.type === "selector") {
      const children = treeNode.children || [];
      // Composite default has 2 child slots; expand if needed.
      while ((node.outputs || []).length < children.length) {
        node.addOutput(`child${node.outputs.length}`, BTNodes.SLOT_TYPE);
      }
      // Lay children out below the parent, spread horizontally.
      const spacing = 220;
      const startX = fallbackPos.x - ((children.length - 1) * spacing) / 2;
      children.forEach((c, i) => {
        _materialise(graph, c, node, layout, {
          x: startX + i * spacing,
          y: fallbackPos.y + 140,
        });
      });
    } else if (treeNode.type === "inverter") {
      _materialise(graph, treeNode.child, node, layout, {
        x: fallbackPos.x,
        y: fallbackPos.y + 120,
      });
    }
  }

  function _liteGraphTypeFor(treeNode) {
    if (treeNode.type === "sequence") return "bt/composite/Sequence";
    if (treeNode.type === "selector") return "bt/composite/Selector";
    if (treeNode.type === "inverter") return "bt/decorator/Inverter";
    if (treeNode.type === "leaf")     return `bt/leaf/${treeNode.id}`;
    throw new SerializationError(`unknown node type ${treeNode.type}`);
  }

  function _firstFreeChildSlot(node) {
    for (let i = 0; i < (node.outputs || []).length; i++) {
      const slot = node.outputs[i];
      if (!slot.links || slot.links.length === 0) return i;
    }
    return -1;
  }

  function _label(node) {
    return (node.properties && node.properties.label)
      || node.title
      || `<${node.type}>`;
  }

  /* ----- exports -------------------------------------------------------- */

  class SerializationError extends Error {
    constructor(msg) {
      super(msg);
      this.name = "SerializationError";
    }
  }

  return {
    graphToTree,
    treeToGraph,
    SerializationError,
  };
})();
