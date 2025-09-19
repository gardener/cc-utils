import ocm.gardener


def test_eval_version_template():
    assert ocm.gardener.eval_version_template(
        version_template=ocm.gardener.VersionTemplate.from_dict({
            'type': 'jq',
            'expr': '."attr-1"',
        }),
        image_dict={
            'attr-1': 'foo',
            'attr-2': 'ignoreme',
        },
    ) == 'foo'
