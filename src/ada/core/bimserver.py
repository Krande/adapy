import base64
import io
import json
import operator
import urllib.request
import zipfile

import requests

urlopen = urllib.request.urlopen
deserializers = dict(ifc4="Ifc4 (Streaming)", ifc2x3="Ifc2x3tc1 (Streaming)")


class BimServerApi:
    """
    A minimal BIMserver.org API client. Interfaces are obtained from the server
    and can be retrieved as attributes from an API instance. The interfaces
    expose their methods as functions with keyword arguments.

    Example:
    import bimserver
    client = bimserver.api(server_address, username, password)
    client.Bimsie1ServiceInterface.addProject(projectName="My new project")
    """

    class interface:
        def __init__(self, api, name):
            self.api, self.name = api, name

        def make_request(self, method, **kwargs):
            request = urlopen(
                self.api.url,
                data=json.dumps(
                    dict(
                        {
                            "request": {
                                "interface": self.name,
                                "method": method,
                                "parameters": kwargs,
                            }
                        },
                        **({"token": self.api.token} if self.api.token else {}),
                    )
                ).encode("utf-8"),
            )
            response = json.loads(request.read().decode("utf-8"))
            exception = response.get("response", {}).get("exception", None)
            if exception:
                raise Exception(exception["message"])
            else:
                return response["response"]["result"]

        def __getattr__(self, method):
            return lambda **kwargs: self.make_request(method, **kwargs)

    token = None
    interfaces = None

    def __init__(self, hostname, username=None, password=None):
        self.url = "%s/json" % hostname
        if not hostname.startswith("http://") and not hostname.startswith("https://"):
            self.url = "http://%s" % self.url

        self.interfaces = set(
            map(
                operator.itemgetter("simpleName"),
                self.MetaInterface.getServiceInterfaces(),
            )
        )
        self.interfaces.add("generateRevisionDownloadUrl")

        self.version = "1.4" if "Bimsie1AuthInterface" in self.interfaces else "1.5"

        if username is not None and password is not None:
            auth_interface = getattr(self, "Bimsie1AuthInterface", getattr(self, "AuthInterface"))
            self.token = auth_interface.login(username=username, password=password)

    def __getattr__(self, interface):
        if self.interfaces is not None and interface not in self.interfaces:

            # Some form of compatibility:
            if self.version == "1.4" and not interface.startswith("Bimsie1"):
                return self.__getattr__("Bimsie1" + interface)
            elif self.version == "1.5" and interface.startswith("Bimsie1"):
                return self.__getattr__(interface[len("Bimsie1") :])

            raise AttributeError("'%s' is does not name a valid interface on this server" % interface)
        return BimServerApi.interface(self, interface)


def download_extract_zip(url):
    """
    Download a ZIP file and extract its contents in memory
    yields (filename, file-like object) pairs

    code from: https://techoverflow.net/2018/01/16/downloading-reading-a-zip-file-in-memory-using-python/

    """
    response = requests.get(url)
    with zipfile.ZipFile(io.BytesIO(response.content)) as thezip:
        for zipinfo in thezip.infolist():
            with thezip.open(zipinfo) as thefile:
                yield zipinfo.filename, thefile


class BimServerConnect:
    """
    BimServerConnect extends the bimserver json api and allows for simple pull/push operations on the server.

    ServiceInterface

    https://github.com/opensourceBIM/BIMserver/blob/8bc7413132ed934d13d4759ad5aadc1e9692f78b/PluginBase/src/org/bimserver/shared/interfaces/ServiceInterface.java

    :param bimserver_url:
    :param username: Valid username
    :param password: Password
    :param assembly:
    :param allow_create_project: Allow creation of projects
    :type assembly: ada.Assembly
    """

    def __init__(self, bimserver_url, username, password, assembly, allow_create_project=True):

        self._bimserver_url = bimserver_url
        self.client = BimServerApi(bimserver_url, username, password)
        self.assembly = assembly
        self._allow_create_project = allow_create_project

    def _get_project(self, project_name, allow_create_project):

        projects = self.client.ServiceInterface.getProjectsByName(name=project_name)

        if len(projects) > 1:
            raise ValueError("More than 1 project was found")
        elif len(projects) == 1:
            print(f'Project "{project_name}" was found!')
            project = projects[0]
        else:
            if allow_create_project is False:
                raise ValueError(f'The project "{project_name}" was not found')
            print(f'Creating new project with name "{project_name}"')
            project = self.client.ServiceInterface.addProject(projectName=project_name, schema="ifc4")
        return project

    def push(self, project_name, comment, merge=False, sync=False):
        """
        Push to BimServer

        :param project_name:
        :param comment: Commit message
        :param merge:
        :param sync:
        """

        deserializer_id = self.client.ServiceInterface.getDeserializerByName(
            deserializerName=deserializers["ifc4"]
        ).get("oid")

        # Write to IFC file and read file into memory
        ifc_f = "temp/exported.ifc"
        self.assembly.to_ifc(ifc_f)

        with open(ifc_f, "rb") as f:
            ifc_data = f.read()

        project = self._get_project(project_name, self._allow_create_project)
        project_id = project.get("oid")
        sync = "false" if sync is False else "true"
        merge = "false" if merge is False else "true"
        res = self.client.ServiceInterface.checkinSync(
            poid=project_id,
            comment=comment,
            deserializerOid=deserializer_id,
            fileSize=len(ifc_data),
            fileName=ifc_f,
            data=base64.b64encode(ifc_data).decode("utf-8"),
            sync=sync,
            merge=merge,
        )
        print(res)
        if "Error" in res["title"]:
            raise ValueError(res["title"])

    def pull(self, project_name, checkout):
        """

        :param project_name:
        :param checkout:
        :return:
        """
        import json

        project = self._get_project(project_name, self._allow_create_project)
        serializer = self.client.ServiceInterface.getSerializerByName(serializerName="Ifc4 (Streaming)")
        serializer_id = serializer.get("oid")

        if checkout is True:
            # Insert checkout code here
            pass

        roid = project.get("lastRevisionId")
        topicId = self.client.ServiceInterface.download(
            roids=[roid],
            serializerOid=serializer_id,
            query=json.dumps({}),
            sync="false",
        )

        download_url = f"{self._bimserver_url}/download?token={self.client.token}&zip=on&topicId={topicId}"
        for filename, file_obj in download_extract_zip(download_url):
            with open(filename, "wb") as d:
                d.write(file_obj.read())
                if self.assembly is not None:
                    self.assembly.read_ifc(filename)

        print("complete")


def bs_get_project(project_name, bimserver_url, username, password, allow_create_project=True, schema="ifc4"):
    """
    For manual interaction with bimserver

    :param project_name:
    :param bimserver_url:
    :param username:
    :param password:
    :param allow_create_project:
    :param schema:
    :return:
    """
    client = BimServerApi(bimserver_url, username, password)
    projects = client.ServiceInterface.getProjectsByName(name=project_name)

    if len(projects) > 1:
        raise ValueError("More than 1 project was found")
    elif len(projects) == 1:
        print(f'Project "{project_name}" was found!')
        project = projects[0]
    else:
        if allow_create_project is False:
            raise ValueError(f'The project "{project_name}" was not found')
        print(f'Creating new project with name "{project_name}"')
        project = client.ServiceInterface.addProject(projectName=project_name, schema=schema)
    return project


def bs_push(
    ifc_file,
    bimserver_url,
    username,
    password,
    project_name,
    comment,
    merge=False,
    sync=False,
    allow_create_project=True,
    schema="ifc4",
):
    """
    For manual interaction with bimserver

    :param ifc_file:
    :param bimserver_url:
    :param username:
    :param password:
    :param project_name:
    :param comment:
    :param merge:
    :param sync:
    :param allow_create_project:
    :param schema:
    :return:
    """
    client = BimServerApi(bimserver_url, username, password)

    deserializer_id = client.ServiceInterface.getDeserializerByName(deserializerName=deserializers[schema]).get("oid")

    # Write to IFC file and read file into memory

    with open(ifc_file, "rb") as f:
        ifc_data = f.read()

    project = bs_get_project(project_name, bimserver_url, username, password, allow_create_project, schema)
    project_id = project.get("oid")
    sync = "false" if sync is False else "true"
    merge = "false" if merge is False else "true"
    res = client.ServiceInterface.checkinSync(
        poid=project_id,
        comment=comment,
        deserializerOid=deserializer_id,
        fileSize=len(ifc_data),
        fileName=ifc_file,
        data=base64.b64encode(ifc_data).decode("utf-8"),
        sync=sync,
        merge=merge,
    )
    print(res)
    if "Error" in res["title"]:
        raise ValueError(res["title"])
