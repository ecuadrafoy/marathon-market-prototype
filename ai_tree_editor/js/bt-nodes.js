/* bt-nodes.js — register custom LiteGraph node types for behaviour trees.
 *
 * We use a fixed connection slot type "bt" so LiteGraph rejects edges
 * connecting non-tree nodes to tree nodes. Every BT node has exactly one
 * input slot named "parent" (except the root, which has none) and zero or
 * more output slots named "child" / "child0" / etc.
 *
 * Node types registered:
 *   bt/composite/Sequence        — N children, evaluated AND
 *   bt/composite/Selector        — N children, evaluated OR
 *   bt/decorator/Inverter        — single child, flips success/failure
 *   bt/leaf/<LeafName>           — one per registered Python condition,
 *                                   added dynamically from /catalog
 */

const BTNodes = (() => {
  const SLOT_TYPE = "bt";

  // ----- helpers ----------------------------------------------------------
  function _setUpComposite(node, kind, defaultChildSlots = 2) {
    node.kind = kind;                 // "sequence" | "selector"
    node.addInput("parent", SLOT_TYPE);
    for (let i = 0; i < defaultChildSlots; i++) {
      node.addOutput(`child${i}`, SLOT_TYPE);
    }
    node.size = [180, 60];
    node.color = "#5a3a1f";           // brown for composites
    node.bgcolor = "#2a1c10";
  }

  function _addChildSlot(node) {
    const idx = node.outputs.length;
    node.addOutput(`child${idx}`, SLOT_TYPE);
    node.setDirtyCanvas(true, true);
  }

  function _removeLastChildSlot(node) {
    if (node.outputs.length <= 1) return;
    // Disconnect any link on the slot we're removing first.
    const lastIdx = node.outputs.length - 1;
    node.disconnectOutput(lastIdx);
    node.outputs.pop();
    node.setDirtyCanvas(true, true);
  }

  // ----- Sequence ---------------------------------------------------------
  function SequenceNode() {
    _setUpComposite(this, "sequence");
    this.title = "Sequence";
    this.addProperty("label", "");
  }
  SequenceNode.title = "Sequence";
  SequenceNode.prototype.onExecute = function () {};
  SequenceNode.prototype.getMenuOptions = function () {
    return [
      { content: "Add child slot", callback: () => _addChildSlot(this) },
      { content: "Remove last child slot",
        callback: () => _removeLastChildSlot(this) },
    ];
  };

  // ----- Selector ---------------------------------------------------------
  function SelectorNode() {
    _setUpComposite(this, "selector");
    this.title = "Selector";
    this.addProperty("label", "");
  }
  SelectorNode.title = "Selector";
  SelectorNode.prototype.onExecute = function () {};
  SelectorNode.prototype.getMenuOptions = SequenceNode.prototype.getMenuOptions;

  // ----- Inverter ---------------------------------------------------------
  function InverterNode() {
    this.kind = "inverter";
    this.addInput("parent", SLOT_TYPE);
    this.addOutput("child", SLOT_TYPE);
    this.size = [160, 50];
    this.color = "#4a3a5a";           // purple for decorators
    this.bgcolor = "#1c1828";
    this.title = "Inverter";
    this.addProperty("label", "");
  }
  InverterNode.title = "Inverter";
  InverterNode.prototype.onExecute = function () {};

  // ----- Leaf factory -----------------------------------------------------
  // `spec` is one entry from the /catalog response.
  function makeLeafConstructor(spec) {
    function LeafNode() {
      this.kind = "leaf";
      this.leafId = spec.name;
      this.addInput("parent", SLOT_TYPE);
      this.title = spec.name;
      this.size = [180, Math.max(40, 30 + (spec.params || []).length * 24)];
      this.color = "#1f4a5a";           // teal for leaves
      this.bgcolor = "#0e2530";
      this.description = spec.description || "";
      this.addProperty("label", "");
      // Each declared parameter becomes an editable property.
      for (const param of spec.params || []) {
        this.addProperty(param.name, param.default);
      }
    }
    LeafNode.title = spec.name;
    LeafNode.prototype.onExecute = function () {};
    LeafNode.prototype.onMouseDown = function () { /* hook for future tooltip */ };
    return LeafNode;
  }

  // ----- Registration -----------------------------------------------------
  function registerBuiltins() {
    LiteGraph.registerNodeType("bt/composite/Sequence", SequenceNode);
    LiteGraph.registerNodeType("bt/composite/Selector", SelectorNode);
    LiteGraph.registerNodeType("bt/decorator/Inverter", InverterNode);
  }

  function registerLeaves(catalog) {
    // Drop any previously registered bt/leaf types so re-running the
    // palette refresh doesn't pile up duplicates.
    const existing = LiteGraph.registered_node_types || {};
    for (const key of Object.keys(existing)) {
      if (key.startsWith("bt/leaf/")) {
        LiteGraph.unregisterNodeType(key);
      }
    }
    for (const spec of catalog.leaves) {
      const path = `bt/leaf/${spec.name}`;
      LiteGraph.registerNodeType(path, makeLeafConstructor(spec));
    }
  }

  return {
    SLOT_TYPE,
    registerBuiltins,
    registerLeaves,
  };
})();
