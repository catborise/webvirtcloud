import base64

from lxml import etree
from vrtManager.connection import wvmConnect

# the element of <usage> that holds the value, per usage type
USAGE_ELEMENTS = {"ceph": "name", "volume": "volume", "iscsi": "target"}


class wvmSecrets(wvmConnect):
    def create_secret(self, ephemeral, private, secret_type, data):
        # built with lxml: data is text, whatever characters it holds
        secret = etree.Element("secret", ephemeral=ephemeral, private=private)
        usage = etree.SubElement(secret, "usage", type=secret_type)
        etree.SubElement(usage, USAGE_ELEMENTS[secret_type]).text = data
        self.wvm.secretDefineXML(etree.tostring(secret, encoding="unicode"))

    def set_secret_value(self, uuid, value):
        """value: the new value as base64 text."""
        secrt = self.get_secret(uuid)
        # validate: a value that is not base64 is refused, not decoded to other bytes
        secrt.setValue(base64.b64decode(value, validate=True))

    def delete_secret(self, uuid):
        secrt = self.get_secret(uuid)
        secrt.undefine()
