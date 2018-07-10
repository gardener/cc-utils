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

from functools import wraps

def args_not_none(*arg_names):
    def not_none_decorator(function):
        @wraps(function)
        def check_not_none(*args, **kwargs):
            for arg_name in arg_names:
                if kwargs[arg_name] is None:
                    raise ValueError(arg_name + ' must not be None')
            return function(*args, **kwargs)
        return check_not_none
    return not_none_decorator
