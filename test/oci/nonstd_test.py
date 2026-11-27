import unittest.mock

import oci.client as oc
import oci.model as om
import oci.nonstd as nonstd


def _resp(repositories, link=None):
    r = unittest.mock.MagicMock()
    r.json.return_value = {'repositories': repositories}
    r.headers = {'Link': link} if link else {}
    return r


def test_iter_repositories_catalog_stops_on_cross_host_next_url():
    # page 1 yields a repo and a Link pointing at a FOREIGN host; iteration must stop rather than
    # follow it with the registry's catalog-scoped auth
    client = unittest.mock.MagicMock(spec=oc.Client)
    client.disable_tls_validation = False

    requested_urls = []

    def fake_request(url, image_reference, scope):
        requested_urls.append(url)
        return _resp(
            ['team/a'],
            link='<https://attacker.example/v2/_catalog?last=a>; rel="next"',
        )

    client._request.side_effect = fake_request

    image_reference = om.OciImageReference('eu.gcr.io/team/a:1')
    repos = list(nonstd._iter_repositories_catalog(client=client, image_reference=image_reference))

    assert repos == ['team/a']
    # exactly one request (the first page); the cross-host next-url was NOT followed
    assert len(requested_urls) == 1
    assert not any('attacker.example' in u for u in requested_urls)


def test_iter_repositories_catalog_follows_same_host_next_url():
    # a same-host relative next-url must be followed (rebased against the registry host)
    client = unittest.mock.MagicMock(spec=oc.Client)
    client.disable_tls_validation = False

    pages = [
        _resp(['team/a'], link='</v2/_catalog?last=a>; rel="next"'),
        _resp(['team/b']),
    ]
    requested_urls = []

    def fake_request(url, image_reference, scope):
        requested_urls.append(url)
        return pages[len(requested_urls) - 1]

    client._request.side_effect = fake_request

    image_reference = om.OciImageReference('eu.gcr.io/team/a:1')
    repos = list(nonstd._iter_repositories_catalog(client=client, image_reference=image_reference))

    assert repos == ['team/a', 'team/b']
    assert len(requested_urls) == 2
    assert all('eu.gcr.io' in u for u in requested_urls)
