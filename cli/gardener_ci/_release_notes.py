import cnudie.retrieve
import ctx
import gci.componentmodel as cm
import release_notes.fetch
import release_notes.markdown
import version


__cmd_name__ = 'release_notes'


def print_release_notes(
    repo_path: str,
    component_name: str,
    ocm_repo_base_url: str = None,
    current_version: str = None,
    previous_version: str = None,
):
    if not ocm_repo_base_url:
        ocm_repository_lookup = ctx.cfg.ctx.ocm_repository_lookup
        ocm_lookup = ctx.cfg.ctx.ocm_lookup
    else:
        ocm_repository_lookup = cnudie.retrieve.ocm_repository_lookup(
            ocm_repo_base_url,
        )
        ocm_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
            ocm_repository_lookup,
        )

    if not ocm_lookup:
        print('must either pass ocm_repo_base_url, or configure in .cc-config')
        exit(1)

    version_lookup = cnudie.retrieve.version_lookup(
        ocm_repository_lookup=ocm_repository_lookup,
    )

    # We need a component. Fetch one with given information (assuming the relevant information
    # is still correct if no version was given).
    if not current_version and not previous_version:
        v = cnudie.retrieve.greatest_component_version(
            component_name=component_name,
            version_lookup=version_lookup,
        )
        component_descriptor = ocm_lookup(
            component_id=cm.ComponentIdentity(
                name=component_name,
                version=v,
            ),
        )
    elif current_version:
        component_descriptor = ocm_lookup(
            component_id=cm.ComponentIdentity(
                name=component_name,
                version=current_version,
            ),
        )
    elif previous_version:
        component_descriptor = ocm_lookup(
            component_id=cm.ComponentIdentity(
                name=component_name,
                version=previous_version,
            ),
        )

    if current_version:
        current_version = version.parse_to_semver(current_version)
    if previous_version:
        previous_version = version.parse_to_semver(previous_version)

    component = component_descriptor.component
    blocks = release_notes.fetch.fetch_release_notes(
        component=component,
        version_lookup=version_lookup,
        repo_path=repo_path,
        current_version=current_version,
        previous_version=previous_version,
    )
    rendered_notes = release_notes.markdown.render(blocks)
    print('\n'.join(str(n) for n in rendered_notes))
