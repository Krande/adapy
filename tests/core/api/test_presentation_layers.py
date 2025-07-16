import pytest

import ada
from ada import Beam
from ada.api.presentation_layers import PresentationLayer, PresentationLayers
from ada.base.changes import ChangeAction


@pytest.fixture
def simple_part():
    """Create a simple part with a beam for testing"""
    part = ada.Part("TestPart")
    beam = ada.Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")
    part.add_beam(beam)
    return part


def test_presentation_layer_creation():
    """Test PresentationLayer creation"""
    layer = PresentationLayer("TestLayer", "Test description")

    assert layer.name == "TestLayer"
    assert layer.description == "Test description"
    assert layer.members == []
    assert layer.change_type == ChangeAction.NOTDEFINED
    assert layer.identifier is not None
    assert len(layer.identifier) > 0


def test_presentation_layer_with_members():
    """Test PresentationLayer with members"""
    beam = ada.Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")
    layer = PresentationLayer("TestLayer", "Test description", members=[beam])

    assert len(layer.members) == 1
    assert layer.members[0] == beam


def test_presentation_layers_creation():
    """Test PresentationLayers creation"""
    layers = PresentationLayers()

    assert layers.layers == {}


def test_add_layer_string():
    """Test adding layer by string name"""
    layers = PresentationLayers()

    layer = layers.add_layer("TestLayer", "Test description")

    assert isinstance(layer, PresentationLayer)
    assert layer.name == "TestLayer"
    assert layer.description == "Test description"
    assert layer.change_type == ChangeAction.ADDED
    assert "TestLayer" in layers.layers


def test_add_layer_object():
    """Test adding layer by PresentationLayer object"""
    layers = PresentationLayers()
    layer_obj = PresentationLayer("TestLayer", "Test description")

    added_layer = layers.add_layer(layer_obj)

    assert added_layer == layer_obj
    assert "TestLayer" in layers.layers
    assert layers.layers["TestLayer"] == layer_obj


def test_add_duplicate_layer():
    """Test adding duplicate layer raises ValueError"""
    layers = PresentationLayers()
    layers.add_layer("TestLayer", "Test description")

    with pytest.raises(ValueError, match='Existing Layer with name="TestLayer"'):
        layers.add_layer("TestLayer", "Another description")


def test_add_duplicate_layer_object():
    """Test adding duplicate layer object raises ValueError"""
    layers = PresentationLayers()
    layer_obj = PresentationLayer("TestLayer", "Test description")
    layers.add_layer(layer_obj)

    duplicate_layer = PresentationLayer("TestLayer", "Another description")
    with pytest.raises(ValueError, match="Existing Layer with name="):
        layers.add_layer(duplicate_layer)


def test_get_by_name_existing():
    """Test getting existing layer by name"""
    layers = PresentationLayers()
    added_layer = layers.add_layer("TestLayer", "Test description")

    retrieved_layer = layers.get_by_name("TestLayer")

    assert retrieved_layer == added_layer
    assert retrieved_layer.name == "TestLayer"


def test_get_by_name_nonexistent():
    """Test getting non-existent layer by name returns None"""
    layers = PresentationLayers()

    retrieved_layer = layers.get_by_name("NonExistentLayer")

    assert retrieved_layer is None


def test_add_object_to_existing_layer():
    """Test adding object to existing layer"""
    layers = PresentationLayers()
    layers.add_layer("TestLayer", "Test description")
    beam = Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")

    layers.add_object(beam, "TestLayer")

    layer = layers.get_by_name("TestLayer")
    assert len(layer.members) == 1
    assert layer.members[0] == beam
    assert layer.change_type == ChangeAction.MODIFIED


def test_add_object_to_nonexistent_layer():
    """Test adding object to non-existent layer creates new layer"""
    layers = PresentationLayers()
    beam = Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")

    layers.add_object(beam, "NewLayer")

    layer = layers.get_by_name("NewLayer")
    assert layer is not None
    assert len(layer.members) == 1
    assert layer.members[0] == beam
    assert layer.change_type == ChangeAction.ADDED


def test_add_part_to_layer(simple_part):
    """Test adding Part object to layer"""
    layers = PresentationLayers()

    layers.add_object(simple_part, "TestLayer")

    layer = layers.get_by_name("TestLayer")
    assert layer is not None
    # Should add all physical objects from the part
    assert len(layer.members) >= 1  # At least the beam from the part


def test_remove_layer_and_delete_objects():
    """Test removing layer and marking objects for deletion"""
    layers = PresentationLayers()
    beam = ada.Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")

    layers.add_object(beam, "TestLayer")

    # Verify layer exists and has members
    layer = layers.get_by_name("TestLayer")
    assert layer is not None
    assert len(layer.members) == 1

    # Remove layer
    layers.remove_layer_and_delete_objects("TestLayer")

    # Verify layer is removed
    assert layers.get_by_name("TestLayer") is None
    assert "TestLayer" not in layers.layers

    # Verify object is marked for deletion
    assert beam.change_type == ChangeAction.DELETED


def test_multiple_layers(tmp_path):
    """Test working with multiple layers"""
    layers = PresentationLayers()

    # Add multiple layers
    layer1 = layers.add_layer("Layer1", "First layer")
    layer2 = layers.add_layer("Layer2", "Second layer")

    # Add objects to different layers
    beam1 = ada.Beam("beam1", (0, 0, 0), (1, 0, 0), "IPE300")
    beam2 = ada.Beam("beam2", (0, 1, 0), (1, 1, 0), "IPE400")

    layers.add_object(beam1, "Layer1")
    layers.add_object(beam2, "Layer2")

    # Verify layers are separate
    assert len(layers.layers) == 2
    l1 = layers.get_by_name("Layer1")
    assert l1 == layer1
    l2 = layers.get_by_name("Layer2")
    assert l2 == layer2
    assert l1.members[0] == beam1
    assert l2.members[0] == beam2

    a = ada.Assembly() / (ada.Part("PresentationLayerPart") / (beam1, beam2))
    a.presentation_layers = layers

    a.to_ifc(tmp_path / "test_layers.ifc", validate=True)


def test_layer_change_types():
    """Test layer change type tracking"""
    layers = PresentationLayers()

    # New layer should have ADDED change type
    layer = layers.add_layer("TestLayer", "Test description")
    assert layer.change_type == ChangeAction.ADDED

    # Adding object to existing layer should change to MODIFIED
    beam = Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")
    layers.add_object(beam, "TestLayer")

    layer = layers.get_by_name("TestLayer")
    assert layer.change_type == ChangeAction.MODIFIED
