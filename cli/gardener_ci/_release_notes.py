import os
import sys

import ccc.github
import ccc.oci
import cnudie.retrieve
import ctx
import gitutil
import ocm
import ocm.gardener
import ocm.util
import release_notes.fetch
import release_notes.ocm
import release_notes.tarutil
import version


__cmd_name__ = 'release_notes'


def print_release_notes(
    component_name: str,
    repo_path: str|None=None,
    ocm_repo_base_url: str | None=None,
    version_whither: str | None=None,
    version_whence: str | None=None,
    outdir: str | None=None,
    outfile: str='-',
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

    if not repo_path:
        print('--repo-path was not passed - will only retrieve subcomponent-release-notes')

    # We need a component. Fetch one with given information (assuming the relevant information
    # is still correct if no version was given).
    if not version_whither and not version_whence:
        greatest_version = version.greatest_version(
            versions=version_lookup(component_name),
        )
        component_descriptor = ocm_lookup(
            ocm.ComponentIdentity(
                name=component_name,
                version=greatest_version,
            ),
        )
    elif version_whither:
        component_descriptor = ocm_lookup(
            ocm.ComponentIdentity(
                name=component_name,
                version=version_whither,
            ),
        )
    elif version_whence:
        component_descriptor = ocm_lookup(
            ocm.ComponentIdentity(
                name=component_name,
                version=version_whence,
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

    if repo_path:
        git_cfg = github_cfg.git_cfg(
            repo_path=f'{src_access.org_name()}/{src_access.repository_name()}',
        )
        if not os.path.exists(repo_path):
            git_helper = gitutil.GitHelper.clone_into(
                target_directory=repo_path,
                git_cfg=git_cfg,
            )
        else:
            git_helper = gitutil.GitHelper(
                repo=repo_path,
                git_cfg=git_cfg,
            )
    else:
        git_helper = None

    docs = list()
    if git_helper:
        release_notes_doc = release_notes.fetch.fetch_release_notes(
            component=component,
            version_lookup=version_lookup,
            git_helper=git_helper,
            github_api_lookup=ccc.github.github_api_lookup,
            version_whither=version_whither,
            version_whence=version_whence,
        )
        docs.append(release_notes_doc)

    whence_component = ocm.ComponentIdentity(
        name=component.name,
        version=version_whence,
    )
    whither_component = ocm.ComponentIdentity(
        name=component.name,
        version=version_whither,
    )
    upgrade_vector = ocm.gardener.UpgradeVector(
        whence=whence_component,
        whither=whither_component,
    )

    sub_component_release_note_docs = release_notes.ocm.release_notes_for_vector(
        upgrade_vector=upgrade_vector,
        component_descriptor_lookup=ocm_lookup,
        version_lookup=version_lookup,
        oci_client=oci_client,
        version_filter=version.is_final,
    )

    docs.extend(sub_component_release_note_docs)
    print(f'found {len(docs)=}')

    if not docs:
        print('no release notes found')
        return

    if outdir:
        release_notes.tarutil.release_notes_docs_into_files(
            release_notes_docs=docs,
            repo_dir=outdir,
            rel_path='',
        )
        return

    if outfile == '-':
        outfh = sys.stdout
    else:
        outfh = open(outfile, 'w')

    outfh.write(release_notes.ocm.release_notes_docs_as_markdown([release_notes_doc]))
