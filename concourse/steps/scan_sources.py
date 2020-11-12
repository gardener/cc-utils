from datetime import datetime
import tempfile
import typing

import ci.util
import checkmarx.util
import mail.model
import product.model
import product.util
import whitesource.client
import whitesource.component
from whitesource.component import get_post_project_object
import whitesource.util
import product.v2


import gci.componentmodel as cm


def scan_sources_and_notify(
    checkmarx_cfg_name: str,
    component_descriptor_path: str,
    email_recipients,
    team_id: str,
    threshold: int = 40,
    exclude_paths: typing.Sequence[str] = [],
    include_paths: typing.Sequence[str] = [],
):
    checkmarx_client = checkmarx.util.create_checkmarx_client(checkmarx_cfg_name)

    scans = checkmarx.util.scan_sources(
        component_descriptor_path=component_descriptor_path,
        cx_client=checkmarx_client,
        team_id=team_id,
        threshold=threshold,
        exclude_paths=exclude_paths,
        include_paths=include_paths,
    )

    checkmarx.util.print_scans(
        scans=scans,
        routes=checkmarx_client.routes,
    )

    checkmarx.util.send_mail(
        scans=scans,
        threshold=threshold,
        email_recipients=email_recipients,
        routes=checkmarx_client.routes,
    )
    #TODO codeowner recipient:


def scan_component_with_whitesource(
    whitesource_cfg_name: str,
    product_token: str,
    component_descriptor_path: str,
    extra_whitesource_config: dict,
    requester_mail: str,
    cve_threshold: float,
    notification_recipients: list,
):

    # create whitesource client
    ci.util.info('creating whitesource client')
    client = whitesource.util.create_whitesource_client(whitesource_cfg_name=whitesource_cfg_name)

    # parse component_descriptor
    ci.util.info('parsing component descriptor')
    component_descriptor = cm.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor_path)
    )

    # for component in []: 'components matching component_descriptor'
    for component in product.v2.components(component_descriptor_v2=component_descriptor):

        # create whitesource component
        ci.util.info(f'preparing POST project for {component.name}')
        post_project_object = get_post_project_object(
            whitesource_client=client,
            product_token=product_token,
            component=component,
        )

        # store in tmp file
        with tempfile.TemporaryFile() as tmp_file:

            ci.util.info('guessing commit hash')
            # guess git-ref for the given component's version
            commit_hash = product.util.guess_commit_from_ref(component)

            # download whitesource component
            ci.util.info('downloading component for scan')
            whitesource.component.download_component(
                github_api=post_project_object.github_api,
                github_repo=post_project_object.github_repo,
                dest=tmp_file,
                ref=commit_hash,
            )

            # POST component>
            ci.util.info('POST project')
            post_project_object.whitesource_client.post_product(
                product_token=post_project_object.product_token,
                component_name=component.name,
                component_version=post_project_object.component_version,
                requester_email=requester_mail,
                extra_whitesource_config=extra_whitesource_config,
                file=tmp_file,
            )

        # get all projects
        ci.util.info('retrieving all projects')
        projects = client.get_all_projects(product_token=product_token)

        # generate reporting table for console
        tables = whitesource.util.generate_reporting_tables(
            projects=projects,
            threshold=cve_threshold,
            tablefmt='simple',
        )

        whitesource.util.print_cve_tables(tables=tables)

        if len(notification_recipients) > 0:
            # generate reporting table for notification
            tables = whitesource.util.generate_reporting_tables(
                projects=projects,
                threshold=cve_threshold,
                tablefmt='html',
            )

            # get product risk report
            ci.util.info('retrieving product risk report')
            prr = client.get_product_risk_report(product_token=product_token)

            # assemble html body
            body = whitesource.util.assemble_mail_body(
                tables=tables,
                threshold=cve_threshold,
            )

            # send mail
            ci.util.info('sending notification')
            now = datetime.now()
            fname = f'{component.name}-{now.year}.{now.month}-{now.day}-product-risk-report.pdf'

            attachment = mail.model.Attachment(
                mimetype_main='application',
                mimetype_sub='pdf',
                bytes=prr.content,
                filename=fname,
            )

            whitesource.util.send_mail(
                body=body,
                recipients=notification_recipients,
                component_name=component.name,
                attachments=[attachment],
            )
