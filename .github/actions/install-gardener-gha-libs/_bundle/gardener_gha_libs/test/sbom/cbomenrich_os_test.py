#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''Unit tests for OS purl injection helpers in cbomenrich.py.'''
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
import sbom.cbomenrich as scbe


# ---------------------------------------------------------------------------
# _parse_os_release
# ---------------------------------------------------------------------------

def test_parse_os_release_basic():
    data = b'ID=alpine\nVERSION_ID=3.18\n'
    assert scbe._parse_os_release(data) == {'ID': 'alpine', 'VERSION_ID': '3.18'}


def test_parse_os_release_quoted_double():
    data = b'ID="debian"\nVERSION_ID="11"\n'
    result = scbe._parse_os_release(data)
    assert result['ID'] == 'debian'
    assert result['VERSION_ID'] == '11'


def test_parse_os_release_quoted_single():
    data = b"ID='ubuntu'\nVERSION_ID='22.04'\n"
    result = scbe._parse_os_release(data)
    assert result['ID'] == 'ubuntu'
    assert result['VERSION_ID'] == '22.04'


def test_parse_os_release_ignores_comments():
    data = b'# comment\nID=alpine\n# another\nVERSION_ID=3.18\n'
    result = scbe._parse_os_release(data)
    assert result == {'ID': 'alpine', 'VERSION_ID': '3.18'}


def test_parse_os_release_crlf():
    data = b'ID=alpine\r\nVERSION_ID=3.18\r\n'
    result = scbe._parse_os_release(data)
    assert result['ID'] == 'alpine'
    assert result['VERSION_ID'] == '3.18'


def test_parse_os_release_empty_lines():
    data = b'\nID=alpine\n\nVERSION_ID=3.18\n\n'
    result = scbe._parse_os_release(data)
    assert result['ID'] == 'alpine'
    assert result['VERSION_ID'] == '3.18'


def test_parse_os_release_non_utf8():
    data = b'ID=alpine\nVERSION_ID=3.18\xff\n'
    result = scbe._parse_os_release(data)
    assert result['ID'] == 'alpine'


# ---------------------------------------------------------------------------
# _synthesise_os_purl
# ---------------------------------------------------------------------------

def test_synthesise_alpine():
    purl, name, ver = scbe._synthesise_os_purl({'ID': 'alpine', 'VERSION_ID': '3.18'})
    assert purl == 'pkg:apk/alpine/alpine@3.18'
    assert name == 'alpine'
    assert ver == '3.18'


def test_synthesise_alpine_mixed_case():
    purl, name, ver = scbe._synthesise_os_purl({'ID': 'Alpine', 'VERSION_ID': '3.18'})
    assert purl == 'pkg:apk/alpine/alpine@3.18'


def test_synthesise_debian():
    purl, name, ver = scbe._synthesise_os_purl({'ID': 'debian', 'VERSION_ID': '11'})
    assert purl == 'pkg:deb/debian/base-files@11'
    assert name == 'base-files'


def test_synthesise_ubuntu():
    purl, name, ver = scbe._synthesise_os_purl({'ID': 'ubuntu', 'VERSION_ID': '22.04'})
    assert purl == 'pkg:deb/ubuntu/base-files@22.04'
    assert name == 'base-files'


def test_synthesise_rhel():
    purl, name, ver = scbe._synthesise_os_purl({'ID': 'rhel', 'VERSION_ID': '8.7'})
    assert purl == 'pkg:rpm/rhel/redhat-release@8.7'
    assert name == 'redhat-release'


def test_synthesise_centos():
    purl, name, ver = scbe._synthesise_os_purl({'ID': 'centos', 'VERSION_ID': '7'})
    assert purl == 'pkg:rpm/rhel/redhat-release@7'


def test_synthesise_ubi():
    purl, name, ver = scbe._synthesise_os_purl({'ID': 'ubi8', 'VERSION_ID': '8.7'})
    assert purl == 'pkg:rpm/rhel/redhat-release@8.7'


def test_synthesise_missing_version_id():
    assert scbe._synthesise_os_purl({'ID': 'alpine'}) is None


def test_synthesise_empty_version_id():
    assert scbe._synthesise_os_purl({'ID': 'alpine', 'VERSION_ID': ''}) is None


def test_synthesise_non_numeric_version_id():
    assert scbe._synthesise_os_purl({'ID': 'debian', 'VERSION_ID': 'bullseye'}) is None


def test_synthesise_unknown_distro():
    assert scbe._synthesise_os_purl({'ID': 'wolfi', 'VERSION_ID': '1.0'}) is None


# ---------------------------------------------------------------------------
# _has_os_purl
# ---------------------------------------------------------------------------

def test_has_os_purl_apk():
    comps = [{'purl': 'pkg:apk/alpine/alpine@3.18'}]
    assert scbe._has_os_purl(comps) is True


def test_has_os_purl_deb():
    comps = [{'purl': 'pkg:deb/debian/base-files@11'}]
    assert scbe._has_os_purl(comps) is True


def test_has_os_purl_rpm():
    comps = [{'purl': 'pkg:rpm/rhel/redhat-release@8.7'}]
    assert scbe._has_os_purl(comps) is True


def test_has_os_purl_false():
    comps = [{'purl': 'pkg:golang/somelib@1.0'}]
    assert scbe._has_os_purl(comps) is False


def test_has_os_purl_empty():
    assert scbe._has_os_purl([]) is False


# ---------------------------------------------------------------------------
# enrich_sbom
# ---------------------------------------------------------------------------

def _minimal_cdx(components=None):
    doc = {'bomFormat': 'CycloneDX', 'specVersion': '1.6', 'components': components or []}
    return json.dumps(doc).encode()


def _make_reader(data):
    def reader(path):
        if path == '/etc/os-release':
            return data
        return None
    return reader


_ALPINE_OS_RELEASE = b'ID=alpine\nVERSION_ID=3.18\n'


def test_enrich_sbom_injects_alpine():
    result = scbe.enrich_sbom(_minimal_cdx(), _make_reader(_ALPINE_OS_RELEASE))
    doc = json.loads(result)
    comps = doc['components']
    assert len(comps) == 1
    c = comps[0]
    assert c['purl'] == 'pkg:apk/alpine/alpine@3.18'
    assert c['name'] == 'alpine'
    assert c['version'] == '3.18'
    assert c['type'] == 'library'
    assert c['properties'] == [
        {'name': 'gardener.cloud/sbom/source', 'value': 'os-release-injection'}
    ]


def test_enrich_sbom_idempotent():
    once = scbe.enrich_sbom(_minimal_cdx(), _make_reader(_ALPINE_OS_RELEASE))
    twice = scbe.enrich_sbom(once, _make_reader(_ALPINE_OS_RELEASE))
    assert json.loads(once)['components'] == json.loads(twice)['components']


def test_enrich_sbom_no_os_release():
    result = scbe.enrich_sbom(_minimal_cdx(), lambda p: None)
    assert json.loads(result)['components'] == []


def test_enrich_sbom_unrecognised_distro():
    data = b'ID=wolfi\nVERSION_ID=1.0\n'
    result = scbe.enrich_sbom(_minimal_cdx(), _make_reader(data))
    assert json.loads(result)['components'] == []


def test_enrich_sbom_existing_os_purl_unchanged():
    existing = [{'purl': 'pkg:apk/alpine/alpine@3.17', 'type': 'library', 'name': 'alpine'}]
    cdx = _minimal_cdx(existing)
    result = scbe.enrich_sbom(cdx, _make_reader(_ALPINE_OS_RELEASE))
    assert json.loads(result)['components'] == existing


def test_enrich_sbom_debian():
    data = b'ID=debian\nVERSION_ID=11\nVERSION_CODENAME=bullseye\n'
    result = scbe.enrich_sbom(_minimal_cdx(), _make_reader(data))
    comps = json.loads(result)['components']
    assert comps[0]['purl'] == 'pkg:deb/debian/base-files@11'
