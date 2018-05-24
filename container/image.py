# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import docker

from util import not_empty

def retrieve_container_image(image_reference):
    if not ':' in not_empty(image_reference):
        # client.pull with not tag specified would pull _all_ images
        raise ValueError('image reference must specify a single image (tag missing)')

    client = docker.Client()
    client.pull(image_reference)
    return client.get_image(image_reference)
