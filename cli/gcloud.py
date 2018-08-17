# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from subprocess import run, STDOUT, PIPE, CalledProcessError

from util import existing_file, not_empty, fail


def authenticate_service_account(credentials_file):
    existing_file(credentials_file)
    run([
      'gcloud', 'auth', 'activate-service-account',
      '--key-file', credentials_file
      ],
      stdout=PIPE, stderr=STDOUT, check=True
    )


def determine_image_digest(image_reference):
    not_empty(image_reference)
    result = run([
      'gcloud', 'container', 'images', 'describe', image_reference,
      '--format', 'value(image_summary.fully_qualified_digest)'
      ],
      stdout=PIPE, stderr=STDOUT, check=True
    )
    return result.stdout.strip()


def image_exists(image_reference):
    not_empty(image_reference)
    try:
        determine_image_digest(image_reference)
        return True
    except(CalledProcessError):
        return False


def untag_image(image_reference):
    not_empty(image_reference)
    run([
      'gcloud', 'container', 'images', 'untag', '--quiet', image_reference
      ],
      stdout=PIPE, stderr=STDOUT, check=True
    )


def untag_and_delete_image_if_no_longer_tagged(image_reference):
    not_empty(image_reference)

    # first determine image digest (required to delete after untagging)
    image_digest = determine_image_digest(image_reference)
    untag_image(image_reference)
    # try to delete (do not raise if this fails, as this is an expected case)
    result = run([
      'gcloud', 'container', 'images', 'delete', '--quiet', image_digest
      ],
      stdout=PIPE, stderr=STDOUT, check=False
    )
    return result.returncode == 0


def deploy_image(image_reference):
    not_empty(image_reference)
    result = run([
      'gcloud', 'docker', '--', 'push', image_reference,
      ],
      stdout=PIPE, stderr=STDOUT, check=False
    )
    if result.returncode != 0:
        fail('Command returned with {} - output: {}'.format(result.returncode, result.stdout))
