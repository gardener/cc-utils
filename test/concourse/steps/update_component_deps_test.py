import pytest

import test_utils

import concourse.steps
import concourse.model.traits.update_component_deps as update_component_deps
import concourse.model.traits.component_descriptor as component_descriptor


@pytest.fixture
def render_step():
    return concourse.steps.step_def('update_component_deps')


@pytest.fixture
def job_variant():
    update_component_deps_trait = update_component_deps.UpdateComponentDependenciesTrait(
        name='update_component_dependencies',
        variant_name='don\'t_care',
        raw_dict={
            'set_dependency_version_script':'some_path',
            'upstream_component_name':'don\'t_care',
        },
    )

    component_descriptor_trait = component_descriptor.ComponentDescriptorTrait(
        name='component_descriptor',
        variant_name='don\'t_care',
        raw_dict={
            'component_name': 'github.com/org/repo_name',
        },
    )

    main_repo = test_utils.repository()

    job_variant = test_utils.job(main_repo)
    job_variant._traits_dict = {
        'update_component_deps': update_component_deps_trait,
        'component_descriptor': component_descriptor_trait,
    }
    return job_variant


def test_render_and_compile(
    render_step,
    job_variant,
):
    # as a smoke-test, just try to render
    step_snippet = render_step(
        job_step=None,
        job_variant=job_variant,
        github_cfg_name=None,
        indent=0
    )

    # try to compile (-> basic syntax check)
    compile(step_snippet, 'update_component_deps.mako', 'exec')
