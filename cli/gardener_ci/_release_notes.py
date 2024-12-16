import ccc.github
import ccc.oci
import cnudie.retrieve
import ctx
import gitutil
import ocm
import ocm.util
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
    oci_client = ccc.oci.oci_client()
    if not ocm_repo_base_url:
        ocm_repository_lookup = ctx.cfg.ctx.ocm_repository_lookup
        ocm_lookup = ctx.cfg.ctx.ocm_lookup
    else:
        ocm_repository_lookup = cnudie.retrieve.ocm_repository_lookup(
            ocm_repo_base_url,
        )
        ocm_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
            ocm_repository_lookup=ocm_repository_lookup,
            oci_client=oci_client,
        )

    if not ocm_lookup:
        print('must either pass ocm_repo_base_url, or configure in .cc-config')
        exit(1)

    version_lookup = cnudie.retrieve.version_lookup(
        ocm_repository_lookup=ocm_repository_lookup,
        oci_client=oci_client,
    )

    # We need a component. Fetch one with given information (assuming the relevant information
    # is still correct if no version was given).
    if not current_version and not previous_version:
        greatest_version = version.greatest_version(
            versions=version_lookup(component_name),
        )
        component_descriptor = ocm_lookup(
            ocm.ComponentIdentity(
                name=component_name,
                version=greatest_version,
            ),
        )
    elif current_version:
        component_descriptor = ocm_lookup(
            ocm.ComponentIdentity(
                name=component_name,
                version=current_version,
            ),
        )
    elif previous_version:
        component_descriptor = ocm_lookup(
            ocm.ComponentIdentity(
                name=component_name,
                version=previous_version,
            ),
        )

    component = component_descriptor.component
    main_source = ocm.util.main_source(component)
    try:
        src_access = main_source.access
        repo_url = src_access.repoUrl
    except:
        print(f'unsupported source-access-type: {main_source}')
        exit(1)

    github_cfg = ccc.github.github_cfg_for_repo_url(repo_url)

    git_helper = gitutil.GitHelper.clone_into(
        target_directory=repo_path,
        git_cfg=github_cfg.git_cfg(
            repo_path=f'{src_access.org_name()}/{src_access.repository_name()}',
        ),
    )

    blocks = release_notes.fetch.fetch_release_notes(
        component=component,
        component_descriptor_lookup=ocm_lookup,
        version_lookup=version_lookup,
        git_helper=git_helper,
        github_api_lookup=ccc.github.github_api_lookup,
        current_version=current_version,
        previous_version=previous_version,
    )
    rendered_notes = release_notes.markdown.render(blocks)
    print('\n'.join(str(n) for n in rendered_notes))
