import product.model
import product.util

v2_dict = {
    'meta':{
        'schemaVersion': 'v2',
    },
    'components':[
      # first_component
      {
          'name': 'example.org/foo/first_component',
          'version': 'first_version',
          'type': 'gardenerComponent',
          'dependencies':
          [
              {
                  'name': 'example.org/bar/second_component',
                  'version': 'second_version',
                  'type': 'gardenerComponent',
              },
              {
                  'name': 'first',
                  'version': 'version',
                  'type': 'ociImage',
                  'imageReference': 'first_creference:version',
              },
              {
                  'name': 'first_web',
                  'version': 'web_version',
                  'type': 'web',
                  'url': 'https://example.org',
              },
              {
                  'name': 'generic',
                  'version': 'generic_version',
                  'type': 'generic',
              },
          ],
      },
      # second_component
      {
          'name': 'example.org/bar/second_component',
          'version': 'second_version',
          'type': 'gardenerComponent',
          'dependencies': None # no dependencies
      }
    ],
    'overwriteDeclarations':[
      {
        'declaringComponent': {
          'name': 'example.org/bar/second_component',
          'version': 'second_version',
          'type': 'gardenerComponent',
        },
        'overwrites': [
          {
            'componentReference': {
              'name': 'example.org/foo/first_component',
              'version': 'first_version',
              'type': 'gardenerComponent',
            },
            'dependencyOverwrites': [
              {
                'name': 'first',
                'version': 'version',
                'type': 'ociImage',
                'imageReference': 'overwritten-image-ref:version',
              },
            ],
            'componentOverwrites': {}
          }
        ],
      },
    ],
}


def test_deserialisation_returns_correct_model():
    examinee = product.model.ComponentDescriptor.from_dict(raw_dict=v2_dict)

    components = list(examinee.components())
    assert len(components) == 2

    first_component = examinee.component(('example.org/foo/first_component', 'first_version'))
    second_component = examinee.component(('example.org/bar/second_component', 'second_version'))

    assert first_component.name() == 'example.org/foo/first_component'
    assert second_component.name() == 'example.org/bar/second_component'

    first_dependencies = first_component.dependencies()
    second_dependencies = second_component.dependencies()

    first_component_deps = list(first_dependencies.components())
    assert len(first_component_deps) == 1
    first_component_dep = first_component_deps[0]

    first_container_deps = list(first_dependencies.container_images())
    assert len(first_container_deps) == 1
    first_container_dep = first_container_deps[0]

    assert first_component_dep.name() == 'example.org/bar/second_component'
    assert first_component_dep.version() == 'second_version'

    assert first_container_dep.image_reference() == 'first_creference:version'

    assert len(list(second_dependencies.components())) == 0
    assert len(list(second_dependencies.container_images())) == 0

    first_web_deps = list(first_dependencies.web_dependencies())
    assert len(first_web_deps) == 1
    first_web_dep = first_web_deps[0]

    assert first_web_dep.name() == 'first_web'
    assert first_web_dep.version() == 'web_version'
    assert first_web_dep.url() == 'https://example.org'

    first_generic_deps = list(first_dependencies.generic_dependencies())
    assert len(first_generic_deps) == 1
    first_generic_dep = first_generic_deps[0]

    assert first_generic_dep.name() == 'generic'
    assert first_generic_dep.version() == 'generic_version'

    # check that overwrite is honoured
    effective_images = list(product.util._enumerate_effective_images(component_descriptor=examinee))
    assert len(effective_images) == 1
    effective_image = effective_images[0][1]

    assert effective_image.name() == 'first'
    assert effective_image.image_reference() == 'overwritten-image-ref:version'
