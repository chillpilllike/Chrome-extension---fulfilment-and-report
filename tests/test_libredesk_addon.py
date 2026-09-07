import ast
import base64
import hashlib
import hmac
import json
import pathlib
import unittest
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
ROOT = pathlib.Path(__file__).resolve().parents[1] / 'odoo-addons/libredesk_website'

def function(path, name, context):
    tree = ast.parse(path.read_text())
    node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), context)
    return context[name]

class AddonTests(unittest.TestCase):
    def test_host_matching(self):
        host = function(ROOT/'models/website.py', 'host', {'urlsplit':urlsplit})
        self.assertEqual(host('https://WWW.Nutricity.ca/shop'), 'nutricity.ca')
        self.assertNotEqual(host('https://nutricity.ca.evil.example'), host('https://nutricity.ca'))
        self.assertEqual(host(''), '')
    def test_jwt_signature_and_scope(self):
        sign = function(ROOT/'controllers/chat.py','sign',dict(base64=base64,hashlib=hashlib,hmac=hmac,json=json))
        payload = {'external_user_id':'odoo:test:1:22','iat':100,'exp':3700}
        token=sign(payload,'test-secret')
        head,body,sig=token.split('.')
        expected=base64.urlsafe_b64encode(hmac.new(b'test-secret',(head+'.'+body).encode(),hashlib.sha256).digest()).rstrip(b'=').decode()
        self.assertEqual(sig, expected)
        self.assertEqual(json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4))),payload)
        self.assertNotEqual(sign(payload,'other-secret'),token)
    def test_sources_parse(self):
        for path in ROOT.rglob('*.py'):ast.parse(path.read_text())
        for path in ROOT.rglob('*.xml'):ET.parse(path)
        manifest=ast.literal_eval((ROOT/'__manifest__.py').read_text())
        for path in manifest['data']:self.assertTrue((ROOT/path).is_file())
if __name__=='__main__':unittest.main()
