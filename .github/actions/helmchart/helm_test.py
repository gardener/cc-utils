import helm


def test_to_ocm_mapping():
    mappings = [
        helm.HelmchartValueMapping(
            ref='ocm-resource:resource-1.repository',
            attribute='example.attribute.repo-1',
        ),
        helm.HelmchartValueMapping(
            ref='ocm-resource:resource-1.tag',
            attribute='example.attribute.tag-1',
        ),
        helm.HelmchartValueMapping(
            ref='ocm-resource:resource-1.image',
            attribute='example.attribute.image-1',
        ),

        helm.HelmchartValueMapping(
            ref='ocm-resource:resource-2.repository',
            attribute='example.repo-2',
        ),
    ]

    ocm_mapping = helm.to_ocm_mapping(
        helmchart_name='helmchart-name-1',
        mappings=mappings,
    )

    assert ocm_mapping == {
        'helmchartResource': {
            'name': 'helmchart-name-1',
        },
        'imageMapping': [
            {
                'resource': {
                    'name': 'resource-1',
                },
                'repository': 'example.attribute.repo-1',
                'tag': 'example.attribute.tag-1',
                'image': 'example.attribute.image-1',
            },
            {
                'resource': {
                    'name': 'resource-2',
                },
                'repository': 'example.repo-2',
            }
        ],
    }
