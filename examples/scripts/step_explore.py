import pathlib

from OCC.Core.XCAFPrs import XCAFPrs_DocumentExplorer, XCAFPrs_DocumentExplorerFlags_OnlyLeafNodes

from ada.occ.step.reader import StepStore, get_color, StepShape

FILES_DIR = pathlib.Path(__file__).parent.parent.parent / "files/step_files"
COLOR_FILE = FILES_DIR / "as1-oc-214.stp"
if not COLOR_FILE.exists():
    raise FileNotFoundError(f"File {COLOR_FILE} not found")

def node_to_step_shape(doc_node, store: StepStore, num_shapes: int)
    """Convert a node from a STEP file to a STEP shape"""
    shape = store.shape_tool.GetShape(doc_node.RefLabel)
    label = doc_node.RefLabel
    name = label.GetLabelName()
    rgb = get_color(store.color_tool, shape, label)
    return StepShape(shape, rgb, num_shapes, name)

def iter_childs(doc_node):
    """Iterate over all child nodes of a given node"""
    child_iter = doc_node.ChildIter()
    while child_iter.More():
        yield child
        child = child.Next()

def main():
    step_store = StepStore(COLOR_FILE)
    caf_reader = step_store.create_step_reader(True)
    doc_exp = XCAFPrs_DocumentExplorer(step_store.doc, XCAFPrs_DocumentExplorerFlags_OnlyLeafNodes)
    while doc_exp.More():
        doc_node = doc_exp.Current()
        shape = step_store.shape_tool.GetShape(doc_node.RefLabel)
        label = doc_node.RefLabel
        name = label.GetLabelName()
        rgb = get_color(doc_exp.ColorTool(), shape, label)
        print(f"Name: {name}, Color: {rgb}")
        doc_exp.Next()


if __name__ == "__main__":
    main()
