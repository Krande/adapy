import pathlib

import bcf.v3.model as mdl
import pytest
from bcf.v3.bcfxml import BcfXml
from bcf.v3.topic import TopicHandler
from bcf.xml_parser import XmlParserSerializer


@pytest.fixture(scope="session")
def xml_handler() -> XmlParserSerializer:
    return XmlParserSerializer()


@pytest.fixture()
def build_sample(xml_handler: XmlParserSerializer) -> tuple[BcfXml, TopicHandler]:
    ext = mdl.Extensions(topic_types=mdl.ExtensionsTopicTypes(topic_type=["Test type"]))
    bcf = BcfXml.create_new("Test project", extensions=ext, xml_handler=xml_handler)
    orig_th = bcf.add_topic("Test topic", "Test message", "Test author", "Test type")
    return bcf, orig_th


def test_save_view(xml_handler, build_sample, tmp_path):
    bcf, orig_th = build_sample
    file_path = pathlib.Path(tmp_path) / "test.bcf"
    bcf.save(file_path)
    with BcfXml.load(file_path, xml_handler=xml_handler) as parsed:
        assert parsed == bcf
        parsed_th = parsed.topics[orig_th.guid]
        assert parsed_th == orig_th
