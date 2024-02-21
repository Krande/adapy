from __future__ import absolute_import, division, print_function

import time
import uuid

from ifcopenshell import main
from ifcopenshell.file import file as ifc_file
from ifcopenshell.guid import compress

# A quick way to setup an 'empty' IFC file, taken from:
# http://academy.ifcopenshell.org/creating-a-simple-wall-with-property-set-and-quantity-information/
TEMPLATE = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');
FILE_NAME('%(filename)s','%(timestring)s',('%(user_id)s'),('%(organization)s'),'%(application)s','%(application)s','');
FILE_SCHEMA(('%(schema_identifier)s'));
ENDSEC;
DATA;
#1=IFCPERSON('%(user_id)s',$,$,$,$,$,$,$);
#2=IFCORGANIZATION('%(organization)s','%(org_name)s',$,$,$);
#3=IFCPERSONANDORGANIZATION(#1,#2,$);
#4=IFCAPPLICATION(#2,'%(application_version)s','%(application)s','');
#5=IFCOWNERHISTORY(#3,#4,$,.ADDED.,$,#3,#4,%(timestamp)s);
#6=IFCDIRECTION((1.,0.,0.));
#7=IFCDIRECTION((0.,0.,1.));
#8=IFCCARTESIANPOINT((0.,0.,0.));
#9=IFCAXIS2PLACEMENT3D(#8,#7,#6);
#10=IFCDIRECTION((0.,1.,0.));
#11=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,#9,#10);
#12=IFCDIMENSIONALEXPONENTS(0,0,0,0,0,0,0);
#13=IFCSIUNIT(*,.LENGTHUNIT.,%(units_str)s);
#14=IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.);
#15=IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.);
#16=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);
#17=IFCMEASUREWITHUNIT(IFCPLANEANGLEMEASURE(0.017453292519943295),#16);
#18=IFCCONVERSIONBASEDUNIT(#12,.PLANEANGLEUNIT.,'DEGREE',#17);
#19=IFCUNITASSIGNMENT((#13,#14,#15,#18));
#20=IFCPROJECT('%(project_globalid)s',#5,'%(project_name)s',$,$,$,$,(#11),#19);
ENDSEC;
END-ISO-10303-21;
"""

DEFAULTS = {
    "application": lambda d: "IfcOpenShell-%s" % main.version,
    "application_version": lambda d: main.version,
    "project_globalid": lambda d: compress(uuid.uuid4().hex),
    "schema_identifier": lambda d: main.schema_identifier,
    "timestamp": lambda d: int(time.time()),
    "timestring": lambda d: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(d.get("timestamp") or time.time())),
}


def tpl_create(
    filename=None,
    timestring=None,
    organization=None,
    user_id=None,
    schema_identifier=None,
    application_version=None,
    timestamp=None,
    application=None,
    project_globalid=None,
    project_name=None,
    units_str=None,
    org_name=None,
):
    d = dict(locals())

    def _():
        for var, value in d.items():
            if value is None:
                yield var, DEFAULTS.get(var, lambda *args: "")(d)

    d.update(dict(_()))

    return ifc_file.from_string(TEMPLATE % d)
